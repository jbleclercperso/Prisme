"""Composants d'interface : grille d'aperçus, lecteur, barre de commandes, réglages."""
from __future__ import annotations

import os
import time
from pathlib import Path

from collections import OrderedDict

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, QRectF, QRunnable, QSize,
    QThread, QThreadPool, QTimer, QUrl, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QGuiApplication, QIcon, QImage, QImageReader, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF, QRegion,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLayout,
    QLineEdit, QListView, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSizePolicy,
    QTreeView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .actions import ActionError
from .floatguard import FloatGuard, GuardedLabel, GuardedWidget
from .icons import GLYPHS, dress, filled, icon
from .config import KEY_ORDER, RESERVED_KEYS, is_photo
from .scan import human_duration, human_resolution

GRID_COLUMNS = 5

STYLESHEET = """
QWidget { background: #14161a; color: #e6e8ea; font-size: 13px; }
QLabel { border: none; background: transparent; }
QLabel#title { font-size: 19px; font-weight: 600; color: #ffffff; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px 0; }
QScrollBar::handle:vertical { background: #2b323d; border-radius: 4px;
                              min-height: 36px; }
QScrollBar::handle:vertical:hover { background: #3d4654; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0 2px; }
QScrollBar::handle:horizontal { background: #2b323d; border-radius: 4px;
                                min-width: 36px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QPushButton#paneGesture { background: #1a1f27; border: 1px solid #2b323d;
                          border-radius: 5px; padding: 0; margin: 0;
                          color: #cdd5df; font-size: 17px; }
QPushButton#paneGesture:hover { color: #ffffff; border-color: #5a6474;
                                background: #242a33; }
QPushButton#paneGesture:checked { background: #1d2a40; border-color: #4c8dff; }
/* Une entree de menu cochable qui porte une icone : Qt ne montre son etat
   qu'a travers l'icone, et rien ne distinguait « Rafale » ou « Passer a la
   suivante apres ★ » en marche ou non. Le cadre des gestes coches. */
QMenu::icon:checked { background: #1d2a40; border: 1px solid #4c8dff;
                      border-radius: 3px; }
/* Le menu ⌂ : une case par racine, qu'on coche sans que le menu se ferme. */
QCheckBox#rootTick { background: transparent; padding: 7px 22px 7px 12px;
                     font-size: 13px; }
QCheckBox#rootTick:hover { background: #1d2a40; }
QCheckBox#rootTick:disabled { color: #6f7885; }
QLabel#subtitle { font-size: 15px; color: #b6c0cc; }
QLabel#counter { font-size: 13px; color: #9aa4b0; }
QLabel#rootPath { font-size: 13px; color: #9aa4b0; }
QLabel#pending { font-size: 12px; color: #8fb4ff; background: #1b2434;
                 border-radius: 5px; padding: 3px 9px; }
QLabel#parentPath { font-size: 15px; color: #a9b4c2; font-weight: 600; }
QLabel#hint { color: #6f7885; }
QFrame#card { background: #1b1f26; border: 1px solid #262c35; border-radius: 10px; }
QFrame#boardCard { background: #171b21; border: 1px solid #262c35; border-radius: 10px; }
QFrame#boardCard[hovered="true"] { border-color: #4c8dff; background: #1c222b; }
/* Ce qui est decide garde une trace, au lieu de disparaitre : on voit son
   avancement sur la page, et l'on peut revenir sur une decision d'un coup
   d'oeil. Un bord seul se remarquait a peine sur une planche dense. */
QFrame#boardCard[state="rangé"] { border-color: #3f6b39;
                                  background: rgba(63, 107, 57, 0.16); }
QFrame#boardCard[state="écarté"] { border-color: #6d2f38;
                                   background: rgba(109, 47, 56, 0.16); }
QFrame#boardCard[state="passé"] { border-color: #4a4f5c;
                                  background: rgba(74, 79, 92, 0.14); }
QFrame#boardCard[state="rangé"] QLabel#boardMeta { color: #8fc386; }
QFrame#boardCard[state="écarté"] QLabel#boardMeta { color: #d08a95; }
QLabel#boardImage { background: #0b0d10; border-radius: 6px; color: #59616d; }
QLabel#boardName { font-size: 14px; font-weight: 600; color: #e6e8ea; }
/* Les deux portees du hasard : la collection entiere, ou l element affiche.
   Elles se distinguent a la couleur, pour qu on sache ce qu on declenche. */
/* L'etat de l'analyse, en toutes lettres : au repos on peut la lancer,
   en marche elle compte, et un clic l'arrete. */
QPushButton#enter { background: #1d4a2e; border: 1px solid #2f7a4a;
                    border-radius: 6px; padding: 5px 12px; color: #cdf0da;
                    font-weight: 600; }
QPushButton#enter:hover { background: #2a6a41; color: #ffffff; }
/* Le repli : un rond presque eteint. Il ne doit rien annoncer a qui
   regarde par-dessus l'epaule, et se trouver sans reflechir. */
QPushButton#quietSwitch { background: transparent; border: 0; color: #2c333d;
                          font-size: 15px; padding: 0; }
QPushButton#quietSwitch:hover { color: #8b94a1; }
QPushButton#up { background: transparent; border: 1px solid #39414d;
                 border-radius: 6px; padding: 5px 0; color: #9aa4b2;
                 font-size: 15px; }
QPushButton#up:hover { color: #ffffff; border-color: #5a6575; }
QPushButton#up:disabled { color: #3e454f; border-color: #262c35; }
QPushButton#scanState { background: transparent; border: 1px solid #39414d;
                        border-radius: 6px; padding: 5px 11px; color: #9aa4b2; }
QPushButton#scanState:hover { color: #ffffff; border-color: #5a6575; }
QPushButton#scanState[running="true"] { background: #1d3a5c; border-color: #2f6fed;
                                        color: #cfe0ff; font-weight: 600; }
QPushButton#random { background: #8c3b52; border: 0; border-radius: 6px;
                     padding: 6px 12px; color: #ffe9ef; font-weight: 600; }
QPushButton#random:hover { background: #a7455f; }
QPushButton#randomHere { background: transparent; border: 1px solid #8c3b52;
                         border-radius: 6px; padding: 5px 10px; color: #e3a3b4; }
QPushButton#randomHere:hover { background: rgba(140, 59, 82, 0.28);
                               color: #ffffff; }
QLabel#boardMeta { font-size: 12px; color: #8b95a3; }
QLabel#subpath { font-size: 12px; color: #7d8796; }
QScrollArea#boardScroll { background: transparent; border: 0; }
QWidget#videoArea { background: #000000; }
QWidget#focusPlayer { background: #07080a; }
QLabel#focusTitle { font-size: 18px; font-weight: 600; color: #ffffff; }
QPushButton#focusMute { background: rgba(0,0,0,0.7); border: 1px solid #39414d;
                        border-radius: 6px; font-size: 16px; }
QFrame#tile { background: #0e1013; border: 1px solid #262c35; border-radius: 8px; }
QFrame#tile[hovered="true"] { border: 1px solid #4c8dff; }
QLabel#tileBadge { background: rgba(0,0,0,0.65); color: #dfe4ea; border-radius: 4px;
                   padding: 1px 5px; font-size: 11px; }
QPushButton#cardDiscard { background: rgba(8, 10, 13, 0.75);
                          border: 1px solid #4a3136; border-radius: 12px;
                          color: #e08b96; font-size: 13px; }
QPushButton#cardDiscard:hover { background: #7a2b34; border-color: #7a2b34;
                                color: #ffffff; }
QCheckBox#cardPick { background: transparent; border: 0; padding: 0; }
QCheckBox#cardPick::indicator { width: 18px; height: 18px;
                                border: 1px solid #6b7684; border-radius: 4px;
                                background: rgba(8, 10, 13, 0.75); }
QCheckBox#cardPick::indicator:checked { background: #2f6fed; border-color: #2f6fed; }
QWidget#asideBar { background: rgba(8, 10, 13, 0.72); }
QLabel#tileDuration { background: rgba(0,0,0,0.78); color: #ffffff;
                     border-radius: 5px; padding: 2px 8px;
                     font-size: 13px; font-weight: 700; }
QLabel#tileCaption { color: #9aa6b4; font-size: 11px; font-weight: 600; }
QLabel#tilePlaceholder { color: #59616d; font-size: 12px; }
QFrame#singleRail { background: #222932; border-radius: 4px; }
QFrame#singleDone { background: #4d8dff; border-radius: 4px; }
QFrame#playRail { background: rgba(255,255,255,0.22); border: 0;
                  border-radius: 4px; }
QFrame#playProgress { background: #5c9dff; border: 0; border-radius: 4px; }
/* Lisible, jamais criard : le temps restant se pose sur l'image sans la
   disputer. Il etait en gras quatorze sur pave noir — on ne voyait que lui. */
QLabel#remaining { background: transparent; color: rgba(255,255,255,0.72);
                   padding: 2px 6px; font-size: 11px; font-weight: 500; }
QPushButton { background: #232932; border: 1px solid #323a45; border-radius: 6px;
              padding: 6px 12px; color: #e6e8ea; }
QPushButton:hover { background: #2c333e; }
QPushButton:pressed { background: #384150; }
QPushButton#primary { background: #2f6fed; border-color: #2f6fed; color: white;
                      font-weight: 600; padding: 9px 18px; }
QPushButton#primary:hover { background: #4280f5; }
QPushButton#danger { background: #3a2226; border-color: #6d2f38; }
QPushButton#danger:hover { background: #4d2a30; }
QFrame#keycap { background: #0e1013; border: 1px solid #39414d; border-radius: 6px; }
QFrame#keycap:hover { background: #1c2430; border-color: #4c8dff; }
QLabel#keyLetter { font-weight: 700; color: #ffd479; font-size: 13px; }
QLabel#keyLabel { color: #c3cad3; font-size: 12px; }
QLabel#statusBanner { border-radius: 6px; padding: 6px 10px; font-weight: 600; }
QTreeWidget#destTree { background: #0e1013; border: 1px solid #262c35;
                       border-radius: 6px; selection-background-color: #2f6fed; }
QTreeWidget#destTree::item { padding: 4px 2px; }
QHeaderView::section { background: #1b1f26; border: 0; padding: 6px; color: #9aa4b0; }
QLineEdit { background: #0e1013; border: 1px solid #323a45; border-radius: 6px;
            padding: 5px 8px; }
QLineEdit:focus { border-color: #4c8dff; }
QLineEdit#excludeEdit:focus { border-color: #d4707c; }
QComboBox { background: #232932; border: 1px solid #323a45;
            border-radius: 6px; padding: 5px 8px; }
QComboBox QAbstractItemView { background: #1b1f26; border: 1px solid #323a45;
                              selection-background-color: #2f6fed; }
QProgressBar { background: #1b1f26; border: 0; border-radius: 2px; }
QProgressBar::chunk { background: #2f6fed; border-radius: 2px; }
"""


def elide(text: str, width: int = 34) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _within(path, target) -> bool:
    """Vrai si `path` est `target` ou se trouve dessous. Sans toucher au disque."""
    try:
        Path(path).relative_to(Path(target))
    except ValueError:
        return False
    return True


def seek_step(event, seconds: int) -> int:
    """Convertit un cran de molette en déplacement, en millisecondes : un
    cran vers le haut avance, partout (fiche, mur, lecteurs).

    Ctrl amplifie le pas, la ou Ctrl+molette ne zoome pas.
    """
    notches = event.angleDelta().y() / 120.0
    if not notches:
        notches = event.angleDelta().x() / 120.0
    factor = 6 if event.modifiers() & Qt.ControlModifier else 1
    return int(notches * seconds * 1000 * factor)


class VideoWake:
    """Fait reapparaitre un lecteur video qu'on vient de deplacer.

    Sous Windows, le widget video deplace d'une case a l'autre continue de
    lire, mais l'ecran n'est recompose a sa nouvelle place que si un widget
    voisin passe au-dessus de lui. L'ancienne etiquette de temps restant le
    faisait sans le savoir ; quand elle est partie, seul le premier apercu
    s'affichait, les autres jouaient dans le vide. Ce point invisible, releve
    au-dessus du lecteur a chaque placement, tient ce role — expres, cette
    fois.
    """

    def __init__(self, canvas):
        self.dot = QLabel("", canvas)
        self.dot.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.dot.setStyleSheet("background: transparent;")
        self.dot.setFixedSize(1, 1)
        self.dot.hide()

    def over(self, geometry) -> None:
        self.dot.move(geometry.topLeft())
        self.dot.raise_()
        self.dot.show()


class Expiring(dict):
    """Un ensemble dont les membres s'oublient d'eux-memes.

    Une video qui refusait de se lire etait ecartee pour toute la session :
    il suffisait que le partage reseau decroche une seconde pour que plus
    aucun apercu ne demarre, jusqu'au redemarrage. On la retente apres un
    moment.
    """

    TTL = 45.0

    def add(self, key) -> None:
        self[key] = time.monotonic()

    def __contains__(self, key) -> bool:
        stamp = self.get(key)
        if stamp is None:
            return False
        if time.monotonic() - stamp > self.TTL:
            self.pop(key, None)
            return False
        return True


class Stepper(QWidget):
    """Un reglage chiffre : moins, la valeur, plus. Le meme partout —
    vignettes par rangee, apercus d'un dossier, videos du mur."""

    chosen = Signal(int)

    def __init__(self, choices, tip: str = "", caption: str = "", parent=None):
        super().__init__(parent)
        self.choices = list(choices)
        self.value = self.choices[0]
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        if caption:
            label = QLabel(caption, self)
            label.setObjectName("hint")
            row.addWidget(label)
        self.minus = QPushButton("", self)
        self.plus = QPushButton("", self)
        self.value_label = QLabel("", self)
        self.value_label.setObjectName("counter")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setMinimumWidth(24)
        self.value_label.setToolTip(tip)
        for button, name, step in ((self.minus, "minus", -1), (self.plus, "plus", 1)):
            button.setObjectName("stepper")
            button.setFixedSize(30, 28)
            button.setFocusPolicy(Qt.NoFocus)
            button.setCursor(Qt.PointingHandCursor)
            dress(button, name, 18)
            button.clicked.connect(lambda _c=False, d=step: self._step(d))
        self.minus.setToolTip(tip + " : moins" if tip else "Moins")
        self.plus.setToolTip(tip + " : plus" if tip else "Plus")
        row.addWidget(self.minus)
        row.addWidget(self.value_label)
        row.addWidget(self.plus)
        self.set_value(self.value)

    def set_value(self, value: int) -> None:
        if value not in self.choices:
            value = min(self.choices, key=lambda c: abs(c - value))
        self.value = value
        self.value_label.setText(str(value))
        at = self.choices.index(value)
        self.minus.setEnabled(at > 0)
        self.plus.setEnabled(at < len(self.choices) - 1)

    def _step(self, step: int) -> None:
        at = self.choices.index(self.value) + step
        if 0 <= at < len(self.choices):
            self.set_value(self.choices[at])
            self.chosen.emit(self.value)


