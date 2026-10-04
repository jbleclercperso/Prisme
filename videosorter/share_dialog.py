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
QTabWidget::pane { background: #0e1116; border: 1px solid #1c222b; top: -1px; }
QTabWidget > QWidget > QWidget { background: #0e1116; }
QTabBar::tab { background: #151a21; color: #8b94a1; padding: 6px 14px;
               border: 1px solid #1c222b; border-bottom: 0; margin-right: 2px; }
QTabBar::tab:selected { background: #0e1116; color: #ffffff; }
QCheckBox { color: #cdd5df; }
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
        self.resize(860, 640)
        self._busy = ""               # ce que Tailscale est en train de faire
        self._rows = (None, None)     # ce que les journaux montrent deja

        outer = QVBoxLayout(self)
        tabs = QTabWidget(self)
        # Trois facons d'entrer : par le PC (sur le Wi-Fi de la maison, ou de
        # n'importe ou par son adresse publique -- Prisme ouvert), ou par le
        # NAS (de partout, meme PC eteint). Un seul onglet « Inviter » ne
        # montrait que le NAS, et l'on ne savait plus lequel etait lequel.
        # Devant tout : « Téléphone », un seul code, celui qui marche.
        tabs.addTab(self._phone(), "Téléphone")
        tabs.addTab(self._invite_pc(), "Via le PC")
        tabs.addTab(self._invite(), "Via le NAS")
        tabs.addTab(self._settings(), "Réglages")
        # Une fiche par personne, avant le detail des journaux.
        tabs.addTab(self._visitors(), "Visiteurs")
        tabs.addTab(self._visits(), "Connexions")
        tabs.addTab(self._views(), "Ce qui a été regardé")
        tabs.addTab(self._favorites(), "Favoris")
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

    # -- le telephone : un seul code ------------------------------------------
    def _phone(self) -> QWidget:
        """Le code a scanner, sur le meilleur chemin du moment -- ou la seule
        chose a faire pour qu'il paraisse.

        Quatre liens dans deux onglets, chacun avec ses conditions : on ne
        savait plus lequel prendre, et quand aucun ne marchait (partage coupe,
        NAS pas a jour), les codes disparaissaient sans dire pourquoi."""
        page = QWidget(self)
        box = QVBoxLayout(page)
        box.setSpacing(12)
        self.phone_state = QLabel("", page)
        self.phone_state.setObjectName("shareHead")
        self.phone_state.setWordWrap(True)
        box.addWidget(self.phone_state)
        self.phone_how = QLabel("", page)
        self.phone_how.setWordWrap(True)
        box.addWidget(self.phone_how)
        self._phone_do = None
        self.phone_fix = QPushButton("", page)
        self.phone_fix.clicked.connect(
            lambda: self._phone_do() if self._phone_do else None)
        self.phone_fix.hide()
        box.addWidget(self.phone_fix, 0, Qt.AlignLeft)
        code = QLabel("", page)
        code.setObjectName("shareCode")
        code.setAlignment(Qt.AlignCenter)
        code.hide()
        box.addWidget(code, 0, Qt.AlignLeft)
        link = QLineEdit(page)
        link.setObjectName("shareLink")
        link.setReadOnly(True)
        box.addWidget(link)
        copy = QPushButton("Copier le lien", page)
        box.addWidget(copy, 0, Qt.AlignLeft)
        said = QLabel("", page)
        said.setWordWrap(True)
        box.addWidget(said)
        tip = QLabel(
            "Un ancien lien, ou une icône posée sur l'écran d'accueil, qui dit "
            "« ne donne plus accès » : scannez ce code à nouveau, puis reposez "
            "l'icône. Les autres liens et les réglages sont dans les onglets "
            "suivants.", page)
        tip.setWordWrap(True)
        box.addWidget(tip)
        box.addStretch(1)
        self.phone_part = {"code": code, "link": link, "copy": copy,
                           "said": said, "shown": None}
        copy.clicked.connect(lambda _c=False: self._copy_invite(self.phone_part))
        return page

    def _phone_fix_set(self, label: str = "", do=None) -> None:
        self._phone_do = do
        self.phone_fix.setText(label)
        self.phone_fix.setVisible(bool(label))
        self.phone_fix.setEnabled(not self._busy)

    def _refresh_phone(self) -> None:
        """Choisit le lien : le NAS (partout, meme PC eteint), sinon l'adresse
        publique du PC, sinon le Wi-Fi de la maison. Appele apres
        `_refresh_pc` et `_refresh_nas`, dont il reprend les liens."""
        from .nas_publish import PUBLISHER, share_root
        window = self.window
        on = bool(window.cfg["share"])
        top = window.top_root()
        on_nas = bool(top is not None and share_root(top) is not None)
        nas_link = self.nas_away["shown"] or ""
        away, home = self.pc_away["shown"] or "", self.pc_home["shown"] or ""
        address, how = "", ""
        self._phone_fix_set()
        if not on:
            state = ("Le partage est coupé : le téléphone ne peut pas entrer, "
                     "et le NAS n'est plus mis à jour.")
            self._phone_fix_set("Activer le partage",
                                lambda: self.switch.setChecked(True))
        elif on_nas and nas_link and PUBLISHER.last_ok:
            address = nas_link
            how = ("Par le NAS : marche partout (Wi-Fi, 4G), même PC éteint.")
        elif on_nas and nas_link and (PUBLISHER.running or window.share_server is None):
            state = "Mise à jour du NAS… le code paraît dans un instant."
        elif away:
            address = away
            how = "Par le PC : marche partout, tant que Prisme reste ouvert sur ce PC."
        elif home:
            address = home
            how = ("Sur le Wi-Fi de la maison seulement, tant que Prisme reste "
                   "ouvert sur ce PC.")
            self._phone_fix_set("Marcher aussi en 4G (adresse publique)",
                                self._open_tunnel)
        else:
            state = window.share_state()
            if window.share_server is not None and not window.tunnel_address:
                self._phone_fix_set("Ouvrir l'adresse publique", self._open_tunnel)
        if on and on_nas and nas_link and not PUBLISHER.last_ok and not address:
            how = PUBLISHER.state
        elif on and not on_nas:
            # Le NAS ne sert pas : qu'on sache pourquoi, au lieu de chercher.
            how = ((how + "\n") if how else "") + (
                f"Le NAS n'est pas utilisé : ce PC ne voit pas la bibliothèque "
                f"sur le NAS (racine : {top}). Choisissez une racine sur le NAS "
                "pour un lien qui marche PC éteint.")
        if address:
            state = "Prêt — scannez ce code avec l'appareil photo du téléphone."
        self.phone_state.setText(state)
        self.phone_how.setText(how)
        self._show_invite(self.phone_part, address,
                          "Le lien paraîtra ici.", scale=6)

    # -- inviter -------------------------------------------------------------
    def _invite(self) -> QWidget:
        """Les liens du NAS, chacun avec son code a scanner : sur le Wi-Fi, et
        de n'importe ou, meme PC eteint. Les ouvrir suffit pour entrer."""
        page = self._nas()
        box = page.layout()
        head = QLabel("Par le NAS — de partout (Wi-Fi, 4G, ailleurs), même PC éteint. "
                      "Scannez le code, ou envoyez le lien : l'ouvrir suffit pour "
                      "entrer, sans mot de passe.", page)
        head.setObjectName("shareHead")
        head.setWordWrap(True)
        box.insertWidget(0, head)
        warn = QLabel(
            "Quiconque a le lien entre dans la bibliothèque : ne l'envoyez "
            "qu'à qui vous voulez.", page)
        warn.setObjectName("shareWarn")
        warn.setWordWrap(True)
        # Avant l'espace qui pousse tout en haut.
        box.insertWidget(box.count() - 1, warn)
        return page

    def _invite_pc(self) -> QWidget:
        """Les liens du PC : Prisme doit y etre ouvert. Sur le Wi-Fi de la
        maison (le telephone sur le meme Wi-Fi que le PC), ou de n'importe ou
        par l'adresse publique du PC (4G, ailleurs)."""
        page = QWidget(self)
        box = QVBoxLayout(page)
        box.setSpacing(10)
        head = QLabel("Par le PC — Prisme doit être ouvert sur ce PC. Scannez le "
                      "code, ou envoyez le lien : l'ouvrir suffit pour entrer, "
                      "sans mot de passe.", page)
        head.setObjectName("shareHead")
        head.setWordWrap(True)
        box.addWidget(head)
        self.pc_state = QLabel("", page)
        self.pc_state.setWordWrap(True)
        box.addWidget(self.pc_state)
        row = QHBoxLayout()
        row.setSpacing(18)
        self.pc_home = self._invite_column(
            page, row, "Sur le Wi-Fi de la maison",
            "Le téléphone sur le même Wi-Fi que ce PC.")
        self.pc_away = self._invite_column(
            page, row, "De n'importe où",
            "Par l'adresse publique du PC : 4G, ailleurs. Elle s'ouvre ici "
            "(et se règle dans « Réglages »).")
        self.pc_open = QPushButton("Ouvrir l'adresse publique", page)
        self.pc_open.clicked.connect(self._open_tunnel)
        column = self.pc_away["box"]
        column.insertWidget(column.count() - 1, self.pc_open, 0, Qt.AlignLeft)
        box.addLayout(row)
        warn = QLabel("Quiconque a le lien entre dans la bibliothèque : ne l'envoyez "
                      "qu'à qui vous voulez.", page)
        warn.setObjectName("shareWarn")
        warn.setWordWrap(True)
        box.addWidget(warn)
        box.addStretch(1)
        return page

    # -- le NAS --------------------------------------------------------------
    def _nas(self) -> QWidget:
        """Le partage publie sur le NAS : joignable meme PC eteint."""
        page = QWidget(self)
        box = QVBoxLayout(page)
        box.setSpacing(10)
        intro = QLabel(
            "Votre Prisme reste sur ce PC. Après chaque analyse, il dépose sur "
            "le NAS le catalogue, les vignettes et la clé du lien ; un petit "
            "serveur, sur le NAS, les montre aux invités et lit les vidéos "
            "directement sur ses disques. À préparer une fois sur le NAS : "
            "voir « Installer sur le NAS ».", page)
        intro.setWordWrap(True)
        box.addWidget(intro)
        self.nas_state = QLabel("", page)
        self.nas_state.setWordWrap(True)
        box.addWidget(self.nas_state)

        row = QHBoxLayout()
        row.setSpacing(18)
        # Le lien Tailscale du NAS marche partout, a la maison comme en 4G :
        # c'est lui d'abord. Celui du Wi-Fi ne sert que sans Tailscale.
        self.nas_away = self._invite_column(
            page, row, "De partout",
            "Par l'adresse Tailscale du NAS : sur le Wi-Fi de la maison, en 4G, "
            "ailleurs — le même lien.")
        self.nas_home = self._invite_column(
            page, row, "Sur le Wi-Fi de la maison seulement",
            "Le téléphone sur le même Wi-Fi que le NAS (sans Tailscale).")
        box.addLayout(row)

        buttons = QHBoxLayout()
        publish = QPushButton("Publier maintenant", page)
        publish.clicked.connect(self._publish_now)
        buttons.addWidget(publish)
        install = QPushButton("Installer sur le NAS…", page)
        install.clicked.connect(self._open_install)
        buttons.addWidget(install)
        buttons.addStretch(1)
        box.addLayout(buttons)
        box.addStretch(1)
        return page

    def _publish_now(self) -> None:
        if not self.window.publish_nas(force=True):
            QMessageBox.information(
                self, "Publier sur le NAS",
                "Rien à publier pour l'instant : le partage doit être ouvert, "
                "sur la collection de vidéos, et celle-ci sur le NAS.")
        self.refresh()

    def _open_install(self) -> None:
        from .nas_publish import FOLDER, share_root
        root = share_root(self.window.top_root() or "")
        if root is None:
            QMessageBox.information(self, "Installer sur le NAS",
                                    "La bibliothèque n'est pas sur un NAS.")
            return
        where = root / FOLDER / "installation"
        if not where.exists():
            self.window.publish_nas(force=True)
            QMessageBox.information(
                self, "Installer sur le NAS",
                "Les fichiers d'installation se préparent : réessayez dans "
                "un instant.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(where)))

    def _invite_column(self, page, row, title: str, hint: str) -> dict:
        column = QVBoxLayout()
        column.setSpacing(6)
        head = QLabel(title, page)
        head.setObjectName("shareHead")
        column.addWidget(head)
        tip = QLabel(hint, page)
        tip.setWordWrap(True)
        column.addWidget(tip)
        code = QLabel("", page)
        code.setObjectName("shareCode")
        code.setAlignment(Qt.AlignCenter)
        code.hide()
        column.addWidget(code, 0, Qt.AlignLeft)
        link = QLineEdit(page)
        link.setObjectName("shareLink")
        link.setReadOnly(True)
        column.addWidget(link)
        copy = QPushButton("Copier le lien", page)
        column.addWidget(copy, 0, Qt.AlignLeft)
        said = QLabel("", page)
        said.setWordWrap(True)
        column.addWidget(said)
        column.addStretch(1)
        row.addLayout(column, 1)
        part = {"box": column, "code": code, "link": link, "copy": copy,
                "said": said, "shown": None}
        copy.clicked.connect(lambda _c=False, p=part: self._copy_invite(p))
        return part

    def _copy_invite(self, part: dict) -> None:
        address = part["link"].text().strip()
        if not address:
            return
        QGuiApplication.clipboard().setText(address)
        part["link"].selectAll()
        part["said"].setText("Lien copié.")

    def _renew(self) -> None:
        if QMessageBox.question(
                self, "Révoquer le lien",
                "Le lien actuel ne marchera plus, nulle part : chaque "
                "téléphone devra scanner le nouveau code (onglet "
                "« Téléphone »), et tous ceux qui étaient entrés seront "
                "déconnectés.\n\nÀ ne faire que si le lien est tombé entre "
                "de mauvaises mains. Continuer ?") != QMessageBox.Yes:
            return
        self.window.renew_invite()
        for part in (self.nas_home, self.nas_away, self.pc_home, self.pc_away,
                     self.phone_part):
            part["said"].setText("Nouveau lien : scannez-le à nouveau.")
        self.refresh()

    def _show_invite(self, part: dict, address: str, empty: str,
                     scale: int = 4) -> None:
        """Le lien et son code, redessines seulement s'ils changent."""
        part["link"].setPlaceholderText(empty)
        if address == part["shown"]:
            return
        part["shown"] = address
        part["link"].setText(address)
        part["link"].setCursorPosition(0)
        part["copy"].setEnabled(bool(address))
        raw = qr_png(address, scale=scale) if address else b""
        if not raw:
            part["code"].hide()
            return
        picture = QPixmap()
        picture.loadFromData(raw, "PNG")
        part["code"].setPixmap(picture)
        part["code"].show()

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

        hint = QLabel(
            "Le partage est actif dès le lancement. On y entre par le lien "
            "d'invitation (onglets « Via le PC » et « Via le NAS »), ou avec ce mot de passe — "
            "utile pour qui n'a pas le lien.", page)
        hint.setWordWrap(True)
        box.addWidget(hint)

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

        # Le lien est fait pour durer : le meme pour les deux PC, PC eteints.
        # « Nouveau lien », pose a cote des codes, se cliquait pour « avoir un
        # lien » -- et cassait celui de tous les telephones. Il est ici, et
        # dit ce qu'il fait.
        renew = QPushButton("Révoquer le lien actuel…", page)
        renew.setToolTip("Seulement si le lien est tombé entre de mauvaises "
                         "mains : tous les téléphones devront rescanner.")
        renew.clicked.connect(self._renew)
        box.addWidget(renew, 0, Qt.AlignLeft)

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
    def _visitors(self) -> QWidget:
        from .visitors_view import VisitorsPage
        self.people = VisitorsPage(self.window, self, on_change=self._people_changed)
        self._people_kept: dict = {}
        return self.people

    def _people_changed(self) -> None:
        self._people_kept = {}
        self._rows = (None, None)
        self.refresh()

    def _kept_by_stamp(self, name: str, path, read):
        """`read(path)`, refait seulement quand le fichier a change."""
        try:
            stamp = path.stat().st_mtime if path is not None else None
        except OSError:
            stamp = None
        kept = self._people_kept.get(name)
        if kept is None or kept[0] != stamp or kept[1] != path:
            kept = (stamp, path, read(path) if stamp is not None else {})
            self._people_kept[name] = kept
        return kept[2]

    def _fill_people(self) -> None:
        """Les fiches : profils et journaux du PC et du NAS reunis."""
        from . import profils
        from .access import summary_in
        from .config import PRIVATE_DIR
        from .demandes import FILE_NAME
        from .nas_publish import FOLDER, share_root
        from .visitors_view import gather
        pc_profiles = profils.path_for(PRIVATE_DIR / FILE_NAME)
        sources = [("PC",
                    self._kept_by_stamp("pc-journal", JOURNAL.path,
                                        lambda _path: JOURNAL.summary()),
                    pc_profiles,
                    self._kept_by_stamp("pc-profiles", pc_profiles, profils.read_all))]
        root = share_root(self.window.top_root() or "")
        if root is not None:
            state = root / FOLDER / "etat"
            nas_profiles = state / profils.FILE_NAME
            sources.append(("NAS",
                            self._kept_by_stamp("nas-journal", state / "acces.db",
                                                summary_in),
                            nas_profiles,
                            self._kept_by_stamp("nas-profiles", nas_profiles,
                                                profils.read_all)))
        aliases = dict(self.window.cfg["share_aliases"] or {})
        self.people.show_people(gather(sources, aliases,
                                       getattr(self.window, "viewers", [])))

    def _visits(self) -> QWidget:
        page = QWidget(self)
        box = QVBoxLayout(page)
        self.visits = QTreeWidget(page)
        self.visits.setHeaderLabels(["Quand", "Depuis", "Appareil", ""])
        self.visits.setRootIsDecorated(False)
        self.visits.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.visits.setContextMenuPolicy(Qt.CustomContextMenu)
        self.visits.customContextMenuRequested.connect(self._visit_menu)
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
        # Une ligne mene a sa video : double-clic, le lecteur flottant ; clic
        # droit, le lecteur ou l'explorateur.
        self.views.itemDoubleClicked.connect(
            lambda item, _column: self._play_row(item))
        self.views.setContextMenuPolicy(Qt.CustomContextMenu)
        self.views.customContextMenuRequested.connect(self._row_menu)
        box.addWidget(self.views, 1)
        hint = QLabel(
            "Double-clic sur une vidéo : elle s'ouvre dans le lecteur flottant. "
            "Clic droit : la montrer dans l'explorateur.\n"
            "Le temps compté est celui où l'image défile : une vidéo en pause "
            "ne compte pas, et un onglet oublié ne rapporte pas la nuit. "
            "« · NAS » : regardée par le partage du NAS.", page)
        hint.setWordWrap(True)
        box.addWidget(hint)
        forget = QPushButton("Effacer ce journal", page)
        forget.setToolTip("Efface ce qui a été regardé, ici et sur le NAS. "
                          "Les connexions restent.")
        forget.clicked.connect(self._forget_views)
        box.addWidget(forget, 0, Qt.AlignLeft)
        return page

    def _forget_views(self) -> None:
        if QMessageBox.question(
                self, "Effacer ce qui a été regardé",
                "Tout ce qui a été regardé, ici et sur le NAS, sera oublié. "
                "Continuer ?") != QMessageBox.Yes:
            return
        JOURNAL.clear_views()
        # Le journal du NAS s'efface chez lui : on lui laisse le mot, il le
        # lit dans la demi-minute (voir nas/serveur.py).
        from .nas_publish import FOLDER, share_root
        root = share_root(self.window.top_root() or "")
        if root is not None:
            try:
                marker = root / FOLDER / "etat" / "effacer-vues"
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("oui", encoding="utf-8")
            except OSError:
                pass
        self._nas_kept = None
        self._rows = (None, None)
        self.refresh()

    def _favorites(self) -> QWidget:
        """Les favoris de chaque appareil, marques depuis le telephone."""
        page = QWidget(self)
        box = QVBoxLayout(page)
        self.favs = QTreeWidget(page)
        self.favs.setHeaderLabels(["Appareil", "Vidéo", "Ajoutée le"])
        self.favs.setRootIsDecorated(True)
        self.favs.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.favs.itemDoubleClicked.connect(lambda item, _c: self._play_row(item))
        self.favs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.favs.customContextMenuRequested.connect(self._fav_menu)
        box.addWidget(self.favs, 1)
        hint = QLabel("Chaque personne a ses favoris, marqués d'une étoile dans le "
                      "lecteur du téléphone. Double-clic : regarder la vidéo. "
                      "Clic droit sur un appareil : lui donner un nom.", page)
        hint.setWordWrap(True)
        box.addWidget(hint)
        self._fav_rows = None
        return page

    def _fav_menu(self, point) -> None:
        from PySide6.QtWidgets import QMenu
        item = self.favs.itemAt(point)
        if item is None:
            return
        menu = QMenu(self)
        name = menu.addAction("Nommer cet appareil…")
        play = menu.addAction("Regarder dans le lecteur flottant") \
            if item.data(0, Qt.UserRole) else None
        chosen = menu.exec(self.favs.viewport().mapToGlobal(point))
        if chosen is name:
            self._name(item)
            self._fav_rows = None
        elif play is not None and chosen is play:
            self._play_row(item)

    def _fill_favorites(self, nas_favorites: list) -> None:
        from .access import when as _when
        rows = [row + ("",) for row in JOURNAL.all_favorites()]
        rows += [row + (" · NAS",) for row in nas_favorites]
        aliases = dict(self.window.cfg["share_aliases"] or {})
        key = (tuple(rows), tuple(sorted(aliases.items())))
        if key == self._fav_rows:
            return
        self._fav_rows = key
        self.favs.clear()
        groups: dict = {}
        for at, label, name, mark, where in sorted(rows, key=lambda r: -r[0]):
            parent = groups.get(label + where)
            if parent is None:
                parent = QTreeWidgetItem(self.favs, [(aliases.get(label) or label) + where,
                                                     "", ""])
                parent.setData(0, Qt.UserRole + 1, label)
                parent.setToolTip(0, label + "\nClic droit : lui donner un nom.")
                parent.setExpanded(True)
                groups[label + where] = parent
            child = QTreeWidgetItem(parent, ["", name, _when(at)])
            child.setData(0, Qt.UserRole, mark)
            child.setData(0, Qt.UserRole + 1, label)
        for label, parent in groups.items():
            parent.setText(1, f"{parent.childCount()} favori(s)")
        self.favs.resizeColumnToContents(0)

    def _row_path(self, item):
        """Le fichier d'une ligne du journal, ou None (et on dit pourquoi)."""
        mark = item.data(0, Qt.UserRole) if item is not None else ""
        path = self.window.video_for_mark(mark) if mark else None
        if path is None:
            QMessageBox.information(
                self, "Vidéo introuvable",
                "Cette vidéo n'est plus dans la collection : déplacée, "
                "renommée ou supprimée depuis.")
        return path

    def _play_row(self, item) -> None:
        path = self._row_path(item)
        if path is not None:
            self.window.play_floating_path(path)

    def _row_menu(self, point) -> None:
        from PySide6.QtWidgets import QMenu
        item = self.views.itemAt(point)
        if item is None:
            return
        menu = QMenu(self)
        play = menu.addAction("Regarder dans le lecteur flottant")
        show = menu.addAction("Montrer dans l'explorateur")
        menu.addSeparator()
        name = menu.addAction("Nommer cet appareil…")
        chosen = menu.exec(self.views.viewport().mapToGlobal(point))
        if chosen is name:
            self._name(item)
        elif chosen is play:
            self._play_row(item)
        elif chosen is show:
            path = self._row_path(item)
            if path is not None:
                self.window.reveal_path(str(path))

    def _visit_menu(self, point) -> None:
        from PySide6.QtWidgets import QMenu
        item = self.visits.itemAt(point)
        if item is None:
            return
        menu = QMenu(self)
        name = menu.addAction("Nommer cet appareil…")
        if menu.exec(self.visits.viewport().mapToGlobal(point)) is name:
            self._name(item)

    def _name(self, item) -> None:
        """Un nom pour l'appareil de cette ligne ; partout ensuite."""
        label = item.data(0, Qt.UserRole + 1) or ""
        if label and self.window.name_device(label, self):
            self._rows = (None, None)
            self.refresh()

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
        self._refresh_pc()
        self._refresh_nas()
        self._refresh_phone()
        self._fill_journals()

    def _refresh_pc(self) -> None:
        window = self.window
        on = bool(window.cfg["share"])
        self.pc_state.setText(window.share_state())
        home = window.share_home_link() if on else ""
        away = window.share_away_link() if on else ""
        if not on:
            closed = "Partage fermé (Réglages › « Autoriser le partage à distance »)."
            self._show_invite(self.pc_home, "", closed)
            self._show_invite(self.pc_away, "", closed)
        else:
            self._show_invite(self.pc_home, home,
                              "Partage sur le Wi-Fi désactivé, ou pas encore ouvert."
                              if window.share_server is None or not window.cfg["share_lan"]
                              else "Adresse du PC sur le Wi-Fi introuvable.")
            self._show_invite(self.pc_away, away,
                              "Adresse publique fermée : « Ouvrir l'adresse publique ».")
        public = bool(window.tunnel_address)
        self.pc_open.setVisible(on and not public)
        self.pc_open.setEnabled(not self._busy and window.share_server is not None)

    def _refresh_nas(self) -> None:
        from .nas_publish import NAS_PORT, PUBLISHER
        from .web import INVITE_PATH
        self.nas_state.setText(PUBLISHER.state)
        key = self.window.cfg["share_invite"]
        tailnet = self.window.nas_tailnet() if hasattr(self.window, "nas_tailnet") else ""
        home = (f"http://{PUBLISHER.lan}:{NAS_PORT}{INVITE_PATH}{key}"
                if PUBLISHER.lan and key else "")
        away = (f"https://prisme-nas.{tailnet}{INVITE_PATH}{key}"
                if tailnet and key else "")
        self._show_invite(self.nas_home, home, "Pas encore publié.")
        self._show_invite(self.nas_away, away,
                          "Nom Tailscale encore inconnu : ouvrez une fois l'adresse "
                          "fixe (Tailscale) du PC, dans « Réglages ».")

    def _nas_journal(self) -> tuple:
        """(connexions, visionnages) notes par le serveur du NAS, relus
        seulement quand son journal a change (on le recopie a chaque fois)."""
        from .access import visits_in, views_in
        from .nas_publish import FOLDER, share_root
        root = share_root(self.window.top_root() or "")
        if root is None:
            return [], []
        journal = root / FOLDER / "etat" / "acces.db"
        try:
            stamp = journal.stat().st_mtime
        except OSError:
            return [], []
        kept = getattr(self, "_nas_kept", None)
        if kept is None or kept[0] != stamp:
            from .access import favorites_in
            kept = (stamp, (visits_in(journal), views_in(journal)))
            self._nas_favorites = favorites_in(journal)
            self._nas_kept = kept
        visits, views = kept[1]
        # Tant que le NAS n'a pas efface ses visionnages, ils ne reviennent pas.
        if (journal.parent / "effacer-vues").exists():
            views = []
        return visits, views

    def _fill_journals(self) -> None:
        """Les journaux, refaits seulement s'ils ont change.

        Les refaire toutes les quatre secondes ramenait la liste en haut et
        perdait la ligne selectionnee, pour rien la plupart du temps.
        """
        nas_visits, nas_views = self._nas_journal()
        self._fill_favorites(getattr(self, "_nas_favorites", []))
        self._fill_people()
        visits = [row + ("",) for row in JOURNAL.visits()]
        visits += [row + (" · NAS",) for row in nas_visits]
        visits.sort(key=lambda row: row[0], reverse=True)
        views = [row + ("",) for row in JOURNAL.views()]
        views += [row + (" · NAS",) for row in nas_views]
        views.sort(key=lambda row: row[0], reverse=True)
        aliases = dict(self.window.cfg["share_aliases"] or {})
        visits = [row + (aliases.get(row[2], ""),) for row in visits]
        views = [row + (aliases.get(row[2], ""),) for row in views]
        if (visits, views) == self._rows:
            return
        self._rows = (visits, views)
        self.visits.clear()
        for at, ip, label, event, where, alias in visits:
            row = QTreeWidgetItem(self.visits, [
                when(at), ip, (alias or label) + where,
                "" if event == "entree" else event])
            row.setData(0, Qt.UserRole + 1, label)
            row.setToolTip(2, label + "\nClic droit : lui donner un nom.")
        self.views.clear()
        for at, _ip, label, name, seconds, mark, where, alias in views:
            row = QTreeWidgetItem(self.views, [
                when(at), (alias or label) + where, name, spell(seconds)])
            row.setData(0, Qt.UserRole, mark)
            row.setData(0, Qt.UserRole + 1, label)
            row.setToolTip(1, label + "\nClic droit : lui donner un nom.")
            row.setToolTip(2, "Double-clic : la regarder. Clic droit : plus.")
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
