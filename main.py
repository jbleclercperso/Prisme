"""Point d'entrée de Prisme.

Tout est enveloppé dans un rapport d'incident : une application sans console
qui échoue au démarrage ne dit rien du tout, et l'on reste devant un bureau
muet. Ici, la moindre erreur s'écrit dans « prisme-erreur.log », à côté du
programme, et une fenêtre la montre quand elle le peut.

Après le démarrage, ce sont les erreurs de la séance qui se perdaient : lancé
par pythonw, Prisme n'a pas de console, et une exception dans un clic
disparaissait sans trace -- l'interface restait à moitié à jour. Elles vont
désormais dans « plantage.log » (avec la dernière action notée par le chien
de garde), un plantage dur y laisse la pile de chaque fil, et un bandeau le dit.
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path


def _report_to() -> Path:
    """À côté du programme, sinon dans le dossier courant."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "prisme-erreur.log"
    return Path(__file__).resolve().parent / "prisme-erreur.log"


def _report(problem: BaseException) -> None:
    """Écrit ce qui s'est passé, puis tente de le dire à l'écran."""
    text = "".join(traceback.format_exception(problem))
    line = (f"--- {datetime.now():%d/%m %H:%M:%S} ---\n"
            f"Python {sys.version}\n"
            f"Programme : {sys.executable}\n"
            f"Gelé : {getattr(sys, 'frozen', False)}\n\n{text}\n")
    try:
        with open(_report_to(), "a", encoding="utf-8-sig") as out:
            out.write(line)
    except OSError:
        pass
    # Une fenêtre vaut mieux qu'un fichier qu'on ne pense pas à ouvrir — mais
    # si Qt est justement ce qui manque, il ne faut pas échouer une seconde fois.
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(
            None, "Prisme n'a pas pu démarrer",
            f"{type(problem).__name__} : {problem}\n\n"
            f"Le détail est dans :\n{_report_to()}")
    except Exception:                                  # noqa: BLE001
        if sys.stderr is not None:
            print(line, file=sys.stderr)


def _read_setting(name: str) -> bool:
    """Lit un reglage avant que Qt ne demarre.

    La mise a l'echelle se decide a la toute premiere ligne, bien avant que
    la configuration ne soit chargee comme il faut : on va donc la lire a la
    main, et l'on se passe d'elle si quoi que ce soit resiste.
    """
    import json
    try:
        from videosorter.config import CONFIG_PATH
        return bool(json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get(name))
    except Exception:                                  # noqa: BLE001
        return False


# Le decodage video par la carte graphique : Qt cree un peripherique Direct3D
# pour chaque video ouverte, sur le fil de l'interface. C'etait l'essentiel du
# gel de 0,13 a 0,5 s a chaque apercu survole sur la planche. Le processeur
# decode sans ce prix-la. `"hw_decoding": true` dans config.json le rend a la
# carte graphique, pour une machine trop juste en 4K.
DECODING_VARIABLE = "QT_FFMPEG_DECODING_HW_DEVICE_TYPES"


def _read_text(name: str, default: str = "") -> str:
    import json
    try:
        from videosorter.config import CONFIG_PATH
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get(name)
        return str(value) if value else default
    except Exception:                                  # noqa: BLE001
        return default


def _pick_decoding(environ, hardware: bool) -> None:
    """Avant tout import de QtMultimedia : Qt lit ce choix une fois pour toutes.
    Un choix pose a la main dans l'environnement l'emporte."""
    if not hardware:
        environ.setdefault(DECODING_VARIABLE, ",")


