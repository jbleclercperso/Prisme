"""Le moteur du Labo IA (essai) : retrouver une scene par sa description.

Chaque video de la collection est resumee par des images prises selon sa
duree (3 pour un clip court, puis environ une par minute, 16 au plus),
calees sur ses changements de plan quand Prisme les connait, et reprises
d'abord parmi les apercus deja en cache. Un modele CLIP, qui tourne sur ce PC, en tire des
« empreintes de sens » ; une phrase (« une plage au coucher du soleil ») en
tire une aussi, et les images les plus proches sont celles qui la montrent.
Les tags IA sont des phrases de ce genre, retenues : chaque video recoit les
tags dont elle est la plus proche.

- Un moteur multilingue : on ecrit en francais. Une demande se comprend
  idee par idee (`parse_query`) : chacune doit se retrouver dans l'image.
- Les bibliotheques (torch, open_clip…) sont facultatives : Prisme marche
  sans elles, et le labo propose de les installer. Dans le programme vendu
  (portable ou installe), elles vont dans un Python a part, que Prisme se
  procure lui-meme (iapython).
- Les empreintes se gardent sur le disque : on n'indexe qu'une fois.

Sans Qt : la fenetre (labo.py) fournit les images (`frame_of`).
"""
from __future__ import annotations

import array
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import threading
from pathlib import Path

from . import iapython
from .config import PRIVATE_DIR

# Les moteurs, du plus rapide au plus precis. Mesure sur un i7 de portable
# (moitie des coeurs) : la partie image coute 56 ms (B-32), 1,1 s (SigLIP 2),
# 1,7 s (H-14).
# La justesse depend aussi de ce que le modele a vu : MetaCLIP 2, DFN et
# DataComp ont retire les contenus adultes de leur entrainement ; les
# modeles LAION (B-32, H-14) les ont vus. SigLIP 2 (Google) est le plus fort
# sur les tests generaux, sans qu'on sache ce qui a ete filtre : il est la
# pour etre juge sur la collection elle-meme (l'onglet « Comparer »).
# `heavy` : le modele ne garde en memoire que la moitie dont il a besoin
# (images pour indexer, texte pour chercher) -- 8 Go de memoire n'en
# tiennent pas deux entiers a cote de Prisme.
ENGINES = {
    "multilingue": {"model": "xlm-roberta-base-ViT-B-32", "pretrained": "laion5b_s13b_b90k",
                    "label": "Rapide — B-32, français ou anglais",
                    "hub": "laion/CLIP-ViT-B-32-xlm-roberta-base-laion5B-s13B-b90k",
                    "short": "Rapide (B-32)", "download": "1,4 Go", "image_ms": 56,
                    "heavy": False},
    "h14": {"model": "xlm-roberta-large-ViT-H-14", "pretrained": "frozen_laion5b_s13b_b90k",
            "label": "Précis — H-14, français ou anglais",
            "hub": "laion/CLIP-ViT-H-14-frozen-xlm-roberta-large-laion5B-s13B-b90k",
            "short": "Précis (H-14)", "download": "4,8 Go", "image_ms": 1690,
            "heavy": True},
    "siglip2": {"model": "ViT-SO400M-14-SigLIP2", "pretrained": "webli",
                "label": "Essai — SigLIP 2 (Google, 2025), plutôt en anglais",
                "hub": "timm/ViT-SO400M-14-SigLIP2",
                "short": "SigLIP 2", "download": "4,5 Go", "image_ms": 1140,
                "heavy": True},
}
DEFAULT_ENGINE = "multilingue"
# (module a importer, paquet pip)
NEEDED = (("torch", "torch"), ("torchvision", "torchvision"),
          ("open_clip", "open_clip_torch"), ("transformers", "transformers"),
          ("numpy", "numpy"))
# CUDA 12.6 : la derniere famille de PyTorch qui sait encore se servir des
# cartes GTX 10 (Pascal) -- les suivantes (12.8 et plus) les ont abandonnees.
CUDA_INDEX = "https://download.pytorch.org/whl/cu126"
CUDA_TAG = "cu126"
# Pilote NVIDIA minimal pour CUDA 12.x sous Windows.
MIN_DRIVER = 528
# L'echantillonnage : combien d'images par video, selon sa duree. Change-t-il
# (SAMPLING), les videos deja indexees sont refaites -- sans rien perdre en
# attendant.
SAMPLING = 2
SHORT_S = 120.0
MAX_FRAMES = 16
# Une phrase se compare sous plusieurs formulations, moyennees : la methode
# classique pour rendre ces modeles plus justes (« prompt ensembling »).
TEMPLATES = {
    "multilingue": ("{}", "une photo de {}", "une image de {}", "une scène de {}",
                    "une capture d'une vidéo amateur : {}", "a photo of {}"),
    "h14": ("{}", "une photo de {}", "une scène de {}", "a photo of {}",
            "a frame from an amateur video of {}"),
    "siglip2": ("{}", "a photo of {}", "this is a photo of {}",
                "a frame from an amateur video of {}"),
}
LAB_DIR = PRIVATE_DIR / "labo"
# Au-dela de tant de nombres par image, l'index se garde en float16 : la
# moitie de la place (H-14 : 1024 nombres, 0,9 Go pour 400 000 images).
HALF_FROM = 1000
BATCH = 16


# -- les bibliotheques ----------------------------------------------------------------

def missing() -> list:
    """Les paquets pip qui manquent au labo (vide : tout est la). Les caches
    d'import sont vides d'abord : apres une installation, Python croyait
    encore les paquets absents."""
    return [package for module, package in NEEDED if not iapython.has(module)]


def console_python() -> str:
    """Le Python a console pour lancer pip : Prisme tourne souvent sous
    pythonw.exe (sans console), et pip, lance par lui, s'arretait en route --
    l'installation restait a moitie faite."""
    if iapython.external():
        return str(iapython.python())
    here = Path(sys.executable)
    if here.name.lower() == "pythonw.exe":
        beside = here.with_name("python.exe")
        if beside.exists():
            return str(beside)
    return sys.executable


def can_install() -> str:
    """"" si l'on peut installer d'ici, sinon pourquoi pas."""
    if iapython.external():
        # Le programme vendu se procure son Python (iapython.prepare).
        return "" if os.name == "nt" else "Le labo IA n'est prévu que pour Windows."
    if importlib.util.find_spec("pip") is None:
        return "pip est introuvable pour ce Python."
    return ""


