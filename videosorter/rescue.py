"""Reconstituer une video MP4 sans se fier a son index : l'en-tete manque
(fichier coupe net, carte arrachee), ou ses tables sont fausses.

On s'appuie sur une video saine du meme appareil (la « reference ») : elle
donne la configuration du decodeur (image et son), la cadence, et la facon
dont l'appareil range les donnees -- un bloc de N images, puis un bloc de
son couvrant la meme duree, et ainsi de suite (camescopes Sony : 12 images,
0,48 s, puis 22 ou 23 paquets AAC).

Le fichier abime est lu d'un bout a l'autre : les images se reconnaissent a
leur chaine d'unites (longueur + en-tete, la premiere etant un delimiteur),
et ce qui separe deux blocs d'images est le son. Chaque image et chaque bloc
de son prend l'instant que lui donne sa place dans cette alternance : une
image perdue laisse un trou de 40 ms, pas un decalage du son. Les blocs de
256 octets deplaces par l'appareil (voir resync) sont remis en place au
passage.

Le son est decode puis reencode (AAC) : ses paquets n'ont pas de longueur,
on ne peut pas les recopier un a un sans table. Les trous sont combles par
du silence, la synchronisation tient.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections import Counter

from .media import NO_WINDOW, Tools
from . import resync

AUD = b"\x00\x00\x00\x02\x09"


def _reference(path: str) -> dict:
    """Ce que la video saine apprend : configurations, cadence, rangement."""
    video, packets = resync._probe(path)
    streams = {s["index"]: s for s in video.get("_streams", [])}
    audio = next((s for s in streams.values() if s.get("codec_type") == "audio"), None)
    located = sorted((p for p in packets if p.get("pos") not in (None, "N/A")),
                     key=lambda p: int(p["pos"]))
    runs, last = [], None
    for packet in located:
        kind = "v" if packet["stream_index"] == video["index"] else "a"
        if runs and runs[-1][0] == kind and int(packet["pos"]) == last:
            runs[-1][1] += 1
        else:
            runs.append([kind, 1])
        last = int(packet["pos"]) + int(packet["size"])
    per_chunk = Counter(n for kind, n in runs if kind == "v").most_common(1)[0][0]
    biggest = max(int(p["size"]) for p in located if p["stream_index"] == video["index"])
    rate = video.get("avg_frame_rate") or "25/1"
    command = [Tools.ffprobe, "-v", "quiet", "-of", "json", "-select_streams", "v:0",
               "-show_entries", "stream=avg_frame_rate", path]
    out = subprocess.run(command, capture_output=True, creationflags=NO_WINDOW).stdout
    rate = (json.loads(out or b"{}").get("streams") or [{}])[0].get("avg_frame_rate", rate)
    num, den = (int(x) for x in rate.split("/"))
    lead = None
    mine = [p for p in located if audio is not None and p["stream_index"] == audio["index"]]
    if mine:
        with open(path, "rb") as handle:
            lead = resync._lead_byte(handle, mine)
    return {
        "max_frame": 3 * biggest,
        "lead": lead,
        "fmt": resync.FORMATS[video["codec_name"]],
        "config": resync._config(path, resync.FORMATS[video["codec_name"]].box),
        "asc": resync._aac_config(path) if audio is not None else b"",
        "fps": num / den,
        "per_chunk": per_chunk,
        "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
        "sample_rate": int((audio or {}).get("sample_rate") or 48000),
        "channels": int((audio or {}).get("channels") or 2),
    }


# Taille maximale d'une image : quelques fois la plus grosse de la reference.
# Une longueur abimee pretendait sinon couvrir 22 Mo, et l'image avalait des
# dizaines de blocs d'images et de son.
MAX_FRAME = [4 * 1024 * 1024]


def _frame_at(image, at: int, fmt) -> int | None:
    """Fin de l'image (unite d'acces) qui commence a `at` par un delimiteur,
    ou None si rien de credible ne commence la."""
    if image[at:at + 5] != AUD:
        return None
    units = resync._chain(image, at, min(len(image), at + MAX_FRAME[0]), fmt)
    if len(units) < 2:
        return None
    end = units[0][1]
    for start, stop in units[1:]:
        if image[start + 4] & 0x1F == 9:          # le delimiteur suivant
            break
        end = stop
    # Une image se termine par sa tranche ; sans tranche, ce n'en est pas une.
    kinds = {image[s + 4] & 0x1F for s, _e in units if s < end}
    return end if kinds & {1, 5} else None


def _next_frame(image, at: int, fmt) -> int:
    """Le prochain debut d'image credible a partir de `at` (ou len)."""
    while True:
        at = image.find(AUD, at)
        if at < 0:
            return len(image)
        if _frame_at(image, at, fmt) is not None:
            return at
        at += 1


