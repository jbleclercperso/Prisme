"""Panneau d'arborescence : un clic sur un dossier y envoie l'élément courant."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFileSystemModel, QHBoxLayout, QLabel, QPushButton, QTreeView,
    QVBoxLayout, QWidget,
)

TREE_STYLE = """
QWidget#treePanel { background: rgba(10, 12, 15, 0.82);
                    border: 1px solid #242a33; border-radius: 10px; }
QLabel#treeTitle { color: #7d8796; font-size: 11px; }
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("treePanel")
        self.setStyleSheet(TREE_STYLE)
        self.setMinimumWidth(210)
        self.setMaximumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Envoyer vers", self)
        title.setObjectName("treeTitle")
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