def _nvidia_smi() -> str:
    """nvidia-smi, la ou les pilotes le posent (anciens ou recents)."""
    for candidate in (shutil.which("nvidia-smi"),
                      r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
                      r"C:\Windows\System32\nvidia-smi.exe"):
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def nvidia_card() -> tuple:
    """(nom de la carte, version du pilote), ou ("", "") sans carte NVIDIA."""
    tool = _nvidia_smi()
    if not tool:
        return "", ""
    try:
        import subprocess
        out = subprocess.run([tool, "--query-gpu=name,driver_version", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        name, version = [part.strip() for part in out.splitlines()[0].split(",")[:2]]
        return name, version
    except Exception:                                       # noqa: BLE001
        return "", ""


def has_nvidia() -> bool:
    return bool(_nvidia_smi())


def torch_build() -> str:
    """La version de PyTorch installee (« 2.14.1+cpu »), sans l'importer :
    l'importer dans la fenetre coutait deux secondes et des centaines de Mo."""
    return iapython.version("torch")


def gpu_status() -> tuple:
    """(etat, phrase) : « ready », « switch » (PyTorch sans carte, a
    remplacer), « driver » (pilote trop ancien), ou « none »."""
    name, driver = nvidia_card()
    if not name:
        return "none", ""
    try:
        major = int(driver.split(".")[0])
    except ValueError:
        major = 0
    if major and major < MIN_DRIVER:
        return "driver", (f"Carte {name} trouvée, mais son pilote ({driver}) est trop ancien pour "
                          "s'en servir. Le Gestionnaire de périphériques ne propose que celui de "
                          "Windows Update : installez le dernier depuis nvidia.com (série 580).")
    build = torch_build()
    if build and "+cu" not in build:
        return "switch", (f"Carte {name} trouvée, mais la version de PyTorch installée n'utilise "
                          "que le processeur.")
    return "ready", f"Carte {name} prête (pilote {driver})."


def gpu_switch_commands() -> list:
    """Remplace PyTorch « processeur » par la meme version pour carte NVIDIA."""
    build = torch_build()
    if not build or "+cu" in build:
        return []
    base = build.split("+")[0]
    vision = iapython.version("torchvision").split("+")[0]
    wanted = [f"torch=={base}+{CUDA_TAG}"] + ([f"torchvision=={vision}+{CUDA_TAG}"] if vision else [])
    return [[console_python(), "-m", "pip", "install", "--disable-pip-version-check",
             "--force-reinstall", "--no-deps"] + wanted + ["--index-url", CUDA_INDEX]]


def install_commands() -> list:
    """Les commandes pip a lancer, dans l'ordre, pour ce Python-ci (torch
    pour carte NVIDIA si l'ordinateur en a une)."""
    pip = [console_python(), "-m", "pip", "install", "--disable-pip-version-check"]
    wanted = missing()
    if not wanted:
        return []
    # Le programme vendu : d'abord son Python a lui (une fois).
    first = [iapython.PrepareStep()] if iapython.external() and not iapython.prepared() else []
    if has_nvidia() and ("torch" in wanted or "torchvision" in wanted):
        rest = [p for p in wanted if p not in ("torch", "torchvision")]
        return first + [pip + ["torch", "torchvision", "--index-url", CUDA_INDEX]] + (
            [pip + rest] if rest else [])
    return first + [pip + wanted]


# -- les moteurs ------------------------------------------------------------------------

def _normalized(vector) -> list:
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


def _hub_cache() -> Path:
    for key in ("HUGGINGFACE_HUB_CACHE", "HF_HUB_CACHE"):
        if os.environ.get(key):
            return Path(os.environ[key])
    home = os.environ.get("HF_HOME")
    return (Path(home) / "hub") if home else Path.home() / ".cache" / "huggingface" / "hub"


def model_on_disk(name: str) -> bool:
    """Le modele de ce moteur est-il deja sur ce PC (telecharge une fois) ?
    Lu sur le disque, sans rien importer."""
    hub = ENGINES.get(name, {}).get("hub", "")
    if not hub:
        return False
    folder = _hub_cache() / ("models--" + hub.replace("/", "--")) / "snapshots"
    try:
        for weights in folder.glob("*/open_clip_*"):
            if weights.stat().st_size > 100 * 2**20:
                return True
    except OSError:
        pass
    return False


class ClipEngine:
    """Un modele CLIP (open_clip), charge a la premiere demande (hors du fil
    de l'interface : le premier chargement telecharge le modele).

    Un modele lourd (`heavy`) ne garde que la moitie utile : la partie image
    pour indexer, la partie texte pour chercher ; passer de l'une a l'autre
    le recharge (une minute). Les poids se copient un a un depuis le fichier,
    projete en memoire : jamais deux exemplaires du modele a la fois."""

    def __init__(self, name: str = DEFAULT_ENGINE):
        self.name = name if name in ENGINES else DEFAULT_ENGINE
        self._model = self._preprocess = self._tokenizer = None
        self._part = ""
        self._device = "cpu"
        self._lock = threading.Lock()

    @property
    def device(self) -> str:
        return self._device

    def load(self, part: str = "") -> None:
        """Charge le modele : « vision », « text », ou les deux (vide)."""
        spec = ENGINES[self.name]
        wanted = part if spec.get("heavy") and part else "both"
        with self._lock:
            if self._model is not None and self._part in (wanted, "both"):
                return
            self._model = None
            import gc
            gc.collect()
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            if model_on_disk(self.name):
                # Deja telecharge : plus aucun appel a Internet. La
                # bibliotheque allait sinon verifier en ligne, a chaque
                # demarrage, que le modele etait toujours le meme.
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
            import open_clip
            import torch
            # La moitie des coeurs : l'indexation tourne pendant qu'on se
            # sert de Prisme, qui doit rester vif.
            torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))
            # La partie texte d'un gros modele reste sur le processeur : elle
            # ne sert qu'a quelques phrases, et laisse la memoire de la carte
            # (4 Go sur une GTX 1050) a la partie images d'un autre moteur.
            gpu = torch.cuda.is_available() and not (spec.get("heavy") and wanted == "text")
            self._device = "cuda" if gpu else "cpu"
            cfg = open_clip.get_pretrained_cfg(spec["model"], spec["pretrained"])
            path = open_clip.download_pretrained(cfg)
            # Le squelette seul, sans telecharger d'autres poids (le texte
            # d'un modele multilingue en irait chercher deux Go de plus).
            with _no_random_init():
                model, _train, preprocess = open_clip.create_model_and_transforms(
                    spec["model"], pretrained=spec["pretrained"], load_weights=False,
                    pretrained_text=False, pretrained_image=False)
            if wanted == "vision" and hasattr(model, "text"):
                model.text = torch.nn.Identity()
            elif wanted == "text":
                model.visual = torch.nn.Identity()
            gc.collect()
            _copy_weights(model, path)
            model.eval()
            if self._device == "cuda" and not _fits_on_card(model):
                # Pas la place sur la carte (un autre moteur l'occupe, ou elle
                # est trop petite) : Windows deborderait sur la memoire du PC,
                # dix fois plus lent que le processeur. Le processeur, donc.
                self._device = "cpu"
            model.to(self._device)
            self._tokenizer = open_clip.get_tokenizer(spec["model"]) if wanted != "vision" else None
            self._model, self._preprocess, self._part = model, preprocess, wanted

    def embed_images(self, paths: list) -> list:
        import torch
        from PIL import Image
        self.load("vision")
        images = []
        for path in paths:
            with Image.open(path) as picture:
                images.append(self._preprocess(picture.convert("RGB")))
        with torch.no_grad():
            batch = torch.stack(images).to(self._device)
            features = self._model.encode_image(batch)
            features = features / features.norm(dim=-1, keepdim=True)
        return [row.tolist() for row in features.float().cpu()]

    def embed_texts(self, texts: list) -> list:
        import torch
        self.load("text")
        with torch.no_grad():
            tokens = self._tokenizer(texts).to(self._device)
            features = self._model.encode_text(tokens)
            features = features / features.norm(dim=-1, keepdim=True)
        return [row.tolist() for row in features.float().cpu()]


