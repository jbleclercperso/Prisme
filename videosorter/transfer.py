"""File de transferts exécutés en tâche de fond.

Trier vite suppose de ne jamais attendre : la décision est prise, l'élément
suivant s'affiche aussitôt, et la copie se poursuit derrière. Un seul fil
travaille à la fois, donc les opérations gardent l'ordre des décisions — ce qui
permet à l'historique d'annulation de rester cohérent.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import actions
from .actions import ActionError

# Un verrou de fichier (lecteur ou ffmpeg qui se termine) se relâche vite, mais
# en tâche de fond rien ne presse : on insiste longuement plutôt qu'échouer.
RETRY_ATTEMPTS = 12
RETRY_DELAY = 0.25

_IDS = itertools.count(1)


@dataclass
class Transfer:
    """Une opération disque en attente, en cours ou terminée."""

    kind: str                       # "move" | "delete" | "undo"
    # "move" range l'element, "delete" l'ecarte dans la corbeille de session :
    # meme operation disque, consequences differentes sur les compteurs.
    purpose: str = "move"
    src: Path = None
    label: str = ""
    item_id: str = ""
    dest: Path | None = None
    mode: str = ""                  # mode de suppression
    entry: object = None            # HistoryEntry, pour une annulation
    id: int = field(default_factory=lambda: next(_IDS))
    state: str = "pending"          # pending | running | done | failed
    result: Path | None = None
    reversible: bool = False
    error: str = ""
    # Abouti, mais avec une reserve a dire (source pas entierement videe).
    warning: str = ""
    # Pour un dossier ecarte : combien d'autres fichiers que des videos on y a
    # trouves en le recomptant apres coup (-1 : pas recompte). La garde de
    # suppression se fiait aux comptes de l'index, qui ignorent ce qui a
    # change plus bas ; c'est ici, avant que la perte ne devienne definitive
    # a la fermeture, qu'on peut encore le dire.
    others: int = -1

    @property
    def name(self) -> str:
        return Path(self.src).name


class _Signals(QObject):
    done = Signal(object)


class _Runner(QRunnable):
    def __init__(self, signals: _Signals, transfer: Transfer):
        super().__init__()
        self.signals = signals
        self.transfer = transfer

    def run(self) -> None:
        job = self.transfer
        job.state = "running"
        try:
            if job.kind == "move":
                job.result = actions.retry(
                    actions.move_to, job.src, job.dest,
                    attempts=RETRY_ATTEMPTS, delay=RETRY_DELAY,
                )
                job.reversible = True
                if job.purpose == "delete":
                    _count_others(job)
            elif job.kind == "delete":
                job.result, job.reversible = actions.retry(
                    actions.delete, job.src, job.mode,
                    attempts=RETRY_ATTEMPTS, delay=RETRY_DELAY,
                )
            else:
                # L'endroit reel ou l'element est revenu : a cote de sa place
                # d'origine si elle a ete reprise entre-temps.
                job.result = actions.retry(
                    actions.undo, job.entry,
                    attempts=RETRY_ATTEMPTS, delay=RETRY_DELAY,
                )
            job.state = "done"
        except actions.PartialMove as exc:
            # Tout est arrive ; seule la source n'a pas pu etre videe. Refaire
            # la copie aurait cree « (2) » : c'est fait, avec une reserve.
            job.result = exc.target
            job.reversible = True
            job.state = "done"
            job.warning = str(exc)
        except ActionError as exc:
            job.state = "failed"
            job.error = str(exc)
        except Exception as exc:                        # noqa: BLE001
            # Toute autre erreur echoue proprement. Autrefois elle traversait,
            # « done » n'etait jamais emis : la file restait « en cours » a
            # vie, Ctrl+Z refuse, et la fenetre ne pouvait plus se fermer.
            job.state = "failed"
            job.error = f"{type(exc).__name__} : {actions.describe(exc)}"
        finally:
            try:
                self.signals.done.emit(job)
            except RuntimeError:
                pass            # l'application se ferme : plus personne n'ecoute


def _count_others(job: Transfer) -> None:
    """Recompte un dossier qu'on vient d'ecarter : documents, images, programmes.

    Le deplacement est deja fait (un renommage, instantane) : on regarde apres
    coup, en tache de fond, ce qu'il emportait vraiment. Rien n'est encore
    detruit -- la fenetre peut le dire tant qu'un Ctrl+Z suffit.
    """
    try:
        if job.result is None or not Path(job.result).is_dir():
            return
        from .scan import scan_folder
        found = scan_folder(Path(job.result))
    except OSError:
        return
    if not found.unreadable:
        job.others = max(0, found.file_count - found.video_count)


class TransferQueue(QObject):
    """Exécute les opérations une par une, sans bloquer l'interface."""

    finished = Signal(object)      # Transfer terminé (réussi ou non)
    changed = Signal(int)          # nombre d'opérations encore en vol

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self.signals = _Signals()
        self.signals.done.connect(self._on_done)
        self.active = 0

    def submit(self, transfer: Transfer) -> Transfer:
        self.active += 1
        self.changed.emit(self.active)
        self.pool.start(_Runner(self.signals, transfer))
        return transfer

    def _on_done(self, transfer: Transfer) -> None:
        self.active = max(0, self.active - 1)
        self.changed.emit(self.active)
        self.finished.emit(transfer)

    @property
    def busy(self) -> bool:
        return self.active > 0

    def wait(self, timeout_ms: int = 120000) -> bool:
        return self.pool.waitForDone(timeout_ms)
