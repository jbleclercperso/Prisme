"""Le telechargement rapporte un vrai fichier video, jamais la page.

Court : de faux sites en local, de vraies petites videos. Chaque page cache
sa video autrement -- balise <video>, adresse dans un script, lecteur integre
d'un autre site, page servie comme un fichier -- et le fichier n'est servi
qu'a qui vient de la page (Referer), comme sur beaucoup de sites.
"""
from __future__ import annotations

import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("PRISME_SANDBOX",
                      os.path.join(tempfile.gettempdir(), "prisme-tests-telechargement"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_fixture import make_video  # noqa: E402
from videosorter.downloader import Downloader, is_video_file, media_candidates  # noqa: E402
from videosorter.mediafind import Media  # noqa: E402
from videosorter.media import Tools  # noqa: E402

FAILS: list = []
BASE = Path(tempfile.gettempdir()) / "prisme-tests-telechargement-sites"
MEDIA: dict = {}
RANGES: list = []                  # les morceaux demandes au serveur
OTHER = {"host": ""}


def check(condition, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


PAGES = {
    "/watch/balise": """<html><head><title>Balise video</title></head><body>
        <video data-src="/media/preview.mp4"><source src="/media/main.mp4"
        type="video/mp4"></video></body></html>""",
    "/watch/script": """<html><head><title>Dans le script</title></head><body>
        <div class="player"></div><img src="/media/preview.mp4">
        <script>var player = {"sources":[{"file":"http:\\/\\/HOST\\/media\\/main.mp4?t=1",
        "label":"720p"}], "hover":"http:\\/\\/HOST\\/media\\/preview.mp4"};</script>
        </body></html>""",
    "/watch/integre": """<html><head><title>Lecteur integre</title></head><body>
        <iframe src="http://OTHER/embed/7" allowfullscreen></iframe></body></html>""",
    "/watch/deguisee": """<html><head><title>Page deguisee</title></head><body>
        <video><source src="/media/main.mp4"></video></body></html>""",
    "/watch/rien": """<html><head><title>Rien</title></head><body>
        <p>Pas de video ici.</p></body></html>""",
    "/watch/apercu": """<html><head><title>Rien qu'un apercu</title></head><body>
        <video src="/media/preview.mp4" autoplay muted></video></body></html>""",
    # Deux versions, sans rien dans leur nom pour les distinguer : la petite
    # d'abord, la grande (720p) ensuite. C'est la mesure qui doit choisir.
    "/watch/versions": """<html><head><title>Deux versions</title></head><body>
        <video><source src="/media/b.mp4"><source src="/media/a.mp4"></video></body></html>""",
    # Un lecteur KVS : l'adresse dans ses « flashvars », avec un « / » apres
    # l'extension, et un apercu de survol a cote.
    "/watch/kvs": """<html><head><title>Lecteur KVS</title></head><body>
        <div id="kt_player"></div><script>var flashvars = {
        video_url: 'http://HOST/media/main.mp4/?br=730', video_url_text: '240p',
        preview_url: 'http://HOST/media/preview.mp4', license_code: '$123456789012345'};
        </script></body></html>""",
    "/embed/7": """<html><body><video src="/media/main.mp4"></video></body></html>""",
    # Le moteur de txxx, hclips, bdsmx.tube : la page ne montre qu'un apercu,
    # le lecteur demande le fichier a « /api/videofile.php », adresse codee.
    "/video/441155/famille/": """<html><head><title>Famille txxx</title></head><body>
        <video src="/media/preview.mp4" autoplay muted></video></body></html>""",
}


def txxx_code(address: str) -> str:
    """Code une adresse comme le moteur de txxx (base64, lettres cyrilliques)."""
    import base64
    text = base64.b64encode(address.encode()).decode()
    return text.translate(str.maketrans({"A": "\u0410", "M": "\u041c", "/": ",",
                                         "+": ".", "=": "~"}))


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self.do_GET(body=False)

    def do_GET(self, body: bool = True):
        path = self.path.split("?", 1)[0]
        if path.startswith("/media/"):
            if not self.headers.get("Referer"):
                return self._send(403, "text/html", b"<html>interdit</html>", body)
            data = MEDIA.get(path.rstrip("/").rsplit("/", 1)[-1])
            if data is None:
                return self._send(404, "text/html", b"<html>absent</html>", body)
            wanted = self.headers.get("Range", "")
            if wanted.startswith("bytes="):
                # Par morceaux, comme les vrais serveurs : c'est ainsi que
                # Prisme lit l'en-tete d'un MP4 (ici range a la fin du fichier).
                first, _, last = wanted[6:].partition("-")
                first = int(first)
                last = min(int(last) if last else len(data) - 1, len(data) - 1)
                RANGES.append(wanted)
                self.send_response(206)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Range", f"bytes {first}-{last}/{len(data)}")
                self.send_header("Content-Length", str(last - first + 1))
                self.end_headers()
                if body:
                    self.wfile.write(data[first:last + 1])
                return
            return self._send(200, "video/mp4", data, body)
        if path == "/api/videofile.php":
            if not (self.headers.get("Referer") and self.headers.get("X-Requested-With")):
                return self._send(403, "text/html", b"<html>interdit</html>", body)
            answer = json.dumps([{"format": "_sd.mp4",
                                  "video_url": txxx_code("/media/main.mp4/?br=900")}])
            return self._send(200, "application/json", answer.encode(), body)
        page = PAGES.get(path)
        if page is None:
            return self._send(404, "text/html", b"<html>absent</html>", body)
        page = page.replace("HOST", self.headers.get("Host", "")).replace("OTHER", OTHER["host"])
        # La page « deguisee » se dit fichier : yt-dlp l'enregistrait telle quelle.
        kind = "application/octet-stream" if path == "/watch/deguisee" else "text/html"
        self._send(200, kind, page.encode(), body)

    def _send(self, code, kind, data, body):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if body:
            self.wfile.write(data)


def serve() -> str:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"127.0.0.1:{server.server_address[1]}"


def main() -> int:
    Tools.resolve()
    shutil.rmtree(BASE, ignore_errors=True)
    BASE.mkdir(parents=True)
    make_video(BASE / "main.mp4", 8, 1)
    make_video(BASE / "preview.mp4", 2, 2)
    MEDIA["main.mp4"] = (BASE / "main.mp4").read_bytes()
    import subprocess
    subprocess.run([Tools.ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=duration=8:size=1280x720:rate=10", "-pix_fmt", "yuv420p",
                    str(BASE / "a.mp4")], check=True)
    MEDIA["a.mp4"] = (BASE / "a.mp4").read_bytes()
    MEDIA["b.mp4"] = MEDIA["main.mp4"]              # la meme video, en 320x240
    MEDIA["preview.mp4"] = (BASE / "preview.mp4").read_bytes()
    site, OTHER["host"] = serve(), serve()

    print("\n[1] Ce qui, dans une page, est la video")
    found = media_candidates(PAGES["/watch/script"].replace("HOST", site),
                             f"http://{site}/watch/script")
    check(found and found[0].endswith("/media/main.mp4?t=1"),
          f"l'adresse cachée dans le script, avant l'aperçu ({found[:2]})")
    from videosorter.mediafind import album_files
    album = ('<video><source src="https://v6.erome.com/1/Ab/x_720p.mp4"></video>'
             '<video><source src="https://v6.erome.com/1/Ab/x_720p.mp4"></video>'
             '<video><source src="https://v6.erome.com/1/Ab/y_720p.mp4"></video>')
    check(album_files(album, "https://www.erome.com/a/Ab12") ==
          ["https://v6.erome.com/1/Ab/x_720p.mp4", "https://v6.erome.com/1/Ab/y_720p.mp4"]
          and album_files(album, "https://www.erome.com/search?q=x") == [],
          "un album erome : chacune de ses vidéos, une fois ; pas sur une autre page")
    check(not is_video_file(Path(__file__)), "un texte n'est pas une vidéo")
    check(is_video_file(BASE / "main.mp4"), "une vidéo en est une")
    from videosorter.mediafind import mp4_facts
    from videosorter.websearch import USER_AGENT
    facts = mp4_facts(f"http://{site}/media/main.mp4", f"http://{site}/watch/balise", USER_AGENT)
    check(facts is not None and abs(facts["duration"] - 8) < 0.5 and facts["height"] == 240
          and facts["size"] == len(MEDIA["main.mp4"]) and len(RANGES) <= 4,
          f"un MP4 se mesure à distance, en quelques morceaux ({facts}, {len(RANGES)} morceaux)")

    loader = Downloader()
    loader.ffmpeg = Tools.ffmpeg
    done: dict = {}
    loader._emit = lambda *_a: None
    loader._emit_done = lambda job, path, error: done.__setitem__(job, (path, error))

    print("\n[2] Chaque page, jusqu'au fichier")
    for number, (name, label) in enumerate([
            ("balise", "une balise <video>"),
            ("script", "une adresse dans le script du lecteur"),
            ("integre", "le lecteur d'un autre site, intégré à la page"),
            ("deguisee", "une page servie comme un fichier"),
            ("kvs", "le lecteur KVS (flashvars, « .mp4/?br= »)")], 1):
        folder = BASE / f"arrivee-{name}"
        loader._expect[number] = 8           # la duree annoncee par le site
        loader._download(number, f"http://{site}/watch/{name}", str(folder), "")
        path, error = done.get(number, ("", "rien"))
        same = bool(path) and Path(path).read_bytes() == MEDIA["main.mp4"]
        others = [p.name for p in folder.iterdir() if str(p) != path] if folder.exists() else []
        check(same and not others,
              f"{label} : la vidéo elle-même, rien d'autre ({Path(path).name if path else error}"
              f"{', restes : ' + str(others) if others else ''})")

    print("\n[2a] Le lecteur de la famille txxx (hclips, bdsmx.tube…)")
    folder = BASE / "arrivee-famille"
    loader._expect[6] = 8
    loader._download(6, f"http://{site}/video/441155/famille/", str(folder), "")
    path, error = done.get(6, ("", "rien"))
    check(bool(path) and Path(path).read_bytes() == MEDIA["main.mp4"],
          f"la vidéo entière, pas l'aperçu de la page ({Path(path).name if path else error})")

    print("\n[2b] Plusieurs versions de la même vidéo")
    folder = BASE / "arrivee-versions"
    loader._expect[7] = 8
    loader._download(7, f"http://{site}/watch/versions", str(folder), "")
    path, error = done.get(7, ("", "rien"))
    check(bool(path) and Path(path).read_bytes() == MEDIA["a.mp4"],
          f"la plus haute définition, la plus lourde ({Path(path).name if path else error})")

    print("\n[2c] Un téléchargement interrompu (Prisme fermé en route)")
    folder = BASE / "arrivee-reprise"
    folder.mkdir(parents=True, exist_ok=True)
    whole = MEDIA["main.mp4"]
    half = len(whole) // 2
    (folder / "Reprise.mp4.part").write_bytes(whole[:half])
    RANGES.clear()
    media = Media(f"http://{site}/media/main.mp4", f"http://{site}/watch/balise",
                  8, 240, len(whole), "Reprise")
    path, error = loader._direct(8, media, str(folder))
    check(bool(path) and Path(path).read_bytes() == whole
          and any(r.startswith(f"bytes={half}-") for r in RANGES),
          f"il repart de la moitié déjà reçue, et le fichier est entier ({error or RANGES[:2]})")

    print("\n[3] Une page sans vidéo")
    folder = BASE / "arrivee-rien"
    loader._download(9, f"http://{site}/watch/rien", str(folder), "")
    path, error = done.get(9, ("", ""))
    left = list(folder.iterdir()) if folder.exists() else []
    check(not path and "aucun fichier vidéo" in error and not left,
          f"un échec clair, et aucun fichier laissé ({error!r}, {left})")

    print("\n[4] Une page qui ne donne qu'un aperçu de 2 s d'une vidéo de 8 s")
    folder = BASE / "arrivee-apercu"
    loader._expect[10] = 8
    loader._download(10, f"http://{site}/watch/apercu", str(folder), "")
    path, error = done.get(10, ("", ""))
    left = list(folder.iterdir()) if folder.exists() else []
    check(not path and "aperçu" in error and not left,
          f"refusé, dit comme tel, et effacé ({error!r}, {left})")
    check(bool(loader.ffprobe), f"ffprobe est trouvé ({loader.ffprobe})")

    print("\ntout est vert" if not FAILS else f"\n{len(FAILS)} échec(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
