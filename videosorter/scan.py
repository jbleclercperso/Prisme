"""Analyse du dossier racine : construction de la liste des éléments à trier."""
from __future__ import annotations

import os
import sys
from functools import cached_property
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .config import VIDEO_EXTS
from .stamps import remember as remember_stamp


# ---------------------------------------------------------------------------
# Des chemins sans repasser par pathlib
#
# `Path(texte)` ne coute presque rien, mais le premier `str()` redecoupe et
# recompose tout le chemin : pour les cent mille videos de la racine, une a
# trois secondes sur le fil de l'interface, a chaque ouverture. Les chemins
# que rendent l'enumeration du disque et l'index sont deja sous leur forme
# definitive : on les confie tels quels a l'objet, qui n'aura plus rien a
# recalculer pour les afficher, les comparer ou les hacher.
# ---------------------------------------------------------------------------
_PATH_CLASS = type(Path())
_SEP = os.sep
_ALT = os.altsep


def _plain(text: str) -> bool:
    """Vrai si pathlib rendrait ce texte exactement tel quel."""
    if not text or text.endswith(_SEP) or text.startswith(_SEP + _SEP):
        return False                    # racine, chemin reseau : on laisse faire
    if _ALT and _ALT in text:
        return False
    if _SEP + _SEP in text or _SEP + "." + _SEP in text:
        return False
    return not (text.endswith(_SEP + ".") or text.startswith("." + _SEP))


def _made_whole(text: str) -> Path:
    path = object.__new__(_PATH_CLASS)
    path._raw_paths = [text]
    path._str = text
    return path


def _fast_works() -> bool:
    """Verifie, une fois, que la fabrication directe donne le meme objet.

    Elle s'appuie sur la facon dont pathlib range un chemin : une autre
    version de Python pourrait la changer. On retombe alors sur `Path()`,
    plus lent mais toujours juste.
    """
    sample = "C:\\Dossier\\sous\\clip.mp4" if _SEP == "\\" else "/Dossier/sous/clip.mp4"
    try:
        made, real = _made_whole(sample), Path(sample)
        return (str(made) == str(real) and made == real
                and hash(made) == hash(real) and made.name == real.name
                and made.parent == real.parent and made.parts == real.parts
                and made.suffix == real.suffix)
    except Exception:                                   # noqa: BLE001
        return False


_FAST = _fast_works()


def fast_path(text: str) -> Path:
    """`Path(text)`, sans le cout du premier `str()` quand le texte est deja net."""
    if _FAST and _plain(text):
        return _made_whole(text)
    return Path(text)


# L'index, retenu une fois : `from .index import INDEX` a chaque appel passait
# par le crochet d'import de PySide6, et `known_media` est appele cent mille
# fois par tri.
_INDEX = None


def _index():
    global _INDEX
    if _INDEX is None:
        from .index import INDEX
        _INDEX = INDEX
    return _INDEX

MODE_FOLDERS = "folders"   # les sous-dossiers, un par un
MODE_FILES = "files"       # les videos posees directement dans la racine
MODE_FLAT = "flat"         # toutes les videos de l'arborescence, sans leurs dossiers

# Un dossier ainsi prefixe est un rayonnage : on le traverse au lieu de le trier.
PARENT_PREFIX = "+"
LOOSE_LABEL = "(sans dossier)"

# Au-delà, on arrête de collecter les chemins de vidéos d'un même dossier :
# dix aperçus n'en demandent pas plus et cela borne la mémoire sur les gros lots.
# Toutes les videos d'un dossier, et non les quatre cents premieres : le
# plafond amputait l'onglet Videos, le hasard et les mots frequents de tout
# ce qui depassait, dans les gros dossiers — c'est-a-dire l'essentiel.
MAX_VIDEOS_PER_ITEM = 10_000_000
# Ce que les versions plafonnees ont laisse dans l'index.
OLD_CAP = 400
# L'empreinte d'un element lu a moitie : elle ne correspond a aucune date, il
# sera donc relu au passage suivant au lieu d'etre repris tel quel.
INCOMPLETE_SIG = "incomplet"


@dataclass
class Item:
    """Un élément à trier : soit un sous-dossier, soit un fichier vidéo."""

    path: Path
    kind: str                       # MODE_FOLDERS | MODE_FILES
    size: int = 0
    file_count: int = 0
    video_count: int = 0
    subdir_count: int = 0
    mtime: float = 0.0
    videos: list = field(default_factory=list)
    info: dict = field(default_factory=dict)   # rempli par ffprobe, mode fichier
    # "", "skipped", "moved", "deleted", ou un etat "pending_*" le temps que le
    # transfert en tache de fond se termine.
    status: str = ""
    status_detail: str = ""
    # Vrai pour l'entree qui ne porte que les videos en vrac d'un rayonnage :
    # elle se trie, mais ne represente pas le dossier lui-meme.
    loose_only: bool = False
    # Vrai pour un dossier virtuel batit sur un mot-cle : il n'existe pas sur
    # le disque, on le parcourt mais on ne le deplace pas.
    is_tag: bool = False
    # Un sous-dossier n'a pas pu etre lu : les comptes sont en dessous de la
    # verite. Ils ne doivent ni rassurer la garde de suppression, ni rester
    # en l'etat dans l'index -- on relira.
    incomplete: bool = False
    # Le dossier lui-meme n'a pas pu etre lu (coupure, NAS endormi) : on ne
    # sait rien de neuf, et l'on garde ce qu'on savait.
    unreadable: bool = False

    @property
    def name(self) -> str:
        if self.is_tag:
            return f"# {self.path.name}"
        if self.loose_only:
            return f"{self.path.name} {LOOSE_LABEL}"
        return self.path.name

    @cached_property
    def sort_name(self) -> str:
        """Le nom en minuscules, pour trier : calcule une fois."""
        return self.name.lower()

    @property
    def item_id(self) -> str:
        if self.is_tag:
            return f"#{self.path}"
        return f"{self.path}|vrac" if self.loose_only else str(self.path)

    @property
    def processed(self) -> bool:
        return self.status in ("moved", "deleted")

    @property
    def pending(self) -> bool:
        """Un transfert le concernant est encore en vol."""
        return self.status.startswith("pending_")

    @property
    def locked(self) -> bool:
        """Ni relire ses apercus, ni agir dessus : il part ou il est deja parti."""
        return self.processed or self.pending

    @property
    def categorized(self) -> bool:
        """Vrai si l element vit deja dans un dossier de tete, donc range."""
        return self.path.parent.name.startswith(PARENT_PREFIX)

    @property
    def movable(self) -> bool:
        """Un mot-cle et une entree « en vrac » sont des vues, non des rangements."""
        return not (self.is_tag or self.loose_only)


