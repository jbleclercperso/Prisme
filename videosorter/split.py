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

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from .perf import mark
from .scan import human_duration
from .widgets import PeekOverlay, elide

# Trois panneaux : sur un ecran large, trois videos verticales le remplissent
# presque exactement. Quatre les amincissent au point qu'on ne distingue plus
# grand-chose.
DEFAULT_PANES = 3
# Delai entre deux demarrages de panneaux.
STAGGER_MS = 650
# Les nombres qui font un rectangle. Cinq ou sept n'en font pas.
PANE_CHOICES = (2, 3, 4, 6, 8, 9, 10)
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
            {2: 1, 3: 1, 4: 2, 6: 2, 8: 2, 9: 3, 10: 2}.get(count, 2)
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
    peekRequested = Signal(int, str)  # clic droit : les neuf instants
    peekChosen = Signal(int, int)     # une case cliquee : (panneau, case)

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

        # Pas de barre d'avancement ici : sur un mur, on regarde plusieurs
        # videos a la fois et l'on ne suit la progression d'aucune. Le seul
        # chiffre qui serve est le temps qu'il reste, et il tient dans la
        # barre du panneau — une rangee de moins pour autant d'image.
        self.remaining = QLabel("", self)
        self.remaining.setObjectName("paneTime")

        self.bar = QWidget(self)
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        self.bare = False
        self.name = QLabel("—", self)
        self.name.setObjectName("splitName")
        # Sans cela, un nom de fichier long imposait sa largeur au panneau, donc
        # au mur, donc a la fenetre entiere, qui ne pouvait plus retrecir.
        self.name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bar.addWidget(self.name, 1)
        bar.addWidget(self.remaining, 0)
        # Trois gestes : la suivante du meme dossier quand une video plait,
        # une autre au hasard n'importe ou, et ouvrir en grand.
        for text, tip, slot in (
            ("▸", "La suivante, dans le même dossier", self._sibling),
            ("⚄", "Une autre, au hasard, n'importe où", self._next),
            ("⤢", "Ouvrir cette vidéo", self._open),
        ):
            button = QPushButton(text, self)
            button.setObjectName("paneGesture")
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.NoFocus)
            button.setFixedSize(26, 22)
            button.clicked.connect(slot)
            bar.addWidget(button)
        layout.addWidget(self.bar)

        # Pas de sortie audio : trois videos qui parlent en meme temps ne
        # s'ecoutent pas, elles se regardent.
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_position)
        self.player.mediaStatusChanged.connect(self._on_status)

    # -- contenu ---------------------------------------------------------
    def play(self, path: str) -> None:
        mark(f"wall.play {path[-40:]}")
        self.video_path = path
        self.name.setText(elide(path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1], 28))
        self.name.setToolTip(path)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self._place()

    def set_bare(self, bare: bool) -> None:
        """Sans sa barre : en plein ecran, elle ne revient qu'au survol."""
        self.bare = bare
        self.bar.setVisible(not bare)

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.bare:
            self.bar.show()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self.bare:
            self.bar.hide()

    def clear(self) -> None:
        self.peek_end()
        self.video_path = ""
        self.name.setText("—")
        self.player.stop()
        self.player.setSource(QUrl())
        self.remaining.setText("")

    def stop(self) -> None:
        self.player.stop()

    def _next(self) -> None:
        self.wants_next.emit(self.index)

    def _sibling(self) -> None:
        if self.video_path:
            self.wants_sibling.emit(self.index, self.video_path)

    def _open(self) -> None:
        if self.video_path:
            self.opened.emit(self.video_path)

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
        self.remaining.setText(
            f"−{human_duration(max(0, duration - position) / 1000.0)}"
            if duration > 0 else "")

    # -- les neuf instants ----------------------------------------------------
    def peek_begin(self, captions: list) -> None:
        self.peeking = True
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
            self.peekRequested.emit(self.index, self.video_path)
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

    def __init__(self, panes: int = DEFAULT_PANES, scroll_seconds: int = 5,
                 parent=None, orientation: str = "vertical"):
        super().__init__(parent)
        self.setStyleSheet(SPLIT_STYLE)
        self.pool: list = []
        self.shown: list = []
        self.scroll_seconds = scroll_seconds
        self.orientation = orientation

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
        for count in PANE_CHOICES:
            button = QPushButton(str(count), self.controls)
            button.setObjectName("splitButton")
            button.setFocusPolicy(Qt.NoFocus)
            button.setToolTip(f"{count} vidéos à la fois")
            button.clicked.connect(lambda _c=False, n=count: self.countChanged.emit(n))
            controls.addWidget(button)
            self.count_buttons[count] = button
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
        full = QPushButton("⛶ Plein écran", self.controls)
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

    def set_unseen(self, on: bool) -> None:
        self.unseen.setProperty("chosen", "true" if on else "false")
        self.unseen.style().unpolish(self.unseen)
        self.unseen.style().polish(self.unseen)

    def _cycle_orientation(self) -> None:
        keys = [key for key, _label in ORIENTATIONS]
        at = keys.index(self.orientation) if self.orientation in keys else 0
        self.orientationChanged.emit(keys[(at + 1) % len(keys)])

    def _mark_choices(self) -> None:
        for count, button in self.count_buttons.items():
            button.setProperty("chosen", "true" if count == len(self.panes) else "false")
            button.style().unpolish(button)
            button.style().polish(button)
        label = dict(ORIENTATIONS).get(self.orientation, "Toutes")
        self.orient_button.setText(f"{label} ▾")

    def set_orientation(self, orientation: str) -> None:
        self.orientation = orientation
        self._lay_out()

    def set_pane_count(self, count: int) -> None:
        """Autant de panneaux que demande, en gardant ceux qui existent."""
        count = max(1, count)
        while len(self.panes) > count:
            pane = self.panes.pop()
            pane.clear()
            self.grid.removeWidget(pane)
            pane.deleteLater()
        while len(self.panes) < count:
            pane = SplitPane(len(self.panes), self.scroll_seconds, self.row)
            pane.wants_next.connect(self.refill_one)
            pane.wants_sibling.connect(self.siblingRequested)
            pane.peekRequested.connect(self.peekRequested)
            pane.peekChosen.connect(self.peekChosen)
            pane.opened.connect(self.opened)
            pane.set_bare(self.panes[0].bare if self.panes else False)
            self.panes.append(pane)
        self._lay_out()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # La meilleure disposition depend de la place : elle change avec la
        # fenetre. On ne refait la grille que si elle change vraiment.
        rows, cols = grid_for(len(self.panes), self.orientation,
                              self.row.width(), self.row.height())
        if (rows, cols) != getattr(self, "_shape", None):
            self._lay_out()

    def _lay_out(self) -> None:
        rows, cols = grid_for(len(self.panes), self.orientation,
                              self.row.width(), self.row.height())
        self._shape = (rows, cols)
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
        for pane in self.panes:
            pane.peek_end()
            pane.stop()

    def end_peeks(self) -> None:
        for pane in self.panes:
            pane.peek_end()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.stop()
