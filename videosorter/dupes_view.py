"""La revue des doublons : chaque groupe cote a cote, et ce qu'on en garde.

La planche cochee d'office ne disait ni ou etait chaque copie, ni pourquoi
l'une etait gardee plutot que l'autre. Ici, chaque copie montre son image, son
dossier (rangee ou non), sa definition, son poids et sa duree, le meilleur de
chaque mesure en vert. Une regle choisit d'un clic la copie a garder dans tous
les groupes : la meilleure qualite, la plus grosse, la plus petite, celle deja
rangee, ou celle d'un dossier qu'on privilegie. Un clic sur une copie la fait
passer de « garder » a « corbeille » ; « Comparer » lit les copies ensemble,
au meme instant, pour juger a l'oeil.

Rien ne part sans le bouton du bas, et tout passe par la corbeille de la
seance (Ctrl+B pour reprendre). Un groupe garde toujours au moins une copie.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from .scan import human_duration, human_resolution, human_size

THUMB_W = 320
THUMB_H = 180
PAGE = 25

# Les dossiers d'arrivee : une copie qui y dort n'est pas encore rangee.
_INBOX = re.compile(
    r"(trier|tri\b|t[ée]l[ée]charg|download|nouveau|nouvelles?|inbox|arriv|import|"
    r"vrac|divers|temp|tmp|a classer|à classer|en attente|telephone|téléphone)",
    re.IGNORECASE)

RULES = (
    ("best", "Meilleure qualité (définition, puis poids)"),
    ("sorted", "Celle déjà rangée dans un dossier"),
    ("biggest", "Le plus gros fichier"),
    ("smallest", "Le plus petit fichier (gagner de la place)"),
    ("shortest_path", "Le chemin le plus court"),
)

STYLE = """
QWidget#dupesRoot, QWidget#dupesContent { background: #0e1116; color: #e6edf3; }
QLabel { color: #e6edf3; }
QLabel#dupesTitle { font-size: 20px; font-weight: 600; }
QLabel#dupesSub, QLabel#dupesDim { color: #8b949e; }
QFrame#dupesBar { background: #161b22; border: 1px solid #262c36; border-radius: 10px; }
QFrame#dupesGroup { background: #131820; border: 1px solid #262c36; border-radius: 12px; }
QFrame#dupesCopy { background: #0e1116; border: 2px solid #262c36; border-radius: 10px; }
QFrame#dupesCopy[state="keep"] { border-color: #2ea043; }
QFrame#dupesCopy[state="trash"] { border-color: #da3633; background: #1a1012; }
QLabel#dupesBadge { border-radius: 8px; padding: 2px 8px; font-weight: 600; }
QLabel#dupesBadge[state="keep"] { background: #2ea043; color: white; }
QLabel#dupesBadge[state="trash"] { background: #da3633; color: white; }
QLabel#dupesBadge[state="free"] { background: #30363d; color: #c9d1d9; }
QLabel#dupesFolder { color: #79c0ff; }
QLabel#dupesFolder:hover { text-decoration: underline; }
QLabel#dupesTag { border-radius: 6px; padding: 1px 6px; font-size: 11px; }
QLabel#dupesTag[kind="sorted"] { background: #1f3a2a; color: #7ee2a8; }
QLabel#dupesTag[kind="loose"] { background: #3a2a14; color: #f0b354; }
QLabel#dupesTag[kind="new"] { background: #1c2f4a; color: #79c0ff; }
QLabel#dupesTag[kind="doubt"] { background: #3a2a14; color: #f0b354; }
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
QScrollArea { border: none; background: #0e1116; }
"""


def _key(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _under(path: str, folder: str) -> bool:
    path, folder = _key(path), _key(folder).rstrip("\\/")
    return bool(folder) and (path == folder or path.startswith(folder + os.sep)
                             or path.startswith(folder + "/"))


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
            self.parts = [p for p in re.split(r"[\\/]+", parent) if p][-2:]
        self.in_destination = any(_under(path, d) for d in destinations if d)
        loose = not self.parts or any(_INBOX.search(p) for p in self.parts)
        # 2 : dans une destination de tri ; 1 : dans un dossier a soi ;
        # 0 : a la racine, ou dans un dossier d'arrivee.
        self.level = 2 if self.in_destination else (0 if loose else 1)

    @property
    def sorted(self) -> bool:
        return self.level > 0

    @property
    def shown(self) -> str:
        """Le dossier, relatif a la racine : « Lesbienne › Couple »."""
        if self.top and not self.parts:
            return "racine de la collection"
        return " › ".join(self.parts) if self.parts else self.folder

    @property
    def why(self) -> str:
        if self.level == 2:
            return "rangée · destination de tri"
        if self.level == 1:
            return "rangée"
        if not self.parts:
            return "non rangée · à la racine"
        return "non rangée · dossier d'arrivée"


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
        """La definition, puis le poids (a definition egale, moins
        compresse), puis la duree, puis le chemin le plus court."""
        return (self.heights[i] or 0, self.sizes[i] or 0, self.durations[i] or 0.0,
                -len(self.paths[i]))

    def bitrate(self, i: int) -> float:
        d = self.durations[i]
        return self.sizes[i] * 8 / d / 1e6 if d and self.sizes[i] else 0.0

    def keeper(self, rule: str, prefer: str = "") -> int:
        n = len(self.paths)
        order = sorted(range(n), key=self.quality, reverse=True)
        if prefer:
            inside = [i for i in order if _under(self.paths[i], prefer)]
            if inside:
                order = inside
        if rule == "biggest":
            return max(order, key=lambda i: (self.sizes[i], self.quality(i)))
        if rule == "smallest":
            return min(order, key=lambda i: (self.sizes[i] or 1 << 62, len(self.paths[i])))
        if rule == "sorted":
            return max(order, key=lambda i: (self.places[i].level,
                                             len(self.places[i].parts), self.quality(i)))
        if rule == "shortest_path":
            return min(order, key=lambda i: len(self.paths[i]))
        return order[0]

    def apply(self, rule: str, prefer: str = "") -> None:
        k = self.keeper(rule, prefer)
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
    """Les images des copies, fabriquees en fond, trois a la fois."""

    ready = Signal(str, str)        # cle (chemin|instant), fichier image

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="prisme-doublons")
        self.asked: set = set()
        self.done: dict = {}
        self.ready.connect(self._remember)

    def _remember(self, key: str, file: str) -> None:
        self.done[key] = file

    def ask(self, video: str, ts: float | None = None, width: int = THUMB_W) -> str:
        from . import media
        if ts is None:
            ts = media.card_moment(video)
        key = f"{video}|{ts:.2f}|{width}"
        if key in self.done:
            # Deja faite : la carte refaite (page, filtre) la reprend.
            file = self.done[key]
            QTimer.singleShot(0, lambda: self.ready.emit(key, file))
            return key
        if key in self.asked:
            return key
        self.asked.add(key)
        hit = media.cached_thumb(Path(video), ts, width)
        if hit is not None:
            QTimer.singleShot(0, lambda: self.ready.emit(key, str(hit)))
            return key

        def make() -> None:
            try:
                out = media.extract_thumb(Path(video), ts, width, keyframe=True)
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
        self.setStyleSheet("background: #05070a; border-radius: 6px; color: #6e7681;")
        self.setText("…")
        self.setCursor(Qt.PointingHandCursor)

    def show_file(self, file: str) -> None:
        if not file:
            self.setText("image indisponible")
            return
        pix = QPixmap(file)
        if pix.isNull():
            self.setText("image indisponible")
            return
        self.setPixmap(pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def mousePressEvent(self, event) -> None:              # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _Link(QLabel):
    clicked = Signal()

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("dupesFolder")
        self.setCursor(Qt.PointingHandCursor)
        self.setWordWrap(True)

    def mousePressEvent(self, event) -> None:              # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _restyle(widget, **props) -> None:
    for name, value in props.items():
        widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _tag(text: str, kind: str, parent=None) -> QLabel:
    label = QLabel(text, parent)
    back, ink = {"sorted": ("#1f3a2a", "#7ee2a8"), "loose": ("#3a2a14", "#f0b354"),
                 "new": ("#1c2f4a", "#79c0ff"), "doubt": ("#3a2a14", "#f0b354")}[kind]
    # Directement sur l'etiquette : la feuille de la fenetre n'y posait pas le fond.
    label.setStyleSheet(f"background: {back}; color: {ink}; border-radius: 6px; "
                        "padding: 1px 6px; font-size: 11px;")
    return label


BADGES = {"keep": ("#2ea043", "white", "✓ GARDER"),
          "trash": ("#da3633", "white", "🗑 CORBEILLE"),
          "free": ("#30363d", "#c9d1d9", "à décider")}


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
        self._infos: dict = {}          # (id groupe, copie) -> etiquette des mesures
        self._rule = ("best", "", False)
        self._pictures: dict = {}       # cle d'image -> [_Picture]
        self._cards: list = []          # (groupe, [(cadre, badge)])
        self.page = 0
        self._compare = None

        screen = (window.screen() if window is not None else None)
        room = screen.availableGeometry() if screen is not None else None
        self.resize(min(1400, room.width() - 60) if room else 1400,
                    min(900, room.height() - 80) if room else 900)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)

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

        # -- les regles ----------------------------------------------------
        bar = QFrame(self)
        bar.setObjectName("dupesBar")
        grid = QGridLayout(bar)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(10)
        grid.addWidget(QLabel("Dans chaque groupe, garder :", bar), 0, 0)
        self.rule = QComboBox(bar)
        for key, text in RULES:
            self.rule.addItem(text, key)
        grid.addWidget(self.rule, 0, 1)
        grid.addWidget(QLabel("Dossier à privilégier :", bar), 0, 2)
        self.prefer = QComboBox(bar)
        self.prefer.setMinimumWidth(260)
        self._fill_prefer()
        self.prefer.activated.connect(self._prefer_picked)
        grid.addWidget(self.prefer, 0, 3)
        self.doubtful = QCheckBox("Aussi les groupes « à vérifier »", bar)
        self.doubtful.setToolTip("Les groupes trop peu sûrs (une seule image commune, durée "
                                 "inconnue) ne sont pas touchés par la règle, sauf ici.")
        grid.addWidget(self.doubtful, 1, 1)
        apply = QPushButton("Appliquer à tous les groupes", bar)
        apply.setObjectName("dupesApply")
        apply.clicked.connect(self._apply_all)
        grid.addWidget(apply, 0, 4)
        clear = QPushButton("Tout décocher", bar)
        clear.clicked.connect(self._clear_all)
        grid.addWidget(clear, 1, 4)
        grid.addWidget(QLabel("Montrer :", bar), 1, 2)
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
        grid.addWidget(self.show_pick, 1, 3)
        grid.setColumnStretch(5, 1)
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
        foot.addStretch(1)
        self.summary = QLabel("", self)
        foot.addWidget(self.summary)
        self.trash_button = QPushButton("", self)
        self.trash_button.setObjectName("dupesPrimary")
        self.trash_button.clicked.connect(self._trash)
        foot.addWidget(self.trash_button)
        outer.addLayout(foot)

        # D'office : la meilleure copie de chaque groupe sur est gardee, les
        # autres vont a la corbeille ; un groupe a verifier attend.
        for group in self.groups:
            if group.sure:
                group.apply("best")
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

    def _fill_prefer(self, chosen: str = "") -> None:
        counts: dict = {}
        for group in self.groups:
            for place in group.places:
                counts[place.folder] = counts.get(place.folder, 0) + 1
        self.prefer.blockSignals(True)
        self.prefer.clear()
        self.prefer.addItem("Aucun", "")
        for folder, n in sorted(counts.items(), key=lambda kv: -kv[1])[:40]:
            shown = folder if len(folder) < 70 else "…" + folder[-68:]
            self.prefer.addItem(f"{shown}  ({n})", folder)
        if chosen and self.prefer.findData(chosen) < 0:
            self.prefer.addItem(chosen, chosen)
        self.prefer.addItem("Choisir un autre dossier…", "?")
        at = self.prefer.findData(chosen) if chosen else 0
        self.prefer.setCurrentIndex(max(0, at))
        self.prefer.blockSignals(False)

    def _prefer_picked(self, _index: int) -> None:
        if self.prefer.currentData() != "?":
            return
        start = self.groups[0].places[0].top if self.groups else ""
        folder = QFileDialog.getExistingDirectory(self, "Dossier à privilégier", start)
        self._fill_prefer(folder or "")

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
        self._cards = []
        self._infos = {}
        content = QWidget()
        content.setObjectName("dupesContent")
        box = QVBoxLayout(content)
        box.setContentsMargins(0, 0, 8, 0)
        box.setSpacing(12)
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
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel(f"<b>Groupe {number}</b> · {len(group)} copies", card)
        head.addWidget(title)
        if not group.sure:
            head.addWidget(_tag("à vérifier", "doubt", card))
        if any(group.new):
            head.addWidget(_tag("vidéo nouvelle", "new", card))
        head.addStretch(1)
        compare = QPushButton("Comparer en vidéo", card)
        compare.setObjectName("dupesSmall")
        compare.clicked.connect(lambda _c=False, g=group: self._open_compare(g))
        head.addWidget(compare)
        keep_all = QPushButton("Tout garder", card)
        keep_all.setObjectName("dupesSmall")
        keep_all.setToolTip("Ne rien supprimer dans ce groupe pour l'instant")
        keep_all.clicked.connect(lambda _c=False, g=group: self._set_all(g, None))
        head.addWidget(keep_all)
        not_dupes = QPushButton("Pas des doublons", card)
        not_dupes.setObjectName("dupesSmall")
        not_dupes.setToolTip("Ce groupe n'en est pas un : il quitte la liste et ne reviendra plus")
        not_dupes.clicked.connect(lambda _c=False, g=group: self._not_dupes(g))
        head.addWidget(not_dupes)
        lay.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(12)
        tiles = []
        for i, path in enumerate(group.paths):
            tile, badge = self._copy_tile(card, group, i)
            tiles.append((tile, badge))
            row.addWidget(tile)
        row.addStretch(1)
        lay.addLayout(row)
        self._cards.append((group, tiles))
        self._paint(group, tiles)
        return card

    def _copy_tile(self, parent, group: Group, i: int) -> tuple:
        path = group.paths[i]
        place = group.places[i]
        tile = QFrame(parent)
        tile.setObjectName("dupesCopy")
        tile.setFixedWidth(THUMB_W + 20)
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)
        picture = _Picture(tile)
        picture.setToolTip("Clic : garder, ou mettre à la corbeille")
        picture.clicked.connect(lambda g=group, n=i: self._toggle(g, n))
        key = self.thumbs.ask(path)
        self._pictures.setdefault(key, []).append(picture)
        lay.addWidget(picture)

        top = QHBoxLayout()
        badge = QLabel("", tile)
        badge.setObjectName("dupesBadge")
        top.addWidget(badge)
        top.addStretch(1)
        only = QPushButton("Garder celle-ci", tile)
        only.setObjectName("dupesSmall")
        only.setToolTip("Garder cette copie, mettre les autres du groupe à la corbeille")
        only.clicked.connect(lambda _c=False, g=group, n=i: self._only(g, n))
        top.addWidget(only)
        play = QPushButton("▶", tile)
        play.setObjectName("dupesSmall")
        play.setToolTip("Ouvrir cette vidéo dans Prisme")
        play.clicked.connect(lambda _c=False, p=path: self._play(p))
        top.addWidget(play)
        lay.addLayout(top)

        name = QLabel(Path(path).name, tile)
        name.setWordWrap(True)
        name.setToolTip(path)
        name.setStyleSheet("font-weight: 600;")
        lay.addWidget(name)
        folder = _Link("📁 " + place.shown, tile)
        folder.setToolTip(f"{place.folder}\n\nClic : montrer le fichier dans l'explorateur")
        folder.clicked.connect(lambda p=path: self._reveal(p))
        lay.addWidget(folder)
        tags = QHBoxLayout()
        tags.addWidget(_tag(place.why, "sorted" if place.sorted else "loose", tile))
        if group.new[i]:
            tags.addWidget(_tag("nouvelle", "new", tile))
        tags.addStretch(1)
        lay.addLayout(tags)

        info = QLabel(self._facts_text(group, i), tile)
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        self._infos[(id(group), i)] = info
        lay.addWidget(info)
        return tile, badge

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
        return " · ".join(x for x in parts if x) or "mesures en cours…"

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
        rule, prefer, doubtful = self._rule
        if not group.touched and (group.sure or doubtful):
            group.apply(rule, prefer)
        for j in range(len(group)):
            label = self._infos.get((id(group), j))
            if label is not None:
                try:
                    label.setText(self._facts_text(group, j))
                except RuntimeError:
                    pass
        self._repaint(group)

    def _paint(self, group: Group, tiles: list) -> None:
        for i, (tile, badge) in enumerate(tiles):
            state = {True: "keep", False: "trash", None: "free"}[group.keep[i]]
            back, ink, text = BADGES[state]
            badge.setText(text)
            badge.setStyleSheet(f"background: {back}; color: {ink}; border-radius: 8px; "
                                "padding: 2px 8px; font-weight: 600;")
            _restyle(tile, state=state)

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
            f"{copies} copie(s) en trop au total. Clic sur une image : garder ↔ corbeille. "
            "Le dossier de chaque copie s'ouvre d'un clic. Rien ne part sans le bouton rouge, "
            "et tout reste récupérable (Ctrl+B dans Prisme).")
        self.summary.setText(f"{count} copie(s) cochée(s) · {human_size(gain)} libérés"
                             if count else "Aucune copie cochée")
        self.trash_button.setText(f"Mettre {count} copie(s) à la corbeille" if count
                                  else "Mettre à la corbeille")
        self.trash_button.setEnabled(bool(count))

    # -- gestes --------------------------------------------------------------
    def _toggle(self, group: Group, i: int) -> None:
        group.touched = True
        now = group.keep[i]
        if now is False:
            group.keep[i] = True
        else:
            if sum(1 for k in group.keep if k is not False) <= 1:
                self._say_last()
                return
            group.keep[i] = False
            # La premiere decision d'un groupe : le reste est garde.
            for j, k in enumerate(group.keep):
                if k is None:
                    group.keep[j] = True
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
        rule = self.rule.currentData() or "best"
        prefer = self.prefer.currentData() or ""
        if prefer == "?":
            prefer = ""
        doubtful = self.doubtful.isChecked()
        self._rule = (rule, prefer, doubtful)
        for group in self.groups:
            if group.sure or doubtful:
                group.touched = False
                group.apply(rule, prefer)
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
        targets = [(g, i) for g in self.groups for i in g.trashed]
        # Jamais un groupe entier : la derniere copie reste, quoi qu'on ait coche.
        for group in self.groups:
            if group.trashed and len(group.trashed) >= len(group):
                return self._say_last()
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
                    sub = Group(group.dupe.subset(left), *self._places_of(window), self.fresh)
                    kept.append(sub)
                else:
                    kept.append(group)
        self.groups = kept
        self._fill_prefer(self.prefer.currentData() if self.prefer.currentData() != "?" else "")
        self._rebuild(self.page)

    def _open_compare(self, group: Group) -> None:
        if self._compare is not None:
            try:
                self._compare.close()
            except RuntimeError:
                pass
        self._compare = CompareDialog(self, group)
        self._compare.decided.connect(lambda i, g=group: self._only(g, i))
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
    """Les copies d'un groupe lues ensemble, au meme instant, sans le son :
    la difference de qualite se voit d'un coup d'oeil."""

    decided = Signal(int)

    def __init__(self, review: DupesReview, group: Group):
        super().__init__(review)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        self.setWindowTitle("Comparer les copies")
        self.setStyleSheet(STYLE + "QDialog { background: #0e1116; }")
        self.group = group
        self.players = []
        shown = list(range(len(group)))[:4]
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        best_h = max(group.heights or [0])
        best_s = max(group.sizes or [0])
        for i in shown:
            col = QVBoxLayout()
            video = QVideoWidget(self)
            video.setMinimumSize(400, 225)
            video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            player = QMediaPlayer(self)
            audio = QAudioOutput(self)
            audio.setMuted(True)
            player.setAudioOutput(audio)
            player.setVideoOutput(video)
            player.setSource(QUrl.fromLocalFile(group.paths[i]))
            self.players.append((player, audio))
            col.addWidget(video, 1)
            h, s = group.heights[i], group.sizes[i]
            good = "#7ee2a8"
            facts = []
            if h:
                facts.append(f"<span style='color:{good if h == best_h else '#c9d1d9'}'>"
                             f"{human_resolution(h)}</span>")
            if s:
                facts.append(f"<span style='color:{good if s == best_s else '#c9d1d9'}'>"
                             f"{human_size(s)}</span>")
            if group.durations[i]:
                facts.append(human_duration(group.durations[i]))
            name = QLabel(f"<b>{Path(group.paths[i]).name}</b><br>"
                          f"<span style='color:#79c0ff'>📁 {group.places[i].shown}</span>"
                          f" · {group.places[i].why}<br>" + " · ".join(facts), self)
            name.setTextFormat(Qt.RichText)
            name.setWordWrap(True)
            col.addWidget(name)
            buttons = QHBoxLayout()
            keep = QPushButton("Garder celle-ci", self)
            keep.setObjectName("dupesApply")
            keep.clicked.connect(lambda _c=False, n=i: self._decide(n))
            buttons.addWidget(keep)
            sound = QPushButton("🔇", self)
            sound.setToolTip("Le son de cette copie seulement")
            sound.setFixedWidth(44)
            sound.clicked.connect(lambda _c=False, a=audio, b=sound: self._sound(a, b))
            buttons.addWidget(sound)
            col.addLayout(buttons)
            row.addLayout(col, 1)
        lay.addLayout(row, 1)
        controls = QHBoxLayout()
        self.pause = QPushButton("⏸", self)
        self.pause.clicked.connect(self._toggle)
        controls.addWidget(self.pause)
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._seek)
        controls.addWidget(self.slider, 1)
        hint = QLabel("Toutes les copies avancent ensemble", self)
        hint.setObjectName("dupesDim")
        controls.addWidget(hint)
        lay.addLayout(controls)
        self.resize(min(1600, 460 * len(shown) + 40), 560)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._follow)
        self.timer.start()
        self._playing = True
        # Les copies demarrent au meme endroit : un tiers de la video, la ou
        # la difference d'image se voit, plutot qu'un generique.
        QTimer.singleShot(400, lambda: self._seek(330))
        for player, _a in self.players:
            player.play()

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
        self.decided.emit(i)
        self.close()

    def closeEvent(self, event) -> None:                    # noqa: N802
        self.timer.stop()
        for player, _a in self.players:
            player.stop()
            player.setSource(QUrl())
        super().closeEvent(event)