def known_media(item) -> tuple:
    """(durée totale connue, hauteur maximale connue) d'après le cache de sondage.

    Un dossier n'est jamais sondé en entier : on ne connaît que les vidéos déjà
    regardées pour un aperçu. Les filtres chiffrés s'appuient donc sur ce qu'on
    sait, et laissent passer ce dont on ne sait rien plutôt que de le masquer.
    """
    probes = _index().probes.get
    total = 0.0
    height = 0
    known = 0
    for video in item.videos:
        found = probes(str(video))
        info = found[1] if found is not None else None
        if not info:
            continue
        known += 1
        total += info.get("duration") or 0.0
        height = max(height, info.get("height") or 0)
    if not known:
        return (0.0, 0)
    # Extrapole la durée du dossier à partir de l'échantillon connu.
    if item.kind == MODE_FOLDERS and known < len(item.videos):
        total = total / known * len(item.videos)
    return (total, height)


def human_size(num: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if num < 1024 or unit == "To":
            return f"{num:.0f} {unit}" if unit == "o" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} To"


def human_duration(seconds: float) -> str:
    seconds = int(seconds or 0)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# Hauteurs normalisees, pour nommer une resolution plutot que la decrire.
STANDARD_HEIGHTS = (144, 240, 360, 480, 576, 720, 1080, 1440)


def human_resolution(height: int) -> str:
    """Traduit une hauteur d'image en appellation courante : 720p, 1080p, 4K."""
    if not height:
        return ""
    if height >= 4320:
        return "8K"
    if height >= 2160:
        return "4K"
    closest = min(STANDARD_HEIGHTS, key=lambda standard: abs(standard - height))
    return f"{closest}p"


def _stamp(entry) -> int:
    """Date de modification rapportee par l enumeration, ou 0.

    `DirEntry.stat()` ne redemande rien au disque : l enumeration d un
    repertoire a deja rapporte les dates de toutes ses entrees. Sur un partage
    reseau, c est la difference entre une lecture et plusieurs centaines — 79 ms
    chacune sur le NAS de mesure, soit trois quarts de minute pour six cents
    dossiers.

    Mais sous NTFS, la date qu un repertoire rapporte de ses sous-repertoires
    retarde sur la realite : un fichier ajoute a l instant peut n y apparaitre
    que plus tard. On ne s en sert donc qu ou elle est a la fois fiable et
    rentable — sur un volume reseau, dont le serveur tient ses dates a jour et
    ou chaque lecture supplementaire se paie cher. En local, on redemande.
    """
    try:
        return entry.stat(follow_symlinks=False).st_mtime_ns
    except OSError:
        return 0


def _stamps_are_trustworthy(root) -> bool:
    """Vrai la ou relever les dates au passage vaut mieux que les redemander."""
    from .media import is_network_path
    return is_network_path(root)


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


# Des dossiers qu'on ne veut simplement pas voir — ni leurs videos, ni leurs
# vignettes, ni leurs mots. Ce ne sont pas des dossiers caches au sens de
# Windows : c'est un choix, et il se defait d'un interrupteur.
VEILED = {"bin"}
SHOW_VEILED = False


def veiled(name: str) -> bool:
    """Ce nom est-il de ceux qu'on masque ? La casse n'y change rien."""
    return not SHOW_VEILED and str(name).casefold() in VEILED


# La reponse, par dossier. Cent mille videos tiennent dans quelques
# centaines de dossiers : on decide une fois pour chacun, et non a chaque
# video — decomposer chaque chemin coutait trois secondes par clic de filtre.
_VEIL_MEMO: dict = {}


