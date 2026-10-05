"""Prisme à distance : la bibliothèque dans un navigateur, protégée par un mot de passe.

L'application de bureau ne s'ouvre pas par un lien : c'est un programme Qt.
Ce module sert donc la même collection en HTML, et réutilise tout ce qui a
déjà coûté cher — l'index des dossiers, les vignettes déjà fabriquées. Un
téléphone au bout du monde voit alors la bibliothèque du NAS, et lit les
vidéos sans rien télécharger d'entier.

Trois précautions, parce qu'il s'agit d'ouvrir une collection personnelle :

* **Le serveur écoute ce PC et le réseau de la maison** (le Wi-Fi), jamais
  l'internet directement : c'est le tunnel — Cloudflare, Tailscale — qui le
  rend joignable de l'extérieur, sans jamais ouvrir un port sur la box. On y
  entre par un mot de passe, ou par le lien d'invitation (`INVITE_PATH`).
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
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import profils
from .access import JOURNAL
from .brand_data import LETTRAGE_PNG
from .query import parse, tester
from .textfold import fold

# Ce qui suit ne sert qu'a la bibliotheque du PC (`Library`) : l'index, le
# cache des vignettes, Qt. Le serveur qui tourne sur le NAS (`nas/serveur.py`)
# n'a que Python, et sa propre bibliotheque, tiree du catalogue que Prisme y
# depose : ces imports ne se font donc qu'a l'usage.

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
# L'invitation : un lien qui suffit pour entrer
# ---------------------------------------------------------------------------
# Taper un mot de passe de douze caracteres sur un telephone, pour chaque
# personne a qui l'on veut montrer la bibliotheque, c'etait trop. Le lien
# porte une cle tiree au hasard (144 bits : introuvable a l'essai) ; l'ouvrir
# ouvre une session, comme le mot de passe. Changer la cle ferme toutes les
# sessions : l'ancien lien ne mene plus nulle part.
INVITE_PATH = "/entrer/"


def new_invite() -> str:
    return secrets.token_urlsafe(18)


def invite_ok(given: str, expected: str) -> bool:
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def lan_address() -> str:
    """L'adresse de ce PC sur le Wi-Fi ou le réseau de la maison, ou "".

    Une « connexion » UDP n'envoie rien : elle demande seulement au système
    par quelle carte il sortirait, et donc sous quelle adresse on le voit.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.254.254.254", 1))
        found = probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()
    return "" if found.startswith("127.") or found == "0.0.0.0" else found


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
        # Les videos arrivees depuis la derniere analyse (les liens envoyes du
        # telephone, `liens.py`) : empreinte -> (chemin, infos). Montrees dans
        # leur dossier tant que le catalogue ne les connait pas.
        self.extras: dict = {}
        self.refresh()

    @staticmethod
    def mark(path) -> str:
        """L'empreinte courte qui désigne un fichier dans les adresses.

        Le chemin lui-même ne circule pas : il dirait où vivent les fichiers,
        et permettrait d'en demander d'autres.
        """
        return hashlib.sha1(str(path).encode("utf-8", "replace")).hexdigest()[:16]

    def refresh(self) -> None:
        from .scan import MODE_FOLDERS, cached_items, human_size, under_veiled
        folders = []
        videos = {}
        by_folder = {}
        for item in cached_items(self.root, MODE_FOLDERS, self.expand):
            # Strict : l'interrupteur « montrer les dossiers masques » vaut
            # pour l'ecran du PC, jamais pour le partage.
            if item.is_tag or not item.videos or under_veiled(item.path, strict=True):
                # Ce qui est masqué ici l'est aussi au dehors : l'adresse
                # publique ne doit pas montrer ce que la fenêtre cache.
                continue
            key = self.mark(item.path)
            inside = []
            for video in item.videos:
                if under_veiled(video, strict=True):
                    continue
                mark = self.mark(video)
                videos[mark] = Path(video)
                inside.append(mark)
            by_folder[key] = inside
            folders.append({
                "id": key,
                "name": ("Sans dossier" if item.loose_only
                         else item.path.name or str(item.path)),
                "count": item.video_count or len(inside),
                "size": human_size(item.size),
                "cover": inside[0] if inside else "",
                "shelf": self._shelf_of(item),
            })
        folders.sort(key=lambda entry: entry["name"].lower())
        now = time.time()
        with self._lock:
            self.folders, self.videos, self.by_folder = folders, videos, by_folder
            self._merge_extras(catalogue=True)
            self.built_at = now
            self.version = f"{time.time_ns():x}"
            # Le reste se refait a la premiere demande, sur le fil du serveur :
            # replier cent mille noms ici retarderait la fenetre.
            self._whole = None
            self._folded = None

    # Le dossier des videos arrivees du telephone, en tete de la liste.
    EXTRAS_ID = "telecharges"

    def add_download(self, path, mark: str = "", info: dict | None = None) -> str:
        """Une video arrivee du telephone : visible tout de suite, sans
        attendre l'analyse. Rend son empreinte."""
        mark = mark or self.mark(path)
        with self._lock:
            self.extras[mark] = (Path(path), dict(info or {}))
            self._merge_extras()
            self.version = f"{time.time_ns():x}"
            self._whole = None
            self._folded = None
        return mark

    def _merge_extras(self, catalogue: bool = False) -> None:
        """Sous le verrou : les videos arrivees, dans leur dossier. `catalogue` :
        `videos` vient d'etre relu -- ce qu'il connait n'est plus a part."""
        from .liens import FOLDER_NAME
        if not self.extras:
            return
        for mark, (path, _info) in list(self.extras.items()):
            if (catalogue and mark in self.videos) or not path.exists():
                del self.extras[mark]
                if not catalogue:
                    self.videos.pop(mark, None)
        self.folders = [f for f in self.folders if f.get("id") != self.EXTRAS_ID]
        self.by_folder.pop(self.EXTRAS_ID, None)
        fresh = list(self.extras)
        if not fresh:
            return
        for mark in fresh:
            self.videos[mark] = self.extras[mark][0]
        fresh.sort(key=lambda mark: -self._extra_time(mark))
        self.by_folder[self.EXTRAS_ID] = fresh
        self.folders.insert(0, {"id": self.EXTRAS_ID, "name": FOLDER_NAME,
                                "count": len(fresh), "size": "", "cover": fresh[0],
                                "shelf": ""})

    def _extra_time(self, mark: str) -> float:
        try:
            return self.extras[mark][0].stat().st_mtime
        except OSError:
            return 0.0

    def _shelf_of(self, item) -> str:
        """Le dossier « + » de la racine d'ou vient ce dossier, ou ""."""
        try:
            parts = Path(item.path).relative_to(self.root).parts
        except ValueError:
            return ""
        if not parts:
            return ""
        return parts[0] if parts[0].startswith("+") else ""

    def shelf_entries(self, shelf: str | None = None) -> list:
        """Ce que montre la page, a un niveau donne.

        Sans `shelf` : les rayons -- un dossier « + » et tous ses dossiers,
        comme une seule carte -- et les dossiers qui n'en ont pas. Avec : les
        dossiers de ce rayon. Sur le telephone, « + Ass » se cherchait en vain
        parmi six cents dossiers melanges ; le voici, avec les siens."""
        with self._lock:
            version, folders = self.version, self.folders
            kept = getattr(self, "_top", None)
        if shelf:
            return [f for f in folders if f.get("shelf") == shelf]
        if kept is not None and kept[0] == version:
            return kept[1]
        shelves: dict = {}
        top = []
        for f in folders:
            name = f.get("shelf") or ""
            if not name:
                top.append(f)
                continue
            entry = shelves.get(name)
            if entry is None:
                entry = shelves[name] = {"id": "rayon:" + name, "name": name,
                                         "count": 0, "folders": 0, "size": "",
                                         "cover": f.get("cover", ""), "rayon": True}
                top.append(entry)
            entry["count"] += f.get("count", 0)
            entry["folders"] += 1
        # Les videos arrivees du telephone d'abord : c'est la qu'on les attend.
        top.sort(key=lambda entry: (entry.get("id") != self.EXTRAS_ID,
                                    entry["name"].lower()))
        with self._lock:
            if self.version == version:
                self._top = (version, top)
        return top

    def folders_page(self, start: int, count: int, shelf: str | None = None) -> tuple:
        """(version, total, entrees de `start` a `start + count`)."""
        with self._lock:
            version = self.version
        found = self.shelf_entries(shelf)
        start = max(0, start)
        return version, len(found), found[start:start + max(0, count)]

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

    def thumb_file(self, path: Path, mark: str = "") -> Path:
        """Ou se trouve la vignette de cette video (`mark` : son empreinte)."""
        from .media import BLIND_START, thumb_path
        return thumb_path(path, BLIND_START, self.width)

    def video_entry(self, mark: str) -> dict | None:
        from .index import INDEX
        from .scan import human_duration
        path = self.videos.get(mark)
        if path is None:
            return None
        if mark in self.extras:
            info = self.extras[mark][1]
            if info.get("duration") or info.get("height"):
                return {"id": mark, "name": path.name, "folder": path.parent.name,
                        "duration": info.get("duration", ""),
                        "height": info.get("height", 0)}
        info = INDEX.probe(path) or {}
        return {
            "id": mark,
            "name": path.name,
            "folder": path.parent.name,
            "duration": human_duration(info["duration"]) if info.get("duration") else "",
            "height": info.get("height") or 0,
        }

    def videos_page(self, start: int, count: int) -> tuple:
        """(version, total, videos de `start` a `start + count`), rangees par
        dossier puis par nom : l'onglet « Vidéos » de la page distante."""
        with self._lock:
            version, folders, by_folder = self.version, self.folders, self.by_folder
            order = self._order if getattr(self, "_order_of", None) == version else None
        if order is None:
            order = [mark for folder in folders for mark in by_folder.get(folder["id"], [])]
            with self._lock:
                if self.version == version:
                    self._order, self._order_of = order, version
        start = max(0, start)
        page = [entry for entry in (self.video_entry(mark)
                                    for mark in order[start:start + max(0, count)])
                if entry]
        return version, len(order), page

    def search_folders(self, text: str, limit: int = 60) -> list:
        """Les dossiers dont le nom repond a la recherche."""
        if not text.strip():
            return []
        test = tester(parse(text))
        with self._lock:
            folders = self.folders
        return [f for f in folders if test(fold(f["name"]))][:limit]

    def random(self, count: int = 1) -> list:
        """Quelques videos au hasard, dans toute la bibliotheque."""
        import random
        with self._lock:
            marks = list(self.videos)
        found = []
        for mark in random.sample(marks, min(max(1, count), 50, len(marks))):
            entry = self.video_entry(mark)
            if entry:
                found.append(entry)
        return found

    def for_likes(self, likes: list, limit: int = 60) -> list:
        """« Pour vous » : des videos de chaque preference, a tour de role,
        melangees chaque jour autrement. Une seule passe sur les noms, gardee
        une heure par catalogue."""
        import random
        likes = [like for like in likes if like in profils.LIKE_TERMS][:20]
        if not likes:
            return []
        day = time.strftime("%Y%m%d%H")
        key = (self.version, tuple(likes), day, limit)
        kept = getattr(self, "_for_likes", {})
        if key in kept:
            marks = kept[key]
        else:
            tests = [tester(parse(profils.like_query(like))) for like in likes]
            buckets = [[] for _ in likes]
            for mark, folded in self._folded_names():
                for test, bucket in zip(tests, buckets):
                    if len(bucket) < 400 and test(folded):
                        bucket.append(mark)
            shuffle = random.Random(f"{day}{likes}")
            for bucket in buckets:
                shuffle.shuffle(bucket)
            marks, seen = [], set()
            while len(marks) < limit and any(buckets):
                for bucket in buckets:
                    while bucket:
                        mark = bucket.pop()
                        if mark not in seen:
                            seen.add(mark)
                            marks.append(mark)
                            break
            if len(kept) > 50:
                kept = {}
            kept[key] = marks
            self._for_likes = kept
        found = [self.video_entry(mark) for mark in marks[:limit]]
        return [entry for entry in found if entry]

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
                 host: str = "127.0.0.1", expand: bool = True, width: int = 480,
                 invite: str = "", library=None):
        # Celle du PC, tiree de l'index ; ou celle qu'on donne (le NAS).
        self.library = library if library is not None else Library(root, expand, width)
        self.salt, self.digest = salt, digest
        # La cle du lien d'invitation (`INVITE_PATH`), ou "" : pas de lien.
        self.invite = invite
        # L'adresse https ou la page s'installe comme une application (le NAS
        # derriere Tailscale), ou "" : celle ou l'on est.
        self.secure = ""
        # Qui est la en ce moment, et ce qu'il regarde : visiteur -> fiche.
        self.live: dict = {}
        self.live_lock = threading.Lock()
        self.guard = Guard()
        # Les icones de l'application (taille -> PNG), pour l'ecran d'accueil.
        self.icons: dict = {}
        # Ou ranger les demandes envoyees du telephone (`demandes.py`) :
        # le PC et le NAS le disent chacun ; None, aucune n'est recue.
        self.requests_path = None
        # La file des liens envoyes du telephone (`liens.LinkJobs`), ou None :
        # un lien n'est alors qu'une demande, a traiter a la main.
        self.downloads = None
        self.host, self.port = host, port
        self.httpd = None
        self.thread = None

    # Un visiteur sans signe de vie depuis ce temps n'est plus « connecte ».
    LIVE_SECONDS = 90

    def seen(self, label: str, ip: str = "") -> None:
        """Un visiteur vient de demander quelque chose."""
        now = time.time()
        with self.live_lock:
            entry = self.live.setdefault(label, {"label": label, "video": "",
                                                 "name": "", "at": 0.0,
                                                 "playing": False, "beat": 0.0})
            entry["seen"] = now
            entry["ip"] = ip

    def watching(self, label: str, mark: str, name: str, at: float,
                 playing: bool) -> None:
        """Ou en est ce visiteur, dans quelle video."""
        now = time.time()
        with self.live_lock:
            entry = self.live.setdefault(label, {"label": label, "ip": ""})
            entry.update(seen=now, beat=now, video=mark, name=name,
                         at=float(at or 0.0), playing=bool(playing))

    def viewers(self) -> list:
        """Les visiteurs du moment, avec leur position estimee a maintenant."""
        now = time.time()
        found = []
        with self.live_lock:
            for label, entry in list(self.live.items()):
                if now - entry.get("seen", 0) > self.LIVE_SECONDS:
                    del self.live[label]
                    continue
                shown = dict(entry)
                if shown.get("playing") and shown.get("beat"):
                    shown["at"] = shown.get("at", 0.0) + (now - shown["beat"])
                found.append(shown)
        found.sort(key=lambda entry: entry["label"])
        return found

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

    @property
    def profiles_path(self):
        """Les profils de la page mobile, a cote des demandes."""
        return profils.path_for(self.requests_path)


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
            """Le nom du visiteur : tire de l'identifiant que la page s'est
            donne, d'abord -- une personne, un nom, quel que soit son
            telephone. Sans lui (mot de passe, page d'avant), celui de sa
            session, ou de son appareil."""
            from .access import describe
            me = self._me()
            if me:
                return describe(self._who(), self._agent(), tag=me[:6])
            return (server.guard.label(self._token())
                    or describe(self._who(), self._agent()))

        def _key(self, query: dict | None = None) -> str:
            """La cle du lien, jointe a la demande par la page (en-tete) ou a
            l'adresse d'une image, d'une video (`?cle=`)."""
            given = self.headers.get("X-Prisme-Cle", "")
            if not given and query:
                given = query.get("cle", [""])[0]
            return given

        def _allowed(self, query: dict | None = None) -> bool:
            """Une session ouverte, ou la cle du lien : l'une suffit. Le cookie
            seul echouait dans les navigateurs integres aux lecteurs de QR
            code, qui ne le gardent pas -- et l'on tombait sur un mot de passe
            qui n'existait pas."""
            if server.guard.valid(self._token()):
                return True
            return invite_ok(self._key(query), server.invite)

        def _has_password(self) -> bool:
            return bool(server.salt and server.digest)

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
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        # Le plus gros corps qu'une requete legitime envoie : le mot de passe,
        # ou un battement « je regarde ».
        BODY_CAP = 8192

        def _read_body(self) -> bytes | None:
            """Lit le corps annonce en entier, ou ferme la connexion apres la reponse.

            Un corps laisse dans la connexion etait relu comme la requete
            suivante : derriere le tunnel, qui garde ses connexions ouvertes
            et les partage entre visiteurs, un inconnu pouvait glisser une
            requete cachee (« /logout ») dans celle du proprietaire. Rend None
            quand le corps est trop gros ou illisible : la connexion ne sert
            alors plus a rien d'autre.
            """
            if self.headers.get("Transfer-Encoding"):
                self.close_connection = True
                return None
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self.close_connection = True
                return None
            if length < 0 or length > self.BODY_CAP:
                self.close_connection = True
                return None
            try:
                body = self.rfile.read(length) if length else b""
            except OSError:
                self.close_connection = True
                return None
            if len(body) != length:
                self.close_connection = True
                return None
            return body

        def _no_body(self) -> None:
            """Une requete sans corps attendu (GET, HEAD) qui en porte un : on
            ne le lit pas, on ferme apres la reponse."""
            if (self.headers.get("Transfer-Encoding")
                    or (self.headers.get("Content-Length") or "0").strip() != "0"):
                self.close_connection = True

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
            self._no_body()
            parsed = urlparse(self.path)
            route = unquote(parsed.path)
            query = parse_qs(parsed.query)
            self._query = query

            if route == "/login":
                # Le mot de passe n'est plus qu'un secours : sans lui, il n'y a
                # que le lien, et la page le dit.
                if not self._has_password():
                    return self._send(HTTPStatus.SEE_OTHER, b"",
                                      extra={"Location": "/"})
                return self._send(HTTPStatus.OK, LOGIN_PAGE)
            if route.startswith(INVITE_PATH):
                return self._invited(route[len(INVITE_PATH):], query)
            if route == "/sw.js":
                # Le petit script de fond qu'exigent certains Chrome pour
                # installer la page comme une application. Il laisse tout
                # passer tel quel (rien n'est garde en memoire).
                return self._send(HTTPStatus.OK, SERVICE_WORKER,
                                  "application/javascript; charset=utf-8",
                                  extra={"Service-Worker-Allowed": "/",
                                         "Cache-Control": "no-cache"})
            if route == "/manifest.webmanifest":
                # L'icone de l'ecran d'accueil s'ouvre a « start_url ». Sur
                # iPhone, l'application posee la ne partage pas la memoire de
                # Safari : sans la cle dans son adresse, elle s'ouvrait sans
                # acces. La page la demande avec sa cle ; seule la bonne est
                # reprise (rien d'autre ne s'y ecrit).
                card = dict(MANIFEST)
                given = (query.get("cle") or [""])[0]
                if given and invite_ok(given, server.invite):
                    card["start_url"] = "/#cle=" + quote(given)
                    # L'appareil aussi : l'icone retrouve son profil.
                    me = profils.valid_me((query.get("moi") or [""])[0])
                    if me:
                        card["start_url"] += "&moi=" + me
                return self._send(HTTPStatus.OK, json.dumps(card),
                                  "application/manifest+json; charset=utf-8")
            if route in BRAND_ROUTES:
                return self._send(HTTPStatus.OK, BRAND_ROUTES[route], "image/png",
                                  {"Cache-Control": "public, max-age=604800"})
            if route in ICON_ROUTES:
                image = server.icons.get(ICON_ROUTES[route], b"")
                if not image:
                    return self._send(HTTPStatus.NOT_FOUND, b"", "image/png")
                return self._send(HTTPStatus.OK, image, "image/png",
                                  {"Cache-Control": "public, max-age=604800"})
            if route == "/":
                # La page seule ne contient rien : ce qu'elle montre passe par
                # /api, qui demande la cle. Elle sait dire « lien invalide ».
                return self._send(HTTPStatus.OK, APP_PAGE)
            if self._allowed(query):
                server.seen(self._label(), self._who())
            else:
                if route.startswith("/thumb/"):
                    return self._send(HTTPStatus.UNAUTHORIZED, b"", "image/jpeg")
                return self._json({"error": "lien requis",
                                   "password": self._has_password()},
                                  HTTPStatus.UNAUTHORIZED)

            if route == "/api/install":
                return self._json({"secure": server.secure})
            if route == "/api/liens":
                # Ou en sont les liens de la personne.
                jobs = server.downloads.mine(self._labels()) if server.downloads else []
                return self._json({"jobs": jobs, "auto": server.downloads is not None})
            if route == "/api/profil":
                return self._profile()
            if route == "/api/pourvous":
                # Les videos des preferences choisies a l'accueil.
                profile = profils.find(server.profiles_path, self._me(), self._label())
                likes = list((profile or {}).get("likes") or [])
                return self._json({"likes": likes,
                                   "videos": server.library.for_likes(likes)})
            if route == "/api/tag":
                # Une preference epinglee : toutes ses videos.
                like = query.get("like", [""])[0]
                if like not in profils.LIKE_TERMS:
                    return self._json({"videos": []})
                return self._json({"videos": server.library.search(
                    profils.like_query(like))})
            if route == "/api/folders":
                return self._folders(query)
            if route == "/api/folder":
                return self._folder(query.get("id", [""])[0])
            if route == "/api/search":
                text = query.get("q", [""])[0]
                return self._json({"videos": server.library.search(text),
                                   "folders": server.library.search_folders(text)})
            if route == "/api/videos":
                try:
                    start = max(0, int(query.get("start", ["0"])[0] or 0))
                    count = int(query.get("count", [str(PAGE)])[0] or PAGE)
                except ValueError:
                    start, count = 0, PAGE
                version, total, page = server.library.videos_page(
                    start, max(0, min(count, PAGE_MAX)))
                return self._json({"videos": page, "total": total,
                                   "version": version})
            if route == "/api/favorites":
                # Les favoris de la personne, quel que soit son navigateur :
                # l'icone d'un iPhone ne se presente pas comme Safari.
                marks, seen = [], set()
                for one in self._labels():
                    for mark in JOURNAL.favorites(one):
                        if mark not in seen:
                            seen.add(mark)
                            marks.append(mark)
                found = [server.library.video_entry(m) for m in marks]
                return self._json({"videos": [v for v in found if v]})
            if route == "/api/random":
                try:
                    count = int(query.get("n", ["1"])[0] or 1)
                except ValueError:
                    count = 1
                return self._json({"videos": server.library.random(count)})
            if route.startswith("/thumb/"):
                return self._thumb(route[len("/thumb/"):])
            if route.startswith("/video/"):
                return self._video(route[len("/video/"):])
            if route == "/logout":
                server.guard.close(self._token())
                return self._send(HTTPStatus.SEE_OTHER, b"", extra={
                    "Location": "/",
                    "Set-Cookie": "prisme=; Path=/; Max-Age=0; HttpOnly",
                })
            return self._send(HTTPStatus.NOT_FOUND, "Rien ici.")

        def do_HEAD(self) -> None:         # noqa: N802
            self.do_GET()

        def do_POST(self) -> None:         # noqa: N802
            # Le corps d'abord, quelle que soit l'issue : un refus qui ne le
            # lisait pas le laissait dans la connexion (voir _read_body).
            body = self._read_body()
            if body is None:
                return self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Trop long.")
            route = urlparse(self.path).path
            if route == "/api/watching":
                return self._watching(body)
            if route == "/api/favorite":
                return self._favorite(body)
            if route == "/api/demande":
                return self._demande(body)
            if route == "/api/profil":
                return self._save_profile(body)
            if route == "/api/pin":
                return self._pin(body)
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
                raw = body.decode("utf-8", "replace")
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
            return self._enter(label)

        def _enter(self, label: str, where: str = "/") -> None:
            """Ouvre une session et mene a la bibliotheque."""
            token = server.guard.open(label)
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            return self._send(
                HTTPStatus.SEE_OTHER, b"", extra={
                    "Location": where,
                    "Set-Cookie": (f"prisme={token}; Path=/; HttpOnly; "
                                   f"SameSite=Lax; Max-Age={SESSION_HOURS * 3600}"
                                   + secure),
                })

        def _invited(self, given: str, query: dict | None = None) -> None:
            """Le lien d'invitation : la bonne cle ouvre une session, sans mot
            de passe. Les essais sont comptes comme ceux du mot de passe.

            `?installer=1&moi=…` : la page s'est rouverte dans Chrome pour
            poser l'icone (voir `escapeUrl`) ; elle y reprend la question, et
            son profil."""
            who = self._who()
            seat = bucket(who)
            left = server.guard.admit(seat)
            if left:
                return self._send(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    f"Trop d'essais. Réessayez dans {int(left) + 1} s.",
                    "text/plain; charset=utf-8")
            verdict = invite_ok(given.strip("/"), server.invite)
            server.guard.settle(seat, verdict)
            if not verdict:
                JOURNAL.entered(who, self._agent(), "refus")
                # Un lien perime : on propose le mot de passe, sans rien dire
                # de plus de la cle.
                return self._send(HTTPStatus.SEE_OTHER, b"",
                                  extra={"Location": "/login"})
            label = JOURNAL.entered(who, self._agent(), "lien")
            where = "/#cle=" + quote(server.invite)
            me = profils.valid_me(((query or {}).get("moi") or [""])[0])
            if me:
                where += "&moi=" + me
            if (query or {}).get("installer"):
                where += "&installer=1"
            # Le lien partage par un ami : la page retient son code.
            code = profils.valid_code(((query or {}).get("parrain") or [""])[0])
            if code:
                where += "&parrain=" + code
            return self._enter(label, where)

        def _watching(self, body: bytes = b"") -> None:
            """Le navigateur dit ce qu'il regarde, et depuis combien de temps.

            Sans ce battement, on saurait seulement qu'une video a ete
            ouverte — pas si elle a ete vue trois secondes ou une heure.
            """
            if not self._allowed():
                return self._json({"error": "lien requis"},
                                  HTTPStatus.UNAUTHORIZED)
            try:
                told = json.loads(body.decode("utf-8", "replace"))
            except (ValueError, OSError):
                return self._json({"ok": False}, HTTPStatus.BAD_REQUEST)
            if not isinstance(told, dict):
                return self._json({"ok": False}, HTTPStatus.BAD_REQUEST)
            mark = str(told.get("id", ""))
            path = server.library.videos.get(mark)
            if path is None:
                return self._json({"ok": False}, HTTPStatus.NOT_FOUND)
            label = self._label()
            JOURNAL.watched(self._who(), label, mark, path.name,
                            told.get("seconds", 0))
            try:
                at = float(told.get("at", 0) or 0)
            except (TypeError, ValueError):
                at = 0.0
            server.watching(label, mark, path.name, at, bool(told.get("playing")))
            me = self._me()
            if me:
                book = server.profiles_path
                profils.attach(book, me, label)
                profils.reward(book, server.requests_path, me, label, self._seen)
            self._json({"ok": True})

        def _demande(self, body: bytes) -> None:
            """Une demande du telephone : un titre, un style de video."""
            if not self._allowed():
                return self._json({"error": "lien requis"}, HTTPStatus.UNAUTHORIZED)
            try:
                told = json.loads(body.decode("utf-8", "replace"))
            except (ValueError, OSError):
                return self._json({"ok": False, "error": "Demande illisible."},
                                  HTTPStatus.BAD_REQUEST)
            if not isinstance(told, dict):
                return self._json({"ok": False, "error": "Demande illisible."},
                                  HTTPStatus.BAD_REQUEST)
            from . import demandes
            label = self._label()
            # Le prenom, s'il a ete donne : Prisme dit qui demande.
            profile = profils.find(server.profiles_path, self._me(), label)
            who = f"{profile['name']} · {label}" if profile and profile.get("name") else label
            kind, text = str(told.get("kind", "")), str(told.get("text", ""))
            auto = False
            if kind == "lien" and server.downloads is not None:
                # Le serveur telecharge lui-meme ; la video ira dans les
                # favoris de la personne (`liens.py`).
                from .liens import first_url
                started = server.downloads.submit(first_url(text), label, who)
                if not started.get("ok"):
                    return self._json(started, HTTPStatus.BAD_REQUEST)
                auto = True
                if started.get("already"):
                    return self._json({"ok": True, "auto": True, "already": True})
                text = f"{text} (téléchargement automatique)"
            done = demandes.append(server.requests_path, who, kind, text)
            done["auto"] = auto
            self._json(done, HTTPStatus.OK if done.get("ok") or auto else HTTPStatus.BAD_REQUEST)

        def _me(self) -> str:
            """L'identifiant que la page s'est donne (`profils.py`) : dans
            l'en-tete, ou dans l'adresse d'une image, d'une video."""
            given = self.headers.get("X-Prisme-Moi", "")
            if not given:
                given = (getattr(self, "_query", {}).get("moi") or [""])[0]
            return profils.valid_me(given)

        def _seen(self, labels: list) -> int:
            return JOURNAL.seen_count(labels, profils.MIN_SECONDS)

        def _profile(self) -> None:
            """Qui est cet appareil, et ou il en est du bon d'achat."""
            label = self._label()
            if getattr(self, "_query", {}).get("ouverture"):
                # La page vient de s'ouvrir : une connexion, au nom de la
                # personne (le lien, lui, n'a que le nom du navigateur).
                JOURNAL.entered(self._who(), self._agent(), "page", label=label)
            # Un navigateur de plus pour la meme personne (l'icone de l'ecran
            # d'accueil, un Chrome mis a jour) : ses favoris la suivent.
            profils.attach(server.profiles_path, self._me(), label)
            profile = profils.find(server.profiles_path, self._me(), label)
            self._json({"profile": profils.public(profile),
                        "seen": self._seen(profils.labels_of(profile, label)),
                        "goal": profils.GOAL, "minimum": profils.MIN_SECONDS,
                        "prize": profils.PRIZE, "friendGoal": profils.FRIEND_GOAL,
                        "friendPrize": profils.FRIEND_PRIZE,
                        "sponsor": profils.sponsorship(server.profiles_path, self._me())
                        if profile else {},
                        "kept": server.profiles_path is not None})

        def _save_profile(self, body: bytes) -> None:
            """Les reponses du premier accueil : prenom, email, preferences."""
            if not self._allowed():
                return self._json({"error": "lien requis"}, HTTPStatus.UNAUTHORIZED)
            try:
                told = json.loads(body.decode("utf-8", "replace"))
            except (ValueError, OSError):
                told = None
            if not isinstance(told, dict):
                return self._json({"ok": False, "error": "Réponse illisible."},
                                  HTTPStatus.BAD_REQUEST)
            label = self._label()
            done = profils.save(server.profiles_path, server.requests_path,
                                self._me(), label, told)
            if done.get("ok"):
                done["seen"] = self._seen(profils.labels_of(
                    profils.find(server.profiles_path, self._me(), label), label))
                done["goal"] = profils.GOAL
                done["sponsor"] = profils.sponsorship(server.profiles_path, self._me())
            self._json(done, HTTPStatus.OK if done.get("ok") else HTTPStatus.BAD_REQUEST)

        def _favorite(self, body: bytes) -> None:
            """Un favori de plus, ou de moins, pour cet appareil."""
            if not self._allowed():
                return self._json({"error": "lien requis"}, HTTPStatus.UNAUTHORIZED)
            try:
                told = json.loads(body.decode("utf-8", "replace"))
            except (ValueError, OSError):
                return self._json({"ok": False}, HTTPStatus.BAD_REQUEST)
            mark = str(told.get("id", "")) if isinstance(told, dict) else ""
            path = server.library.videos.get(mark)
            if path is None:
                return self._json({"ok": False}, HTTPStatus.NOT_FOUND)
            on = bool(told.get("on"))
            if on:
                JOURNAL.set_favorite(self._label(), mark, path.name, True)
            else:
                # Retire partout ou la personne l'avait mise.
                for one in self._labels():
                    JOURNAL.set_favorite(one, mark, path.name, False)
            self._json({"ok": True, "on": on})

        def _labels(self) -> list:
            """Les noms d'appareil de la personne : ceux de son profil, et
            celui d'ou elle demande."""
            label = self._label()
            return profils.labels_of(
                profils.find(server.profiles_path, self._me(), label), label)

        def _pin(self, body: bytes) -> None:
            """Le code de verrouillage du visiteur (`profils.pin_action`)."""
            if not self._allowed():
                return self._json({"error": "lien requis"}, HTTPStatus.UNAUTHORIZED)
            try:
                told = json.loads(body.decode("utf-8", "replace"))
            except (ValueError, OSError):
                told = None
            if not isinstance(told, dict):
                return self._json({"ok": False, "error": "Demande illisible."},
                                  HTTPStatus.BAD_REQUEST)
            done = profils.pin_action(server.profiles_path, self._me(), self._label(), told)
            self._json(done)

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
            version, total, page = shelf.folders_page(
                start, count, query.get("rayon", [""])[0] or None)
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
            image = server.library.thumb_file(path, mark)
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
:root { color-scheme: dark; --bg: #0b0d10; --panel: #11151b; --line: #1f2630;
  --ink: #e9eef4; --dim: #8b94a1; --accent: #4f8bf0; }
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
[hidden] { display: none !important; }
html, body { margin: 0; background: var(--bg); color: var(--ink);
  font-family: -apple-system, "Segoe UI", Roboto, system-ui, sans-serif;
  font-size: 15px; }
header { position: sticky; top: 0; z-index: 5; background: rgba(14,17,22,.96);
  border-bottom: 1px solid var(--line); padding: 8px 12px 10px;
  display: flex; flex-direction: column; gap: 8px; }
.top { display: flex; align-items: center; gap: 8px; }
.brand { font-weight: 700; font-size: 17px; letter-spacing: .3px;
  background: none; border: 0; padding: 4px 2px; color: var(--ink); }
.wordmark { display: block; height: 15px; width: auto; }
.logo { display: block; width: 30px; height: 30px; border-radius: 8px; }
.qbox { position: relative; flex: 1; min-width: 0; }
.qbox #q { width: 100%; padding-right: 48px; }
#q::-webkit-search-cancel-button { -webkit-appearance: none; display: none; }
.qgo { position: absolute; right: 4px; top: 4px; bottom: 4px; width: 40px; padding: 0;
  display: flex; align-items: center; justify-content: center; border: 0;
  border-radius: 8px; background: #1d2a40; color: #dbe7ff; }
.qgo svg { width: 18px; height: 18px; }
/* Les preferences epinglees : une ligne qui defile, au-dessus de tout. */
.pins { display: flex; gap: 7px; overflow-x: auto; scrollbar-width: none;
  margin: 0 -12px 12px; padding: 0 12px; }
.pins[hidden] { display: none; }
.pins::-webkit-scrollbar { display: none; }
.pin { flex: none; display: inline-flex; align-items: center; gap: 6px;
  border-radius: 999px; padding: 6px 12px; font-size: 13px; background: #141a22;
  border: 1px solid #263040; color: #cfd7e1; }
.pin svg { width: 13px; height: 13px; color: #8fb4ff; }
.pin.on { background: #1d2a40; border-color: #4f8bf0; color: #fff; }
.pin.edit { color: #8b94a1; background: none; }
/* « Pour vous » : une rangee qu'on fait glisser, pas une grille de plus. */
#forYou[hidden] { display: none; }
.strip { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(150px, 42%);
  gap: 10px; overflow-x: auto; scroll-snap-type: x mandatory; scrollbar-width: none;
  margin: 0 -12px 16px; padding: 0 12px 4px; }
.strip::-webkit-scrollbar { display: none; }
.strip .card { scroll-snap-align: start; }
@media (min-width: 700px) { .strip { grid-auto-columns: 220px; } }
/* Peu de resultats : on invite a demander ce qui manque. */
.few { display: flex; gap: 12px; align-items: flex-start; background: #121a28;
  border: 1px solid #2a3d5e; border-radius: 14px; padding: 14px; margin: 4px 0 14px;
  color: #c9d4e3; font-size: 14px; line-height: 1.5; }
.few[hidden] { display: none; }
.few b { color: #fff; display: block; margin-bottom: 2px; }
.few a { color: #8fb4ff; font-weight: 600; text-decoration: underline; cursor: pointer; }
.few .perk-ic svg { fill: currentColor; width: 17px; height: 17px; }
.few .perk-ic.line svg { fill: none; }
/* Le verrou : plein ecran, un clavier a chiffres, rien d'autre. */
#lock { position: fixed; inset: 0; z-index: 60; display: flex; align-items: center;
  justify-content: center; background: radial-gradient(120% 80% at 50% 0%, #17233a 0%,
  #0b0d10 60%); }
#lock[hidden] { display: none; }
.lock-in { width: 100%; max-width: 340px; text-align: center;
  padding: 24px 20px calc(20px + env(safe-area-inset-bottom)); }
#lock.veil .lock-in > :not(.lockicon) { visibility: hidden; }
.lockicon { display: block; width: 64px; height: 64px; border-radius: 16px;
  margin: 0 auto 16px; }
#lock h2 { font-size: 20px; margin: 0 0 4px; }
#lock p { color: #aeb8c5; font-size: 14px; margin: 0 0 20px; }
.pindots { display: flex; gap: 18px; justify-content: center; margin: 0 0 10px; }
.pindots i { width: 14px; height: 14px; border-radius: 50%; border: 2px solid #4f8bf0;
  transition: background .1s; }
.pindots i.on { background: #4f8bf0; }
.pindots.shake { animation: shake .4s; }
@keyframes shake { 20%, 60% { transform: translateX(-9px); }
  40%, 80% { transform: translateX(9px); } }
#lockErr { text-align: center; margin: 0 0 8px; }
.pad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px 18px;
  max-width: 280px; margin: 0 auto 12px; }
.pad button { justify-self: center; width: 72px; height: 72px; border-radius: 50%;
  padding: 0; font-size: 27px; font-weight: 500; background: #161c25;
  border: 1px solid #263040; color: #fff; }
.pad button:active { background: #24324a; }
.pad .k0 { grid-column: 2; }
.pad .del { background: none; border: 0; font-size: 22px; color: #aeb8c5; }
.row2 { display: flex; gap: 8px; }
.row2 .wide { flex: 1; }
.wide.soft { background: #252c36; color: #e9eef4; }
.lockbig { display: flex; width: 58px; height: 58px; border-radius: 16px;
  margin: 8px auto 14px; }
.lockbig svg { width: 28px; height: 28px; }
.wordmark.big { height: 26px; margin: 0 auto; }
.acts { margin-left: auto; display: flex; gap: 6px; }
input[type=search], input[type=password] { width: 100%; min-width: 0;
  background: #151a21; border: 1px solid #262e39; border-radius: 10px;
  padding: 11px 13px; color: var(--ink); font-size: 16px; }
button { background: #1a1f27; border: 1px solid #2b323d; border-radius: 10px;
  padding: 9px 13px; color: #cdd5df; font-size: 14px; cursor: pointer; }
button:active { background: #232a35; }
main { padding: 12px; }
.crumb { color: var(--dim); font-size: 13px; padding: 2px 2px 10px; }
.section { color: var(--dim); font-size: 12px; text-transform: uppercase;
  letter-spacing: .6px; padding: 6px 2px 8px; }
.grid { display: grid; gap: 10px; margin-bottom: 14px;
  grid-template-columns: repeat(auto-fill, minmax(155px, 1fr)); }
@media (max-width: 420px) { .grid { grid-template-columns: 1fr 1fr; gap: 8px; } }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  overflow: hidden; cursor: pointer; }
.card:active { border-color: #3a4452; }
.frame { position: relative; }
.shot { width: 100%; aspect-ratio: 16/9; object-fit: cover; background: #05070a;
  display: block; }
.badge { position: absolute; right: 6px; bottom: 6px; background: rgba(0,0,0,.75);
  color: #fff; font-size: 12px; font-weight: 600; padding: 2px 6px;
  border-radius: 6px; font-variant-numeric: tabular-nums; }
.label { padding: 7px 9px 2px; font-size: 13px; line-height: 1.35;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden; word-break: break-word; }
.dim { color: var(--dim); font-size: 12px; }
.note { padding: 0 9px 8px; }
#more { height: 1px; }
.empty { color: #6f7885; padding: 40px 12px; text-align: center; line-height: 1.5; }
#player { position: fixed; inset: 0; background: #000; display: none; z-index: 10; }
#stage { position: absolute; inset: 0; background: #000; overflow: hidden; }
#stage video { width: 100%; height: 100%; object-fit: contain; background: #000;
  display: block; }
/* Une seule barre, fine, toujours la, posee sur le bas de l'image. */
#obot { position: absolute; left: 0; right: 0; bottom: 0; z-index: 2;
  display: flex; align-items: center; gap: 2px;
  padding: 0 4px env(safe-area-inset-bottom); background: rgba(0,0,0,.55);
  color: #fff; font-size: 11px; font-variant-numeric: tabular-nums; }
#obot span { flex: none; min-width: 30px; text-align: center; opacity: .9; }
.ic { background: none; border: 0; padding: 0; border-radius: 50%;
  width: 34px; height: 34px; display: inline-flex; align-items: center;
  justify-content: center; color: #fff; flex: none; }
.ic:active { background: rgba(255,255,255,.18); }
.ic svg { width: 20px; height: 20px; fill: currentColor; }
.ic:disabled { opacity: .3; }
#iStarOn { fill: #f5c542; }
/* Le plein ecran tourne a la main : un quart de tour, tout l'ecran. */
#stage.turned { position: fixed; inset: auto; top: 0; left: 0; z-index: 20;
  width: 100vh; height: 100vw; transform-origin: top left;
  transform: rotate(90deg) translateY(-100%); }
@supports (height: 100dvh) {
  #stage.turned { width: 100dvh; height: 100dvw; }
}
#seek { flex: 1; min-width: 0; height: 28px; margin: 0 2px; background: transparent;
  -webkit-appearance: none; appearance: none; touch-action: none; }
#seek::-webkit-slider-runnable-track { height: 3px; border-radius: 2px;
  background: rgba(255,255,255,.35); }
#seek::-webkit-slider-thumb { -webkit-appearance: none; width: 14px; height: 14px;
  margin-top: -5.5px; border-radius: 50%; background: #fff; border: 0; }
#seek::-moz-range-track { height: 3px; border-radius: 2px; background: rgba(255,255,255,.35); }
#seek::-moz-range-progress { height: 3px; border-radius: 2px; background: #fff; }
#seek::-moz-range-thumb { width: 14px; height: 14px; border-radius: 50%;
  background: #fff; border: 0; }
#flash { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  background: rgba(0,0,0,.6); color: #fff; font-size: 18px; font-weight: 700;
  padding: 10px 16px; border-radius: 30px; pointer-events: none; opacity: 0;
  transition: opacity .25s; z-index: 3; }
#flash.on { opacity: 1; }
#flash.left { left: 22%; }
#flash.right { left: 78%; }
body.watching { overflow: hidden; }
.tab.on { border-color: #4f8bf0; color: #fff; }
.acts button { padding: 8px 10px; font-size: 13px; }
form.login { max-width: 340px; margin: 18vh auto; padding: 0 16px;
  display: flex; flex-direction: column; gap: 12px; }
.err { color: #e26d76; font-size: 13px; min-height: 18px; }
h1 { font-size: 17px; margin: 0; }
#install { position: fixed; inset: 0; z-index: 40; background: rgba(0,0,0,.55);
  display: flex; align-items: flex-end; justify-content: center; }
#install[hidden] { display: none; }
#install .sheet { background: #161b22; color: #e9eef4; width: 100%; max-width: 460px;
  border-radius: 18px 18px 0 0; padding: 22px 20px calc(18px + env(safe-area-inset-bottom));
  box-shadow: 0 -8px 30px rgba(0,0,0,.5); text-align: center; }
#install .appicon { width: 64px; height: 64px; border-radius: 15px; }
#install h2 { font-size: 17px; margin: 12px 0 6px; }
#install p { font-size: 14px; line-height: 1.45; color: #b8c1cc; margin: 0 0 12px; }
#install p b { color: #fff; }
#install .never { display: flex; gap: 8px; align-items: center; justify-content: center;
  font-size: 13px; color: #8b94a1; margin-bottom: 14px; }
#install .row { display: flex; gap: 10px; }
#install .row button { flex: 1; padding: 12px; border-radius: 12px; font-size: 15px;
  font-weight: 600; border: 0; }
#install .go { background: #2f6fed; color: #fff; }
#install .quiet { background: #252c36; color: #e9eef4; }
.searchrow { display: flex; gap: 8px; align-items: stretch; }
.searchrow #q { flex: 1; min-width: 0; }
.searchrow .ask { display: inline-flex; align-items: center; gap: 6px; flex: none;
  padding: 0 12px; border-radius: 10px; background: #1d2a40; color: #dbe7ff;
  border: 1px solid #2f4f86; font-size: 14px; font-weight: 600; }
.searchrow .ask svg { width: 16px; height: 16px; fill: currentColor; }
@media (max-width: 360px) { .searchrow .ask span { display: none; } }
#ask { position: fixed; inset: 0; z-index: 41; background: rgba(0,0,0,.55);
  display: flex; align-items: flex-end; justify-content: center; }
#ask[hidden] { display: none; }
#ask .sheet { background: #161b22; color: #e9eef4; width: 100%; max-width: 520px;
  border-radius: 18px 18px 0 0; padding: 22px 20px calc(18px + env(safe-area-inset-bottom));
  box-shadow: 0 -8px 30px rgba(0,0,0,.5); }
#ask h2 { font-size: 18px; margin: 0 0 6px; }
#ask p { font-size: 14px; line-height: 1.45; color: #b8c1cc; margin: 0 0 14px; }
#ask .kinds { display: flex; gap: 8px; margin-bottom: 12px; }
#ask .kind { flex: 1; padding: 10px 6px; border-radius: 12px; font-size: 14px;
  background: #1f2630; color: #c9d1db; border: 1px solid #2c3541; }
#ask .kind.on { background: #1d2a40; color: #fff; border-color: #4f8bf0; font-weight: 600; }
#ask textarea { width: 100%; box-sizing: border-box; background: #0b0e12; color: #fff;
  border: 1px solid #364050; border-radius: 12px; padding: 12px; font: inherit;
  font-size: 15px; resize: none; }
#ask textarea:focus { outline: none; border-color: #4f8bf0; }
#ask .count { text-align: right; font-size: 12px; color: #8b94a1; margin: 4px 2px 12px; }
#ask .row { display: flex; gap: 10px; }
#ask .row button { flex: 1; padding: 13px; border-radius: 12px; font-size: 15px;
  font-weight: 600; border: 0; }
#ask .go { background: #2f6fed; color: #fff; }
#ask .go:disabled { background: #22324f; color: #8da0bf; }
#ask .quiet { background: #252c36; color: #e9eef4; }
#ask .told { min-height: 20px; margin-top: 10px; text-align: center; font-size: 14px; }
#ask .told.ok { color: #5fd38d; }
#ask .told.bad { color: #e26d76; }

/* Le premier accueil : plein ecran, une question a la fois. */
#hello { position: fixed; inset: 0; z-index: 45; background: radial-gradient(120% 80% at
  50% 0%, #17233a 0%, #0b0d10 60%); overflow-y: auto; -webkit-overflow-scrolling: touch; }
#hello[hidden] { display: none; }
.hello-in { max-width: 460px; min-height: 100%; margin: 0 auto; display: flex;
  flex-direction: column; padding: calc(18px + env(safe-area-inset-top)) 20px
  calc(16px + env(safe-area-inset-bottom)); }
.dots { display: flex; gap: 6px; justify-content: center; margin-bottom: 22px; }
.dots i { width: 7px; height: 7px; border-radius: 4px; background: #2a3442;
  transition: width .25s, background .25s; }
.dots i.on { width: 22px; background: #4f8bf0; }
.dots i.done { background: #35507d; }
#hello section { flex: 1; animation: rise .35s ease both; }
#hello section[hidden] { display: none; }
@keyframes rise { from { opacity: 0; transform: translateY(14px); }
  to { opacity: 1; transform: none; } }
#hello h2 { font-size: 24px; line-height: 1.2; margin: 6px 0 8px; letter-spacing: -.2px; }
#hello .lead { color: #aeb8c5; font-size: 15px; line-height: 1.5; margin: 0 0 22px; }
#hello .wordmark.big { margin: 4px 0 22px; height: 30px; }
.field { display: block; margin-bottom: 16px; }
.field span { display: block; font-size: 13px; color: #aeb8c5; margin: 0 0 7px 2px; }
.field em { font-style: normal; color: #6f7885; }
.field input { width: 100%; background: #10151c; border: 1px solid #2b3542;
  border-radius: 12px; padding: 14px; color: #fff; font-size: 17px; }
.field input:focus { outline: none; border-color: #4f8bf0;
  box-shadow: 0 0 0 3px rgba(79,139,240,.2); }
/* Le bon d'achat : une carte doree, sobre, avec un reflet qui passe. */
.perk { position: relative; display: flex; gap: 14px; align-items: center; overflow: hidden;
  padding: 14px 16px; border-radius: 16px; margin: -2px 0 8px;
  background: linear-gradient(140deg, rgba(232,190,104,.14), rgba(232,190,104,.03) 55%,
    rgba(255,255,255,.02));
  border: 1px solid rgba(232,190,104,.32);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.06), 0 10px 26px rgba(0,0,0,.28); }
.perk::after { content: ""; position: absolute; inset: 0; pointer-events: none;
  background: linear-gradient(105deg, transparent 40%, rgba(255,236,190,.12) 50%,
    transparent 60%);
  transform: translateX(-100%); animation: sheen 5s ease-in-out 1s infinite; }
@keyframes sheen { 0% { transform: translateX(-100%); }
  35%, 100% { transform: translateX(100%); } }
.perk-ic { flex: none; width: 42px; height: 42px; border-radius: 12px; display: flex;
  align-items: center; justify-content: center; color: #2a1d05;
  background: linear-gradient(145deg, #f3d48a, #b8862f);
  box-shadow: 0 4px 14px rgba(200,150,60,.35), inset 0 1px 0 rgba(255,255,255,.4); }
.perk-ic svg { width: 22px; height: 22px; }
.perk-ic.sm { width: 36px; height: 36px; border-radius: 10px; }
.perk-ic.sm svg { width: 19px; height: 19px; }
.perk-ic.alt { background: linear-gradient(145deg, #9db8ff, #4a6fd6); color: #0b1530;
  box-shadow: 0 4px 14px rgba(74,111,214,.35), inset 0 1px 0 rgba(255,255,255,.4); }
.perk b { display: block; color: #f6e3b4; font-size: 15px; letter-spacing: .1px; }
.perk small { display: block; color: #b7ab92; font-size: 13px; line-height: 1.4;
  margin-top: 2px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; padding-bottom: 8px; }
.chip { border-radius: 999px; padding: 9px 14px; font-size: 14px; background: #161c25;
  border: 1px solid #2b3542; color: #cfd7e1; transition: transform .12s, background .15s; }
.chip:active { transform: scale(.95); }
.chip.on { background: linear-gradient(135deg, #3a6fe0, #8a4fe8); border-color: transparent;
  color: #fff; font-weight: 600; }
.picked { color: #8b94a1; font-size: 13px; margin: 0 0 12px 2px; }
.tips { display: flex; flex-direction: column; gap: 12px; margin-top: 4px; }
.tip { display: flex; gap: 13px; background: #121821; border: 1px solid #222b37;
  border-radius: 14px; padding: 14px; }
.tip .em { font-size: 24px; line-height: 1; flex: none; }
.tip b { display: block; color: #fff; font-size: 15px; margin-bottom: 4px; }
.tip p { margin: 0; color: #aeb8c5; font-size: 14px; line-height: 1.45; }
.demo { display: flex; justify-content: center; margin: 2px 0 20px; }
.demo .ask { display: inline-flex; align-items: center; gap: 8px; padding: 11px 18px;
  border-radius: 12px; background: #1d2a40; color: #dbe7ff; border: 1px solid #2f4f86;
  font-size: 16px; font-weight: 600; animation: glow 1.8s ease-in-out infinite; }
.demo .ask svg { width: 18px; height: 18px; fill: currentColor; }
@keyframes glow { 0%, 100% { box-shadow: 0 0 0 0 rgba(79,139,240,.55); }
  50% { box-shadow: 0 0 0 10px rgba(79,139,240,0); } }
.party { font-size: 54px; text-align: center; margin: 8px 0 4px;
  animation: pop .6s cubic-bezier(.2,1.6,.4,1) both; }
@keyframes pop { from { transform: scale(.3); opacity: 0; } to { transform: none; opacity: 1; } }
.center { text-align: center; }
.meter { height: 8px; border-radius: 4px; background: #1d2530; overflow: hidden; margin: 6px 0 4px; }
.meter i { display: block; height: 100%; width: 0; border-radius: 4px;
  background: linear-gradient(90deg, #f5c542, #f08a4f); transition: width .6s; }
.meterline { display: flex; justify-content: space-between; font-size: 12px; color: #8b94a1; }
.meterline .gold { color: #e8c068; font-weight: 600; }
.aside { color: #8b94a1; font-size: 13px; line-height: 1.5; text-align: center;
  margin: 14px 4px 0; }
.aside b { color: #cfd7e1; font-weight: 600; }
.card2 { background: #121821; border: 1px solid #222b37; border-radius: 14px; padding: 14px;
  margin-top: 14px; }
.card2 h3 { font-size: 15px; margin: 0 0 4px; }
.card2 p { color: #aeb8c5; font-size: 14px; line-height: 1.45; margin: 0 0 12px; }
.how { color: #d5dce5; font-size: 14px; line-height: 1.55; margin: 0 0 12px; }
.how b { color: #fff; }
.how ol { margin: 0; padding-left: 20px; }
.how li { margin-bottom: 4px; }
.share { display: inline-block; width: 16px; height: 16px; vertical-align: -3px;
  fill: #4f8bf0; }
a.btn, .wide { display: block; width: 100%; text-align: center; padding: 13px;
  border-radius: 12px; font-size: 15px; font-weight: 600; border: 0; text-decoration: none; }
a.btn { background: #252c36; color: #e9eef4; margin-top: 8px; }
.wide.go, .hello-nav .go { background: #2f6fed; color: #fff; }
.hello-nav { display: flex; gap: 10px; padding-top: 18px; }
.hello-nav button { flex: 1; padding: 15px; border-radius: 14px; font-size: 16px;
  font-weight: 600; border: 0; }
.hello-nav .quiet { flex: 0 0 auto; background: #1b222c; color: #cfd7e1; }
.hello-nav .go:disabled { background: #22324f; color: #8da0bf; }
/* Les recompenses : une ligne fine au-dessus des dossiers, rien de plus. */
.perkline { display: flex; width: 100%; align-items: center; gap: 9px; margin: -4px 0 6px;
  padding: 6px 2px; background: none; border: 0; border-radius: 0; color: #8b94a1;
  font-size: 12px; text-align: left; font-variant-numeric: tabular-nums; }
.perkline:active { background: none; opacity: .7; }
.perkline[hidden] { display: none; }
.perkline .gi { display: inline-flex; color: #d8b46a; flex: none; }
.perkline .gi svg { width: 14px; height: 14px; }
.perkline .thin { flex: 1; height: 2px; background: #1d2530; border-radius: 1px;
  overflow: hidden; }
.perkline .thin i { display: block; height: 100%; background: linear-gradient(90deg,
  #e8c068, #c98a3a); }
.perkline .go { color: #d8b46a; font-weight: 600; flex: none; }
#perks { position: fixed; inset: 0; z-index: 41; background: rgba(0,0,0,.55);
  display: flex; align-items: flex-end; justify-content: center; }
#perks[hidden] { display: none; }
#perks .sheet { background: #12161c; color: #e9eef4; width: 100%; max-width: 520px;
  border-radius: 18px 18px 0 0; padding: 22px 18px calc(16px + env(safe-area-inset-bottom));
  box-shadow: 0 -8px 30px rgba(0,0,0,.5); max-height: 92vh; overflow-y: auto; }
#perks h2 { font-size: 19px; margin: 0 0 14px; }
.reward { background: #171c24; border: 1px solid #252e3a; border-radius: 16px;
  padding: 14px; margin-bottom: 12px; }
.reward.gold { border-color: rgba(232,190,104,.30); background: linear-gradient(140deg,
  rgba(232,190,104,.10), rgba(23,28,36,1) 60%); }
.reward-head { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
.reward-head b { display: block; font-size: 15px; color: #fff; }
.reward-head small { display: block; color: #aeb8c5; font-size: 13px; line-height: 1.45;
  margin-top: 2px; }
.reward .meter { margin: 0; height: 6px; }
.reward .stats { display: flex; gap: 8px; margin: 0 0 12px; }
.reward .stats span { flex: 1; background: #10141a; border: 1px solid #222a35;
  border-radius: 12px; padding: 9px; text-align: center; color: #8b94a1; font-size: 12px; }
.reward .stats b { display: block; color: #fff; font-size: 18px; }
.textlink { background: none; border: 0; padding: 10px 0 0; color: #8fb4ff;
  font-size: 14px; font-weight: 600; }
.textlink:active { background: none; }
#perks .told { min-height: 18px; margin-top: 8px; text-align: center; font-size: 13px;
  color: #5fd38d; word-break: break-all; }
#perks .foot { display: flex; justify-content: space-between; padding: 2px 4px 0; }
/* Le doigt pointe le bouton « Demande », une seule fois. */
#coach { position: fixed; inset: 0; z-index: 46; }
#coach[hidden] { display: none; }
#coachRing { position: fixed; border-radius: 12px; pointer-events: auto;
  box-shadow: 0 0 0 9999px rgba(3,6,10,.78), 0 0 0 3px #4f8bf0;
  animation: ring 1.6s ease-in-out infinite; }
@keyframes ring { 50% { box-shadow: 0 0 0 9999px rgba(3,6,10,.78), 0 0 0 7px #4f8bf0; } }
#coachBubble { position: fixed; background: #fff; color: #10151c; border-radius: 14px;
  padding: 14px 16px; width: min(300px, calc(100vw - 32px)); font-size: 14px;
  line-height: 1.45; box-shadow: 0 10px 30px rgba(0,0,0,.4); }
#coachBubble::before { content: ""; position: absolute; top: -7px; width: 14px; height: 14px;
  background: #fff; transform: rotate(45deg); right: var(--tail, 40px); }
#coachBubble b { display: block; font-size: 15px; margin-bottom: 3px; }
#coachBubble button { margin-top: 10px; background: #2f6fed; color: #fff; border: 0;
  font-weight: 600; width: 100%; padding: 10px; }
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
const hits = $('hits'), hitsHead = $('hitsHead'), videosHead = $('videosHead');
const empty = $('empty'), more = $('more'), player = $('player');
const video = $('video');
const PAGE = 300;

// -- la cle du lien : gardee par le telephone, jointe a chaque demande -------
// Le lien d'invitation mene ici avec « #cle=… » : on la retient, et l'adresse
// redevient « / ». Sans cookie, sans mot de passe : la cle suffit.
let key = '';
try { key = localStorage.getItem('prisme-cle') || ''; } catch (_) {}
const given = /[#&]cle=([^&]+)/.exec(location.hash);
// Venu du lien Wi-Fi pour poser l'icone : on reprend la question ici.
const cameToInstall = /[#&]installer=1/.test(location.hash);
if (given) {
  key = decodeURIComponent(given[1]);
  try { localStorage.setItem('prisme-cle', key); } catch (_) {}
}
// L'appareil se donne un nom tire au hasard (« moi ») : son profil le suit,
// de Chrome a l'icone de l'ecran d'accueil.
let me = '';
try { me = localStorage.getItem('prisme-moi') || ''; } catch (_) {}
const givenMe = /[#&]moi=([0-9a-f]{32})/.exec(location.hash);
if (givenMe) me = givenMe[1];
// Venu par le lien d'un ami : son code de parrainage, garde jusqu'a l'inscription.
const givenSponsor = /[#&]parrain=([a-z0-9]{8})/.exec(location.hash);
if (givenSponsor) {
  try { localStorage.setItem('prisme-parrain', givenSponsor[1]); } catch (_) {}
}
if (!/^[0-9a-f]{32}$/.test(me)) {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  me = Array.from(bytes, (b) => (b < 16 ? '0' : '') + b.toString(16)).join('');
}
try { localStorage.setItem('prisme-moi', me); } catch (_) {}
function selfHash() {
  return '#cle=' + encodeURIComponent(key) + '&moi=' + me;
}
// La cle reste dans l'adresse (apres #, jamais envoyee au serveur) : une
// icone posee sur l'ecran d'accueil garde l'adresse, et l'iPhone n'y partage
// pas la memoire du navigateur.
if (key && location.hash !== selfHash()) {
  history.replaceState(null, '', '/' + selfHash());
}
function withKey(url) {
  // L'identifiant aussi : une image, une video comptent pour la bonne personne.
  return key ? url + (url.includes('?') ? '&' : '?') + 'cle=' + encodeURIComponent(key) +
    '&moi=' + me : url;
}
function headers(extra) {
  const h = Object.assign({}, extra || {});
  if (key) h['X-Prisme-Cle'] = key;
  h['X-Prisme-Moi'] = me;
  return h;
}

function denied(password) {
  closePlayer();
  document.querySelector('main').replaceChildren();
  const box = document.createElement('div');
  box.className = 'empty';
  box.textContent = "Ce lien a été remplacé : il ne donne plus accès à la " +
    "bibliothèque. Si c'est la vôtre : sur le PC, Prisme › Partage à " +
    "distance › onglet « Téléphone », et scannez le code. Sinon, demandez " +
    "le nouveau lien à qui vous l'a envoyé.";
  document.querySelector('main').appendChild(box);
  if (password) {
    const a = document.createElement('p');
    const link = document.createElement('a');
    link.href = '/login'; link.textContent = "J'ai un mot de passe";
    link.style.color = '#8fb4ff';
    a.appendChild(link); box.appendChild(a);
  }
}

async function getJSON(url, signal) {
  try {
    const r = await fetch(url, {signal, headers: headers()});
    if (r.status === 401) {
      let told = {};
      try { told = await r.json(); } catch (_) {}
      denied(told.password);
      return null;
    }
    if (!r.ok) return null;
    return await r.json();
  } catch (_) {
    return null;
  }
}

// -- une carte : du texte pose comme texte, jamais interprete en HTML ------
function card(c) {
  const el = document.createElement('div');
  el.className = 'card';
  const frame = document.createElement('div');
  frame.className = 'frame';
  const blank = () => {
    const b = document.createElement('div');
    b.className = 'shot';
    return b;
  };
  if (c.shot) {
    const img = document.createElement('img');
    img.className = 'shot';
    img.loading = 'lazy';
    img.alt = '';
    img.onerror = () => img.replaceWith(blank());
    img.src = withKey(c.shot);
    frame.appendChild(img);
  } else {
    frame.appendChild(blank());
  }
  if (c.badge) {
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = c.badge;
    frame.appendChild(badge);
  }
  el.appendChild(frame);
  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = c.title;
  el.appendChild(label);
  if (c.note) {
    const note = document.createElement('div');
    note.className = 'dim note';
    note.textContent = c.note;
    el.appendChild(note);
  }
  el.onclick = c.go;
  return el;
}

function folderCard(f) {
  if (f.rayon) {
    return {shot: f.cover ? '/thumb/' + f.cover : '', title: f.name,
            badge: String(f.count),
            note: f.folders + ' dossier(s) · ' + f.count + ' vidéo(s)',
            go: () => openShelf(f.name, true)};
  }
  return {shot: f.cover ? '/thumb/' + f.cover : '', title: f.name,
          badge: String(f.count),
          note: f.count + ' vidéo(s) · ' + f.size,
          go: () => openFolder(f.id, f.name, true)};
}

function videoCard(v, list, index) {
  return {shot: '/thumb/' + v.id, title: v.name, badge: v.duration || '',
          note: [v.folder, v.height ? v.height + 'p' : ''].filter(Boolean).join(' · '),
          go: () => play(list, index)};
}

// -- deux grilles : les dossiers, gardes tels quels, et le reste ----------
const views = {
  folders: {el: shelf, pending: [], more: null, where: ''},
  videos: {el: grid, pending: [], more: null, where: ''},
};
let view = views.folders;
const folders = {total: -1, loaded: 0, version: '', scroll: 0, round: 0};
let nav = 0;
let shown = {v: 'folders'};
// Les preferences epinglees, « Pour vous », et le mot d'une vue vide.
let pinned = [], forYouList = [], fewText = '', emptyNote = '';

function showEmpty() {
  const none = !view.el.childElementCount && !view.pending.length && !view.more
    && hits.hidden;
  empty.hidden = !none;
  if (none) empty.textContent = emptyNote || 'Rien à montrer ici.';
}

function reveal(target) {
  if (view === views.folders && target !== view) folders.scroll = scrollY;
  view = target;
  shelf.hidden = target !== views.folders;
  grid.hidden = target !== views.videos;
  if (target === views.folders) { hits.hidden = true; hitsHead.hidden = true; videosHead.hidden = true; }
  emptyNote = '';
  $('few').hidden = true;
  $('forYou').hidden = !(forYouList.length && (shown.v === 'folders' || shown.v === 'videos'));
  markPins();
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

async function nextFolders() {
  const round = folders.round, start = folders.loaded;
  const d = await getJSON('/api/folders?start=' + start + '&count=' + PAGE);
  if (!d || round !== folders.round) return false;
  if (folders.version && d.version !== folders.version) {
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
  $('q').value = '';
  mark('tabFolders');
  reveal(views.folders);
  scrollTo(0, folders.scroll);
  if (folders.total < 0) { topUp(); return; }
  const d = await getJSON('/api/folders?start=0&count=0');
  if (!d || ticket !== nav) return;
  if (d.version !== folders.version) {
    resetFolders();
    reveal(views.folders);
    topUp();
  }
}

function showVideos(list, where, found) {
  grid.replaceChildren();
  hits.replaceChildren();
  views.videos.pending = list.map((v, i) => videoCard(v, list, i));
  views.videos.more = null;
  views.videos.where = where;
  reveal(views.videos);
  const folderHits = found || [];
  hits.hidden = !folderHits.length;
  hitsHead.hidden = !folderHits.length;
  videosHead.hidden = !folderHits.length || !list.length;
  for (const f of folderHits) hits.appendChild(card(folderCard(f)));
  scrollTo(0, 0);
  showEmpty();
  topUp();
}

// -- un rayon : un dossier « + » et les siens ------------------------------
async function openShelf(name, push) {
  const ticket = ++nav;
  if (asking) { asking.abort(); asking = null; }
  if (push) remember({v: 'shelf', name});
  shown = {v: 'shelf', name};
  const d = await getJSON('/api/folders?rayon=' + encodeURIComponent(name) +
                          '&start=0&count=1000');
  if (ticket !== nav) return;
  const list = (d && d.folders) || [];
  grid.replaceChildren(); hits.replaceChildren();
  hits.hidden = true; hitsHead.hidden = true; videosHead.hidden = true;
  views.videos.pending = list.map(folderCard);
  views.videos.more = null;
  views.videos.where = name + ' — ' + list.length + ' dossier(s)';
  reveal(views.videos);
  scrollTo(0, 0);
  topUp();
}

// -- les favoris de ce telephone -------------------------------------------
let favs = new Set();
async function loadFavs() {
  const d = await getJSON('/api/favorites');
  favs = new Set(((d && d.videos) || []).map((v) => v.id));
  return (d && d.videos) || [];
}
// -- les liens envoyes : ou en est leur telechargement ------------------------
let linksTimer = null;
function linkLine(jobs) {
  const live = jobs.filter((j) => j.state === 'attente' || j.state === 'en cours');
  const recent = Date.now() / 1000 - 6 * 3600;
  const failed = jobs.filter((j) => j.state === 'échec' && j.at > recent);
  const parts = [];
  if (live.length) {
    const now = live.find((j) => j.state === 'en cours');
    parts.push('⏳ ' + live.length + ' vidéo(s) en téléchargement' +
      (now && now.progress > 0 ? ' — ' + Math.round(now.progress * 100) + ' %' : ''));
  }
  if (failed.length) parts.push('✕ ' + failed[0].error);
  return parts.join(' · ');
}
async function watchLinks() {
  clearTimeout(linksTimer);
  const d = await getJSON('/api/liens');
  const jobs = (d && d.jobs) || [];
  const live = jobs.some((j) => j.state === 'attente' || j.state === 'en cours');
  if (shown.v === 'favs') {
    const line = linkLine(jobs);
    const base = views.videos.where.split(' · ⏳')[0].split(' · ✕')[0];
    crumb.textContent = line ? base + ' · ' + line : base;
    // Une video vient d'arriver : la liste des favoris se refait.
    const arrived = jobs.some((j) => j.state === 'fait' && j.mark && !favs.has(j.mark));
    if (arrived) { showFavorites(false); return; }
  }
  if (live) linksTimer = setTimeout(watchLinks, 5000);
}

async function showFavorites(push) {
  const ticket = ++nav;
  if (asking) { asking.abort(); asking = null; }
  if (push) remember({v: 'favs'});
  shown = {v: 'favs'};
  $('q').value = '';
  const list = await loadFavs();
  if (ticket !== nav) return;
  mark('tabFavs');
  showVideos(list, list.length ? 'Favoris — ' + list.length + ' vidéo(s)'
                               : 'Pas encore de favori : l’étoile du lecteur en ajoute.');
  watchLinks();
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

// -- la recherche : dossiers et videos, la derniere frappe gagne ------------
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
  const list = (d && d.videos) || [], found = (d && d.folders) || [];
  showVideos(list, '« ' + text + ' » — ' + found.length + ' dossier(s), ' +
             list.length + ' vidéo(s)', found);
  // Peu de resultats : on invite a demander ce qui manque.
  if (list.length + found.length < 6) showFew(text);
  if (!list.length && !found.length) {
    emptyNote = 'Aucun résultat pour « ' + text + ' ».';
    showEmpty();
  }
}
function showFew(text) {
  fewText = text;
  $('few').hidden = false;
}

function typed() {
  clearTimeout(typing);
  const text = $('q').value.trim();
  typing = setTimeout(() => search(text, true), 300);
}
$('q').addEventListener('input', typed);
// Certains claviers (Samsung) n'envoient le mot qu'a la fin de la saisie.
$('q').addEventListener('compositionend', typed);
// La loupe, ou « Entree » : tout de suite, et le clavier se range.
function searchNow() {
  clearTimeout(typing);
  $('q').blur();
  search($('q').value.trim(), true);
}
$('q').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); searchNow(); }
});
$('qGo').onclick = searchNow;

// -- le lecteur : une seule barre, fine, toujours affichee -----------------
let watching = null, lastBeat = 0;
let queue = [], index = -1, shuffle = false;
const stage = $('stage'), seekBar = $('seek');
let dragging = false;

function beat(force) {
  if (!watching) return;
  const now = Date.now() / 1000;
  const delta = lastBeat ? now - lastBeat : 0;
  lastBeat = now;
  if (delta <= 0 && !force) return;
  fetch('/api/watching', {
    method: 'POST', headers: headers({'Content-Type': 'application/json'}),
    body: JSON.stringify({id: watching, seconds: Math.min(delta, 60),
                          at: video.currentTime || 0, playing: !video.paused}),
    keepalive: true,
  }).catch(() => {});
}

function clock(s) {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), r = s % 60;
  return (h ? h + ':' + (m < 10 ? '0' : '') : '') + m + ':' + (r < 10 ? '0' : '') + r;
}

let flashTimer = null;
function flash(text, side) {
  const f = $('flash');
  f.textContent = text;
  f.className = 'on' + (side ? ' ' + side : '');
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { f.className = side || ''; }, 600);
}

// Un dessin SVG n'a pas la propriete « hidden » des elements HTML : c'est
// l'attribut qu'il faut poser, sinon les deux icones d'un bouton s'affichent.
function shows(id, on) { $(id).toggleAttribute('hidden', !on); }

function realFull() {
  return !!(document.fullscreenElement || document.webkitFullscreenElement);
}
function isFull() {
  return realFull() || stage.classList.contains('turned');
}

function dress() {
  shows('iSound', !video.muted);
  shows('iMuted', video.muted);
  shows('iFull', !isFull());
  shows('iUnfull', isFull());
  shows('iStar', !favs.has(watching));
  shows('iStarOn', favs.has(watching));
  $('prev').disabled = !shuffle && index <= 0;
  $('next').disabled = !shuffle && index >= queue.length - 1;
}

function paintSeek() {
  const d = video.duration || 0;
  if (!dragging) seekBar.value = d ? Math.round(1000 * video.currentTime / d) : 0;
  $('now').textContent = clock(dragging ? d * seekBar.value / 1000 : video.currentTime);
  $('total').textContent = d ? clock(d) : '0:00';
}

function load(v) {
  if (watching) beat(false);
  document.title = v.name + ' — Prisme';
  video.src = withKey('/video/' + v.id);
  watching = v.id; lastBeat = 0;
  paintSeek(); dress();
  video.play().catch(() => {});
}

function play(list, i, random) {
  queue = list; index = i; shuffle = !!random;
  if (player.style.display !== 'block') {
    player.style.display = 'block';
    document.body.classList.add('watching');
    // « Retour » ferme le lecteur, au lieu de quitter le site.
    remember({v: 'player'});
  }
  load(queue[index]);
}

async function step(delta) {
  if (shuffle && delta > 0 && index >= queue.length - 1) {
    const d = await getJSON('/api/random?n=20');
    const more = (d && d.videos) || [];
    if (!more.length) return;
    queue = queue.concat(more);
  }
  const target = index + delta;
  if (target < 0 || target >= queue.length) return;
  index = target;
  load(queue[index]);
}

function seek(by) {
  const d = video.duration || 0;
  if (!d) return;
  video.currentTime = Math.max(0, Math.min(d - 0.5, video.currentTime + by));
  flash((by > 0 ? '+' : '−') + Math.abs(by) + ' s', by > 0 ? 'right' : 'left');
}

function toggle() {
  if (video.paused) { video.play().catch(() => {}); flash('▶'); }
  else { video.pause(); flash('❚❚'); }
}

async function goFull() {
  // Le plein ecran prend tout le lecteur, barre comprise : le meme bouton en
  // fait sortir. « navigationUI: hide » : ni barre d'adresse, ni barre du
  // systeme.
  const wide = !video.videoWidth || video.videoWidth >= video.videoHeight;
  try {
    // Certains navigateurs ne repondent jamais : on n'attend pas plus d'une
    // seconde, le quart de tour prend le relais.
    if (stage.requestFullscreen) await Promise.race([
      stage.requestFullscreen({navigationUI: 'hide'}),
      new Promise(done => setTimeout(done, 1000))]);
    else if (stage.webkitRequestFullscreen) stage.webkitRequestFullscreen();
  } catch (_) {}
  // Une video en largeur : on demande au telephone de passer en paysage.
  try {
    if (realFull() && screen.orientation && screen.orientation.lock) {
      await screen.orientation.lock(wide ? 'landscape' : 'portrait');
      dress();
      return;
    }
  } catch (_) {}
  // Il refuse (iPhone, certains navigateurs) : c'est l'image qui tourne d'un
  // quart de tour, barre comprise, tant que le telephone est tenu droit.
  if (wide && innerHeight > innerWidth) stage.classList.add('turned');
  dress();
}

function leaveFull() {
  try { if (screen.orientation && screen.orientation.unlock) screen.orientation.unlock(); } catch (_) {}
  stage.classList.remove('turned');
  if (realFull()) (document.exitFullscreen || document.webkitExitFullscreen).call(document);
  dress();
}

// Le telephone tourne pour de bon : l'image n'a plus a tourner elle-meme.
addEventListener('resize', () => {
  if (stage.classList.contains('turned') && innerWidth > innerHeight) {
    stage.classList.remove('turned');
    dress();
  }
});
document.addEventListener('fullscreenchange', () => {
  if (!realFull()) stage.classList.remove('turned');
});

$('prev').onclick = () => step(-1);
$('next').onclick = () => step(1);
$('mute').onclick = () => { video.muted = !video.muted; };
$('fav').onclick = async () => {
  if (!watching) return;
  const id = watching, on = !favs.has(id);
  if (on) favs.add(id); else favs.delete(id);
  dress();
  flash(on ? '★ En favori' : 'Retiré des favoris');
  try {
    await fetch('/api/favorite', {method: 'POST',
      headers: headers({'Content-Type': 'application/json'}),
      body: JSON.stringify({id, on})});
  } catch (_) {}
};
$('full').onclick = () => { if (isFull()) leaveFull(); else goFull(); };
document.addEventListener('fullscreenchange', dress);
document.addEventListener('webkitfullscreenchange', dress);
video.addEventListener('volumechange', dress);
video.addEventListener('timeupdate', paintSeek);
video.addEventListener('loadedmetadata', paintSeek);

seekBar.addEventListener('input', () => { dragging = true; paintSeek(); });
seekBar.addEventListener('change', () => {
  const d = video.duration || 0;
  if (d) video.currentTime = d * seekBar.value / 1000;
  dragging = false;
  paintSeek();
});

// Un tap sur l'image : pause, ou lecture. Deux taps : a gauche on recule, a
// droite on avance de dix secondes.
let tapTimer = null, lastTap = 0, lastX = 0;
video.addEventListener('pointerup', (e) => {
  const now = Date.now();
  if (now - lastTap < 300 && Math.abs(e.clientX - lastX) < 80) {
    clearTimeout(tapTimer);
    lastTap = 0;
    const box = video.getBoundingClientRect();
    const x = (e.clientX - box.left) / box.width;
    if (x < 0.35) seek(-10);
    else if (x > 0.65) seek(10);
    return;
  }
  lastTap = now; lastX = e.clientX;
  clearTimeout(tapTimer);
  tapTimer = setTimeout(toggle, 300);
});

addEventListener('keydown', (e) => {
  if (player.style.display !== 'block' || e.target === $('q')) return;
  if (e.key === ' ') { e.preventDefault(); toggle(); }
  else if (e.key === 'ArrowRight') seek(10);
  else if (e.key === 'ArrowLeft') seek(-10);
  else if (e.key === 'n' || e.key === 'PageDown') step(1);
  else if (e.key === 'p' || e.key === 'PageUp') step(-1);
  else if (e.key === 'm') video.muted = !video.muted;
  else if (e.key === 'Escape' && !isFull()) history.back();
});

function closePlayer() {
  if (player.style.display !== 'block') return;
  beat(false); watching = null;
  video.pause(); video.removeAttribute('src'); video.load();
  if (isFull()) leaveFull();
  player.style.display = 'none';
  document.body.classList.remove('watching');
  document.title = 'Prisme';
  refreshGift();
}

video.addEventListener('playing', () => { lastBeat = Date.now() / 1000; });
video.addEventListener('pause', () => beat(false));
// A la fin, la suivante : c'est ce qu'on attend d'une liste.
video.addEventListener('ended', () => { beat(false); step(1); });
// Toutes les dix secondes en lecture : Prisme, sur le PC, suit en direct.
setInterval(() => { if (!video.paused && watching) beat(false); }, 10000);
video.addEventListener('seeked', () => beat(true));
addEventListener('pagehide', () => beat(true));

// -- « Retour » : on revient a la vue d'avant, sans quitter le site ---------
function same(a, b) {
  return a.v === b.v && a.id === b.id && a.q === b.q && a.like === b.like;
}
addEventListener('popstate', (e) => {
  // « Retour » dans l'accueil : l'etape d'avant, sans quitter la page.
  if (!$('hello').hidden) {
    if (helloStep > 0) helloShow(helloStep - 1);
    history.pushState({v: 'hello'}, '');
    return;
  }
  // « Retour » sur une feuille (demande, recompenses, icone) : elle se baisse.
  const sheet = SHEETS.find((id) => !$(id).hidden);
  if (sheet) { $(sheet).hidden = true; return; }
  const s = e.state || {v: 'folders'};
  closePlayer();
  if (s.v === 'player' || same(s, shown)) return;
  if (s.v === 'folder') openFolder(s.id, s.name, false);
  else if (s.v === 'search') { $('q').value = s.q; search(s.q, false); }
  else if (s.v === 'videos') showAllVideos(false);
  else if (s.v === 'shelf') openShelf(s.name, false);
  else if (s.v === 'favs') showFavorites(false);
  else if (s.v === 'tag') showTag(s.like, false);
  else showFolders(false);
});

$('home').onclick = () => showFolders(shown.v !== 'folders');
$('tabFolders').onclick = () => showFolders(shown.v !== 'folders');
$('tabVideos').onclick = () => showAllVideos(shown.v !== 'videos');

function mark(tab) {
  for (const id of ['tabFolders', 'tabVideos', 'tabFavs']) {
    $(id).classList.toggle('on', id === tab);
  }
}
$('tabFavs').onclick = () => showFavorites(shown.v !== 'favs');

// -- toutes les videos : par pages, au fil du defilement ---------------------
const every = {loaded: 0, total: -1, version: '', round: 0, list: []};
async function nextVideos() {
  const round = every.round, start = every.loaded;
  const d = await getJSON('/api/videos?start=' + start + '&count=' + PAGE);
  if (!d || round !== every.round) return false;
  every.total = d.total;
  const list = d.videos || [];
  every.loaded = start + list.length;
  // Une seule liste, qui s'allonge : « suivante » continue au-dela de la page.
  const base = every.list.length;
  every.list.push(...list);
  list.forEach((v, i) => views.videos.pending.push(videoCard(v, every.list, base + i)));
  if (!list.length || every.loaded >= every.total) views.videos.more = null;
  views.videos.where = every.total + ' vidéo(s)';
  if (view === views.videos) crumb.textContent = views.videos.where;
  return true;
}

function showAllVideos(push) {
  ++nav;
  if (asking) { asking.abort(); asking = null; }
  if (push) remember({v: 'videos'});
  shown = {v: 'videos'};
  $('q').value = '';
  every.round += 1;
  Object.assign(every, {loaded: 0, total: -1, list: []});
  grid.replaceChildren(); hits.replaceChildren();
  views.videos.pending = [];
  views.videos.more = nextVideos;
  views.videos.where = '';
  reveal(views.videos);
  mark('tabVideos');
  scrollTo(0, 0);
  topUp();
}

$('home').querySelector('img').addEventListener('error', (e) => {
  e.target.src = '/lettrage.png';
  e.target.className = 'wordmark';
}, {once: true});
history.replaceState({v: 'folders'}, '');
loadFavs();
resetFolders();
showFolders(false);

// -- l'icone sur l'ecran d'accueil ---------------------------------------
// La fiche de l'application porte la cle et l'appareil : l'icone s'ouvre avec
// l'acces et le profil, meme sur iPhone (ou elle ne partage pas la memoire
// de Safari).
if (key) {
  const card = document.querySelector('link[rel=manifest]');
  if (card) card.href = '/manifest.webmanifest?cle=' + encodeURIComponent(key) + '&moi=' + me;
}
const ua = navigator.userAgent;
const installed = matchMedia('(display-mode: standalone)').matches ||
                  matchMedia('(display-mode: fullscreen)').matches ||
                  navigator.standalone === true;
const apple = /iphone|ipad|ipod/i.test(ua) ||
              (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const android = /android/i.test(ua);
const samsung = /SamsungBrowser/i.test(ua);
// Chrome, Firefox… sur iPhone : l'icone se pose aussi, par leur « Partager ».
const appleOther = apple && /CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
// Les navigateurs integres aux applis (WhatsApp, Gmail, Instagram, le lecteur
// de QR code…) n'installent rien, et leur menu ⋮ n'a pas « Ajouter a l'ecran
// d'accueil » : il faut en sortir. On ne les reconnait pas tous ; ceux qu'on
// ne reconnait pas finissent sur le meme bouton « Ouvrir dans Chrome ».
const inApp = /; wv\\)|FBAN|FBAV|FB_IAB|Instagram|Snapchat|Line\\/|MicroMessenger|GSA\\/|WhatsApp|Telegram/i.test(ua) ||
              document.referrer.startsWith('android-app://') ||
              (apple && !/Safari\\//.test(ua));
let offer = null;
if ('serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(() => {});
}
// L'icone du panneau, si le NAS n'en a pas encore : pas d'image cassee.
$('install').querySelector('img').addEventListener('error', e => { e.target.hidden = true; });
addEventListener('beforeinstallprompt', e => { e.preventDefault(); offer = e; });
addEventListener('appinstalled', () => {
  try { localStorage.setItem('prisme-icone', 'posee'); } catch (_) {}
  closeSheet('install');
  installDone();
});

// Sortir du navigateur integre : Chrome sur Android, par le lien d'invitation
// (« /entrer/ », qui rouvre la session la-bas, avec le meme profil) ; Safari
// sur iPhone, par « x-safari-https » (iOS 17 et suivants).
function escapeUrl() {
  if (!key) return '';
  if (android) {
    return 'intent://' + location.host + '/entrer/' + encodeURIComponent(key) +
      '?installer=1&moi=' + me + '#Intent;scheme=' + location.protocol.slice(0, -1) +
      ';package=com.android.chrome;end';
  }
  if (apple) return 'x-safari-' + location.origin + '/' + selfHash() + '&installer=1';
  return '';
}

// Ouvert dans Messenger, Facebook, Instagram… : la page y est plus petite, et
// rien ne s'y installe. On propose le vrai navigateur ; sur Android, on y va
// tout de suite, une fois par visite.
function offerEscape() {
  if (installed || !key || !inApp || !(android || apple)) return;
  const url = escapeUrl();
  if (!url) return;
  const where = android ? 'Chrome' : 'Safari';
  $('outAppTitle').textContent = 'Prisme s’affiche mieux dans ' + where;
  $('outAppGo').textContent = 'Ouvrir dans ' + where;
  $('outAppGo').onclick = () => { location.href = url; };
  $('outApp').hidden = false;
  let tried = '';
  try { tried = sessionStorage.getItem('prisme-sortie') || ''; } catch (_) {}
  if (android && !tried) {
    try { sessionStorage.setItem('prisme-sortie', '1'); } catch (_) {}
    setTimeout(() => { location.href = url; }, 400);
  }
}
offerEscape();

const SHARE = '<svg class="share" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 7.5 6.5l1.4 1.4L11 5.8V15h2V5.8l2.1 2.1 1.4-1.4zM5 10v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V10h-3v2h1v8H7v-8h1v-2z"/></svg>';
// Le geste a la main, dit pour le navigateur ou l'on est.
function howTo() {
  if (apple && inApp) {
    return '<p>Cette page est ouverte dans une autre application, qui ne sait pas ' +
      'poser d’icône. Ouvrez-la dans <b>Safari</b> : la question reviendra là-bas.</p>';
  }
  if (apple) {
    return '<ol><li>Touchez <b>Partager</b> ' + SHARE +
      (appleOther ? ', dans la barre d’adresse.'
                  : ' en bas de l’écran (ou <b>⋯</b>, puis <b>Partager</b>).') +
      '</li><li>Faites défiler, puis touchez <b>« Sur l’écran d’accueil »</b>.</li>' +
      '<li>Touchez <b>Ajouter</b> : l’icône Prisme apparaît.</li></ol>' +
      (appleOther ? '<p>Pas cette option ? Ouvrez la page dans Safari :</p>' : '');
  }
  if (android && inApp) {
    return '<p>Cette page est ouverte dans une autre application (WhatsApp, Gmail, ' +
      'le lecteur de QR code…), dont le menu ne sait pas poser d’icône. Ouvrez-la ' +
      'dans <b>Chrome</b> : la question reviendra là-bas.</p>';
  }
  if (android && samsung) {
    return '<ol><li>Touchez <b>≡</b>, en bas à droite.</li><li>Puis <b>« Ajouter ' +
      'la page à »</b> › <b>« Écran d’accueil »</b>.</li></ol><p>Ou l’icône ' +
      '<b>⬇</b> de la barre d’adresse, si elle y est. Sinon, avec Chrome :</p>';
  }
  if (android) {
    return '<ol><li>Touchez le menu <b>⋮</b>, en haut à droite.</li><li>Puis ' +
      '<b>« Ajouter à l’écran d’accueil »</b> ou <b>« Installer l’application »</b>.' +
      '</li></ol><p>Pas cette option dans le menu ? La page est ouverte dans une ' +
      'autre application : ouvrez-la dans Chrome.</p>';
  }
  return '<p>Dans la barre d’adresse, touchez l’icône d’installation, ou le menu ' +
    'du navigateur › <b>« Installer Prisme »</b>.</p>';
}

// Poser l'icone : la fenetre du telephone s'il en a une, sinon le geste a la
// main et, au besoin, de quoi sortir du navigateur integre. Rend « done »,
// « again » (toucher encore : la fenetre est prete), « refused », « shown »
// (le geste est affiche) ou « away » (on part vers l'adresse https).
async function doInstall(ui) {
  // Aucun telephone n'installe une page « http » (le lien Wi-Fi du NAS) :
  // on pose l'icone depuis l'adresse https, qui marche aussi hors de chez soi.
  if (location.protocol !== 'https:' && (android || apple)) {
    const d = await getJSON('/api/install');
    if (d && d.secure && d.secure.startsWith('https://')) {
      location.href = d.secure + '/' + selfHash() + '&installer=1';
      return 'away';
    }
  }
  let waited = false;
  if (!offer && !apple && !inApp) {
    // Chrome ne propose d'installer qu'au bout d'un moment : on attend sa
    // fenetre quelques secondes avant de montrer le geste a la main.
    const label = ui.go.textContent;
    ui.go.textContent = 'Un instant…';
    ui.go.disabled = true;
    for (let i = 0; i < 25 && !offer; i++) await new Promise(r => setTimeout(r, 200));
    ui.go.textContent = label;
    ui.go.disabled = false;
    waited = !!offer;
  }
  if (offer && waited) {
    // Arrivee pendant l'attente : le toucher est trop ancien pour l'ouvrir,
    // le suivant l'ouvrira.
    return 'again';
  }
  if (offer) {
    const asked = offer;
    offer = null;
    try {
      await asked.prompt();
    } catch (_) {
      offer = asked;
      return 'again';
    }
    const choice = await asked.userChoice.catch(() => ({}));
    if (choice.outcome === 'accepted') {
      try { localStorage.setItem('prisme-icone', 'posee'); } catch (_) {}
      return 'done';
    }
    return 'refused';
  }
  ui.how.innerHTML = howTo();
  ui.how.hidden = false;
  // Safari lui-meme n'a rien a quitter ; partout ailleurs, la sortie.
  const out = apple && !inApp && !appleOther ? '' : escapeUrl();
  if (out) {
    ui.out.href = out;
    ui.out.textContent = android ? 'Ouvrir dans Chrome' : 'Ouvrir dans Safari';
    ui.out.hidden = false;
  }
  return 'shown';
}

function askInstall() {
  // Deja une application, pas encore de cle, ou « ne plus demander » : rien.
  if (installed || !key) return;
  let said = '';
  try { said = localStorage.getItem('prisme-icone') || ''; } catch (_) {}
  if ((said === 'jamais' || said === 'posee') && !cameToInstall) return;
  openSheet('install');
}
$('installNo').onclick = () => {
  // « Non » : la question revient a la prochaine visite -- sauf si l'on a
  // coche « Ne plus me le demander ».
  if ($('installNever').checked) {
    try { localStorage.setItem('prisme-icone', 'jamais'); } catch (_) {}
  }
  closeSheet('install');
};
$('installYes').onclick = async () => {
  if ($('installYes').dataset.state === 'ok') { closeSheet('install'); return; }
  const result = await doInstall({go: $('installYes'), how: $('installHow'),
                                  out: $('installOut')});
  if (result === 'done') {
    closeSheet('install');
  } else if (result === 'again') {
    $('installYes').textContent = 'Installer maintenant';
  } else if (result === 'shown') {
    $('installTitle').textContent = 'Pour ajouter l’icône';
    $('installNeverRow').hidden = true;
    $('installNo').hidden = true;
    $('installYes').textContent = 'Compris';
    $('installYes').dataset.state = 'ok';
  }
};

// -- envoyer une demande -----------------------------------------------------------
let askKind = 'demande';
const ASK_HINT = $('askText').placeholder;
const LINK_HINT = 'Collez ici le lien de la vidéo (https://…) : elle arrivera dans ' +
                  'vos favoris sous 24 heures.';
// Les feuilles du bas : chacune ouvre une entree d'historique, pour que
// « Retour » (Android) la baisse au lieu de quitter Prisme.
const SHEETS = ['ask', 'perks', 'install'];
function openSheet(id) {
  if (!$(id).hidden) return;
  $(id).hidden = false;
  if (history.state && history.state.v === 'sheet') history.replaceState({v: 'sheet'}, '');
  else history.pushState({v: 'sheet'}, '');
}
function closeSheet(id) {
  if ($(id).hidden) return;
  $(id).hidden = true;
  if (history.state && history.state.v === 'sheet') history.back();
}
function askOpen() {
  $('askTold').textContent = '';
  $('askTold').className = 'told';
  openSheet('ask');
  setTimeout(() => $('askText').focus(), 60);
}
function askClose() { closeSheet('ask'); }
$('askBtn').onclick = askOpen;
$('askNo').onclick = askClose;
$('ask').onclick = (e) => { if (e.target === $('ask')) askClose(); };
$('askKinds').querySelectorAll('.kind').forEach((b) => {
  // Le bouton ne prend pas le focus : le clavier reste ouvert.
  b.addEventListener('pointerdown', (e) => e.preventDefault());
  b.addEventListener('mousedown', (e) => e.preventDefault());
  b.onclick = () => {
    askKind = b.dataset.kind;
    $('askKinds').querySelectorAll('.kind').forEach((o) => o.classList.toggle('on', o === b));
    $('askText').placeholder = askKind === 'lien' ? LINK_HINT : ASK_HINT;
    $('askText').inputMode = askKind === 'lien' ? 'url' : 'text';
    $('askText').focus();
  };
});
$('fewAsk').onclick = () => {
  askOpen();
  if (fewText && !$('askText').value) $('askText').value = 'J’aimerais voir : ' + fewText;
};
$('askGo').onclick = async () => {
  const text = $('askText').value.trim();
  const told = $('askTold');
  if (text.length < 2) {
    told.textContent = askKind === 'lien' ? 'Collez le lien de la vidéo.' : 'Écrivez votre demande.';
    told.className = 'told bad';
    return;
  }
  if (askKind === 'lien' && !/https?:\\/\\/\\S+/i.test(text)) {
    told.textContent = 'Collez un lien complet, qui commence par https://';
    told.className = 'told bad';
    return;
  }
  $('askGo').disabled = true;
  $('askGo').textContent = 'Envoi…';
  try {
    const r = await fetch('/api/demande', {method: 'POST',
      headers: headers({'Content-Type': 'application/json'}),
      body: JSON.stringify({kind: askKind, text: text})});
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) {
      told.textContent = askKind !== 'lien' ? '✓ Demande envoyée : réponse sous 24 h'
        : d.auto ? '✓ Téléchargement lancé : la vidéo arrive dans vos Favoris'
        : '✓ Reçu : elle sera dans vos favoris sous 24 h';
      if (askKind === 'lien' && d.auto) watchLinks();
      told.className = 'told ok';
      $('askText').value = '';
      setTimeout(askClose, 1600);
    } else {
      told.textContent = d.error || 'L’envoi a échoué, réessayez.';
      told.className = 'told bad';
    }
  } catch (_) {
    told.textContent = 'Pas de connexion : réessayez dans un instant.';
    told.className = 'told bad';
  } finally {
    $('askGo').disabled = false;
    $('askGo').textContent = 'Envoyer';
  }
};

// -- le premier accueil : qui, quels gouts, comment participer, bravo --------
const LIKES = /*LIKES*/[];
const MAIL = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]{2,}$/;
let profile = null, seen = 0, goal = 100, minimum = 30, kept = true;
let prize = 20, friendGoal = 50, friendPrize = 10, sponsor = {};
// Les regles du serveur (`profils.py`) : objectifs, montants, parrainage.
function takeRules(d) {
  seen = d.seen || 0;
  goal = d.goal || goal;
  minimum = d.minimum || minimum;
  prize = d.prize || prize;
  friendGoal = d.friendGoal || friendGoal;
  friendPrize = d.friendPrize || friendPrize;
  if (d.sponsor) sponsor = d.sponsor;
}
let helloStep = 0, editing = false;
const picked = new Set();

const GIFT = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C9.5 3 12 8 12 8s2.5-5 4.5-5a2.5 2.5 0 0 1 0 5"/></svg>';
// Les recompenses, en une ligne fine au-dessus des dossiers.
function paintGift() {
  const bar = $('gift');
  bar.replaceChildren();
  if (!profile) { bar.hidden = true; return; }
  const icon = document.createElement('span');
  icon.className = 'gi';
  icon.innerHTML = GIFT;
  const text = document.createElement('span');
  const thin = document.createElement('span');
  thin.className = 'thin';
  const fill = document.createElement('i');
  fill.style.width = (profile.email ? Math.min(100, 100 * seen / goal) : 0) + '%';
  thin.appendChild(fill);
  const go = document.createElement('span');
  go.className = 'go';
  go.textContent = 'Récompenses ›';
  text.textContent = !profile.email ? prize + ' € offerts'
    : seen >= goal ? 'Bon d’achat en route' : seen + ' / ' + goal + ' vidéos';
  bar.append(icon, text, thin, go);
  bar.hidden = false;
}
$('gift').onclick = () => openPerks();

let giftAt = 0;
async function refreshGift() {
  if (!profile || Date.now() - giftAt < 20000) return;
  giftAt = Date.now();
  const d = await getJSON('/api/profil');
  if (d) { takeRules(d); paintGift(); }
}

// -- les recompenses : le bon d'achat, et le parrainage ------------------------
let shareBase = '';
function paintPerks() {
  const mail = profile && profile.email;
  $('perkTitle').textContent = 'Bon d’achat Amazon de ' + prize + ' €';
  $('perkSub').textContent = !mail
    ? 'Ajoutez votre email : il vous sera envoyé après ' + goal + ' vidéos regardées.'
    : seen >= goal ? 'Objectif atteint : il arrive à ' + mail + '.'
    : seen + ' / ' + goal + ' vidéos regardées — il arrivera à ' + mail + '.';
  $('perkBar').style.width = (mail ? Math.min(100, 100 * seen / goal) : 0) + '%';
  $('perkMail').hidden = !!mail;
  $('friendSub').textContent = friendPrize + ' € en bon d’achat Amazon pour chaque ami ' +
    'qui s’inscrit par votre lien et regarde ' + friendGoal + ' vidéos.' +
    (mail ? '' : ' Ajoutez votre email pour les recevoir.');
  $('friendCount').textContent = String(sponsor.friends || 0);
  $('friendEarned').textContent = ((sponsor.earned || 0) * friendPrize) + ' €';
  $('friendShare').disabled = !sponsor.code;
  paintLockRow();
}
async function openPerks() {
  $('friendTold').textContent = '';
  paintPerks();
  openSheet('perks');
  refreshGift().then(paintPerks);
  // L'adresse https, celle qui marche partout : demandee d'avance, le partage
  // doit partir au toucher.
  if (!shareBase) {
    const d = await getJSON('/api/install');
    shareBase = (d && d.secure && d.secure.startsWith('https://')) ? d.secure : location.origin;
  }
}
$('perksClose').onclick = () => closeSheet('perks');
$('perks').onclick = (e) => { if (e.target === $('perks')) closeSheet('perks'); };
$('perksEdit').onclick = () => { $('perks').hidden = true; openHello(0, true); };
$('perkMail').onclick = () => {
  $('perks').hidden = true;
  openHello(0, true);
  setTimeout(() => $('helloMail').focus(), 300);
};
$('friendShare').onclick = async () => {
  if (!sponsor.code) return;
  const url = (shareBase || location.origin) + '/entrer/' + encodeURIComponent(key) +
              '?parrain=' + sponsor.code;
  const told = $('friendTold');
  if (navigator.share) {
    try {
      await navigator.share({title: 'Prisme',
        text: 'Je t’invite sur Prisme : des vidéos sans pub, entre nous.', url});
      told.textContent = '✓ Lien partagé';
      return;
    } catch (e) {
      if (e && e.name === 'AbortError') return;
    }
  }
  try {
    await navigator.clipboard.writeText(url);
    told.textContent = '✓ Lien copié : collez-le dans un message';
  } catch (_) {
    told.textContent = url;
  }
};

function paintPicked() {
  const n = picked.size;
  $('helloPicked').textContent = n ? n + ' choisi' + (n > 1 ? 's' : '') + ' ✓'
                                   : 'Rien de choisi pour l’instant.';
}
function paintChips() {
  const box = $('helloChips');
  box.replaceChildren();
  for (const like of LIKES) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip' + (picked.has(like) ? ' on' : '');
    b.textContent = like;
    b.setAttribute('aria-pressed', String(picked.has(like)));
    b.onclick = () => {
      if (picked.has(like)) picked.delete(like); else picked.add(like);
      b.classList.toggle('on', picked.has(like));
      b.setAttribute('aria-pressed', String(picked.has(like)));
      paintPicked();
    };
    box.appendChild(b);
  }
  paintPicked();
}

function paintBravo() {
  const name = (profile && profile.name) || $('helloName').value.trim();
  const mail = (profile && profile.email) || '';
  $('helloBravo').textContent = 'Bravo ' + name + ', vous y êtes !';
  if (mail) {
    $('helloDeal').textContent = 'Vous faites maintenant partie de la communauté. ' +
      'Regardez ' + goal + ' vidéos sur Prisme (au moins ' + minimum + ' secondes ' +
      'chacune) : votre bon d’achat Amazon de ' + prize + ' € sera envoyé à ' + mail + '.';
    $('helloPrize').textContent = prize + ' €';
    $('helloMeter').hidden = false;
    $('helloSeen').textContent = seen + ' / ' + goal + ' vidéos';
    $('helloBar').style.width = '0';
    setTimeout(() => { $('helloBar').style.width = Math.max(3, Math.min(100, 100 * seen / goal)) + '%'; }, 250);
  } else {
    $('helloDeal').textContent = 'Vous faites maintenant partie de la communauté. ' +
      'Envie du bon d’achat Amazon de ' + prize + ' € ? Ajoutez votre email quand ' +
      'vous voulez, dans « Récompenses », en haut de la page.';
    $('helloMeter').hidden = true;
  }
  $('helloFriends').innerHTML = 'Et parrainez vos amis : <b>' + friendPrize +
    ' € pour chaque ami</b> qui regarde ' + friendGoal + ' vidéos. Votre lien est ' +
    'dans « Récompenses ».';
  $('helloInstall').hidden = installed || !key || !(android || apple);
}

function installDone() {
  $('helloInstallLead').textContent = '✓ C’est fait : l’icône Prisme est sur votre écran d’accueil.';
  $('helloInstallLead').hidden = false;
  for (const id of ['helloInstallGo', 'helloHow', 'helloOut']) $(id).hidden = true;
}
$('helloInstallGo').onclick = async () => {
  const result = await doInstall({go: $('helloInstallGo'), how: $('helloHow'),
                                  out: $('helloOut')});
  if (result === 'done') installDone();
  else if (result === 'again') $('helloInstallGo').textContent = 'Installer maintenant';
  else if (result === 'shown') {
    $('helloInstallGo').hidden = true;
    $('helloInstallLead').hidden = true;
  }
};

function helloShow(step) {
  helloStep = step;
  document.querySelectorAll('#hello section').forEach((el) => {
    el.hidden = Number(el.dataset.step) !== step;
  });
  $('helloDots').querySelectorAll('i').forEach((dot, i) => {
    dot.className = i === step ? 'on' : i < step ? 'done' : '';
  });
  $('helloBack').hidden = step === 0;
  $('helloNext').textContent = step === 5 ? 'C’est parti'
    : editing && step === 1 ? 'Enregistrer' : 'Continuer';
  $('hello').scrollTop = 0;
  if (step === 4) paintPinStep();
  if (step === 5) paintBravo();
}

function openHello(step, edit) {
  editing = !!edit;
  if (profile) {
    $('helloName').value = profile.name || '';
    $('helloMail').value = profile.email || '';
    picked.clear();
    for (const like of profile.likes || []) picked.add(like);
  }
  paintChips();
  $('helloErr').textContent = '';
  $('hello').hidden = false;
  document.body.classList.add('watching');
  helloShow(step);
  // « Retour » : l'etape d'avant (voir popstate). Venu d'une feuille, on
  // prend sa place dans l'historique.
  if (history.state && history.state.v === 'sheet') history.replaceState({v: 'hello'}, '');
  else if (!(history.state && history.state.v === 'hello')) history.pushState({v: 'hello'}, '');
}

function closeHello() {
  $('hello').hidden = true;
  if (history.state && history.state.v === 'hello') history.back();
  document.body.classList.remove('watching');
  try { localStorage.setItem('prisme-accueil', 'fait'); } catch (_) {}
}

function checkWho() {
  const name = $('helloName').value.trim(), mail = $('helloMail').value.trim();
  let trouble = '';
  if (!name) trouble = 'Indiquez un prénom ou un pseudo.';
  else if (mail && !MAIL.test(mail)) trouble = 'Cette adresse email ne semble pas valide.';
  $('helloErr').textContent = trouble;
  if (trouble) (name ? $('helloMail') : $('helloName')).focus();
  return !trouble;
}

async function saveProfile() {
  let parrain = '';
  try { parrain = localStorage.getItem('prisme-parrain') || ''; } catch (_) {}
  const told = {name: $('helloName').value.trim(), email: $('helloMail').value.trim(),
                likes: Array.from(picked), parrain};
  if (!kept) {
    // Ce serveur ne garde pas les profils : la page s'en souvient seule.
    profile = told;
    paintGift();
    return '';
  }
  try {
    const r = await fetch('/api/profil', {method: 'POST',
      headers: headers({'Content-Type': 'application/json'}), body: JSON.stringify(told)});
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) {
      profile = d.profile;
      takeRules(d);
      paintGift();
      loadForYou();
      return '';
    }
    return d.error || 'L’enregistrement a échoué, réessayez.';
  } catch (_) {
    return 'Pas de connexion : réessayez dans un instant.';
  }
}

$('helloNext').onclick = async () => {
  if (helloStep === 0) {
    if (checkWho()) helloShow(1);
    return;
  }
  if (helloStep === 1) {
    $('helloNext').disabled = true;
    const trouble = await saveProfile();
    $('helloNext').disabled = false;
    if (trouble) {
      helloShow(0);
      $('helloErr').textContent = trouble;
      return;
    }
    if (editing) { closeHello(); return; }
    helloShow(2);
    return;
  }
  if (helloStep < 5) { helloShow(helloStep + 1); return; }
  closeHello();
  setTimeout(coach, 350);
};
$('helloBack').onclick = () => helloShow(Math.max(0, helloStep - 1));
$('helloName').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); $('helloMail').focus(); }
});
$('helloMail').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); $('helloNext').click(); }
});

// -- les preferences : epinglees en haut, et leurs videos en premier ---------
const PIN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>';
function markPins() {
  $('pins').querySelectorAll('.pin').forEach((b) => {
    b.classList.toggle('on', shown.v === 'tag' && b.dataset.like === shown.like);
  });
}
function paintPins() {
  const box = $('pins');
  box.replaceChildren();
  if (!pinned.length) { box.hidden = true; return; }
  for (const like of pinned) {
    const b = document.createElement('button');
    b.className = 'pin';
    b.dataset.like = like;
    b.innerHTML = PIN;
    b.append(like);
    b.onclick = () => showTag(like, true);
    box.appendChild(b);
  }
  const edit = document.createElement('button');
  edit.className = 'pin edit';
  edit.textContent = 'Modifier';
  edit.onclick = () => openHello(1, true);
  box.appendChild(edit);
  box.hidden = false;
  markPins();
}
async function loadForYou() {
  pinned = (profile && profile.likes) || [];
  paintPins();
  if (!pinned.length) {
    forYouList = [];
    $('forYou').hidden = true;
    return;
  }
  const d = await getJSON('/api/pourvous');
  forYouList = (d && d.videos) || [];
  const strip = $('forYouStrip');
  strip.replaceChildren();
  forYouList.forEach((v, i) => strip.appendChild(card(videoCard(v, forYouList, i))));
  $('forYou').hidden = !(forYouList.length && (shown.v === 'folders' || shown.v === 'videos'));
}
// Une preference epinglee : toutes ses videos.
async function showTag(like, push) {
  const ticket = ++nav;
  if (asking) { asking.abort(); asking = null; }
  if (push) remember({v: 'tag', like});
  shown = {v: 'tag', like};
  $('q').value = '';
  mark('');
  const d = await getJSON('/api/tag?like=' + encodeURIComponent(like));
  if (ticket !== nav) return;
  const list = (d && d.videos) || [];
  showVideos(list, like + ' — ' + list.length + ' vidéo(s)');
  if (list.length < 6) showFew(like);
}

// -- le doigt sur « Demande », une fois l'accueil fini ----------------------
function coach() {
  scrollTo(0, 0);
  const spot = $('askBtn').getBoundingClientRect();
  if (!spot.width) return;
  const ring = $('coachRing'), bubble = $('coachBubble'), pad = 5;
  ring.style.left = (spot.left - pad) + 'px';
  ring.style.top = (spot.top - pad) + 'px';
  ring.style.width = (spot.width + 2 * pad) + 'px';
  ring.style.height = (spot.height + 2 * pad) + 'px';
  $('coach').hidden = false;
  const wide = bubble.offsetWidth;
  const left = Math.max(16, Math.min(innerWidth - wide - 16, spot.right - wide + 10));
  bubble.style.left = left + 'px';
  bubble.style.top = (spot.bottom + pad + 14) + 'px';
  bubble.style.setProperty('--tail',
    Math.max(14, left + wide - (spot.left + spot.width / 2) - 7) + 'px');
}
$('coachOk').onclick = () => { $('coach').hidden = true; };
$('coachRing').onclick = () => { $('coach').hidden = true; askOpen(); };
$('coach').onclick = (e) => { if (e.target === $('coach')) $('coach').hidden = true; };

// La premiere visite : l'accueil (l'icone y est proposee a la fin). Sinon,
// la question de l'icone, comme avant.
// -- le code de verrouillage, choisi par le visiteur : la discretion ----------
let pinPurpose = '', pinStage = '', pinTyped = '', pinOld = '', pinFirst = '';
let hiddenAt = 0, pinFlag = false;
try { pinFlag = localStorage.getItem('prisme-verrou') === '1'; } catch (_) {}
function setPinFlag(on) {
  pinFlag = on;
  try { localStorage.setItem('prisme-verrou', on ? '1' : ''); } catch (_) {}
}
// Le voile cache la page tant qu'on ne sait pas s'il faut un code. Sans code
// connu sur cet appareil, il se leve vite, quoi qu'il arrive.
if (!pinFlag) setTimeout(() => { if ($('lock').classList.contains('veil')) lockHide(); }, 4000);
const PIN_TEXT = {
  check: ['Prisme est verrouillé', 'Entrez votre code'],
  old: ['Code actuel', 'Entrez d’abord votre code actuel'],
  new: ['Nouveau code', 'Choisissez quatre chiffres'],
  confirm: ['Confirmez', 'Entrez-le une seconde fois'],
};
function paintPinDots() {
  $('pinDots').querySelectorAll('i').forEach((dot, i) => dot.classList.toggle('on', i < pinTyped.length));
}
function pinStageShow(stage, trouble) {
  pinStage = stage;
  pinTyped = '';
  $('lock').classList.remove('veil');
  $('lock').hidden = false;
  $('lockTitle').textContent = PIN_TEXT[stage][0];
  $('lockLead').textContent = PIN_TEXT[stage][1];
  $('lockErr').textContent = trouble || '';
  $('lockCancel').hidden = pinPurpose === 'unlock';
  $('lockForgot').hidden = pinPurpose !== 'unlock';
  paintPinDots();
}
function lockHide() {
  $('lock').hidden = true;
  $('lock').classList.remove('veil');
}
function lockApp() {
  if (!$('lock').hidden && pinPurpose === 'unlock') return;
  if (!video.paused) video.pause();
  pinPurpose = 'unlock';
  pinStageShow('check');
}
function pinSetup(purpose) {
  pinPurpose = purpose;
  pinOld = '';
  pinFirst = '';
  pinStageShow(profile && profile.pin ? 'old' : 'new');
}
async function pinPost(told) {
  try {
    const r = await fetch('/api/pin', {method: 'POST',
      headers: headers({'Content-Type': 'application/json'}), body: JSON.stringify(told)});
    return await r.json().catch(() => ({ok: false, error: 'Réponse illisible.'}));
  } catch (_) {
    return {ok: false, error: 'Pas de connexion : réessayez.'};
  }
}
function pinWrong(stage, trouble) {
  const dots = $('pinDots');
  dots.classList.remove('shake');
  void dots.offsetWidth;
  dots.classList.add('shake');
  if (navigator.vibrate) navigator.vibrate(80);
  pinStageShow(stage, trouble);
}
function pinChanged() {
  paintPinStep();
  paintLockRow();
}
async function pinDone() {
  const pin = pinTyped;
  if (pinStage === 'check') {
    const d = await pinPost({action: 'check', pin});
    if (d.ok) lockHide(); else pinWrong('check', d.error || 'Code incorrect.');
    return;
  }
  if (pinStage === 'old') {
    pinOld = pin;
    if (pinPurpose !== 'clear') { pinStageShow('new'); return; }
    const d = await pinPost({action: 'clear', old: pin});
    if (!d.ok) { pinWrong('old', d.error || 'Code incorrect.'); return; }
    profile.pin = false;
    setPinFlag(false);
    lockHide();
    pinChanged();
    return;
  }
  if (pinStage === 'new') { pinFirst = pin; pinStageShow('confirm'); return; }
  if (pin !== pinFirst) { pinWrong('new', 'Les deux codes diffèrent : recommencez.'); return; }
  const d = await pinPost({action: 'set', pin, old: pinOld});
  if (!d.ok) {
    pinWrong(profile && profile.pin ? 'old' : 'new', d.error || 'L’enregistrement a échoué.');
    return;
  }
  profile.pin = true;
  setPinFlag(true);
  lockHide();
  pinChanged();
}
function pinKey(digit) {
  if (pinTyped.length >= 4) return;
  pinTyped += digit;
  paintPinDots();
  if (pinTyped.length === 4) setTimeout(pinDone, 150);
}
$('pad').querySelectorAll('button').forEach((b) => {
  b.onclick = () => {
    if (b.dataset.k !== 'del') { pinKey(b.dataset.k); return; }
    pinTyped = pinTyped.slice(0, -1);
    paintPinDots();
  };
});
addEventListener('keydown', (e) => {
  if ($('lock').hidden) return;
  if (e.key.length === 1 && e.key >= '0' && e.key <= '9') pinKey(e.key);
  else if (e.key === 'Backspace') { pinTyped = pinTyped.slice(0, -1); paintPinDots(); }
  e.stopPropagation();
}, true);
document.querySelector('.lockicon').addEventListener('error', (e) => { e.target.hidden = true; });
$('lockCancel').onclick = lockHide;
$('lockForgot').onclick = () => {
  $('lockErr').textContent = 'Demandez à la personne qui vous a invité de retirer votre code.';
};
// Revenu apres une minute ailleurs : le code, de nouveau.
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { hiddenAt = Date.now(); return; }
  if (profile && profile.pin && hiddenAt && Date.now() - hiddenAt > 60000) lockApp();
});
function paintPinStep() {
  const on = !!(profile && profile.pin);
  $('helloPin').textContent = on ? 'Changer mon code' : 'Choisir mon code';
  $('helloPinState').textContent = on ? '✓ Code activé : Prisme le demandera à chaque ouverture.' : '';
  if (helloStep === 4 && !$('hello').hidden) $('helloNext').textContent = on ? 'Continuer' : 'Plus tard';
}
function paintLockRow() {
  const on = !!(profile && profile.pin);
  $('lockState').textContent = on ? 'Activé : demandé à chaque ouverture.'
                                  : 'Un code à 4 chiffres, pour la discrétion.';
  $('lockSet').textContent = on ? 'Changer le code' : 'Choisir un code';
  $('lockClear').hidden = !on;
}
$('helloPin').onclick = () => pinSetup('set');
$('lockSet').onclick = () => pinSetup('set');
$('lockClear').onclick = () => pinSetup('clear');

async function welcome() {
  const d = await getJSON('/api/profil?ouverture=1');
  if (!d) { lockHide(); return; }
  profile = d.profile;
  seen = d.seen || 0;
  goal = d.goal || goal;
  takeRules(d);
  kept = d.kept !== false;
  let done = '';
  try { done = localStorage.getItem('prisme-accueil') || ''; } catch (_) {}
  if (profile) {
    try { localStorage.setItem('prisme-accueil', 'fait'); } catch (_) {}
  }
  if (profile && profile.pin) {
    setPinFlag(true);
    lockApp();
  } else {
    if (pinFlag) setPinFlag(false);
    lockHide();
  }
  paintGift();
  loadForYou();
  if (!profile && !(done === 'fait' && !kept)) {
    openHello(0);
    // Le clavier d'un telephone cacherait l'explication : seulement au PC.
    if (!android && !apple) setTimeout(() => $('helloName').focus(), 400);
    return;
  }
  // Arrive du lien Wi-Fi ou d'une autre appli pour poser l'icone : la
  // question tout de suite (l'installation, elle, attend toujours un toucher).
  setTimeout(askInstall, cameToInstall ? 300 : 1200);
}
welcome();
"""


# Les preferences proposees au premier accueil (`profils.LIKES`).
APP_SCRIPT = APP_SCRIPT.replace(
    "/*LIKES*/[]", json.dumps(list(profils.LIKES), ensure_ascii=False))

# L'application sur l'ecran d'accueil : un nom, des icones, plein ecran.
MANIFEST = {
    "name": "Prisme", "short_name": "Prisme", "start_url": "/", "scope": "/",
    "display": "standalone", "background_color": "#0b0d10", "theme_color": "#0e1116",
    "icons": [{"src": "/icon-192.png?v=2", "sizes": "192x192", "type": "image/png"},
              {"src": "/icon-512.png?v=2", "sizes": "512x512", "type": "image/png"}],
}
ICON_ROUTES = {"/icon-192.png": 192, "/icon-512.png": 512, "/apple-touch-icon.png": 180,
               "/favicon.ico": 192}
# Le lettrage PRISME (brand_data) ; et la version du logo, jointe aux adresses
# des icones : gardees une semaine par les telephones, elles ne changeaient
# pas quand le logo changeait.
BRAND_ROUTES = {"/lettrage.png": LETTRAGE_PNG}
LOGO_VERSION = "2"

# Il ne garde rien et ne touche pas aux videos (lecture par morceaux) : il est
# la parce que certains Chrome n'installent une page qu'avec lui.
SERVICE_WORKER = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET' || request.headers.has('range') ||
      request.destination === 'video' || request.destination === 'audio') return;
  event.respondWith(fetch(request));
});
"""


def _digest(text: str) -> str:
    return "'sha256-" + base64.b64encode(
        hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


# Rien d'autre que ces trois textes ne s'execute ni ne s'applique ; images,
# videos et requetes ne vont qu'au serveur lui-meme.
CSP = ("default-src 'none'; "
       f"script-src {_digest(APP_SCRIPT)} {_digest(LOGIN_SCRIPT)}; "
       f"style-src {_digest(STYLE)}; "
       "img-src 'self'; media-src 'self'; connect-src 'self'; manifest-src 'self'; "
       "worker-src 'self'; "
       "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")

LOGIN_PAGE = f"""<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png?v={LOGO_VERSION}">
<title>Prisme</title><style>{STYLE}</style>
<form class="login" method="post" action="/login">
  <h1><img src="/lettrage.png" alt="Prisme" class="wordmark big"></h1>
  <div class="dim">Cette bibliothèque est privée.</div>
  <input type="password" name="password" placeholder="Mot de passe"
         autocomplete="current-password" autofocus required>
  <button type="submit">Entrer</button>
  <div class="err" id="err"></div>
</form>
<script>{LOGIN_SCRIPT}</script></html>"""

APP_PAGE = f"""<!doctype html><html lang="fr"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/apple-touch-icon.png?v={LOGO_VERSION}">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png?v={LOGO_VERSION}">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Prisme">
<meta name="apple-mobile-web-app-status-bar-style" content="black">
<meta name="theme-color" content="#0e1116">
<title>Prisme</title><style>{STYLE}</style>
<header>
  <div class="top">
    <button id="home" class="brand" title="Tous les dossiers" aria-label="Prisme"><img
      src="/icon-192.png?v={LOGO_VERSION}" alt="Prisme" class="logo"></button>
    <div class="acts">
      <button id="tabFolders" class="tab on">Dossiers</button>
      <button id="tabVideos" class="tab">Vidéos</button>
      <button id="tabFavs" class="tab">Favoris</button>
    </div>
  </div>
  <div class="searchrow">
    <div class="qbox">
      <input type="search" id="q" placeholder="Chercher un dossier, une vidéo…"
             enterkeyhint="search" autocomplete="off">
      <button id="qGo" class="qgo" title="Rechercher" aria-label="Rechercher"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/></svg></button>
    </div>
    <button id="askBtn" class="ask" title="Envoyer une demande"
            aria-label="Envoyer une demande"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 21 23 12 2 3v7l15 2-15 2z"/></svg><span>Demande</span></button>
  </div>
</header>
<main>
  <div class="few" id="outApp" hidden><span class="perk-ic sm alt line"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg></span><div>
    <b id="outAppTitle">Prisme s’affiche mieux dans Chrome</b>Cette page est ouverte
    dans le navigateur d’une autre application, en plus petit.
    <a id="outAppGo" role="button" tabindex="0">Ouvrir dans Chrome</a></div></div>
  <button class="perkline" id="gift" hidden></button>
  <div class="pins" id="pins" hidden></div>
  <div id="forYou" hidden><div class="section">Pour vous</div>
    <div class="strip" id="forYouStrip"></div></div>
  <div class="crumb" id="crumb"></div>
  <div class="section" id="hitsHead" hidden>Dossiers</div>
  <div class="grid" id="hits" hidden></div>
  <div class="section" id="videosHead" hidden>Vidéos</div>
  <div class="grid" id="folders"></div>
  <div class="grid" id="grid" hidden></div>
  <div class="few" id="few" hidden><span class="perk-ic sm alt"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 21 23 12 2 3v7l15 2-15 2z"/></svg></span><div>
    <b>La collection s’enrichit de jour en jour</b>N’hésitez pas à
    <a id="fewAsk" role="button" tabindex="0">envoyer une demande</a> pour dire quel
    contenu vous aimeriez voir sur Prisme.</div></div>
  <div class="empty" id="empty" hidden></div>
  <div id="more"></div>
</main>
<div id="install" hidden>
  <div class="sheet" role="dialog" aria-labelledby="installTitle">
    <img src="/icon-192.png?v={LOGO_VERSION}" alt="" class="appicon">
    <h2 id="installTitle">Ajouter Prisme à votre écran d’accueil ?</h2>
    <p id="installHow">Une icône comme une vraie application : Prisme s’ouvre en
      plein écran, sans barre d’adresse, en un geste.</p>
    <a class="btn" id="installOut" hidden></a>
    <label class="never" id="installNeverRow"><input type="checkbox" id="installNever">
      Ne plus me le demander</label>
    <div class="row">
      <button id="installNo" class="quiet">Non</button>
      <button id="installYes" class="go">Oui</button>
    </div>
  </div>
</div>
<div id="ask" hidden>
  <div class="sheet" role="dialog" aria-labelledby="askTitle">
    <h2 id="askTitle">Envoyer une demande</h2>
    <div class="kinds" id="askKinds">
      <button data-kind="demande" class="kind on">Faire une demande</button>
      <button data-kind="lien" class="kind">Envoyer un lien</button>
    </div>
    <textarea id="askText" maxlength="500" rows="3"
      placeholder="Un titre, un style, une actrice, une envie… Réponse sous 24 h."></textarea>
    <div class="count"></div>
    <div class="row">
      <button id="askNo" class="quiet">Annuler</button>
      <button id="askGo" class="go">Envoyer</button>
    </div>
    <div class="told" id="askTold" aria-live="polite"></div>
  </div>
</div>
<div id="perks" hidden>
  <div class="sheet" role="dialog" aria-labelledby="perksTitle">
    <h2 id="perksTitle">Vos récompenses</h2>
    <div class="reward gold">
      <div class="reward-head"><span class="perk-ic sm"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C9.5 3 12 8 12 8s2.5-5 4.5-5a2.5 2.5 0 0 1 0 5"/></svg></span><div>
        <b id="perkTitle">Bon d’achat Amazon de 20&nbsp;€</b><small id="perkSub"></small></div></div>
      <div class="meter"><i id="perkBar"></i></div>
      <button class="textlink" id="perkMail" hidden>Ajouter mon email</button>
    </div>
    <div class="reward">
      <div class="reward-head"><span class="perk-ic sm alt"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg></span><div>
        <b>Parrainez vos amis</b><small id="friendSub"></small></div></div>
      <div class="stats"><span><b id="friendCount">0</b>ami(s) inscrit(s)</span>
        <span><b id="friendEarned">0&nbsp;€</b>gagnés</span></div>
      <button class="wide go" id="friendShare">Partager mon lien</button>
      <div class="told" id="friendTold" aria-live="polite"></div>
    </div>
    <div class="reward">
      <div class="reward-head"><span class="perk-ic sm alt line"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></span><div>
        <b>Code de verrouillage</b><small id="lockState"></small></div></div>
      <div class="row2"><button class="wide go" id="lockSet">Choisir un code</button>
        <button class="wide soft" id="lockClear" hidden>Retirer</button></div>
    </div>
    <div class="foot">
      <button class="textlink" id="perksEdit">Modifier mon profil</button>
      <button class="textlink" id="perksClose">Fermer</button>
    </div>
  </div>
</div>
<div id="hello" hidden>
  <div class="hello-in">
    <div class="dots" id="helloDots" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i></div>
    <section data-step="0">
      <img src="/lettrage.png" alt="Prisme" class="wordmark big">
      <h2>Bienvenue&nbsp;!</h2>
      <p class="lead">Faisons connaissance : trente secondes, et Prisme est à vous.</p>
      <label class="field"><span>Prénom ou pseudo</span>
        <input id="helloName" maxlength="40" autocomplete="nickname" autocapitalize="words"
               enterkeyhint="next" placeholder="Comment doit-on vous appeler ?"></label>
      <label class="field"><span>Adresse email <em>· facultatif</em></span>
        <input id="helloMail" type="email" inputmode="email" maxlength="120"
               autocomplete="email" enterkeyhint="next" placeholder="vous@exemple.fr"></label>
      <div class="perk"><span class="perk-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C9.5 3 12 8 12 8s2.5-5 4.5-5a2.5 2.5 0 0 1 0 5"/></svg></span><span>
        <b>20&nbsp;€ offerts sur Amazon</b>
        <small>Votre bon d’achat arrivera à cette adresse — et rien d’autre.</small></span></div>
      <div class="err" id="helloErr" aria-live="polite"></div>
    </section>
    <section data-step="1" hidden>
      <h2>Vos préférences</h2>
      <p class="lead">Touchez tout ce qui vous plaît. Vos choix seront épinglés en haut
        de Prisme, et les vidéos qui leur correspondent vous seront proposées en
        premier.</p>
      <div class="picked" id="helloPicked">Rien de choisi pour l’instant.</div>
      <div class="chips" id="helloChips"></div>
    </section>
    <section data-step="2" hidden>
      <h2>Une envie précise&nbsp;?</h2>
      <p class="lead">Tout passe par ce bouton, en haut de la page :</p>
      <div class="demo"><span class="ask"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 21 23 12 2 3v7l15 2-15 2z"/></svg>Demande</span></div>
      <div class="tips">
        <div class="tip"><span class="em">🎯</span><div><b>Des vidéos en particulier</b>
          <p>Décrivez-les (un titre, un style, une actrice…) : elles s’ajoutent
          sous 24&nbsp;heures.</p></div></div>
        <div class="tip"><span class="em">🔗</span><div><b>Une vidéo trouvée sur le net</b>
          <p>Copiez son lien et collez-le dans « Demande » : elle s’ajoute à vos
          favoris sous 24&nbsp;heures.</p></div></div>
      </div>
    </section>
    <section data-step="3" hidden>
      <h2>Bienvenue dans la communauté Prisme</h2>
      <p class="lead">Prisme est un site collaboratif : chacun y apporte ses vidéos, et tout
        le monde en profite.</p>
      <div class="tips">
        <div class="tip"><span class="em">🚫</span><div><b>Sans pub, sans rien</b>
          <p>Pas de publicité, pas de traqueur publicitaire, pas de fenêtre qui s’ouvre
          toute seule.</p></div></div>
        <div class="tip"><span class="em">🔒</span><div><b>En toute sécurité</b>
          <p>Les vidéos restent sur un serveur privé, jointes par une connexion
          chiffrée.</p></div></div>
        <div class="tip"><span class="em">🤝</span><div><b>Vous aussi, participez</b>
          <p>Demandez des vidéos, ou envoyez vos préférées : elles s’ajoutent pour tous,
          et dans vos favoris.</p></div></div>
      </div>
    </section>
    <section data-step="4" hidden>
      <div class="center"><span class="perk-ic alt line lockbig"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="11" x="3" y="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg></span></div>
      <h2 class="center">Discrétion</h2>
      <p class="lead center">Protégez Prisme par un code à 4 chiffres, que vous
        choisissez : il sera demandé à chaque ouverture, et quand vous revenez après
        une minute d’absence.</p>
      <button class="wide go" id="helloPin">Choisir mon code</button>
      <p class="aside" id="helloPinState"></p>
    </section>
    <section data-step="5" hidden>
      <div class="party">🎉</div>
      <h2 class="center" id="helloBravo">Bravo, vous y êtes&nbsp;!</h2>
      <p class="lead center" id="helloDeal"></p>
      <div id="helloMeter" hidden>
        <div class="meter"><i id="helloBar"></i></div>
        <div class="meterline"><span id="helloSeen">0 vidéo</span><span class="gold"
          id="helloPrize">20&nbsp;€</span></div>
      </div>
      <p class="aside" id="helloFriends"></p>
      <div class="card2" id="helloInstall" hidden>
        <h3>📲 Prisme sur votre écran d’accueil</h3>
        <p id="helloInstallLead">Une icône comme une vraie application : Prisme s’ouvre
          en plein écran, sans barre d’adresse.</p>
        <div class="how" id="helloHow" hidden></div>
        <button class="wide go" id="helloInstallGo">Ajouter l’icône</button>
        <a class="btn" id="helloOut" hidden></a>
      </div>
    </section>
    <div class="hello-nav">
      <button id="helloBack" class="quiet" hidden>Retour</button>
      <button id="helloNext" class="go">Continuer</button>
    </div>
  </div>
</div>
<div id="coach" hidden>
  <div id="coachRing"></div>
  <div id="coachBubble" role="dialog"><b>C’est ici&nbsp;!</b>Une envie de vidéo, ou un lien
    trouvé sur le net : touchez « Demande ».<button id="coachOk">Compris</button></div>
</div>
<div id="lock" class="veil">
  <div class="lock-in" role="dialog" aria-labelledby="lockTitle">
    <img src="/icon-192.png?v={LOGO_VERSION}" alt="" class="lockicon">
    <h2 id="lockTitle">Prisme est verrouillé</h2>
    <p id="lockLead">Entrez votre code</p>
    <div class="pindots" id="pinDots"><i></i><i></i><i></i><i></i></div>
    <div class="err" id="lockErr" aria-live="polite"></div>
    <div class="pad" id="pad">
      <button data-k="1">1</button><button data-k="2">2</button><button data-k="3">3</button>
      <button data-k="4">4</button><button data-k="5">5</button><button data-k="6">6</button>
      <button data-k="7">7</button><button data-k="8">8</button><button data-k="9">9</button>
      <button data-k="0" class="k0">0</button><button data-k="del" class="del"
        aria-label="Effacer">⌫</button>
    </div>
    <button class="textlink" id="lockCancel" hidden>Annuler</button>
    <button class="textlink" id="lockForgot">Code oublié ?</button>
  </div>
</div>
<div id="player">
  <div id="stage">
    <video id="video" playsinline preload="metadata" disablepictureinpicture
           controlslist="nodownload noplaybackrate"></video>
    <div id="obot">
      <button class="ic" id="mute" title="Couper le son"><svg viewBox="0 0 24 24" id="iSound" aria-hidden="true"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6z"/></svg><svg viewBox="0 0 24 24" id="iMuted" aria-hidden="true"><path d="M16.5 12A4.5 4.5 0 0 0 14 8v2.2l2.4 2.4c.1-.2.1-.4.1-.6zM19 12c0 .9-.2 1.8-.5 2.6l1.5 1.5A9 9 0 0 0 14 3.2v2.1A7 7 0 0 1 19 12zM4.3 3 3 4.3 7.7 9H3v6h4l5 5v-6.7l4.3 4.3a7 7 0 0 1-2.3 1.2v2.1a9 9 0 0 0 3.7-1.8l2 2 1.3-1.3L4.3 3zM12 4 9.9 6.1 12 8.2V4z"/></svg></button>
      <button class="ic" id="prev" title="Précédente"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg></button>
      <span id="now">0:00</span>
      <input type="range" id="seek" min="0" max="1000" value="0" step="1"
             aria-label="Avancer dans la vidéo">
      <span id="total">0:00</span>
      <button class="ic" id="next" title="Suivante"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M16 6h2v12h-2zM6 18l8.5-6L6 6z"/></svg></button>
      <button class="ic" id="fav" title="Favori"><svg viewBox='0 0 24 24' id='iStar' aria-hidden='true'><path d="m22 9.2-7.2-.6L12 2 9.2 8.6 2 9.2 7.4 14l-1.6 7L12 17.3 18.2 21l-1.6-7L22 9.2zM12 15.4l-3.8 2.3 1-4.3-3.3-2.9 4.4-.4L12 6.1l1.7 4 4.4.4-3.3 2.9 1 4.3-3.8-2.3z"/></svg><svg viewBox='0 0 24 24' id='iStarOn' aria-hidden='true'><path d="M12 17.3 18.2 21l-1.6-7L22 9.2l-7.2-.6L12 2 9.2 8.6 2 9.2 7.4 14l-1.6 7z"/></svg></button>
      <button class="ic" id="full" title="Plein écran"><svg viewBox="0 0 24 24" id="iFull" aria-hidden="true"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg><svg viewBox="0 0 24 24" id="iUnfull" aria-hidden="true"><path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg></button>
    </div>
    <div id="flash"></div>
  </div>
</div>
<script>{APP_SCRIPT}</script></html>"""
