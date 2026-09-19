"""Accès à ffmpeg / ffprobe : sondage des vidéos et extraction de vignettes."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import shutil
import subprocess
import sys
from pathlib import Path

import time
from collections import deque

from PySide6.QtCore import (
    QObject, QRunnable, QThread, QThreadPool, Signal,
)

from .config import THUMB_DIR
from .index import INDEX

# Évite une fenêtre console qui clignote à chaque appel ffmpeg sous Windows.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_WINGET_GLOB = "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/*/bin"


def _find_tool(name: str, configured: str = "") -> str:
    """Localise ffmpeg/ffprobe : config explicite, puis PATH, puis winget."""
    if configured and Path(configured).exists():
        return configured
    found = shutil.which(name)
    if found:
        return found
    for candidate in Path.home().glob(_WINGET_GLOB):
        exe = candidate / f"{name}.exe"
        if exe.exists():
            return str(exe)
    return ""


class Tools:
    """Chemins vers les binaires, résolus une fois au démarrage."""

    ffmpeg = ""
    ffprobe = ""

    @classmethod
    def resolve(cls, cfg=None) -> bool:
        conf_ffmpeg = cfg.get("ffmpeg", "") if cfg else ""
        conf_ffprobe = cfg.get("ffprobe", "") if cfg else ""
        cls.ffmpeg = _find_tool("ffmpeg", conf_ffmpeg)
        cls.ffprobe = _find_tool("ffprobe", conf_ffprobe)
        return bool(cls.ffmpeg and cls.ffprobe)


DRIVE_REMOTE = 4


def is_network_path(path) -> bool:
    """Vrai si le chemin vit sur un partage réseau (UNC ou lecteur mappé).

    Sur un NAS, ce n'est pas le processeur qui limite mais la latence : chaque
    lecture attend un aller-retour. Il vaut alors mieux lancer plus d'extractions
    de front, là où en local on saturerait le disque pour rien.
    """
    text = str(path)
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    if sys.platform != "win32":
        return False
    drive = os.path.splitdrive(os.path.abspath(text))[0]
    if not drive:
        return False
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") == DRIVE_REMOTE
    except (OSError, AttributeError, ValueError):
        return False


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=NO_WINDOW, encoding="utf-8", errors="replace",
        )
        return proc.returncode, proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return 1, ""


# ---------------------------------------------------------------------------
# Sondage (durée / résolution / codec), retenu par l'index
# ---------------------------------------------------------------------------

# Borner l'examen de l'en-tete ne tient pas la mesure : trois fois plus rapide
# sur certains fichiers, deux fois plus lent sur d'autres, ou ffmpeg doit relire
# apres avoir echoue dans la borne. On ne le fait donc que pour ffprobe, dont
# c'est justement le travail de lire l'en-tete, et ou deux megaoctets suffisent.
PROBE_LIMITS = ["-probesize", "2M", "-analyzeduration", "2M"]

# Instants d'essai quand on ignore la duree : plutot que de payer un ffprobe
# pour la connaitre, on tente un endroit plausible.
#
# Ils sont volontairement proches du debut. Sur un partage, atteindre la
# soixantieme seconde d'un fichier coute presque deux fois la dixieme — il faut
# faire venir ce qu'on saute — et echoue sur les videos plus courtes, ce qui
# oblige a tout recommencer : 1,94 s et 11 reussites sur 14 a t=60, contre
# 1,05 s et 13 sur 14 a t=10. Une image prise un peu plus tot vaut mieux qu'une
# image qui coute le double et manque une fois sur cinq.
BLIND_OFFSETS = (6.0, 12.0, 20.0, 9.0, 16.0, 25.0)


def _stamp_of(path: Path) -> str:
    """Taille et date : ce qui distingue deux versions d'un meme chemin."""
    try:
        st = path.stat()
        return f"{int(st.st_mtime)}|{st.st_size}"
    except OSError:
        return ""