def under_veiled(path) -> bool:
    """Ce chemin traverse-t-il un dossier masque ?

    L'index garde ce qu'il a vu autrefois : masquer a l'enumeration ne suffit
    donc pas, il faut aussi ecarter a l'affichage ce qui y dort deja.
    """
    if SHOW_VEILED or not VEILED:
        return False
    text = str(path)
    cut = max(text.rfind("\\"), text.rfind("/"))
    folder, name = (text[:cut], text[cut + 1:]) if cut >= 0 else ("", text)
    hit = _VEIL_MEMO.get(folder)
    if hit is None:
        low = folder.casefold()
        # Le plus souvent, aucun nom masque n'apparait meme en sous-chaine :
        # on le sait sans rien decouper.
        if not any(name_ in low for name_ in VEILED):
            hit = False
        else:
            hit = any(part in VEILED
                      for part in low.replace("/", "\\").split("\\"))
        if len(_VEIL_MEMO) > 200_000:
            _VEIL_MEMO.clear()
        _VEIL_MEMO[folder] = hit
    return hit or name.casefold() in VEILED


def set_veiled(names, show: bool) -> None:
    """Pose la liste et l'interrupteur, d'un seul geste."""
    global SHOW_VEILED
    _VEIL_MEMO.clear()
    VEILED.clear()
    VEILED.update(str(name).casefold() for name in (names or []) if str(name).strip())
    SHOW_VEILED = bool(show)


def _is_hidden(entry) -> bool:
    """Teste l'attribut caché, en préférant les données déjà lues par scandir.

    Sur un partage réseau, chaque `stat()` supplémentaire est un aller-retour :
    l'énumération d'un répertoire rapporte déjà les attributs de ses entrées,
    autant s'en servir plutôt que d'interroger le serveur une fois par élément.
    """
    name = entry.name if hasattr(entry, "name") else Path(entry).name
    if name.startswith("."):
        return True
    # Le voile passe par ici : c'est le seul point que tous les parcours
    # traversent — l'analyse, la preparation des vignettes, l'audit, les
    # doublons, les empreintes, les plans, les titres.
    if veiled(name):
        return True
    try:
        stat_result = entry.stat(follow_symlinks=False) if hasattr(entry, "stat")             else Path(entry).stat()
        # FILE_ATTRIBUTE_HIDDEN (0x2) | FILE_ATTRIBUTE_SYSTEM (0x4)
        return bool(stat_result.st_file_attributes & 0x6)
    except (OSError, AttributeError, TypeError):
        return False


def scan_folder(folder: Path) -> Item:
    """Parcourt récursivement un dossier et en agrège les statistiques.

    Écrit avec `os.scandir` et non `os.walk` : sous Windows, l'énumération d'un
    répertoire rapporte déjà taille et type de chaque entrée, et `DirEntry.stat()`
    se sert de ces données au lieu d'interroger le disque une seconde fois. Les
    chemins restent des chaînes tant que possible, `pathlib` coûtant cher quand
    on l'invoque des dizaines de milliers de fois.

    Une lecture qui échoue n'est plus avalée en silence : un sous-dossier
    illisible marque l'élément `incomplete` (ses comptes sont trop bas), le
    dossier lui-même illisible le marque `unreadable`. Sans cela, une coupure
    pendant l'analyse enregistrait des dossiers pleins à zéro vidéo, pour
    toujours, et la garde de suppression croyait un dossier « 100 % vidéo ».
    """
    item = Item(path=folder, kind=MODE_FOLDERS)
    try:
        item.mtime = folder.stat().st_mtime
    except OSError:
        pass

    size = 0
    file_count = 0
    video_count = 0
    subdir_count = 0
    videos: list = []

    stack = [str(folder)]
    first = True
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            if first:
                item.unreadable = True
            else:
                item.incomplete = True
            first = False
            continue
        first = False
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdir_count += 1
                    stack.append(entry.path)
                    continue
                file_count += 1
                size += entry.stat(follow_symlinks=False).st_size
            except OSError:
                item.incomplete = True
                continue
            dot = entry.name.rfind(".")
            if dot > 0 and entry.name[dot:].lower() in VIDEO_EXTS:
                video_count += 1
                if len(videos) < MAX_VIDEOS_PER_ITEM:
                    videos.append(entry.path)
                    # L'enumeration vient de lire taille et date : les retenir
                    # epargne autant de lectures reseau a l'affichage.
                    try:
                        st = entry.stat(follow_symlinks=False)
                        remember_stamp(entry.path, st.st_size, st.st_mtime)
                    except OSError:
                        pass

    item.size = size
    item.file_count = file_count
    item.video_count = video_count
    item.subdir_count = subdir_count
    videos.sort(key=str.lower)
    item.videos = [fast_path(path) for path in videos]
    return item


def scan_file(path: Path, stat_pair=None) -> Item:
    """Un element pour cette video.

    `stat_pair`, quand on l'a, est le (taille, date) deja rapporte par
    l'enumeration du dossier : redemander au disque ce qu'il vient de dire
    coutait une lecture reseau par video, soit des milliers en vue a plat.
    """
    item = Item(path=path, kind=MODE_FILES, file_count=1, video_count=1, videos=[path])
    if stat_pair:
        item.size, item.mtime = stat_pair
        return item
    try:
        st = path.stat()
        item.size = st.st_size
        item.mtime = st.st_mtime
    except OSError:
        pass
    return item


def file_signature(item) -> str:
    """Empreinte d'une video : sa taille et sa date. Elle ne bouge qu'a l'edition."""
    return f"{item.size}|{int(item.mtime)}"


