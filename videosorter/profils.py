"""Les profils de la page mobile : un prénom, un email, des préférences.

La première visite demande qui l'on est (`/api/profil`) ; le serveur qui sert
la page range les réponses dans `profils.json`, à côté des demandes -- sur le
PC, ou dans le partage quand c'est le NAS qui sert. Chaque appareil se
présente par un identifiant tiré au hasard (« moi »), gardé par la page et
joint à l'adresse de l'icône : l'application posée sur l'écran d'accueil, qui
ne partage pas la mémoire du navigateur sur iPhone, se reconnaît ainsi.

Le parrainage : chaque profil a son code, joint au lien qu'il partage
(`/entrer/<clé>?parrain=<code>`). Le nouveau venu qui s'inscrit par ce lien
devient son filleul ; quand le filleul a regardé `FRIEND_GOAL` vidéos, le
parrain gagne `FRIEND_PRIZE` euros.

Les nouveaux inscrits, et les bons d'achat à envoyer (le sien, ou celui d'un
parrain), arrivent dans « Demandes reçues » : c'est là que Prisme les montre.

Sans Qt : le NAS s'en sert aussi.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from . import demandes

FILE_NAME = "profils.json"
# Le bon d'achat : tant de vidéos regardées, chacune au moins tant de secondes.
GOAL = 100
MIN_SECONDS = 30
PRIZE = 20
# Le parrainage : le filleul regarde tant de vidéos, le parrain gagne tant.
FRIEND_GOAL = 50
FRIEND_PRIZE = 10
NAME_MAX = 40
MAIL_MAX = 120

# Les préférences proposées, et les mots qui les trouvent dans les noms de
# fichiers (français et anglais, sans accents ni majuscules : la recherche les
# replie). Une préférence n'est pas un mot-clé : c'est une famille de mots.
# Rien de trop court : « bi » ou « cam » se trouvent au milieu de n'importe
# quel nom. La page n'envoie que ces préférences, et le serveur ne garde
# qu'elles.
LIKE_TERMS = {
    "Amour": ("amour", "love", "passion"),
    "Couple": ("couple",),
    "Romantique": ("romanti",),
    "Sensuel": ("sensu", "erotic", "erotique"),
    "Amateur": ("amateur", "homemade"),
    "Masturbation": ("masturb", "solo"),
    "Lesbienne": ("lesb",),
    "Gay": ("gay",),
    "Bi": ("bisex",),
    "Trans": ("transex", "transgen", "tgirl"),
    "Hardcore": ("hardcore",),
    "Soft": ("softcore", "soft"),
    "Soumission": ("soumi", "submiss", "slave"),
    "Domination": ("domina", "femdom", "maitresse"),
    "BDSM": ("bdsm",),
    "Bondage": ("bondage", "shibari", "attache"),
    "Fétichisme": ("fetis", "fetich", "fetish"),
    "Pieds": ("pieds", "feet", "foot"),
    "Lingerie": ("lingerie", "stocking", "bas nylon", "nylon"),
    "Jeux de rôle": ("roleplay", "role play", "jeu de role"),
    "Pissing": ("pissing", "piss", "golden shower"),
    "Squirt": ("squirt", "fontaine"),
    "Anal": ("anal", "sodom"),
    "Oral": ("oral", "blowjob", "fellation", "cunni", "pipe"),
    "Plan à trois": ("threesome", "trio", "plan a trois", "ffm", "mmf"),
    "Gang bang": ("gangbang", "gang bang"),
    "Échangisme": ("echangis", "swinger", "libertin"),
    "Exhibition": ("exhib", "public"),
    "Voyeur": ("voyeur", "hidden cam"),
    "Extérieur": ("outdoor", "exterieur", "plage", "beach", "foret"),
    "POV": ("pov",),
    "Massage": ("massage",),
    "Jouets": ("toy", "dildo", "vibro", "gode", "sextoy"),
    "MILF": ("milf",),
    "Mature": ("mature",),
    "Rondes": ("bbw", "curvy", "ronde", "chubby"),
    "Gros seins": ("busty", "big tits", "bigtits", "boobs", "gros seins"),
    "Poilu·e·s": ("hairy", "poilu"),
    "Black": ("ebony", "black"),
    "Asiatique": ("asian", "asiat", "japan", "japon"),
    "Latina": ("latina",),
    "Vintage": ("vintage", "retro"),
    "Cosplay": ("cosplay",),
    "Webcam": ("webcam",),
}
LIKES = tuple(LIKE_TERMS)


def like_query(like: str) -> str:
    """La recherche d'une préférence : ses mots, l'un ou l'autre."""
    return " or ".join(f'"{term}"' if " " in term else term
                       for term in LIKE_TERMS.get(like, ()))

