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
    """Une catégorie par mot-clé, **chaque vidéo n'allant que dans une seule**.

    C'est tout l'écart avec la version précédente, qui rangeait une vidéo dans
    chacun des mots que son nom contenait : les mots fréquents se recouvraient
    presque entièrement, et l'on ouvrait dix catégories pour y retrouver les
    dix mêmes vidéos. Une vidéo va donc au mot qui la décrit le mieux — le plus
    fréquent de ceux qu'elle porte, le plus long à égalité — et les catégories
    deviennent des parts, pas des points de vue.

    Elles sortent de la plus fournie à la plus rare : c'est l'ordre dans lequel
    on veut les parcourir. La comparaison ignore casse et accents, « Été »,
    « ete » et « ETE » tombent dans la même.
    """
    if not tags or not videos:
        return []

    folded = []
    seen = set()
    for term in tags:
        needle = fold(term.strip())
        if not needle or needle in seen:
            continue
        seen.add(needle)
        folded.append((term.strip(), needle))
    if not folded:
        return []

    # Premier passage : qui porte quoi. On garde les correspondances plutot que
    # de refaire le test, un nom etant relu autant de fois qu'il y a de mots.
    carried: list = []
    counts: dict = {needle: 0 for _term, needle in folded}
    for video in videos:
        name = fold(Path(video).name)
        hits = [needle for _term, needle in folded if needle in name]
        if not hits:
            continue
        carried.append((video, hits))
        for needle in hits:
            counts[needle] += 1

    # Second passage : chacun choisit sa categorie, celle qui rassemble le plus.
    buckets: dict = {needle: [] for _term, needle in folded}
    for video, hits in carried:
        best = max(hits, key=lambda needle: (counts[needle], len(needle)))
        buckets[best].append(Path(video))

    labels = {needle: term for term, needle in folded}
    items = []
    for needle, found in buckets.items():
        if not found:
            continue
        found.sort(key=lambda path: str(path).lower())
        item = Item(path=Path(labels[needle]), kind=MODE_FOLDERS, videos=found,
                    video_count=len(found), file_count=len(found))
        item.is_tag = True
        # La taille demanderait un `stat()` par video : sur un millier de
        # fichiers en reseau, l'ouverture de l'onglet y passait des minutes,
        # pour un chiffre que la carte n'affiche meme plus.
        items.append(item)
    items.sort(key=lambda entry: (-entry.video_count, entry.name.lower()))
    return items
