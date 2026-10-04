"""La recherche web, gardee sur le disque : ses resultats, ce qu'on en sait,
et les telechargements en cours.

Prisme ferme (ou coupe), tout vivait en memoire : a la relance, la fenetre
etait vide, les resultats perdus, et les telechargements a moitie faits
oublies. Tout se note ici au fil de l'eau ; la fenetre de recherche reprend
de la ou elle en etait, et les telechargements interrompus repartent.

Sans Qt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .mediafind import Media

VERSION = 1
# Les telechargements finis se gardent un temps (pour que leur carte dise
# « dans la collection ») ; au-dela, on les oublie.
KEEP_FINISHED = 300

_VIDEO_FIELDS = ("title", "page_url", "source_domain", "thumbnail_url", "duration_s",
                 "height", "snippet", "query", "size", "found_on")


def media_to_dict(media) -> dict | None:
    if not isinstance(media, Media):
        return None
    return {"url": media.url, "referer": media.referer, "duration": media.duration,
            "height": media.height, "size": media.size, "title": media.title,
            "found_at": media.found_at, "format_id": media.format_id,
            "poster": media.poster}


def media_from_dict(data) -> Media | None:
    if not isinstance(data, dict) or not data.get("url"):
        return None
    try:
        return Media(data["url"], data.get("referer", ""), float(data.get("duration") or 0),
                     int(data.get("height") or 0), int(data.get("size") or 0),
                     data.get("title", ""), float(data.get("found_at") or 0),
                     data.get("format_id", ""), data.get("poster", ""))
    except (TypeError, ValueError):
        return None


def video_to_dict(video) -> dict:
    row = {name: getattr(video, name, None) for name in _VIDEO_FIELDS}
    row["media"] = media_to_dict(getattr(video, "media", None))
    row["unavailable"] = getattr(video, "unavailable", "") or ""
    return row


def video_from_dict(row: dict):
    from .websearch import VideoResult
    video = VideoResult(title=row.get("title") or "", page_url=row.get("page_url") or "",
                        source_domain=row.get("source_domain") or "")
    for name in _VIDEO_FIELDS:
        if name in row and row[name] is not None:
            setattr(video, name, row[name])
    video.media = media_from_dict(row.get("media"))
    if row.get("unavailable"):
        video.unavailable = row["unavailable"]
    return video


def save(path: Path, data: dict) -> None:
    """D'un bloc : une coupure pendant l'ecriture ne laisse pas un fichier
    a moitie ecrit."""
    data = dict(data, version=VERSION)
    downloads = data.get("downloads") or []
    finished = [d for d in downloads if d.get("state") in ("done", "failed")]
    if len(finished) > KEEP_FINISHED:
        drop = {id(d) for d in finished[:len(finished) - KEEP_FINISHED]}
        data["downloads"] = [d for d in downloads if id(d) not in drop]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != VERSION:
        return {}
    return data
