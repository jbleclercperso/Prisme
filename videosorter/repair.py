"""Les videos abimees : les pavés de couleur, la mosaique, les images figees.

Ces degats viennent de donnees perdues dans le fichier (telechargement troue,
secteur defectueux, copie interrompue). Rien ne peut faire revenir ce qui
manque -- les outils « IA » de 2026 restent des prototypes de laboratoire,
sur de petits extraits et de gros GPU. Ce qui marche, et que fait ce module :

1. **Diagnostiquer** : la video est decodee en entier ; chaque erreur du
   decodeur est datee, avec les images cles autour (`diagnose`).
2. **Couper les passages abimes**, sans reencoder : chaque degat s'etend de
   l'image cle qui le precede a la suivante (le decodeur repart propre a
   chaque image cle). On garde tout le reste, copie tel quel, sans perte
   (`cut_damage`). La video saute quelques secondes a chaque degat, mais plus
   aucun pavé ne s'incruste.
3. **Masquer** les degats en reencodant : le decodeur remplit les blocs perdus
   avec le mouvement des images voisines (« dissimulation d'erreurs »). Rien
   ne saute, mais une zone peut rester floue ou trainer (`conceal`).
4. **Recoller l'enveloppe** : un fichier qui refuse de s'ouvrir ou dont la
   duree est fausse a souvent un index abime, refait par une copie (`remux`).

Avant tout cela, `resync.rebuild` (« Reconstruire ») : quand les images ne
sont que decalees par des octets parasites, il les retrouve -- les images
d'origine, sans perte.

Rien n'ecrase l'original : la reparation est un nouveau fichier a cote.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .media import NO_WINDOW, Tools

# Ce que le decodeur dit quand il tombe sur un degat.
DAMAGE = re.compile(r"error while decoding|concealing|corrupt|decode_slice_header error"
                    r"|non-existing PPS|no frame!|missing picture|Invalid NAL"
                    r"|out of range intra|left block unavailable|top block unavailable"
                    r"|cabac decode|mb_type .* in [IPB] slice too large|ac-tex damaged"
                    r"|Error splitting the input|Packet corrupt"
                    # HEVC, et les vieux AVI en MPEG-4 :
                    r"|Could not find ref with POC|Error constructing the frame RPS"
                    r"|PPS id out of range|Error at MB|damaged|illegal (?:dc|ac)", re.I)
SHOWINFO = re.compile(r"Parsed_showinfo.*?pts_time:\s*([0-9.]+).*?iskey:(\d)")
# L'avance du decodeur sur l'image montree (images B reordonnees), en secondes.
REORDER_MARGIN = 0.5
DURATION = re.compile(r"Duration:\s*(\d+):(\d+):([0-9.]+)")
FPS = re.compile(r"Video:.*?(\d+(?:\.\d+)?) fps")


def _ffmpeg() -> str:
    if not Tools.ffmpeg:
        Tools.resolve()
    if not Tools.ffmpeg:
        raise RuntimeError("ffmpeg introuvable")
    return Tools.ffmpeg


def diagnose(path, progress=None, stop=None) -> dict:
    """Decode toute la video et rend {"duration", "keys", "errors"} : les
    instants (secondes) des images cles et des degats. `progress(fraction)`
    suit l'avancement ; `stop()` vrai l'interrompt."""
    command = [_ffmpeg(), "-hide_banner", "-nostdin", "-v", "info", "-threads", "0",
               "-i", str(path), "-map", "0:v:0", "-vf", "showinfo", "-f", "null", "-"]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               creationflags=NO_WINDOW, text=True, encoding="utf-8",
                               errors="replace")
    keys, errors, duration, last, fps = [], [], 0.0, 0.0, 0.0
    pending = 0                       # des degats dont on attend l'image suivante
    messages = []
    try:
        for line in process.stderr:
            if stop is not None and stop():
                process.kill()
                break
            match = SHOWINFO.search(line)
            if match:
                last = float(match.group(1))
                if match.group(2) == "1":
                    keys.append(last)
                if pending:
                    errors.extend([last] * pending)
                    pending = 0
                if progress is not None and duration:
                    progress(min(1.0, last / duration))
                continue
            if not fps:
                rate = FPS.search(line)
                if rate:
                    fps = float(rate.group(1))
            if not duration:
                found = DURATION.search(line)
                if found:
                    hours, minutes, seconds = found.groups()
                    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            # Seulement les messages du decodeur (« [h264 @ …] ») : le nom du
            # fichier, repete par ffmpeg, peut contenir « corrupt ».
            if (line.lstrip().startswith("[") and "Parsed_" not in line
                    and DAMAGE.search(line)):
                pending += 1
                if len(messages) < 12:
                    messages.append(line.strip())
    finally:
        process.wait()
    if pending:
        errors.extend([last] * pending)
    return {"duration": duration or last, "keys": sorted(set(keys)), "fps": fps,
            "errors": sorted(set(round(t, 3) for t in errors)), "messages": messages}


def damaged_spans(report: dict) -> list:
    """Les passages a retirer : de l'image cle avant chaque degat a la
    suivante, fusionnes."""
    keys = report["keys"] or [0.0]
    end = report["duration"]
    spans = []
    for moment in report["errors"]:
        before = max((k for k in keys if k <= moment + 0.001), default=0.0)
        # Le decodeur date l'erreur dans l'ordre ou il decode, un peu avant
        # l'image qu'on verra abimee : une image cle juste apres peut etre
        # touchee elle-meme. On va jusqu'a la suivante.
        after = min((k for k in keys if k > moment + REORDER_MARGIN), default=end)
        if spans and before <= spans[-1][1] + 0.001:
            spans[-1] = (spans[-1][0], max(spans[-1][1], after))
        else:
            spans.append((before, after))
    return spans


def summary(report: dict) -> str:
    spans = damaged_spans(report)
    lost = sum(b - a for a, b in spans)
    if not report["errors"]:
        return "aucun dégât trouvé"
    return (f"{len(spans)} passage{'s' if len(spans) > 1 else ''} abîmé"
            f"{'s' if len(spans) > 1 else ''}, {lost:.0f} s sur "
            f"{report['duration']:.0f} s")


def repaired_name(path, how: str) -> Path:
    path = Path(path)
    suffix = path.suffix.lower() if path.suffix.lower() in (".mp4", ".mkv", ".mov",
                                                            ".m4v") else ".mkv"
    if how == "conceal":
        suffix = ".mp4"
    target = path.with_name(f"{path.stem} (réparée){suffix}")
    count = 2
    while target.exists():
        target = path.with_name(f"{path.stem} (réparée {count}){suffix}")
        count += 1
    return target


def _run(command: list, progress=None, duration: float = 0.0, stop=None) -> None:
    # -max_error_rate 1 : une video tres abimee (le quart de ses images en
    # erreur) faisait rendre a ffmpeg un code d'echec, son travail pourtant fait.
    command = command[:1] + ["-hide_banner", "-nostdin", "-y", "-progress", "pipe:1",
                             "-v", "error", "-max_error_rate", "1"] + command[1:]
    # Les messages du decodeur dans un fichier, pas dans un tuyau : une video
    # tres abimee en ecrit des centaines, le tuyau plein bloquait ffmpeg, et
    # la reparation ne finissait jamais.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log,
                                   creationflags=NO_WINDOW, text=True, encoding="utf-8",
                                   errors="replace")
        for line in process.stdout:
            if stop is not None and stop():
                process.kill()
                break
            if progress is not None and duration and line.startswith("out_time_ms="):
                try:
                    progress(min(1.0, int(line.split("=")[1]) / 1e6 / duration))
                except ValueError:
                    pass
        process.wait()
        log.seek(0)
        error = log.read()[-4000:]
    if stop is not None and stop():
        raise RuntimeError("arrêté")
    if process.returncode != 0:
        raise RuntimeError((error or "ffmpeg a échoué").strip().splitlines()[-1][:200])


def cut_damage(path, report: dict, target=None, progress=None, stop=None) -> Path:
    """Garde tout sauf les passages abimes, copie sans reencoder."""
    spans = damaged_spans(report)
    end = report["duration"]
    keep, at = [], 0.0
    for start, stop_at in spans:
        if start - at > 0.2:
            keep.append((at, start))
        at = stop_at
    if end - at > 0.2:
        keep.append((at, end))
    if not keep:
        raise RuntimeError("tout est abîmé : rien à garder")
    target = Path(target) if target else repaired_name(path, "cut")
    listing = Path(tempfile.gettempdir()) / f"prisme-reparation-{os.getpid()}.txt"
    source = str(Path(path)).replace("'", "'\\''")
    lines = ["ffconcat version 1.0"]
    for start, finish in keep:
        lines += [f"file '{source}'", f"inpoint {start:.3f}", f"outpoint {finish:.3f}"]
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        _run([_ffmpeg(), "-f", "concat", "-safe", "0", "-i", str(listing),
              "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
              "-avoid_negative_ts", "make_zero", str(target)],
             progress, sum(b - a for a, b in keep), stop)
    finally:
        try:
            listing.unlink()
        except OSError:
            pass
    return target


def conceal(path, report: dict, target=None, progress=None, stop=None) -> Path:
    """Reencode toute la video, les blocs perdus remplis d'apres le
    mouvement des images voisines."""
    target = Path(target) if target else repaired_name(path, "conceal")
    video = _video_codec()
    _run([_ffmpeg(), "-err_detect", "ignore_err", "-ec", "guess_mvs+deblock+favor_inter",
          "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?", *video,
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
          "-movflags", "+faststart", str(target)],
         progress, report.get("duration", 0.0), stop)
    return target


def remux(path, target=None, progress=None, stop=None) -> Path:
    """L'enveloppe refaite (index, durees), les images telles quelles."""
    target = Path(target) if target else repaired_name(path, "remux")
    _run([_ffmpeg(), "-err_detect", "ignore_err", "-fflags", "+genpts+discardcorrupt",
          "-i", str(path), "-map", "0", "-c", "copy", str(target)], progress, 0.0, stop)
    return target


