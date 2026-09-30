"""Labo IA (essai) : retrouver une scène d'après sa description, sur la machine.

Un modèle de la famille CLIP place images et phrases dans un même espace : une
image et la phrase qui la décrit y tombent côte à côte. On encode donc une
fois pour toutes quelques images de chaque vidéo, puis chaque recherche
n'encode que la phrase, et la compare à toutes les images d'un seul produit
matriciel.

Tout reste ici : le modèle est téléchargé une fois (Hugging Face), puis tout
tourne sur le processeur ou la carte graphique de la machine. Aucune image,
aucune phrase ne part ailleurs.

Rien de ce module n'est nécessaire à Prisme : torch, open_clip et numpy ne
sont pas dans requirements.txt, et ne sont importés qu'au moment où le labo
s'en sert. Importer ce module-ci ne les charge pas — c'est ce qui garde le
lancement de Prisme aussi rapide qu'avant, et intact sans eux.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import queue
import sqlite3
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from . import config as _config
from . import media
from .config import is_photo

# Chez soi, jamais sur le partage : un cache de vignettes pose sur un NAS peut
# etre lu par deux machines a la fois, et une base SQLite ecrite par le reseau
# se fragilise (voir index.py). Et l'on ne charge pas 1,5 Go de modele a
# travers le reseau a chaque ouverture. Dans le cas ordinaire, c'est
# APP_DIR/ia.
IA_DIR = _config.PRIVATE_DIR / "ia"
MODELS_DIR = IA_DIR / "modeles"
STORE_PATH = IA_DIR / "labo.db"
# Les moteurs deja telecharges une fois : on les charge ensuite hors ligne,
# sans meme demander au serveur s'il existe une version plus recente.
READY_PATH = IA_DIR / "modeles-prets.json"


# ---------------------------------------------------------------------------
# Les moteurs proposés
# ---------------------------------------------------------------------------

# Chaque formulation est encodee, puis on en fait la moyenne : c'est
# l'« ensemble de gabarits » des articles CLIP, qui gagne quelques points de
# precision sur une phrase nue. Le « {} » seul y figure aussi : une
# description deja redigee (« fille rousse sous la douche ») n'a pas besoin
# d'emballage, et le gabarit ne doit pas l'ecraser.
FR_TEMPLATES = ("une photo de {}", "une image de {}",
                "une scène de film : {}", "{}")
EN_TEMPLATES = ("a photo of {}", "a picture of {}",
                "a still from a video of {}", "{}")
# Les memes gabarits, vides. Leur moyenne sert de reference neutre : voir
# `query_vector`.
FR_NEUTRAL = ("une photo", "une image", "une scène de film", "une image quelconque")
EN_NEUTRAL = ("a photo", "a picture", "a still from a video", "an image")


@dataclass(frozen=True)
class Preset:
    """Un moteur proposé dans le labo."""

    key: str
    label: str
    model: str          # nom open_clip
    pretrained: str     # poids open_clip ("" : poids aleatoires, pour les tests)
    lang: str           # langue des descriptions : "fr" ou "en"
    download_mb: int    # taille approximative du telechargement
    note: str

    @property
    def size_text(self) -> str:
        return f"{self.download_mb / 1000:.1f} Go".replace(".", ",")

    @property
    def name(self) -> str:
        """Ce qui distingue ses vecteurs dans l'index : deux moteurs ne se
        comparent pas, leurs espaces n'ont rien de commun."""
        return f"{self.model}/{self.pretrained or 'aleatoire'}"


PRESETS = (
    Preset("multi-b32", "Multilingue (français) — rapide",
           "xlm-roberta-base-ViT-B-32", "laion5b_s13b_b90k", "fr", 1500,
           "Descriptions en français, ou dans toute autre langue. Côté image, "
           "un ViT-B/32 : le plus rapide à analyser."),
    Preset("en-l14", "Précis (anglais)",
           "ViT-L-14", "laion2b_s32b_b82k", "en", 1700,
           "Descriptions en anglais : « redhead woman in the shower ». Voit "
           "plus finement, mais l'analyse est environ quinze fois plus lente "
           "sur le processeur."),
)


def preset_by_key(key: str) -> Preset:
    return next((p for p in PRESETS if p.key == key), PRESETS[0])


# ---------------------------------------------------------------------------
# Dépendances facultatives
# ---------------------------------------------------------------------------

# (module a importer, paquet pip). transformers porte le texte du modele
# multilingue (xlm-roberta) ; pillow vient avec torchvision, donc avec
# open_clip, mais on le verifie quand meme.
REQUIRED = (("numpy", "numpy"), ("torch", "torch"),
            ("open_clip", "open_clip_torch"), ("transformers", "transformers"),
            ("PIL", "pillow"))
PIP_PACKAGES = ["torch", "open_clip_torch", "transformers", "numpy"]
# Les roues CUDA de torch ne sont pas sur PyPI (celles de Windows y sont
# pour le processeur seul). CUDA 12.8 couvre les cartes NVIDIA des series
# 20 a 50, avec un pilote de 2025 ou plus recent.
CUDA_INDEX = "https://download.pytorch.org/whl/cu128"


