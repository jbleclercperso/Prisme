"""Prisme à distance : la bibliothèque dans un navigateur, protégée par un mot de passe.

L'application de bureau ne s'ouvre pas par un lien : c'est un programme Qt.
Ce module sert donc la même collection en HTML, et réutilise tout ce qui a
déjà coûté cher — l'index des dossiers, les vignettes déjà fabriquées. Un
téléphone au bout du monde voit alors la bibliothèque du NAS, et lit les
vidéos sans rien télécharger d'entier.

Trois précautions, parce qu'il s'agit d'ouvrir une collection personnelle :

* **Le serveur n'écoute que la machine elle-même** par défaut. C'est le
  tunnel — Cloudflare, Tailscale — qui le rend joignable de l'extérieur, sans
  jamais ouvrir un port sur la box.
* **Aucun chemin ne circule.** Chaque vidéo est désignée par une empreinte, et
  le serveur ne sert que ce que l'index connaît : demander
  `../../Windows/System32` ne mène nulle part.
* **Le mot de passe n'est jamais gardé en clair**, et les essais sont freinés
  puis bloqués — par visiteur, pas pour tout le monde : derrière le tunnel,
  chacun est reconnu à l'adresse que le tunnel a vue.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import secrets
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .access import JOURNAL
from .config import THUMB_DIR  # noqa: F401  (le cache des vignettes)
from .index import INDEX
from .media import BLIND_START, thumb_path
from .query import parse, tester
from .scan import (
    MODE_FOLDERS, cached_items, human_duration, human_size, under_veiled,
)
from .tagging import fold

# Combien de temps une session reste ouverte sans qu'on ait à se réauthentifier.
SESSION_HOURS = 24 * 14
# Essais manques avant blocage, pour un meme visiteur. Le premier blocage dure
# `LOCKOUT` secondes, et double a chaque nouvel echec, jusqu'a `LOCKOUT_MAX` :
# un blocage fixe laissait un essai toutes les cinq minutes, indefiniment.
MAX_TRIES = 8
LOCKOUT = 300
LOCKOUT_MAX = 6 * 3600
# Un visiteur sans echec depuis ce temps repart de zero : les fautes de frappe
# d'hier ne comptent pas contre aujourd'hui.
FORGET = 24 * 3600
# Une verification a la fois (le PBKDF2 prend un quart de seconde de
# processeur) ; au-dela de cette attente, on demande de revenir plutot que
# d'empiler des fils qui attendent.
CHECK_WAIT = 10
# Morceaux envoyés au navigateur : assez gros pour que le partage suive, assez
# petits pour qu'un saut dans la vidéo réponde tout de suite.
CHUNK = 512 * 1024
# Ce qu'on rend d'un coup quand le navigateur demande « la suite » sans borne :
# de quoi lancer la lecture, sans lire le fichier entier sur le NAS.
OPEN_RANGE = CHUNK * 8
# Les dossiers partent par pages : la premiere s'affiche au bout de quelques
# kilo-octets, au lieu d'attendre sept mega-octets en 4G.
PAGE = 300
PAGE_MAX = 1000
# En dessous, compresser coute plus que ce que l'on gagne.
GZIP_MIN = 1024
JSON_KIND = "application/json; charset=utf-8"


# ---------------------------------------------------------------------------
# Le mot de passe : jamais en clair, nulle part
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: str = "") -> tuple:
    """(sel, empreinte) — PBKDF2, deux cent mille tours.

    Même si le fichier de configuration tombait entre de mauvaises mains, le
    mot de passe ne s'en déduirait pas en un temps raisonnable.
    """
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return salt, digest.hex()


def password_ok(password: str, salt: str, expected: str) -> bool:
    if not salt or not expected:
        return False
    _salt, found = hash_password(password, salt)
    # Comparaison à temps constant : autrement, la durée de la réponse
    # renseignerait sur le nombre de caractères devinés.
    return hmac.compare_digest(found, expected)


# ---------------------------------------------------------------------------
# Le catalogue : ce que le serveur accepte de montrer
# ---------------------------------------------------------------------------

class Library:
    """Les dossiers et les vidéos connus de l'index, désignés par empreinte.

    Rien n'est relu sur le partage pour dresser cette liste : c'est l'index
    que l'application a rempli en analysant, et il tient en mémoire.
    """

    def __init__(self, root: Path, expand: bool = True, width: int = 480):
        self.root = Path(root)
        self.expand = expand
        self.width = width
        self.folders: list = []
        self.videos: dict = {}        # empreinte -> chemin
        self.by_folder: dict = {}     # empreinte de dossier -> [empreintes]
        self.built_at = 0.0
        # Change a chaque refresh : c'est l'ETag du catalogue, et ce qui dit
        # au navigateur que la grille qu'il garde est encore la bonne.
        self.version = ""
        self._lock = threading.Lock()
        self._whole = None            # (version, json, json compresse)
        self._folded = None           # (version, [(empreinte, nom replie)])
        self.refresh()

    @staticmethod
    def mark(path) -> str:
        """L'empreinte courte qui désigne un fichier dans les adresses.

        Le chemin lui-même ne circule pas : il dirait où vivent les fichiers,
        et permettrait d'en demander d'autres.
        """
        return hashlib.sha1(str(path).encode("utf-8", "replace")).hexdigest()[:16]

    def refresh(self) -> None:
        folders = []
        videos = {}
        by_folder = {}
        for item in cached_items(self.root, MODE_FOLDERS, self.expand):
            if item.is_tag or not item.videos or under_veiled(item.path):
                # Ce qui est masqué ici l'est aussi au dehors : l'adresse
                # publique ne doit pas montrer ce que la fenêtre cache.
                continue
            key = self.mark(item.path)
            inside = []
            for video in item.videos:
                if under_veiled(video):
                    continue
                mark = self.mark(video)
                videos[mark] = Path(video)
                inside.append(mark)
            by_folder[key] = inside
            folders.append({
                "id": key,
                "name": item.path.name or str(item.path),
                "count": item.video_count or len(inside),
                "size": human_size(item.size),
                "cover": inside[0] if inside else "",
            })
        folders.sort(key=lambda entry: entry["name"].lower())
        now = time.time()
        with self._lock:
            self.folders, self.videos, self.by_folder = folders, videos, by_folder
            self.built_at = now
            self.version = f"{time.time_ns():x}"
            # Le reste se refait a la premiere demande, sur le fil du serveur :
            # replier cent mille noms ici retarderait la fenetre.
            self._whole = None
            self._folded = None

    def folders_page(self, start: int, count: int) -> tuple:
        """(version, total, dossiers de `start` a `start + count`)."""
        with self._lock:
            version, folders = self.version, self.folders
        start = max(0, start)
        return version, len(folders), folders[start:start + max(0, count)]

    def whole(self) -> tuple:
        """(version, json, json compresse) de tout le catalogue, fait une fois.

        Le reserialiser a chaque demande tenait le verrou global de Python
        pendant cent cinquante millisecondes — autant de gel pour la fenetre.
        """
        with self._lock:
            version, folders, kept = self.version, self.folders, self._whole
        if kept is not None and kept[0] == version:
            return kept
        body = json.dumps(
            {"folders": folders, "total": len(folders), "version": version},
            ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        kept = (version, body, gzip.compress(body, compresslevel=5, mtime=0))
        with self._lock:
            if self.version == version:
                self._whole = kept
        return kept

    def _folded_names(self) -> list:
        """[(empreinte, nom replie)] des videos, replies une fois par catalogue."""
        with self._lock:
            version, videos, kept = self.version, self.videos, self._folded
        if kept is not None and kept[0] == version:
            return kept[1]
        names = [(mark, fold(path.name)) for mark, path in videos.items()]
        with self._lock:
            if self.version == version:
                self._folded = (version, names)
        return names

    def video_entry(self, mark: str) -> dict | None:
        path = self.videos.get(mark)
        if path is None:
            return None
        info = INDEX.probe(path) or {}
        return {
            "id": mark,
            "name": path.name,
            "folder": path.parent.name,
            "duration": human_duration(info["duration"]) if info.get("duration") else "",
            "height": info.get("height") or 0,
        }

    def search(self, text: str, limit: int = 300) -> list:
        # La recherche est analysee une fois, et les noms sont deja replies :
        # on la reanalysait pour chacun des cent mille noms.
        test = tester(parse(text))
        found = []
        for mark, folded in self._folded_names():
            if test(folded):
                entry = self.video_entry(mark)
                if entry:
                    found.append(entry)
                if len(found) >= limit:
                    break
        return found


# ---------------------------------------------------------------------------
# Le serveur
# ---------------------------------------------------------------------------

def visitor(peer: str, forwarded: str = "") -> str:
    """L'adresse du visiteur : celle du pair, ou celle que le tunnel a vue.

    Derriere cloudflared ou Tailscale, tout arrive de 127.0.0.1 : compter les
    essais par pair revenait a les compter pour tout le monde, et un inconnu
    bloquait le proprietaire. Les deux tunnels ecrivent l'adresse du vrai
    visiteur a la FIN de X-Forwarded-For (Cloudflare l'ajoute, Tailscale la
    pose seule) ; le debut de la liste, lui, vient du visiteur et ne prouve
    rien. On ne croit cet en-tete que d'un pair local — le tunnel : sur le
    reseau, n'importe qui pourrait l'ecrire.
    """
    if not forwarded:
        return peer
    try:
        if not ipaddress.ip_address(peer).is_loopback:
            return peer
        return str(ipaddress.ip_address(forwarded.split(",")[-1].strip()))
    except ValueError:
        return peer


def bucket(address: str) -> str:
    """Ce qui compte les essais : l'adresse, ou son /64 en IPv6.

    Une seule box IPv6 dispose de milliards d'adresses : les compter une par
    une laisserait changer d'adresse a chaque essai.
    """
    try:
        found = ipaddress.ip_address(address)
    except ValueError:
        return address
    if found.version == 6:
        if found.ipv4_mapped is not None:
            return str(found.ipv4_mapped)
        return str(ipaddress.ip_network(f"{found}/64", strict=False))
    return str(found)


def byte_range(asked: str, total: int):
    """Lit un en-tete Range. Rend None (tout envoyer), () (demande hors du
    fichier : 416) ou (premier, dernier) octets, bornes comprises.

    Un en-tete mal forme est ignore, comme le veut la norme : on envoie tout.
    Un debut au-dela de la fin n'est pas « la fin du fichier » — c'est une
    demande impossible, et Safari attend qu'on le dise.
    """
    if total <= 0 or not asked.startswith("bytes="):
        return None
    start, dash, end = asked[len("bytes="):].split(",")[0].strip().partition("-")
    start, end = start.strip(), end.strip()

    def number(text: str) -> bool:
        return text.isascii() and text.isdigit()

    if not dash or (start and not number(start)) or (end and not number(end)):
        return None
    if start:
        first = int(start)
        last = int(end) if end else first + OPEN_RANGE - 1
        if last < first:
            return None
    elif end:
        suffix = int(end)                      # les derniers octets
        if suffix == 0:
            return ()
        first, last = max(0, total - suffix), total - 1
    else:
        return None
    if first >= total:
        return ()
    return first, min(last, total - 1)


class Guard:
    """Sessions ouvertes, et essais manqués par visiteur."""

    def __init__(self):
        self.sessions: dict = {}
        # visiteur -> (echecs, bloque jusqu'a, dernier echec)
        self.tries: dict = {}
        # visiteur -> essais en cours de verification
        self.pending: dict = {}
        self.lock = threading.Lock()
        # Le hachage, un a la fois : une rafale ne prend jamais qu'un coeur,
        # et la fenetre de Prisme garde les autres.
        self.checking = threading.Lock()

    def open(self, label: str = "") -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[token] = (time.time() + SESSION_HOURS * 3600, label)
        return token

    def label(self, token: str) -> str:
        with self.lock:
            found = self.sessions.get(token)
        return found[1] if found else "" 

    def valid(self, token: str) -> bool:
        if not token:
            return False
        with self.lock:
            found = self.sessions.get(token)
            if found is None:
                return False
            if found[0] < time.time():
                self.sessions.pop(token, None)
                return False
        return True

    def close(self, token: str) -> None:
        with self.lock:
            self.sessions.pop(token, None)

    def _state(self, who: str, now: float) -> tuple:
        count, until, last = self.tries.get(who, (0, 0.0, 0.0))
        if count and until <= now and now - last > FORGET:
            self.tries.pop(who, None)
            return 0, 0.0, 0.0
        return count, until, last

    def admit(self, who: str) -> float:
        """Reserve un essai. Zero s'il est accepte, sinon les secondes a attendre.

        La reservation se fait sous le verrou, AVANT le hachage : verifier
        d'abord et compter apres laissait quarante essais simultanes passer
        la barre des huit.
        """
        now = time.time()
        with self.lock:
            count, until, _last = self._state(who, now)
            if until > now:
                return until - now
            busy = self.pending.get(who, 0)
            if count >= MAX_TRIES:
                # Apres un blocage : un seul essai, et le suivant attend son
                # verdict.
                if busy:
                    return 1.0
            elif count + busy >= MAX_TRIES:
                return 1.0
            self.pending[who] = busy + 1
            return 0.0

    def settle(self, who: str, ok) -> None:
        """Rend la reservation. `ok` : vrai, faux, ou None si rien n'a ete verifie."""
        now = time.time()
        with self.lock:
            busy = self.pending.get(who, 0) - 1
            if busy > 0:
                self.pending[who] = busy
            else:
                self.pending.pop(who, None)
            if ok is None:
                return
            if ok:
                self.tries.pop(who, None)
                return
            count, _until, _last = self._state(who, now)
            count += 1
            until = 0.0
            if count >= MAX_TRIES:
                until = now + min(LOCKOUT * 2 ** min(count - MAX_TRIES, 16),
                                  LOCKOUT_MAX)
            self.tries[who] = (count, until, now)
            if len(self.tries) > 10_000:
                # Des milliers d'adresses : ne garder que ce qui bloque encore.
                for key in [key for key, (_c, end, last) in self.tries.items()
                            if end <= now and now - last > LOCKOUT]:
                    self.tries.pop(key, None)


