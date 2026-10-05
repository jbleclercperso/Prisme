"""La saisie vocale : un micro a cote des champs de recherche.

On parle, Prisme ecrit, puis cherche. Tout se passe sur ce PC : la voix
n'est envoyee nulle part. La reconnaissance est celle de Whisper (OpenAI,
modele « small », multilingue : francais et anglais melanges), telecharge une
fois puis garde sur le disque. Elle tourne dans son propre processus, comme le
moteur du Labo IA : importer torch et charger le modele figeait la fenetre.

L'enregistrement s'arrete tout seul quand on se tait, ou d'un second clic.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import iapython, voice_model

from .voice_model import MODEL, MODEL_SIZE, PATTERNS, RATE  # noqa: F401

# Le silence qui clot la phrase, et les bornes de l'ecoute (secondes).
SILENCE_END = 1.3
NOTHING_SAID = 7.0
LONGEST = 15.0
# Au-dessus de ce niveau (moyenne quadratique, de 0 a 1), c'est de la voix.
# Releve sur le bruit de fond des premiers instants, il s'ajuste a la piece.
SPEECH_FLOOR = 0.008


def available() -> bool:
    """torch et transformers sont-ils la ? (Les paquets du Labo IA.) Sans
    rien importer : le demander ne doit rien couter."""
    return all(iapython.has(name) for name in ("torch", "transformers", "numpy"))


def _hub_cache() -> Path:
    for key in ("HUGGINGFACE_HUB_CACHE", "HF_HUB_CACHE"):
        if os.environ.get(key):
            return Path(os.environ[key])
    home = os.environ.get("HF_HOME")
    return (Path(home) / "hub") if home else Path.home() / ".cache" / "huggingface" / "hub"


def model_on_disk() -> bool:
    folder = _hub_cache() / ("models--" + MODEL.replace("/", "--")) / "snapshots"
    try:
        for weights in folder.glob("*/model.safetensors"):
            if weights.stat().st_size > 100 * 2**20:
                return True
    except OSError:
        pass
    return False


def _model_folder() -> Path:
    return _hub_cache() / ("models--" + MODEL.replace("/", "--"))


def downloaded_bytes() -> int:
    """Ce qui est deja arrive (fichiers en cours compris) : le pourcentage."""
    total = 0
    try:
        for blob in (_model_folder() / "blobs").iterdir():
            total += blob.stat().st_size
    except OSError:
        pass
    return total


MODEL_BYTES = 967_000_000 + 3_000_000
_downloading = False


def download_progress():
    """Ou en est le telechargement du modele (de 0 a 1), ou None s'il n'y
    en a pas : la pastille de la barre de Prisme."""
    if not _downloading:
        return None
    return min(0.99, downloaded_bytes() / MODEL_BYTES)


def download() -> None:
    """Telecharge le modele (une fois). Long : hors du fil de l'interface.
    Le pourcentage se lit sur le disque (`downloaded_bytes`). Dans le
    programme vendu, c'est le Python du labo qui telecharge : huggingface_hub
    n'est que la-bas."""
    global _downloading
    _downloading = True
    try:
        iapython.call(voice_model.fetch_model, (), name="prisme-voix-modele")
    finally:
        _downloading = False
    if not model_on_disk():
        raise RuntimeError("le modèle n'est pas arrivé en entier")


