"""Entête : où l'on est, ce qu'on regarde, et comment.

Les cinq boutons d'avant mélangeaient deux questions distinctes — *quoi* je
regarde et *comment* — sans jamais dire laquelle était en cours. Deux sélecteurs
segmentés les séparent, et un fil d'Ariane répond à la troisième question, celle
que l'on se pose le plus souvent : où suis-je.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from .widgets import FlowLayout

# Trois facons de regarder la meme collection. Ce ne sont pas trois
# applications : ouvrir une video depuis n'importe quel onglet mene toujours
# a la meme fiche, avec ses destinations et sa note.
TAB_FOLDERS = "folders"
TAB_VIDEOS = "videos"
TAB_EDIT = "edit"
TABS = (TAB_FOLDERS, TAB_VIDEOS, TAB_EDIT)

# Conserves pour les appels existants : un onglet dit a la fois quoi et comment.
CONTENT_FOLDERS = TAB_FOLDERS
CONTENT_VIDEOS = TAB_VIDEOS
VIEW_BROWSE = "browse"
VIEW_EDIT = "edit"

HEADER_STYLE = """
QFrame#segment { background: #14181e; border: 1px solid #2b323d;
                 border-radius: 8px; }
QPushButton#segmentChoice { background: transparent; border: 0;
                            border-radius: 6px; padding: 7px 20px;
                            color: #93a0b0; font-weight: 600; font-size: 14px; }
QPushButton#chip { background: #1a1f27; border: 1px solid #2b323d;
                   border-radius: 13px; padding: 4px 14px; color: #93a0b0; }
QPushButton#chip[chosen="true"] { background: #2f6fed; border-color: #2f6fed;
                                  color: #ffffff; font-weight: 600; }
QPushButton#segmentChoice:hover { color: #dfe6ee; }
QPushButton#segmentChoice[chosen="true"] { background: #2f6fed; color: #ffffff; }
QLabel#segmentLabel { color: #6f7885; font-size: 12px; }
QPushButton#crumb { background: transparent; border: 0; padding: 3px 6px;
                    color: #9fb0c4; font-size: 14px; }
QPushButton#crumb:hover { color: #ffffff; text-decoration: underline; }
QPushButton#crumb[last="true"] { color: #ffffff; font-weight: 600; }
QLabel#crumbSep { color: #4d5563; font-size: 14px; }
"""


class Segmented(QWidget):
    """Deux ou trois choix exclusifs, celui en cours restant visiblement allumé."""

    chosen = Signal(str)

    def __init__(self, label: str, choices: list, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        caption = QLabel(label, self)
        caption.setObjectName("segmentLabel")
        layout.addWidget(caption)

        frame = QFrame(self)
        frame.setObjectName("segment")
        inner = QHBoxLayout(frame)
        inner.setContentsMargins(3, 3, 3, 3)
        inner.setSpacing(2)
        self.buttons: dict = {}
        for key, text, tip in choices:
            button = QPushButton(text, frame)
            button.setObjectName("segmentChoice")
            button.setToolTip(tip)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setProperty("chosen", "false")
            button.clicked.connect(lambda _c=False, k=key: self.chosen.emit(k))
            inner.addWidget(button)
            self.buttons[key] = button
        layout.addWidget(frame)

    def set_value(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.setProperty("chosen", "true" if name == key else "false")
            button.style().unpolish(button)
            button.style().polish(button)


class Chips(QWidget):
    """Petits boutons ronds, un seul actif : deux familles de mots-cles."""

    chosen = Signal(str)

    def __init__(self, choices: list, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.buttons: dict = {}
        for key, text, tip in choices:
            button = QPushButton(text, self)
            button.setObjectName("chip")
            button.setToolTip(tip)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setProperty("chosen", "false")
            button.clicked.connect(lambda _c=False, k=key: self.chosen.emit(k))
            layout.addWidget(button)
            self.buttons[key] = button
        layout.addStretch(1)

    def set_value(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.setProperty("chosen", "true" if name == key else "false")
            button.style().unpolish(button)
            button.style().polish(button)


class Breadcrumb(QWidget):
    """Chemin cliquable depuis la racine ouverte jusqu'au dossier courant."""

    jumped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(2)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._top: Path | None = None

    def set_path(self, top, current) -> None:
        """Affiche la chaîne de `top` à `current`, chaque segment cliquable."""
        while self.layout_.count():
            child = self.layout_.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        if current is None:
            return

        self._top = Path(top) if top else Path(current)
        chain = []
        walk = Path(current)
        while True:
            chain.append(walk)
            if walk == self._top or walk.parent == walk or len(chain) > 16:
                break
            walk = walk.parent
        chain.reverse()

        for position, path in enumerate(chain):
            if position:
                separator = QLabel("›", self)
                separator.setObjectName("crumbSep")
                self.layout_.addWidget(separator)
            button = QPushButton(path.name or str(path), self)
            button.setObjectName("crumb")
            button.setToolTip(str(path))
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setProperty("last", "true" if position == len(chain) - 1 else "false")
            button.clicked.connect(lambda _c=False, p=str(path): self.jumped.emit(p))
            self.layout_.addWidget(button)
        self.layout_.addStretch(1)


