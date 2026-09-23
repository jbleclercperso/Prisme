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
