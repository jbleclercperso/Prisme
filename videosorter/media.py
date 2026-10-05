"""Accès à ffmpeg / ffprobe : sondage des vidéos et extraction de vignettes."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import shutil
import re
import subprocess
import sys
import warnings
from pathlib import Path

import time
from collections import deque

from PySide6.QtCore import (
    QObject, QRunnable, QThread, QThreadPool, QTimer, Qt, Signal,
)
from PySide6.QtGui import QColor, QImage, QImageIOHandler, QImageReader, QPainter

from .config import THUMB_DIR, VIDEO_EXTS, is_photo
from .index import INDEX
from .stamps import carry as carry_stamps, stamp_of

# Évite une fenêtre console qui clignote à chaque appel ffmpeg sous Windows.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_WINGET_GLOB = "AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg*/*/bin"


def _find_tool(name: str, configured: str = "") -> str:
    """Localise ffmpeg/ffprobe : config explicite, puis PATH, puis winget,
    puis celui que Prisme a telecharge (ou pose a cote du programme)."""
    if configured and Path(configured).exists():
        return configured
    found = shutil.which(name)
    if found:
        return found
    for candidate in Path.home().glob(_WINGET_GLOB):
        exe = candidate / f"{name}.exe"
        if exe.exists():
            return str(exe)
    from .ffmpeg_fetch import candidates
    for candidate in candidates():
        exe = candidate / f"{name}.exe"
        if exe.is_file():
            return str(exe)
    return ""


class Tools:
    """Chemins vers les binaires, résolus une fois au démarrage."""

    ffmpeg = ""
    ffprobe = ""

    @classmethod
    def resolve(cls, cfg=None) -> bool:
        conf_ffmpeg = cfg.get("ffmpeg", "") if cfg else ""
        conf_ffprobe = cfg.get("ffprobe", "") if cfg else ""
        cls.ffmpeg = _find_tool("ffmpeg", conf_ffmpeg)
        cls.ffprobe = _find_tool("ffprobe", conf_ffprobe)
        return bool(cls.ffmpeg and cls.ffprobe)


DRIVE_REMOTE = 4


def is_network_path(path) -> bool:
    """Vrai si le chemin vit sur un partage réseau (UNC ou lecteur mappé).

    Sur un NAS, ce n'est pas le processeur qui limite mais la latence : chaque
    lecture attend un aller-retour. Il vaut alors mieux lancer plus d'extractions
    de front, là où en local on saturerait le disque pour rien.
    """
    text = str(path)
    if text.startswith("\\\\") or text.startswith("//"):
        return True
    if sys.platform != "win32":
        return False
    drive = os.path.splitdrive(os.path.abspath(text))[0]
    if not drive:
        return False
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(f"{drive}\\") == DRIVE_REMOTE
    except (OSError, AttributeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Lectures en cours : qui lit quoi, et de quoi les interrompre
# ---------------------------------------------------------------------------

# Les fichiers en cours de lecture par un fil de fond. Deplacer ou supprimer
# une video n'a besoin de liberer que ceux-la — et non plus toute la
# fabrication de vignettes, ce qui gelait l'interface a chaque decision.
_READING: dict = {}
_READING_LOCK = threading.Lock()
# Les ffmpeg et ffprobe vivants, par fichier lu. Les garder permet de les
# arreter : attendre qu'un ffmpeg ait fini de lire un fichier qu'on range
# coutait jusqu'a une seconde et demie, sur le fil de l'interface, a chaque
# touche de tri.
_PROCS: dict = {}
# Les chemins qu'on s'apprete a deplacer ou supprimer, et jusqu'a quand aucune
# lecture nouvelle ne doit s'y ouvrir : sous Windows, un fichier ouvert ne se
# renomme pas.
_BLOCKED: dict = {}
_LOCAL = threading.local()
# Vrai une fois l'application en train de se fermer : plus rien ne part.
CLOSING = False


def _key(path) -> str:
    """Forme comparable d'un chemin : Windows ne distingue ni la casse ni
    le sens des barres."""
    return str(path).replace("/", "\\").lower()


def _under(key: str, prefix: str) -> bool:
    return key == prefix or key.startswith(prefix.rstrip("\\") + "\\")


class _Reading:
    """Signale qu'un fil lit ce fichier, pour la duree du bloc."""

    def __init__(self, path):
        self.key = _key(path)

    def __enter__(self):
        with _READING_LOCK:
            _READING[self.key] = _READING.get(self.key, 0) + 1
        stack = getattr(_LOCAL, "keys", None)
        if stack is None:
            stack = _LOCAL.keys = []
        stack.append(self.key)

    def __exit__(self, *_exc):
        stack = getattr(_LOCAL, "keys", None)
        if stack:
            stack.pop()
        with _READING_LOCK:
            left = _READING.get(self.key, 1) - 1
            if left:
                _READING[self.key] = left
            else:
                _READING.pop(self.key, None)


def reading_under(target) -> bool:
    """Vrai si un fil de fond lit ce fichier, ou un fichier de ce dossier."""
    prefix = _key(target)
    with _READING_LOCK:
        return any(_under(key, prefix) for key in _READING)


def _kill(proc) -> None:
    proc._prisme_killed = True
    try:
        proc.kill()
    except OSError:
        pass


def _kill_owned(owner) -> None:
    """Arrete les outils lances pour ce travail-la."""
    with _READING_LOCK:
        victims = list(owner._procs)
    for proc in victims:
        _kill(proc)


def release_reads(target, hold: float = 1.5) -> int:
    """Libere ce fichier (ou ce dossier) sans rien attendre.

    Les ffmpeg et ffprobe qui le lisent sont arretes sur-le-champ, et aucune
    lecture nouvelle ne s'y ouvre pendant `hold` secondes — le temps que le
    transfert le deplace. Rend le nombre d'outils arretes.
    """
    prefix = _key(target)
    with _READING_LOCK:
        _BLOCKED[prefix] = time.monotonic() + max(0.0, hold)
        victims = [proc for key, procs in _PROCS.items()
                   if key and _under(key, prefix) for proc in procs]
    for proc in victims:
        _kill(proc)
    return len(victims)


def unblock(target) -> None:
    """Le deplacement est fait : les lectures de ce chemin peuvent reprendre."""
    with _READING_LOCK:
        _BLOCKED.pop(_key(target), None)


def wait_free(target, timeout: float = 1.5) -> bool:
    """Attend que plus aucun fil ne lise ce chemin. Hors du fil graphique.

    C'est au transfert d'attendre, pas a l'interface : il tourne deja en
    tache de fond et reessaie de toute facon.
    """
    deadline = time.monotonic() + timeout
    while reading_under(target):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def close_all() -> None:
    """Arrete tout ffmpeg et ffprobe en vol, et n'en laisse plus partir.

    Pour la fermeture : un outil encore lance tiendrait des fichiers ouverts,
    et un processus invisible survivrait a la fenetre.
    """
    global CLOSING
    CLOSING = True
    with _READING_LOCK:
        victims = [proc for procs in _PROCS.values() for proc in procs]
    for proc in victims:
        _kill(proc)


def _held_until(key: str) -> float:
    """Echeance du blocage qui couvre ce chemin, ou zero."""
    now = time.monotonic()
    with _READING_LOCK:
        if not _BLOCKED:
            return 0.0
        for prefix, until in list(_BLOCKED.items()):
            if until <= now:
                del _BLOCKED[prefix]
            elif _under(key, prefix):
                return until
    return 0.0