# -- le processus de la reconnaissance -----------------------------------------
class _Recognizer:
    """La reconnaissance, dans son processus ; un seul pour tout Prisme."""

    def __init__(self):
        self.ready = False
        self._process = None
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._process, self._requests, self._answers = iapython.spawn(
            voice_model.voice_child, (), "prisme-voix")

    def _ask(self, kind: str, payload, timeout: float):
        import queue as _queue
        with self._lock:
            self._ensure()
            self._requests.put((kind, payload))
            left = timeout
            while True:
                try:
                    state, value = self._answers.get(timeout=min(2.0, left))
                    break
                except _queue.Empty:
                    left -= 2.0
                    if left <= 0 or not self._process.is_alive():
                        raise RuntimeError("la reconnaissance ne répond pas")
        if state != "ok":
            raise RuntimeError(value)
        return value

    def warm(self) -> None:
        """Charge le modele d'avance. Une minute environ sur un portable --
        importer torch et transformers en prend l'essentiel --, d'ou le
        chargement discret apres le lancement de Prisme (`window`)."""
        if self.ready and self._process is not None and self._process.is_alive():
            return
        self._ask("load", None, 600)
        self.ready = True

    def transcribe(self, samples: bytes) -> str:
        return self._ask("audio", samples, 300)

    def close(self) -> None:
        self.ready = False
        process, self._process = self._process, None
        if process is None:
            return
        try:
            self._requests.put(("quit", None))
        except Exception:                                   # noqa: BLE001
            pass
        threading.Thread(target=lambda: process.join(3), daemon=True).start()


RECOGNIZER = _Recognizer()


# -- l'ecoute -------------------------------------------------------------------
class Recorder(QObject):
    """Ecoute le micro jusqu'au silence qui suit la phrase.

    `level` : le niveau, de 0 a 1, pour que le bouton vive pendant qu'on parle.
    `done` : l'audio en 16 kHz mono float32 (bytes), vide si rien n'a ete dit.
    `failed` : pas de micro, ou il refuse de s'ouvrir.
    """

    level = Signal(float)
    done = Signal(bytes)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._device = None
        self._chunks: list = []
        self._rate = RATE
        self._channels = 1
        self._kind = "int16"
        self._heard = False
        self._quiet = 0.0
        self._elapsed = 0.0
        self._noise: list = []
        self._finished = False

    @property
    def listening(self) -> bool:
        return self._source is not None

    def start(self) -> None:
        from PySide6.QtMultimedia import QAudioFormat, QAudioSource
        from .audiodev import best_input
        # Jamais le micro « mains libres » d'un casque s'il y en a un autre :
        # l'ouvrir le fait passer en mode appel, et les videos se taisent.
        device = best_input()
        if device.isNull():
            self.failed.emit("Aucun micro n'est branché (ou Windows ne le donne pas).")
            return
        wanted = QAudioFormat()
        wanted.setSampleRate(RATE)
        wanted.setChannelCount(1)
        wanted.setSampleFormat(QAudioFormat.Int16)
        fmt = wanted if device.isFormatSupported(wanted) else device.preferredFormat()
        self._rate = fmt.sampleRate()
        self._channels = max(1, fmt.channelCount())
        self._kind = {QAudioFormat.Int16: "int16", QAudioFormat.Int32: "int32",
                      QAudioFormat.Float: "float32",
                      QAudioFormat.UInt8: "uint8"}.get(fmt.sampleFormat(), "int16")
        self._chunks, self._noise = [], []
        self._heard, self._quiet, self._elapsed, self._finished = False, 0.0, 0.0, False
        self._source = QAudioSource(device, fmt, self)
        self._device = self._source.start()
        if self._device is None:
            self._source = None
            self.failed.emit("Le micro refuse de s'ouvrir. Windows › Paramètres › "
                             "Confidentialité › Microphone : autorisez les applications "
                             "de bureau.")
            return
        self._device.readyRead.connect(self._read)
        # Un micro muet (desactive dans Windows) ne declenche jamais readyRead.
        QTimer.singleShot(int(NOTHING_SAID * 1000) + 500, self._check_alive)

    def _check_alive(self) -> None:
        if self._source is not None and not self._chunks:
            self.stop()

    def _samples(self, raw: bytes):
        import numpy as np
        if self._kind == "float32":
            data = np.frombuffer(raw, dtype=np.float32)
        elif self._kind == "int32":
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2**31
        elif self._kind == "uint8":
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
        else:
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768
        if self._channels > 1:
            usable = len(data) - len(data) % self._channels
            data = data[:usable].reshape(-1, self._channels).mean(axis=1)
        return data

    def _read(self) -> None:
        import numpy as np
        if self._device is None:
            return
        raw = bytes(self._device.readAll())
        if not raw:
            return
        data = self._samples(raw)
        if not len(data):
            return
        self._chunks.append(data)
        seconds = len(data) / float(self._rate)
        self._elapsed += seconds
        power = float(np.sqrt(np.mean(data * data)))
        self.level.emit(min(1.0, power * 8))
        if self._elapsed < 0.4:
            self._noise.append(power)
        # Le bruit de la piece, mais plafonne : qui parle des le clic ne doit
        # pas passer pour du bruit de fond.
        noise = sum(self._noise) / len(self._noise) if self._noise else 0.0
        floor = min(0.04, max(SPEECH_FLOOR, 2.0 * noise))
        if power > floor:
            self._heard, self._quiet = True, 0.0
        else:
            self._quiet += seconds
        if ((self._heard and self._quiet >= SILENCE_END)
                or (not self._heard and self._elapsed >= NOTHING_SAID)
                or self._elapsed >= LONGEST):
            self.stop()

    def stop(self) -> None:
        """Fin de l'ecoute (second clic, ou silence) : rend ce qui a ete dit."""
        import numpy as np
        if self._finished:
            return
        self._finished = True
        source, self._source, self._device = self._source, None, None
        if source is not None:
            source.stop()
            source.deleteLater()
        if not self._heard or not self._chunks:
            self.done.emit(b"")
            return
        audio = np.concatenate(self._chunks)
        if self._rate != RATE:
            # Reechantillonnage lineaire : largement assez pour la voix.
            count = int(len(audio) * RATE / self._rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, count),
                              np.arange(len(audio)), audio)
        self.done.emit(audio.astype(np.float32).tobytes())


