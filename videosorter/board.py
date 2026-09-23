"""Vue planche : les éléments en cartes, pour parcourir plutôt que décider.

Ce n'est pas un second logiciel mais une autre présentation du même contenu. La
racine, le filtre, l'arborescence et les raccourcis de destination restent ceux
du tri : seules changent la densité — vingt éléments au lieu d'un — et
l'intention, puisqu'un clic ouvre au lieu d'envoyer.
"""
from __future__ import annotations

import random

from PySide6.QtCore import QPoint, QRect, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QCursor, QPixmap, QRegion
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QPushButton,
    QFrame, QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from .perf import mark
from .scan import MODE_FOLDERS, human_duration, human_resolution, human_size
from .widgets import PlayMarks, elide

RATING_STYLE = ("QLabel { color: #f5c542; background: rgba(8, 10, 13, 190);"
                " border-radius: 4px; padding: 0 5px; font-size: 14px;"
                " font-weight: 700; }")

# Densites proposees : moins de colonnes, donc des cartes plus grandes.
COLUMN_CHOICES = (2, 3, 4, 5, 6, 7, 8, 9, 10)
DEFAULT_COLUMNS = 5
CARD_GAP = 6
MIN_CARD_WIDTH = 150
# Une carte coute cher a construire : six cents d'un coup prenaient plusieurs
# secondes. On n'en batit qu'une page, et l'on tourne les pages.
PAGE_SIZE = 40


