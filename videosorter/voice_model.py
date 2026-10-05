"""La reconnaissance vocale elle-meme, sans Qt : ce que fait tourner le
processus de la reconnaissance (`voice_child`), et le telechargement du
modele (`fetch_model`). A part de voice.py parce que, dans le programme
vendu, c'est le Python du labo qui l'importe -- et il n'a pas PySide6.
"""
from __future__ import annotations

import os

MODEL = "openai/whisper-small"
MODEL_SIZE = "970 Mo"
RATE = 16000                 # ce qu'attend Whisper : 16 kHz, mono
# Les fichiers utiles du depot : il porte aussi les poids TensorFlow, Flax et
# l'ancien format, trois fois le telechargement pour rien.
PATTERNS = ["*.json", "model.safetensors", "*.txt"]


def fetch_model() -> None:
    """Telecharge le modele. Sans console, la barre de progression de
    huggingface_hub ecrivait dans un sys.stderr absent : le telechargement
    s'arretait des le depart. Plus de barre."""
    from .engine import ensure_streams
    ensure_streams()
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import disable_progress_bars
    disable_progress_bars()
    snapshot_download(MODEL, allow_patterns=PATTERNS)


def voice_child(requests, answers) -> None:
    import multiprocessing
    import queue as _queue
    os.environ["HF_HUB_OFFLINE"] = "1"
    from .engine import _lower_priority, ensure_streams
    from .ia import _die_with
    ensure_streams()
    _lower_priority()
    parent = multiprocessing.parent_process()
    _die_with(parent)
    model = processor = None
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
            import numpy as np
            import torch
            if model is None:
                from transformers import WhisperForConditionalGeneration, WhisperProcessor
                torch.set_num_threads(max(1, (os.cpu_count() or 4) - 1))
                processor = WhisperProcessor.from_pretrained(MODEL)
                model = WhisperForConditionalGeneration.from_pretrained(MODEL)
                model.eval()
                device, dtype = "cpu", torch.float32
                try:
                    # La carte NVIDIA, si elle a la place : une phrase y est
                    # reconnue en moins d'une seconde, contre huit sur le
                    # processeur d'un portable.
                    if torch.cuda.is_available() and torch.cuda.mem_get_info()[0] > 900 * 2**20:
                        model = model.to("cuda", dtype=torch.float16)
                        device, dtype = "cuda", torch.float16
                except Exception:                           # noqa: BLE001
                    model = model.to("cpu", dtype=torch.float32)
                    device, dtype = "cpu", torch.float32
            if kind == "load":
                # Une seconde de silence, une fois : la carte prepare ses
                # calculs maintenant, et non pendant la premiere phrase.
                silence = processor(np.zeros(RATE, dtype=np.float32), sampling_rate=RATE,
                                    return_tensors="pt").input_features.to(device, dtype=dtype)
                with torch.inference_mode():
                    model.generate(silence, task="transcribe", max_new_tokens=4)
                answers.put(("ok", device))
                continue
            audio = np.frombuffer(payload, dtype=np.float32)
            features = processor(audio, sampling_rate=RATE,
                                 return_tensors="pt").input_features.to(device, dtype=dtype)
            with torch.inference_mode():
                ids = model.generate(features, task="transcribe", max_new_tokens=96)
            text = processor.batch_decode(ids, skip_special_tokens=True)[0]
            answers.put(("ok", text.strip()))
        except Exception as exc:                            # noqa: BLE001
            answers.put(("error", f"{type(exc).__name__} : {exc}"))
