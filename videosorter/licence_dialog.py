"""Le dialogue de licence : où en est l'essai, et où coller sa clé.

Deux usages. Depuis « ⋯ › Aide › Licence… », il renseigne et laisse
activer ou retirer une clé. Au lancement, l'essai fini, il est la porte :
Prisme ne s'ouvre qu'avec une clé valable, et « Quitter » ferme tout sans
rien toucher.
"""
from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QVBoxLayout,
)

from . import licence

STYLE = """
QDialog { background: #16181c; }
QLabel { color: #d7dbe0; }
QLabel#licHead { color: #ffffff; font-size: 17px; font-weight: 600; }
QLabel#licLine { color: #aab2bc; font-size: 13px; }
QLabel#licNote { color: #f2a25c; font-size: 12px; }
QPlainTextEdit { background: #0f1114; color: #e6e8ea; border: 1px solid #2c3139;
                 border-radius: 6px; font-family: Consolas, monospace;
                 font-size: 12px; padding: 6px; }
QPushButton { padding: 7px 14px; }
"""


class LicenceDialog(QDialog):
    def __init__(self, cfg, parent=None, gate: bool = False):
        super().__init__(parent)
        self.cfg = cfg
        self.gate = gate
        self.setWindowTitle("Licence de Prisme")
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(520)

        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(10)

        self.head = QLabel("", self)
        self.head.setObjectName("licHead")
        self.head.setWordWrap(True)
        box.addWidget(self.head)
        self.line = QLabel("", self)
        self.line.setObjectName("licLine")
        self.line.setWordWrap(True)
        box.addWidget(self.line)

        box.addSpacing(6)
        ask = QLabel("Votre clé de licence (reçue après l'achat, et dans "
                     "« Mon compte » sur le site) :", self)
        ask.setWordWrap(True)
        box.addWidget(ask)
        self.field = QPlainTextEdit(self)
        self.field.setPlaceholderText("PRISME1-…")
        self.field.setFixedHeight(76)
        box.addWidget(self.field)
        self.note = QLabel("", self)
        self.note.setObjectName("licNote")
        self.note.setWordWrap(True)
        self.note.hide()
        box.addWidget(self.note)

        buttons = QHBoxLayout()
        self.buy = QPushButton("Choisir une formule…", self)
        self.buy.clicked.connect(self.open_plans)
        buttons.addWidget(self.buy)
        self.remove = QPushButton("Retirer la licence de ce PC", self)
        self.remove.clicked.connect(self.remove_key)
        buttons.addWidget(self.remove)
        buttons.addStretch(1)
        self.close_button = QPushButton("Quitter Prisme" if gate else "Fermer", self)
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.activate_button = QPushButton("Activer", self)
        self.activate_button.setDefault(True)
        self.activate_button.clicked.connect(self.activate)
        buttons.addWidget(self.activate_button)
        box.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        state = licence.status(self.cfg)
        self.state = state
        if state.kind == "expired":
            self.head.setText(state.headline())
            self.line.setText(
                "Vos réglages, vos favoris et vos vignettes sont conservés, et "
                "vos dossiers n'ont pas été touchés : collez votre clé, et "
                "tout revient tel quel.")
        else:
            self.head.setText(state.headline())
            self.line.setText(
                "Merci de votre confiance." if state.kind == "licensed" else
                "À la fin de l'essai, une clé de licence vous sera demandée. "
                "Aucune carte n'a été enregistrée : rien ne sera prélevé "
                "sans vous.")
        self.remove.setVisible(state.kind == "licensed" and not self.gate)
        self.buy.setVisible(state.kind != "licensed" or state.plan != "lifetime")

    def open_plans(self) -> None:
        QDesktopServices.openUrl(QUrl(licence.SITE.rstrip("/") + "/#tarifs"))

    def activate(self) -> None:
        ok, message = licence.activate(self.cfg, self.field.toPlainText())
        self.note.setText(message)
        self.note.setStyleSheet("color: #7fd18b;" if ok else "")
        self.note.show()
        if ok:
            self.field.clear()
            self.refresh()
            if self.gate:
                self.accept()

    def remove_key(self) -> None:
        answer = QMessageBox.question(
            self, "Retirer la licence",
            "Retirer la clé de ce PC ? Vous pourrez la recoller ici quand vous "
            "voudrez (elle reste dans « Mon compte », sur le site).")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.cfg["licence_key"] = ""
        self.cfg.save()
        self.note.hide()
        self.refresh()


def gate(cfg, parent=None, before=None) -> bool:
    """Au lancement : vrai si Prisme peut s'ouvrir (essai en cours ou licence).

    L'essai fini, le dialogue demande la clé ; le fermer quitte Prisme.
    `before` : ce qu'il faut faire avant de le montrer (fermer l'écran
    d'accueil, qui resterait par-dessus).
    """
    state = licence.status(cfg)
    cfg.save()
    if state.open:
        return True
    if before is not None:
        before()
    dialog = LicenceDialog(cfg, parent, gate=True)
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    return dialog.exec() == QDialog.DialogCode.Accepted
