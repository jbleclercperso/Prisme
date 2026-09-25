"""Corbeille de session : rien n'est détruit avant la fermeture.

Trier vite suppose de supprimer sans confirmation ; supprimer sans confirmation
suppose de pouvoir se raviser. Un élément supprimé est donc seulement déplacé
dans un dossier de session, d'où il revient à sa place d'un clic.

À la fermeture, la corbeille de session est vidée. Sur un disque local, son
contenu part dans la corbeille de Windows, d'où l'explorateur peut encore le
sortir. **Sur un partage réseau (le NAS), il n'y a pas de corbeille Windows :
le contenu est détruit définitivement.** C'est voulu -- supprimer, c'est
demander que ça disparaisse -- mais cela veut dire que ce qu'on veut garder
se restaure *avant* de fermer.

Le dossier de session est placé **dans la racine triée** et non dans les données
de l'application : sur le même volume, supprimer est alors un renommage
instantané, alors que d'un disque à l'autre il faudrait recopier chaque octet.

Chaque dossier de session porte un petit registre (`MANIFEST`) : ce qu'il
contient et d'où cela vient. Un Prisme arrêté net (plantage, coupure, arrêt
forcé) laissait sinon ses dossiers de session orphelins -- invisibles, puisque
le point les cache, et irrestaurables, puisque plus rien ne savait d'où
venaient les éléments. `leftovers` les retrouve au lancement suivant.

Il ne reprend que ceux d'une séance vraiment terminée. Chaque séance tient
ouvert, tant qu'elle vit, un petit fichier dans son dossier (`LOCK_NAME`) :
Windows -- et le NAS, qui applique les mêmes règles de partage -- refuse de
l'effacer tant qu'il est ouvert. Une séance plantée l'a lâché avec son
processus. Sans cela, un second Prisme lancé sur la même racine (une autre
machine) reprenait la corbeille vivante du premier, puis la détruisait en se
fermant.
"""
from __future__ import annotations

import json
import os
import platform
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import actions
from .actions import ActionError
from .config import TRASH_FOLDER_NAME

FOLDER_NAME = TRASH_FOLDER_NAME
MANIFEST = "prisme-corbeille.json"
# Tenu ouvert par la seance qui vit : son dossier n'est pas a reprendre.
LOCK_NAME = "prisme-seance.lock"
# La date de la seance, suivie du nom de la machine depuis cette version :
# deux PC lances dans la meme seconde partageaient sinon un dossier, et
# s'ecrasaient leurs registres.
_STAMP = re.compile(r"^\d{8}-\d{6}(-[A-Za-z0-9_-]+)?$")


def _machine() -> str:
    name = os.environ.get("COMPUTERNAME") or platform.node() or ""
    return re.sub(r"[^A-Za-z0-9_-]", "", name)[:24]


def _session_stamp() -> str:
    machine = _machine()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{machine}" if machine else stamp


@dataclass
class TrashEntry:
    """Un élément mis de côté, et l'endroit d'où il vient."""

    origin: Path
    stored: Path
    size: int = 0
    at: float = field(default_factory=time.time)
    # Retrouvé d'une séance précédente sans registre : l'origine est devinée
    # (la racine), à confirmer avant de restaurer.
    guessed: bool = False

    @property
    def name(self) -> str:
        return Path(self.origin).name


