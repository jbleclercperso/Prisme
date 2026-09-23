"""La fenêtre du partage : la serrure, l'interrupteur, et le journal.

Ouvrir sa bibliothèque au dehors demande trois choses au même endroit : un
mot de passe qu'on puisse changer, un interrupteur qu'on puisse couper, et
de quoi regarder qui est entré. Éparpillées dans des menus, ces trois choses
ne se consultent jamais.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .access import JOURNAL, spell, when
from .tunnel import find as find_tunnel, install as install_tunnel, qr_png


class Installer(QThread):
    """Installe cloudflared sans figer la fenêtre : winget prend son temps."""

    done = Signal(str, str)          # chemin trouve, ce qu'il faut en dire

    def run(self) -> None:
        where, said = install_tunnel()
        self.done.emit(where, said)

SHARE_STYLE = """
QDialog { background: #0e1116; }
QLabel { color: #b9c2cd; font-size: 13px; }
QLabel#shareHead { color: #ffffff; font-size: 14px; font-weight: 600; }
QLabel#shareLink { color: #e9eef4; font-size: 13px; }
QLabel#shareWarn { color: #d8c05a; font-size: 12px; }
QLabel#shareCode { background: #ffffff; border-radius: 6px; padding: 6px; }
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

        # -- l'adresse publique ---------------------------------------------
        head = QLabel("Depuis l'extérieur", page)
        head.setObjectName("shareHead")
        box.addWidget(head)

        self.tunnel_auto = QCheckBox(
            "Ouvrir l'adresse publique au lancement", page)
        self.tunnel_auto.setChecked(bool(self.window.cfg["tunnel_auto"]))
        self.tunnel_auto.toggled.connect(self._auto)
        box.addWidget(self.tunnel_auto)

        row = QHBoxLayout()
        self.open_tunnel = QPushButton("Ouvrir l'adresse publique", page)
        self.open_tunnel.clicked.connect(self._open_tunnel)
        row.addWidget(self.open_tunnel)
        self.shut_tunnel = QPushButton("Fermer", page)
        self.shut_tunnel.clicked.connect(self._shut_tunnel)
        row.addWidget(self.shut_tunnel)
        self.copy = QPushButton("Copier l'adresse", page)
        self.copy.clicked.connect(self._copy)
        row.addWidget(self.copy)
        row.addStretch(1)
        box.addLayout(row)

        self.tunnel_state = QLabel("", page)
        box.addWidget(self.tunnel_state)

        self.code = QLabel("", page)
        self.code.setObjectName("shareCode")
        self.code.setAlignment(Qt.AlignCenter)
        self.code.hide()
        box.addWidget(self.code, 0, Qt.AlignLeft)
        self.code_hint = QLabel(
            "Scannez ce code avec l'appareil photo du téléphone.", page)
        self.code_hint.hide()
        box.addWidget(self.code_hint)

        warn = QLabel(
            "L'adresse publique passe par un tunnel Cloudflare : rien n'est "
            "ouvert sur votre box, et la liaison est chiffrée. Elle change à "
            "chaque ouverture, et se ferme avec Prisme.", page)
        warn.setObjectName("shareWarn")
        warn.setWordWrap(True)
        box.addWidget(warn)
        box.addStretch(1)
        self._shown_code = ""
        self.installer = None
        return page

    # -- le tunnel -----------------------------------------------------------
    def _auto(self, on: bool) -> None:
        self.window.cfg["tunnel_auto"] = bool(on)
        self.window.cfg.save()

    def _open_tunnel(self) -> None:
        if not find_tunnel():
            if QMessageBox.question(
                    self, "Installer cloudflared",
                    "Pour une adresse publique, Prisme a besoin de "
                    "cloudflared — le programme de Cloudflare qui tient le "
                    "tunnel.\n\nL'installer maintenant ? Cela passe par "
                    "winget et prend une minute.") != QMessageBox.Yes:
                return
            self.open_tunnel.setEnabled(False)
            self.tunnel_state.setText("Installation de cloudflared…")
            self.installer = Installer(self)
            self.installer.done.connect(self._installed)
            self.installer.start()
            return
        if not self.window.start_tunnel():
            self.refresh()

    def _installed(self, where: str, said: str) -> None:
        self.installer = None
        self.open_tunnel.setEnabled(True)
        self.tunnel_state.setText(said)
        if where:
            self.window.start_tunnel()
        else:
            QMessageBox.warning(self, "cloudflared", said)
        self.refresh()

    def _shut_tunnel(self) -> None:
        self.window.stop_tunnel()
        self.refresh()

    def _copy(self) -> None:
        address = self.window.share_link()
        if address:
            QGuiApplication.clipboard().setText(address)
            self.tunnel_state.setText("Adresse copiée.")

    def _paint_code(self, address: str) -> None:
        """Dessine le code à scanner, et seulement quand l'adresse change."""
        if address == self._shown_code:
            return
        self._shown_code = address
        raw = qr_png(address) if address.startswith("https://") else b""
        if not raw:
            self.code.hide()
            self.code_hint.hide()
            return
        picture = QPixmap()
        picture.loadFromData(raw, "PNG")
        self.code.setPixmap(picture)
        self.code.show()
        self.code_hint.show()

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
        address = self.window.share_link()
        self.link.setText(address)
        self.tunnel_state.setText(self.window.tunnel_state())
        public = self.window.tunnel_address
        self.open_tunnel.setText("Ouvrir l'adresse publique" if not public
                                 else "Adresse publique ouverte")
        self.open_tunnel.setEnabled(not public and self.installer is None)
        self.shut_tunnel.setEnabled(bool(public))
        self.copy.setEnabled(bool(address))
        self._paint_code(public + "/" if public else "")

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
