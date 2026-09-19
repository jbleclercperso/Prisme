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

# Mots de grammaire, et jetons techniques que porte tout nom de fichier : ils
# reviennent partout, donc ne distinguent rien. Anglais et francais melanges,
# les collections l'etant aussi.
STOP_WORDS = {
    # extensions et jargon de fichier
    "mp4", "mkv", "avi", "mov", "wmv", "webm", "flv", "mpg", "mpeg", "m4v",
    "video", "videos", "vid", "clip", "clips", "movie", "movies", "film",
    "full", "hd", "sd", "uhd", "fhd", "hq", "lq", "1080p", "720p", "480p",
    "2160p", "4k", "8k", "x264", "x265", "h264", "h265", "hevc", "avc", "aac",
    "xxx", "part", "partie", "pt", "vol", "volume", "final", "copy", "copie",
    "sans", "titre", "untitled", "new", "old", "final", "edit", "cut", "scene",
    "scenes", "www", "com", "net", "org", "mp3", "wav", "web", "dvd", "rip",
    # grammaire anglaise
    "the", "this", "that", "these", "those", "and", "but", "for", "nor", "yet",
    "with", "without", "from", "into", "onto", "upon", "over", "under", "off",
    "out", "her", "his", "its", "their", "our", "your", "you", "she", "him",
    "them", "they", "who", "whom", "what", "when", "where", "why", "how",
    "all", "any", "some", "more", "most", "very", "just", "not", "than",
    "then", "there", "here", "have", "has", "had", "was", "were", "are", "been",
    "being", "get", "got", "let", "make", "made", "one", "two", "too", "also",
    "can", "will", "would", "should", "could", "about", "after", "before",
    # grammaire francaise
    "les", "des", "une", "aux", "avec", "pour", "dans", "sur", "sous", "par",
    "que", "qui", "quoi", "dont", "elle", "ils", "elles", "lui", "leur",
    "mon", "ton", "son", "mes", "tes", "ses", "nos", "vos", "leurs", "cette",
    "ces", "cet", "celui", "celle", "ceux", "est", "sont", "etait", "etaient",
    "ete", "avoir", "etre", "fait", "faire", "plus", "moins", "tres", "tout",
    "tous", "toute", "toutes", "mais", "donc", "alors", "comme", "chez",
}

# Un mot-cle est **un mot**. En deca il ne distingue rien ; au-dela, ce n'est
# plus un mot mais un titre colle — les noms de fichiers en sont pleins, et
# c'est ce qui donnait des categories de trois ou quatre mots a la suite.
MIN_WORD = 3
MAX_WORD = 12

# En deca, ce n'est pas une categorie mais une video isolee sous un titre.
MIN_BUCKET = 2

# Un nom de fichier separe ses mots de trois facons, souvent dans le meme nom :
# par une ponctuation, par une majuscule (« BeachSunset »), ou par un chiffre
# (« s01e02 », « 4kbeach »). Ne couper qu'a la ponctuation laissait les deux
# autres formes entieres.
_BREAK = re.compile(r"[^0-9A-Za-z\u00c0-\u024f]+")
_CAMEL = re.compile(r"(?<=[a-z\u00df-\u00ff])(?=[A-Z\u00c0-\u00de])")
_DIGIT = re.compile(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)")


def words_of(name: str):
    """Les mots d'un nom de fichier, un par un."""
    for chunk in _BREAK.split(Path(name).stem):
        for piece in _CAMEL.split(chunk):
            for word in _DIGIT.split(piece):
                if word:
                    yield word


def top_words(videos: list, limit: int = 100, minimum: int = 2) -> list:
    """Les mots qui reviennent le plus dans les noms de fichiers.

    Sert a proposer des categories sans rien saisir : un mot present dans des
    centaines de noms designe presque toujours quelque chose. Un seul mot a la
    fois — pour chercher une expression de deux ou trois mots, on la saisit
    dans ses propres mots-cles, ou la recherche se fait par sous-chaine.
    """
    counts = Counter()
    for video in videos:
        seen = set()
        for word in words_of(Path(video).name):
            folded = fold(word)
            if not MIN_WORD <= len(folded) <= MAX_WORD:
                continue
            if folded in STOP_WORDS or folded.isdigit():
                continue
            # Un mot repete dans un meme nom ne vaut pas deux fichiers.
            if folded in seen:
                continue
            seen.add(folded)
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


def build_tag_items(tags: list, videos: list, minimum: int = 1) -> list:
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

    `minimum` est le nombre de vidéos en dessous duquel une catégorie se dissout.
    Il vaut un pour les mots que l'on a saisis soi-même : s'il n'y a qu'une seule
    vidéo qui porte « montagne », c'est celle-là qu'on cherchait, et la faire
    disparaître serait perdre ce qu'on venait d'écrire. Les mots tirés
    automatiquement des noms de fichiers, eux, se dissolvent plus volontiers :
    personne ne les a demandés, et une catégorie à une vidéo n'y range rien.
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
    #
    # Un mot present dans deux noms peut n'en garder qu'un, l'autre etant parti
    # vers un mot plus frequent — d'ou des categories a une seule video, qui ne
    # categorisent rien. On dissout donc les trop maigres et l'on replace leurs
    # videos sur le mot suivant, jusqu'a ce que tout ce qui reste tienne debout.
    active = {needle for _term, needle in folded}
    buckets: dict = {}
    for _ in range(8):
        buckets = {needle: [] for needle in active}
        for video, hits in carried:
            eligible = [needle for needle in hits if needle in active]
            if not eligible:
                continue
            best = max(eligible, key=lambda needle: (counts[needle], len(needle)))
            buckets[best].append(Path(video))
        thin = {needle for needle, found in buckets.items()
                if len(found) < minimum}
        if not thin or len(thin) == len(active):
            break
        active -= thin

    labels = {needle: term for term, needle in folded}
    # Les mots dissous n'ont plus de bac : rien a rendre pour eux.
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