def _absent(module: str) -> bool:
    """Vrai si ce module manque. Ne l'importe pas : find_spec ne fait que
    le chercher — importer torch coute plusieurs secondes."""
    if module in sys.modules:
        return sys.modules[module] is None
    try:
        return importlib.util.find_spec(module) is None
    except (ImportError, ValueError):
        return True


def missing_packages() -> list:
    """Les paquets pip qui manquent au labo (vide : tout est là)."""
    # Un paquet installe pendant la seance n'est vu qu'apres ceci.
    importlib.invalidate_caches()
    return [package for module, package in REQUIRED if _absent(module)]


def can_install() -> str:
    """Pourquoi l'on ne peut pas installer depuis Prisme ("" : on peut)."""
    if getattr(sys, "frozen", False):
        return ("Cette version de Prisme est un programme autonome : le labo "
                "ne fonctionne que lancé depuis les sources (python main.py).")
    if _absent("pip"):
        return "pip est introuvable pour ce Python."
    return ""


def install_commands(cuda: bool = False) -> list:
    """Les commandes pip à lancer, dans l'ordre, pour ce Python-ci.

    Pour la carte graphique, torch vient d'abord de l'index de PyTorch : les
    autres paquets le trouvent alors deja la et ne le remplacent pas par la
    version processeur de PyPI.
    """
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if cuda:
        return [pip + ["torch", "torchvision", "--index-url", CUDA_INDEX],
                pip + ["open_clip_torch", "transformers", "numpy"]]
    return [pip + PIP_PACKAGES]


def _np():
    import numpy
    return numpy


# ---------------------------------------------------------------------------
# Les moteurs : ce que le labo attend d'eux
# ---------------------------------------------------------------------------

class Encoder:
    """Un moteur : des images et des phrases vers des vecteurs normés.

    Le labo ne connait que cette interface : les tests y branchent un moteur
    factice, sans poids a telecharger, et un autre modele s'ajouterait de la
    meme facon.
    """

    name = ""            # cle des vecteurs dans l'index
    label = ""
    device = ""          # en toutes lettres, pour l'affichage
    dim = 0
    lang = "fr"
    batch = 16           # images encodees ensemble

    def __init__(self):
        self._texts: dict = {}
        self._text_lock = threading.Lock()

    # -- a fournir --------------------------------------------------------
    def encode_images(self, paths: list) -> tuple:
        """Rend (vecteurs float32 (n, dim) normés, [lisible ?] par image)."""
        raise NotImplementedError

    def encode_texts(self, texts: list):
        """Rend les vecteurs float32 (n, dim) normés de ces phrases."""
        raise NotImplementedError

    # -- commun -----------------------------------------------------------
    @property
    def templates(self) -> tuple:
        return FR_TEMPLATES if self.lang == "fr" else EN_TEMPLATES

    @property
    def neutral_prompts(self) -> tuple:
        return FR_NEUTRAL if self.lang == "fr" else EN_NEUTRAL

    def _cached_texts(self, texts: list):
        """Les phrases deja encodees ne le sont plus : retaper une recherche,
        ou recompter les tags apres avoir bouge le seuil, est immediat."""
        np = _np()
        with self._text_lock:
            todo = [t for t in dict.fromkeys(texts) if t not in self._texts]
            if todo:
                for text, vector in zip(todo, self.encode_texts(todo)):
                    self._texts[text] = np.asarray(vector, dtype=np.float32)
            return np.stack([self._texts[t] for t in texts])

    def describe(self, queries: list) -> list:
        """Pour chaque description : la moyenne normée de ses gabarits."""
        np = _np()
        texts = [template.format(q) for q in queries for template in self.templates]
        vectors = self._cached_texts(texts)
        per = len(self.templates)
        out = []
        for index in range(len(queries)):
            mean = vectors[index * per:(index + 1) * per].mean(axis=0)
            out.append(mean / max(float(np.linalg.norm(mean)), 1e-8))
        return out

    def neutral(self):
        """La moyenne des gabarits vides (non renormee : voir query_vector)."""
        return self._cached_texts(list(self.neutral_prompts)).mean(axis=0)


