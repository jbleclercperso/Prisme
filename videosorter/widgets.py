"""Composants d'interface : grille d'aperçus, lecteur, barre de commandes, réglages."""
from __future__ import annotations

import math
import time
from pathlib import Path

from PySide6.QtCore import (
    QPoint, QPointF, QRect, QSize, QTimer, QUrl, Qt, Signal,
)
from PySide6.QtGui import QColor, QCursor, QPainter, QPixmap, QPolygonF
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit,
    QListView, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
    QTreeView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .actions import ActionError
from .config import KEY_ORDER, RESERVED_KEYS
from .scan import human_duration, human_resolution

GRID_COLUMNS = 5

STYLESHEET = """
QWidget { background: #14161a; color: #e6e8ea; font-size: 13px; }
QLabel { border: none; background: transparent; }
QLabel#title { font-size: 23px; font-weight: 600; color: #ffffff; }
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
QFrame#boardCard[state="rangé"] { border-color: #3f6b39; }
QFrame#boardCard[state="écarté"] { border-color: #6d2f38; }
QFrame#boardCard[state="passé"] { border-color: #4a4f5c; }
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
QCheckBox#cardPick::indicator { width: 18px; height: 18px;
                                border: 1px solid #6b7684; border-radius: 4px;
                                background: rgba(8, 10, 13, 0.75); }
QCheckBox#cardPick::indicator:checked { background: #2f6fed; border-color: #2f6fed; }
QLabel#tileDuration { background: rgba(0,0,0,0.78); color: #ffffff;
                     border-radius: 5px; padding: 2px 8px;
                     font-size: 13px; font-weight: 700; }
QLabel#tileCaption { color: #9aa6b4; font-size: 11px; font-weight: 600; }
QLabel#tilePlaceholder { color: #59616d; font-size: 12px; }
QFrame#playRail { background: rgba(255,255,255,0.22); border: 0;
                  border-radius: 4px; }
QFrame#playProgress { background: #5c9dff; border: 0; border-radius: 4px; }
QLabel#remaining { background: rgba(0,0,0,0.82); color: #ffffff;
                   border-radius: 5px; padding: 3px 9px;
                   font-size: 14px; font-weight: 700; }
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


class PreviewTile(QFrame):
    """Une case d'aperçu : vignette + numéro + nom du fichier source."""

    def __init__(self, slot: int, parent=None):
        super().__init__(parent)
        self.setObjectName("tile")
        self.setProperty("hovered", "false")
        self.slot = slot
        self.video: str = ""
        self.ts: float = 0.0
        self.setMinimumSize(190, 132)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.image = QLabel(self)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setScaledContents(False)

        self.placeholder = QLabel("…", self)
        self.placeholder.setObjectName("tilePlaceholder")
        self.placeholder.setAlignment(Qt.AlignCenter)

        self.badge = QLabel(str(slot + 1), self)
        self.badge.setObjectName("tileBadge")

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
        self.unplayable: set = set()

        self.grid_layout = QGridLayout(self)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(8)
        self.tiles: list = []
        for slot in range(count):
            tile = PreviewTile(slot, self)
            self.grid_layout.addWidget(tile, slot // GRID_COLUMNS, slot % GRID_COLUMNS)
            self.tiles.append(tile)
        self.visible_count = count

        # Trait d'avancement, pose sur le bas de la case en cours de lecture.
        self.progress = QFrame(self)
        self.progress.setObjectName("playProgress")
        self.progress.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.progress.hide()

        # Temps restant de l'extrait survole, en haut a droite de la case.
        self.remaining = QLabel("", self)
        self.remaining.setObjectName("remaining")
        self.remaining.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.remaining.hide()

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
        self.progress.hide()
        self.remaining.hide()
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
        if self.hovered_slot == -1:
            self.progress.hide()
            self.remaining.hide()
            return
        rect = self.tiles[self.hovered_slot].geometry()
        width = max(1, int(rect.width() * fraction))
        self.progress.setGeometry(rect.x(), rect.bottom() - 24, width, 5)
        self.progress.raise_()
        self.progress.show()

        duration = self.player.duration()
        if duration > 0:
            left = max(0, duration - self.player.position()) / 1000.0
            self.remaining.setText(f"−{human_duration(left)}")
            self.remaining.adjustSize()
            self.remaining.move(rect.right() - self.remaining.width() - 8, rect.y() + 8)
            self.remaining.raise_()
            self.remaining.show()

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
        self.progress.hide()
        self.remaining.hide()
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


class SinglePlayer(QWidget):
    """Mode fichier : la vidéo courante est lue en grand, avec une pellicule."""

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
        # Posee a droite plutot qu'en dessous : une bande horizontale volait au
        # lecteur quatre-vingt-dix pixels sur toute la largeur, alors que la
        # place perdue sur le cote ne coute rien a une video large.
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Le lecteur est posé dans un cadre qui le rogne : agrandir sa géométrie
        # au-delà du cadre produit un zoom, sans passer par une scène graphique.
        self.video_area = QWidget(self)
        self.video_area.setObjectName("videoArea")
        self.video_area.setMinimumHeight(320)
        self.video = QVideoWidget(self.video_area)
        self.video.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.video_area, 1)

        self.zoom = 1.0
        self.zoom_focus = QPointF(0.5, 0.5)   # point fixe, en proportion du cadre

        self.position_label = QLabel("", self.video_area)
        self.position_label.setObjectName("tileBadge")
        self.position_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.position_label.hide()

        # Rail et trait d'avancement : discrets, mais toujours la. Enfants du
        # cadre video et non de la fenetre : poses ailleurs, ils passaient
        # derriere l'image et l'on ne voyait jamais ou en etait la lecture.
        self.progress_rail = QFrame(self.video_area)
        self.progress_rail.setObjectName("playRail")
        self.progress_rail.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.progress = QFrame(self.video_area)
        self.progress.setObjectName("playProgress")
        self.progress.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # Le temps restant, en permanence, en haut a droite de l'image : c'est
        # la question qu'on se pose en regardant, bien plus que la position
        # absolue.
        self.remaining = QLabel("", self.video_area)
        self.remaining.setObjectName("remaining")
        self.remaining.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.remaining.hide()

        strip = QWidget(self)
        self.strip_layout = QVBoxLayout(strip)
        self.strip_layout.setContentsMargins(0, 0, 0, 0)
        self.strip_layout.setSpacing(6)
        self.tiles: list = []
        for slot in range(self.count):
            tile = PreviewTile(slot, strip)
            tile.strip_mode = True
            tile.duration_chip.hide()
            tile.setMinimumSize(150, 86)
            tile.setMaximumWidth(190)
            self.strip_layout.addWidget(tile, 1)
            self.tiles.append(tile)
        strip.setFixedWidth(176)
        layout.addWidget(strip)

        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(lambda _d: self._on_position(
            self.player.position()))

        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(90)
        self.hover_timer.timeout.connect(self._poll_hover)
        self.hovered_slot = -1

        self.position_timer = QTimer(self)
        self.position_timer.setSingleShot(True)
        self.position_timer.timeout.connect(self.position_label.hide)

    def showEvent(self, event):
        super().showEvent(event)
        self.hover_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.hover_timer.stop()
        self.stop()

    def set_muted(self, muted: bool) -> None:
        self.audio.setMuted(muted)

    def set_item(self, path: str, message: str = "…") -> None:
        self.reset_zoom()
        for tile in self.tiles:
            tile.reset()
            tile.placeholder.setText(message)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

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

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(0)
            self.player.play()

    RAIL_HEIGHT = 8

    def _on_position(self, position: int) -> None:
        duration = self.player.duration()
        fraction = (position / duration) if duration > 0 else 0.0
        # Coordonnees du cadre video, ces pieces en etant les enfants.
        area = self.video_area.rect()
        top = area.bottom() - self.RAIL_HEIGHT
        self.progress_rail.setGeometry(0, top, area.width(), self.RAIL_HEIGHT)
        self.progress.setGeometry(
            0, top,
            max(0, int(area.width() * max(0.0, min(1.0, fraction)))),
            self.RAIL_HEIGHT,
        )
        self.progress_rail.show()
        self.progress.show()
        self.progress_rail.raise_()
        self.progress.raise_()

        if duration > 0:
            left = max(0, duration - position) / 1000.0
            self.remaining.setText(f"−{human_duration(left)}")
            self.remaining.adjustSize()
            self.remaining.move(area.right() - self.remaining.width() - 12, 12)
            self.remaining.raise_()
            self.remaining.show()
        else:
            self.remaining.hide()

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
        self.video.setGeometry(left, top, width, height)

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
        # Le clic droit remet l'image à sa taille : geste unique, sans menu.
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

    def toggle_pause(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self) -> None:
        self.player.stop()