def scan(path: str, ref: dict, shift: int = 256, progress=None, stop=None) -> dict:
    """Parcourt le fichier : {"video": [(index, octets, cle)], "audio": [(bloc,
    octets)], "chunks", "fixed", "dropped"}."""
    fmt = ref["fmt"]
    MAX_FRAME[0] = ref.get("max_frame", MAX_FRAME[0])
    with open(path, "rb") as handle:
        image = bytearray(handle.read())
    # Fin des donnees : l'en-tete (moov), s'il est en fin de fichier.
    tail = image.rfind(b"moov")
    limit = tail - 4 if tail > len(image) - 16 * 1024 * 1024 else len(image)
    del image[limit:]
    at = _next_frame(image, 0, fmt)
    video, audio = [], []
    chunk = -1
    fixed = dropped = 0
    resume = [0]
    while at < len(image):
        if stop is not None and stop():
            raise RuntimeError("arrêté")
        if progress is not None:
            progress(at / len(image))
        # Un bloc d'images (ou la suite d'un bloc coupe par une image cassee).
        chunk += 1
        index, resume[0] = resume[0], 0
        while True:
            end = _frame_at(image, at, fmt)
            if end is None:
                break
            skip = 0
            if _frame_at(image, end, fmt) is None and end < len(image):
                # Rien a la place attendue : l'image suivante a-t-elle ete
                # ecrite `shift` octets trop tot, par-dessus la fin de celle-ci ?
                early = end - shift
                if early > at and _frame_at(image, early, fmt) is not None:
                    q = early
                    while True:
                        stop_at = _frame_at(image, q, fmt)
                        if stop_at is None:
                            break
                        q = stop_at
                        if _frame_at(image, q, fmt) is None:
                            break
                    if q + shift <= len(image) and q > early:
                        # La serie deplacee va de `early` a `q` ; le bloc
                        # repousse est juste apres. On le remet en place.
                        block = image[q:q + shift]
                        image[early:q + shift] = block + image[early:q]
                        fixed += 1
                        end = _frame_at(image, at, fmt) or end
                elif (_frame_at(image, end + shift, fmt) is not None
                      and audio and audio[-1][0] == chunk - 1):
                    # Le bloc a commence par une image ecrite trop tot, dans
                    # la fin du son : le morceau de son repousse est ici, au
                    # bout de la serie. Il retourne a son bloc de son.
                    audio[-1] = (chunk - 1, audio[-1][1] + bytes(image[end:end + shift]))
                    skip = shift
                    fixed += 1
            if index >= ref["per_chunk"]:
                # Un bloc plein suivi d'images sans son entre deux : le son de
                # ce bloc est perdu. Le bloc suivant commence, a sa place.
                chunk += 1
                index = 0
            body = bytes(image[at:end])
            key = any(image[s + 4] & 0x1F == 5 for s, _e in
                      resync._chain(image, at, end, fmt))
            video.append((chunk, index, body, key))
            index += 1
            at = end + skip
        if index == 0 or (resume[0] == 0 and video and video[-1][0] != chunk):
            # Pas d'image ici : des octets perdus, on cherche la suite.
            chunk -= 1
            nxt = _next_frame(image, at + 1, fmt)
            dropped += nxt - at
            at = nxt
            continue
        # Le son, jusqu'au prochain bloc d'images.
        nxt = _next_frame(image, at, fmt)
        lead = ref.get("lead")
        if (index < ref["per_chunk"] and lead is not None and nxt < len(image)
                and nxt > at and image[at] != lead):
            # Le bloc n'est pas fini et ce qui suit n'est pas du son : une
            # image a la chaine cassee (octets abimes). Elle est perdue, le
            # bloc continue apres elle -- sans quoi tout ce qui suit
            # glissait de 0,48 s.
            gap = nxt - at
            per = max(1, len(video[-1][2]) if video else gap)
            lost = max(1, min(ref["per_chunk"] - index, round(gap / per)))
            dropped += gap
            at = nxt
            chunk -= 1
            resume[0] = index + lost
            continue
        if nxt > at:
            audio.append((chunk, bytes(image[at:nxt])))
        at = nxt
    return {"video": video, "audio": audio, "chunks": chunk + 1, "fixed": fixed,
            "dropped": dropped}


