"""La fenêtre du partage : la serrure, l'interrupteur, et le journal.

Ouvrir sa bibliothèque au dehors demande trois choses au même endroit : un
mot de passe qu'on puisse changer, un interrupteur qu'on puisse couper, et
de quoi regarder qui est entré. Éparpillées dans des menus, ces trois choses
ne se consultent jamais.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .access import JOURNAL, spell, when
from .tunnel import (
    connect_fixed, find as find_tunnel, find_fixed, install as install_tunnel,
    install_fixed, qr_png,
)


class Installer(QThread):
    """Installe le programme du tunnel sans figer la fenêtre : winget prend son temps."""

    done = Signal(str, str)          # chemin trouve, ce qu'il faut en dire

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind

    def run(self) -> None:
        where, said = (install_fixed() if self.kind == "tailscale"
                       else install_tunnel())
        self.done.emit(where, said)

SHARE_STYLE = """
QDialog { background: #0e1116; }
QLabel { color: #b9c2cd; font-size: 13px; }
QLabel#shareHead { color: #ffffff; font-size: 14px; font-weight: 600; }
QLineEdit#shareLink { color: #e9eef4; font-size: 13px; background: #151a21;
                      border: 1px solid #2b323d; border-radius: 6px;
                      padding: 7px 9px; }
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

        # Un champ, non une etiquette : on y selectionne, on y fait Ctrl+C,
        # et l'on voit tout de suite que c'est l'adresse a emporter.
        self.link = QLineEdit(page)
        self.link.setObjectName("shareLink")
        self.link.setReadOnly(True)
        self.link.setPlaceholderText("l'adresse paraîtra ici")
        self.link.setCursorPosition(0)
        box.addWidget(self.link)

        # -- l'adresse publique ---------------------------------------------
        head = QLabel("Depuis l'extérieur", page)
        head.setObjectName("shareHead")
        box.addWidget(head)

        row = QHBoxLayout()
        row.addWidget(QLabel("Adresse", page))
        self.kind = QComboBox(page)
        self.kind.addItem("D'un soir — prête tout de suite (Cloudflare)",
                          "cloudflare")
        self.kind.addItem("De toujours — la même chaque jour (Tailscale)",
                          "tailscale")
        at = self.kind.findData(self.window.cfg["tunnel_kind"])
        self.kind.setCurrentIndex(max(0, at))
        self.kind.currentIndexChanged.connect(self._kind_changed)
        row.addWidget(self.kind, 1)
        box.addLayout(row)

        self.kind_hint = QLabel("", page)
        self.kind_hint.setWordWrap(True)
        box.addWidget(self.kind_hint)

        self.setup = QPushButton("Connecter un compte Tailscale", page)
        self.setup.clicked.connect(self._connect_fixed)
        self.setup.hide()
        box.addWidget(self.setup, 0, Qt.AlignLeft)

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
    HINTS = {
        "cloudflare": ("Rien à configurer : un clic et l'adresse existe. Mais "
                       "elle change à chaque ouverture — il faut la renvoyer "
                       "à chaque fois."),
        "tailscale": ("La même adresse tous les jours, gratuite, sans nom de "
                      "domaine. Il faut connecter un compte une fois, et "
                      "autoriser le partage public — Prisme vous y mène."),
    }

    def _kind_changed(self) -> None:
        self.window.set_tunnel_kind(self.kind.currentData())
        self.refresh()

    def _connect_fixed(self) -> None:
        """Ouvre la page qui connecte le compte, ou celle qui autorise le partage."""
        if not find_fixed():
            return self._open_tunnel()
        link, said = connect_fixed()
        if link:
            QDesktopServices.openUrl(QUrl(link))
            QMessageBox.information(
                self, "Connecter Tailscale",
                "Votre navigateur s'ouvre sur la page de connexion. Une fois "
                "le compte connecté, revenez ici et cliquez « Ouvrir "
                "l'adresse publique ».")
        else:
            QMessageBox.information(self, "Tailscale", said)
        self.refresh()

    def _auto(self, on: bool) -> None:
        self.window.cfg["tunnel_auto"] = bool(on)
        self.window.cfg.save()

    def _open_tunnel(self) -> None:
        kind = self.kind.currentData()
        present = find_fixed() if kind == "tailscale" else find_tunnel()
        name = "Tailscale" if kind == "tailscale" else "cloudflared"
        if not present:
            if QMessageBox.question(
                    self, f"Installer {name}",
                    f"Pour une adresse publique, Prisme a besoin de {name} — "
                    "le programme qui tient le tunnel.\n\nL'installer "
                    "maintenant ? Cela passe par winget et prend une "
                    "minute.") != QMessageBox.Yes:
                return
            self.open_tunnel.setEnabled(False)
            self.tunnel_state.setText(f"Installation de {name}…")
            self.installer = Installer(kind, self)
            self.installer.done.connect(self._installed)
            self.installer.start()
            return
        if not self.window.start_tunnel():
            self.refresh()
            # Tailscale dit souvent ce qui manque, et donne le lien qui le
            # regle : mieux vaut le montrer que de laisser l'etat muet.
            trouble = self.window.tunnel_trouble
            if trouble and "http" in trouble:
                QMessageBox.information(self, "Une étape à faire", trouble)

    def _installed(self, where: str, said: str) -> None:
        self.installer = None
        self.open_tunnel.setEnabled(True)
        self.tunnel_state.setText(said)
        if where and self.kind.currentData() == "tailscale":
            # Installe ne veut pas dire connecte : Tailscale demande un compte.
            self._connect_fixed()
        elif where:
            self.window.start_tunnel()
        else:
            QMessageBox.warning(self, "cloudflared", said)
        self.refresh()

    def _shut_tunnel(self) -> None:
        self.window.stop_tunnel()
        self.refresh()

    def _copy(self) -> None:
        """Copie ce qui est affiché — l'adresse publique quand elle existe."""
        address = self.link.text().strip() or self.window.share_link()
        if not address:
            self.tunnel_state.setText("Aucune adresse à copier pour l'instant.")
            return
        QGuiApplication.clipboard().setText(address)
        self.link.selectAll()
        self.tunnel_state.setText(f"Copié : {address}")

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
        if address != self.link.text():
            self.link.setText(address)
            self.link.setCursorPosition(0)
        self.tunnel_state.setText(self.window.tunnel_state())
        public = self.window.tunnel_address
        self.open_tunnel.setText("Ouvrir l'adresse publique" if not public
                                 else "Adresse publique ouverte")
        self.open_tunnel.setEnabled(not public and self.installer is None)
        self.shut_tunnel.setEnabled(bool(public))
        self.copy.setEnabled(bool(address))
        kind = self.kind.currentData()
        self.kind_hint.setText(self.HINTS.get(kind, ""))
        self.setup.setVisible(kind == "tailscale" and not public)
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
