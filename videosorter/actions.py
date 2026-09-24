"""Actions destructrices : déplacement, suppression, annulation."""
from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import LOCAL_TRASH


class ActionError(Exception):
    pass


@dataclass
class HistoryEntry:
    action: str          # "move" | "delete"
    src: Path            # emplacement d'origine
    dst: Path | None     # emplacement après action (None si non réversible)
    label: str
    reversible: bool


def unique_target(directory: Path, name: str) -> Path:
    """Retourne un chemin libre dans `directory`, en suffixant (2), (3)... si besoin."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for counter in range(2, 10000):
        candidate = directory / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
    raise ActionError(f"Impossible de trouver un nom libre dans {directory}")


def is_cross_device(src: Path, dest_dir: Path) -> bool:
    """Vrai si l'opération traversera deux volumes, donc copiera réellement.

    Sur un même disque, déplacer revient à renommer : c'est instantané, quelle
    que soit la taille. D'un disque à l'autre, il faut recopier chaque octet.
    """
    src_drive = os.path.splitdrive(os.path.abspath(str(src)))[0].lower()
    dest_drive = os.path.splitdrive(os.path.abspath(str(dest_dir)))[0].lower()
    return src_drive != dest_drive


def retry(func, *args, attempts: int = 4, delay: float = 0.12):
    """Réessaie une action : un verrou de fichier se relâche souvent en un instant.

    Sans appel à l'interface : utilisable depuis un fil d'exécution secondaire.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return func(*args)
        except ActionError as exc:
            last = exc
            time.sleep(delay * (attempt + 1))
    raise last


def _forget(src: Path, target: Path) -> None:
    """Retire du cache les dossiers que ce deplacement va changer."""
    # Import tardif : l'index depend de modules qui dependent d'ici.
    from .index import INDEX
    from .stamps import forget as forget_stamp
    for path in (Path(src).parent, Path(target).parent, Path(src)):
        INDEX.forget(path)
    # Le fichier change de place : l'empreinte retenue pour lui ne vaut plus.
    forget_stamp(src)


def _relocate(src: Path, target: Path) -> None:
    """Déplace src vers target.

    On tente d'abord un renommage : sur un même volume il est atomique, donc un
    verrou transitoire échoue proprement au lieu de laisser une copie partielle.
    Le repli copie-puis-efface de shutil n'est utilisé que d'un disque à l'autre.
    """
    _forget(src, target)
    try:
        os.rename(src, target)
        return
    except OSError as exc:
        cross_device = exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17
        if not cross_device:
            raise
    shutil.move(str(src), str(target))


def move_to(src: Path, dest_dir: Path) -> Path:
    """Déplace src dans dest_dir. Retourne le nouveau chemin."""
    src = Path(src)
    dest_dir = Path(dest_dir)
    if not src.exists():
        raise ActionError(f"Introuvable : {src}")
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ActionError(f"Destination inaccessible : {dest_dir} ({exc})") from exc

    # Refuse de déplacer un dossier dans lui-même ou dans un de ses descendants.
    if src.is_dir():
        try:
            dest_dir.resolve().relative_to(src.resolve())
            raise ActionError("La destination est à l'intérieur du dossier à déplacer.")
        except ValueError:
            pass

    target = unique_target(dest_dir, src.name)
    try:
        _relocate(src, target)
    except (OSError, shutil.Error) as exc:
        raise ActionError(f"Déplacement impossible : {exc}") from exc
    return target


def delete(path: Path, mode: str = "recycle") -> tuple[Path | None, bool]:
    """Supprime selon le mode choisi.

    Retourne (nouvel_emplacement, réversible_dans_l_app).
    """
    path = Path(path)
    if not path.exists():
        raise ActionError(f"Introuvable : {path}")

    if mode == "recycle":
        from .media import is_network_path
        if is_network_path(path):
            # Un partage reseau n'a pas de corbeille Windows : l'envoi y
            # echouait, et les fichiers « supprimes » s'entassaient, caches,
            # sur le NAS. On supprime donc pour de bon — c'est ce qu'on
            # demande en supprimant.
            mode = "permanent"

    if mode == "permanent":
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            raise ActionError(f"Suppression impossible : {exc}") from exc
        return None, False

    if mode == "local_trash":
        target = move_to(path, LOCAL_TRASH)
        return target, True

    # Corbeille Windows : récupérable depuis l'explorateur, mais pas via Ctrl+Z ici.
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise ActionError(
            "Le module send2trash est absent : pip install send2trash"
        ) from exc
    try:
        send2trash(str(path))
    except Exception as exc:  # send2trash lève des exceptions variées selon l'OS
        raise ActionError(f"Envoi à la corbeille impossible : {exc}") from exc
    return None, False


def undo(entry: HistoryEntry) -> None:
    """Remet un élément à son emplacement d'origine."""
    if not entry.reversible or entry.dst is None:
        raise ActionError("Cette action ne peut pas être annulée depuis l'application.")
    dst = Path(entry.dst)
    if not dst.exists():
        raise ActionError(f"Introuvable à son nouvel emplacement : {dst}")
    src = Path(entry.src)
    src.parent.mkdir(parents=True, exist_ok=True)
    target = src if not src.exists() else unique_target(src.parent, src.name)
    try:
        _relocate(dst, target)
    except (OSError, shutil.Error) as exc:
        raise ActionError(f"Restauration impossible : {exc}") from exc


def reveal(path: Path) -> None:
    """Ouvre l'explorateur sur l'élément."""
    path = Path(path)
    if sys.platform != "win32":
        return
    flags = subprocess.CREATE_NO_WINDOW
    if path.exists():
        subprocess.Popen(["explorer", "/select,", str(path)], creationflags=flags)
    elif path.parent.exists():
        subprocess.Popen(["explorer", str(path.parent)], creationflags=flags)


def open_recycle_bin() -> None:
    if sys.platform == "win32":
        subprocess.Popen(
            ["explorer", "shell:RecycleBinFolder"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
