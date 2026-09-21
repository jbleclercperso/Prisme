"""Configuration persistante et emplacements de VideoSorter."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "Prisme"
# Le logiciel s'est appele VideoSorter. Son cache pese plus d'un gigaoctet de
# vignettes et un index de vingt-six mega : on le reprend tel quel au premier
# lancement, au lieu de tout refabriquer.
_OLD_NAME = "VideoSorter"
_LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home())
APP_DIR = _LOCAL / APP_NAME


def adopt_old_cache() -> str:
    """Renomme l'ancien dossier, si le nouveau n'existe pas encore.

    Rend le chemin repris, ou une chaine vide. Un echec n'est pas grave : on
    repart d'un cache vide, ce qui coute du temps mais ne perd rien.
    """
    old = _LOCAL / _OLD_NAME
    if APP_DIR.exists() or not old.is_dir():
        return ""
    try:
        old.rename(APP_DIR)
    except OSError:
        return ""
    return str(old)
CONFIG_PATH = APP_DIR / "config.json"
def _chosen_cache() -> Path | None:
    """Le cache designe ailleurs, s'il l'a ete.

    Deux ordinateurs qui regardent le meme partage fabriquent exactement les
    memes vignettes : leur nom ne depend que du chemin, de la taille et de la
    date du fichier. Les mettre en commun evite au second de refaire le
    travail du premier — et c'est le travail le plus long de tous.

    Deux facons de le designer, dans cet ordre : la variable d'environnement
    PRISME_CACHE, ou un fichier `prisme.cache` pose a cote du programme, qui
    ne contient qu'un chemin. Un fichier plutot qu'un reglage dans
    config.json : celui-ci vit deja dans le cache, et l'on ne peut pas y lire
    ou il se trouve.
    """
    told = os.environ.get("PRISME_CACHE", "").strip()
    if not told:
        for folder in (Path(sys.argv[0]).resolve().parent, Path.cwd()):
            note = folder / "prisme.cache"
            try:
                if note.is_file():
                    told = note.read_text(encoding="utf-8").strip()
                    if told:
                        break
            except OSError:
                continue
    if not told:
        return None
    try:
        chosen = Path(told).expanduser()
        chosen.mkdir(parents=True, exist_ok=True)
        return chosen
    except OSError:
        return None


SHARED_DIR = _chosen_cache()
if SHARED_DIR is not None:
    APP_DIR = SHARED_DIR

# Les vignettes se partagent sans risque : ce sont des fichiers independants,
# nommes par leur contenu. L'index, lui, est une base : deux ecritures en
# meme temps par le reseau la fragiliseraient. Il reste donc chez chacun,
# sauf demande expresse.
THUMB_DIR = APP_DIR / "thumbs"
INDEX_PATH = (_LOCAL / APP_NAME / "index.db" if SHARED_DIR is not None
              else APP_DIR / "index.db")
# Anciens caches JSON, repris puis effaces par l'index au premier lancement.
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
    # Ou prendre l'image d'une carte : le reglage le plus cher de tous.
    # 0 = le plus rapide, 6 = des images plus parlantes et deux fois plus lentes.
    "preview_start": 2.0,
    "thumb_count": 10,
    "thumb_width": 480,
    "delete_mode": "recycle",    # recycle | permanent | local_trash
    "ffmpeg": "",                # vide => recherche automatique
    "ffprobe": "",
    "window": {"w": 1400, "h": 900},
    "skip_hidden": True,
    "use_scan_cache": True,      # reutiliser l analyse precedente
    "sort_mode": "random",       # random | duration_desc | size_asc | …
    "content": "folders",        # conserve pour compatibilite
    "view": "browse",            # conserve pour compatibilite
    "tab": "folders",            # onglet : folders | videos | tags
    "tag_family": "mine",        # mots-cles affiches : mine | top
    "tree_action": "send",       # clic dans l arborescence : send | go
    "tags": [],
    "last_item": "",             # dernier element regarde, pour y revenir
    "collection": {},            # videos, vignettes, dates : l'etat affiche en haut
    "only_unseen": False,        # ne montrer que ce qui reste a voir
    "orientations": ["vertical", "horizontal"],  # ce qu'on veut voir
    "folder_min": 0,             # dossiers d'au moins tant de videos (0 : tous)
    "folder_max": 0,             # d'au plus tant (0 : sans limite)
    "searches": [],              # recherches enregistrees : nom, requete, tri, non-vus, onglet
    "burst": False,              # rafale : passer tout seul apres quelques secondes
    "wall_panes": 3,             # panneaux du mur
    "wall_orientation": "vertical",  # ce qu'il y pioche : vertical | horizontal | any
    "thumbs_last_run": "",       # date de la derniere preparation menee a terme                  # mots-cles, un par ligne
    "board_columns": 5,          # cartes par rangee en vue planche
    "expand_parents": True,      # traverser les dossiers prefixes « + »
    "web_search_api_key": "",           # cle SerpAPI (gratuite, 250 recherches/mois)
    "web_search_min_duration_min": 0,   # filtre de la recherche web, en minutes
    "web_search_min_height": 0,         # filtre de la recherche web, en pixels
    "web_search_max_sites": 15,         # nombre de sites explores par recherche
    "web_search_max_results": 30,       # plafond de resultats, pour rester qualitatif
    "web_search_strict_keywords": True, # tous les mots-cles requis, pas un seul
    "web_search_known_domains": "",     # domaines de confiance, pour restreindre la recherche
    "web_search_discover_new_sites": False,  # completer par SerpAPI (quota limite)
}


class Config:
    """Petit wrapper JSON, tolerant aux fichiers absents ou corrompus."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self._timer = None
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

    def save_soon(self) -> None:
        """Ecrit dans un instant, une fois pour toutes les modifications.

        Chaque onglet, chaque fiche ecrivait le fichier sur-le-champ. On
        regroupe : la derniere demande l'emporte, une demi-seconde plus tard.
        """
        from PySide6.QtCore import QCoreApplication, QTimer
        if QCoreApplication.instance() is None:
            return self.save()
        if self._timer is None:
            self._timer = QTimer()
            self._timer.setSingleShot(True)
            self._timer.setInterval(500)
            self._timer.timeout.connect(self.save)
        self._timer.start()

    def save(self) -> None:
        if self._timer is not None:
            self._timer.stop()
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
