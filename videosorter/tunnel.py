"""Le tunnel : une adresse publique, sans ouvrir un port ni quitter Prisme.

Le serveur n'écoute que la machine elle-même. Pour qu'un téléphone au bout du
monde le joigne, il faut un intermédiaire qui sorte *depuis* la maison et
tienne la porte ouverte de l'extérieur. C'est ce que fait `cloudflared` : il
se connecte à Cloudflare, reçoit une adresse en `https`, et renvoie ce qui y
arrive vers le serveur local. Aucun port n'est ouvert sur la box, et la
liaison est chiffrée de bout en bout.

Tout se fait ici, depuis l'application : trouver le programme, l'installer
s'il manque, l'ouvrir, lire l'adresse qu'il annonce, et le refermer.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Signal

# Windows n'ouvre pas de fenetre noire quand on lance ces programmes.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Chercher un programme parcourt tout le PATH : vingt a cinquante
# millisecondes, que la fenetre du partage payait toutes les quatre secondes.
# Un programme trouve est garde (on verifie seulement qu'il est encore la) ;
# une absence est redemandee au bout de ce delai.
MISSING_TTL = 30.0
_FOUND: dict = {}
_FOUND_LOCK = threading.Lock()


def _located(key: str, search) -> str:
    now = time.monotonic()
    with _FOUND_LOCK:
        known = _FOUND.get(key)
    if known is not None:
        at, where = known
        if where and os.path.isfile(where):
            return where
        if not where and now - at < MISSING_TTL:
            return ""
    where = search()
    with _FOUND_LOCK:
        _FOUND[key] = (now, where)
    return where


def forget_found() -> None:
    """Oublie ce qu'on sait des programmes : apres une installation."""
    with _FOUND_LOCK:
        _FOUND.clear()


class Chore(QObject):
    """Une commande lente, lancee hors du fil de l'interface.

    Un fil Python, et non un QThread : un QThread detruit en marche fait
    tomber Prisme — fermer la fenetre du partage pendant une installation y
    suffisait —, alors qu'un fil Python s'eteint avec le programme sans rien
    casser. Le resultat revient par `done`, dans le fil de l'interface, et
    `then` s'y execute aussi : ce qui doit arriver meme si la fenetre qui a
    lance la commande a ete fermee entre-temps.
    """

    done = Signal(object)

    def __init__(self, work, parent=None, fallback=None, then=None):
        super().__init__(parent)
        self._work = work
        self._fallback = fallback
        self._then = then
        self._thread = None
        self.result = None
        self.done.connect(self._finish)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="prisme-corvee")
        self._thread.start()

    def isRunning(self) -> bool:                 # noqa: N802  (comme QThread)
        return self._thread is not None and self._thread.is_alive()

    def wait(self, timeout_ms: int = -1) -> bool:
        if self._thread is None:
            return True
        self._thread.join(None if timeout_ms < 0 else timeout_ms / 1000)
        return not self._thread.is_alive()

    def _run(self) -> None:
        try:
            result = self._work()
        except Exception:                                  # noqa: BLE001
            result = self._fallback
        self.result = result
        try:
            self.done.emit(result)
        except RuntimeError:
            # L'objet Qt est parti avec sa fenetre (Prisme se ferme) : il n'y
            # a plus personne a prevenir.
            pass

    def _finish(self, result) -> None:
        then, self._then = self._then, None
        try:
            if then is not None:
                then(result)
        finally:
            self.deleteLater()

# L'adresse que cloudflared annonce sur sa sortie d'erreur.
ADDRESS = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com")

# Le paquet winget, et les endroits ou il se pose.
PACKAGE = "Cloudflare.cloudflared"
LIKELY = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
    Path(os.environ.get("ProgramFiles", "")) / "cloudflared",
    Path(os.environ.get("ProgramW6432", "")) / "cloudflared",
)


def find() -> str:
    """Le chemin de cloudflared, ou une chaîne vide.

    Le PATH d'abord, puis les endroits où winget dépose ses programmes : une
    invite ouverte avant l'installation ne connaît pas encore le nouveau
    PATH, et l'application non plus.
    """
    return _located("cloudflared", _search_cloudflared)


