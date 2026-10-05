"""Le partage de Prisme, sur le NAS : la bibliothèque reste joignable PC éteint.

Prisme, sur le PC, reste la version maître : c'est lui qui analyse, trie et
fabrique les vignettes. A chaque analyse, il dépose dans `.prisme-partage`, sur
le NAS, ce que le partage doit montrer -- le catalogue, les vignettes, la clé du
lien d'invitation -- et ce programme même. Ici, on ne fait que servir : les
vidéos sont lues directement sur le disque du NAS, sans passer par le PC.

Rien d'autre que Python n'est nécessaire (l'image Docker `python:3.13-slim`).

Dossiers, vus depuis le conteneur :
  /bibliotheque   le partage du NAS qui contient les vidéos (lecture seule)
  /partage        le dossier `.prisme-partage` que Prisme remplit
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARE = Path(os.environ.get("PRISME_PARTAGE", "/partage"))
LIBRARY_ROOT = Path(os.environ.get("PRISME_BIBLIOTHEQUE", "/bibliotheque"))
PORT = int(os.environ.get("PRISME_PORT", "8714"))
CATALOGUE = SHARE / "catalogue.json"
ACCESS = SHARE / "acces.json"
THUMBS = SHARE / "vignettes"
# Qui regarde quoi, en ce moment : Prisme, sur le PC, le lit pour son compteur.
LIVE = SHARE / "etat" / "en-direct.json"
# Le journal des visites vit a cote, et nulle part ailleurs (voir config.py).
os.environ.setdefault("PRISME_SANDBOX", str(SHARE / "etat"))
sys.path.insert(0, str(HERE))
# Les liens envoyes du telephone : les videos arrivent ici, et l'outil qui
# les telecharge (yt-dlp, ffmpeg) s'installe a cote, une fois -- hors du
# dossier du programme, que Prisme remplace (et dont un changement relance
# le serveur).
DOWNLOADS = SHARE / "telechargements"
TOOLS = SHARE / "outils" / "python"
RENAMES = SHARE / "etat" / "renommes.jsonl"
sys.path.insert(0, str(TOOLS))
TOOLS_READY = threading.Event()

from videosorter import web  # noqa: E402
from videosorter.access import JOURNAL  # noqa: E402


class NasLibrary(web.Library):
    """La bibliothèque telle que Prisme l'a publiée : les mêmes dossiers, les
    mêmes empreintes, mais des chemins vus depuis le NAS."""

    def __init__(self):
        self.entries: dict = {}
        self.loaded_at = 0.0
        super().__init__(LIBRARY_ROOT, True, 480)

    def refresh(self) -> None:
        try:
            told = json.loads(CATALOGUE.read_text(encoding="utf-8"))
            stamp = CATALOGUE.stat().st_mtime
        except (OSError, ValueError) as trouble:
            print(f"catalogue illisible : {trouble}", flush=True)
            return
        videos, entries = {}, {}
        for mark, entry in (told.get("videos") or {}).items():
            path = LIBRARY_ROOT / Path(*entry["rel"].split("/"))
            videos[mark] = path
            entries[mark] = entry
        with self._lock:
            self.folders = told.get("folders") or []
            self.by_folder = told.get("by_folder") or {}
            self.videos = videos
            self.entries = entries
            self.version = str(told.get("version") or f"{time.time_ns():x}")
            self.built_at = self.loaded_at = stamp
            self._merge_extras(catalogue=True)
            self._whole = None
            self._folded = None
        print(f"catalogue chargé : {len(self.folders)} dossiers, "
              f"{len(videos)} fichiers", flush=True)

    def thumb_file(self, path: Path, mark: str = "") -> Path:
        # Deposee par Prisme sous l'empreinte de la video.
        return THUMBS / f"{mark}.jpg"

    def video_entry(self, mark: str) -> dict | None:
        path = self.videos.get(mark)
        if mark in self.extras and path is not None:
            info = self.extras[mark][1]
            return {"id": mark, "name": path.name, "folder": path.parent.name,
                    "duration": info.get("duration", ""), "height": info.get("height", 0)}
        entry = self.entries.get(mark)
        if path is None or entry is None:
            return None
        return {
            "id": mark,
            "name": path.name,
            "folder": path.parent.name,
            "duration": entry.get("duration", ""),
            "height": entry.get("height", 0),
        }


# -- les liens envoyes du telephone -------------------------------------------
def _install_tools() -> None:
    """yt-dlp (lire les sites), requests et BeautifulSoup (lire les pages, comme
    la recherche web du PC), et un ffmpeg tout fait pour ce
    processeur (imageio-ffmpeg) : installes dans le partage au premier
    lancement, remis a jour chaque semaine -- les sites changent, yt-dlp suit."""
    stamp = TOOLS / ".installe"
    try:
        fresh = time.time() - stamp.stat().st_mtime < 7 * 86400
    except OSError:
        fresh = False
    try:
        import bs4  # noqa: F401
        import requests  # noqa: F401
        import yt_dlp  # noqa: F401
        have = True
    except ImportError:
        have = False
    if not (have and fresh):
        print("outil de téléchargement : installation…", flush=True)
        try:
            TOOLS.mkdir(parents=True, exist_ok=True)
            done = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade",
                 "--disable-pip-version-check", "--no-cache-dir", "--target", str(TOOLS),
                 "yt-dlp", "requests", "beautifulsoup4", "imageio-ffmpeg"],
                capture_output=True, text=True, timeout=1800)
            if done.returncode == 0:
                stamp.touch()
                print("outil de téléchargement : prêt", flush=True)
            else:
                print("outil de téléchargement : échec de l'installation\n"
                      + (done.stderr or done.stdout)[-1500:], flush=True)
        except (OSError, subprocess.SubprocessError) as trouble:
            print(f"outil de téléchargement : {trouble}", flush=True)
        importlib.invalidate_caches()
    try:
        import yt_dlp  # noqa: F401,F811
        TOOLS_READY.set()
    except ImportError:
        pass


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        found = imageio_ffmpeg.get_ffmpeg_exe()
        if found and not os.access(found, os.X_OK):
            os.chmod(found, 0o755)
        return found
    except Exception:                                   # noqa: BLE001
        return shutil.which("ffmpeg") or ""


def _describe(path: Path, mark: str) -> dict:
    """Duree et hauteur d'une video arrivee, lues par ffmpeg ; et sa vignette,
    a la place ou le partage les cherche."""
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        return {}
    info: dict = {}
    try:
        told = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                              capture_output=True, text=True, timeout=60).stderr
    except (OSError, subprocess.SubprocessError):
        return info
    found = re.search(r"Duration: (\d+):(\d+):(\d+)", told)
    seconds = 0
    if found:
        hours, minutes, secs = (int(part) for part in found.groups())
        seconds = hours * 3600 + minutes * 60 + secs
        info["duration"] = (f"{hours}:{minutes:02d}:{secs:02d}" if hours
                            else f"{minutes}:{secs:02d}")
    size = re.search(r"Video:.*?, (\d{2,5})x(\d{2,5})", told)
    if size:
        info["height"] = int(size.group(2))
    try:
        THUMBS.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", str(max(1, seconds // 10)), "-i", str(path),
                        "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4",
                        str(THUMBS / f"{mark}.jpg")],
                       capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def _arrived(library: NasLibrary, job: dict, path: Path) -> str:
    """Une video telechargee : dans la bibliotheque, et dans les favoris de
    qui l'a envoyee."""
    from videosorter.liens import temp_mark
    mark = temp_mark(path.name)
    library.add_download(path, mark, _describe(path, mark))
    JOURNAL.set_favorite(job.get("label", ""), mark, path.name, True)
    print(f"lien téléchargé : {path.name}", flush=True)
    return mark