ME = re.compile(r"[0-9a-f]{32}")
# Le code de verrouillage choisi par le visiteur : quatre chiffres, gardes
# haches (jamais en clair), et des essais comptes.
PIN = re.compile(r"[0-9]{4}")
PIN_ROUNDS = 60_000
PIN_FREE_TRIES = 5
PIN_WAIT = 30.0
PIN_WAIT_MAX = 900.0
_pin_fails: dict = {}
CODE = re.compile(r"[a-z0-9]{8}")
CODE_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"
MAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}")

_lock = threading.Lock()
# Le compteur du bon n'est recalculé qu'une fois par minute et par appareil :
# le battement « je regarde » arrive toutes les dix secondes.
_checked: dict = {}
CHECK_EVERY = 60.0


def path_for(requests_path) -> Path | None:
    """Le fichier des profils : à côté de celui des demandes."""
    return Path(requests_path).with_name(FILE_NAME) if requests_path else None


def valid_me(me: str) -> str:
    me = str(me or "").strip().lower()
    return me if ME.fullmatch(me) else ""


def valid_code(code: str) -> str:
    code = str(code or "").strip().lower()
    return code if CODE.fullmatch(code) else ""


def _new_code(profiles: dict) -> str:
    taken = {one.get("code") for one in profiles.values() if isinstance(one, dict)}
    while True:
        code = "".join(secrets.choice(CODE_LETTERS) for _ in range(8))
        if code not in taken:
            return code


def _read(path: Path) -> dict:
    try:
        told = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return told if isinstance(told, dict) else {}


