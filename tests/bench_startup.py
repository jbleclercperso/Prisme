"""Mesure ce que coûte l'ouverture d'une racine, deux fois de suite.

Le second passage doit se servir de l'analyse précédente. Sinon, le cache ne
sert à rien et il faut le dire plutôt que de le supposer.

Lancement : python tests/bench_startup.py <racine>
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from PySide6.QtWidgets import QApplication  # noqa: E402

from videosorter.config import Config  # noqa: E402
from videosorter.media import Tools  # noqa: E402
from videosorter.window import MainWindow  # noqa: E402


def open_root(app, window, root: Path) -> tuple:
    start = time.perf_counter()
    window.start_root(root)
    deadline = time.time() + 600
    while time.time() < deadline:
        app.processEvents()
        if not window.scanning:
            break
        time.sleep(0.01)
    elapsed = time.perf_counter() - start
    thread = window.scan_thread
    return elapsed, len(window.all_items), thread.reused, thread.rescanned


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not root or not root.is_dir():
        print("usage : python tests/bench_startup.py <racine>")
        return 2

    app = QApplication.instance() or QApplication([])
    Tools.resolve()
    window = MainWindow(Config())

    first = open_root(app, window, root)
    print(f"1er passage  : {first[0]:6.2f} s   {first[1]} éléments   "
          f"{first[2]} repris, {first[3]} analysés")

    second = open_root(app, window, root)
    print(f"2e passage   : {second[0]:6.2f} s   {second[1]} éléments   "
          f"{second[2]} repris, {second[3]} analysés")

    if second[0] > 0:
        print(f"\nrapport      : {first[0] / max(second[0], 1e-6):.1f}× plus rapide")
    if second[2] == 0 and second[1]:
        print("ATTENTION : rien n'a été repris du cache, il ne sert pas.")
    window.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
