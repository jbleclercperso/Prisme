"""Favoris, conservés d'une session à l'autre.

Les favoris vivent à côté de l'application et non dans les dossiers triés :
rien n'est écrit sur le NAS. Déplacer un dossier ne perd ni son étoile ni
celles des vidéos qu'il contient : `rename` fait suivre tout ce qui est dessous.

Le fichier est précieux -- des centaines d'étoiles posées à la main -- et il
était traité comme jetable : illisible, il repartait de zéro en silence, et
la première étoile suivante écrasait tout. Il est désormais écrit pour de bon
(vidage forcé), copié une fois par lancement, et un fichier abîmé est mis de
côté au lieu d'être remplacé.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal

from . import config as _config
from .config import (
    RATINGS_PATH, _copy_quietly, _parse_object, _read_text, _set_aside,
    _write_atomic,
)

MAX_STARS = 5
# Une rafale de rangements ne reecrit le fichier qu'une fois, un instant apres.
FLUSH_DELAY_MS = 1500
# La trace, locale, d'une reprise des favoris qui vivaient sur le partage.
_ADOPTED_MARK = "ratings.partage-repris"


def _clean(raw: dict) -> dict:
    return {
        key: int(value) for key, value in raw.items()
        if isinstance(value, (int, float)) and 0 <= value <= MAX_STARS
    }


class Ratings(QObject):
    """Table {chemin: étoiles}, relue au démarrage et écrite au fil de l'eau."""

    changed = Signal(str, int)

    def __init__(self, path: Path | None = None, parent=None):
        super().__init__(parent)
        # Lu a l'appel, jamais fige a la definition : c'est ce qui permet de
        # deporter le fichier ailleurs -- un bac a sable, un cache portable --
        # sans que rien ne touche a celui de l'utilisateur.
        self.path = Path(path) if path else Path(RATINGS_PATH)
        self.data: dict = {}
        self.dirty = False
        # Vrai quand le fichier existe mais n'a pas pu etre lu : repartir de
        # zero puis ecrire aurait efface toutes les etoiles.
        self.read_only = False
        # Ce qu'il faudrait dire a l'utilisateur, s'il y a lieu.
        self.problem = ""
        self._timer: QTimer | None = None
        self.load()

    @property
    def backup_path(self) -> Path:
        return self.path.with_name(self.path.name + ".bak")

    def load(self) -> None:
        text = _read_text(self.path)
        if text is None:
            self._adopt_shared()
            return
        if isinstance(text, OSError):
            self.read_only = True
            self.problem = (f"Favoris illisibles pour l'instant ({text}) : "
                            "ils ne seront pas réécrits pendant cette séance.")
            return
        raw = _parse_object(text)
        if raw is None:
            aside = _set_aside(self.path)
            backup = _read_text(self.backup_path)
            raw = _parse_object(backup) if isinstance(backup, str) else None
            where = f" (mis de côté sous « {aside.name} »)" if aside else ""
            self.problem = ("Le fichier des favoris était abîmé" + where + (
                " : la copie de secours a été reprise." if raw is not None
                else " : les favoris repartent de zéro."))
            if aside is None:
                self.read_only = True
            self.data = _clean(raw or {})
            self.dirty = raw is not None and not self.read_only
            self._adopt_shared()
            return
        self.data = _clean(raw)
        # Une copie par lancement : c'est d'elle qu'on repartira si le
        # fichier s'abimait. Jamais d'une table vide, qui n'apprend rien.
        if self.data:
            _copy_quietly(self.path, self.backup_path)
        self._adopt_shared()

    def _adopt_shared(self) -> None:
        """Reprend, une fois, les favoris laisses sur le partage par l'ancienne version.

        Fusion et non remplacement : ce qui a ete marque ici depuis l'emporte.
        La trace est locale ; le fichier du partage reste tel quel, pour
        l'autre machine qui voudrait le reprendre a son tour.
        """
        shared = _config.SHARED_RATINGS_PATH
        if shared is None or Path(shared) == self.path or self.read_only:
            return
        mark = self.path.with_name(_ADOPTED_MARK)
        if mark.exists():
            return
        text = _read_text(Path(shared))
        if isinstance(text, OSError):
            return                      # partage endormi : ce sera pour la prochaine fois
        raw = _parse_object(text) if text else None
        if raw:
            merged = _clean(raw)
            merged.update(self.data)
            if merged != self.data:
                self.data = merged
                self.dirty = True
                self.flush()
        if not self.dirty:
            try:
                mark.write_text(str(shared), encoding="utf-8")
            except OSError:
                pass

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
        self.flush_soon()
        return stars

    def rename(self, old, new) -> int:
        """Suit un élément qui change de place, pour ne pas perdre ses étoiles.

        Un dossier emporte celles de tout ce qu'il contient : seule sa propre
        clé suivait, et ses vidéos perdaient leur étoile au premier rangement.
        Rend le nombre d'étoiles déplacées.
        """
        old_s, new_s = str(old), str(new)
        if not old_s or old_s == new_s:
            return 0
        prefixes = (old_s + "\\", old_s + "/")
        names = [key for key in self.data
                 if key == old_s or key.startswith(prefixes)]
        for key in names:
            stars = self.data.pop(key)
            self.data[new_s + key[len(old_s):]] = stars
        if names:
            self.dirty = True
            self.flush_soon()
        return len(names)

    # Meme geste, nom qui dit ce qu'il fait : utilise pour la restauration.
    relocate = rename

    def forget_under(self, path) -> int:
        """Oublie les étoiles de ce chemin et de tout ce qu'il contient : il est détruit."""
        root = str(path)
        prefixes = (root + "\\", root + "/")
        names = [key for key in self.data
                 if key == root or key.startswith(prefixes)]
        for key in names:
            self.data.pop(key, None)
        if names:
            self.dirty = True
            self.flush_soon()
        return len(names)

    def flush_soon(self) -> None:
        """Ecrit un instant plus tard, une fois pour toute une rafale.

        Un renommage ne marquait le fichier que « a ecrire » : rien n'etait
        enregistre avant la prochaine etoile ou la fermeture, et un arret net
        laissait les anciens chemins.
        """
        app = QCoreApplication.instance()
        if app is None or QThread.currentThread() is not self.thread():
            self.flush()
            return
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(FLUSH_DELAY_MS)
            self._timer.timeout.connect(self.flush)
        self._timer.start()

    def flush(self) -> None:
        if not self.dirty or self.read_only:
            return
        if self._timer is not None:
            self._timer.stop()
        problem = _write_atomic(
            self.path, json.dumps(self.data, separators=(",", ":")))
        if problem:
            self.problem = f"Favoris non enregistrés : {problem}"
        else:
            self.dirty = False

    @property
    def count(self) -> int:
        return len(self.data)