class ClipEncoder(Encoder):
    """Un modèle open_clip, chargé une fois, sur la carte graphique s'il y en a une."""

    def __init__(self, preset: Preset, model, preprocess, tokenizer, device: str):
        super().__init__()
        import torch
        self.preset = preset
        self.name = preset.name
        self.label = preset.label
        self.lang = preset.lang
        self.model = model
        self.preprocess = preprocess
        self.tokenizer = tokenizer
        self.torch_device = device
        if device == "cuda":
            self.device = f"carte graphique ({torch.cuda.get_device_name(0)})"
            self.batch = 64
        else:
            self.device = f"processeur ({torch.get_num_threads()} fils)"
            # Un lot doit tenir en deux secondes : a la fermeture de Prisme,
            # les fils de fond ont trois secondes pour s'arreter, et un lot
            # commence ne s'interrompt pas. ViT-L/14 : 2 images/s sur quatre
            # coeurs.
            self.batch = 4 if "L-14" in preset.model else 16
        with torch.inference_mode():
            probe = self.tokenizer(["x"]).to(device)
            self.dim = int(self.model.encode_text(probe).shape[-1])

    @classmethod
    def load(cls, preset: Preset, say=lambda _text: None,
             device: str | None = None) -> "ClipEncoder":
        """Importe torch et open_clip, télécharge le modèle s'il le faut, le charge.

        Plusieurs secondes au mieux, plusieurs minutes au premier
        telechargement : jamais dans le fil de l'interface.
        """
        prepare_environment()
        say("Chargement de torch et open_clip…")
        import torch
        import open_clip
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cpu":
            # Un coeur reste a l'interface : torch prend sinon tous les coeurs,
            # et Prisme repond mal pendant l'analyse.
            torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
        ready = _ready_models()
        cache = str(MODELS_DIR / "hub")

        def build():
            if not preset.pretrained:
                model, _train, preprocess = open_clip.create_model_and_transforms(
                    preset.model, pretrained=None, device=device)
            else:
                model, _train, preprocess = open_clip.create_model_and_transforms(
                    preset.model, pretrained=preset.pretrained, device=device,
                    precision="fp16" if device == "cuda" else "fp32",
                    cache_dir=cache)
            tokenizer = open_clip.get_tokenizer(preset.model, cache_dir=cache)
            return model, preprocess, tokenizer

        if preset.pretrained and preset.name in ready:
            say("Chargement du modèle (hors ligne)…")
            _set_offline(True)
            try:
                model, preprocess, tokenizer = build()
            except Exception:                              # noqa: BLE001
                # Le cache a ete vide ou abime : on retelecharge.
                _set_offline(False)
                say(f"Téléchargement du modèle (≈ {preset.size_text})…")
                model, preprocess, tokenizer = build()
        else:
            _set_offline(False)
            if preset.pretrained:
                say(f"Téléchargement du modèle (≈ {preset.size_text}, "
                    "une seule fois)…")
            model, preprocess, tokenizer = build()
        _set_offline(False)
        model.eval()
        if preset.pretrained:
            _mark_ready(preset.name)
        return cls(preset, model, preprocess, tokenizer, device)

    def encode_images(self, paths: list) -> tuple:
        import torch
        from PIL import Image
        np = _np()
        tensors, ok = [], []
        blank = None
        for path in paths:
            try:
                with Image.open(path) as image:
                    tensors.append(self.preprocess(image.convert("RGB")))
                ok.append(True)
            except Exception:                              # noqa: BLE001
                # Une image illisible garde sa place dans le lot, pour que
                # chaque vecteur reste en face de son image.
                if blank is None:
                    blank = self.preprocess(Image.new("RGB", (224, 224)))
                tensors.append(blank)
                ok.append(False)
        if not tensors:
            return np.zeros((0, self.dim), dtype=np.float32), []
        batch = torch.stack(tensors).to(self.torch_device)
        if self.torch_device == "cuda":
            batch = batch.half()
        with torch.inference_mode():
            features = self.model.encode_image(batch).float()
            features = torch.nn.functional.normalize(features, dim=-1)
        return features.cpu().numpy().astype(np.float32), ok

    def encode_texts(self, texts: list):
        import torch
        np = _np()
        tokens = self.tokenizer(list(texts)).to(self.torch_device)
        with torch.inference_mode():
            features = self.model.encode_text(tokens).float()
            features = torch.nn.functional.normalize(features, dim=-1)
        return features.cpu().numpy().astype(np.float32)


