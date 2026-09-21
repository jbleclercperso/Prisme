"""Recherche de doublons, sans relire un octet des vidéos.

Deux fichiers vidéo de taille rigoureusement identique, à l'octet près, sont
presque toujours le même fichier : les formats compressés ne produisent pas deux
fois la même longueur par hasard. La taille est donc le premier tri, et elle ne
coûte rien — l'énumération d'un répertoire la rapporte déjà.

Quand la durée des deux est connue, elle sert de confirmation. Quand elle ne
l'est pas, on ne sonde rien : mieux vaut proposer un groupe à regarder que faire
attendre des minutes pour une certitude dont l'œil se charge en une seconde.

Rien n'est supprimé ici. Ce module rassemble et propose ; la décision revient à
qui regarde, et passe par la corbeille de session comme toute suppression.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QThread, Signal

from .config import VIDEO_EXTS
from .scan import _is_hidden

# En deça, deux fichiers de même taille ne prouvent rien : les vidéos minuscules
# — vignettes animées, fragments — se ressemblent trop.
MIN_SIZE = 1024 * 1024


def walk_sized(root: Path, skip_hidden: bool = True):
    """Rend (chemin, taille) pour chaque vidéo, sans lecture supplémentaire."""
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
                dot = entry.name.rfind(".")
                if dot <= 0 or entry.name[dot:].lower() not in VIDEO_EXTS:
                    continue
                yield Path(entry.path), entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue


def group_by_size(pairs, minimum: int = MIN_SIZE) -> list:
    """Groupes d'au moins deux fichiers partageant exactement une taille.

    Les groupes sortent du plus lourd au plus léger : c'est dans cet ordre qu'on
    veut les traiter, puisque c'est là que se trouve la place à récupérer.
    """
    by_size: dict = {}
    for path, size in pairs:
        if size < minimum:
            continue
        by_size.setdefault(size, []).append(path)
    groups = [(size, sorted(paths, key=lambda p: str(p).lower()))
              for size, paths in by_size.items() if len(paths) > 1]
    groups.sort(key=lambda pair: -pair[0])
    return groups


class DuplicateScan(QThread):
    """Parcourt la collection et rassemble les fichiers de taille identique."""

    progress = Signal(int)                # videos examinees
    found = Signal(list)                  # [(taille, [chemins])]

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
        for path, size in walk_sized(self.root, self.skip_hidden):
            if self._stop:
                break
            pairs.append((path, size))
            now = time.monotonic()
            if now - last >= 0.3:
                self.progress.emit(len(pairs))
                last = now
        self.progress.emit(len(pairs))
        self.found.emit([] if self._stop else group_by_size(pairs))


# ---------------------------------------------------------------------------
# Par image : deux encodages du meme film n'ont pas la meme taille, mais la
# meme vignette. On ne lit rien sur le partage : les vignettes deja fabriquees
# suffisent, et l'enumeration rapporte gratuitement tailles et dates.
# ---------------------------------------------------------------------------

# Distance de Hamming maximale entre deux empreintes pour parler de ressemblance.
NEAR = 6


def dhash(path) -> int | None:
    """Empreinte 64 bits d'une image : chaque bit compare deux pixels voisins.

    Insensible a la taille, au format, a la compression et aux petites
    variations de couleur — exactement ce qui separe deux encodages d'un meme
    film. Une image plate (noir, blanc, fondu) donne une empreinte vide, que
    l'on ecarte : elle rapprocherait tout ce qui commence par du noir.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    image = QImage(str(path))
    if image.isNull():
        return None
    small = image.convertToFormat(QImage.Format.Format_Grayscale8).scaled(
        9, 8, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    stride = small.bytesPerLine()
    raw = bytes(small.constBits())
    value = 0
    for y in range(8):
        row = raw[y * stride:y * stride + 9]
        for x in range(8):
            value = (value << 1) | (1 if row[x] < row[x + 1] else 0)
    bits = bin(value).count("1")
    if bits < 4 or bits > 60:
        return None
    return value


def group_by_look(entries: list, near: int = NEAR) -> list:
    """Groupes de vignettes qui se ressemblent. `entries` : (chemin, taille, empreinte).

    Cent mille empreintes ne se comparent pas deux a deux. On les range par
    tranches de huit bits : deux empreintes a moins de huit bits d'ecart ont
    forcement une tranche identique, donc se retrouvent dans un meme casier.
    """
    buckets: dict = {}
    for index, (_path, _size, value) in enumerate(entries):
        for chunk in range(8):
            key = (chunk, (value >> (chunk * 8)) & 0xFF)
            buckets.setdefault(key, []).append(index)
    parent = list(range(len(entries)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for members in buckets.values():
        if len(members) < 2 or len(members) > 400:
            continue
        for a in range(len(members)):
            va = entries[members[a]][2]
            for b in range(a + 1, len(members)):
                if bin(va ^ entries[members[b]][2]).count("1") <= near:
                    ra, rb = find(members[a]), find(members[b])
                    if ra != rb:
                        parent[ra] = rb
    groups: dict = {}
    for index in range(len(entries)):
        groups.setdefault(find(index), []).append(index)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        paths = sorted((entries[i][0] for i in members), key=lambda p: str(p).lower())
        size = max(entries[i][1] for i in members)
        out.append((size, paths))
    out.sort(key=lambda group: group[0], reverse=True)
    return out


class ImageDuplicateScan(QThread):
    """Meme contrat que `DuplicateScan`, mais compare les vignettes."""

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
        from .media import build_preview_plan, thumb_path
        from .stamps import remember
        entries = []
        seen = 0
        last = 0.0
        stack = [str(self.root)]
        while stack and not self._stop:
            current = stack.pop()
            try:
                listing = list(os.scandir(current))
            except OSError:
                continue
            for entry in listing:
                if self._stop:
                    break
                try:
                    if self.skip_hidden and _is_hidden(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        continue
                    dot = entry.name.rfind(".")
                    if dot <= 0 or entry.name[dot:].lower() not in VIDEO_EXTS:
                        continue
                    stat = entry.stat(follow_symlinks=False)
                    remember(entry.path, stat.st_size, stat.st_mtime)
                except OSError:
                    continue
                seen += 1
                video = Path(entry.path)
                try:
                    plan = build_preview_plan([video], 1, 0, True, True)
                    target = thumb_path(video, plan[0][1], self.width)
                    if target.exists():
                        value = dhash(target)
                        if value is not None:
                            entries.append((video, stat.st_size, value))
                except OSError:
                    pass
                now = time.monotonic()
                if now - last >= 0.3:
                    self.progress.emit(seen)
                    last = now
        self.progress.emit(seen)
        self.found.emit([] if self._stop else group_by_look(entries))


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


def walk_stamped(root: Path, skip_hidden: bool = True):
    """(chemin, taille, date) de chaque video. L'enumeration les donne toutes
    les trois sans une seule lecture de plus."""
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
                dot = entry.name.rfind(".")
                if dot <= 0 or entry.name[dot:].lower() not in VIDEO_EXTS:
                    continue
                stat = entry.stat(follow_symlinks=False)
                yield Path(entry.path), stat.st_size, stat.st_mtime
            except OSError:
                continue


def signature(video, width: int, shots: int = SHOTS) -> list:
    """Les empreintes de `shots` images de cette video.

    Les instants viennent des changements de plan quand ils sont connus —
    c'est le releve des plans qui les donne — sinon de fractions echelonnees.
    Les images passent par le cache de vignettes : ce qui a deja ete extrait
    ne l'est pas deux fois, et la deuxieme recherche de doublons ne coute
    presque rien.
    """
    from .index import INDEX
    from .media import extract_thumb, pick_moments, probe

    video = Path(video)
    # Sans la duree, les quatre instants tombent tous a zero et l'empreinte
    # ne vaut plus rien : on sonde la video si on ne la connait pas encore.
    # Un ffprobe de plus au premier passage, jamais aux suivants.
    info = INDEX.probe(video)
    if not info:
        info = probe(video) or {}
    duration = info.get("duration") or 0.0
    moments = pick_moments(INDEX.scenes_of(video), duration, shots)
    if not moments:
        if duration > 2:
            moments = [duration * (index + 1) / (shots + 1) for index in range(shots)]
        else:
            moments = [0.0]
    found = []
    for ts in moments:
        try:
            image = extract_thumb(video, ts, width)
        except OSError:
            continue
        if image is None:
            continue
        value = dhash(image)
        if value is not None:
            found.append(value)
    return found


def _near(left: list, right: list, close: int = CLOSE) -> int:
    """Combien d'images de l'une trouvent une semblable dans l'autre."""
    agreed = 0
    for value in left:
        if any(bin(value ^ other).count("1") <= close for other in right):
            agreed += 1
    return agreed


def group_by_signature(entries: list, close: int = CLOSE,
                       agree: int = AGREE) -> list:
    """Groupes de videos qui se ressemblent. `entries` : (chemin, empreintes, taille).

    Cent mille empreintes ne se comparent pas deux a deux. Chaque image est
    rangee par tranches de huit bits : deux images proches partagent
    forcement une tranche, donc se croisent dans un meme casier, et l'on ne
    compare que ce qui s'y trouve.
    """
    buckets: dict = {}
    for index, (_path, values, _size) in enumerate(entries):
        for value in values:
            for chunk in range(8):
                buckets.setdefault((chunk, (value >> (chunk * 8)) & 0xFF),
                                   set()).add(index)
    parent = list(range(len(entries)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    tried = set()
    for members in buckets.values():
        if len(members) < 2 or len(members) > 400:
            continue
        ordered = sorted(members)
        for at, a in enumerate(ordered):
            for b in ordered[at + 1:]:
                if (a, b) in tried:
                    continue
                tried.add((a, b))
                # Une video dont on n'a tire qu'une image ne peut pas en
                # accorder deux : on n'exige jamais plus qu'elle ne porte.
                want = min(agree, len(entries[a][1]), len(entries[b][1]))
                if want and _near(entries[a][1], entries[b][1], close) >= want:
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[ra] = rb
    groups: dict = {}
    for index in range(len(entries)):
        groups.setdefault(find(index), []).append(index)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        paths = sorted((Path(entries[i][0]) for i in members),
                       key=lambda p: str(p).lower())
        out.append((max(entries[i][2] for i in members), paths))
    out.sort(key=lambda group: group[0], reverse=True)
    return out


class SignatureScan(QThread):
    """Remplit la base d'empreintes — seulement ce qui manque.

    Le premier passage coute : quatre images par video. Les suivants sont
    immediats, puisque chaque empreinte est retenue avec la taille et la date
    du fichier : seules les videos nouvelles, ou modifiees, sont a sonder.
    """

    WORKERS = 6
    progress = Signal(int, int)      # faites, total
    done = Signal(int, int)          # sondees, dans la base

    def __init__(self, root: Path, width: int, skip_hidden: bool = True,
                 parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.width = width
        self.skip_hidden = skip_hidden
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def _one(self, pair) -> int:
        from .index import INDEX
        video, size, stamp = pair
        if self._stop:
            return 0
        values = signature(video, self.width)
        INDEX.put_sig(video, stamp, values, size)
        return 1 if values else 0

    def run(self) -> None:
        from .index import INDEX
        from .stamps import remember
        todo = []
        known = 0
        for video in walk_stamped(self.root, self.skip_hidden):
            if self._stop:
                break
            path, size, mtime = video
            remember(str(path), size, mtime)
            stamp = f"{int(mtime)}|{size}"
            if INDEX.sig_fresh(path, stamp):
                known += 1
                continue
            todo.append((path, size, stamp))
        total = len(todo)
        seen = 0
        last = 0.0
        if total:
            with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
                for _outcome in pool.map(self._one, todo):
                    seen += 1
                    now = time.monotonic()
                    if now - last >= 0.3:
                        self.progress.emit(seen, total)
                        last = now
                    if self._stop:
                        break
        self.progress.emit(seen, total)
        self.done.emit(seen, known + seen)
