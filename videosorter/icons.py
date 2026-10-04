"""Les icones de Prisme : un seul jeu, dessine au trait, net a toute taille.

Les signes typographiques (◂ ⛶ ⚄ ⌸) dependaient de la police de Windows : a
treize points, ils n'etaient que des poussieres dans leur bouton, et chacun
avait sa graisse. Les icones Lucide (licence ISC, voir `icons_data`) sont
rendues ici a la taille voulue, dans la couleur voulue.
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .icons_data import SVG

INK = "#dbe2ea"
DIM = "#5c6573"
ACCENT = "#ffffff"

# Les signes d'hier et l'icone qui les remplace : les appels existants
# continuent de passer un signe, il suffit de le traduire.
GLYPHS = {
    "◂": "chevron-left", "▸": "chevron-right", "⛶": "maximize",
    "✕": "x", "⚄": "dices", "⤢": "external-link", "⌸": "folder-open",
    "−": "minus", "+": "plus", "⋯": "ellipsis", "⏯": "pause",
    "⧉": "picture-in-picture-2", "↩": "undo-2", "⏪": "rewind", "⏩": "fast-forward",
    # Un signe = un geste : ⛶ ne veut plus dire que « plein ecran ».
    "⊙": "focus",              # cette video seule, sur tout le mur
    "⏏": "app-window",         # revenir dans Prisme (le lecteur flottant)
    "⤮": "shuffle",            # une autre, au hasard (le mur)
}


def _pixmap(name: str, color: str, px: int, stroke: float,
            fill: str = "") -> QPixmap:
    svg = SVG[name].replace("currentColor", color)
    if fill:
        svg = svg.replace('fill="none"', f'fill="{fill}"', 1)
    if stroke != 2.0:
        svg = svg.replace('stroke-width="2"', f'stroke-width="{stroke:g}"')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    return pixmap


@lru_cache(maxsize=256)
def icon(name: str, color: str = INK, stroke: float = 2.0) -> QIcon:
    """L'icone `name`, nette jusqu'a 64 points, grisee quand le bouton l'est."""
    name = GLYPHS.get(name, name)
    result = QIcon()
    for px in (24, 48, 64):
        result.addPixmap(_pixmap(name, color, px, stroke), QIcon.Normal)
        result.addPixmap(_pixmap(name, DIM, px, stroke), QIcon.Disabled)
        result.addPixmap(_pixmap(name, ACCENT, px, stroke), QIcon.Active)
    return result


@lru_cache(maxsize=16)
def lettered(name: str, color: str, letter: str) -> QIcon:
    """L'icone avec une lettre posee en haut a gauche : « ⧉ A », le lecteur
    flottant **automatique**, qu'on ne confond plus avec « ouvrir le
    flottant maintenant »."""
    result = QIcon()
    for px in (24, 48, 64):
        pixmap = _pixmap(name, color, px, 2.0)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(7, int(px * 0.42)))
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(QRectF(px * 0.13, px * 0.17, px * 0.42, px * 0.42),
                         Qt.AlignCenter, letter)
        painter.end()
        result.addPixmap(pixmap, QIcon.Normal)
    return result


@lru_cache(maxsize=16)
def filled(name: str, color: str, line: str = "") -> QIcon:
    """L'icone remplie de sa couleur : l'etoile doree d'un favori. `line` :
    ses traits d'une autre couleur, pour qu'un dessin interieur (le cadenas
    du dossier) reste lisible sur le plein."""
    result = QIcon()
    for px in (24, 48, 64):
        result.addPixmap(_pixmap(name, line or color, px, 2.0, color), QIcon.Normal)
    return result


def dress(button, name: str, size: int = 20, text: str | None = None,
          color: str = INK) -> None:
    """Pose l'icone sur un bouton, en grand dans sa boite, avec ou sans texte."""
    button.setIcon(icon(name, color))
    button.setIconSize(QSize(size, size))
    button.setText(text or "")
