"""Les mises à jour : savoir qu'il y en a une, et la poser d'un clic.

Une version publiée (`construire.py --publier`) se résume à deux fichiers,
dans un dossier (le NAS, aujourd'hui) ou à une adresse https (plus tard) :

  version.json         numéro, date, nouveautés, nom de l'archive, empreinte
  Prisme-1.2.0.zip     le dossier du programme, tel que `construire.py` le fait

Prisme lit `version.json` au lancement puis toutes les six heures. Plus
récente que la sienne : une pastille, un bandeau, une entrée du menu. Un clic
télécharge l'archive, vérifie son empreinte (et sa signature, dès que le
programme en porte une), la décompresse à côté, puis confie la pose à un petit
script : il attend que Prisme soit fermé, remplace `Prisme.exe` et `_internal`
-- rien d'autre : `cache/` et `prisme.cache` restent --, et relance. Un échec
en route remet l'ancienne version en place.

Ne concerne que le programme construit : lancé depuis les sources, Prisme se
met à jour par git.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from . import __version__

MANIFEST = "version.json"
# A cote de Prisme.exe : ou chercher les mises a jour. Ecrit par
# `construire.py`, pour qu'un programme installe sache ou regarder sans
# qu'on ait rien a regler.
SOURCE_FILE = "mise-a-jour.txt"
# Dans le dossier du programme : l'archive, la version decompressee, l'ancienne
# mise de cote. Le meme disque que le programme : un dossier s'y deplace d'un
# coup, sans recopie.
WORK = "_mise-a-jour"
# Ce que la pose remplace ; le reste du dossier (cache, reglages poses a
# cote du programme) n'est pas touche.
EXE = "Prisme.exe"
CHECK_EVERY_S = 6 * 3600
TIMEOUT_S = 20


class UpdateError(Exception):
    """Ce qui empeche la mise a jour, dit en clair."""


def parse(version: str) -> tuple:
    """« 1.10.2 » -> (1, 10, 2) : « 1.10 » passe bien apres « 1.9 »."""
    return tuple(int(part) for part in re.findall(r"\d+", str(version))[:4])


def newer(version: str, than: str = __version__) -> bool:
    return bool(parse(version)) and parse(version) > parse(than)


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path | None:
    """Le dossier du programme construit ; None depuis les sources."""
    return Path(sys.executable).resolve().parent if frozen() else None


def source(configured: str = "") -> str:
    """Ou regarder : le reglage s'il y en a un, sinon ce que la construction
    a pose a cote du programme."""
    if configured.strip():
        return configured.strip()
    here = install_dir()
    if here is None:
        return ""
    try:
        return (here / SOURCE_FILE).read_text(encoding="utf-8").strip().splitlines()[0]
    except (OSError, IndexError):
        return ""


def _remote(where: str) -> bool:
    return where.lower().startswith(("http://", "https://"))


def _join(where: str, name: str) -> str:
    return where.rstrip("/") + "/" + name if _remote(where) else str(Path(where) / name)


def fetch_manifest(where: str) -> dict:
    """La derniere version publiee. Leve UpdateError, en clair."""
    if not where:
        raise UpdateError("Aucune source de mises à jour réglée.")
    try:
        if _remote(where):
            import requests
            answer = requests.get(_join(where, MANIFEST), timeout=TIMEOUT_S)
            answer.raise_for_status()
            told = answer.json()
        else:
            told = json.loads(Path(_join(where, MANIFEST)).read_text(encoding="utf-8"))
    except (OSError, ValueError) as trouble:
        raise UpdateError(f"Source des mises à jour injoignable : {trouble}") from trouble
    except Exception as trouble:                    # noqa: BLE001  (requests)
        raise UpdateError(f"Source des mises à jour injoignable : {trouble}") from trouble
    if not isinstance(told, dict) or not all(
            told.get(key) for key in ("version", "fichier", "sha256")):
        raise UpdateError("Le fichier de version publié est incomplet.")
    if "/" in told["fichier"] or "\\" in told["fichier"] or ".." in told["fichier"]:
        raise UpdateError("Le fichier de version publié est invalide.")
    return told


def check(where: str) -> dict | None:
    """La version publiee si elle est plus recente que celle-ci, sinon None."""
    told = fetch_manifest(where)
    return told if newer(told["version"]) else None


def download(where: str, told: dict, into: Path, progress=None,
             should_stop=None) -> Path:
    """Recopie l'archive et verifie son empreinte. `progress(fait, total)`."""
    into.mkdir(parents=True, exist_ok=True)
    target = into / told["fichier"]
    total = int(told.get("taille") or 0)
    digest = hashlib.sha256()
    done = 0
    try:
        if _remote(where):
            import requests
            answer = requests.get(_join(where, told["fichier"]), stream=True,
                                  timeout=TIMEOUT_S)
            answer.raise_for_status()
            total = total or int(answer.headers.get("Content-Length") or 0)
            pieces = answer.iter_content(1 << 20)
        else:
            reader = open(_join(where, told["fichier"]), "rb")
            pieces = iter(lambda: reader.read(1 << 20), b"")
        with open(target, "wb") as out:
            for piece in pieces:
                if should_stop and should_stop():
                    raise UpdateError("Téléchargement interrompu.")
                out.write(piece)
                digest.update(piece)
                done += len(piece)
                if progress:
                    progress(done, total)
        if not _remote(where):
            reader.close()
    except UpdateError:
        target.unlink(missing_ok=True)
        raise
    except Exception as trouble:                    # noqa: BLE001
        target.unlink(missing_ok=True)
        raise UpdateError(f"Téléchargement impossible : {trouble}") from trouble
    if digest.hexdigest().lower() != str(told["sha256"]).lower():
        target.unlink(missing_ok=True)
        raise UpdateError("L'archive téléchargée est abîmée (empreinte différente) : "
                          "rien n'a été changé.")
    return target


