"""Cache persistant des analyses de dossiers.

Réanalyser une collection entière à chaque démarrage est du gaspillage : d'un
lancement à l'autre, presque rien n'a bougé. On garde donc le résultat de chaque
dossier, avec une empreinte bon marché qui dit s'il faut le refaire.

L'empreinte est la date de modification du dossier et celles de ses sous-dossiers
immédiats. Windows met à jour la date d'un dossier dès qu'une entrée y est
ajoutée, retirée ou renommée : cela couvre donc tout changement survenu au
premier ou au deuxième niveau. Plus profond, la modification passe inaperçue
jusqu'à une actualisation forcée (Ctrl+R) — c'est le prix d'une vérification qui
ne relit pas l'arborescence entière, et c'est justement ce qu'on veut éviter.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from .config import APP_DIR, SCAN_CACHE_PATH
from .scan import MODE_FOLDERS, Item

CACHE_VERSION = 1
MAX_ENTRIES = 30000


def signature(folder: Path) -> str:
    """Empreinte du dossier et de ses sous-dossiers directs, ou "" si illisible.

    Les dates sont lues en nanosecondes : arrondies à la seconde, deux
    modifications rapprochées donneraient la même empreinte et la seconde
    passerait inaperçue.
    """
    try:
        parts = [str(folder.stat().st_mtime_ns)]
    except OSError:
        return ""
    subdirs = []
    try:
        for entry in os.scandir(folder):
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                # os.stat sur le chemin, et non entry.stat() : l'enumeration d'un
                # repertoire NTFS rapporte une date de sous-dossier qui peut
                # retarder de plusieurs secondes sur la realite, ce qui ferait
                # manquer une modification recente.
                subdirs.append(f"{entry.name}:{os.stat(entry.path).st_mtime_ns}")
            except OSError:
                continue
    except OSError:
        return ""
    parts.extend(sorted(subdirs))
    digest = hashlib.sha1("|".join(parts).encode("utf-8", "replace"))
    return digest.hexdigest()[:16]


class ScanCache:
    """Table {dossier: statistiques}, relue au démarrage et écrite en fin d'analyse."""

    def __init__(self, path: Path = SCAN_CACHE_PATH):
        self.path = path
        self.data: dict = {}
        self.dirty = False
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict) and raw.get("version") == CACHE_VERSION:
            entries = raw.get("entries")
            if isinstance(entries, dict):
                self.data = entries

    def get(self, folder: Path, sig: str) -> Item | None:
        if not sig:
            return None
        record = self.data.get(str(folder))
        if not record or record.get("sig") != sig:
            return None
        record["used"] = int(time.time())
        folder = Path(folder)
        return Item(
            path=folder,
            kind=MODE_FOLDERS,
            size=record.get("size", 0),
            file_count=record.get("file_count", 0),
            video_count=record.get("video_count", 0),
            subdir_count=record.get("subdir_count", 0),
            mtime=record.get("mtime", 0.0),
            # Les chemins sont stockés relatifs : sur des milliers de dossiers,
            # répéter le préfixe commun gonflerait le fichier pour rien.
            videos=[folder / relative for relative in record.get("videos", [])],
        )

    def put(self, item: Item, sig: str) -> None:
        if not sig or item.kind != MODE_FOLDERS:
            return
        folder = Path(item.path)
        relatives = []
        for video in item.videos:
            try:
                relatives.append(str(Path(video).relative_to(folder)))
            except ValueError:
                relatives.append(str(video))
        self.data[str(folder)] = {
            "sig": sig,
            "size": item.size,
            "file_count": item.file_count,
            "video_count": item.video_count,
            "subdir_count": item.subdir_count,
            "mtime": item.mtime,
            "videos": relatives,
            "used": int(time.time()),
        }
        self.dirty = True

    def forget(self, folder: Path) -> None:
        if self.data.pop(str(folder), None) is not None:
            self.dirty = True

    def flush(self) -> None:
        if not self.dirty:
            return
        if len(self.data) > MAX_ENTRIES:
            # Les dossiers les moins récemment consultés partent en premier.
            ordered = sorted(self.data.items(), key=lambda kv: kv[1].get("used", 0))
            self.data = dict(ordered[-MAX_ENTRIES:])
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            payload = {"version": CACHE_VERSION, "entries": self.data}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.path)
            self.dirty = False
        except OSError:
            pass

    def clear(self) -> None:
        self.data = {}
        self.dirty = True
        self.flush()

    def size_on_disk(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


CACHE = ScanCache()
