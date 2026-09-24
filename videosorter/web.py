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
  puis bloqués. Sans cela, une adresse publique se fait essayer toute la nuit.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import secrets
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
from .query import matches_text
from .scan import (
    MODE_FOLDERS, cached_items, human_duration, human_size, under_veiled,
)

# Combien de temps une session reste ouverte sans qu'on ait à se réauthentifier.
SESSION_HOURS = 24 * 14
# Au-delà, on ne répond plus à cette adresse pendant `LOCKOUT` secondes.
MAX_TRIES = 8
LOCKOUT = 300
# Morceaux envoyés au navigateur : assez gros pour que le partage suive, assez
# petits pour qu'un saut dans la vidéo réponde tout de suite.
CHUNK = 512 * 1024


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
        self.folders, self.videos, self.by_folder = folders, videos, by_folder
        self.built_at = time.time()

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
        found = []
        for mark, path in self.videos.items():
            if matches_text(path.name, text):
                entry = self.video_entry(mark)
                if entry:
                    found.append(entry)
                if len(found) >= limit:
                    break
        return found


# ---------------------------------------------------------------------------
# Le serveur
# ---------------------------------------------------------------------------

class Guard:
    """Sessions ouvertes, et essais manqués par adresse."""

    def __init__(self):
        self.sessions: dict = {}
        self.tries: dict = {}
        self.lock = threading.Lock()

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

    def barred(self, who: str) -> float:
        """Secondes restantes avant de pouvoir réessayer, zéro si c'est libre."""
        with self.lock:
            count, until = self.tries.get(who, (0, 0.0))
        return max(0.0, until - time.time()) if count >= MAX_TRIES else 0.0

    def failed(self, who: str) -> None:
        with self.lock:
            count, _until = self.tries.get(who, (0, 0.0))
            self.tries[who] = (count + 1, time.time() + LOCKOUT)

    def cleared(self, who: str) -> None:
        with self.lock:
            self.tries.pop(who, None)


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
        self.httpd = ThreadingHTTPServer((self.host, self.port), handler)
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
            return self.client_address[0] if self.client_address else "?"

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
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload, code=HTTPStatus.OK) -> None:
            self._send(code, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")

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
                return self._json({"folders": server.library.folders})
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
            left = server.guard.barred(who)
            if left:
                return self._json(
                    {"error": f"Trop d'essais. Réessayez dans {int(left)} s."},
                    HTTPStatus.TOO_MANY_REQUESTS)
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 4096)
            except ValueError:
                length = 0
            raw = self.rfile.read(length).decode("utf-8", "replace")
            given = parse_qs(raw).get("password", [""])[0]
            # Un temps de reponse constant, et jamais instantane : c'est ce
            # qui decourage les essais en rafale.
            time.sleep(0.4)
            if not password_ok(given, server.salt, server.digest):
                server.guard.failed(who)
                JOURNAL.entered(who, self._agent(), "refus")
                return self._json({"error": "Mot de passe refusé."},
                                  HTTPStatus.UNAUTHORIZED)
            server.guard.cleared(who)
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
            if path is None or not path.exists():
                return self._send(HTTPStatus.NOT_FOUND, "Vidéo introuvable.")
            try:
                total = path.stat().st_size
            except OSError:
                return self._send(HTTPStatus.NOT_FOUND, "Vidéo illisible.")
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

            first, last = 0, total - 1
            asked = self.headers.get("Range", "")
            partial = asked.startswith("bytes=")
            if partial:
                piece = asked[len("bytes="):].split(",")[0]
                start, _, end = piece.partition("-")
                try:
                    if start:
                        first = int(start)
                        last = int(end) if end else min(first + CHUNK * 8 - 1, last)
                    elif end:                      # les derniers octets
                        first = max(0, total - int(end))
                except ValueError:
                    partial = False
                first = max(0, min(first, total - 1))
                last = max(first, min(last, total - 1))

            length = last - first + 1
            self.send_response(HTTPStatus.PARTIAL_CONTENT if partial
                               else HTTPStatus.OK)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("X-Content-Type-Options", "nosniff")
            if partial:
                self.send_header("Content-Range", f"bytes {first}-{last}/{total}")
            self.end_headers()
            if self.command == "HEAD":
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

