"""Reconstruire : retrouver les images decalees d'une video H.264 / H.265.

Bien des videos « en mosaique » n'ont pas perdu leurs images : quelques
octets parasites (une copie ou un telechargement troue) se sont glisses
dans le fichier. L'index de la video (MP4, MOV) range chaque image a une
position et une taille fixes ; un decalage de quelques octets, et toutes les
images suivantes du meme bloc sont lues de travers -- le decodeur ne trouve
plus que du bruit, et la mosaique dure jusqu'a l'image cle suivante.

Les images, elles, sont toujours la, juste a cote. Chacune commence par sa
longueur sur quatre octets puis un en-tete reconnaissable : en suivant cette
chaine dans les octets bruts, on retrouve les vraies limites, on saute les
octets parasites, et l'on reconstruit la piste video avec ses instants
d'origine. Le son et le reste sont recopies tels quels. C'est une vraie
reparation : les images retrouvees sont les images d'origine, pas une
imitation.

Ce qui manque vraiment (des octets ecrases, et non ajoutes) reste perdu : la
methode « Reconstruire » fige ensuite les rares passages encore abimes.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path

from .media import NO_WINDOW, Tools

# Combien de longueurs-en-tete valides a la suite pour croire a une vraie
# image apres des octets parasites : une chaine de trois au hasard est
# invraisemblable.
CHAIN = 3
# Une piste ne se lit qu'en entier en memoire par bloc (« chunk ») : un bloc
# demesure est refuse plutot que de remplir la memoire.
MAX_RUN = 256 * 1024 * 1024


def _ffprobe() -> str:
    if not Tools.ffprobe:
        Tools.resolve()
    if not Tools.ffprobe:
        raise RuntimeError("ffprobe introuvable")
    return Tools.ffprobe


def _ffmpeg() -> str:
    if not Tools.ffmpeg:
        Tools.resolve()
    if not Tools.ffmpeg:
        raise RuntimeError("ffmpeg introuvable")
    return Tools.ffmpeg


# -- le format des unites (NAL) ------------------------------------------------
class _Avc:
    codec_id = "V_MPEG4/ISO/AVC"
    box = b"avcC"
    header = 1

    @staticmethod
    def plausible(data: bytes, at: int) -> bool:
        head = data[at]
        if head & 0x80:
            return False
        kind, ref = head & 0x1F, (head >> 5) & 3
        if kind in (1, 2, 3, 4):
            return True
        if kind == 5:
            return ref != 0
        if kind in (7, 8):
            return ref != 0
        if kind in (6, 9, 10, 11, 12):
            return ref == 0
        return False

    @staticmethod
    def slice_start(data: bytes, at: int) -> bool | None:
        """Une tranche d'image : vrai si elle ouvre une image (premier bloc 0)."""
        if data[at] & 0x1F not in (1, 2, 5):
            return None
        return bool(data[at + 1] & 0x80)

    @staticmethod
    def key(data: bytes, at: int) -> bool:
        return data[at] & 0x1F == 5


class _Hevc:
    codec_id = "V_MPEGH/ISO/HEVC"
    box = b"hvcC"
    header = 2

    @staticmethod
    def plausible(data: bytes, at: int) -> bool:
        head, tail = data[at], data[at + 1]
        if head & 0x80:
            return False
        kind = (head >> 1) & 0x3F
        layer = ((head & 1) << 5) | (tail >> 3)
        temporal = tail & 7
        return layer == 0 and temporal != 0 and (kind <= 21 or 32 <= kind <= 40)

    @staticmethod
    def slice_start(data: bytes, at: int) -> bool | None:
        if (data[at] >> 1) & 0x3F > 21:
            return None
        return bool(data[at + 2] & 0x80)

    @staticmethod
    def key(data: bytes, at: int) -> bool:
        return 16 <= (data[at] >> 1) & 0x3F <= 21


FORMATS = {"h264": _Avc, "hevc": _Hevc}


def _chain(data: bytes, at: int, end: int, fmt) -> list:
    """Les unites qui se suivent proprement a partir de `at` : [(debut, fin)]."""
    found = []
    while at + 4 + fmt.header + 1 <= end:
        size = int.from_bytes(data[at:at + 4], "big")
        if size <= fmt.header or at + 4 + size > end or not fmt.plausible(data, at + 4):
            break
        found.append((at, at + 4 + size))
        at += 4 + size
    return found


def _packet_fits(data: bytes, start: int, size: int, fmt) -> bool:
    units = _chain(data, start, start + size, fmt)
    return bool(units) and units[-1][1] == start + size


