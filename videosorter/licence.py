"""L'essai de quatorze jours, et les cles de licence.

Une cle « PRISME1-… » est signee par le site (Ed25519) et se verifie ici,
hors ligne, avec la seule cle publique ci-dessous. Sa forme :

    PRISME1-<contenu>.<signature>

ou <contenu> et <signature> sont en base64 « url » sans remplissage ; le
contenu est un petit JSON : {"plan": "mois" | "an" | "vie", "expires": <date
d'expiration, secondes depuis 1970, 0 pour une licence a vie>, "email": …,
"issued": …}, et la signature porte sur ces octets-la, tels quels.

- **PUBLIC_KEY vide : version de developpement, sans essai ni licence.**
  Avant de distribuer, y coller la cle publique (32 octets, en hexadecimal ou
  en base64) donnee par le site, et regler SITE sur son adresse definitive.
- Le debut de l'essai est note dans la configuration **et** dans le registre
  (HKCU\\Software\\Prisme) : effacer l'un ne remet pas l'essai a zero.
  Reculer l'horloge ne sert a rien non plus : on retient le plus tard deja vu.
- Un abonnement se prolonge seul : dans ses dix derniers jours, Prisme
  demande une cle plus longue au site (POST /api/licence), une fois par jour
  au plus.

La verification Ed25519 suit la RFC 8032, sans dependance.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass

PUBLIC_KEY = ""
SITE = "https://prisme-website.netlify.app"
PREFIX = "PRISME1-"
TRIAL_DAYS = 14
RENEW_DAYS = 10
DAY = 86400.0
_REG_PATH = r"Software\Prisme"
_REG_NAME = "Debut"

# -- Ed25519 (RFC 8032), verification ---------------------------------------------

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _add(a: tuple, b: tuple) -> tuple:
    """Addition de deux points, en coordonnees etendues (X, Y, Z, T)."""
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    k1 = (y1 - x1) * (y2 - x2) % _P
    k2 = (y1 + x1) * (y2 + x2) % _P
    k3 = 2 * t1 * t2 * _D % _P
    k4 = 2 * z1 * z2 % _P
    e, f, g, h = k2 - k1, k4 - k3, k4 + k3, k2 + k1
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(scalar: int, point: tuple) -> tuple:
    result = (0, 1, 1, 0)                   # le point neutre
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _same(a: tuple, b: tuple) -> bool:
    return ((a[0] * b[2] - b[0] * a[2]) % _P == 0
            and (a[1] * b[2] - b[1] * a[2]) % _P == 0)


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _decompress(raw: bytes):
    if len(raw) != 32:
        return None
    y = int.from_bytes(raw, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _compress(point: tuple) -> bytes:
    z = _inv(point[2])
    x, y = point[0] * z % _P, point[1] * z % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _hash_mod_q(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """La signature Ed25519 de `message` par la cle `public` est-elle bonne ?"""
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = _hash_mod_q(signature[:32] + public + message)
    return _same(_mul(s, _G), _add(r, _mul(h, a)))


# -- les cles ---------------------------------------------------------------------

def _public_bytes(public: str) -> bytes:
    text = (public or "").strip()
    if len(text) == 64:
        try:
            return bytes.fromhex(text)
        except ValueError:
            pass
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4))
    except ValueError:
        return b""


def _b64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def read_key(key: str, public: str | None = None) -> dict | None:
    """Le contenu d'une cle dont la signature est bonne, ou None."""
    text = "".join((key or "").split())
    if not text.upper().startswith(PREFIX):
        return None
    body = text[len(PREFIX):]
    head, _, tail = body.partition(".")
    try:
        payload, signature = _b64url(head), _b64url(tail)
    except (ValueError, TypeError):
        return None
    public_raw = _public_bytes(PUBLIC_KEY if public is None else public)
    if not public_raw or not verify(public_raw, payload, signature):
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        data["expires"] = int(data.get("expires") or 0)
    except (TypeError, ValueError):
        return None
    return data


# -- l'essai ----------------------------------------------------------------------

def _registry_start() -> float:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            value, _kind = winreg.QueryValueEx(key, _REG_NAME)
        return float(value)
    except (OSError, ImportError, ValueError):
        return 0.0


def _write_registry_start(moment: float) -> None:
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            winreg.SetValueEx(key, _REG_NAME, 0, winreg.REG_SZ, str(int(moment)))
    except (OSError, ImportError):
        pass


