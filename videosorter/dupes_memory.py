"""Ce que la recherche de doublons retient d'une séance à l'autre.

Deux mémoires, toutes deux de simples fichiers JSON :

- les paires « pas des doublons » : deux vidéos qu'on a regardées et jugées
  différentes. Sans elles, chaque recherche remontrait les mêmes faux
  doublons — deux épisodes au même générique, une série entière réunie par
  une chaîne de ressemblances — et l'on referait sans fin le même tri.
- l'empreinte de chaque vignette déjà examinée par « même image » : la
  recalculer demandait de relire et de décoder cent mille JPEG à chaque
  recherche, six minutes de calcul pour un résultat qui ne change pas.

Rien ici ne touche aux vidéos : ce ne sont que des notes sur elles.

Les « pas des doublons » sont des décisions prises à la main, comme les
favoris, et sont traitées comme eux : à côté d'eux, chez soi et non sur un
cache partagé où deux machines s'écraseraient l'une l'autre ; écrites pour de
bon ; copiées une fois par lancement ; et un fichier illisible est mis de
côté au lieu d'être remplacé par la décision suivante.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import config


def _key(path) -> str:
    """La forme sous laquelle un chemin est retenu.

    Windows ne distingue pas les majuscules : sans cette normalisation, le
    meme fichier vu par deux parcours pourrait compter pour deux.
    """
    return os.path.normcase(os.path.normpath(str(path)))


def _pair(a, b) -> tuple:
    left, right = _key(a), _key(b)
    return (left, right) if left <= right else (right, left)


def _under(key: str, prefix: str) -> bool:
    """Vrai si `key` est `prefix` lui-meme ou se trouve dessous."""
    if key == prefix:
        return True
    head = prefix.rstrip("\\/")
    return key.startswith(head + "\\") or key.startswith(head + "/")


def _write_json(path: Path, data) -> None:
    """Ecriture atomique et forcee sur le disque : un fichier a moitie ecrit
    ne remplace jamais le bon, et une coupure de courant juste apres ne
    laisse pas un fichier vide. Leve OSError si le disque refuse."""
    problem = config._write_atomic(
        path, json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    if problem:
        raise OSError(problem)


def _pairs_of(raw) -> set:
    pairs = set()
    for item in (raw.get("pairs") or []) if isinstance(raw, dict) else []:
        if isinstance(item, list) and len(item) == 2 and item[0] != item[1]:
            pairs.add(_pair(item[0], item[1]))
    return pairs


class NotDupes:
    """Les paires de videos declarees « pas des doublons ».

    Chargees au premier usage et non a l'import : le dossier de
    l'application peut etre un cache partage sur le reseau, et le demarrage
    ne doit rien y lire pour une fonction qu'on n'utilisera peut-etre pas.

    Partagee entre le fil de l'interface, qui ajoute des paires, et le fil
    qui cherche les doublons, qui les consulte : d'ou le verrou, et
    `snapshot` qui fige l'etat pour la duree d'un calcul.
    """

    FILE = "pas-doublons.json"

    def __init__(self, path: Path | None = None):
        # Lu a l'appel, jamais fige ici : le bac a sable des tests et un
        # cache portable deplacent le dossier de l'application.
        self._path = Path(path) if path else None
        self.lock = threading.Lock()
        self.pairs: set | None = None
        # Vrai tant que le fichier existe mais n'a pas pu etre lu : ecrire
        # alors effacerait toutes les decisions qu'il porte.
        self.read_only = False
        # Ce qu'il faudrait dire a l'utilisateur, s'il y a lieu.
        self.problem = ""

    @property
    def path(self) -> Path:
        return self._path or (config.PRIVATE_DIR / self.FILE)

    @property
    def backup_path(self) -> Path:
        return self.path.with_name(self.path.name + ".bak")

    def _read(self) -> set | None:
        """Les paires du fichier, ou None s'il ne se lit pas pour l'instant.

        Absent : rien, ou ce que l'ancienne version avait laisse dans le
        dossier de l'application (un cache partage), repris une fois. Abime :
        mis de cote, et la copie du lancement reprise a sa place.
        """
        text = config._read_text(self.path)
        if text is None:
            legacy = config.APP_DIR / self.FILE
            if self._path is None and legacy != self.path:
                old = config._read_text(legacy)
                raw = config._parse_object(old) if isinstance(old, str) else None
                return _pairs_of(raw or {})
            return set()
        if isinstance(text, OSError):
            self.problem = (f"« Pas des doublons » illisibles pour l'instant "
                            f"({text}) : rien n'est réécrit tant qu'ils ne se "
                            "relisent pas.")
            return None
        raw = config._parse_object(text)
        if raw is None:
            aside = config._set_aside(self.path)
            backup = config._read_text(self.backup_path)
            raw = config._parse_object(backup) if isinstance(backup, str) else None
            where = f" (mis de côté sous « {aside.name} »)" if aside else ""
            self.problem = ("Le fichier des « pas des doublons » était abîmé" + where
                            + (" : la copie de secours a été reprise."
                               if raw is not None else " : il repart de zéro."))
            if aside is None:
                return None
            return _pairs_of(raw or {})
        pairs = _pairs_of(raw)
        if pairs:
            # Une copie par lancement : c'est d'elle qu'on repartira si le
            # fichier s'abimait.
            config._copy_quietly(self.path, self.backup_path)
        return pairs

    def _loaded(self) -> set:
        if self.pairs is None:
            found = self._read()
            self.read_only = found is None
            self.pairs = found or set()
        return self.pairs

    def save(self) -> bool:
        """Ecrit la memoire. Faux si le disque a refuse : on garde la
        decision en memoire, elle sera ecrite a la prochaine occasion.

        Tant que le fichier n'a pas pu etre lu, on le relit d'abord et l'on
        y ajoute ce qu'on a decide entre-temps : l'ecraser perdait toutes
        les decisions d'avant.
        """
        with self.lock:
            pairs = self._loaded()
            if self.read_only:
                found = self._read()
                if found is None:
                    return False
                pairs |= found
                self.read_only = False
                self.problem = ""
            data = {"version": 1, "pairs": sorted([a, b] for a, b in pairs)}
        try:
            _write_json(self.path, data)
            return True
        except OSError:
            return False

    # -- consultation -----------------------------------------------------
    def est_ignoree(self, a, b) -> bool:
        """Vrai si l'on a dit que `a` et `b` ne sont pas des doublons."""
        with self.lock:
            return _pair(a, b) in self._loaded()

    def snapshot(self) -> frozenset:
        """Les paires connues, figees : un calcul de fond les consulte des
        milliers de fois sans prendre le verrou a chacune."""
        with self.lock:
            return frozenset(self._loaded())

    def __len__(self) -> int:
        with self.lock:
            return len(self._loaded())

    # -- decisions --------------------------------------------------------
    def ignorer(self, a, b, save: bool = True) -> bool:
        """Retient que `a` et `b` ne sont pas des doublons. Vrai si c'est
        nouveau."""
        if _key(a) == _key(b):
            return False
        with self.lock:
            pairs = self._loaded()
            pair = _pair(a, b)
            if pair in pairs:
                return False
            pairs.add(pair)
        if save:
            self.save()
        return True

    def ignorer_groupe(self, paths) -> int:
        """Toutes les paires d'un groupe d'un coup : le geste « pas des
        doublons » porte sur ce qu'on a coche, souvent plus de deux videos.
        Une seule ecriture pour tout le groupe."""
        paths = list(dict.fromkeys(_key(p) for p in paths))
        added = 0
        for at, left in enumerate(paths):
            for right in paths[at + 1:]:
                added += self.ignorer(left, right, save=False)
        if added:
            self.save()
        return added

    def retablir(self, a, b) -> bool:
        """Annule « pas des doublons » pour cette paire."""
        with self.lock:
            pairs = self._loaded()
            pair = _pair(a, b)
            if pair not in pairs:
                return False
            pairs.discard(pair)
        self.save()
        return True

    def renommer(self, old, new) -> int:
        """Suit une video, ou un dossier entier, qui change de place.

        Sans cela, deplacer un dossier ferait reapparaitre tous les faux
        doublons qu'il contient. Rend le nombre de paires touchees.
        """
        before, after = _key(old), _key(new)
        if before == after:
            return 0
        with self.lock:
            pairs = self._loaded()
            moved = [pair for pair in pairs
                     if _under(pair[0], before) or _under(pair[1], before)]
            for pair in moved:
                pairs.discard(pair)
                a, b = (after + side[len(before):] if _under(side, before) else side
                        for side in pair)
                if a != b:
                    pairs.add(_pair(a, b))
        if moved:
            self.save()
        return len(moved)

    def oublier(self, path) -> int:
        """Oublie les paires d'une video, ou d'un dossier, supprime pour de
        bon : elles ne pourraient plus servir."""
        gone = _key(path)
        with self.lock:
            pairs = self._loaded()
            dropped = [pair for pair in pairs
                       if _under(pair[0], gone) or _under(pair[1], gone)]
            for pair in dropped:
                pairs.discard(pair)
        if dropped:
            self.save()
        return len(dropped)

    # -- application aux groupes ------------------------------------------
    def filtrer_groupes(self, groups: list) -> list:
        """Retire des groupes ce qu'on a declare different.

        Un groupe est un ensemble de videos semblables deux a deux. On y
        retire les liens declares faux, puis on garde chaque morceau encore
        relie d'au moins deux videos : A, B et C de meme taille, avec « A et
        B ne sont pas des doublons », restent ensemble tant que C ressemble
        aux deux -- c'est C qui est en trop. Un groupe dont tous les liens
        ont ete refuses disparait.

        Accepte les groupes rendus par `dupes` comme de simples
        (taille, chemins) : chaque morceau garde la forme de son groupe.
        """
        return filter_groups(groups, self.snapshot())

    # Noms anglais, pour qui lit le reste du code.
    ignore = ignorer
    is_ignored = est_ignoree
    filter_groups = filtrer_groupes


