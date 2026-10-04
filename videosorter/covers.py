"""Les couvertures des dossiers changent, sans jamais ralentir l'affichage.

Une carte de dossier montrait toujours la meme image : celle de sa premiere
video. Elle change desormais de temps en temps, selon le principe des
applications de photos (« stale-while-revalidate ») :

- une carte ne montre **que** des images deja faites : l'affichage n'attend
  jamais une extraction ;
- pendant qu'on regarde une carte, sa **prochaine** couverture se prepare en
  fond, une a la fois, en cedant la place des que Prisme demande une image ;
- quand son tour vient, la carte passe a cette couverture -- si elle est
  prete ; sinon elle garde l'actuelle, et reessaiera ;
- chaque dossier a sa propre horloge, decalee des autres : les couvertures
  changent quelques-unes a la fois, jamais toutes ensemble.

Seuls les dossiers qu'on regarde coutent une extraction : ceux de la page
affichee, fenetre active, une par tour (`period`, reglable). L'etat (couverture actuelle, prochaine prete) tient dans un petit
fichier, a cote des reglages.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from collections import deque
from pathlib import Path

from .config import PRIVATE_DIR

COVERS_PATH = PRIVATE_DIR / "couvertures.json"
PERIOD_S = 40.0              # une nouvelle couverture toutes les ~40 s, par dossier
MAX_WAITING = 200            # au-dela, les plus anciennes demandes tombent
SAVE_EVERY_S = 15.0
PAUSE_BETWEEN_S = 0.5        # entre deux extractions : un travail de fond


def _fraction(text: str, salt: str) -> float:
    """Un nombre de 0 a 1, propre a chaque dossier : son decalage d'horloge."""
    digest = hashlib.sha1(f"{salt}|{text}".encode("utf-8", "replace")).digest()
    return int.from_bytes(digest[:4], "big") / 2 ** 32


class Covers:
    """L'etat des couvertures : {dossier: {"i": actuelle, "t": depuis, "n": prete}}."""

    def __init__(self, path: Path = COVERS_PATH, period: float = PERIOD_S):
        self.path = Path(path)
        self.period = period
        self._lock = threading.Lock()
        self._state: dict | None = None
        self._dirty = False
        self._saved_at = 0.0
        self._waiting: deque = deque()          # (dossier, index, video)
        self._queued: set = set()
        self._worker: threading.Thread | None = None
        self._stop = False
        self.make = None                        # (video) -> bool : fabrique l'image
        self.wait_quiet = None                  # () -> None : cede la place

    # -- l'etat, sur le disque -------------------------------------------------
    def _load(self) -> dict:
        if self._state is None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._state = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._state = {}
        return self._state

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not self._dirty or (not force and
                                   time.monotonic() - self._saved_at < SAVE_EVERY_S):
                return
            text = json.dumps(self._load(), separators=(",", ":"))
            self._dirty = False
            self._saved_at = time.monotonic()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            spare = self.path.with_suffix(".tmp")
            spare.write_text(text, encoding="utf-8")
            os.replace(spare, self.path)
        except OSError:
            pass

    # -- le choix de l'image -----------------------------------------------------
    def _entry(self, key: str, count: int) -> dict:
        state = self._load()
        entry = state.get(key)
        if not isinstance(entry, dict) or not (0 <= int(entry.get("i", 0)) < count):
            # Premiere fois : l'image d'hier (la premiere video), et une
            # horloge deja entamee d'une part propre au dossier -- les
            # premiers changements s'etalent sur l'heure, au lieu d'arriver
            # tous ensemble dans une heure.
            entry = {"i": 0, "t": time.time() - self.period * _fraction(key, "debut"),
                     "n": -1}
            state[key] = entry
            self._dirty = True
        return entry

    def _due(self, key: str, entry: dict) -> bool:
        wait = self.period * (0.75 + 0.5 * _fraction(key, "rythme"))
        return time.time() - float(entry.get("t", 0)) >= wait

    def current(self, key: str, count: int) -> int:
        """L'index de la couverture a montrer, sans rien changer."""
        if count < 2:
            return 0
        with self._lock:
            return int(self._entry(key, count)["i"])

    def show(self, key: str, videos: list) -> int:
        """L'index de la couverture a montrer maintenant. Passe a la suivante
        si son tour est venu et qu'elle est prete ; sinon la prepare."""
        count = len(videos)
        if count < 2:
            return 0
        with self._lock:
            entry = self._entry(key, count)
            ready = int(entry.get("n", -1))
            if 0 <= ready < count and ready != entry["i"] and self._due(key, entry):
                entry["i"], entry["t"], entry["n"] = ready, time.time(), -1
                self._dirty = True
            shown = int(entry["i"])
            # Une extraction ratee (fichier abime, NAS absent) ne se retente
            # qu'au tour suivant, pas a chaque affichage.
            wanted = (int(entry.get("n", -1)) < 0
                      and time.time() - float(entry.get("f", 0)) >= self.period)
        if wanted:
            self._prepare(key, videos, shown)
        self.save()
        return shown

    def due_ready(self, key: str, count: int) -> bool:
        """Cette carte changerait-elle d'image si on la redemandait ?"""
        if count < 2:
            return False
        with self._lock:
            entry = self._load().get(key)
            if not isinstance(entry, dict):
                return False
            ready = int(entry.get("n", -1))
            return (0 <= ready < count and ready != entry.get("i")
                    and self._due(key, entry))

    def _next_index(self, key: str, count: int, shown: int) -> int:
        """Une autre video du dossier, au hasard -- mais pas celles d'il y a
        peu : un tour complet avant de revoir la meme."""
        order = list(range(count))
        random.Random(key).shuffle(order)
        return order[(order.index(shown) + 1) % count] if shown in order else order[0]

    # -- la preparation, en fond -------------------------------------------------
    def _prepare(self, key: str, videos: list, shown: int) -> None:
        if self.make is None:
            return
        index = self._next_index(key, len(videos), shown)
        with self._lock:
            if key in self._queued:
                return
            self._queued.add(key)
            # Ce qu'on regarde a l'instant passe devant ce qu'on a vu avant.
            self._waiting.appendleft((key, index, str(videos[index])))
            while len(self._waiting) > MAX_WAITING:
                dropped = self._waiting.pop()
                self._queued.discard(dropped[0])
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, daemon=True,
                                                name="prisme-couvertures")
                self._worker.start()

    def _run(self) -> None:
        while not self._stop:
            with self._lock:
                if not self._waiting:
                    self._worker = None
                    return
                key, index, video = self._waiting.popleft()
            if self.wait_quiet is not None:
                self.wait_quiet()
            if self._stop:
                return
            try:
                made = bool(self.make(video))
            except Exception:                           # noqa: BLE001
                made = False
            with self._lock:
                self._queued.discard(key)
                entry = self._load().get(key)
                if isinstance(entry, dict):
                    if made and entry.get("i") != index:
                        entry["n"] = index
                    elif not made:
                        entry["f"] = time.time()
                    self._dirty = True
            self.save()
            time.sleep(PAUSE_BETWEEN_S)

    def stop(self) -> None:
        self._stop = True
        self.save(force=True)


COVERS = Covers()
