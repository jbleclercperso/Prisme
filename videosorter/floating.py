"""Le lecteur flottant : la video continue, par-dessus tout le reste.

On trie, on ouvre un dossier ou une page web -- et la video disparaissait
derriere. Le lecteur flottant prend le relais, a l'instant ou elle en etait :
une fenetre toujours au premier plan, sans cadre, ou toute la place va a
l'image. Elle prend les proportions de la video, se redimensionne par ses
bords et se deplace par son titre, pose discretement en haut a gauche.

C'est le meme lecteur que la fiche (`SinglePlayer`), sans sa pellicule : le
clic met en pause, la molette avance, clic maintenu + molette zoome, le temps
restant et le trait d'avancement sont la, le bandeau de gestes parait au
survol, et le clic droit ouvre les destinations autour du pointeur, comme
partout ailleurs.

Il s'ouvre de deux facons : le bouton ⧉ du bandeau de la fiche (Ctrl+L), ou
tout seul quand Prisme est reduit ou recouvert par une autre application
pendant qu'une video joue (reglage « Lecteur flottant automatique », menu ⋯).
Revenir dans Prisme le referme, et la fiche reprend au meme instant.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QPoint, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from .icons import icon
from .widgets import OverBar, SinglePlayer, STYLESHEET, app_icon, elide

FLOAT_STYLE = """
QWidget#floatingPlayer { background: #000000; }
"""


def _on_top(widget) -> None:
    """Une fenetre-outil du lecteur (bandeau, trait, pastille) doit passer
    devant lui, donc etre elle aussi au premier plan."""
    widget.setWindowFlag(Qt.WindowStaysOnTopHint, True)


class _TitleChip(QLabel):
    """Le nom de la video, discret, pose sur l'image : on l'attrape pour
    deplacer la fenetre, qui n'a pas de barre de titre."""

    def __init__(self, owner):
        super().__init__(owner, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowStaysOnTopHint)
        self.owner = owner
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet(
            "QLabel { background: rgba(0,0,0,110); color: rgba(255,255,255,0.80);"
            " padding: 2px 8px; font-size: 12px; border-radius: 0; }")
        self.setCursor(Qt.SizeAllCursor)
        self.setToolTip("Glisser pour déplacer · double-clic : plein écran")
        self.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.owner.windowHandle()
            if handle is not None:
                handle.startSystemMove()
        elif event.button() == Qt.RightButton:
            self.owner.player.radialRequested.emit()

    def mouseDoubleClickEvent(self, event):
        self.owner.toggle_full()


