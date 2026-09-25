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

    kind: str                       # "move" | "delete" | "undo" | "rename"
    # "move" range l'element, "delete" l'ecarte dans la corbeille de session :
    # meme operation disque, consequences differentes sur les compteurs.
    purpose: str = "move"
    src: Path = None
    label: str = ""
    item_id: str = ""
    dest: Path | None = None
    mode: str = ""                  # mode de suppression
    entry: object = None            # HistoryEntry, pour une annulation
    # Pour un renommage : le nouveau nom, dans le meme dossier.
    new_name: str = ""
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
    # a la fermeture, qu'on peut encore le dire. -2 : relu en partie seulement,
    # il contenait peut-etre autre chose.
    others: int = -1
    # La garde a-t-elle parle de ce dossier a la touche (sa question, ou celle
    # du lot) ? Decide a l'appui : a l'arrivee, l'element a pu etre remplace
    # par un autre, fraichement recompte, et l'on se taisait a tort.
    asked: bool = False

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
        # L'interface n'attend plus que les ffmpeg lachent le fichier : elle les
        # arrete et passe a la suite. C'est ici, en tache de fond, qu'on attend
        # qu'il soit libre — sous Windows, un fichier ouvert ne se deplace pas.
        _wait_free(job.src)
        try:
            if job.kind == "move":
                job.result = actions.retry(
                    actions.move_to, job.src, job.dest,
                    attempts=RETRY_ATTEMPTS, delay=RETRY_DELAY,
                )
                job.reversible = True
                if job.purpose == "delete":
                    _count_others(job)
                else:
                    _carry_thumbs(job.src, job.result)
            elif job.kind == "rename":
                # Dans la meme file que les rangements : sur le NAS, un
                # renommage est un aller-retour reseau, et l'ordre des
                # decisions reste celui de l'historique (Ctrl+Z).
                job.result = actions.retry(
                    actions.rename_to, job.src, job.new_name,
                    attempts=RETRY_ATTEMPTS, delay=RETRY_DELAY,
                )
                job.reversible = True
                _carry_thumbs(job.src, job.result)
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
                if getattr(job.entry, "dst", None):
                    _carry_thumbs(job.entry.dst, job.result)
            job.state = "done"
        except actions.PartialMove as exc:
            # Tout est arrive ; seule la source n'a pas pu etre videe. Refaire
            # la copie aurait cree « (2) » : c'est fait, avec une reserve.
            job.result = exc.target
            job.reversible = True
            job.state = "done"
            job.warning = str(exc)
            if job.kind == "move" and job.purpose != "delete":
                _carry_thumbs(job.src, job.result)
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
            _unblock(job.src)
            try:
                self.signals.done.emit(job)
            except RuntimeError:
                pass            # l'application se ferme : plus personne n'ecoute


def _wait_free(path) -> None:
    """Attend, deux secondes au plus, que plus aucun ffmpeg ne lise ce chemin.

    Au-dela, le transfert essaie quand meme : il reessaie de toute facon.
    """
    try:
        from . import media
        media.wait_free(path, 2.0)
    except Exception:                                   # noqa: BLE001
        pass


def _unblock(path) -> None:
    """Le transfert est fini : les lectures de ce chemin peuvent reprendre."""
    try:
        from . import media
        media.unblock(path)
    except Exception:                                   # noqa: BLE001
        pass


def _carry_thumbs(old, new) -> None:
    """Fait suivre vignettes, sondage et empreinte a une video rangee.

    Ici, dans le fil du transfert : pour un dossier, cela parcourt les
    empreintes connues, quelques dizaines de millisecondes qui n'ont rien a
    faire sur le fil de l'interface. Jamais pour la corbeille.
    """
    if old is None or new is None:
        return
    try:
        from . import media
        media.relocate_thumbs(old, new)
    except Exception:                                   # noqa: BLE001
        # La memoire des images ne doit jamais faire echouer un deplacement
        # qui, lui, a reussi.
        pass


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
    others = 0 if found.unreadable else max(0, found.file_count - found.video_count)
    if others:
        job.others = others
    elif found.unreadable or found.incomplete:
        # Un sous-dossier illisible cachait peut-etre les documents : rien ne
        # permet de dire que ce ne sont que des videos.
        job.others = -2
    else:
        job.others = 0


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
        # Ce qui est encore dans la file, par numero : ce qui s'apprete a
        # quitter sa place se sait sans rien demander au disque.
        self.pending: dict = {}

    def submit(self, transfer: Transfer) -> Transfer:
        self.active += 1
        self.pending[transfer.id] = transfer
        self.changed.emit(self.active)
        self.pool.start(_Runner(self.signals, transfer))
        return transfer

    def _on_done(self, transfer: Transfer) -> None:
        self.pending.pop(transfer.id, None)
        self.active = max(0, self.active - 1)
        self.changed.emit(self.active)
        self.finished.emit(transfer)

    @property
    def busy(self) -> bool:
        return self.active > 0

    def wait(self, timeout_ms: int = 120000) -> bool:
        return self.pool.waitForDone(timeout_ms)
