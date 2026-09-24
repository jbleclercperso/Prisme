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

from PySide6.QtCore import QPoint, QRect, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .perf import mark
from .icons import dress, icon
from .widgets import OverBar, PeekOverlay, PlayMarks, Stepper

# Trois panneaux : sur un ecran large, trois videos verticales le remplissent
# presque exactement. Quatre les amincissent au point qu'on ne distingue plus
# grand-chose.
DEFAULT_PANES = 3
# Delai entre deux demarrages de panneaux.
STAGGER_MS = 650
# Les nombres qui font un rectangle. Cinq ou sept n'en font pas.
PANE_CHOICES = (2, 3, 4, 5, 6, 8, 9, 10)
ORIENTATIONS = (("vertical", "Verticales"), ("horizontal", "Horizontales"),
                ("any", "Toutes"))


def grid_for(count: int, orientation: str, width: int = 0,
             height: int = 0) -> tuple:
    """(rangees, colonnes) qui montrent le plus d'image possible.

    Une table figee donnait quatre videos sur deux rangees de deux, meme sur
    un ecran large ou elles tenaient en ligne — la moitie de la place partait
    en bandes noires. On essaie donc chaque disposition et l'on garde celle
    dont les images occupent la plus grande surface, la forme des videos
    etant connue : debout pour des verticales, couchee sinon.

    Sans dimensions, on retombe sur un partage raisonnable : c'est le cas au
    tout premier affichage, avant que la fenetre n'ait sa taille.
    """
    count = max(1, count)
    if width <= 0 or height <= 0:
        rows = 1 if (orientation == "vertical" and count <= 5) else \
            {2: 1, 3: 1, 4: 2, 5: 1, 6: 2, 8: 2, 9: 3, 10: 2}.get(count, 2)
        return rows, -(-count // rows)

    shape = 9 / 16 if orientation == "vertical" else 16 / 9
    best = (1, count)
    seen = -1.0
    for rows in range(1, count + 1):
        cols = -(-count // rows)
        if rows * cols - count >= cols:
            continue          # une rangee resterait vide
        cell_w = width / cols
        cell_h = height / rows
        # L'image garde sa forme dans sa case : c'est la plus petite des deux
        # contraintes qui decide.
        shown_w = min(cell_w, cell_h * shape)
        shown_h = min(cell_h, cell_w / shape)
        area = shown_w * shown_h * count
        if area > seen:
            seen = area
            best = (rows, cols)
    return best

SPLIT_STYLE = """
QFrame#splitPane { background: #0b0d10; border: 1px solid #242a33;
                   border-radius: 8px; }
QLabel#splitName { color: #b9c2cd; font-size: 12px; }
QPushButton#splitButton { background: #1a1f27; border: 1px solid #2b323d;
                          border-radius: 5px; padding: 3px 10px;
                          color: #b9c2cd; font-size: 12px; }
/* Les gestes d'un panneau : le cadre garde sa taille, le signe la remplit.
   A douze points, on ne distinguait pas la fleche du de. */
QPushButton#paneGesture { background: #1a1f27; border: 1px solid #2b323d;
                          border-radius: 5px; padding: 0; margin: 0;
                          color: #cdd5df; font-size: 19px; line-height: 19px; }
QPushButton#paneGesture:hover { color: #ffffff; border-color: #5a6474;
                                background: #242a33; }
QLabel#paneTime { color: rgba(255,255,255,0.72); font-size: 11px;
                  font-weight: 500; background: transparent; }
QPushButton#splitButton:hover { color: #ffffff; border-color: #39414d; }
QLabel#splitEmpty { color: #6f7885; font-size: 13px; }
QPushButton#splitButton[chosen="true"] { color: #ffffff; background: #242a33;
                                          border-color: #5a6474; }
"""


class SplitPane(QFrame):
    """Un panneau du mur : une vidéo, son avancement, et de quoi en changer."""

    RAIL_HEIGHT = 6

    wants_next = Signal(int)          # une autre, au hasard, n'importe ou
    wants_sibling = Signal(int, str)  # la suivante du meme dossier
    opened = Signal(str)
    peekRequested = Signal(int, str)  # Maj + clic droit : les neuf instants
    sortRequested = Signal(int, str)  # clic droit : les destinations, pour ranger
    peekChosen = Signal(int, int)     # une case cliquee : (panneau, case)
    soloRequested = Signal(int)       # cette video seule, sur tout le mur
    stayToggled = Signal(bool)        # la coche « rester dans ce dossier »

    def __init__(self, index: int, scroll_seconds: int = 5, parent=None):
        super().__init__(parent)
        self.setObjectName("splitPane")
        self.index = index
        self.scroll_seconds = scroll_seconds
        self.video_path = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(0)

        self.stage = QWidget(self)
        self.stage.setObjectName("videoArea")
        self.stage.setMinimumHeight(140)
        self.setMinimumWidth(140)
        layout.addWidget(self.stage, 1)

        self.video = QVideoWidget(self.stage)
        self.video.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # Le widget video de Windows avale les gestes : on les lui reprend.
        self.video.mouseReleaseEvent = self.mouseReleaseEvent
        self.video.wheelEvent = self.wheelEvent
        self.peek = PeekOverlay(self.stage)
        self.peek.chosen.connect(lambda slot: self.peekChosen.emit(self.index, slot))
        self.peeking = False

        # Au repos, un trait tres fin en bas de l'image et le temps restant,
        # discret, en haut a droite. Au survol, le bandeau complet — nom et
        # gestes — pose sur l'image. Toute la hauteur du panneau va a la
        # video : la barre du dessous lui prenait trente pixels chacun.
        self.marks = PlayMarks(self, rail=True, left=True)
        self.bare = False
        self.bar = OverBar(self)
        self.bar.left.hide()          # le temps restant est deja en haut a droite
        self.name = self.bar.name
        self.name.setText("—")
        # Le bandeau pilote tout : revenir a la precedente, pause, la
        # suivante du meme dossier, une autre au hasard, sa fiche, et elle
        # seule en grand.
        self.history: list = []
        self.stay = False
        for text, tip, slot in (
            ("◂", "La précédente, dans ce panneau", self._previous),
            ("⏯", "Pause, ou reprendre", self.toggle_pause),
            ("▸", "Une autre : au hasard, ou dans ce dossier si la case est cochée",
             self._forward),
            ("⤢", "Ouvrir cette vidéo dans sa fiche", self._open),
            ("⛶", "Cette vidéo seule, en grand — Échap pour revenir",
             self._solo),
        ):
            self.bar.add_gesture(text, tip, slot)
        self.pause_button = self.bar.buttons.itemAt(1).widget()
        self.bar.add_stay("Rester dans ce dossier : ▸ prend la suivante du "
                          "même dossier au lieu d'une vidéo au hasard",
                          False, self.stayToggled)
        self.hovered = False

        # Le son ne vient que de la video survolee : six videos qui parlent
        # en meme temps ne s'ecoutent pas.
        # Pas de sortie audio a soi : le mur n'en a qu'une, branchee sur la
        # video survolee. Dix sorties ouvertes a la fois pesaient sur le
        # systeme pour n'en faire entendre qu'une.
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        self.player.playbackStateChanged.connect(self._show_pause)
        self._show_pause()
        self.player.positionChanged.connect(self._on_position)
        self.player.mediaStatusChanged.connect(self._on_status)

    # -- contenu ---------------------------------------------------------
    def play(self, path: str, remember: bool = True) -> None:
        mark(f"wall.play {path[-40:]}")
        if remember and self.video_path and self.video_path != path:
            self.history = (self.history + [self.video_path])[-30:]
        self.video_path = path
        self.bar.set_name(path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1])
        self.name.setToolTip(path)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self._place()

    def set_bare(self, bare: bool) -> None:
        """Le bandeau ne vient plus qu'au survol, plein ecran ou non."""
        self.bare = bare

    def watch(self, hovered: bool, muted: bool) -> None:
        """Appele par le mur a chaque battement : bandeau et trait."""
        self.hovered = hovered
        if (not self.isVisible() or not self.video_path or self.peeking
                or not self.stage.isVisible()):
            self.bar.hide()
            self.marks.hide()
            return
        if hovered:
            self.marks.with_rail = False
            self.bar.place_on(self.stage)
            self.bar.reveal()
        else:
            self.marks.with_rail = True
            self.bar.hide()
        self.marks.place_on(self.stage)
        self.marks._lay_out()

    def hide_overlays(self) -> None:
        self.bar.hide()
        self.marks.hide()

    def clear(self) -> None:
        self.peek_end()
        self.video_path = ""
        self.name.setText("—")
        self.player.stop()
        self.player.setSource(QUrl())
        self.marks.clear()
        self.bar.hide()

    def stop(self) -> None:
        self.player.stop()

    def _next(self) -> None:
        self.wants_next.emit(self.index)

    def _forward(self) -> None:
        """▸ : une autre au hasard, ou la suivante du meme dossier."""
        if self.stay:
            self._sibling()
        else:
            self._next()

    def set_stay(self, on: bool) -> None:
        self.stay = bool(on)
        self.bar.stay.blockSignals(True)
        self.bar.stay.setChecked(self.stay)
        self.bar.stay.blockSignals(False)

    def _previous(self) -> None:
        """Revient a la video d'avant, dans ce panneau."""
        if self.history:
            self.play(self.history.pop(), remember=False)

    def toggle_pause(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _show_pause(self, *_args) -> None:
        playing = (self.player.playbackState()
                   == QMediaPlayer.PlaybackState.PlayingState)
        self.pause_button.setIcon(icon("pause" if playing else "play"))

    def _sibling(self) -> None:
        if self.video_path:
            self.wants_sibling.emit(self.index, self.video_path)

    def _open(self) -> None:
        if self.video_path:
            self.opened.emit(self.video_path)

    def _solo(self) -> None:
        self.soloRequested.emit(self.index)

    # -- avancement ------------------------------------------------------
    def _place(self) -> None:
        area = self.stage.rect()
        self.video.setGeometry(area)
        self.peek.setGeometry(area)

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            # Une video finie laisse la place a une autre : le mur ne doit pas
            # se figer sur une image d'arret.
            self.wants_next.emit(self.index)

    def _on_position(self, position: int) -> None:
        duration = self.player.duration()
        self.marks.set_progress(position, duration)
        self.bar.set_progress(position, duration)

    # -- les neuf instants ----------------------------------------------------
    def peek_begin(self, captions: list) -> None:
        self.peeking = True
        self.hide_overlays()
        self.peek.reset(captions)
        self.peek.setGeometry(self.stage.rect())
        self.video.hide()
        self.peek.show()
        self.peek.raise_()

    def peek_end(self) -> None:
        if not self.peeking:
            return
        self.peeking = False
        self.peek.hide()
        if self.video_path:
            self.video.show()

    def seek(self, seconds: float) -> None:
        self.player.setPosition(int(seconds * 1000))
        self.player.play()

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
        if event.button() == Qt.RightButton and self.video_path:
            # Le clic droit sert a ranger : ce sont les destinations qui
            # apparaissent, comme sur la fiche. Les neuf instants restent
            # accessibles, mais avec Maj — on les consulte, on ne les
            # utilise pas pour decider.
            if event.modifiers() & Qt.ShiftModifier:
                self.peekRequested.emit(self.index, self.video_path)
            else:
                self.sortRequested.emit(self.index, self.video_path)
            event.accept()
            return
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

    STAGGER_MS = STAGGER_MS

    opened = Signal(str)
    siblingRequested = Signal(int, str)
    countChanged = Signal(int)
    orientationChanged = Signal(str)
    fullscreenRequested = Signal()
    exitRequested = Signal()
    unseenToggled = Signal(bool)
    peekRequested = Signal(int, str)
    peekChosen = Signal(int, int)
    sortRequested = Signal(int, str)

    def __init__(self, panes: int = DEFAULT_PANES, scroll_seconds: int = 5,
                 parent=None, orientation: str = "vertical"):
        super().__init__(parent)
        self.setStyleSheet(SPLIT_STYLE)
        self.pool: list = []
        self.shown: list = []
        self.scroll_seconds = scroll_seconds
        self.orientation = orientation
        self.solo = -1                 # rang du panneau seul en grand, ou -1
        # La forme des cases, quand on la connait mieux que le reglage : des
        # videos choisies a la main, horizontales, n'ont rien a faire dans
        # des cases debout.
        self.shape = None
        # « Rester dans ce dossier », pour tous les panneaux a la fois.
        self.stay = False
        # Une poignee de videos choisies a la main : une seule rangee.
        self.single_row = False

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

        # La taille du vivier, dite au-dessus des panneaux : on veut savoir
        # dans combien de videos le hasard pioche, surtout apres un filtre.
        # Une ligne de reglages : combien, lesquelles, et le plein ecran.
        self.controls = QWidget(self)
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        self.caption = QLabel("", self.controls)
        self.caption.setObjectName("splitName")
        self.caption.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.caption.hide()
        controls.addStretch(1)
        self.count_buttons: dict = {}
        self.count_stepper = Stepper(PANE_CHOICES, "Vidéos à la fois", "",
                                     self.controls)
        self.count_stepper.chosen.connect(self.countChanged)
        controls.addWidget(self.count_stepper)
        controls.addSpacing(10)
        # Un seul bouton qui tourne : Verticales → Horizontales → Toutes.
        # Trois chips prenaient la place de deux boutons pour un choix a
        # trois positions.
        self.orient_button = QPushButton("", self.controls)
        self.orient_button.setObjectName("splitButton")
        self.orient_button.setFocusPolicy(Qt.NoFocus)
        self.orient_button.setProperty("chosen", "true")
        self.orient_button.setToolTip("Ce que le mur pioche : cliquer pour changer")
        self.orient_button.clicked.connect(self._cycle_orientation)
        controls.addWidget(self.orient_button)
        controls.addSpacing(10)
        self.unseen = QPushButton("Non vus", self.controls)
        self.unseen.setObjectName("splitButton")
        self.unseen.setFocusPolicy(Qt.NoFocus)
        self.unseen.setToolTip("Ne piocher que dans ce qui n'a été ni décidé, "
                               "ni regardé plus de cinq secondes")
        self.unseen.clicked.connect(
            lambda _c=False: self.unseenToggled.emit(
                self.unseen.property("chosen") != "true"))
        controls.addWidget(self.unseen)
        controls.addSpacing(10)
        full = QPushButton("", self.controls)
        dress(full, "maximize", 18)
        full.setFixedSize(34, 28)
        full.setObjectName("splitButton")
        full.setFocusPolicy(Qt.NoFocus)
        full.setToolTip("Le mur seul, sur tout l'écran — Échap pour revenir")
        full.clicked.connect(self.fullscreenRequested)
        controls.addWidget(full)
        outer.addWidget(self.controls)

        # En plein ecran, une seule chose reste : de quoi en sortir. Une ligne
        # fine, a droite, hors des panneaux — poses dessus, elle passerait
        # derriere leurs lecteurs natifs.
        self.exit_row = QWidget(self)
        exit_row = QHBoxLayout(self.exit_row)
        exit_row.setContentsMargins(0, 0, 0, 0)
        exit_row.addStretch(1)
        self.exit_button = QPushButton("✕  Quitter le plein écran   (Échap)", self.exit_row)
        self.exit_button.setObjectName("splitButton")
        self.exit_button.setFocusPolicy(Qt.NoFocus)
        self.exit_button.clicked.connect(self.exitRequested)
        exit_row.addWidget(self.exit_button)
        self.exit_row.hide()
        outer.addWidget(self.exit_row)

        self.row = QWidget(self)
        self.grid = QGridLayout(self.row)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.panes: list = []
        self.set_pane_count(panes)
        self.row.hide()
        outer.addWidget(self.row, 1)
        self._mark_choices()

        # Le widget video natif ne signale pas le survol : on le sonde.
        self.muted = False
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)
        self._heard = None
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(120)
        self.watch_timer.timeout.connect(self._watch)

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        self.audio.setMuted(self.muted)
        self._watch()

    def _hear(self, pane) -> None:
        """Branche l'unique sortie audio sur ce panneau, et sur lui seul."""
        if pane is self._heard:
            return
        if self._heard is not None:
            try:
                self._heard.player.setAudioOutput(None)
            except RuntimeError:
                pass
        self._heard = pane
        if pane is not None:
            pane.player.setAudioOutput(self.audio)
            self.audio.setMuted(self.muted)

    def _watch(self) -> None:
        window = self.window()
        active = window is not None and window.isActiveWindow()
        cursor = QCursor.pos()
        heard = None
        for pane in self.panes:
            stage = pane.stage
            corner = stage.mapToGlobal(QPoint(0, 0))
            over = (active and pane.isVisible() and QRect(
                corner.x(), corner.y(), stage.width(),
                stage.height()).contains(cursor))
            pane.watch(over, self.muted)
            if over:
                heard = pane
        self._hear(heard)

    def showEvent(self, event):
        super().showEvent(event)
        self.watch_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.watch_timer.stop()
        self._hear(None)
        self.stop()

    def set_unseen(self, on: bool) -> None:
        self.unseen.setProperty("chosen", "true" if on else "false")
        self.unseen.style().unpolish(self.unseen)
        self.unseen.style().polish(self.unseen)

    def _cycle_orientation(self) -> None:
        keys = [key for key, _label in ORIENTATIONS]
        at = keys.index(self.orientation) if self.orientation in keys else 0
        self.orientationChanged.emit(keys[(at + 1) % len(keys)])

    def _mark_choices(self) -> None:
        self.count_stepper.set_value(len(self.panes))
        label = dict(ORIENTATIONS).get(self.orientation, "Toutes")
        self.orient_button.setText(f"{label} ▾")

    def set_orientation(self, orientation: str) -> None:
        self.orientation = orientation
        self._lay_out()

    stayChanged = Signal(bool)

    def set_stay(self, on: bool) -> None:
        self.stay = bool(on)
        for pane in self.panes:
            pane.set_stay(self.stay)
        self.stayChanged.emit(self.stay)

    def fill_empty(self) -> None:
        """Remplit les seuls panneaux vides : ajouter un panneau ne doit pas
        remplacer les videos qu'on etait en train de regarder."""
        if not self.pool:
            return self.shuffle_all()
        busy = {pane.video_path for pane in self.panes if pane.video_path}
        free = [video for video in self.pool if video not in busy]
        random.shuffle(free)
        self._queue = [(at, free.pop()) for at, pane in enumerate(self.panes)
                       if not pane.video_path and free]
        self._start_one()

    def set_shape(self, shape) -> None:
        self.shape = shape
        self._lay_out()

    def set_pane_count(self, count: int) -> None:
        """Autant de panneaux que demande, en gardant ceux qui existent."""
        count = max(1, count)
        self.solo = -1
        while len(self.panes) > count:
            pane = self.panes.pop()
            if pane is self._heard:
                self._hear(None)
            pane.clear()
            pane.hide_overlays()
            self.grid.removeWidget(pane)
            pane.deleteLater()
        while len(self.panes) < count:
            pane = SplitPane(len(self.panes), self.scroll_seconds, self.row)
            pane.wants_next.connect(self.refill_one)
            pane.wants_sibling.connect(self.siblingRequested)
            pane.peekRequested.connect(self.peekRequested)
            pane.sortRequested.connect(self.sortRequested)
            pane.soloRequested.connect(self.toggle_solo)
            pane.stayToggled.connect(self.set_stay)
            pane.set_stay(self.stay)
            pane.peekChosen.connect(self.peekChosen)
            pane.opened.connect(self.opened)
            pane.set_bare(self.panes[0].bare if self.panes else False)
            self.panes.append(pane)
        self._lay_out()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # La meilleure disposition depend de la place : elle change avec la
        # fenetre. On ne refait la grille que si elle change vraiment.
        rows, cols = self._grid_shape()
        if (rows, cols) != getattr(self, "_shape", None):
            self._lay_out()

    def _grid_shape(self) -> tuple:
        if self.single_row and len(self.panes) <= 4:
            return 1, max(1, len(self.panes))
        return grid_for(len(self.panes), self.shape or self.orientation,
                        self.row.width(), self.row.height())

    def _clear_stretch(self) -> None:
        """Les rangees et colonnes d'une grille precedente gardaient leur
        etirement : trois videos apres douze n'occupaient que le coin des
        douze, le reste de l'ecran restait vide."""
        for row in range(self.grid.rowCount()):
            self.grid.setRowStretch(row, 0)
        for col in range(self.grid.columnCount()):
            self.grid.setColumnStretch(col, 0)

    def _lay_out(self) -> None:
        self._clear_stretch()
        if self.solo != -1 and self.solo < len(self.panes):
            # Un seul panneau occupe tout : les autres se retirent de la
            # grille, ils reviendront tels quels.
            for at, pane in enumerate(self.panes):
                self.grid.removeWidget(pane)
                pane.setVisible(at == self.solo)
            self.grid.addWidget(self.panes[self.solo], 0, 0)
            self.grid.setRowStretch(0, 1)
            self.grid.setColumnStretch(0, 1)
            self._shape = (1, 1)
            self._mark_choices()
            return
        rows, cols = self._grid_shape()
        self._shape = (rows, cols)
        for pane in self.panes:
            pane.setVisible(True)
        for pane in self.panes:
            self.grid.removeWidget(pane)
        for index, pane in enumerate(self.panes):
            # Plusieurs rangees : chacune doit pouvoir tenir dans l'ecran.
            pane.stage.setMinimumHeight(140 if rows == 1 else 90)
            self.grid.addWidget(pane, index // cols, index % cols)
        for row in range(rows):
            self.grid.setRowStretch(row, 1)
        for col in range(cols):
            self.grid.setColumnStretch(col, 1)
        self._mark_choices()

    def toggle_solo(self, index: int) -> None:
        """Ne garder que ce panneau, ou rendre les autres.

        Les autres se mettent en pause plutot que de continuer derriere : on
        ne les regarde plus, et six decodages pour une seule image visible
        n'apportent rien qu'un processeur occupe.
        """
        if self.solo == index:
            return self.unsolo()
        if not (0 <= index < len(self.panes)):
            return
        self.solo = index
        for at, pane in enumerate(self.panes):
            if at == index:
                continue
            pane.player.pause()
        self._lay_out()

    def unsolo(self) -> None:
        if self.solo == -1:
            return
        self.solo = -1
        for pane in self.panes:
            if pane.video_path:
                pane.player.play()
        self._lay_out()

    def set_bare(self, bare: bool) -> None:
        """Rien que les videos : ni reglages, ni barres de panneau."""
        self.controls.setVisible(not bare)
        self.exit_row.setVisible(bare)
        for pane in self.panes:
            pane.set_bare(bare)

    def set_caption(self, count: int, unknown: int = 0, pinned: bool = False,
                    heavy: int = 0) -> None:
        if pinned:
            self.caption.setText(f"{count} vidéo(s) choisie(s) — lues ensemble")
            return
        kind = {"vertical": "verticales", "horizontal": "horizontales"}.get(
            self.orientation, "")
        text = f"{count} vidéo(s) {kind}".replace("  ", " ")
        if unknown:
            text += f" (dont {unknown} d'orientation encore inconnue)"
        if heavy:
            text += f" — {heavy} au-delà de 1080p écartée(s) : trop lourdes pour ce mur"
        self.caption.setText(text + " — le mur y pioche au hasard" if count else "")

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
        # Un panneau a la fois, pas six d'un coup : six ouvertures simultanees
        # sur le partage, six decodages qui demarrent ensemble, et l'interface
        # ne respire plus. Echelonnes, chacun a la ligne pour lui un instant.
        for pane in self.panes:
            pane.clear()
        self._queue = list(enumerate(picks))
        self._start_one()

    def _start_one(self) -> None:
        if not getattr(self, "_queue", None):
            return
        index, path = self._queue.pop(0)
        if index < len(self.panes) and self.isVisible():
            self.panes[index].play(path)
        if self._queue:
            QTimer.singleShot(self.STAGGER_MS, self._start_one)

    def play_in(self, index: int, path: str) -> None:
        """Pose cette video dans ce panneau (la suivante du dossier, par exemple)."""
        if 0 <= index < len(self.panes) and path:
            self.panes[index].play(path)

    def refill_one(self, index: int) -> None:
        """Remplace la vidéo d'un seul panneau, sans toucher aux autres."""
        if not self.pool or not (0 <= index < len(self.panes)):
            return
        # On evite de reposer ce qui est deja a l'ecran, tant qu'il y a le choix.
        busy = {pane.video_path for pane in self.panes if pane.video_path}
        choices = [video for video in self.pool if video not in busy] or self.pool
        self.panes[index].play(random.choice(choices))

    def stop(self) -> None:
        self._queue = []
        self.solo = -1
        for pane in self.panes:
            pane.peek_end()
            pane.stop()
            pane.hide_overlays()

    def end_peeks(self) -> None:
        for pane in self.panes:
            pane.peek_end()
