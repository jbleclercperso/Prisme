"""Entête : où l'on est, ce qu'on regarde, et comment.

Les cinq boutons d'avant mélangeaient deux questions distinctes — *quoi* je
regarde et *comment* — sans jamais dire laquelle était en cours. Deux sélecteurs
segmentés les séparent, et un fil d'Ariane répond à la troisième question, celle
que l'on se pose le plus souvent : où suis-je.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIntValidator
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QWidgetAction,
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from .icons import dress
from .widgets import FlowLayout

# Trois facons de regarder la meme collection. Ce ne sont pas trois
# applications : ouvrir une video depuis n'importe quel onglet mene toujours
# a la meme fiche, avec ses destinations et sa note.
TAB_FOLDERS = "folders"
TAB_VIDEOS = "videos"
TAB_EDIT = "edit"
TAB_TAGS = "tags"
# Plusieurs videos verticales cote a cote : un ecran large en contient
# trois, la ou une seule y laisse deux bandes noires.
TAB_SPLIT = "split"
# L'edition n'est plus un onglet mais l'etage du dessous : on y entre
# en cliquant une carte, on en sort par Echap.
# Ce qu'on a mis de cote : dossiers et videos, ensemble.
TAB_FAVS = "favs"
TABS = (TAB_FOLDERS, TAB_VIDEOS, TAB_TAGS, TAB_SPLIT, TAB_FAVS)

# Conserves pour les appels existants : un onglet dit a la fois quoi et comment.
CONTENT_FOLDERS = TAB_FOLDERS
CONTENT_VIDEOS = TAB_VIDEOS
VIEW_BROWSE = "browse"
VIEW_EDIT = "edit"

HEADER_STYLE = """
QFrame#segment { background: #14181e; border: 1px solid #262c35;
                 border-radius: 7px; }
QPushButton#segmentChoice { background: transparent; border: 0;
                            border-radius: 5px; padding: 5px 11px;
                            color: #8b94a1; font-size: 13px; }
QPushButton#chip { background: transparent; border: 1px solid #262c35;
                   border-radius: 5px; padding: 4px 12px; color: #8b94a1;
                   font-size: 13px; }
QPushButton#chip:hover { color: #dfe6ee; }
QPushButton#chip[chosen="true"] { background: #262c35; border-color: #39414d;
                                  color: #ffffff; }
QPushButton#sortChip { background: transparent; border: 1px solid #262c35;
                       border-radius: 5px; padding: 4px 9px; color: #8b94a1;
                       font-size: 13px; }
QPushButton#sortChip:hover { color: #dfe6ee; }
QPushButton#sortChip[chosen="true"] { background: #262c35; border-color: #39414d;
                                      color: #ffffff; }
QPushButton#segmentChoice:hover { color: #dfe6ee; }
QPushButton#segmentChoice[chosen="true"] { background: #262c35; color: #ffffff; }
QPushButton#stepper { background: #14181e; border: 1px solid #2b323d;
                      border-radius: 5px; color: #b9c2cd; font-size: 14px;
                      padding: 2px 0; }
QPushButton#stepper:hover { background: #262c35; border-color: #39414d;
                            color: #ffffff; }
QPushButton#stepper:disabled { color: #4a515c; border-color: #1d222a; }
QPushButton#lineAction { background: #1a1f27; border: 1px solid #2b323d;
                         border-radius: 6px; padding: 4px 11px 4px 8px;
                         color: #cdd5df; font-size: 13px; }
QPushButton#lineAction:hover { background: #242a33; border-color: #5a6474;
                               color: #ffffff; }
QPushButton#lineAction:checked { background: #1d2a40; border-color: #4c8dff; }
QLabel#counter { color: #9fb0c4; font-size: 13px; }
QLabel#segmentLabel { color: #6f7885; font-size: 12px; }
QPushButton#crumb { background: transparent; border: 0; padding: 3px 6px;
                    color: #9fb0c4; font-size: 14px; }
