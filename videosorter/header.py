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
TAB_TAGS = "tags"
# L'edition n'est plus un onglet mais l'etage du dessous : on y entre
# en cliquant une carte, on en sort par Echap.
TABS = (TAB_FOLDERS, TAB_VIDEOS, TAB_TAGS)

# Conserves pour les appels existants : un onglet dit a la fois quoi et comment.
CONTENT_FOLDERS = TAB_FOLDERS
CONTENT_VIDEOS = TAB_VIDEOS
VIEW_BROWSE = "browse"
VIEW_EDIT = "edit"

HEADER_STYLE = """
QFrame#segment { background: #14181e; border: 1px solid #262c35;
                 border-radius: 7px; }
QPushButton#segmentChoice { background: transparent; border: 0;
                            border-radius: 5px; padding: 5px 11px;
                            color: #8b94a1; font-size: 13px; }
QPushButton#chip { background: transparent; border: 1px solid #262c35;
                   border-radius: 5px; padding: 4px 12px; color: #8b94a1;
                   font-size: 13px; }
QPushButton#chip:hover { color: #dfe6ee; }
QPushButton#chip[chosen="true"] { background: #262c35; border-color: #39414d;
                                  color: #ffffff; }
QPushButton#sortChip { background: transparent; border: 1px solid #262c35;
                       border-radius: 5px; padding: 4px 9px; color: #8b94a1;
                       font-size: 13px; }
QPushButton#sortChip:hover { color: #dfe6ee; }
QPushButton#sortChip[chosen="true"] { background: #262c35; border-color: #39414d;
                                      color: #ffffff; }
QPushButton#segmentChoice:hover { color: #dfe6ee; }
QPushButton#segmentChoice[chosen="true"] { background: #262c35; color: #ffffff; }
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


class SortChips(QWidget):
    """Un critere par pastille : un clic decroissant, deux croissant, trois rien.

    Une liste deroulante obligeait a l'ouvrir pour savoir ce qui etait en cours,
    et a la reouvrir pour l'annuler. Ici l'etat se lit sans rien ouvrir, et
    l'ordre s'inverse du meme geste qui l'a pose.
    """

    chosen = Signal(str)

    CRITERIA = (("duration", "Durée"), ("size", "Taille"),
                ("stars", "Note"), ("resolution", "Résolution"))

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.key = ""
        self.order = ""
        self.buttons: dict = {}
        for key, text in self.CRITERIA:
            button = QPushButton(text, self)
            button.setObjectName("sortChip")
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setProperty("chosen", "false")
            button.setToolTip("Clic : du plus grand au plus petit — "
                              "reclic : l'inverse — troisième clic : au hasard")
            button.clicked.connect(lambda _c=False, k=key: self._cycle(k))
            layout.addWidget(button)
            self.buttons[key] = button
        self._repaint()

    def _cycle(self, key: str) -> None:
        if key != self.key:
            self.key, self.order = key, "desc"
        elif self.order == "desc":
            self.order = "asc"
        else:
            self.key, self.order = "", ""
        self._repaint()
        self.chosen.emit(f"{self.key}_{self.order}" if self.key else "random")

    def set_value(self, mode: str) -> None:
        key, _, order = (mode or "").rpartition("_")
        self.key = key if order in ("desc", "asc") else ""
        self.order = order if self.key else ""
        self._repaint()

    def _repaint(self) -> None:
        for key, text in self.CRITERIA:
            button = self.buttons[key]
            active = key == self.key
            arrow = " ▼" if self.order == "desc" else " ▲"
            button.setText(f"{text}{arrow}" if active else text)
            button.setProperty("chosen", "true" if active else "false")
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
    """Chercher, classer, paginer — sur une seule rangée.

    Elle en occupait trois, repliees, et mangeait la moitie haute de la fenetre
    pour des reglages qui servent rarement : deux champs de texte, quatre listes
    deroulantes, un bouton de remise a zero. L'ecran appartient aux vignettes.
    Ce qui reste est ce qu'on touche vraiment : chercher un nom, classer, et
    regler la densite de la planche.
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
        # Une disposition qui se replie, et non une rangee rigide : un
        # QHBoxLayout impose sa largeur a la fenetre entiere, qui ne peut alors
        # plus retrecir et laisse tout deborder de l'ecran.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = FlowLayout(spacing=8)
        outer.addLayout(row)

        self.include = _Field("chercher…", 150)
        # Conserve sans etre montre : le filtre d'exclusion garde sa place dans
        # les criteres et dans la configuration, il n'occupe plus l'ecran.
        self.exclude = _Field("", 0)
        self.exclude.hide()
        row.addWidget(self.include)

        self.sorts = SortChips(self)
        self.sorts.chosen.connect(self.sortChanged)
        row.addWidget(self.sorts)

        self.random_here = _button("⚄", self.randomHere.emit)
        self.random_here.setFixedWidth(32)
        self.random_here.setToolTip("Une vidéo au hasard parmi celles d'ici")
        row.addWidget(self.random_here)

        self.columns_caption = _caption("par rangée")
        self.columns = _combo([(n, str(n)) for n in columns_choices])
        self.columns.setFixedWidth(52)
        row.addWidget(self.columns)
        row.addWidget(self.columns_caption)

        self.count = QLabel("", self)
        self.count.setObjectName("counter")
        self.previous = _button("◂", self.previousPage.emit)
        self.next = _button("▸", self.nextPage.emit)
        self.previous.setFixedWidth(30)
        self.next.setFixedWidth(30)
        row.addWidget(self.previous)
        row.addWidget(self.count)
        row.addWidget(self.next)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(220)
        self.timer.timeout.connect(self.changed)
        self.include.textChanged.connect(lambda _t: self.timer.start())
        self.include.released.connect(self.released)
        self.columns.currentIndexChanged.connect(
            lambda _i: self.columnsChanged.emit(int(self.columns.currentData()))
        )

    # -- lecture ---------------------------------------------------------
    def criteria(self) -> dict:
        return {
            "include": self.include.text(),
            "exclude": self.exclude.text(),
            "duration_op": "",
            "duration_s": 0.0,
            "resolution_op": "gte",
            "resolution": 0,
            "stars": -1,
        }

    def set_terms(self, include: str, exclude: str) -> None:
        for field, value in ((self.include, include), (self.exclude, exclude)):
            field.blockSignals(True)
            field.setText(value)
            field.blockSignals(False)

    def set_sort(self, mode: str) -> None:
        self.sorts.set_value(mode)

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
        for widget in (self.columns, self.columns_caption, self.previous,
                       self.next):
            widget.setVisible(browsing)

    def reset(self) -> None:
        for field in (self.include, self.exclude):
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
