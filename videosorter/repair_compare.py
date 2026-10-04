"""Avant / apres : l'originale et la reparee cote a cote, d'un passage abime
a l'autre.

« Regarder » ouvrait la reparee dans la fiche, derriere la fenetre de
reparation -- et seulement si une ligne etait choisie. On ne pouvait donc ni
la voir, ni juger ce qu'elle valait.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
)

from . import repair

STYLE = """
QDialog { background: #0e1116; }
QLabel { color: #c9d1db; }
QLabel#cmpSide { color: #ffffff; font-size: 13px; font-weight: 600; }
QLabel#cmpWhere { color: #f5c542; font-size: 13px; font-weight: 600; }
QPushButton { background: #232a34; border: 1px solid #364050; border-radius: 6px;
              padding: 6px 12px; color: #eef1f4; }
QPushButton:hover { background: #2c3541; }
QPushButton:disabled { color: #6f7a87; background: #1a1f27; border-color: #262d37; }
QSlider::groove:horizontal { height: 6px; background: #2a2f38; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #2f6fed; border-radius: 3px; }
QSlider::handle:horizontal { width: 14px; margin: -5px 0; border-radius: 7px;
                             background: #e9eef4; }
"""

# On arrive un peu avant le degat, pour voir l'image saine puis ce qu'en
# fait la reparation.
LEAD_S = 1.5


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class RepairCompare(QDialog):
    """`synced` : la reparee a la meme duree (figer, masquer, reconstituer) --
    les deux avancent ensemble. Couper raccourcit la video : chacune pour soi."""

    def __init__(self, parent, original: str, repaired: str = "",
                 report: dict | None = None, synced: bool = True):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle(f"Avant / après — {Path(original).name}")
        self.setStyleSheet(STYLE)
        self.resize(1280, 640)
        self.spans = repair.damaged_spans(report) if report and report.get("errors") else []
        self.synced = synced and bool(repaired)
        self.at = -1
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(8)

        sides = QHBoxLayout()
        sides.setSpacing(10)
        self.players = []
        self.sound = None
        pairs = [("Originale", original)]
        if repaired:
            pairs.append(("Réparée", repaired))
        for title, path in pairs:
            column = QVBoxLayout()
            label = QLabel(title, self)
            label.setObjectName("cmpSide")
            label.setToolTip(path)
            column.addWidget(label)
            view = QVideoWidget(self)
            view.setStyleSheet("background: #000;")
            column.addWidget(view, 1)
            player = QMediaPlayer(self)
            player.setVideoOutput(view)
            # Le son de la reparee seulement (sinon de l'originale) : deux
            # pistes melees, on n'entend rien.
            if path == pairs[-1][1]:
                self.sound = QAudioOutput(self)
                self.sound.setVolume(0.8)
                player.setAudioOutput(self.sound)
            player.setSource(QUrl.fromLocalFile(path))
            self.players.append(player)
            sides.addLayout(column, 1)
        box.addLayout(sides, 1)

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._scrub)
        box.addWidget(self.slider)

        row = QHBoxLayout()
        self.play = QPushButton("⏸ Pause", self)
        self.play.clicked.connect(self._toggle)
        row.addWidget(self.play)
        self.prev = QPushButton("◂ Passage abîmé précédent", self)
        self.prev.clicked.connect(lambda: self._jump(-1))
        row.addWidget(self.prev)
        self.next = QPushButton("Passage abîmé suivant ▸", self)
        self.next.clicked.connect(lambda: self._jump(1))
        row.addWidget(self.next)
        self.where = QLabel("", self)
        self.where.setObjectName("cmpWhere")
        row.addWidget(self.where, 1)
        close = QPushButton("Fermer", self)
        close.clicked.connect(self.close)
        row.addWidget(close)
        box.addLayout(row)
        for button in (self.prev, self.next):
            button.setEnabled(bool(self.spans) and self.synced)
        if not self.spans:
            self.where.setText("Aucun passage abîmé relevé.")
        elif not self.synced and repaired:
            self.where.setText("Coupée : la réparée est plus courte, chacune avance "
                               "pour soi.")

        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        for player in self.players:
            player.play()
        if self.spans and (self.synced or not repaired):
            QTimer.singleShot(600, lambda: self._jump(1))

    def _main(self) -> QMediaPlayer:
        return self.players[-1]

    def _seek(self, ms: int) -> None:
        if self.synced or len(self.players) == 1:
            for player in self.players:
                player.setPosition(max(0, ms))
        else:
            self._main().setPosition(max(0, ms))

    def _jump(self, step: int) -> None:
        if not self.spans:
            return
        self.at = max(0, min(len(self.spans) - 1, self.at + step))
        start, end = self.spans[self.at]
        self._seek(int((start - LEAD_S) * 1000))
        self.where.setText(f"Passage abîmé {self.at + 1} / {len(self.spans)} : "
                           f"{_clock(start)} → {_clock(end)}")
        for player in self.players:
            player.play()
        self.play.setText("⏸ Pause")

    def _toggle(self) -> None:
        playing = self._main().playbackState() == QMediaPlayer.PlayingState
        for player in self.players:
            player.pause() if playing else player.play()
        self.play.setText("▶ Lecture" if playing else "⏸ Pause")

    def _scrub(self, value: int) -> None:
        duration = self._main().duration()
        if duration > 0:
            self._seek(int(duration * value / 1000))

    def _tick(self) -> None:
        main = self._main()
        duration = main.duration()
        if duration > 0 and not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(int(main.position() * 1000 / duration))
            self.slider.blockSignals(False)
        if self.synced and len(self.players) == 2:
            # Les deux lecteurs derivent un peu : on recale l'originale.
            other = self.players[0]
            if abs(other.position() - main.position()) > 250:
                other.setPosition(main.position())

    def closeEvent(self, event):
        self.timer.stop()
        for player in self.players:
            player.stop()
            player.setSource(QUrl())
        super().closeEvent(event)
