"""Composants d'interface : grille d'aperçus, lecteur, barre de commandes, réglages."""
from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import (
    QPoint, QPointF, QRect, QSize, QTimer, QUrl, Qt, Signal,
)
from PySide6.QtGui import (
    QColor, QCursor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
    QRegion,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLayout,
    QListView, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
    QTreeView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .actions import ActionError
from .icons import GLYPHS, dress, icon
from .config import KEY_ORDER, RESERVED_KEYS
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


def seek_step(event, seconds: int) -> int:
    """Convertit un cran de molette en déplacement, en millisecondes.

    Ctrl amplifie le pas pour traverser rapidement une longue vidéo.
    """
    notches = event.angleDelta().y() / 120.0
    if not notches:
        notches = event.angleDelta().x() / 120.0
    factor = 6 if event.modifiers() & Qt.ControlModifier else 1
    return int(notches * seconds * 1000 * factor)


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
        if self.duration:
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
        self.tiles[slot].set_hovered(True)
        self._play_slot(slot)

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

    def _play_slot(self, slot: int) -> None:
        tile = self.tiles[slot]
        if not tile.video:
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
        self.marks.fraction = fraction
        self.marks.place_on(self, self.video.geometry())
        self.marks._lay_out()

    def _on_error(self, *_args) -> None:
        if 0 <= self.hovered_slot < len(self.tiles):
            video = self.tiles[self.hovered_slot].video
            if video:
                self.unplayable.add(video)
        self._blank()
        self.player.stop()

    def wheelEvent(self, event):
        """La molette avance ou recule dans l'extrait survolé."""
        if self.hovered_slot == -1 or not self.player.source().isValid():
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
        self.player.stop()
        self._blank()
        self.marks.clear()
        for tile in self.tiles:
            tile.set_hovered(False)

    def mouseReleaseEvent(self, event):
        """Un clic sur une case ouvre cette vidéo, comme partout ailleurs.

        Il fallait un double-clic, alors qu'une carte de la planche s'ouvre d'un
        seul : le même geste donnait deux résultats selon l'endroit.
        """
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        slot = self._slot_at(event.position().toPoint())
        if slot >= 0 and self.tiles[slot].video:
            self.playRequested.emit(self.tiles[slot].video, self.tiles[slot].ts)
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        # Le premier clic a deja ouvert : ne pas rouvrir par-dessus.
        event.accept()


PEEK_STYLE = """
QWidget#peekOverlay { background: rgba(8, 10, 13, 235); }
QFrame#peekCell { background: #0e1116; border: 1px solid #242a33; border-radius: 6px; }
QLabel#peekImage { color: #6f7885; }
QLabel#peekCaption { color: #e9eef4; font-size: 12px; font-weight: 600; }
"""


def app_icon(size: int = 256) -> QIcon:
    """Le prisme : un rai entre, un triangle le decompose, trois rais sortent.

    Dessinee plutot que chargee : pas de fichier a livrer, et l'icone reste
    nette a toutes les tailles que Windows demande.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0

    # Le fond : un carre sombre aux coins arrondis, comme les autres icones
    # de la barre des taches.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#11151b"))
    painter.drawRoundedRect(0, 0, size, size, 12 * s, 12 * s)

    # Le rai qui entre, blanc, par la gauche.
    painter.setPen(QPen(QColor("#e9eef4"), 3 * s, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(int(6 * s), int(30 * s), int(24 * s), int(30 * s))

    # Les trois rais qui sortent, ecartes en eventail.
    for color, dy in (("#e2645c", -9), ("#d8c05a", 0), ("#5aa9d8", 9)):
        painter.setPen(QPen(QColor(color), 3 * s, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(int(40 * s), int(32 * s),
                         int(58 * s), int((36 + dy) * s))

    # Le prisme lui-meme : un triangle clair, pose sur la pointe du haut.
    triangle = QPolygonF([QPointF(32 * s, 12 * s), QPointF(48 * s, 46 * s),
                          QPointF(16 * s, 46 * s)])
    painter.setPen(QPen(QColor("#8b94a1"), 2.5 * s, Qt.SolidLine,
                        Qt.SquareCap, Qt.RoundJoin))
    painter.setBrush(QColor(233, 238, 244, 26))
    painter.drawPolygon(triangle)
    painter.end()
    return QIcon(pixmap)


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
QWidget#overBar { background: rgba(8, 10, 13, 216); border-radius: 8px; }
QLabel#overName { color: #e9eef4; font-size: 12px; }
QLabel#overLeft { color: rgba(255,255,255,0.78); font-size: 12px;
                  font-weight: 500; }
QFrame#overRail { background: rgba(255,255,255,0.20); border-radius: 2px; }
QFrame#overDone { background: #e9eef4; border-radius: 2px; }
QPushButton#overGesture { background: transparent; border: 0; padding: 0;
                          color: #dbe2ea; font-size: 20px; }
QPushButton#overGesture:hover { color: #ffffff; }
"""


MARKS_STYLE = """
QLabel#marksLeft { color: #ffffff; background: rgba(8, 10, 13, 200);
                   border-radius: 5px; padding: 1px 7px; font-size: 12px;
                   font-weight: 600; }
QFrame#marksRail { background: rgba(255, 255, 255, 60); }
QFrame#marksDone { background: #e9eef4; }
"""


class PlayMarks(QWidget):
    """Ou en est la lecture, pose **sur** l'image : un trait tres fin tout en
    bas, et, si on le demande, le temps restant en haut a droite.

    La meme regle partout — fiche, apercus d'un dossier, planche, mur : un
    trait discret toujours present, le detail au survol. Le widget video de
    Windows se dessine par-dessus ses voisins ; seule une fenetre-outil passe
    devant. Elle ne recoit aucun clic : ils vont a l'image, dessous.
    """

    RAIL = 3

    def __init__(self, parent=None, rail: bool = True, left: bool = True):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(MARKS_STYLE)
        self.with_rail = rail
        self.with_left = left
        self.fraction = 0.0
        self.rail = QFrame(self)
        self.rail.setObjectName("marksRail")
        self.done = QFrame(self.rail)
        self.done.setObjectName("marksDone")
        self.left = QLabel("", self)
        self.left.setObjectName("marksLeft")
        self.left.hide()
        self.hide()

    def set_progress(self, position: int, duration: int) -> None:
        """Position et duree en millisecondes."""
        self.fraction = (max(0.0, min(1.0, position / duration))
                         if duration > 0 else 0.0)
        self.left.setText(
            f"−{human_duration(max(0, duration - position) / 1000.0)}"
            if duration > 0 else "")
        self._lay_out()

    def clear(self) -> None:
        self.fraction = 0.0
        self.left.setText("")
        self.hide()

    def place_on(self, widget, rect=None) -> None:
        """Se pose sur `rect` (coordonnees de `widget`, tout le widget par
        defaut). Rien si le widget n'est pas a l'ecran."""
        if widget is None or not widget.isVisible():
            return self.hide()
        rect = widget.rect() if rect is None else rect
        corner = widget.mapToGlobal(rect.topLeft())
        wanted = QRect(corner.x(), corner.y(), rect.width(), rect.height())
        if self.geometry() != wanted:
            self.setGeometry(wanted)
            self._lay_out()
        if self.isHidden():
            self.show()
        self.raise_()

    def _lay_out(self) -> None:
        width, height = self.width(), self.height()
        region = QRegion()
        self.rail.setVisible(self.with_rail)
        if self.with_rail:
            self.rail.setGeometry(0, height - self.RAIL, width, self.RAIL)
            self.done.setGeometry(0, 0, int(width * self.fraction), self.RAIL)
            region = region.united(QRegion(self.rail.geometry()))
        shown = self.with_left and bool(self.left.text())
        self.left.setVisible(shown)
        if shown:
            self.left.adjustSize()
            self.left.move(width - self.left.width() - 8, 8)
            region = region.united(QRegion(self.left.geometry()))
        # Une region vide ote le masque : on garde alors au moins un point.
        self.setMask(region if not region.isEmpty() else QRegion(0, 0, 1, 1))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._lay_out()


class OverBar(QWidget):
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
        self.left = QLabel("", self.skin)
        self.left.setObjectName("overLeft")
        top.addWidget(self.left, 0)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(2)
        top.addLayout(self.buttons)
        box.addLayout(top)

        self.rail = QFrame(self.skin)
        self.rail.setObjectName("overRail")
        self.rail.setFixedHeight(self.RAIL)
        self.done = QFrame(self.rail)
        self.done.setObjectName("overDone")
        self.done.setGeometry(0, 0, 0, self.RAIL)
        box.addWidget(self.rail)
        self.hide()

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
        button.clicked.connect(slot)
        self.buttons.addWidget(button)

    def set_name(self, text: str) -> None:
        self.name.setText(elide(text, 60))
        self.name.setToolTip(text)

    def set_progress(self, position: int, duration: int) -> None:
        fraction = (position / duration) if duration > 0 else 0.0
        width = max(0, self.rail.width())
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
        width = max(180, target.width() - 16)
        height = self.sizeHint().height()
        self.setGeometry(corner.x() + 8,
                         corner.y() + target.height() - height - 8,
                         width, height)


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
        self.audio = QAudioOutput(area)
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

    # Cinq reperes suffisent a se reperer dans une video : un cinquieme, deux
    # cinquiemes, et ainsi de suite. Dix prenaient deux fois plus de place pour
    # une precision dont on ne fait rien — on survole pour chercher, on ne
    # compte pas les images.
    STRIP_COUNT = 5

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

        # Le lecteur est posé dans un cadre qui le rogne : agrandir sa géométrie
        # au-delà du cadre produit un zoom, sans passer par une scène graphique.
        self.video_area = QWidget(top)
        self.video_area.setObjectName("videoArea")
        # Assez pour voir, assez peu pour tenir sur un ecran agrandi.
        self.video_area.setMinimumHeight(180)
        self.decks = [_Deck(self.video_area), _Deck(self.video_area)]
        self._active = 0
        self._muted = False
        self.peek = PeekOverlay(self.video_area)
        self.peeking = False
        # Le widget video de Windows avale les clics : on lui prend ses gestes
        # a la source, comme la planche le fait deja.
        for deck in self.decks:
            deck.video.mousePressEvent = self._scrub_press
            deck.video.mouseMoveEvent = self._scrub_move
            deck.video.mouseReleaseEvent = self._scrub_release
        self._scrub_x0 = None
        self._scrub_pos0 = 0
        self._scrubbing = False
        self._zoomed_while_held = False
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
            tile.strip_mode = True
            tile.duration_chip.hide()
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

    @property
    def audio(self):
        return self.decks[self._active].audio

    @property
    def spare(self):
        return self.decks[1 - self._active]

    def preload(self, path: str) -> None:
        """Charge `path` dans la reserve, jusqu'a sa premiere image, puis attend."""
        spare = self.spare
        if spare.path == path:
            return
        spare.path = path
        spare.primed = False
        spare.audio.setMuted(True)
        spare.video.hide()
        spare.player.setSource(QUrl.fromLocalFile(path))
        spare.player.play()

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
        """Glisser sur l'image : toute la largeur vaut toute la duree."""
        if self._scrub_x0 is None or not (event.buttons() & Qt.LeftButton):
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
        if was_click:
            # Un clic sans glisser : pause ou reprise, comme sur le mur.
            self.toggle_pause()
        event.accept()

    def release(self) -> None:
        """Lache les deux fichiers : avant de deplacer ou supprimer."""
        for deck in self.decks:
            deck.clear()

    def showEvent(self, event):
        super().showEvent(event)
        self.hover_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.hover_timer.stop()
        self.marks.hide()
        self.stop()

    def hide_strip(self) -> None:
        """Retire la pellicule : tout l'espace revient a l'image."""
        self.strip.hide()

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self.audio.setMuted(muted)

    def set_item(self, path: str, message: str = "…") -> None:
        for tile in self.tiles:
            tile.reset()
            tile.placeholder.setText(message)
        spare = self.spare
        if spare.path == path:
            # La suivante etait deja prete : on echange, sans rien attendre.
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
        if not self.isVisible() or not self.window().isActiveWindow():
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
        if event.buttons() & Qt.LeftButton or event.modifiers() & Qt.ControlModifier:
            self._zoomed_while_held = True
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
        self.zoom = max(1.0, min(6.0, self.zoom * (1.25 ** notches)))
        self._apply_zoom()
        self.position_label.setText(f"×{self.zoom:.1f}" if self.zoom > 1 else "×1")
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
        self.peek.setGeometry(area)

    def reset_zoom(self) -> None:
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

    def toggle_pause(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self) -> None:
        self.player.stop()
        # Une reserve qui continue de tenir un fichier pendant qu'on fait
        # autre chose n'a plus de sens ; on la lache.
        self.spare.clear()


class TagsDialog(QDialog):
    """Mots-cles automatiques : un par ligne, mot ou expression.

    Chaque mot devient un dossier virtuel rassemblant les videos dont le nom le
    comporte. Ce sont des vues, non des rangements : elles ne se deplacent pas,
    mais on edite normalement les videos qu'elles reunissent.
    """

    def __init__(self, tags: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mots-cles automatiques")
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Un mot ou une expression par ligne. Chaque ligne devient un dossier "
            "virtuel reunissant les videos dont le nom la comporte, ou qu'elle "
            "soit rangee. La casse et les accents sont ignores."
        ))
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


