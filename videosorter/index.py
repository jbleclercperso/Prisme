"""Index persistant : ce qu'on sait deja du disque, d'un lancement a l'autre.

L'application repartait chaque fois du disque : avant le moindre affichage, il
fallait parcourir recursivement les six cents dossiers de la racine, une minute
et demie sur un partage reseau. Un cache existait, mais il n'etait ecrit qu'a la
toute fin d'une analyse complete -- or on ferme, on entre dans un dossier, on
change d'onglet bien avant. Le travail etait donc systematiquement jete, et
chaque lancement repayait la minute et demie.

Ce module renverse la charge : **ce qu'on a appris est ecrit au fil de l'eau**,
et l'affichage repart de la sans toucher au disque. Le disque n'est relu qu'en
tache de fond, apres coup, pour rattraper ce qui a bouge.

SQLite plutot qu'un JSON : un fichier JSON se relit et se reecrit en entier. A
trente mille dossiers portant chacun leurs chemins de videos, la seule
sauvegarde coutait plus que l'analyse evitee -- ce qui obligeait justement a ne
l'ecrire qu'une fois, a la fin. Ici chaque dossier analyse est enregistre
aussitot, une analyse interrompue garde tout ce qu'elle a appris, et relire une
racine est une requete indexee au lieu d'un fichier entier.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import APP_DIR, INDEX_PATH

# Au-dela, les dossiers les moins recemment consultes sont oublies.
MAX_FOLDERS = 60000
MAX_PROBES = 200000

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders(
    id           TEXT PRIMARY KEY,
    path         TEXT NOT NULL,
    sig          TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'folders',
    loose        INTEGER NOT NULL DEFAULT 0,
    size         INTEGER NOT NULL DEFAULT 0,
    file_count   INTEGER NOT NULL DEFAULT 0,
    video_count  INTEGER NOT NULL DEFAULT 0,
    subdir_count INTEGER NOT NULL DEFAULT 0,
    mtime        REAL    NOT NULL DEFAULT 0,
    videos       TEXT    NOT NULL DEFAULT '',
    used         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS expansions(
    path     TEXT PRIMARY KEY,
    sig      TEXT NOT NULL,
    loose    INTEGER NOT NULL DEFAULT 0,
    children TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS listings(
    root  TEXT NOT NULL,
    mode  TEXT NOT NULL DEFAULT '',
    ids   TEXT NOT NULL DEFAULT '',
    stamp REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (root, mode)
);
CREATE TABLE IF NOT EXISTS titles(
    path  TEXT PRIMARY KEY,
    stamp TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS seen(
    id TEXT PRIMARY KEY,
    at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS probes(
    path     TEXT PRIMARY KEY,
    stamp    TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0,
    width    INTEGER NOT NULL DEFAULT 0,
    height   INTEGER NOT NULL DEFAULT 0,
    codec    TEXT NOT NULL DEFAULT '',
    ok       INTEGER NOT NULL DEFAULT 0
);
"""