def probe(path: Path) -> dict:
    """Retourne {duration, width, height, codec, ok} pour une vidéo."""
    stamp = _stamp_of(path)
    cached = INDEX.probe(path, stamp)
    if cached is not None:
        return cached

    info = {"duration": 0.0, "width": 0, "height": 0, "codec": "", "ok": False}
    if not Tools.ffprobe:
        return info

    code, out = _run([
        Tools.ffprobe, "-v", "error",
    ] + PROBE_LIMITS + [
        "-print_format", "json", "-show_format", "-show_streams", str(path),
    ])
    if code == 0 and out:
        try:
            data = json.loads(out)
        except ValueError:
            data = {}
        try:
            info["duration"] = float(data.get("format", {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            info["duration"] = 0.0
        for stream in data.get("streams", []):
            if stream.get("codec_type") != "video":
                continue
            info["width"] = int(stream.get("width") or 0)
            info["height"] = int(stream.get("height") or 0)
            info["codec"] = stream.get("codec_name") or ""
            if not info["duration"]:
                try:
                    info["duration"] = float(stream.get("duration") or 0.0)
                except (TypeError, ValueError):
                    pass
            break
        info["ok"] = info["width"] > 0 or info["duration"] > 0

    INDEX.put_probe(path, stamp, info)
    return info


# ---------------------------------------------------------------------------
# Vignettes
# ---------------------------------------------------------------------------

def thumb_path(video: Path, ts: float, width: int) -> Path:
    stamp = _stamp_of(video) or "0|0"
    digest = hashlib.sha1(
        f"{video}|{stamp}|{ts:.2f}|{width}".encode("utf-8", "replace")
    ).hexdigest()
    return THUMB_DIR / digest[:2] / f"{digest}.jpg"


def extract_thumb(video: Path, ts: float, width: int) -> Path | None:
    """Extrait une image à l'instant ts. Retourne le fichier de cache, ou None."""
    out = thumb_path(video, ts, width)
    try:
        if out.exists() and out.stat().st_size > 0:
            return out
    except OSError:
        pass
    if not Tools.ffmpeg:
        return None

    out.parent.mkdir(parents=True, exist_ok=True)
    base = [Tools.ffmpeg, "-hide_banner", "-loglevel", "error"]
    # -an : pas de piste son a demultiplexer pour fabriquer une image fixe.
    tail = ["-an", "-frames:v", "1", "-vf", f"scale={width}:-2",
            "-q:v", "4", "-y", str(out)]
    seek = ["-ss", f"{max(0.0, ts):.2f}"]

    # Du plus rapide au plus sur : en-tete borne a l'endroit voulu, puis borne
    # au debut, puis sans borne. La quasi-totalite des fichiers s'arretent au
    # premier essai ; les rares recalcitrants coutent ce qu'ils coutaient avant.
    attempts = [base + seek + ["-i", str(video)] + tail]
    if ts > 0:
        # Certaines vidéos refusent le seek rapide, d'autres sont plus courtes
        # que l'instant demandé : on retombe sur la première image.
        attempts.append(base + ["-i", str(video)] + tail)

    for cmd in attempts:
        code, _ = _run(cmd, timeout=25)
        try:
            if code == 0 and out.exists() and out.stat().st_size > 0:
                return out
        except OSError:
            pass
    return None


def cache_size_bytes() -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(THUMB_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def clear_cache() -> None:
    shutil.rmtree(THUMB_DIR, ignore_errors=True)


def page_count(videos: list, count: int, one_per_video: bool = True) -> int:
    """Nombre de pages d'aperçus disponibles pour ces vidéos."""
    if not one_per_video or not videos:
        return 1
    return max(1, -(-len(videos) // count))


def build_preview_plan(videos: list, count: int, page: int = 0,
                       one_per_video: bool = True, blind: bool = False) -> list:
    """Construit une page d'aperçus.

    Chaque entrée est (fichier, instant, durée, hauteur) : le sondage ffprobe a
    déjà eu lieu ici, autant en faire profiter l'affichage plutôt que de le
    refaire depuis le fil de l'interface.

    Pour un dossier, une image par vidéo, par tranches de `count` : un dossier de
    trois vidéos montre trois aperçus, pas trois vidéos étirées sur dix cases.
    Pour une vidéo seule, `count` instants échelonnés à l'intérieur.
    """
    if not videos:
        return []

    if one_per_video:
        chunk = videos[page * count:(page + 1) * count]
        # Les hauteurs varient d'une case à l'autre : deux plans pris au même
        # endroit de deux épisodes se ressemblent souvent trop.
        pairs = [
            (video, (0.2, 0.35, 0.5, 0.65, 0.8)[index % 5])
            for index, video in enumerate(chunk)
        ]
    else:
        video = videos[0]
        pairs = [
            (video, 0.05 + 0.9 * ((index + 1) / (count + 1)))
            for index in range(count)
        ]

    plan = []
    for index, (video, fraction) in enumerate(pairs):
        info = INDEX.probe(video)
        if info is None:
            if blind:
                # Une image par video : on ne saurait que faire de la duree, et
                # la demander couterait un ffprobe de plus que l'extraction
                # elle-meme (0,36 s contre 0,17 s). Elle arrivera apres, sans
                # retenir l'image.
                ts = BLIND_OFFSETS[index % len(BLIND_OFFSETS)]
                plan.append((str(video), ts, 0.0, 0))
                continue
            # Dix instants dans une meme video : sans sa duree, on ne sait pas
            # les echelonner. Un seul sondage sert alors les dix images.
            info = probe(Path(video))
        duration = info.get("duration") or 0.0
        ts = min(duration - 1.0, duration * fraction) if duration > 2 else 0.0
        plan.append((str(video), max(0.0, ts), duration, info.get("height") or 0))
    return plan


# ---------------------------------------------------------------------------
# Exécution en arrière-plan
# ---------------------------------------------------------------------------

class JobSignals(QObject):
    plan_ready = Signal(str, list)        # item_id, [(video, ts), ...]
    thumb_ready = Signal(str, int, str)   # item_id, slot, chemin vignette
    thumb_failed = Signal(str, int)       # item_id, slot
    info_ready = Signal(str, int, float, int)   # item_id, slot, duree, hauteur


class PlanJob(QRunnable):
    """Sonde les vidéos retenues et calcule les instants des aperçus."""

    def __init__(self, signals: JobSignals, item_id: str, videos: list, count: int,
                 page: int = 0, one_per_video: bool = True, blind: bool = False):
        super().__init__()
        self.signals = signals
        self.item_id = item_id
        self.videos = videos
        self.count = count
        self.page = page
        self.one_per_video = one_per_video
        self.blind = blind
        self.cancelled = False

    def run(self) -> None:
        if self.cancelled:
            return
        plan = build_preview_plan(
            self.videos, self.count, self.page, self.one_per_video, self.blind
        )
        if not self.cancelled:
            self.signals.plan_ready.emit(self.item_id, plan)


class InfoJob(QRunnable):
    """Sonde une video apres coup, pour completer sa duree et sa resolution.

    L'image passe devant : on la montre des qu'elle est la, et ces deux
    chiffres la rejoignent quand ils arrivent. Les demander d'abord doublait le
    temps avant le premier apercu, pour une pastille.
    """

    def __init__(self, signals: JobSignals, item_id: str, slot: int, video: str):
        super().__init__()
        self.signals = signals
        self.item_id = item_id
        self.slot = slot
        self.video = video
        self.cancelled = False

    def run(self) -> None:
        if self.cancelled:
            return
        info = probe(Path(self.video))
        if not self.cancelled:
            self.signals.info_ready.emit(
                self.item_id, self.slot,
                info.get("duration") or 0.0, info.get("height") or 0)


class ThumbJob(QRunnable):
    def __init__(self, signals: JobSignals, item_id: str, slot: int,
                 video: Path, ts: float, width: int):
        super().__init__()
        self.signals = signals
        self.item_id = item_id
        self.slot = slot
        self.video = video
        self.ts = ts
        self.width = width
        self.cancelled = False

    def run(self) -> None:
        if self.cancelled:
            return
        out = extract_thumb(self.video, self.ts, self.width)
        if self.cancelled:
            return
        if out:
            self.signals.thumb_ready.emit(self.item_id, self.slot, str(out))
        else:
            self.signals.thumb_failed.emit(self.item_id, self.slot)


class Harvester(QThread):
    """Fabrique les vignettes a l'avance, quand personne ne regarde.

    La mesure est sans appel : sur le partage, une vignette coute environ une
    seconde, et seize extractions de front ne vont pas plus vite que huit — la
    ligne est saturee, pas le processeur. Une page de quarante cartes demande
    donc une demi-minute, et rien ne peut la raccourcir **au moment ou on la
    regarde**.

    Mais une vignette deja fabriquee se relit en deux millisecondes. Tout
    l'enjeu est donc de les fabriquer avant, une fois, pendant qu'on fait autre
    chose — et de s'effacer des que quelqu'un demande quelque chose.
    """

    progress = Signal(int, int)        # faites, a faire
    finished_harvest = Signal(int)     # fabriquees

    # Deux extractions seulement : la recolte est un travail de fond, elle ne
    # doit pas prendre la ligne a ce qu'on regarde.
    WORKERS = 2
    # Apres une demande au premier plan, on se tait le temps qu'elle aboutisse.
    QUIET_AFTER_REQUEST = 2.5          # secondes

    def __init__(self, width: int, parent=None):
        super().__init__(parent)
        self.width = width
        self.tasks: list = []          # [(cle, video, instant)]
        self.made = 0
        self.busy_until = 0.0
        self._lock = threading.Lock()
        self._queue: deque = deque()
        self._urgent: set = set()
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def hold(self) -> None:
        """Quelqu'un regarde : on s'ecarte."""
        self.busy_until = time.monotonic() + self.QUIET_AFTER_REQUEST

    def prioritise(self, keys) -> None:
        """Fait passer ces elements en tete de la recolte.

        Elle parcourait la collection dans l'ordre : arrive a la page cinq, on
        attendait ses apercus pendant qu'elle preparait tranquillement la page
        une. Ce qu'on a sous les yeux passe devant.
        """
        with self._lock:
            self._urgent = set(keys)

    def _next(self):
        with self._lock:
            if self._urgent:
                for position, task in enumerate(self._queue):
                    if task[0] in self._urgent:
                        del self._queue[position]
                        return task
                # Plus rien d'urgent en attente : on reprend le fil.
                self._urgent.clear()
            return self._queue.popleft() if self._queue else None

    def run(self) -> None:
        total = len(self.tasks)
        if not total:
            self.finished_harvest.emit(0)
            return

        # Ce qui est deja sur le disque ne se refait pas : la recolte reprend
        # ou elle s'etait arretee, meme apres avoir ferme l'application.
        todo = []
        for key, video, ts in self.tasks:
            if self._stop:
                return
            out = thumb_path(Path(video), ts, self.width)
            try:
                if out.exists() and out.stat().st_size > 0:
                    continue
            except OSError:
                pass
            todo.append((key, video, ts))

        done = total - len(todo)
        self.progress.emit(done, total)
        if not todo:
            self.finished_harvest.emit(0)
            return

        self._queue = deque(todo)
        counted = [done]
        last = [0.0]
        report_lock = threading.Lock()

        def worker():
            while not self._stop:
                task = self._next()
                if task is None:
                    return
                while not self._stop and time.monotonic() < self.busy_until:
                    time.sleep(0.15)
                if self._stop:
                    return
                made = extract_thumb(Path(task[1]), task[2], self.width)
                with report_lock:
                    counted[0] += 1
                    self.made += bool(made)
                    now = time.monotonic()
                    if now - last[0] >= 0.5 or counted[0] == total:
                        self.progress.emit(counted[0], total)
                        last[0] = now

        hands = [threading.Thread(target=worker, daemon=True)
                 for _ in range(self.WORKERS)]
        for hand in hands:
            hand.start()
        for hand in hands:
            hand.join()
        if not self._stop:
            self.finished_harvest.emit(self.made)


class PreviewManager(QObject):
    """Orchestre plans et vignettes, en annulant les travaux devenus inutiles."""

    plan_ready = Signal(str, list)
    thumb_ready = Signal(str, int, str)
    thumb_failed = Signal(str, int)
    info_ready = Signal(str, int, float, int)

    def __init__(self, thumb_width: int, parent=None):
        super().__init__(parent)
        self.thumb_width = thumb_width
        self.signals = JobSignals()
        self.signals.plan_ready.connect(self.plan_ready)
        self.signals.thumb_ready.connect(self.thumb_ready)
        self.signals.thumb_failed.connect(self.thumb_failed)
        self.signals.info_ready.connect(self.info_ready)
        self.pool = QThreadPool()
        self.local_workers = max(2, min(4, (os.cpu_count() or 4) // 2))
        self.pool.setMaxThreadCount(self.local_workers)
        self.jobs: list = []
        self.harvester: Harvester | None = None

    def _track(self, job) -> None:
        self.jobs.append(job)
        if len(self.jobs) > 120:
            self.jobs = self.jobs[-60:]

    def cancel_all(self) -> None:
        for job in self.jobs:
            job.cancelled = True
        self.jobs.clear()

    def cancel_except(self, keep_ids: set) -> None:
        kept = []
        for job in self.jobs:
            if job.item_id in keep_ids:
                kept.append(job)
            else:
                job.cancelled = True
        self.jobs = kept

    # L element affiche passe devant ceux qu on prepare pour apres : sans cela,
    # les vingt vignettes des deux suivants occupaient les huit fils pendant que
    # la case qu on regarde attendait son tour.
    URGENT = 10
    AHEAD = 0

    def request_plan(self, item_id: str, videos: list, count: int,
                     page: int = 0, one_per_video: bool = True,
                     urgent: bool = True, blind: bool = False) -> None:
        job = PlanJob(self.signals, item_id, videos, count, page, one_per_video,
                      blind)
        self._track(job)
        self.pool.start(job, self.URGENT if urgent else self.AHEAD)

    def request_info(self, item_id: str, slot: int, video: str) -> None:
        """Duree et resolution, en dernier : l'image ne les attend pas."""
        job = InfoJob(self.signals, item_id, slot, video)
        self._track(job)
        self.pool.start(job, -10)

    def request_thumb(self, item_id: str, slot: int, video: str, ts: float,
                      urgent: bool = True) -> None:
        if urgent and self.harvester is not None:
            self.harvester.hold()
        job = ThumbJob(self.signals, item_id, slot, Path(video), ts, self.thumb_width)
        self._track(job)
        self.pool.start(job, self.URGENT if urgent else self.AHEAD)

    def quiesce(self, timeout_ms: int = 6000) -> None:
        """Annule et attend la fin des ffmpeg en cours.

        Indispensable avant de déplacer ou supprimer : un ffmpeg qui lit encore
        un fichier empêche le renommage de son dossier parent sous Windows.
        """
        self.cancel_all()
        self.pool.waitForDone(timeout_ms)

    def tune_for(self, root) -> None:
        """Adapte le nombre d'extractions simultanées au support de stockage."""
        # Une extraction attend le reseau bien plus qu'elle n'occupe le
        # processeur : on en lance seize de front la ou une seule tiendrait la
        # ligne occupee a ne rien faire.
        workers = 16 if is_network_path(root) else self.local_workers
        if workers != self.pool.maxThreadCount():
            self.pool.setMaxThreadCount(workers)

    def start_harvest(self, tasks: list) -> "Harvester":
        """Lance, ou relance, la fabrication d'avance des vignettes."""
        self.stop_harvest()
        self.harvester = Harvester(self.thumb_width, self)
        self.harvester.tasks = tasks
        self.harvester.start(QThread.LowestPriority)
        return self.harvester

    def stop_harvest(self) -> None:
        if self.harvester is not None:
            self.harvester.stop()
            self.harvester.wait(3000)
            self.harvester = None

    def shutdown(self) -> None:
        self.stop_harvest()
        self.quiesce(2000)
        INDEX.commit(force=True)
