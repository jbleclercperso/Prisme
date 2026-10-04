"""Les petites fenetres-outils posees sur la fenetre principale (coche et
etoile des vignettes, trait d'avancement, bandeau du lecteur, instants du
plein ecran, messages) passent devant le lecteur natif -- et, sans garde,
devant toute fenetre de Prisme ouverte par-dessus : le Labo IA voyait
traverser les coches des vignettes et les barres d'avancement qu'il
cachait, au moindre survol.

`FloatGuard`, en tete des bases d'une telle fenetre, la retient de paraitre
(ou de remonter) la ou une fenetre de Prisme la recouvre.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget

# Ce qui ne cache pas une fenetre-outil : les autres fenetres-outils, les
# bulles d'aide, l'ecran de demarrage.
_SKIP = {Qt.Tool, Qt.ToolTip, Qt.SplashScreen, Qt.Desktop, Qt.Widget}


def covered(widget) -> bool:
    """Une fenetre de Prisme recouvre-t-elle l'endroit ou paraitrait `widget` ?"""
    owner = widget.parentWidget()
    host = owner.window() if owner is not None else None
    if host is None or host is widget:
        return False
    if host.windowFlags() & Qt.WindowStaysOnTopHint:
        # Le lecteur flottant est devant tout : ses fenetres aussi.
        return False
    area = widget.frameGeometry()
    if area.isEmpty():
        return False
    for other in QApplication.topLevelWidgets():
        if other is host or other is widget or not other.isVisible():
            continue
        if other.isMinimized():
            continue
        kind = other.windowFlags() & Qt.WindowType_Mask
        if kind in _SKIP:
            continue
        if kind != Qt.Popup:
            # Une fenetre de Prisme n'est devant la principale que si elle
            # lui appartient (le Labo, un dialogue) ; une fenetre libre peut
            # etre derriere.
            parent = other.parentWidget()
            if parent is None or parent.window() is not host:
                continue
        if other.frameGeometry().intersects(area):
            return True
    return False


class FloatGuard:
    """A mettre en tete des bases : `class Bandeau(FloatGuard, QWidget)`."""

    def setVisible(self, visible: bool) -> None:
        if visible and self.isWindow() and covered(self):
            visible = False
        super().setVisible(visible)

    def raise_(self) -> None:
        if self.isWindow() and covered(self):
            return
        super().raise_()


class GuardedWidget(FloatGuard, QWidget):
    pass


class GuardedLabel(FloatGuard, QLabel):
    pass
