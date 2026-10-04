"""Le NAS est la, mais son nom ne repond plus : le dire vite, et reparer.

Un VPN remplace le serveur de noms de la box par le sien, qui ne connait pas
les appareils de la maison. Le NAS repond toujours a son adresse (et a son nom
« .local », qui interroge directement le reseau de la maison), mais plus a son
nom court -- et Windows cherchait ce nom pendant des minutes : l'accueil de
Prisme restait fige, ses boutons grises.

Ici : un diagnostic en quelques secondes, avant de toucher au NAS, et une
reparation en un clic -- la ligne « adresse  nom » ajoutee au fichier `hosts`
de Windows, avec l'autorisation de l'utilisateur (une seule demande Windows).
Les chemins de la collection ne changent pas : rien a reindexer.
"""
from __future__ import annotations

import os
import re
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

NAME_WAIT = 2.5          # secondes pour que le nom du NAS reponde vite
SLOW_WAIT = 12.0         # au plus, pour le trouver quand meme (reseau de la maison)
PORT_WAIT = 2.5          # secondes pour que son partage (SMB) reponde
MARK = "# Prisme"


def hosts_path() -> Path:
    """Le fichier `hosts` de Windows (PRISME_HOSTS_FILE pour les essais)."""
    forced = os.environ.get("PRISME_HOSTS_FILE", "").strip()
    if forced:
        return Path(forced)
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return Path(root) / "System32" / "drivers" / "etc" / "hosts"


def server_of(path) -> str:
    """« as1104t » pour « \\\\as1104t\\Volume 3\\… » ; "" hors reseau."""
    text = str(path).replace("/", "\\")
    if not text.startswith("\\\\"):
        return ""
    name = text[2:].split("\\", 1)[0]
    return "" if not name or name.startswith("?") else name


def _in_time(work, seconds: float):
    """Le resultat de `work()`, ou None s'il tarde : une resolution de nom
    bloquee ne retient plus personne."""
    box: list = []
    thread = threading.Thread(target=lambda: box.append(work()), daemon=True)
    thread.start()
    thread.join(seconds)
    return box[0] if box else None


def address_of(name: str, seconds: float = NAME_WAIT) -> str:
    """L'adresse IPv4 d'un nom, ou "" s'il ne repond pas a temps."""
    def ask():
        try:
            return socket.getaddrinfo(name, 445, socket.AF_INET)[0][4][0]
        except (OSError, IndexError):
            return ""
    return _in_time(ask, seconds) or ""


def answers(ip: str, seconds: float = PORT_WAIT) -> bool:
    """Le partage de fichiers (SMB, port 445) repond-il a cette adresse ?"""
    try:
        with socket.create_connection((ip, 445), timeout=seconds):
            return True
    except OSError:
        return False


def diagnose(path, remembered: str = "") -> tuple:
    """(etat, adresse) pour une racine :

    - « ok » : le nom du NAS repond (adresse : la sienne, a retenir) ;
    - « cache » : le nom ne repond plus, mais le NAS, si (a son nom « .local »
      ou a l'adresse retenue) -- un VPN, le plus souvent : a reparer ;
    - « absent » : le NAS ne repond pas du tout (eteint, autre reseau) ;
    - « local » : pas un chemin reseau, rien a diagnostiquer.
    """
    name = server_of(path)
    if not name:
        return "local", ""
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", name):
        return ("ok" if answers(name) else "absent"), name
    # Le nom et son « .local » sont cherches ensemble. Derriere un VPN, Windows
    # finit par trouver le nom en interrogeant le reseau de la maison -- mais
    # en sept secondes, a chaque acces : c'est cela qui figeait tout.
    found: dict = {}

    def look(key: str, host: str) -> None:
        found[key] = address_of(host, SLOW_WAIT)

    lookups = [threading.Thread(target=look, args=(k, h), daemon=True)
               for k, h in (("name", name), ("local", name + ".local"))]
    for thread in lookups:
        thread.start()
    lookups[0].join(NAME_WAIT)
    if found.get("name"):
        return "ok", found["name"]                      # le nom repond, et vite
    if remembered and answers(remembered):
        return "cache", remembered                      # l'adresse retenue suffit
    deadline = time.monotonic() + SLOW_WAIT
    while time.monotonic() < deadline and not (found.get("name") or found.get("local")):
        if all(not t.is_alive() for t in lookups):
            break
        time.sleep(0.1)
    ip = found.get("name") or found.get("local") or ""
    if ip and answers(ip):
        return "cache", ip                              # lent, ou cache : a reparer
    return "absent", ""


def hosts_line(name: str) -> str:
    """L'adresse que le fichier `hosts` donne deja a ce nom, ou ""."""
    try:
        text = hosts_path().read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for line in text.splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2 and any(p.lower() == name.lower() for p in parts[1:]):
            return parts[0]
    return ""


def fixed_text(text: str, name: str, ip: str) -> str:
    """Le contenu de `hosts` avec « ip  name » : l'ancienne ligne de ce nom
    retiree (une adresse changee se met a jour), la nouvelle ajoutee."""
    kept = []
    for line in text.splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2 and any(p.lower() == name.lower() for p in parts[1:]):
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    kept.append(f"{ip}\t{name}\t{MARK} : le NAS, trouvable meme derriere un VPN")
    return "\r\n".join(kept) + "\r\n"


def repair(name: str, ip: str) -> str:
    """Ajoute « ip  name » au fichier `hosts`. Rend "" si c'est fait, sinon ce
    qui a empeche. Le fichier de Windows demande l'autorisation de
    l'utilisateur : une seule fenetre Windows, a laquelle il repond « Oui »."""
    target = hosts_path()
    try:
        current = target.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        current = ""
    wanted = fixed_text(current, name, ip)
    try:
        target.write_text(wanted, encoding="utf-8", newline="")
        return ""                           # permis sans autorisation (essais)
    except PermissionError:
        pass
    except OSError as exc:
        return f"écriture impossible ({exc})"
    if sys.platform != "win32":
        return "réparation prévue pour Windows seulement"
    # Le fichier de Windows : on prepare le nouveau contenu a cote, et une
    # petite commande, lancee avec l'autorisation de l'utilisateur, le met en
    # place puis vide la memoire des noms de Windows.
    spare = Path(tempfile.gettempdir()) / "prisme-hosts.txt"
    spare.write_text(wanted, encoding="utf-8", newline="")
    command = (f"Copy-Item -LiteralPath '{spare}' -Destination '{target}' -Force; "
               "ipconfig /flushdns | Out-Null")
    done = _run_elevated("powershell.exe",
                         f'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
                         f'-Command "{command}"')
    try:
        spare.unlink()
    except OSError:
        pass
    if done:
        return done
    return "" if hosts_line(name) == ip else "la réparation n'a pas pu s'écrire"


def _run_elevated(program: str, arguments: str) -> str:
    """Lance `program` avec l'autorisation de l'utilisateur et attend la fin.
    Rend "" si c'est fait, ou la raison (refusee par l'utilisateur…)."""
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040                 # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"                   # la demande d'autorisation de Windows
    info.lpFile = program
    info.lpParameters = arguments
    info.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.GetLastError()
        return ("autorisation refusée" if error == 1223
                else f"lancement impossible (erreur {error})")
    if info.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 30_000)
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
    return ""