def _spawn(cmd: list[str], timeout: float, binary: bool = False) -> tuple:
    """Lance un outil et rend (code, sortie, erreurs, pourquoi).

    `pourquoi` vaut "" pour une execution menee a terme, "tue" quand on l'a
    arretee (fichier range, travail annule, fermeture), "delai" quand elle a
    depasse son temps, "echec" quand elle n'a pas pu partir. Dans ces trois
    cas le code vaut None : rien ne peut en etre conclu sur le fichier.

    `binary` rend la sortie en octets (une image tiree par un tuyau) : tout
    outil passe par ici, pour que `release_reads`, `close_all` et l'arret
    d'un travail l'atteignent.

    Un chemin qu'on s'apprete a ranger n'est pas ouvert : on renonce tout de
    suite (« tue ») au lieu d'attendre la fin du blocage pour le lire quand
    meme. Attendre gardait la lecture inscrite, que le rangement attendait a
    son tour ; le ffmpeg partait ensuite sur un fichier en plein deplacement,
    qui echouait alors en « utilise par un autre processus ».
    """
    empty = b"" if binary else ""
    stack = getattr(_LOCAL, "keys", None)
    key = stack[-1] if stack else ""
    owner = getattr(_LOCAL, "owner", None)
    if CLOSING or (owner is not None and owner.cancelled):
        return None, empty, empty, "tue"
    if key and _held_until(key):
        return None, empty, empty, "tue"
    text = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=NO_WINDOW, **text,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, empty, empty, "echec"
    proc._prisme_killed = False
    with _READING_LOCK:
        _PROCS.setdefault(key, set()).add(proc)
        doomed = CLOSING
        if owner is not None:
            owner._procs.add(proc)
            doomed = doomed or owner._kill
    if doomed:
        _kill(proc)
    why = ""
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill(proc)
            proc._prisme_killed = False
            why = "delai"
            try:
                out, err = proc.communicate(timeout=5)
            except (OSError, ValueError, subprocess.SubprocessError):
                out, err = empty, empty
        except (OSError, ValueError, subprocess.SubprocessError):
            _kill(proc)
            out, err = empty, empty
            why = "echec"
    finally:
        with _READING_LOCK:
            procs = _PROCS.get(key)
            if procs is not None:
                procs.discard(proc)
                if not procs:
                    _PROCS.pop(key, None)
            if owner is not None:
                owner._procs.discard(proc)
    if proc._prisme_killed:
        why = "tue"
    if why:
        return None, out or empty, err or empty, why
    return proc.returncode, out or empty, err or empty, ""


def _run_err(cmd: list[str], timeout: int = 30) -> tuple:
    """Comme `_run`, mais rend aussi la sortie d'erreur.

    ffmpeg ecrit ce que `showinfo` releve sur la sortie d'erreur, pas sur la
    sortie standard : sans elle, on ne verrait rien.

    Le code vaut None quand l'outil n'a pas termine (delai depasse, arret,
    lancement impossible) : ce n'est pas un echec du fichier, et il ne faut
    surtout pas l'enregistrer comme tel.
    """
    code, out, err, _why = _spawn(cmd, timeout)
    return code, out, err


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    code, out, _err, _why = _spawn(cmd, timeout)
    return (1, "") if code is None else (code, out)


# ---------------------------------------------------------------------------
# Premier plan : ce qu'on regarde passe devant les travaux de fond
# ---------------------------------------------------------------------------

class _Foreground:
    """Compte les demandes au premier plan encore en attente.

    La recolte, la preparation, les releves de plans et de titres tiraient
    sur le partage jusqu'a quatorze ffmpeg a la fois pendant qu'on regardait
    une page : ses images arrivaient deux fois moins vite. Ils attendent
    desormais que ce compte retombe a zero, et un instant de plus.
    """

    GRACE = 0.5            # secondes de calme avant de reprendre
    # Garde-fou : si un compte restait coince, les travaux de fond ne
    # s'arreteraient pas pour toujours — ils avanceraient au pas.
    PATIENCE = 20.0

    def __init__(self):
        self._lock = threading.Lock()
        self._count = 0
        self._last = 0.0

    def enter(self) -> None:
        with self._lock:
            self._count += 1

    def leave(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)
            self._last = time.monotonic()

    @property
    def pending(self) -> int:
        return self._count

    def idle(self, grace: float | None = None) -> bool:
        grace = self.GRACE if grace is None else grace
        with self._lock:
            return (self._count == 0
                    and time.monotonic() - self._last >= grace)

    def wait(self, stop=None, grace: float | None = None) -> None:
        """Attend le calme. Hors du fil graphique."""
        deadline = time.monotonic() + self.PATIENCE
        while not self.idle(grace):
            if CLOSING or (stop is not None and stop()):
                return
            if time.monotonic() >= deadline:
                return
            time.sleep(0.1)


FOREGROUND = _Foreground()


def wait_foreground(stop=None) -> None:
    """Pour les travaux de fond : cede la place a ce qu'on regarde."""
    FOREGROUND.wait(stop)


class _Owner:
    """Porte les outils lances pour un travail, pour pouvoir les arreter."""

    def __init__(self):
        self.cancelled = False
        self._kill = False
        self._procs: set = set()

    def kill(self) -> None:
        self.cancelled = True
        self._kill = True
        _kill_owned(self)


# ---------------------------------------------------------------------------
# Sondage (durée / résolution / codec), retenu par l'index
# ---------------------------------------------------------------------------

# Borner l'examen de l'en-tete ne tient pas la mesure : trois fois plus rapide
# sur certains fichiers, deux fois plus lent sur d'autres, ou ffmpeg doit relire
# apres avoir echoue dans la borne. On ne le fait donc que pour ffprobe, dont
# c'est justement le travail de lire l'en-tete, et ou deux megaoctets suffisent.
PROBE_LIMITS = ["-probesize", "2M", "-analyzeduration", "2M"]

# Ou prendre l'image d'une carte, quand on ignore la duree de la video.
#
# C'est le reglage le plus cher de toute l'application. Chercher l'image a la
# sixieme seconde coute le double de la prendre au tout debut — 1,00 s contre
# 0,48 s par image, mesure sur le NAS — parce qu'un saut oblige a faire venir ce
# qu'on saute, et la difference se multiplie par quarante a chaque page.
#
# Deux secondes : assez pour depasser l'image noire ou le logo d'ouverture, mais
# ffmpeg y rejoint presque toujours la meme image-cle qu'a zero, donc sans rien
# faire venir de plus. `preview_start` dans la configuration deplace ce curseur
# — 0 pour le plus rapide, davantage pour des images plus parlantes.
# Instant de l image tiree sans rien savoir de la video. A deux secondes on
# tombait encore sur un logo, un noir d ouverture ou un carton de titre ; a dix,
# parfois encore le generique. A vingt, on est dans le sujet. Les videos plus
# courtes retombent sur leur premiere image, la seconde tentative d extraction
# s en charge.
BLIND_START = 20.0


def _stamp_of(path: Path) -> str:
    """Taille et date : ce qui distingue deux versions d'un meme chemin.

    Deleguee a `stamps`, qui retient ce que l'analyse a deja lu : sans cela,
    afficher une planche demandait une lecture reseau par carte, meme quand
    toutes les vignettes etaient deja fabriquees.
    """
    return stamp_of(path)


def probe(path: Path) -> dict:
    with _Reading(path):
        return _probe(path)


# Ce que ffprobe dit d'un fichier reellement abime...
_BROKEN = ("invalid data found", "moov atom not found",
           "could not find codec parameters", "end of file",
           "unknown format", "does not contain any stream", "header missing")
# ... et ce qu'il dit d'un partage absent ou lent, qui ne dit rien du fichier.
_UNREACHABLE = ("no such file", "permission denied", "access is denied",
                "input/output error", "i/o error", "network", "connection",
                "timed out", "resource temporarily", "cannot find", "device",
                "not ready")
# Un echec passager n'est pas grave en soi ; le redemander vingt fois pendant
# la meme coupure, si. On se tait donc un moment sur ce fichier.
_MISS_QUIET = 60.0
_MISSED: dict = {}
# Les echecs deja retenus qu'on a reverifies pendant cette seance.
_RECHECKED: set = set()


def _really_broken(path, stamp: str, code, err: str) -> bool:
    """Vrai seulement pour un fichier present, lisible, et que ffprobe refuse.

    Enregistrer n'importe quel echec sortait pour toujours du Mur et des
    filtres les videos sondees pendant une coupure du NAS.
    """
    if code is None or not stamp:
        return False
    text = (err or "").lower()
    if not text or any(word in text for word in _UNREACHABLE):
        return False
    if not any(word in text for word in _BROKEN):
        return False
    try:
        os.stat(str(path))
    except OSError:
        return False
    return True


