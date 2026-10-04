"""Entête : où l'on est, ce qu'on regarde, et comment.

Les cinq boutons d'avant mélangeaient deux questions distinctes — *quoi* je
regarde et *comment* — sans jamais dire laquelle était en cours. Deux sélecteurs
segmentés les séparent, et un fil d'Ariane répond à la troisième question, celle
que l'on se pose le plus souvent : où suis-je.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon, QIntValidator, QPainter
from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QSizePolicy, QStyle, QStyleOptionButton, QStylePainter, QVBoxLayout, QWidget,
)

from .icons import dress, icon
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
                    color: #9fb0c4; font-size: 14px; text-align: left; }
QPushButton#crumb:hover { color: #ffffff; text-decoration: underline; }
QPushButton#crumb[last="true"] { color: #ffffff; font-weight: 600; }
QLabel#crumbSep { color: #4d5563; font-size: 14px; }
QLabel#crumb { color: #ffffff; font-weight: 600; font-size: 14px; }
QLineEdit#renameField { font-weight: 600; font-size: 14px; }
"""


class Segmented(QWidget):
    """Deux ou trois choix exclusifs, celui en cours restant visiblement allumé."""

    chosen = Signal(str)

    def __init__(self, label: str, choices: list, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Une legende vide occupait quand meme sa place et son ecart : les
        # onglets commencaient vingt pixels trop loin du bord.
        if label:
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
        self.compact = False
        self._texts = {key: text for key, text, _tip in choices}
        self._tips = {key: tip for key, _text, tip in choices}
        self.glyphs: dict = {}          # cle -> nom d'icone, pour l'etat compact

    def set_compact(self, on: bool) -> None:
        """Ecran etroit : l'icone seule, le nom passe dans l'infobulle. Le
        dernier repli de la premiere ligne, apres les boutons ronds."""
        on = bool(on) and bool(self.glyphs)
        if on == self.compact:
            return
        self.compact = on
        for key, button in self.buttons.items():
            name = self.glyphs.get(key)
            if on and name:
                button.setText("")
                button.setIcon(icon(name))
                button.setIconSize(QSize(17, 17))
                button.setToolTip(f"{self._texts[key].lstrip('★ ')} — {self._tips[key]}")
            else:
                button.setIcon(QIcon())
                button.setText(self._texts[key])
                button.setToolTip(self._tips[key])

    def compact_width(self) -> int:
        """La largeur une fois en icones, sans avoir a basculer pour la
        mesurer : une case de 36 points par choix, plus le cadre."""
        return len(self.buttons) * 36 + (len(self.buttons) - 1) * 2 + 6

    def set_value(self, key: str) -> None:
        for name, button in self.buttons.items():
            button.setProperty("chosen", "true" if name == key else "false")
            button.style().unpolish(button)
            button.style().polish(button)


class KindToggle(QPushButton):
    """Une seule icone, celle du mode en cours : la pellicule en videos,
    l'image en photos. Un clic passe a l'autre collection.

    Deux boutons « Vidéos | Photos » prenaient une place qu'un choix qu'on
    fait rarement ne merite pas.
    """

    chosen = Signal(str)

    # (cle, icone, ce que le bouton dit quand ce mode est le courant)
    KINDS = {
        "video": ("clapperboard", "Mode vidéos — cliquer pour trier les photos"),
        "photo": ("image", "Mode photos — cliquer pour trier les vidéos"),
    }

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.setObjectName("kindToggle")
        self.setFixedWidth(38)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.value = ""
        self.clicked.connect(
            lambda _c=False: self.chosen.emit("photo" if self.value == "video" else "video"))
        self.set_value("video")

    def set_value(self, key: str) -> None:
        key = "photo" if key == "photo" else "video"
        if key == self.value:
            return
        self.value = key
        glyph, tip = self.KINDS[key]
        dress(self, glyph, 20)
        self.setToolTip(tip)


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
        # Ses pastilles, et pas un pixel de plus. Le ressort qui les suivait
        # rendait l'ensemble extensible : sur la premiere ligne, il disputait
        # au fil d'Ariane la place qui lui revenait.
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

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

    CRITERIA = (("date", "Date"), ("duration", "Durée"), ("size", "Taille"),
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
            button.setToolTip(
                "Date de modification sur le disque. Clic : du plus récent au plus "
                "ancien — reclic : l'inverse — troisième clic : au hasard"
                if key == "date" else
                "Clic : du plus grand au plus petit — "
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


def _shrink(wants: list, floors: list, budget: int):
    """Largeurs qui tiennent dans `budget` en rognant les plus longues
    d'abord, chacune au plus jusqu'a son plancher ; None si meme les
    planchers ne tiennent pas."""
    def widths(level: int) -> list:
        return [min(want, max(floor, level)) for want, floor in zip(wants, floors)]

    if sum(floors) > budget:
        return None
    low, high = 0, max(wants, default=0)
    while low < high:
        middle = (low + high + 1) // 2
        if sum(widths(middle)) <= budget:
            low = middle
        else:
            high = middle - 1
    return widths(low)


# En deca de tant de pixels gagnes, on ne rogne pas un nom : « root » devenait
# « r…t » pour un seul pixel.
_NOT_WORTH = 12


def _floor_width(text: str, metrics, natural: int) -> int:
    """La plus petite largeur qui dise encore quelque chose d'un nom.

    Le dessin coupe au milieu : il lui faut au moins une lettre de chaque
    cote des points de suspension, sans quoi il ne reste que « … » -- le
    plancher calcule sur « ab… » etait trop etroit pour cela, et le dossier
    parent s'affichait « … ». Plutot que de deviner comment Qt partage la
    place (il compte en fractions de pixel, et la moitie gauche doit loger
    sa premiere lettre), on lui demande : quelques dizaines d'essais, a
    peine une fraction de milliseconde.
    """
    margins = natural - metrics.horizontalAdvance(text)
    if len(text) <= 3:
        return natural
    room = None
    for width in range(metrics.horizontalAdvance("…"), natural - margins + 1):
        shown = metrics.elidedText(text, Qt.ElideMiddle, width)
        if len(shown) >= 3 and shown[0] != "…" and shown[-1] != "…":
            room = width
            break
    if room is None:
        return natural
    shortest = margins + room
    if natural - shortest < _NOT_WORTH:
        return natural
    return min(natural, shortest)


def _cached_floor(widget) -> int:
    """Le plancher d'un segment, calcule une fois par nom et par police : le
    fil d'Ariane le redemande a chaque redimensionnement."""
    natural = widget.natural()
    key = (widget.text(), widget.font().toString(), natural)
    if getattr(widget, "_floor_key", None) != key:
        widget._floor_key = key
        widget._floor_value = _floor_width(key[0], widget.fontMetrics(), natural)
    return widget._floor_value


def _elided(text: str, metrics, room: int) -> str:
    """Le nom coupe au milieu ; a droite si le milieu ne laisse que « … »."""
    shown = metrics.elidedText(text, Qt.ElideMiddle, max(0, room))
    if shown == "…" and text != "…":
        shown = metrics.elidedText(text, Qt.ElideRight, max(0, room))
    return shown


class _Crumb(QPushButton):
    """Un segment du fil d'Ariane : son nom entier quand la place le permet,
    rogne au milieu sinon — le debut et la fin d'un nom sont ce qui le
    distingue. Le texte du bouton reste entier ; seul le dessin l'abrege.
    """

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("crumb")
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(text)

    def natural(self) -> int:
        """Largeur voulue pour le nom entier, marges comprises."""
        self.ensurePolished()
        return QPushButton.sizeHint(self).width()

    def floor(self) -> int:
        """La plus petite largeur qui dise encore quelque chose (voir
        `_floor_width`)."""
        return _cached_floor(self)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        room = self.style().subElementRect(QStyle.SE_PushButtonContents,
                                           option, self).width()
        option.text = _elided(self.text(), self.fontMetrics(), room)
        painter = QStylePainter(self)
        painter.drawControl(QStyle.CE_PushButton, option)


class _Leaf(QLabel):
    """Le dernier segment quand il n'est pas un dossier : un mot-cle, une
    video. Il s'abrege comme les autres, au lieu d'etre coupe net a
    soixante-dix lettres puis rogne au pixel sans rien en dire."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("crumb")
        self.setToolTip(text)
        self.setContentsMargins(6, 0, 4, 0)

    def natural(self) -> int:
        self.ensurePolished()
        return QLabel.sizeHint(self).width()

    def floor(self) -> int:
        return _cached_floor(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        room = self.contentsRect()
        painter.drawText(room, Qt.AlignLeft | Qt.AlignVCenter,
                         _elided(self.text(), self.fontMetrics(), room.width()))
        painter.end()


class Breadcrumb(QWidget):
    """Chemin cliquable depuis la racine ouverte jusqu'au dossier courant.

    Quand la place manque, les segments s'abregent, les intermediaires
    d'abord et les plus longs en premier ; le dernier — ce qu'on regarde —
    ne cede qu'en dernier. Sur la fiche d'une video, ou tout tient sur une
    ligne, les boutons comprimes perdaient les deux bouts de leur nom :
    « root › ssie › clip. », et l'on ne lisait ni la video ni son dossier.
    """

    jumped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(2)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._top: Path | None = None
        # La fiche d'une video : le dossier passe avant le nom, qui se lit
        # aussi dans le bandeau survole. C'est le dossier qu'on cherche.
        self.parent_first = False

    # -- mesure ------------------------------------------------------------
    def _pieces(self) -> list:
        return [self.layout_.itemAt(i).widget() for i in range(self.layout_.count())
                if self.layout_.itemAt(i).widget() is not None]

    def sizeHint(self):
        """La largeur du chemin entier, quelle que soit l'abreviation en cours :
        sans quoi abreger reduirait la place demandee, qui ferait abreger
        davantage."""
        hint = super().sizeHint()
        pieces = self._pieces()
        if not pieces:
            return hint
        width = sum(p.natural() if isinstance(p, (_Crumb, _Leaf)) else p.sizeHint().width()
                    for p in pieces)
        return QSize(width + self.layout_.spacing() * len(pieces), hint.height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self) -> None:
        """Donne a chaque segment sa largeur : entiere si possible, sinon
        abregee, a commencer par les plus longs des intermediaires."""
        pieces = self._pieces()
        names = [p for p in pieces if isinstance(p, (_Crumb, _Leaf))]
        if not names:
            return
        fixed = sum(p.sizeHint().width() for p in pieces if p not in names)
        room = self.width() - fixed - self.layout_.spacing() * len(pieces)
        wants = [name.natural() for name in names]
        floors = [name.floor() for name in names]
        widths = list(wants)
        if self.parent_first and len(names) == 2 and sum(wants) > room:
            parent = max(floors[0], min(wants[0], room - floors[1]))
            widths = [parent, max(floors[1], room - parent)]
        elif sum(wants) > room:
            head = _shrink(wants[:-1], floors[:-1], room - wants[-1])
            if head is not None:
                widths = head + [wants[-1]]
            else:
                widths = floors[:-1] + [max(floors[-1], room - sum(floors[:-1]))]
        for name, width in zip(names, widths):
            if name.minimumWidth() != width or name.maximumWidth() != width:
                name.setFixedWidth(width)

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
            label = _Leaf(text, self)
            label.setProperty("last", "true")
            self.layout_.addWidget(label)
            self.layout_.addItem(item)
            self.updateGeometry()
            self._fit()

    def set_path(self, top, current, parent_first: bool = False) -> None:
        """Affiche la chaîne de `top` à `current`, chaque segment cliquable.
        `parent_first` : le dossier garde sa largeur avant le nom qui suit."""
        self.parent_first = parent_first
        while self.layout_.count():
            child = self.layout_.takeAt(0)
            if child.widget():
                # Cachee tout de suite : detruite plus tard seulement, elle
                # restait un instant affichee sous le nouveau chemin.
                child.widget().hide()
                child.widget().deleteLater()
        if current is None:
            return

        from . import roots
        self._top = Path(top) if top else Path(current)
        chain = []
        walk = Path(current)
        union = roots.is_union(self._top)
        # Sous « Toutes les racines », la chaine remonte jusqu'a la racine qui
        # porte le dossier, puis a la reunion elle-meme.
        ceiling = roots.owner(walk) if union and not roots.is_union(walk) else None
        while not roots.is_union(walk):
            chain.append(walk)
            if (walk == self._top or walk == ceiling or walk.parent == walk
                    or len(chain) > 16):
                break
            walk = walk.parent
        if union:
            chain.append(Path(roots.UNION))
        chain.reverse()

        for position, path in enumerate(chain):
            if position:
                separator = QLabel("›", self)
                separator.setObjectName("crumbSep")
                self.layout_.addWidget(separator)
            named = roots.label(path) if (roots.is_union(path) or (
                union and path == ceiling)) else (path.name or str(path))
            button = _Crumb(named, self)
            button.setToolTip(str(path))
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("last", "true" if position == len(chain) - 1 else "false")
            button.clicked.connect(lambda _c=False, p=str(path): self.jumped.emit(p))
            self.layout_.addWidget(button)
        self.layout_.addStretch(1)
        self.updateGeometry()
        self._fit()


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
    # Les notes de 1 a 5 ont laisse la place a l'etoile des favoris et a leur
    # onglet : plus rien ne choisit une note ici. Le signal reste declare
    # parce que la fenetre s'y abonne encore ; il n'est plus jamais emis.
    starsChanged = Signal(int)

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
        self.row = row

        self.include = _Field("chercher…", 184)
        # Conserve sans etre montre : le filtre d'exclusion garde sa place dans
        # les criteres et dans la configuration, il n'occupe plus l'ecran.
        self.exclude = _Field("", 0)
        self.exclude.hide()
        row.addWidget(self.include)

        self.sorts = SortChips(self)
        self.sorts.chosen.connect(self.sortChanged)
        row.addWidget(self.sorts)

        # Conserve pour la fenetre, qui le relit encore : -1, aucune note
        # imposee. Le bouton « ★ Note », son menu et ses sept choix, toujours
        # caches mais repolis a chaque appel, sont partis.
        self.stars_pick = -1

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
        # Quatre-vingt-quatre pixels : a soixante-huit, une fois les marges du
        # champ otees, il en restait quarante-six pour un texte qui en
        # demande cinquante et un, et l'on lisait « ≥ vid… ».
        self.folder_min = QLineEdit(self)
        self.folder_min.setObjectName("bound")
        self.folder_min.setPlaceholderText("≥ vidéos")
        self.folder_min.setFixedWidth(84)
        self.folder_min.setValidator(QIntValidator(0, 999999, self))
        self.folder_max = QLineEdit(self)
        self.folder_max.setObjectName("bound")
        self.folder_max.setPlaceholderText("≤ vidéos")
        self.folder_max.setFixedWidth(84)
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
        # Apres la pagination, et non avant la densite : il apparait des
        # qu'on tape une recherche, et poussait alors le « − n + » de
        # soixante-sept pixels, sous la souris.
        row.addWidget(self.clear)
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
        """Ce que les reglages affiches demandent, et rien d'autre.

        Duree, resolution et note y figuraient encore en constantes, sans
        aucun champ derriere : le filtre les relisait pour chacun des cent
        mille elements, pour ne jamais rien ecarter. La fenetre les lit avec
        une valeur par defaut ; leur absence ne change aucun resultat.
        """
        return {
            "include": self.include.text(),
            "exclude": self.exclude.text(),
            "orientations": self.orientations(),
            "folder_min": int(self.folder_min.text() or 0),
            "folder_max": int(self.folder_max.text() or 0),
        }

    def set_terms(self, include: str, exclude: str) -> None:
        for field, value in ((self.include, include), (self.exclude, exclude)):
            field.blockSignals(True)
            field.setText(value)
            field.blockSignals(False)

    def set_sort(self, mode: str) -> None:
        self.sorts.set_value(mode)

    def set_stars(self, pick: int) -> None:
        """Conserve pour les appels de la fenetre : retient la valeur, sans
        rien afficher ni filtrer — il n'y a plus de note a choisir."""
        self.stars_pick = int(pick)

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

    def set_photo(self, on: bool) -> None:
        """Des photos : ni duree, ni taille a trier -- la resolution suffit."""
        for key in ("duration", "size"):
            self.sorts.buttons[key].setVisible(not on)
        noun = "photos" if on else "vidéos"
        self.folder_min.setPlaceholderText(f"≥ {noun}")
        self.folder_max.setPlaceholderText(f"≤ {noun}")
        for field in (self.folder_min, self.folder_max):
            field.setToolTip("Ne garder que les dossiers qui comptent au moins, "
                             f"au plus, ce nombre de {noun}")
        self.random_here.setToolTip(f"Une {noun[:-1]} au hasard, parmi celles affichées")

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
    ("Réglages", "gauge"), ("Confidentialité", "eye-off"), ("Avancé", "folder-cog"),
    ("abîmées", "film"), ("Ultra tri", "zap"),
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
