"""Analyse du dossier racine : construction de la liste des éléments à trier."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .config import VIDEO_EXTS
from .stamps import remember as remember_stamp

MODE_FOLDERS = "folders"   # les sous-dossiers, un par un
MODE_FILES = "files"       # les videos posees directement dans la racine
MODE_FLAT = "flat"         # toutes les videos de l'arborescence, sans leurs dossiers

# Un dossier ainsi prefixe est un rayonnage : on le traverse au lieu de le trier.
PARENT_PREFIX = "+"
LOOSE_LABEL = "(sans dossier)"

# Au-delà, on arrête de collecter les chemins de vidéos d'un même dossier :
# dix aperçus n'en demandent pas plus et cela borne la mémoire sur les gros lots.
MAX_VIDEOS_PER_ITEM = 400


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

    @property
    def name(self) -> str:
        if self.is_tag:
            return f"# {self.path.name}"
        if self.loose_only:
            return f"{self.path.name} {LOOSE_LABEL}"
        return self.path.name

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
    from .index import INDEX
    total = 0.0
    height = 0
    known = 0
    for video in item.videos:
        info = INDEX.probe(video)
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


def _is_hidden(entry) -> bool:
    """Teste l'attribut caché, en préférant les données déjà lues par scandir.

    Sur un partage réseau, chaque `stat()` supplémentaire est un aller-retour :
    l'énumération d'un répertoire rapporte déjà les attributs de ses entrées,
    autant s'en servir plutôt que d'interroger le serveur une fois par élément.
    """
    name = entry.name if hasattr(entry, "name") else Path(entry).name
    if name.startswith("."):
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
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    subdir_count += 1
                    stack.append(entry.path)
                    continue
                file_count += 1
                size += entry.stat(follow_symlinks=False).st_size
            except OSError:
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
    item.videos = [Path(path) for path in videos]
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
    """Entrée représentant les seules vidéos en vrac d'un rayonnage."""
    videos = loose_videos(folder, skip_hidden)
    item = Item(path=folder, kind=MODE_FOLDERS, videos=videos,
                video_count=len(videos), file_count=len(videos))
    item.loose_only = True
    for video in videos:
        try:
            item.size += video.stat().st_size
        except OSError:
            pass
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


def list_all_videos(root: Path, skip_hidden: bool = True, limit: int = 50000,
                    stamps: dict | None = None) -> list:
    """Toutes les vidéos de l'arborescence, à plat, quel que soit leur dossier.

    C'est la vue qu'on veut pour chercher par nom dans toute une collection :
    les dossiers n'y sont qu'un détail de rangement.

    `stamps`, s'il est fourni, recueille au passage le (taille, date) de chaque
    vidéo : l'énumération les rapporte déjà, les redemander ensuite ferait une
    lecture réseau par fichier.
    """
    found = []
    stack = [str(root)]
    while stack and len(found) < limit:
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
    return [Path(path) for path in found]


def is_parent_folder(path) -> bool:
    """Vrai pour un dossier de tete, que l'on traverse au lieu de le trier."""
    name = path.name if hasattr(path, "name") else Path(path).name
    return name.startswith(PARENT_PREFIX)


def loose_videos(folder: Path, skip_hidden: bool = True) -> list:
    """Vidéos posées directement dans ce dossier, sans sous-dossier."""
    found = []
    try:
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
                found.append(Path(entry.path))
    except OSError:
        pass
    found.sort(key=lambda path: str(path).lower())
    return found


