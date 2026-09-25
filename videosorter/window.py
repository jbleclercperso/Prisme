"""Fenêtre principale : enchaînement des éléments et exécution des actions."""
from __future__ import annotations

import gc
import operator
import os
import stat
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import shiboken6
from PySide6.QtGui import QCursor
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QThread, QTimer, QUrl, Qt
from PySide6.QtWidgets import (
    QSplitter,
    QApplication, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QMainWindow, QMessageBox, QProgressBar, QProgressDialog, QPushButton,
    QLayout, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import actions
from .backfill import InfoScan, SceneScan, TitleScan, ThumbAudit, ThumbBackfill, VideoCount
from .dupes import (
    DuplicateScan, ImageDuplicateScan, SignatureGroupScan, SignatureScan,
)
from .help import HelpDialog
from .board import COLUMN_CHOICES, BoardView
from .actions import HistoryEntry
from .config import APP_DIR, APP_NAME, TRASH_FOLDER_NAME, VIDEO_EXTS, Config
from .header import (
    CONTENT_FOLDERS, CONTENT_VIDEOS, HEADER_STYLE, TAB_FOLDERS,
    TAB_FAVS, TAB_SPLIT, TAB_TAGS, TAB_VIDEOS, TABS, VIEW_BROWSE, VIEW_EDIT, Breadcrumb, Chips,
    ControlBar, Segmented,
    build_overflow,
)
from . import media
from .media import PreviewManager, Tools, page_count
from .ratings import Ratings
from .tagging import MIN_BUCKET, TagsThread, build_tag_items
from .split import DEFAULT_PANES, SplitWall
from .access import JOURNAL
from .perf import LOG as STALL_LOG, WATCH, mark
from .quiet import QUIET_TITLE, QuietPage
from .share_dialog import ShareDialog
from .query import (
    available as fuzzy_available, matches as matches_parsed, parse as parse_query,
    tester as query_tester,
)
from .tagging import fold

# Les tons du bandeau d'etat. Ils etaient ecrits en dur a chaque appel, avec
# sept teintes pour quatre intentions.
BANNER_TONES = {
    "info": "#22303f",     # ce qui se passe
    "quiet": "#2a2f38",    # ce qui n'a rien donne
    "done": "#1f3326",     # ce qui a abouti
    "error": "#3a2226",    # ce qui a echoue
}
from .scan import (
    set_veiled, under_veiled,
    MODE_FILES, MODE_FLAT, MODE_FOLDERS, PARENT_PREFIX, Item, RefreshThread,
    RootUnreadable, cached_items, canon_root, fast_path, human_duration,
    human_resolution, human_size, known_media, list_entries,
)
from .index import INDEX
from .search_dialog import WebSearchDialog
from .transfer import Transfer, TransferQueue
from .trash import SessionTrash
from .tree import TreePanel
from .icons import dress, icon
from .widgets import (
    Stepper, OverBar, PeekOverlay, RadialMenu, app_icon, draw_icon,
    STYLESHEET, CommandBar, DestinationsDialog, PreviewGrid, SinglePlayer,
    FavoriteStar, TagsDialog, TrashDialog,
)

PAGE_WELCOME, PAGE_SORT, PAGE_DONE, PAGE_QUIET = 0, 1, 2, 3

# Ce que dit le bouton rouge. « Dossier _TRASH » ne disait rien a personne :
# on lit un nom de dossier, pas ce qui va arriver au fichier.
DELETE_LABELS = {
    "recycle": "Mettre à la corbeille",
    "permanent": "Supprimer définitivement",
    "local_trash": "Écarter — récupérable",
}


class WelcomePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 50, 60, 50)
        layout.setSpacing(14)

        title = QLabel(APP_NAME, self)
        title.setObjectName("title")
        subtitle = QLabel(
            "Choisissez un dossier racine. S'il contient des sous-dossiers, ils sont "
            "triés un par un ; s'il ne contient que des vidéos, elles le sont une par une.",
            self,
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        self.choose = QPushButton("Choisir un dossier racine…", self)
        self.choose.setObjectName("primary")

        recent_label = QLabel("Racines récentes", self)
        recent_label.setObjectName("hint")
        self.recent = QListWidget(self)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(10)
        layout.addWidget(self.choose, 0, Qt.AlignLeft)
        layout.addSpacing(20)
        layout.addWidget(recent_label)
        layout.addWidget(self.recent, 1)

    def set_recent(self, roots: list) -> None:
        self.recent.clear()
        for root in roots:
            self.recent.addItem(root)


class DonePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 50, 60, 50)
        layout.setSpacing(14)
        self.title = QLabel("Tri terminé", self)
        self.title.setObjectName("title")
        self.summary = QLabel("", self)
        self.summary.setObjectName("subtitle")
        # Le bilan d'une seance s'allonge avec elle. Sur une seule ligne, il
        # imposait sa largeur a la fenetre entiere — et celle-ci ne pouvait
        # plus retrecir, alors meme que cette page n'etait pas affichee : une
        # pile exige la plus large de ses pages.
        self.summary.setWordWrap(True)
        self.summary.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.rescan = QPushButton("Réanalyser cette racine", self)
        self.rescan.setObjectName("primary")
        self.change = QPushButton("Changer de racine", self)
        layout.addWidget(self.title)
        layout.addWidget(self.summary)
        layout.addSpacing(16)
        row = QHBoxLayout()
        row.addWidget(self.rescan)
        row.addWidget(self.change)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)


# La cle du tri par nom, calculee une fois par element et non a chaque tri.
_BY_NAME = operator.attrgetter("sort_name")

# Les delais des nouveaux essais quand la racine ne repond pas (NAS endormi,
# coupure) : vite d'abord, puis une fois par minute tant qu'il se tait.
RETRY_DELAYS_S = (5, 15, 60)


@contextmanager
def _without_gc():
    """Le ramasse-miettes en pause le temps de fabriquer des milliers d'objets.

    Sans cela, chaque paquet de sept cents allocations declenchait une passe,
    et les plus completes parcouraient toute la collection deja en memoire :
    un quart de seconde a pres d'une seconde de gel au milieu d'un simple
    changement d'onglet. Ce qu'on vient de fabriquer dure : on le gele
    ensuite, pour qu'aucune passe ne le reexamine.
    """
    was = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()
        gc.freeze()


def _as_path(video) -> Path:
    """Le chemin tel quel s'il en est deja un : `Path(Path)` refait l'objet,
    et cent mille fois de suite, cela se sent."""
    return video if isinstance(video, Path) else fast_path(str(video))


