"""Actions destructrices : déplacement, suppression, annulation."""
from __future__ import annotations

import errno
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import LOCAL_TRASH

# Ce que Windows repond quand le partage ne repond plus : chemin ou nom reseau
# introuvable, delai depasse, connexion coupee ou refusee. Python les traduit
# en « argument invalide » (errno 22) ou en « fichier introuvable » : sans les
# reconnaitre, un NAS endormi se disait « n'existe plus ».
NET_WINERRORS = {51, 53, 59, 64, 67, 121, 1222, 1231, 2250}

# Le suffixe d'une copie en cours d'un volume a l'autre : tant qu'il est la,
# la copie n'est pas finie -- et aucun essai suivant ne la prend pour la cible.
PARTIAL_SUFFIX = ".prisme-partiel"


class ActionError(Exception):
    """Un echec dit en francais. `retry` : vaut-il la peine d'insister ?

    Un element introuvable ne reviendra pas en vingt secondes : on ne
    reessaie plus douze fois ce qui n'a aucune chance d'aboutir.
    """

    def __init__(self, message: str = "", retry: bool = True):
        super().__init__(message)
        self.retry = retry


class PartialMove(ActionError):
    """Tout est arrive a destination, mais l'ancien emplacement n'a pas pu etre
    vide en entier. Le deplacement compte comme fait ; `target` dit ou."""

    def __init__(self, message: str, target: Path):
        super().__init__(message, retry=False)
        self.target = Path(target)


@dataclass
class HistoryEntry:
    action: str          # "move" | "delete"
    src: Path            # emplacement d'origine
    dst: Path | None     # emplacement après action (None si non réversible)
    label: str
    reversible: bool


def is_network_error(exc: BaseException) -> bool:
    """Vrai pour une erreur qui dit « le partage ne repond pas »."""
    return getattr(exc, "winerror", None) in NET_WINERRORS


def describe(exc: BaseException) -> str:
    """L'erreur, avec son vrai nom quand c'est le reseau qui manque."""
    if is_network_error(exc):
        return f"NAS injoignable ({exc})"
    return str(exc)


def probe(path) -> str:
    """« ok », « absent » ou « injoignable ».

    « Introuvable » ne se dit que si l'on a pu regarder : un element dont le
    volume ne repond pas n'a pas disparu. On interroge alors la racine du
    volume -- un aller-retour de plus, sur le seul chemin de l'echec.
    """
    try:
        os.stat(path)
        return "ok"
    except OSError as exc:
        if is_network_error(exc):
            return "injoignable"
        if not isinstance(exc, (FileNotFoundError, NotADirectoryError)):
            # Acces refuse, nom trop long... : l'element est la, ou du moins
            # rien ne dit qu'il n'y est plus. L'action dira ce qui cloche.
            return "ok"
    anchor = Path(path).anchor
    if anchor:
        try:
            os.stat(anchor)
        except OSError:
            return "injoignable"
    return "absent"


def _require(path: Path, what: str = "Introuvable") -> None:
    state = probe(path)
    if state == "absent":
        raise ActionError(f"{what} : {path}", retry=False)
    if state == "injoignable":
        raise ActionError(f"NAS injoignable : {path}")


def unique_target(directory: Path, name: str) -> Path:
    """Retourne un chemin libre dans `directory`, en suffixant (2), (3)... si besoin."""
    candidate = Path(directory) / name
    if not _taken(candidate):
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for counter in range(2, 10000):
        candidate = Path(directory) / f"{stem} ({counter}){suffix}"
        if not _taken(candidate):
            return candidate
    raise ActionError(f"Impossible de trouver un nom libre dans {directory}",
                      retry=False)


