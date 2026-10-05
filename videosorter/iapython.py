"""Le Python du Labo IA, pour que le labo marche aussi dans le programme vendu.

Depuis les sources, le labo installe ses bibliotheques (torch, open_clip,
transformers…) dans le Python qui fait tourner Prisme, et ses calculs vivent
dans un processus fils (multiprocessing). Le programme empaquete, portable ou
installe, n'a ni pip ni Python a lui : rien ne s'y installe, et un processus
fils n'y est qu'une seconde copie de Prisme.exe -- sans torch.

Ici, Prisme se procure donc un Python a lui : le Python « embarquable »
officiel (une archive d'une dizaine de Mo, sur python.org), pose dans
%LOCALAPPDATA%\\Prisme\\ia, avec pip, puis les bibliotheques du labo. Les
calculs du labo (le moteur CLIP, la reconnaissance vocale) tournent dans ce
Python-la. Les deux processus se parlent par une connexion locale
(127.0.0.1, cle secrete) qui imite les files de multiprocessing : le code
des moteurs (`ia._clip_child`, `voice_model.voice_child`) ne voit pas la
difference.

Les sources de Prisme voyagent dans le programme (dossier « prisme-src ») :
le Python du labo les importe telles quelles.

Depuis les sources, rien ne change -- sauf si PRISME_IA_PYTHON designe un
autre Python : on essaie ainsi le chemin du programme vendu sans empaqueter.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from . import config

FROZEN = bool(getattr(sys, "frozen", False))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
# Le Python de ce programme d'abord (memes habitudes), puis des versions
# connues, au cas ou python.org n'aurait pas l'archive de celle-ci.
FALLBACK_VERSIONS = ("3.13.12", "3.12.10")
EMBED_URL = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
# Le temps laisse au Python du labo pour demarrer et se presenter.
START_TIMEOUT = 120.0


# -- ou il vit -------------------------------------------------------------------------

def external() -> bool:
    """Le labo passe-t-il par un Python a part ? Dans le programme empaquete,
    toujours ; depuis les sources, seulement si PRISME_IA_PYTHON le demande."""
    return FROZEN or bool(os.environ.get("PRISME_IA_PYTHON", "").strip())


def home() -> Path:
    """Le dossier du Python du labo : propre a la machine (la carte
    graphique, les pilotes), donc jamais dans un dossier portable."""
    told = os.environ.get("PRISME_IA_HOME", "").strip()
    if told:
        return Path(told)
    if config.SANDBOX:
        return Path(config.SANDBOX) / "ia"
    return config._LOCAL / config.APP_NAME / "ia"


def python() -> Path:
    told = os.environ.get("PRISME_IA_PYTHON", "").strip()
    return Path(told) if told else home() / "python" / "python.exe"


def site_packages() -> Path:
    return python().parent / "Lib" / "site-packages"


def prepared() -> bool:
    """Le Python du labo est-il la, avec pip ?"""
    return python().is_file() and (site_packages() / "pip").is_dir()


def sources() -> Path:
    """Le dossier d'ou le Python du labo importe `videosorter`."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "prisme-src"
    return Path(__file__).resolve().parent.parent


# -- ce qui est installe ---------------------------------------------------------------

def has(module: str) -> bool:
    """Ce module est-il installe pour le labo ? Sans rien importer."""
    if not external():
        importlib.invalidate_caches()
        return importlib.util.find_spec(module) is not None
    base = site_packages()
    return (base / module / "__init__.py").is_file() or (base / f"{module}.py").is_file()


def version(distribution: str) -> str:
    """La version installee d'un paquet pip, pour le labo ; « » s'il manque."""
    from importlib import metadata
    if not external():
        try:
            return metadata.version(distribution)
        except Exception:                                   # noqa: BLE001
            return ""
    wanted = distribution.lower().replace("_", "-")
    try:
        for found in metadata.distributions(path=[str(site_packages())]):
            name = (found.metadata["Name"] or "").lower().replace("_", "-")
            if name == wanted:
                return found.version
    except Exception:                                       # noqa: BLE001
        pass
    return ""


# -- la preparation ---------------------------------------------------------------------

def _versions() -> list:
    here = "%d.%d.%d" % sys.version_info[:3]
    return [here] + [v for v in FALLBACK_VERSIONS if v != here]


def _download(url: str, target: Path, log) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Prisme"})
    with urllib.request.urlopen(request, timeout=60) as answer, open(target, "wb") as out:
        total = int(answer.headers.get("Content-Length") or 0)
        done = told = 0
        while True:
            block = answer.read(1 << 16)
            if not block:
                break
            out.write(block)
            done += len(block)
            if total and done * 10 // total > told:
                told = done * 10 // total
                log(f"  {told * 10} %")


def _stream(argv: list, log) -> int:
    """Lance une commande sans fenetre et raconte sa sortie, ligne a ligne."""
    with _clean_dll_path():
        process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                                   cwd=str(home()), env=_child_env())
    for raw in process.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        if line:
            log(line)
    return process.wait()