def filter_groups(groups: list, known) -> list:
    """`NotDupes.filtrer_groupes`, avec des paires deja figees."""
    if not known:
        return list(groups)
    out = []
    for group in groups:
        paths = list(group[1])
        keys = [_key(p) for p in paths]
        if not any(_pair(a, b) in known
                   for at, a in enumerate(keys) for b in keys[at + 1:]):
            out.append(group)
            continue
        for part in _components(keys, known):
            if len(part) < 2:
                continue
            subset = getattr(group, "subset", None)
            if subset is not None:
                out.append(subset(part))
            else:
                out.append((group[0], [paths[i] for i in part]))
    return out


def pair_test(ignored):
    """Une fonction (a, b) -> vrai si la paire est declaree differente.

    `ignored` vaut None pour la memoire de l'application, un ensemble de
    paires deja figees (`snapshot`), un objet qui sait `est_ignoree`, ou
    un ensemble vide pour ne rien exclure.
    """
    if ignored is None:
        ignored = NOT_DUPES.snapshot()
    if hasattr(ignored, "est_ignoree"):
        return ignored.est_ignoree
    known = frozenset(ignored)
    if not known:
        return lambda _a, _b: False
    return lambda a, b: _pair(a, b) in known


def _components(keys: list, known) -> list:
    """Morceaux d'un groupe une fois retires les liens refuses. Indices."""
    parent = list(range(len(keys)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a in range(len(keys)):
        for b in range(a + 1, len(keys)):
            if _pair(keys[a], keys[b]) not in known:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
    parts: dict = {}
    for index in range(len(keys)):
        parts.setdefault(find(index), []).append(index)
    return sorted(parts.values())


class LookMemo:
    """L'empreinte de chaque vignette deja examinee, par nom de vignette.

    Le nom d'une vignette resume la video, sa taille, sa date, l'instant et
    la largeur : a nom egal, image egale, donc empreinte egale -- pour
    toujours. On peut la retenir sans jamais la revalider.

    Une image plate, sans empreinte, est retenue aussi (valeur 0) : sinon
    on la redecoderait a chaque recherche pour rien.
    """

    FILE = "empreintes-vignettes.json"
    # Au-dela, on oublie les plus anciennes : cent mille videos tiennent
    # largement en dessous, et un fichier sans borne finirait par couter
    # plus a relire que les decodages qu'il epargne.
    MAX = 300_000
    MISSING = object()

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path else None
        self.values: dict | None = None
        self.dirty = False

    @property
    def path(self) -> Path:
        return self._path or (config.APP_DIR / self.FILE)

    def load(self) -> None:
        if self.values is not None:
            return
        values: dict = {}
        text = config._read_text(self.path)
        raw = config._parse_object(text) if isinstance(text, str) else {}
        if raw is None:
            # Un cache se recalcule, mais on garde le fichier abime de cote
            # plutot que de l'ecraser sans rien dire.
            config._set_aside(self.path)
            raw = {}
        if isinstance(raw, dict):
            for name, text in (raw.get("looks") or {}).items():
                try:
                    values[str(name)] = int(text, 16)
                except (TypeError, ValueError):
                    continue
        self.values = values

    def get(self, name: str):
        """L'empreinte retenue (None pour une image plate), ou MISSING."""
        self.load()
        found = self.values.get(name)
        if found is None:
            return self.MISSING
        return found or None

    def put(self, name: str, value: int | None) -> None:
        self.load()
        self.values[name] = int(value or 0)
        self.dirty = True

    def save(self) -> bool:
        if not self.dirty or self.values is None:
            return True
        if len(self.values) > self.MAX:
            # Les plus anciennes d'abord : le dictionnaire garde l'ordre
            # d'insertion, et ce qui vient d'etre vu resservira le premier.
            for name in list(self.values)[:len(self.values) - self.MAX]:
                del self.values[name]
        try:
            _write_json(self.path, {"version": 1, "looks": {
                name: f"{value:x}" for name, value in self.values.items()}})
        except OSError:
            return False
        self.dirty = False
        return True


# La memoire des faux doublons, unique pour toute l'application.
NOT_DUPES = NotDupes()
