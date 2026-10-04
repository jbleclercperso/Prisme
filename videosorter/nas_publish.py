"""Publier le partage sur le NAS : la bibliothèque reste joignable PC éteint.

Prisme, sur le PC, reste la version maître. Le partage, lui, peut vivre sur le
NAS, là où sont déjà les vidéos : Prisme y dépose, dans `.prisme-partage` à la
racine du partage réseau, tout ce qu'un petit serveur a besoin de montrer --

  catalogue.json   les dossiers et les vidéos, par empreinte, chemins relatifs
  acces.json       la clé du lien d'invitation, et le mot de passe (haché)
  vignettes/       une image par vidéo, nommée par son empreinte
  programme/       le serveur (`nas/serveur.py`) et les modules qu'il emploie
  installation/    de quoi le lancer dans Docker, avec Tailscale

Tout s'écrit d'un bloc (fichier temporaire, puis renommage) : le serveur du NAS
ne lit jamais un catalogue à moitié écrit. Les vignettes ne se recopient que si
elles manquent là-bas : la première fois est longue, les suivantes non.
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path, PureWindowsPath

FOLDER = ".prisme-partage"
NAS_PORT = 8714
# Les modules que le serveur du NAS importe : rien qui tire Qt.
PROGRAM = ("__init__.py", "web.py", "access.py", "config.py", "query.py",
           "textfold.py", "brand_data.py", "demandes.py")
REPO = Path(__file__).resolve().parents[1]


def share_root(top) -> Path | None:
    """La racine du partage réseau (\\\\serveur\\partage\\) d'un dossier, ou None
    s'il n'est pas sur un partage : il n'y a alors pas de NAS où publier."""
    from . import roots
    if roots.is_union(top):
        # Plusieurs racines : celle qui est sur un partage, s'il y en a une.
        for member in roots.members(top):
            found = share_root(member)
            if found is not None:
                return found
        return None
    anchor = PureWindowsPath(unc(top)).anchor
    if not anchor.startswith("\\\\"):
        return None
    return Path(anchor)


@functools.lru_cache(maxsize=32)
def _mapped(drive: str) -> str:
    """« Z: » -> « \\\\as1104t\\Volume 3 » si c'est un lecteur reseau, sinon ""."""
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes
    size = wintypes.DWORD(1024)
    buffer = ctypes.create_unicode_buffer(size.value)
    try:
        code = ctypes.windll.mpr.WNetGetConnectionW(drive, buffer, ctypes.byref(size))
    except (AttributeError, OSError):
        return ""
    return buffer.value if code == 0 else ""


def unc(path) -> str:
    """Le chemin reseau d'un chemin, meme pris par une lettre de lecteur.

    Un PC voit le NAS en « \\\\as1104t\\Volume 3 », l'autre en « Z: » : pour ce
    dernier, Prisme disait « la bibliothèque n'est pas sur un NAS », et ne
    publiait rien -- ou un catalogue vide, aucune video « Z:\\… » ne se
    trouvant sous « \\\\as1104t\\Volume 3 ». Un chemin local reste tel quel."""
    text = str(path)
    drive = PureWindowsPath(text).drive
    if len(drive) == 2 and drive[1] == ":":
        target = _mapped(drive)
        if target:
            return target.rstrip("\\/") + text[2:]
    return text


def nas_address(root) -> str:
    """L'adresse IPv4 du NAS sur le reseau de la maison, d'apres son nom de
    partage. Un telephone ne connait pas ce nom : il lui faut l'adresse."""
    import socket
    host = PureWindowsPath(str(root)).drive.lstrip("\\/").split("\\")[0]
    if not host:
        return ""
    try:
        found = socket.getaddrinfo(host, None, socket.AF_INET)
    except OSError:
        return ""
    return found[0][4][0] if found else ""


def _write_atomic(target: Path, text: str) -> None:
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, target)


def _copy_if_changed(source: Path, target: Path) -> bool:
    try:
        if target.exists() and target.read_bytes() == source.read_bytes():
            return False
    except OSError:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