def expand_parents(entries: list, skip_hidden: bool = True,
                   stamps: dict | None = None, cache=None) -> list:
    """Remplace chaque rayonnage par son contenu, en gardant l'ordre.

    Les dossiers qu'il contient prennent sa place dans la liste ; les vidéos
    posées directement dedans sont signalées par le dossier lui-même, qui reste
    en tête sous un libellé explicite au lieu de disparaître avec elles.
    """
    expanded = []
    for path in entries:
        if not is_parent_folder(path):
            expanded.append(path)
            continue

        own = (stamps or {}).get(str(path), 0)
        known = cache.expansion(path, own) if cache is not None else None
        if known is not None:
            # Le rayonnage n'a pas bouge : sa composition est connue, inutile de
            # redemander au reseau ce qu'on a deja note.
            if known["loose"]:
                expanded.append(path)
            for name, stamp in known["children"]:
                child = path / name
                expanded.append(child)
                if stamps is not None:
                    stamps[str(child)] = stamp
            continue

        # Une seule enumeration : les videos en vrac, les sous-dossiers et leurs
        # dates se lisent du meme passage. En demander plusieurs multipliait le
        # temps d'ouverture sur un partage reseau, ou chaque lecture est un
        # aller-retour de plusieurs dizaines de millisecondes.
        children = []
        has_loose = False
        readable = True
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
            children = []
            readable = False
        children.sort(key=lambda pair: pair[0].lower())
        if cache is not None and readable and own:
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
                 stamps: dict | None = None, cache=None) -> list:
    """Liste, sans les analyser, les chemins de premier niveau à traiter.

    `stamps`, s'il est fourni, se remplit des dates de modification relevées au
    passage. Elles ne coûtent rien ici et évitent plus tard une lecture réseau
    par dossier pour savoir s'il a bougé.
    """
    if mode == MODE_FLAT:
        return list_all_videos(root, skip_hidden, stamps=stamps)
    entries = []
    try:
        for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
            if skip_hidden and _is_hidden(entry):
                continue
            if mode == MODE_FOLDERS and entry.is_dir(follow_symlinks=False):
                entries.append(Path(entry.path))
                if stamps is not None:
                    stamps[entry.path] = _stamp(entry)
            elif mode == MODE_FILES and entry.is_file():
                dot = entry.name.rfind(".")
                if dot > 0 and entry.name[dot:].lower() in VIDEO_EXTS:
                    entries.append(Path(entry.path))
                    if stamps is not None:
                        try:
                            st = entry.stat(follow_symlinks=False)
                            stamps[entry.path] = (st.st_size, st.st_mtime)
                        except OSError:
                            pass
    except OSError:
        pass
    if mode == MODE_FOLDERS:
        if expand_parent_folders:
            entries = expand_parents(entries, skip_hidden, stamps, cache)
        else:
            # Un dossier de tete est une destination, pas quelque chose a trier :
            # c'est la qu'on range les autres. Le laisser dans la liste revenait
            # a proposer de ranger le rangement.
            entries = [path for path in entries if not is_parent_folder(path)]
    return entries




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
        paths = list_entries(self.root, mode, self.skip_hidden,
                             self.expand_parents, stamps,
                             None if self.force or not self.use_cache else INDEX)
        if self._stop:
            INDEX.commit(force=True)
            return

        ids = [item_id_for(path, mode, self.expand_parents) for path in paths]
        total = len(paths)
        if self.use_cache:
            # La composition de la racine est notee tout de suite, avant meme de
            # verifier quoi que ce soit. Attendre la fin aurait reproduit le
            # defaut qu'on corrige : un premier inventaire interrompu aurait
            # garde ses dossiers sans que rien ne sache plus qu'ils forment
            # cette racine, et le lancement suivant serait reparti de zero.
            INDEX.put_listing(self.root, listing_key(mode, self.expand_parents), ids)

        # Ce que la liste affichee porte encore alors que le disque ne le porte
        # plus : on le retire avant meme de verifier le reste. Un dossier disparu
        # n'a pas a rester une minute a l'ecran.
        present = set(ids)
        gone = [key for key in self.known_ids if key not in present]
        if gone:
            self.patch.emit([], [], gone)
        shown = set(self.known_ids) - set(gone)

        known_sigs = {} if self.force or not self.use_cache else INDEX.signatures(ids)

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
                    if self.use_cache:
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
