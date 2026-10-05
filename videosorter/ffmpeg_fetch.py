"""ffmpeg et ffprobe, que Prisme va chercher lui-meme s'ils manquent.

Prisme ne peut rien sans eux : ce sont eux qui fabriquent les apercus, lisent
les durees, reparent les videos. Ils ne sont pas dans le programme (plus de
quatre cents Mo a eux deux), et le programme vendu, sur un PC neuf, ne
demarrait pas : il disait « installez-les avec winget » et s'arretait. Ici,
il propose de les telecharger -- une archive officielle d'une centaine de
Mo, verifiee par son empreinte quand le site la donne --, et les pose :

- a cote du programme, dans « ffmpeg », pour la version portable (ils
  voyagent avec elle) ;
- sinon dans %LOCALAPPDATA%\\Prisme\\ffmpeg.

Sans Qt : la fenetre (window.check_tools) montre la progression.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

from . import config

# (archive, empreinte SHA-256 publiee a cote, ou « »). Gyan est la version
# que winget installe ; BtbN, sur GitHub, en secours.
SOURCES = (
    ("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
     "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256"),
    ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
     "ffmpeg-master-latest-win64-gpl.zip", ""),
)
SIZE_TEXT = "environ 115 Mo"
TOOLS = ("ffmpeg.exe", "ffprobe.exe")


def _program_dir() -> Path | None:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None


def folder() -> Path:
    """Ou Prisme pose ffmpeg quand il le telecharge."""
    if config.SANDBOX:
        return Path(config.SANDBOX) / "ffmpeg"
    beside = _program_dir()
    if config.PORTABLE and beside is not None:
        return beside / "ffmpeg"
    return config._LOCAL / config.APP_NAME / "ffmpeg"


def candidates() -> list:
    """Les dossiers ou chercher un ffmpeg pose par Prisme (ou a la main, a
    cote du programme)."""
    found = [folder()]
    beside = _program_dir()
    if beside is not None:
        found += [beside / "ffmpeg", beside]
    return found


def fetch(progress=lambda done, total: None, stopped=lambda: False) -> Path:
    """Telecharge et pose ffmpeg.exe et ffprobe.exe ; rend leur dossier.
    `progress(octets, total)` suit le telechargement ; `stopped()` vrai
    l'interrompt (RuntimeError « annulé »)."""
    target = folder()
    work = target.with_name(target.name + ".part")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    archive = work / "ffmpeg.zip"
    problems = []
    for url, digest_url in SOURCES:
        try:
            expected = ""
            if digest_url:
                expected = _read(digest_url).decode("ascii", "replace").split()[0].lower()
            _download(url, archive, progress, stopped)
            if expected and _sha256(archive) != expected:
                raise OSError("l'archive reçue ne correspond pas à son empreinte")
            _extract(archive, work)
            break
        except OSError as exc:
            problems.append(f"{url.split('/')[2]} : {exc}")
            archive.unlink(missing_ok=True)
    else:
        shutil.rmtree(work, ignore_errors=True)
        raise RuntimeError("téléchargement impossible (" + " ; ".join(problems) + ")")
    archive.unlink(missing_ok=True)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    work.rename(target)
    return target


def _read(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Prisme"})
    with urllib.request.urlopen(request, timeout=30) as answer:
        return answer.read()


def _download(url: str, target: Path, progress, stopped) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Prisme"})
    with urllib.request.urlopen(request, timeout=60) as answer, open(target, "wb") as out:
        total = int(answer.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if stopped():
                raise RuntimeError("annulé")
            block = answer.read(1 << 17)
            if not block:
                break
            out.write(block)
            done += len(block)
            progress(done, total)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract(archive: Path, into: Path) -> None:
    """Ne garde que ffmpeg.exe et ffprobe.exe (le dossier bin de l'archive)."""
    with zipfile.ZipFile(archive) as packed:
        for member in packed.infolist():
            name = member.filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
            if name in TOOLS and "/bin/" in "/" + member.filename.replace("\\", "/").lower():
                with packed.open(member) as source, open(into / name, "wb") as out:
                    shutil.copyfileobj(source, out)
    missing = [name for name in TOOLS if not (into / name).is_file()]
    if missing:
        raise OSError("archive sans " + ", ".join(missing))
