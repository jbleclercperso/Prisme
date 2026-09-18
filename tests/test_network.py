"""Vérifie la reconnaissance d'un stockage réseau et l'adaptation qui en découle."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from PySide6.QtWidgets import QApplication  # noqa: E402

from videosorter.media import PreviewManager, is_network_path  # noqa: E402

FAILURES: list = []


def check(condition: bool, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILURES.append(label)


def main() -> int:
    QApplication.instance() or QApplication([])

    print("\n[1] Reconnaissance du support")
    check(is_network_path(r"\\nas\videos\film.mp4"), "chemin UNC reconnu comme réseau")
    check(is_network_path("//nas/videos/film.mp4"), "UNC en barres obliques aussi")
    check(not is_network_path(r"C:\Users\test"), "disque local non pris pour un réseau")
    check(not is_network_path(str(Path.home())), "dossier personnel non plus")
    check(not is_network_path(""), "chemin vide sans incident")

    print("\n[2] Parallélisme adapté")
    manager = PreviewManager(480)
    local = manager.local_workers
    check(2 <= local <= 4, f"en local, {local} extractions de front")
    manager.tune_for(r"\\nas\videos")
    check(manager.pool.maxThreadCount() == 8,
          f"sur le réseau, davantage ({manager.pool.maxThreadCount()}) : "
          "c'est la latence qui limite, pas le disque")
    manager.tune_for(str(Path.home()))
    check(manager.pool.maxThreadCount() == local, "retour au réglage local ensuite")

    print(f"\n{5 + 3 - len(FAILURES)}/8 vérifications réussies")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
