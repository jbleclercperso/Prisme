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


# Hauteurs normalisees, pour nommer une resolution plutot que la decrire.
STANDARD_HEIGHTS = (144, 240, 360, 480, 576, 720, 1080, 1440)


def human_resolution(height: int) -> str:
    """Traduit une hauteur d'image en appellation courante : 720p, 1080p, 4K."""
    if not height:
        return ""
    if height >= 4320:
        return "8K"
    if height >= 2160:
        return "4K"
    closest = min(STANDARD_HEIGHTS, key=lambda standard: abs(standard - height))
    return f"{closest}p"


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
    """Parcourt récursivement un dossier et en agrège les statistiques.

    Écrit avec `os.scandir` et non `os.walk` : sous Windows, l'énumération d'un
    répertoire rapporte déjà taille et type de chaque entrée, et `DirEntry.stat()`
    se sert de ces données au lieu d'interroger le disque une seconde fois. Les
    chemins restent des chaînes tant que possible, `pathlib` coûtant cher quand
    on l'invoque des dizaines de milliers de fois.
    """
    item = Item(path=folder, kind=MODE_FOLDERS)
    try:
        item.mtime = folder.stat().st_mtime
    except OSError:
        pass

    size = 0
    file_count = 0
    video_count = 0
    subdir_count = 0
    videos: list = []

    stack = [str(folder)]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdir_count += 1
                    stack.append(entry.path)
                    continue
                file_count += 1
                size += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            dot = entry.name.rfind(".")
            if dot > 0 and entry.name[dot:].lower() in VIDEO_EXTS:
                video_count += 1
                if len(videos) < MAX_VIDEOS_PER_ITEM:
                    videos.append(entry.path)

    item.size = size
    item.file_count = file_count
    item.video_count = video_count
    item.subdir_count = subdir_count
    videos.sort(key=str.lower)
    item.videos = [Path(path) for path in videos]
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

    def __init__(self, root: Path, mode: str = "", skip_hidden: bool = True,
                 use_cache: bool = True, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.mode = mode
        self.skip_hidden = skip_hidden
        self.use_cache = use_cache
        self.reused = 0        # dossiers relus depuis le cache
        self.rescanned = 0     # dossiers qu il a fallu reparcourir
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        # Import tardif : le cache depend de ce module, l importer en tete
        # creerait un cycle.
        from .scan_cache import CACHE, signature

        mode = self.mode or detect_mode(self.root, self.skip_hidden)
        paths = list_entries(self.root, mode, self.skip_hidden)
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            if self._stop:
                return
            self.progress.emit(index, total, path.name)

            if mode != MODE_FOLDERS:
                item = scan_file(path)
            else:
                sig = signature(path) if self.use_cache else ""
                item = CACHE.get(path, sig) if sig else None
                if item is None:
                    item = scan_folder(path)
                    CACHE.put(item, sig or signature(path))
                    self.rescanned += 1
                else:
                    self.reused += 1

            if self._stop:
                return
            self.item_ready.emit(item)

        CACHE.flush()
        self.finished_scan.emit(mode, total)
