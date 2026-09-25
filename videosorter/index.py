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

import datetime
import json
import os
import shutil
import sqlite3
import threading
import time
from array import array
from pathlib import Path

from . import config as _config
from .config import INDEX_PATH, TRASH_FOLDER_NAME

# Au-dela, les dossiers les moins recemment ecrits sont oublies -- mais jamais
# ceux qu'une racine connue affiche : ce sont eux qui font l'ouverture immediate.
MAX_FOLDERS = 60000
MAX_PROBES = 200000
# Chaque dossier visite laisse sa composition ; on garde les plus recentes.
MAX_LISTINGS = 400

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
CREATE TABLE IF NOT EXISTS sigs(
    path   TEXT PRIMARY KEY,
    stamp  TEXT NOT NULL DEFAULT '',
    hashes TEXT NOT NULL DEFAULT '',
    size   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS scenes(
    path   TEXT PRIMARY KEY,
    stamp  TEXT NOT NULL DEFAULT '',
    times  TEXT NOT NULL DEFAULT ''
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
CREATE TABLE IF NOT EXISTS meta(
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""

# Les tables dont chaque ligne parle d'un chemin : c'est ce qui doit suivre un
# fichier qu'on range, et disparaitre avec celui qu'on detruit.
_PATH_TABLES = (("probes", "path"), ("titles", "path"), ("scenes", "path"),
                ("sigs", "path"), ("seen", "id"))

# Les seuls codes qui disent « le fichier est abime ». Tout le reste -- disque
# plein, erreur d'entree-sortie, verrou, antivirus qui tient le journal -- est
# passager, et ne justifie jamais de jeter un index sain.
_CORRUPT_CODES = {getattr(sqlite3, "SQLITE_CORRUPT", 11),
                  getattr(sqlite3, "SQLITE_NOTADB", 26)}


def _busy(exc: Exception) -> bool:
    """Vrai quand SQLite dit « occupe » et non « abime »."""
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _corrupt(exc: Exception) -> bool:
    """Vrai seulement quand SQLite dit que le fichier lui-meme est abime.

    `OperationalError` herite de `DatabaseError` : les distinguer par la classe
    faisait prendre un disque plein ou une erreur reseau passagere pour un
    index casse, qui etait alors mis de cote et refait de zero.
    """
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        return (code & 0xFF) in _CORRUPT_CODES
    text = str(exc).lower()
    return "malformed" in text or "not a database" in text or "corrupt" in text


def _spans(key: str) -> tuple:
    """Bornes des chemins situes sous `key`, pour une requete sur l'index.

    `chemin\\...` s'etend jusqu'a `chemin]` exclu : « ] » suit « \\ » dans
    l'ordre des octets. Meme chose pour « / », suivi de « 0 ». Une comparaison
    de bornes passe par la cle primaire, au lieu de lire toute la table comme
    le ferait un LIKE.
    """
    return (key + "\\", key + "]", key + "/", key + "0")


def _under(key: str, text: str) -> bool:
    return text == key or text.startswith((key + "\\", key + "/"))


def _parse_hashes(text: str):
    try:
        return array("Q", (int(x, 16) for x in text.split(",") if x))
    except (ValueError, OverflowError):
        return array("Q")


class Index:
    """Table {element: statistiques}, ecrite au fil de l'eau.

    Une seule connexion, partagee entre le fil de l'interface et celui de la
    reconciliation, protegee par un verrou : les ecritures sont breves et
    groupees, la contention est negligeable devant une lecture reseau.
    """

    # Les ecritures sont validees par paquets : une transaction par dossier
    # ferait un fsync par dossier, plus cher que l'analyse elle-meme.
    COMMIT_EVERY = 200
    # Le temps d'attendre un autre Prisme qui ecrit, avant de renoncer.
    OPEN_TIMEOUT = 30.0
    COMMIT_AFTER = 2.0     # secondes
    # La verification complete et la copie du jour attendent que le lancement
    # soit passe : elles relisent tout le fichier, et ce n'est pas le moment.
    UPKEEP_DELAY = 20.0
    # Au-dela, le plan d'une video relu une fois est oublie : on le relira.
    SCENE_CACHE = 4096

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else INDEX_PATH
        self.lock = threading.RLock()
        self.db: sqlite3.Connection | None = None
        self.pending = 0
        # Vrai quand le fichier a du etre refait : la collection sera
        # reanalysee une fois, et l'application doit pouvoir le dire.
        self.rebuilt = False
        # La copie d'ou l'index a ete repris, quand il etait abime.
        self.restored = ""
        # La derniere erreur d'ecriture qui n'etait pas un abime : on garde
        # l'index, mais on sait qu'il n'a pas tout retenu.
        self.problem = ""
        self.last_commit = time.monotonic()
        # Les sondages ffprobe tiennent en memoire : ils sont consultes des
        # dizaines de milliers de fois par tri (durees, resolutions, filtres) et
        # une requete par consultation couterait plus que le service rendu.
        self.probes: dict = {}
        self.titles: dict = {}
        self.seen: set = set()
        self._reset_lazy()
        self.open()

    def _reset_lazy(self) -> None:
        # Plans et empreintes ne se chargent plus au lancement : cent mille
        # lignes de chacun coutaient une a deux secondes et plus de deux cents
        # megaoctets avant meme la fenetre. On ne garde en memoire que de quoi
        # repondre « connu ? », et l'on va chercher le detail quand il sert.
        self._lazy_lock = threading.Lock()
        self._scene_paths: set | None = None
        self._scene_cache: dict = {}
        self._sig_stamps: dict | None = None

    # -- ouverture ------------------------------------------------------
    def _connect(self, timeout: float) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, check_same_thread=False, timeout=timeout)
        try:
            # WAL : un lecteur ne bloque pas l'ecrivain, l'interface n'attend
            # donc jamais la reconciliation qui tourne derriere.
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(SCHEMA)
            db.commit()
        except sqlite3.Error:
            try:
                db.close()
            except sqlite3.Error:
                pass
            raise
        return db

    def open(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = self._connect(self.OPEN_TIMEOUT)
        except sqlite3.Error as exc:
            if not _corrupt(exc):
                # Occupe par un autre Prisme, disque plein, erreur d'acces :
                # rien ne dit que le fichier est abime. On travaille sans index
                # plutot que d'effacer un index sain. C'est ce qui a fait perdre
                # l'index une fois -- « verrouille » etait pris pour « casse ».
                self.problem = str(exc)
                self._drop_connection()
                return
            self._rebuild()
            if self.db is None:
                return
        except OSError as exc:
            self.problem = str(exc)
            self._drop_connection()
            return
        state = self._sound(full=self._suspect_marker().exists())
        if state == "corrupt":
            # Un index abime ne se signale pas : chaque lecture rend « rien de
            # connu », l'application reanalyse tout, et recommence au lancement
            # suivant -- indefiniment, sans qu'un seul message ne l'explique.
            # Mieux vaut le refaire une fois (ou reprendre la copie) et le dire.
            self._rebuild()
            if self.db is None:
                return
        elif state == "unavailable":
            self._drop_connection()
            return
        self._load_probes()
        self._load_seen()
        self._load_titles()
        self._migrate_json()
        self._start_upkeep()

    def _suspect_marker(self) -> Path:
        return Path(str(self.path) + ".suspect")

    def _sound(self, full: bool = False) -> str:
        """« ok », « corrupt » ou « unavailable » : lisible, abime, ou pas pour l'instant.

        La verification complete relit tout le fichier -- une demi-seconde a
        chaque lancement, avant la fenetre. Elle ne se fait plus ici que si la
        verification de fond l'a demandee ; sinon on touche une table, la ou
        l'abime s'etait loge, ce qui suffit a reconnaitre un fichier casse.
        """
        if self.db is None:
            return "unavailable"
        try:
            if full:
                row = self.db.execute("PRAGMA quick_check(1)").fetchone()
                if not row or row[0] != "ok":
                    return "corrupt"
            self.db.execute("SELECT COUNT(*) FROM folders").fetchone()
            if full:
                try:
                    self._suspect_marker().unlink()
                except OSError:
                    pass
            return "ok"
        except sqlite3.Error as exc:
            if _corrupt(exc):
                return "corrupt"
            if _busy(exc):
                # Un autre programme ecrit : le fichier n'est pas en cause.
                return "ok"
            self.problem = str(exc)
            return "unavailable"

    def _drop_connection(self) -> None:
        try:
            if self.db is not None:
                self.db.close()
        except sqlite3.Error:
            pass
        self.db = None

    # -- entretien de fond : verification et copie du jour ---------------
    def _backups(self) -> tuple:
        return (Path(str(self.path) + ".sauvegarde"),
                Path(str(self.path) + ".sauvegarde-1"))

    def _backup_due(self) -> bool:
        main, _prev = self._backups()
        try:
            return datetime.date.fromtimestamp(
                main.stat().st_mtime) != datetime.date.today()
        except OSError:
            return True

    def _start_upkeep(self) -> None:
        if self.db is None:
            return
        timer = threading.Timer(self.UPKEEP_DELAY, self._upkeep, args=(self.path,))
        timer.daemon = True
        timer.start()

    def _upkeep(self, path: Path) -> None:
        """Une fois par jour : verification complete, puis copie, sans rien bloquer.

        Sur une seconde connexion, en lecture seule : le mode WAL laisse
        l'interface lire et ecrire pendant ce temps. Autrefois les deux se
        faisaient au lancement, avant la fenetre.
        """
        if path != self.path or self.db is None or not self._backup_due():
            return
        try:
            con = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True,
                                  timeout=5.0)
        except (sqlite3.Error, ValueError, OSError):
            return
        try:
            row = con.execute("PRAGMA quick_check(1)").fetchone()
            if not row or row[0] != "ok":
                self._flag_suspect(row[0] if row else "?")
                return
            self._write_backup(con)
        except sqlite3.Error as exc:
            if _corrupt(exc):
                self._flag_suspect(str(exc))
        finally:
            try:
                con.close()
            except sqlite3.Error:
                pass

    def check_now(self) -> None:
        """L'entretien de fond, tout de suite et sur ce fil (outils et tests)."""
        self._upkeep(self.path)

    def _flag_suspect(self, detail: str) -> None:
        """Le lancement suivant verifiera tout, et reprendra la copie s'il le faut.

        On ne refait pas l'index sous l'application qui s'en sert : on le note,
        et c'est l'ouverture suivante, avant tout usage, qui tranche.
        """
        try:
            self._suspect_marker().write_text(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} {detail}\n", encoding="utf-8")
        except OSError:
            pass

    def _write_backup(self, con: sqlite3.Connection) -> None:
        """Deux generations, a cote : si l'index se perdait, on repartirait
        d'hier et non de zero. Une copie bien plus petite que la precedente
        ne chasse pas la plus grosse -- c'est la signature d'un index vide."""
        if self.rebuilt or con.execute("SELECT COUNT(*) FROM folders").fetchone()[0] == 0:
            return          # un index vide ou refait ne remplace pas une vraie copie
        main, prev = self._backups()
        tmp = Path(str(main) + ".tmp")
        try:
            if tmp.exists():
                tmp.unlink()
            target = sqlite3.connect(tmp)
            try:
                con.backup(target)
            finally:
                target.close()
            size = tmp.stat().st_size
            if main.exists():
                main_size = main.stat().st_size
                small = size < main_size / 2
                keep_prev = small and prev.exists() and prev.stat().st_size >= main_size
                if not keep_prev:
                    os.replace(main, prev)
            os.replace(tmp, main)
        except (OSError, sqlite3.Error):
            try:
                tmp.unlink()
            except OSError:
                pass

    def _rebuild(self) -> None:
        """Repart de la derniere copie saine, sinon d'un index vide.

        L'ancien n'est jamais efface : il est mis de cote sous un autre nom, au
        cas ou il vaudrait encore quelque chose.
        """
        try:
            if self.db is not None:
                self.db.close()
        except sqlite3.Error:
            pass
        self.db = None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for suffix in ("", "-wal", "-shm"):
            source = Path(str(self.path) + suffix)
            try:
                if source.exists():
                    source.replace(Path(f"{self.path}.abime-{stamp}{suffix}"))
            except OSError:
                # Impossible a deplacer : un autre programme le tient. Il
                # n'est donc pas abime, il est occupe -- on n'y touche pas.
                return
        try:
            self._suspect_marker().unlink()
        except OSError:
            pass
        self._reset_lazy()
        for copy in self._backups():
            if self._restore_from(copy):
                self.rebuilt = True
                self.restored = str(copy)
                return
        try:
            self.db = self._connect(10.0)
            self.rebuilt = True
        except sqlite3.Error:
            self.db = None

    def _restore_from(self, copy: Path) -> bool:
        """Reprend une copie, si elle est saine. Rend vrai en cas de succes."""
        if not copy.exists():
            return False
        try:
            shutil.copy2(copy, self.path)
            db = self._connect(10.0)
        except (OSError, sqlite3.Error):
            return False
        try:
            row = db.execute("PRAGMA quick_check(1)").fetchone()
            if row and row[0] == "ok":
                self.db = db
                return True
        except sqlite3.Error:
            pass
        try:
            db.close()
        except sqlite3.Error:
            pass
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(self.path) + suffix).unlink()
            except OSError:
                pass
        return False

    # -- empreintes d'images --------------------------------------------
    def _sig_index(self) -> dict:
        """{chemin: date} des empreintes connues, lu une fois, a la demande."""
        found = self._sig_stamps
        if found is not None:
            return found
        with self._lazy_lock:
            if self._sig_stamps is None:
                rows = self._fetch("SELECT path, stamp FROM sigs")
                self._sig_stamps = {path: stamp for path, stamp in rows}
            return self._sig_stamps

    def _fetch(self, sql: str, args: tuple = ()) -> list:
        if self.db is None:
            return []
        with self.lock:
            try:
                return self.db.execute(sql, args).fetchall()
            except sqlite3.Error:
                return []

    def put_sig(self, path, stamp: str, hashes: list, size: int) -> None:
        """L'empreinte d'une video. Une liste vide vaut « essaye, rien
        d'exploitable » : on ne la recalculera pas tant qu'elle ne change pas."""
        key = str(path)
        self._sig_index()[key] = stamp or ""
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO sigs(path, stamp, hashes, size)"
                    " VALUES (?,?,?,?)",
                    (key, stamp or "", ",".join(f"{h:016x}" for h in hashes),
                     int(size or 0)))
            except sqlite3.Error:
                return
            # Groupe avec le reste : un commit par empreinte, depuis six fils,
            # tenait le verrou que l'interface attendait.
            self._touched()

    def sig_fresh(self, path, stamp: str) -> bool:
        """Vrai si l'empreinte connue vaut encore pour ce fichier.

        C'est ce qui rend le second passage immediat : seules les videos
        nouvelles, ou modifiees depuis, sont a sonder.
        """
        found = self._sig_index().get(str(path))
        return found is not None and (not stamp or found == stamp)

    def sig_of(self, path) -> list:
        rows = self._fetch("SELECT hashes FROM sigs WHERE path = ?", (str(path),))
        return list(_parse_hashes(rows[0][0])) if rows else []

    def all_sigs(self) -> list:
        """(chemin, empreintes, taille) de tout ce qui est connu et encore en place.

        Lu d'un bloc dans la base -- un instantane, que les fils qui sondent
        ne peuvent pas modifier sous nos pieds (le parcours du dictionnaire
        partage levait une erreur une fois sur trois pendant un sondage).
        Ce qui dort dans une corbeille n'est plus dans la collection : le
        proposer comme doublon enverrait detruire la seule copie restante.
        """
        trash_mark = os.sep + TRASH_FOLDER_NAME + os.sep
        local_trash = str(_config.LOCAL_TRASH)
        out = []
        for path, hashes, size in self._fetch(
                "SELECT path, hashes, size FROM sigs WHERE hashes != ''"):
            if trash_mark in path or _under(local_trash, path):
                continue
            values = _parse_hashes(hashes)
            if values:
                out.append((path, values, size))
        return out

    def forget_sig(self, path) -> None:
        key = str(path)
        self._sig_index().pop(key, None)
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute("DELETE FROM sigs WHERE path = ?", (key,))
            except sqlite3.Error:
                return
            self._touched()

    # -- plans ----------------------------------------------------------
    def _scene_index(self) -> set:
        found = self._scene_paths
        if found is not None:
            return found
        with self._lazy_lock:
            if self._scene_paths is None:
                self._scene_paths = {row[0] for row in
                                     self._fetch("SELECT path FROM scenes")}
            return self._scene_paths

    def put_scenes(self, path, stamp: str, times: list) -> None:
        """Les changements de plan releves. Une liste vide vaut « cherche, rien
        trouve » : on ne redemandera pas."""
        key = str(path)
        self._scene_index().add(key)
        self._remember_scenes(key, list(times))
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO scenes(path, stamp, times) VALUES (?,?,?)",
                    (key, stamp or "", ",".join(f"{t:.2f}" for t in times)))
            except sqlite3.Error:
                return
            self._touched()

    def _remember_scenes(self, key: str, times: list) -> None:
        cache = self._scene_cache
        if len(cache) >= self.SCENE_CACHE:
            cache.clear()
        cache[key] = array("d", times)

    def has_scenes(self, path) -> bool:
        return str(path) in self._scene_index()

    def scenes_of(self, path) -> list:
        key = str(path)
        cached = self._scene_cache.get(key)
        if cached is not None:
            return list(cached)
        if key not in self._scene_index():
            return []
        rows = self._fetch("SELECT times FROM scenes WHERE path = ?", (key,))
        try:
            times = [float(x) for x in rows[0][0].split(",") if x] if rows else []
        except ValueError:
            times = []
        self._remember_scenes(key, times)
        return times

    # -- titres ---------------------------------------------------------
    def _load_titles(self) -> None:
        self.titles = {}
        if self.db is None:
            return
        try:
            for path, title in self.db.execute("SELECT path, title FROM titles"):
                self.titles[path] = title
        except sqlite3.Error:
            pass

    def put_title(self, path, stamp: str, title: str) -> None:
        """Le titre des metadonnees -- vide quand la video n'en porte pas, ce
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
            except sqlite3.Error:
                return
            self._touched()

    def has_title(self, path) -> bool:
        return str(path) in self.titles

    def title_of(self, path) -> str:
        return self.titles.get(str(path), "")

    # -- deja vu --------------------------------------------------------
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
            except sqlite3.Error:
                return
            self._touched()

    def is_seen(self, item_id: str) -> bool:
        return item_id in self.seen

    # -- sondages ffprobe -----------------------------------------------
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
                self.problem = ""
            except sqlite3.Error as exc:
                if _corrupt(exc):
                    # Le fichier est vraiment abime : on reprend la derniere
                    # copie saine plutot que d'ecrire dans un index dont plus
                    # rien ne ressortira.
                    self._rebuild()
                elif not _busy(exc):
                    # Disque plein, erreur d'entree-sortie, antivirus : ce
                    # paquet-ci est perdu, mais l'index reste tel quel. Le
                    # jeter -- ce que faisait l'ancienne version -- coutait une
                    # reanalyse complete du NAS et tous les « deja vu ».
                    self.problem = str(exc)
                    try:
                        self.db.rollback()
                    except sqlite3.Error:
                        pass
                # Occupe : la transaction reste ouverte, le prochain commit
                # l'emportera avec lui.
            self.pending = 0
            self.last_commit = time.monotonic()

    # -- dossiers -------------------------------------------------------
    def folders(self, ids: list) -> dict:
        """Tout ce qu'on sait de ces elements, en une requete.

        Sert l'affichage instantane : on rend ce qu'on avait note la derniere
        fois, sans demander au disque s'il est toujours d'accord. C'est la
        reconciliation, plus tard, qui corrige ce qui a bouge.

        Le verrou n'est tenu que le temps de chaque requete : fabriquer cent
        mille chemins sous lui faisait attendre l'interface (un « deja vu »
        a patiente plus d'une seconde et demie).
        """
        if self.db is None or not ids:
            return {}
        rows: list = []
        # SQLite borne le nombre de parametres d'une requete : on decoupe.
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            marks = ",".join("?" * len(chunk))
            with self.lock:
                try:
                    rows.extend(self.db.execute(
                        "SELECT id, path, sig, kind, loose, size, file_count,"
                        " video_count, subdir_count, mtime, videos FROM folders"
                        " WHERE id IN (" + marks + ")", chunk).fetchall())
                except sqlite3.Error:
                    break
        build = _row_builder()
        return {row[0]: build(row) for row in rows}

    def signatures(self, ids: list) -> dict:
        """Empreintes connues, pour savoir sans rien relire ce qui a bouge."""
        if self.db is None or not ids:
            return {}
        known: dict = {}
        for start in range(0, len(ids), 400):
            chunk = ids[start:start + 400]
            marks = ",".join("?" * len(chunk))
            with self.lock:
                try:
                    rows = self.db.execute(
                        "SELECT id, sig FROM folders WHERE id IN (" + marks + ")",
                        chunk).fetchall()
                except sqlite3.Error:
                    return known
            known.update(dict(rows))
        return known

    def capped_ids(self, old_cap: int) -> list:
        """Les dossiers retenus du temps du plafond de videos, a relire en entier.

        Compte les lignes de la colonne en SQL : fabriquer les Item de toute
        la racine pour les compter tenait le verrou deux secondes a chaque
        lancement. Une fois qu'il n'en reste aucun, on ne le demande plus.
        """
        if self.db is None or self._meta("uncapped") == "1":
            return []
        with self.lock:
            try:
                rows = self.db.execute(
                    "SELECT id FROM folders WHERE kind != 'files' AND videos != ''"
                    " AND video_count > ? AND length(videos)"
                    " - length(replace(videos, char(10), '')) + 1"
                    " BETWEEN ? AND video_count - 1",
                    (old_cap, old_cap)).fetchall()
            except sqlite3.Error:
                return []
            if not rows:
                self._set_meta("uncapped", "1")
        return [row[0] for row in rows]

    def put_folder(self, item, sig: str) -> None:
        if self.db is None or not sig:
            return
        from .scan import MODE_FILES
        folder = str(item.path)
        head = folder if folder.endswith(("\\", "/")) else folder + os.sep
        names = []
        if item.kind != MODE_FILES:
            for video in item.videos:
                text = str(video)
                if text.startswith(head):
                    names.append(text[len(head):])
                else:
                    try:
                        names.append(str(Path(text).relative_to(folder)))
                    except ValueError:
                        names.append(text)
        with self.lock:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO folders(id, path, sig, kind, loose,"
                    " size, file_count, video_count, subdir_count, mtime, videos,"
                    " used) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item.item_id, folder, sig, item.kind,
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

    # -- ce qui suit un fichier -----------------------------------------
    def relocate(self, old, new) -> int:
        """Fait suivre a un chemin -- fichier ou dossier entier -- ce qu'on sait de lui.

        Sondage, plans, empreinte, titre et « deja vu » : sans cela, une video
        rangee perdait tout (re-sondee, 4 ffmpeg de plus), et son ancien
        chemin restait en base, presente comme doublon de la nouvelle. La date
        et la taille survivent a un renommage : ce qui a ete mesure vaut encore.
        Rend le nombre de cles deplacees en memoire.
        """
        old_s, new_s = str(old), str(new)
        if not old_s or old_s == new_s:
            return 0
        moved = 0
        for table in (self.probes, self.titles, self._scene_cache,
                      self._sig_stamps):
            if table is not None:
                moved += _rekey(table, old_s, new_s)
        for keys in (self.seen, self._scene_paths):
            if keys is not None:
                moved += _rekey_set(keys, old_s, new_s)
        if self.db is None:
            return moved
        spans = _spans(old_s)
        with self.lock:
            try:
                for table, column in _PATH_TABLES:
                    extra = f" OR {column} = ?" if table == "seen" else ""
                    args = [new_s, len(old_s) + 1, old_s, *spans]
                    if extra:
                        args.append(old_s + "|vrac")
                    self.db.execute(
                        f"UPDATE OR REPLACE {table}"
                        f" SET {column} = ? || substr({column}, ?)"
                        f" WHERE {column} = ? OR ({column} >= ? AND {column} < ?)"
                        f" OR ({column} >= ? AND {column} < ?){extra}", args)
            except sqlite3.Error:
                return moved
            self._touched()
        return moved

    def forget_tree(self, path) -> None:
        """Oublie tout ce qui vivait a ce chemin ou dessous : il est detruit.

        Sans cela, les lignes d'une video supprimee restaient pour toujours --
        et son empreinte continuait de la presenter comme doublon.
        """
        key = str(path)
        if not key:
            return
        for table in (self.probes, self.titles, self._scene_cache,
                      self._sig_stamps):
            if table is not None:
                for name in [k for k in list(table) if _under(key, k)]:
                    table.pop(name, None)
        for keys in (self.seen, self._scene_paths):
            if keys is not None:
                for name in [k for k in list(keys)
                             if _under(key, k) or k == key + "|vrac"]:
                    keys.discard(name)
        if self.db is None:
            return
        spans = _spans(key)
        with self.lock:
            try:
                for table, column in _PATH_TABLES:
                    self.db.execute(
                        f"DELETE FROM {table} WHERE {column} = ?"
                        f" OR ({column} >= ? AND {column} < ?)"
                        f" OR ({column} >= ? AND {column} < ?)"
                        + (f" OR {column} = ?" if table == "seen" else ""),
                        (key, *spans) + ((key + "|vrac",) if table == "seen" else ()))
            except sqlite3.Error:
                return
            self._touched()

    # -- rayonnages -----------------------------------------------------
    def expansion(self, folder: Path, stamp: int, any_stamp: bool = False) -> dict | None:
        """Composition connue de ce dossier de tete.

        Avec `any_stamp`, quelle que soit sa date : c'est ce qu'on montre quand
        le rayonnage ne repond pas, plutot que de le presenter vide.
        """
        if self.db is None or (not stamp and not any_stamp):
            return None
        with self.lock:
            try:
                row = self.db.execute(
                    "SELECT sig, loose, children FROM expansions WHERE path = ?",
                    (str(folder),)).fetchone()
            except sqlite3.Error:
                return None
        if not row or (not any_stamp and row[0] != str(stamp)):
            return None
        try:
            children = [tuple(pair) for pair in json.loads(row[2])]
        except ValueError:
            return None
        return {"children": children, "loose": bool(row[1])}

    def put_expansion(self, folder: Path, stamp: int, children: list,
                      loose: bool) -> None:
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute(
                    "INSERT OR REPLACE INTO expansions(path, sig, loose, children)"
                    " VALUES(?,?,?,?)",
                    (str(folder), str(stamp or 0), int(loose),
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

    # -- petites notes --------------------------------------------------
    def _meta(self, key: str) -> str:
        rows = self._fetch("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0][0] if rows else ""

    def _set_meta(self, key: str, value: str) -> None:
        if self.db is None:
            return
        with self.lock:
            try:
                self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                                (key, value))
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
        """Borne l'index, sans jamais oublier ce qu'une racine connue affiche.

        L'ancienne version gardait les soixante mille lignes les plus
        recemment *ecrites* -- or un dossier inchange n'est jamais reecrit :
        les dossiers stables de la racine partaient les premiers, et la
        collection reapparaissait amputee. Elle supprimait aussi a chaque
        fermeture, meme sous le plafond (une demi-seconde, sur l'interface).
        On compte d'abord ; on ne supprime que ce qui depasse, et jamais une
        ligne citee par une composition de racine.
        """
        if self.db is None:
            return
        with self.lock:
            try:
                count = self.db.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
                if count > MAX_LISTINGS:
                    self.db.execute(
                        "DELETE FROM listings WHERE rowid NOT IN (SELECT rowid"
                        " FROM listings ORDER BY stamp DESC LIMIT ?)", (MAX_LISTINGS,))
                count = self.db.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
                if count > MAX_FOLDERS:
                    listed: set = set()
                    for (ids,) in self.db.execute("SELECT ids FROM listings"):
                        listed.update(ids.split("\n"))
                    rows = self.db.execute(
                        "SELECT id FROM folders ORDER BY used ASC").fetchall()
                    victims = [row[0] for row in rows
                               if row[0] not in listed][:count - MAX_FOLDERS]
                    for start in range(0, len(victims), 400):
                        chunk = victims[start:start + 400]
                        self.db.execute(
                            "DELETE FROM folders WHERE id IN ("
                            + ",".join("?" * len(chunk)) + ")", chunk)
                count = self.db.execute("SELECT COUNT(*) FROM probes").fetchone()[0]
                if count > MAX_PROBES:
                    self.db.execute(
                        "DELETE FROM probes WHERE rowid IN (SELECT rowid FROM"
                        " probes ORDER BY rowid ASC LIMIT ?)", (count - MAX_PROBES,))
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
        self.titles = {}
        self.seen = set()
        self._reset_lazy()
        self.pending = 0
        self.rebuilt = False
        self.restored = ""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.db = self._connect(10.0)
        except (sqlite3.Error, OSError):
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
            return "indisponible" + (f" ({self.problem})" if self.problem else "")
        if self.restored:
            return "repris de la copie de secours (il etait abime)"
        if self.rebuilt:
            return "refait (il etait abime)"
        if self.problem:
            return f"ok, mais une ecriture a echoue ({self.problem})"
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


def _rekey(table: dict, old: str, new: str) -> int:
    """Renomme dans ce dictionnaire la cle `old` et tout ce qui est dessous."""
    names = [k for k in list(table) if _under(old, k)]
    for name in names:
        value = table.pop(name, None)
        if value is not None:
            table[new + name[len(old):]] = value
    return len(names)


def _rekey_set(keys: set, old: str, new: str) -> int:
    vrac = old + "|vrac"
    names = [k for k in list(keys) if _under(old, k) or k == vrac]
    for name in names:
        keys.discard(name)
        keys.add(new + name[len(old):])
    return len(names)


def _row_builder():
    """Fabrique les Item d'une requete, sans repasser par pathlib a chaque video.

    `dossier / nom` puis le premier `str()` de chaque chemin coutaient une a
    sept secondes a l'ouverture de la racine (cent mille videos) : pathlib
    redecoupe et recompose chaque chemin. Ceux de l'index sont deja sous leur
    forme definitive ; `fast_path` les prend tels quels.
    """
    from .scan import MODE_FILES, Item, fast_path
    seps = ("\\", "/")

    def video_path(head: str, name: str):
        if name[1:2] == ":" or name.startswith(seps):
            return Path(name)       # un chemin complet, garde tel quel
        return fast_path(head + name)

    def build(row):
        (_id, path, _sig, kind, loose, size, file_count, video_count,
         subdir_count, mtime, videos) = row
        folder = fast_path(path)
        if kind == MODE_FILES:
            # Une video se porte elle-meme : rien a recomposer.
            return Item(path=folder, kind=MODE_FILES, size=size, file_count=1,
                        video_count=1, mtime=mtime, videos=[folder])
        head = path if path.endswith(seps) else path + os.sep
        return Item(
            path=folder, kind=kind, size=size, file_count=file_count,
            video_count=video_count, subdir_count=subdir_count, mtime=mtime,
            videos=[video_path(head, name) for name in videos.split("\n") if name],
            loose_only=bool(loose),
        )

    return build


INDEX = Index()