def _leave_now_if_stuck(app, window, code: int, wait: float = 2.0) -> None:
    """Apres la fermeture, ne laisse pas un fil de fond detruire le processus.

    Un fil qui n'a pas entendu l'arret (un ffmpeg lent, une lecture du NAS
    qui ne revient pas) survivait a la fenetre : detruit encore en marche,
    Qt arrete le programme en catastrophe, et d'ici la le processus garde le
    verrou -- relancer Prisme repondait « deja ouvert ». Tout ce qui compte
    est deja ecrit (reglages, favoris, index ferme) : au-dela d'un court
    delai, on s'en va. Sauf un transfert encore en vol, qu'on laisse finir
    comme avant : couper une copie laisserait un dossier a moitie deplace.
    """
    import os
    import time
    try:
        from PySide6.QtCore import QThread
        if window.transfers.busy:
            return
    except (AttributeError, RuntimeError, ImportError):
        return

    def running() -> list:
        try:
            return [type(thread).__name__ for thread in window.findChildren(QThread)
                    if thread.isRunning()]
        except RuntimeError:
            return []

    deadline = time.monotonic() + wait
    while running():
        if time.monotonic() >= deadline:
            crash = getattr(app, "_prisme_crash", None)
            if crash is not None:
                crash.write(f"--- fermeture forcee apres {wait:.0f} s : "
                            f"{', '.join(running()[:6])} ---\n")
            try:
                app._prisme_lock.unlock()
            except (AttributeError, RuntimeError):
                pass
            os._exit(code)
        time.sleep(0.05)


def _leave_now(app, window, code: int) -> None:
    """La fenetre fermee, le processus s'en va -- vraiment.

    `_leave_now_if_stuck` ne guette que les fils Qt de la fenetre. Restaient
    ceux qu'il ne voit pas : un groupe de lectures du NAS (les rayonnages se
    lisent de front), un apercu de la reserve commune, un fil Python ordinaire.
    La sortie normale de Python les attend tous, sans limite : la fenetre
    avait disparu, le processus restait, invisible, avec le verrou -- et
    chaque relance repondait « Prisme finit de se fermer ». Tout ce qui compte
    est ecrit par `closeEvent` (reglages, favoris, index referme, corbeille) :
    on s'en va sans attendre personne. Sauf un transfert encore en vol.
    """
    import os
    try:
        if window.transfers.busy:
            return
    except (AttributeError, RuntimeError):
        return
    try:
        app._prisme_lock.unlock()
    except (AttributeError, RuntimeError):
        pass
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except (AttributeError, OSError, ValueError):
        pass
    os._exit(code)


# ---------------------------------------------------------------------------
# Les erreurs de la séance
# ---------------------------------------------------------------------------
class _CrashLog:
    """Écrit chaque erreur de la séance, et prévient la fenêtre une fois.

    Chaque type d'erreur n'est annoncé qu'une fois par séance, et jamais plus
    d'un bandeau toutes les dix secondes : une erreur qui se répète à chaque
    image ne doit pas couvrir l'écran.
    """

    def __init__(self, path: Path):
        self.path = path
        self.file = None
        self.window = None
        self.notifier = None
        self._told: set = set()
        self._last_told = 0.0

    def open(self) -> None:
        import faulthandler
        import threading
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not self.path.exists() or self.path.stat().st_size == 0
            self.file = open(self.path, "a", buffering=1,
                             encoding="utf-8-sig" if fresh else "utf-8")
        except OSError:
            return
        # Le fichier est indispensable : sous pythonw, il n'y a pas de sortie
        # d'erreur, et `enable()` sans lui echouerait.
        try:
            faulthandler.enable(file=self.file, all_threads=True)
        except (RuntimeError, ValueError, OSError):
            pass
        sys.excepthook = self._excepthook
        threading.excepthook = self._thread_hook
        try:
            from PySide6.QtCore import QtMsgType, qInstallMessageHandler
            self._qt_levels = {QtMsgType.QtCriticalMsg: "Qt critique",
                               QtMsgType.QtFatalMsg: "Qt fatal"}
            qInstallMessageHandler(self._qt_message)
        except ImportError:
            pass

    def attach(self, window) -> None:
        """Les bandeaux passent désormais par cette fenêtre."""
        from PySide6.QtCore import QObject, Signal

        class _Notifier(QObject):
            # Un signal, et non un appel direct : l'erreur peut venir d'un
            # autre fil, et seul celui de l'interface a le droit d'afficher.
            said = Signal(str)

        self.window = window
        self.notifier = _Notifier()
        self.notifier.said.connect(self._show)

    def _show(self, text: str) -> None:
        try:
            if self.window is not None:
                self.window.show_banner(text, "error")
        except Exception:                               # noqa: BLE001
            pass

    def write(self, text: str) -> None:
        if self.file is None:
            return
        try:
            self.file.write(text)
            self.file.flush()
        except (OSError, ValueError):
            pass

    def _last_action(self) -> str:
        try:
            from videosorter.perf import WATCH
            return WATCH.last_action
        except Exception:                               # noqa: BLE001
            return "?"

    def _record(self, kind, value, tb, where: str = "") -> None:
        import time
        text = "".join(traceback.format_exception(kind, value, tb))
        self.write(f"--- {datetime.now():%d/%m %H:%M:%S}{where} — dernière action : "
                   f"{self._last_action()} ---\n{text}\n")
        # Une seule annonce par erreur distincte : son type et l'endroit.
        frame = traceback.extract_tb(tb)[-1] if tb is not None else None
        key = (kind.__name__, frame.filename if frame else "", frame.lineno if frame else 0)
        now = time.monotonic()
        if key in self._told or now - self._last_told < 10 or self.notifier is None:
            return
        self._told.add(key)
        self._last_told = now
        try:
            self.notifier.said.emit(
                f"Erreur interne ({kind.__name__}) : le détail est dans {self.path}")
        except RuntimeError:
            pass

    def _excepthook(self, kind, value, tb) -> None:
        try:
            self._record(kind, value, tb)
        except Exception:                               # noqa: BLE001
            pass

    def _thread_hook(self, args) -> None:
        if args.exc_type is SystemExit:
            return
        try:
            name = getattr(args.thread, "name", "?")
            self._record(args.exc_type, args.exc_value, args.exc_traceback,
                         f" (fil {name})")
        except Exception:                               # noqa: BLE001
            pass

    def _qt_message(self, mode, context, message) -> None:
        level = self._qt_levels.get(mode)
        if level is None:
            # Les avertissements ordinaires restent ou ils allaient : la
            # console, quand il y en a une.
            if sys.stderr is not None:
                try:
                    print(message, file=sys.stderr)
                except (OSError, ValueError):
                    pass
            return
        self.write(f"--- {datetime.now():%d/%m %H:%M:%S} {level} — dernière action : "
                   f"{self._last_action()} ---\n{message}\n\n")