def _frames(data: bytes, fmt) -> tuple[list, int]:
    """Les images retrouvees dans ces octets : [(debut, octets, cle)], et le
    nombre d'octets parasites sautes."""
    end = len(data)
    at, units, skipped = 0, [], 0
    while at < end:
        run = _chain(data, at, end, fmt)
        if run and (len(run) >= CHAIN or run[-1][1] == end or not units and at == 0):
            units += run
            at = run[-1][1]
            continue
        # Des octets parasites : on avance jusqu'a une chaine credible.
        probe = at + 1
        while probe < end and len(_chain(data, probe, end, fmt)) < CHAIN:
            probe += 1
        if probe >= end:
            # Plus de chaine de trois : la fin du bloc, une ou deux unites.
            probe = at + 1
            while probe < end and not (_chain(data, probe, end, fmt)
                                       and _chain(data, probe, end, fmt)[-1][1] == end):
                probe += 1
        skipped += probe - at
        at = probe
    frames, pending, current = [], [], None
    for start, stop in units:
        body = start + 4
        opens = fmt.slice_start(data, body)
        if opens is None:
            # Les unites hors image (SEI, delimiteurs, parametres) vont a l'image suivante.
            if current is not None and not pending and _is_suffix(data, body, fmt):
                current[1].append((start, stop))
            else:
                pending.append((start, stop))
            continue
        if opens or current is None:
            current = [start, pending + [(start, stop)], fmt.key(data, body)]
            frames.append(current)
            pending = []
        else:
            current[1].extend(pending + [(start, stop)])
            current[2] = current[2] or fmt.key(data, body)
            pending = []
    out = []
    for start, parts, key in frames:
        out.append((start, b"".join(data[a:b] for a, b in parts), key))
    return out, skipped


def _is_suffix(data: bytes, at: int, fmt) -> bool:
    # Le SEI « suffixe » de H.265 (type 40) suit son image.
    return fmt is _Hevc and (data[at] >> 1) & 0x3F == 40


# Jusqu'ou une image deplacee peut avoir commence avant sa place.
LOOK_BACK = 4096


def _swap_fix(data: bytes, run: list, first: int, fits: list, valid, quick=None,
              deltas=None, seen=None) -> list:
    """Un bloc deplace : le debut d'une image ecrit quelques centaines
    d'octets trop tot, par-dessus la fin de la precedente -- fin qu'on
    retrouve juste apres l'image deplacee (camescopes Sony, MAH*.MP4 : 256
    octets, image et son). Rien n'est perdu : on recompose les deux.

    `valid(data, debut, taille)` : ces octets font-ils un paquet credible ?
    `quick(data, debut)` : tri rapide des debuts possibles. `deltas` : les
    seuls decalages a essayer (le son, sans longueurs, ne se juge que par
    son premier octet : on n'essaie que les decalages vus sur l'image).
    `seen` : un Counter des decalages trouves.

    Rend les octets de chaque paquet du bloc -- None pour un paquet abime
    qui ne s'explique pas ainsi."""
    # Le bloc deplace se remet a sa place dans une copie des octets ; chaque
    # paquet se relit ensuite a sa position : un paquet plus court que le
    # bloc (un silence, 100 octets) est retabli du meme coup.
    buf = bytearray(data)
    unfixed = set()
    for index, ok in enumerate(fits):
        if ok:
            continue
        a, size = int(run[index]["pos"]) - first, int(run[index]["size"])
        if valid(buf, a, size):
            continue                  # retabli en remettant un bloc voisin
        found = None
        for delta in (deltas if deltas is not None else range(1, LOOK_BACK)):
            if delta > a:
                if deltas is None:
                    break
                continue
            start = a - delta
            if quick is not None and not quick(buf, start):
                continue
            if valid(buf, start, size):
                found = delta
                break
        if found is None:
            unfixed.add(index)
            continue
        if seen is not None:
            seen[found] += 1
        # Le bloc a pu etre repousse apres plusieurs paquets : tous ceux qui
        # suivent, decales du meme ecart, font partie de la serie, et le bloc
        # est au bout.
        last = index
        while last + 1 < len(run):
            nxt_at = int(run[last + 1]["pos"]) - first
            nxt_size = int(run[last + 1]["size"])
            if valid(buf, nxt_at, nxt_size) or not valid(buf, nxt_at - found, nxt_size):
                break
            last += 1
        end = int(run[last]["pos"]) - first + int(run[last]["size"])
        buf[a - found:end] = buf[end - found:end] + buf[a - found:end - found]
    out = []
    for index, packet in enumerate(run):
        a, size = int(packet["pos"]) - first, int(packet["size"])
        out.append(None if index in unfixed else bytes(buf[a:a + size]))
    return out


