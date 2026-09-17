"""Composants d'interface : grille d'aperçus, lecteur, barre de commandes, réglages."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLayout, QLineEdit,
    QListView, QMessageBox, QPushButton, QSizePolicy, QTreeView, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .config import KEY_ORDER, RESERVED_KEYS
from .scan import human_duration, human_resolution

GRID_COLUMNS = 5

STYLESHEET = """
QWidget { background: #14161a; color: #e6e8ea; font-size: 13px; }
QLabel#title { font-size: 23px; font-weight: 600; color: #ffffff; }
QLabel#subtitle { font-size: 15px; color: #b6c0cc; }
QLabel#counter { font-size: 13px; color: #9aa4b0; }
QLabel#rootPath { font-size: 13px; color: #9aa4b0; }
QLabel#pending { font-size: 12px; color: #8fb4ff; background: #1b2434;
                 border-radius: 5px; padding: 3px 9px; }
QLabel#parentPath { font-size: 13px; color: #8a94a2; }
QLabel#hint { color: #6f7885; }
QFrame#card { background: #1b1f26; border: 1px solid #262c35; border-radius: 10px; }
QFrame#tile { background: #0e1013; border: 1px solid #262c35; border-radius: 8px; }
QFrame#tile[hovered="true"] { border: 1px solid #4c8dff; }
QLabel#tileBadge { background: rgba(0,0,0,0.65); color: #dfe4ea; border-radius: 4px;
                   padding: 1px 5px; font-size: 11px; }
QLabel#tileDuration { background: rgba(0,0,0,0.78); color: #ffffff;
                     border-radius: 5px; padding: 2px 8px;
                     font-size: 13px; font-weight: 700; }
