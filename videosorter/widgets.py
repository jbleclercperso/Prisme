"""Composants d'interface : grille d'aperçus, lecteur, barre de commandes, réglages."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLayout, QMessageBox,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from .config import KEY_ORDER, RESERVED_KEYS
from .scan import human_duration

GRID_COLUMNS = 5

STYLESHEET = """
QWidget { background: #14161a; color: #e6e8ea; font-size: 13px; }
QLabel#title { font-size: 21px; font-weight: 600; color: #ffffff; }
QLabel#subtitle { font-size: 13px; color: #9aa4b0; }
QLabel#counter { font-size: 13px; color: #9aa4b0; }
QLabel#pending { font-size: 12px; color: #8fb4ff; background: #1b2434;
                 border-radius: 5px; padding: 3px 9px; }
QLabel#parentPath { font-size: 12px; color: #6f7885; }
QLabel#hint { color: #6f7885; }
QFrame#card { background: #1b1f26; border: 1px solid #262c35; border-radius: 10px; }
QFrame#tile { background: #0e1013; border: 1px solid #262c35; border-radius: 8px; }
QFrame#tile[hovered="true"] { border: 1px solid #4c8dff; }
QLabel#tileBadge { background: rgba(0,0,0,0.65); color: #dfe4ea; border-radius: 4px;
                   padding: 1px 5px; font-size: 11px; }