class _Listener(ThreadingHTTPServer):
    """Le serveur HTTP, seul sur son port.

    Sous Windows, SO_REUSEADDR (pose par defaut) laisse un second programme
    ecouter sur le meme port : un ancien Prisme reste en vie, ou un intrus
    local, recevait alors une partie des visites — mots de passe compris.
    """

    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if os.name == "nt" and exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


class Server:
    """Le serveur, et ce qu'il faut pour le démarrer et l'arrêter proprement."""

    def __init__(self, root: Path, salt: str, digest: str, port: int = 8713,
                 host: str = "127.0.0.1", expand: bool = True, width: int = 480):
        self.library = Library(root, expand, width)
        self.salt, self.digest = salt, digest
        self.guard = Guard()
        self.host, self.port = host, port
        self.httpd = None
        self.thread = None

    def start(self) -> int:
        handler = _make_handler(self)
        try:
            self.httpd = _Listener((self.host, self.port), handler)
        except OSError:
            if not self.port:
                raise
            # Le port habituel est pris (un autre programme, un ancien Prisme) :
            # un port libre vaut mieux qu'un partage qui ne s'ouvre pas.
            self.httpd = _Listener((self.host, 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       name="prisme-web", daemon=True)
        self.thread.start()
        return self.port

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        self.thread = None

    @property
    def running(self) -> bool:
        return self.httpd is not None


def _make_handler(server: Server):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Prisme"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        # -- plomberie ---------------------------------------------------
        def log_message(self, *_args) -> None:
            """Silence : le journal par défaut écrit sur la sortie d'erreur,
            que l'application n'a pas quand elle tourne sans console."""

        def _who(self) -> str:
            peer = self.client_address[0] if self.client_address else "?"
            return visitor(peer, self.headers.get("X-Forwarded-For", ""))

        def _agent(self) -> str:
            return self.headers.get("User-Agent", "")

        def _label(self) -> str:
            """Le nom du visiteur, retenu avec sa session."""
            return server.guard.label(self._token()) or "inconnu"

        def _token(self) -> str:
            raw = self.headers.get("Cookie", "")
            for piece in raw.split(";"):
                name, _, value = piece.strip().partition("=")
                if name == "prisme":
                    return value
            return ""

        def _send(self, code, body=b"", kind="text/html; charset=utf-8",
                  extra=None) -> None:
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            # Une bibliotheque personnelle n'a rien a faire dans un cadre
            # etranger, ni dans un moteur de recherche.
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            if kind.startswith("text/html"):
                # Seuls les scripts et le style ecrits ici s'executent : un nom
                # de fichier piege ne pourrait rien lancer, meme insere tel quel.
                self.send_header("Content-Security-Policy", CSP)
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _gzip_ok(self) -> bool:
            for part in self.headers.get("Accept-Encoding", "").split(","):
                name, _, rest = part.strip().partition(";")
                if name.strip().lower() == "gzip":
                    return rest.replace(" ", "") not in ("q=0", "q=0.0", "q=0.00")
            return False

        def _blob(self, body: bytes, kind: str = JSON_KIND, tag: str = "",
                  packed: bytes | None = None, code=HTTPStatus.OK) -> None:
            """Envoie un texte : rien s'il est deja dans le navigateur (ETag),
            compresse sinon — le catalogue passe de sept mega-octets a deux."""
            extra = {"Vary": "Accept-Encoding"}
            if tag:
                extra["ETag"] = tag
                # Le navigateur garde la reponse, mais redemande a chaque fois
                # si elle vaut encore : un « 304 » ne pese rien.
                extra["Cache-Control"] = "private, no-cache"
                known = self.headers.get("If-None-Match", "")
                if known.strip() == "*" or tag in [
                        one.strip().removeprefix("W/") for one in known.split(",")]:
                    self.send_response(HTTPStatus.NOT_MODIFIED)
                    for name, value in extra.items():
                        self.send_header(name, value)
                    self.end_headers()
                    return
            if len(body) >= GZIP_MIN and self._gzip_ok():
                body = packed if packed is not None else gzip.compress(
                    body, compresslevel=5, mtime=0)
                extra["Content-Encoding"] = "gzip"
            self._send(code, body, kind, extra)

        def _json(self, payload, code=HTTPStatus.OK, tag: str = "") -> None:
            self._blob(json.dumps(payload, ensure_ascii=False,
                                  separators=(",", ":")).encode("utf-8"),
                       tag=tag, code=code)

        # -- routes ------------------------------------------------------
        def do_GET(self) -> None:          # noqa: N802
            parsed = urlparse(self.path)
            route = unquote(parsed.path)
            query = parse_qs(parsed.query)

            if route == "/login":
                return self._send(HTTPStatus.OK, LOGIN_PAGE)
            if not server.guard.valid(self._token()):
                if route.startswith("/api/") or route.startswith("/video/"):
                    return self._json({"error": "connexion requise"},
                                      HTTPStatus.UNAUTHORIZED)
                return self._send(HTTPStatus.SEE_OTHER, b"",
                                  extra={"Location": "/login"})

            if route == "/":
                return self._send(HTTPStatus.OK, APP_PAGE)
            if route == "/api/folders":
                return self._folders(query)
            if route == "/api/folder":
                return self._folder(query.get("id", [""])[0])
            if route == "/api/search":
                return self._json({"videos": server.library.search(
                    query.get("q", [""])[0])})
            if route.startswith("/thumb/"):
                return self._thumb(route[len("/thumb/"):])
            if route.startswith("/video/"):
                return self._video(route[len("/video/"):])
            if route == "/logout":
                server.guard.close(self._token())
                return self._send(HTTPStatus.SEE_OTHER, b"", extra={
                    "Location": "/login",
                    "Set-Cookie": "prisme=; Path=/; Max-Age=0; HttpOnly",
                })
            return self._send(HTTPStatus.NOT_FOUND, "Rien ici.")

        def do_HEAD(self) -> None:         # noqa: N802
            self.do_GET()

        def do_POST(self) -> None:         # noqa: N802
            route = urlparse(self.path).path
            if route == "/api/watching":
                return self._watching()
            if route != "/login":
                return self._send(HTTPStatus.NOT_FOUND, "Rien ici.")
            who = self._who()
            seat = bucket(who)
            left = server.guard.admit(seat)
            if left:
                return self._json(
                    {"error": f"Trop d'essais. Réessayez dans {int(left) + 1} s."},
                    HTTPStatus.TOO_MANY_REQUESTS)
            verdict = None
            try:
                try:
                    length = min(int(self.headers.get("Content-Length") or 0), 4096)
                except ValueError:
                    length = 0
                raw = self.rfile.read(length).decode("utf-8", "replace")
                given = parse_qs(raw).get("password", [""])[0]
                # Un temps de reponse constant, et jamais instantane : c'est ce
                # qui decourage les essais en rafale.
                time.sleep(0.4)
                if not server.guard.checking.acquire(timeout=CHECK_WAIT):
                    return self._json(
                        {"error": "Trop de monde à la porte. Réessayez dans "
                                  "un instant."},
                        HTTPStatus.SERVICE_UNAVAILABLE)
                try:
                    verdict = password_ok(given, server.salt, server.digest)
                finally:
                    server.guard.checking.release()
            finally:
                server.guard.settle(seat, verdict)
            if not verdict:
                JOURNAL.entered(who, self._agent(), "refus")
                return self._json({"error": "Mot de passe refusé."},
                                  HTTPStatus.UNAUTHORIZED)
            label = JOURNAL.entered(who, self._agent())
            token = server.guard.open(label)
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return self._send(
                HTTPStatus.SEE_OTHER, b"", extra={
                    "Location": "/",
                    "Set-Cookie": (f"prisme={token}; Path=/; HttpOnly; "
                                   f"SameSite=Lax; Max-Age={SESSION_HOURS * 3600}"
                                   + secure),
                })

        def _watching(self) -> None:
            """Le navigateur dit ce qu'il regarde, et depuis combien de temps.

            Sans ce battement, on saurait seulement qu'une video a ete
            ouverte — pas si elle a ete vue trois secondes ou une heure.
            """
            if not server.guard.valid(self._token()):
                return self._json({"error": "connexion requise"},
                                  HTTPStatus.UNAUTHORIZED)
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 2048)
                told = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
            except (ValueError, OSError):
                return self._json({"ok": False}, HTTPStatus.BAD_REQUEST)
            mark = str(told.get("id", ""))
            path = server.library.videos.get(mark)
            if path is None:
                return self._json({"ok": False}, HTTPStatus.NOT_FOUND)
            JOURNAL.watched(self._who(), self._label(), mark, path.name,
                            told.get("seconds", 0))
            self._json({"ok": True})

        # -- contenus ----------------------------------------------------
        def _folders(self, query: dict) -> None:
            """Le catalogue des dossiers : par pages, ou d'un bloc sans `start`.

            La page distante demande la suite au fil du defilement ; le bloc
            entier reste pour qui ne pagine pas, fait une fois par catalogue.
            """
            shelf = server.library
            asked = query.get("start", [""])[0]
            if not asked:
                version, body, packed = shelf.whole()
                return self._blob(body, tag=f'"{version}"', packed=packed)
            try:
                start = max(0, int(asked))
                count = int(query.get("count", [str(PAGE)])[0] or PAGE)
            except ValueError:
                return self._json({"error": "page inconnue"},
                                  HTTPStatus.BAD_REQUEST)
            count = max(0, min(count, PAGE_MAX))
            version, total, page = shelf.folders_page(start, count)
            self._json({"folders": page, "total": total, "version": version},
                       tag=f'"{version}.{start}.{count}"')

        def _folder(self, mark: str) -> None:
            inside = server.library.by_folder.get(mark)
            if inside is None:
                return self._json({"error": "dossier inconnu"},
                                  HTTPStatus.NOT_FOUND)
            found = [server.library.video_entry(one) for one in inside]
            self._json({"videos": [entry for entry in found if entry]})

        def _thumb(self, mark: str) -> None:
            path = server.library.videos.get(mark)
            if path is None:
                return self._send(HTTPStatus.NOT_FOUND, b"", "image/jpeg")
            image = thumb_path(path, BLIND_START, server.library.width)
            if not image.exists():
                # Pas encore fabriquee : le navigateur affichera son cadre.
                return self._send(HTTPStatus.NOT_FOUND, b"", "image/jpeg")
            try:
                body = image.read_bytes()
            except OSError:
                return self._send(HTTPStatus.NOT_FOUND, b"", "image/jpeg")
            self._send(HTTPStatus.OK, body, "image/jpeg",
                       {"Cache-Control": "private, max-age=86400"})

        def _video(self, mark: str) -> None:
            """Sert la vidéo en morceaux, pour qu'on puisse y sauter.

            Sans les « Range », un téléphone télécharge le fichier entier
            avant la première image, et déplacer le curseur recommence tout.
            """
            path = server.library.videos.get(mark)
            if path is None:
                return self._send(HTTPStatus.NOT_FOUND, "Vidéo introuvable.")
            try:
                # Un seul appel au NAS par saut : le stat dit a la fois que le
                # fichier existe et combien il pese.
                total = path.stat().st_size
            except OSError:
                return self._send(HTTPStatus.NOT_FOUND, "Vidéo introuvable.")
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

            span = byte_range(self.headers.get("Range", ""), total)
            if span == ():
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{total}")
                self.send_header("Content-Length", "0")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return
            first, last = span if span else (0, total - 1)
            length = max(0, last - first + 1)
            self.send_response(HTTPStatus.PARTIAL_CONTENT if span
                               else HTTPStatus.OK)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            if span:
                self.send_header("Content-Range", f"bytes {first}-{last}/{total}")
            self.end_headers()
            if self.command == "HEAD" or not length:
                return
            try:
                with open(path, "rb") as source:
                    source.seek(first)
                    left = length
                    while left > 0:
                        block = source.read(min(CHUNK, left))
                        if not block:
                            break
                        self.wfile.write(block)
                        left -= len(block)
            except (OSError, ConnectionError):
                # Le navigateur a change d'avis, ou saute ailleurs : c'est
                # normal, et il ne faut pas en faire une erreur.
                pass

    return Handler