def scan_loose(folder: Path, skip_hidden: bool = True) -> Item:
    """Entrée représentant les seules vidéos en vrac d'un rayonnage.

    Les tailles viennent de l'énumération elle-même : un `stat()` par vidéo
    coûtait un aller-retour réseau chacune.
    """
    try:
        found = _loose_entries(folder, skip_hidden)
        unreadable = False
    except OSError:
        found, unreadable = [], True
    videos = [fast_path(path) for path, _size in found]
    item = Item(path=folder, kind=MODE_FOLDERS, videos=videos,
                video_count=len(videos), file_count=len(videos))
    item.loose_only = True
    item.unreadable = unreadable
    item.size = sum(size for _path, size in found)
    try:
        item.mtime = folder.stat().st_mtime
    except OSError:
        pass
    return item


def detect_mode(root: Path, skip_hidden: bool = True) -> str:
    """Dossiers à l'intérieur -> mode dossier, sinon mode fichier."""
    try:
        for entry in os.scandir(root):
            if entry.is_dir(follow_symlinks=False):
                if skip_hidden and _is_hidden(entry):
                    continue
                return MODE_FOLDERS
    except OSError:
        pass
    return MODE_FILES


def walk_videos(root: Path, skip_hidden: bool = True):
    """Rend les vidéos de l'arborescence une par une, sans rien accumuler.

    La version qui rend une liste doit tout parcourir avant de rendre la main :
    sur une collection de cent mille fichiers, c'est plusieurs minutes pendant
    lesquelles l'appelant ne peut rien annoncer.
    """
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not (skip_hidden and _is_hidden(entry)):
                        stack.append(entry.path)
                    continue
                if skip_hidden and _is_hidden(entry):
                    continue
            except OSError:
                continue
            dot = entry.name.rfind(".")
            if dot > 0 and entry.name[dot:].lower() in VIDEO_EXTS:
                yield Path(entry.path)


class RootUnreadable(OSError):
    """La racine elle-meme n'a pas pu etre lue : rien ne dit qu'elle est vide.

    Rendre une liste vide, comme autrefois, revenait a annoncer « 0 element » :
    un seul hoquet reseau vidait la planche, la composition de la racine en
    index et le compteur de la collection.
    """


def list_all_videos(root: Path, skip_hidden: bool = True, limit: int = 50000,
                    stamps: dict | None = None, strict: bool = False) -> list:
    """Toutes les vidéos de l'arborescence, à plat, quel que soit leur dossier.

    C'est la vue qu'on veut pour chercher par nom dans toute une collection :
    les dossiers n'y sont qu'un détail de rangement.

    `stamps`, s'il est fourni, recueille au passage le (taille, date) de chaque
    vidéo : l'énumération les rapporte déjà, les redemander ensuite ferait une
    lecture réseau par fichier.

    `strict` : une racine illisible lève `RootUnreadable` au lieu de passer
    pour vide.
    """
    found = []
    top = str(root)
    stack = [top]
    while stack and len(found) < limit:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            if strict and current is top:
                raise RootUnreadable(f"{root} : {exc}") from exc
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if not (skip_hidden and _is_hidden(entry)):
                        stack.append(entry.path)
                    continue
            except OSError:
                continue
            dot = entry.name.rfind(".")
            if dot > 0 and entry.name[dot:].lower() in VIDEO_EXTS:
                if skip_hidden and _is_hidden(entry):
                    continue
                found.append(entry.path)
                if stamps is not None:
                    try:
                        st = entry.stat(follow_symlinks=False)
                        stamps[entry.path] = (st.st_size, st.st_mtime)
                    except OSError:
                        pass
                if len(found) >= limit:
                    break
    found.sort(key=str.lower)
    return [fast_path(path) for path in found]


def is_parent_folder(path) -> bool:
    """Vrai pour un dossier de tete, que l'on traverse au lieu de le trier."""
    name = path.name if hasattr(path, "name") else Path(path).name
    return name.startswith(PARENT_PREFIX)


def _loose_entries(folder: Path, skip_hidden: bool = True) -> list:
    """(chemin, taille) des vidéos posées directement dans ce dossier.

    Lève OSError si le dossier lui-même ne se lit pas : c'est à l'appelant de
    dire s'il vaut mieux « rien » ou « on ne sait pas ».
    """
    found = []
    for entry in os.scandir(folder):
        try:
            if entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        if skip_hidden and _is_hidden(entry):
            continue
        dot = entry.name.rfind(".")
        if dot > 0 and entry.name[dot:].lower() in VIDEO_EXTS:
            try:
                st = entry.stat(follow_symlinks=False)
                size = st.st_size
                remember_stamp(entry.path, st.st_size, st.st_mtime)
            except OSError:
                size = 0
            found.append((entry.path, size))
    found.sort(key=lambda pair: pair[0].lower())
    return found


def loose_videos(folder: Path, skip_hidden: bool = True) -> list:
    """Vidéos posées directement dans ce dossier, sans sous-dossier."""
    try:
        return [fast_path(path) for path, _size in _loose_entries(folder, skip_hidden)]
    except OSError:
        return []


def _read_shelf(path: Path, skip_hidden: bool):
    """(sous-dossiers et leurs dates, vidéos en vrac ?) d'un rayonnage, ou None.

    Une seule enumeration : les videos en vrac, les sous-dossiers et leurs
    dates se lisent du meme passage. En demander plusieurs multipliait le
    temps d'ouverture sur un partage reseau, ou chaque lecture est un
    aller-retour de plusieurs dizaines de millisecondes.
    """
    children = []
    has_loose = False
    try:
        for entry in os.scandir(path):
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if skip_hidden and _is_hidden(entry):
                continue
            if is_dir:
                children.append((entry.name, _stamp(entry)))
            elif not has_loose:
                dot = entry.name.rfind(".")
                if dot > 0 and entry.name[dot:].lower() in VIDEO_EXTS:
                    has_loose = True
    except OSError:
        return None
    children.sort(key=lambda pair: pair[0].lower())
    return children, has_loose