class _no_random_init:
    """Le squelette du modele sans le remplir de nombres au hasard : ses
    poids sont aussitot remplaces par ceux du fichier. Remplir H-14 au
    hasard prenait 52 secondes a chaque ouverture ; sans, une."""

    INIT = ("normal_", "uniform_", "trunc_normal_", "xavier_uniform_", "xavier_normal_",
            "kaiming_uniform_", "kaiming_normal_", "orthogonal_")
    TENSOR = ("normal_", "uniform_")

    def __enter__(self):
        import torch
        self._init = {n: getattr(torch.nn.init, n) for n in self.INIT if hasattr(torch.nn.init, n)}
        self._tensor = {n: getattr(torch.Tensor, n) for n in self.TENSOR}
        for name in self._init:
            setattr(torch.nn.init, name, lambda tensor, *_a, **_k: tensor)
        for name in self._tensor:
            setattr(torch.Tensor, name, lambda self_, *_a, **_k: self_)
        return self

    def __exit__(self, *_exc):
        import torch
        for name, function in self._init.items():
            setattr(torch.nn.init, name, function)
        for name, function in self._tensor.items():
            setattr(torch.Tensor, name, function)
        return False


def _fits_on_card(model) -> bool:
    """Le modele tient-il dans ce qui reste de memoire sur la carte, avec de
    quoi calculer a cote ?"""
    import torch
    try:
        free, _total = torch.cuda.mem_get_info()
    except Exception:                                       # noqa: BLE001
        return False
    need = sum(p.numel() * p.element_size() for p in model.parameters())
    return need * 1.3 + 400 * 2**20 < free


def _copy_weights(model, path) -> None:
    """Les poids du fichier dans le modele, un tenseur a la fois.

    `open_clip` lit tout le fichier en memoire puis le recopie : deux fois
    4,8 Go pour H-14, plus que ce qu'a un portable de 8 Go. Ici le fichier
    est projete (mmap) et chaque tenseur copie a sa place ; ceux de la moitie
    retiree (Identity) sont sautes."""
    import torch
    path = str(path)
    own = model.state_dict()
    if path.endswith(".safetensors"):
        # Lu a la main, tenseur par tenseur : `safe_open` reservait la
        # memoire du fichier entier (4,5 Go) a cote du modele -- plantage net
        # sur un portable de 8 Go.
        keys, get = _safetensors_reader(path)
    else:
        loaded = torch.load(path, map_location="cpu", mmap=True, weights_only=True)
        if isinstance(loaded, dict) and "state_dict" in loaded:
            loaded = loaded["state_dict"]
        keys, get = list(loaded.keys()), loaded.__getitem__
    done = 0
    with torch.no_grad():
        for key in keys:
            name = key[7:] if key.startswith("module.") else key
            target = own.get(name)
            if target is None:
                continue
            value = get(key)
            if value.shape != target.shape:
                if value.numel() != target.numel():
                    continue
                value = value.reshape(target.shape)
            target.copy_(value)
            done += 1
    if not done:
        raise RuntimeError("aucun poids reconnu dans " + path)


def _safetensors_reader(path: str) -> tuple:
    """(noms, lire(nom)) pour un fichier .safetensors, sans le projeter :
    une en-tete JSON, puis les nombres bruts de chaque tenseur."""
    import struct
    import torch
    kinds = {"F32": torch.float32, "F16": torch.float16, "BF16": torch.bfloat16,
             "F64": torch.float64, "I64": torch.int64, "I32": torch.int32,
             "I16": torch.int16, "I8": torch.int8, "U8": torch.uint8, "BOOL": torch.bool}
    with open(path, "rb") as handle:
        size = struct.unpack("<Q", handle.read(8))[0]
        header = json.loads(handle.read(size))
    base = 8 + size
    header.pop("__metadata__", None)

    def read(key):
        info = header[key]
        start, end = info["data_offsets"]
        with open(path, "rb") as handle:
            handle.seek(base + start)
            raw = bytearray(handle.read(end - start))
        dtype = kinds[info["dtype"]]
        if not raw:
            return torch.empty(info["shape"], dtype=dtype)
        return torch.frombuffer(raw, dtype=dtype).reshape(info["shape"])
    return list(header), read