class SessionTrash(QObject):
    """Mise à l'écart réversible, vidée à la fin de la séance."""

    changed = Signal(int)
    # (ancien chemin dans la corbeille, chemin où l'élément est revenu) : les
    # favoris et l'index doivent le suivre, comme pour un Ctrl+Z.
    restored = Signal(str, str)
    # Chemins détruits pour de bon par le vidage : leurs favoris n'ont plus
    # d'objet.
    purged = Signal(list)
    FOLDER_NAME = FOLDER_NAME

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stamp = _session_stamp()
        self.base: Path | None = None
        self.entries: list = []
        self.folders: set = set()
        # Le registre s'écrit à côté des éléments, sur le NAS : quelques
        # millisecondes chaque fois, qu'on ne fait pas payer à l'interface.
        self._writer: ThreadPoolExecutor | None = None
        self._writer_lock = threading.Lock()
        # (nombre traité, dernière erreur) du dernier vidage en tâche de fond.
        self.flush_result: tuple | None = None
        # Les fichiers tenus ouverts, un par dossier de session : tant qu'ils
        # le sont, aucune autre seance ne reprend ces dossiers.
        self._locks: dict = {}
        self._locks_lock = threading.Lock()

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
            folder = Path(actions.LOCAL_TRASH) / self.stamp
        self.folders.add(folder)
        if folder not in self._locks:
            # Des la premiere mise a l'ecart, et sur le fil du registre : le
            # dossier se marque « vivant » avant que quiconque puisse le voir.
            self._submit(self._hold, folder)
        return folder

    # -- dossiers vivants ---------------------------------------------------
    def _hold(self, folder: Path) -> bool:
        """Ouvre, et garde ouvert, le fichier qui dit « seance en cours »."""
        with self._locks_lock:
            if folder in self._locks:
                return True
            try:
                Path(folder).mkdir(parents=True, exist_ok=True)
                handle = open(Path(folder) / LOCK_NAME, "a", encoding="utf-8")
            except OSError:
                return False
            self._locks[folder] = handle
            return True

    def _let_go(self, folder: Path | None = None, remove: bool = False) -> None:
        """Lache le fichier de ce dossier (de tous, sans dossier)."""
        with self._locks_lock:
            folders = [folder] if folder is not None else list(self._locks)
            handles = [(one, self._locks.pop(one, None)) for one in folders]
        for one, handle in handles:
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            if remove:
                try:
                    (Path(one) / LOCK_NAME).unlink()
                except OSError:
                    pass

    def _claim(self, folder: Path) -> bool:
        """Vrai si le dossier d'une autre seance est libre, et le prend.

        Effacer son fichier echoue tant qu'une seance vivante le tient
        ouvert ; une seance plantee l'a lache avec son processus. Un dossier
        qu'on ne peut pas examiner (NAS qui ne repond pas) n'est pas repris.
        """
        try:
            (Path(folder) / LOCK_NAME).unlink()
        except FileNotFoundError:
            pass                    # une version d'avant, ou rien d'ecarte
        except OSError:
            return False            # tenu par une seance vivante, ou illisible
        return self._hold(folder)

    # -- registre ---------------------------------------------------------
    def record(self, origin: Path, stored: Path, size: int = 0) -> TrashEntry:
        entry = TrashEntry(origin=Path(origin), stored=Path(stored), size=size)
        self.entries.append(entry)
        self.folders.add(Path(stored).parent)
        self.changed.emit(len(self.entries))
        self._save_manifest(Path(stored).parent)
        return entry

    def forget(self, stored: Path) -> None:
        """Retire une entrée dont l'élément a été restauré par ailleurs."""
        stored = Path(stored)
        for entry in list(self.entries):
            if entry.stored == stored:
                self.entries.remove(entry)
        self.changed.emit(len(self.entries))
        self._save_manifest(stored.parent)

    def _snapshot(self, folder: Path) -> list:
        return [
            {"origin": str(entry.origin), "stored": Path(entry.stored).name,
             "size": entry.size, "at": entry.at}
            for entry in self.entries if Path(entry.stored).parent == folder
        ]

    def _save_manifest(self, folder: Path) -> None:
        self._submit(_write_manifest, folder, self._snapshot(folder))

    def _submit(self, work, *args) -> None:
        with self._writer_lock:
            if self._writer is None:
                self._writer = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="corbeille")
            self._writer.submit(work, *args)

    def _drain(self) -> None:
        """Attend que les registres en attente soient écrits."""
        with self._writer_lock:
            writer = self._writer
        if writer is not None:
            try:
                writer.submit(lambda: None).result(timeout=30)
            except Exception:                           # noqa: BLE001
                pass

    # -- restauration -----------------------------------------------------
    def restore(self, entry: TrashEntry) -> Path:
        """Remet l'élément à sa place, ou à côté si la place est reprise.

        Toute erreur du disque devient une ActionError : une seule erreur brute
        interrompait « Tout restaurer » en silence, et les éléments suivants
        étaient détruits à la fermeture.
        """
        if entry not in self.entries:
            raise ActionError("Cet élément a déjà été restauré.", retry=False)
        stored = Path(entry.stored)
        state = actions.probe(stored)
        if state == "injoignable":
            # Le NAS dort : l'élément n'a pas disparu, on garde sa trace.
            raise ActionError(f"NAS injoignable : {entry.name} reste dans la corbeille")
        if state == "absent":
            self.entries.remove(entry)
            self.changed.emit(len(self.entries))
            self._save_manifest(stored.parent)
            raise ActionError(f"Introuvable dans la corbeille : {entry.name}",
                              retry=False)
        origin = Path(entry.origin)
        try:
            origin.parent.mkdir(parents=True, exist_ok=True)
            target = actions.unique_target(origin.parent, origin.name)
            actions._relocate(stored, target)
        except actions.PartialMove as exc:
            target = exc.target
        except ActionError as exc:
            raise ActionError(f"Restauration impossible : {entry.name} ({exc})") from exc
        except Exception as exc:                        # noqa: BLE001
            raise ActionError(
                f"Restauration impossible : {entry.name} ({actions.describe(exc)})"
            ) from exc
        self.entries.remove(entry)
        self.changed.emit(len(self.entries))
        self._save_manifest(stored.parent)
        self.restored.emit(str(stored), str(target))
        return target

    # -- vidage -------------------------------------------------------------
    def has_network_entries(self) -> bool:
        """Vrai si le vidage détruira quelque chose pour de bon (partage réseau)."""
        from .media import is_network_path
        return any(is_network_path(entry.stored) for entry in self.entries)

    def flush(self, mode: str = "recycle") -> tuple[int, str]:
        """Vide la corbeille de session. Retourne (nombre traité, dernière erreur).

        Sur un partage réseau, c'est une destruction définitive. Utilisable
        hors du fil de l'interface (`flush_in_background`) : il ne touche à
        rien d'autre qu'au disque et à ses propres entrées.
        """
        self._drain()
        done = 0
        problems: list = []
        purged: list = []
        touched: set = set()
        for entry in list(self.entries):
            stored = Path(entry.stored)
            touched.add(stored.parent)
            state = actions.probe(stored)
            if state == "injoignable":
                problems.append(f"NAS injoignable : « {entry.name} » reste dans "
                                f"{stored.parent}")
                continue
            try:
                if state == "ok":
                    actions.delete(stored, mode)
                    purged.append(str(stored))
                done += 1
                self.entries.remove(entry)
            except ActionError as exc:
                problems.append(str(exc))
            except Exception as exc:                    # noqa: BLE001
                # Une erreur imprevue sur un element ne doit pas laisser les
                # suivants de cote, ni faire tomber la fermeture.
                problems.append(f"« {entry.name} » : {actions.describe(exc)}")
        self.changed.emit(len(self.entries))
        # Ce qui reste garde son registre : le lancement suivant le retrouvera.
        for folder in touched:
            _write_manifest(folder, self._snapshot(folder))
        self._cleanup_folders()
        if purged:
            self.purged.emit(purged)
        problem = problems[-1] if problems else ""
        if len(problems) > 1:
            problem = f"{len(problems)} élément(s) non vidés. Dernier : {problem}"
        return done, problem

    def flush_in_background(self, mode: str = "recycle") -> threading.Thread:
        """Lance le vidage sur un fil à part ; le résultat ira dans `flush_result`.

        Un gros dossier sur le NAS, c'est un aller-retour réseau par fichier :
        vidé sur le fil de l'interface, la fenêtre restait « Ne répond pas »
        des minutes. Les signaux émis depuis ce fil arrivent à l'interface
        par sa file d'événements.
        """
        self.flush_result = None

        def work() -> None:
            try:
                self.flush_result = self.flush(mode)
            except Exception as exc:                    # noqa: BLE001
                self.flush_result = (0, f"{type(exc).__name__} : {exc}")

        thread = threading.Thread(target=work, name="vidage-corbeille", daemon=False)
        thread.start()
        return thread

    def _cleanup_folders(self) -> None:
        """Retire les dossiers de session devenus vides, sans jamais forcer."""
        for folder in list(self.folders):
            try:
                if any(Path(e.stored).parent == folder for e in self.entries):
                    continue
                if not folder.is_dir():
                    self._let_go(folder)
                    continue
                self._let_go(folder, remove=True)
                manifest = folder / MANIFEST
                if manifest.exists():
                    manifest.unlink()
                if not any(folder.iterdir()):
                    folder.rmdir()
                    parent = folder.parent
                    if parent.name == FOLDER_NAME and not any(parent.iterdir()):
                        parent.rmdir()
            except OSError:
                continue

    # -- séances précédentes ------------------------------------------------
    def leftovers(self, base: Path | None = None) -> list:
        """Ce que les séances précédentes ont laissé dans leurs dossiers de session.

        À appeler hors du fil de l'interface (lectures réseau). Rien n'est
        ajouté à la séance : c'est à l'utilisateur de décider (restaurer,
        détruire, voir), et `adopt` les rend alors restaurables comme les autres.
        """
        base = Path(base) if base else self.base
        spots = []
        if base is not None:
            spots.append((base / FOLDER_NAME, base))
        spots.append((Path(actions.LOCAL_TRASH), base))
        found: list = []
        for top, guess_base in spots:
            try:
                with os.scandir(top) as listing:
                    stamps = [entry.path for entry in listing
                              if entry.is_dir(follow_symlinks=False)
                              and _STAMP.match(entry.name)
                              and entry.name != self.stamp]
            except OSError:
                continue
            for folder in sorted(stamps):
                # Une seance encore ouverte ailleurs garde sa corbeille : la
                # reprendre la faisait detruire a notre fermeture, sous ses
                # yeux.
                if not self._claim(Path(folder)):
                    continue
                left = _read_session(Path(folder), guess_base)
                if not left:
                    # Rien a reprendre : le dossier vide ne doit pas rester,
                    # retenu a chaque lancement par une seance differente.
                    self._let_go(Path(folder), remove=True)
                    _remove_empty(Path(folder))
                found.extend(left)
        return found

    def adopt(self, entries: list) -> None:
        """Reprend dans la séance des éléments laissés par une séance précédente."""
        known = {Path(entry.stored) for entry in self.entries}
        added = [entry for entry in entries if Path(entry.stored) not in known]
        if not added:
            return
        self.entries.extend(added)
        for entry in added:
            self.folders.add(Path(entry.stored).parent)
        self.changed.emit(len(self.entries))

    @property
    def count(self) -> int:
        return len(self.entries)


