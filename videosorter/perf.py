"""Le chien de garde : mesure les gels de l'interface, et dit ce qui les precede.

« C'est lent » ne se corrige pas ; « l'interface s'est figee 4,2 s juste
apres wall.play » se corrige. Un battement toutes les 250 ms : s'il arrive
en retard de plus de 800 ms, c'est que le fil d'interface etait occupe — on
note combien de temps, et la derniere action marquee avant.
"""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import QObject, QTimer

from .config import APP_DIR

LOG = APP_DIR / "gel.log"
STALL = 0.8       # secondes de retard a partir desquelles on parle de gel


class Watchdog(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_action = "démarrage"
        self.last_at = time.monotonic()
        self._beat = time.monotonic()
        self.stalls = 0
        self.worst = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._beat = time.monotonic()
        self.timer.start()

    def mark(self, action: str) -> None:
        """A appeler avant tout ce qui pourrait couter : on saura quoi accuser."""
        self.last_action = action
        self.last_at = time.monotonic()

    def _tick(self) -> None:
        now = time.monotonic()
        late = now - self._beat - 0.25
        self._beat = now
        if late < STALL:
            return
        self.stalls += 1
        self.worst = max(self.worst, late)
        since = now - self.last_at
        line = (f"{datetime.now():%d/%m %H:%M:%S}  interface figée {late:.1f} s"
                f"  — dernière action : {self.last_action}"
                f" (il y a {since:.1f} s)\n")
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as out:
                out.write(line)
        except OSError:
            pass


WATCH = Watchdog()


def mark(action: str) -> None:
    WATCH.mark(action)