def _known_downloads(library: NasLibrary) -> None:
    """Au lancement : les videos deja arrivees, et pas encore rangees."""
    from videosorter.liens import VIDEO_EXT, temp_mark
    try:
        found = [p for p in DOWNLOADS.iterdir()
                 if p.is_file() and p.suffix.lower() in VIDEO_EXT]
    except OSError:
        return
    for path in found:
        mark = temp_mark(path.name)
        info = {}
        if not (THUMBS / f"{mark}.jpg").exists():
            info = _describe(path, mark)
        library.add_download(path, mark, info)


_renames_seen = [0.0]


def _apply_renames(library: NasLibrary) -> None:
    """Prisme a range des videos arrivees dans la collection : leurs favoris
    et visionnages suivent la nouvelle empreinte, et la video reste visible a
    sa nouvelle place jusqu'au prochain catalogue."""
    try:
        stamp = RENAMES.stat().st_mtime
    except OSError:
        return
    if stamp == _renames_seen[0]:
        return
    _renames_seen[0] = stamp
    try:
        lines = RENAMES.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return
    for line in lines:
        try:
            told = json.loads(line)
        except ValueError:
            continue
        old, new, rel = told.get("old", ""), told.get("new", ""), told.get("rel", "")
        if not (old and new and rel):
            continue
        JOURNAL.rename_video(old, new)
        if new in library.entries:
            continue
        path = LIBRARY_ROOT / Path(*rel.split("/"))
        if path.exists() and new not in library.extras:
            library.add_download(path, new, {})