class FilterEdit(QLineEdit):
    """Champ de filtre qui rend la main au clavier de tri sur Échap ou Entrée."""

    released = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self.released.emit()
            return
        super().keyPressEvent(event)


class FilterBar(QWidget):
    """Filtre par nom : ce qu'on veut voir, et ce qu'on veut écarter."""

    changed = Signal(str, str)
    released = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        label = QLabel("Filtre", self)
        label.setObjectName("hint")
        self.include = FilterEdit(self)
        self.include.setPlaceholderText("contient… (plusieurs termes séparés par des virgules)")
        self.exclude = FilterEdit(self)
        self.exclude.setPlaceholderText("exclure… (ex : +)")
        self.exclude.setObjectName("excludeEdit")
        self.count = QLabel("", self)
        self.count.setObjectName("counter")
        clear = QPushButton("Effacer", self)
        clear.setFocusPolicy(Qt.NoFocus)
        clear.clicked.connect(self.clear)

        layout.addWidget(label)
        layout.addWidget(self.include, 3)
        layout.addWidget(self.exclude, 2)
        layout.addWidget(self.count)
        layout.addWidget(clear)

        # Un court délai évite de refiltrer à chaque frappe pendant la saisie.
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(220)
        self.timer.timeout.connect(self._emit)
        for field in (self.include, self.exclude):
            field.textChanged.connect(lambda _t: self.timer.start())
            field.released.connect(self.released)

    def _emit(self) -> None:
        self.changed.emit(self.include.text(), self.exclude.text())

    def set_terms(self, include: str, exclude: str) -> None:
        for field, value in ((self.include, include), (self.exclude, exclude)):
            field.blockSignals(True)
            field.setText(value)
            field.blockSignals(False)

    def clear(self) -> None:
        self.set_terms("", "")
        self._emit()
        self.released.emit()

    def set_count(self, shown: int, total: int) -> None:
        hidden = total - shown
        self.count.setText(f"{hidden} masqué{'s' if hidden > 1 else ''}" if hidden else "")


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