def _write_manifest(folder: Path, rows: list) -> None:
    """Écrit le registre d'un dossier de session, ou l'efface s'il est vide.

    Écriture à côté puis remplacement : un arrêt en plein milieu laisse
    l'ancien registre, jamais un fichier tronqué.
    """
    target = Path(folder) / MANIFEST
    try:
        if not rows:
            if target.exists():
                target.unlink()
            return
        if not Path(folder).is_dir():
            return
        tmp = target.with_name(MANIFEST + ".tmp")
        tmp.write_text(json.dumps({"entries": rows}, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def _remove_empty(folder: Path) -> None:
    """Retire un dossier de session vide, son registre compris."""
    try:
        manifest = folder / MANIFEST
        if manifest.exists():
            manifest.unlink()
        if not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass


def _read_session(folder: Path, base: Path | None) -> list:
    """Les éléments encore présents dans un dossier de session d'autrefois."""
    try:
        with os.scandir(folder) as listing:
            present = {entry.name for entry in listing}
    except OSError:
        return []
    present.discard(MANIFEST)
    present.discard(MANIFEST + ".tmp")
    present.discard(LOCK_NAME)
    rows = []
    try:
        raw = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        rows = raw.get("entries", []) if isinstance(raw, dict) else []
    except (OSError, ValueError):
        rows = []
    found = []
    for row in rows:
        name = str(row.get("stored", ""))
        if not name or name not in present:
            continue
        present.discard(name)
        found.append(TrashEntry(origin=Path(row.get("origin") or name),
                                stored=folder / name,
                                size=int(row.get("size") or 0),
                                at=float(row.get("at") or 0.0)))
    # Sans registre (séance d'avant cette version) : l'origine se devine.
    for name in sorted(present):
        if name.endswith(actions.PARTIAL_SUFFIX):
            continue
        origin = (Path(base) / name) if base is not None else folder / name
        try:
            at = (folder / name).stat().st_mtime
        except OSError:
            at = 0.0
        found.append(TrashEntry(origin=origin, stored=folder / name, at=at,
                                guessed=True))
    return found
