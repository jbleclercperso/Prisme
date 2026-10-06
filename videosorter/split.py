"""Mur de lecture : plusieurs vidéos côte à côte, en même temps.

Trois vidéos verticales remplissent un écran large mieux qu'une seule, qui y
laisse deux bandes noires. On les regarde ensemble, on passe celles qui
n'intéressent pas, et l'on garde les autres — c'est du tri à trois voies.

Chaque panneau est indépendant : sa lecture, sa position, son remplacement. Le
mur ne pioche que dans les vidéos dont on connaît déjà la résolution, puisque
c'est elle qui dit si une vidéo est verticale ; ce vivier s'étoffe à mesure que
l'on parcourt la collection.

Verticales et horizontales se melangent : chaque panneau prend la forme de sa
video, et la mosaique se compose pour remplir l'ecran (voir `mosaic.py`).
"""
from __future__ import annotations

import random

from PySide6.QtCore import (
    QEasingCurve, QPoint, QRect, QTimer, QUrl, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import QCursor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

import time

from PySide6.QtCore import QThreadPool

from .config import is_photo
from .mosaic import TALL, WIDE, family, layout as mosaic_layout
from .perf import mark
from .icons import dress, icon
from .widgets import (
    OverBar, PeekOverlay, PlayMarks, Stepper, _Still, _StillLoader,
    _StillSignals,
)

# Le diaporama d'un panneau photo, par defaut : le mur le regle
# (`SplitWall.set_photo`) d'apres la configuration.
SLIDESHOW_MS = 6000

# Trois panneaux : sur un ecran large, trois videos verticales le remplissent
# presque exactement. Quatre les amincissent au point qu'on ne distingue plus
# grand-chose.
DEFAULT_PANES = 3
# Delai entre deux demarrages de panneaux, au plus : le suivant part des que
# le precedent a ouvert son fichier, un court instant apres (STAGGER_MIN_MS).
# Six ouvertures simultanees sur le partage figeaient tout ; attendre 650 ms
# fixes mettait six secondes a remplir un mur de dix.
STAGGER_MS = 650
STAGGER_MIN_MS = 150
# Le son suit la souris, mais pas a chaque panneau traverse : la premiere fois
# qu'on entend un panneau, on lui branche sa propre sortie audio, ce qui ouvre
# un flux Windows. On attend que la souris se pose. Ensuite, passer d'un
# panneau a l'autre ne fait plus que couper l'un et rendre le son a l'autre.
HEAR_SETTLE_MS = 250
# Comment chaque video occupe sa case, au choix (bouton du mur).
FITS = ("show", "full")
FIT_TEXT = {
    "show": ("▢ Tout montrer", "Chaque vidéo tient entière dans sa case, avec de fines "
                              "bandes noires."),
    "full": ("■ Remplir à 100 %", "L'écran entier est couvert. La mosaïque est choisie "
                                 "pour rogner le moins possible, et chaque vidéo perd la même "
                                 "part de son image — jamais une seule très zoomée."),
}

# Les videos filmees debout mais enregistrees dans un cadre couche, bandes
# noires sur les cotes (telephones, reprises) : la forme du fichier les dit
# horizontales, l'image les montre verticales. Retenue pour la seance :
# {chemin: forme de l'image utile}.
BOXED: dict = {}


def content_aspect(image):
    """(forme de l'image utile, forme du cadre), d'apres une image du lecteur ;
    None si l'image est noire (un debut de video) : on regardera plus tard."""
    try:
        import numpy as np
        from PySide6.QtGui import QImage
        small = image.scaledToWidth(160).convertToFormat(QImage.Format_Grayscale8)
        width, height = small.width(), small.height()
        if width < 16 or height < 16:
            return None
        raw = np.frombuffer(small.constBits(), dtype=np.uint8,
                            count=small.bytesPerLine() * height)
        pixels = raw.reshape(height, small.bytesPerLine())[:, :width]
    except Exception:                                       # noqa: BLE001
        return None
    lit = pixels > 28
    columns = np.flatnonzero(lit.mean(axis=0) > 0.04)
    rows = np.flatnonzero(lit.mean(axis=1) > 0.04)
    if len(columns) < 4 or len(rows) < 4:
        return None
    useful = (columns[-1] - columns[0] + 1) / (rows[-1] - rows[0] + 1)
    return float(useful), width / height

# Combien de videos a la fois. La mosaique s'arrange de tous les nombres :
# sept ne faisaient pas de rectangle dans une grille, ils en font un ici.
PANE_CHOICES = (2, 3, 4, 5, 6, 7, 8, 9, 10)
# Les memes mots que sur la planche (header.ControlBar.FORMAT_TEXT).
ORIENTATIONS = (("vertical", "▯ Verticales"), ("horizontal", "▭ Horizontales"),
                ("any", "▯▭ Tous formats"))


class Deck:
    """Le hasard sans remise : chaque video sort une fois avant qu'aucune ne revienne.

    `random.choice` ramenait la meme video trois fois en vingt tirages, et en
    laissait d'autres de cote pour toujours. Un paquet melange d'avance aurait
    coute un melange de cent mille chemins a chaque fois que le vivier change
    (collection, filtre, orientation) : on tire donc au hasard dans le vivier
    tel qu'il est, et l'on rejette ce qui est deja sorti. Quelques essais
    suffisent tant que le paquet est plein ; la liste exacte de ce qui reste
    ne se dresse que lorsqu'il s'epuise, et un nouveau tour commence quand
    tout est passe.

    Ce qui est sorti est retenu par chemin, et non par position : un vivier
    refait garde la memoire des tirages, et ne remontre pas ce qu'on vient de
    voir.
    """

    # Au-dela, le paquet est presque vide : on dresse la liste des restes.
    TRIES = 24

    def __init__(self):
        self.drawn: set = set()
        self.rounds = 0

    def draw(self, pool, avoid=()) -> str:
        """Une video de `pool` jamais sortie dans ce tour, hors `avoid` ("" sinon)."""
        if not pool:
            return ""
        return self.draw_with(lambda: random.choice(pool), lambda: pool, avoid)

    def draw_with(self, pick, everything, avoid=(), accept=None) -> str:
        """Le meme tirage, quand le vivier ne se donne pas en liste.

        `pick()` rend un candidat au hasard (a poids egaux sur les videos) ;
        `everything()` rend tout le vivier, et n'est appele que lorsque le
        paquet s'epuise. `accept` ecarte ce qui ne doit pas sortir du tout.
        """
        if not isinstance(avoid, (set, frozenset)):
            avoid = set(avoid)
        drawn = self.drawn
        for _ in range(self.TRIES):
            video = pick()
            if (video and video not in drawn and video not in avoid
                    and (accept is None or accept(video))):
                drawn.add(video)
                return video
        rest = [video for video in everything()
                if video not in drawn and video not in avoid
                and (accept is None or accept(video))]
        if not rest:
            # Tout le vivier est passe : un nouveau tour. Ce qui est deja a
            # l'ecran compte pour ce tour-ci, et ne revient pas aussitot.
            self.rounds += 1
            drawn.clear()
            drawn.update(avoid)
            rest = [video for video in everything()
                    if video not in avoid and (accept is None or accept(video))]
            if not rest:
                return ""
        video = random.choice(rest)
        drawn.add(video)
        return video


SPLIT_STYLE = """
QFrame#splitPane { background: #0b0d10; border: 1px solid #242a33;
                   border-radius: 8px; }
/* Le panneau qu'on entend : un liseré d'or, comme Stash multiview. A trois
   videos et plus, on cherchait d'ou venait le son. */
QFrame#splitPane[audible="true"] { background: #f5c542; border-color: #f5c542; }
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
    revealRequested = Signal(str)     # ⌸ : le fichier, dans l'explorateur
    peekRequested = Signal(int, str)  # Maj + clic droit : les neuf instants
    sortRequested = Signal(int, str)  # clic droit : les destinations, pour ranger
    peekChosen = Signal(int, int)     # une case cliquee : (panneau, case)
    soloRequested = Signal(int)       # cette video seule, sur tout le mur
    fullRequested = Signal(int)       # double-clic : cette video en plein ecran
    stayToggled = Signal(bool)        # la coche « rester dans ce dossier »
    favoriteToggled = Signal(str)     # l'etoile du bandeau : cette video
    reshaped = Signal(int)            # la forme de sa video est connue, ou a change

    def __init__(self, index: int, scroll_seconds: int = 5, parent=None):
        super().__init__(parent)
        self.setObjectName("splitPane")
        self.index = index
        self.scroll_seconds = scroll_seconds
        self.video_path = ""
        # La forme (largeur / hauteur) de la video montree, 0 tant qu'on
        # l'ignore : le mur compose sa mosaique avec. `aspect_of` la donne
        # d'apres l'index ; a defaut, la premiere image la dit.
        self.aspect = 0.0
        self.aspect_of = lambda _path: 0.0
        self.fill = False
        # Une video « en boite » (bandes noires dans le fichier) : rognee de
        # ses bandes, meme en « Tout montrer ».
        self.boxed = False
        self._frames_seen = 0
        self._box_checked = False
        # « Remplir a 100 % » : ni marge ni cadre, l'image va jusqu'au bord.
        self.tight = False

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
        # La barre de la fiche : nom, place dans le dossier (« 3 / 12 »),
        # temps restant, gestes. Le temps quitte le coin de l'image quand
        # elle est la.
        self.position_of = lambda _path: ""
        self.name = self.bar.name
        self.name.setText("—")
        # Le bandeau pilote tout : revenir a la precedente, pause, la
        # suivante du meme dossier, une autre au hasard, son fichier dans
        # l'explorateur, et elle seule en grand. La fiche reste a portee de
        # la touche F.
        self.history: list = []
        self.stay = False
        for text, tip, slot in (
            ("◂", "La précédente, dans ce panneau", self._previous),
            ("⏯", "Pause, ou reprendre   (un clic sur l'image)", self.toggle_pause),
            ("▸", "Une autre : au hasard, ou dans ce dossier si la case est cochée"
             "   (Espace)", self._forward),
            ("⌸", "Montrer ce fichier dans l'explorateur", self._reveal),
            # La meme icone « plein ecran » que partout dans Prisme.
            ("⛶", "Cette vidéo seule, sur tout le mur — Échap pour revenir",
             self._solo),
        ):
            self.bar.add_gesture(text, tip, slot)
        self.pause_button = self.bar.pause_button
        # Un panneau etroit ne garde que l'essentiel : l'explorateur s'efface
        # le premier, puis le grand ecran, puis la precedente.
        self.bar.spare = [self.bar.by_glyph[g] for g in ("⌸", "⛶", "◂")]
        self.bar.add_stay("Rester dans ce dossier : ▸ prend la suivante du "
                          "même dossier au lieu d'une vidéo au hasard",
                          False, self.stayToggled)
        self.bar.stay.toggled.connect(self._dress_forward)
        self._dress_forward(False)
        # Le favori d'un clic, sans quitter le mur : il fallait ouvrir la
        # fiche — et perdre le mur — pour marquer une video qu'on aimait.
        # Juste apres le nom, vide ou doree comme partout ailleurs.
        self.favorite = False
        self.favorite_of = lambda _path: False
        self.star_button = self.bar.add_star(self._star)
        # Survoler le trait : l'image a cet instant ; un clic y va.
        self.bar.scrub_source = self.scrub_source
        self.bar.seekRequested.connect(self.seek_fraction)
        self.hovered = False

        # Le son ne vient que de la video survolee : six videos qui parlent
        # en meme temps ne s'ecoutent pas. La sortie du panneau ne nait que
        # la premiere fois qu'on l'entend (son actif), puis reste branchee,
        # coupee quand la souris est ailleurs : la faire passer d'un lecteur a
        # l'autre, comme avant, figeait l'interface 0,1 a 0,5 s a chaque
        # changement de panneau. Tant que le son reste coupe -- le reglage
        # par defaut -- aucune n'existe.
        self.audio = None
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        # Les photos : une image fixe, et un diaporama a chaque panneau, qui
        # tourne des qu'il se remplit. ⏯ ou un clic l'arrete.
        self.still = _Still(self.stage)
        self.still_signals = _StillSignals(self)
        self.still_signals.loaded.connect(self._still_loaded)
        self.slideshow_ms = SLIDESHOW_MS
        self.slideshow_on = True
        self.slideshow = QTimer(self)
        self.slideshow.setSingleShot(True)
        self.slideshow.timeout.connect(self._forward)
        self._slide_started = 0.0
        self.zoom = 1.0
        self.zoom_focus = (0.5, 0.5)
        # Le zoom des videos, comme sur la fiche : bouton gauche tenu (ou
        # Ctrl) et molette ; glisser ensuite promene l'image ; clic droit,
        # retour a la taille normale.
        self._zoom_anim = None
        self._held = False
        self._zoomed_while_held = False
        self._pan_last = None
        self.player.playbackStateChanged.connect(self._show_pause)
        self._show_pause()
        self.player.positionChanged.connect(self._on_position)
        self.player.mediaStatusChanged.connect(self._on_status)
        try:
            self.video.videoSink().videoSizeChanged.connect(self._sink_size)
            self.video.videoSink().videoFrameChanged.connect(self._look_for_bars)
        except (AttributeError, RuntimeError):
            pass

    # -- forme -------------------------------------------------------------
    def learn_aspect(self, aspect: float) -> None:
        """La forme de la video est connue : le mur recompose, si elle change."""
        if not aspect or aspect <= 0:
            return
        if self.aspect and abs(aspect - self.aspect) < 0.02:
            return
        self.aspect = aspect
        self.reshaped.emit(self.index)

    def _sink_size(self) -> None:
        """La premiere image d'une video dont l'index ignorait la resolution."""
        try:
            size = self.video.videoSink().videoSize()
        except RuntimeError:
            return
        if (self.video_path and not self.photo and not self.boxed
                and size.width() > 0 and size.height() > 0):
            self.learn_aspect(size.width() / size.height())

    def _look_for_bars(self, frame) -> None:
        """Quelques images apres le debut, une fois : des bandes noires dans
        le fichier ? Alors la case prend la forme de l'image utile, et le
        lecteur rogne les bandes. Quatre verticales « en boite » passaient
        pour des horizontales, et le mur les empilait deux par deux."""
        if self._box_checked or self.photo or not self.video_path:
            return
        self._frames_seen += 1
        if self._frames_seen not in (12, 40, 90, 200):
            return
        try:
            image = frame.toImage()
        except Exception:                                   # noqa: BLE001
            return
        if image.isNull():
            return
        found = content_aspect(image)
        if found is None:
            if self._frames_seen >= 200:
                self._box_checked = True
            return
        self._box_checked = True
        useful, whole = found
        if abs(useful - whole) / whole < 0.15:
            return
        BOXED[self.video_path] = useful
        self.boxed = True
        self.set_fill(self.fill)
        self.learn_aspect(useful)

    def set_fill(self, on: bool) -> None:
        """Remplir sa case (en rognant un peu), ou y tenir entiere."""
        self.fill = bool(on)
        self.layout().setContentsMargins(*((0,) * 4 if self.tight else (3,) * 4))
        self.video.setAspectRatioMode(
            Qt.KeepAspectRatioByExpanding if self.fill or self.boxed else Qt.KeepAspectRatio)
        self.still.fill = self.fill
        self.still.update()

    # -- photos ------------------------------------------------------------
    @property
    def photo(self) -> bool:
        return bool(self.video_path) and is_photo(self.video_path)

    def _show_photo(self, path: str) -> None:
        """Une photo dans le panneau : le lecteur video se tait."""
        if self.player.source().isValid():
            self._pause()
            self.player.setSource(QUrl())
        self.video.hide()
        self.zoom, self.zoom_focus = 1.0, (0.5, 0.5)
        self.still.set_image(None)
        self.still.show()
        self.still.raise_()
        self._place()
        screen = self.screen()
        longest = 1600
        if screen is not None:
            size = screen.size() * screen.devicePixelRatio()
            longest = max(800, min(2560, max(size.width(), size.height())))
        QThreadPool.globalInstance().start(
            _StillLoader(path, longest, self.still_signals))
        self._restart_slide()

    def _still_loaded(self, path: str, image) -> None:
        if path == self.video_path:
            self.still.set_image(image)
            if image is not None and not image.isNull() and image.height() > 0:
                self.learn_aspect(image.width() / image.height())

    def _restart_slide(self) -> None:
        self.slideshow.stop()
        if self.photo and self.slideshow_on:
            self._slide_started = time.monotonic()
            self.slideshow.start(self.slideshow_ms)
        self._show_pause()

    def _hide_photo(self) -> None:
        self.slideshow.stop()
        self.still.hide()
        self.still.set_image(None)

    # -- contenu ---------------------------------------------------------
    def play(self, path: str, remember: bool = True) -> None:
        mark(f"wall.play {path[-40:]}")
        if remember and self.video_path and self.video_path != path:
            self.history = (self.history + [self.video_path])[-30:]
        self.video_path = path
        # Deja vue « en boite » cette seance : sa vraie forme, tout de suite.
        boxed = BOXED.get(path)
        self._frames_seen = 0
        self._box_checked = boxed is not None
        if self.boxed != bool(boxed):
            self.boxed = bool(boxed)
            self.set_fill(self.fill)
        # Connue de l'index, la forme compose la mosaique tout de suite ;
        # inconnue, l'ancienne reste le temps que la premiere image arrive --
        # le mur ne se recompose pas deux fois.
        self.learn_aspect(boxed or self.aspect_of(path) or 0.0)
        # Une autre video repart a sa taille normale.
        self._stop_zoom()
        self.zoom, self.zoom_focus = 1.0, (0.5, 0.5)
        self.bar.set_name(path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1])
        self.name.setToolTip(path)
        self.set_favorite(self.favorite_of(path))
        if is_photo(path):
            self._show_photo(path)
            return
        self._hide_photo()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        if self.video.isHidden() and not self.peeking:
            # Panneau libere par vacate() : l'image restee dans le lecteur
            # est celle de la video d'avant, on la vide avant de reparaitre.
            try:
                self.video.videoSink().setVideoFrame(QVideoFrame())
            except (RuntimeError, TypeError):
                pass
            self.video.show()
        self._place()

    def set_favorite(self, on: bool) -> None:
        self.favorite = bool(on)
        self.bar.set_favorite(self.favorite)

    def _star(self) -> None:
        if self.video_path:
            self.favoriteToggled.emit(self.video_path)

    def set_bare(self, bare: bool) -> None:
        """Le bandeau ne vient plus qu'au survol, plein ecran ou non."""
        self.bare = bare

    def watch(self, hovered: bool, muted: bool) -> None:
        """Appele par le mur a chaque battement : bandeau et trait."""
        self.hovered = hovered
        self._show_audible(hovered and not muted and bool(self.video_path)
                           and not self.photo)
        if (not self.isVisible() or not self.video_path or self.peeking
                or not self.stage.isVisible()):
            self.bar.hide()
            self.marks.hide()
            return
        # Sur une photo, le trait suffit : le compte a rebours des secondes
        # n'apprenait rien et chargeait l'image. Au survol, le temps est dans
        # la barre, comme sur la fiche.
        self.marks.with_left = not self.photo and not hovered
        if self.photo:
            # Le trait d'une photo : ce qui reste avant la suivante.
            if self.slideshow.isActive():
                done = int((time.monotonic() - self._slide_started) * 1000)
                self.marks.set_progress(min(done, self.slideshow_ms), self.slideshow_ms)
                self.bar.set_progress(min(done, self.slideshow_ms), self.slideshow_ms)
            else:
                self.marks.set_progress(0, 0)
                self.bar.set_progress(0, 0)
        if hovered:
            self.marks.with_rail = False
            self.bar.set_position(self.position_of(self.video_path) if not self.photo else "")
            self.bar.place_on(self.stage)
            self.bar.reveal()
        else:
            self.marks.with_rail = True
            self.bar.hide()
        # Au repos, l'etoile doree dit le favori, a cote du temps restant.
        self.marks.set_favorite(self.favorite and not hovered)
        self.marks.place_on(self.stage)
        self.marks._lay_out()

    def _show_audible(self, on: bool) -> None:
        value = "true" if on else "false"
        if self.property("audible") == value:
            return
        self.setProperty("audible", value)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def hide_overlays(self) -> None:
        self.bar.hide()
        self.marks.hide()

    def clear(self) -> None:
        self.peek_end()
        self._hide_photo()
        self.video_path = ""
        self.name.setText("—")
        # Une pause, puis la source videe -- sans stop(). Sur un lecteur qui
        # a sa propre sortie son, stop() du moteur FFmpeg de Qt ne rendait
        # parfois plus jamais la main (un panneau en train de se remplir) :
        # la fenetre restait figee pour de bon. Vider la source libere le
        # fichier tout autant.
        self._pause()
        self.player.setSource(QUrl())
        self.marks.clear()
        self.bar.hide()
        self.set_favorite(False)

    def vacate(self) -> None:
        """Libere le panneau sans rien decharger : la video d'avant ne part
        qu'a l'arrivee de la suivante, par play(), a son tour.

        Changer ou vider la source d'un lecteur fige l'interface le temps que
        Qt defasse l'ancien media. Remanier le mur videait tous les panneaux
        d'un coup : autant de gels mis bout a bout. Echelonnes avec les
        demarrages, ils se perdent entre deux images.
        """
        self.peek_end()
        self._hide_photo()
        self.video_path = ""
        self.name.setText("—")
        # Pause seulement s'il jouait : sur un lecteur arrete, pause()
        # rechargerait la video pour en montrer la premiere image.
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        self.video.hide()
        self.marks.clear()
        self.bar.hide()
        self.set_favorite(False)

    def stop(self) -> None:
        """Arrete le panneau : une pause, jamais stop() (voir `clear`)."""
        self.slideshow.stop()
        self._pause()

    def _pause(self) -> None:
        # Pause seulement s'il jouait : sur un lecteur arrete, pause()
        # rechargerait la video pour en montrer la premiere image.
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()

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
        self.bar.stay._dress(self.stay)
        self._dress_forward(self.stay)

    def _dress_forward(self, stay: bool) -> None:
        """▸ garde son chevron « suivante », comme partout ; la bulle dit ce
        qu'il fera : la suivante du dossier, ou une autre au hasard."""
        button = self.bar.by_glyph["▸"]
        dress(button, "chevron-right", 20)
        button.setToolTip("La suivante, dans le dossier de cette vidéo" if stay
                          else "Une autre vidéo, au hasard")

    def _previous(self) -> None:
        """Revient a la video d'avant, dans ce panneau."""
        if self.history:
            self.play(self.history.pop(), remember=False)

    def scrub_source(self):
        """(video, duree en secondes) pour l'apercu du trait, ou None."""
        duration = self.player.duration()
        if self.photo or not self.video_path or duration <= 0:
            return None
        return self.video_path, duration / 1000.0

    def seek_fraction(self, fraction: float) -> None:
        duration = self.player.duration()
        if self.photo or duration <= 0:
            return
        target = int(max(0.0, min(1.0, fraction)) * duration)
        self.player.setPosition(min(target, max(0, duration - 500)))

    def jump(self, seconds: float) -> None:
        """Maj+← / Maj+→ sur le panneau survole : quelques secondes."""
        if self.photo or not self.video_path:
            return
        duration = self.player.duration()
        target = self.player.position() + int(seconds * 1000)
        if duration > 0:
            target = min(duration - 1000, target)
        self.player.setPosition(max(0, target))

    def toggle_pause(self) -> None:
        if self.photo:
            # Une photo : le diaporama de ce panneau s'arrete, ou repart.
            self.slideshow_on = not self.slideshow_on
            self._restart_slide()
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def resume(self) -> None:
        """Reprend apres un arret du mur : le diaporama, s'il tournait."""
        if self.photo:
            self._restart_slide()
        elif self.video_path:
            self.player.play()

    def _show_pause(self, *_args) -> None:
        if self.photo:
            playing = self.slideshow_on
        else:
            playing = (self.player.playbackState()
                       == QMediaPlayer.PlaybackState.PlayingState)
        self.pause_button.setIcon(icon("pause" if playing else "play"))
        self.pause_button.setToolTip(
            ("Arrêter le diaporama" if playing else "Relancer le diaporama")
            if self.photo else "Pause, ou reprendre")

    def _sibling(self) -> None:
        if self.video_path:
            self.wants_sibling.emit(self.index, self.video_path)

    def _open(self) -> None:
        if self.video_path:
            self.opened.emit(self.video_path)

    def _reveal(self) -> None:
        if self.video_path:
            self.revealRequested.emit(self.video_path)

    def _solo(self) -> None:
        self.soloRequested.emit(self.index)

    # -- avancement ------------------------------------------------------
    def _place(self) -> None:
        area = self.stage.rect()
        self.peek.setGeometry(area)
        # L'image s'agrandit dans son cadre, autour du point vise : la photo,
        # et la video comme sur la fiche (le cadre rogne ce qui deborde).
        width, height = int(area.width() * self.zoom), int(area.height() * self.zoom)
        fx, fy = self.zoom_focus
        zoomed = QRect(int(fx * (area.width() - width)),
                       int(fy * (area.height() - height)), width, height)
        self.video.setGeometry(zoomed)
        self.still.setGeometry(zoomed)

    # -- le zoom ---------------------------------------------------------------
    def _zoom_goal(self) -> float:
        anim = self._zoom_anim
        if anim is not None and anim.state() == QVariantAnimation.Running:
            return float(anim.endValue())
        return self.zoom

    def _zoom_to(self, goal: float) -> None:
        """Le zoom glisse jusqu'a `goal` en un instant, au lieu de sauter."""
        if self._zoom_anim is None:
            self._zoom_anim = QVariantAnimation(self)
            self._zoom_anim.setDuration(140)
            self._zoom_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._zoom_anim.valueChanged.connect(self._zoom_frame)
        self._zoom_anim.stop()
        self._zoom_anim.setStartValue(float(self.zoom))
        self._zoom_anim.setEndValue(float(goal))
        self._zoom_anim.start()

    def _zoom_frame(self, value) -> None:
        self.zoom = float(value)
        self._place()

    def _stop_zoom(self) -> None:
        if self._zoom_anim is not None:
            self._zoom_anim.stop()

    def reset_zoom(self) -> None:
        self._stop_zoom()
        self.zoom, self.zoom_focus = 1.0, (0.5, 0.5)
        self._place()

    def _zoom_wheel(self, notches: float) -> None:
        """Agrandit ou reduit en gardant fixe le point sous la souris."""
        area = self.stage
        local = area.mapFromGlobal(QCursor.pos())
        if area.width() > 0 and area.height() > 0:
            self.zoom_focus = (max(0.0, min(1.0, local.x() / area.width())),
                               max(0.0, min(1.0, local.y() / area.height())))
        self._zoom_to(max(1.0, min(6.0, self._zoom_goal() * (1.25 ** notches))))

    def _pan_to(self, where: QPoint) -> None:
        """Deplace l'image agrandie avec la souris."""
        last, self._pan_last = self._pan_last, where
        if last is None or self.zoom <= 1.0:
            return
        area = self.stage.rect()
        span_x = area.width() * (self.zoom - 1.0)
        span_y = area.height() * (self.zoom - 1.0)
        fx, fy = self.zoom_focus
        fx -= (where.x() - last.x()) / span_x if span_x > 0 else 0.0
        fy -= (where.y() - last.y()) / span_y if span_y > 0 else 0.0
        self.zoom_focus = (max(0.0, min(1.0, fx)), max(0.0, min(1.0, fy)))
        self._place()

    def _on_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            # Une video finie laisse la place a une autre : le mur ne doit pas
            # se figer sur une image d'arret. Comme ▸ : la suivante du meme
            # dossier si la case est cochee -- la fin de video prenait
            # toujours une video au hasard, case cochee ou non.
            self._forward()

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
        held = bool(event.buttons() & Qt.LeftButton)
        if self.photo or held or event.modifiers() & Qt.ControlModifier:
            # Une photo n'a pas de temps a parcourir : la molette zoome la ou
            # pointe la souris. Une video aussi, bouton gauche tenu ou Ctrl,
            # comme sur la fiche ; revenue a x1, l'image se recadre entiere.
            if held:
                self._zoomed_while_held = True
                self._pan_last = QCursor.pos()
            self._zoom_wheel(notches)
            event.accept()
            return
        # Le meme sens que sur la fiche : un cran vers le haut avance. Le mur
        # reculait, et l'on passait de l'un a l'autre sans cesse.
        step = int(notches * self.scroll_seconds * 1000)
        duration = self.player.duration()
        target = self.player.position() + step
        if duration > 0:
            target = max(0, min(duration - 1000, target))
        self.player.setPosition(max(0, target))
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton and self.video_path:
            # Le clic molette : la suivante, comme Espace.
            event.accept()
            return self._forward()
        if event.button() == Qt.LeftButton:
            self._held = True
            self._zoomed_while_held = False
            self._panned = False
            self._press_at = QCursor.pos()
            self._pan_last = QCursor.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # Image agrandie : glisser la promene, bouton tenu depuis le zoom ou
        # non (une fois relache, on ne pouvait plus s'y deplacer).
        if self._held and event.buttons() & Qt.LeftButton and (
                self._zoomed_while_held or self.zoom > 1.0):
            start = getattr(self, "_press_at", None) or QCursor.pos()
            if (self._panned or self._zoomed_while_held
                    or (QCursor.pos() - start).manhattanLength() >= 4):
                self._panned = True
                self._pan_to(QCursor.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and (self._zoomed_while_held
                                                or getattr(self, "_panned", False)):
            # Le bouton servait au zoom, ou a promener l'image : ce n'etait
            # pas un clic de pause.
            self._held = False
            self._zoomed_while_held = False
            self._panned = False
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._held = False
        if event.button() == Qt.RightButton and self.zoom > 1.0:
            # Le clic droit defait d'abord le zoom, comme sur la fiche.
            self.reset_zoom()
            event.accept()
            return
        if event.button() == Qt.RightButton and self.video_path:
            # Le clic droit sert a ranger : ce sont les destinations qui
            # apparaissent, comme sur la fiche. Les neuf instants restent
            # accessibles, mais avec Maj — on les consulte, on ne les
            # utilise pas pour decider.
            if event.modifiers() & Qt.ShiftModifier:
                if not self.photo:      # une photo n'a pas d'instants
                    self.peekRequested.emit(self.index, self.video_path)
            else:
                self.sortRequested.emit(self.index, self.video_path)
            event.accept()
            return
        if event.button() == Qt.LeftButton and self.video_path:
            # Un clic sur l'image met en pause ou reprend : on regarde trois
            # videos, il faut pouvoir en retenir une sans perdre les autres.
            # Sur une photo, c'est son diaporama.
            self.toggle_pause()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        """Double-clic : cette video en plein ecran, comme partout dans
        Prisme (la fiche reste sur F). Les deux clics ont bascule la pause
        deux fois : elle reste comme elle etait."""
        if event.button() == Qt.LeftButton and self.video_path:
            self.fullRequested.emit(self.index)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class SplitWall(QWidget):
    """Le mur entier : quelques panneaux, et un vivier où puiser."""

    STAGGER_MS = STAGGER_MS

    opened = Signal(str)
    revealRequested = Signal(str)
    siblingRequested = Signal(int, str)
    countChanged = Signal(int)
    orientationChanged = Signal(str)
    fullscreenRequested = Signal()
    paneFullRequested = Signal(int)
    exitRequested = Signal()
    unseenToggled = Signal(bool)
    peekRequested = Signal(int, str)
    peekChosen = Signal(int, int)
    sortRequested = Signal(int, str)
    # L'etoile d'un panneau : bascule le favori de cette video. Le mur
    # n'enregistre rien lui-meme ; on lui renvoie l'etat par set_favorite.
    favoriteToggled = Signal(str)
    # « Tout montrer », « tout remplir » ou « remplir a 100 % » : la fenetre
    # l'enregistre.
    fitChanged = Signal(str)

    def __init__(self, panes: int = DEFAULT_PANES, scroll_seconds: int = 5,
                 parent=None, orientation: str = "vertical"):
        super().__init__(parent)
        self.setStyleSheet(SPLIT_STYLE)
        self.pool: list = []
        # Le hasard du mur est sans remise (▸, remaniement, panneau a
        # remplir) : une video vue ne revient qu'une fois tout le vivier passe.
        self.deck = Deck()
        self.favorite_of = lambda _path: False
        # La forme d'une video (largeur / hauteur), 0 si on l'ignore : la
        # fenetre la lit dans l'index. La mosaique se compose avec.
        self.aspect_of = lambda _path: 0.0
        # Ce que les premieres images ont appris des videos que l'index ne
        # connaissait pas : une remplacante de meme forme peut s'y choisir.
        self.learned: dict = {}
        self.fit = "show"
        # Les demarrages echelonnes : une seule minuterie, et non une chaine
        # de minuteries a usage unique. Chaque remaniement en lancait une de
        # plus, qui vidaient ensemble la meme file : les panneaux partaient a
        # trois cents millisecondes d'ecart, en rafale sur le partage.
        self._queue: list = []
        self._starting = None
        self.stagger = QTimer(self)
        self.stagger.setSingleShot(True)
        self.stagger.timeout.connect(self._start_one)
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
        # Les cases remplissent toujours l'ecran ; reste a choisir, pour la
        # video dans sa case : toute l'image (de fines bandes noires), ou la
        # case pleine (les bords un peu rognes).
        self.fill_button = QPushButton("", self.controls)
        self.fill_button.setObjectName("splitButton")
        self.fill_button.setFocusPolicy(Qt.NoFocus)
        self.fill_button.clicked.connect(
            lambda _c=False: self.fitChanged.emit(
                FITS[(FITS.index(self.fit) + 1) % len(FITS)] if self.fit in FITS else "show"))
        controls.addWidget(self.fill_button)
        controls.addSpacing(10)
        full = QPushButton("", self.controls)
        dress(full, "maximize", 18)
        full.setFixedSize(34, 28)
        full.setObjectName("splitButton")
        full.setFocusPolicy(Qt.NoFocus)
        full.setToolTip("Le mur seul, sur tout l'écran (F11) — Échap pour revenir")
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

        # Les panneaux sont poses a la main, chacun a sa place dans la
        # mosaique (`_lay_out`) : une grille de cases egales ne savait pas
        # melanger verticales et horizontales.
        self.row = QWidget(self)
        self.row.installEventFilter(self)
        self.gap = 8
        self.rects: list = []
        self._shape = ()
        # Plusieurs panneaux changent de forme d'un coup (un remaniement) :
        # une seule recomposition, juste apres.
        self.reshape = QTimer(self)
        self.reshape.setSingleShot(True)
        self.reshape.setInterval(0)
        self.reshape.timeout.connect(self._lay_out)
        self.panes: list = []
        self.set_pane_count(panes)
        self.row.hide()
        outer.addWidget(self.row, 1)
        self._mark_choices()
        self.set_fit("show")

        # Le widget video natif ne signale pas le survol : on le sonde.
        self.muted = False
        self._heard = None
        self._hear_next = None       # le panneau survole, pas encore branche
        self.hear_timer = QTimer(self)
        self.hear_timer.setSingleShot(True)
        self.hear_timer.setInterval(HEAR_SETTLE_MS)
        self.hear_timer.timeout.connect(self._settle_heard)
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(120)
        self.watch_timer.timeout.connect(self._watch)

    @property
    def audio(self):
        """La sortie du panneau qu'on entend, s'il y en a un."""
        pane = self._heard
        return getattr(pane, "audio", None) if pane is not None else None

    def _mute_heard(self, muted: bool) -> None:
        output = self.audio
        if output is not None:
            try:
                output.setMuted(muted)
            except RuntimeError:
                pass

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        self._mute_heard(self.muted)
        # Rendre le son est un geste voulu : le panneau survole l'a aussitot.
        self._watch(at_once=True)

    def _hear(self, pane) -> None:
        """Le son pour ce panneau, et pour lui seul.

        L'ancien se tait, sans rien debrancher. Le nouveau recoit sa propre
        sortie la premiere fois (un flux Windows s'ouvre, une fois pour
        toutes), sinon il retrouve simplement le son.
        """
        if pane is self._heard:
            return
        self._mute_heard(True)
        self._heard = pane
        if pane is None:
            return
        if pane.audio is None:
            output = QAudioOutput(pane)
            from .audiodev import follow
            follow(output)
            output.setMuted(self.muted)
            pane.audio = output
            pane.player.setAudioOutput(output)
        else:
            self._mute_heard(self.muted)

    def _choose_heard(self, pane, at_once: bool = False) -> None:
        """Le son pour ce panneau, sans rebrancher quoi que ce soit pour rien.

        Couper ou rendre le son d'une sortie deja branchee ne coute rien ;
        la brancher sur un autre lecteur ouvre un flux audio, l'en retirer
        le ferme. Balayer le mur le faisait plusieurs fois par seconde, son
        coupe compris — ce qui est le reglage par defaut. On ne rebranche
        donc que pour faire entendre un autre panneau, son actif, et une fois
        la souris posee dessus.
        """
        if self.muted:
            # Son coupe : on ne branche plus rien, et celui qui l'etait se
            # tait sans etre debranche. Debrancher coute jusqu'a cinquante
            # millisecondes, et Qt s'y est deja bloque pour de bon juste apres
            # un branchement ; une piste son decodee pour rien, sur un seul
            # panneau, coute bien moins.
            self.hear_timer.stop()
            self._hear_next = None
            self._mute_heard(True)
            return
        if pane is None:
            # Plus rien sous la souris : on se tait, sans debrancher.
            self.hear_timer.stop()
            self._hear_next = None
            self._mute_heard(True)
            return
        if pane is self._heard:
            self.hear_timer.stop()
            self._hear_next = None
            self._mute_heard(False)
            return
        if at_once or pane.audio is not None:
            # Deja equipe : lui rendre le son ne coute rien, inutile
            # d'attendre que la souris se pose.
            self.hear_timer.stop()
            self._hear_next = None
            self._hear(pane)
            return
        if pane is not self._hear_next:
            # Un autre panneau, jamais entendu : l'ancien se tait tout de
            # suite, le nouveau ne sera branche que si la souris s'y pose.
            self._hear_next = pane
            self._mute_heard(True)
            self.hear_timer.start()

    def _settle_heard(self) -> None:
        pane = self._hear_next
        self._hear_next = None
        if pane is not None and not self.muted and pane in self.panes:
            self._hear(pane)

    def _watch(self, at_once: bool = False) -> None:
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
        self._choose_heard(heard, at_once)

    def showEvent(self, event):
        super().showEvent(event)
        self.watch_timer.start()

    # Vrai le temps que la fenetre passe au repli : le mur se fige tel quel
    # (memes videos, meme instant, panneau seul garde) au lieu de s'arreter.
    # Il repartait sinon de zero, sur dix autres videos tirees au hasard.
    hold = False

    def hideEvent(self, event):
        super().hideEvent(event)
        self.watch_timer.stop()
        # Se taire suffit : la sortie reste branchee, et sera reprise au
        # retour sans avoir ete fermee entre-temps.
        self.hear_timer.stop()
        self._hear_next = None
        self._mute_heard(True)
        if self.hold:
            # Les demarrages en attente se perdraient derriere une page
            # cachee (`_start_one` ne lance rien d'invisible) : ils attendent.
            self.stagger.stop()
            for pane in self.panes:
                pane.peek_end()
                pane.hide_overlays()
                pane.slideshow.stop()
                if (pane.player.playbackState()
                        == QMediaPlayer.PlaybackState.PlayingState):
                    pane.player.pause()
            return
        self.stop()

    def resume(self, playing: list) -> None:
        """Au retour du repli : reprend les panneaux qui jouaient, et les
        demarrages restes en attente."""
        for pane in self.panes:
            if pane.photo:
                pane.resume()
            elif pane.player in playing and pane.video_path:
                pane.player.play()
        if self._queue:
            self._start_one()

    def eventFilter(self, watched, event):
        if watched is self.row and event.type() == event.Type.Resize:
            self._lay_out()
        return super().eventFilter(watched, event)

    @property
    def fill(self) -> bool:
        return self.fit != "show"

    def set_fit(self, mode: str) -> None:
        """Comment chaque video occupe sa case : entiere (« show »), case
        remplie (« fill »), ou l'ecran entier couvert (« full »)."""
        self.fit = mode if mode in FITS else "show"
        for pane in self.panes:
            pane.tight = self.fit == "full"
            pane.set_fill(self.fill)
        text, tip = FIT_TEXT[self.fit]
        self.fill_button.setText(text)
        self.fill_button.setToolTip(tip + "\n\nCliquer pour passer au mode suivant.")
        self.gap = 2 if self.fit == "full" else 8
        self._lay_out()

    def set_fill(self, on: bool) -> None:
        self.set_fit("fill" if on else "show")

    def set_aspect_of(self, lookup) -> None:
        """Comment connaitre la forme d'une video : la fenetre le sait."""
        self.aspect_of = lookup
        for pane in self.panes:
            pane.aspect_of = lookup

    def _reshaped(self, index: int) -> None:
        if 0 <= index < len(self.panes):
            pane = self.panes[index]
            if pane.video_path and pane.aspect:
                self.learned[pane.video_path] = pane.aspect
        self.reshape.start()

    def _known_aspect(self, path: str) -> float:
        return BOXED.get(path) or self.aspect_of(path) or self.learned.get(path, 0.0)

    def _default_aspect(self) -> float:
        return TALL if (self.shape or self.orientation) == "vertical" else WIDE

    def _family_of(self, path: str) -> int:
        return family(self._known_aspect(path) or self._default_aspect())

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
        # Sans « ▾ » : il promettait un menu, et le clic fait tourner.
        self.orient_button.setText(
            dict(ORIENTATIONS).get(self.orientation, "▯▭ Tous formats"))

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
        self.stagger.stop()
        self._starting = None
        busy = {pane.video_path for pane in self.panes if pane.video_path}
        empty = [at for at, pane in enumerate(self.panes) if not pane.video_path]
        self._queue = list(zip(empty, self._draw_many(len(empty), busy)))
        self._start_one()

    def _draw_many(self, count: int, avoid=()) -> list:
        """`count` videos distinctes, sans remise, hors `avoid`."""
        avoid = set(avoid)
        picks = []
        for _ in range(max(0, count)):
            video = self.deck.draw(self.pool, avoid)
            if not video:
                break
            picks.append(video)
            avoid.add(video)
        return picks

    def set_favorite_of(self, lookup) -> None:
        """Comment savoir si une video est en favori : la fenetre le sait."""
        self.favorite_of = lookup
        for pane in self.panes:
            pane.favorite_of = lookup
            pane.set_favorite(bool(pane.video_path) and lookup(pane.video_path))

    def set_position_of(self, lookup) -> None:
        """« 3 / 12 » : la place d'une video dans son dossier (la fenetre)."""
        self.position_of = lookup
        for pane in self.panes:
            pane.position_of = lookup

    def set_favorite(self, path: str, on: bool) -> None:
        """Le favori de cette video a change : les panneaux qui la montrent
        suivent."""
        for pane in self.panes:
            if pane.video_path == path:
                pane.set_favorite(on)

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
            pane.hide()
            pane.deleteLater()
        while len(self.panes) < count:
            pane = SplitPane(len(self.panes), self.scroll_seconds, self.row)
            pane.wants_next.connect(self.refill_one)
            pane.wants_sibling.connect(self.siblingRequested)
            pane.peekRequested.connect(self.peekRequested)
            pane.sortRequested.connect(self.sortRequested)
            pane.soloRequested.connect(self.toggle_solo)
            pane.fullRequested.connect(self.paneFullRequested)
            # La coche d'un panneau ne vaut que pour lui : on en laisse
            # trois dans le dossier, et l'on envoie la quatrieme ailleurs.
            pane.stayToggled.connect(lambda on, p=pane: p.set_stay(on))
            pane.set_stay(self.stay)
            pane.peekChosen.connect(self.peekChosen)
            pane.opened.connect(self.opened)
            pane.revealRequested.connect(self.revealRequested)
            pane.favoriteToggled.connect(self.favoriteToggled)
            pane.favorite_of = self.favorite_of
            pane.position_of = getattr(self, "position_of", pane.position_of)
            pane.aspect_of = self.aspect_of
            pane.reshaped.connect(self._reshaped)
            pane.tight = self.fit == "full"
            pane.set_fill(self.fill)
            pane.player.mediaStatusChanged.connect(
                lambda status, p=pane: self._pane_status(p, status))
            pane.set_bare(self.panes[0].bare if self.panes else False)
            pane.slideshow_ms = self.slideshow_ms
            self.panes.append(pane)
        self._lay_out()

    # Le diaporama de chaque panneau photo (`set_photo`).
    slideshow_ms = SLIDESHOW_MS
    noun = "vidéo"

    def set_photo(self, on: bool, seconds: float = SLIDESHOW_MS / 1000) -> None:
        """Des photos : chaque panneau les fait defiler en diaporama."""
        self.noun = "photo" if on else "vidéo"
        self.set_slideshow_seconds(seconds)
        for pane in self.panes:
            pane.slideshow_on = True

    def set_slideshow_seconds(self, seconds: float) -> None:
        """La duree de chaque photo, sans relancer les panneaux en pause."""
        self.slideshow_ms = max(1000, int(seconds * 1000))
        for pane in self.panes:
            pane.slideshow_ms = self.slideshow_ms

    def _lay_out(self) -> None:
        """Pose chaque panneau a sa place dans la mosaique.

        Chaque panneau a la forme de sa video (celle qu'on attend, tant
        qu'elle n'est pas connue) ; `mosaic.layout` les compose pour montrer
        le plus d'image possible, puis les cases s'etirent jusqu'a remplir
        tout l'ecran. Rien ne bouge si la disposition est la meme.
        """
        self.reshape.stop()
        width, height = self.row.width(), self.row.height()
        if self.solo != -1 and self.solo < len(self.panes):
            # Un seul panneau occupe tout : les autres se cachent, ils
            # reviendront tels quels.
            rects = [(0, 0, width, height) if at == self.solo else None
                     for at in range(len(self.panes))]
            shape = ("seul",)
        else:
            default = self._default_aspect()
            aspects = [pane.aspect or default for pane in self.panes]
            rects, shape, _shown = mosaic_layout(aspects, width, height, self.gap,
                                                 full=self.fit == "full")
            if not rects:
                rects = [None] * len(self.panes)
        if rects == self.rects and shape == self._shape:
            return
        self.rects, self._shape = rects, shape
        for pane, rect in zip(self.panes, rects):
            if rect is None:
                pane.setVisible(False)
                continue
            x, y, w, h = rect
            # Un petit panneau doit pouvoir tenir dans sa case.
            pane.stage.setMinimumHeight(40)
            pane.setMinimumWidth(40)
            pane.setGeometry(x, y, w, h)
            pane.setVisible(True)
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
            pane.stop()
        self._lay_out()

    def unsolo(self) -> None:
        if self.solo == -1:
            return
        self.solo = -1
        for pane in self.panes:
            if pane.video_path:
                pane.resume()
        self._lay_out()

    def set_bare(self, bare: bool) -> None:
        """Rien que les videos : ni reglages, ni barres de panneau."""
        self.controls.setVisible(not bare)
        self.exit_row.setVisible(bare)
        for pane in self.panes:
            pane.set_bare(bare)

    def set_caption(self, count: int, unknown: int = 0, pinned: bool = False,
                    heavy: int = 0) -> None:
        noun = self.noun
        if pinned:
            self.caption.setText(f"{count} {noun}(s) choisie(s) — lues ensemble")
            return
        kind = {"vertical": "verticales", "horizontal": "horizontales"}.get(
            self.orientation, "")
        text = f"{count} {noun}(s) {kind}".replace("  ", " ")
        if unknown:
            text += f" (dont {unknown} d'orientation encore inconnue)"
        if heavy:
            text += f" — {heavy} au-delà de 1080p écartée(s) : trop lourdes pour ce mur"
        self.caption.setText(text + " — le mur y pioche au hasard" if count else "")

    # -- vivier ----------------------------------------------------------
    def set_pool(self, videos: list, first=None) -> None:
        """Remplace le vivier et relance les panneaux.

        `first` : les videos a reprendre, panneau par panneau -- celles d'avant
        une fiche ouverte depuis le mur. Y revenir tirait un mur tout neuf.
        """
        self.pool = [str(video) for video in videos]
        self.shuffle_all(first)

    def grow_pool(self, videos: list) -> None:
        """Le vivier s'est etoffe : les panneaux gardent leur video, seuls les
        vides se remplissent.

        Relancer tout le mur a chaque sondage qui trouvait de nouvelles
        verticales remplacait, juste apres l'entree dans le mur, la video
        qu'on commencait a regarder.
        """
        self.pool = [str(video) for video in videos]
        has = bool(self.pool)
        self.empty.setVisible(not has)
        self.row.setVisible(has)
        self.fill_empty()

    def shuffle_all(self, first=None) -> None:
        self.stagger.stop()
        self._queue = []
        self._starting = None
        # Des videos donnees d'office (un dossier, un retour de fiche) se
        # jouent meme quand le vivier est encore vide.
        has = bool(self.pool) or any(first or ())
        self.empty.setVisible(not has)
        self.row.setVisible(has)
        if not has:
            for pane in self.panes:
                pane.clear()
            return
        # Les videos a reprendre gardent leur panneau ; les autres se tirent
        # sans remise, autant que le vivier en a.
        kept = [str(path) if path else "" for path in (first or ())]
        kept = (kept + [""] * len(self.panes))[:len(self.panes)]
        taken = {path for path in kept if path}
        wanted = max(0, min(len(self.panes), len(self.pool)) - len(taken))
        fresh = iter(self._draw_many(wanted, taken))
        picks = [path or next(fresh, "") for path in kept]
        # Un panneau a la fois, pas six d'un coup : six ouvertures simultanees
        # sur le partage, six decodages qui demarrent ensemble, et l'interface
        # ne respire plus. Echelonnes, chacun a la ligne pour lui un instant.
        # Ceux qui vont recevoir une video sont liberes sans rien decharger
        # (vacate) ; seul un panneau qui restera vide rend son fichier.
        for at, pane in enumerate(self.panes):
            if picks[at]:
                pane.vacate()
            else:
                pane.clear()
        self._queue = [(at, path) for at, path in enumerate(picks) if path]
        self._start_one()

    def _start_one(self) -> None:
        self._starting = None
        if not self._queue:
            return
        index, path = self._queue.pop(0)
        if index < len(self.panes) and self.isVisible():
            self.panes[index].play(path)
            self._starting = self.panes[index]
        if self._queue:
            self.stagger.start(self.STAGGER_MS)

    def _pane_status(self, pane, status) -> None:
        """Le panneau lance en dernier a ouvert son fichier : le suivant peut
        partir, sans attendre le delai prevu pour le pire."""
        if pane is not self._starting or not self.stagger.isActive():
            return
        if status in (QMediaPlayer.MediaStatus.LoadedMedia,
                      QMediaPlayer.MediaStatus.BufferedMedia):
            self._starting = None
            if self.stagger.remainingTime() > STAGGER_MIN_MS:
                self.stagger.start(STAGGER_MIN_MS)

    def play_in(self, index: int, path: str) -> None:
        """Pose cette video dans ce panneau (la suivante du dossier, par exemple)."""
        if 0 <= index < len(self.panes) and path:
            self.panes[index].play(path)

    # Combien de tirages pour trouver une remplacante de la meme forme.
    SAME_SHAPE_TRIES = 40

    def refill_one(self, index: int) -> None:
        """Remplace la vidéo d'un seul panneau, sans toucher aux autres."""
        if not self.pool or not (0 <= index < len(self.panes)):
            return
        # Sans remise, et jamais ce qui est deja a l'ecran tant qu'il y a le
        # choix : ▸ ramenait souvent une video vue deux minutes plus tot.
        busy = {pane.video_path for pane in self.panes if pane.video_path}
        video = self._same_shape(index, busy)
        if not video:
            video = self.deck.draw(self.pool, busy) or random.choice(self.pool)
        self.panes[index].play(video)

    def _same_shape(self, index: int, busy: set) -> str:
        """Une remplacante de la meme forme que la video qui s'en va, s'il y
        en a : la mosaique reste alors telle quelle, au lieu de se recomposer
        a chaque fin de video. Quelques tirages au hasard, sans parcourir le
        vivier ; a defaut, n'importe laquelle."""
        pane = self.panes[index]
        if not pane.aspect:
            return ""
        wanted = family(pane.aspect)
        drawn = self.deck.drawn
        for _ in range(self.SAME_SHAPE_TRIES):
            video = random.choice(self.pool)
            if (video not in drawn and video not in busy
                    and self._known_aspect(video) and self._family_of(video) == wanted):
                drawn.add(video)
                return video
        return ""

    def stop(self) -> None:
        self.stagger.stop()
        self._queue = []
        self._starting = None
        self.solo = -1
        for pane in self.panes:
            pane.peek_end()
            pane.stop()
            pane.hide_overlays()

    def end_peeks(self) -> None:
        for pane in self.panes:
            pane.peek_end()