class TrashDialog(QDialog):
    """Ce qui a été écarté pendant la session, et de quoi le remettre en place."""

    def __init__(self, trash, parent=None):
        super().__init__(parent)
        self.trash = trash
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

        box = QDialogButtonBox(QDialogButtonBox.Close, self)
        box.button(QDialogButtonBox.Close).setText("Fermer")
        box.rejected.connect(self.accept)
        layout.addWidget(box)

        self.refresh()

    def refresh(self) -> None:
        self.tree.clear()
        for entry in reversed(self.trash.entries):
            row = QTreeWidgetItem([
                entry.name,
                time.strftime("%H:%M:%S", time.localtime(entry.at)),
                str(Path(entry.origin).parent),
            ])
            row.setData(0, Qt.UserRole, entry)
            self.tree.addTopLevelItem(row)
        count = self.trash.count
        self.summary.setText(
            f"{count} élément(s) écartés. Rien n'est encore supprimé : le contenu "
            "ne partira vers la corbeille de Windows qu'à la fermeture, et vous "
            "pourrez encore l'en sortir depuis l'explorateur."
            if count else "La corbeille de session est vide."
        )

    def _restore(self, entries: list) -> None:
        failures = []
        for entry in entries:
            try:
                self.trash.restore(entry)
            except ActionError as exc:
                failures.append(str(exc))
        self.refresh()
        if failures:
            QMessageBox.warning(
                self, "Restauration incomplète", "\n".join(failures[:6])
            )

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