# -- le fichier ----------------------------------------------------------------
def _probe(path: str) -> tuple[dict, list]:
    """(la piste video, les paquets de toutes les pistes). La piste video
    porte aussi la liste des pistes (« _streams »)."""
    command = [_ffprobe(), "-v", "quiet", "-of", "json", "-show_entries",
               "format=format_name:stream=index,codec_type,codec_name,width,height,"
               "time_base,sample_rate,channels:packet=stream_index,pts,dts,pos,size,flags",
               str(path)]
    out = subprocess.run(command, capture_output=True, creationflags=NO_WINDOW,
                         timeout=600).stdout
    info = json.loads(out.decode("utf-8", "replace") or "{}")
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"
                  and s.get("codec_name") in FORMATS), None)
    if video is None:
        raise RuntimeError("seules les vidéos H.264 et H.265 se reconstruisent")
    # Les images rangees a une position et une taille fixes, chacune
    # precedee de sa longueur : MP4, MOV. Un .MTS (AVCHD) decoupe le flux en
    # paquets de 188 octets, sans longueurs : il n'a pas ce defaut-la, et
    # chaque image y passait pour « decalee ».
    if "mp4" not in str(info.get("format", {}).get("format_name", "")):
        raise Unsupported("ce format (.MTS, .AVI…) ne se reconstruit pas")
    video["_streams"] = info.get("streams", [])
    return video, info.get("packets", [])


def _config(path: str, box: bytes) -> bytes:
    """La configuration du decodeur (avcC / hvcC), lue dans l'en-tete."""
    head, at = _find_box(path, box)
    if at < 4:
        raise RuntimeError("configuration du décodeur introuvable")
    length = struct.unpack(">I", head[at - 4:at])[0]
    return head[at + 4:at - 4 + length]


def _find_box(path: str, box: bytes) -> tuple[bytes, int]:
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        # L'en-tete (moov) est au debut ou a la fin : on lit les deux bouts.
        head = handle.read(min(size, 64 * 1024 * 1024))
        at = head.find(box)
        if at < 4 and size > len(head):
            handle.seek(max(0, size - 64 * 1024 * 1024))
            head = handle.read()
            at = head.find(box)
    return head, at


def _aac_config(path: str) -> bytes:
    """L'AudioSpecificConfig du son AAC, dans la boite esds."""
    head, at = _find_box(path, b"esds")
    if at < 4:
        return b""
    at += 8                                   # 'esds' + version/drapeaux

    def descriptor(at):
        tag = head[at]
        at += 1
        length = 0
        for _ in range(4):
            byte = head[at]
            at += 1
            length = (length << 7) | (byte & 0x7F)
            if not byte & 0x80:
                break
        return tag, at, length

    tag, at, _length = descriptor(at)
    if tag != 0x03:
        return b""
    flags = head[at + 2]
    at += 3
    if flags & 0x80:
        at += 2
    if flags & 0x40:
        at += 1 + head[at]
    if flags & 0x20:
        at += 2
    tag, at, _length = descriptor(at)
    if tag != 0x04:
        return b""
    at += 13
    tag, at, length = descriptor(at)
    return head[at:at + length] if tag == 0x05 else b""


class Unsupported(RuntimeError):
    """Un conteneur sans images rangees par l'index : rien a recaler."""


def analyse(path) -> dict:
    """Combien d'images sont lues de travers, sans rien ecrire."""
    try:
        video, packets = _probe(str(path))
    except Unsupported:
        return {"packets": 0, "bad": 0, "unsupported": True}
    fmt = FORMATS[video["codec_name"]]
    mine = [p for p in packets if p.get("stream_index") == video["index"]
            and p.get("pos") not in (None, "N/A")]
    bad = 0
    # Un grand tampon : les paquets se suivent, chaque saut reste dedans --
    # pas un aller-retour au NAS par image.
    with open(path, "rb", buffering=4 * 1024 * 1024) as handle:
        for packet in mine:
            handle.seek(int(packet["pos"]))
            size = int(packet["size"])
            if not _packet_fits(handle.read(size), 0, size, fmt):
                bad += 1
    return {"packets": len(mine), "bad": bad}


