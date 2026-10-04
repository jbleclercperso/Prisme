"""Les videos seules dans leur dossier : regroupees dans « 1 ».

Un dossier qui ne contient qu'une video, c'est une video rangee a part pour
rien. Apres une analyse, Prisme les nomme et propose de les regrouper : la
video prend le nom de son dossier et part dans le dossier « 1 » de la racine
principale ; le dossier vide disparait. S'il restait autre chose a cote
(des photos, une affiche, un .nfo), le dossier, video en moins, part dans
« PICS OK ». Un dossier qui contient d'autres dossiers n'est pas concerne.

Les « 1 » des autres racines se fusionnent dans celui de la racine
principale, au meme moment.

Rien ne se fait sans qu'on l'ait accepte, et chaque deplacement s'annule
(Ctrl+Z). Tout est reverifie sur le disque juste avant d'agir : la liste
proposee vient de l'index, qui peut avoir un temps de retard.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import VIDEO_EXTS

HOME_NAME = "1"
PICS_NAME = "PICS OK"
# Ce que le NAS ou Windows posent d'eux-memes dans un dossier : ni un fichier
# a garder, ni un sous-dossier qui empecherait de regrouper.
JUNK_FILES = {"thumbs.db", "desktop.ini", ".ds_store", "ehthumbs.db"}
JUNK_DIRS = {"@eadir", ".@__thumb", ".@__desc", ".@__comments"}


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(str(path))).rstrip("\\/")


def _is_video(name: str) -> bool:
    dot = name.rfind(".")
    return dot > 0 and name[dot:].lower() in VIDEO_EXTS


def main_root(roots: list):
    """La racine principale : la premiere sur le reseau (le NAS), sinon la
    premiere tout court."""
    from .media import is_network_path
    for root in roots:
        if is_network_path(root):
            return Path(root)
    return Path(roots[0]) if roots else None


def candidates(items, home: Path, pics: Path, roots: list, ignored) -> list:
    """Les dossiers que l'index dit a une seule video, d'apres l'analyse."""
    skip = {_norm(home), _norm(pics)}
    tops = {_norm(root) for root in roots}
    ignored = {_norm(p) for p in ignored or []}
    found = []
    for item in items:
        if (item.kind != "folders" or item.video_count != 1 or item.is_tag
                or item.pinned or getattr(item, "loose_only", False)
                or item.unreadable or item.incomplete):
            continue
        text = _norm(item.path)
        if (Path(item.path).name in (HOME_NAME, PICS_NAME)
                and _norm(Path(item.path).parent) in tops):
            continue                              # le « 1 » ou « PICS OK » d'une autre racine
        if (text in tops or text in ignored
                or any(text == s or text.startswith(s + os.sep) for s in skip)):
            continue
        found.append(Path(item.path))
    return found


def inspect(folder: Path):
    """Ce qu'il y a vraiment dans ce dossier, lu sur le disque : None s'il
    n'a pas exactement une video, ou s'il contient d'autres dossiers."""
    videos, others, junk = [], [], []
    try:
        with os.scandir(folder) as listing:
            for entry in listing:
                low = entry.name.lower()
                if entry.is_dir(follow_symlinks=False):
                    if low in JUNK_DIRS:
                        junk.append(entry.path)
                        continue
                    return None                  # d'autres dossiers : on n'y touche pas
                if low in JUNK_FILES:
                    junk.append(entry.path)
                elif _is_video(entry.name):
                    videos.append(entry.path)
                else:
                    others.append(entry.path)
    except OSError:
        return None
    if len(videos) != 1:
        return None
    return {"folder": Path(folder), "video": Path(videos[0]),
            "others": [Path(p) for p in others], "junk": junk}


def new_name(folder: Path, video: Path) -> str:
    """Le nom du dossier, avec l'extension de la video."""
    from .actions import name_problem
    name = folder.name.rstrip(" .") + video.suffix.lower()
    return video.name if name_problem(name) else name


def other_homes(roots: list, home: Path) -> list:
    """Les « 1 » des autres racines, a fusionner dans le principal."""
    found = []
    for root in roots:
        candidate = Path(root) / HOME_NAME
        if _norm(candidate) == _norm(home):
            continue
        try:
            if candidate.is_dir() and any(os.scandir(candidate)):
                found.append(candidate)
        except OSError:
            continue
    return found


def _drop_junk(paths) -> None:
    for path in paths:
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            pass


def gather(plan: dict, home: Path, pics: Path, done: list) -> None:
    """Regroupe un dossier : la video dans « 1 » sous le nom du dossier, le
    reste dans « PICS OK », le dossier vide supprime. Rend les entrees
    d'historique (Ctrl+Z) dans `done`, au fur et a mesure : un echec a
    mi-chemin laisse annulable ce qui a deja ete fait."""
    from .actions import ActionError, HistoryEntry, move_to, rename_to
    folder, video = plan["folder"], plan["video"]
    name = new_name(folder, video)
    renamed = video
    if name != video.name:
        try:
            renamed = rename_to(video, name)
        except ActionError:
            renamed = video                       # le nom est pris a cote : on garde le sien
    target = move_to(renamed, home)
    done.append(HistoryEntry("move", video, target,
                             f"« {folder.name} » → {HOME_NAME}", True))
    if plan["others"]:
        _drop_junk(plan["junk"])                  # les vignettes du NAS ne voyagent pas
        moved = move_to(folder, pics)
        done.append(HistoryEntry("move", folder, moved,
                                 f"« {folder.name} » → {PICS_NAME}", True))
    else:
        _drop_junk(plan["junk"])
        try:
            folder.rmdir()
        except OSError:
            pass                                  # quelque chose est arrive entre-temps : on le laisse


def merge_home(source: Path, home: Path, done: list, progress=None) -> None:
    """Fusionne un autre « 1 » dans le principal ; le vide disparait."""
    from .actions import HistoryEntry, move_to
    for entry in sorted(os.scandir(source), key=lambda e: e.name.lower()):
        if entry.name.lower() in JUNK_FILES:
            continue
        target = move_to(Path(entry.path), home)
        done.append(HistoryEntry("move", Path(entry.path), target,
                                 f"{source} → {HOME_NAME}", True))
        if progress is not None:
            progress(entry.name)
    try:
        leftovers = [e.path for e in os.scandir(source)
                     if e.name.lower() in JUNK_FILES]
        _drop_junk(leftovers)
        source.rmdir()
    except OSError:
        pass