class StarStrip(QWidget):
    """Cinq étoiles cliquables : survoler montre la note, cliquer la pose.

    Rappuyer sur l'étoile déjà atteinte efface la note, ce qui évite un bouton
    « remettre à zéro » de plus.
    """

    rated = Signal(int)

    def __init__(self, size: int = 20, parent=None):
        super().__init__(parent)
        self.star_size = size
        self.value = 0
        self.preview = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(size * 5 + 8, size + 4)
        self.setToolTip("Noter de 1 à 5 étoiles   (touches 1 à 5, 0 pour effacer)")

    def set_value(self, value: int) -> None:
        self.value = max(0, min(5, int(value)))
        self.update()

    def _index_at(self, x: int) -> int:
        return max(0, min(4, (x - 4) // self.star_size))

    def mouseMoveEvent(self, event):
        self.preview = self._index_at(event.position().toPoint().x())
        self.update()

    def leaveEvent(self, event):
        self.preview = -1
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.rated.emit(self._index_at(event.position().toPoint().x()) + 1)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        shown = (self.preview + 1) if self.preview >= 0 else self.value
        drawn = self.value if not self.isEnabled() else 5
        for index in range(drawn):
            filled = index < shown
            if self.preview >= 0 and index < shown:
                colour = QColor("#ffd479")
            elif filled:
                colour = QColor("#e0a53d")
            else:
                colour = QColor("#3a4150")
            painter.setPen(Qt.NoPen)
            painter.setBrush(colour)
            painter.drawPolygon(self._star(index))
        painter.end()

    def _star(self, index: int) -> QPolygonF:
        size = self.star_size
        cx = 4 + index * size + size / 2
        cy = self.height() / 2
        radius = size * 0.42
        points = []
        for step in range(10):
            angle = math.pi / 2 + step * math.pi / 5
            length = radius if step % 2 == 0 else radius * 0.45
            points.append(QPointF(cx + length * math.cos(angle),
                                  cy - length * math.sin(angle)))
        return QPolygonF(points)


class KeyCap(QFrame):
    """Un raccourci et son effet — utilisable au clavier comme à la souris."""

    clicked = Signal()

    def __init__(self, key: str, label: str, tone: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("keycap")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{label}   (touche {key})")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 6, 11, 6)
        layout.setSpacing(8)
        key_label = QLabel(key, self)
        key_label.setObjectName("keyLetter")
        text = QLabel(elide(label, 18), self)
        text.setObjectName("keyLabel")
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
        self.text.setVisible(not on)
        self._layout.setContentsMargins(*((8, 4, 8, 4) if on else (9, 4, 11, 4)))

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
        margins = self.contentsMargins()
        left = rect.x() + margins.left()
        right = rect.right() - margins.right()
        x, y = left, rect.y() + margins.top()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            if x > left and x + hint.width() > right:
                x = left
                y += line_height + self.spacing()
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line_height = max(line_height, hint.height())
        return y + line_height + margins.bottom() - rect.y()


class CommandBar(QWidget):
    """Bandeau des actions disponibles : rappel des touches, et boutons cliquables."""

    deleteRequested = Signal()
    skipRequested = Signal()
    moveRequested = Signal(dict)
    rateRequested = Signal(int)

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

        delete_cap = KeyCap("Suppr", delete_label, "danger")
        delete_cap.clicked.connect(self.deleteRequested)
        self.layout_.addWidget(delete_cap)

        skip_cap = KeyCap("Espace", "Passer", "neutral")
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
        """Resserre les vignettes si la ligne deborde, les rouvre sinon."""
        caps = self.caps()
        if not caps:
            return
        for cap in caps:
            cap.set_compact(False)
        spacing = self.layout_.spacing()
        wanted = sum(cap.sizeHint().width() + spacing for cap in caps)
        self.compact = wanted > max(1, self.width())
        for cap in caps:
            cap.set_compact(self.compact)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())


def pick_folders(parent, caption: str, start: str = "") -> list:
    """Ouvre un sélecteur de dossiers acceptant une sélection multiple.

    Le sélecteur natif de Windows ne laisse choisir qu'un dossier à la fois. On
    passe donc par celui de Qt, dont on élargit le mode de sélection des vues
    internes — seule façon d'ajouter vingt destinations en une fois.
    """
    dialog = QFileDialog(parent, caption, start)
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.DontUseNativeDialog, True)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    for view in dialog.findChildren((QListView, QTreeView)):
        view.setSelectionMode(QAbstractItemView.ExtendedSelection)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return []
    chosen = []
    for selected in dialog.selectedFiles():
        path = Path(selected)
        if path.is_dir():
            chosen.append(path)
    return chosen


class DestinationsDialog(QDialog):
    """Gestion des dossiers de destination : touche, libellé et ordre d'affichage."""

    COL_GRIP, COL_KEY, COL_LABEL, COL_PATH = range(4)

    def __init__(self, destinations: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dossiers de destination")
        self.resize(820, 480)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Chaque destination est déclenchée par sa touche pendant le tri. "
            "Double-cliquez une cellule pour la modifier, et glissez une ligne "
            "par sa poignée pour changer l'ordre des boutons."
        ))

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
        add_many = QPushButton("Ajouter tous les sous-dossiers de…")
        remove = QPushButton("Retirer")
        renumber = QPushButton("Renuméroter")
        renumber.setToolTip("Réattribue les touches dans l'ordre de la liste")
        reset = QPushButton("Réinitialiser")
        reset.setObjectName("danger")
        add.clicked.connect(self.add_folders)
        add_many.clicked.connect(self.add_children_of)
        remove.clicked.connect(self.remove_selected)
        renumber.clicked.connect(self.renumber)
        reset.clicked.connect(self.reset_all)
        for button in (add, add_many, remove, renumber):
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
        start = ""
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
            self, "Dossier contenant les destinations"
        )
        if not parent:
            return
        children = sorted(
            (p for p in Path(parent).iterdir() if p.is_dir()),
            key=lambda p: p.name.lower(),
        )
        if not children:
            QMessageBox.information(
                self, "Rien à ajouter", "Ce dossier ne contient aucun sous-dossier."
            )
            return
        self._add_paths(children)

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