def _write(path: Path, profiles: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        spare = path.with_name(path.name + ".tmp")
        spare.write_text(json.dumps(profiles, ensure_ascii=False), encoding="utf-8")
        os.replace(spare, path)
        return True
    except OSError:
        return False


def find(path, me: str, label: str) -> dict | None:
    """Le profil de cet appareil : par son identifiant, sinon par son nom
    d'appareil (une page ouverte avant que l'identifiant existe)."""
    if path is None:
        return None
    profiles = _read(Path(path))
    if me and isinstance(profiles.get(me), dict):
        return profiles[me]
    for profile in profiles.values():
        if isinstance(profile, dict) and label in (profile.get("labels") or []):
            return profile
    return None


def public(profile: dict | None) -> dict | None:
    """Ce que la page a le droit de relire : rien d'autre que ce qu'elle a dit."""
    if not profile:
        return None
    return {"name": profile.get("name", ""), "email": profile.get("email", ""),
            "likes": list(profile.get("likes") or []),
            "pin": bool(profile.get("pin_hash"))}


def save(path, requests_path, me: str, label: str, told: dict) -> dict:
    """Range le profil envoyé par la page. Rend {"ok": True, "profile": …}
    ou {"ok": False, "error": …}."""
    if path is None:
        return {"ok": False, "error": "Les profils ne sont pas reçus ici."}
    me = valid_me(me)
    if not me:
        return {"ok": False, "error": "Appareil inconnu : rechargez la page."}
    name = " ".join(str(told.get("name") or "").split())[:NAME_MAX]
    email = str(told.get("email") or "").strip()[:MAIL_MAX]
    if not name:
        return {"ok": False, "error": "Indiquez un prénom ou un pseudo."}
    if email and not MAIL.fullmatch(email):
        return {"ok": False, "error": "Cette adresse email ne semble pas valide."}
    asked = told.get("likes") if isinstance(told.get("likes"), list) else []
    likes = [like for like in LIKES if like in {str(one) for one in asked}]
    now = time.time()
    with _lock:
        profiles = _read(Path(path))
        before = profiles.get(me) if isinstance(profiles.get(me), dict) else {}
        labels = list(before.get("labels") or [])
        if label and label not in labels:
            labels.append(label)
        profile = dict(before, name=name, email=email, likes=likes,
                       labels=labels[-20:], at=before.get("at") or now, updated=now)
        if not profile.get("code"):
            profile["code"] = _new_code(profiles)
        # Venu par le lien d'un ami : son parrain, une fois pour toutes.
        code = valid_code(told.get("parrain"))
        if not before and code:
            for key, other in profiles.items():
                if key != me and isinstance(other, dict) and other.get("code") == code:
                    profile["parrain"] = key
                    break
        profiles[me] = profile
        if not _write(Path(path), profiles):
            return {"ok": False, "error": "Impossible d'enregistrer le profil."}
    # Prisme le montre dans « Demandes reçues » : un nouvel inscrit, ou une
    # adresse qui arrive (c'est elle qui sert au bon d'achat).
    if not before or (email and email != before.get("email")):
        text = name + (f" · {email}" if email else "") + (
            " — aime : " + ", ".join(likes) if likes else "")
        demandes.append(requests_path, label, "inscription", text, system=True)
    return {"ok": True, "profile": public(profile)}


def sponsorship(path, me: str) -> dict:
    """Le code de parrainage de cet appareil, ses filleuls, ses primes."""
    me = valid_me(me)
    if path is None or not me:
        return {}
    with _lock:
        profiles = _read(Path(path))
        profile = profiles.get(me)
        if not isinstance(profile, dict):
            return {}
        if not profile.get("code"):
            # Un profil d'avant le parrainage : son code, maintenant.
            profile["code"] = _new_code(profiles)
            _write(Path(path), profiles)
    friends = [one for one in profiles.values()
               if isinstance(one, dict) and one.get("parrain") == me]
    return {"code": profile["code"], "friends": len(friends),
            "earned": sum(1 for one in friends if one.get("parrain_bon_at"))}


def attach(path, me: str, label: str) -> None:
    """Ajoute ce nom d'appareil au profil (le navigateur s'est mis à jour,
    l'icône de l'écran d'accueil se présente autrement) : le compteur du bon
    suit la personne, pas son navigateur."""
    me = valid_me(me)
    if path is None or not me or not label:
        return
    with _lock:
        profiles = _read(Path(path))
        profile = profiles.get(me)
        if not isinstance(profile, dict) or label in (profile.get("labels") or []):
            return
        profile["labels"] = (list(profile.get("labels") or []) + [label])[-20:]
        _write(Path(path), profiles)


def _pin_hash(salt: str, pin: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("ascii"), bytes.fromhex(salt),
                               PIN_ROUNDS).hex()


def _pin_wait(me: str) -> int:
    """Les secondes a attendre avant un nouvel essai, 0 si l'on peut."""
    fails, until = _pin_fails.get(me, (0, 0.0))
    return max(0, int(until - time.time()) + 1) if until > time.time() else 0


def _pin_note(me: str, ok: bool) -> None:
    if ok:
        _pin_fails.pop(me, None)
        return
    fails = _pin_fails.get(me, (0, 0.0))[0] + 1
    until = 0.0
    if fails >= PIN_FREE_TRIES:
        until = time.time() + min(PIN_WAIT * 2 ** (fails - PIN_FREE_TRIES), PIN_WAIT_MAX)
    _pin_fails[me] = (fails, until)


def _key(profiles: dict, me: str, label: str) -> str:
    """La cle du profil de cet appareil : son identifiant, sinon le profil qui
    connait deja ce nom d'appareil."""
    if isinstance(profiles.get(me), dict):
        return me
    for key, profile in profiles.items():
        if isinstance(profile, dict) and label in (profile.get("labels") or []):
            return key
    return ""


def pin_action(path, me: str, label: str, told: dict) -> dict:
    """Le code de verrouillage : « check » (ouvrir), « set » (choisir ou
    changer, l'ancien exige), « clear » (retirer, l'ancien exige)."""
    me = valid_me(me)
    if path is None or not me:
        return {"ok": False, "error": "Profil inconnu : rechargez la page."}
    action = str(told.get("action") or "")
    pin, old = str(told.get("pin") or ""), str(told.get("old") or "")
    with _lock:
        profiles = _read(Path(path))
        key = _key(profiles, me, label)
        if not key:
            return {"ok": False, "error": "Profil inconnu : rechargez la page."}
        profile = profiles[key]
        has = bool(profile.get("pin_hash"))
        # Ouvrir, changer, retirer : l'actuel d'abord, essais comptes.
        if has and action in ("check", "set", "clear"):
            given = pin if action == "check" else old
            wait = _pin_wait(key)
            if wait:
                return {"ok": False, "wait": wait,
                        "error": f"Trop d'essais : réessayez dans {wait} s."}
            right = bool(PIN.fullmatch(given)) and hmac.compare_digest(
                _pin_hash(profile.get("pin_salt", ""), given), profile["pin_hash"])
            _pin_note(key, right)
            if not right:
                wait = _pin_wait(key)
                return {"ok": False, "wait": wait, "error": "Code incorrect." + (
                    f" Réessayez dans {wait} s." if wait else "")}
        if action == "check":
            return {"ok": True}
        if action == "set":
            if not PIN.fullmatch(pin):
                return {"ok": False, "error": "Le code fait quatre chiffres."}
            salt = secrets.token_hex(16)
            profile["pin_salt"], profile["pin_hash"] = salt, _pin_hash(salt, pin)
        elif action == "clear":
            profile.pop("pin_salt", None)
            profile.pop("pin_hash", None)
        else:
            return {"ok": False, "error": "Demande inconnue."}
        if not _write(Path(path), profiles):
            return {"ok": False, "error": "Impossible d'enregistrer le code."}
    return {"ok": True, "pin": action == "set"}


def labels_of(profile: dict | None, label: str) -> list:
    labels = list((profile or {}).get("labels") or [])
    if label and label not in labels:
        labels.append(label)
    return labels


def reward(path, requests_path, me: str, label: str, count) -> None:
    """Les objectifs atteints, signalés une seule fois dans « Demandes
    reçues » : le bon de cet appareil (s'il a donné son adresse), et la prime
    de son parrain (dès que le parrain en a une). `count(labels)` compte les
    vidéos vues."""
    me = valid_me(me)
    if path is None or not me:
        return
    now = time.time()
    with _lock:
        if now - _checked.get(me, 0.0) < CHECK_EVERY:
            return
        _checked[me] = now
        profile = _read(Path(path)).get(me)
    if not isinstance(profile, dict):
        return
    own = bool(profile.get("email")) and not profile.get("bon_at")
    sponsor = bool(profile.get("parrain")) and not profile.get("parrain_bon_at")
    if not own and not sponsor:
        return
    seen = count(labels_of(profile, label))
    notes = []
    with _lock:
        profiles = _read(Path(path))
        profile = profiles.get(me)
        if not isinstance(profile, dict):
            return
        name = profile.get("name", "")
        if profile.get("email") and not profile.get("bon_at") and seen >= GOAL:
            profile["bon_at"] = now
            notes.append(f"{name} a regardé {seen} vidéos : envoyer le bon d'achat "
                         f"Amazon de {PRIZE} € à {profile['email']}")
        godparent = profiles.get(profile.get("parrain") or "")
        if (isinstance(godparent, dict) and godparent.get("email")
                and not profile.get("parrain_bon_at") and seen >= FRIEND_GOAL):
            profile["parrain_bon_at"] = now
            notes.append(f"Parrainage : {name}, filleul de {godparent.get('name', '')}, "
                         f"a regardé {seen} vidéos : envoyer {FRIEND_PRIZE} € en bon "
                         f"d'achat Amazon à {godparent['email']}")
        if not notes or not _write(Path(path), profiles):
            return
    for text in notes:
        demandes.append(requests_path, label, "bon", text, system=True)
