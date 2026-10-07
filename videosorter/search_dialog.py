"""Recherche video sur le web : les sites qu'on aime, d'un seul geste.

Une fenetre a part, qu'on garde ouverte a cote de Prisme. A gauche, les sites
(un par ligne), les recherches (une par ligne) et les filtres. A droite, les
resultats en grille de vignettes -- duree, qualite, site --, chacun avec
« Télécharger » (dans le dossier choisi, par yt-dlp), « Ouvrir » et « Copier
le lien ». En bas, l'etat de chaque site : ce qu'il a rendu, ou pourquoi rien.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QEvent, QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QPainter, QPixmap,
    QSyntaxHighlighter, QTextCharFormat,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QGridLayout, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGraphicsOpacityEffect, QTabWidget,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QScrollArea, QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from .widgets import FlowLayout

from .downloader import Downloader, available as can_download
from .engine import Engine, next_number
from . import websession
from .config import PRIVATE_DIR

# La recherche en cours, gardee sur le disque (websession.py) : resultats,
# verifications, telechargements -- repris a la relance.
SESSION_PATH = PRIVATE_DIR / "recherche-web.json"
from .websearch import SearchFilters, USER_AGENT, VideoResult, site_root, split_lines
from .mediafind import Media

RESOLUTION_CHOICES = [
    ("Peu importe", 0), ("480p", 480), ("720p (HD)", 720),
    ("1080p (Full HD)", 1080), ("4K", 2160),
]
BROWSERS = [("Aucun", ""), ("Firefox", "firefox"), ("Chrome", "chrome"),
            ("Edge", "edge"), ("Brave", "brave"), ("Opera", "opera")]
SORTS = [("Ordre d'arrivée", "arrival"), ("Plus longues d'abord", "duration"),
         ("Meilleure qualité d'abord", "height"), ("Par site", "site"),
         ("Par titre", "title")]

CARD_W, CARD_H = 232, 214
THUMB_W, THUMB_H = 220, 124


def set_card_width(width: int) -> None:
    """La taille des cartes, reglee par le curseur « Taille » : la vignette
    en 16/9, et sous elle le titre, le site et les boutons."""
    global CARD_W, CARD_H, THUMB_W, THUMB_H
    CARD_W = int(width)
    THUMB_W = CARD_W - 12
    THUMB_H = round(THUMB_W * 9 / 16)
    CARD_H = THUMB_H + 90
MAX_THUMB_BYTES = 4 * 1024 * 1024
CHECK_STYLE = ("QLabel { background: %s; color: #fff; font-size: 10px; font-weight: 600;"
               " padding: 1px 6px; border-radius: 4px; }")
BADGE_STYLE = ("QLabel { background: rgba(0,0,0,.78); color: #fff; font-size: 11px;"
               " font-weight: 700; padding: 1px 6px; border-radius: 5px; }")
QUALITY_STYLE = ("QLabel { background: #4f8bf0; color: #fff; font-size: 10px;"
                 " font-weight: 800; padding: 1px 5px; border-radius: 4px; }")

STYLE = """
QWidget#webSearch { background: #0e1116; }
QWidget#webSearch QLabel, QWidget#webSearch QCheckBox { color: #c9d1db; }
QWidget#webSearch QTabBar::tab { color: #c9d1db; background: #161b22; padding: 4px 10px; }
QWidget#webSearch QTabBar::tab:selected { background: #222a35; color: #ffffff; }
QLabel#head { color: #ffffff; font-size: 13px; font-weight: 600; }
QLabel#dim { color: #8b94a1; font-size: 12px; }
QFrame#card { background: #151a21; border: 1px solid #1f2630; border-radius: 10px; }
QFrame#card:hover { border-color: #3a4452; }
QLabel#thumb { background: #07090c; border-radius: 8px; }
QLabel#badge { background: rgba(0,0,0,.78); color: #fff; font-size: 11px;
  font-weight: 700; padding: 1px 6px; border-radius: 5px; }
QLabel#quality { background: #4f8bf0; color: #fff; font-size: 10px;
  font-weight: 800; padding: 1px 5px; border-radius: 4px; }
QLabel#title { color: #e9eef4; font-size: 12px; }
QLabel#site { color: #8b94a1; font-size: 11px; }
QPushButton#get { background: #2f6fed; border: 0; color: #fff; font-weight: 600;
  padding: 4px 10px; border-radius: 6px; }
QPushButton#get:disabled { background: #22324f; color: #8fa6cc; }
QPushButton#small { padding: 4px 8px; border-radius: 6px; }
QListWidget#results { background: #0b0d10; border: 0; }
QCheckBox#pick { background: transparent; border: 0; padding: 0; }
QCheckBox#pick::indicator { width: 18px; height: 18px; border: 1px solid #9aa6b4;
  border-radius: 4px; background: rgba(8, 10, 13, 0.75); }
QCheckBox#pick::indicator:hover { border-color: #ffffff; }
QCheckBox#pick::indicator:checked { background: #2f6fed; border-color: #2f6fed; }
QFrame#card[picked="true"] { border: 2px solid #2f6fed; }
QWidget#pickBar { background: #14213a; border: 1px solid #2f4a78; border-radius: 8px; }
QWidget#pickBar QLabel { color: #dfe8f5; font-weight: 600; }
QWidget#pickBar QPushButton#small { color: #dfe8f5; background: #1f3d6e; border: 0;
  border-radius: 6px; padding: 4px 10px; }
QWidget#pickBar QPushButton#small:hover { background: #2a4f8c; }
QWidget#webSearch QScrollBar:vertical { background: #0e1116; width: 10px; margin: 0; }
QWidget#webSearch QScrollBar::handle:vertical { background: #2a323d; border-radius: 5px;
  min-height: 30px; }
QWidget#webSearch QScrollBar::handle:vertical:hover { background: #3a4452; }
QWidget#webSearch QScrollBar::add-line:vertical, QWidget#webSearch QScrollBar::sub-line:vertical
  { height: 0; }
QWidget#webSearch QScrollBar::add-page:vertical, QWidget#webSearch QScrollBar::sub-page:vertical
  { background: none; }
QToolButton#chip { background: #161b22; color: #c9d1db; border: 1px solid #2a323d;
  border-radius: 13px; padding: 3px 11px 3px 8px; font-size: 12px; }
QToolButton#chip:hover { border-color: #4a5566; color: #ffffff; }
QToolButton#chip:checked { background: #1d3357; border-color: #4f8bf0; color: #ffffff; }
QToolButton#chip[empty="true"] { color: #5d6672; }
QProgressBar { background: #0b0d10; border: 0; border-radius: 3px; height: 6px;
  text-align: center; color: transparent; }
QProgressBar::chunk { background: #4f8bf0; border-radius: 3px; }
"""


def web_address(url: str) -> QUrl | None:
    """L'adresse si elle mene au web (http, https), None sinon.

    Dernier rempart, au moment de charger ou d'ouvrir : un « file://hote/… »
    ferait ouvrir un partage de fichiers etranger, et presenter l'empreinte
    du compte Windows a qui le tient.
    """
    address = QUrl(str(url or ""))
    if address.scheme().lower() not in ("http", "https") or not address.host():
        return None
    return address


class _SearchWorker:
    """Une recherche, menee par le processus de recherche (engine.py).

    Ce qu'elle trouve arrive dans une boite aux lettres que la fenetre releve
    dix fois par seconde, sur son fil. Menee ici meme, dans des fils de ce
    processus, elle decortiquait ses pages en Python pendant que la fenetre
    attendait son tour : Prisme gelait pendant toute la recherche."""

    def __init__(self, engine, filters: SearchFilters, templates: dict, generation: int,
                 browser: str = "", renderer=None):
        self.engine = engine
        self.filters = filters
        self.templates = templates
        self.generation = generation
        self.browser = browser
        self.renderer = renderer
        self.number = next_number()
        self.mailbox: queue.SimpleQueue = queue.SimpleQueue()

    def stop(self) -> None:
        self.engine.send("stop_search", self.number)

    def start(self) -> None:
        self.engine.listen("search", self.number, self.mailbox)
        self.engine.send("search", self.number, self.filters, self.templates,
                         self.browser, self.renderer is not None)


HEALTH_VERSION = 2

# Un site en rouge est mis de cote un moment, au lieu d'etre reessaye a
# chaque recherche : c'etait jusqu'a deux minutes par site, a chaque fois,
# pour le meme refus. Une panne passagere se reessaie le lendemain ; un refus
# durable (anti-robot, pas de recherche), trois jours apres.
REST_PASSING_S = 24 * 3600
REST_LASTING_S = 3 * 24 * 3600
_PASSING = ("injoignable", "timeout", "http 5", "trop de demandes", "trop lentement",
            "interrompue", "pas répondu", "ne répond", "nom introuvable")


# Ce que veut dire un rouge, selon sa raison. Seuls les « morts » (nom de
# domaine inexistant pour deux annuaires publics, ou domaine a vendre) sont
# proposes a la suppression ; un site « renomme » se corrige.
RED_KINDS = {
    "dead": "Mort, avec certitude",
    "renamed": "Renommé : il a changé d'adresse",
    "robots": "Refuse les robots : utilisable seulement à la main (navigateur, puis coller le lien)",
    "later": "En panne ou trop lent pour l'instant : réessayé plus tard",
    "search": "Recherche introuvable pour l'instant",
}


def red_kind(reason: str) -> str:
    text = (reason or "").lower()
    if "n'existe plus (confirmé" in text or "à vendre" in text:
        return "dead"
    if "mène à un autre site" in text:
        return "renamed"
    if "robot" in text or "bloque les recherches automatiques" in text or "bloqué (un vpn" in text:
        return "robots"
    if any(word in text for word in _PASSING) or "navigateur n'a pas" in text:
        return "later"
    return "search"


def renamed_to(reason: str) -> str:
    """La nouvelle adresse d'un site qui a demenage : « déménagé vers
    goldmaal.cc » (redirection suivie), ou l'ancien « … autre site
    (goldmaal.cc) … »."""
    found = (re.search(r"déménagé vers (\S+)", reason or "")
             or re.search(r"autre site \(([^)\s]+)\)", reason or ""))
    return found.group(1).rstrip(";,") if found else ""


def red_rest_left(entry, now: float) -> float:
    """Secondes pendant lesquelles ce site en rouge reste de cote (0 : a
    reessayer). `entry` : [etat, raison, quand]."""
    if not entry or entry[0] != "erreur" or len(entry) < 3:
        return 0.0
    reason = str(entry[1]).lower()
    rest = REST_PASSING_S if any(word in reason for word in _PASSING) else REST_LASTING_S
    return max(0.0, float(entry[2] or 0) + rest - now)
STATE_COLORS = {"ok": "#7bd88f", "vide": "#d8c05a", "erreur": "#e26d76"}


class _Verifier:
    """Suit chaque resultat jusqu'a sa video entiere -- apres la recherche,
    dans le processus de recherche.

    Les cartes a l'ecran passent d'abord ; chacune dit ensuite « telechargeable,
    telle qualite, tel poids » ou non."""

    def __init__(self, engine, ffprobe: str, browser: str = ""):
        self.engine = engine
        self.ffprobe = ffprobe
        self.browser = browser
        self.number = next_number()
        self.stopped = False
        self.sent = self.received = 0
        self.visible: tuple = ()
        self.lock = threading.Lock()
        self.mailbox: queue.SimpleQueue = queue.SimpleQueue()
        engine.listen("verify", self.number, self)

    def put(self, value) -> None:
        """Une verification rendue par le processus de recherche."""
        with self.lock:
            self.received += 1
        self.mailbox.put(value)

    def add(self, key, url: str, expect, origin: str = "") -> None:
        with self.lock:
            self.sent += 1
        self.engine.send("verify", self.number, key, url, expect, origin,
                         self.ffprobe, self.browser)

    def prefer(self, keys) -> None:
        """Les cartes a l'ecran passent devant (dit au processus seulement
        quand elles changent)."""
        keys = tuple(keys)
        if keys != self.visible and not self.stopped:
            self.visible = keys
            self.engine.send("prefer", list(keys))

    def stop(self) -> None:
        if not self.stopped:
            self.stopped = True
            self.engine.send("stop_verify", next_number())

    def busy(self) -> bool:
        with self.lock:
            return not self.stopped and self.received < self.sent


def letter_icon(name: str, size: int = 32) -> QIcon:
    """L'initiale du site sur une pastille de couleur : en attendant son
    icone, ou quand il n'en a pas."""
    colors = ("#4f8bf0", "#e0864a", "#57b37c", "#b36bd6", "#d6566b", "#3fb6c2", "#c9a93b")
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(colors[sum(map(ord, name)) % len(colors)]))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(0, 0, size, size)
    font = QFont()
    font.setBold(True)
    font.setPixelSize(int(size * 0.55))
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, (name[:1] or "?").upper())
    painter.end()
    return QIcon(pixmap)