# -- le micro d'un champ ----------------------------------------------------------
class VoiceInput(QObject):
    """Le micro d'un champ de recherche : un clic, on parle, le texte arrive.

    `on_text(texte)` recoit ce qui a ete dit ; `tell(texte)` dit ce qui se
    passe (un bandeau de la fenetre, d'ordinaire). Avec un QLineEdit, le micro
    se pose dans le champ, a droite ; sinon `button()` en fabrique un.
    """

    state = Signal(str)          # "repos", "ecoute", "reconnaissance", "telechargement"

    TIPS = {
        "repos": "Dicter la recherche (le micro du PC) — tout reste sur ce PC",
        "ecoute": "J'écoute… parlez, puis taisez-vous (ou cliquez pour finir)",
        "reconnaissance": "Reconnaissance en cours…",
        "telechargement": "Téléchargement du modèle de reconnaissance vocale…",
    }
    COLORS = {"repos": None, "ecoute": "#ff5d6c", "reconnaissance": "#8fb4ff",
              "telechargement": "#8fb4ff"}

    def __init__(self, field, on_text, tell=None, parent=None):
        super().__init__(parent or field)
        self.field = field
        self.on_text = on_text
        self.tell = tell or (lambda text: None)
        self.mode = "repos"
        self.recorder = Recorder(self)
        self.recorder.done.connect(self._heard)
        self.recorder.failed.connect(self._failed)
        self.action = None
        self._button = None
        from PySide6.QtWidgets import QLineEdit
        if isinstance(field, QLineEdit):
            from PySide6.QtGui import QAction
            self.action = QAction(self)
            self.action.triggered.connect(self.trigger)
            field.addAction(self.action, QLineEdit.TrailingPosition)
        self._show("repos")

    def button(self, parent=None):
        """Un bouton micro, pour un champ qui ne peut pas le porter."""
        from PySide6.QtCore import QSize, Qt
        from PySide6.QtWidgets import QToolButton
        self._button = QToolButton(parent)
        self._button.setObjectName("micButton")
        self._button.setIconSize(QSize(18, 18))
        self._button.setAutoRaise(True)
        self._button.setFocusPolicy(Qt.NoFocus)
        self._button.setCursor(Qt.PointingHandCursor)
        self._button.clicked.connect(self.trigger)
        self._show(self.mode)
        return self._button

    def _show(self, mode: str) -> None:
        from .icons import icon
        self.mode = mode
        color = self.COLORS.get(mode)
        glyph = icon("mic", color) if color else icon("mic")
        for target in (self.action, self._button):
            if target is not None:
                target.setIcon(glyph)
                target.setToolTip(self.TIPS.get(mode, ""))
        self.state.emit(mode)

    def trigger(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        if self.mode == "ecoute":
            self.recorder.stop()
            return
        if self.mode != "repos":
            return
        if not available():
            self.tell("La saisie vocale utilise les paquets du Labo IA (torch, "
                      "transformers) : installez-les depuis ⋯ › Collection › Labo IA, "
                      "puis réessayez.")
            return
        if not model_on_disk():
            window = self.field.window()
            answer = QMessageBox.question(
                window, "Saisie vocale",
                f"Première fois : Prisme télécharge le modèle de reconnaissance "
                f"vocale (Whisper « small », {MODEL_SIZE}, depuis Hugging Face), une "
                f"seule fois.\n\nEnsuite tout se passe sur ce PC : votre voix n'est "
                f"envoyée nulle part. Français et anglais, même mélangés.\n\n"
                f"Télécharger maintenant ?")
            if answer != QMessageBox.Yes:
                return
            self._show("telechargement")
            self.tell(f"Téléchargement du modèle de reconnaissance vocale ({MODEL_SIZE})…")
            self._watch = QTimer(self)
            self._watch.setInterval(4000)
            self._watch.timeout.connect(self._progress)
            self._watch.start()
            self._run(download, self._downloaded)
            return
        # Le modele se charge pendant qu'on parle : il est pret a la fin.
        self._run(RECOGNIZER.warm, lambda _r: None)
        self._show("ecoute")
        self.recorder.start()

    def _run(self, work, then) -> None:
        from .tunnel import Chore

        def guarded():
            try:
                return ("ok", work())
            except Exception as exc:                        # noqa: BLE001
                return ("error", f"{type(exc).__name__} : {exc}")
        Chore(guarded, self, fallback=("error", "interrompu"), then=then).start()

    def _progress(self) -> None:
        """Ou en est le telechargement : dans le bandeau et sur le micro."""
        part = min(99, int(100 * downloaded_bytes() / MODEL_BYTES))
        text = f"Téléchargement du modèle de reconnaissance vocale : {part} %"
        for target in (self.action, self._button):
            if target is not None:
                target.setToolTip(text)
        self.tell(text)

    def _downloaded(self, result) -> None:
        watch = getattr(self, "_watch", None)
        if watch is not None:
            watch.stop()
            self._watch = None
        self._show("repos")
        if result[0] != "ok":
            self.tell(f"Téléchargement impossible ({result[1]}). Réessayez plus tard.")
            return
        self.tell("Modèle de reconnaissance vocale prêt : cliquez sur le micro et parlez.")

    def _failed(self, why: str) -> None:
        self._show("repos")
        self.tell(why)

    def _heard(self, audio: bytes) -> None:
        if not audio:
            self._show("repos")
            self.tell("Je n'ai rien entendu. Cliquez sur le micro, puis parlez.")
            return
        self._show("reconnaissance")
        if not RECOGNIZER.ready:
            self.tell("Chargement de la reconnaissance vocale… jusqu'à une minute, "
                      "une seule fois par lancement de Prisme.")
        self._run(lambda: RECOGNIZER.transcribe(audio), self._recognized)

    def _recognized(self, result) -> None:
        self._show("repos")
        if result[0] != "ok":
            self.tell(f"Reconnaissance impossible ({result[1]}).")
            return
        text = (result[1] or "").strip().strip(".!?…").strip()
        if not text:
            self.tell("Je n'ai pas compris. Réessayez, un peu plus près du micro.")
            return
        self.on_text(text)


def attach(field, on_text, tell=None) -> VoiceInput:
    return VoiceInput(field, on_text, tell)
