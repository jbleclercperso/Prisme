"""Vérifie la corbeille Windows réelle, puis rend la fenêtre en PNG.

Lancement : python tests/test_recycle_and_render.py <dossier_fixture> <sortie.png>
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from videosorter import actions  # noqa: E402
from videosorter.config import Config  # noqa: E402
from videosorter.media import Tools  # noqa: E402
from videosorter.window import MainWindow  # noqa: E402


def test_recycle(tmp: Path) -> bool:
    """Un aller simple vers la corbeille : on vérifie juste que le fichier part."""
    probe_file = tmp / "corbeille-test.txt"
    probe_file.parent.mkdir(parents=True, exist_ok=True)
    probe_file.write_text("fichier jetable cree par le test VideoSorter", encoding="utf-8")
    try:
        new_path, reversible = actions.delete(probe_file, "recycle")
    except actions.ActionError as exc:
        print(f"  FAIL corbeille Windows : {exc}")
        return False
    ok = not probe_file.exists() and new_path is None and reversible is False
    print(f"  {'ok  ' if ok else 'FAIL'} corbeille Windows : fichier retire du disque")
    return ok


def render(base: Path, out: Path) -> bool:
    app = QApplication.instance() or QApplication(sys.argv)
    sandbox = base / "_appdata_render"
    sandbox.mkdir(parents=True, exist_ok=True)
    cfg = Config(path=sandbox / "config.json")
    cfg.set_destinations([
        {"key": "1", "label": "2019", "path": str(base / "tri" / "2019")},
        {"key": "2", "label": "2020", "path": str(base / "tri" / "2020")},
        {"key": "3", "label": "2021", "path": str(base / "tri" / "2021")},
        {"key": "a", "label": "A revoir", "path": str(base / "tri" / "revoir")},
    ])
    window = MainWindow(cfg)
    window.resize(1400, 900)
    window.show()
    window.start_root(base / "root")

    deadline = time.time() + 120
    while time.time() < deadline:
        app.processEvents()
        filled = sum(1 for tile in window.grid.tiles if tile._pixmap)
        if not window.scanning and filled >= 10:
            break
        time.sleep(0.05)

    for _ in range(30):
        app.processEvents()
        time.sleep(0.02)

    out.parent.mkdir(parents=True, exist_ok=True)
    ok = window.grab().save(str(out))
    filled = sum(1 for tile in window.grid.tiles if tile._pixmap)
    print(f"  {'ok  ' if ok else 'FAIL'} rendu de la fenetre ({filled}/10 vignettes) -> {out}")
    window.close()
    app.processEvents()
    return ok and filled >= 10


if __name__ == "__main__":
    base = Path(sys.argv[1])
    out = Path(sys.argv[2])
    Tools.resolve()
    results = [test_recycle(base / "_scratch"), render(base, out)]
    sys.exit(0 if all(results) else 1)