def expand_parents(entries: list, skip_hidden: bool = True,
                   stamps: dict | None = None, cache=None,
                   unreadable: list | None = None) -> list:
    """Remplace chaque rayonnage par son contenu, en gardant l'ordre.

    Les dossiers qu'il contient prennent sa place dans la liste ; les vidéos
    posées directement dedans sont signalées par le dossier lui-même, qui reste
    en tête sous un libellé explicite au lieu de disparaître avec elles.

    Chaque rayonnage est relu à chaque fois, tous de front. Se fier à sa date
    pour reprendre la composition notée -- et les dates de ses enfants --
    laissait passer ce qui change plus bas : une vidéo ajoutée dans
    « +Rayon/Enfant » ne touche pas la date de « +Rayon », et l'enfant restait
    indéfiniment à son ancien compte. Relus en parallèle, dix rayonnages coûtent
    à peu près un seul aller-retour.

    La composition notée ne sert plus qu'au secours : un rayonnage qui ne
    répond pas garde ses enfants connus au lieu de les voir disparaître. S'il
    n'en a pas, il est ajouté à `unreadable` : la liste est incomplète.
    """
    shelves = [path for path in entries if is_parent_folder(path)]
    read: dict = {}
    if len(shelves) == 1:
        read[shelves[0]] = _read_shelf(shelves[0], skip_hidden)
    elif shelves:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(shelves))) as pool:
            for path, found in zip(shelves, pool.map(
                    lambda shelf: _read_shelf(shelf, skip_hidden), shelves)):
                read[path] = found

    expanded = []
    for path in entries:
        if not is_parent_folder(path):
            expanded.append(path)
            continue
        found = read.get(path)
        own = (stamps or {}).get(str(path), 0)
        if found is None:
            known = (cache.expansion(path, own, any_stamp=True)
                     if cache is not None else None)
            if known is None:
                if unreadable is not None:
                    unreadable.append(path)
                continue
            children, has_loose = known["children"], known["loose"]
        else:
            children, has_loose = found
            if cache is not None:
                cache.put_expansion(path, own, children, has_loose)
        if has_loose:
            expanded.append(path)
        for name, stamp in children:
            child = path / name
            expanded.append(child)
            if stamps is not None:
                stamps[str(child)] = stamp
    return expanded


def list_entries(root: Path, mode: str, skip_hidden: bool = True,
                 expand_parent_folders: bool = False,
                 stamps: dict | None = None, cache=None,
                 strict: bool = False, unreadable: list | None = None) -> list:
    """Liste, sans les analyser, les chemins de premier niveau à traiter.

    `stamps`, s'il est fourni, se remplit des dates de modification relevées au
    passage. Elles ne coûtent rien ici et évitent plus tard une lecture réseau
    par dossier pour savoir s'il a bougé.

    `strict` : une racine illisible lève `RootUnreadable` au lieu de rendre une
    liste vide ; `unreadable` recueille les rayonnages qui n'ont pas répondu.
    """
    if mode == MODE_FLAT:
        return list_all_videos(root, skip_hidden, stamps=stamps, strict=strict)
    entries = []
    try:
        listing = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError as exc:
        if strict:
            raise RootUnreadable(f"{root} : {exc}") from exc
        listing = []
    for entry in listing:
        try:
            if skip_hidden and _is_hidden(entry):
                continue
            if mode == MODE_FOLDERS and entry.is_dir(follow_symlinks=False):
                entries.append(fast_path(entry.path))
                if stamps is not None:
                    stamps[entry.path] = _stamp(entry)
            elif mode == MODE_FILES and entry.is_file():
                dot = entry.name.rfind(".")
                if dot > 0 and entry.name[dot:].lower() in VIDEO_EXTS:
                    entries.append(fast_path(entry.path))
                    if stamps is not None:
                        try:
                            st = entry.stat(follow_symlinks=False)
                            stamps[entry.path] = (st.st_size, st.st_mtime)
                        except OSError:
                            pass
        except OSError as exc:
            if strict:
                raise RootUnreadable(f"{root} : {exc}") from exc
            continue
    if mode == MODE_FOLDERS:
        if expand_parent_folders:
            entries = expand_parents(entries, skip_hidden, stamps, cache,
                                     unreadable)
        else:
            # Un dossier de tete est une destination, pas quelque chose a trier :
            # c'est la qu'on range les autres. Le laisser dans la liste revenait
            # a proposer de ranger le rangement.
            entries = [path for path in entries if not is_parent_folder(path)]
    return entries


def canon_root(path) -> Path:
    """Une seule ecriture pour un meme dossier racine.

    Les cles de l'index, des favoris et des vignettes sont les chemins tels
    quels : une racine choisie par « Réseau » (\\\\serveur\\partage) plutot que
    par sa lettre (X:) faisait tout « perdre », et relire la collection. On
    rend la lettre quand le partage est monte, et une forme normalisee sinon.
    Rien n'est lu sur le partage : seule la table des lecteurs montes l'est.
    """
    text = os.path.normpath(str(path))
    if sys.platform == "win32" and text.startswith("\\\\"):
        mapped = _drive_for_unc(text)
        if mapped:
            text = mapped
    if len(text) == 2 and text[1] == ":":
        text += "\\"
    return Path(text)


