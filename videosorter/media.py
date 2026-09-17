"""Accès à ffmpeg / ffprobe : sondage des vidéos et extraction de vignettes."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .config import APP_DIR, PROBE_CACHE_PATH, THUMB_DIR

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
# Sondage (durée / résolution / codec) avec cache disque
# ---------------------------------------------------------------------------

class _ProbeCache:
    def __init__(self):
        self.data: dict[str, dict] = {}
        self.dirty = False
        try:
            loaded = json.loads(PROBE_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data = loaded
        except (OSError, ValueError):
            self.data = {}

    def key(self, path: Path) -> str:
        try:
            st = path.stat()
            return f"{path}|{int(st.st_mtime)}|{st.st_size}"
        except OSError:
            return str(path)

    def get(self, path: Path):
        return self.data.get(self.key(path))

    def put(self, path: Path, info: dict) -> None:
        self.data[self.key(path)] = info
        self.dirty = True

    def flush(self) -> None:
        if not self.dirty:
            return
        APP_DIR.mkdir(parents=True, exist_ok=True)
        # Borne la taille du cache pour qu'il ne grossisse pas sans fin.
        if len(self.data) > 20000:
            self.data = dict(list(self.data.items())[-10000:])
        try:
            PROBE_CACHE_PATH.write_text(json.dumps(self.data), encoding="utf-8")
            self.dirty = False
        except OSError:
            pass


PROBE_CACHE = _ProbeCache()


def probe(path: Path) -> dict:
    """Retourne {duration, width, height, codec, ok} pour une vidéo."""
    cached = PROBE_CACHE.get(path)
    if cached is not None:
        return cached

    info = {"duration": 0.0, "width": 0, "height": 0, "codec": "", "ok": False}
    if not Tools.ffprobe:
        return info

    code, out = _run([
        Tools.ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
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

    PROBE_CACHE.put(path, info)
    return info


# ---------------------------------------------------------------------------
# Vignettes
# ---------------------------------------------------------------------------

def thumb_path(video: Path, ts: float, width: int) -> Path:
    try:
        st = video.stat()
        stamp = f"{int(st.st_mtime)}|{st.st_size}"
    except OSError:
        stamp = "0|0"
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
    tail = ["-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", "-y", str(out)]

    attempts = [base + ["-ss", f"{max(0.0, ts):.2f}", "-i", str(video)] + tail]
    if ts > 0:
        # Certaines vidéos refusent le seek rapide : on retombe sur la première image.
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


def build_preview_plan(videos: list, count: int) -> list:
    """Répartit `count` aperçus sur les vidéos disponibles.

    Chaque entrée est (fichier, instant, durée, hauteur) : le sondage ffprobe a
    déjà eu lieu ici, autant en faire profiter l'affichage plutôt que de le
    refaire depuis le fil de l'interface.

    Beaucoup de vidéos : une image par vidéo, prise à des hauteurs variées.
    Peu de vidéos : plusieurs instants échelonnés dans chacune.
    """
    if not videos:
        return []

    if len(videos) >= count:
        # Échantillonne toute la liste plutôt que de prendre les N premières.
        step = len(videos) / count
        pairs = [
            (videos[min(len(videos) - 1, int(i * step))], (0.2, 0.35, 0.5, 0.65, 0.8)[i % 5])
            for i in range(count)
        ]
    else:
        per_video = [count // len(videos)] * len(videos)
        for i in range(count % len(videos)):
            per_video[i] += 1
        pairs = []
        for video, slots in zip(videos, per_video):
            for i in range(slots):
                pairs.append((video, 0.05 + 0.9 * ((i + 1) / (slots + 1))))

    plan = []
    for video, fraction in pairs:
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


class PlanJob(QRunnable):
    """Sonde les vidéos retenues et calcule les instants des aperçus."""

    def __init__(self, signals: JobSignals, item_id: str, videos: list, count: int):
        super().__init__()
        self.signals = signals
        self.item_id = item_id
        self.videos = videos
        self.count = count
        self.cancelled = False

    def run(self) -> None:
        if self.cancelled:
            return
        plan = build_preview_plan(self.videos, self.count)
        if not self.cancelled:
            self.signals.plan_ready.emit(self.item_id, plan)


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


class PreviewManager(QObject):
    """Orchestre plans et vignettes, en annulant les travaux devenus inutiles."""

    plan_ready = Signal(str, list)
    thumb_ready = Signal(str, int, str)
    thumb_failed = Signal(str, int)

    def __init__(self, thumb_width: int, parent=None):
        super().__init__(parent)
        self.thumb_width = thumb_width
        self.signals = JobSignals()
        self.signals.plan_ready.connect(self.plan_ready)
        self.signals.thumb_ready.connect(self.thumb_ready)
        self.signals.thumb_failed.connect(self.thumb_failed)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(2, min(4, (os.cpu_count() or 4) // 2)))
        self.jobs: list = []

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

    def request_plan(self, item_id: str, videos: list, count: int) -> None:
        job = PlanJob(self.signals, item_id, videos, count)
        self._track(job)
        self.pool.start(job)

    def request_thumb(self, item_id: str, slot: int, video: str, ts: float) -> None:
        job = ThumbJob(self.signals, item_id, slot, Path(video), ts, self.thumb_width)
        self._track(job)
        self.pool.start(job)

    def quiesce(self, timeout_ms: int = 6000) -> None:
        """Annule et attend la fin des ffmpeg en cours.

        Indispensable avant de déplacer ou supprimer : un ffmpeg qui lit encore
        un fichier empêche le renommage de son dossier parent sous Windows.
        """
        self.cancel_all()
        self.pool.waitForDone(timeout_ms)

    def shutdown(self) -> None:
        self.quiesce(2000)
        PROBE_CACHE.flush()