def prepare_environment() -> None:
    """Range le téléchargement du modèle sous IA_DIR, et coupe les bavardages.

    A poser avant le premier import de huggingface_hub, qui lit ces variables
    une fois pour toutes. Les barres de progression sont coupees : lance par
    pythonw, Prisme n'a pas de sortie d'erreur, et tqdm y echouerait.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(MODELS_DIR)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _set_offline(on: bool) -> None:
    """Hors ligne : charger depuis le cache sans rien demander au serveur."""
    os.environ["HF_HUB_OFFLINE"] = "1" if on else "0"
    try:
        from huggingface_hub import constants
        constants.HF_HUB_OFFLINE = on
    except Exception:                                      # noqa: BLE001
        pass


def _ready_models() -> set:
    try:
        return set(json.loads(READY_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return set()


def _mark_ready(name: str) -> None:
    ready = _ready_models()
    if name in ready:
        return
    ready.add(name)
    try:
        READY_PATH.parent.mkdir(parents=True, exist_ok=True)
        READY_PATH.write_text(json.dumps(sorted(ready)), encoding="utf-8")
    except OSError:
        pass


def is_downloaded(preset: Preset) -> bool:
    return preset.name in _ready_models()


def folder_size(folder: Path) -> int:
    """Octets sous ce dossier : l'avancement d'un téléchargement qu'on ne
    voit pas autrement (huggingface_hub n'en dit rien sans console)."""
    total = 0
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------------------
# Classement
# ---------------------------------------------------------------------------

def path_key(path) -> str:
    """Un chemin pour comparer : casse de Windows, et une seule sorte de
    barre (les chemins de l'index melangent parfois les deux)."""
    return os.path.normcase(str(path)).replace("/", "\\")


def query_vector(described, neutral=None, calibrate: bool = True):
    """Le vecteur de recherche d'une description, calibré ou non.

    Calibrer, c'est retirer a chaque image ce qu'elle a de commun avec une
    phrase qui ne dit rien (« une photo », « une image ») : le score devient
    « combien cette image ressemble plus a la description qu'a une image
    quelconque ».

    Pourquoi : dans l'espace de CLIP, certaines images (sombres, floues,
    plans larges tres « photo ») ressemblent un peu a toutes les phrases, et
    remontent en tete de recherches qui n'ont rien a voir. Retirer la
    reference neutre les remet a leur place. Et comme les gabarits vides sont
    ceux de la description, leur tournure s'annule aussi : il reste ce que la
    description ajoute.

    Le calcul ne coute rien : moyenne_k(image . neutre_k) = image . moyenne_k(
    neutre_k), donc soustraire ce score revient a chercher avec
    (description - moyenne des neutres). Un seul produit matriciel, comme
    sans calibrage.
    """
    np = _np()
    q = np.asarray(described, dtype=np.float32)
    if calibrate and neutral is not None:
        q = q - np.asarray(neutral, dtype=np.float32)
    return q.astype(np.float32)


@dataclass
class Hit:
    """Une vidéo trouvée : son meilleur instant, et son image."""

    path: str
    score: float
    ts: float
    thumb: str
    video: int          # rang dans la matrice
    frame: int          # rang de l'image dans la matrice


class Matrix:
    """Tous les vecteurs d'un moteur, en mémoire, prêts à comparer.

    Les images d'une meme video sont contigues : le score d'une video (le
    maximum de ses images) se calcule alors d'un seul `maximum.reduceat`,
    sans boucle Python. 100 000 images : quelques millisecondes.

    En float32 : numpy n'a pas de produit matriciel rapide en float16 (trente
    fois plus lent, mesure). Au-dela de MAX_FLOAT32 images, on garde le
    float16 du disque et l'on calcule par tranches : deux fois moins de
    memoire, dix fois plus lent.
    """

    MAX_FLOAT32 = 600_000
    CHUNK = 32768

    def __init__(self, name: str, paths: list, counts, ts, emb, thumbs: list,
                 durations=None):
        np = _np()
        self.name = name
        self.paths = paths
        self.counts = np.asarray(counts, dtype=np.int64)
        self.starts = np.zeros(len(paths), dtype=np.int64)
        if len(paths) > 1:
            self.starts[1:] = np.cumsum(self.counts)[:-1]
        self.ts = np.asarray(ts, dtype=np.float32)
        emb = np.asarray(emb)
        if len(emb) <= self.MAX_FLOAT32:
            emb = emb.astype(np.float32)
        self.emb = emb
        self.dim = int(emb.shape[1]) if emb.ndim == 2 and len(emb) else 0
        self._thumbs = thumbs       # une liste JSON par video, lue a la demande
        self.durations = durations if durations is not None else [0.0] * len(paths)
        self._masks: dict = {}

    @property
    def frames(self) -> int:
        return int(len(self.emb))

    def __len__(self) -> int:
        return len(self.paths)

    def thumb(self, video: int, frame: int) -> str:
        found = self._thumbs[video]
        if isinstance(found, str):
            try:
                found = json.loads(found)
            except ValueError:
                found = []
            self._thumbs[video] = found
        local = frame - int(self.starts[video])
        return found[local] if 0 <= local < len(found) else ""

    def mask_under(self, root) -> object:
        """Les vidéos sous ce dossier, hors dossiers masqués (None : toutes)."""
        np = _np()
        from . import scan
        veil = (scan.SHOW_VEILED, tuple(sorted(scan.VEILED)))
        key = (path_key(root) if root else "", veil)
        found = self._masks.get(key)
        if found is not None:
            return found
        head = key[0].rstrip("\\")
        head = head + "\\" if head else ""
        mask = np.fromiter(
            ((not head or path_key(path).startswith(head))
             and not scan.under_veiled(path) for path in self.paths),
            dtype=bool, count=len(self.paths))
        self._masks[key] = mask
        return mask

    def frame_scores(self, q):
        """Le score de chaque image (produit scalaire avec q)."""
        np = _np()
        q = np.asarray(q, dtype=np.float32)
        if self.emb.dtype == np.float32:
            return self.emb @ q
        out = np.empty(len(self.emb), dtype=np.float32)
        for start in range(0, len(self.emb), self.CHUNK):
            out[start:start + self.CHUNK] = (
                self.emb[start:start + self.CHUNK].astype(np.float32) @ q)
        return out

    def video_scores(self, frame_scores):
        """Le score d'une vidéo : celui de sa meilleure image."""
        np = _np()
        if not len(self.paths):
            return np.zeros(0, dtype=np.float32)
        return np.maximum.reduceat(frame_scores, self.starts)

    def best_frame(self, video: int, frame_scores) -> int:
        start = int(self.starts[video])
        count = int(self.counts[video])
        return start + int(frame_scores[start:start + count].argmax())

    def rank(self, q, limit: int = 60, mask=None, exclude=()) -> tuple:
        """Les `limit` meilleures vidéos pour ce vecteur ; rend (hits, scores)."""
        fs = self.frame_scores(q)
        vs = self.video_scores(fs)
        return self.hits_from(vs, fs, limit, mask, exclude), vs

    def hits_from(self, vs, fs, limit: int, mask=None, exclude=(),
                  only=None) -> list:
        np = _np()
        scores = vs.astype(np.float32, copy=True)
        if mask is not None:
            scores[~mask] = -np.inf
        for video in exclude:
            scores[video] = -np.inf
        # A score egal, l'ordre des chemins : le meme classement d'une
        # recherche a l'autre.
        if only is not None:
            order = np.asarray(only, dtype=np.int64)
        else:
            limit = min(limit, len(scores))
            if limit <= 0:
                return []
            order = np.argpartition(-scores, limit - 1)[:limit]
        order = order[np.lexsort((order, -scores[order]))]
        hits = []
        for video in order[:limit]:
            video = int(video)
            if not np.isfinite(scores[video]):
                break
            frame = self.best_frame(video, fs)
            hits.append(Hit(self.paths[video], float(scores[video]),
                            float(self.ts[frame]), self.thumb(video, frame),
                            video, frame))
        return hits

    def video_vector(self, video: int):
        """La moyenne normée des images d'une vidéo (« plus comme cette vidéo »)."""
        np = _np()
        start = int(self.starts[video])
        mean = self.emb[start:start + int(self.counts[video])].astype(
            np.float32).mean(axis=0)
        return mean / max(float(np.linalg.norm(mean)), 1e-8)

    def frame_vector(self, frame: int):
        np = _np()
        return self.emb[frame].astype(np.float32)


def tag_matches(scores, z_min: float = 1.5, top_share: float = 0.10) -> list:
    """Les vidéos qu'un tag retient : celles qui se détachent du lot.

    Un seuil fixe sur le score brut ne tient pas : chaque description a son
    propre niveau (« lingerie noire » donne 0,27 a presque tout, « plage »
    0,21), si bien qu'un seuil juste pour l'une retient tout ou rien pour
    l'autre. On mesure donc de combien une video se detache des autres pour
    CETTE description, en ecarts-types (score z sur la portee regardee) :
    un seul curseur vaut alors pour toutes les descriptions.

    Le plafond (une part de la collection) garde d'une description trop vague,
    qui « trouverait » la moitie des videos : au-dela, ce n'est plus un tag.
    Il faut les deux : score z >= z_min ET parmi les `top_share` meilleures.

    Rend les rangs retenus (dans `scores`), du meilleur au moins bon.
    """
    np = _np()
    s = np.asarray(scores, dtype=np.float64)
    n = len(s)
    if n < 2:
        return []
    std = float(s.std())
    if std < 1e-9:
        return []
    z = (s - float(s.mean())) / std
    cap = max(1, int(math.ceil(top_share * n)))
    order = np.argsort(-s, kind="stable")[:cap]
    return [int(i) for i in order if z[i] >= z_min]


# ---------------------------------------------------------------------------
# L'index du labo
# ---------------------------------------------------------------------------

class Store:
    """Les vecteurs de chaque vidéo, par moteur, et les réglages du labo.

    Une ligne par video (et par moteur), ses images a la suite dans un seul
    BLOB : relire cent mille lignes d'images une a une coutait plus cher que
    la recherche elle-meme. Le float16 divise la place par deux, sans
    difference visible au classement (les vecteurs sont normes, l'ecart reste
    sous le millieme).

    L'empreinte (taille, date) de la video est gardee avec : une video
    remplacee sous le meme nom est reanalysee, les autres sont sautees.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS videos(
        model    TEXT NOT NULL,
        path     TEXT NOT NULL,
        stamp    TEXT NOT NULL DEFAULT '',
        duration REAL NOT NULL DEFAULT 0,
        n        INTEGER NOT NULL DEFAULT 0,
        ts       BLOB,
        emb      BLOB,
        thumbs   TEXT NOT NULL DEFAULT '[]',
        added    REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (model, path)
    );
    CREATE TABLE IF NOT EXISTS meta(
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT ''
    );
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path or STORE_PATH)
        self.lock = threading.Lock()
        self.db = None
        # Change a chaque ecriture : la matrice en memoire sait qu'elle est
        # perimee sans rien relire.
        self.version = 0
        self._matrices: dict = {}

    def open(self) -> "Store":
        if self.db is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
        try:
            db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        db.executescript(self.SCHEMA)
        db.commit()
        self.db = db
        return self

    def close(self) -> None:
        with self.lock:
            if self.db is not None:
                try:
                    self.db.commit()
                    self.db.close()
                except sqlite3.Error:
                    pass
                self.db = None

    def _rows(self, sql: str, args: tuple = ()) -> list:
        self.open()
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    # -- videos -----------------------------------------------------------
    def known(self, model: str) -> dict:
        """chemin -> empreinte, pour sauter ce qui n'a pas changé."""
        return dict(self._rows("SELECT path, stamp FROM videos WHERE model = ?",
                               (model,)))

    def put(self, model: str, path: str, stamp: str, duration: float,
            ts, emb, thumbs: list) -> None:
        """Retient les vecteurs d'une vidéo (n = 0 : illisible, on ne réessaie
        qu'une fois la vidéo changée)."""
        np = _np()
        n = 0 if emb is None else int(len(emb))
        ts_blob = np.asarray(ts if n else [], dtype=np.float32).tobytes()
        emb_blob = (np.asarray(emb, dtype=np.float16).tobytes() if n else b"")
        self.open()
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO videos(model, path, stamp, duration, n,"
                " ts, emb, thumbs, added) VALUES(?,?,?,?,?,?,?,?,?)",
                (model, str(path), stamp, float(duration or 0.0), n, ts_blob,
                 emb_blob, json.dumps(list(thumbs), ensure_ascii=False),
                 time.time()))
            self.db.commit()
            self.version += 1

    def forget(self, model: str, paths) -> int:
        paths = list(paths)
        if not paths:
            return 0
        self.open()
        with self.lock:
            for start in range(0, len(paths), 400):
                chunk = paths[start:start + 400]
                self.db.execute(
                    "DELETE FROM videos WHERE model = ? AND path IN ("
                    + ",".join("?" * len(chunk)) + ")", (model, *chunk))
            self.db.commit()
            self.version += 1
        return len(paths)

    def counts(self, model: str) -> tuple:
        """(vidéos lues, images, vidéos illisibles) pour ce moteur."""
        rows = self._rows(
            "SELECT COUNT(*), COALESCE(SUM(n), 0), COALESCE(SUM(n = 0), 0)"
            " FROM videos WHERE model = ?", (model,))
        videos, frames, failed = rows[0] if rows else (0, 0, 0)
        return int(videos) - int(failed), int(frames), int(failed)

    def size_on_disk(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(str(self.path) + suffix)
            except OSError:
                pass
        return total

    def matrix(self, model: str, dim_hint: int = 0) -> Matrix | None:
        """Tous les vecteurs de ce moteur, relus seulement s'ils ont changé."""
        np = _np()
        cached = self._matrices.get(model)
        if cached is not None and cached[0] == self.version:
            return cached[1]
        version = self.version
        rows = self._rows(
            "SELECT path, n, ts, emb, thumbs, duration FROM videos"
            " WHERE model = ? AND n > 0 ORDER BY path", (model,))
        paths, counts, thumbs, durations = [], [], [], []
        ts_parts, emb_parts = [], []
        for path, n, ts, emb, thumb_list, duration in rows:
            paths.append(path)
            counts.append(n)
            ts_parts.append(ts)
            emb_parts.append(emb)
            thumbs.append(thumb_list)
            durations.append(duration)
        frames = int(sum(counts))
        if frames:
            flat = np.frombuffer(b"".join(emb_parts), dtype=np.float16)
            emb = flat.reshape(frames, -1)
            ts = np.frombuffer(b"".join(ts_parts), dtype=np.float32)
        else:
            emb = np.zeros((0, dim_hint or 1), dtype=np.float32)
            ts = np.zeros(0, dtype=np.float32)
        found = Matrix(model, paths, counts, ts, emb, thumbs, durations)
        self._matrices = {model: (version, found)}
        return found

    # -- reglages du labo ---------------------------------------------------
    def meta(self, key: str, default: str = "") -> str:
        rows = self._rows("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0][0] if rows else default

    def set_meta(self, key: str, value: str) -> None:
        self.open()
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
                            (key, str(value)))
            self.db.commit()

    def tags(self) -> list:
        try:
            found = json.loads(self.meta("tags", "[]"))
        except ValueError:
            return []
        return [str(t) for t in found if str(t).strip()]

    def set_tags(self, tags: list) -> None:
        clean = [t.strip() for t in tags if t and t.strip()]
        self.set_meta("tags", json.dumps(list(dict.fromkeys(clean)),
                                         ensure_ascii=False))


# ---------------------------------------------------------------------------
# Les images d'une vidéo
# ---------------------------------------------------------------------------

# Une image deja en cache sert a la place de l'instant vise si elle en est a
# moins de cette part de la duree : a 4 %, les cinq reperes de la pellicule
# (20 %, 35 %…) remplacent cinq des neuf instants par defaut.
NEAR_SHARE = 0.04
# Neuf, par defaut : ce sont les instants de l'apercu au survol (PeekOverlay),
# si bien que les images que le labo fabrique servent aussi a Prisme.
DEFAULT_FRAMES = 9


def frame_times(video, count: int) -> tuple:
    """(durée, instants visés) : `count` instants échelonnés dans la vidéo.

    La formule est celle des apercus de Prisme (`build_preview_plan`) : les
    plans reperes s'il y en a, sinon des fractions regulieres. Le sondage de
    la duree y est fait au besoin (un ffprobe, garde par l'index).
    """
    plan = media.build_preview_plan([Path(video)], count, one_per_video=False)
    if not plan:
        return 0.0, []
    duration = float(plan[0][2] or 0.0)
    times = sorted({round(float(entry[1]), 2) for entry in plan})
    return duration, times


def gather_frames(video, count: int, width: int, stop=lambda: False) -> tuple:
    """Les images d'une vidéo pour l'analyse : (durée, [(instant, fichier)]).

    D'abord ce que Prisme a deja fabrique : chaque instant vise prend
    l'image en cache la plus proche (a NEAR_SHARE de la duree pres), sinon
    ffmpeg l'extrait dans le meme cache — ou Prisme la retrouvera a son tour.
    Une photo n'a qu'une image.
    """
    path = Path(video)
    if is_photo(path):
        made = media.extract_thumb(path, 0.0, width)
        return 0.0, ([(0.0, str(made))] if made else [])
    duration, targets = frame_times(path, count)
    if not targets:
        return duration, []
    cached = []
    try:
        moments = media._moments_for(str(path), media.INDEX.probe(path))
    except Exception:                                      # noqa: BLE001
        moments = set()
    for ts in sorted(moments):
        found = media.cached_thumb(path, ts, width)
        if found is not None:
            cached.append((ts, str(found)))
    near = max(0.5, duration * NEAR_SHARE)
    frames = []
    for ts in targets:
        if stop():
            return duration, []
        best = None
        for index, (have, _file) in enumerate(cached):
            gap = abs(have - ts)
            if gap <= near and (best is None or gap < best[0]):
                best = (gap, index)
        if best is not None:
            frames.append(cached.pop(best[1]))
            continue
        found = media.cached_thumb(path, ts, width)
        if found is None:
            # Ce qu'on regarde passe devant : le labo cede la place aux
            # vignettes que Prisme affiche.
            media.wait_foreground(stop)
            if stop():
                return duration, []
            found = media.extract_thumb(path, ts, width)
        if found is not None:
            frames.append((ts, str(found)))
    frames.sort()
    # Une video tres courte ramene plusieurs instants sur la meme image.
    unique, seen = [], set()
    for ts, file in frames:
        if file not in seen:
            seen.add(file)
            unique.append((ts, file))
    return duration, unique


# ---------------------------------------------------------------------------
# L'analyse, en tâche de fond
# ---------------------------------------------------------------------------

class Indexer(QThread):
    """Parcourt une portée, prépare les images, les encode par lots, les range.

    Trois fils preparent les images (ffmpeg attend le disque plus qu'il ne
    calcule) pendant que celui-ci encode : le processeur n'attend pas le
    disque, ni l'inverse. Les videos deja analysees, inchangees, sont sautees.

    Arreter garde tout ce qui est deja range : au plus un lot est perdu.
    """

    progress = Signal(object)       # dict : voir _report
    finished_run = Signal(object)   # dict : bilan
    said = Signal(str)

    WORKERS = 3

    def __init__(self, encoder: Encoder, store: Store, root, count: int,
                 width: int, skip_hidden: bool = True, parent=None):
        super().__init__(parent)
        self.encoder = encoder
        self.store = store
        self.root = Path(root)
        self.count = max(1, int(count))
        self.width = int(width)
        self.skip_hidden = skip_hidden
        self._stop = False
        # Une erreur d'encodage (memoire de la carte, par exemple) arrete
        # aussi les fils de preparation : sans cela, ils continuaient de
        # lancer ffmpeg pour un lot que personne n'encoderait.
        self._abort = False
        self._owner = media._Owner()
        self.stats = {"total": 0, "todo": 0, "done": 0, "skipped": 0,
                      "failed": 0, "frames": 0, "removed": 0, "stopped": False,
                      "encode_s": 0.0, "images_encoded": 0, "seconds": 0.0,
                      "error": ""}

    def stop(self) -> None:
        """Arrête au plus vite : ffmpeg en cours compris."""
        self._stop = True
        self._owner.kill()

    def stopped(self) -> bool:
        return self._stop or self._abort or media.CLOSING

    def run(self) -> None:
        started = time.monotonic()
        try:
            self._run()
        except Exception as exc:                           # noqa: BLE001
            self._abort = True
            self._owner.kill()
            self.stats["error"] = f"{type(exc).__name__} : {exc}"
        self.stats["stopped"] = self._stop
        self.stats["seconds"] = time.monotonic() - started
        if self.stats["images_encoded"]:
            self.store.set_meta(
                f"ms_image|{self.encoder.name}",
                f"{1000 * self.stats['encode_s'] / self.stats['images_encoded']:.1f}")
        self.finished_run.emit(dict(self.stats))

    def _run(self) -> None:
        from . import scan, stamps
        self.said.emit(f"Inventaire de {self.root}…")
        found: dict = {}
        videos = scan.list_all_videos(self.root, self.skip_hidden,
                                      limit=10 ** 7, stamps=found, strict=True)
        if self.stopped():
            return
        stamp_of = {}
        for path in videos:
            key = str(path)
            size_mtime = found.get(key)
            if size_mtime is None:
                continue
            size, mtime = size_mtime
            # L'enumeration a lu taille et date : les vignettes les nomment,
            # et `stamps` evite de les redemander au disque une a une.
            stamps.remember(key, size, mtime)
            stamp_of[key] = f"{int(mtime)}|{size}"
        known = self.store.known(self.encoder.name)
        # Ce que l'index garde sous cette portee et qui n'y est plus : range
        # ou supprime depuis. L'inventaire a abouti (strict) : on peut
        # l'oublier sans risque.
        head = path_key(self.root).rstrip("\\") + "\\"
        gone = [path for path in known
                if path_key(path).startswith(head) and path not in stamp_of]
        self.stats["removed"] = self.store.forget(self.encoder.name, gone)
        todo = [key for key in stamp_of if known.get(key) != stamp_of[key]]
        self.stats["total"] = len(stamp_of)
        self.stats["todo"] = len(todo)
        self.stats["skipped"] = len(stamp_of) - len(todo)
        self._report(started=time.monotonic())
        if not todo:
            return
        self._pipeline(todo, stamp_of)

    def _pipeline(self, todo: list, stamp_of: dict) -> None:
        tasks = deque(todo)
        results: queue.Queue = queue.Queue(maxsize=24)
        lock = threading.Lock()

        def worker() -> None:
            media._LOCAL.owner = self._owner
            while not self.stopped():
                with lock:
                    if not tasks:
                        break
                    video = tasks.popleft()
                try:
                    duration, frames = gather_frames(video, self.count, self.width,
                                                     self.stopped)
                except Exception:                          # noqa: BLE001
                    duration, frames = 0.0, []
                if self.stopped():
                    break
                results.put((video, duration, frames))
            results.put(None)

        hands = [threading.Thread(target=worker, daemon=True)
                 for _ in range(self.WORKERS)]
        for hand in hands:
            hand.start()
        alive = len(hands)
        pending: list = []
        started = time.monotonic()
        while alive and not self.stopped():
            try:
                item = results.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                alive -= 1
                continue
            pending.append(item)
            if sum(len(p[2]) for p in pending) >= self.encoder.batch:
                self._encode(pending, stamp_of)
                pending = []
                self._report(started)
        if pending and not self.stopped():
            self._encode(pending, stamp_of)
            self._report(started)

    def _encode(self, pending: list, stamp_of: dict) -> None:
        """Encode les images de ces vidéos d'un seul lot, puis range chacune."""
        np = _np()
        files = [file for _video, _duration, frames in pending
                 for _ts, file in frames]
        if files:
            # Le temps par image se mesure a l'encodage seul : c'est ce qui
            # distingue les moteurs, ffmpeg est le meme pour tous.
            began = time.perf_counter()
            vectors, ok = self.encoder.encode_images(files)
            self.stats["encode_s"] += time.perf_counter() - began
            self.stats["images_encoded"] += len(files)
        else:
            vectors, ok = np.zeros((0, 1), dtype=np.float32), []
        at = 0
        for video, duration, frames in pending:
            rows = list(range(at, at + len(frames)))
            at += len(frames)
            keep = [r for r in rows if ok[r]]
            if not frames and not media.Tools.ffmpeg and not is_photo(video):
                continue            # sans ffmpeg, rien ne dit que la video est illisible
            ts = [frames[r - rows[0]][0] for r in keep]
            thumbs = [frames[r - rows[0]][1] for r in keep]
            emb = vectors[keep] if keep else None
            self.store.put(self.encoder.name, video, stamp_of.get(video, ""),
                           duration, ts, emb, thumbs)
            self.stats["done"] += 1
            self.stats["frames"] += len(keep)
            if not keep:
                self.stats["failed"] += 1

    def _report(self, started: float) -> None:
        done = self.stats["done"]
        todo = self.stats["todo"]
        elapsed = time.monotonic() - started
        eta = (elapsed / done * (todo - done)) if done else None
        ms = (1000 * self.stats["encode_s"] / self.stats["images_encoded"]
              if self.stats["images_encoded"] else None)
        self.progress.emit({"done": done, "todo": todo,
                            "skipped": self.stats["skipped"],
                            "frames": self.stats["frames"],
                            "failed": self.stats["failed"],
                            "eta": eta, "ms_image": ms})


class TextJob(QObject):
    """Encode des descriptions hors du fil de l'interface.

    Un fil Python ordinaire (daemon) plutot qu'un QThread : un encodage de
    texte ne s'interrompt pas, et Prisme ne doit pas attendre sa fin pour se
    fermer. Le resultat revient par un signal, donc dans le fil de
    l'interface.
    """

    done = Signal(object, object, float)     # [vecteurs], neutre, secondes
    failed = Signal(str)

    def __init__(self, encoder: Encoder, queries: list, parent=None):
        super().__init__(parent)
        self.encoder = encoder
        self.queries = list(queries)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        started = time.monotonic()
        try:
            vectors = self.encoder.describe(self.queries) if self.queries else []
            neutral = self.encoder.neutral()
        except Exception as exc:                           # noqa: BLE001
            self._emit(self.failed, f"{type(exc).__name__} : {exc}")
            return
        self._emit(self.done, vectors, neutral, time.monotonic() - started)

    @staticmethod
    def _emit(signal, *args) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            pass            # le labo a ete ferme entre-temps


class EngineLoader(QObject):
    """Charge un moteur hors du fil de l'interface (import de torch compris)."""

    loaded = Signal(object)
    failed = Signal(str)
    said = Signal(str)

    def __init__(self, preset: Preset, parent=None, factory=None):
        super().__init__(parent)
        self.preset = preset
        self.factory = factory or ClipEncoder.load

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            encoder = self.factory(self.preset, say=self._say)
        except Exception as exc:                           # noqa: BLE001
            TextJob._emit(self.failed, _explain_load_error(exc))
            return
        TextJob._emit(self.loaded, encoder)

    def _say(self, text: str) -> None:
        TextJob._emit(self.said, text)


def _explain_load_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__} : {exc}"
    low = text.lower()
    if any(word in low for word in ("connection", "403", "resolve", "offline",
                                    "download", "hub", "timed out")):
        return ("Le modèle n'a pas pu être téléchargé depuis Hugging Face "
                "(huggingface.co). Vérifiez la connexion, un pare-feu ou un "
                f"proxy, puis réessayez.\n\nDétail : {text[:600]}")
    return text[:800]