class ControlBar(QWidget):
    """Tout ce qui règle la liste : chercher, filtrer, classer, paginer.

    Les trois barres d'avant disaient chacune un morceau de la même chose. Une
    seule rangée, identique quel que soit le mode, évite d'avoir à retrouver
    lequel des trois endroits porte le réglage cherché.
    """

    changed = Signal()
    sortChanged = Signal(str)
    columnsChanged = Signal(int)
    previousPage = Signal()
    nextPage = Signal()
    randomHere = Signal()
    released = Signal()

    RESOLUTIONS = (
        ("toutes", 0), ("360p", 360), ("480p", 480), ("720p", 720),
        ("1080p", 1080), ("1440p", 1440), ("4K", 2160),
    )
    SORTS = (
        ("name", "Nom"), ("duration_desc", "Durée ▼"), ("duration_asc", "Durée ▲"),
        ("stars_desc", "Note ▼"), ("size_desc", "Taille ▼"), ("random", "Au hasard"),
    )

    def __init__(self, columns_choices, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.flow = FlowLayout(spacing=8)
        outer.addLayout(self.flow)

        self.include = _Field("contient…", 210)
        self.exclude = _Field("exclure…", 160)
        self.exclude.setObjectName("excludeEdit")
        self.flow.addWidget(_caption("Nom"))
        self.flow.addWidget(self.include)
        self.flow.addWidget(self.exclude)

        self.duration_op = _combo([("", "toutes"), ("gt", "plus longue que"),
                                   ("lt", "plus courte que")])
        self.duration_value = _Field("min", 64)
        self.flow.addWidget(_caption("Durée"))
        self.flow.addWidget(self.duration_op)
        self.flow.addWidget(self.duration_value)

        self.resolution_op = _combo([("gte", "au moins"), ("lte", "au plus")])
        self.resolution = _combo([(height, text) for text, height in self.RESOLUTIONS])
        self.flow.addWidget(_caption("Résolution"))
        self.flow.addWidget(self.resolution_op)
        self.flow.addWidget(self.resolution)

        self.stars = _combo([(-1, "toutes")] + [(n, "★" * n or "aucune")
                                                for n in range(6)])
        self.flow.addWidget(_caption("Note"))
        self.flow.addWidget(self.stars)

        self.sort = _combo(list(self.SORTS))
        self.flow.addWidget(_caption("Tri"))
        self.flow.addWidget(self.sort)

        self.columns = _combo([(n, str(n)) for n in columns_choices])
        self.flow.addWidget(_caption("Par rangée"))
        self.flow.addWidget(self.columns)

        self.reset_button = _button("Tout afficher", self.reset)
        self.flow.addWidget(self.reset_button)

        self.random_here = _button("Au hasard ici", self.randomHere.emit)
        self.random_here.setToolTip("Une vidéo au hasard parmi celles d'ici")
        self.flow.addWidget(self.random_here)

        self.count = QLabel("", self)
        self.count.setObjectName("counter")
        self.previous = _button("◂", self.previousPage.emit)
        self.next = _button("▸", self.nextPage.emit)
        self.flow.addWidget(self.previous)
        self.flow.addWidget(self.count)
        self.flow.addWidget(self.next)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(220)
        self.timer.timeout.connect(self.changed)
        for widget in (self.duration_op, self.resolution_op, self.resolution,
                       self.stars):
            widget.currentIndexChanged.connect(lambda _i: self.timer.start())
        for field in (self.include, self.exclude, self.duration_value):
            field.textChanged.connect(lambda _t: self.timer.start())
            field.released.connect(self.released)
        self.sort.currentIndexChanged.connect(
            lambda _i: self.sortChanged.emit(self.sort.currentData())
        )
        self.columns.currentIndexChanged.connect(
            lambda _i: self.columnsChanged.emit(int(self.columns.currentData()))
        )

    # -- lecture ---------------------------------------------------------
    def criteria(self) -> dict:
        try:
            minutes = float(self.duration_value.text().replace(",", "."))
        except ValueError:
            minutes = 0.0
        return {
            "include": self.include.text(),
            "exclude": self.exclude.text(),
            "duration_op": self.duration_op.currentData() if minutes > 0 else "",
            "duration_s": minutes * 60,
            "resolution_op": self.resolution_op.currentData(),
            "resolution": self.resolution.currentData(),
            "stars": self.stars.currentData(),
        }

    def set_terms(self, include: str, exclude: str) -> None:
        for field, value in ((self.include, include), (self.exclude, exclude)):
            field.blockSignals(True)
            field.setText(value)
            field.blockSignals(False)

    def set_sort(self, mode: str) -> None:
        index = self.sort.findData(mode)
        if index >= 0:
            self.sort.blockSignals(True)
            self.sort.setCurrentIndex(index)
            self.sort.blockSignals(False)

    def set_columns(self, columns: int) -> None:
        index = self.columns.findData(columns)
        if index >= 0:
            self.columns.blockSignals(True)
            self.columns.setCurrentIndex(index)
            self.columns.blockSignals(False)

    def set_page(self, text: str, has_previous: bool, has_next: bool) -> None:
        self.count.setText(text)
        self.previous.setEnabled(has_previous)
        self.next.setEnabled(has_next)

    def set_browsing(self, browsing: bool) -> None:
        """Le nombre par rangée et la pagination ne valent qu'en parcours."""
        for widget in (self.columns, self.previous, self.next):
            widget.setVisible(browsing)

    def reset(self) -> None:
        for combo in (self.duration_op, self.resolution_op, self.resolution,
                      self.stars):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        for field in (self.include, self.exclude, self.duration_value):
            field.blockSignals(True)
            field.clear()
            field.blockSignals(False)
        self.changed.emit()
        self.released.emit()

    def focus_search(self) -> None:
        self.include.setFocus()
        self.include.selectAll()


class _Field(QLineEdit):
    """Champ qui rend la main au clavier de tri sur Échap ou Entrée."""

    released = Signal()

    def __init__(self, placeholder: str, width: int, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFixedWidth(width)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self.released.emit()
            return
        super().keyPressEvent(event)


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    return label


def _combo(pairs: list) -> QComboBox:
    combo = QComboBox()
    for data, text in pairs:
        combo.addItem(text, data)
    return combo


def _button(text: str, slot) -> QPushButton:
    button = QPushButton(text)
    button.setFocusPolicy(Qt.NoFocus)
    button.clicked.connect(lambda _checked=False: slot())
    return button


def build_overflow(parent, entries: list) -> QMenu:
    """Regroupe ce qui ne sert qu'occasionnellement derrière un seul bouton."""
    menu = QMenu(parent)
    for text, slot in entries:
        if text == "-":
            menu.addSeparator()
            continue
        menu.addAction(text, slot)
    return menu