def _taken(path: Path) -> bool:
    state = probe(path)
    if state == "injoignable":
        raise ActionError(f"NAS injoignable : {path.parent}")
    return state == "ok"


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
    Une erreur du système (réseau qui hoquette, fichier tenu) est réessayée
    comme une autre, puis rendue en ActionError : autrefois elle traversait
    tout, et la file des transferts restait bloquée « en cours » pour toujours.
    """
    last: ActionError | None = None
    for attempt in range(attempts):
        try:
            return func(*args)
        except ActionError as exc:
            if not exc.retry:
                raise
            last = exc
        except (OSError, shutil.Error) as exc:
            last = ActionError(describe(exc))
            last.__cause__ = exc
        if attempt + 1 < attempts:
            time.sleep(delay * (attempt + 1))
    raise last if last is not None else ActionError("Action impossible")


def _forget(src: Path, target: Path) -> None:
    """Retire du cache les dossiers que ce deplacement va changer.

    Tous les ancetres, et non les seuls parents : un element de la racine
    porte la liste de toutes les videos de son arborescence, et sa date ne
    bouge pas quand on range plus bas (« Films\\Action »). Il gardait alors la
    video a son ancien chemin, et ne la montrait jamais au nouveau.
    """
    # Import tardif : l'index depend de modules qui dependent d'ici.
    from .index import INDEX
    from .stamps import forget as forget_stamp
    done: set = set()
    for start in (Path(src).parent, Path(target).parent):
        for folder in (start, *start.parents):
            key = str(folder)
            if key not in done:
                done.add(key)
                INDEX.forget(folder)
    INDEX.forget(src)
    # Le fichier change de place : l'empreinte retenue pour lui ne vaut plus.
    forget_stamp(src)


def _carry(src: Path, target: Path) -> None:
    """Fait suivre sondages, plans, empreintes, titres et « deja vu »."""
    try:
        from .index import INDEX
        INDEX.relocate(src, target)
    except Exception:                                   # noqa: BLE001
        # La memoire de l'index ne doit jamais faire echouer un deplacement
        # qui, lui, a reussi.
        pass


def _relocate(src: Path, target: Path) -> None:
    """Déplace src vers target.

    On tente d'abord un renommage : sur un même volume il est atomique, donc un
    verrou transitoire échoue proprement au lieu de laisser une copie partielle.
    D'un disque à l'autre, la copie se fait sous un nom provisoire et ne prend
    le vrai nom qu'une fois complète et vérifiée (voir `_move_across`).
    """
    src, target = Path(src), Path(target)
    _forget(src, target)
    try:
        os.rename(src, target)
    except OSError as exc:
        cross_device = exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17
        if not cross_device:
            raise
        try:
            _move_across(src, target)
        except PartialMove:
            _carry(src, target)
            raise
    _carry(src, target)


def _move_across(src: Path, target: Path) -> None:
    """Déplace d'un volume à l'autre sans jamais laisser de copie trompeuse.

    `shutil.move` copiait directement sous le nom final puis effaçait : une
    coupure en pleine copie laissait un dossier partiel sous le bon nom, et
    chaque nouvel essai en créait un autre, « (2) », « (3) »... On copie donc
    sous un nom provisoire, on vérifie le nombre de fichiers et les tailles,
    et seulement alors on donne le vrai nom. Puis on vide la source en allant
    jusqu'au bout malgré un fichier récalcitrant : ce qui résiste est signalé
    par `PartialMove`, sans refaire la copie.
    """
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    _discard(partial)           # le reste d'un essai interrompu
    try:
        if src.is_dir():
            shutil.copytree(src, partial, symlinks=True)
        else:
            shutil.copy2(src, partial)
        want, got = _inventory(src), _inventory(partial)
        if want != got:
            raise OSError(f"copie incomplète : {got[0]} fichier(s) et {got[1]} "
                          f"octet(s) arrivés, sur {want[0]} et {want[1]}")
        os.rename(partial, target)
    except BaseException:
        _discard(partial)
        raise
    removed, left = _remove_tree(src)
    if left:
        raise PartialMove(
            f"« {src.name} » est arrivé à destination, mais {len(left)} "
            f"élément(s) n'ont pas pu être retirés de l'ancien emplacement "
            f"({src})", target)


def _discard(path: Path) -> None:
    try:
        if os.path.lexists(path):
            _remove_tree(path)
    except OSError:
        pass


def _inventory(path: Path) -> tuple:
    """(nombre de fichiers, octets) : de quoi dire qu'une copie est complète."""
    top = str(path)
    if not os.path.isdir(top):
        return 1, os.stat(top).st_size
    count = size = 0
    stack = [top]
    while stack:
        with os.scandir(stack.pop()) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                else:
                    count += 1
                    size += entry.stat(follow_symlinks=False).st_size
    return count, size


def _unlink(path: str) -> bool:
    """Efface un fichier, en insistant un peu. Rend vrai s'il n'est plus là."""
    for attempt in range(3):
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            # Lecture seule : Windows refuse d'effacer tant qu'on ne l'a pas levée.
            try:
                os.chmod(path, stat.S_IWRITE)
            except OSError:
                pass
        except OSError:
            pass
        time.sleep(0.1 * (attempt + 1))
    return False


