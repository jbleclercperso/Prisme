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

import json
import os
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
            self._whole = None
            self._folded = None
        print(f"catalogue chargé : {len(self.folders)} dossiers, "
              f"{len(videos)} fichiers", flush=True)

    def thumb_file(self, path: Path, mark: str = "") -> Path:
        # Deposee par Prisme sous l'empreinte de la video.
        return THUMBS / f"{mark}.jpg"

    def video_entry(self, mark: str) -> dict | None:
        path = self.videos.get(mark)
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