def unpack(archive: Path, into: Path) -> Path:
    """Decompresse a cote et rend le dossier du programme (`Prisme/`)."""
    shutil.rmtree(into, ignore_errors=True)
    into.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            # Rien hors du dossier : une archive piegee ne doit rien ecrire
            # ailleurs (« ../../ »).
            place = (into / name).resolve()
            if into.resolve() not in place.parents and place != into.resolve():
                raise UpdateError("Archive invalide : rien n'a été changé.")
        zf.extractall(into)
    found = into / "Prisme"
    if not (found / EXE).is_file() or not (found / "_internal").is_dir():
        raise UpdateError("L'archive ne contient pas le programme : rien n'a été changé.")
    return found


def signature(exe: Path) -> tuple:
    """(etat, signataire) de la signature Windows d'un programme ; ("", "")
    si on ne peut pas le savoir."""
    command = ("$s = Get-AuthenticodeSignature -LiteralPath $env:PRISME_EXE; "
               "@{etat = [string]$s.Status; qui = [string]$s.SignerCertificate.Subject}"
               " | ConvertTo-Json -Compress")
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PRISME_EXE=str(exe)),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        told = json.loads(done.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return "", ""
    return str(told.get("etat") or ""), str(told.get("qui") or "")


def check_signature(current: Path, fresh: Path) -> None:
    """Un programme signe n'accepte qu'une mise a jour signee par le meme
    editeur. Tant que Prisme n'est pas signe, l'empreinte seule fait foi."""
    state, who = signature(current)
    if state != "Valid":
        return
    new_state, new_who = signature(fresh)
    if new_state != "Valid" or new_who != who:
        raise UpdateError("La nouvelle version n'est pas signée par le même éditeur : "
                          "rien n'a été changé.")


def _quoted(path) -> str:
    """Une chaine PowerShell entre apostrophes."""
    return "'" + str(path).replace("'", "''") + "'"


SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$log = {log}
function Say($text) {{
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ("{{0:dd/MM HH:mm:ss}} {{1}}" -f (Get-Date), $text)
}}
$dir = {dir}
$new = {new}
$old = {old}
$names = @({names})
$exe = Join-Path $dir {exe}
try {{
    $p = Get-Process -Id {pid} -ErrorAction SilentlyContinue
    if ($p -and -not $p.WaitForExit(300000)) {{
        Say 'Prisme ne s''est pas fermé : mise à jour abandonnée, rien n''a changé.'
        exit 1
    }}
    Start-Sleep -Milliseconds 800
    if (Test-Path -LiteralPath $old) {{ Remove-Item -LiteralPath $old -Recurse -Force }}
    New-Item -ItemType Directory -Force -Path $old | Out-Null
    $done = New-Object System.Collections.ArrayList
    try {{
        foreach ($name in $names) {{
            $here = Join-Path $dir $name
            for ($try = 0; $try -lt 20; $try++) {{
                try {{
                    if (Test-Path -LiteralPath $here) {{
                        Move-Item -LiteralPath $here -Destination (Join-Path $old $name)
                    }}
                    break
                }} catch {{
                    if ($try -eq 19) {{ throw }}
                    Start-Sleep -Milliseconds 500
                }}
            }}
            [void]$done.Add($name)
            Move-Item -LiteralPath (Join-Path $new $name) -Destination $here
        }}
    }} catch {{
        Say "Échec de la pose : $_ -- retour à la version précédente."
        for ($i = $done.Count - 1; $i -ge 0; $i--) {{
            $name = $done[$i]
            $here = Join-Path $dir $name
            $kept = Join-Path $old $name
            if (Test-Path -LiteralPath $kept) {{
                if (Test-Path -LiteralPath $here) {{ Remove-Item -LiteralPath $here -Recurse -Force }}
                Move-Item -LiteralPath $kept -Destination $here
            }}
        }}
        if ({relaunch}) {{ Start-Process -FilePath $exe -WorkingDirectory $dir }}
        exit 1
    }}
    Say 'Mise à jour posée : {version}.'
    if ({relaunch}) {{ Start-Process -FilePath $exe -WorkingDirectory $dir }}
    Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
}} catch {{
    Say "Erreur : $_"
    exit 1
}}
"""


def write_swap(fresh: Path, here: Path, pid: int, version: str,
               relaunch: bool = True) -> Path:
    """Ecrit le script de pose. Il remplace, dans `here`, chaque element de
    premier niveau de `fresh` (Prisme.exe, _internal, LISEZ-MOI…), et rien
    d'autre."""
    work = here / WORK
    names = sorted(entry.name for entry in fresh.iterdir())
    if EXE not in names:
        raise UpdateError("L'archive ne contient pas le programme : rien n'a été changé.")
    text = SCRIPT.format(
        log=_quoted(work / "pose.log"), dir=_quoted(here), new=_quoted(fresh),
        old=_quoted(work / "ancien"), names=", ".join(_quoted(n) for n in names),
        exe=_quoted(EXE), pid=int(pid), relaunch="$true" if relaunch else "$false",
        version=version.replace("'", ""))
    script = work / "poser.ps1"
    # Avec BOM : Windows PowerShell 5 lit sinon les accents de travers.
    script.write_text(text, encoding="utf-8-sig")
    return script


def start_swap(script: Path) -> None:
    """Lance la pose, detachee : elle survit a la fermeture de Prisme."""
    flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "DETACHED_PROCESS", 0))
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-WindowStyle", "Hidden", "-File", str(script)],
        creationflags=flags, close_fds=True, cwd=str(script.parent))


