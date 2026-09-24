"""Empreintes des vidéos déjà lues, pour la durée de la session.

Le nom d'une vignette en cache contient l'empreinte de sa vidéo — taille et
date — afin qu'un fichier remplacé ne garde pas l'image de l'ancien. Mais la
relire fichier par fichier coûte un aller-retour réseau chacune : afficher une
planche de quarante cartes en demandait quarante, soit environ trois secondes
avant la première image sur le NAS de mesure, **même lorsque toutes les
vignettes étaient déjà fabriquées**.

Or l'analyse les a déjà lues : l'énumération d'un répertoire rapporte taille et
date de chacune de ses entrées, sans rien demander de plus. Il suffit de les
retenir au passage.

Ce souvenir ne survit pas à la fermeture, et c'est voulu : au prochain
lancement, une vidéo remplacée entre-temps sera revue telle qu'elle est.
"""
from __future__ import annotations

from pathlib import Path

_STAMPS: dict = {}
# Au-dela, on repart de zero plutot que de laisser la table gonfler sans fin :
# une collection de cent mille videos tient largement en dessous.
_MAX = 400_000


def remember(path, size: int, mtime: float) -> None:
    """Retient l'empreinte relevée par l'analyse, qui l'obtient gratuitement."""
    if len(_STAMPS) >= _MAX:
        _STAMPS.clear()
    _STAMPS[str(path)] = f"{int(mtime)}|{size}"


def forget(path) -> None:
    """Oublie ce chemin : on vient d'y toucher."""
    _STAMPS.pop(str(path), None)


def stamp_of(path: Path) -> str:
    """Empreinte de cette vidéo, retenue si on la connaît, lue sinon."""
    key = str(path)
    known = _STAMPS.get(key)
    if known is not None:
        return known
    try:
        stat = Path(path).stat()
    except OSError:
        return ""
    stamp = f"{int(stat.st_mtime)}|{stat.st_size}"
    _STAMPS[key] = stamp
    return stamp


def known(path) -> tuple | None:
    """(taille, date) si l'analyse les a deja releves ; rien sinon, et aucune
    lecture : la fiche ne doit jamais attendre le reseau pour un chiffre."""
    stamp = _STAMPS.get(str(path))
    if not stamp:
        return None
    try:
        mtime, size = stamp.split("|")
        return int(size), float(mtime)
    except ValueError:
        return None


def count() -> int:
    """Nombre d'empreintes retenues, pour pouvoir le vérifier."""
    return len(_STAMPS)