def _probe_photo(path: Path, stamp: str) -> dict | None:
    """Dimensions d'une photo, lues dans son en-tete par Qt : ni ffprobe, ni
    lecture de l'image entiere. Rien si Qt ne sait pas la lire (HEIC)."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid():
        return None
    width, height = size.width(), size.height()
    # Une photo prise de cote est rangee couchee : c'est son orientation
    # EXIF qui la redresse, et les filtres doivent la voir debout.
    if reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90:
        width, height = height, width
    info = {"duration": 0.0, "width": width, "height": height,
            "codec": bytes(reader.format()).decode("ascii", "replace"),
            "ok": width > 0}
    INDEX.put_probe(path, stamp, info)
    return info


def _probe(path: Path) -> dict:
    """Retourne {duration, width, height, codec, ok} pour une vidéo."""
    key = str(path)
    stamp = _stamp_of(path)
    cached = INDEX.probe(path, stamp)
    if cached is not None:
        if cached.get("ok") or key in _RECHECKED:
            return cached
        # Un echec retenu a pu l'etre pendant une coupure, par une version
        # qui gardait tout : on le reverifie une fois par seance.
        _RECHECKED.add(key)

    if is_photo(path):
        found = _probe_photo(path, stamp)
        if found is not None:
            return found
    info = {"duration": 0.0, "width": 0, "height": 0, "codec": "", "ok": False}
    if not Tools.ffprobe:
        return cached or info
    missed = _MISSED.get(key)
    if missed is not None and time.monotonic() - missed < _MISS_QUIET:
        return cached or info

    code, out, err, why = _spawn([
        Tools.ffprobe, "-v", "error",
    ] + PROBE_LIMITS + [
        "-print_format", "json", "-show_format", "-show_streams", str(path),
    ], 30)
    if why == "tue":
        # Arrete expres (page quittee, video rangee, fermeture) : ce n'est pas
        # une panne. Le retenir comme telle privait la video de sondage une
        # minute, et une pellicule preparee entre-temps posait ses cinq
        # images a zero seconde.
        return cached or info
    if code == 0 and out:
        try:
            data = json.loads(out)
        except ValueError:
            data = {}
        try:
            info["duration"] = float(data.get("format", {}).get("duration") or 0.0)
        except (TypeError, ValueError):
            info["duration"] = 0.0
        for stream in data.get("streams", []):
            if stream.get("codec_type") != "video":
                continue
            info["width"] = int(stream.get("width") or 0)
            info["height"] = int(stream.get("height") or 0)
            info["codec"] = stream.get("codec_name") or ""
            if not info["duration"]:
                try:
                    info["duration"] = float(stream.get("duration") or 0.0)
                except (TypeError, ValueError):
                    pass
            break
        info["ok"] = info["width"] > 0 or info["duration"] > 0
        INDEX.put_title(path, stamp, title_from(data))
        INDEX.put_probe(path, stamp, info)
        _MISSED.pop(key, None)
        return info

    if _really_broken(path, stamp, code, err):
        INDEX.put_probe(path, stamp, info)
        return info
    # Rien a conclure : ni la video ni l'index n'en gardent trace, elle sera
    # sondee de nouveau quand le partage repondra.
    if len(_MISSED) > 5000:
        _MISSED.clear()
    _MISSED[key] = time.monotonic()
    return cached or info


def title_from(data: dict) -> str:
    """Le titre porte par le conteneur, s'il y en a un — quel que soit sa casse."""
    tags = (data.get("format") or {}).get("tags") or {}
    for key, value in tags.items():
        if str(key).lower() == "title" and str(value).strip():
            return str(value).strip()
    return ""


# ---------------------------------------------------------------------------
# Vignettes
# ---------------------------------------------------------------------------

# Les largeurs demandees pendant la seance, et les images servies pour chaque
# video : pour savoir quelles vignettes faire suivre a une video rangee.
_WIDTHS: set = set()
_SERVED: dict = {}


def _served(video, ts: float, width: int) -> None:
    if len(_SERVED) >= 50_000:
        _SERVED.clear()
    _SERVED.setdefault(str(video), set()).add((ts, width))


def _thumb_file(digest: str) -> Path:
    return THUMB_DIR / digest[:2] / f"{digest}.jpg"


# Des noms que portent des milliers de fichiers differents, et de meme taille :
# les parties d'un DVD, les sequences d'un camescope AVCHD. Deux disques d'un
# meme coffret ont chacun leur VTS_01_1.VOB, de meme taille (le graveur coupe
# a 1 Go) et souvent de meme date : leur nom seul ne les distingue plus. Les
# noms d'appareil photo (IMG_1234) n'en sont pas : leurs tailles different, et
# changer leur nom de vignette ferait tout refabriquer sur le partage.
_GENERIC_NAME = re.compile(r"^(vts_\d+_\d+\.vob|video_ts\.vob|\d{5}\.(m2ts|mts))$",
                           re.IGNORECASE)


def _key_name(video) -> str:
    """Ce qui nomme une video dans le nom de ses vignettes.

    Son nom seul, sauf pour un nom generique, qu'on complete par son dossier
    (celui du disque, au-dessus de VIDEO_TS) : ranger le disque entier garde
    alors ses images.
    """
    text = str(video).replace("/", "\\")
    name = text.rsplit("\\", 1)[-1].casefold()
    if not _GENERIC_NAME.match(name):
        return name
    parts = text.rstrip("\\").split("\\")[:-1]
    if parts and parts[-1].casefold() == "video_ts":
        parts = parts[:-1]
    return parts[-1].casefold() + "\\" + name if parts else name


def _thumb_key(video, stamp: str, ts: float, width: int) -> Path:
    """Le nom d'une vignette : nom du fichier, taille, date, instant, largeur.

    Le dossier n'y entre plus. Ranger une video garde sa taille et sa date,
    mais changeait son chemin, donc le nom de toutes ses vignettes : le
    dossier de destination n'avait que des cartes vides, refaites une a une
    par ffmpeg sur le NAS. Deux fichiers de meme nom, meme taille et meme date
    a la seconde pres sont des copies : ils partagent leurs images -- sauf
    sous un nom generique (VTS_01_1.VOB), ou le dossier les distingue
    (voir _key_name).
    """
    if not stamp:
        # Sans empreinte, le nom seul confondrait des videos differentes.
        return _legacy_key(video, "0|0", ts, width)
    name = _key_name(video)
    digest = hashlib.sha1(
        f"{name}|{stamp}|{ts:.2f}|{width}".encode("utf-8", "replace")
    ).hexdigest()
    return _thumb_file(digest)


def _legacy_key(video, stamp: str, ts: float, width: int) -> Path:
    """L'ancien nom, fait du chemin complet : les vignettes deja fabriquees
    le portent encore, et on les reprend au passage."""
    digest = hashlib.sha1(
        f"{video}|{stamp}|{ts:.2f}|{width}".encode("utf-8", "replace")
    ).hexdigest()
    return _thumb_file(digest)


def thumb_path(video: Path, ts: float, width: int) -> Path:
    _WIDTHS.add(width)
    return _thumb_key(video, _stamp_of(video), ts, width)


def _usable(path: Path) -> bool:
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def cached_thumb(video: Path, ts: float, width: int) -> Path | None:
    """La vignette deja fabriquee pour cet instant, ou rien. Ne lance rien.

    Une vignette nommee a l'ancienne est renommee au passage : les heures de
    preparation deja faites ne sont pas perdues au changement de nom.
    """
    _WIDTHS.add(width)
    stamp = _stamp_of(video)
    out = _thumb_key(video, stamp, ts, width)
    if _usable(out):
        _served(video, ts, width)
        return out
    if not stamp:
        return None
    legacy = _legacy_key(video, stamp, ts, width)
    if legacy == out or not _usable(legacy):
        return None
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        os.replace(legacy, out)
    except OSError:
        return legacy if _usable(legacy) else None
    if not _usable(out):
        return None
    _served(video, ts, width)
    return out


