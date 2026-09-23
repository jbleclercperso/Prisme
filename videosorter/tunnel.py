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
from pathlib import Path

from PySide6.QtCore import QObject, Signal

# Windows n'ouvre pas de fenetre noire quand on lance ces programmes.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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
    where = find()
    if where:
        return where, "cloudflared est installé."
    tail = (done.stdout or done.stderr or "").strip().splitlines()
    return "", ("L'installation s'est terminée, mais cloudflared reste "
                "introuvable. " + (tail[-1] if tail else ""))


class Tunnel(QObject):
    """Ouvre le tunnel et annonce l'adresse dès que Cloudflare la donne."""

    ready = Signal(str)        # l'adresse publique
    failed = Signal(str)
    closed = Signal()

    def __init__(self, port: int, parent=None):
        super().__init__(parent)
        self.port = int(port)
        self.process = None
        self.address = ""
        self._reader = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> bool:
        where = find()
        if not where:
            self.failed.emit("cloudflared n'est pas installé.")
            return False
        if self.running:
            return True
        try:
            self.process = subprocess.Popen(
                [where, "tunnel", "--no-autoupdate", "--url",
                 f"http://127.0.0.1:{self.port}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                creationflags=NO_WINDOW, bufsize=1)
        except OSError as trouble:
            self.process = None
            self.failed.emit(f"Le tunnel n'a pas démarré : {trouble}")
            return False
        self.address = ""
        self._reader = threading.Thread(target=self._listen, daemon=True,
                                        name="prisme-tunnel")
        self._reader.start()
        return True

    def _listen(self) -> None:
        """Lit ce que dit cloudflared jusqu'à trouver l'adresse.

        Il l'écrit au milieu d'un encadrement de tirets, sur sa sortie
        d'erreur : c'est la seule façon de la connaître.
        """
        process = self.process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                if not self.address:
                    found = ADDRESS.search(line)
                    if found:
                        self.address = found.group(0)
                        self.ready.emit(self.address)
                if process.poll() is not None:
                    break
        except (OSError, ValueError):
            pass
        if not self.address:
            self.failed.emit("Le tunnel s'est fermé sans donner d'adresse.")
        self.closed.emit()

    def stop(self) -> None:
        process, self.process = self.process, None
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


def find_fixed() -> str:
    """Le chemin de tailscale, ou une chaîne vide."""
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


def _ask(args: list, timeout: int = 60) -> tuple:
    """Lance tailscale et rend (code, ce qu'il a dit)."""
    where = find_fixed()
    if not where:
        return 1, ""
    try:
        done = subprocess.run(
            [where] + args, capture_output=True, text=True, timeout=timeout,
            creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as trouble:
        return 1, str(trouble)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def install_fixed(timeout: int = 900) -> tuple:
    """Installe Tailscale par winget. Rend (chemin, ce qu'il faut en dire)."""
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
    where = find_fixed()
    return ((where, "Tailscale est installé.") if where else
            ("", "L'installation s'est terminée, mais Tailscale reste "
                 "introuvable. Un redémarrage peut être nécessaire."))


def fixed_name() -> str:
    """Le nom de cette machine dans le réseau Tailscale, ou rien.

    C'est lui qui donne l'adresse : `nom.tailnet.ts.net`. Il ne change pas.
    """
    code, said = _ask(["status", "--json"], timeout=20)
    if code != 0 or not said.strip():
        return ""
    try:
        told = json.loads(said)
    except ValueError:
        return ""
    name = ((told.get("Self") or {}).get("DNSName") or "").strip(".")
    return name


def fixed_state() -> tuple:
    """(prêt, ce qu'il faut en dire) — l'état de la mise en route."""
    if not find_fixed():
        return False, "Tailscale n'est pas installé."
    code, said = _ask(["status"], timeout=20)
    if "Logged out" in said or "logged out" in said:
        return False, "Tailscale est installé, mais aucun compte n'est connecté."
    if code != 0 and not fixed_name():
        return False, "Tailscale ne répond pas. " + said.strip()[:160]
    name = fixed_name()
    if not name:
        return False, "Tailscale est connecté, mais cette machine n'a pas de nom."
    return True, name


def connect_fixed() -> tuple:
    """Ouvre la page de connexion à un compte Tailscale, dans le navigateur."""
    code, said = _ask(["up"], timeout=15)
    found = CONSENT.search(said)
    if found:
        return found.group(0), "Ouvrez cette adresse pour connecter le compte."
    if code == 0:
        return "", "Le compte est connecté."
    return "", said.strip()[:200] or "Tailscale n'a pas répondu."


def open_fixed(port: int) -> tuple:
    """Publie le port sur l'adresse fixe. Rend (adresse, ce qu'il faut en dire).

    Une adresse peut manquer pour deux raisons : le compte n'est pas
    connecte, ou le partage public n'est pas encore autorise. Dans le second
    cas Tailscale imprime le lien qui l'autorise — on le rend tel quel.
    """
    ready, why = fixed_state()
    if not ready:
        return "", why
    code, said = _ask(["funnel", "--bg", str(port)], timeout=60)
    consent = CONSENT.search(said)
    if consent:
        return "", ("Le partage public doit être autorisé une fois : "
                    + consent.group(0))
    if code != 0:
        return "", said.strip()[:200] or "Tailscale a refusé d'ouvrir l'adresse."
    return f"https://{why}", "Adresse fixe ouverte."


def close_fixed() -> str:
    """Retire le partage public. Rend ce qu'il faut en dire."""
    code, said = _ask(["funnel", "--https=443", "off"], timeout=30)
    if code != 0:
        code, said = _ask(["funnel", "reset"], timeout=30)
    return "Adresse fixe fermée." if code == 0 else said.strip()[:160]