class Publisher:
    """Une publication à la fois, dans un fil ; la suivante attend la fin."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.again = None
        self.state = "Jamais publié sur le NAS."
        self.last_ok = 0.0
        # L'adresse du NAS sur le reseau de la maison, pour le lien Wi-Fi.
        self.lan = ""

    def start(self, library, top, access: dict, tailnet: str = "",
              icons: dict | None = None) -> bool:
        root = share_root(top)
        if root is None:
            self.state = "La bibliothèque n'est pas sur un NAS : rien à publier."
            return False
        with self.lock:
            if self.running:
                self.again = (library, top, access, tailnet, icons)
                return True
            self.running = True
        threading.Thread(target=self._run, args=(library, root, access, tailnet, icons),
                         name="prisme-publication-nas", daemon=True).start()
        return True

    def _run(self, library, root: Path, access: dict, tailnet: str,
             icons: dict | None = None) -> None:
        self.lan = nas_address(root) or self.lan
        try:
            self.state = "Publication sur le NAS…"
            self.state = publish(library, root, access, tailnet,
                                 progress=self._progress, icons=icons)
            self.last_ok = time.time()
        except Exception as trouble:                   # noqa: BLE001
            self.state = f"Publication sur le NAS impossible : {trouble}"
        finally:
            with self.lock:
                self.running = False
                again, self.again = self.again, None
            if again is not None:
                self.start(*again)

    def _progress(self, text: str) -> None:
        self.state = text


PUBLISHER = Publisher()


def _write_access(target: Path, access: dict, tailnet: str) -> None:
    """La cle du lien et la serrure, que le serveur du NAS relit seul."""
    # L'adresse https du NAS : le lien Wi-Fi (http) y envoie le telephone
    # pour poser l'icone, aucun telephone n'installant une page http.
    if tailnet:
        access = dict(access, secure=f"https://prisme-nas.{tailnet}")
    _write_atomic(target / "acces.json", json.dumps(access))


# Un autre PC ne remplace la liste que s'il en voit au moins autant, a 10 %
# pres (ce qui a ete supprime ou range entre-temps).
CATALOGUE_KEEP = 0.9


def _catalogue_head(place: Path) -> tuple:
    """(nombre de videos, PC qui l'a publie) du catalogue en place ;
    (0, "") s'il n'y en a pas, ou d'avant que les PC signent."""
    try:
        told = json.loads(place.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, ""
    if not isinstance(told, dict):
        return 0, ""
    return len(told.get("videos") or {}), str(told.get("by") or "")


def published_access(top) -> dict | None:
    """La cle et la serrure deja publiees sur le NAS, ou None (pas de NAS,
    rien de publie, illisible). Lu hors du fil de l'interface."""
    root = share_root(top)
    if root is None:
        return None
    try:
        found = json.loads((root / FOLDER / "acces.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def publish_access(top, access: dict, tailnet: str = "") -> bool:
    """Seulement la cle, sans catalogue ni serveur du PC.

    « Nouveau lien », partage coupe, ne prevenait pas le NAS : l'ancien lien
    y restait valable, et le nouveau y etait refuse. Rend faux s'il n'y a pas
    de NAS, ou s'il n'a jamais ete prepare."""
    root = share_root(top)
    if root is None:
        return False
    target = root / FOLDER
    try:
        if not target.is_dir():
            return False
        _write_access(target, access, tailnet)
    except OSError:
        return False
    return True


def publish(library, root: Path, access: dict, tailnet: str = "",
            progress=lambda _text: None, icons: dict | None = None) -> str:
    """Dépose sur le NAS ce que le partage doit montrer. Rend un compte rendu."""
    from .index import INDEX
    from .scan import human_duration

    target = root / FOLDER
    target.mkdir(parents=True, exist_ok=True)
    # Un lecteur reconnecte sous une autre lettre depuis la derniere fois.
    _mapped.cache_clear()
    root_text = str(root).rstrip("\\/")

    # -- le catalogue : chemins relatifs a la racine du partage ------------
    with library._lock:
        folders = list(library.folders)
        by_folder = dict(library.by_folder)
        videos = dict(library.videos)
    entries = {}
    for mark, path in videos.items():
        text = unc(path)
        if not text.lower().startswith(root_text.lower()):
            continue
        rel = text[len(root_text):].lstrip("\\/").replace("\\", "/")
        info = INDEX.probe(path) or {}
        entries[mark] = {
            "rel": rel,
            "duration": (human_duration(info["duration"])
                         if info.get("duration") else ""),
            "height": info.get("height") or 0,
        }
    # La cle d'abord : un nouveau lien doit marcher sur le NAS meme quand le
    # catalogue, lui, n'est pas pret.
    _write_access(target, access, tailnet)
    if not entries:
        # Une bibliotheque pas encore lue (le NAS pas encore joignable au
        # lancement, sous « Toutes les racines ») publiait un catalogue vide
        # par-dessus le bon : le telephone ne voyait plus rien.
        return (f"Clé du lien à jour sur le NAS le {time.strftime('%d/%m à %H:%M')} ; "
                "catalogue gardé tel quel, la bibliothèque n'est pas encore lue.")
    kept = {key: [m for m in marks if m in entries] for key, marks in by_folder.items()}
    folders = [f for f in folders if kept.get(f["id"])]
    me = socket.gethostname()
    count, author = _catalogue_head(target / "catalogue.json")
    # Deux PC publient sur le meme NAS. Celui qui voit moins (une autre
    # racine, une bibliotheque pas finie de lire) ne remplace pas la liste de
    # l'autre : le telephone ne doit pas dependre du PC allume. Le meme PC,
    # lui, remplace toujours la sienne : c'est ainsi que les suppressions
    # passent.
    left_alone = (author and author != me
                  and len(entries) < count * CATALOGUE_KEEP)
    if not left_alone:
        catalogue = {"version": f"{time.time_ns():x}",
                     "made": time.strftime("%Y-%m-%d %H:%M:%S"), "by": me,
                     "folders": folders, "by_folder": kept, "videos": entries}
        _write_atomic(target / "catalogue.json",
                      json.dumps(catalogue, ensure_ascii=False, separators=(",", ":")))

    # -- le programme : recopie seulement s'il a change ---------------------
    program = target / "programme"
    for name in PROGRAM:
        _copy_if_changed(REPO / "videosorter" / name, program / "videosorter" / name)
    _copy_if_changed(REPO / "nas" / "serveur.py", program / "serveur.py")
    _write_installation(target / "installation", tailnet)
    # L'icone de l'application, pour l'ecran d'accueil du telephone.
    for size, image in (icons or {}).items():
        try:
            place = target / "icones" / f"{size}.png"
            if not place.exists() or place.read_bytes() != image:
                place.parent.mkdir(parents=True, exist_ok=True)
                place.write_bytes(image)
        except OSError:
            pass

    # -- les vignettes : seulement celles qui manquent la-bas ---------------
    thumbs = target / "vignettes"
    thumbs.mkdir(exist_ok=True)
    there = {}
    try:
        for entry in os.scandir(thumbs):
            there[entry.name] = entry.stat().st_size
    except OSError:
        pass
    copied = missing = 0
    total = len(entries)
    for done, mark in enumerate(entries, 1):
        source = library.thumb_file(videos[mark], mark)
        try:
            size = source.stat().st_size
        except OSError:
            missing += 1
            continue
        name = f"{mark}.jpg"
        if there.get(name) == size:
            continue
        try:
            shutil.copyfile(source, thumbs / name)
            copied += 1
        except OSError:
            missing += 1
        if copied and copied % 200 == 0:
            progress(f"Publication sur le NAS : vignettes {done} / {total}…")
    if left_alone:
        return (f"Clé et vignettes à jour sur le NAS le "
                f"{time.strftime('%d/%m à %H:%M')} ; liste gardée, celle de "
                f"{author} ({count} vidéos) : ce PC n'en voit que {len(entries)}.")
    return (f"Publié sur le NAS le {time.strftime('%d/%m à %H:%M')} : "
            f"{len(folders)} dossiers, {len(entries)} vidéos, "
            f"{copied} nouvelle(s) vignette(s)"
            + (f", {missing} pas encore prête(s)" if missing else "") + ".")


def _write_installation(where: Path, tailnet: str) -> None:
    """Les deux fichiers à donner à Docker sur le NAS, et le mode d'emploi."""
    where.mkdir(parents=True, exist_ok=True)
    host = "prisme-nas"
    address = f"https://{host}.{tailnet}" if tailnet else f"https://{host}.<votre-tailnet>.ts.net"
    serve = {
        "TCP": {"443": {"HTTPS": True}},
        "Web": {"${TS_CERT_DOMAIN}:443": {"Handlers": {"/": {
            "Proxy": f"http://127.0.0.1:{NAS_PORT}"}}}},
        "AllowFunnel": {"${TS_CERT_DOMAIN}:443": True},
    }
    _write_atomic(where / "serve.json", json.dumps(serve, indent=2))
    compose = f"""# Prisme -- le partage sur le NAS, joignable PC eteint.
# A coller dans Portainer (Stacks > Add stack), apres avoir remplace :
#   CHEMIN_DU_PARTAGE  le dossier du NAS qui contient vos videos
#                      (celui que Windows appelle \\\\as1104t\\Volume 3)
#   TSKEY-...          une cle d'authentification Tailscale
services:
  tailscale:
    image: tailscale/tailscale:latest
    hostname: {host}
    restart: unless-stopped
    # Directement sur la connexion du NAS : le reseau interne de Docker, sur
    # un Asustor, ne laissait rien sortir (« i/o timeout » vers Tailscale).
    network_mode: host
    environment:
      - TS_AUTHKEY=TSKEY-A-REMPLACER
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_SERVE_CONFIG=/config/serve.json
      - TS_USERSPACE=true
    volumes:
      # L'identite Tailscale du NAS : dans un volume de Docker, pas sur le
      # partage ou n'importe quel poste du reseau pourrait la lire.
      - prisme-tailscale:/var/lib/tailscale
      - CHEMIN_DU_PARTAGE/{FOLDER}/installation:/config
  prisme:
    image: python:3.13-slim
    restart: unless-stopped
    network_mode: service:tailscale
    working_dir: /partage/programme
    command: ["python", "-u", "serveur.py"]
    environment:
      - PRISME_PORT={NAS_PORT}
    volumes:
      - CHEMIN_DU_PARTAGE:/bibliotheque:ro
      - CHEMIN_DU_PARTAGE/{FOLDER}:/partage

volumes:
  prisme-tailscale:
"""
    _write_atomic(where / "docker-compose.yml", compose)
    _write_atomic(where / "LISEZMOI.txt", f"""Prisme sur le NAS
=================

Une fois lance, la bibliotheque est joignable meme PC eteint :
  - de n'importe ou : {address}/entrer/<cle>
  - sur le Wi-Fi     : http://<adresse du NAS>:{NAS_PORT}/entrer/<cle>
Les liens complets, avec la cle, sont dans Prisme : Partage a distance > Via le NAS.

Prisme (sur le PC) republie le catalogue et les vignettes apres chaque analyse ;
le serveur du NAS les recharge tout seul en moins d'une minute.
""")
