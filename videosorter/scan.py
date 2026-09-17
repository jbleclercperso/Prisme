"""Analyse du dossier racine : construction de la liste des éléments à trier."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .config import VIDEO_EXTS

MODE_FOLDERS = "folders"
MODE_FILES = "files"

# Au-delà, on arrête de collecter les chemins de vidéos d'un même dossier :
# dix aperçus n'en demandent pas plus et cela borne la mémoire sur les gros lots.
MAX_VIDEOS_PER_ITEM = 400


@dataclass
class Item:
    """Un élément à trier : soit un sous-dossier, soit un fichier vidéo."""

    path: Path
    kind: str                       # MODE_FOLDERS | MODE_FILES
    size: int = 0
    file_count: int = 0
    video_count: int = 0
    subdir_count: int = 0
    mtime: float = 0.0
    videos: list = field(default_factory=list)
    info: dict = field(default_factory=dict)   # rempli par ffprobe, mode fichier
    # "", "skipped", "moved", "deleted", ou un etat "pending_*" le temps que le
    # transfert en tache de fond se termine.
    status: str = ""
    status_detail: str = ""

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def item_id(self) -> str:
        return str(self.path)

    @property
    def processed(self) -> bool:
        return self.status in ("moved", "deleted")

    @property
    def pending(self) -> bool:
        """Un transfert le concernant est encore en vol."""
        return self.status.startswith("pending_")

    @property
    def locked(self) -> bool:
        """Ni relire ses apercus, ni agir dessus : il part ou il est deja parti."""
        return self.processed or self.pending


def human_size(num: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if num < 1024 or unit == "To":
            return f"{num:.0f} {unit}" if unit == "o" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} To"


def human_duration(seconds: float) -> str:
    seconds = int(seconds or 0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def _is_hidden(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    try:
        # FILE_ATTRIBUTE_HIDDEN (0x2) | FILE_ATTRIBUTE_SYSTEM (0x4)
        return bool(path.stat().st_file_attributes & 0x6)
    except (OSError, AttributeError):
        return False


def scan_folder(folder: Path) -> Item:
    """Parcourt récursivement un dossier et en agrège les statistiques."""
    item = Item(path=folder, kind=MODE_FOLDERS)
    try:
        item.mtime = folder.stat().st_mtime
    except OSError:
        pass

    for dirpath, dirnames, filenames in os.walk(folder, onerror=lambda _e: None):
        item.subdir_count += len(dirnames)
        for name in filenames:
            full = Path(dirpath) / name
            item.file_count += 1
            try:
                item.size += full.stat().st_size
            except OSError:
                pass
            if is_video(full):
                item.video_count += 1
                if len(item.videos) < MAX_VIDEOS_PER_ITEM:
                    item.videos.append(full)

    item.videos.sort(key=lambda p: str(p).lower())
    return item


def scan_file(path: Path) -> Item:
    item = Item(path=path, kind=MODE_FILES, file_count=1, video_count=1, videos=[path])
    try:
        st = path.stat()
        item.size = st.st_size
        item.mtime = st.st_mtime
    except OSError:
        pass
    return item


def detect_mode(root: Path, skip_hidden: bool = True) -> str:
    """Dossiers à l'intérieur -> mode dossier, sinon mode fichier."""
    try:
        for entry in os.scandir(root):
            if entry.is_dir(follow_symlinks=False):
                candidate = Path(entry.path)
                if skip_hidden and _is_hidden(candidate):
                    continue
                return MODE_FOLDERS
    except OSError:
        pass
    return MODE_FILES


def list_entries(root: Path, mode: str, skip_hidden: bool = True) -> list:
    """Liste, sans les analyser, les chemins de premier niveau à traiter."""
    entries = []
    try:
        for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
            path = Path(entry.path)
            if skip_hidden and _is_hidden(path):
                continue
            if mode == MODE_FOLDERS and entry.is_dir(follow_symlinks=False):
                entries.append(path)
            elif mode == MODE_FILES and entry.is_file() and is_video(path):
                entries.append(path)
    except OSError:
        pass
    return entries


class ScanThread(QThread):
    """Analyse la racine en tâche de fond, en publiant les éléments au fil de l'eau."""

    progress = Signal(int, int, str)   # fait, total, nom courant
    item_ready = Signal(object)
    finished_scan = Signal(str, int)   # mode, total

    def __init__(self, root: Path, mode: str = "", skip_hidden: bool = True, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.mode = mode
        self.skip_hidden = skip_hidden
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        mode = self.mode or detect_mode(self.root, self.skip_hidden)
        paths = list_entries(self.root, mode, self.skip_hidden)
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            if self._stop:
                return
            self.progress.emit(index, total, path.name)
            item = scan_folder(path) if mode == MODE_FOLDERS else scan_file(path)
            if self._stop:
                return
            self.item_ready.emit(item)
        self.finished_scan.emit(mode, total)