class SiteChips(QWidget):
    """Les sites qui ont donne des resultats, en puces : icone, nom, nombre.

    Rien de coche (« Tous ») : tout s'affiche. Cocher des puces n'affiche
    que ces sites-la -- un ou plusieurs ; tout decocher revient a « Tous ».

    Deux rangees au plus, repliee : les sites les plus fournis, et « + N
    sites » pour le reste. Deux cent soixante-quinze sites couvraient toute la
    page. Depliee, la rangee defile (cinq rangees au plus) et un champ filtre
    les sites par leur nom. Un site coche reste toujours en vue.
    """

    changed = Signal()
    ROWS = 2                                # repliee
    OPEN_ROWS = 5                           # depliee, avant de defiler

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        self.find = QLineEdit(self)
        self.find.setPlaceholderText("Filtrer les sites…")
        self.find.setClearButtonEnabled(True)
        self.find.setMaximumWidth(260)
        self.find.textChanged.connect(lambda _t: self._fit())
        # Depliee : le filtre et « Replier », toujours en vue au-dessus des
        # puces (en bout de liste, il fallait faire defiler pour replier).
        self.head = QWidget(self)
        head = QHBoxLayout(self.head)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        head.addWidget(self.find)
        self.less = QToolButton(self.head)
        self.less.setObjectName("chip")
        self.less.setCursor(Qt.PointingHandCursor)
        self.less.setText("Replier ▴")
        self.less.clicked.connect(self._toggle_more)
        head.addWidget(self.less)
        head.addStretch(1)
        self.head.hide()
        outer.addWidget(self.head)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget "
                                   "{ background: transparent; }")
        self._inner = QWidget()
        self._flow = FlowLayout(self._inner, spacing=6)
        self._scroll.setWidget(self._inner)
        outer.addWidget(self._scroll)
        self._chips: dict = {}              # domaine -> bouton
        self._counts: dict = {}
        self._expanded = False
        self._sorted = False                # rangee dans l'ordre des nombres ?
        # Les icones arrivent d'un fil a part, par cette boite aux lettres :
        # demandees par le gestionnaire reseau de Qt, par centaines, elles
        # faisaient planter Prisme de temps en temps.
        self._icon_mail: queue.SimpleQueue = queue.SimpleQueue()
        self._icon_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._icon_threads = 0
        self._icon_timer = QTimer(self)
        self._icon_timer.setInterval(250)
        self._icon_timer.timeout.connect(self._take_icons)
        self.all = self._make("Tous", QIcon())
        self.all.setChecked(True)
        self.all.setToolTip("Afficher les résultats de tous les sites")
        self.all.clicked.connect(self._show_all)
        self.more = self._make("", QIcon())
        self.more.setCheckable(False)
        self.more.setToolTip("Voir tous les sites qui ont donné des résultats")
        self.more.clicked.connect(self._toggle_more)
        self.hide()

    def _make(self, label: str, icon: QIcon) -> QToolButton:
        chip = QToolButton(self._inner)
        chip.setObjectName("chip")
        chip.setCheckable(True)
        chip.setCursor(Qt.PointingHandCursor)
        chip.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        chip.setIconSize(QSize(16, 16))
        chip.setIcon(icon)
        chip.setText(label)
        self._flow.addWidget(chip)
        return chip

    def reset(self) -> None:
        for chip in self._chips.values():
            self._flow.removeWidget(chip)
            chip.deleteLater()
        self._chips.clear()
        self._counts.clear()
        self._expanded = self._sorted = False
        self.find.clear()
        self.all.setChecked(True)
        self.hide()

    def selected(self) -> set:
        return {name for name, chip in self._chips.items() if chip.isChecked()}

    def add(self, domain: str) -> None:
        if not domain or domain in self._chips:
            return
        name = short_site(domain)
        chip = self._make(name, letter_icon(name))
        chip.setToolTip(f"N'afficher que {name} — cliquez-en plusieurs pour les "
                        "combiner ; « Tous » réaffiche tout")
        chip.toggled.connect(lambda _on, c=chip: self._toggled(c))
        self._chips[domain] = chip
        self._fetch_icon(domain)
        self.show()
        self._fit()

    def set_counts(self, counts: dict, total: int) -> None:
        """Le nombre de resultats de chaque site, tels que les autres filtres
        les laissent voir (un site a zero reste la, grise)."""
        self._counts = counts
        if self.all.text() != f"Tous · {total}":
            self.all.setText(f"Tous · {total}")
        for domain, chip in self._chips.items():
            number = counts.get(domain, 0)
            label = ("✓ " if chip.isChecked() else "") + f"{short_site(domain)} · {number}"
            if chip.text() != label:            # chaque texte change refait la rangee
                chip.setText(label)
            if chip.property("empty") != (number == 0):
                chip.setProperty("empty", number == 0)
                chip.style().unpolish(chip)
                chip.style().polish(chip)
        self._fit()

    def sort_by_count(self) -> None:
        """La recherche finie : du site le plus fourni au moins fourni."""
        self._sorted = True
        self._fit()

    # -- repliee, depliee -------------------------------------------------------
    def _order(self) -> list:
        """Repliee, ou la recherche finie : les plus fournis d'abord (ce sont
        eux qu'on veut voir). Depliee pendant la recherche : l'ordre d'arrivee,
        pour qu'une puce ne se derobe pas sous le doigt."""
        if self._expanded and not self._sorted:
            return list(self._chips)
        return sorted(self._chips, key=lambda d: (-self._counts.get(d, 0), short_site(d)))

    def _toggle_more(self) -> None:
        self._expanded = not self._expanded
        self.head.setVisible(self._expanded)
        if self._expanded:
            self.find.setFocus()
        else:
            self.find.clear()
        self._fit()

    def _fit(self) -> None:
        """Montre les puces qui tiennent (repliee) ou toutes (depliee), dans
        l'ordre voulu, et regle la hauteur de la rangee."""
        if not self._chips:
            return
        order = self._order()
        wanted = [self.all] + [self._chips[d] for d in order] + [self.more]
        current = [self._flow.itemAt(i).widget() for i in range(self._flow.count())]
        if current != wanted:
            for chip in wanted:
                self._flow.removeWidget(chip)
            for chip in wanted:
                self._flow.addWidget(chip)
        text = self.find.text().strip().lower()
        spacing = self._flow.spacing()
        width = max(200, self._scroll.viewport().width() or self.width())
        if self._expanded:
            shown = [d for d in order if not text or text in d.lower()]
        else:
            # Ce qui tient en deux rangees, la puce « + N » comprise ; les
            # sites coches toujours.
            more_width = self.more.sizeHint().width() + 40
            rows, x, shown = 1, self.all.sizeHint().width() + spacing, []
            for domain in order:
                w = self._chips[domain].sizeHint().width()
                if x + w > width:
                    rows, x = rows + 1, 0
                if rows > self.ROWS or (rows == self.ROWS and x + w + spacing + more_width > width):
                    break
                shown.append(domain)
                x += w + spacing
            shown += [d for d in order if self._chips[d].isChecked() and d not in shown]
        keep = set(shown)
        for domain, chip in self._chips.items():
            visible = domain in keep
            if chip.isVisibleTo(self._inner) != visible:
                chip.setVisible(visible)
        hidden = len(self._chips) - len(keep)
        if self._expanded:
            self.more.hide()                    # « Replier » est au-dessus
        else:
            self.more.setText(f"+ {hidden} site{'s' if hidden > 1 else ''} ▾")
            self.more.setVisible(hidden > 0)
        self._flow.invalidate()
        needed = self._flow.heightForWidth(width)
        row = self.all.sizeHint().height() + spacing
        limit = row * (self.OPEN_ROWS if self._expanded else self.ROWS)
        self._scroll.setFixedHeight(min(needed, limit) + 2)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if self._expanded else Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._expanded:
            QTimer.singleShot(0, self._fit)

    def _toggled(self, _chip) -> None:
        self.all.setChecked(not self.selected())
        self.set_counts(self._counts, sum(self._counts.values()))
        self.changed.emit()

    def _show_all(self) -> None:
        for chip in self._chips.values():
            chip.blockSignals(True)
            chip.setChecked(False)
            chip.blockSignals(False)
        self.all.setChecked(True)
        self.set_counts(self._counts, sum(self._counts.values()))
        self.changed.emit()

    # L'icone du site : son « favicon.ico », demande au site lui-meme (pas a
    # un service tiers, qui apprendrait la liste des sites cherches).
    ICON_THREADS = 4

    def _fetch_icon(self, domain: str) -> None:
        self._icon_queue.put(domain)
        self._icon_timer.start()
        if self._icon_threads < self.ICON_THREADS:
            self._icon_threads += 1
            threading.Thread(target=self._icon_worker, daemon=True,
                             name="icones-sites").start()

    def _icon_worker(self) -> None:
        from .mediafind import safe_session
        session = safe_session()
        while True:
            try:
                domain = self._icon_queue.get(timeout=5)
            except queue.Empty:
                break
            data = b""
            try:
                with session.get(f"https://{domain}/favicon.ico", timeout=8, stream=True,
                                 headers={"User-Agent": USER_AGENT}) as answer:
                    if answer.ok and "html" not in answer.headers.get("Content-Type", ""):
                        data = answer.raw.read(256_000, decode_content=True)
            except Exception:                               # noqa: BLE001
                pass
            if data:
                self._icon_mail.put((domain, data))
        self._icon_mail.put((None, b""))                    # ce fil s'en va

    def _take_icons(self) -> None:
        for _ in range(40):
            try:
                domain, data = self._icon_mail.get_nowait()
            except queue.Empty:
                return
            if domain is None:
                self._icon_threads = max(0, self._icon_threads - 1)
                if not self._icon_threads:
                    self._icon_timer.stop()
                continue
            chip = self._chips.get(domain)
            pixmap = QPixmap()
            if chip is not None and pixmap.loadFromData(data) and not pixmap.isNull():
                chip.setIcon(QIcon(pixmap.scaled(32, 32, Qt.KeepAspectRatio,
                                                 Qt.SmoothTransformation)))


