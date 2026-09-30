"""L'essai de quatorze jours, et la clé de licence qui lui succède.

Au premier lancement, Prisme note la date : quatorze jours complets, toutes
fonctionnalités, sans carte ni compte. Les jours qui restent se lisent en
haut de la fenêtre ; à la fin, Prisme demande une clé de licence avant de
s'ouvrir. Rien n'est effacé ni déplacé : réglages, favoris, index et
vignettes attendent, et reviennent tels quels avec la clé.

La clé est un petit texte signé par le site (Ed25519) :

    PRISME1-<contenu en base64url>.<signature en base64url>

Le contenu : { v: 1, p: formule, e: e-mail, c: client, s: abonnement,
i: émise le, x: vaut jusqu'au } — sans `x` pour une licence à vie. Prisme
la vérifie seul, sans connexion, avec la clé publique ci-dessous : personne
ne peut en fabriquer sans la clé privée, qui ne vit que sur le site.

Un abonnement reçoit une clé valable jusqu'à la fin de la période payée,
plus sept jours. Dans ses dix derniers jours, Prisme demande au site, une
fois par jour au plus, une clé prolongée — en n'envoyant que la clé, rien
de la collection. Une licence à vie ne se connecte jamais.

Tant que `PUBLIC_KEY` est vide, rien de tout cela ne s'applique : c'est la
version de développement, qui s'ouvre toujours. La clé publique se pose une
fois, avant de distribuer Prisme (`npm run licence:keys`, dans le site).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import time
from dataclasses import dataclass

# La clé publique du site, en base64 (32 octets). Vide : version de
# developpement, sans essai ni licence.
PUBLIC_KEY = ""
PREFIX = "PRISME1-"
# Le site : la page des formules, et l'adresse qui prolonge les abonnements.
# A changer avec le nom de domaine definitif.
SITE = "https://prisme-website.netlify.app"
TRIAL_DAYS = 14
DAY = 86400
# Un abonnement se prolonge dans ses dix derniers jours, une fois par jour.
RENEW_WITHIN = 10 * DAY
RENEW_EVERY = DAY

PLAN_NAMES = {"monthly": "Abonnement mensuel", "yearly": "Abonnement annuel",
              "lifetime": "Licence à vie"}

# Le debut de l'essai est aussi note dans le registre de Windows : effacer
# les reglages ne suffit pas a recommencer un essai. Sans pretention de
# forteresse -- c'est un rappel honnete, pas une protection.
_REG_PATH = r"Software\Prisme"
_REG_NAME = "Essai"


# ---------------------------------------------------------------------------
# Ed25519 : la verification seule (RFC 8032, section 5.1.7)
# ---------------------------------------------------------------------------
# Ecrite ici plutot qu'empruntee a une bibliotheque : une verification par
# lancement ne demande pas de vitesse, et une dependance de cryptographie
# mal installee (on en a vu une, cassee) empecherait Prisme de s'ouvrir.
# C'est la verification de reference de la norme, en coordonnees etendues.
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(one: tuple, two: tuple) -> tuple:
    x1, y1, z1, t1 = one
    x2, y2, z2, t2 = two
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _times(scalar: int, point: tuple) -> tuple:
    result = (0, 1, 1, 0)
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _same(one: tuple, two: tuple) -> bool:
    x1, y1, z1, _t1 = one
    x2, y2, z2, _t2 = two
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _x_from(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    square = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if square == 0:
        return None if sign else 0
    x = pow(square, (_P + 3) // 8, _P)
    if (x * x - square) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - square) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _point(raw: bytes) -> tuple | None:
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _x_from(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


_BASE_Y = 4 * pow(5, _P - 2, _P) % _P
_BASE = (_x_from(_BASE_Y, 0), _BASE_Y, 1, _x_from(_BASE_Y, 0) * _BASE_Y % _P)


def ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    key = _point(public)
    first = _point(signature[:32])
    if key is None or first is None:
        return False
    scalar = int.from_bytes(signature[32:], "little")
    if scalar >= _L:
        return False
    digest = hashlib.sha512(signature[:32] + public + message).digest()
    challenge = int.from_bytes(digest, "little") % _L
    return _same(_times(scalar, _BASE), _add(first, _times(challenge, key)))


# ---------------------------------------------------------------------------
# La cle
# ---------------------------------------------------------------------------
def _b64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def read_key(text: str, public_key: str | None = None) -> dict | None:
    """Le contenu d'une clé signée par le site, ou None.

    Tolère les espaces et retours à la ligne d'un copier-coller.
    """
    public_key = PUBLIC_KEY if public_key is None else public_key
    text = "".join(str(text or "").split())
    if not public_key or not text.startswith(PREFIX):
        return None
    parts = text[len(PREFIX):].split(".")
    if len(parts) != 2 or not all(parts):
        return None
    body, signature = parts
    try:
        if not ed25519_verify(base64.b64decode(public_key), body.encode("ascii"),
                              _b64url(signature)):
            return None
        payload = json.loads(_b64url(body).decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeError):
        return None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return None
    if payload.get("p") not in PLAN_NAMES:
        return None
    return payload


# ---------------------------------------------------------------------------
# L'etat
# ---------------------------------------------------------------------------
@dataclass
class Status:
    kind: str                 # dev | licensed | trial | expired
    days_left: int = 0        # essai : jours entiers restants
    plan: str = ""
    email: str = ""
    until: float = 0.0        # abonnement : fin de validite de la cle
    note: str = ""            # ce qui s'est passe (cle perimee, abonnement fini)

    @property
    def open(self) -> bool:
        """Prisme peut-il s'ouvrir ?"""
        return self.kind in ("dev", "licensed", "trial")

    def headline(self) -> str:
        """L'état en une phrase, pour le dialogue de licence."""
        if self.kind == "dev":
            return "Version de développement : ni essai ni licence."
        if self.kind == "trial":
            days = self.days_left
            return (f"Essai gratuit : {days} jour{'s' if days > 1 else ''} "
                    f"restant{'s' if days > 1 else ''}, toutes fonctionnalités.")
        if self.kind == "expired":
            return self.note or "Votre essai de 14 jours est terminé."
        name = PLAN_NAMES.get(self.plan, "Licence")
        who = f" · {self.email}" if self.email else ""
        if self.plan == "lifetime":
            return f"{name}{who}. Merci !"
        return (f"{name}{who}, valable jusqu'au "
                f"{time.strftime('%d/%m/%Y', time.localtime(self.until))} "
                "(prolongée d'elle-même tant que l'abonnement court).")