def _runs(packets: list) -> list:
    """Les blocs : des paquets d'une piste qui se suivent dans le fichier."""
    runs, current = [], [packets[0]]
    for packet in packets[1:]:
        last = current[-1]
        if int(packet["pos"]) == int(last["pos"]) + int(last["size"]):
            current.append(packet)
        else:
            runs.append(current)
            current = [packet]
    runs.append(current)
    return runs


def _lead_byte(handle, packets: list) -> int | None:
    """Le premier octet commun a presque tous les paquets de son (AAC : 0x20,
    0x21…), s'il y en a un : c'est a lui qu'on reconnait un paquet a sa place."""
    from collections import Counter
    sample = packets[:: max(1, len(packets) // 600)]
    counts = Counter()
    for packet in sample:
        handle.seek(int(packet["pos"]))
        counts[handle.read(1)[:1]] += 1
    if not counts:
        return None
    byte, number = counts.most_common(1)[0]
    return byte[0] if byte and number >= 0.85 * len(sample) else None


def rebuild(path, target, progress=None, stop=None) -> dict:
    """Ecrit `target` (le conteneur de l'original) avec l'image -- et le son
    AAC -- recales. Rend {"packets", "bad", "recovered", "lost", "skipped",
    "audio_bad", "audio_recovered"}."""
    import bisect
    from collections import Counter
    path, target = str(path), str(target)
    video, packets = _probe(path)
    fmt = FORMATS[video["codec_name"]]
    located = [p for p in packets if p.get("pos") not in (None, "N/A")]
    mine = [p for p in located if p.get("stream_index") == video["index"]]
    if not mine:
        raise RuntimeError("aucune image localisable dans ce fichier")
    starts = sorted(int(p["pos"]) for p in located)
    file_size = os.path.getsize(path)
    streams = {s["index"]: s for s in video.get("_streams", [])}
    audio = next((s for s in streams.values() if s.get("codec_type") == "audio"
                  and s.get("codec_name") == "aac"), None)
    theirs = ([p for p in located if p.get("stream_index") == audio["index"]]
              if audio is not None else [])

    def clock(stream):
        num, den = (int(x) for x in str(stream.get("time_base", "1/1000")).split("/"))
        return num / den

    def stamp(packet, unit, field="pts") -> float:
        value = packet.get(field)
        if value in (None, "N/A"):
            value = packet.get("dts" if field == "pts" else "pts")
        return int(value) * unit if value not in (None, "N/A") else 0.0

    stats = {"packets": len(mine), "bad": 0, "recovered": 0, "lost": 0, "skipped": 0,
             "audio_bad": 0, "audio_recovered": 0}
    seen = Counter()
    blocks = []                   # (instant, octets, cle, piste, ordre)
    done = [0]
    total = max(1, len(mine) + len(theirs))

    def read_run(handle, run):
        first = int(run[0]["pos"])
        nominal_end = int(run[-1]["pos"]) + int(run[-1]["size"])
        # Jusqu'au debut du paquet suivant, quelle que soit sa piste : des
        # octets ajoutes repoussent la fin du dernier paquet au-dela.
        following = bisect.bisect_right(starts, nominal_end - 1)
        end = starts[following] if following < len(starts) else file_size
        end = max(nominal_end, end)
        if end - first > MAX_RUN:
            end = nominal_end
        # Un peu avant le bloc : un paquet deplace peut commencer avant lui.
        lead = min(first, LOOK_BACK)
        handle.seek(first - lead)
        return handle.read(end - first + lead), first - lead

    def tick(count):
        done[0] += count
        if stop is not None and stop():
            raise RuntimeError("arrêté")
        if progress is not None:
            progress(0.8 * done[0] / total)

    with open(path, "rb", buffering=4 * 1024 * 1024) as handle:
        # L'image d'abord : ses decalages, surs, guident ceux du son.
        unit = clock(video)
        valid = lambda data, at, size: _packet_fits(data, at, size, fmt)   # noqa: E731
        quick = (lambda data, at: int.from_bytes(data[at:at + 4], "big") > fmt.header  # noqa: E731
                 and fmt.plausible(data, at + 4))
        # Le decalage propre a l'appareil : celui de presque tous les paquets
        # deplaces (256 octets chez Sony). Sans decalage dominant, ce sont
        # des octets inseres ou perdus : on relit la chaine des unites.
        votes = Counter()
        for run in _runs(mine):
            data, first = read_run(handle, run)
            for packet in run:
                at, size = int(packet["pos"]) - first, int(packet["size"])
                if valid(data, at, size):
                    continue
                for delta in range(1, min(LOOK_BACK, at) + 1):
                    if quick(data, at - delta) and valid(data, at - delta, size):
                        votes[delta] += 1
                        break
                else:
                    votes[None] += 1
        common = [(d, n) for d, n in votes.most_common() if d is not None]
        camera = ([common[0][0]] if common and common[0][1] >= 3
                  and common[0][1] >= 0.6 * sum(votes.values()) else None)
        for run in _runs(mine):
            tick(len(run))
            data, first = read_run(handle, run)
            fits = [valid(data, int(p["pos"]) - first, int(p["size"])) for p in run]

            def keep(packet, body):
                blocks.append((stamp(packet, unit), body,
                               "K" in str(packet.get("flags", "")), 1,
                               stamp(packet, unit, "dts")))
            if all(fits):
                for packet in run:
                    a = int(packet["pos"]) - first
                    keep(packet, data[a:a + int(packet["size"])])
                continue
            stats["bad"] += fits.count(False)
            fixed = (_swap_fix(data, run, first, fits, valid, quick, deltas=camera,
                               seen=seen) if camera else None)
            if fixed is not None and sum(1 for f in fixed if f is None) * 2 < fits.count(False):
                # Le bloc s'explique par des images deplacees : celles qui ne
                # se recomposent pas sont laissees de cote (un trou de 40 ms),
                # plutot que de tout relire autrement.
                for index, packet in enumerate(run):
                    if fixed[index] is None:
                        continue
                    keep(packet, fixed[index])
                    if not fits[index]:
                        stats["recovered"] += 1
                continue
            frames, skipped = _frames(data, fmt)
            stats["skipped"] += skipped
            # Une image retrouvee prend l'instant du paquet a sa place exacte
            # s'il y en a un, sinon celui qui suit le dernier attribue.
            nominal = {int(p["pos"]) - first: i for i, p in enumerate(run)}
            used = -1
            for start, body, key in frames:
                slot = nominal.get(start)
                if slot is None or slot <= used:
                    slot = used + 1
                if slot >= len(run):
                    break
                used = slot
                blocks.append((stamp(run[slot], unit), body, key, 1,
                               stamp(run[slot], unit, "dts")))
                if not fits[slot]:
                    stats["recovered"] += 1
        stats["lost"] = max(0, stats["packets"]
                            - sum(1 for b in blocks if b[3] == 1))

        # Le son AAC, s'il se reconnait a son premier octet et que l'image a
        # montre de quel decalage il s'agit.
        lead = _lead_byte(handle, theirs) if theirs else None
        asc = _aac_config(path) if lead is not None else b""
        with_audio = bool(lead is not None and asc and seen)
        if with_audio:
            unit = clock(audio)
            deltas = [delta for delta, _n in seen.most_common(3)]
            valid_a = lambda data, at, size: data[at] == lead   # noqa: E731
            for run in _runs(theirs):
                tick(len(run))
                data, first = read_run(handle, run)
                fits = [valid_a(data, int(p["pos"]) - first, int(p["size"])) for p in run]
                fixed = (_swap_fix(data, run, first, fits, valid_a, deltas=deltas)
                         if not all(fits) else None)
                stats["audio_bad"] += fits.count(False)
                for index, packet in enumerate(run):
                    a = int(packet["pos"]) - first
                    body = data[a:a + int(packet["size"])]
                    if fixed is not None and fixed[index] is not None:
                        if not fits[index]:
                            stats["audio_recovered"] += 1
                        body = fixed[index]
                    t = stamp(packet, unit)
                    blocks.append((t, body, True, 2, t))

    config = _config(path, fmt.box)
    tracks = [{"number": 1, "type": 1, "codec": fmt.codec_id, "private": config,
               "width": int(video.get("width") or 0),
               "height": int(video.get("height") or 0)}]
    if with_audio:
        tracks.append({"number": 2, "type": 2, "codec": "A_AAC", "private": asc,
                       "rate": float(audio.get("sample_rate") or 48000),
                       "channels": int(audio.get("channels") or 2)})
    # Dans l'ordre de decodage de chaque piste, entrelacees par l'instant.
    blocks.sort(key=lambda b: b[4])
    with tempfile.TemporaryDirectory(prefix="prisme-reconstruire-") as folder:
        rebuilt = os.path.join(folder, "video.mkv")
        _write_mkv(rebuilt, tracks, blocks)
        if progress is not None:
            progress(0.85)
        command = [_ffmpeg(), "-hide_banner", "-nostdin", "-y", "-v", "error",
                   "-i", rebuilt, "-i", path, "-map", "0:v:0"]
        if with_audio:
            command += ["-map", "0:a:0"]
        command += ["-map", "1", "-map", "-1:v"]
        if with_audio:
            command += ["-map", "-1:a"]
        command += ["-c", "copy", "-map_metadata", "1"]
        if target.lower().endswith((".mp4", ".m4v", ".mov")):
            command += ["-movflags", "+faststart"]
        result = subprocess.run(command + [target], capture_output=True,
                                creationflags=NO_WINDOW, timeout=3600)
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError((message[-1] if message else "ffmpeg a échoué")[:200])
    if progress is not None:
        progress(1.0)
    stats["swapped"] = sum(seen.values())
    return stats


# -- Matroska, juste ce qu'il faut ---------------------------------------------
def _size(n: int) -> bytes:
    return bytes([0x01]) + n.to_bytes(7, "big")


def _el(ident: int, body: bytes) -> bytes:
    raw = ident.to_bytes((ident.bit_length() + 7) // 8, "big")
    return raw + _size(len(body)) + body


def _uint(ident: int, value: int) -> bytes:
    return _el(ident, value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big"))


# Un dix-millieme de seconde par tic : au millieme, les 21,33 ms d'un paquet
# AAC s'arrondissaient, et le son tremblait d'un echantillon a l'autre.
TICK_NS = 100_000


def _write_mkv(path: str, tracks, blocks: list, *legacy) -> None:
    """`tracks` : [{"number", "type" (1 image, 2 son), "codec", "private",
    "width"/"height" ou "rate"/"channels"}] ; `blocks` : (instant s, octets,
    cle, piste[, ordre]).

    Ancienne forme, une seule piste video : _write_mkv(path, codec_id,
    config, largeur, hauteur, [(instant, octets, cle)])."""
    if isinstance(tracks, str):
        codec_id, config, width, height, old = tracks, blocks, legacy[0], legacy[1], legacy[2]
        tracks = [{"number": 1, "type": 1, "codec": codec_id, "private": config,
                   "width": width, "height": height}]
        blocks = [(t, b, k, 1) for t, b, k in old]
    header = _el(0x1A45DFA3, _uint(0x4286, 1) + _uint(0x42F7, 1) + _uint(0x42F2, 4)
                 + _uint(0x42F3, 8) + _el(0x4282, b"matroska") + _uint(0x4287, 4)
                 + _uint(0x4285, 2))
    last = max((b[0] for b in blocks), default=0.0)
    info = _el(0x1549A966, _uint(0x2AD7B1, TICK_NS) + _el(0x4D80, b"Prisme")
               + _el(0x5741, b"Prisme")
               + _el(0x4489, struct.pack(">d", last * 1e9 / TICK_NS)))
    entries = b""
    for track in tracks:
        body = (_uint(0xD7, track["number"]) + _uint(0x73C5, track["number"])
                + _uint(0x83, track["type"]) + _uint(0x9C, 0)
                + _el(0x86, track["codec"].encode()))
        if track.get("private"):
            body += _el(0x63A2, track["private"])
        if track["type"] == 1:
            body += _el(0xE0, _uint(0xB0, track["width"]) + _uint(0xBA, track["height"]))
        else:
            body += _el(0xE1, _el(0xB5, struct.pack(">d", track["rate"]))
                        + _uint(0x9F, track["channels"]))
        entries += _el(0xAE, body)
    with open(path, "wb") as out:
        out.write(header)
        out.write(bytes.fromhex("18538067") + bytes.fromhex("01FFFFFFFFFFFFFF"))
        out.write(info + _el(0x1654AE6B, entries))
        cluster, base, size = [], None, 0
        for block in blocks:
            moment, body, key, number = block[0], block[1], block[2], block[3]
            tick = int(round(moment * 1e9 / TICK_NS))
            if (base is None or size > 4 * 1024 * 1024
                    or not -32000 < tick - base < 32000):
                if cluster:
                    out.write(_el(0x1F43B675, b"".join(cluster)))
                base = max(0, tick)
                cluster, size = [_uint(0xE7, base)], 0
            simple = (bytes([0x80 | number]) + struct.pack(">h", tick - base)
                      + bytes([0x80 if key else 0x00]) + body)
            cluster.append(_el(0xA3, simple))
            size += len(simple)
        if cluster:
            out.write(_el(0x1F43B675, b"".join(cluster)))
