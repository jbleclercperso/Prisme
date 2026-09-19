"""Fabrication des vignettes d'avance, pour que l'affichage n'attende plus.

Lire une vignette deja faite sur le disque local coute environ une milliseconde ;
la tirer d'une video posee sur un partage reseau en coute deux a cinq cents. Cet
ecart ne se comble pas : il est dans la nature des deux operations. La seule
facon d'afficher une planche instantanement est donc de n'y mettre que des
vignettes deja fabriquees.

D'ou ce parcours : une fois, en tache de fond, on passe sur toutes les videos et
l'on fabrique l'image que la planche demandera. Il dure ce qu'il dure — c'est du
reseau — mais on ne le paie qu'une fois, et l'on peut trier pendant.

Il s'interrompt a tout moment et reprend ou il en etait : une vignette deja
presente n'est jamais refaite, donc relancer le parcours ne recommence rien.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .media import build_preview_plan, extract_thumb, thumb_path
from .scan import list_all_videos

# Quatre extractions de front, quand l'affichage en utilise huit. Ce parcours
# n'est pas presse : ce qu'on regarde doit passer devant lui.
WORKERS = 4


class ThumbBackfill(QThread):
    """Parcourt la collection et fabrique la vignette manquante de chaque video."""

    progress = Signal(int, int, int)      # faites, deja presentes, total
    counted = Signal(int)                 # total, une fois le recensement fini
    done = Signal(int, int, bool)         # fabriquees, deja presentes, termine

    MADE, KEPT, FAILED = "made", "kept", "failed"

    def __init__(self, root: Path, width: int, skip_hidden: bool = True,
                 parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.width = width
        self.skip_hidden = skip_hidden
        self._stop = False
        self.made = 0
        self.kept = 0
        self.failed = 0
        self.total = 0

    def stop(self) -> None:
        self._stop = True

    # -- le travail d'une video -----------------------------------------
    def _one(self, video) -> str:
        """Dit ce qu'il est advenu de cette video : faite, deja la, ou ratee.

        Une extraction qui echoue ne doit surtout pas se compter comme une
        vignette faite : le parcours annoncerait un travail accompli qui ne
        l'est pas, et la planche attendrait toujours.
        """
        plan = build_preview_plan([video], 1, 0, True, True)
        if not plan:
            return self.FAILED
        path = Path(plan[0][0])
        ts = plan[0][1]
        try:
            target = thumb_path(path, ts, self.width)
            if target.exists() and target.stat().st_size > 0:
                return self.KEPT
        except OSError:
            pass
        return self.MADE if extract_thumb(path, ts, self.width) else self.FAILED

    def run(self) -> None:
        # La limite par defaut vise l'affichage d'une liste ; ici on les veut
        # toutes, c'est tout l'objet du parcours.
        videos = list_all_videos(self.root, self.skip_hidden, limit=5_000_000)
        if self._stop:
            return self.done.emit(0, 0, False)
        self.total = len(videos)
        self.counted.emit(self.total)

        last = 0.0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for outcome in pool.map(self._guarded, videos):
                if outcome is None:
                    continue
                if outcome == self.MADE:
                    self.made += 1
                elif outcome == self.KEPT:
                    self.kept += 1
                else:
                    self.failed += 1
                # Une annonce par video repeindrait l'interface cent mille fois.
                now = time.monotonic()
                if now - last >= 0.25:
                    self.progress.emit(self.made, self.kept + self.failed,
                                       self.total)
                    last = now
        self.progress.emit(self.made, self.kept, self.total)
        self.done.emit(self.made, self.kept, not self._stop)

    def _guarded(self, video):
        if self._stop:
            return None
        try:
            return self._one(video)
        except Exception:
            # Un fichier illisible ne doit pas arreter les cent mille autres.
            return self.FAILED