def prepare(where: str, told: dict, progress=None, should_stop=None) -> Path:
    """Tout ce qui peut echouer sans rien casser : telecharger, verifier,
    decompresser. Rend le script de pose, pret a lancer."""
    here = install_dir()
    if here is None:
        raise UpdateError("Prisme tourne depuis ses sources : mettez-le à jour par git.")
    work = here / WORK
    try:
        work.mkdir(exist_ok=True)
        probe = work / ".ecriture"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as trouble:
        raise UpdateError(
            f"Prisme ne peut pas écrire dans son dossier ({here}) : déplacez-le dans "
            "un dossier à vous, ou mettez-le à jour à la main.") from trouble
    archive = download(where, told, work, progress, should_stop)
    fresh = unpack(archive, work / "nouveau")
    check_signature(here / EXE, fresh / EXE)
    return write_swap(fresh, here, os.getpid(), told["version"])


def tidy() -> None:
    """Au lancement : ce qu'une mise a jour passee a laisse (archive, ancien
    dossier). Le journal de la pose reste, pour qui voudrait le lire."""
    here = install_dir()
    if here is None:
        return
    work = here / WORK
    for name in ("nouveau", "ancien"):
        shutil.rmtree(work / name, ignore_errors=True)
    try:
        for entry in work.glob("*.zip"):
            entry.unlink()
    except OSError:
        pass


def last_log_line() -> str:
    here = install_dir()
    if here is None:
        return ""
    try:
        lines = (here / WORK / "pose.log").read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""
    return lines[-1] if lines else ""
