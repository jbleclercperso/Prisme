"""Recherche de doublons, en relisant le moins d'octets possible.

Deux fichiers vidéo de taille rigoureusement identique, à l'octet près, sont
souvent le même fichier : la taille est donc le premier tri, et elle ne coûte
rien — l'énumération d'un répertoire la rapporte déjà. Souvent, pas toujours :
les parties d'un DVD (VTS_01_1.VOB, VTS_01_2.VOB…) et les segments d'une
caméra ont tous la même taille. Avant d'annoncer un groupe comme sûr — donc
d'en cocher d'office les exemplaires en trop —, on compare trois petits blocs
de chaque membre (début, milieu, fin) : ce qui diffère n'est pas un doublon.
Un groupe qu'on n'a pas pu vérifier est proposé « à comparer », sans rien de
coché.

Deux encodages d'un même film, eux, n'ont ni la même taille ni les mêmes octets,
mais les mêmes images : on les compare par empreintes d'image. Là, la durée sert
de garde-fou : deux vidéos de durées franchement différentes ne sont pas le même
film, quelle que soit la ressemblance d'une image. Quand la durée n'est pas
connue, on ne sonde rien : le groupe est proposé « à comparer », jamais comme des
fichiers en trop.

Dans chaque groupe, le meilleur exemplaire vient en tête — la plus grande
définition, puis le plus gros fichier, puis le plus long, puis le chemin le plus
court. Les paires qu'on a déclarées « pas des doublons » ne reviennent plus.

Rien n'est supprimé ici. Ce module rassemble et propose ; la décision revient à
qui regarde, et passe par la corbeille de session comme toute suppression.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .config import VIDEO_EXTS
from .scan import _is_hidden, human_duration, human_resolution, human_size

# En deça, deux fichiers de même taille ne prouvent rien : les vidéos minuscules
# — vignettes animées, fragments — se ressemblent trop.
MIN_SIZE = 1024 * 1024


# ---------------------------------------------------------------------------
# Parcours
# ---------------------------------------------------------------------------

def _walk(root, skip_hidden: bool = True, should_stop=None, on_count=None):
    """(chemin, stat) de chaque video sous `root`, dossier par dossier.

    `should_stop` est consulte a chaque dossier : sur le partage, un
    parcours complet prend des minutes, et « recliquer arrete » doit valoir
    pendant le recensement aussi, pas seulement apres. `on_count` recoit le
    nombre de videos deja vues, au plus trois fois par seconde : sans lui,
    l'attente du recensement ne se voyait pas.
    """
    stack = [str(root)]
    seen = 0
    last = time.monotonic()
    while stack:
        if should_stop is not None and should_stop():
            return
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
                dot = entry.name.rfind(".")
                if dot <= 0 or entry.name[dot:].lower() not in VIDEO_EXTS:
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            seen += 1
            yield entry.path, stat
        if on_count is not None:
            now = time.monotonic()
            if now - last >= 0.3:
                on_count(seen)
                last = now
    if on_count is not None:
        on_count(seen)


def walk_sized(root: Path, skip_hidden: bool = True, should_stop=None,
               on_count=None):
    """Rend (chemin, taille) pour chaque vidéo, sans lecture supplémentaire."""
    for path, stat in _walk(root, skip_hidden, should_stop, on_count):
        yield Path(path), stat.st_size


def walk_stamped(root: Path, skip_hidden: bool = True, should_stop=None,
                 on_count=None):
    """(chemin, taille, date) de chaque video. L'enumeration les donne toutes
    les trois sans une seule lecture de plus."""
    for path, stat in _walk(root, skip_hidden, should_stop, on_count):
        yield Path(path), stat.st_size, stat.st_mtime


# ---------------------------------------------------------------------------
# Un groupe de doublons, le meilleur exemplaire en tete
# ---------------------------------------------------------------------------

# Deux durees s'accordent a tant pres. Une seule image ne prouve presque
# rien — deux episodes au meme generique se ressemblent — d'ou l'ecart serre
# pour « meme image ». Quatre images qui concordent sont une preuve bien plus
# forte : on y laisse passer une copie acceleree (25 images/s contre 23,976,
# quatre pour cent plus courte), qui reste le meme film.
LOOK_SLACK = 0.02
SIG_SLACK = 0.05
# Et jamais moins que ceci : les conteneurs arrondissent, le son deborde.
SLACK_FLOOR = 2.0


def _same_length(first: float, second: float, slack: float) -> bool:
    """Vrai si deux durees connues sont celles d'un meme film."""
    return abs(first - second) <= max(SLACK_FLOOR, slack * max(first, second))


def _facts(path) -> tuple:
    """(pixels, hauteur, duree) connus de l'index. Lecture memoire seule :
    classer un groupe ne doit jamais attendre le reseau."""
    from .index import INDEX
    info = INDEX.probe(path) or {}
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    duration = float(info.get("duration") or 0.0)
    return (width * height or height), height, duration


def _known_size(path) -> int:
    from .stamps import known
    found = known(path)
    return int(found[0]) if found else 0


def _rank_key(path, size: int, pixels: int, duration: float) -> tuple:
    # Le chemin le plus court d'abord : « film.mp4 » plutot que « Copie de
    # film.mp4 », le dossier range plutot que le sous-dossier oublie.
    text = str(path)
    return (-pixels, -int(size or 0), -float(duration or 0.0),
            len(text), text.lower())


def rank_group(paths, sizes=None, durations=None) -> list:
    """Les chemins d'un groupe, du meilleur exemplaire au moins bon.

    Le meilleur a la plus grande definition, puis le plus gros fichier (a
    definition egale, moins compresse), puis la plus longue duree (pas
    tronque), puis le chemin le plus court. Tout vient de l'index et de ce
    que le parcours a deja releve : rien n'est lu sur le partage.
    `sizes` et `durations`, s'ils sont donnes, sont alignes sur `paths`.
    """
    paths = [Path(p) for p in paths]
    order = _rank_order(paths, sizes, durations)
    return [paths[i] for i in order]


def _rank_order(paths, sizes=None, durations=None) -> list:
    keys = []
    for index, path in enumerate(paths):
        pixels, _height, duration = _facts(path)
        size = (sizes[index] if sizes else 0) or _known_size(path)
        if durations and durations[index]:
            duration = durations[index]
        keys.append(_rank_key(path, size, pixels, duration))
    return sorted(range(len(paths)), key=keys.__getitem__)


