"""Le bilan de sante du programme : « Prisme-diagnostic.exe --verifier ».

Ce qui marche depuis les sources peut manquer au programme empaquete -- un
module laisse de cote a la construction, un fichier qui n'a pas suivi. Ce
bilan essaie, dans le programme lui-meme, chaque piece qui en depend : numpy,
la recherche web (pages, yt-dlp, navigateur invisible), ffmpeg, et le Python
du Labo IA. Une ligne par piece, « ok » ou ce qui ne va pas.

    --avec-python   pose aussi le Python du labo (une vingtaine de Mo), puis
                    lui fait faire un aller-retour
    --avec-clip     charge le moteur CLIP du labo et calcule une empreinte
                    (le Python du labo doit avoir ses bibliotheques)

Il travaille dans un bac a sable : jamais l'index ni les reglages reels.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

# Avant tout import de videosorter : l'index s'ouvre au chargement.
os.environ.setdefault("PRISME_SANDBOX", tempfile.mkdtemp(prefix="prisme-verifier-"))

RESULTS: list = []


def _check(label: str):
    def wrap(work):
        start = time.monotonic()
        try:
            said = work()
            RESULTS.append((True, label, said or "ok", time.monotonic() - start))
        except Exception as exc:                            # noqa: BLE001
            detail = f"{type(exc).__name__} : {exc}"
            if os.environ.get("PRISME_VERIFIER_DETAIL"):
                detail += "\n" + traceback.format_exc()
            RESULTS.append((False, label, detail, time.monotonic() - start))
        _say(RESULTS[-1])
        return work
    return wrap


def _say(result) -> None:
    good, label, said, seconds = result
    line = f"{'ok ' if good else 'KO '} {label} : {said} ({seconds:.1f} s)"
    if sys.stdout is not None:
        print(line, flush=True)


def run(argv: list) -> int:
    from PySide6.QtCore import QCoreApplication, Qt
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

    @_check("programme")
    def _program():
        return (f"Python {sys.version.split()[0]}, "
                f"{'empaqueté' if getattr(sys, 'frozen', False) else 'sources'} ({sys.executable})")

    @_check("numpy")
    def _numpy():
        import numpy
        return f"{numpy.__version__}, {float(numpy.arange(4).sum()):.0f} = 6"

    @_check("pages web (bs4)")
    def _bs4():
        from bs4 import BeautifulSoup
        found = BeautifulSoup("<a href='/v/1'>x</a>", "html.parser").a["href"]
        assert found == "/v/1", found
        return "ok"

    @_check("yt-dlp")
    def _ytdlp():
        import yt_dlp
        from yt_dlp.extractor import gen_extractor_classes
        return f"{yt_dlp.version.__version__}, {len(gen_extractor_classes())} sites connus"

    @_check("certificats (truststore)")
    def _trust():
        import truststore
        truststore.SSLContext
        return "ok"

    @_check("navigateur invisible (QtWebEngine)")
    def _webengine():
        from PySide6.QtWebEngineCore import QWebEnginePage
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        app = QApplication.instance() or QApplication(sys.argv[:1])
        page = QWebEnginePage()
        loop = QEventLoop()
        box = {}
        page.loadFinished.connect(lambda ok: (box.update(loaded=ok), loop.quit()))
        QTimer.singleShot(30000, loop.quit)
        page.setHtml("<html><body><script>document.title = 'p' + (6 * 7)</script></body></html>",
                     QUrl("https://prisme.invalid/"))
        loop.exec()
        assert box.get("loaded"), "la page ne s'est pas chargée"

        def got(value):
            box["title"] = value
            loop.quit()
        page.runJavaScript("document.title", 0, got)
        QTimer.singleShot(10000, loop.quit)
        loop.exec()
        page.deleteLater()
        app.processEvents()
        assert box.get("title") == "p42", box.get("title")
        return "JavaScript exécuté"

    @_check("ffmpeg")
    def _ffmpeg():
        from .media import Tools
        if Tools.resolve():
            return f"{Tools.ffmpeg}"
        from .ffmpeg_fetch import folder
        return f"absent : proposé au lancement (posé dans {folder()})"

    @_check("lecture vidéo (QtMultimedia)")
    def _playback():
        import subprocess
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        from PySide6.QtWidgets import QApplication
        from .media import NO_WINDOW, Tools
        if not Tools.resolve():
            raise RuntimeError("sans ffmpeg, pas de vidéo d'essai")
        clip = os.path.join(os.environ["PRISME_SANDBOX"], "essai.mp4")
        subprocess.run([Tools.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=320x240:rate=25:duration=2", "-pix_fmt", "yuv420p", clip],
                       check=True, creationflags=NO_WINDOW)
        app = QApplication.instance() or QApplication(sys.argv[:1])
        player, sink, loop, box = QMediaPlayer(), QVideoSink(), QEventLoop(), {}

        def frame(video) -> None:
            if video.isValid() and "size" not in box:
                box["size"] = (video.width(), video.height())
                loop.quit()
        sink.videoFrameChanged.connect(frame)
        player.setVideoSink(sink)
        player.errorOccurred.connect(lambda _e, text: (box.update(error=text), loop.quit()))
        player.setSource(QUrl.fromLocalFile(clip))
        player.play()
        QTimer.singleShot(15000, loop.quit)
        loop.exec()
        player.stop()
        app.processEvents()
        assert "size" in box, box.get("error") or "aucune image reçue"
        return f"images {box['size'][0]}×{box['size'][1]} reçues"

    from . import ia, iapython

    @_check("Labo IA : son Python")
    def _labo_python():
        if not iapython.external():
            return f"celui de Prisme ({sys.executable}) ; manque : {', '.join(ia.missing()) or 'rien'}"
        state = "prêt" if iapython.prepared() else "pas encore installé"
        return f"{iapython.python()} : {state} ; manque : {', '.join(ia.missing()) or 'rien'}"

    if "--avec-python" in argv:
        @_check("Labo IA : installer son Python")
        def _prepare():
            iapython.prepare(lambda line: None)
            return str(iapython.python())

    if iapython.external() and iapython.prepared():
        @_check("Labo IA : aller-retour avec son Python")
        def _echo():
            process, requests, answers = iapython.spawn(iapython.echo_child, (), "prisme-verifier")
            try:
                requests.put(("echo", "bonjour"))
                state, value = answers.get(timeout=60)
                assert state == "ok" and value["payload"] == "bonjour", value
                return f"Python {value['python']} ({value['executable']})"
            finally:
                requests.put(("quit", None))
                process.join(5)

    if "--avec-clip" in argv:
        @_check("Labo IA : moteur CLIP")
        def _clip():
            engine = ia.RemoteClip(ia.DEFAULT_ENGINE)
            try:
                engine.load("text")
                vector = engine.embed_texts(["une plage au coucher du soleil"])[0]
                return f"empreinte de {len(vector)} nombres, sur {engine.device}"
            finally:
                engine.close(wait=True)

    failed = [r for r in RESULTS if not r[0]]
    if sys.stdout is not None:
        print(f"\n{len(RESULTS) - len(failed)} / {len(RESULTS)} pièces en ordre.", flush=True)
    return 1 if failed else 0
