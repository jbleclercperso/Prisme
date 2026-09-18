"""Configuration persistante et emplacements de VideoSorter."""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "VideoSorter"
APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
CONFIG_PATH = APP_DIR / "config.json"
THUMB_DIR = APP_DIR / "thumbs"
PROBE_CACHE_PATH = APP_DIR / "probe-cache.json"
SCAN_CACHE_PATH = APP_DIR / "scan-cache.json"
RATINGS_PATH = APP_DIR / "ratings.json"
LOCAL_TRASH = APP_DIR / "_TRASH"

VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".webm",
    ".mpg", ".mpeg", ".m2ts", ".mts", ".ts", ".vob", ".3gp", ".ogv",
    ".rm", ".rmvb", ".asf", ".divx", ".f4v",
}

# Ordre d'attribution automatique des touches : les chiffres d'abord, puis les
# lettres dans l'ordre du clavier AZERTY. Toutes les lettres sont disponibles,
# les commandes de l'application etant sur Ctrl ou sur des touches de navigation.
# 0 a 5 sont la notation : une main sur les chiffres note, l'autre range.
# Les destinations prennent donc la suite, a partir de 6.
KEY_ORDER = "6789azertyuiopqsdfghjklmwxcvbn"
RESERVED_KEYS = {"0", "1", "2", "3", "4", "5"}

DEFAULTS = {
    "root": "",
    "recent_roots": [],
    "destinations": [],          # [{"key": "1", "label": "2019", "path": "D:/Tri/2019"}]
    "muted": True,
    "scroll_seconds": 5,         # pas de la molette dans une video
    "tree_root": "",             # racine du panneau d'arborescence
    "tree_visible": False,
    "filter_include": "",       # termes a chercher dans le nom
    "filter_exclude": "",       # termes qui ecartent un element
    "preview_seconds": 10,
    "thumb_count": 10,
    "thumb_width": 480,
    "delete_mode": "recycle",    # recycle | permanent | local_trash
    "ffmpeg": "",                # vide => recherche automatique
    "ffprobe": "",
    "window": {"w": 1400, "h": 900},
    "skip_hidden": True,
    "use_scan_cache": True,      # reutiliser l analyse precedente
    "sort_mode": "",             # "" | "desc" | "asc" : classement des apercus
    "board_view": False,         # vue planche plutot que fiche unique
    "board_columns": 5,          # cartes par rangee en vue planche
}


class Config:
    """Petit wrapper JSON, tolerant aux fichiers absents ou corrompus."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self.data = json.loads(json.dumps(DEFAULTS))
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in DEFAULTS:
                    self.data[key] = value
        self._migrate_reserved_keys()

    def _migrate_reserved_keys(self) -> None:
        """Deplace les destinations posees sur une touche devenue la notation.

        Les configurations ecrites avant que 0 a 5 servent a noter gardaient ces
        touches : le raccourci ne se serait plus jamais declenche, sans rien dire.
        """
        destinations = self.data.get("destinations") or []
        taken = {d.get("key") for d in destinations if d.get("key") not in RESERVED_KEYS}
        moved = False
        for dest in destinations:
            if dest.get("key") not in RESERVED_KEYS:
                continue
            for candidate in KEY_ORDER:
                if candidate not in taken:
                    dest["key"] = candidate
                    taken.add(candidate)
                    moved = True
                    break
            else:
                dest["key"] = ""
                moved = True
        if moved:
            self.save()

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)

    # -- racines recentes ------------------------------------------------
    def push_recent_root(self, root: str) -> None:
        recents = [r for r in self.data.get("recent_roots", []) if r != root]
        recents.insert(0, root)
        self.data["recent_roots"] = recents[:8]
        self.data["root"] = root

    # -- destinations ----------------------------------------------------
    @property
    def destinations(self) -> list[dict]:
        return self.data.get("destinations", [])

    def set_destinations(self, destinations: list[dict]) -> None:
        self.data["destinations"] = destinations

    def destination_for_key(self, key: str) -> dict | None:
        for dest in self.destinations:
            if dest.get("key") == key:
                return dest
        return None

    def next_free_key(self) -> str:
        used = {d.get("key") for d in self.destinations}
        for key in KEY_ORDER:
            if key not in used and key not in RESERVED_KEYS:
                return key
        return ""