STYLE = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
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
<script>
document.querySelector('form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const err = document.getElementById('err');
  err.textContent = 'Vérification…';
  const body = new URLSearchParams(new FormData(e.target));
  const r = await fetch('/login', {{method: 'POST', body, redirect: 'follow'}});
  if (r.redirected || r.ok) {{ location.href = '/'; return; }}
  let message = 'Refusé.';
  try {{ message = (await r.json()).error || message; }} catch (_) {{}}
  err.textContent = message;
}});
</script></html>"""

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
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" style="display:none"></div>
</main>
<div id="player">
  <video id="video" playsinline preload="metadata"></video>
  <div id="rail"><div id="done"></div></div>
  <div id="bar"><div class="label" id="playing"></div>
    <span id="left"></span>
    <button id="close">Fermer</button></div>
</div>
<script>
const grid = document.getElementById('grid'), crumb = document.getElementById('crumb');
const empty = document.getElementById('empty'), player = document.getElementById('player');
const video = document.getElementById('video'), playing = document.getElementById('playing');

function show(cards, where) {{
  crumb.textContent = where || '';
  grid.innerHTML = '';
  empty.style.display = cards.length ? 'none' : 'block';
  if (!cards.length) empty.textContent = 'Rien à montrer ici.';
  for (const c of cards) {{
    const el = document.createElement('div');
    el.className = 'card';
    el.innerHTML = `<img class="shot" loading="lazy" src="${{c.shot}}" alt="">`
      + `<div class="label">${{c.title}}</div>`
      + (c.note ? `<div class="label dim">${{c.note}}</div>` : '');
    el.onclick = c.go;
    grid.appendChild(el);
  }}
}}

async function folders() {{
  const r = await fetch('/api/folders');
  const d = await r.json();
  show((d.folders || []).map(f => ({{
    shot: f.cover ? '/thumb/' + f.cover : '',
    title: f.name,
    note: f.count + ' vidéo(s) · ' + f.size,
    go: () => openFolder(f.id, f.name),
  }})), (d.folders || []).length + ' dossier(s)');
}}

function asVideos(list, where) {{
  show(list.map(v => ({{
    shot: '/thumb/' + v.id,
    title: v.name,
    note: [v.duration, v.height ? v.height + 'p' : ''].filter(Boolean).join(' · '),
    go: () => play(v),
  }})), where);
}}

async function openFolder(id, name) {{
  const r = await fetch('/api/folder?id=' + encodeURIComponent(id));
  const d = await r.json();
  asVideos(d.videos || [], name + ' — ' + (d.videos || []).length + ' vidéo(s)');
}}

let watching = null, lastBeat = 0;

function beat(force) {{
  if (!watching) return;
  const now = Date.now() / 1000;
  const delta = lastBeat ? now - lastBeat : 0;
  lastBeat = now;
  if (delta <= 0 && !force) return;
  fetch('/api/watching', {{
    method: 'POST', headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{id: watching, seconds: Math.min(delta, 60)}}),
    keepalive: true,
  }}).catch(() => {{}});
}}

const rail = document.getElementById('rail'), done = document.getElementById('done');
const left = document.getElementById('left');
function clock(s) {{
  s = Math.max(0, Math.round(s));
  const m = Math.floor(s / 60), r = s % 60;
  return m + ':' + (r < 10 ? '0' : '') + r;
}}
video.addEventListener('click', () => {{
  if (video.paused) video.play().catch(() => {{}}); else video.pause();
}});
video.addEventListener('timeupdate', () => {{
  const d = video.duration || 0;
  done.style.width = d ? (100 * video.currentTime / d) + '%' : '0';
  left.textContent = d ? '−' + clock(d - video.currentTime) : '';
}});
rail.addEventListener('click', (e) => {{
  const d = video.duration || 0;
  if (d) video.currentTime = d * e.offsetX / rail.clientWidth;
}});

function play(v) {{
  playing.textContent = v.name;
  video.src = '/video/' + v.id;
  player.style.display = 'flex';
  watching = v.id; lastBeat = 0;
  video.play().catch(() => {{}});
}}

// On ne compte que ce qui defile vraiment : en pause, le temps ne passe pas.
video.addEventListener('playing', () => {{ lastBeat = Date.now() / 1000; }});
video.addEventListener('pause', () => beat(false));
video.addEventListener('ended', () => beat(false));
setInterval(() => {{ if (!video.paused && watching) beat(false); }}, 15000);
window.addEventListener('pagehide', () => beat(true));

document.getElementById('close').onclick = () => {{
  beat(false); watching = null;
  video.pause(); video.removeAttribute('src'); video.load();
  player.style.display = 'none';
}};
document.getElementById('home').onclick = folders;
document.getElementById('out').onclick = () => location.href = '/logout';

let typing = null;
document.getElementById('q').addEventListener('input', (e) => {{
  clearTimeout(typing);
  const text = e.target.value.trim();
  typing = setTimeout(async () => {{
    if (!text) return folders();
    const r = await fetch('/api/search?q=' + encodeURIComponent(text));
    const d = await r.json();
    asVideos(d.videos || [], '« ' + text + ' » — ' + (d.videos || []).length);
  }}, 250);
}});

folders();
</script></html>"""