def _remove_link(path: str) -> bool:
    """Retire un lien ou une jonction, jamais ce vers quoi il pointe."""
    try:
        os.unlink(path)
        return True
    except OSError:
        try:
            os.rmdir(path)
            return True
        except OSError:
            return False


def _remove_tree(top) -> tuple:
    """Efface tout ce qui peut l'être. Rend (fichiers effacés, restes).

    `shutil.rmtree` s'arrêtait au premier fichier verrouillé, après avoir
    détruit tout ce qui le précédait, sans rien dire de ce qu'il laissait.
    Ici on va jusqu'au bout, on insiste un peu sur ce qui résiste, et l'on
    rend la liste exacte de ce qui reste.
    """
    top = str(top)
    try:
        info = os.lstat(top)
    except FileNotFoundError:
        return 0, []
    if not stat.S_ISDIR(info.st_mode) or _is_link(top):
        if _is_link(top):
            return (1, []) if _remove_link(top) else (0, [top])
        return (1, []) if _unlink(top) else (0, [top])
    removed = 0
    left: list = []
    stack = [(top, False)]
    while stack:
        current, emptied = stack.pop()
        if emptied:
            try:
                os.rmdir(current)
            except FileNotFoundError:
                pass
            except OSError:
                if not any(item.startswith(current + os.sep) for item in left):
                    left.append(current)
            continue
        try:
            with os.scandir(current) as listing:
                entries = list(listing)
        except OSError:
            left.append(current)
            continue
        stack.append((current, True))
        for entry in entries:
            try:
                is_link = entry.is_symlink() or _is_junction(entry)
                is_dir = not is_link and entry.is_dir(follow_symlinks=False)
            except OSError:
                is_link, is_dir = False, False
            if is_link:
                if _remove_link(entry.path):
                    removed += 1
                else:
                    left.append(entry.path)
            elif is_dir:
                stack.append((entry.path, False))
            elif _unlink(entry.path):
                removed += 1
            else:
                left.append(entry.path)
    return removed, left


def _is_link(path: str) -> bool:
    try:
        return os.path.islink(path) or bool(getattr(os.path, "isjunction", None)
                                            and os.path.isjunction(path))
    except OSError:
        return False


def _is_junction(entry) -> bool:
    test = getattr(entry, "is_junction", None)
    try:
        return bool(test and test())
    except OSError:
        return False


def move_to(src: Path, dest_dir: Path) -> Path:
    """Déplace src dans dest_dir. Retourne le nouveau chemin."""
    src = Path(src)
    dest_dir = Path(dest_dir)
    _require(src)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ActionError(f"Destination inaccessible : {dest_dir} ({describe(exc)})",
                          retry=is_network_error(exc)) from exc

    # Refuse de déplacer un dossier dans lui-même ou dans un de ses descendants.
    try:
        if src.is_dir():
            try:
                dest_dir.resolve().relative_to(src.resolve())
                raise ActionError("La destination est à l'intérieur du dossier à déplacer.",
                                  retry=False)
            except ValueError:
                pass
        # Calcule une fois par essai, et jamais trompe par une copie en cours :
        # celle-ci porte un autre nom tant qu'elle n'est pas complete.
        target = unique_target(dest_dir, src.name)
        _relocate(src, target)
    except ActionError:
        raise
    except (OSError, shutil.Error) as exc:
        raise ActionError(f"Déplacement impossible : {describe(exc)}") from exc
    return target


# Ce que Windows refuse dans un nom de fichier, et les noms qu'il se reserve
# (« CON.mp4 » compris) : un renommage qui les contient echoue sur le NAS avec
# un message obscur, ou cree un fichier qu'on ne peut plus ouvrir.
FORBIDDEN_CHARS = '<>:"/\\|?*'
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                  *(f"COM{n}" for n in range(1, 10)),
                  *(f"LPT{n}" for n in range(1, 10))}


def name_problem(name: str) -> str:
    """Ce qui empeche `name` d'etre un nom de fichier Windows, dit en francais.

    Vide si le nom convient. Rien n'est demande au disque : la question vaut
    avant meme d'envoyer le renommage.
    """
    if not name or not name.strip():
        return "Le nom ne peut pas être vide."
    bad = sorted({char for char in name
                  if char in FORBIDDEN_CHARS or ord(char) < 32})
    if bad:
        shown = " ".join(char if ord(char) >= 32 else "(contrôle)" for char in bad)
        return f"Caractère interdit sous Windows : {shown}"
    if name != name.rstrip(" ."):
        return "Un nom ne peut pas finir par un point ou une espace."
    if name.split(".")[0].strip().upper() in RESERVED_NAMES:
        return f"« {name.split('.')[0]} » est un nom réservé par Windows."
    if len(name) > 255:
        return "Nom trop long : 255 caractères au plus."
    return ""