def _access() -> dict:
    try:
        return json.loads(ACCESS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _program_stamp() -> tuple:
    """Les dates des fichiers du programme : Prisme les remplace quand il a
    change, et c'est le signe qu'il faut repartir sur la nouvelle version."""
    stamps = []
    for path in sorted(HERE.rglob("*.py")):
        try:
            stamps.append((str(path), path.stat().st_mtime))
        except OSError:
            pass
    return tuple(stamps)


def _write_live(server: web.Server) -> None:
    """Depose la liste des visiteurs du moment, d'un bloc."""
    try:
        LIVE.parent.mkdir(parents=True, exist_ok=True)
        temporary = LIVE.with_name(LIVE.name + ".tmp")
        temporary.write_text(json.dumps({"at": time.time(),
                                         "viewers": server.viewers()}),
                             encoding="utf-8")
        os.replace(temporary, LIVE)
    except OSError:
        pass


def _watch(server: web.Server, library: NasLibrary) -> None:
    """Recharge ce que Prisme republie : le catalogue, la clé du lien -- et le
    programme lui-même, en redémarrant (Docker le relance : « restart »)."""
    seen_catalogue, seen_access = library.loaded_at, _access()
    seen_program = _program_stamp()
    while True:
        time.sleep(10)
        _write_live(server)
        forget = SHARE / "etat" / "effacer-vues"
        if forget.exists():
            # Prisme a efface « Ce qui a ete regarde » : ici aussi.
            JOURNAL.clear_views()
            try:
                forget.unlink()
            except OSError:
                pass
            print("journal des visionnages effacé", flush=True)
        _apply_renames(library)
        if _program_stamp() != seen_program:
            # Laisser a Prisme le temps de finir de tout recopier.
            time.sleep(5)
            print("nouvelle version du programme : redémarrage", flush=True)
            os._exit(0)
        try:
            if CATALOGUE.stat().st_mtime != seen_catalogue:
                library.refresh()
                seen_catalogue = library.loaded_at
        except OSError:
            pass
        access = _access()
        if access and access != seen_access:
            changed_key = access.get("invite") != seen_access.get("invite")
            server.invite = access.get("invite", "")
            server.salt = access.get("salt", "")
            server.digest = access.get("digest", "")
            server.secure = access.get("secure", "")
            if changed_key:
                # Un nouveau lien sur le PC : l'ancien ne mene plus nulle part,
                # ici non plus.
                with server.guard.lock:
                    server.guard.sessions.clear()
            seen_access = access
            print("accès mis à jour", flush=True)


def main() -> None:
    library = NasLibrary()
    access = _access()
    JOURNAL.open()
    server = web.Server(LIBRARY_ROOT, access.get("salt", ""), access.get("digest", ""),
                        port=PORT, host="0.0.0.0", invite=access.get("invite", ""),
                        library=library)
    server.secure = access.get("secure", "")
    # Les demandes envoyees du telephone : dans le partage, ou Prisme les lit.
    server.requests_path = SHARE / "etat" / "demandes.jsonl"
    # Les liens envoyes du telephone : le NAS les telecharge lui-meme, PC
    # eteint, et les met dans les favoris de qui les a envoyes.
    from videosorter.liens import LinkJobs
    server.downloads = LinkJobs(DOWNLOADS, SHARE / "etat" / "liens.json",
                                on_ready=lambda job, path: _arrived(library, job, path),
                                ffmpeg=_ffmpeg, available=TOOLS_READY.is_set)
    threading.Thread(target=_install_tools, name="outils", daemon=True).start()
    _known_downloads(library)
    _apply_renames(library)
    # Les icones de l'application, deposees par Prisme.
    for size in (180, 192, 512):
        try:
            server.icons[size] = (SHARE / "icones" / f"{size}.png").read_bytes()
        except OSError:
            pass
    port = server.start()
    print(f"Prisme partage : à l'écoute sur le port {port}", flush=True)
    threading.Thread(target=_watch, args=(server, library), daemon=True).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