class Index:
    """Table {element: statistiques}, ecrite au fil de l'eau.

    Une seule connexion, partagee entre le fil de l'interface et celui de la
    reconciliation, protegee par un verrou : les ecritures sont breves et
    groupees, la contention est negligeable devant une lecture reseau.
    """

    # Les ecritures sont validees par paquets : une transaction par dossier
    # ferait un fsync par dossier, plus cher que l'analyse elle-meme.
    COMMIT_EVERY = 200
    COMMIT_AFTER = 2.0     # secondes

    def __init__(self, path: Path = INDEX_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.db: sqlite3.Connection | None = None
        self.pending = 0
        # Vrai quand le fichier a du etre refait : la collection sera
        # reanalysee une fois, et l'application doit pouvoir le dire.
        self.rebuilt = False
        self.last_commit = time.monotonic()
        # Les sondages ffprobe tiennent en memoire : ils sont consultes des
        # dizaines de milliers de fois par tri (durees, resolutions, filtres) et
        # une requete par consultation couterait plus que le service rendu.
        self.probes: dict = {}
        self.open()

    # -- ouverture ------------------------------------------------------
    def open(self) -> None:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, check_same_thread=False,
                                      timeout=10.0)
            # WAL : un lecteur ne bloque pas l'ecrivain, l'interface n'attend
            # donc jamais la reconciliation qui tourne derriere.
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            self.db.commit()
        except sqlite3.Error:
            # Meme un fichier qu'on n'arrive pas a ouvrir se refait : c'est un
            # cache, rien d'irremplacable n'y dort.
            self._rebuild()
            if self.db is None:
                return
        if not self._sound():
            # Un index abime ne se signale pas : chaque lecture rend « rien de
            # connu », l'application reanalyse tout, et recommence au lancement
            # suivant — indefiniment, sans qu'un seul message ne l'explique.
            # Mieux vaut le refaire une fois et le dire.
            self._rebuild()
            if self.db is None:
                return
        self._load_probes()
        self._load_seen()
        self._load_titles()
        self._migrate_json()

    def _sound(self) -> bool:
        """Verifie que le fichier est lisible, et pas seulement ouvrable."""
        if self.db is None:
            return False
        try:
            row = self.db.execute("PRAGMA quick_check(1)").fetchone()
            if not row or row[0] != "ok":
                return False
            # `quick_check` ne relit pas forcement chaque table : on en touche
            # une, c'est la ou l'abime s'etait loge.
            self.db.execute("SELECT COUNT(*) FROM folders").fetchone()
            return True
        except sqlite3.Error:
            return False

    def _rebuild(self) -> None:
        """Repart d'un index vide, apres avoir efface celui qui est abime."""
        try:
            if self.db is not None:
                self.db.close()
        except sqlite3.Error:
            pass
        self.db = None
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(self.path) + suffix).unlink()
            except OSError:
                pass
        try:
            self.db = sqlite3.connect(self.path, check_same_thread=False,
                                      timeout=10.0)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            self.db.commit()
            self.rebuilt = True
        except sqlite3.Error:
            self.db = None

    def _load_titles(self) -> None:
        self.titles: dict = {}
        if self.db is None:
            return
        try:
            for path, title in self.db.execute("SELECT path, title FROM titles"):
                self.titles[path] = title
        except sqlite3.Error:
            pass

    def put_title(self, path, stamp: str, title: str) -> None:
        """Le titre des metadonnees — vide quand la video n'en porte pas, ce
        qui vaut « sonde, rien trouve » et evite de le redemander."""
        key = str(path)
        self.titles[key] = title
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO titles(path, stamp, title) VALUES (?,?,?)",
                    (key, stamp or "", title or ""))
                self.db.commit()
            except sqlite3.Error:
                pass

    def has_title(self, path) -> bool:
        return str(path) in self.titles

    def title_of(self, path) -> str:
        return self.titles.get(str(path), "")

    def _load_seen(self) -> None:
        self.seen = set()
        if self.db is None:
            return
        try:
            self.seen = {row[0] for row in self.db.execute("SELECT id FROM seen")}
        except sqlite3.Error:
            pass

    def mark_seen(self, item_id: str) -> None:
        """Regarde plus de quelques secondes : on s'en souviendra."""
        if item_id in self.seen:
            return
        self.seen.add(item_id)
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute("INSERT OR REPLACE INTO seen(id, at) VALUES (?, ?)",
                                (item_id, time.time()))
                self.db.commit()
            except sqlite3.Error:
                pass

    def is_seen(self, item_id: str) -> bool:
        return item_id in self.seen

    def _load_probes(self) -> None:
        if self.db is None:
            return
        try:
            rows = self.db.execute(
                "SELECT path, stamp, duration, width, height, codec, ok FROM probes"
            ).fetchall()
        except sqlite3.Error:
            return
        for path, stamp, duration, width, height, codec, ok in rows:
            self.probes[path] = (stamp, {
                "duration": duration, "width": width, "height": height,
                "codec": codec, "ok": bool(ok),
            })

    def _migrate_json(self) -> None:
        """Recupere les sondages de l'ancien cache JSON, puis s'en debarrasse.

        Un sondage est du vrai travail -- un ffprobe par video, sur le reseau.
        Le perdre en changeant de format aurait fait repayer ce qui etait acquis.
        """
        from .config import PROBE_CACHE_PATH, SCAN_CACHE_PATH
        if PROBE_CACHE_PATH.exists():
            try:
                raw = json.loads(PROBE_CACHE_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            for key, info in (raw or {}).items():
                # Ancienne cle : "chemin|mtime|taille".
                parts = str(key).rsplit("|", 2)
                if len(parts) != 3 or not isinstance(info, dict):
                    continue
                path, stamp = parts[0], f"{parts[1]}|{parts[2]}"
                if path in self.probes:
                    continue
                self.put_probe(path, stamp, info)
            self.commit(force=True)
            try:
                PROBE_CACHE_PATH.unlink()
            except OSError:
                pass
        # L'ancien cache d'analyse ne gardait que des empreintes, refaites en
        # quelques secondes : rien a recuperer, autant ne pas le laisser trainer.
        try:
            SCAN_CACHE_PATH.unlink()
        except OSError:
            pass

    # -- ecriture groupee -----------------------------------------------
    def _touched(self) -> None:
        self.pending += 1
        now = time.monotonic()
        if (self.pending >= self.COMMIT_EVERY
                or now - self.last_commit >= self.COMMIT_AFTER):
            self.commit()

    def commit(self, force: bool = False) -> None:
        with self.lock:
            if self.db is None or (not self.pending and not force):
                return
            try:
                self.db.commit()
            except sqlite3.DatabaseError:
                # Le fichier est abime : on le refait plutot que de continuer a
                # ecrire dans un index dont plus rien ne ressortira.
                self._rebuild()
            except sqlite3.Error:
                pass
            self.pending = 0
            self.last_commit = time.monotonic()

    # -- dossiers -------------------------------------------------------
    def _item_from_row(self, row):
        from .scan import MODE_FILES, Item
        (_id, path, _sig, kind, loose, size, file_count, video_count,
         subdir_count, mtime, videos) = row
        folder = Path(path)
        if kind == MODE_FILES:
            # Une video se porte elle-meme : rien a recomposer.
            return Item(path=folder, kind=MODE_FILES, size=size, file_count=1,
                        video_count=1, mtime=mtime, videos=[folder])
        return Item(
            path=folder, kind=kind, size=size, file_count=file_count,
            video_count=video_count, subdir_count=subdir_count, mtime=mtime,
            videos=[folder / name for name in videos.split("\n") if name],
            loose_only=bool(loose),
        )

    def folders(self, ids: list) -> dict:
        """Tout ce qu'on sait de ces elements, en une requete.

        Sert l'affichage instantane : on rend ce qu'on avait note la derniere
        fois, sans demander au disque s'il est toujours d'accord. C'est la
        reconciliation, plus tard, qui corrige ce qui a bouge.
        """
        if self.db is None or not ids:
            return {}
        found: dict = {}
        with self.lock:
            # SQLite borne le nombre de parametres d'une requete : on decoupe.
            for start in range(0, len(ids), 400):
                chunk = ids[start:start + 400]
                marks = ",".join("?" * len(chunk))
                try:
                    rows = self.db.execute(
                        "SELECT id, path, sig, kind, loose, size, file_count,"
                        " video_count, subdir_count, mtime, videos FROM folders"
                        " WHERE id IN (" + marks + ")", chunk).fetchall()
                except sqlite3.Error:
                    return found
                for row in rows:
                    found[row[0]] = self._item_from_row(row)
        return found

    def signatures(self, ids: list) -> dict:
        """Empreintes connues, pour savoir sans rien relire ce qui a bouge."""
        if self.db is None or not ids:
            return {}
        known: dict = {}
        with self.lock:
            for start in range(0, len(ids), 400):
                chunk = ids[start:start + 400]
                marks = ",".join("?" * len(chunk))
                try:
                    rows = self.db.execute(
                        "SELECT id, sig FROM folders WHERE id IN (" + marks + ")",
                        chunk).fetchall()
                except sqlite3.Error:
                    return known
                known.update(dict(rows))
        return known

    def put_folder(self, item, sig: str) -> None:
        if self.db is None or not sig:
            return
        from .scan import MODE_FILES
        folder = Path(item.path)
        names = []
        if item.kind != MODE_FILES:
            for video in item.videos:
                try:
                    names.append(str(Path(video).relative_to(folder)))
                except ValueError:
                    names.append(str(video))
        with self.lock:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO folders(id, path, sig, kind, loose,"
                    " size, file_count, video_count, subdir_count, mtime, videos,"
                    " used) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item.item_id, str(folder), sig, item.kind,
                     int(item.loose_only), item.size, item.file_count,
                     item.video_count, item.subdir_count, item.mtime,
                     "\n".join(names), int(time.time())),
                )
            except sqlite3.Error:
                return
            self._touched()

    def forget(self, folder) -> None:
        """Oublie ce dossier : l'application vient d'y toucher.

        Les dates relevees en enumerant un repertoire peuvent retarder sur la
        realite. Pour les changements que l'application fait elle-meme, on ne
        s'en remet pas a elles.
        """
        if self.db is None:
            return
        key = str(folder)
        with self.lock:
            try:
                self.db.execute("DELETE FROM folders WHERE id = ? OR id = ?",
                                (key, key + "|vrac"))
                self.db.execute("DELETE FROM expansions WHERE path = ?", (key,))
            except sqlite3.Error:
                return
            self._touched()

    # -- rayonnages -----------------------------------------------------
    def expansion(self, folder: Path, stamp: int) -> dict | None:
        """Composition connue de ce dossier de tete, si rien n'y a bouge."""
        if self.db is None or not stamp:
            return None
        with self.lock:
            try:
                row = self.db.execute(
                    "SELECT sig, loose, children FROM expansions WHERE path = ?",
                    (str(folder),)).fetchone()
            except sqlite3.Error:
                return None
        if not row or row[0] != str(stamp):
            return None
        try:
            children = [tuple(pair) for pair in json.loads(row[2])]
        except ValueError:
            return None
        return {"children": children, "loose": bool(row[1])}

    def put_expansion(self, folder: Path, stamp: int, children: list,
                      loose: bool) -> None:
        if self.db is None or not stamp:
            return
        with self.lock:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO expansions(path, sig, loose, children)"
                    " VALUES(?,?,?,?)",
                    (str(folder), str(stamp), int(loose),
                     json.dumps([[name, value] for name, value in children])),
                )
            except sqlite3.Error:
                return
            self._touched()

    # -- composition d'une racine ---------------------------------------
    def listing(self, root: Path, mode: str) -> list:
        """Ce que cette racine contenait la derniere fois, dans l'ordre."""
        if self.db is None:
            return []
        with self.lock:
            try:
                row = self.db.execute(
                    "SELECT ids FROM listings WHERE root = ? AND mode = ?",
                    (str(root), mode)).fetchone()
            except sqlite3.Error:
                return []
        return [line for line in row[0].split("\n") if line] if row else []

    def put_listing(self, root: Path, mode: str, ids: list) -> None:
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO listings(root, mode, ids, stamp)"
                    " VALUES(?,?,?,?)",
                    (str(root), mode, "\n".join(ids), time.time()),
                )
            except sqlite3.Error:
                return
            self._touched()

    def drop_listing(self, root: Path) -> None:
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute("DELETE FROM listings WHERE root = ?", (str(root),))
            except sqlite3.Error:
                return
            self._touched()

    # -- sondages ffprobe -----------------------------------------------
    def probe(self, path, stamp: str = "") -> dict | None:
        """Sondage connu de cette video. Lecture memoire, aucun acces disque.

        L'ancien cache calculait sa cle a partir d'un `stat()` : une lecture
        reseau par consultation, alors que le tri en fait des milliers rien que
        pour classer une liste.
        """
        found = self.probes.get(str(path))
        if found is None:
            return None
        if stamp and found[0] and found[0] != stamp:
            return None
        return found[1]

    def put_probe(self, path, stamp: str, info: dict) -> None:
        self.probes[str(path)] = (stamp, info)
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO probes(path, stamp, duration, width,"
                    " height, codec, ok) VALUES(?,?,?,?,?,?,?)",
                    (str(path), stamp, info.get("duration") or 0.0,
                     info.get("width") or 0, info.get("height") or 0,
                     info.get("codec") or "", int(bool(info.get("ok")))),
                )
            except sqlite3.Error:
                return
            self._touched()

    # -- entretien ------------------------------------------------------
    def clear(self) -> None:
        """Oublie tout ce qui vient du disque. Les sondages, eux, restent."""
        if self.db is None:
            return
        with self.lock:
            try:
                for table in ("folders", "expansions", "listings"):
                    self.db.execute("DELETE FROM " + table)
                self.db.commit()
            except sqlite3.Error:
                pass
            self.pending = 0

    def prune(self) -> None:
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute(
                    "DELETE FROM folders WHERE id NOT IN"
                    " (SELECT id FROM folders ORDER BY used DESC LIMIT ?)",
                    (MAX_FOLDERS,))
                self.db.execute(
                    "DELETE FROM probes WHERE rowid NOT IN"
                    " (SELECT rowid FROM probes ORDER BY rowid DESC LIMIT ?)",
                    (MAX_PROBES,))
                self.db.commit()
            except sqlite3.Error:
                pass

    def reopen(self, path: Path, migrate: bool = False) -> None:
        """Repointe l'index vers un autre fichier, sans changer d'objet.

        Les modules gardent une reference sur l'index : le remplacer en laisserait
        certains parler a l'ancien. Les tests s'en servent pour travailler a
        l'ecart de l'index de l'utilisateur.
        """
        self.close()
        self.path = Path(path)
        self.probes = {}
        self.pending = 0
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(self.path, check_same_thread=False,
                                      timeout=10.0)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript(SCHEMA)
            self.db.commit()
        except sqlite3.Error:
            self.db = None
            return
        self._load_probes()
        self._load_seen()
        self._load_titles()
        if migrate:
            self._migrate_json()

    def health(self) -> str:
        """Un mot sur l'etat de l'index, pour le dire a qui regarde."""
        if self.db is None:
            return "indisponible"
        if self.rebuilt:
            return "refait (il etait abime)"
        return "ok"

    def count_folders(self) -> int:
        if self.db is None:
            return 0
        with self.lock:
            try:
                return self.db.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
            except sqlite3.Error:
                return 0

    def close(self) -> None:
        self.commit(force=True)
        with self.lock:
            if self.db is not None:
                try:
                    self.db.close()
                except sqlite3.Error:
                    pass
                self.db = None

    def size_on_disk(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


INDEX = Index()
