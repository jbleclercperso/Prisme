"""Mots-clés automatiques : des dossiers virtuels bâtis sur les noms de fichiers.

Un mot-clé n'est pas un rangement mais une vue : il rassemble les vidéos dont le
nom le comporte, où qu'elles soient. On les parcourt comme un dossier, on les
édite comme n'importe quelle vidéo, mais le mot-clé lui-même ne se déplace pas —
il n'existe pas sur le disque.

La comparaison ignore casse et accents : « Été » trouve « ete » et « ETE ».
"""
from __future__ import annotations

import os
import unicodedata
from functools import lru_cache
from pathlib import Path

import re

from PySide6.QtCore import QThread, Signal

from .scan import MODE_FOLDERS, Item

# Mots de grammaire, et jetons techniques que porte tout nom de fichier : ils
# reviennent partout, donc ne distinguent rien. Anglais et francais melanges,
# les collections l'etant aussi.
STOP_WORDS = {
    # jetons d'appareils et d'outils
    "img", "dsc", "pxl", "vid", "mvi", "gopro", "dji", "screen", "record",
    "recording", "capture", "export", "output", "render", "trim", "converted",
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


def words_of_text(text: str):
    """Les mots d'un texte quelconque — un titre de metadonnees, par exemple."""
    for chunk in _BREAK.split(text):
        for piece in _CAMEL.split(chunk):
            for word in _DIGIT.split(piece):
                if word:
                    yield word


def words_of(name: str):
    """Les mots d'un nom de fichier, un par un."""
    yield from words_of_text(Path(name).stem)


# Un mot, c'est des lettres : « s01e02 », « 4k », « 1080 » n'en sont pas.
_ALPHA = re.compile(r"^[a-z\u00df-\u00ff]+$")


def _keep(folded: str) -> bool:
    return (MIN_WORD <= len(folded) <= MAX_WORD and folded not in STOP_WORDS
            and _ALPHA.match(folded) is not None)


def word_index(videos: list, titles: dict | None = None) -> dict:
    """mot → videos qui le portent, en **un seul passage** sur les noms.

    L'ancienne version testait chaque mot contre chaque nom : cent mots sur
    cent mille noms, dix millions de comparaisons, vingt secondes d'onglet
    fige. Ici chaque nom est decoupe une fois, et chaque video va dans
    **tous** ses mots — « House » reunit bien toutes les videos qui disent
    house. Les titres des metadonnees, quand on les connait, comptent aussi :
    un nom de fichier n'est parfois qu'une suite de caracteres, et le vrai
    titre est dedans.
    """
    titles = titles or {}
    index: dict = {}
    for video in videos:
        key = str(video)
        seen = set()
        sources = [Path(key).stem]
        extra = titles.get(key)
        if extra:
            sources.append(extra)
        for source in sources:
            for word in words_of_text(source):
                folded = fold(word)
                if folded in seen or not _keep(folded):
                    continue
                seen.add(folded)
                index.setdefault(folded, []).append(video)
    return index


def _ranked(videos: list, titles: dict | None = None) -> list:
    """(mot, videos), du mot le plus porte au moins porte."""
    index = word_index(videos, titles)
    return sorted(index.items(), key=lambda pair: (-len(pair[1]), pair[0]))


def frequent_tag_items(videos: list, titles: dict | None = None,
                       limit: int = 100, minimum: int = 2) -> list:
    """Les `limit` mots les plus portes, chacun avec toutes ses videos."""
    items = []
    for word, found in _ranked(videos, titles)[:limit]:
        if len(found) < minimum:
            break
        paths = sorted((Path(video) for video in found), key=lambda p: str(p).lower())
        item = Item(path=Path(word.capitalize()), kind=MODE_FOLDERS, videos=paths,
                    video_count=len(paths), file_count=len(paths))
        item.is_tag = True
        items.append(item)
    return items


class TagsThread(QThread):
    """Calcule les mots frequents hors du fil d'interface : l'onglet reste vivant."""

    ready = Signal(list)

    def __init__(self, videos: list, titles: dict, parent=None):
        super().__init__(parent)
        self.videos = list(videos)
        # Les titres ne sont plus recopies : le calcul ne fait qu'y lire, cle
        # par cle, et la copie de dizaines de milliers de titres se faisait
        # sur le fil d'interface, a chaque visite de l'onglet.
        self.titles = titles
        # Le fil se detruit une fois fini. Rattache a la fenetre, il vivait
        # aussi longtemps qu'elle, avec sa liste de cent mille videos : plus
        # de quatre mega-octets par visite de l'onglet, jamais rendus.
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            found = frequent_tag_items(self.videos, self.titles)
        finally:
            self.videos = self.titles = None
        self.ready.emit(found)


def top_words(videos: list, limit: int = 100, minimum: int = 2) -> list:
    """Les mots qui reviennent le plus dans les noms de fichiers.

    Le meme calcul que les mots frequents de l'onglet (word_index) : il en
    existait un second, plus ancien, que seuls les essais verifiaient — ils
    validaient un algorithme que l'application n'employait plus.
    """
    return [word for word, found in _ranked(videos)[:limit]
            if len(found) >= minimum]


@lru_cache(maxsize=300_000)
def fold(text: str) -> str:
    """Ramène un texte à une forme comparable : sans accents ni majuscules."""
    stripped = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in stripped if not unicodedata.combining(c))
    return without_marks.casefold()


