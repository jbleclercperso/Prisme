"""Plusieurs racines : le NAS, un disque externe, un dossier du PC.

Prisme travaille sur « une racine » : la fenetre, l'analyse, les vignettes,
les doublons... Pour les reunir toutes, une racine virtuelle, UNION, se deplie
en vraies racines la ou l'on parcourt le disque (`members`) ; tout le reste de
Prisme la traite comme n'importe quelle racine.

Une racine injoignable -- disque debranche, NAS eteint -- n'est pas depliee :
ses dossiers et ses videos disparaissent des listes, et reviennent avec elle.
Rien n'est oublie pour autant : l'index garde tout ce qu'il sait d'elle.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

# Le nom de la racine virtuelle : il ne peut pas etre un vrai chemin.
UNION = "\u222a Toutes les racines"

_lock = threading.Lock()
_roots: list = []            # les racines choisies, dans l'ordre
_reachable: set = set()      # celles qui repondent (chemins normalises)
_off: set = set()            # celles qu'on a decochees (chemins normalises)


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def is_union(path) -> bool:
    return path is not None and str(path) == UNION


def set_roots(paths: list) -> None:
    """Les racines choisies (la fenetre les tient de la configuration)."""
    with _lock:
        _roots[:] = [Path(p) for p in paths if p and not is_union(p)]


def roots() -> list:
    with _lock:
        return list(_roots)


def set_off(paths) -> None:
    """Les racines decochees : elles restent dans la liste, hors de la reunion."""
    with _lock:
        _off.clear()
        _off.update(_norm(p) for p in paths if p)


def is_on(path) -> bool:
    with _lock:
        return _norm(path) not in _off


def chosen_on() -> list:
    """Les racines cochees, joignables ou non."""
    with _lock:
        return [p for p in _roots if _norm(p) not in _off]


def set_reachable(paths) -> None:
    with _lock:
        _reachable.clear()
        _reachable.update(_norm(p) for p in paths)


def reachable(path) -> bool:
    with _lock:
        return _norm(path) in _reachable


def members(path) -> list:
    """Les vraies racines derriere `path` : celles qui sont cochees et qui
    repondent, pour la racine virtuelle ; `path` lui-meme sinon."""
    if not is_union(path):
        return [Path(path)]
    with _lock:
        return [p for p in _roots if _norm(p) in _reachable and _norm(p) not in _off]


def starts(path) -> list:
    """Les dossiers d'ou partir pour parcourir `path` (chemins texte)."""
    return [str(p) for p in members(path)]


def owner(path):
    """La racine choisie qui contient ce chemin, ou None."""
    text = _norm(path)
    best = None
    with _lock:
        for root in _roots:
            top = _norm(root).rstrip("\\/")
            if text == top or text.startswith(top + os.sep):
                if best is None or len(top) > len(_norm(best)):
                    best = root
    return best


def label(path) -> str:
    """Le nom court d'une racine, pour les menus : « Volume 3 (\\\\as1104t) »,
    « E:\\ », « Vidéos (D:) »."""
    if is_union(path):
        with _lock:
            on = sum(1 for p in _roots if _norm(p) not in _off)
            total = len(_roots)
        return "Toutes les racines" if on >= total else f"{on} racines sur {total}"
    text = str(path).rstrip("\\/")
    name = os.path.basename(text)
    drive = os.path.splitdrive(text)[0]
    if not name and text.startswith("\\\\"):
        # Le partage lui-meme (\\as1104t\Volume 3) : pour Windows, c'est un
        # « lecteur », sans nom de dossier ; on montrait le chemin entier.
        parts = text.lstrip("\\").split("\\")
        if len(parts) >= 2:
            return f"{parts[1]} (\\\\{parts[0]})"
    if not name:
        return text + os.sep
    if text.startswith("\\\\"):
        server = text.lstrip("\\").split("\\")[0]
        return f"{name} (\\\\{server})"
    return f"{name} ({drive})" if drive else name


def probe(path, timeout: float = 3.0) -> bool:
    """La racine repond-elle ? Sans jamais attendre plus de `timeout` : un
    disque debranche ou un NAS eteint faisait patienter des dizaines de
    secondes."""
    result = {}

    def look() -> None:
        try:
            result["ok"] = os.path.isdir(str(path))
        except OSError:
            result["ok"] = False
    worker = threading.Thread(target=look, daemon=True, name="prisme-racine")
    worker.start()
    worker.join(timeout)
    return bool(result.get("ok"))


def check_all(timeout: float = 3.0) -> set:
    """Les racines qui repondent, sondees ensemble ; retenues pour `members`."""
    found = set()
    threads = []
    lock = threading.Lock()

    def one(root) -> None:
        if probe(root, timeout):
            with lock:
                found.add(str(root))
    for root in roots():
        thread = threading.Thread(target=one, args=(root,), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout + 0.5)
    set_reachable(found)
    return found