def _search_cloudflared() -> str:
    found = shutil.which("cloudflared")
    if found:
        return found
    for folder in LIKELY:
        try:
            candidate = folder / "cloudflared.exe"
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def install(timeout: int = 600) -> tuple:
    """Installe cloudflared par winget. Rend (chemin, ce qu'il faut en dire)."""
    forget_found()
    if find():
        return find(), "cloudflared était déjà là."
    if not shutil.which("winget"):
        return "", ("winget est introuvable sur cette machine. Installez "
                    "cloudflared à la main, puis revenez ici.")
    try:
        done = subprocess.run(
            ["winget", "install", "--id", PACKAGE, "--silent",
             "--accept-source-agreements", "--accept-package-agreements"],
            capture_output=True, text=True, timeout=timeout,
            creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as trouble:
        return "", f"L'installation a échoué : {trouble}"
    forget_found()
    where = find()
    if where:
        return where, "cloudflared est installé."
    tail = (done.stdout or done.stderr or "").strip().splitlines()
    return "", ("L'installation s'est terminée, mais cloudflared reste "
                "introuvable. " + (tail[-1] if tail else ""))


class Tunnel(QObject):
    """Ouvre le tunnel et annonce l'adresse dès que Cloudflare la donne.

    S'il tombe en route, il se rouvre de lui-meme, de moins en moins souvent,
    et le dit : `failed` pour la chute, `ready` pour la nouvelle adresse.
    Une fermeture demandee, elle, ne s'annonce pas comme un echec.
    """

    ready = Signal(str)        # l'adresse publique
    failed = Signal(str)
    closed = Signal()

    # Delais des relances apres une chute ; au-dela, on s'arrete et on le dit.
    RETRY = (5, 20, 60, 300)

    def __init__(self, port: int, parent=None):
        super().__init__(parent)
        self.port = int(port)
        self.process = None
        self.address = ""
        self._reader = None
        self._lock = threading.Lock()
        self._stopping = False
        self._wake = threading.Event()
        self._waiting = False          # une relance est programmee
        self._retries = 0

    @property
    def running(self) -> bool:
        # Une relance en attente compte : sinon la fenetre, croyant le tunnel
        # mort, en ouvrirait un second a cote.
        process = self.process
        return (process is not None and process.poll() is None) or self._waiting

    def start(self) -> bool:
        where = find()
        if not where:
            self.failed.emit("cloudflared n'est pas installé.")
            return False
        if self.running:
            return True
        self._stopping = False
        self._wake.clear()
        self._retries = 0
        return self._spawn(where)

    def _spawn(self, where: str) -> bool:
        with self._lock:
            # Sous le verrou : un stop() arrive pendant une relance ne doit
            # pas laisser derriere lui un cloudflared que plus rien ne tient.
            if self._stopping:
                return False
            try:
                process = subprocess.Popen(
                    [where, "tunnel", "--no-autoupdate", "--url",
                     f"http://127.0.0.1:{self.port}"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=NO_WINDOW, bufsize=1)
            except OSError as trouble:
                self.process = None
                self.failed.emit(f"Le tunnel n'a pas démarré : {trouble}")
                return False
            self.process = process
            self.address = ""
        self._reader = threading.Thread(target=self._listen, args=(process,),
                                        daemon=True, name="prisme-tunnel")
        self._reader.start()
        return True

    def _listen(self, process) -> None:
        """Lit ce que dit cloudflared jusqu'à ce qu'il se taise.

        Il écrit l'adresse au milieu d'un encadrement de tirets, sur sa
        sortie d'erreur : c'est la seule façon de la connaître.
        """
        if process.stdout is None:
            return
        try:
            for line in process.stdout:
                if not self.address and process is self.process:
                    found = ADDRESS.search(line)
                    if found:
                        self.address = found.group(0)
                        self._retries = 0
                        self.ready.emit(self.address)
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.SubprocessError:
            pass
        if self._stopping or process is not self.process:
            # Fermeture demandee : la fenetre le sait deja. Annoncer ici
            # « ferme sans donner d'adresse » etait un faux echec.
            return
        self._dropped(bool(self.address))

    def _dropped(self, had_address: bool) -> None:
        """Le tunnel est tombe sans qu'on le lui demande."""
        self.address = ""
        if not had_address and not self._retries:
            # Il n'a jamais marche : le relancer ne changerait rien.
            self.failed.emit("Le tunnel s'est fermé sans donner d'adresse.")
            self.closed.emit()
            return
        if self._retries >= len(self.RETRY):
            self.failed.emit("L'adresse publique est tombée et ne se rouvre "
                             "pas. Rouvrez-la depuis « Partage à distance ».")
            self.closed.emit()
            return
        delay = self.RETRY[self._retries]
        self._retries += 1
        self._waiting = True
        self.failed.emit(f"L'adresse publique est tombée : nouvel essai dans "
                         f"{delay} s. Elle changera — il faudra la renvoyer.")
        self.closed.emit()
        woke = self._wake.wait(delay)
        self._waiting = False
        if woke or self._stopping:
            return
        where = find()
        if not where:
            self.failed.emit("cloudflared n'est plus là : l'adresse publique "
                             "reste fermée.")
            return
        self._spawn(where)

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            process, self.process = self.process, None
        self._wake.set()
        self.address = ""
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            try:
                process.kill()
            except OSError:
                pass


def qr_png(text: str, scale: int = 5) -> bytes:
    """Le code à scanner, en PNG. Vide si de quoi le dessiner manque.

    Taper une adresse de trente caractères sur un téléphone est une corvée,
    et une faute de frappe ne se voit pas. Le code se scanne avec l'appareil
    photo.
    """
    try:
        import io

        import segno
    except ImportError:
        return b""
    try:
        buffer = io.BytesIO()
        segno.make(text, error="m").save(
            buffer, kind="png", scale=scale, border=2,
            dark="#0b0d10", light="#ffffff")
        return buffer.getvalue()
    except Exception:                                  # noqa: BLE001
        return b""


# ---------------------------------------------------------------------------
# L'adresse fixe : Tailscale
# ---------------------------------------------------------------------------
#
# Cloudflare donne une adresse en quelques secondes, mais elle change a chaque
# ouverture : ses tunnels gratuits sont ephemeres, et une adresse permanente y
# demande un nom de domaine.
#
# Tailscale, lui, attache l'adresse a la machine : `nom.tailnet.ts.net`, en
# https, avec un vrai certificat, gratuitement et sans rien acheter. Elle est
# la meme demain. Le prix a payer est une mise en route : creer un compte, et
# autoriser le partage une premiere fois.

TAILSCALE_PACKAGE = "tailscale.tailscale"
TAILSCALE_LIKELY = (
    Path(os.environ.get("ProgramFiles", "")) / "Tailscale",
    Path(os.environ.get("ProgramW6432", "")) / "Tailscale",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
)
# Ce que Tailscale imprime quand le partage n'est pas encore autorise dans le
# compte : une adresse a ouvrir dans le navigateur, et tout est dit.
CONSENT = re.compile(r"https://login\.tailscale\.com/\S+")


# Une commande qui attend qu'on autorise dans le navigateur n'est pas
# abandonnee des que son lien est lu : elle finit le travail une fois
# l'autorisation donnee. Au-dela de ce delai, on la coupe.
CONSENT_WAIT = 900
# L'etat de Tailscale se redemande au plus toutes les trente secondes, et
# jamais depuis le fil de l'interface.
STATE_TTL = 30.0
NOT_LOGGED = "Tailscale est installé, mais aucun compte n'est connecté."
FIXED_CLOSED = "Adresse fixe fermée."


def find_fixed() -> str:
    """Le chemin de tailscale, ou une chaîne vide."""
    return _located("tailscale", _search_tailscale)


def _search_tailscale() -> str:
    found = shutil.which("tailscale")
    if found:
        return found
    for folder in TAILSCALE_LIKELY:
        try:
            candidate = folder / "tailscale.exe"
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def _run(args: list, timeout: int = 60) -> tuple:
    """Lance tailscale et rend (code, sortie, erreurs)."""
    where = find_fixed()
    if not where:
        return 1, "", ""
    try:
        done = subprocess.run(
            [where] + args, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
            encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as trouble:
        return 1, "", str(trouble)
    return done.returncode, done.stdout or "", done.stderr or ""


def _ask(args: list, timeout: int = 60) -> tuple:
    """Lance tailscale et rend (code, ce qu'il a dit)."""
    code, out, err = _run(args, timeout)
    return code, out + err


def _kill(process) -> None:
    try:
        process.kill()
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def _watch(args: list, timeout: int) -> tuple:
    """Lance tailscale en lisant ce qu'il dit au fil de l'eau.

    Rend (code, ce qu'il a dit, lien d'autorisation). `tailscale up` et
    `tailscale funnel` impriment un lien a ouvrir, puis ATTENDENT qu'on
    l'ait ouvert : les attendre jusqu'au bout avec un delai perdait le lien,
    et l'on affichait « timed out » a sa place. Le lien est rendu des qu'il
    parait (code None) ; la commande, elle, continue sans nous.
    """
    where = find_fixed()
    if not where:
        return 1, "", ""
    try:
        process = subprocess.Popen(
            [where] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", creationflags=NO_WINDOW)
    except OSError as trouble:
        return 1, str(trouble), ""
    lines: list = []
    link: list = []
    heard = threading.Event()

    def read() -> None:
        try:
            for line in process.stdout:
                lines.append(line)
                found = CONSENT.search(line)
                if found and not link:
                    link.append(found.group(0))
                    heard.set()
        except (OSError, ValueError):
            pass
        heard.set()

    threading.Thread(target=read, daemon=True, name="prisme-tailscale").start()
    heard.wait(timeout)
    if link:
        def reap() -> None:
            try:
                process.wait(timeout=CONSENT_WAIT)
            except subprocess.SubprocessError:
                _kill(process)
        threading.Thread(target=reap, daemon=True, name="prisme-tailscale-fin").start()
        return None, "".join(lines), link[0]
    try:
        code = process.wait(timeout=5 if heard.is_set() else 0.1)
    except subprocess.SubprocessError:
        _kill(process)
        said = "".join(lines).strip()
        return 1, (said + "\n" if said else "") + "Tailscale n'a pas répondu à temps.", ""
    return code, "".join(lines), ""


def install_fixed(timeout: int = 900) -> tuple:
    """Installe Tailscale par winget. Rend (chemin, ce qu'il faut en dire)."""
    forget_found()
    if find_fixed():
        return find_fixed(), "Tailscale était déjà là."
    if not shutil.which("winget"):
        return "", ("winget est introuvable. Installez Tailscale à la main, "
                    "puis revenez ici.")
    try:
        subprocess.run(
            ["winget", "install", "--id", TAILSCALE_PACKAGE, "--silent",
             "--accept-source-agreements", "--accept-package-agreements"],
            capture_output=True, text=True, timeout=timeout,
            creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as trouble:
        return "", f"L'installation a échoué : {trouble}"
    forget_found()
    where = find_fixed()
    return ((where, "Tailscale est installé.") if where else
            ("", "L'installation s'est terminée, mais Tailscale reste "
                 "introuvable. Un redémarrage peut être nécessaire."))


def _json_of(text: str):
    """Le premier objet JSON d'une sortie, ou None.

    Tailscale peut faire preceder son JSON d'un avertissement (version du
    client differente de celle du service) : on part de la premiere accolade.
    """
    start = (text or "").find("{")
    if start < 0:
        return None
    try:
        found = json.loads(text[start:])
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def fixed_name() -> str:
    """Le nom de cette machine dans le réseau Tailscale, ou rien.

    C'est lui qui donne l'adresse : `nom.tailnet.ts.net`. Il ne change pas.
    """
    code, out, _err = _run(["status", "--json"], timeout=20)
    told = _json_of(out)
    if told is None:
        return ""
    return ((told.get("Self") or {}).get("DNSName") or "").strip(".")


def _local_port(proxy: str) -> int:
    """Le port local vise par un relais Funnel : « http://127.0.0.1:8713 »."""
    proxy = proxy.strip()
    if proxy.isdigit():
        return int(proxy)
    try:
        return urlparse(proxy if "://" in proxy else "http://" + proxy).port or 0
    except ValueError:
        return 0


def _published() -> dict:
    """{port local: adresse publique} des partages Funnel en place.

    Un `funnel --bg` survit a Prisme : s'il a plante, l'adresse est restee
    publiee sans que rien ne le montre.
    """
    code, out, _err = _run(["funnel", "status", "--json"], timeout=20)
    told = _json_of(out)
    if code != 0 or told is None:
        return {}
    allowed = told.get("AllowFunnel") or {}
    found = {}
    for host_port, served in (told.get("Web") or {}).items():
        if not allowed.get(host_port):
            continue
        host = host_port[:-4] if host_port.endswith(":443") else host_port
        for handler in ((served or {}).get("Handlers") or {}).values():
            port = _local_port(str((handler or {}).get("Proxy") or ""))
            if port:
                found[port] = f"https://{host}"
    return found


def _probe_fixed() -> tuple:
    """(pret, ce qu'il faut en dire) — une seule commande, bloquante."""
    code, out, err = _run(["status", "--json"], timeout=20)
    told = _json_of(out)
    if told is None:
        said = (out + err).strip()
        low = said.lower()
        if "logged out" in low or "needslogin" in low:
            value = (False, NOT_LOGGED)
        else:
            value = (False, "Tailscale ne répond pas. " + said[:160])
    else:
        backend = str(told.get("BackendState") or "")
        name = ((told.get("Self") or {}).get("DNSName") or "").strip(".")
        if backend in ("NeedsLogin", "NoState"):
            value = (False, NOT_LOGGED)
        elif backend == "NeedsMachineAuth":
            value = (False, "Cette machine attend d'être approuvée dans la "
                            "console Tailscale.")
        elif backend == "Stopped":
            value = (False, "Tailscale est arrêté : « Connecter un compte "
                            "Tailscale » le relance.")
        elif not name:
            value = (False, "Tailscale est connecté, mais cette machine n'a "
                            "pas de nom.")
        else:
            value = (True, name)
    published = _published() if value[0] else {}
    with _STATE_LOCK:
        _STATE.update(at=time.monotonic(), value=value, published=published)
    return value


def _probe_quietly() -> None:
    try:
        _probe_fixed()
    finally:
        with _STATE_LOCK:
            _STATE["busy"] = False


_STATE_LOCK = threading.Lock()
_STATE: dict = {"at": 0.0, "value": None, "published": {}, "busy": False}


def forget_fixed() -> None:
    """L'etat a change (compte, partage) : on le redemandera."""
    with _STATE_LOCK:
        _STATE.update(at=0.0, published={})


def fixed_state(fresh: bool = False) -> tuple:
    """(prêt, ce qu'il faut en dire) — l'état de la mise en route.

    Sans `fresh`, rend tout de suite ce qu'on sait, et redemande en arriere-
    plan si c'est vieux : la fenetre du partage l'affiche toutes les quatre
    secondes, et chaque question a Tailscale pouvait la figer vingt secondes.
    """
    if not find_fixed():
        return False, "Tailscale n'est pas installé."
    if fresh:
        return _probe_fixed()
    with _STATE_LOCK:
        value = _STATE["value"]
        stale = value is None or time.monotonic() - _STATE["at"] > STATE_TTL
        launch = stale and not _STATE["busy"]
        if launch:
            _STATE["busy"] = True
    if launch:
        threading.Thread(target=_probe_quietly, daemon=True,
                         name="prisme-tailscale-etat").start()
    return value or (False, "Vérification de Tailscale…")


def fixed_published(port: int) -> str:
    """L'adresse fixe qui mene deja a ce port, d'apres le dernier etat lu."""
    with _STATE_LOCK:
        return _STATE["published"].get(int(port or 0), "")


def connect_fixed() -> tuple:
    """Demande la connexion à un compte Tailscale. Rend (lien, ce qu'il faut en dire).

    Bloquant : a lancer hors du fil de l'interface (voir `Chore`).
    """
    code, said, link = _watch(["up"], timeout=15)
    forget_fixed()
    if link:
        return link, "Ouvrez cette adresse pour connecter le compte."
    if code == 0:
        return "", "Le compte est connecté."
    return "", said.strip()[:200] or "Tailscale n'a pas répondu."


def open_fixed(port: int) -> tuple:
    """Publie le port sur l'adresse fixe. Rend (adresse, ce qu'il faut en dire).

    Une adresse peut manquer pour deux raisons : le compte n'est pas
    connecte, ou le partage public n'est pas encore autorise. Dans le second
    cas Tailscale imprime le lien qui l'autorise — on le rend des qu'il
    parait. Bloquant : a lancer hors du fil de l'interface.
    """
    ready, why = fixed_state(fresh=True)
    if not ready:
        return "", why
    # Le port HTTPS dit en toutes lettres : ouvert sans lui, le partage ne se
    # laissait plus retirer par « funnel --https=443 off » (Tailscale 1.102 :
    # « handler does not exist »), et « Fermer » ne fermait rien.
    code, said, link = _watch(["funnel", "--bg", "--https=443", str(port)],
                              timeout=60)
    forget_fixed()
    if link:
        return "", "Le partage public doit être autorisé une fois : " + link
    if code != 0:
        return "", said.strip()[:200] or "Tailscale a refusé d'ouvrir l'adresse."
    return f"https://{why}", "Adresse fixe ouverte."


def close_fixed() -> str:
    """Retire le partage public de l'adresse fixe. Rend ce qu'il faut en dire.

    Seulement lui : « funnel reset », tente autrefois quand ceci echouait,
    effacait toute la configuration de Tailscale, d'autres services compris.
    Le message vaut FIXED_CLOSED quand c'est fait ; sinon, l'adresse est
    peut-etre encore publiee, et il le dit.
    """
    code, said = _ask(["funnel", "--https=443", "off"], timeout=30)
    forget_fixed()
    if code == 0:
        return FIXED_CLOSED
    return said.strip()[:160] or "Tailscale n'a pas pu fermer l'adresse fixe."