# Un nom qui ne dit rien : « 0x56b4787xb7 », « a8f3c2d1-9e… », « IMG_2041 »,
# « 20230512_184455 ». Aucun mot n'y range la video : il faut la regarder.
# Les memes coupures que `words_of` -- ponctuation, chiffre, majuscule qui
# suit une minuscule -- en une seule passe : on la fait sur toute la
# collection, et `words_of` + `fold` coutaient 150 µs par nom.
_WORD_RUN = re.compile(r"[A-ZÀ-Þ]+[a-zß-ÿ]*|[a-zß-ÿ]+")
_VOWELS = re.compile(r"[aeiouyà-æè-ïò-öù-ýÿ]")
_CONSONANTS = re.compile(r"[bcdfghjklmnpqrstvwxz]{5,}")


def unreadable_name(name: str) -> bool:
    """Vrai quand le nom du fichier ne porte aucun vrai mot."""
    name = str(name)
    cut = max(name.rfind("\\"), name.rfind("/"))
    stem = name[cut + 1:]
    dot = stem.rfind(".")
    if dot > 0:
        stem = stem[:dot]
    for run in _WORD_RUN.findall(stem):
        word = run.lower()
        if (MIN_WORD <= len(word) <= MAX_WORD and word not in STOP_WORDS
                and _VOWELS.search(word) and not _CONSONANTS.search(word)):
            return False
    return True


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
    work = iter_tag_items(tags, videos, minimum)
    while True:
        try:
            next(work)
        except StopIteration as done:
            return done.value


# Combien de videos entre deux pauses de `iter_tag_items` : quelques
# millisecondes de calcul.
_STRIDE = 128


def iter_tag_items(tags: list, videos: list, minimum: int = 1):
    """`build_tag_items`, par petites etapes : chaque `yield` rend la main.

    Un fil Python ne soulageait pas l'interface d'un calcul Python : il garde
    le verrou global, et la fenetre ne se repeignait qu'a la fin -- un quart
    de seconde fige pour vingt mille videos, une seconde et plus sur toute la
    collection. Mene par tranches sur le fil de l'interface, une image passe
    entre deux (`MainWindow._in_slices`). Le resultat sort par StopIteration.
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
    for at, video in enumerate(videos):
        if at % _STRIDE == 0:
            yield
        # Le nom sans fabriquer de Path : cent mille fois par calcul.
        name = fold(os.path.basename(str(video)))
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
        for at, (video, hits) in enumerate(carried):
            if at % _STRIDE == 0:
                yield
            eligible = [needle for needle in hits if needle in active]
            if not eligible:
                continue
            best = max(eligible, key=lambda needle: (counts[needle], len(needle)))
            # Le chemin tel quel s'il en est deja un : `Path(Path)` refait
            # l'objet, a chaque video.
            buckets[best].append(video if isinstance(video, Path) else Path(video))
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
        yield
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
