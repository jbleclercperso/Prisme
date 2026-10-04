"""Qui s'est connecté à distance, et ce qu'il a regardé.

Ouvrir sa bibliothèque au dehors n'a de sens que si l'on peut regarder par
la fenêtre : qui est entré, depuis où, quand, et ce qu'il a vu — pendant
combien de temps, pas seulement « il a cliqué dessus ».

Le journal vit dans son propre fichier, à part de l'index. Deux raisons : il
n'a rien à faire dans le cache que l'on recopie d'une machine à l'autre, et
ce qu'il contient regarde une seule personne.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import APP_DIR

LOG_PATH = APP_DIR / "acces.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS visits(
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    at    REAL NOT NULL,
    ip    TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    event TEXT NOT NULL DEFAULT 'entree'
);
CREATE TABLE IF NOT EXISTS views(
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started REAL NOT NULL,
    seen_at REAL NOT NULL,
    ip      TEXT NOT NULL DEFAULT '',
    label   TEXT NOT NULL DEFAULT '',
    video   TEXT NOT NULL DEFAULT '',
    name    TEXT NOT NULL DEFAULT '',
    seconds REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS views_by_time ON views(seen_at);
CREATE TABLE IF NOT EXISTS favorites(
    label TEXT NOT NULL,
    video TEXT NOT NULL,
    name  TEXT NOT NULL DEFAULT '',
    at    REAL NOT NULL,
    PRIMARY KEY (label, video)
);
"""

# Un battement plus vieux que cela ouvre une nouvelle ligne : on ne veut pas
# additionner la seance d'hier soir et celle de ce matin sur la meme video.
GAP = 600.0
# Un battement ne peut pas valoir plus que cela, quoi qu'en dise le navigateur.
# Sans ce plafond, un onglet laisse ouvert toute la nuit rapporterait huit
# heures de visionnage.
MAX_BEAT = 60.0


def describe(ip: str, agent: str) -> str:
    """Un nom court et stable pour un visiteur, sans prétendre l'identifier.

    On ne sait pas qui c'est — on sait seulement que c'est le même appareil
    que la dernière fois. Le nom dit l'essentiel : d'où, avec quoi.
    """
    agent = agent or ""
    if "Android" in agent:
        what = "Android"
    elif "iPhone" in agent or "iPad" in agent:
        what = "iPhone" if "iPhone" in agent else "iPad"
    elif "Macintosh" in agent or "Mac OS" in agent:
        what = "Mac"
    elif "Windows" in agent:
        what = "Windows"
    elif "Linux" in agent:
        what = "Linux"
    else:
        what = "appareil"
    for name in ("Firefox", "Edg", "Chrome", "Safari"):
        if name in agent:
            what += " · " + ("Edge" if name == "Edg" else name)
            break
    # Quatre caracteres tires de l'appareil : deux visiteurs Android depuis
    # la meme adresse ne se confondent plus.
    tag = hashlib.sha1(agent.encode("utf-8", "replace")).hexdigest()[:4]
    return f"{what} ({tag})"