# ---------------------------------------------------------------------------
# Un seul Prisme, et le second ramène le premier
# ---------------------------------------------------------------------------
def _server_name(lock_path: Path) -> str:
    import hashlib
    digest = hashlib.sha1(str(lock_path).lower().encode("utf-8")).hexdigest()[:12]
    return f"prisme-{digest}"


def _wake_other(name: str) -> bool:
    """Demande au Prisme déjà ouvert de passer devant. Vrai s'il a répondu.

    Relancer, c'est d'abord vouloir retrouver sa fenêtre : un message « déjà
    ouvert » obligeait à la chercher. Le premier répond « ok » ; s'il est
    occupé à se fermer, il ne répond pas, et l'on attend son verrou.
    """
    from PySide6.QtNetwork import QLocalSocket
    if sys.platform == "win32":
        try:
            import ctypes
            # Sans cette permission, Windows interdit au premier de passer
            # au premier plan : il clignoterait seulement dans la barre.
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except (OSError, AttributeError):
            pass
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(500):
        return False
    socket.write(b"montre\n")
    socket.flush()
    answered = socket.waitForReadyRead(1500) and bytes(socket.readAll()).startswith(b"ok")
    socket.disconnectFromServer()
    return answered


def _serve_wake(app, window, name: str):
    """Écoute les Prisme lancés après celui-ci, et ramène la fenêtre au premier plan."""
    from PySide6.QtCore import Qt
    from PySide6.QtNetwork import QLocalServer

    server = QLocalServer(app)
    QLocalServer.removeServer(name)
    if not server.listen(name):
        return None

    def woken() -> None:
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if window.isVisible():
                window.setWindowState(
                    (window.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
                window.show()
                window.raise_()
                window.activateWindow()
                socket.write(b"ok\n")
                socket.flush()
            socket.disconnected.connect(socket.deleteLater)

    server.newConnection.connect(woken)
    return server


def _splash(app, text: str):
    """Un mot à l'écran tout de suite : sans lui, deux à quatre secondes de
    rien après le double-clic, et l'on recliquait."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
    from PySide6.QtWidgets import QSplashScreen

    from PySide6.QtGui import QImage

    pixmap = QPixmap(420, 140)
    pixmap.fill(QColor("#14161a"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    try:
        # Le logo : le P prismatique et le lettrage PRISME, cote a cote.
        from videosorter.brand_data import LETTRAGE_PNG, P_MARK_PNG
        mark = QImage.fromData(P_MARK_PNG, "PNG").scaledToHeight(64, Qt.SmoothTransformation)
        word = QImage.fromData(LETTRAGE_PNG, "PNG").scaledToHeight(26, Qt.SmoothTransformation)
        left = (pixmap.width() - mark.width() - 14 - word.width()) // 2
        painter.drawImage(left, 14, mark)
        painter.drawImage(left + mark.width() + 14, 14 + (64 - word.height()) // 2, word)
    except Exception:                                   # noqa: BLE001
        painter.setPen(QColor("#e6e8ea"))
        title = QFont()
        title.setPointSize(22)
        title.setBold(True)
        painter.setFont(title)
        painter.drawText(pixmap.rect().adjusted(0, 22, 0, -60), Qt.AlignHCenter, "Prisme")
    body = QFont()
    body.setPointSize(10)
    painter.setFont(body)
    painter.setPen(QColor("#9aa3ad"))
    painter.drawText(pixmap.rect().adjusted(0, 84, 0, -20), Qt.AlignHCenter, text)
    painter.end()
    splash = QSplashScreen(pixmap)
    splash.show()
    app.processEvents()
    return splash


# ---------------------------------------------------------------------------
# Le ramasse-miettes
# ---------------------------------------------------------------------------
class _Gardener:
    """Tient la collection hors des passes complètes du ramasse-miettes.

    Une passe complète examine chaque objet vivant : avec cent mille vidéos
    en mémoire (1,4 million d'objets suivis), un quart de seconde à près d'une
    seconde de gel, à des moments imprévisibles. Geler ce qui existe déjà
    (`gc.freeze`) le retire de ces passes, pour un coût nul : ce sont des
    objets qui durent, et sans cycle -- le compteur de références suffit à les
    libérer quand ils sont remplacés.

    Contrepartie : un cycle devenu inutile après avoir été gelé attend. On
    fait donc un vrai ménage quand l'utilisateur regarde ailleurs (fenêtre
    inactive), jamais pendant qu'il trie.
    """

    FREEZE_EVERY_MS = 30_000
    CLEAN_AFTER_S = 600

    def __init__(self, app):
        import gc
        import time
        from PySide6.QtCore import QTimer
        self.gc = gc
        self.time = time
        self.cleaned = time.monotonic()
        self.timer = QTimer(app)
        self.timer.setInterval(self.FREEZE_EVERY_MS)
        self.timer.timeout.connect(self.freeze)
        self.timer.start()
        QTimer.singleShot(3000, self.freeze)
        app.applicationStateChanged.connect(self._state)

    def freeze(self) -> None:
        # Les jeunes générations d'abord, en quelques millisecondes : on ne
        # gèle pas les déchets de l'instant.
        self.gc.collect(1)
        self.gc.freeze()

    def _state(self, state) -> None:
        from PySide6.QtCore import Qt
        if state == Qt.ApplicationActive:
            return
        if self.time.monotonic() - self.cleaned < self.CLEAN_AFTER_S:
            return
        self.cleaned = self.time.monotonic()
        self.gc.unfreeze()
        self.gc.collect()
        self.gc.freeze()


def run() -> int:
    import os

    # Avant toute chose : Qt fige sa politique d'echelle des sa creation.
    # A 200 %, l'application suit Windows et devient deux fois plus grande —
    # ce qui est juste sur un ecran lointain, mais fait deborder la fenetre
    # d'un petit ecran. Ce reglage lui fait ignorer l'agrandissement.
    if _read_setting("ignore_dpi"):
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = "1"
    from videosorter.config import wants_hardware
    _pick_decoding(os.environ, wants_hardware(_read_text("decoding", "auto"),
                                              _read_setting("hw_decoding"),
                                              os.cpu_count() or 0))

    # Prisme se presente a Windows sous son propre nom : sans cela, sa
    # fenetre se rangeait avec « Python » dans la barre des taches, et en
    # prenait l'icone au lieu de la sienne.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Prisme.App")
        except (OSError, AttributeError):
            pass

    from PySide6.QtCore import QCoreApplication, QLockFile, Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from videosorter.config import ADOPTED, APP_NAME, CRASH_LOG, LOCK_PATH, Config

    # Le navigateur invisible de la recherche web (charge seulement quand on
    # s'en sert) exige ce reglage avant la creation de l'application.
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # Un seul Prisme a la fois sur les memes donnees, et on le verifie AVANT
    # d'ouvrir l'index : deux fenetres qui ecrivent dans la meme base se
    # genent, et c'est dans cette bousculade qu'un index a ete perdu. Le
    # verrou vit a cote de l'index qu'il protege, jamais sur un partage.
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    name = _server_name(LOCK_PATH)
    lock = QLockFile(str(LOCK_PATH))
    splash = None
    if not lock.tryLock(200):
        if _wake_other(name):
            return 0
        # Pas de reponse : l'autre est en train de se fermer (il range son
        # index, vide la corbeille). On l'attend un moment plutot que de
        # refuser tout net -- relancer juste apres avoir ferme est courant.
        splash = _splash(app, "Prisme finit de se fermer… un instant.")
        acquired = False
        for _ in range(150):
            if lock.tryLock(100):
                acquired = True
                break
            app.processEvents()
        if not acquired:
            splash.close()
            QMessageBox.information(
                None, APP_NAME,
                "Prisme est déjà ouvert.\n\nFermez l'autre fenêtre (ou attendez "
                "qu'elle ait fini de se fermer), puis relancez.")
            return 0
    app._prisme_lock = lock          # garde le verrou tant que l'application vit

    if splash is not None:
        splash.close()
    splash = _splash(app, "Ouverture…")
    crash = _CrashLog(CRASH_LOG)
    crash.open()
    app._prisme_crash = crash

    from videosorter.media import Tools
    from videosorter.widgets import app_icon
    from videosorter.window import MainWindow, check_tools

    app.setWindowIcon(app_icon())

    cfg = Config()
    Tools.resolve(cfg)

    # L'essai fini sans licence valable : la cle d'abord (« Quitter » ne
    # touche a rien). Version de developpement (sans cle publique) : rien.
    from videosorter.licence_dialog import gate
    if not gate(cfg, before=splash.close):
        return 0

    window = MainWindow(cfg)
    window.show()
    splash.finish(window)
    # La derniere racine s'ouvre d'elle-meme, sur « Dossiers » : l'accueil ne
    # sert plus qu'a la premiere fois, ou si elle a disparu.
    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, window.open_at_launch)
    crash.attach(window)
    app._prisme_server = _serve_wake(app, window, name)
    app._prisme_gardener = _Gardener(app)
    if ADOPTED:
        window.show_banner(
            f"Cache repris depuis {ADOPTED} : vignettes, index et réglages "
            "sont conservés.", "done")
    if cfg.problem:
        # Un fichier de reglages abime n'est plus remplace en silence.
        window.show_banner(cfg.problem, "error")

    if not check_tools(window):
        return 1

    # Une fermeture de session Windows ne passe pas toujours par la fenetre :
    # ce que l'index a appris est ecrit quoi qu'il arrive.
    from videosorter.index import INDEX
    app.aboutToQuit.connect(lambda: INDEX.commit(force=True))
    code = app.exec()
    _leave_now_if_stuck(app, window, code)
    _leave_now(app, window, code)
    return code


def main() -> int:
    from videosorter.engine import ensure_streams
    ensure_streams()
    try:
        return run()
    except BaseException as problem:                   # noqa: BLE001
        # Y compris les erreurs d'import : c'est là que se logent les greffons
        # Qt manquants et les bibliothèques système absentes.
        if isinstance(problem, SystemExit):
            raise
        _report(problem)
        return 2


if __name__ == "__main__":
    # La recherche web tourne dans un processus a part (videosorter/engine.py) :
    # dans le programme empaquete, ce processus repasse par ici et doit
    # devenir le moteur de recherche, pas un second Prisme.
    import multiprocessing
    multiprocessing.freeze_support()
    if "--verifier" in sys.argv:
        # Le bilan de sante du programme (Prisme-diagnostic.exe --verifier).
        from videosorter.selftest import run as verify
        sys.exit(verify(sys.argv))
    sys.exit(main())
