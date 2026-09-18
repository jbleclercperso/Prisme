"""Test d'intégration : analyse, aperçus et actions, sur une vraie arborescence.

Lancement : python tests/test_app.py [dossier_fixture]
L'interface tourne en mode « offscreen », aucune fenêtre ne s'affiche.
"""
from __future__ import annotations

import faulthandler
import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# La console Windows est en cp1252 : sans cela, une flèche ou un accent dans un
# libellé ferait échouer l'affichage du résultat plutôt que le test lui-même.
# line_buffering : rediriger la sortie la met sinon en tampon, et le journal
# retarde alors sur l'exécution — de quoi croire à un blocage qui n'existe pas.
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

# Une interface graphique peut se bloquer sur une boîte de dialogue modale
# qu'aucun test ne ferme. Passé ce délai, on imprime la pile plutôt que
# d'attendre indéfiniment.
faulthandler.enable()
faulthandler.dump_traceback_later(
    int(os.environ.get("VS_TEST_WATCHDOG", "900")), exit=True
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication  # noqa: E402

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


def fresh_root(app, window, base, tri, name: str, count: int) -> Path:
    """Racine jetable peuplee de `count` videos, pour une etape qui en consomme."""
    folder = base / name
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    sources = sorted(tri.rglob("*.mp4"))
    for position in range(count):
        shutil.copy2(sources[position % len(sources)], folder / f"{name}_{position}.mp4")
    window.start_root(folder)
    wait_for(app, lambda: not window.scanning and len(window.items) == count, 60)
    return folder


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
    check(window.item_parent.text() == flat.name,
          f"mode fichier : dossier affiché (obtenu {window.item_parent.text()!r})")
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
    check(KEY_ORDER.startswith("6789azertyuiop"),
          "les destinations commencent à 6, puis suivent l'ordre AZERTY")
    check(len(KEY_ORDER) == 30,
          f"30 destinations possibles (obtenu {len(KEY_ORDER)})")
    check(not set(KEY_ORDER) & set("012345"),
          "les chiffres 0 à 5 restent à la notation")
    many = [
        {"key": KEY_ORDER[i], "label": f"dest{i}", "path": str(tri / f"d{i}")}
        for i in range(20)
    ]
    window.cfg.set_destinations(many)
    window.commands.rebuild(many, "Corbeille")
    caps = window.commands.layout_.count()
    check(caps == 23, f"les 20 destinations sont toutes affichées (obtenu {caps - 3})")
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

    print("\n[16] Barre de commandes cliquable")
    fresh_root(app, window, base, tri, "clics", 5)
    window.cfg.set_destinations([
        {"key": "6", "label": "Souris", "path": str(tri / "souris")},
    ])
    window.commands.rebuild(window.cfg.destinations, "Corbeille")
    caps = [
        window.commands.layout_.itemAt(i).widget()
        for i in range(window.commands.layout_.count())
    ]
    check(len(caps) == 4,
          f"quatre vignettes : Suppr, Espace, Noter, 1 destination ({len(caps)})")
    check(all(c.cursor().shape() == Qt.PointingHandCursor for c in caps),
          "les vignettes se signalent comme cliquables")
    check("touche 6" in caps[3].toolTip(), "l'infobulle rappelle la touche")

    window.show_item(first_untouched(window))
    name = window.current.name
    index_before = window.index
    QTest.mouseClick(caps[3], Qt.LeftButton)
    settle(app, window)
    check((tri / "souris" / name).exists(),
          f"un clic sur la vignette « 1 » envoie « {name} » vers sa destination")
    check(window.index == index_before + 1, "et enchaîne sur l'élément suivant")

    window.show_item(first_untouched(window))
    skipped_before = window.stats["skipped"]
    QTest.mouseClick(caps[1], Qt.LeftButton)
    pump(app, 0.3)
    check(window.stats["skipped"] == skipped_before + 1,
          "un clic sur « Espace » passe l'élément")

    window.show_item(first_untouched(window))
    trashed = window.current.name
    QTest.mouseClick(caps[0], Qt.LeftButton)
    settle(app, window)
    check(not (window.root / trashed).exists(),
          f"un clic sur « Suppr » supprime « {trashed} »")

    # Relâcher en dehors de la vignette ne doit rien déclencher.
    window.show_item(first_untouched(window))
    intact = window.current.name
    QTest.mousePress(caps[3], Qt.LeftButton)
    QTest.mouseRelease(caps[3], Qt.LeftButton, Qt.NoModifier,
                       QPoint(caps[3].width() + 40, 5))
    settle(app, window, 5)
    check(not (tri / "souris" / intact).exists(),
          "un clic relâché en dehors est sans effet")

    print("\n[17] Transfert en tâche de fond")
    import videosorter.actions as vs_actions
    real_move = vs_actions.move_to
    started = {"at": 0.0}

    def slow_move(src, dest):
        started["at"] = time.time()
        time.sleep(1.2)          # simule une copie vers un autre disque
        return real_move(src, dest)

    # Racine dédiée : il faut un élément suivant pour vérifier l'enchaînement.
    fresh_root(app, window, base, tri, "fond_src", 2)

    vs_actions.move_to = slow_move
    try:
        window.show_item(first_untouched(window))
        slow_name = window.current.name
        dest_dir = tri / "fond"
        window.cfg.set_destinations([{"key": "6", "label": "Fond", "path": str(dest_dir)}])
        index_before = window.index
        begin = time.time()
        QTest.keyClick(window, Qt.Key_6)
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

    print("\n[18] Durée et résolution sur les vignettes")
    from videosorter.scan import human_resolution
    check(human_resolution(240) == "240p", "240 -> 240p")
    check(human_resolution(1080) == "1080p", "1080 -> 1080p")
    check(human_resolution(352) == "360p", "352 est ramené au standard le plus proche")
    check(human_resolution(2160) == "4K", "2160 -> 4K")
    check(human_resolution(0) == "", "hauteur inconnue : rien d'affiché")

    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 1, 60)
    window.show_item(0)
    wait_for(app, lambda: window.grid.tiles[0].duration > 0, 90)
    tile = window.grid.tiles[0]
    check(tile.duration > 0, f"durée remontée jusqu'à la vignette ({tile.duration:.1f} s)")
    check(tile.height_px == 240, f"hauteur remontée ({tile.height_px})")
    check(not tile.duration_chip.isHidden(), "pastille de durée affichée")
    check(tile.duration_chip.text() == "0:06", f"durée totale, pas l'instant de l'aperçu "
          f"(obtenu {tile.duration_chip.text()!r})")
    check(tile.duration_chip.x() > tile.width() / 2, "pastille placée en haut à droite")
    check(tile.caption.text().startswith("240p"),
          f"légende commençant par la résolution (obtenu {tile.caption.text()!r})")
    check(".mp4" in tile.caption.text(), "le nom du fichier reste dans la légende")

    # Pendant un déplacement à la molette, la pastille situe la lecture.
    tile.show_position(3.0)
    check(" / " in tile.duration_chip.text(),
          f"molette : position et durée (obtenu {tile.duration_chip.text()!r})")
    tile.show_duration()
    check(tile.duration_chip.text() == "0:06", "la durée revient une fois le survol fini")

    print("\n[19] Bouton de son")
    check(window.root_bar.mute.text() == "Son coupé", "état initial visible dans l'entête")
    QTest.mouseClick(window.root_bar.mute, Qt.LeftButton)
    pump(app, 0.3)
    check(window.cfg["muted"] is False, "un clic réactive le son")
    check(window.root_bar.mute.text() == "Son actif", "le bouton reflète le nouvel état")
    check(window.grid.audio.isMuted() is False, "le lecteur suit")
    QTest.keyClick(window, Qt.Key_M, Qt.ControlModifier)
    pump(app, 0.3)
    check(window.cfg["muted"] is True, "Ctrl+M recoupe le son")
    check(window.root_bar.mute.text() == "Son coupé", "et le bouton se remet à jour")

    print("\n[20] Filtre par nom")
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    total = len(window.all_items)
    names = [item.name for item in window.all_items]
    check(total >= 3, f"{total} éléments avant filtrage : {names}")

    window.apply_filter("anniv", "")
    pump(app, 0.3)
    check([i.name for i in window.items] == ["Anniversaire"],
          f"« contient » ne garde que la correspondance ({[i.name for i in window.items]})")
    check(len(window.all_items) == total, "la liste complète est conservée derrière")
    check("filtrés" in window.root_bar.counter.text(),
          "le compteur signale que des éléments sont masqués")
    check("masqué" in window.filter_bar.count.text(), "et la barre de filtre aussi")

    window.apply_filter("", "melange")
    pump(app, 0.3)
    kept = [i.name for i in window.items]
    check("Melange" not in kept, f"« exclure » retire la correspondance ({kept})")
    check(len(kept) == total - 1, "et ne retire rien d'autre")

    # Le cas décrit : des dossiers préfixés que l'on veut écarter.
    plus_dir = root / "+a_ignorer"
    plus_dir.mkdir(exist_ok=True)
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 4, 60)
    window.apply_filter("", "+")
    pump(app, 0.3)
    check(all(not i.name.startswith("+") for i in window.items),
          "les dossiers commençant par « + » sont écartés")
    check(any(i.name.startswith("+") for i in window.all_items),
          "mais ils restent dans la liste complète")

    window.apply_filter("", "")
    pump(app, 0.3)
    check(len(window.items) == len(window.all_items), "effacer le filtre rend tout")
    check(window.filter_bar.count.text() == "", "et éteint l'indicateur")

    window.apply_filter("zzz-introuvable", "")
    pump(app, 0.3)
    check(window.items == [], "un filtre sans correspondance vide la liste")
    check("Aucun élément" in window.item_title.text(),
          f"et le dit clairement (obtenu {window.item_title.text()!r})")
    window.apply_filter("", "")
    pump(app, 0.3)
    check(len(window.items) > 0, "et l'on peut repartir de là")

    check(window.cfg["filter_exclude"] == "", "le filtre est mémorisé dans la configuration")
    window.filter_bar.set_terms("abc", "def")
    window.filter_bar._emit()
    pump(app, 0.5)
    check(window.cfg["filter_include"] == "abc" and window.cfg["filter_exclude"] == "def",
          "la saisie alimente bien la configuration")
    window.filter_bar.clear()
    pump(app, 0.5)
    check(window.cfg["filter_include"] == "", "le bouton Effacer remet tout à zéro")

    print("\n[21] Entrer dans un dossier, puis en revenir")
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    position = [i.name for i in window.items].index("Anniversaire")
    window.show_item(position)
    parent_item = window.current
    check(window.mode == MODE_FOLDERS, "on part du mode dossiers")
    check(not window.root_bar.enter.isHidden(), "le bouton « Entrer » est proposé")
    check(window.root_bar.up.isHidden(), "pas de « Remonter » au niveau racine")

    QTest.mouseClick(window.root_bar.enter, Qt.LeftButton)
    ok = wait_for(app, lambda: not window.scanning and window.root == parent_item.path, 60)
    check(ok, "la racine devient le dossier sur lequel on était")
    check(window.mode == MODE_FILES,
          f"et le mode bascule sur les fichiers (obtenu {window.mode})")
    check(len(window.items) == 12,
          f"les 12 vidéos du dossier sont listées (obtenu {len(window.items)})")
    check(all(i.kind == MODE_FILES for i in window.items), "ce sont bien des fichiers")
    check(not window.root_bar.up.isHidden(), "« Remonter » apparaît")
    check("niveau 2" in window.root_bar.root_label.text(),
          f"la profondeur est indiquée (obtenu {window.root_bar.root_label.text()!r})")

    # On trie une vidéo à l'intérieur, pour vérifier que tout fonctionne en profondeur.
    inner_dest = tri / "interieur"
    window.cfg.set_destinations([{"key": "6", "label": "Intérieur", "path": str(inner_dest)}])
    inner_name = window.current.name
    QTest.keyClick(window, Qt.Key_6)
    settle(app, window)
    check((inner_dest / inner_name).exists(),
          f"une vidéo du sous-dossier part vers sa destination ({inner_name})")

    QTest.mouseClick(window.root_bar.up, Qt.LeftButton)
    ok = wait_for(app, lambda: not window.scanning and window.root == root, 60)
    check(ok, "« Remonter » ramène au dossier parent")
    check(window.mode == MODE_FOLDERS, "et retrouve le mode dossiers")
    check(window.current is not None and window.current.name == "Anniversaire",
          f"sur le dossier d'où l'on était parti (obtenu "
          f"{window.current.name if window.current else None})")
    check(window.root_bar.up.isHidden(), "« Remonter » disparaît de nouveau")

    print("\n[22] Profondeur et retour par Échap")
    QTest.keyClick(window, Qt.Key_Down, Qt.ControlModifier)
    wait_for(app, lambda: not window.scanning and window.root == parent_item.path, 60)
    check(len(window.levels) == 1, "Ctrl+↓ entre aussi dans le dossier")
    QTest.keyClick(window, Qt.Key_Escape)
    ok = wait_for(app, lambda: not window.scanning and window.root == root, 60)
    check(ok, "Échap remonte au lieu de quitter le tri")
    check(window.stack.currentIndex() == 1, "on reste dans l'écran de tri")
    check(window.levels == [], "la pile de navigation est vidée")
    QTest.keyClick(window, Qt.Key_Escape)
    pump(app, 0.4)
    check(window.stack.currentIndex() == 0,
          "au niveau racine, Échap quitte bien le tri")

    print("\n[23] Un dossier sans vidéo directe se parcourt en dossiers")
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    position = [i.name for i in window.items].index("Sous-dossiers")
    window.show_item(position)
    QTest.mouseClick(window.root_bar.enter, Qt.LeftButton)
    wait_for(app, lambda: not window.scanning and window.root.name == "Sous-dossiers", 60)
    check(window.mode == MODE_FOLDERS,
          f"pas de vidéo directe : on descend en mode dossiers (obtenu {window.mode})")
    check([i.name for i in window.items] == ["interne"],
          f"le sous-dossier est listé ({[i.name for i in window.items]})")
    QTest.mouseClick(window.root_bar.enter, Qt.LeftButton)
    wait_for(app, lambda: not window.scanning and window.root.name == "interne", 60)
    check(len(window.levels) == 2, "on peut descendre de plusieurs niveaux")
    check(window.mode == MODE_FILES, "et le dernier niveau contient les vidéos")
    QTest.keyClick(window, Qt.Key_Up, Qt.ControlModifier)
    wait_for(app, lambda: not window.scanning and window.root.name == "Sous-dossiers", 60)
    check(len(window.levels) == 1, "Ctrl+↑ remonte d'un seul niveau")

    print("\n[24] Cache d'analyse")
    from videosorter.scan_cache import CACHE, signature

    CACHE.data = {}
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 3, 60)
    first_pass = window.scan_thread
    check(first_pass.reused == 0, "premier passage : rien à réutiliser")
    check(first_pass.rescanned == len(window.all_items),
          f"tout est analysé ({first_pass.rescanned})")
    check(len(CACHE.data) == len(window.all_items),
          f"chaque dossier est mémorisé ({len(CACHE.data)})")
    reference = {i.name: (i.size, i.file_count, i.video_count) for i in window.all_items}

    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 3, 60)
    second_pass = window.scan_thread
    check(second_pass.reused == len(window.all_items),
          f"second passage : tout vient du cache ({second_pass.reused} réutilisés, "
          f"{second_pass.rescanned} réanalysés)")
    again = {i.name: (i.size, i.file_count, i.video_count) for i in window.all_items}
    check(again == reference, "et les chiffres sont identiques à l'analyse complète")
    cached_videos = {i.name: len(i.videos) for i in window.all_items}
    check(all(count > 0 for name, count in cached_videos.items()
              if reference[name][2] > 0),
          "les chemins des vidéos sont restitués")

    # Un fichier ajouté doit invalider le dossier concerné, et lui seul.
    victim = next(i for i in window.all_items if i.name == "Anniversaire")
    (victim.path / "ajout.mp4").write_bytes(b"x" * 1024)
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 3, 60)
    third_pass = window.scan_thread
    check(third_pass.rescanned == 1,
          f"un seul dossier réanalysé après modification (obtenu {third_pass.rescanned})")
    refreshed = next(i for i in window.all_items if i.name == "Anniversaire")
    check(refreshed.file_count == reference["Anniversaire"][1] + 1,
          "et ses chiffres sont à jour")

    # Une modification au deuxième niveau doit compter aussi.
    nested = next(i for i in window.all_items if i.name == "Sous-dossiers")
    (nested.path / "interne" / "ajout2.mp4").write_bytes(b"x" * 512)
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 3, 60)
    check(window.scan_thread.rescanned == 1,
          "une modification dans un sous-dossier invalide le dossier parent")

    # Ctrl+R ignore le cache et relit tout.
    window.refresh_root()
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 3, 60)
    check(window.scan_thread.reused == 0,
          f"Ctrl+R relit tout depuis le disque ({window.scan_thread.rescanned} dossiers)")

    check(signature(root / "Anniversaire") != "", "empreinte calculable sur un dossier")
    check(signature(root / "inexistant") == "", "et vide sur un dossier absent")
    window.preview.quiesce()
    for leftover in ((victim.path / "ajout.mp4"),
                     (nested.path / "interne" / "ajout2.mp4")):
        for _ in range(10):
            try:
                leftover.unlink()
                break
            except PermissionError:
                time.sleep(0.2)
            except FileNotFoundError:
                break

    print("\n[25] Entête : arborescence complète et durée près du titre")
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    position = [i.name for i in window.items].index("Anniversaire")
    window.show_item(position)
    check(window.item_parent.text() == root.name,
          f"au premier niveau, la racine seule (obtenu {window.item_parent.text()!r})")

    window.enter_current()
    wait_for(app, lambda: not window.scanning and window.mode == MODE_FILES, 60)
    wait_for(app, lambda: bool(window.current.info.get("duration")), 30)
    window.show_item(0)
    crumbs = window.item_parent.text()
    check(crumbs == f"{root.name}  ›  Anniversaire",
          f"chaîne complète depuis la racine (obtenu {crumbs!r})")
    from videosorter.scan import human_duration
    expected = human_duration(window.current.info["duration"])
    title = window.item_title.text()
    check("—" in title and title.endswith(expected),
          f"durée accolée au titre (attendu …{expected}, obtenu {title!r})")
    check(window.current.name in title, "le nom du fichier reste en tête")
    info_line = window.item_subtitle.text()
    check("240p" in info_line, f"résolution nommée dans les infos (obtenu {info_line!r})")
    check("320×240" in info_line, "dimensions exactes conservées")
    check("Ko" in info_line or "Mo" in info_line, "poids présent")
    check("modifié le" in info_line, "date présente")

    # Trois niveaux : la chaîne doit tous les montrer.
    window.go_up()
    wait_for(app, lambda: not window.scanning and window.mode == MODE_FOLDERS, 60)
    position = [i.name for i in window.items].index("Sous-dossiers")
    window.show_item(position)
    window.enter_current()
    wait_for(app, lambda: not window.scanning and window.root.name == "Sous-dossiers", 60)
    window.enter_current()
    wait_for(app, lambda: not window.scanning and window.root.name == "interne", 60)
    window.show_item(0)
    crumbs = window.item_parent.text()
    check(crumbs == f"{root.name}  ›  Sous-dossiers  ›  interne",
          f"trois niveaux affichés (obtenu {crumbs!r})")
    while window.go_up():
        wait_for(app, lambda: not window.scanning, 60)

    print("\n[26] Boîte de destinations : ordre, renumérotation, réinitialisation")
    from videosorter.widgets import DestinationsDialog

    dialog = DestinationsDialog([
        {"key": "6", "label": "2019", "path": str(tri / "2019")},
        {"key": "7", "label": "2020", "path": str(tri / "2020")},
        {"key": "8", "label": "2021", "path": str(tri / "2021")},
    ])
    rows = dialog._rows()
    check(len(rows) == 3, f"trois lignes reprises (obtenu {len(rows)})")
    check(rows[0].text(DestinationsDialog.COL_GRIP) == "⠿",
          "chaque ligne porte une poignée")
    check(bool(rows[0].flags() & Qt.ItemIsDragEnabled), "et se laisse déplacer")
    check(dialog.tree.dragDropMode() == QAbstractItemView.InternalMove,
          "le glisser-déposer réorganise la liste")
    check(dialog.tree.selectionMode() == QAbstractItemView.ExtendedSelection,
          "plusieurs lignes se sélectionnent à la fois")

    # L'ordre de la liste est celui des boutons : on inverse et on vérifie.
    moved = dialog.tree.takeTopLevelItem(0)
    dialog.tree.addTopLevelItem(moved)
    order = [d["label"] for d in dialog.result_destinations()]
    check(order == ["2020", "2021", "2019"],
          f"le résultat suit l'ordre affiché (obtenu {order})")
    keys = [d["key"] for d in dialog.result_destinations()]
    check(keys == ["7", "8", "6"], f"les touches suivent leur destination ({keys})")

    dialog.renumber()
    renumbered = dialog.result_destinations()
    check([d["key"] for d in renumbered] == ["6", "7", "8"],
          "« Renuméroter » réattribue les touches dans l'ordre")
    check([d["label"] for d in renumbered] == ["2020", "2021", "2019"],
          "sans toucher à l'ordre ni aux libellés")

    added = dialog._add_paths([tri / "2019", tri / "souris"])
    check(added == 1, f"un dossier déjà présent n'est pas ajouté deux fois ({added})")
    check(len(dialog._rows()) == 4, "et le nouveau prend la suite")
    check(dialog._rows()[3].text(DestinationsDialog.COL_KEY) == "9",
          "avec la première touche libre")

    dialog.tree.clear()
    check(dialog.result_destinations() == [], "la réinitialisation vide bien la liste")

    print("\n[27] Sélection multiple de dossiers")
    from videosorter.widgets import pick_folders
    import inspect
    source = inspect.getsource(pick_folders)
    check("DontUseNativeDialog" in source,
          "le sélecteur Qt remplace celui de Windows, qui ne sait pas sélectionner plusieurs dossiers")
    check("ExtendedSelection" in source, "les vues internes acceptent la sélection multiple")

    print("\n[28] Notation de 0 à 5 étoiles")
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    window.show_item(first_untouched(window))
    target = window.current
    check(window.ratings.get(target.path) == 0, "un élément démarre sans note")
    window.rate_current(4)
    check(window.ratings.get(target.path) == 4, "une note s'attribue")
    check(window.stars.value == 4, "et s'affiche dans la bande d'étoiles")
    window.rate_current(4)
    check(window.ratings.get(target.path) == 0,
          "rappuyer sur la même valeur efface la note")
    window.rate_current(2)
    QTest.keyClick(window, Qt.Key_0)
    check(window.ratings.get(target.path) == 0, "la touche 0 efface la note")
    QTest.keyClick(window, Qt.Key_5)
    check(window.ratings.get(target.path) == 5, "la touche 5 attribue cinq étoiles")

    # La note doit suivre l'élément quand il change de place.
    moved_dir = tri / "notes"
    window.cfg.set_destinations([{"key": "6", "label": "Notes", "path": str(moved_dir)}])
    name = target.name
    QTest.keyClick(window, Qt.Key_6)
    settle(app, window)
    check(window.ratings.get(moved_dir / name) == 5,
          "la note suit l'élément déplacé")
    check(window.ratings.get(target.path) == 0, "et ne reste pas sur l'ancien chemin")

    window.ratings.flush()
    from videosorter.ratings import Ratings
    reloaded = Ratings(path=window.ratings.path)
    check(reloaded.get(moved_dir / name) == 5, "la note survit à un redémarrage")

    print("\n[29] Vue planche")
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    window.toggle_board(True)
    pump(app, 0.5)
    check(window.board_view, "la planche est active")
    check(window.viewer.currentWidget() is window.board, "et occupe la zone centrale")
    check(len(window.board.items) == len(window.items),
          f"une carte par élément ({len(window.board.items)})")
    visible = [c for c in window.board.cards if not c.isHidden()]
    check(len(visible) == len(window.items), "toutes les cartes sont affichées")
    check(visible[0].name.text() != "", "chaque carte porte son nom")
    check("vidéo" in visible[0].meta.text(), "et ses chiffres")

    ok = wait_for(app, lambda: any(c._pixmap for c in window.board.cards), 120)
    check(ok, "les images des cartes arrivent")

    # Noter depuis une carte.
    window.on_board_rate(0, 3)
    check(window.ratings.get(window.items[0].path) == 3, "on note depuis une carte")
    check(window.board.cards[0].stars.value == 3, "l'étoile de la carte suit")

    # Un clic sur une carte de dossier ouvre, il ne déplace rien.
    before = str(window.root)
    position = [i.name for i in window.items].index("Anniversaire")
    window.on_board_open(position)
    ok = wait_for(app, lambda: not window.scanning and window.root.name == "Anniversaire", 60)
    check(ok, "un clic sur une carte ouvre le dossier")
    check((Path(before) / "Anniversaire").exists(), "sans le déplacer")

    print("\n[30] L'arborescence navigue en planche, envoie en fiche")
    window.cfg["tree_root"] = str(tri)
    window.toggle_tree(True)
    window.toggle_board(True)
    pump(app, 0.3)
    index = window.tree.model.index(str(tri / "2019"))
    window.tree.view.clicked.emit(index)
    ok = wait_for(app, lambda: not window.scanning and window.root.name == "2019", 60)
    check(ok, "en planche, un clic dans l'arbre ouvre le dossier visé")
    check(window.transfers.active == 0, "et ne déclenche aucun transfert")
    window.toggle_tree(False)
    window.toggle_board(False)
    pump(app, 0.3)
    check(not window.board_view, "retour à la fiche unique")

    print("\n[31] Zoom au pointeur")
    player = window.single
    player.resize(800, 500)
    player.reset_zoom()
    check(player.zoom == 1.0, "on part sans zoom")
    wheel(player, 2, Qt.ControlModifier)
    pump(app, 0.2)
    check(player.zoom > 1.0, f"Ctrl+molette agrandit (×{player.zoom:.2f})")
    widened = player.video.width()
    check(widened > player.video_area.width(),
          "l'image déborde son cadre, qui la rogne")
    wheel(player, -2, Qt.ControlModifier)
    pump(app, 0.2)
    check(player.zoom == 1.0, "et l'on revient exactement à l'échelle d'origine")
    wheel(player, -3, Qt.ControlModifier)
    check(player.zoom == 1.0, "sans jamais réduire en deçà")
    for _ in range(20):
        wheel(player, 3, Qt.ControlModifier)
    check(player.zoom <= 6.0, f"ni grossir sans fin (×{player.zoom:.1f})")
    player.reset_zoom()

    print("\n[32] Tirage au hasard")
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    seen = set()
    for _ in range(25):
        window.pick_random()
        seen.add(window.index)
    check(len(seen) > 1, f"le tirage visite plusieurs éléments ({len(seen)})")
    check(all(0 <= i < len(window.items) for i in seen), "toujours dans la liste")

    print("\n[33] Densité de la planche et clic sur toute la carte")
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    window.toggle_board(True)
    window.resize(1400, 900)
    window.show()
    pump(app, 0.5)
    window.set_board_columns(3)
    pump(app, 0.3)
    check(window.board.columns == 3, "le sélecteur change le nombre de colonnes")
    wide = window.board.cards[0].width()
    window.set_board_columns(6)
    pump(app, 0.3)
    narrow = window.board.cards[0].width()
    check(narrow < wide, f"moins de colonnes donne de plus grandes cartes ({wide} > {narrow})")
    check(window.cfg["board_columns"] == 6, "le choix est mémorisé")

    card = window.board.cards[0]
    for child in (card.image, card.name, card.meta):
        check(child.testAttribute(Qt.WA_TransparentForMouseEvents),
              f"un clic traverse « {child.objectName() or 'image'} »")
    opened = []
    card.opened.connect(opened.append)
    QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier,
                     QPoint(card.width() // 2, 30))
    check(opened == [0], "un clic sur l'image ouvre bien la carte")

    print("\n[34] Filtres chiffrés de la planche")
    filters = window.advanced_filter
    check(not filters.isHidden(), "les filtres chiffrés accompagnent la planche")
    check(not filters.is_active(), "et ne masquent rien au départ")
    check(filters.duration_op.itemText(1) == "plus longue que",
          "les opérateurs sont écrits en toutes lettres")
    check(filters.duration_op.itemText(2) == "plus courte que", "dans les deux sens")

    window.ratings.data.clear()
    window.apply_filter(window.cfg["filter_include"], window.cfg["filter_exclude"])
    pump(app, 0.3)
    total = len(window.items)
    window.on_board_rate(0, 4)
    filters.stars_value.setCurrentIndex(filters.stars_value.findData(4))
    pump(app, 0.6)
    check(len(window.items) == 1,
          f"filtrer sur 4 étoiles ne garde que l'élément noté ({len(window.items)})")
    filters.reset()
    pump(app, 0.6)
    check(len(window.items) == total, "« Tout afficher » rend la liste entière")

    # La durée s'appuie sur ce que le sondage a appris.
    wait_for(app, lambda: any(c._pixmap for c in window.board.cards), 90)
    filters.duration_op.setCurrentIndex(2)          # plus courte que
    filters.duration_value.setText("120")
    pump(app, 0.6)
    check(len(window.items) <= total, "un filtre de durée restreint ou laisse tel quel")
    filters.duration_op.setCurrentIndex(1)          # plus longue que
    filters.duration_value.setText("600")
    pump(app, 0.6)
    check(len(window.items) < total,
          f"aucun dossier ne dure plus de 10 heures ({len(window.items)} restants)")
    filters.reset()
    pump(app, 0.6)
    check(len(window.items) == total,
          f"réinitialiser après un filtre vidant la liste la rétablit "
          f"({len(window.items)} sur {total})")

    print("\n[35] Précédent et temps restant")
    window.toggle_board(False)
    # Le clic de l'étape 33 a réellement ouvert un dossier : on revient à la
    # racine avant d'éprouver la navigation.
    window.start_root(root)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    pump(app, 0.3)
    start = str(window.root)
    # Un dossier quelconque encore présent : les étapes précédentes en ont
    # déplacé plusieurs, viser un nom précis rendrait le test fragile.
    position = next(i for i, item in enumerate(window.items)
                    if item.kind == MODE_FOLDERS and Path(item.path).is_dir())
    entered = window.items[position].name
    window.show_item(position)
    window.enter_current()
    wait_for(app, lambda: not window.scanning and window.root.name == entered, 60)
    check(window.root_bar.back.isEnabled(), "« Précédent » devient disponible")
    check(window.go_back(), "le retour aboutit")
    ok = wait_for(app, lambda: not window.scanning and str(window.root) == start, 60)
    check(ok, "et ramène à l'endroit précédent")

    player = window.single
    player.resize(900, 600)
    player._on_position(0)
    check(player.progress_rail.height() >= 8,
          f"la barre d'avancement fait {player.progress_rail.height()} px")
    check(not player.progress_rail.isHidden(), "et reste visible")
    window.start_root(flat) if flat.exists() else None
    wait_for(app, lambda: not window.scanning, 30)
    if window.items and window.current.kind == MODE_FILES:
        wait_for(app, lambda: player.player.duration() > 0, 30)
        player.player.setPosition(1000)
        pump(app, 0.4)
        check(player.remaining.text().startswith("−"),
              f"le temps restant s'affiche (obtenu {player.remaining.text()!r})")
        check(not player.remaining.isHidden(), "et il est visible")

    print("\n[36] Lecteur intégré au double-clic")
    window.start_root(flat)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 1, 60)
    video = str(window.current.path)
    check(window.focus.isHidden(), "le lecteur intégré reste caché tant qu'on ne l'ouvre pas")
    window.play_in_app(video, 1.0)
    pump(app, 0.6)
    check(not window.focus.isHidden(), "il s'ouvre sur demande")
    check(window.focus.title.text() == Path(video).name, "et annonce le fichier lu")
    check(window.focus.player.source().toLocalFile().endswith(Path(video).name),
          "c'est bien la vidéo demandée, lue dans l'application")
    ok = wait_for(app, lambda: window.focus.player.duration() > 0, 30)
    check(ok, "la lecture démarre")
    pump(app, 0.4)
    check(window.focus.remaining.text().startswith("−"),
          f"le temps restant s'affiche (obtenu {window.focus.remaining.text()!r})")
    check(window.focus.remaining.y() < 100, "en haut de l'image")
    check(window.focus.remaining.x() > window.focus.width() // 2, "et à droite")

    print("\n[37] Zoom au clic et retour à la taille normale")
    focus = window.focus
    focus.resize(1200, 800)
    pump(app, 0.2)
    check(focus.zoom == 1.0, "on part sans zoom")
    # Molette seule : on parcourt, on ne zoome pas.
    wheel(focus, 2)
    pump(app, 0.2)
    check(focus.zoom == 1.0, "la molette seule ne zoome pas")
    # Bouton gauche maintenu : on zoome.
    center = focus.rect().center()
    event = QWheelEvent(
        QPointF(center), QPointF(focus.mapToGlobal(center)),
        QPoint(0, 0), QPoint(0, 240),
        Qt.LeftButton, Qt.NoModifier, Qt.NoScrollPhase, False,
    )
    QApplication.sendEvent(focus, event)
    pump(app, 0.2)
    check(focus.zoom > 1.0, f"clic gauche + molette agrandit (×{focus.zoom:.2f})")
    QTest.mouseClick(focus, Qt.RightButton)
    pump(app, 0.2)
    check(focus.zoom == 1.0, "un clic droit ramène à la taille normale")

    QTest.keyClick(focus, Qt.Key_Escape)
    pump(app, 0.4)
    check(window.focus.isHidden(), "Échap referme le lecteur")
    check(not window.focus.player.source().isValid(),
          "et relâche le fichier, sans quoi il resterait verrouillé")

    print("\n[38] Temps restant dans les aperçus")
    check(window.grid.remaining is not None, "la grille d'aperçus en a un")
    check(window.board.remaining is not None, "la planche aussi")
    check(window.grid.remaining.isHidden(), "masqué tant que rien ne se lit")

    probe_dialog = DestinationsDialog([])
    picked = [tri / "2019", tri / "2020", tri / "2021"]
    check(probe_dialog._add_paths(picked) == 3,
          "trois dossiers choisis d'un coup donnent trois raccourcis")
    results = probe_dialog.result_destinations()
    check([d["key"] for d in results] == ["6", "7", "8"],
          f"chacun reçoit une touche distincte ({[d['key'] for d in results]})")
    check([d["label"] for d in results] == ["2019", "2020", "2021"],
          "et le nom du dossier sert de libellé")


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
    import videosorter.ratings as vs_ratings
    vs_ratings.RATINGS_PATH = sandbox / "ratings.json"
    import videosorter.scan_cache as vs_scan_cache
    vs_scan_cache.CACHE.path = sandbox / "scan-cache.json"
    vs_scan_cache.CACHE.data = {}

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = Config(path=sandbox / "config.json")
    cfg["delete_mode"] = "local_trash"
    cfg.set_destinations([
        {"key": "6", "label": "2019", "path": str(tri / "2019")},
        {"key": "7", "label": "2020", "path": str(tri / "2020")},
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

    plan_anniv = window.plans.get(f"{anniv.path}@0", [])
    check(len(plan_anniv) == 10, f"Anniversaire : 10 aperçus (obtenu {len(plan_anniv)})")
    check(len({entry[0] for entry in plan_anniv}) == 10,
          "Anniversaire : 10 vidéos distinctes échantillonnées")

    melange = by_name.get("Melange")
    plan_mel = window.plans.get(f"{melange.path}@0", [])
    check(len(plan_mel) == 2,
          f"Melange (2 vidéos) : 2 aperçus, pas dix (obtenu {len(plan_mel)})")
    check(len({entry[0] for entry in plan_mel}) == 2, "Melange : une image par vidéo")
    check(window.grid.visible_count in (0, 2, 10),
          "la grille n'affiche que les cases utiles")
    check(all(entry[1] > 0 for entry in plan_mel), "instants strictement positifs")

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
    QTest.keyClick(window, Qt.Key_6)
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
    check(window.trash.count == 1, f"écarté dans la corbeille de session ({window.trash.count})")
    stored = window.trash.entries[0].stored
    check(Path(stored).exists(), f"et retrouvable sur le disque ({Path(stored).name})")
    check(".videosorter-corbeille" in str(stored),
          "dans un dossier de session place sous la racine triee")
    check(window.stats["deleted"] == 1, "compteur de suppressions incrémenté")

    print("\n[7] Annulation d'une suppression réversible")
    QTest.keyClick(window, Qt.Key_Z, Qt.ControlModifier)
    settle(app, window)
    check((root / target_name).exists(), f"« {target_name} » restauré")
    check(window.stats["deleted"] == 0, "compteur de suppressions remis à zéro")

    print("\n[8] Collision de noms")
    (tri / "2019" / first_name).mkdir(parents=True, exist_ok=True)
    window.show_item(0)
    QTest.keyClick(window, Qt.Key_6)
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
    plan_file = window.plans.get(f"{window.current.path}@0", [])
    check(len({round(entry[1], 2) for entry in plan_file}) == 10,
          "10 instants distincts dans la même vidéo")

    print("\n[10] Déplacement d'un fichier seul")
    video_name = window.current.name
    QTest.keyClick(window, Qt.Key_7)
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
