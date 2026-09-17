"""Fabrique une arborescence de test avec de vraies vidéos (ffmpeg testsrc)."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from videosorter.media import NO_WINDOW, Tools  # noqa: E402


def make_video(path: Path, seconds: int, seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    subprocess.run(
        [
            Tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency={200 + seed * 40}:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        creationflags=NO_WINDOW, check=True,
    )


def build(base: Path) -> Path:
    shutil.rmtree(base, ignore_errors=True)
    root = base / "root"
    counter = 0

    layout = {
        "Vacances 2019": 3,
        "Anniversaire": 12,
        "Sous-dossiers": 0,
        "Melange": 2,
    }
    for folder, count in layout.items():
        target = root / folder
        target.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            counter += 1
            make_video(target / f"clip_{i:02d}.mp4", 6 + (i % 3), counter)

    # Un dossier sans vidéo mais avec des fichiers, et un dossier imbriqué.
    (root / "Sous-dossiers" / "notes.txt").write_text("rien a voir", encoding="utf-8")
    for i in range(2):
        counter += 1
        make_video(root / "Sous-dossiers" / "interne" / f"nested_{i}.mp4", 5, counter)
    (root / "Melange" / "lisezmoi.txt").write_text("mixte", encoding="utf-8")

    # Racine en mode fichier : des vidéos directement dedans.
    flat = base / "flat"
    for i in range(4):
        counter += 1
        make_video(flat / f"film_{i}.mp4", 8, counter)

    # Destinations.
    for year in ("2019", "2020", "2021"):
        (base / "tri" / year).mkdir(parents=True, exist_ok=True)

    return root


if __name__ == "__main__":
    Tools.resolve()
    if not Tools.ffmpeg:
        raise SystemExit("ffmpeg introuvable")
    out = build(Path(sys.argv[1]))
    print("fixture:", out)