class _FocusGlass(QWidget):
    """Surface transparente posée sur le lecteur, qui en capte les gestes.

    Le widget vidéo de Qt est une fenêtre native : sous Windows il reçoit les
    clics directement du système, sans que Qt puisse les faire traverser. La
    seule façon fiable de garder la molette et les clics est donc d'interposer
    un widget ordinaire au-dessus.
    """

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

    def wheelEvent(self, event):
        self.owner.handle_wheel(event)

    def mousePressEvent(self, event):
        self.owner.handle_press(event)

    def mouseDoubleClickEvent(self, event):
        self.owner.closed.emit()

    def keyPressEvent(self, event):
        self.owner.keyPressEvent(event)


class FocusPlayer(QWidget):
    """Lecteur plein écran, à l'intérieur de l'application.

    Un double-clic sur une vidéo ouvre celle-ci ici plutôt que de la confier au
    lecteur du système : on reste dans le tri, avec le zoom, l'avancement et le
    temps restant sous la main, et la touche Échap rend la main.
    """

    closed = Signal()

    def __init__(self, scroll_seconds: int = 5, parent=None):
        super().__init__(parent)
        self.setObjectName("focusPlayer")
        self.scroll_seconds = scroll_seconds
        self.zoom = 1.0
        self.zoom_focus = QPointF(0.5, 0.5)

        self.video_area = QWidget(self)
        self.video_area.setObjectName("videoArea")
        self.video_area.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.video = QVideoWidget(self.video_area)
        self.video.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.title = QLabel("", self)
        self.title.setObjectName("focusTitle")
        self.remaining = QLabel("", self)
        self.remaining.setObjectName("remaining")
        self.hint = QLabel(
            "Clic gauche + molette : zoomer  ·  clic droit : taille normale  ·  "
            "molette : parcourir  ·  Échap ou double-clic : fermer", self,
        )
        self.hint.setObjectName("hint")
        self.progress_rail = QFrame(self)
        self.progress_rail.setObjectName("playRail")
        self.progress = QFrame(self)
        self.progress.setObjectName("playProgress")
        for child in (self.title, self.remaining, self.hint,
                      self.progress_rail, self.progress):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # Une vitre par-dessus tout capte souris et molette. Le widget vidéo est
        # une fenêtre native : sous Windows, il reçoit les clics du système même
        # marqué transparent aux événements, et les mangeait tous.
        self.glass = _FocusGlass(self)

        self.mute_button = QPushButton("", self)
        self.mute_button.setObjectName("focusMute")
        self.mute_button.setFixedSize(40, 32)
        self.mute_button.setFocusPolicy(Qt.NoFocus)
        self.mute_button.clicked.connect(self.toggle_mute)

        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(
            lambda _d: self._on_position(self.player.position())
        )
        self.player.mediaStatusChanged.connect(self._on_status)
        self._pending_seek = 0

    def toggle_mute(self) -> None:
        self.audio.setMuted(not self.audio.isMuted())
        self._refresh_mute()

    def _refresh_mute(self) -> None:
        self.mute_button.setText("🔇" if self.audio.isMuted() else "🔊")
        self.mute_button.setToolTip(
            "Son coupé — cliquer pour l'activer (M)" if self.audio.isMuted()
            else "Son actif — cliquer pour le couper (M)"
        )

    # -- lecture ---------------------------------------------------------
    def play(self, path: str, start_s: float = 0.0, muted: bool = False) -> None:
        self.zoom = 1.0
        self.zoom_focus = QPointF(0.5, 0.5)
        self.title.setText(Path(path).name)
        self.audio.setMuted(muted)
        self._pending_seek = int(start_s * 1000)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.show()
        self.raise_()
        self._layout_children()
        self.glass.setFocus()

    def stop(self) -> None:
        """Referme le lecteur et relâche le fichier.

        On masque avant d'arrêter : démonter la sortie vidéo pendant que le
        widget est encore affiché peut bloquer le moteur multimédia.
        """
        self.hide()
        self.player.stop()
        self.player.setSource(QUrl())

    def _on_status(self, status) -> None:
        loaded = (QMediaPlayer.MediaStatus.LoadedMedia,
                  QMediaPlayer.MediaStatus.BufferedMedia)
        if status in loaded and self._pending_seek:
            self.player.setPosition(self._pending_seek)
            self._pending_seek = 0
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(0)
            self.player.play()

    def _on_position(self, position: int) -> None:
        duration = self.player.duration()
        fraction = (position / duration) if duration > 0 else 0.0
        rect = self.rect()
        top = rect.bottom() - 40
        self.progress_rail.setGeometry(24, top, max(0, rect.width() - 48), 8)
        self.progress.setGeometry(
            24, top, max(0, int((rect.width() - 48) * min(1.0, fraction))), 8
        )
        if duration > 0:
            left = max(0, duration - position) / 1000.0
            self.remaining.setText(f"−{human_duration(left)}")
        else:
            self.remaining.setText("")
        self.remaining.adjustSize()
        self.remaining.move(rect.right() - self.remaining.width() - 24, 24)
        self.remaining.raise_()

    # -- disposition et zoom ---------------------------------------------
    def _layout_children(self) -> None:
        rect = self.rect()
        self.video_area.setGeometry(0, 60, rect.width(), max(0, rect.height() - 110))
        self._apply_zoom()
        self.glass.setGeometry(rect)
        self.glass.raise_()
        self.title.adjustSize()
        self.title.move(24, 22)
        self.hint.adjustSize()
        self.hint.move(24, rect.bottom() - 24)
        self.mute_button.move(rect.right() - self.mute_button.width() - 24, 58)
        self.mute_button.raise_()
        self._refresh_mute()
        for widget in (self.title, self.remaining, self.hint,
                       self.progress_rail, self.progress):
            widget.raise_()
        self._on_position(self.player.position())

    def _apply_zoom(self) -> None:
        area = self.video_area.rect()
        width = int(area.width() * self.zoom)
        height = int(area.height() * self.zoom)
        left = int(self.zoom_focus.x() * (area.width() - width))
        top = int(self.zoom_focus.y() * (area.height() - height))
        self.video.setGeometry(left, top, width, height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_children()

    def handle_wheel(self, event):
        notches = event.angleDelta().y() / 120.0
        if not notches:
            return
        if event.buttons() & Qt.LeftButton or event.modifiers() & Qt.ControlModifier:
            area = self.video_area.geometry()
            local = event.position().toPoint() - area.topLeft()
            if area.width() > 0 and area.height() > 0:
                self.zoom_focus = QPointF(
                    max(0.0, min(1.0, local.x() / area.width())),
                    max(0.0, min(1.0, local.y() / area.height())),
                )
            self.zoom = max(1.0, min(6.0, self.zoom * (1.25 ** notches)))
            self._apply_zoom()
        else:
            self.player.setPosition(
                max(0, self.player.position() + seek_step(event, self.scroll_seconds))
            )
        event.accept()

    def handle_press(self, event):
        if event.button() == Qt.RightButton:
            self.zoom = 1.0
            self.zoom_focus = QPointF(0.5, 0.5)
            self._apply_zoom()

    def wheelEvent(self, event):
        self.handle_wheel(event)

    def mousePressEvent(self, event):
        self.handle_press(event)

    def mouseDoubleClickEvent(self, event):
        self.closed.emit()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Escape, Qt.Key_F):
            self.closed.emit()
        elif key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()
        elif key == Qt.Key_M:
            self.toggle_mute()
        elif key == Qt.Key_Left:
            self.player.setPosition(
                max(0, self.player.position() - self.scroll_seconds * 1000)
            )
        elif key == Qt.Key_Right:
            self.player.setPosition(self.player.position() + self.scroll_seconds * 1000)
        else:
            super().keyPressEvent(event)


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


