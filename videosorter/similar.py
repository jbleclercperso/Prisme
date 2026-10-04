"""« Que des vidéos qui ressemblent à celle-ci » : les voisines d'une video.

L'index du Labo IA garde l'empreinte de quelques images de chaque video. Leur
moyenne fait l'empreinte de la video entiere ; deux videos qui se ressemblent
ont des empreintes proches, quels que soient leurs dossiers. Le nom compte un
peu (des mots en commun rapprochent), jamais seul.

Aucun moteur a charger : tout est deja calcule par l'indexation du Labo. Les
empreintes des videos se gardent a cote de l'index (<moteur>.videos.npy) et
se refont quand il a change.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

# Au-dela, c'est la meme video (une copie) : pas une voisine.
SAME_VIDEO = 0.995
# Ce que pese un nom en commun, a cote de l'image (de 0 a 1).
NAME_WEIGHT = 0.06
_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

_lock = threading.Lock()
_space = None


def _words(path: str) -> frozenset:
    stem = os.path.splitext(os.path.basename(path))[0]
    return frozenset(word.lower() for word in _WORD.findall(stem))


class VideoSpace:
    """Une empreinte par video indexee, normalisee, dans un tableau numpy."""

    def __init__(self, folder: Path, engine: str):
        import numpy as np
        self.np = np
        self.engine = engine
        self.folder = Path(folder)
        self.paths: list = []
        self.where: dict = {}
        self.matrix = np.zeros((0, 0), dtype="float16")
        self._words: list = []

    # -- construction -----------------------------------------------------------
    def _source(self) -> tuple:
        head = self.folder / f"{self.engine}.json"
        body = self.folder / f"{self.engine}.bin"
        stat = body.stat()
        return head, body, f"{stat.st_size}:{int(stat.st_mtime)}"

    def load(self, cache: bool = True) -> None:
        np = self.np
        head, body, signature = self._source()
        cached = self.folder / f"{self.engine}.videos.npy"
        listing = self.folder / f"{self.engine}.videos.json"
        try:
            meta = json.loads(listing.read_text(encoding="utf-8"))
            if meta.get("source") == signature:
                self.paths = list(meta["paths"])
                self.matrix = np.load(str(cached), mmap_mode=None)
                self._finish()
                return
        except (OSError, ValueError, KeyError):
            pass
        info = json.loads(head.read_text(encoding="utf-8"))
        rows = info["rows"]
        width = int(info["width"])
        frames = np.memmap(str(body), dtype=info.get("dtype", "float32"), mode="r",
                           shape=(len(rows), width))
        ids, paths, seen = [], [], {}
        for video, _ts in rows:
            number = seen.get(video)
            if number is None:
                number = seen[video] = len(paths)
                paths.append(video)
            ids.append(number)
        ids = np.asarray(ids, dtype=np.int64)
        sums = np.zeros((len(paths), width), dtype=np.float64)
        counts = np.bincount(ids, minlength=len(paths)).astype(np.float64)
        step = 40000
        for start in range(0, len(rows), step):
            chunk = np.asarray(frames[start:start + step], dtype=np.float32)
            chunk_ids = ids[start:start + step]
            # Les images d'une video se suivent : on additionne par tranches
            # contigues, puis une seule addition par tranche.
            cuts = np.flatnonzero(np.diff(chunk_ids)) + 1
            starts = np.concatenate(([0], cuts))
            np.add.at(sums, chunk_ids[starts], np.add.reduceat(chunk, starts, axis=0))
        del frames
        means = sums / np.maximum(counts, 1.0)[:, None]
        norms = np.linalg.norm(means, axis=1, keepdims=True)
        means = (means / np.maximum(norms, 1e-9)).astype(np.float16)
        self.paths, self.matrix = paths, means
        if not cache:
            self._finish()
            return
        try:
            np.save(str(cached), means)
            listing.write_text(json.dumps({"source": signature, "paths": paths}),
                               encoding="utf-8")
        except OSError:
            pass
        self._finish()

    def _finish(self) -> None:
        self.where = {os.path.normcase(p): i for i, p in enumerate(self.paths)}
        self._words = [_words(p) for p in self.paths]

    # -- usage --------------------------------------------------------------------
    def knows(self, path) -> bool:
        return os.path.normcase(str(path)) in self.where

    def nearest(self, seed, keep=None, limit: int = 3000) -> list:
        """Les videos les plus proches de `seed`, de la plus ressemblante a la
        moins : [(chemin, score)]. `keep(chemin)` ecarte ce qu'on ne veut pas."""
        np = self.np
        at = self.where.get(os.path.normcase(str(seed)))
        if at is None:
            return []
        target = self.matrix[at].astype(np.float32)
        scores = np.empty(len(self.paths), dtype=np.float32)
        step = 16384
        for start in range(0, len(self.paths), step):
            scores[start:start + step] = self.matrix[start:start + step].astype(
                np.float32) @ target
        scores[at] = -2.0
        scores[scores > SAME_VIDEO] = -2.0
        count = min(limit, len(scores) - 1)
        if count <= 0:
            return []
        best = np.argpartition(-scores, count)[:count]
        mine = self._words[at]
        ranked = []
        for index in best:
            score = float(scores[index])
            if score <= -1.0:
                continue
            if mine:
                theirs = self._words[index]
                if theirs:
                    score += NAME_WEIGHT * len(mine & theirs) / len(mine | theirs)
            ranked.append((score, self.paths[index]))
        ranked.sort(reverse=True)
        return [(path, score) for score, path in ranked
                if keep is None or keep(path)]


def _main_engine(folder: Path) -> str:
    """Le moteur dont l'index est le plus grand : c'est lui qui connait le
    plus de videos."""
    best, size = "", -1
    try:
        for body in Path(folder).glob("*.bin"):
            if body.stem.endswith(".videos"):
                continue
            if (body.with_suffix(".json")).exists() and body.stat().st_size > size:
                best, size = body.stem, body.stat().st_size
    except OSError:
        pass
    return best


def space() -> VideoSpace | None:
    """L'espace des videos, construit une fois (quelques secondes la premiere
    fois, puis lu tel quel). Hors du fil de l'interface."""
    global _space
    from .ia import LAB_DIR
    with _lock:
        engine = _main_engine(LAB_DIR)
        if not engine:
            return None
        if _space is not None and _space.engine == engine:
            try:
                if _space._source()[2] == getattr(_space, "signature", None):
                    return _space
            except OSError:
                return _space
        built = VideoSpace(LAB_DIR, engine)
        built.load()
        try:
            built.signature = built._source()[2]
        except OSError:
            built.signature = None
        _space = built
        return built