class _Making:
    """Une extraction en cours, que d'autres demandes peuvent attendre."""

    def __init__(self):
        self.event = threading.Event()
        self.interrupted = False


# Deux demandes de la meme image lancaient deux ffmpeg ecrivant le meme
# fichier : la fiche prepare les deux suivantes, puis les redemande en urgence
# quand on y arrive. La seconde attend desormais la premiere.
_MAKING: dict = {}
_MAKING_LOCK = threading.Lock()
# Assez pour les trois essais d'une extraction qui traine.
_MAKING_WAIT = 90.0


def extract_thumb(video: Path, ts: float, width: int,
                  keyframe: bool = False) -> Path | None:
    """Extrait une image a l'instant ts. Retourne le fichier de cache, ou None.

    `keyframe` accepte l'image-cle qui precede l'instant, a partir de
    `KEYFRAME_FROM` : voir `_make_thumb`.
    """
    with _Reading(video):
        return _extract_thumb(video, ts, width, keyframe)


def _extract_thumb(video: Path, ts: float, width: int,
                   keyframe: bool = False) -> Path | None:
    while True:
        found = cached_thumb(video, ts, width)
        if found is not None:
            return found
        if not Tools.ffmpeg and not is_photo(video):
            return None
        out = thumb_path(video, ts, width)
        slot_key = str(out)
        with _MAKING_LOCK:
            making = _MAKING.get(slot_key)
            mine = making is None
            if mine:
                making = _MAKING[slot_key] = _Making()
        if mine:
            made, interrupted = None, True
            try:
                made, interrupted = _make_thumb(video, ts, width, out, keyframe)
            finally:
                with _MAKING_LOCK:
                    _MAKING.pop(slot_key, None)
                making.interrupted = interrupted
                making.event.set()
            return made
        if not _await(making):
            return None
        if not making.interrupted:
            return out if _usable(out) else None
        # Celle qu'on attendait a ete arretee : on la reprend a son compte.


def _await(making: _Making) -> bool:
    """Attend l'extraction d'un autre, sauf si l'on nous annule entre-temps."""
    owner = getattr(_LOCAL, "owner", None)
    deadline = time.monotonic() + _MAKING_WAIT
    while not making.event.wait(0.1):
        if CLOSING or (owner is not None and owner.cancelled):
            return False
        if time.monotonic() >= deadline:
            return False
    return True


# A partir de cet instant, une carte peut se contenter de l'image-cle qui le
# precede. Pour tomber pile, ffmpeg doit faire venir et decoder tout ce qui
# separe cette image-cle de l'instant voulu : mesure sur une video 720p a
# 8 Mb/s, 9,4 Mo lus pour une vignette, contre 1,0 Mo en prenant l'image-cle.
# Plus tot dans la video, l'image-cle risque d'etre celle du debut — le logo ou
# le noir que l'instant choisi voulait justement eviter.
KEYFRAME_FROM = 30.0
# De combien on depasse l'instant d'une carte prise a l'image-cle (voir
# _make_thumb).
KEYFRAME_NUDGE = 0.2