class PageBar(QWidget):
    """Navigation entre les pages d'aperçus d'un même dossier."""

    previousPage = Signal()
    nextPage = Signal()
    toggleSort = Signal()
    randomHere = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.random_here = QPushButton("Au hasard ici", self)
        self.random_here.setToolTip(
            "Regarder une vidéo au hasard parmi celles de cet élément"
        )
        self.sort = QPushButton("Durée ▼", self)
        self.sort.setToolTip("Classer les vidéos par durée, décroissante ou croissante")
        self.previous = QPushButton("◂", self)
        self.previous.setToolTip("Aperçus précédents   (Ctrl+←)")
        self.label = QLabel("", self)
        self.label.setObjectName("hint")
        self.next = QPushButton("▸", self)
        self.next.setToolTip("Aperçus suivants   (Ctrl+→)")
        for button in (self.sort, self.previous, self.next, self.random_here):
            button.setFocusPolicy(Qt.NoFocus)
        self.random_here.clicked.connect(self.randomHere)
        self.previous.clicked.connect(self.previousPage)
        self.next.clicked.connect(self.nextPage)
        self.sort.clicked.connect(self.toggleSort)

        layout.addWidget(self.random_here)
        layout.addWidget(self.sort)
        layout.addStretch(1)
        layout.addWidget(self.previous)
        layout.addWidget(self.label)
        layout.addWidget(self.next)

    def set_state(self, text: str, has_previous: bool, has_next: bool) -> None:
        self.label.setText(text)
        self.previous.setEnabled(has_previous)
        self.next.setEnabled(has_next)

    def set_sort(self, mode: str) -> None:
        labels = {"": "Ordre du dossier", "desc": "Durée ▼", "asc": "Durée ▲"}
        self.sort.setText(labels.get(mode, "Durée ▼"))