def prepare(log) -> None:
    """Pose le Python du labo : l'archive officielle, site-packages, pip.
    Une ou deux minutes : dans un fil. `log(ligne)` raconte."""
    if prepared():
        log("Le Python du labo est déjà prêt.")
        return
    if os.environ.get("PRISME_IA_PYTHON", "").strip():
        raise RuntimeError(f"PRISME_IA_PYTHON ({python()}) n'a pas pip.")
    target = python().parent
    work = target.with_name(target.name + ".part")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    archive = work / "python.zip"
    problem = None
    for wanted in _versions():
        log(f"Téléchargement de Python {wanted} (une dizaine de Mo)…")
        try:
            _download(EMBED_URL.format(v=wanted), archive, log)
            break
        except OSError as exc:
            problem = exc
            log(f"  indisponible : {exc}")
    else:
        raise RuntimeError(f"Python introuvable sur python.org ({problem}). "
                           "Vérifiez la connexion à Internet.")
    with zipfile.ZipFile(archive) as packed:
        packed.extractall(work)
    archive.unlink()
    # Le Python embarquable ignore site-packages tant qu'on ne le lui dit
    # pas : sans cela, pip et tout ce qu'il installe resteraient invisibles.
    for pth in work.glob("python*._pth"):
        lines = pth.read_text(encoding="utf-8").splitlines()
        lines = ["import site" if line.strip() == "#import site" else line for line in lines]
        if "Lib\\site-packages" not in lines:
            lines.insert(0, "Lib\\site-packages")
        if "import site" not in lines:
            lines.append("import site")
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("Installation de pip…")
    _download(GET_PIP_URL, work / "get-pip.py", log)
    if target.exists():
        shutil.rmtree(target)
    work.rename(target)
    code = _stream([str(python()), str(target / "get-pip.py"), "--no-warn-script-location",
                    "--disable-pip-version-check"], log)
    if code != 0 or not prepared():
        raise RuntimeError(f"pip ne s'est pas installé (code {code}).")
    log("Python du labo prêt.")


class PrepareStep:
    """L'etape « preparer le Python » de l'installation du labo : un appel
    Python plutot qu'une commande (labo._next_command les distingue)."""

    label = "Préparation du Python du labo (une fois)…"

    def __call__(self, log) -> None:
        prepare(log)


# -- les processus du labo ---------------------------------------------------------------

def _child_env() -> dict:
    """L'environnement du Python du labo. Un bac a sable a lui : en important
    `videosorter`, il ne doit jamais toucher l'index ni les reglages."""
    env = dict(os.environ)
    sandbox = home() / "fil"
    sandbox.mkdir(parents=True, exist_ok=True)
    env["PRISME_SANDBOX"] = str(sandbox)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
        if FROZEN:
            env.pop(key, None)
    return env


_DLL_LOCK = threading.Lock()


class _clean_dll_path:
    """Le programme empaquete ajoute son dossier aux chemins des DLL, et un
    processus lance de la en herite : le Python du labo y aurait trouve les
    DLL de Prisme au lieu des siennes. On l'efface le temps du lancement."""

    def __enter__(self):
        _DLL_LOCK.acquire()
        self._reset = False
        if FROZEN and os.name == "nt":
            try:
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(None)
                self._reset = True
            except Exception:                               # noqa: BLE001
                pass
        return self

    def __exit__(self, *_exc):
        try:
            if self._reset:
                import ctypes
                ctypes.windll.kernel32.SetDllDirectoryW(getattr(sys, "_MEIPASS", None))
        finally:
            _DLL_LOCK.release()
        return False


class _Process:
    """Un subprocess.Popen avec les manieres d'un multiprocessing.Process."""

    def __init__(self, popen: subprocess.Popen, name: str):
        self._popen = popen
        self.name = name

    def is_alive(self) -> bool:
        return self._popen.poll() is None

    def join(self, timeout: float | None = None) -> None:
        try:
            self._popen.wait(timeout)
        except subprocess.TimeoutExpired:
            pass

    def terminate(self) -> None:
        try:
            self._popen.kill()
        except OSError:
            pass


class _Line:
    """Une file de multiprocessing, vue d'un bout de la connexion.

    Cote Prisme, une connexion coupee vaut une file vide : l'appelant voit
    ensuite que le processus est mort, et le dit. Cote labo, elle vaut fin :
    `EOFError`, comme une file dont l'autre bout a disparu."""

    def __init__(self, connection, lock: threading.Lock, prisme_side: bool):
        self._connection = connection
        self._lock = lock
        self._prisme_side = prisme_side

    def put(self, value) -> None:
        with self._lock:
            self._connection.send(value)

    def get(self, timeout: float | None = None):
        try:
            if not self._connection.poll(timeout):
                raise queue.Empty
            return self._connection.recv()
        except (EOFError, OSError):
            if self._prisme_side:
                time.sleep(min(timeout or 0, 0.5))
                raise queue.Empty from None
            raise EOFError from None