def _drive_for_unc(text: str) -> str:
    """X:\\... pour un \\\\serveur\\partage\\... monte sous une lettre, sinon ""."""
    try:
        import ctypes
        from ctypes import wintypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        connection = ctypes.WinDLL("mpr").WNetGetConnectionW
    except (OSError, AttributeError):
        return ""
    low = text.casefold()
    for index in range(26):
        if not mask & (1 << index):
            continue
        letter = chr(ord("A") + index)
        buffer = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        # 1201 : lecteur deconnecte pour l'instant, mais toujours monte.
        if connection(f"{letter}:", buffer, ctypes.byref(size)) not in (0, 1201):
            continue
        remote = buffer.value.rstrip("\\")
        if not remote:
            continue
        folded = remote.casefold()
        if low == folded or low.startswith(folded + "\\"):
            return f"{letter}:" + (text[len(remote):] or "\\")
    return ""


def signature(folder: Path, stamp: int | None = None) -> str:
    """Empreinte d'un dossier : sa date de modification, ou "" s'il est illisible.

    Lue en nanosecondes : arrondies à la seconde, deux modifications rapprochées
    donneraient la même empreinte et la seconde passerait inaperçue.

    `stamp` est la date déjà relevée en énumérant le dossier parent. Quand on
    l'a, on s'en sert : la redemander coûte un aller-retour réseau par dossier.

    Elle couvre ce qui compte ici — un fichier ajouté, retiré ou renommé dans le
    dossier, ce que fait l'application elle-même en rangeant. Un changement
    survenu plus profond passe inaperçu jusqu'à une relecture forcée (Ctrl+R).
    """
    if stamp:
        return str(stamp)
    try:
        return str(folder.stat().st_mtime_ns)
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Le moteur, en deux temps
#
# 1. `cached_items` rend instantanement ce qu'on savait de cette racine au
#    dernier passage. Aucun acces disque : la liste est a l'ecran avant que
#    l'oeil ait fini de s'y poser.
# 2. `RefreshThread` relit le disque derriere, compare, et ne publie que les
#    differences. Un dossier qui n'a pas bouge ne coute rien : ni parcours, ni
#    signal, ni repeinte.
#
# L'ancien moteur faisait les deux d'un bloc, avant tout affichage, et
# n'enregistrait son travail qu'a la toute fin : une analyse interrompue —
# fermer la fenetre, entrer dans un dossier, changer d'onglet — jetait tout, si
# bien que chaque lancement repayait le parcours complet.
# ---------------------------------------------------------------------------


def item_id_for(path: Path, mode: str, expand: bool) -> str:
    """Identifiant d'un element, deduit de son chemin sans rien lire."""
    if mode == MODE_FOLDERS and expand and is_parent_folder(path):
        return f"{path}|vrac"
    return str(path)


def listing_key(mode: str, expand: bool) -> str:
    """Cle de composition : traverser les dossiers de tete donne une autre liste."""
    return f"{mode}+" if expand and mode == MODE_FOLDERS else mode


def cached_items(root: Path, mode: str, expand: bool = False) -> list:
    """Ce qu'on savait de cette racine, sans toucher au disque.

    Rend la liste telle qu'elle etait au dernier passage, dans l'ordre. Ce qui a
    bouge depuis sera corrige par la reconciliation ; presenter d'abord une
    collection presque juste vaut mieux que de n'en presenter aucune pendant
    une minute et demie.
    """
    from .index import INDEX
    ids = INDEX.listing(root, listing_key(mode, expand))
    if not ids:
        return []
    known = INDEX.folders(ids)
    return [known[key] for key in ids if key in known]