class Journal:
    """Le journal, ouvert une fois, écrit depuis plusieurs fils."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else LOG_PATH
        self.lock = threading.Lock()
        self.db = None
        self.open()

    def open(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.db.executescript(SCHEMA)
            self.db.commit()
        except sqlite3.Error:
            self.db = None

    def reopen(self, path: Path) -> None:
        self.close()
        self.path = Path(path)
        self.open()

    def close(self) -> None:
        with self.lock:
            if self.db is not None:
                try:
                    self.db.close()
                except sqlite3.Error:
                    pass
                self.db = None

    # -- ecriture ---------------------------------------------------------
    def entered(self, ip: str, agent: str, event: str = "entree") -> str:
        """Note une entrée — ou un essai refusé — et rend le nom du visiteur."""
        label = describe(ip, agent)
        with self.lock:
            if self.db is not None:
                try:
                    self.db.execute(
                        "INSERT INTO visits(at, ip, agent, label, event)"
                        " VALUES (?,?,?,?,?)",
                        (time.time(), ip, (agent or "")[:300], label, event))
                    self.db.commit()
                except sqlite3.Error:
                    pass
        return label

    def watched(self, ip: str, label: str, video: str, name: str,
                seconds: float) -> None:
        """Ajoute du temps de visionnage à la séance en cours, ou en ouvre une.

        Le client envoie ce qui s'est écoulé depuis son dernier signe de vie.
        On le plafonne : un onglet oublié ne doit pas compter pour la nuit.
        """
        seconds = max(0.0, min(float(seconds or 0.0), MAX_BEAT))
        if not video:
            return
        now = time.time()
        with self.lock:
            if self.db is None:
                return
            try:
                row = self.db.execute(
                    "SELECT id, seen_at FROM views WHERE label = ? AND video = ?"
                    " ORDER BY seen_at DESC LIMIT 1", (label, video)).fetchone()
                if row is not None and now - float(row[1]) <= GAP:
                    self.db.execute(
                        "UPDATE views SET seconds = seconds + ?, seen_at = ?"
                        " WHERE id = ?", (seconds, now, row[0]))
                else:
                    self.db.execute(
                        "INSERT INTO views(started, seen_at, ip, label, video,"
                        " name, seconds) VALUES (?,?,?,?,?,?,?)",
                        (now, now, ip, label, video, name, seconds))
                self.db.commit()
            except sqlite3.Error:
                pass

    # -- lecture ------------------------------------------------------------
    def visits(self, limit: int = 200) -> list:
        return self._rows(
            "SELECT at, ip, label, event FROM visits ORDER BY at DESC LIMIT ?",
            (limit,))

    def views(self, limit: int = 500) -> list:
        """(quand, ip, appareil, nom, secondes, empreinte de la video)."""
        return self._rows(
            "SELECT seen_at, ip, label, name, seconds, video FROM views"
            " ORDER BY seen_at DESC LIMIT ?", (limit,))

    def _rows(self, sql: str, args: tuple) -> list:
        with self.lock:
            if self.db is None:
                return []
            try:
                return self.db.execute(sql, args).fetchall()
            except sqlite3.Error:
                return []

    def seen_count(self, labels: list, minimum: float) -> int:
        """Combien de videos differentes ces appareils ont regardees, chacune
        au moins `minimum` secondes en tout."""
        labels = [label for label in labels if label][:20]
        if not labels:
            return 0
        marks = ",".join("?" * len(labels))
        rows = self._rows(
            "SELECT COUNT(*) FROM (SELECT video FROM views"
            f" WHERE label IN ({marks}) GROUP BY video HAVING SUM(seconds) >= ?)",
            (*labels, float(minimum)))
        return int(rows[0][0]) if rows else 0

    # -- les favoris de chaque appareil -------------------------------------
    def set_favorite(self, label: str, video: str, name: str, on: bool) -> None:
        with self.lock:
            if self.db is None:
                return
            try:
                if on:
                    self.db.execute(
                        "INSERT OR REPLACE INTO favorites(label, video, name, at)"
                        " VALUES (?,?,?,?)", (label, video, name, time.time()))
                else:
                    self.db.execute("DELETE FROM favorites WHERE label = ? AND video = ?",
                                    (label, video))
                self.db.commit()
            except sqlite3.Error:
                pass

    def favorites(self, label: str) -> list:
        """Les empreintes des favoris de cet appareil, du plus recent au plus ancien."""
        return [row[0] for row in self._rows(
            "SELECT video FROM favorites WHERE label = ? ORDER BY at DESC LIMIT ?",
            (label, 5000))]

    def all_favorites(self, limit: int = 5000) -> list:
        """(quand, appareil, nom, empreinte) de tous les favoris."""
        return self._rows("SELECT at, label, name, video FROM favorites"
                          " ORDER BY at DESC LIMIT ?", (limit,))

    def clear_views(self) -> None:
        """Efface ce qui a ete regarde, et garde les connexions."""
        with self.lock:
            if self.db is None:
                return
            try:
                self.db.execute("DELETE FROM views")
                self.db.commit()
            except sqlite3.Error:
                pass

    def clear(self) -> None:
        with self.lock:
            if self.db is None:
                return
            try:
                self.db.execute("DELETE FROM visits")
                self.db.execute("DELETE FROM views")
                self.db.commit()
            except sqlite3.Error:
                pass


def favorites_in(path: Path, limit: int = 5000) -> list:
    """Les favoris notes dans un autre journal -- celui du NAS."""
    return _rows_in(path, "SELECT at, label, name, video FROM favorites"
                          " ORDER BY at DESC LIMIT ?", limit)


def visits_in(path: Path, limit: int = 200) -> list:
    """Les connexions notees dans un autre journal -- celui du NAS."""
    return _rows_in(path, "SELECT at, ip, label, event FROM visits"
                          " ORDER BY at DESC LIMIT ?", limit)


def views_in(path: Path, limit: int = 500) -> list:
    """Les visionnages notes dans un autre journal -- celui du NAS."""
    return _rows_in(path, "SELECT seen_at, ip, label, name, seconds, video FROM views"
                          " ORDER BY seen_at DESC LIMIT ?", limit)


def _rows_in(path: Path, sql: str, limit: int) -> list:
    """Des lignes d'un autre journal, lues dans une copie : ouvrir en direct,
    par le reseau, une base que le serveur du NAS est en train d'ecrire
    risquait de la verrouiller sous lui."""
    import os
    import shutil
    import tempfile
    try:
        handle, copy = tempfile.mkstemp(suffix=".db", prefix="prisme-nas-")
        os.close(handle)
        shutil.copyfile(path, copy)
    except OSError:
        return []
    try:
        con = sqlite3.connect(copy)
        try:
            return con.execute(sql, (limit,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    finally:
        try:
            os.remove(copy)
        except OSError:
            pass


def when(moment: float) -> str:
    return datetime.fromtimestamp(moment).strftime("%d/%m %H:%M")


def spell(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60:02d}"
    return f"{seconds // 3600} h {(seconds % 3600) // 60:02d}"


JOURNAL = Journal()
