"""Configuration persistante et emplacements de VideoSorter."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

APP_NAME = "Prisme"
# Le logiciel s'est appele VideoSorter. Son cache pese plus d'un gigaoctet de
# vignettes et un index de vingt-six mega : on le reprend tel quel au premier
# lancement, au lieu de tout refabriquer.
_OLD_NAME = "VideoSorter"
_LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home())
APP_DIR = _LOCAL / APP_NAME
# Les tests et les outils de mesure travaillent dans un dossier a eux, pose
# AVANT tout import : l'index s'ouvre des le chargement du module, et c'est
# ainsi qu'un test lance pendant que Prisme tournait a pu effacer l'index
# reel. Avec PRISME_SANDBOX, reglages, index, vignettes et journaux vivent
# la, et nulle part ailleurs.
SANDBOX = os.environ.get("PRISME_SANDBOX", "").strip()
if SANDBOX:
    APP_DIR = Path(SANDBOX)


def _has_thumbs(folder: Path) -> bool:
    """Vrai si ce dossier porte de vraies vignettes — le seul bien qui compte.

    On ne se fie pas a l'existence du dossier : il se cree tout seul des
    qu'un module s'ouvre, bien avant qu'on ait pu decider quoi que ce soit.
    C'est precisement ce qui avait fait echouer la premiere reprise.
    """
    thumbs = folder / "thumbs"
    if not thumbs.is_dir():
        return False
    for _dirpath, _dirs, files in os.walk(thumbs):
        if files:
            return True
    return False


def adopt_old_cache() -> str:
    """Reprend le cache de l'ancien nom, et **fusionne** avec celui-ci.

    Une seule regle pour les vignettes : ce qui manque ici est deplace, ce
    qui s'y trouve deja est laisse. C'est sans risque, puisqu'une vignette
    porte le nom de son contenu — meme nom veut dire meme image.

    Fichier par fichier, et non d'un seul renommage de dossier : un seul
    fichier verrouille par une autre fenetre faisait echouer le tout, et
    soixante-quinze mille vignettes restaient de l'autre cote. Ce qui ne
    peut pas bouger aujourd'hui bougera au prochain lancement.
    """
    old = _LOCAL / _OLD_NAME
    if old == APP_DIR or not old.is_dir():
        return ""
    moved = 0
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""

    for entry in sorted(old.rglob("*")):
        if not entry.is_file():
            continue
        target = APP_DIR / entry.relative_to(old)
        if target.exists():
            if "thumbs" in entry.relative_to(old).parts:
                # Meme nom, donc meme image : une vignette porte le nom de
                # son contenu. On retire le doublon plutot que de le laisser
                # occuper la place, et le vieux dossier peut disparaitre.
                try:
                    entry.unlink()
                except OSError:
                    pass
                continue
            # Reglages, notes, index : le plus recent gagne. Un import suffit
            # a creer une ebauche ici, et elle ne doit pas primer sur ce qui
            # a reellement servi la-bas.
            try:
                if entry.stat().st_mtime <= target.stat().st_mtime:
                    continue
                target.unlink()
            except OSError:
                continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            entry.rename(target)
            moved += 1
        except OSError:
            continue            # verrouille : ce sera pour la prochaine fois

    # Les dossiers vides s'en vont, du plus profond au plus haut.
    for folder in sorted(old.rglob("*"), reverse=True):
        if folder.is_dir():
            try:
                folder.rmdir()
            except OSError:
                pass
    try:
        old.rmdir()
    except OSError:
        pass
    return str(old) if moved else ""


# Au lancement seulement, jamais dans le bac a sable des tests.
ADOPTED = "" if SANDBOX else adopt_old_cache()
# Le dossier de cette machine, avant tout partage : c'est la que vit ce qui
# ne doit jamais partir sur le reseau (reglages, favoris, verrou, journaux).
_HOME = APP_DIR
CONFIG_PATH = APP_DIR / "config.json"
def _chosen_cache() -> tuple:
    """Le cache designe ailleurs, s'il l'a ete.

    Deux ordinateurs qui regardent le meme partage fabriquent exactement les
    memes vignettes : leur nom ne depend que du chemin, de la taille et de la
    date du fichier. Les mettre en commun evite au second de refaire le
    travail du premier — et c'est le travail le plus long de tous.

    Rend (dossier, portable). « Portable » veut dire : un dossier « cache »
    pose a cote du programme, qui voyage avec lui — l'index y vit aussi.
    Sinon la variable d'environnement PRISME_CACHE, ou un fichier
    `prisme.cache` pose a cote du programme, qui ne contient qu'un chemin. Un fichier plutot qu'un reglage dans
    config.json : celui-ci vit deja dans le cache, et l'on ne peut pas y lire
    ou il se trouve.
    """
    told = os.environ.get("PRISME_CACHE", "").strip()
    here = [Path(sys.argv[0]).resolve().parent, Path.cwd()]
    if not told:
        # Un dossier « cache » pose a cote du programme le rend portable :
        # tout voyage ensemble, et rien ne depend de la machine. C'est ce que
        # contient l'archive complete.
        for folder in here:
            beside = folder / "cache"
            if beside.is_dir():
                return beside, True
    if not told:
        for folder in here:
            note = folder / "prisme.cache"
            try:
                if note.is_file():
                    told = note.read_text(encoding="utf-8").strip()
                    if told:
                        break
            except OSError:
                continue
    if not told:
        return None, False
    try:
        chosen = Path(told).expanduser()
        chosen.mkdir(parents=True, exist_ok=True)
        return chosen, False
    except OSError:
        return None, False


SHARED_DIR, PORTABLE = (None, False) if SANDBOX else _chosen_cache()
if SHARED_DIR is not None:
    APP_DIR = SHARED_DIR

# Les vignettes se partagent sans risque : ce sont des fichiers independants,
# nommes par leur contenu. L'index, lui, est une base : deux ecritures en
# meme temps par le reseau la fragiliseraient. Il reste donc chez chacun,
# sauf demande expresse.
THUMB_DIR = APP_DIR / "thumbs"
# Un cache pose a cote du programme voyage entier : tout l'accompagne. Un
# cache designe sur un partage, lui, est peut-etre lu par deux machines a la
# fois, et peut dormir au lancement : l'index, les favoris, le verrou et le
# journal des visionnages restent alors chez chacune. Les favoris y etaient
# partis -- deux PC s'ecrasaient leurs etoiles, et un NAS endormi au
# lancement faisait repartir de zero, puis la premiere etoile ecrasait tout.
PRIVATE_DIR = APP_DIR if (SHARED_DIR is None or PORTABLE) else _HOME
INDEX_PATH = PRIVATE_DIR / "index.db"
LOCK_PATH = PRIVATE_DIR / "prisme.lock"
CRASH_LOG = PRIVATE_DIR / "plantage.log"
RATINGS_PATH = PRIVATE_DIR / "ratings.json"
# Ou vivaient les favoris avant : repris une fois, puis laisses tels quels.
SHARED_RATINGS_PATH = (SHARED_DIR / "ratings.json"
                       if SHARED_DIR is not None and not PORTABLE else None)
# Anciens caches JSON, repris puis effaces par l'index au premier lancement.
PROBE_CACHE_PATH = APP_DIR / "probe-cache.json"
SCAN_CACHE_PATH = APP_DIR / "scan-cache.json"
# Sur le partage quand il y en a un : ecarter y reste un simple renommage.
LOCAL_TRASH = APP_DIR / "_TRASH"
# Le dossier de session, pose sous la racine triee (voir trash.py).
TRASH_FOLDER_NAME = ".videosorter-corbeille"

VIDEO_FILE_EXTS = frozenset({
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".webm",
    ".mpg", ".mpeg", ".m2ts", ".mts", ".ts", ".vob", ".3gp", ".ogv",
    ".rm", ".rmvb", ".asf", ".divx", ".f4v",
})
PHOTO_EXTS = frozenset({
    ".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".bmp", ".tif",
    ".tiff", ".heic", ".heif",
})
ALL_MEDIA_EXTS = VIDEO_FILE_EXTS | PHOTO_EXTS

# Ce que l'on trie en ce moment : des videos, ou des photos. Tout Prisme
# demande « est-ce un media ? » a cet ensemble-ci ; choisir les photos sur
# l'accueil le remplit sur place (`set_media_kind`), et chaque module qui l'a
# importe voit aussitot le changement. Il garde son nom d'origine : c'est
# celui qu'emploient l'analyse, l'index, les doublons et la fenetre.
VIDEO_EXTS = set(VIDEO_FILE_EXTS)
KIND_VIDEO, KIND_PHOTO = "video", "photo"
_KIND = [KIND_VIDEO]


def media_kind() -> str:
    return _KIND[0]


def photo_mode() -> bool:
    return _KIND[0] == KIND_PHOTO


def is_photo(path) -> bool:
    name = str(path)
    dot = name.rfind(".")
    return dot > 0 and name[dot:].lower() in PHOTO_EXTS


def set_media_kind(kind: str) -> None:
    """Bascule tout Prisme sur les videos ou sur les photos."""
    kind = KIND_PHOTO if kind == KIND_PHOTO else KIND_VIDEO
    VIDEO_EXTS.clear()
    VIDEO_EXTS.update(PHOTO_EXTS if kind == KIND_PHOTO else VIDEO_FILE_EXTS)
    _KIND[0] = kind


def kind_paths(kind: str) -> tuple:
    """L'index et les favoris de chaque collection : un dossier deja analyse
    pour ses videos ne doit pas passer pour « a jour » quand on y cherche des
    photos, et une etoile de photo n'a rien a faire parmi les videos."""
    if kind == KIND_PHOTO:
        return PRIVATE_DIR / "index-photos.db", PRIVATE_DIR / "ratings-photos.json"
    return INDEX_PATH, RATINGS_PATH