QLabel#tileCaption { color: #9aa6b4; font-size: 11px; font-weight: 600; }
QLabel#tilePlaceholder { color: #59616d; font-size: 12px; }
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
        self._pixmap: QPixmap | None = None

    # -- contenu ---------------------------------------------------------
    def reset(self) -> None:
        self.video = ""
        self.ts = 0.0
        self.duration = 0.0
        self.height_px = 0
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

    def __init__(self, count: int = 10, preview_seconds: int = 10,
                 scroll_seconds: int = 5, parent=None):
        super().__init__(parent)
        self.count = count
        self.preview_seconds = preview_seconds
        self.scroll_seconds = scroll_seconds
        self.item_id = ""
        self.hovered_slot = -1
        self.unplayable: set = set()

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.tiles: list = []
        for slot in range(count):
            tile = PreviewTile(slot, self)
            layout.addWidget(tile, slot // GRID_COLUMNS, slot % GRID_COLUMNS)
            self.tiles.append(tile)

        # Le lecteur flotte au-dessus de la case survolée.
        self.video = QVideoWidget(self)
        self.video.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.video.hide()
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        self.player.setAudioOutput(self.audio)
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
        self.audio.setMuted(muted)

    def set_item(self, item_id: str, message: str = "…") -> None:
        self.stop()
        self.item_id = item_id
        for tile in self.tiles:
            tile.reset()
            tile.placeholder.setText(message)

    def set_plan(self, plan: list) -> None:
        for slot, tile in enumerate(self.tiles):
            if slot < len(plan):
                tile.set_source(*plan[slot])
            else:
                tile.set_empty("")

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
        self.hovered_slot = slot
        if slot == -1:
            self._leave()
            return
        self.tiles[slot].set_hovered(True)
        self._play_slot(slot)

    def _leave(self) -> None:
        self.hovered_slot = -1
        self.stop()

    def _play_slot(self, slot: int) -> None:
        tile = self.tiles[slot]
        if not tile.video:
            self.video.hide()
            return
        if tile.video in self.unplayable:
            self.video.hide()
            return

        self.video.setGeometry(tile.geometry().adjusted(1, 1, -1, -19))
        self.video.raise_()
        self.video.show()

        self._segment_start = int(tile.ts * 1000)
        self._pending_seek = self._segment_start
        url = QUrl.fromLocalFile(tile.video)
        if self.player.source() == url:
            self.player.setPosition(self._segment_start)
            self.player.play()
        else:
            self.player.setSource(url)
            self.player.play()

    def _on_status(self, status) -> None:
        loaded = (QMediaPlayer.MediaStatus.LoadedMedia,
                  QMediaPlayer.MediaStatus.BufferedMedia)
        if status in loaded and self._pending_seek:
            self.player.setPosition(self._pending_seek)
            self._pending_seek = 0
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(self._segment_start)
            self.player.play()

    def _on_position(self, position: int) -> None:
        if self.hovered_slot == -1:
            return
        if position > self._segment_start + self.preview_seconds * 1000:
            self.player.setPosition(self._segment_start)

    def _on_error(self, *_args) -> None:
        if 0 <= self.hovered_slot < len(self.tiles):
            video = self.tiles[self.hovered_slot].video
            if video:
                self.unplayable.add(video)
        self.video.hide()
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
        self.video.hide()
        for tile in self.tiles:
            tile.set_hovered(False)

    def mouseDoubleClickEvent(self, event):
        slot = self._slot_at(event.position().toPoint())
        if slot >= 0 and self.tiles[slot].video:
            self.openRequested.emit(self.tiles[slot].video)


class SinglePlayer(QWidget):
    """Mode fichier : la vidéo courante est lue en grand, avec une pellicule."""

    def __init__(self, count: int = 10, scroll_seconds: int = 5, parent=None):
        super().__init__(parent)
        self.count = count
        self.scroll_seconds = scroll_seconds
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.video = QVideoWidget(self)
        self.video.setMinimumHeight(320)
        # La molette doit atteindre ce widget-ci, pas le widget vidéo natif.
        self.video.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.video, 1)

        self.position_label = QLabel("", self)
        self.position_label.setObjectName("tileBadge")
        self.position_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.position_label.hide()

        strip = QWidget(self)
        self.strip_layout = QHBoxLayout(strip)
        self.strip_layout.setContentsMargins(0, 0, 0, 0)
        self.strip_layout.setSpacing(6)
        self.tiles: list = []
        for slot in range(count):
            tile = PreviewTile(slot, strip)
            tile.setMinimumSize(110, 72)
            tile.setMaximumHeight(86)
            self.strip_layout.addWidget(tile)
            self.tiles.append(tile)
        strip.setFixedHeight(92)
        layout.addWidget(strip)

        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self._on_status)

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
        """La molette avance ou recule dans la vidéo lue."""
        if not self.player.source().isValid():
            return super().wheelEvent(event)
        position = max(0, self.player.position() + seek_step(event, self.scroll_seconds))
        duration = self.player.duration()
        if duration > 0:
            position = min(position, max(0, duration - 500))
        self.player.setPosition(position)
        self._show_position(position)
        event.accept()

    def _show_position(self, position: int) -> None:
        duration = self.player.duration()
        text = human_duration(position / 1000.0)
        if duration > 0:
            text += f" / {human_duration(duration / 1000.0)}"
        self.position_label.setText(text)
        self.position_label.adjustSize()
        self.position_label.move(
            self.video.geometry().right() - self.position_label.width() - 10,
            self.video.geometry().top() + 10,
        )
        self.position_label.raise_()
        self.position_label.show()
        self.position_timer.start(1800)

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
    """Entête : racine courante, avancement, accès aux réglages."""

    changeRoot = Signal()
    openSettings = Signal()
    toggleMode = Signal()
    toggleTree = Signal()
    toggleMute = Signal()
    enterItem = Signal()
    goUp = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.root_label = QLabel("—", self)
        self.root_label.setObjectName("rootPath")
        self.counter = QLabel("", self)
        self.counter.setObjectName("counter")
        self.pending = QLabel("", self)
        self.pending.setObjectName("pending")
        self.pending.hide()

        change = QPushButton("Changer de racine")
        settings = QPushButton("Destinations…")
        mode = QPushButton("Mode")
        tree = QPushButton("Arborescence")
        self.mute = QPushButton("Son coupé")
        self.enter = QPushButton("Entrer ▸")
        self.enter.setObjectName("enterButton")
        self.enter.setToolTip(
            "Trier les vidéos de ce dossier, une par une   (Ctrl+↓)"
        )
        self.up = QPushButton("◂ Remonter")
        self.up.setToolTip("Revenir au dossier parent   (Ctrl+↑ ou Échap)")
        self.up.hide()
        for button in (change, settings, mode, tree, self.mute, self.enter, self.up):
            button.setFocusPolicy(Qt.NoFocus)
        change.clicked.connect(self.changeRoot)
        settings.clicked.connect(self.openSettings)
        mode.clicked.connect(self.toggleMode)
        tree.clicked.connect(self.toggleTree)
        self.mute.clicked.connect(self.toggleMute)
        self.enter.clicked.connect(self.enterItem)
        self.up.clicked.connect(self.goUp)

        self.root_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.root_label, 1)
        layout.addWidget(self.pending)
        layout.addWidget(self.counter)
        layout.addWidget(self.up)
        layout.addWidget(self.enter)
        layout.addWidget(self.mute)
        layout.addWidget(tree)
        layout.addWidget(mode)
        layout.addWidget(settings)
        layout.addWidget(change)

    def set_muted(self, muted: bool) -> None:
        self.mute.setText("Son coupé" if muted else "Son actif")
        self.mute.setToolTip("Ctrl+M")

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

