"""Notes de 0 à 5 étoiles, conservées d'une session à l'autre.

Les notes vivent à côté de l'application et non dans les dossiers triés : rien
n'est écrit sur le NAS, et déplacer un dossier ne perd pas sa note puisqu'on
réattribue l'entrée au nouveau chemin lors du déplacement.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .config import APP_DIR, RATINGS_PATH

MAX_STARS = 5


class Ratings(QObject):
    """Table {chemin: étoiles}, relue au démarrage et écrite au fil de l'eau."""

    changed = Signal(str, int)

    def __init__(self, path: Path | None = None, parent=None):
        super().__init__(parent)
        # Lu a l'appel, jamais fige a la definition : c'est ce qui permet de
        # deporter le fichier ailleurs — un bac a sable, un cache portable —
        # sans que rien ne touche a celui de l'utilisateur.
        self.path = Path(path) if path else RATINGS_PATH
        self.data: dict = {}
        self.dirty = False
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self.data = {
                key: int(value) for key, value in raw.items()
                if isinstance(value, (int, float)) and 0 <= value <= MAX_STARS
            }

    def get(self, path) -> int:
        return self.data.get(str(path), 0)

    def set(self, path, stars: int) -> int:
        """Attribue une note. Rappuyer sur la même valeur l'efface."""
        key = str(path)
        stars = max(0, min(MAX_STARS, int(stars)))
        if stars == 0 or self.data.get(key) == stars:
            self.data.pop(key, None)
            stars = 0
        else:
            self.data[key] = stars
        self.dirty = True
        self.changed.emit(key, stars)
        return stars

    def rename(self, old, new) -> None:
        """Suit un élément qui change de place, pour ne pas perdre sa note."""
        stars = self.data.pop(str(old), None)
        if stars is not None:
            self.data[str(new)] = stars
            self.dirty = True

    def flush(self) -> None:
        if not self.dirty:
            return
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.path)
            self.dirty = False
        except OSError:
            pass

    @property
    def count(self) -> int:
        return len(self.data)
