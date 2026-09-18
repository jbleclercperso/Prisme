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

from .scan import MODE_FOLDERS, Item


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

    items = []
    for term, _needle in folded:
        found = buckets[term]
        if not found:
            continue
        item = Item(path=Path(term), kind=MODE_FOLDERS, videos=found,
                    video_count=len(found), file_count=len(found))
        item.is_tag = True
        for video in found:
            try:
                item.size += video.stat().st_size
            except OSError:
                pass
        items.append(item)
    return items
