"""La fenetre « Demandes reçues » : ce qu'on a demande depuis le telephone."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
    QVBoxLayout,
)

from . import demandes

STYLE = """
QDialog { background: #0e1116; }
QLabel { color: #c9d1db; }
QLabel#askHead { color: #ffffff; font-size: 18px; font-weight: 700; }
QLabel#askLead { color: #aab4c0; font-size: 12px; }
QListWidget { background: #0b0e12; border: 1px solid #242b35; border-radius: 8px;
              color: #e6e8ea; padding: 4px; font-size: 13px; }
QListWidget::item { padding: 10px 10px; border-bottom: 1px solid #161c24; }
QListWidget::item:selected { background: #1d2a40; color: #ffffff; }
QPushButton { background: #232a34; border: 1px solid #364050; border-radius: 6px;
              padding: 7px 14px; color: #eef1f4; }
QPushButton:hover { background: #2c3541; }
QPushButton:disabled { color: #6f7a87; background: #1a1f27; border-color: #262d37; }
QPushButton#askPrimary { background: #2f6fed; border-color: #2f6fed; font-weight: 600; }
"""


def _when(at: float) -> str:
    return time.strftime("%d/%m à %H:%M", time.localtime(float(at or 0)))


class DemandesDialog(QDialog):
    """Les demandes, la plus recente d'abord : la chercher sur le web ou dans
    le Labo IA, la marquer comme faite, la supprimer."""

    def __init__(self, window, entries: list, paths: list):
        super().__init__(window)
        self.window = window
        self.paths = paths
        self.setWindowTitle("Demandes reçues")
        self.setStyleSheet(STYLE)
        self.resize(760, 520)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 16, 18, 14)
        box.setSpacing(10)
        head = QLabel("Demandes reçues", self)
        head.setObjectName("askHead")
        box.addWidget(head)
        lead = QLabel("Envoyées depuis le téléphone (bouton ➤ de la page mobile). Choisissez-en "
                      "une, puis cherchez-la sur le web ou dans le Labo IA.", self)
        lead.setObjectName("askLead")
        lead.setWordWrap(True)
        box.addWidget(lead)
        self.view = QListWidget(self)
        self.view.setWordWrap(True)
        self.view.currentRowChanged.connect(self._picked)
        box.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.web = QPushButton("Chercher sur le web", self)
        self.web.setObjectName("askPrimary")
        self.web.clicked.connect(self._web)
        row.addWidget(self.web)
        self.lab = QPushButton("Chercher dans le Labo IA", self)
        self.lab.clicked.connect(self._lab)
        row.addWidget(self.lab)
        self.done_button = QPushButton("Marquer comme faite", self)
        self.done_button.clicked.connect(self._toggle_done)
        row.addWidget(self.done_button)
        row.addStretch(1)
        self.drop = QPushButton("Supprimer", self)
        self.drop.clicked.connect(self._drop)
        row.addWidget(self.drop)
        close = QPushButton("Fermer", self)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        box.addLayout(row)
        self._fill(entries)

    # -- la liste ------------------------------------------------------------
    def _handled(self) -> set:
        return set(self.window.cfg["demandes_faites"] or [])

    def _fill(self, entries: list) -> None:
        self.entries = list(entries)
        self.view.clear()
        handled = self._handled()
        for entry in self.entries:
            done = entry["id"] in handled
            kind = demandes.KINDS.get(entry.get("kind"), "Autre")
            who = entry.get("who") or "un appareil"
            text = (f"{'✓  ' if done else ''}{entry.get('text', '')}\n"
                    f"{kind}  ·  {_when(entry.get('at'))}  ·  {who}"
                    + ("  ·  faite" if done else ""))
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry["id"])
            if done:
                item.setForeground(Qt.gray)
            self.view.addItem(item)
        if not self.entries:
            empty = QListWidgetItem("Aucune demande pour l'instant. Sur le téléphone : le bouton ➤ "
                                    "en haut de la page Prisme.")
            empty.setFlags(Qt.NoItemFlags)
            self.view.addItem(empty)
        self.view.setCurrentRow(0 if self.entries else -1)
        self._picked(self.view.currentRow())

    def _current(self):
        row = self.view.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def _picked(self, _row: int) -> None:
        entry = self._current()
        for button in (self.web, self.lab, self.done_button, self.drop):
            button.setEnabled(entry is not None)
        if entry is not None:
            self.done_button.setText("Remettre à faire" if entry["id"] in self._handled()
                              else "Marquer comme faite")

    # -- les gestes ----------------------------------------------------------
    def _web(self) -> None:
        entry = self._current()
        if entry is not None:
            self.window.search_web_for(entry["text"])

    def _lab(self) -> None:
        entry = self._current()
        if entry is not None:
            self.window.search_labo_for(entry["text"])

    def _toggle_done(self) -> None:
        entry = self._current()
        if entry is None:
            return
        handled = self._handled()
        handled.symmetric_difference_update({entry["id"]})
        self.window.cfg["demandes_faites"] = sorted(handled)
        self.window.cfg.save_soon()
        row = self.view.currentRow()
        self._fill(self.entries)
        self.view.setCurrentRow(row)

    def _drop(self) -> None:
        entry = self._current()
        if entry is None:
            return
        demandes.remove(self.paths, {entry["id"]})
        row = self.view.currentRow()
        self._fill([e for e in self.entries if e["id"] != entry["id"]])
        self.view.setCurrentRow(min(row, len(self.entries) - 1))