class DupeGroup(tuple):
    """Un groupe de doublons : se deballe en (taille, chemins), comme avant.

    Les chemins vont du meilleur exemplaire au moins bon (`rank_group`) : le
    premier est celui a garder, les suivants ceux qu'on proposera de cocher.

    Le premier terme n'est plus la taille du plus gros : c'est ce que libere
    en moyenne chaque exemplaire en trop, si bien que « taille x (n - 1) »
    donne exactement la place rendue en gardant le meilleur. L'ancien calcul
    comptait la plus grosse taille autant de fois qu'il y avait de copies, et
    annoncait deux fois plus qu'on ne recupererait pour trois episodes de
    tailles differentes. Pour des fichiers de meme taille, rien ne change.

    `sure` est faux quand le groupe repose sur trop peu : une seule image,
    une duree inconnue. On le presente alors « a comparer », sans rien
    cocher d'office.
    """

    def __new__(cls, paths, sizes=None, durations=None, sure: bool = True,
                ranked: bool = False):
        paths = [Path(p) for p in paths]
        count = len(paths)
        sizes = [int(s or 0) for s in sizes] if sizes else [0] * count
        sizes = [size or _known_size(path) for size, path in zip(sizes, paths)]
        facts = [_facts(path) for path in paths]
        durations = [float(d or 0.0) for d in durations] if durations else [0.0] * count
        durations = [d or fact[2] for d, fact in zip(durations, facts)]
        if not ranked:
            order = sorted(range(count), key=lambda i: _rank_key(
                paths[i], sizes[i], facts[i][0], durations[i]))
            paths = [paths[i] for i in order]
            sizes = [sizes[i] for i in order]
            durations = [durations[i] for i in order]
            facts = [facts[i] for i in order]
        gain = sum(sizes) - sizes[0] if count else 0
        each = gain // (count - 1) if count > 1 else 0
        self = tuple.__new__(cls, (each, paths))
        self.paths = paths
        self.sizes = sizes
        self.durations = durations
        self.heights = [fact[1] for fact in facts]
        self.sure = bool(sure)
        self.gain = gain
        return self

    def __reduce__(self):
        return (DupeGroup, (self.paths, self.sizes, self.durations, self.sure, True))

    @property
    def keep(self) -> Path:
        """L'exemplaire a garder."""
        return self.paths[0]

    @property
    def extras(self) -> list:
        """Les exemplaires en trop, du meilleur au moins bon."""
        return self.paths[1:]

    @property
    def to_check(self) -> list:
        """Ce qu'on peut cocher d'office : les exemplaires en trop d'un groupe
        sur, rien d'un groupe « a comparer »."""
        return self.paths[1:] if self.sure else []

    def subset(self, indices) -> "DupeGroup":
        """Le meme groupe restreint a ces membres (indices), reclasse."""
        return DupeGroup([self.paths[i] for i in indices],
                         [self.sizes[i] for i in indices],
                         [self.durations[i] for i in indices], self.sure)

    def gap(self, path) -> str:
        """Ce qui distingue cet exemplaire du meilleur, en quelques mots."""
        wanted = os.path.normcase(str(path))
        try:
            at = [os.path.normcase(str(p)) for p in self.paths].index(wanted)
        except ValueError:
            return ""
        if at == 0:
            return "à garder"
        parts = []
        best, this = self.heights[0], self.heights[at]
        if best and this and best != this:
            parts.append(f"{human_resolution(this)} au lieu de "
                         f"{human_resolution(best)}")
        weight = self.sizes[at] - self.sizes[0]
        if weight:
            parts.append(f"{human_size(abs(weight))} de "
                         f"{'plus' if weight > 0 else 'moins'}")
        first, other = self.durations[0], self.durations[at]
        if first and other and abs(other - first) >= 1.0:
            parts.append(f"{human_duration(abs(other - first))} de "
                         f"{'plus' if other > first else 'moins'}")
        return " · ".join(parts) or "identique"


# Des noms de segments : les parties d'un meme DVD, d'une meme sequence de
# camescope, ont toutes la meme taille (le graveur coupe a 1 Go) sans etre le
# meme fichier. Leur taille seule ne prouve rien.
_SEGMENT = re.compile(r"^(vts_\d+_\d+\.vob|\d{5}\.(m2ts|mts))$", re.IGNORECASE)


def _is_segment(path) -> bool:
    return bool(_SEGMENT.match(os.path.basename(str(path))))


def _split_same_size(paths: list, size: int) -> list:
    """Les sous-groupes d'un meme casier de taille, sans rien lire.

    Deux membres dont les durees connues (ou les hauteurs connues) different
    ne sont pas le meme fichier : ils ne partagent jamais un groupe. Les
    membres a duree inconnue rejoignent le seul sous-groupe qui existe, ou se
    groupent entre eux. Un sous-groupe n'est « sur » que si toutes ses durees
    sont connues et concordent, et qu'aucun nom ne trahit un segment
    (VTS_01_1.VOB) : la taille seule ne prouve rien. `DuplicateScan`
    confirme ensuite par le contenu.
    """
    facts = [_facts(path) for path in paths]
    known = sorted((i for i in range(len(paths)) if facts[i][2] > 0),
                   key=lambda i: facts[i][2])
    clusters: list = []
    for i in known:
        duration, height = facts[i][2], facts[i][1]
        home = None
        for cluster in clusters:
            heights = {facts[j][1] for j in cluster if facts[j][1]}
            # Tries par duree croissante : le dernier membre est le plus proche.
            if (_same_length(duration, facts[cluster[-1]][2], 0.0)
                    and (not height or not heights or heights == {height})):
                home = cluster
                break
        if home is None:
            clusters.append([i])
        else:
            home.append(i)
    unknown = [i for i in range(len(paths)) if facts[i][2] <= 0]
    if unknown:
        if len(clusters) == 1:
            clusters[0].extend(unknown)
        else:
            clusters.append(unknown)
    out = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        members = [paths[i] for i in sorted(cluster)]
        sure = (all(facts[i][2] > 0 for i in cluster)
                and not any(_is_segment(path) for path in members))
        out.append(DupeGroup(members, [size] * len(members), sure=sure))
    return out


def group_by_size(pairs, minimum: int = MIN_SIZE, ignored=None) -> list:
    """Groupes d'au moins deux fichiers partageant exactement une taille.

    Les groupes sortent du plus lourd au plus léger : c'est dans cet ordre qu'on
    veut les traiter, puisque c'est là que se trouve la place à récupérer.
    `ignored` : les paires declarees « pas des doublons » (voir
    `dupes_memory.pair_test`), la memoire de l'application par defaut.

    Rien n'est lu ici : un groupe n'y est « sur » que sur la foi de l'index
    (voir `_split_same_size`). `DuplicateScan` le confirme par le contenu.
    """
    from .dupes_memory import NOT_DUPES, filter_groups
    by_size: dict = {}
    for path, size in pairs:
        if size < minimum:
            continue
        by_size.setdefault(size, []).append(path)
    groups = [group for size, paths in by_size.items() if len(paths) > 1
              for group in _split_same_size(paths, size)]
    if ignored is None:
        ignored = NOT_DUPES.snapshot()
    if ignored and not hasattr(ignored, "est_ignoree"):
        groups = filter_groups(groups, frozenset(ignored))
    elif ignored:
        groups = ignored.filtrer_groupes(groups)
    groups.sort(key=lambda group: (-group[0], str(group[1][0]).lower()))
    return groups


# Ce qu'on relit de chaque membre d'un groupe de meme taille : trois blocs,
# au debut, au milieu et a la fin. Deux parties de DVD ou deux segments de
# camera different des le premier ; deux vraies copies sont identiques
# partout. Trois petites lectures par membre d'un groupe candidat -- et non
# par video de la collection.
CONTENT_BLOCK = 64 * 1024
CONTENT_WORKERS = 8


