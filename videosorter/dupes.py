"""Recherche de doublons, sans relire un octet des vidéos.

Deux fichiers vidéo de taille rigoureusement identique, à l'octet près, sont
presque toujours le même fichier : les formats compressés ne produisent pas deux
fois la même longueur par hasard. La taille est donc le premier tri, et elle ne
coûte rien — l'énumération d'un répertoire la rapporte déjà.

Quand la durée des deux est connue, elle sert de confirmation. Quand elle ne
l'est pas, on ne sonde rien : mieux vaut proposer un groupe à regarder que faire
attendre des minutes pour une certitude dont l'œil se charge en une seconde.

Rien n'est supprimé ici. Ce module rassemble et propose ; la décision revient à
qui regarde, et passe par la corbeille de session comme toute suppression.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .config import VIDEO_EXTS
from .scan import _is_hidden

# En deça, deux fichiers de même taille ne prouvent rien : les vidéos minuscules
# — vignettes animées, fragments — se ressemblent trop.
MIN_SIZE = 1024 * 1024


def walk_sized(root: Path, skip_hidden: bool = True):
    """Rend (chemin, taille) pour chaque vidéo, sans lecture supplémentaire."""
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not (skip_hidden and _is_hidden(entry)):
                        stack.append(entry.path)
                    continue
                if skip_hidden and _is_hidden(entry):
                    continue
                dot = entry.name.rfind(".")
                if dot <= 0 or entry.name[dot:].lower() not in VIDEO_EXTS:
                    continue
                yield Path(entry.path), entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue


def group_by_size(pairs, minimum: int = MIN_SIZE) -> list:
    """Groupes d'au moins deux fichiers partageant exactement une taille.

    Les groupes sortent du plus lourd au plus léger : c'est dans cet ordre qu'on
    veut les traiter, puisque c'est là que se trouve la place à récupérer.
    """
    by_size: dict = {}
    for path, size in pairs:
        if size < minimum:
            continue
        by_size.setdefault(size, []).append(path)
    groups = [(size, sorted(paths, key=lambda p: str(p).lower()))
              for size, paths in by_size.items() if len(paths) > 1]
    groups.sort(key=lambda pair: -pair[0])
    return groups


class DuplicateScan(QThread):
    """Parcourt la collection et rassemble les fichiers de taille identique."""

    progress = Signal(int)                # videos examinees
    found = Signal(list)                  # [(taille, [chemins])]

    def __init__(self, root: Path, skip_hidden: bool = True, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.skip_hidden = skip_hidden
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        pairs = []
        last = 0.0
        for path, size in walk_sized(self.root, self.skip_hidden):
            if self._stop:
                break
            pairs.append((path, size))
            now = time.monotonic()
            if now - last >= 0.3:
                self.progress.emit(len(pairs))
                last = now
        self.progress.emit(len(pairs))
        self.found.emit([] if self._stop else group_by_size(pairs))
