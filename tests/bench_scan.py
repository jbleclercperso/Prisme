"""Mesure le coût de l'analyse sur une arborescence synthétique.

Les fichiers sont vides : l'analyse ne lit jamais leur contenu, seuls comptent
le nombre d'entrées et le coût de lecture de leurs métadonnées.

Lancement : python tests/bench_scan.py [dossier] [nb_dossiers] [fichiers_par_dossier]
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from videosorter import scan as vs_scan  # noqa: E402


def build(base: Path, folders: int, files_per_folder: int) -> None:
    if base.exists():
        print(f"réutilisation de {base}")
        return
    print(f"fabrication de {folders} dossiers × {files_per_folder} fichiers…")
    for index in range(folders):
        folder = base / f"dossier_{index:04d}"
        # Une vraie collection est imbriquee : on repartit les fichiers sur
        # plusieurs niveaux, sinon le cache paraitrait moins utile qu'il ne l'est.
        levels = [folder, folder / "saison_1", folder / "saison_1" / "extras",
                  folder / "saison_2"]
        for level in levels:
            level.mkdir(parents=True, exist_ok=True)
        for position in range(files_per_folder):
            suffix = ".mp4" if position % 2 == 0 else ".txt"
            (levels[position % len(levels)] / f"f_{position:04d}{suffix}").touch()
    print("fait")


def measure(base: Path, label: str) -> float:
    entries = vs_scan.list_entries(base, vs_scan.MODE_FOLDERS)
    start = time.perf_counter()
    total_files = 0
    total_size = 0
    for path in entries:
        item = vs_scan.scan_folder(path)
        total_files += item.file_count
        total_size += item.size
    elapsed = time.perf_counter() - start
    print(f"{label:28s} {elapsed:7.2f} s   ({len(entries)} dossiers, "
          f"{total_files} fichiers, {total_size} octets)")
    return elapsed


def measure_with_cache(base: Path) -> None:
    """Compare l'analyse complète et la relecture depuis l'index."""
    from videosorter.index import INDEX
    from videosorter.scan import signature

    entries = vs_scan.list_entries(base, vs_scan.MODE_FOLDERS)
    ids = [str(path) for path in entries]

    INDEX.clear()
    start = time.perf_counter()
    for path in entries:
        item = vs_scan.scan_folder(path)
        INDEX.put_folder(item, signature(path))
    INDEX.commit(force=True)
    cold = time.perf_counter() - start
    print(f"{'analyse complète':28s} {cold:7.2f} s")

    # Le relancement ne demande plus rien au disque : une requête rend la
    # collection entière, et seules les empreintes sont ensuite comparées.
    start = time.perf_counter()
    known = INDEX.folders(ids)
    restore = time.perf_counter() - start
    print(f"{'restitution de l index':28s} {restore:7.3f} s   "
          f"({len(known)}/{len(entries)} éléments, sans lecture disque)")

    start = time.perf_counter()
    stored = INDEX.signatures(ids)
    reused = sum(1 for path in entries if stored.get(str(path)) == signature(path))
    warm = time.perf_counter() - start
    print(f"{'vérification des dates':28s} {warm:7.2f} s   "
          f"({reused}/{len(entries)} inchangés, {cold / max(warm, 1e-6):.0f}× "
          f"plus rapide que l analyse)")

    # Un fichier ajouté au deuxième niveau doit invalider son dossier.
    victim = entries[0]
    target = next((p for p in victim.iterdir() if p.is_dir()), victim)
    (target / "nouveau.mp4").touch()
    changed = INDEX.signatures([str(victim)]).get(str(victim)) != signature(victim)
    print(f"{'modification détectée':28s} {'oui' if changed else 'NON'}")
    (target / "nouveau.mp4").unlink()


if __name__ == "__main__":
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        os.environ.get("TEMP", "."), "vs-bench"
    )
    folders = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    per_folder = int(sys.argv[3]) if len(sys.argv) > 3 else 60

    if "--clean" in sys.argv:
        shutil.rmtree(base, ignore_errors=True)
    build(base, folders, per_folder)

    measure(base, "analyse (1er passage)")
    measure(base, "analyse (cache OS chaud)")
    print()
    measure_with_cache(base)


