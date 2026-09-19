"""Fenêtre principale : enchaînement des éléments et exécution des actions."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QMainWindow, QMessageBox, QProgressBar, QProgressDialog, QPushButton,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from . import actions
from .board import COLUMN_CHOICES, BoardView
from .actions import ActionError, HistoryEntry
from .config import Config
from .header import (
    CONTENT_FOLDERS, CONTENT_VIDEOS, HEADER_STYLE, TAB_FOLDERS,
    TAB_TAGS, TAB_VIDEOS, TABS, VIEW_BROWSE, VIEW_EDIT, Breadcrumb, Chips,
    ControlBar, Segmented,
    build_overflow,
)
from .media import PreviewManager, Tools, page_count
from .ratings import Ratings
from .tagging import build_tag_items, top_words
from .scan import (
    MODE_FILES, MODE_FLAT, MODE_FOLDERS, PARENT_PREFIX, Item, RefreshThread,
    cached_items, detect_mode, human_duration, human_resolution, human_size,
    known_media, list_entries,
)
from .index import INDEX
from .transfer import Transfer, TransferQueue
from .trash import SessionTrash
from .tree import TreePanel
from .widgets import (
    STYLESHEET, CommandBar, DestinationsDialog, PreviewGrid, SinglePlayer,
    StarStrip, TagsDialog, TrashDialog,
)

PAGE_WELCOME, PAGE_SORT, PAGE_DONE = 0, 1, 2

DELETE_LABELS = {
    "recycle": "Corbeille",
    "permanent": "Supprimer définitivement",
    "local_trash": "Dossier _TRASH",
}


class WelcomePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 50, 60, 50)
        layout.setSpacing(14)

        title = QLabel("VideoSorter", self)
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


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("VideoSorter")
        self.resize(cfg["window"].get("w", 1400), cfg["window"].get("h", 900))
        self.setStyleSheet(STYLESHEET + HEADER_STYLE)

        self.all_items: list = []      # tout ce que l'analyse a trouve
        self.items: list = []          # ce que le filtre laisse passer
        self.index = 0                 # index dans self.items
        # Un seul onglet dit ou l'on est : dossiers, videos, ou edition.
        self.tab = cfg["tab"] if cfg["tab"] in TABS else TAB_FOLDERS
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
        self._plain_items: list = []
        self._plain_root = None
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

        self.transfers = TransferQueue(self)
        self.transfers.finished.connect(self.on_transfer_finished)
        self.transfers.changed.connect(self.on_transfers_changed)

        self.preview = PreviewManager(cfg["thumb_width"], self)
        self.preview.plan_ready.connect(self.on_plan_ready)
        self.preview.thumb_ready.connect(self.on_thumb_ready)
        self.preview.thumb_failed.connect(self.on_thumb_failed)

        self._build_ui()
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
        self.welcome.recent.itemActivated.connect(
            lambda item: self.start_root(Path(item.text()))
        )
        self.stack.addWidget(self.welcome)

        sort_page = QWidget(self)
        layout = QVBoxLayout(sort_page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(10)
        self.crumbs = Breadcrumb(sort_page)
        self.crumbs.jumped.connect(self.jump_to)
        # Le fil d'Ariane prend ce qu'il lui faut, les reglages le reste :
        # l'inverse les comprimait dans une colonne ou ils se repliaient
        # sur quatre rangees.
        self.crumbs.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        header_row.addWidget(self.crumbs, 0)

        self.pending_label = QLabel("", sort_page)
        self.pending_label.setObjectName("pending")
        self.pending_label.hide()
        header_row.addWidget(self.pending_label)

        # Deux portees, deux boutons. Le general pioche dans toute la
        # collection ; le local, dans le seul element affiche. Ils etaient
        # confondus derriere un raccourci, et l un des deux se cherchait.
        self.scan_button = QPushButton("⟲  Analyser", sort_page)
        self.scan_button.setObjectName("scanState")
        self.scan_button.setProperty("running", "false")
        self.scan_button.setFocusPolicy(Qt.NoFocus)
        self.scan_button.clicked.connect(self.toggle_scan)

        self.random_button = QPushButton("⚄  Aléatoire", sort_page)
        self.random_button.setObjectName("random")
        self.random_button.setToolTip("Une vidéo au hasard, dans toute la "
                                      "collection   (Ctrl+H)")
        self.random_button.setFocusPolicy(Qt.NoFocus)
        self.random_button.clicked.connect(self.pick_random)

        self.random_here_button = QPushButton("⚄  Ici", sort_page)
        self.random_here_button.setObjectName("randomHere")
        self.random_here_button.setToolTip(
            "Une vidéo au hasard, dans l'élément affiché seulement")
        self.random_here_button.setFocusPolicy(Qt.NoFocus)
        self.random_here_button.clicked.connect(self.pick_random_here)

        # L'arborescence se montrait et se cachait depuis un menu : un reglage
        # qu'on bascule sans arret n'a rien a faire derriere trois clics.
        self.tree_button = QPushButton("Arborescence", sort_page)
        self.tree_button.setCheckable(True)
        self.tree_button.setToolTip("Afficher le panneau des dossiers   (Ctrl+T)")
        self.tree_button.setFocusPolicy(Qt.NoFocus)
        self.tree_button.clicked.connect(lambda checked: self.toggle_tree(checked))

        self.mute_button = QPushButton("🔇", sort_page)
        self.mute_button.setFixedWidth(42)
        self.mute_button.setFocusPolicy(Qt.NoFocus)
        self.mute_button.clicked.connect(self.toggle_mute)
        header_row.addWidget(self.mute_button)

        self.more_button = QPushButton("⋯", sort_page)
        self.more_button.setFixedWidth(42)
        self.more_button.setToolTip("Destinations, corbeille, arborescence…")
        self.more_button.setFocusPolicy(Qt.NoFocus)
        self.overflow = build_overflow(self, [
            ("Destinations…", self.edit_destinations),
            ("Mots-clés automatiques…", self.edit_tags),
            ("-", None),
            ("Corbeille de session", self.open_trash),
            ("Arborescence des destinations", self.toggle_tree),
            ("-", None),
            ("Réanalyser tout le disque", self.refresh_root),
            ("Changer de racine…", self.choose_root),
        ])
        self.more_button.setMenu(self.overflow)

        selectors = QHBoxLayout()
        selectors.setContentsMargins(0, 0, 0, 0)
        selectors.setSpacing(12)
        self.tabs = Segmented("", [
            (TAB_FOLDERS, "Dossiers", "Chaque dossier comme une carte"),
            (TAB_VIDEOS, "Vidéos", "Toutes les vidéos en vrac, au hasard"),
            (TAB_TAGS, "Mots-clés", "Les vidéos réunies par les mots de leurs noms"),
        ], sort_page)
        self.tabs.chosen.connect(self.set_tab)
        selectors.addWidget(self.tabs)

        self.tag_chips = Chips([
            ("mine", "Mes mots-clés", "Ceux que vous avez saisis"),
            ("top", "Mots fréquents", "Les mots qui reviennent le plus dans vos noms"),
        ], sort_page)
        self.tag_chips.chosen.connect(self.set_tag_family)
        self.tag_chips.hide()
        selectors.addWidget(self.tag_chips)

        self.enter_button = QPushButton("Entrer ▸", sort_page)
        self.enter_button.setToolTip("Trier le contenu de ce dossier   (Ctrl+↓)")
        self.enter_button.setFocusPolicy(Qt.NoFocus)
        self.enter_button.clicked.connect(self.enter_current)
        selectors.addWidget(self.enter_button)
        selectors.addStretch(1)
        selectors.addWidget(self.scan_button)
        selectors.addWidget(self.random_button)
        selectors.addWidget(self.random_here_button)
        selectors.addWidget(self.tree_button)
        selectors.addWidget(self.mute_button)
        selectors.addWidget(self.more_button)
        layout.addLayout(selectors)
        layout.addLayout(header_row)

        self.controls = ControlBar(COLUMN_CHOICES, sort_page)
        self.controls.changed.connect(self.on_controls_changed)
        self.controls.released.connect(self.setFocus)
        self.controls.sortChanged.connect(self.set_sort)
        self.controls.columnsChanged.connect(self.set_board_columns)
        self.controls.previousPage.connect(lambda: self.change_page(-1))
        self.controls.nextPage.connect(lambda: self.change_page(1))
        self.controls.randomHere.connect(self.pick_random_here)
        self.controls.set_terms(self.cfg["filter_include"], self.cfg["filter_exclude"])
        self.controls.set_sort(self.cfg["sort_mode"] or "random")
        self.controls.set_columns(self.cfg["board_columns"])
        header_row.addWidget(self.controls, 1)
        self.tabs.set_value(self.tab)
        self.tag_chips.set_value(self.tag_family)
        self.controls.set_browsing(self.browsing)

        self.progress = QProgressBar(sort_page)
        # Une barre muette de quatre pixels ne disait pas s'il restait dix
        # dossiers ou six cents : sur un partage reseau, l'attente se compte en
        # minutes et l'on veut savoir ou elle en est.
        self.progress.setTextVisible(True)
        self.progress.setFormat("%v / %m analysés")
        self.progress.setFixedHeight(16)
        layout.addWidget(self.progress)

        header = QFrame(sort_page)
        header.setObjectName("card")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 7, 14, 7)
        header_layout.setSpacing(14)
        self.item_title = QLabel("—", header)
        self.item_title.setObjectName("title")
        self.item_parent = QLabel("", header)
        self.item_parent.setObjectName("parentPath")
        self.item_subtitle = QLabel("", header)
        self.item_subtitle.setObjectName("subtitle")
        # Le chemin est deja dans le fil d'Ariane : le repeter sur sa propre
        # ligne prenait de la hauteur pour rien. Il reste en infobulle.
        self.item_parent.hide()
        for label in (self.item_title, self.item_subtitle):
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header_layout.addWidget(self.item_title, 0)
        header_layout.addWidget(self.item_subtitle, 1)
        # En planche, ce bloc repetait le fil d'Ariane et une phrase d'aide, sur
        # trois lignes, au detriment d'une rangee entiere de vignettes.
        self.item_card = header
        layout.addWidget(header)

        self.banner = QLabel("", sort_page)
        self.banner.setObjectName("statusBanner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        layout.addWidget(self.banner)

        # Le panneau d'arborescence occupe la gauche, le lecteur le reste.
        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(10)

        self.tree = TreePanel(sort_page)
        self.tree.folderChosen.connect(self.on_tree_folder)
        self.tree.rootChanged.connect(self.on_tree_root_changed)
        self.tree.actionChanged.connect(self.on_tree_action)
        self.tree.set_action("go" if self.tab == TAB_VIDEOS else "send")
        self.tree.hide()
        middle.addWidget(self.tree)

        self.viewer = QStackedWidget(sort_page)
        self.grid = PreviewGrid(
            self.cfg["thumb_count"], self.cfg["preview_seconds"],
            self.cfg["scroll_seconds"], self.viewer,
        )
        self.grid.openRequested.connect(self.open_external)
        self.grid.playRequested.connect(self.play_in_app)
        self.single = SinglePlayer(
            self.cfg["thumb_count"], self.cfg["scroll_seconds"], self.viewer
        )
        self.board = BoardView(
            self.cfg["preview_seconds"], self.cfg["board_columns"], self.viewer
        )
        self.board.openRequested.connect(self.on_board_open)
        self.board.rateRequested.connect(self.on_board_rate)
        self.board.previewNeeded.connect(self.on_board_preview)
        self.board.playRequested.connect(self.play_in_app)
        self.board.pageChanged.connect(self.on_board_page)
        self.viewer.addWidget(self.grid)
        self.viewer.addWidget(self.single)
        self.viewer.addWidget(self.board)
        middle.addWidget(self.viewer, 1)
        layout.addLayout(middle, 1)

        self.stars = StarStrip(22, sort_page)
        self.stars.rated.connect(self.rate_current)

        self.commands = CommandBar(sort_page)
        # Les memes actions qu'au clavier, accessibles a la souris.
        self.commands.deleteRequested.connect(self.on_command_delete)
        self.commands.skipRequested.connect(self.on_command_skip)
        self.commands.moveRequested.connect(self.on_command_move)
        self.commands.rateRequested.connect(self.rate_current)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addWidget(self.commands, 1)
        bottom.addWidget(self.stars, 0, Qt.AlignBottom)
        layout.addLayout(bottom)

        # Ce pense-bete etait une seule etiquette de cinq mille pixels de large.
        # Qt en faisait la largeur minimale de la fenetre entiere : elle ne
        # pouvait plus retrecir, et tout le reste debordait de l'ecran. Il se
        # consulte desormais sous le bouton « ⋯ », ou il ne coute rien.
        self.more_button.setToolTip(
            "←/→ naviguer   ·   molette avancer/reculer   ·   Ctrl+Z annuler\n"
            "Ctrl+F filtrer   ·   Ctrl+T arborescence   ·   Ctrl+M son\n"
            "Ctrl+O ouvrir   ·   Ctrl+D destinations   ·   Entrée pause\n"
            "Ctrl+←/→ page d'aperçus   ·   Ctrl+↓ entrer dans le dossier\n"
            "Ctrl+P planche   ·   Ctrl+H au hasard   ·   0…5 noter\n"
            "Ctrl+molette zoomer   ·   Ctrl+B corbeille   ·   Échap remonter"
        )

        self.stack.addWidget(sort_page)

        self.done_page = DonePage(self)
        self.done_page.rescan.clicked.connect(self.refresh_root)
        self.done_page.change.clicked.connect(self.choose_root)
        self.stack.addWidget(self.done_page)

        self.banner_timer = QTimer(self)
        self.banner_timer.setSingleShot(True)
        self.banner_timer.timeout.connect(self.banner.hide)

        # La planche se rebatit au plus une fois par seconde : pendant une
        # relecture qui corrige cinquante dossiers, la refaire a chaque paquet
        # la ferait clignoter sans rien apprendre a personne.
        self._board_dirty = False
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
            self.start_root(Path(chosen))

    def start_root(self, root: Path | None, mode: str = "",
                   reset_levels: bool = True, restore_id: str = "",
                   force: bool = False) -> None:
        if root is None or not Path(root).is_dir():
            QMessageBox.warning(self, "Dossier introuvable", f"{root} n'existe plus.")
            return
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
        if not known and self.mode == MODE_FOLDERS and not list_entries(
                self.root, MODE_FOLDERS, self.cfg["skip_hidden"], False):
            # Racine inconnue et sans sous-dossier : on bascule sur les videos
            # plutot que de presenter une liste vide sans explication. Sans
            # traverser les dossiers de tete — la question posee est « y a-t-il
            # quelque chose ici », et la traversee, qui lit tout le reseau, ne
            # la change pas.
            self.mode = MODE_FLAT
            self.content = CONTENT_VIDEOS
            known = (cached_items(self.root, self.mode, self.cfg["expand_parents"])
                 if indexed else [])

        self.preview.tune_for(self.root)
        self.trash.set_base(self.levels[0]["root"] if self.levels else self.root)
        self.cfg.push_recent_root(str(self.root))
        self.cfg.save()
        self.welcome.set_recent(self.cfg["recent_roots"])

        top = Path(self.levels[0]["root"]) if self.levels else self.root
        self.crumbs.set_path(top, self.root)
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
        self.progress.setRange(0, 0)
        self.progress.setVisible(not self.items)

        if INDEX.rebuilt and not getattr(self, "_told_rebuild", False):
            # Sans ce mot, un index abime se traduisait par « c'est lent », sans
            # que rien ne dise que la memoire venait d'etre remise a zero.
            self._told_rebuild = True
            self.show_banner(
                "L'index etait abime : il a ete refait. Cette analyse-ci sera "
                "complete, les suivantes seront immediates.", "#3a3322")

        self.scanning = True
        self._scan_started = time.monotonic()
        self._scan_done, self._scan_total, self._scan_name = 0, 0, ""
        self._refresh_scan_button()
        self.scan_thread = RefreshThread(
            self.root, self.mode, self.cfg["skip_hidden"],
            self.cfg["use_scan_cache"], self.cfg["expand_parents"],
            [item.item_id for item in self.all_items], force, self,
        )
        self.scan_thread.progress.connect(self.on_scan_progress)
        self.scan_thread.patch.connect(self.on_patch)
        self.scan_thread.finished_scan.connect(self.on_scan_finished)
        # Sous la priorite normale : la reconciliation a tout son temps, les
        # vignettes de ce qu'on regarde, non.
        self.scan_thread.start(RefreshThread.LowPriority)

    def _show_known(self, known: list, restore_id: str = "") -> None:
        """Affiche d'emblee ce que l'index savait de cette racine."""
        self.all_items = known
        self.items = [item for item in known if self._matches(item)]
        self.apply_sort()
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
        for signal in (thread.progress, thread.patch, thread.finished_scan):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        thread.stop()
        if not thread.wait(400):
            # Il se terminera de lui-meme ; on le garde en vie le temps qu'il le
            # fasse, sans quoi Qt detruirait un QThread encore en marche.
            self._dying = [t for t in getattr(self, "_dying", []) if t.isRunning()]
            self._dying.append(thread)
        self._scan_started = 0.0
        self._refresh_scan_button()

    # ------------------------------------------------------------------
    # Corbeille de session
    # ------------------------------------------------------------------
    def open_trash(self) -> None:
        """Liste ce qui a été écarté, avec de quoi le remettre en place."""
        if not self.trash.count:
            self.show_banner("La corbeille de session est vide", "#2a2f38")
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
                if Path(item.path).exists():
                    self.stats["deleted"] = max(0, self.stats["deleted"] - 1)
                    yield item.item_id, item

    def _flush_trash_on_close(self) -> None:
        """Envoie le contenu de la corbeille de session vers celle de Windows.

        Sans rien demander : la corbeille de Windows rend l'opération réversible
        depuis l'explorateur, et une question posée à chaque fermeture finirait
        par être approuvée sans être lue.
        """
        _done, problem = self.trash.flush(self.cfg["delete_mode"])
        if problem:
            QMessageBox.warning(
                self, "Corbeille incomplète",
                "Certains éléments écartés n'ont pas pu rejoindre la corbeille de "
                f"Windows. Ils restent dans « {self.trash.FOLDER_NAME} ».\n\n"
                f"Dernière erreur : {problem}",
            )

    def refresh_root(self) -> None:
        """Relit tout le disque, sans se fier a l'analyse precedente."""
        if self.root is None:
            return
        self.show_banner("Réanalyse complète en cours…", "#22303f")
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
        if not target.is_dir():
            self.show_banner(f"Introuvable : {item.name}", "#3a2226")
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
            self.show_banner("Rien avant cet endroit", "#2a2f38")
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
        self.items = [entry for entry in self.all_items if self._matches(entry)]
        self.apply_sort()
        self.mode = MODE_FLAT
        self.content = CONTENT_VIDEOS
        self.index = 0
        self._apply_selectors()
        self.crumbs.set_path(
            Path(self.levels[0]["root"]) if self.levels else self.root, self.root
        )
        self.show_banner(
            f"{len(self.items)} vidéo(s) portant « {item.path.name} »", "#22303f"
        )
        if self.browsing:
            self.refresh_board()
        elif self.items:
            self.show_item(0)

    def go_up(self) -> bool:
        """Remonte d'un niveau, en retrouvant le dossier d'ou l'on etait parti."""
        if not self.levels:
            return False
        level = self.levels.pop()
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

    def _refresh_scan_button(self) -> None:
        """Dit sans ambiguite si une analyse tourne, et ou elle en est.

        C'etait la vraie plainte : on ne savait pas distinguer une application
        lente d'une analyse en cours. Le bouton porte donc l'etat, et le nom du
        dossier en cours montre que quelque chose avance meme quand le chiffre
        met du temps a changer.
        """
        if not hasattr(self, "scan_button"):
            return
        if not self.scanning:
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
                             "#3a3322")
            self.update_counter()
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
        if removed:
            self._drop_items(removed)
        if replaced:
            self._replace_items(replaced)
        for item in added:
            self.all_items.append(item)
            if self._matches(item):
                self._accept_item(item)
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
        # Les dossiers virtuels ne debordent plus sur la liste des dossiers
        # reels : ils ont leur onglet, c'est la qu'on les cherche.
        if self.tab != TAB_TAGS:
            if any(i.is_tag for i in self.all_items):
                self.all_items = [i for i in self.all_items if not i.is_tag]
                if not self.all_items:
                    self.all_items = list(getattr(self, "_plain_items", []))
                self.items = [i for i in self.all_items if self._matches(i)]
                self.apply_sort()
            return
        plain = [i for i in self.all_items if not i.is_tag]
        if plain:
            # L'onglet des mots-cles remplace la liste par les dossiers
            # virtuels : on garde de cote celle des dossiers reels, qui reste
            # la seule source des videos a regrouper.
            self._plain_items = plain
        else:
            plain = getattr(self, "_plain_items", [])
        videos = [video for item in plain for video in item.videos]
        # Deux familles : les mots qu'on a saisis, et ceux que les noms de
        # fichiers repetent d'eux-memes. Les seconds ne demandent aucune saisie
        # et decrivent souvent mieux la collection que ce qu'on aurait pense.
        words = self.tags if self.tag_family == "mine" else top_words(videos)
        if not words:
            self.all_items = [i for i in self.all_items if not i.is_tag]
            self.items = [i for i in self.all_items if self._matches(i)]
            return
        found = build_tag_items(words, videos)
        if not found:
            return
        # Dans son onglet, un mot-cle n'est pas un en-tete pose sur la liste des
        # dossiers : c'est toute la liste.
        self.all_items = found if self.tab == TAB_TAGS else found + plain
        self.items = [item for item in self.all_items if self._matches(item)]
        self.apply_sort()
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
        if mode == MODE_FOLDERS:
            self._plain_items = [i for i in self.all_items if not i.is_tag]
            self._plain_root = self.root
        self._add_tag_items()
        self._show_counts()
        thread = self.scan_thread
        if thread is not None:
            # Une analyse qui se termine doit le dire, meme quand elle n'a rien
            # trouve a changer : sans quoi on ne sait pas si elle tourne encore.
            self.show_banner(
                f"✓ Analyse terminée en {elapsed:.0f} s — {total} élément(s), "
                f"{thread.rescanned} mis à jour, {thread.reused} inchangé(s).",
                "#1f3326" if not thread.rescanned else "#22303f",
            )
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(total)
        if not self.items:
            self.item_title.setText("Rien à trier")
            self.item_subtitle.setText(
                "Aucun sous-dossier trouvé." if mode == MODE_FOLDERS
                else "Aucune vidéo trouvée directement dans ce dossier."
            )
            self.grid.set_no_videos("—")
        else:
            self.update_counter()

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
        top = Path(self.levels[0]["root"] if self.levels else (self.root or item.path))
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
            self.item_title.setText(f"📁  {item.name}")
            parts = [
                human_size(item.size),
                f"{item.file_count} fichier{'s' if item.file_count > 1 else ''}",
                f"{item.video_count} vidéo{'s' if item.video_count > 1 else ''}",
            ]
            if item.subdir_count:
                parts.append(f"{item.subdir_count} sous-dossier{'s' if item.subdir_count > 1 else ''}")
        else:
            info = item.info or INDEX.probe(item.path) or {}
            item.info = info
            # La durée rejoint le titre : c'est ce qu'on veut savoir en premier
            # d'une vidéo, et la ligne d'informations est déjà chargée.
            duration = human_duration(info["duration"]) if info.get("duration") else ""
            self.item_title.setText(
                f"🎬  {item.name}   —   {duration}" if duration
                else f"🎬  {item.name}"
            )
            parts = []
            if info.get("height"):
                parts.append(human_resolution(info["height"]))
            if info.get("width"):
                parts.append(f"{info['width']}×{info['height']}")
            parts.append(human_size(item.size))
            if info.get("codec"):
                parts.append(info["codec"])
        if item.mtime:
            parts.append("modifié le " + datetime.fromtimestamp(item.mtime).strftime("%d/%m/%Y"))
        self.item_subtitle.setText("   ·   ".join(parts))

    def show_item(self, index: int) -> None:
        if not self.items:
            return
        self.index = max(0, min(index, len(self.items) - 1))
        item = self.items[self.index]
        self.update_counter()
        self.commands.rebuild(
            self.cfg.destinations, DELETE_LABELS.get(self.cfg["delete_mode"], "Supprimer")
        )

        self._describe(item)

        self.stars.show()
        self.stars.set_value(self.ratings.get(item.path))
        crumbs = self._breadcrumb(item)
        self.item_parent.setText(crumbs)
        self.item_parent.setToolTip(str(Path(item.path).parent))

        if item.pending:
            self.show_banner(f"Transfert en cours vers {item.status_detail}…", "#2a3340")
        elif item.processed:
            tone = "#22331f" if item.status == "moved" else "#3a2226"
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

        self._sort_videos(item)
        self._request_previews(item, current=True)
        self._update_page_bar()
        keep = {self._plan_key(item, self.page_of(item))}
        for offset in (1, 2):
            if self.index + offset < len(self.items):
                nxt = self.items[self.index + offset]
                keep.add(self._plan_key(nxt, self.page_of(nxt)))
                self._request_previews(nxt, current=False)
        self.preview.cancel_except(keep)

    # ------------------------------------------------------------------
    # Apercus, par pages de `thumb_count`
    # ------------------------------------------------------------------
    def page_of(self, item) -> int:
        return self.pages.get(item.item_id, 0)

    def total_pages(self, item) -> int:
        if item.kind != MODE_FOLDERS:
            return 1
        return page_count(item.videos, self.cfg["thumb_count"])

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
        key = self._plan_key(item, page)
        plan = self.plans.get(key)
        if plan is None:
            self.preview.request_plan(
                key, item.videos, self.cfg["thumb_count"],
                page=page, one_per_video=item.kind == MODE_FOLDERS,
                urgent=current,
            )
            return
        if current:
            self._apply_plan(item, plan)
        for slot, entry in enumerate(plan):
            self.preview.request_thumb(key, slot, entry[0], entry[1], urgent=current)

    def _apply_plan(self, item, plan: list) -> None:
        viewer = self.grid if item.kind == MODE_FOLDERS else self.single
        viewer.set_plan(plan)

    def _current_key(self) -> str:
        current = self.current
        return self._plan_key(current, self.page_of(current)) if current else ""

    def on_plan_ready(self, key: str, plan: list) -> None:
        self.plans[key] = plan
        if key.startswith("board@"):
            position = self._board_position_of(key)
            if position >= 0 and plan:
                self.board.set_source(position, plan[0])
                self.preview.request_thumb(key, 0, plan[0][0], plan[0][1])
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
        for slot, entry in enumerate(plan):
            self.preview.request_thumb(key, slot, entry[0], entry[1], urgent)

    def on_thumb_ready(self, key: str, slot: int, path: str) -> None:
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
        if key.startswith("board@"):
            return
        current = self.current
        if current is None or key != self._current_key():
            return
        viewer = self.grid if current.kind == MODE_FOLDERS else self.single
        viewer.set_failed(slot)

    def on_board_page(self, first: int, last: int, total: int) -> None:
        if self.browsing:
            self._show_counts()

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
            self.items.sort(key=lambda i: i.name.lower())

    def _sort_videos(self, item) -> None:
        """Reclasse les vidéos d'un dossier selon le mode choisi.

        Le tri s'appuie sur les durées déjà sondées ; celles qui ne le sont pas
        encore comptent pour zéro et remonteront au prochain affichage.
        """
        if item.kind != MODE_FOLDERS or not item.videos:
            return
        if not self.sort_mode:
            item.videos.sort(key=lambda path: str(path).lower())
            return
        def duration_of(path):
            return (INDEX.probe(path) or {}).get("duration", 0.0)
        item.videos.sort(key=duration_of, reverse=self.sort_mode == "desc")

    def _show_counts(self) -> None:
        """Une seule ligne dit ce qui est montre, ce qui est masque, et ou l'on en est."""
        hidden = len(self.all_items) - len(self.items)
        if self.browsing:
            first, last = self.board._page_bounds()
            shown = f"{first + 1 if self.items else 0}–{last} sur {len(self.items)}"
            self.controls.set_page(
                shown + (f"  ·  {hidden} filtrés" if hidden else ""),
                self.board.page > 0,
                self.board.page < self.board.total_pages() - 1,
            )
            return
        total = len(self.items)
        done = self.stats["moved"] + self.stats["deleted"]
        self.controls.set_page(
            f"{min(self.index + 1, total)} / {total}"
            + (f"  ·  {hidden} filtrés" if hidden else "")
            + (f"  ·  {done} traités" if done else ""),
            False, False,
        )

    def _update_page_bar(self) -> None:
        self._show_counts()

    def show_banner(self, text: str, color: str = "#22303f") -> None:
        self.banner.setText(text)
        self.banner.setStyleSheet(f"background: {color}; color: #e9eef4;")
        self.banner.show()
        self.banner_timer.start(4000)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _release_media(self) -> None:
        """Relâche tous les handles sur les fichiers avant une opération disque.

        Deux sources de verrous sous Windows : le lecteur Qt, et les ffmpeg de
        préchargement qui fabriquent les vignettes des éléments suivants.
        """
        for player in (self.grid.player, self.single.player):
            player.stop()
            player.setSource(QUrl())
        self.grid.video.hide()
        self.preview.quiesce(1200)
        QApplication.processEvents()

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
        for item in self.all_items:
            if item.is_tag:
                continue
            for video in item.videos:
                key = str(video)
                if key in seen:
                    continue
                seen.add(key)
                found.append(video)
        return found

    def restore_folders(self) -> bool:
        """Remet la liste des dossiers déjà analysée, sans rien relire.

        Sans cela, chaque aller-retour entre les onglets relançait l'analyse
        complète de la racine — plusieurs minutes sur un partage réseau, pour
        retrouver exactement ce qu'on venait de quitter.
        """
        if not self._plain_items or self._plain_root != self.root:
            return False
        self.all_items = list(self._plain_items)
        self.items = [i for i in self.all_items if self._matches(i)]
        self.mode = MODE_FOLDERS
        self.apply_sort()
        self.index = 0
        self._show_counts()
        return True

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
        if not videos:
            self.start_root(self.root, MODE_FLAT, reset_levels=False)
            return
        self.all_items = [Item(path=Path(video), kind=MODE_FILES, videos=[video],
                               video_count=1, file_count=1) for video in videos]
        self.items = [i for i in self.all_items if self._matches(i)]
        self.mode = MODE_FLAT
        self.apply_sort()
        self.index = 0
        self._show_counts()

    def set_tab(self, tab: str, reposition: bool = True) -> None:
        """Change de point de vue sans changer de collection.

        Les trois onglets ne sont pas trois applications : ouvrir une vidéo,
        d'où qu'elle vienne, mène toujours à la même fiche, avec ses
        destinations et sa note. Seule la façon de présenter l'ensemble change.
        """
        if tab not in TABS:
            return
        # Recliquer l'onglet ou l'on est ramene chez soi : a la racine, sur les
        # vignettes. C'est le geste qu'on fait quand on s'est perdu en
        # descendant, et il ne faisait rien.
        if (tab == self.tab and self.browsing and not self.levels):
            return
        self.tab = tab
        self.content = (CONTENT_VIDEOS if tab == TAB_VIDEOS
                        else CONTENT_FOLDERS)
        # Changer de collection ramene aux vignettes.
        self.browsing = True
        self.cfg["tab"] = tab
        self.cfg["content"] = self.content
        self.cfg["view"] = self.view
        self.cfg.save()
        self._release_media()
        self._apply_selectors()

        if self.root is None:
            return

        # L arborescence prend le geste du contexte : en edition on range, et
        # c est donc « envoyer vers » ; en videos on se promene, et c est
        # « aller dans ». Se tromper de geste deplace des fichiers.
        self.tree.set_action("go" if tab == TAB_VIDEOS else "send")
        self.cfg["tree_action"] = self.tree.action

        # Un onglet est un point de vue sur **toute** la collection, pas sur le
        # sous-dossier ou l on se trouvait. Rester en place donnait un onglet
        # « Dossiers » qui montrait trois sous-dossiers au lieu de la racine.
        top = Path(self.levels[0]["root"]) if self.levels else self.root
        mode = MODE_FLAT if tab == TAB_VIDEOS else MODE_FOLDERS
        if tab == TAB_VIDEOS:
            # Sans quoi les memes vidéos reviennent toujours en tete.
            self.sort_mode = "random"
            self.cfg["sort_mode"] = "random"
            self.controls.set_sort("random")
            self.stop_scan()
            self.progress.hide()
            self.levels = []
            self.root = top
            self.crumbs.set_path(top, top)
            self.show_videos_tab()
            if self.browsing:
                self.refresh_board()
            elif self.items:
                self.show_item(0)
            self.setFocus()
            return
        if top != self.root or self.mode != mode or tab == TAB_TAGS:
            self.levels = []
            self.start_root(top, mode, reset_levels=True)
            return

        if self.browsing:
            self.refresh_board()
        elif self.items:
            self.show_item(self.index)
        self.setFocus()

    def set_tag_family(self, family: str) -> None:
        """Mes propres mots-clés, ou ceux que les noms de fichiers répètent."""
        if family == self.tag_family:
            return
        self.tag_family = family
        self.cfg["tag_family"] = family
        self.cfg.save()
        self.tag_chips.set_value(family)
        if not self.scanning:
            self._add_tag_items()

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
        self.tag_chips.setVisible(self.tab == TAB_TAGS)
        self.controls.set_browsing(self.browsing)
        # La fiche ne dit rien qu'on ne lise deja ailleurs quand on parcourt.
        self.item_card.setVisible(not self.browsing)
        item = self.current
        self.enter_button.setVisible(
            not self.browsing and item is not None
            and item.kind == MODE_FOLDERS and not item.locked
        )
        self.stars.setVisible(not self.browsing)
        self.random_here_button.setVisible(
            item is not None and bool(item.videos) and not self.browsing)

    def jump_to(self, path: str) -> None:
        """Le fil d'Ariane ramène directement au dossier cliqué."""
        target = Path(path)
        if self.root is not None and target == self.root:
            return
        while self.levels and Path(self.levels[-1]["root"]) != target:
            self.levels.pop()
        if self.levels and Path(self.levels[-1]["root"]) == target:
            self.levels.pop()
        self.start_root(target, self.mode_for_content(), reset_levels=False)

    def toggle_board(self, visible: bool | None = None,
                     reposition: bool = True) -> None:
        target = (not self.browsing) if visible is None else visible
        self.set_view(VIEW_BROWSE if target else VIEW_EDIT, reposition)

    def refresh_board(self) -> None:
        if not self.browsing:
            return
        self.viewer.setCurrentWidget(self.board)
        self.board.set_muted(self.cfg["muted"])
        self.board.set_items(self.items, self.ratings.get)
        self.item_title.setText(
            f"{len(self.items)} élément(s)" if self.items else "Rien à afficher"
        )
        self.item_parent.setText(str(self.root) if self.root else "")
        self.item_title.setToolTip(str(self.root) if self.root else "")
        self.item_subtitle.setText(
            "Survolez une carte pour la lire, cliquez pour l'ouvrir, "
            "notez d'un clic sur les étoiles."
        )
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
                one_per_video=item.kind == MODE_FOLDERS,
            )
            return
        self.board.set_source(position, plan[0])
        self.preview.request_thumb(key, 0, plan[0][0], plan[0][1])

    def _board_position_of(self, key: str) -> int:
        item_id = key[len("board@"):]
        for position, item in enumerate(self.items):
            if item.item_id == item_id:
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
        self._release_media()
        self.refresh_board()
        if 0 <= position < len(self.items):
            self.board.scroll_to(position)
        self._apply_selectors()
        self.setFocus()

    def on_board_rate(self, position: int, stars: int) -> None:
        if 0 <= position < len(self.items):
            value = self.ratings.set(self.items[position].path, stars)
            self.board.set_stars(position, value)
            self.ratings.flush()

    def rate_current(self, stars: int) -> None:
        item = self.current
        if item is None:
            return
        value = self.ratings.set(item.path, stars)
        self.stars.set_value(value)
        self.ratings.flush()
        self.show_banner(
            f"« {item.name} » : {value} étoile(s)" if value
            else f"« {item.name} » : note effacée", "#2a2f38",
        )

    def pick_random_here(self) -> None:
        """Tire au hasard parmi les vidéos du seul élément affiché."""
        import random
        item = self.current
        pool = [str(video) for video in (item.videos if item else [])]
        if not pool:
            self.show_banner("Aucune vidéo ici", "#2a2f38")
            return
        video = random.choice(pool)
        self.show_banner(
            f"Au hasard dans « {item.name} » : {Path(video).name}", "#22303f"
        )
        self.play_in_app(video)

    def pick_random(self) -> None:
        """Lance une vidéo au hasard, piochée dans tout ce que l'analyse connaît.

        Tirer parmi les seuls éléments affichés ramenait toujours les mêmes :
        en mode dossier, la liste ne compte que quelques dizaines d'entrées.
        """
        import random
        pool = []
        for item in self.all_items:
            if item.locked:
                continue
            pool.extend(str(video) for video in item.videos)
        if not pool:
            self.show_banner("Aucune vidéo à tirer au sort", "#2a2f38")
            return
        video = random.choice(pool)
        self.show_banner(
            f"Au hasard parmi {len(pool)} vidéos : « {Path(video).name} »", "#22303f"
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
        self.cfg["tree_root"] = path
        self.cfg.save()

    # ------------------------------------------------------------------
    # Filtre par nom
    # ------------------------------------------------------------------
    @staticmethod
    def _terms(text: str) -> list:
        return [term.strip().lower() for term in text.split(",") if term.strip()]

    def _matches(self, item) -> bool:
        name = item.name.lower()
        rules = self.criteria or {}
        include = self._terms(rules.get("include", self.cfg["filter_include"]))
        exclude = self._terms(rules.get("exclude", self.cfg["filter_exclude"]))
        if include and not any(term in name for term in include):
            return False
        if any(term in name for term in exclude):
            return False
        return self._matches_numeric(item)

    def _matches_numeric(self, item) -> bool:
        """Durée, résolution et note. Ce qu'on ignore encore passe le filtre."""
        rules = self.criteria
        if not rules:
            return True

        stars_min = rules.get("stars", -1)
        if stars_min >= 0 and self.ratings.get(item.path) < stars_min:
            return False

        needs_media = rules.get("duration_op") or rules.get("resolution", 0) > 0
        if not needs_media:
            return True
        duration, height = known_media(item)

        op = rules.get("duration_op")
        if op and duration > 0:
            wanted = rules.get("duration_s", 0)
            if op == "gt" and duration <= wanted:
                return False
            if op == "lt" and duration >= wanted:
                return False

        wanted_height = rules.get("resolution", 0)
        if wanted_height and height > 0:
            if rules.get("resolution_op") == "gte" and height < wanted_height:
                return False
            if rules.get("resolution_op") == "lte" and height > wanted_height:
                return False
        return True

    def on_controls_changed(self) -> None:
        """Un reglage a bouge : on refiltre, puis on reclasse."""
        rules = self.controls.criteria()
        self.criteria = rules
        self.cfg["filter_include"] = rules["include"]
        self.cfg["filter_exclude"] = rules["exclude"]
        self.cfg.save()
        self.apply_filter(rules["include"], rules["exclude"])

    def apply_filter(self, include: str, exclude: str) -> None:
        self.cfg["filter_include"] = include
        self.cfg["filter_exclude"] = exclude
        # Les criteres sont la seule source consultee par _matches : les laisser
        # de cote ferait ignorer silencieusement les termes qu'on vient de poser.
        self.criteria = dict(self.criteria or {})
        self.criteria["include"] = include
        self.criteria["exclude"] = exclude
        self.cfg.save()

        current = self.current
        self.items = [item for item in self.all_items if self._matches(item)]
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

        # On reste sur le même élément s'il passe encore le filtre.
        if current is not None and current in self.items:
            self.index = self.items.index(current)
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
                "#3a2226",
            )
            return
        if not Path(item.path).exists():
            self.show_banner(f"Introuvable : {item.name}", "#3a2226")
            return self.advance()
        self._release_media()
        item.status = "pending_delete"
        item.status_detail = "Corbeille"
        self._enqueue(
            Transfer(kind="move", purpose="delete", src=item.path,
                     dest=self.trash.folder_for(item.path),
                     label="Corbeille", item_id=item.item_id),
            f"« {item.name} » → corbeille  ·  Ctrl+Z ou Ctrl+B pour la rouvrir",
            "#3a2226",
        )

    def act_move(self, dest: dict) -> None:
        item = self.current
        if item is None or item.locked:
            return self.advance()
        dest_dir = Path(dest["path"])
        label = dest.get("label") or dest_dir.name
        problem = self._move_objection(item, dest_dir)
        if problem:
            self.show_banner(problem, "#3a2226")
            return
        self._release_media()
        item.status = "pending_move"
        item.status_detail = label
        self._enqueue(
            Transfer(kind="move", src=item.path, dest=dest_dir,
                     label=label, item_id=item.item_id),
            f"« {item.name} » → {label}", "#22331f",
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
        if not Path(item.path).exists():
            return f"Introuvable : {item.name}"
        try:
            if Path(item.path).samefile(dest_dir):
                return "C'est deja ce dossier."
        except OSError:
            pass
        if Path(item.path).is_dir():
            try:
                dest_dir.resolve().relative_to(Path(item.path).resolve())
                return "Impossible : la destination est dans le dossier a deplacer."
            except ValueError:
                pass
        return ""

    def act_skip(self) -> None:
        item = self.current
        if item is not None and not item.processed and item.status != "skipped":
            item.status = "skipped"
            self.stats["skipped"] += 1
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
            self.show_banner("Rien à annuler", "#2a2f38")
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
        self.show_banner(f"Restauration de « {Path(entry.src).name} »…", "#22303f")

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
            self.show_banner(f"Échec sur « {job.name} » : {job.error}", "#4a1f24")
            self.update_counter()
            return

        if job.kind == "move" and job.purpose == "delete":
            if item is not None:
                item.status = "deleted"
            self.stats["deleted"] += 1
            size = item.size if item is not None else 0
            self.trash.record(Path(job.src), job.result, size)
            self.ratings.rename(job.src, job.result)
            self.history.append(
                HistoryEntry("delete", Path(job.src), job.result, job.label, True)
            )
        elif job.kind == "move":
            if item is not None:
                item.status = "moved"
            self.stats["moved"] += 1
            self.ratings.rename(job.src, job.result)
            self.history.append(
                HistoryEntry("move", Path(job.src), job.result, job.label, True)
            )
        elif job.kind == "delete":
            if item is not None:
                item.status = "deleted"
            self.stats["deleted"] += 1
            self.history.append(
                HistoryEntry("delete", Path(job.src), job.result, job.label, job.reversible)
            )
        else:  # annulation
            entry = job.entry
            if getattr(entry, "action", "") == "delete" and entry.dst:
                self.trash.forget(Path(entry.dst))
            if item is not None:
                item.status = ""
                item.status_detail = ""
            key = "deleted" if getattr(entry, "action", "") == "delete" else "moved"
            self.stats[key] = max(0, self.stats[key] - 1)
            self.show_banner(
                f"Annulé : « {Path(entry.src).name} » est revenu à sa place", "#22303f"
            )
            if item is not None and self.current is item:
                self.show_item(self.index)

        self.update_counter()
        if self.browsing and item is not None:
            for position, listed in enumerate(self.items):
                if listed is item:
                    self.board.set_state(position, item.status)
                    break

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
        self.mute_button.setText("🔇" if muted else "🔊")
        self.mute_button.setToolTip(
            "Son coupé — cliquer pour l'activer   (Ctrl+M)" if muted
            else "Son actif — cliquer pour le couper   (Ctrl+M)"
        )

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
        self.show_banner("Survolez une vidéo, ou double-cliquez dessus", "#2a2f38")

    def play_in_app(self, path: str, start_s: float = 0.0) -> None:
        """Ouvre la fiche de cette vidéo : son dossier, en mode fichier, sur elle.

        Un lecteur séparé demandait ses propres commandes et sa propre fenêtre
        pour refaire ce que la fiche fait déjà. Aller à la fiche garde un seul
        endroit où l'on regarde, avec le tri et les destinations sous la main.
        """
        video = Path(path)
        if not path or not video.exists():
            self.show_banner("Vidéo introuvable", "#3a2226")
            return
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
        """Saisit les mots-clés qui deviendront des dossiers virtuels."""
        dialog = TagsDialog(self.tags, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.tags = dialog.result_tags()
            self.cfg["tags"] = self.tags
            self.cfg.save()
            if self.root is not None:
                self.start_root(self.root, reset_levels=False)
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
        if self.stack.currentIndex() != PAGE_SORT:
            return super().keyPressEvent(event)

        # Les commandes de l'application sont toutes sur Ctrl ou sur une touche
        # de navigation : chiffres et lettres restent libres pour les destinations.
        if ctrl:
            if key == Qt.Key_Z:
                return self.act_undo()
            if key == Qt.Key_T:
                return self.toggle_tree()
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

        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            return self.act_delete()
        if key == Qt.Key_Space:
            return self.act_skip()
        if key == Qt.Key_Right:
            return self.show_item(self.index + 1)
        if key == Qt.Key_Left:
            return self.show_item(self.index - 1)
        if key == Qt.Key_Escape:
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
        self._refresh_mute()
        self.show_banner("Son coupé" if muted else "Son activé", "#2a2f38")

    def closeEvent(self, event):
        self.stop_scan()
        self._release_media()
        # Un transfert interrompu laisserait un dossier à moitié copié : on
        # attend, en le disant, plutôt que de couper net.
        if self.transfers.busy:
            waiter = QProgressDialog(
                f"{self.transfers.active} transfert(s) en cours — "
                "fermeture dès qu'ils sont terminés.",
                "", 0, 0, self,
            )
            waiter.setWindowTitle("VideoSorter")
            waiter.setCancelButton(None)
            waiter.setMinimumDuration(0)
            waiter.show()
            while self.transfers.busy:
                QApplication.processEvents()
                self.transfers.wait(200)
            waiter.close()
        self._flush_trash_on_close()
        self.preview.shutdown()
        self.ratings.flush()
        # Tout ce que la relecture a appris est deja ecrit : il ne reste qu'a
        # refermer. C'est l'inverse de l'ancien cache, qui n'ecrivait qu'a la
        # fin d'une analyse complete et perdait tout des qu'on fermait avant.
        # Une relecture abandonnee ecrit encore : fermer la connexion sous elle
        # laissait un fichier a moitie ecrit, que le lancement suivant trouvait
        # illisible — et l'on reanalysait tout, chaque fois, sans le savoir.
        for thread in getattr(self, "_dying", []):
            thread.stop()
            thread.wait(4000)
        self._dying = []
        INDEX.prune()
        INDEX.close()
        self.cfg["window"] = {"w": self.width(), "h": self.height()}
        self.cfg.save()
        super().closeEvent(event)


def check_tools(parent=None) -> bool:
    if Tools.ffmpeg and Tools.ffprobe:
        return True
    QMessageBox.critical(
        parent, "ffmpeg introuvable",
        "VideoSorter a besoin de ffmpeg et ffprobe pour fabriquer les aperçus.\n\n"
        "Installez-les (winget install Gyan.FFmpeg) ou renseignez leur chemin "
        "dans le fichier de configuration.",
    )
    return False
