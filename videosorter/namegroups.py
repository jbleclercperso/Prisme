"""Les fichiers d'une meme collection, reconnus a leur nom.

Une collection telechargee d'un meme endroit garde un morceau de nom commun,
a la lettre pres -- meme casse, meme ponctuation : « Cum Fantasy, Titre un »,
« Cum Fantasy, Autre titre », ou au milieu : « Anna - Cum Fantasy - Plage ».
Ce n'est pas un mot-cle (deux fichiers qui contiennent « cum » et « fantasy »
ne sont pas de la meme collection) : c'est un morceau exact, borne par la
ponctuation (debut du nom, virgule, tiret entoure d'espaces, souligne,
parenthese…), le reste du nom changeant. On les retrouve, et l'on propose un
dossier pour les reunir.

Sans Qt, sans IA : seulement les noms.
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

# Ce qui separe deux morceaux d'un nom. Un debut commun s'arrete toujours a
# l'un d'eux : « Cum Fantasy » et non « Cum Fanta ».
SEPARATORS = set(" ,-_.|()[]{}+&#!~;:'–—")
# Les separateurs qui ferment souvent un nom de collection : « Serie, titre »,
# « Studio - titre », « Studio_titre », « Studio | titre », « Studio (titre) ».
STRONG = set(",-_|([–—")

# Ce qu'un appareil met en tete de ses fichiers : ce n'est pas une collection.
_CAMERA = re.compile(r"^(img|vid|pxl|dsc|dscn|mvi|gopr|gp|dji|mov|video|screen|"
                     r"record|recording|capture|whatsapp|snapchat|signal|telegram)"
                     r"[\s_\-]*\d", re.IGNORECASE)

# La finesse : le plus long debut commun (petits groupes tres surs), jusqu'a
# un separateur fort (« Serie, » : le reglage par defaut), ou le plus court.
GRAINS = ("tres_fin", "fin", "large")
MIN_CHARS = {"tres_fin": 6, "fin": 6, "large": 6}


def _strong_at(stem: str, at: int) -> bool:
    """Un separateur fort commence-t-il ici ? Un tiret ne compte qu'entoure
    d'espaces : « Jean-Luc » ou « X-Men » ne se coupent pas."""
    for offset in range(0, 3):
        ch = stem[at + offset] if at + offset < len(stem) else ""
        if not ch:
            return False
        if ch == "-":
            return (at + offset == 0 or stem[at + offset - 1] == " "
                    or at + offset + 1 >= len(stem) or stem[at + offset + 1] == " ")
        if ch in STRONG:
            return True
        if ch != " ":
            return False
    return False


def _cuts(stem: str) -> list:
    """Les debuts possibles d'un nom : chaque fin de morceau, avec le
    separateur qui suit. [(debut, separateur fort ?)]"""
    out = []
    for at in range(1, len(stem)):
        here, before = stem[at], stem[at - 1]
        if here in SEPARATORS and before not in SEPARATORS:
            rest = stem[at:].lstrip("".join(SEPARATORS))
            if not rest:
                continue                 # rien ne change apres : pas un debut
            out.append((stem[:at], _strong_at(stem, at)))
    return out


def _segments(stem: str) -> list:
    """Les morceaux bornes par des separateurs forts, ou qu'ils soient dans le
    nom : « Anna - Cum Fantasy - Plage » donne « Anna », « Cum Fantasy »,
    « Plage »."""
    pieces, start, at = [], 0, 0
    while at < len(stem):
        if stem[at] in STRONG and _strong_at(stem, at):
            pieces.append(stem[start:at])
            while at < len(stem) and (stem[at] in STRONG or stem[at] == " "):
                at += 1
            start = at
            continue
        at += 1
    pieces.append(stem[start:])
    out = []
    for piece in pieces:
        text = piece.strip("".join(SEPARATORS))
        if text and text != stem.strip("".join(SEPARATORS)):
            out.append(text)
    return out


@lru_cache(maxsize=500000)
def _generic(text: str) -> bool:
    """« Part 2 », « Full HD », « Scene 01 » : des morceaux qu'on lit dans
    toutes les collections, qui n'en designent aucune."""
    words = [w for w in _WORDS.split(_fold(text)) if w]
    stop = _stop_words()
    return all(w.isdigit() or w in stop or len(w) <= 2 for w in words)