def rename_to(src: Path, new_name: str) -> Path:
    """Renomme sur place, dans le meme dossier. Rend le nouveau chemin.

    Jamais « (2) » comme pour un rangement : un nom deja pris est un refus,
    pas un nom voisin qu'on n'aurait pas choisi. Changer la seule casse
    (« clip » en « Clip ») reste permis, bien que Windows y voie le meme nom.
    Sondages, empreintes et « deja vu » suivent (`_relocate`).
    """
    src = Path(src)
    problem = name_problem(new_name)
    if problem:
        raise ActionError(problem, retry=False)
    target = src.with_name(new_name)
    if str(target) == str(src):
        return src
    _require(src)
    same = os.path.normcase(str(target)) == os.path.normcase(str(src))
    if not same and _taken(target):
        raise ActionError(f"« {new_name} » existe déjà dans ce dossier.",
                          retry=False)
    try:
        # os.rename ne remplace jamais un fichier existant sous Windows : un
        # homonyme apparu entre-temps fait echouer, il n'est pas ecrase.
        _relocate(src, target)
    except ActionError:
        raise
    except FileExistsError as exc:
        raise ActionError(f"« {new_name} » existe déjà dans ce dossier.",
                          retry=False) from exc
    except (OSError, shutil.Error) as exc:
        raise ActionError(f"Renommage impossible : {describe(exc)}") from exc
    return target


def delete(path: Path, mode: str = "recycle") -> tuple[Path | None, bool]:
    """Supprime selon le mode choisi.

    Retourne (nouvel_emplacement, réversible_dans_l_app).
    """
    path = Path(path)
    _require(path)

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
            removed, left = _remove_tree(path)
        except OSError as exc:
            raise ActionError(f"Suppression impossible : {describe(exc)}") from exc
        if left:
            # Le compte exact, et ou regarder : l'ancien message disait « tout
            # est reste » alors que quarante fichiers sur quarante et un
            # venaient d'etre detruits.
            files = [item for item in left if not os.path.isdir(item)]
            count = len(files) or len(left)
            raise ActionError(
                f"« {path.name} » : {removed} fichier(s) supprimé(s), {count} "
                f"impossible(s) à supprimer (verrouillé ou en cours d'usage), "
                f"encore dans {path}")
        _forget_tree(path)
        return None, False

    if mode == "local_trash":
        target = move_to(path, LOCAL_TRASH)
        return target, True

    # Corbeille Windows : récupérable depuis l'explorateur, mais pas via Ctrl+Z ici.
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise ActionError(
            "Le module send2trash est absent : pip install send2trash", retry=False
        ) from exc
    try:
        send2trash(str(path))
    except Exception as exc:  # send2trash lève des exceptions variées selon l'OS
        raise ActionError(f"Envoi à la corbeille impossible : {exc}") from exc
    _forget_tree(path)
    return None, False


def _forget_tree(path: Path) -> None:
    """Ce qui n'existe plus n'a plus de sondage, de plan ni d'empreinte."""
    try:
        from .index import INDEX
        INDEX.forget_tree(path)
    except Exception:                                   # noqa: BLE001
        pass


def undo(entry: HistoryEntry) -> Path:
    """Remet un élément à son emplacement d'origine. Rend l'endroit réel."""
    if not entry.reversible or entry.dst is None:
        raise ActionError("Cette action ne peut pas être annulée depuis l'application.",
                          retry=False)
    dst = Path(entry.dst)
    _require(dst, "Introuvable à son nouvel emplacement")
    src = Path(entry.src)
    try:
        src.parent.mkdir(parents=True, exist_ok=True)
        if os.path.normcase(str(dst)) == os.path.normcase(str(src)):
            # Un renommage de la seule casse : pour Windows, c'est le meme
            # nom -- le croire pris donnait « clip (2).mp4 ».
            target = src
        else:
            target = unique_target(src.parent, src.name)
        _relocate(dst, target)
    except ActionError:
        raise
    except (OSError, shutil.Error) as exc:
        raise ActionError(f"Restauration impossible : {describe(exc)}") from exc
    return target


def open_recycle_bin() -> None:
    if sys.platform == "win32":
        subprocess.Popen(
            ["explorer", "shell:RecycleBinFolder"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
