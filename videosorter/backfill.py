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

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QThread, Signal

import json
import os

from .config import APP_DIR, VIDEO_EXTS
from .media import build_preview_plan, extract_thumb, thumb_path
from .scan import walk_videos

# Quatre extractions de front, quand l'affichage en utilise huit. Ce parcours
# n'est pas presse : ce qu'on regarde doit passer devant lui.
WORKERS = 4


class ThumbBackfill(QThread):
    """Parcourt la collection et fabrique la vignette manquante de chaque video."""

    progress = Signal(int, int, int)      # faites, deja presentes, total
    counting = Signal(int)                # videos recensees jusqu ici
    counted = Signal(int)                 # total, une fois le recensement fini
    done = Signal(int, int, bool)         # fabriquees, deja presentes, termine

    MADE, KEPT, FAILED = "made", "kept", "failed"

    # Un compte rendu ecrit a cote du cache : quand l interface laisse un
    # doute, ce fichier tranche. On peut l ouvrir pendant que le parcours
    # tourne et voir les lignes s ajouter.
    LOG = APP_DIR / "preparation.log"

    def __init__(self, root: Path, width: int, skip_hidden: bool = True,
                 parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.width = width
        self.skip_hidden = skip_hidden
        self._stop = False
        self._go = threading.Event()
        self._go.set()
        self.made = 0
        self.kept = 0
        self.failed = 0
        self.total = 0

    def stop(self) -> None:
        self._stop = True
        self._go.set()

    def pause(self) -> None:
        """Suspend les extractions : le mur, lui, a besoin de la ligne."""
        self._go.clear()

    def resume(self) -> None:
        self._go.set()

    @property
    def paused(self) -> bool:
        return not self._go.is_set()

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

    def _log(self, line: str) -> None:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            with self.LOG.open("a", encoding="utf-8") as out:
                out.write(f"{time.strftime('%d/%m %H:%M:%S')}  {line}\n")
        except OSError:
            pass

    def run(self) -> None:
        # Le recensement seul prend plusieurs minutes sur un partage reseau. Sans
        # nouvelle pendant ce temps, on croit que rien ne se passe : il annonce
        # donc ce qu'il trouve au fur et a mesure.
        self.counting.emit(0)
        videos = self._collect()
        if self._stop:
            return self.done.emit(0, 0, False)
        self.total = len(videos)
        self.counted.emit(self.total)
        self._log(f"debut — {self.total} video(s) recensee(s) sous {self.root}")

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
                seen = self.made + self.kept + self.failed
                if seen % 500 == 0:
                    self._log(f"{seen}/{self.total} — {self.made} fabriquee(s), "
                              f"{self.kept} deja la, {self.failed} ratee(s)")
                now = time.monotonic()
                if now - last >= 0.25:
                    self.progress.emit(self.made, self.kept + self.failed,
                                       self.total)
                    last = now
        self.progress.emit(self.made, self.kept, self.total)
        self._log(f"fin — {self.made} fabriquee(s), {self.kept} deja la, "
                  f"{self.failed} ratee(s)"
                  + ("" if not self._stop else " (interrompu)"))
        self.done.emit(self.made, self.kept, not self._stop)

    def _collect(self) -> list:
        """Recense les videos en disant ou il en est."""
        found: list = []
        last = 0.0
        for video in walk_videos(self.root, self.skip_hidden):
            if self._stop:
                break
            found.append(video)
            now = time.monotonic()
            if now - last >= 0.4:
                self.counting.emit(len(found))
                last = now
        return found

    def _guarded(self, video):
        self._go.wait()
        if self._stop:
            return None
        try:
            return self._one(video)
        except Exception:
            # Un fichier illisible ne doit pas arreter les cent mille autres.
            return self.FAILED

class VideoCount(QThread):
    """Compte les videos d une racine, sans rien fabriquer.

    Savoir combien il y en a est la premiere question qu on se pose devant une
    collection, et la seule facon de verifier qu une preparation a bien tout
    vu. Elle ne meritait pas une ligne de commande.
    """

    progress = Signal(int)
    counted = Signal(int)

    def __init__(self, root: Path, skip_hidden: bool = True, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.skip_hidden = skip_hidden
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        total = 0
        last = 0.0
        for _video in walk_videos(self.root, self.skip_hidden):
            if self._stop:
                break
            total += 1
            now = time.monotonic()
            if now - last >= 0.3:
                self.progress.emit(total)
                last = now
        self.counted.emit(total)

class ThumbAudit(QThread):
    """Compte les videos qui ont deja leur vignette, et celles qui ne l ont pas.

    C est la seule reponse qui tranche : savoir si la preparation « tourne » ne
    dit rien, alors que compter les fichiers reellement presents sur le disque
    ne laisse pas de place au doute. On ne fabrique rien ici, on regarde.

    Les dates et tailles sont relevees pendant l enumeration, qui les rapporte
    gratuitement : l audit ne redemande donc rien au reseau.
    """

    progress = Signal(int, int)          # vues, avec vignette
    done = Signal(int, int)              # total, avec vignette

    def __init__(self, root: Path, width: int, skip_hidden: bool = True,
                 parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.width = width
        self.skip_hidden = skip_hidden
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from .stamps import remember
        seen = 0
        ready = 0
        last = 0.0
        stack = [str(self.root)]
        while stack and not self._stop:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                if self._stop:
                    break
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    dot = entry.name.rfind(".")
                    if dot <= 0 or entry.name[dot:].lower() not in VIDEO_EXTS:
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    remember(entry.path, stat.st_size, stat.st_mtime)
                except OSError:
                    continue
                seen += 1
                video = Path(entry.path)
                try:
                    plan = build_preview_plan([video], 1, 0, True, True)
                    target = thumb_path(video, plan[0][1], self.width)
                    if target.exists() and target.stat().st_size > 0:
                        ready += 1
                except OSError:
                    pass
                now = time.monotonic()
                if now - last >= 0.3:
                    self.progress.emit(seen, ready)
                    last = now
        self.done.emit(seen, ready)


class TitleScan(QThread):
    """Lit le titre des metadonnees de chaque video qui n'a pas encore ete sondee.

    Des heures s'il le faut : un ffprobe par video, huit de front, et rien
    n'est redemande a la relance — ce qui a ete lu est dans l'index. Les mots
    frequents en tiennent compte des la fin.
    """

    WORKERS = 8
    progress = Signal(int, int)      # faits, total
    done = Signal(int, int)          # sondes, avec un titre

    def __init__(self, root: Path, skip_hidden: bool = True, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.skip_hidden = skip_hidden
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _one(self, video) -> int:
        """1 si un titre a ete trouve, 0 sinon, -1 si l'on n'a rien pu lire."""
        from .index import INDEX
        from .media import PROBE_LIMITS, Tools, _run, title_from
        from .stamps import stamp_of
        if self._stop or not Tools.ffprobe:
            return -1
        code, out = _run([Tools.ffprobe, "-v", "error"] + PROBE_LIMITS + [
            "-print_format", "json", "-show_entries", "format_tags", str(video)])
        if code != 0 or not out:
            return -1
        try:
            title = title_from(json.loads(out))
        except ValueError:
            return -1
        INDEX.put_title(video, stamp_of(str(video)) or "", title)
        return 1 if title else 0

    def run(self) -> None:
        from .index import INDEX
        todo = [video for video in walk_videos(self.root, self.skip_hidden)
                if not INDEX.has_title(video)]
        if self._stop:
            return self.done.emit(0, 0)
        total = len(todo)
        found = 0
        seen = 0
        last = 0.0
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            for outcome in pool.map(self._one, todo):
                seen += 1
                if outcome == 1:
                    found += 1
                now = time.monotonic()
                if now - last >= 0.3:
                    self.progress.emit(seen, total)
                    last = now
                if self._stop:
                    break
        self.progress.emit(seen, total)
        self.done.emit(seen, found)


class SceneScan(QThread):
    """Releve les changements de plan des videos qui n'en ont pas encore.

    Quatre de front seulement : chaque relevé traverse le fichier, meme en ne
    lisant que les images cles, et saturer le partage ferait tout ralentir.
    """

    WORKERS = 4
    progress = Signal(int, int)      # faites, total
    done = Signal(int, int)          # sondees, avec des plans

    def __init__(self, root: Path, skip_hidden: bool = True, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.skip_hidden = skip_hidden
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _one(self, video) -> int:
        from .index import INDEX
        from .media import scene_times
        from .stamps import stamp_of
        if self._stop:
            return 0
        times = scene_times(Path(video))
        INDEX.put_scenes(video, stamp_of(str(video)) or "", times)
        return 1 if times else 0

    def run(self) -> None:
        from .index import INDEX
        todo = [video for video in walk_videos(self.root, self.skip_hidden)
                if not INDEX.has_scenes(video)]
        if self._stop:
            return self.done.emit(0, 0)
        total = len(todo)
        seen = found = 0
        last = 0.0
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            for outcome in pool.map(self._one, todo):
                seen += 1
                found += outcome
                now = time.monotonic()
                if now - last >= 0.3:
                    self.progress.emit(seen, total)
                    last = now
                if self._stop:
                    break
        self.progress.emit(seen, total)
        self.done.emit(seen, found)


class InfoScan(QThread):
    """Sonde la resolution d'une poignee de videos, et rien d'autre.

    Le mur en a besoin pour savoir ce qui est debout : sans cela, il ne peut
    ni promettre des verticales, ni se remplir. Quelques dizaines a la fois,
    pour ne jamais retenir l'ecran.
    """

    WORKERS = 6
    done = Signal(int)               # combien ont ete sondees

    def __init__(self, videos: list, parent=None):
        super().__init__(parent)
        self.videos = list(videos)
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _one(self, video) -> int:
        from .media import probe
        if self._stop:
            return 0
        try:
            return 1 if (probe(Path(video)) or {}).get("width") else 0
        except OSError:
            return 0

    def run(self) -> None:
        if not self.videos:
            return self.done.emit(0)
        count = 0
        with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
            for outcome in pool.map(self._one, self.videos):
                count += outcome
                if self._stop:
                    break
        self.done.emit(count)
