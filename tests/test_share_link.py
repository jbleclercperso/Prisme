"""Le lien d'invitation suffit : sans cookie, sans mot de passe.

Court, sans Qt : le serveur de partage tel qu'il tourne sur le NAS, sur une
bibliotheque de deux dossiers.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("PRISME_SANDBOX",
                      os.path.join(tempfile.gettempdir(), "prisme-tests-lien"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videosorter.web import INVITE_PATH, Library, Server, new_invite  # noqa: E402

FAILS: list = []


def check(condition, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


class Shelf(Library):
    """Deux dossiers, trois videos : sans index ni vignettes du PC."""

    def __init__(self, root: Path):
        self.entries = {}
        super().__init__(root)

    def refresh(self) -> None:
        folders, videos, by_folder = [], {}, {}
        for folder in sorted(self.root.iterdir()):
            marks = []
            for path in sorted(folder.iterdir()):
                mark = self.mark(path)
                videos[mark] = path
                marks.append(mark)
            fid = self.mark(folder)
            by_folder[fid] = marks
            folders.append({"id": fid, "name": folder.name, "count": len(marks),
                            "size": "1 Ko", "cover": marks[0]})
        with self._lock:
            self.folders, self.videos, self.by_folder = folders, videos, by_folder
            self.version = "1"

    def thumb_file(self, path, mark=""):
        return self.root / "absente.jpg"

    def video_entry(self, mark):
        path = self.videos.get(mark)
        return None if path is None else {"id": mark, "name": path.name,
                                          "folder": path.parent.name,
                                          "duration": "", "height": 0}


def main() -> int:
    root = Path(tempfile.gettempdir()) / "prisme-tests-lien-racine"
    shutil.rmtree(root, ignore_errors=True)
    for folder, names in {"Plage": ["vagues.mp4", "surf.mp4"],
                          "Montagne": ["neige.mp4"]}.items():
        (root / folder).mkdir(parents=True)
        for name in names:
            (root / folder / name).write_bytes(bytes(range(256)) * 8)
    key = new_invite()
    shelf = Shelf(root)
    server = Server(root, "", "", port=0, invite=key, library=shelf)
    port = server.start()

    def ask(route, headers=None, method="GET"):
        link = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        link.request(method, route, headers=headers or {})
        answer = link.getresponse()
        body = answer.read()
        return answer.status, dict(answer.getheaders()), body

    try:
        print("\n[1] Sans le lien")
        code, _h, _b = ask("/")
        check(code == 200, "la page s'ouvre, mais ne montre rien d'elle-même")
        code, _h, body = ask("/api/folders")
        check(code == 401 and json.loads(body).get("password") is False,
              "l'inventaire est refusé, et la page sait qu'il n'y a pas de mot de passe")
        code, head, _b = ask("/login")
        check(code == 303 and head.get("Location") == "/",
              "sans mot de passe posé, plus de page de mot de passe")
        mark = next(iter(shelf.videos))
        code, _h, _b = ask(f"/video/{mark}?cle=mauvaise")
        check(code == 401, "une mauvaise clé n'ouvre pas une vidéo")

        print("\n[2] Le lien d'invitation")
        code, head, _b = ask(INVITE_PATH + key)
        check(code == 303 and head.get("Location") == f"/#cle={key}",
              "il mène à la page en lui confiant la clé (après #)")
        check("prisme=" in head.get("Set-Cookie", ""), "et pose aussi une session")

        print("\n[3] La clé seule, sans cookie")
        with_key = {"X-Prisme-Cle": key}
        code, _h, body = ask("/api/folders", with_key)
        check(code == 200 and json.loads(body)["total"] == 2,
              "l'inventaire s'ouvre avec la clé en en-tête")
        code, _h, body = ask(f"/video/{mark}?cle={key}", {"Range": "bytes=0-9"})
        check(code == 206 and len(body) == 10,
              "une vidéo se lit avec la clé dans son adresse, par morceaux")
        code, _h, body = ask("/api/search?q=plage", with_key)
        found = json.loads(body)
        check([f["name"] for f in found["folders"]] == ["Plage"],
              f"la recherche trouve aussi les dossiers ({found['folders']})")
        code, _h, body = ask("/api/random?n=2", with_key)
        check(code == 200 and len(json.loads(body)["videos"]) == 2,
              "« Au hasard » rend des vidéos")

        print("\n[4] En direct : qui regarde, et où il en est")
        body = json.dumps({"id": mark, "seconds": 5, "at": 42.0,
                           "playing": False}).encode()
        code, _h, _b = ask_post = None, None, None
        link = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        link.request("POST", "/api/watching", body=body,
                     headers={"X-Prisme-Cle": key, "Content-Type": "application/json",
                              "User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/130"})
        code = link.getresponse().status
        live = [v for v in server.viewers() if "Android" in v["label"]]
        check(code == 200 and len(live) == 1 and live[0]["video"] == mark
              and abs(live[0]["at"] - 42.0) < 0.5 and "Android" in live[0]["label"],
              f"le visiteur apparaît, sur sa vidéo, à 42 s ({live})")
        server.LIVE_SECONDS = 0
        check(server.viewers() == [], "et disparaît quand il ne donne plus signe de vie")
        server.LIVE_SECONDS = 90

        print("\n[5] Les favoris, par appareil")
        phone = {"X-Prisme-Cle": key, "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (Linux; Android 14) Chrome/130"}
        other = dict(phone, **{"User-Agent": "Mozilla/5.0 (iPhone) Safari/17"})
        link = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        link.request("POST", "/api/favorite", body=json.dumps({"id": mark, "on": True}),
                     headers=phone)
        answer = link.getresponse()
        answer.read()
        check(answer.status == 200, "l'étoile du lecteur ajoute un favori")
        code, _h, body = ask("/api/favorites", phone)
        mine = [v["id"] for v in json.loads(body)["videos"]]
        code, _h, body = ask("/api/favorites", other)
        theirs = [v["id"] for v in json.loads(body)["videos"]]
        check(mine == [mark] and theirs == [],
              "chaque téléphone a ses propres favoris")
        from videosorter.access import JOURNAL as _journal
        check(any(row[3] == mark for row in _journal.all_favorites()),
              "et Prisme, sur le PC, les voit tous")
        link = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        link.request("POST", "/api/favorite", body=json.dumps({"id": mark, "on": False}),
                     headers=phone)
        link.getresponse().read()
        code, _h, body = ask("/api/favorites", phone)
        check(json.loads(body)["videos"] == [], "l'étoile, de nouveau, le retire")

        print("\n[6] L'application sur l'écran d'accueil")
        code, head, body = ask("/manifest.webmanifest")
        check(code == 200 and json.loads(body)["display"] == "standalone",
              "le manifeste se lit sans clé (rien de privé)")
        code, _h, body = ask(f"/manifest.webmanifest?cle={key}")
        check(json.loads(body)["start_url"] == f"/#cle={key}",
              "avec la clé, l'icône s'ouvrira avec l'accès (iPhone : mémoire à part)")
        code, _h, body = ask("/manifest.webmanifest?cle=mauvaise")
        check(json.loads(body)["start_url"] == "/",
              "une mauvaise clé n'y est pas recopiée")
        code, head, body = ask("/sw.js")
        check(code == 200 and b"respondWith" in body
              and head.get("Service-Worker-Allowed") == "/"
              and "javascript" in head.get("Content-Type", ""),
              "le script de fond (installation Android) se lit sans clé")
        code, head, _b = ask("/")
        check("worker-src 'self'" in head.get("Content-Security-Policy", ""),
              "et la page a le droit de l'enregistrer")
        server.icons[192] = b"\x89PNG-test"
        code, _h, body = ask("/icon-192.png")
        check(code == 200 and body == b"\x89PNG-test", "et l'icône aussi")
        code, _h, _b = ask("/api/install")
        check(code == 401, "l'adresse https d'installation ne se lit pas sans clé")
        server.secure = "https://prisme-nas.exemple.ts.net"
        code, _h, body = ask("/api/install", with_key)
        check(code == 200 and json.loads(body)["secure"] == server.secure,
              "avec la clé, le lien Wi-Fi apprend où poser l'icône (https)")

        print("\n[7] Un nouveau lien")
        server.invite = new_invite()
        code, _h, _b = ask("/api/folders", with_key)
        check(code == 401, "l'ancienne clé ne vaut plus rien")
    finally:
        server.stop()
    print("\ntout est vert" if not FAILS else f"\n{len(FAILS)} échec(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
