"""Mur de lecture : plusieurs vidéos verticales côte à côte, en même temps.

Trois vidéos verticales remplissent un écran large mieux qu'une seule, qui y
laisse deux bandes noires. On les regarde ensemble, on passe celles qui
n'intéressent pas, et l'on garde les autres — c'est du tri à trois voies.

Chaque panneau est indépendant : sa lecture, sa position, son remplacement. Le
mur ne pioche que dans les vidéos dont on connaît déjà la résolution, puisque
c'est elle qui dit si une vidéo est verticale ; ce vivier s'étoffe à mesure que
l'on parcourt la collection.
"""
from __future__ import annotations

import random

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

from .scan import human_duration
from .widgets import elide

# Trois panneaux : sur un ecran large, trois videos verticales le remplissent
# presque exactement. Quatre les amincissent au point qu'on ne distingue plus
# grand-chose.
DEFAULT_PANES = 3

SPLIT_STYLE = """
QFrame#splitPane { background: #0b0d10; border: 1px solid #242a33;
                   border-radius: 8px; }
QLabel#splitName { color: #b9c2cd; font-size: 12px; }
QPushButton#splitButton { background: #1a1f27; border: 1px solid #2b323d;
                          border-radius: 5px; padding: 3px 10px;
                          color: #b9c2cd; font-size: 12px; }
QPushButton#splitButton:hover { color: #ffffff; border-color: #39414d; }
QLabel#splitEmpty { color: #6f7885; font-size: 13px; }
"""


