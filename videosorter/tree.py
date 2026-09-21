"""Panneau d'arborescence : un clic sur un dossier y envoie l'élément courant."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QModelIndex, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFileIconProvider, QFileSystemModel, QHBoxLayout, QPushButton,
    QTreeView, QVBoxLayout, QWidget,
)

# Deux gestes opposes partagent le meme clic : envoyer l'element dans un
# dossier, ou s'y rendre. Rien ne les distinguait, et se tromper deplace des
# fichiers. Chacun porte donc sa couleur, sur le cadre, le titre et les icones.
SEND_COLOR = "#e0922f"     # ambre : on deplace, cela merite un temps d arret
GO_COLOR = "#4f8bf0"       # bleu : on se deplace, rien n est touche


def _folder_icon(color: str) -> QIcon:
    """Un dossier dessine, a la couleur du geste en cours.

    Celui du systeme etait un dossier jaune de bureau, illisible sur fond
    sombre et surtout identique dans les deux modes — l icone ne disait rien
    de ce qu un clic allait faire.
    """
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    shape = QPainterPath()
    # L onglet, puis le corps : deux rectangles arrondis qui se rejoignent.
    shape.addRoundedRect(QRectF(3, 7, 11, 5), 2, 2)
    shape.addRoundedRect(QRectF(3, 9.5, 26, 16), 3, 3)
    tint = QColor(color)
    painter.fillPath(shape, tint)
    painter.end()
    return QIcon(pixmap)


class ModeIcons(QFileIconProvider):
    """Fournit l icone des dossiers, teintee par le geste en cours."""

    def __init__(self, color: str):
        super().__init__()
        self.icon_ = _folder_icon(color)

    def set_color(self, color: str) -> None:
        self.icon_ = _folder_icon(color)

    def icon(self, info):
        return self.icon_


TREE_STYLE = """
QWidget#treePanel { background: rgba(10, 12, 15, 0.82);
                    border: 1px solid #242a33; border-radius: 10px; }
QWidget#treePanel[action="send"] { border: 2px solid #e0922f; }
QWidget#treePanel[action="go"] { border: 2px solid #4f8bf0; }
QPushButton#treeAction { border: 0; border-radius: 6px; padding: 5px 10px;
                         font-size: 12px; font-weight: 600; color: #11150f; }
QPushButton#treeAction[action="send"] { background: #e0922f; }
QPushButton#treeAction[action="go"] { background: #4f8bf0; color: #0b1220; }
QTreeView#tree { background: transparent; border: 0; color: #b9c2cd;
                 font-size: 13px; outline: 0; }
QTreeView#tree::item { padding: 3px 2px; border-radius: 4px; }
QTreeView#tree::item:hover { background: rgba(47, 111, 237, 0.28); color: #ffffff; }
QTreeView#tree::item:selected { background: rgba(47, 111, 237, 0.45); color: #ffffff; }
QPushButton#treeRootButton { background: transparent; border: 0; color: #8fb4ff;
                             padding: 2px 4px; text-align: left; font-size: 12px; }
QPushButton#treeRootButton:hover { color: #cfe0ff; text-decoration: underline; }
"""


class TreePanel(QWidget):
    """Arborescence des dossiers de destination, volontairement discrète.

    Un simple clic suffit : pas de confirmation, l'élément part immédiatement.
    Le modèle de Qt ne lit un niveau que lorsqu'il est déplié, donc une
    arborescence profonde ne coûte rien tant qu'elle reste repliée.
    """

    folderChosen = Signal(str)
    rootChanged = Signal(str)
    actionChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("treePanel")
        self.setStyleSheet(TREE_STYLE)
        self.setMinimumWidth(150)
        self.setMaximumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.action = "send"
        title = QPushButton("Envoyer vers ⇄", self)
        title.setObjectName("treeAction")
        title.setToolTip("Basculer entre envoyer l element et s y rendre")
        title.setFocusPolicy(Qt.NoFocus)
        title.clicked.connect(self.toggle_action)
        self.action_button = title
        change = QPushButton("changer…", self)
        change.setObjectName("treeRootButton")
        change.setFocusPolicy(Qt.NoFocus)
        change.clicked.connect(self.choose_root)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(change)
        layout.addLayout(header)

        self.root_button = QPushButton("—", self)
        self.root_button.setObjectName("treeRootButton")
        self.root_button.setFocusPolicy(Qt.NoFocus)
        self.root_button.clicked.connect(self.choose_root)
        layout.addWidget(self.root_button)

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Dirs | QDir.NoDotAndDotDot | QDir.Drives)
        self.model.setReadOnly(True)
        # Seuls les dossiers de tete sont des destinations : ce sont les seuls
        # vers lesquels on range. Montrer toute l arborescence obligeait a
        # chercher les bons parmi des centaines, et invitait a la faute.
        self.model.setNameFilters(["+*"])
        self.model.setNameFilterDisables(False)
        self.icons = ModeIcons(SEND_COLOR)
        self.model.setIconProvider(self.icons)

        self.view = QTreeView(self)
        self.view.setObjectName("tree")
        self.view.setModel(self.model)
        self.view.setHeaderHidden(True)
        self.view.setAnimated(False)
        self.view.setIndentation(14)
        self.view.setFocusPolicy(Qt.NoFocus)
        self.view.setExpandsOnDoubleClick(False)
        for column in range(1, self.model.columnCount()):
            self.view.hideColumn(column)
        self.view.clicked.connect(self._on_clicked)
        layout.addWidget(self.view, 1)

        self.root = ""
        self.set_action(self.action)

    def toggle_action(self) -> None:
        """Un clic dans l arbre envoie l element, ou nous y emmene."""
        self.action = "go" if self.action == "send" else "send"
        self.set_action(self.action)
        self.actionChanged.emit(self.action)

    def set_action(self, action: str) -> None:
        """Change de geste, et le fait voir : un clic ici deplace des fichiers."""
        self.action = action
        self.action_button.setText(
            "⇄  Envoyer vers" if action == "send" else "⇄  Aller dans"
        )
        color = SEND_COLOR if action == "send" else GO_COLOR
        self.icons.set_color(color)
        self.view.setStyleSheet(
            "QTreeView#tree::item:hover { background: %s; color: #0b1220; }"
            % QColor(color).darker(115).name()
        )
        for widget in (self, self.action_button):
            widget.setProperty("action", action)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        # Qt ne redemande les icones que si le modele le dit.
        self.model.setIconProvider(self.icons)

    def set_root(self, root: str) -> None:
        if not root or not Path(root).is_dir():
            return
        self.root = str(root)
        self.model.setRootPath(self.root)
        self.view.setRootIndex(self.model.index(self.root))
        self.root_button.setText(Path(self.root).name or self.root)
        self.root_button.setToolTip(self.root)

    def choose_root(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Dossier contenant les destinations", self.root or ""
        )
        if chosen:
            self.set_root(chosen)
            self.rootChanged.emit(chosen)

    def _on_clicked(self, index: QModelIndex) -> None:
        # Le clic sur la flèche de dépliage n'atteint pas l'élément : seul un
        # clic sur le nom du dossier déclenche l'envoi.
        path = self.model.filePath(index)
        if path and Path(path).is_dir():
            self.folderChosen.emit(path)