def _folder_state(path) -> str:
    """« ok », « absent » ou « injoignable » pour un dossier, en un aller-retour.

    `is_dir()` rendait faux pour un NAS endormi, et l'on annoncait « n'existe
    plus » un dossier qui n'avait pas bouge. Le second aller-retour, celui qui
    departage, n'a lieu que sur le chemin de l'echec.
    """
    try:
        return "ok" if stat.S_ISDIR(os.stat(path).st_mode) else "absent"
    except OSError:
        return actions.probe(path)


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self._fit_to_screen(cfg["window"].get("w", 1400),
                            cfg["window"].get("h", 900))
        self.setStyleSheet(STYLESHEET + HEADER_STYLE)

        self.all_items: list = []      # tout ce que l'analyse a trouve
        self.items: list = []          # ce que le filtre laisse passer
        self.index = 0                 # index dans self.items
        # Au lancement : l'onglet Dossiers, a la racine, sans filtre. Des
        # filtres oublies d'une session a l'autre (« non vus », « horizontales
        # seules », « 5 videos au plus ») donnaient des ecrans vides qu'on ne
        # savait pas expliquer. Les recherches a garder s'enregistrent.
        self.tab = TAB_FOLDERS
        cfg["tab"] = TAB_FOLDERS
        cfg["content"] = CONTENT_FOLDERS
        cfg["view"] = VIEW_BROWSE
        for key, value in (("filter_include", ""), ("filter_exclude", ""),
                           ("only_unseen", False), ("orientations", []),
                           ("folder_min", 0), ("folder_max", 0)):
            cfg[key] = value
        # On arrive toujours sur les vignettes : l'edition se choisit.
        self._editing = False
        # L'onglet d'edition ne dit pas ce qu'on regarde : on garde a part la
        # collection en cours, dossiers ou videos, pour la retrouver en sortant.
        self.content = (CONTENT_VIDEOS if self.tab == TAB_VIDEOS else
                        CONTENT_FOLDERS if self.tab == TAB_FOLDERS else
                        cfg["content"])
        self.tag_family = cfg["tag_family"]
        # Derniere liste de dossiers analysee, gardee telle quelle : changer
        # d'onglet ne doit jamais relire le disque pour la retrouver.
        # Parcours de pre-fabrication des vignettes, quand il tourne.
        self.backfill = None
        self.dupes = None
        self.counter = None
        self.audit = None
        self._backfill_started = 0.0
        # La collection : les dossiers de la racine choisie, et rien d'autre.
        # Chaque onglet en tire sa liste ; seule l'analyse de la racine elle-
        # meme la met a jour, meme quand un autre onglet est affiche.
        self._plain_items: list = []
        self._plain_root = None
        self._plain_whole = False
        self._scan_top = False
        # Le cinema : la video seule, sans rien autour.
        self.cinema = False
        # Ce que le fil d'Ariane ajoute au bout de la liste : le mot-cle ouvert.
        self._list_leaf = ""
        # La racine que l on a choisie : le fil d Ariane en part toujours,
        # quels que soient les onglets traverses depuis.
        self.origin = Path(cfg["root"]) if cfg["root"] else None
        self.tags: list = list(cfg["tags"])
        self.mode = MODE_FOLDERS
        self.root: Path | None = None
        self.plans: dict = {}       # cle "chemin@page" -> plan d'apercus
        self.pages: dict = {}       # page d'apercus courante par element
        self.sort_mode = ""         # "" | "desc" | "asc" : classement des apercus
        self.criteria: dict = {}    # filtres chiffres de la planche
        self.history: list = []
        self.stats = {"moved": 0, "deleted": 0, "skipped": 0}
        self.scan_thread: RefreshThread | None = None
        self.scanning = False
        # Pile des dossiers traverses, pour pouvoir remonter d'ou l'on vient.
        self.levels: list = []
        self._restore_id = ""
        # Historique de navigation, distinct de la pile des niveaux : il retient
        # les endroits visites, y compris lateralement, pour un vrai « Precedent ».
        self.visited: list = []

        self.ratings = Ratings(parent=self)

        self.trash = SessionTrash(self)
        self.trash.changed.connect(self.on_trash_changed)
        # Un element restaure depuis la corbeille reprend son etoile ; un
        # element detruit pour de bon n'en a plus besoin.
        self.trash.restored.connect(self._trash_restored)
        self.trash.purged.connect(self._trash_purged)
        # Les racines dont on a deja cherche les restes d'une seance
        # interrompue : une fois par racine et par lancement suffit.
        self._leftovers_seen: set = set()
        # La racine ne repond pas : on reessaie, de moins en moins souvent.
        self._retry_step = 0
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self._retry_root)
        # Ce qu'on a deja dit au premier affichage (index repris, favoris
        # illisibles) : une fois par lancement.
        self._told_startup = False

        self.transfers = TransferQueue(self)
        set_veiled(cfg["veiled_names"], cfg["show_veiled"])
        self._commands_signature = None
        self._top_tags = None          # ((racine, nb videos), items) deja calcules
        self._tags_thread = None
        self._tags_key = None
        self.titles_scan = None
        self.scene_scan = None
        self.sig_scan = None
        # Plus de reprise dans un sous-dossier au lancement : on arrive a la
        # racine, comme demande.
        self._resume_id = ""
        # Vrai le temps d'un repli sur l'a-peu-pres, quand l'exact n'a rien rendu.
        self._loose = False
        # Le compteur de session : combien de decisions, depuis quand.
        self._session_started = time.monotonic()
        self._decisions = 0
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(30000)
        self.session_timer.timeout.connect(self._show_counts)
        self.session_timer.start()
        # Cinq secondes sur un element, et il compte comme vu.
        self.seen_timer = QTimer(self)
        self.seen_timer.setSingleShot(True)
        self.seen_timer.setInterval(5000)
        self.seen_timer.timeout.connect(self._mark_current_seen)
        # La rafale : sans decision au bout de huit secondes, on passe.
        self.burst_timer = QTimer(self)
        self.burst_timer.setSingleShot(True)
        self.burst_timer.setInterval(8000)
        self.burst_timer.timeout.connect(self._burst_next)
        self.peek_thumbs: dict = {}
        self._resume_hop = False
        self._flat_cache: dict = {}
        self.transfers.finished.connect(self.on_transfer_finished)
        self.transfers.changed.connect(self.on_transfers_changed)

        # Le curseur le plus cher de l'application : voir `media.BLIND_START`.
        media.BLIND_START = max(0.0, float(cfg["preview_start"]))
        self.preview = PreviewManager(cfg["thumb_width"], self)
        self.preview.plan_ready.connect(self.on_plan_ready)
        self.preview.thumb_ready.connect(self.on_thumb_ready)
        self.preview.thumb_failed.connect(self.on_thumb_failed)
        self.preview.info_ready.connect(self.on_info_ready)

        self._build_ui()
        self._refresh_mute()
        self._refresh_state()
        WATCH.setParent(self)
        WATCH.start(str(cfg["root"] or ""))
        self._siblings_cache: dict = {}
        self.welcome.set_recent(cfg["recent_roots"])
        self.stack.setCurrentIndex(PAGE_WELCOME)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.stack = QStackedWidget(self)
        self.setCentralWidget(self.stack)

        self.welcome = WelcomePage(self)
        self.welcome.choose.clicked.connect(self.choose_root)
        # Une seule ecriture pour un meme dossier : « \\serveur\partage » et
        # « X: » donnaient deux collections, et l'on relisait tout.
        self.welcome.recent.itemActivated.connect(
            lambda item: self.start_root(canon_root(item.text()))
        )
        self.stack.addWidget(self.welcome)

        sort_page = QWidget(self)
        layout = QVBoxLayout(sort_page)
        # Sans cela, Qt additionne les largeurs minimales de tout ce que la
        # page contient et en fait la largeur minimale de la fenetre : il a
        # suffi une fois d'une etiquette un peu large pour la rendre
        # impossible a retrecir, et tout debordait de l'ecran. La page se
        # laisse desormais comprimer, quitte a rogner ce qu'elle montre.
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Deux lignes au-dessus de l'image, jamais plus. La premiere dit ou
        # l'on est et ce qui se passe ; la seconde, ce qu'on peut regler ici —
        # les filtres sur la planche, le titre et ses gestes sur une fiche.
        # Chaque ligne en plus etait une rangee de vignettes en moins.
        row_one = QHBoxLayout()
        row_one.setContentsMargins(0, 0, 0, 0)
        row_one.setSpacing(8)
        row_two = QHBoxLayout()
        row_two.setContentsMargins(0, 0, 0, 0)
        row_two.setSpacing(10)

        self.tabs = Segmented("", [
            (TAB_FOLDERS, "Dossiers", "Chaque dossier comme une carte"),
            (TAB_VIDEOS, "Vidéos", "Toutes les vidéos en vrac, au hasard"),
            (TAB_TAGS, "Mots-clés", "Les vidéos réunies par les mots de leurs noms"),
            (TAB_SPLIT, "Mur", "Plusieurs vidéos à la fois"),
            (TAB_FAVS, "★ Favoris", "Les dossiers et les vidéos mis en favori"),
        ], sort_page)
        self.tabs.chosen.connect(self.set_tab)
        row_one.addWidget(self.tabs, 0)

        # Remonter d'un cran, d'ou qu'on soit. `go_up` ne savait revenir que
        # par ou l'on etait descendu : arrive par un mot-cle, par l'historique
        # ou par une racine choisie, la pile etait vide et rien ne remontait.
        self.up_button = QPushButton("", sort_page)
        self.up_button.setIcon(draw_icon("up"))
        self.up_button.setIconSize(QSize(20, 20))
        self.up_button.setObjectName("up")
        self.up_button.setFixedWidth(34)
        self.up_button.setToolTip("Remonter au dossier parent   (Ctrl+↑)")
        self.up_button.setFocusPolicy(Qt.NoFocus)
        self.up_button.clicked.connect(self.go_parent)
        row_one.addWidget(self.up_button, 0)

        self.crumbs = Breadcrumb(sort_page)
        self.crumbs.jumped.connect(self.jump_to)
        # Le fil d'Ariane prend la place qui reste, et cede la sienne quand
        # il n'y en a plus : il ne doit jamais elargir la fenetre.
        self.crumbs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.crumbs.setMinimumWidth(40)
        row_one.addWidget(self.crumbs, 1)

        # Ce qui se fabrique, dit en trois mots a cote du fil d'Ariane.
        self.activity_label = QLabel("", sort_page)
        self.activity_label.setObjectName("hint")
        # Un message long se coupe ; il n'elargit jamais la fenetre.
        self.activity_label.setMinimumWidth(10)
        row_one.addWidget(self.activity_label, 0)

        self.pending_label = QLabel("", sort_page)
        self.pending_label.setObjectName("pending")
        self.pending_label.hide()
        row_one.addWidget(self.pending_label, 0)

        self.progress = QProgressBar(sort_page)
        # Une barre muette de quatre pixels ne disait pas s'il restait dix
        # dossiers ou six cents : sur un partage reseau, l'attente se compte en
        # minutes et l'on veut savoir ou elle en est. Elle vit sur la premiere
        # ligne : une rangee a elle seule coutait une rangee de vignettes.
        self.progress.setTextVisible(True)
        self.progress.setFormat("%v / %m analysés")
        self.progress.setFixedHeight(16)
        self.progress.setFixedWidth(130)
        # Pendant une analyse, la barre tient lieu d'etat de la collection :
        # les deux cote a cote debordaient d'un ecran agrandi.
        self.progress.installEventFilter(self)
        row_one.addWidget(self.progress, 0)

        # « Analyser » vit dans le menu.
        self.scan_button = QPushButton("⟲  Analyser", sort_page)
        self.scan_button.setObjectName("scanState")
        self.scan_button.setProperty("running", "false")
        self.scan_button.setFocusPolicy(Qt.NoFocus)
        self.scan_button.clicked.connect(self.toggle_scan)
        self.scan_button.hide()

        # L'etat de la collection, toujours sous les yeux : combien de videos
        # sont repertoriees, combien ont leur vignette, et de quand ca date.
        # On ne devrait jamais avoir a se demander si « ca compte ».
        self.state_button = QPushButton("", sort_page)
        self.state_button.setObjectName("collectionState")
        self.state_button.setFocusPolicy(Qt.NoFocus)
        self.state_button.setCursor(Qt.PointingHandCursor)
        # Assez large pour se lire, jamais assez pour dicter la fenetre.
        self.state_button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.state_button.setMinimumWidth(110)
        self.state_button.setStyleSheet(
            "QPushButton#collectionState { background: transparent; border: 0;"
            " color: #8b94a1; font-size: 12px; padding: 0 8px; }"
            "QPushButton#collectionState:hover { color: #e9eef4; }")
        self.state_button.clicked.connect(self.verify_collection)
        row_one.addWidget(self.state_button, 0)

        self.random_button = QPushButton("", sort_page)
        # Le de seul : son infobulle dit ce qu'il fait, et la premiere ligne
        # a besoin de sa place pour le chemin et le nom.
        dress(self.random_button, "dices", 20)
        self.random_button.setFixedWidth(42)
        self.random_button.setObjectName("random")
        self.random_button.setToolTip("Une vidéo au hasard, dans toute la "
                                      "collection   (Ctrl+H)")
        self.random_button.setFocusPolicy(Qt.NoFocus)
        self.random_button.clicked.connect(self.pick_random)
        row_one.addWidget(self.random_button, 0)

        # L'arborescence se montrait et se cachait depuis un menu : un reglage
        # qu'on bascule sans arret n'a rien a faire derriere trois clics.
        self.tree_button = QPushButton("", sort_page)
        self.tree_button.setIcon(draw_icon("tree"))
        self.tree_button.setIconSize(QSize(20, 20))
        self.tree_button.setFixedWidth(42)
        self.tree_button.setCheckable(True)
        self.tree_button.setToolTip("Afficher le panneau des dossiers   (Ctrl+T)")
        self.tree_button.setFocusPolicy(Qt.NoFocus)
        self.tree_button.clicked.connect(lambda checked: self.toggle_tree(checked))
        row_one.addWidget(self.tree_button, 0)

        self.mute_button = QPushButton("", sort_page)
        self.mute_button.setIconSize(QSize(20, 20))
        self.mute_button.setIcon(draw_icon("speaker", on=not self.cfg["muted"]))
        self.mute_button.setFixedWidth(42)
        self.mute_button.setFocusPolicy(Qt.NoFocus)
        self.mute_button.clicked.connect(self.toggle_mute)
        row_one.addWidget(self.mute_button, 0)

        # Le repli. Un rond gris, sans legende : il ne doit rien annoncer a
        # qui regarde par-dessus l'epaule, et se trouver sans reflechir.
        self.quiet_button = QPushButton("●", sort_page)
        self.quiet_button.setObjectName("quietSwitch")
        self.quiet_button.setFixedWidth(30)
        self.quiet_button.setToolTip(
            "Passer à autre chose : Prisme s'efface derrière une page neutre.\n"
            "Ctrl+K, Échap ou un double-clic pour revenir.   (Ctrl+K)")
        self.quiet_button.setFocusPolicy(Qt.NoFocus)
        self.quiet_button.clicked.connect(self.enter_quiet)
        row_one.addWidget(self.quiet_button, 0)

        self.more_button = QPushButton("", sort_page)
        self.more_button.setFixedWidth(42)
        dress(self.more_button, "ellipsis", 22)
        # La petite fleche de menu n'apprenait rien et mangeait la place.
        self.more_button.setStyleSheet(
            "QPushButton::menu-indicator { image: none; width: 0px; }")
        self.more_button.setToolTip("Destinations, corbeille, arborescence…")
        self.more_button.setFocusPolicy(Qt.NoFocus)
        # Les usages de tous les jours en tete ; le reste range par theme,
        # dans des sous-menus qui s'ouvrent au survol.
        self.overflow = build_overflow(self, [
            ("Destinations…", self.edit_destinations),
            ("Mots-clés automatiques…", self.edit_tags),
            ("Corbeille de session", self.open_trash),
            ("-", None),
            ("Recherches", [
                ("Enregistrer cette recherche…", self.save_search),
            ]),
            ("Affichage", [
                ("Arborescence des destinations", self.toggle_tree),
                ("Rafale : passer tout seul après 8 s", self.toggle_burst),
                ("Afficher les dossiers masqués", self.toggle_veiled),
                ("Ignorer la mise à l'échelle de Windows", self.toggle_dpi),
            ]),
            ("Collection", [
                ("État de la collection…", self.show_collection_state),
                ("Compter les vidéos", self.count_videos),
                ("État des vignettes", self.audit_thumbs),
                ("Préparer toutes les vignettes", self.toggle_backfill),
                ("Analyser les titres des métadonnées", self.scan_titles),
                ("Repérer les plans (vignettes plus parlantes)", self.scan_scenes),
                ("-", None),
                ("Réanalyser tout le disque", self.refresh_root),
                ("Changer de racine…", self.choose_root),
                ("Où sont les vignettes…", self.show_cache_place),
            ]),
            ("Doublons", [
                ("Chercher les doublons (même taille)", self.find_duplicates),
                ("Chercher les doublons (même image)",
                 lambda: self.find_duplicates(by_image=True)),
                ("Empreintes : sonder ce qui manque", self.scan_signatures),
                ("Doublons d'après les empreintes", self.duplicates_from_sigs),
            ]),
            ("Connexion", [
                ("Partage à distance…", self.open_share),
                ("Recherche vidéo sur le web…", self.open_web_search),
            ]),
            ("Aide", [
                ("Raccourcis et recherche…", self.show_help),
                ("Journal des gels de l'interface", self.open_stall_log),
            ]),
        ])
        # Les recherches enregistrees : la requete, son tri, et « Non vus »,
        # sous un nom. Le langage de recherche existait, il manquait de le
        # retenir.
        searches = next(a.menu() for a in self.overflow.actions()
                        if a.menu() is not None and a.text() == "Recherches")
        self.searches_menu = searches.addMenu(icon("bookmark"),
                                              "Recherches enregistrées")
        self.searches_menu.aboutToShow.connect(self._fill_searches_menu)
        self.more_button.setMenu(self.overflow)
        self._name_backfill_action()
        self._name_veil_action()
        row_one.addWidget(self.more_button, 0)

        # -- deuxieme ligne : ce qui se regle ici ---------------------------
        self.tag_chips = Chips([
            ("mine", "Mes mots", "Mes mots-clés : ceux que vous avez saisis"),
            ("top", "Fréquents", "Les cent mots qui reviennent le plus dans vos noms"),
        ], sort_page)
        self.tag_chips.chosen.connect(self.set_tag_family)
        # Le seul endroit ou l'on pense a ses mots-cles est celui ou on les
        # regarde : les faire chercher dans un menu n'avait pas de sens.
        self.tags_button = QPushButton("", sort_page)
        dress(self.tags_button, "plus", 18)
        self.tags_button.setFixedWidth(34)
        self.tags_button.setToolTip(
            "Mes mots-clés : un mot par ligne. Chacun réunit les vidéos "
            "dont le nom le porte.")
        self.tags_button.setFocusPolicy(Qt.NoFocus)
        self.tags_button.clicked.connect(self.edit_tags)
        self.tags_button.hide()
        self.tag_chips.hide()
        # Les deux familles de mots-cles sont un sous-onglet : elles vont sur
        # la premiere ligne, pas sur celle des filtres, qu'elles faisaient
        # deborder sur une troisieme. A la suite du chemin, comme les reglages
        # du mur : posees entre les onglets et ↑, elles decalaient ce bouton
        # et le fil d'Ariane de plus de deux cents pixels sur cet onglet seul.
        at = row_one.indexOf(self.crumbs) + 1
        row_one.insertWidget(at, self.tag_chips, 0)
        row_one.insertWidget(at + 1, self.tags_button, 0)

        self.controls = ControlBar(COLUMN_CHOICES, sort_page)
        self.controls.changed.connect(self.on_controls_changed)
        self.controls.released.connect(self.setFocus)
        self.controls.sortChanged.connect(self.set_sort)
        self.controls.unseenChanged.connect(self.set_only_unseen)
        self.controls.set_unseen(bool(self.cfg["only_unseen"]))
        self.controls.set_orientations(self.cfg["orientations"])
        self.controls.set_folder_bounds(int(self.cfg["folder_min"] or 0),
                                        int(self.cfg["folder_max"] or 0))
        # Les criteres valent des le depart, pas seulement apres un premier
        # clic sur un chip : sans cela, un filtre retenu d'une session a
        # l'autre s'affichait coche mais ne filtrait rien.
        self.criteria = self.controls.criteria()
        self.controls.columnsChanged.connect(self.set_board_columns)
        self.controls.previousPage.connect(lambda: self.change_page(-1))
        self.controls.nextPage.connect(lambda: self.change_page(1))
        self.controls.randomHere.connect(self.pick_random_here)
        self.controls.set_terms(self.cfg["filter_include"], self.cfg["filter_exclude"])
        self.controls.set_sort(self.cfg["sort_mode"] or "random")
        self.controls.set_columns(self.cfg["board_columns"])
        row_two.addWidget(self.controls, 1)
        self.tabs.set_value(self.tab)
        self.tag_chips.set_value(self.tag_family)
        self.controls.set_browsing(self.browsing)

        # Elle n'existe que le temps d'une selection : une barre d'actions
        # permanente occuperait une rangee pour ne rien dire la plupart du temps.
        self.picked_bar = QWidget(sort_page)
        picked_row = QHBoxLayout(self.picked_bar)
        picked_row.setContentsMargins(0, 0, 0, 0)
        picked_row.setSpacing(6)
        self.picked_label = QLabel("", self.picked_bar)
        self.picked_label.setObjectName("pending")
        picked_row.addWidget(self.picked_label)
        for name, text, tip, slot in (
            ("square-stack", "Mur", "Les vidéos cochées, toutes à la fois, sur le mur",
             self.wall_picked),
            ("play", "Playlist",
             "Les vidéos cochées l'une après l'autre, à droite, en boucle",
             self.playlist_picked),
            ("folder-input", "Déplacer…", "Cliquez ensuite un dossier de l'arborescence",
             self.move_picked_hint),
            ("trash-2", "Supprimer", "Écarte les vidéos cochées", self.delete_picked),
            ("x", "Annuler", "Décoche tout", self.clear_picked),
        ):
            button = QPushButton(text, self.picked_bar)
            dress(button, name, 16, text)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(slot)
            picked_row.addWidget(button)
        picked_row.addStretch(1)
        self.picked_bar.hide()
        self.picked_bar.setMinimumWidth(120)
        # En bout de la ligne des filtres, pas sur une ligne a elle.
        row_two.addWidget(self.picked_bar, 0)

        # Sur une fiche, la seconde ligne est la meme pour tout ce qu'on
        # regarde — video, dossier, mot-cle : ce qu'on en sait a gauche, les
        # gestes a droite. Le nom, lui, est au bout du fil d'Ariane, une
        # seule fois : il y etait deja, et la ligne le repetait en gros.
        header = QFrame(sort_page)
        header.setObjectName("titleLine")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 0, 0)
        header_layout.setSpacing(8)
        # Garde pour les appels existants ; le nom se lit dans le fil.
        self.item_title = QLabel("—", header)
        self.item_title.setObjectName("title")
        self.item_title.hide()
        self.item_parent = QLabel("", header)
        self.item_parent.setObjectName("parentPath")
        self.item_parent.hide()
        self.item_subtitle = QLabel("", header)
        self.item_subtitle.setObjectName("subtitle")
        self.item_subtitle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.item_subtitle.setMinimumWidth(60)
        header_layout.addWidget(self.item_subtitle, 1)

        def action(name: str, text: str, tip: str, slot) -> QPushButton:
            button = QPushButton("", header)
            button.setObjectName("lineAction")
            dress(button, name, 18, text)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(slot)
            header_layout.addWidget(button, 0)
            return button

        # Ouvrir l'explorateur : sur le dossier lui-meme pour un dossier, sur
        # le fichier deja selectionne pour une video.
        self.reveal_button = action(
            "folder-open", "Ouvrir le dossier",
            "Ouvrir dans l'explorateur   (Ctrl+E)", self.reveal_current)
        self.random_here_button = action(
            "shuffle", "Au hasard ici",
            "Une vidéo au hasard, dans ce dossier seulement", self.pick_random_here)
        self.cinema_button = action(
            "maximize", "Cinéma",
            "L'image seule, sans rien autour   (Ctrl+J)", self.toggle_cinema)
        self.cinema_button.setCheckable(True)
        self.cinema_button.hide()
        # Combien d'apercus a la fois : le meme − n + que partout.
        self.grid_chips = Stepper((2, 4, 6, 8, 10), "Aperçus à la fois",
                                  "Aperçus", header)
        self.grid_chips.chosen.connect(self.set_thumb_count)
        self.grid_chips.hide()
        header_layout.addWidget(self.grid_chips, 0)
        self._mark_grid_count()
        self.item_card = header
        row_two.addWidget(header, 1)
        self._row_one, self._row_two = row_one, row_two
        self._one_line = False


        # Les deux rangees du haut dans un seul widget : le plein ecran du
        # mur et le cinema doivent pouvoir tout effacer d'un geste.
        self.top_bar = QWidget(sort_page)
        top_box = QVBoxLayout(self.top_bar)
        top_box.setContentsMargins(0, 0, 0, 0)
        top_box.setSpacing(8)
        top_box.addLayout(row_one)
        top_box.addLayout(row_two)
        layout.addWidget(self.top_bar)

        # Le bandeau d'information flotte au-dessus du contenu, le temps de
        # se lire : pose dans la page, il poussait tout vers le bas a chaque
        # message, et la planche sautait. Fenetre-outil, pour passer aussi
        # devant une video.
        self.banner = QLabel("", self, Qt.Tool | Qt.FramelessWindowHint
                             | Qt.WindowDoesNotAcceptFocus)
        self.banner.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.banner.setObjectName("statusBanner")
        self.banner.setWordWrap(True)
        self.banner.hide()

        # Le panneau d'arborescence occupe la gauche, le lecteur le reste.
        # Planche, lecteur de droite : un separateur qu'on tire a la souris,
        # pour donner plus de place a l'un ou a l'autre.
        middle = QSplitter(Qt.Horizontal, sort_page)
        middle.setChildrenCollapsible(False)
        middle.setHandleWidth(8)
        middle.setStyleSheet(
            "QSplitter::handle { background: transparent; }"
            "QSplitter::handle:hover { background: #2b323d; border-radius: 3px; }")
        self.middle = middle

        self.tree = TreePanel(sort_page)
        self.tree.folderChosen.connect(self.on_tree_folder)
        self.tree.rootChanged.connect(self.on_tree_root_changed)
        self.tree.actionChanged.connect(self.on_tree_action)
        self.tree.set_action("go" if self.tab == TAB_VIDEOS else "send")
        self.tree.hide()
        middle.addWidget(self.tree)

        self.viewer = QStackedWidget(sort_page)
        self.grid = PreviewGrid(
            max(10, self.cfg["thumb_count"]), self.cfg["preview_seconds"],
            self.cfg["scroll_seconds"], self.viewer,
        )
        self.grid.openRequested.connect(self.open_external)
        self.grid.playRequested.connect(self.play_in_app)
        self.single = SinglePlayer(
            self.cfg["thumb_count"], self.cfg["scroll_seconds"], self.viewer
        )
        self.single.finished.connect(self.on_video_finished)
        self.single.radialRequested.connect(self.open_radial)
        self.single.peek.chosen.connect(self.peek_seek)
        self.radial = RadialMenu(self)
        self.radial.chosen.connect(self._radial_chosen)
        self.radial.closed.connect(self._radial_closed)
        self._sorting_pane = None
        self.board = BoardView(
            self.cfg["preview_seconds"], self.cfg["board_columns"], self.viewer
        )
        self.board.openRequested.connect(self.on_board_open)
        self.board.asideRequested.connect(self.open_aside)
        self.board.pickedChanged.connect(self.on_picked_changed)
        self.board.previewNeeded.connect(self.on_board_preview)
        self.board.playRequested.connect(self.play_in_app)
        self.board.pageChanged.connect(self.on_board_page)
        self.viewer.addWidget(self.grid)
        self.viewer.addWidget(self.single)
        self.wall = SplitWall(
            self.cfg["wall_panes"] or DEFAULT_PANES, self.cfg["scroll_seconds"],
            self.viewer, self.cfg["wall_orientation"] or "vertical")
        self.wall.opened.connect(self.open_video_path)
        self.wall.set_muted(bool(self.cfg["muted"]))
        self.wall.set_stay(bool(self.cfg["stay_in_folder"]))
        self.wall.stayChanged.connect(self.set_stay_in_folder)
        self.wall.countChanged.connect(self.set_wall_count)
        self.wall.orientationChanged.connect(self.set_wall_orientation)
        self.wall.fullscreenRequested.connect(self.toggle_wall_fullscreen)
        self.wall.exitRequested.connect(lambda: self.toggle_wall_fullscreen(False))
        self.wall.siblingRequested.connect(self.wall_sibling)
        self.wall.unseenToggled.connect(self._wall_unseen)
        # Les reglages du mur — nombre, orientation, non vus, plein ecran — en
        # bout de la ligne de recherche, pas sur une ligne a eux.
        # Sur la premiere ligne, a la suite du chemin : le mur n'a qu'une
        # ligne, et pas de recherche par nom — il pioche au hasard.
        self.wall.controls.setParent(self.top_bar)
        self._row_one.insertWidget(self._row_one.indexOf(self.crumbs) + 1,
                                   self.wall.controls, 0)
        self.wall.controls.hide()
        self.wall.peekRequested.connect(self.wall_peek)
        self.wall.peekChosen.connect(self.wall_peek_chosen)
        self.wall.sortRequested.connect(self.wall_sort)
        self._wall_peek = None
        self.controls.clearRequested.connect(self.reset_filters)
        self.wall_full = False
        self._wall_pinned: list = []
        self._wall_unsure: list = []
        self.wall_prober = None
        self.share_server = None
        self.tunnel = None
        self.tunnel_address = ""
        self.tunnel_trouble = ""
        self.viewer.addWidget(self.board)
        self.viewer.addWidget(self.wall)
        middle.addWidget(self.viewer)

        # Le bandeau de la fiche : au survol de l'image, le nom, le temps
        # restant et les gestes ; le reste du temps, un trait tres fin. La
        # meme regle que sur le mur et la planche.
        self.single_bar = OverBar(self)
        for text, tip, slot in (
            ("◂", "Précédente   (←)", lambda: self.step(-1)),
            ("⏯", "Pause, ou reprendre   (Entrée)", self.single.toggle_pause),
            ("▸", "Suivante   (→)", lambda: self.step(1)),
            ("⌸", "Ouvrir dans l'explorateur   (Ctrl+E)", self.reveal_current),
            ("⛶", "Cinéma   (Ctrl+J)", self.toggle_cinema),
        ):
            self.single_bar.add_gesture(text, tip, slot)
        self.single.progressed.connect(self.single_bar.set_progress)
        self.single_bar.add_stay(
            "Rester dans ce dossier : ◂ ▸ ne sortent plus du dossier de la vidéo",
            bool(self.cfg["stay_in_folder"]), self.set_stay_in_folder)

        # Le lecteur de cote : on y envoie une video d'un clic droit, et la
        # planche continue de vivre a gauche — on peut changer de page, cocher,
        # ranger, pendant que la video se lit.
        self.aside = QWidget(sort_page)
        aside_box = QVBoxLayout(self.aside)
        aside_box.setContentsMargins(0, 0, 0, 0)
        aside_box.setSpacing(0)
        self.aside_player = SinglePlayer(
            self.cfg["thumb_count"], self.cfg["scroll_seconds"], self.aside)
        # Pas de pellicule ici : on regarde, on ne cherche pas un passage. Elle
        # volait de la largeur a l'image sans rien apporter.
        self.aside_player.hide_strip()
        # Le lecteur de cote enchaine lui aussi : une video finie appelle la
        # suivante de la planche, sans qu'on ait a y revenir.
        self.aside_player.finished.connect(lambda: self.aside_step(1))
        aside_box.addWidget(self.aside_player, 1)

        # Le bandeau flotte sur l'image et ne parait qu'au survol : une
        # rangee permanente coutait quarante pixels de hauteur, et c'est la
        # hauteur qui fait voir une video.
        self.aside_bar = OverBar(self)
        self.aside_title = self.aside_bar.name
        for text, tip, slot in (
            ("◂", "Précédente", lambda: self.aside_step(-1)),
            ("⏯", "Pause, ou reprendre", self.aside_player.toggle_pause),
            ("▸", "Suivante", lambda: self.aside_step(1)),
            ("⛶", "Plein écran", self.aside_fullscreen),
            ("✕", "Fermer le lecteur", self.close_aside),
        ):
            self.aside_bar.add_gesture(text, tip, slot)
        self.aside_player.progressed.connect(self.aside_bar.set_progress)
        self.aside_bar.add_stay(
            "Rester dans ce dossier : ◂ ▸ ne sortent plus du dossier de la vidéo",
            bool(self.cfg["stay_in_folder"]), self.set_stay_in_folder)

        # Un battement suffit a savoir si la souris est sur l'image : le
        # widget video natif ne rend pas les evenements de survol.
        self.aside_watch = QTimer(self)
        self.aside_watch.setInterval(120)
        self.aside_watch.timeout.connect(self._watch_bars)
        self.aside_watch.start()
        self.aside.hide()
        self.aside_index = -1
        self.aside_playlist: list = []
        self.aside_playlist_at = 0
        self.aside_current = ""
        middle.addWidget(self.aside)
        middle.setStretchFactor(0, 0)
        middle.setStretchFactor(1, 1)
        middle.setStretchFactor(2, 1)
        middle.splitterMoved.connect(self._remember_split)
        layout.addWidget(middle, 1)

        # Une seule ligne sous l'image, et seulement sur une fiche : reculer,
        # les touches qui decident, la note, avancer.
        self.prev_button = QPushButton("", sort_page)
        dress(self.prev_button, "chevron-left", 20)
        self.prev_button.setToolTip("Élément précédent   (←)")
        self.prev_button.setFixedWidth(34)
        self.prev_button.setFocusPolicy(Qt.NoFocus)
        self.prev_button.clicked.connect(lambda: self.step(-1))
        self.next_button = QPushButton("", sort_page)
        dress(self.next_button, "chevron-right", 20)
        self.next_button.setToolTip("Élément suivant   (→)")
        self.next_button.setFixedWidth(34)
        self.next_button.setFocusPolicy(Qt.NoFocus)
        self.next_button.clicked.connect(lambda: self.step(1))

        self.stars = FavoriteStar(22, sort_page)
        self.stars.rated.connect(self.rate_current)

        self.commands = CommandBar(sort_page)
        # Les memes actions qu'au clavier, accessibles a la souris.
        self.commands.deleteRequested.connect(self.on_command_delete)
        self.commands.skipRequested.connect(self.on_command_skip)
        self.commands.moveRequested.connect(self.on_command_move)
        self.bottom_bar = QWidget(sort_page)
        bottom = QHBoxLayout(self.bottom_bar)
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(8)
        bottom.addWidget(self.prev_button, 0)
        bottom.addWidget(self.commands, 1)
        bottom.addWidget(self.stars, 0)
        bottom.addWidget(self.next_button, 0)
        layout.addWidget(self.bottom_bar)
        self.nav_row = self.bottom_bar

        # Ce pense-bete etait une seule etiquette de cinq mille pixels de large.
        # Qt en faisait la largeur minimale de la fenetre entiere : elle ne
        # pouvait plus retrecir, et tout le reste debordait de l'ecran. Il se
        # consulte desormais sous le bouton « ⋯ », ou il ne coute rien.
        self.more_button.setToolTip(
            "←/→ naviguer   ·   molette avancer/reculer   ·   Ctrl+Z annuler\n"
            "Ctrl+F filtrer   ·   Ctrl+T arborescence   ·   Ctrl+M son\n"
            "Ctrl+O ouvrir   ·   Ctrl+D destinations   ·   Entrée pause\n"
            "Ctrl+←/→ page d'aperçus   ·   Ctrl+↓ entrer dans le dossier\n"
            "Ctrl+P planche   ·   Ctrl+H au hasard   ·   1 favori, 0 retirer\n"
            "Ctrl+molette zoomer   ·   Ctrl+B corbeille   ·   Échap remonter"
        )

        self.stack.addWidget(sort_page)

        self.done_page = DonePage(self)
        self.done_page.rescan.clicked.connect(self.refresh_root)
        self.done_page.change.clicked.connect(self.choose_root)
        self.stack.addWidget(self.done_page)
        # La page de repli, prete des le depart : la chercher au moment ou
        # l'on en a besoin serait trop tard.
        self.quiet_page = QuietPage(self)
        self.quiet_page.back.clicked.connect(self.leave_quiet)
        self.quiet_page.leave.connect(self.leave_quiet)
        self.stack.addWidget(self.quiet_page)

        # Rien ne disait que des apercus etaient en fabrication : devant une
        # planche qui ne se remplit pas, on ne sait pas s'il faut attendre ou
        # si quelque chose est bloque. Cette barre repond a la question.
        self.activity_timer = QTimer(self)
        self.activity_timer.setInterval(400)
        self.activity_timer.timeout.connect(self._show_activity)
        self.activity_timer.start()

        self.chrome_timer = QTimer(self)
        self.chrome_timer.setSingleShot(True)
        self.chrome_timer.timeout.connect(self._sleep_chrome)

        self.banner_timer = QTimer(self)
        self.banner_timer.setSingleShot(True)
        self.banner_timer.timeout.connect(self.banner.hide)

        # La planche se rebatit au plus une fois par seconde : pendant une
        # relecture qui corrige cinquante dossiers, la refaire a chaque paquet
        # la ferait clignoter sans rien apprendre a personne.
        self._board_dirty = False
        self._harvest = (0, 0)
        self.board_timer = QTimer(self)
        self.board_timer.setSingleShot(True)
        self.board_timer.setInterval(1000)
        self.board_timer.timeout.connect(self._flush_board)

    # ------------------------------------------------------------------
    # Racine et analyse
    # ------------------------------------------------------------------
    def choose_root(self) -> None:
        start = self.cfg["root"] or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choisir le dossier racine", start)
        if chosen:
            # « Réseau › AS1104T › partage » ou « X: » : la meme racine, donc
            # les memes cles d'index, de favoris et de vignettes.
            self.start_root(canon_root(chosen), new_origin=True)

    def _under_origin(self, path) -> bool:
        if self.origin is None or path is None:
            return False
        try:
            Path(path).relative_to(self.origin)
            return True
        except ValueError:
            return False

    def top_root(self):
        """La racine du fil : celle qu'on a choisie, tant qu'on est dessous."""
        if self.root is None:
            return None
        if self._under_origin(self.root):
            return Path(self.origin)
        return Path(self.levels[0]["root"]) if self.levels else Path(self.root)

    def start_root(self, root: Path | None, mode: str = "",
                   reset_levels: bool = True, restore_id: str = "",
                   new_origin: bool = False,
                   force: bool = False) -> None:
        if root is None:
            return
        state = _folder_state(root)
        if state == "absent":
            QMessageBox.warning(self, "Dossier introuvable", f"{root} n'existe plus.")
            return
        # Injoignable, on continue : l'index sait ce qu'il y avait, et la
        # relecture dira que le NAS ne repond pas, puis reessaiera. Refuser
        # d'ouvrir annoncait « n'existe plus » une racine qui dormait.
        self.retry_timer.stop()
        self._retry_step = 0
        self.stop_scan()
        if self.root is not None and Path(root) != self.root:
            self.visited.append({
                "root": self.root, "mode": self.mode,
                "levels": list(self.levels), "board": self.browsing,
                "item_id": self.current.item_id if self.current else "",
            })
            del self.visited[:-40]
        if reset_levels:
            self.levels = []
            # L'origine du fil d'Ariane est la racine qu'on a choisie. Elle ne
            # bouge que si l'on en sort : la reposer a chaque analyse la
            # ramenait au sous-dossier courant, et « remonter » n'avait plus
            # de haut — c'est ce qui balançait vers autre chose.
            if new_origin or self.origin is None or not self._under_origin(root):
                self.origin = Path(root)
        self._restore_id = restore_id
        self.root = Path(root)
        # Un mode impose doit reconduire le selecteur, sinon l'entete annonce
        # une chose et la liste en montre une autre.
        if mode:
            self.content = (CONTENT_FOLDERS if mode == MODE_FOLDERS
                            else CONTENT_VIDEOS)
            # L'onglet ne suit plus le mode. Entrer dans un dossier de videos
            # faisait sauter l'onglet ouvert sur « Videos », et l'onglet
            # « Mots-cles » revenait a « Dossiers » a chaque analyse : on
            # changeait de point de vue sans l'avoir demande. Seul un clic sur
            # un onglet en change desormais.
        self.mode = mode or self.mode_for_content()
        self.all_items = []
        self.items = []
        self._scan_top = False
        self.board.empty.setText("Rien à afficher ici.")
        self.plans = {}
        self.history = []
        self.index = 0
        self.stats = {"moved": 0, "deleted": 0, "skipped": 0}
        self.preview.cancel_all()

        # Ce qu'on savait de cette racine, sans rien demander au disque. C'est
        # tout le propos : l'ecran se remplit avant que la question « qu'y a-t-il
        # ici » ne parte sur le reseau.
        indexed = self.cfg["use_scan_cache"] and not force
        known = (cached_items(self.root, self.mode, self.cfg["expand_parents"])
                 if indexed else [])
        empty = False
        if not known and self.mode == MODE_FOLDERS and state == "ok":
            try:
                empty = not list_entries(self.root, MODE_FOLDERS,
                                         self.cfg["skip_hidden"], False,
                                         strict=True)
            except RootUnreadable:
                # Illisible n'est pas vide : basculer sur les videos lancait
                # un parcours de tout le partage pour un NAS qui dormait.
                empty = False
        if empty:
            # Racine inconnue et sans sous-dossier : on bascule sur les videos
            # plutot que de presenter une liste vide sans explication. Sans
            # traverser les dossiers de tete — la question posee est « y a-t-il
            # quelque chose ici », et la traversee, qui lit tout le reseau, ne
            # la change pas.
            self.mode = MODE_FLAT
            self.content = CONTENT_VIDEOS
            known = (cached_items(self.root, self.mode, self.cfg["expand_parents"])
                 if indexed else [])
        # Analyser la racine elle-meme, c'est tenir la collection a jour.
        self._scan_top = (self.mode == MODE_FOLDERS
                          and Path(self.root) == Path(self.top_root()))
        if self._scan_top:
            self._plain_items = self.all_items
            self._plain_root = self.root
            # Complete des que l'index l'a donnee, ou que la lecture aboutit.
            self._plain_whole = bool(known)

        self.preview.tune_for(self.root)
        self.trash.set_base(self.top_root())
        self._look_for_leftovers(self.top_root())
        self.cfg.push_recent_root(str(self.root))
        # Plus tard, et d'un bloc : l'ecriture attend desormais que le disque
        # l'ait prise (fsync), quelques millisecondes a chaque dossier ouvert.
        self.cfg.save_soon()
        self.welcome.set_recent(self.cfg["recent_roots"])

        # Le fil part de la racine choisie, et non du premier niveau empile :
        # changer d'onglet vidait la pile, et le fil se reduisait alors au seul
        # dossier courant — on ne pouvait plus remonter.
        self._list_leaf = ""
        self.crumbs.set_path(self._origin_for(self.root), self.root)
        self._apply_selectors()
        self.commands.rebuild(self.cfg.destinations, DELETE_LABELS.get(self.cfg["delete_mode"], "Supprimer"))
        # En planche, la vue reste la planche : basculer sur la fiche le temps
        # de l'analyse faisait clignoter l'affichage a chaque changement d'onglet.
        self.viewer.setCurrentWidget(
            self.board if self.browsing
            else (self.grid if self.mode == MODE_FOLDERS else self.single)
        )
        if self.browsing:
            self.board.set_items([], self.ratings.get)
        self.stack.setCurrentIndex(PAGE_SORT)
        self.setFocus()

        if known:
            self._show_known(known, restore_id)
        else:
            self.item_title.setText("Analyse en cours…")
            self.item_subtitle.setText("")
        # La barre d'avancement n'occupe l'ecran que tant qu'il n'y a rien a
        # regarder : une fois la liste affichee, la relecture se signale d'un
        # mot dans le compteur et ne vole plus la place aux vignettes.
        # Une barre indeterminee va et vient sans rien promettre : elle attire
        # l'oeil en permanence pour ne rien apprendre. Elle reste donc fixe, et
        # c'est son texte qui dit ce qui se passe.
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setVisible(not self.items)

        self._tell_startup()
        self._launch_scan(force)

    def _launch_scan(self, force: bool = False) -> None:
        """Relit la racine affichee en tache de fond, sans rien vider a l'ecran.

        Separee de `start_root` pour les nouveaux essais d'une racine qui ne
        repondait pas : tout reprendre de zero ramenait la planche a sa
        premiere page, a chaque essai.
        """
        self.scanning = True
        self._scan_started = time.monotonic()
        self._scan_done, self._scan_total, self._scan_name = 0, 0, ""
        self._refresh_scan_button()
        old = self.scan_thread
        if old is not None and shiboken6.isValid(old) and not old.isRunning():
            # Le passage precedent a fini : rien ne le liberait, et chaque
            # dossier ouvert en laissait un en memoire, attache a la fenetre.
            old.deleteLater()
        # Ce que la collection montre deja : depuis un autre onglet, la liste
        # affichee n'est pas celle des dossiers que la relecture compare.
        shown = self._plain_items if self._scan_top else self.all_items
        self.scan_thread = RefreshThread(
            self.root, self.mode, self.cfg["skip_hidden"],
            self.cfg["use_scan_cache"], self.cfg["expand_parents"],
            [item.item_id for item in shown], force, self,
        )
        self.scan_thread.progress.connect(self.on_scan_progress)
        self.scan_thread.patch.connect(self.on_patch)
        self.scan_thread.finished_scan.connect(self.on_scan_finished)
        self.scan_thread.unreachable.connect(self.on_root_unreachable)
        # Sous la priorite normale : la reconciliation a tout son temps, les
        # vignettes de ce qu'on regarde, non.
        self.scan_thread.start(RefreshThread.LowPriority)

    def on_root_unreachable(self, why: str) -> None:
        """La racine n'a pas repondu : la liste reste celle du dernier passage.

        Rien n'a ete efface. On le dit, puis on reessaie tout seul — 5 s, 15 s,
        puis chaque minute — tant que le NAS se tait.
        """
        if self.sender() is not None and self.sender() is not self.scan_thread:
            return
        delay = RETRY_DELAYS_S[min(self._retry_step, len(RETRY_DELAYS_S) - 1)]
        self._retry_step += 1
        self.show_banner(
            f"NAS injoignable — liste du dernier passage. Nouvel essai dans "
            f"{delay} s.\n{why}", "error")
        self.retry_timer.start(delay * 1000)

    def _retry_root(self) -> None:
        """Nouvel essai de lecture, sur la racine qu'on regarde encore."""
        if self.root is None or self.scanning:
            return
        if self.stack.currentIndex() == PAGE_WELCOME:
            return
        mark(f"nouvel essai {self.root}")
        self._launch_scan()

    def _tell_startup(self) -> None:
        """Ce qui s'est mal passe au lancement, dit une fois, au premier dossier.

        Sans ce mot, un index abime se traduisait par « c'est lent », sans que
        rien ne dise que la memoire venait d'etre remise a zero ; et des
        favoris illisibles semblaient simplement perdus.
        """
        if self._told_startup:
            return
        self._told_startup = True
        said = []
        if INDEX.restored:
            said.append("L'index était abîmé : il a été repris de la copie de "
                        "secours. Ce qui a changé depuis sera relu.")
        elif INDEX.rebuilt:
            said.append("L'index était abîmé : il a été refait. Cette analyse-ci "
                        "sera complète, les suivantes seront immédiates.")
        problem = getattr(self.ratings, "problem", "")
        if problem:
            said.append(problem)
        if said:
            self.show_banner("\n".join(said),
                             "error" if problem else "info")

    def _look_for_leftovers(self, top) -> None:
        """Ce qu'une seance interrompue a laisse dans la corbeille de session.

        Lu hors du fil de l'interface (c'est le NAS) : un Prisme arrete net
        laissait ses dossiers de session invisibles et irrestaurables. Repris
        dans la seance, ils se restaurent comme les autres — et sinon, partent
        a la fermeture, comme ce qu'on ecarte aujourd'hui.
        """
        if top is None:
            return
        key = os.path.normcase(str(top))
        if key in self._leftovers_seen:
            return
        self._leftovers_seen.add(key)
        from .tunnel import Chore
        trash = self.trash
        Chore(lambda: trash.leftovers(Path(top)), self, fallback=[],
              then=self._adopt_leftovers).start()

    def _adopt_leftovers(self, found) -> None:
        if not found or not shiboken6.isValid(self):
            return
        self.trash.adopt(found)
        guessed = sum(1 for entry in found if getattr(entry, "guessed", False))
        self.show_banner(
            f"{len(found)} élément(s) laissé(s) dans la corbeille par une séance "
            "interrompue — Ctrl+B pour les voir et restaurer ce qu'il faut "
            "garder ; le reste partira à la fermeture."
            + (f" L'origine de {guessed} d'entre eux est devinée." if guessed
               else ""), "info")

    def _show_known(self, known: list, restore_id: str = "") -> None:
        """Affiche d'emblee ce que l'index savait de cette racine."""
        self.all_items = known
        if self._scan_top:
            self._plain_items = known
        self.items = self._filtered(known)
        self.apply_sort()
        # Des que la liste est a l'ecran, on prepare ce qu'elle montrera : la
        # relecture du disque n'a pas a finir pour que les apercus commencent.
        self.start_harvest()
        if self.browsing:
            self.refresh_board()
        elif self.items:
            target = 0
            if restore_id:
                for position, item in enumerate(self.items):
                    if item.item_id == restore_id:
                        target = position
                        # Trouve : la consigne est honoree, on l'oublie. Sinon
                        # on la garde, car l'element cherche est justement celui
                        # que l'index ne connait plus — la relecture va le
                        # republier, et c'est elle qui nous y posera.
                        self._restore_id = ""
                        break
            self.show_item(target)
        self._show_counts()
        self.update_counter()
        if self.tab == TAB_TAGS and self.mode == MODE_FOLDERS:
            # Les mots-cles n'attendent plus la fin de la relecture : ils se
            # tirent de ce que l'index connait deja, et se referont a la fin.
            self._add_tag_items()
        elif self.tab == TAB_FAVS and self._scan_top:
            self.show_favorites()

    def stop_scan(self) -> None:
        """Abandonne la relecture en cours, et la fait taire immediatement.

        Un fil bloque sur une longue lecture reseau ne s'arrete pas sur commande :
        il finit d'abord ce qu'il attend. On le coupait de la parole trois
        secondes, puis on l'oubliait — mais ses signaux restaient branches, et il
        continuait a repeindre l'avancement de l'analyse suivante. D'ou une barre
        qui semblait tourner en boucle sans jamais aboutir.
        """
        thread = self.scan_thread
        self.scan_thread = None
        self.scanning = False
        if thread is None:
            return
        if self._scan_top and not self._plain_whole:
            # Une collection lue a moitie ne doit pas passer pour entiere : la
            # prochaine visite de la racine la relira.
            self._plain_root = None
        self._scan_top = False
        if not shiboken6.isValid(thread):
            self._scan_started = 0.0
            self._refresh_scan_button()
            return
        for signal in (thread.progress, thread.patch, thread.finished_scan,
                       thread.unreachable):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        thread.stop()
        if thread.isRunning():
            # Il se terminera de lui-meme ; on le garde en vie le temps qu'il le
            # fasse, sans quoi Qt detruirait un QThread encore en marche. Puis
            # il se libere : chacun gardait la liste de toute la collection.
            self._dying = [t for t in getattr(self, "_dying", [])
                           if shiboken6.isValid(t) and t.isRunning()]
            self._dying.append(thread)
            thread.finished.connect(thread.deleteLater)
        else:
            thread.deleteLater()
        self._scan_started = 0.0
        self._refresh_scan_button()

    # ------------------------------------------------------------------
    # Corbeille de session
    # ------------------------------------------------------------------
    def open_trash(self) -> None:
        """Liste ce qui a été écarté, avec de quoi le remettre en place."""
        if not self.trash.count:
            self.show_banner("La corbeille de session est vide", "quiet")
            return
        dialog = TrashDialog(self.trash, self)
        dialog.exec()
        self.setFocus()
        # Un élément restauré redevient triable.
        for entry_path, item in list(self._restored_items()):
            item.status = ""
            item.status_detail = ""
        self.update_counter()
        if self.current is not None:
            self.show_item(self.index)

    def _restored_items(self):
        """Éléments dont la suppression a été défaite depuis la corbeille."""
        still_gone = {str(entry.origin) for entry in self.trash.entries}
        for item in self.all_items:
            if item.status == "deleted" and item.item_id not in still_gone:
                # « ok » seulement : un NAS qui ne repond pas ne dit pas que
                # l'element est revenu.
                if actions.probe(item.path) == "ok":
                    self.stats["deleted"] = max(0, self.stats["deleted"] - 1)
                    yield item.item_id, item

    def _trash_restored(self, stored: str, target: str) -> None:
        """Restaure depuis la corbeille : l'etoile revient avec l'element."""
        self.ratings.rename(stored, target)

    def _trash_purged(self, paths: list) -> None:
        """Detruits pour de bon : leurs favoris n'ont plus d'objet."""
        for path in paths:
            self.ratings.forget_under(path)

    def _flush_trash_on_close(self) -> None:
        """Vide la corbeille de session, sans rien demander.

        Sur un disque local, le contenu rejoint la corbeille de Windows ; sur
        le NAS, il est detruit pour de bon — c'est voulu, et une question posee
        a chaque fermeture finirait par etre approuvee sans etre lue.

        Hors du fil de l'interface : un gros dossier sur le NAS, c'est un
        aller-retour reseau par fichier, et la fenetre restait « Ne repond
        pas » des minutes. La fenetre est deja masquee ; une petite boite dit
        ce qui se passe si cela dure.
        """
        thread = self.trash.flush_in_background(self.cfg["delete_mode"])
        started = time.monotonic()
        waiter = None
        while thread.is_alive():
            thread.join(0.05)
            QApplication.processEvents()
            if waiter is None and time.monotonic() - started > 0.6:
                waiter = QProgressDialog(
                    "Vidage de la corbeille de session…", "", 0, 0, self)
                waiter.setWindowTitle(APP_NAME)
                waiter.setCancelButton(None)
                waiter.setMinimumDuration(0)
                waiter.show()
        if waiter is not None:
            waiter.close()
        # Les signaux du vidage (favoris a oublier) arrivent par la file
        # d'evenements : on les laisse passer avant d'ecrire les favoris.
        QApplication.processEvents()
        _done, problem = self.trash.flush_result or (0, "")
        if problem:
            QMessageBox.warning(
                self, "Corbeille incomplète",
                "La corbeille de session n'a pas pu être vidée entièrement. Ce "
                "qui reste est gardé et sera repris au prochain lancement "
                "(Ctrl+B).\n\n" + problem,
            )

    # ------------------------------------------------------------------
    # Fabrication des vignettes d'avance
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Doublons
    # ------------------------------------------------------------------
    def show_help(self) -> None:
        """Ouvre la fiche des raccourcis, tiree de la table qui fait foi."""
        HelpDialog(self).exec()
        self.setFocus()

    def _state(self) -> dict:
        state = dict(self.cfg["collection"] or {})
        self.cfg["collection"] = state
        return state

    def _note_state(self, **fields) -> None:
        state = self._state()
        state.update(fields)
        self.cfg.save_soon()
        self._refresh_state()

    @staticmethod
    def _stamp() -> str:
        return datetime.now().strftime("%d/%m %H:%M")

    @staticmethod
    def _thousands(value: int) -> str:
        return f"{value:,}".replace(",", " ")

    def _refresh_state(self, live: str = "") -> None:
        """« 106 903 vidéos · 41 230 vignettes (38 %) · analysé 21/09 14:32 »."""
        state = self._state()
        videos = int(state.get("videos") or 0)
        thumbs = int(state.get("thumbs") or 0)
        audited = int(state.get("audited") or 0)
        parts = []
        parts.append(f"{self._thousands(videos)} vidéos" if videos else "vidéos : ?")
        if live:
            parts.append(live)
        elif audited:
            pct = thumbs * 100 // max(1, audited)
            parts.append(f"{self._thousands(thumbs)} vignettes ({pct} %)")
        else:
            parts.append("vignettes : ?")
        when = state.get("scanned_at") or state.get("counted_at")
        if when:
            parts.append(f"analysé {when}")
        # Court a l'ecran — le detail est dans l'infobulle. Une phrase
        # entiere se faisait rogner jusqu'a ne plus rien vouloir dire, et
        # l'on se demandait ce qu'etait ce bout de texte.
        short = []
        if videos:
            short.append(f"{self._thousands(videos)} vidéos")
        if live:
            short.append(live)
        elif audited:
            short.append(f"{thumbs * 100 // max(1, audited)} % de vignettes")
        self.state_button.setText("  ·  ".join(short) or "collection : ?")
        self._state_full = "   ·   ".join(parts)
        self.state_button.setToolTip(
            (self._state_full + "\n\n" if getattr(self, "_state_full", "") else "")
            + "Ce que le logiciel sait de la collection :\n"
            f"• vidéos répertoriées : {self._thousands(videos) if videos else 'pas encore comptées'}"
            f"{' (' + state['counted_at'] + ')' if state.get('counted_at') else ''}\n"
            f"• vignettes déjà faites : {self._thousands(thumbs) if audited else 'pas encore vérifiées'}"
            f"{' sur ' + self._thousands(audited) + ' (' + state['audited_at'] + ')' if audited and state.get('audited_at') else ''}\n"
            f"• dernière analyse des dossiers : {state.get('scanned_at') or 'jamais'}\n\n"
            "Cliquer : recompter les vidéos, puis vérifier les vignettes.")

    def show_collection_state(self) -> None:
        """Ce que Prisme sait de la collection, en clair, et de quoi le verifier."""
        self._refresh_state()
        box = QMessageBox(self)
        box.setWindowTitle("État de la collection")
        box.setText(self.state_button.toolTip().replace(
            "\n\nCliquer : recompter les vidéos, puis vérifier les vignettes.", ""))
        check = box.addButton("Recompter et vérifier", QMessageBox.AcceptRole)
        box.addButton("Fermer", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is check:
            self.verify_collection()

    def verify_collection(self) -> None:
        """Un clic : compter, puis verifier les vignettes. Les deux chiffres qui tranchent."""
        if self.root is None:
            return
        self._audit_after_count = True
        self.count_videos()

    def _own_thread(self, thread, attr: str) -> None:
        """Libere un fil de fond une fois fini, et oublie la reference.

        Rien ne les detruisait : chaque passage restait en memoire avec ses
        dizaines de milliers de taches, attache a la fenetre. La reference est
        remise a vide ici aussi : un fil qui s'arrete sans avoir rendu son
        resultat ne doit pas passer pour « en cours » a jamais.
        """
        def finished() -> None:
            if getattr(self, attr, None) is thread:
                setattr(self, attr, None)
            thread.deleteLater()
        thread.finished.connect(finished)

    def count_videos(self) -> None:
        """Compte les videos sous la racine, et le dit en clair."""
        if self.root is None or self.counter is not None:
            return
        top = self.top_root()
        self.counter = VideoCount(top, self.cfg["skip_hidden"], self)
        self._own_thread(self.counter, "counter")
        self.counter.progress.connect(
            lambda n: (self.show_banner(f"Comptage… {n} vidéo(s)", "info"),
                       self._refresh_state(f"comptage {n}…")))
        self.counter.counted.connect(lambda n: self._told_count(top, n))
        self.counter.start()
        self.show_banner(f"Comptage des vidéos sous {top}…", "info")

    def _told_count(self, top, total: int) -> None:
        self.counter = None
        if self.origin is None or Path(top) == Path(self.origin):
            self._note_state(videos=total, counted_at=self._stamp())
        self.show_banner(
            f"{total} vidéo(s) sous {top}. "
            f"C'est ce nombre que la préparation des vignettes doit atteindre.",
            "info")
        if getattr(self, "_audit_after_count", False):
            self._audit_after_count = False
            self.audit_thumbs()

    def audit_thumbs(self) -> None:
        """Dit combien de videos ont deja leur vignette, et combien n'en ont pas.

        « Est-ce que ca analyse ? » ne se repond pas en regardant une barre : on
        compte les fichiers reellement presents. Rien n'est fabrique ici.
        """
        if self.root is None or self.audit is not None:
            return
        top = self.top_root()
        self.audit = ThumbAudit(top, self.cfg["thumb_width"],
                                self.cfg["skip_hidden"], self)
        self._own_thread(self.audit, "audit")
        self.audit.progress.connect(
            lambda seen, ready: (
                self.show_banner(
                    f"Vérification… {ready} vignette(s) sur {seen} vidéo(s)",
                    "info"),
                self._refresh_state(f"vérification {ready} / {seen}")))
        self.audit.done.connect(lambda seen, ready: self._told_audit(seen, ready))
        self.audit.start()
        self.show_banner("Vérification des vignettes déjà fabriquées…", "info")

    def _told_audit(self, seen: int, ready: int) -> None:
        self.audit = None
        self._note_state(thumbs=ready, audited=seen, audited_at=self._stamp())
        missing = max(0, seen - ready)
        if not seen:
            return self.show_banner("Aucune vidéo trouvée.", "error")
        part = ready * 100 // seen
        self.show_banner(
            f"{ready} vignette(s) sur {seen} vidéo(s) — {part} %. "
            + (f"Il en manque {missing} : lancez « Préparer toutes les "
               f"vignettes »." if missing else
               "Tout est prêt : l'affichage ne fabrique plus rien."),
            "info" if missing else "done")

    def find_duplicates(self, by_image: bool = False) -> None:
        """Rassemble les vidéos de taille rigoureusement identique.

        Rien n'est supprimé : les groupes s'affichent comme une planche
        ordinaire, les membres d'un même groupe côte à côte. On coche ce dont on
        ne veut plus et l'on se sert du bouton « Supprimer » habituel, qui passe
        par la corbeille de session — donc réversible.
        """
        if self.dupes is not None:
            self._stop_dupes()
            return
        if self.root is None:
            return
        top = self.top_root()
        self._dupes_by_image = by_image
        self._dupes_from_sigs = False
        if by_image:
            # Sur les vignettes deja faites : rien n'est relu sur le partage.
            self.dupes = ImageDuplicateScan(
                top, self.cfg["thumb_width"], self.cfg["skip_hidden"], self)
        else:
            self.dupes = DuplicateScan(top, self.cfg["skip_hidden"], self)
        self._start_dupes(f"Recherche de doublons sous {top}…")

    def _start_dupes(self, banner: str) -> None:
        """Lance la recherche preparee dans `self.dupes`, et le dit."""
        self._dupes_stopped = False
        self._own_thread(self.dupes, "dupes")
        self.dupes.progress.connect(self.on_dupes_progress)
        self.dupes.found.connect(self.on_dupes_found)
        self.dupes.start()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("recherche de doublons…")
        self.progress.show()
        self.show_banner(banner, "info")

    def _stop_dupes(self) -> None:
        """Recliquer arrete : la recherche rendra une liste vide, qu'il ne
        faut pas annoncer comme « aucun doublon »."""
        self._dupes_stopped = True
        self.dupes.stop()
        self.show_banner("Recherche de doublons : arrêt demandé…", "quiet")

    def on_dupes_progress(self, seen: int) -> None:
        if self.scanning:
            return
        self.progress.setFormat(f"doublons : {seen} vidéo(s) examinée(s)…")
        self.progress.show()

    def on_dupes_found(self, groups: list) -> None:
        scan = self.dupes
        self.dupes = None
        self.progress.hide()
        self.progress.setFormat("%v / %m analysés")
        if getattr(self, "_dupes_stopped", False):
            self._dupes_stopped = False
            self.show_banner("Recherche de doublons arrêtée.", "quiet")
            return
        from_sigs = getattr(self, "_dupes_from_sigs", False)
        if from_sigs and scan is not None and not getattr(scan, "examined", 1):
            top = self.top_root()
            self.show_banner(
                f"Aucune empreinte sous {top} : lancez d'abord « Empreintes : "
                "sonder ce qui manque ».", "quiet")
            return
        if from_sigs:
            groups = self._only_in_collection(groups)
        if not groups:
            self.show_banner(
                "Aucun doublon trouvé." + (
                    " Par image, seules les vidéos qui ont déjà leur vignette "
                    "sont comparées : « État des vignettes » dit combien."
                    if getattr(self, "_dupes_by_image", False) else ""),
                "info")
            return
        # Les membres d'un meme groupe se suivent : c'est ce qui permet de les
        # comparer d'un coup d'oeil au lieu de les chercher dans la liste.
        items = []
        extra = 0
        doubtful = 0
        gagne = 0
        for group in groups:
            _each, paths = group
            if getattr(group, "sure", True):
                extra += len(paths) - 1
                gagne += getattr(group, "gain", _each * (len(paths) - 1))
            else:
                # Trop peu pour trancher (une seule image, duree inconnue) :
                # a comparer, pas « en trop ».
                doubtful += 1
            for path in paths:
                path = _as_path(path)
                items.append(Item(path=path, kind=MODE_FILES,
                                  videos=[path], video_count=1,
                                  file_count=1))
        self.stop_scan()
        self.browsing = True
        self.all_items = items
        self.mode = MODE_FLAT
        self.sort_mode = ""            # l'ordre des groupes doit tenir
        self.items = list(items)
        self.index = 0
        self._apply_selectors()
        self.refresh_board()
        self._show_counts()
        how = ("qui se ressemblent" if getattr(self, "_dupes_by_image", False)
               else "de doublons")
        sure = len(groups) - doubtful
        said = []
        if sure:
            said.append(f"{sure} groupe(s) {how} — {extra} fichier(s) en trop, "
                        f"jusqu'à {human_size(gagne)} à récupérer")
        if doubtful:
            said.append(f"{doubtful} groupe(s) à comparer, trop peu sûrs pour "
                        "dire ce qui est en trop")
        self.show_banner(
            " ; ".join(said) + ". Cochez ce dont vous ne voulez plus, puis "
            "« Supprimer » : tout part dans la corbeille de session et revient "
            "par Ctrl+Z.", "info")

    def _only_in_collection(self, groups: list) -> list:
        """Ecarte des groupes ce qui n'est plus dans la collection.

        Les empreintes vivent dans l'index, qui ne sait pas tout : une video
        supprimee ou rangee hors de Prisme y garde la sienne, et reparaissait
        comme doublon de sa propre copie. Seulement quand la collection est
        entierement connue — sinon, on ne saurait pas quoi ecarter.
        """
        top = self.top_root()
        if (not self._plain_whole or self._plain_root is None or top is None
                or Path(self._plain_root) != Path(top)):
            return groups
        present = {os.path.normcase(str(video))
                   for item in self._plain_items for video in item.videos}
        kept = []
        for group in groups:
            _each, paths = group
            inside = [at for at, path in enumerate(paths)
                      if os.path.normcase(str(path)) in present]
            if len(inside) == len(paths):
                kept.append(group)
            elif len(inside) >= 2:
                kept.append(group.subset(inside) if hasattr(group, "subset")
                            else (_each, [paths[at] for at in inside]))
        return kept

    def toggle_backfill(self) -> None:
        """Lance, ou arrête, la fabrication de toutes les vignettes manquantes.

        Elle tourne pendant qu'on trie. Ce qu'on regarde passe devant : le
        parcours n'occupe que la moitié des extractions simultanées.
        """
        if self.backfill is not None:
            self.backfill.stop()
            self.show_banner("Préparation des vignettes : arrêt demandé…", "quiet")
            return
        if self.root is None:
            return
        top = self.top_root()
        self.backfill = ThumbBackfill(top, self.cfg["thumb_width"],
                                      self.cfg["skip_hidden"], self)
        self._own_thread(self.backfill, "backfill")
        self.backfill.counting.connect(self.on_backfill_counting)
        self.backfill.counted.connect(self.on_backfill_counted)
        self.backfill.progress.connect(self.on_backfill_progress)
        self.backfill.done.connect(self.on_backfill_done)
        self._backfill_started = time.monotonic()
        self.backfill.start()
        self._name_backfill_action()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("recensement des vidéos…")
        self.progress.show()
        self.show_banner(
            f"Préparation des vignettes de {top} — recensement des vidéos…",
            "info",
        )

    def _show_activity(self) -> None:
        """Dit ce qui se fabrique, en toutes lettres et sans barre qui ondule.

        Une barre indeterminee va et vient sans rien promettre : elle attire
        l'oeil en permanence pour ne rien apprendre. Un compte discret a cote du
        fil d'Ariane suffit, et s'efface des qu'il n'y a plus rien a dire.
        """
        if self.scanning or self.backfill is not None:
            return
        busy = self.preview.busy()
        self.activity_label.setText(f"⋯ {busy} aperçu(s)" if busy else "")

    def _name_backfill_action(self, seen: int = -1, total: int = 0) -> None:
        """Dit, dans le menu, ou en est la preparation — ou quand elle a fini."""
        if self.backfill is not None:
            label = (f"Arrêter la préparation   ({seen} / {total})"
                     if seen >= 0 else "Arrêter la préparation")
        else:
            done = self.cfg["thumbs_last_run"]
            label = ("Préparer toutes les vignettes"
                     + (f"   (dernière : {done})" if done else "   (jamais faite)"))
        for action in self._menu_actions():
            if action.text().startswith("Préparer toutes les vignettes"):
                action.setText(label)
                return

    def on_backfill_counting(self, found: int) -> None:
        """Le recensement dure : il dit ce qu'il trouve en chemin."""
        if self.scanning:
            return
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat(f"recensement : {found} vidéo(s) trouvée(s)…")
        self.progress.show()

    def on_backfill_counted(self, total: int) -> None:
        if self.scanning:
            return
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self.progress.setFormat("vignettes : %v / %m")
        self.progress.show()

    def on_backfill_progress(self, made: int, kept: int, total: int) -> None:
        self._name_backfill_action(made + kept, total)
        self._refresh_state(f"préparation {self._thousands(made + kept)} / "
                            f"{self._thousands(total)}")
        if self.scanning:
            return
        seen = made + kept
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(seen)
        # Une barre qui avance sans dire combien de temps il reste n'apprend
        # rien qu'on ne voie deja : c'est la fin qu'on veut connaitre.
        elapsed = time.monotonic() - self._backfill_started
        if seen > 20 and elapsed > 5:
            left = (total - seen) * elapsed / seen
            self.progress.setFormat(
                f"vignettes : %v / %m — {human_duration(left)} restant")
        self.progress.show()

    def on_backfill_done(self, made: int, kept: int, complete: bool) -> None:
        self.backfill = None
        # Ce que la preparation a parcouru vaut une verification : chaque
        # video vue a, ou n'a pas, sa vignette.
        if made + kept:
            self._note_state(thumbs=made + kept, audited=made + kept,
                             audited_at=self._stamp())
        else:
            self._refresh_state()
        if not self.scanning:
            self.progress.hide()
            self.progress.setFormat("%v / %m analysés")
        if complete:
            self.cfg["thumbs_last_run"] = datetime.now().strftime("%d/%m/%Y à %H:%M")
            self.cfg.save()
            self._name_backfill_action()
        self._name_backfill_action()
        fin = "terminée" if complete else "interrompue"
        self.show_banner(
            f"Préparation {fin} : {made} vignette(s) fabriquée(s), "
            f"{kept} déjà présente(s). Compte rendu : {ThumbBackfill.LOG}",
            "info",
        )

    def refresh_root(self) -> None:
        """Relit tout le disque, sans se fier a l'analyse precedente."""
        if self.root is None:
            return
        self.show_banner("Réanalyse complète en cours…", "info")
        self.start_root(self.root, self.mode, reset_levels=False, force=True)

    def toggle_mode(self) -> None:
        """Bascule entre dossiers et videos, comme le selecteur."""
        self.set_content(
            CONTENT_VIDEOS if self.content == CONTENT_FOLDERS else CONTENT_FOLDERS
        )

    # ------------------------------------------------------------------
    # Descendre dans un dossier, et en revenir
    # ------------------------------------------------------------------
    def enter_current(self) -> None:
        """Ouvre le dossier affiche pour en trier le contenu piece par piece.

        Le cas vise : un dossier trop melange pour recevoir une seule etiquette.
        On y entre en mode fichier s'il contient des videos directement, sinon
        on laisse la detection choisir — un dossier de dossiers se parcourt.
        """
        item = self.current
        if item is None or item.kind != MODE_FOLDERS or item.locked:
            return
        if item.is_tag:
            return self.open_tag(item)
        target = Path(item.path)
        state = _folder_state(target)
        if state == "absent":
            self.show_banner(f"Introuvable : {item.name}", "error")
            return
        if state == "injoignable":
            # Le dossier n'a pas disparu : c'est le NAS qui ne repond pas.
            self.show_banner(f"NAS injoignable : « {item.name} » n'a pas pu "
                             "être ouvert. Réessayez dans un instant.", "error")
            return

        if item.loose_only:
            # L'entree ne porte que les videos en vrac : on va droit a elles.
            mode = MODE_FILES
        elif list_entries(target, MODE_FOLDERS, self.cfg["skip_hidden"],
                          self.cfg["expand_parents"]):
            mode = self.mode_for_content()
        else:
            # Pas de sous-dossier : il n'y a que des videos a montrer.
            mode = MODE_FLAT
        self.levels.append({
            "root": self.root,
            "mode": self.mode,
            "item_id": item.item_id,
        })
        self.start_root(target, mode, reset_levels=False)

    def go_back(self) -> bool:
        """Revient a l'endroit precedemment visite, quel qu'en soit le niveau."""
        if not self.visited:
            self.show_banner("Rien avant cet endroit", "quiet")
            return False
        previous = self.visited.pop()
        self.levels = list(previous["levels"])
        if previous["board"] != self.browsing:
            self.browsing = previous["board"]
            self._apply_selectors()
            self.cfg["view"] = self.view
            # start_root empilerait a nouveau : on neutralise le temps du retour.
        target, self.root = previous["root"], None
        self.start_root(target, previous["mode"], reset_levels=False,
                        restore_id=previous["item_id"])
        return True

    def set_board_columns(self, columns: int) -> None:
        self.cfg["board_columns"] = columns
        self.cfg.save()
        self.board.set_columns(columns)

    def open_tag(self, item) -> None:
        """Montre les vidéos réunies par un mot-clé, comme une liste ordinaire."""
        self.levels.append({
            "root": self.root, "mode": self.mode,
            "item_id": item.item_id,
        })
        # Sans `stat()` : sur un partage, ouvrir un mot-cle de cinq cents videos
        # coutait cinq cents allers-retours pour une taille que rien n'affiche.
        self.all_items = [Item(path=Path(video), kind=MODE_FILES, videos=[video],
                               video_count=1, file_count=1)
                          for video in item.videos]
        self.items = self._filtered()
        self.apply_sort()
        self.mode = MODE_FLAT
        self.content = CONTENT_VIDEOS
        self.index = 0
        self.start_harvest()
        self._apply_selectors()
        # Un mot-cle n'est pas un dossier : sans ce rappel, le fil d'Ariane
        # restait sur la racine et l'on ne savait plus ce qu'on regardait.
        self._list_leaf = item.name
        self.crumbs.set_path(self.top_root(), self.root)
        self.crumbs.append_leaf(item.name)
        self.show_banner(
            f"{len(self.items)} vidéo(s) portant « {item.path.name} »", "info"
        )
        if self.browsing:
            self.refresh_board()
        elif self.items:
            self.show_item(0)

    def go_parent(self) -> None:
        """Remonte d'un cran : par ou l'on est venu, sinon vers le parent reel."""
        if not self.browsing and self.tab != TAB_SPLIT:
            # Sur la fiche d'un dossier, « remonter » rend la liste d'ou on
            # l'a ouverte. Le bouton ne faisait rien : on etait deja, pour
            # lui, a la racine.
            return self.show_board_at(self.index)
        if self.go_up():
            return
        if self.root is None:
            return
        here = Path(self.root)
        origin = Path(self.origin) if self.origin is not None else None
        if origin is not None and here == origin:
            self.show_banner("Déjà au sommet.", "quiet")
            return
        # Le vrai dossier parent, un cran au-dessus — et non la racine
        # entiere, qu'on atteignait en sautant les dossiers « + ».
        parent = here.parent
        if origin is not None:
            try:
                parent.relative_to(origin)
            except ValueError:
                parent = origin
        if parent == here or not parent.is_dir():
            self.show_banner("Déjà au sommet.", "quiet")
            return
        self.start_root(parent, self.mode_for_content(),
                        restore_id=str(self.root))

    def go_up(self) -> bool:
        """Remonte d'un niveau, en retrouvant le dossier d'ou l'on etait parti."""
        if not self.levels:
            return False
        level = self.levels.pop()
        top = self.top_root()
        on_sheet = not self.browsing
        if (not self.levels and top is not None
                and Path(level["root"]) == Path(top)
                and self._show_tab_list(restore_id=level["item_id"])):
            # Revenir au premier niveau, c'est retrouver la liste de l'onglet
            # — les favoris, les mots-cles — sans relire toute la racine. Depuis
            # une fiche, on retrouve la fiche du dossier d'ou l'on venait.
            if on_sheet and self.current is not None \
                    and self.current.item_id == level["item_id"]:
                self.on_board_open(self.index)
            return True
        self.start_root(
            level["root"], level["mode"],
            reset_levels=False, restore_id=level["item_id"],
        )
        return True

    def on_scan_progress(self, done: int, total: int, name: str = "") -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self._scan_done, self._scan_total, self._scan_name = done, total, name
        self._refresh_scan_button()
        if not self.items:
            self.item_subtitle.setText(
                f"Analyse {done}/{total} — {name}" if name
                else f"Analyse {done}/{total}…")

    def start_harvest(self) -> None:
        """Voir `_harvest_tasks`. Idempotent : relancer remplace la precedente."""
        """Fabrique d'avance la vignette de chaque élément, une fois pour toutes.

        Sur le partage, une vignette coûte environ une seconde et seize
        extractions de front ne vont pas plus vite que huit : la ligne est
        saturée. Une page de quarante cartes demande donc une demi-minute, et
        **rien ne peut la raccourcir au moment où on la regarde**. Mais une
        vignette déjà faite se relit en deux millisecondes. On les fabrique donc
        avant, pendant qu'on fait autre chose, et la récolte s'écarte dès que
        quelqu'un demande quelque chose.
        """
        mark(f"start_harvest {len(self.all_items)}")
        tasks = []
        for index, item in enumerate(self.all_items):
            if item.locked or not item.videos:
                continue
            video = str(item.videos[0])
            blind = media.BLIND_START
            if item.kind == MODE_FOLDERS:
                tasks.append((item.item_id, video, blind))
            else:
                info = INDEX.probe(video)
                duration = (info or {}).get("duration") or 0.0
                tasks.append((item.item_id, video,
                              min(duration * 0.2, 20.0) if duration > 2 else blind))
        if not tasks:
            return
        harvester = self.preview.start_harvest(tasks)
        harvester.progress.connect(self.on_harvest_progress)
        harvester.finished_harvest.connect(self.on_harvest_finished)
        self._harvest = (0, len(tasks))
        self._refresh_scan_button()

    def on_harvest_progress(self, done: int, total: int) -> None:
        self._harvest = (done, total)
        self._refresh_scan_button()

    def on_harvest_finished(self, made: int) -> None:
        self._harvest = (0, 0)
        self._refresh_scan_button()
        if made:
            self.show_banner(
                f"✓ {made} aperçu(s) préparés d'avance. Les revoir est "
                f"désormais immédiat.", "done")

    def _refresh_scan_button(self) -> None:
        """Dit sans ambiguite si une analyse tourne, et ou elle en est.

        C'etait la vraie plainte : on ne savait pas distinguer une application
        lente d'une analyse en cours. Le bouton porte donc l'etat, et le nom du
        dossier en cours montre que quelque chose avance meme quand le chiffre
        met du temps a changer.
        """
        if not hasattr(self, "scan_button"):
            return
        harvest_done, harvest_total = getattr(self, "_harvest", (0, 0))
        if not self.scanning and harvest_total:
            # L'analyse est finie, les apercus se preparent encore : il faut le
            # dire, sinon on croit l'application occupee sans savoir a quoi.
            self.scan_button.setText(f"◷  aperçus {harvest_done} / {harvest_total}")
            self.scan_button.setProperty("running", "true")
            self.scan_button.setToolTip(
                "Les aperçus se fabriquent d'avance, en arrière-plan.\n"
                "Ils s'effacent dès que vous regardez quelque chose.\n"
                "Cliquer pour arrêter.")
        elif not self.scanning:
            self.scan_button.setText("⟲  Analyser")
            self.scan_button.setProperty("running", "false")
            self.scan_button.setToolTip(
                "Relire le disque et mettre à jour ce qui a changé   (Ctrl+R "
                "pour tout relire sans se fier aux dates)")
        else:
            total = getattr(self, "_scan_total", 0)
            done = getattr(self, "_scan_done", 0)
            self.scan_button.setText(f"⟳  {done} / {total}" if total
                                     else "⟳  Analyse…")
            self.scan_button.setProperty("running", "true")
            name = getattr(self, "_scan_name", "")
            self.scan_button.setToolTip(
                (f"En cours : {name}\n" if name else "")
                + "Cliquer pour arrêter l'analyse")
        self.scan_button.style().unpolish(self.scan_button)
        self.scan_button.style().polish(self.scan_button)

    def toggle_scan(self) -> None:
        """Le bouton lance l'analyse, ou l'arrête si elle tourne."""
        if self.scanning:
            self.stop_scan()
            self.progress.hide()
            self.show_banner("Analyse interrompue. Ce qui a été lu est gardé.",
                             "info")
            self.update_counter()
            return
        if getattr(self, "_harvest", (0, 0))[1]:
            self.preview.stop_harvest()
            self._harvest = (0, 0)
            self._refresh_scan_button()
            self.show_banner("Préparation des aperçus interrompue. Ceux qui "
                             "sont faits restent faits.", "info")
            return
        if self.root is not None:
            self.start_root(self.root, self.mode, reset_levels=False,
                            restore_id=self.current.item_id if self.current else "")

    def on_patch(self, added: list, replaced: list, removed: list) -> None:
        """Applique ce que la relecture a trouvé de différent, et rien d'autre.

        L'ancien moteur republiait la collection entière à chaque lancement :
        même inchangée, elle était reconstruite dossier par dossier. Ici un
        relancement sur une collection stable n'appelle simplement jamais cette
        méthode — c'est le cas courant, et c'est ce qui rend l'ouverture immédiate.
        """
        mark(f"on_patch +{len(added)} ~{len(replaced)} -{len(removed)}")
        if (self._scan_top and self.all_items is not self._plain_items
                and self.tab == TAB_FOLDERS and self.mode == MODE_FOLDERS
                and not self.levels):
            # L'onglet Dossiers, a la racine : c'est la collection qui doit
            # etre a l'ecran. Un rappel tardif d'un autre onglet avait pu
            # remplacer la liste, et la planche restait alors d'un passage en
            # retard — un dossier ajoute n'y paraissait jamais.
            self.all_items = self._plain_items
            self.items = self._filtered()
            self.apply_sort()
            if self.browsing:
                self._board_dirty = True
        if self._scan_top and self.all_items is not self._plain_items:
            # Un autre onglet est a l'ecran (mots-cles, videos, favoris) : la
            # relecture de la racine corrige la collection, sans rien melanger
            # a la liste affichee. Chaque onglet s'y reservira.
            plain = {i.item_id: i for i in self._plain_items}
            for key in removed:
                plain.pop(key, None)
            for item in list(replaced) + list(added):
                plain[item.item_id] = item
            self._plain_items = list(plain.values())
            self._plain_root = self.root
            return
        if removed:
            self._drop_items(removed)
        if replaced:
            self._replace_items(replaced)
        for item in added:
            self.all_items.append(item)
            if self._matches(item):
                self._accept_item(item)
        if self._scan_top:
            # `_drop_items` refait la liste : la collection la suit.
            self._plain_items = self.all_items
        if (added or removed) and self.browsing:
            self._board_dirty = True
        if self._board_dirty:
            # Rebâtir la planche à chaque paquet la ferait clignoter : on le
            # fait au plus une fois par seconde, quand la rafale est passée.
            self.board_timer.start()
        self._show_counts()
        self.update_counter()

    def _drop_items(self, removed: list) -> None:
        """Retire les éléments que le disque ne porte plus."""
        gone = set(removed)
        current = self.current
        self.all_items = [item for item in self.all_items if item.item_id not in gone]
        self.items = [item for item in self.items if item.item_id not in gone]
        if self.browsing:
            self._board_dirty = True
            return
        if current is not None and current.item_id in gone:
            self.index = min(self.index, max(0, len(self.items) - 1))
            if self.items:
                self.show_item(self.index)
            else:
                self._release_media()
        elif current is not None:
            # Rester sur le meme element, meme si des voisins ont disparu.
            try:
                self.index = self.items.index(current)
            except ValueError:
                self.index = min(self.index, max(0, len(self.items) - 1))

    def _replace_items(self, fresh: list) -> None:
        """Substitue les éléments relus, en gardant ce que la session a décidé."""
        by_id = {item.item_id: item for item in fresh}
        current = self.current
        current_id = current.item_id if current is not None else ""
        redraw = False
        for holder in (self.all_items, self.items):
            for position, old in enumerate(holder):
                new = by_id.get(old.item_id)
                if new is None:
                    continue
                # Une decision prise, ou un transfert encore en vol, survit a la
                # relecture : le disque ne sait rien de ce que la session a fait.
                new.status = old.status
                new.status_detail = old.status_detail
                new.info = old.info
                holder[position] = new
                if old.item_id == current_id and not redraw:
                    redraw = [str(v) for v in old.videos] != [str(v) for v in new.videos]
        if self.browsing:
            self._board_dirty = True
        elif redraw:
            # Les apercus retenus portent l'ancienne composition du dossier.
            self.plans = {key: plan for key, plan in self.plans.items()
                          if not key.startswith(current_id + "@")}
            self.show_item(self.index)

    def _flush_board(self) -> None:
        self._board_dirty = False
        if self.browsing:
            self.refresh_board()

    def _accept_item(self, item) -> None:
        first = not self.items
        self.items.append(item)
        if self.browsing:
            # Ajout d'une carte, sans reconstruire la planche : la rebatir a
            # chaque element qui arrive la faisait clignoter et redemandait des
            # vignettes deja obtenues.
            self.board.append_item(item, self.ratings.get(item.path))
            return
        if self._restore_id and item.item_id == self._restore_id:
            self._restore_id = ""
            self.show_item(len(self.items) - 1)
            return
        if first:
            self.show_item(0)
            return
        # L'analyse alimente la liste en continu : un élément qui arrive juste
        # après celui affiché doit être préchargé lui aussi.
        if len(self.items) - 1 <= self.index + 2:
            self._request_previews(item, current=False)

    def _add_tag_items(self) -> None:
        """Place les dossiers virtuels en tete, une fois l'analyse terminee.

        Ils se calculent sur les videos deja trouvees : inutile de relire le
        disque, l'analyse vient de le faire.
        """
        if self.mode != MODE_FOLDERS:
            return
        mark(f"_add_tag_items {self.tag_family}")
        # Les dossiers virtuels ne debordent plus sur la liste des dossiers
        # reels : ils ont leur onglet, c'est la qu'on les cherche.
        if self.tab != TAB_TAGS:
            if any(i.is_tag for i in self.all_items):
                self.all_items = [i for i in self.all_items if not i.is_tag]
                if not self.all_items:
                    self.all_items = self._collection()
                self.items = self._filtered()
                self.apply_sort()
            return
        # Les mots se tirent de la collection entiere, jamais de la liste
        # affichee : celle-ci peut etre celle des mots eux-memes.
        plain = self._collection()
        videos = [video for item in plain for video in item.videos]
        # Deux familles : les mots qu'on a saisis, et ceux que les noms de
        # fichiers repetent d'eux-memes. Les seconds ne demandent aucune saisie
        # et decrivent souvent mieux la collection que ce qu'on aurait pense.
        if self.tag_family != "mine":
            key = (str(self.root), len(videos))
            cached = self._top_tags
            if cached is not None and cached[0] == key:
                found = cached[1]
            else:
                if self._tags_thread is None or self._tags_key != key:
                    self._tags_key = key
                    self._tags_thread = TagsThread(videos, INDEX.titles, self)
                    self._tags_thread.ready.connect(self._on_top_tags)
                    self._tags_thread.start()
                    self.show_banner("Mots fréquents : calcul en cours…", "info")
                self.all_items = []
                self.items = []
                self.board.empty.setText("Calcul des mots fréquents…")
                if self.browsing:
                    self.refresh_board()
                self.item_title.setText("Calcul des mots fréquents…")
                self.item_subtitle.setText("")
                self._show_counts()
                return
            if not found:
                self.all_items = []
                self.items = []
                if self.browsing:
                    self.refresh_board()
                self.item_title.setText("Aucun mot-clé")
                self.item_subtitle.setText(
                    "Aucun mot ne revient assez souvent dans ces noms de fichiers.")
                self._show_counts()
                self.update_counter()
                return
            self.all_items = found if self.tab == TAB_TAGS else found + plain
            self.items = self._filtered()
            self.apply_sort()
            self.start_harvest()
            if self.browsing:
                self.refresh_board()
            self._show_counts()
            self.update_counter()
            return
        words = self.tags
        if not words:
            # Aucun mot-cle : la liste se vide **et l'affichage suit**. Il
            # restait auparavant sur les categories precedentes, si bien que
            # « Mes mots-cles » semblait rendre les mots frequents.
            self.all_items = []
            self.items = []
            if self.browsing:
                self.refresh_board()
            self.item_title.setText("Aucun mot-clé")
            self.item_subtitle.setText(
                "Ajoutez les vôtres par « ⋯ › Mots-clés automatiques… », ou "
                "choisissez « Fréquents » pour les laisser deviner."
                if self.tag_family == "mine"
                else "Aucun mot ne revient assez souvent dans ces noms de fichiers."
            )
            self._show_counts()
            self.update_counter()
            return
        # Mes propres mots valent pour une seule video ; ceux tires des noms
        # de fichiers doivent en reunir plusieurs pour meriter une categorie.
        with _without_gc():
            found = build_tag_items(
                words, videos, 1 if self.tag_family == "mine" else MIN_BUCKET
            )
        if not found:
            return
        # Dans son onglet, un mot-cle n'est pas un en-tete pose sur la liste des
        # dossiers : c'est toute la liste.
        self.all_items = found if self.tab == TAB_TAGS else found + plain
        self.items = self._filtered()
        self.apply_sort()
        self.start_harvest()
        if self.browsing:
            self.refresh_board()
        elif self.items:
            self.show_item(min(self.index, len(self.items) - 1))

    def on_scan_finished(self, mode: str, total: int) -> None:
        self.scanning = False
        self.progress.hide()
        self._refresh_scan_button()
        elapsed = time.monotonic() - (getattr(self, "_scan_started", 0.0) or
                                      time.monotonic())
        thread = self.scan_thread
        # La racine n'a pas repondu : la liste est celle du dernier passage.
        # Ni « analyse terminee », ni total de la collection, ni collection
        # declaree complete — `on_root_unreachable` l'a dit, et reessaiera.
        failed = bool(thread is not None and shiboken6.isValid(thread)
                      and getattr(thread, "failure", ""))
        if thread is not None and shiboken6.isValid(thread):
            # La liste de ce que la fenetre montrait : cent mille identifiants
            # qui ne servent plus a rien une fois la relecture finie.
            thread.known_ids = []
        if not failed:
            self.retry_timer.stop()
            self._retry_step = 0
        if mode == MODE_FOLDERS and self._scan_top and not failed:
            # La collection a ete tenue a jour au fil de la relecture
            # (`on_patch`) : les mots frequents sont a recalculer sur elle.
            self._top_tags = None
            self._plain_whole = True
            if (self.browsing and not self.levels
                    and self.all_items is not self._plain_items):
                # L'onglet affiche a pu s'ouvrir avant la fin de la lecture :
                # il se complete, sans qu'on ait a le recliquer.
                if self.tab == TAB_VIDEOS:
                    before = len(self.all_items)
                    self.show_videos_tab()
                    if len(self.all_items) != before:
                        self.refresh_board()
                elif self.tab == TAB_FAVS:
                    self.show_favorites()
            if self.origin is not None and Path(self.root) == Path(self.origin):
                # L'analyse de la racine repertorie toute la collection : son
                # total est la premiere reponse a « combien de videos ? ».
                counted = sum(i.video_count for i in self._plain_items)
                self._note_state(videos=counted, scanned_at=self._stamp())
        self._add_tag_items()
        self._show_counts()
        if thread is not None and not failed and shiboken6.isValid(thread):
            # Une analyse qui se termine doit le dire, meme quand elle n'a rien
            # trouve a changer : sans quoi on ne sait pas si elle tourne encore.
            incomplete = getattr(thread, "incomplete", 0)
            self.show_banner(
                f"✓ Analyse terminée en {elapsed:.0f} s — {total} élément(s), "
                f"{thread.rescanned} mis à jour, {thread.reused} inchangé(s)."
                + (f" {incomplete} dossier(s) lu(s) en partie : ils seront "
                   "relus au prochain passage." if incomplete else ""),
                "done" if not thread.rescanned and not incomplete else "info",
            )
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(total)
        self.start_harvest()
        if not self.items:
            self.item_title.setText("Rien à trier")
            self.item_subtitle.setText(
                "NAS injoignable : rien n'est encore connu ici. Nouvel essai "
                "automatique." if failed
                else "Aucun sous-dossier trouvé." if mode == MODE_FOLDERS
                else "Aucune vidéo trouvée directement dans ce dossier."
            )
            self.grid.set_no_videos("—")
        else:
            self.update_counter()
        self.resume_last()
        # La racine est enfin connue : c'est ici que le partage peut ouvrir.
        self.start_share()
        # Ce que la relecture vient de fabriquer dure : on le retire des passes
        # du ramasse-miettes, qui le reparcouraient sinon en gelant tout.
        gc.freeze()

    # ------------------------------------------------------------------
    # Affichage de l'élément courant
    # ------------------------------------------------------------------
    @property
    def current(self):
        if 0 <= self.index < len(self.items):
            return self.items[self.index]
        return None

    def update_counter(self) -> None:
        total = len(self.items)
        # Le mot change de sens : ce n'est plus l'attente d'une liste, c'est
        # une relecture qui tourne derriere une liste deja utilisable.
        suffix = " (vérification…)" if self.scanning else ""
        hidden = len(self.all_items) - total
        if hidden > 0:
            suffix += f" · {hidden} filtrés"
        done = self.stats["moved"] + self.stats["deleted"]
        self.controls.count.setToolTip(
            f"{min(self.index + 1, total)} / {total}{suffix}   ·   "
            f"{self.stats['moved']} déplacés · {self.stats['deleted']} supprimés · "
            f"{self.stats['skipped']} passés"
        )
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(self.index + done, total))

    def _breadcrumb(self, item) -> str:
        """Chaîne des dossiers depuis la racine du tri jusqu'à celui de l'élément.

        En mode fichier, le seul nom du dossier parent ne suffit pas à se situer :
        plusieurs dossiers portent souvent le même nom à des endroits différents.
        """
        top = (self.top_root() or item.path)
        parts = []
        current = Path(item.path).parent
        while True:
            parts.append(current.name or str(current))
            if current == top or current.parent == current or len(parts) > 12:
                break
            current = current.parent
        parts.reverse()
        if len(parts) > 6:
            parts = parts[:2] + ["…"] + parts[-3:]
        return "  ›  ".join(parts)

    def _describe(self, item) -> None:
        """Titre et ligne d'informations de l'élément affiché.

        Pour une vidéo, ce qu'on sait vient de l'index, en mémoire. L'ancienne
        version lançait ici un ffprobe **depuis le fil de l'interface** : la
        fenêtre restait figée le temps d'un aller-retour réseau, à chaque
        élément. Ce qui manque encore arrive avec le plan d'aperçus, préparé en
        tâche de fond, et la ligne se complète alors d'elle-même.
        """
        if item.kind == MODE_FOLDERS:
            # Une icône devant le titre : on sait sans lire si l'on décide du
            # sort d'un dossier entier ou d'un seul fichier.
            self.item_title.setText(item.name)
            # Le compte de fichiers melait aux videos les images et les textes
            # qui trainent a cote : on ne trie pas ceux-la.
            # Un mot-cle n'a pas de poids connu : « 0 o » ne disait rien.
            parts = ([human_size(item.size)] if item.size else []) + [
                f"{item.video_count} vidéo{'s' if item.video_count > 1 else ''}",
            ]
            if item.subdir_count:
                parts.append(f"{item.subdir_count} sous-dossier{'s' if item.subdir_count > 1 else ''}")
        else:
            info = item.info or INDEX.probe(item.path) or {}
            item.info = info
            # La durée rejoint le titre : c'est ce qu'on veut savoir en premier
            # d'une vidéo, et la ligne d'informations est déjà chargée.
            # Le nom, et dessous, en gris : la definition en « p », le poids,
            # la date. Ni codec ni dimensions exactes — on ne trie pas la-dessus.
            self.item_title.setText(item.name)
            parts = []
            if info.get("height"):
                parts.append(human_resolution(info["height"]))
            size, mtime = item.size, item.mtime
            if not size:
                from .stamps import known
                found = known(item.path)
                if found:
                    size, mtime = found
                    item.size, item.mtime = size, mtime
            if size:
                # « 0 o » ne disait rien : la taille est tue quand on l'ignore.
                parts.append(human_size(size))
        if item.mtime:
            parts.append(datetime.fromtimestamp(item.mtime).strftime("%d/%m/%Y"))
        self.item_subtitle.setText("   ·   ".join(parts))

    def _remember_item(self, item) -> None:
        """Retient ce qu'on regarde, pour y revenir au prochain lancement.

        Sur cent mille videos, retrouver son point d'arret a la main est le
        genre de corvee quotidienne qui use plus que le tri lui-meme.
        """
        if item is not None and not item.is_tag:
            self.cfg["last_item"] = item.item_id
            self.cfg["root"] = str(self.root) if self.root else ""
            self.cfg.save_soon()

    def resume_last(self) -> bool:
        """Revient sur le dernier element regarde, s'il est dans la liste.

        `last_item` etait ecrit a chaque fiche et jamais relu : sur cent mille
        videos, retrouver son point d'arret a la main etait une corvee
        quotidienne. On ne le fait qu'une fois par lancement, a la premiere
        liste qui le contient.
        """
        wanted = self._resume_id
        if not wanted:
            return False
        for position, item in enumerate(self.items):
            if item.item_id == wanted:
                self._resume_id = ""
                self.index = position
                if self.browsing:
                    self.board.scroll_to(position)
                else:
                    self.show_item(position)
                self.show_banner(f"Reprise : {item.name}", "quiet")
                return True
        # Pas dans cette liste. Si c'est une video d'un sous-dossier de la
        # racine, on y descend — une seule fois : au-dela, ce serait une
        # surprise des heures plus tard, au detour d'un autre dossier.
        if self._resume_hop or self.root is None or self.scanning:
            self._resume_id = ""
            return False
        self._resume_hop = True
        parent = Path(wanted.split("|")[0]).parent
        try:
            parent.relative_to(self.root)
        except ValueError:
            self._resume_id = ""
            return False
        if parent != self.root and parent.is_dir():
            self.jump_to(str(parent))
        else:
            self._resume_id = ""
        return False

    def _mark_current_seen(self) -> None:
        item = self.current
        if item is not None and not self.browsing and not item.is_tag:
            INDEX.mark_seen(item.item_id)

    def _burst_next(self) -> None:
        if (self.cfg["burst"] and not self.browsing and self.items
                and self.index + 1 < len(self.items)):
            self.show_item(self.index + 1)

    def toggle_burst(self) -> None:
        self.cfg["burst"] = not self.cfg["burst"]
        self.cfg.save_soon()
        if self.cfg["burst"]:
            self.show_banner("Rafale : la suivante arrive toute seule après 8 s "
                             "sans décision. Une décision, une flèche, et elle "
                             "repart de zéro.", "info")
            if not self.browsing and self.current is not None:
                self.burst_timer.start()
        else:
            self.burst_timer.stop()
            self.show_banner("Rafale arrêtée.", "quiet")

    def set_only_unseen(self, on: bool) -> None:
        self.cfg["only_unseen"] = bool(on)
        self.cfg.save_soon()
        self.controls.set_unseen(bool(on))
        self.apply_filter(self.cfg["filter_include"], self.cfg["filter_exclude"])

    def _note_decision(self) -> None:
        self._decisions += 1
        self.burst_timer.stop()

    def peek_show(self) -> None:
        """Maj maintenue : neuf instants, et les neuf premieres destinations."""
        item = self.current
        if (self.browsing or item is None or item.kind == MODE_FOLDERS
                or item.locked or self.single.peeking):
            return
        key = f"peek@{item.item_id}"
        plan = self.plans.get(key) or []
        captions = [human_duration(entry[1]) for entry in plan]
        self.single.peek_begin(captions)
        ready = self.peek_thumbs.get(key)
        if ready:
            for slot, path in ready.items():
                self.single.peek_thumb(slot, path)
            return
        if key not in self.plans:
            self.preview.request_plan(key, [str(item.path)], PeekOverlay.COUNT,
                                      page=0, one_per_video=False, urgent=True)

    def _radial_closed(self) -> None:
        """Referme sans choisir : le panneau ne doit pas rester en attente."""
        self._sorting_pane = None

    def peek_hide(self) -> None:
        self.single.peek_end()

    def toggle_dpi(self) -> None:
        """Suivre l'agrandissement de Windows, ou l'ignorer.

        Sur un ecran lointain regle a 200 %, tout est deux fois plus grand —
        confortable, mais la fenetre ne tient plus. L'ignorer rend a
        l'application sa taille en points d'ecran : plus petit, mais entier.
        Qt fige ce choix a son demarrage : il faut donc relancer.
        """
        wanted = not self.cfg["ignore_dpi"]
        self.cfg["ignore_dpi"] = wanted
        self.cfg.save()
        QMessageBox.information(
            self, "Mise à l'échelle",
            ("Prisme ignorera l'agrandissement de Windows au prochain "
             "lancement : tout sera plus petit, mais la fenêtre tiendra "
             "entière sur l'écran."
             if wanted else
             "Prisme suivra de nouveau l'agrandissement de Windows au "
             "prochain lancement.")
            + "\n\nCe choix se fige au démarrage : fermez et rouvrez Prisme.")

    # ------------------------------------------------------------------
    # Le repli
    # ------------------------------------------------------------------
    def enter_quiet(self) -> None:
        """Tout s'efface, et l'on voit autre chose. Sans rien perdre.

        Les lecteurs se taisent d'abord : une image figee ou un son qui
        continue trahirait la page en un instant.
        """
        if self.stack.currentIndex() == PAGE_QUIET:
            return
        self._quiet_from = self.stack.currentIndex()
        # Ce qui flotte par-dessus la fenetre d'abord, puis la page : elle
        # doit paraitre tout de suite. Arreter les lecteurs coute cent a cinq
        # cents millisecondes ; fait avant, c'etait autant de temps ou l'on
        # voyait encore ce qu'on voulait cacher.
        self.banner.hide()
        self.single_bar.hide()
        self.aside_bar.hide()
        self.single.marks.hide()
        self.radial.close_menu()
        first = not self.cfg["quiet_explained"]
        self.quiet_page.start()
        self.stack.setCurrentIndex(PAGE_QUIET)
        self.setWindowTitle(QUIET_TITLE)
        self.quiet_page.setFocus()
        QTimer.singleShot(0, self._hush_for_quiet)
        if first:
            # Une page dont on ne sait plus sortir pieg e au lieu de proteger :
            # la premiere fois, elle dit comment on la quitte. Une seule fois.
            self.cfg["quiet_explained"] = True
            self.cfg.save()
            QMessageBox.information(
                self, "Passer à autre chose",
                "Prisme s'efface derrière une page qui ne dit rien de ce que "
                "vous faisiez.\n\nPour revenir : Ctrl+K, la touche Échap, "
                "ou un double-clic n'importe où sur la page.\n\nCe message "
                "ne reparaîtra plus.")

    def _hush_for_quiet(self) -> None:
        """Les lecteurs se taisent, une fois la page de repli a l'ecran."""
        if self.stack.currentIndex() != PAGE_QUIET:
            return
        self._hush_players()
        self.wall.stop()
        self.close_aside()
        self.single.peek_end()
        # `close_aside` rend le clavier a la fenetre : la page le reprend.
        self.quiet_page.setFocus()

    def leave_quiet(self) -> None:
        """On revient là où l'on était."""
        if self.stack.currentIndex() != PAGE_QUIET:
            return
        self.quiet_page.stop()
        self.stack.setCurrentIndex(getattr(self, "_quiet_from", PAGE_SORT))
        self.setWindowTitle(APP_NAME)
        self.setFocus()
        if self.tab == TAB_SPLIT:
            self.show_wall()

    def toggle_quiet(self) -> None:
        if self.stack.currentIndex() == PAGE_QUIET:
            self.leave_quiet()
        else:
            self.enter_quiet()

    def toggle_veiled(self) -> None:
        """Montre, ou remasque, les dossiers mis de côté.

        Le masque vaut partout à la fois : listes, vignettes, mots-clés, mur,
        doublons, et le partage à distance. Le lever demande donc de refaire
        la liste — rien n'est relu sur le disque pour autant.
        """
        wanted = not self.cfg["show_veiled"]
        self.cfg["show_veiled"] = wanted
        self.cfg.save()
        set_veiled(self.cfg["veiled_names"], wanted)
        self._name_veil_action()
        self._top_tags = None
        self.plans = {}
        if self.share_server is not None:
            self.share_server.library.refresh()
        if self.root is not None:
            self.items = self._filtered()
            if self.tab == TAB_VIDEOS:
                self.show_videos_tab()
            self.apply_sort()
            if self.browsing:
                self.refresh_board()
            self._show_counts()
        names = ", ".join(self.cfg["veiled_names"]) or "—"
        self.show_banner(
            f"Dossiers masqués affichés : {names}." if wanted
            else f"Dossiers masqués de nouveau cachés : {names}.",
            "info" if wanted else "quiet")

    def _name_veil_action(self) -> None:
        """L'entrée du menu dit ce qu'elle fera, et sur quoi."""
        names = ", ".join(self.cfg["veiled_names"]) or "aucun"
        label = ("Masquer de nouveau les dossiers mis de côté"
                 if self.cfg["show_veiled"]
                 else f"Afficher les dossiers masqués ({names})")
        for action in self._menu_actions():
            text = action.text()
            if text.startswith("Afficher les dossiers masqués") or \
                    text.startswith("Masquer de nouveau"):
                action.setText(label)
                return

    def _menu_actions(self, menu=None) -> list:
        """Toutes les entrees du menu ⋯, sous-menus compris."""
        menu = self.overflow if menu is None else menu
        found = []
        for action in menu.actions():
            found.append(action)
            if action.menu() is not None:
                found.extend(self._menu_actions(action.menu()))
        return found

    def show_cache_place(self) -> None:
        """Dit ou vit le cache, et comment le partager avec un autre PC.

        Les fichiers se comptent hors du fil de l'interface : des centaines
        de milliers de vignettes, parfois sur le partage, figeaient la fenetre
        plusieurs secondes avant que la boite ne paraisse.
        """
        from .config import THUMB_DIR
        from .tunnel import Chore

        def count() -> int:
            return sum(len(files) for _d, _s, files in os.walk(THUMB_DIR))

        self.show_banner("Comptage des vignettes…", "info")
        Chore(count, self, fallback=0, then=self._show_cache_place).start()

    def _show_cache_place(self, count) -> None:
        from .config import INDEX_PATH, SHARED_DIR, THUMB_DIR
        self.banner.hide()
        count = int(count or 0)
        shared =("Ce cache est partagé : il a été désigné par PRISME_CACHE "
                  "ou par un fichier « prisme.cache » posé à côté du "
                  "programme.\n\n"
                  if SHARED_DIR is not None else
                  "Ce cache est propre à cet ordinateur.\n\n")
        QMessageBox.information(
            self, "Où sont les vignettes",
            f"Vignettes : {THUMB_DIR}\n"
            f"({count} fichier(s))\n\n"
            f"Index : {INDEX_PATH}\n\n"
            + shared +
            "Pour qu'un second ordinateur les réutilise au lieu de tout "
            "refabriquer :\n"
            "1. posez le dossier des vignettes sur le partage, par exemple "
            "X:\\_prisme ;\n"
            "2. à côté du programme, créez un fichier « prisme.cache » "
            "contenant ce seul chemin ;\n"
            "3. lancez Prisme sur les deux machines.\n\n"
            "Le nom d'une vignette ne dépend que du nom, de la taille et de "
            "la date de la vidéo : les deux machines fabriquent donc "
            "exactement les mêmes, et une vidéo rangée ailleurs garde les "
            "siennes.")

    def open_stall_log(self) -> None:
        """Ouvre le journal des gels : quand, combien de temps, et apres quoi."""
        if not STALL_LOG.exists():
            self.show_banner(
                f"Aucun gel relevé depuis le début ({WATCH.stalls} au total "
                "cette session). Le journal sera à : " + str(STALL_LOG), "done")
            return
        try:
            os.startfile(str(STALL_LOG))
        except OSError as exc:
            self.show_banner(f"Journal illisible : {exc}", "error")

    # ------------------------------------------------------------------
    # Le partage a distance
    # ------------------------------------------------------------------
    def share_ready(self) -> bool:
        """Un mot de passe est-il pose ? Sans lui, on ne sert rien."""
        return bool(self.cfg["share_salt"] and self.cfg["share_digest"])

    def start_share(self) -> bool:
        """Ouvre le partage si tout est reuni. Sans bruit s'il manque quelque chose.

        Appele a chaque analyse terminee : c'est la qu'on connait enfin la
        racine, et le catalogue se refait avec ce qu'on vient d'apprendre.
        """
        from .web import Server

        if not self.cfg["share"] or self.root is None or not self.share_ready():
            return False
        top = self.top_root()
        if self.share_server is not None:
            # Deja ouvert : on lui repasse simplement le catalogue a jour.
            try:
                self.share_server.library.refresh()
            except OSError:
                pass
            return True
        try:
            self.share_server = Server(
                top, self.cfg["share_salt"], self.cfg["share_digest"],
                port=int(self.cfg["share_port"] or 0),
                host=self.cfg["share_host"] or "127.0.0.1",
                expand=self.cfg["expand_parents"],
                width=self.cfg["thumb_width"])
            port = self.share_server.start()
            self.cfg["share_port"] = port
            self.cfg.save_soon()
            if self.cfg["tunnel_auto"]:
                # L'adresse publique s'ouvre d'elle-meme : c'est elle qu'on
                # veut, pas une commande a retaper a chaque fois.
                QTimer.singleShot(400, self.start_tunnel)
            elif self.cfg["tunnel_kind"] == "tailscale":
                self._check_leftover_funnel(port)
        except OSError as exc:
            self.share_server = None
            self.show_banner(f"Partage impossible : {exc}", "error")
            return False
        return True

    def _check_leftover_funnel(self, port: int) -> None:
        """Une adresse fixe restee ouverte par une seance plantee.

        `funnel --bg` survit a Prisme : s'il a plante, l'adresse publique
        menait encore a ce port sans que rien ne le montre. On le demande a
        Tailscale hors du fil de l'interface, et on le dit.
        """
        from .tunnel import Chore, find_fixed, fixed_published, fixed_state

        if not find_fixed():
            return

        def ask() -> str:
            ready, _why = fixed_state(fresh=True)
            return fixed_published(port) if ready else ""

        def then(address) -> None:
            server = self.share_server
            if (not address or server is None or server.port != port
                    or self.tunnel_address):
                return
            self.tunnel_address = address
            self.tunnel_trouble = ""
            self.show_banner(
                f"L'adresse fixe d'une séance précédente est encore ouverte : "
                f"{address} — « Partage à distance » pour la fermer.", "info")

        Chore(ask, self, fallback="", then=then).start()

    # -- le tunnel : une adresse publique, sans ouvrir de port -------------
    def start_tunnel(self) -> bool:
        """Ouvre l'adresse publique, par le chemin choisi.

        Tailscale peut mettre une minute a repondre : sa commande tourne hors
        du fil de l'interface, qui restait sinon figee jusqu'a quatre-vingts
        secondes — des le lancement, quand l'adresse s'ouvre d'elle-meme.
        Vrai veut dire « en cours ».
        """
        from .tunnel import Chore, Tunnel, find, find_fixed, open_fixed

        if self.share_server is None:
            return False
        if self.cfg["tunnel_kind"] == "tailscale":
            if not find_fixed():
                self.tunnel_trouble = "Tailscale n'est pas installé."
                return False
            if getattr(self, "_fixed_busy", False):
                return True
            from .share_dialog import _fixed_opened
            self._fixed_busy = True
            port, window = self.share_server.port, self
            # Une fermeture encore en cours passe d'abord : l'ouverture qui la
            # doublerait serait aussitot defaite.
            closing = getattr(self, "_fixed_closing", None)

            def work():
                if closing is not None:
                    closing.wait(35)
                return open_fixed(port)

            def then(result) -> None:
                self._fixed_busy = False
                _fixed_opened(window, result)
                _address, said = result
                if not _address and said:
                    self.show_banner(said, "error")

            Chore(work, self, fallback=("", "Tailscale n'a pas répondu."),
                  then=then).start()
            return True
        if self.tunnel is not None and self.tunnel.running:
            return True
        if not find():
            return False
        self.tunnel_trouble = ""
        self.tunnel = Tunnel(self.share_server.port, self)
        self.tunnel.ready.connect(self._tunnel_ready)
        self.tunnel.failed.connect(self._tunnel_failed)
        self.tunnel.closed.connect(self._tunnel_closed)
        return self.tunnel.start()

    def _from_old_tunnel(self) -> bool:
        """Vrai pour un signal venu d'un tunnel deja remplace ou ferme."""
        sender = self.sender()
        return sender is not None and sender is not self.tunnel

    def _tunnel_ready(self, address: str) -> None:
        if self._from_old_tunnel():
            return
        self.tunnel_address = address
        self.tunnel_trouble = ""
        self.show_banner(f"Adresse publique ouverte : {address}", "done")

    def _tunnel_failed(self, why: str) -> None:
        if self._from_old_tunnel():
            return
        self.tunnel_trouble = why
        self.tunnel_address = ""
        # Une chute en route se dit : l'adresse envoyee ne mene plus nulle
        # part, et la suivante sera differente.
        self.show_banner(why, "error")

    def _tunnel_closed(self) -> None:
        if self._from_old_tunnel():
            return
        self.tunnel_address = ""

    def stop_tunnel(self, wait: bool = False) -> None:
        """Ferme l'adresse publique.

        Pour Tailscale, hors du fil de l'interface (jusqu'a trente secondes),
        sauf a la fermeture de Prisme (`wait`), ou la fenetre est deja
        masquee et ou l'adresse ne doit pas survivre au programme. L'adresse
        n'est oubliee que si Tailscale confirme l'avoir fermee : sinon elle est
        peut-etre encore publiee, et on le dit.
        """
        if self.cfg["tunnel_kind"] == "tailscale" and self.tunnel_address:
            from .share_dialog import _fixed_closed
            from .tunnel import FIXED_CLOSED, Chore, close_fixed
            window = self

            def then(said) -> None:
                _fixed_closed(window, said)
                if said != FIXED_CLOSED:
                    self.show_banner(
                        f"L'adresse fixe est peut-être encore ouverte : {said}",
                        "error")

            if wait:
                then(close_fixed())
            else:
                import threading
                closed = threading.Event()
                self._fixed_closing = closed

                def work() -> str:
                    try:
                        return close_fixed()
                    finally:
                        closed.set()

                Chore(work, self, fallback="Tailscale n'a pas répondu.",
                      then=then).start()
        if self.tunnel is not None:
            self.tunnel.stop()
            self.tunnel = None
        if self.cfg["tunnel_kind"] != "tailscale":
            self.tunnel_address = ""

    def stop_share(self, wait: bool = False) -> None:
        self.stop_tunnel(wait)
        if self.share_server is not None:
            self.share_server.stop()
            self.share_server = None

    def set_share(self, on: bool) -> None:
        self.cfg["share"] = bool(on)
        self.cfg.save()
        if on:
            if self.start_share():
                self.show_banner("Partage à distance ouvert.", "done")
        else:
            self.stop_share()
            self.show_banner("Partage à distance fermé.", "quiet")

    def set_share_password(self, given: str) -> None:
        from .web import hash_password

        salt, digest = hash_password(given)
        self.cfg["share_salt"], self.cfg["share_digest"] = salt, digest
        self.cfg.save()
        # Changer la serrure ferme les sessions ouvertes : c'est le but.
        self.stop_share()
        self.start_share()

    def share_state(self) -> str:
        if not self.cfg["share"]:
            return "Partage fermé."
        if not self.share_ready():
            return "Aucun mot de passe : le partage reste fermé."
        if self.share_server is None:
            return ("Prêt, en attente d'une racine analysée."
                    if self.root is None else "Partage arrêté.")
        return (f"Ouvert — {len(self.share_server.library.videos)} vidéo(s) "
                f"servies depuis {self.share_server.library.root}")

    def share_link(self) -> str:
        """L'adresse publique si le tunnel est ouvert, la locale sinon."""
        if self.tunnel_address:
            return self.tunnel_address + "/"
        if self.share_server is None:
            return ""
        return f"http://127.0.0.1:{self.share_server.port}/"

    def set_tunnel_kind(self, kind: str) -> None:
        """Change de chemin : l'adresse d'avant se ferme avec l'ancien."""
        if kind == self.cfg["tunnel_kind"]:
            return
        self.stop_tunnel()
        self.cfg["tunnel_kind"] = kind
        self.cfg.save()

    def tunnel_state(self) -> str:
        from .tunnel import find, fixed_state

        if self.cfg["tunnel_kind"] == "tailscale":
            if self.tunnel_address:
                return f"Adresse fixe ouverte : {self.tunnel_address}"
            if getattr(self, "_fixed_busy", False):
                return "Ouverture de l'adresse fixe…"
            ready, why = fixed_state()
            return why if not ready else f"Prêt — {why}"
        if not find():
            return "cloudflared n'est pas installé."
        if self.tunnel_address:
            return "Adresse publique ouverte."
        if self.tunnel is not None and self.tunnel.running:
            # Une relance en attente apres une chute compte comme « en
            # marche » : c'est alors la chute qu'il faut dire.
            return self.tunnel_trouble or "Ouverture de l'adresse publique…"
        return self.tunnel_trouble or "Adresse publique fermée."

    def open_share(self) -> None:
        ShareDialog(self, self).exec()

    def open_radial(self) -> None:
        """Clic droit : les destinations en rond autour de la souris."""
        item = self.current
        if self.browsing or item is None or item.locked:
            return
        if not self.radial.isHidden():
            return self.radial.close_menu()
        entries = [(d.get("key", "?"), d.get("label") or Path(d["path"]).name)
                   for d in list(self.cfg.destinations)[:RadialMenu.MAX]]
        if not entries:
            self.show_banner("Aucune destination : ⋯ → Destinations…", "quiet")
            return
        self.radial.open_at(QCursor.pos(), entries)

    def _radial_chosen(self, index: int) -> None:
        destinations = list(self.cfg.destinations)
        if not (0 <= index < len(destinations)):
            return
        sorting = getattr(self, "_sorting_pane", None)
        if sorting is not None:
            # Venu du mur : on range cette video-la, puis le panneau se
            # remplit d'une autre.
            self._sorting_pane = None
            pane_at, path = sorting
            if self.move_one(path, destinations[index]):
                self.wall.refill_one(pane_at)
            return
        self.act_move(destinations[index])

    def peek_seek(self, slot: int) -> None:
        """Une case cliquee : la lecture saute a cet instant, la mosaique se ferme."""
        item = self.current
        if item is None:
            return
        plan = self.plans.get(f"peek@{item.item_id}") or []
        if 0 <= slot < len(plan):
            self.single.player.setPosition(int(plan[slot][1] * 1000))
            self.single.player.play()
        self.peek_hide()

    def save_search(self) -> None:
        """Retient la recherche en cours sous un nom, avec son tri et « Non vus »."""
        query = self.controls.include.text().strip()
        unseen = bool(self.cfg["only_unseen"])
        if not query and not unseen and self.sort_mode in ("", "random"):
            self.show_banner("Rien à enregistrer : le champ est vide et rien "
                             "n'est filtré.", "quiet")
            return
        name, ok = QInputDialog.getText(
            self, "Enregistrer la recherche", "Sous quel nom ?", text=query or "Non vus")
        name = (name or "").strip()
        if not ok or not name:
            return
        searches = [s for s in list(self.cfg["searches"] or [])
                    if s.get("name") != name]
        searches.append({"name": name, "query": query, "sort": self.sort_mode,
                         "unseen": unseen, "tab": self.tab})
        self.cfg["searches"] = searches
        self.cfg.save()
        self.show_banner(f"Recherche « {name} » enregistrée — menu ⋯ → "
                         "Recherches enregistrées.", "done")

    def _fill_searches_menu(self) -> None:
        menu = self.searches_menu
        menu.clear()
        searches = list(self.cfg["searches"] or [])
        if not searches:
            none = menu.addAction("(aucune pour l'instant)")
            none.setEnabled(False)
            return
        for entry in searches:
            menu.addAction(entry.get("name", "?"),
                           lambda checked=False, e=entry: self.apply_search(e))
        menu.addSeparator()
        menu.addAction("Oublier une recherche…", self.forget_search)

    def apply_search(self, entry: dict) -> None:
        """Repose une recherche : onglet, requête, tri, puis « Non vus »."""
        tab = entry.get("tab") or self.tab
        if tab != self.tab and tab in TABS and tab != TAB_SPLIT:
            self.set_tab(tab)
        field = self.controls.include
        field.blockSignals(True)
        field.setText(entry.get("query", ""))
        field.blockSignals(False)
        self.on_controls_changed()
        sort = entry.get("sort") or "random"
        self.controls.set_sort(sort)
        self.set_sort(sort)
        self.set_only_unseen(bool(entry.get("unseen")))
        self.show_banner(f"Recherche « {entry.get('name', '')} »", "quiet")

    def forget_search(self) -> None:
        names = [s.get("name", "?") for s in list(self.cfg["searches"] or [])]
        if not names:
            return
        name, ok = QInputDialog.getItem(
            self, "Oublier une recherche", "Laquelle ?", names, 0, False)
        if not ok:
            return
        self.cfg["searches"] = [s for s in self.cfg["searches"] if s.get("name") != name]
        self.cfg.save()
        self.show_banner(f"« {name} » oubliée.", "quiet")

    def _rebuild_commands(self, force: bool = False) -> None:
        """Refait la barre des touches seulement quand elle a change.

        Elle etait recreee a chaque video : une quinzaine de widgets detruits
        et reconstruits par fleche, pour afficher exactement la meme chose.
        """
        label = DELETE_LABELS.get(self.cfg["delete_mode"], "Supprimer")
        signature = (repr(self.cfg.destinations), label)
        if force or signature != self._commands_signature:
            self._commands_signature = signature
            self.commands.rebuild(self.cfg.destinations, label)

    def show_item(self, index: int) -> None:
        if not self.items:
            return
        mark(f"show_item {index}")
        self.index = max(0, min(index, len(self.items) - 1))
        item = self.items[self.index]
        self._remember_item(item)
        self.update_counter()
        self._rebuild_commands()

        self._describe(item)

        self.stars.show()
        self.stars.set_value(self.ratings.get(item.path))
        self.item_parent.setText(self._breadcrumb(item))
        self.item_parent.setToolTip(str(Path(item.path).parent))
        # Le fil d'Ariane mene jusqu'au dossier de l'element, et non plus
        # seulement jusqu'a la racine regardee. En vue a plat, ou toutes les
        # videos de la collection se cotoient, le seul nom du fichier ne disait
        # plus du tout d'ou il sortait — et ses segments restent cliquables,
        # donc il sert aussi a y retourner.
        top = (self.top_root() or item.path)
        folder = Path(item.path) if item.kind == MODE_FOLDERS else Path(item.path).parent
        try:
            folder.relative_to(top)
        except ValueError:
            folder = self.root or top
        self.crumbs.set_path(top, folder)
        # Le nom de ce qu'on regarde finit le fil, une seule fois : un
        # dossier y est deja ; une video et un mot-cle s'y ajoutent.
        if item.is_tag:
            self.crumbs.append_leaf(item.name)
        elif item.kind != MODE_FOLDERS:
            self.crumbs.append_leaf(item.name)

        if item.pending:
            self.show_banner(f"Transfert en cours vers {item.status_detail}…", "info")
        elif item.processed:
            tone = "done" if item.status == "moved" else "error"
            self.show_banner(f"Déjà traité : {item.status_detail}", tone)

        self._apply_selectors()

        viewer = self.grid if item.kind == MODE_FOLDERS else self.single
        self.viewer.setCurrentWidget(viewer)
        viewer.set_muted(self.cfg["muted"])

        if item.locked:
            # Inutile de relancer des ffmpeg sur un élément qui n'est plus là.
            self.grid.set_no_videos("—")
            self.single.stop()
            return

        if item.kind == MODE_FOLDERS:
            self.grid.set_item(self._plan_key(item, self.page_of(item)), "…")
            if not item.videos:
                self.grid.set_no_videos("aucune vidéo")
        else:
            self.single.set_item(str(item.path), "…")
        self.single.peek_end()
        self.radial.close_menu()
        self.seen_timer.start()
        if self.cfg["burst"] and item.kind != MODE_FOLDERS:
            self.burst_timer.start()
        else:
            self.burst_timer.stop()

        self._sort_videos(item)
        self._request_previews(item, current=True)
        self._update_page_bar()
        keep = {self._plan_key(item, self.page_of(item))}
        for offset in (1, 2):
            if self.index + offset < len(self.items):
                nxt = self.items[self.index + offset]
                keep.add(self._plan_key(nxt, self.page_of(nxt)))
                self._request_previews(nxt, current=False)
                if (offset == 1 and item.kind != MODE_FOLDERS
                        and nxt.kind != MODE_FOLDERS and not nxt.locked):
                    # La suivante se charge des maintenant dans le lecteur de
                    # reserve : la fleche ne montrera plus de noir.
                    self.single.preload(str(nxt.path))
        # Uniquement hors planche : les vignettes de la planche portent des
        # cles « board@… » que cet ensemble ne contient jamais, et les annuler
        # revenait a vider la planche de ses images a chaque fois qu'une fiche
        # se preparait en coulisse. C'est pourquoi l'onglet Videos restait
        # desesperement gris.
        if not self.browsing:
            self.preview.cancel_except(keep)

    # ------------------------------------------------------------------
    # Apercus, par pages de `thumb_count`
    # ------------------------------------------------------------------
    def page_of(self, item) -> int:
        return self.pages.get(item.item_id, 0)

    def total_pages(self, item) -> int:
        if item.kind != MODE_FOLDERS:
            return 1
        return page_count(self._preview_videos(item), self.cfg["thumb_count"])

    def _preview_videos(self, item) -> list:
        """Les videos d'un dossier ou d'un mot-cle qui passent les filtres.

        Poser « verticales, non vues » puis ouvrir un mot-cle doit montrer
        ses videos verticales non vues, et non toutes. La recherche par nom,
        elle, a deja choisi le mot-cle : on ne la reapplique pas dedans.
        """
        if item.kind != MODE_FOLDERS or not item.videos:
            return item.videos
        rules = self.criteria or {}
        wanted = rules.get("orientations")
        narrowing = (bool(self.cfg["only_unseen"])
                     or (wanted is not None and len(wanted) == 1))
        if not narrowing:
            return item.videos
        key = (item.item_id, len(item.videos), repr(sorted(rules.items())),
               bool(self.cfg["only_unseen"]), self.tab)
        cache = self.__dict__.setdefault("_preview_cache", {})
        if key in cache:
            return cache[key]
        keep = self._matcher(ignore_text=True)
        flat = self._flat_cache
        kept = []
        for video in item.videos:
            probe = flat.get(str(video))
            if probe is None:
                probe = Item(path=_as_path(video), kind=MODE_FILES,
                             videos=[video], video_count=1, file_count=1)
            if keep(probe):
                kept.append(video)
        if len(cache) > 300:
            cache.clear()
        cache[key] = kept
        return kept

    def _plan_key(self, item, page: int) -> str:
        """Identifiant porte par les signaux : il doit distinguer les pages."""
        return f"{item.item_id}@{page}"

    def _request_previews(self, item, current: bool, page: int | None = None) -> None:
        """Demande les aperçus d'un élément. `current` le fait passer devant.

        Les deux éléments suivants sont préparés d'avance, mais derrière : sans
        cette distinction, leurs vingt vignettes occupaient les huit fils
        pendant que la case qu'on regarde attendait son tour.
        """
        if not item.videos or item.locked:
            return
        page = self.page_of(item) if page is None else page
        # Un dossier montre une image par video, par pages ; une video montre
        # les cinq reperes de sa pellicule. En demander dix pour une video
        # faisait tourner ffmpeg cinq fois pour rien.
        count = (self.cfg["thumb_count"] if item.kind == MODE_FOLDERS
                 else SinglePlayer.STRIP_COUNT)
        key = self._plan_key(item, page)
        plan = self.plans.get(key)
        videos = self._preview_videos(item)
        if not videos:
            if current:
                self.grid.set_no_videos("aucune vidéo ne passe les filtres")
            return
        if plan is None:
            self.preview.request_plan(
                key, videos, count,
                page=page, one_per_video=item.kind == MODE_FOLDERS,
                urgent=current,
                blind=item.kind == MODE_FOLDERS,
            )
            return
        if current:
            self._apply_plan(item, plan)
        # Un dossier montre une image par video : l'image-cle qui precede
        # l'instant suffit, et coute dix fois moins a lire sur le partage. La
        # pellicule d'une video, elle, doit garder des images distinctes.
        keyframe = item.kind == MODE_FOLDERS
        for slot, entry in enumerate(plan):
            self.preview.request_thumb(key, slot, entry[0], entry[1],
                                       urgent=current, keyframe=keyframe)

    def _apply_plan(self, item, plan: list) -> None:
        viewer = self.grid if item.kind == MODE_FOLDERS else self.single
        viewer.set_plan(plan)

    def _current_key(self) -> str:
        current = self.current
        return self._plan_key(current, self.page_of(current)) if current else ""

    def on_plan_ready(self, key: str, plan: list) -> None:
        self.plans[key] = plan
        if key.startswith("peek@"):
            peeked = self._wall_peek
            for slot, entry in enumerate(plan):
                self.single.peek.set_caption(slot, human_duration(entry[1]))
                if peeked and peeked[1] == key and peeked[0] < len(self.wall.panes):
                    self.wall.panes[peeked[0]].peek.set_caption(
                        slot, human_duration(entry[1]))
                self.preview.request_thumb(key, slot, entry[0], entry[1], True)
            return
        if key.startswith("board@"):
            # Seulement pour une carte de la page affichee : une page quittee
            # ne doit plus rien lancer.
            position = self._board_position_of(key)
            if position >= 0 and plan:
                self.board.set_source(position, plan[0])
                self.preview.request_thumb(key, 0, plan[0][0], plan[0][1],
                                           keyframe=True)
                if not plan[0][2]:
                    self.preview.request_info(key, 0, plan[0][0])
            return
        current = self.current
        if current is not None and key == self._current_key():
            self._apply_plan(current, plan)
            # Le sondage a eu lieu dans le fil du plan : la fiche se complete
            # sans que l'interface ait attendu quoi que ce soit.
            if current.kind != MODE_FOLDERS and not current.info:
                self._describe(current)
            self._update_page_bar()
        urgent = key == self._current_key()
        # Une image par video (un dossier) : l'image-cle suffit. Plusieurs
        # instants d'une meme video (la pellicule) : ils doivent differer.
        keyframe = len({str(entry[0]) for entry in plan}) == len(plan)
        for slot, entry in enumerate(plan):
            self.preview.request_thumb(key, slot, entry[0], entry[1], urgent,
                                       keyframe=keyframe)
            if not entry[2]:
                # Duree inconnue : l'image part d'abord, le sondage suivra.
                self.preview.request_info(key, slot, entry[0])

    def on_info_ready(self, key: str, slot: int, duration: float,
                      height: int) -> None:
        """La durée et la résolution arrivent après l'image, et la complètent."""
        if key.startswith("peek@"):
            return
        if key.startswith("board@"):
            position = self._board_position_of(key)
            if position >= 0:
                self.board.set_info(position, duration, height)
            return
        current = self.current
        if current is None or key != self._current_key():
            return
        plan = self.plans.get(key)
        if plan and slot < len(plan):
            video, ts, _d, _h = plan[slot]
            plan[slot] = (video, ts, duration, height)
        if current.kind == MODE_FOLDERS:
            self.grid.set_info(slot, duration, height)

    def on_thumb_ready(self, key: str, slot: int, path: str) -> None:
        if key.startswith("peek@"):
            self.peek_thumbs.setdefault(key, {})[slot] = path
            current = self.current
            if (self.single.peeking and current is not None
                    and key == f"peek@{current.item_id}"):
                self.single.peek_thumb(slot, path)
            peeked = self._wall_peek
            if peeked and peeked[1] == key and peeked[0] < len(self.wall.panes):
                self.wall.panes[peeked[0]].peek.set_thumb(slot, path)
            return
        if key.startswith("board@"):
            position = self._board_position_of(key)
            if position >= 0:
                self.board.set_thumb(position, path)
            return
        current = self.current
        if current is None or key != self._current_key():
            return
        viewer = self.grid if current.kind == MODE_FOLDERS else self.single
        viewer.set_thumb(slot, path)

    def on_thumb_failed(self, key: str, slot: int) -> None:
        if key.startswith("board@") or key.startswith("peek@"):
            return
        current = self.current
        if current is None or key != self._current_key():
            return
        viewer = self.grid if current.kind == MODE_FOLDERS else self.single
        viewer.set_failed(slot)

    def on_board_page(self, first: int, last: int, total: int) -> None:
        if self.browsing:
            self._show_counts()
        self._harvest_here(first, last)
        # Une autre page, ou une autre liste : les images de celle qu'on
        # quitte passaient avant celles qu'on regarde, et leurs ffmpeg
        # occupaient la ligne. Seulement quand la page change vraiment — une
        # carte ajoutee par l'analyse emet aussi ce signal.
        seen = (id(self.board.items), self.board.page)
        if seen != getattr(self, "_board_page_seen", None):
            self._board_page_seen = seen
            lo, hi = self.board._page_bounds()
            self.preview.cancel_prefix(
                "board@", keep={f"board@{item.item_id}"
                                for item in self.board.items[lo:hi]})

    def _harvest_here(self, first: int, last: int) -> None:
        """Fait passer la page regardée en tête de la récolte.

        Elle parcourait la collection dans l'ordre : arrivé à la page cinq, on
        attendait ses aperçus pendant qu'elle préparait tranquillement la page
        une. Ce qui est sous les yeux passe devant.
        """
        harvester = self.preview.harvester
        if harvester is None:
            return
        keys = {item.item_id for item in self.items[max(0, first):last + 1]}
        if keys:
            harvester.prioritise(keys)

    def change_page(self, step: int) -> None:
        """Page suivante ou precedente : de cartes en planche, d'apercus en fiche."""
        if self.browsing:
            self.board.set_page(self.board.page + step)
            return
        item = self.current
        if item is None or item.kind != MODE_FOLDERS:
            return
        total = self.total_pages(item)
        page = max(0, min(self.page_of(item) + step, total - 1))
        if page == self.page_of(item):
            return
        self.pages[item.item_id] = page
        self.grid.stop()
        self.grid.set_item(self._plan_key(item, page), "…")
        self._request_previews(item, current=True, page=page)
        self._update_page_bar()

    def set_sort(self, mode: str) -> None:
        """Classement de la liste : nom, duree, note, taille, ou au hasard."""
        if mode == self.sort_mode:
            return
        self.sort_mode = mode
        self.cfg["sort_mode"] = mode
        self.cfg.save()
        self.plans = {}
        self.pages = {}
        self.apply_sort()
        if self.browsing:
            self.refresh_board()
        elif self.items:
            self.show_item(min(self.index, len(self.items) - 1))

    def apply_sort(self) -> None:
        """Reclasse la liste visible selon le mode choisi."""
        import random
        if (self.tab == TAB_TAGS and self.sort_mode in ("random", "name", "", None)
                and self.items and all(i.is_tag for i in self.items)):
            # Les mots-cles se lisent du plus frequent au moins frequent — et
            # dans le meme ordre d'une fois a l'autre.
            self.items.sort(key=lambda i: (-i.video_count, i.sort_name))
            return

        def duration_of(item):
            total = 0.0
            for video in item.videos[:8]:
                total += (INDEX.probe(video) or {}).get("duration", 0.0)
            return total

        if self.sort_mode == "random":
            random.shuffle(self.items)
        elif self.sort_mode == "duration_desc":
            self.items.sort(key=duration_of, reverse=True)
        elif self.sort_mode == "duration_asc":
            self.items.sort(key=duration_of)
        elif self.sort_mode == "stars_desc":
            self.items.sort(key=lambda i: self.ratings.get(i.path), reverse=True)
        elif self.sort_mode == "stars_asc":
            self.items.sort(key=lambda i: self.ratings.get(i.path))
        elif self.sort_mode == "size_desc":
            self.items.sort(key=lambda i: i.size, reverse=True)
        elif self.sort_mode == "size_asc":
            self.items.sort(key=lambda i: i.size)
        elif self.sort_mode in ("resolution_desc", "resolution_asc"):
            self.items.sort(key=lambda i: known_media(i)[1],
                            reverse=self.sort_mode.endswith("desc"))
        else:
            self.items.sort(key=_BY_NAME)

    def _sort_videos(self, item) -> None:
        """Reclasse les vidéos d'un dossier selon le mode choisi.

        Le tri s'appuie sur les durées déjà sondées ; celles qui ne le sont pas
        encore comptent pour zéro et remonteront au prochain affichage.
        """
        if item.kind != MODE_FOLDERS or not item.videos:
            return
        # Par nom, sauf si l'on classe par duree : passer de 4 a 6 apercus
        # rebattait les videos, parce que le classement suivait des durees
        # qui arrivaient au fil de l'eau. Les premieres restent les premieres.
        if self.sort_mode in ("duration_desc", "duration_asc"):
            def duration_of(path):
                return (INDEX.probe(path) or {}).get("duration", 0.0)
            item.videos.sort(key=lambda path: (duration_of(path),
                                               str(path).lower()),
                             reverse=self.sort_mode == "duration_desc")
        else:
            item.videos.sort(key=lambda path: str(path).lower())

    def _refresh_random_here(self) -> None:
        """Le hasard « dans ce qu'on regarde » : seulement si l'on regarde une
        partie — des resultats filtres, ou un dossier ou l'on est entre."""
        narrowed = bool(self.levels) or bool(self._active_filters())
        self.controls.random_here.setVisible(
            self.browsing and self.tab != TAB_SPLIT and bool(self.items) and narrowed)

    def _show_counts(self) -> None:
        """Une seule ligne dit ce qui est montre, ce qui est masque, et ou l'on en est."""
        self.controls.set_filters(self._active_filters())
        self._refresh_random_here()
        hidden = self._sortable_count() - len(self.items)
        if self.browsing:
            first, last = self.board._page_bounds()
            shown = (f"{first + 1 if self.items else 0}–{last} sur "
                     f"{self._thousands(len(self.items))}")
            self.controls.count.setToolTip(
                f"{self._thousands(hidden)} élément(s) masqué(s) par les filtres"
                if hidden else "")
            self.controls.set_page(
                shown,
                self.board.page > 0,
                self.board.page < self.board.total_pages() - 1,
            )
            return
        total = len(self.items)
        done = self.stats["moved"] + self.stats["deleted"]
        self.controls.set_page(
            f"{min(self.index + 1, total)} / {total}"
            + (f"  ·  {hidden} filtrés" if hidden else "")
            + (f"  ·  {done} traités" if done else "")
            + self._session_text(),
            False, False,
        )

    def _session_text(self) -> str:
        """« 143 décisions · 41 min · 3,5/min » — ce qu'on a fait, depuis quand."""
        if not self._decisions:
            return ""
        minutes = max(1.0, (time.monotonic() - self._session_started) / 60.0)
        rate = self._decisions / minutes
        return (f"  ·  {self._decisions} décision{'s' if self._decisions > 1 else ''}"
                f" · {int(minutes)} min · {rate:.1f}/min").replace(".", ",")

    def _update_page_bar(self) -> None:
        self._show_counts()

    def show_banner(self, text: str, tone: str = "info") -> None:
        """Un bandeau, quatre tons : info, quiet, done, error. Rien d'autre."""
        color = BANNER_TONES.get(tone, BANNER_TONES["info"])
        self.banner.setText(text)
        self.banner.setStyleSheet(
            f"QLabel {{ background: {color}; color: #e9eef4; border-radius: 8px;"
            " padding: 8px 14px; font-weight: 600; }")
        if self.stack.currentIndex() == PAGE_QUIET or self.isMinimized():
            return
        self._place_banner()
        self.banner.show()
        self.banner.raise_()
        self.banner_timer.start(4000)

    def _place_banner(self) -> None:
        """En haut de la zone d'image, centre : il se lit sans rien pousser."""
        anchor = self.viewer if self.viewer.isVisible() else self.centralWidget()
        width = max(240, min(680, anchor.width() - 40))
        height = self.banner.heightForWidth(width)
        if height <= 0:
            height = self.banner.sizeHint().height()
        corner = anchor.mapToGlobal(QPoint((anchor.width() - width) // 2, 10))
        self.banner.setGeometry(corner.x(), corner.y(), width, height)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _hush_players(self) -> None:
        """Met en pause ce qui joue, sans rien attendre ni rien decharger.

        Une pause, pas un arret : `stop()` defait le decodage et coutait cent
        a cinq cents millisecondes par lecteur, a chaque changement d'onglet,
        a chaque Echap. Un lecteur deja arrete n'est pas touche — une pause le
        rechargerait pour montrer sa premiere image. Les verrous sur les
        fichiers, eux, se lachent avant tout deplacement (`_release_media`).
        """
        for player in (self.grid.player, self.single.player,
                       self.board.player, self.aside_player.player):
            if player.playbackState() == player.PlaybackState.PlayingState:
                player.pause()
        self.grid.video.hide()
        self.board.video.hide()

    def _release_media(self, target=None) -> None:
        """Relâche les handles sur les fichiers avant une opération disque.

        Deux sources de verrous sous Windows : le lecteur Qt, et les ffmpeg de
        préchargement qui fabriquent les vignettes des éléments suivants.

        Avec une cible, seuls les lecteurs qui la montrent sont vidés : vider
        une source coute de trente a deux cents millisecondes par lecteur, et
        on le payait quatre fois a chaque touche de tri. La video suivante,
        deja chargee dans la reserve de la fiche, y reste : elle s'affiche par
        simple echange. Les ffmpeg qui lisent la cible sont arretes net, sans
        rien attendre — c'est le transfert, en tache de fond, qui attend
        qu'elle soit libre.
        """
        if target is None:
            # La planche et le lecteur de cote lisent eux aussi des fichiers :
            # les oublier laissait un verrou Windows sur la video qu'on venait
            # d'ecarter, et le deplacement echouait sans rien dire.
            for player in (self.grid.player, self.single.player,
                           self.board.player, self.aside_player.player):
                player.stop()
                player.setSource(QUrl())
            self.single.release()
            for pane in self.wall.panes:
                pane.stop()
            self.grid.video.hide()
            self.board.video.hide()
            self.preview.cancel_all()
            return
        from .widgets import _within

        def shows(player) -> bool:
            source = player.source()
            return (not source.isEmpty() and source.isLocalFile()
                    and _within(source.toLocalFile(), target))

        for player, video in ((self.grid.player, self.grid.video),
                              (self.board.player, self.board.video)):
            if shows(player):
                player.stop()
                player.setSource(QUrl())
                video.hide()
        for single in (self.single, self.aside_player):
            decks = getattr(single, "decks", ())
            if any(deck.path and _within(deck.path, target) for deck in decks) \
                    or shows(single.player):
                single.release(target)
        for pane in self.wall.panes:
            if pane.video_path and _within(pane.video_path, target):
                pane.clear()
        self.preview.release(target)

    # ------------------------------------------------------------------
    # Vue planche et notation
    # ------------------------------------------------------------------
    @property
    def browsing(self) -> bool:
        """Vrai quand on parcourt des vignettes, faux quand on en edite une.

        C'etait auparavant un onglet, ce qui posait l'edition a cote des trois
        collections alors qu'elle en est l'etage du dessous : on y entre en
        cliquant une vignette, on en sort par Echap, et l'onglet ne bouge pas.
        """
        return not self._editing

    @browsing.setter
    def browsing(self, value: bool) -> None:
        self._editing = not value

    @property
    def view(self) -> str:
        """Conservé pour l'enregistrement : l'onglet dit déjà tout."""
        return VIEW_BROWSE if self.browsing else VIEW_EDIT

    def _videos_from_items(self) -> list:
        """Toutes les vidéos déjà repérées, sans rien redemander au disque.

        Passer aux vidéos relançait une analyse complète de la racine, alors que
        l'analyse des dossiers vient d'en relever le contenu. Sur un partage
        réseau, c'était plusieurs minutes pour une information déjà connue.
        """
        seen = set()
        found = []
        # La collection, et non la liste affichee : depuis les favoris ou un
        # sous-dossier, l'onglet ne montrait que ceux-la.
        for item in self._collection():
            if item.processed:
                # Un dossier range ou ecarte n'est plus la : ses videos non
                # plus, jusqu'a ce qu'un Ctrl+Z le ramene.
                continue
            for video in item.videos:
                key = str(video)
                if key in seen or under_veiled(video):
                    continue
                seen.add(key)
                found.append(video)
        return found

    def _collection(self) -> list:
        """Les dossiers de la racine, d'ou que l'on regarde.

        Tant que la racine n'a pas ete lue, on se contente des vrais dossiers
        de la liste affichee.
        """
        top = self.top_root()
        if (self._plain_root is not None and top is not None
                and Path(self._plain_root) == Path(top)):
            return self._plain_items
        return [i for i in self.all_items if not i.is_tag]

    def restore_folders(self) -> bool:
        """Remet la liste des dossiers déjà analysée, sans rien relire.

        Sans cela, chaque aller-retour entre les onglets relançait l'analyse
        complète de la racine — plusieurs minutes sur un partage réseau, pour
        retrouver exactement ce qu'on venait de quitter.
        """
        if self._plain_root is None or self.root is None or \
                Path(self._plain_root) != Path(self.root):
            return False
        # La liste elle-meme, pas une copie : la relecture en cours continue
        # de l'alimenter, et ce qu'on y range en disparait partout.
        self.all_items = self._plain_items
        self.items = self._filtered()
        self.mode = MODE_FOLDERS
        self.apply_sort()
        self.index = 0
        self._show_counts()
        return True

    def wall_sort(self, index: int, path: str) -> None:
        """Clic droit sur un panneau : les destinations, pour ranger la vidéo."""
        entries = [(d.get("key", "?"), d.get("label") or Path(d["path"]).name)
                   for d in list(self.cfg.destinations)[:RadialMenu.MAX]]
        if not entries:
            self.show_banner("Aucune destination : ⋯ → Destinations…", "quiet")
            return
        self._sorting_pane = (index, path)
        self.radial.open_at(QCursor.pos(), entries)

    def move_one(self, path, dest: dict) -> bool:
        """Range **ce fichier**, sans passer par la liste.

        `act_move` travaille sur l'element courant ; depuis le mur, la vidéo
        qu'on range n'est pas celle-là.
        """
        source = Path(path)
        dest_dir = Path(dest["path"])
        label = dest.get("label") or dest_dir.name
        state = actions.probe(source)
        if state != "ok":
            self.show_banner(
                f"Introuvable : {source.name}" if state == "absent" else
                f"NAS injoignable : « {source.name} » n'a pas été déplacé.",
                "error")
            return False
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.show_banner(f"Destination inaccessible : {actions.describe(exc)}",
                             "error")
            return False
        # Le panneau qui la montre la lache, et les ffmpeg qui la lisent sont
        # arretes : sans cela, le deplacement butait sur le fichier ouvert.
        self._release_media(source)
        self.transfers.submit(Transfer(
            kind="move", src=source, dest=dest_dir, label=label, item_id=""))
        self.show_banner(f"« {source.name} » → {label}", "done")
        return True

    def wall_peek(self, index: int, path: str) -> None:
        """Clic droit sur un panneau : ses neuf instants, par-dessus lui."""
        pane = self.wall.panes[index] if 0 <= index < len(self.wall.panes) else None
        if pane is None:
            return
        if pane.peeking:
            pane.peek_end()
            self._wall_peek = None
            return
        self.wall.end_peeks()
        key = f"peek@{path}"
        plan = self.plans.get(key) or []
        pane.peek_begin([human_duration(entry[1]) for entry in plan])
        self._wall_peek = (index, key)
        ready = self.peek_thumbs.get(key)
        if ready:
            for slot, thumb in ready.items():
                pane.peek.set_thumb(slot, thumb)
            return
        if key not in self.plans:
            self.preview.request_plan(key, [path], PeekOverlay.COUNT,
                                      page=0, one_per_video=False, urgent=True)

    def wall_peek_chosen(self, index: int, slot: int) -> None:
        pane = self.wall.panes[index] if 0 <= index < len(self.wall.panes) else None
        if pane is None:
            return
        plan = self.plans.get(f"peek@{pane.video_path}") or []
        if 0 <= slot < len(plan):
            pane.seek(plan[slot][1])
        pane.peek_end()
        self._wall_peek = None

    def probe_for_wall(self) -> None:
        """Sonde en fond ce dont on ignore l'orientation, tant que le mur a faim.

        Le vivier est strict : sans sondage, une collection neuve donnerait
        un mur vide. On va donc chercher la resolution des videos inconnues,
        quelques-unes a la fois, et le mur se garnit a mesure.
        """
        if self.tab != TAB_SPLIT or self._wall_pinned:
            return
        if self.wall_prober is not None or not self._wall_unsure:
            return
        if len(self.wall.pool) >= max(12, len(self.wall.panes) * 3):
            return
        import random as _random
        batch = list(self._wall_unsure)
        _random.shuffle(batch)
        self.wall_prober = InfoScan(batch[:60], self)
        # Le mur est ce qu'on regarde : il n'attend pas que les vignettes de
        # la planche, qu'on vient de quitter, aient fini.
        self.wall_prober.yields = False
        self._own_thread(self.wall_prober, "wall_prober")
        self.wall_prober.done.connect(self._wall_probed)
        self.wall_prober.start()

    def _wall_probed(self, count: int) -> None:
        self.wall_prober = None
        if self.tab != TAB_SPLIT or self._wall_pinned:
            return
        before = len(self.wall.pool)
        pool, unsure = self.vertical_pool()
        if len(pool) > before:
            # Le vivier s'etoffe sans relancer le mur : la video qu'on
            # commencait a regarder restait sinon remplacee par une autre.
            self.wall.grow_pool(pool)
            self.wall.set_caption(len(pool), unsure)
        elif count:
            # Rien de nouveau, mais il reste a chercher.
            self.probe_for_wall()

    def _wall_unseen(self, on: bool) -> None:
        self.cfg["only_unseen"] = bool(on)
        self.cfg.save_soon()
        self.controls.set_unseen(bool(on))
        self.show_wall()

    def set_thumb_count(self, count: int) -> None:
        """Combien d'apercus a la fois dans un dossier : 2, 4, 6, 8 ou 10."""
        count = max(1, min(10, int(count)))
        if count == self.cfg["thumb_count"]:
            return
        self.cfg["thumb_count"] = count
        self.cfg.save_soon()
        self.plans = {}
        self.pages = {}
        self._mark_grid_count()
        if not self.browsing and self.current is not None:
            self.show_item(self.index)

    def _mark_grid_count(self) -> None:
        self.grid_chips.set_value(int(self.cfg["thumb_count"]))

    def _active_filters(self) -> list:
        """Ce qui masque des elements en ce moment, en clair."""
        active = []
        query = (self.criteria or {}).get("include", self.cfg["filter_include"])
        if query:
            active.append(f"recherche « {query} »"
                          + (" — à peu près" if self._loose else ""))
        if self.cfg["only_unseen"]:
            active.append("non vus seulement")
        chosen = self.controls.orientations()
        if len(chosen) == 1:
            active.append("verticales seulement" if chosen[0] == "vertical"
                          else "horizontales seulement")
        low, high = int(self.cfg["folder_min"] or 0), int(self.cfg["folder_max"] or 0)
        if low:
            active.append(f"dossiers d'au moins {low} vidéos")
        if high:
            active.append(f"dossiers d'au plus {high} vidéos")
        return active

    def reset_filters(self) -> None:
        """Tout retirer d'un geste : recherche, non vus, orientation, bornes."""
        self.controls.set_terms("", "")
        self.controls.set_orientations(("vertical", "horizontal"))
        self.controls.set_folder_bounds(0, 0)
        self.cfg["only_unseen"] = False
        self.controls.set_unseen(False)
        self.wall.set_unseen(False)
        self.on_controls_changed()
        self.show_banner("Tous les filtres sont retirés.", "quiet")

    def set_wall_count(self, count: int) -> None:
        self.cfg["wall_panes"] = count
        self.cfg.save_soon()
        self.wall.set_pane_count(count)
        self.wall.fill_empty()

    def set_wall_orientation(self, orientation: str) -> None:
        self._wall_pinned = []
        self.cfg["wall_orientation"] = orientation
        self.cfg.save_soon()
        self.wall.set_orientation(orientation)
        self.show_wall()

    def toggle_wall_fullscreen(self, on: bool | None = None) -> None:
        """Le mur seul, sur tout l'écran. Échap ramène tout le reste."""
        self.wall_full = (not self.wall_full) if on is None else bool(on)
        chrome = (self.top_bar, self.bottom_bar)
        if self.wall_full:
            self._wall_kept = (self.tree.isVisible(), self.aside.isVisible(),
                               self.windowState())
            for widget in chrome:
                widget.hide()
            self.tree.hide()
            self.aside.hide()
            self.wall.set_bare(True)
            self.showFullScreen()
            self.activateWindow()
        else:
            tree, aside, state = getattr(self, "_wall_kept", (False, False, None))
            self.wall.set_bare(False)
            for widget in chrome:
                widget.show()
            self.tree.setVisible(tree)
            self.aside.setVisible(aside)
            if state is not None:
                self.setWindowState(state)
            else:
                self.showNormal()
            self._apply_selectors()
        self.setFocus()

    def vertical_pool(self) -> tuple:
        """(vivier, nombre d'orientation encore inconnue) pour le mur.

        Strict : on ne met dans le vivier que ce dont on **sait** qu'il est
        de l'orientation demandee. Laisser entrer l'inconnu remplissait le
        mur d'horizontales quand on avait demande des verticales — et c'est
        precisement ce qu'on voulait eviter. Ce qui n'a pas encore ete sonde
        est compte a part, et `probe_for_wall` va le chercher en tache de
        fond : le mur se remplit alors tout seul, sans jamais mentir.
        """
        mark("vertical_pool")
        terms = self._terms((self.criteria or {}).get(
            "include", self.cfg["filter_include"]))
        wanted = self.cfg["wall_orientation"] or "vertical"
        only_unseen = bool(self.cfg["only_unseen"])
        top = self.top_root()
        videos = list(self._videos_from_items())
        if top is not None:
            try:
                for item in cached_items(top, MODE_FOLDERS,
                                         self.cfg["expand_parents"]):
                    videos.extend(item.videos)
            except Exception:
                pass
        found = []
        unsure = []
        seen = set()
        for video in videos:
            key = str(video)
            if key in seen:
                continue
            seen.add(key)
            if terms and not any(term in key.lower() for term in terms):
                continue
            if only_unseen and INDEX.is_seen(key):
                continue
            if wanted == "any":
                found.append(key)
                continue
            info = INDEX.probe(video) or {}
            width, height = info.get("width") or 0, info.get("height") or 0
            if not (width and height):
                unsure.append(key)
                continue
            kind = "vertical" if height > width else "horizontal"
            if kind == wanted:
                found.append(key)
        self._wall_unsure = unsure
        return found, len(unsure)

    def wall_sibling(self, index: int, path: str) -> None:
        """La suivante du meme dossier, dans ce panneau. Une lecture du dossier,
        puis plus aucune : la liste est gardee pour la session."""
        listing = self._folder_videos(path)
        if len(listing) < 2:
            self.show_banner("C'est la seule vidéo de son dossier.", "quiet")
            return
        try:
            at = listing.index(path)
        except ValueError:
            at = -1
        mark("wall.play sibling")
        self.wall.play_in(index, listing[(at + 1) % len(listing)])

    def show_wall(self) -> None:
        """Remplit le mur avec ce que l'on connaît de vertical."""
        mark(f"show_wall {len(self.wall.panes)} panneaux")
        # Le mur lit plusieurs videos a la fois : la recolte de vignettes et la
        # preparation, qui occupent jusqu'a douze lectures du partage, lui
        # laissent la ligne. La preparation reprend quand on quitte le mur.
        self.preview.stop_harvest()
        # Les images de la planche quittee aussi : demandees en urgence, elles
        # tenaient la porte fermee aux lectures du mur.
        self.preview.cancel_prefix("board@")
        if self.backfill is not None:
            self.backfill.pause()
        self.viewer.setCurrentWidget(self.wall)
        if self._wall_pinned:
            tall = wide = 0
            for video in self._wall_pinned:
                info = INDEX.probe(video) or {}
                width, height = info.get("width") or 0, info.get("height") or 0
                if width and height:
                    tall += height > width
                    wide += width >= height
            self.wall.set_shape("vertical" if tall > wide else
                                "horizontal" if wide else None)
            self.wall.set_pool(self._wall_pinned)
            self.wall.set_caption(len(self.wall.pool), pinned=True)
        else:
            self.wall.single_row = False
            self.wall.set_shape(None)
            pool, unknown = self.vertical_pool()
            heavy = 0
            if len(self.wall.panes) >= 4:
                # A quatre panneaux et plus, chacun fait 300 pixels de large :
                # une source 4K n'y apporte rien, et six decodages 4K a la fois
                # mettent n'importe quel processeur a genoux — c'etait le gel
                # de cinq minutes. On ecarte ce qu'on sait etre au-dela de
                # 1080p ; l'inconnu reste.
                light = []
                for video in pool:
                    info = INDEX.probe(video) or {}
                    if max(info.get("width") or 0, info.get("height") or 0) > 1920:
                        heavy += 1
                    else:
                        light.append(video)
                if light:
                    pool = light
            # Dans le journal des gels : combien de panneaux, et combien de
            # sources lourdes ecartees — ce qui pese sur le decodage.
            mark(f"show_wall {len(self.wall.panes)} panneaux, vivier "
                 f"{len(pool)}, {heavy} au-delà de 1080p écartée(s)")
            self.wall.set_pool(pool)
            self.wall.set_caption(len(pool), unknown, heavy=heavy)
        self.wall.set_unseen(bool(self.cfg["only_unseen"]))
        self.wall.orient_button.setToolTip(
            (self.wall.caption.text() or "Le mur") + " — cliquer pour changer")
        self.item_title.setText(self.wall.caption.text()
                                or f"{len(self.wall.pool)} vidéo(s) pour le mur")
        self.item_subtitle.setText("")
        # L'entete se regle sur ce qu'on voit : le mur est maintenant a l'ecran.
        self._apply_selectors()
        self.probe_for_wall()

    def open_video_path(self, path: str) -> None:
        """Ouvre dans la fiche une vidéo désignée par son chemin.

        Depuis le mur, on quitte le mur : la fiche d'une video est la meme
        partout — son dossier, ses voisines, ses etoiles, ses destinations.
        L'ouvrir sans changer d'onglet donnait une fiche amputee, ni notes ni
        commandes, qui n'existait que la.
        """
        if self.tab == TAB_SPLIT:
            if self.wall_full:
                self.toggle_wall_fullscreen(False)
            self.set_tab(TAB_FOLDERS)
            self.play_in_app(path)
            return
        for position, item in enumerate(self.items):
            if str(item.path) == path:
                self.on_board_open(position)
                return
        self.open_external(path)

    def show_favorites(self) -> None:
        """Les dossiers et les videos en favori, sans rien relire du disque.

        Un dossier vient de la liste deja analysee ; une video, de son seul
        chemin. On y ouvre un dossier ou une video comme partout ailleurs.
        """
        mark("show_favorites")
        # Ce qui dort dans une corbeille n'est plus dans la collection : son
        # etoile y etait partie avec lui, et la carte menait a la corbeille.
        trash_mark = os.sep + TRASH_FOLDER_NAME + os.sep
        local_trash = os.path.normcase(str(actions.LOCAL_TRASH)).rstrip("\\/")
        loved = {key for key, value in self.ratings.data.items()
                 if value and trash_mark not in key
                 and not os.path.normcase(key).startswith(local_trash + os.sep)}
        collection = self._collection()
        folders = [item for item in collection if str(item.path) in loved]
        known = {str(item.path) for item in folders}
        # Un dossier en favori plus bas dans l'arborescence : l'index le
        # connait ; a defaut, ses videos sont deja dans celles de son dossier
        # de tete. Rien n'est lu sur le disque.
        deeper = sorted((key for key in loved - known
                         if Path(key).suffix.lower() not in VIDEO_EXTS
                         and not under_veiled(key)), key=str.lower)
        if deeper:
            indexed = INDEX.folders(deeper)
            for key in deeper:
                item = indexed.get(key)
                if item is None:
                    prefix = key.rstrip("\\/") + os.sep
                    inside = [video for top_item in collection
                              if prefix.startswith(str(top_item.path) + os.sep)
                              for video in top_item.videos
                              if str(video).startswith(prefix)]
                    if not inside:
                        continue
                    item = Item(path=Path(key), kind=MODE_FOLDERS,
                                videos=inside, video_count=len(inside),
                                file_count=len(inside))
                folders.append(item)
                known.add(key)
        videos = []
        wanted = sorted((key for key in loved - known
                         if Path(key).suffix.lower() in VIDEO_EXTS
                         and not under_veiled(key)), key=str.lower)
        top = self.top_root()
        if (wanted and self._plain_whole and self._plain_root is not None
                and top is not None and Path(self._plain_root) == Path(top)):
            # Une video rangee ou supprimee hors de Prisme garde son etoile :
            # la collection, entierement lue, dit si elle est encore la.
            present = {str(video) for item in collection for video in item.videos}
            wanted = [key for key in wanted if key in present]
        for key in wanted:
            item = self._flat_cache.get(key) or Item(
                path=Path(key), kind=MODE_FILES, videos=[Path(key)],
                video_count=1, file_count=1)
            videos.append(item)
        self.levels = []
        self.browsing = True
        self.all_items = folders + videos
        self.items = self._filtered()
        self.index = 0
        top = self.top_root()
        if top is not None:
            self.crumbs.set_path(top, top)
            self.crumbs.append_leaf("★ Favoris")
        self._apply_selectors()
        self.board.empty.setText(
            "Aucun favori pour l'instant : l'étoile, en bas à droite d'une "
            "fiche, ou la touche 1, en ajoute un.")
        self.refresh_board()
        if top is not None:
            self.crumbs.set_path(top, top)
            self.crumbs.append_leaf("★ Favoris")
        self._show_counts()
        QTimer.singleShot(150, self.start_harvest)

    def show_videos_tab(self) -> None:
        """Toutes les vidéos de la racine, sans relire le disque.

        Les parcourir pour de bon signifiait traverser toute l'arborescence du
        partage avant d'afficher quoi que ce soit — plusieurs minutes, pendant
        lesquelles l'onglet annonçait « rien à afficher ». Or l'index porte déjà
        la liste des vidéos de chaque dossier : leur réunion est la même réponse,
        obtenue en mémoire.
        """
        videos = self._videos_from_items()
        if not videos:
            known = cached_items(self.root, MODE_FOLDERS,
                                 self.cfg["expand_parents"])
            videos = [video for item in known for video in item.videos]
        if not videos and self._scan_top and self.scanning:
            # La collection est en cours de lecture : l'interrompre pour en
            # lancer une autre laissait ensuite « Dossiers » vide. On attend
            # sa fin, qui remplit cet onglet (`on_scan_finished`).
            self.all_items = []
            self.items = []
            self.mode = MODE_FLAT
            self.board.empty.setText(
                "Lecture de la collection en cours : les vidéos s'afficheront "
                "dès qu'elle sera finie.")
            return
        if not videos:
            self.start_root(self.root, MODE_FLAT, reset_levels=False)
            return
        # Les memes objets d'une visite a l'autre : en refaire cent mille a
        # chaque onglet coutait une demi-seconde, et perdait au passage ce
        # qu'on avait decide d'eux.
        mark(f"show_videos_tab {len(videos)}")
        cache = self._flat_cache
        fresh = {}
        with _without_gc():
            for video in videos:
                key = str(video)
                item = cache.get(key)
                if item is None:
                    item = Item(path=_as_path(video), kind=MODE_FILES,
                                videos=[video], video_count=1, file_count=1)
                fresh[key] = item
        self._flat_cache = fresh
        self.all_items = list(fresh.values())
        self.items = self._filtered()
        self.mode = MODE_FLAT
        self.apply_sort()
        self.index = 0
        self._show_counts()
        # Sans cela, l'onglet Videos ne beneficiait d'aucune preparation : il ne
        # passe par aucune analyse, et c'est elle seule qui lancait la recolte.
        # Un instant apres l'affichage, pas avant : l'onglet doit d'abord
        # apparaitre, la recolte parcourt cent mille elements.
        QTimer.singleShot(150, self.start_harvest)

    def set_tab(self, tab: str, reposition: bool = True) -> None:
        """Change de point de vue sans changer de collection.

        Les trois onglets ne sont pas trois applications : ouvrir une vidéo,
        d'où qu'elle vienne, mène toujours à la même fiche, avec ses
        destinations et sa note. Seule la façon de présenter l'ensemble change.
        """
        if tab not in TABS:
            return
        mark(f"set_tab {tab}")
        # Recliquer l'onglet ou l'on est ramene chez soi : a la racine, sur les
        # vignettes. C'est le geste qu'on fait quand on s'est perdu en
        # descendant, et il ne faisait rien.
        if tab == self.tab:
            # Recliquer l'onglet ou l'on est ramene chez soi ; s'y trouver
            # deja ne coute rien.
            return self.go_home()
        was = self.tab
        self.tab = tab
        self.content = (CONTENT_VIDEOS if tab == TAB_VIDEOS
                        else CONTENT_FOLDERS)
        # Changer de collection ramene aux vignettes.
        self.browsing = True
        self.cfg["tab"] = tab
        self.cfg["content"] = self.content
        self.cfg["view"] = self.view
        self.cfg.save_soon()
        # On ne deplace aucun fichier ici : inutile de relacher les verrous,
        # et surtout d'attendre les ffmpeg en cours. `_release_media` bloquait
        # le fil de l'interface jusqu'a 1,2 s a chaque onglet — c'etait la
        # lenteur qu'on sentait sous le doigt.
        self._hush_players()
        # Une video ouverte a cote appartient a la liste qu'on quitte : la
        # laisser jouer sur un autre onglet n'avait aucun sens, et son bouton
        # de fermeture se trouvait hors de l'ecran sur un affichage agrandi.
        self.close_aside()
        if tab != TAB_SPLIT:
            if self.wall_full:
                self.toggle_wall_fullscreen(False)
            self.wall.stop()
            self._wall_pinned = []
            if was == TAB_SPLIT:
                # Recolte et preparation reprennent des qu'on quitte le mur.
                if self.backfill is not None:
                    self.backfill.resume()
                if self.items:
                    self.start_harvest()
        self._apply_selectors()

        if self.root is None:
            return

        # L arborescence prend le geste du contexte : en edition on range, et
        # c est donc « envoyer vers » ; en videos on se promene, et c est
        # « aller dans ». Se tromper de geste deplace des fichiers.
        self.tree.set_action("go" if tab == TAB_VIDEOS else "send")
        self.cfg["tree_action"] = self.tree.action

        top = self.top_root()
        if tab == TAB_SPLIT:
            # Le fil ne porte plus la fiche qu'on vient de quitter.
            self.crumbs.set_path(top, self.root)
            # Le mur ne change ni de dossier ni de mode : il regarde autrement
            # ce que l'on a deja sous la main.
            self.show_wall()
            self.setFocus()
            return
        if tab == TAB_VIDEOS:
            # Sans quoi les memes vidéos reviennent toujours en tete.
            self.sort_mode = "random"
            self.cfg["sort_mode"] = "random"
            self.controls.set_sort("random")
        # Un onglet est un point de vue sur **toute** la collection, pas sur
        # la liste qu'on quitte : depuis les favoris ou un sous-dossier,
        # « Vidéos » ne montrait que ceux-la, et « Dossiers » restait sur les
        # favoris. Chaque onglet repart donc de la collection.
        if not self._show_tab_list():
            self.start_root(top, MODE_FOLDERS, reset_levels=True)
            return
        if tab == TAB_VIDEOS:
            self.resume_last()

    def _show_tab_list(self, restore_id: str = "") -> bool:
        """La liste de l'onglet courant, tiree de la collection, sans relire.

        Seul chemin vers la liste d'un onglet : en changer, le recliquer ou
        remonter du dernier niveau y menent tous. Faux quand la collection
        n'est pas encore connue et qu'il faut donc lire la racine.
        """
        top = self.top_root()
        if top is None:
            return False
        known = (self._plain_root is not None
                 and Path(self._plain_root) == Path(top))
        if not known and self.tab != TAB_VIDEOS:
            return False
        if self.scanning and not self._scan_top:
            # Une relecture de sous-dossier n'a plus rien a dire. Celle de la
            # racine, elle, continue : elle tient la collection a jour.
            self.stop_scan()
            self.progress.hide()
        self.levels = []
        self.root = top
        self.browsing = True
        self.stack.setCurrentIndex(PAGE_SORT)
        self._hush_players()
        self._list_leaf = ""
        self.crumbs.set_path(top, top)
        # Le message d'une liste vide appartient a l'onglet qui l'a pose.
        self.board.empty.setText("Rien à afficher ici.")
        if self.tab == TAB_FAVS:
            self.mode = MODE_FOLDERS
            self.show_favorites()
        else:
            if self.tab == TAB_VIDEOS:
                self.show_videos_tab()
            else:
                self.restore_folders()
                if self.tab == TAB_TAGS:
                    self._add_tag_items()
            self._apply_selectors()
            self.refresh_board()
            self._show_counts()
        if restore_id:
            for position, item in enumerate(self.items):
                if item.item_id == restore_id:
                    self.index = position
                    self.board.scroll_to(position)
                    break
        self.setFocus()
        return True

    def _on_top_tags(self, found: list) -> None:
        sender = self.sender()
        if sender is not None and sender is not self._tags_thread:
            # Un calcul remplace entre-temps (autre racine, autre collection) :
            # son resultat se rangeait sous la cle du nouveau.
            return
        self.board.empty.setText("Rien à afficher ici.")
        self._top_tags = (self._tags_key, found)
        self._tags_thread = None
        if self.tab == TAB_TAGS and self.tag_family != "mine":
            self._add_tag_items()

    def scan_signatures(self) -> None:
        """Remplit la base d'empreintes — seulement ce qui n'y est pas encore.

        Quatre images par video, retenues avec sa taille et sa date. Le
        premier passage coute ; ensuite, ajouter mille videos ne demande que
        de sonder ces mille-la, et la recherche de doublons devient immediate.
        """
        if self.sig_scan is not None:
            self.sig_scan.stop()
            self.show_banner("Empreintes : arrêt demandé…", "quiet")
            return
        if self.root is None:
            return
        top = self.top_root()
        self.sig_scan = SignatureScan(top, self.cfg["thumb_width"],
                                      self.cfg["skip_hidden"], self)
        self._own_thread(self.sig_scan, "sig_scan")
        self._sigs_unreadable = 0
        self.sig_scan.progress.connect(
            lambda done, total: self._refresh_state(
                f"empreintes {self._thousands(done)} / {self._thousands(total)}"))
        # Le recensement dure : il dit ce qu'il trouve en chemin, au lieu de
        # laisser croire que rien ne se passe.
        self.sig_scan.walking.connect(
            lambda found: self._refresh_state(
                f"empreintes : recensement {self._thousands(found)} vidéos"))
        self.sig_scan.unreadable.connect(self._sigs_unreadable_count)
        self.sig_scan.done.connect(self._told_sigs)
        self.sig_scan.start()
        self.show_banner(
            f"Empreintes sous {top} : seules les vidéos nouvelles ou modifiées "
            "sont sondées. Recliquer arrête.", "info")

    def _sigs_unreadable_count(self, count: int) -> None:
        self._sigs_unreadable = int(count)

    def _told_sigs(self, seen: int, total: int) -> None:
        self.sig_scan = None
        self._refresh_state()
        unreadable = getattr(self, "_sigs_unreadable", 0)
        self.show_banner(
            f"Empreintes : {seen} vidéo(s) sondée(s) cette fois, "
            f"{total} dans la base."
            + (f" {unreadable} illisible(s), retentée(s) au prochain passage."
               if unreadable else "")
            + " « Doublons d'après les empreintes » peut maintenant les "
            "comparer.", "done" if not unreadable else "info")

    def duplicates_from_sigs(self) -> None:
        """Compare les empreintes deja en base. Aucun acces au disque.

        Dans un fil : le calcul prenait de trente secondes a plusieurs minutes
        sur le fil de l'interface, qui ne repondait plus. La lecture de la
        base s'y fait aussi, avec le tri par racine — « X:\\Films2 » n'est plus
        pris pour un morceau de « X:\\Films ». Recliquer arrete.
        """
        if self.dupes is not None:
            self._stop_dupes()
            return
        if self.root is None:
            return
        top = self.top_root()
        self._dupes_by_image = True
        self._dupes_from_sigs = True
        self.dupes = SignatureGroupScan(top, self)
        self._start_dupes(f"Comparaison des empreintes sous {top}…")

    def scan_scenes(self) -> None:
        """Releve les changements de plan sous la racine, en fond.

        Les vignettes cessent alors d'etre prises a des fractions rondes : on
        prend les plans eux-memes, et une video qui commence par du noir ne
        montre plus du noir.
        """
        if self.scene_scan is not None:
            self.scene_scan.stop()
            self.show_banner("Repérage des plans : arrêt demandé…", "quiet")
            return
        if self.root is None:
            return
        top = self.top_root()
        self.scene_scan = SceneScan(top, self.cfg["skip_hidden"], self)
        self._own_thread(self.scene_scan, "scene_scan")
        self.scene_scan.progress.connect(
            lambda done, total: self._refresh_state(
                f"plans {self._thousands(done)} / {self._thousands(total)}"))
        self.scene_scan.done.connect(self._told_scenes)
        self.scene_scan.start()
        self.show_banner(
            f"Repérage des plans sous {top}. Chaque vidéo est traversée une "
            "fois, images clés seulement ; rien n'est redemandé ensuite. "
            "Recliquer arrête.", "info")

    def _told_scenes(self, seen: int, found: int) -> None:
        self.scene_scan = None
        self.plans = {}
        self._refresh_state()
        self.show_banner(
            f"Plans : {seen} vidéo(s) parcourue(s), {found} avec des "
            "changements de plan. Les vignettes s'y posent désormais.", "done")
        if not self.browsing and self.current is not None:
            self.show_item(self.index)
        elif self.browsing:
            self.refresh_board()

    def scan_titles(self) -> None:
        """Lit le titre des metadonnees de toute la collection, en fond."""
        if self.titles_scan is not None:
            self.titles_scan.stop()
            self.show_banner("Analyse des titres : arrêt demandé…", "quiet")
            return
        if self.root is None:
            return
        top = self.top_root()
        self.titles_scan = TitleScan(top, self.cfg["skip_hidden"], self)
        self._own_thread(self.titles_scan, "titles_scan")
        self.titles_scan.progress.connect(
            lambda done, total: self._refresh_state(
                f"titres {self._thousands(done)} / {self._thousands(total)}"))
        self.titles_scan.done.connect(self._told_titles)
        self.titles_scan.start()
        self.show_banner(
            f"Lecture des titres sous {top} — des heures s'il le faut, rien "
            "n'est redemandé à la relance. Recliquer arrête.", "info")

    def _told_titles(self, seen: int, found: int) -> None:
        self.titles_scan = None
        self._top_tags = None
        self._refresh_state()
        self.show_banner(
            f"Titres : {seen} vidéo(s) sondée(s), {found} avec un titre dans "
            "leurs métadonnées. Les mots fréquents en tiennent compte.", "done")
        if self.tab == TAB_TAGS and self.tag_family != "mine":
            self._add_tag_items()

    def at_home(self) -> bool:
        """Vrai quand on est sur la liste d'un onglet, a sa racine."""
        if not self.browsing or self.levels:
            return False
        if (self.tab == TAB_FOLDERS and self._plain_root is not None
                and self.all_items is not self._plain_items):
            # Une liste de passage (doublons…) : recliquer rend les dossiers.
            return False
        top = self.top_root()
        return self.root is None or top is None or Path(self.root) == Path(top)

    def go_home(self) -> None:
        """Remonte a la liste de l'onglet courant, d'ou qu'on vienne.

        C'est la sortie de secours : recliquer le bouton sur lequel on se
        trouve deja doit toujours ramener ici. Sans elle, on descendait dans
        un mot-cle et plus rien ne repondait — il fallait deviner qu'une
        fleche, ailleurs, faisait remonter.
        """
        if self.at_home() or self.tab == TAB_SPLIT:
            # Le mur n'a pas de « chez soi » : le recliquer le laisse tel quel.
            return
        mark("go_home")
        self.close_aside()
        if self.root is not None and not self._show_tab_list():
            self.start_root(self.top_root(), MODE_FOLDERS, reset_levels=True)

    def set_tag_family(self, family: str) -> None:
        """Mes propres mots-clés, ou ceux que les noms de fichiers répètent."""
        if family == self.tag_family:
            # Recliquer la famille ou l'on est : on remonte a sa liste. C'est
            # le geste qu'on fait quand on s'est perdu dans un mot-cle.
            return self.go_home()
        self.tag_family = family
        self.cfg["tag_family"] = family
        self.cfg.save_soon()
        self.tag_chips.set_value(family)
        self.levels = []
        self.browsing = True
        self._add_tag_items()
        self._apply_selectors()

    def _first_to_sort(self) -> int:
        """Le premier élément qui n'est pas déjà rangé sous un dossier de tête.

        L'édition sert à trancher : commencer par ce qui est déjà classé ferait
        perdre le temps qu'elle est censée gagner.
        """
        for position, item in enumerate(self.items):
            if not item.is_tag and not item.categorized:
                return position
        return min(self.index, len(self.items) - 1)

    def set_view(self, view: str, reposition: bool = True) -> None:
        """Parcourir les vignettes, ou editer l'element ou l'on se trouve."""
        if view == self.view:
            return
        if view == VIEW_EDIT:
            self.on_board_open(self.index)
        else:
            self.show_board_at(self.index)

    def set_content(self, content: str) -> None:
        """Regarder les dossiers, ou les vidéos qu'ils contiennent."""
        if content == self.content or self.root is None:
            return
        self.set_tab(TAB_VIDEOS if content == CONTENT_VIDEOS else TAB_FOLDERS)

    def mode_for_content(self) -> str:
        return MODE_FOLDERS if self.content == CONTENT_FOLDERS else MODE_FLAT

    def _apply_selectors(self) -> None:
        """Aligne l'entête sur l'état réel, pour qu'il dise où l'on se trouve."""
        self.tabs.set_value(self.tab)
        self.tag_chips.set_value(self.tag_family)
        # Le mur, c'est ce qu'on voit, pas seulement l'onglet : si une fiche
        # est a l'ecran, l'entete est celui d'une fiche.
        wall = (self.tab == TAB_SPLIT
                and self.viewer.currentWidget() is self.wall)
        # Une fiche est ce qu'on regarde quand on ne parcourt pas — hors du
        # mur, qui n'en a pas.
        sheet = not self.browsing and not wall
        self.tag_chips.setVisible(self.tab == TAB_TAGS and not sheet)
        self.tags_button.setVisible(self.tab == TAB_TAGS and not sheet)
        # L'etat de la collection vit dans le menu ⋯ : sur la premiere ligne,
        # il se faisait rogner jusqu'a chevaucher les boutons voisins.
        self.state_button.hide()
        self.controls.set_browsing(self.browsing)
        # Le jeu de filtres suit ce que montre la liste, pas l'onglet : dans un
        # dossier de videos ouvert depuis « Dossiers », les bornes « ≥ / ≤
        # videos » ne s'appliquaient a rien, et la case « non vus » manquait.
        if self.tab == TAB_SPLIT:
            mode = "wall"
        elif self.tab == TAB_TAGS:
            mode = "tags"
        elif self.tab == TAB_FOLDERS and self.mode == MODE_FOLDERS:
            mode = "folders"
        else:
            mode = "videos"
        self.controls.set_mode(mode)
        # La seconde ligne : les filtres sur une planche, le titre sur une
        # fiche. Jamais les deux — c'etait trois lignes avant l'image.
        self.controls.setVisible(not sheet and not wall and not (
            self.board.picked_ids and self.browsing))
        # La fiche d'une video tient sur une seule ligne : onglets, chemin et
        # nom, puis ce qu'on en sait et ses deux gestes. Toute la hauteur
        # restante va a l'image.
        self._title_line(sheet and self.current is not None
                         and self.current.kind != MODE_FOLDERS)
        self.item_card.setVisible(sheet)
        self.picked_bar.setVisible(not sheet and bool(self.board.picked_ids)
                                   and not wall)
        self.wall.controls.setVisible(wall and not self.wall_full)
        item = self.current
        folder = (item is not None and item.kind == MODE_FOLDERS
                  and not item.locked)
        self.grid_chips.setVisible(sheet and folder)
        self.cinema_button.setVisible(
            sheet and item is not None and item.kind != MODE_FOLDERS)
        self.cinema_button.setChecked(self.cinema)
        self.reveal_button.setVisible(sheet and item is not None
                                      and not item.is_tag and not item.locked)
        # Le hasard local ne vaut que devant un dossier : devant une seule
        # video, il la relancait, ce qui n'a aucun sens.
        self.random_here_button.setVisible(sheet and folder
                                           and bool(item.videos))
        if not self._one_line:
            self.reveal_button.setText(
                "Ouvrir le dossier" if folder or (item is not None and item.is_tag)
                else "Emplacement")
        # Le hasard « dans ce qu'on regarde » n'a de sens que si l'on regarde
        # une partie : des resultats filtres, ou un dossier ou l'on est entre.
        self._refresh_random_here()
        # Un dossier s'ouvre d'un clic sur son titre : c'est la que l'on
        # vient de decider qu'il fallait y descendre.
        # Une seule ligne sous l'image, et seulement sur une fiche.
        self.bottom_bar.setVisible(sheet and not self.cinema)
        for button in (self.prev_button, self.next_button):
            button.setVisible(bool(self.items))
        # Les dossiers se notent comme les videos ; un mot-cle, non.
        self.stars.setVisible(sheet and item is not None and not item.is_tag)
        root = self.root
        self.up_button.setEnabled(
            sheet or bool(self.levels)
            or (root is not None and Path(root).parent != Path(root)))
        # Le titre de l'arborescence annonce ce que fera un clic : en planche,
        # y aller (ou y envoyer les coches) ; sur une fiche, le geste choisi.
        self.tree.set_context(self.browsing and self.tab != TAB_SPLIT,
                              len(self.board.picked_ids))

    def _title_line(self, first: bool) -> None:
        """Pose la ligne d'informations sur la premiere ligne, ou la rend a
        la seconde. On ne deplace rien si elle est deja a sa place."""
        if first == self._one_line:
            return
        self._one_line = first
        one, two, card = self._row_one, self._row_two, self.item_card
        # Sur la ligne unique, les gestes de la fiche ne gardent que leur
        # icone : le nom de la video passe avant leur libelle.
        for button, label in ((self.reveal_button, "Emplacement"),
                              (self.cinema_button, "Cinéma")):
            button.setText("" if first else label)
        if first:
            two.removeWidget(card)
            at = one.indexOf(self.crumbs)
            one.setStretchFactor(self.crumbs, 0)
            self.crumbs.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            one.insertWidget(at + 1, card, 0)
            one.insertStretch(at + 2, 1)
            # Sur la ligne unique, ce qu'on sait de la video (definition,
            # poids, date) reste entier : c'est le fil d'Ariane, qui s'abrege
            # de lui-meme, qui cede la place. Rogne, il ne disait plus rien.
            self.item_subtitle.setMinimumWidth(0)
            self.item_subtitle.setSizePolicy(QSizePolicy.Minimum,
                                             QSizePolicy.Preferred)
        else:
            at = one.indexOf(card)
            one.removeWidget(card)
            one.takeAt(at)                   # le ressort pose a sa suite
            self.crumbs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            one.setStretchFactor(self.crumbs, 1)
            self.item_subtitle.setMinimumWidth(60)
            self.item_subtitle.setSizePolicy(QSizePolicy.Ignored,
                                             QSizePolicy.Preferred)
            two.addWidget(card, 1)

    def _origin_for(self, current):
        """Le plus haut dossier dont `current` descend : la racine du fil."""
        if current is None:
            return None
        candidates = [Path(level["root"]) for level in self.levels]
        if self.origin is not None:
            candidates.append(self.origin)
        for candidate in candidates:
            try:
                Path(current).relative_to(candidate)
            except ValueError:
                continue
            return candidate
        return current

    def jump_to(self, path: str) -> None:
        """Le fil d'Ariane ramène directement au dossier cliqué.

        Il dépilait les niveaux traversés jusqu'à retrouver la cible — et ne
        trouvait rien quand la pile avait été vidée entre-temps, par exemple en
        changeant d'onglet. Il fallait alors cliquer plusieurs fois, ou bien le
        clic ne faisait rien du tout. On ne se sert donc plus de la pile pour
        savoir où aller : le chemin cliqué suffit, et la pile est simplement
        ramenée à ce qui reste au-dessus de lui.
        """
        target = Path(path)
        item = self.current
        if (not self.browsing and item is not None and item.kind == MODE_FOLDERS
                and not item.is_tag and Path(item.path) == target):
            return self.enter_current()
        top = self.top_root()
        if (top is not None and target == Path(top) and self.levels
                and self.tab != TAB_SPLIT):
            # La racine du fil, depuis un dossier ou un mot-cle ouvert : la
            # liste de l'onglet (favoris, mots-cles…), et non les dossiers
            # relus du disque.
            return self.go_home()
        if self.root is not None and target == self.root:
            # Depuis une fiche, cliquer le dossier ou l'on est rend sa liste.
            if not self.browsing:
                self.show_board_at(self.index)
            return
        if not target.is_dir():
            self.show_banner(f"Introuvable : {target}", "error")
            return
        kept = []
        for level in self.levels:
            above = Path(level["root"])
            if above == target:
                break
            try:
                target.relative_to(above)
            except ValueError:
                continue
            kept.append(level)
        self.levels = kept
        self.start_root(target, self.mode_for_content(), reset_levels=False)

    def toggle_board(self, visible: bool | None = None,
                     reposition: bool = True) -> None:
        target = (not self.browsing) if visible is None else visible
        self.set_view(VIEW_BROWSE if target else VIEW_EDIT, reposition)

    def refresh_board(self) -> None:
        if not self.browsing:
            return
        mark(f"refresh_board {len(self.items)}")
        self.viewer.setCurrentWidget(self.board)
        self.board.set_muted(self.cfg["muted"])
        self.board.set_items(self.items, self.ratings.get)
        self.item_title.setText(
            f"{len(self.items)} élément(s)" if self.items else "Rien à afficher"
        )
        self.item_parent.setText(str(self.root) if self.root else "")
        self.item_title.setToolTip(str(self.root) if self.root else "")
        if self.root is not None:
            top = self.top_root()
            self.crumbs.set_path(top, self.root)
            if self._list_leaf:
                self.crumbs.append_leaf(self._list_leaf)
        self.item_subtitle.setText("")
        self.stars.hide()
        # En planche, la barre de pages compte les cartes et non les apercus.

    def on_board_preview(self, position: int) -> None:
        """Une carte réclame son image : on lui construit sa première vignette."""
        if not (0 <= position < len(self.items)):
            return
        item = self.items[position]
        if not item.videos or item.locked:
            return
        key = f"board@{item.item_id}"
        plan = self.plans.get(key)
        if plan is None:
            self.preview.request_plan(
                key, item.videos, 1, page=0,
                one_per_video=item.kind == MODE_FOLDERS, blind=True,
            )
            return
        self.board.set_source(position, plan[0])
        self.preview.request_thumb(key, 0, plan[0][0], plan[0][1],
                                   keyframe=True)

    def _board_position_of(self, key: str) -> int:
        """La carte de la page affichee qui attend cette reponse, ou -1.

        Sur la seule page : une reponse pour une page quittee n'a plus de
        carte ou se poser, et la chercher parmi cent mille elements, a chaque
        image recue, coutait plus que l'image elle-meme.
        """
        item_id = key[len("board@"):]
        items = self.board.items
        first, last = self.board._page_bounds()
        for position in range(max(0, first), min(last, len(items))):
            if items[position].item_id == item_id:
                return position
        return -1

    # ------------------------------------------------------------------
    # Le lecteur de cote
    # ------------------------------------------------------------------
    def open_aside(self, position: int) -> None:
        """Ouvre a droite la video de cette carte, sans quitter la planche."""
        if not (0 <= position < len(self.items)):
            return
        item = self.items[position]
        if not item.videos:
            return
        self.aside_index = position
        self.aside_playlist = []
        self._aside_play(str(item.videos[0]), item.name, str(item.path))

    def _aside_play(self, video: str, title: str, tip: str = "") -> None:
        """Le lecteur de droite, sur cette video."""
        mark("open_aside")
        self.aside_current = video
        self._restore_split()
        self.aside_title.setText(title)
        self.aside_title.setToolTip(tip or video)
        self.aside.show()
        self.aside_player.set_muted(self.cfg["muted"])
        self.aside_player.set_loop(False)
        self.aside_player.set_item(video)
        # Aucun apercu n'est demande : le lecteur de cote n'a pas de pellicule,
        # et fabriquer cinq images pour rien retardait celles de la planche.

    def _remember_split(self, *_args) -> None:
        if not self.aside.isHidden():
            self.cfg["aside_split"] = list(self.middle.sizes())
            self.cfg.save_soon()

    def _restore_split(self) -> None:
        """Le lecteur de droite reprend la largeur qu'on lui avait donnee."""
        wanted = self.cfg["aside_split"]
        if (self.aside.isHidden() and isinstance(wanted, list)
                and len(wanted) == 3 and sum(wanted) > 0):
            QTimer.singleShot(0, lambda: self.middle.setSizes(
                [self.tree.width() if self.tree.isVisible() else 0]
                + list(wanted[1:])))

    def _place_aside_bar(self) -> None:
        """Repose le bandeau sur l'image — il suit le lecteur, il ne le pousse pas."""
        self.aside_bar.place_on(self.aside_player.video_area)

    def _watch_aside(self) -> None:
        """Montre le bandeau tant que la souris est sur l'image, l'efface sinon."""
        area = self.aside_player.video_area
        if (self.aside.isHidden() or not self.aside_current
                or not area.isVisible() or not self.isActiveWindow()):
            self.aside_player.marks.hide()
            return self.aside_bar.hide()
        if self._pointer_on(area):
            self.aside_player.marks.hide()
            self._show_pause(self.aside_bar, self.aside_player)
            self._place_aside_bar()
            self.aside_bar.reveal()
        else:
            self.aside_bar.hide()
            self.aside_player.marks.place_on(area)

    @staticmethod
    def _show_pause(bar, player) -> None:
        """Le bouton du milieu dit ce qu'il fera : pause si ca joue."""
        button = bar.buttons.itemAt(1).widget()
        playing = (player.player.playbackState()
                   == player.player.PlaybackState.PlayingState)
        wanted = "pause" if playing else "play"
        if button.property("glyph") != wanted:
            button.setProperty("glyph", wanted)
            button.setIcon(icon(wanted))

    @staticmethod
    def _pointer_on(widget) -> bool:
        corner = widget.mapToGlobal(QPoint(0, 0))
        return QRect(corner.x(), corner.y(), widget.width(),
                     widget.height()).contains(QCursor.pos())

    def _watch_bars(self) -> None:
        """Le meme geste pour toutes les images qui jouent : un trait tres fin
        en permanence, le bandeau complet au survol."""
        self._watch_aside()
        area = self.single.video_area
        item = self.current
        playing = (self.stack.currentIndex() == PAGE_SORT
                   and not self.isMinimized() and area.isVisible()
                   and self.viewer.currentWidget() is self.single
                   and item is not None and item.kind != MODE_FOLDERS
                   and not self.single.peeking)
        if not playing:
            self.single_bar.hide()
            self.single.marks.hide()
            return
        if self.isActiveWindow() and self._pointer_on(area):
            self.single.marks.hide()
            self._show_pause(self.single_bar, self.single)
            self.single_bar.set_name(item.name)
            self.single_bar.place_on(area)
            self.single_bar.reveal()
        else:
            self.single_bar.hide()
            self.single.marks.place_on(area)
        if self.banner.isVisible():
            self._place_banner()

    def aside_fullscreen(self) -> None:
        """Donne tout l'ecran a la video ouverte a cote, et sait en revenir."""
        if self.aside_playlist:
            video = self.aside_playlist[self.aside_playlist_at]
            self.close_aside()
            self.play_in_app(video)
            self.toggle_cinema(True)
            return
        if self.aside_index < 0:
            return
        # On retient d'ou l'on vient : sortir du plein ecran laissait sur la
        # fiche, au lieu de rendre la planche qu'on etait en train de parcourir.
        self._back_to_board = self.browsing
        self.on_board_open(self.aside_index)
        self.close_aside()
        self.toggle_cinema(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.aside.isHidden():
            self._place_aside_bar()

    def aside_step(self, step: int) -> None:
        """Passe a la vignette voisine, dans l'ordre de la planche."""
        if self.cfg["stay_in_folder"] and self.aside_current and not self.aside_playlist:
            target = self._folder_neighbour(self.aside_current, step)
            if target:
                self._aside_play(target, Path(target).name)
            return
        if self.aside_playlist:
            # En playlist, on tourne : apres la derniere, la premiere.
            count = len(self.aside_playlist)
            self.aside_playlist_at = (self.aside_playlist_at + step) % count
            return self._aside_play_list()
        if self.aside_index < 0:
            return
        target = self.aside_index + step
        while 0 <= target < len(self.items) and not self.items[target].videos:
            target += step
        if 0 <= target < len(self.items):
            self.open_aside(target)

    def close_aside(self) -> None:
        self.aside_playlist = []
        self.aside_current = ""
        self.aside_player.stop()
        self.aside_bar.hide()
        self.aside.hide()
        self.aside_index = -1
        self.setFocus()

    def toggle_cinema(self, on: bool | None = None) -> None:
        """Ne laisse que l'image : tout le reste s'efface le temps de regarder."""
        self.cinema = (not self.cinema) if on is None else bool(on)
        self.cinema_button.setChecked(self.cinema)
        # L'image seule. On en sort par le bandeau qui parait au survol de
        # l'image (⛶), par Ctrl+J ou par Échap : rien d'autre ne reste.
        for widget in (self.top_bar, self.bottom_bar):
            widget.setVisible(not self.cinema)
        if not self.cinema and getattr(self, "_back_to_board", False):
            self._back_to_board = False
            self.show_board_at(self.index)
        if self.cinema:
            self.tree.hide()
        else:
            self._apply_selectors()
        self.setFocus()

    def _wake_chrome(self) -> None:
        """Le cinema n'a plus de chrome a rappeler : le bandeau de survol,
        pose sur l'image, en tient lieu."""

    def _sleep_chrome(self) -> None:
        pass

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self._wake_chrome()

    def eventFilter(self, watched, event):
        if (watched is getattr(self, "progress", None)
                and event.type() in (QEvent.Show, QEvent.Hide)
                and hasattr(self, "state_button")):
            self.state_button.hide()
        if (watched is getattr(self, "item_title", None)
                and event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton):
            item = self.current
            if (not self.browsing and item is not None
                    and item.kind == MODE_FOLDERS and not item.locked):
                self.enter_current()
                return True
        return super().eventFilter(watched, event)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.banner.isVisible():
            self._place_banner()

    def on_video_finished(self) -> None:
        """La vidéo est allée à son terme : on passe à la suivante.

        Elle repartait en boucle. Quand on trie, revoir indéfiniment ce qu'on
        vient de voir est exactement ce qu'on ne veut pas — la fin d'une vidéo
        est une décision prise, même quand on n'a rien décidé.
        """
        item = self.current
        if self.browsing or item is None or item.kind == MODE_FOLDERS:
            return
        if self.index + 1 < len(self.items):
            self.show_item(self.index + 1)
        else:
            self.show_banner("Dernière vidéo de la liste", "quiet")

    def on_picked_changed(self, count: int) -> None:
        # Tant que des elements sont coches, leurs actions prennent la place
        # des filtres : deux lignes, toujours. « Annuler » rend les filtres.
        self.picked_bar.setVisible(bool(count))
        if self.browsing and self.tab != TAB_SPLIT:
            self.controls.setVisible(not count)
        self.picked_label.setText(
            f"{count} élément(s) coché(s)" if count else "")
        # Le titre de l'arborescence dit ce que fera le prochain clic :
        # envoyer les coches, ou y aller.
        self.tree.set_context(self.browsing and self.tab != TAB_SPLIT, count)

    def pick_all(self) -> None:
        """Coche tout ce qui est affiche."""
        self.board.pick_all(True)
        self.setFocus()

    def pick_invert(self) -> None:
        """Coche ce qui ne l'est pas, et decoche le reste."""
        self.board.pick_all(None)
        self.setFocus()

    def reveal_target(self):
        """Le fichier que l'on veut retrouver dans l'explorateur, ou None.

        Sur le mur, c'est la video du panneau seul — ou du premier qui joue —
        et non l'element de la liste qu'on a quittee. Une fonction a part :
        elle se verifie sans rien lancer.
        """
        if self.tab == TAB_SPLIT:
            panes = self.wall.panes
            at = self.wall.solo if self.wall.solo != -1 else 0
            ordered = ([panes[at]] if at < len(panes) else []) + panes
            for pane in ordered:
                if pane.video_path:
                    return Path(pane.video_path)
        item = self.current
        return None if item is None else Path(item.path)

    @staticmethod
    def reveal_command(target) -> str:
        """La commande qui ouvre l'explorateur **sur** le fichier.

        La virgule colle au chemin, et le tout ne fait qu'un seul argument :
        separes, l'explorateur ouvre le dossier parent et ne selectionne
        rien — ce que Ctrl+E faisait depuis toujours.
        """
        return f'explorer /select,"{target}"'

    def reveal_current(self) -> None:
        """Ouvre l'explorateur sur l'element courant, deja selectionne."""
        target = self.reveal_target()
        if target is None:
            return
        if not target.exists():
            return self.show_banner(f"Introuvable : {target.name}", "error")
        try:
            item = self.current
            if (target.is_dir() and item is not None and self.tab != TAB_SPLIT
                    and item.kind == MODE_FOLDERS):
                # Un dossier s'ouvre, on y entre : le selectionner dans son
                # parent obligeait a un double-clic de plus.
                subprocess.Popen(f'explorer "{target}"')
                return
            subprocess.Popen(self.reveal_command(target))
        except OSError as exc:
            self.show_banner(f"Explorateur indisponible : {exc}", "error")

    def clear_picked(self) -> None:
        self.board.clear_picked()
        self.setFocus()

    def _picked_videos(self) -> list:
        """Toutes les vidéos des éléments cochés, dossiers compris."""
        videos = []
        seen = set()
        for item in self.board.picked_items():
            for video in item.videos:
                key = str(video)
                if key not in seen:
                    seen.add(key)
                    videos.append(Path(video))
        return videos

    def wall_picked(self) -> None:
        """Les videos cochees, lues ensemble sur le mur — jusqu'a dix."""
        videos = [str(video) for video in self._picked_videos()]
        if not videos:
            return
        self._wall_pinned = videos
        self.board.clear_picked()
        # Le mur se regle avant de s'ouvrir : l'ouvrir puis le regler le
        # remplissait deux fois, chaque panneau chargeant deux videos.
        self.wall.set_pane_count(max(1, min(len(videos), 10)))
        # Trois ou quatre videos choisies : sur une seule ligne, comme on les
        # a choisies, et non empilees.
        self.wall.single_row = len(videos) <= 4
        self.set_tab(TAB_SPLIT)
        if self.viewer.currentWidget() is not self.wall:
            self.show_wall()

    def playlist_picked(self) -> None:
        """Les videos cochees, l'une apres l'autre dans le lecteur de droite,
        et on recommence : trois videos choisies tournent en boucle."""
        videos = [str(video) for video in self._picked_videos()]
        if not videos:
            return
        self.board.clear_picked()
        self.aside_playlist = videos
        self.aside_playlist_at = 0
        self._aside_play_list()

    def _aside_play_list(self) -> None:
        video = self.aside_playlist[self.aside_playlist_at]
        count = len(self.aside_playlist)
        self._aside_play(video, f"{self.aside_playlist_at + 1}/{count} · "
                                f"{Path(video).name}")

    def play_picked(self) -> None:
        """Écrit une liste de lecture et la confie au lecteur du système.

        Un fichier .m3u plutôt qu'une ligne de commande vers tel ou tel lecteur :
        on ne présume pas de celui qui est installé, et celui que l'utilisateur
        a choisi pour ses vidéos est le bon.
        """
        videos = self._picked_videos()
        if not videos:
            return
        playlist = APP_DIR / "selection.m3u"
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            playlist.write_text(
                "#EXTM3U\n" + "\n".join(str(video) for video in videos),
                encoding="utf-8",
            )
            os.startfile(str(playlist))
        except OSError as exc:
            self.show_banner(f"Lecture impossible : {exc}", "error")
            return
        self.show_banner(f"{len(videos)} vidéo(s) envoyée(s) au lecteur", "info")

    def move_picked_hint(self) -> None:
        """Le deplacement groupe passe par l'arborescence : on l'ouvre."""
        if not self.tree.isVisible():
            self.toggle_tree(True)
        self.tree.set_action("send")
        self.show_banner(
            "Cliquez le dossier de destination dans l'arborescence.", "info")

    def delete_picked(self) -> None:
        for item in list(self.board.picked_items()):
            position = self._position_of(item)
            if position >= 0:
                self.index = position
                self.act_delete()
        self.board.clear_picked()

    def move_picked(self, dest: dict) -> int:
        """Envoie tous les elements coches vers cette destination."""
        moved = 0
        for item in list(self.board.picked_items()):
            position = self._position_of(item)
            if position < 0:
                continue
            self.index = position
            before = self.stats["moved"]
            self.act_move(dest)
            moved += self.stats["moved"] > before
        self.board.clear_picked()
        return moved

    def _position_of(self, item) -> int:
        for position, other in enumerate(self.items):
            if other.item_id == item.item_id:
                return position
        return -1

    def on_board_open(self, position: int) -> None:
        """Un clic sur une vignette descend a l'etage du dessous : sa fiche.

        Le meme geste partout, quel que soit l'onglet. On y note, on y range, on
        y passe — et chaque decision avance a l'element suivant de la meme liste.
        `Echap` remonte aux vignettes, a l'endroit qu'on avait quitte.
        """
        if not (0 <= position < len(self.items)):
            return
        if self.tab == TAB_SPLIT:
            return self.open_video_path(str(self.items[position].path))
        self.index = position
        item = self.items[position]
        self.browsing = False
        self._apply_selectors()
        self.viewer.setCurrentWidget(
            self.grid if item.kind == MODE_FOLDERS else self.single)
        self.show_item(position)

    def show_board_at(self, position: int) -> None:
        """Retour aux vignettes, sur celle qu'on venait d'ouvrir."""
        self.browsing = True
        self._hush_players()
        self.refresh_board()
        if 0 <= position < len(self.items):
            self.board.scroll_to(position)
        self._apply_selectors()
        self.setFocus()

    def rate_current(self, stars: int) -> None:
        """Favori, ou non : 1 a 5 mettent en favori, 0 retire."""
        item = self.current
        if item is None:
            return
        wanted = 1 if int(stars or 0) > 0 else 0
        if (self.ratings.get(item.path) > 0) == bool(wanted):
            self.stars.set_value(wanted)
            return
        value = self.ratings.set(item.path, wanted)
        self.stars.set_value(value)
        self.ratings.flush()
        self.show_banner(
            f"★ « {item.name} » en favori" if value
            else f"« {item.name} » retiré des favoris", "quiet",
        )

    def pick_random_here(self) -> None:
        """Tire au hasard parmi les vidéos du seul élément affiché."""
        import random
        if self.browsing:
            # Sur la planche, « ici » est la liste affichee, filtres compris.
            pool = [str(v) for i in self.items if not i.locked for v in i.videos]
            where = "cette liste"
        else:
            item = self.current
            pool = [str(video) for video in
                    (self._preview_videos(item) if item else [])]
            where = f"« {item.name} »" if item else ""
        if not pool:
            self.show_banner("Aucune vidéo ici", "quiet")
            return
        video = random.choice(pool)
        self.show_banner(f"Au hasard dans {where} : {Path(video).name}", "info")
        self.play_in_app(video)

    def pick_random(self) -> None:
        """Lance une vidéo au hasard, piochée dans tout ce que l'analyse connaît.

        Tirer parmi les seuls éléments affichés ramenait toujours les mêmes :
        en mode dossier, la liste ne compte que quelques dizaines d'entrées.
        """
        import random
        # Toute la collection, meme quand on est entre dans un dossier : la
        # liste affichee n'etait plus, apres un premier tirage, que le dossier
        # ou il avait mene — le hasard « general » tournait en rond dedans.
        source = list(self._collection()) + list(self.all_items)
        pool = []
        seen = set()
        for item in source:
            if item.locked or item.is_tag:
                continue
            for video in item.videos:
                key = str(video)
                if key not in seen and not under_veiled(key):
                    seen.add(key)
                    pool.append(key)
        if not pool:
            self.show_banner("Aucune vidéo à tirer au sort", "quiet")
            return
        video = random.choice(pool)
        self.show_banner(
            f"Au hasard parmi {len(pool)} vidéos : « {Path(video).name} »", "info"
        )
        self.play_in_app(video)

    # ------------------------------------------------------------------
    # Panneau d'arborescence
    # ------------------------------------------------------------------
    def toggle_tree(self, visible: bool | None = None) -> None:
        show = (not self.tree.isVisible()) if visible is None else visible
        if show and not self.tree.root:
            # Par défaut, on montre les voisins de la racine triée : c'est là
            # que se trouvent presque toujours les dossiers de destination.
            default = self.cfg["tree_root"] or (
                str(self.root.parent) if self.root else ""
            )
            self.tree.set_root(default)
            if not self.tree.root:
                self.tree.choose_root()
                if not self.tree.root:
                    return
        self.tree.setVisible(show)
        self.cfg["tree_visible"] = show
        self.cfg.save()
        self.tree_button.setChecked(show)
        self.setFocus()

    def on_tree_folder(self, path: str) -> None:
        # Une selection en cours prime : c'est elle qu'on vient de designer.
        if self.board.picked_ids and self.tree.action == "send":
            moved = self.move_picked({"path": path, "label": Path(path).name})
            self.show_banner(
                f"{moved} élément(s) envoyé(s) vers « {Path(path).name} »",
                "info")
            return
        return self._on_tree_folder(path)

    def _on_tree_folder(self, path: str) -> None:
        """Le clic envoie l'élément en fiche, et ouvre le dossier en planche.

        C'est la seule différence d'intention entre les deux vues : parcourir
        d'un côté, décider de l'autre.
        """
        if self.tree.action == "go" or self.browsing:
            self.levels.append({
                "root": self.root, "mode": self.mode,
                "item_id": self.current.item_id if self.current else "",
            })
            self.start_root(Path(path), reset_levels=False)
            return
        self.act_move({"path": path, "label": Path(path).name})

    def on_tree_action(self, action: str) -> None:
        self.cfg["tree_action"] = action
        self.cfg.save()
        self.setFocus()

    def on_tree_root_changed(self, path: str) -> None:
        # La meme ecriture partout (lettre plutot que \\serveur\partage).
        self.cfg["tree_root"] = str(canon_root(path)) if path else ""
        self.cfg.save_soon()

    # ------------------------------------------------------------------
    # Filtre par nom
    # ------------------------------------------------------------------
    @staticmethod
    def _terms(text: str) -> list:
        return [term.strip().lower() for term in text.split(",") if term.strip()]

    def _parsed(self, query: str):
        """La requete, analysee une fois par changement et non par element.

        On la reanalysait pour chacune des cent mille videos, a chaque clic.
        """
        if query != getattr(self, "_parsed_for", None):
            self._parsed_for = query
            self._parsed_value = parse_query(query)
        return self._parsed_value

    def _sortable_count(self) -> int:
        """Combien d'elements pourraient s'afficher, filtres mis a part."""
        return len(self._sortable_items())

    def _sortable_items(self) -> list:
        """Les elements affichables, filtres mis a part — gardes tant que la
        liste ne change pas.

        Les recalculer a chaque clic, trois fois, sur cent mille elements,
        c'etait l'essentiel de la lenteur des filtres.
        """
        from . import scan as _scan
        key = (id(self.all_items), len(self.all_items), _scan.SHOW_VEILED,
               frozenset(_scan.VEILED))
        if self.scanning or key != getattr(self, "_sortable_key", None):
            self._sortable_key = key
            sortable = self._sortable
            self._sortable_list = [i for i in self.all_items if sortable(i)]
            self._sortable_folded = None
        return self._sortable_list

    def _sortable_names(self) -> list:
        """Les noms replies (sans accents ni majuscules) de cette liste,
        calcules une fois par liste et non a chaque frappe.

        Replier cent mille noms a chaque lettre tapee coutait plus que la
        recherche elle-meme.
        """
        items = self._sortable_items()
        folded = getattr(self, "_sortable_folded", None)
        if folded is None or len(folded) != len(items):
            folded = [fold(item.name) for item in items]
            self._sortable_folded = folded
        return folded

    def _sortable(self, item) -> bool:
        """Un dossier sans une seule video n'a rien a trier.

        L'afficher revenait a faire chercher, parmi des vignettes grises, celles
        qui montrent quelque chose. Il ne compte pas non plus comme « filtre » :
        ce n'est pas l'utilisateur qui l'a ecarte.
        """
        if not item.is_tag and under_veiled(item.path):
            # Ce que l'index garde d'un dossier masque ne doit pas reparaitre.
            return False
        return not (item.kind == MODE_FOLDERS and not item.is_tag
                    and not item.video_count)

    def _matches(self, item) -> bool:
        return self._sortable(item) and self._matcher()(item)

    def _filtered(self, items=None) -> list:
        """Ce qui passe les filtres, parmi `items` (tout, par defaut)."""
        if items is None:
            rules = self.criteria or {}
            query = rules.get("include", self.cfg["filter_include"])
            if query:
                # La recherche porte sur les noms deja replies de la liste :
                # un seul test prepare, et plus aucun repli par element.
                test = query_tester(self._parsed(query), self._loose)
                keep = self._matcher(ignore_query=True)
                return [item for item, name in zip(self._sortable_items(),
                                                   self._sortable_names())
                        if test(name) and keep(item)]
            keep = self._matcher()
            return [item for item in self._sortable_items() if keep(item)]
        keep = self._matcher()
        sortable = self._sortable
        return [item for item in items if sortable(item) and keep(item)]

    def _matcher(self, ignore_text: bool = False, ignore_query: bool = False):
        """Le filtre, pret a courir : reglages lus une fois, pas par element.

        Relire la configuration, reanalyser la recherche et redecouper les
        exclusions pour chacune des cent mille videos coutait l'essentiel
        d'un clic sur « Non vus » ou « Verticales ». `ignore_query` laisse de
        cote la seule recherche, que l'appelant fait lui-meme sur des noms
        deja replies.
        """
        rules = self.criteria or {}
        cfg = self.cfg
        only_unseen = bool(cfg["only_unseen"])
        seen = INDEX.seen
        query = ("" if ignore_text or ignore_query
                 else rules.get("include", cfg["filter_include"]))
        parsed = self._parsed(query) if query else None
        loose = self._loose
        exclude = [] if ignore_text else self._terms(
            rules.get("exclude", cfg["filter_exclude"]))
        folder_min = (rules.get("folder_min") or 0) if rules else 0
        folder_max = (rules.get("folder_max") or 0) if rules else 0
        wanted = rules.get("orientations") if rules else None
        orient = next(iter(wanted)) if wanted is not None and len(wanted) == 1 else None
        probe = INDEX.probe

        def keep(item) -> bool:
            is_tag = item.is_tag
            if only_unseen and not is_tag and (item.status or item.item_id in seen):
                return False
            if parsed is not None or exclude:
                name = item.name
                # Un seul champ, mais quelques mots de plus que les siens :
                # « plage -hiver », « plage or mer », « "saison 2" ».
                if parsed is not None and not matches_parsed(name, parsed, loose):
                    return False
                if exclude:
                    low = name.lower()
                    if any(term in low for term in exclude):
                        return False
            if item.kind == MODE_FOLDERS:
                # Un dossier ou un mot-cle n'a ni format ni duree : ces filtres
                # portent sur ses videos, une fois dedans. Les appliquer a la
                # carte faisait disparaitre tous les mots-cles des qu'on
                # choisissait « Horizontales ».
                if not is_tag:
                    if folder_min and item.video_count < folder_min:
                        return False
                    if folder_max and item.video_count > folder_max:
                        return False
                return True
            if orient is not None:
                # Strict : ce dont on ignore l'orientation est ecarte aussi.
                # Le laisser passer donnait « Verticales » plein
                # d'horizontales — tout ce qui n'avait pas encore ete sonde.
                info = probe(item.path) or {}
                width, height = info.get("width") or 0, info.get("height") or 0
                if not (width and height):
                    return False
                if ("vertical" if height > width else "horizontal") != orient:
                    return False
            return True

        return keep

    def on_controls_changed(self) -> None:
        if self.tab == TAB_SPLIT:
            rules = self.controls.criteria()
            self.criteria = rules
            self.cfg["filter_include"] = rules["include"]
            self.cfg.save()
            self.show_wall()
            return
        return self._on_controls_changed()

    def _explain_orientation(self) -> None:
        """Un écran vide après un filtre d'orientation mérite une explication.

        Le filtre est strict : il écarte ce dont l'orientation n'est pas
        connue. Sur une collection peu analysée, cela peut tout écarter — et
        rien ne le disait, ce qui ressemblait à une panne.
        """
        chosen = self.controls.orientations()
        if len(chosen) != 1 or self.items:
            return
        unsure = 0
        for item in self.all_items:
            if item.is_tag or item.kind == MODE_FOLDERS:
                continue
            info = INDEX.probe(item.path) or {}
            if not (info.get("width") and info.get("height")):
                unsure += 1
        if not unsure:
            return
        quoi = "verticale" if chosen[0] == "vertical" else "horizontale"
        self.show_banner(
            f"Aucune vidéo {quoi} connue — {self._thousands(unsure)} n'ont pas "
            "encore été analysées. Leur résolution se cherche en ce moment ; "
            "⋯ → « Repérer les plans » la trouve pour toutes d'un coup.",
            "info")
        self.learn_orientations()

    def learn_orientations(self) -> None:
        """Sonde en fond la résolution de ce qu'on ne connaît pas encore."""
        if self.wall_prober is not None:
            return
        unknown = [str(item.path) for item in self.all_items
                   if not item.is_tag and item.kind != MODE_FOLDERS
                   and not (INDEX.probe(item.path) or {}).get("width")]
        if not unknown:
            return
        import random as _random
        _random.shuffle(unknown)
        self.wall_prober = InfoScan(unknown[:120], self)
        self._own_thread(self.wall_prober, "wall_prober")
        self.wall_prober.done.connect(self._orientations_learned)
        self.wall_prober.start()

    def _orientations_learned(self, count: int) -> None:
        self.wall_prober = None
        if not count:
            return
        before = len(self.items)
        self.items = self._filtered()
        if len(self.items) != before:
            self.apply_sort()
            if self.browsing:
                self.refresh_board()
            self._show_counts()

    def _on_controls_changed(self) -> None:
        """Un reglage a bouge : on refiltre, puis on reclasse."""
        self._loose = False
        rules = self.controls.criteria()
        self.criteria = rules
        self.cfg["orientations"] = list(rules.get("orientations") or [])
        self.cfg["folder_min"] = int(rules.get("folder_min") or 0)
        self.cfg["folder_max"] = int(rules.get("folder_max") or 0)
        self.cfg["filter_include"] = rules["include"]
        self.cfg["filter_exclude"] = rules["exclude"]
        # Une seule ecriture, un instant plus tard : `apply_filter` en
        # demande une aussi, et chacune attend le disque.
        self.cfg.save_soon()
        self.apply_filter(rules["include"], rules["exclude"])

    def _retry_loosely(self) -> bool:
        """Rien d'exact : on retente a peu pres, et on le dit.

        Une lettre de travers ne doit pas rendre un ecran vide quand il y a
        cent mille fichiers derriere, dont beaucoup sont mal nommes.
        """
        query = (self.criteria or {}).get("include", self.cfg["filter_include"])
        if self._loose or not query or not self.all_items:
            return False
        self._loose = True
        found = self._filtered()
        if not found:
            self._loose = False
            return False
        self.items = found
        self.show_banner(
            f"Rien d'exact pour « {query} » — voici les {len(found)} "
            "résultat(s) approchants." + ("" if fuzzy_available() else
            " (installez rapidfuzz pour de meilleurs rapprochements)"),
            "quiet")
        return True

    def apply_filter(self, include: str, exclude: str) -> None:
        # Toute nouvelle recherche repart de l'exact : sans cela, un repli
        # sur l'a-peu-pres restait en vigueur pour les suivantes, sans que
        # rien ne le dise.
        self._loose = False
        self.cfg["filter_include"] = include
        self.cfg["filter_exclude"] = exclude
        # Les criteres sont la seule source consultee par _matches : les laisser
        # de cote ferait ignorer silencieusement les termes qu'on vient de poser.
        self.criteria = dict(self.criteria or {})
        self.criteria["include"] = include
        self.criteria["exclude"] = exclude
        self.cfg.save_soon()
        mark(f"apply_filter {include!r}")

        current = self.current
        self.plans = {k: v for k, v in self.plans.items() if k.startswith("board@")}
        self.items = self._filtered()
        if not self.items:
            self._retry_loosely()
        self.apply_sort()
        self._show_counts()

        if not self.items:
            self.preview.cancel_all()
            self._release_media()
            self.index = 0
            self.item_title.setText("Aucun élément ne correspond au filtre")
            self.item_parent.setText("")
            self.item_subtitle.setText(
                f"{len(self.all_items)} élément(s) masqué(s). Modifiez ou effacez le filtre."
            )
            self.grid.set_no_videos("—")
            self.update_counter()
            return

        # On reste sur le même élément s'il passe encore le filtre. Cherche
        # par identite : « in » comparait champ a champ chacun des cent mille
        # elements.
        found = next((at for at, item in enumerate(self.items)
                      if item is current), -1) if current is not None else -1
        if found >= 0:
            self.index = found
        else:
            self.index = min(self.index, len(self.items) - 1)
        if self.stack.currentIndex() == PAGE_DONE:
            self.stack.setCurrentIndex(PAGE_SORT)
        if self.browsing:
            self.refresh_board()
        else:
            self.show_item(self.index)

    def focus_filter(self) -> None:
        self.controls.focus_search()

    def _item_by_id(self, item_id: str):
        # Sur self.all_items : un transfert peut aboutir alors que le filtre a
        # entre-temps ecarte l'element de la liste visible.
        for item in self.all_items:
            if item.item_id == item_id:
                return item
        return None

    def _enqueue(self, job: Transfer, banner: str, tone: str) -> None:
        """Lance l'operation en tache de fond et passe tout de suite a la suite."""
        self.transfers.submit(job)
        self.show_banner(banner, tone)
        self.advance()

    def act_delete(self) -> None:
        """Écarte l'élément sans rien détruire : il part dans la corbeille de session."""
        item = self.current
        if item is None or item.locked:
            return self.advance()
        if not item.movable or Path(item.path).name.startswith(PARENT_PREFIX):
            self.show_banner(
                "Un mot-clé ou un dossier de tête ne se supprime pas d'ici",
                "error",
            )
            return
        state = actions.probe(item.path)
        if state == "absent":
            self.show_banner(f"Introuvable : {item.name}", "error")
            return self.advance()
        if state == "injoignable":
            # Il n'a pas disparu : le NAS ne repond pas. On reste dessus.
            self.show_banner(f"NAS injoignable : « {item.name} » n'a pas été "
                             "touché. Réessayez dans un instant.", "error")
            return
        others = (item.file_count - item.video_count
                  if item.kind == MODE_FOLDERS else 0)
        # Un dossier lu en partie (sous-dossier illisible pendant une coupure)
        # peut cacher ce que ses comptes ne disent pas : on demande aussi.
        unsure = item.kind == MODE_FOLDERS and (
            getattr(item, "incomplete", False) or getattr(item, "unreadable", False))
        if others > 0 or unsure:
            # Un dossier qui contient autre chose que des videos — documents,
            # images, programmes — ne part pas sur une seule touche : c'est
            # ainsi qu'un dossier entier de papiers a ete ecarte une fois.
            what = (f"« {item.name} » contient {item.video_count} vidéo(s), mais "
                    f"aussi {others} autre(s) fichier(s) : documents, images, "
                    "programmes…" if others > 0 else
                    f"« {item.name} » n'a pas pu être lu en entier (NAS) : il "
                    "contient peut-être autre chose que des vidéos.")
            answer = QMessageBox.question(
                self, "Supprimer ce dossier ?",
                what + "\n\nTout le dossier sera supprimé. Continuer ?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self._release_media(item.path)
        item.status = "pending_delete"
        item.status_detail = "Corbeille"
        self._enqueue(
            Transfer(kind="move", purpose="delete", src=item.path,
                     dest=self.trash.folder_for(item.path),
                     label="Corbeille", item_id=item.item_id),
            f"« {item.name} » → corbeille  ·  Ctrl+Z ou Ctrl+B pour la rouvrir",
            "error",
        )

    def act_move(self, dest: dict) -> None:
        item = self.current
        if item is None or item.locked:
            return self.advance()
        dest_dir = Path(dest["path"])
        label = dest.get("label") or dest_dir.name
        problem = self._move_objection(item, dest_dir)
        if problem:
            self.show_banner(problem, "error")
            return
        self._release_media(item.path)
        item.status = "pending_move"
        item.status_detail = label
        self._enqueue(
            Transfer(kind="move", src=item.path, dest=dest_dir,
                     label=label, item_id=item.item_id),
            f"« {item.name} » → {label}", "done",
        )

    def _move_objection(self, item, dest_dir: Path) -> str:
        """Verifie d'avance ce qui condamnerait le transfert, pour ne pas avancer."""
        if item.is_tag:
            return (f"« {item.name} » est un mot-clé, pas un dossier : entrez "
                    "dedans (Ctrl+↓) pour traiter les vidéos qu'il réunit.")
        if item.loose_only:
            return ("Cette entrée regroupe des vidéos en vrac : entrez dedans "
                    "(Ctrl+↓) pour les traiter une par une.")
        if Path(item.path).name.startswith(PARENT_PREFIX):
            return f"« {item.name} » est un dossier de tête : il ne se déplace pas."
        state = actions.probe(item.path)
        if state == "absent":
            return f"Introuvable : {item.name}"
        if state == "injoignable":
            return (f"NAS injoignable : « {item.name} » n'a pas été déplacé. "
                    "Réessayez dans un instant.")
        try:
            if Path(item.path).samefile(dest_dir):
                return "C'est déjà ce dossier."
        except OSError:
            pass
        if Path(item.path).is_dir():
            try:
                dest_dir.resolve().relative_to(Path(item.path).resolve())
                return "Impossible : la destination est dans le dossier à déplacer."
            except ValueError:
                pass
        return ""

    def act_skip(self) -> None:
        item = self.current
        if item is not None and not item.processed and item.status != "skipped":
            item.status = "skipped"
            self.stats["skipped"] += 1
            self._note_decision()
        self.advance()

    def act_undo(self) -> None:
        if self.transfers.busy:
            # L'historique ne contient que les transferts aboutis : annuler
            # maintenant risquerait de défaire autre chose que la dernière action.
            self.show_banner(
                "Transfert en cours — l'annulation sera possible dans un instant.",
                "#3a3226",
            )
            return
        if not self.history:
            self.show_banner("Rien à annuler", "quiet")
            return
        entry = self.history[-1]
        if not entry.reversible:
            self.show_banner(
                "Suppression envoyée à la corbeille Windows : restaurez-la depuis l'explorateur.",
                "#3a3226",
            )
            return
        self._release_media()
        self.history.pop()
        item = self._item_by_path(entry.src)
        if item is not None:
            item.status = "pending_undo"
        self.transfers.submit(Transfer(
            kind="undo", src=entry.dst or entry.src, entry=entry,
            label=entry.label, item_id=item.item_id if item else "",
        ))
        self.show_banner(f"Restauration de « {Path(entry.src).name} »…", "info")

    def _item_by_path(self, path: Path):
        for item in self.all_items:
            if item.path == path:
                return item
        return None

    # ------------------------------------------------------------------
    # Retour des transferts
    # ------------------------------------------------------------------
    def on_transfer_finished(self, job: Transfer) -> None:
        item = self._item_by_id(job.item_id) if job.item_id else None

        if job.state == "failed":
            if item is not None:
                item.status = ""
                item.status_detail = ""
            self.show_banner(f"Échec sur « {job.name} » : {job.error}", "error")
            self.update_counter()
            return

        from .dupes_memory import NOT_DUPES

        if job.kind == "move" and job.purpose == "delete":
            if item is not None:
                item.status = "deleted"
            self.stats["deleted"] += 1
            self._note_decision()
            size = item.size if item is not None else 0
            self.trash.record(Path(job.src), job.result, size)
            self.ratings.rename(job.src, job.result)
            self.history.append(
                HistoryEntry("delete", Path(job.src), job.result, job.label, True)
            )
            self._tell_hidden_files(job, item)
        elif job.kind == "move":
            if item is not None:
                item.status = "moved"
            self.stats["moved"] += 1
            self._note_decision()
            # Un dossier emporte les etoiles, les « pas des doublons » et la
            # place dans la collection de tout ce qu'il contient.
            self.ratings.rename(job.src, job.result)
            NOT_DUPES.renommer(job.src, job.result)
            self._follow_move(job.src, job.result)
            self.history.append(
                HistoryEntry("move", Path(job.src), job.result, job.label, True)
            )
        elif job.kind == "delete":
            if item is not None:
                item.status = "deleted"
            self.stats["deleted"] += 1
            self._note_decision()
            self.history.append(
                HistoryEntry("delete", Path(job.src), job.result, job.label, job.reversible)
            )
        else:  # annulation
            entry = job.entry
            action = getattr(entry, "action", "")
            # La ou l'element est vraiment revenu : a cote de sa place si
            # elle a ete reprise entre-temps.
            back = Path(job.result) if job.result else Path(entry.src)
            if action == "delete" and entry.dst:
                self.trash.forget(Path(entry.dst))
            if entry.dst:
                # Son etoile etait partie avec lui : elle revient aussi.
                self.ratings.rename(entry.dst, back)
                if action == "move":
                    NOT_DUPES.renommer(entry.dst, back)
                    self._follow_move(entry.dst, back)
            if item is not None:
                item.status = ""
                item.status_detail = ""
            key = "deleted" if action == "delete" else "moved"
            self.stats[key] = max(0, self.stats[key] - 1)
            self.show_banner(
                f"Annulé : « {Path(entry.src).name} » est revenu à sa place"
                if back == Path(entry.src) else
                f"Annulé : « {Path(entry.src).name} » est revenu, sous le nom "
                f"« {back.name} » : sa place avait été reprise", "info"
            )
            if item is not None and self.current is item:
                self.show_item(self.index)

        if job.warning:
            # Tout est arrive, mais l'ancienne place n'a pas pu etre videe en
            # entier : c'est fait, avec une reserve qu'il faut dire.
            self.show_banner(f"« {job.name} » : {job.warning}", "error")
        self.update_counter()
        if self.browsing and item is not None:
            for position, listed in enumerate(self.items):
                if listed is item:
                    self.board.set_state(position, item.status)
                    break

    def _tell_hidden_files(self, job: Transfer, item) -> None:
        """Un dossier ecarte emportait plus que ses videos : le dire tant qu'un
        Ctrl+Z suffit.

        La garde de suppression se fie aux comptes de l'index, qui ignorent ce
        qui a change plus bas. Le transfert recompte apres coup, en tache de
        fond ; c'est ici, avant que la perte ne devienne definitive a la
        fermeture, qu'on peut encore le rattraper.
        """
        others = getattr(job, "others", -1)
        if others <= 0:
            return
        expected = 0
        if item is not None and item.kind == MODE_FOLDERS:
            expected = max(0, item.file_count - item.video_count)
        if expected:
            return          # la question a ete posee avant d'ecarter
        where = ("sur le NAS il sera détruit à la fermeture"
                 if media.is_network_path(job.result or job.src)
                 else "à la fermeture il rejoindra la corbeille de Windows")
        self.show_banner(
            f"« {job.name} » contenait aussi {others} autre(s) fichier(s) — "
            f"Ctrl+Z pour le récupérer ; {where}.", "error")

    def _follow_move(self, old, new) -> None:
        """La collection en memoire suit un deplacement reussi.

        L'index oublie les dossiers touches, et la prochaine relecture les
        corrigera ; d'ici la, la video rangee restait a son ancien chemin dans
        l'onglet Videos, le mur ou les favoris, et n'apparaissait pas au
        nouveau. On retire donc ce qui est parti des dossiers qui le
        contenaient, et on l'ajoute a ceux qui le recoivent. L'element deplace
        lui-meme garde son etat « rangé » jusqu'a la relecture.
        """
        old_s, new_s = str(old), str(new)
        if not old_s or not new_s or old_s == new_s or not self._plain_items:
            return
        old_pref = old_s.rstrip("\\/") + os.sep
        new_pref = new_s.rstrip("\\/") + os.sep
        low_old = os.path.normcase(old_pref)
        low_new = os.path.normcase(new_pref)
        moved = None           # les videos parties, telles qu'elles etaient
        takers = []
        touched = set()
        for item in self._plain_items:
            if item.is_tag or item.kind != MODE_FOLDERS:
                continue
            base = os.path.normcase(str(item.path).rstrip("\\/") + os.sep)
            if low_old.startswith(base) and low_old != base:
                # Un dossier qui contenait ce qui est parti.
                keep, gone = [], []
                for video in item.videos:
                    text = str(video)
                    if text == old_s or os.path.normcase(text).startswith(low_old):
                        gone.append(text)
                    else:
                        keep.append(video)
                if gone:
                    item.videos = keep
                    item.video_count = max(0, item.video_count - len(gone))
                    item.file_count = max(0, item.file_count - len(gone))
                    if moved is None:
                        moved = gone
                    touched.add(item.item_id)
            if low_new.startswith(base) and low_new != base:
                takers.append(item)
        if moved is None:
            # Le dossier deplace etait lui-meme un element de la collection :
            # ce sont ses videos qui partent.
            for item in self._plain_items:
                if not item.is_tag and str(item.path) == old_s:
                    moved = [str(video) for video in item.videos]
                    break
        # Les fiches « video » de l'onglet Videos : celles de ce qui est parti
        # seulement, et non un balayage des cent mille.
        self._flat_cache.pop(old_s, None)
        for text in moved or ():
            self._flat_cache.pop(text, None)
        if moved and takers:
            arrived = [_as_path(new_s + text[len(old_s):]) for text in moved]
            for item in takers:
                known = {str(video) for video in item.videos}
                fresh = [video for video in arrived if str(video) not in known]
                if item.loose_only:
                    # L'entree « en vrac » d'un rayonnage ne porte que ses
                    # videos directes, pas celles de ses sous-dossiers.
                    here = os.path.normcase(str(item.path).rstrip("\\/"))
                    fresh = [video for video in fresh
                             if os.path.normcase(str(Path(video).parent)) == here]
                if fresh:
                    item.videos = list(item.videos) + fresh
                    item.video_count += len(fresh)
                    item.file_count += len(fresh)
                    touched.add(item.item_id)
        if touched:
            # Les apercus retenus montraient l'ancienne composition.
            def owner(key: str) -> str:
                for prefix in ("board@", "peek@"):
                    if key.startswith(prefix):
                        return key[len(prefix):]
                return key.rsplit("@", 1)[0]

            self.plans = {key: plan for key, plan in self.plans.items()
                          if owner(key) not in touched}

    def on_transfers_changed(self, active: int) -> None:
        self.pending_label.setText(
            f"⟳ {active} transfert{'s' if active > 1 else ''}" if active else ""
        )
        self.pending_label.setVisible(active > 0)

    def on_trash_changed(self, count: int) -> None:
        for action in self.overflow.actions():
            if action.text().startswith("Corbeille"):
                action.setText(
                    f"Corbeille de session ({count})" if count
                    else "Corbeille de session"
                )

    def _refresh_mute(self) -> None:
        muted = self.cfg["muted"]
        self.mute_button.setIcon(draw_icon("speaker", on=not muted))
        self.mute_button.setToolTip(
            "Son coupé — cliquer pour l'activer   (Ctrl+M)" if muted
            else "Son actif — cliquer pour le couper   (Ctrl+M)"
        )

    def step(self, delta: int) -> None:
        """Element precedent ou suivant, comme les fleches du clavier.

        Case « rester dans ce dossier » cochee : la voisine dans le dossier
        de la video, et non dans la liste — qui, dans l'onglet Videos, melange
        toute la collection.
        """
        item = self.current
        if (self.cfg["stay_in_folder"] and item is not None
                and item.kind != MODE_FOLDERS and not self.browsing):
            target = self._folder_neighbour(str(item.path), delta)
            if target:
                for position, other in enumerate(self.items):
                    if str(other.path) == target:
                        return self.show_item(position)
                return self.play_in_app(target)
        if self.items:
            self.show_item(self.index + delta)

    def set_stay_in_folder(self, on: bool) -> None:
        """Une seule coche pour toute l'application : fiche, lecteur, mur."""
        on = bool(on)
        if bool(self.cfg["stay_in_folder"]) == on:
            return
        self.cfg["stay_in_folder"] = on
        self.cfg.save_soon()
        for bar in (self.single_bar, self.aside_bar):
            bar.stay.blockSignals(True)
            bar.stay.setChecked(on)
            bar.stay.blockSignals(False)
        if self.wall.stay != on:
            self.wall.stay = on
            for pane in self.wall.panes:
                pane.set_stay(on)

    def _folder_videos(self, path: str) -> list:
        """Les videos du dossier de ce fichier, lues une fois par session."""
        from .config import VIDEO_EXTS
        folder = Path(path).parent
        listing = self._siblings_cache.get(str(folder))
        if listing is None:
            mark(f"listing {folder}")
            try:
                listing = sorted(
                    (entry.path for entry in os.scandir(folder)
                     if entry.is_file()
                     and entry.name[entry.name.rfind("."):].lower() in VIDEO_EXTS),
                    key=str.lower)
            except OSError:
                listing = []
            self._siblings_cache[str(folder)] = listing
        return listing

    def _folder_neighbour(self, path: str, delta: int) -> str:
        listing = self._folder_videos(path)
        if len(listing) < 2:
            self.show_banner("C'est la seule vidéo de son dossier.", "quiet")
            return ""
        try:
            at = listing.index(path)
        except ValueError:
            at = -1 if delta > 0 else 0
        return listing[(at + delta) % len(listing)]

    def advance(self) -> None:
        if self.index + 1 >= len(self.items):
            if self.scanning:
                self.item_title.setText("En attente de la suite de l'analyse…")
                self.index = len(self.items) - 1
                QTimer.singleShot(400, self._advance_when_ready)
                return
            self.finish()
            return
        self.show_item(self.index + 1)

    def _advance_when_ready(self) -> None:
        if self.index + 1 < len(self.items):
            self.show_item(self.index + 1)
        elif self.scanning:
            QTimer.singleShot(400, self._advance_when_ready)
        else:
            self.finish()

    def finish(self) -> None:
        self._release_media()
        summary = (
            f"{self.stats['moved']} déplacés   ·   {self.stats['deleted']} supprimés   ·   "
            f"{self.stats['skipped']} laissés de côté   ·   {len(self.items)} éléments vus"
        )
        if self.transfers.busy:
            summary += f"\n\n{self.transfers.active} transfert(s) encore en cours."
        self.done_page.summary.setText(summary)
        self.stack.setCurrentIndex(PAGE_DONE)

    def play_focused(self) -> None:
        """Va à la fiche de ce qui est sous la souris, ou de la vidéo courante."""
        slot = self.grid.hovered_slot
        if self.viewer.currentWidget() is self.grid and slot >= 0:
            tile = self.grid.tiles[slot]
            if tile.video:
                return self.play_in_app(tile.video, tile.ts)
        if self.browsing and self.board.hovered >= 0:
            card = self.board.cards[self.board.hovered]
            if card.video:
                return self.play_in_app(card.video, card.ts)
        item = self.current
        if item is not None and item.kind == MODE_FILES:
            return self.play_in_app(str(item.path))
        self.show_banner("Survolez une vidéo, ou double-cliquez dessus", "quiet")

    def play_in_app(self, path: str, start_s: float = 0.0) -> None:
        """Ouvre la fiche de cette vidéo : son dossier, en mode fichier, sur elle.

        Un lecteur séparé demandait ses propres commandes et sa propre fenêtre
        pour refaire ce que la fiche fait déjà. Aller à la fiche garde un seul
        endroit où l'on regarde, avec le tri et les destinations sous la main.
        """
        video = Path(path)
        if not path or not video.exists():
            self.show_banner("Vidéo introuvable", "error")
            return
        if self.tab == TAB_SPLIT:
            # La fiche d'une video est la meme page, d'ou qu'on vienne : le
            # hasard lance depuis le mur l'ouvrait dans l'onglet du mur, avec
            # ses reglages en haut et sans les touches ni les etoiles en bas.
            if self.wall_full:
                self.toggle_wall_fullscreen(False)
            self.set_tab(TAB_FOLDERS)
        self.grid.stop()
        self.board.stop()

        parent = video.parent
        already_there = (self.root == parent and self.mode == MODE_FILES
                         and not self.browsing)
        if not already_there:
            if self.browsing:
                self.toggle_board(False)
            if self.root is not None and self.root != parent:
                self.levels.append({
                    "root": self.root, "mode": self.mode,
                    "item_id": self.current.item_id if self.current else "",
                })
            self.start_root(parent, MODE_FILES, reset_levels=False,
                            restore_id=str(video))
            return
        for position, item in enumerate(self.items):
            if item.path == video:
                self.show_item(position)
                return

    def open_external(self, path: str = "") -> None:
        target = Path(path) if path else (self.current.path if self.current else None)
        if target:
            actions.reveal(target)

    def edit_tags(self) -> None:
        """Saisit les mots-clés qui deviendront des dossiers virtuels.

        Un mot par ligne ; les dossiers se refont aussitôt, sans relire le
        disque — ce qu'il faut pour savoir en changeant un mot s'il attrape ce
        qu'on visait.
        """
        dialog = TagsDialog(self.tags, self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return self.setFocus()
        self.tags = dialog.result_tags()
        self.cfg["tags"] = self.tags
        self.tag_family = "mine"
        self.cfg["tag_family"] = "mine"
        self.cfg.save()
        self.tag_chips.set_value("mine")
        if self.root is None:
            return self.setFocus()
        if self.tab == TAB_TAGS and self.mode == MODE_FOLDERS:
            self._add_tag_items()
            self.refresh_board()
            found = [i for i in self.all_items if i.is_tag]
            self.show_banner(
                f"{len(found)} mot(s)-clé(s) sur {len(self.tags)} ont trouvé "
                f"des vidéos", "info")
        else:
            self.set_tab(TAB_TAGS)
        self.setFocus()

    def edit_destinations(self) -> None:
        dialog = DestinationsDialog(self.cfg.destinations, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.cfg.set_destinations(dialog.result_destinations())
            self.cfg.save()
            self.commands.rebuild(
                self.cfg.destinations, DELETE_LABELS.get(self.cfg["delete_mode"], "Supprimer")
            )
        self.setFocus()

    def open_web_search(self) -> None:
        """Recherche de videos hors des grandes plateformes, en lien seul."""
        dialog = WebSearchDialog(self.cfg, self)
        dialog.exec()
        self.setFocus()

    # ------------------------------------------------------------------
    # Clavier
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        if event.modifiers() & Qt.AltModifier and key == Qt.Key_Left:
            self.go_back()
            return

        if self.stack.currentIndex() == PAGE_DONE:
            # Le tri d'un sous-dossier fini, on revient d'ou l'on venait.
            if key == Qt.Key_Escape or (ctrl and key == Qt.Key_Up):
                if self.go_up():
                    return
            return super().keyPressEvent(event)
        # Avant tout : le repli doit repondre d'ou que l'on vienne, et en
        # sortir de meme.
        if ctrl and key == Qt.Key_K:
            return self.toggle_quiet()
        if self.stack.currentIndex() == PAGE_QUIET:
            return super().keyPressEvent(event)
        if self.stack.currentIndex() != PAGE_SORT:
            return super().keyPressEvent(event)

        # Les commandes de l'application sont toutes sur Ctrl ou sur une touche
        # de navigation : chiffres et lettres restent libres pour les destinations.
        if ctrl:
            if key == Qt.Key_Z:
                return self.act_undo()
            if key == Qt.Key_T:
                return self.toggle_tree()
            if key == Qt.Key_A:
                return self.pick_all()
            if key == Qt.Key_N:
                return self.clear_picked()
            if key == Qt.Key_I:
                return self.pick_invert()
            if key == Qt.Key_E:
                return self.reveal_current()
            if key == Qt.Key_J:
                if self.tab == TAB_SPLIT:
                    return self.toggle_wall_fullscreen()
                return self.toggle_cinema()
            if key == Qt.Key_M:
                return self.toggle_mute()
            if key == Qt.Key_O:
                return self.open_external()
            if key == Qt.Key_D:
                return self.edit_destinations()
            if key == Qt.Key_F:
                return self.focus_filter()
            if key == Qt.Key_B:
                return self.open_trash()
            if key == Qt.Key_P:
                return self.toggle_board()
            if key == Qt.Key_H:
                return self.pick_random()

            if key == Qt.Key_R:
                return self.refresh_root()
            if key == Qt.Key_Right:
                return self.change_page(1)
            if key == Qt.Key_Left:
                return self.change_page(-1)
            if key == Qt.Key_Down:
                return self.enter_current()
            if key == Qt.Key_Up:
                self.go_up()
                return
            return super().keyPressEvent(event)

        if key == Qt.Key_Shift:
            if not event.isAutoRepeat():
                self.peek_show()
            return
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            return self.act_delete()
        if key == Qt.Key_Space:
            return self.act_skip()
        if key == Qt.Key_Right:
            return self.show_item(self.index + 1)
        if key == Qt.Key_Left:
            return self.show_item(self.index - 1)
        if key == Qt.Key_Escape:
            if self.wall_full:
                return self.toggle_wall_fullscreen(False)
            if not self.radial.isHidden():
                return self.radial.close_menu()
            if self.single.peeking:
                return self.peek_hide()
            if self._wall_peek is not None:
                self.wall.end_peeks()
                self._wall_peek = None
                return
            if self.tab == TAB_SPLIT and self.wall.solo != -1:
                return self.wall.unsolo()
            if self.cinema:
                # Echap sort d'abord du cinema : c'est le geste qu'on fait.
                return self.toggle_cinema(False)
            if not self.browsing:
                # On edite : on remonte aux vignettes avant de quitter le niveau.
                self.show_board_at(self.index)
                return
            if self.go_up():
                return
            self.stop_scan()
            self._release_media()
            self.stack.setCurrentIndex(PAGE_WELCOME)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self.viewer.currentWidget() is self.single:
                self.single.toggle_pause()
            return
        if key == Qt.Key_F:
            return self.play_focused()

        text = event.text().lower().strip()
        if text in ("0", "1", "2", "3", "4", "5"):
            return self.rate_current(int(text))
        if text:
            dest = self.cfg.destination_for_key(text)
            if dest:
                return self.act_move(dest)
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Barre de commandes a la souris
    # ------------------------------------------------------------------
    def on_command_delete(self) -> None:
        self.act_delete()
        self.setFocus()

    def on_command_skip(self) -> None:
        self.act_skip()
        self.setFocus()

    def on_command_move(self, dest: dict) -> None:
        self.act_move(dest)
        self.setFocus()

    def toggle_mute(self) -> None:
        muted = not self.cfg["muted"]
        self.cfg["muted"] = muted
        self.cfg.save()
        self.grid.set_muted(muted)
        self.single.set_muted(muted)
        self.wall.set_muted(muted)
        self._refresh_mute()
        self.show_banner("Son coupé" if muted else "Son activé", "quiet")

    def _fit_to_screen(self, width: int, height: int) -> None:
        """Ouvre a la taille voulue, mais jamais plus grande que l'ecran.

        A 200 % de mise a l'echelle, un ecran de 1920 sur 1080 n'offre plus
        que 960 sur 540 points : une fenetre de 1400 sur 900 y depasse par le
        bas, et la derniere rangee — celle des fleches et de l'avancement —
        se retrouve sous le bord. On laisse donc l'ecran decider du plafond.
        """
        screen = QApplication.primaryScreen()
        if screen is not None:
            free = screen.availableGeometry()
            width = min(width, free.width())
            # Barre de titre et bordures comprises : sans cette marge, le bas
            # de la fenetre passe encore sous la barre des taches.
            height = min(height, free.height() - 48)
        self.resize(max(720, width), max(420, height))

    def minimumSizeHint(self):
        """Plafonne ce que la fenetre exige, quoi qu'en disent ses pieces.

        Qt additionne les largeurs minimales de tout ce qu'elle contient, et il
        a suffi une fois d'une etiquette un peu large pour rendre la fenetre
        impossible a retrecir — tout debordait alors de l'ecran par la droite.
        Plutot que de surveiller chaque piece a jamais, on pose la limite ici :
        la fenetre peut toujours descendre a cette taille, quitte a rogner ce
        qu'elle montre.
        """
        hint = super().minimumSizeHint()
        # Le plafond suit l'ecran : sur un affichage agrandi, il n'y a plus
        # mille cent points de large, et une fenetre qui les exige deborde.
        top_w, top_h = 1100, 620
        screen = QApplication.primaryScreen()
        if screen is not None:
            free = screen.availableGeometry()
            top_w = min(top_w, max(640, free.width() - 40))
            top_h = min(top_h, max(400, free.height() - 80))
        return QSize(min(hint.width(), top_w), min(hint.height(), top_h))

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Shift and not event.isAutoRepeat():
            self.peek_hide()
            return
        super().keyReleaseEvent(event)

    # A la fermeture, combien de temps attendre les transferts en vol avant de
    # proposer de fermer quand meme : un NAS qui ne repond plus les tiendrait
    # sinon indefiniment, fenetre masquee et Prisme impossible a relancer.
    CLOSE_TRANSFER_WAIT_S = 30
    # Et les fils de fond, tous ensemble, une fois arretes.
    CLOSE_THREADS_WAIT_S = 3

    def closeEvent(self, event):
        mark("fermeture")
        # Ce qui est local et rapide d'abord : reglages et favoris ne doivent
        # jamais dependre d'un NAS qui repond.
        self.cfg["window"] = {"w": self.width(), "h": self.height()}
        self.cfg.save()
        self.ratings.flush()
        # La fenetre disparait tout de suite : ce qui suit peut durer, et
        # rien ne doit plus y repondre.
        self.banner.hide()
        self.hide()
        for timer in (self.session_timer, self.activity_timer, self.aside_watch,
                      self.board_timer, self.banner_timer, self.seen_timer,
                      self.burst_timer, self.retry_timer):
            timer.stop()
        # Tous les travaux de fond s'arretent d'un coup — on le demande a
        # chacun, on attendra plus loin, ensemble. Plus aucun ffmpeg ne part,
        # et ceux qui tournent sont tues : un processus invisible gardait
        # sinon le verrou et lisait le partage des heures durant.
        self._stop_background()
        self.stop_scan()
        try:
            self._release_media()
        except RuntimeError:
            pass
        media.close_all()
        try:
            self._wait_transfers()
            self.stop_share(wait=True)
            JOURNAL.close()
            self._flush_trash_on_close()
        except Exception as exc:                           # noqa: BLE001
            # Une coupure au mauvais moment ne doit empecher ni l'index ni les
            # favoris d'etre ecrits.
            QMessageBox.warning(self, "Fermeture incomplète",
                                f"Tout n'a pas pu être rangé : "
                                f"{actions.describe(exc)}")
        finally:
            self.preview.shutdown()
            self.ratings.flush()
            # Tout ce que la relecture a appris est deja ecrit : il ne reste
            # qu'a refermer. Une relecture abandonnee ecrit encore : fermer la
            # connexion sous elle laissait un fichier a moitie ecrit, que le
            # lancement suivant trouvait illisible — et l'on reanalysait
            # tout, chaque fois, sans le savoir.
            self._wait_threads(self.CLOSE_THREADS_WAIT_S)
            self._dying = []
            INDEX.prune()
            INDEX.close()
            self.cfg.save()
        super().closeEvent(event)

    def _stop_background(self) -> None:
        """Demande a chaque fil de fond de s'arreter, sans en attendre aucun."""
        if self.backfill is not None:
            self.backfill.stop()
        for thread in self.findChildren(QThread):
            stop = getattr(thread, "stop", None)
            if callable(stop):
                try:
                    stop()
                except RuntimeError:
                    pass

    def _wait_threads(self, seconds: float) -> None:
        """Attend les fils de fond, tous ensemble, pendant `seconds` au plus.

        L'attente etait de trois secondes pour l'un, quatre pour chaque
        relecture abandonnee, l'une apres l'autre ; les autres n'etaient pas
        attendus du tout, et Qt detruisait un fil encore en marche.
        """
        deadline = time.monotonic() + max(0.0, seconds)
        for thread in self.findChildren(QThread):
            if not shiboken6.isValid(thread) or not thread.isRunning():
                continue
            left = int((deadline - time.monotonic()) * 1000)
            if left <= 0:
                break
            thread.wait(left)

    def _wait_transfers(self) -> None:
        """Un transfert interrompu laisserait un dossier a moitie copie : on
        l'attend, en le disant — mais pas indefiniment."""
        if not self.transfers.busy:
            return
        waiter = QProgressDialog(
            f"{self.transfers.active} transfert(s) en cours — fermeture dès "
            "qu'ils sont terminés.", "Fermer quand même", 0, 0, self)
        waiter.setWindowTitle(APP_NAME)
        waiter.setMinimumDuration(0)
        waiter.show()
        deadline = time.monotonic() + self.CLOSE_TRANSFER_WAIT_S
        while (self.transfers.busy and not waiter.wasCanceled()
               and time.monotonic() < deadline):
            QApplication.processEvents()
            self.transfers.wait(100)
        waiter.close()


def check_tools(parent=None) -> bool:
    if Tools.ffmpeg and Tools.ffprobe:
        return True
    QMessageBox.critical(
        parent, "ffmpeg introuvable",
        f"{APP_NAME} a besoin de ffmpeg et ffprobe pour fabriquer les aperçus.\n\n"
        "Installez-les (winget install Gyan.FFmpeg) ou renseignez leur chemin "
        "dans le fichier de configuration.",
    )
    return False