class SplitPane(QFrame):
    """Un panneau du mur : une vidéo, son avancement, et de quoi en changer."""

    RAIL_HEIGHT = 6

    wants_next = Signal(int)
    opened = Signal(str)

    def __init__(self, index: int, scroll_seconds: int = 5, parent=None):
        super().__init__(parent)
        self.setObjectName("splitPane")
        self.index = index
        self.scroll_seconds = scroll_seconds
        self.video_path = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.stage = QWidget(self)
        self.stage.setObjectName("videoArea")
        self.stage.setMinimumHeight(240)
        self.setMinimumWidth(180)
        layout.addWidget(self.stage, 1)

        self.video = QVideoWidget(self.stage)
        self.video.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # Poses sur l'image, donc enfants du cadre qui la porte : ailleurs, ils
        # passeraient derriere elle.
        self.rail = QFrame(self.stage)
        self.rail.setObjectName("playRail")
        self.rail.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.done = QFrame(self.stage)
        self.done.setObjectName("playProgress")
        self.done.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.remaining = QLabel("", self.stage)
        self.remaining.setObjectName("remaining")
        self.remaining.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.remaining.hide()

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        self.name = QLabel("—", self)
        self.name.setObjectName("splitName")
        # Sans cela, un nom de fichier long imposait sa largeur au panneau, donc
        # au mur, donc a la fenetre entiere, qui ne pouvait plus retrecir.
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bar.addWidget(self.name, 1)
        for text, tip, slot in (
            ("⇄", "Une autre vidéo dans ce panneau", self._next),
            ("⤢", "Ouvrir cette vidéo", self._open),
        ):
            button = QPushButton(text, self)
            button.setObjectName("splitButton")
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(slot)
            bar.addWidget(button)
        layout.addLayout(bar)

        # Pas de sortie audio : trois videos qui parlent en meme temps ne
        # s'ecoutent pas, elles se regardent.
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_position)
        self.player.mediaStatusChanged.connect(self._on_status)

    # -- contenu ---------------------------------------------------------
    def play(self, path: str) -> None:
        self.video_path = path
        self.name.setText(elide(path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1], 28))
        self.name.setToolTip(path)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self._place()

    def clear(self) -> None:
        self.video_path = ""
        self.name.setText("—")
        self.player.stop()
        self.player.setSource(QUrl())
        self.remaining.hide()

    def stop(self) -> None:
        self.player.stop()

    def _next(self) -> None:
        self.wants_next.emit(self.index)

    def _open(self) -> None:
        if self.video_path:
            self.opened.emit(self.video_path)

    # -- avancement ------------------------------------------------------
    def _place(self) -> None:
        area = self.stage.rect()
        self.video.setGeometry(area)
        top = area.bottom() - self.RAIL_HEIGHT
        self.rail.setGeometry(0, top, area.width(), self.RAIL_HEIGHT)
        self.rail.raise_()
        self.done.raise_()
        self.remaining.raise_()

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            # Une video finie laisse la place a une autre : le mur ne doit pas
            # se figer sur une image d'arret.
            self.wants_next.emit(self.index)

    def _on_position(self, position: int) -> None:
        duration = self.player.duration()
        area = self.stage.rect()
        fraction = (position / duration) if duration > 0 else 0.0
        top = area.bottom() - self.RAIL_HEIGHT
        self.rail.setGeometry(0, top, area.width(), self.RAIL_HEIGHT)
        self.done.setGeometry(
            0, top, max(0, int(area.width() * max(0.0, min(1.0, fraction)))),
            self.RAIL_HEIGHT,
        )
        self.rail.show()
        self.done.show()
        self.rail.raise_()
        self.done.raise_()
        if duration > 0:
            left = max(0, duration - position) / 1000.0
            self.remaining.setText(f"−{human_duration(left)}")
            self.remaining.adjustSize()
            self.remaining.move(area.right() - self.remaining.width() - 10, 10)
            self.remaining.raise_()
            self.remaining.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place()

    # -- gestes ----------------------------------------------------------
    def wheelEvent(self, event):
        """La molette avance ou recule dans **cette** vidéo, pas dans les autres."""
        notches = event.angleDelta().y() / 120.0
        if not notches or not self.video_path:
            return super().wheelEvent(event)
        step = int(notches * self.scroll_seconds * 1000)
        duration = self.player.duration()
        target = self.player.position() - step
        if duration > 0:
            target = max(0, min(duration - 1000, target))
        self.player.setPosition(max(0, target))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.video_path:
            # Un clic sur l'image met en pause ou reprend : on regarde trois
            # videos, il faut pouvoir en retenir une sans perdre les autres.
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SplitWall(QWidget):
    """Le mur entier : quelques panneaux, et un vivier où puiser."""

    opened = Signal(str)

    def __init__(self, panes: int = DEFAULT_PANES, scroll_seconds: int = 5,
                 parent=None):
        super().__init__(parent)
        self.setStyleSheet(SPLIT_STYLE)
        self.pool: list = []
        self.shown: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        self.empty = QLabel(
            "Aucune vidéo verticale connue pour l'instant.\n\n"
            "Le mur ne puise que dans les vidéos dont la résolution a déjà été "
            "relevée : parcourez les vignettes, le vivier se remplit tout seul.",
            self,
        )
        self.empty.setObjectName("splitEmpty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        # Une explication de trois lignes ne doit pas dicter la largeur du mur,
        # ni celle de la fenetre qui le contient.
        self.empty.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        outer.addWidget(self.empty)

        self.row = QWidget(self)
        row_layout = QHBoxLayout(self.row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        self.panes: list = []
        for index in range(panes):
            pane = SplitPane(index, scroll_seconds, self.row)
            pane.wants_next.connect(self.refill_one)
            pane.opened.connect(self.opened)
            row_layout.addWidget(pane, 1)
            self.panes.append(pane)
        self.row.hide()
        outer.addWidget(self.row, 1)

    # -- vivier ----------------------------------------------------------
    def set_pool(self, videos: list) -> None:
        """Remplace le vivier et relance les panneaux."""
        self.pool = [str(video) for video in videos]
        self.shuffle_all()

    def shuffle_all(self) -> None:
        has = bool(self.pool)
        self.empty.setVisible(not has)
        self.row.setVisible(has)
        if not has:
            for pane in self.panes:
                pane.clear()
            return
        picks = random.sample(self.pool, min(len(self.panes), len(self.pool)))
        self.shown = list(picks)
        for index, pane in enumerate(self.panes):
            if index < len(picks):
                pane.play(picks[index])
            else:
                pane.clear()

    def refill_one(self, index: int) -> None:
        """Remplace la vidéo d'un seul panneau, sans toucher aux autres."""
        if not self.pool or not (0 <= index < len(self.panes)):
            return
        # On evite de reposer ce qui est deja a l'ecran, tant qu'il y a le choix.
        busy = {pane.video_path for pane in self.panes if pane.video_path}
        choices = [video for video in self.pool if video not in busy] or self.pool
        self.panes[index].play(random.choice(choices))

    def stop(self) -> None:
        for pane in self.panes:
            pane.stop()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.stop()
