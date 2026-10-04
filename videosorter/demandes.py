"""Les demandes envoyees depuis le telephone : un titre, un style de video.

La page mobile les envoie (`/api/demande`) ; le serveur qui la sert les
range, une par ligne, dans un fichier : celui du PC (les reglages de Prisme),
ou celui du partage quand c'est le NAS qui sert la page. Prisme lit les deux,
et les montre dans sa fenetre « Demandes reçues ».

Sans Qt : le NAS s'en sert aussi.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

KINDS = {"demande": "Une demande", "titre": "Un titre", "style": "Un style de vidéo", "lien": "Un lien à ajouter",
         "autre": "Autre", "inscription": "Nouvel inscrit",
         "bon": "Bon d'achat à envoyer"}
# Ce que la page peut envoyer ; les deux autres, c'est le serveur qui les
# écrit (`profils.py`).
PUBLIC_KINDS = ("demande", "titre", "style", "lien", "autre")
MAX_TEXT = 500
# Au plus tant de demandes par appareil et par heure : un doigt qui insiste,
# ou quelqu'un qui s'amuse, ne remplit pas le fichier.
PER_HOUR = 30
FILE_NAME = "demandes.jsonl"

_lock = threading.Lock()
_recent: dict = {}


def entry_id(entry: dict) -> str:
    raw = f"{entry.get('at')}|{entry.get('who')}|{entry.get('text')}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]


def append(path, who: str, kind: str, text: str, system: bool = False) -> dict:
    """Range une demande. Rend {"ok": True} ou {"ok": False, "error": ...}.

    `system` : écrite par le serveur lui-même (un inscrit, un bon d'achat),
    hors du plafond par appareil."""
    text = " ".join(str(text or "").split())[:MAX_TEXT]
    kind = kind if kind in (KINDS if system else PUBLIC_KINDS) else "autre"
    if len(text) < 2:
        return {"ok": False, "error": "Écrivez votre demande."}
    if path is None:
        return {"ok": False, "error": "Les demandes ne sont pas reçues ici."}
    now = time.time()
    with _lock:
        if not system:
            recent = [at for at in _recent.get(who, []) if now - at < 3600]
            if len(recent) >= PER_HOUR:
                return {"ok": False,
                        "error": "Beaucoup de demandes d'un coup : réessayez plus tard."}
            recent.append(now)
            _recent[who] = recent
        entry = {"at": round(now, 3), "who": str(who or "")[:80], "kind": kind, "text": text}
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            return {"ok": False, "error": f"Impossible de l'enregistrer : {exc}"}
    return {"ok": True}


def read_all(paths) -> list:
    """Toutes les demandes de ces fichiers, la plus recente d'abord, chacune
    avec son identifiant et le fichier d'ou elle vient."""
    found = {}
    for path in paths:
        if path is None:
            continue
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or not entry.get("text"):
                continue
            entry["id"] = entry_id(entry)
            entry["source"] = str(path)
            found[entry["id"]] = entry
    return sorted(found.values(), key=lambda e: -float(e.get("at") or 0))


def remove(paths, ids: set) -> int:
    """Retire ces demandes de leurs fichiers (reecrits d'un bloc)."""
    removed = 0
    with _lock:
        for path in paths:
            if path is None:
                continue
            try:
                lines = Path(path).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            keep = []
            for line in lines:
                try:
                    entry = json.loads(line)
                except ValueError:
                    keep.append(line)
                    continue
                if isinstance(entry, dict) and entry_id(entry) in ids:
                    removed += 1
                    continue
                keep.append(line)
            spare = Path(str(path) + ".tmp")
            try:
                spare.write_text("".join(f"{line}\n" for line in keep), encoding="utf-8")
                os.replace(spare, path)
            except OSError:
                pass
    return removed