def _die_with(parent) -> None:
    """Le processus du modele s'arrete avec Prisme, quoi qu'il fasse.

    Il ne regardait si Prisme vivait encore qu'entre deux demandes : occupe
    (un chargement, une indexation) ou bloque, il survivait a sa fenetre. Des
    moteurs orphelins de sessions fermees gardaient ainsi des gigaoctets de
    memoire -- le PC ramait, et les moteurs suivants plantaient, faute de
    place."""
    if parent is None:
        return
    import time as _time

    def watch() -> None:
        while True:
            _time.sleep(2)
            try:
                alive = parent.is_alive()
            except Exception:                               # noqa: BLE001
                alive = False
            if not alive:
                os._exit(0)
    threading.Thread(target=watch, daemon=True, name="prisme-labo-garde").start()


def _clip_child(name: str, requests, answers) -> None:
    """Le processus du modele : charge, et calcule les empreintes demandees.
    A part, et a basse priorite : importer torch et charger le modele, c'est
    des secondes de Python qui figeaient toute la fenetre de Prisme."""
    import multiprocessing
    import queue as _queue
    from .engine import _lower_priority, ensure_streams
    ensure_streams()
    _lower_priority()
    parent = multiprocessing.parent_process()
    _die_with(parent)
    engine = ClipEngine(name)
    while True:
        try:
            kind, payload = requests.get(timeout=2)
        except _queue.Empty:
            if parent is not None and not parent.is_alive():
                return
            continue
        except (EOFError, OSError):
            return
        if kind == "quit":
            return
        try:
            if kind == "load":
                engine.load(payload or "")
                result = engine.device
            elif kind == "images":
                result = engine.embed_images(payload)
            else:
                result = engine.embed_texts(payload)
            answers.put(("ok", result))
        except Exception as exc:                            # noqa: BLE001
            answers.put(("error", f"{type(exc).__name__} : {exc}"))


class RemoteClip:
    """Le modele CLIP, dans son propre processus (voir `_clip_child`). Meme
    usage que ClipEngine ; la fenetre de Prisme ne gele jamais."""

    def __init__(self, name: str = DEFAULT_ENGINE):
        self.name = name if name in ENGINES else DEFAULT_ENGINE
        self._process = None
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        # Depuis les sources, un processus fils ; dans le programme vendu, le
        # Python du labo (iapython) -- le meme dialogue de part et d'autre.
        self._process, self._requests, self._answers = iapython.spawn(
            _clip_child, (self.name,), "prisme-labo-modele")

    def _ask(self, kind: str, payload, timeout: float):
        import queue as _queue
        with self._lock:
            self._ensure()
            self._requests.put((kind, payload))
            deadline = timeout
            while True:
                try:
                    state, value = self._answers.get(timeout=min(5.0, deadline))
                    break
                except _queue.Empty:
                    deadline -= 5.0
                    if deadline <= 0 or not self._process.is_alive():
                        raise RuntimeError("le moteur ne répond pas")
        if state != "ok":
            raise RuntimeError(value)
        return value

    device = "cpu"

    def load(self, part: str = "") -> None:
        # La premiere fois, le modele se telecharge : jusqu'a une heure.
        self.device = self._ask("load", part, 3600) or "cpu"

    def embed_images(self, paths: list) -> list:
        # Un modele lourd peut devoir recharger sa partie image.
        return self._ask("images", list(paths), 1800)

    def embed_texts(self, texts: list) -> list:
        return self._ask("texts", list(texts), 1800)

    def close(self, wait: bool = False) -> None:
        """Arrete le processus du modele. `wait` : jusqu'a ce qu'il ait rendu
        la memoire (et la carte) -- avant d'en charger un autre."""
        process, self._process = self._process, None
        if process is None:
            return
        try:
            self._requests.put(("quit", None))
        except Exception:                                   # noqa: BLE001
            pass

        def finish() -> None:
            # En coulisse : attendre ici figeait la fenetre a la fermeture.
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
        if wait:
            finish()
        else:
            threading.Thread(target=finish, daemon=True, name="prisme-labo-fin").start()


