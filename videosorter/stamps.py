"""Empreintes des vidéos déjà lues.

Le nom d'une vignette en cache contient l'empreinte de sa vidéo — taille et
date — afin qu'un fichier remplacé ne garde pas l'image de l'ancien. Mais la
relire fichier par fichier coûte un aller-retour réseau chacune : afficher une
planche de quarante cartes en demandait quarante, soit environ trois secondes
avant la première image sur le NAS de mesure, **même lorsque toutes les
vignettes étaient déjà fabriquées**.

Or l'analyse les a déjà lues : l'énumération d'un répertoire rapporte taille et
date de chacune de ses entrées, sans rien demander de plus. Il suffit de les
retenir au passage.

Ce souvenir-là ne survit pas à la fermeture. Mais au lancement suivant,
l'analyse ne relit que les dossiers qui ont bougé : sur une collection stable,
elle ne retenait plus rien, et chaque vignette — même déjà faite — recoûtait
sa lecture réseau. L'index garde pourtant, avec chaque sondage, l'empreinte
exacte de la vidéo sondée : c'est elle qu'on reprend, sans rien demander au
disque. Le prix : une vidéo écrasée sur place, sous le même nom et sans que
son dossier change, n'est revue qu'à la prochaine analyse complète.
"""
from __future__ import annotations

from pathlib import Path

from .config import VIDEO_EXTS

_STAMPS: dict = {}
# Au-dela, on repart de zero plutot que de laisser la table gonfler sans fin :
# une collection de cent mille videos tient largement en dessous.
_MAX = 400_000
# Les chemins auxquels l'application vient de toucher : pour eux, l'index ne
# vaut plus rien, seul le disque dit vrai.
_STALE: set = set()
# La derniere empreinte des chemins oublies, le temps de la reporter a leur
# nouvel emplacement.
_GONE: dict = {}
_GONE_MAX = 20_000


def _indexed(key: str) -> str | None:
    """L'empreinte retenue par l'index avec le sondage de cette video."""
    try:
        from .index import INDEX
    except ImportError:
        return None
    found = INDEX.probes.get(key)
    if found and found[0]:
        return found[0]
    return None


def remember(path, size: int, mtime: float) -> None:
    """Retient l'empreinte relevée par l'analyse, qui l'obtient gratuitement."""
    key = str(path)
    if len(_STAMPS) >= _MAX:
        _STAMPS.clear()
    _STAMPS[key] = f"{int(mtime)}|{size}"
    _STALE.discard(key)


def forget(path) -> None:
    """Oublie ce chemin : on vient d'y toucher."""
    key = str(path)
    old = _STAMPS.pop(key, None) or (None if key in _STALE else _indexed(key))
    if old:
        if len(_GONE) >= _GONE_MAX:
            _GONE.clear()
        _GONE[key] = old
    if len(_STALE) >= _MAX:
        _STALE.clear()
    _STALE.add(key)


def _under(table: dict, heads: tuple) -> list:
    """Les (cle, valeur) de cette table sous ce dossier.

    Sans copier la table d'abord : c'etait l'essentiel du temps. Un autre fil
    peut l'agrandir pendant le parcours ; on recommence alors sur une copie.
    """
    try:
        return [(key, value) for key, value in table.items()
                if key.startswith(heads)]
    except RuntimeError:
        return [(key, value) for key, value in list(table.items())
                if key.startswith(heads)]


def carry(old, new) -> list:
    """Reporte les empreintes d'un fichier, ou d'un dossier, deplace.

    Un renommage garde taille et date : la video rangee a la meme empreinte
    qu'avant, inutile de la redemander au reseau. Rend la liste des
    (ancien chemin, nouveau chemin, empreinte) reportes — pour un dossier,
    toutes les videos connues dessous.
    """
    old_key, new_key = str(old), str(new)
    moved = []
    stamp = (_STAMPS.pop(old_key, None) or _GONE.pop(old_key, None)
             or (None if old_key in _STALE else _indexed(old_key)))
    if stamp:
        moved.append((old_key, new_key, stamp))
    prefix = old_key.rstrip("\\/")
    dot = prefix.rfind(".")
    is_video = dot > 0 and prefix[dot:].lower() in VIDEO_EXTS
    if not is_video:
        heads = (prefix + "\\", prefix + "/")
        target = new_key.rstrip("\\/")
        seen = {old_key}
        # Un dossier : tout ce qu'on connait dessous, dans la memoire de la
        # seance comme dans l'index.
        # Quelques dizaines de millisecondes sur cent mille videos : a faire
        # hors du fil de l'interface.
        sources = _under(_STAMPS, heads) + _under(_GONE, heads)
        try:
            from .index import INDEX
            sources += [(path, entry[0]) for path, entry
                        in _under(INDEX.probes, heads) if entry[0]]
            # L'index a pu suivre le deplacement avant nous (actions._carry) :
            # ce qu'il sait vit alors sous le nouveau chemin, et l'on retrouve
            # l'ancien en remplacant la tete.
            fresh = (target + "\\", target + "/")
            sources += [(prefix + path[len(target):], entry[0]) for path, entry
                        in _under(INDEX.probes, fresh) if entry[0]]
        except ImportError:
            pass
        for path, value in sources:
            if path in seen:
                continue
            if path in _STALE and path not in _STAMPS and path not in _GONE:
                continue
            seen.add(path)
            moved.append((path, target + path[len(prefix):], value))
    for before, after, value in moved:
        _STAMPS.pop(before, None)
        _GONE.pop(before, None)
        _STALE.add(before)
        _STAMPS[after] = value
        _STALE.discard(after)
    return moved


def stamp_of(path: Path) -> str:
    """Empreinte de cette vidéo : retenue, sinon celle de l'index, sinon lue."""
    key = str(path)
    known = _STAMPS.get(key)
    if known is not None:
        return known
    if key not in _STALE:
        indexed = _indexed(key)
        if indexed:
            return indexed
    try:
        stat = Path(path).stat()
    except OSError:
        return ""
    stamp = f"{int(stat.st_mtime)}|{stat.st_size}"
    _STAMPS[key] = stamp
    _STALE.discard(key)
    return stamp


def known(path) -> tuple | None:
    """(taille, date) si l'analyse ou l'index les connaissent ; rien sinon, et
    aucune lecture : la fiche ne doit jamais attendre le reseau pour un
    chiffre."""
    key = str(path)
    stamp = _STAMPS.get(key)
    if stamp is None and key not in _STALE:
        stamp = _indexed(key)
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