_WORDS = re.compile(r"[^0-9A-Za-z\u00c0-\u024f]+")
_TWO_LETTERS = re.compile(r"[A-Za-z\u00c0-\u024f]{2}")
_STRIP = "".join(SEPARATORS)


@lru_cache(maxsize=1)
def _stop_words() -> frozenset:
    from .tagging import STOP_WORDS
    return frozenset(STOP_WORDS)


@lru_cache(maxsize=500000)
def _meaningful(prefix: str, minimum: int) -> bool:
    text = prefix.strip(_STRIP)
    if len(text) < minimum or not _TWO_LETTERS.search(text):
        return False
    letters = sum(map(str.isalpha, text))
    if letters < len(text.replace(" ", "")) / 2:
        return False                     # surtout des chiffres : une date, un numero
    return not _CAMERA.match(text)


def folder_name(prefix: str) -> str:
    """Le nom de dossier tire d'un debut commun, sans ce que Windows refuse."""
    name = prefix.strip("".join(SEPARATORS))
    name = re.sub(r'[<>:"/\\|?*]', " ", name)
    return " ".join(name.split()).rstrip(". ") or "Collection"


def _fold(text: str) -> str:
    if text.isascii():
        return text.lower()             # le cas courant, et bien plus vite
    import unicodedata
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def find_groups(paths: list, grain: str = "fin", min_size: int = 3) -> list:
    """Les groupes de fichiers d'une meme collection, du plus fourni au moins :
    [{"name", "prefix", "videos": [chemins], "folders": nombre de dossiers}].

    Un fichier ne va que dans un groupe. Les groupes deja reunis dans un
    dossier qui porte leur nom ne sont pas proposes : il n'y a rien a faire."""
    grain = grain if grain in GRAINS else "fin"
    minimum = MIN_CHARS[grain]
    paths = [str(p) for p in paths]
    stems = [Path(p).stem for p in paths]
    owners: dict = defaultdict(set)
    cuts_of = []
    strip = "".join(SEPARATORS)
    for at, stem in enumerate(stems):
        found: dict = {}
        # Les debuts du nom, puis les morceaux du milieu : le meme texte, d'ou
        # qu'il vienne, designe la meme collection.
        for prefix, strong in _cuts(stem):
            key = prefix.strip(strip)
            if _meaningful(key, minimum) and not _generic(key):
                found[key] = found.get(key, False) or strong
        for piece in _segments(stem):
            if _meaningful(piece, minimum) and not _generic(piece):
                found[piece] = True
        cuts_of.append(found)
        for key in found:
            owners[key].add(at)
    chosen: dict = defaultdict(list)
    for at, found in enumerate(cuts_of):
        shared = [(key, strong) for key, strong in found.items() if len(owners[key]) >= min_size]
        if not shared:
            continue
        shared.sort(key=lambda pair: (len(pair[0]), pair[0]))
        if grain == "large":
            key = shared[0][0]
        elif grain == "tres_fin":
            key = shared[-1][0]
        else:
            # Le morceau qui reunit le plus de fichiers : la collection, et non
            # un titre qu'elle partage par hasard avec deux ou trois autres.
            strong = [k for k, s in shared if s] or [k for k, _s in shared]
            # A egalite, le debut du nom : c'est la qu'est presque toujours
            # le nom de la collection.
            stem = stems[at]
            key = max(strong, key=lambda k: (len(owners[k]), stem.startswith(k), len(k)))
        chosen[key].append(at)
    groups = []
    for prefix, members in chosen.items():
        if len(members) < min_size:
            continue
        videos = sorted((paths[i] for i in members), key=lambda p: Path(p).name.lower())
        parents = {os.path.dirname(v) for v in videos}
        name = folder_name(prefix)
        if len(parents) == 1 and _fold(name) in _fold(os.path.basename(next(iter(parents)))):
            continue                     # deja reunis, dans un dossier a leur nom
        groups.append({"name": name, "prefix": prefix, "videos": videos,
                       "folders": len(parents)})
    groups.sort(key=lambda g: (-len(g["videos"]), g["name"].lower()))
    return groups


def home_of(videos: list) -> Path:
    """Ou creer le dossier : la ou se trouvent la plupart de ces fichiers."""
    counts: dict = defaultdict(int)
    for video in videos:
        counts[os.path.dirname(str(video))] += 1
    return Path(max(counts, key=lambda d: (counts[d], -len(d))))