class RefreshThread(QThread):
    """Relit le disque en tache de fond et ne publie que ce qui a change."""

    progress = Signal(int, int, str)       # verifies, total, en cours
    # ajoutes [Item], remplaces [Item], retires [item_id]
    patch = Signal(list, list, list)
    finished_scan = Signal(str, int)       # mode, total
    # La racine n'a pas repondu (NAS endormi, coupure) : rien n'a ete efface,
    # la liste affichee est celle du dernier passage. Emis juste avant
    # `finished_scan`, qui annonce alors le total deja connu.
    unreachable = Signal(str)

    # Les elements partent par paquets : une mise a jour d'interface par
    # element coutait plus cher que l'analyse elle-meme sur un gros dossier.
    BATCH_SIZE = 40
    BATCH_DELAY = 0.12                     # secondes

    def __init__(self, root: Path, mode: str = "", skip_hidden: bool = True,
                 use_cache: bool = True, expand_parents: bool = False,
                 known_ids: list | None = None, force: bool = False,
                 parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.mode = mode
        self.skip_hidden = skip_hidden
        self.use_cache = use_cache
        # Ctrl+R : tout reparcourir sans se fier aux empreintes — mais en
        # reecrivant l'index au passage. L'ancienne version le court-circuitait
        # aussi en ecriture, si bien qu'une relecture forcee laissait la
        # collection aussi inconnue qu'avant.
        self.force = force
        self.expand_parents = expand_parents
        # Ce que la fenetre affiche deja : tout le reste est une nouveaute.
        self.known_ids = list(known_ids or [])
        self.reused = 0        # elements laisses tels quels
        self.rescanned = 0     # elements qu il a fallu reparcourir
        # Elements lus a moitie (sous-dossier illisible) ou pas du tout : leurs
        # comptes ne sont pas surs, l'index les relira au passage suivant.
        self.incomplete = 0
        # Pourquoi la racine n'a pas pu etre lue, quand c'est le cas : la
        # fenetre le dit au lieu d'annoncer « Analyse terminee ».
        self.failure = ""
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _workers(self) -> int:
        """Combien de dossiers parcourir de front.

        Le parcours attend le disque, pas le processeur : sur un partage reseau,
        huit attentes simultanees coutent a peu pres le temps d'une seule. C'est
        ce qui ramene un premier inventaire de six cents dossiers de
        quatre-vingts secondes a une dizaine.
        """
        from .media import is_network_path
        if is_network_path(self.root):
            return 8
        return max(2, min(6, os.cpu_count() or 4))

    def _signature_of(self, path: Path, key: str, mode: str, stamps) -> str:
        if mode != MODE_FOLDERS:
            pair = stamps.get(str(path)) if stamps else None
            return f"{pair[0]}|{int(pair[1])}" if pair else ""
        if not self.use_cache:
            return ""
        return signature(path, stamps.get(str(path)) if stamps else None)

    def run(self) -> None:
        import time as _time
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from .index import INDEX

        mode = self.mode or detect_mode(self.root, self.skip_hidden)
        # Les dates relevees pendant l'enumeration evitent, plus bas, une lecture
        # reseau par element. Pour un fichier elles sont toujours exactes ; pour
        # un repertoire, NTFS les laisse retarder, on ne s'y fie donc que sur un
        # volume reseau, dont le serveur les tient a jour.
        if mode == MODE_FOLDERS:
            stamps = {} if _stamps_are_trustworthy(self.root) else None
        else:
            stamps = {}
        cache = None if self.force or not self.use_cache else INDEX
        unreadable: list = []
        try:
            paths = list_entries(self.root, mode, self.skip_hidden,
                                 self.expand_parents, stamps, cache,
                                 strict=True, unreadable=unreadable)
            ids = [item_id_for(path, mode, self.expand_parents) for path in paths]
            present = set(ids)
            gone = [key for key in self.known_ids if key not in present]
            if (len(gone) > max(20, len(self.known_ids) // 2)
                    and not self._stop):
                # La moitie de la collection qui s'evanouit d'un coup ressemble
                # plus a une lecture tronquee qu'a un grand menage : on relit
                # une fois, et l'on garde la lecture la plus complete.
                again: list = []
                second = list_entries(self.root, mode, self.skip_hidden,
                                      self.expand_parents, stamps, cache,
                                      strict=True, unreadable=again)
                if len(second) > len(paths):
                    paths, unreadable = second, again
                    ids = [item_id_for(path, mode, self.expand_parents)
                           for path in paths]
                    present = set(ids)
                    gone = [key for key in self.known_ids if key not in present]
        except RootUnreadable as exc:
            self._unreachable(mode, exc, INDEX)
            return
        if self._stop:
            INDEX.commit(force=True)
            return

        total = len(paths)
        if unreadable:
            # Un rayonnage qui n'a pas repondu n'est pas vide : ses enfants
            # restent a l'ecran, et la composition de la racine n'est pas
            # reecrite avec ce trou.
            holes = tuple(str(path) + os.sep for path in unreadable)
            gone = [key for key in gone if not key.startswith(holes)]
        elif self.use_cache:
            # La composition de la racine est notee tout de suite, avant meme de
            # verifier quoi que ce soit. Attendre la fin aurait reproduit le
            # defaut qu'on corrige : un premier inventaire interrompu aurait
            # garde ses dossiers sans que rien ne sache plus qu'ils forment
            # cette racine, et le lancement suivant serait reparti de zero.
            INDEX.put_listing(self.root, listing_key(mode, self.expand_parents), ids)

        # Ce que la liste affichee porte encore alors que le disque ne le porte
        # plus : on le retire avant meme de verifier le reste. Un dossier disparu
        # n'a pas a rester une minute a l'ecran.
        if gone:
            self.patch.emit([], [], gone)
        shown = set(self.known_ids) - set(gone)

        known_sigs = {} if cache is None else INDEX.signatures(ids)
        if known_sigs and mode == MODE_FOLDERS:
            # Un dossier retenu du temps du plafond — quatre cents videos
            # sur davantage — se relit une fois, en entier. Compte en SQL, et
            # plus du tout une fois qu'il n'en reste aucun.
            for key in INDEX.capped_ids(OLD_CAP):
                known_sigs.pop(key, None)

        # Premier tri, sans rien lire : qui peut rester en l'etat, qui doit etre
        # reparcouru. C'est ici que se joue la fluidite d'un relancement — sur
        # une collection qui n'a pas bouge, `todo` est vide et il ne se passe
        # tout simplement rien.
        todo = []          # (chemin, id, empreinte) a reparcourir
        recall = set()     # connus de l'index, mais pas encore affiches
        for path, key in zip(paths, ids):
            sig = self._signature_of(path, key, mode, stamps)
            if sig and known_sigs.get(key) == sig:
                self.reused += 1
                if key not in shown:
                    recall.add(key)
                continue
            todo.append((path, key, sig))

        # Ce qui n'a pas bouge mais n'etait pas affiche : l'index le rend sans
        # aucune lecture disque.
        if recall and not self._stop:
            # L'ordre de la racine prime sur celui de la table : on republie
            # dans la suite ou l'on trie, pas dans celle ou SQLite a repondu.
            wanted = [key for key in ids if key in recall]
            found = INDEX.folders(wanted)
            fresh = [found[key] for key in wanted if key in found]
            for start in range(0, len(fresh), self.BATCH_SIZE):
                batch = fresh[start:start + self.BATCH_SIZE]
                self.patch.emit(batch, [], [])
                shown.update(item.item_id for item in batch)
            # Celui que l'index ne sait plus rendre se reparcourt.
            for path, key in zip(paths, ids):
                if key in recall and key not in found:
                    todo.append((path, key,
                                 self._signature_of(path, key, mode, stamps)))

        done = total - len(todo)
        self.progress.emit(done, total, "")

        def work(entry):
            path, _key, _sig = entry
            if self._stop:
                return None
            if mode != MODE_FOLDERS:
                return scan_file(path, stamps.get(str(path)) if stamps else None)
            if self.expand_parents and is_parent_folder(path):
                return scan_loose(path, self.skip_hidden)
            return scan_folder(path)

        added: list = []
        replaced: list = []
        last_flush = _time.monotonic()
        last_progress = 0.0

        def flush() -> None:
            nonlocal added, replaced, last_flush
            if added or replaced:
                self.patch.emit(added, replaced, [])
                added, replaced = [], []
            last_flush = _time.monotonic()

        stopped = False
        if todo:
            pool = ThreadPoolExecutor(max_workers=self._workers())
            # Chacun est publie des qu'il est pret, et non dans l'ordre de la
            # liste. `pool.map` rendait ses resultats en rang : un seul dossier
            # lourd — une arborescence de milliers de fichiers sur le reseau —
            # retenait derriere lui tous ceux, deja termines, qui le suivaient.
            # L'avancement restait cloue sur le meme chiffre plusieurs minutes,
            # alors que huit parcours tournaient.
            pending = {pool.submit(work, entry): entry for entry in todo}
            try:
                for future in as_completed(pending):
                    if self._stop:
                        stopped = True
                        break
                    entry = pending[future]
                    try:
                        item = future.result()
                    except Exception:
                        item = None
                    if item is None:
                        stopped = True
                        break
                    key, sig = entry[1], entry[2]
                    done += 1
                    self.rescanned += 1
                    if item.unreadable:
                        # Le dossier n'a pas repondu : on ne sait rien de neuf.
                        # On garde la ligne de l'index et ce qui est affiche,
                        # plutot que d'enregistrer « zero video » pour toujours.
                        self.incomplete += 1
                        if key not in shown:
                            old = INDEX.folders([key]).get(key)
                            added.append(old or item)
                            shown.add(key)
                        continue
                    if self.use_cache:
                        if item.incomplete:
                            # Comptes trop bas : notes pour l'affichage, mais
                            # sous une empreinte qui ne correspondra jamais, pour
                            # qu'il soit relu en entier la fois suivante.
                            self.incomplete += 1
                            stored = INCOMPLETE_SIG
                        else:
                            stored = sig or (signature(Path(item.path))
                                             if mode == MODE_FOLDERS
                                             else file_signature(item))
                        INDEX.put_folder(item, stored)
                    if key in shown:
                        replaced.append(item)
                    else:
                        added.append(item)
                        shown.add(key)

                    # L'avancement est annonce au rythme de l'oeil, pas du
                    # disque : un signal par element repeignait la barre six
                    # cents fois. Le nom qui l'accompagne dit *sur quoi* on
                    # attend — sans lui, une longue lecture ne se distingue pas
                    # d'un blocage.
                    now = _time.monotonic()
                    if now - last_progress >= 0.10 or done == total:
                        self.progress.emit(done, total, item.path.name)
                        last_progress = now
                    # Le premier element part seul : on veut pouvoir trier tout
                    # de suite, sans attendre que le paquet se remplisse.
                    if (len(added) + len(replaced) >= self.BATCH_SIZE
                            or done == 1 or now - last_flush >= self.BATCH_DELAY):
                        flush()
            finally:
                # Sans `cancel_futures`, sortir du bloc attendrait les six cents
                # parcours deja soumis : fermer la fenetre prendrait une minute.
                pool.shutdown(wait=False, cancel_futures=True)

        if stopped or self._stop:
            # L'index garde ce qui a ete appris avant l'arret : c'est tout
            # l'interet d'ecrire au fil de l'eau plutot qu'a la fin.
            flush()
            INDEX.commit(force=True)
            return

        flush()
        INDEX.commit(force=True)
        self.progress.emit(total, total, "")
        self.finished_scan.emit(mode, total)

    def _unreachable(self, mode: str, exc: Exception, index) -> None:
        """La racine n'a pas repondu : ne rien effacer, et le dire.

        Autrefois une racine illisible passait pour vide : la composition en
        index etait reecrite a vide, tout l'affiche etait retire, et la
        fenetre annoncait « 0 element » -- puis, relancee sans NAS, basculait
        en vue a plat et relisait tout le partage. Ici, rien ne bouge : ni
        l'index, ni la liste affichee. La fin est annoncee avec le total deja
        connu, pour que la fenetre quitte l'etat « analyse en cours ».
        """
        self.failure = str(exc)
        index.commit(force=True)
        self.unreachable.emit(self.failure)
        known = len(self.known_ids)
        self.progress.emit(known, known, "")
        self.finished_scan.emit(mode, known)