class PreviewTile(QFrame):
    """Une case d'aperçu : vignette + numéro + nom du fichier source."""

    def __init__(self, slot: int, parent=None):
        super().__init__(parent)
        self.setObjectName("tile")
        self.setProperty("hovered", "false")
        self.slot = slot
        self.video: str = ""
        self.ts: float = 0.0
        # Cinq cases par rangee : un minimum genereux ici devient mille
        # pixels exiges par la fenetre entiere, qui ne peut alors plus
        # retrecir. Les cases s'etirent de toute facon pour occuper la
        # place disponible.
        # Deux rangees de cases a quatre-vingt-quatre points imposaient
        # leur hauteur a la fenetre entiere. Elles gardent leur taille
        # des qu'il y a la place — c'est la mise en page qui la leur
        # donne — mais ne l'exigent plus.
        self.setMinimumSize(58, 40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.image = QLabel(self)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setScaledContents(False)

        self.placeholder = QLabel("…", self)
        self.placeholder.setObjectName("tilePlaceholder")
        self.placeholder.setAlignment(Qt.AlignCenter)

        # Le numero de case ne disait rien : on ne choisit pas « la 7 ». Il
        # ne sert plus qu'a la pellicule, ou il porte l'instant de l'image.
        self.badge = QLabel("", self)
        self.badge.setObjectName("tileBadge")
        self.badge.hide()

        # La durée est l'information qu'on cherche le plus vite : elle occupe un
        # coin à elle, en gros, plutôt que d'être noyée dans la légende.
        self.duration_chip = QLabel("", self)
        self.duration_chip.setObjectName("tileDuration")
        self.duration_chip.hide()

        self.caption = QLabel("", self)
        self.caption.setObjectName("tileCaption")

        self.duration = 0.0
        self.height_px = 0
        self.strip_mode = False
        self._pixmap: QPixmap | None = None

    # -- contenu ---------------------------------------------------------
    def reset(self) -> None:
        self.video = ""
        self.ts = 0.0
        self.duration = 0.0
        self.height_px = 0
        if self.strip_mode:
            self.badge.setText("")
        self._pixmap = None
        self.image.clear()
        self.placeholder.setText("…")
        self.placeholder.show()
        self.caption.setText("")
        self.duration_chip.hide()
        self.set_hovered(False)

    def set_source(self, video: str, ts: float, duration: float = 0.0,
                   height: int = 0) -> None:
        self.video = video
        self.ts = ts
        self.duration = duration
        self.height_px = height
        name = Path(video).name if video else ""
        if self.strip_mode:
            # Pellicule d'une seule vidéo : l'instant capté est la seule chose
            # utile, le numéro de case et le nom du fichier n'apprennent rien.
            self.badge.setText(human_duration(ts))
            self.badge.adjustSize()
            self.badge.show()
            self.caption.setText("")
        else:
            resolution = human_resolution(height)
            legend = f"{resolution}  ·  {name}" if resolution else name
            self.caption.setText(elide(legend, 40))
        self.caption.setToolTip(name)
        self.show_duration()

    def show_duration(self) -> None:
        """Rétablit la durée totale dans la pastille."""
        # Sur la pellicule, chaque case est un instant de la meme video : sa
        # duree, en gros et en gras sur les cinq images, ecrasait l'instant,
        # seule chose qui les distingue. La regle est tenue ici, et non par un
        # hide() a la construction, que le premier plan recu defaisait.
        if self.duration and not self.strip_mode:
            self.duration_chip.setText(human_duration(self.duration))
            self.duration_chip.show()
            self._place_chip()
        else:
            self.duration_chip.hide()

    def show_position(self, seconds: float) -> None:
        """Pendant un déplacement à la molette, la pastille situe la lecture."""
        if not self.duration:
            return
        self.duration_chip.setText(
            f"{human_duration(seconds)} / {human_duration(self.duration)}"
        )
        self.duration_chip.show()
        self._place_chip()

    def set_thumb(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.set_failed()
            return
        self._pixmap = pixmap
        self.placeholder.hide()
        self._rescale()

    def set_failed(self, text: str = "aperçu indisponible") -> None:
        self._pixmap = None
        self.image.clear()
        self.placeholder.setText(text)
        self.placeholder.show()

    def set_empty(self, text: str) -> None:
        self.reset()
        self.placeholder.setText(text)

    def set_hovered(self, hovered: bool) -> None:
        self.setProperty("hovered", "true" if hovered else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    # -- disposition -----------------------------------------------------
    def _rescale(self) -> None:
        if self._pixmap is None:
            return
        area = self.rect().adjusted(1, 1, -1, -19)
        self.image.setPixmap(
            self._pixmap.scaled(area.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _place_chip(self) -> None:
        self.duration_chip.adjustSize()
        self.duration_chip.move(self.width() - self.duration_chip.width() - 7, 7)
        self.duration_chip.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.rect()
        self.image.setGeometry(1, 1, rect.width() - 2, rect.height() - 20)
        self.placeholder.setGeometry(1, 1, rect.width() - 2, rect.height() - 20)
        self.badge.adjustSize()
        self.badge.move(7, 7)
        self._place_chip()
        self.caption.setGeometry(7, rect.height() - 18, rect.width() - 14, 15)
        self._rescale()


class PreviewGrid(QWidget):
    """Dix aperçus. Le survol d'une case y lance la lecture de l'extrait."""

    openRequested = Signal(str)
    playRequested = Signal(str, float)

    def __init__(self, count: int = 10, preview_seconds: int = 10,
                 scroll_seconds: int = 5, parent=None):
        super().__init__(parent)
        self.count = count
        self.preview_seconds = preview_seconds
        self.scroll_seconds = scroll_seconds
        self.item_id = ""
        self.hovered_slot = -1
        self.unplayable = Expiring()

        self.grid_layout = QGridLayout(self)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(6)
        self.tiles: list = []
        for slot in range(max(count, 10)):
            tile = PreviewTile(slot, self)
            self.grid_layout.addWidget(tile, slot // GRID_COLUMNS, slot % GRID_COLUMNS)
            self.tiles.append(tile)
        self.visible_count = count

        # Le trait d'avancement et le temps restant de l'extrait survole,
        # poses sur son image. Enfants de la grille, ils passaient derriere
        # la video et l'on ne voyait jamais le compte a rebours.
        self.marks = PlayMarks(self)

        # Le lecteur flotte au-dessus de la case survolée.
        self.video = QVideoWidget(self)
        self.video.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.video.hide()
        self.wake = VideoWake(self)
        # Aucune sortie audio : un aperçu survolé se regarde, il ne s'écoute
        # pas. Sans sortie, Qt ne décode pas la piste son du tout — c'est
        # autant de travail et de bande passante réseau en moins par vignette.
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        # « Charge » ne veut pas dire « affiche » : montrer le widget des la fin
        # du chargement devoilait la derniere image du media precedent, le temps
        # que la nouvelle soit rendue. On attend cette image-la.
        self._awaiting_frame = False
        self._blackout = False
        self.blackout_timer = QTimer(self)
        self.blackout_timer.setSingleShot(True)
        self.blackout_timer.timeout.connect(self._end_blackout)
        self.video.videoSink().videoFrameChanged.connect(self._on_frame)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.positionChanged.connect(self._on_position)
        self.player.errorOccurred.connect(self._on_error)
        self._pending_seek = 0
        self._segment_start = 0

        # Le survol est sondé plutôt qu'écouté : le widget vidéo natif ne
        # transmet pas toujours les événements de souris aux cases en dessous.
        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(70)
        self.hover_timer.timeout.connect(self._poll_hover)
        self.setMouseTracking(True)

        # Un autre fichier ne s'ouvre qu'une fois la souris posee sur sa case.
        # Changer de source fige l'interface le temps de demonter la
        # precedente (60 a 700 ms selon la machine), et chaque case traversee
        # ouvrait en plus son fichier sur le partage : balayer la grille
        # saccadait a chaque case. La case s'allume tout de suite ; seule la
        # lecture attend. Un autre instant du meme fichier, lui, ne coute
        # qu'un saut et part aussitot.
        self.settle_timer = QTimer(self)
        self.settle_timer.setSingleShot(True)
        self.settle_timer.setInterval(self.SETTLE_MS)
        self.settle_timer.timeout.connect(self._settled)

    SETTLE_MS = 120

    # -- cycle de vie ----------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self.hover_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.hover_timer.stop()
        self.stop()

    def set_muted(self, muted: bool) -> None:
        """Sans piste son décodée, il n'y a rien à couper : conservé pour l'appel."""

    def set_item(self, item_id: str, message: str = "…") -> None:
        self.stop()
        self.item_id = item_id
        for tile in self.tiles:
            tile.reset()
            tile.placeholder.setText(message)

    def set_visible_count(self, count: int) -> None:
        """N'affiche que `count` cases, réparties sur le moins de rangées possible.

        Un dossier de trois vidéos montre trois grandes cases, pas trois images
        perdues au milieu de sept cadres vides.
        """
        count = max(0, min(count, len(self.tiles)))
        self.visible_count = count
        columns = min(GRID_COLUMNS, max(1, count))
        for index, tile in enumerate(self.tiles):
            self.grid_layout.removeWidget(tile)
            if index < count:
                self.grid_layout.addWidget(tile, index // columns, index % columns)
                tile.show()
            else:
                tile.hide()

    def set_plan(self, plan: list) -> None:
        self.set_visible_count(len(plan))
        for slot, tile in enumerate(self.tiles):
            if slot < len(plan):
                tile.set_source(*plan[slot])
            else:
                tile.set_empty("")

    def set_info(self, slot: int, duration: float, height: int) -> None:
        """La durée et la résolution rejoignent une case déjà affichée."""
        if not 0 <= slot < len(self.tiles):
            return
        tile = self.tiles[slot]
        tile.set_source(tile.video, tile.ts, duration or tile.duration,
                        height or tile.height_px)

    def set_thumb(self, slot: int, path: str) -> None:
        if 0 <= slot < len(self.tiles):
            self.tiles[slot].set_thumb(path)

    def set_failed(self, slot: int) -> None:
        if 0 <= slot < len(self.tiles):
            self.tiles[slot].set_failed()

    def set_no_videos(self, text: str = "aucune vidéo") -> None:
        self.stop()
        for tile in self.tiles:
            tile.set_empty(text)

    # -- survol et lecture ----------------------------------------------
    def _slot_at(self, pos: QPoint) -> int:
        if not self.rect().contains(pos):
            return -1
        for slot, tile in enumerate(self.tiles):
            if tile.geometry().contains(pos):
                return slot
        return -1

    def _poll_hover(self) -> None:
        if not self.isVisible() or not self.window().isActiveWindow():
            if self.hovered_slot != -1:
                self._leave()
            return
        slot = self._slot_at(self.mapFromGlobal(QCursor.pos()))
        if slot == self.hovered_slot:
            return
        if self.hovered_slot != -1:
            self.tiles[self.hovered_slot].set_hovered(False)
            self.tiles[self.hovered_slot].show_duration()
        # Masquer avant tout, et effacer la surface : sinon l'image de la case
        # quittee reste affichee par-dessus la nouvelle, le temps que celle-ci
        # se charge.
        self._blank()
        self.marks.clear()
        self.hovered_slot = slot
        if slot == -1:
            self._leave()
            return
        tile = self.tiles[slot]
        tile.set_hovered(True)
        if tile.video and self.player.source() != QUrl.fromLocalFile(tile.video):
            # L'extrait quitte ne se lit plus pour personne : il n'a pas a
            # continuer de tirer sur le partage pendant l'attente.
            self.player.pause()
            self.settle_timer.start()
        else:
            self.settle_timer.stop()
            self._play_slot(slot)

    def _settled(self) -> None:
        """La souris est restee sur la case : on ouvre son fichier."""
        if 0 <= self.hovered_slot < len(self.tiles):
            self._play_slot(self.hovered_slot)

    def _leave(self) -> None:
        self.hovered_slot = -1
        self.stop()

    def _blank(self) -> None:
        """Cache l'apercu et efface ce qu'il restait de l'image precedente."""
        self._awaiting_frame = False
        self.video.hide()
        try:
            self.video.videoSink().setVideoFrame(QVideoFrame())
        except (RuntimeError, TypeError):
            pass

    def _end_blackout(self) -> None:
        """Fin du noir de transition : l'image peut reparaitre."""
        self._blackout = False
        if not self._awaiting_frame and self.hovered_slot != -1:
            self.video.show()

    def _on_frame(self, frame) -> None:
        """Premiere image du nouvel extrait : c'est maintenant qu'on l'affiche."""
        if not self._awaiting_frame or self.hovered_slot == -1:
            return
        try:
            valid = frame.isValid()
        except (RuntimeError, AttributeError):
            valid = True
        if not valid:
            return
        self._awaiting_frame = False
        self.video.show()
        self.wake.over(self.video.geometry())

    def _play_slot(self, slot: int) -> None:
        tile = self.tiles[slot]
        if not tile.video:
            self._blank()
            return
        if is_photo(tile.video):
            # Une photo n'a rien a lire : sa vignette est deja l'image.
            self._blank()
            return
        if tile.video in self.unplayable:
            self._blank()
            return

        self.video.setGeometry(tile.geometry().adjusted(1, 1, -1, -19))
        self.video.raise_()

        self._segment_start = int(tile.ts * 1000)
        self._pending_seek = self._segment_start
        url = QUrl.fromLocalFile(tile.video)
        # Meme fichier ou non, le widget garde la derniere image rendue : celle
        # d'un autre instant du meme extrait trompe autant que celle d'un autre
        # fichier. Dans les deux cas on attend la nouvelle, la vignette prenant
        # le relais jusque-la.
        self._awaiting_frame = True
        if self.player.source() == url:
            self.player.setPosition(self._segment_start)
        else:
            self.player.setSource(url)
        self.player.play()

    def _on_status(self, status) -> None:
        loaded = (QMediaPlayer.MediaStatus.LoadedMedia,
                  QMediaPlayer.MediaStatus.BufferedMedia)
        if status in loaded:
            if self._pending_seek:
                self.player.setPosition(self._pending_seek)
                self._pending_seek = 0
            # L'affichage revient a _on_frame : ici, rien n'est encore rendu.
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(self._segment_start)
            self.player.play()

    def _on_position(self, position: int) -> None:
        if self.hovered_slot == -1:
            return
        span = self.preview_seconds * 1000
        if position > self._segment_start + span:
            self.player.setPosition(self._segment_start)
            position = self._segment_start
        self._draw_progress(max(0.0, min(1.0, (position - self._segment_start) / span)))

    def _draw_progress(self, fraction: float) -> None:
        if self.hovered_slot == -1 or self.video.isHidden():
            self.marks.clear()
            return
        self.marks.set_progress(self.player.position(), self.player.duration())
        # Le trait suit l'extrait, le temps restant la video entiere.
        if int(fraction * 1000) != int(self.marks.fraction * 1000):
            self.marks.fraction = fraction
            self.marks.update()
        self.marks.place_on(self, self.video.geometry())
        self.wake.over(self.video.geometry())

    def _on_error(self, *_args) -> None:
        if 0 <= self.hovered_slot < len(self.tiles):
            video = self.tiles[self.hovered_slot].video
            if video:
                self.unplayable.add(video)
        self._blank()
        self.player.stop()

    def wheelEvent(self, event):
        """La molette avance ou recule dans l'extrait survolé."""
        if self.hovered_slot == -1 or not self.player.source().isValid() \
                or is_photo(self.tiles[self.hovered_slot].video or ""):
            return super().wheelEvent(event)
        step = seek_step(event, self.scroll_seconds)
        position = max(0, self.player.position() + step)
        duration = self.player.duration()
        if duration > 0:
            position = min(position, max(0, duration - 500))
        # La fenêtre de lecture suit le déplacement, sinon la boucle des dix
        # secondes ramènerait aussitôt à l'endroit qu'on vient de quitter.
        self._segment_start = position
        self.player.setPosition(position)
        self.tiles[self.hovered_slot].show_position(position / 1000.0)
        event.accept()

    def stop(self) -> None:
        self.settle_timer.stop()
        self.player.stop()
        self._blank()
        self.marks.clear()
        for tile in self.tiles:
            tile.set_hovered(False)

    def mousePressEvent(self, event):
        # Seul un appui recu ici arme le relachement qui ouvre une case.
        self._armed = event.button() == Qt.LeftButton
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """Un clic sur une case ouvre cette vidéo, comme partout ailleurs.

        Il fallait un double-clic, alors qu'une carte de la planche s'ouvre d'un
        seul : le même geste donnait deux résultats selon l'endroit.

        Seulement si l'appui a eu lieu ici : un double-clic sur une carte de
        dossier ouvrait la fiche au premier clic, et le second, tombe sur la
        grille, entrait aussitot dans une video du dossier.
        """
        armed, self._armed = getattr(self, "_armed", False), False
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        slot = self._slot_at(event.position().toPoint())
        if armed and slot >= 0 and self.tiles[slot].video:
            self.playRequested.emit(self.tiles[slot].video, self.tiles[slot].ts)
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Le premier clic a deja ouvert : ne pas rouvrir par-dessus.
        self._armed = False
        event.accept()


PEEK_STYLE = """
QWidget#peekOverlay { background: rgba(8, 10, 13, 235); }
QFrame#peekCell { background: #0e1116; border: 1px solid #242a33; border-radius: 6px; }
QLabel#peekImage { color: #6f7885; }
QLabel#peekCaption { color: #e9eef4; font-size: 12px; font-weight: 600; }
"""


_BRAND_CACHE: dict = {}


def brand_image(name: str) -> QImage:
    """Le logo, en image : « p » (le P prismatique) ou « lettrage » (PRISME)."""
    image = _BRAND_CACHE.get(name)
    if image is None:
        from .brand_data import LETTRAGE_PNG, P_MARK_PNG
        image = QImage.fromData(P_MARK_PNG if name == "p" else LETTRAGE_PNG, "PNG")
        _BRAND_CACHE[name] = image
    return image


def brand_pixmap(name: str, height: int, ratio: float = 1.0) -> QPixmap:
    """Le logo a cette hauteur (en points), net sur un ecran a haute densite."""
    image = brand_image(name).scaledToHeight(max(1, round(height * ratio)),
                                             Qt.SmoothTransformation)
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def paint_app_icon(painter: QPainter, size: int) -> None:
    """Le P prismatique, lumineux, sur un carre sombre aux coins arrondis.
    Sert a la fenetre, au fichier prisme.ico (`construire.py`) et a l'icone
    de l'ecran d'accueil des telephones."""
    from PySide6.QtGui import QLinearGradient, QPainterPath
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    radius = size * 0.22
    square = QRectF(0, 0, size, size)
    shape = QPainterPath()
    shape.addRoundedRect(square, radius, radius)
    ground = QLinearGradient(0, 0, 0, size)
    ground.setColorAt(0, QColor("#171a22"))
    ground.setColorAt(1, QColor("#050608"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(ground)
    painter.drawPath(shape)
    # Le P, centre, avec sa lueur -- qui reste dans le carre.
    painter.save()
    painter.setClipPath(shape)
    mark = brand_image("p")
    side = size * (0.80 if size >= 48 else 0.92)     # petit : le P prend toute la place
    scaled = mark.scaled(round(side), round(side), Qt.KeepAspectRatio,
                         Qt.SmoothTransformation)
    painter.drawImage(QPointF((size - scaled.width()) / 2, (size - scaled.height()) / 2),
                      scaled)
    painter.restore()
    if size >= 32:
        # Un liseré a peine visible : le carre se detache d'un fond sombre.
        painter.setPen(QPen(QColor(255, 255, 255, 28), max(1.0, size / 128)))
        painter.setBrush(Qt.NoBrush)
        inset = max(0.5, size / 256)
        painter.drawRoundedRect(square.adjusted(inset, inset, -inset, -inset), radius, radius)


def app_icon_pngs(sizes=(180, 192, 512)) -> dict:
    """L'icone en PNG, pour l'application posee sur l'ecran d'accueil d'un
    telephone. A faire sur le fil de l'interface (polices, peinture)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    found = {}
    for px in sizes:
        image = QImage(px, px, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        paint_app_icon(painter, px)
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        found[px] = bytes(data)
    return found


def app_icon(size: int = 256) -> QIcon:
    """L'icone de Prisme, dessinee a chaque taille que Windows demande.

    Dessinee plutot que chargee : pas de fichier a livrer, et l'icone reste
    nette de la barre des taches au bureau.
    """
    result = QIcon()
    for px in sorted({16, 24, 32, 48, 64, 128, int(size)}):
        pixmap = QPixmap(px, px)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        paint_app_icon(painter, px)
        painter.end()
        result.addPixmap(pixmap)
    return result


def draw_icon(kind: str, on: bool = True, size: int = 20,
              color: str = "#d5dbe3") -> QIcon:
    """Icones dessinees plutot que des emojis : nettes, sobres, et lisibles.

    « speaker » : un haut-parleur, barre au milieu quand le son est coupe.
    « tree » : un petit schema d'arborescence. « up » : un chevron large.
    """
    if kind == "speaker":
        return icon("volume-2") if on else icon("volume-x", "#e26d76")
    if kind in ("tree", "up"):
        return icon({"tree": "folder-tree", "up": "arrow-up"}[kind])
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    painter.setPen(pen)
    s = size / 20.0
    if kind == "speaker":
        body = QPainterPath()
        body.moveTo(3 * s, 8 * s)
        body.lineTo(6.5 * s, 8 * s)
        body.lineTo(11 * s, 4 * s)
        body.lineTo(11 * s, 16 * s)
        body.lineTo(6.5 * s, 12 * s)
        body.lineTo(3 * s, 12 * s)
        body.closeSubpath()
        painter.fillPath(body, QColor(color))
        if on:
            painter.drawArc(int(9 * s), int(6 * s), int(8 * s), int(8 * s), -50 * 16, 100 * 16)
            painter.drawArc(int(9 * s), int(3 * s), int(13 * s), int(14 * s), -45 * 16, 90 * 16)
        else:
            painter.setPen(QPen(QColor("#e26d76"), 2.6, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(int(14 * s), int(7 * s), int(14 * s), int(13 * s))
    elif kind == "tree":
        painter.drawLine(int(5 * s), int(3 * s), int(5 * s), int(15 * s))
        for y in (6, 10.5, 15):
            painter.drawLine(int(5 * s), int(y * s), int(9 * s), int(y * s))
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(int(9 * s), int((y - 2) * s), int(7 * s), int(4 * s), 1, 1)
    elif kind == "up":
        pen.setWidthF(2.8)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(3 * s, 13 * s), QPointF(10 * s, 6 * s),
                                        QPointF(17 * s, 13 * s)]))
    painter.end()
    return QIcon(pixmap)


class RadialMenu(QWidget):
    """Les destinations en rond autour du pointeur, le temps d'un clic.

    Comme dans un jeu : clic droit, les choix apparaissent autour de la
    souris, on en clique un, ils disparaissent. La lecture continue et
    l'image reste visible — seules de petites pastilles se posent dessus.
    C'est une fenetre-outil sans cadre : la seule chose qui passe devant le
    lecteur natif. Entre les pastilles, elle est percee : les clics et la
    molette vont a la video.
    """

    chosen = Signal(int)
    closed = Signal()

    RADIUS = 98        # distance des pastilles au pointeur
    PILL_H = 26
    MAX = 9

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.entries: list = []
        self.rects: list = []
        self.hot = -1
        self.watch = QTimer(self)
        self.watch.setInterval(50)
        self.watch.timeout.connect(self._watch)
        self.hide()

    # -- ouverture ----------------------------------------------------------
    def open_at(self, center: QPoint, entries: list) -> None:
        """`entries` : [(touche, libelle)], neuf au plus, dans l'ordre."""
        self.entries = list(entries)[:self.MAX]
        if not self.entries:
            return
        side = 2 * (self.RADIUS + 90)
        self.setGeometry(center.x() - side // 2, center.y() - side // 2, side, side)
        self._layout()
        self.hot = -1
        self.show()
        self.raise_()
        self.watch.start()

    def close_menu(self) -> None:
        if self.isHidden():
            return
        self.watch.stop()
        self.hide()
        self.closed.emit()

    def _layout(self) -> None:
        import math
        metrics = self.fontMetrics()
        count = len(self.entries)
        middle = QPointF(self.width() / 2, self.height() / 2)
        self.rects = []
        region = QRegion()
        for index, (key, label) in enumerate(self.entries):
            angle = -math.pi / 2 + index * 2 * math.pi / count
            cx = middle.x() + self.RADIUS * math.cos(angle)
            cy = middle.y() + self.RADIUS * math.sin(angle)
            text = self._text(key, label)
            width = metrics.horizontalAdvance(text) + 24
            rect = QRect(int(cx - width / 2), int(cy - self.PILL_H / 2), width, self.PILL_H)
            self.rects.append(rect)
            region = region.united(QRegion(rect.adjusted(-2, -2, 2, 2)))
        self.setMask(region)

    @staticmethod
    def _text(key: str, label: str) -> str:
        return f"{key.upper()}  {elide(label, 16)}"

    # -- dessin --------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        for index, rect in enumerate(self.rects):
            hot = index == self.hot
            painter.setPen(QPen(QColor("#5a6474" if hot else "#39414d"), 1))
            painter.setBrush(QColor(43, 50, 61, 245) if hot else QColor(22, 26, 32, 235))
            painter.drawRoundedRect(rect, 13, 13)
            key, label = self.entries[index]
            painter.setPen(QColor("#ffffff" if hot else "#e9eef4"))
            painter.drawText(rect, Qt.AlignCenter, self._text(key, label))
        painter.end()

    # -- souris ------------------------------------------------------------------
    def _hit(self, local: QPoint) -> int:
        for index, rect in enumerate(self.rects):
            if rect.contains(local):
                return index
        return -1

    def _watch(self) -> None:
        """Suit le pointeur : surbrillance, et fermeture s'il s'eloigne."""
        local = self.mapFromGlobal(QCursor.pos())
        middle = QPoint(self.width() // 2, self.height() // 2)
        away = (local - middle).manhattanLength()
        if away > self.RADIUS + 120:
            self.close_menu()
            return
        hot = self._hit(local)
        if hot != self.hot:
            self.hot = hot
            self.update()

    def mouseMoveEvent(self, event):
        hot = self._hit(event.position().toPoint())
        if hot != self.hot:
            self.hot = hot
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            hit = self._hit(event.position().toPoint())
            if hit >= 0:
                self.chosen.emit(hit)
            self.close_menu()
        elif event.button() == Qt.RightButton:
            self.close_menu()


OVER_STYLE = """
QWidget#overBar { background: rgba(8, 10, 13, 216); border-radius: 0; }
QLabel#overName { color: #e9eef4; font-size: 12px; }
QLabel#overLeft { color: rgba(255,255,255,0.78); font-size: 12px;
                  font-weight: 500; }
QFrame#overRail { background: rgba(255,255,255,0.20); border-radius: 2px; }
QFrame#overDone { background: #e9eef4; border-radius: 2px; }
QPushButton#overGesture { background: transparent; border: 0; padding: 0;
                          color: #dbe2ea; font-size: 20px; }
QPushButton#overGesture:hover { color: #ffffff; }
QPushButton#overStay { background: transparent; border: 0; padding: 0; }
QPushButton#overStay:hover { background: rgba(255,255,255,0.08); border-radius: 5px; }
QLabel#overSpeed { color: rgba(255,255,255,0.78); font-size: 12px; font-weight: 600;
                   padding: 0 6px; border-radius: 5px; }
QLabel#overSpeed:hover { background: rgba(255,255,255,0.10); color: #ffffff; }
"""

# « Rester dans ce dossier » : un dossier verrouille, gris quand il dort, dore
# quand il vaut. La case pleine de bleu pesait plus lourd que tous les gestes.
STAY_ON = "#f5c542"


class StayCheck(QPushButton):
    """« Rester dans ce dossier », qu'on allume ou eteint d'un clic (API d'une
    case : `isChecked`, `setChecked`, `toggled`). Un dossier verrouille, dore
    quand il vaut : une coche grise, a cote de l'etoile, se lisait « vu »."""

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setObjectName("overStay")
        self.setCheckable(True)
        self.setFixedSize(28, 28)
        self.setIconSize(QSize(18, 18))
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.toggled.connect(self._dress)
        self._dress(False)

    def setChecked(self, on: bool) -> None:          # noqa: N802  (API Qt)
        # Cochee d'ailleurs, signaux bloques (l'autre bandeau, le menu) :
        # `toggled` ne part pas, et le dessin restait celui d'avant.
        super().setChecked(on)
        self._dress(bool(on))

    def _dress(self, on: bool) -> None:
        # Comme l'etoile : au repos, le trait blanc des autres gestes ;
        # allume, plein et colore. Grise au repos, on la croyait desactivee.
        self.setIcon(filled("folder-lock", STAY_ON, "#14161a") if on
                     else icon("folder-lock"))


SIMILAR_ON = "#b99cff"
ULTRA_ON = "#ff9b3d"


class UltraCheck(StayCheck):
    """L'ultra tri : clic gauche, on garde ; clic droit, on supprime."""

    def _dress(self, on: bool) -> None:
        self.setIcon(filled("zap", ULTRA_ON) if on else icon("zap"))


class SimilarCheck(StayCheck):
    """« Que des vidéos qui ressemblent à celle-ci » : allumee, ▸ et Espace
    ne proposent plus que ses voisines (similar.py)."""

    def _dress(self, on: bool) -> None:
        self.setIcon(filled("sparkles", SIMILAR_ON) if on else icon("sparkles"))


class SpeedDial(QLabel):
    """La duree du diaporama, dans le bandeau : « 6 s ». La molette dessus
    l'allonge ou la raccourcit d'une seconde a chaque cran."""

    turned = Signal(int)          # +1 ou -1 seconde

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setObjectName("overSpeed")
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip("Durée de chaque photo au diaporama.\n"
                        "Molette dessus : une seconde de plus ou de moins.")
        self._rest = 0

    def set_seconds(self, seconds: int) -> None:
        # Sans signe : ceux de la police de Windows n'etaient que des
        # poussieres a douze points (voir icons.py).
        self.setText(f"{int(seconds)} s")

    def wheelEvent(self, event):
        # Un cran de molette vaut 120 ; un pave tactile en envoie des
        # fractions, qu'on cumule pour ne pas sauter de dix secondes.
        self._rest += event.angleDelta().y()
        while abs(self._rest) >= 120:
            step = 1 if self._rest > 0 else -1
            self._rest -= 120 * step
            self.turned.emit(step)
        event.accept()


class PlayMarks(FloatGuard, QWidget):
    """Ou en est la lecture, pose **sur** l'image : un trait tres fin tout en
    bas, et, si on le demande, le temps restant en haut a droite.

    Deux toutes petites fenetres opaques — le trait, la pastille — et non plus
    une grande fenetre transparente posee sur toute l'image : celle-ci, selon
    la carte graphique, pouvait masquer la video, et chaque avancement
    redessinait une surface de la taille de l'image, par panneau.
    """

    RAIL = 3
    TRACK = QColor("#2a2f38")
    DONE = QColor("#e9eef4")

    def __init__(self, parent=None, rail: bool = True, left: bool = True):
        flags = (Qt.Tool | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                 | Qt.WindowTransparentForInput)
        super().__init__(parent, flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.with_rail = rail
        self.with_left = left
        self.fraction = 0.0
        self._target = None
        self._time = ""
        # En favori : une petite etoile doree dans la pastille, au repos. Le
        # bandeau survole a la sienne ; sans lui, on ne le voyait plus.
        self.favorite = False
        # Cache par le lecteur (souris sur l'image) : seul `place_on` le
        # fait revenir. Le temps restant changeait chaque seconde et le
        # remontrait au-dessus du bandeau, jusqu'au controle suivant 120 ms
        # plus tard -- la barre fantome qui clignotait sous la barre de lecture.
        self._held = True
        self.left = GuardedLabel("", parent, flags)
        self.left.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.left.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.left.setStyleSheet(
            "QLabel { background: #0b0d10; color: #ffffff; padding: 1px 7px;"
            " font-size: 12px; font-weight: 600; }")
        self.left.hide()
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.TRACK)
        done = int(self.width() * self.fraction)
        if done > 0:
            painter.fillRect(0, 0, done, self.height(), self.DONE)
        painter.end()

    def setVisible(self, visible: bool) -> None:
        super().setVisible(visible)
        self._held = not visible
        if not visible:
            self.left.hide()

    def set_progress(self, position: int, duration: int) -> None:
        """Position et duree en millisecondes."""
        fraction = (max(0.0, min(1.0, position / duration))
                    if duration > 0 else 0.0)
        text = (f"−{human_duration(max(0, duration - position) / 1000.0)}"
                if duration > 0 else "")
        changed_text = text != self._time
        self._time = text
        if int(fraction * 1000) != int(self.fraction * 1000):
            self.fraction = fraction
            self.update()
        if changed_text:
            self._lay_out()

    def set_favorite(self, on: bool) -> None:
        on = bool(on)
        if on != self.favorite:
            self.favorite = on
            self._lay_out()

    def _chip_text(self) -> str:
        star = '<span style="color:#f5c542">★</span>'
        time_text = self._time if self.with_left else ""
        if self.favorite:
            return f"{star}&nbsp;{time_text}" if time_text else star
        return time_text

    def clear(self) -> None:
        self.fraction = 0.0
        self._time = ""
        self.left.setText("")
        self._target = None
        self.hide()

    def place_on(self, widget, rect=None) -> None:
        """Se pose sur `rect` (coordonnees de `widget`, tout le widget par
        defaut). Rien si le widget n'est pas a l'ecran."""
        if widget is None or not widget.isVisible():
            return self.hide()
        rect = widget.rect() if rect is None else rect
        corner = widget.mapToGlobal(rect.topLeft())
        self._target = QRect(corner.x(), corner.y(), rect.width(), rect.height())
        self._held = False
        self._lay_out()

    def _lay_out(self) -> None:
        target = self._target
        if target is None or self._held:
            return
        if self.with_rail:
            wanted = QRect(target.x(), target.bottom() - self.RAIL + 1,
                           target.width(), self.RAIL)
            if self.geometry() != wanted:
                self.setGeometry(wanted)
            if self.isHidden():
                # Remonter une fenetre a chaque battement occupait le
                # gestionnaire de fenetres pour rien : seulement en apparaissant.
                self.show()
                self.raise_()
        else:
            super().setVisible(False)
        chip = self.left
        text = self._chip_text()
        if chip.text() != text:
            chip.setText(text)
        if text:
            chip.adjustSize()
            spot = QPoint(target.right() - chip.width() - 7, target.y() + 7)
            if chip.pos() != spot:
                chip.move(spot)
            if chip.isHidden():
                chip.show()
                chip.raise_()
        else:
            chip.hide()


class _ScrubSignals(QObject):
    ready = Signal(str, float, str)       # video, instant, image (ou "")


class _ScrubJob(QRunnable):
    """Une image a cet instant, hors du fil de l'interface (ffmpeg)."""

    def __init__(self, signals, video: str, ts: float, width: int):
        super().__init__()
        self.signals, self.video, self.ts, self.width = signals, video, ts, width

    def run(self):
        from .media import extract_thumb
        try:
            found = extract_thumb(Path(self.video), self.ts, self.width)
        except Exception:                             # noqa: BLE001
            found = None
        try:
            self.signals.ready.emit(self.video, self.ts, str(found or ""))
        except RuntimeError:
            pass                                      # fenetre deja fermee


class ScrubPreview(QWidget):
    """L'image a l'instant vise, au survol du trait d'avancement d'un bandeau
    (comme Plex, Jellyfin ou YouTube) : on vise avant de cliquer, sans aucune
    barre de plus. Une fenetre-outil, comme le bandeau : le widget video
    natif passe par-dessus tout le reste.

    Les images viennent du cache des vignettes ; celles qui manquent sont
    extraites une a une, la derniere visee d'abord -- un survol rapide ne
    lance pas cinquante ffmpeg.
    """

    WIDTH = 200

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setStyleSheet(
            "QWidget#scrub { background: #0b0d10; border: 1px solid #39414d; }"
            "QLabel#scrubImage { background: #15181d; }"
            "QLabel#scrubTime { color: #ffffff; font-size: 12px; font-weight: 600;"
            " background: transparent; }")
        self.setObjectName("scrub")
        self.setAttribute(Qt.WA_StyledBackground, True)
        box = QVBoxLayout(self)
        box.setContentsMargins(3, 3, 3, 3)
        box.setSpacing(2)
        self.image = QLabel(self)
        self.image.setObjectName("scrubImage")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setFixedSize(self.WIDTH, int(self.WIDTH * 9 / 16))
        box.addWidget(self.image)
        self.time = QLabel("", self)
        self.time.setObjectName("scrubTime")
        self.time.setAlignment(Qt.AlignCenter)
        box.addWidget(self.time)
        self.signals = _ScrubSignals(self)
        self.signals.ready.connect(self._ready)
        self.video = ""
        self.wanted = None            # (video, instant) vise en dernier
        self.busy = False
        self.hide()

    @staticmethod
    def instant(fraction: float, duration_s: float) -> float:
        """L'instant vise, arrondi : un pas d'un centieme (2 s au moins), pour
        que le cache serve d'un survol a l'autre."""
        step = max(2.0, duration_s / 100.0)
        return max(0.0, min(duration_s - 0.5, round(fraction * duration_s / step) * step))

    def show_at(self, video: str, fraction: float, duration_s: float,
                spot: QPoint, area: QRect) -> None:
        from .media import cached_thumb
        ts = self.instant(fraction, duration_s)
        self.time.setText(human_duration(fraction * duration_s))
        key = (video, ts)
        if key != self.wanted:
            self.wanted = key
            found = cached_thumb(Path(video), ts, self.WIDTH) if video else None
            if found is not None:
                self._set_image(str(found))
            else:
                if video != self.video:
                    self.image.clear()
                self._fetch()
        self.video = video
        self.adjustSize()
        x = max(area.left(), min(area.right() - self.width(), spot.x() - self.width() // 2))
        y = spot.y() - self.height() - 6
        if self.pos() != QPoint(x, y):
            self.move(x, y)
        if self.isHidden():
            self.show()
            self.raise_()

    def _fetch(self) -> None:
        if self.busy or self.wanted is None:
            return
        video, ts = self.wanted
        self.busy = True
        QThreadPool.globalInstance().start(
            _ScrubJob(self.signals, video, ts, self.WIDTH))

    def _set_image(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        self.image.setPixmap(pixmap.scaled(self.image.size(), Qt.KeepAspectRatio,
                                           Qt.SmoothTransformation))

    def _ready(self, video: str, ts: float, path: str) -> None:
        self.busy = False
        if (video, ts) == self.wanted:
            if path:
                self._set_image(path)
            return
        # On a vise ailleurs entre-temps : la derniere visee, maintenant.
        if not self.isHidden():
            self._fetch()


class OverBar(FloatGuard, QWidget):
    """Le bandeau d'un lecteur : posé **sur** l'image, et seulement au survol.

    Le widget vidéo de Windows est une fenêtre native : il se dessine
    par-dessus tout ce qu'on lui superpose, et un bandeau ordinaire y
    disparaissait. Celui-ci est une fenêtre-outil sans cadre — la seule
    chose qui passe devant — posée au bas de l'image. Elle ne vole donc
    aucune hauteur à la vidéo, et s'efface dès qu'on s'éloigne.
    """

    RAIL = 4

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(OVER_STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.skin = QFrame(self)
        self.skin.setObjectName("overBar")
        outer.addWidget(self.skin)

        box = QVBoxLayout(self.skin)
        box.setContentsMargins(10, 6, 10, 8)
        box.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.name = QLabel("", self.skin)
        self.name.setObjectName("overName")
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(self.name, 1)
        # Ou l'on en est dans la liste (« 12 / 340 »), en or : sur la fiche,
        # on triait sans le savoir. Cache tant que personne ne le donne.
        self.pos = QLabel("", self.skin)
        self.pos.setStyleSheet("QLabel { color: #f5c542; font-weight: 600;"
                               " background: transparent; }")
        self.pos.hide()
        top.addWidget(self.pos, 0)
        self.left = QLabel("", self.skin)
        self.left.setObjectName("overLeft")
        top.addWidget(self.left, 0)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(2)
        top.addLayout(self.buttons)
        box.addLayout(top)

        # Les gestes qu'on peut cacher quand la place manque, du premier
        # sacrifie au dernier (un panneau etroit du mur).
        self.by_glyph: dict = {}
        self.spare: list = []
        self.rail = QFrame(self.skin)
        self.rail.setObjectName("overRail")
        self.rail.setFixedHeight(self.RAIL)
        self.done = QFrame(self.rail)
        self.done.setObjectName("overDone")
        self.done.setGeometry(0, 0, 0, self.RAIL)
        box.addWidget(self.rail)
        # Survoler le trait montre l'image a cet instant ; un clic y va.
        # `scrub_source` rend (video, duree en secondes), ou None : le
        # lecteur qui porte le bandeau le donne.
        self.scrub_source = None
        self.scrub = None
        # Bouton tenu sur le trait : la video suit la souris, comme VLC, sans
        # poignee a attraper. Les sauts sont espaces (`DRAG_STEP_S`) : un
        # setPosition a chaque pixel noyait le lecteur, sur le NAS surtout.
        self.dragging = False
        self._drag_sent = 0.0
        self._drag_pending = None
        self._drag_flush = QTimer(self)
        self._drag_flush.setSingleShot(True)
        self._drag_flush.timeout.connect(self._send_drag)
        # Le trait laisse passer la souris : c'est le bandeau qui la lit.
        self.rail.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.done.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.skin.setMouseTracking(True)
        self.setMouseTracking(True)
        self.skin.installEventFilter(self)
        self.hide()

    seekRequested = Signal(float)            # une fraction de la duree
    DRAG_STEP_S = 0.07

    def _along_rail(self, pos: QPoint) -> float:
        """La fraction sous la souris pendant qu'on tire : seule la largeur
        compte, on peut deborder au-dessus ou au-dela du trait."""
        rail = self.rail.geometry()
        return max(0.0, min(1.0, (pos.x() - rail.left()) / max(1, rail.width())))

    def _drag_to(self, fraction: float) -> None:
        """Le trait suit tout de suite ; la video, au plus tous les
        `DRAG_STEP_S`."""
        self.done.setGeometry(0, 0, int(self.rail.width() * fraction), self.RAIL)
        self._drag_pending = fraction
        wait = self.DRAG_STEP_S - (time.monotonic() - self._drag_sent)
        if wait <= 0:
            self._send_drag()
        elif not self._drag_flush.isActive():
            self._drag_flush.start(int(wait * 1000) + 1)

    def _send_drag(self) -> None:
        if self._drag_pending is None:
            return
        self._drag_sent = time.monotonic()
        fraction, self._drag_pending = self._drag_pending, None
        self.seekRequested.emit(fraction)

    def _stop_drag(self) -> None:
        if self.dragging:
            self.dragging = False
            self._drag_flush.stop()
            self._send_drag()             # la ou l'on a lache, exactement

    def _preview(self, fraction: float, local: QPoint) -> bool:
        """L'image a cet instant au-dessus du trait. Faux sans video."""
        source = self.scrub_source() if self.scrub_source is not None else None
        if not source or source[1] <= 0:
            return False
        if self.scrub is None:
            self.scrub = ScrubPreview(self)
        spot = self.skin.mapToGlobal(local)
        top = self.mapToGlobal(QPoint(0, 0))
        self.scrub.show_at(source[0], fraction, source[1],
                           QPoint(spot.x(), top.y()), QRect(top, self.size()))
        return True

    def _on_rail(self, pos: QPoint):
        """La fraction visee si `pos` (coordonnees du bandeau) touche le trait
        -- avec une marge : quatre pixels ne se visent pas."""
        rail = self.rail.geometry()
        if self.scrub_source is None or rail.width() <= 0:
            return None
        if not (rail.top() - 9 <= pos.y() <= rail.bottom() + 9):
            return None
        if not (rail.left() <= pos.x() <= rail.right()):
            return None
        return (pos.x() - rail.left()) / max(1, rail.width())

    def eventFilter(self, watched, event):
        if watched is self.skin:
            kind = event.type()
            if kind == QEvent.MouseMove:
                local = event.position().toPoint()
                if self.dragging and event.buttons() & Qt.LeftButton:
                    fraction = self._along_rail(local)
                    self._preview(fraction, local)
                    self._drag_to(fraction)
                    return True
                self._stop_drag()
                fraction = self._on_rail(local)
                if fraction is not None and self._preview(fraction, local):
                    self.skin.setCursor(Qt.PointingHandCursor)
                else:
                    self._end_scrub()
            elif kind == QEvent.Leave:
                if not self.dragging:
                    self._end_scrub()
            elif (kind == QEvent.MouseButtonPress
                  and event.button() == Qt.LeftButton):
                fraction = self._on_rail(event.position().toPoint())
                if fraction is not None:
                    self.dragging = True
                    self._drag_sent = 0.0
                    self._drag_to(max(0.0, min(1.0, fraction)))
                    return True
            elif (kind == QEvent.MouseButtonRelease
                  and event.button() == Qt.LeftButton and self.dragging):
                self._drag_pending = self._along_rail(event.position().toPoint())
                self._stop_drag()
                if self._on_rail(event.position().toPoint()) is None:
                    self._end_scrub()
                return True
        return super().eventFilter(watched, event)

    def _end_scrub(self) -> None:
        if self.scrub is not None and not self.scrub.isHidden():
            self.scrub.hide()
        self.skin.unsetCursor()

    def hideEvent(self, event):
        self._stop_drag()
        self._end_scrub()
        super().hideEvent(event)

    def add_gesture(self, glyph: str, tip: str, slot) -> None:
        """Un geste de plus. Le signe remplit le bouton : à douze points, on
        ne distinguait pas la flèche de la croix."""
        button = QPushButton(glyph, self.skin)
        button.setObjectName("overGesture")
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.NoFocus)
        button.setFixedSize(32, 28)
        name = GLYPHS.get(glyph)
        if name:
            dress(button, name, 20)
        button.setCursor(Qt.PointingHandCursor)
        # Le geste s'appelle sans argument. PySide passe sinon l'etat coche du
        # bouton (False) a tout slot qui accepte un parametre : toggle_cinema
        # le prenait pour « sortir du cinema », et le ⛶ ne faisait rien.
        button.clicked.connect(lambda _checked=False, s=slot: s())
        self.buttons.addWidget(button)
        self.by_glyph[glyph] = button
        if glyph == "⏯":
            # Retenu par lui-meme, et non par sa place : la coche « rester
            # dans ce dossier » se pose devant, et c'etait alors ◂ qui prenait
            # l'icone lecture / pause.
            self.pause_button = button
        return button

    def add_star(self, slot) -> QPushButton:
        """Le favori d'un clic, juste apres le nom : vide, ou doree. Le meme
        partout ou une video joue -- fiche, mur, lecteur de cote, lecteur
        flottant -- pour ne pas avoir a revenir a la planche pour l'aimer."""
        button = self.add_gesture("☆", "Mettre en favori", slot)
        self.buttons.removeWidget(button)
        self.buttons.insertWidget(0, button)
        button.setIconSize(QSize(18, 18))
        self.star_button = button
        self.favorite = None
        self.set_favorite(False)
        return button

    def _front(self) -> int:
        """Ou poser ce qui va « devant les gestes » : apres l'etoile, qui
        reste la premiere, juste a cote du nom."""
        return 1 if hasattr(self, "star_button") else 0

    def set_favorite(self, on: bool) -> None:
        on = bool(on)
        if getattr(self, "favorite", None) == on:
            return
        self.favorite = on
        button = self.star_button
        button.setText("")
        button.setIcon(filled("star", GOLD) if on else icon("star"))
        button.setToolTip("Retirer des favoris" if on else "Mettre en favori")

    def set_position(self, text: str) -> None:
        if self.pos.text() != text:
            self.pos.setText(text)
            self.pos.setVisible(bool(text))

    def set_name(self, text: str) -> None:
        self.name.setText(elide(text, 60))
        self.name.setToolTip(text)

    def set_progress(self, position: int, duration: int) -> None:
        fraction = (position / duration) if duration > 0 else 0.0
        width = max(0, self.rail.width())
        if not self.dragging:
            # Pendant qu'on tire, le trait suit la souris : le lecteur, en
            # retard d'un saut, le ferait trembler en arriere.
            self.done.setGeometry(
                0, 0, int(width * max(0.0, min(1.0, fraction))), self.RAIL)
        self.left.setText(
            f"−{human_duration(max(0, duration - position) / 1000.0)}"
            if duration > 0 else "")

    def place_on(self, target) -> None:
        """Se pose au bas de la zone d'image, sans jamais la surmonter."""
        if target is None or not target.isVisible():
            return self.hide()
        corner = target.mapToGlobal(QPoint(0, 0))
        width = max(180, target.width())
        self._fit_width(width)
        # Les gestes caches a l'instant ne comptent plus : sans cela, la
        # fenetre gardait jusqu'au tour suivant sa largeur minimale d'avant,
        # et debordait de l'image.
        self.skin.layout().activate()
        self.layout().activate()
        height = self.sizeHint().height()
        # Colle au bas de l'image, bord a bord : pose un peu au-dessus, il
        # semblait flotter au milieu de nulle part.
        wanted = QRect(corner.x(), corner.y() + target.height() - height,
                       width, height)
        if self.geometry() != wanted:
            self.setGeometry(wanted)

    def _fit_width(self, width: int) -> None:
        """Trop etroit pour tous ses gestes (un panneau du mur) : il cache les
        moins utiles, dans l'ordre de `spare`, plutot que de deborder sur le
        panneau voisin."""
        if not self.spare:
            return
        # Le temps restant et la place dans la liste comptent aussi : arrives
        # apres coup, ils poussaient les gestes hors de l'image.
        key = (width, len(self.left.text()), self.pos.text() if self.pos.isVisibleTo(self) else "")
        if getattr(self, "_fitted_for", None) == key:
            return
        self._fitted_for = key
        def needed() -> int:
            # Les marges du bandeau, le nom (au moins quelques lettres), le
            # temps restant s'il est la, puis chaque geste visible.
            shown = [self.buttons.itemAt(i).widget() for i in range(self.buttons.count())]
            shown = [w for w in shown if w is not None and not w.isHidden()]
            total = 20 + 60 + 8
            for label in (self.left, self.pos):
                if not label.isHidden():
                    total += label.sizeHint().width() + 8
            total += sum(w.sizeHint().width() for w in shown)
            return total + self.buttons.spacing() * max(0, len(shown) - 1)
        for button in self.spare:
            button.show()
        for button in self.spare:
            if needed() <= width:
                break
            button.hide()

    def reveal(self) -> None:
        """Se montre, et ne remonte au premier plan qu'a ce moment-la."""
        if self.isHidden():
            self.show()
            self.raise_()

    def add_stay(self, tip: str, on: bool, slot) -> "StayCheck":
        """La coche « rester dans ce dossier » : ◂ ▸ ne sortent plus du
        dossier de la video. Sans texte — elle se reconnait a sa place."""
        box = StayCheck(self.skin)
        box.setToolTip(tip)
        box.setChecked(on)
        box.toggled.connect(slot)
        self.buttons.insertWidget(self._front(), box)
        self.stay = box
        return box

    def add_similar(self, tip: str, slot) -> "SimilarCheck":
        """L'interrupteur « Similaires », a cote de « rester dans ce dossier »."""
        box = SimilarCheck(self.skin)
        box.setToolTip(tip)
        box.toggled.connect(slot)
        self.buttons.insertWidget(self._front(), box)
        self.similar = box
        return box

    def add_ultra(self, tip: str, slot) -> "UltraCheck":
        """L'interrupteur de l'ultra tri, devant les gestes."""
        box = UltraCheck(self.skin)
        box.setToolTip(tip)
        box.toggled.connect(slot)
        self.buttons.insertWidget(self._front(), box)
        self.ultra = box
        return box

    def add_speed(self, seconds: int, slot) -> "SpeedDial":
        """La duree du diaporama, reglable a la molette, devant les gestes.
        Cachee tant qu'on ne regarde pas des photos."""
        dial = SpeedDial(self.skin)
        dial.set_seconds(seconds)
        dial.turned.connect(slot)
        self.buttons.insertWidget(self._front(), dial)
        self.speed = dial
        dial.hide()
        return dial


class PeekCell(QFrame):
    """Une case du peek : un instant de la video, et la destination qui va avec."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("peekCell")
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        self.image = QLabel("…", self)
        self.image.setObjectName("peekImage")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(80, 45)
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        layout.addWidget(self.image, 1)
        self.caption = QLabel("", self)
        self.caption.setObjectName("peekCaption")
        self.caption.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.caption, 0)
        self._pixmap: QPixmap | None = None

    def reset(self, caption: str) -> None:
        self._pixmap = None
        self.image.setPixmap(QPixmap())
        self.image.setText("…")
        self.caption.setText(caption)

    def set_thumb(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.image.setText("—")
            return
        self._pixmap = pixmap
        self.image.setText("")
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap is not None:
            self.image.setPixmap(self._pixmap.scaled(
                self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class PeekOverlay(QWidget):
    """Neuf instants de la video, en mosaique, le temps qu'on tient la touche.

    La planche contact existait, mais elle etait modale : on y entrait, on en
    sortait. Ici on jette un oeil et on relache — la lecture n'a pas bouge.
    Chaque case porte l'une des neuf premieres destinations : on juge la video
    entiere d'un regard, et la touche a presser est ecrite dessous.
    """

    COUNT = 9
    chosen = Signal(int)      # une case cliquee : on veut aller a cet instant

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("peekOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(PEEK_STYLE)
        grid = QGridLayout(self)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(8)
        self.cells: list = []
        for slot in range(self.COUNT):
            cell = PeekCell(self)
            cell.clicked.connect(lambda s=slot: self.chosen.emit(s))
            grid.addWidget(cell, slot // 3, slot % 3)
            self.cells.append(cell)
        for index in range(3):
            grid.setRowStretch(index, 1)
            grid.setColumnStretch(index, 1)
        self.hide()

    def reset(self, captions: list) -> None:
        for slot, cell in enumerate(self.cells):
            cell.reset(captions[slot] if slot < len(captions) else "")

    def set_thumb(self, slot: int, path: str) -> None:
        if 0 <= slot < len(self.cells):
            self.cells[slot].set_thumb(path)

    def set_caption(self, slot: int, text: str) -> None:
        if 0 <= slot < len(self.cells):
            self.cells[slot].caption.setText(text)


class _Still(QWidget):
    """Une photo, entiere dans son cadre. Le cadre peut deborder de la zone
    d'image : c'est ainsi que le zoom de la fiche l'agrandit, comme la video."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.image = None
        # Vrai : la photo remplit son cadre, quitte a en rogner les bords
        # (le mur, en mode « tout remplir »).
        self.fill = False
        self.hide()

    def set_image(self, image) -> None:
        self.image = image if image is not None and not image.isNull() else None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self.image is not None:
            size = self.image.size().scaled(
                self.size(), Qt.KeepAspectRatioByExpanding if self.fill
                else Qt.KeepAspectRatio)
            spot = QRect(0, 0, size.width(), size.height())
            spot.moveCenter(self.rect().center())
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawImage(spot, self.image)
        painter.end()


class _StillSignals(QObject):
    loaded = Signal(str, QImage)


class _StillLoader(QRunnable):
    """Lit une photo hors du fil de l'interface, deja reduite a la taille
    de l'ecran : un JPEG de vingt megapixels lu en entier sur le NAS gelait
    la fiche une demi-seconde."""

    def __init__(self, path: str, longest: int, signals: _StillSignals):
        super().__init__()
        self.path = path
        self.longest = longest
        self.signals = signals

    def run(self) -> None:
        image = QImage()
        try:
            reader = QImageReader(self.path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and max(size.width(), size.height()) > self.longest:
                reader.setScaledSize(size.scaled(self.longest, self.longest,
                                                 Qt.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                # HEIC et consorts : ffmpeg sait les lire, Qt non.
                from . import media
                made = media.extract_thumb(Path(self.path), 0.0,
                                           min(self.longest, 2560))
                if made is not None:
                    image = QImage(str(made))
        except Exception:                               # noqa: BLE001
            image = QImage()
        try:
            self.signals.loaded.emit(self.path, image)
        except RuntimeError:
            pass                        # la fiche a disparu entre-temps


class _Deck:
    """Un lecteur complet : la surface, le moteur, le son.

    La fiche en a deux. Pendant qu'on regarde une video, la suivante se charge
    dans l'autre jusqu'a sa premiere image, puis attend. Passer a la suivante
    revient alors a echanger les deux : ni noir, ni attente reseau, et plus
    d'image fantome puisque chaque surface n'a jamais montre qu'un fichier.
    """

    def __init__(self, area):
        self.video = QVideoWidget(area)
        self.video.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.video.hide()
        # Chaque lecteur garde sa sortie son, branchee une fois pour toutes.
        # Une sortie unique passee de l'un a l'autre a l'echange semblait plus
        # econome ; mesure faite, c'est l'inverse : debrancher le son d'un
        # lecteur lui fait payer cent a cent cinquante millisecondes de
        # demontage sur le fil de l'interface, a chaque video suivante. Un
        # lecteur qui garde son son se vide, lui, le plus souvent en une
        # quinzaine.
        self.audio = QAudioOutput(area)
        # La bonne sortie (jamais le « mains libres » d'un casque), suivie
        # quand elle change.
        from .audiodev import follow
        follow(self.audio)
        self.player = QMediaPlayer(area)
        self.player.setVideoOutput(self.video)
        self.player.setAudioOutput(self.audio)
        self.path = ""
        self.primed = False      # une image de `path` est arrivee sur la surface

    def clear(self) -> None:
        self.player.stop()
        self.player.setSource(QUrl())
        self.video.hide()
        self.path = ""
        self.primed = False


class SinglePlayer(QWidget):
    """Mode fichier : la vidéo courante est lue en grand, avec une pellicule."""

    # Emis quand la video arrive a son terme. La boucle avait du sens tant que
    # le lecteur servait a examiner un fichier ; quand il sert a trier, revoir
    # indefiniment ce qu'on vient de voir est exactement ce qu'on ne veut pas.
    finished = Signal()
    radialRequested = Signal()   # clic droit sur l'image : les destinations en rond
    progressed = Signal(int, int)   # position, duree : pour le bandeau de survol
    # Double-clic sur l'image : le cinema, comme dans tous les lecteurs. Le
    # premier clic a deja mis en pause, sans attendre de savoir s'il en
    # viendrait un second ; le double-clic defait cette pause.
    cinemaRequested = Signal()
    # ⏯, Entree ou un clic sur une photo : le diaporama demarre, ou s'arrete.
    slideshowToggled = Signal()
    slideshow_on = False

    # Cinq reperes suffisent a se reperer dans une video : un cinquieme, deux
    # cinquiemes, et ainsi de suite. Dix prenaient deux fois plus de place pour
    # une precision dont on ne fait rien — on survole pour chercher, on ne
    # compte pas les images.
    STRIP_COUNT = 5

    # Vrai le temps que la fenetre passe au repli : cache, le lecteur se met
    # en pause au lieu de s'arreter. Arrete, il repartait du debut, sur une
    # image noire, apres une nouvelle lecture du partage.
    hold = False

    def __init__(self, count: int = 10, scroll_seconds: int = 5, parent=None):
        super().__init__(parent)
        # La pellicule ne suit plus le reglage du nombre d'apercus : elle sert a
        # se deplacer dans une video, pas a en faire le tour.
        self.count = self.STRIP_COUNT
        self.scroll_seconds = scroll_seconds
        self.loop = False
        # Posee a droite plutot qu'en dessous : une bande horizontale volait au
        # lecteur quatre-vingt-dix pixels sur toute la largeur, alors que la
        # place perdue sur le cote ne coute rien a une video large.
        # Une colonne : l'image et sa pellicule en haut, la barre d'avancement
        # en dessous sur toute la largeur. Ajoutee a la rangee horizontale, elle
        # formait une colonne de vingt pixels contre le bord droit — presente,
        # mais introuvable.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        top = QWidget(self)
        layout = QHBoxLayout(top)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        outer.addWidget(top, 1)
        self._top, self._top_row = top, layout
        # En plein ecran, la pellicule flotte sur l'image (`float_strip`).
        self._strip_floating = False
        self._strip_wanted = True
        self._strip_win = None
        self._strip_anim = None
        self._strip_out = True

        # Le lecteur est posé dans un cadre qui le rogne : agrandir sa géométrie
        # au-delà du cadre produit un zoom, sans passer par une scène graphique.
        self.video_area = QWidget(top)
        self.video_area.setObjectName("videoArea")
        # Assez pour voir, assez peu pour tenir sur un ecran agrandi.
        self.video_area.setMinimumHeight(180)
        self.decks = [_Deck(self.video_area), _Deck(self.video_area)]
        self._active = 0
        self._muted = False
        # Les photos : une image fixe a la place des lecteurs, lue hors du
        # fil de l'interface, et les dernieres gardees en memoire pour que
        # ◂ ▸ soient immediats.
        self.still = _Still(self.video_area)
        self.still_path = ""
        self._stills: OrderedDict = OrderedDict()
        self._still_wanted: set = set()
        self._still_signals = _StillSignals(self)
        self._still_signals.loaded.connect(self._still_loaded)
        self.peek = PeekOverlay(self.video_area)
        self.peeking = False
        # Le widget video de Windows avale les clics : on lui prend ses gestes
        # a la source, comme la planche le fait deja.
        for deck in self.decks:
            deck.video.mousePressEvent = self._scrub_press
            deck.video.mouseMoveEvent = self._scrub_move
            deck.video.mouseReleaseEvent = self._scrub_release
            deck.video.mouseDoubleClickEvent = self._double_click
        self._scrub_x0 = None
        self._scrub_pos0 = 0
        self._scrubbing = False
        self._zoomed_while_held = False
        # Le dernier relachement a-t-il bascule la pause ? Un double-clic la
        # rebascule, pour que ses deux clics se compensent.
        self._click_paused = False
        # L'heure du dernier appui recu par l'image elle-meme : un double-clic
        # dont le premier appui a eu lieu ailleurs n'est pas pour elle.
        self._press_ts = None
        layout.addWidget(self.video_area, 1)

        self.zoom = 1.0
        self.zoom_focus = QPointF(0.5, 0.5)   # point fixe, en proportion du cadre

        self.position_label = QLabel("", self.video_area)
        self.position_label.setObjectName("tileBadge")
        self.position_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.position_label.hide()

        # Un trait tres fin, pose sur le bas de l'image, toujours la. Il
        # remplace la rangee qu'on lui reservait sous l'image : vingt-quatre
        # pixels de hauteur pris a la video, pour un trait de huit. Le detail
        # — nom, temps restant, gestes — vient au survol, par le bandeau.
        self.marks = PlayMarks(self, rail=True, left=False)

        strip = QWidget(top)
        self.strip_layout = QVBoxLayout(strip)
        self.strip_layout.setContentsMargins(0, 0, 0, 0)
        self.strip_layout.setSpacing(6)
        self.tiles: list = []
        for slot in range(self.count):
            tile = PreviewTile(slot, strip)
            # Sans pastille de duree : `show_duration` le sait.
            tile.strip_mode = True
            # Cinq cases a quatre-vingt-six points imposaient quatre cent
            # trente points de hauteur a la fiche entiere : sur un ecran
            # agrandi, la derniere rangee passait sous le bord. Elles gardent
            # leur taille des qu'il y a la place, mais cedent quand il n'y en
            # a pas.
            tile.setMinimumSize(90, 44)
            tile.setMaximumWidth(190)
            self.strip_layout.addWidget(tile, 1)
            self.tiles.append(tile)
        strip.setFixedWidth(176)
        layout.addWidget(strip)
        self.strip = strip

        # Le widget garde a l ecran la derniere image rendue : en passant d une
        # video a l autre, on voyait donc un instant celle d avant. On le cache
        # et l on vide sa surface jusqu a la premiere image de la nouvelle.
        self._awaiting_frame = False
        self._blackout = False
        self.blackout_timer = QTimer(self)
        self.blackout_timer.setSingleShot(True)
        self.blackout_timer.timeout.connect(self._end_blackout)
        for deck in self.decks:
            deck.video.videoSink().videoFrameChanged.connect(
                lambda frame, d=deck: self._on_frame(d, frame))
            deck.player.mediaStatusChanged.connect(
                lambda status, d=deck: self._on_status(d, status))
            deck.player.positionChanged.connect(
                lambda pos, d=deck: self._on_deck_position(d, pos))
            deck.player.durationChanged.connect(
                lambda _dur, d=deck: self._on_deck_position(d, d.player.position()))

        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(30)
        self.hover_timer.timeout.connect(self._poll_hover)
        self.hovered_slot = -1

        # La reserve a remplacer, et quand (voir preload).
        self._spare_wanted = ""
        self.spare_timer = QTimer(self)
        self.spare_timer.setSingleShot(True)
        self.spare_timer.timeout.connect(self._preload_later)

        self.position_timer = QTimer(self)
        self.position_timer.setSingleShot(True)
        self.position_timer.timeout.connect(self.position_label.hide)

    # Le code de la fiche parle d'« un » lecteur : c'est toujours celui qui
    # joue. L'autre est la reserve.
    @property
    def player(self):
        return self.decks[self._active].player

    @property
    def video(self):
        return self.decks[self._active].video

    def scrub_source(self):
        """(video, duree en secondes) pour l'apercu du trait, ou None."""
        if self.still_path:
            return None
        deck = self.decks[self._active]
        duration = deck.player.duration()
        if not deck.path or duration <= 0:
            return None
        return deck.path, duration / 1000.0

    def seek_fraction(self, fraction: float) -> None:
        """Un clic sur le trait du bandeau : a cet endroit de la video."""
        duration = self.player.duration()
        if self.still_path or duration <= 0:
            return
        target = int(max(0.0, min(1.0, fraction)) * duration)
        self.player.setPosition(min(target, max(0, duration - 500)))
        self._show_position(target)

    @property
    def audio(self):
        return self.decks[self._active].audio

    @property
    def spare(self):
        return self.decks[1 - self._active]

    # Le delai avant de remplacer une reserve deja chargee : la fiche se peint
    # d'abord, et le demontage tombe pendant son noir d'ouverture.
    SPARE_SWAP_MS = 60

    def preload(self, path: str) -> None:
        """Charge `path` dans la reserve, jusqu'a sa premiere image, puis attend.

        Une reserve vide se charge tout de suite. Une reserve qui tient
        encore un autre fichier -- on revient de la planche sur une autre
        video -- se vide un instant plus tard : son demontage, cent a quatre
        cents millisecondes sur le fil de l'interface, passait avant la
        peinture de la fiche, et c'etait le clic qui semblait lent.
        """
        if is_photo(path):
            self._load_still(path)
            return
        spare = self.spare
        if spare.path == path:
            self._spare_wanted = ""
            return
        if spare.path:
            self._spare_wanted = path
            self.spare_timer.start(self.SPARE_SWAP_MS)
            return
        self._spare_wanted = ""
        self._load_spare(path)

    def _load_spare(self, path: str) -> None:
        spare = self.spare
        spare.path = path
        spare.primed = False
        spare.audio.setMuted(True)
        spare.video.hide()
        spare.player.setSource(QUrl.fromLocalFile(path))
        spare.player.play()

    def _preload_later(self) -> None:
        path, self._spare_wanted = self._spare_wanted, ""
        if not path or not self.isVisible():
            return
        if path in (self.spare.path, self.decks[self._active].path):
            return
        self._load_spare(path)

    def peek_begin(self, captions: list) -> None:
        """Montre la mosaique par-dessus l'image, sans arreter la lecture."""
        self.peeking = True
        self.peek.reset(captions)
        self.peek.setGeometry(self.video_area.rect())
        for deck in self.decks:
            deck.video.hide()
        self.peek.show()
        self.peek.raise_()

    def peek_end(self) -> None:
        if not self.peeking:
            return
        self.peeking = False
        self.peek.hide()
        deck = self.decks[self._active]
        if deck.primed and not self._blackout:
            deck.video.show()

    def peek_thumb(self, slot: int, path: str) -> None:
        self.peek.set_thumb(slot, path)

    # -- scrub --------------------------------------------------------------
    def _scrub_press(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._press_ts = event.timestamp()
        if event.button() == Qt.LeftButton and self.player.source().isValid():
            self._scrub_x0 = event.globalPosition().x()
            self._scrub_pos0 = self.player.position()
            self._scrubbing = False
            self._zoomed_while_held = False
            event.accept()
            return
        if event.button() == Qt.RightButton:
            return self.mousePressEvent(event)

    def _scrub_move(self, event) -> None:
        """Glisser sur l'image : toute la largeur vaut toute la duree. Apres
        un zoom bouton tenu, glisser promene l'image agrandie : avancer dans
        la video la faisait sauter, et la barre avec."""
        if self._scrub_x0 is None or not (event.buttons() & Qt.LeftButton):
            return
        if self._zoomed_while_held:
            self._pan_by(event.globalPosition())
            event.accept()
            return
        dx = event.globalPosition().x() - self._scrub_x0
        if not self._scrubbing and abs(dx) < 6:
            return
        self._scrubbing = True
        duration = self.player.duration()
        width = max(1, self.video_area.width())
        if duration <= 0:
            return
        target = int(self._scrub_pos0 + dx / width * duration)
        target = max(0, min(duration - 500, target))
        self.player.setPosition(target)
        self._show_position(target)
        event.accept()

    def _scrub_release(self, event) -> None:
        if event.button() != Qt.LeftButton or self._scrub_x0 is None:
            return
        was_click = not self._scrubbing and not self._zoomed_while_held
        self._scrub_x0 = None
        self._scrubbing = False
        self._click_paused = was_click and not getattr(self, "ultra", None)
        if was_click and getattr(self, "ultra", None):
            # Ultra tri : le clic gauche garde, et passe a la suivante.
            self.ultra("left")
        elif was_click:
            # Un clic sans glisser : pause ou reprise, comme sur le mur. Tout
            # de suite : attendre de savoir si un second clic suit ferait
            # payer a chaque pause le delai du double-clic.
            self.toggle_pause()
        event.accept()

    def _double_click(self, event) -> None:
        """Double-clic sur l'image : le cinema, sans a-coup dans la lecture.

        Qt livre appui, relachement, double-clic, relachement. Le premier
        relachement a bascule la pause ; on la rebascule ici, et le second
        relachement ne fait rien puisqu'aucun appui ne l'a arme. Le
        double-clic par defaut rappelait l'appui : la video s'arretait puis
        repartait, sans rien produire d'autre.
        """
        if getattr(self, "ultra", None):
            # Ultra tri : des clics rapides ne sont pas un plein ecran, chacun
            # compte (la fenetre ecarte ceux qui viennent trop vite).
            self._scrub_x0 = None
            self._scrubbing = False
            event.accept()
            return self.ultra("left" if event.button() == Qt.LeftButton else "right")
        if event.button() != Qt.LeftButton:
            # Le clic droit garde son effet, double ou non : les destinations.
            return self.mousePressEvent(event)
        if self.peeking:
            event.accept()
            return
        pressed, self._press_ts = self._press_ts, None
        interval = QGuiApplication.styleHints().mouseDoubleClickInterval()
        if pressed is None or event.timestamp() - pressed > interval:
            # Le premier clic est tombe ailleurs : sur la carte de la planche
            # ou la case du mur qui vient d'ouvrir cette fiche. Il n'etait pas
            # pour l'image, le second non plus -- sans cela, ouvrir une video
            # d'un double-clic la passait aussitot en plein ecran.
            self._click_paused = False
            self._scrub_x0 = None
            event.accept()
            return
        if self._click_paused:
            self.toggle_pause()
        self._click_paused = False
        self._scrub_x0 = None
        self._scrubbing = False
        event.accept()
        self.cinemaRequested.emit()

    def mouseDoubleClickEvent(self, event):
        # Seule l'image mene au cinema : un double-clic sur la pellicule
        # n'est qu'un clic de trop sur une case.
        local = self.video_area.mapFrom(self, event.position().toPoint())
        if self.video_area.rect().contains(local):
            return self._double_click(event)
        super().mouseDoubleClickEvent(event)

    def release(self, target=None) -> None:
        """Lache les fichiers : avant de deplacer ou supprimer.

        Sans `target`, les deux. Avec, la reserve garde la video suivante
        tant qu'elle n'est pas celle qu'on deplace, ni dans le dossier qu'on
        deplace : apres un tri, la suivante s'affiche alors par simple
        echange, sans noir ni rechargement sur le partage.
        """
        spare = self.spare
        wanted = getattr(self, "_spare_wanted", "")
        if wanted and (target is None or _within(wanted, target)):
            # Une reserve pas encore chargee ne doit pas ouvrir, juste apres,
            # le fichier qu'on range.
            self._spare_wanted = ""
        for deck in self.decks:
            if (target is not None and deck is spare and spare.path
                    and not _within(spare.path, target)):
                continue
            deck.clear()

    def showEvent(self, event):
        super().showEvent(event)
        self.hover_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.hover_timer.stop()
        self.marks.hide()
        if self.hold:
            # Pause seulement s'il jouait : sur un lecteur arrete, pause()
            # rechargerait la video pour en montrer la premiere image.
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            return
        self.stop()

    def hide_strip(self) -> None:
        """Retire la pellicule : tout l'espace revient a l'image."""
        self._strip_wanted = False
        self.strip.hide()
        if self._strip_win is not None:
            self._strip_win.hide()

    def show_strip(self) -> None:
        self._strip_wanted = True
        self.strip.show()

    # -- la pellicule en plein ecran : posee sur l'image, au survol ----------
    def float_strip(self, on: bool) -> None:
        """En plein ecran, la pellicule quitte sa colonne : l'image prend
        toute la largeur, et les cinq instants glissent depuis la droite au
        survol (`reveal_strip`), puis s'en retournent (`tuck_strip`).

        Une petite fenetre a part, comme le bandeau : posee dans l'image, le
        lecteur natif la recouvrait."""
        on = bool(on)
        if on == self._strip_floating:
            return
        self._strip_floating = on
        if on:
            if self._strip_win is None:
                from PySide6.QtWidgets import QVBoxLayout as _Column
                win = GuardedWidget(self, Qt.Tool | Qt.FramelessWindowHint
                              | Qt.WindowDoesNotAcceptFocus)
                win.setAttribute(Qt.WA_ShowWithoutActivating, True)
                win.setObjectName("stripFloat")
                win.setStyleSheet("QWidget#stripFloat { background: #0b0d10; }")
                column = _Column(win)
                column.setContentsMargins(8, 8, 8, 8)
                self._strip_win = win
            self._top_row.removeWidget(self.strip)
            self.strip.setParent(self._strip_win)
            self._strip_win.layout().addWidget(self.strip)
            self.strip.setVisible(self._strip_wanted)
            self._strip_win.hide()
            self._strip_out = True
        else:
            if self._strip_anim is not None:
                self._strip_anim.stop()
            if self._strip_win is not None:
                self._strip_win.hide()
                self._strip_win.layout().removeWidget(self.strip)
            self.strip.setParent(self._top)
            self._top_row.addWidget(self.strip)
            self.strip.setVisible(self._strip_wanted)
            self._strip_out = True

    def _strip_place(self) -> tuple:
        """(position visible, position cachee, taille) contre le bord droit
        de l'image."""
        area = self.video_area
        corner = area.mapToGlobal(QPoint(0, 0))
        width = self.strip.width() + 16
        height = max(220, min(area.height() - 120, 640))
        y = corner.y() + (area.height() - height) // 2
        right = corner.x() + area.width()
        return QPoint(right - width - 14, y), QPoint(right + 2, y), QSize(width, height)

    def _animate_strip(self, start: QPoint, end: QPoint, fade_from: float,
                       fade_to: float, ms: int, then=None) -> None:
        from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup, QPropertyAnimation
        win = self._strip_win
        if self._strip_anim is not None:
            self._strip_anim.stop()
        group = QParallelAnimationGroup(win)
        move = QPropertyAnimation(win, b"pos", group)
        move.setDuration(ms)
        move.setStartValue(start)
        move.setEndValue(end)
        move.setEasingCurve(QEasingCurve.OutCubic if fade_to > fade_from
                            else QEasingCurve.InCubic)
        fade = QPropertyAnimation(win, b"windowOpacity", group)
        fade.setDuration(ms)
        fade.setStartValue(fade_from)
        fade.setEndValue(fade_to)
        group.addAnimation(move)
        group.addAnimation(fade)
        if then is not None:
            group.finished.connect(then)
        self._strip_anim = group
        group.start()

    def reveal_strip(self) -> None:
        """Les cinq instants arrivent de la droite, en fondu."""
        win = self._strip_win
        if not self._strip_floating or win is None or not self._strip_wanted:
            return
        shown, hidden, size = self._strip_place()
        if not self._strip_out:
            if win.isVisible() and win.pos() != shown and (
                    self._strip_anim is None
                    or self._strip_anim.state() != self._strip_anim.State.Running):
                win.move(shown)                       # l'image a bouge : on suit
            return
        self._strip_out = False
        win.resize(size)
        start = win.pos() if win.isVisible() else hidden
        win.setWindowOpacity(win.windowOpacity() if win.isVisible() else 0.0)
        win.move(start)
        win.show()
        win.raise_()
        self._animate_strip(start, shown, win.windowOpacity(), 1.0, 220)

    def tuck_strip(self, now: bool = False) -> None:
        """Ils repartent vers la droite, et disparaissent."""
        win = self._strip_win
        if not self._strip_floating or win is None or self._strip_out:
            return
        self._strip_out = True
        if now or not win.isVisible():
            if self._strip_anim is not None:
                self._strip_anim.stop()
            win.hide()
            return
        _shown, hidden, _size = self._strip_place()
        self._animate_strip(win.pos(), hidden, win.windowOpacity(), 0.0, 280,
                            then=lambda: win.hide() if self._strip_out else None)

    def strip_has_pointer(self) -> bool:
        win = self._strip_win
        return bool(win is not None and win.isVisible()
                    and win.geometry().contains(QCursor.pos()))

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self.audio.setMuted(muted)

    # -- photos ---------------------------------------------------------------
    STILLS_KEPT = 8

    def _still_longest(self) -> int:
        """Assez de pixels pour l'ecran entier, zoom modere compris."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return 2560
        size = screen.size() * screen.devicePixelRatio()
        return max(1280, min(4096, int(max(size.width(), size.height()) * 1.5)))

    def _load_still(self, path: str) -> None:
        if path in self._stills or path in self._still_wanted:
            return
        self._still_wanted.add(path)
        QThreadPool.globalInstance().start(
            _StillLoader(path, self._still_longest(), self._still_signals))

    def _still_loaded(self, path: str, image) -> None:
        self._still_wanted.discard(path)
        self._stills[path] = image
        self._stills.move_to_end(path)
        while len(self._stills) > self.STILLS_KEPT:
            self._stills.popitem(last=False)
        if path == self.still_path:
            self.still.set_image(image)
            self.position_label.hide()

    def _show_still(self, path: str) -> None:
        """Une photo dans la fiche : les lecteurs video se taisent."""
        for deck in self.decks:
            if deck.path or deck.player.source().isValid():
                deck.clear()
        self.blackout_timer.stop()
        self._blackout = False
        self._awaiting_frame = False
        self._spare_wanted = ""
        self.reset_zoom()
        self.still_path = path
        image = self._stills.get(path)
        if image is not None:
            self._stills.move_to_end(path)
        self.still.set_image(image)
        self.still.show()
        self.still.raise_()
        self.marks.set_progress(0, 0)
        if image is None:
            self._load_still(path)

    def _hide_still(self) -> None:
        if self.still_path:
            self.still_path = ""
            self.still.hide()
            self.still.set_image(None)

    def set_item(self, path: str, message: str = "…") -> None:
        for tile in self.tiles:
            tile.reset()
            tile.placeholder.setText(message)
        if is_photo(path):
            self._show_still(path)
            return
        self._hide_still()
        spare = self.spare
        # Un clic ou un appui d'avant ne vaut plus pour ce fichier.
        self._click_paused = False
        self._press_ts = None
        if spare.path == path:
            # La suivante etait deja prete : on echange, sans rien attendre.
            # L'ancienne est videe d'abord : la vider pendant que la suivante
            # decodait deja coutait trois a huit fois plus cher (jusqu'a
            # plusieurs centaines de millisecondes de gel a chaque fleche).
            old = self.decks[self._active]
            self._active = 1 - self._active
            old.clear()
            self.blackout_timer.stop()
            self._blackout = False
            self.reset_zoom()
            spare.audio.setMuted(self._muted)
            if spare.primed:
                self._awaiting_frame = False
                spare.video.show()
            else:
                self._awaiting_frame = True
            spare.player.setPosition(0)
            spare.player.play()
            return
        self.reset_zoom()
        deck = self.decks[self._active]
        deck.path = path
        deck.primed = False
        # Attendre la premiere image ne suffisait pas : Qt en livre parfois une
        # qui appartient encore au fichier precedent, et l'on voyait passer une
        # image subliminale. On impose donc un noir franc, court mais entier :
        # l'image ne revient qu'une fois ce delai passe **et** une image du
        # nouveau fichier arrivee.
        self._awaiting_frame = True
        self._blackout = True
        deck.video.hide()
        try:
            deck.video.videoSink().setVideoFrame(QVideoFrame())
        except (RuntimeError, TypeError):
            pass
        self.blackout_timer.start(180)
        deck.player.setSource(QUrl.fromLocalFile(path))
        deck.player.play()

    def _on_frame(self, deck, frame) -> None:
        """Premiere image d'un fichier : la reserve s'arrete dessus, la fiche l'attend."""
        try:
            if not frame.isValid():
                return
        except (RuntimeError, AttributeError):
            pass
        if deck is self.spare:
            if not deck.primed:
                deck.primed = True
                deck.player.pause()
            return
        deck.primed = True
        if not self._awaiting_frame:
            return
        self._awaiting_frame = False
        if not self._blackout:
            deck.video.show()

    def _on_deck_position(self, deck, position: int) -> None:
        if deck is self.decks[self._active]:
            self._on_position(position)

    def _end_blackout(self) -> None:
        self._blackout = False
        if not self._awaiting_frame:
            self.video.show()

    def set_plan(self, plan: list) -> None:
        for slot, tile in enumerate(self.tiles):
            if slot < len(plan):
                tile.set_source(*plan[slot])

    def set_thumb(self, slot: int, path: str) -> None:
        if 0 <= slot < len(self.tiles):
            self.tiles[slot].set_thumb(path)

    def set_failed(self, slot: int) -> None:
        if 0 <= slot < len(self.tiles):
            self.tiles[slot].set_failed()

    def _on_status(self, deck, status) -> None:
        if deck is not self.decks[self._active]:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.loop:
                self.player.setPosition(0)
                self.player.play()
                return
            self.finished.emit()

    def set_loop(self, loop: bool) -> None:
        """En boucle, ou bien on passe a la suivante une fois la fin atteinte."""
        self.loop = loop

    RAIL_HEIGHT = 8

    def _on_position(self, position: int) -> None:
        duration = self.player.duration()
        self.marks.set_progress(position, duration)
        self.progressed.emit(position, duration)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_zoom()
        self._on_position(self.player.position())

    def _poll_hover(self) -> None:
        # Survoler une image de la pellicule déplace la lecture à cet instant.
        # Le lecteur flottant repond au survol meme sans avoir le clavier : on
        # travaille ailleurs pendant qu'il joue.
        owner = self.window()
        if not self.isVisible() or not (
                owner.isActiveWindow() or getattr(owner, "hover_anytime", False)):
            return
        slot = -1
        for index, tile in enumerate(self.tiles):
            if tile.rect().contains(tile.mapFromGlobal(QCursor.pos())):
                slot = index
                break
        if slot == self.hovered_slot:
            return
        if self.hovered_slot != -1:
            self.tiles[self.hovered_slot].set_hovered(False)
        self.hovered_slot = slot
        if slot != -1:
            self.tiles[slot].set_hovered(True)
            self.player.setPosition(int(self.tiles[slot].ts * 1000))
            self.player.play()

    def wheelEvent(self, event):
        """Molette : parcourir la vidéo.

        Bouton gauche maintenu, ou Ctrl : zoomer là où pointe la souris.
        """
        if (event.buttons() & Qt.LeftButton or event.modifiers() & Qt.ControlModifier
                or self.still_path):
            # Une photo n'a pas de temps a parcourir : la molette zoome.
            self._zoomed_while_held = bool(event.buttons() & Qt.LeftButton)
            return self._zoom_at(event)
        if not self.player.source().isValid():
            return super().wheelEvent(event)
        position = max(0, self.player.position() + seek_step(event, self.scroll_seconds))
        duration = self.player.duration()
        if duration > 0:
            position = min(position, max(0, duration - 500))
        self.player.setPosition(position)
        self._show_position(position)
        event.accept()

    def _zoom_at(self, event) -> None:
        """Agrandit ou réduit l'image en gardant fixe le point sous le pointeur."""
        notches = event.angleDelta().y() / 120.0
        if not notches:
            return
        area = self.video_area.geometry()
        local = event.position().toPoint() - area.topLeft()
        if area.width() > 0 and area.height() > 0:
            self.zoom_focus = QPointF(
                max(0.0, min(1.0, local.x() / area.width())),
                max(0.0, min(1.0, local.y() / area.height())),
            )
        goal = max(1.0, min(6.0, self._zoom_goal * (1.25 ** notches)))
        self._zoom_to(goal)
        if self._zoomed_while_held:
            self._pan_last = QCursor.pos()
        self.position_label.setText(f"×{goal:.1f}" if goal > 1 else "×1")
        self.position_label.adjustSize()
        # La pastille est fille du cadre video : ses coordonnees sont celles du
        # cadre, pas de la fenetre.
        inner = self.video_area.rect()
        self.position_label.move(
            inner.right() - self.position_label.width() - 10, 10)
        self.position_label.raise_()
        self.position_label.show()
        self.position_timer.start(1500)
        event.accept()

    def _apply_zoom(self) -> None:
        """Place le lecteur dans son cadre selon le facteur et le point fixe."""
        area = self.video_area.rect()
        width = int(area.width() * self.zoom)
        height = int(area.height() * self.zoom)
        # Le point visé doit rester au même endroit à l'écran après l'agrandissement.
        left = int(self.zoom_focus.x() * (area.width() - width))
        top = int(self.zoom_focus.y() * (area.height() - height))
        # Les deux surfaces, pour que la reserve soit deja en place a l'echange.
        for deck in self.decks:
            deck.video.setGeometry(left, top, width, height)
        self.still.setGeometry(left, top, width, height)
        self.peek.setGeometry(area)

    @property
    def _zoom_goal(self) -> float:
        """Le facteur vise : plusieurs crans rapides s'additionnent, meme
        pendant que l'image s'agrandit encore."""
        anim = getattr(self, "_zoom_anim", None)
        if anim is not None and anim.state() == QVariantAnimation.Running:
            return float(anim.endValue())
        return self.zoom

    def _zoom_to(self, goal: float) -> None:
        """Le zoom glisse jusqu'a `goal` en un instant, au lieu de sauter."""
        anim = getattr(self, "_zoom_anim", None)
        if anim is None:
            anim = self._zoom_anim = QVariantAnimation(self)
            anim.setDuration(140)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.valueChanged.connect(self._zoom_frame)
        anim.stop()
        anim.setStartValue(float(self.zoom))
        anim.setEndValue(float(goal))
        anim.start()

    def _zoom_frame(self, value) -> None:
        self.zoom = float(value)
        self._apply_zoom()

    def _pan_by(self, where) -> None:
        """Deplace l'image agrandie avec la souris."""
        last = getattr(self, "_pan_last", None)
        self._pan_last = where.toPoint() if hasattr(where, "toPoint") else where
        if last is None or self.zoom <= 1.0:
            return
        area = self.video_area.rect()
        span_x = area.width() * (self.zoom - 1.0)
        span_y = area.height() * (self.zoom - 1.0)
        dx = self._pan_last.x() - last.x()
        dy = self._pan_last.y() - last.y()
        fx = self.zoom_focus.x() - (dx / span_x if span_x > 0 else 0.0)
        fy = self.zoom_focus.y() - (dy / span_y if span_y > 0 else 0.0)
        self.zoom_focus = QPointF(max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy)))
        self._apply_zoom()

    def reset_zoom(self) -> None:
        anim = getattr(self, "_zoom_anim", None)
        if anim is not None:
            anim.stop()
        self.zoom = 1.0
        self.zoom_focus = QPointF(0.5, 0.5)
        self._apply_zoom()

    def _show_position(self, position: int) -> None:
        duration = self.player.duration()
        text = human_duration(position / 1000.0)
        if duration > 0:
            text += f" / {human_duration(duration / 1000.0)}"
        self.position_label.setText(text)
        self.position_label.adjustSize()
        self.position_label.move(
            self.video_area.geometry().right() - self.position_label.width() - 10,
            self.video_area.geometry().top() + 10,
        )
        self.position_label.raise_()
        self.position_label.show()
        self.position_timer.start(1800)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            return self._scrub_press(event)
        if event.button() == Qt.RightButton and getattr(self, "ultra", None):
            # Ultra tri : le clic droit supprime, sans menu.
            event.accept()
            return self.ultra("right")
        # Le clic droit remet l'image à sa taille : geste unique, sans menu.
        if event.button() == Qt.RightButton and self.zoom <= 1.0:
            # Le clic droit ouvre les destinations autour du pointeur ; s'il y
            # a un zoom, il le defait d'abord — un geste, un effet.
            self.radialRequested.emit()
            return
        if event.button() == Qt.RightButton and self.zoom > 1.0:
            self.reset_zoom()
            self.position_label.setText("×1")
            self.position_label.adjustSize()
            self.position_label.move(self.video_area.geometry().right()
                                     - self.position_label.width() - 10,
                                     self.video_area.geometry().top() + 46)
            self.position_label.raise_()
            self.position_label.show()
            self.position_timer.start(1200)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._scrub_move(event)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._scrub_release(event)
        super().mouseReleaseEvent(event)

    def jump(self, seconds: float) -> None:
        """Maj+← / Maj+→ : quelques secondes en arriere ou en avant. Les
        fleches seules restent au tri (la precedente, la suivante)."""
        if self.still_path:
            return
        duration = self.player.duration()
        target = self.player.position() + int(seconds * 1000)
        if duration > 0:
            target = min(duration - 1000, target)
        target = max(0, target)
        self.player.setPosition(target)
        self._show_position(target)

    def toggle_pause(self) -> None:
        if self.still_path:
            # Une photo ne joue pas : le meme geste lance ou arrete le
            # diaporama, que la fenetre fait avancer.
            self.slideshowToggled.emit()
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self) -> None:
        # Seul le lecteur qui joue s'arrete. La reserve reste en pause sur la
        # premiere image de la suivante : la vider coutait souvent cent a
        # trois cents millisecondes sur le fil de l'interface, et c'etait le
        # prix d'Echap, d'un changement d'onglet ou du repli. Si l'on revient
        # a la suivante, elle est prete ; sinon, son remplacement se fait au
        # prochain prechargement, pendant le noir d'une ouverture. Elle ne
        # verrouille rien de genant : `release` la lache avant toute
        # operation sur le disque.
        self.player.stop()


class TagsDialog(QDialog):
    """Mots-cles automatiques : un par ligne, mot ou expression.

    Chaque mot devient un dossier virtuel rassemblant les videos dont le nom le
    comporte. Ce sont des vues, non des rangements : elles ne se deplacent pas,
    mais on edite normalement les videos qu'elles reunissent.
    """

    def __init__(self, tags: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mots-clés automatiques")
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        # Repliee : sur une seule ligne, la phrase imposait sa longueur a la
        # fenetre, qui passait de 560 a plus de 1 000 pixels.
        self.intro = QLabel(
            "Un mot ou une expression par ligne. Chaque ligne devient un dossier "
            "virtuel réunissant les vidéos dont le nom la comporte, où qu'elle "
            "soit rangée. La casse et les accents sont ignorés.", self)
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.editor = QPlainTextEdit(self)
        self.editor.setPlaceholderText("plage\nmontagne\nsaison 2")
        self.editor.setPlainText("\n".join(tags))
        layout.addWidget(self.editor, 1)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        box.button(QDialogButtonBox.Ok).setText("Enregistrer")
        box.button(QDialogButtonBox.Cancel).setText("Annuler")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def result_tags(self) -> list:
        seen = []
        for line in self.editor.toPlainText().splitlines():
            term = line.strip()
            if term and term.lower() not in [t.lower() for t in seen]:
                seen.append(term)
        return seen


class VeilDialog(QDialog):
    """Les noms de dossiers qu'on ne veut pas voir, et ceux qu'on reaffiche.

    Coche : le dossier est masque partout — listes, recherche, mots-cles,
    mur, doublons, partage. Decoche : il revient, mais son nom reste dans la
    liste, pret a etre recoche. Rien ne bouge sur le disque.
    """

    def __init__(self, names: list, off: list, parent=None, start: str = ""):
        super().__init__(parent)
        # Ou s'ouvre le choix des dossiers : la racine en cours.
        self.start = start
        self.setWindowTitle("Dossiers masqués")
        self.resize(460, 420)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Les dossiers qui portent un de ces noms, où qu'ils soient, et tout "
            "ce qu'ils contiennent, sont cachés de la recherche et des listes. "
            "Décochez un nom pour le réafficher sans l'oublier. La casse est "
            "ignorée ; rien n'est déplacé sur le disque.", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.list = QListWidget(self)
        low_off = {str(n).casefold() for n in off}
        for name in names:
            self._add_row(name, str(name).casefold() not in low_off)
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.entry = QLineEdit(self)
        self.entry.setPlaceholderText("…ou tapez un nom, puis Entrée (par ex. @eaDir)")
        self.entry.returnPressed.connect(self.add_name)
        add = QPushButton("Ajouter des dossiers…", self)
        add.setToolTip("Choisir un ou plusieurs dossiers (Ctrl ou Maj) : leurs noms "
                       "s'ajoutent à la liste")
        add.clicked.connect(self.add_folders)
        row.addWidget(add)
        row.addWidget(self.entry, 1)
        remove = QPushButton("Retirer", self)
        remove.setToolTip("Enlève le nom sélectionné de la liste")
        remove.clicked.connect(self.remove_name)
        row.addWidget(remove)
        layout.addLayout(row)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        box.button(QDialogButtonBox.Ok).setText("Enregistrer")
        box.button(QDialogButtonBox.Cancel).setText("Annuler")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        # Entree dans le champ ajoute le nom, elle ne ferme pas la fenetre.
        for button in box.buttons():
            button.setAutoDefault(False)
        layout.addWidget(box)

    def _add_row(self, name: str, on: bool) -> None:
        item = QListWidgetItem(str(name), self.list)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _names(self) -> list:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def add_folders(self) -> None:
        """Les dossiers choisis dans l'explorateur : leurs noms, tous d'un coup."""
        for folder in pick_folders(self, "Dossiers à masquer", self.start):
            self._take(folder.name or str(folder))

    def add_name(self) -> None:
        self._take(self.entry.text())
        self.entry.clear()

    def _take(self, text: str) -> None:
        # Un nom de dossier, pas un chemin : c'est ainsi que le masque compare.
        name = str(text).strip().strip("\\/").split("\\")[-1].split("/")[-1]
        if not name:
            return
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.text().casefold() == name.casefold():
                item.setCheckState(Qt.Checked)
                self.list.setCurrentItem(item)
                return
        self._add_row(name, True)
        self.list.setCurrentRow(self.list.count() - 1)

    def remove_name(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)

    def result_veil(self) -> tuple:
        """(tous les noms, ceux qui sont decoches)."""
        names = self._names()
        off = [self.list.item(i).text() for i in range(self.list.count())
               if self.list.item(i).checkState() != Qt.Checked]
        return names, off


class _Restorer(QThread):
    """Remet des elements de la corbeille de session en place, hors de
    l'interface. Rend la liste des echecs, une fois tout tente."""

    done = Signal(list)

    def __init__(self, trash, entries: list, parent=None):
        super().__init__(parent)
        self.trash = trash
        self.entries = list(entries)

    def run(self) -> None:
        failures = []
        for entry in self.entries:
            try:
                self.trash.restore(entry)
            except ActionError as exc:
                failures.append(str(exc))
            except (OSError, ValueError) as exc:
                # Un dossier d'origine qu'on ne peut plus recreer, un element
                # deja retire par ailleurs : on le dit, on passe au suivant.
                failures.append(f"{entry.name} : {exc}")
        self.done.emit(failures)


def trash_fate(entries: list, mode: str = "recycle") -> str:
    """Ce qui arrivera, a la fermeture, a ce qui reste dans la corbeille.

    La boite promettait toujours la corbeille de Windows, alors que sur le
    NAS il n'y en a pas : tout y est detruit pour de bon a la fermeture --
    c'est voulu, encore faut-il le lire avant qu'il soit trop tard.
    """
    from .config import APP_NAME
    from .media import is_network_path
    count = len(entries)
    if not count:
        return "La corbeille de session est vide."
    head = f"{count} élément(s) écartés. Rien n'est encore supprimé : "
    if mode == "local_trash":
        return head + (f"à la fermeture, le contenu rejoindra le dossier de "
                       f"secours de {APP_NAME}.")
    remote = sum(1 for entry in entries if is_network_path(entry.stored))
    if mode == "permanent" or remote == count:
        where = "" if mode == "permanent" else " (sur le NAS, il n'y a pas de corbeille)"
        return head + (f"à la fermeture de {APP_NAME}, ce qui reste ici sera "
                       f"détruit définitivement{where}. Restaurez maintenant ce "
                       "que vous voulez garder.")
    if remote:
        return head + (f"à la fermeture, les {remote} élément(s) venus du NAS "
                       "seront détruits définitivement, les autres rejoindront "
                       "la corbeille de Windows. Restaurez maintenant ce que vous "
                       "voulez garder.")
    return head + ("le contenu ne partira vers la corbeille de Windows qu'à la "
                   "fermeture, et vous pourrez encore l'en sortir depuis "
                   "l'explorateur.")


class TrashDialog(QDialog):
    """Ce qui a été écarté pendant la session, et de quoi le remettre en place."""

    def __init__(self, trash, parent=None, mode: str | None = None):
        super().__init__(parent)
        self.trash = trash
        # Le sort de ce qui reste depend du reglage de suppression : celui de
        # la fenetre, a defaut de mieux.
        if mode is None:
            try:
                mode = parent.cfg["delete_mode"]
            except (AttributeError, KeyError, TypeError):
                mode = "recycle"
        self.mode = mode
        self.setWindowTitle("Corbeille de session")
        self.resize(760, 420)

        layout = QVBoxLayout(self)
        self.summary = QLabel("", self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("destTree")
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Élément", "Écarté à", "Emplacement d'origine"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        restore = QPushButton("Restaurer la sélection", self)
        restore.setObjectName("primary")
        restore_all = QPushButton("Tout restaurer", self)
        restore.clicked.connect(self.restore_selected)
        restore_all.clicked.connect(self.restore_all)
        buttons.addWidget(restore)
        buttons.addWidget(restore_all)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.actions_ = (restore, restore_all)

        box = QDialogButtonBox(QDialogButtonBox.Close, self)
        box.button(QDialogButtonBox.Close).setText("Fermer")
        box.rejected.connect(self.accept)
        layout.addWidget(box)

        # La restauration en cours, et une fermeture demandee pendant ce temps.
        self.worker: _Restorer | None = None
        self._close_when_done = False
        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        # En tete, ce qui emportait autre chose que des videos : c'est ce
        # qu'on ne doit pas laisser partir sans l'avoir vu.
        entries = sorted(reversed(self.trash.entries),
                         key=lambda entry: not getattr(entry, "others", 0))
        today = time.strftime("%Y%m%d")
        for entry in entries:
            name = entry.name
            others = getattr(entry, "others", 0)
            if others > 0:
                name += f"  ⚠ contient {others} autre(s) fichier(s)"
            elif others == -2:
                name += "  ⚠ lu en partie : peut-être d'autres fichiers"
            if getattr(entry, "adopted", False):
                name += "  (séance interrompue)"
            stamp = time.localtime(entry.at)
            origin = str(Path(entry.origin).parent)
            if getattr(entry, "guessed", False):
                origin += "  (origine devinée)"
            row = QTreeWidgetItem([
                name,
                time.strftime("%H:%M:%S" if time.strftime("%Y%m%d", stamp) == today
                              else "%d/%m %H:%M", stamp),
                origin,
            ])
            row.setData(0, Qt.UserRole, entry)
            self.tree.addTopLevelItem(row)
            # Vu ici : il suivra le sort des autres a la fermeture.
            entry.seen = True
        self.summary.setText(trash_fate(list(self.trash.entries), self.mode))

    def _restore(self, entries: list) -> None:
        """Remet les elements en place dans un fil a part.

        Chacun coute quatre ou cinq allers-retours avec le partage (existe-t-il,
        creer le dossier, renommer) : cinquante elements figeaient la fenetre
        plusieurs secondes. La boite reste vivante et dit ou elle en est ;
        elle ne se ferme qu'une fois le travail fini, pour que la fenetre
        principale relise un etat complet.
        """
        if self.worker is not None or not entries:
            return
        for button in self.actions_:
            button.setEnabled(False)
        self.summary.setText(f"Restauration de {len(entries)} élément(s)…")
        self.worker = _Restorer(self.trash, entries, self)
        self.worker.done.connect(self._restored)
        self.worker.start()

    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def _restored(self, failures: list) -> None:
        self.worker.wait()
        self.worker = None
        for button in self.actions_:
            button.setEnabled(True)
        self.refresh()
        if failures:
            QMessageBox.warning(
                self, "Restauration incomplète", "\n".join(failures[:6])
            )
        if self._close_when_done:
            self._close_when_done = False
            self.accept()

    def done(self, result) -> None:
        # Fermer pendant la restauration : on attend qu'elle finisse.
        if self.worker is not None:
            self._close_when_done = True
            return
        super().done(result)

    def restore_selected(self) -> None:
        entries = [row.data(0, Qt.UserRole) for row in self.tree.selectedItems()]
        if not entries:
            QMessageBox.information(
                self, "Rien à restaurer", "Sélectionnez d'abord une ou plusieurs lignes."
            )
            return
        self._restore(entries)

    def restore_all(self) -> None:
        self._restore(list(self.trash.entries))


GOLD = "#f5c542"


class FavoriteStar(QPushButton):
    """Une etoile : vide, ou doree quand l'element est en favori.

    Elle remplace les cinq etoiles de la note : on ne classait pas de 1 a 5,
    on voulait seulement retrouver ce qu'on aime. Elle garde l'interface de
    l'ancienne bande (`rated`, `value`, `set_value`) : 1 pour favori, 0 sinon.
    """

    rated = Signal(int)

    def __init__(self, size: int = 22, parent=None):
        super().__init__("", parent)
        self.value = 0
        self.setObjectName("favoriteStar")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(size + 16, size + 10)
        self.setIconSize(QSize(size, size))
        self.setStyleSheet("QPushButton#favoriteStar { background: transparent;"
                           " border: 0; }")
        self.clicked.connect(lambda: self.rated.emit(0 if self.value else 1))
        self.set_value(0)

    def set_value(self, value: int) -> None:
        self.value = 1 if int(value or 0) > 0 else 0
        self.setIcon(filled("star", GOLD) if self.value else icon("star", "#8b94a1"))
        self.setToolTip("Retirer des favoris   (touche 0)" if self.value
                        else "Mettre en favori   (touche 1)")


class PinButton(QPushButton):
    """L'epingle de la fiche : l'element passe en tete de sa liste (onglet
    Dossiers ou Videos). Grise, ou bleue quand il est epingle."""

    toggledPin = Signal()

    def __init__(self, size: int = 20, parent=None):
        super().__init__("", parent)
        self.value = False
        self.setObjectName("pinButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(size + 14, size + 10)
        self.setIconSize(QSize(size, size))
        self.setStyleSheet("QPushButton#pinButton { background: transparent; border: 0; }")
        self.clicked.connect(lambda _c=False: self.toggledPin.emit())
        self.set_value(False)

    def set_value(self, pinned: bool) -> None:
        self.value = bool(pinned)
        self.setIcon(icon("pin", "#6ea8ff" if self.value else "#8b94a1"))
        self.setToolTip("Désépingler" if self.value else
                        "Épingler : en tête de la liste, dans son onglet")


class KeyCap(QFrame):
    """Un raccourci et son effet — utilisable au clavier comme à la souris."""

    clicked = Signal()

    # Un simple garde-fou contre un libelle demesure. La vraie limite est la
    # largeur de la ligne : c'est la barre qui rogne, a la mesure de la place.
    # Un plafond fixe de dix-huit lettres coupait « Supprimer définit… » meme
    # avec mille pixels libres.
    LONGEST = 60

    def __init__(self, key: str, label: str, tone: str = "", parent=None,
                 key_only: bool = False):
        super().__init__(parent)
        self.setObjectName("keycap")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{label}   (touche {key})")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 6, 11, 6)
        layout.setSpacing(8)
        key_label = QLabel(key, self)
        key_label.setObjectName("keyLetter")
        # La touche seule quand elle dit deja tout (« Suppr ») : le libelle
        # ne reste qu'en infobulle.
        self.full = "" if key_only else elide(label, self.LONGEST)
        text = QLabel(self.full, self)
        text.setObjectName("keyLabel")
        text.setVisible(not key_only)
        layout.addWidget(key_label)
        layout.addWidget(text)
        self.text = text
        self._layout = layout
        # Sans cela, un clic tombant sur le texte n'atteindrait pas la vignette.
        for child in (key_label, text):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if tone == "danger":
            key_label.setStyleSheet("color: #ff8a8a;")
        elif tone == "neutral":
            key_label.setStyleSheet("color: #8fd0ff;")

    def set_compact(self, on: bool) -> None:
        """La touche seule, son effet en infobulle : quand la ligne manque de
        place, on resserre plutot que d'ouvrir une deuxieme rangee."""
        self.text.setVisible(not on and bool(self.full))
        self._layout.setContentsMargins(*((8, 4, 8, 4) if on else (9, 4, 11, 4)))

    def label_width(self) -> int:
        """Largeur du libelle entier, en pixels."""
        return self.text.fontMetrics().horizontalAdvance(self.full)

    def set_room(self, width: int | None) -> None:
        """Le libelle entier (None), ou rogne pour tenir en `width` pixels."""
        shown = self.full if width is None else self.text.fontMetrics().elidedText(
            self.full, Qt.ElideRight, max(0, int(width)))
        if self.text.text() != shown:
            self.text.setText(shown)

    def mouseReleaseEvent(self, event):
        # Relâcher en dehors annule le clic, comme sur un vrai bouton.
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class FlowLayout(QLayout):
    """Dispose les éléments de gauche à droite, en passant à la ligne.

    Nécessaire pour la barre de commandes : le nombre de destinations n'est pas
    borné, une seule rangée finirait par déborder de la fenêtre.
    """

    def __init__(self, parent=None, spacing: int = 8):
        super().__init__(parent)
        self._items: list = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def insert_widget(self, index: int, widget) -> None:
        """Pose un element a ce rang (en tete : 0)."""
        from PySide6.QtWidgets import QWidgetItem
        self.addChildWidget(widget)
        self._items.insert(max(0, min(index, len(self._items))), QWidgetItem(widget))
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    def _arrange(self, rect, apply: bool) -> int:
        """Deux passes : former les rangees, puis y centrer chaque element.

        Un element cache ne compte plus : il ajoutait quand meme son ecart, et
        la ligne des filtres avait des trous de vingt a quarante pixels selon
        l'onglet. Et chaque element est centre sur la hauteur de sa rangee,
        au lieu d'etre colle en haut, ou des hauteurs de 17 a 31 pixels
        donnaient une ligne en escalier.
        """
        margins = self.contentsMargins()
        left = rect.x() + margins.left()
        right = rect.right() - margins.right()
        rows, row, x = [], [], left
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if row and x + hint.width() > right:
                rows.append(row)
                row, x = [], left
            row.append((item, hint))
            x += hint.width() + self.spacing()
        if row:
            rows.append(row)
        y = rect.y() + margins.top()
        for index, row in enumerate(rows):
            if index:
                y += self.spacing()
            height = max(hint.height() for _item, hint in row)
            if apply:
                x = left
                for item, hint in row:
                    item.setGeometry(QRect(QPoint(x, y + (height - hint.height()) // 2),
                                           hint))
                    x += hint.width() + self.spacing()
            y += height
        return y + margins.bottom() - rect.y()


class CommandBar(QWidget):
    """Bandeau des actions disponibles : rappel des touches, et boutons cliquables."""

    deleteRequested = Signal()
    skipRequested = Signal()
    moveRequested = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Une seule ligne, toujours. Elle ne dicte pas sa largeur a la
        # fenetre : quand la place manque, les vignettes se resserrent.
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(6)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.compact = False

    def rebuild(self, destinations: list, delete_label: str) -> None:
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

        # « Supprimer », court et en gris ; le detail (corbeille, NAS) en
        # infobulle -- le libelle long debordait de la ligne.
        delete_cap = KeyCap("Suppr", "Supprimer", "danger")
        if delete_label:
            delete_cap.setToolTip(f"Supprimer : corbeille de session, puis "
                                  f"{delete_label}   (touche Suppr)")
        delete_cap.clicked.connect(self.deleteRequested)
        self.layout_.addWidget(delete_cap)

        # Espace : la suivante, sans rien decider -- la meme touche pour une
        # video et pour un dossier.
        skip_cap = KeyCap("Espace", "Suivante", "neutral")
        skip_cap.clicked.connect(self.skipRequested)
        self.layout_.addWidget(skip_cap)

        # Les notes n'ont plus leurs cinq vignettes : les etoiles, a droite,
        # et les touches 1 a 5 font la meme chose sur une ligne de moins.
        for dest in destinations:
            cap = KeyCap(
                dest.get("key", "?").upper(),
                dest.get("label") or Path(dest["path"]).name,
            )
            # dict(dest) fige la destination : sans copie, toutes les vignettes
            # partageraient la dernière du tour de boucle.
            cap.clicked.connect(
                lambda checked=False, d=dict(dest): self.moveRequested.emit(d)
            )
            self.layout_.addWidget(cap)
        self.layout_.addStretch(1)
        self.compact = False
        self._fit()
        self.updateGeometry()

    def caps(self) -> list:
        return [self.layout_.itemAt(i).widget()
                for i in range(self.layout_.count())
                if self.layout_.itemAt(i).widget() is not None]

    def _fit(self) -> None:
        """Les libelles entiers si la ligne le permet. Sinon, les plus longs
        se rognent d'abord, a la mesure de la place qui manque ; la touche
        seule ne vient qu'en dernier recours, quand meme trois lettres ne
        tiendraient plus. C'etait tout ou rien : des libelles entiers
        auraient fait passer toute la ligne aux touches seules bien plus tot."""
        caps = self.caps()
        if not caps:
            return
        for cap in caps:
            cap.ensurePolished()
            cap.set_compact(False)
            cap.set_room(None)
        spacing = self.layout_.spacing()
        room = max(1, self.width())
        hints = [cap.sizeHint().width() + spacing for cap in caps]
        self.compact = False
        if sum(hints) <= room:
            return
        labels = [cap.label_width() for cap in caps]
        # Ce que les vignettes occupent hors libelle : touche, marges, ecarts ;
        # un pixel de marge chacune pour les arrondis de mesure.
        frame = sum(hint - label for hint, label in zip(hints, labels)) + len(caps)
        floor = caps[0].text.fontMetrics().horizontalAdvance("Abc…")
        level = _water_level(labels, room - frame, floor)
        if level is None:
            self.compact = True
            for cap in caps:
                cap.set_compact(True)
            return
        for cap, label in zip(caps, labels):
            cap.set_room(level if label > level else None)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())


def _water_level(widths: list, budget: int, floor: int):
    """Le plus haut plafond (au moins `floor`) qui fait tenir `widths` dans
    `budget` une fois chacune ramenee a ce plafond ; None s'il n'y en a pas.

    Seules les plus longues sont rognees, et toutes a la meme longueur : un
    nom court reste entier tant qu'un long peut ceder.
    """
    def used(level: int) -> int:
        return sum(min(width, level) for width in widths)

    if not widths or used(floor) > budget:
        return None
    low, high = floor, max(max(widths), floor)
    while low < high:
        middle = (low + high + 1) // 2
        if used(middle) <= budget:
            low = middle
        else:
            high = middle - 1
    return low


class FolderPicker(QDialog):
    """Choisir plusieurs dossiers d'un coup : Ctrl ou Maj, puis « Ajouter ».

    Le selecteur de Qt, force en selection multiple, grisait son bouton des
    que deux dossiers etaient choisis -- il ne sait en valider qu'un -- et
    « Ajouter des dossiers » ne faisait rien. Celui-ci ne montre que des
    dossiers, s'ouvre ou l'on veut (un chemin se colle en haut), et rend tous
    ceux qu'on a selectionnes.
    """

    def __init__(self, parent=None, caption: str = "", start: str = ""):
        from PySide6.QtWidgets import QFileSystemModel
        from PySide6.QtCore import QDir
        super().__init__(parent)
        self.setWindowTitle(caption or "Choisir des dossiers")
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.up = QPushButton("↑", self)
        self.up.setToolTip("Dossier parent")
        self.up.setFixedWidth(36)
        self.up.clicked.connect(self._go_up)
        row.addWidget(self.up)
        self.where = QLineEdit(self)
        self.where.setPlaceholderText(r"Un chemin : C:\Vidéos, \\NAS\partage…")
        self.where.returnPressed.connect(lambda: self.open_folder(self.where.text()))
        row.addWidget(self.where, 1)
        layout.addLayout(row)
        # Les racines de Prisme, d'un clic : un partage reseau (le NAS)
        # n'apparait jamais parmi les lecteurs, et l'on ne pouvait pas y aller.
        places = QHBoxLayout()
        places.setSpacing(6)
        for place in _picker_places():
            button = QPushButton("⌂ " + place[1], self)
            button.setToolTip(place[0])
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda _c=False, p=place[0]: self.open_folder(p))
            places.addWidget(button)
        drives = QPushButton("Lecteurs", self)
        drives.setToolTip("La liste des lecteurs de ce PC")
        drives.setFocusPolicy(Qt.NoFocus)
        drives.clicked.connect(lambda: self.open_folder(""))
        places.addWidget(drives)
        places.addStretch(1)
        layout.addLayout(places)
        hint = QLabel("Double-clic : entrer dans un dossier.  Ctrl ou Maj : en "
                      "sélectionner plusieurs.", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Drives)
        self.model.setRootPath("")
        self.view = QTreeView(self)
        self.view.setModel(self.model)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setUniformRowHeights(True)
        self.view.setSortingEnabled(True)
        self.view.sortByColumn(0, Qt.AscendingOrder)
        for column in (1, 2, 3):
            self.view.hideColumn(column)
        self.view.header().hide()
        self.view.doubleClicked.connect(
            lambda index: self.open_folder(self.model.filePath(index)))
        layout.addWidget(self.view, 1)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.ok = box.button(QDialogButtonBox.Ok)
        box.button(QDialogButtonBox.Cancel).setText("Annuler")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.view.selectionModel().selectionChanged.connect(lambda *_a: self._name_ok())
        from . import roots
        if roots.is_union(start):
            # « Toutes les racines » n'est pas un dossier : la premiere qui
            # repond. Le selecteur retombait sinon sur la liste des lecteurs.
            members = roots.members(start) or roots.roots()
            start = str(members[0]) if members else ""
        self.open_folder(start or str(Path.home()))

    def _name_ok(self) -> None:
        """Le bouton dit ce qu'il ajoutera : sans selection, le dossier ouvert
        lui-meme. « Ajouter la sélection » ne faisait rien, sans un mot, quand
        on etait entre dans le dossier voulu sans rien selectionner."""
        count = len(self.view.selectionModel().selectedRows(0))
        here = self.where.text().strip()
        if count:
            self.ok.setText(f"Ajouter {count} dossier{'s' if count > 1 else ''}")
            self.ok.setEnabled(True)
        else:
            self.ok.setText(f"Ajouter « {Path(here).name or here} »" if here else "Ajouter")
            self.ok.setEnabled(bool(here) and Path(here).is_dir())

    def open_folder(self, folder: str) -> None:
        folder = (folder or "").strip().strip('"')
        if folder and not Path(folder).is_dir():
            self.where.setText(folder)
            return
        index = self.model.index(folder) if folder else self.model.index("")
        self.view.setRootIndex(index)
        self.where.setText(folder)
        self.view.clearSelection()
        self._name_ok()

    def _go_up(self) -> None:
        current = self.where.text().strip()
        if not current:
            return
        parent = str(Path(current).parent)
        # Au sommet d'un lecteur, on remonte a la liste des lecteurs.
        self.open_folder("" if parent == current else parent)

    def chosen(self) -> list:
        rows = self.view.selectionModel().selectedRows(0)
        found = [Path(self.model.filePath(index)) for index in rows]
        if not found and self.where.text().strip():
            found = [Path(self.where.text().strip())]   # le dossier ouvert lui-meme
        return sorted({path for path in found if path.is_dir()},
                      key=lambda path: str(path).lower())


def _picker_places() -> list:
    """(chemin, nom court) des racines de Prisme qu'on peut ouvrir."""
    from . import roots
    found, seen = [], set()
    for root in roots.roots():
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            found.append((str(root), roots.label(root)))
    return found


def pick_folders(parent, caption: str, start: str = "") -> list:
    """Un ou plusieurs dossiers, choisis d'un coup (`FolderPicker`)."""
    dialog = FolderPicker(parent, caption, start)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return []
    return dialog.chosen()


class DestinationsDialog(QDialog):
    """Gestion des dossiers de destination : touche, libellé et ordre d'affichage."""

    COL_GRIP, COL_KEY, COL_LABEL, COL_PATH = range(4)

    def __init__(self, destinations: list, parent=None, start: str = ""):
        super().__init__(parent)
        # Ou s'ouvre le choix des dossiers : la racine en cours, et non le
        # dossier personnel, ou le NAS n'apparait pas.
        self.start = start
        self.setWindowTitle("Dossiers de destination")
        self.resize(820, 480)

        layout = QVBoxLayout(self)
        # Repliee, comme celle des mots-cles : la phrase d'une traite
        # elargissait la fenetre au-dela de ses 820 pixels.
        self.intro = QLabel(
            "Chaque destination est déclenchée par sa touche pendant le tri. "
            "Double-cliquez une cellule pour la modifier, et glissez une ligne "
            "par sa poignée pour changer l'ordre des boutons.", self)
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)

        self.tree = QTreeWidget(self)
        self.tree.setObjectName("destTree")
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["", "Touche", "Libellé", "Dossier"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        # InternalMove sur un QTreeWidget deplace la ligne entiere, contrairement
        # a un QTableWidget qui deplacerait les cellules une a une.
        self.tree.setDragDropMode(QAbstractItemView.InternalMove)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        header = self.tree.header()
        header.setSectionResizeMode(self.COL_GRIP, QHeaderView.Fixed)
        header.resizeSection(self.COL_GRIP, 28)
        header.setSectionResizeMode(self.COL_KEY, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_LABEL, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_PATH, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        buttons = QHBoxLayout()
        add = QPushButton("Ajouter des dossiers…")
        add.setObjectName("primary")
        add.setToolTip("Ctrl ou Maj pour en sélectionner plusieurs d'un coup")
        remove = QPushButton("Retirer")
        renumber = QPushButton("Renuméroter")
        renumber.setToolTip("Réattribue les touches dans l'ordre de la liste")
        reset = QPushButton("Réinitialiser")
        reset.setObjectName("danger")
        add.clicked.connect(self.add_folders)
        remove.clicked.connect(self.remove_selected)
        renumber.clicked.connect(self.renumber)
        reset.clicked.connect(self.reset_all)
        for button in (add, remove, renumber):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(reset)
        layout.addLayout(buttons)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        # Qt libelle ces boutons en anglais faute de traduction installee.
        box.button(QDialogButtonBox.Ok).setText("Enregistrer")
        box.button(QDialogButtonBox.Cancel).setText("Annuler")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self.set_destinations(destinations)

    # -- contenu ---------------------------------------------------------
    def set_destinations(self, destinations: list) -> None:
        self.tree.clear()
        for dest in destinations:
            self._append_row(
                dest.get("key", ""),
                dest.get("label", "") or Path(dest.get("path", "")).name,
                dest.get("path", ""),
            )

    def _append_row(self, key: str, label: str, path: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(["⠿", key, label, path])
        item.setFlags(
            Qt.ItemIsEnabled | Qt.ItemIsSelectable
            | Qt.ItemIsEditable | Qt.ItemIsDragEnabled
        )
        item.setToolTip(self.COL_GRIP, "Glissez pour déplacer cette ligne")
        item.setToolTip(self.COL_PATH, path)
        self.tree.addTopLevelItem(item)
        return item

    def _rows(self) -> list:
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def _used_keys(self) -> set:
        return {row.text(self.COL_KEY).strip().lower() for row in self._rows()}

    def _free_key(self, taken: set) -> str:
        for key in KEY_ORDER:
            if key not in taken and key not in RESERVED_KEYS:
                return key
        return ""

    def _add_paths(self, paths: list) -> int:
        existing = {row.text(self.COL_PATH) for row in self._rows()}
        taken = self._used_keys()
        added = 0
        for path in paths:
            if str(path) in existing:
                continue
            key = self._free_key(taken)
            if key:
                taken.add(key)
            self._append_row(key, path.name, str(path))
            existing.add(str(path))
            added += 1
        return added

    # -- actions ---------------------------------------------------------
    def add_folders(self) -> None:
        start = self.start
        rows = self._rows()
        if rows:
            start = str(Path(rows[-1].text(self.COL_PATH)).parent)
        chosen = pick_folders(self, "Choisir un ou plusieurs dossiers", start)
        if not chosen:
            return
        added = self._add_paths(chosen)
        if added < len(chosen):
            QMessageBox.information(
                self, "Doublons ignorés",
                f"{len(chosen) - added} dossier(s) figuraient déjà dans la liste.",
            )

    def add_children_of(self) -> None:
        parent = QFileDialog.getExistingDirectory(
            self, "Dossier contenant les destinations", self.start
        )
        if parent:
            self._add_children(parent)

    def _add_children(self, parent: str) -> int:
        """Ajoute chaque sous-dossier de `parent` ; rend le nombre ajoute."""
        # Une seule lecture du dossier : scandir rapporte deja la nature de
        # chaque entree, la ou is_dir() interrogeait le partage une fois par
        # sous-dossier — trois cents allers-retours pour trois cents
        # destinations, la fenetre figee pendant ce temps.
        try:
            with os.scandir(parent) as entries:
                children = sorted(
                    (Path(entry.path) for entry in entries if entry.is_dir()),
                    key=lambda p: p.name.lower(),
                )
        except OSError as exc:
            QMessageBox.warning(self, "Dossier illisible", str(exc))
            return 0
        if not children:
            QMessageBox.information(
                self, "Rien à ajouter", "Ce dossier ne contient aucun sous-dossier."
            )
            return 0
        return self._add_paths(children)

    def remove_selected(self) -> None:
        for item in self.tree.selectedItems():
            index = self.tree.indexOfTopLevelItem(item)
            if index >= 0:
                self.tree.takeTopLevelItem(index)

    def renumber(self) -> None:
        """Réattribue les touches en suivant l'ordre affiché."""
        available = [key for key in KEY_ORDER if key not in RESERVED_KEYS]
        for position, row in enumerate(self._rows()):
            row.setText(self.COL_KEY, available[position] if position < len(available) else "")

    def reset_all(self) -> None:
        if not self.tree.topLevelItemCount():
            return
        confirm = QMessageBox.question(
            self, "Réinitialiser",
            "Retirer les "
            f"{self.tree.topLevelItemCount()} destinations et repartir de zéro ?\n\n"
            "Les dossiers eux-mêmes ne sont pas touchés.",
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self.tree.clear()

    # -- resultat --------------------------------------------------------
    def result_destinations(self) -> list:
        """Relit la liste dans son ordre d'affichage, en signalant les rejets."""
        out = []
        seen = set()
        rejected = []
        for row in self._rows():
            path = row.text(self.COL_PATH).strip()
            if not path:
                continue
            label = row.text(self.COL_LABEL).strip() or Path(path).name
            key = row.text(self.COL_KEY).strip()[:1].lower()

            if not key:
                rejected.append(f"{label} : aucune touche")
                continue
            if key in RESERVED_KEYS:
                rejected.append(
                    f"{label} : la touche « {key} » sert à noter"
                )
                continue
            if key in seen:
                rejected.append(f"{label} : la touche « {key} » est déjà prise")
                continue
            seen.add(key)
            out.append({"key": key, "label": label, "path": path})

        if rejected:
            QMessageBox.warning(
                self, "Destinations ignorées",
                "Ces destinations n'ont pas été enregistrées :\n\n  · "
                + "\n  · ".join(rejected)
                + "\n\nChaque destination a besoin d'une touche qui lui soit propre : "
                  "un chiffre ou une lettre. « Renuméroter » s'en charge d'un coup.",
            )
        return out