class BoardCard(QFrame):
    """Un élément de la planche : image, nom, chiffres, note."""

    opened = Signal(int)
    asided = Signal(int)
    discarded = Signal(int)
    rated = Signal(int, int)
    played = Signal(int)
    picked = Signal(int, bool)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.setObjectName("boardCard")
        self.setProperty("hovered", "false")
        self.index = index
        self.video: str = ""
        self.ts: float = 0.0
        self.item = None
        self._resolution = ""
        self._pixmap: QPixmap | None = None
        self._scaled_for = None
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        # Des marges de quatre points : chaque pixel d'encadrement est pris
        # a l'image, et une planche en porte quarante.
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        self.image = QLabel(self)
        self.image.setObjectName("boardImage")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumHeight(110)
        self.image.setText("…")
        layout.addWidget(self.image)

        # Une seule ligne sous l'image, la meme que sous un apercu : ce qui
        # qualifie l'element, puis son nom. Les deux lignes d'avant — nom,
        # puis chiffres et etoiles — doublaient la hauteur du texte pour dire
        # ce qu'on lit deja sur l'image, et chaque planche en portait vingt.
        self.meta = QLabel("", self)
        self.meta.setObjectName("boardMeta")
        self.meta.setWordWrap(False)

        self.duration_chip = QLabel("", self)
        self.duration_chip.setObjectName("tileDuration")
        self.duration_chip.hide()

        # La note, en un chiffre d'or, en haut a gauche — sous la place de la
        # case a cocher. Cinq etoiles dessinees sur chaque carte faisaient un
        # damier ; un chiffre se lit d'un coup d'oeil.
        self.stars_value = 0
        self.rating = QLabel("", self)
        self.rating.setObjectName("cardRating")
        self.rating.setStyleSheet(RATING_STYLE)
        self.rating.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.rating.hide()

        # Elle ne se montre qu'au survol, ou si elle est cochee : une case par
        # vignette, visible en permanence, ferait un damier avant de faire une
        # planche.
        # Sous l'image, avec le texte : posees dessus, elles disparaissaient
        # des que le lecteur d'apercu s'affichait — il couvre l'image, et sous
        # Windows sa fenetre native passe devant tout. Elles etaient visibles
        # une demi-seconde, puis inatteignables.
        self.pick = QCheckBox(self)
        self.pick.setObjectName("cardPick")
        self.pick.setCursor(Qt.PointingHandCursor)
        self.pick.setFocusPolicy(Qt.NoFocus)
        # Rien que la case : sans taille fixe, le widget s'etalait en bandeau
        # noir sur toute la largeur de la vignette.
        self.pick.setFixedSize(20, 20)
        self.pick.toggled.connect(
            lambda on: self.picked.emit(self.index, bool(on)))

        # Rejeter d'un clic, au coin oppose de la case a cocher. Garder, c'est
        # passer au suivant ; rejeter demandait jusqu'ici le clavier, ce qui
        # obligeait a lacher la souris a chaque decision.
        # Conservee pour les branchements, mais plus jamais montree : la croix
        # sur chaque vignette a ete retiree a la demande.
        self.discard = QPushButton("✕", self)
        self.discard.setObjectName("cardDiscard")
        self.discard.setToolTip("Écarter — récupérable dans la corbeille de session")
        self.discard.setCursor(Qt.PointingHandCursor)
        self.discard.setFocusPolicy(Qt.NoFocus)
        self.discard.setFixedSize(22, 22)
        self.discard.clicked.connect(
            lambda _c=False: self.discarded.emit(self.index))
        # Rien que le nom sous l'image : la ligne doit etre la plus courte
        # possible, chaque pixel rendu va a la video. Coche et croix se posent
        # sur l'image, en haut, et n'apparaissent qu'au survol ou cochees.
        layout.addWidget(self.meta)
        self.pick.hide()
        self.discard.hide()

        # Sans cela, un clic tombant sur l'image ou le texte n'atteindrait pas
        # la carte : seules ses marges auraient repondu.
        for child in (self.image, self.meta, self.duration_chip):
            child.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _line(self, lead: str = "") -> str:
        """« 1080p · nom » pour une video ; pour un dossier, son seul nom.

        Le compte de ses videos est passe dans la pastille, ou se trouvait une
        duree qui ne voulait rien dire : celle de l'unique video dont l'image
        sert de vignette, et non du dossier.
        """
        item = self.item
        if item is None:
            return ""
        head = "" if item.kind == MODE_FOLDERS else (lead or self._resolution)
        name = elide(item.name, 38)
        line = f"{head}   ·   {name}" if head else name
        # Une seule ligne sous l'image : le dossier est dans l'infobulle.
        return line

    def _show_chip(self, text: str) -> None:
        self.duration_chip.setText(text)
        self.duration_chip.adjustSize()
        self._place_chip()
        self.duration_chip.raise_()
        self.duration_chip.show()

    def _place_chip(self) -> None:
        """En haut a droite de l'image : c'est la qu'on la cherche."""
        chip = self.duration_chip
        area = self.image.geometry()
        chip.move(area.right() - chip.width() - 8, area.top() + 8)

    def _place_handles(self) -> None:
        """Coche en haut a gauche de l'image, la note juste dessous."""
        area = self.image.geometry()
        self.pick.move(area.left() + 6, area.top() + 6)
        self.pick.raise_()
        self.rating.adjustSize()
        self.rating.move(area.left() + 6, area.top() + 30)
        self.rating.raise_()

    def handle_rect(self) -> QRect:
        """La bande du haut de l'image, en coordonnees globales : la coche,
        et la note dessous."""
        area = self.image.geometry()
        top_left = self.mapToGlobal(area.topLeft())
        return QRect(top_left.x(), top_left.y(), area.width(), 58)

    def set_item(self, item, stars: int) -> None:
        self.item = item
        self.video = ""
        self._pixmap = None
        self._scaled_for = None
        self._resolution = ""
        self.image.setPixmap(QPixmap())
        self.image.setText("…")
        self.set_picked(False)
        self.set_stars(stars)
        self.meta.setText(self._line())
        count = (f"{item.video_count} vidéo{'s' if item.video_count > 1 else ''}\n"
                 if item.kind == MODE_FOLDERS else "")
        self.meta.setToolTip(f"{item.path}\n{count}{human_size(item.size)}")
        if item.kind == MODE_FOLDERS:
            # Le seul chiffre : sur une pastille posee au coin d'une image, le
            # mot « videos » ne dit rien que la vignette ne montre deja, et il
            # prend la moitie de la place.
            self._show_chip(str(item.video_count))
        else:
            self.duration_chip.hide()
        self.set_state(item.status)

    def set_stars(self, stars: int) -> None:
        """La note en un chiffre d'or ; rien du tout sans note."""
        self.stars_value = int(stars or 0)
        self.rating.setText(str(self.stars_value) if self.stars_value else "")
        self.rating.setVisible(self.stars_value > 0)
        if self.stars_value:
            self._place_handles()

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
        if resolution and resolution != self._resolution:
            self._resolution = resolution
            self.meta.setText(self._line(resolution))
        item = self.item
        if item is not None and item.kind == MODE_FOLDERS:
            return          # la pastille compte deja ses videos
        if duration:
            self._show_chip(human_duration(duration))

    def set_info(self, duration: float, height: int) -> None:
        """Ce que le sondage a fini par apprendre, une fois l'image posée."""
        if self.item is None:
            return
        self.set_source(self.video, self.ts, duration, height)

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
        self.image.setFixedHeight(int((width - 8) * 9 / 16))
        self._rescale()

    def _rescale(self) -> None:
        if self._pixmap is None:
            return
        # Un lissage par carte et par taille, pas un par evenement : la meme
        # image etait relissee deux fois a chaque re-mise en page, pour
        # trente cartes, et la planche s'en ressentait a chaque onglet.
        wanted = (self.image.width(), self.image.height(), id(self._pixmap))
        if wanted == self._scaled_for:
            return
        self._scaled_for = wanted
        self.image.setPixmap(self._pixmap.scaled(
            self.image.width(), self.image.height(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        ))

    def set_hovered(self, hovered: bool) -> None:
        self.setProperty("hovered", "true" if hovered else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self._show_handles(hovered)

    def _show_handles(self, hovered: bool) -> None:
        self.pick.setVisible(hovered or self.pick.isChecked())
        if hovered or self.pick.isChecked():
            self._place_handles()

    def set_picked(self, picked: bool) -> None:
        """Pose ou retire la coche sans reemettre le signal."""
        self.pick.blockSignals(True)
        self.pick.setChecked(picked)
        self.pick.blockSignals(False)
        self.pick.setVisible(picked or self.property("hovered") == "true")
        if self.pick.isVisible():
            self._place_handles()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()
        if not self.duration_chip.isHidden():
            self._place_chip()
        self._place_handles()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.opened.emit(self.index)
        elif event.button() == Qt.RightButton:
            # Le clic droit regarde sans quitter : la video s'ouvre a cote, et
            # la planche reste sous la main.
            self.asided.emit(self.index)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.video:
            self.played.emit(self.index)


class HoverHandles(QWidget):
    """Coche et croix flottantes, au-dessus de la carte survolee.

    Le lecteur d'apercu est une fenetre native : il passe devant tout ce qu'on
    pose dans la carte, et la coche devenait inatteignable des que la video
    demarrait. Une fenetre-outil sans cadre, elle, reste devant. Seuls ses
    deux boutons recoivent la souris : le reste de la bande est transparent
    et laisse passer les clics vers la carte.
    """

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 0)
        layout.setSpacing(0)
        self.pick = QCheckBox(self)
        self.pick.setObjectName("cardPick")
        self.pick.setCursor(Qt.PointingHandCursor)
        self.pick.setFocusPolicy(Qt.NoFocus)
        self.pick.setFixedSize(20, 20)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        column.addWidget(self.pick, 0, Qt.AlignLeft)
        # La note reste lisible pendant que l'apercu joue : la video, native,
        # couvrirait celle de la carte.
        self.rating = QLabel("", self)
        self.rating.setObjectName("cardRating")
        self.rating.setStyleSheet(RATING_STYLE)
        self.rating.hide()
        column.addWidget(self.rating, 0, Qt.AlignLeft)
        column.addStretch(1)
        layout.addLayout(column)
        layout.addStretch(1)
        self.card = None
        self.hide()

    def attach(self, card, rect: QRect) -> None:
        """Se pose sur cette carte, et relaie ses gestes a ses propres poignees."""
        if self.card is not card:
            self.card = card
            self.pick.blockSignals(True)
            self.pick.setChecked(card.pick.isChecked())
            self.pick.blockSignals(False)
            self.rating.setText(str(card.stars_value) if card.stars_value else "")
            self.rating.setVisible(card.stars_value > 0)
            self.setGeometry(QRect())
        if self.geometry() != rect:
            self.setGeometry(rect)
            self.layout().activate()
            region = QRegion(self.pick.geometry())
            if not self.rating.isHidden():
                region = region.united(QRegion(self.rating.geometry()))
            self.setMask(region)
        if self.isHidden():
            self.show()
        self.raise_()

    def detach(self) -> None:
        self.card = None
        self.hide()


class BoardView(QWidget):
    """Grille défilante de cartes, avec lecture au survol."""

    openRequested = Signal(int)
    asideRequested = Signal(int)
    discardRequested = Signal(int)
    pickedChanged = Signal(int)
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
        self.grid.setSpacing(CARD_GAP)
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

        # Trait d'avancement et temps restant, poses sur l'image survolee.
        # Sur un dossier, le temps restant se tait : la pastille dit deja
        # combien de videos il contient, un compte a rebours par-dessus
        # brouillait les deux.
        self.marks = PlayMarks(self)
        # Aucune sortie audio : un aperçu survolé se regarde, il ne s'écoute
        # pas. Sans sortie, Qt ne décode pas la piste son du tout — c'est
        # autant de travail et de bande passante réseau en moins par vignette.
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video)
        # Le widget video garde a l'ecran la derniere image affichee. En passant
        # d'une carte a l'autre, on le deplacait puis on le montrait des que le
        # media etait charge : pendant une fraction de seconde, la nouvelle
        # vignette portait donc l'image de la precedente. On attend desormais
        # qu'une image du nouveau media soit reellement arrivee, et on vide la
        # surface entre-temps.
        self._awaiting_frame = False
        self.video.videoSink().videoFrameChanged.connect(self._on_frame)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.positionChanged.connect(self._on_position)
        self.player.errorOccurred.connect(self._on_error)
        self._pending_seek = 0
        self._segment_start = 0
        self.unplayable: set = set()

        # Les vignettes ne sont fabriquees que pour les cartes reellement a
        # l ecran : en demander soixante d un coup saturait le reseau avant que
        # la premiere rangee ne s affiche.
        # Identifiants des elements coches : ils survivent au changement de
        # page, les cartes etant reutilisees d'une page a l'autre.
        self.picked_ids: set = set()
        self._pending_previews: set = set()
        self.visible_timer = QTimer(self)
        self.visible_timer.setSingleShot(True)
        self.visible_timer.setInterval(90)
        self.visible_timer.timeout.connect(self.request_visible)
        self.scroll.verticalScrollBar().valueChanged.connect(
            lambda _v: self.visible_timer.start()
        )
        # Arriver en bas fait passer a la page suivante. C'est le geste qu'on
        # fait naturellement, et il evite d'aller chercher une fleche a l'autre
        # bout de l'ecran pour continuer.
        self.scroll.verticalScrollBar().valueChanged.connect(self._maybe_next_page)
        self._at_end = False

        # Les cartes gardaient la largeur calculee au premier affichage :
        # agrandir la fenetre laissait une bande vide a droite, la retrecir les
        # faisait deborder.
        self.width_timer = QTimer(self)
        self.width_timer.setSingleShot(True)
        self.width_timer.setInterval(60)
        self.width_timer.timeout.connect(self._apply_widths)

        # Le widget video couvre l'image de la carte survolee, et sous Windows
        # une fenetre native avale les clics meme declaree transparente. Sans
        # cela, cliquer sur l'apercu en train de jouer ne faisait rien, et il
        # fallait viser le titre.
        self.video.mouseReleaseEvent = self._video_clicked

        self.floating = HoverHandles(self.window())
        self.floating.pick.toggled.connect(self._float_picked)
        # Le fantome : Qt livre parfois une image qui appartient encore au
        # fichier precedent, juste apres le changement de source. Deux
        # verrous, comme dans la fiche : rien avant que le nouveau media soit
        # charge, et rien avant un court noir — l'image ne revient qu'une
        # fois les deux leves.
        self._loaded = False
        self._blackout = False
        self.blackout_timer = QTimer(self)
        self.blackout_timer.setSingleShot(True)
        self.blackout_timer.setInterval(160)
        self.blackout_timer.timeout.connect(self._end_blackout)
        self.hover_timer = QTimer(self)
        self.hover_timer.setInterval(80)
        self.hover_timer.timeout.connect(self._poll_hover)

    # -- contenu ---------------------------------------------------------
    def showEvent(self, event):
        super().showEvent(event)
        self.hover_timer.start()

    def hideEvent(self, event):
        self.floating.detach()
        super().hideEvent(event)
        self.hover_timer.stop()
        self.stop()

    def set_muted(self, muted: bool) -> None:
        """Sans piste son décodée, il n'y a rien à couper : conservé pour l'appel."""

    def _ensure_cards(self, count: int) -> None:
        while len(self.cards) < count:
            card = BoardCard(len(self.cards), self.canvas)
            card.opened.connect(self.openRequested)
            card.asided.connect(self.asideRequested)
            card.discarded.connect(self.discardRequested)
            card.picked.connect(self._on_picked)
            card.rated.connect(self.rateRequested)
            card.played.connect(self._play_full)
            self.cards.append(card)

    def set_items(self, items: list, stars_of) -> None:
        """Remplit la planche, en ne refaisant que ce qui a changé.

        Les cartes dont l'élément n'a pas bougé gardent leur image : tout
        reconstruire ferait clignoter la planche et redemanderait des vignettes
        déjà obtenues.
        """
        self.floating.detach()
        self.hovered = -1
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
                card.set_state(item.status)
                card.set_stars(self._stars_of(item.path))
            # La coche appartient a l'element, pas a la carte : les cartes sont
            # reutilisees d'une page a l'autre.
            card.set_picked(item.item_id in self.picked_ids)
            self.grid.addWidget(card, slot // self.columns, slot % self.columns)
            card.show()
        self.pageChanged.emit(first + 1 if self.items else 0, last, len(self.items))
        self._pending_previews = set(needed)
        self.request_visible()

    def _on_picked(self, position: int, picked: bool) -> None:
        if not (0 <= position < len(self.items)):
            return
        item_id = self.items[position].item_id
        if picked:
            self.picked_ids.add(item_id)
        else:
            self.picked_ids.discard(item_id)
        self.pickedChanged.emit(len(self.picked_ids))

    def picked_items(self) -> list:
        """Les elements coches, dans l'ordre ou ils sont affiches."""
        return [item for item in self.items if item.item_id in self.picked_ids]

    def pick_all(self, value) -> None:
        """Coche tout (True), decoche tout (False), ou inverse (None)."""
        for item in self.items:
            if value is None:
                if item.item_id in self.picked_ids:
                    self.picked_ids.discard(item.item_id)
                else:
                    self.picked_ids.add(item.item_id)
            elif value:
                self.picked_ids.add(item.item_id)
            else:
                self.picked_ids.discard(item.item_id)
        for card in self.cards:
            if 0 <= card.index < len(self.items):
                card.set_picked(self.items[card.index].item_id in self.picked_ids)
        self.pickedChanged.emit(len(self.picked_ids))

    def clear_picked(self) -> None:
        self.picked_ids.clear()
        for card in self.cards:
            card.set_picked(False)
        self.pickedChanged.emit(0)

    def _video_clicked(self, event) -> None:
        """Renvoie le clic tombe sur l'apercu a la carte qui est dessous."""
        if not (0 <= self.hovered < len(self.cards)):
            return
        card = self.cards[self.hovered]
        if event.button() == Qt.LeftButton:
            self.openRequested.emit(card.index)
        elif event.button() == Qt.RightButton:
            self.asideRequested.emit(card.index)
        event.accept()

    def _maybe_next_page(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        at_end = value >= bar.maximum() - 4
        # Une seule fois par arrivee en bas : sans ce verrou, le moindre
        # tremblement de molette avalerait plusieurs pages d'affilee.
        if at_end and not self._at_end and self.page < self.total_pages() - 1:
            self._at_end = True
            self.set_page(self.page + 1)
            return
        self._at_end = at_end

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.width_timer.start()

    def _apply_widths(self) -> None:
        width = self._card_width()
        for card in self.cards:
            if not card.isHidden() and card.width() != width:
                card.set_card_width(width)
        self.visible_timer.start()

    def request_visible(self) -> None:
        """Reclame les apercus des seules cartes visibles dans la fenetre."""
        if not self._pending_previews:
            return
        area = self.scroll.viewport().rect()
        first, _last = self._page_bounds()
        done = set()
        for position in sorted(self._pending_previews):
            card = self._card_for(position)
            if card is None:
                done.add(position)
                continue
            top_left = card.mapTo(self.scroll.viewport(), QPoint(0, 0))
            if top_left.y() > area.height() + 400 or top_left.y() + card.height() < -400:
                continue
            self.previewNeeded.emit(position)
            done.add(position)
        self._pending_previews -= done

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
        self._pending_previews.add(position)
        self.visible_timer.start()

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
            card.set_stars(stars)

    def set_state(self, position: int, status: str) -> None:
        card = self._card_for(position)
        if card is not None:
            card.set_state(status)

    def set_source(self, position: int, entry) -> None:
        card = self._card_for(position)
        if card is not None:
            card.set_source(*entry)

    def set_info(self, position: int, duration: float, height: int) -> None:
        card = self._card_for(position)
        if card is not None:
            card.set_info(duration, height)

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
            if found != -1:
                # La fenetre principale a pu bouger, la planche defiler.
                self.floating.attach(self.cards[found], self.cards[found].handle_rect())
            return
        if self.hovered != -1 and self.hovered < len(self.cards):
            self.cards[self.hovered].set_hovered(False)
        self._blank()
        self.marks.clear()
        self.hovered = found
        if found == -1:
            self.floating.detach()
            self.stop()
            return
        self.cards[found].set_hovered(True)
        self.floating.attach(self.cards[found], self.cards[found].handle_rect())
        # Sans delai : la video part des que la souris est sur la carte.
        self._play(found)

    def _float_picked(self, on: bool) -> None:
        card = self.floating.card
        if card is not None:
            # Par la coche de la carte : c'est elle qui est branchee au reste.
            card.pick.setChecked(on)

    def _float_discard(self) -> None:
        card = self.floating.card
        if card is not None:
            card.discarded.emit(card.index)

    def _blank(self) -> None:
        """Cache l'apercu et efface ce qu'il restait de l'image precedente."""
        self._awaiting_frame = False
        self.video.hide()
        try:
            self.video.videoSink().setVideoFrame(QVideoFrame())
        except (RuntimeError, TypeError):
            pass

    def _on_frame(self, frame) -> None:
        """Premiere image du media survole : c'est maintenant qu'on l'affiche."""
        if not self._awaiting_frame or self.hovered == -1 or not self._loaded:
            return
        try:
            valid = frame.isValid()
        except (RuntimeError, AttributeError):
            valid = True
        if not valid:
            return
        self._awaiting_frame = False
        if not self._blackout:
            self.video.show()

    def _end_blackout(self) -> None:
        self._blackout = False
        if not self._awaiting_frame and self.hovered != -1 and self._loaded:
            self.video.show()

    def _play(self, position: int) -> None:
        card = self.cards[position]
        if not card.video or card.video in self.unplayable:
            self._blank()
            return
        area = card.image
        origin = area.mapTo(self.canvas, QPoint(0, 0))
        self.video.setGeometry(origin.x(), origin.y(), area.width(), area.height())
        self.video.raise_()

        self._segment_start = int(card.ts * 1000)
        self._pending_seek = self._segment_start
        url = QUrl.fromLocalFile(card.video)
        mark("board.play")
        self._awaiting_frame = True
        self._blackout = True
        self.blackout_timer.start()
        if self.player.source() == url:
            self.player.setPosition(self._segment_start)
        else:
            self._loaded = False
            self.player.setSource(url)
        self.player.play()

    def _on_status(self, status) -> None:
        loaded = (QMediaPlayer.MediaStatus.LoadedMedia,
                  QMediaPlayer.MediaStatus.BufferedMedia)
        if status in loaded:
            self._loaded = True
            if self._pending_seek:
                self.player.setPosition(self._pending_seek)
                self._pending_seek = 0
            # L'affichage n'a plus lieu ici : charge ne veut pas dire affiche,
            # et montrer le widget a cet instant devoilait l'image du media
            # precedent. C'est _on_frame qui decide.
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(self._segment_start)
            self.player.play()

    def _on_position(self, position: int) -> None:
        if self.hovered == -1 or self.video.isHidden():
            self.marks.clear()
            return
        if position > self._segment_start + self.preview_seconds * 1000:
            self.player.setPosition(self._segment_start)
        duration = self.player.duration()
        if duration > 0 and self.hovered < len(self.cards):
            card = self.cards[self.hovered]
            folder = card.item is not None and card.item.kind == MODE_FOLDERS
            self.marks.with_left = not folder
            self.marks.set_progress(position, duration)
            self.marks.place_on(card.image)

    def _on_error(self, *_args) -> None:
        if 0 <= self.hovered < len(self.cards) and self.cards[self.hovered].video:
            self.unplayable.add(self.cards[self.hovered].video)
        self._blank()
        self.player.stop()

    def stop(self) -> None:
        self.player.stop()
        self._blank()
        self.marks.clear()
        for card in self.cards:
            card.set_hovered(False)
        self.hovered = -1