QPushButton#crumb:hover { color: #ffffff; text-decoration: underline; }
QPushButton#crumb[last="true"] { color: #ffffff; font-weight: 600; }
QLabel#crumbSep { color: #4d5563; font-size: 14px; }
QLabel#crumb { color: #ffffff; font-weight: 600; font-size: 14px; }
"""


# Les chiffres de la note, en or, sur une rangee : on voit d'un coup ce qu'on
# choisit, et celui qui est pris.
RATING_CHOICE_STYLE = (
    "QPushButton { background: transparent; border: 1px solid transparent;"
    " border-radius: 6px; padding: 4px 8px; color: #c9d1db; font-size: 13px; }"
    "QPushButton[gold=\"true\"] { color: #f5c542; font-size: 15px;"
    " font-weight: 700; }"
    "QPushButton:hover { background: #222a35; }"
    "QPushButton[chosen=\"true\"] { border-color: #4c8dff;"
    " background: #1d2a40; }")

class Segmented(QWidget):
    """Deux ou trois choix exclusifs, celui en cours restant visiblement allumé."""

    chosen = Signal(str)

    def __init__(self, label: str, choices: list, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        caption = QLabel(label, self)
        caption.setObjectName("segmentLabel")
        layout.addWidget(caption)

        frame = QFrame(self)
        frame.setObjectName("segment")
        inner = QHBoxLayout(frame)
        inner.setContentsMargins(3, 3, 3, 3)
        inner.setSpacing(2)
        self.buttons: dict = {}
        for key, text, tip in choices:
            button = QPushButton(text, frame)
            button.setObjectName("segmentChoice")
            button.setToolTip(tip)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setProperty("chosen", "false")
            button.clicked.connect(lambda _c=False, k=key: self.chosen.emit(k))
            inner.addWidget(button)
            self.buttons[key] = button
        layout.addWidget(frame)

    def set_value(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.setProperty("chosen", "true" if name == key else "false")
            button.style().unpolish(button)
            button.style().polish(button)


class Chips(QWidget):
    """Petits boutons ronds, un seul actif : deux familles de mots-cles."""

    chosen = Signal(str)

    def __init__(self, choices: list, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.buttons: dict = {}
        for key, text, tip in choices:
            button = QPushButton(text, self)
            button.setObjectName("chip")
            button.setToolTip(tip)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setProperty("chosen", "false")
            button.clicked.connect(lambda _c=False, k=key: self.chosen.emit(k))
            layout.addWidget(button)
            self.buttons[key] = button
        layout.addStretch(1)

    def set_value(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.setProperty("chosen", "true" if name == key else "false")
            button.style().unpolish(button)
            button.style().polish(button)


class SortChips(QWidget):
    """Un critere par pastille : un clic decroissant, deux croissant, trois rien.

    Une liste deroulante obligeait a l'ouvrir pour savoir ce qui etait en cours,
    et a la reouvrir pour l'annuler. Ici l'etat se lit sans rien ouvrir, et
    l'ordre s'inverse du meme geste qui l'a pose.
    """

    chosen = Signal(str)

    CRITERIA = (("duration", "Durée"), ("size", "Taille"),
                ("resolution", "Résolution"))

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.key = ""
        self.order = ""
        self.buttons: dict = {}
        for key, text in self.CRITERIA:
            button = QPushButton(text, self)
            button.setObjectName("sortChip")
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setProperty("chosen", "false")
            button.setToolTip("Clic : du plus grand au plus petit — "
                              "reclic : l'inverse — troisième clic : au hasard")
            button.clicked.connect(lambda _c=False, k=key: self._cycle(k))
            layout.addWidget(button)
            self.buttons[key] = button
        self._repaint()

    def _cycle(self, key: str) -> None:
        if key != self.key:
            self.key, self.order = key, "desc"
        elif self.order == "desc":
            self.order = "asc"
        else:
            self.key, self.order = "", ""
        self._repaint()
        self.chosen.emit(f"{self.key}_{self.order}" if self.key else "random")

    def set_value(self, mode: str) -> None:
        key, _, order = (mode or "").rpartition("_")
        self.key = key if order in ("desc", "asc") else ""
        self.order = order if self.key else ""
        self._repaint()

    def _repaint(self) -> None:
        for key, text in self.CRITERIA:
            button = self.buttons[key]
            active = key == self.key
            arrow = " ▼" if self.order == "desc" else " ▲"
            button.setText(f"{text}{arrow}" if active else text)
            button.setProperty("chosen", "true" if active else "false")
            button.style().unpolish(button)
            button.style().polish(button)


class Breadcrumb(QWidget):
    """Chemin cliquable depuis la racine ouverte jusqu'au dossier courant."""

    jumped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(2)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._top: Path | None = None

    def append_leaf(self, text: str) -> None:
        """Ajoute un dernier segment qui ne correspond a aucun dossier.

        Un mot-cle n'existe pas sur le disque : sans cela, y entrer laissait le
        fil d'Ariane sur la racine, et l'on ne savait plus ou l'on etait.
        """
        if self.layout_.count():
            # Le ressort final, pose par set_path, doit rester en queue.
            item = self.layout_.takeAt(self.layout_.count() - 1)
            # Seul le dernier segment est en gras : celui qu'on regarde.
            for index in range(self.layout_.count()):
                widget = self.layout_.itemAt(index).widget()
                if widget is not None and widget.property("last") == "true":
                    widget.setProperty("last", "false")
                    widget.style().unpolish(widget)
                    widget.style().polish(widget)
            separator = QLabel("›", self)
            separator.setObjectName("crumbSep")
            self.layout_.addWidget(separator)
            label = QLabel(text if len(text) <= 70 else text[:67] + "…", self)
            label.setToolTip(text)
            label.setContentsMargins(6, 0, 4, 0)
            label.setObjectName("crumb")
            label.setProperty("last", "true")
            self.layout_.addWidget(label)
            self.layout_.addItem(item)

    def set_path(self, top, current) -> None:
        """Affiche la chaîne de `top` à `current`, chaque segment cliquable."""
        while self.layout_.count():
            child = self.layout_.takeAt(0)
            if child.widget():
                # Cachee tout de suite : detruite plus tard seulement, elle
                # restait un instant affichee sous le nouveau chemin.
                child.widget().hide()
                child.widget().deleteLater()
        if current is None:
            return

        self._top = Path(top) if top else Path(current)
        chain = []
        walk = Path(current)
        while True:
            chain.append(walk)
            if walk == self._top or walk.parent == walk or len(chain) > 16:
                break
            walk = walk.parent
        chain.reverse()

        for position, path in enumerate(chain):
            if position:
                separator = QLabel("›", self)
                separator.setObjectName("crumbSep")
                self.layout_.addWidget(separator)
            button = QPushButton(path.name or str(path), self)
            button.setObjectName("crumb")
            button.setToolTip(str(path))
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.setProperty("last", "true" if position == len(chain) - 1 else "false")
            button.clicked.connect(lambda _c=False, p=str(path): self.jumped.emit(p))
            self.layout_.addWidget(button)
        self.layout_.addStretch(1)