def short_site(line: str) -> str:
    """« https://www.pandeb.com/ » -> « pandeb.com » ; une adresse de recherche
    (avec {q}) garde son chemin, pour qu'on la reconnaisse."""
    text = line.strip()
    text = re.sub(r"^[a-z]+://", "", text, flags=re.I)
    text = re.sub(r"^www\.", "", text, flags=re.I)
    return text if "{q}" in text else text.rstrip("/")


def site_name(line: str) -> str:
    """Le nom sous lequel la recherche parle d'une ligne de la liste."""
    line = line.strip()
    return urlparse(site_root(line) or line).netloc or line


class SiteColors(QSyntaxHighlighter):
    """Chaque ligne de la liste des sites, a la couleur de sa derniere
    recherche : vert, il a donne des videos ; jaune, sa recherche marche mais
    rien n'a passe les filtres ; rouge (barre), rien a en tirer -- il bloque
    les robots, ou sa recherche est introuvable."""

    def __init__(self, document, health: dict):
        super().__init__(document)
        self.health = health

    def highlightBlock(self, text: str) -> None:
        if not text.strip():
            return
        state = (self.health.get(site_name(text)) or ["", ""])[0]
        color = STATE_COLORS.get(state)
        if not color:
            return
        form = QTextCharFormat()
        form.setForeground(QColor(color))
        if state == "erreur":
            form.setFontStrikeOut(True)
        self.setFormat(0, len(text), form)


class _Item(QListWidgetItem):
    """Une case de la grille, triable selon le choix du moment."""

    order = "arrival"

    def __init__(self, video: VideoResult, rank: int):
        super().__init__()
        self.video = video
        self.rank = rank
        self.setSizeHint(QSize(CARD_W, CARD_H))

    def key(self):
        v = self.video
        if _Item.order == "duration":
            return (-(v.duration_s or 0), self.rank)
        if _Item.order == "height":
            return (-(v.height or 0), -(v.duration_s or 0), self.rank)
        if _Item.order == "site":
            return (v.source_domain, self.rank)
        if _Item.order == "title":
            return (v.title.lower(), self.rank)
        return (self.rank,)

    def __lt__(self, other) -> bool:
        return self.key() < other.key()


class Card(QFrame):
    """Une video : sa vignette, sa duree, sa qualite, son titre, et ce qu'on
    peut en faire."""

    def __init__(self, window, video: VideoResult):
        super().__init__()
        self.window = window
        self.video = video
        self.job = 0
        self.path = ""
        self._pix = None
        self.setObjectName("card")
        self.setFixedSize(CARD_W - 8, CARD_H - 8)
        box = QVBoxLayout(self)
        box.setContentsMargins(5, 5, 5, 5)
        box.setSpacing(2)
        self.thumb = QLabel(self)
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedSize(THUMB_W - 8, THUMB_H - 8)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setCursor(Qt.PointingHandCursor)
        self.thumb.setToolTip("Double-clic : ouvrir la vidéo sur le site")
        box.addWidget(self.thumb)
        # Leur style porte par eux-memes : la carte n'est pas encore dans la
        # fenetre quand on les mesure, et sans lui « 2:05 » devenait « 2 ».
        self.duration_badge = QLabel("", self.thumb)
        self.duration_badge.setStyleSheet(BADGE_STYLE)
        self.quality_badge = QLabel("", self.thumb)
        self.quality_badge.setStyleSheet(QUALITY_STYLE)
        # L'etat de la verification, en haut a droite de la vignette.
        self.check = QLabel("", self.thumb)
        self.check.setStyleSheet(CHECK_STYLE % "#8b94a1")
        self.check.hide()
        # La coche, en haut a gauche : en cocher plusieurs, puis tout
        # telecharger d'un coup (la barre au-dessus des resultats).
        self.pick = QCheckBox(self.thumb)
        self.pick.setObjectName("pick")
        self.pick.setCursor(Qt.PointingHandCursor)
        self.pick.setFocusPolicy(Qt.NoFocus)
        self.pick.setToolTip("Cocher pour télécharger plusieurs vidéos d'un coup")
        self.pick.move(8, 8)
        self.pick.toggled.connect(self._picked)
        self.pick.hide()
        self._badges()
        title = QLabel(self)
        title.setObjectName("title")
        title.setWordWrap(True)
        title.setFixedHeight(30)
        title.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        title.setText(video.title)
        title.setToolTip(f"{video.title}\n{video.page_url}")
        box.addWidget(title)
        self.site = QLabel(self._where(), self)
        self.site.setObjectName("site")
        box.addWidget(self.site)
        self.bar = QProgressBar(self)
        self.bar.setFixedHeight(6)
        self.bar.setRange(0, 1000)
        self.bar.hide()
        box.addWidget(self.bar)
        row = QHBoxLayout()
        row.setSpacing(4)
        self.get = QPushButton("Télécharger", self)
        self.get.setObjectName("get")
        self.get.clicked.connect(self._get)
        row.addWidget(self.get, 1)
        opener = QPushButton("Ouvrir", self)
        opener.setObjectName("small")
        opener.clicked.connect(lambda: self.window.open_url(video.page_url))
        row.addWidget(opener)
        copy = QPushButton("Lien", self)
        copy.setObjectName("small")
        copy.setToolTip("Copier le lien")
        copy.clicked.connect(self._copy)
        row.addWidget(copy)
        box.addLayout(row)

    def _picked(self, on: bool) -> None:
        self.setProperty("picked", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.window.picked_changed(self, on)

    def show_pick(self, force: bool = False) -> None:
        """La coche se montre au survol, cochee, ou des qu'une autre l'est."""
        self.pick.setVisible(force or self.pick.isChecked() or self.underMouse())

    def enterEvent(self, event):
        self.pick.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.show_pick(bool(self.window._picked))
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.window.open_url(self.video.page_url)

    def _badges(self) -> None:
        """Duree et qualite sur la vignette -- celles du vrai fichier des
        qu'il est verifie."""
        video = self.video
        for badge, text in ((self.duration_badge, video.duration_label if video.duration_s
                             else ""),
                            (self.quality_badge, video.resolution_label if video.height
                             else "")):
            badge.setText(text)
            badge.setVisible(bool(text))
            badge.adjustSize()
        self.duration_badge.move(self.thumb.width() - self.duration_badge.width() - 6,
                                 self.thumb.height() - self.duration_badge.height() - 6)
        self.quality_badge.move(6, 6)

    def _mark(self, text: str, color: str) -> None:
        self.check.setText(text)
        self.check.setStyleSheet(CHECK_STYLE % color)
        self.check.adjustSize()
        self.check.move(self.thumb.width() - self.check.width() - 6, 6)
        self.check.show()

    def pending(self) -> None:
        self._mark("vérification…", "#8b94a1")

    def waiting(self) -> None:
        self._mark("à vérifier", "#5d6672")

    def verified(self, media, why: str) -> None:
        """La video entiere est trouvee (et mesuree), ou non."""
        video = self.video
        if media is not None:
            video.media = media
            video.duration_s = int(media.duration) or video.duration_s
            video.height = media.height or video.height
            video.size = media.size
            video.unavailable = ""
            if not video.thumbnail_url and getattr(media, "poster", ""):
                # Arrivee sans vignette (xhamster) : l'image de sa page.
                video.thumbnail_url = media.poster
                self.window._fetch_thumbnail(self, media.poster, video.page_url)
            self._badges()
            self._mark("✓ téléchargeable", "#3f9d5a")
            if not self.job and not self.path:
                self.site.setText(self._where())
            return
        video.unavailable = why or "pas de vidéo entière"
        self._mark("✗ pas téléchargeable", "#b0424c")
        if not self.job and not self.path:
            self.site.setText(video.unavailable)
            self.site.setToolTip(video.unavailable)
            self.get.setText("Essayer quand même")
        fade = QGraphicsOpacityEffect(self)
        fade.setOpacity(0.45)
        self.setGraphicsEffect(fade)

    def fit(self) -> None:
        """Reprend la taille du moment (le curseur « Taille »)."""
        self.setFixedSize(CARD_W - 8, CARD_H - 8)
        self.thumb.setFixedSize(THUMB_W - 8, THUMB_H - 8)
        self._badges()
        if self._pix is not None:
            self.set_thumbnail(self._pix)

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        self._pix = pixmap
        self.thumb.setPixmap(pixmap.scaled(self.thumb.size(), Qt.KeepAspectRatioByExpanding,
                                           Qt.SmoothTransformation)
                             .copy(0, 0, self.thumb.width(), self.thumb.height()))

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.video.page_url)
        self.site.setText("Lien copié.")
        QTimer.singleShot(1500, lambda: self.site.setText(self._where()))

    def _where(self) -> str:
        """Le site, et le poids du fichier entier quand on l'a mesure."""
        video = self.video
        weight = f"  ·  {video.size / 1e6:.0f} Mo" if video.size else ""
        return f"{video.source_domain}{weight}  ·  « {video.query} »"

    def _get(self) -> None:
        if self.path:
            return self.window.reveal(self.path)
        if self.job:
            self.window.downloader.cancel(self.job)
            return
        self.job = self.window.download(self)

    # -- l'avancement du telechargement ------------------------------------
    def started(self) -> None:
        self.get.setText("Annuler")
        self.bar.show()
        self.bar.setRange(0, 0)

    def advanced(self, fraction: float, text: str) -> None:
        if fraction >= 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(fraction * 1000))
        self.site.setText(text)

    def done(self, path: str, error: str) -> None:
        self.job = 0
        self.bar.hide()
        if error:
            self.get.setText("Réessayer")
            self.site.setText(error)
            self.site.setToolTip(error)
            self.site.setStyleSheet("color: #e26d76;")
            return
        self.path = path
        self.get.setText("✓ Dans la collection")
        self.get.setToolTip("Montrer le fichier dans l'explorateur")
        self.site.setText(Path(path).name if path else "téléchargée")
        self.site.setStyleSheet("color: #7bd88f;")


