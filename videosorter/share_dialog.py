"""La fenêtre du partage : la serrure, l'interrupteur, et le journal.

Ouvrir sa bibliothèque au dehors demande trois choses au même endroit : un
mot de passe qu'on puisse changer, un interrupteur qu'on puisse couper, et
de quoi regarder qui est entré. Éparpillées dans des menus, ces trois choses
ne se consultent jamais.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .access import JOURNAL, spell, when

SHARE_STYLE = """
QDialog { background: #0e1116; }
QLabel { color: #b9c2cd; font-size: 13px; }
QLabel#shareHead { color: #ffffff; font-size: 14px; font-weight: 600; }
QLabel#shareLink { color: #e9eef4; font-size: 13px; }
QLabel#shareWarn { color: #d8c05a; font-size: 12px; }
QLineEdit { background: #151a21; border: 1px solid #262e39; border-radius: 6px;
            padding: 6px 9px; color: #e9eef4; }
QTreeWidget { background: #11151b; border: 1px solid #1c222b; color: #cdd5df;
              font-size: 12px; }
QHeaderView::section { background: #151a21; color: #8b94a1; border: 0;
                       padding: 5px; }
"""


class ShareDialog(QDialog):
    """Ce qu'on règle, et ce qu'on surveille."""

    def __init__(self, window, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.setWindowTitle("Partage à distance")
        self.setStyleSheet(SHARE_STYLE)
        self.resize(760, 560)

        outer = QVBoxLayout(self)
        tabs = QTabWidget(self)
        tabs.addTab(self._settings(), "Réglages")
        tabs.addTab(self._visits(), "Connexions")
        tabs.addTab(self._views(), "Ce qui a été regardé")
        outer.addWidget(tabs, 1)

        box = QDialogButtonBox(QDialogButtonBox.Close, self)
        box.button(QDialogButtonBox.Close).setText("Fermer")
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        outer.addWidget(box)

        # Le journal se remplit pendant qu'on le regarde.
        self.beat = QTimer(self)
        self.beat.setInterval(4000)
        self.beat.timeout.connect(self.refresh)
        self.beat.start()
        self.refresh()

    # -- reglages -----------------------------------------------------------
    def _settings(self) -> QWidget:
        page = QWidget(self)
        box = QVBoxLayout(page)
        box.setSpacing(10)

        head = QLabel("Votre bibliothèque, depuis n'importe où", page)
        head.setObjectName("shareHead")
        box.addWidget(head)

        self.switch = QCheckBox("Autoriser le partage à distance", page)
        self.switch.setChecked(bool(self.window.cfg["share"]))
        self.switch.toggled.connect(self.window.set_share)
        box.addWidget(self.switch)

        box.addWidget(QLabel(
            "Le partage est actif dès le lancement. Il reste sans effet tant "
            "qu'aucun mot de passe n'est posé : on n'ouvre pas une collection "
            "sans serrure.", page))

        row = QHBoxLayout()
        row.addWidget(QLabel("Mot de passe", page))
        self.word = QLineEdit(page)
        self.word.setEchoMode(QLineEdit.Password)
        self.word.setPlaceholderText("au moins huit caractères")
        row.addWidget(self.word, 1)
        keep = QPushButton("Enregistrer", page)
        keep.clicked.connect(self._save_word)
        row.addWidget(keep)
        box.addLayout(row)

        self.state = QLabel("", page)
        box.addWidget(self.state)

        self.link = QLabel("", page)
        self.link.setObjectName("shareLink")
        self.link.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.link.setWordWrap(True)
        box.addWidget(self.link)

        warn = QLabel(
            "Sur cette machine, l'adresse ci-dessus ne répond qu'ici. Pour y "
            "accéder de l'extérieur, ouvrez un tunnel — la fiche « Partage à "
            "distance » du menu ⋯ explique comment. Le tunnel évite d'ouvrir "
            "le moindre port sur votre box.", page)
        warn.setObjectName("shareWarn")
        warn.setWordWrap(True)
        box.addWidget(warn)
        box.addStretch(1)
        return page

    def _save_word(self) -> None:
        given = self.word.text()
        if len(given) < 8:
            QMessageBox.warning(
                self, "Mot de passe trop court",
                "Huit caractères au moins. Une adresse joignable depuis "
                "l'internet se fait essayer des milliers de fois par nuit.")
            return
        self.window.set_share_password(given)
        self.word.clear()
        self.refresh()
        QMessageBox.information(
            self, "Mot de passe enregistré",
            "Il n'est gardé nulle part en clair. Notez-le : il ne peut pas "
            "être relu, seulement remplacé.")

    # -- journaux ------------------------------------------------------------
    def _visits(self) -> QWidget:
        page = QWidget(self)
        box = QVBoxLayout(page)
        self.visits = QTreeWidget(page)
        self.visits.setHeaderLabels(["Quand", "Depuis", "Appareil", ""])
        self.visits.setRootIsDecorated(False)
        self.visits.header().setSectionResizeMode(2, QHeaderView.Stretch)
        box.addWidget(self.visits, 1)
        box.addWidget(QLabel(
            "« refus » signale un mot de passe rejeté. Après huit essais, "
            "l'adresse est bloquée cinq minutes.", page))
        forget = QPushButton("Effacer le journal", page)
        forget.clicked.connect(self._forget)
        box.addWidget(forget, 0, Qt.AlignLeft)
        return page

    def _views(self) -> QWidget:
        page = QWidget(self)
        box = QVBoxLayout(page)
        self.views = QTreeWidget(page)
        self.views.setHeaderLabels(["Quand", "Appareil", "Vidéo", "Regardée"])
        self.views.setRootIsDecorated(False)
        self.views.header().setSectionResizeMode(2, QHeaderView.Stretch)
        box.addWidget(self.views, 1)
        box.addWidget(QLabel(
            "Le temps compté est celui où l'image défile : une vidéo en pause "
            "ne compte pas, et un onglet oublié ne rapporte pas la nuit.", page))
        return page

    def _forget(self) -> None:
        if QMessageBox.question(
                self, "Effacer le journal",
                "Tout le journal des connexions et des visionnages sera perdu. "
                "Continuer ?") != QMessageBox.Yes:
            return
        JOURNAL.clear()
        self.refresh()

    # -- mise a jour ----------------------------------------------------------
    def refresh(self) -> None:
        self.state.setText(self.window.share_state())
        self.link.setText(self.window.share_link())

        self.visits.clear()
        for at, ip, label, event in JOURNAL.visits():
            QTreeWidgetItem(self.visits, [
                when(at), ip, label, "" if event == "entree" else event])
        self.views.clear()
        for at, _ip, label, name, seconds in JOURNAL.views():
            QTreeWidgetItem(self.views, [
                when(at), label, name, spell(seconds)])
        for tree in (self.visits, self.views):
            for column in (0, 1, 3):
                tree.resizeColumnToContents(column)

    def closeEvent(self, event):
        self.beat.stop()
        super().closeEvent(event)
