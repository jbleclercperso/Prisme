"""La fenêtre du partage : la serrure, l'interrupteur, et le journal.

Ouvrir sa bibliothèque au dehors demande trois choses au même endroit : un
mot de passe qu'on puisse changer, un interrupteur qu'on puisse couper, et
de quoi regarder qui est entré. Éparpillées dans des menus, ces trois choses
ne se consultent jamais.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .access import JOURNAL, spell, when
from .tunnel import (
    CONSENT, FIXED_CLOSED, Chore, close_fixed, connect_fixed,
    find as find_tunnel, find_fixed, fixed_published, install as install_tunnel,
    install_fixed, open_fixed, qr_png,
)

# Un mot de passe devant l'internet entier : douze caracteres au moins. Une
# adresse publique se fait essayer des milliers de fois par nuit.
MIN_PASSWORD = 12


class Installer(QObject):
    """Installe le programme du tunnel sans figer la fenêtre : winget prend son temps.

    Il appartient a la fenetre principale, pas a celle du partage : fermer
    celle-ci pendant l'installation detruisait un fil encore en marche, et
    Prisme tombait. Il tourne dans un fil Python, qui s'eteint sans dommage
    si l'on quitte Prisme avant la fin — winget, lui, termine son travail.
    """

    done = Signal(str, str)          # chemin trouve, ce qu'il faut en dire
    # L'installation en cours, par programme : une fenetre du partage
    # rouverte la retrouve au lieu d'en lancer une seconde.
    running: dict = {}

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        # La fenetre du partage qui suit l'installation, s'il y en a une.
        self.dialog = None
        self._thread = None
        self.done.connect(self._settle)

    def start(self) -> None:
        Installer.running[self.kind] = self
        self._thread = threading.Thread(target=self.run, daemon=True,
                                        name="prisme-installation")
        self._thread.start()

    def isRunning(self) -> bool:                  # noqa: N802  (comme QThread)
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout_ms: int = -1) -> bool:
        if self._thread is None:
            return True
        self._thread.join(None if timeout_ms < 0 else timeout_ms / 1000)
        return not self._thread.is_alive()

    def run(self) -> None:
        try:
            where, said = (install_fixed() if self.kind == "tailscale"
                           else install_tunnel())
        except Exception as trouble:                   # noqa: BLE001
            where, said = "", f"L'installation a échoué : {trouble}"
        try:
            self.done.emit(where, said)
        except RuntimeError:
            pass                  # Prisme se ferme : plus personne a prevenir

    def _settle(self, where: str, said: str) -> None:
        """Dans le fil de l'interface, une fois l'installation finie."""
        if Installer.running.get(self.kind) is self:
            Installer.running.pop(self.kind, None)
        window = self.parent()
        if self.dialog is None and window is not None:
            # La fenetre du partage a ete fermee entre-temps : on fait ce
            # qu'elle aurait fait, sans elle.
            if where and self.kind != "tailscale":
                window.start_tunnel()
            elif where:
                window.show_banner(
                    "Tailscale est installé : rouvrez « Partage à distance » "
                    "pour connecter un compte.", "done")
            else:
                window.show_banner(said, "error")
        self.deleteLater()


def _fixed_opened(window, result) -> None:
    """Ce que l'ouverture de l'adresse fixe change a la fenetre principale.

    Execute meme si la fenetre du partage a ete fermee pendant l'attente.
    """
    address, said = result
    if window.cfg["tunnel_kind"] != "tailscale" or window.share_server is None:
        return
    window.tunnel_trouble = "" if address else said
    window.tunnel_address = address
    if address:
        window.show_banner(f"Adresse fixe ouverte : {address}", "done")
    consent = CONSENT.search(said or "")
    if consent:
        # Le lien est ouvert tout de suite : c'est la seule etape a faire,
        # et Tailscale attend qu'elle soit faite.
        QDesktopServices.openUrl(QUrl(consent.group(0)))


def _fixed_closed(window, said: str) -> None:
    if said == FIXED_CLOSED:
        window.tunnel_address = ""
        window.tunnel_trouble = ""
    else:
        # L'adresse est peut-etre encore publiee : on ne pretend pas le
        # contraire.
        window.tunnel_trouble = said
        window.show_banner(said, "error")


def _open_link(result) -> None:
    link = result[0] if result else ""
    if link:
        QDesktopServices.openUrl(QUrl(link))


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
        # Detruite a la fermeture : gardee cachee, elle continuait de tout
        # relire toutes les quatre secondes, et une de plus a chaque ouverture.
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Partage à distance")
        self.setStyleSheet(SHARE_STYLE)
        self.resize(760, 560)
        self._busy = ""               # ce que Tailscale est en train de faire
        self._rows = (None, None)     # ce que les journaux montrent deja

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

        # Une installation lancee depuis une fenetre precedente : on la suit.
        for installer in list(Installer.running.values()):
            self._follow(installer)
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
        self.word.setPlaceholderText("au moins douze caractères, ou une phrase")
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

        # Ce qu'il faut savoir de l'adresse depend du chemin choisi : un seul
        # texte pour les deux disait de Tailscale ce qui n'est vrai que de
        # Cloudflare.
        self.warn = QLabel("", page)
        self.warn.setObjectName("shareWarn")
        self.warn.setWordWrap(True)
        box.addWidget(self.warn)
        box.addStretch(1)
        self._shown_code = ""
        self.installer = None
        return page

    # -- le tunnel -----------------------------------------------------------
    HINTS = {
        "cloudflare": ("Rien à configurer : un clic et l'adresse existe. Mais "
                       "elle change à chaque ouverture — il faut la renvoyer "
                       "à chaque fois. Cloudflare réserve ces tunnels "
                       "gratuits aux essais : pour regarder des vidéos "
                       "souvent, préférez l'adresse fixe."),
        "tailscale": ("La même adresse tous les jours, gratuite, sans nom de "
                      "domaine. Il faut connecter un compte une fois, et "
                      "autoriser le partage public — Prisme vous y mène."),
    }
    WARNS = {
        "cloudflare": ("L'adresse publique passe par un tunnel Cloudflare : "
                       "rien n'est ouvert sur votre box, et la liaison est "
                       "chiffrée. Elle change à chaque ouverture, et se ferme "
                       "avec Prisme."),
        "tailscale": ("L'adresse fixe est permanente : elle figure dans les "
                      "registres publics de certificats, et reste publiée si "
                      "Prisme s'arrête brutalement. Rien n'est ouvert sur "
                      "votre box, et la liaison est chiffrée — mais seul le "
                      "mot de passe la garde : choisissez-le long."),
    }

    def _kind_changed(self) -> None:
        self.window.set_tunnel_kind(self.kind.currentData())
        self.refresh()

    def _chore(self, what: str, work, fallback, then, answer) -> None:
        """Lance une commande Tailscale hors du fil de l'interface.

        Elle appartient a la fenetre principale : fermer celle-ci n'interrompt
        rien, et `then` s'applique quand meme. `answer` ne sert qu'ici, tant
        que la fenetre du partage est ouverte.
        """
        self._busy = what
        chore = Chore(work, self.window, fallback=fallback, then=then)
        chore.done.connect(answer)
        chore.start()
        self.refresh()

    def _connect_fixed(self) -> None:
        """Ouvre la page qui connecte le compte, ou celle qui autorise le partage."""
        if not find_fixed():
            return self._open_tunnel()
        if self._busy:
            return
        self._chore("Connexion à Tailscale…", connect_fixed,
                    ("", "Tailscale n'a pas répondu."), _open_link,
                    self._connected)

    def _connected(self, result) -> None:
        self._busy = ""
        link, said = result
        if link:
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
            if Installer.running.get(kind) is not None:
                return
            if QMessageBox.question(
                    self, f"Installer {name}",
                    f"Pour une adresse publique, Prisme a besoin de {name} — "
                    "le programme qui tient le tunnel.\n\nL'installer "
                    "maintenant ? Cela passe par winget et prend une "
                    "minute.") != QMessageBox.Yes:
                return
            installer = Installer(kind, self.window)
            self._follow(installer)
            installer.start()
            self.refresh()
            return
        if kind == "tailscale":
            # Tailscale peut mettre une minute a repondre : jamais dans le fil
            # de l'interface.
            server = self.window.share_server
            if server is None or self._busy:
                self.refresh()
                return
            port, window = server.port, self.window
            self._chore("Ouverture de l'adresse fixe…",
                        lambda: open_fixed(port),
                        ("", "Tailscale n'a pas répondu."),
                        lambda result: _fixed_opened(window, result),
                        self._fixed_answer)
            return
        if not self.window.start_tunnel():
            self.refresh()

    def _fixed_answer(self, result) -> None:
        self._busy = ""
        self.refresh()
        _address, said = result
        # Tailscale dit souvent ce qui manque, et donne le lien qui le regle :
        # il vient de s'ouvrir dans le navigateur, on dit pourquoi.
        if CONSENT.search(said or ""):
            QMessageBox.information(
                self, "Une étape à faire",
                "Votre navigateur s'ouvre sur la page Tailscale qui autorise "
                "le partage public. Une fois accepté, cliquez de nouveau "
                "« Ouvrir l'adresse publique ».\n\n" + said)

    def _follow(self, installer: Installer) -> None:
        """Suit une installation : c'est cette fenetre qui en fera la suite."""
        self.installer = installer
        installer.dialog = self
        installer.done.connect(self._installed)
        name = "Tailscale" if installer.kind == "tailscale" else "cloudflared"
        self._busy = f"Installation de {name}…"

    def _installed(self, where: str, said: str) -> None:
        self.installer = None
        self._busy = ""
        self.tunnel_state.setText(said)
        if where and self.kind.currentData() == "tailscale":
            # Installe ne veut pas dire connecte : Tailscale demande un compte.
            self._connect_fixed()
        elif where:
            self.window.start_tunnel()
        else:
            QMessageBox.warning(self, "Installation", said)
        self.refresh()

    def _shut_tunnel(self) -> None:
        if self.kind.currentData() == "tailscale" and self.window.tunnel_address:
            if self._busy:
                return
            window = self.window
            self._chore("Fermeture de l'adresse fixe…", close_fixed,
                        "Tailscale n'a pas répondu.",
                        lambda said: _fixed_closed(window, said),
                        self._fixed_shut)
            return
        self.window.stop_tunnel()
        self.refresh()

    def _fixed_shut(self, _said) -> None:
        self._busy = ""
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
        if len(given) < MIN_PASSWORD:
            QMessageBox.warning(
                self, "Mot de passe trop court",
                "Douze caractères au moins — une petite phrase fait très bien "
                "l'affaire. Une adresse joignable depuis l'internet se fait "
                "essayer des milliers de fois par nuit.")
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
            "« refus » signale un mot de passe rejeté. Après huit essais "
            "manqués, l'appareil est bloqué cinq minutes, puis deux fois plus "
            "longtemps à chaque nouvel échec — les autres entrent toujours.",
            page))
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
        """Relit l'etat. Rien ici n'attend un programme exterieur : c'est
        appele toutes les quatre secondes, dans le fil de l'interface."""
        kind = self.kind.currentData()
        server = self.window.share_server
        if (kind == "tailscale" and not self.window.tunnel_address
                and not self._busy and server is not None):
            # Une adresse fixe restee publiee d'une session precedente (Prisme
            # arrete net) mene deja ici : on la montre, et on peut la fermer.
            left = fixed_published(server.port)
            if left:
                self.window.tunnel_address = left
        self.state.setText(self.window.share_state())
        address = self.window.share_link()
        if address != self.link.text():
            self.link.setText(address)
            self.link.setCursorPosition(0)
        self.tunnel_state.setText(self._busy or self.window.tunnel_state())
        public = self.window.tunnel_address
        self.open_tunnel.setText("Ouvrir l'adresse publique" if not public
                                 else "Adresse publique ouverte")
        self.open_tunnel.setEnabled(not public and not self._busy)
        self.shut_tunnel.setEnabled(bool(public) and not self._busy)
        self.copy.setEnabled(bool(address))
        self.kind_hint.setText(self.HINTS.get(kind, ""))
        self.warn.setText(self.WARNS.get(kind, ""))
        self.setup.setVisible(kind == "tailscale" and not public)
        self.setup.setEnabled(not self._busy)
        self._paint_code(public + "/" if public else "")
        self._fill_journals()

    def _fill_journals(self) -> None:
        """Les journaux, refaits seulement s'ils ont change.

        Les refaire toutes les quatre secondes ramenait la liste en haut et
        perdait la ligne selectionnee, pour rien la plupart du temps.
        """
        visits, views = JOURNAL.visits(), JOURNAL.views()
        if (visits, views) == self._rows:
            return
        self._rows = (visits, views)
        self.visits.clear()
        for at, ip, label, event in visits:
            QTreeWidgetItem(self.visits, [
                when(at), ip, label, "" if event == "entree" else event])
        self.views.clear()
        for at, _ip, label, name, seconds in views:
            QTreeWidgetItem(self.views, [
                when(at), label, name, spell(seconds)])
        for tree in (self.visits, self.views):
            for column in (0, 1, 3):
                tree.resizeColumnToContents(column)

    def done(self, result: int) -> None:
        # Fermer la fenetre arrete sa relecture ; une installation en cours
        # continue, et la fenetre principale en fera la suite.
        self.beat.stop()
        if self.installer is not None and self.installer.dialog is self:
            self.installer.dialog = None
        super().done(result)

    def showEvent(self, event):
        if not self.beat.isActive():
            self.beat.start()
            self.refresh()
        super().showEvent(event)

    def hideEvent(self, event):
        # Cachee (fenetre principale reduite), elle n'a rien a relire.
        self.beat.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.beat.stop()
        super().closeEvent(event)