class ControlBar(QWidget):
    """Chercher, classer, paginer — sur une seule rangée.

    Elle en occupait trois, repliees, et mangeait la moitie haute de la fenetre
    pour des reglages qui servent rarement : deux champs de texte, quatre listes
    deroulantes, un bouton de remise a zero. L'ecran appartient aux vignettes.
    Ce qui reste est ce qu'on touche vraiment : chercher un nom, classer, et
    regler la densite de la planche.
    """

    changed = Signal()
    sortChanged = Signal(str)
    columnsChanged = Signal(int)
    previousPage = Signal()
    nextPage = Signal()
    randomHere = Signal()
    released = Signal()
    unseenChanged = Signal(bool)
    clearRequested = Signal()
    starsChanged = Signal(int)       # -1 : toutes ; 0 a 5 : exactement

    RESOLUTIONS = (
        ("toutes", 0), ("360p", 360), ("480p", 480), ("720p", 720),
        ("1080p", 1080), ("1440p", 1440), ("4K", 2160),
    )
    SORTS = (
        ("name", "Nom"), ("duration_desc", "Durée ▼"), ("duration_asc", "Durée ▲"),
        ("stars_desc", "Note ▼"), ("size_desc", "Taille ▼"), ("random", "Au hasard"),
    )

    def __init__(self, columns_choices, parent=None):
        super().__init__(parent)
        # Une disposition qui se replie, et non une rangee rigide : un
        # QHBoxLayout impose sa largeur a la fenetre entiere, qui ne peut alors
        # plus retrecir et laisse tout deborder de l'ecran.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = FlowLayout(spacing=8)
        outer.addLayout(row)

        self.include = _Field("chercher…", 150)
        # Conserve sans etre montre : le filtre d'exclusion garde sa place dans
        # les criteres et dans la configuration, il n'occupe plus l'ecran.
        self.exclude = _Field("", 0)
        self.exclude.hide()
        row.addWidget(self.include)

        self.sorts = SortChips(self)
        self.sorts.chosen.connect(self.sortChanged)
        row.addWidget(self.sorts)

        # La note : non pas « au moins », mais « exactement ». On cherche
        # ses cinq etoiles, pas tout ce qui en a au moins quatre. Le choix
        # tient sur une rangee — tout, 1 a 5 en chiffres d'or, sans note —
        # et non plus dans une colonne d'etoiles qu'il fallait dechiffrer.
        self.stars_pick = -1
        self.rating_pick = QPushButton("★ Note", self)
        self.rating_pick.setObjectName("sortChip")
        self.rating_pick.setCursor(Qt.PointingHandCursor)
        self.rating_pick.setFocusPolicy(Qt.NoFocus)
        self.rating_pick.setProperty("chosen", "false")
        self.rating_pick.setToolTip("N'afficher que les éléments de cette note")
        self.rating_menu = QMenu(self.rating_pick)
        self.rating_menu.setStyleSheet(
            "QMenu { background: #151a21; border: 1px solid #2a313b;"
            " border-radius: 8px; padding: 6px; }")
        strip = QWidget(self.rating_menu)
        strip_row = QHBoxLayout(strip)
        strip_row.setContentsMargins(4, 2, 4, 2)
        strip_row.setSpacing(4)
        self.rating_choices: dict = {}
        for value, text, tip in ([(-1, "Toutes", "Toutes les notes")]
                                 + [(n, str(n), f"Notés exactement {n}")
                                    for n in range(1, 6)]
                                 + [(0, "Sans", "Sans note")]):
            choice = QPushButton(text, strip)
            choice.setObjectName("ratingChoice")
            choice.setCursor(Qt.PointingHandCursor)
            choice.setFocusPolicy(Qt.NoFocus)
            choice.setToolTip(tip)
            choice.setProperty("gold", "true" if value > 0 else "false")
            choice.setMinimumWidth(34 if value > 0 else 58)
            choice.setStyleSheet(RATING_CHOICE_STYLE)
            choice.clicked.connect(
                lambda _c=False, v=value: self._choose_rating(v))
            strip_row.addWidget(choice)
            self.rating_choices[value] = choice
        action = QWidgetAction(self.rating_menu)
        action.setDefaultWidget(strip)
        self.rating_menu.addAction(action)
        self.rating_pick.setMenu(self.rating_menu)
        row.addWidget(self.rating_pick)

        # Ce qui reste a voir : ni decide, ni deja regarde. Au bout d'une
        # semaine sur cent mille videos, c'est la seule vue qui compte.
        self.unseen = QPushButton("Non vus", self)
        self.unseen.setObjectName("sortChip")
        self.unseen.setCursor(Qt.PointingHandCursor)
        self.unseen.setFocusPolicy(Qt.NoFocus)
        self.unseen.setProperty("chosen", "false")
        self.unseen.setToolTip("Ne montrer que ce qui n'a été ni décidé, "
                               "ni regardé plus de cinq secondes")
        self.unseen.clicked.connect(
            lambda _c=False: self.unseenChanged.emit(
                self.unseen.property("chosen") != "true"))
        row.addWidget(self.unseen)

        # Le format : un seul bouton, qui dit ce qu'il montre. Deux boutons
        # « Verticales » et « Horizontales » allumes ensemble ne disaient pas
        # si allume voulait dire montre ou retire. Chaque clic passe au
        # suivant : tous les formats, verticales seules, horizontales seules.
        self.orientation = "all"
        self.format_button = QPushButton("", self)
        self.format_button.setObjectName("sortChip")
        self.format_button.setCursor(Qt.PointingHandCursor)
        self.format_button.setFocusPolicy(Qt.NoFocus)
        self.format_button.clicked.connect(self._cycle_orientation)
        row.addWidget(self.format_button)
        self._show_orientation()

        # Dossiers d'au moins, d'au plus tant de videos. Visible en onglet
        # Dossiers seulement.
        self.folder_min = QLineEdit(self)
        self.folder_min.setObjectName("bound")
        self.folder_min.setPlaceholderText("≥ vidéos")
        self.folder_min.setFixedWidth(68)
        self.folder_min.setValidator(QIntValidator(0, 999999, self))
        self.folder_max = QLineEdit(self)
        self.folder_max.setObjectName("bound")
        self.folder_max.setPlaceholderText("≤ vidéos")
        self.folder_max.setFixedWidth(68)
        self.folder_max.setValidator(QIntValidator(0, 999999, self))
        for field in (self.folder_min, self.folder_max):
            field.setToolTip("Ne garder que les dossiers qui comptent au moins, "
                             "au plus, ce nombre de vidéos")
            field.textChanged.connect(lambda _t: self.timer.start())
            row.addWidget(field)

        # Le hasard dans ce qu'on regarde : les resultats des filtres, ou le
        # dossier ou l'on est entre. Il ne parait que la ; sans filtre, le
        # hasard general, en haut, fait deja l'affaire.
        self.random_here = _button("", self.randomHere.emit)
        self.random_here.setObjectName("sortChip")
        dress(self.random_here, "shuffle", 17, "Au hasard")
        self.random_here.setToolTip("Une vidéo au hasard, parmi celles affichées")
        self.random_here.hide()

        # La densite se regle comme on regle un zoom : deux boutons et le
        # chiffre entre eux. Une liste deroulante et son etiquette « par rangee »
        # occupaient quatre fois la place pour le meme reglage, et il fallait
        # l'ouvrir pour savoir ou l'on en etait.
        self.columns_choices = list(columns_choices)
        # Pour les dossiers : le nombre de cartes par rangee en clair, comme
        # sur le mur, et non un ± qu'il faut cliquer plusieurs fois.
        self.column_chips = QWidget(self)
        chips = QHBoxLayout(self.column_chips)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(4)
        self.column_buttons: dict = {}
        for count in columns_choices:
            button = QPushButton(str(count), self.column_chips)
            button.setObjectName("splitButton")
            button.setFocusPolicy(Qt.NoFocus)
            button.setToolTip(f"{count} par rangée")
            button.clicked.connect(lambda _c=False, n=count: self._choose_columns(n))
            chips.addWidget(button)
            self.column_buttons[count] = button
        row.addWidget(self.column_chips)
        self.column_chips.hide()

        self.wider = _button("−", lambda: self._step_columns(-1))
        self.tighter = _button("+", lambda: self._step_columns(1))
        self.columns_label = QLabel("", self)
        self.columns_label.setObjectName("counter")
        self.columns_label.setAlignment(Qt.AlignCenter)
        self.columns_label.setFixedWidth(24)
        self.columns_label.setToolTip("Vignettes par rangée")
        for widget, tip, name in ((self.wider, "Des vignettes plus grandes", "minus"),
                                  (self.tighter, "Des vignettes plus petites", "plus")):
            widget.setObjectName("stepper")
            widget.setFixedSize(30, 28)
            dress(widget, name, 18)
            widget.setToolTip(tip)
            widget.setCursor(Qt.PointingHandCursor)
        # Conserve pour les appels existants, sans occuper l'ecran.
        self.columns = _combo([(n, str(n)) for n in columns_choices])
        self.columns.hide()
        self.columns_caption = self.columns_label
        # Quand un filtre est pose, on doit pouvoir le voir et le defaire d'un
        # geste : « 4 filtres » sans savoir lesquels, ni comment les oter,
        # c'est ce qu'on nous a reproche.
        self.clear = QPushButton("✕ filtres", self)
        self.clear.setObjectName("sortChip")
        self.clear.setCursor(Qt.PointingHandCursor)
        self.clear.setFocusPolicy(Qt.NoFocus)
        self.clear.setProperty("chosen", "true")
        self.clear.clicked.connect(self.clearRequested)
        self.clear.hide()
        row.addWidget(self.clear)

        row.addWidget(self.wider)
        row.addWidget(self.columns_label)
        row.addWidget(self.tighter)

        # Une seule etiquette dit ou l'on en est, et les fleches l'encadrent.
        self.count = QLabel("", self)
        self.count.setObjectName("counter")
        self.previous = _button("◂", self.previousPage.emit)
        self.next = _button("▸", self.nextPage.emit)
        for widget, tip, name in ((self.previous, "Page précédente", "chevron-left"),
                                  (self.next, "Page suivante", "chevron-right")):
            widget.setObjectName("stepper")
            widget.setFixedSize(30, 28)
            dress(widget, name, 20)
            widget.setToolTip(tip)
            widget.setCursor(Qt.PointingHandCursor)
        row.addWidget(self.previous)
        row.addWidget(self.count)
        row.addWidget(self.next)
        row.addWidget(self.random_here)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(220)
        self.timer.timeout.connect(self.changed)
        self.include.textChanged.connect(lambda _t: self.timer.start())
        self.include.released.connect(self.released)
        self.columns.currentIndexChanged.connect(
            lambda _i: self.columnsChanged.emit(int(self.columns.currentData()))
        )

    # -- lecture ---------------------------------------------------------
    FORMATS = ("all", "vertical", "horizontal")
    FORMAT_TEXT = {"all": "▯▭ Tous formats", "vertical": "▯ Verticales",
                   "horizontal": "▭ Horizontales"}

    def _cycle_orientation(self) -> None:
        order = self.FORMATS
        self.set_orientation(order[(order.index(self.orientation) + 1) % len(order)])
        self.changed.emit()

    def set_orientation(self, key: str) -> None:
        self.orientation = key if key in self.FORMATS else "all"
        self._show_orientation()

    def _show_orientation(self) -> None:
        button = self.format_button
        button.setText(self.FORMAT_TEXT[self.orientation])
        following = self.FORMATS[(self.FORMATS.index(self.orientation) + 1) % 3]
        button.setToolTip(
            f"Montré : {self.FORMAT_TEXT[self.orientation][2:].lower()}.\n"
            f"Cliquer : {self.FORMAT_TEXT[following][2:].lower()}.\n"
            "Une vidéo dont le format n'est pas encore connu n'apparaît "
            "que dans « tous formats ».")
        self._mark(button, self.orientation != "all")

    def _choose_rating(self, value: int) -> None:
        self.rating_menu.close()
        self.starsChanged.emit(value)

    @staticmethod
    def _mark(button, on: bool) -> None:
        button.setProperty("chosen", "true" if on else "false")
        button.style().unpolish(button)
        button.style().polish(button)

    def orientations(self) -> list:
        if self.orientation == "all":
            return ["vertical", "horizontal"]
        return [self.orientation]

    def set_orientations(self, keys) -> None:
        keys = [k for k in (keys or ()) if k in ("vertical", "horizontal")]
        self.set_orientation(keys[0] if len(keys) == 1 else "all")

    def set_folder_bounds(self, low: int, high: int) -> None:
        for field, value in ((self.folder_min, low), (self.folder_max, high)):
            field.blockSignals(True)
            field.setText(str(value) if value else "")
            field.blockSignals(False)

    def set_folder_fields_visible(self, on: bool) -> None:
        """Conserve : c'est `set_mode` qui decide desormais."""

    def criteria(self) -> dict:
        return {
            "include": self.include.text(),
            "exclude": self.exclude.text(),
            "orientations": self.orientations(),
            "stars_pick": self.stars_pick,
            "folder_min": int(self.folder_min.text() or 0),
            "folder_max": int(self.folder_max.text() or 0),
            "duration_op": "",
            "duration_s": 0.0,
            "resolution_op": "gte",
            "resolution": 0,
            "stars": -1,
        }

    def set_terms(self, include: str, exclude: str) -> None:
        for field, value in ((self.include, include), (self.exclude, exclude)):
            field.blockSignals(True)
            field.setText(value)
            field.blockSignals(False)

    def set_sort(self, mode: str) -> None:
        self.sorts.set_value(mode)

    def set_stars(self, pick: int) -> None:
        """Pose le choix, et le dit sur le bouton."""
        self.stars_pick = int(pick)
        if pick < 0:
            self.rating_pick.setText("★ Note")
        elif pick == 0:
            self.rating_pick.setText("★ Sans note")
        else:
            self.rating_pick.setText(f"★ {pick}")
        self._mark(self.rating_pick, pick >= 0)
        for value, choice in self.rating_choices.items():
            self._mark(choice, value == pick)

    def set_unseen(self, on: bool) -> None:
        self.unseen.setProperty("chosen", "true" if on else "false")
        self.unseen.style().unpolish(self.unseen)
        self.unseen.style().polish(self.unseen)

    def _step_columns(self, step: int) -> None:
        """Une vignette plus grande, ou plus petite, d'un cran."""
        current = int(self.columns.currentData() or self.columns_choices[0])
        try:
            index = self.columns_choices.index(current)
        except ValueError:
            index = 0
        index = max(0, min(len(self.columns_choices) - 1, index + step))
        chosen = self.columns_choices[index]
        if chosen == current:
            return
        self.set_columns(chosen)
        self.columnsChanged.emit(chosen)

    def _choose_columns(self, columns: int) -> None:
        self.set_columns(columns)
        self.columnsChanged.emit(columns)

    def set_columns(self, columns: int) -> None:
        for count, button in self.column_buttons.items():
            button.setProperty("chosen", "true" if count == columns else "false")
            button.style().unpolish(button)
            button.style().polish(button)
        index = self.columns.findData(columns)
        if index >= 0:
            self.columns.blockSignals(True)
            self.columns.setCurrentIndex(index)
            self.columns.blockSignals(False)
        self.columns_label.setText(str(columns))
        self.wider.setEnabled(columns > self.columns_choices[0])
        self.tighter.setEnabled(columns < self.columns_choices[-1])

    def set_page(self, text: str, has_previous: bool, has_next: bool) -> None:
        self.count.setText(text)
        self.count.setVisible(bool(text) and getattr(self, "_mode", "") != "wall")
        self.previous.setEnabled(has_previous)
        self.next.setEnabled(has_next)

    def set_browsing(self, browsing: bool) -> None:
        """La densité et la pagination ne valent qu'en parcours."""
        self._browsing = browsing
        self._lay_out_mode()

    def set_mode(self, mode: str) -> None:
        """Chaque onglet n'a que ce qui lui sert.

        « folders » : recherche, tris sans la note, bornes de dossier, cartes
        par rangee en clair, pagination. « videos » et « tags » : tout.
        « wall » : la recherche seule — ses reglages vivent a cote.
        """
        self._mode = mode
        self._lay_out_mode()

    def _lay_out_mode(self) -> None:
        mode = getattr(self, "_mode", "videos")
        browsing = getattr(self, "_browsing", True)
        wall = mode == "wall"
        folders = mode == "folders"
        self.sorts.setVisible(not wall)
        # Plus de notes de 1 a 5 : l'onglet Favoris remplace ce filtre.
        self.rating_pick.setVisible(False)
        self.unseen.setVisible(not wall and not folders)
        self.format_button.setVisible(not wall and not folders)
        self.folder_min.setVisible(folders)
        self.folder_max.setVisible(folders)
        # Le meme reglage − 5 + sur toutes les planches, dossiers compris.
        self.column_chips.hide()
        for widget in (self.wider, self.columns_label, self.tighter):
            widget.setVisible(browsing and not wall)
        for widget in (self.previous, self.next):
            widget.setVisible(browsing and not wall)
        self.count.setVisible(not wall and bool(self.count.text()))
        if wall:
            self.clear.hide()

    def set_wall(self, on: bool) -> None:
        """Conserve pour les appels existants : « wall » ou l'onglet courant."""
        if on:
            self.set_mode("wall")
        elif getattr(self, "_mode", "") == "wall":
            self.set_mode("videos")

    def set_filters(self, active: list) -> None:
        """Montre « ✕ filtres » si quelque chose est pose, et dit quoi."""
        if getattr(self, "_mode", "") == "wall":
            self.clear.hide()
            return
        self.clear.setVisible(bool(active))
        self.clear.setToolTip("Retirer : " + " · ".join(active) if active else "")
        self.count.setToolTip("Filtres actifs : " + " · ".join(active)
                              if active else "")

    def reset(self) -> None:
        for field in (self.include, self.exclude):
            field.blockSignals(True)
            field.clear()
            field.blockSignals(False)
        self.changed.emit()
        self.released.emit()

    def focus_search(self) -> None:
        self.include.setFocus()
        self.include.selectAll()