def _registry_start() -> float:
    if os.name != "nt":
        return 0.0
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            value, _kind = winreg.QueryValueEx(key, _REG_NAME)
            return float(value)
    except (OSError, ValueError):
        return 0.0


def _registry_note(moment: float) -> None:
    if os.name != "nt":
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            winreg.SetValueEx(key, _REG_NAME, 0, winreg.REG_SZ, str(int(moment)))
    except OSError:
        pass


def _now(cfg, now: float | None) -> float:
    """L'heure, jamais en arrière de la plus tardive déjà vue.

    Reculer l'horloge du PC ne rallonge ni l'essai ni une clé.
    """
    now = time.time() if now is None else now
    seen = float(cfg["licence_seen"] or 0)
    if now > seen:
        cfg["licence_seen"] = now
        return now
    return seen


def trial_start(cfg, now: float) -> float:
    """Le premier jour de l'essai : le plus ancien des deux endroits notés."""
    known = [moment for moment in (float(cfg["trial_started"] or 0),
                                   _registry_start()) if moment > 0]
    start = min(known) if known else now
    if float(cfg["trial_started"] or 0) != start:
        cfg["trial_started"] = start
    if _registry_start() != start:
        _registry_note(start)
    return start


def status(cfg, now: float | None = None, public_key: str | None = None) -> Status:
    """Où en est cette copie de Prisme : développement, licence, essai ou fin."""
    public_key = PUBLIC_KEY if public_key is None else public_key
    if not public_key:
        return Status("dev")
    now = _now(cfg, now)
    note = ""
    payload = read_key(cfg["licence_key"], public_key)
    if payload is not None:
        until = float(payload.get("x") or 0)
        if payload["p"] == "lifetime" or until > now:
            return Status("licensed", plan=payload["p"],
                          email=str(payload.get("e") or ""), until=until)
        note = ("Votre abonnement a pris fin. Reprenez une formule pour "
                "retrouver Prisme tel que vous l'avez laissé.")
    elif cfg["licence_key"]:
        note = "La clé enregistrée n'est pas valide."
    start = trial_start(cfg, now)
    left = start + TRIAL_DAYS * DAY - now
    if left > 0 and not note:
        return Status("trial", days_left=max(1, int(left // DAY) + (left % DAY > 0)))
    return Status("expired", note=note or "Votre essai de 14 jours est terminé. "
                  "Merci d'avoir essayé Prisme !")


def activate(cfg, text: str, now: float | None = None,
             public_key: str | None = None) -> tuple:
    """(vrai, message) si la clé est bonne et encore valable ; elle est gardée."""
    payload = read_key(text, public_key)
    if payload is None:
        return False, ("Cette clé n'est pas reconnue. Copiez-la en entier, "
                       "depuis « PRISME1- » jusqu'au dernier caractère.")
    moment = _now(cfg, now)
    if payload["p"] != "lifetime" and float(payload.get("x") or 0) <= moment:
        return False, ("Cette clé a expiré. Une clé à jour vous attend dans "
                       "« Mon compte », sur le site.")
    cfg["licence_key"] = "".join(str(text).split())
    cfg["licence_checked"] = 0
    cfg.save()
    return True, PLAN_NAMES[payload["p"]] + " activée. Merci !"


# ---------------------------------------------------------------------------
# La prolongation d'un abonnement
# ---------------------------------------------------------------------------
def renew_due(cfg, now: float | None = None, public_key: str | None = None) -> bool:
    """Faut-il demander une clé prolongée aujourd'hui ?"""
    payload = read_key(cfg["licence_key"], public_key)
    if payload is None or payload["p"] == "lifetime":
        return False
    now = time.time() if now is None else now
    if float(payload.get("x") or 0) - now > RENEW_WITHIN:
        return False
    return now - float(cfg["licence_checked"] or 0) >= RENEW_EVERY


def ask_site(key: str, site: str = SITE, timeout: float = 20.0) -> dict:
    """La réponse du site à une clé d'abonnement : `key`, `status` ou `error`."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        site.rstrip("/") + "/api/licence",
        data=json.dumps({"key": key}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}"}
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}


def take_renewal(cfg, answer: dict) -> str:
    """Range la réponse du site ; dit ce qu'il y a à dire (vide : rien)."""
    fresh = answer.get("key")
    if fresh and read_key(fresh) is not None:
        cfg["licence_key"] = fresh
        cfg.save()
        return ""
    if answer.get("status") == "ended":
        return ("Votre abonnement est arrêté : Prisme reste ouvert jusqu'à la "
                "fin de la période payée.")
    return ""