def content_key(path, size: int) -> str | None:
    """Empreinte de trois blocs du fichier, ou None s'il n'a pas pu etre lu.

    Signale sa lecture (`media._Reading`) : un rangement attend qu'elle
    finisse, et un chemin qu'on s'apprete a deplacer n'est pas ouvert.
    """
    from . import media
    key = media._key(path)
    if media.CLOSING or media._held_until(key):
        return None
    size = int(size or 0)
    spots = sorted({0, max(0, size // 2 - CONTENT_BLOCK // 2),
                    max(0, size - CONTENT_BLOCK)})
    digest = hashlib.sha1(str(size).encode("ascii"))
    try:
        with media._Reading(path), open(path, "rb", buffering=0) as handle:
            for spot in spots:
                handle.seek(spot)
                digest.update(handle.read(CONTENT_BLOCK))
    except (OSError, ValueError):
        return None
    return digest.hexdigest()


def confirm_by_content(groups: list, should_stop=None) -> list:
    """Ne garde « surs » que les groupes dont les membres ont le meme contenu.

    Les membres identiques forment un groupe sur, dont les exemplaires en
    trop seront coches d'office : la taille seule avait fait cocher la
    partie 2 d'un DVD comme copie de sa partie 1. Un membre dont le contenu
    ne ressemble a aucun autre n'est pas un doublon : il sort -- sans quoi
    cinquante DVD formaient un groupe de deux cents parties « a comparer ».
    Ceux qu'on n'a pas pu lire (NAS qui ne repond pas) restent ensemble « a
    comparer », sans rien de coche.
    """
    jobs = sorted({(str(path), group.sizes[at]) for group in groups
                   for at, path in enumerate(group.paths)})
    if not jobs:
        return list(groups)
    keys: dict = {}
    with ThreadPoolExecutor(max_workers=CONTENT_WORKERS) as pool:
        futures = {pool.submit(content_key, path, size): path
                   for path, size in jobs}
        for future, path in futures.items():
            if should_stop is not None and should_stop():
                pool.shutdown(wait=True, cancel_futures=True)
                return []
            try:
                keys[path] = future.result()
            except Exception:                           # noqa: BLE001
                keys[path] = None
    out = []
    for group in groups:
        by_key: dict = {}
        for at, path in enumerate(group.paths):
            found = keys.get(str(path))
            if found is not None:
                by_key.setdefault(found, []).append(at)
        twins = [members for members in by_key.values() if len(members) > 1]
        if len(twins) == 1 and len(twins[0]) == len(group.paths):
            if group.sure:
                out.append(group)
            else:
                out.append(DupeGroup(group.paths, group.sizes, group.durations,
                                     sure=True, ranked=True))
            continue
        placed = set()
        for members in twins:
            placed.update(members)
            out.append(DupeGroup([group.paths[i] for i in members],
                                 [group.sizes[i] for i in members],
                                 [group.durations[i] for i in members],
                                 sure=True, ranked=True))
        rest = [at for at in range(len(group.paths))
                if at not in placed and keys.get(str(group.paths[at])) is None]
        if len(rest) > 1:
            out.append(DupeGroup([group.paths[i] for i in rest],
                                 [group.sizes[i] for i in rest],
                                 [group.durations[i] for i in rest],
                                 sure=False, ranked=True))
    out.sort(key=lambda group: (-group[0], str(group[1][0]).lower()))
    return out


class DuplicateScan(QThread):
    """Parcourt la collection et rassemble les fichiers de taille identique."""

    progress = Signal(int)                # videos examinees
    found = Signal(list)                  # [DupeGroup] : (taille, [chemins])

    def __init__(self, root: Path, skip_hidden: bool = True, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.skip_hidden = skip_hidden
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        pairs = []
        last = 0.0
        for path, size in walk_sized(self.root, self.skip_hidden,
                                     should_stop=lambda: self._stop):
            if self._stop:
                break
            pairs.append((path, size))
            now = time.monotonic()
            if now - last >= 0.3:
                self.progress.emit(len(pairs))
                last = now
        self.progress.emit(len(pairs))
        if self._stop:
            self.found.emit([])
            return
        groups = confirm_by_content(group_by_size(pairs),
                                    should_stop=lambda: self._stop)
        self.found.emit([] if self._stop else groups)


# ---------------------------------------------------------------------------
# Rapprocher des empreintes, sans les comparer deux a deux
# ---------------------------------------------------------------------------

# Distance de Hamming maximale entre deux empreintes pour parler de ressemblance.
NEAR = 6

# Faux pour forcer le calcul en Python pur, comme sur une machine sans numpy
# — le programme emporte n'en a pas. Les tests s'en servent pour verifier que
# les deux chemins rendent la meme chose.
USE_NUMPY = True
# En dessous de tant d'images, le Python pur va plus vite : numpy ne rattrape
# pas le temps de son chargement et de ses appels sur de petits tableaux.
NUMPY_FROM = 20_000
# Nombre de paires d'images qu'on s'autorise a comparer par cle. Les casiers
# les plus petits passent d'abord : seuls les casiers enormes — noir, bandes
# noires, logo commun — restent de cote, et ceux-la ne distinguent rien. Un
# vrai doublon partage presque toujours une quinzaine de cles, dont au moins
# une tombe dans un petit casier : sur cent sept mille empreintes realistes,
# un budget quatre fois plus large ne retrouvait pas une paire de plus, et
# doublait le temps.
PAIR_BUDGET = 1_500_000
# Paires comparees d'un coup : la memoire reste bornee quelle que soit la
# collection (quelques dizaines de megaoctets).
BLOCK = 1 << 20
# Un casier de plus de tant d'images partage seize bits avec mille autres :
# c'est du noir, des bandes, un fond uni. On le laisse, sans meme le
# deplier : ses paires couteraient plus en memoire qu'elles n'apprennent.
MAX_RUN = 1024
# Une image semblable a celles de plus de tant d'autres ne distingue plus
# rien : un fondu presque noir, un carton de titre, un generique commun. On
# la retire du compte, comme un mot vide d'une recherche. Sans cela, une
# poignee d'images sombres liait des milliers de videos entre elles, et le
# calcul y passait des minutes. Un vrai doublon a rarement plus de quelques
# copies : soixante-quatre laisse une large marge.
HUB = 64
# On commence a retirer ces images-la des que les rapprochements depassent
# ce nombre, sans attendre la fin : la memoire reste bornee.
PRUNE_AT = 1_000_000
# Au-dela, on cesse d'accumuler des rapprochements.
MAX_LINKS = 2_000_000

try:
    _bits = int.bit_count
except AttributeError:          # Python d'avant 3.10
    def _bits(value: int) -> int:
        return bin(value).count("1")


class _Stopped(Exception):
    """Le calcul a ete arrete : on rend « rien » plutot qu'un resultat partiel."""


class _Pace:
    """Rend la main regulierement pendant un long calcul en Python.

    Un fil de fond qui calcule sans relache garde le verrou de Python : le
    fil de l'interface, qui le lache et le reprend des dizaines de fois par
    image affichee, attend a chaque fois. On mesurait des survols a quatre
    images par seconde pendant la fin d'une recherche. Une courte pause
    toutes les quinze millisecondes lui laisse la place, pour quelques pour
    cent de temps de calcul. C'est aussi la qu'on verifie l'arret demande.
    """

    EVERY = 0.015
    REST = 0.001

    def __init__(self, should_stop=None):
        self.should_stop = should_stop
        self.count = 0
        self.last = time.monotonic()

    def tick(self) -> None:
        self.count += 1
        if not self.count & 1023:
            self.breathe()

    def breathe(self) -> None:
        now = time.monotonic()
        if now - self.last < self.EVERY:
            return
        if self.should_stop is not None and self.should_stop():
            raise _Stopped()
        time.sleep(self.REST)
        self.last = time.monotonic()


def _distinct(values) -> list:
    """Les empreintes d'une video, chacune une seule fois, dans l'ordre.

    Une video immobile rend quatre fois la meme image : comptees quatre
    fois, elles « s'accordaient » a elles seules avec n'importe quelle video
    qui montrait une image semblable. Les quasi-repetitions, elles, restent :
    c'est l'appariement (`_matching`) qui les empeche de compter double.
    """
    return list(dict.fromkeys(int(value) for value in values))


def _matching(edges) -> int:
    """Combien d'images de l'une s'apparient a des images toutes differentes
    de l'autre. `edges` : (image de l'une, image de l'autre) semblables.

    Compter seulement les images de la premiere qui trouvent une semblable
    ne suffisait pas : quatre plans quasi identiques d'une video immobile
    trouvaient tous la meme image de l'autre, et faisaient « quatre images
    qui concordent » avec une video qui n'en partageait qu'une. Un
    appariement ne se sert de chaque image qu'une fois.
    """
    follow: dict = {}
    for left, right in edges:
        follow.setdefault(left, []).append(right)
    taken: dict = {}

    def place(left, tried) -> bool:
        for right in follow[left]:
            if right in tried:
                continue
            tried.add(right)
            if right not in taken or place(taken[right], tried):
                taken[right] = left
                return True
        return False

    return sum(1 for left in follow if place(left, set()))


def _combos(close: int) -> list:
    """Les cles de rapprochement, en octets de l'empreinte.

    Deux empreintes a au plus six bits d'ecart ont au moins deux octets sur
    huit identiques : les six bits changes touchent au plus six octets. Une
    cle par paire d'octets (vingt-huit cles de seize bits) les reunit donc
    forcement dans un meme casier, et chaque casier ne contient qu'une
    poignee d'images — la ou les anciens casiers d'un octet en contenaient
    des milliers, qu'il fallait sauter, et avec eux les vrais doublons.
    """
    if close <= 6:
        return list(combinations(range(8), 2))
    if close == 7:
        return [(chunk,) for chunk in range(8)]
    return [()]


def _numpy():
    if not USE_NUMPY:
        return None
    try:
        import numpy
    except ImportError:
        return None
    return numpy


def _popcount(np):
    found = getattr(np, "bitwise_count", None)
    if found is not None:
        return found
    table = np.array([bin(i).count("1") for i in range(1 << 16)], dtype=np.int16)
    mask = np.uint64(0xFFFF)

    def count(values):
        total = table[(values & mask).astype(np.intp)]
        for shift in (16, 32, 48):
            total = total + table[((values >> np.uint64(shift)) & mask).astype(np.intp)]
        return total
    return count


def _links(frames: list, owner: list, nimg: list, close: int, agree: int,
           pace: _Pace, report=None) -> list:
    """Les couples de videos (i, j, fort), i < j, qui s'accordent assez.

    `frames` : toutes les empreintes d'image, `owner` : la video de chacune,
    `nimg` : le nombre d'images de chaque video. Deux videos s'accordent
    quand au moins `agree` de leurs images s'apparient a `close` bits pres
    — ou toutes les images utiles de la plus pauvre, si elle en porte moins.
    `fort` dit si l'accord atteint vraiment `agree`.
    """
    count, total = len(nimg), len(frames)
    if total < 2 or count < 2:
        return []
    np = _numpy() if total >= NUMPY_FROM else None
    if np is not None and max(count * count, total) * total < (1 << 62):
        edges, useful = _edges_numpy(np, frames, owner, nimg, close, agree,
                                     pace, report)
    else:
        edges, useful = _edges_python(frames, owner, nimg, close, pace, report)
    return _agreements(edges, owner, useful, agree, pace)


def _agreements(edges, owner, useful, agree, pace) -> list:
    """Des couples d'images semblables aux couples de videos qui s'accordent.

    `edges` : (image, image) semblables, de deux videos differentes ;
    `useful` : le nombre d'images de chaque video qui distinguent encore
    quelque chose (voir `HUB`)."""
    by_pair: dict = {}
    for a, b in edges:
        oa, ob = owner[a], owner[b]
        if oa < ob:
            by_pair.setdefault((oa, ob), []).append((a, b))
        else:
            by_pair.setdefault((ob, oa), []).append((b, a))
        pace.tick()
    out = []
    for (lo, hi), pairs in sorted(by_pair.items()):
        want = min(agree, useful[lo], useful[hi])
        if want <= 0:
            continue
        matched = _matching(pairs) if want > 1 else 1
        if matched >= want:
            out.append((lo, hi, matched >= agree))
        pace.tick()
    return out


def _unique_sorted(np, values):
    """Les valeurs distinctes d'un tableau d'entiers, triees.

    Pas `np.unique` : il passe desormais par une table de hachage, vingt
    fois plus lente ici que le simple tri sur des entiers de 64 bits."""
    values = np.sort(values)
    if len(values) < 2:
        return values
    keep = np.empty(len(values), dtype=bool)
    keep[0] = True
    np.not_equal(values[1:], values[:-1], out=keep[1:])
    return values[keep]


def _run_counts(np, ordered):
    """(valeurs, nombre de fois) d'un tableau deja trie."""
    if not len(ordered):
        return ordered, np.zeros(0, dtype=np.int64)
    cuts = np.flatnonzero(ordered[1:] != ordered[:-1]) + 1
    starts = np.concatenate((np.zeros(1, dtype=np.int64), cuts))
    counts = np.diff(np.concatenate((starts, np.array([len(ordered)], dtype=np.int64))))
    return ordered[starts], counts


def _triangle(np, size: int, kept: dict) -> tuple:
    """Les positions (i, j), i < j, de toutes les paires d'un casier de
    `size` images. Retenues pour les petits casiers, qui reviennent sans
    cesse ; recalculees pour les grands, qui peseraient trop en memoire."""
    found = kept.get(size)
    if found is None:
        left, right = np.triu_indices(size, 1)
        found = (left.astype(np.int32), right.astype(np.int32))
        if size <= 128:
            kept[size] = found
    return found


def _prune_numpy(np, found, total):
    """Retire les images trop communes (`HUB`). Rend (liens, communes)."""
    a, b = found // total, found % total
    degree = np.bincount(a, minlength=total) + np.bincount(b, minlength=total)
    hubs = degree > HUB
    if hubs.any():
        found = found[~(hubs[a] | hubs[b])]
    return found, hubs


def _edges_numpy(np, frames, owner, nimg, close, agree, pace, report) -> tuple:
    """Les couples d'images semblables, calcules avec numpy.

    Les tris et comparaisons se font hors du verrou de Python : le fil de
    l'interface n'attend pas. Chaque casier est compare sur des tableaux
    deja ranges dans l'ordre de la cle, pour que la memoire soit lue d'un
    trait plutot qu'au hasard. Seuls les couples de videos qui peuvent
    encore s'accorder repassent en Python."""
    total = len(frames)
    count = len(nimg)
    values = np.empty(total, dtype=np.uint64)
    owners = np.empty(total, dtype=np.int64)
    for start in range(0, total, 65536):
        values[start:start + 65536] = frames[start:start + 65536]
        owners[start:start + 65536] = owner[start:start + 65536]
        pace.breathe()
    chunks = [((values >> np.uint64(8 * c)) & np.uint64(255)).astype(np.int32)
              for c in range(8)]
    popcount = _popcount(np)
    combos = _combos(close)
    found = np.empty(0, dtype=np.int64)
    common = np.zeros(total, dtype=bool)
    triangles: dict = {}
    for step, combo in enumerate(combos):
        if combo:
            key = chunks[combo[0]]
            for chunk in combo[1:]:
                key = (key << 8) | chunks[chunk]
        else:
            key = np.zeros(total, dtype=np.int32)
        if common.any():
            active = np.flatnonzero(~common)
            order = active[np.argsort(key[active], kind="stable")]
        else:
            order = np.argsort(key, kind="stable")
        ordered = key[order]
        size_all = len(order)
        cuts = np.flatnonzero(ordered[1:] != ordered[:-1]) + 1
        starts = np.concatenate((np.zeros(1, dtype=np.int64), cuts))
        lengths = np.diff(np.concatenate((starts, np.array([size_all], dtype=np.int64))))
        several = (lengths >= 2) & ((lengths <= MAX_RUN) if combo else True)
        starts, lengths = starts[several], lengths[several]
        if combo:
            # Les petits casiers d'abord, puis tant que le budget le permet.
            by_size = np.argsort(lengths, kind="stable")
            starts, lengths = starts[by_size], lengths[by_size]
            within = np.cumsum(lengths * (lengths - 1) // 2) <= PAIR_BUDGET
            starts, lengths = starts[within], lengths[within]
        ranked_values = values[order]
        ranked_owners = owners[order]
        starts = starts.astype(np.int32)
        pace.breathe()
        batch = []
        pending = 0
        for size in _unique_sorted(np, lengths).tolist():
            chosen = starts[lengths == size]
            left, right = _triangle(np, size, triangles)
            per = max(1, BLOCK // len(left))
            for at in range(0, len(chosen), per):
                if size == 2:
                    # Le cas de loin le plus frequent : deux images par
                    # casier, sans rien a deplier.
                    pa = chosen[at:at + per]
                    pb = pa + 1
                else:
                    base = chosen[at:at + per, None]
                    pa = (base + left).ravel()
                    pb = (base + right).ravel()
                keep = ((popcount(ranked_values[pa] ^ ranked_values[pb]) <= close)
                        & (ranked_owners[pa] != ranked_owners[pb]))
                if keep.any():
                    a, b = order[pa[keep]], order[pb[keep]]
                    batch.append(np.minimum(a, b) * total + np.maximum(a, b))
                    pending += len(batch[-1])
                    if pending > BLOCK:
                        found = _unique_sorted(np, np.concatenate([found] + batch))
                        batch, pending = [], 0
                pace.breathe()
        if batch:
            found = _unique_sorted(np, np.concatenate([found] + batch))
        if len(found) > PRUNE_AT:
            found, hubs = _prune_numpy(np, found, total)
            common |= hubs
        if report is not None:
            report((step + 1) / len(combos))
        if len(found) > MAX_LINKS:
            break
    found, hubs = _prune_numpy(np, found, total)
    common |= hubs
    useful = np.asarray(nimg, dtype=np.int64) - np.bincount(
        owners[common], minlength=count)
    if not len(found):
        return [], useful.tolist()
    # Avant l'appariement exact, en Python, un tri grossier : un couple de
    # videos ne peut pas apparier plus d'images qu'il n'en a de distinctes
    # d'un cote comme de l'autre.
    # Les tableaux intermediaires sont laches aussitot : sur une collection
    # d'images tres semblables, ils pesaient des centaines de megaoctets.
    a, b = found // total, found % total
    del found
    oa, ob = owners[a], owners[b]
    swap = oa > ob
    fa, fb = np.where(swap, b, a), np.where(swap, a, b)
    del a, b
    pair = np.minimum(oa, ob) * count + np.maximum(oa, ob)
    del oa, ob, swap
    pairs, left = _run_counts(np, _unique_sorted(np, pair * total + fa) // total)
    _same, right = _run_counts(np, _unique_sorted(np, pair * total + fb) // total)
    want = np.minimum(agree, np.minimum(useful[pairs // count], useful[pairs % count]))
    hopeful = pairs[(want > 0) & (np.minimum(left, right) >= want)]
    if not len(hopeful):
        return [], useful.tolist()
    at = np.minimum(np.searchsorted(hopeful, pair), len(hopeful) - 1)
    keep = hopeful[at] == pair
    return list(zip(fa[keep].tolist(), fb[keep].tolist())), useful.tolist()


def _prune_python(links: set, common: set) -> set:
    """`_prune_numpy` sans numpy."""
    degree: dict = {}
    for a, b in links:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    hubs = {frame for frame, links_of in degree.items() if links_of > HUB}
    if hubs:
        common |= hubs
        links = {(a, b) for a, b in links if a not in hubs and b not in hubs}
    return links


def _edges_python(frames, owner, nimg, close, pace, report) -> tuple:
    """Les couples d'images semblables, sans numpy : memes cles, memes
    casiers, meme resultat, en plus lent. C'est le chemin des petites
    collections, et du programme emporte, qui n'embarque pas numpy."""
    combos = _combos(close)
    links: set = set()
    common: set = set()
    for step, combo in enumerate(combos):
        buckets: dict = {}
        if len(combo) == 2:
            first, second = 8 * combo[0], 8 * combo[1]
            for index, value in enumerate(frames):
                if index in common:
                    continue
                buckets.setdefault(((value >> first) & 255) << 8
                                   | ((value >> second) & 255), []).append(index)
                pace.tick()
        else:
            shifts = [8 * c for c in combo]
            for index, value in enumerate(frames):
                if index in common:
                    continue
                key = 0
                for shift in shifts:
                    key = (key << 8) | ((value >> shift) & 255)
                buckets.setdefault(key, []).append(index)
                pace.tick()
        runs = sorted((members for members in buckets.values()
                       if 1 < len(members) and (not combo or len(members) <= MAX_RUN)),
                      key=len)
        budget = PAIR_BUDGET if combo else None
        for members in runs:
            size = len(members)
            if budget is not None:
                cost = size * (size - 1) // 2
                if cost > budget:
                    break
                budget -= cost
            for at in range(size - 1):
                a = members[at]
                va, oa = frames[a], owner[a]
                for b in members[at + 1:]:
                    if owner[b] != oa and _bits(va ^ frames[b]) <= close:
                        links.add((a, b) if a < b else (b, a))
                pace.tick()
        if len(links) > PRUNE_AT:
            links = _prune_python(links, common)
        if report is not None:
            report((step + 1) / len(combos))
        if len(links) > MAX_LINKS:
            break
    links = _prune_python(links, common)
    useful = list(nimg)
    for frame in common:
        useful[owner[frame]] -= 1
    return sorted(links), useful


def _duration_of(path) -> float:
    from .index import INDEX
    return float((INDEX.probe(path) or {}).get("duration") or 0.0)


def _group_links(paths, sizes, durations, links, slack, ignored, trust) -> list:
    """Reunit les videos liees en groupes, apres les derniers garde-fous.

    Un lien est refuse si les deux durees sont connues et trop differentes,
    ou si l'on a declare la paire « pas des doublons ».

    Les groupes se forment en etoile autour de la video la plus liee, et non
    de proche en proche : A ressemblait a B, B a C, C a D, et une chaine de
    ressemblances reunissait des series entieres — jusqu'a des dizaines de
    milliers de videos dans un seul groupe, sur une collection d'images
    sombres. Ici chaque membre ressemble directement au centre. Une video
    dont toutes les voisines ont deja un groupe rejoint celui de la
    premiere, mais ce groupe devient alors « a comparer ».

    Un groupe n'est « sur » que si chacun de ses liens au centre l'est :
    durees connues des deux cotes et accord complet.
    """
    from .dupes_memory import pair_test
    refused = pair_test(ignored)
    near: dict = {}
    for lo, hi, strong in links:
        first, second = durations[lo], durations[hi]
        if first and second and not _same_length(first, second, slack):
            continue
        if refused(paths[lo], paths[hi]):
            continue
        firm = bool(strong and first and second)
        near.setdefault(lo, {})[hi] = firm
        near.setdefault(hi, {})[lo] = firm
    centers = sorted(near, key=lambda video: (-len(near[video]), video))
    home: dict = {}
    groups: list = []
    for center in centers:
        if center in home:
            continue
        free = [video for video in sorted(near[center]) if video not in home]
        if not free:
            continue
        for video in [center] + free:
            home[video] = len(groups)
        groups.append([[center] + free,
                       trust and all(near[center][video] for video in free)])
    for video in centers:
        if video in home:
            continue
        target = min(home[other] for other in near[video])
        groups[target][0].append(video)
        groups[target][1] = False
        home[video] = target
    out = [DupeGroup([paths[i] for i in members], [sizes[i] for i in members],
                     [durations[i] for i in members], sure=sure)
           for members, sure in groups]
    out.sort(key=lambda group: (-group.gain, str(group[1][0]).lower()))
    return out


# ---------------------------------------------------------------------------
# Par image : deux encodages du meme film n'ont pas la meme taille, mais la
# meme vignette. On ne lit rien sur le partage : les vignettes deja fabriquees
# suffisent, et l'enumeration rapporte gratuitement tailles et dates.
# ---------------------------------------------------------------------------

def dhash(source) -> int | None:
    """Empreinte 64 bits d'une image : chaque bit compare deux pixels voisins.

    Insensible a la taille, au format, a la compression et aux petites
    variations de couleur — exactement ce qui separe deux encodages d'un meme
    film. Une image plate (noir, blanc, fondu) donne une empreinte vide, que
    l'on ecarte : elle rapprocherait tout ce qui commence par du noir.
    `source` est un chemin d'image, ou une QImage deja en memoire.
    """
    from PySide6.QtGui import QImage
    image = source if isinstance(source, QImage) else QImage(str(source))
    if image.isNull():
        return None
    return _dhash_image(image)


def _dhash_image(image) -> int | None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    small = image.convertToFormat(QImage.Format.Format_Grayscale8).scaled(
        9, 8, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    stride = small.bytesPerLine()
    raw = bytes(small.constBits())
    value = 0
    for y in range(8):
        row = raw[y * stride:y * stride + 9]
        for x in range(8):
            value = (value << 1) | (1 if row[x] < row[x + 1] else 0)
    bits = _bits(value)
    if bits < 4 or bits > 60:
        return None
    return value


def group_by_look(entries: list, near: int = NEAR, ignored=None,
                  should_stop=None, report=None, slack: float = LOOK_SLACK) -> list:
    """Groupes de vignettes qui se ressemblent.

    `entries` : (chemin, taille, empreinte), ou (chemin, taille, empreinte,
    duree). Sans duree, on prend celle que l'index connait. Deux videos
    dont les durees connues different nettement ne sont pas reunies, meme a
    image identique : c'est le cas des episodes d'une serie, qui partagent
    leur generique. Et comme une seule image ne prouve jamais tout, ces
    groupes sont toujours « a comparer » : rien n'y est coche d'office.
    """
    paths, sizes, durations, frames, owner = [], [], [], [], []
    for entry in entries:
        if not entry[2]:
            continue
        owner.append(len(paths))
        frames.append(int(entry[2]))
        paths.append(entry[0])
        sizes.append(int(entry[1] or 0))
        durations.append(float((entry[3] if len(entry) > 3 else 0) or 0.0))
    durations = [d or _duration_of(p) for d, p in zip(durations, paths)]
    pace = _Pace(should_stop)
    try:
        links = _links(frames, owner, [1] * len(paths), near, 1, pace, report)
        return _group_links(paths, sizes, durations, links, slack, ignored,
                            trust=False)
    except _Stopped:
        return []


def _look_of(target: Path) -> tuple:
    """(empreinte, definitive) de cette vignette. Une image illisible — en
    cours d'ecriture par un autre fil, par exemple — n'est pas definitive :
    on ne la retient pas, on la relira la prochaine fois."""
    from PySide6.QtGui import QImage
    image = QImage(str(target))
    if image.isNull():
        return None, False
    return _dhash_image(image), True


class ImageDuplicateScan(QThread):
    """Meme contrat que `DuplicateScan`, mais compare les vignettes.

    L'empreinte de chaque vignette est retenue d'une recherche a l'autre
    (`dupes_memory.LookMemo`) : la recalculer demandait de relire et de
    decoder chaque image, des minutes de calcul pour un resultat identique.
    """

    progress = Signal(int)
    found = Signal(list)

    def __init__(self, root: Path, width: int, skip_hidden: bool = True,
                 parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.width = width
        self.skip_hidden = skip_hidden
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from .dupes_memory import LookMemo
        from .media import build_preview_plan, thumb_path
        from .stamps import remember
        memo = LookMemo()
        entries = []
        seen = 0
        last = 0.0
        pace = _Pace()
        for video, size, mtime in walk_stamped(self.root, self.skip_hidden,
                                               should_stop=lambda: self._stop):
            if self._stop:
                break
            remember(str(video), size, mtime)
            seen += 1
            try:
                plan = build_preview_plan([video], 1, 0, True, True)
                target = thumb_path(video, plan[0][1], self.width)
                value = memo.get(target.stem)
                if value is memo.MISSING:
                    value = None
                    if target.exists():
                        value, final = _look_of(target)
                        if final:
                            memo.put(target.stem, value)
                if value is not None:
                    entries.append((video, size, value, plan[0][2] or 0.0))
            except OSError:
                pass
            pace.tick()
            now = time.monotonic()
            if now - last >= 0.3:
                self.progress.emit(seen)
                last = now
        memo.save()
        self.progress.emit(seen)
        groups = [] if self._stop else group_by_look(
            entries, should_stop=lambda: self._stop)
        self.found.emit([] if self._stop else groups)


# ---------------------------------------------------------------------------
# L'empreinte d'une video : plusieurs images, retenues une fois pour toutes
# ---------------------------------------------------------------------------

# Quatre images valent bien mieux qu'une : deux encodages du meme film ont
# rarement la meme premiere image — generique, logo, recadrage — mais se
# suivent ensuite. Huit n'apportaient presque rien et coutaient le double.
SHOTS = 4
# Deux images se ressemblent en dessous de cette distance.
CLOSE = 6
# Et deux videos sont un doublon si au moins tant de leurs images concordent.
AGREE = 2
# La methode de calcul, inscrite avec chaque empreinte. Les empreintes
# d'avant (instants tires des plans quand ils etaient connus, images passees
# par le cache de vignettes) restent valables quand elles ont ete prises aux
# memes fractions de la duree ; les autres sont refaites une fois.
SIG_METHOD = "v2"
# Le temps laisse a ffmpeg pour une image, au-dela on compte un echec.
GRAB_TIMEOUT = 25


def _grab(video: Path, ts: float, width: int) -> tuple:
    """L'image de `video` a l'instant `ts`, en memoire : (image, echec).

    Tiree directement par un tuyau, sans passer par le cache de vignettes :
    chaque video y laissait quatre JPEG que rien ne relisait, des gigaoctets
    pour trente-deux octets d'empreinte. Meme largeur et meme conversion
    qu'avant, pour que les empreintes deja en base restent comparables.

    Le saut est exact (l'instant demande, pas l'image cle qui le precede) :
    c'est ce qui rend l'empreinte independante de l'encodage. `echec` est
    vrai quand ffmpeg n'a pas pu lire — partage injoignable, delai depasse,
    fichier abime, arret demande — et faux quand la video n'a simplement pas
    d'image a cet instant : le premier se retente, le second non.

    ffmpeg part par `media._spawn`, comme tous les autres : ranger la video,
    arreter les empreintes ou fermer Prisme l'arrete net. Lance a part, il
    tenait le fichier ouvert pendant qu'on le rangeait (« utilise par un
    autre processus »), et survivait a la fenetre.
    """
    from PySide6.QtGui import QImage
    from .media import Tools, _spawn
    if not Tools.ffmpeg:
        return None, True
    head = [Tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    seek = ["-ss", f"{max(0.0, ts):.2f}", "-i", str(video)]
    tail = ["-an", "-sn", "-dn", "-frames:v", "1", "-vf", f"scale={int(width)}:-2",
            "-f", "image2pipe", "-c:v", "ppm", "pipe:1"]
    # En-tete abrege d'abord, comme pour les vignettes ; en entier ensuite,
    # pour les rares fichiers qui ne se laissent pas lire autrement.
    for quick in (["-probesize", "2M", "-analyzeduration", "2M"], []):
        code, out, _err, why = _spawn(head + quick + seek + tail, GRAB_TIMEOUT,
                                      binary=True)
        if why == "tue":
            return None, True
        if why or code != 0:
            continue
        if not out:
            return None, False
        image = QImage.fromData(out, "PPM")
        if not image.isNull():
            return image, False
    return None, True


def signature_report(video, width: int, shots: int = SHOTS,
                     should_stop=None) -> tuple:
    """(empreintes, echecs, essais) pour cette video.

    Les instants sont toujours des fractions de la duree, jamais les plans
    releves : ceux-ci dependent de l'encodage et de l'ordre dans lequel on
    avait lance « Plans » et « Empreintes », si bien que deux copies d'un meme
    film pouvaient etre prises a des instants differents et ne plus se
    reconnaitre.

    `echecs` compte les images que la lecture n'a pas pu donner : une
    coupure passagere ne doit pas etre retenue comme « rien d'exploitable ».
    `should_stop` est consulte avant chaque image : un arret n'attend plus
    les images restantes.
    """
    from .index import INDEX
    from . import media
    from .media import Tools, _Reading, probe

    video = Path(video)
    if not Tools.ffmpeg:
        return [], 1, 1
    # Sans la duree, les quatre instants tombent tous a zero et l'empreinte
    # ne vaut plus rien : on sonde la video si on ne la connait pas encore.
    # Un ffprobe de plus au premier passage, jamais aux suivants.
    info = INDEX.probe(video)
    if not info:
        info = probe(video) or {}
    if not info.get("ok"):
        # Le sondage lui-meme a echoue : la lecture echouerait aussi.
        return [], 1, 1
    duration = float(info.get("duration") or 0.0)
    if duration > 2:
        moments = [duration * (index + 1) / (shots + 1) for index in range(shots)]
    else:
        moments = [0.0]
    found = []
    failed = 0
    owner = getattr(media._LOCAL, "owner", None)
    with _Reading(video):
        for ts in moments:
            if (media.CLOSING or (owner is not None and owner.cancelled)
                    or (should_stop is not None and should_stop())):
                failed += 1
                break
            image, broken = _grab(video, ts, width)
            if broken:
                failed += 1
                continue
            if image is None:
                continue
            value = _dhash_image(image)
            if value is not None:
                found.append(value)
    return _distinct(found), failed, len(moments)


def signature(video, width: int, shots: int = SHOTS) -> list:
    """Les empreintes de `shots` images de cette video (voir `signature_report`)."""
    return signature_report(video, width, shots)[0]


def _near(left: list, right: list, close: int = CLOSE) -> int:
    """Combien d'images de l'une trouvent une semblable dans l'autre."""
    agreed = 0
    for value in left:
        if any(_bits(value ^ other) <= close for other in right):
            agreed += 1
    return agreed


def group_by_signature(entries: list, close: int = CLOSE, agree: int = AGREE,
                       ignored=None, should_stop=None, report=None,
                       slack: float = SIG_SLACK) -> list:
    """Groupes de videos qui se ressemblent.

    `entries` : (chemin, empreintes, taille), ou (chemin, empreintes, taille,
    duree) ; sans duree, celle de l'index. Deux videos sont reunies quand au
    moins `agree` de leurs images concordent (a `close` bits pres) et que
    leurs durees, si on les connait, s'accordent.

    Cent mille videos ne se comparent pas deux a deux : chaque image est
    rangee sous vingt-huit cles (voir `_combos`), et l'on ne compare que ce
    qui partage une cle. Le calcul peut tourner hors du fil de l'interface
    (`SignatureGroupScan`) ; `should_stop` l'interrompt, `report` recoit
    l'avancement de 0 a 1.
    """
    paths, sizes, durations, frames, owner, nimg = [], [], [], [], [], []
    pace = _Pace(should_stop)
    try:
        for entry in entries:
            values = _distinct(entry[1])
            if not values:
                continue
            index = len(paths)
            paths.append(entry[0])
            sizes.append(int(entry[2] or 0))
            durations.append(float((entry[3] if len(entry) > 3 else 0) or 0.0))
            nimg.append(len(values))
            frames.extend(values)
            owner.extend([index] * len(values))
            pace.tick()
        durations = [d or _duration_of(p) for d, p in zip(durations, paths)]
        links = _links(frames, owner, nimg, close, agree, pace, report)
        return _group_links(paths, sizes, durations, links, slack, ignored,
                            trust=True)
    except _Stopped:
        return []


def sig_current(path, base: str) -> bool:
    """Vrai si l'empreinte retenue vaut encore pour ce fichier.

    `base` est « date|taille » tel que le parcours le releve. Une empreinte
    de la methode actuelle vaut tant que le fichier ne change pas. Une plus
    ancienne vaut encore si elle a ete prise aux fractions de la duree —
    c'etait le cas de toute video dont les plans ne donnaient aucun instant
    — et qu'elle porte assez d'images : une empreinte vide ou maigre pouvait
    n'etre qu'une coupure passagere, on la refait une fois. Refaire toute la
    base aurait demande des heures de lecture sur le partage pour des
    empreintes qui, a une image JPEG pres, sont les memes.
    """
    from .index import INDEX
    from .media import pick_moments
    # L'index ne garde en memoire que la date de chaque empreinte : les
    # valeurs ne sont relues que pour une ancienne empreinte a juger.
    stamp = INDEX.sig_stamp(path)
    if stamp is None:
        return False
    if stamp == f"{base}|{SIG_METHOD}":
        return True
    if stamp != base or len(INDEX.sig_of(path)) < AGREE:
        return False
    duration = float((INDEX.probe(path) or {}).get("duration") or 0.0)
    return not pick_moments(INDEX.scenes_of(path), duration, SHOTS)


class SignatureScan(QThread):
    """Remplit la base d'empreintes — seulement ce qui manque.

    Le premier passage coute : quatre images par video. Les suivants sont
    immediats, puisque chaque empreinte est retenue avec la taille et la date
    du fichier : seules les videos nouvelles, ou modifiees, sont a sonder.

    Une video qu'on n'a pas pu lire n'est pas retenue : elle sera retentee au
    passage suivant, et `unreadable` dit combien il y en a eu.
    """

    WORKERS = 6
    progress = Signal(int, int)      # faites, total
    walking = Signal(int)            # videos recensees, pendant le parcours
    unreadable = Signal(int)         # illisibles cette fois, avant `done`
    done = Signal(int, int)          # sondees, dans la base

    def __init__(self, root: Path, width: int, skip_hidden: bool = True,
                 parent=None):
        super().__init__(parent)
        from .media import _Owner
        self.root = Path(root)
        self.width = width
        self.skip_hidden = skip_hidden
        self.failed_count = 0
        self._stop = False
        # Porte les ffmpeg des empreintes : l'arret les coupe net, au lieu
        # d'attendre jusqu'a quatre images de vingt-cinq secondes chacune
        # pendant que la fenetre se ferme.
        self._owner = _Owner()

    def stop(self) -> None:
        self._stop = True
        self._owner.kill()

    def _one(self, job):
        """Sonde une video. Rend None si l'arret a ete demande, -1 si elle
        est illisible (rien n'est retenu), 0 si elle n'a que des images
        plates, 1 sinon."""
        from .index import INDEX
        from . import media
        video, size, stamp = job
        if self._stop:
            return None
        # Ce qu'on regarde passe devant : six ffmpeg d'empreintes sur le
        # partage ralentissaient d'autant la page affichee.
        media.wait_foreground(lambda: self._stop)
        if self._stop or media.CLOSING:
            return None
        media._LOCAL.owner = self._owner
        try:
            values, failed, tried = signature_report(
                video, self.width, should_stop=lambda: self._stop)
        finally:
            media._LOCAL.owner = None
        # Coupee par l'arret : ni illisible, ni a retenir -- l'index est
        # peut-etre deja ferme.
        if self._stop or media.CLOSING:
            return None
        if failed and len(values) < min(AGREE, tried):
            return -1
        INDEX.put_sig(video, stamp, values, size)
        return 1 if values else 0

    def run(self) -> None:
        from .index import INDEX
        from .stamps import remember
        todo = []
        known = 0
        for path, size, mtime in walk_stamped(self.root, self.skip_hidden,
                                              should_stop=lambda: self._stop,
                                              on_count=self.walking.emit):
            if self._stop:
                break
            remember(str(path), size, mtime)
            base = f"{int(mtime)}|{size}"
            if sig_current(path, base):
                known += 1
                continue
            todo.append((path, size, f"{base}|{SIG_METHOD}"))
        total = len(todo)
        seen = stored = failed = 0
        last = 0.0
        if total and not self._stop:
            pool = ThreadPoolExecutor(max_workers=self.WORKERS)
            try:
                for outcome in pool.map(self._one, todo):
                    if outcome is None:
                        continue
                    seen += 1
                    if outcome < 0:
                        failed += 1
                    else:
                        stored += 1
                    now = time.monotonic()
                    if now - last >= 0.3:
                        self.progress.emit(seen, total)
                        last = now
                    if self._stop:
                        break
            finally:
                # Sans `cancel_futures`, sortir attendait les milliers de
                # videos deja confiees au pot.
                pool.shutdown(wait=True, cancel_futures=True)
        # Ce qui vient d'etre appris est ecrit tout de suite : un arret net
        # juste apres ne doit pas le perdre.
        INDEX.commit(force=True)
        self.progress.emit(seen, total)
        self.failed_count = failed
        self.unreadable.emit(failed)
        self.done.emit(seen, known + stored)


def _inside(path: str, root: str) -> bool:
    """Vrai si `path` est `root` ou se trouve dessous — et non a cote :
    « X:\\Films2 » n'est pas sous « X:\\Films »."""
    path, root = os.path.normcase(path), os.path.normcase(root).rstrip("\\/")
    return path == root or path.startswith(root + "\\") or path.startswith(root + "/")


class SignatureGroupScan(QThread):
    """Compare les empreintes deja en base, hors du fil de l'interface.

    Meme contrat que `DuplicateScan` : `progress` (videos examinees) puis
    `found` (les groupes). Aucun acces au disque : tout vient de l'index. Le
    calcul prenait jusqu'a plusieurs minutes sur le fil de l'interface, qui
    ne repondait plus pendant ce temps.
    """

    progress = Signal(int)
    found = Signal(list)

    def __init__(self, root=None, parent=None, entries=None):
        super().__init__(parent)
        self.root = str(root) if root else ""
        self.entries = entries
        self.examined = 0
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from .index import INDEX
        entries = self.entries
        if entries is None:
            # Un instantane lu d'un bloc dans la base : les fils d'empreintes
            # peuvent ecrire dans la table pendant qu'on la parcourt, et ce
            # qui dort dans une corbeille n'y figure deja plus.
            entries = [(path, values, size)
                       for path, values, size in INDEX.all_sigs()
                       if not self.root or _inside(path, self.root)]
        self.examined = len(entries)
        self.progress.emit(0)
        groups = group_by_signature(
            entries, should_stop=lambda: self._stop,
            report=lambda part: self.progress.emit(int(len(entries) * part)))
        if groups and not self._stop:
            groups = present_only(groups, should_stop=lambda: self._stop)
        self.found.emit([] if self._stop else groups)


# Combien de chemins on verifie de front sur le partage : autant que de
# dossiers parcourus a la fois par l'analyse.
PRESENCE_WORKERS = 8


def present_only(groups: list, should_stop=None) -> list:
    """Retire des groupes les videos qui n'existent plus, puis les reclasse.

    Les empreintes vivent dans l'index, qui garde celle d'une video rangee ou
    supprimee hors de Prisme a son ancien chemin -- souvent moins profond,
    donc classe « ✓ à garder » devant la vraie copie, qui etait alors cochee
    d'office. On regarde chaque membre (une lecture par membre de groupe, et
    non par video) : un absent est oublie de l'index ; un membre qu'on n'a
    pas pu voir (NAS qui ne repond pas) rend son groupe « a comparer », sans
    rien de coche.
    """
    from . import actions
    from .index import INDEX
    members = sorted({str(path) for group in groups for path in group.paths})
    if not members:
        return list(groups)
    states: dict = {}
    with ThreadPoolExecutor(max_workers=PRESENCE_WORKERS) as pool:
        for path, state in zip(members, pool.map(actions.probe, members)):
            states[path] = state
            if should_stop is not None and should_stop():
                pool.shutdown(wait=True, cancel_futures=True)
                return []
    for path, state in states.items():
        if state == "absent":
            INDEX.forget_sig(path)
    out = []
    for group in groups:
        kept = [at for at, path in enumerate(group.paths)
                if states.get(str(path)) != "absent"]
        if len(kept) < 2:
            continue
        doubtful = any(states.get(str(group.paths[at])) != "ok" for at in kept)
        if len(kept) == len(group.paths) and not doubtful:
            out.append(group)
            continue
        # Reclasse sans les absents : le meilleur exemplaire peut changer.
        out.append(DupeGroup([group.paths[at] for at in kept],
                             [group.sizes[at] for at in kept],
                             [group.durations[at] for at in kept],
                             sure=group.sure and not doubtful))
    out.sort(key=lambda group: (-group.gain, str(group[1][0]).lower()))
    return out