class FakeEngine:
    """Un moteur pour les tests : l'image vaut sa couleur moyenne, la phrase
    ses mots de couleur. Deterministe, sans rien a telecharger."""

    name = "factice"
    device = "cpu"
    COLORS = {"rouge": (1, 0, 0), "red": (1, 0, 0), "vert": (0, 1, 0), "verte": (0, 1, 0),
              "green": (0, 1, 0), "bleu": (0, 0, 1), "bleue": (0, 0, 1), "blue": (0, 0, 1)}

    def load(self, part: str = "") -> None:
        pass

    def embed_images(self, paths: list) -> list:
        from PySide6.QtGui import QImage
        out = []
        for path in paths:
            image = QImage(str(path))
            r = g = b = 0.0
            count = 0
            for y in range(0, image.height(), max(1, image.height() // 8)):
                for x in range(0, image.width(), max(1, image.width() // 8)):
                    color = image.pixelColor(x, y)
                    r, g, b, count = r + color.redF(), g + color.greenF(), b + color.blueF(), count + 1
            out.append(_normalized((r / count, g / count, b / count, 0.05)))
        return out

    def embed_texts(self, texts: list) -> list:
        out = []
        for text in texts:
            vector = [0.0, 0.0, 0.0, 0.05]
            for word in text.lower().replace(",", " ").split():
                for index, value in enumerate(self.COLORS.get(word, (0, 0, 0))):
                    vector[index] += value
            out.append(_normalized(vector))
        return out


def frame_count(duration: float) -> int:
    """Combien d'images pour une video de cette duree : 3 sous deux minutes,
    puis une de plus par minute, jusqu'a MAX_FRAMES."""
    if duration <= 2:
        return 1
    if duration <= SHORT_S:
        return 3
    return min(MAX_FRAMES, 3 + int((duration - SHORT_S) // 60) + 1)


def spread(moments: list, wanted: int, duration: float) -> list:
    """`wanted` instants : pris parmi `moments` (bien repartis) s'il y en a
    assez, completes sinon par des instants reguliers, loin des existants."""
    moments = sorted({round(float(m), 2) for m in moments if 0 <= m <= max(duration, 0)})
    if wanted <= 0:
        return []
    if len(moments) >= wanted:
        if wanted == 1:
            return [moments[len(moments) // 2]]
        step = (len(moments) - 1) / (wanted - 1)
        return [moments[round(i * step)] for i in range(wanted)]
    out = list(moments)
    if duration > 2:
        gap = duration / (wanted * 2)
        for i in range(wanted):
            if len(out) >= wanted:
                break
            ts = duration * (i + 0.5) / wanted
            if all(abs(ts - m) >= gap for m in out):
                out.append(round(ts, 2))
    return sorted(out)[:wanted] if out else [0.0]


def _templates_for(engine) -> tuple:
    return TEMPLATES.get(getattr(engine, "name", ""), TEMPLATES["multilingue"])


def embed_query(engine, text: str) -> list:
    """L'empreinte d'une phrase : la moyenne de ses formulations."""
    phrases = [t.format(text) for t in _templates_for(engine)]
    vectors = engine.embed_texts(phrases)
    mean = [sum(column) / len(vectors) for column in zip(*vectors)]
    return _normalized(mean)


# -- l'index des images -------------------------------------------------------------------

def _split_path(path: str) -> list:
    return [p for p in re.split(r"[\\/]+", path) if p]


def _last_part(path: str) -> str:
    parts = _split_path(path)
    return parts[-1] if parts else ""


def _cut_at(path: str, count: int) -> int:
    """Ou commencent les `count` derniers elements du chemin (a leur
    separateur)."""
    at = len(path)
    for _ in range(count):
        at = max(path.rfind("\\", 0, at), path.rfind("/", 0, at))
        if at < 0:
            return 0
    return at


class SceneIndex:
    """Les empreintes des images de chaque video, sur le disque :
    <dossier>/<moteur>.json (quelles images) et .bin (les nombres, en float32).

    En memoire, un seul tableau numpy de float32. Une liste Python par image
    pesait des dizaines d'octets par nombre : toute la collection (six cent
    mille images de 512 nombres) aurait demande plus de quinze gigaoctets, et
    chaque recherche reconstruisait le tableau entier.
    """

    def __init__(self, engine_name: str, folder: Path = LAB_DIR):
        import numpy as np
        self._np = np
        self.folder = Path(folder)
        self.engine_name = engine_name
        self.rows: list = []            # (video, instant)
        self._matrix = np.zeros((0, 0), dtype="float32")
        self.dtype = "float32"
        self._fresh: list = []          # lignes ajoutees, empilees a la demande
        self.done: set = set()          # videos deja indexees
        self._load()

    # -- le tableau ----------------------------------------------------------------
    @property
    def vectors(self):
        """Toutes les empreintes, une ligne par image (tableau numpy)."""
        if self._fresh:
            np = self._np
            if not self._matrix.size and len(self._fresh[0]) >= HALF_FROM:
                self.dtype = "float16"
            fresh = np.asarray(self._fresh, dtype=self.dtype)
            self._matrix = fresh if not self._matrix.size else np.vstack([self._matrix, fresh])
            self._fresh = []
        return self._matrix

    @property
    def size(self) -> int:
        return len(self.rows)

    @property
    def _paths(self) -> tuple:
        return (self.folder / f"{self.engine_name}.json", self.folder / f"{self.engine_name}.bin")

    def _load(self) -> None:
        np = self._np
        head, body = self._paths
        try:
            meta = json.loads(head.read_text(encoding="utf-8"))
            self.dtype = meta.get("dtype") or "float32"
            raw = np.fromfile(str(body), dtype=self.dtype)
        except (OSError, ValueError):
            return
        width = int(meta.get("width") or 0)
        rows = meta.get("rows") or []
        if not width or raw.size != width * len(rows):
            return
        self.rows = [(str(v), float(t)) for v, t in rows]
        self._matrix = raw.reshape(len(rows), width)
        self.done = set(meta.get("done") or [v for v, _t in self.rows])
        if int(meta.get("sampling") or 1) != SAMPLING:
            # Indexees avec moins d'images : a refaire. Les anciennes servent
            # aux recherches en attendant.
            self.done = set()

    def save(self) -> None:
        head, body = self._paths
        self.folder.mkdir(parents=True, exist_ok=True)
        matrix = self.vectors
        width = int(matrix.shape[1]) if matrix.size else 0
        spare = body.with_suffix(".tmp")
        matrix.astype(self.dtype).tofile(str(spare))
        os.replace(spare, body)
        spare = head.with_suffix(".tmpj")
        spare.write_text(json.dumps({"width": width, "rows": self.rows, "sampling": SAMPLING,
                                     "dtype": self.dtype, "done": sorted(self.done)}),
                         encoding="utf-8")
        os.replace(spare, head)

    def _keep(self, keep: list) -> None:
        matrix = self.vectors
        self.rows = [self.rows[i] for i in keep]
        self._matrix = matrix[keep] if len(keep) else matrix[:0]

    def add(self, video: str, moments: list, vectors: list) -> None:
        video = str(video)
        if video in self.done or any(v == video for v, _t in self.rows[-64:]):
            # Refaite (nouvel echantillonnage) : ses anciennes images partent.
            keep = [i for i, (v, _t) in enumerate(self.rows) if v != video]
            if len(keep) != len(self.rows):
                self._keep(keep)
        for ts, vector in zip(moments, vectors):
            self.rows.append((video, float(ts)))
            self._fresh.append(list(vector))
        self.done.add(video)

    def forget_missing(self, videos: set) -> None:
        """Les videos parties de la collection quittent l'index."""
        self._keep([i for i, (v, _t) in enumerate(self.rows) if v in videos])
        self.done &= videos

    def rebase(self, videos) -> int:
        """Un index fait sur un autre PC : les memes videos, vues sous un autre
        chemin (Z:\\ au lieu de \\\\as1104t\\Volume 3). Les chemins de l'index
        prennent ceux de la collection d'ici, d'apres la fin commune de leurs
        chemins ; rien n'est a refaire. Rend le nombre de videos retrouvees."""
        here = {str(v) for v in videos}
        known = {v for v, _t in self.rows}
        missing = known - here
        if not here or not missing or len(missing) < len(known) // 2:
            return 0
        by_name: dict = {}
        for old in missing:
            by_name.setdefault(_last_part(old).lower(), []).append(old)
        votes: dict = {}
        for new in here - known:
            olds = by_name.get(_last_part(new).lower())
            if not olds or len(olds) != 1:
                continue
            old = olds[0]
            a, b = _split_path(old), _split_path(new)
            same = 0
            while (same < min(len(a), len(b)) - 1
                   and a[-1 - same].lower() == b[-1 - same].lower()):
                same += 1
            pair = (old[:_cut_at(old, same)], new[:_cut_at(new, same)])
            if pair[0] and pair[1] and pair[0] != pair[1]:
                votes[pair] = votes.get(pair, 0) + 1
        moves = sorted((p for p, n in votes.items() if n >= 2 or len(votes) == 1),
                       key=lambda p: -len(p[0]))
        if not moves:
            return 0

        def moved(video: str) -> str:
            low = video.lower()
            for old, new in moves:
                if low.startswith(old.lower()) and video[len(old):len(old) + 1] in ("\\", "/"):
                    rest = video[len(old):]
                    return new + (rest.replace("/", "\\") if "\\" in new or ":" in new
                                  else rest.replace("\\", "/"))
            return video

        names = {v: moved(v) for v in known}
        # Deja refaite ici sous son nouveau chemin : l'ancienne copie part.
        dropped = {v for v, n in names.items() if n != v and n in known}
        if dropped:
            self._keep([i for i, (v, _t) in enumerate(self.rows) if v not in dropped])
        self.rows = [(names[v], t) for v, t in self.rows]
        self.done = {names.get(v, v) for v in self.done}
        return sum(1 for v, n in names.items() if n != v and n in here)

    def save_head(self) -> None:
        """Seuls les chemins ont change : le gros .bin reste tel quel."""
        head, body = self._paths
        matrix = self.vectors
        width = int(matrix.shape[1]) if matrix.size else 0
        try:
            on_disk = body.stat().st_size // max(1, width * matrix.dtype.itemsize)
        except OSError:
            on_disk = -1
        if on_disk != len(self.rows):
            # Des lignes ont bouge (copie en double retiree, ajouts) : tout s'ecrit.
            self.save()
            on_disk = len(self.rows)
        spare = head.with_suffix(".tmpj")
        spare.write_text(json.dumps({"width": width, "rows": self.rows, "sampling": SAMPLING,
                                     "dtype": self.dtype, "done": sorted(self.done)}),
                         encoding="utf-8")
        os.replace(spare, head)
        # Les empreintes par video (voisines, doublons) gardent les anciens
        # chemins : elles se refont.
        try:
            (self.folder / f"{self.engine_name}.videos.json").unlink()
        except OSError:
            pass

    def snapshot(self) -> "SceneIndex":
        """L'index tel qu'il est a cet instant, fige : une recherche lancee
        pendant une indexation lisait un index qui grandissait entre deux
        idees de la phrase, et plantait (tailles differentes)."""
        frozen = object.__new__(SceneIndex)
        frozen.__dict__.update(self.__dict__)
        frozen._matrix = self.vectors
        frozen._fresh = []
        frozen.rows = list(self.rows)
        frozen.done = set(self.done)
        return frozen

    def frame_vectors(self, picks: list):
        """Les empreintes de ces images [(video, instant)] (pour « plus comme ça »)."""
        wanted = {(str(v), round(float(t), 2)) for v, t in picks}
        at = [i for i, (v, t) in enumerate(self.rows) if (v, round(t, 2)) in wanted]
        return self.vectors[at] if at else None

    # -- chercher ------------------------------------------------------------------
    def _scores(self, query):
        np = self._np
        matrix = self.vectors
        if not matrix.size:
            return np.zeros(0, dtype="float32")
        query = np.asarray(query, dtype="float32")
        if matrix.dtype == np.float32:
            return matrix @ query
        # Gardees en float16 (la moitie de la place) : calculees par
        # tranches, sans jamais doubler tout le tableau en memoire.
        out = np.empty(len(matrix), dtype="float32")
        for start in range(0, len(matrix), 65536):
            out[start:start + 65536] = matrix[start:start + 65536].astype("float32") @ query
        return out

    def videos(self) -> set:
        """Les videos qui ont au moins une image dans l'index."""
        return {v for v, _t in self.rows}

    def _z(self, query, bias=None):
        """La proximite de chaque image, en ecarts a la moyenne de l'index.

        Une proximite brute ne se compare pas d'une phrase a l'autre (« en
        exterieur » donne partout plus que « qui pisse ») ; ramenee a
        l'ensemble des images, oui : on peut alors exiger toutes les idees
        d'une phrase a la fois."""
        scores = self._scores(query)
        if bias is not None and len(bias) == len(scores):
            scores = scores - bias
        if scores.size < 2:
            return scores
        spread_ = float(scores.std()) or 1.0
        return (scores - float(scores.mean())) / spread_

    def rank(self, frame_scores, top: int = 60, only=None) -> list:
        """[(video, instant, score)] : la meilleure image de chaque video,
        des plus proches aux moins proches (parmi `only`, si donne)."""
        np = self._np
        if frame_scores is None or not len(frame_scores):
            return []
        order = np.argsort(-frame_scores)
        out, seen = [], set()
        for i in order:
            video, ts = self.rows[int(i)]
            if video in seen or (only is not None and video not in only):
                continue
            seen.add(video)
            out.append((video, ts, float(frame_scores[int(i)])))
            if len(out) >= top:
                break
        return out

    def best_per_video(self, query: list) -> dict:
        """video -> (meilleur score, instant de l'image la plus proche)."""
        best: dict = {}
        for (video, ts), score in zip(self.rows, self._scores(query).tolist()):
            if video not in best or score > best[video][0]:
                best[video] = (score, ts)
        return best

    def search(self, query: list, top: int = 60) -> list:
        """[(video, instant, score)], des plus proches aux moins proches."""
        return self.rank(self._scores(query), top)


# -- comprendre une demande ----------------------------------------------------------

# Les mots qui ouvrent une nouvelle idee dans une phrase francaise : « une
# amatrice | qui pisse | en exterieur ». Chaque idee doit se retrouver dans
# l'image : sans cela, « en exterieur » suffisait, et une plage quelconque
# passait devant la scene cherchee.
_JOINS = r"qui|que|avec|en|dans|sur|sous|pendant|devant|derri[eè]re|pr[eè]s|contre|au|aux|chez|par"
_SPLIT = re.compile(rf"\s+(?=(?:{_JOINS})\s)|\s+et\s+|\s*[,;+]\s*", re.IGNORECASE)
_LEAD = re.compile(r"^(?:qui|que|et)\s+", re.IGNORECASE)
_NEG = re.compile(r"(?:^|\s)(?:-|sans\s+|pas\s+de\s+|pas\s+d')([^\s,;+-][^,;+]*?)(?=$|[,;+]|\s+-|\s+sans\s)",
                  re.IGNORECASE)


def parse_query(text: str) -> tuple:
    """(phrase entiere, idees a exiger toutes, idees a ecarter).

    « amatrice qui pisse en extérieur -plage » donne la phrase « amatrice qui
    pisse en extérieur », les idees « amatrice », « pisse », « en extérieur »,
    et l'idee a ecarter « plage ». Les virgules separent des idees a la main."""
    text = " ".join(str(text).split())
    negatives = [m.strip() for m in _NEG.findall(text) if m.strip()]
    whole = _NEG.sub(" ", text)
    whole = " ".join(whole.split()).strip(" ,;+")
    parts = []
    for piece in _SPLIT.split(whole):
        piece = _LEAD.sub("", piece.strip(" ,;+"))
        if len(piece.replace(" ", "")) >= 3 and piece.lower() not in {p.lower() for p in parts}:
            parts.append(piece)
    if len(parts) < 2:
        parts = []
    return whole or text, parts, negatives


# Des phrases tres variees : la moyenne de ce qu'une image leur ressemble est
# son « penchant general ». Certaines images ressortent haut pour a peu pres
# n'importe quelle phrase ; sur une vraie collection, « foret » et « une
# voiture » donnaient des classements correles a 0,69. Retirer ce penchant a
# chaque image les ramene a 0,37 (« une douche » / « une voiture » : de 0,35 a
# 0,01) : chaque recherche ne repond plus qu'a elle-meme.
BIAS_BANK = ("a photo", "a room", "a person", "a car", "a dog", "food on a table",
             "a city street", "a landscape", "a document", "a cartoon", "a sports game",
             "a concert", "a beach", "a forest", "a kitchen", "an office", "a bedroom",
             "a bathroom", "a face close-up", "text on a screen", "a group of people",
             "an animal", "the sky", "a building", "water", "a dark image", "a bright image",
             "a phone video", "a professional video", "clothes")
_BANKS: dict = {}


def image_bias(index: SceneIndex, engine):
    """Le penchant general de chaque image (voir BIAS_BANK), garde avec
    l'index et complete a mesure qu'il grandit."""
    np = index._np
    name = getattr(engine, "name", "")
    bank = _BANKS.get(name)
    if bank is None:
        bank = np.asarray(engine.embed_texts(list(BIAS_BANK)), dtype="float32")
        _BANKS[name] = bank
    cached = getattr(index, "_bias", None)
    matrix = index.vectors
    have = 0 if cached is None or cached[0] != name else len(cached[1])
    if have > len(matrix):
        have = 0                       # l'index a perdu des lignes : on refait tout
    if have == len(matrix):
        return cached[1]
    parts = [cached[1][:have]] if have else []
    for start in range(have, len(matrix), 65536):
        rows = matrix[start:min(len(matrix), start + 65536)].astype("float32")
        parts.append((rows @ bank.T).mean(axis=1))
    bias = np.concatenate(parts) if parts else np.zeros(0, dtype="float32")
    index._bias = (name, bias)
    return bias


def smart_scores(index: SceneIndex, engine, text: str, liked=None, disliked=None):
    """La pertinence de chaque image pour une demande (voir `parse_query`),
    affinee par des exemples : `liked` / `disliked`, des empreintes d'images
    (« plus comme ça », « moins comme ça »)."""
    np = index._np
    whole, parts, negatives = parse_query(text)
    try:
        bias = image_bias(index, engine)
    except Exception:                                        # noqa: BLE001
        bias = None
    score = index._z(embed_query(engine, whole), bias) if whole else None
    if parts:
        each = np.vstack([index._z(embed_query(engine, part), bias) for part in parts])
        # La plus faible des idees decide : toutes doivent y etre.
        weakest = each.min(axis=0)
        score = weakest if score is None else 0.4 * score + 0.6 * weakest
    if score is None:
        score = np.zeros(index.size, dtype="float32")
    for negative in negatives:
        score = score - 0.6 * np.maximum(index._z(embed_query(engine, negative), bias), 0.0)
    if liked is not None and len(liked):
        score = score + 0.8 * index._z(_centroid(np, liked))
    if disliked is not None and len(disliked):
        # Aussi fort que les bons exemples : « moins comme ça » ne se voyait
        # presque pas.
        score = score - 0.9 * np.maximum(index._z(_centroid(np, disliked)), 0.0)
    return score


def _centroid(np, vectors):
    mean = np.asarray(vectors, dtype="float32").mean(axis=0)
    return mean / (float(np.linalg.norm(mean)) or 1.0)


def smart_search(index: SceneIndex, engine, text: str, top: int = 60,
                 liked=None, disliked=None, only=None) -> list:
    """[(video, instant, score)] : une video par ligne, a son meilleur moment
    (parmi `only`, si donne : l'essai compare les moteurs sur les memes)."""
    if not index.size:
        return []
    frozen = index.snapshot()
    found = frozen.rank(smart_scores(frozen, engine, text, liked, disliked), top, only)
    # Le penchant des images, calcule sur la copie, sert aussi a l'original.
    cached = getattr(frozen, "_bias", None)
    if cached is not None and len(cached[1]) <= index.size:
        index._bias = cached
    return found


def build(index: SceneIndex, engine, videos: list, frame_of, duration_of=None,
          progress=lambda done, total: None, stop=lambda: False, save_every: int = 40,
          moments_of=None, workers: int = 1) -> int:
    """Indexe les videos qui ne le sont pas encore. `frame_of(video, instant)`
    rend le chemin d'une image (ou None) ; `moments_of(video)` les instants a
    prendre (sinon, selon la duree : `frame_count`). Rend le nombre de videos
    ajoutees.

    `workers` : combien de videos dont on extrait les images a la fois. C'est
    l'extraction sur le partage qui coute (quatre secondes par video, une
    image apres l'autre), pas le modele ; trois a la fois la font avancer
    trois fois plus vite sans assommer le NAS."""
    todo = [v for v in videos if str(v) not in index.done]
    added = 0
    pending: list = []                  # (video, instants, chemins)

    def frames_for(video) -> tuple:
        if moments_of is not None:
            wanted = list(moments_of(video))
        else:
            duration = float(duration_of(video) or 0) if duration_of else 0.0
            wanted = spread([], frame_count(duration), duration)
        moments, paths = [], []
        for ts in wanted:
            if stop():
                break
            path = frame_of(video, ts)
            if path:
                moments.append(ts)
                paths.append(str(path))
        return moments, paths

    def flush() -> None:
        paths = [p for _v, _m, ps in pending for p in ps]
        if not paths:
            pending.clear()
            return
        vectors = []
        for start in range(0, len(paths), BATCH):
            vectors += engine.embed_images(paths[start:start + BATCH])
        at = 0
        for video, moments, ps in pending:
            index.add(video, moments, vectors[at:at + len(ps)])
            at += len(ps)
        pending.clear()

    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=workers) if workers > 1 else None
    step = max(1, workers * 2)
    number = 0
    try:
        for start in range(0, len(todo), step):
            if stop():
                break
            chunk = todo[start:start + step]
            found = pool.map(frames_for, chunk) if pool is not None else map(frames_for, chunk)
            for video, (moments, paths) in zip(chunk, found):
                if stop():
                    break
                number += 1
                if paths:
                    pending.append((str(video), moments, paths))
                    added += 1
                elif moments_of is None or list(moments_of(video)):
                    # Illisible : on ne reessaie pas a chaque fois.
                    index.done.add(str(video))
                if len(pending) >= BATCH:
                    flush()
                if number % save_every == 0:
                    flush()
                    index.save()
                progress(number, len(todo))
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
    flush()
    index.save()
    return added


# Ce qu'exige un tag, en ecarts a la moyenne des images : souple, normal, strict.
STRICTNESS = {"souple": 2.0, "normal": 2.6, "strict": 3.2}


def tag_matches(index: SceneIndex, engine, tags: list, margin: float = 0.02,
                top: int = 200, strictness: str = "") -> dict:
    """Pour chaque tag (une phrase), les videos qui lui ressemblent le plus :
    {tag: [(video, instant, score)]}.

    Meme comprehension qu'une recherche (`smart_scores`) ; une video recoit le
    tag si sa meilleure image s'ecarte nettement de l'ordinaire (`strictness`).
    Sans `strictness`, l'ancienne regle : plus proche d'une phrase neutre d'au
    moins `margin`."""
    tags = [t.strip() for t in tags if t.strip()]
    if not tags or not index.size:
        return {t: [] for t in tags}
    found = {}
    if strictness:
        floor = STRICTNESS.get(strictness, STRICTNESS["normal"])
        index = index.snapshot()
        for tag in tags:
            ranked = index.rank(smart_scores(index, engine, tag), top)
            found[tag] = [row for row in ranked if row[2] >= floor]
        return found
    neutral = engine.embed_texts(["une image", "a picture"])
    vectors = [embed_query(engine, tag) for tag in tags]
    low = {}
    for vector in neutral:
        for video, (score, _ts) in index.best_per_video(vector).items():
            low[video] = max(low.get(video, -1.0), score)
    for tag, vector in zip(tags, vectors):
        best = index.best_per_video(vector)
        keep = [(video, ts, score) for video, (score, ts) in best.items()
                if score - low.get(video, -1.0) >= margin]
        keep.sort(key=lambda item: -item[2])
        found[tag] = keep[:top]
    return found


def rename_videos(moves: dict, folder: Path = None, live=()) -> int:
    """Les index des moteurs suivent les fichiers deplaces : {ancien chemin
    (fichier ou dossier) : nouveau}. Sans cela, une video rangee ailleurs
    disparaissait des recherches, puis etait indexee une seconde fois.

    `live` : des index deja en memoire (la fenetre du labo ouverte) -- ils
    changent en place et s'enregistrent ; les autres se corrigent dans leur
    en-tete, sans toucher aux empreintes (l'ordre des lignes ne change pas).
    Rend le nombre de lignes deplacees."""
    if not moves:
        return 0
    exact = {str(k): str(v) for k, v in moves.items()}
    roots = [(k.rstrip("\\/"), v.rstrip("\\/")) for k, v in exact.items()]

    def follow(path: str):
        found = exact.get(path)
        if found is not None:
            return found
        for old, new in roots:
            if path.startswith(old) and path[len(old):len(old) + 1] in ("\\", "/"):
                return new + path[len(old):]
        return None

    changed = 0
    done_live = set()
    for index in live:
        if index is None:
            continue
        done_live.add(index.engine_name)
        rows = []
        for video, ts in index.rows:
            new = follow(video)
            if new is not None:
                changed += 1
                video = new
            rows.append((video, ts))
        index.rows = rows
        index.done = {follow(v) or v for v in index.done}
        index.save()
    folder = Path(folder or LAB_DIR)
    for name in ENGINES:
        if name in done_live:
            continue
        head = folder / f"{name}.json"
        try:
            meta = json.loads(head.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        moved = 0
        rows = []
        for video, ts in meta.get("rows") or []:
            new = follow(str(video))
            if new is not None:
                moved += 1
                video = new
            rows.append([video, ts])
        done = [follow(str(v)) or v for v in meta.get("done") or []]
        if not moved and done == list(meta.get("done") or []):
            continue
        meta["rows"], meta["done"] = rows, done
        spare = head.with_suffix(".tmpj")
        spare.write_text(json.dumps(meta), encoding="utf-8")
        os.replace(spare, head)
        changed += moved
    return changed


def known_moments(exclude: str = "", folder: Path = None) -> dict:
    """{video: [instants]} deja pris par les autres moteurs (lu dans leurs
    en-tetes, sans charger les empreintes). Un moteur qui indexe apres un
    autre reprend ces instants : leurs images sont deja en cache, seul son
    propre calcul reste a faire -- l'extraction sur le partage ne se paie
    qu'une fois."""
    folder = Path(folder or LAB_DIR)
    found: dict = {}
    for name in ENGINES:
        if name == exclude:
            continue
        try:
            meta = json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for video, ts in meta.get("rows") or []:
            found.setdefault(str(video), set()).add(float(ts))
    return {video: sorted(times) for video, times in found.items()}


def fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]
