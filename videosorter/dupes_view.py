"""La revue des doublons : chaque groupe d'un coup d'oeil, et ce qu'on en garde.

Une ligne par copie : trois petites images prises aux memes moments (15, 50
et 85 % de la video), alignees d'une copie a l'autre -- on voit tout de
suite si c'est la meme video --, puis son chemin depuis la racine, ses
mesures (le meilleur de chaque mesure en vert) et sa decision.

Le choix de la copie a garder se fait par criteres croises : un premier
critere, puis, a egalite, un deuxieme, puis un troisieme (« meilleure
qualite, puis le chemin le plus long »). Des dossiers a privilegier passent
avant tout. « Comparer en video » lit les copies d'un groupe ensemble, au
meme instant, et enchaine les groupes un par un.

Rien ne part sans le bouton du bas, et tout passe par la corbeille de la
seance (Ctrl+B pour reprendre). Un groupe garde toujours au moins une copie.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from .scan import human_duration, human_resolution, human_size

THUMB_W = 160
THUMB_H = 90
# Extraites un peu plus grandes que montrees : nettes une fois reduites.
THUMB_MADE = 240
MOMENTS = (0.15, 0.5, 0.85)
PAGE = 30

# Les dossiers d'arrivee : une copie qui y dort n'est pas encore rangee.
_INBOX = re.compile(
    r"(trier|tri\b|t[ée]l[ée]charg|download|nouveau|nouvelles?|inbox|arriv|import|"
    r"vrac|divers|temp|tmp|a classer|à classer|en attente|telephone|téléphone)",
    re.IGNORECASE)

# Les criteres : (cle, texte). Chacun donne une valeur, la plus grande gagne ;
# a egalite, le critere suivant departage.
CRITERIA = (
    ("best", "Meilleure qualité (définition)"),
    ("deepest", "Chemin le plus long (la plus classée)"),
    ("sorted", "Déjà rangée (ni racine, ni dossier d'arrivée)"),
    ("bitrate", "Meilleur débit (la moins compressée)"),
    ("biggest", "Le plus gros fichier"),
    ("smallest", "Le plus petit fichier (gagner de la place)"),
    ("longest", "La plus longue (pas tronquée)"),
    ("shallowest", "Chemin le plus court"),
)
DEFAULT_CRITERIA = ("best", "deepest", "biggest")

STYLE = """
QWidget#dupesRoot, QWidget#dupesContent { background: #0e1116; color: #e6edf3; }
QLabel { color: #e6edf3; }
QLabel#dupesTitle { font-size: 20px; font-weight: 600; }
QLabel#dupesSub, QLabel#dupesDim { color: #8b949e; }
QFrame#dupesBar { background: #161b22; border: 1px solid #262c36; border-radius: 10px; }
QFrame#dupesGroup { background: #131820; border: 1px solid #262c36; border-radius: 10px; }
QFrame#dupesCopy { background: #0e1116; border: 2px solid #262c36; border-radius: 8px; }
QFrame#dupesCopy[state="keep"] { border-color: #2ea043; }
QFrame#dupesCopy[state="trash"] { border-color: #da3633; background: #1a1012; }
QPushButton { background: #21262d; color: #e6edf3; border: 1px solid #30363d;
              border-radius: 8px; padding: 6px 12px; }
QPushButton:hover { background: #30363d; }
QPushButton:disabled { color: #6e7681; }
QPushButton#dupesPrimary { background: #da3633; border-color: #f85149; font-weight: 600; }
QPushButton#dupesPrimary:hover { background: #f85149; }
QPushButton#dupesPrimary:disabled { background: #3d1d1f; border-color: #3d1d1f; }
QPushButton#dupesApply { background: #1f6feb; border-color: #388bfd; font-weight: 600; }
QPushButton#dupesSmall { padding: 3px 8px; font-size: 12px; }
QComboBox { background: #21262d; color: #e6edf3; border: 1px solid #30363d;
            border-radius: 8px; padding: 4px 8px; }
QComboBox QAbstractItemView { background: #161b22; color: #e6edf3; }
QCheckBox { color: #c9d1d9; }
QListWidget { background: #0e1116; color: #e6edf3; border: 1px solid #30363d; }
QScrollArea { border: none; background: #0e1116; }
QDialog { background: #0e1116; }
"""

BADGES = {"keep": ("#2ea043", "white", "✓ GARDER"),
          "trash": ("#da3633", "white", "🗑 CORBEILLE"),
          "free": ("#30363d", "#c9d1d9", "à décider")}


def _key(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _under(path: str, folder: str) -> bool:
    path, folder = _key(path), _key(folder).rstrip("\\/")
    return bool(folder) and (path == folder or path.startswith(folder + os.sep)
                             or path.startswith(folder + "/"))


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class Placement:
    """Ou se trouve une copie : sous quelle racine, dans quel dossier, et si ce
    dossier est un vrai rangement ou un dossier d'arrivee."""

    def __init__(self, path: str, tops: list, destinations: list):
        self.path = str(path)
        parent = str(Path(path).parent)
        self.folder = parent
        top = next((t for t in sorted(tops, key=len, reverse=True) if _under(path, t)), "")
        self.top = top
        if top:
            rest = parent[len(top.rstrip("\\/")):].strip("\\/")
            self.parts = [p for p in re.split(r"[\\/]+", rest) if p]
        else:
            self.parts = [p for p in re.split(r"[\\/]+", parent) if p][1:]
        self.in_destination = any(_under(path, d) for d in destinations if d)
        loose = not self.parts or any(_INBOX.search(p) for p in self.parts)
        # 2 : dans une destination de tri ; 1 : dans un dossier a soi ;
        # 0 : a la racine, ou dans un dossier d'arrivee.
        self.level = 2 if self.in_destination else (0 if loose else 1)

    @property
    def depth(self) -> int:
        return len(self.parts)

    @property
    def sorted(self) -> bool:
        return self.level > 0

    @property
    def root_name(self) -> str:
        top = self.top.rstrip("\\/")
        return re.split(r"[\\/]+", top)[-1] if top else ""

    def crumbs_html(self) -> str:
        """« Volume 3 › Lesbienne › Couple » : la racine en gris, chaque
        dossier en bleu, le dernier en blanc et gras."""
        sep = "<span style='color:#6e7681'>&nbsp;›&nbsp;</span>"
        bits = []
        if self.root_name:
            bits.append(f"<span style='color:#8b949e'>{_esc(self.root_name)}</span>")
        for n, part in enumerate(self.parts):
            last = n == len(self.parts) - 1
            bits.append(f"<span style='color:{'#ffffff' if last else '#79c0ff'};"
                        f"{'font-weight:700;' if last else ''}'>{_esc(part)}</span>")
        if not self.parts:
            bits.append("<span style='color:#f0b354;font-weight:700'>(à la racine)</span>")
        return "📁 " + sep.join(bits)

    @property
    def why(self) -> str:
        levels = f" · {self.depth} niveau{'x' if self.depth > 1 else ''}" if self.depth else ""
        if self.level == 2:
            return "rangée · destination de tri" + levels
        if self.level == 1:
            return "rangée" + levels
        if not self.parts:
            return "non rangée · à la racine"
        return "non rangée · dossier d'arrivée" + levels


class Group:
    """Un groupe et la decision prise pour chacune de ses copies."""

    def __init__(self, dupe, tops, destinations, fresh=frozenset()):
        self.dupe = dupe
        self.paths = [str(p) for p in dupe.paths]
        n = len(self.paths)
        self.sizes = list(getattr(dupe, "sizes", [0] * n))
        self.heights = list(getattr(dupe, "heights", [0] * n))
        self.durations = list(getattr(dupe, "durations", [0.0] * n))
        self.sure = bool(getattr(dupe, "sure", True))
        self.places = [Placement(p, tops, destinations) for p in self.paths]
        self.new = [_key(p) in fresh for p in self.paths]
        self.codecs = [""] * n
        # Une decision prise a la main n'est plus refaite par la regle quand
        # les mesures d'une copie arrivent.
        self.touched = False
        # Garder (True) / corbeille (False) / rien de decide (None).
        self.keep = [None] * n

    def __len__(self) -> int:
        return len(self.paths)

    def quality(self, i: int) -> tuple:
        """Le dernier recours : definition, poids, duree, chemin court."""
        return (self.heights[i] or 0, self.sizes[i] or 0, self.durations[i] or 0.0,
                -len(self.paths[i]))

    def bitrate(self, i: int) -> float:
        d = self.durations[i]
        return self.sizes[i] * 8 / d / 1e6 if d and self.sizes[i] else 0.0

    def score(self, criterion: str, i: int):
        """La valeur d'une copie pour ce critere : la plus grande gagne."""
        place = self.places[i]
        if criterion == "best":
            return self.heights[i] or 0
        if criterion == "deepest":
            return place.depth
        if criterion == "shallowest":
            return -place.depth
        if criterion == "sorted":
            return place.level
        if criterion == "bitrate":
            # A un demi-megabit pres : deux encodages semblables sont a egalite.
            return round(self.bitrate(i) * 2)
        if criterion == "biggest":
            return self.sizes[i] or 0
        if criterion == "smallest":
            return -(self.sizes[i] or 1 << 62)
        if criterion == "longest":
            return round(self.durations[i] or 0.0)
        return 0

    def keeper(self, criteria=DEFAULT_CRITERIA, prefer=()) -> int:
        """La copie a garder : celles des dossiers privilegies d'abord, puis
        chaque critere ne garde que les meilleures, le suivant departage."""
        left = list(range(len(self.paths)))
        inside = [i for i in left if any(_under(self.paths[i], f) for f in prefer if f)]
        if inside:
            left = inside
        for criterion in criteria:
            if not criterion or len(left) == 1:
                continue
            top = max(self.score(criterion, i) for i in left)
            left = [i for i in left if self.score(criterion, i) == top]
        return max(left, key=self.quality)

    def apply(self, criteria=DEFAULT_CRITERIA, prefer=()) -> None:
        k = self.keeper(criteria, prefer)
        self.keep = [i == k for i in range(len(self.paths))]

    @property
    def trashed(self) -> list:
        return [i for i, k in enumerate(self.keep) if k is False]

    @property
    def gain(self) -> int:
        return sum(self.sizes[i] for i in self.trashed)

    def has_loose(self) -> bool:
        return any(not p.sorted for p in self.places) and any(p.sorted for p in self.places)


class _Thumbs(QObject):
    """Les images des copies, fabriquees en fond, trois a la fois. Ce qui
    n'est plus a l'ecran (autre page) n'est pas fabrique."""

    ready = Signal(str, str)        # cle (chemin|instant), fichier image

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="prisme-doublons")
        self.asked: set = set()
        self.done: dict = {}
        self.wanted: set = set()
        self.ready.connect(self._remember)

    def _remember(self, key: str, file: str) -> None:
        self.done[key] = file

    def ask(self, video: str, ts: float) -> str:
        from . import media
        key = f"{video}|{ts:.2f}"
        self.wanted.add(key)
        if key in self.done:
            file = self.done[key]
            QTimer.singleShot(0, lambda: self.ready.emit(key, file))
            return key
        if key in self.asked:
            return key
        self.asked.add(key)
        hit = media.cached_thumb(Path(video), ts, THUMB_MADE)
        if hit is not None:
            QTimer.singleShot(0, lambda: self.ready.emit(key, str(hit)))
            return key

        def make() -> None:
            if key not in self.wanted:
                self.asked.discard(key)       # redemandee si on y revient
                return
            try:
                out = media.extract_thumb(Path(video), ts, THUMB_MADE, keyframe=True)
            except Exception:                                # noqa: BLE001
                out = None
            self.ready.emit(key, str(out) if out else "")
        self.pool.submit(make)
        return key

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


class _Facts(QObject):
    """Ce que l'index ne savait pas d'une copie (poids, definition, duree,
    codec) : releve en fond, deux fichiers a la fois."""

    ready = Signal(object, int, dict)      # groupe, copie, mesures

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prisme-doublons-infos")

    def ask(self, group, i: int) -> None:
        path = group.paths[i]

        def work() -> None:
            from . import media
            found: dict = {}
            try:
                found["size"] = os.stat(path).st_size
            except OSError:
                pass
            try:
                info = media.probe(Path(path)) or {}
                found.update(height=int(info.get("height") or 0),
                             duration=float(info.get("duration") or 0.0),
                             codec=str(info.get("codec") or ""))
            except Exception:                                # noqa: BLE001
                pass
            self.ready.emit(group, i, found)
        self.pool.submit(work)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


class _Picture(QLabel):
    clicked = Signal()

    def __init__(self, parent=None, w: int = THUMB_W, h: int = THUMB_H):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: #05070a; border-radius: 4px; color: #6e7681;")
        self.setText("…")
        self.setCursor(Qt.PointingHandCursor)

    def show_file(self, file: str) -> None:
        pix = QPixmap(file) if file else QPixmap()
        if pix.isNull():
            self.setText("—")
            return
        self.setPixmap(pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def mousePressEvent(self, event) -> None:              # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _Clickable(QLabel):
    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:              # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _tag(text: str, kind: str, parent=None) -> QLabel:
    label = QLabel(text, parent)
    back, ink = {"sorted": ("#1f3a2a", "#7ee2a8"), "loose": ("#3a2a14", "#f0b354"),
                 "new": ("#1c2f4a", "#79c0ff"), "doubt": ("#3a2a14", "#f0b354")}[kind]
    # Directement sur l'etiquette : la feuille de la fenetre n'y posait pas le fond.
    label.setStyleSheet(f"background: {back}; color: {ink}; border-radius: 6px; "
                        "padding: 1px 6px; font-size: 11px;")
    return label


def _restyle(widget, **props) -> None:
    for name, value in props.items():
        widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def moments_of(duration: float) -> list:
    """Les instants des trois images : les memes fractions pour chaque copie."""
    if duration and duration > 3:
        return [duration * f for f in MOMENTS]
    return []


class PreferDialog(QDialog):
    """Cocher autant de dossiers a privilegier qu'on veut."""

    def __init__(self, parent, folders: list, chosen: list):
        super().__init__(parent)
        self.setWindowTitle("Dossiers à privilégier")
        self.setStyleSheet(STYLE)
        self.resize(720, 520)
        lay = QVBoxLayout(self)
        said = QLabel("Une copie qui se trouve dans l'un de ces dossiers (ou dessous) est "
                      "gardée en priorité ; les critères départagent ensuite.", self)
        said.setWordWrap(True)
        said.setObjectName("dupesSub")
        lay.addWidget(said)
        self.list = QListWidget(self)
        lay.addWidget(self.list, 1)
        seen = set()
        for folder, count in folders:
            self._add(folder, f"{folder}   ({count})", folder in chosen)
            seen.add(folder)
        for folder in chosen:
            if folder not in seen:
                self._add(folder, folder, True)
        more = QPushButton("Ajouter un autre dossier…", self)
        more.clicked.connect(self._more)
        lay.addWidget(more)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _add(self, folder: str, text: str, on: bool) -> None:
        item = QListWidgetItem(text, self.list)
        item.setData(Qt.UserRole, folder)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _more(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Dossier à privilégier")
        if folder:
            self._add(os.path.normpath(folder), os.path.normpath(folder), True)

    def chosen(self) -> list:
        return [self.list.item(n).data(Qt.UserRole) for n in range(self.list.count())
                if self.list.item(n).checkState() == Qt.Checked]


class DupesReview(QWidget):
    """La fenetre des doublons."""

    def __init__(self, window, groups: list, fresh=(), title: str = ""):
        super().__init__(None, Qt.Window)
        self.window = window
        self.setObjectName("dupesRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.setWindowTitle(title or "Prisme — doublons")
        try:
            from .widgets import app_icon
            self.setWindowIcon(app_icon())
        except Exception:                                    # noqa: BLE001
            pass
        tops, dests = self._places_of(window)
        self.fresh = {_key(p) for p in fresh}
        self.groups = [Group(g, tops, dests, self.fresh) for g in groups if len(g.paths) >= 2]
        self.thumbs = _Thumbs(self)
        self.thumbs.ready.connect(self._thumb_ready)
        self.facts = _Facts(self)
        self.facts.ready.connect(self._facts_ready)
        self._pictures: dict = {}       # cle d'image -> [_Picture]
        self._strips: dict = {}         # (id groupe, copie) -> [_Picture] x 3
        self._infos: dict = {}          # (id groupe, copie) -> etiquette des mesures
        self._cards: list = []          # (groupe, [(cadre, badge)])
        self.prefer: list = []
        self._rule = (DEFAULT_CRITERIA, (), False)
        self.page = 0
        self._compare = None

        screen = (window.screen() if window is not None else None)
        room = screen.availableGeometry() if screen is not None else None
        self.resize(min(1400, room.width() - 60) if room else 1400,
                    min(950, room.height() - 80) if room else 950)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        self.title = QLabel("Doublons", self)
        self.title.setObjectName("dupesTitle")
        self.sub = QLabel("", self)
        self.sub.setObjectName("dupesSub")
        titles.addWidget(self.title)
        titles.addWidget(self.sub)
        head.addLayout(titles, 1)
        board = QPushButton("Voir en planche", self)
        board.setToolTip("L'ancienne présentation : toutes les copies sur la planche de Prisme")
        board.clicked.connect(self._as_board)
        head.addWidget(board)
        outer.addLayout(head)

        # -- les criteres --------------------------------------------------
        bar = QFrame(self)
        bar.setObjectName("dupesBar")
        grid = QGridLayout(bar)
        grid.setContentsMargins(12, 8, 12, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.addWidget(QLabel("<b>Garder</b> dans chaque groupe :", bar), 0, 0)
        self.criteria = []
        for n, wanted in enumerate(DEFAULT_CRITERIA):
            if n:
                grid.addWidget(QLabel("puis, à égalité :", bar), 0, 2 * n)
            pick = QComboBox(bar)
            if n:
                pick.addItem("—", "")
            for key, text in CRITERIA:
                pick.addItem(text, key)
            pick.setCurrentIndex(max(0, pick.findData(wanted)))
            self.criteria.append(pick)
            grid.addWidget(pick, 0, 2 * n + 1)
        apply = QPushButton("Appliquer à tous les groupes", bar)
        apply.setObjectName("dupesApply")
        apply.setToolTip("Dans chaque groupe : la copie choisie par les critères est gardée, "
                         "les autres passent en corbeille (rien ne part avant le bouton rouge)")
        apply.clicked.connect(self._apply_all)
        grid.addWidget(apply, 0, 6)

        self.prefer_button = QPushButton("Dossiers à privilégier : aucun", bar)
        self.prefer_button.setToolTip("Une copie dans l'un de ces dossiers passe avant les critères")
        self.prefer_button.clicked.connect(self._pick_prefer)
        grid.addWidget(self.prefer_button, 1, 0, 1, 2)
        self.doubtful = QCheckBox("Aussi les groupes « à vérifier »", bar)
        self.doubtful.setToolTip("Les groupes peu sûrs (une seule image commune, durée inconnue) "
                                 "ne sont pas touchés par les critères, sauf ici.")
        grid.addWidget(self.doubtful, 1, 2, 1, 2)
        grid.addWidget(QLabel("Montrer :", bar), 1, 4, Qt.AlignRight)
        self.show_pick = QComboBox(bar)
        for key, text in (("all", "Tous les groupes"), ("sure", "Les groupes sûrs"),
                          ("doubt", "Les groupes à vérifier"),
                          ("loose", "Une copie rangée + une non rangée"),
                          ("new", "Avec une vidéo nouvelle"),
                          ("todo", "Sans décision")):
            self.show_pick.addItem(text, key)
        if self.fresh:
            self.show_pick.setCurrentIndex(4)
        self.show_pick.currentIndexChanged.connect(lambda _i: self._rebuild(0))
        grid.addWidget(self.show_pick, 1, 5)
        reset = QPushButton("Annuler les choix", bar)
        reset.setToolTip("Toutes les copies repassent « à décider » : rien en corbeille")
        reset.clicked.connect(self._clear_all)
        grid.addWidget(reset, 1, 6)
        grid.setColumnStretch(7, 1)
        outer.addWidget(bar)

        # -- les groupes ---------------------------------------------------
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.viewport().setStyleSheet("background: #0e1116;")
        outer.addWidget(self.scroll, 1)

        # -- le pied -------------------------------------------------------
        foot = QHBoxLayout()
        self.prev_button = QPushButton("◂ Groupes précédents", self)
        self.prev_button.clicked.connect(lambda: self._rebuild(self.page - 1))
        self.next_button = QPushButton("Groupes suivants ▸", self)
        self.next_button.clicked.connect(lambda: self._rebuild(self.page + 1))
        self.page_label = QLabel("", self)
        self.page_label.setObjectName("dupesDim")
        foot.addWidget(self.prev_button)
        foot.addWidget(self.page_label)
        foot.addWidget(self.next_button)
        compare_all = QPushButton("▶ Comparer en vidéo, groupe par groupe", self)
        compare_all.clicked.connect(lambda: self._open_compare(None))
        foot.addWidget(compare_all)
        foot.addStretch(1)
        self.summary = QLabel("", self)
        foot.addWidget(self.summary)
        self.trash_button = QPushButton("", self)
        self.trash_button.setObjectName("dupesPrimary")
        self.trash_button.clicked.connect(self._trash)
        foot.addWidget(self.trash_button)
        outer.addLayout(foot)

        # D'office : les criteres par defaut sur chaque groupe sur ; un groupe
        # a verifier attend qu'on le regarde.
        for group in self.groups:
            if group.sure:
                group.apply(DEFAULT_CRITERIA)
        self._rebuild(0)
        # Le codec n'est jamais dans le groupe ; le reste, parfois pas.
        for group in self.groups:
            for i in range(len(group)):
                self.facts.ask(group, i)

    # -- donnees -------------------------------------------------------------
    @staticmethod
    def _places_of(window) -> tuple:
        tops, dests = [], []
        try:
            from . import roots
            top = window.top_root() if window is not None and window.root is not None else None
            if top is not None:
                tops = [str(m) for m in roots.members(top)] or [str(top)]
        except Exception:                                    # noqa: BLE001
            pass
        try:
            dests = [str(d.get("path") or "") for d in window.cfg.destinations]
        except Exception:                                    # noqa: BLE001
            pass
        return tops, [d for d in dests if d]

    def _folders(self) -> list:
        counts: dict = {}
        for group in self.groups:
            for place in group.places:
                counts[place.folder] = counts.get(place.folder, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:200]

    def _pick_prefer(self) -> None:
        dialog = PreferDialog(self, self._folders(), self.prefer)
        if dialog.exec() != QDialog.Accepted:
            return
        self.prefer = dialog.chosen()
        n = len(self.prefer)
        self.prefer_button.setText("Dossiers à privilégier : " + (
            "aucun" if not n else Path(self.prefer[0]).name if n == 1 else f"{n} dossiers"))
        self.prefer_button.setToolTip("\n".join(self.prefer) or
                                      "Une copie dans l'un de ces dossiers passe avant les critères")

    def _chosen_criteria(self) -> tuple:
        return tuple(c for c in (pick.currentData() for pick in self.criteria) if c)

    def _visible(self) -> list:
        mode = self.show_pick.currentData()
        out = []
        for group in self.groups:
            if mode == "sure" and not group.sure:
                continue
            if mode == "doubt" and group.sure:
                continue
            if mode == "loose" and not group.has_loose():
                continue
            if mode == "new" and not any(group.new):
                continue
            if mode == "todo" and any(k is not None for k in group.keep):
                continue
            out.append(group)
        return out

    # -- affichage -----------------------------------------------------------
    def _rebuild(self, page: int) -> None:
        shown = self._visible()
        pages = max(1, (len(shown) + PAGE - 1) // PAGE)
        self.page = max(0, min(page, pages - 1))
        part = shown[self.page * PAGE:(self.page + 1) * PAGE]
        self._pictures = {}
        self._strips = {}
        self._infos = {}
        self._cards = []
        self.thumbs.wanted = set()
        content = QWidget()
        content.setObjectName("dupesContent")
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 0, 8, 0)
        box.setSpacing(8)
        if not part:
            empty = QLabel("Aucun groupe à montrer ici." if self.groups
                           else "Plus aucun doublon à examiner.", content)
            empty.setObjectName("dupesSub")
            empty.setAlignment(Qt.AlignCenter)
            box.addWidget(empty)
        for number, group in enumerate(part, start=self.page * PAGE + 1):
            box.addWidget(self._group_card(content, group, number))
        box.addStretch(1)
        self.scroll.setWidget(content)
        self.scroll.verticalScrollBar().setValue(0)
        self.page_label.setText(f"page {self.page + 1} / {pages} · {len(shown)} groupe(s)")
        self.prev_button.setEnabled(self.page > 0)
        self.next_button.setEnabled(self.page < pages - 1)
        self._update_totals()

    def _group_card(self, parent, group: Group, number: int) -> QFrame:
        card = QFrame(parent)
        card.setObjectName("dupesGroup")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(5)
        head = QHBoxLayout()
        head.addWidget(QLabel(f"<b>Groupe {number}</b> · {len(group)} copies", card))
        if not group.sure:
            head.addWidget(_tag("à vérifier", "doubt", card))
        if any(group.new):
            head.addWidget(_tag("vidéo nouvelle", "new", card))
        head.addStretch(1)
        for text, tip, slot in (
                ("▶ Comparer en vidéo", "Lire les copies ensemble, au même instant",
                 lambda _c=False, g=group: self._open_compare(g)),
                ("Tout garder", "Ne rien supprimer dans ce groupe",
                 lambda _c=False, g=group: self._set_all(g, True)),
                ("Pas des doublons", "Ce groupe n'en est pas un : il quitte la liste et ne "
                 "reviendra plus", lambda _c=False, g=group: self._not_dupes(g))):
            button = QPushButton(text, card)
            button.setObjectName("dupesSmall")
            button.setToolTip(tip)
            button.clicked.connect(slot)
            head.addWidget(button)
        lay.addLayout(head)
        tiles = []
        for i in range(len(group)):
            tiles.append(self._copy_row(card, group, i))
            lay.addWidget(tiles[-1][0])
        self._cards.append((group, tiles))
        self._paint(group, tiles)
        return card

    def _copy_row(self, parent, group: Group, i: int) -> tuple:
        path = group.paths[i]
        place = group.places[i]
        row = QFrame(parent)
        row.setObjectName("dupesCopy")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(6, 4, 8, 4)
        lay.setSpacing(8)
        badge = _Clickable("", row)
        badge.setFixedWidth(108)
        badge.setAlignment(Qt.AlignCenter)
        badge.setToolTip("Clic : garder, ou mettre à la corbeille")
        badge.clicked.connect(lambda g=group, n=i: self._toggle(g, n))
        lay.addWidget(badge)
        strip = []
        for _slot in MOMENTS:
            picture = _Picture(row)
            picture.setToolTip("Les mêmes moments pour chaque copie (15, 50 et 85 %).\n"
                               "Clic : garder, ou mettre à la corbeille")
            picture.clicked.connect(lambda g=group, n=i: self._toggle(g, n))
            strip.append(picture)
            lay.addWidget(picture)
        self._strips[(id(group), i)] = strip
        self._ask_strip(group, i)

        text = QVBoxLayout()
        text.setSpacing(2)
        name = QLabel(f"<b>{_esc(Path(path).name)}</b>", row)
        name.setToolTip(path)
        name.setTextFormat(Qt.RichText)
        text.addWidget(name)
        crumbs = _Clickable("", row)
        crumbs.setTextFormat(Qt.RichText)
        crumbs.setText(place.crumbs_html())
        crumbs.setStyleSheet("font-size: 14px;")
        crumbs.setWordWrap(True)
        crumbs.setToolTip(f"{path}\n\nClic : montrer le fichier dans l'explorateur")
        crumbs.clicked.connect(lambda p=path: self._reveal(p))
        text.addWidget(crumbs)
        line = QHBoxLayout()
        line.setSpacing(6)
        line.addWidget(_tag(place.why, "sorted" if place.sorted else "loose", row))
        if group.new[i]:
            line.addWidget(_tag("nouvelle", "new", row))
        info = QLabel(self._facts_text(group, i), row)
        info.setTextFormat(Qt.RichText)
        self._infos[(id(group), i)] = info
        line.addWidget(info)
        line.addStretch(1)
        text.addLayout(line)
        lay.addLayout(text, 1)

        only = QPushButton("Garder celle-ci", row)
        only.setObjectName("dupesSmall")
        only.setToolTip("Garder cette copie, mettre les autres du groupe à la corbeille")
        only.clicked.connect(lambda _c=False, g=group, n=i: self._only(g, n))
        lay.addWidget(only)
        play = QPushButton("▶", row)
        play.setObjectName("dupesSmall")
        play.setToolTip("Ouvrir cette vidéo dans Prisme")
        play.clicked.connect(lambda _c=False, p=path: self._play(p))
        lay.addWidget(play)
        return row, badge

    def _ask_strip(self, group: Group, i: int) -> None:
        strip = self._strips.get((id(group), i))
        if not strip:
            return
        moments = moments_of(group.durations[i])
        if not moments:
            return              # la duree arrive avec les mesures
        for picture, ts in zip(strip, moments):
            if getattr(picture, "asked", None) == round(ts, 2):
                continue
            picture.asked = round(ts, 2)
            key = self.thumbs.ask(group.paths[i], ts)
            self._pictures.setdefault(key, []).append(picture)

    @staticmethod
    def _facts_text(group: Group, i: int) -> str:
        """Les mesures d'une copie, la meilleure de chaque mesure en vert."""
        def fact(text: str, best: bool) -> str:
            colour = "#7ee2a8" if best else "#c9d1d9"
            return f"<span style='color:{colour}'>{text}</span>"
        many = len(group) > 1
        best_h = max(group.heights or [0])
        best_s = max(group.sizes or [0])
        best_d = max(group.durations or [0.0])
        rates = [group.bitrate(j) for j in range(len(group))]
        h, s, d = group.heights[i], group.sizes[i], group.durations[i]
        rate, codec = rates[i], group.codecs[i]
        parts = [
            fact(human_resolution(h), many and h == best_h) if h else "",
            fact(human_size(s), many and s == best_s) if s else "",
            fact(human_duration(d), many and abs(d - best_d) < 1) if d else "",
            fact(f"{rate:.1f} Mb/s".replace(".", ","), many and rate == max(rates))
            if rate else "",
            fact(codec.upper(), False) if codec else "",
        ]
        return " · ".join(x for x in parts if x) or "<span style='color:#6e7681'>mesures…</span>"

    def _facts_ready(self, group: Group, i: int, found: dict) -> None:
        if not any(g is group for g in self.groups) or i >= len(group):
            return
        if found.get("size"):
            group.sizes[i] = int(found["size"])
        if found.get("height"):
            group.heights[i] = int(found["height"])
        if found.get("duration"):
            group.durations[i] = float(found["duration"])
        if found.get("codec"):
            group.codecs[i] = found["codec"]
        criteria, prefer, doubtful = self._rule
        if not group.touched and (group.sure or doubtful):
            group.apply(criteria, prefer)
        self._ask_strip(group, i)
        for j in range(len(group)):
            label = self._infos.get((id(group), j))
            if label is not None:
                try:
                    label.setText(self._facts_text(group, j))
                except RuntimeError:
                    pass
        self._repaint(group)

    def _paint(self, group: Group, tiles: list) -> None:
        for i, (row, badge) in enumerate(tiles):
            state = {True: "keep", False: "trash", None: "free"}[group.keep[i]]
            back, ink, text = BADGES[state]
            try:
                badge.setText(text)
                badge.setStyleSheet(f"background: {back}; color: {ink}; border-radius: 8px; "
                                    "padding: 6px 4px; font-weight: 700;")
                _restyle(row, state=state)
            except RuntimeError:
                pass

    def _repaint(self, group: Group) -> None:
        for known, tiles in self._cards:
            if known is group:
                self._paint(group, tiles)
        self._update_totals()

    def _thumb_ready(self, key: str, file: str) -> None:
        for picture in self._pictures.get(key, []):
            try:
                picture.show_file(file)
            except RuntimeError:            # carte deja remplacee
                pass

    def _update_totals(self) -> None:
        count = sum(len(g.trashed) for g in self.groups)
        gain = sum(g.gain for g in self.groups)
        copies = sum(len(g) - 1 for g in self.groups)
        self.title.setText(f"Doublons — {len(self.groups)} groupe(s)")
        self.sub.setText(
            f"{copies} copie(s) en trop au total. Clic sur une copie : garder ↔ corbeille. "
            "Clic sur un chemin : le fichier dans l'explorateur. Rien ne part sans le bouton "
            "rouge, et tout reste récupérable (Ctrl+B dans Prisme).")
        self.summary.setText(f"{count} copie(s) en corbeille · {human_size(gain)} libérés"
                             if count else "Aucune copie en corbeille")
        self.trash_button.setText(f"Mettre {count} copie(s) à la corbeille" if count
                                  else "Mettre à la corbeille")
        self.trash_button.setEnabled(bool(count))

    # -- gestes --------------------------------------------------------------
    def _toggle(self, group: Group, i: int) -> None:
        group.touched = True
        if group.keep[i] is False:
            group.keep[i] = True
        else:
            if sum(1 for k in group.keep if k is not False) <= 1:
                return self._say_last()
            group.keep[i] = False
            # La premiere decision d'un groupe : le reste est garde.
            group.keep = [True if k is None else k for k in group.keep]
        self._repaint(group)

    def _say_last(self) -> None:
        self.summary.setText("Un groupe garde toujours au moins une copie.")
        QTimer.singleShot(2500, self._update_totals)

    def _only(self, group: Group, i: int) -> None:
        group.touched = True
        group.keep = [j == i for j in range(len(group))]
        self._repaint(group)

    def _set_all(self, group: Group, value) -> None:
        group.touched = True
        group.keep = [value] * len(group)
        self._repaint(group)

    def _apply_all(self) -> None:
        criteria = self._chosen_criteria() or DEFAULT_CRITERIA
        doubtful = self.doubtful.isChecked()
        self._rule = (criteria, tuple(self.prefer), doubtful)
        for group in self.groups:
            if group.sure or doubtful:
                group.touched = False
                group.apply(criteria, self.prefer)
        self._rebuild(self.page)

    def _clear_all(self) -> None:
        for group in self.groups:
            group.touched = True
            group.keep = [None] * len(group)
        self._rebuild(self.page)

    def _play(self, path: str) -> None:
        window = self.window
        if window is None:
            return
        try:
            window.open_video_path(path)
            window.raise_()
            window.activateWindow()
        except Exception:                                    # noqa: BLE001
            pass

    def _reveal(self, path: str) -> None:
        try:
            self.window.reveal_path(path)
        except Exception:                                    # noqa: BLE001
            pass

    def _not_dupes(self, group: Group) -> None:
        from .dupes_memory import NOT_DUPES
        for at, left in enumerate(group.paths):
            for right in group.paths[at + 1:]:
                NOT_DUPES.ignorer(left, right, save=False)
        NOT_DUPES.save()
        self.groups = [g for g in self.groups if g is not group]
        self._rebuild(self.page)

    def _as_board(self) -> None:
        try:
            self.window.show_found_dupes_board()
            self.window.raise_()
        except Exception:                                    # noqa: BLE001
            pass

    def _trash(self) -> None:
        window = self.window
        for group in self.groups:
            if group.trashed and len(group.trashed) >= len(group):
                return self._say_last()
        targets = [(g, i) for g in self.groups for i in g.trashed]
        if not targets or window is None:
            return
        from .scan import MODE_FILES, Item
        items = []
        for group, i in targets:
            path = Path(group.paths[i])
            items.append(Item(path=path, kind=MODE_FILES, videos=[path],
                              video_count=1, file_count=1))
        sent = window._delete_items(items)
        if not sent:
            return
        gone = {_key(p) for p in window._gone_paths()} | {_key(it.path) for it in items}
        kept = []
        for group in self.groups:
            left = [i for i, p in enumerate(group.paths) if _key(p) not in gone]
            if len(left) >= 2:
                if len(left) != len(group):
                    kept.append(Group(group.dupe.subset(left), *self._places_of(window),
                                      self.fresh))
                else:
                    kept.append(group)
        self.groups = kept
        self._rebuild(self.page)

    def _open_compare(self, group) -> None:
        shown = self._visible()
        if not shown:
            return
        if self._compare is not None:
            try:
                self._compare.close()
            except RuntimeError:
                pass
        start = next((n for n, g in enumerate(shown) if g is group), 0)
        self._compare = CompareDialog(self, shown, start)
        self._compare.show()

    def closeEvent(self, event) -> None:                    # noqa: N802
        self.thumbs.close()
        self.facts.close()
        if self._compare is not None:
            try:
                self._compare.close()
            except RuntimeError:
                pass
        super().closeEvent(event)


class CompareDialog(QDialog):
    """Les copies d'un groupe lues ensemble, au meme instant, sans le son ;
    ◂ ▸ passent au groupe precedent ou suivant. « Garder celle-ci » decide et
    enchaine : on juge tous les groupes douteux a la suite."""

    START = 0.33

    def __init__(self, review: DupesReview, groups: list, start: int = 0):
        super().__init__(review)
        self.review = review
        self.groups = groups
        self.at = max(0, min(start, len(groups) - 1))
        self.players = []
        self.setWindowTitle("Comparer les copies")
        self.setStyleSheet(STYLE)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.prev = QPushButton("◂ Groupe précédent", self)
        self.prev.clicked.connect(lambda: self.show_group(self.at - 1))
        self.where = QLabel("", self)
        self.where.setAlignment(Qt.AlignCenter)
        self.next = QPushButton("Groupe suivant ▸", self)
        self.next.clicked.connect(lambda: self.show_group(self.at + 1))
        top.addWidget(self.prev)
        top.addWidget(self.where, 1)
        top.addWidget(self.next)
        lay.addLayout(top)
        self.stage = QWidget(self)
        self.stage_lay = QHBoxLayout(self.stage)
        self.stage_lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stage, 1)
        controls = QHBoxLayout()
        self.pause = QPushButton("⏸", self)
        self.pause.setFixedWidth(48)
        self.pause.clicked.connect(self._toggle)
        controls.addWidget(self.pause)
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._seek)
        controls.addWidget(self.slider, 1)
        hint = QLabel("Toutes les copies avancent ensemble · ← → groupes · Espace pause", self)
        hint.setObjectName("dupesDim")
        controls.addWidget(hint)
        lay.addLayout(controls)
        self.resize(1300, 640)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._follow)
        self.timer.start()
        self._playing = True
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.show_group(self.at + 1))
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.show_group(self.at - 1))
        QShortcut(QKeySequence(Qt.Key_Space), self, self._toggle)
        self.show_group(self.at)

    def _clear(self) -> None:
        self.timer.stop()
        for player, _a in self.players:
            player.stop()
            player.setSource(QUrl())
        self.players = []
        while self.stage_lay.count():
            item = self.stage_lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def show_group(self, at: int) -> None:
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        if not self.groups or at < 0 or at >= len(self.groups):
            return
        self._clear()
        self.at = at
        group = self.groups[at]
        self.where.setText(f"<b>Groupe {at + 1} / {len(self.groups)}</b> · "
                           f"{len(group)} copies" + ("" if group.sure else " · à vérifier"))
        self.prev.setEnabled(at > 0)
        self.next.setEnabled(at < len(self.groups) - 1)
        for i in range(min(4, len(group))):
            column = QWidget(self.stage)
            col = QVBoxLayout(column)
            col.setContentsMargins(4, 0, 4, 0)
            video = QVideoWidget(column)
            video.setMinimumSize(300, 170)
            video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            player = QMediaPlayer(column)
            audio = QAudioOutput(column)
            audio.setMuted(True)
            player.setAudioOutput(audio)
            player.setVideoOutput(video)
            # Chaque copie part du meme endroit des que sa duree est connue :
            # demande trop tot, le saut etait perdu et tout partait du debut.
            player.durationChanged.connect(
                lambda length, p=player: self._first_seek(p, length))
            player.setSource(QUrl.fromLocalFile(group.paths[i]))
            self.players.append((player, audio))
            col.addWidget(video, 1)
            place = group.places[i]
            label = QLabel(f"<b>{_esc(Path(group.paths[i]).name)}</b><br>"
                           f"{place.crumbs_html()}<br>"
                           f"<span style='color:#8b949e'>{_esc(place.why)}</span><br>"
                           + DupesReview._facts_text(group, i), column)
            label.setTextFormat(Qt.RichText)
            label.setWordWrap(True)
            col.addWidget(label)
            buttons = QHBoxLayout()
            state = {True: "keep", False: "trash", None: "free"}[group.keep[i]]
            keep = QPushButton("✓ Gardée" if state == "keep" else "Garder celle-ci", column)
            keep.setObjectName("dupesApply")
            keep.setToolTip("Garder celle-ci, les autres en corbeille, puis groupe suivant")
            keep.clicked.connect(lambda _c=False, n=i: self._decide(n))
            buttons.addWidget(keep, 1)
            sound = QPushButton("🔇", column)
            sound.setFixedWidth(44)
            sound.setToolTip("Le son de cette copie seulement")
            sound.clicked.connect(lambda _c=False, a=audio, b=sound: self._sound(a, b))
            buttons.addWidget(sound)
            col.addLayout(buttons)
            self.stage_lay.addWidget(column, 1)
        both = QPushButton("Tout garder", self.stage)
        both.setToolTip("Ce groupe garde toutes ses copies ; groupe suivant")
        both.clicked.connect(self._keep_all)
        self.stage_lay.addWidget(both, 0, Qt.AlignBottom)
        self._playing = True
        self.pause.setText("⏸")
        for player, _a in self.players:
            player.play()
        self.timer.start()

    def _first_seek(self, player, length: int) -> None:
        if length > 0 and not getattr(player, "_placed", False):
            player._placed = True
            player.setPosition(int(length * self.START))

    def _sound(self, audio, button) -> None:
        on = audio.isMuted()
        for _p, other in self.players:
            other.setMuted(True)
        audio.setMuted(not on)
        button.setText("🔊" if on else "🔇")

    def _toggle(self) -> None:
        self._playing = not self._playing
        for player, _a in self.players:
            (player.play if self._playing else player.pause)()
        self.pause.setText("⏸" if self._playing else "▶")

    def _seek(self, value: int) -> None:
        for player, _a in self.players:
            length = player.duration()
            if length > 0:
                player.setPosition(int(length * value / 1000))

    def _follow(self) -> None:
        if self.slider.isSliderDown() or not self.players:
            return
        player = self.players[0][0]
        if player.duration() > 0:
            self.slider.setValue(int(1000 * player.position() / player.duration()))

    def _decide(self, i: int) -> None:
        self.review._only(self.groups[self.at], i)
        self._advance()

    def _keep_all(self) -> None:
        self.review._set_all(self.groups[self.at], True)
        self._advance()

    def _advance(self) -> None:
        if self.at < len(self.groups) - 1:
            self.show_group(self.at + 1)
        else:
            self.close()

    def closeEvent(self, event) -> None:                    # noqa: N802
        self._clear()
        super().closeEvent(event)
