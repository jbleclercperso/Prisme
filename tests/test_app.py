"""Test d'intégration : analyse, aperçus et actions, sur une vraie arborescence.

Lancement : python tests/test_app.py [dossier_fixture]
L'interface tourne en mode « offscreen », aucune fenêtre ne s'affiche.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from videosorter import config as vs_config  # noqa: E402
from videosorter import media as vs_media  # noqa: E402
from videosorter.config import Config  # noqa: E402
from videosorter.media import Tools  # noqa: E402
from videosorter.scan import MODE_FILES, MODE_FOLDERS  # noqa: E402
from videosorter.window import MainWindow  # noqa: E402

from make_fixture import build  # noqa: E402

FAILURES: list = []
CHECKS = 0


def check(condition: bool, label: str) -> bool:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        FAILURES.append(label)
    return bool(condition)


def settle(app: QApplication, window, timeout: float = 30.0) -> bool:
    """Attend la fin des transferts en tache de fond avant de verifier le disque."""
    ok = wait_for(app, lambda: not window.transfers.busy, timeout)
    pump(app, 0.25)
    return ok


def pump(app: QApplication, seconds: float = 0.3) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def wait_for(app: QApplication, predicate, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def first_untouched(window) -> int:
    """Index du premier element encore intact : les precedents sont deja partis."""
    for index, item in enumerate(window.items):
        if not item.status:
            return index
    return 0


def wheel(widget, notches: int, modifiers=Qt.NoModifier) -> None:
    """Simule un cran de molette au centre du widget."""
    center = widget.rect().center()
    event = QWheelEvent(
        QPointF(center), QPointF(widget.mapToGlobal(center)),
        QPoint(0, 0), QPoint(0, 120 * notches),
        Qt.NoButton, modifiers, Qt.NoScrollPhase, False,
    )
    QApplication.sendEvent(widget, event)


def check_new_features(app, window, base, root, flat, tri) -> None:
    """Dossier parent, molette, raccourcis illimités, arborescence, tâche de fond."""

    print("\n[12] Nom du dossier parent sous le titre")
    window.start_root(flat)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 1, 60)
    window.show_item(0)
    check(window.item_parent.text() == f"dans  {flat.name}",
          f"mode fichier : parent affiché (obtenu {window.item_parent.text()!r})")
    check(window.item_parent.toolTip() == str(flat), "chemin complet en infobulle")

    print("\n[13] Molette pour avancer et reculer")
    player = window.single.player
    ok = wait_for(app, lambda: player.duration() > 0, 30)
    check(ok, "vidéo chargée par le lecteur")
    player.setPosition(1000)
    pump(app, 0.3)
    wheel(window.single, 1)
    pump(app, 0.3)
    forward = player.position()
    check(forward >= 5000,
          f"un cran avance de {window.cfg['scroll_seconds']} s (position {forward} ms)")
    wheel(window.single, -1)
    pump(app, 0.3)
    check(player.position() < forward, "un cran inverse recule")
    check(not window.single.position_label.isHidden(),
          "position affichée pendant le réglage")

    print("\n[14] Raccourcis au-delà des chiffres")
    from videosorter.config import KEY_ORDER
    check(KEY_ORDER.startswith("1234567890azertyuiop"),
          "chiffres puis lettres dans l'ordre AZERTY")
    check(len(KEY_ORDER) == 36, f"36 raccourcis possibles (obtenu {len(KEY_ORDER)})")
    many = [
        {"key": KEY_ORDER[i], "label": f"dest{i}", "path": str(tri / f"d{i}")}
        for i in range(20)
    ]
    window.cfg.set_destinations(many)
    window.commands.rebuild(many, "Corbeille")
    caps = window.commands.layout_.count()
    check(caps == 22, f"les 20 destinations sont toutes affichées (obtenu {caps - 2})")
    window.commands.setFixedWidth(600)
    window.commands.layout_.setGeometry(window.commands.rect())
    height = window.commands.layout_.heightForWidth(600)
    check(height > 40, f"la barre passe à la ligne au lieu de déborder ({height} px)")

    # Une lettre lointaine doit déclencher le déplacement.
    letter = KEY_ORDER[12]           # au-delà des chiffres
    dest_dir = tri / "lettre"
    window.cfg.set_destinations([{"key": letter, "label": "Lettre", "path": str(dest_dir)}])
    window.show_item(first_untouched(window))
    name = window.current.name
    QTest.keyClick(window, getattr(Qt, f"Key_{letter.upper()}"))
    settle(app, window)
    check((dest_dir / name).exists(),
          f"la touche « {letter} » envoie bien vers sa destination")

    print("\n[15] Mode arborescence")
    window.cfg["tree_root"] = str(tri)
    window.toggle_tree(True)
    check(not window.tree.isHidden(), "panneau affiché")
    check(window.tree.root == str(tri), "arborescence enracinée sur le dossier de tri")
    index = window.tree.model.index(str(tri / "2019"))
    check(index.isValid(), "les sous-dossiers sont listés")

    window.show_item(first_untouched(window))
    name = window.current.name
    window.tree.view.clicked.emit(index)
    settle(app, window)
    check((tri / "2019" / name).exists(),
          f"un clic sur « 2019 » envoie « {name} » sans confirmation")
    window.toggle_tree(False)
    check(window.tree.isHidden(), "panneau masqué à la bascule")

    print("\n[16] Transfert en tâche de fond")
    import videosorter.actions as vs_actions
    real_move = vs_actions.move_to
    started = {"at": 0.0}

    def slow_move(src, dest):
        started["at"] = time.time()
        time.sleep(1.2)          # simule une copie vers un autre disque
        return real_move(src, dest)

    # Racine dédiée : il faut un élément suivant pour vérifier l'enchaînement.
    fond_src = base / "fond_src"
    shutil.rmtree(fond_src, ignore_errors=True)
    fond_src.mkdir(parents=True, exist_ok=True)
    for position, source in enumerate(sorted(tri.rglob("*.mp4"))[:2]):
        shutil.copy2(source, fond_src / f"fond_{position}.mp4")
    window.start_root(fond_src)
    wait_for(app, lambda: not window.scanning and len(window.items) == 2, 60)

    vs_actions.move_to = slow_move
    try:
        window.show_item(first_untouched(window))
        slow_name = window.current.name
        dest_dir = tri / "fond"
        window.cfg.set_destinations([{"key": "1", "label": "Fond", "path": str(dest_dir)}])
        index_before = window.index
        begin = time.time()
        QTest.keyClick(window, Qt.Key_1)
        elapsed = time.time() - begin
        check(elapsed < 0.5, f"la main est rendue tout de suite ({elapsed:.2f} s)")
        check(window.index == index_before + 1, "on est déjà sur l'élément suivant")
        check(window.transfers.busy, "le transfert se poursuit derrière")
        check(not window.root_bar.pending.isHidden(),
              "un indicateur signale le transfert")
        settle(app, window, 30)
        check((dest_dir / slow_name).exists(), f"« {slow_name} » bien arrivé à destination")
        check(window.root_bar.pending.isHidden(), "indicateur éteint une fois fini")
    finally:
        vs_actions.move_to = real_move


def main() -> int:
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        os.environ.get("TEMP", "."), "vs-fixture"
    )
    if not Tools.resolve():
        print("ffmpeg/ffprobe introuvables")
        return 2

    # Reconstruit systématiquement : le test déplace et supprime des fichiers,
    # réutiliser l'arborescence précédente fausserait les comptages.
    print("Fabrication du jeu de test…")
    build(base)
    root = base / "root"
    flat = base / "flat"
    tri = base / "tri"

    # Isole la configuration et le cache de vignettes de ceux de l'utilisateur.
    sandbox = base / "_appdata"
    shutil.rmtree(sandbox, ignore_errors=True)
    sandbox.mkdir(parents=True, exist_ok=True)
    vs_media.THUMB_DIR = sandbox / "thumbs"
    vs_config.LOCAL_TRASH = sandbox / "_TRASH"
    import videosorter.actions as vs_actions
    vs_actions.LOCAL_TRASH = sandbox / "_TRASH"

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = Config(path=sandbox / "config.json")
    cfg["delete_mode"] = "local_trash"
    cfg.set_destinations([
        {"key": "1", "label": "2019", "path": str(tri / "2019")},
        {"key": "2", "label": "2020", "path": str(tri / "2020")},
    ])
    window = MainWindow(cfg)

    # ---------------------------------------------------------------- scan
    print("\n[1] Analyse du dossier racine (mode dossiers)")
    window.start_root(root)
    ok = wait_for(app, lambda: not window.scanning and len(window.items) >= 4, 60)
    check(ok, "analyse terminée")
    names = [item.name for item in window.items]
    check(window.mode == MODE_FOLDERS, f"mode détecté = dossiers (obtenu {window.mode})")
    check(len(window.items) == 4, f"4 dossiers trouvés (obtenu {len(window.items)}) : {names}")

    by_name = {item.name: item for item in window.items}
    anniv = by_name.get("Anniversaire")
    check(anniv is not None and anniv.video_count == 12,
          f"Anniversaire : 12 vidéos (obtenu {anniv.video_count if anniv else '-'})")
    sous = by_name.get("Sous-dossiers")
    check(sous is not None and sous.video_count == 2,
          "Sous-dossiers : vidéos imbriquées trouvées récursivement")
    check(sous is not None and sous.file_count == 3,
          f"Sous-dossiers : 3 fichiers au total (obtenu {sous.file_count if sous else '-'})")
    check(sous is not None and sous.size > 0, "taille agrégée non nulle")

    # ------------------------------------------------------------- aperçus
    print("\n[2] Plans d'aperçus et vignettes")
    ok = wait_for(app, lambda: len(window.plans) >= 3, 90)
    check(ok, f"plans calculés ({len(window.plans)} éléments)")

    plan_anniv = window.plans.get(str(anniv.path), [])
    check(len(plan_anniv) == 10, f"Anniversaire : 10 aperçus (obtenu {len(plan_anniv)})")
    check(len({v for v, _ in plan_anniv}) == 10,
          "Anniversaire : 10 vidéos distinctes échantillonnées")

    melange = by_name.get("Melange")
    plan_mel = window.plans.get(str(melange.path), [])
    check(len(plan_mel) == 10, f"Melange (2 vidéos) : 10 aperçus (obtenu {len(plan_mel)})")
    check(len({v for v, _ in plan_mel}) == 2, "Melange : les 2 vidéos sont utilisées")
    check(len({round(ts, 2) for _, ts in plan_mel}) >= 5,
          "Melange : instants échelonnés dans chaque vidéo")
    check(all(ts > 0 for _, ts in plan_mel), "instants strictement positifs")

    window.show_item([i.name for i in window.items].index("Anniversaire"))
    ok = wait_for(app, lambda: sum(1 for t in window.grid.tiles if t._pixmap) >= 10, 120)
    filled = sum(1 for t in window.grid.tiles if t._pixmap)
    check(ok, f"10 vignettes affichées (obtenu {filled})")
    check(all(t.caption.text() for t in window.grid.tiles[:10]),
          "chaque case indique son horodatage et son fichier")

    # ------------------------------------------------------------- actions
    print("\n[3] Déplacement par touche")
    window.show_item(0)
    first = window.current
    first_name = first.name
    QTest.keyClick(window, Qt.Key_1)
    settle(app, window)
    moved = tri / "2019" / first_name
    check(moved.exists(), f"« {first_name} » déplacé dans tri/2019")
    check(not (root / first_name).exists(), "supprimé de la racine")
    check(window.stats["moved"] == 1, "compteur de déplacements incrémenté")
    check(window.index == 1, "passage automatique à l'élément suivant")

    print("\n[4] Annulation (Ctrl+Z)")
    QTest.keyClick(window, Qt.Key_Z, Qt.ControlModifier)
    settle(app, window)
    check((root / first_name).exists(), f"« {first_name} » revenu à sa place")
    check(not moved.exists(), "plus rien dans tri/2019")
    check(window.stats["moved"] == 0, "compteur remis à zéro")

    print("\n[5] Passer (Espace)")
    index_before = window.index
    QTest.keyClick(window, Qt.Key_Space)
    pump(app, 0.3)
    check(window.index == index_before + 1, "Espace passe à l'élément suivant")
    check(window.stats["skipped"] == 1, "compteur de « passés » incrémenté")

    print("\n[6] Suppression")
    target = window.current
    target_name = target.name
    QTest.keyClick(window, Qt.Key_Delete)
    settle(app, window)
    check(not (root / target_name).exists(), f"« {target_name} » retiré de la racine")
    check((sandbox / "_TRASH" / target_name).exists(), "déplacé dans la corbeille locale")
    check(window.stats["deleted"] == 1, "compteur de suppressions incrémenté")

    print("\n[7] Annulation d'une suppression réversible")
    QTest.keyClick(window, Qt.Key_Z, Qt.ControlModifier)
    settle(app, window)
    check((root / target_name).exists(), f"« {target_name} » restauré")
    check(window.stats["deleted"] == 0, "compteur de suppressions remis à zéro")

    print("\n[8] Collision de noms")
    (tri / "2019" / first_name).mkdir(parents=True, exist_ok=True)
    window.show_item(0)
    QTest.keyClick(window, Qt.Key_1)
    settle(app, window)
    check((tri / "2019" / f"{first_name} (2)").exists(),
          "le doublon est suffixé « (2) » au lieu d'écraser")
    QTest.keyClick(window, Qt.Key_Z, Qt.ControlModifier)
    settle(app, window)
    shutil.rmtree(tri / "2019" / first_name, ignore_errors=True)

    # ---------------------------------------------------------- mode fichier
    print("\n[9] Mode fichier (racine remplie de vidéos)")
    window.start_root(flat)
    ok = wait_for(app, lambda: not window.scanning and len(window.items) == 4, 60)
    check(ok, f"4 vidéos listées (obtenu {len(window.items)})")
    check(window.mode == MODE_FILES, f"mode détecté = fichiers (obtenu {window.mode})")
    ok = wait_for(app, lambda: bool(window.current.info.get("duration")), 30)
    check(ok, "durée lue par ffprobe")
    check(window.current.info.get("width") == 320, "résolution lue par ffprobe")
    check(window.viewer.currentWidget() is window.single, "lecteur plein cadre affiché")

    ok = wait_for(app, lambda: sum(1 for t in window.single.tiles if t._pixmap) >= 10, 90)
    check(ok, "pellicule de 10 images pour la vidéo courante")
    plan_file = window.plans.get(str(window.current.path), [])
    check(len({round(ts, 2) for _, ts in plan_file}) == 10,
          "10 instants distincts dans la même vidéo")

    print("\n[10] Déplacement d'un fichier seul")
    video_name = window.current.name
    QTest.keyClick(window, Qt.Key_2)
    settle(app, window)
    check((tri / "2020" / video_name).exists(), f"« {video_name} » déplacé dans tri/2020")
    check(not (flat / video_name).exists(), "fichier libéré par le lecteur puis déplacé")

    print("\n[11] Fin de parcours")
    for _ in range(5):
        QTest.keyClick(window, Qt.Key_Space)
        settle(app, window, 10)
    check(window.stack.currentIndex() == 2, "page de fin affichée après le dernier élément")

    check_new_features(app, window, base, root, flat, tri)

    window.close()
    pump(app, 0.3)

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} vérifications réussies")
    if FAILURES:
        print("Échecs :")
        for failure in FAILURES:
            print("  -", failure)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