# ---------------------------------------------------------------------------
# Les pages, tenues ici : rien à installer à côté du programme
# ---------------------------------------------------------------------------
#
# Le style et les scripts sont des chaines a part, et non des f-strings : leur
# empreinte entre dans l'en-tete Content-Security-Policy, qui n'autorise
# qu'eux. Tout autre script — un nom de fichier piege, une extension — reste
# lettre morte.

STYLE = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: #0b0d10; color: #e9eef4; font-size: 15px;
  font-family: -apple-system, "Segoe UI", Roboto, system-ui, sans-serif; }
header { position: sticky; top: 0; z-index: 5; background: #0e1116;
  border-bottom: 1px solid #1c222b; padding: 10px 14px;
  display: flex; gap: 10px; align-items: center; }
h1 { font-size: 15px; margin: 0; font-weight: 600; letter-spacing: .3px; }
input[type=search], input[type=password] { flex: 1; min-width: 0;
  background: #151a21; border: 1px solid #262e39; border-radius: 8px;
  padding: 9px 12px; color: #e9eef4; font-size: 15px; }
button { background: #1a1f27; border: 1px solid #2b323d; border-radius: 8px;
  padding: 9px 13px; color: #cdd5df; font-size: 14px; cursor: pointer; }
button:hover { color: #fff; border-color: #5a6474; }
main { padding: 12px; }
.grid { display: grid; gap: 10px;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
.card { background: #11151b; border: 1px solid #1c222b; border-radius: 10px;
  overflow: hidden; cursor: pointer; }
.card:hover { border-color: #39414d; }
.shot { width: 100%; aspect-ratio: 16/9; object-fit: cover; background: #05070a;
  display: block; }
.label { padding: 7px 9px; font-size: 13px; line-height: 1.35;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dim { color: #8b94a1; font-size: 12px; }
.crumb { color: #8b94a1; font-size: 13px; padding: 0 2px 10px; }
#more { height: 1px; }
#player { position: fixed; inset: 0; background: #000; display: none;
  flex-direction: column; z-index: 10; }
#player video { flex: 1; min-height: 0; width: 100%; background: #000;
  cursor: pointer; }
#rail { height: 4px; background: #2a2f38; cursor: pointer; }
#done { height: 4px; width: 0; background: #e9eef4; }
#left { color: #cdd5df; font-size: 13px; font-variant-numeric: tabular-nums; }
#bar { display: flex; gap: 10px; align-items: center; padding: 10px 12px;
  background: #0e1116; border-top: 1px solid #1c222b; }
#bar .label { flex: 1; padding: 0; }
.empty { color: #6f7885; padding: 40px 12px; text-align: center; }
form.login { max-width: 340px; margin: 18vh auto; padding: 0 16px;
  display: flex; flex-direction: column; gap: 12px; }
.err { color: #e26d76; font-size: 13px; min-height: 18px; }
"""

LOGIN_SCRIPT = """
document.querySelector('form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const err = document.getElementById('err');
  err.textContent = 'Vérification…';
  const body = new URLSearchParams(new FormData(e.target));
  let r;
  try {
    r = await fetch('/login', {method: 'POST', body, redirect: 'follow'});
  } catch (_) {
    err.textContent = 'Serveur injoignable.';
    return;
  }
  if (r.redirected || r.ok) { location.href = '/'; return; }
  let message = 'Refusé.';
  try { message = (await r.json()).error || message; } catch (_) {}
  err.textContent = message;
});
"""

APP_SCRIPT = """
'use strict';
const $ = (id) => document.getElementById(id);
const crumb = $('crumb'), shelf = $('folders'), grid = $('grid');
const empty = $('empty'), more = $('more'), player = $('player');
const video = $('video'), playing = $('playing');
const PAGE = 300;

// -- une carte : du texte pose comme texte, jamais interprete en HTML ------
function card(c) {
  const el = document.createElement('div');
  el.className = 'card';
  // Une vignette absente ou pas encore fabriquee laisse le cadre sombre, sans
  // l'icone d'image cassee.
  const blank = () => {
    const frame = document.createElement('div');
    frame.className = 'shot';
    return frame;
  };
  if (c.shot) {
    const img = document.createElement('img');
    img.className = 'shot';
    img.loading = 'lazy';
    img.alt = '';
    img.onerror = () => img.replaceWith(blank());
    img.src = c.shot;
    el.appendChild(img);
  } else {
    el.appendChild(blank());
  }
  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = c.title;
  el.appendChild(label);
  if (c.note) {
    const note = document.createElement('div');
    note.className = 'label dim';
    note.textContent = c.note;
    el.appendChild(note);
  }
  el.onclick = c.go;
  return el;
}

function folderCard(f) {
  return {shot: f.cover ? '/thumb/' + f.cover : '', title: f.name,
          note: f.count + ' vidéo(s) · ' + f.size,
          go: () => openFolder(f.id, f.name, true)};
}

function videoCard(v) {
  return {shot: '/thumb/' + v.id, title: v.name,
          note: [v.duration, v.height ? v.height + 'p' : ''].filter(Boolean).join(' · '),
          go: () => play(v)};
}

async function getJSON(url, signal) {
  try {
    const r = await fetch(url, {signal});
    if (r.status === 401) { location.href = '/login'; return null; }
    if (!r.ok) return null;
    return await r.json();
  } catch (_) {
    return null;
  }
}

// -- deux grilles : les dossiers, gardes tels quels, et le reste ----------
// Revenir aux dossiers ne recharge rien : la grille est encore la, a la meme
// hauteur de defilement. Chaque grille ne pose que ce qu'on voit venir.
const views = {
  folders: {el: shelf, pending: [], more: null, where: ''},
  videos: {el: grid, pending: [], more: null, where: ''},
};
let view = views.folders;
const folders = {total: -1, loaded: 0, version: '', scroll: 0, round: 0};
let nav = 0;                     // une reponse d'une navigation passee est ignoree
let shown = {v: 'folders'};

function showEmpty() {
  const none = !view.el.childElementCount && !view.pending.length && !view.more;
  empty.hidden = !none;
  if (none) empty.textContent = 'Rien à montrer ici.';
}

function reveal(target) {
  if (view === views.folders && target !== view) folders.scroll = scrollY;
  view = target;
  shelf.hidden = target !== views.folders;
  grid.hidden = target !== views.videos;
  crumb.textContent = target.where;
  showEmpty();
}

function fill(target) {
  const frag = document.createDocumentFragment();
  for (const c of target.pending.splice(0, PAGE)) frag.appendChild(card(c));
  target.el.appendChild(frag);
}

function nearEnd() {
  return more.getBoundingClientRect().top < innerHeight + 1500;
}

let topping = false, again = false;
async function topUp() {
  if (topping) { again = true; return; }
  topping = true;
  try {
    do {
      again = false;
      const target = view;
      while (view === target && nearEnd()) {
        if (target.pending.length) { fill(target); continue; }
        if (!target.more || !(await target.more())) break;
      }
      if (view === target) showEmpty();
    } while (again);
  } finally {
    topping = false;
  }
}
if ('IntersectionObserver' in window) {
  new IntersectionObserver(() => topUp(), {rootMargin: '0px 0px 1500px 0px'}).observe(more);
} else {
  addEventListener('scroll', () => topUp(), {passive: true});
}

// -- les dossiers : par pages, demandees au fil du defilement --------------
async function nextFolders() {
  const round = folders.round, start = folders.loaded;
  const d = await getJSON('/api/folders?start=' + start + '&count=' + PAGE);
  if (!d || round !== folders.round) return false;
  if (folders.version && d.version !== folders.version) {
    // Le catalogue a change entre deux pages : on repart du debut.
    resetFolders();
    return true;
  }
  folders.version = d.version;
  folders.total = d.total;
  const list = d.folders || [];
  folders.loaded = start + list.length;
  for (const f of list) views.folders.pending.push(folderCard(f));
  if (!list.length || folders.loaded >= folders.total) views.folders.more = null;
  views.folders.where = folders.total + ' dossier(s)';
  if (view === views.folders) crumb.textContent = views.folders.where;
  return true;
}

function resetFolders() {
  folders.round += 1;
  Object.assign(folders, {total: -1, loaded: 0, version: '', scroll: 0});
  shelf.replaceChildren();
  views.folders.pending = [];
  views.folders.more = nextFolders;
  views.folders.where = '';
}

function remember(state, soft) {
  // Une recherche qui s'affine remplace l'entree precedente : sinon
  // « Retour » defait les lettres une a une.
  if (soft && history.state && history.state.v === state.v) {
    history.replaceState(state, '');
  } else {
    history.pushState(state, '');
  }
}

async function showFolders(push) {
  const ticket = ++nav;
  if (asking) { asking.abort(); asking = null; }
  if (push) remember({v: 'folders'});
  shown = {v: 'folders'};
  reveal(views.folders);
  scrollTo(0, folders.scroll);
  if (folders.total < 0) { topUp(); return; }
  // Deja construite : on demande seulement si le catalogue a change.
  const d = await getJSON('/api/folders?start=0&count=0');
  if (!d || ticket !== nav) return;
  if (d.version !== folders.version) {
    resetFolders();
    reveal(views.folders);
    topUp();
  }
}

function showVideos(list, where) {
  grid.replaceChildren();
  views.videos.pending = list.map(videoCard);
  views.videos.more = null;
  views.videos.where = where;
  reveal(views.videos);
  scrollTo(0, 0);
  topUp();
}

async function openFolder(id, name, push) {
  const ticket = ++nav;
  if (asking) { asking.abort(); asking = null; }
  if (push) remember({v: 'folder', id, name});
  shown = {v: 'folder', id};
  const d = await getJSON('/api/folder?id=' + encodeURIComponent(id));
  if (ticket !== nav) return;
  const list = (d && d.videos) || [];
  showVideos(list, name + ' — ' + list.length + ' vidéo(s)');
}

// -- la recherche : la derniere frappe gagne ---------------------------------
let typing = null, asking = null;
async function search(text, push) {
  if (!text) return showFolders(push);
  const ticket = ++nav;
  if (asking) asking.abort();
  const mine = asking = new AbortController();
  const d = await getJSON('/api/search?q=' + encodeURIComponent(text), mine.signal);
  if (ticket !== nav) return;
  asking = null;
  if (push) remember({v: 'search', q: text}, true);
  shown = {v: 'search', q: text};
  const list = (d && d.videos) || [];
  showVideos(list, '« ' + text + ' » — ' + list.length);
}

$('q').addEventListener('input', (e) => {
  clearTimeout(typing);
  const text = e.target.value.trim();
  typing = setTimeout(() => search(text, true), 250);
});

// -- le lecteur --------------------------------------------------------------
let watching = null, lastBeat = 0;

function beat(force) {
  if (!watching) return;
  const now = Date.now() / 1000;
  const delta = lastBeat ? now - lastBeat : 0;
  lastBeat = now;
  if (delta <= 0 && !force) return;
  fetch('/api/watching', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: watching, seconds: Math.min(delta, 60)}),
    keepalive: true,
  }).catch(() => {});
}

const rail = $('rail'), done = $('done'), left = $('left');
function clock(s) {
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), r = s % 60;
  return m + ':' + (r < 10 ? '0' : '') + r;
}
video.addEventListener('click', () => {
  if (video.paused) video.play().catch(() => {}); else video.pause();
});
video.addEventListener('timeupdate', () => {
  const d = video.duration || 0;
  done.style.width = d ? (100 * video.currentTime / d) + '%' : '0';
  left.textContent = d ? '−' + clock(d - video.currentTime) : '';
});
rail.addEventListener('click', (e) => {
  const d = video.duration || 0;
  if (d) video.currentTime = d * e.offsetX / rail.clientWidth;
});

function play(v) {
  playing.textContent = v.name;
  video.src = '/video/' + v.id;
  player.style.display = 'flex';
  watching = v.id; lastBeat = 0;
  // Le bouton « Retour » du telephone ferme le lecteur, au lieu de quitter.
  remember({v: 'player'});
  video.play().catch(() => {});
}

function closePlayer() {
  if (player.style.display !== 'flex') return;
  beat(false); watching = null;
  video.pause(); video.removeAttribute('src'); video.load();
  player.style.display = 'none';
}

// On ne compte que ce qui defile vraiment : en pause, le temps ne passe pas.
video.addEventListener('playing', () => { lastBeat = Date.now() / 1000; });
video.addEventListener('pause', () => beat(false));
video.addEventListener('ended', () => beat(false));
setInterval(() => { if (!video.paused && watching) beat(false); }, 15000);
addEventListener('pagehide', () => beat(true));

$('close').onclick = () => {
  if (history.state && history.state.v === 'player') history.back();
  else closePlayer();
};

// -- « Retour » : on revient a la vue d'avant, sans quitter le site ---------
function same(a, b) {
  return a.v === b.v && a.id === b.id && a.q === b.q;
}
addEventListener('popstate', (e) => {
  const s = e.state || {v: 'folders'};
  closePlayer();
  if (s.v === 'player' || same(s, shown)) return;
  if (s.v === 'folder') openFolder(s.id, s.name, false);
  else if (s.v === 'search') { $('q').value = s.q; search(s.q, false); }
  else showFolders(false);
});

$('home').onclick = () => showFolders(shown.v !== 'folders');
$('out').onclick = () => { location.href = '/logout'; };

history.replaceState({v: 'folders'}, '');
resetFolders();
showFolders(false);
"""


def _digest(text: str) -> str:
    return "'sha256-" + base64.b64encode(
        hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


# Rien d'autre que ces trois textes ne s'execute ni ne s'applique ; images,
# videos et requetes ne vont qu'au serveur lui-meme.
CSP = ("default-src 'none'; "
       f"script-src {_digest(APP_SCRIPT)} {_digest(LOGIN_SCRIPT)}; "
       f"style-src {_digest(STYLE)}; "
       "img-src 'self'; media-src 'self'; connect-src 'self'; "
       "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")

LOGIN_PAGE = f"""<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prisme</title><style>{STYLE}</style>
<form class="login" method="post" action="/login">
  <h1>Prisme</h1>
  <div class="dim">Cette bibliothèque est privée.</div>
  <input type="password" name="password" placeholder="Mot de passe"
         autocomplete="current-password" autofocus required>
  <button type="submit">Entrer</button>
  <div class="err" id="err"></div>
</form>
<script>{LOGIN_SCRIPT}</script></html>"""

APP_PAGE = f"""<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prisme</title><style>{STYLE}</style>
<header>
  <h1>Prisme</h1>
  <input type="search" id="q" placeholder="Chercher… (plage -hiver)">
  <button id="home">Dossiers</button>
  <button id="out">Sortir</button>
</header>
<main>
  <div class="crumb" id="crumb"></div>
  <div class="grid" id="folders"></div>
  <div class="grid" id="grid" hidden></div>
  <div class="empty" id="empty" hidden></div>
  <div id="more"></div>
</main>
<div id="player">
  <video id="video" playsinline preload="metadata"></video>
  <div id="rail"><div id="done"></div></div>
  <div id="bar"><div class="label" id="playing"></div>
    <span id="left"></span>
    <button id="close">Fermer</button></div>
</div>
<script>{APP_SCRIPT}</script></html>"""