_encoder_cache = None


def encoder() -> str:
    """h264_nvenc si la carte et son pilote le permettent (essaye une fois),
    sinon libx264, sur le processeur."""
    global _encoder_cache
    if _encoder_cache is None:
        _encoder_cache = "libx264"
        try:
            result = subprocess.run(
                [_ffmpeg(), "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                 "color=black:size=256x144:rate=10:duration=0.5", "-c:v", "h264_nvenc",
                 "-f", "null", "-"], capture_output=True, timeout=20,
                creationflags=NO_WINDOW)
            if result.returncode == 0:
                _encoder_cache = "h264_nvenc"
        except (OSError, subprocess.SubprocessError):
            pass
    return _encoder_cache


def _video_codec() -> list:
    if encoder() == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "20"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19"]


def freeze(path, report: dict, target=None, progress=None, stop=None) -> Path:
    """Sur chaque passage abime, la derniere image saine reste a l'ecran ; le
    son continue, la duree ne change pas. Reencode la video."""
    spans = damaged_spans(report)
    if not spans:
        raise RuntimeError("aucun passage abîmé")
    target = Path(target) if target else repaired_name(path, "conceal")
    hidden = "+".join(f"between(t\\,{a:.3f}\\,{b - 0.001:.3f})" for a, b in spans)
    rate = report.get("fps") or 30
    _run([_ffmpeg(), "-err_detect", "ignore_err", "-i", str(path),
          "-map", "0:v:0", "-map", "0:a:0?",
          "-vf", f"select='not({hidden})',fps={rate:g}", *_video_codec(),
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
          "-movflags", "+faststart", str(target)],
         progress, report.get("duration", 0.0), stop)
    return target
