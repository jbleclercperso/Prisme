"""Le lecteur flottant, le ⌸ du mur et la touche « Suppr » seule.

Lancement : python tests/test_floating.py
Quelques vraies videos (ffmpeg), l'interface en mode « offscreen ».
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PRISME_SANDBOX",
                      os.path.join(tempfile.gettempdir(), "prisme-tests-float"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from videosorter.config import Config  # noqa: E402
from videosorter.media import Tools  # noqa: E402
from videosorter.scan import MODE_FILES  # noqa: E402

from make_fixture import make_video  # noqa: E402

FAILS: list = []


def check(condition, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


def wait_for(app, predicate, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def main() -> int:
    app = QApplication(sys.argv)
    Tools.resolve()
    if not Tools.ffmpeg:
        print("ffmpeg introuvable")
        return 1
    base = Path(tempfile.gettempdir()) / "prisme-float-fixture"
    folder = base / "films"
    for i in range(3):
        make_video(folder / f"film_{i}.mp4", 12, i + 1)

    from videosorter.window import MainWindow
    window = MainWindow(Config())
    window.show()
    window.start_root(folder, MODE_FILES)
    wait_for(app, lambda: not window.scanning and len(window.items) == 3)
    window.on_board_open(0)
    single = window.single

    print("\n[1] La fiche joue")
    check(wait_for(app, lambda: single.player.position() > 1200),
          f"la vidéo avance dans la fiche ({single.player.position()} ms)")
    before = single.player.position()

    print("\n[2] ⧉ : la vidéo passe dans le lecteur flottant, au même instant")
    window.open_floating()
    floating = window.floating
    check(floating.active, "le lecteur flottant est ouvert")
    check(floating.windowFlags() & Qt.WindowStaysOnTopHint,
          "il reste au premier plan")
    check(os.path.normcase(floating.path) == os.path.normcase(str(window.current.path)),
          "il lit la vidéo de la fiche")
    check(not any(deck.path for deck in single.decks),
          "la fiche a lâché son fichier : on ne l'entend pas deux fois")
    check(wait_for(app, lambda: floating.player.player.position() >= before - 300),
          f"il reprend où en était la fiche ({before} → "
          f"{floating.player.player.position()} ms)")
    check(floating.player.strip.isHidden()
          and floating.windowFlags() & Qt.FramelessWindowHint,
          "ni pellicule ni cadre : toute la place va à la vidéo")
    check(wait_for(app, lambda: floating.title.isVisible()
                   and floating.title.text() == window.current.name, 5),
          "le titre, discret, posé sur l'image")
    check(wait_for(app, lambda: floating._fitted == floating.path, 10)
          and abs((floating.height() - 8) / max(1, floating.width() - 8) - 240 / 320) < 0.05,
          f"la fenêtre prend les proportions de la vidéo ({floating.width()}×{floating.height()})")

    print("\n[3] Ses gestes")
    window.step(1)
    check(wait_for(app, lambda: floating.path == str(window.items[1].path)),
          "▸ passe à la suivante, dans le lecteur flottant")
    check(not any(deck.path for deck in single.decks),
          "la fiche ne charge toujours rien")
    window.cfg.set_destinations([{"key": "6", "label": "Rangés",
                                  "path": str(base / "ranges")}])
    floating.player.radialRequested.emit()
    check(window.float_radial.isVisible() and window.float_radial.entries
          and window.float_radial.parent() is floating,
          "clic droit : les destinations, comme sur la fiche")
    window.float_radial.close_menu()
    window.toggle_mute()
    check(floating.player._muted == window.cfg["muted"], "Ctrl+M le concerne aussi")
    window.toggle_mute()

    print("\n[4] Une touche de tri dans le lecteur flottant")
    seen = []
    original = window.keyPressEvent
    window.keyPressEvent = lambda event: seen.append(event.key())
    floating.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    window.keyPressEvent = original
    check(seen == [Qt.Key_Space], "elle est transmise à Prisme")

    print("\n[5] ↩ : retour dans Prisme, au même instant")
    wait_for(app, lambda: floating.player.player.position() > 2500)
    at = floating.player.player.position()
    window.leave_floating(activate=False)
    check(not floating.isVisible(), "le lecteur flottant se referme")
    check(not any(deck.path for deck in floating.player.decks),
          "et lâche son fichier")
    check(wait_for(app, lambda: single.player.position() >= at - 300),
          f"la fiche reprend à l'instant atteint ({at} → {single.player.position()} ms)")

    print("\n[6] Automatique : Prisme réduit pendant qu'une vidéo joue")
    wait_for(app, lambda: single.player.position() > at + 500)
    window.showMinimized()
    wait_for(app, lambda: window.isMinimized(), 3)
    window._float_check()
    check(floating.active and floating.auto,
          "le lecteur flottant s'ouvre tout seul")
    check(floating.testAttribute(Qt.WA_ShowWithoutActivating),
          "sans prendre le clavier à l'application où l'on travaille")
    window.leave_floating(activate=True)
    check(not floating.isVisible() and not window.isMinimized(),
          "revenir le referme et rend la fenêtre")
    window.cfg["float_auto"] = False
    window.showMinimized()
    wait_for(app, lambda: window.isMinimized(), 3)
    window._float_check()
    check(not floating.isVisible(), "réglage décoché : rien ne s'ouvre")
    window.cfg["float_auto"] = True
    window.showNormal()

    print("\n[7] Le mur : ⌸ montre le fichier dans l'explorateur")
    shown = []
    window.reveal_path = lambda path: shown.append(path)
    window.wall.revealRequested.disconnect()
    window.wall.revealRequested.connect(window.reveal_path)
    pane = window.wall.panes[0]
    pane.video_path = str(window.items[0].path)
    tips = [pane.bar.buttons.itemAt(i).widget().toolTip()
            for i in range(pane.bar.buttons.count())
            if pane.bar.buttons.itemAt(i).widget() is not None]
    check(any("explorateur" in tip for tip in tips)
          and not any("fiche" in tip for tip in tips),
          "le bandeau d'un panneau a « explorateur » à la place de « fiche »")
    pane._reveal()
    check(shown == [pane.video_path], "et son clic vise la vidéo du panneau")

    print("\n[8] La touche rouge dit seulement « Suppr »")
    from videosorter.widgets import KeyCap
    caps = window.commands.findChildren(KeyCap)
    delete = next((c for c in caps if c.findChildren(type(c.text))[0].text() == "Suppr"),
                  None)
    check(delete is not None and delete.text.isHidden() and not delete.full,
          "la touche seule, sans « Écarter — détruit à la fermeture »")
    check(delete is not None and "Supprimer" in delete.toolTip(),
          "l'infobulle dit « Supprimer »")

    window.close()
    print("\ntout est vert" if not FAILS else f"\n{len(FAILS)} échec(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
