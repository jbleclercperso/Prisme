"""Le mode photo : bascule, diaporama, mur, feuilletage, format.

Lancement : python tests/test_photos.py
Quelques vraies images (et une video, qui doit etre ignoree en mode photo),
l'interface en mode « offscreen ».
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("PRISME_SANDBOX",
                      os.path.join(tempfile.gettempdir(), "prisme-tests-photos"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from videosorter import config as vs_config  # noqa: E402
from videosorter.config import Config  # noqa: E402
from videosorter.media import Tools  # noqa: E402

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


def make_photo(path: Path, width: int, height: int, color: str,
               alpha: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, height,
                   QImage.Format_ARGB32 if alpha else QImage.Format_RGB32)
    image.fill(QColor(0, 0, 0, 0) if alpha else QColor(color))
    if alpha:
        for y in range(height // 4, height // 2):
            for x in range(width // 4, width // 2):
                image.setPixelColor(x, y, QColor(color))
    assert image.save(str(path)), path


def main() -> int:
    app = QApplication(sys.argv)
    Tools.resolve()
    base = Path(tempfile.gettempdir()) / "prisme-photos-fixture"
    shutil.rmtree(base, ignore_errors=True)
    root = base / "Photos"
    make_photo(root / "Vacances" / "plage.jpg", 2400, 1600, "#3a86ff")
    make_photo(root / "Vacances" / "coucher.jpg", 1600, 2400, "#ff006e")
    make_photo(root / "Vacances" / "logo.png", 800, 800, "#ffbe0b", alpha=True)
    make_photo(root / "Vacances" / "port.jpg", 2000, 1500, "#8338ec")
    make_photo(root / "Noel" / "sapin.jpg", 1200, 900, "#2a9d8f")
    make_photo(root / "Noel" / "table.webp", 1200, 900, "#e76f51")
    if Tools.ffmpeg:
        make_video(root / "Noel" / "clip.mp4", 4, 3)
    films = base / "Films"
    films.mkdir(parents=True, exist_ok=True)

    from videosorter.index import INDEX
    from videosorter.header import TAB_FOLDERS, TAB_SPLIT, TAB_VIDEOS
    from videosorter.window import MainWindow, PAGE_SORT

    print("\n[1] Au lancement : les vidéos, « Dossiers », sans filtre")
    cfg = Config()
    cfg["media_kind"] = "photo"
    cfg.set_of_kind("photo", "only_unseen", True)
    cfg.set_of_kind("photo", "orientations", ["horizontal"])
    cfg.set_of_kind("photo", "root", str(root))
    cfg.set_of_kind("video", "root", str(films))
    window = MainWindow(cfg)
    window.resize(1280, 800)
    window.show()
    check(vs_config.media_kind() == "video" and window.tab == TAB_FOLDERS,
          "toujours les vidéos, onglet Dossiers")
    check(cfg.of_kind("photo", "only_unseen") is False
          and cfg.of_kind("photo", "orientations") == [],
          "« Non vus » et « Horizontales » d'hier ne cachent plus de photos")
    check(window.kind_switch.value == "video"
          and "photos" in window.kind_switch.toolTip(),
          "l'icône dit qu'on est en vidéos, et que le clic mène aux photos")
    video_index = Path(INDEX.path)

    print("\n[2] « Photos » : on bascule, à sa racine")
    window.kind_switch.click()
    check(vs_config.media_kind() == "photo" and ".jpg" in vs_config.VIDEO_EXTS
          and ".mp4" not in vs_config.VIDEO_EXTS, "Prisme ne regarde plus que les photos")
    check(wait_for(app, lambda: window.root == root and not window.scanning
                   and len(window.items) == 2, 30),
          f"la racine des photos s'ouvre d'elle-même ({[i.name for i in window.items]})")
    check(window.stack.currentIndex() == PAGE_SORT and window.tab == TAB_FOLDERS,
          "sur « Dossiers »")
    names = sorted(Path(v).name for i in window.items for v in i.videos)
    check(names == ["coucher.jpg", "logo.png", "plage.jpg", "port.jpg", "sapin.jpg",
                    "table.webp"],
          f"les photos seules, sans la vidéo glissée parmi elles ({names})")
    check(Path(INDEX.path).name == "index-photos.db"
          and window.ratings.path.name == "ratings-photos.json",
          "avec leur propre index et leurs propres notes")
    check(window.tabs.buttons[TAB_VIDEOS].text() == "Photos"
          and not window.tabs.buttons[TAB_SPLIT].isHidden(),
          "l'onglet s'appelle « Photos », et le Mur reste")
    check(window.kind_switch.value == "photo"
          and "vidéos" in window.kind_switch.toolTip(),
          "l'icône est devenue celle des photos")
    check(window.controls.sorts.buttons["duration"].isHidden()
          and window.controls.sorts.buttons["size"].isHidden()
          and not window.controls.sorts.buttons["resolution"].isHidden(),
          "ni durée ni taille à trier")

    print("\n[3] Vignettes et dimensions, sans ffmpeg")
    from videosorter import media
    saved = Tools.ffmpeg
    Tools.ffmpeg = ""
    try:
        thumb = media.extract_thumb(root / "Vacances" / "plage.jpg", 0.0, 480)
        clear = media.extract_thumb(root / "Vacances" / "logo.png", 0.0, 480)
    finally:
        Tools.ffmpeg = saved
    image = QImage(str(thumb)) if thumb else QImage()
    check(thumb is not None and image.width() == 480 and image.height() == 320,
          f"une vignette de 480 px, à ses proportions ({image.width()}×{image.height()})")
    check(clear is not None and QImage(str(clear)).pixelColor(5, 5).lightness() < 60,
          "un PNG transparent sur fond sombre")
    info = media.probe(root / "Noel" / "sapin.jpg")
    check(info["width"] == 1200 and info["height"] == 900 and info["duration"] == 0,
          f"ses dimensions, lues dans l'en-tête ({info})")

    print("\n[4] La fiche d'une photo, et son diaporama")
    photo = str(root / "Vacances" / "plage.jpg")
    window.play_in_app(photo)
    single = window.single
    check(wait_for(app, lambda: single.still_path == photo
                   and single.still.image is not None, 20),
          "la fiche montre la photo")
    check(not any(deck.path for deck in single.decks) and single.strip.isHidden(),
          "aucun lecteur vidéo, pas de pellicule")
    check(window.single_bar.pause_button.toolTip().startswith("Pause")
          and window.single_bar.pause_button is not window.single_bar.buttons.itemAt(1).widget(),
          "le bouton ⏯ est bien le sien — la flèche ◂ ne prend plus son icône")
    area = single.video_area
    wheel = QWheelEvent(QPointF(area.width() / 2, area.height() / 2),
                        QPointF(area.mapToGlobal(QPoint(10, 10))), QPoint(0, 0),
                        QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                        Qt.ScrollUpdate, False)
    single.wheelEvent(wheel)
    check(single.zoom > 1.0, f"la molette zoome dans la photo (×{single.zoom:.2f})")
    window.slideshow_timer.setInterval(300)
    window.cfg["stay_in_folder"] = True
    single.toggle_pause()                  # ⏯, Entrée ou un clic
    check(window.slideshow_timer.isActive() and single.slideshow_on,
          "⏯ lance le diaporama")
    seen = {single.still_path}
    wait_for(app, lambda: (seen.add(single.still_path) or len(seen) >= 3), 10)
    check(len(seen) >= 3, f"les photos défilent toutes seules ({len(seen)} vues)")
    check(all(Path(p).parent == root / "Vacances" for p in seen),
          "sans quitter le dossier quand la case est cochée")
    single.toggle_pause()
    at = single.still_path
    check(not window.slideshow_timer.isActive(), "⏯ l'arrête")
    wait_for(app, lambda: False, 0.8)
    check(single.still_path == at, "et plus rien ne bouge")
    window.cfg["stay_in_folder"] = False

    print("\n[5] « Verticales » sur des photos jamais mesurées")
    window.set_tab(TAB_VIDEOS)
    wait_for(app, lambda: not window.scanning and len(window.all_items) == 6, 30)
    for item in window.all_items:
        INDEX.probes.pop(str(item.path), None)
    window._photo_tried = set()
    window.controls.set_orientation("vertical")
    window._on_controls_changed()
    check(wait_for(app, lambda: [i.name for i in window.items] == ["coucher"]
                   or [Path(i.path).name for i in window.items] == ["coucher.jpg"], 20),
          f"elles se mesurent, et la verticale reste "
          f"({[Path(i.path).name for i in window.items]})")
    window.controls.set_orientation("all")
    window._on_controls_changed()

    print("\n[6] Le mur : un diaporama par panneau")
    window.set_tab(TAB_SPLIT)
    window.set_wall_orientation("any")
    window.set_wall_count(2)
    panes = window.wall.panes
    check(wait_for(app, lambda: all(p.video_path for p in panes), 20),
          "le mur se remplit de photos")
    check(all(p.photo and p.still.isVisible() and p.slideshow.isActive() for p in panes),
          "chacune en diaporama, dès l'ouverture")
    for pane in panes:
        pane.slideshow_ms = 300
        pane._restart_slide()
    first = panes[0].video_path
    check(wait_for(app, lambda: panes[0].video_path != first, 5),
          "la photo d'un panneau change toute seule")
    panes[0].toggle_pause()
    still = panes[0].video_path
    check(not panes[0].slideshow.isActive() and not panes[0].slideshow_on,
          "⏯ met ce panneau en pause")
    wait_for(app, lambda: False, 0.8)
    check(panes[0].video_path == still, "il garde sa photo")
    panes[0].toggle_pause()
    check(panes[0].slideshow.isActive(), "et le relance")
    window.set_tab(TAB_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) == 2, 20)

    print("\n[7] Survoler un dossier : ses photos défilent")
    window.toggle_board(True) if not window.browsing else None
    board = window.board
    at = next(i for i, item in enumerate(board.items) if item.name == "Vacances")
    card = board._card_for(at)
    wait_for(app, lambda: bool(card.thumb_path), 10)
    home = card.thumb_path
    board._flip_start(card)
    check(wait_for(app, lambda: len(board._flip_paths) >= 3, 15),
          f"une dizaine de ses photos, tirées au hasard ({len(board._flip_paths)})")
    board._flip_step()
    board._flip_step()
    check(card.thumb_path != home or len(set(board._flip_paths)) > 1,
          "elles se succèdent sur la carte")
    board._flip_stop()
    check(card.thumb_path == home, "et la carte reprend son image quand on s'en va")

    print("\n[8] Retour aux vidéos")
    window.kind_switch.click()
    check(vs_config.media_kind() == "video" and ".mp4" in vs_config.VIDEO_EXTS,
          "les vidéos reviennent")
    check(Path(INDEX.path) == video_index and window.ratings.path.name == "ratings.json",
          "avec leur index et leurs notes")
    check(window.tabs.buttons[TAB_VIDEOS].text() == "Vidéos"
          and not window.controls.sorts.buttons["duration"].isHidden(),
          "l'onglet Vidéos et le tri par durée aussi")

    window.close()
    print("\ntout est vert" if not FAILS else f"\n{len(FAILS)} échec(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