class AdvancedFilterBar(QWidget):
    """Filtres chiffrés : durée, résolution, note, avec des opérateurs écrits.

    « plus longue que » se lit mieux que « > » quand on revient sur un réglage
    posé la veille.
    """

    changed = Signal(dict)
    columnsChanged = Signal(int)

    RESOLUTIONS = (
        ("peu importe", 0), ("360p", 360), ("480p", 480), ("720p", 720),
        ("1080p", 1080), ("1440p", 1440), ("4K", 2160),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        def label(text):
            widget = QLabel(text, self)
            widget.setObjectName("hint")
            return widget

        self.duration_op = QComboBox(self)
        self.duration_op.addItem("peu importe", "")
        self.duration_op.addItem("plus longue que", "gt")
        self.duration_op.addItem("plus courte que", "lt")
        self.duration_value = QLineEdit(self)
        self.duration_value.setPlaceholderText("minutes")
        self.duration_value.setFixedWidth(80)

        self.resolution_op = QComboBox(self)
        self.resolution_op.addItem("au moins", "gte")
        self.resolution_op.addItem("au plus", "lte")
        self.resolution_value = QComboBox(self)
        for text, height in self.RESOLUTIONS:
            self.resolution_value.addItem(text, height)

        self.stars_value = QComboBox(self)
        self.stars_value.addItem("peu importe", -1)
        for count in range(6):
            self.stars_value.addItem("★" * count if count else "aucune", count)

        layout.addWidget(label("Durée"))
        layout.addWidget(self.duration_op)
        layout.addWidget(self.duration_value)
        layout.addSpacing(10)
        layout.addWidget(label("Résolution"))
        layout.addWidget(self.resolution_op)
        layout.addWidget(self.resolution_value)
        layout.addSpacing(10)
        layout.addWidget(label("Note"))
        layout.addWidget(self.stars_value)
        layout.addSpacing(10)
        layout.addWidget(label("Par rangée"))
        self.density = QComboBox(self)
        self.density.setToolTip("Nombre de cartes par rangée")
        layout.addWidget(self.density)
        layout.addStretch(1)

        self.reset_button = QPushButton("Tout afficher", self)
        self.reset_button.setFocusPolicy(Qt.NoFocus)
        self.reset_button.clicked.connect(self.reset)
        layout.addWidget(self.reset_button)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(220)
        self.timer.timeout.connect(self._emit)
        for widget in (self.duration_op, self.resolution_op,
                       self.resolution_value, self.stars_value):
            widget.currentIndexChanged.connect(lambda _i: self.timer.start())
        self.duration_value.textChanged.connect(lambda _t: self.timer.start())
        self.density.currentIndexChanged.connect(
            lambda _i: self.columnsChanged.emit(int(self.density.currentData() or 5))
        )

    def set_columns_choices(self, choices, current: int) -> None:
        self.density.blockSignals(True)
        self.density.clear()
        for count in choices:
            self.density.addItem(str(count), count)
        index = self.density.findData(current)
        self.density.setCurrentIndex(index if index >= 0 else 0)
        self.density.blockSignals(False)

    def criteria(self) -> dict:
        try:
            minutes = float(self.duration_value.text().replace(",", "."))
        except ValueError:
            minutes = 0.0
        return {
            "duration_op": self.duration_op.currentData() if minutes > 0 else "",
            "duration_s": minutes * 60,
            "resolution_op": self.resolution_op.currentData(),
            "resolution": self.resolution_value.currentData(),
            "stars": self.stars_value.currentData(),
        }

    def _emit(self) -> None:
        self.changed.emit(self.criteria())

    def is_active(self) -> bool:
        rules = self.criteria()
        return bool(rules["duration_op"]) or rules["resolution"] > 0 \
            or rules["stars"] >= 0

    def reset(self) -> None:
        for widget in (self.duration_op, self.resolution_op,
                       self.resolution_value, self.stars_value):
            widget.blockSignals(True)
            widget.setCurrentIndex(0)
            widget.blockSignals(False)
        self.duration_value.blockSignals(True)
        self.duration_value.clear()
        self.duration_value.blockSignals(False)
        self._emit()


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
        text = QLabel(elide(label, 26), self)
        text.setObjectName("keyLabel")
        layout.addWidget(key_label)
        layout.addWidget(text)
        # Sans cela, un clic tombant sur le texte n'atteindrait pas la vignette.
        for child in (key_label, text):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if tone == "danger":
            key_label.setStyleSheet("color: #ff8a8a;")
        elif tone == "neutral":
            key_label.setStyleSheet("color: #8fd0ff;")

    def mouseReleaseEvent(self, event):
        # Relâcher en dehors annule le clic, comme sur un vrai bouton.
        if event.button() == Qt.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StarCap(QFrame):
    """Vignette de notation : le chiffre à presser, et les étoiles qu'il pose."""

    clicked = Signal()

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setObjectName("keycap")
        self.count = count
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"Noter {count} étoile{'s' if count > 1 else ''}   (touche {count})")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 6, 11, 6)
        layout.setSpacing(6)

        digit = QLabel(str(count), self)
        digit.setObjectName("keyLetter")
        digit.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(digit)

        self.stars = StarStrip(15, self)
        self.stars.set_value(count)
        self.stars.setEnabled(False)
        self.stars.setFixedWidth(15 * count + 8)
        self.stars.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.stars)

    def mouseReleaseEvent(self, event):
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
        self.layout_ = FlowLayout(self)

    def rebuild(self, destinations: list, delete_label: str) -> None:
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        delete_cap = KeyCap("Suppr", delete_label, "danger")
        delete_cap.clicked.connect(self.deleteRequested)
        self.layout_.addWidget(delete_cap)

        skip_cap = KeyCap("Espace", "Passer", "neutral")
        skip_cap.clicked.connect(self.skipRequested)
        self.layout_.addWidget(skip_cap)

        # Une vignette par note : le chiffre, puis autant d'étoiles dessinées.
        # « 0–5 Noter » n'apprenait rien à qui ne connaissait pas déjà.
        for count in range(1, 6):
            cap = StarCap(count)
            cap.clicked.connect(
                lambda checked=False, value=count: self.rateRequested.emit(value)
            )
            self.layout_.addWidget(cap)
        clear = KeyCap("0", "Effacer la note", "neutral")
        clear.clicked.connect(lambda: self.rateRequested.emit(0))
        self.layout_.addWidget(clear)

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
        self.updateGeometry()


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