def _drop(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _make_photo_thumb(photo: Path, width: int, part: Path, out: Path) -> Path | None:
    """La vignette d'une photo, par Qt : decodee directement a la taille
    voulue (un JPEG se lit alors bien plus vite), redressee selon l'EXIF.
    Ecrite a cote, puis renommee d'un coup, comme celles de ffmpeg."""
    reader = QImageReader(str(photo))
    reader.setAutoTransform(True)
    size = reader.size()
    rotated = bool(reader.transformation()
                   & QImageIOHandler.Transformation.TransformationRotate90)
    if size.isValid() and INDEX.probe(photo) is None:
        # Ses dimensions, au passage : l'en-tete est deja lu, et le filtre
        # « Verticales » en a besoin.
        tall, wide = (size.width(), size.height()) if rotated else \
            (size.height(), size.width())
        INDEX.put_probe(photo, _stamp_of(photo), {
            "duration": 0.0, "width": wide, "height": tall,
            "codec": bytes(reader.format()).decode("ascii", "replace"),
            "ok": wide > 0})
    if size.isValid() and size.width() > width:
        # La largeur voulue est celle de l'image redressee.
        if rotated:
            reader.setScaledSize(size.scaled(10 ** 6, width, Qt.KeepAspectRatio))
        else:
            reader.setScaledSize(size.scaled(width, 10 ** 6, Qt.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return None
    if image.hasAlphaChannel():
        # Un PNG transparent devient noir en JPEG : on le pose sur du sombre.
        flat = QImage(image.size(), QImage.Format_RGB32)
        flat.fill(QColor("#14161a"))
        painter = QPainter(flat)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flat
    if not image.save(str(part), "JPG", 85) or not _usable(part):
        _drop(part)
        return None
    try:
        os.replace(part, out)
    except OSError:
        _drop(part)
        return out if _usable(out) else None
    return out


def _make_thumb(video: Path, ts: float, width: int, out: Path,
                keyframe: bool) -> tuple:
    """Lance ffmpeg ; rend (fichier ou None, vrai si l'on a ete arrete).

    L'image s'ecrit a cote puis prend son nom d'un coup : un ffmpeg arrete en
    route laissait un JPEG tronque, pris ensuite pour une vignette valide et
    garde pour toujours.
    """
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None, False
    part = out.with_name(out.stem + ".part.jpg")
    if is_photo(video):
        made = _make_photo_thumb(video, width, part, out)
        if made is not None:
            _served(video, ts, width)
            return made, False
        if not Tools.ffmpeg:
            return None, False
        # Qt ne sait pas la lire (HEIC) : ffmpeg, a partir de sa seule image.
        ts = 0.0
    base = [Tools.ffmpeg, "-hide_banner", "-loglevel", "error"]
    # -an : pas de piste son a demultiplexer pour fabriquer une image fixe.
    tail = ["-an", "-frames:v", "1", "-vf", f"scale={width}:-2",
            "-q:v", "4", "-y", str(part)]
    at = max(0.0, ts)
    seek = ["-ss", f"{at:.2f}"]
    source = ["-i", str(video)]

    # Premier essai, l'en-tete abrege : l'analyse par defaut lit plusieurs
    # megaoctets avant la premiere image, deux suffisent ici.
    #
    # -noaccurate_seek n'y est plus : la sortie image impose un rythme fixe, si
    # bien que ffmpeg decodait de toute facon tout ce qui precede l'instant,
    # pour le jeter ensuite. Memes octets lus, deux fois plus de calcul.
    #
    # Seule la carte qui accepte l'image-cle la prend telle quelle
    # (-fps_mode passthrough) : c'est la que le reseau economise vraiment.
    quick = ["-probesize", "2M", "-analyzeduration", "2M"]
    if keyframe and at >= KEYFRAME_FROM:
        # Un peu au-dela de l'instant : les plans sont des images-cles
        # exactes (36,703333 s), et l'arrondi a deux decimales (36,70)
        # tombait juste avant, donc sur l'image-cle d'avant -- le plan
        # precedent. Deux dixiemes couvrent aussi le recul que ffmpeg
        # applique aux fichiers a images B ; on reste bien avant l'image-cle
        # suivante. Le nom de la vignette garde l'instant demande.
        nudged = ["-ss", f"{at + KEYFRAME_NUDGE:.3f}"]
        first = (base + ["-noaccurate_seek", "-threads", "1"] + quick + nudged
                 + source + ["-fps_mode", "passthrough"] + tail)
    else:
        first = base + quick + seek + source + tail
    # Puis l'en-tete complet, puis la premiere image : certaines videos
    # refusent le saut, d'autres sont plus courtes que l'instant demande.
    attempts = [first, base + seek + source + tail]
    if at > 0:
        attempts.append(base + source + tail)

    for cmd in attempts:
        code, _out, _err, why = _spawn(cmd, 25)
        if why == "tue":
            _drop(part)
            return None, True
        if code == 0 and _usable(part):
            try:
                os.replace(part, out)
            except OSError:
                _drop(part)
                return (out if _usable(out) else None), False
            _served(video, ts, width)
            return out, False
        _drop(part)
    return None, False


def _moments_for(video: str, info: dict | None) -> set:
    """Les instants auxquels l'application a pu tirer une image de cette video.

    Ce sont les formules des cartes, de la grille, de la pellicule, de
    l'apercu et de la recolte : l'index n'en garde pas la liste.
    """
    found = {BLIND_START, 0.0}
    duration = (info or {}).get("duration") or 0.0
    if duration > 2:
        fractions = {0.2, 0.35, 0.5, 0.65, 0.8}
        for count in (1, 5, 9):
            fractions.update(0.05 + 0.9 * ((index + 1) / (count + 1))
                             for index in range(count))
        for fraction in fractions:
            found.add(max(0.0, min(duration - 1.0, duration * fraction)))
        found.add(min(duration * 0.2, 20.0))
    scenes = INDEX.scenes_of(video)
    if scenes:
        for count in (1, 5, 9):
            found.update(pick_moments(scenes, duration, count))
    found.add(card_moment(video))
    return found


def relocate_thumbs(old, new) -> int:
    """Fait suivre a une video (ou a un dossier) rangee ce qu'on savait d'elle.

    A appeler une fois le deplacement reussi — pas pour la corbeille. Le nom
    des vignettes ne depend plus du dossier : elles suivent d'elles-memes un
    fichier range sous le meme nom. Restent a reporter :
    - l'empreinte taille/date, pour ne pas la redemander au reseau ;
    - le sondage et les plans, dont depend l'instant de la carte — sans eux,
      la carte changerait d'instant et donc d'image ;
    - les vignettes encore nommees a l'ancienne, ou d'un fichier renomme a
      l'arrivee (« clip (2).mp4 »).

    Pour un dossier, parcourt les empreintes connues : quelques dizaines de
    millisecondes sur cent mille videos, donc hors du fil graphique. Rend le
    nombre de vignettes reportees.
    """
    carried = 0
    for before, after, stamp in carry_stamps(old, new):
        dot = after.rfind(".")
        if dot <= 0 or after[dot:].lower() not in VIDEO_EXTS:
            continue
        # Le deplacement a deja fait suivre sondage et plans a l'index
        # (actions._carry) : c'est au nouveau chemin qu'on les trouve. Lus a
        # l'ancien, ils manquaient, et seules les images a 0 et 20 s
        # suivaient la video.
        info = INDEX.probe(after) or INDEX.probe(before)
        if info is not None and INDEX.probe(after) is None:
            INDEX.put_probe(after, stamp, dict(info))
        if INDEX.has_scenes(before) and not INDEX.has_scenes(after):
            INDEX.put_scenes(after, stamp, INDEX.scenes_of(before))
        carried += _carry_images(before, after, stamp, info)
    return carried


def _carry_images(before: str, after: str, stamp: str, info) -> int:
    moments = _moments_for(after, info) | _moments_for(before, info)
    pairs = {(ts, width) for width in (list(_WIDTHS) or [480])
             for ts in moments}
    # Et tout ce qui a ete servi pour elle pendant la seance, quel qu'en soit
    # l'instant.
    pairs |= _SERVED.pop(before, set())
    count = 0
    for ts, width in pairs:
        target = _thumb_key(after, stamp, ts, width)
        if _usable(target):
            continue
        try:
            legacy = _legacy_key(before, stamp, ts, width)
            if _usable(legacy):
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(legacy, target)
                count += 1
                continue
            source = _thumb_key(before, stamp, ts, width)
            if source != target:
                # Renommee a l'arrivee, ou nom generique dans un autre dossier.
                if _usable(source):
                    # Une copie, pas un deplacement : une autre copie de la
                    # video, restee sous l'ancien nom, s'en sert peut-etre.
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    count += 1
        except OSError:
            continue
    return count


def cache_size_bytes() -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(THUMB_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def clear_cache() -> None:
    shutil.rmtree(THUMB_DIR, ignore_errors=True)


def page_count(videos: list, count: int, one_per_video: bool = True) -> int:
    """Nombre de pages d'aperçus disponibles pour ces vidéos."""
    if not one_per_video or not videos:
        return 1
    return max(1, -(-len(videos) // count))




# Au-dessus de ce seuil, ffmpeg considere que l'image a franchement change.
# Trop bas, un mouvement de camera compte pour un plan ; trop haut, un film
# entier n'en a que trois.
SCENE_THRESHOLD = 0.28
# On ne lit que les images cles : un changement de plan en est presque
# toujours une, et le fichier se traverse alors dix fois plus vite. Sur un
# partage reseau, c'est la difference entre quelques secondes et une minute.
#
# « -discard nokey » ferait lire moins encore au demultiplexeur, mais il
# fausse l'heure des plans trouves (6,00 s annonce a 6,12 s, 20,00 a 20,04) :
# on ne l'emploie pas.
SCENE_ARGS = ["-skip_frame", "nokey"]
_SCENE_TIME = re.compile(r"pts_time:([0-9.]+)")


def scene_times(video: Path, timeout: int = 90) -> list | None:
    """Les instants ou l'image change franchement, en secondes.

    Les vignettes etaient prises a des fractions fixes — 20 %, 35 %, 50 %… —
    ce qui donne une image noire sur une video qui commence par un fondu, et
    cinq fois le meme plan sur une video statique. Ici l'on prend les plans
    eux-memes.

    Rend None quand on ne peut rien conclure — delai depasse, ffmpeg arrete
    ou en erreur sans rien avoir releve. Une liste vide, elle, veut dire
    « parcourue en entier, aucun plan » et s'enregistre pour toujours : un
    delai depasse sur une longue video la privait a jamais de ses plans.
    """
    if not Tools.ffmpeg:
        return None
    with _Reading(video):
        code, _out, err = _run_err([
            Tools.ffmpeg, "-v", "info", "-nostdin"] + SCENE_ARGS + [
            "-i", str(video), "-an", "-sn",
            "-filter:v", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
            "-fps_mode", "vfr", "-f", "null", "-",
        ], timeout=timeout)
    if code is None:
        return None
    times = []
    for found in _SCENE_TIME.findall(err):
        try:
            value = float(found)
        except ValueError:
            continue
        # Deux plans a moins d'une seconde ne donneront pas deux images
        # differentes : on garde le premier.
        if not times or value - times[-1] >= 1.0:
            times.append(value)
    if code != 0 and not times:
        return None
    return times


def pick_moments(times: list, duration: float, count: int) -> list:
    """Choisit `count` instants parmi les plans releves, bien repartis.

    Prendre les premiers donnerait cinq fois le generique ; on les echelonne
    donc sur toute la duree, en evitant les toutes premieres secondes qui ne
    montrent souvent qu'un logo.
    """
    usable = [t for t in times if t >= 1.0
              and (duration <= 0 or t <= duration - 0.5)]
    if not usable:
        return []
    if len(usable) <= count:
        return usable
    step = len(usable) / float(count)
    return [usable[min(len(usable) - 1, int(i * step))] for i in range(count)]


def card_moment(video) -> float:
    """L'instant de l'image d'une carte : le meme, que la video soit sondee ou non.

    Trois formules cohabitaient -- la planche, la recolte, la preparation --
    et celle de la planche changeait des que la video etait sondee (20 % ou
    50 % de sa duree) : chaque carte s'extrayait deux fois, et la preparation
    d'avance fabriquait des images que la planche ne demandait plus. Seuls
    les plans reperes (« Repérer les plans », un choix explicite) deplacent
    l'image, une fois pour toutes. La duree n'y entre pas : elle arrive apres.
    """
    if is_photo(video):
        return 0.0                      # une photo n'a qu'une image
    if INDEX.has_scenes(video):
        moments = pick_moments(INDEX.scenes_of(video), 0.0, 1)
        if moments:
            return moments[0]
    return BLIND_START


def build_preview_plan(videos: list, count: int, page: int = 0,
                       one_per_video: bool = True, blind: bool = False) -> list:
    """Construit une page d'aperçus.

    Chaque entrée est (fichier, instant, durée, hauteur) : le sondage ffprobe a
    déjà eu lieu ici, autant en faire profiter l'affichage plutôt que de le
    refaire depuis le fil de l'interface.

    Pour un dossier, une image par vidéo, par tranches de `count` : un dossier de
    trois vidéos montre trois aperçus, pas trois vidéos étirées sur dix cases.
    Pour une vidéo seule, `count` instants échelonnés à l'intérieur.

    Avec `blind`, rien n'est demandé au disque : que des lectures en mémoire.
    """
    if not videos:
        return []

    if is_photo(videos[0]):
        # Des photos : une image chacune, a l'instant zero, et une seule pour
        # une photo seule -- dix « instants » d'une meme image n'ont pas de sens.
        if one_per_video:
            chunk = (videos[page:page + 1] if blind and count == 1
                     else videos[page * count:(page + 1) * count])
        else:
            chunk = videos[:1]
        plan = []
        for photo in chunk:
            info = INDEX.probe(photo) or {}
            plan.append((str(photo), 0.0, 0.0, info.get("height") or 0))
        return plan

    if blind and count == 1:
        # Une carte : son instant ne depend que de la video (`card_moment`),
        # pour que planche, recolte et preparation tombent sur la meme image.
        chunk = videos[page:page + 1] if one_per_video else videos[:1]
        if not chunk:
            return []
        video = chunk[0]
        info = INDEX.probe(video) or {}
        return [(str(video), card_moment(video), info.get("duration") or 0.0,
                 info.get("height") or 0)]

    if one_per_video:
        chunk = videos[page * count:(page + 1) * count]
        # Les hauteurs varient d'une case à l'autre : deux plans pris au même
        # endroit de deux épisodes se ressemblent souvent trop.
        pairs = [
            (video, (0.2, 0.35, 0.5, 0.65, 0.8)[index % 5])
            for index, video in enumerate(chunk)
        ]
    else:
        video = videos[0]
        pairs = [
            (video, 0.05 + 0.9 * ((index + 1) / (count + 1)))
            for index in range(count)
        ]

    # Les plans deja releves servent d'abord : c'est la seule facon d'avoir
    # des images qui montrent quelque chose plutot que des fractions rondes.
    if not one_per_video and INDEX.has_scenes(videos[0]):
        info = INDEX.probe(videos[0]) or {}
        duration = info.get("duration") or 0.0
        moments = pick_moments(INDEX.scenes_of(videos[0]), duration, count)
        if moments:
            return [(str(videos[0]), ts, duration, info.get("height") or 0)
                    for ts in moments]

    plan = []
    for index, (video, fraction) in enumerate(pairs):
        if one_per_video and INDEX.has_scenes(video):
            info = INDEX.probe(video) or {}
            duration = info.get("duration") or 0.0
            moments = pick_moments(INDEX.scenes_of(video), duration, 5)
            if moments:
                plan.append((str(video), moments[index % len(moments)],
                             duration, info.get("height") or 0))
                continue
        info = INDEX.probe(video)
        if info is None:
            if blind:
                # Une image par video : on ne saurait que faire de la duree, et
                # la demander couterait un ffprobe de plus que l'extraction
                # elle-meme. Elle arrivera apres, sans retenir l'image.
                plan.append((str(video), BLIND_START, 0.0, 0))
                continue
            # Dix instants dans une meme video : sans sa duree, on ne sait pas
            # les echelonner. Un seul sondage sert alors les dix images.
            info = probe(Path(video))
        duration = info.get("duration") or 0.0
        ts = min(duration - 1.0, duration * fraction) if duration > 2 else 0.0
        plan.append((str(video), max(0.0, ts), duration, info.get("height") or 0))
    return plan


# ---------------------------------------------------------------------------
# Exécution en arrière-plan
# ---------------------------------------------------------------------------

class JobSignals(QObject):
    plan_ready = Signal(str, list)        # item_id, [(video, ts), ...]
    thumb_ready = Signal(str, int, str)   # item_id, slot, chemin vignette
    thumb_failed = Signal(str, int)       # item_id, slot
    info_ready = Signal(str, int, float, int)   # item_id, slot, duree, hauteur


class _Jobs:
    """Les travaux en vol, par element ; chacun s'en retire en finissant.

    Une liste tronquee aux soixante derniers perdait la trace de la moitie
    d'une page de planche (plan, image et sondage par carte) : ceux-la
    n'etaient plus jamais annules.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._by_item: dict = {}

    def add(self, job) -> None:
        with self._lock:
            self._by_item.setdefault(job.item_id, {})[id(job)] = job

    def discard(self, job) -> None:
        with self._lock:
            jobs = self._by_item.get(job.item_id)
            if jobs is not None:
                jobs.pop(id(job), None)
                if not jobs:
                    del self._by_item[job.item_id]

    def take(self, doomed) -> list:
        """Retire et rend les travaux des elements pour lesquels doomed(id)."""
        with self._lock:
            ids = [item_id for item_id in self._by_item if doomed(item_id)]
            taken = []
            for item_id in ids:
                taken.extend(self._by_item.pop(item_id).values())
        return taken

    def all(self) -> list:
        with self._lock:
            return [job for jobs in self._by_item.values()
                    for job in jobs.values()]

    def __len__(self) -> int:
        with self._lock:
            return sum(len(jobs) for jobs in self._by_item.values())


class _Job(QRunnable):
    """Socle commun : annulable, arretable, et qui se retire du suivi."""

    def __init__(self, signals: JobSignals, item_id: str):
        super().__init__()
        self.signals = signals
        self.item_id = item_id
        self.cancelled = False
        self.tracker: _Jobs | None = None
        self._kill = False
        self._procs: set = set()
        self._counted = False

    def cancel(self, kill: bool = False) -> None:
        self.cancelled = True
        if kill:
            self._kill = True
            _kill_owned(self)

    def count_foreground(self) -> None:
        if not self._counted:
            self._counted = True
            FOREGROUND.enter()

    def finish(self) -> None:
        if self._counted:
            self._counted = False
            FOREGROUND.leave()
        tracker = self.tracker
        if tracker is not None:
            tracker.discard(self)

    def run(self) -> None:
        _LOCAL.owner = self
        try:
            if not self.cancelled:
                self.work()
        finally:
            _LOCAL.owner = None
            self.finish()

    def work(self) -> None:
        raise NotImplementedError


class PlanJob(_Job):
    """Sonde les vidéos retenues et calcule les instants des aperçus."""

    def __init__(self, signals: JobSignals, item_id: str, videos: list, count: int,
                 page: int = 0, one_per_video: bool = True, blind: bool = False):
        super().__init__(signals, item_id)
        self.videos = videos
        self.count = count
        self.page = page
        self.one_per_video = one_per_video
        self.blind = blind

    def work(self) -> None:
        plan = build_preview_plan(self.videos, self.count, self.page,
                                  self.one_per_video, self.blind)
        if not self.cancelled:
            self.signals.plan_ready.emit(self.item_id, plan)


class InfoJob(_Job):
    """Sonde une video apres coup, pour completer sa duree et sa resolution.

    L'image passe devant : on la montre des qu'elle est la, et ces deux
    chiffres la rejoignent quand ils arrivent. Les demander d'abord doublait le
    temps avant le premier apercu, pour une pastille.
    """

    def __init__(self, signals: JobSignals, item_id: str, slot: int, video: str):
        super().__init__(signals, item_id)
        self.slot = slot
        self.video = video

    def work(self) -> None:
        info = probe(Path(self.video))
        if not self.cancelled:
            self.signals.info_ready.emit(
                self.item_id, self.slot,
                info.get("duration") or 0.0, info.get("height") or 0)


class ThumbJob(_Job):
    def __init__(self, signals: JobSignals, item_id: str, slot: int,
                 video: Path, ts: float, width: int, keyframe: bool = False):
        super().__init__(signals, item_id)
        self.slot = slot
        self.video = video
        self.ts = ts
        self.width = width
        self.keyframe = keyframe
        self.priority = 0
        self.urgent = False

    def work(self) -> None:
        out = extract_thumb(self.video, self.ts, self.width, self.keyframe)
        if self.cancelled:
            return
        if out:
            self.signals.thumb_ready.emit(self.item_id, self.slot, str(out))
        else:
            self.signals.thumb_failed.emit(self.item_id, self.slot)


class _CacheLane(QRunnable):
    """La voie rapide : une vignette deja faite ne fait pas la queue.

    Elle attendait derriere les ffmpeg en cours — une seconde chacun sur le
    NAS — pour un simple test de presence sur le disque local. La planche se
    remplissait par vagues alors que trente cartes sur quarante etaient
    pretes. Seules les absentes partent vers ffmpeg.
    """

    def __init__(self, manager: "PreviewManager", job: ThumbJob):
        super().__init__()
        self.manager = manager
        self.job = job
        self.counted = job.urgent
        if self.counted:
            FOREGROUND.enter()

    def run(self) -> None:
        job = self.job
        handed = False
        try:
            if job.cancelled:
                return
            try:
                found = cached_thumb(job.video, job.ts, job.width)
            except OSError:
                found = None
            if job.cancelled:
                return
            if found is not None:
                job.signals.thumb_ready.emit(job.item_id, job.slot, str(found))
                return
            if job.urgent:
                job.count_foreground()
            self.manager.pool.start(job, job.priority)
            handed = True
        finally:
            if self.counted:
                FOREGROUND.leave()
            if not handed:
                job.finish()


class _InlinePlan:
    """Un plan « a l'aveugle », calcule sur-le-champ.

    Il ne lit que la memoire : l'envoyer dans la file des ffmpeg le faisait
    attendre une seconde derriere eux, avant meme de chercher l'image.
    """

    def __init__(self, item_id: str, plan: list):
        self.item_id = item_id
        self.plan = plan
        self.cancelled = False
        self.tracker = None

    def cancel(self, kill: bool = False) -> None:
        self.cancelled = True


class Harvester(QThread):
    """Fabrique les vignettes a l'avance, quand personne ne regarde.

    La mesure est sans appel : sur le partage, une vignette coute environ une
    seconde, et seize extractions de front ne vont pas plus vite que huit — la
    ligne est saturee, pas le processeur. Une page de quarante cartes demande
    donc une demi-minute, et rien ne peut la raccourcir **au moment ou on la
    regarde**.

    Mais une vignette deja fabriquee se relit en deux millisecondes. Tout
    l'enjeu est donc de les fabriquer avant, une fois, pendant qu'on fait autre
    chose — et de s'effacer des que quelqu'un demande quelque chose.
    """

    progress = Signal(int, int)        # faites, a faire
    finished_harvest = Signal(int)     # fabriquees

    # Deux extractions seulement : la recolte est un travail de fond, elle ne
    # doit pas prendre la ligne a ce qu'on regarde.
    WORKERS = 2
    # Apres une demande au premier plan, on se tait le temps qu'elle aboutisse.
    QUIET_AFTER_REQUEST = 2.5          # secondes

    def __init__(self, width: int, parent=None):
        super().__init__(parent)
        self.width = width
        self.tasks: list = []          # [(cle, video, instant)]
        self.made = 0
        self.busy_until = 0.0
        self._lock = threading.Lock()
        self._queue: deque = deque()
        self._urgent: set = set()
        self._stop = False
        self._owner = _Owner()

    def stop(self) -> None:
        self._stop = True

    def kill(self) -> None:
        """Arrete aussi les extractions en cours : pour la fermeture."""
        self._stop = True
        self._owner.kill()

    def hold(self) -> None:
        """Quelqu'un regarde : on s'ecarte."""
        self.busy_until = time.monotonic() + self.QUIET_AFTER_REQUEST

    def prioritise(self, keys) -> None:
        """Fait passer ces elements en tete de la recolte.

        Elle parcourait la collection dans l'ordre : arrive a la page cinq, on
        attendait ses apercus pendant qu'elle preparait tranquillement la page
        une. Ce qu'on a sous les yeux passe devant.
        """
        with self._lock:
            self._urgent = set(keys)

    def _next(self):
        with self._lock:
            if self._urgent:
                for position, task in enumerate(self._queue):
                    if task[0] in self._urgent:
                        del self._queue[position]
                        return task
                # Plus rien d'urgent en attente : on reprend le fil.
                self._urgent.clear()
            return self._queue.popleft() if self._queue else None

    def _quiet(self) -> None:
        """Attend que personne ne demande rien."""
        while not self._stop:
            FOREGROUND.wait(lambda: self._stop)
            if time.monotonic() >= self.busy_until:
                return
            time.sleep(0.15)

    def run(self) -> None:
        try:
            self._harvest()
        finally:
            # Une recolte finie gardait ses cent mille taches jusqu'a la fin
            # de la seance, et il en nait une a chaque onglet.
            self.tasks = []
            with self._lock:
                self._queue = deque()
                self._urgent = set()

    def _harvest(self) -> None:
        total = len(self.tasks)
        if not total:
            self.finished_harvest.emit(0)
            return
        with self._lock:
            self._queue = deque(self.tasks)
        self.tasks = []
        # Plus de passe prealable sur toute la collection : elle demandait
        # au reseau taille et date de chaque video avant la premiere image,
        # dix secondes pour deux mille videos. Chaque tache regarde elle-meme
        # si son image existe deja.
        self.progress.emit(0, total)
        counted = [0]
        last = [0.0]
        report_lock = threading.Lock()

        def worker():
            _LOCAL.owner = self._owner
            while not self._stop:
                task = self._next()
                if task is None:
                    return
                video = Path(task[1])
                try:
                    ready = cached_thumb(video, task[2], self.width)
                except OSError:
                    ready = None
                made = None
                # Un fichier qu'on range en ce moment : inutile de l'ouvrir.
                if ready is None and not _held_until(_key(video)):
                    self._quiet()
                    if self._stop:
                        return
                    made = extract_thumb(video, task[2], self.width,
                                         keyframe=True)
                with report_lock:
                    self.made += bool(made)
                    counted[0] += 1
                    now = time.monotonic()
                    if now - last[0] >= 0.5 or counted[0] == total:
                        self.progress.emit(counted[0], total)
                        last[0] = now

        hands = [threading.Thread(target=worker, daemon=True)
                 for _ in range(self.WORKERS)]
        for hand in hands:
            hand.start()
        for hand in hands:
            hand.join()
        if not self._stop:
            self.finished_harvest.emit(self.made)


class PreviewManager(QObject):
    """Orchestre plans et vignettes, en annulant les travaux devenus inutiles."""

    plan_ready = Signal(str, list)
    thumb_ready = Signal(str, int, str)
    thumb_failed = Signal(str, int)
    info_ready = Signal(str, int, float, int)

    # La voie rapide ne fait que regarder le disque local : quatre suffisent.
    FAST_WORKERS = 4

    def __init__(self, thumb_width: int, parent=None):
        super().__init__(parent)
        self.thumb_width = thumb_width
        self.signals = JobSignals()
        self.signals.plan_ready.connect(self.plan_ready)
        self.signals.thumb_ready.connect(self.thumb_ready)
        self.signals.thumb_failed.connect(self.thumb_failed)
        self.signals.info_ready.connect(self.info_ready)
        self.pool = QThreadPool()
        self.local_workers = max(2, min(4, (os.cpu_count() or 4) // 2))
        self.pool.setMaxThreadCount(self.local_workers)
        self.lane = QThreadPool()
        self.lane.setMaxThreadCount(self.FAST_WORKERS)
        self.tracked = _Jobs()
        self.harvester: Harvester | None = None
        self._retired: list = []
        self._inline: list = []
        self._turn = 0
        self._turn_open = False

    @property
    def jobs(self) -> list:
        """Les travaux encore suivis."""
        return self.tracked.all()

    def _track(self, job) -> None:
        job.tracker = self.tracked
        self.tracked.add(job)

    def busy(self) -> int:
        """Nombre d'extractions en cours, pour pouvoir le dire a l'ecran."""
        return self.pool.activeThreadCount()

    def cancel_all(self, kill: bool = False) -> None:
        for job in self.tracked.take(lambda _item_id: True):
            job.cancel(kill)

    def cancel_except(self, keep_ids: set, kill: bool = False) -> None:
        for job in self.tracked.take(lambda item_id: item_id not in keep_ids):
            job.cancel(kill)

    def cancel_prefix(self, prefix: str, keep=(), kill: bool = True) -> int:
        """Annule les travaux dont la cle commence ainsi, sauf ceux de `keep`.

        Pour la planche : en tournant les pages, les images de la page quittee
        passaient avant celles de la page regardee. Leurs ffmpeg sont arretes
        par defaut — la ligne revient a ce qu'on regarde. Rend le nombre de
        travaux annules.
        """
        keep = set(keep)
        taken = self.tracked.take(
            lambda item_id: item_id.startswith(prefix) and item_id not in keep)
        for job in taken:
            job.cancel(kill)
        return len(taken)

    # L element affiche passe devant ceux qu on prepare pour apres : sans cela,
    # les vingt vignettes des deux suivants occupaient les huit fils pendant que
    # la case qu on regarde attendait son tour.
    URGENT = 10
    AHEAD = 0
    # Trois etages — ce qu'on regarde, ce qu'on prepare pour apres, les
    # pastilles de duree — et, dans chaque etage, la demande la plus recente
    # d'abord : a egalite, l'ordre d'arrivee servait la page quittee avant
    # celle qu'on vient d'ouvrir.
    _FLOORS = {"urgent": 2, "ahead": 1, "info": 0}
    _FLOOR = 100_000_000

    def _priority(self, floor: str) -> int:
        if not self._turn_open:
            # Tout ce qui est demande pendant un meme tour de la boucle
            # d'evenements — une page entiere — partage le meme rang.
            self._turn_open = True
            self._turn += 1
            QTimer.singleShot(0, self._close_turn)
        return (self._FLOORS[floor] * self._FLOOR
                + min(self._turn, self._FLOOR - 1))

    def _close_turn(self) -> None:
        self._turn_open = False

    def request_plan(self, item_id: str, videos: list, count: int,
                     page: int = 0, one_per_video: bool = True,
                     urgent: bool = True, blind: bool = False) -> None:
        if blind:
            try:
                plan = build_preview_plan(videos, count, page, one_per_video,
                                          True)
            except Exception:
                # Rien ne devrait echouer ici ; si cela arrive, la voie
                # ordinaire fera ce qu'elle a toujours fait.
                plan = None
            if plan is not None:
                job = _InlinePlan(item_id, plan)
                self._track(job)
                self._inline.append(job)
                if len(self._inline) == 1:
                    # Rendu au prochain tour, comme avant : l'appelant ne voit
                    # pas le plan arriver au milieu de sa propre demande.
                    QTimer.singleShot(0, self._flush_inline)
                return
        job = PlanJob(self.signals, item_id, videos, count, page, one_per_video,
                      blind)
        self._track(job)
        if urgent:
            job.count_foreground()
        self.pool.start(job, self._priority("urgent" if urgent else "ahead"))

    def _flush_inline(self) -> None:
        pending, self._inline = self._inline, []
        for job in pending:
            self.tracked.discard(job)
            if not job.cancelled:
                self.plan_ready.emit(job.item_id, job.plan)

    def request_info(self, item_id: str, slot: int, video: str) -> None:
        """Duree et resolution, en dernier : l'image ne les attend pas."""
        job = InfoJob(self.signals, item_id, slot, video)
        self._track(job)
        self.pool.start(job, self._priority("info"))

    def request_thumb(self, item_id: str, slot: int, video: str, ts: float,
                      urgent: bool = True, keyframe: bool = False) -> None:
        """Demande une vignette. `keyframe` : voir `KEYFRAME_FROM` — pour les
        cartes et la grille, pas pour la pellicule d'une video, dont les
        images doivent rester distinctes."""
        if urgent and self.harvester is not None:
            self.harvester.hold()
        job = ThumbJob(self.signals, item_id, slot, Path(video), ts,
                       self.thumb_width, keyframe)
        job.urgent = urgent
        job.priority = self._priority("urgent" if urgent else "ahead")
        self._track(job)
        self.lane.start(_CacheLane(self, job))

    def quiesce(self, timeout_ms: int = 6000) -> None:
        """Annule et attend la fin des ffmpeg en cours.

        Indispensable avant de déplacer ou supprimer : un ffmpeg qui lit encore
        un fichier empêche le renommage de son dossier parent sous Windows.
        """
        self.cancel_all()
        self.lane.waitForDone(timeout_ms)
        self.pool.waitForDone(timeout_ms)

    def release(self, target, timeout_ms: int = 1500) -> None:
        """Libere ce fichier (ou ce dossier) avant qu'on le deplace.

        Sans rien attendre. On attendait ici, sur le fil de l'interface, que
        les ffmpeg qui le lisaient aient fini : jusqu'a une seconde et demie
        de gel a chaque touche de tri. Ils sont desormais arretes net, la
        recolte s'ecarte, et aucune lecture nouvelle ne s'y ouvre pendant
        `timeout_ms` — le transfert, qui reessaie de toute facon, fait le
        reste.
        """
        self.cancel_all()
        if self.harvester is not None:
            self.harvester.hold()
        release_reads(target, timeout_ms / 1000.0)

    def tune_for(self, root) -> None:
        """Adapte le nombre d'extractions simultanées au support de stockage."""
        # Huit, et pas davantage. Une extraction attend le reseau plus qu'elle
        # n'occupe le processeur, mais au-dela le partage se met a pietiner :
        # mesure sur le NAS, 1,49 s par image a huit de front, 1,58 a quatre, et
        # 3,31 a seize — deux fois pire. Le debit d'un partage ne s'additionne
        # pas indefiniment, il s'ecroule.
        workers = 8 if is_network_path(root) else self.local_workers
        if workers != self.pool.maxThreadCount():
            self.pool.setMaxThreadCount(workers)

    def start_harvest(self, tasks: list) -> "Harvester":
        """Lance, ou relance, la fabrication d'avance des vignettes."""
        self.stop_harvest()
        self.harvester = Harvester(self.thumb_width, self)
        self.harvester.tasks = tasks
        self.harvester.start(QThread.LowestPriority)
        return self.harvester

    def stop_harvest(self) -> None:
        """Arrete la recolte sans l'attendre.

        On attendait qu'elle ait fini l'image en cours : sur le partage, une
        seconde — a chaque onglet, a chaque dossier ouvert. Elle s'arrete
        d'elle-meme a la fin de cette image ; on la garde en vie jusque-la.
        """
        if self.harvester is not None:
            old = self.harvester
            self.harvester = None
            old.stop()
            with warnings.catch_warnings():
                # Rien de branche : PySide le signale, il n'y a rien a en dire.
                warnings.simplefilter("ignore", RuntimeWarning)
                for signal in (old.progress, old.finished_harvest):
                    try:
                        signal.disconnect()
                    except (RuntimeError, TypeError):
                        pass
            self._retired.append(old)
        self._reap()

    def _reap(self) -> None:
        """Libere les recoltes terminees.

        Rien ne les detruisait : chacune restait en memoire avec ses taches,
        et il en nait une a chaque onglet, a chaque dossier ouvert. Seules
        celles qui tournent encore restent en vie, jusqu'a leur fin.
        """
        alive = []
        for old in self._retired:
            if old.isRunning():
                alive.append(old)
            else:
                old.deleteLater()
        self._retired = alive

    def shutdown(self) -> None:
        self.stop_harvest()
        # A la fermeture, rien ne sert d'attendre la fin des images en cours.
        for old in self._retired:
            old.kill()
        self.cancel_all(kill=True)
        for old in self._retired:
            old.wait(2000)
        self._reap()
        self.quiesce(2000)
        INDEX.commit(force=True)
