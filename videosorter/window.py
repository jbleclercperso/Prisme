"""Fenêtre principale : enchaînement des éléments et exécution des actions."""
from __future__ import annotations

import gc
import json
import operator
import os
import stat
import subprocess
import threading
import time
import zlib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import shiboken6
from PySide6.QtGui import QAction, QCursor, QIcon, QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtCore import (
    QEvent, QEventLoop, QObject, QPoint, QRect, QSize, QThread, QTimer, QUrl, Qt, Signal,
)
from PySide6.QtWidgets import (
    QSplitter,
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMenu, QMessageBox, QProgressBar, QProgressDialog,
    QPushButton, QLayout, QSizePolicy, QStackedWidget, QStyle, QToolButton, QToolTip,
    QVBoxLayout, QWidget,
)

from .floatguard import GuardedLabel
from . import actions
from .backfill import InfoScan, SceneScan, TitleScan, ThumbAudit, ThumbBackfill, VideoCount
from .dupes import (
    DuplicateScan, ImageDuplicateScan, SignatureGroupScan, SignatureScan,
)
from .help import HelpDialog
from .board import COLUMN_CHOICES, BoardView
from .actions import HistoryEntry
from .config import (
    APP_NAME, KIND_PHOTO, KIND_VIDEO, TRASH_FOLDER_NAME, VIDEO_EXTS, Config,
    kind_paths, media_kind, photo_mode, set_media_kind,
)
from .header import (
    CONTENT_FOLDERS, CONTENT_VIDEOS, HEADER_STYLE, TAB_FOLDERS,
    TAB_FAVS, TAB_SPLIT, TAB_TAGS, TAB_VIDEOS, TABS, VIEW_BROWSE, VIEW_EDIT, Breadcrumb, Chips,
    ControlBar, KindToggle, Segmented,
    build_overflow,
)
from . import media
from .covers import COVERS
from .media import PreviewManager, Tools, page_count
from .ratings import Ratings
from .tagging import TagsThread, iter_tag_items, unreadable_name
from .split import DEFAULT_PANES, SPLIT_STYLE, Deck, SplitWall
from .access import JOURNAL
from .perf import LOG as STALL_LOG, WATCH, mark
from .quiet import QUIET_COVERS, QuietPage
from .lock import LockPage, PinDialog, ask_pin, pin_is_set
from . import licence
from . import update
# Le dialogue du partage et la recherche sur le web s'importent a leur
# premiere ouverture : la seconde tirait requests, bs4 et robotparser au
# lancement -- pres d'une seconde avant la fenetre, plusieurs a froid --
# pour une fonction qu'on ouvre rarement.
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
    # Ce qu'on vient d'ecarter : ambre, pas rouge. Une suppression voulue
    # s'affichait comme un echec, et l'on ne voyait plus le rouge le jour
    # d'un vrai echec.
    "trash": "#46381a",
}
from .scan import (
    set_veiled, under_veiled,
    MODE_FILES, MODE_FLAT, MODE_FOLDERS, PARENT_PREFIX, Item, RefreshThread,
    RootUnreadable, cached_items, canon_root, fast_path, human_duration,
    human_resolution, human_size, known_media, list_entries,
)
from .index import INDEX
from .transfer import Transfer, TransferQueue
from .trash import SessionTrash
from .tree import TreePanel
from .floating import FloatingPlayer, covered as _covered
from .icons import DIM, INK, dress, icon, lettered
from .widgets import (
    Stepper, OverBar, PeekOverlay, RadialMenu, app_icon, brand_pixmap, draw_icon,
    STYLESHEET, CommandBar, DestinationsDialog, PreviewGrid, SinglePlayer,
    FavoriteStar, PinButton, TagsDialog, TrashDialog, VeilDialog,
)

PAGE_WELCOME, PAGE_SORT, PAGE_DONE, PAGE_QUIET, PAGE_LOCK = 0, 1, 2, 3, 4

# Les dossiers virtuels epingles en tete de la collection : ce qu'aucun
# nom ne range, et ce qui n'est dans aucun sous-dossier. Leur nom dit ce
# qu'ils contiennent : « À trier » et « Orphelins » ne s'expliquaient pas,
# on ne se rappelait jamais lequel etait lequel.
PINNED_UNREAD = "Noms sans aucun mot — partout"
PINNED_LOOSE = "Noms sans aucun mot — en vrac"
# Assez courts pour tenir sur une carte ; la phrase entiere est dans la bulle.
PINNED_TIPS = {
    PINNED_UNREAD: "Les vidéos dont le nom ne dit rien (8f7e6d5c4b, IMG_2041…), "
                   "où qu'elles soient : à regarder pour les ranger.",
    PINNED_LOOSE: "Parmi elles, seulement celles posées en vrac : directement à "
                  "la racine ou dans un dossier « + », hors de tout sous-dossier.",
}


class _RenameField(QLineEdit):
    """Le nom en cours de renommage, pose sur le titre (F2).

    Entree valide ; Echap renonce, et un clic ailleurs aussi : rien ne se
    renomme sur le disque sans qu'on l'ait valide.
    """

    committed = Signal(str)
    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if not event.isAutoRepeat():
                self.committed.emit(self.text())
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        # Le menu du clic droit prend le clavier un instant : ce n'est pas
        # partir ailleurs.
        if event.reason() != Qt.PopupFocusReason:
            self.cancelled.emit()

# Ce qu'il advient de ce qu'on supprime, dit apres « Supprimer » (infobulle
# de la touche). Une seule action, un seul mot : on ne lisait plus « Écarter »,
# « Mettre à la corbeille » et « Supprimer » pour le meme geste.
DELETE_LABELS = {
    "recycle": "corbeille de Windows à la fermeture",
    "permanent": "détruit à la fermeture",
    "local_trash": "dossier de secours à la fermeture",
}


def _weight(size: int) -> str:
    """Un poids lisible : « 850 Mo », « 1,4 Go »."""
    if size >= 1e9:
        return f"{size / 1e9:.1f} Go".replace(".", ",")
    return f"{size / 1e6:.0f} Mo"


def web_recap(activity: dict) -> tuple:
    """(texte, avancement 0..1 ou None, infobulle) de ce que fait la recherche
    web -- ou ("", None, "") s'il n'y a rien a dire."""
    parts, tips = [], []
    if activity.get("searching"):
        parts.append(f"Recherche {activity['sites_done']}/{activity['sites_total']} sites"
                     f" · {activity['results']} résultats")
    elif activity.get("verifying"):
        parts.append(f"Vérifiées {activity['checked']}/{activity['results']}"
                     f" · {activity['good']} bonnes")
    downloads = activity.get("downloads") or {}
    fraction = None
    active = downloads.get("active", 0)
    if active:
        done, total = downloads.get("done", 0), downloads.get("total", 0)
        text = f"↓ {active}"
        if total:
            text += f" · {_weight(done)} / {_weight(total)}"
            if downloads.get("unknown"):
                text += " +"                    # des fichiers au poids encore inconnu
            fraction = min(1.0, done / total)
        elif done:
            text += f" · {_weight(done)}"
        parts.append(text)
    else:
        finished, failed = downloads.get("finished", 0), downloads.get("failed", 0)
        if finished or failed:
            text = f"✓ {finished} téléchargé{'s' if finished > 1 else ''}" if finished else ""
            if failed:
                text += (" · " if text else "") + f"{failed} échec{'s' if failed > 1 else ''}"
            parts.append(text)
    tips = list(activity.get("lines") or [])
    if not parts:
        return "", None, ""
    tip = "Recherche vidéo sur le web — cliquez pour y revenir"
    if tips:
        tip += "\n\n" + "\n".join(tips[:12])
        if len(tips) > 12:
            tip += f"\n… et {len(tips) - 12} autre(s)"
    return "  |  ".join(parts), fraction, tip


class WelcomePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 50, 60, 50)
        layout.setSpacing(14)

        # Le logo, en ligne : le P prismatique et le lettrage PRISME.
        screen = QApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen else 1.0
        title = QWidget(self)
        lockup = QHBoxLayout(title)
        lockup.setContentsMargins(0, 0, 0, 0)
        lockup.setSpacing(14)
        mark = QLabel(title)
        mark.setPixmap(brand_pixmap("p", 58, ratio))
        word = QLabel(title)
        word.setPixmap(brand_pixmap("lettrage", 26, ratio))
        word.setAccessibleName(APP_NAME)
        lockup.addWidget(mark, 0, Qt.AlignVCenter)
        lockup.addWidget(word, 0, Qt.AlignVCenter)
        lockup.addStretch(1)
        self.subtitle = QLabel("", self)
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setWordWrap(True)

        self.choose = QPushButton("Choisir un dossier racine…", self)
        self.choose.setObjectName("primary")

        recent_label = QLabel("Racines récentes", self)
        recent_label.setObjectName("hint")
        self.recent = QListWidget(self)

        layout.addWidget(title)
        layout.addWidget(self.subtitle)
        layout.addSpacing(10)
        layout.addWidget(self.choose, 0, Qt.AlignLeft)
        layout.addSpacing(20)
        layout.addWidget(recent_label)
        layout.addWidget(self.recent, 1)
        self.set_kind(KIND_VIDEO)

    def set_kind(self, kind: str) -> None:
        noun = "photos" if kind == KIND_PHOTO else "vidéos"
        self.subtitle.setText(
            f"Choisissez le dossier racine de vos {noun}. S'il contient des "
            "sous-dossiers, ils sont triés un par un ; sinon, ses "
            f"{noun} le sont une par une.")

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
        # La sortie qu'on cherche en premier : un Espace de trop sur la
        # derniere video enfermait ici, et il fallait tout reanalyser pour
        # retrouver ses vignettes. Echap y ramene aussi.
        self.back = QPushButton("Revenir aux vignettes", self)
        self.back.setObjectName("primary")
        self.rescan = QPushButton("Réanalyser ce dossier", self)
        self.change = QPushButton("Changer de racine", self)
        layout.addWidget(self.title)
        layout.addWidget(self.summary)
        layout.addSpacing(16)
        row = QHBoxLayout()
        row.addWidget(self.back)
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


def _within(key: str, places: set) -> bool:
    """Vrai si ce chemin (deja en `normcase`) est l'un de ces endroits, ou
    s'il est dessous : un dossier ecarte emporte toutes ses videos."""
    while key:
        if key in places:
            return True
        parent = os.path.dirname(key)
        if parent == key:
            return False
        key = parent
    return False


def _reach_state(path, remembered: str = "") -> tuple:
    """(etat, adresse du NAS) : d'abord le diagnostic de `nasreach`, en
    quelques secondes -- le nom du NAS repond-il ? -- puis seulement le
    dossier. Derriere un VPN, Windows cherchait le nom pendant des minutes,
    boutons de l'accueil grises : « cache » le dit tout de suite."""
    from . import nasreach, roots
    if roots.is_union(path):
        return ("ok" if roots.check_all() else "injoignable"), ""
    state, ip = nasreach.diagnose(path, remembered)
    if state == "cache":
        return "cache", ip
    if state == "absent":
        return "injoignable", ""
    return _folder_state(path), ip


def _folder_state(path) -> str:
    """« ok », « absent » ou « injoignable » pour un dossier, en un aller-retour.

    `is_dir()` rendait faux pour un NAS endormi, et l'on annoncait « n'existe
    plus » un dossier qui n'avait pas bouge. Le second aller-retour, celui qui
    departage, n'a lieu que sur le chemin de l'echec.
    """
    from . import roots
    if roots.is_union(path):
        return "ok" if roots.members(path) else "injoignable"
    try:
        return "ok" if stat.S_ISDIR(os.stat(path).st_mode) else "absent"
    except OSError:
        return actions.probe(path)


class _QuietKeys(QObject):
    """Ctrl+K par-dessus un dialogue : le repli doit y repondre aussi.

    Un dialogue modal garde le clavier pour lui, et Ctrl+K n'y faisait rien --
    pas meme sur la corbeille ou la question « Supprimer ce dossier ? », qui
    affichent des noms de fichiers. Le filtre ne s'arme que le temps d'un
    dialogue : pose sur toute l'application en permanence, il ferait passer
    chaque evenement (chaque image des videos) par Python, et par son verrou.
    """

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.armed = False

    def follow(self, *_args) -> None:
        """Arme le filtre tant qu'un dialogue modal tient le clavier.

        Suit le focus en plus de l'activation de la fenetre : une boite
        ouverte pendant qu'on etait dans une autre application ne changeait
        rien a l'activation de la fenetre de Prisme, deja inactive.
        """
        modal = QApplication.activeModalWidget()
        self.arm(modal is not None and modal is not self.window
                 and not self.window._closing)

    def arm(self, on: bool) -> None:
        if on == self.armed:
            return
        app = QApplication.instance()
        if app is None:
            return
        if on:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
        self.armed = on

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key_K
                and event.modifiers() & Qt.ControlModifier
                and not event.modifiers() & Qt.AltModifier):
            if not event.isAutoRepeat():
                # Apres coup : les dialogues fermes rendent d'abord la main.
                QTimer.singleShot(0, self.window.quiet_now)
            return True
        return False


class _GlobalQuietKey(QObject):
    """Ctrl+Alt+K depuis n'importe quelle fenetre : Prisme passe au repli.

    Ctrl+K ne vaut que si Prisme a le clavier ; au moment ou quelqu'un
    arrive, on est souvent ailleurs, et il fallait deux gestes -- le premier
    clic sur un panneau du mur ouvrant parfois une fiche. Un fil a lui attend
    le raccourci aupres de Windows : rien ne passe par la fenetre, et rien ne
    coute tant qu'on ne le presse pas. Il ne fait qu'entrer au repli : presse
    deux fois, il ne ramene jamais Prisme a l'ecran.
    """

    pressed = Signal()
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ok = False
        self._thread_id = 0
        self._thread = None

    def start(self) -> bool:
        """Vrai si le raccourci est pris ; faux s'il est deja a un autre."""
        if os.name != "nt" or self._thread is not None:
            return self.ok
        import threading
        ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(ready,),
                                        daemon=True, name="prisme-repli")
        self._thread.start()
        ready.wait(2.0)
        return self.ok

    def _loop(self, ready) -> None:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self.ok = bool(user32.RegisterHotKey(
            None, 1, self.MOD_CONTROL | self.MOD_ALT | self.MOD_NOREPEAT, ord("K")))
        ready.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == self.WM_HOTKEY:
                    self.pressed.emit()
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if not self._thread_id or self._thread is None:
            return
        import ctypes
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        self._thread.join(1.0)
        self._thread = None
        self.ok = False


def _phone_links(server, top):
    """La file des liens envoyes du telephone, pour le partage du PC : les
    videos arrivent dans la collection, et dans les favoris de qui les a
    envoyees. None si la racine n'est pas un vrai dossier."""
    from . import roots
    from .config import PRIVATE_DIR
    from .downloader import available
    from .liens import FOLDER_NAME, LinkJobs
    if top is None or roots.is_union(top):
        return None

    def arrived(job: dict, path: Path) -> str:
        mark = server.library.add_download(path)
        JOURNAL.set_favorite(job.get("label", ""), mark, path.name, True)
        return mark

    return LinkJobs(Path(top) / FOLDER_NAME, PRIVATE_DIR / "liens.json", arrived,
                    ffmpeg=lambda: Tools.ffmpeg, available=available)


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle(APP_NAME)
        # L'application porte deja l'icone (main.py) : la fenetre en herite.
        # La redessiner ici coutait un second rendu au lancement.
        if QApplication.windowIcon().isNull():
            self.setWindowIcon(app_icon())
        self._restore_geometry(cfg["window"])
        # Les regles du mur aussi : ses reglages vivent sur la premiere ligne,
        # hors du mur, et les colonnes de la planche portent le meme nom. Hors
        # de sa cascade, « Non vus » actif ne se distinguait plus d'inactif.
        # Mais la taille des boutons d'un panneau (12 points, peu de marge)
        # rapetissait « Verticales » et « Non vus » a cote des autres boutons
        # de la ligne : ils gardent ici la taille de ceux-ci.
        self.setStyleSheet(STYLESHEET + HEADER_STYLE + SPLIT_STYLE
                           + "QWidget#wallControls QPushButton#splitButton "
                             "{ padding: 6px 12px; font-size: 13px; }")

        self.all_items: list = []      # tout ce que l'analyse a trouve
        self.items: list = []          # ce que le filtre laisse passer
        self.index = 0                 # index dans self.items
        # Au lancement : l'onglet Dossiers, a la racine, sans filtre. Des
        # filtres oublies d'une session a l'autre (« non vus », « horizontales
        # seules », « 5 videos au plus ») donnaient des ecrans vides qu'on ne
        # savait pas expliquer. Les recherches a garder s'enregistrent.
        # Et toujours les videos : les photos se choisissent (Vidéos | Photos).
        set_media_kind(KIND_VIDEO)
        cfg["media_kind"] = KIND_VIDEO
        self.tab = TAB_FOLDERS
        cfg["content"] = CONTENT_FOLDERS
        cfg["view"] = VIEW_BROWSE
        # Le tri aussi : la pastille « Durée ▼ » d'hier s'affichait allumee sur
        # une liste qui, elle, restait dans l'ordre des noms -- et le premier
        # clic l'inversait.
        # La rafale de meme : retenue d'une seance a l'autre, sans rien qui la
        # montre, elle faisait defiler les videos toutes seules au lancement.
        # Pour les deux collections : les photos gardaient sinon « Non vus »
        # et « Horizontales » d'une seance a l'autre, et la moitie manquait.
        for kind in (KIND_VIDEO, KIND_PHOTO):
            self._reset_filters(cfg, kind)
        cfg["burst"] = False
        # On arrive toujours sur les vignettes : l'edition se choisit.
        self._editing = False
        # Le reflet de l'onglet, pour la configuration. Il ne decide plus de
        # rien : il derivait de l'onglet des qu'on ouvrait un dossier sans
        # sous-dossier, et « Dossiers » se mettait alors a tout lister a plat.
        self.content = CONTENT_FOLDERS
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
        self._init_roots()
        self.tags: list = list(cfg["tags"])
        self.mode = MODE_FOLDERS
        self.root: Path | None = None
        self.plans: dict = {}       # cle "chemin@page" -> plan d'apercus
        self.pages: dict = {}       # page d'apercus courante par element
        self.sort_mode = ""         # le classement de l'onglet affiche
        # Le classement de chaque onglet : « Vidéos » part toujours au hasard,
        # et c'etait toute l'application qui s'y mettait -- « Dossiers » se
        # remelangeait a chaque retour, vignettes a refaire.
        self._tab_sorts: dict = {}
        # Le hasard de « Dossiers » tient toute la seance : le meme ordre a
        # chaque retour, tant qu'on ne le redemande pas.
        self._shuffle_seed = int.from_bytes(os.urandom(4), "little")
        # L'instant ou ouvrir la prochaine video (chemin, ms, quand) : un clic
        # sur un apercu a 3 min 20 ouvrait la video a son debut.
        self._pending_start = None
        # Le message de l'onglet qu'un « rien ne passe les filtres » a
        # remplace sur la planche vide : (le sien, celui pose a sa place).
        self._empty_said = None
        self.criteria: dict = {}    # filtres chiffres de la planche
        # Tout ce que Ctrl+Z peut defaire, pour toute la seance.
        self.history: list = []
        # Les elements dont un transfert est en vol, par identifiant : son
        # retour les retrouve sans parcourir la collection.
        self._in_flight: dict = {}
        # Lecteur -> partage reseau ou non : la question revient a chaque fiche.
        self._remote_drives: dict = {}
        # Comptage et verification arretes a la demande : leur resultat
        # partiel ne doit pas passer pour celui de la collection.
        self._count_stopped = False
        self._audit_stopped = False
        self.stats = {"moved": 0, "deleted": 0, "skipped": 0}
        self.scan_thread: RefreshThread | None = None
        self.scanning = False
        # Pile des dossiers traverses, pour pouvoir remonter d'ou l'on vient.
        self.levels: list = []
        self._restore_id = ""
        # Historique de navigation, distinct de la pile des niveaux : il retient
        # les endroits visites, y compris lateralement, pour un vrai « Precedent ».
        self.visited: list = []

        # Videos ou photos : la collection de la derniere fois. L'index et les
        # favoris de chacune vivent a part (`config.kind_paths`).
        self.ratings = Ratings(path=self._enter_kind(cfg["media_kind"]), parent=self)

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
        set_veiled(self._active_veil(), cfg["show_veiled"])
        self._commands_signature = None
        self._top_tags = None          # ((racine, nb videos), items) deja calcules
        self._tags_thread = None
        self._tags_key = None
        self.titles_scan = None
        self.scene_scan = None
        self.sig_scan = None
        # Vrai le temps d'un repli sur l'a-peu-pres, quand l'exact n'a rien rendu.
        self._loose = False
        # Le parcours approchant en cours, par tranches (`_loose_step`).
        self._loose_scan = None
        # Le compteur de session : combien de decisions, depuis quand.
        self._session_started = time.monotonic()
        self._decisions = 0
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(30000)
        self.session_timer.timeout.connect(self._session_tick)
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
        self._flat_cache: dict = {}
        # Les versions de la collection. Chaque liste qu'on en tire (videos a
        # plat, mots-cles, vivier du mur, tirage au hasard, voisines d'une
        # video) se garde tant que la collection ne bouge pas : les refaire a
        # chaque onglet coutait de une demi-seconde a quatre secondes sur cent
        # mille videos. `_collection_gen` bouge a chaque changement ;
        # `_rebuild_gen` seulement quand la liste ne fait pas que s'allonger.
        self._collection_gen = 0
        self._rebuild_gen = 0
        self._sortable_memo: dict = {}
        self._collection_src = None
        self._collection_serial = 0
        self._videos_memo = None       # (cle, videos) de `_videos_from_items`
        self._pinned_memo = None       # (cle, cartes epinglees « À trier »…)
        self._pinned_job = None        # cle du calcul en cours
        self._flat_list = None         # (cle, liste a plat de l'onglet Videos)
        self._flat_job = None          # cle de la preparation en cours
        self._flat_show = False        # la poser a l'ecran une fois prete
        self._mine_tags = None         # (cle, mots-cles « Mes mots »)
        self._mine_key = None          # cle du calcul en cours
        self._tags_banner = False      # dire le compte une fois les mots faits
        self._pool_memo = None         # (cle, vivier, inconnues) du mur
        self._wall_batch: list = []    # le lot en cours de sondage pour le mur
        self._random_memo = None       # (cle, dossiers, poids cumules)
        # Le hasard sans remise : Ctrl+H et le de, puis « Au hasard ici ». Une
        # video tiree ne revient qu'une fois tout le vivier passe -- random
        # ramenait la meme deux fois en dix tirages.
        self._random_deck = Deck()
        self._here_deck = Deck()
        # Le mur quitte pour la fiche d'une de ses videos (⤢, F) : Echap y
        # ramene, avec les memes panneaux. Il fallait refaire un mur neuf.
        self._back_to_wall = None
        self._wall_return = None
        # Les tirages au de qui se suivent (`pick_random`) : la derniere video
        # tiree, et celles d'avant pour ←.
        self._random_run = ""
        self._random_path = ""
        # Mode « au hasard » : il tient jusqu'a ce qu'on aille ailleurs soi-meme
        # (un dossier, une vignette, un onglet), et non tant que la fiche
        # montre la video tiree -- voir `_in_random_run`.
        self._random_on = False
        self._random_launching = False
        self._random_history: list = []
        # Le cinema est un vrai plein ecran : l'etat de la fenetre a rendre.
        self._cinema_kept = None
        self._cinema_aside = None
        # Le renommage sur place (F2) : le champ, et l'element qu'il renomme.
        self._rename_field = None
        self._renaming = None
        # L'etat d'origine de la ligne d'infos masquee pendant l'edition.
        self._rename_subtitle = None
        # Une fiche rechargee apres son renommage reprend a cet instant.
        self._rename_resume = None
        self._siblings_gen = -1
        # La recolte : ce qu'elle a deja sur le metier, pour ne pas la
        # relancer de zero a chaque onglet.
        self._harvest_key = None
        self._harvest_src = None
        self._harvest_complete = None
        self._harvest_token = 0
        # Vrai le temps d'appliquer un paquet de l'analyse : la planche
        # signale chaque carte ajoutee, on ne recompte qu'une fois a la fin.
        self._patching = False
        # Une liste de passage (les doublons) : aucun rappel ne l'ecrase, et
        # recliquer l'onglet rend la collection.
        self._transient = ""
        self._dupe_members: dict = {}
        self._pending_dupes = None
        # Vrai quand ce resultat n'est qu'une etape (empreintes en cours).
        self._pending_partial = False
        self._dupes_next = None
        self._banner_action = None
        # Le partage : son catalogue se refait hors du fil de l'interface.
        self._share_stamp = None
        self._share_opening = False
        self._share_building = False
        self._share_again = False
        self._share_token = 0
        self._share_announce = False
        self._closing = False
        self._timers_paused = False
        # Le repli (Ctrl+K). Tant qu'il dure, rien ne joue ni n'avance en
        # coulisse ; ce qu'on regardait reprend au retour, la ou on l'a laisse.
        self._quiet = False
        self._quiet_from = PAGE_SORT
        self._quiet_by_lock = False    # le repli vient du cadenas du lancement
        self._quiet_show = False       # une fiche demandee pendant le repli
        self._quiet_playing: list = []  # les lecteurs qui jouaient
        self._quiet_timers: tuple = ()  # vu, rafale : lesquels couraient
        self._quiet_full = False       # le mur etait en plein ecran
        self._quiet_icon = None        # (icone, posee par la fenetre ?)
        # Une touche maintenue au moment du retour ne doit pas continuer sur
        # la page retrouvee (Echap remontait jusqu'en haut).
        self._eat_repeat = False
        # Les bandeaux dits pendant le repli ou fenetre reduite : montres au
        # retour, et non perdus.
        self._held_banners: list = []
        self._banner_tone = None
        # Un echec reste lisible quelques secondes : le message ordinaire qui
        # suit attend (`show_banner`).
        self._banner_guard = 0.0
        self._banner_next = None
        # Une alerte (echec qu'on garde) : tout autre bandeau attend qu'elle
        # ait ete lue -- cliquee, ou son temps ecoule.
        self._alarm_until = 0.0
        # La racine qu'on est en train d'ouvrir, et son numero (`open_root_async`).
        self._opening = None
        self._opening_serial = 0
        self._opening_text = ""
        # Le calcul de « Mes mots-clés », par tranches (`_in_slices`).
        self._mine_slicer = None
        self._quiet_keys = _QuietKeys(self)
        self.global_quiet = None
        self._menu_by_text: dict = {}
        # La recherche qui se precise : on filtre ce qui est deja a l'ecran.
        self._narrow_items = None
        self._narrow_source = None
        self._narrow_context = None
        self._narrow_query = ""
        # L'a-peu-pres attend que la frappe se pose.
        self.loose_timer = QTimer(self)
        self.loose_timer.setSingleShot(True)
        self.loose_timer.setInterval(450)
        self.loose_timer.timeout.connect(self._loose_later)
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
        # Un favori pose ou retire, d'ou que ce soit (fiche, vignette, panneau
        # du mur) : chaque vue qui montre cet element suit aussitot.
        self.ratings.changed.connect(self._favorite_changed)
        self._refresh_mute()
        self._refresh_state()
        # Le chien de garde est unique : une fenetre d'essai detruite l'a pu
        # emporter avec elle (il se rattache a la derniere construite).
        if shiboken6.isValid(WATCH):
            WATCH.setParent(self)
            WATCH.start(str(cfg["root"] or ""))
        self._siblings_cache: dict = {}
        self.welcome.set_recent(cfg["recent_roots"])
        self._apply_kind_ui()
        self.stack.setCurrentIndex(PAGE_WELCOME)
        # Un plancher pose, et non calcule : Qt faisait de la somme des
        # largeurs de la premiere ligne le minimum de la fenetre -- 995 points
        # au lancement, 1 082 sur le mur --, et une fois elargie par un onglet
        # elle ne retrecissait plus. Sur un ecran agrandi, ⋯ sortait du cadre.
        # Ce qui ne tient pas se laisse rogner ; la fenetre, elle, tient.
        self.setMinimumSize(*self._window_floor())
        # Ctrl+K d'ou que l'on soit dans Prisme : un menu ouvert (⋯, clic
        # droit) garde le clavier, et la touche n'arrivait pas a la fenetre.
        self._quiet_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._quiet_shortcut.setContext(Qt.ApplicationShortcut)
        self._quiet_shortcut.setAutoRepeat(False)
        self._quiet_shortcut.activated.connect(self._quiet_key)
        QApplication.instance().focusChanged.connect(self._quiet_keys.follow)
        # L'essai, en haut de la fenetre ; puis le cadenas, si un code est
        # pose : rien de la collection ne parait avant le bon code.
        self._show_trial()
        self.lock_now()

    @staticmethod
    def _window_floor() -> tuple:
        """La plus petite taille ou la fenetre reste utilisable, ecran compris."""
        width, height = 640, 400
        screen = QApplication.primaryScreen()
        if screen is not None:
            free = screen.availableGeometry()
            width = min(width, max(320, free.width() - 40))
            height = min(height, max(240, free.height() - 80))
        return width, height

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
        # Une racine prise dans cette liste est l'origine du fil, comme une
        # racine choisie : elle ne s'accrochait pas sous la precedente.
        self.welcome.recent.itemActivated.connect(
            lambda item: self.open_root_async(canon_root(item.text())))
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

        # Videos ou photos : l'icone dit ou l'on est, un clic, et l'on trie
        # l'autre collection, a sa derniere racine.
        self.kind_switch = KindToggle(sort_page)
        self.kind_switch.chosen.connect(self.pick_kind)
        row_one.addWidget(self.kind_switch, 0)

        self.tabs = Segmented("", [
            (TAB_FOLDERS, "Dossiers", "Chaque dossier comme une carte"),
            (TAB_VIDEOS, "Vidéos", "Toutes les vidéos en vrac, au hasard"),
            (TAB_TAGS, "Mots-clés", "Les vidéos réunies par les mots de leurs noms"),
            (TAB_SPLIT, "Mur", "Plusieurs vidéos à la fois"),
            (TAB_FAVS, "★ Favoris", "Les dossiers et les vidéos mis en favori"),
        ], sort_page)
        self.tabs.chosen.connect(self.set_tab)
        # Leurs icones, pour un ecran etroit (`_fit_top_row`).
        self.tabs.glyphs = {TAB_FOLDERS: "folder-open", TAB_VIDEOS: "film",
                            TAB_TAGS: "tags", TAB_SPLIT: "layout-grid",
                            TAB_FAVS: "star"}
        row_one.addWidget(self.tabs, 0)

        # Remonter d'un cran, d'ou qu'on soit. `go_up` ne savait revenir que
        # par ou l'on etait descendu : arrive par un mot-cle, par l'historique
        # ou par une racine choisie, la pile etait vide et rien ne remontait.
        self.up_button = QPushButton("", sort_page)
        self.up_button.setIcon(draw_icon("up"))
        self.up_button.setIconSize(QSize(20, 20))
        self.up_button.setObjectName("up")
        self.up_button.setFixedWidth(34)
        self.up_button.setToolTip("Remonter au dossier parent   (Ctrl+↑ ou Échap)")
        self.up_button.setFocusPolicy(Qt.NoFocus)
        self.up_button.clicked.connect(self.go_parent)
        row_one.addWidget(self.up_button, 0)

        # Les racines : la reunion de toutes, ou l'une d'elles, d'un clic. Une
        # racine debranchee est grisee, et ses videos ne se montrent pas.
        self.root_button = QToolButton(sort_page)
        self.root_button.setObjectName("rootButton")
        self.root_button.setPopupMode(QToolButton.InstantPopup)
        self.root_button.setFocusPolicy(Qt.NoFocus)
        self.root_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.root_button.setToolTip("Les racines : toutes ensemble, ou l'une d'elles")
        self.root_menu = QMenu(self.root_button)
        self.root_menu.aboutToShow.connect(self._fill_root_menu)
        # Apres la fermeture, pas pendant : le menu doit avoir disparu avant
        # que la fenetre ne recompose la liste.
        self.root_menu.aboutToHide.connect(
            lambda: QTimer.singleShot(0, self._root_menu_closed))
        self.root_button.setMenu(self.root_menu)
        row_one.addWidget(self.root_button, 0)

        self.crumbs = Breadcrumb(sort_page)
        self.crumbs.jumped.connect(self.jump_to)
        # Le fil d'Ariane prend la place qui reste, et cede la sienne quand
        # il n'y en a plus : il ne doit jamais elargir la fenetre.
        self.crumbs.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.crumbs.setMinimumWidth(40)
        row_one.addWidget(self.crumbs, 1)

        # La recherche web, fenetre reduite : ce qu'elle fait, en une pastille
        # au milieu de la barre -- les sites parcourus, les telechargements
        # et leur poids. Un clic ramene sa fenetre. Rien en cours : rien.
        self.web_status = QPushButton("", sort_page)
        self.web_status.setObjectName("webStatus")
        self.web_status.setCursor(Qt.PointingHandCursor)
        self.web_status.setFocusPolicy(Qt.NoFocus)
        dress(self.web_status, "globe", 15)
        self.web_status.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.web_status.setMaximumWidth(520)
        self.web_status.clicked.connect(self.open_web_search)
        self.web_status.hide()
        self._web_status_style = None
        row_one.addWidget(self.web_status, 0)
        # Les autres travaux de fond, une pastille chacun, a cote de celle de
        # la recherche web : l'indexation du Labo IA, les empreintes des
        # doublons, le regroupement dans « 1 », le modele de la voix. On suit
        # ou ils en sont sans quitter ce qu'on regarde, Labo cache compris.
        self.task_pills: dict = {}
        self._pill_row = QHBoxLayout()
        self._pill_row.setContentsMargins(0, 0, 0, 0)
        self._pill_row.setSpacing(6)
        row_one.addLayout(self._pill_row, 0)
        self._web_status_timer = QTimer(self)
        self._web_status_timer.setInterval(1000)
        self._web_status_timer.timeout.connect(self._show_web_status)
        self._web_status_timer.start()
        # Les telechargements interrompus a la derniere fermeture repartent
        # peu apres le lancement (la collection s'ouvre d'abord).
        QTimer.singleShot(4000, self._resume_web_downloads)

        # Ce qui tourne en fond, dit en trois mots a cote du fil d'Ariane :
        # analyse, aperçus, préparation, empreintes, rafale. Ces avancements
        # s'ecrivaient dans des boutons toujours caches : on ne distinguait
        # pas une application lente d'une analyse en cours, et rien ne
        # permettait d'arreter celle-ci. Un clic propose de l'arreter.
        self.activity_label = QLabel("", sort_page)
        self.activity_label.setObjectName("hint")
        # Un message long se coupe ; il n'elargit jamais la fenetre.
        self.activity_label.setMinimumWidth(10)
        self.activity_label.installEventFilter(self)
        self._activity_said = None
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
        # Sa largeur suit son texte, de 130 a 300 points (`_progress_text`) :
        # a 130 fixes, « vignettes : 41 230 / 106 903 — 2 h restant » perdait
        # justement le total et le temps restant.
        self.progress.setFixedWidth(130)
        # Pendant une analyse, la barre tient lieu d'etat de la collection :
        # les deux cote a cote debordaient d'un ecran agrandi.
        self.progress.installEventFilter(self)
        row_one.addWidget(self.progress, 0)

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
                                      "collection — jamais deux fois la même "
                                      "avant que toutes soient sorties   (Ctrl+H)")
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
        # La molette regle le volume, le clic droit ouvre son curseur.
        self.mute_button.installEventFilter(self)
        self.mute_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.mute_button.customContextMenuRequested.connect(self._volume_menu)
        from . import audiodev
        audiodev.set_volume(int(self.cfg["volume"] if self.cfg["volume"] is not None
                                else 100) / 100.0)
        audiodev.choose(self.cfg["audio_output"] or "")
        row_one.addWidget(self.mute_button, 0)

        # Le lecteur flottant automatique, d'un clic : allume, la video passe
        # par-dessus tout quand Prisme est reduit ou recouvert ; eteint, elle
        # reste dans Prisme. Le reglage vivait seul au fond du menu ⋯.
        self.float_button = QPushButton("", sort_page)
        self.float_button.setIconSize(QSize(20, 20))
        self.float_button.setFixedWidth(42)
        self.float_button.setFocusPolicy(Qt.NoFocus)
        self.float_button.clicked.connect(self.toggle_float_auto)
        row_one.addWidget(self.float_button, 0)
        self._dress_float_button()

        # L'adresse publique ouverte : un temoin tant qu'elle l'est. Elle
        # s'ouvre d'elle-meme au lancement, et seul un bandeau de quatre
        # secondes le disait -- la bibliotheque restait joignable d'Internet
        # sans que rien ne le rappelle. Un clic ouvre le partage ; un clic
        # droit ferme l'adresse.
        self.share_badge = QPushButton("", sort_page)
        dress(self.share_badge, "share-2", 18)
        self.share_badge.setObjectName("shareBadge")
        self.share_badge.setFixedWidth(34)
        self.share_badge.setFocusPolicy(Qt.NoFocus)
        self.share_badge.setStyleSheet(
            "QPushButton#shareBadge { background: #1f3326; border-color: #2f5a3c; }"
            "QPushButton#shareBadge:hover { background: #26402f; }")
        self.share_badge.clicked.connect(self.open_share)
        self.share_badge.setContextMenuPolicy(Qt.CustomContextMenu)
        self.share_badge.customContextMenuRequested.connect(self._share_badge_menu)
        self.share_badge.hide()
        row_one.addWidget(self.share_badge, 0)
        # Les demandes du telephone qu'on n'a pas encore vues : une pastille,
        # et un clic les montre.
        self.ask_badge = QPushButton("", sort_page)
        self.ask_badge.setObjectName("askBadge")
        self.ask_badge.setFocusPolicy(Qt.NoFocus)
        self.ask_badge.setStyleSheet(
            "QPushButton#askBadge { background: #3a2a10; border: 1px solid #8a6420;"
            " color: #ffd479; font-weight: 600; padding: 4px 10px; border-radius: 6px; }"
            "QPushButton#askBadge:hover { background: #4a3614; }")
        self.ask_badge.setToolTip("Des demandes envoyées depuis le téléphone")
        self.ask_badge.clicked.connect(self.open_demandes)
        self.ask_badge.hide()
        row_one.addWidget(self.ask_badge, 0)
        self._asks: list = []
        self._asks_timer = QTimer(self)
        self._asks_timer.setInterval(30000)
        self._asks_timer.timeout.connect(self._poll_demandes)
        self._asks_timer.start()
        QTimer.singleShot(4000, self._poll_demandes)

        # La corbeille de session, tant qu'elle n'est pas vide : ce qui partira
        # a la fermeture. Rouge quand ce sera detruit pour de bon (le NAS) --
        # le bandeau ne restait que quatre secondes, et le compte ne se lisait
        # que dans le menu ⋯. Un clic l'ouvre (Ctrl+B).
        self.trash_badge = QPushButton("", sort_page)
        self.trash_badge.setObjectName("trashBadge")
        self.trash_badge.setIcon(icon("trash-2"))
        self.trash_badge.setIconSize(QSize(17, 17))
        self.trash_badge.setFocusPolicy(Qt.NoFocus)
        self.trash_badge.setCursor(Qt.PointingHandCursor)
        self.trash_badge.clicked.connect(self.open_trash)
        self.trash_badge.hide()
        self._trash_badge_tone = None
        row_one.addWidget(self.trash_badge, 0)
        self._show_trash_badge(self.trash.count)

        # Qui regarde, en ce moment, par le lien d'invitation (PC ou NAS) :
        # un oeil et un nombre. Un clic dit quoi, et permet de regarder avec.
        self.viewers_button = QPushButton("0", sort_page)
        self.viewers_button.setObjectName("viewersBadge")
        self.viewers_button.setIcon(icon("eye", DIM))
        self.viewers_button.setIconSize(QSize(18, 18))
        self.viewers_button.setFocusPolicy(Qt.NoFocus)
        self.viewers_button.setToolTip("Personne ne regarde la bibliothèque "
                                       "partagée en ce moment.")
        self.viewers_button.clicked.connect(self._viewers_menu)
        # Toujours la : un oeil gris « 0 » dit aussi quelque chose (personne),
        # et l'on sait ou regarder quand il passe au vert.
        row_one.addWidget(self.viewers_button, 0)
        self.viewers = []
        self._viewers_busy = False
        self._live_seen = None          # (« at » du NAS, heure du PC ou il a change)
        self.viewers_timer = QTimer(self)
        self.viewers_timer.setInterval(5000)
        self.viewers_timer.timeout.connect(self._poll_viewers)
        self.viewers_timer.start()

        # Le repli. Un rond gris, sans legende : il ne doit rien annoncer a
        # qui regarde par-dessus l'epaule, et se trouver sans reflechir.
        # Un bouton comme les autres, et non plus un point : on le trouvait mal.
        # L'essai : « Essai · N jours », discret ; un clic ouvre la licence.
        self.trial_badge = QPushButton("", sort_page)
        self.trial_badge.setObjectName("trialBadge")
        self.trial_badge.setFocusPolicy(Qt.NoFocus)
        self.trial_badge.setCursor(Qt.PointingHandCursor)
        self.trial_badge.setStyleSheet(
            "QPushButton#trialBadge { background: transparent; border: 1px solid #2a323d;"
            " border-radius: 10px; color: #8b94a1; font-size: 11px; padding: 2px 9px; }"
            "QPushButton#trialBadge:hover { color: #e9eef4; border-color: #4a5566; }")
        self.trial_badge.setToolTip("Essai gratuit de Prisme — cliquez pour saisir une licence")
        self.trial_badge.clicked.connect(self.open_licence)
        self.trial_badge.hide()
        row_one.addWidget(self.trial_badge, 0)

        # Une nouvelle version : une pastille qui reste tant qu'on ne l'a pas
        # posee, et un clic la pose. Le bandeau, lui, passe.
        self.update_badge = QPushButton("", sort_page)
        self.update_badge.setObjectName("updateBadge")
        self.update_badge.setFocusPolicy(Qt.NoFocus)
        self.update_badge.setCursor(Qt.PointingHandCursor)
        self.update_badge.setStyleSheet(
            "QPushButton#updateBadge { background: #12304f; border: 1px solid #2f6fed;"
            " color: #cfe0ff; font-weight: 600; padding: 4px 10px; border-radius: 6px; }"
            "QPushButton#updateBadge:hover { background: #1a3d63; }")
        self.update_badge.clicked.connect(self.install_update)
        self.update_badge.hide()
        row_one.addWidget(self.update_badge, 0)
        self._update_found = None         # le version.json d'une version plus recente
        self._update_busy = False
        self._update_said = ""            # la version deja annoncee par un bandeau
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(update.CHECK_EVERY_S * 1000)
        self._update_timer.timeout.connect(lambda: self.check_updates(quiet=True))
        self._update_timer.start()
        # Le premier coup d'oeil attend que le lancement soit passe.
        QTimer.singleShot(15000, lambda: self.check_updates(quiet=True))
        QTimer.singleShot(3000, self._said_new_version)

        self.quiet_button = QPushButton("", sort_page)
        # Une lune : « s'effacer ». L'oeil barre disait aussi « dossiers
        # masques » et « pas des doublons ».
        self.quiet_button.setIcon(icon("moon"))
        self.quiet_button.setIconSize(QSize(20, 20))
        self.quiet_button.setFixedWidth(42)
        self.quiet_button.setToolTip(
            "Passer à autre chose : Prisme s'efface derrière une page neutre.\n"
            "Ctrl+K ou un double-clic pour revenir.   (Ctrl+K)\n"
            "Depuis une autre fenêtre : Ctrl+Alt+K.")
        self.quiet_button.setFocusPolicy(Qt.NoFocus)
        self.quiet_button.clicked.connect(self.enter_quiet)
        row_one.addWidget(self.quiet_button, 0)

        # La recherche video sur le web, d'un clic.
        self.web_button = QPushButton("", sort_page)
        self.web_button.setIcon(icon("globe"))
        self.web_button.setIconSize(QSize(20, 20))
        self.web_button.setFixedWidth(42)
        self.web_button.setFocusPolicy(Qt.NoFocus)
        self.web_button.setToolTip("Recherche vidéo sur le web : vos sites, "
                                   "vos mots-clés, vos filtres")
        self.web_button.clicked.connect(self.open_web_search)
        row_one.addWidget(self.web_button, 0)

        self.labo_button = QPushButton("", sort_page)
        self.labo_button.setIcon(icon("sparkles"))
        self.labo_button.setIconSize(QSize(20, 20))
        self.labo_button.setFixedWidth(42)
        self.labo_button.setFocusPolicy(Qt.NoFocus)
        self.labo_button.setToolTip("Labo IA : chercher une scène en la décrivant, "
                                    "collections par nom, comparer les moteurs")
        self.labo_button.clicked.connect(self.open_ai_lab)
        row_one.addWidget(self.labo_button, 0)

        self.more_button = QPushButton("", sort_page)
        self.more_button.setFixedWidth(42)
        dress(self.more_button, "ellipsis", 22)
        # La petite fleche de menu n'apprenait rien et mangeait la place.
        self.more_button.setStyleSheet(
            "QPushButton::menu-indicator { image: none; width: 0px; }")
        self.more_button.setToolTip(
            "Destinations, corbeille, doublons, réglages…\n"
            "F1 : tous les raccourcis et la recherche")
        self.more_button.setFocusPolicy(Qt.NoFocus)
        # Neuf lignes au premier niveau : ce qui sert tous les jours, puis
        # des sous-menus par theme. Le tri, l'affichage, la confidentialite
        # et les reglages d'expert ne se melangent plus ; « Changer de
        # racine », une action principale, n'est plus enfouie.
        self.overflow = build_overflow(self, [
            ("Destinations…", self.edit_destinations),
            ("Mots-clés automatiques…", self.edit_tags),
            ("Corbeille de session", self.open_trash),
            ("Ultra tri : clic gauche garde, clic droit supprime   (Ctrl+U)",
             self.toggle_ultra),
            ("Réparer des vidéos abîmées…", self.open_repair),
            ("Changer de racine…", self.choose_root),
            ("-", None),
            ("Recherches", [
                ("Enregistrer cette recherche…", self.save_search),
            ]),
            ("Collection", [
                ("Chercher les doublons", self.find_dupes),
                ("-", None),
                ("État de la collection…", self.show_collection_state),
                ("Compter les vidéos", self.count_videos),
                ("État des vignettes", self.audit_thumbs),
                ("Préparer toutes les vignettes", self.toggle_backfill),
                ("Analyser les titres des métadonnées", self.scan_titles),
                ("Repérer les plans (vignettes plus parlantes)", self.scan_scenes),
                ("Labo IA (essai)…", self.open_ai_lab),
                ("-", None),
                # « Tout le disque » promettait plus que ce qui se faisait :
                # seul le dossier affiche etait relu. A la racine, c'est
                # toute la collection, et la question le dit.
                ("Réanalyser ce dossier en entier…", self.refresh_root),
                ("-", None),
                ("Partage à distance…", self.open_share),
                ("Recherche vidéo sur le web…", self.open_web_search),
                ("Telegram : recherche et alertes…", self.open_telegram),
                ("Demandes reçues…", self.open_demandes),
            ]),
            ("Réglages", [
                ("Rafale : passer tout seul après 8 s", self.toggle_burst),
                ("Passer à la suivante après ★", self.toggle_advance_after_star),
                ("-", None),
                ("Arborescence des destinations", self.toggle_tree),
                ("Lecteur flottant automatique", self.toggle_float_auto),
                ("Bande des mots épinglés", self.toggle_word_strip),
                ("Durée du diaporama…", self.edit_slideshow_seconds),
                ("-", None),
                ("Avancé", [
                    ("Ignorer la mise à l'échelle de Windows", self.toggle_dpi),
                    ("Décodage vidéo", [
                        ("Automatique (recommandé)", lambda: self.set_decoding("auto")),
                        ("Processeur — aperçus plus vifs",
                         lambda: self.set_decoding("cpu")),
                        ("Carte graphique — soulage le processeur",
                         lambda: self.set_decoding("gpu")),
                    ]),
                    ("Où sont les vignettes…", self.show_cache_place),
                    ("Journal des gels de l'interface", self.open_stall_log),
                    # Caches (HIDDEN_MENU) : d'autres parties de Prisme les
                    # retrouvent par leur nom.
                    ("Chercher les doublons (même taille)", self.find_duplicates),
                    ("Chercher les doublons (même image)",
                     lambda: self.find_duplicates(by_image=True)),
                    ("Empreintes : sonder ce qui manque", self.scan_signatures),
                    ("Doublons d'après les empreintes", self.duplicates_from_sigs),
                ]),
            ]),
            ("Confidentialité", [
                ("Dossiers masqués…", self.edit_veiled),
                ("Afficher les dossiers masqués", self.toggle_veiled),
                ("Code PIN…", self.edit_pin),
                ("Écran de repli", [
                    (label, (lambda k=key: self.set_quiet_cover(k)))
                    for key, label, _cls in QUIET_COVERS
                ]),
            ]),
            ("Aide", [
                ("Raccourcis et recherche…", self.show_help),
                ("Rechercher une mise à jour", lambda: self.check_updates(quiet=False)),
                ("Licence…", self.open_licence),
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
        collection = next(a.menu() for a in self.overflow.actions()
                          if a.menu() is not None and a.text() == "Collection")
        # Le resultat d'une recherche de doublons attend qu'on le demande :
        # il ne remplace plus d'office ce qu'on regarde. Le bandeau passe,
        # cette entree reste tant qu'il n'a pas ete affiche.
        after_dupes = collection.actions()[1]
        self.dupes_result_action = QAction("Afficher les doublons trouvés", collection)
        self.dupes_result_action.triggered.connect(self.show_found_dupes)
        collection.insertAction(after_dupes, self.dupes_result_action)
        self.dupes_result_action.setVisible(False)
        self.dupes_watch_action = QAction("Vérifier les nouvelles vidéos (doublons)", collection)
        self.dupes_watch_action.setCheckable(True)
        self.dupes_watch_action.setChecked(bool(self.cfg["dupes_watch"]))
        self.dupes_watch_action.setToolTip(
            "À chaque arrivée de vidéos, Prisme regarde si elles ne sont pas déjà "
            "dans la collection")
        self.dupes_watch_action.toggled.connect(self._set_dupes_watch)
        collection.insertAction(after_dupes, self.dupes_watch_action)
        # Le dernier resultat a survecu a la fermeture : l'entree revient.
        QTimer.singleShot(0, self._reload_found_dupes)
        # Les racines recentes : l'accueil, qui les listait, ne parait plus
        # qu'au tout premier lancement -- la derniere racine s'ouvre seule.
        self.recent_menu = QMenu("Racines récentes", collection)
        where = next(a for a in collection.actions()
                     if a.text() == "Réanalyser ce dossier en entier…")
        collection.insertMenu(where, self.recent_menu)
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        # Les entrees par leur libelle d'origine : celles des travaux de fond
        # changent de nom pendant qu'ils tournent (« Arrêter … »), et on ne
        # les retrouvait plus par leur texte une fois renommees.
        self._menu_by_text = {action.text(): action
                              for action in self._menu_actions()}
        # La rafale dit si elle est en marche : une coche, et non un
        # libelle qui ne change jamais.
        burst = self._menu_by_text["Rafale : passer tout seul après 8 s"]
        burst.setCheckable(True)
        burst.setChecked(bool(self.cfg["burst"]))
        # Decochee par defaut : le favori se combine souvent avec un
        # rangement (★ puis « 6 »), qu'une avance automatique empecherait.
        after_star = self._menu_by_text["Passer à la suivante après ★"]
        after_star.setCheckable(True)
        after_star.setChecked(bool(self.cfg["advance_after_star"]))
        after_star.setIcon(icon("star"))
        self._dress_decoding_menu()
        self._dress_quiet_menu()
        strip = self._menu_by_text["Bande des mots épinglés"]
        strip.setCheckable(True)
        strip.setChecked(bool(self.cfg["word_pins_shown"]))
        strip.setToolTip("Sous l'en-tête, les mots-clés épinglés (l'épingle d'un mot, "
                         "dans l'onglet « Mots-clés ») : un clic montre leurs vidéos")
        floating = self._menu_by_text["Lecteur flottant automatique"]
        floating.setCheckable(True)
        floating.setChecked(bool(self.cfg["float_auto"]))
        floating.setToolTip("Quand Prisme est réduit ou recouvert pendant qu'une "
                            "vidéo joue, elle continue dans une fenêtre "
                            "toujours au premier plan")
        self.more_button.setMenu(self.overflow)
        # Ecran etroit : les boutons ronds qui n'ont plus leur place sur la
        # premiere ligne passent en tete de ce menu (`_fit_top_row`).
        self._folded: list = []
        self._folded_actions: list = []
        self._tabs_full = 0
        self.overflow.aboutToShow.connect(self._show_folded_in_menu)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(0)
        self._fit_timer.timeout.connect(self._fit_top_row)
        # Les pastilles (partage, corbeille, mise a jour…) vont et viennent :
        # on refait le compte avec l'etat de la recherche web, chaque seconde.
        self._web_status_timer.timeout.connect(self._fit_top_row)
        # Un menu plus court : ces entrees restent la (d'autres parties de
        # Prisme les retrouvent par leur nom), mais ne s'affichent plus.
        for text in self.HIDDEN_MENU:
            action = self._menu_by_text.get(text)
            if action is not None:
                action.setVisible(False)
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
        # L'etoile du dossier ou l'on est entre, en tete des filtres : on
        # mettait en favori chacune de ses videos, jamais le dossier lui-meme.
        self.folder_star = FavoriteStar(18, self.controls)
        self.folder_star.rated.connect(self.toggle_folder_favorite)
        self.folder_star.hide()
        self.controls.row.insert_widget(0, self.folder_star)
        # Le mur, lance d'ici : les videos de ce dossier dans ses panneaux.
        self.folder_wall = QPushButton("▦ Mur", self.controls)
        self.folder_wall.setObjectName("sortChip")
        self.folder_wall.setCursor(Qt.PointingHandCursor)
        self.folder_wall.setFocusPolicy(Qt.NoFocus)
        self.folder_wall.setToolTip("Le mur avec les vidéos de ce dossier : chaque "
                                    "panneau y reste, jusqu'à ce qu'on décoche sa case")
        self.folder_wall.clicked.connect(self.wall_from_folder)
        self.folder_wall.hide()
        self.controls.row.insert_widget(1, self.folder_wall)
        from . import voice
        self.voice = voice.attach(self.controls.include, self._spoken_search,
                                  lambda text: self.show_banner(text, "info", seconds=8))
        # La reconnaissance se charge en coulisse, une demi-minute apres le
        # lancement : sinon le premier clic sur le micro attendait une minute.
        QTimer.singleShot(30000, self._warm_voice)
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
        # La pagination de la ligne des filtres, que les coches remplacent :
        # une liste de doublons cochee d'office ne disait plus qu'elle avait
        # d'autres pages, et « Supprimer » emportait aussi celles qu'on
        # n'avait jamais vues.
        self.picked_count = QLabel("", self.picked_bar)
        self.picked_count.setObjectName("counter")
        self.picked_previous = QPushButton("◂", self.picked_bar)
        self.picked_next = QPushButton("▸", self.picked_bar)
        for button, tip, name, step in (
                (self.picked_previous, "Page précédente", "chevron-left", -1),
                (self.picked_next, "Page suivante", "chevron-right", 1)):
            button.setObjectName("stepper")
            button.setFixedSize(30, 28)
            dress(button, name, 20)
            button.setToolTip(tip)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda _c=False, n=step: self.change_page(n))
        for widget in (self.picked_previous, self.picked_count, self.picked_next):
            widget.hide()
            picked_row.addWidget(widget)
        # « Supprimer » en dernier, en rouge et a l'ecart : l'action de masse
        # la plus lourde avait l'air d'« Annuler », et se tenait contre lui.
        for name, text, tip, slot in (
            ("square-stack", "Mur", "Les vidéos cochées, toutes à la fois, sur le mur",
             self.wall_picked),
            ("play", "Playlist",
             "Les vidéos cochées l'une après l'autre, à droite, en boucle",
             self.playlist_picked),
            ("folder-input", "Déplacer…", "Cliquez ensuite un dossier de l'arborescence",
             self.move_picked_hint),
            # Seulement dans la liste des doublons : le groupe des elements
            # coches sort de la liste, et n'y reviendra plus.
            ("ban", "Pas des doublons",
             "Les groupes des éléments cochés ne sont pas des doublons : ils "
             "quittent la liste et ne reviendront plus", self.not_dupes_picked),
            # « Décocher » : « Annuler » est Ctrl+Z partout ailleurs.
            ("x", "Décocher", "Décoche tout   (Ctrl+N)", self.clear_picked),
            ("trash-2", "Suppr",
             "Supprime les éléments cochés — ils passent par la corbeille de session "
             "(Ctrl+B) — sur le NAS, détruits à la fermeture", self.delete_picked),
        ):
            button = QPushButton(text, self.picked_bar)
            dress(button, name, 16, text)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(slot)
            if name == "trash-2":
                button.setObjectName("danger")
                picked_row.addSpacing(18)
                self.picked_delete = button
            if name == "ban":
                self.not_dupes_button = button
                button.hide()
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
        # Sa largeur au plus (elle cede la sienne si la fenetre retrecit) :
        # l'etoile et l'epingle se posent juste apres le texte.
        self.item_subtitle.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.item_subtitle.setMinimumWidth(60)
        header_layout.addWidget(self.item_subtitle, 0)
        # L'etoile et l'epingle y viennent (voir la barre du bas) ; a leur
        # suite, « Au hasard ici » ; tout a droite, les apercus puis
        # « Ouvrir le dossier ».
        self._header_layout = header_layout

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

        self.random_here_button = action(
            "shuffle", "Au hasard ici",
            "Une vidéo au hasard, dans ce dossier seulement", self.pick_random_here)
        header_layout.addStretch(1)
        self.cinema_button = action(
            "maximize", "Cinéma",
            "Plein écran : l'image seule, sans rien autour   (F11, Ctrl+J ou "
            "double-clic sur l'image)", self.toggle_cinema)
        self.cinema_button.setCheckable(True)
        self.cinema_button.hide()
        # Dans la fiche d'un doublon : son groupe n'en est pas un. On le juge
        # en le regardant, c'est la qu'on doit pouvoir le dire.
        self.not_dupe_button = action(
            "ban", "Pas des doublons",
            "Ce groupe n'est pas un groupe de doublons : il quitte la liste et "
            "ne reviendra plus", self.not_dupes_current)
        self.not_dupe_button.hide()
        # Combien d'apercus a la fois : le meme − n + que partout.
        self.grid_chips = Stepper((2, 4, 6, 8, 10), "Aperçus à la fois",
                                  "Aperçus", header)
        self.grid_chips.chosen.connect(self.set_thumb_count)
        self.grid_chips.hide()
        header_layout.addWidget(self.grid_chips, 0)
        self._mark_grid_count()
        # Ouvrir l'explorateur, tout a droite : sur le dossier lui-meme pour un
        # dossier, sur le fichier deja selectionne pour une video.
        self.reveal_button = action(
            "folder-open", "Explorateur",
            "Montrer dans l'Explorateur Windows   (Ctrl+E)", self.reveal_current)
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
        # Les mots epingles : une ligne fine, comme sur la page mobile.
        from .wordstrip import WordStrip
        self.word_strip = WordStrip(self.top_bar)
        self.word_strip.chosen.connect(self.open_word)
        self.word_strip.removeRequested.connect(self.unpin_word)
        self.word_strip.moveRequested.connect(self.move_word)
        self.word_strip.hideRequested.connect(lambda: self.toggle_word_strip(False))
        self.word_strip.moreRequested.connect(self._pin_more_words)
        self.word_strip.set_words(list(self.cfg["word_pins"] or []))
        self.word_strip.hide()
        top_box.addWidget(self.word_strip)
        layout.addWidget(self.top_bar)

        # Le bandeau d'information flotte au-dessus du contenu, le temps de
        # se lire : pose dans la page, il poussait tout vers le bas a chaque
        # message, et la planche sautait. Fenetre-outil, pour passer aussi
        # devant une video.
        self.banner = GuardedLabel("", self, Qt.Tool | Qt.FramelessWindowHint
                             | Qt.WindowDoesNotAcceptFocus)
        self.banner.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.banner.setObjectName("statusBanner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        # Certains bandeaux proposent un geste (« afficher les doublons ») :
        # un clic dessus le fait.
        self.banner.installEventFilter(self)

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
        # L'instant demande (`play_in_app`) se pose des que la video le
        # permet : avant son chargement, un saut se perdait.
        for deck in self.single.decks:
            deck.player.mediaStatusChanged.connect(self._seek_when_ready)
        self.single.finished.connect(self.on_video_finished)
        # Le diaporama des photos : ⏯, Entree ou un clic sur l'image.
        self.single.slideshowToggled.connect(self.toggle_slideshow)
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.setInterval(
            max(1, int(self.cfg["slideshow_seconds"] or 6)) * 1000)
        self.slideshow_timer.timeout.connect(self._slideshow_next)
        # Un double-clic sur l'image : le plein ecran, et retour.
        self.single.cinemaRequested.connect(lambda: self.toggle_cinema())
        self.single.radialRequested.connect(self.open_radial)
        self.single.nextRequested.connect(self.act_skip)
        self.single.peek.chosen.connect(self.peek_seek)
        self.radial = RadialMenu(self)
        self.radial.chosen.connect(self._radial_chosen)
        self.radial.closed.connect(self._radial_closed)
        self._sorting_pane = None
        self.board = BoardView(
            self.cfg["preview_seconds"], self.cfg["board_columns"], self.viewer
        )
        self.board.openRequested.connect(self.on_board_click)
        self.board.asideRequested.connect(self.open_aside)
        self.board.pickedChanged.connect(self.on_picked_changed)
        self.board.previewNeeded.connect(self.on_board_preview)
        self.board.playRequested.connect(self.play_in_app)
        self.board.pageChanged.connect(self.on_board_page)
        # L'etoile de la vignette survolee : le favori d'un clic, sans fiche.
        self.board.favoriteToggled.connect(self.on_board_favorite)
        self.board.pinToggled.connect(self.on_board_pin)
        self.board.pinned_of = self._board_pinned
        self.board.flipNeeded.connect(self.on_board_flip)
        self.viewer.addWidget(self.grid)
        self.viewer.addWidget(self.single)
        self.wall = SplitWall(
            self.cfg["wall_panes"] or DEFAULT_PANES, self.cfg["scroll_seconds"],
            self.viewer, self.cfg["wall_orientation"] or "vertical")
        self.wall.opened.connect(self.open_video_path)
        self.wall.revealRequested.connect(self.reveal_path)
        self.wall.set_muted(bool(self.cfg["muted"]))
        self.wall.set_stay(bool(self.cfg["stay_in_folder"]))
        self.wall.stayChanged.connect(self.set_stay_in_folder)
        self.wall.countChanged.connect(self.set_wall_count)
        self.wall.orientationChanged.connect(self.set_wall_orientation)
        self.wall.fullscreenRequested.connect(self.toggle_wall_fullscreen)
        self.wall.paneFullRequested.connect(self.wall_pane_full)
        self.wall.exitRequested.connect(lambda: self.toggle_wall_fullscreen(False))
        self.wall.siblingRequested.connect(self.wall_sibling)
        self.wall.unseenToggled.connect(self._wall_unseen)
        # L'etoile d'un panneau : le mur demande, la fenetre enregistre.
        self.wall.favoriteToggled.connect(self.on_wall_favorite)
        self.wall.set_favorite_of(self._is_favorite)
        self.wall.set_position_of(self._wall_position)
        # La forme de chaque video compose la mosaique ; chaque video tient
        # entiere dans sa case, ou la remplit.
        self.wall.set_aspect_of(self._aspect_of)
        self.wall.set_fit(self.cfg["wall_fit"] or "show")
        self.wall.fitChanged.connect(self.set_wall_fit)
        # Les reglages du mur — nombre, orientation, non vus, plein ecran — en
        # bout de la ligne de recherche, pas sur une ligne a eux.
        # Sur la premiere ligne, a la suite du chemin : le mur n'a qu'une
        # ligne, et pas de recherche par nom — il pioche au hasard.
        self.wall.controls.setObjectName("wallControls")
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
            ("⧉", "Lecteur flottant : la vidéo par-dessus tout, "
                  "déplaçable et redimensionnable   (Ctrl+L)", self.open_floating),
            ("⛶", "Cinéma : plein écran   (F11 ou double-clic)", self.toggle_cinema),
        ):
            self.single_bar.add_gesture(text, tip, slot)
        self.single_bar.add_star(lambda: self.toggle_favorite_of(
            self.current.path if self.current is not None else ""))
        self.single.progressed.connect(self.single_bar.set_progress)
        # Survoler le trait : l'image a cet instant ; un clic y va.
        self.single_bar.scrub_source = self.single.scrub_source
        self.single_bar.seekRequested.connect(self.single.seek_fraction)
        self.single.progressed.connect(self._note_sheet_position)

        # Le lecteur flottant : une fenetre a part, toujours devant, qui
        # reprend la video de la fiche quand Prisme disparait de l'ecran.
        self.floating = FloatingPlayer(self.cfg["scroll_seconds"])
        self.floating.returnRequested.connect(
            lambda: self.leave_floating(activate=True))
        self.floating.dismissRequested.connect(self.dismiss_floating)
        # Ferme par ✕ : il ne revient pas tout seul avant qu'on soit repasse
        # par Prisme.
        self._float_dismissed = False
        self._float_resume = ("", 0)
        self.floating.stepRequested.connect(self.step)
        self.floating.revealRequested.connect(self.reveal_path)
        self.floating.radialRequested.connect(self.open_float_radial)
        self.floating.favoriteRequested.connect(self.toggle_favorite_of)
        self.floating.favorite_of = self._is_favorite
        self.floating.position_text = self._float_position
        self.floating.keyForwarded.connect(self._float_key)
        self.floating.player.finished.connect(self.on_video_finished)
        # Les memes destinations sur le lecteur flottant : a lui, et au
        # premier plan comme lui. Rattachees a la fenetre de Prisme, elles
        # disparaissaient avec elle quand on la reduit.
        self.float_radial = RadialMenu(self.floating)
        self.float_radial.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.float_radial.chosen.connect(self._radial_chosen)
        self.float_radial.closed.connect(self._radial_closed)
        self._sheet_position = ("", 0)
        # Les photos dont on mesure le format (`_measure_photos`).
        self._photo_prober = None
        self._photo_tried: set = set()
        # Prisme est-il encore a l'ecran ? Un coup d'oeil par demi-seconde,
        # seulement tant qu'il n'a plus le clavier.
        self.float_timer = QTimer(self)
        self.float_timer.setInterval(500)
        self.float_timer.timeout.connect(self._float_check)
        self.single_bar.add_stay(
            "Rester dans ce dossier : ◂ ▸ ne sortent plus du dossier de la vidéo",
            bool(self.cfg["stay_in_folder"]), self.set_stay_in_folder)
        self.single_bar.add_ultra(
            "Ultra tri (Ctrl+U) : clic gauche sur l'image, on garde et on passe à la "
            "suivante ; clic droit, on la supprime (corbeille de séance, Ctrl+Z).",
            self.toggle_ultra)
        self.ultra = False
        self._ultra_at = 0.0
        self._ultra_warned = False
        self.single_bar.add_similar(
            "Similaires : ▸ et Espace ne proposent plus que les vidéos qui ressemblent "
            "à celle-ci (l'index du Labo IA), tous dossiers confondus. Décocher : "
            "retour aux suivantes habituelles.", self.set_similar)
        self._similar = None
        # La vitesse du diaporama, a la molette, sans passer par le menu.
        self.single_bar.add_speed(int(self.cfg["slideshow_seconds"] or 6),
                                  self.turn_slideshow_seconds)

        # Le lecteur de cote : on y envoie une video d'un clic droit, et la
        # planche continue de vivre a gauche — on peut changer de page, cocher,
        # ranger, pendant que la video se lit.
        self.aside = QWidget(sort_page)
        # Rien dans le lecteur n'a de largeur propre : sans plancher, il
        # s'ouvrait a zero pixel tant qu'aucune largeur n'etait retenue, et un
        # clic droit semblait ne rien faire. Le separateur ne le replie pas
        # en dessous (`setChildrenCollapsible(False)`).
        self.aside.setMinimumWidth(self.ASIDE_MIN_WIDTH)
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
        self.aside_player.nextRequested.connect(lambda: self.aside_step(1))
        # Double-clic sur la video de cote : la meme que ⛶, en plein ecran.
        self.aside_player.cinemaRequested.connect(self.aside_fullscreen)
        aside_box.addWidget(self.aside_player, 1)

        # Le bandeau flotte sur l'image et ne parait qu'au survol : une
        # rangee permanente coutait quarante pixels de hauteur, et c'est la
        # hauteur qui fait voir une video.
        self.aside_bar = OverBar(self)
        self.aside_title = self.aside_bar.name
        for text, tip, slot in (
            ("◂", "Précédente", lambda: self.aside_step(-1)),
            ("⏯", "Pause, ou reprendre   (un clic sur l'image)",
             self.aside_player.toggle_pause),
            ("▸", "Suivante   (Espace)", lambda: self.aside_step(1)),
            ("⛶", "Plein écran   (double-clic sur l'image)", self.aside_fullscreen),
            ("✕", "Fermer le lecteur", self.close_aside),
        ):
            self.aside_bar.add_gesture(text, tip, slot)
        self.aside_bar.add_star(
            lambda: self.toggle_favorite_of(self.aside_current))
        self.aside_player.progressed.connect(self.aside_bar.set_progress)
        self.aside_bar.scrub_source = self.aside_player.scrub_source
        self.aside_bar.seekRequested.connect(self.aside_player.seek_fraction)
        self.aside_bar.add_stay(
            "Rester dans ce dossier : ◂ ▸ ne sortent plus du dossier de la vidéo",
            bool(self.cfg["stay_in_folder"]), self.set_stay_in_folder)
        self.aside_bar.add_similar(
            "Similaires : ▸ et Espace ne proposent plus que les vidéos qui ressemblent "
            "à celle-ci (l'index du Labo IA), tous dossiers confondus. Décocher : "
            "retour aux suivantes habituelles.", self.set_aside_similar)
        self._aside_similar = None

        # Un battement suffit a savoir si la souris est sur l'image : le
        # widget video natif ne rend pas les evenements de survol.
        self.aside_watch = QTimer(self)
        self.aside_watch.setInterval(120)
        self.aside_watch.timeout.connect(self._watch_bars_tick)
        self.aside_watch.start()
        # Il ne bat que tant qu'une image joue a l'ecran : huit reveils par
        # seconde pour une planche ou une fenetre reduite, le processeur ne
        # dormait jamais. Il se rendort de lui-meme (`_watch_bars`) et se
        # reveille quand ce qu'on voit change.
        self.viewer.currentChanged.connect(self._wake_watch)
        self.stack.currentChanged.connect(self._wake_watch)
        self.aside.hide()
        # La carte ouverte a cote, par son identifiant : sa position ne vaut
        # que dans la liste d'ou on l'a ouverte, et un filtre ou un tri la
        # deplacent -- ◂ ▸ et ⛶ visaient alors une autre carte.
        self.aside_item_id = ""
        self._aside_hint = -1
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
        # L'epingle, a cote de l'etoile : en tete de la liste, dans son onglet.
        self.pin_button = PinButton(19, sort_page)
        self.pin_button.toggledPin.connect(self.pin_current)

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
        # L'etoile et l'epingle vivent sur la ligne du haut, juste apres ce
        # qu'on sait de l'element (poids, nombre, date) : en bas a droite, on
        # ne les trouvait pas.
        self._header_layout.insertWidget(1, self.stars, 0)
        self._header_layout.insertWidget(2, self.pin_button, 0)
        bottom.addWidget(self.next_button, 0)
        layout.addWidget(self.bottom_bar)
        self.nav_row = self.bottom_bar

        # Le pense-bete des touches vit dans la fiche d'aide (F1), tiree de la
        # table qui fait foi : recopie ici en infobulle, il ecrasait celle de
        # « ⋯ » et vieillissait a chaque raccourci ajoute.

        self.stack.addWidget(sort_page)

        self.done_page = DonePage(self)
        self.done_page.back.clicked.connect(self.leave_done)
        self.done_page.rescan.clicked.connect(self.refresh_root)
        self.done_page.change.clicked.connect(self.choose_root)
        self.stack.addWidget(self.done_page)
        # La page de repli, prete des le depart : la chercher au moment ou
        # l'on en a besoin serait trop tard.
        self.quiet_page = QuietPage(self, self.cfg["quiet_cover"] or "update")
        self.quiet_page.back.clicked.connect(self.leave_quiet)
        self.quiet_page.leave.connect(self.leave_quiet)
        self.quiet_page.titleChanged.connect(self._quiet_title)
        self.stack.addWidget(self.quiet_page)
        # Le cadenas : aussi neutre que la page de repli. Le bon code ramene
        # la ou l'on etait ; « Retour » ou Echap, la page neutre.
        self.lock_page = LockPage(self.cfg, self)
        self.lock_page.unlocked.connect(self._leave_quiet_now)
        self.lock_page.hide_me.connect(self.show_quiet_page)
        self.stack.addWidget(self.lock_page)

        # Rien ne disait que des apercus etaient en fabrication : devant une
        # planche qui ne se remplit pas, on ne sait pas s'il faut attendre ou
        # si quelque chose est bloque. Cette barre repond a la question.
        self.activity_timer = QTimer(self)
        self.activity_timer.setInterval(400)
        self.activity_timer.timeout.connect(self._show_activity)
        self.activity_timer.start()

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

        # Les couvertures des dossiers changent d'elles-memes (covers.py) :
        # la prochaine se prepare en fond, en cedant la place a ce qu'on
        # regarde, et la carte ne change que quand elle est prete -- jamais
        # d'attente a l'affichage. Seule la page affichee travaille, et
        # seulement fenetre active.
        COVERS.period = float(self.cfg["cover_period_s"] or 0) or COVERS.period
        COVERS.make = self._make_cover
        COVERS.wait_quiet = media.FOREGROUND.wait
        self._cover_applied: dict = {}      # dossier -> couverture posee sur sa carte
        self.cover_timer = QTimer(self)
        self.cover_timer.setInterval(2000)
        self.cover_timer.timeout.connect(self._turn_covers)
        if self.cfg["cover_period_s"]:
            self.cover_timer.start()

    # ------------------------------------------------------------------
    # Racine et analyse
    # ------------------------------------------------------------------
    # -- plusieurs racines -------------------------------------------------------
    def _init_roots(self) -> None:
        """Les racines de la configuration ; la premiere fois, celle qu'on
        avait deja. Puis on les sonde regulierement : un disque qu'on branche
        ou debranche se voit dans les vingt secondes."""
        from . import roots
        chosen = [str(p) for p in (self.cfg["roots"] or []) if p]
        current = self.cfg["root"]
        if not chosen and current and not roots.is_union(current):
            chosen = [str(canon_root(current))]
            self.cfg["roots"] = chosen
        roots.set_roots(chosen)
        roots.set_off(self.cfg["roots_off"] or [])
        self._reachable_roots = None
        self._roots_timer = QTimer(self)
        self._roots_timer.setInterval(20000)
        self._roots_timer.timeout.connect(self._watch_roots)
        self._roots_timer.start()
        QTimer.singleShot(1500, self._watch_roots)

    def _watch_roots(self) -> None:
        """Sonde les racines hors du fil de l'interface."""
        if getattr(self, "_roots_probing", False) or self._closing:
            return
        from . import roots
        from .tunnel import Chore
        self._roots_probing = True
        Chore(lambda: frozenset(roots.check_all()), self, fallback=None,
              then=self._roots_seen).start()

    def _roots_seen(self, found) -> None:
        from . import roots
        self._roots_probing = False
        if found is None:
            return
        before, self._reachable_roots = self._reachable_roots, found
        self._show_root_button()
        if before is None or before == found:
            return
        gone = [roots.label(p) for p in before - found]
        back = [roots.label(p) for p in found - before]
        if gone:
            self.show_banner(f"{', '.join(gone)} : injoignable — ses vidéos sont masquées "
                             "jusqu'à son retour.", "info")
        if back:
            self.show_banner(f"{', '.join(back)} : de retour — ses vidéos réapparaissent.",
                             "done")
        if roots.is_union(self.root) and not self.scanning:
            # La reunion se recompose : les dossiers de la racine partie
            # quittent l'ecran, ceux de la racine revenue y reviennent.
            self._launch_scan()

    def _show_root_button(self) -> None:
        """« ⌂ 2/2 » : combien de racines on regarde, sur combien. Le nom de
        la racine est deja dans le fil d'Ariane, juste a cote : le bouton le
        repetait."""
        from . import roots
        top = self.top_root()
        total = len(roots.roots())
        if top is None or total < 2:
            self.root_button.setText("⌂  Racines")
            self.root_button.setToolTip("Les racines : en ajouter, choisir celles qu'on regarde")
            return
        shown = len(roots.members(top)) if roots.is_union(top) else 1
        self.root_button.setText(f"⌂  {shown}/{total}")
        self.root_button.setToolTip(
            f"{shown} racine{'s' if shown > 1 else ''} affichée{'s' if shown > 1 else ''} "
            f"sur {total} : cliquer pour cocher celles qu'on regarde")

    def _root_ticks(self) -> set:
        """Les racines cochees : celles qu'on regarde en ce moment."""
        from . import roots
        top = self.top_root()
        if top is None:
            return set()
        if roots.is_union(top):
            return {os.path.normcase(str(p)) for p in roots.chosen_on()}
        return {os.path.normcase(str(p)) for p in roots.roots()
                if os.path.normcase(str(p)) == os.path.normcase(str(top))}

    def _fill_root_menu(self) -> None:
        """Une case par racine : cochee, on la regarde. Le menu reste ouvert
        le temps de cocher ; la vue suit a sa fermeture."""
        from PySide6.QtWidgets import QCheckBox, QWidgetAction
        from . import roots
        menu = self.root_menu
        menu.clear()
        reach = self._reachable_roots
        chosen = roots.roots()
        ticks = self._root_ticks()
        self._root_boxes = []
        if chosen:
            head = menu.addAction("Racines affichées")
            head.setEnabled(False)
        for root in chosen:
            ok = reach is None or str(root) in reach
            box = QCheckBox(roots.label(root) + ("" if ok else "  — injoignable"), menu)
            box.setObjectName("rootTick")
            box.setChecked(os.path.normcase(str(root)) in ticks)
            box.setEnabled(ok)
            box.setToolTip(str(root) if ok else
                           f"{root}\nDébranchée ou éteinte : elle revient d'elle-même.")
            box.toggled.connect(lambda _on, b=box: self._keep_one_tick(b))
            holder = QWidgetAction(menu)
            holder.setDefaultWidget(box)
            menu.addAction(holder)
            self._root_boxes.append((root, box))
        if chosen:
            menu.addSeparator()
        menu.addAction("Ajouter des racines…", self.add_root).setToolTip(
            "Un ou plusieurs dossiers d'un coup (Ctrl ou Maj pour en choisir plusieurs)")
        if chosen:
            drop = menu.addMenu("Retirer une racine")
            for root in chosen:
                drop.addAction(roots.label(root),
                               lambda r=root: self.remove_root(r)).setToolTip(str(root))

    def _keep_one_tick(self, box) -> None:
        """Au moins une racine reste cochee : sinon il n'y aurait rien a voir."""
        boxes = [b for _r, b in getattr(self, "_root_boxes", [])]
        if not any(b.isChecked() for b in boxes):
            box.blockSignals(True)
            box.setChecked(True)
            box.blockSignals(False)

    def _root_menu_closed(self) -> None:
        """Le menu se ferme : on regarde les racines cochees."""
        from . import roots
        pairs = getattr(self, "_root_boxes", [])
        if not pairs:
            return
        wanted = [root for root, box in pairs if box.isChecked()]
        self._root_boxes = []
        if not wanted:
            return
        if {os.path.normcase(str(p)) for p in wanted} == self._root_ticks():
            return
        off = [str(root) for root, _box in pairs if root not in wanted]
        self.cfg["roots_off"] = off
        self.cfg.save_soon()
        roots.set_off(off)
        # Une seule : on l'ouvre elle-meme ; plusieurs : leur reunion.
        self.open_root_async(wanted[0] if len(wanted) == 1 else Path(roots.UNION),
                             MODE_FOLDERS)

    def add_root(self) -> None:
        """Une racine de plus : un disque, un dossier du PC, un partage."""
        from . import roots
        from .widgets import pick_folders
        start = str(self.top_root()) if self.top_root() is not None and not roots.is_union(
            self.top_root()) else ""
        chosen = pick_folders(self, "Ajouter une ou plusieurs racines", start)
        if not chosen:
            return
        have = {os.path.normcase(str(p)) for p in roots.roots()}
        fresh = [str(canon_root(p)) for p in chosen
                 if os.path.normcase(str(canon_root(p))) not in have]
        if not fresh:
            return self.show_banner("Ces racines y étaient déjà.", "quiet")
        self.cfg["roots"] = [str(p) for p in roots.roots()] + fresh
        self.cfg.save_soon()
        roots.set_roots(self.cfg["roots"])
        self.show_banner(f"{len(fresh)} racine(s) ajoutée(s) : le bouton ⌂ coche celles "
                         "qu'on regarde.", "done")
        self._reachable_roots = None
        self._watch_roots()
        if len(roots.roots()) > 1 and not roots.is_union(self.top_root()):
            self.open_root_async(Path(roots.UNION), MODE_FOLDERS)
        elif roots.is_union(self.root) and not self.scanning:
            self._launch_scan()

    def remove_root(self, root) -> None:
        """Retire une racine de la liste ; rien ne bouge sur le disque."""
        from . import roots
        keep = [str(p) for p in roots.roots()
                if os.path.normcase(str(p)) != os.path.normcase(str(root))]
        self.cfg["roots"] = keep
        self.cfg["roots_off"] = [p for p in (self.cfg["roots_off"] or [])
                                 if os.path.normcase(str(p)) != os.path.normcase(str(root))]
        self.cfg.save_soon()
        roots.set_roots(keep)
        roots.set_off(self.cfg["roots_off"])
        self.show_banner(f"« {roots.label(root)} » retirée de la liste des racines (rien "
                         "n'a bougé sur le disque).", "quiet")
        if roots.is_union(self.root) and not self.scanning:
            self._launch_scan()
        self._show_root_button()

    def open_at_launch(self) -> None:
        """Au lancement : la derniere racine, sur « Dossiers », sans accueil.

        Il fallait double-cliquer la meme racine a chaque ouverture, alors que
        l'index la connaissait deja. Le dossier est interroge hors du fil de
        l'interface -- un NAS endormi peut mettre des secondes a repondre. Un
        dossier parti ramene a l'accueil, en le disant ; injoignable, on
        l'ouvre quand meme : l'index sait ce qu'il y avait.
        Le raccourci du repli, valable partout, se pose ici aussi.
        """
        self.arm_global_quiet()
        self.renew_licence()
        root = self.cfg["root"]
        if not root or self.root is not None or self._closing:
            return
        self.open_root_async(canon_root(root), MODE_FOLDERS, launch=True)

    def open_root_async(self, path, mode: str = "", launch: bool = False) -> None:
        """Ouvre une racine sans jamais attendre le NAS sur le fil de l'interface.

        Le seul chemin du lancement, de l'accueil, des racines recentes et de
        « Changer de racine… ». Un NAS en veille met des secondes, parfois des
        dizaines, a repondre : l'accueil restait muet, l'air d'avoir oublie
        la racine, et la redouble-cliquer figeait la fenetre tout ce temps.
        On le dit, et l'accueil attend la reponse sans rien relancer.
        """
        path = Path(path)
        opening = self._opening
        if opening is not None and os.path.normcase(str(opening[0])) == \
                os.path.normcase(str(path)):
            return          # deja demandee : la meme reponse servira
        from .tunnel import Chore
        self._opening_serial += 1
        serial = self._opening_serial
        self._opening = (path, serial)
        self.welcome.recent.setEnabled(False)
        self.welcome.choose.setEnabled(False)
        self._opening_text = (f"Ouverture de « {path} »… le NAS peut mettre "
                              "quelques secondes à répondre.")

        def tell() -> None:
            # Seulement si la reponse tarde : une racine qui repond tout de
            # suite n'a rien a annoncer, et le message effacait ceux du
            # lancement.
            if self._opening is not None and self._opening[1] == serial:
                self.show_banner(self._opening_text, "quiet", seconds=60)

        QTimer.singleShot(300, tell)
        from .nasreach import server_of
        server = server_of(path)
        remembered = dict(self.cfg["nas_addresses"] or {}).get(server.lower(), "")

        def reached(result) -> None:
            state, ip = result
            if ip and server:
                # On retient l'adresse du NAS, pour le retrouver tout de suite
                # le jour (ou le lancement suivant) ou un VPN cachera son nom.
                known = dict(self.cfg["nas_addresses"] or {})
                if known.get(server.lower()) != ip:
                    known[server.lower()] = ip
                    self.cfg["nas_addresses"] = known
                    self.cfg.save_soon()
            self._launch_into(path, state, serial, mode, launch, nas_ip=ip)

        Chore(lambda: _reach_state(path, remembered), self,
              fallback=("injoignable", ""), then=reached).start()

    def _fill_recent_menu(self) -> None:
        """Les racines choisies ces derniers temps, la courante cochee."""
        menu = self.recent_menu
        menu.clear()
        here = os.path.normcase(str(self.origin)) if self.origin else ""
        for root in self.cfg["recent_roots"]:
            action = menu.addAction(root)
            action.setCheckable(True)
            action.setChecked(os.path.normcase(root) == here)
            action.triggered.connect(
                lambda _c=False, root=root: self.open_root_async(canon_root(root)))
        if menu.isEmpty():
            menu.addAction("Aucune pour l'instant").setEnabled(False)

    def _launch_into(self, path, state: str, serial: int | None = None,
                     mode: str = MODE_FOLDERS, launch: bool = True,
                     nas_ip: str = "") -> None:
        """La reponse du NAS est la : on ouvre, si c'est encore voulu."""
        if not shiboken6.isValid(self) or self._closing:
            return
        opening = self._opening
        if serial is not None and (opening is None or opening[1] != serial):
            return                      # une autre racine demandee entre-temps
        self._opening = None
        self.welcome.recent.setEnabled(True)
        self.welcome.choose.setEnabled(True)
        if self.banner.isVisible() and self.banner.text() == self._opening_text:
            self.banner.hide()
        if launch and self.root is not None:
            return                      # on a choisi autre chose entre-temps
        if state == "cache":
            return self._offer_nas_repair(path, nas_ip, mode)
        if state == "absent":
            self.show_banner(
                f"La dernière racine, « {path} », n'existe plus : choisissez-en "
                "une autre." if launch else f"« {path} » n'existe plus.",
                "error", seconds=8)
            return
        self.start_root(path, mode, new_origin=True, state=state)

    def _offer_nas_repair(self, path, ip: str, mode: str) -> None:
        """Le NAS repond a son adresse, plus a son nom (un VPN, le plus
        souvent) : proposer d'ajouter la ligne au fichier `hosts` de Windows."""
        from . import nasreach
        name = nasreach.server_of(path)
        box = QMessageBox(QMessageBox.Question, "Le NAS est caché", "", parent=self)
        fix = box.addButton("Réparer", QMessageBox.AcceptRole)
        box.addButton("Plus tard", QMessageBox.RejectRole)
        box.setDefaultButton(fix)
        box.setText(
            f"Le NAS « {name} » est bien là (il répond à l'adresse {ip}), mais "
            f"son nom ne répond plus : un VPN, le plus souvent, remplace le "
            f"serveur de noms de votre box par le sien, qui ne connaît pas les "
            f"appareils de la maison.\n\n"
            f"Prisme peut le réparer : il indique à Windows que « {name} » est "
            f"à l'adresse {ip}. Windows va vous demander l'autorisation "
            f"(répondez « Oui »). Rien d'autre ne change.\n\nRéparer maintenant ?")
        box.exec()
        if box.clickedButton() is not fix:
            self.show_banner(
                f"« {name} » reste introuvable par son nom tant que le VPN est "
                f"actif. Coupez-le, ou acceptez la réparation au prochain "
                f"lancement.", "error", seconds=10)
            return
        from .tunnel import Chore
        self.show_banner("Réparation en cours… (autorisation Windows)", "quiet",
                         seconds=30)

        def repaired(error: str) -> None:
            if error:
                self.show_banner(
                    f"Réparation impossible ({error}). À la main : ajoutez la "
                    f"ligne « {ip}   {name} » au fichier "
                    f"{nasreach.hosts_path()}.", "error",
                    seconds=20)
                return
            self.show_banner(f"Réparé : « {name} » est de nouveau trouvable, même "
                             f"avec le VPN.", "info", seconds=6)
            self.open_root_async(path, mode)

        Chore(lambda: nasreach.repair(name, ip), self,
              fallback="la réparation a échoué", then=repaired).start()

    def arm_global_quiet(self) -> bool:
        """Ctrl+Alt+K, depuis n'importe quelle fenetre (`_GlobalQuietKey`)."""
        if self.global_quiet is None:
            self.global_quiet = _GlobalQuietKey(self)
            self.global_quiet.pressed.connect(self.quiet_now)
        return self.global_quiet.start()

    def choose_root(self) -> None:
        start = self.cfg["root"] or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choisir le dossier racine", start)
        if chosen:
            # « Réseau › AS1104T › partage » ou « X: » : la meme racine, donc
            # les memes cles d'index, de favoris et de vignettes.
            self.open_root_async(canon_root(chosen))

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
                   force: bool = False, state: str = "",
                   remember: bool = True, departure: dict | None = None,
                   probe_empty: bool = True) -> None:
        """Ouvre ce dossier : ce que l'index en sait d'abord, puis la relecture.

        `state` : l'appelant vient de regarder si le dossier est la (« ok »,
        « injoignable ») -- inutile de redemander au NAS, chaque question y
        coute un aller-retour.
        `remember` a faux : on revient en arriere (Alt+←), l'endroit quitte
        ne s'empile pas dans l'historique.
        `departure` : l'endroit quitte, pris par l'appelant avant qu'il ne
        touche aux niveaux (`_here`) ; sans lui, l'historique retenait le
        niveau qu'on venait d'empiler, et Alt+← laissait un niveau fantome.
        `probe_empty` a faux : l'appelant regarde lui-meme, hors du fil de
        l'interface, si un dossier inconnu de l'index est vide.
        """
        if root is None:
            return
        if not self._random_launching:
            self._random_on = False
        state = state or _folder_state(root)
        if state == "absent":
            QMessageBox.warning(self, "Dossier introuvable", f"{root} n'existe plus.")
            return
        # Injoignable, on continue : l'index sait ce qu'il y avait, et la
        # relecture dira que le NAS ne repond pas, puis reessaiera. Refuser
        # d'ouvrir annoncait « n'existe plus » une racine qui dormait.
        self.retry_timer.stop()
        self._retry_step = 0
        self.stop_scan()
        moving = self.root is None or Path(root) != self.root
        if moving and not self.aside_playlist and not self.aside.isHidden():
            # La video ouverte a cote appartient a la liste qu'on quitte, comme
            # en changeant d'onglet : elle continuait hors du dossier, et ses
            # ◂ ▸ tombaient sur les cartes d'une autre liste. Une playlist, elle,
            # ne doit rien a la liste : elle continue.
            self.close_aside()
        if remember and self.root is not None and moving:
            self._remember(departure or self._here())
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
        # L'onglet ne suit pas le mode, et le mode choisi pour ce dossier-ci
        # ne decide pas du suivant : un dossier sans sous-dossier ouvert a plat
        # faisait lister a plat -- et relire recursivement sur le NAS -- tous
        # les dossiers ouverts ensuite sous « Dossiers ».
        self.mode = mode or self.mode_for_content()
        self.all_items = []
        self.items = []
        # Une liste de passage (doublons) ne survit pas a un autre dossier.
        self._leave_transient()
        self._scan_top = False
        self.board.empty.setText("Rien à afficher ici.")
        self.plans = {}
        # L'historique d'annulation n'est pas remis a zero : il vaut pour la
        # seance. Les compteurs, eux, ne font que le bilan de ce dossier-ci.
        self.index = 0
        self.stats = {"moved": 0, "deleted": 0, "skipped": 0}
        self.preview.cancel_all()

        # Ce qu'on savait de cette racine, sans rien demander au disque. C'est
        # tout le propos : l'ecran se remplit avant que la question « qu'y a-t-il
        # ici » ne parte sur le reseau. Meme pour une relecture forcee
        # (Ctrl+R) : la planche restait vide des minutes durant ; la liste
        # connue reste a l'ecran et la relecture, qui ne se fie a aucune
        # date, la corrige au fil de l'eau.
        indexed = self.cfg["use_scan_cache"]
        known = (cached_items(self.root, self.mode, self.cfg["expand_parents"])
                 if indexed else [])
        empty = False
        if not known and self.mode == MODE_FOLDERS and state == "ok" and probe_empty:
            try:
                # Les dossiers « + » comptent : une racine qui n'est faite
                # que d'eux s'ouvrait a plat, sous « Dossiers ».
                empty = not list_entries(self.root, MODE_FOLDERS,
                                         self.cfg["skip_hidden"], False,
                                         strict=True,
                                         keep_parents=self.cfg["expand_parents"])
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

        from . import roots as _roots
        top = self.top_root()
        union = _roots.is_union(top)
        members = _roots.members(top) if top is not None else []
        self.preview.tune_for(members[0] if union and members else self.root)
        # Plusieurs racines : la corbeille de seance se range sur celle de
        # chaque element (`trash.folder_for`).
        self.trash.set_base(None if union else top)
        if union:
            # Ce qui repond a l'ouverture : la reference du prochain sondage.
            self._reachable_roots = frozenset(str(m) for m in members)
        for member in (members if union else [top]):
            self._look_for_leftovers(member)
        self._show_root_button()
        # Les « Racines récentes » sont les racines choisies, pas chaque
        # dossier traverse : huit descentes suffisaient a en chasser X:\, et
        # la « racine » rouverte au lancement etait le dernier sous-dossier vu.
        origin = str(self.origin) if self.origin is not None else str(self.root)
        if _roots.is_union(origin):
            if self.cfg["root"] != origin:
                self.cfg["root"] = origin
                self.cfg.save_soon()
        elif self.cfg["root"] != origin or (
                self.cfg["recent_roots"][:1] != [origin]):
            self.cfg.push_recent_root(origin)
            # Plus tard, et d'un bloc : l'ecriture attend que le disque l'ait
            # prise (fsync).
            self.cfg.save_soon()
            self.welcome.set_recent(self.cfg["recent_roots"])

        # Le fil part de la racine choisie, et non du premier niveau empile :
        # changer d'onglet vidait la pile, et le fil se reduisait alors au seul
        # dossier courant — on ne pouvait plus remonter.
        self._list_leaf = ""
        self.crumbs.set_path(self._origin_for(self.root), self.root)
        self._apply_selectors()
        # Seulement si les touches ont change (destinations, ou libelle du
        # bouton rouge entre disque local et NAS) : une quinzaine de boutons
        # detruits et recrees a chaque dossier ouvert, pour les memes.
        self._rebuild_commands()
        # En planche, la vue reste la planche : basculer sur la fiche le temps
        # de l'analyse faisait clignoter l'affichage a chaque changement d'onglet.
        if self.tab == TAB_SPLIT:
            # Sous l'onglet « Mur », le mur reste a l'ecran quoi qu'on relise
            # (Ctrl+R, Alt+←…) : une planche vide prenait sa place.
            self.viewer.setCurrentWidget(self.wall)
        else:
            self.viewer.setCurrentWidget(
                self.board if self.browsing
                else (self.grid if self.mode == MODE_FOLDERS else self.single)
            )
            if self.browsing:
                self.board.set_items([], self.ratings.get)
        if self._goto_page(PAGE_SORT):
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
            # Les fichiers poses a cote des sous-dossiers : une entree
            # « (sans dossier) » en tete, au lieu de disparaitre.
            own_loose=True,
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
        if self._page() == PAGE_WELCOME:
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
        dans la seance, ils se restaurent comme les autres — et sinon, une
        fois vus dans Ctrl+B, partent a la fermeture comme ce qu'on ecarte
        aujourd'hui. Jamais vus, ils restent sur le disque.
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
        names = ", ".join(f"« {entry.name} »" for entry in found[:3])
        if len(found) > 3:
            names += "…"
        # Une alerte qu'on clique, et non un « info » de quatre secondes que
        # la fin de l'analyse remplacait aussitot. Rien ne part sans avoir
        # ete vu : ces elements restent sur le disque tant que Ctrl+B ne les
        # a pas montres (`TrashEntry.seen`).
        self.show_banner(
            f"{len(found)} élément(s) laissé(s) dans la corbeille par une séance "
            f"interrompue : {names}. Cliquez ici (ou Ctrl+B) pour les voir et "
            "restaurer ce qu'il faut garder ; une fois vus, ils partiront à la "
            "fermeture comme le reste."
            + (f" L'origine de {guessed} d'entre eux est devinée." if guessed
               else ""), "error", action=self.open_trash, seconds=20, keep=True)

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
        if self.mode == MODE_FOLDERS and self._at_tag_list():
            # Les mots-cles n'attendent plus la fin de la relecture : ils se
            # tirent de ce que l'index connait deja, et se referont a la fin.
            # A la racine seulement : un vrai dossier ouvert sous cet onglet
            # montre son contenu, et non les mots de toute la collection.
            self._add_tag_items()
        elif self.tab == TAB_FAVS and self._scan_top:
            self.show_favorites()
        if self._scan_top:
            # La collection est la, telle que l'index la connait : la liste de
            # l'onglet Videos se prepare en fond, avant qu'on la demande --
            # un instant apres, que l'ecran qui s'ouvre passe d'abord.
            QTimer.singleShot(1500, self._prepare_flat)

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
        restored = list(self._restored_items())
        for entry_path, item in restored:
            item.status = ""
            item.status_detail = ""
            self._settle_board(item)
        if restored:
            # Ses videos reviennent dans les listes tirees de la collection.
            self._touch(sortable=False)
        self.update_counter()
        # Sur la planche, on y reste : redessiner la fiche courante la
        # remplacait par une video qu'on ne regardait pas.
        if self.current is not None and not self.browsing:
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
        """Restaure depuis la corbeille : l'etoile revient avec l'element.

        Et Ctrl+Z oublie cette suppression : l'annuler encore echouait sur un
        element deja revenu, et masquait l'annulation d'avant.
        """
        self.ratings.rename(stored, target)
        from .dupes_memory import NOT_DUPES
        NOT_DUPES.renommer(stored, target)
        gone = os.path.normcase(str(stored))
        self.history = [entry for entry in self.history
                        if not (entry.dst is not None
                                and os.path.normcase(str(entry.dst)) == gone)]

    def _trash_purged(self, paths: list) -> None:
        """Detruits pour de bon : leurs favoris et leurs « pas des doublons »
        n'ont plus d'objet."""
        from .dupes_memory import NOT_DUPES
        for path in paths:
            self.ratings.forget_under(path)
            NOT_DUPES.oublier(path)

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
        """Un clic : compter, puis verifier les vignettes. Les deux chiffres qui tranchent.

        Recliquer pendant l'un ou l'autre arrete : sur tout le NAS, cela dure
        des minutes, et rien ne permettait d'y renoncer.
        """
        if self.root is None:
            return
        if self.counter is not None or self.audit is not None:
            self._audit_after_count = False
            if self.counter is not None:
                self.count_videos()
            if self.audit is not None:
                self.audit_thumbs()
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
            if shiboken6.isValid(self) and not self._closing:
                self._show_activity()
        thread.finished.connect(finished)
        # Ce qui part se dit tout de suite, a cote du fil d'Ariane, et non
        # au battement suivant.
        QTimer.singleShot(0, self._show_activity)

    def count_videos(self) -> None:
        """Compte les videos sous la racine, et le dit en clair. Recliquer arrete."""
        if self.root is None:
            return
        if self.counter is not None:
            self._count_stopped = True
            self.counter.stop()
            self.show_banner("Comptage : arrêt demandé…", "quiet")
            return
        self._count_stopped = False
        top = self.top_root()
        self.counter = VideoCount(top, self.cfg["skip_hidden"], self)
        self._own_thread(self.counter, "counter")
        # L'avancement va a cote du fil d'Ariane, pas au bandeau : trois
        # fois par seconde pendant des minutes, il ecrasait tout autre
        # message -- une erreur comprise -- avant qu'on ait pu le lire.
        self.counter.progress.connect(
            lambda n: self._task_said("counter", f"comptage {self._thousands(n)}…"))
        self.counter.counted.connect(lambda n: self._told_count(top, n))
        self.counter.start()
        self.show_banner(f"Comptage des vidéos sous {top}…", "info")

    def _told_count(self, top, total: int) -> None:
        self.counter = None
        if self._count_stopped:
            # Un compte interrompu n'est pas celui de la collection.
            self._count_stopped = False
            self._audit_after_count = False
            self._refresh_state()
            self.show_banner(f"Comptage arrêté : {total} vidéo(s) vues jusque-là.",
                             "quiet")
            return
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
        Recliquer arrete.
        """
        if self.root is None:
            return
        if self.audit is not None:
            self._audit_stopped = True
            self.audit.stop()
            self.show_banner("Vérification : arrêt demandé…", "quiet")
            return
        self._audit_stopped = False
        top = self.top_root()
        self.audit = ThumbAudit(top, self.cfg["thumb_width"],
                                self.cfg["skip_hidden"], self)
        self._own_thread(self.audit, "audit")
        self.audit.progress.connect(
            lambda seen, ready: self._task_said(
                "audit", f"vérification {ready} / {seen}"))
        self.audit.done.connect(lambda seen, ready: self._told_audit(seen, ready))
        self.audit.start()
        self.show_banner("Vérification des vignettes déjà fabriquées…", "info")

    def _told_audit(self, seen: int, ready: int) -> None:
        self.audit = None
        if self._audit_stopped:
            self._audit_stopped = False
            self._refresh_state()
            self.show_banner(f"Vérification arrêtée : {ready} vignette(s) sur "
                             f"{seen} vidéo(s) vues jusque-là.", "quiet")
            return
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

    # Les trois recherches de doublons, et ce qu'on en dit.
    DUPES_KINDS = {"size": "même taille", "image": "même image",
                   "sigs": "d'après les empreintes"}

    def find_duplicates(self, by_image: bool = False) -> None:
        """Rassemble les vidéos de taille rigoureusement identique.

        Rien n'est supprimé : les groupes s'affichent comme une planche
        ordinaire, les membres d'un même groupe côte à côte. On coche ce dont on
        ne veut plus et l'on se sert du bouton « Supprimer » habituel, qui passe
        par la corbeille de session — donc réversible.
        """
        if self.dupes is not None:
            self._stop_dupes("image" if by_image else "size")
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
        self._progress_text("recherche de doublons…")
        self.progress.show()
        self.show_banner(banner, "info")

    def _dupes_kind(self) -> str:
        """La recherche en cours : « size », « image » ou « sigs »."""
        if getattr(self, "_dupes_from_sigs", False):
            return "sigs"
        return "image" if getattr(self, "_dupes_by_image", False) else "size"

    def _stop_dupes(self, wanted: str = "") -> None:
        """Recliquer arrete : la recherche rendra une liste vide, qu'il ne
        faut pas annoncer comme « aucun doublon ».

        Une autre recherche demandee pendant ce temps arrete celle qui tourne,
        puis part d'elle-meme : l'autre entree du menu arretait la recherche
        en cours sans rien lancer, et sans rien dire.
        """
        self._dupes_stopped = True
        self.dupes.stop()
        running = self._dupes_kind()
        if wanted and wanted != running:
            self._dupes_next = wanted
            self.show_banner(
                f"La recherche « {self.DUPES_KINDS[running]} » s'arrête ; "
                f"celle « {self.DUPES_KINDS[wanted]} » part dès qu'elle a "
                "rendu la main.", "quiet")
            return
        self._dupes_next = None
        self.show_banner("Recherche de doublons : arrêt demandé…", "quiet")

    def _launch_dupes(self, kind: str) -> None:
        if kind == "sigs":
            self.duplicates_from_sigs()
        else:
            self.find_duplicates(by_image=kind == "image")

    def on_dupes_progress(self, seen: int) -> None:
        if self.scanning:
            return
        self._progress_text(f"doublons : {self._thousands(seen)}",
                            f"Recherche de doublons : {self._thousands(seen)} "
                            "vidéo(s) examinée(s)…")
        self.progress.show()

    def on_dupes_found(self, groups: list) -> None:
        """Le resultat d'une recherche : propose, jamais impose.

        Il arrive des minutes apres le lancement, sur tout le NAS. Il
        remplacait d'office ce qu'on regardait -- une video, le mur, une
        autre page -- et coupait au passage la relecture de la collection.
        Il attend desormais qu'on le demande : un bandeau qu'on clique, ou
        « ⋯ › Doublons ».

        Une comparaison d'etape (`interim`, pendant les empreintes) ne dit
        rien quand elle ne trouve rien, et ne parle que si elle trouve plus
        qu'avant : ce sont des nouvelles en cours de route, pas un verdict.
        """
        scan = self.dupes
        self.dupes = None
        interim = getattr(scan, "interim", False)
        if not interim:
            if not self.scanning:
                self.progress.hide()
            self._progress_text("%v / %m analysés")
        if getattr(self, "_dupes_stopped", False):
            self._dupes_stopped = False
            following, self._dupes_next = self._dupes_next, None
            if following:
                return self._launch_dupes(following)
            if interim:
                return
            kept = self._pending_dupes
            self.show_banner(
                "Recherche de doublons arrêtée." + (
                    f" Les {len(kept[0])} groupe(s) trouvé(s) jusque-là restent "
                    "dans « ⋯ › Doublons »." if kept else ""), "quiet")
            return
        if interim:
            return self._interim_dupes_found(scan, groups)
        from_sigs = getattr(self, "_dupes_from_sigs", False)
        if from_sigs and scan is not None and not getattr(scan, "examined", 1):
            top = self.top_root()
            self.show_banner(
                f"Aucune empreinte sous {top} : lancez d'abord « Empreintes : "
                "sonder ce qui manque ».", "quiet")
            return
        if from_sigs:
            groups = self._only_in_collection(groups)
        groups = self._one_spelling(groups)
        by_image = getattr(self, "_dupes_by_image", False)
        if not groups:
            # Le verdict complet remplace ce qu'une etape avait cru voir.
            self._keep_found_dupes(None, by_image)
            self.show_banner(
                "Aucun doublon trouvé." + (
                    " Par image, seules les vidéos qui ont déjà leur vignette "
                    "sont comparées : « État des vignettes » dit combien."
                    if by_image else ""),
                "info")
            return
        self._keep_found_dupes(groups, by_image)
        self.show_banner(
            self._dupes_summary(groups, by_image) + ". Cliquez ici pour les "
            "afficher — ou plus tard par « ⋯ › Doublons ».", "done",
            action=self.show_found_dupes, seconds=15)

    def _interim_dupes_found(self, scan, groups: list) -> None:
        """Une comparaison d'etape : ce qui est deja releve, compare.

        Le resultat attend comme les autres (« ⋯ › Doublons »), et le bandeau
        ne parle que quand il y a plus de groupes qu'a l'etape d'avant. Les
        groupes ne sont pas definitifs : une video pas encore relevee peut
        venir s'y ajouter, et la comparaison finale a le dernier mot.
        """
        spent = time.monotonic() - getattr(self, "_interim_began", time.monotonic())
        # Une comparaison de trois minutes ne se relance pas toutes les dix :
        # elle prendrait le processeur aux empreintes.
        self._interim_next = time.monotonic() + max(
            self.INTERIM_EVERY_S, 5 * spent)
        if self.sig_scan is None or not getattr(scan, "examined", 0):
            return
        groups = self._one_spelling(self._only_in_collection(groups))
        if not groups:
            return
        self._keep_found_dupes(groups, True, partial=True)
        if len(groups) <= getattr(self, "_interim_told", 0):
            return
        self._interim_told = len(groups)
        self.show_banner(
            f"Doublons, recherche en cours — sur les "
            f"{self._thousands(scan.examined)} vidéos déjà relevées : "
            + self._dupes_summary(groups, True) + ". Cliquez ici pour les "
            "voir. D'autres peuvent encore s'ajouter, y compris à ces groupes.",
            "info", action=self.show_found_dupes, seconds=15)

    @staticmethod
    def _dupes_summary(groups: list, by_image: bool) -> str:
        """« 12 groupe(s) de doublons — 15 fichier(s) en trop, jusqu'a… »."""
        extra = doubtful = gagne = 0
        for group in groups:
            _each, paths = group
            if getattr(group, "sure", True):
                extra += len(paths) - 1
                gagne += getattr(group, "gain", _each * (len(paths) - 1))
            else:
                # Trop peu pour trancher (une seule image, duree inconnue) :
                # a comparer, pas « en trop ».
                doubtful += 1
        how = "qui se ressemblent" if by_image else "de doublons"
        sure = len(groups) - doubtful
        said = []
        if sure:
            said.append(f"{sure} groupe(s) {how} — {extra} fichier(s) en trop, "
                        f"jusqu'à {human_size(gagne)} à récupérer")
        if doubtful:
            said.append(f"{doubtful} groupe(s) à comparer, trop peu sûrs pour "
                        "dire ce qui est en trop")
        return " ; ".join(said)

    def _name_dupes_action(self, count: int = 0, partial: bool = False) -> None:
        """L'entree « Afficher les doublons trouvés » n'existe que s'il y en a."""
        action = getattr(self, "dupes_result_action", None)
        if action is None:
            return
        so_far = " jusqu'ici" if partial else ""
        action.setText(f"Afficher les doublons trouvés{so_far} ({count} groupe(s))")
        action.setVisible(bool(count))

    def _keep_found_dupes(self, groups, by_image: bool, partial: bool = False) -> None:
        """Retient le resultat d'une recherche -- en memoire et sur le disque.

        Il ne vivait qu'en memoire : trois heures d'empreintes et quatre
        mille paires disparaissaient a la fermeture, sans plus aucun moyen
        de les revoir. Ecrit a chaque etape et a la fin, il revient au
        lancement suivant (`_reload_found_dupes`). Rien (None) l'efface.
        """
        from .dupes_memory import FOUND_DUPES
        self._pending_dupes = (groups, by_image) if groups else None
        self._pending_partial = bool(groups) and partial
        self._name_dupes_action(len(groups or ()), partial=self._pending_partial)
        if not groups:
            FOUND_DUPES.clear()
            return
        top = self.top_root() if self.root is not None else ""
        if not FOUND_DUPES.save(groups, by_image, partial, str(top or "")):
            self.show_banner("Les doublons trouvés n'ont pas pu être écrits sur "
                             "le disque : ils restent visibles jusqu'à la "
                             "fermeture de Prisme.", "error")

    def _reload_found_dupes(self) -> None:
        """Au lancement : le dernier resultat de doublons, s'il y en a un."""
        if self._pending_dupes is not None or self.dupes is not None:
            return
        from .dupes_memory import FOUND_DUPES
        try:
            found = FOUND_DUPES.load()
        except Exception:                               # noqa: BLE001
            found = None
        if not found:
            return
        groups, by_image, partial = found
        self._pending_dupes = (groups, by_image)
        self._pending_partial = partial
        self._name_dupes_action(len(groups), partial=partial)

    def _fresh_dupes(self, groups: list) -> list:
        """Le resultat tel qu'il vaut maintenant, peut-etre des jours apres.

        Ce qu'on a declare « pas des doublons » depuis en sort, comme ce qui
        a quitte la collection ; le resultat retenu est mis a jour d'autant.
        Ce qui est a la corbeille de la seance sort de l'affichage seulement :
        on peut encore le restaurer.
        """
        from .dupes_memory import NOT_DUPES
        before = len(groups)
        groups = NOT_DUPES.filtrer_groupes(list(groups))
        groups = self._one_spelling(self._only_in_collection(groups))
        if len(groups) != before:
            by_image = self._pending_dupes[1] if self._pending_dupes else True
            self._keep_found_dupes(groups, by_image, self._pending_partial)
        return self._still_here(groups)

    def show_found_dupes(self, groups: list | None = None, fresh=()) -> None:
        """Ouvre la revue des doublons : chaque groupe cote a cote, le dossier
        de chaque copie, et une regle pour choisir ce qu'on garde.

        `groups` : seulement ceux-la (le guetteur des nouvelles videos) ;
        sinon, le dernier resultat retenu."""
        if not isinstance(groups, list):
            groups = None          # le « checked » d'une action de menu
        if groups is None:
            pending = self._pending_dupes
            if not pending:
                self.show_banner("Aucun résultat de doublons en attente.", "quiet")
                return
            if self.root is None:
                return
            groups = self._fresh_dupes(pending[0])
        else:
            groups = self._still_here(list(groups))
        if not groups:
            self.show_banner("Plus aucun doublon à examiner : ce qui a été "
                             "trouvé a été écarté ou rangé depuis.", "quiet")
            return
        from .dupes_view import DupesReview
        old = getattr(self, "_dupes_review", None)
        if old is not None:
            try:
                old.close()
            except RuntimeError:
                pass
        review = DupesReview(self, groups, fresh=fresh)
        review.setAttribute(Qt.WA_DeleteOnClose, True)
        review.destroyed.connect(
            lambda *_a, r=review: setattr(self, "_dupes_review", None)
            if getattr(self, "_dupes_review", None) is r else None)
        self._dupes_review = review
        review.show()
        review.raise_()
        review.activateWindow()

    def show_found_dupes_board(self) -> None:
        """Montre le dernier resultat de doublons, comme une planche ordinaire.

        Une liste de passage : la relecture de la collection continue en
        dessous sans la toucher, les groupes restent groupes quel que soit
        le tri ou le filtre, et recliquer l'onglet rend la collection.

        Le resultat reste ensuite dans « ⋯ › Doublons » -- et d'une seance a
        l'autre -- jusqu'a la recherche suivante : une fois affiche, il
        disparaissait, et il fallait tout relancer pour le revoir.
        """
        pending = self._pending_dupes
        if not pending:
            self.show_banner("Aucun résultat de doublons en attente.", "quiet")
            return
        groups, by_image = pending
        if self.root is None:
            return
        groups = self._fresh_dupes(groups)
        if not groups:
            self.show_banner("Plus aucun doublon à examiner : ce qui a été "
                             "trouvé a été écarté ou rangé depuis.", "quiet")
            return
        if self.tab == TAB_SPLIT:
            if self.wall_full:
                self.toggle_wall_fullscreen(False)
            self.set_tab(TAB_FOLDERS)
        self.close_aside()
        # Les membres d'un meme groupe se suivent : c'est ce qui permet de les
        # comparer d'un coup d'oeil au lieu de les chercher dans la liste.
        # Le meilleur exemplaire vient en tete, marque « à garder » ; les
        # autres disent sur leur carte ce qui les en separe, et sont coches
        # d'office : cocher N-1 cases par groupe, a la main, etait tout le
        # travail. Un groupe trop peu sur (« à comparer ») n'est pas coche.
        items = []
        checked = set()
        extra_size = 0
        for number, group in enumerate(groups):
            _each, paths = group
            gap = getattr(group, "gap", None)
            sure = getattr(group, "sure", True)
            to_check = {os.path.normcase(str(path))
                        for path in getattr(group, "to_check", ())}
            # Jamais le meilleur, meme sous une autre casse : sous Windows et
            # sur le NAS, c'est le meme fichier.
            to_check.discard(os.path.normcase(str(paths[0])) if paths else "")
            if to_check:
                extra_size += getattr(group, "gain", 0)
            for rank, path in enumerate(paths):
                path = _as_path(path)
                item = Item(path=path, kind=MODE_FILES, videos=[path],
                            video_count=1, file_count=1)
                # Le groupe de chacun : Ctrl+A puis Supprimer emportait
                # l'original avec ses copies. Un groupe garde toujours au
                # moins son meilleur exemplaire (voir _spare_last_copies).
                item.dupe_group = number
                if gap is not None:
                    item.board_note = (("✓ à garder" if sure else "à comparer")
                                       if rank == 0 else gap(path))
                if os.path.normcase(str(path)) in to_check:
                    checked.add(item.item_id)
                items.append(item)
        # La relecture de la racine continue : elle tient la collection a
        # jour en dessous, sans rien melanger a cette liste (`on_patch`).
        # Celle d'un sous-dossier n'a plus rien a dire.
        if self.scanning and not self._scan_top:
            self.stop_scan()
            self.progress.hide()
        self._hush_players()
        self.browsing = True
        self._transient = "dupes"
        # Chaque groupe tel qu'il a ete montre, le meilleur en tete : c'est lui
        # que _spare_last_copies consulte, et non la liste du moment, qu'un
        # filtre, un tri ou un autre onglet peuvent avoir remplacee.
        self._dupe_members = {}
        for item in items:
            self._dupe_members.setdefault(item.dupe_group, []).append(item)
        self.all_items = items
        self.mode = MODE_FLAT
        self.items = list(items)
        self.index = 0
        self._list_leaf = "Doublons"
        self.board.picked_ids = set(checked)
        self._apply_selectors()
        self.refresh_board()
        self.on_picked_changed(len(self.board.picked_ids))
        said = self._dupes_summary(groups, by_image) + ". "
        if checked:
            said += (f"Le meilleur de chaque groupe est marqué « ✓ à garder » ; "
                     f"les {len(checked)} autres sont cochés d'office "
                     f"({human_size(extra_size)}) : vérifiez, puis "
                     "« Supprimer ». ")
        else:
            said += "Cochez ce dont vous ne voulez plus, puis « Supprimer ». "
        said += ("Un groupe garde toujours un exemplaire ; « Pas des doublons » "
                 "en retire un pour de bon.")
        self.show_banner(said, "info", seconds=12)

    def _leave_transient(self) -> None:
        """On quitte la liste de passage : ce qu'elle avait coche la quitte aussi.

        Les coches d'office des doublons n'ont de sens qu'a cote de leur groupe.
        Les memes chemins, dans « Vidéos », n'ont plus de groupe : Suppr y
        emportait l'original avec ses copies, sans un mot sur les doublons.
        La planche le fait deja quand elle change de liste ; pas quand elle
        n'est pas refaite (mur, fiche ouverte d'emblee).
        """
        if self._transient == "dupes" and self.board.picked_ids:
            self.board.clear_picked()
        if self._transient == "similar":
            # On ouvre autre chose : les ressemblances s'eteignent.
            self._similar = None
            self._tick_similar(self.single_bar, False)
        self._transient = ""
        self._dupe_members = {}

    def _dupe_group_of(self, items) -> list:
        """Les numeros des groupes de doublons de ces elements, dans l'ordre."""
        found = []
        for item in items:
            number = getattr(item, "dupe_group", None)
            if number is not None and number not in found:
                found.append(number)
        return found

    def not_dupes_picked(self) -> None:
        """« Pas des doublons » : les groupes des elements coches sortent de la
        liste, et la recherche ne les remontrera plus."""
        numbers = self._dupe_group_of(self.board.picked_items())
        if not numbers:
            self.show_banner("Cochez au moins un exemplaire du groupe qui n'en "
                             "est pas un.", "quiet")
            return
        if len(numbers) > 1:
            # Les exemplaires en trop sont coches d'office : tout accepter
            # d'un clic retirerait tous les groupes. On le demande.
            answer = QMessageBox.question(
                self, "Pas des doublons ?",
                f"Les éléments cochés touchent {len(numbers)} groupes. Les "
                "retirer tous, comme n'étant pas des doublons ?\n\n"
                "Ils quitteront la liste et ne reviendront plus dans les "
                "recherches. Pour un seul groupe : décochez les autres, ou "
                "ouvrez l'une de ses vidéos — « Pas des doublons » est aussi "
                "sur sa fiche.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self._dismiss_dupe_groups(numbers)

    def not_dupes_current(self) -> None:
        """Depuis la fiche d'un doublon : son groupe n'en est pas un."""
        item = self.current
        numbers = self._dupe_group_of([item] if item is not None else [])
        if not numbers:
            return
        self._dismiss_dupe_groups(numbers)
        self.show_board_at(min(self.index, max(0, len(self.items) - 1)))

    def _dismiss_dupe_groups(self, numbers: list) -> None:
        """Retient ces groupes « pas des doublons », et les retire de la liste."""
        from .dupes_memory import NOT_DUPES
        wanted = set(numbers)
        members = [item for item in self.all_items
                   if getattr(item, "dupe_group", None) in wanted]
        by_group: dict = {}
        for item in members:
            by_group.setdefault(item.dupe_group, []).append(str(item.path))
        added = 0
        for paths in by_group.values():
            for at, left in enumerate(paths):
                for right in paths[at + 1:]:
                    added += NOT_DUPES.ignorer(left, right, save=False)
        # Une seule ecriture pour tout le geste, et non une par paire.
        if added and not NOT_DUPES.save():
            self.show_banner("La mémoire des « pas des doublons » n'a pas pu être "
                             "écrite : elle le sera à la prochaine occasion.",
                             "error")
        gone = {item.item_id for item in members}
        for number in wanted:
            self._dupe_members.pop(number, None)
        page = self.board.page
        self.all_items = [item for item in self.all_items if item.item_id not in gone]
        self.items = [item for item in self.items if item.item_id not in gone]
        self.board.picked_ids -= gone
        self.index = max(0, min(self.index, len(self.items) - 1))
        if not self.items:
            self.board.empty.setText("Plus aucun groupe de doublons à examiner.")
        if self.browsing:
            self.refresh_board()
            self.board.set_page(page)
        self.on_picked_changed(len(self.board.picked_ids))
        self.show_banner(f"{len(wanted)} groupe(s) retiré(s) : ils ne reviendront "
                         "plus parmi les doublons.", "done")

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

    def _gone_paths(self) -> set:
        """Ce qui a quitte sa place, ou s'apprete a le faire (`normcase`).

        La corbeille de session, et la source de ce qui est encore dans la
        file (rangement, mise a l'ecart, renommage) : une video ecartee depuis
        le mur n'a pas toujours d'element a son nom. Tout est en memoire.
        """
        # Une origine devinee (reste d'une seance plantee) ne designe rien de
        # sur : un dossier du meme nom a la racine n'est pas parti pour autant.
        gone = {os.path.normcase(str(entry.origin)) for entry in self.trash.entries
                if not getattr(entry, "guessed", False)}
        for job in list(self.transfers.pending.values()):
            if job.kind in ("move", "delete", "rename") and job.src is not None:
                gone.add(os.path.normcase(str(job.src)))
        return gone

    def _still_here(self, groups: list) -> list:
        """Les groupes de doublons, sans ce qui a ete ecarte ou range depuis.

        Le resultat attend qu'on le demande, parfois toute la seance, pendant
        qu'on continue de trier : un « ✓ à garder » deja a la corbeille
        laissait sa seule copie cochee d'office, et « Supprimer » emportait
        le dernier exemplaire. Un groupe qui a perdu son meilleur n'est plus
        coche du tout : c'est peut-etre l'autre qu'on a choisi de garder.
        """
        gone = self._gone_paths()
        if not gone:
            return groups
        kept = []
        for group in groups:
            _each, paths = group
            inside = [at for at, path in enumerate(paths)
                      if not _within(os.path.normcase(str(path)), gone)]
            if len(inside) == len(paths):
                kept.append(group)
                continue
            if len(inside) < 2:
                continue
            if not hasattr(group, "subset"):
                kept.append((_each, [paths[at] for at in inside]))
                continue
            smaller = group.subset(inside)
            if inside[0] != 0:
                smaller.sure = False
            kept.append(smaller)
        return kept

    def _one_spelling(self, groups: list) -> list:
        """Un meme fichier sous deux casses ne fait pas un groupe de doublons.

        L'index peut garder l'ancienne ecriture d'un fichier renomme par la
        seule casse (hors de Prisme, ou par F2 pendant un sondage). Sous
        Windows et sur le NAS c'est le meme fichier : montre comme doublon de
        lui-meme, ses deux cartes etaient cochees, et la seule copie partait.
        On garde l'ecriture que la collection connait.
        """
        spelled = None
        kept = []
        for group in groups:
            _each, paths = group
            keys = [os.path.normcase(str(path)) for path in paths]
            if len(set(keys)) == len(keys):
                kept.append(group)
                continue
            if spelled is None:
                # Seulement dans ce cas rare : toute la collection, une fois.
                spelled = {str(video) for item in self._plain_items
                           for video in item.videos}
            chosen: dict = {}
            for at, key in enumerate(keys):
                if key not in chosen or str(paths[at]) in spelled:
                    chosen[key] = at
            inside = sorted(chosen.values())
            if len(inside) < 2:
                continue
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
        self._progress_text("recensement des vidéos…")
        self.progress.show()
        self.show_banner(
            f"Préparation des vignettes de {top} — recensement des vidéos…",
            "info",
        )

    # Les travaux de fond : l'attribut qui les tient, ce qu'on en dit tant
    # qu'ils n'ont rien compte, l'entree du menu qui les lance et ce qu'elle
    # devient pendant qu'ils tournent. Recliquer l'entree les arrete : elle
    # le dit desormais, au lieu d'arreter des heures de travail sans prevenir.
    TASKS = (
        ("backfill", "préparation…", "Préparer toutes les vignettes",
         "Arrêter la préparation des vignettes"),
        ("counter", "comptage…", "Compter les vidéos", "Arrêter le comptage"),
        ("audit", "vérification…", "État des vignettes",
         "Arrêter la vérification des vignettes"),
        ("sig_scan", "empreintes…", "Empreintes : sonder ce qui manque",
         "Arrêter les empreintes"),
        ("scene_scan", "plans…", "Repérage des plans (vignettes plus parlantes)",
         "Arrêter le repérage des plans"),
        ("titles_scan", "titres…", "Analyser les titres des métadonnées",
         "Arrêter l'analyse des titres"),
    )

    def _task_said(self, key: str, text: str) -> None:
        """L'avancement d'un travail de fond : a cote du fil d'Ariane."""
        self.__dict__.setdefault("_task_text", {})[key] = text
        self._refresh_state(text)
        self._show_activity()

    def _activities(self) -> list:
        """Ce qui tourne : [(texte court, entree « Arrêter … », geste)]."""
        said = self.__dict__.get("_task_text", {})
        found = []
        if self.scanning:
            done, total = self._scan_done, self._scan_total
            found.append((
                f"⟳ analyse {self._thousands(done)} / {self._thousands(total)}"
                if total else "⟳ analyse…",
                "Arrêter l'analyse — ce qui est lu est gardé", self.toggle_scan))
        else:
            done, total = getattr(self, "_harvest", (0, 0))
            if total:
                found.append((f"◷ aperçus {self._thousands(done)} / "
                              f"{self._thousands(total)}",
                              "Arrêter la préparation des aperçus", self.toggle_scan))
        for key, idle, entry, stop in self.TASKS:
            action = self._menu_by_text.get(entry)
            if getattr(self, key, None) is not None and action is not None:
                found.append((said.get(key) or idle, stop, action.trigger))
        # Une comparaison d'etape se range sous les empreintes, qui l'ont lancee.
        if self.dupes is not None and not getattr(self.dupes, "interim", False):
            found.append(("doublons…", "Arrêter la recherche de doublons",
                          self._stop_dupes))
        if self.cfg["burst"]:
            found.append(("rafale", "Arrêter la rafale", self.toggle_burst))
        return found

    def _show_activity(self) -> None:
        """Dit ce qui tourne en fond, en toutes lettres et sans barre qui ondule.

        Une barre indeterminee va et vient sans rien promettre : elle attire
        l'oeil en permanence pour ne rien apprendre. Un compte discret a cote du
        fil d'Ariane suffit, et s'efface des qu'il n'y a plus rien a dire. Il
        se reecrit a chaque battement, mais ne touche a l'ecran que s'il change.
        """
        if not hasattr(self, "activity_label"):
            return
        found = self._activities()
        busy = 0 if found else self.preview.busy()
        text = found[0][0] if found else (f"⋯ {busy} aperçu(s)" if busy else "")
        if len(found) > 1:
            text += f"  ·  +{len(found) - 1}"
        tip = ("\n".join(f"• {line}" for line, _stop, _slot in found)
               + "\n\nCliquer pour en arrêter un." if found else "")
        said = (text, tip)
        if said != self._activity_said:
            self._activity_said = said
            label = self.activity_label
            # Une largeur qui avance par pas de quarante points : un chiffre
            # qui change dix fois par seconde pendant l'analyse ne remet pas
            # toute la ligne en page -- ni le fil d'Ariane a recouper.
            need = label.fontMetrics().horizontalAdvance(text) + 8 if text else 0
            width = min(280, -(-need // 40) * 40)
            if label.minimumWidth() != width or label.maximumWidth() != width:
                label.setFixedWidth(width)
            label.setText(text)
            label.setToolTip(tip)
            label.setCursor(Qt.PointingHandCursor if found else Qt.ArrowCursor)
        self._name_task_actions(found)
        self._show_share_badge()

    def _name_task_actions(self, found=None) -> None:
        """Les entrees du menu disent ce qu'elles feront : lancer, ou arreter."""
        said = self.__dict__.get("_task_text", {})
        for key, _idle, entry, stop in self.TASKS:
            if key == "backfill":
                continue                   # `_name_backfill_action`
            action = self._menu_by_text.get(entry)
            if action is None:
                continue
            running = getattr(self, key, None) is not None
            label = (f"{stop}   ({said[key]})" if running and said.get(key)
                     else stop if running else entry)
            if action.text() != label:
                action.setText(label)
        kinds = {"size": "Chercher les doublons (même taille)",
                 "image": "Chercher les doublons (même image)",
                 "sigs": "Doublons d'après les empreintes"}
        running = self._dupes_kind() if self.dupes is not None else ""
        for kind, entry in kinds.items():
            action = self._menu_by_text.get(entry)
            if action is None:
                continue
            label = ("Arrêter la recherche de doublons" if kind == running
                     else entry)
            if action.text() != label:
                action.setText(label)

    def _activity_menu(self) -> None:
        """Un clic sur l'activite : de quoi arreter ce qui tourne."""
        found = [entry for entry in self._activities() if entry[2] is not None]
        if not found:
            return
        menu = QMenu(self)
        for line, stop, slot in found:
            action = menu.addAction(f"{stop}   ({line})")
            action.triggered.connect(lambda _c=False, slot=slot: slot())
        menu.exec(self.activity_label.mapToGlobal(
            QPoint(0, self.activity_label.height())))
        menu.deleteLater()
        self._show_activity()

    def _progress_text(self, text: str, tip: str = "") -> None:
        """Le texte de la barre, et sa largeur : assez pour se lire en entier.

        Le detail -- la phrase entiere -- va dans l'infobulle. La largeur
        avance par pas de vingt points : pas de remise en page a chaque
        chiffre qui change.
        """
        self.progress.setFormat(text)
        self.progress.setToolTip(tip)
        need = self.progress.fontMetrics().horizontalAdvance(text) + 24
        width = max(130, min(300, -(-need // 20) * 20))
        if self.progress.minimumWidth() != width:
            self.progress.setFixedWidth(width)

    def _name_backfill_action(self, seen: int = -1, total: int = 0) -> None:
        """Dit, dans le menu, ou en est la preparation — ou quand elle a fini."""
        if self.backfill is not None:
            label = (f"Arrêter la préparation des vignettes   "
                     f"({self._thousands(seen)} / {self._thousands(total)})"
                     if seen >= 0 else "Arrêter la préparation des vignettes")
        else:
            done = self.cfg["thumbs_last_run"]
            label = ("Préparer toutes les vignettes"
                     + (f"   (dernière : {done})" if done else "   (jamais faite)"))
        # Par l'entree elle-meme : une fois renommee « Arrêter … », sa
        # recherche par le texte ne la retrouvait plus, et le menu gardait
        # « Arrêter » bien apres la fin.
        action = self._menu_by_text.get("Préparer toutes les vignettes")
        if action is not None and action.text() != label:
            action.setText(label)

    def on_backfill_counting(self, found: int) -> None:
        """Le recensement dure : il dit ce qu'il trouve en chemin."""
        if self.scanning:
            return
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._progress_text(f"recensement : {self._thousands(found)}",
                            f"Préparation des vignettes : recensement, "
                            f"{self._thousands(found)} vidéo(s) trouvée(s)…")
        self.progress.show()

    def on_backfill_counted(self, total: int) -> None:
        if self.scanning:
            return
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self._progress_text(f"vignettes : 0 / {self._thousands(total)}")
        self.progress.show()

    def on_backfill_progress(self, made: int, kept: int, total: int) -> None:
        self._name_backfill_action(made + kept, total)
        self._task_said("backfill", f"préparation {self._thousands(made + kept)} / "
                                    f"{self._thousands(total)}")
        if self.scanning:
            return
        seen = made + kept
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(seen)
        # Une barre qui avance sans dire combien de temps il reste n'apprend
        # rien qu'on ne voie deja : c'est la fin qu'on veut connaitre.
        elapsed = time.monotonic() - self._backfill_started
        counts = f"{self._thousands(seen)} / {self._thousands(total)}"
        if seen > 20 and elapsed > 5:
            left = human_duration((total - seen) * elapsed / seen)
            self._progress_text(
                f"{counts} · {left}",
                f"Préparation des vignettes : {counts} — {left} restant")
        else:
            self._progress_text(f"vignettes : {counts}",
                                f"Préparation des vignettes : {counts}")
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
            self._progress_text("%v / %m analysés")
        if complete:
            self.cfg["thumbs_last_run"] = datetime.now().strftime("%d/%m/%Y à %H:%M")
            self.cfg.save()
        self._name_backfill_action()
        fin = "terminée" if complete else "interrompue"
        self.show_banner(
            f"Préparation {fin} : {made} vignette(s) fabriquée(s), "
            f"{kept} déjà présente(s). Compte rendu : {ThumbBackfill.LOG}",
            "info",
        )

    def refresh_root(self) -> None:
        """Relit en entier le dossier affiche, sans se fier aux dates.

        A la racine, c'est toute la collection : plusieurs minutes sur le
        NAS, d'ou la question -- Ctrl+R est le voisin de Ctrl+E et de Ctrl+T,
        et une faute de frappe vidait la planche le temps de tout relire. La
        liste reste desormais a l'ecran et se corrige au fil de la lecture.
        """
        if self.root is None:
            return
        top = self.top_root()
        # Le mur montre la collection, quel que soit le dossier d'ou on l'a
        # ouvert : c'est elle qu'on relit.
        if self.tab != TAB_SPLIT and (top is None or Path(self.root) != Path(top)):
            self.show_banner(
                f"Réanalyse complète de « {Path(self.root).name} »…", "info")
            self.start_root(self.root, self.mode, reset_levels=False, force=True)
            return
        answer = QMessageBox.question(
            self, "Réanalyser toute la collection ?",
            f"Chaque dossier de « {top} » va être relu sur le disque, sans se "
            "fier aux dates.\n\nSur le NAS, cela prend plusieurs minutes. Les "
            "vignettes restent affichées et se corrigent au fil de la lecture.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        self.setFocus()
        if answer != QMessageBox.Yes:
            return
        self._reread_collection(top)

    def _reread_collection(self, top) -> None:
        """Relit toute la collection, sans se fier aux dates."""
        self.show_banner("Réanalyse complète de la collection…", "info")
        tab = self.tab
        # La collection, c'est la liste des dossiers de la racine : c'est elle
        # qu'on relit, quel que soit l'onglet. L'onglet Vidéos relancait un
        # parcours a plat de tout le partage, qui ne mettait pas la collection
        # a jour ; sur le mur, la planche prenait la place des panneaux.
        self.start_root(top, MODE_FOLDERS, reset_levels=True, force=True)
        if tab == TAB_SPLIT:
            self.viewer.setCurrentWidget(self.wall)
            self._apply_selectors()
        elif tab == TAB_VIDEOS:
            # La liste a plat d'avant la relecture, tout de suite ; elle se
            # complete a la fin (`on_scan_finished`).
            self._show_tab_list()

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
        if not self.browsing and self.tab != TAB_SPLIT:
            # Trois etages, toujours les memes : le panorama, l'interieur d'un
            # dossier (ou d'un mot-cle) en vignettes, la fiche d'une video.
            # Entre depuis une fiche (Entree, Ctrl+↓), on arrivait sur la fiche
            # de la premiere video au lieu des vignettes du dossier.
            self.browsing = True
            self._hush_players()
            self._apply_selectors()
        if item.is_tag:
            return self.open_tag(item)
        target = Path(item.path)

        # Le mode se decide sur ce qu'on sait deja du dossier. Le dossier
        # etait enumere ici, sur le fil de l'interface, pour un seul booleen
        # -- puis une seconde fois par `start_root`, une troisieme par la
        # relecture.
        if item.loose_only:
            # L'entree ne porte que les videos en vrac : on va droit a elles.
            mode = MODE_FILES
        elif (item.subdir_count == 0 and item.file_count >= 0
              and not item.incomplete and not item.unreadable):
            # Aucun sous-dossier, a aucune profondeur (le compte de l'analyse
            # descend partout) : il n'y a que des videos a montrer.
            mode = MODE_FLAT
        else:
            # Des sous-dossiers, ou un compte incertain : `start_root` bascule
            # sur les videos si la lecture n'en trouve aucun.
            mode = self.mode_for_content()
        here = self._here()
        self.levels.append({
            "root": self.root,
            "mode": self.mode,
            "item_id": item.item_id,
        })
        # On entre sur ce que l'index sait deja, sans rien demander au NAS sur
        # le fil de l'interface : la question « le dossier est-il la » se pose
        # a cote (`_check_entered`). Posee ici, elle figeait chaque entree
        # d'un aller-retour -- de plusieurs secondes, NAS endormi.
        self.start_root(target, mode, reset_levels=False, state="ok",
                        departure=here, probe_empty=False)
        self._check_entered(item, target)

    def _check_entered(self, item, target: Path) -> None:
        """Hors du fil de l'interface : le dossier ou l'on vient d'entrer est-il
        la, et, inconnu de l'index, contient-il des sous-dossiers ?"""
        from .tunnel import Chore
        name = item.name
        # Des sous-dossiers comptes par l'analyse, ou une liste deja connue :
        # rien a verifier de plus que sa presence.
        doubt = (self.mode == MODE_FOLDERS and not self.all_items
                 and not item.subdir_count > 0)
        skip_hidden = self.cfg["skip_hidden"]
        expand = self.cfg["expand_parents"]

        def work():
            state = _folder_state(target)
            empty = False
            if state == "ok" and doubt:
                try:
                    empty = not list_entries(target, MODE_FOLDERS, skip_hidden,
                                             False, strict=True,
                                             keep_parents=expand)
                except RootUnreadable:
                    # Illisible n'est pas vide (voir `start_root`).
                    empty = False
            return state, empty

        Chore(work, self, fallback=("injoignable", False),
              then=lambda found: self._entered_checked(target, name, *found)).start()

    def _entered_checked(self, target: Path, name: str, state: str,
                         empty: bool) -> None:
        if self.root is None or Path(self.root) != target:
            return          # on est deja ailleurs : plus rien a corriger ici
        if state == "absent":
            self.show_banner(f"Introuvable : {name}", "error")
            if self.levels:
                self.go_up()
            return
        if state == "injoignable":
            # Le dossier n'a pas disparu : c'est le NAS qui ne repond pas. Ce
            # que l'index en savait reste a l'ecran ; la relecture reessaiera.
            self.show_banner(f"NAS injoignable : « {name} » n'a pas pu être "
                             "relu. Réessayez dans un instant.", "error")
            return
        if empty and self.mode == MODE_FOLDERS and not self.all_items:
            # Sans sous-dossier : ses videos, plutot qu'une liste vide.
            self.start_root(target, MODE_FLAT, reset_levels=False, state="ok",
                            remember=False)

    def _here(self) -> dict:
        """L'endroit ou l'on est, tel qu'Alt+← doit pouvoir y revenir.

        L'onglet en fait partie : sans lui, revenir rejouait un dossier sous
        l'onglet d'arrivee -- des dossiers sous « Vidéos », et sous
        « Dossiers » un parcours a plat de tout le partage.
        """
        return {
            "root": self.root, "mode": self.mode, "tab": self.tab,
            "levels": list(self.levels), "board": self.browsing,
            "item_id": self.current.item_id if self.current else "",
        }

    def _remember(self, place: dict) -> None:
        self.visited.append(place)
        del self.visited[:-40]

    def go_back(self) -> bool:
        """Revient a l'endroit precedemment visite, quel qu'en soit le niveau.

        Rien ne change avant d'avoir trouve ou aller : un dossier range ou
        supprime depuis se saute. On effacait d'abord la racine affichee,
        puis `start_root` refusait le dossier disparu -- et les onglets, ↑ et
        la relecture ne repondaient plus jusqu'a « Changer de racine ».
        L'endroit revient sous son onglet ; la racine de la collection
        revient par la liste de l'onglet, deja en memoire, sans rien relire.
        """
        if self._quiet:
            return False
        if self.tab == TAB_SPLIT:
            # Le mur n'a pas d'endroit d'avant : il remplacait les panneaux
            # par la fiche d'un dossier, sous l'onglet « Mur ».
            self.show_banner("Le mur n'a pas de page précédente : changez "
                             "d'onglet pour en sortir.", "quiet")
            return False
        previous, state = None, ""
        while self.visited:
            candidate = self.visited.pop()
            if candidate.get("tab") == TAB_SPLIT:
                continue
            # Une question au NAS par endroit essaye, que `start_root` ne
            # repose pas ; une racine injoignable n'est pas une racine partie.
            state = _folder_state(candidate["root"])
            if state != "absent":
                previous = candidate
                break
        if previous is None:
            self.show_banner("Rien avant cet endroit", "quiet")
            return False
        tab = previous.get("tab") or self.tab
        if tab != self.tab and tab in TABS:
            self._enter_tab(tab)
        self.levels = list(previous["levels"])
        top = self.top_root()
        if top is not None and Path(previous["root"]) == Path(top):
            # La racine de la collection : la liste de l'onglet, sans relire.
            # Rejouer son mode relancait, sous « Dossiers », le parcours a plat
            # de tout le partage laisse par l'onglet « Vidéos ».
            self.levels = []
            if self._show_tab_list(restore_id=previous["item_id"],
                                   remember=False):
                current = self.current
                if (not previous["board"] and current is not None
                        and current.item_id == previous["item_id"]):
                    self.on_board_open(self.index)
                return True
            self.start_root(top, MODE_FOLDERS, reset_levels=False,
                            restore_id=previous["item_id"], state=state,
                            remember=False)
            return True
        if previous["board"] != self.browsing:
            self.browsing = previous["board"]
            self._apply_selectors()
        # Revenir en arriere n'empile pas l'endroit qu'on quitte.
        self.start_root(previous["root"], previous["mode"], reset_levels=False,
                        restore_id=previous["item_id"], state=state,
                        remember=False)
        return True

    def set_board_columns(self, columns: int) -> None:
        self.cfg["board_columns"] = columns
        self.cfg.save()
        self.board.set_columns(columns)

    def browse_videos(self, name: str, videos: list) -> None:
        """Ces videos comme un dossier : leurs vignettes, leurs fiches, et
        Echap pour revenir. Rien ne bouge sur le disque."""
        videos = [str(video) for video in videos]
        if not videos:
            return
        if self.tab == TAB_SPLIT:
            self.set_tab(TAB_FOLDERS)
        item = Item(path=Path(name), kind=MODE_FOLDERS, videos=[Path(v) for v in videos],
                    video_count=len(videos), file_count=len(videos))
        item.is_tag = True
        if not self.browsing:
            self.browsing = True
            self._hush_players()
            self._apply_selectors()
        self.open_tag(item)
        self.showNormal() if self.isMinimized() else None
        self.raise_()
        self.activateWindow()

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
        self.index = 0
        # Les videos d'un autre mot-cle, pas celle d'avant : le lecteur de
        # cote lisait encore une video de la liste quittee.
        if not self.aside_playlist and not self.aside.isHidden():
            self.close_aside()
        self.start_harvest()
        self._apply_selectors()
        # Un mot-cle n'est pas un dossier : sans ce rappel, le fil d'Ariane
        # restait sur la racine et l'on ne savait plus ce qu'on regardait.
        self._list_leaf = item.name
        self.crumbs.set_path(self.top_root(), self.root)
        self.crumbs.append_leaf(item.name)
        noun = "photo(s)" if photo_mode() else "vidéo(s)"
        if item.path.name == PINNED_UNREAD:
            told = (f"{len(self.items)} {noun} dont le nom ne dit rien (8f7e6d5c4b, "
                    "IMG_2041…), où qu'elles soient : à regarder pour les ranger")
        elif item.path.name == PINNED_LOOSE:
            told = (f"{len(self.items)} {noun} dont le nom ne dit rien, posées en "
                    "vrac à la racine ou dans un dossier « + », hors de tout "
                    "sous-dossier : à regarder pour les ranger")
        else:
            told = f"{len(self.items)} {noun} portant « {item.path.name} »"
        self.show_banner(told, "info")
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
            if self.tab != TAB_SPLIT and not self.at_home():
                # Une liste de passage (les doublons) : au-dessus d'elle, il
                # y a la liste de l'onglet.
                return self.go_home()
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
        if parent == here:
            self.show_banner("Déjà au sommet.", "quiet")
            return
        top = self.top_root()
        if (self.tab != TAB_SPLIT and top is not None and parent == Path(top)
                and self._show_tab_list(restore_id=str(self.root))):
            # Remonter jusqu'a la racine, c'est retrouver la liste de
            # l'onglet, deja en memoire : sur « Vidéos », on relancait un
            # parcours a plat de tout le NAS.
            return
        # Une seule question au NAS, que `start_root` ne repose pas.
        state = _folder_state(parent)
        if state == "absent":
            self.show_banner("Déjà au sommet.", "quiet")
            return
        self.start_root(parent, self._mode_for_folder(),
                        restore_id=str(self.root), state=state)

    def go_up(self) -> bool:
        """Remonte d'un niveau, en retrouvant le dossier d'ou l'on etait parti."""
        if not self.levels:
            return False
        # L'endroit qu'on quitte, niveaux compris : Alt+← y ramene.
        here = self._here()
        level = self.levels.pop()
        top = self.top_root()
        on_sheet = not self.browsing
        if (not self.levels and top is not None
                and Path(level["root"]) == Path(top)
                and self._show_tab_list(restore_id=level["item_id"],
                                        departure=here)):
            # Revenir au premier niveau, c'est retrouver la liste de l'onglet
            # — les favoris, les mots-cles — sans relire toute la racine. Depuis
            # une fiche, on retrouve la fiche du dossier d'ou l'on venait.
            if on_sheet and self.current is not None \
                    and self.current.item_id == level["item_id"]:
                self.on_board_open(self.index)
            return True
        self.start_root(
            level["root"], level["mode"],
            reset_levels=False, restore_id=level["item_id"], departure=here,
        )
        return True

    def on_scan_progress(self, done: int, total: int, name: str = "") -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        if not self.progress.isHidden():
            self._progress_text(
                f"{self._thousands(done)} / {self._thousands(total)} analysés",
                f"En cours : {name}" if name else "")
        self._scan_done, self._scan_total, self._scan_name = done, total, name
        self._refresh_scan_button()
        if not self.items:
            self.item_subtitle.setText(
                f"Analyse {done}/{total} — {name}" if name
                else f"Analyse {done}/{total}…")

    def start_harvest(self) -> None:
        """Fabrique d'avance la vignette de chaque élément, une fois pour toutes.

        Sur le partage, une vignette coûte environ une seconde et seize
        extractions de front ne vont pas plus vite que huit : la ligne est
        saturée. Une page de quarante cartes demande donc une demi-minute, et
        **rien ne peut la raccourcir au moment où on la regarde**. Mais une
        vignette déjà faite se relit en deux millisecondes. On les fabrique donc
        avant, pendant qu'on fait autre chose, et la récolte s'écarte dès que
        quelqu'un demande quelque chose.

        Idempotent : la même liste ne relance rien. Elle repartait de zéro à
        chaque onglet, et ne fabriquait presque rien. La liste des tâches se
        dresse hors du fil de l'interface (un tiers de seconde sur cent mille
        éléments), sur un instantané de la liste.
        """
        if self.tab == TAB_SPLIT or self._closing:
            # Le mur a besoin de la ligne : il arrete la recolte, rien ne doit
            # la relancer derriere lui.
            return
        items = self.all_items
        key = (str(self.top_root()), len(items))
        harvester = self.preview.harvester
        if items is self._harvest_src and key == self._harvest_key and (
                self._harvest_complete == key
                or (harvester is not None and harvester.isRunning())):
            return
        mark(f"start_harvest {len(items)}")
        self._harvest_src = items
        self._harvest_key = key
        self._harvest_complete = None
        self._harvest_token += 1
        token = self._harvest_token
        snapshot = list(items)
        from .tunnel import Chore

        def then(tasks) -> None:
            if (token != self._harvest_token or self._closing
                    or not shiboken6.isValid(self) or self.tab == TAB_SPLIT):
                return
            if not tasks:
                return
            harvester = self.preview.start_harvest(tasks)
            harvester.progress.connect(self.on_harvest_progress)
            harvester.finished_harvest.connect(self.on_harvest_finished)
            self._harvest = (0, len(tasks))
            self._refresh_scan_button()

        Chore(lambda: self._harvest_tasks(snapshot), self, fallback=[],
              then=then).start()

    @staticmethod
    def _harvest_tasks(items: list) -> list:
        """(cle, video, instant) de chaque carte : l'instant meme que la
        planche demandera (`media.card_moment`). Hors du fil de l'interface.

        La recolte prenait l'instant d'une formule, la planche d'une autre, la
        preparation d'une troisieme : elle fabriquait des images que personne
        ne demandait.
        """
        tasks = []
        moment = media.card_moment
        for item in items:
            if item.locked or not item.videos:
                continue
            video = str(item.videos[0])
            tasks.append((item.item_id, video, moment(video)))
        return tasks

    def on_harvest_progress(self, done: int, total: int) -> None:
        self._harvest = (done, total)
        self._refresh_scan_button()

    def on_harvest_finished(self, made: int) -> None:
        self._harvest = (0, 0)
        # Tout est fait pour cette liste : y revenir ne relance rien.
        self._harvest_complete = self._harvest_key
        self._refresh_scan_button()
        if made:
            self.show_banner(
                f"✓ {made} aperçu(s) préparés d'avance. Les revoir est "
                f"désormais immédiat.", "done")

    def _refresh_scan_button(self) -> None:
        """Dit sans ambiguite si une analyse tourne, et ou elle en est.

        C'etait la vraie plainte : on ne savait pas distinguer une application
        lente d'une analyse en cours. Elle s'ecrivait dans un bouton cache,
        repoli a chaque dossier lu ; c'est l'activite, a cote du fil
        d'Ariane, qui le dit maintenant (`_show_activity`).
        """
        self._show_activity()

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
        if not (added or replaced or removed):
            return
        if self._transient and not self._scan_top:
            # Une liste de passage (doublons) : la relecture d'un sous-dossier
            # n'a rien a y ajouter.
            return
        if (self._scan_top and self.all_items is not self._plain_items
                and self.tab == TAB_FOLDERS and self.mode == MODE_FOLDERS
                and not self.levels and not self._transient):
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
            # La liste affichee n'a pas bouge : sa liste triable reste bonne.
            self._touch(sortable=False)
            return
        # La planche signale chaque carte ajoutee ; on ne recompte qu'une
        # fois, a la fin du paquet (`on_board_page`).
        self._patching = True
        try:
            if removed:
                self._drop_items(removed)
            if replaced:
                self._replace_items(replaced)
            if added:
                # Le filtre se prepare une fois par paquet, non par element :
                # c'etait, avec la liste triable refaite a chaque carte, ce
                # qui figeait la fenetre pendant tout un premier inventaire.
                keep = self._matcher()
                sortable = self._sortable
                for item in added:
                    self.all_items.append(item)
                    if sortable(item) and keep(item):
                        self._accept_item(item)
                # La liste ne fait que s'allonger : la liste triable se
                # complete par la fin, sans tout reparcourir.
                self._touch(sortable=False,
                            collection=self._shows_collection())
        finally:
            self._patching = False
        if self._scan_top:
            # `_drop_items` refait la liste : la collection la suit.
            self._plain_items = self.all_items
        if (added or removed) and self.browsing:
            self._board_dirty = True
        if self._board_dirty:
            # Rebâtir la planche à chaque paquet la ferait clignoter : on le
            # fait au plus une fois par seconde, quand la rafale est passée.
            self.board_timer.start()
        # Les comptes, et eux seuls : la barre d'avancement appartient a
        # l'analyse (`on_scan_progress`). Recompter ici la remettait a zero a
        # chaque paquet, et l'on croyait l'analyse repartie de rien.
        if self.browsing:
            first, last = self.board._page_bounds()
            self.on_board_page(first + 1 if self.board.items else 0, last,
                               len(self.board.items))
        else:
            self._show_counts()

    def _drop_items(self, removed: list) -> None:
        """Retire les éléments que le disque ne porte plus."""
        gone = set(removed)
        current = self.current
        self.all_items = [item for item in self.all_items if item.item_id not in gone]
        self.items = [item for item in self.items if item.item_id not in gone]
        # Une liste neuve : tout ce qui se tire d'elle se reconnait a son
        # identite, rien a signaler de plus.
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
                if self._in_flight.get(old.item_id) is old:
                    # Son transfert doit revenir a l'element qu'on affiche.
                    self._in_flight[old.item_id] = new
                if old.item_id == current_id and not redraw:
                    redraw = [str(v) for v in old.videos] != [str(v) for v in new.videos]
        # Remplaces sur place, sans que la liste change de longueur : tout
        # ce qu'on en a tire est a refaire.
        self._touch(collection=self._shows_collection())
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
            if self._restore_id and item.item_id == self._restore_id:
                # La carte qu'on venait voir (fil d'Ariane depuis sa fiche) :
                # en vue des qu'elle arrive.
                self._restore_id = ""
                self.index = len(self.items) - 1
                self.board.scroll_to(self.index)
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
        disque, l'analyse vient de le faire. Les deux familles se calculent
        hors du fil de l'interface et se gardent tant que la collection ne
        bouge pas : « Mes mots » se refaisait a chaque visite, deux a quatre
        secondes de fenetre figee sur cent mille videos.
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
        key = self._collection_key()
        # Deux familles : les mots qu'on a saisis, et ceux que les noms de
        # fichiers repetent d'eux-memes. Les seconds ne demandent aucune saisie
        # et decrivent souvent mieux la collection que ce qu'on aurait pense.
        if self.tag_family != "mine":
            cached = self._top_tags
            if cached is None or cached[0] != key:
                if self._tags_thread is None or self._tags_key != key:
                    videos = [video for item in plain for video in item.videos]
                    self._tags_key = key
                    self._tags_thread = TagsThread(videos, INDEX.titles, self)
                    self._tags_thread.ready.connect(self._on_top_tags)
                    self._tags_thread.start()
                    self.show_banner("Mots fréquents : calcul en cours…", "info")
                if cached is None:
                    return self._tags_computing("Calcul des mots fréquents…")
                # L'ancienne liste reste a l'ecran le temps du calcul : une
                # planche videe puis refaite pour presque les memes mots.
            found = cached[1]
            if not found:
                return self._no_tags(
                    "Aucun mot ne revient assez souvent dans ces noms de fichiers.")
            return self._show_tag_list(found)
        words = self.tags
        if not words:
            # Aucun mot-cle : la liste se vide **et l'affichage suit**. Il
            # restait auparavant sur les categories precedentes, si bien que
            # « Mes mots-cles » semblait rendre les mots frequents.
            return self._no_tags(
                "Ajoutez les vôtres par « ⋯ › Mots-clés automatiques… », ou "
                "choisissez « Fréquents » pour les laisser deviner.")
        # Mes propres mots valent pour une seule video ; ceux tires des noms
        # de fichiers (`TagsThread`) doivent en reunir plusieurs.
        minimum = 1
        members = self.cfg["tag_members"] or {}
        wanted = (key, tuple(words), minimum,
                  tuple(sorted((k, len(v)) for k, v in members.items())))
        cached = self._mine_tags
        if cached is None or cached[0] != wanted:
            if self._mine_key != wanted:
                self._mine_key = wanted
                videos = [video for item in plain for video in item.videos]
                # Par tranches sur le fil de l'interface, et non dans un fil :
                # un calcul Python y garde le verrou global, et la fenetre
                # restait figee jusqu'a la fin (`iter_tag_items`).
                old = self._mine_slicer
                if old is not None and shiboken6.isValid(old):
                    old.stop()
                    old.deleteLater()
                self._mine_slicer = self._in_slices(
                    iter_tag_items(list(words), videos, minimum, dict(members)),
                    lambda found, k=wanted: self._on_mine_tags(k, found))
            if cached is None or cached[0][1:3] != wanted[1:3]:
                return self._tags_computing("Calcul de vos mots-clés…")
            # Memes mots, collection un peu changee : l'ancienne liste reste
            # a l'ecran le temps du calcul.
        found = cached[1]
        if not found:
            return self._no_tags(
                "Aucun de vos mots-clés ne figure dans les noms de fichiers.")
        self._show_tag_list(found)

    def _in_slices(self, work, then, budget_ms: float = 8.0) -> QTimer:
        """Mene un calcul (un generateur) par tranches sur le fil de l'interface.

        Quelques millisecondes, puis la main revient a la boucle
        d'evenements : une image passe entre deux tranches. `then` recoit ce
        que le generateur rend ([] s'il echoue). Le minuteur rendu l'arrete.
        """
        timer = QTimer(self)
        # Une milliseconde, et non zero : un minuteur a zero se relance dans la
        # meme passe d'evenements, et un `processEvents` menait alors tout le
        # calcul d'un bloc.
        timer.setInterval(1)

        def finish(result) -> None:
            timer.stop()
            timer.deleteLater()
            if not self._closing:
                then(result)

        def tick() -> None:
            if self._closing:
                timer.stop()
                return
            end = time.perf_counter() + budget_ms / 1000.0
            try:
                while time.perf_counter() < end:
                    next(work)
            except StopIteration as done:
                finish(done.value)
            except Exception:                           # noqa: BLE001
                finish([])

        timer.timeout.connect(tick)
        timer.start()
        return timer

    def _tags_up_to_date(self) -> bool:
        """Les mots-cles a l'ecran sont-ils ceux de la collection actuelle ?

        Une relecture qui n'a rien change ne les refait pas : la planche des
        mots-cles se rebatissait a chaque fin d'analyse, pour les memes.
        """
        key = self._collection_key()
        if self.tag_family != "mine":
            cached = self._top_tags
            return (cached is not None and cached[0] == key
                    and self.all_items is cached[1])
        cached = self._mine_tags
        return (cached is not None and cached[0][0] == key
                and cached[0][1] == tuple(self.tags)
                and self.all_items is cached[1])

    def _on_mine_tags(self, key, found: list) -> None:
        """Vos mots-cles sont faits : ils s'affichent, s'ils sont encore voulus."""
        if key != self._mine_key:
            # Un calcul remplace entre-temps (autres mots, autre collection).
            return
        self._mine_key = None
        self._mine_tags = (key, found)
        if self._at_tag_list() and self.tag_family == "mine":
            self._add_tag_items()

    def _tags_computing(self, text: str) -> None:
        """Rien a montrer encore : on dit que ca se calcule."""
        self.all_items = []
        self.items = []
        self.board.empty.setText(text)
        if self.browsing:
            self.refresh_board()
        self.item_title.setText(text)
        self.item_subtitle.setText("")
        self._show_counts()

    def _no_tags(self, why: str) -> None:
        """Aucun mot-cle : la liste se vide, et le dit.

        « Mes mots » sans aucune correspondance laissait la liste des
        dossiers a l'ecran, sous l'onglet des mots-cles.
        """
        self.all_items = []
        self.items = []
        self.board.empty.setText(why)
        if self.browsing:
            self.refresh_board()
        self.item_title.setText("Aucun mot-clé")
        self.item_subtitle.setText(why)
        self._show_counts()
        self.update_counter()
        self._tell_tags([])

    def _show_tag_list(self, found: list) -> None:
        # Dans son onglet, un mot-cle n'est pas un en-tete pose sur la liste des
        # dossiers : c'est toute la liste (`_add_tag_items` ne vient ici que
        # sous l'onglet des mots-cles).
        self.all_items = found
        self.items = self._filtered()
        self.apply_sort()
        self.board.empty.setText("Rien à afficher ici.")
        self.start_harvest()
        if self.browsing:
            self.refresh_board()
        elif self.items:
            self.show_item(min(self.index, len(self.items) - 1))
        self._show_counts()
        self.update_counter()
        self._tell_tags(found)

    def _tell_tags(self, found: list) -> None:
        """Apres « Mots-clés automatiques… » : combien ont trouve des videos."""
        if not self._tags_banner:
            return
        self._tags_banner = False
        self.show_banner(
            f"{len(found)} mot(s)-clé(s) sur {len(self.tags)} ont trouvé "
            f"des vidéos", "info")

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
            # (`on_patch`). Les mots frequents se recalculent d'eux-memes si
            # elle a change : leur cle est sa version, et une relecture qui
            # n'a rien trouve ne relance plus rien.
            self._plain_whole = True
            # La collection est entiere : « À trier » et « Orphelins » se
            # (re)calculent, et se posent en tete une fois prets.
            self._pin_refresh()
            if (self.browsing and not self.levels and not self._transient
                    and self.all_items is not self._plain_items):
                # L'onglet affiche a pu s'ouvrir avant la fin de la lecture :
                # il se complete, sans qu'on ait a le recliquer.
                if self.tab == TAB_VIDEOS:
                    kept = self._flat_list
                    if (kept is None or kept[1] is not self.all_items
                            or kept[0] != self._collection_key()):
                        # La liste ET la planche, ensemble, dans le meme
                        # ordre : la liste se rebattait sans la planche, et
                        # un clic ouvrait -- ou ✕ ecartait -- une autre video
                        # que celle de la vignette. Ce qu'on voyait garde sa
                        # place ; seul ce qui arrive s'ajoute a la fin -- des
                        # que la liste est prete, hors du fil de l'interface.
                        self._prepare_flat(show=True)
                elif self.tab == TAB_FAVS:
                    self.show_favorites()
            # La liste de l'onglet Videos se prepare pendant qu'on fait autre
            # chose : son premier clic n'aura rien a fabriquer.
            self._prepare_flat()
            if self.origin is not None and Path(self.root) == Path(self.origin):
                # L'analyse de la racine repertorie toute la collection : son
                # total est la premiere reponse a « combien de videos ? ».
                counted = sum(i.video_count for i in self._plain_items)
                self._note_state(videos=counted, scanned_at=self._stamp())
            # Les videos seules dans leur dossier : proposees au regroupement.
            QTimer.singleShot(1500, self._propose_solos)
            if thread is not None and shiboken6.isValid(thread) and getattr(
                    thread, "rescanned", 0):
                # Des videos sont arrivees (ou ont change) : le guetteur des
                # doublons regarde si elles ne sont pas deja la.
                QTimer.singleShot(8000, self._watch_new_dupes)
        if self.tab != TAB_TAGS or (self._at_tag_list()
                                    and not self._tags_up_to_date()):
            self._add_tag_items()
        self._show_counts()
        if thread is not None and not failed and shiboken6.isValid(thread):
            # Une analyse qui se termine doit le dire, meme quand elle n'a rien
            # trouve a changer : sans quoi on ne sait pas si elle tourne encore.
            incomplete = getattr(thread, "incomplete", 0)
            summary, self._solo_summary = getattr(self, "_solo_summary", None), None
            if summary is not None:
                self.show_banner(summary[0], summary[1], seconds=10, keep=summary[2])
            else:
                self.show_banner(
                    f"✓ Analyse terminée en {elapsed:.0f} s — {total} élément(s), "
                    f"{thread.rescanned} mis à jour, {thread.reused} inchangé(s)."
                    + (f" {incomplete} dossier(s) lu(s) en partie : ils seront "
                       "relus au prochain passage." if incomplete else ""),
                    "done" if not thread.rescanned and not incomplete else "info",
                )
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
        # La racine est enfin connue : c'est ici que le partage peut ouvrir.
        # Son catalogue ne se refait que si la collection a change, et hors
        # du fil de l'interface : une a huit secondes de fenetre figee a la
        # fin de chaque analyse, meme d'un sous-dossier.
        self.start_share(rebuild=False)
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
        """Garde pour les appelants : les comptes n'ont plus qu'un auteur.

        Celle-ci ecrivait aussi la barre d'avancement de l'analyse -- a
        « deja traites / elements », presque toujours zero -- et l'infobulle
        du compteur, que `_show_counts` ecrivait deja autrement. Les deux se
        contredisaient a chaque paquet de l'analyse.
        """
        self._show_counts()

    def _breadcrumb(self, item) -> str:
        """Chaîne des dossiers depuis la racine du tri jusqu'à celui de l'élément.

        En mode fichier, le seul nom du dossier parent ne suffit pas à se situer :
        plusieurs dossiers portent souvent le même nom à des endroits différents.
        """
        from . import roots as _roots
        top = (self.top_root() or item.path)
        if _roots.is_union(top):
            top = _roots.owner(item.path) or Path(item.path).parent
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

    def _mark_current_seen(self) -> None:
        item = self.current
        if (item is not None and not self.browsing and not item.is_tag
                and not self._quiet):
            INDEX.mark_seen(item.item_id)

    def _burst_next(self) -> None:
        if not self.cfg["burst"] or self.browsing or not self.items or self._quiet:
            return
        if self._stays_in_folder():
            # La rafale reste dans le dossier, comme → ; avant la borne de la
            # liste, puisque la voisine dans le dossier fait le tour.
            return self.step(1)
        if self.index + 1 < len(self.items):
            self.show_item(self.index + 1)

    def toggle_burst(self) -> None:
        self.cfg["burst"] = not self.cfg["burst"]
        self.cfg.save_soon()
        action = self._menu_by_text.get("Rafale : passer tout seul après 8 s")
        if action is not None:
            action.setChecked(bool(self.cfg["burst"]))
        self._show_activity()
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

    def _peek_timer(self) -> QTimer:
        timer = getattr(self, "_peek_wait", None)
        if timer is None:
            timer = self._peek_wait = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(250)
            timer.timeout.connect(self._peek_if_held)
        return timer

    def _peek_if_held(self) -> None:
        if getattr(self, "_shift_held", False):
            self.peek_show()

    def peek_show(self) -> None:
        """Maj maintenue : neuf instants, et les neuf premieres destinations."""
        item = self.current
        if (self.browsing or item is None or item.kind == MODE_FOLDERS
                or item.locked or self.single.peeking or photo_mode()):
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

    DECODING_ENTRIES = {
        "auto": ("Automatique (recommandé)",
                 "Prisme choisit selon ce PC : le processeur s'il est puissant, "
                 "la carte graphique sinon."),
        "cpu": ("Processeur — aperçus plus vifs",
                "Les aperçus survolés démarrent plus vite ; le processeur travaille "
                "davantage quand plusieurs vidéos jouent."),
        "gpu": ("Carte graphique — soulage le processeur",
                "Quatre fois moins de processeur sur le mur ; chaque aperçu survolé "
                "démarre un peu moins vite."),
    }

    def _dress_decoding_menu(self) -> None:
        """Le choix en cours coche, et ce que chaque choix change, en une ligne."""
        chosen = self.cfg["decoding"] or "auto"
        for key, (text, tip) in self.DECODING_ENTRIES.items():
            action = self._menu_by_text.get(text)
            if action is None:
                continue
            action.setCheckable(True)
            action.setChecked(key == chosen)
            action.setToolTip(tip)
            parent = action.parent()
            if hasattr(parent, "setToolTipsVisible"):
                parent.setToolTipsVisible(True)

    def set_decoding(self, choice: str) -> None:
        """Processeur ou carte graphique pour decoder les videos (voir main.py).

        Qt fige ce choix pour tout le programme a son demarrage : il vaut au
        prochain lancement, et le mur ne peut pas l'avoir seul.
        """
        self.cfg["decoding"] = choice
        if choice != "auto":
            self.cfg["hw_decoding"] = False     # l'ancien reglage ne decide plus
        self.cfg.save()
        self._dress_decoding_menu()
        from .config import wants_hardware
        hardware = wants_hardware(choice, bool(self.cfg["hw_decoding"]),
                                        os.cpu_count() or 0)
        who = "la carte graphique" if hardware else "le processeur"
        self.show_banner(
            (f"Décodage automatique : sur ce PC, ce sera {who}. " if choice == "auto"
             else f"Décodage par {who}. ")
            + "Pris en compte au prochain lancement de Prisme.", "info")

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

        Les lecteurs se mettent en pause -- une image qui bouge ou un son qui
        continue trahirait la page --, mais ne s'arretent pas : au retour, la
        video reprend a l'instant ou on l'a laissee, le lecteur de cote et sa
        liste sont la, le mur montre les memes panneaux. Rien ne s'affiche
        par-dessus la page, pas meme la premiere fois : c'est souvent
        justement parce que quelqu'un arrive qu'on s'en sert.
        """
        if self._quiet or self._closing:
            return
        # Le lecteur flottant avant tout : il est par-dessus tout le reste,
        # et c'est lui qu'on verrait. La video retourne a la fiche, en pause.
        if self.floating.active:
            self.leave_floating(activate=False)
        self.float_timer.stop()
        # Les menus et les dialogues ouverts d'abord : ils restaient par-dessus
        # la page neutre, noms de fichiers compris.
        self._close_overlays()
        # Puis les autres fenetres de Prisme -- le Labo IA, la recherche web :
        # elles restaient a l'ecran et dans la barre des taches. Cachees, pas
        # fermees : au retour, elles reviennent comme on les avait laissees.
        self._quiet_hidden = []
        for widget in QApplication.topLevelWidgets():
            if (widget is self or not widget.isVisible()
                    or isinstance(widget, QDialog)
                    or widget.windowType() not in (Qt.Window, Qt.Dialog)):
                continue
            self._quiet_hidden.append(widget)
            widget.hide()
        self._quiet = True
        self._quiet_from = self.stack.currentIndex()
        self._quiet_show = False
        # Ce qui flotte par-dessus la fenetre d'abord, puis la page : elle
        # doit paraitre tout de suite.
        self.banner.hide()
        self.banner_timer.stop()
        self.single_bar.hide()
        self.aside_bar.hide()
        self.single.marks.hide()
        self.radial.close_menu()
        QToolTip.hideText()
        # Ni « vu » ni rafale en coulisse : une video regardee 0,4 s sortait
        # des « Non vus » apres cinq secondes de repli, et la rafale relancait
        # la suivante, avec le son, derriere la page neutre.
        self._quiet_timers = tuple(timer for timer in (self.seen_timer,
                                                       self.burst_timer)
                                   if timer.isActive())
        for timer in self._quiet_timers:
            timer.stop()
        playing = QMediaPlayer.PlaybackState.PlayingState
        self._quiet_playing = [
            player for player in
            [self.single.player, self.aside_player.player]
            + [pane.player for pane in self.wall.panes]
            if player.playbackState() == playing]
        # Le mur en plein ecran : la page neutre n'a pas a l'etre, elle
        # attirait l'oeil. La fenetre reprend sa taille d'avant le mur.
        # Le cinema de meme : il est en plein ecran lui aussi.
        self._quiet_full = self.wall_full or self._cinema_kept is not None
        if self._quiet_full:
            kept = (getattr(self, "_wall_kept", (False, False, None))[2]
                    if self.wall_full else self._cinema_kept)
            state = (kept & ~Qt.WindowFullScreen if kept is not None
                     else Qt.WindowMaximized)
            self.setWindowState(state)
        self.quiet_page.start()
        # Caches, les lecteurs se mettent en pause au lieu de s'arreter.
        for holder in (self.single, self.aside_player, self.wall):
            holder.hold = True
        try:
            self.stack.setCurrentIndex(PAGE_QUIET)
        finally:
            for holder in (self.single, self.aside_player, self.wall):
                holder.hold = False
        self.setWindowTitle(self.quiet_page.title())
        # L'icone de Prisme restait dans la barre des taches, sous le titre
        # d'un autre logiciel : celle de l'ecran choisi.
        self._quiet_icon = (self.windowIcon(),
                            self.testAttribute(Qt.WA_SetWindowIcon))
        self.setWindowIcon(self.style().standardIcon(self.quiet_page.icon_kind()))
        self.quiet_page.setFocus()
        QTimer.singleShot(0, self._hush_for_quiet)

    def _hush_for_quiet(self) -> None:
        """Ce qui jouerait encore se tait, une fois la page de repli a l'ecran."""
        if not self._quiet:
            return
        self._hush_players()
        self.single.peek_end()
        self.wall.end_peeks()
        self._wall_peek = None
        self.stack.currentWidget().setFocus()

    def _close_overlays(self) -> None:
        """Ferme menus et dialogues ouverts, dans le sens prudent (Annuler).

        « Supprimer ce dossier ? » rend Non, une saisie rend « annule », les
        mots-cles et les destinations ne changent pas. Un dialogue qui refuse
        de se fermer (une restauration en cours) est laisse tel quel.
        """
        for _ in range(8):
            popup = QApplication.activePopupWidget()
            if popup is None:
                break
            popup.close()
            if QApplication.activePopupWidget() is popup:
                break
        for _ in range(8):
            modal = QApplication.activeModalWidget()
            if modal is None or modal is self:
                break
            if isinstance(modal, QDialog):
                modal.reject()
            else:
                modal.close()
            if QApplication.activeModalWidget() is modal:
                break
        for widget in QApplication.topLevelWidgets():
            if (isinstance(widget, QDialog) and widget.isVisible()
                    and widget is not self):
                widget.reject()
        # Plus de dialogue : le filtre de Ctrl+K n'a plus lieu d'etre.
        self._quiet_keys.follow()

    def _quiet_title(self, title: str) -> None:
        if self._quiet:
            self.setWindowTitle(title)

    def set_quiet_cover(self, key: str) -> None:
        """⋯ › Confidentialité › Écran de repli : ce que montre Ctrl+K."""
        self.cfg["quiet_cover"] = key
        self.cfg.save_soon()
        self.quiet_page.set_cover(key)
        self._dress_quiet_menu()
        label = next((text for k, text, _cls in QUIET_COVERS if k == key), key)
        self.show_banner(f"Écran de repli : « {label} ». Ctrl+K (ou la lune) pour le "
                         "voir ; un double-clic ou Ctrl+K pour revenir.", "quiet")

    def _dress_quiet_menu(self) -> None:
        """Une coche devant l'écran choisi."""
        chosen = self.cfg["quiet_cover"] or "update"
        for key, label, _cls in QUIET_COVERS:
            action = self._menu_by_text.get(label)
            if action is not None:
                action.setCheckable(True)
                action.setChecked(key == chosen)

    def quiet_now(self) -> None:
        """Le repli, d'ou qu'on le demande -- un dialogue, une autre fenetre.

        N'y fait jamais revenir : presse deux fois, il ne doit pas ramener
        Prisme a l'ecran au moment ou l'on voulait le cacher.
        """
        if self._closing:
            return
        if self._quiet:
            self._close_overlays()
            return
        self.enter_quiet()

    def _quiet_key(self) -> None:
        """Ctrl+K, par le raccourci de l'application (menus ouverts compris)."""
        if QApplication.activePopupWidget() is not None:
            # Depuis un menu : on se cache, on ne revient pas.
            return self.quiet_now()
        self.toggle_quiet()

    def leave_quiet(self) -> None:
        """Sortir du repli : par le cadenas si un code est pose (et, depuis le
        cadenas, retour a la page neutre) ; sinon, tout de suite."""
        if not self._quiet:
            return
        if self.pin_set():
            if self.stack.currentIndex() == PAGE_LOCK:
                return self.show_quiet_page()
            return self.show_lock()
        self._leave_quiet_now()

    def show_lock(self) -> None:
        """Le cadenas, a la place de la page neutre."""
        self.lock_page.reset()
        self.stack.setCurrentIndex(PAGE_LOCK)
        self.lock_page.setFocus()

    def show_quiet_page(self) -> None:
        """Retour du cadenas a la page neutre."""
        self.stack.setCurrentIndex(PAGE_QUIET)
        self.quiet_page.setFocus()

    def lock_now(self) -> None:
        """Au lancement, avec un code pose : le repli d'abord, puis le cadenas.
        Rien de la collection (ni titre, ni icone) avant le bon code."""
        if not self.pin_set() or self._quiet:
            return
        self._quiet_by_lock = True
        self.enter_quiet()
        self.show_lock()

    def _leave_quiet_now(self) -> None:
        """On revient là où l'on était, dans l'état où on l'avait laissé."""
        if not self._quiet:
            return
        self.lock_page.reset()
        self.quiet_page.stop()
        self._quiet = False
        hidden, self._quiet_hidden = getattr(self, "_quiet_hidden", []), []
        for widget in hidden:
            if shiboken6.isValid(widget):
                widget.show()
        page = self._quiet_from
        self.stack.setCurrentIndex(page)
        self.setWindowTitle(APP_NAME)
        icon_was, own = self._quiet_icon or (QIcon(), False)
        self.setWindowIcon(icon_was if own else QIcon())
        if self._quiet_full and (self.wall_full or self._cinema_kept is not None):
            self.showFullScreen()
            self.activateWindow()
        self.setFocus()
        # Echap ou Ctrl+K maintenus : la repetition ne continue pas ici.
        self._eat_repeat = True
        playing, self._quiet_playing = self._quiet_playing, []
        timers, self._quiet_timers = self._quiet_timers, ()
        if page == PAGE_SORT:
            if self._quiet_show:
                # Une autre fiche a ete demandee pendant le repli (rafale,
                # fin d'un rangement) : elle s'ouvre maintenant.
                self._quiet_show = False
                self.show_item(self.index)
            else:
                if self.single.player in playing:
                    self.single.player.play()
                # Le delai « vu » et la rafale repartent de zero : la video
                # n'a pas ete regardee pendant qu'elle etait cachee.
                for timer in timers:
                    timer.start()
            if self.aside_player.player in playing and not self.aside.isHidden():
                self.aside_player.player.play()
            if self.tab == TAB_SPLIT and self.viewer.currentWidget() is self.wall:
                self.wall.resume(playing)
                if self.wall.pool and any(not pane.video_path
                                          for pane in self.wall.panes):
                    self.wall.fill_empty()
        self._quiet_show = False
        self._tell_after_quiet()

    def _tell_after_quiet(self) -> None:
        """Ce qui s'est dit pendant le repli, puis, une fois, comment on en sort."""
        if any(tone == "error" for _t, tone, _a, _s in self._held_banners):
            return self._show_held_banners()
        if self._quiet_by_lock:
            # Ouvert par le cadenas du lancement : pas un vrai repli, rien a
            # expliquer.
            self._quiet_by_lock = False
            return self._show_held_banners()
        if not self.cfg["quiet_explained"]:
            # Au premier retour, et non a la premiere entree : une boite au
            # milieu de la page neutre annoncait que c'etait un leurre.
            self.cfg["quiet_explained"] = True
            self.cfg.save_soon()
            self._held_banners = []
            self.show_banner(
                "C'était le repli : Ctrl+K pour y passer (Ctrl+Alt+K depuis "
                "une autre fenêtre), et Ctrl+K, Échap ou un double-clic "
                "n'importe où pour en revenir.", "info", seconds=8, keep=True)
            return
        self._show_held_banners()

    # -- le code PIN ---------------------------------------------------------
    def pin_set(self) -> bool:
        return pin_is_set(self.cfg)

    def _confirm_pin(self, reason: str) -> bool:
        return ask_pin(self.cfg, self, reason)

    def edit_pin(self) -> None:
        """Poser, changer ou retirer le code PIN."""
        dialog = PinDialog(self.cfg, self)
        if dialog.exec() == dialog.DialogCode.Accepted and dialog.said:
            self.show_banner(dialog.said, "done")
        self.setFocus()

    # -- le Labo IA (essai) --------------------------------------------------
    def _labo_follow(self, src, dst) -> None:
        """Le Labo IA suit les fichiers ranges : regroupes, puis appliques
        quelques secondes apres le dernier (un groupe de cent fichiers ne
        reecrit pas les index cent fois)."""
        if src is None or dst is None:
            return
        moves = self.__dict__.setdefault("_labo_moves", {})
        moves[str(src)] = str(dst)
        timer = self.__dict__.get("_labo_timer")
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(3000)
            timer.timeout.connect(self._labo_flush)
            self._labo_timer = timer
        timer.start()

    def _labo_flush(self) -> None:
        moves, self._labo_moves = dict(self.__dict__.get("_labo_moves", {})), {}
        if not moves:
            return
        lab = getattr(self, "_lab_window", None)
        if lab is not None and shiboken6.isValid(lab):
            lab.follow_moves(moves)
            return
        import threading
        from . import ia
        threading.Thread(target=lambda: ia.rename_videos(moves), daemon=True,
                         name="labo-suit").start()

    # -- les demandes du telephone ------------------------------------------------
    def _import_phone_videos(self) -> None:
        """Les videos que le NAS a telechargees pour les telephones : rangees
        dans la collection, hors du fil de l'interface (`nas_publish`)."""
        from . import roots
        top = self.top_root()
        if (top is None or roots.is_union(top) or getattr(self, "_phone_importing", False)
                or time.time() - getattr(self, "_phone_imported_at", 0.0) < 120):
            return
        from .nas_publish import import_downloads
        from .tunnel import Chore
        self._phone_importing = True
        self._phone_imported_at = time.time()

        def then(count) -> None:
            self._phone_importing = False
            if count:
                from .liens import FOLDER_NAME
                self.show_banner(
                    f"{count} vidéo(s) envoyée(s) depuis le téléphone, rangée(s) dans "
                    f"« {FOLDER_NAME} ».", "done", seconds=10)

        Chore(lambda: import_downloads(top), self, fallback=0, then=then).start()

    def _demande_paths(self) -> list:
        """Ou les demandes arrivent : chez Prisme (page servie par le PC), et
        dans le partage du NAS (page servie par le NAS)."""
        from .config import PRIVATE_DIR
        from .demandes import FILE_NAME
        from .nas_publish import FOLDER, share_root
        paths = [PRIVATE_DIR / FILE_NAME]
        top = self.top_root()
        share = share_root(top) if top is not None else None
        if share is not None:
            paths.append(Path(share) / FOLDER / "etat" / FILE_NAME)
        return paths

    def _poll_demandes(self) -> None:
        if self._closing or getattr(self, "_asks_reading", False):
            return
        self._import_phone_videos()
        from . import demandes
        from .tunnel import Chore
        paths = self._demande_paths()
        self._asks_reading = True
        Chore(lambda: demandes.read_all(paths), self, fallback=None,
              then=self._demandes_read).start()

    def _demandes_read(self, entries) -> None:
        self._asks_reading = False
        if entries is None:
            return
        self._asks = entries
        seen = set(self.cfg["demandes_vues"] or [])
        fresh = [e for e in entries if e["id"] not in seen]
        if fresh:
            self.ask_badge.setText(f"➤ {len(fresh)} demande{'s' if len(fresh) > 1 else ''}")
            if not self.ask_badge.isVisible():
                self.show_banner(f"Nouvelle demande du téléphone : « {fresh[0]['text'][:80]} »",
                                 "info")
            self.ask_badge.show()
        else:
            self.ask_badge.hide()

    def open_telegram(self) -> None:
        """Recherche de channels publics et alertes sur ceux qu'on suit."""
        from .telegram_hub import TelegramDialog
        dialog = getattr(self, "_telegram_dialog", None)
        if dialog is None or not shiboken6.isValid(dialog):
            dialog = TelegramDialog(self)
            self._telegram_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_demandes(self) -> None:
        """Les demandes reçues ; les ouvrir, c'est les avoir vues."""
        from . import demandes
        from .demandes_dialog import DemandesDialog
        paths = self._demande_paths()
        entries = demandes.read_all(paths)
        self.cfg["demandes_vues"] = sorted({e["id"] for e in entries})
        self.cfg.save_soon()
        self.ask_badge.hide()
        dialog = DemandesDialog(self, entries, paths)
        dialog.exec()
        self.setFocus()

    def search_web_for(self, text: str) -> None:
        """La recherche web, preremplie avec cette demande."""
        window = self._web_search_window()
        window.queries.setPlainText(text)
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def search_labo_for(self, text: str) -> None:
        """Le Labo IA, cette demande deja tapee et lancee."""
        self.open_ai_lab()
        lab = getattr(self, "_lab_window", None)
        if lab is None or not shiboken6.isValid(lab):
            return
        lab.tabs.setCurrentIndex(0)
        lab.query.setText(text)
        QTimer.singleShot(300, lab._search)

    def open_ai_lab(self) -> None:
        """Chercher une scene par sa description ; collections par nom (labo.py)."""
        window = getattr(self, "_lab_window", None)
        if window is not None and shiboken6.isValid(window):
            window.showNormal()
            window.raise_()
            window.activateWindow()
            return
        from .labo import LaboWindow
        window = LaboWindow(self)
        window.setAttribute(Qt.WA_DeleteOnClose)
        self._lab_window = window
        window.show()

    # -- les mises a jour -------------------------------------------------------
    def check_updates(self, quiet: bool = True) -> None:
        """Regarde, hors du fil de l'interface, s'il existe une version plus
        recente. `quiet` (au lancement, toutes les six heures) : rien n'est dit
        quand il n'y a rien -- ni quand la source ne repond pas."""
        if self._update_busy or self._closing:
            return
        where = update.source(self.cfg["update_source"] or "")
        if not where:
            if not quiet:
                self.show_banner(
                    f"Prisme {update.__version__} — "
                    + ("lancé depuis ses sources : il se met à jour par git."
                       if not update.frozen() else
                       "aucune source de mises à jour n'est réglée."),
                    "quiet", seconds=8)
            return
        from .tunnel import Chore

        def work():
            try:
                return update.check(where)
            except update.UpdateError as trouble:
                return trouble

        Chore(work, self, fallback=None,
              then=lambda found: self._update_checked(found, quiet)).start()

    @staticmethod
    def _update_notes(found: dict) -> str:
        notes = found.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        return "\n".join(f"• {line}" for line in notes if str(line).strip())

    def _update_checked(self, found, quiet: bool) -> None:
        if not shiboken6.isValid(self) or self._closing or self._update_busy:
            return
        if isinstance(found, update.UpdateError):
            if not quiet:
                self.show_banner(str(found), "error", seconds=8)
            return
        self._update_found = found
        if not found:
            self.update_badge.hide()
            if getattr(self, "_update_tray", None) is not None:
                self._update_tray.hide()
            if not quiet:
                self.show_banner(f"Prisme est à jour (version {update.__version__}).",
                                 "done")
            return
        version = found["version"]
        self.update_badge.setText(f"⬆ Mise à jour {version}")
        notes = self._update_notes(found)
        self.update_badge.setToolTip(
            f"Prisme {version} est disponible — cliquez pour mettre à jour"
            + (f"\n\n{notes}" if notes else ""))
        self.update_badge.show()
        if not quiet:
            return self.install_update()
        if self._update_said != version:
            self._update_said = version
            self.show_banner(
                f"Prisme {version} est disponible (vous avez la {update.__version__}). "
                "Cliquez ici pour mettre à jour — ou plus tard, par la pastille "
                "en haut.", "info", action=self.install_update, seconds=20)
            self._notify_update(version, notes)

    def _notify_update(self, version: str, notes: str) -> None:
        """Une notification Windows, et Prisme qui clignote dans la barre des
        taches : le bandeau seul passait inapercu, fenetre reduite ou cachee
        derriere une autre."""
        QApplication.alert(self, 0)
        from PySide6.QtWidgets import QSystemTrayIcon
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = getattr(self, "_update_tray", None)
        if tray is None:
            tray = QSystemTrayIcon(app_icon(), self)
            tray.messageClicked.connect(self._update_from_tray)
            tray.activated.connect(lambda _why: self._update_from_tray())
            self._update_tray = tray
        tray.setToolTip(f"Prisme {version} est disponible — cliquez pour mettre à jour")
        tray.show()
        first = notes.splitlines()[0].lstrip("• ") if notes else ""
        tray.showMessage(f"Prisme {version} est disponible",
                         (first + "\n" if first else "")
                         + "Cliquez pour mettre à jour.",
                         QSystemTrayIcon.Information, 15000)

    def _update_from_tray(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.install_update()

    def install_update(self) -> None:
        """Dit ce qui change, puis telecharge, verifie et pose -- d'un clic."""
        ready = getattr(self, "_update_script", None)
        if ready is not None and ready.exists():
            return self._close_for_update(ready)
        found = self._update_found
        if found is None:
            return self.check_updates(quiet=False)
        if self._update_busy:
            self.show_banner("Mise à jour en cours de téléchargement…", "quiet")
            return
        if not update.frozen():
            self.show_banner("Prisme tourne depuis ses sources : mettez-le à jour "
                             "par git.", "quiet", seconds=8)
            return
        version = found["version"]
        notes = self._update_notes(found)
        box = QMessageBox(self)
        box.setWindowTitle("Mettre à jour Prisme")
        box.setIcon(QMessageBox.Information)
        box.setText(f"<b>Prisme {version} est disponible</b> "
                    f"(vous avez la {update.__version__}).")
        box.setInformativeText(
            (f"Nouveautés :\n{notes}\n\n" if notes else "")
            + "Prisme télécharge la nouvelle version, se ferme, puis se rouvre "
            "tout seul quelques secondes plus tard. Réglages, index, vignettes "
            "et favoris ne bougent pas.")
        go = box.addButton("Mettre à jour maintenant", QMessageBox.AcceptRole)
        box.addButton("Plus tard", QMessageBox.RejectRole)
        box.setDefaultButton(go)
        box.exec()
        if box.clickedButton() is not go:
            return
        from .tunnel import Chore
        where = update.source(self.cfg["update_source"] or "")
        self._update_busy = True
        self._update_progress = (0, 0)

        def progress(done: int, total: int) -> None:
            self._update_progress = (done, total)

        def work():
            try:
                return update.prepare(where, found, progress,
                                      should_stop=lambda: self._closing)
            except update.UpdateError as trouble:
                return trouble
            except Exception as trouble:            # noqa: BLE001
                return update.UpdateError(f"Mise à jour impossible : {trouble}")

        self._update_ticker = QTimer(self)
        self._update_ticker.setInterval(400)
        self._update_ticker.timeout.connect(self._update_tick)
        self._update_ticker.start()
        self._update_tick()
        Chore(work, self, fallback=None, then=self._update_ready).start()

    def _update_tick(self) -> None:
        done, total = getattr(self, "_update_progress", (0, 0))
        share = f" {int(100 * done / total)} %" if total else ""
        self.update_badge.setText(f"⬆ Téléchargement{share}")

    def _update_ready(self, script) -> None:
        if not shiboken6.isValid(self):
            return
        self._update_ticker.stop()
        self._update_busy = False
        found = self._update_found or {}
        self.update_badge.setText(f"⬆ Mise à jour {found.get('version', '')}")
        if self._closing:
            return
        if not isinstance(script, Path):
            self.show_banner(str(script) or "Mise à jour impossible.", "error",
                             seconds=12)
            return
        # Fermer d'abord, et ne lancer la pose qu'une fois la fermeture
        # acceptee : refusee (des telechargements en cours), la pose aurait
        # attendu pour rien, puis renonce sans rien dire.
        self.show_banner("Mise à jour prête : Prisme se ferme et se rouvre…", "done")
        QTimer.singleShot(400, lambda: self._close_for_update(script))

    def _close_for_update(self, script: Path) -> None:
        if not self.close():
            # Deja telechargee et verifiee : la pastille la posera sans tout
            # refaire.
            self._update_script = script
            self.update_badge.setText("⬆ Poser la mise à jour")
            self.show_banner("Mise à jour prête : cliquez la pastille en haut quand "
                             "Prisme peut se fermer.", "info", seconds=10)
            return
        try:
            self.cfg["update_tried"] = (self._update_found or {}).get("version", "")
            self.cfg.save()
            update.start_swap(script)
        except OSError as trouble:
            # Trop tard pour le dire a l'ecran : la fenetre est fermee.
            try:
                (script.parent / "pose.log").open("a", encoding="utf-8").write(
                    f"Lancement de la pose impossible : {trouble}\n")
            except OSError:
                pass

    def _said_new_version(self) -> None:
        """Au lancement : la version a change depuis la derniere fois ? On le
        dit, avec ce que la pose a note. Et l'on range ce qu'elle a laisse."""
        update.tidy()
        before = self.cfg["version_seen"] or ""
        if before != update.__version__:
            self.cfg["version_seen"] = update.__version__
            self.cfg.save_soon()
            if before and update.newer(update.__version__, before):
                self.show_banner(f"Prisme est passé à la version {update.__version__}.",
                                 "done", seconds=10)
        # Une pose lancee a la fermeture, et pourtant toujours l'ancienne
        # version : on le dit, avec ce que la pose a note.
        tried = self.cfg["update_tried"] or ""
        if tried:
            self.cfg["update_tried"] = ""
            self.cfg.save_soon()
            if update.newer(tried, update.__version__):
                said = update.last_log_line()
                self.show_banner(
                    f"La mise à jour {tried} n'a pas pu se poser"
                    + (f" : {said.split(' ', 2)[-1]}" if said else ".")
                    + " Réessayez par la pastille en haut.", "error", seconds=20)

    # -- l'essai et la licence -------------------------------------------------
    def _show_trial(self) -> None:
        """« Essai · N jours » en haut de la fenetre, pendant l'essai."""
        try:
            state = licence.status(self.cfg)
        except Exception:                                   # noqa: BLE001
            return self.trial_badge.hide()
        if state.mode == "essai":
            self.trial_badge.setText(f"Essai · {state.days_left} jour"
                                     f"{'s' if state.days_left > 1 else ''}")
            self.trial_badge.show()
        else:
            self.trial_badge.hide()

    def open_licence(self) -> None:
        from .licence_dialog import LicenceDialog
        LicenceDialog(self.cfg, self).exec()
        self._show_trial()
        self.setFocus()

    def renew_licence(self) -> None:
        """Un abonnement dans ses dix derniers jours : une cle prolongee est
        demandee au site, hors du fil de l'interface."""
        if not licence.PUBLIC_KEY or not licence.renewal_due(self.cfg):
            return
        import threading
        self._renewal_said = None

        def ask() -> None:
            self._renewal_said = licence.renew(self.cfg)
        threading.Thread(target=ask, daemon=True, name="prisme-licence").start()
        QTimer.singleShot(12000, self._took_renewal)

    def _took_renewal(self) -> None:
        said = getattr(self, "_renewal_said", None)
        if said:
            self.show_banner(said, "done")
            self._show_trial()

    def toggle_quiet(self) -> None:
        if self._quiet:
            self.leave_quiet()
        else:
            self.enter_quiet()

    def _page(self) -> int:
        """La page ou l'on est -- pendant le repli, celle qu'on retrouvera."""
        if self._quiet:
            return self._quiet_from
        return self.stack.currentIndex()

    def _goto_page(self, page: int) -> bool:
        """Change de page, sauf pendant le repli : on note ou revenir.

        Un tri qui finissait derriere la page neutre y posait son bilan, a la
        place de la page neutre, et Ctrl+K n'y faisait plus rien.
        """
        if self._quiet:
            self._quiet_from = page
            return False
        self.stack.setCurrentIndex(page)
        return True

    def toggle_veiled(self) -> None:
        """Montre, ou remasque, les dossiers mis de côté.

        Le masque vaut partout à la fois : listes, vignettes, mots-clés, mur,
        doublons, et le partage à distance. Le lever demande donc de refaire
        la liste — rien n'est relu sur le disque pour autant.
        """
        wanted = not self.cfg["show_veiled"]
        if wanted and not self._confirm_pin("Code d'accès pour afficher les dossiers masqués :"):
            return self.setFocus()
        self.cfg["show_veiled"] = wanted
        self.cfg.save()
        self._refresh_veil()
        names = ", ".join(self._active_veil()) or "—"
        self.show_banner(
            f"Dossiers masqués affichés : {names}." if wanted
            else f"Dossiers masqués de nouveau cachés : {names}.",
            "info" if wanted else "quiet")

    def _active_veil(self) -> list:
        """Les noms de la liste qui masquent vraiment : les cochés."""
        off = {str(n).casefold() for n in (self.cfg["veiled_off"] or [])}
        return [n for n in self.cfg["veiled_names"] or []
                if str(n).casefold() not in off]

    def edit_veiled(self) -> None:
        """La liste des dossiers masqués : ajouter, retirer, réafficher."""
        before = [n.casefold() for n in self._active_veil()]
        from . import roots
        here = self.root if self.root is not None and not roots.is_union(self.root) else None
        if here is None and self.root is not None:
            members = roots.members(self.root)
            here = members[0] if members else None
        dialog = VeilDialog(self.cfg["veiled_names"] or [],
                            self.cfg["veiled_off"] or [], self,
                            start=str(here) if here is not None else "")
        if dialog.exec() != dialog.DialogCode.Accepted:
            return self.setFocus()
        names, off = dialog.result_veil()
        self.cfg["veiled_names"] = names
        self.cfg["veiled_off"] = off
        self.cfg.save()
        after = [n.casefold() for n in self._active_veil()]
        if after == before:
            return self.setFocus()
        self._refresh_veil()
        shown = ", ".join(self._active_veil()) or "aucun"
        self.show_banner(f"Dossiers masqués : {shown}.", "info")
        # Ce qui etait masque n'a jamais ete lu : l'analyse passait a cote.
        # Le reafficher demande donc de relire ces dossiers-la.
        freed = [n for n in before if n not in after]
        if freed and not self.cfg["show_veiled"] and self.root is not None:
            self._rescan_for_unveiled()
        self.setFocus()

    def _rescan_for_unveiled(self) -> None:
        """Propose de relire la collection pour y trouver ce qu'on réaffiche."""
        top = self.top_root()
        if top is None:
            return
        answer = QMessageBox.question(
            self, "Relire la collection ?",
            "Les dossiers que vous réaffichez n'avaient jamais été lus : ils "
            f"n'apparaîtront qu'après une réanalyse de « {top} ».\n\n"
            "Sur le NAS, cela prend plusieurs minutes. La lancer maintenant ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self._reread_collection(top)

    def _refresh_veil(self) -> None:
        """Pose le masque en cours et refait tout ce qui en dépend."""
        set_veiled(self._active_veil(), self.cfg["show_veiled"])
        self._name_veil_action()
        self._touch()
        self._top_tags = None
        self.plans = {}
        # Le catalogue du partage suit, hors du fil de l'interface.
        self.start_share()
        if self.root is not None:
            if self.tab == TAB_VIDEOS and not self._transient:
                self.show_videos_tab()
            else:
                self.items = self._filtered()
                self.apply_sort()
            if self.browsing:
                self.refresh_board()
            self._show_counts()

    def _name_veil_action(self) -> None:
        """L'entrée du menu dit ce qu'elle fera, et sur quoi."""
        # Leur nombre, pas leurs noms : ecrits en clair dans le menu, ils
        # se lisaient par-dessus l'epaule -- ce que le repli veut eviter.
        count = len(self._active_veil())
        label = ("Masquer de nouveau les dossiers mis de côté"
                 if self.cfg["show_veiled"]
                 else f"Afficher les dossiers masqués ({count})")
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
        """Une serrure est-elle posee -- un mot de passe, ou la cle du lien
        d'invitation ? Sans l'une ou l'autre, on ne sert rien."""
        return bool((self.cfg["share_salt"] and self.cfg["share_digest"])
                    or self.share_invite())

    def share_invite(self) -> str:
        """La cle du lien d'invitation, tiree au premier besoin."""
        if not self.cfg["share_invite"]:
            from .web import new_invite
            self.cfg["share_invite"] = new_invite()
            self.cfg.save_soon()
        return self.cfg["share_invite"]

    def renew_invite(self) -> None:
        """Un nouveau lien : l'ancien ne mene plus nulle part, et tous ceux
        qui etaient entres -- par lien ou par mot de passe -- sont deconnectes."""
        from .web import new_invite
        self.cfg["share_invite"] = new_invite()
        self.cfg.save()
        server = self.share_server
        if server is not None:
            server.invite = self.cfg["share_invite"]
            with server.guard.lock:
                server.guard.sessions.clear()
        self._nas_access_mine = True
        if not self.publish_nas(force=True):
            self._nas_access_mine = False
            self._publish_nas_key()

    def _publish_nas_key(self) -> None:
        """La seule cle vers le NAS, partage du PC coupe : sans elle, le NAS
        gardait l'ancien lien et refusait le nouveau. Dans un fil : le NAS
        peut mettre des secondes a repondre."""
        from .nas_publish import publish_access
        top = self.top_root()
        if not self.cfg["nas_publish"] or top is None or media_kind() != KIND_VIDEO:
            return
        access = {"invite": self.share_invite(), "salt": self.cfg["share_salt"],
                  "digest": self.cfg["share_digest"]}
        threading.Thread(target=publish_access,
                         args=(top, access, self.cfg["nas_tailnet"] or ""),
                         name="prisme-cle-nas", daemon=True).start()

    def nas_tailnet(self) -> str:
        """Le nom du reseau Tailscale (« tail38dac2.ts.net »), tire de
        l'adresse fixe du PC : le NAS y prendra le nom « prisme-nas »."""
        address = self.tunnel_address or ""
        host = address.split("://", 1)[-1].split("/", 1)[0]
        if self.cfg["tunnel_kind"] == "tailscale" and host.endswith(".ts.net"):
            tailnet = host.split(".", 1)[1]
            if tailnet != self.cfg["nas_tailnet"]:
                self.cfg["nas_tailnet"] = tailnet
                self.cfg.save_soon()
        return self.cfg["nas_tailnet"]

    def publish_nas(self, force: bool = False) -> bool:
        """Depose sur le NAS le catalogue, les vignettes et la cle du partage.

        Seulement la collection de videos, et seulement si elle vit sur un
        partage reseau. Tout se fait dans un fil : la premiere fois, les
        vignettes se recopient pendant de longues minutes.
        """
        from .nas_publish import PUBLISHER
        server = self.share_server
        if (not self.cfg["nas_publish"] or server is None
                or media_kind() != KIND_VIDEO or self.top_root() is None):
            return False
        # Une analyse qui finit toutes les dix secondes ne republie pas a
        # chaque fois : le catalogue pese, et le NAS relit toutes les 30 s.
        if not force and time.time() - PUBLISHER.last_ok < 60:
            return False
        top = self.top_root()
        if getattr(self, "_nas_access_mine", False):
            # « Nouveau lien », ou un mot de passe change ici : c'est ce PC
            # qui decide, et l'autre PC s'y rangera a sa prochaine publication.
            self._nas_access_mine = False
            return self._publish_nas_now(server, top)
        from .nas_publish import published_access
        from .tunnel import Chore

        def then(found) -> None:
            if not shiboken6.isValid(self) or self._closing:
                return
            self._adopt_nas_access(found)
            if self.share_server is server:
                self._publish_nas_now(server, top)

        Chore(lambda: published_access(top), self, fallback=None, then=then).start()
        return True

    def _publish_nas_now(self, server, top) -> bool:
        from .nas_publish import PUBLISHER
        access = {"invite": self.share_invite(), "salt": self.cfg["share_salt"],
                  "digest": self.cfg["share_digest"]}
        return PUBLISHER.start(server.library, top, access,
                               self.nas_tailnet(), self._share_icons())

    def _adopt_nas_access(self, found) -> None:
        """La cle (et le mot de passe) deja sur le NAS l'emportent.

        Deux PC publient sur le meme NAS, chacun avec sa cle : le dernier a
        publier ecrasait celle de l'autre, et le lien du telephone cessait
        de marcher sans que personne n'ait rien demande. Le NAS est la
        reference : ce PC prend sa cle au lieu de la remplacer."""
        if not isinstance(found, dict):
            return
        cfg, server = self.cfg, self.share_server
        changed = False
        key = str(found.get("invite") or "")
        if key and key != cfg["share_invite"]:
            cfg["share_invite"] = key
            changed = True
            if server is not None:
                server.invite = key
        salt, digest = str(found.get("salt") or ""), str(found.get("digest") or "")
        if salt and digest and (salt, digest) != (cfg["share_salt"], cfg["share_digest"]):
            cfg["share_salt"], cfg["share_digest"] = salt, digest
            changed = True
            if server is not None:
                server.salt, server.digest = salt, digest
        if changed:
            cfg.save()

    def _share_icons(self) -> dict:
        """L'icone de Prisme en PNG, faite une fois (ecran d'accueil du telephone)."""
        icons = getattr(self, "_icons_png", None)
        if icons is None:
            from .widgets import app_icon_pngs
            icons = self._icons_png = app_icon_pngs()
        return icons

    def start_share(self, rebuild: bool = True) -> bool:
        """Ouvre le partage si tout est reuni. Sans bruit s'il manque quelque chose.

        Appele a chaque analyse terminee : c'est la qu'on connait enfin la
        racine. Le catalogue ne se refait que s'il le faut (`rebuild`, ou une
        collection qui a change, ou une autre racine), et toujours hors du fil
        de l'interface : il relit toute la racine dans l'index, une a huit
        secondes de fenetre figee a la fin de chaque analyse, meme d'un
        sous-dossier. Vrai veut dire « ouvert, ou en cours d'ouverture ».
        """
        # Rien a importer tant que le partage n'est pas voulu : le serveur
        # tire http.server et le courriel, pour rien.
        if not self.cfg["share"] or self.root is None or not self.share_ready():
            return False
        top = self.top_root()
        stamp = (str(top), self._collection_key())
        server = self.share_server
        if server is not None:
            if rebuild or stamp != self._share_stamp:
                self._rebuild_catalogue(server, top, stamp)
            return True
        if self._share_opening:
            return True
        self._open_share_server(top, stamp)
        return True

    def _open_share_server(self, top, stamp) -> None:
        """Le serveur et son catalogue, faits dans un fil ; branches ici."""
        from .tunnel import Chore
        from .web import Server

        self._share_opening = True
        token = self._share_token
        cfg = self.cfg
        salt, digest = cfg["share_salt"], cfg["share_digest"]
        port = int(cfg["share_port"] or 0)
        # Le Wi-Fi de la maison : le serveur ecoute tout le reseau local, et
        # plus seulement ce PC. Le tunnel, lui, passe toujours par ici.
        host = "0.0.0.0" if cfg["share_lan"] else (cfg["share_host"] or "127.0.0.1")
        expand, width = cfg["expand_parents"], cfg["thumb_width"]
        invite = self.share_invite()

        def work():
            try:
                server = Server(top, salt, digest, port=port, host=host,
                                expand=expand, width=width, invite=invite)
                from .config import PRIVATE_DIR
                from .demandes import FILE_NAME
                server.requests_path = PRIVATE_DIR / FILE_NAME
                server.downloads = _phone_links(server, top)
                server.start()
                return server
            except OSError as exc:
                return exc

        def then(result) -> None:
            if not shiboken6.isValid(self):
                return
            self._share_opening = False
            stale = (token != self._share_token or self._closing
                     or not self.cfg["share"] or not self.share_ready())
            if isinstance(result, Exception) or result is None:
                if not stale:
                    self.show_banner(f"Partage impossible : {result}", "error")
                return
            if stale:
                # Ferme ou change entre-temps : ce serveur n'a plus d'objet.
                result.stop()
                if not self._closing and self.cfg["share"] and self.share_ready():
                    self.start_share()
                return
            self.share_server = result
            self._share_stamp = stamp
            result.icons = self._share_icons()
            self.cfg["share_port"] = result.port
            self.cfg.save_soon()
            self.publish_nas()
            if self._share_announce:
                self._share_announce = False
                self.show_banner("Partage à distance ouvert.", "done")
            if self.cfg["tunnel_auto"]:
                # L'adresse publique s'ouvre d'elle-meme : c'est elle qu'on
                # veut, pas une commande a retaper a chaque fois.
                QTimer.singleShot(400, self.start_tunnel)
            elif self.cfg["tunnel_kind"] == "tailscale":
                self._check_leftover_funnel(result.port)
            if self._share_again:
                self._share_again = False
                self.start_share(rebuild=False)

        Chore(work, self, fallback=None, then=then).start()

    def _rebuild_catalogue(self, server, top, stamp) -> None:
        """Refait le catalogue du partage dans un fil, puis l'echange d'un bloc."""
        library = getattr(server, "library", None)
        if library is None:
            return
        if self._share_building:
            # Un seul a la fois ; le suivant part a la fin de celui-ci.
            self._share_again = True
            return
        from .tunnel import Chore
        from .web import Library

        self._share_building = True
        same = Path(library.root) == Path(top)
        expand, width = self.cfg["expand_parents"], self.cfg["thumb_width"]

        def work():
            if same:
                library.refresh()           # s'echange d'un bloc, sous verrou
                return library
            # Une autre racine : le partage servait l'ancienne.
            return Library(top, expand, width)

        def then(result) -> None:
            if not shiboken6.isValid(self):
                return
            self._share_building = False
            if result is not None and self.share_server is server:
                server.library = result
                self._share_stamp = stamp
                self.publish_nas()
            if self._share_again:
                self._share_again = False
                self.start_share(rebuild=False)

        Chore(work, self, fallback=None, then=then).start()

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
        # Une ouverture encore en cours, dans son fil, est oubliee : son
        # serveur se ferme a l'arrivee.
        self._share_token += 1
        self._share_opening = False
        self._share_again = False
        self.stop_tunnel(wait)
        if self.share_server is not None:
            if self.share_server.downloads is not None:
                self.share_server.downloads.close()
            self.share_server.stop()
            self.share_server = None

    def set_share(self, on: bool) -> None:
        self.cfg["share"] = bool(on)
        self.cfg.save()
        if on:
            if self.share_server is not None:
                self.show_banner("Partage à distance ouvert.", "done")
            else:
                # Il s'ouvre dans un fil : il le dira une fois ouvert.
                self._share_announce = True
                self.start_share()
        else:
            self.stop_share()
            self.show_banner("Partage à distance fermé.", "quiet")

    def set_share_password(self, given: str) -> None:
        from .web import hash_password

        salt, digest = hash_password(given)
        self.cfg["share_salt"], self.cfg["share_digest"] = salt, digest
        self.cfg.save()
        # Choisi ici : il remplace celui du NAS, au lieu de lui ceder.
        self._nas_access_mine = True
        # Changer la serrure ferme les sessions ouvertes : c'est le but.
        self.stop_share()
        self.start_share()

    def share_state(self) -> str:
        if not self.cfg["share"]:
            return "Partage fermé."
        if not self.share_ready():
            return "Aucune serrure : le partage reste fermé."
        if self.share_server is None:
            if self._share_opening:
                return "Ouverture du partage…"
            return ("Prêt, en attente d'une racine analysée."
                    if self.root is None else "Partage arrêté.")
        return (f"Ouvert — {len(self.share_server.library.videos)} vidéo(s) "
                f"servies depuis {self.share_server.library.root}")

    def share_home_link(self) -> str:
        """Le lien d'invitation sur le Wi-Fi de la maison, ou ""."""
        if self.share_server is None or not self.cfg["share_lan"]:
            return ""
        from .web import INVITE_PATH, lan_address
        here = lan_address()
        if not here:
            return ""
        return f"http://{here}:{self.share_server.port}{INVITE_PATH}{self.share_invite()}"

    def share_away_link(self) -> str:
        """Le lien d'invitation par l'adresse publique, ou "" si elle est fermee."""
        if self.share_server is None or not self.tunnel_address:
            return ""
        from .web import INVITE_PATH
        return f"{self.tunnel_address.rstrip('/')}{INVITE_PATH}{self.share_invite()}"

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
        """La fenetre du partage, sans bloquer le reste : une video de « Ce
        qui a ete regarde » s'ouvre dans le lecteur flottant, qu'une fenetre
        modale aurait rendu impossible a toucher."""
        from .share_dialog import ShareDialog
        dialog = getattr(self, "_share_dialog", None)
        if dialog is not None and shiboken6.isValid(dialog):
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return
        dialog = ShareDialog(self, self)
        dialog.setModal(False)
        dialog.finished.connect(lambda _code: self._show_share_badge())
        self._share_dialog = dialog
        dialog.show()

    def video_for_mark(self, mark: str):
        """Le fichier que designe une empreinte du partage (`web.Library.mark`),
        ou None. Le catalogue du partage d'abord ; sinon toute la collection,
        empreintes calculees une fois."""
        server = self.share_server
        if server is not None:
            found = server.library.videos.get(mark)
            if found is not None:
                return found
        from .web import Library
        key = self._collection_key()
        memo = getattr(self, "_marks_memo", None)
        if memo is None or memo[0] != key:
            memo = (key, {Library.mark(video): video
                          for video in self._videos_from_items()})
            self._marks_memo = memo
        return memo[1].get(mark)

    def play_floating_path(self, path, start_ms: int = 0) -> None:
        """Une video quelconque dans le lecteur flottant, depuis `start_ms`."""
        path = str(path)
        for player in (self.single.player, self.aside_player.player,
                       self.grid.player, self.board.player):
            if player.playbackState() == player.PlaybackState.PlayingState:
                player.pause()
        name = Path(path).name
        if self.floating.isVisible():
            self.floating.load(path, name, start_ms)
            self.floating.raise_()
            return
        self._float_dismissed = False
        self.floating.auto = False
        self.floating.open(path, name, start_ms, muted=self.cfg["muted"],
                           geometry=self.cfg["float_geometry"])

    # L'adresse publique : chaque ecriture -- la fenetre, le dialogue du
    # partage -- met le temoin a jour.
    @property
    def tunnel_address(self) -> str:
        return self.__dict__.get("_tunnel_address", "")

    @tunnel_address.setter
    def tunnel_address(self, address: str) -> None:
        self.__dict__["_tunnel_address"] = address or ""
        self._show_share_badge()

    def _show_share_badge(self) -> None:
        """Le temoin de l'adresse publique. Il ne s'affiche plus : le compteur
        de spectateurs (l'oeil) dit ce qui compte, et le partage vit sur le NAS."""
        badge = self.__dict__.get("share_badge")
        if badge is None:
            return
        if not badge.isHidden():
            badge.hide()
        return
        address = self.tunnel_address
        if not address:
            if not badge.isHidden():
                badge.hide()
            return
        sessions = 0
        server = self.share_server
        guard = getattr(server, "guard", None)
        if guard is not None:
            now = time.time()
            try:
                sessions = sum(1 for until, _label in list(guard.sessions.values())
                               if until > now)
            except (RuntimeError, ValueError, TypeError):
                sessions = 0
        tip = (f"Adresse publique ouverte : {address}\n"
               + (f"{sessions} session(s) ouverte(s)\n" if sessions else
                  "Aucune session ouverte\n")
               + "Cliquer : Partage à distance…   ·   Clic droit : fermer l'adresse")
        if badge.toolTip() != tip:
            badge.setToolTip(tip)
        if badge.isHidden():
            badge.show()

    # -- en direct : qui regarde, et quoi ---------------------------------------
    def _poll_viewers(self) -> None:
        """Toutes les cinq secondes, hors du fil de l'interface : les visiteurs
        du partage du PC, et ceux du NAS (le fichier qu'il tient a jour)."""
        if self._viewers_busy or self._closing:
            return
        from .nas_publish import FOLDER, share_root
        from .tunnel import Chore
        server = self.share_server
        root = share_root(self.top_root() or "") if self.root is not None else None

        def work():
            found = []
            if server is not None and hasattr(server, "viewers"):
                found += [dict(entry, where="PC") for entry in server.viewers()]
            if root is not None:
                live = root / FOLDER / "etat" / "en-direct.json"
                try:
                    told = json.loads(live.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    told = {}
                # Un fichier qui ne change plus depuis une minute : le serveur
                # du NAS s'est tu. Jugé à l'horloge du PC, quand on voit son
                # « at » changer : celle du NAS peut avoir des minutes d'écart,
                # et tous ses visiteurs passaient pour partis.
                now = time.time()
                stamp = float(told.get("at", 0) or 0)
                last = self._live_seen
                if last is None or last[0] != stamp:
                    fresh = last is not None or abs(now - stamp) < 60
                    self._live_seen = (stamp, now if fresh else now - 60)
                seen_at = self._live_seen[1]
                if stamp and now - seen_at < 60:
                    late = now - seen_at
                    for entry in told.get("viewers") or []:
                        if entry.get("playing"):
                            entry["at"] = float(entry.get("at", 0)) + late
                        found.append(dict(entry, where="NAS"))
            if time.time() - self._people_read > 30:
                # Les profils : qui est qui, quelle que soit l'etiquette.
                try:
                    self._people = self._read_people(root)
                except Exception:                       # noqa: BLE001
                    pass
                self._people_read = time.time()
            return found

        self._viewers_busy = True
        Chore(work, self, fallback=[], then=self._viewers_ready).start()

    def _viewers_ready(self, found: list) -> None:
        self._viewers_busy = False
        self.viewers = found or []
        self._follow_step()
        count = len(self.viewers)
        button = self.viewers_button
        text = str(count)
        if button.text() != text:
            button.setText(text)
            button.setIcon(icon("eye", "#7bd88f" if count else DIM))
        if count:
            lines = []
            for entry in self.viewers:
                what = (f"regarde « {entry.get('name')} »" if entry.get("name")
                        else entry.get("view") or "parcourt la bibliothèque")
                lines.append(f"• {self.alias(entry.get('label', ''))} "
                             f"({entry.get('where')}) : {what}")
            tip = (f"{count} personne(s) connectée(s)\n" + "\n".join(lines)
                   + "\n\nCliquer : voir, ou regarder avec elle.")
        else:
            tip = "Personne ne regarde la bibliothèque partagée en ce moment."
        if button.toolTip() != tip:
            button.setToolTip(tip)

    def _viewers_menu(self) -> None:
        menu = QMenu(self)
        if not self.viewers:
            menu.addAction("Personne n'est connecté en ce moment").setEnabled(False)
        for entry in self.viewers:
            head = menu.addAction(
                f"{self.alias(entry.get('label', ''))}  —  par le {entry.get('where')}")
            head.setEnabled(False)
            if entry.get("view"):
                # Ce qu'il a accepte de partager : ou il en est dans la page.
                menu.addAction(f"    {entry['view']}").setEnabled(False)
            if entry.get("name"):
                at = human_duration(entry.get("at", 0) or 0)
                state = "" if entry.get("playing") else " (en pause)"
                menu.addAction(icon("picture-in-picture-2"),
                               f"    Regarder avec : « {entry['name']} » à {at}{state}",
                               lambda e=dict(entry): self.watch_along(e))
            elif not entry.get("view"):
                menu.addAction("    parcourt la bibliothèque").setEnabled(False)
            following = self._following_key() == (entry.get("label"), entry.get("where"))
            follow = menu.addAction(
                "    Arrêter de le suivre" if following else
                "    Suivre en direct (le lecteur flottant suit sa vidéo)",
                (lambda: self.stop_following()) if following else
                (lambda e=dict(entry): self.follow_viewer(e)))
            follow.setEnabled(True)
            menu.addAction("    Nommer cette personne…",
                           lambda label=entry.get("label", ""): self.name_device(label))
        menu.addSeparator()
        menu.addAction("Ce qui a été regardé, les connexions…", self.open_share)
        menu.exec(self.viewers_button.mapToGlobal(
            self.viewers_button.rect().bottomLeft()))
        menu.deleteLater()

    # Une personne, plusieurs etiquettes : celles que son profil a retenues
    # (Chrome, puis l'icone, puis Chrome mis a jour…), relues en fond.
    _people: dict = {}          # etiquette -> (nom du profil, toutes ses etiquettes)
    _people_read = 0.0

    def _read_people(self, root) -> dict:
        """Dans un fil : les profils du PC et du NAS, par etiquette."""
        from . import profils
        files = []
        server = self.share_server
        if server is not None and getattr(server, "profiles_path", None):
            files.append(Path(server.profiles_path))
        if root is not None:
            from .nas_publish import FOLDER
            files.append(root / FOLDER / "etat" / "profils.json")
        people: dict = {}
        for path in files:
            for me, profile in profils._read(path).items():
                if not isinstance(profile, dict):
                    continue
                labels = set(profile.get("labels") or [])
                labels |= {label for label in labels if f"({str(me)[:6]})" in label}
                if not labels:
                    continue
                name = str(profile.get("name") or "")
                for label in labels:
                    known = people.get(label)
                    merged = (known[1] | labels) if known else labels
                    people[label] = (name or (known[0] if known else ""), frozenset(merged))
        return people

    def _siblings(self, label: str) -> set:
        """Toutes les etiquettes de la meme personne : son profil, et celles
        qu'on a nommees pareil."""
        found = set(self._people.get(label, ("", frozenset()))[1]) | {label}
        aliases = self.cfg["share_aliases"] or {}
        names = {aliases[l] for l in found if aliases.get(l)}
        if names:
            found |= {l for l, n in aliases.items() if n in names}
        return found

    def alias(self, label: str) -> str:
        """Le nom de la personne : celui qu'on lui a donne (sur l'une de ses
        etiquettes), sinon celui de son profil, sinon l'etiquette."""
        aliases = self.cfg["share_aliases"] or {}
        if aliases.get(label):
            return aliases[label]
        person = self._people.get(label)
        if person:
            for other in person[1]:
                if aliases.get(other):
                    return aliases[other]
            if person[0]:
                return person[0]
        return label

    def name_device(self, label: str, parent=None) -> bool:
        """Un nom pour la personne derriere cette etiquette : il vaut pour
        toutes ses etiquettes (Chrome, icone, 4G, autre wifi). Reprendre un
        nom deja donne reunit deux etiquettes en une seule personne."""
        aliases = dict(self.cfg["share_aliases"] or {})
        names = sorted({n for n in aliases.values() if n}, key=str.lower)
        current = self.alias(label)
        if current != label and current not in names:
            names.insert(0, current)
        dialog = QInputDialog(parent or self)
        dialog.setWindowTitle("Nommer cette personne")
        dialog.setLabelText(
            f"Un nom pour « {label} ».\nChoisir un nom déjà donné : c'est la même "
            "personne (ses connexions sont réunies). Vide : l'étiquette d'origine.")
        dialog.setComboBoxEditable(True)
        dialog.setComboBoxItems(names or [""])
        dialog.setTextValue(current if current != label else "")
        if not dialog.exec():
            return False
        name = dialog.textValue().strip()
        family = self._siblings(label)
        for one in family:
            if name:
                aliases[one] = name
            else:
                aliases.pop(one, None)
        self.cfg["share_aliases"] = aliases
        self.cfg.save()
        self._viewers_ready(self.viewers)
        return True

    # -- suivre un visiteur en direct ------------------------------------------
    def _following_key(self):
        follow = getattr(self, "_following", None)
        return (follow["label"], follow["where"]) if follow else None

    def follow_viewer(self, entry: dict) -> None:
        """Le lecteur flottant suit ce visiteur : sa video, a son instant,
        en pause quand il l'est -- relu a chaque nouvelle des visiteurs."""
        self._following = {"label": entry.get("label", ""), "where": entry.get("where", ""),
                           "video": "", "missed": 0}
        self.show_banner(f"Suivi en direct de {self.alias(entry.get('label', ''))} : le "
                         "lecteur flottant suit sa vidéo. L'œil, en haut, pour arrêter.",
                         "info", seconds=8)
        self._follow_step()

    def stop_following(self, said: str = "") -> None:
        if getattr(self, "_following", None) is None:
            return
        self._following = None
        self.show_banner(said or "Suivi en direct arrêté.", "quiet")

    def _follow_step(self) -> None:
        follow = getattr(self, "_following", None)
        if not follow:
            return
        entry = next((e for e in self.viewers if e.get("label") == follow["label"]
                      and e.get("where") == follow["where"]), None)
        if entry is None:
            # Un battement manque parfois : on laisse une demi-minute.
            follow["missed"] += 1
            if follow["missed"] >= 6:
                self.stop_following(f"{self.alias(follow['label'])} n'est plus connecté : "
                                    "suivi arrêté.")
            return
        follow["missed"] = 0
        mark = entry.get("video") or ""
        if not mark:
            return
        at_ms = int(float(entry.get("at", 0) or 0) * 1000)
        if mark != follow["video"]:
            path = self.video_for_mark(mark)
            if path is None:
                return
            follow["video"] = mark
            follow["path"] = str(path)
            self.play_floating_path(path, at_ms)
            return
        floating = self.floating
        if not floating.isVisible():
            # On a ferme le lecteur : on ne suit plus.
            return self.stop_following()
        if str(getattr(floating, "path", "")) != follow.get("path"):
            return
        player = floating.player.player
        if abs(floating.position() - at_ms) > 8000:
            player.setPosition(at_ms)
        if entry.get("playing") and not floating.playing():
            player.play()
        elif not entry.get("playing") and floating.playing():
            player.pause()

    def watch_along(self, entry: dict) -> None:
        """La video que regarde un visiteur, dans le lecteur flottant, au meme
        instant que lui."""
        path = self.video_for_mark(entry.get("video", ""))
        if path is None:
            self.show_banner("Cette vidéo n'est pas dans la collection affichée.",
                             "error")
            return
        self.play_floating_path(path, int(float(entry.get("at", 0) or 0) * 1000))

    def _share_badge_menu(self, where) -> None:
        menu = QMenu(self)
        menu.addAction("Partage à distance…", self.open_share)
        menu.addAction("Fermer l'adresse publique", self.stop_tunnel)
        menu.exec(self.share_badge.mapToGlobal(where))
        menu.deleteLater()

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

    def open_float_radial(self) -> None:
        """Clic droit sur le lecteur flottant : les destinations, pour ranger
        la video qu'il montre -- la video courante de la fiche."""
        item = self.current
        if (item is None or item.locked or not self.floating.path
                or os.path.normcase(str(item.path))
                != os.path.normcase(self.floating.path)):
            return
        if not self.float_radial.isHidden():
            return self.float_radial.close_menu()
        entries = [(d.get("key", "?"), d.get("label") or Path(d["path"]).name)
                   for d in list(self.cfg.destinations)[:RadialMenu.MAX]]
        if not entries:
            self.show_banner("Aucune destination : ⋯ → Destinations…", "quiet")
            return
        self.float_radial.open_at(QCursor.pos(), entries)

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
        label = self._delete_label()
        signature = (repr(self.cfg.destinations), label)
        if force or signature != self._commands_signature:
            self._commands_signature = signature
            self.commands.rebuild(self.cfg.destinations, label)

    def show_item(self, index: int, spare: str = "") -> None:
        """La fiche de cet element. `spare` : la video a charger en reserve,
        quand la suivante n'est pas la voisine dans la liste (`step`)."""
        if not self.items:
            return
        mark(f"show_item {index}")
        self.index = max(0, min(index, len(self.items) - 1))
        # Le champ de renommage appartenait a la fiche qu'on quitte.
        self._cancel_rename()
        if self._quiet:
            # Rien ne se charge derriere la page neutre : la fiche suivante
            # (rafale, fin d'un rangement) partait avec le son. Elle s'ouvrira
            # au retour (`leave_quiet`).
            self._quiet_show = True
            return
        item = self.items[self.index]
        # Les comptes se refont plus bas (`_update_page_bar`), une seule fois.
        self._rebuild_commands()

        self._describe(item)

        self.stars.show()
        self.stars.set_value(self.ratings.get(item.path))
        self.pin_button.set_value(self._is_pinned(item.path))
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
        if item.kind != MODE_FOLDERS and not item.is_tag:
            # La fiche d'une video : son dossier, puis son nom -- pas toute la
            # chaine depuis la racine. Sur une seule ligne, la chaine entiere
            # s'ecrasait et l'on ne savait plus d'ou sortait la video.
            top = folder
        self.crumbs.set_path(top, folder, parent_first=(
            item.kind != MODE_FOLDERS and not item.is_tag))
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
        elif self.floating.active:
            # La video se lit dans le lecteur flottant : la fiche ne charge
            # rien, sans quoi on l'entendrait deux fois.
            self.floating.load(str(item.path), item.name)
        else:
            self.single.set_item(str(item.path), "…")
            # Deja prete (la reserve) : l'instant demande se pose tout de
            # suite ; sinon, au chargement.
            self._seek_when_ready()
        self.single.peek_end()
        self.radial.close_menu()
        self.seen_timer.start()
        if self.cfg["burst"] and item.kind != MODE_FOLDERS:
            self.burst_timer.start()
        else:
            self.burst_timer.stop()
        if self.slideshow_timer.isActive():
            # Une photo choisie a la main a droit a sa pleine duree.
            self.slideshow_timer.start()

        self._sort_videos(item)
        self._request_previews(item, current=True)
        self._update_page_bar()
        keep = {self._plan_key(item, self.page_of(item))}
        for offset in (1, 2):
            if self.index + offset < len(self.items):
                nxt = self.items[self.index + offset]
                keep.add(self._plan_key(nxt, self.page_of(nxt)))
                self._request_previews(nxt, current=False)
                if (offset == 1 and not spare and item.kind != MODE_FOLDERS
                        and nxt.kind != MODE_FOLDERS and not nxt.locked
                        and not self.floating.active):
                    # La suivante se charge des maintenant dans le lecteur de
                    # reserve : la fleche ne montrera plus de noir.
                    self.single.preload(str(nxt.path))
        if (spare and item.kind != MODE_FOLDERS and spare != str(item.path)
                and not self.floating.active):
            self.single.preload(spare)
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
                # Duree et hauteur inconnues : on sonde. Une photo n'a jamais
                # de duree ; sa hauteur connue suffit.
                if not plan[0][2] and not plan[0][3]:
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
            if not entry[2] and not entry[3]:
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
        if key.startswith("flip@"):
            position = self._board_position_of(key)
            if position >= 0:
                self.board.add_flip(position, path)
            return
        current = self.current
        if current is None or key != self._current_key():
            return
        viewer = self.grid if current.kind == MODE_FOLDERS else self.single
        viewer.set_thumb(slot, path)

    def on_thumb_failed(self, key: str, slot: int) -> None:
        if key.startswith(("board@", "peek@", "flip@")):
            return
        current = self.current
        if current is None or key != self._current_key():
            return
        viewer = self.grid if current.kind == MODE_FOLDERS else self.single
        viewer.set_failed(slot)

    def on_board_page(self, first: int, last: int, total: int) -> None:
        if self._patching:
            # Une carte ajoutee par l'analyse : `on_patch` recompte une fois,
            # a la fin du paquet, et non quarante.
            return
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
        # Pour cet onglet seulement : les autres gardent le leur.
        self._tab_sorts[self.tab] = mode
        self.cfg["sort_mode"] = mode
        # Un instant plus tard, et d'un bloc : l'ecriture attend le disque.
        self.cfg.save_soon()
        self.plans = {}
        self.pages = {}
        self.apply_sort()
        if self.browsing:
            self.refresh_board()
        elif self.items:
            self.show_item(min(self.index, len(self.items) - 1))

    def apply_sort(self) -> None:
        """Reclasse la liste visible selon le mode choisi ; les dossiers
        epingles restent en tete."""
        self.items = [item for item in self.items if not item.pinned]
        self._sort_items()
        if self._pin_here():
            pinned = self._pinned_items()
            if pinned:
                self.items[:0] = pinned
        pins = self._pin_keys()
        if pins:
            # Ce que l'on a epingle passe devant tout le reste, dans l'ordre du
            # tri choisi (le tri est stable).
            self.items.sort(key=lambda item: item.is_tag or self._pin_key(item.path) not in pins)

    # -- « À trier » et « Orphelins », epingles en tete de la collection ------
    def _pin_here(self) -> bool:
        """La liste affichee est-elle la collection, sous « Dossiers », sans
        recherche ? C'est la seulement que les dossiers epingles ont leur place."""
        return (self.tab == TAB_FOLDERS and not self._transient
                and self.mode == MODE_FOLDERS and self._collection_known()
                and self.all_items is self._plain_items
                and not (self.criteria or {}).get("include", self.cfg["filter_include"]))

    def _pinned_items(self) -> list:
        """Les cartes epinglees, si elles sont pretes ; sinon on les calcule a
        cote, et elles se posent en tete une fois faites."""
        key = self._collection_key()
        memo = self._pinned_memo
        if memo is not None and memo[0] == key:
            return memo[1]
        if self._pinned_job == key:
            return memo[1] if memo is not None else []
        self._pinned_job = key
        from .tunnel import Chore
        videos = list(self._videos_from_items())
        loose = [video for item in self._collection()
                 if item.loose_only and not item.processed
                 for video in item.videos if not under_veiled(str(video))]

        def work():
            unread = [video for video in videos if unreadable_name(str(video))]
            # Les orphelins : hors de tout sous-dossier, **et** sans un mot
            # dans le nom. Un titre lisible pose en vrac se range d'apres son
            # titre ; ce sont les autres qu'il faut regarder un par un.
            orphans = [video for video in loose if unreadable_name(str(video))]
            return unread, orphans

        Chore(work, self, fallback=([], []),
              then=lambda found: self._pinned_ready(key, *found)).start()
        return memo[1] if memo is not None else []

    def _pinned_ready(self, key, unread: list, loose: list) -> None:
        if self._pinned_job == key:
            self._pinned_job = None
        cards = []
        for name, found in ((PINNED_UNREAD, unread), (PINNED_LOOSE, loose)):
            if not found:
                continue
            item = Item(path=Path(name), kind=MODE_FOLDERS, videos=list(found),
                        video_count=len(found), file_count=len(found))
            item.is_tag = True
            item.pinned = True
            item.board_tip = PINNED_TIPS[name]
            cards.append(item)
        self._pinned_memo = (key, cards)
        if self._closing or not self._pin_here():
            return
        if key != self._collection_key():
            # La collection a bouge pendant le calcul : on recommence.
            self._pinned_items()
            return
        self._pin_refresh()

    def _pin_refresh(self) -> None:
        """Pose les cartes epinglees en tete si elles n'y sont pas, ou plus a jour."""
        if not self._pin_here():
            return
        cards = self._pinned_items()
        shown = [item for item in self.items if item.pinned]
        if ([i.item_id for i in shown] == [c.item_id for c in cards]
                and all(a.video_count == b.video_count for a, b in zip(shown, cards))):
            return
        self.apply_sort()
        if self.browsing:
            self.refresh_board()
        self._show_counts()

    def _sort_items(self) -> None:
        """Reclasse la liste visible selon le mode choisi."""
        import random
        if self._transient == "similar":
            return
        if self._transient == "dupes":
            # Les doublons se lisent groupe par groupe, le meilleur exemplaire
            # d'abord : un tri ou un filtre les dispersait dans la liste.
            self.items.sort(key=lambda item: getattr(item, "dupe_group", 0))
            return
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

        seen = self._seen_before()
        if self.tab == TAB_VIDEOS and self.sort_mode in ("", "random"):
            # « Vidéos » est le vrac, au hasard, rebattu a chaque visite : sans
            # quoi les memes videos reviennent toujours en tete. Les jamais
            # vues passent devant (le tri est stable : elles restent melangees).
            random.shuffle(self.items)
            self.items.sort(key=lambda item: item.item_id in seen)
        elif self.sort_mode == "random":
            # Ailleurs, le hasard tient la seance : le meme ordre a chaque
            # retour, et la planche garde ses vignettes. Il se melangeait a
            # chaque retour, a chaque filtre -- quarante vignettes a refaire.
            # Un nombre par nom, tire de la graine de la seance : aussi vite
            # fait qu'un melange (une trentaine de millisecondes sur
            # cinquante-sept mille dossiers).
            # Les jamais vus d'abord : le lendemain, on ne retombe pas sur ce
            # qu'on a deja regarde.
            seed, crc = self._shuffle_seed, zlib.crc32
            self.items.sort(key=lambda item: (item.item_id in seen, crc(
                item.sort_name.encode("utf-8", "surrogatepass"), seed)))
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
        elif self.sort_mode in ("date_desc", "date_asc"):
            # La date de modification sur le disque, celle de l'explorateur :
            # ni la date d'analyse, ni celle de l'index. Sous « Vidéos », les
            # elements a plat n'en portent pas : on la prend a l'analyse, et
            # celles qu'on ignore encore se lisent en tache de fond.
            self._read_dates(self._fill_dates(self.items))
            self.items.sort(key=lambda i: i.mtime or 0.0,
                            reverse=self.sort_mode == "date_desc")
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
        elif self.sort_mode in ("date_desc", "date_asc"):
            # Les dates relevees par l'analyse : aucune lecture disque ici.
            from .stamps import known as known_stamp

            missing = []

            def date_of(path):
                pair = known_stamp(path)
                if not pair:
                    missing.append(path)
                return pair[1] if pair else 0.0
            keys = {str(path): date_of(path) for path in item.videos}
            self._read_dates(missing)
            item.videos.sort(key=lambda path: (keys[str(path)], str(path).lower()),
                             reverse=self.sort_mode == "date_desc")
        else:
            item.videos.sort(key=lambda path: str(path).lower())

    def _fill_dates(self, items) -> list:
        """Date et taille des videos a plat, depuis ce que l'analyse a releve ;
        rend les chemins dont on ne sait encore rien."""
        from .stamps import known as known_stamp
        missing = []
        for item in items:
            if item.mtime or item.kind != MODE_FILES or item.is_tag:
                continue
            pair = known_stamp(item.path)
            if pair:
                item.size = item.size or pair[0]
                item.mtime = pair[1]
            else:
                missing.append(item.path)
        return missing

    def _read_dates(self, paths) -> None:
        """Lit les dates qui manquent, un dossier a la fois : une seule
        enumeration rapporte celles de toutes ses videos, la ou un `stat()`
        par fichier coutait un aller-retour reseau chacun. Puis on reclasse."""
        tried = self.__dict__.setdefault("_dates_tried", set())
        wanted = self.__dict__.setdefault("_dates_wanted", set())
        for path in paths:
            text = str(path)
            if text not in tried:
                wanted.add(text)
        if not wanted or getattr(self, "_dates_busy", False):
            return
        batch = list(wanted)
        wanted.clear()
        tried.update(batch)
        self._dates_busy = True
        folders = sorted({os.path.dirname(text) for text in batch})
        if len(folders) > 20:
            self.show_banner(f"Lecture des dates de modification ({len(batch)} vidéos, "
                             f"{len(folders)} dossiers)…", "quiet", seconds=6)

        def work():
            from .stamps import remember
            found = 0
            for folder in folders:
                if self._closing:
                    break
                try:
                    with os.scandir(folder) as listing:
                        for entry in listing:
                            dot = entry.name.rfind(".")
                            if dot <= 0 or entry.name[dot:].lower() not in VIDEO_EXTS:
                                continue
                            try:
                                st = entry.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            remember(entry.path, st.st_size, st.st_mtime)
                            found += 1
                except OSError:
                    continue
            return found

        def then(found) -> None:
            self._dates_busy = False
            if not shiboken6.isValid(self) or self._closing:
                return
            if found and self.sort_mode in ("date_desc", "date_asc"):
                self.plans = {}
                self.pages = {}
                self.apply_sort()
                if self.browsing:
                    self.refresh_board()
            if self._dates_wanted:
                self._read_dates([])

        from .tunnel import Chore
        Chore(work, self, fallback=0, then=then).start()

    def _refresh_random_here(self) -> None:
        """Le hasard « dans ce qu'on regarde » : seulement si l'on regarde une
        partie — des resultats filtres, ou un dossier ou l'on est entre."""
        narrowed = bool(self.levels) or bool(self._active_filters())
        self.controls.random_here.setVisible(
            self.browsing and self.tab != TAB_SPLIT and bool(self.items) and narrowed)

    def _show_counts(self) -> None:
        """Une seule ligne dit ce qui est montre, ce qui est masque, et ou l'on en est.

        Seul auteur du compteur et de son infobulle : ils en avaient trois,
        qui se contredisaient.
        """
        active = self._active_filters()
        self.controls.set_filters(active)
        self._refresh_random_here()
        hidden = self._sortable_count() - len(self.items)
        tip = []
        if active:
            tip.append("Filtres actifs : " + " · ".join(active))
        if hidden > 0:
            tip.append(f"{self._thousands(hidden)} élément(s) masqué(s) "
                       "par les filtres")
        if self.browsing:
            first, last = self.board._page_bounds()
            shown = (f"{first + 1 if self.items else 0}–{last} sur "
                     f"{self._thousands(len(self.items))}")
            self.controls.count.setToolTip("\n".join(tip))
            self.controls.set_page(
                shown,
                self.board.page > 0,
                self.board.page < self.board.total_pages() - 1,
            )
            if self.board.picked_ids:
                self._show_picked_pages()
            return
        total = len(self.items)
        stats = self.stats
        done = stats["moved"] + stats["deleted"]
        tip.append(f"Ici : {stats['moved']} déplacé(s) · {stats['deleted']} "
                   f"supprimé(s) · {stats['skipped']} passé(s)")
        if self.scanning:
            tip.append("Vérification du disque en cours…")
        self.controls.count.setToolTip("\n".join(tip))
        self.controls.set_page(
            f"{min(self.index + 1, total)} / {total}"
            + (f"  ·  {hidden} filtrés" if hidden > 0 else "")
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

    # Combien de temps un echec reste a l'abri d'un message ordinaire.
    BANNER_GUARD_S = 4.0

    def show_banner(self, text: str, tone: str = "info", action=None,
                    seconds: int = 4, keep: bool = False) -> None:
        """Un bandeau, cinq tons : info, quiet, done, error, et trash (ce
        qu'on vient d'ecarter, qu'un clic reprend). Rien d'autre.

        `action` : ce que fait un clic sur le bandeau (« afficher les
        doublons ») ; il reste alors plus longtemps, le temps d'y aller.
        `keep` : comme un echec, il ne se laisse pas effacer aussitot. Un
        echec garde (`keep` et « error ») est une alerte : aucun autre bandeau,
        pas meme un echec, ne la remplace avant qu'on l'ait lue.
        """
        if self._quiet or self.isMinimized():
            # Retenu, et montre au retour : un « Échec sur … » dit pendant le
            # repli ou fenetre reduite etait efface sans avoir ete lu, et l'on
            # croyait rangee une video qui n'avait pas bouge.
            self._held_banners.append((text, tone, action, seconds))
            del self._held_banners[:-12]
            return
        now = time.monotonic()
        alarm = keep and tone == "error"
        if (not alarm and now < self._alarm_until and self.banner.isVisible()):
            # Une alerte a l'ecran : ce qui suit -- la suppression suivante,
            # par exemple -- attend qu'elle ait ete lue au lieu de l'effacer.
            self._banner_next = (text, tone, action, seconds)
            QTimer.singleShot(int((self._alarm_until - now) * 1000) + 20,
                              self._flush_banner_next)
            return
        if (action is None and tone != "error" and not keep
                and now < self._banner_guard and self.banner.isVisible()):
            # Un echec encore frais : le message ordinaire attend son tour au
            # lieu de l'effacer avant qu'on l'ait lu.
            self._banner_next = (text, tone, action, seconds)
            QTimer.singleShot(int((self._banner_guard - now) * 1000) + 20,
                              self._flush_banner_next)
            return
        self._banner_next = None
        self._banner_guard = (now + self.BANNER_GUARD_S
                              if tone == "error" or keep else 0.0)
        self._alarm_until = now + max(1, int(seconds)) if alarm else 0.0
        self._banner_action = action
        self.banner.setCursor(Qt.PointingHandCursor if action is not None
                              else Qt.ArrowCursor)
        self.banner.setText(text)
        if tone != self._banner_tone:
            # Restyler coute : un comptage qui parlait trois fois par seconde
            # refaisait la feuille de style a chaque fois, pour le meme ton.
            self._banner_tone = tone
            color = BANNER_TONES.get(tone, BANNER_TONES["info"])
            self.banner.setStyleSheet(
                f"QLabel {{ background: {color}; color: #e9eef4; border-radius: 8px;"
                " padding: 8px 14px; font-weight: 600; }")
        self._place_banner()
        self.banner.show()
        self.banner.raise_()
        self.banner_timer.start(max(1, int(seconds)) * 1000)

    def _flush_banner_next(self) -> None:
        """Le message qui attendait la fin d'un echec, maintenant."""
        following, self._banner_next = self._banner_next, None
        if following is not None and not self._closing:
            self._banner_guard = 0.0
            self.show_banner(*following)

    def _show_held_banners(self) -> None:
        """Ce qui s'est dit pendant le repli ou fenetre reduite.

        Les echecs d'abord, ensemble, et plus longtemps : c'est ce qu'il ne
        faut pas manquer. A defaut, le dernier message seulement.
        """
        held, self._held_banners = self._held_banners, []
        if not held:
            return
        errors = []
        for text, tone, _action, _seconds in held:
            if tone == "error" and text not in errors:
                errors.append(text)
        if errors:
            action = next((a for _t, tone, a, _s in reversed(held)
                           if tone == "error"), None)
            self.show_banner("\n".join(errors[-3:]), "error", action, seconds=10)
            return
        text, tone, action, seconds = held[-1]
        self.show_banner(text, tone, action, seconds)

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
            self.floating.release()
            # Un panneau arrete garde son fichier ouvert : le deplacer
            # echouait alors apres vingt secondes d'essais. On les vide — sauf
            # si le mur est a l'ecran, qu'on ne veut pas voir s'eteindre.
            on_screen = (self.tab == TAB_SPLIT
                         and self.viewer.currentWidget() is self.wall
                         and self.stack.currentIndex() == PAGE_SORT)
            for pane in self.wall.panes:
                if pane.video_path and not on_screen:
                    pane.clear()
                else:
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
        for single in (self.single, self.aside_player, self.floating.player):
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
        if value:
            # Revenu a une planche : Echap n'a plus de mur ou ramener.
            self._back_to_wall = None

    @property
    def view(self) -> str:
        """Conservé pour l'enregistrement : l'onglet dit déjà tout."""
        return VIEW_BROWSE if self.browsing else VIEW_EDIT

    def _videos_from_items(self) -> list:
        """Toutes les vidéos déjà repérées, sans rien redemander au disque.

        Passer aux vidéos relançait une analyse complète de la racine, alors que
        l'analyse des dossiers vient d'en relever le contenu. Sur un partage
        réseau, c'était plusieurs minutes pour une information déjà connue.

        Gardée tant que la collection ne bouge pas : l'onglet Vidéos, le mur
        et les voisines d'une vidéo la redemandaient chacun, cent mille
        chemins revus à chaque fois. La liste rendue ne se modifie pas.
        """
        key = self._collection_key()
        memo = self._videos_memo
        if memo is not None and memo[0] == key:
            return memo[1]
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
                key_ = str(video)
                if key_ in seen or under_veiled(key_):
                    continue
                seen.add(key_)
                found.append(video)
        self._videos_memo = (key, found)
        return found

    def _collection(self) -> list:
        """Les dossiers de la racine, d'ou que l'on regarde.

        Tant que la racine n'a pas ete lue, on se contente des vrais dossiers
        de la liste affichee.
        """
        if self._collection_known():
            return self._plain_items
        return [i for i in self.all_items if not i.is_tag]

    def _collection_known(self) -> bool:
        """Vrai quand la collection de la racine du fil est en memoire."""
        top = self.top_root()
        return (self._plain_root is not None and top is not None
                and Path(self._plain_root) == Path(top))

    def _touch(self, sortable: bool = True, collection: bool = True) -> None:
        """Une liste a change sur place : ce qu'on en a tire est a refaire.

        `sortable` a faux : la liste n'a fait que s'allonger (une analyse qui
        avance), ou seuls des etats ont change (un transfert) -- la liste
        triable se complete alors par la fin, sans tout reparcourir.
        `collection` a faux : ce n'est pas la collection qui a bouge (la
        relecture d'un sous-dossier), et l'onglet Videos, le mur, les
        mots-cles gardent ce qu'ils en avaient tire.
        """
        if collection:
            self._collection_gen += 1
        if sortable:
            self._rebuild_gen += 1

    def _shows_collection(self) -> bool:
        """La liste affichee est-elle la collection elle-meme ?"""
        return (self.all_items is self._plain_items
                or not self._collection_known())

    def _collection_key(self) -> tuple:
        """Ce qui distingue une version de la collection d'une autre.

        La liste meme (son identite et sa longueur), le compteur des
        changements faits sur place, et le voile : tout ce dont dependent
        les listes qu'on en tire.
        """
        from . import scan as _scan
        source = self._plain_items if self._collection_known() else self.all_items
        if source is not self._collection_src:
            # Une autre liste : un autre numero. On garde la liste vue en
            # dernier, pour qu'aucune autre ne naisse a son adresse et ne
            # passe pour elle.
            self._collection_src = source
            self._collection_serial += 1
        return (str(self.top_root()), self._collection_serial, len(source),
                self._collection_gen, _scan.SHOW_VEILED, frozenset(_scan.VEILED))

    def restore_folders(self) -> bool:
        """Remet la liste des dossiers déjà analysée, sans rien relire.

        Sans cela, chaque aller-retour entre les onglets relançait l'analyse
        complète de la racine — plusieurs minutes sur un partage réseau, pour
        retrouver exactement ce qu'on venait de quitter.
        """
        if (self._plain_root is None or self.root is None
                or Path(self._plain_root) != Path(self.root)):
            return False
        # La liste elle-meme, pas une copie : la relecture en cours continue
        # de l'alimenter, et ce qu'on y range en disparait partout.
        self.all_items = self._plain_items
        self.items = self._filtered()
        self.mode = MODE_FOLDERS
        self.apply_sort()
        self.index = 0
        # Les comptes suivent la planche, que l'appelant rebatit
        # (`on_board_page`) : les faire ici les calculait sur l'ancienne.
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
        # La destination se cree dans le transfert (`actions.move_to`) : la
        # creer ici coutait un aller-retour de plus, sur le fil de l'interface.
        # Le panneau qui la montre la lache, et les ffmpeg qui la lisent sont
        # arretes : sans cela, le deplacement butait sur le fichier ouvert.
        self._release_media(source)
        self._leave_wall_pool(source)
        self.transfers.submit(Transfer(
            kind="move", src=source, dest=dest_dir, label=label, item_id=""))
        self.show_banner(f"« {source.name} » → {label}", "done")
        return True

    def delete_one(self, path) -> bool:
        """Ecarte **ce fichier** dans la corbeille de session, depuis le mur.

        Le pendant de `move_one` : Suppr sur le mur visait l'element courant
        de la liste restee dessous, jamais la video qu'on regardait.
        """
        source = Path(path)
        state = actions.probe(source)
        if state != "ok":
            self.show_banner(
                f"Introuvable : {source.name}" if state == "absent" else
                f"NAS injoignable : « {source.name} » n'a pas été touché.",
                "error")
            return False
        # Sa fiche « video », si l'onglet Videos l'a deja faite : elle dira
        # qu'elle est partie, et la corbeille saura sa taille.
        item = self._flat_cache.get(str(source))
        if item is not None and item.locked:
            return False
        self._release_media(source)
        self._leave_wall_pool(source)
        if item is not None:
            item.status = "pending_delete"
            item.status_detail = "Corbeille"
        self._submit(Transfer(kind="move", purpose="delete", src=source,
                              dest=self.trash.folder_for(source),
                              label="Corbeille",
                              item_id=item.item_id if item is not None else ""),
                     item)
        self._show_deleted(self._deleted_text(item) if item is not None else
                           f"« {source.name} » supprimé · cliquer ici pour "
                           "annuler (Ctrl+Z)")
        return True

    def _leave_wall_pool(self, path) -> None:
        """Ce qui part ne revient plus sur le mur au prochain tirage."""
        pool = self.wall.pool
        text = str(path)
        if text in pool:
            pool.remove(text)

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
        batch = _random.sample(self._wall_unsure,
                               min(60, len(self._wall_unsure)))
        # Le lot est retenu : a son retour, on n'examine que lui.
        self._wall_batch = batch
        self.wall_prober = InfoScan(batch, self)
        # Le mur est ce qu'on regarde : il n'attend pas que les vignettes de
        # la planche, qu'on vient de quitter, aient fini.
        self.wall_prober.yields = False
        self._own_thread(self.wall_prober, "wall_prober")
        self.wall_prober.done.connect(self._wall_probed)
        self.wall_prober.start()

    def _wall_probed(self, count: int) -> None:
        """Un lot sonde : ce qu'on y a appris rejoint le vivier, et rien d'autre.

        Le vivier entier se refaisait a chaque lot -- toute la collection
        relue, deux a quatre secondes de fenetre figee toutes les soixante
        videos sondees.
        """
        self.wall_prober = None
        batch, self._wall_batch = self._wall_batch, []
        if self.tab != TAB_SPLIT or self._wall_pinned:
            return
        wanted = self.cfg["wall_orientation"] or "vertical"
        # Une photo se lit reduite a la taille de l'ecran : sa definition ne
        # pese rien, contrairement a une video 4K a decoder.
        heavy_cut = len(self.wall.panes) >= 4 and not photo_mode()
        known = set()
        fresh = []
        for key in batch:
            info = INDEX.probe(key) or {}
            width, height = info.get("width") or 0, info.get("height") or 0
            # Sonde ou non, il quitte la liste d'attente : une video que
            # ffprobe ne lit pas ne reviendrait sinon qu'a chaque lot.
            known.add(key)
            if not (width and height):
                continue
            if heavy_cut and max(width, height) > 1920:
                continue
            if wanted == "any" or (
                    ("vertical" if height > width else "horizontal") == wanted):
                fresh.append(key)
        if known:
            self._wall_unsure = [key for key in self._wall_unsure
                                 if key not in known]
            self._pool_memo = None
        pool = self.wall.pool
        have = set(pool)
        fresh = [key for key in fresh if key not in have]
        if fresh:
            # Le vivier s'etoffe sans relancer le mur : la video qu'on
            # commencait a regarder restait sinon remplacee par une autre.
            pool = list(pool) + fresh
            self.wall.grow_pool(pool)
            self.wall.set_caption(len(pool), len(self._wall_unsure))
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

    def set_wall_fit(self, mode: str) -> None:
        """« Tout montrer », « tout remplir » ou « remplir à 100 % », retenu
        d'une fois sur l'autre."""
        self.cfg["wall_fit"] = mode
        self.cfg.save_soon()
        self.wall.set_fit(mode)

    @staticmethod
    def _aspect_of(path) -> float:
        """Largeur sur hauteur, d'apres l'index ; 0 si on l'ignore encore."""
        info = INDEX.probe(str(path)) or {}
        width, height = info.get("width") or 0, info.get("height") or 0
        return width / height if width and height else 0.0

    def set_wall_orientation(self, orientation: str) -> None:
        self._wall_pinned = []
        self.cfg["wall_orientation"] = orientation
        self.cfg.save_soon()
        self.wall.set_orientation(orientation)
        self.show_wall()

    def wall_pane_full(self, index: int) -> None:
        """Double-clic sur un panneau : cette video seule, en plein ecran ; un
        second double-clic (ou Echap) rend le mur tel qu'il etait."""
        if self.wall_full and self.wall.solo == index:
            return self.toggle_wall_fullscreen(False)
        if self.wall.solo != index:
            self.wall.toggle_solo(index)
            self._solo_for_full = True
        if not self.wall_full:
            self.toggle_wall_fullscreen(True)

    def toggle_wall_fullscreen(self, on: bool | None = None) -> None:
        """Le mur seul, sur tout l'écran. Échap ramène tout le reste."""
        self.wall_full = (not self.wall_full) if on is None else bool(on)
        if not self.wall_full and getattr(self, "_solo_for_full", False):
            # Seule par un double-clic : elle rend sa place en sortant.
            self._solo_for_full = False
            self.wall.unsolo()
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

        Une seule source, la collection en memoire -- deja filtree par le
        voile. L'index etait relu en plus, a chaque ouverture et a chaque
        lot sonde : deux a quatre secondes de gel, et un dossier masque
        pouvait jouer en grand sur le mur. Le resultat se garde tant que ni
        la collection, ni les reglages, ni les sondages ne bougent.
        """
        mark("vertical_pool")
        terms = self._terms((self.criteria or {}).get(
            "include", self.cfg["filter_include"]))
        wanted = self.cfg["wall_orientation"] or "vertical"
        only_unseen = bool(self.cfg["only_unseen"])
        key = (self._collection_key(), wanted, only_unseen, tuple(terms),
               len(INDEX.probes), len(INDEX.seen) if only_unseen else 0)
        memo = self._pool_memo
        if memo is not None and memo[0] == key:
            self._wall_unsure = list(memo[2])
            return list(memo[1]), len(memo[2])
        videos = self._videos_from_items()
        if not self._collection_known():
            # La collection de la racine n'est pas encore lue (premier
            # inventaire interrompu) : l'index, a defaut, masque compris.
            top = self.top_root()
            extra = []
            if top is not None:
                try:
                    for item in cached_items(top, MODE_FOLDERS,
                                             self.cfg["expand_parents"]):
                        extra.extend(video for video in item.videos
                                     if not under_veiled(video))
                except Exception:
                    pass
            videos = list(videos) + extra
        found = []
        unsure = []
        seen = set()
        probe = INDEX.probe
        is_seen = INDEX.is_seen
        for video in videos:
            key_ = str(video)
            if key_ in seen:
                continue
            seen.add(key_)
            if terms and not any(term in key_.lower() for term in terms):
                continue
            if only_unseen and is_seen(key_):
                continue
            if wanted == "any":
                found.append(key_)
                continue
            info = probe(key_) or {}
            width, height = info.get("width") or 0, info.get("height") or 0
            if not (width and height):
                unsure.append(key_)
                continue
            kind = "vertical" if height > width else "horizontal"
            if kind == wanted:
                found.append(key_)
        self._pool_memo = (key, found, unsure)
        self._wall_unsure = list(unsure)
        return list(found), len(unsure)

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
        # La suivante qu'aucun autre panneau ne montre deja : cinq panneaux
        # dans le meme dossier finissaient sur les memes videos.
        busy = {pane.video_path for pane in self.wall.panes if pane.video_path}
        target = listing[(at + 1) % len(listing)]
        for step in range(1, len(listing)):
            candidate = listing[(at + step) % len(listing)]
            if candidate not in busy:
                target = candidate
                break
        mark("wall.play sibling")
        self.wall.play_in(index, target)

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
            self.wall.set_pool(self._wall_pinned, first=self._take_wall_return())
            self.wall.set_caption(len(self.wall.pool), pinned=True)
        else:
            self.wall.single_row = False
            self.wall.set_shape(None)
            pool, unknown = self.vertical_pool()
            heavy = 0
            if len(self.wall.panes) >= 4 and not photo_mode():
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
            self.wall.set_pool(pool, first=self._take_wall_return())
            self.wall.set_caption(len(pool), unknown, heavy=heavy)
        self.wall.set_unseen(bool(self.cfg["only_unseen"]))
        self.wall.orient_button.setToolTip(
            (self.wall.caption.text() or "Le mur") + " — cliquer pour changer")
        self.item_title.setText(self.wall.caption.text()
                                or f"{len(self.wall.pool)} {self.wall.noun}(s) pour le mur")
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
        if not self._random_launching:
            self._random_on = False
        if self.tab == TAB_SPLIT:
            back = self._wall_snapshot()
            if self.wall_full:
                self.toggle_wall_fullscreen(False)
            self.set_tab(TAB_FOLDERS)
            self.play_in_app(path)
            if not self.browsing:
                self._back_to_wall = back
            return
        for position, item in enumerate(self.items):
            if str(item.path) == path:
                self.on_board_open(position)
                return
        self.play_in_app(path)

    def _wall_snapshot(self) -> dict:
        """Le mur tel qu'on le quitte pour une fiche : Echap y ramene, avec les
        memes videos -- on en retrouvait un autre, tire a neuf -- et en plein
        ecran s'il l'etait."""
        return {"paths": [pane.video_path for pane in self.wall.panes],
                "pinned": list(self._wall_pinned),
                "single_row": self.wall.single_row,
                "full": self.wall_full}

    def return_to_wall(self) -> None:
        """Echap sur une fiche ouverte depuis le mur : le meme mur, les memes videos."""
        back, self._back_to_wall = self._back_to_wall, None
        if back is None:
            return
        if self.cinema:
            self.toggle_cinema(False)
        # La fiche avait ouvert le dossier de sa video : le mur, lui, regarde
        # la collection. Reste sur ce dossier, Ctrl+R le relisait, et le fil
        # d'Ariane ou Alt+← posaient une planche a la place des panneaux.
        top = self.top_root()
        if top is not None and (self.levels or Path(self.root) != Path(top)):
            if self.scanning and not self._scan_top:
                self.stop_scan()
                self.progress.hide()
            self.levels = []
            self.root = top
        # Un mur de videos cochees : ce sont elles qu'on retrouve. Poses
        # avant l'onglet, qui s'ouvre sur elles.
        self._wall_pinned = list(back["pinned"])
        if back["pinned"]:
            self.wall.single_row = back["single_row"]
        self._wall_return = back["paths"]
        # L'onglet d'ou l'on etait venu au mur reste le meme : la fiche n'est
        # qu'un detour, et le dernier Echap du mur doit y ramener.
        kept = getattr(self, "_tab_before_wall", None)
        self.set_tab(TAB_SPLIT)
        if kept:
            self._tab_before_wall = kept
        if back.get("full") and not self.wall_full:
            # Apres l'onglet : le plein ecran retient l'etat fenetre du moment,
            # que l'Echap suivant rendra.
            self.toggle_wall_fullscreen(True)
        self._wall_return = None

    def _take_wall_return(self):
        """Les videos du mur a reprendre, une seule fois."""
        paths, self._wall_return = self._wall_return, None
        return paths

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
                    # Ses videos sont connues, pas ses autres fichiers : -1
                    # dit « non compte », et la garde de suppression demande.
                    # En annoncer autant que de videos le faisait passer pour
                    # un dossier de videos seules, papiers compris.
                    item = Item(path=Path(key), kind=MODE_FOLDERS,
                                videos=inside, video_count=len(inside),
                                file_count=-1)
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
        # La feuille du fil, posee une fois : `refresh_board` la redessine,
        # ici comme apres un filtre ou un tri, qui la perdaient.
        self._list_leaf = "★ Favoris"
        self._apply_selectors()
        self.board.empty.setText(
            "Aucun favori pour l'instant : l'étoile d'une vignette (au survol) ou "
            "d'une fiche, ou la touche 1, en ajoute un.")
        # Les comptes suivent : la planche les refait en changeant de page
        # (`on_board_page`).
        self.refresh_board()
        QTimer.singleShot(150, self.start_harvest)

    def _prepare_flat(self, show: bool = False) -> None:
        """Prepare en tache de fond la liste a plat de l'onglet Videos.

        Au premier clic sur « Vidéos », cent mille elements se fabriquaient
        sur le fil de l'interface -- une a cinq secondes ou l'application
        semblait plantee. On les fait des que la collection est connue,
        pendant qu'on regarde autre chose, avec leur liste triable et leurs
        noms replies : le premier filtre n'a plus rien a preparer non plus.

        `show` : l'onglet Videos est a l'ecran et attend cette liste (fin
        d'une relecture) ; elle s'y pose des qu'elle est prete, liste et
        planche ensemble, sans rebattre ce qu'on regardait.
        """
        if self._closing or not self._collection_known():
            return
        key = self._collection_key()
        kept = self._flat_list
        if kept is not None and kept[0] == key:
            if show:
                self._show_prepared_flat()
            return
        self._flat_show = self._flat_show or show
        if self._flat_job == key:
            return
        self._flat_job = key
        collection = list(self._collection())
        cache = self._flat_cache
        rebuild = self._sortable_key()
        sortable = self._sortable

        def work():
            with _without_gc():
                seen = set()
                videos = []
                for item in collection:
                    if item.processed:
                        continue
                    for video in item.videos:
                        text = str(video)
                        if text in seen or under_veiled(text):
                            continue
                        seen.add(text)
                        videos.append(video)
                fresh = {}
                for video in videos:
                    text = str(video)
                    made = cache.get(text)
                    if made is None:
                        made = Item(path=_as_path(video), kind=MODE_FILES,
                                    videos=[video], video_count=1, file_count=1)
                    fresh[text] = made
                flat = list(fresh.values())
                triable = [one for one in flat if sortable(one)]
                names = [fold(one.name) for one in triable]
            return videos, fresh, flat, triable, names

        def then(result) -> None:
            if not shiboken6.isValid(self):
                return
            if self._flat_job == key:
                self._flat_job = None
            if not result or self._closing or self._collection_key() != key:
                # La collection a encore bouge : la prochaine demande refera.
                return
            kept = self._flat_list
            if kept is None or kept[0] != key:
                videos, fresh, flat, triable, names = result
                self._videos_memo = (key, videos)
                self._flat_cache = fresh
                self._flat_list = (key, flat)
                if self._sortable_key() == rebuild:
                    memo = self._sortable_memo
                    memo[id(flat)] = [flat, rebuild, triable, len(flat), names,
                                      None]
                    while len(memo) > self.SORTABLE_KEPT:
                        memo.pop(next(iter(memo)))
            if self._flat_show:
                self._show_prepared_flat()

        from .tunnel import Chore
        Chore(work, self, fallback=None, then=then).start()

    def _show_prepared_flat(self) -> None:
        """La liste a plat, prete, prend la place de celle d'avant la relecture."""
        self._flat_show = False
        kept = self._flat_list
        if (kept is None or self.tab != TAB_VIDEOS or not self.browsing
                or self.levels or self._transient or self.all_items is kept[1]):
            return
        self.show_videos_tab(keep_order=True)
        self.refresh_board()

    def show_videos_tab(self, keep_order: bool = False) -> None:
        """Toutes les vidéos de la racine, sans relire le disque.

        Les parcourir pour de bon signifiait traverser toute l'arborescence du
        partage avant d'afficher quoi que ce soit — plusieurs minutes, pendant
        lesquelles l'onglet annonçait « rien à afficher ». Or l'index porte déjà
        la liste des vidéos de chaque dossier : leur réunion est la même réponse,
        obtenue en mémoire.

        La liste à plat se garde tant que la collection ne bouge pas : la même
        liste, les mêmes objets. La refaire coûtait de une demi-seconde à cinq
        secondes à chaque visite, et c'est elle que la liste triable, la
        récolte et les filtres reconnaissent pour ne rien refaire.

        `keep_order` : la liste se complète (fin d'une analyse) sans se
        rebattre — ce qu'on regardait garde sa place.
        """
        key = self._collection_key()
        kept = self._flat_list
        if kept is not None and kept[0] == key:
            flat = kept[1]
        elif (kept is not None and self.scanning and self._scan_top
              and kept[0][0] == key[0] and kept[0][4:] == key[4:]):
            # La collection se relit : la liste d'avant la relecture, tout de
            # suite. Elle se complete a la fin (`on_scan_finished`), au lieu
            # de se refaire ici, cent mille elements a chaque visite.
            flat = kept[1]
        else:
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
                    text = str(video)
                    item = cache.get(text)
                    if item is None:
                        item = Item(path=_as_path(video), kind=MODE_FILES,
                                    videos=[video], video_count=1, file_count=1)
                    fresh[text] = item
            self._flat_cache = fresh
            flat = list(fresh.values())
            self._flat_list = (key, flat)
        previous = self.items if keep_order else None
        self.all_items = flat
        self.items = self._filtered()
        self.mode = MODE_FLAT
        if previous and self.sort_mode == "random":
            import random
            # Ce qui etait a l'ecran garde son rang ; ce qui arrive se melange
            # a la suite.
            rank = {id(item): at for at, item in enumerate(previous)}
            random.shuffle(self.items)
            last = len(rank)
            self.items.sort(key=lambda item: rank.get(id(item), last))
        else:
            self.apply_sort()
        self.index = 0
        # Les comptes suivent la planche, que l'appelant rebatit
        # (`on_board_page`) : les faire ici les calculait sur l'ancienne.
        # Sans cela, l'onglet Videos ne beneficiait d'aucune preparation : il ne
        # passe par aucune analyse, et c'est elle seule qui lancait la recolte.
        # Un instant apres l'affichage, pas avant : l'onglet doit d'abord
        # apparaitre.
        QTimer.singleShot(150, self.start_harvest)

    def set_tab(self, tab: str) -> None:
        """Change de point de vue sans changer de collection.

        Les trois onglets ne sont pas trois applications : ouvrir une vidéo,
        d'où qu'elle vienne, mène toujours à la même fiche, avec ses
        destinations et sa note. Seule la façon de présenter l'ensemble change.
        """
        if tab not in TABS:
            return
        if not self._random_launching:
            self._random_on = False
        mark(f"set_tab {tab}")
        # Recliquer l'onglet ou l'on est ramene chez soi : a la racine, sur les
        # vignettes. C'est le geste qu'on fait quand on s'est perdu en
        # descendant, et il ne faisait rien.
        if tab == self.tab:
            # Recliquer l'onglet ou l'on est ramene chez soi ; s'y trouver
            # deja ne coute rien.
            return self.go_home()
        # L'endroit qu'on quitte, sous son onglet, pour Alt+← (`_show_tab_list`).
        here = self._here() if self.root is not None else None
        self._enter_tab(tab)
        if self.root is None:
            return
        top = self.top_root()
        if tab == TAB_SPLIT:
            # Le fil ne porte plus la fiche qu'on vient de quitter.
            self.crumbs.set_path(top, self.root)
            # Le mur ne change ni de dossier ni de mode : il regarde autrement
            # ce que l'on a deja sous la main.
            self.show_wall()
            self.setFocus()
            return
        # Un onglet est un point de vue sur **toute** la collection, pas sur
        # la liste qu'on quitte : depuis les favoris ou un sous-dossier,
        # « Vidéos » ne montrait que ceux-la, et « Dossiers » restait sur les
        # favoris. Chaque onglet repart donc de la collection.
        if not self._show_tab_list(departure=here):
            self.start_root(top, MODE_FOLDERS, reset_levels=True)

    def _enter_tab(self, tab: str) -> None:
        """Passe sous cet onglet -- classement, geste de l'arborescence, mur --
        sans rien afficher encore : a l'appelant de dire quoi (sa liste, ou
        l'endroit qu'Alt+← retrouve)."""
        was = self.tab
        if tab == TAB_SPLIT and was != TAB_SPLIT:
            # D'ou l'on vient : le dernier Echap du mur y ramene.
            self._tab_before_wall = was
        self.tab = tab
        self.content = (CONTENT_VIDEOS if tab == TAB_VIDEOS
                        else CONTENT_FOLDERS)
        # Chaque onglet retrouve son classement. « Vidéos » repart toujours au
        # hasard : sans quoi les memes videos reviendraient en tete. Il
        # l'imposait a toute l'application, et « Dossiers » se remelangeait
        # ensuite a chaque retour.
        self._tab_sorts[was] = self.sort_mode
        self.sort_mode = ("random" if tab == TAB_VIDEOS
                          else self._tab_sorts.get(tab, ""))
        self._tab_sorts[tab] = self.sort_mode
        self.controls.set_sort(self.sort_mode or "random")
        # Changer de collection ramene aux vignettes. L'onglet ne s'ecrit
        # plus dans les reglages : on demarre toujours sur « Dossiers », et
        # chaque clic reecrivait le fichier pour rien.
        self.browsing = True
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

    def _show_tab_list(self, restore_id: str = "", remember: bool = True,
                       departure: dict | None = None) -> bool:
        """La liste de l'onglet courant, tiree de la collection, sans relire.

        Seul chemin vers la liste d'un onglet : en changer, le recliquer ou
        remonter du dernier niveau y menent tous. Faux quand la collection
        n'est pas encore connue et qu'il faut donc lire la racine.
        Le vrai dossier qu'on quitte ainsi s'empile dans l'historique : Alt+←
        y ramene (`departure`, pris par l'appelant avant de changer d'onglet
        ou de niveau). Un simple changement d'onglet a la racine, non.
        """
        top = self.top_root()
        if top is None:
            return False
        known = (self._plain_root is not None
                 and Path(self._plain_root) == Path(top))
        if not known and self.tab != TAB_VIDEOS:
            return False
        place = departure or (self._here() if self.root is not None else None)
        if (remember and place is not None and place.get("root") is not None
                and Path(place["root"]) != Path(top)):
            self._remember(place)
        if self.scanning and not self._scan_top:
            # Une relecture de sous-dossier n'a plus rien a dire. Celle de la
            # racine, elle, continue : elle tient la collection a jour.
            self.stop_scan()
            self.progress.hide()
        if not self.aside_playlist and not self.aside.isHidden():
            # La video ouverte a cote appartenait a la liste qu'on quitte
            # (↑ depuis un dossier, la racine du fil) : ses ◂ ▸ n'y menaient
            # plus a rien.
            self.close_aside()
        self.levels = []
        self.root = top
        self.browsing = True
        # Une liste de passage (doublons) s'efface devant celle de l'onglet.
        self._leave_transient()
        self._goto_page(PAGE_SORT)
        self._hush_players()
        self._list_leaf = ""
        self.crumbs.set_path(top, top)
        # Le message d'une liste vide appartient a l'onglet qui l'a pose.
        self.board.empty.setText("Rien à afficher ici.")
        if self.tab == TAB_FAVS:
            self.mode = MODE_FOLDERS
            self.show_favorites()
        elif self.tab == TAB_TAGS:
            # Les mots-cles se tirent de la collection sans passer par la
            # liste des dossiers : la filtrer et la melanger pour la jeter
            # aussitot, puis batir la planche deux fois, coutait pour rien.
            self.mode = MODE_FOLDERS
            self.index = 0
            self._apply_selectors()
            self._add_tag_items()
        else:
            if self.tab == TAB_VIDEOS:
                self.show_videos_tab()
            else:
                self.restore_folders()
            self._apply_selectors()
            # Les comptes suivent la planche (`on_board_page`).
            self.refresh_board()
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
        if self._at_tag_list() and self.tag_family != "mine":
            self._add_tag_items()

    def scan_signatures(self) -> None:
        """Remplit la base d'empreintes — seulement ce qui n'y est pas encore.

        Quatre images par video, retenues avec sa taille et sa date. Le
        premier passage coute ; ensuite, ajouter mille videos ne demande que
        de sonder ces mille-la, et la recherche de doublons devient immediate.
        """
        if self.sig_scan is not None:
            self._sigs_cut = True
            self.sig_scan.stop()
            self.show_banner("Empreintes : arrêt demandé…", "quiet")
            return
        if self.root is None or not self._sigs_can_be_kept():
            return
        top = self.top_root()
        self.sig_scan = SignatureScan(top, self.cfg["thumb_width"],
                                      self.cfg["skip_hidden"], self)
        self._own_thread(self.sig_scan, "sig_scan")
        self._sigs_unreadable = 0
        self._sigs_started = None
        self._sigs_cut = False
        self._interim_next = None
        self._interim_told = 0
        self.sig_scan.progress.connect(self._sigs_progress)
        # Le recensement dure : il dit ce qu'il trouve en chemin, au lieu de
        # laisser croire que rien ne se passe.
        self.sig_scan.walking.connect(
            lambda found: self._task_said(
                "sig_scan",
                f"empreintes : recensement {self._thousands(found)} vidéos"))
        self.sig_scan.unreadable.connect(self._sigs_unreadable_count)
        self.sig_scan.done.connect(self._told_sigs)
        self.sig_scan.start()
        self.show_banner(
            f"Empreintes sous {top} : seules les vidéos nouvelles ou modifiées "
            "sont sondées. Recliquer arrête.", "info")

    def _sigs_progress(self, done: int, total: int) -> None:
        """« empreintes 1 200 / 58 000 — encore ~3 h 20 » : le temps restant,
        d'apres le rythme tenu jusque-la. Sans lui, une longue premiere fois
        ressemblait a une recherche bloquee."""
        now = time.monotonic()
        self._sig_counts = (done, total)
        if self._sigs_started is None:
            self._sigs_started = (now, done)
        line = f"empreintes {self._thousands(done)} / {self._thousands(total)}"
        began, first = self._sigs_started
        spent, made = now - began, done - first
        if spent >= 20 and made >= 5 and total > done:
            left = (total - done) * spent / made
            hours, minutes = divmod(int(left // 60), 60)
            line += (f" — encore ~{hours} h {minutes:02d}" if hours
                     else f" — encore ~{max(1, minutes)} min")
        self._task_said("sig_scan", line)
        self._interim_dupes(done)

    # Pendant les empreintes de « Chercher les doublons », ce qui est deja
    # releve se compare de temps en temps : on voit les doublons en cours de
    # route au lieu d'attendre des heures. Premiere etape apres trois minutes,
    # puis au plus toutes les dix (davantage si la comparaison est longue).
    INTERIM_FIRST_S = 180
    INTERIM_EVERY_S = 600

    def _interim_dupes(self, done: int) -> None:
        """Lance une comparaison d'etape, si c'est le moment."""
        if (not getattr(self, "_dupes_after_sigs", False) or self.dupes is not None
                or self.root is None or getattr(self, "_sigs_cut", False)):
            return
        now = time.monotonic()
        due = getattr(self, "_interim_next", None)
        if due is None:
            self._interim_next = now + self.INTERIM_FIRST_S
            return
        if now < due:
            return
        self._interim_began = now
        self._interim_next = now + self.INTERIM_EVERY_S
        self._dupes_by_image = True
        self._dupes_from_sigs = True
        self._dupes_stopped = False
        self.dupes = SignatureGroupScan(self.top_root(), self)
        # Discrete : ni barre de progression, ni bandeau au depart.
        self.dupes.interim = True
        self._own_thread(self.dupes, "dupes")
        self.dupes.found.connect(self.on_dupes_found)
        self.dupes.start()

    def _sigs_unreadable_count(self, count: int) -> None:
        self._sigs_unreadable = int(count)

    def _told_sigs(self, seen: int, total: int) -> None:
        self.sig_scan = None
        if self._closing:
            return
        self._refresh_state()
        unreadable = getattr(self, "_sigs_unreadable", 0)
        cut = getattr(self, "_sigs_cut", False)
        self.show_banner(
            (f"Empreintes arrêtées : {total} dans la base, la suite reprendra "
             "où elle s'est arrêtée." if cut else
             f"Empreintes : {seen} vidéo(s) sondée(s) cette fois, "
             f"{total} dans la base.")
            + (f" {unreadable} illisible(s), retentée(s) au prochain passage."
               if unreadable else "")
            + " « Doublons d'après les empreintes » peut maintenant les "
            "comparer.", "done" if not unreadable else "info")
        if getattr(self, "_dupes_after_sigs", False):
            # « Chercher les doublons » : la comparaison suit, sans rien
            # demander -- arretees ou non, les empreintes deja relevees se
            # comparent : ce qui a ete trouve n'est pas jete.
            self._dupes_after_sigs = False
            if getattr(self.dupes, "interim", False):
                # Une etape tourne encore : elle cede la place a la finale.
                self._dupes_stopped = True
                self._dupes_next = "sigs"
                self.dupes.stop()
            else:
                self.duplicates_from_sigs()
            if cut:
                self.show_banner(
                    f"Empreintes arrêtées ({total} dans la base, la suite "
                    "reprendra où elle s'est arrêtée) : comparaison de celles "
                    "déjà relevées… Recliquer « Chercher les doublons » "
                    "l'arrête aussi.", "info")

    def find_dupes(self) -> None:
        """La seule recherche de doublons : d'apres les empreintes.

        Sonde d'abord les videos qui n'en ont pas encore (seulement celles-la :
        la seconde fois, c'est presque immediat), puis compare, tout seul.
        Recliquer arrete ce qui tourne.
        """
        if self.sig_scan is not None:
            # L'arrete -- et compare ce qui est deja releve.
            self._dupes_after_sigs = True
            return self.scan_signatures()
        if self.dupes is not None:
            return self.duplicates_from_sigs()   # l'arrete aussi
        if self.root is None or not self._sigs_can_be_kept():
            return
        missing = self._sigs_missing()
        if missing >= self.SIGS_WARN and not self._confirm_first_dupes(missing):
            return self.setFocus()
        self._dupes_after_sigs = True
        self.scan_signatures()

    # Au-dela, la premiere recherche s'annonce : sans prevenir, on la croyait
    # bloquee au bout de dix minutes.
    SIGS_WARN = 300

    def _sigs_can_be_kept(self) -> bool:
        """Faux, et le dit, quand l'index ne peut rien retenir.

        Sans index (un autre Prisme le tient, disque refuse), les empreintes
        ne vivaient qu'en memoire : des heures de sondage perdues a la
        fermeture, et tout etait a refaire au lancement suivant.
        """
        if INDEX.db is not None:
            return True
        why = f" ({INDEX.problem})" if INDEX.problem else ""
        self.show_banner(
            "Empreintes impossibles à retenir : l'index de Prisme n'est pas "
            f"disponible{why}. Fermez les autres fenêtres de Prisme, puis "
            "relancez-le avant de chercher les doublons.", "error")
        return False

    def _sigs_missing(self) -> int:
        """Combien de videos de la collection n'ont pas encore d'empreinte
        (une estimation : la collection telle qu'on la connait)."""
        try:
            if self._collection_known():
                total = sum(len(item.videos) for item in self._collection())
            else:
                total = int((self.cfg["collection"] or {}).get("videos") or 0)
            from . import roots as _roots
            known = sum(INDEX.sig_count_under(member)
                        for member in _roots.members(self.top_root()))
            return max(0, total - known)
        except Exception:                               # noqa: BLE001
            return 0

    def _confirm_first_dupes(self, missing: int) -> bool:
        """Dit, avant de lancer, que la premiere fois est longue -- et pourquoi
        ca vaut le coup."""
        box = QMessageBox(self)
        box.setWindowTitle("Chercher les doublons")
        box.setIcon(QMessageBox.Information)
        box.setText(f"<b>Une première fois un peu longue, et c'est fini.</b>")
        box.setInformativeText(
            f"Prisme doit d'abord relever l'empreinte de "
            f"{self._thousands(missing)} vidéo(s) : quelques images de chacune, "
            "qui permettent de reconnaître une copie même réencodée, recadrée ou "
            "renommée. Sur une grande collection, cela prend plusieurs heures.\n\n"
            "Pendant ce temps, Prisme reste utilisable, et le temps restant "
            "s'affiche en haut. Vous pouvez arrêter quand vous voulez : rien "
            "n'est perdu, la suite reprendra où elle s'est arrêtée.\n\n"
            "Ensuite, seules les nouvelles vidéos sont relevées : les recherches "
            "suivantes prennent quelques secondes.")
        go = box.addButton("C'est parti", QMessageBox.AcceptRole)
        box.addButton("Plus tard", QMessageBox.RejectRole)
        box.setDefaultButton(go)
        box.exec()
        return box.clickedButton() is go

    def duplicates_from_sigs(self) -> None:
        """Compare les empreintes deja en base. Aucun acces au disque.

        Dans un fil : le calcul prenait de trente secondes a plusieurs minutes
        sur le fil de l'interface, qui ne repondait plus. La lecture de la
        base s'y fait aussi, avec le tri par racine — « X:\\Films2 » n'est plus
        pris pour un morceau de « X:\\Films ». Recliquer arrete.
        """
        if self.dupes is not None:
            self._stop_dupes("sigs")
            return
        if self.root is None:
            return
        top = self.top_root()
        self._dupes_by_image = True
        self._dupes_from_sigs = True
        self.dupes = SignatureGroupScan(top, self)
        self._start_dupes(f"Comparaison des empreintes sous {top}…")

    # -- le guetteur des doublons --------------------------------------------
    # Au-dela, ce n'est plus « quelques nouvelles videos » mais une premiere
    # fois : on ne la lance pas sans le dire (« Chercher les doublons »).
    WATCH_MAX = 400

    def _set_dupes_watch(self, on: bool) -> None:
        self.cfg["dupes_watch"] = bool(on)
        self.show_banner("Les nouvelles vidéos seront comparées à la collection à chaque "
                         "arrivée." if on else "Plus de vérification automatique des "
                         "doublons.", "quiet")
        if on:
            QTimer.singleShot(500, self._watch_new_dupes)

    def _watch_new_dupes(self) -> None:
        """Le guetteur : des videos sont arrivees, sont-elles deja la ?

        Seules les videos sans empreinte (les nouvelles) sont sondees, puis
        comparees a toute la collection, en fond et sans bandeau. Rien ne se
        dit s'il n'y a rien ; sinon un bandeau ouvre leur comparaison. Il
        attend qu'une premiere recherche de doublons ait releve la
        collection : sans elle, tout serait « nouveau »."""
        if (self._closing or self.root is None or not self.cfg["dupes_watch"]
                or self.sig_scan is not None or self.dupes is not None
                or INDEX.db is None):
            return
        try:
            from . import roots as _roots
            known = sum(INDEX.sig_count_under(m) for m in _roots.members(self.top_root()))
        except Exception:                               # noqa: BLE001
            return
        missing = self._sigs_missing()
        if not known or not missing or missing > self.WATCH_MAX:
            return
        scan = SignatureScan(self.top_root(), self.cfg["thumb_width"],
                             self.cfg["skip_hidden"], self)
        scan.watch = True
        self.sig_scan = scan
        self._own_thread(scan, "sig_scan")
        scan.progress.connect(lambda done, total: self._task_said(
            "sig_scan", f"doublons : nouvelles vidéos {done} / {total}"))
        scan.done.connect(lambda seen, total, s=scan: self._watch_sigs_done(s, seen, total))
        scan.start()

    def _watch_sigs_done(self, scan, seen: int, total: int) -> None:
        if self.sig_scan is scan:
            self.sig_scan = None
        if self._closing:
            return
        if getattr(self, "_dupes_after_sigs", False):
            # « Chercher les doublons » a ete demande pendant ce temps.
            return self._told_sigs(seen, total)
        fresh = list(getattr(scan, "fresh", []) or [])
        if not fresh or self.dupes is not None:
            return
        keys = {os.path.normcase(p) for p in fresh}
        compare = SignatureGroupScan(self.top_root(), self)
        compare.watch = True
        self.dupes = compare
        self._own_thread(compare, "dupes")
        compare.found.connect(lambda groups, c=compare, k=keys, f=fresh:
                              self._watch_found(c, groups, k, f))
        compare.start()

    def _watch_found(self, compare, groups: list, keys: set, fresh: list) -> None:
        if self.dupes is compare:
            self.dupes = None
        if self._closing or not groups:
            return
        from .dupes_memory import NOT_DUPES
        groups = [g for g in groups
                  if any(os.path.normcase(str(p)) in keys for p in g.paths)]
        groups = NOT_DUPES.filtrer_groupes(groups)
        groups = self._one_spelling(self._only_in_collection(groups))
        if not groups:
            return
        new = sum(1 for g in groups for p in g.paths if os.path.normcase(str(p)) in keys)
        self._watch_result = (groups, fresh)
        self.show_banner(
            f"⚠ {new} nouvelle(s) vidéo(s) déjà dans la collection "
            f"({len(groups)} groupe(s)). Cliquez ici pour comparer et choisir "
            "quelle copie garder.", "info",
            action=lambda g=groups, f=fresh: self.show_found_dupes(g, fresh=f),
            seconds=40)

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
            lambda done, total: self._task_said(
                "scene_scan",
                f"plans {self._thousands(done)} / {self._thousands(total)}"))
        self.scene_scan.done.connect(self._told_scenes)
        self.scene_scan.start()
        self.show_banner(
            f"Repérage des plans sous {top}. Chaque vidéo est traversée une "
            "fois, images clés seulement ; rien n'est redemandé ensuite. "
            "Recliquer arrête.", "info")

    def _told_scenes(self, seen: int, found: int) -> None:
        self.scene_scan = None
        self._refresh_state()
        self.show_banner(
            f"Plans : {seen} vidéo(s) parcourue(s), {found} avec des "
            "changements de plan. Les vignettes s'y posent désormais.", "done")
        if not found:
            # Rien de nouveau : les apercus en place restent les bons.
            return
        self.plans = {}
        if not self.browsing and self.current is not None:
            # Seuls les apercus se refont : repasser par `show_item`
            # rechargeait la video qu'on regardait -- un noir, et la lecture
            # repartait de zero.
            self._request_previews(self.current, current=True)
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
            lambda done, total: self._task_said(
                "titles_scan",
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
        if self._at_tag_list() and self.tag_family != "mine":
            self._add_tag_items()

    def at_home(self) -> bool:
        """Vrai quand on est sur la liste d'un onglet, a sa racine."""
        if not self.browsing or self.levels:
            return False
        if self._transient:
            # Une liste de passage (doublons), sur n'importe quel onglet :
            # recliquer rend la collection. Sur « Vidéos », elle restait.
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
        if self.tab == TAB_SPLIT:
            # Le mur n'a pas de « chez soi » : le recliquer le laisse tel quel.
            return
        if self.at_home():
            if self.tab == TAB_FAVS and self.root is not None:
                # Recliquer « Favoris » refait la liste : une etoile retiree
                # garde sa carte (un clic de trop se rattrape d'un autre),
                # jusqu'ici. Rien n'est relu du disque.
                current = self.current
                self._show_tab_list(
                    restore_id=current.item_id if current is not None else "")
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
        # Le chemin de tous les onglets vers leur liste. Depuis l'interieur
        # d'un mot-cle, la remise a zero faite ici laissait le mode a plat :
        # les puces changeaient, la planche montrait toujours l'ancien mot, et
        # Echap quittait la racine.
        self.close_aside()
        if self.root is not None and not self._show_tab_list():
            self.start_root(self.top_root(), MODE_FOLDERS, reset_levels=True)

    def set_view(self, view: str) -> None:
        """Parcourir les vignettes, ou editer l'element ou l'on se trouve."""
        if view == self.view:
            return
        if view == VIEW_EDIT:
            self.on_board_open(self.index)
        else:
            self.show_board_at(self.index)

    def mode_for_content(self) -> str:
        """Le mode d'un dossier qu'on ouvre : celui de l'onglet, et lui seul.

        Il se lisait dans `content`, que l'ouverture d'un dossier sans
        sous-dossier passait a « videos » : sous « Dossiers », les dossiers
        ouverts ensuite se listaient a plat, relus recursivement sur le NAS.
        """
        return MODE_FLAT if self.tab == TAB_VIDEOS else MODE_FOLDERS

    def _mode_for_folder(self) -> str:
        """Le mode d'un vrai dossier ouvert par le fil d'Ariane ou ↑.

        Sous « Mots-clés », ses videos, comme on y voit celles d'un mot-cle :
        en dossiers, il recevait la liste des mots-cles de toute la
        collection, sous un chemin qui disait le contraire.
        """
        return MODE_FLAT if self.tab == TAB_TAGS else self.mode_for_content()

    def _at_tag_list(self) -> bool:
        """Vrai a la racine de l'onglet « Mots-clés », la ou vivent ses mots."""
        if self.tab != TAB_TAGS or self.levels or self.root is None:
            return False
        top = self.top_root()
        return top is not None and Path(self.root) == Path(top)

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
        self._show_word_strip()
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
        # La fiche d'une video : ☆, ⛶ et l'explorateur sont dans le bandeau
        # survole. En double sur la ligne du haut, ils ecrasaient le chemin
        # jusqu'a « V..A.. ». Ils restent sur la fiche d'un dossier.
        video_sheet = (sheet and item is not None and item.kind != MODE_FOLDERS
                       and not item.is_tag)
        self.cinema_button.setVisible(False)
        self.cinema_button.setChecked(self.cinema)
        self._show_folder_star()
        dupes = self._transient == "dupes"
        self.not_dupes_button.setVisible(dupes)
        self.not_dupe_button.setVisible(
            sheet and dupes and item is not None
            and getattr(item, "dupe_group", None) is not None)
        self.reveal_button.setVisible(sheet and item is not None and not video_sheet
                                      and not item.is_tag and not item.locked)
        # Le hasard local ne vaut que devant un dossier : devant une seule
        # video, il la relancait, ce qui n'a aucun sens.
        self.random_here_button.setVisible(sheet and folder
                                           and bool(item.videos))
        if not self._one_line:
            self.reveal_button.setText("Explorateur")
        # Le hasard « dans ce qu'on regarde » n'a de sens que si l'on regarde
        # une partie : des resultats filtres, ou un dossier ou l'on est entre.
        self._refresh_random_here()
        # Un dossier s'ouvre d'un clic sur son titre : c'est la que l'on
        # vient de decider qu'il fallait y descendre.
        # Une seule ligne sous l'image, et seulement sur une fiche.
        self.bottom_bar.setVisible(sheet and not self.cinema)
        for button in (self.prev_button, self.next_button):
            button.setVisible(bool(self.items))
        # Les dossiers se notent comme les videos ; un mot-cle, non. Sur la
        # fiche d'une video aussi, a cote de l'epingle, meme si le bandeau
        # survole a deja la sienne : on la cherchait ici.
        self.stars.setVisible(sheet and item is not None and not item.is_tag)
        self.pin_button.setVisible(sheet and item is not None and not item.is_tag)
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
        for button, label in ((self.reveal_button, "Explorateur"),
                              (self.cinema_button, "Cinéma"),
                              (self.not_dupe_button, "Pas des doublons")):
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
            # Sa largeur au plus, comme a la construction : « Ignored » lui
            # en donnait moins que son texte, et l'etoile se posait dessus
            # (« 7,1 Go » devenait « 7,1☆o »).
            self.item_subtitle.setMinimumWidth(60)
            self.item_subtitle.setSizePolicy(QSizePolicy.Maximum,
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
        if self.tab == TAB_SPLIT:
            # Le mur n'a pas de dossier : ouvrir un dossier ici posait une
            # planche a la place des panneaux, sous l'onglet « Mur ».
            return
        target = Path(path)
        item = self.current
        if (not self.browsing and item is not None and item.kind == MODE_FOLDERS
                and not item.is_tag and Path(item.path) == target):
            return self.enter_current()
        top = self.top_root()
        if (top is not None and target == Path(top) and self.tab != TAB_SPLIT
                and (self.levels or self._transient
                     or (self.root is not None and Path(self.root) != Path(top)))):
            # La racine du fil, depuis un dossier ou un mot-cle ouvert : la
            # liste de l'onglet (favoris, mots-cles…), et non les dossiers
            # relus du disque. Arrive par le fil d'une video, la pile etait
            # vide : l'onglet Videos relancait alors un parcours a plat de
            # tout le NAS pour une liste deja en memoire.
            return self.go_home()
        if self.root is not None and target == self.root:
            # Depuis une fiche, cliquer le dossier ou l'on est rend sa liste.
            if not self.browsing:
                self.show_board_at(self.index)
            return
        # Une seule question au NAS, que `start_root` ne repose pas.
        state = _folder_state(target)
        if state == "absent":
            self.show_banner(f"Introuvable : {target}", "error")
            return
        # L'endroit quitte, tel quel : les niveaux sont retailles juste apres.
        here = self._here()
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
        # Depuis une fiche, on vient voir le dossier de ce qu'on regarde : ses
        # vignettes, la carte de la video en vue. `start_root` ouvrait sinon
        # la fiche du premier element -- une autre video partait a sa place.
        shown = self.current if not self.browsing else None
        if shown is not None:
            self.browsing = True
            self._hush_players()
            self._apply_selectors()
        self.start_root(target, self._mode_for_folder(), reset_levels=False,
                        state=state, departure=here,
                        restore_id=shown.item_id if shown is not None else "")
        if shown is not None and self._board_on(shown.path):
            # Deja en vue : la relecture n'a plus a nous y poser.
            self._restore_id = ""

    def _board_on(self, path) -> bool:
        """La planche en vue sur cet element, ou sur le dossier qui le contient."""
        wanted = os.path.normcase(str(path))
        for position, item in enumerate(self.items):
            here = os.path.normcase(str(item.path))
            if here == wanted or wanted.startswith(here.rstrip("\\/") + os.sep):
                self.index = position
                self.board.scroll_to(position)
                return True
        return False

    def toggle_board(self, visible: bool | None = None) -> None:
        target = (not self.browsing) if visible is None else visible
        self.set_view(VIEW_BROWSE if target else VIEW_EDIT)

    def refresh_board(self) -> None:
        if not self.browsing or self.tab == TAB_SPLIT:
            # Le mur n'a pas de planche : un paquet de l'analyse de la racine
            # la remettait a l'ecran sous l'onglet « Mur », a la place du mur.
            # Quitter le mur rebatit la planche de toute facon.
            return
        mark(f"refresh_board {len(self.items)}")
        self._cancel_rename()
        self.viewer.setCurrentWidget(self.board)
        self.board.set_muted(self.cfg["muted"])
        self._explain_empty_board()
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

    def _explain_empty_board(self) -> None:
        """Une planche videe par les filtres le dit, et dit comment en sortir.

        Elle annoncait « Rien à afficher ici » : on croyait l'application
        figee, ou le dossier vide. Le message de l'onglet (favoris, mots-cles)
        revient des que les filtres laissent passer quelque chose -- sauf si
        l'onglet en a pose un autre entre-temps.
        """
        label = self.board.empty
        said = self._empty_said
        hidden = (not self.items and bool(self.all_items)
                  and bool(self._active_filters()))
        masked = self._sortable_count() if hidden else 0
        if masked:
            text = (f"Aucun élément ne correspond aux filtres — "
                    f"{self._thousands(masked)} masqué(s).\n"
                    "Modifiez-les, ou effacez-les d'un clic sur « ✕ filtres ».")
            base = (said[0] if said is not None and label.text() == said[1]
                    else label.text())
            label.setText(text)
            self._empty_said = (base, text)
        elif said is not None:
            if label.text() == said[1]:
                label.setText(said[0])
            self._empty_said = None

    # -- les couvertures qui changent ---------------------------------------
    def _covers_on(self, item) -> bool:
        return (bool(self.cfg["cover_period_s"]) and item.kind == MODE_FOLDERS
                and not item.locked and not item.is_tag and len(item.videos) >= 2)

    def _make_cover(self, video: str) -> bool:
        """Fabrique (en fond) l'image de carte de cette video."""
        return media.extract_thumb(Path(video), media.card_moment(video),
                                   self.cfg["thumb_width"], keyframe=True) is not None

    def _cover_now(self, position: int, item, index: int | None = None) -> bool:
        """Pose sur la carte la couverture du moment, si son image est deja
        faite. Vrai si c'est fait. (La premiere video, index 0, passe par le
        chemin habituel de la planche quand son image n'est pas prete.)"""
        if index is None:
            index = COVERS.show(str(item.path), item.videos)
        if not 0 <= index < len(item.videos):
            return False
        video = str(item.videos[index])
        moment = media.card_moment(video)
        ready = media.cached_thumb(Path(video), moment, self.cfg["thumb_width"])
        if ready is None:
            return False
        info = INDEX.probe(video) or {}
        self.board.set_source(position, (video, moment, info.get("duration") or 0.0,
                                         info.get("height") or 0))
        self.board.set_thumb(position, str(ready))
        self._cover_applied[str(item.path)] = index
        return True

    def _turn_covers(self) -> None:
        """Toutes les deux secondes : les cartes de la page dont le tour est
        venu (et la nouvelle image prete) changent de couverture."""
        if (not self.browsing or not self.isActiveWindow() or self.tab == TAB_SPLIT
                or self.board.isHidden()):
            return
        items = self.board.items
        first, last = self.board._page_bounds()
        for position in range(max(0, first), min(last, len(items))):
            item = items[position]
            if not self._covers_on(item):
                continue
            card = self.board._card_for(position)
            if card is None or not card.thumb_path:
                continue            # sa premiere image n'est pas encore la
            key = str(item.path)
            index = COVERS.show(key, item.videos)
            # On ne touche a la carte que si sa couverture change vraiment :
            # la reposer toutes les deux secondes remettait la page en place,
            # et la planche tournait la page toute seule.
            if index != self._cover_applied.get(key, 0):
                self._cover_now(position, item, index)

    def on_board_preview(self, position: int) -> None:
        """Une carte réclame son image : on lui construit sa première vignette."""
        if not (0 <= position < len(self.items)):
            return
        item = self.items[position]
        if not item.videos or item.locked:
            return
        if self._covers_on(item):
            index = COVERS.show(str(item.path), item.videos)
            if index and self._cover_now(position, item, index):
                return
            self._cover_applied[str(item.path)] = 0
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

    FLIP_COUNT = 10

    def on_board_flip(self, position: int) -> None:
        """Un dossier de photos survole : dix de ses photos, au hasard, a
        feuilleter sur sa carte."""
        if not (0 <= position < len(self.board.items)):
            return
        item = self.board.items[position]
        photos = [str(p) for p in item.videos]
        if len(photos) < 2:
            return
        import random
        picked = random.sample(photos, min(self.FLIP_COUNT, len(photos)))
        key = f"flip@{item.item_id}"
        self.preview.cancel_prefix("flip@", keep={key})
        width = self.cfg["thumb_width"]
        for slot, photo in enumerate(picked):
            ready = media.cached_thumb(Path(photo), 0.0, width)
            if ready is not None:
                self.board.add_flip(position, str(ready))
            else:
                self.preview.request_thumb(key, slot, photo, 0.0, urgent=True,
                                           keyframe=True)

    def _board_position_of(self, key: str) -> int:
        """La carte de la page affichee qui attend cette reponse, ou -1.

        Sur la seule page : une reponse pour une page quittee n'a plus de
        carte ou se poser, et la chercher parmi cent mille elements, a chaque
        image recue, coutait plus que l'image elle-meme.
        """
        item_id = key.split("@", 1)[1]
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
        self.aside_item_id = item.item_id
        self._aside_hint = position
        self.aside_playlist = []
        self._aside_play(str(item.videos[0]), item.name, str(item.path))

    @property
    def aside_index(self) -> int:
        """Position, dans la liste affichee, de la carte ouverte a cote -- ou -1.

        Retrouvee par son identifiant a chaque usage : un filtre, un tri ou
        une relecture deplacent les cartes, et la position retenue a
        l'ouverture designait ensuite une autre carte.
        """
        wanted = self.aside_item_id
        if not wanted:
            return -1
        items = self.items
        hint = self._aside_hint
        # Le plus souvent, rien n'a bouge : on regarde d'abord la ou elle etait.
        if 0 <= hint < len(items) and items[hint].item_id == wanted:
            return hint
        for position, item in enumerate(items):
            if item.item_id == wanted:
                self._aside_hint = position
                return position
        return -1

    def _aside_play(self, video: str, title: str, tip: str = "") -> None:
        """Le lecteur de droite, sur cette video."""
        mark("open_aside")
        self.aside_current = video
        self._restore_split()
        self.aside_title.setText(title)
        self.aside_title.setToolTip(tip or video)
        self.aside.show()
        self._wake_watch()
        self.aside_player.set_muted(self.cfg["muted"])
        self.aside_player.set_loop(False)
        self.aside_player.set_item(video)
        # Aucun apercu n'est demande : le lecteur de cote n'a pas de pellicule,
        # et fabriquer cinq images pour rien retardait celles de la planche.

    def _remember_split(self, *_args) -> None:
        if not self.aside.isHidden():
            self.cfg["aside_split"] = list(self.middle.sizes())
            self.cfg.save_soon()

    # Le lecteur de cote ne descend pas en dessous : il s'ouvrait a zero
    # pixel sur une configuration neuve.
    ASIDE_MIN_WIDTH = 280

    def _restore_split(self) -> None:
        """Le lecteur de droite reprend la largeur qu'on lui avait donnee.

        Sans largeur retenue -- installation neuve, reglages remis a zero, ou
        la largeur nulle qu'enregistrait l'ancien defaut --, deux cinquiemes
        de la place, et jamais moins de 360 pixels.
        """
        if not self.aside.isHidden():
            return
        wanted = self.cfg["aside_split"]
        kept = (isinstance(wanted, list) and len(wanted) == 3
                and all(isinstance(size, int) for size in wanted)
                and wanted[2] >= self.ASIDE_MIN_WIDTH)

        def place() -> None:
            tree = self.tree.width() if self.tree.isVisible() else 0
            if kept:
                self.middle.setSizes([tree] + list(wanted[1:]))
                return
            room = max(0, self.middle.width() - tree)
            side = max(360, room * 2 // 5)
            self.middle.setSizes([tree, max(0, room - side), side])

        QTimer.singleShot(0, place)

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
            self.aside_bar.set_favorite(self._is_favorite(self.aside_current))
            self._place_aside_bar()
            self.aside_bar.reveal()
        else:
            self.aside_bar.hide()
            self.aside_player.marks.set_favorite(self._is_favorite(self.aside_current))
            self.aside_player.marks.place_on(area)

    @staticmethod
    def _show_pause(bar, player) -> None:
        """Le bouton du milieu dit ce qu'il fera : pause si ca joue -- pour
        une photo, si le diaporama tourne."""
        button = getattr(bar, "pause_button", None) or bar.buttons.itemAt(1).widget()
        if getattr(player, "still_path", ""):
            playing = bool(getattr(player, "slideshow_on", False))
        else:
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
                   and not self.single.peeking
                   and not self.floating.active)
        if not playing:
            self.single_bar.hide()
            self.single.marks.hide()
            self.single.tuck_strip(now=True)
            return
        self._note_pointer_moves()
        if self.cinema:
            self._watch_cinema_strip(area)
        bar = self.single_bar
        on_bar = bar.isVisible() and bar.geometry().contains(QCursor.pos())
        # La souris se tait : le bandeau s'en va avec les cinq instants, et
        # il ne reste que le trait fin. Sur le bandeau, ou en tirant le
        # trait, il reste.
        awake = (on_bar or bar.dragging
                 or (self.cinema and self.single.strip_has_pointer())
                 or time.monotonic() - getattr(self, "_cine_moved", 0.0)
                 < self.STRIP_IDLE_S)
        if self.isActiveWindow() and self._pointer_on(area) and awake:
            self.single.marks.hide()
            self._show_pause(self.single_bar, self.single)
            self.single_bar.set_favorite(self._is_favorite(item.path))
            self.single_bar.set_position(self._position_text())
            self.single_bar.set_name(item.name)
            self.single_bar.place_on(area)
            self.single_bar.reveal()
        else:
            self.single_bar.hide()
            self.single.marks.set_favorite(self._is_favorite(item.path))
            self.single.marks.place_on(area)
        if self.banner.isVisible():
            self._place_banner()

    def _watch_needed(self) -> bool:
        """Une image joue-t-elle a l'ecran, a cote ou sur la fiche ?"""
        aside = (not self.aside.isHidden()
                 and bool(getattr(self, "aside_current", "")))
        sheet = (self.stack.currentIndex() == PAGE_SORT
                 and not self.isMinimized()
                 and self.viewer.currentWidget() is self.single)
        return aside or sheet

    # La pellicule repart quand la souris se tait depuis tant de secondes.
    STRIP_IDLE_S = 2.5

    def _note_pointer_moves(self) -> None:
        """Retient quand la souris a bouge pour la derniere fois."""
        pos = QCursor.pos()
        if pos != getattr(self, "_cine_pos", None):
            self._cine_pos = pos
            self._cine_moved = time.monotonic()

    def _watch_cinema_strip(self, area) -> None:
        """Plein ecran : la souris bouge sur l'image, les cinq instants
        arrivent ; elle se tait ou s'en va, ils repartent. Sur eux, ils
        restent."""
        now = time.monotonic()
        if self.single.strip_has_pointer() or (
                self.isActiveWindow() and self._pointer_on(area)
                and now - getattr(self, "_cine_moved", 0.0) < self.STRIP_IDLE_S):
            self.single.reveal_strip()
        else:
            self.single.tuck_strip()

    def _watch_bars_tick(self) -> None:
        """Un battement du guet ; il se rendort quand rien ne joue."""
        self._watch_bars()
        if not self._watch_needed():
            self.aside_watch.stop()

    def _wake_watch(self, *_args) -> None:
        """Ce qu'on voit a change : le guet des bandeaux reprend s'il le faut."""
        if not hasattr(self, "aside_watch"):
            return
        if (not self._timers_paused and not self._closing
                and not self.aside_watch.isActive() and self._watch_needed()):
            self.aside_watch.start()

    def _session_tick(self) -> None:
        """Le compteur de seance ne s'affiche que sur une fiche : sur la
        planche, le recompter toutes les trente secondes ne servait a rien."""
        if not self.browsing:
            self._show_counts()

    def changeEvent(self, event):
        """Fenetre reduite : les minuteurs se taisent, et reprennent au retour.

        Quinze a cinquante-sept reveils par seconde pour une fenetre qu'on
        ne voit pas : le processeur ne dormait jamais.
        """
        super().changeEvent(event)
        if not hasattr(self, "aside_watch"):
            return                      # la fenetre se construit encore
        if event.type() == QEvent.WindowStateChange and not self._closing:
            if self.isMinimized() and not self._timers_paused:
                self._timers_paused = True
                for timer in (self.activity_timer, self.session_timer,
                              self.aside_watch):
                    timer.stop()
                # Le chien de garde appartient a la derniere fenetre construite :
                # s'il est parti avec une autre, il n'y a rien a endormir.
                alive = shiboken6.isValid(WATCH) and shiboken6.isValid(WATCH.timer)
                self._watchdog_was = alive and WATCH.timer.isActive()
                if alive:
                    WATCH.timer.stop()
            elif not self.isMinimized() and self._timers_paused:
                self._timers_paused = False
                self.activity_timer.start()
                self.session_timer.start()
                if (getattr(self, "_watchdog_was", False)
                        and shiboken6.isValid(WATCH)
                        and shiboken6.isValid(WATCH.timer)):
                    # Le battement repart d'ici : sinon la reduction entiere
                    # s'inscrirait au journal comme un gel.
                    WATCH._beat = time.monotonic()
                    WATCH.timer.start()
                self._wake_watch()
            if not self.isMinimized() and not self._quiet:
                # Ce qui s'est dit pendant que la fenetre etait reduite.
                QTimer.singleShot(0, self._show_held_banners)
            if self.isMinimized():
                # Reduite pendant qu'une video joue : le lecteur flottant.
                QTimer.singleShot(0, self._float_check)
        elif event.type() == QEvent.ActivationChange:
            self._wake_watch()
            # Un dialogue modal prend le clavier : Ctrl+K doit y repondre
            # aussi, le temps qu'il est ouvert (`_QuietKeys`).
            self._quiet_keys.follow()
            self._float_follow()

    # ------------------------------------------------------------------
    # Le lecteur flottant
    # ------------------------------------------------------------------
    # Une video dont la position a avance il y a moins que cela jouait
    # encore : reduite, la fiche s'arrete avant qu'on ait pu le lui demander.
    FLOAT_PLAYING_S = 1.5

    def _note_sheet_position(self, position: int, _duration: int) -> None:
        """Ou en est la fiche : c'est de la que repartira le lecteur flottant,
        meme si la reduction de la fenetre l'a deja arretee."""
        if position > 0:
            deck = self.single.decks[self.single._active]
            self._sheet_position = (deck.path, position, time.monotonic())

    def _sheet_can_float(self) -> bool:
        """Une video est-elle ouverte dans la fiche ?"""
        item = self.current
        return (not self._quiet and not self._closing
                and self.stack.currentIndex() == PAGE_SORT
                and not self.browsing
                and self.viewer.currentWidget() is self.single
                and item is not None and item.kind != MODE_FOLDERS
                and not item.locked)

    def _sheet_playing(self) -> bool:
        if (self.single.player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState):
            return True
        noted = self._sheet_position
        return len(noted) == 3 and time.monotonic() - noted[2] < self.FLOAT_PLAYING_S

    def open_floating(self, auto: bool = False) -> None:
        """La video de la fiche passe dans le lecteur flottant, a son instant."""
        if self.floating.active:
            self.floating.raise_()
            return
        if not self._sheet_can_float():
            if not auto:
                self.show_banner("Ouvrez d'abord une vidéo : le lecteur flottant "
                                 "reprend celle de la fiche.", "quiet")
            return
        item = self.current
        path = str(item.path)
        key = os.path.normcase(path)
        deck = self.single.decks[self.single._active]
        position = 0
        if deck.path and os.path.normcase(deck.path) == key:
            position = self.single.player.position()
        if position <= 0 and os.path.normcase(self._sheet_position[0]) == key:
            position = self._sheet_position[1]
        wanted = self._pending_start
        if wanted is not None and wanted[0] == key:
            position = wanted[1]
            self._pending_start = None
        self.single.release()
        self.single_bar.hide()
        self.single.marks.hide()
        self.floating.auto = auto
        self.floating.open(path, item.name, position, muted=self.cfg["muted"],
                           geometry=self.cfg["float_geometry"])
        if not auto:
            self.show_banner("Lecteur flottant : déplacez-le, agrandissez-le. "
                             "↩ ou Échap le ramène dans Prisme.", "info")

    def leave_floating(self, activate: bool = True) -> None:
        """Referme le lecteur flottant ; la fiche reprend au meme instant."""
        if not self.floating.isVisible():
            return
        path = self.floating.path
        position = self.floating.position()
        self.cfg["float_geometry"] = self.floating.geometry_to_keep()
        self.cfg.save_soon()
        self.floating.close_quietly()
        if activate:
            if self.isMinimized():
                self.setWindowState(
                    (self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
            self.show()
            self.raise_()
            self.activateWindow()
        self._resume_sheet(path, position)

    def dismiss_floating(self) -> None:
        """✕ sur le lecteur flottant : il se retire, la video s'arrete, et
        Prisme reste ou il est. La fiche reprendra a cet instant quand on y
        reviendra ; d'ici la, le lecteur ne se rouvre pas tout seul."""
        if not self.floating.isVisible():
            return
        self._float_dismissed = True
        self._float_resume = (self.floating.path, self.floating.position())
        self.cfg["float_geometry"] = self.floating.geometry_to_keep()
        self.cfg.save_soon()
        self.float_timer.stop()
        self.floating.close_quietly()

    def _resume_sheet(self, path: str, position: int) -> None:
        """La fiche reprend la video du lecteur flottant, a son instant."""
        item = self.current
        if (not path or item is None or item.kind == MODE_FOLDERS
                or os.path.normcase(str(item.path)) != os.path.normcase(path)
                or not self._sheet_can_float()):
            return
        self._pending_start = ((os.path.normcase(path), int(position), time.monotonic())
                               if position > 0 else None)
        self.single.set_muted(self.cfg["muted"])
        self.single.set_item(path, "…")
        self._seek_when_ready()
        self._request_previews(item, current=True)
        self._wake_watch()

    def _float_follow(self) -> None:
        """Prisme reprend le clavier : le lecteur ouvert tout seul se retire.
        Il le perd : on guette s'il disparait de l'ecran."""
        if self._closing or not hasattr(self, "float_timer"):
            return
        if self.isActiveWindow():
            self.float_timer.stop()
            if self.floating.active and self.floating.auto:
                self.leave_floating(activate=False)
            elif self._float_dismissed:
                self._float_dismissed = False
                self._resume_sheet(*self._float_resume)
            return
        if (self.cfg["float_auto"] and not self.floating.active and not self._quiet
                and not self._float_dismissed):
            self.float_timer.start()
            QTimer.singleShot(250, self._float_check)

    def _float_check(self) -> None:
        """Prisme est-il encore visible ? Sinon, la video passe au premier plan."""
        if (not self.cfg["float_auto"] or self._quiet or self._closing
                or self._float_dismissed
                or self.floating.active
                # Reduit, Prisme ne se voit plus, meme s'il garde le clavier.
                or (self.isActiveWindow() and not self.isMinimized())):
            self.float_timer.stop()
            return
        if not self._sheet_can_float() or not self._sheet_playing():
            return
        if self.isMinimized() or _covered(self.single.video_area):
            self.float_timer.stop()
            self.open_floating(auto=True)

    def toggle_float_auto(self) -> None:
        self.cfg["float_auto"] = not self.cfg["float_auto"]
        self.cfg.save_soon()
        action = self._menu_by_text.get("Lecteur flottant automatique")
        if action is not None:
            action.setChecked(bool(self.cfg["float_auto"]))
        self._dress_float_button()
        self.show_banner(
            "Lecteur flottant automatique : quand Prisme est réduit ou "
            "recouvert, la vidéo continue par-dessus tout." if self.cfg["float_auto"]
            else "Lecteur flottant automatique désactivé (Ctrl+L l'ouvre à la "
                 "demande).", "info" if self.cfg["float_auto"] else "quiet")

    def _dress_float_button(self) -> None:
        """Allume, l'icone en clair ; eteint, grisee."""
        on = bool(self.cfg["float_auto"])
        # « ⧉ A » : le reglage automatique. Le ⧉ seul, dans le bandeau de la
        # fiche, ouvre le lecteur flottant tout de suite.
        self.float_button.setIcon(lettered("picture-in-picture-2",
                                           INK if on else DIM, "A"))
        self.float_button.setToolTip(
            "Lecteur flottant automatique : activé.\nQuand Prisme est réduit ou "
            "recouvert, la vidéo continue par-dessus tout.\nCliquer pour le "
            "désactiver." if on else
            "Lecteur flottant automatique : désactivé.\nLa vidéo reste dans "
            "Prisme (Ctrl+L l'ouvre quand même à la demande).\nCliquer pour "
            "l'activer.")

    def _float_key(self, event) -> None:
        """Une touche pressee dans le lecteur flottant : les destinations, les
        notes, Suppr, Espace… font ici ce qu'elles font dans Prisme."""
        self.keyPressEvent(event)

    # ------------------------------------------------------------------
    # L'accueil, et le choix entre videos et photos
    # ------------------------------------------------------------------
    # Ce qui, dans le menu ⋯, ne parle que de videos : cache en mode photo.
    # Ce que le menu ⋯ ne montre plus : les doublons se cherchent d'une seule
    # facon (les empreintes), la collection se tient toute seule.
    HIDDEN_MENU = (
        "Chercher les doublons (même taille)",
        "Chercher les doublons (même image)",
        "Empreintes : sonder ce qui manque",
        "Doublons d'après les empreintes",
        "État de la collection…",
        "Compter les vidéos",
        "État des vignettes",
        "Préparer toutes les vignettes",
        "Analyser les titres des métadonnées",
    )

    VIDEO_ONLY_MENU = (
        "Analyser les titres des métadonnées",
        "Repérer les plans (vignettes plus parlantes)",
        "Empreintes : sonder ce qui manque",
        "Doublons d'après les empreintes",
        "Recherche vidéo sur le web…",
        "Lecteur flottant automatique",
    )

    SESSION_FILTERS = (("filter_include", ""), ("filter_exclude", ""),
                       ("only_unseen", False), ("orientations", []),
                       ("folder_min", 0), ("folder_max", 0),
                       ("sort_mode", "random"), ("tab", TAB_FOLDERS))

    @classmethod
    def _reset_filters(cls, cfg, kind: str) -> None:
        """Les filtres d'une collection repartent de zero."""
        for key, value in cls.SESSION_FILTERS:
            cfg.set_of_kind(kind, key, value)

    # L'index des videos tel qu'on l'a trouve : c'est lui qu'on retrouve en
    # revenant des photos (un bac a sable, un cache portable l'ont peut-etre
    # place ailleurs que le chemin par defaut).
    _video_index = None

    def _enter_kind(self, kind: str):
        """Pose le type de media et ouvre son index ; rend le fichier de ses
        favoris."""
        set_media_kind(kind)
        photo_index, _ = kind_paths(KIND_PHOTO)
        index_path, ratings_path = kind_paths(media_kind())
        here = Path(INDEX.path)
        if media_kind() == KIND_PHOTO:
            if here != Path(photo_index):
                MainWindow._video_index = here
                INDEX.reopen(photo_index, migrate=False)
        elif here == Path(photo_index):
            INDEX.reopen(MainWindow._video_index or index_path, migrate=False)
        return ratings_path

    def _apply_kind_ui(self) -> None:
        """Ce qui change a l'ecran entre videos et photos."""
        photo = photo_mode()
        self.tabs.buttons[TAB_VIDEOS].setText("Photos" if photo else "Vidéos")
        self.tabs.buttons[TAB_VIDEOS].setToolTip(
            "Toutes les photos en vrac, au hasard" if photo
            else "Toutes les vidéos en vrac, au hasard")
        self.tabs.buttons[TAB_TAGS].setToolTip(
            "Les photos réunies par les mots de leurs noms" if photo
            else "Les vidéos réunies par les mots de leurs noms")
        self.tabs.buttons[TAB_SPLIT].setToolTip(
            "Plusieurs photos à la fois, chacune en diaporama" if photo
            else "Plusieurs vidéos à la fois")
        self.kind_switch.set_value(media_kind())
        self.welcome.set_kind(media_kind())
        # Ni duree ni taille a trier pour des photos.
        self.controls.set_photo(photo)
        if photo and (self.cfg["sort_mode"] or "").startswith(("duration", "size")):
            self.cfg["sort_mode"] = "random"
            self.controls.set_sort("random")
        # Une photo n'a pas d'instants a parcourir : pas de pellicule.
        if photo:
            self.single.hide_strip()
        else:
            self.single.show_strip()
        self.wall.set_photo(photo, float(self.cfg["slideshow_seconds"] or 6))
        # La duree du diaporama n'a de sens que pour des photos.
        self.single_bar.speed.setVisible(photo)
        for text in self.VIDEO_ONLY_MENU:
            action = self._menu_by_text.get(text)
            if action is not None:
                action.setVisible(not photo and text not in self.HIDDEN_MENU)
        self.random_button.setToolTip(
            ("Une photo au hasard" if photo else "Une vidéo au hasard")
            + ", dans toute la collection — jamais deux fois la même avant "
              "que toutes soient sorties   (Ctrl+H)")
        self.setWindowTitle(f"{APP_NAME} — Photos" if photo else APP_NAME)

    # -- le diaporama des photos --------------------------------------------
    def _photo_sheet(self) -> bool:
        """Une photo est-elle a l'ecran, dans sa fiche ?"""
        item = self.current
        return (self.stack.currentIndex() == PAGE_SORT and not self.browsing
                and self.viewer.currentWidget() is self.single
                and item is not None and item.kind != MODE_FOLDERS
                and bool(self.single.still_path))

    def toggle_slideshow(self) -> None:
        if self.slideshow_timer.isActive():
            self.slideshow_stop()
            self.show_banner("Diaporama en pause.", "quiet")
        elif self._photo_sheet():
            self.slideshow_timer.start()
            self.single.slideshow_on = True
            self.show_banner(
                f"Diaporama : une photo toutes les {self.cfg['slideshow_seconds']} s"
                + (", dans ce dossier" if self._stays_in_folder() else "")
                + ". ⏯, Entrée ou un clic l'arrête.", "info")
        self._wake_watch()

    def edit_slideshow_seconds(self) -> None:
        """Combien de temps chaque photo reste, au diaporama comme au mur."""
        seconds, ok = QInputDialog.getInt(
            self, "Durée du diaporama",
            "Secondes par photo (diaporama et mur) :",
            int(self.cfg["slideshow_seconds"] or 6), 1, 120, 1)
        self.setFocus()
        if not ok:
            return
        self._set_slideshow_seconds(seconds)
        self.show_banner(f"Diaporama : une photo toutes les {seconds} s.", "info")

    def turn_slideshow_seconds(self, step: int) -> None:
        """La molette sur « 6 s » : une seconde de plus, ou de moins."""
        seconds = int(self.cfg["slideshow_seconds"] or 6) + int(step)
        self._set_slideshow_seconds(max(1, min(120, seconds)))

    def _set_slideshow_seconds(self, seconds: int) -> None:
        self.cfg["slideshow_seconds"] = seconds
        self.cfg["slideshow_chosen"] = True
        self.cfg.save_soon()
        running = self.slideshow_timer.isActive()
        self.slideshow_timer.setInterval(seconds * 1000)
        if running:
            # La photo en cours repart pour la nouvelle duree.
            self.slideshow_timer.start()
        self.wall.set_slideshow_seconds(float(seconds))
        self.single_bar.speed.set_seconds(seconds)

    def slideshow_stop(self) -> None:
        self.slideshow_timer.stop()
        self.single.slideshow_on = False

    def _slideshow_next(self) -> None:
        """La photo suivante -- du meme dossier si la case est cochee."""
        if self._quiet:
            return
        if not self._photo_sheet():
            return self.slideshow_stop()
        if self._stays_in_folder():
            return self.step(1)
        if self.index + 1 < len(self.items):
            self.show_item(self.index + 1)
        else:
            self.slideshow_stop()
            self.show_banner("Fin du diaporama : dernière photo de la liste.", "quiet")

    def pick_kind(self, kind: str) -> None:
        """Vidéos | Photos : on passe a l'autre collection, a sa derniere
        racine, sur « Dossiers ». Sans racine encore, on la demande."""
        if kind == media_kind():
            self.kind_switch.set_value(kind)
            return
        if not self.switch_kind(kind):
            self.kind_switch.set_value(media_kind())
            return
        root = self.cfg["root"]
        if root:
            self.open_root_async(canon_root(root), MODE_FOLDERS)
        else:
            self._goto_page(PAGE_WELCOME)
            self.choose_root()

    def switch_kind(self, kind: str) -> bool:
        """Passe de la collection de videos a celle de photos, ou l'inverse.

        Tout ce qui tourne s'arrete, l'index et les favoris changent de
        fichier, et la fenetre oublie la collection quittee : elle repart de
        la derniere racine de l'autre, filtres a zero. Refuse pendant un
        transfert, qui doit finir chez lui.
        """
        if kind == media_kind():
            return True
        if self.transfers.busy:
            self.show_banner("Un transfert est en cours : attendez qu'il se "
                             "termine pour changer de collection.", "error")
            return False
        if self.floating.isVisible():
            self.floating.close_quietly()
        self.close_aside()
        self.stop_scan()
        self._stop_background()
        self.preview.stop_harvest()
        self._release_media()
        self.preview.quiesce(3000)
        self._wait_threads(3.0)
        for pane in self.wall.panes:
            pane.clear()
        self.ratings.flush()
        INDEX.commit(force=True)
        self.slideshow_stop()
        self.cfg["media_kind"] = kind
        ratings_path = self._enter_kind(kind)
        self.ratings.switch(ratings_path)
        # Des filtres d'une seance precedente masquaient la moitie des photos.
        self._reset_filters(self.cfg, kind)
        self.controls.set_unseen(False)
        self.controls.set_orientations(("vertical", "horizontal"))
        self.cfg.save_soon()
        self._reset_collection_state()
        self._apply_kind_ui()
        return True

    def _reset_collection_state(self) -> None:
        """Oublie la collection quittee : listes, apercus, et tout ce qui en
        etait tire. La suivante repart de sa derniere racine."""
        self.root = None
        self.origin = Path(self.cfg["root"]) if self.cfg["root"] else None
        self.tags = list(self.cfg["tags"] or [])
        self.all_items, self.items, self.index = [], [], 0
        self._plain_items, self._plain_root, self._plain_whole = [], None, False
        self.levels, self.visited, self.history = [], [], []
        self.plans, self.pages, self.peek_thumbs = {}, {}, {}
        self._flat_cache = {}
        self._siblings_cache = {}
        self._siblings_gen = -1
        self._collection_gen += 1
        self._rebuild_gen += 1
        self._sortable_memo = {}
        self._collection_src = None
        self._videos_memo = None
        self._pinned_memo = None
        self._pinned_job = None
        self._flat_list = None
        self._flat_job = None
        self._mine_tags = None
        self._mine_key = None
        self._top_tags = None
        self._tags_key = None
        self._pool_memo = None
        self._wall_batch = []
        self._random_memo = None
        self._random_deck = Deck()
        self._here_deck = Deck()
        self._harvest_key = None
        self._harvest_src = None
        self._harvest_complete = None
        self._harvest_token += 1
        self._tab_sorts = {}
        self.criteria = {}
        self._narrow_items = None
        self._narrow_source = None
        self._narrow_context = None
        self._narrow_query = ""
        self._transient = ""
        self._dupe_members = {}
        self._back_to_wall = None
        self._wall_return = None
        self._restore_id = ""
        self._pending_start = None
        self._commands_signature = None
        self._photo_tried = set()
        if self.tab == TAB_SPLIT:
            self.tab = TAB_FOLDERS
            self.tabs.set_value(TAB_FOLDERS)
        self.board.set_items([], self.ratings.get)
        self.welcome.set_recent(self.cfg["recent_roots"])
        self._refresh_state()

    def aside_fullscreen(self) -> None:
        """Donne tout l'ecran a la video ouverte a cote, et sait en revenir.

        La video qui joue, a l'instant ou elle en est -- et non la carte qui
        se trouvait a la position retenue : apres un filtre, un tri ou une
        voisine du dossier, ⛶ en montrait une autre, depuis son debut.
        """
        video = self.aside_current
        if not video:
            return
        start = max(0.0, self.aside_player.player.position() / 1000.0)
        position = -1 if self.aside_playlist else self.aside_index
        item = self.items[position] if position >= 0 else None
        self.close_aside()
        if (item is not None and item.kind != MODE_FOLDERS
                and os.path.normcase(str(item.path)) == os.path.normcase(video)):
            # Sa carte est dans la liste : sa fiche, dans cette liste. On
            # retient d'ou l'on vient : sortir du plein ecran laissait sur la
            # fiche, au lieu de rendre la planche qu'on parcourait.
            self._back_to_board = self.browsing
            self._pending_start = (
                (os.path.normcase(video), int(start * 1000), time.monotonic())
                if start > 0 else None)
            self.on_board_open(position)
        else:
            # Une playlist, un dossier, une voisine : la video elle-meme.
            self.play_in_app(video, start)
        self.toggle_cinema(True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.aside.isHidden():
            self._place_aside_bar()
        if hasattr(self, "_fit_timer"):
            self._fit_timer.start()

    # ------------------------------------------------------------------
    # La premiere ligne sur un petit ecran
    # ------------------------------------------------------------------
    # Ce qui cede sa place quand la ligne ne tient plus, du premier sacrifie
    # au dernier : les boutons ronds (retrouves en tete du menu ⋯), puis les
    # onglets passent en icones, et la lune en tout dernier. Le fil d'Ariane,
    # lui, s'abrege de lui-meme jusqu'a `CRUMBS_ROOM`.
    CRUMBS_ROOM = 160
    FOLD_LABELS = {
        "labo_button": "Labo IA",
        "web_button": "Recherche vidéo sur le web",
        "float_button": "Lecteur flottant automatique",
        "tree_button": "Panneau des dossiers   (Ctrl+T)",
        "random_button": "Une vidéo au hasard   (Ctrl+H)",
        "mute_button": "Couper ou remettre le son",
        "quiet_button": "S'effacer   (Ctrl+K)",
    }
    FOLD_ORDER = ("labo_button", "web_button", "float_button", "tree_button",
                  "random_button", "mute_button", "tabs", "quiet_button")

    def _row_need(self, widget) -> int:
        """La place qu'un element de la premiere ligne ne peut pas ceder."""
        if widget is self.crumbs:
            return self.CRUMBS_ROOM
        if widget is self.tabs:
            return self._tabs_full or widget.sizeHint().width()
        if widget.minimumWidth() == widget.maximumWidth():
            return widget.minimumWidth()
        if widget.sizePolicy().horizontalPolicy() == QSizePolicy.Ignored:
            return widget.minimumWidth()
        return max(widget.minimumSizeHint().width(), widget.minimumWidth())

    def _fit_top_row(self) -> None:
        """Replie ce qu'il faut pour que la premiere ligne tienne en entier :
        sans cela, sur un 13 pouces ou une demi-fenetre, les onglets se
        rognaient (« Dossier », « lots-clé ») et le chemin passait dessous."""
        if not hasattr(self, "_folded") or not self.top_bar.isVisible():
            return
        room = self.top_bar.width()
        if room <= 0:
            return
        if not self.tabs.compact:
            self._tabs_full = self.tabs.sizeHint().width()
        row = self._row_one
        folded = set(self._folded)
        spacing = row.spacing()
        total, count = 0, 0
        for index in range(row.count()):
            item = row.itemAt(index)
            widget = item.widget()
            if widget is not None:
                name = next((n for n in self.FOLD_ORDER
                             if n != "tabs" and getattr(self, n) is widget), None)
                if widget.isHidden() and name not in folded:
                    continue
                total += self._row_need(widget)
                count += 1
            elif item.layout() is not None:
                width = item.layout().minimumSize().width()
                if width > 0:
                    total += width
                    count += 1
        total += spacing * max(0, count - 1)
        fold = []
        for name in self.FOLD_ORDER:
            if total <= room:
                break
            fold.append(name)
            if name == "tabs":
                total -= max(0, self._tabs_full - self.tabs.compact_width())
            else:
                total -= self._row_need(getattr(self, name)) + spacing
        if fold == self._folded:
            return
        self._folded = fold
        self.tabs.set_compact("tabs" in fold)
        for name in self.FOLD_ORDER:
            if name != "tabs":
                getattr(self, name).setVisible(name not in fold)

    def _show_folded_in_menu(self) -> None:
        """En tete du menu ⋯ : les boutons que la premiere ligne a replies."""
        for action in self._folded_actions:
            self.overflow.removeAction(action)
            action.deleteLater()
        self._folded_actions = []
        names = [n for n in self.FOLD_ORDER if n in self._folded and n != "tabs"]
        if not names:
            return
        first = self.overflow.actions()[0] if self.overflow.actions() else None
        # Dans l'ordre de la ligne, de gauche a droite, comme on les voyait.
        names.sort(key=lambda n: self._row_one.indexOf(getattr(self, n)))
        for name in names:
            button = getattr(self, name)
            action = QAction(button.icon(), self.FOLD_LABELS[name], self.overflow)
            action.triggered.connect(lambda _c=False, b=button: b.click())
            self.overflow.insertAction(first, action)
            self._folded_actions.append(action)
        line = QAction(self.overflow)
        line.setSeparator(True)
        self.overflow.insertAction(first, line)
        self._folded_actions.append(line)

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
        here = self.aside_index
        if here < 0:
            # Sa carte n'est plus dans la liste affichee : on ne saute pas au
            # hasard dans une autre.
            return
        target = here + step
        while 0 <= target < len(self.items) and not self.items[target].videos:
            target += step
        if 0 <= target < len(self.items):
            self.open_aside(target)

    def close_aside(self) -> None:
        self._aside_similar = None
        self._tick_similar(self.aside_bar, False)
        self.aside_playlist = []
        self.aside_current = ""
        self.aside_player.stop()
        self.aside_bar.hide()
        self.aside.hide()
        self.aside_item_id = ""
        self._aside_hint = -1
        self.setFocus()

    def toggle_full_view(self) -> None:
        """Ctrl+J, F11, Alt+Entree : le mur seul sur le mur, l'image seule ailleurs."""
        if self.tab == TAB_SPLIT:
            return self.toggle_wall_fullscreen()
        self.toggle_cinema()

    def toggle_cinema(self, on: bool | None = None) -> None:
        """Ne laisse que l'image, sur tout l'ecran : le reste s'efface.

        Le cinema restait dans la fenetre, bords et barre de titre compris :
        ce n'etait pas le plein ecran que F11, Alt+Entree ou un double-clic
        promettent dans tous les lecteurs. L'etat de la fenetre (agrandie ou
        non) est rendu a la sortie, comme pour le mur.
        """
        was = self.cinema
        self.cinema = (not self.cinema) if on is None else bool(on)
        self.cinema_button.setChecked(self.cinema)
        # L'image seule. On en sort par le bandeau qui parait au survol de
        # l'image (⛶), par Ctrl+J, F11, un double-clic ou Échap.
        for widget in (self.top_bar, self.bottom_bar):
            widget.setVisible(not self.cinema)
        if self.cinema and not was:
            self._cancel_rename()
            # La video ouverte a cote s'efface elle aussi, et se tait : le
            # plein ecran promis gardait une seconde video qui jouait a droite.
            # Elle revient telle quelle a la sortie.
            aside = self.aside_player.player
            self._cinema_aside = (
                (aside.playbackState() == aside.PlaybackState.PlayingState)
                if not self.aside.isHidden() and self.aside_current else None)
            if self._cinema_aside is not None:
                if self._cinema_aside:
                    aside.pause()
                self.aside_bar.hide()
                self.aside.hide()
            if not self.wall_full and not self._quiet:
                self._cinema_kept = self.windowState()
                self.showFullScreen()
                self.activateWindow()
        elif was and not self.cinema:
            kept, self._cinema_kept = self._cinema_kept, None
            if kept is not None and not self.wall_full and not self._quiet:
                self.setWindowState(kept)
            playing, self._cinema_aside = getattr(self, "_cinema_aside", None), None
            if playing is not None and self.aside_current and self.tab != TAB_SPLIT:
                self.aside.show()
                if playing:
                    self.aside_player.player.play()
        if not self.cinema and getattr(self, "_back_to_board", False):
            self._back_to_board = False
            self.show_board_at(self.index)
        # La pellicule : sur l'image et au survol en plein ecran, dans sa
        # colonne sinon.
        self.single.float_strip(self.cinema)
        self._cine_moved = time.monotonic()
        if self.cinema:
            self.tree.hide()
        else:
            self._apply_selectors()
        self.setFocus()

    def eventFilter(self, watched, event):
        if watched is getattr(self, "mute_button", None) and event.type() == QEvent.Wheel:
            notches = event.angleDelta().y() / 120.0
            if notches:
                self.set_volume(int(self.cfg["volume"] or 0) + int(round(notches * 5)))
            return True
        if (watched is getattr(self, "activity_label", None)
                and event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton):
            self._activity_menu()
            return True
        if (watched is getattr(self, "banner", None)
                and event.type() == QEvent.MouseButtonRelease):
            action, self._banner_action = self._banner_action, None
            if action is not None:
                # Lue : ce qui attendait derriere l'alerte peut passer.
                self._alarm_until = 0.0
                self.banner.hide()
                action()
                return True
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
        if self._stays_in_folder():
            # Comme → : la case vaut aussi pour la suite qui vient toute seule.
            return self.step(1)
        if self.index + 1 < len(self.items):
            self.show_item(self.index + 1)
        else:
            self.show_banner("Dernière vidéo de la liste", "quiet")

    def on_picked_changed(self, count: int) -> None:
        # Tant que des elements sont coches, leurs actions prennent la place
        # des filtres : deux lignes, toujours. « Annuler » rend les filtres.
        self.picked_bar.setVisible(bool(count))
        self.not_dupes_button.setVisible(self._transient == "dupes")
        if self.browsing and self.tab != TAB_SPLIT:
            self.controls.setVisible(not count)
        self.picked_label.setText(
            f"{count} élément(s) coché(s)" if count else "")
        self._show_picked_pages()
        # Le titre de l'arborescence dit ce que fera le prochain clic :
        # envoyer les coches, ou y aller.
        self.tree.set_context(self.browsing and self.tab != TAB_SPLIT, count)

    def _show_picked_pages(self) -> None:
        """La page ou l'on en est, sur la barre des coches, s'il y en a plusieurs."""
        board = self.board
        many = (bool(board.picked_ids) and self.browsing
                and self.tab != TAB_SPLIT and board.total_pages() > 1)
        for widget in (self.picked_previous, self.picked_count, self.picked_next):
            widget.setVisible(many)
        if many:
            first, last = board._page_bounds()
            self.picked_count.setText(
                f"{first + 1}–{last} sur {self._thousands(len(board.items))}")
            self.picked_previous.setEnabled(board.page > 0)
            self.picked_next.setEnabled(board.page < board.total_pages() - 1)

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
            # Le panneau sous la souris d'abord, comme pour les touches de tri.
            hovered = self._wall_hovered()
            if hovered is not None:
                return Path(hovered[1])
            panes = self.wall.panes
            at = self.wall.solo if self.wall.solo != -1 else 0
            ordered = ([panes[at]] if at < len(panes) else []) + panes
            for pane in ordered:
                if pane.video_path:
                    return Path(pane.video_path)
        if self.browsing:
            # Sur la planche, la vignette survolee : la fiche « courante » n'y
            # est pas a l'ecran.
            position = self._board_hovered()
            if position >= 0:
                return Path(self.board.items[position].path)
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

    def reveal_path(self, path: str) -> None:
        """Ouvre l'explorateur sur ce fichier precis, deja selectionne — le
        ⌸ d'un panneau du mur, qui sait quelle video il montre."""
        target = Path(path)
        state = actions.probe(target)
        if state != "ok":
            return self.show_banner(
                f"Introuvable : {target.name}" if state == "absent" else
                f"NAS injoignable : « {target.name} »", "error")
        try:
            subprocess.Popen(self.reveal_command(target))
        except OSError as exc:
            self.show_banner(f"Explorateur indisponible : {exc}", "error")

    def reveal_current(self) -> None:
        """Ouvre l'explorateur sur l'element courant, deja selectionne."""
        target = self.reveal_target()
        if target is None:
            return
        # Une question au NAS, pas trois : ce qu'est l'element (dossier ou
        # fichier), la liste le sait deja.
        state = actions.probe(target)
        if state != "ok":
            return self.show_banner(
                f"Introuvable : {target.name}" if state == "absent" else
                f"NAS injoignable : « {target.name} »", "error")
        try:
            item = self.current
            if self.browsing:
                # La vignette survolee, c'est elle que vise Ctrl+E ici.
                position = self._board_hovered()
                if position >= 0:
                    item = self.board.items[position]
            if (item is not None and self.tab != TAB_SPLIT
                    and item.kind == MODE_FOLDERS and Path(item.path) == target):
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
        self.board.clear_picked()
        self.wall_videos(videos)

    def wall_from_folder(self) -> None:
        """Le mur, lance depuis un dossier : ses videos dans les panneaux,
        chacun coche « rester dans ce dossier ». On en decoche un : il part
        au hasard dans toute la collection, les autres restent ici."""
        import random
        folder = self._starrable_folder()
        videos = [str(video) for item in self.items if not item.is_tag
                  for video in item.videos]
        if folder is None or not videos:
            return self.show_banner("Entrez d'abord dans un dossier qui a des vidéos.",
                                    "quiet")
        count = max(2, len(self.wall.panes) or 4)
        first = random.sample(videos, min(count, len(videos)))
        self._wall_pinned = []
        self._wall_return = first
        if len(self.wall.panes) > len(first):
            self.wall.set_pane_count(max(2, len(first)))
        self.set_tab(TAB_SPLIT)
        if self.viewer.currentWidget() is not self.wall:
            self.show_wall()
        for pane in self.wall.panes:
            pane.set_stay(True)
        self.show_banner(f"Le mur avec les vidéos de « {Path(folder).name} » : chaque "
                         "panneau reste dans le dossier. Décochez sa case (dossier "
                         "doré) pour l'envoyer ailleurs.", "info", seconds=8)

    def wall_videos(self, videos: list) -> None:
        """Ces videos sur le mur (une collection du labo, une selection)."""
        videos = [str(video) for video in videos]
        if not videos:
            return
        self._wall_pinned = videos
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
        self.playlist_videos(videos)

    def playlist_videos(self, videos: list) -> None:
        """Ces videos l'une apres l'autre, dans le lecteur de droite."""
        videos = [str(video) for video in videos]
        if not videos:
            return
        self.aside_playlist = videos
        self.aside_playlist_at = 0
        # Une playlist ne doit rien a une carte de la planche.
        self.aside_item_id = ""
        self._aside_play_list()

    def _aside_play_list(self) -> None:
        video = self.aside_playlist[self.aside_playlist_at]
        count = len(self.aside_playlist)
        self._aside_play(video, f"{self.aside_playlist_at + 1}/{count} · "
                                f"{Path(video).name}")

    def move_picked_hint(self) -> None:
        """Le deplacement groupe passe par l'arborescence : on l'ouvre."""
        if not self.tree.isVisible():
            self.toggle_tree(True)
        self.tree.set_action("send")
        self.show_banner(
            "Cliquez le dossier de destination dans l'arborescence.", "info")

    def delete_picked(self) -> None:
        """Ecarte les elements coches, sans quitter la planche."""
        self._delete_items(list(self.board.picked_items()))

    def move_picked(self, dest: dict) -> int:
        """Envoie tous les elements coches vers cette destination.

        Rend le nombre d'envois reellement partis.
        """
        return self._move_items(list(self.board.picked_items()), dest)

    def _delete_items(self, items: list) -> int:
        """Ecarte ces elements dans la corbeille de session, sans avancer.

        Chaque element passait par la touche Suppr : une fiche s'ouvrait pour
        chacun, la planche disparaissait, et l'on jugeait deux cents fiches
        pour rien. Ici, tout part d'un bloc, sans rien ouvrir, et un seul
        bandeau dit ce qui est parti. Au-dela d'un element, une seule
        question, qui dit ce que l'on s'apprete a ecarter : Ctrl+A puis Suppr
        videait toute la liste sans un mot.
        """
        picked = bool(self.board.picked_ids) and any(
            item.item_id in self.board.picked_ids for item in items)
        items = [item for item in items if not item.locked]
        if not items:
            if picked:
                self.board.clear_picked()
            self.show_banner("Rien à écarter : c'est déjà parti.", "quiet")
            return 0
        # Mots-cles et dossiers de tete ne s'ecartent pas : ecartes d'emblee,
        # ils ne gonflent pas les chiffres de la question.
        fixed = [item for item in items if not item.movable
                 or Path(item.path).name.startswith(PARENT_PREFIX)]
        if fixed and len(fixed) < len(items):
            gone = {id(item) for item in fixed}
            items = [item for item in items if id(item) not in gone]
        else:
            fixed = []          # tous tels : chacun dira son refus
        items, spared = self._spare_last_copies(items)
        if not items:
            self.show_banner(
                "C'est le dernier exemplaire de ce groupe de doublons : il reste.",
                "quiet")
            return 0
        if len(items) == 1 and not spared:
            item = items[0]
            state, why = self._submit_delete(item)
            if state == "sent":
                self._settle_board(item)
                self._show_deleted(self._deleted_text(item))
                if picked:
                    self.board.clear_picked()
                return 1
            if why:
                self.show_banner(why, "error")
            return 0
        if not self._confirm_many_delete(items, spared):
            return 0
        # Un seul aller-retour vers le partage pour tout le lot : cent etats
        # demandes un par un, c'etait cent attentes sur le fil de l'interface.
        # Ce qui aurait disparu entre-temps echouera proprement en fond.
        if actions.probe(items[0].path) == "injoignable":
            self.show_banner("NAS injoignable : rien n'a été écarté. Réessayez "
                             "dans un instant.", "error")
            return 0
        sent, refused = 0, [f"« {item.name} » ne s'écarte pas d'ici"
                            for item in fixed]
        for item in items:
            state, why = self._submit_delete(item, ask=False, disk=False)
            if state == "sent":
                sent += 1
                self._settle_board(item)
            else:
                refused.append(why or item.name)
        if picked:
            self.board.clear_picked()
        self.show_banner(self._batch_text(sent, refused, "écarté(s)")
                         + " · Ctrl+B pour les reprendre", "error")
        return sent

    def _move_items(self, items: list, dest: dict) -> int:
        """Envoie ces elements vers une destination, sans avancer.

        Meme principe que `_delete_items` : aucune fiche ouverte, un bandeau
        pour le lot. Le bandeau annoncait « 0 element envoye » : il comptait
        les transferts deja finis, et non ceux qu'on venait de lancer.
        """
        picked = bool(self.board.picked_ids) and any(
            item.item_id in self.board.picked_ids for item in items)
        items = [item for item in items if not item.locked]
        dest_dir = Path(dest["path"])
        label = dest.get("label") or dest_dir.name
        if not items:
            if picked:
                self.board.clear_picked()
            self.show_banner("Rien à envoyer : c'est déjà parti.", "quiet")
            return 0
        if len(items) == 1:
            item = items[0]
            state, why = self._submit_move(item, dest)
            if state == "sent":
                self._settle_board(item)
                self.show_banner(f"« {item.name} » → {label}", "done")
                if picked:
                    self.board.clear_picked()
                return 1
            if why:
                self.show_banner(why, "error")
            return 0
        if actions.probe(items[0].path) == "injoignable":
            self.show_banner("NAS injoignable : rien n'a été déplacé. Réessayez "
                             "dans un instant.", "error")
            return 0
        sent, refused = 0, []
        for item in items:
            state, why = self._submit_move(item, dest, disk=False)
            if state == "sent":
                sent += 1
                self._settle_board(item)
            else:
                refused.append(why or item.name)
        if picked:
            self.board.clear_picked()
        self.show_banner(self._batch_text(sent, refused, "envoyé(s)")
                         + f" vers « {label} »", "done" if sent else "error")
        return sent

    def _rate_items(self, items: list, stars: int) -> None:
        """Favori, ou non, pour ces elements — sans ouvrir aucune fiche."""
        wanted = 1 if int(stars or 0) > 0 else 0
        tags = any(item.is_tag for item in items)
        items = [item for item in items if not item.is_tag]
        if not items:
            if tags:
                # 1 a 5 sur une carte « # mot » ne faisaient rien, sans un mot.
                self.show_banner("Un mot-clé ne se met pas en favori : ouvrez-le "
                                 "pour noter ses vidéos.", "quiet")
            return
        for item in items:
            if (self.ratings.get(item.path) > 0) != bool(wanted):
                # set() bascule : rappeler la meme valeur l'effacerait.
                self.ratings.set(item.path, wanted)
            position = self._board_position_of(f"board@{item.item_id}")
            if position >= 0:
                self.board.set_stars(position, wanted)
        if len(items) == 1:
            name = items[0].name
            self.show_banner(f"★ « {name} » en favori" if wanted
                             else f"« {name} » retiré des favoris", "quiet")
        else:
            self.show_banner(f"★ {len(items)} élément(s) en favori" if wanted
                             else f"{len(items)} élément(s) retiré(s) des favoris",
                             "quiet")

    def _rate_paths(self, paths: list, stars: int) -> None:
        """Favori, ou non, pour des videos du mur, designees par leur chemin."""
        wanted = 1 if int(stars or 0) > 0 else 0
        for path in paths:
            if (self.ratings.get(path) > 0) != bool(wanted):
                self.ratings.set(path, wanted)
            self.wall.set_favorite(path, bool(wanted))
        name = Path(paths[0]).name if paths else ""
        self.show_banner(f"★ « {name} » en favori" if wanted
                         else f"« {name} » retiré des favoris", "quiet")

    @staticmethod
    def _batch_text(sent: int, refused: list, verb: str) -> str:
        """« 12 element(s) ecarte(s) · 2 refuse(s) : raison », en une ligne."""
        text = f"{sent} élément(s) {verb}"
        if refused:
            text += f" · {len(refused)} refusé(s) : {refused[0]}"
            if len(refused) > 1:
                text += "…"
        return text

    def _settle_board(self, item) -> None:
        """La carte de cet element prend son nouvel etat, si elle est a l'ecran.

        Cherchee sur la seule page affichee : parcourir toute la liste a
        chaque transfert termine coutait plus que le transfert lui-meme.
        """
        if item is None or not self.browsing:
            return
        position = self._board_position_of(f"board@{item.item_id}")
        if position >= 0:
            self.board.set_state(position, item.status)

    def _spare_last_copies(self, items: list) -> tuple:
        """Dans la vue des doublons, un groupe ne part jamais en entier.

        Cocher toute la liste et supprimer emportait l'original avec ses
        copies. Si tout ce qui reste d'un groupe est vise, on garde son
        meilleur exemplaire (le premier du groupe : definition, taille, duree).
        Rend (elements a ecarter, nombre de groupes epargnes).
        """
        groups = {getattr(item, "dupe_group", None) for item in items}
        groups.discard(None)
        if not groups:
            return items, 0
        wanted = {id(item) for item in items}
        # Un exemplaire deja a la corbeille, ou en route, n'est plus la pour
        # garder le groupe -- meme si sa carte, faite avant, ne le sait pas.
        gone = self._gone_paths()
        members: dict = {}
        for group in groups:
            known = self._dupe_members.get(group)
            if known is None:
                # Un groupe que la table ne connait plus (une liste d'avant) :
                # dans le doute, un exemplaire vise reste.
                members[group] = [item for item in items
                                  if getattr(item, "dupe_group", None) == group]
                continue
            for item in known:
                if (not item.locked
                        and not _within(os.path.normcase(str(item.path)), gone)):
                    members.setdefault(group, []).append(item)
        keep = set()
        for group, present in members.items():
            if present and all(id(item) in wanted for item in present):
                keep.add(id(present[0]))
        kept = [item for item in items if id(item) not in keep]
        return kept, len(keep)

    def _confirm_many_delete(self, items: list, spared: int = 0) -> bool:
        """Une seule question pour tout un lot, avec « Non » par defaut.

        Tout ce qu'elle dit est deja en memoire : aucun acces au disque.
        """
        folders = [item for item in items if item.kind == MODE_FOLDERS]
        videos = sum(max(0, item.video_count) for item in items)
        others = sum(max(0, item.file_count - item.video_count)
                     for item in folders)
        unknown = sum(1 for item in folders
                      if item.file_count < 0 or item.incomplete or item.unreadable)
        size = sum(max(0, item.size) for item in items)
        lines = [f"{len(items)} élément(s)"
                 + (f", dont {len(folders)} dossier(s)" if folders else "")
                 + f" : {videos} vidéo(s)"
                 + (f" et {others} autre(s) fichier(s) — documents, images…"
                    if others else "")
                 + (f", {human_size(size)}" if size else "") + "."]
        if unknown:
            lines.append(f"{unknown} dossier(s) n'ont pas pu être comptés en "
                         "entier : ils contiennent peut-être autre chose que "
                         "des vidéos.")
        if spared:
            lines.append(f"Le meilleur exemplaire de {spared} groupe(s) de "
                         "doublons est gardé : un groupe ne part jamais en entier.")
        lines.append(self._fate_text(items[0].path, plural=True))
        answer = QMessageBox.question(
            self, "Écarter la sélection ?",
            "\n\n".join(lines) + "\n\nContinuer ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def on_board_click(self, position: int) -> None:
        """Un clic sur une carte. Trois etages, toujours les memes : le
        panorama (tous les dossiers, toutes les videos), l'interieur d'un
        dossier ou d'un mot-cle en vignettes, la fiche d'une video. Un
        dossier s'ouvre donc comme dans l'explorateur, ses videos en
        vignettes : sa fiche -- dix apercus et une ligne d'informations --
        montrait moins, et moins bien. Les touches de tri visent toujours la
        carte survolee."""
        if not (0 <= position < len(self.items)):
            return
        item = self.items[position]
        if (self.tab != TAB_SPLIT and item.kind == MODE_FOLDERS
                and not item.locked):
            self.index = position
            return self.enter_current()
        self.on_board_open(position)

    def on_board_open(self, position: int) -> None:
        """Un clic sur une vignette descend a l'etage du dessous : sa fiche.

        Le meme geste partout, quel que soit l'onglet. On y note, on y range, on
        y passe — et chaque decision avance a l'element suivant de la meme liste.
        `Echap` remonte aux vignettes, a l'endroit qu'on avait quitte.
        """
        if not (0 <= position < len(self.items)):
            return
        if not self._random_launching:
            self._random_on = False
        if self.tab == TAB_SPLIT:
            return self.open_video_path(str(self.items[position].path))
        self.index = position
        item = self.items[position]
        if (photo_mode() and item.kind == MODE_FOLDERS and not item.locked):
            # Un dossier de photos s'ouvre comme dans l'explorateur : ses
            # sous-dossiers et ses photos en vignettes. Sa fiche n'en montrait
            # que quelques-unes, en grand, et l'on n'y voyait « que des photos ».
            return self.enter_current()
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

    # -- l'epingle -------------------------------------------------------------
    @staticmethod
    def _pin_key(path) -> str:
        return os.path.normcase(os.path.normpath(str(path)))

    def _pin_keys(self) -> set:
        return {self._pin_key(p) for p in (self.cfg["pins"] or [])}

    def _is_pinned(self, path) -> bool:
        return bool(path) and self._pin_key(path) in self._pin_keys()

    def _board_pinned(self, path) -> bool:
        """L'epingle d'une tuile : un mot-cle (chemin sans dossier) est-il dans
        la bande ? un dossier ou une video, en tete de sa liste ?"""
        if path and not Path(str(path)).is_absolute():
            return fold(Path(str(path)).name) in {
                fold(word) for word in self.cfg["word_pins"] or []}
        return self._is_pinned(path)

    def toggle_pin(self, item) -> None:
        """Epingle ou desepingle un dossier ou une video : l'element passe en
        tete de sa liste (ou reprend sa place), et la liste se reclasse. Un
        mot-cle, lui, va dans la bande des mots epingles."""
        if item is not None and item.is_tag:
            return self.toggle_word_pin(item)
        if item is None:
            return
        key = self._pin_key(item.path)
        pins = [p for p in (self.cfg["pins"] or []) if self._pin_key(p) != key]
        wanted = len(pins) == len(self.cfg["pins"] or [])
        if wanted:
            pins.insert(0, str(item.path))
        self.cfg["pins"] = pins
        self.cfg.save_soon()
        name = Path(str(item.path)).name
        self.show_banner(f"« {name} » épinglé : en tête de la liste" if wanted
                         else f"« {name} » désépinglé", "quiet")
        # Rien ne bouge sous les yeux : l'element garde sa place, sa carte
        # prend l'epingle. Il passera en tete au prochain affichage de la
        # liste (en revenant sur l'onglet) -- le voir disparaitre d'un coup
        # deroutait.
        for position, other in enumerate(self.board.items):
            if other is item:
                self.board.set_pinned(position, wanted)
                break
        if self.current is item:
            self.pin_button.set_value(wanted)

    # -- la bande des mots epingles (`wordstrip.py`) ---------------------------
    def toggle_word_pin(self, item) -> None:
        """L'epingle d'un mot-cle : dans la bande, ou hors d'elle."""
        word = Path(str(item.path)).name
        if word in (PINNED_UNREAD, PINNED_LOOSE):
            return self.show_banner("Cette liste ne s'épingle pas : ce n'est pas un mot.",
                                    "quiet")
        words = list(self.cfg["word_pins"] or [])
        kept = [w for w in words if fold(w) != fold(word)]
        wanted = len(kept) == len(words)
        if wanted:
            kept.append(word)
        self._set_word_pins(kept)
        if wanted and not self.cfg["word_pins_shown"]:
            self.toggle_word_strip(True)
        self.show_banner(f"« {word} » épinglé dans la bande du haut : un clic, et ses "
                         "vidéos s'affichent, de n'importe quel onglet." if wanted
                         else f"« {word} » retiré de la bande", "quiet", seconds=6)
        for position, other in enumerate(self.board.items):
            if other is item:
                self.board.set_pinned(position, wanted)
                break

    def _set_word_pins(self, words: list) -> None:
        self.cfg["word_pins"] = words
        self.cfg.save_soon()
        self.word_strip.set_words(words)
        self._show_word_strip()

    def unpin_word(self, word: str) -> None:
        self._set_word_pins([w for w in self.cfg["word_pins"] or [] if fold(w) != fold(word)])
        # La tuile du mot, si elle est a l'ecran, perd son epingle.
        for position, other in enumerate(self.board.items):
            if other.is_tag and fold(Path(str(other.path)).name) == fold(word):
                self.board.set_pinned(position, False)

    def move_word(self, word: str, step: int) -> None:
        words = list(self.cfg["word_pins"] or [])
        if word not in words:
            return
        index = words.index(word)
        target = max(0, min(len(words) - 1, index + step))
        words.insert(target, words.pop(index))
        self._set_word_pins(words)

    def toggle_word_strip(self, on=None) -> None:
        """Montre ou masque la bande (⋯ › Réglages, ou son clic droit)."""
        shown = (not self.cfg["word_pins_shown"]) if on is None else bool(on)
        self.cfg["word_pins_shown"] = shown
        self.cfg.save_soon()
        action = self._menu_by_text.get("Bande des mots épinglés")
        if action is not None:
            action.setChecked(shown)
        self._show_word_strip()
        if not shown:
            self.show_banner("Bande des mots épinglés masquée : ⋯ › Réglages pour la "
                             "remettre.", "quiet", seconds=6)

    def _show_word_strip(self) -> None:
        """Visible s'il y a des mots, qu'on la veut, et qu'on parcourt une planche
        (sur une fiche ou le mur, la place va a l'image)."""
        strip = getattr(self, "word_strip", None)
        if strip is None:
            return
        wall = (self.tab == TAB_SPLIT and getattr(self, "wall", None) is not None
                and self.viewer.currentWidget() is self.wall)
        strip.setVisible(bool(self.cfg["word_pins"]) and bool(self.cfg["word_pins_shown"])
                         and self.browsing and not wall)
        strip.set_current(getattr(self, "_list_leaf", "") or "")

    def _pin_more_words(self) -> None:
        """« + épingler » : l'onglet des mots-cles, ou chaque mot a son epingle."""
        if self.tab != TAB_TAGS:
            self.set_tab(TAB_TAGS)
        self.show_banner("Survolez un mot-clé et touchez son épingle : il rejoint la "
                         "bande du haut.", "info", seconds=8)

    def open_word(self, word: str) -> None:
        """Un mot de la bande : les videos qui le portent, comme un mot-cle,
        sans quitter l'onglet ou l'on est. Calcule par tranches : sur cent
        mille noms, la fenetre ne fige pas."""
        if self.root is None:
            return self.show_banner("Ouvrez d'abord une collection.", "quiet")
        videos = [video for item in self._collection() for video in item.videos]
        members = {k: v for k, v in (self.cfg["tag_members"] or {}).items()
                   if fold(k) == fold(word)}
        self._word_wanted = word
        old = getattr(self, "_word_slicer", None)
        if old is not None and shiboken6.isValid(old):
            old.stop()
            old.deleteLater()
        self.word_strip.set_current(word)
        self._word_slicer = self._in_slices(
            iter_tag_items([word], videos, 1, members),
            lambda found, w=word: self._word_ready(w, found))

    def _word_ready(self, word: str, found: list) -> None:
        if word != getattr(self, "_word_wanted", ""):
            return
        self._word_slicer = None
        videos = [video for item in found for video in item.videos]
        if not videos:
            self.word_strip.set_current(self._list_leaf or "")
            return self.show_banner(f"Aucune vidéo ne porte « {word} » pour l'instant.",
                                    "quiet")
        self.browse_videos(word, videos)
        self._show_word_strip()

    def pin_current(self) -> None:
        """L'epingle de la fiche."""
        self.toggle_pin(self.current)

    def on_board_pin(self, position: int) -> None:
        """L'epingle d'une vignette survolee."""
        items = self.board.items
        if 0 <= position < len(items):
            self.toggle_pin(items[position])

    def _is_favorite(self, path) -> bool:
        return self.ratings.get(path) > 0

    def _starrable_folder(self):
        """Le dossier ou l'on est entre, s'il en est un vrai : ni la racine
        (ou la reunion des racines), ni un mot-cle, ni une liste de passage."""
        from . import roots
        root = self.root
        if (root is None or not self.browsing or self.tab == TAB_SPLIT
                or self._transient or self._list_leaf or roots.is_union(root)):
            return None
        top = self.top_root()
        if top is None or os.path.normcase(str(top)) == os.path.normcase(str(root)):
            return None
        return root

    def _show_folder_star(self) -> None:
        folder = self._starrable_folder()
        self.folder_star.setVisible(folder is not None)
        self.folder_wall.setVisible(folder is not None
                                    and any(not item.is_tag for item in self.items))
        if folder is None:
            return
        self.folder_star.set_value(1 if self._is_favorite(folder) else 0)
        self.folder_star.setToolTip(
            f"« {Path(folder).name} » est dans vos favoris : cliquer pour l'en retirer"
            if self.folder_star.value else
            f"Mettre le dossier « {Path(folder).name} » en favori")

    def toggle_folder_favorite(self, _value: int = 0) -> None:
        """L'etoile des filtres : le dossier ou l'on est, en favori ou non."""
        folder = self._starrable_folder()
        if folder is None:
            return
        wanted = 0 if self._is_favorite(folder) else 1
        self.ratings.set(folder, wanted)
        self.ratings.flush()
        self._show_folder_star()
        self.show_banner(f"★ Dossier « {Path(folder).name} » en favori" if wanted
                         else f"Dossier « {Path(folder).name} » retiré des favoris",
                         "quiet")

    def _favorite_changed(self, key: str, stars: int) -> None:
        """Un favori a change, d'ou qu'il vienne : chaque vue qui le montre suit.

        Pose depuis la fiche, l'etoile du panneau du mur qui montrait la meme
        video restait vide ; depuis le mur, la vignette de la planche.
        """
        on = int(stars or 0) > 0
        self.wall.set_favorite(key, on)
        folder = self._starrable_folder()
        if folder is not None and os.path.normcase(str(folder)) == os.path.normcase(str(key)):
            self._show_folder_star()
        # Les etoiles des bandeaux de lecteur, sans attendre le survol.
        same = os.path.normcase(str(key))
        item = self.current
        for bar, marks, path in (
                (self.single_bar, self.single.marks, item.path if item else ""),
                (self.aside_bar, self.aside_player.marks, self.aside_current),
                (self.floating.bar, self.floating.player.marks, self.floating.path)):
            if path and os.path.normcase(str(path)) == same:
                bar.set_favorite(on)
                marks.set_favorite(on)
        if self.browsing:
            position = self._board_position_of(f"board@{key}")
            if position >= 0:
                self.board.set_stars(position, 1 if on else 0)

    def on_board_favorite(self, position: int) -> None:
        """L'etoile d'une vignette survolee : bascule son favori, sans fiche."""
        items = self.board.items
        if not 0 <= position < len(items):
            return
        item = items[position]
        self._rate_items([item], 0 if self._is_favorite(item.path) else 1)

    def toggle_favorite_of(self, path: str) -> None:
        """L'etoile du bandeau d'un lecteur -- fiche, lecteur de cote, lecteur
        flottant : bascule le favori de la video qu'il lit."""
        if not path:
            return
        item = self.current
        if (item is not None and not self.browsing
                and os.path.normcase(item.path) == os.path.normcase(path)):
            # La fiche de cette video : ses etoiles suivent aussi.
            return self.rate_current(0 if self._is_favorite(path) else 1)
        self._rate_paths([path], 0 if self._is_favorite(path) else 1)
        self.ratings.flush()

    def on_wall_favorite(self, path: str) -> None:
        """L'etoile d'un panneau du mur : bascule le favori de sa video."""
        if path:
            self._rate_paths([path], 0 if self._is_favorite(path) else 1)

    def toggle_advance_after_star(self) -> None:
        """⋯ › Réglages : 1 a 5, sur la fiche d'une video, passent aussi a la suivante."""
        on = not self.cfg["advance_after_star"]
        self.cfg["advance_after_star"] = on
        self.cfg.save_soon()
        action = self._menu_by_text.get("Passer à la suivante après ★")
        if action is not None:
            action.setChecked(on)
        self.show_banner(
            "Après ★ (touches 1 à 5), la fiche passe à la vidéo suivante." if on
            else "Après ★, la fiche reste sur la vidéo.", "quiet")

    def _advance_after_star(self) -> None:
        """La suite d'un ★ au clavier, si l'option le demande.

        Sur la fiche d'une video seulement : sur la planche, la touche vise la
        vignette survolee, et avancer ouvrirait une fiche. Ni « passe » ni
        decision comptee : un favori n'est pas un tri. Le clic sur l'etoile,
        lui, n'avance jamais -- on peut encore ranger la video.
        """
        item = self.current
        if (not self.cfg["advance_after_star"] or self.browsing
                or self.tab == TAB_SPLIT or item is None
                or item.kind == MODE_FOLDERS or item.is_tag):
            return
        if self.cfg["stay_in_folder"]:
            # Comme →, que la case « rester dans ce dossier » retient aussi.
            return self.step(1)
        if self._in_random_run():
            return self.pick_random()
        if self.index + 1 < len(self.items):
            self.show_item(self.index + 1)
        else:
            # Le bilan « tri terminé » n'a rien a faire ici : un favori n'est
            # pas une decision.
            self.show_banner("★ posé — c'était la dernière vidéo de la liste.",
                             "quiet")

    def pick_random_here(self) -> None:
        """Tire au hasard parmi les vidéos du seul élément affiché.

        Sans remise, comme Ctrl+H : un petit dossier ramenait sinon la meme
        video deux fois de suite.
        """
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
        showing = self.current
        avoid = ({str(showing.path)} if showing is not None
                 and showing.kind != MODE_FOLDERS and not self.browsing else set())
        video = self._here_deck.draw(pool, avoid) or pool[0]
        self.show_banner(f"Au hasard dans {where} : {Path(video).name}", "info")
        self._random_on = False
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
        #
        # Un dossier tire au sort selon son nombre de videos, puis une video
        # dedans : c'est le meme tirage, uniforme sur les videos, sans dresser
        # a chaque appui la liste des cent mille (un tiers de seconde).
        key = self._collection_key()
        memo = self._random_memo
        if memo is None or memo[0] != key:
            folders, weights, total = [], [], 0
            for item in self._collection():
                if item.locked or item.is_tag or not item.videos:
                    continue
                if under_veiled(item.path):
                    continue
                total += len(item.videos)
                folders.append(item)
                weights.append(total)
            memo = self._random_memo = (key, folders, weights)
        _key, folders, weights = memo
        video = ""
        if folders:
            # Sans remise : une video tiree ne revient qu'une fois toutes
            # passees. Le tirage reste celui d'avant, a poids egaux sur les
            # videos ; seule la liste des restes se dresse, quand le paquet
            # s'epuise -- jamais a chaque appui.
            showing = self.current
            avoid = ({str(showing.path)} if showing is not None
                     and showing.kind != MODE_FOLDERS else set())
            video = self._random_deck.draw_with(
                lambda: str(random.choice(
                    random.choices(folders, cum_weights=weights)[0].videos)),
                lambda: [str(v) for item in folders for v in item.videos],
                avoid, accept=lambda pick: not under_veiled(pick))
        if not video:
            self.show_banner("Aucune vidéo à tirer au sort", "quiet")
            return
        self.show_banner(
            f"Au hasard parmi {weights[-1]} vidéos : « {Path(video).name} »",
            "info")
        # Lance depuis le mur, comme ⤢ : Echap sur la fiche ramene au mur.
        back = self._wall_snapshot() if self.tab == TAB_SPLIT else None
        # Un premier tirage repart d'un historique vide ; les suivants s'y
        # empilent, pour ←.
        previous = self._random_path if self._in_random_run() else ""
        if not previous:
            self._random_history = []
        self._random_launching = True
        try:
            self.play_in_app(video)
        finally:
            self._random_launching = False
        self._random_on = True
        if back is not None and self.tab != TAB_SPLIT and not self.browsing:
            self._back_to_wall = back
        # La suite d'un tirage est un autre tirage : la fiche s'ouvrait dans
        # le dossier de la video, et « suivante » restait dans ce dossier,
        # case « rester dans ce dossier » cochee ou non.
        if previous:
            self._random_history = (self._random_history + [previous])[-50:]
        self._random_run = os.path.normcase(video)
        self._random_path = video

    def _in_random_run(self) -> bool:
        """Est-on toujours dans un enchainement de tirages au de ?

        On le demandait a la video affichee : etait-ce bien la derniere
        tiree ? Il suffisait d'appuyer sur Espace pendant que son dossier se
        chargeait (la fiche montrait encore la precedente), ou que le dossier
        relu ne la retrouve pas, pour retomber dans « suivante du dossier » --
        et y rester. Le mode tient desormais jusqu'a ce qu'on aille ailleurs
        soi-meme.
        """
        item = self.current
        return bool(self._random_on and not self.browsing
                    and self.tab != TAB_SPLIT
                    and (item is None or item.kind != MODE_FOLDERS)
                    and not self.cfg["stay_in_folder"]
                    and self._transient != "similar")

    def _random_back(self) -> None:
        """← apres des tirages : la video tiree juste avant."""
        while self._random_history:
            previous = self._random_history.pop()
            if os.path.exists(previous):
                self._random_launching = True
                try:
                    self.play_in_app(previous)
                finally:
                    self._random_launching = False
                self._random_on = True
                self._random_run = os.path.normcase(previous)
                self._random_path = previous
                return
        self.show_banner("C'était le premier tirage.", "quiet")

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
        self.tree_button.setChecked(show)
        self.setFocus()

    def on_tree_folder(self, path: str) -> None:
        # Une selection en cours prime : c'est elle qu'on vient de designer.
        if self.board.picked_ids and self.tree.action == "send":
            # Le bandeau dit ce qui est vraiment parti, et ce qui a ete refuse.
            self.move_picked({"path": path, "label": Path(path).name})
            return
        return self._on_tree_folder(path)

    def _on_tree_folder(self, path: str) -> None:
        """Le clic envoie l'élément en fiche, et ouvre le dossier en planche.

        C'est la seule différence d'intention entre les deux vues : parcourir
        d'un côté, décider de l'autre.
        """
        if self.tree.action == "go" or self.browsing:
            here = self._here()
            self.levels.append({
                "root": self.root, "mode": self.mode,
                "item_id": self.current.item_id if self.current else "",
            })
            self.start_root(Path(path), reset_levels=False, departure=here)
            return
        self.act_move({"path": path, "label": Path(path).name})

    def on_tree_action(self, action: str) -> None:
        # Le geste suit l'onglet (`set_tab`) : rien a retenir sur le disque.
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

    # Combien de listes gardent leur liste triable : celle des dossiers, celle
    # des videos a plat, et une ou deux de passage (favoris, mots-cles).
    SORTABLE_KEPT = 4

    def _sortable_key(self) -> tuple:
        from . import scan as _scan
        return (_scan.SHOW_VEILED, frozenset(_scan.VEILED), self._rebuild_gen)

    def _sortable_entry(self) -> list:
        """[liste, cle, triables, longueur vue, noms replies, noms par element]
        de la liste affichee, gardee d'une fois a l'autre.

        Les elements triables se recalculaient a chaque clic, trois fois, sur
        cent mille elements ; pendant une analyse, a chaque appel -- chaque
        carte ajoutee, chaque page tournee : un premier inventaire figeait la
        fenetre de bout en bout. Et une seule liste etait gardee : passer de
        « Dossiers » a « Vidéos » et retour les refaisait toutes deux. Chaque
        liste garde desormais la sienne ; celle qui s'allonge se complete par
        la fin, tout autre changement (`_touch`) la refait. La liste elle-meme
        est tenue : son adresse seule pouvait etre reprise par une autre.
        """
        items = self.all_items
        key = self._sortable_key()
        memo = self._sortable_memo
        entry = memo.get(id(items))
        if (entry is None or entry[0] is not items or entry[1] != key
                or len(items) < entry[3]):
            sortable = self._sortable
            entry = [items, key, [i for i in items if sortable(i)], len(items),
                     None, None]
            memo.pop(id(items), None)
            memo[id(items)] = entry
            while len(memo) > self.SORTABLE_KEPT:
                memo.pop(next(iter(memo)))
        elif len(items) > entry[3]:
            sortable = self._sortable
            tail = [i for i in items[entry[3]:] if sortable(i)]
            # Une liste neuve : qui tient l'ancienne la garde telle quelle.
            entry[2] = entry[2] + tail
            entry[3] = len(items)
            entry[5] = None
        return entry

    def _sortable_items(self) -> list:
        """Les elements affichables, filtres mis a part (`_sortable_entry`)."""
        return self._sortable_entry()[2]

    def _sortable_names(self) -> list:
        """Les noms replies (sans accents ni majuscules) de cette liste,
        calcules une fois par liste et non a chaque frappe.

        Replier cent mille noms a chaque lettre tapee coutait plus que la
        recherche elle-meme. Une liste qui s'allonge ne replie que sa fin.
        """
        entry = self._sortable_entry()
        items, folded = entry[2], entry[4]
        if folded is not None and len(folded) < len(items):
            folded = folded + [fold(item.name) for item in items[len(folded):]]
        elif folded is None or len(folded) != len(items):
            folded = [fold(item.name) for item in items]
        else:
            return folded
        entry[4] = folded
        entry[5] = None
        return folded

    def _folded_of(self) -> dict:
        """Le nom replie de chaque element triable, par element : pour
        preciser une recherche sur ce qui est deja a l'ecran."""
        entry = self._sortable_entry()
        names = self._sortable_names()
        if entry[5] is None:
            entry[5] = {id(item): name for item, name in zip(entry[2], names)}
        return entry[5]

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
            # Plus tard, et d'un bloc : l'ecriture attend que le disque l'ait.
            self.cfg.save_soon()
            self.show_wall()
            return
        return self._on_controls_changed()

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

    def _loose_later(self, retries: int = 0) -> None:
        """L'a-peu-pres, une fois la frappe posee : rien d'exact, on retente
        a peu pres, et on le dit.

        Une lettre de travers ne doit pas rendre un ecran vide quand il y a
        cent mille fichiers derriere, dont beaucoup sont mal nommes. Comparer
        chaque nom a peu pres coute pres d'une demi-seconde sur la collection
        entiere : le parcours se fait par tranches de quelques millisecondes,
        entre lesquelles l'interface respire. D'un bloc, il figeait l'ecran
        juste apres la derniere lettre.
        """
        self._loose_scan = None
        query = (self.criteria or {}).get("include", self.cfg["filter_include"])
        if self.items or self._loose or not query or not self.all_items:
            return
        from . import query as query_module
        if query_module._FUZZ is None:
            # rapidfuzz ne se charge qu'a la premiere recherche approchee :
            # son import (150 ms) se fait hors du fil de l'interface.
            from .tunnel import Chore
            Chore(fuzzy_available, self, fallback=False,
                  then=lambda _found: self._loose_later(retries)).start()
            return
        scan = {
            "test": query_tester(self._parsed(query), True),
            "items": self._sortable_items(), "names": self._sortable_names(),
            "at": 0, "hits": [], "query": query,
            "context": self._filter_context(), "all": self.all_items,
            "retries": retries,
        }
        self._loose_scan = scan
        self._loose_step(scan)

    def _loose_step(self, scan: dict) -> None:
        """Une tranche du parcours approchant ; la suite au prochain tour."""
        if self._loose_scan is not scan:
            return          # une frappe, un autre reglage : abandonne
        query = (self.criteria or {}).get("include", self.cfg["filter_include"])
        if self.items or self._loose or query != scan["query"]:
            self._loose_scan = None
            return
        test, names, hits = scan["test"], scan["names"], scan["hits"]
        at, total = scan["at"], len(names)
        deadline = time.perf_counter() + 0.008
        while at < total:
            end = min(total, at + 400)
            hits.extend(position for position in range(at, end)
                        if test(names[position]))
            at = end
            if time.perf_counter() >= deadline:
                break
        scan["at"] = at
        if at < total:
            QTimer.singleShot(0, lambda: self._loose_step(scan))
            return
        self._loose_scan = None
        if (self.all_items is not scan["all"]
                or self._filter_context() != scan["context"]):
            # La collection a bouge pendant le parcours (relecture, filtre) :
            # on repart d'elle, quelques fois au plus -- une relecture qui
            # publie sans cesse ne doit pas faire tourner ceci en rond.
            if scan["retries"] < 3:
                self._loose_later(scan["retries"] + 1)
            return
        items = scan["items"]
        keep = self._matcher(ignore_query=True)
        found = [items[position] for position in hits if keep(items[position])]
        if not found:
            return
        self._loose = True
        self.items = found
        self.show_banner(
            f"Rien d'exact pour « {query} » — voici les {len(found)} "
            "résultat(s) approchants." + ("" if fuzzy_available() else
            " (installez rapidfuzz pour de meilleurs rapprochements)"),
            "quiet")
        self._narrow_items = None
        self.apply_sort()
        self._show_counts()
        self.index = 0
        if self._page() == PAGE_DONE:
            self._goto_page(PAGE_SORT)
        if self.browsing:
            self.refresh_board()
        else:
            self.show_item(0)

    @staticmethod
    def _narrows(old: str, new: str) -> bool:
        """Vrai si la recherche `new` ne peut que retirer des resultats a `old`.

        Les memes mots, le dernier allonge ou d'autres ajoutes : chaque mot
        est exige, en sous-chaine. Pas d'operateur (« or », « - », guillemets,
        « ~ ») : ils peuvent elargir.
        """
        if not old.strip() or not new.startswith(old):
            return False
        for text in (old, new):
            if any(sign in text for sign in '"-~|'):
                return False
            if any(word in ("or", "ou") for word in text.lower().split()):
                return False
        return True

    def _filter_context(self) -> tuple:
        """Tout ce qui, hors du texte cherche, decide de ce qui passe."""
        rules = self.criteria or {}
        return (self.all_items is self._narrow_source, len(self.all_items),
                self._rebuild_gen, self.tab, self._transient,
                rules.get("exclude", self.cfg["filter_exclude"]),
                bool(self.cfg["only_unseen"]),
                tuple(sorted(rules.get("orientations") or ())),
                rules.get("folder_min") or 0, rules.get("folder_max") or 0)

    def apply_filter(self, include: str, exclude: str) -> None:
        # Toute nouvelle recherche repart de l'exact : sans cela, un repli
        # sur l'a-peu-pres restait en vigueur pour les suivantes, sans que
        # rien ne le dise.
        self.loose_timer.stop()
        self._loose_scan = None
        was_loose = self._loose
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
        context = self._filter_context()
        narrowing = (not was_loose and self._narrow_items is not None
                     and self.items is self._narrow_items
                     and self._narrow_context == context
                     and self._narrows(self._narrow_query, include))
        if narrowing:
            # La recherche ne fait que se preciser (« pla » puis « plag ») :
            # on filtre ce qui est deja a l'ecran, sans tout reparcourir ni
            # rebattre l'ordre -- les cartes restent ou elles etaient.
            test = query_tester(self._parsed(include), False)
            names = self._folded_of()
            self.items = [item for item in self.items
                          if test(names.get(id(item)) or fold(item.name))]
        else:
            self.items = self._filtered()
            self.apply_sort()
        self._narrow_source = self.all_items
        self._narrow_context = self._filter_context()
        self._narrow_query = include
        self._narrow_items = self.items
        self._show_counts()
        self._measure_photos()

        if not self.items:
            # Rien d'exact : l'a-peu-pres attend que la frappe se pose.
            if include:
                self.loose_timer.start()
            self.preview.cancel_all()
            # Une pause, pas un arret : on ne deplace rien ici, et decharger
            # chaque lecteur coutait jusqu'a une demi-seconde par lettre.
            self._hush_players()
            self.index = 0
            self.item_title.setText("Aucun élément ne correspond au filtre")
            self.item_parent.setText("")
            self.item_subtitle.setText(
                f"{len(self.all_items)} élément(s) masqué(s). Modifiez ou effacez le filtre."
            )
            self.grid.set_no_videos("—")
            if self.browsing:
                # La planche suit la liste, meme vide : ses cartes restaient,
                # et ne menaient plus a rien. Elle dit aussi pourquoi
                # (`_explain_empty_board`), et ses comptes suivent
                # (`on_board_page`).
                self.refresh_board()
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
        if self._page() == PAGE_DONE:
            self._goto_page(PAGE_SORT)
        if self.browsing:
            self.refresh_board()
        else:
            self.show_item(self.index)

    # Photos mesurees par lot : assez pour avancer vite, assez peu pour que
    # la liste se complete sous les yeux.
    MEASURE_BATCH = 1500

    def _measure_photos(self) -> None:
        """« Verticales » ou « Horizontales » sur des photos : on mesure celles
        dont on ignore encore le format.

        Le filtre ecarte ce qu'il ne connait pas (sans quoi « Verticales »
        montrait des horizontales). Pour des videos, c'est un ffprobe par
        fichier ; une photo, elle, se mesure en lisant son en-tete -- quelques
        millisecondes. On le fait donc tout de suite, par lots, en tache de
        fond, et la liste se complete a chaque lot.
        """
        if not photo_mode() or getattr(self, "_photo_prober", None) is not None:
            return
        wanted = (self.criteria or {}).get("orientations")
        if not wanted or len(wanted) != 1:
            return
        probe = INDEX.probe
        tried = self._photo_tried
        batch = []
        for item in self.all_items:
            if item.kind == MODE_FOLDERS:
                continue
            key = str(item.path)
            if key in tried or (probe(item.path) or {}).get("width"):
                continue
            batch.append(key)
            if len(batch) >= self.MEASURE_BATCH:
                break
        if not batch:
            return
        tried.update(batch)
        prober = InfoScan(batch, self)
        prober.done.connect(self._photos_measured)
        self._photo_prober = prober
        prober.start(QThread.LowPriority)
        self.show_banner(f"Mesure du format des photos… ({len(batch)} de plus)",
                         "quiet")

    def _photos_measured(self, _count: int) -> None:
        self._photo_prober = None
        if self._closing or not photo_mode():
            return
        # Refiltre avec ce qu'on sait de plus, puis le lot suivant.
        self.apply_filter(self.cfg["filter_include"], self.cfg["filter_exclude"])

    def focus_filter(self) -> None:
        self.controls.focus_search()

    def _item_by_id(self, item_id: str):
        # Sur self.all_items : un transfert peut aboutir alors que le filtre a
        # entre-temps ecarte l'element de la liste visible.
        for item in self.all_items:
            if item.item_id == item_id:
                return item
        return None

    def _submit(self, job: Transfer, item=None) -> None:
        """Lance l'operation en tache de fond, et retient a qui elle revient.

        Le retour d'un transfert cherchait son element parmi toute la
        collection : cent mille comparaisons a chaque transfert termine.
        """
        if item is not None and job.item_id:
            self._in_flight[job.item_id] = item
        self.transfers.submit(job)

    def _fate_text(self, path, plural: bool = False) -> str:
        """Ce qui arrivera a ce qu'on ecarte, dit sans detour.

        L'interface parlait de corbeille partout, alors que sur le NAS rien
        n'y va : ce qui reste dans la corbeille de session y est detruit pour
        de bon a la fermeture — c'est voulu, encore faut-il le savoir.
        """
        it = "ils" if plural else "il"
        mode = self.cfg["delete_mode"]
        back = f"d'ici là, Ctrl+Z ou Ctrl+B {'les' if plural else 'le'} reprennent."
        if mode == "local_trash":
            return (f"À la fermeture, {it} rejoindr{'ont' if plural else 'a'} "
                    f"le dossier de secours de {APP_NAME} ; {back}")
        if mode == "permanent" or self._is_remote(path):
            return (f"À la fermeture de {APP_NAME}, {it} ser{'ont' if plural else 'a'} "
                    f"détruit{'s' if plural else ''} définitivement ; {back}")
        return (f"À la fermeture, {it} rejoindr{'ont' if plural else 'a'} la "
                f"corbeille de Windows ; {back}")

    def _is_remote(self, path) -> bool:
        """Vrai sur un partage reseau. Retenu par lecteur : la question ne
        touche pas le reseau, mais elle revient a chaque fiche."""
        if path is None:
            return False
        text = str(path)
        if text.startswith(("\\\\", "//")):
            return True
        drive = os.path.splitdrive(text)[0].upper()
        if not drive:
            return media.is_network_path(text)
        known = self._remote_drives.get(drive)
        if known is None:
            known = self._remote_drives[drive] = media.is_network_path(text)
        return known

    def _delete_label(self) -> str:
        """Ce que dit le bouton rouge : « a la corbeille » est faux sur le NAS."""
        mode = self.cfg["delete_mode"]
        if mode == "recycle" and self._is_remote(self.root):
            return "détruit à la fermeture (NAS)"
        return DELETE_LABELS.get(mode, "")

    def _show_deleted(self, text: str) -> None:
        """Le bandeau d'une suppression : ambre, et un clic l'annule."""
        self.show_banner(text, "trash", action=self.act_undo, seconds=6)

    def _deleted_text(self, item) -> str:
        """Le bandeau d'un element ecarte : ou il est, et ce qu'il deviendra."""
        text = f"« {item.name} » supprimé · cliquer ici pour annuler (Ctrl+Z)"
        if self.cfg["delete_mode"] != "local_trash" and (
                self.cfg["delete_mode"] == "permanent" or self._is_remote(item.path)):
            text += " — détruit à la fermeture"
        return text

    def _confirm_folder_delete(self, item) -> bool:
        """La garde d'un dossier qui n'est pas fait que de videos.

        Un dossier de videos part sur une touche : se raviser, c'est Ctrl+Z.
        Mais un dossier qui contient aussi des documents, des images ou des
        programmes ne part pas sans qu'on l'ait lu — c'est ainsi qu'un dossier
        entier de papiers a ete ecarte une fois. Un dossier qu'on n'a pas pu
        compter (lu en partie, ou favori plus profond que la collection lue)
        ne rassure pas davantage : on demande aussi.
        """
        what = self._folder_doubt(item)
        if not what:
            return True
        where = str(item.path) + (f" — {human_size(item.size)}" if item.size > 0 else "")
        answer = QMessageBox.question(
            self, "Supprimer ce dossier ?",
            f"{what}\n\n{where}\n\nTout le dossier sera écarté. "
            f"{self._fate_text(item.path)}\n\nContinuer ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    def _folder_doubt(self, item) -> str:
        """Ce qui fait douter qu'un dossier ne contienne que des videos, ou ""."""
        if item.kind != MODE_FOLDERS:
            return ""
        unknown = item.file_count < 0
        others = 0 if unknown else item.file_count - item.video_count
        partial = bool(getattr(item, "incomplete", False)
                       or getattr(item, "unreadable", False))
        if others <= 0 and not unknown and not partial:
            return ""
        if others > 0:
            what = (f"« {item.name} » contient {item.video_count} vidéo(s), mais "
                    f"aussi {others} autre(s) fichier(s) : documents, images, "
                    "programmes…")
        elif unknown:
            what = (f"Le contenu de « {item.name} » n'a pas été compté : il "
                    "contient peut-être autre chose que des vidéos.")
        else:
            what = (f"« {item.name} » n'a pas pu être lu en entier (NAS) : il "
                    "contient peut-être autre chose que des vidéos.")
        return what

    def _submit_delete(self, item, ask: bool = True, disk: bool = True) -> tuple:
        """Ecarte cet element dans la corbeille de session, sans avancer.

        Rend (etat, pourquoi) : « sent » s'il est parti, « absent » s'il n'est
        plus la, « refused » sinon — pourquoi est vide si l'on a repondu non.
        Ni bandeau ni avance : c'est a l'appelant de le dire, une fois pour
        une touche, une fois pour cent coches. `disk` a faux, on ne demande
        rien au partage : l'appelant l'a interroge une fois pour tout le lot.
        """
        if item is None or item.locked:
            return "refused", ""
        if not item.movable or Path(item.path).name.startswith(PARENT_PREFIX):
            return "refused", "Un mot-clé ou un dossier de tête ne se supprime pas d'ici"
        if disk:
            state = actions.probe(item.path)
            if state == "absent":
                return "absent", f"Introuvable : {item.name}"
            if state == "injoignable":
                # Il n'a pas disparu : le NAS ne repond pas. On reste dessus.
                return "refused", (f"NAS injoignable : « {item.name} » n'a pas "
                                   "été touché. Réessayez dans un instant.")
        # Sa question, ou celle du lot (`_confirm_many_delete`), en a parle :
        # l'alerte d'apres coup n'a pas a le redire.
        asked = bool(self._folder_doubt(item))
        if ask and not self._confirm_folder_delete(item):
            return "refused", ""
        self._release_media(item.path)
        item.status = "pending_delete"
        item.status_detail = "Corbeille"
        self._submit(Transfer(kind="move", purpose="delete", src=item.path,
                              dest=self.trash.folder_for(item.path),
                              label="Corbeille", item_id=item.item_id,
                              asked=asked), item)
        return "sent", ""

    def _submit_move(self, item, dest: dict, disk: bool = True) -> tuple:
        """Envoie cet element vers une destination, sans avancer.

        Rend (etat, pourquoi), comme `_submit_delete`.
        """
        if item is None or item.locked:
            return "refused", ""
        dest_dir = Path(dest["path"])
        label = dest.get("label") or dest_dir.name
        problem = self._move_objection(item, dest_dir, disk)
        if problem:
            return ("absent" if problem.startswith("Introuvable") else "refused",
                    problem)
        self._release_media(item.path)
        item.status = "pending_move"
        item.status_detail = label
        self._submit(Transfer(kind="move", src=item.path, dest=dest_dir,
                              label=label, item_id=item.item_id), item)
        return "sent", ""

    def act_delete(self) -> None:
        """Écarte l'élément sans rien détruire : il part dans la corbeille de session."""
        item = self.current
        if item is None or item.locked:
            return self.advance()
        kept, _spared = self._spare_last_copies([item])
        if not kept:
            self.show_banner(
                "C'est le dernier exemplaire de ce groupe de doublons : il reste.",
                "quiet")
            return
        state, why = self._submit_delete(item)
        if state == "sent":
            self._show_deleted(self._deleted_text(item))
            return self.advance()
        if why:
            self.show_banner(why, "error")
        if state == "absent":
            self.advance()

    def act_move(self, dest: dict) -> None:
        item = self.current
        if item is None or item.locked:
            return self.advance()
        dest_dir = Path(dest["path"])
        label = dest.get("label") or dest_dir.name
        state, why = self._submit_move(item, dest)
        if state == "sent":
            self.show_banner(f"« {item.name} » → {label}", "done")
            return self.advance()
        if why:
            self.show_banner(why, "error")

    def _move_objection(self, item, dest_dir: Path, disk: bool = True) -> str:
        """Verifie d'avance ce qui condamnerait le transfert, pour ne pas avancer.

        Sans `disk`, seulement ce qui se voit dans les chemins : pour un lot,
        chaque question au partage etait une attente sur le fil de
        l'interface, et le transfert refuse de toute facon ce qui cloche.
        """
        if item.is_tag:
            return (f"« {item.name} » est un mot-clé, pas un dossier : entrez "
                    "dedans (Ctrl+↓) pour traiter les vidéos qu'il réunit.")
        if item.loose_only:
            return ("Cette entrée regroupe des vidéos en vrac : entrez dedans "
                    "(Ctrl+↓) pour les traiter une par une.")
        if Path(item.path).name.startswith(PARENT_PREFIX):
            return f"« {item.name} » est un dossier de tête : il ne se déplace pas."
        here = os.path.normcase(str(item.path)).rstrip("\\/")
        there = os.path.normcase(str(dest_dir)).rstrip("\\/")
        if os.path.normcase(str(Path(item.path).parent)).rstrip("\\/") == there:
            # Deja dans ce dossier : l'y « envoyer » le renommait en « (2) ».
            return f"« {item.name} » est déjà dans « {Path(dest_dir).name} »."
        if there == here:
            return "C'est déjà ce dossier."
        if there.startswith(here + os.sep):
            return "Impossible : la destination est dans le dossier à déplacer."
        if not disk:
            return ""
        # Une seule question au NAS : l'element est-il la, le NAS repond-il.
        # « Meme dossier » et « destination dedans » se lisent dans les
        # chemins, plus haut : les redemander au disque (samefile, is_dir,
        # deux resolve) coutait jusqu'a huit allers-retours par touche, et le
        # transfert, en tache de fond, refait ses verifications de toute facon.
        state = actions.probe(item.path)
        if state == "absent":
            return f"Introuvable : {item.name}"
        if state == "injoignable":
            return (f"NAS injoignable : « {item.name} » n'a pas été déplacé. "
                    "Réessayez dans un instant.")
        return ""

    def act_skip(self) -> None:
        item = self.current
        if item is not None and not item.processed and item.status != "skipped":
            item.status = "skipped"
            self.stats["skipped"] += 1
            self._note_decision()
        self.advance()

    def act_undo(self) -> None:
        if getattr(self, "_solo_busy", False):
            self.show_banner("Regroupement en cours — l'annulation sera possible "
                             "dans un instant.", "quiet")
            return
        if self.transfers.busy:
            # L'historique ne contient que les transferts aboutis : annuler
            # maintenant risquerait de défaire autre chose que la dernière action.
            self.show_banner(
                "Transfert en cours — l'annulation sera possible dans un instant.",
                "quiet",
            )
            return
        if not self.history:
            self.show_banner("Rien à annuler", "quiet")
            return
        entry = self.history[-1]
        if not entry.reversible:
            self.show_banner(
                "Suppression envoyée à la corbeille Windows : restaurez-la depuis l'explorateur.",
                "error",
            )
            return
        # Seuls les lecteurs qui montrent l'element se vident : tout decharger
        # vidait aussi le mur qu'on regardait.
        self._release_media(entry.dst or entry.src)
        self.history.pop()
        item = self._item_by_path(entry.src)
        if item is None and entry.action == "rename" and entry.dst is not None:
            # Renomme, l'element porte deja son nouveau nom.
            item = self._item_by_path(Path(entry.dst))
        if item is not None:
            item.status = "pending_undo"
        self._submit(Transfer(
            kind="undo", src=entry.dst or entry.src, entry=entry,
            label=entry.label, item_id=item.item_id if item else "",
        ), item)
        name = Path(entry.src).name
        if item is None:
            # L'historique vit toute la seance : ce qu'on annule peut venir
            # d'un autre dossier, qu'on nomme alors.
            self.show_banner(f"Restauration de « {name} » dans "
                             f"« {Path(entry.src).parent.name} »…", "info")
        else:
            self.show_banner(f"Restauration de « {name} »…", "info")

    # -- les videos seules dans leur dossier (solos.py) -------------------------
    def _solo_places(self):
        """(racine principale, racines joignables), ou None si la principale
        ne repond pas : c'est elle qui recoit."""
        from . import roots, solos
        top = self.top_root()
        listed = roots.roots() or ([top] if top is not None and not roots.is_union(top)
                                   else [])
        reach = self._reachable_roots
        live = [r for r in listed if reach is None or str(r) in reach]
        main = solos.main_root(listed)
        if main is None or not any(os.path.normcase(str(main)) == os.path.normcase(str(r))
                                   for r in live):
            return None
        return main, live

    def _propose_solos(self) -> None:
        """Apres une analyse : les dossiers a une seule video, et les « 1 » des
        autres racines, proposes au regroupement. Une fois par seance pour les
        memes ; rien n'est fait sans accord."""
        if (self._closing or getattr(self, "_solo_busy", False)
                or getattr(self, "_solo_dialog", None) is not None
                or not self._plain_whole):
            return
        from . import solos
        from .tunnel import Chore
        places = self._solo_places()
        if places is None:
            return
        main, live = places
        home, pics = main / solos.HOME_NAME, main / solos.PICS_NAME
        snoozed = self.__dict__.setdefault("_solo_snoozed", set())
        found = solos.candidates(self._plain_items, home, pics, live,
                                 list(self.cfg["solo_ignored"] or []) + list(snoozed))
        merging = "merge" not in snoozed

        def work():
            # Sur le disque, pas d'apres l'index : il peut avoir du retard.
            plans = [plan for plan in (solos.inspect(f) for f in found) if plan]
            merges = []
            if merging:
                for source in solos.other_homes(live, home):
                    try:
                        merges.append((source, sum(1 for _e in os.scandir(source))))
                    except OSError:
                        continue
            return plans, merges

        def then(result) -> None:
            if not result or self._closing or not shiboken6.isValid(self):
                return
            plans, merges = result
            if plans or merges:
                self._show_solos(plans, merges, home, pics)

        Chore(work, self, fallback=None, then=then).start()

    def _show_solos(self, plans: list, merges: list, home: Path, pics: Path) -> None:
        from .solos_dialog import SolosDialog
        if getattr(self, "_solo_dialog", None) is not None:
            return
        dialog = SolosDialog(self, plans, merges, home)
        self._solo_dialog = dialog
        snoozed = self._solo_snoozed

        def closed(_code) -> None:
            self._solo_dialog = None
            snoozed.update(str(plan["folder"]) for plan in plans)
            if merges:
                snoozed.add("merge")
            dialog.deleteLater()

        dialog.finished.connect(closed)
        dialog.chosen.connect(
            lambda folders, merge, left: self._gather_solos(folders, merge, left, home, pics))
        # Sans bloquer la fenetre : on peut finir ce qu'on regardait.
        dialog.setModal(False)
        dialog.show()
        dialog.raise_()

    def _gather_solos(self, folders: list, merge: list, left: list,
                      home: Path, pics: Path) -> None:
        """Fait ce qui a ete coche, hors du fil de l'interface."""
        from . import solos
        from .tunnel import Chore
        if left:
            self.cfg["solo_ignored"] = sorted(set(self.cfg["solo_ignored"] or []) | set(left))
            self.cfg.save_soon()
        if not folders and not merge:
            return
        for plan in folders:
            self._release_media(plan["video"])
        for source in merge:
            self._release_media(source)
        self._solo_busy = True
        total = len(folders) + len(merge)
        state = {"done": 0, "videos": 0, "merged": 0, "name": "", "total": total}
        self._solo_state = state

        def work():
            entries, failures = [], []
            for plan in folders:
                state["name"] = plan["folder"].name
                try:
                    solos.gather(plan, home, pics, entries)
                    state["videos"] += 1
                except Exception as exc:                    # noqa: BLE001
                    failures.append(f"{plan['folder'].name} : {exc}")
                state["done"] += 1
            for source in merge:
                before = len(entries)

                def seen(name: str) -> None:
                    state["name"] = name
                try:
                    solos.merge_home(source, home, entries, seen)
                except Exception as exc:                    # noqa: BLE001
                    failures.append(f"{source} : {exc}")
                state["merged"] += len(entries) - before
                state["done"] += 1
            return entries, failures

        ticker = QTimer(self)
        ticker.setInterval(1500)
        ticker.timeout.connect(lambda: self.show_banner(
            f"Regroupement dans « {solos.HOME_NAME} »… {state['done']}/{total} — "
            f"{state['name']}", "quiet", seconds=3))
        ticker.start()

        def then(result) -> None:
            ticker.stop()
            ticker.deleteLater()
            self._solo_busy = False
            entries, failures = result or ([], ["interrompu"])
            self.history.extend(entries)
            del self.history[:-self.HISTORY_MAX]
            parts = []
            if state["videos"]:
                parts.append(f"{state['videos']} vidéo(s) regroupée(s) dans « {home} »")
            if state["merged"]:
                parts.append(f"{state['merged']} élément(s) fusionné(s) depuis les autres « 1 »")
            text = ("✓ " + " ; ".join(parts) + ". Ctrl+Z annule.") if parts else ""
            if failures:
                text = (text + " " if text else "") + (
                    f"{len(failures)} échec(s) : " + " ; ".join(failures[:3]))
            tone = "error" if failures else "done"
            if entries and not self.scanning:
                # Le bilan attend la fin de la relecture : « Analyse terminée »
                # l'effacait aussitot.
                self._solo_summary = (text, tone, bool(failures)) if text else None
                self._launch_scan()
            elif text:
                self.show_banner(text, tone, seconds=10, keep=bool(failures))

        Chore(work, self, fallback=None, then=then).start()

    # -- « Similaires » : que des videos qui ressemblent a celle-ci ----------------
    @staticmethod
    def _tick_similar(bar, on: bool) -> None:
        box = getattr(bar, "similar", None)
        if box is not None and box.isChecked() != on:
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)

    def _similar_scope(self, seed: str = ""):
        """Ou chercher : toutes les racines qui repondent, pas le seul dossier.

        Celle de la video regardee en est toujours : un sondage du NAS qui
        avait tarde (trois secondes) le disait injoignable, et toutes ses
        voisines etaient ecartees -- « aucune vidéo ressemblante »."""
        from . import roots
        top = self.top_root()
        listed = roots.roots() or ([top] if top is not None and not roots.is_union(top)
                                   else [])
        reach = self._reachable_roots
        home = roots.owner(seed) if seed else None
        if home is None and top is not None and not roots.is_union(top):
            home = top
        keep = [r for r in listed if reach is None or str(r) in reach]
        if home is not None and not any(os.path.normcase(str(r)) == os.path.normcase(str(home))
                                        for r in keep):
            keep.append(home)
        return [os.path.normcase(str(r)).rstrip("\\/") for r in keep]

    def _find_similar(self, seed: str, then) -> None:
        """Les voisines de `seed`, hors du fil de l'interface : `then(liste)`,
        ou `then(None)` avec la raison deja dite."""
        from . import similar
        from .tunnel import Chore
        scope = self._similar_scope(seed)
        name = Path(seed).name
        self.show_banner(f"✦ Recherche des vidéos qui ressemblent à « {name} »…",
                         "quiet", seconds=20)

        def keep(path: str) -> bool:
            low = os.path.normcase(path)
            if scope and not any(low == top or low.startswith(top + os.sep)
                                 for top in scope):
                return False
            return not under_veiled(path)

        def work():
            space = similar.space()
            if space is None:
                return "index"
            if not space.knows(seed):
                return "inconnue"
            raw = space.nearest(seed)
            kept = [path for path, _score in raw if keep(path)]
            found, missing = [], 0
            for path in kept:
                # L'index peut garder une video deplacee entre-temps.
                if os.path.exists(path):
                    found.append(path)
                    if len(found) >= self.SIMILAR_COUNT:
                        break
                else:
                    missing += 1
                    if missing >= 400 and not found:
                        break
            if not found:
                # Rien ne reste : on dit a quelle etape, plutot qu'un « aucune ».
                return ("vide", len(raw), len(kept), missing)
            return found

        def done(result) -> None:
            if result == "index" or result is None:
                self.show_banner("Les ressemblances viennent de l'index du Labo IA, encore "
                                 "vide : lancez l'indexation (bouton ✦ en haut à droite).",
                                 "error", seconds=8)
                return then(None)
            if result == "inconnue":
                self.show_banner(f"« {name} » n'est pas encore dans l'index du Labo IA : "
                                 "indexez-la (Labo IA) pour chercher ses voisines.",
                                 "error", seconds=8)
                return then(None)
            if isinstance(result, tuple):
                _tag, raw, kept, missing = result
                if not raw:
                    why = "l'index du Labo ne lui connaît aucune voisine"
                elif not kept:
                    why = (f"ses {raw} voisines sont hors des racines joignables "
                           f"ou dans des dossiers masqués")
                else:
                    why = (f"ses voisines ne sont plus à leur place sur le disque "
                           f"({missing} introuvables) : relancez l'indexation du Labo")
                self.show_banner(f"Aucune vidéo ressemblante trouvée : {why}.", "error",
                                 seconds=10)
                return then(None)
            if not result:
                self.show_banner("Aucune vidéo ressemblante trouvée.", "quiet")
                return then(None)
            then(result)

        Chore(work, self, fallback=None, then=done).start()

    SIMILAR_COUNT = 150

    def set_similar(self, on: bool) -> None:
        """L'interrupteur ✦ de la fiche : la liste devient les voisines de la
        video regardee, de la plus ressemblante a la moins ; decoche, on
        retrouve la liste d'avant."""
        if not on:
            return self._stop_similar()
        item = self.current
        if (item is None or self.browsing or item.kind == MODE_FOLDERS
                or item.is_tag or self._transient == "dupes"):
            self._tick_similar(self.single_bar, False)
            self.show_banner("✦ Similaires : ouvrez d'abord la fiche d'une vidéo.", "quiet")
            return
        seed = str(item.path)

        def arrived(found) -> None:
            current = self.current
            box = getattr(self.single_bar, "similar", None)
            if (found is None or box is None or not box.isChecked()
                    or current is None or str(current.path) != seed):
                self._tick_similar(self.single_bar, False)
                return
            self._enter_similar(seed, found)

        self._find_similar(seed, arrived)

    def _enter_similar(self, seed: str, videos: list) -> None:
        back = {"root": self.root, "mode": self.mode, "levels": list(self.levels),
                "all_items": self.all_items, "items": self.items, "index": self.index,
                "list_leaf": self._list_leaf, "origin": self.origin}
        position = max(0, self.single.player.position())
        made = [Item(path=Path(path), kind=MODE_FILES, videos=[Path(path)],
                     video_count=1, file_count=1) for path in [seed] + videos]
        self._similar = {"seed": seed, "back": back}
        self._transient = "similar"
        self.all_items = made
        self.items = list(made)
        self.mode = MODE_FLAT
        self.index = 0
        self.plans = {}
        self.pages = {}
        name = Path(seed).name
        self._list_leaf = f"✦ Comme « {Path(seed).stem} »"
        self.crumbs.set_path(self.top_root(), self.root)
        self.crumbs.append_leaf(self._list_leaf)
        self._pending_start = ((os.path.normcase(seed), position, time.monotonic())
                               if position > 0 else None)
        self.show_item(0)
        self._apply_selectors()
        self.show_banner(f"✦ {len(videos)} vidéos qui ressemblent à « {name} », de la plus "
                         "proche à la moins : ▸ ou Espace pour la suivante. Décochez ✦ "
                         "pour revenir.", "info", seconds=8)

    def _stop_similar(self) -> None:
        state, self._similar = self._similar, None
        if state is None or self._transient != "similar":
            return
        back = state["back"]
        item = self.current
        path = str(item.path) if item is not None else ""
        position = max(0, self.single.player.position())
        self._transient = ""
        self.root, self.mode, self.origin = back["root"], back["mode"], back["origin"]
        self.levels = back["levels"]
        self.all_items, self.items = back["all_items"], back["items"]
        self._list_leaf = back["list_leaf"]
        self.plans = {}
        self.pages = {}
        self.crumbs.set_path(self._origin_for(self.root), self.root)
        if self._list_leaf:
            self.crumbs.append_leaf(self._list_leaf)
        self._apply_selectors()
        # La video regardee continue ; ▸ reprend la suite habituelle.
        wanted = os.path.normcase(path)
        for at, other in enumerate(self.items):
            if os.path.normcase(str(other.path)) == wanted:
                self._pending_start = ((wanted, position, time.monotonic())
                                       if position > 0 else None)
                return self.show_item(at)
        if path:
            # Une voisine venue d'ailleurs : la suite, c'est son dossier.
            return self.play_in_app(path, position / 1000.0)
        self.show_item(min(back["index"], len(self.items) - 1))

    def set_aside_similar(self, on: bool) -> None:
        """Le meme interrupteur, pour le lecteur de cote : ◂ ▸ y parcourent
        les voisines de la video qu'il lit."""
        if not on:
            state, self._aside_similar = self._aside_similar, None
            if state is not None:
                self.aside_playlist = state["playlist"]
                self.aside_playlist_at = state["at"]
            return
        seed = self.aside_current
        if not seed:
            self._tick_similar(self.aside_bar, False)
            return

        def arrived(found) -> None:
            box = getattr(self.aside_bar, "similar", None)
            if (found is None or box is None or not box.isChecked()
                    or self.aside_current != seed):
                self._tick_similar(self.aside_bar, False)
                return
            self._aside_similar = {"playlist": list(self.aside_playlist),
                                   "at": self.aside_playlist_at}
            self.aside_playlist = [seed] + found
            self.aside_playlist_at = 0
            self.show_banner(f"✦ {len(found)} vidéos qui ressemblent à « {Path(seed).name} » "
                             "dans le lecteur de côté : ▸ pour la suivante.", "info")

        self._find_similar(seed, arrived)

    # -- l'ultra tri : tout a la souris ------------------------------------------
    def toggle_ultra(self, on=None) -> None:
        """Ctrl+U, le ⚡ du bandeau : clic gauche garde, clic droit supprime."""
        on = (not self.ultra) if on is None else bool(on)
        if on and not self._ultra_warned:
            answer = QMessageBox.question(
                self, "Ultra tri",
                "Sur la fiche d'une vidéo, tout se fait à la souris :\n\n"
                "   • clic gauche sur l'image : on la garde, et c'est la suivante ;\n"
                "   • clic droit sur l'image : on la supprime.\n\n"
                "Ce qui est supprimé passe par la corbeille de séance : Ctrl+Z le "
                "reprend. Sur le NAS, c'est détruit pour de bon à la fermeture de "
                "Prisme.\n\nActiver l'ultra tri ?")
            if answer != QMessageBox.Yes:
                on = False
            else:
                self._ultra_warned = True
        self.ultra = on
        hook = self._ultra_click if on else None
        self.single.ultra = hook
        self.floating.player.ultra = hook
        box = getattr(self.single_bar, "ultra", None)
        if box is not None and box.isChecked() != on:
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)
        self._show_task_pills()
        self.show_banner("⚡ Ultra tri : clic gauche, on garde ; clic droit, on supprime. "
                         "Ctrl+U pour arrêter." if on else "Ultra tri arrêté.",
                         "info" if on else "quiet")

    def _ultra_click(self, button: str) -> None:
        item = self.current
        if (not self.ultra or self.browsing or item is None or self.tab == TAB_SPLIT
                or self._quiet):
            return
        # Deux clics trop rapprochés ne valent qu'un : le second tomberait sur
        # la video suivante avant qu'on l'ait vue.
        now = time.monotonic()
        if now - self._ultra_at < 0.4:
            return
        self._ultra_at = now
        if button == "left":
            return self.act_skip()
        if item.kind == MODE_FOLDERS or item.is_tag:
            return self.show_banner("Ultra tri : le clic droit ne supprime que des vidéos, "
                                    "jamais un dossier entier.", "quiet")
        self.act_delete()

    # -- les videos abimees (repair.py) -------------------------------------------
    def open_repair(self) -> None:
        """Les videos cochees, sinon celle de la fiche, sinon celles de la liste."""
        if self.browsing and self.board.picked_ids:
            paths = [str(v) for v in self._picked_videos()]
        elif not self.browsing and self.current is not None and self.current.kind != MODE_FOLDERS:
            paths = [str(self.current.path)]
        else:
            paths = [str(v) for item in self.items if not item.is_tag and not item.locked
                     for v in item.videos]
            if len(paths) > 40:
                answer = QMessageBox.question(
                    self, "Réparer des vidéos abîmées",
                    f"Vérifier les {len(paths)} vidéos de cette liste ? Chacune est lue en "
                    "entier : comptez quelques secondes par minute de vidéo.\n\n"
                    "Pour quelques vidéos seulement : cochez-les, ou ouvrez la fiche "
                    "de l'une d'elles.")
                if answer != QMessageBox.Yes:
                    return
        if not paths:
            return self.show_banner("Aucune vidéo à vérifier ici.", "quiet")
        from .repair_dialog import RepairDialog
        old = getattr(self, "_repair_dialog", None)
        if old is not None and shiboken6.isValid(old) and old.busy:
            return self._show_repair()
        dialog = RepairDialog(self, paths)
        dialog.setModal(False)
        self._repair_dialog = dialog
        dialog.show()

    def _show_repair(self) -> None:
        dialog = getattr(self, "_repair_dialog", None)
        if dialog is not None and shiboken6.isValid(dialog):
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

    def replace_with_repaired(self, pairs: list) -> None:
        """Les originales a la corbeille de seance, les reparees a leur place
        (sous leur nom, favoris compris)."""
        from .tunnel import Chore
        jobs = []
        for original, repaired in pairs:
            self._release_media(original)
            jobs.append((Path(original), Path(repaired), self.trash.folder_for(original)))

        def work():
            done, failures = [], []
            for original, repaired, bin_dir in jobs:
                try:
                    size = original.stat().st_size if original.exists() else 0
                    stored = actions.move_to(original, bin_dir)
                    name = original.stem + repaired.suffix
                    final = actions.rename_to(repaired, name)
                    done.append((original, stored, final, size))
                except Exception as exc:                    # noqa: BLE001
                    failures.append(f"{original.name} : {exc}")
            return done, failures

        def then(result) -> None:
            done, failures = result or ([], ["interrompu"])
            for original, stored, final, size in done:
                self.trash.record(original, stored, size)
                self.ratings.rename(original, final)
            text = (f"✓ {len(done)} vidéo(s) remplacée(s) par leur réparation ; les "
                    "originales sont dans la corbeille de séance." if done else "")
            if failures:
                text += (" " if text else "") + f"Échec : {failures[0]}"
            tone = "error" if failures else "done"
            if done and not self.scanning:
                # Le bilan attend la fin de la relecture, qui l'effacait.
                self._solo_summary = (text, tone, bool(failures))
                self._launch_scan()
            else:
                self.show_banner(text, tone, seconds=10)

        Chore(work, self, fallback=None, then=then).start()

    def _item_by_path(self, path: Path):
        for item in self.all_items:
            if item.path == path:
                return item
        return None

    # ------------------------------------------------------------------
    # Retour des transferts
    # ------------------------------------------------------------------
    # Ce que Ctrl+Z peut encore defaire : toute la seance, et non plus le seul
    # dossier ouvert — changer de dossier effacait tout, et un deplacement
    # fait par erreur ne s'annulait plus.
    HISTORY_MAX = 200

    def on_transfer_finished(self, job: Transfer) -> None:
        item = None
        if job.item_id:
            item = self._in_flight.pop(job.item_id, None)
            if item is None:
                item = self._item_by_id(job.item_id)

        if job.state == "failed":
            if item is not None:
                item.status = ""
                item.status_detail = ""
            if job.kind == "undo" and job.entry is not None:
                # L'annulation n'a pas abouti : l'element est toujours la ou on
                # l'avait mis, et Ctrl+Z doit pouvoir reessayer.
                if item is not None:
                    item.status = ("deleted" if job.entry.action == "delete"
                                   else "moved")
                    item.status_detail = job.entry.label
                self.history.append(job.entry)
            self.show_banner(f"Échec sur « {job.name} » : {job.error}", "error")
            self.update_counter()
            self._settle_board(item)
            if (job.kind == "rename" and item is not None
                    and item is self.current and not self.browsing):
                # La fiche avait lache sa video pour le renommage : elle la
                # reprend, sous son nom d'avant.
                self._reshow_renamed(item)
            return

        from .dupes_memory import NOT_DUPES

        if job.kind == "move" and job.purpose == "delete":
            if item is not None:
                item.status = "deleted"
            self.stats["deleted"] += 1
            self._note_decision()
            size = item.size if item is not None else 0
            if not size:
                # Une video de l'onglet « Vidéos » ne porte pas sa taille : celle
                # que l'analyse a relevee, sinon celle du fichier, une fois.
                from .stamps import known as known_stamp
                pair = known_stamp(job.src)
                size = pair[0] if pair else 0
                if not size:
                    try:
                        if os.path.isfile(str(job.result)):
                            size = os.path.getsize(str(job.result))
                    except OSError:
                        size = 0
            # Ce qu'il emportait d'autre que des videos, retenu avec lui : Ctrl+B
            # le met en tete, bien apres que le bandeau a disparu.
            others = job.others if job.others > 0 or job.others == -2 else 0
            self.trash.record(Path(job.src), job.result, size, others=others)
            self.ratings.rename(job.src, job.result)
            # Ses « pas des doublons » le suivent dans la corbeille : restaure,
            # il les retrouve ; detruit, ils s'effacent avec lui.
            NOT_DUPES.renommer(job.src, job.result)
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
            self._labo_follow(job.src, job.result)
            self.history.append(
                HistoryEntry("move", Path(job.src), job.result, job.label, True)
            )
        elif job.kind == "rename":
            if item is not None:
                item.status = ""
                item.status_detail = ""
            # Etoile et « pas des doublons » suivent ; l'index et les
            # vignettes ont suivi dans le fil du transfert.
            self.ratings.rename(job.src, job.result)
            NOT_DUPES.renommer(job.src, job.result)
            self._follow_rename(item, job.src, job.result)
            self.history.append(
                HistoryEntry("rename", Path(job.src), job.result, job.label, True)
            )
            self.show_banner(f"Renommé en « {Path(job.result).name} » · Ctrl+Z "
                             "pour revenir", "done")
            if item is not None and item is self.current and not self.browsing:
                self._reshow_renamed(item)
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
                # Son etoile etait partie avec lui : elle revient aussi, et
                # ses « pas des doublons » de meme.
                self.ratings.rename(entry.dst, back)
                NOT_DUPES.renommer(entry.dst, back)
                if action == "move":
                    self._follow_move(entry.dst, back)
                elif action == "rename":
                    self._follow_rename(item, entry.dst, back)
            if item is not None:
                item.status = ""
                item.status_detail = ""
            if action in ("move", "delete") and item is not None:
                # Le bilan de ce dossier seulement : un envoi fait ailleurs
                # et annule ici ne se retranche pas de celui-ci.
                key = "deleted" if action == "delete" else "moved"
                self.stats[key] = max(0, self.stats[key] - 1)
            if action == "rename":
                self.show_banner(
                    f"Annulé : « {back.name} » a repris son nom"
                    if back == Path(entry.src) else
                    f"Annulé : renommé « {back.name} » — son ancien nom avait "
                    "été repris entre-temps", "info")
            else:
                self.show_banner(
                    f"Annulé : « {Path(entry.src).name} » est revenu à sa place"
                    if back == Path(entry.src) else
                    f"Annulé : « {Path(entry.src).name} » est revenu, sous le nom "
                    f"« {back.name} » : sa place avait été reprise", "info"
                )
            # Sur la planche, la carte reprend son etat ; seule une fiche
            # ouverte sur lui se redessine — rouvrir la fiche depuis la
            # planche la faisait disparaitre.
            if item is not None and self.current is item and not self.browsing:
                self.show_item(self.index)

        if job.kind != "undo":
            del self.history[:-self.HISTORY_MAX]
        if job.kind != "move" or item is None or item.kind == MODE_FOLDERS:
            # Un dossier ecarte ou revenu : ses videos quittent, ou retrouvent,
            # les listes tirees de la collection (onglet Videos, mur, hasard,
            # voisines). Une video ecartee, elle, y reste avec son etat ; un
            # deplacement est suivi par `_follow_move`.
            self._touch(sortable=False)
        if job.warning:
            # Tout est arrive, mais l'ancienne place n'a pas pu etre videe en
            # entier : c'est fait, avec une reserve qu'il faut dire.
            self.show_banner(f"« {job.name} » : {job.warning}", "error")
        self.update_counter()
        self._settle_board(item)

    def start_rename(self, text: str | None = None) -> None:
        """F2 : le nom de ce qu'on regarde devient un champ, sur place.

        Au bout du fil d'Ariane, la ou on le lit : aucune fenetre. Entree
        renomme, Echap renonce. Le renommage passe par la file des transferts,
        hors du fil de l'interface, et Ctrl+Z le defait.
        """
        item = self.current
        if self.browsing or self.tab == TAB_SPLIT or item is None:
            self.show_banner("F2 renomme la vidéo ou le dossier ouvert dans sa "
                             "fiche.", "quiet")
            return
        if item.locked:
            self.show_banner("Cet élément part, ou est déjà parti : il ne se "
                             "renomme plus.", "quiet")
            return
        if not item.movable or Path(item.path).name.startswith(PARENT_PREFIX):
            self.show_banner("Un mot-clé, une entrée « en vrac » ou un dossier de "
                             "tête ne se renomme pas d'ici.", "quiet")
            return
        if self.cinema:
            # Le titre est dans la barre que le cinema cache : on en sort, et
            # le champ se pose une fois la barre revenue a sa place. Sur la
            # fiche : venu du ⛶ d'une video ouverte a cote, la sortie rendait
            # la planche, et F2 n'avait plus rien a renommer.
            self._back_to_board = False
            self.toggle_cinema(False)
            QTimer.singleShot(0, lambda: self.start_rename(text))
            return
        self._cancel_rename()
        piece = self._title_piece()
        if piece is None:
            return
        field = _RenameField(self.top_bar)
        field.setObjectName("renameField")
        # La police du titre qu'il remplace (14 points, gras), et avant de
        # mesurer le texte : le champ ecrivait plus petit que le nom.
        field.ensurePolished()
        field.setText(Path(item.path).name if text is None else text)
        bar = self.top_bar.width()
        corner = piece.mapTo(self.top_bar, QPoint(0, 0))
        wanted = field.fontMetrics().horizontalAdvance(field.text()) + 40
        width = max(160, min(max(wanted, piece.width()), bar - 8))
        height = max(piece.height(), field.sizeHint().height())
        x = max(0, min(corner.x(), bar - width - 4))
        y = max(0, corner.y() + (piece.height() - height) // 2)
        field.setGeometry(x, y, width, height)
        # Ce qui suit le titre sur la ligne (« 240p · 76,7 Ko ») s'efface le
        # temps de l'edition, sans que rien ne bouge : le champ le recouvrait
        # a moitie et touchait la taille du fichier.
        subtitle = self.item_subtitle
        if subtitle.isVisible():
            policy = subtitle.sizePolicy()
            self._rename_subtitle = policy.retainSizeWhenHidden()
            policy.setRetainSizeWhenHidden(True)
            subtitle.setSizePolicy(policy)
            subtitle.hide()
        field.committed.connect(self._commit_rename)
        field.cancelled.connect(self._cancel_rename)
        self._rename_field, self._renaming = field, item
        field.show()
        field.raise_()
        field.setFocus(Qt.OtherFocusReason)
        # Le nom sans son extension, pret a etre remplace, comme dans
        # l'explorateur.
        name = field.text()
        stem = (len(Path(name).stem) if item.kind != MODE_FOLDERS and Path(name).suffix
                else len(name))
        field.setSelection(0, stem)

    def _title_piece(self):
        """Le dernier segment du fil d'Ariane : le nom de ce qu'on regarde."""
        for piece in reversed(self.crumbs._pieces()):
            if hasattr(piece, "natural") and piece.isVisible():
                return piece
        return None

    def _cancel_rename(self) -> None:
        """Referme le champ de renommage, sans rien renommer."""
        field, self._rename_field = self._rename_field, None
        self._renaming = None
        retain, self._rename_subtitle = self._rename_subtitle, None
        if retain is not None:
            subtitle = self.item_subtitle
            subtitle.show()
            policy = subtitle.sizePolicy()
            policy.setRetainSizeWhenHidden(retain)
            subtitle.setSizePolicy(policy)
        if field is None:
            return
        field.blockSignals(True)
        field.hide()
        field.deleteLater()
        if self.isActiveWindow():
            self.setFocus()

    def _commit_rename(self, text: str) -> None:
        """Entree dans le champ : verifie le nom, puis envoie le renommage."""
        item = self._renaming
        self._cancel_rename()
        if item is None or item is not self.current or item.locked:
            return
        old = Path(item.path)
        name = text.strip()
        if not name or name.startswith("."):
            # Avant l'extension : un champ vide devenait « .mp4 », un nom
            # cache, et la video disparaissait de la collection.
            what = "le dossier" if item.kind == MODE_FOLDERS else "la vidéo"
            self.show_banner("Le nom ne peut pas être vide." if not name else
                             f"Un nom qui commence par « . » serait caché : {what} "
                             "disparaîtrait de Prisme.", "error")
            QTimer.singleShot(0, lambda: self.start_rename(text))
            return
        if (item.kind != MODE_FOLDERS and old.suffix
                and not name.lower().endswith(old.suffix.lower())):
            # L'extension reste : sans elle, la video ne s'ouvrirait plus.
            name += old.suffix
        if name == old.name:
            return
        problem = actions.name_problem(name) or self._name_taken(item, name)
        if problem:
            self.show_banner(problem, "error")
            # On reste dans le champ, avec ce qu'on avait tape, pour corriger.
            QTimer.singleShot(0, lambda: self.start_rename(text))
            return
        resume = 0
        if item.kind != MODE_FOLDERS and not self.browsing:
            resume = max(0, int(self.single.player.position()))
        item.status = "pending_rename"
        item.status_detail = name
        self._rename_resume = resume
        self.show_banner(f"Renommage : « {old.name} » → « {name} »…", "info")

        def send() -> None:
            # Windows ne renomme pas un fichier ouvert : le lecteur le lache.
            # Il le reprend a l'arrivee, au meme instant (`_reshow_renamed`).
            # Vider un lecteur coute jusqu'a cent millisecondes : au tour
            # suivant, une fois le champ parti et le bandeau pose, Entree
            # repond aussitot.
            self._release_media(old)
            self._submit(Transfer(kind="rename", src=old, new_name=name,
                                  label=old.name, item_id=item.item_id), item)
        QTimer.singleShot(0, send)

    def _name_taken(self, item, name: str) -> str:
        """Un voisin connu porte-t-il deja ce nom ? Sans rien demander au NAS :
        le transfert le reverifie de toute facon."""
        want = os.path.normcase(str(Path(item.path).with_name(name)))
        if want == os.path.normcase(str(item.path)):
            return ""           # la seule casse change
        for other in self.all_items:
            if (other is not item and not other.is_tag
                    and os.path.normcase(str(other.path)) == want):
                return f"« {name} » existe déjà dans ce dossier."
        return ""

    def _reshow_renamed(self, item) -> None:
        """La fiche reprend sa video, sous son nom, a l'instant ou on l'a laissee."""
        ms, self._rename_resume = self._rename_resume, None
        if ms and item.kind != MODE_FOLDERS:
            self._pending_start = (os.path.normcase(str(item.path)), int(ms),
                                   time.monotonic())
        self.show_item(self.index)

    def _follow_rename(self, item, old, new) -> None:
        """La collection en memoire suit un renommage reussi.

        Contrairement a un rangement, l'element reste la ou il est, sous son
        nouveau nom : sa carte, sa fiche et ses voisines le montrent aussitot,
        sans attendre la relecture du dossier. Un dossier renomme emporte le
        chemin de tout ce qu'il contient.
        """
        old_s, new_s = str(old), str(new)
        if not old_s or not new_s or old_s == new_s:
            return
        if (item is not None and item.kind != MODE_FOLDERS and not item.is_tag
                and self._follow_file_rename(item, old_s, new_s)):
            return
        # Les dossiers qui le contiennent d'abord, tant que l'element porte
        # encore son ancien nom.
        self._follow_move(old_s, new_s)
        size = len(old_s)

        def moved(path):
            text = str(path)
            if text.startswith(old_s) and (len(text) == size
                                           or text[size] in "\\/"):
                return _as_path(new_s + text[size:])
            return None

        done = set()
        renamed = []
        for items in (self.all_items, self._plain_items,
                      [item] if item is not None else []):
            for other in items:
                if id(other) in done:
                    continue
                done.add(id(other))
                fresh = moved(other.path)
                if fresh is None:
                    continue
                was = other.item_id
                other.path = fresh
                other.videos = [moved(video) or video for video in other.videos]
                other.__dict__.pop("sort_name", None)
                renamed.append(other)
                if was in self.board.picked_ids:
                    self.board.picked_ids.discard(was)
                    self.board.picked_ids.add(other.item_id)
        # Sa carte, si elle est sur la page : la planche la croyait inchangee
        # (meme element), et gardait l'ancien nom et un apercu introuvable.
        first, last = self.board._page_bounds()
        shown = self.board.items
        for position in range(max(0, first), min(last, len(shown))):
            if any(shown[position] is other for other in renamed):
                card = self.board._card_for(position)
                if card is not None:
                    card.set_item(shown[position],
                                  self.ratings.get(shown[position].path))
                    if self.browsing:
                        self.on_board_preview(position)
        # Les niveaux empiles et l'historique de navigation passaient
        # peut-etre par un dossier renomme : ils y menaient a « introuvable ».
        for entry in list(self.levels) + list(self.visited):
            fresh = moved(entry.get("root", ""))
            if fresh is not None:
                entry["root"] = fresh
        # Le nom a change : la recherche et les tris sont a refaire.
        self._touch()

    def _follow_file_rename(self, item, old_s: str, new_s: str) -> bool:
        """Une video renommee : on ne suit que ce qui la porte.

        Le cas general balaye toute la collection (un dossier renomme emporte
        tout ce qu'il contient), puis fait tout replier et retrier : sur cent
        mille videos, deux a trois cents millisecondes de gel a la fin du
        renommage, et autant a son Ctrl+Z, plus une demi-seconde au geste
        suivant. Une video n'a rien sous elle : ses dossiers, ses fiches et
        son nom replie suffisent. Rend faux si ses dossiers sont introuvables
        tels quels -- le cas general s'en charge alors.
        """
        parents = {str(parent) for parent in Path(old_s).parents}
        holders = []
        for other in self._plain_items:
            if (not other.is_tag and other.kind == MODE_FOLDERS
                    and str(other.path) in parents):
                holders.append(other)
        if self._plain_items and not holders:
            return False
        fresh = _as_path(new_s)
        touched = set()
        for other in holders:
            videos = other.videos
            for at, video in enumerate(videos):
                if str(video) == old_s:
                    other.videos = list(videos)
                    other.videos[at] = fresh
                    touched.add(other.item_id)
                    break
        # Les fiches qui la montrent : elle-meme, celle de l'onglet Videos, et
        # celle de la liste affichee si c'en est une autre.
        renamed = [item]
        cached = self._flat_cache.pop(old_s, None)
        if cached is not None and str(cached.path) == old_s:
            self._flat_cache[new_s] = cached
            renamed.append(cached)
        renamed.extend(other for other in self.all_items
                       if str(other.path) == old_s)
        seen = set()
        for other in renamed:
            if id(other) in seen or str(other.path) != old_s:
                continue
            seen.add(id(other))
            was = other.item_id
            other.path = fresh
            other.videos = [fresh if str(video) == old_s else video
                            for video in other.videos]
            other.__dict__.pop("sort_name", None)
            if was in self.board.picked_ids:
                self.board.picked_ids.discard(was)
                self.board.picked_ids.add(other.item_id)
        # Les noms replies, a leur place : la liste triable ne change pas de
        # membres, seul ce nom-ci a change.
        for entry in self._sortable_memo.values():
            folded = entry[4]
            if folded is None:
                continue
            for position, other in enumerate(entry[2]):
                if id(other) in seen and position < len(folded):
                    name = fold(other.name)
                    folded[position] = name
                    if entry[5] is not None:
                        entry[5][id(other)] = name
        first, last = self.board._page_bounds()
        shown = self.board.items
        for position in range(max(0, first), min(last, len(shown))):
            if id(shown[position]) in seen:
                card = self.board._card_for(position)
                if card is not None:
                    card.set_item(shown[position],
                                  self.ratings.get(shown[position].path))
                    if self.browsing:
                        self.on_board_preview(position)
        if touched:
            # Les apercus retenus de ses dossiers montraient l'ancien chemin
            # (meme regle que `_follow_move`).
            def owner(key: str) -> str:
                for prefix in ("board@", "peek@"):
                    if key.startswith(prefix):
                        return key[len(prefix):]
                return key.rsplit("@", 1)[0]

            self.plans = {key: plan for key, plan in self.plans.items()
                          if owner(key) not in touched}
        # La collection a change de chemins (mur, hasard, voisines) ; la liste
        # triable, elle, garde ses membres et ses noms viennent d'etre suivis.
        self._touch(sortable=False)
        return True

    def _tell_hidden_files(self, job: Transfer, item) -> None:
        """Un dossier ecarte emportait plus que ses videos : le dire tant qu'un
        Ctrl+Z suffit.

        La garde de suppression se fie aux comptes de l'index, qui ignorent ce
        qui a change plus bas. Le transfert recompte apres coup, en tache de
        fond ; c'est ici, avant que la perte ne devienne definitive a la
        fermeture, qu'on peut encore le rattraper.
        """
        others = getattr(job, "others", -1)
        if others in (0, -1) or getattr(job, "asked", False):
            # Rien d'autre que des videos, ou la question l'a dit a la touche.
            # Juge sur ce qu'on savait a l'appui, et non sur l'element a
            # l'arrivee : la relecture a pu le remplacer par un autre, deja
            # recompte, et l'on se taisait sans avoir rien demande.
            return
        what = (f"contenait aussi {others} autre(s) fichier(s)" if others > 0
                else "n'a pas pu être relu en entier : il contenait peut-être "
                     "autre chose que des vidéos")
        # Une alerte qui reste, et qui mene a la corbeille : un bandeau
        # ordinaire etait efface par la suppression suivante, et Ctrl+B ne
        # disait rien.
        self.show_banner(
            f"« {job.name} » {what}. {self._fate_text(job.src)} Cliquez ici "
            "pour le reprendre.", "error", action=self.open_trash,
            seconds=15, keep=True)

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
        if touched or moved:
            # La collection a change : ce qu'on en a tire est a refaire. La
            # liste triable seulement si c'est elle qu'on regarde : un dossier
            # vide de ses videos cesse d'etre triable.
            self._touch(sortable=self.all_items is self._plain_items)
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
        self._show_trash_badge(count)
        for action in self.overflow.actions():
            if action.text().startswith("Corbeille"):
                action.setText(
                    f"Corbeille de session ({count})" if count
                    else "Corbeille de session"
                )

    def _show_trash_badge(self, count: int) -> None:
        """La pastille de la corbeille de session : son compte, et son sort."""
        badge = getattr(self, "trash_badge", None)
        if badge is None:
            return
        if not count:
            badge.hide()
            return
        mode = self.cfg["delete_mode"]
        doomed = mode == "permanent" or (mode != "local_trash" and any(
            self._is_remote(entry.origin) for entry in self.trash.entries))
        tone = "doomed" if doomed else "kept"
        if tone != self._trash_badge_tone:
            self._trash_badge_tone = tone
            badge.setStyleSheet(
                "QPushButton#trashBadge { background: #3a2226; border-color: #6b3036;"
                " color: #ffb4b8; font-weight: 600; padding: 4px 10px; }"
                if doomed else
                "QPushButton#trashBadge { color: #c9d1db; padding: 4px 10px; }")
        # Le poids, a cote du nombre : ce que la fermeture va liberer.
        weight = sum(int(entry.size or 0) for entry in self.trash.entries)
        badge.setText(f"{count} · {self._weight(weight)}" if weight else str(count))
        noun = "élément" if count == 1 else "éléments"
        badge.setToolTip(
            (f"{count} {noun} ({self._weight(weight)}) dans la corbeille de session — "
             if weight else f"{count} {noun} dans la corbeille de session — ")
            + ("détruit(s) définitivement à la fermeture de Prisme.\n"
               if doomed else "envoyé(s) à la corbeille de Windows à la fermeture.\n")
            + "Cliquer pour les revoir ou les reprendre   (Ctrl+B)")
        badge.show()

    @staticmethod
    def _weight(size: int) -> str:
        """« 100,4 Mo », « 1,38 To » : une decimale en Mo, deux en Go et To."""
        size = float(size or 0)
        for unit, decimals in (("o", 0), ("Ko", 0), ("Mo", 1), ("Go", 2), ("To", 2)):
            if size < 1024 or unit == "To":
                return f"{size:.{decimals}f} {unit}".replace(".", ",")
            size /= 1024
        return ""

    def _refresh_mute(self) -> None:
        muted = self.cfg["muted"]
        volume = int(self.cfg["volume"] if self.cfg["volume"] is not None else 100)
        self.mute_button.setIcon(draw_icon("speaker", on=not muted))
        self.mute_button.setToolTip(
            ("Son coupé — cliquer pour l'activer   (Ctrl+M)" if muted
             else "Son actif — cliquer pour le couper   (Ctrl+M)")
            + f"\nVolume de Prisme : {volume} % — molette pour le régler, "
            "clic droit pour le curseur et la sortie"
            + f"\nSortie : {self._audio_name()}")

    @staticmethod
    def _audio_name() -> str:
        from . import audiodev
        try:
            return audiodev.best_output().description()
        except Exception:                                   # noqa: BLE001
            return "?"

    def set_volume(self, percent: int) -> None:
        """Le volume de tous les lecteurs de Prisme, de 0 a 100 %."""
        from . import audiodev
        percent = max(0, min(100, int(percent)))
        self.cfg["volume"] = percent
        self.cfg.save_soon()
        audiodev.set_volume(percent / 100.0)
        if self.cfg["muted"] and percent > 0:
            self.toggle_mute()              # regler le volume rend le son
        self._refresh_mute()
        self.show_banner(f"Volume : {percent} %", "quiet", seconds=2)

    def _volume_menu(self, _pos=None) -> None:
        """Clic droit sur le haut-parleur : un curseur de volume."""
        from PySide6.QtWidgets import QSlider, QWidgetAction
        menu = QMenu(self)
        slider = QSlider(Qt.Horizontal, menu)
        slider.setRange(0, 100)
        slider.setSingleStep(5)
        slider.setPageStep(10)
        slider.setFixedWidth(180)
        slider.setValue(int(self.cfg["volume"] if self.cfg["volume"] is not None else 100))
        slider.valueChanged.connect(self.set_volume)
        holder = QWidgetAction(menu)
        holder.setDefaultWidget(slider)
        title = menu.addAction("Volume de Prisme")
        title.setEnabled(False)
        menu.addAction(holder)
        # Ou part le son : celle de Windows, ou une sortie choisie ici.
        from . import audiodev
        menu.addSeparator()
        head = menu.addAction("Sortie du son")
        head.setEnabled(False)
        current = audiodev.best_output().description()
        auto = menu.addAction(f"Celle de Windows   ({current})" if not audiodev.chosen()
                              else "Celle de Windows")
        auto.setCheckable(True)
        auto.setChecked(not audiodev.chosen())
        auto.triggered.connect(lambda: self.set_audio_output(""))
        for device in audiodev.outputs():
            name = device.description()
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(audiodev.chosen() == name)
            action.triggered.connect(lambda _c=False, n=name: self.set_audio_output(n))
        # Un carillon sur chaque sortie, pour savoir laquelle on entend.
        tests = menu.addMenu("Tester une sortie (un petit carillon)")
        for device in audiodev.outputs():
            tests.addAction(device.description(),
                            lambda d=device: audiodev.test_output(d))
        menu.exec(self.mute_button.mapToGlobal(self.mute_button.rect().bottomLeft()))

    def set_audio_output(self, name: str) -> None:
        """La sortie du son des lecteurs : choisie ici, ou celle de Windows."""
        from . import audiodev
        self.cfg["audio_output"] = name
        self.cfg.save_soon()
        audiodev.choose(name)
        self.show_banner(f"Le son part vers « {audiodev.best_output().description()} »"
                         + ("" if name else " (la sortie de Windows)") + ".", "info")
        self._refresh_mute()

    def step(self, delta: int) -> None:
        """Element precedent ou suivant, comme les fleches du clavier.

        Case « rester dans ce dossier » cochee : la voisine dans le dossier
        de la video, et non dans la liste — qui, dans l'onglet Videos, melange
        toute la collection.
        """
        item = self.current
        if self._in_random_run():
            return self.pick_random() if delta > 0 else self._random_back()
        if self._stays_in_folder():
            target = self._folder_neighbour(str(item.path), delta)
            if not target:
                # Seule video de son dossier : le bandeau l'a dit, et la case
                # demande justement de ne pas en sortir.
                return
            # La reserve prend la voisine suivante dans le dossier, et non
            # dans la liste (melangee sous « Vidéos ») : la fleche d'apres
            # tombait toujours a cote, et chaque appui changeait deux fois
            # de source, avec un noir force. La liste du dossier est en
            # memoire, `_folder_neighbour` vient de la faire.
            listing = self._folder_videos(target)
            spare = ""
            if len(listing) >= 2 and target in listing:
                spare = listing[(listing.index(target) + delta) % len(listing)]
                if spare == target:
                    spare = ""
            for position, other in enumerate(self.items):
                if str(other.path) == target:
                    return self.show_item(position, spare=spare)
            return self.play_in_app(target)
        if self.items:
            target = self.index + delta
            if not 0 <= target < len(self.items):
                # Au bout de la liste, rien a montrer : `show_item` bornait
                # l'index et rechargeait la meme video, repartie du debut
                # sans un mot -- la position de lecture etait perdue.
                if delta > 0 and self.scanning:
                    self.show_banner("La suite arrive : l'analyse est en cours…",
                                     "quiet")
                else:
                    self.show_banner("Dernier élément de la liste." if delta > 0
                                     else "Premier élément de la liste.", "quiet")
                return
            self.show_item(target)

    def _float_position(self) -> str:
        """La position, pour le lecteur flottant -- s'il lit bien la video de
        la fiche."""
        item = self.current
        if item is None or os.path.normcase(str(item.path)) != os.path.normcase(
                self.floating.path or ""):
            return ""
        return self._position_text()

    def _position_text(self) -> str:
        """« 12 / 340 » : la place de ce qu'on regarde dans ce que parcourent
        ◂ ▸ -- son dossier si « rester dans ce dossier » est cochee, la liste
        sinon (meme melangee : c'est l'ordre ou l'on avance)."""
        item = self.current
        if item is None or not self.items:
            return ""
        stay = self._stays_in_folder()
        key = (str(item.path), stay, self.index, len(self.items),
               self._collection_gen)
        cached = getattr(self, "_position_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        text = ""
        if stay:
            listing = self._folder_videos(str(item.path))
            wanted = os.path.normcase(str(item.path))
            for number, other in enumerate(listing, 1):
                if os.path.normcase(str(other)) == wanted:
                    text = f"{number} / {len(listing)}"
                    break
        else:
            text = f"{self.index + 1} / {len(self.items)}"
        self._position_cache = (key, text)
        return text

    def _stays_in_folder(self) -> bool:
        """Vrai quand la case « rester dans ce dossier » vaut pour ce qu'on voit :
        la fiche d'une video (ni un dossier, ni un mot-cle)."""
        item = self.current
        return bool(self.cfg["stay_in_folder"] and item is not None
                    and item.kind != MODE_FOLDERS and not item.is_tag
                    and not self.browsing and self._transient != "similar")

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

    def _wall_position(self, path: str) -> str:
        """« 3 / 12 » pour une case du mur : sa place dans son dossier, tiree
        de ce que la collection sait deja -- jamais du disque, c'est demande
        a chaque survol."""
        if not path:
            return ""
        if self._siblings_gen != self._collection_gen:
            self._siblings_cache = {}
            self._siblings_gen = self._collection_gen
        folder = str(Path(path).parent)
        listing = self._siblings_cache.get(folder)
        if listing is None:
            listing = self._known_folder_videos(folder)
            if listing is not None:
                self._siblings_cache[folder] = listing
        if not listing or path not in listing:
            return ""
        return f"{listing.index(path) + 1} / {len(listing)}"

    def _folder_videos(self, path: str) -> list:
        """Les videos du dossier de ce fichier, dans l'ordre des noms.

        Tirees de la collection deja en memoire : le dossier etait relu sur
        le NAS, sur le fil de l'interface, a la premiere fleche -- et la
        liste gardee pour toute la seance, si bien qu'une voisine rangee ou
        ecartee depuis menait a « Vidéo introuvable ». Elle se refait
        desormais des que la collection bouge. Le disque n'est lu que pour
        un dossier qu'elle ne connait pas.
        """
        if self._siblings_gen != self._collection_gen:
            self._siblings_cache = {}
            self._siblings_gen = self._collection_gen
        folder = str(Path(path).parent)
        listing = self._siblings_cache.get(folder)
        if listing is None:
            listing = self._known_folder_videos(folder)
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
        if self.sort_mode in ("", "random"):
            # Au hasard de la seance, les jamais vues d'abord : par ordre des
            # noms, on retombait chaque jour sur les memes videos du dossier.
            listing = self._session_order(listing)
        self._siblings_cache[folder] = listing
        return listing

    def _seen_before(self) -> set:
        """Ce qui etait deja vu a l'ouverture de la seance (ou de l'index).

        Fige pour la seance : une video qu'on regarde ne file pas en fin de
        liste sous nos yeux -- ◂ ▸ et « 12 / 340 » resteraient sinon
        instables. C'est a la seance suivante qu'elle passe derriere.
        """
        live = INDEX.seen
        snap = getattr(self, "_seen_snap", None)
        if snap is None or snap[0] is not live:
            snap = self._seen_snap = (live, frozenset(live))
        return snap[1]

    def _session_order(self, paths: list) -> list:
        """Au hasard, mais le meme toute la seance ; les jamais vues d'abord."""
        seen, seed, crc = self._seen_before(), self._shuffle_seed, zlib.crc32
        return sorted(paths, key=lambda path: (
            str(path) in seen,
            crc(str(path).lower().encode("utf-8", "surrogatepass"), seed)))

    def _known_folder_videos(self, folder: str):
        """Les videos de ce dossier que la memoire connait, ou None.

        Le dossier ouvert en mode fichier d'abord (sa liste est celle de
        l'analyse), puis les dossiers de la collection qui le contiennent.
        """
        want = os.path.normcase(folder)
        if (self.mode == MODE_FILES and self.root is not None
                and not self.scanning
                and os.path.normcase(str(self.root)) == want):
            return sorted((str(item.path) for item in self.all_items
                           if item.kind == MODE_FILES and not item.processed),
                          key=str.lower)
        prefix = want.rstrip("\\/") + os.sep
        found = []
        covered = False
        for item in self._collection():
            if item.is_tag or item.processed:
                continue
            base = os.path.normcase(str(item.path))
            if item.kind != MODE_FOLDERS:
                if os.path.dirname(base) == want:
                    covered = True
                    found.append(str(item.path))
                continue
            if base != want and not prefix.startswith(base.rstrip("\\/") + os.sep):
                continue
            covered = True
            for video in item.videos:
                text = str(video)
                if os.path.normcase(os.path.dirname(text)) == want:
                    found.append(text)
        if not covered:
            return None
        return sorted(set(found), key=str.lower)

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
        if self._in_random_run():
            return self.pick_random()
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
        if self._quiet:
            # Rien ne se charge derriere la page neutre : on reessaie ensuite.
            QTimer.singleShot(400, self._advance_when_ready)
            return
        if self.index + 1 < len(self.items):
            self.show_item(self.index + 1)
        elif self.scanning:
            QTimer.singleShot(400, self._advance_when_ready)
        else:
            self.finish()

    def finish(self) -> None:
        # La page de fin n'est pas une image : le plein ecran s'en va.
        if self.cinema:
            self.toggle_cinema(False)
        self._release_media()
        stats = self.stats
        # Des decisions, pas la longueur de la liste : une seule video vue
        # sur dix-neuf s'annoncait « 19 éléments vus ».
        decided = stats["moved"] + stats["deleted"] + stats["skipped"]
        summary = (
            f"{stats['moved']} déplacé(s)   ·   {stats['deleted']} supprimé(s)   ·   "
            f"{stats['skipped']} laissé(s) de côté   ·   {decided} décision(s) "
            f"sur {len(self.items)} élément(s)"
        )
        if self.transfers.busy:
            summary += f"\n\n{self.transfers.active} transfert(s) encore en cours."
        self.done_page.summary.setText(summary)
        # Pendant le repli, le bilan attend le retour : il remplacait la page
        # neutre, sous son titre factice.
        if self._goto_page(PAGE_DONE):
            # Le clavier reste a la fenetre : Echap ramene aux vignettes, et
            # un Espace de plus ne declenche aucun bouton.
            self.setFocus()

    def leave_done(self) -> None:
        """Quitte « Tri terminé » pour les vignettes de la liste, sur la derniere.

        Rien d'autre n'en sortait qu'une remontee d'un niveau : a la racine
        d'un onglet -- les favoris, un mot-cle --, il fallait tout
        reanalyser pour retrouver ses vignettes.
        """
        if self.stack.currentIndex() != PAGE_DONE:
            return
        self.stack.setCurrentIndex(PAGE_SORT)
        self.show_board_at(max(0, min(self.index, len(self.items) - 1)))

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
        if not path:
            self.show_banner("Vidéo introuvable", "error")
            return
        video = Path(path)
        parent = video.parent
        # L'instant voulu attend que la fiche charge la video : le dossier est
        # parfois relu avant, et un saut pose trop tot se perd.
        self._pending_start = (
            (os.path.normcase(str(video)), int(start_s * 1000), time.monotonic())
            if start_s and start_s > 0 else None)
        if (self.tab != TAB_SPLIT and self.root == parent
                and self.mode == MODE_FILES):
            # Deja dans son dossier : la liste la connait, rien a demander --
            # depuis sa planche aussi (F sur une vignette, le hasard) : on
            # relisait le dossier pour y retrouver la meme liste.
            for position, item in enumerate(self.items):
                if item.path == video:
                    if self.browsing:
                        self.on_board_open(position)
                    else:
                        self.show_item(position)
                    return
        # Une seule question au NAS -- la video est-elle la -- qui vaut aussi
        # pour son dossier : `start_root` ne la repose pas.
        state = actions.probe(video)
        if state != "ok":
            self.show_banner(
                "Vidéo introuvable" if state == "absent" else
                f"NAS injoignable : « {video.name} » n'a pas pu être ouverte.",
                "error")
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

        already_there = (self.root == parent and self.mode == MODE_FILES
                         and not self.browsing)
        if not already_there:
            # L'endroit quitte, avant d'y toucher : planche, niveaux.
            here = self._here()
            if self.browsing:
                # La fiche de la video demandee, directement : ouvrir d'abord
                # celle de l'element courant de la planche chargeait une video
                # pour rien -- et, la planche encore vide (un onglet dont la
                # liste se lit), laissait sur les vignettes au lieu de la fiche.
                self.browsing = False
            if self.root is not None and self.root != parent:
                self.levels.append({
                    "root": self.root, "mode": self.mode,
                    "item_id": self.current.item_id if self.current else "",
                })
            self.start_root(parent, MODE_FILES, reset_levels=False,
                            restore_id=str(video), state="ok", departure=here)
            return
        for position, item in enumerate(self.items):
            if item.path == video:
                self.show_item(position)
                return

    # Combien de temps l'instant demande attend sa video : au-dela, on est
    # passe a autre chose, et y revenir par les fleches ne doit pas y sauter.
    PENDING_START_S = 30.0

    def _seek_when_ready(self, *_args) -> None:
        """Pose l'instant demande par `play_in_app`, des que la video le permet."""
        wanted = self._pending_start
        if wanted is None:
            return
        path, ms, asked = wanted
        if time.monotonic() - asked > self.PENDING_START_S:
            self._pending_start = None
            return
        player = self.single.player
        deck = next((d for d in self.single.decks if d.player is player), None)
        if deck is None or not deck.path or os.path.normcase(deck.path) != path:
            return
        status = player.mediaStatus()
        if status not in (player.MediaStatus.LoadedMedia,
                          player.MediaStatus.BufferingMedia,
                          player.MediaStatus.BufferedMedia):
            return
        self._pending_start = None
        player.setPosition(ms)

    def open_external(self, path: str = "") -> None:
        """Ctrl+O : ouvre la video dans le lecteur du systeme, un dossier dans
        l'explorateur.

        Ctrl+O refaisait ce que fait Ctrl+E, par un second chemin, alors que
        l'aide promettait le lecteur du systeme. Hors du fil de l'interface :
        Windows interroge le NAS avant d'ouvrir, et un partage endormi peut
        mettre des secondes a repondre.
        """
        target = Path(path) if path else self.reveal_target()
        if target is None:
            return
        import threading

        def launch() -> None:
            try:
                os.startfile(str(target))
            except OSError:
                pass

        threading.Thread(target=launch, name="ouvrir", daemon=True).start()
        self.show_banner(f"Ouverture de « {target.name} »…", "quiet")

    def add_to_keyword(self, keyword: str, videos: list) -> int:
        """Range ces videos sous ce mot-cle (le Labo IA), sans rien deplacer.

        Le mot rejoint « mes mots-cles » s'il n'y est pas. Rend le nombre de
        videos nouvellement rangees."""
        keyword = " ".join(str(keyword).split())
        if not keyword or not videos:
            return 0
        if fold(keyword) not in {fold(t) for t in self.tags}:
            self.tags = list(self.tags) + [keyword]
            self.cfg["tags"] = self.tags
        members = dict(self.cfg["tag_members"] or {})
        # Le mot tel qu'il est deja ecrit, s'il existe sous une autre casse.
        name = next((t for t in self.tags if fold(t) == fold(keyword)), keyword)
        have = list(members.get(name, []))
        known = set(have)
        fresh = [str(v) for v in dict.fromkeys(str(v) for v in videos) if str(v) not in known]
        members[name] = have + fresh
        self.cfg["tag_members"] = members
        self.cfg.save()
        self._mine_tags = None
        self._mine_key = None
        if self.tab == TAB_TAGS and self.tag_family == "mine" and self.mode == MODE_FOLDERS:
            self._add_tag_items()
        return len(fresh)

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
        # Un mot retire emporte les videos qu'on y avait rangees a la main.
        kept = {fold(t) for t in self.tags}
        members = self.cfg["tag_members"] or {}
        if any(fold(k) not in kept for k in members):
            self.cfg["tag_members"] = {k: v for k, v in members.items() if fold(k) in kept}
            self._mine_tags = None
        self.tag_family = "mine"
        self.cfg["tag_family"] = "mine"
        self.cfg.save()
        self.tag_chips.set_value("mine")
        if self.root is None:
            return self.setFocus()
        # Le compte se dit une fois les mots faits : ils se calculent hors
        # du fil de l'interface (`_tell_tags`).
        self._tags_banner = True
        if self.tab == TAB_TAGS and self.mode == MODE_FOLDERS:
            self._add_tag_items()
        else:
            self.set_tab(TAB_TAGS)
        self.setFocus()

    def edit_destinations(self) -> None:
        top = self.top_root()
        dialog = DestinationsDialog(self.cfg.destinations, self,
                                    start=str(top) if top is not None else "")
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.cfg.set_destinations(dialog.result_destinations())
            self.cfg.save()
            self._rebuild_commands(force=True)
        self.setFocus()

    def _show_web_status(self) -> None:
        """La pastille de la recherche web, a jour (chaque seconde)."""
        window = getattr(self, "_web_window", None)
        text, fraction, tip = "", None, ""
        if window is not None and shiboken6.isValid(window):
            try:
                text, fraction, tip = web_recap(window.activity())
            except Exception:                           # noqa: BLE001
                text = ""
        self._show_task_pills()
        if not text:
            self.web_status.hide()
            return
        if self.web_status.text() != text:
            self.web_status.setText(text)
            # Sa largeur, prise au fil d'Ariane (qui cede la sienne), jamais
            # au point d'elargir la fenetre.
            self.web_status.setMinimumWidth(min(self.web_status.sizeHint().width(), 460))
        self.web_status.setToolTip(tip)
        # L'avancement des telechargements remplit la pastille, de gauche a
        # droite, comme une barre de progression.
        base = ("QPushButton#webStatus { border: 1px solid #2f4a78; border-radius: 11px;"
                " padding: 2px 11px; color: #dfe8f5; font-size: 12px; background: %s; }"
                "QPushButton#webStatus:hover { border-color: #4f8bf0; color: #ffffff; }")
        if fraction is None:
            fill = "#151a21"
        else:
            edge = max(0.0, min(0.999, fraction))
            fill = ("qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1f3d6e, "
                    f"stop:{edge:.3f} #1f3d6e, stop:{edge + 0.001:.3f} #151a21, stop:1 #151a21)")
        style = base % fill
        if style != self._web_status_style:
            self._web_status_style = style
            self.web_status.setStyleSheet(style)
        self.web_status.show()

    @staticmethod
    def _pill_style(fraction) -> str:
        """Une pastille qui se remplit de gauche a droite, comme celle du web."""
        if fraction is None:
            fill = "#151a21"
        else:
            edge = max(0.0, min(0.999, float(fraction)))
            fill = ("qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1f3d6e, "
                    f"stop:{edge:.3f} #1f3d6e, stop:{edge + 0.001:.3f} #151a21, stop:1 #151a21)")
        return ("QPushButton#taskPill { border: 1px solid #2f4a78; border-radius: 11px;"
                " padding: 2px 11px; color: #dfe8f5; font-size: 12px; background: %s; }"
                "QPushButton#taskPill:hover { border-color: #4f8bf0; color: #ffffff; }"
                % fill)

    def _task_pill_states(self) -> list:
        """Ce qui tourne en fond : [(clef, texte, avancement, infobulle, clic)]."""
        found = []
        lab = getattr(self, "_lab_window", None)
        if lab is not None and shiboken6.isValid(lab):
            try:
                said = lab.pill_activity()
            except Exception:                           # noqa: BLE001
                said = None
            if said:
                found.append(("labo", said[0], said[1], said[2], self.open_ai_lab))
        if getattr(self, "sig_scan", None) is not None:
            done, total = getattr(self, "_sig_counts", (0, 0))
            text = self.__dict__.get("_task_text", {}).get("sig_scan") or "empreintes…"
            found.append(("sigs", f"⧉ {text}", done / total if total else None,
                          "Les empreintes des vidéos, pour la recherche de doublons.\n"
                          "⋯ › Doublons pour l'arrêter.", None))
        if getattr(self, "ultra", False):
            found.append(("ultra", "⚡ Ultra tri — gauche : garder · droit : supprimer",
                          None, "Sur la fiche d'une vidéo : clic gauche, on la garde et on "
                          "passe à la suivante ; clic droit, on la supprime (Ctrl+Z annule).\n"
                          "Cliquer ici (ou Ctrl+U) pour arrêter.",
                          lambda: self.toggle_ultra(False)))
        state = getattr(self, "_solo_state", None)
        if state is not None and getattr(self, "_solo_busy", False):
            total = max(1, state["total"])
            found.append(("solos", f"⇢ « 1 » : {state['done']} / {state['total']}",
                          state["done"] / total,
                          f"Regroupement des vidéos seules dans « 1 » — {state['name']}",
                          None))
        dialog = getattr(self, "_repair_dialog", None)
        if dialog is not None and shiboken6.isValid(dialog) and dialog.busy:
            done, total, whole, name = dialog.progress_state
            found.append(("repair", f"🛠 réparation {min(done + 1, max(total, 1))} / "
                          f"{max(total, 1)} · {int(whole * 100)} %", whole,
                          f"Vérification ou réparation de vidéos abîmées — {name}\n"
                          "Cliquer pour ouvrir la fenêtre.", self._show_repair))
        from . import voice
        part = voice.download_progress()
        if part is not None:
            found.append(("voix", f"🎤 voix : {int(part * 100)} %", part,
                          "Téléchargement du modèle de reconnaissance vocale (970 Mo)",
                          None))
        return found

    def _show_task_pills(self) -> None:
        states = self._task_pill_states()
        alive = set()
        for key, text, fraction, tip, slot in states:
            alive.add(key)
            pill = self.task_pills.get(key)
            if pill is None:
                pill = QPushButton("", self.web_status.parentWidget())
                pill.setObjectName("taskPill")
                pill.setFocusPolicy(Qt.NoFocus)
                pill.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
                pill.setMaximumWidth(420)
                if slot is not None:
                    pill.setCursor(Qt.PointingHandCursor)
                    pill.clicked.connect(slot)
                pill._style = None
                self._pill_row.addWidget(pill)
                self.task_pills[key] = pill
            if pill.text() != text:
                pill.setText(text)
                pill.setMinimumWidth(min(pill.sizeHint().width(), 380))
            pill.setToolTip(tip)
            style = self._pill_style(fraction)
            if key == "ultra":
                style = style.replace("#2f4a78", "#b8691f").replace("#151a21", "#2a1a0c")
            if style != pill._style:
                pill._style = style
                pill.setStyleSheet(style)
            pill.show()
        for key, pill in self.task_pills.items():
            if key not in alive and pill.isVisible():
                pill.hide()

    def open_web_search(self) -> None:
        """Recherche de videos hors des grandes plateformes, en lien seul.

        Importee a la premiere ouverture seulement (requests, bs4) : pres
        d'une seconde, qu'on payait sinon a chaque lancement.
        """
        # Une fenetre a part, qu'on garde ouverte a cote : elle etait une
        # boite modale, petite, qui bloquait Prisme tant qu'on cherchait.
        window = self._web_search_window()
        window.showNormal()
        window.raise_()
        window.activateWindow()

    def _web_search_window(self, busy_cursor: bool = True):
        """La fenetre de recherche web, creee au premier besoin (cachee)."""
        window = getattr(self, "_web_window", None)
        if window is not None and shiboken6.isValid(window):
            return window
        if busy_cursor:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            from .search_dialog import WebSearchWindow
            window = WebSearchWindow(self.cfg, self, ffmpeg=Tools.ffmpeg)
        finally:
            if busy_cursor:
                QApplication.restoreOverrideCursor()
        window.setAttribute(Qt.WA_DeleteOnClose)
        self._web_window = window
        return window

    def _resume_web_downloads(self) -> None:
        """Au lancement : des telechargements interrompus a la derniere
        fermeture ? La recherche web se rouvre en coulisse et les reprend (la
        pastille de la barre les montre) -- sans attendre qu'on ouvre sa
        fenetre."""
        import json
        from .config import PRIVATE_DIR
        try:
            data = json.loads((PRIVATE_DIR / "recherche-web.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        pending = [d for d in (data.get("downloads") or [])
                   if isinstance(d, dict) and d.get("state") == "running"]
        if pending and not getattr(self, "_closing", False):
            self._web_search_window(busy_cursor=False)

    # ------------------------------------------------------------------
    # Clavier
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.ControlModifier)
        if self._eat_repeat:
            # La touche qui a ramene du repli, encore maintenue : Echap
            # remontait ensuite jusqu'en haut, Ctrl+K rebasculait.
            if event.isAutoRepeat():
                return
            self._eat_repeat = False

        # Avant tout : le repli doit repondre d'ou que l'on vienne -- la page
        # de fin comprise, qui l'ignorait --, et en sortir de meme. Une touche
        # maintenue ne bascule qu'une fois : sinon l'etat final tenait du pile
        # ou face, et le mur se rechargeait a chaque retour.
        if ctrl and key == Qt.Key_K:
            if event.isAutoRepeat():
                return
            if event.modifiers() & Qt.AltModifier:
                # Ctrl+Alt+K ne fait que cacher, ici comme ailleurs.
                return self.quiet_now()
            self.toggle_quiet()
            return
        if self._quiet:
            # Rien ne traverse la page de repli : Alt+← y faisait reparaitre
            # les vignettes. Echap non plus n'en sort : c'est la touche du
            # reflexe. Ctrl+K (plus haut) ou un double-clic.
            return
        if key == Qt.Key_F1:
            # L'aide, a la touche de toujours : elle ne se trouvait que dans
            # « ⋯ › Aide ».
            if not event.isAutoRepeat():
                self.show_help()
            return
        if event.modifiers() & Qt.AltModifier and key == Qt.Key_Left:
            if not self.go_back() and self.stack.currentIndex() == PAGE_DONE:
                self.leave_done()
            return
        if self.stack.currentIndex() == PAGE_DONE:
            # Le tri d'un sous-dossier fini, on revient d'ou l'on venait ; a
            # la racine d'un onglet, a ses vignettes. Il n'y avait la aucune
            # sortie, et il fallait tout reanalyser.
            if key == Qt.Key_Escape or (ctrl and key == Qt.Key_Up):
                if not self.go_up():
                    self.leave_done()
                return
            return super().keyPressEvent(event)
        if self.stack.currentIndex() != PAGE_SORT:
            return super().keyPressEvent(event)
        # Le plein ecran, aux touches de tous les lecteurs. Avant la branche
        # d'Entree : Alt+Entree mettait en pause.
        if key == Qt.Key_F11 or (event.modifiers() & Qt.AltModifier
                                 and key in (Qt.Key_Return, Qt.Key_Enter)):
            if not event.isAutoRepeat():
                self.toggle_full_view()
            return
        if key == Qt.Key_F2:
            if not event.isAutoRepeat():
                self.start_rename()
            return

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
                return self.toggle_full_view()
            if key == Qt.Key_L:
                if event.isAutoRepeat():
                    return
                if self.floating.active:
                    return self.leave_floating(activate=True)
                return self.open_floating()
            if key == Qt.Key_M:
                return self.toggle_mute()
            if key == Qt.Key_U:
                return self.toggle_ultra()
            if key == Qt.Key_O:
                return self.open_external()
            if key == Qt.Key_D:
                return self.edit_destinations()
            if key == Qt.Key_F:
                return self.focus_filter()
            if key == Qt.Key_B:
                return self.open_trash()
            if key == Qt.Key_P:
                # Le mur n'a pas de fiche a lui : basculer y menait a une
                # planche « Dossiers » vide.
                if self.tab == TAB_SPLIT:
                    return
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
                # Ce que fait le bouton ↑, dont l'infobulle l'annonce : arrive
                # par le fil, un mot-cle ou une fiche, la pile etait vide et
                # rien ne remontait.
                return self.go_parent()
            return super().keyPressEvent(event)

        if key == Qt.Key_Shift:
            # Un quart de seconde avant les neuf instants : Maj+← / Maj+→
            # (10 s) ne doivent pas les faire clignoter.
            if not event.isAutoRepeat():
                self._shift_held = True
                self._peek_timer().start()
            return
        if key == Qt.Key_Backspace:
            # Retour arriere ne supprime plus rien. Sous Windows, c'est le
            # geste de « revenir » : on le faisait pour remonter, et c'est un
            # dossier entier qui partait a la corbeille, detruit a la fermeture.
            return
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
                if self._back_to_wall is not None:
                    # Une fiche ouverte depuis le mur (⤢, F) : on y revient,
                    # avec les memes videos, et non a une planche.
                    return self.return_to_wall()
                # On edite : on remonte aux vignettes avant de quitter le niveau.
                self.show_board_at(self.index)
                return
            if self.tab == TAB_SPLIT:
                # Le mur n'a pas d'etage au-dessus de lui : le dernier Echap
                # ramene a l'onglet d'ou l'on venait. Il ne faisait rien, et
                # le mur etait un cul-de-sac au clavier.
                if not event.isAutoRepeat():
                    self.set_tab(getattr(self, "_tab_before_wall", TAB_FOLDERS)
                                 or TAB_FOLDERS)
                return
            # Puis d'un dossier a son parent, comme ↑. Au sommet, on le dit :
            # un Echap de trop menait a l'accueil et abandonnait la
            # verification du NAS -- la collection etait a relire.
            return self.go_parent()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if event.isAutoRepeat():
                return
            if self.viewer.currentWidget() is self.single:
                self.single.toggle_pause()
                return
            # Entree « ouvre » : on entre dans le dossier de la fiche, ou dans
            # celui sous la souris sur la planche. Il fallait connaitre Ctrl+↓.
            if self.viewer.currentWidget() is self.grid:
                return self.enter_current()
            if self.browsing and self.tab != TAB_SPLIT:
                return self._enter_hovered()
            return
        if self.browsing or self.tab == TAB_SPLIT:
            return self._key_on_board(event)

        # La fiche : l'element courant est celui qu'on regarde.
        if key == Qt.Key_Delete:
            # Une touche maintenue n'enchaine pas les suppressions.
            if not event.isAutoRepeat():
                self.act_delete()
            return
        if key == Qt.Key_Space:
            # La suivante, sans rien decider : la meme touche pour une video
            # et pour un dossier. La pause est sur Entree (ou un clic).
            return self.act_skip()
        if (key in (Qt.Key_Left, Qt.Key_Right)
                and event.modifiers() & Qt.ShiftModifier):
            # Maj+← / Maj+→ : 10 s en arriere ou en avant, comme VLC. Les
            # boutons ⏪ ⏩ sont partis ; les fleches seules restent au tri.
            self._shift_held = False          # pas de mosaique ensuite
            self._peek_timer().stop()
            if self.single.peeking:
                self.peek_hide()
            if self.viewer.currentWidget() is self.single:
                self.single.jump(10 if key == Qt.Key_Right else -10)
            return
        # Comme ◂ ▸, qui annoncent ces touches : « rester dans ce dossier »
        # vaut aussi au clavier.
        if key == Qt.Key_Right:
            return self.step(1)
        if key == Qt.Key_Left:
            return self.step(-1)
        if key == Qt.Key_F:
            return self.play_focused()

        text = event.text().lower().strip()
        if text in ("0", "1", "2", "3", "4", "5"):
            self.rate_current(int(text))
            if int(text) > 0 and not event.isAutoRepeat():
                self._advance_after_star()
            return
        if text:
            dest = self.cfg.destination_for_key(text)
            if dest:
                if not event.isAutoRepeat():
                    self.act_move(dest)
                return
        super().keyPressEvent(event)

    def _key_on_board(self, event) -> None:
        """Planche et mur : les touches de tri visent ce qu'on survole.

        L'element « courant » n'y est pas a l'ecran : c'est la derniere fiche
        ouverte, le dossier dont on vient de remonter, ou la liste restee sous
        le mur. Suppr, une lettre ou un chiffre l'ecartaient, le rangeaient ou
        le mettaient en favori sans qu'on l'ait vu -- c'est ainsi qu'un
        dossier entier est parti du NAS. On vise donc la vignette ou le
        panneau sous la souris, ou les elements coches ; a defaut, rien, et on
        le dit.
        """
        key = event.key()
        wall = self.tab == TAB_SPLIT
        if key == Qt.Key_Space:
            # Espace : la suivante, partout -- dans le panneau du mur survole,
            # ou dans le lecteur de cote. La pause est au clic sur l'image.
            if not event.isAutoRepeat():
                found = self._wall_hovered() if wall else None
                if found is not None:
                    self.wall.panes[found[0]]._forward()
                elif not self.aside.isHidden() and self.aside_current:
                    self.aside_step(1)
            return
        if (key in (Qt.Key_Left, Qt.Key_Right)
                and event.modifiers() & Qt.ShiftModifier):
            # Maj+← / Maj+→ : 10 s dans ce qui joue -- le panneau du mur
            # survole, ou le lecteur de cote.
            seconds = 10 if key == Qt.Key_Right else -10
            found = self._wall_hovered() if wall else None
            if found is not None:
                self.wall.panes[found[0]].jump(seconds)
            elif not self.aside.isHidden() and self.aside_current:
                self.aside_player.jump(seconds)
            return
        if key in (Qt.Key_Left, Qt.Key_Right):
            # Sur la planche, les fleches tournent les pages : elles ouvraient
            # une fiche sans entete, par-dessus les vignettes.
            if not wall:
                self.change_page(1 if key == Qt.Key_Right else -1)
            return
        if key == Qt.Key_F:
            if wall:
                return self._open_wall_hovered()
            return self.play_focused()
        text = event.text().lower().strip()
        stars = int(text) if text in ("0", "1", "2", "3", "4", "5") else None
        dest = (self.cfg.destination_for_key(text)
                if text and stars is None else None)
        delete = key == Qt.Key_Delete
        if not delete and stars is None and not dest:
            # Espace et le reste : rien a decider ici.
            return super().keyPressEvent(event)
        if event.isAutoRepeat():
            # Une touche maintenue n'enchaine ni suppressions ni envois.
            return
        if wall:
            return self._wall_key(delete, stars, dest)
        targets = self._board_targets()
        if not targets:
            self.show_banner(
                "Survolez une vignette, ou cochez-en : sur la planche, les "
                "touches visent ce qui est sous la souris.", "quiet")
            return
        if stars is not None:
            return self._rate_items(targets, stars)
        if delete:
            return self._delete_items(targets)
        self._move_items(targets, dest)

    def _enter_hovered(self) -> None:
        """Entree sur la planche : entrer dans le dossier survole ; une video
        survolee s'ouvre en fiche, comme d'un clic."""
        position = self._board_hovered()
        if position < 0:
            self.show_banner("Survolez un dossier, puis Entrée pour y entrer",
                             "quiet")
            return
        self.on_board_click(position)

    def _board_hovered(self) -> int:
        """Position, dans la planche, de la carte sous la souris — ou -1.

        Relue a l'instant de la touche plutot que prise au dernier sondage :
        une souris partie de la carte ne doit plus la designer.
        """
        board = self.board
        if not board.isVisible() or self.viewer.currentWidget() is not board:
            return -1
        slot = board._card_under(QCursor.pos())
        if not 0 <= slot < len(board.cards):
            return -1
        position = board.cards[slot].index
        return position if 0 <= position < len(board.items) else -1

    def _board_targets(self) -> list:
        """Ce que vise une touche sur la planche.

        La carte survolee ; toute la selection si cette carte en fait partie,
        ou si rien n'est survole. Vide sinon : on ne devine pas.
        """
        board = self.board
        position = self._board_hovered()
        picked = board.picked_ids
        if position >= 0:
            item = board.items[position]
            if picked and item.item_id in picked:
                return board.picked_items()
            return [item]
        return board.picked_items() if picked else []

    def _wall_hovered(self):
        """(rang, chemin) du panneau du mur sous la souris, s'il joue — ou None."""
        if self.viewer.currentWidget() is not self.wall:
            return None
        cursor = QCursor.pos()
        for index, pane in enumerate(self.wall.panes):
            if not pane.video_path or not pane.isVisible():
                continue
            stage = pane.stage
            corner = stage.mapToGlobal(QPoint(0, 0))
            if QRect(corner, stage.size()).contains(cursor):
                return index, pane.video_path
        return None

    def _wall_key(self, delete: bool, stars, dest) -> None:
        """Une touche de tri sur le mur : pour la video du panneau survole."""
        found = self._wall_hovered()
        if found is None:
            self.show_banner(
                "Survolez un panneau : sur le mur, les touches visent la vidéo "
                "sous la souris.", "quiet")
            return
        index, path = found
        if stars is not None:
            return self._rate_paths([path], stars)
        # Le panneau se remplit d'une autre video une fois celle-ci partie.
        if delete:
            if self.delete_one(path):
                self.wall.refill_one(index)
            return
        if self.move_one(path, dest):
            self.wall.refill_one(index)

    def _open_wall_hovered(self) -> None:
        """F sur le mur : la fiche de la video survolee, comme son ⤢."""
        found = self._wall_hovered()
        if found is None:
            self.show_banner("Survolez un panneau, ou double-cliquez dessus",
                             "quiet")
            return
        self.open_video_path(found[1])

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
        self.cfg.save_soon()
        # Tous les lecteurs, celui de cote et l'apercu de la planche compris :
        # Ctrl+M laissait le lecteur de droite parler.
        for player in (self.grid, self.single, self.board, self.aside_player,
                       self.wall, self.floating.player):
            player.set_muted(muted)
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

    def _geometry_to_keep(self) -> dict:
        """Taille, place et etat agrandi de la fenetre, pour le prochain lancement.

        Seule la taille etait retenue : sur un second ecran, ou agrandie, il
        fallait replacer la fenetre a chaque lancement. On garde la place
        d'avant l'agrandissement -- et d'avant le plein ecran du mur, qui
        n'est pas un etat ou l'on veut rouvrir.
        """
        if self.wall_full:
            state = getattr(self, "_wall_kept", (0, 0, None))[2]
        elif self._cinema_kept is not None:
            state = self._cinema_kept
        else:
            state = self.windowState()
        maximized = bool(state is not None and state & Qt.WindowMaximized)
        shape = self.normalGeometry()
        if shape.isEmpty():
            shape = self.geometry()
        return {"w": shape.width(), "h": shape.height(),
                "x": shape.x(), "y": shape.y(), "maximized": maximized}

    def _restore_geometry(self, saved) -> None:
        """Rouvre ou l'on avait laisse la fenetre, si cet ecran est encore la.

        L'ecran vient de la place retenue, et non de l'ecran principal : un
        ecran debranche depuis ramene la fenetre sur le principal, a une
        taille qui y tient (`_fit_to_screen`).
        """
        saved = saved if isinstance(saved, dict) else {}

        def number(key, default):
            value = saved.get(key, default)
            return value if isinstance(value, int) else default

        width, height = number("w", 1400), number("h", 900)
        x, y = saved.get("x"), saved.get("y")
        screen = None
        if isinstance(x, int) and isinstance(y, int):
            screen = QApplication.screenAt(
                QPoint(x + min(width, 200) // 2, y + 10))
        if screen is None:
            self._fit_to_screen(width, height)
        else:
            free = screen.availableGeometry()
            width = max(720, min(width, free.width()))
            height = max(420, min(height, free.height() - 48))
            # Tout entiere sur cet ecran, barre de titre comprise : un bord
            # sorti du cadre n'aurait plus de poignee pour la ramener.
            x = max(free.left(), min(x, free.right() - width + 1))
            y = max(free.top() + 30, min(y, free.bottom() - height + 1))
            self.setGeometry(x, y, width, height)
        if saved.get("maximized") is True:
            # Pris en compte a l'affichage (`show`), sur l'ecran ou elle est.
            self.setWindowState(self.windowState() | Qt.WindowMaximized)

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
        if self._eat_repeat and not event.isAutoRepeat():
            self._eat_repeat = False
            return
        if event.key() == Qt.Key_Shift and not event.isAutoRepeat():
            self._shift_held = False
            self._peek_timer().stop()
            self.peek_hide()
            return
        super().keyReleaseEvent(event)

    # A la fermeture, les fils de fond, tous ensemble, une fois arretes. Les
    # transferts en vol, eux, s'attendent jusqu'au bout (`_wait_transfers`).
    CLOSE_THREADS_WAIT_S = 3

    def _warm_voice(self) -> None:
        from . import voice
        from .tunnel import Chore
        if self._closing or not voice.available() or not voice.model_on_disk():
            return

        def work():
            try:
                voice.RECOGNIZER.warm()
            except Exception:                               # noqa: BLE001
                pass
        Chore(work, self).start()

    def _spoken_search(self, text: str) -> None:
        """Ce qu'on a dit au micro devient la recherche, comme tapee."""
        self.controls.include.setText(text)
        self.controls.include.setFocus()

    def closeEvent(self, event):
        # Les couvertures preparees, notees pour la prochaine fois.
        COVERS.save(force=True)
        from .voice import RECOGNIZER
        RECOGNIZER.close()
        # Des telechargements en cours : on demande, ils reprendront a la
        # prochaine ouverture. Fermer Prisme les coupait sans rien dire.
        web = getattr(self, "_web_window", None)
        if web is not None and shiboken6.isValid(web):
            try:
                active = web.downloader.summary()["active"]
            except (RuntimeError, AttributeError):
                active = 0
            if active:
                answer = QMessageBox.question(
                    self, "Téléchargements en cours",
                    f"{active} téléchargement{'s' if active > 1 else ''} en cours.\n\n"
                    "Ils reprendront d'eux-mêmes, là où ils en sont, au prochain "
                    "lancement de Prisme.\n\nQuitter maintenant ?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    event.ignore()
                    return
            web._quitting = True
        mark("fermeture")
        # Plus rien ne doit repartir : recolte, partage, guet des bandeaux.
        self._closing = True
        self._quiet_keys.arm(False)
        if self.global_quiet is not None:
            self.global_quiet.stop()
        # Ce qui est local et rapide d'abord : reglages et favoris ne doivent
        # jamais dependre d'un NAS qui repond.
        self.cfg["window"] = self._geometry_to_keep()
        self.float_timer.stop()
        if self.floating.isVisible():
            self.cfg["float_geometry"] = self.floating.geometry_to_keep()
        self.floating.close_quietly()
        self.cfg.save()
        self.ratings.flush()
        # La fenetre disparait tout de suite : ce qui suit peut durer, et
        # rien ne doit plus y repondre.
        self.banner.hide()
        self.hide()
        for timer in (self.session_timer, self.activity_timer, self.aside_watch,
                      self.board_timer, self.banner_timer, self.seen_timer,
                      self.burst_timer, self.retry_timer, self.loose_timer):
            timer.stop()
        # Le parcours approchant en cours, par tranches, s'arrete aussi.
        self._loose_scan = None
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
        # Prisme ne s'arretait qu'une fois toutes ses fenetres fermees -- et
        # la recherche web, ou les vues invisibles de son navigateur, en
        # comptaient encore une : la fenetre disparaissait, le processus
        # restait, verrou compris, et la relance repondait « deja ouvert ».
        # La fenetre principale fermee, c'est Prisme qui se ferme.
        web = getattr(self, "_web_window", None)
        if web is not None:
            try:
                web.close()
            except RuntimeError:
                pass
        QTimer.singleShot(0, QApplication.instance().quit)

    def _stop_background(self) -> None:
        """Demande a chaque fil de fond de s'arreter, sans en attendre aucun."""
        # Un comptage coupe par la fermeture ne doit pas s'enregistrer comme
        # le compte de la collection.
        self._count_stopped = self.counter is not None
        self._audit_stopped = self.audit is not None
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
        l'attend, en le disant, jusqu'a ce qu'il aboutisse.

        Seul « Fermer quand même » y renonce : c'est un choix. On passait
        outre de soi-meme au bout de trente secondes, et ce qui arrivait
        ensuite -- dans un processus qui ne recevait plus rien -- perdait son
        etoile et ses « pas des doublons », ou restait hors de la corbeille
        de session. Le processus attendait le transfert de toute facon.
        """
        if not self.transfers.busy:
            return
        waiter = QProgressDialog(
            f"{self.transfers.active} transfert(s) en cours — fermeture dès "
            "qu'ils sont terminés.", "Fermer quand même", 0, 0, self)
        waiter.setWindowTitle(APP_NAME)
        waiter.setMinimumDuration(0)
        waiter.show()
        while self.transfers.busy and not waiter.wasCanceled():
            QApplication.processEvents()
            self.transfers.wait(100)
        waiter.close()


def check_tools(parent=None) -> bool:
    """ffmpeg et ffprobe sont-ils la ? Sinon, Prisme propose de les
    telecharger lui-meme : sur un PC neuf, le programme vendu s'arretait."""
    if Tools.ffmpeg and Tools.ffprobe:
        return True
    from . import ffmpeg_fetch
    ask = QMessageBox(QMessageBox.Question, "ffmpeg nécessaire",
                      f"{APP_NAME} a besoin de ffmpeg et ffprobe pour lire les vidéos et "
                      "fabriquer les aperçus.\n\nLes télécharger maintenant ? "
                      f"({ffmpeg_fetch.SIZE_TEXT}, une seule fois, depuis gyan.dev)", parent=parent)
    fetch = ask.addButton("Télécharger", QMessageBox.AcceptRole)
    ask.addButton("Quitter", QMessageBox.RejectRole)
    ask.setDefaultButton(fetch)
    ask.exec()
    if ask.clickedButton() is not fetch:
        return False
    progress = QProgressDialog("Téléchargement de ffmpeg…", "Annuler", 0, 1000, parent)
    progress.setWindowTitle(APP_NAME)
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    state = {"done": 0, "total": 0, "error": None, "over": False}

    def work() -> None:
        try:
            ffmpeg_fetch.fetch(lambda done, total: state.update(done=done, total=total),
                               lambda: progress.wasCanceled())
        except Exception as exc:                            # noqa: BLE001
            state["error"] = str(exc) or type(exc).__name__
        state["over"] = True
    threading.Thread(target=work, daemon=True, name="prisme-ffmpeg").start()
    loop = QEventLoop()
    timer = QTimer()

    def tick() -> None:
        if state["total"]:
            progress.setValue(min(999, state["done"] * 1000 // state["total"]))
            progress.setLabelText(f"Téléchargement de ffmpeg… {state['done'] // 2**20} / "
                                  f"{state['total'] // 2**20} Mo")
        if state["over"]:
            loop.quit()
    timer.timeout.connect(tick)
    timer.start(100)
    loop.exec()
    timer.stop()
    progress.close()
    if state["error"] is None and Tools.resolve():
        return True
    QMessageBox.critical(
        parent, "ffmpeg introuvable",
        f"ffmpeg n'a pas pu être téléchargé ({state['error'] or 'fichiers absents'}).\n\n"
        "Installez-le (winget install Gyan.FFmpeg), ou posez ffmpeg.exe et ffprobe.exe "
        f"dans {ffmpeg_fetch.folder()}, puis relancez {APP_NAME}.",
    )
    return False