class RootBar(QWidget):
    """Entête : racine courante, avancement, accès aux réglages.

    Les boutons sont posés sur une disposition qui passe à la ligne : sur un
    écran étroit ou en plein écran sur un format inattendu, ils s'empilent au
    lieu de sortir du cadre.
    """

    changeRoot = Signal()
    openSettings = Signal()
    toggleMode = Signal()
    toggleTree = Signal()
    toggleMute = Signal()
    enterItem = Signal()
    goUp = Signal()
    openTrash = Signal()
    toggleBoard = Signal()
    pickRandom = Signal()
    goBack = Signal()
    columnsChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.root_label = QLabel("—", self)
        self.root_label.setObjectName("rootPath")
        self.counter = QLabel("", self)
        self.counter.setObjectName("counter")
        self.pending = QLabel("", self)
        self.pending.setObjectName("pending")
        self.pending.hide()

        change = QPushButton("Changer de racine")
        settings = QPushButton("Destinations…")
        self.mode_button = QPushButton("Mode")
        mode = self.mode_button
        tree = QPushButton("Arborescence")
        self.mute = QPushButton("🔇")
        self.mute.setFixedWidth(42)
        self.back = QPushButton("◂ Précédent", self)
        self.back.setToolTip("Revenir à l'endroit précédent   (Alt+←)")
        self.back.setEnabled(False)
        self.board = QPushButton("Planche", self)
        self.board.setToolTip("Voir les éléments en cartes   (Ctrl+P)")
        self.random = QPushButton("Au hasard", self)
        self.random.setToolTip("Se placer sur un élément au hasard   (Ctrl+H)")
        self.trash = QPushButton("Corbeille", self)
        self.trash.setToolTip("Ce qui a été écarté cette session   (Ctrl+B)")
        self.trash.hide()
        self.enter = QPushButton("Entrer ▸")
        self.enter.setObjectName("enterButton")
        self.enter.setToolTip(
            "Trier les vidéos de ce dossier, une par une   (Ctrl+↓)"
        )
        self.up = QPushButton("◂ Remonter")
        self.up.setToolTip("Revenir au dossier parent   (Ctrl+↑ ou Échap)")
        self.up.hide()
        for button in (change, settings, mode, tree, self.mute, self.enter,
                       self.up, self.trash, self.board, self.random, self.back):
            button.setFocusPolicy(Qt.NoFocus)
        change.clicked.connect(self.changeRoot)
        settings.clicked.connect(self.openSettings)
        mode.clicked.connect(self.toggleMode)
        tree.clicked.connect(self.toggleTree)
        self.mute.clicked.connect(self.toggleMute)
        self.enter.clicked.connect(self.enterItem)
        self.up.clicked.connect(self.goUp)
        self.trash.clicked.connect(self.openTrash)
        self.board.clicked.connect(self.toggleBoard)
        self.random.clicked.connect(self.pickRandom)
        self.back.clicked.connect(self.goBack)

        self.root_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        top.addWidget(self.root_label, 1)
        top.addWidget(self.pending)
        top.addWidget(self.counter)
        outer.addLayout(top)

        self.buttons = FlowLayout(spacing=8)
        for widget in (self.board, self.mode_button, self.enter, self.up, self.back,
                       self.random, self.mute, self.trash, tree, settings, change):
            self.buttons.addWidget(widget)
        outer.addLayout(self.buttons)

    def set_muted(self, muted: bool) -> None:
        # Un pictogramme se lit plus vite qu'une etiquette, et prend moins de place.
        self.mute.setText("🔇" if muted else "🔊")
        self.mute.setToolTip(
            "Son coupé — cliquer pour l'activer   (Ctrl+M)" if muted
            else "Son actif — cliquer pour le couper   (Ctrl+M)"
        )

    def set_can_go_back(self, can: bool) -> None:
        self.back.setEnabled(can)

    def set_board(self, active: bool) -> None:
        # Le bouton annonce la vue courante et non celle qu'il ferait apparaitre :
        # « Planche / Fiche » sans contexte ne disait pas ou l'on se trouvait.
        self.board.setText("Vue : planche" if active else "Vue : fiche")
        self.board.setToolTip(
            "Vous êtes en planche — cliquer pour revenir à l'élément unique   (Ctrl+P)"
            if active else
            "Vous êtes sur un élément — cliquer pour voir la planche   (Ctrl+P)"
        )

    def set_trash(self, count: int) -> None:
        self.trash.setText(f"Corbeille ({count})")
        self.trash.setVisible(count > 0)

    def set_navigation(self, can_enter: bool, nested: bool) -> None:
        """N'offre « Entrer » que s'il y a un dossier ouvrable sous le curseur."""
        self.enter.setVisible(can_enter)
        self.up.setVisible(nested)

    def set_pending(self, count: int) -> None:
        """Rappelle discrètement que des copies se poursuivent en arrière-plan."""
        if count > 0:
            self.pending.setText(f"⟳ {count} transfert{'s' if count > 1 else ''}")
            self.pending.show()
        else:
            self.pending.hide()

