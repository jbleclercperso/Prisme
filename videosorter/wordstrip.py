"""La bande des mots épinglés : une ligne fine, sous l'en-tête.

Comme sur la page mobile : les mots-clés qu'on a épinglés (l'épingle d'une
tuile, dans l'onglet « Mots-clés »), en petites étiquettes. Un clic montre les
vidéos du mot, depuis n'importe quel onglet ; un clic droit retire, déplace,
ou masque la bande. Elle ne prend que la hauteur d'une étiquette.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QMenu, QPushButton, QScrollArea, QWidget,
)

from .icons import icon
from .tagging import fold

STYLE = """
QPushButton#wordPin { background: #141a22; border: 1px solid #263040; border-radius: 11px;
                      color: #cfd7e1; font-size: 12px; padding: 2px 10px 2px 7px; }
QPushButton#wordPin:hover { background: #1b2330; border-color: #35445a; }
QPushButton#wordPin[current="true"] { background: #1d2a40; border-color: #4f8bf0;
                                       color: #ffffff; }
QPushButton#wordMore { background: transparent; border: 0; color: #6f7885;
                       font-size: 12px; padding: 2px 6px; }
QPushButton#wordMore:hover { color: #cfd7e1; }
QScrollArea#wordStrip { background: transparent; border: 0; }
"""


class WordStrip(QScrollArea):
    """Les mots épinglés, en une ligne qui défile à la molette si elle déborde."""

    chosen = Signal(str)
    removeRequested = Signal(str)
    moveRequested = Signal(str, int)      # le mot, -1 (à gauche) ou +1
    hideRequested = Signal()
    moreRequested = Signal()              # « + » : aller épingler d'autres mots

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("wordStrip")
        self.setStyleSheet(STYLE)
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(26)
        self.inner = QWidget(self)
        self.inner.setStyleSheet("background: transparent;")
        self.row = QHBoxLayout(self.inner)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(6)
        self.setWidget(self.inner)
        self.words: list = []
        self.current = ""

    def set_words(self, words: list) -> None:
        if list(words) == self.words:
            return
        self.words = list(words)
        while self.row.count():
            item = self.row.takeAt(0)
            if item.widget() is not None:
                # Cachee tout de suite : detruite plus tard, l'ancienne
                # etiquette restait dessinee sous la nouvelle.
                item.widget().hide()
                item.widget().deleteLater()
        for word in self.words:
            button = QPushButton(word, self.inner)
            button.setObjectName("wordPin")
            button.setIcon(icon("pin", "#8fb4ff"))
            button.setIconSize(QSize(12, 12))
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setToolTip(f"Les vidéos qui portent « {word} ». Clic droit : "
                              "retirer, déplacer, masquer la bande.")
            button.setProperty("current", "false")
            button.clicked.connect(lambda _c=False, w=word: self.chosen.emit(w))
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda _p, w=word, b=button: self._menu(w, b))
            self.row.addWidget(button)
        more = QPushButton("+ épingler", self.inner)
        more.setObjectName("wordMore")
        more.setCursor(Qt.PointingHandCursor)
        more.setFocusPolicy(Qt.NoFocus)
        more.setToolTip("Onglet « Mots-clés » : l'épingle d'un mot l'ajoute ici.")
        more.clicked.connect(self.moreRequested.emit)
        self.row.addWidget(more)
        self.row.addStretch(1)
        self.set_current(self.current, force=True)

    def set_current(self, word: str, force: bool = False) -> None:
        """Le mot qu'on regarde, souligné."""
        word = word or ""
        # Un mot-cle ouvert s'appelle « # mot » (`Item.name`).
        if word.startswith("# "):
            word = word[2:]
        if word == self.current and not force:
            return
        self.current = word
        wanted = fold(word) if word else ""
        for index in range(self.row.count()):
            button = self.row.itemAt(index).widget()
            if button is None or button.objectName() != "wordPin":
                continue
            on = "true" if wanted and fold(button.text()) == wanted else "false"
            if button.property("current") != on:
                button.setProperty("current", on)
                button.style().unpolish(button)
                button.style().polish(button)

    def wheelEvent(self, event):                   # noqa: N802
        # La molette fait defiler la bande de cote, quand elle deborde.
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - event.angleDelta().y())
        event.accept()

    def _menu(self, word: str, button) -> None:
        menu = QMenu(self)
        remove = menu.addAction(icon("x"), f"Retirer « {word} » de la bande")
        index = self.words.index(word) if word in self.words else -1
        left = menu.addAction("Déplacer à gauche")
        left.setEnabled(index > 0)
        right = menu.addAction("Déplacer à droite")
        right.setEnabled(0 <= index < len(self.words) - 1)
        menu.addSeparator()
        hide = menu.addAction("Masquer la bande")
        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if chosen is remove:
            self.removeRequested.emit(word)
        elif chosen is left:
            self.moveRequested.emit(word, -1)
        elif chosen is right:
            self.moveRequested.emit(word, 1)
        elif chosen is hide:
            self.hideRequested.emit()
