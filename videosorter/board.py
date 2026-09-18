"""Vue planche : les éléments en cartes, pour parcourir plutôt que décider.

Ce n'est pas un second logiciel mais une autre présentation du même contenu. La
racine, le filtre, l'arborescence et les raccourcis de destination restent ceux
du tri : seules changent la densité — vingt éléments au lieu d'un — et
l'intention, puisqu'un clic ouvre au lieu d'envoyer.
"""
from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from .scan import MODE_FOLDERS, human_duration, human_resolution, human_size
from .widgets import StarStrip, elide

# Densites proposees : moins de colonnes, donc des cartes plus grandes.
COLUMN_CHOICES = (2, 3, 4, 5, 6, 7, 8, 9, 10)
DEFAULT_COLUMNS = 5
CARD_GAP = 10
MIN_CARD_WIDTH = 150
# Une carte coute cher a construire : six cents d'un coup prenaient plusieurs
# secondes. On n'en batit qu'une page, et l'on tourne les pages.
PAGE_SIZE = 60


class BoardCard(QFrame):
    """Un élément de la planche : image, nom, chiffres, note."""

    opened = Signal(int)
    rated = Signal(int, int)
    played = Signal(int)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.setObjectName("boardCard")
        self.setProperty("hovered", "false")
        self.index = index
        self.video: str = ""
        self.ts: float = 0.0
        self.item = None
        self._pixmap: QPixmap | None = None
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)

        self.image = QLabel(self)
        self.image.setObjectName("boardImage")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumHeight(110)
        self.image.setText("…")
        layout.addWidget(self.image)

        self.name = QLabel("", self)
        self.name.setObjectName("boardName")
        layout.addWidget(self.name)

        # Seconde ligne : ce qui qualifie la video a gauche, la note a droite.
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(8)
        self.meta = QLabel("", self)
        self.meta.setObjectName("boardMeta")
        self.size_label = QLabel("", self)
        self.size_label.setObjectName("boardMeta")
        self.stars = StarStrip(16, self)
        self.stars.rated.connect(lambda value: self.rated.emit(self.index, value))
        bottom.addWidget(self.meta)
        bottom.addStretch(1)
        bottom.addWidget(self.size_label)
        bottom.addWidget(self.stars)
        layout.addLayout(bottom)

        self.duration_chip = QLabel("", self)
        self.duration_chip.setObjectName("tileDuration")
        self.duration_chip.hide()

        # Sans cela, un clic tombant sur l'image ou le texte n'atteindrait pas
        # la carte : seules ses marges auraient repondu.
        for child in (self.image, self.name, self.meta, self.size_label,
                      self.duration_chip):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_item(self, item, stars: int) -> None:
        self.item = item
        self.video = ""
        self._pixmap = None
        self.image.setPixmap(QPixmap())
        self.image.setText("…")
        self.name.setText(elide(item.name, 30))
        self.name.setToolTip(str(item.path))
        if item.kind == MODE_FOLDERS:
            pieces = [f"{item.video_count} vidéo{'s' if item.video_count > 1 else ''}"]
            if item.subdir_count:
                pieces.append(f"{item.subdir_count} dossier(s)")
        else:
            pieces = []
        self.meta.setText("   ·   ".join(pieces))
        self.size_label.setText(human_size(item.size))
        self.stars.set_value(stars)
        self.duration_chip.hide()
        self.set_state(item.status)

    def set_state(self, status: str) -> None:
        marks = {"moved": "rangé", "deleted": "écarté", "skipped": "passé"}
        self.setProperty("state", marks.get(status, ""))
        self.style().unpolish(self)
        self.style().polish(self)

    def set_source(self, video: str, ts: float, duration: float = 0.0,
                   height: int = 0) -> None:
        self.video = video
        self.ts = ts
        resolution = human_resolution(height)
        if resolution:
            current = self.meta.text()
            if resolution not in current:
                self.meta.setText(
                    f"{resolution}   ·   {current}" if current else resolution
                )
        if duration:
            self.duration_chip.setText(human_duration(duration))
            self.duration_chip.adjustSize()
            self.duration_chip.move(
                self.width() - self.duration_chip.width() - 14, 14
            )
            self.duration_chip.raise_()
            self.duration_chip.show()

    def set_thumb(self, path: str) -> None:
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        self._pixmap = pixmap
        self.image.setText("")
        self._rescale()

    def set_card_width(self, width: int) -> None:
        """Fixe la largeur, l'image gardant un cadre 16:9."""
        self.setFixedWidth(width)
        self.image.setFixedHeight(int((width - 16) * 9 / 16))
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap is None:
            return
        self.image.setPixmap(self._pixmap.scaled(
            self.image.width(), self.image.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        ))

    def set_hovered(self, hovered: bool) -> None:
        self.setProperty("hovered", "true" if hovered else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def mouseReleaseEvent(self, event):
        # La bande d'étoiles gère ses propres clics ; ailleurs, on ouvre.
        if event.button() == Qt.LeftButton and not self.stars.geometry().contains(
            event.position().toPoint()
        ):
            self.opened.emit(self.index)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.video:
            self.played.emit(self.index)


class BoardView(QWidget):
    """Grille défilante de cartes, avec lecture au survol."""

    openRequested = Signal(int)
    rateRequested = Signal(int, int)
    previewNeeded = Signal(int)
    playRequested = Signal(str, float)
    pageChanged = Signal(int, int, int)   # premier, dernier, total

    def __init__(self, preview_seconds: int = 10, columns: int = DEFAULT_COLUMNS,
                 parent=None):
        super().__init__(parent)
        self.preview_seconds = preview_seconds
        self.columns = columns
        self.items: list = []
        self.cards: list = []
        self.page = 0
        self.hovered = -1          # rang de la carte survolee, dans la page
        self._stars_of = lambda _path: 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("boardScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.canvas = QWidget()
        self.grid = QGridLayout(self.canvas)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.canvas)
        outer.addWidget(self.scroll)

        self.empty = QLabel("Rien à afficher ici.", self)
        self.empty.setObjectName("hint")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.hide()
        outer.addWidget(self.empty)

        self.video = QVideoWidget(self.canvas)
        self.video.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.video.hide()

        # Temps restant de l'extrait survole, comme dans la grille d'apercus.
        self.remaining = QLabel("", self.canvas)
        self.remaining.setObjectName("remaining")
        self.remaining.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.remaining.hide()
        self.audio = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        self.player.setAudioOutput(self.audio)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.positionChanged.connect(self._on_position)
        self.player.errorOccurred.connect(self._on_error)
        self._pending_seek = 0
        self._segment_start = 0
        self.unplayable: set = set()

        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(80)
        self.hover_timer.timeout.connect(self._poll_hover)

    # -- contenu ---------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self.hover_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.hover_timer.stop()
        self.stop()

    def set_muted(self, muted: bool) -> None:
        self.audio.setMuted(muted)

    def _ensure_cards(self, count: int) -> None:
        while len(self.cards) < count:
            card = BoardCard(len(self.cards), self.canvas)
            card.opened.connect(self.openRequested)
            card.rated.connect(self.rateRequested)
            card.played.connect(self._play_full)
            self.cards.append(card)

    def set_items(self, items: list, stars_of) -> None:
        """Remplit la planche, en ne refaisant que ce qui a changé.

        Les cartes dont l'élément n'a pas bougé gardent leur image : tout
        reconstruire ferait clignoter la planche et redemanderait des vignettes
        déjà obtenues.
        """
        self.stop()
        previous = self.items
        same_head = bool(previous) and bool(items) and previous[:1] == items[:1]
        self.items = list(items)
        if not same_head:
            self.page = 0
        self.empty.setVisible(not self.items)
        self.scroll.setVisible(bool(self.items))
        self._stars_of = stars_of
        self._fill_page(previous)

    def _page_bounds(self) -> tuple:
        first = self.page * PAGE_SIZE
        return first, min(first + PAGE_SIZE, len(self.items))

    def _fill_page(self, previous=None) -> None:
        first, last = self._page_bounds()
        shown = last - first
        self._ensure_cards(shown)
        width = self._card_width()
        needed = []
        for slot, card in enumerate(self.cards):
            if slot >= shown:
                self.grid.removeWidget(card)
                card.hide()
                continue
            position = first + slot
            item = self.items[position]
            unchanged = (previous is not None and position < len(previous)
                         and previous[position] is item and card.video
                         and card.index == position)
            card.index = position
            if card.width() != width:
                card.set_card_width(width)
            if not unchanged:
                card.set_item(item, self._stars_of(item.path))
                needed.append(position)
            else:
                card.stars.set_value(self._stars_of(item.path))
                card.set_state(item.status)
            self.grid.addWidget(card, slot // self.columns, slot % self.columns)
            card.show()
        self.pageChanged.emit(first + 1 if self.items else 0, last, len(self.items))
        for position in needed:
            self.previewNeeded.emit(position)

    def total_pages(self) -> int:
        return max(1, -(-len(self.items) // PAGE_SIZE))

    def set_page(self, page: int) -> None:
        page = max(0, min(page, self.total_pages() - 1))
        if page == self.page:
            return
        self.stop()
        self.page = page
        self.scroll.verticalScrollBar().setValue(0)
        self._fill_page()

    def append_item(self, item, stars: int) -> None:
        """Ajoute une carte sans toucher aux autres, pendant que l'analyse avance."""
        position = len(self.items)
        self.items.append(item)
        first, _last = self._page_bounds()
        if not first <= position < first + PAGE_SIZE:
            # Hors de la page regardee : rien a batir, on met juste le compte a jour.
            self.pageChanged.emit(first + 1, first + PAGE_SIZE, len(self.items))
            return
        slot = position - first
        self._ensure_cards(slot + 1)
        card = self.cards[slot]
        card.index = position
        card.set_card_width(self._card_width())
        card.set_item(item, stars)
        self.grid.addWidget(card, slot // self.columns, slot % self.columns)
        card.show()
        self.empty.hide()
        self.scroll.show()
        self.pageChanged.emit(first + 1, position + 1, len(self.items))
        self.previewNeeded.emit(position)

    def _card_for(self, position: int):
        """Carte montrant cet element, ou None s'il n'est pas sur la page vue."""
        first, last = self._page_bounds()
        if not first <= position < last:
            return None
        slot = position - first
        return self.cards[slot] if slot < len(self.cards) else None

    def set_stars(self, position: int, stars: int) -> None:
        card = self._card_for(position)
        if card is not None:
            card.stars.set_value(stars)

    def set_state(self, position: int, status: str) -> None:
        card = self._card_for(position)
        if card is not None:
            card.set_state(status)

    def set_source(self, position: int, entry) -> None:
        card = self._card_for(position)
        if card is not None:
            card.set_source(*entry)

    def set_thumb(self, position: int, path: str) -> None:
        card = self._card_for(position)
        if card is not None:
            card.set_thumb(path)

    def _play_full(self, position: int) -> None:
        card = self._card_for(position)
        if card is not None and card.video:
            self.playRequested.emit(card.video, card.ts)

    def random_index(self) -> int:
        candidates = [i for i, item in enumerate(self.items) if not item.status]
        return random.choice(candidates) if candidates else -1

    def scroll_to(self, position: int) -> None:
        if not 0 <= position < len(self.items):
            return
        self.set_page(position // PAGE_SIZE)
        card = self._card_for(position)
        if card is not None:
            self.scroll.ensureWidgetVisible(card, 40, 40)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.items:
            self._relayout()

    def _card_width(self) -> int:
        available = self.scroll.viewport().width() - CARD_GAP * (self.columns + 1)
        return max(MIN_CARD_WIDTH, available // max(1, self.columns))

    def set_columns(self, columns: int) -> None:
        self.columns = max(1, columns)
        self._relayout()

    def _relayout(self) -> None:
        first, last = self._page_bounds()
        width = self._card_width()
        for slot, card in enumerate(self.cards[:last - first]):
            self.grid.removeWidget(card)
            card.set_card_width(width)
            self.grid.addWidget(card, slot // self.columns, slot % self.columns)

    # -- survol et lecture ----------------------------------------------
    def _poll_hover(self) -> None:
        if not self.isVisible() or not self.window().isActiveWindow():
            return
        cursor = QCursor.pos()
        first, last = self._page_bounds()
        found = -1
        for position, card in enumerate(self.cards[:last - first]):
            if card.isVisible() and card.rect().contains(card.mapFromGlobal(cursor)):
                found = position
                break
        if found == self.hovered:
            return
        if self.hovered != -1 and self.hovered < len(self.cards):
            self.cards[self.hovered].set_hovered(False)
        self.video.hide()
        self.remaining.hide()
        self.hovered = found
        if found == -1:
            self.stop()
            return
        self.cards[found].set_hovered(True)
        self._play(found)

    def _play(self, position: int) -> None:
        card = self.cards[position]
        if not card.video or card.video in self.unplayable:
            self.video.hide()
            return
        area = card.image
        origin = area.mapTo(self.canvas, QPoint(0, 0))
        self.video.setGeometry(origin.x(), origin.y(), area.width(), area.height())
        self.video.raise_()

        self._segment_start = int(card.ts * 1000)
        self._pending_seek = self._segment_start
        url = QUrl.fromLocalFile(card.video)
        if self.player.source() == url:
            self.player.setPosition(self._segment_start)
            self.video.show()
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
            # L'image n'est devoilee qu'une fois prete : la vignette reste
            # visible jusque-la, au lieu d'un rectangle noir.
            if self.hovered != -1:
                self.video.show()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(self._segment_start)
            self.player.play()

    def _on_position(self, position: int) -> None:
        if self.hovered == -1:
            self.remaining.hide()
            return
        if position > self._segment_start + self.preview_seconds * 1000:
            self.player.setPosition(self._segment_start)
        duration = self.player.duration()
        if duration > 0 and self.hovered < len(self.cards):
            card = self.cards[self.hovered]
            left = max(0, duration - position) / 1000.0
            self.remaining.setText(f"−{human_duration(left)}")
            self.remaining.adjustSize()
            origin = card.image.mapTo(self.canvas, QPoint(0, 0))
            self.remaining.move(
                origin.x() + card.image.width() - self.remaining.width() - 8,
                origin.y() + 8,
            )
            self.remaining.raise_()
            self.remaining.show()

    def _on_error(self, *_args) -> None:
        if 0 <= self.hovered < len(self.cards) and self.cards[self.hovered].video:
            self.unplayable.add(self.cards[self.hovered].video)
        self.video.hide()
        self.player.stop()

    def stop(self) -> None:
        self.player.stop()
        self.video.hide()
        self.remaining.hide()
        for card in self.cards:
            card.set_hovered(False)
        self.hovered = -1
