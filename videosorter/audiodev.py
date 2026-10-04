"""La sortie son des lecteurs, et le micro de la saisie vocale.

Deux pieges, venus d'un casque Bluetooth :

- Un casque a deux sorties : « Stereo », pour ecouter, et « Hands-Free »
  (mains libres), pour telephoner, qui ne joue rien hors d'un appel. Windows
  met parfois la seconde par defaut -- et toutes les videos etaient muettes.
  On prend alors la stereo du meme casque.
- Qt garde la sortie choisie a la creation du lecteur : un casque connecte,
  ou une sortie par defaut changee, apres le lancement de Prisme, et le son
  partait vers l'ancienne. Les sorties suivent desormais (`follow`).

Pour la voix, on evite de meme le micro « mains libres » d'un casque : l'ouvrir
fait basculer le casque en mode appel, et le son des videos s'arrete.
"""
from __future__ import annotations

import shiboken6
from PySide6.QtCore import QObject, QTimer
from PySide6.QtMultimedia import QMediaDevices

HANDS_FREE = ("hands-free", "handsfree", "mains libres", "mains-libres")


def hands_free(device) -> bool:
    low = device.description().lower()
    return any(word in low for word in HANDS_FREE)


def _head(device) -> str:
    """Le nom de l'appareil, sans son profil : « casque (wh-h910n (h.ear) »."""
    low = device.description().lower()
    for word in HANDS_FREE + ("stereo", "stéréo"):
        at = low.find(word)
        if at > 0:
            low = low[:at]
    return low.strip(" ()")


# La sortie choisie a la main (clic droit sur le haut-parleur), par son nom ;
# vide : celle de Windows.
_chosen = ""


def choose(description: str) -> None:
    """Une sortie choisie a la main, ou "" pour suivre celle de Windows."""
    global _chosen
    _chosen = description or ""
    if _follower is not None:
        _follower.check()


def chosen() -> str:
    return _chosen


def outputs() -> list:
    """Les sorties ou l'on peut ecouter (les « mains libres » ecartees)."""
    return [out for out in QMediaDevices.audioOutputs() if not hands_free(out)]


def best_output():
    """La sortie choisie a la main, si elle est la ; sinon celle de Windows,
    sauf si c'est un « mains libres » : alors la stereo du meme casque, a
    defaut n'importe quelle autre."""
    if _chosen:
        for out in QMediaDevices.audioOutputs():
            if out.description() == _chosen:
                return out
    default = QMediaDevices.defaultAudioOutput()
    if default.isNull() or not hands_free(default):
        return default
    others = [out for out in QMediaDevices.audioOutputs() if not hands_free(out)]
    head = _head(default)
    for out in others:
        if head and _head(out) == head:
            return out
    return others[0] if others else default


def best_input():
    """Le micro par defaut, sauf celui, « mains libres », d'un casque."""
    default = QMediaDevices.defaultAudioInput()
    if default.isNull() or not hands_free(default):
        return default
    for device in QMediaDevices.audioInputs():
        if not hands_free(device):
            return device
    return default


class _Follower(QObject):
    """Toutes les sorties son des lecteurs, rebranchees quand la bonne
    sortie change (casque connecte, sortie par defaut changee)."""

    def __init__(self):
        super().__init__()
        self.outputs: list = []
        self.current = b""
        self.devices = QMediaDevices(self)
        self.devices.audioOutputsChanged.connect(self.check)
        # Windows ne previent pas toujours d'un changement de sortie par
        # defaut : un coup d'oeil de temps en temps, qui ne coute rien.
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def adopt(self, output) -> None:
        device = best_output()
        if not device.isNull():
            output.setDevice(device)
            self.current = bytes(device.id())
        self.outputs.append(output)

    def check(self) -> None:
        device = best_output()
        if device.isNull():
            return
        wanted = bytes(device.id())
        alive = [out for out in self.outputs if shiboken6.isValid(out)]
        self.outputs = alive
        if wanted == self.current:
            return
        self.current = wanted
        for out in alive:
            try:
                out.setDevice(device)
            except RuntimeError:
                pass


def _beep_file() -> str:
    """Un carillon de deux notes, fabrique une fois (wav, dans le dossier
    temporaire)."""
    import math
    import os
    import struct
    import tempfile
    import wave
    path = os.path.join(tempfile.gettempdir(), "prisme-test-son.wav")
    if os.path.exists(path):
        return path
    rate = 44100
    frames = bytearray()
    for freq, length in ((660, 0.25), (880, 0.35)):
        count = int(rate * length)
        for i in range(count):
            fade = min(1.0, i / 800, (count - i) / 2500)
            value = int(0.5 * fade * 32767 * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<hh", value, value)
    with wave.open(path, "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(frames))
    return path


_beeps: list = []


def test_output(device) -> None:
    """Joue un petit carillon sur cette sortie : pour savoir laquelle on
    entend vraiment."""
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QSoundEffect
    effect = QSoundEffect()
    effect.setAudioDevice(device)
    effect.setSource(QUrl.fromLocalFile(_beep_file()))
    effect.setVolume(0.8)
    _beeps[:] = [b for b in _beeps if shiboken6.isValid(b) and b.isPlaying()] + [effect]
    effect.play()


_follower = None
# Le volume de Prisme, de 0 a 1 (`set_volume`). Le curseur de Windows regle
# la sortie par defaut : quand c'est le « mains libres » d'un casque et que
# Prisme joue sur sa stereo, il ne touchait plus les videos -- tout ou rien.
_volume = 1.0


def _linear(volume: float) -> float:
    """Le volume tel qu'on l'entend vers celui de Qt : au carre, proche du
    curseur de Windows (80 % → -4 dB, 50 % → -12 dB). L'echelle
    logarithmique de Qt tombait deja au tiers a 80 %."""
    return volume * volume


def set_volume(volume: float) -> None:
    """Le volume de tous les lecteurs de Prisme (de 0 a 1)."""
    global _volume
    _volume = max(0.0, min(1.0, float(volume)))
    if _follower is None:
        return
    level = _linear(_volume)
    for out in list(_follower.outputs):
        if shiboken6.isValid(out):
            try:
                out.setVolume(level)
            except RuntimeError:
                pass


def follow(output) -> None:
    """Cette sortie son va vers la bonne sortie, et la suit."""
    global _follower
    if _follower is None:
        _follower = _Follower()
    _follower.adopt(output)
    output.setVolume(_linear(_volume))
