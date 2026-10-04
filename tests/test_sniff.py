"""Le fichier qu'aucune lecture de la page ne trouve : on ecoute le lecteur.

Court : une page qui integre le lecteur d'un autre site ; ce lecteur ne
construit l'adresse de sa video (codee) qu'au clic. Ni la page ni le lecteur
ne l'ecrivent en clair. Le navigateur invisible clique au milieu du lecteur,
note ce qu'il charge, et la video entiere est trouvee et mesuree.
"""
from __future__ import annotations

import base64
import http.server
import os
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("PRISME_SANDBOX",
                      os.path.join(tempfile.gettempdir(), "prisme-tests-ecoute"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QCoreApplication, Qt, QTimer  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
from PySide6.QtWidgets import QApplication  # noqa: E402

from make_fixture import make_video  # noqa: E402
from videosorter.media import Tools  # noqa: E402
from videosorter.mediafind import media_candidates, resolve  # noqa: E402
from videosorter.websearch import USER_AGENT, Fetcher  # noqa: E402

FAILS: list = []
BASE = Path(tempfile.gettempdir()) / "prisme-tests-ecoute-sites"
HOSTS: dict = {}
MEDIA: dict = {}


def check(condition, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/watch/cachee":
            page = (f"<html><head><title>Video cachee</title></head><body>"
                    f"<iframe src='http://{HOSTS['player']}/player' width='800' "
                    f"height='450'></iframe></body></html>")
            return self._send("text/html", page.encode())
        if path == "/player":
            hidden = base64.b64encode(f"http://{HOSTS['player']}/media/main.mp4".encode()).decode()
            page = ("<html><body style='margin:0'><div id='p' style='width:800px;height:450px;"
                    "background:#000'></div><script>document.getElementById('p').onclick ="
                    " () => { const v = document.createElement('video'); v.muted = true;"
                    f" v.src = atob('{hidden}'); document.body.appendChild(v); v.play(); }};"
                    "</script></body></html>")
            return self._send("text/html", page.encode())
        if path.startswith("/media/"):
            data = MEDIA["main.mp4"]
            return self._send("video/mp4", data)
        self.send_response(404)
        self.end_headers()

    def _send(self, kind, data):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "none")
        self.end_headers()
        self.wfile.write(data)


def serve() -> str:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"127.0.0.1:{server.server_address[1]}"


def main() -> int:
    Tools.resolve()
    BASE.mkdir(parents=True, exist_ok=True)
    make_video(BASE / "main.mp4", 30, 3)
    MEDIA["main.mp4"] = (BASE / "main.mp4").read_bytes()
    HOSTS["page"], HOSTS["player"] = serve(), serve()
    app = QApplication([])
    from videosorter.webpage import PageReader
    reader = PageReader(USER_AGENT)
    page_url = f"http://{HOSTS['page']}/watch/cachee"
    outcome: dict = {}
    finished = threading.Event()

    def work():
        fetcher = Fetcher()
        page, _why = fetcher.get(page_url)
        outcome["static"] = media_candidates(page or "", page_url)
        outcome["without"] = resolve(fetcher.get, page_url, Tools.ffprobe, USER_AGENT, 30)
        outcome["with"] = resolve(fetcher.get, page_url, Tools.ffprobe, USER_AGENT, 30,
                                  sniff=reader.sniff)
        finished.set()

    watch = QTimer()
    watch.timeout.connect(lambda: finished.is_set() and app.quit())
    watch.start(100)
    threading.Thread(target=work, daemon=True).start()
    QTimer.singleShot(120_000, app.quit)
    app.exec()
    reader.close()

    print("\n[1] En lisant la page")
    check(outcome.get("static") == [], "aucune adresse de vidéo en clair")
    media, why = outcome.get("without", (None, ""))
    check(media is None, f"donc rien de trouvé sans écouter ({why})")

    print("\n[2] En écoutant le lecteur")
    media, why = outcome.get("with", (None, "pas fini"))
    check(media is not None and media.url.endswith("/media/main.mp4"),
          f"le fichier que le lecteur charge ({media.url if media else why})")
    check(media is not None and abs(media.duration - 30) < 1 and media.height == 240,
          f"mesuré : la vidéo entière ({(media.duration, media.height) if media else '-'})")

    print("\ntout est vert" if not FAILS else f"\n{len(FAILS)} échec(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
