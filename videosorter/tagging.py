"""Mots-clés automatiques : des dossiers virtuels bâtis sur les noms de fichiers.

Un mot-clé n'est pas un rangement mais une vue : il rassemble les vidéos dont le
nom le comporte, où qu'elles soient. On les parcourt comme un dossier, on les
édite comme n'importe quelle vidéo, mais le mot-clé lui-même ne se déplace pas —
il n'existe pas sur le disque.

La comparaison ignore casse et accents : « Été » trouve « ete » et « ETE ».
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

import re
from collections import Counter

from .scan import MODE_FOLDERS, Item

# Mots trop courants ou trop courts pour distinguer quoi que ce soit.
STOP_WORDS = {
    "mp4", "mkv", "avi", "mov", "wmv", "webm", "video", "videos", "full",
    "hd", "sd", "new", "the", "and", "les", "des", "une", "avec", "pour",
    "part", "partie", "final", "copy", "copie", "sans", "titre",
}
MIN_WORD = 3


def top_words(videos: list, limit: int = 100, minimum: int = 2) -> list:
    """Les mots qui reviennent le plus dans les noms de fichiers.

    Sert a proposer des categories sans rien saisir : un mot present dans
    des centaines de noms designe presque toujours quelque chose.
    """
    counts = Counter()
    for video in videos:
        stem = Path(video).stem
        for word in re.split(r"[^0-9a-zA-Z\u00c0-\u024f]+", stem):
            folded = fold(word)
            if len(folded) < MIN_WORD or folded in STOP_WORDS:
                continue
            if folded.isdigit():
                continue
            counts[folded] += 1
    return [word for word, n in counts.most_common(limit) if n >= minimum]


def fold(text: str) -> str:
    """Ramène un texte à une forme comparable : sans accents ni majuscules."""
    stripped = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in stripped if not unicodedata.combining(c))
    return without_marks.casefold()


def matches(term: str, path) -> bool:
    """Vrai si le nom du fichier comporte le terme, accents et casse ignorés."""
    return fold(term) in fold(Path(path).name)


def build_tag_items(tags: list, videos: list) -> list:
    """Un élément par mot-clé trouvant au moins une vidéo, dans l'ordre saisi.

    Les vidéos sans mot-clé ne sont pas perdues : elles restent accessibles par
    les dossiers ordinaires, et un mot-clé sans correspondance est simplement
    omis plutôt que d'afficher une carte vide.
    """
    if not tags or not videos:
        return []

    folded = [(term, fold(term)) for term in tags if term.strip()]
    buckets: dict = {term: [] for term, _ in folded}
    for video in videos:
        name = fold(Path(video).name)
        for term, needle in folded:
            if needle and needle in name:
                buckets[term].append(Path(video))

    # Une meme video appartient souvent a plusieurs mots : sans ce cache,
    # cent mots-cles relisaient cent fois la taille de chaque fichier, ce
    # qui se paie tres cher sur un disque reseau.
    sizes: dict = {}

    def size_of(video) -> int:
        if video not in sizes:
            try:
                sizes[video] = video.stat().st_size
            except OSError:
                sizes[video] = 0
        return sizes[video]

    items = []
    for term, _needle in folded:
        found = buckets[term]
        if not found:
            continue
        item = Item(path=Path(term), kind=MODE_FOLDERS, videos=found,
                    video_count=len(found), file_count=len(found))
        item.is_tag = True
        item.size = sum(size_of(video) for video in found)
        items.append(item)
    return items