class FloatingPlayer(QWidget):
    """Une fenetre a part, toujours devant, qui lit la video de la fiche."""

    returnRequested = Signal()        # ↩, Echap : revenir dans Prisme
    dismissRequested = Signal()       # ✕ : se retirer, sans ramener Prisme
    stepRequested = Signal(int)       # ◂ ▸ : la precedente, la suivante
    revealRequested = Signal(str)     # ⌸ : le fichier dans l'explorateur
    radialRequested = Signal()        # clic droit : les destinations
    keyForwarded = Signal(object)     # une touche de tri, pour la fenetre

    WATCH_MS = 120
    BORDER = 4                        # la marge ou l'on saisit un bord

    def __init__(self, scroll_seconds: int = 5):
        flags = (Qt.Window | Qt.Tool | Qt.WindowStaysOnTopHint
                 | Qt.FramelessWindowHint)
        super().__init__(None, flags)
        self.setObjectName("floatingPlayer")
        self.setWindowTitle("Prisme — lecteur flottant")
        self.setWindowIcon(app_icon())
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(STYLESHEET + FLOAT_STYLE)
        self.setMinimumSize(240, 140)
        self.setMouseTracking(True)
        # Survoler l'image y montre le bandeau meme quand on travaille dans
        # une autre application : c'est tout l'interet de la fenetre.
        self.hover_anytime = True

        layout = QVBoxLayout(self)
        border = self.BORDER
        layout.setContentsMargins(border, border, border, border)
        layout.setSpacing(0)
        self.player = SinglePlayer(5, scroll_seconds, self)
        # Toute la place a l'image : ni pellicule, ni hauteur minimale.
        self.player.hide_strip()
        self.player.video_area.setMinimumHeight(60)
        layout.addWidget(self.player, 1)
        self.player.radialRequested.connect(self.radialRequested)
        self.player.cinemaRequested.connect(self.toggle_full)
        for deck in self.player.decks:
            deck.player.mediaStatusChanged.connect(
                lambda status, d=deck: self._when_loaded(d, status))

        self.bar = OverBar(self)
        for text, tip, slot in (
            ("◂", "Précédente   (←)", lambda: self.stepRequested.emit(-1)),
            ("⏯", "Pause, ou reprendre   (Entrée)", self.player.toggle_pause),
            ("▸", "Suivante   (→)", lambda: self.stepRequested.emit(1)),
            ("⌸", "Montrer ce fichier dans l'explorateur", self._reveal),
            ("⛶", "Plein écran, ou revenir   (double-clic)", self.toggle_full),
            ("↩", "Revenir dans Prisme   (Échap)", self.returnRequested.emit),
            ("✕", "Fermer le lecteur flottant : la vidéo s'arrête, "
                  "Prisme reste où il est", self.dismissRequested.emit),
        ):
            self.bar.add_gesture(text, tip, slot)
        self.player.progressed.connect(self.bar.set_progress)
        # Au repos, le temps restant en haut a droite, comme sur le mur.
        self.player.marks.with_left = True
        self.title = _TitleChip(self)
        for widget in (self.bar, self.player.marks, self.player.marks.left):
            _on_top(widget)

        self.path = ""
        self.name = ""
        self.auto = False             # ouvert tout seul (Prisme recouvert)
        self._pending_ms = 0
        self._fitted = ""             # la video dont on a pris les proportions

        self.watch = QTimer(self)
        self.watch.setInterval(self.WATCH_MS)
        self.watch.timeout.connect(self._watch)

    # -- ouverture ------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.isVisible() and bool(self.path)

    def open(self, path: str, name: str, start_ms: int = 0, muted: bool = True,
             geometry=None) -> None:
        """Montre la fenetre sur cette video, a cet instant."""
        if not self.isVisible():
            self._place(geometry)
            # Ouvert tout seul, il ne prend pas le clavier : on est en train
            # de taper ailleurs. Demande (⧉, Ctrl+L), il le prend.
            self.setAttribute(Qt.WA_ShowWithoutActivating, self.auto)
            self.show()
            if not self.auto:
                self.raise_()
                self.activateWindow()
        self.player.set_muted(muted)
        self.load(path, name, start_ms)
        self.watch.start()

    def load(self, path: str, name: str, start_ms: int = 0) -> None:
        """Une autre video dans la fenetre deja ouverte (◂ ▸, un tri)."""
        self.path = path
        self.name = name
        self._pending_ms = max(0, int(start_ms))
        self.bar.set_name(name)
        self.title.setText(elide(name, 70))
        self.title.adjustSize()
        self.setWindowTitle(f"{name} — Prisme")
        self.player.set_item(path, "…")
        deck = self.player.decks[self.player._active]
        self._when_loaded(deck, deck.player.mediaStatus())

    def _when_loaded(self, deck, status) -> None:
        """Des la video chargee : l'instant demande, et ses proportions."""
        if deck is not self.player.decks[self.player._active]:
            return
        if os.path.normcase(deck.path) != os.path.normcase(self.path):
            return
        if status not in (QMediaPlayer.MediaStatus.LoadedMedia,
                          QMediaPlayer.MediaStatus.BufferingMedia,
                          QMediaPlayer.MediaStatus.BufferedMedia):
            return
        if self._pending_ms:
            wanted, self._pending_ms = self._pending_ms, 0
            deck.player.setPosition(wanted)
        if self._fitted != self.path:
            self._fitted = self.path
            self._fit(deck)

    def _fit(self, deck) -> None:
        """La fenetre prend les proportions de la video : pas de bandes noires.
        La largeur reste celle qu'on a choisie ; seule la hauteur suit."""
        if self.isFullScreen() or self.isMaximized():
            return
        size = deck.video.videoSink().videoSize()
        try:
            width, height = size.width(), size.height()
        except AttributeError:
            return
        if width <= 0 or height <= 0:
            return
        border = 2 * self.BORDER
        inner_width = max(1, self.width() - border)
        wanted = int(inner_width * height / width) + border
        screen = self.screen() or QGuiApplication.primaryScreen()
        limit = screen.availableGeometry().height() - 40 if screen else 1000
        if wanted > limit:
            # Trop haute (une video verticale) : c'est la largeur qui cede.
            inner_height = limit - border
            self.resize(int(inner_height * width / height) + border, limit)
        else:
            self.resize(self.width(), max(self.minimumHeight(), wanted))

    def position(self) -> int:
        """L'instant atteint, en millisecondes -- ou celui qu'on attend encore."""
        if self._pending_ms:
            return self._pending_ms
        return max(0, self.player.player.position())

    def playing(self) -> bool:
        return (self.player.player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState)

    def geometry_to_keep(self) -> list:
        rect = self.normalGeometry() if self.isFullScreen() else self.geometry()
        return [rect.x(), rect.y(), rect.width(), rect.height()]

    def _place(self, geometry) -> None:
        """La place retenue, si elle tient encore sur un ecran ; sinon, en bas
        a droite de l'ecran principal, a un quart de sa largeur."""
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 720)
        if geometry and len(geometry) == 4:
            rect = QRect(*[int(v) for v in geometry])
            for candidate in QGuiApplication.screens():
                if candidate.availableGeometry().intersects(rect):
                    self.setGeometry(rect)
                    return
        width = max(420, area.width() // 4)
        height = int(width * 9 / 16) + 2 * self.BORDER
        self.setGeometry(area.right() - width - 24, area.bottom() - height - 48,
                         width, height)

    # -- les bords : on les saisit pour agrandir ------------------------------
    def _edges(self, point) -> Qt.Edges:
        grip = self.BORDER + 4
        edges = Qt.Edges()
        if point.x() <= grip:
            edges |= Qt.LeftEdge
        if point.x() >= self.width() - grip:
            edges |= Qt.RightEdge
        if point.y() <= grip:
            edges |= Qt.TopEdge
        if point.y() >= self.height() - grip:
            edges |= Qt.BottomEdge
        return edges

    def mouseMoveEvent(self, event):
        edges = self._edges(event.position().toPoint())
        if edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeFDiagCursor)
        elif edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeBDiagCursor)
        elif edges & (Qt.LeftEdge | Qt.RightEdge):
            self.setCursor(Qt.SizeHorCursor)
        elif edges & (Qt.TopEdge | Qt.BottomEdge):
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edges = self._edges(event.position().toPoint())
            handle = self.windowHandle()
            if edges and handle is not None:
                handle.startSystemResize(edges)
                return
        super().mousePressEvent(event)

    # -- fermeture ------------------------------------------------------------
    def release(self, target=None) -> None:
        self.player.release(target)

    def close_quietly(self) -> None:
        """Se retire : la video est rendue, les fichiers laches."""
        self.watch.stop()
        self.bar.hide()
        self.title.hide()
        self.player.marks.hide()
        if self.isFullScreen():
            self.showNormal()
        self.player.release()
        self.path = ""
        self._fitted = ""
        self.auto = False
        self.hide()

    def closeEvent(self, event):
        # Alt+F4 : on revient dans Prisme, on ne detruit rien.
        event.ignore()
        self.returnRequested.emit()

    # -- gestes ---------------------------------------------------------------
    def _reveal(self) -> None:
        if self.path:
            self.revealRequested.emit(self.path)

    def toggle_full(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
            else:
                self.returnRequested.emit()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if not event.isAutoRepeat():
                self.player.toggle_pause()
            return
        if key == Qt.Key_Left and not event.modifiers():
            self.stepRequested.emit(-1)
            return
        if key == Qt.Key_Right and not event.modifiers():
            self.stepRequested.emit(1)
            return
        if key == Qt.Key_F11:
            self.toggle_full()
            return
        # Le reste (destinations, Suppr, notes, Ctrl+M…) : la fenetre de
        # Prisme sait deja quoi en faire, sur la meme video.
        self.keyForwarded.emit(event)

    # -- bandeau --------------------------------------------------------------
    def _watch(self) -> None:
        """Le bandeau au survol de l'image, le trait fin le reste du temps ;
        le titre toujours, discret, en haut a gauche."""
        if not self.isVisible() or self.isMinimized() or not self.path:
            self.bar.hide()
            self.title.hide()
            self.player.marks.hide()
            return
        area = self.player.video_area
        corner = area.mapToGlobal(QPoint(0, 0))
        spot = QPoint(corner.x() + 6, corner.y() + 6)
        if self.title.pos() != spot:
            self.title.move(spot)
        if self.title.isHidden():
            self.title.show()
            self.title.raise_()
        over = QRect(corner.x(), corner.y(), area.width(),
                     area.height()).contains(QCursor.pos())
        if over:
            self.player.marks.hide()
            wanted = "pause" if self.playing() else "play"
            button = self.bar.pause_button
            if button.property("glyph") != wanted:
                button.setProperty("glyph", wanted)
                button.setIcon(icon(wanted))
            self.bar.place_on(area)
            self.bar.reveal()
        else:
            self.bar.hide()
            self.player.marks.place_on(area)


# ---------------------------------------------------------------------------
# Prisme est-il encore visible ?
# ---------------------------------------------------------------------------
def covered(widget, samples: int = 5) -> bool:
    """Vrai si la zone de `widget` est recouverte par une autre application.

    Qt ne sait pas si une fenetre est cachee par une autre. Windows, lui,
    dit quelle fenetre se trouve sous un point de l'ecran : on sonde le
    centre et quelques points autour. Une fenetre de Prisme (bandeau, lecteur
    flottant, dialogue) ne compte pas -- seules celles d'un autre programme.
    """
    if sys.platform != "win32" or widget is None or not widget.isVisible():
        return False
    if QGuiApplication.platformName() != "windows":
        # Hors ecran (les tests), la fenetre n'est pas sur le bureau : ce
        # qu'on trouverait sous ses coordonnees serait une autre fenetre.
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        mine = os.getpid()
        ratio = widget.devicePixelRatioF() or 1.0
        width, height = widget.width(), widget.height()
        points = [(0.5, 0.5), (0.25, 0.25), (0.75, 0.25), (0.25, 0.75),
                  (0.75, 0.75)][:max(1, samples)]
        foreign = 0
        for fx, fy in points:
            spot = widget.mapToGlobal(QPoint(int(width * fx), int(height * fy)))
            point = wintypes.POINT(int(spot.x() * ratio), int(spot.y() * ratio))
            hwnd = user32.WindowFromPoint(point)
            if not hwnd:
                continue
            root = user32.GetAncestor(hwnd, 2) or hwnd       # GA_ROOT
            owner = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(root, ctypes.byref(owner))
            if owner.value != mine:
                foreign += 1
        # La majorite des points : une petite fenetre posee dans un coin ne
        # cache pas la video.
        return foreign * 2 > len(points)
    except (OSError, AttributeError, ValueError):
        return False


def application_active() -> bool:
    """Une fenetre de Prisme a-t-elle le clavier ?"""
    return QApplication.activeWindow() is not None