_BOOT = ("import sys; sys.path.insert(0, sys.argv[1]); "
         "from videosorter.iapython import _child_main; _child_main()")


def _log_file(name: str):
    folder = home()
    folder.mkdir(parents=True, exist_ok=True)
    return open(folder / f"{name}.log", "ab")


def spawn(target, args: tuple, name: str) -> tuple:
    """(processus, demandes, reponses) : comme un multiprocessing.Process
    qui ferait target(*args, demandes, reponses). `target` doit vivre dans un
    module sans Qt : le Python du labo n'a pas PySide6."""
    if not external():
        import multiprocessing
        context = multiprocessing.get_context("spawn")
        requests, answers = context.Queue(), context.Queue()
        process = context.Process(target=target, daemon=True,
                                  args=tuple(args) + (requests, answers), name=name)
        process.start()
        return process, requests, answers
    if not python().is_file():
        raise RuntimeError("Le Python du labo n'est pas installé : ⋯ › Collection › Labo IA, "
                           "« Installer les bibliothèques ».")
    from multiprocessing.connection import Listener
    key = secrets.token_bytes(32)
    listener = Listener(("127.0.0.1", 0), authkey=key)
    env = _child_env()
    env["PRISME_IA_KEY"] = key.hex()
    argv = [str(python()), "-c", _BOOT, str(sources()), str(listener.address[1]),
            str(os.getpid()), f"{target.__module__}:{target.__name__}", json.dumps(list(args))]
    with _log_file(name) as log, _clean_dll_path():
        popen = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 cwd=str(home()), env=env,
                                 creationflags=NO_WINDOW | BELOW_NORMAL)
    box: dict = {}

    def accept() -> None:
        try:
            box["connection"] = listener.accept()
        except Exception as exc:                            # noqa: BLE001
            box["error"] = exc
    waiter = threading.Thread(target=accept, daemon=True, name=f"{name}-accueil")
    waiter.start()
    deadline = time.monotonic() + START_TIMEOUT
    while waiter.is_alive() and popen.poll() is None and time.monotonic() < deadline:
        waiter.join(0.25)
    listener.close()
    connection = box.get("connection")
    if connection is None:
        try:
            popen.kill()
        except OSError:
            pass
        raise RuntimeError(f"Le Python du labo n'a pas démarré (voir {home() / (name + '.log')}).")
    lock = threading.Lock()
    return (_Process(popen, name), _Line(connection, lock, True), _Line(connection, lock, True))


def call(target, args: tuple = (), name: str = "prisme-labo-tache") -> None:
    """Fait `target(*args)` dans le Python du labo, et attend. Une erreur
    revient en RuntimeError, avec la fin de ce qu'il a ecrit."""
    if not external():
        target(*args)
        return
    boot = ("import sys, json, importlib; sys.path.insert(0, sys.argv[1]); "
            "module, name = sys.argv[2].split(':'); "
            "getattr(importlib.import_module(module), name)(*json.loads(sys.argv[3]))")
    argv = [str(python()), "-c", boot, str(sources()),
            f"{target.__module__}:{target.__name__}", json.dumps(list(args))]
    with _clean_dll_path():
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                              cwd=str(home()), env=_child_env(), creationflags=NO_WINDOW)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else f"échec (code {done.returncode})")


# -- cote Python du labo ---------------------------------------------------------------

def echo_child(requests, answers) -> None:
    """Un correspondant qui repond ce qu'on lui dit : le bilan de sante
    (selftest) verifie ainsi le chemin jusqu'au Python du labo."""
    while True:
        try:
            kind, payload = requests.get(timeout=2)
        except queue.Empty:
            continue
        except (EOFError, OSError):
            return
        if kind == "quit":
            return
        answers.put(("ok", {"payload": payload, "python": sys.version.split()[0],
                            "executable": sys.executable}))


def _watch_parent(pid: int) -> None:
    """Le Python du labo s'arrete avec Prisme, meme occupe a calculer."""
    if os.name != "nt":
        return
    import ctypes
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        os._exit(0)

    def watch() -> None:
        ctypes.windll.kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        os._exit(0)
    threading.Thread(target=watch, daemon=True, name="prisme-labo-garde").start()


def _child_main() -> None:
    from multiprocessing.connection import Client
    port, parent, target, args = (int(sys.argv[2]), int(sys.argv[3]), sys.argv[4],
                                  json.loads(sys.argv[5]))
    _watch_parent(parent)
    connection = Client(("127.0.0.1", port), authkey=bytes.fromhex(os.environ.pop("PRISME_IA_KEY")))
    module, name = target.split(":")
    work = getattr(importlib.import_module(module), name)
    lock = threading.Lock()
    work(*args, _Line(connection, lock, False), _Line(connection, lock, False))