def rebuild(path, reference, target, progress=None, stop=None) -> dict:
    """Reconstitue `target` (MP4) a partir des seuls octets de `path`."""
    if not Tools.ffmpeg:
        Tools.resolve()
    path, reference, target = str(path), str(reference), str(target)
    ref = _reference(reference)
    found = scan(path, ref, progress=(lambda f: progress(0.6 * f)) if progress else None,
                 stop=stop)
    period = ref["per_chunk"] / ref["fps"]
    frame = 1024 / ref["sample_rate"]
    blocks = []
    for chunk, index, body, key in found["video"]:
        moment = chunk * period + index / ref["fps"]
        blocks.append((moment, body, key, 1, moment))
    blocks.sort(key=lambda b: b[4])
    tracks = [{"number": 1, "type": 1, "codec": ref["fmt"].codec_id,
               "private": ref["config"], "width": ref["width"], "height": ref["height"]}]
    with tempfile.TemporaryDirectory(prefix="prisme-reconstituer-") as folder:
        rebuilt = os.path.join(folder, "brut.mkv")
        resync._write_mkv(rebuilt, tracks, blocks)
        if progress is not None:
            progress(0.65)
        command = [Tools.ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error",
                   "-i", rebuilt]
        sound = os.path.join(folder, "son.raw")
        if ref["asc"] and found["audio"]:
            _lay_sound(found["audio"], ref, period, found["chunks"], sound, folder, stop)
            command += ["-f", "s16le", "-ar", str(ref["sample_rate"]),
                        "-ac", str(ref["channels"]), "-i", sound]
        command += ["-map", "0:v:0", "-c:v", "copy"]
        if os.path.exists(sound):
            command += ["-map", "1:a:0", "-c:a", "aac", "-b:a", "256k"]
        command += ["-movflags", "+faststart", target]
        result = subprocess.run(command, capture_output=True, creationflags=NO_WINDOW,
                                timeout=7200)
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError((message[-1] if message else "ffmpeg a échoué")[:200])
    if progress is not None:
        progress(1.0)
    return {"frames": len(found["video"]), "chunks": found["chunks"],
            "audio_chunks": len(found["audio"]), "fixed": found["fixed"],
            "dropped_bytes": found["dropped"],
            "seconds": round(found["chunks"] * period, 2)}


def _decode_chunk(body: bytes, ref: dict, folder: str, number: int) -> bytes:
    """Un bloc de son (plusieurs paquets AAC colles) -> PCM 16 bits."""
    path = os.path.join(folder, f"son{number}.mkv")
    resync._write_mkv(path, [{"number": 1, "type": 2, "codec": "A_AAC",
                              "private": ref["asc"], "rate": float(ref["sample_rate"]),
                              "channels": ref["channels"]}], [(0.0, body, True, 1)])
    result = subprocess.run([Tools.ffmpeg, "-hide_banner", "-nostdin", "-v", "quiet",
                             "-i", path, "-f", "s16le", "-ar", str(ref["sample_rate"]),
                             "-ac", str(ref["channels"]), "-"],
                            capture_output=True, creationflags=NO_WINDOW, timeout=120)
    os.remove(path)
    return result.stdout


def _lay_sound(chunks: list, ref: dict, period: float, count: int, out: str,
               folder: str, stop=None) -> None:
    """Chaque bloc de son, decode a part, pose a sa place sur la ligne de
    temps : coupe s'il deborde (octets parasites decodes comme du son),
    complete de silence s'il manque. Laisser ffmpeg recaler le tout faisait
    des paquets d'un echantillon et un son haché."""
    from concurrent.futures import ThreadPoolExecutor
    rate, channels = ref["sample_rate"], ref["channels"]
    width = 2 * channels
    frame = 1024
    total = int(round(count * period * rate))
    timeline = bytearray(total * width)

    def place(item):
        number, (chunk, body) = item
        if stop is not None and stop():
            return
        pcm = _decode_chunk(body, ref, folder, number)
        start = int(chunk * period * rate / frame) * frame
        end = min(total, int((chunk + 1) * period * rate / frame) * frame)
        if chunk + 1 >= count:
            end = total
        room = max(0, end - start) * width
        piece = pcm[:min(room, len(pcm) // width * width)]
        timeline[start * width:start * width + len(piece)] = piece

    with ThreadPoolExecutor(3) as pool:
        list(pool.map(place, enumerate(chunks)))
    if stop is not None and stop():
        raise RuntimeError("arrêté")
    with open(out, "wb") as handle:
        handle.write(timeline)