QLabel#tileCaption { color: #8b95a3; font-size: 11px; }
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
QLabel#keyLetter { font-weight: 700; color: #ffd479; font-size: 13px; }
QLabel#keyLabel { color: #c3cad3; font-size: 12px; }
QLabel#statusBanner { border-radius: 6px; padding: 6px 10px; font-weight: 600; }
QTableWidget { background: #0e1013; gridline-color: #262c35;
               selection-background-color: #2f6fed; }
QHeaderView::section { background: #1b1f26; border: 0; padding: 6px; color: #9aa4b0; }
QLineEdit { background: #0e1013; border: 1px solid #323a45; border-radius: 6px;
            padding: 6px; }
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

        self.caption = QLabel("", self)
        self.caption.setObjectName("tileCaption")

        self._pixmap: QPixmap | None = None

    # -- contenu ---------------------------------------------------------
    def reset(self) -> None:
        self.video = ""
        self.ts = 0.0
        self._pixmap = None
        self.image.clear()
        self.placeholder.setText("…")
        self.placeholder.show()
        self.caption.setText("")
        self.set_hovered(False)

    def set_source(self, video: str, ts: float) -> None:
        self.video = video
        self.ts = ts
        self.set_position(ts)

    def set_position(self, seconds: float) -> None:
        """Rafraîchit l'horodatage affiché, sans toucher au point d'entrée."""
        name = Path(self.video).name if self.video else ""
        self.caption.setText(elide(f"{human_duration(seconds)}  ·  {name}", 40))

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.rect()
        self.image.setGeometry(1, 1, rect.width() - 2, rect.height() - 20)
        self.placeholder.setGeometry(1, 1, rect.width() - 2, rect.height() - 20)
        self.badge.adjustSize()
        self.badge.move(7, 7)
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
                tile.set_source(plan[slot][0], plan[slot][1])
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
        self.tiles[self.hovered_slot].set_position(position / 1000.0)
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
                tile.set_source(plan[slot][0], plan[slot][1])

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


class KeyCap(QFrame):
    """Rappel visuel d'un raccourci et de son effet."""

    def __init__(self, key: str, label: str, tone: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("keycap")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 6, 11, 6)
        layout.setSpacing(8)
        key_label = QLabel(key, self)
        key_label.setObjectName("keyLetter")
        text = QLabel(elide(label, 26), self)
        text.setObjectName("keyLabel")
        text.setToolTip(label)
        layout.addWidget(key_label)
        layout.addWidget(text)
        if tone == "danger":
            key_label.setStyleSheet("color: #ff8a8a;")
        elif tone == "neutral":
            key_label.setStyleSheet("color: #8fd0ff;")


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
    """Bandeau listant les actions disponibles pour l'élément courant."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = FlowLayout(self)

    def rebuild(self, destinations: list, delete_label: str) -> None:
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.layout_.addWidget(KeyCap("Suppr", delete_label, "danger"))
        self.layout_.addWidget(KeyCap("Espace", "Passer", "neutral"))
        for dest in destinations:
            self.layout_.addWidget(
                KeyCap(dest.get("key", "?").upper(), dest.get("label") or Path(dest["path"]).name)
            )
        self.updateGeometry()


class DestinationsDialog(QDialog):
    """Gestion des dossiers de destination et de leurs touches."""

    def __init__(self, destinations: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dossiers de destination")
        self.resize(720, 420)
        self.destinations = [dict(d) for d in destinations]

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Chaque destination est déclenchée par sa touche pendant le tri. "
            "Double-cliquez une cellule pour la modifier."
        ))

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Touche", "Libellé", "Dossier"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        add = QPushButton("Ajouter un dossier…")
        add_many = QPushButton("Ajouter tous les sous-dossiers de…")
        remove = QPushButton("Retirer")
        add.clicked.connect(self.add_one)
        add_many.clicked.connect(self.add_many)
        remove.clicked.connect(self.remove_selected)
        buttons.addWidget(add)
        buttons.addWidget(add_many)
        buttons.addWidget(remove)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self.refresh()

    def refresh(self) -> None:
        self.table.setRowCount(len(self.destinations))
        for row, dest in enumerate(self.destinations):
            for column, value in enumerate((dest.get("key", ""), dest.get("label", ""), dest.get("path", ""))):
                cell = QTableWidgetItem(value)
                if column == 2:
                    cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, column, cell)

    def _free_key(self) -> str:
        used = {d.get("key") for d in self.destinations}
        for key in KEY_ORDER:
            if key not in used and key not in RESERVED_KEYS:
                return key
        return ""

    def _append(self, path: Path) -> None:
        if any(Path(d["path"]) == path for d in self.destinations):
            return
        self.destinations.append({"key": self._free_key(), "label": path.name, "path": str(path)})

    def add_one(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choisir un dossier de destination")
        if chosen:
            self._append(Path(chosen))
            self.refresh()

    def add_many(self) -> None:
        parent = QFileDialog.getExistingDirectory(self, "Dossier contenant les destinations")
        if not parent:
            return
        children = sorted(
            (p for p in Path(parent).iterdir() if p.is_dir()),
            key=lambda p: p.name.lower(),
        )
        if not children:
            QMessageBox.information(self, "Rien à ajouter", "Ce dossier ne contient aucun sous-dossier.")
            return
        for child in children:
            self._append(child)
        self.refresh()

    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.destinations):
                del self.destinations[row]
        self.refresh()

    def result_destinations(self) -> list:
        """Relit le tableau. Les lignes inutilisables sont écartées et signalées."""
        out = []
        seen = set()
        rejected = []
        for row in range(self.table.rowCount()):
            key_item = self.table.item(row, 0)
            label_item = self.table.item(row, 1)
            path_item = self.table.item(row, 2)
            if not path_item:
                continue
            path = path_item.text().strip()
            label = (label_item.text().strip() if label_item else "") or Path(path).name
            key = key_item.text().strip()[:1].lower() if key_item else ""

            reason = ""
            if not key:
                reason = "aucune touche"
            elif key in seen:
                reason = f"la touche « {key} » est déjà prise"
            if reason:
                rejected.append(f"{label} : {reason}")
                continue

            seen.add(key)
            out.append({"key": key, "label": label, "path": path})

        if rejected:
            QMessageBox.warning(
                self, "Destinations ignorées",
                "Ces destinations n'ont pas été enregistrées :\n\n  · "
                + "\n  · ".join(rejected)
                + "\n\nLes touches m, o, c et z sont réservées "
                  "(son, ouvrir, configurer, annuler).",
            )
        return out


class RootBar(QWidget):
    """Entête : racine courante, avancement, accès aux réglages."""

    changeRoot = Signal()
    openSettings = Signal()
    toggleMode = Signal()
    toggleTree = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.root_label = QLabel("—", self)
        self.root_label.setObjectName("subtitle")
        self.counter = QLabel("", self)
        self.counter.setObjectName("counter")
        self.pending = QLabel("", self)
        self.pending.setObjectName("pending")
        self.pending.hide()

        change = QPushButton("Changer de racine")
        settings = QPushButton("Destinations…")
        mode = QPushButton("Mode")
        tree = QPushButton("Arborescence")
        for button in (change, settings, mode, tree):
            button.setFocusPolicy(Qt.NoFocus)
        change.clicked.connect(self.changeRoot)
        settings.clicked.connect(self.openSettings)
        mode.clicked.connect(self.toggleMode)
        tree.clicked.connect(self.toggleTree)

        layout.addWidget(self.root_label, 1)
        layout.addWidget(self.pending)
        layout.addWidget(self.counter)
        layout.addWidget(tree)
        layout.addWidget(mode)
        layout.addWidget(settings)
        layout.addWidget(change)

    def set_pending(self, count: int) -> None:
        """Rappelle discrètement que des copies se poursuivent en arrière-plan."""
        if count > 0:
            self.pending.setText(f"⟳ {count} transfert{'s' if count > 1 else ''}")
            self.pending.show()
        else:
            self.pending.hide()

