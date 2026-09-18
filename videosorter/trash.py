"""Corbeille de session : rien n'est réellement supprimé avant la fermeture.

Trier vite suppose de supprimer sans confirmation ; supprimer sans confirmation
suppose de pouvoir se raviser. Un élément supprimé est donc seulement déplacé
dans un dossier de session, d'où il revient à sa place d'un clic. Ce n'est qu'à
la fermeture que le contenu part vers la corbeille de Windows, d'où il reste
récupérable par l'explorateur.

Le dossier de session est placé **dans la racine triée** et non dans les données
de l'application : sur le même volume, supprimer est alors un renommage
instantané, alors que d'un disque à l'autre il faudrait recopier chaque octet.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import actions
from .actions import ActionError
from .config import LOCAL_TRASH

FOLDER_NAME = ".videosorter-corbeille"


@dataclass
class TrashEntry:
    """Un élément mis de côté, et l'endroit d'où il vient."""

    origin: Path
    stored: Path
    size: int = 0
    at: float = field(default_factory=time.time)

    @property
    def name(self) -> str:
        return Path(self.origin).name


class SessionTrash(QObject):
    """Mise à l'écart réversible, vidée vers la corbeille de Windows à la fin."""

    changed = Signal(int)
    FOLDER_NAME = FOLDER_NAME

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stamp = time.strftime("%Y%m%d-%H%M%S")
        self.base: Path | None = None
        self.entries: list = []
        self.folders: set = set()

    def set_base(self, root: Path | None) -> None:
        """Choisit la racine sous laquelle mettre les éléments écartés."""
        self.base = Path(root) if root else None

    def folder_for(self, path: Path) -> Path:
        """Dossier de session à utiliser pour cet élément, sur son propre volume."""
        base = self.base
        if base is None or actions.is_cross_device(path, base):
            # Pas de racine utilisable, ou racine sur un autre disque : on se
            # rabat sur le voisinage immédiat de l'élément.
            base = Path(path).parent
        folder = Path(base) / FOLDER_NAME / self.stamp
        if actions.is_cross_device(path, folder):
            folder = LOCAL_TRASH / self.stamp
        self.folders.add(folder)
        return folder

    def record(self, origin: Path, stored: Path, size: int = 0) -> TrashEntry:
        entry = TrashEntry(origin=Path(origin), stored=Path(stored), size=size)
        self.entries.append(entry)
        self.changed.emit(len(self.entries))
        return entry

    def forget(self, stored: Path) -> None:
        """Retire une entrée dont l'élément a été restauré par ailleurs."""
        stored = Path(stored)
        for entry in list(self.entries):
            if entry.stored == stored:
                self.entries.remove(entry)
        self.changed.emit(len(self.entries))

    def restore(self, entry: TrashEntry) -> Path:
        """Remet l'élément à sa place, ou à côté si la place est reprise."""
        if entry not in self.entries:
            raise ActionError("Cet élément a déjà été restauré.")
        stored = Path(entry.stored)
        if not stored.exists():
            self.entries.remove(entry)
            raise ActionError(f"Introuvable dans la corbeille : {entry.name}")
        origin = Path(entry.origin)
        origin.parent.mkdir(parents=True, exist_ok=True)
        target = origin if not origin.exists() else actions.unique_target(
            origin.parent, origin.name
        )
        actions._relocate(stored, target)
        self.entries.remove(entry)
        self.changed.emit(len(self.entries))
        return target

    def flush(self, mode: str = "recycle") -> tuple[int, str]:
        """Vide la corbeille de session. Retourne (nombre traité, dernière erreur)."""
        done = 0
        problem = ""
        for entry in list(self.entries):
            try:
                if Path(entry.stored).exists():
                    actions.delete(entry.stored, mode)
                done += 1
                self.entries.remove(entry)
            except ActionError as exc:
                problem = str(exc)
        self.changed.emit(len(self.entries))
        self._cleanup_folders()
        return done, problem

    def _cleanup_folders(self) -> None:
        """Retire les dossiers de session devenus vides, sans jamais forcer."""
        for folder in list(self.folders):
            try:
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
                    parent = folder.parent
                    if parent.name == FOLDER_NAME and not any(parent.iterdir()):
                        parent.rmdir()
            except OSError:
                continue

    @property
    def count(self) -> int:
        return len(self.entries)