class WebSearchWindow(QWidget):
    """La recherche video sur le web : une fenetre a part, qu'on garde ouverte."""

    def __init__(self, cfg, parent=None, ffmpeg: str = ""):
        super().__init__(parent, Qt.Window)
        self.cfg = cfg
        self.setObjectName("webSearch")
        self.setWindowTitle("Prisme — Recherche vidéo sur le web")
        self.setStyleSheet(STYLE)
        self.resize(1360, 860)
        self._worker = None
        self._generation = 0
        self._mail = QTimer(self)
        self._mail.setInterval(100)
        self._mail.timeout.connect(self._read_mail)
        self._net = QNetworkAccessManager(self)
        self._thumbs: dict = {}
        self._cards: dict = {}
        self._rank = 0
        self._sites: dict = {}
        self.downloader = Downloader(self)
        self.downloader.ffmpeg = ffmpeg
        self.downloader.progress.connect(self._download_progress)
        self.downloader.finished.connect(self._download_done)
        self._jobs: dict = {}
        # Chaque telechargement, tel qu'il se note sur le disque : travail ->
        # adresse, dossier, fichier trouve, etat. Ceux d'avant la relance, deja
        # finis, attendent dans `_past_downloads`.
        self._downloads: dict = {}
        self._past_downloads: list = []
        self._session_timer = QTimer(self)
        self._session_timer.setSingleShot(True)
        self._session_timer.setInterval(2000)
        self._session_timer.timeout.connect(self._save_session)
        self._quitting = False
        self._picked: dict = {}             # carte -> carte cochee
        self._job_text: dict = {}           # travail -> dernier avancement dit
        self._verifier = None
        self._searching = False
        self._site_total = 0
        self._checked = self._good = 0
        self._look = QTimer(self)
        self._look.setInterval(250)
        self._look.timeout.connect(self._look_around)
        # Les resultats a verifier, gardes jusqu'a la fin de la recherche :
        # verifier en meme temps la ralentissait, et le navigateur invisible
        # ne servait plus qu'a moitie a chercher.
        self._to_verify: list = []
        self._harvest: list = []            # recherches arretees, dont on attend les adresses
        # Compteur, puces, couleurs des sites : refaits par paquets, au plus
        # trois fois par seconde. Refaits a chaque resultat et a chaque site,
        # ils figeaient la fenetre pendant toute la recherche.
        self._refresh = QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(300)
        self._refresh.timeout.connect(self._refresh_now)
        self._health_dirty = False

        outer = QHBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        split = QSplitter(Qt.Horizontal, self)
        outer.addWidget(split)
        split.setHandleWidth(8)
        self.panel = self._panel()
        split.addWidget(self.panel)
        split.addWidget(self._results())
        split.setStretchFactor(1, 1)
        split.setCollapsible(1, False)
        # Assez large pour lire les noms des sites sur deux colonnes.
        split.setSizes([380, 980])
        self.main_split = split
        self._load_settings()
        QTimer.singleShot(0, self._restore_session)

    # -- a gauche : ce qu'on cherche, et ou ------------------------------------
    def _panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setMinimumWidth(280)
        box = QVBoxLayout(panel)
        box.setContentsMargins(0, 0, 6, 0)
        head = QLabel("Sites", panel)
        head.setObjectName("head")
        box.addWidget(head)
        # Deux faces : cocher les sites de cette recherche-ci, ou coller et
        # modifier la liste en texte, un par ligne, comme avant.
        self.site_tabs = QTabWidget(panel)
        choose = QWidget(self.site_tabs)
        choose_box = QVBoxLayout(choose)
        choose_box.setContentsMargins(0, 4, 0, 0)
        self.site_list = QListWidget(choose)
        self.site_list.setToolTip("Cochez les sites à interroger pour cette recherche.\n"
                                  "Vert : il a donné des vidéos ; jaune : rien pour ces "
                                  "mots ; rouge : rien à en tirer.")
        self.site_list.itemChanged.connect(self._site_checked)
        # Deux colonnes : on voit deux fois plus de sites d'un coup (ils sont
        # des centaines) ; un nom trop long se termine par « … ».
        self.site_list.setFlow(QListView.LeftToRight)
        self.site_list.setWrapping(True)
        self.site_list.setResizeMode(QListView.Adjust)
        self.site_list.setUniformItemSizes(False)   # sinon tous prennent la largeur du premier nom
        self.site_list.setTextElideMode(Qt.ElideRight)
        self.site_list.viewport().installEventFilter(self)
        choose_box.addWidget(self.site_list, 1)
        buttons = QHBoxLayout()
        every = QPushButton("Tout", choose)
        every.clicked.connect(lambda: self._check_all(True))
        none = QPushButton("Aucun", choose)
        none.clicked.connect(lambda: self._check_all(False))
        buttons.addWidget(every)
        buttons.addWidget(none)
        buttons.addStretch(1)
        choose_box.addLayout(buttons)
        self.site_tabs.addTab(choose, "Choisir")
        self.sites = QPlainTextEdit(self.site_tabs)
        self.sites.setPlaceholderText(
            "exemple.com\nautre-site.net\nsite.org/search?q={q}  ← si besoin")
        self.sites.setToolTip(
            "Le nom du site suffit : Prisme trouve sa page de recherche et la "
            "retient.\nS'il n'y arrive pas, collez l'adresse d'une recherche en "
            "remplaçant les mots cherchés par {q}.")
        self.site_tabs.addTab(self.sites, "Coller / modifier")
        # Les sites prennent toute la hauteur libre : on en voit le plus
        # possible d'un coup (ils sont des centaines). Une poignee les separe
        # des recherches et reglages : la tirer donne plus de place a l'un ou
        # a l'autre.
        lower = QWidget(panel)
        lower_box = QVBoxLayout(lower)
        lower_box.setContentsMargins(0, 0, 0, 0)
        side = QSplitter(Qt.Vertical, panel)
        side.setHandleWidth(8)
        side.addWidget(self.site_tabs)
        side.addWidget(lower)
        side.setStretchFactor(0, 1)
        side.setCollapsible(0, False)
        box.addWidget(side, 1)
        box = lower_box
        self._unchecked = set(self.cfg.get("web_search_unchecked", []) or [])
        self._list_timer = QTimer(self)
        self._list_timer.setSingleShot(True)
        self._list_timer.setInterval(400)
        self._list_timer.timeout.connect(self._fill_site_list)
        self.sites.textChanged.connect(self._list_timer.start)
        # Les couleurs d'avant les certificats de Windows (29/09) disaient
        # « injoignable » a tort : on ne les garde pas.
        if self.cfg.get("web_search_health_v", 0) != HEALTH_VERSION:
            self.cfg["web_search_health"] = {}
            self.cfg["web_search_health_v"] = HEALTH_VERSION
        self._health = dict(self.cfg.get("web_search_health", {}) or {})
        self._colors = SiteColors(self.sites.document(), self._health)
        # Rouge ne veut pas dire « jamais ». Les sites morts avec certitude
        # sont retires de la liste, et ceux qui ont demenage y prennent leur
        # nouvelle adresse -- d'eux-memes, a la fin de chaque recherche
        # (`_tidy_sites`). Les autres (lents, en panne, anti-robot) restent.
        self.retry_red = QPushButton("Retester les rouges", panel)
        self.retry_red.setToolTip("Les sites en rouge sont mis de côté un jour (panne "
                                  "passagère) ou trois (refus durable) au lieu d'être "
                                  "réessayés à chaque recherche.\nCe bouton les remet "
                                  "dans la prochaine recherche.")
        self.retry_red.clicked.connect(self._retry_red)
        self.retry_red.hide()
        reds = QHBoxLayout()
        reds.addStretch(1)
        reds.addWidget(self.retry_red)
        box.addLayout(reds)
        head = QLabel("Recherches — une par ligne", panel)
        head.setObjectName("head")
        explained = ("Chaque ligne est une recherche à part ; les mots d'une "
                     "même ligne sont cherchés ensemble.")
        head.setToolTip(explained)
        self.queries = QPlainTextEdit(panel)
        # Le micro : ce qu'on dit s'ajoute comme une recherche de plus.
        from . import voice

        def spoken(text: str) -> None:
            lines = [line for line in self.queries.toPlainText().splitlines() if line.strip()]
            if text not in lines:
                lines.append(text)
            self.queries.setPlainText("\n".join(lines))

        def tell(text: str) -> None:
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(self.queries.mapToGlobal(self.queries.rect().topLeft()),
                              text, self.queries)
        self.voice = voice.VoiceInput(self.queries, spoken, tell)
        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.addWidget(head, 1)
        heading.addWidget(self.voice.button(panel), 0)
        box.addLayout(heading)
        self.queries.setToolTip(explained)
        self.queries.setPlaceholderText("coucher de soleil\nplage 4k\nrandonnée")
        # Quatre lignes suffisent ; au-dela, la case defile. La place va aux
        # sites.
        self.queries.setFixedHeight(self.queries.fontMetrics().lineSpacing() * 4 + 12)
        box.addWidget(self.queries, 0)

        # Les reglages deux par deux : quatre rangees prenaient la hauteur
        # qui manquait a la liste des sites.
        form = QGridLayout()
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(4)

        def add_row(label: str, widget, row: int, column: int) -> None:
            text = QLabel(label, panel)
            text.setObjectName("dim")
            form.addWidget(text, row, column * 2)
            form.addWidget(widget, row, column * 2 + 1)
            form.setColumnStretch(column * 2 + 1, 1)
        self.min_duration = QSpinBox(panel)
        self.min_duration.setRange(0, 600)
        self.min_duration.setSuffix(" min")
        self.min_duration.setSpecialValueText("aucune")
        add_row("Durée min.", self.min_duration, 0, 0)
        self.min_resolution = QComboBox(panel)
        for label, _ in RESOLUTION_CHOICES:
            self.min_resolution.addItem(label)
        add_row("Qualité", self.min_resolution, 0, 1)
        # 0 = « Tout » : le site est parcouru jusqu'a sa derniere page de
        # resultats (47 videos sur tout pisshamster, c'etait la limite).
        self.per_site = QSpinBox(panel)
        self.per_site.setRange(0, 5000)
        self.per_site.setSingleStep(10)
        self.per_site.setSpecialValueText("Tout")
        self.per_site.setToolTip("Résultats par site : gardés sur chaque site, pour chaque "
                                 "recherche : tous les sites ont leur part.\n"
                                 "« Tout » (tout en bas) : sans limite.")
        add_row("Par site", self.per_site, 1, 0)
        self.max_pages = QSpinBox(panel)
        self.max_pages.setRange(0, 500)
        self.max_pages.setSpecialValueText("Toutes")
        self.max_pages.setToolTip("Pages par site : pages de résultats lues sur chaque site.\n"
                                  "« Toutes » (tout en bas) : jusqu'à la dernière.")
        add_row("Pages", self.max_pages, 1, 1)
        box.addLayout(form)
        self.title_match = QCheckBox("Strict : le titre contient tous les mots", panel)
        self.title_match.setToolTip(
            "Coché : ne garde que les vidéos dont le titre contient chacun des mots "
            "de la recherche (dans n'importe quel ordre, sans tenir compte des "
            "accents ni des majuscules).\nDécoché : le site décide de ce qui "
            "correspond.")
        box.addWidget(self.title_match)
        self.keep_unknown = QCheckBox("Garder les vidéos sans durée affichée", panel)
        box.addWidget(self.keep_unknown)

        row = QHBoxLayout()
        self.search_button = QPushButton("Rechercher", panel)
        self.search_button.setObjectName("primary")
        self.search_button.clicked.connect(self.start_search)
        self.stop_button = QPushButton("Arrêter", panel)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_search)
        row.addWidget(self.search_button, 1)
        row.addWidget(self.stop_button)
        box.addLayout(row)

        head = QLabel("Téléchargements", panel)
        head.setObjectName("head")
        box.addWidget(head)
        self.folder = QPushButton("", panel)
        self.folder.setToolTip("Le dossier où arrivent les vidéos téléchargées "
                               "(dans votre collection, pour que Prisme les voie).")
        self.folder.clicked.connect(self._choose_folder)
        box.addWidget(self.folder)
        row = QHBoxLayout()
        row.addWidget(QLabel("Cookies du navigateur", panel))
        self.browser = QComboBox(panel)
        for label, _ in BROWSERS:
            self.browser.addItem(label)
        self.browser.setToolTip(
            "Pour les sites à vérification d'âge ou à compte : la recherche et les "
            "téléchargements reprennent la connexion du navigateur choisi, où vous "
            "avez déjà passé la vérification. Firefox marche le mieux (Chrome et "
            "Edge chiffrent leurs cookies).")
        row.addWidget(self.browser, 1)
        box.addLayout(row)
        row = QHBoxLayout()
        self.link = QLineEdit(panel)
        self.link.setPlaceholderText("Coller le lien d'une vidéo…")
        self.link.setToolTip("Une vidéo trouvée dans le navigateur (sur un site qui "
                             "refuse les recherches automatiques, par exemple) : "
                             "collez son adresse, elle est téléchargée.")
        self.link.returnPressed.connect(self._paste_link)
        row.addWidget(self.link, 1)
        paste = QPushButton("Télécharger", panel)
        paste.clicked.connect(self._paste_link)
        row.addWidget(paste)
        box.addLayout(row)
        if not can_download():
            warn = QLabel("L'outil de téléchargement (yt-dlp) n'est pas installé : "
                          "lancez install.bat.", panel)
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #d8c05a;")
            box.addWidget(warn)
        return panel

    # -- a droite : les resultats -------------------------------------------
    def _results(self) -> QWidget:
        area = QWidget(self)
        box = QVBoxLayout(area)
        box.setContentsMargins(6, 0, 0, 0)
        bar = QHBoxLayout()
        self.counter = QLabel("Aucun résultat", area)
        self.counter.setObjectName("head")
        bar.addWidget(self.counter)
        # Ou en est-on ? Une petite barre qui defile tant que la recherche ou
        # la verification travaillent.
        self.busy = QProgressBar(area)
        self.busy.setRange(0, 0)
        self.busy.setFixedSize(90, 6)
        self.busy.setTextVisible(False)
        self.busy.hide()
        bar.addWidget(self.busy)
        bar.addStretch(1)
        self.panel_toggle = QPushButton("◀ Panneau", area)
        self.panel_toggle.setObjectName("small")
        self.panel_toggle.setToolTip("Masquer ou montrer le panneau des sites et réglages : "
                                     "toute la largeur pour les résultats")
        self.panel_toggle.clicked.connect(self._toggle_panel)
        bar.addWidget(self.panel_toggle)
        self.states_toggle = QPushButton("État des sites", area)
        self.states_toggle.setObjectName("small")
        self.states_toggle.setCheckable(True)
        self.states_toggle.setToolTip("Montrer ou fermer l'état des sites, sous les résultats")
        bar.addWidget(self.states_toggle)
        self.hide_unavailable = QCheckBox("Masquer les non téléchargeables", area)
        self.hide_unavailable.setToolTip("Chaque résultat est vérifié en fond (la vidéo "
                                         "entière existe-t-elle, en quelle qualité) : "
                                         "cochée, cette case cache ceux qui ne se "
                                         "téléchargent pas.")
        self.hide_unavailable.setChecked(bool(self.cfg.get("web_search_hide_unavailable", True)))
        self.hide_unavailable.toggled.connect(self._apply_view)
        bar.addWidget(self.hide_unavailable)
        self.filter = QLineEdit(area)
        self.filter.setPlaceholderText("Filtrer les résultats…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_view)
        self.filter.setFixedWidth(220)
        bar.addWidget(self.filter)
        self.sort = QComboBox(area)
        for label, _ in SORTS:
            self.sort.addItem(label)
        self.sort.currentIndexChanged.connect(self._apply_view)
        bar.addWidget(self.sort)
        # La taille des vignettes, comme dans le Labo IA : plus petites, on en
        # voit davantage par ecran.
        from PySide6.QtWidgets import QSlider
        bar.addWidget(QLabel("Taille", area))
        self.card_size = QSlider(Qt.Horizontal, area)
        self.card_size.setRange(150, 380)
        self.card_size.setFixedWidth(110)
        self.card_size.setToolTip("La taille des vignettes : plus petites, on en voit davantage")
        self.card_size.setValue(int(self.cfg.get("web_search_card_w", CARD_W) or CARD_W))
        set_card_width(self.card_size.value())
        self.card_size.valueChanged.connect(self._resize_cards)
        bar.addWidget(self.card_size)
        box.addLayout(bar)
        self.pick_bar = QWidget(area)
        self.pick_bar.setObjectName("pickBar")
        picks = QHBoxLayout(self.pick_bar)
        picks.setContentsMargins(10, 4, 6, 4)
        picks.setSpacing(8)
        self.pick_count = QLabel("", self.pick_bar)
        picks.addWidget(self.pick_count)
        picks.addStretch(1)
        self.pick_get = QPushButton("Télécharger la sélection", self.pick_bar)
        self.pick_get.setObjectName("get")
        self.pick_get.clicked.connect(self.download_picked)
        picks.addWidget(self.pick_get)
        pick_all = QPushButton("Tout cocher", self.pick_bar)
        pick_all.setObjectName("small")
        pick_all.setToolTip("Cocher toutes les vidéos affichées (selon les filtres et les sites)")
        pick_all.clicked.connect(lambda: self.pick_all(True))
        picks.addWidget(pick_all)
        pick_none = QPushButton("Aucune", self.pick_bar)
        pick_none.setObjectName("small")
        pick_none.clicked.connect(lambda: self.pick_all(False))
        picks.addWidget(pick_none)
        self.pick_bar.hide()
        box.addWidget(self.pick_bar)
        self.chips = SiteChips(area)
        self.chips.changed.connect(self._apply_view)
        box.addWidget(self.chips)

        split = QSplitter(Qt.Vertical, area)
        split.setHandleWidth(8)
        self.grid = QListWidget(area)
        self.grid.setObjectName("results")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setGridSize(QSize(CARD_W, CARD_H))
        self.grid.setSpacing(2)
        self.grid.setSelectionMode(QAbstractItemView.NoSelection)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.grid.verticalScrollBar().setSingleStep(24)
        split.addWidget(self.grid)

        states = QWidget(area)
        states_box = QVBoxLayout(states)
        states_box.setContentsMargins(0, 4, 0, 0)
        head = QLabel("État des sites", states)
        head.setObjectName("head")
        head_row = QHBoxLayout()
        head_row.addWidget(head, 1)
        close = QPushButton("✕", states)
        close.setObjectName("small")
        close.setToolTip("Fermer l'état des sites (le bouton « État des sites », en haut, "
                         "le rouvre). Tirer la poignée au-dessus l'agrandit ou le réduit.")
        close.clicked.connect(lambda: self.states_toggle.setChecked(False))
        head_row.addWidget(close)
        states_box.addLayout(head_row)
        self.site_tree = QTreeWidget(states)
        self.site_tree.setHeaderLabels(["Site", "", "Ce qui s'est passé"])
        self.site_tree.setRootIsDecorated(False)
        # Des lignes de meme hauteur : sans cela, chaque etat de site faisait
        # remesurer les 275 lignes de la liste.
        self.site_tree.setUniformRowHeights(True)
        self.site_tree.header().setSectionResizeMode(2, QHeaderView.Stretch)
        self.site_tree.itemDoubleClicked.connect(self._search_in_browser)
        states_box.addWidget(self.site_tree)
        split.addWidget(states)
        split.setStretchFactor(0, 4)
        split.setCollapsible(0, False)
        split.setSizes([700, 140])
        self.states = states
        opened = bool(self.cfg.get("web_search_states_open", False))
        states.setVisible(opened)
        self.states_toggle.setChecked(opened)
        self.states_toggle.toggled.connect(self._show_states)
        box.addWidget(split, 1)
        return area

    def _resize_cards(self, width: int) -> None:
        set_card_width(width)
        self.cfg["web_search_card_w"] = int(width)
        self.grid.setUpdatesEnabled(False)
        self.grid.setGridSize(QSize(CARD_W, CARD_H))
        for row in range(self.grid.count()):
            item = self.grid.item(row)
            item.setSizeHint(QSize(CARD_W, CARD_H))
            card = self.grid.itemWidget(item)
            if card is not None:
                card.fit()
        self.grid.setUpdatesEnabled(True)
        self.grid.doItemsLayout()

    def _show_states(self, on: bool) -> None:
        self.states.setVisible(on)
        self.cfg["web_search_states_open"] = on

    def _toggle_panel(self) -> None:
        shown = not self.panel.isVisible()
        self.panel.setVisible(shown)
        self.panel_toggle.setText("◀ Panneau" if shown else "▶ Panneau")

    # -- reglages persistes ----------------------------------------------
    def _load_settings(self) -> None:
        cfg = self.cfg
        sites = cfg.get("web_search_sites", "") or ""
        if not sites:
            old = cfg.get("web_search_known_domains", "") or ""
            sites = "\n".join(part for part in old.replace(",", " ").split() if part)
        self.sites.setPlainText(sites)
        self._show_health()
        self.queries.setPlainText(cfg.get("web_search_queries", "") or "")
        self.min_duration.setValue(int(cfg.get("web_search_min_duration_min", 0) or 0))
        # 0 veut dire « Tout » ; seule l'absence de reglage donne les valeurs d'usage.
        per_site = cfg.get("web_search_per_site", None)
        pages = cfg.get("web_search_max_pages", None)
        self.per_site.setValue(40 if per_site is None else int(per_site))
        self.max_pages.setValue(10 if pages is None else int(pages))
        self.keep_unknown.setChecked(bool(cfg.get("web_search_keep_unknown", True)))
        self.title_match.setChecked(bool(cfg.get("web_search_title_match", False)))
        want_height = int(cfg.get("web_search_min_height", 0) or 0)
        for index, (_, height) in enumerate(RESOLUTION_CHOICES):
            if height == want_height:
                self.min_resolution.setCurrentIndex(index)
        browser = cfg.get("web_download_browser", "") or ""
        for index, (_, name) in enumerate(BROWSERS):
            if name == browser:
                self.browser.setCurrentIndex(index)
        self._show_folder()

    def _show_health(self) -> None:
        """Les sites a leurs couleurs, et l'etat de chacun tel que la derniere
        recherche l'a laisse -- on sait, sans rien relancer, lesquels servent."""
        self._colors.rehighlight()
        self._list_timer.start()
        names = [site_name(line) for line in split_lines(self.sites.toPlainText())]
        red = [n for n in names if (self._health.get(n) or [""])[0] == "erreur"]
        self.retry_red.setVisible(bool(red))
        if self.site_tree.topLevelItemCount() == 0:
            for name in names:
                state, text = (self._health.get(name) or ["", ""])[:2]
                if state:
                    self._paint_site(name, state, text)

    def eventFilter(self, watched, event):
        if watched is self.site_list.viewport() and event.type() == QEvent.Resize:
            self._site_columns()
        return super().eventFilter(watched, event)

    def _site_columns(self) -> None:
        """Autant de colonnes que la largeur en laisse (150 px chacune au
        moins), en lignes serrees : on voit plus de sites d'un coup."""
        room = self.site_list.viewport().width() - 2
        width = max(80, room // max(1, room // 150))
        height = self.site_list.fontMetrics().height() + 4
        if self.site_list.gridSize() != QSize(width, height):
            self.site_list.setGridSize(QSize(width, height))

    def _fill_site_list(self) -> None:
        """La liste a cocher, refaite depuis le texte : memes sites, dans le
        meme ordre, avec leur couleur et leur case (retenue par site)."""
        self.site_list.blockSignals(True)
        self.site_list.clear()
        for line in split_lines(self.sites.toPlainText()):
            name = site_name(line)
            # Le nom court (« pandeb.com ») ; l'adresse entiere reste dans
            # l'onglet « Coller / modifier » et dans l'infobulle.
            item = QListWidgetItem(short_site(line), self.site_list)
            item.setStatusTip(line)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if name in self._unchecked else Qt.Checked)
            state, why = (self._health.get(name) or ["", ""])[:2]
            color = STATE_COLORS.get(state)
            if color:
                item.setForeground(QColor(color))
            if state == "erreur":
                font = item.font()
                font.setStrikeOut(True)
                item.setFont(font)
            if state == "erreur":
                why = f"{RED_KINDS[red_kind(why)]} — {why}"
            item.setToolTip(f"{line}\n{why}" if why else line)
            item.setData(Qt.UserRole, name)
        self.site_list.blockSignals(False)

    def _site_checked(self, item) -> None:
        name = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self._unchecked.discard(name)
        else:
            self._unchecked.add(name)
        self.cfg["web_search_unchecked"] = sorted(self._unchecked)
        self.cfg.save_soon() if hasattr(self.cfg, "save_soon") else self.cfg.save()

    def _check_all(self, on: bool) -> None:
        names = [site_name(line) for line in split_lines(self.sites.toPlainText())]
        self._unchecked = set() if on else set(names)
        self.cfg["web_search_unchecked"] = sorted(self._unchecked)
        self._fill_site_list()

    def _chosen_sites(self) -> list:
        """Les sites coches -- tous ceux du texte, moins ceux qu'on a decoches."""
        return [line for line in split_lines(self.sites.toPlainText())
                if site_name(line) not in self._unchecked]

    def _retry_red(self) -> None:
        """Les rouges mis de cote reviennent dans la prochaine recherche."""
        for name, entry in list(self._health.items()):
            if entry and entry[0] == "erreur" and len(entry) >= 3:
                self._health[name] = [entry[0], entry[1], 0]
        self.cfg["web_search_health"] = dict(self._health)
        self.counter.setText("Les sites en rouge seront réessayés à la prochaine recherche.")

    def _tidy_sites(self) -> None:
        """A la fin d'une recherche : les sites morts avec certitude quittent
        la liste, ceux qui ont demenage y prennent leur nouvelle adresse (et
        disparaissent si elle y est deja). Rien d'autre n'est touche."""
        lines = split_lines(self.sites.toPlainText())
        present = {site_name(line).removeprefix("www.") for line in lines}
        out, moved, gone, merged = [], [], [], []
        for line in lines:
            name = site_name(line)
            entry = self._health.get(name) or ["", ""]
            text = entry[1] if len(entry) > 1 else ""
            target = renamed_to(text) if "{q}" not in line else ""
            if entry[0] == "erreur" and red_kind(text) == "dead":
                gone.append(short_site(line))
                self._health.pop(name, None)
                continue
            if target and target.removeprefix("www.") != name.removeprefix("www."):
                self._health.pop(name, None)
                if target.removeprefix("www.") in present:
                    merged.append(f"{short_site(line)} (déjà là sous {target})")
                    continue
                present.add(target.removeprefix("www."))
                if entry[0] and entry[0] != "erreur":
                    # Sa nouvelle adresse garde la couleur de cette recherche.
                    self._health[target] = list(entry)
                out.append(target)
                moved.append(f"{short_site(line)} → {target}")
                continue
            out.append(line)
        if not (moved or gone or merged):
            return
        self.sites.blockSignals(True)
        self.sites.setPlainText("\n".join(out))
        self.sites.blockSignals(False)
        self.cfg["web_search_health"] = dict(self._health)
        self._save_settings()
        self._fill_site_list()
        self._show_health()
        told = []
        if moved:
            told.append(f"{len(moved)} site(s) déménagé(s) mis à leur nouvelle adresse")
        if merged:
            told.append(f"{len(merged)} retiré(s) car leur nouvelle adresse était déjà dans la liste")
        if gone:
            told.append(f"{len(gone)} site(s) mort(s) retiré(s) (nom de domaine disparu, "
                        "confirmé par deux annuaires publics)")
        head = QTreeWidgetItem(self.site_tree, ["Liste des sites", "↻", " ; ".join(told)])
        head.setToolTip(2, "\n".join(moved + merged + [f"{g} : mort" for g in gone]))
        self.site_tree.insertTopLevelItem(0, self.site_tree.takeTopLevelItem(
            self.site_tree.indexOfTopLevelItem(head)))

    def _save_settings(self) -> None:
        cfg = self.cfg
        cfg["web_search_sites"] = "\n".join(split_lines(self.sites.toPlainText()))
        cfg["web_search_queries"] = "\n".join(split_lines(self.queries.toPlainText()))
        cfg["web_search_min_duration_min"] = self.min_duration.value()
        cfg["web_search_per_site"] = self.per_site.value()
        cfg["web_search_max_pages"] = self.max_pages.value()
        cfg["web_search_keep_unknown"] = self.keep_unknown.isChecked()
        cfg["web_search_title_match"] = self.title_match.isChecked()
        cfg["web_search_hide_unavailable"] = self.hide_unavailable.isChecked()
        cfg["web_search_unchecked"] = sorted(self._unchecked)
        cfg["web_search_min_height"] = RESOLUTION_CHOICES[self.min_resolution.currentIndex()][1]
        cfg["web_download_browser"] = BROWSERS[self.browser.currentIndex()][1]
        cfg.save()

    def _show_folder(self) -> None:
        folder = self.cfg.get("web_download_dir", "") or ""
        self.folder.setText(("Dossier : " + folder) if folder
                            else "Choisir le dossier de téléchargement…")

    def _choose_folder(self) -> bool:
        start = self.cfg.get("web_download_dir", "") or ""
        folder = QFileDialog.getExistingDirectory(
            self, "Où ranger les vidéos téléchargées ?", start)
        if not folder:
            return False
        self.cfg["web_download_dir"] = os.path.normpath(folder)
        self.cfg.save()
        self._show_folder()
        return True

    # -- recherche ---------------------------------------------------------
    def start_search(self) -> None:
        sites = self._chosen_sites()
        queries = split_lines(self.queries.toPlainText())
        if not sites or not queries:
            self.counter.setText("Cochez au moins un site, et écrivez une recherche.")
            return
        self.stop_search(quiet=True)
        self._save_settings()
        self._generation += 1
        self.grid.clear()
        self._cards.clear()
        self._picked.clear()
        self._show_picked()
        self._rank = 0
        self.site_tree.clear()
        self._sites.clear()
        # Les sites en rouge depuis peu sont mis de cote (voir red_rest_left) ;
        # les autres sont retestes : ils perdent leur couleur d'avant et
        # prennent la nouvelle des que leur resultat arrive.
        now, resting, active = time.time(), [], []
        for line in sites:
            entry = self._health.get(site_name(line))
            left = red_rest_left(entry, now)
            (resting if left else active).append((line, entry, left))
        for line, _entry, _left in active:
            self._health.pop(site_name(line), None)
        self._show_health()
        self.chips.reset()
        for line, entry, left in resting:
            name = site_name(line)
            again = time.strftime("%d/%m à %H:%M", time.localtime(now + left))
            self._sites[name] = "erreur"
            self._paint_site(name, "erreur", f"mis de côté jusqu'au {again} "
                                             f"(« Retester les rouges » pour forcer) : {entry[1]}")
        sites = [line for line, _e, _l in active]
        if not sites:
            self.counter.setText("Tous les sites cochés sont en rouge, mis de côté pour "
                                 "l'instant : « Retester les rouges » pour les réessayer.")
            return
        self._searching = True
        self._site_total = len(sites) + len(resting)
        self._checked = self._good = 0
        if self._verifier is not None:
            self._verifier.stop()
        self._verifier = (_Verifier(self._engine(), self.downloader.ffprobe,
                                    BROWSERS[self.browser.currentIndex()][1])
                          if self.downloader.ffprobe else None)
        self._status()
        filters = SearchFilters(
            queries=queries, sites=sites,
            min_duration_s=self.min_duration.value() * 60,
            min_height=RESOLUTION_CHOICES[self.min_resolution.currentIndex()][1],
            # « Tout » : pas de plafond (la derniere page du site arrete tout).
            max_per_site=self.per_site.value() or 1_000_000,
            max_pages_per_site=self.max_pages.value() or 10_000,
            keep_unknown=self.keep_unknown.isChecked(),
            title_must_match=self.title_match.isChecked(),
        )
        templates = dict(self.cfg.get("web_search_templates", {}) or {})
        generation = self._generation
        worker = _SearchWorker(self._engine(), filters, templates, generation,
                               browser=BROWSERS[self.browser.currentIndex()][1],
                               renderer=self._page_reader())
        self._worker = worker
        worker.start()
        self._mail.start()
        self.search_button.setText("Relancer")
        self.stop_button.setEnabled(True)

    def stop_search(self, quiet: bool = False) -> None:
        """Arrete tout de suite : l'ecran ne montre plus rien de cette
        recherche-la, et l'on peut en relancer une aussitot. Le fil finit
        seul, en fond, sans plus rien toucher."""
        was_searching = self._worker is not None
        if self._worker is not None:
            self._worker.stop()
            # Les adresses de recherche qu'elle a apprises arrivent a son
            # arret : on les garde. Perdues, chaque arret faisait tout
            # rapprendre -- le plus long d'une premiere recherche.
            self._harvest.append(self._worker)
            self._generation += 1
        self._worker = None
        if not self._harvest:
            self._mail.stop()
        self._searching = False
        self.search_button.setText("Rechercher")
        if quiet:
            self._to_verify = []
            self.stop_button.setEnabled(False)
            return
        if was_searching and self._to_verify:
            # Arreter la recherche : ce qu'elle a deja trouve se verifie
            # quand meme. Appuyer de nouveau arrete la verification.
            self._verify_found()
            self._status("arrêtée")
            return
        if self._verifier is not None:
            self._verifier.stop()
        self.stop_button.setEnabled(False)
        self._status("arrêtée")

    def _read_mail(self) -> None:
        """Releve ce que la recherche en cours a trouve depuis la derniere fois."""
        self._collect_harvest()
        worker = self._worker
        if worker is None:
            if not self._harvest:
                self._mail.stop()
            return
        g = worker.generation
        # Tout le lot d'un coup, affichage suspendu : la grille et la liste des
        # sites ne se redessinent qu'une fois, a la fin du lot.
        self.grid.setUpdatesEnabled(False)
        self.site_tree.setUpdatesEnabled(False)
        # Au plus 40 ms par releve : une carte coute quelques millisecondes,
        # deux cents d'un coup figeaient la fenetre une seconde.
        budget = time.monotonic() + 0.04
        try:
            while time.monotonic() < budget:
                try:
                    kind, value = worker.mailbox.get_nowait()
                except queue.Empty:
                    return
                if kind == "result":
                    self._add_result(value, g)
                elif kind == "site":
                    self._site_state(*value, g)
                else:
                    self._on_finished(value, g)
                    return
        finally:
            self.grid.setUpdatesEnabled(True)
            self.site_tree.setUpdatesEnabled(True)

    def _collect_harvest(self) -> None:
        """Les recherches arretees : seules comptent les adresses de recherche
        qu'elles rendent en s'arretant."""
        for worker in list(self._harvest):
            while True:
                try:
                    kind, value = worker.mailbox.get_nowait()
                except queue.Empty:
                    break
                if kind == "finished":
                    self._harvest.remove(worker)
                    if value is not None:
                        # Comme a la fin d'une recherche : la liste a jour, sans
                        # les adresses qui se sont revelees fausses.
                        self.cfg["web_search_templates"] = dict(value)
                        self.cfg.save()
                    break

    def _on_finished(self, templates: dict, generation: int) -> None:
        if templates is not None:          # None : la recherche a echoue en route
            # Les adresses de recherche trouvees : la fois suivante ne les
            # cherche plus. La recherche part d'une copie de toutes celles
            # qu'on connait et rend cette copie a jour -- y compris sans
            # celles qui se sont revelees fausses. Les fusionner avec
            # l'ancienne liste remettait les fausses : erome restait rouge.
            self.cfg["web_search_templates"] = dict(templates)
        self.cfg.save()                 # l'etat des sites, pour la prochaine fois
        if generation != self._generation:
            return
        self._worker = None
        self._mail.stop()
        self._searching = False
        self.stop_button.setEnabled(self._verifier is not None and self._verifier.busy())
        self.search_button.setText("Rechercher")
        if self._health_dirty:
            self._health_dirty = False
            self._show_health()
        self._tidy_sites()
        self._verify_found()
        self._status()
        self.chips.sort_by_count()          # la recherche finie : les plus fournis d'abord

    def _site_state(self, name: str, state: str, text: str, generation: int) -> None:
        if generation != self._generation:
            return
        self._sites[name] = state
        if state in STATE_COLORS:
            self._health[name] = [state, text, time.time()]
            self.cfg["web_search_health"] = dict(self._health)
            self._health_dirty = True
        self._paint_site(name, state, text)
        self._soon()

    def _soon(self) -> None:
        """Le compteur, les puces et les couleurs, refaits d'ici peu (une fois
        pour tout ce qui sera arrive entre-temps)."""
        if not self._refresh.isActive():
            self._refresh.start()

    def _refresh_now(self) -> None:
        if getattr(self, "_columns_dirty", False):
            self._columns_dirty = False
            self.site_tree.resizeColumnToContents(0)
        if self._health_dirty:
            self._health_dirty = False
            self._show_health()
        self._status()

    def _verify_found(self) -> None:
        """La recherche finie (ou arretee) : on verifie ce qu'elle a trouve,
        les cartes a l'ecran d'abord."""
        pending, self._to_verify = self._to_verify, []
        if self._verifier is None or not pending:
            return
        for entry in pending:
            card = self._cards.get(entry[0])
            if card is None:
                continue
            try:
                card.pending()
            except RuntimeError:
                continue
            self._verifier.add(*entry)
        self._look.start()
        self.stop_button.setEnabled(True)

    def _paint_site(self, name: str, state: str, text: str) -> None:
        found = self.site_tree.findItems(name, Qt.MatchExactly, 0)
        row = found[0] if found else QTreeWidgetItem(self.site_tree, [name, "", ""])
        mark = {"ok": "✓", "vide": "∅", "erreur": "✗", "cherche": "…", "arrêté": "■"}[state] \
            if state in ("ok", "vide", "erreur", "cherche", "arrêté") else ""
        row.setText(1, mark)
        row.setText(2, text)
        row.setToolTip(2, text)
        color = {"ok": "#7bd88f", "erreur": "#e26d76", "vide": "#d8c05a"}.get(state, "#8b94a1")
        row.setForeground(1, QColor(color))
        if state in ("erreur", "vide"):
            row.setToolTip(0, "Double-clic : faire cette recherche dans votre navigateur")
        self._columns_dirty = True

    def _search_in_browser(self, row, _column: int = 0) -> None:
        """Un site qui refuse les recherches automatiques : on fait la
        recherche dans le navigateur, comme on la ferait a la main."""
        from urllib.parse import quote_plus
        name = row.text(0)
        queries = split_lines(self.queries.toPlainText())
        words = queries[0] if queries else ""
        template = (self.cfg.get("web_search_templates", {}) or {}).get(name, "")
        if not template:
            for line in split_lines(self.sites.toPlainText()):
                if name in line and "{q}" in line:
                    template = line if "://" in line else "https://" + line
        url = (template.replace("{q}", quote_plus(words)) if template
               else f"https://www.google.com/search?q={quote_plus(words)}+site%3A{name}")
        self.open_url(url)

    def _paste_link(self) -> None:
        """Telecharger une video dont on a l'adresse : trouvee dans le
        navigateur, sur un site qui refuse les recherches automatiques."""
        url = self.link.text().strip()
        if not web_address(url):
            self.link.setPlaceholderText("Collez l'adresse d'une vidéo (https://…)")
            self.link.clear()
            return
        from urllib.parse import urlparse
        video = VideoResult(title=url, page_url=url,
                            source_domain=urlparse(url).netloc, query="lien collé")
        self._add_result(video, self._generation)
        card = None
        for index in range(self.grid.count() - 1, -1, -1):
            item = self.grid.item(index)
            if item.video is video:
                card = self.grid.itemWidget(item)
                break
        self.link.clear()
        if card is not None:
            card.job = self.download(card)

    def _add_result(self, video: VideoResult, generation: int) -> None:
        if generation != self._generation:
            return
        self._rank += 1
        item = _Item(video, self._rank)
        card = Card(self, video)
        self.grid.addItem(item)
        self.grid.setItemWidget(item, card)
        self._cards[id(card)] = card
        self.chips.add(video.source_domain)
        self._apply_item(item)
        if self._verifier is not None and video.query != "lien collé":
            entry = (id(card), video.page_url, video.duration_s,
                     getattr(video, "found_on", ""))
            if self._searching:
                card.waiting()
                self._to_verify.append(entry)
            else:
                card.pending()
                self._verifier.add(*entry)
                self._look.start()
        self._soon()
        if video.thumbnail_url:
            self._fetch_thumbnail(card, video.thumbnail_url, video.page_url)
        self._session_changed()

    # -- la verification, en fond --------------------------------------------
    def _look_around(self) -> None:
        """Les cartes a l'ecran : a verifier en premier. Et les verifications
        arrivees depuis la derniere fois, reportees sur leurs cartes."""
        verifier = self._verifier
        if verifier is None:
            self._look.stop()
            return
        area = self.grid.viewport().rect()
        seen = []
        for index in range(self.grid.count()):
            item = self.grid.item(index)
            if not item.isHidden() and self.grid.visualItemRect(item).intersects(area):
                card = self.grid.itemWidget(item)
                if card is not None:
                    seen.append(id(card))
        verifier.prefer(seen)
        changed = False
        for _ in range(60):
            try:
                key, media, why = verifier.mailbox.get_nowait()
            except queue.Empty:
                break
            card = self._cards.get(key)
            if card is None:
                continue
            try:
                card.verified(media, why)
            except RuntimeError:
                continue
            self._checked += 1
            self._good += media is not None
            changed = True
        if changed:
            for index in range(self.grid.count()):
                self._apply_item(self.grid.item(index))
            self._status()
            self._session_changed()
        if not verifier.busy() and verifier.mailbox.empty():
            self._look.stop()
            if not self._searching:
                self.stop_button.setEnabled(False)
            self._status()

    def _status(self, ended: str = "") -> None:
        """Ou en est la recherche : les sites faits, les resultats, les
        verifications -- en cours, ou finie."""
        total = self.grid.count()
        shown = sum(1 for i in range(total) if not self.grid.item(i).isHidden())
        done = sum(1 for st in self._sites.values() if st != "cherche")
        verifier = self._verifier
        checking = verifier is not None and (verifier.busy() or not verifier.mailbox.empty())
        parts = [f"{shown} résultat(s)" + (f" affiché(s) sur {total}" if shown != total else "")]
        if self._searching:
            parts.append(f"recherche en cours — {done}/{self._site_total} site(s) terminé(s)")
        elif ended:
            parts.append(f"recherche {ended}")
        elif self._site_total:
            parts.append("recherche terminée")
        if self._to_verify:
            parts.append(f"{len(self._to_verify)} à vérifier une fois la recherche finie")
        elif verifier is not None and total:
            parts.append(f"vérifiées {self._checked}/{total}, {self._good} téléchargeable(s)"
                         + ("…" if checking else ""))
        self.counter.setText("  ·  ".join(parts) if total or self._searching else
                             ("Aucun résultat" + (f" — recherche {ended}" if ended else "")))
        self.busy.setVisible(self._searching or checking)
        self._count_sites()

    # -- filtre et tri de la grille ------------------------------------------
    def _filtered_out(self, v) -> bool:
        """Cache par un autre filtre que les puces des sites."""
        text = self.filter.text().strip().lower()
        wanted = RESOLUTION_CHOICES[self.min_resolution.currentIndex()][1]
        real = isinstance(v.media, Media)
        return bool((text and text not in v.title.lower())
                    or (self.hide_unavailable.isChecked() and getattr(v, "unavailable", ""))
                    # La qualite demandee, jugee sur le vrai fichier une fois verifie.
                    or (real and wanted and (v.height or 0) < wanted))

    def _apply_item(self, item) -> None:
        sites = self.chips.selected()
        v = item.video
        item.setHidden(self._filtered_out(v) or bool(sites and v.source_domain not in sites))

    def _count_sites(self) -> None:
        """Le nombre de chaque puce : ce que l'on verrait en la choisissant."""
        counts: dict = {}
        for index in range(self.grid.count()):
            v = self.grid.item(index).video
            if not self._filtered_out(v):
                counts[v.source_domain] = counts.get(v.source_domain, 0) + 1
        self.chips.set_counts(counts, sum(counts.values()))

    def _apply_view(self) -> None:
        _Item.order = SORTS[self.sort.currentIndex()][1]
        self.grid.sortItems(Qt.AscendingOrder)
        for index in range(self.grid.count()):
            self._apply_item(self.grid.item(index))
        self._status()

    # -- vignettes ---------------------------------------------------------
    def _fetch_thumbnail(self, card: Card, url: str, page: str) -> None:
        address = web_address(url)
        if address is None:
            return
        request = QNetworkRequest(address)
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                             QNetworkRequest.NoLessSafeRedirectPolicy)
        # Beaucoup de sites ne servent leurs vignettes qu'a leurs propres pages.
        request.setRawHeader(b"User-Agent", USER_AGENT.encode())
        request.setRawHeader(b"Referer", page.encode("utf-8", "ignore"))
        reply = self._net.get(request)
        self._thumbs[reply] = card
        reply.downloadProgress.connect(
            lambda got, _total: reply.abort() if got > MAX_THUMB_BYTES else None)
        reply.finished.connect(lambda: self._on_thumbnail(reply))

    def _on_thumbnail(self, reply) -> None:
        card = self._thumbs.pop(reply, None)
        reply.deleteLater()
        if card is None or reply.error() != QNetworkReply.NoError:
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(bytes(reply.readAll())):
            try:
                card.set_thumbnail(pixmap)
            except RuntimeError:
                pass                     # la carte a disparu (nouvelle recherche)

    # -- telechargements ---------------------------------------------------
    def download(self, card: Card) -> int:
        self.downloader.renderer = self._page_reader()
        if not can_download():
            card.done("", "l'outil yt-dlp n'est pas installé (lancez install.bat)")
            return 0
        folder = self.cfg.get("web_download_dir", "") or ""
        if not folder and not self._choose_folder():
            return 0
        folder = self.cfg.get("web_download_dir", "")
        self._save_settings()
        return self._queue_download({
            "page_url": card.video.page_url, "title": card.video.title, "folder": folder,
            "expect": card.video.duration_s, "origin": getattr(card.video, "found_on", ""),
            "media": websession.media_to_dict(card.video.media)}, card)

    def _queue_download(self, info: dict, card=None) -> int:
        """Met une video en file et note son telechargement (pour le reprendre
        si Prisme se ferme en route)."""
        info = dict(info, state="running", path="", error="")
        job = self.downloader.add(info["page_url"], info["folder"],
                                  BROWSERS[self.browser.currentIndex()][1],
                                  expect_s=info.get("expect"), origin=info.get("origin", ""),
                                  media=websession.media_from_dict(info.get("media")))
        self._downloads[job] = info
        if card is not None:
            self._jobs[job] = card
            card.job = job
            card.started()
        self._session_changed()
        return job

    # -- la recherche gardee sur le disque ------------------------------------------
    def _session_changed(self) -> None:
        if not getattr(self, "_restoring", False) and not self._session_timer.isActive():
            self._session_timer.start()

    def _save_session(self) -> None:
        """Note la recherche : ses resultats (dans l'ordre affiche), ce qu'on en
        sait, et chaque telechargement avec son etat."""
        results = []
        for index in range(self.grid.count()):
            card = self.grid.itemWidget(self.grid.item(index))
            if card is not None:
                try:
                    results.append(websession.video_to_dict(card.video))
                except RuntimeError:
                    pass
        try:
            websession.save(SESSION_PATH, {
                "queries": self.queries.toPlainText(), "results": results,
                "downloads": self._past_downloads + list(self._downloads.values())})
        except OSError:
            pass

    def _restore_session(self) -> None:
        """A l'ouverture : les resultats de la derniere recherche reviennent,
        et les telechargements interrompus repartent -- la ou ils en etaient
        (le fichier commence est complete, pas recommence)."""
        data = websession.load(SESSION_PATH)
        rows, downloads = data.get("results") or [], data.get("downloads") or []
        if not rows and not downloads:
            return
        self._restoring = True
        verifier, self._verifier = self._verifier, None     # rien ne se verifie en posant
        by_url, unknown = {}, []
        try:
            for row in rows:
                video = websession.video_from_dict(row)
                if not video.page_url:
                    continue
                self._add_result(video, self._generation)
                item = self.grid.item(self.grid.count() - 1)
                card = self.grid.itemWidget(item) if item is not None else None
                if card is None:
                    continue
                by_url[video.page_url] = card
                if isinstance(video.media, Media):
                    card.verified(video.media, "")
                elif getattr(video, "unavailable", ""):
                    card.verified(None, video.unavailable)
                else:
                    unknown.append((id(card), video.page_url, video.duration_s,
                                    getattr(video, "found_on", "")))
            resumed = 0
            for info in downloads:
                if not isinstance(info, dict) or not info.get("page_url"):
                    continue
                card = by_url.get(info["page_url"])
                state = info.get("state")
                if state == "done":
                    if card is not None and info.get("path") and Path(info["path"]).exists():
                        card.done(info["path"], "")
                    self._past_downloads.append(info)
                elif state == "failed":
                    if card is not None:
                        card.done("", info.get("error") or "échec")
                    self._past_downloads.append(info)
                elif info.get("folder"):
                    self._queue_download(info, card)
                    resumed += 1
        finally:
            self._verifier = verifier
            self._restoring = False
        if data.get("queries") and not self.queries.toPlainText().strip():
            self.queries.setPlainText(data["queries"])
        self.chips.sort_by_count()
        # Ce qui n'avait pas encore ete verifie l'est maintenant.
        if unknown and self.downloader.ffprobe:
            self._verifier = _Verifier(self._engine(), self.downloader.ffprobe,
                                       BROWSERS[self.browser.currentIndex()][1])
            self._to_verify = unknown
            self._verify_found()
        self._status("reprise" if rows else "")
        told = f"Recherche précédente reprise : {len(by_url)} résultat(s)"
        if resumed:
            told += f", {resumed} téléchargement(s) repris là où ils en étaient"
        self.counter.setText(told + ".")
        self.counter.setToolTip(told)

    # -- la selection ------------------------------------------------------------
    def picked_changed(self, card, on: bool) -> None:
        was = bool(self._picked)
        if on:
            self._picked[id(card)] = card
        else:
            self._picked.pop(id(card), None)
        if was != bool(self._picked):
            # La premiere coche montre toutes les autres ; la derniere les cache.
            for other in list(self._cards.values()):
                try:
                    other.show_pick(bool(self._picked))
                except RuntimeError:
                    pass
        self._show_picked()

    def _show_picked(self) -> None:
        count = len(self._picked)
        self.pick_bar.setVisible(count > 0)
        self.pick_count.setText(f"{count} vidéo{'s' if count > 1 else ''} cochée{'s' if count > 1 else ''}")
        self.pick_get.setText(f"Télécharger la sélection ({count})")

    def pick_all(self, on: bool) -> None:
        """Coche (ou decoche) toutes les cartes affichees."""
        for index in range(self.grid.count()):
            item = self.grid.item(index)
            if on and item.isHidden():
                continue
            card = self.grid.itemWidget(item)
            if card is not None and card.pick.isChecked() != on:
                card.pick.setChecked(on)

    def download_picked(self) -> None:
        """Chaque video cochee part en telechargement ; les coches s'en vont."""
        # Le dossier, une fois pour tout le lot (et rien ne part si l'on renonce).
        if not (self.cfg.get("web_download_dir", "") or "") and not self._choose_folder():
            return
        for card in list(self._picked.values()):
            try:
                if not card.job and not card.path:
                    card._get()
                card.pick.setChecked(False)
            except RuntimeError:
                self._picked.pop(id(card), None)
        self._show_picked()

    def activity(self) -> dict:
        """Ou en sont la recherche, la verification et les telechargements :
        ce que la barre de Prisme resume quand cette fenetre est reduite."""
        verifier = self._verifier
        total = self.grid.count()
        lines = []
        for job, card in list(self._jobs.items()):
            try:
                title = card.video.title
            except RuntimeError:
                continue
            said = self._job_text.get(job, "en attente")
            lines.append(f"{title[:60]} — {said}")
        for job, info in list(self._downloads.items()):
            if job not in self._jobs and info.get("state") == "running":
                said = self._job_text.get(job, "en attente")
                lines.append(f"{(info.get('title') or info['page_url'])[:60]} — {said}")
        return {
            "searching": self._searching,
            "sites_done": sum(1 for st in self._sites.values() if st != "cherche"),
            "sites_total": self._site_total,
            "results": total,
            "verifying": verifier is not None and verifier.busy() and not self._searching,
            "checked": self._checked,
            "good": self._good,
            "downloads": self.downloader.summary(),
            "lines": lines,
        }

    def _download_progress(self, job: int, fraction: float, text: str) -> None:
        self._job_text[job] = text
        card = self._jobs.get(job)
        if card is not None:
            try:
                card.advanced(fraction, text)
            except RuntimeError:
                pass

    def _download_done(self, job: int, path: str, error: str) -> None:
        self._job_text.pop(job, None)
        info = self._downloads.get(job)
        if info is not None:
            if error == "annulé":
                self._downloads.pop(job, None)
            else:
                info.update(state="done" if path else "failed", path=path or "",
                            error=error or "")
            self._session_changed()
        card = self._jobs.pop(job, None)
        if card is not None:
            try:
                card.done(path, error)
            except RuntimeError:
                pass

    # -- ouvrir ---------------------------------------------------------------
    def open_url(self, url: str) -> None:
        address = web_address(url)
        if address is not None:
            QDesktopServices.openUrl(address)

    def reveal(self, path: str) -> None:
        import subprocess
        if path and Path(path).exists():
            subprocess.Popen(f'explorer /select,"{path}"')

    def _engine(self) -> Engine:
        """Le processus de recherche, demarre au premier besoin. Il demande
        le navigateur invisible a la fenetre quand il en a besoin."""
        engine = getattr(self, "_engine_process", None)
        if engine is None:
            reader = self._page_reader()          # cree ici, sur le fil de la fenetre

            def render(method: str, args):
                if reader is None:
                    return None
                return getattr(reader, method)(*args)
            engine = self._engine_process = Engine(render)
        return engine

    def _page_reader(self):
        """Le navigateur invisible, cree au premier besoin (sur ce fil-ci)."""
        reader = getattr(self, "_reader", None)
        if reader is None:
            try:
                from .webpage import PageReader
                reader = PageReader(USER_AGENT, self)
            except Exception:                           # noqa: BLE001
                reader = None       # sans moteur de navigateur : on s'en passe
            self._reader = reader
        return reader

    def closeEvent(self, event) -> None:
        self._save_session()
        if not self._quitting and self.downloader.summary()["active"]:
            # Des telechargements tournent : la fenetre se cache, eux
            # continuent (la barre de Prisme les montre ; un clic la ramene).
            event.ignore()
            self.hide()
            return
        if self._verifier is not None:
            self._verifier.stop()
        reader = getattr(self, "_reader", None)
        if reader is not None:
            reader.close()
        if self._worker is not None:
            self._worker.stop()
        engine = getattr(self, "_engine_process", None)
        if engine is not None:
            engine.close()
        self._save_settings()
        super().closeEvent(event)


# L'ancien nom, pour ce qui l'appelait encore.
WebSearchDialog = WebSearchWindow