# Ordre d'attribution automatique des touches : les chiffres d'abord, puis les
# lettres dans l'ordre du clavier AZERTY. Les commandes de l'application sont
# sur Ctrl ou sur des touches de navigation, sauf une : « f » ouvre la fiche
# survolee. Elle etait pourtant donnee a la dix-huitieme destination, qui ne
# rangeait donc jamais rien, sans un mot : elle est retiree de l'ordre et
# reservee. 0 a 5 sont les favoris : une main sur les chiffres marque,
# l'autre range. Les destinations prennent donc la suite, a partir de 6.
KEY_ORDER = "6789azertyuiopqsdghjklmwxcvbn"
RESERVED_KEYS = {"0", "1", "2", "3", "4", "5", "f"}

DEFAULTS = {
    "root": "",
    "recent_roots": [],
    "destinations": [],          # [{"key": "1", "label": "2019", "path": "D:/Tri/2019"}]
    "muted": True,
    "scroll_seconds": 5,         # pas de la molette dans une video
    "tree_root": "",             # racine du panneau d'arborescence
    "tree_visible": False,
    "filter_include": "",       # termes a chercher dans le nom
    "filter_exclude": "",       # termes qui ecartent un element
    "preview_seconds": 10,
    "stay_in_folder": False,     # ◂ ▸ restent dans le dossier de la video
    # Apres ★ au clavier, la fiche passe a la suivante. Absente d'ici, la
    # cle etait oubliee au lancement suivant malgre l'enregistrement.
    "advance_after_star": False,
    "aside_split": None,         # largeurs planche / lecteur de droite
    # Le lecteur flottant : il prend le relais, toujours au premier plan, des
    # que Prisme est reduit ou recouvert pendant qu'une video joue.
    "float_auto": True,
    "float_geometry": None,      # [x, y, largeur, hauteur] du lecteur flottant
    # Ou prendre l'image d'une carte : le reglage le plus cher de tous.
    # 0 = le plus rapide, 6 = des images plus parlantes et deux fois plus lentes.
    "preview_start": 2.0,
    "thumb_count": 10,
    "thumb_width": 480,
    "delete_mode": "recycle",    # recycle | permanent | local_trash
    "ffmpeg": "",                # vide => recherche automatique
    "ffprobe": "",
    "window": {"w": 1400, "h": 900},
    "ignore_dpi": False,         # ignorer la mise a l'echelle de Windows
    # Decoder par la carte graphique (lu au lancement, voir main.py). Faux :
    # le processeur decode, sans le gel qu'impose a chaque video ouverte la
    # creation d'un peripherique Direct3D.
    "hw_decoding": False,
    # Le partage a distance. Actif des le depart : il ne sert a rien s'il
    # faut penser a l'allumer. Mais il reste inerte tant qu'aucun mot de
    # passe n'est pose — on n'ouvre pas une collection sans serrure.
    "share": True,
    "share_port": 8713,
    "share_host": "127.0.0.1",   # le tunnel fait le reste ; aucun port ouvert
    "tunnel_auto": True,         # ouvrir l'adresse publique des le lancement
    # « cloudflare » : prete en dix secondes, mais elle change a chaque fois.
    # « tailscale » : la meme tous les jours, gratuite, sans nom de domaine —
    # au prix d'une mise en route.
    "tunnel_kind": "cloudflare",
    "share_salt": "",
    "share_digest": "",
    # Des dossiers masques par choix — leur contenu n'apparait nulle part
    # tant que l'interrupteur est leve.
    "veiled_names": ["BIN"],
    # Ceux de la liste qu'on a reaffiches un par un, sans les oublier : une
    # case a recocher suffit a les masquer de nouveau.
    "veiled_off": [],
    "show_veiled": False,
    "quiet_explained": False,    # le repli s'est-il deja explique une fois ?
    "pin_salt": "",              # le code PIN : sel et empreinte, jamais en clair
    "pin_digest": "",
    "pin_failures": 0,           # erreurs de suite, pour l'attente imposee
    "pin_wait_until": 0,         # pas de nouvel essai avant cet instant
    "licence_key": "",           # la cle de licence (PRISME1-…), signee par le site
    "trial_started": 0,          # debut de l'essai de 14 jours
    "licence_seen": 0,           # l'heure la plus tardive vue : l'horloge ne recule pas
    "licence_checked": 0,        # derniere demande de prolongation au site
    "skip_hidden": True,
    "use_scan_cache": True,      # reutiliser l analyse precedente
    "sort_mode": "random",       # random | duration_desc | size_asc | …
    "content": "folders",        # conserve pour compatibilite
    "view": "browse",            # conserve pour compatibilite
    "tab": "folders",            # onglet : folders | videos | tags
    "tag_family": "mine",        # mots-cles affiches : mine | top
    "tree_action": "send",       # clic dans l arborescence : send | go
    "tags": [],
    "last_item": "",             # dernier element regarde, pour y revenir
    "collection": {},            # videos, vignettes, dates : l'etat affiche en haut
    "stars_pick": -1,            # -1 : toutes les notes ; 0 a 5 : exactement
    "only_unseen": False,        # ne montrer que ce qui reste a voir
    "orientations": ["vertical", "horizontal"],  # ce qu'on veut voir
    "folder_min": 0,             # dossiers d'au moins tant de videos (0 : tous)
    "folder_max": 0,             # d'au plus tant (0 : sans limite)
    "searches": [],              # recherches enregistrees : nom, requete, tri, non-vus, onglet
    "burst": False,              # rafale : passer tout seul apres quelques secondes
    "wall_panes": 3,             # panneaux du mur
    "wall_orientation": "vertical",  # ce qu'il y pioche : vertical | horizontal | any
    "thumbs_last_run": "",       # date de la derniere preparation menee a terme                  # mots-cles, un par ligne
    "board_columns": 5,          # cartes par rangee en vue planche
    "expand_parents": True,      # traverser les dossiers prefixes « + »
    "web_search_api_key": "",           # cle SerpAPI (gratuite, 250 recherches/mois)
    "web_search_min_duration_min": 0,   # filtre de la recherche web, en minutes
    "web_search_min_height": 0,         # filtre de la recherche web, en pixels
    "web_search_max_sites": 15,         # nombre de sites explores par recherche
    "web_search_max_results": 30,       # plafond de resultats, pour rester qualitatif
    "web_search_strict_keywords": True, # tous les mots-cles requis, pas un seul
    "web_search_known_domains": "",     # domaines de confiance, pour restreindre la recherche
    "web_search_discover_new_sites": False,  # completer par SerpAPI (quota limite)
    # L'accueil : ce qu'on a trie la derniere fois (video | photo), et les
    # reglages propres a la collection de photos (voir PER_KIND).
    "media_kind": "video",
    "photo": {},
    "slideshow_seconds": 6,      # diaporama des photos : une image toutes les…
    # La duree a-t-elle ete choisie ? Sinon, les 4 s d'avant passent a 6.
    "slideshow_chosen": False,
}