def trial_start(cfg, now: float, registry: bool = True) -> float:
    """Le debut de l'essai : le plus ancien des deux endroits ou il est note,
    ou maintenant (et on le note aux deux)."""
    known = [v for v in (float(cfg["trial_started"] or 0),
                         _registry_start() if registry else 0.0) if v > 0]
    start = min(known) if known else now
    if float(cfg["trial_started"] or 0) != start:
        cfg["trial_started"] = start
        cfg.save_soon()
    if registry and _registry_start() != int(start):
        _write_registry_start(start)
    return start


@dataclass
class Status:
    mode: str                  # « dev », « licence », « essai », « fini »
    days_left: int = 0
    expires: int = 0           # 0 : a vie (ou sans objet)
    plan: str = ""
    email: str = ""

    @property
    def usable(self) -> bool:
        return self.mode != "fini"


def status(cfg, now: float | None = None, public: str | None = None,
           registry: bool = True) -> Status:
    """Ou en est cette installation : licence, essai, ou essai fini."""
    public = PUBLIC_KEY if public is None else public
    if not public:
        return Status("dev")
    now = time.time() if now is None else now
    # L'horloge reculee ne rend pas de jours : on garde le plus tard deja vu.
    seen = float(cfg["licence_seen"] or 0)
    if now > seen:
        cfg["licence_seen"] = now
        cfg.save_soon()
    now = max(now, seen)
    data = read_key(cfg["licence_key"], public)
    if data is not None and (not data["expires"] or data["expires"] > now):
        left = int((data["expires"] - now) // DAY) if data["expires"] else 0
        return Status("licence", left, data["expires"], str(data.get("plan") or ""),
                      str(data.get("email") or ""))
    start = trial_start(cfg, now, registry)
    left = TRIAL_DAYS - int((now - start) // DAY)
    if left > 0:
        return Status("essai", left)
    return Status("fini")


def accept_key(cfg, key: str, now: float | None = None, public: str | None = None) -> str:
    """Enregistre une cle si elle est bonne et encore valable. Rend "" si
    c'est fait, sinon ce qui ne va pas."""
    now = time.time() if now is None else now
    data = read_key(key, public)
    if data is None:
        return "Cette clé n'est pas valable (vérifiez qu'elle est copiée en entier)."
    if data["expires"] and data["expires"] <= now:
        return "Cette clé a expiré."
    cfg["licence_key"] = "".join(key.split())
    cfg.save()
    return ""


# -- la prolongation d'un abonnement ------------------------------------------------

def renewal_due(cfg, now: float | None = None, public: str | None = None) -> bool:
    """Faut-il demander au site une cle prolongee ? (Dix derniers jours d'un
    abonnement, et pas deja demande dans la journee.)"""
    now = time.time() if now is None else now
    data = read_key(cfg["licence_key"], public)
    if data is None or not data["expires"]:
        return False
    if data["expires"] - now > RENEW_DAYS * DAY:
        return False
    return now - float(cfg["licence_checked"] or 0) >= DAY


def renew(cfg, now: float | None = None, public: str | None = None,
          timeout: float = 10.0, post=None) -> str:
    """Demande au site une cle prolongee. Rend un message si la licence a ete
    prolongee, "" sinon (pas le moment, site muet, pas encore renouvelee).
    Hors du fil de l'interface : c'est un appel reseau."""
    now = time.time() if now is None else now
    if not renewal_due(cfg, now, public):
        return ""
    cfg["licence_checked"] = now
    cfg.save_soon()
    old = read_key(cfg["licence_key"], public) or {}
    try:
        answer = (post or _post)(SITE.rstrip("/") + "/api/licence",
                                 {"key": cfg["licence_key"]}, timeout)
    except Exception:                                   # noqa: BLE001
        return ""
    new_key = (answer or {}).get("key") or ""
    data = read_key(new_key, public)
    if data is None or (data["expires"] and data["expires"] <= old.get("expires", 0)):
        return ""
    cfg["licence_key"] = "".join(new_key.split())
    cfg.save()
    if not data["expires"]:
        return "Licence renouvelée."
    return "Licence prolongée jusqu'au " + time.strftime("%d/%m/%Y", time.localtime(data["expires"])) + "."


def _post(url: str, body: dict, timeout: float) -> dict:
    import urllib.request
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.loads(answer.read().decode("utf-8"))
