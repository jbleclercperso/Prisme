"""Les demandes envoyées du téléphone : la page, le serveur, le fichier.

    python tests/test_demandes.py
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BOX = os.path.join(tempfile.gettempdir(), "prisme-test-demandes")
shutil.rmtree(BOX, ignore_errors=True)
os.environ["PRISME_SANDBOX"] = BOX
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from videosorter import demandes  # noqa: E402
from videosorter.web import Server, new_invite  # noqa: E402

FAILS = []


def check(condition, label: str) -> None:
    print(("  ok   " if condition else "  ÉCHEC ") + label)
    if not condition:
        FAILS.append(label)


class Shelf:
    """Une bibliothèque vide : ici, seules les demandes comptent."""

    def __init__(self):
        self.videos = {}
        self.loaded_at = 0

    def refresh(self):
        pass

    def __getattr__(self, name):
        raise AttributeError(name)


def main() -> int:
    root = Path(BOX) / "coll"
    root.mkdir(parents=True)
    key = new_invite()
    box = Path(BOX) / "demandes.jsonl"
    server = Server(root, "", "", port=0, invite=key)
    server.requests_path = box
    port = server.start()

    def post(route, payload, headers=None):
        link = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        body = json.dumps(payload).encode("utf-8")
        link.request("POST", route, body=body,
                     headers={"Content-Type": "application/json", **(headers or {})})
        answer = link.getresponse()
        return answer.status, json.loads(answer.read() or b"{}")

    try:
        print("\n[1] La page")
        link = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        link.request("GET", "/")
        page = link.getresponse().read().decode("utf-8")
        check('id="askBtn"' in page and 'id="ask"' in page and "/api/demande" in page,
              "la page porte le bouton et la feuille « Envoyer une demande »")

        print("\n[2] Envoyer")
        code, said = post("/api/demande", {"kind": "style", "text": "des vidéos à la plage"})
        check(code == 401, "sans le lien : refusé")
        with_key = {"X-Prisme-Cle": key}
        code, said = post("/api/demande", {"kind": "style", "text": "  des vidéos   à la plage "},
                          with_key)
        check(code == 200 and said.get("ok"), f"avec le lien : reçue ({said})")
        code, said = post("/api/demande", {"kind": "titre", "text": "x"}, with_key)
        check(code == 400 and not said.get("ok"), "une demande vide est refusée, et le dit")
        post("/api/demande", {"kind": "titre", "text": "La suite de « Plage 2 »"}, with_key)

        print("\n[3] Dans Prisme")
        found = demandes.read_all([box, Path(BOX) / "absent.jsonl"])
        check([e["text"] for e in found] == ["La suite de « Plage 2 »", "des vidéos à la plage"],
              "les deux demandes, la plus récente d'abord, le texte nettoyé")
        check(found[0]["kind"] == "titre" and found[1]["kind"] == "style" and found[0]["who"],
              "avec leur sorte et l'appareil qui les a envoyées")
        demandes.remove([box], {found[0]["id"]})
        check([e["text"] for e in demandes.read_all([box])] == ["des vidéos à la plage"],
              "« Supprimer » la retire du fichier")
        server.requests_path = None
        code, said = post("/api/demande", {"kind": "autre", "text": "encore une"}, with_key)
        check(code == 400 and "reçues" in said.get("error", ""),
              "un serveur sans boîte le dit au lieu de perdre la demande")
    finally:
        server.stop()

    print("\n" + ("tout est vert" if not FAILS else f"{len(FAILS)} échec(s)"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