# Ce qui appartient a une collection et non a Prisme : la racine, ses
# destinations, ses filtres. En mode photo, ces reglages vivent a part, sous
# « photo » : on ne range pas une photo dans le dossier des films.
PER_KIND = frozenset({
    "root", "recent_roots", "destinations", "tree_root", "filter_include",
    "filter_exclude", "tab", "tags", "last_item", "collection", "searches",
    "thumbs_last_run", "sort_mode", "stars_pick", "only_unseen",
    "folder_min", "folder_max", "orientations",
})


class Config:
    """Petit wrapper JSON, tolerant aux fichiers absents ou corrompus.

    Tolerant ne veut pas dire oublieux : un fichier illisible etait remplace
    en silence par les reglages par defaut, et l'ecriture suivante effacait
    destinations, mots-cles, recherches et mot de passe du partage. Il est
    desormais mis de cote, la copie de secours reprend sa place, et `problem`
    le dit.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else CONFIG_PATH
        self._timer = None
        self.data = json.loads(json.dumps(DEFAULTS))
        # Ce qu'il faudrait dire a l'utilisateur, s'il y a lieu.
        self.problem = ""
        # Vrai quand le fichier existe mais n'a pas pu etre lu : on ne
        # l'ecrase pas avec des reglages qui n'en sont pas.
        self.read_only = False
        self.load()

    @property
    def backup_path(self) -> Path:
        return self.path.with_name(self.path.name + ".bak")

    def load(self) -> None:
        text = _read_text(self.path)
        if text is None:
            return                      # premier lancement : les defauts
        if isinstance(text, OSError):
            self.read_only = True
            self.problem = (f"Réglages illisibles pour l'instant ({text}) : "
                            "ils ne seront pas réécrits pendant cette séance.")
            return
        raw = _parse_object(text)
        if raw is None:
            aside = _set_aside(self.path)
            text = _read_text(self.backup_path)
            raw = _parse_object(text) if isinstance(text, str) else None
            where = f" (mis de côté sous « {aside.name} »)" if aside else ""
            self.problem = ("Le fichier de réglages était abîmé" + where + (
                " : la copie de secours a été reprise." if raw is not None
                else " : les réglages repartent de zéro."))
            if aside is None:
                # Impossible de l'ecarter : on ne l'ecrase pas non plus.
                self.read_only = True
            if raw is None:
                return
        else:
            # Une copie par lancement, pas a chaque ecriture : c'est d'elle
            # qu'on repartira si le fichier s'abimait.
            _copy_quietly(self.path, self.backup_path)
        for key, value in raw.items():
            if key in DEFAULTS:
                self.data[key] = value
        self._migrate_reserved_keys()
        # Quatre secondes, l'ancien defaut, passaient trop vite : qui ne l'a
        # pas choisi passe a six.
        if not self.data.get("slideshow_chosen") and self.data.get("slideshow_seconds") == 4:
            self.data["slideshow_seconds"] = DEFAULTS["slideshow_seconds"]

    def _migrate_reserved_keys(self) -> None:
        """Deplace les destinations posees sur une touche devenue reservee.

        Les configurations ecrites avant que 0 a 5 servent aux favoris, ou
        avant que « f » ouvre la fiche, gardaient ces touches : le raccourci
        ne se serait plus jamais declenche, sans rien dire.
        """
        destinations = self.data.get("destinations") or []
        taken = {d.get("key") for d in destinations if d.get("key") not in RESERVED_KEYS}
        moved = False
        for dest in destinations:
            if dest.get("key") not in RESERVED_KEYS:
                continue
            for candidate in KEY_ORDER:
                if candidate not in taken and candidate not in RESERVED_KEYS:
                    dest["key"] = candidate
                    taken.add(candidate)
                    moved = True
                    break
            else:
                dest["key"] = ""
                moved = True
        if moved:
            self.save()

    def save_soon(self) -> None:
        """Ecrit dans un instant, une fois pour toutes les modifications.

        Chaque onglet, chaque fiche ecrivait le fichier sur-le-champ. On
        regroupe : la derniere demande l'emporte, une demi-seconde plus tard.
        """
        from PySide6.QtCore import QCoreApplication, QTimer
        if QCoreApplication.instance() is None:
            return self.save()
        if self._timer is None:
            self._timer = QTimer()
            self._timer.setSingleShot(True)
            self._timer.setInterval(500)
            self._timer.timeout.connect(self.save)
        self._timer.start()

    def save(self) -> None:
        """Ecrit sur le disque pour de bon, ou ne touche a rien.

        Ecriture a cote puis remplacement : une coupure en plein milieu laisse
        l'ancien fichier intact, et le vidage force garantit que le nouveau
        est vraiment sur le disque avant de prendre sa place. Une erreur ne
        remonte plus dans l'interface : elle est notee, et la sauvegarde
        suivante reessaiera.
        """
        if self._timer is not None:
            self._timer.stop()
        if self.read_only:
            return
        problem = _write_atomic(
            self.path, json.dumps(self.data, indent=2, ensure_ascii=False))
        if problem:
            self.problem = f"Réglages non enregistrés : {problem}"

    def _slot(self, key, kind: str | None = None) -> dict:
        """Le dictionnaire ou vit `key` : celui des photos, en mode photo,
        pour ce qui appartient a la collection."""
        kind = kind or media_kind()
        if kind == KIND_PHOTO and key in PER_KIND:
            slot = self.data.get("photo")
            if not isinstance(slot, dict):
                slot = self.data["photo"] = {}
            if key not in slot:
                # Une copie : les listes par defaut ne doivent jamais etre
                # modifiees sur place.
                slot[key] = json.loads(json.dumps(DEFAULTS.get(key)))
            return slot
        return self.data

    def __getitem__(self, key):
        return self._slot(key).get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self._slot(key)[key] = value

    def get(self, key, default=None):
        return self._slot(key).get(key, default)

    def of_kind(self, kind: str, key):
        """Un reglage de l'autre collection, sans basculer : l'accueil montre
        les racines recentes des deux."""
        return self._slot(key, kind).get(key, DEFAULTS.get(key))

    def set_of_kind(self, kind: str, key, value) -> None:
        self._slot(key, kind)[key] = value

    # -- racines recentes ------------------------------------------------
    def push_recent_root(self, root: str) -> None:
        # Windows ne distingue ni la casse ni les separateurs : « x:/Films »
        # et « X:\Films » sont le meme dossier, pas deux lignes des recents.
        same = _same_path_key(root)
        recents = [r for r in (self["recent_roots"] or [])
                   if _same_path_key(r) != same]
        recents.insert(0, root)
        self["recent_roots"] = recents[:8]
        self["root"] = root

    # -- destinations ----------------------------------------------------
    @property
    def destinations(self) -> list[dict]:
        return self["destinations"] or []

    def set_destinations(self, destinations: list[dict]) -> None:
        self["destinations"] = destinations

    def destination_for_key(self, key: str) -> dict | None:
        for dest in self.destinations:
            if dest.get("key") == key:
                return dest
        return None

    def next_free_key(self) -> str:
        used = {d.get("key") for d in self.destinations}
        for key in KEY_ORDER:
            if key not in used and key not in RESERVED_KEYS:
                return key
        return ""


def _same_path_key(text: str) -> str:
    if not text:
        return ""
    return os.path.normcase(os.path.normpath(str(text))).rstrip("\\/")


# -- fichiers de reglages et de favoris -------------------------------------
def _read_text(path: Path):
    """Le texte du fichier, None s'il n'existe pas, ou l'erreur qui l'a empeche.

    Un verrou (antivirus, sauvegarde) se relache souvent en un instant : on
    reessaie avant de conclure que le fichier est illisible. Un contenu qui
    n'est pas du texte rend "" : c'est un fichier abime, pas absent.
    """
    for attempt in range(3):
        try:
            return Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeDecodeError:
            return ""
        except OSError as exc:
            if attempt == 2:
                return exc
            time.sleep(0.1 * (attempt + 1))
    return None


def _parse_object(text: str):
    """Le dictionnaire JSON de ce texte, ou None s'il n'en est pas un."""
    try:
        raw = json.loads(text)
    except ValueError:
        return None
    return raw if isinstance(raw, dict) else None


def _set_aside(path: Path) -> Path | None:
    """Met un fichier abime de cote, sans jamais l'effacer."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    aside = path.with_name(f"{path.stem}.abime-{stamp}{path.suffix}")
    try:
        path.replace(aside)
        return aside
    except OSError:
        return None


def _copy_quietly(source: Path, target: Path) -> None:
    try:
        shutil.copy2(source, target)
    except OSError:
        pass


def _write_atomic(path: Path, text: str) -> str:
    """Ecrit a cote, force sur le disque, puis remplace. Rend l'erreur, ou "".

    Sans le vidage force, une coupure de courant juste apres le remplacement
    pouvait laisser un fichier vide a la place du bon.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        return ""
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        return str(exc)