class _Field(QLineEdit):
    """Champ qui rend la main au clavier de tri sur Échap ou Entrée."""

    released = Signal()

    def __init__(self, placeholder: str, width: int, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setFixedWidth(width)
        # Une croix discrete a droite : effacer une recherche a la main, mot par
        # mot, est le genre de corvee qu'on remarque des la deuxieme fois.
        self.setClearButtonEnabled(True)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self.released.emit()
            return
        super().keyPressEvent(event)


def _combo(pairs: list) -> QComboBox:
    combo = QComboBox()
    for data, text in pairs:
        combo.addItem(text, data)
    return combo


def _button(text: str, slot) -> QPushButton:
    button = QPushButton(text)
    button.setFocusPolicy(Qt.NoFocus)
    button.clicked.connect(lambda _checked=False: slot())
    return button


# Une icone par entree du menu : on retrouve une ligne a sa forme, sans
# relire toute la liste.
MENU_ICONS = (
    ("Destinations", "folder-input"), ("Mots-clés", "tags"),
    ("Corbeille", "trash-2"), ("Arborescence", "folder-tree"),
    ("Recherche vidéo sur le web", "globe"), ("vignettes", "images"),
    ("Compter", "hash"), ("État des vignettes", "gauge"),
    ("titres", "captions"), ("plans", "film"), ("doublons", "copy"),
    ("Doublons", "copy"), ("Empreintes", "fingerprint"), ("Rafale", "timer"),
    ("Raccourcis", "keyboard"), ("gels", "activity"),
    ("dossiers masqués", "eye-off"), ("Partage", "share-2"),
    ("Où sont", "hard-drive"), ("mise à l'échelle", "monitor"),
    ("Réanalyser", "refresh-cw"), ("racine", "folder-cog"),
    ("Enregistrer", "bookmark-plus"), ("enregistrées", "bookmark"),
    ("Affichage", "monitor"), ("Collection", "hard-drive"),
    ("Connexion", "share-2"), ("Aide", "keyboard"), ("Recherches", "search"),
)


def menu_icon(text: str):
    from .icons import icon
    for needle, name in MENU_ICONS:
        if needle in text:
            return icon(name)
    return None


def build_overflow(parent, entries: list, menu: QMenu | None = None) -> QMenu:
    """Regroupe ce qui ne sert qu'occasionnellement derrière un seul bouton.

    Une entree dont le second terme est une liste devient un sous-menu : une
    colonne de vingt-cinq lignes ne se lisait plus, on y cherchait.
    """
    menu = QMenu(parent) if menu is None else menu
    for text, slot in entries:
        if text == "-":
            menu.addSeparator()
            continue
        if isinstance(slot, list):
            sub = menu.addMenu(text)
            found = menu_icon(text)
            if found is not None:
                sub.setIcon(found)
            build_overflow(parent, slot, sub)
            continue
        action = menu.addAction(text, slot)
        found = menu_icon(text)
        if found is not None:
            action.setIcon(found)
    return menu
