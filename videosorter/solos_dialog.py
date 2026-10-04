"""La fenetre « Vidéos seules dans leur dossier » : ce que la regle propose."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout,
)

from . import roots, solos

STYLE = """
QDialog { background: #0e1116; }
QLabel { color: #c9d1db; }
QLabel#soloHead { color: #ffffff; font-size: 18px; font-weight: 700; }
QLabel#soloLead { color: #aab4c0; font-size: 12px; }
QListWidget { background: #0b0e12; border: 1px solid #242b35; border-radius: 8px;
              color: #e6e8ea; padding: 4px; font-size: 13px; }
QListWidget::item { padding: 8px 6px; border-bottom: 1px solid #161c24; }
QListWidget::item:selected { background: #1d2a40; color: #ffffff; }
QPushButton { background: #232a34; border: 1px solid #364050; border-radius: 6px;
              padding: 7px 14px; color: #eef1f4; }
QPushButton:hover { background: #2c3541; }
QPushButton:disabled { color: #6f7a87; background: #1a1f27; border-color: #262d37; }
QPushButton#soloPrimary { background: #2f6fed; border-color: #2f6fed; font-weight: 600; }
"""


class SolosDialog(QDialog):
    """Une case par dossier (et par « 1 » a fusionner) ; « Regrouper » fait
    les cases cochees. Les dossiers decoches ne seront plus proposes."""

    chosen = Signal(list, list, list)     # dossiers, « 1 » a fusionner, laisses

    def __init__(self, parent, plans: list, merges: list, home):
        super().__init__(parent)
        self.plans, self.merges, self.home = plans, merges, home
        self.setWindowTitle("Vidéos seules dans leur dossier")
        self.setStyleSheet(STYLE)
        self.resize(820, 540)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 16, 18, 14)
        box.setSpacing(10)
        count = len(plans)
        head = QLabel(f"{count} dossier{'s' if count > 1 else ''} ne contien"
                      f"{'nent' if count > 1 else 't'} qu'une seule vidéo"
                      if count else "Les dossiers « 1 » à fusionner", self)
        head.setObjectName("soloHead")
        box.addWidget(head)
        lead = QLabel(
            f"Chaque vidéo prend le nom de son dossier et part dans « {home} » ; le "
            f"dossier vide est supprimé. S'il contient aussi des photos ou d'autres "
            f"fichiers, le dossier (sans la vidéo) part dans « {solos.PICS_NAME} ». "
            f"Décochez ce qu'il faut laisser tel quel : ce ne sera plus proposé. "
            f"Ctrl+Z annule.", self)
        lead.setObjectName("soloLead")
        lead.setWordWrap(True)
        box.addWidget(lead)
        self.view = QListWidget(self)
        self.view.setWordWrap(True)
        for plan in plans:
            folder = plan["folder"]
            owner = roots.owner(folder)
            where = roots.label(owner) if owner else str(folder.parent)
            if owner is not None and folder.parent != owner:
                # Plus bas qu'en tete de la racine : on dit ou (« › + Set »).
                where += "  › " + str(folder.parent.relative_to(owner)).replace("\\", " › ")
            extra = (f"  ·  + {len(plan['others'])} autre(s) fichier(s) → {solos.PICS_NAME}"
                     if plan["others"] else "")
            text = (f"{folder.name}\n{where}  ·  → « {solos.new_name(folder, plan['video'])} »"
                    f"{extra}")
            self._row(text, str(folder))
        for source, size in merges:
            self._row(f"Fusionner « {source} » ({size} élément{'s' if size > 1 else ''})\n"
                      f"→ « {home} »", "merge:" + str(source))
        box.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        later = QPushButton("Plus tard", self)
        later.clicked.connect(self.reject)
        row.addWidget(later)
        self.go = QPushButton("", self)
        self.go.setObjectName("soloPrimary")
        self.go.clicked.connect(self._go)
        row.addWidget(self.go)
        box.addLayout(row)
        self.view.itemChanged.connect(lambda *_a: self._count())
        self._count()

    def _row(self, text: str, key: str) -> None:
        item = QListWidgetItem(text, self.view)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        item.setData(Qt.UserRole, key)

    def _keys(self, checked: bool) -> list:
        state = Qt.Checked if checked else Qt.Unchecked
        return [self.view.item(i).data(Qt.UserRole) for i in range(self.view.count())
                if self.view.item(i).checkState() == state]

    def _count(self) -> None:
        count = len(self._keys(True))
        self.go.setText(f"Regrouper ({count})" if count else "Regrouper")
        self.go.setEnabled(bool(count))

    def _go(self) -> None:
        on = set(self._keys(True))
        folders = [p for p in self.plans if str(p["folder"]) in on]
        merges = [s for s, _n in self.merges if "merge:" + str(s) in on]
        left = [k for k in self._keys(False) if not k.startswith("merge:")]
        self.chosen.emit(folders, merges, left)
        self.accept()
