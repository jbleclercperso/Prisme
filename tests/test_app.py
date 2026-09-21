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
from videosorter.scan import MODE_FILES, MODE_FLAT, MODE_FOLDERS  # noqa: E402
from videosorter.header import (  # noqa: E402
    CONTENT_FOLDERS, CONTENT_VIDEOS, TAB_EDIT, TAB_FOLDERS, TAB_SPLIT,
    TAB_TAGS, TAB_VIDEOS,
    VIEW_BROWSE, VIEW_EDIT,
)
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
    window.start_root(flat, MODE_FLAT)
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
    check(caps == 28, f"les 20 destinations sont toutes affichées (obtenu {caps - 8})")
    # Sur un exemplaire dedie : redimensionner la barre vivante la fait
    # reagencer par sa disposition parente, ce qui fausse la mesure.
    from videosorter.widgets import CommandBar
    probe_bar = CommandBar()
    probe_bar.rebuild(many, "Corbeille")
    narrow = probe_bar.layout_.heightForWidth(400)
    wide = probe_bar.layout_.heightForWidth(2400)
    check(narrow > wide > 0,
          f"la barre passe à la ligne quand elle manque de place "
          f"({narrow} px à 400, {wide} px à 2400)")

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
    # L'arborescence ne montre que les dossiers de tete — ceux prefixes d'un
    # « + ». Ce sont les seules destinations : chercher les bonnes parmi des
    # centaines de dossiers ordinaires invitait a la faute.
    shelf_dest = tri / "+archive"
    shelf_dest.mkdir(exist_ok=True)
    window.cfg["tree_root"] = str(tri)
    window.toggle_tree(True)
    window.tree.set_root(str(tri))
    pump(app, 0.4)
    check(not window.tree.isHidden(), "panneau affiché")
    check(window.tree.root == str(tri), "arborescence enracinée sur le dossier de tri")
    index = window.tree.model.index(str(shelf_dest))
    check(index.isValid(), "les dossiers de tête sont listés")
    check(not window.tree.model.index(str(tri / "2019")).isValid(),
          "les dossiers ordinaires, non")

    window.show_item(first_untouched(window))
    name = window.current.name
    window.tree.view.clicked.emit(index)
    settle(app, window)
    check((shelf_dest / name).exists(),
          f"un clic sur « +archive » envoie « {name} » sans confirmation")
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
    check(len(caps) == 9,
          f"neuf vignettes : Suppr, Espace, 5 notes, effacer, 1 destination "
          f"({len(caps)})")
    check(all(c.cursor().shape() == Qt.PointingHandCursor for c in caps),
          "les vignettes se signalent comme cliquables")
    check("touche 6" in caps[8].toolTip(), "l'infobulle rappelle la touche")

    window.show_item(first_untouched(window))
    name = window.current.name
    index_before = window.index
    QTest.mouseClick(caps[8], Qt.LeftButton)
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
    QTest.mousePress(caps[8], Qt.LeftButton)
    QTest.mouseRelease(caps[8], Qt.LeftButton, Qt.NoModifier,
                       QPoint(caps[8].width() + 40, 5))
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
        check(elapsed < 1.0,
              f"la main est rendue sans subir la copie de 1,2 s ({elapsed:.2f} s)")
        check(window.index == index_before + 1, "on est déjà sur l'élément suivant")
        check(window.transfers.busy, "le transfert se poursuit derrière")
        check(not window.pending_label.isHidden(),
              "un indicateur signale le transfert")
        settle(app, window, 30)
        check((dest_dir / slow_name).exists(), f"« {slow_name} » bien arrivé à destination")
        check(window.pending_label.isHidden(), "indicateur éteint une fois fini")
    finally:
        vs_actions.move_to = real_move

    print("\n[18] Durée et résolution sur les vignettes")
    from videosorter.scan import human_resolution
    check(human_resolution(240) == "240p", "240 -> 240p")
    check(human_resolution(1080) == "1080p", "1080 -> 1080p")
    check(human_resolution(352) == "360p", "352 est ramené au standard le plus proche")
    check(human_resolution(2160) == "4K", "2160 -> 4K")
    check(human_resolution(0) == "", "hauteur inconnue : rien d'affiché")

    window.start_root(root, MODE_FOLDERS)
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
    # Une icone dessinee, pas un emoji : c'est l'infobulle qui porte l'etat.
    check(window.mute_button.toolTip().startswith("Son coupé") and not window.mute_button.icon().isNull(),
          f"le pictogramme dit que le son est coupé (obtenu {window.mute_button.toolTip()!r})")
    QTest.mouseClick(window.mute_button, Qt.LeftButton)
    pump(app, 0.3)
    check(window.cfg["muted"] is False, "un clic réactive le son")
    check(window.mute_button.toolTip().startswith("Son actif"), "il change quand le son revient")
    check(window.single.audio.isMuted() is False, "le lecteur suit")
    check(not hasattr(window.grid, "audio") and not hasattr(window.board, "audio"),
          "les apercus survoles n'ont aucune piste son a decoder")
    QTest.keyClick(window, Qt.Key_M, Qt.ControlModifier)
    pump(app, 0.3)
    check(window.cfg["muted"] is True, "Ctrl+M recoupe le son")
    check(window.mute_button.toolTip().startswith("Son coupé"), "et se remet à jour")

    print("\n[20] Filtre par nom")
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    total = len(window.all_items)
    names = [item.name for item in window.all_items]
    check(total >= 3, f"{total} éléments avant filtrage : {names}")

    window.apply_filter("anniv", "")
    pump(app, 0.3)
    check([i.name for i in window.items] == ["Anniversaire"],
          f"« contient » ne garde que la correspondance ({[i.name for i in window.items]})")
    check(len(window.all_items) == total, "la liste complète est conservée derrière")
    check("filtrés" in window.controls.count.text(),
          f"le compteur signale les éléments masqués "
          f"(obtenu {window.controls.count.text()!r})")
    check(len(window.items) < len(window.all_items), "et la liste est bien réduite")

    window.apply_filter("", "melange")
    pump(app, 0.3)
    kept = [i.name for i in window.items]
    check("Melange" not in kept, f"« exclure » retire la correspondance ({kept})")
    check(len(kept) == total - 1, "et ne retire rien d'autre")

    # Un prefixe quelconque que l'on veut ecarter. « + » ne sert plus d'exemple :
    # il est devenu structurel, ces dossiers etant traverses et non listes.
    marked = root / "zz-a-ignorer"
    marked.mkdir(exist_ok=True)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 4, 60)
    window.apply_filter("", "zz-")
    pump(app, 0.3)
    check(all(not i.name.startswith("zz-") for i in window.items),
          "les dossiers commençant par « zz- » sont écartés")
    check(any(i.name.startswith("zz-") for i in window.all_items),
          "mais ils restent dans la liste complète")

    window.apply_filter("", "")
    pump(app, 0.3)
    # « Tout », c'est tout ce qui se trie : un dossier sans la moindre vidéo
    # n'en fait pas partie, il n'y a rien à y décider.
    sortable = [i for i in window.all_items
                if i.is_tag or i.kind != MODE_FOLDERS or i.video_count]
    check(len(window.items) == len(sortable),
          f"effacer le filtre rend tout ({len(window.items)} sur {len(sortable)})")
    check(all(i.video_count or i.is_tag or i.kind != MODE_FOLDERS
              for i in window.items),
          "et aucun dossier vide ne s'y trouve")
    check("filtrés" not in window.controls.count.text(),
          "et le compteur ne signale plus rien de masqué")

    window.apply_filter("zzz-introuvable", "")
    pump(app, 0.3)
    check(window.items == [], "un filtre sans correspondance vide la liste")
    check("Aucun élément" in window.item_title.text(),
          f"et le dit clairement (obtenu {window.item_title.text()!r})")
    window.apply_filter("", "")
    pump(app, 0.3)
    check(len(window.items) > 0, "et l'on peut repartir de là")

    check(window.cfg["filter_exclude"] == "", "le filtre est mémorisé dans la configuration")
    window.controls.set_terms("abc", "def")
    window.on_controls_changed()
    pump(app, 0.5)
    check(window.cfg["filter_include"] == "abc" and window.cfg["filter_exclude"] == "def",
          "la saisie alimente bien la configuration")
    window.controls.reset()
    pump(app, 0.5)
    check(window.cfg["filter_include"] == "", "le bouton Effacer remet tout à zéro")

    print("\n[21] Entrer dans un dossier, puis en revenir")
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    position = [i.name for i in window.items].index("Anniversaire")
    window.show_item(position)
    parent_item = window.current
    check(window.mode == MODE_FOLDERS, "on part du mode dossiers")
    check(not window.enter_button.isHidden(), "le bouton « Entrer » est proposé")
    check(window.levels == [], "on est bien au niveau racine")

    QTest.mouseClick(window.enter_button, Qt.LeftButton)
    ok = wait_for(app, lambda: not window.scanning and window.root == parent_item.path, 60)
    check(ok, "la racine devient le dossier sur lequel on était")
    check(window.mode == MODE_FLAT,
          f"et l'on bascule sur les vidéos, à plat (obtenu {window.mode})")
    check(len(window.items) == 12,
          f"les 12 vidéos du dossier sont listées (obtenu {len(window.items)})")
    check(all(i.kind == MODE_FILES for i in window.items), "ce sont bien des fichiers")
    check(len(window.levels) == 1, "un niveau est empilé")
    check(window.crumbs.layout_.count() >= 3,
          f"le fil d'Ariane montre la descente ({window.crumbs.layout_.count()})")

    # On trie une vidéo à l'intérieur, pour vérifier que tout fonctionne en profondeur.
    inner_dest = tri / "interieur"
    window.cfg.set_destinations([{"key": "6", "label": "Intérieur", "path": str(inner_dest)}])
    inner_name = window.current.name
    QTest.keyClick(window, Qt.Key_6)
    settle(app, window)
    check((inner_dest / inner_name).exists(),
          f"une vidéo du sous-dossier part vers sa destination ({inner_name})")

    window.go_up()
    ok = wait_for(app, lambda: not window.scanning and window.root == root, 60)
    check(ok, "« Remonter » ramène au dossier parent")
    check(window.mode == MODE_FOLDERS, "et retrouve le mode dossiers")
    check(window.current is not None and window.current.name == "Anniversaire",
          f"sur le dossier d'où l'on était parti (obtenu "
          f"{window.current.name if window.current else None})")
    check(window.levels == [], "la pile est revenue à zéro")

    print("\n[22] Profondeur et retour par Échap")
    QTest.keyClick(window, Qt.Key_Down, Qt.ControlModifier)
    wait_for(app, lambda: not window.scanning and window.root == parent_item.path, 60)
    check(len(window.levels) == 1, "Ctrl+↓ entre aussi dans le dossier")
    # Echap sort maintenant par etages : de la fiche aux vignettes, puis d'un
    # niveau de dossier, puis seulement du tri. On n'a jamais l'impression de
    # tout perdre d'un coup.
    QTest.keyClick(window, Qt.Key_Escape)
    pump(app, 0.4)
    check(window.browsing, "Échap rend d'abord la planche")
    check(window.root == parent_item.path, "sans quitter le dossier ouvert")
    QTest.keyClick(window, Qt.Key_Escape)
    ok = wait_for(app, lambda: not window.scanning and window.root == root, 60)
    check(ok, "le suivant remonte d'un niveau")
    check(window.stack.currentIndex() == 1, "on reste dans l'écran de tri")
    check(window.levels == [], "la pile de navigation est vidée")
    QTest.keyClick(window, Qt.Key_Escape)
    pump(app, 0.4)
    check(window.stack.currentIndex() == 0,
          "au niveau racine, Échap quitte bien le tri")

    print("\n[23] Un dossier sans vidéo directe se parcourt en dossiers")
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    position = [i.name for i in window.items].index("Sous-dossiers")
    window.show_item(position)
    QTest.mouseClick(window.enter_button, Qt.LeftButton)
    wait_for(app, lambda: not window.scanning and window.root.name == "Sous-dossiers", 60)
    check(window.mode == MODE_FOLDERS,
          f"pas de vidéo directe : on descend en mode dossiers (obtenu {window.mode})")
    check([i.name for i in window.items] == ["interne"],
          f"le sous-dossier est listé ({[i.name for i in window.items]})")
    QTest.mouseClick(window.enter_button, Qt.LeftButton)
    wait_for(app, lambda: not window.scanning and window.root.name == "interne", 60)
    check(len(window.levels) == 2, "on peut descendre de plusieurs niveaux")
    check(window.mode == MODE_FLAT, "et le dernier niveau montre ses vidéos")
    QTest.keyClick(window, Qt.Key_Up, Qt.ControlModifier)
    wait_for(app, lambda: not window.scanning and window.root.name == "Sous-dossiers", 60)
    check(len(window.levels) == 1, "Ctrl+↑ remonte d'un seul niveau")

    print("\n[24] Index d'analyse")
    from videosorter.index import INDEX
    from videosorter.scan import signature

    INDEX.clear()
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 3, 60)
    first_pass = window.scan_thread
    check(first_pass.reused == 0, "premier passage : rien à réutiliser")
    check(first_pass.rescanned == len(window.all_items),
          f"tout est analysé ({first_pass.rescanned})")
    check(INDEX.count_folders() == len(window.all_items),
          f"chaque dossier est mémorisé ({INDEX.count_folders()})")
    reference = {i.name: (i.size, i.file_count, i.video_count) for i in window.all_items}

    window.start_root(root, MODE_FOLDERS)
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
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 3, 60)
    third_pass = window.scan_thread
    check(third_pass.rescanned == 1,
          f"un seul dossier réanalysé après modification (obtenu {third_pass.rescanned})")
    refreshed = next(i for i in window.all_items if i.name == "Anniversaire")
    check(refreshed.file_count == reference["Anniversaire"][1] + 1,
          "et ses chiffres sont à jour")

    # Une modification au deuxième niveau passe volontairement inaperçue :
    # l'empreinte ne lit plus que la date du dossier lui-même. Interroger chaque
    # sous-dossier coûtait, sur un partage réseau, deux fois et demie le prix de
    # l'analyse que le cache était censé éviter.
    nested = next(i for i in window.all_items if i.name == "Sous-dossiers")
    (nested.path / "interne" / "ajout2.mp4").write_bytes(b"x" * 512)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 3, 60)
    check(window.scan_thread.rescanned == 0,
          "une modification plus profonde attend une actualisation forcée")

    # C'est Ctrl+R qui rattrape le coup, et il doit vraiment tout relire.
    window.refresh_root()
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 3, 60)
    check(window.scan_thread.reused == 0,
          f"Ctrl+R ne reprend rien du cache (obtenu {window.scan_thread.reused})")
    seen = next(i for i in window.all_items if i.name == "Sous-dossiers")
    check(seen.file_count == reference["Sous-dossiers"][1] + 1,
          "et retrouve le fichier ajouté en profondeur")

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
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    position = [i.name for i in window.items].index("Anniversaire")
    window.show_item(position)
    check(window.item_parent.text() == root.name,
          f"au premier niveau, la racine seule (obtenu {window.item_parent.text()!r})")

    window.enter_current()
    wait_for(app, lambda: not window.scanning and window.mode == MODE_FLAT, 60)
    wait_for(app, lambda: bool(window.current.info.get("duration")), 30)
    window.show_item(0)
    crumbs = window.item_parent.text()
    check(crumbs == f"{root.name}  ›  Anniversaire",
          f"chaîne complète depuis la racine (obtenu {crumbs!r})")
    from videosorter.scan import human_duration
    expected = human_duration(window.current.info["duration"])
    title = window.item_title.text()
    # Le titre est le nom seul ; la duree se lit sous l'image, en temps restant.
    check(title == window.current.name,
          f"le titre est le nom seul (obtenu {title!r})")
    check(window.current.name in title, "le nom du fichier suit")
    info_line = window.item_subtitle.text()
    check("240p" in info_line, f"résolution nommée dans les infos (obtenu {info_line!r})")
    # Les dimensions exactes ne disaient rien de plus que « 240p » et
    # repoussaient la taille hors de vue.
    check("320×240" not in info_line, "sans les dimensions exactes, redondantes")
    check("Ko" in info_line or "Mo" in info_line, "poids présent")
    import re as _re_date
    check(_re_date.search(r"\d{2}/\d{2}/\d{4}", info_line) is not None, "date présente")

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
    window.start_root(root, MODE_FOLDERS)
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
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    window.toggle_board(True)
    pump(app, 0.5)
    check(window.browsing, "la planche est active")
    check(window.viewer.currentWidget() is window.board, "et occupe la zone centrale")
    check(len(window.board.items) == len(window.items),
          f"une carte par élément ({len(window.board.items)})")
    visible = [c for c in window.board.cards if not c.isHidden()]
    check(len(visible) == len(window.items), "toutes les cartes sont affichées")
    check(visible[0].meta.text() != "", "chaque carte porte son nom")
    check(visible[0].meta.text().count("\n") == 0,
          f"sur une seule ligne ({visible[0].meta.text()!r})")
    check(not visible[0].duration_chip.isHidden(),
          "le nombre de vidéos tient dans la pastille")
    chip = visible[0].duration_chip
    image = visible[0].image.geometry()
    check(chip.y() < image.center().y(),
          f"posée en haut de la vignette (y={chip.y()}, image {image.top()}"
          f"–{image.bottom()})")
    check(chip.x() > image.center().x(), "et à droite")

    ok = wait_for(app, lambda: any(c._pixmap for c in window.board.cards), 120)
    check(ok, "les images des cartes arrivent")

    # Noter depuis une carte.
    window.ratings.set(window.items[0].path, 0)
    window.on_board_rate(0, 3)
    check(window.ratings.get(window.items[0].path) == 3, "on note depuis une carte")
    # La carte ne porte plus d'etoiles : elles doublaient la hauteur du texte
    # sous chaque vignette. La note se relit sur la fiche.
    check(not hasattr(window.board.cards[0], "stars"),
          "la carte ne porte plus d'étoiles")

    # Un clic sur une carte descend a l'etage du dessous — sa fiche — sans
    # changer de dossier ni rien deplacer. On entre dans le dossier depuis la
    # fiche, par « Entrer » ou Ctrl+Bas.
    before = str(window.root)
    position = next(i for i, item in enumerate(window.items)
                    if item.kind == MODE_FOLDERS and Path(item.path).is_dir()
                    and not item.loose_only)
    opened_name = window.items[position].name
    window.on_board_open(position)
    pump(app, 0.5)
    check(not window.browsing, f"un clic sur une carte ouvre sa fiche ({opened_name})")
    check(window.current is not None and window.current.name == opened_name,
          "celle de l'élément cliqué")
    check(str(window.root) == before, "sans quitter le dossier courant")
    check((Path(before) / opened_name).exists(), "ni rien déplacer")
    window.toggle_board(True)
    pump(app, 0.4)

    print("\n[30] L'arborescence navigue en planche, envoie en fiche")
    window.cfg["tree_root"] = str(tri)
    window.toggle_tree(True)
    window.toggle_board(True)
    pump(app, 0.3)
    window.tree.set_root(str(tri))
    pump(app, 0.4)
    index = window.tree.model.index(str(tri / "+archive"))
    window.tree.view.clicked.emit(index)
    ok = wait_for(app, lambda: not window.scanning
                  and window.root.name == "+archive", 60)
    check(ok, "en planche, un clic dans l'arbre ouvre le dossier visé")
    check(window.transfers.active == 0, "et ne déclenche aucun transfert")
    window.toggle_tree(False)
    window.toggle_board(False)
    pump(app, 0.3)
    check(not window.browsing, "retour à la fiche unique")

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
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    seen = set()
    for _ in range(12):
        window.pick_random()
        wait_for(app, lambda: not window.scanning, 30)
        pump(app, 0.15)
        if window.current is not None and window.current.kind == MODE_FILES:
            seen.add(Path(window.current.path))
    check(len(seen) > 1, f"le tirage visite plusieurs vidéos ({len(seen)})")
    pump(app, 0.3)

    print("\n[33] Densité de la planche et clic sur toute la carte")
    window.start_root(root, MODE_FOLDERS)
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
    for child in (card.image, card.meta):
        check(child.testAttribute(Qt.WA_TransparentForMouseEvents),
              f"un clic traverse « {child.objectName() or 'image'} »")
    opened = []
    card.opened.connect(opened.append)
    QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier,
                     QPoint(card.width() // 2, 30))
    check(opened == [0], "un clic sur l'image ouvre bien la carte")

    print("\n[33b] La fenêtre doit pouvoir rétrécir")
    # Une seule étiquette trop large imposait sa largeur à la fenêtre entière :
    # elle ne pouvait plus rétrécir, et tout débordait de l'écran à droite.
    # Le seuil garde d'une catastrophe — une etiquette avait un jour porte ce
    # minimum a 5076 px, et tout sortait de l'ecran par la droite — pas d'un
    # pixel de trop. Il doit rester sous la largeur d'un ecran ordinaire.
    minimum = window.minimumSizeHint().width()
    check(minimum <= 1400,
          f"la fenêtre tient dans un écran ordinaire ({minimum} px exigés)")
    window.resize(1500, 800)
    pump(app, 0.4)
    check(window.width() == 1500,
          f"et elle obéit quand on la redimensionne ({window.width()} px)")
    for name in ("crumbs", "tabs", "controls", "commands"):
        widget = getattr(window, name)
        check(widget.minimumSizeHint().width() <= 1400,
              f"« {name} » n'élargit pas la fenêtre "
              f"({widget.minimumSizeHint().width()} px)")
    window.resize(1400, 900)
    pump(app, 0.4)

    print("\n[34] Pastilles de tri")
    filters = window.controls
    check(not filters.isHidden(), "la barre de réglages est visible")
    check(filters.criteria()["stars"] == -1, "et ne masque rien au départ")
    check(not hasattr(filters, "duration_op") and not hasattr(filters, "stars"),
          "les filtres chiffrés ont quitté l'écran")
    check(filters.exclude.isHidden(), "l'exclusion par le nom aussi")

    chips = filters.sorts
    modes = []
    chips.chosen.connect(modes.append)
    chips.buttons["duration"].click()
    check(modes[-1] == "duration_desc", f"un clic classe du plus long ({modes[-1]})")
    check("▼" in chips.buttons["duration"].text(),
          f"et la pastille le montre ({chips.buttons['duration'].text()})")
    chips.buttons["duration"].click()
    check(modes[-1] == "duration_asc", f"un second clic inverse ({modes[-1]})")
    check("▲" in chips.buttons["duration"].text(), "la flèche suit")
    chips.buttons["duration"].click()
    check(modes[-1] == "random", f"un troisième remet au hasard ({modes[-1]})")
    check(chips.buttons["duration"].text() == "Durée", "la pastille s'éteint")

    chips.buttons["size"].click()
    check(modes[-1] == "size_desc", "la taille se classe pareil")
    chips.buttons["stars"].click()
    check(modes[-1] == "stars_desc", "et la note")
    check(chips.buttons["size"].property("chosen") == "false",
          "un seul critère à la fois")

    # Le classement doit vraiment reordonner la liste.
    window.sort_mode = "size_desc"
    window.apply_sort()
    sizes = [i.size for i in window.items]
    check(sizes == sorted(sizes, reverse=True),
          f"du plus gros au plus petit ({sizes[:5]})")
    window.sort_mode = "size_asc"
    window.apply_sort()
    sizes = [i.size for i in window.items]
    check(sizes == sorted(sizes), f"et l'inverse au clic suivant ({sizes[:5]})")
    window.sort_mode = "stars_asc"
    window.apply_sort()
    notes = [window.ratings.get(i.path) for i in window.items]
    check(notes == sorted(notes), "la note se classe dans les deux sens aussi")

    print("\n[35] Précédent et temps restant")
    window.toggle_board(False)
    # Le clic de l'étape 33 a réellement ouvert un dossier : on revient à la
    # racine avant d'éprouver la navigation.
    window.start_root(root, MODE_FOLDERS)
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
    check(bool(window.visited), "un endroit précédent est mémorisé")
    check(window.go_back(), "le retour aboutit")
    ok = wait_for(app, lambda: not window.scanning and str(window.root) == start, 60)
    check(ok, "et ramène à l'endroit précédent")

    player = window.single
    player.resize(900, 600)
    player._on_position(0)
    # Elle est passee sous l'image : posee dessus, la fenetre video native de
    # Windows se dessinait par-dessus et on ne la voyait jamais. Plus fine,
    # donc, mais reellement visible.
    check(4 <= player.progress_rail.height() <= 10,
          f"la barre d'avancement reste discrète ({player.progress_rail.height()} px)")
    check(player.progress_rail.parent() is not player.video_area,
          "et n'est plus posée sur l'image, où elle disparaissait")
    check(not player.remaining.isHidden(),
          "le temps restant s'affiche à côté d'elle")
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

    print("\n[38] Temps restant dans les aperçus")
    check(window.grid.remaining is not None, "la grille d'aperçus en a un")
    check(window.board.remaining is not None, "la planche aussi")
    check(window.grid.remaining.isHidden(), "masqué tant que rien ne se lit")

    print("\n[39] Trois modes de lecture de la racine")
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    folders_count = len(window.items)
    check(all(i.kind == MODE_FOLDERS for i in window.items),
          "mode dossiers : des dossiers")

    window.start_root(root, MODE_FLAT)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 90)
    flat_items = window.items
    check(all(i.kind == MODE_FILES for i in flat_items),
          "mode a plat : des fichiers")
    check(len(flat_items) > folders_count,
          f"et bien plus nombreux ({len(flat_items)} contre {folders_count} dossiers)")
    depths = {len(Path(i.path).relative_to(root).parts) for i in flat_items}
    check(max(depths) >= 2,
          f"des videos nichees dans des sous-dossiers y figurent (profondeurs {sorted(depths)})")
    check(all(Path(i.path).suffix.lower() == ".mp4" for i in flat_items),
          "et rien d'autre que des videos")

    # Le filtre par nom porte alors sur toute la collection.
    lone = flat_items[0].name.rsplit(".", 1)[0]
    window.apply_filter(lone, "")
    pump(app, 0.4)
    found = [i.name for i in window.items]
    check(0 < len(found) <= len(flat_items) and all(lone in n for n in found),
          f"filtrer par « {lone} » fouille tout le stock ({len(found)} trouvées)")
    window.apply_filter("", "")
    pump(app, 0.4)

    print("\n[40] Tirage au hasard sur tout le stock")
    # Vivier dedie : les etapes precedentes ont deplace presque toutes les
    # videos de la racine, et tirer parmi trois ne prouverait rien.
    draw_root = base / "tirage"
    shutil.rmtree(draw_root, ignore_errors=True)
    sources = sorted(tri.rglob("*.mp4"))
    for position in range(12):
        folder = draw_root / f"lot_{position % 3}"
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sources[position % len(sources)], folder / f"clip_{position}.mp4")
    window.start_root(draw_root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    pool = {Path(v) for item in window.all_items for v in item.videos}
    check(len(pool) > len(window.items),
          f"le vivier depasse la liste affichee ({len(pool)} videos)")
    tirages = set()
    for _ in range(20):
        window.pick_random()
        wait_for(app, lambda: not window.scanning, 30)
        pump(app, 0.12)
        if window.current is not None and window.current.kind == MODE_FILES:
            tirages.add(Path(window.current.path))
    check(len(tirages) >= 4,
          f"le tirage varie vraiment ({len(tirages)} sur {len(pool)} possibles)")
    check(tirages <= pool,
          f"et reste dans le stock analyse ({len(tirages - pool)} intrus)")

    print("\n[41] Notation visuelle et touches de destination")
    from videosorter.config import KEY_ORDER, RESERVED_KEYS
    caps = [window.commands.layout_.itemAt(i).widget()
            for i in range(window.commands.layout_.count())]
    star_caps = [c for c in caps if type(c).__name__ == "StarCap"]
    check(len(star_caps) == 5, f"cinq vignettes de notation ({len(star_caps)})")
    check([c.count for c in star_caps] == [1, 2, 3, 4, 5],
          "une par nombre d'etoiles")
    check(star_caps[2].stars.value == 3, "la troisieme en dessine trois")
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    window.show_item(first_untouched(window))
    target = window.current
    window.ratings.set(target.path, 0)
    star_caps[3].clicked.emit()
    pump(app, 0.2)
    check(window.ratings.get(target.path) == 4,
          "cliquer la vignette « 4 » pose quatre etoiles")

    print("\n[42] Une configuration ancienne migre ses touches")
    import json, tempfile
    from videosorter.config import Config as VSConfig
    old = Path(tempfile.mkdtemp()) / "config.json"
    old.write_text(json.dumps({"destinations": [
        {"key": "1", "label": "A", "path": "C:/a"},
        {"key": "3", "label": "B", "path": "C:/b"},
        {"key": "9", "label": "C", "path": "C:/c"},
    ]}), encoding="utf-8")
    migrated = VSConfig(path=old)
    keys = [d["key"] for d in migrated.destinations]
    check(not set(keys) & RESERVED_KEYS,
          f"plus aucune destination sur une touche de notation ({keys})")
    check(keys[2] == "9", "celles deja valides ne bougent pas")
    check(len(set(keys)) == 3, "et restent distinctes")

    print("\n[43] La planche ne batit qu'une page de cartes")
    from videosorter.board import PAGE_SIZE
    from videosorter.scan import Item

    window.toggle_board(True)
    pump(app, 0.3)
    many = [Item(path=Path(f"C:/faux/dossier_{i:03d}"), kind=MODE_FOLDERS,
                 video_count=3, size=1024)
            for i in range(PAGE_SIZE * 2 + 15)]
    window.board.set_items(many, lambda _p: 0)
    pump(app, 0.4)
    built = sum(1 for c in window.board.cards if not c.isHidden())
    check(built == PAGE_SIZE,
          f"une page de {PAGE_SIZE} cartes, pas {len(many)} ({built} bâties)")
    check(len(window.board.items) == len(many),
          "alors que la liste complète est bien retenue")
    check(window.board.total_pages() == 3,
          f"trois pages pour {len(many)} éléments ({window.board.total_pages()})")

    window.board.set_page(1)
    pump(app, 0.3)
    check(window.board.page == 1, "on tourne la page")
    check(window.board.cards[0].index == PAGE_SIZE,
          f"la première carte reprend au bon rang ({window.board.cards[0].index})")
    still = sum(1 for c in window.board.cards if not c.isHidden())
    check(still == PAGE_SIZE, f"toujours une page de cartes ({still})")

    window.board.set_page(2)
    pump(app, 0.3)
    last = sum(1 for c in window.board.cards if not c.isHidden())
    check(last == 15, f"la dernière page n'affiche que le reste ({last})")
    window.board.set_page(99)
    check(window.board.page == 2, "on ne déborde pas au-delà de la dernière")
    window.board.set_page(-5)
    check(window.board.page == 0, "ni en deçà de la première")

    # Un element hors page ne doit pas planter les mises a jour.
    window.board.set_stars(PAGE_SIZE + 3, 4)
    window.board.set_state(PAGE_SIZE + 3, "moved")
    window.board.set_thumb(PAGE_SIZE + 3, "C:/inexistant.jpg")
    check(True, "agir sur un élément hors page reste sans incident")
    window.toggle_board(False)
    pump(app, 0.3)

    print("\n[44] La relecture livre par paquets, et rien que les différences")
    from videosorter.index import INDEX as _INDEX
    from videosorter.scan import RefreshThread
    check(RefreshThread.BATCH_SIZE > 1,
          f"les éléments partent groupés ({RefreshThread.BATCH_SIZE} par paquet)")
    _INDEX.clear()
    batches = []
    window.start_root(root, MODE_FOLDERS)
    window.scan_thread.patch.connect(
        lambda a, r, g: batches.append((len(a), len(r), len(g))))
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 3, 60)
    pump(app, 0.3)
    check(sum(added for added, _, _ in batches) >= 3,
          f"des paquets ont bien été reçus ({batches})")
    check(len(window.all_items) >= 3,
          f"et tous les éléments sont arrivés ({len(window.all_items)})")

    # Le relancement est le cas qui comptait : rien n'a bougé, donc rien n'est
    # republié — la liste est à l'écran avant même que la relecture commence.
    quiet = []
    shown = len(window.all_items)
    window.start_root(root, MODE_FOLDERS)
    check(len(window.all_items) == shown,
          f"la liste s'affiche d'emblée, sans attendre le disque "
          f"({len(window.all_items)})")
    window.scan_thread.patch.connect(
        lambda a, r, g: quiet.append((len(a), len(r), len(g))))
    wait_for(app, lambda: not window.scanning, 60)
    pump(app, 0.3)
    check(quiet == [], f"et la relecture n'a rien eu à corriger ({quiet})")

    print("\n[45] Les dossiers de tete se traversent")
    from videosorter.scan import (
        LOOSE_LABEL, expand_parents, is_parent_folder, list_entries, loose_videos,
    )

    shelf = base / "rayonnage"
    shutil.rmtree(shelf, ignore_errors=True)
    source = sorted(tri.rglob("*.mp4"))[0]
    for parent_name in ("+beach", "+montagne"):
        for child in ("serie_a", "serie_b"):
            target = shelf / parent_name / child
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target / "clip.mp4")
        # Des videos posees directement dans le rayonnage, sans sous-dossier.
        for loose in range(2):
            shutil.copy2(source, shelf / parent_name / f"vrac_{loose}.mp4")
    (shelf / "dossier_normal").mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, shelf / "dossier_normal" / "clip.mp4")

    check(is_parent_folder(shelf / "+beach"), "un nom préfixé est reconnu")
    check(not is_parent_folder(shelf / "dossier_normal"), "un nom ordinaire non")
    check(len(loose_videos(shelf / "+beach")) == 2,
          "les vidéos en vrac d'un rayonnage sont repérées")

    # Sans traversee, un rayonnage disparait de la liste : c'est une
    # destination, l'endroit ou l'on range, pas quelque chose a ranger. Le
    # laisser revenait a proposer de trier le rangement lui-meme.
    plain = list_entries(shelf, MODE_FOLDERS, True, False)
    check(sorted(x.name for x in plain) == ["dossier_normal"],
          f"sans traversée, les rayonnages s'effacent ({[x.name for x in plain]})")

    opened = list_entries(shelf, MODE_FOLDERS, True, True)
    names = [x.name for x in opened]
    check("serie_a" in names and "serie_b" in names,
          f"traversés, ce sont leurs sous-dossiers qui apparaissent ({names})")
    check("dossier_normal" in names, "les dossiers ordinaires restent")
    check(names.count("+beach") == 1,
          "le rayonnage ne figure qu'une fois, pour ses vidéos en vrac")

    window.cfg["expand_parents"] = True
    window.start_root(shelf, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.all_items) >= 5, 60)
    listed = [i.name for i in window.all_items]
    check(not any(n == "+beach" or n == "+montagne" for n in listed),
          f"aucun rayonnage brut dans la liste à trier ({listed})")
    loose_items = [i for i in window.all_items if i.loose_only]
    check(len(loose_items) == 2, f"deux entrées « en vrac » ({len(loose_items)})")
    check(LOOSE_LABEL in loose_items[0].name,
          f"nommées clairement (obtenu {loose_items[0].name!r})")
    check(loose_items[0].video_count == 2,
          f"portant les seules vidéos en vrac ({loose_items[0].video_count})")

    # Un rayonnage ne se deplace ni ne se supprime.
    position = [i.name for i in window.items].index(loose_items[0].name)
    window.show_item(position)
    before = window.stats["moved"]
    window.act_move({"path": str(tri / "2019"), "label": "2019"})
    settle(app, window, 10)
    check(window.stats["moved"] == before,
          "une entrée « en vrac » refuse d'être déplacée telle quelle")
    check((shelf / "+beach").is_dir(), "et le rayonnage reste en place")

    print("\n[46] Double-clic : la fiche, pas un lecteur à part")
    check(not hasattr(window, "focus"), "plus de lecteur séparé")
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    target_video = next(v for item in window.all_items for v in item.videos)
    window.play_in_app(str(target_video))
    ok = wait_for(app, lambda: not window.scanning
                  and window.root == Path(target_video).parent, 60)
    check(ok, "on arrive dans le dossier de la vidéo")
    check(window.mode == MODE_FILES, "en mode fichier")
    ok = wait_for(app, lambda: window.current is not None
                  and window.current.path == Path(target_video), 30)
    check(ok, f"et sur sa fiche ({window.current.name if window.current else None})")

    print("\n[47] Pas de fantôme au changement d'aperçu")
    grid = window.grid
    check(grid.video.isHidden() or grid.hovered_slot == -1,
          "au repos, aucune image de lecture n'est affichée")
    grid.hovered_slot = 0
    grid.tiles[0].video = str(target_video)
    grid.tiles[0].ts = 1.0
    grid._play_slot(0)
    check(grid.video.isHidden(),
          "sur un nouveau fichier, l'image reste masquée jusqu'à ce qu'elle soit prête")
    grid.stop()
    check(grid.video.isHidden(), "et à l'arrêt également")

    print("\n[48] Mots-cles automatiques")
    from videosorter.tagging import build_tag_items, fold, matches
    from videosorter.widgets import TagsDialog

    check(fold("Été") == fold("ete"), "accents ignorés à la comparaison")
    check(fold("PLAGE") == fold("plage"), "casse ignorée aussi")
    check(matches("été", "X/mon-ETE-2019.mp4"), "un mot se retrouve dans un nom")
    check(not matches("ski", "X/plage.mp4"), "et ne se retrouve pas ailleurs")

    sample = [Path(f"X/plage_{i}.mp4") for i in range(3)]
    sample += [Path("X/MONTAGNE.mp4"), Path("X/autre.mp4")]
    built = build_tag_items(["plage", "montagne", "introuvable"], sample)
    check([b.path.name for b in built] == ["plage", "montagne"],
          f"un élément par mot trouvé, dans l'ordre saisi ({[b.path.name for b in built]})")
    check(built[0].video_count == 3, f"le premier réunit trois vidéos ({built[0].video_count})")
    check(built[0].is_tag and not built[0].movable,
          "un mot-clé est une vue, pas un rangement")
    check(built[0].name.startswith("#"), f"et se distingue à l'œil ({built[0].name})")

    dialog = TagsDialog(["plage", "  ", "Plage", "montagne"])
    check(dialog.result_tags() == ["plage", "montagne"],
          f"la saisie ignore les lignes vides et les doublons ({dialog.result_tags()})")

    # De bout en bout : les mots-cles apparaissent en tete de la liste.
    tagged = base / "motscles"
    shutil.rmtree(tagged, ignore_errors=True)
    origin = sorted(tri.rglob("*.mp4"))[0]
    for folder, names in (("lot_a", ["plage_ete.mp4", "plage_hiver.mp4"]),
                          ("lot_b", ["montagne_1.mp4", "divers.mp4"])):
        (tagged / folder).mkdir(parents=True, exist_ok=True)
        for name in names:
            shutil.copy2(origin, tagged / folder / name)

    window.tags = ["plage", "montagne"]
    window.set_tab(TAB_FOLDERS)
    window.start_root(tagged, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning, 60)
    pump(app, 0.4)
    check(not any(i.is_tag for i in window.all_items),
          "aucun dossier virtuel ne s'invite parmi les dossiers réels")

    window.set_tab(TAB_TAGS)
    wait_for(app, lambda: any(i.is_tag for i in window.all_items), 30)
    tags_found = [i for i in window.all_items if i.is_tag]
    check(len(tags_found) == 2, f"deux dossiers virtuels ({len(tags_found)})")
    check(all(i.is_tag for i in window.all_items),
          "et l'onglet ne montre qu'eux")
    check(not window.tag_chips.isHidden(),
          "les deux familles de mots-clés s'offrent ici, et seulement ici")
    check(tags_found[0].video_count == 2,
          f"« plage » réunit les deux vidéos ({tags_found[0].video_count})")

    # Un mot-cle refuse d'etre deplace, mais s'ouvre.
    position = [i.item_id for i in window.items].index(tags_found[0].item_id)
    window.show_item(position)
    before = window.stats["moved"]
    window.act_move({"path": str(tri / "2019"), "label": "2019"})
    settle(app, window, 10)
    check(window.stats["moved"] == before, "un mot-clé ne se déplace pas")

    window.show_item(position)
    window.enter_current()
    pump(app, 0.5)
    check(window.mode == MODE_FLAT, "l'ouvrir montre ses vidéos")
    check(len(window.items) == 2, f"les deux vidéos du mot-clé ({len(window.items)})")
    check(all("plage" in i.name.lower() for i in window.items),
          f"et rien d'autre ({[i.name for i in window.items]})")

    print("\n[49] Un onglet à quatre entrées dit où l'on est")
    # Sans mode imposé : c'est le chemin de l'écran d'accueil, celui que prend
    # « Choisir un dossier racine ». Il doit aboutir comme les autres.
    window.start_root(tagged)
    ok = wait_for(app, lambda: not window.scanning and bool(window.items), 60)
    check(ok, "choisir une racine, sans rien préciser, ouvre bien le tri")

    window.set_tab(TAB_FOLDERS)
    window.start_root(tagged, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning, 60)
    check(window.tab == TAB_FOLDERS, "on arrive sur les dossiers")
    check(window.tabs.buttons[TAB_FOLDERS].property("chosen") == "true",
          "et l'onglet l'annonce")
    check(window.browsing, "les dossiers se parcourent en planche")

    window.set_tab(TAB_VIDEOS)
    wait_for(app, lambda: not window.scanning, 60)
    check(window.mode == MODE_FLAT, "l'onglet « Vidéos » met la liste à plat")
    check(window.tabs.buttons[TAB_VIDEOS].property("chosen") == "true",
          "l'onglet suit")
    check(all(i.kind == MODE_FILES for i in window.items), "ce sont des fichiers")
    from videosorter.config import DEFAULTS
    check(DEFAULTS["sort_mode"] == "random",
          "et le classement par défaut est le hasard")
    check(window.viewer.currentWidget() is window.board, "la planche est affichée")

    check(TAB_EDIT not in window.tabs.buttons,
          "l'édition n'est plus un onglet : on y entre en ouvrant un élément")

    # Un clic sur une carte ouvre cette carte-la, et pas une autre.
    window.set_tab(TAB_VIDEOS)
    pump(app, 0.4)
    target_id = window.items[2].item_id
    window.on_board_open(2)
    pump(app, 0.4)
    check(not window.browsing, "cliquer une vidéo bascule en fiche")
    check(window.current.item_id == target_id,
          "et c'est bien celle qu'on a cliquée")

    # L'edition commence par ce qui n'est pas encore range.
    window.set_tab(TAB_FOLDERS)
    wait_for(app, lambda: not window.scanning, 60)
    window.set_tab(TAB_EDIT)
    pump(app, 0.4)
    item = window.current
    check(item is not None and not item.categorized,
          "l'édition commence par un élément pas encore classé")

    # Les deux familles de mots-cles, dans l'onglet qui les porte.
    window.set_tab(TAB_TAGS)
    pump(app, 0.4)
    check(window.mode == MODE_FOLDERS,
          "l'onglet des mots-clés remet la liste des dossiers sans la relire")
    window.set_tag_family("top")
    pump(app, 0.4)
    tops = [i for i in window.all_items if i.is_tag]
    check(bool(tops), f"les mots fréquents forment des dossiers ({len(tops)})")
    check(any(i.path.name == "plage" for i in tops),
          f"« plage » en fait partie ({[i.path.name for i in tops][:5]})")
    check(window.tag_chips.buttons["top"].property("chosen") == "true",
          "et la pastille le montre")
    window.set_tag_family("mine")
    pump(app, 0.4)
    check([i.path.name for i in window.all_items if i.is_tag] == ["plage", "montagne"],
          "revenir à mes mots-clés restitue les miens")

    # L'arborescence envoie, ou nous emmene.
    check(window.tree.action == "send", "l'arborescence envoie par défaut")
    window.tree.toggle_action()
    check(window.tree.action == "go", "un clic sur son titre la change en navigation")
    check("Aller" in window.tree.action_button.text(), "ce que le titre dit")
    window.tree.toggle_action()
    check(window.tree.action == "send", "et l'on revient à l'envoi")

    # Le fil d'Ariane doit montrer la descente et savoir y ramener.
    window.set_tab(TAB_FOLDERS)
    window.start_root(tagged, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning, 60)
    top = str(window.root)
    position = next(i for i, item in enumerate(window.items)
                    if item.movable and Path(item.path).is_dir())
    window.show_item(position)
    window.enter_current()
    wait_for(app, lambda: not window.scanning and str(window.root) != top, 60)
    check(window.crumbs.layout_.count() >= 3,
          f"le fil montre la descente ({window.crumbs.layout_.count()} éléments)")
    window.jump_to(top)
    ok = wait_for(app, lambda: not window.scanning and str(window.root) == top, 60)
    check(ok, "et cliquer un segment y ramène")
    check(window.levels == [], "en dépilant les niveaux traversés")

    print("\n[50] Vignettes fabriquées d'avance")
    from videosorter.backfill import ThumbBackfill

    def run_backfill(target):
        """Lance le parcours et rend (fabriquées, déjà là, mené à terme)."""
        out = {}
        worker = ThumbBackfill(target, window.cfg["thumb_width"], True)
        worker.counted.connect(lambda n: out.__setitem__("total", n))
        worker.done.connect(
            lambda made, kept, whole: out.update(
                made=made, kept=kept, whole=whole))
        worker.start()
        wait_for(app, lambda: "made" in out, 180)
        return out

    first = run_backfill(root)
    check(first.get("total", 0) > 0,
          f"le parcours recense les vidéos ({first.get('total')})")
    check(first.get("whole") is True, "et va jusqu'au bout")
    check(first.get("made", 0) + first.get("kept", 0) == first["total"],
          f"chaque vidéo est comptée une fois "
          f"({first.get('made')} faites, {first.get('kept')} déjà là)")

    second = run_backfill(root)
    check(second.get("made") == 0,
          f"un second passage ne refait rien ({second.get('made')} refaite(s))")
    check(second.get("kept") == first["total"],
          "il les retrouve toutes en cache")

    # Et ce que la planche demande doit etre exactement ce qui a ete fabrique :
    # sinon la pre-fabrication ne servirait a rien.
    from videosorter.media import build_preview_plan, thumb_path
    sample = sorted(root.rglob("*.mp4"))[0]
    plan = build_preview_plan([sample], 1, 0, True, True)
    ready = thumb_path(Path(plan[0][0]), plan[0][1], window.cfg["thumb_width"])
    check(ready.exists(),
          "la vignette préparée est bien celle que la planche réclame")

    print("\n[51] Lecteur de côté")
    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    window.toggle_board(True)
    pump(app, 0.4)
    check(window.aside.isHidden(), "le lecteur de côté se tient à l'écart")

    position = next(i for i, it in enumerate(window.items) if it.videos)
    window.open_aside(position)
    pump(app, 0.6)
    check(not window.aside.isHidden(), "un clic droit l'ouvre à droite")
    check(window.browsing, "et la planche reste affichée")
    check(window.viewer.currentWidget() is window.board,
          "on n'a pas quitté les vignettes")
    check(window.aside_title.text() == window.items[position].name,
          f"il annonce ce qu'il lit ({window.aside_title.text()})")

    following = next((i for i in range(position + 1, len(window.items))
                      if window.items[i].videos), -1)
    if following >= 0:
        window.aside_step(1)
        pump(app, 0.4)
        check(window.aside_index == following,
              "« suivante » passe à la vignette d'après")

    window.close_aside()
    pump(app, 0.3)
    check(window.aside.isHidden(), "et il se referme")

    print("\n[52] Changer d'onglet ne relit pas le disque")
    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    pump(app, 0.4)
    folders_seen = len(window.items)

    scans = {"n": 0}
    real_start = window.start_root

    def counted(*args, **kwargs):
        scans["n"] += 1
        return real_start(*args, **kwargs)

    window.start_root = counted
    try:
        window.set_tab(TAB_VIDEOS)
        pump(app, 0.6)
        check(scans["n"] == 0,
              f"passer aux vidéos se sert de ce qu'on a déjà ({scans['n']} analyse(s))")
        check(window.mode == MODE_FLAT, "et la liste est bien à plat")

        window.set_tab(TAB_FOLDERS)
        pump(app, 0.6)
        check(scans["n"] == 0,
              f"et revenir aux dossiers non plus ({scans['n']} analyse(s))")
        check(window.mode == MODE_FOLDERS, "on retrouve le mode dossiers")
        check(len(window.items) == folders_seen,
              f"avec la même liste qu'avant ({len(window.items)} sur {folders_seen})")
    finally:
        window.start_root = real_start

    print("\n[53] Mur de vidéos verticales")
    from videosorter.index import INDEX
    from videosorter.split import DEFAULT_PANES
    from videosorter.stamps import stamp_of

    check(len(window.wall.panes) == DEFAULT_PANES,
          f"le mur compte {DEFAULT_PANES} panneaux ({len(window.wall.panes)})")

    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    window.set_tab(TAB_VIDEOS)
    pump(app, 0.5)

    # On declare verticales quelques videos connues : le mur ne sonde rien
    # lui-meme, il se sert de ce qui a deja ete releve.
    videos = [Path(v) for it in window.items for v in it.videos][:4]
    check(bool(videos), "des vidéos sont disponibles pour l'essai")
    for video in videos[:2]:
        INDEX.put_probe(video, stamp_of(video),
                        {"duration": 8.0, "width": 320, "height": 640,
                         "codec": "h264", "ok": True})
    for video in videos[2:]:
        INDEX.put_probe(video, stamp_of(video),
                        {"duration": 8.0, "width": 640, "height": 320,
                         "codec": "h264", "ok": True})

    pool, unknown = window.vertical_pool()
    # Les horizontales connues restent dehors ; ce qui n'a pas ete sonde entre,
    # et la legende dit combien — un mur vide n'apprend rien.
    check(all(str(v) in pool for v in videos[:2]),
          "les verticales connues entrent au vivier")
    check(not any(str(v) in pool for v in videos[2:]),
          "les horizontales connues restent dehors")
    check(len(pool) == 2 + unknown, f"et l'inconnu est compté ({unknown})")

    window.set_tab(TAB_SPLIT)
    pump(app, 0.6)
    check(window.viewer.currentWidget() is window.wall, "l'onglet montre le mur")
    playing = [p for p in window.wall.panes if p.video_path]
    check(len(playing) == min(DEFAULT_PANES, len(pool)),
          f"un panneau par vidéo disponible ({len(playing)})")
    check(all(p.video_path in pool for p in playing),
          "et chacun lit une vidéo du vivier")

    # Un panneau se renouvelle sans toucher aux autres.
    others = [p.video_path for p in window.wall.panes[1:]]
    window.wall.refill_one(0)
    pump(app, 0.3)
    check([p.video_path for p in window.wall.panes[1:]] == others,
          "changer un panneau laisse les autres en place")

    # La recherche restreint le vivier.
    window.controls.include.setText("zzz-introuvable")
    pump(app, 0.8)
    check(not window.wall.pool, "une recherche sans résultat vide le mur")
    check(not window.wall.empty.isHidden(), "et le mur le dit au lieu de rester noir")
    window.controls.include.setText("")
    pump(app, 0.8)
    window.set_tab(TAB_FOLDERS)
    pump(app, 0.4)
    check(all(p.player.playbackState() != p.player.PlaybackState.PlayingState
              for p in window.wall.panes),
          "quitter le mur arrête ses lecteurs")

    print("\n[54] Le fil d'Ariane ramène d'un seul clic")
    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    origin = str(window.root)

    position = next(i for i, it in enumerate(window.items)
                    if it.kind == MODE_FOLDERS and Path(it.path).is_dir()
                    and not it.loose_only)
    window.show_item(position)
    window.enter_current()
    wait_for(app, lambda: not window.scanning and str(window.root) != origin, 60)
    check(str(window.root) != origin, "on est descendu d'un niveau")
    check(window.crumbs.layout_.count() >= 3,
          f"le fil montre la descente ({window.crumbs.layout_.count()} pièces)")

    # Le detour : changer d'onglet vidait la pile des niveaux, et le fil ne
    # savait plus d'ou l'on venait. Un seul clic doit quand meme ramener.
    window.set_tab(TAB_VIDEOS)
    pump(app, 0.5)
    window.set_tab(TAB_FOLDERS)
    pump(app, 0.5)
    window.start_root(Path(origin) / window.items[0].path.name
                      if False else Path(origin), MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning, 60)

    window.show_item(position)
    window.enter_current()
    wait_for(app, lambda: not window.scanning and str(window.root) != origin, 60)
    window.levels = []          # comme apres un changement d'onglet
    window.jump_to(origin)
    ok = wait_for(app, lambda: not window.scanning and str(window.root) == origin, 60)
    check(ok, "un seul clic ramène à la racine, même la pile vidée")

    print("\n[55] Planche contact, cinéma et enchaînement")
    from videosorter.media import CONTACT_PER_VIDEO, CONTACT_ROWS, build_contact_plan

    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    position = next(i for i, it in enumerate(window.items)
                    if it.kind == MODE_FOLDERS and len(it.videos) >= 2)
    window.toggle_board(False)
    window.show_item(position)
    pump(app, 0.5)

    check(not window.contact_button.isHidden(),
          "un dossier propose sa planche contact")
    check(window.cinema_button.isHidden(),
          "et pas le cinéma, qui ne vaut que pour une vidéo")

    folder = window.items[position]
    plan = build_contact_plan(folder.videos)
    rows = min(CONTACT_ROWS, len(folder.videos))
    check(len(plan) == rows * CONTACT_PER_VIDEO,
          f"la planche fait {rows} ligne(s) de {CONTACT_PER_VIDEO} ({len(plan)})")
    first = [entry[0] for entry in plan[:CONTACT_PER_VIDEO]]
    check(len(set(first)) == 1, "une seule vidéo par ligne")
    moments = [round(entry[1], 2) for entry in plan[:CONTACT_PER_VIDEO]]
    check(len(set(moments)) == CONTACT_PER_VIDEO or plan[0][2] <= 2,
          f"des instants échelonnés en son sein ({moments})")

    window.toggle_contact(True)
    pump(app, 0.4)
    check(window.contact, "la bascule tient")
    check(window.contact_button.isChecked(), "et le bouton le montre")
    window.toggle_contact(False)
    pump(app, 0.3)
    check(not window.contact, "et se relâche")

    # Le cinema efface tout ce qui n'est pas l'image.
    flat_position = next((i for i, it in enumerate(window.items)
                          if it.kind != MODE_FOLDERS), -1)
    window.start_root(flat, MODE_FILES)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    window.toggle_board(False)
    window.show_item(0)
    pump(app, 0.4)
    check(not window.cinema_button.isHidden(), "une vidéo propose le cinéma")
    window.toggle_cinema(True)
    pump(app, 0.3)
    check(window.commands.isHidden() and window.controls.isHidden(),
          "le cinéma efface les barres")
    window.toggle_cinema(False)
    pump(app, 0.3)
    check(not window.commands.isHidden(), "et les rend")

    # La fin d'une video mene a la suivante, elle ne reboucle pas. Il faut une
    # liste qui en comporte plusieurs : le dossier plat a ete vide par les
    # etapes precedentes.
    window.start_root(root, MODE_FLAT)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    window.toggle_board(False)
    window.show_item(0)
    pump(app, 0.4)
    before = window.index
    window.on_video_finished()
    pump(app, 0.4)
    check(window.index == before + 1,
          f"la vidéo finie mène à la suivante ({before} → {window.index})")

    print("\n[56] Densité et pagination")
    filters = window.controls
    check(filters.columns.isHidden(),
          "la liste déroulante « par rangée » a quitté l'écran")

    window.set_board_columns(5)
    filters.set_columns(5)
    check(filters.columns_label.text() == "5",
          f"le chiffre se lit sans rien ouvrir ({filters.columns_label.text()})")

    steps = []
    filters.columnsChanged.connect(steps.append)
    filters.tighter.click()
    check(steps and steps[-1] > 5,
          f"« + » resserre les vignettes ({steps[-1] if steps else None})")
    filters.wider.click()
    filters.wider.click()
    check(steps[-1] < 5, f"« − » les agrandit ({steps[-1]})")
    check(filters.columns_label.text() == str(steps[-1]),
          "et le chiffre suit")

    # Aux extremites, les boutons se desactivent plutot que de ne rien faire.
    filters.set_columns(filters.columns_choices[0])
    check(not filters.wider.isEnabled(), "au plus grand, « − » se désactive")
    filters.set_columns(filters.columns_choices[-1])
    check(not filters.tighter.isEnabled(), "au plus petit, « + » aussi")
    filters.set_columns(window.cfg["board_columns"])

    # Arriver en bas de la planche passe a la page suivante.
    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and window.items, 60)
    window.toggle_board(True)
    pump(app, 0.4)
    board = window.board
    # Une liste fabriquee : la pagination se mesure sur le nombre d'elements,
    # pas sur ce que les etapes precedentes ont laisse dans le jeu d'essai.
    board.items = list(window.items) * 60
    board.set_page(0)
    pump(app, 0.3)
    check(board.total_pages() > 1, "assez d'éléments pour paginer")
    # Hors écran, la zone de défilement n'a pas de hauteur reelle et sa barre
    # reste a zero : on eprouve donc la regle elle-meme, en lui annoncant qu'on
    # est arrive au bout.
    bar = board.scroll.verticalScrollBar()
    bar.setRange(0, 100)
    board._at_end = False
    board._maybe_next_page(100)
    pump(app, 0.3)
    check(board.page == 1,
          f"arriver en bas mène à la page suivante ({board.page})")
    board._maybe_next_page(100)
    check(board.page == 1,
          "et l'on n'en avale pas deux pour un seul arrêt en bas")

    print("\n[57] Doublons et rejet à la souris")
    from videosorter.dupes import MIN_SIZE, group_by_size, walk_sized

    # Le regroupement lui-meme : meme taille, au moins deux fichiers, et les
    # plus lourds d'abord.
    gros = MIN_SIZE + 1000
    paires = [(Path("a/x.mp4"), gros), (Path("b/y.mp4"), gros),
              (Path("c/z.mp4"), gros + 5000), (Path("d/w.mp4"), gros + 5000),
              (Path("e/seul.mp4"), gros + 77),
              (Path("f/minus.mp4"), 10), (Path("g/minus2.mp4"), 10)]
    groupes = group_by_size(paires)
    check(len(groupes) == 2, f"deux groupes de doublons ({len(groupes)})")
    check(groupes[0][0] > groupes[1][0], "le plus lourd d'abord")
    check(all(len(paths) == 2 for _s, paths in groupes), "deux fichiers chacun")
    check(not any("seul" in str(pth) for _s, paths in groupes for pth in paths),
          "un fichier de taille unique n'en est pas un")
    check(not any("minus" in str(pth) for _s, paths in groupes for pth in paths),
          "et les fichiers minuscules sont écartés, trop peu concluants")

    # De bout en bout, sur un dossier fabrique pour l'occasion.
    twins = base / "doublons"
    shutil.rmtree(twins, ignore_errors=True)
    (twins / "un").mkdir(parents=True, exist_ok=True)
    (twins / "deux").mkdir(parents=True, exist_ok=True)
    source = sorted(tri.rglob("*.mp4"))[0]
    shutil.copy2(source, twins / "un" / "copie.mp4")
    shutil.copy2(source, twins / "deux" / "copie.mp4")
    found = group_by_size(walk_sized(twins), minimum=1)
    check(len(found) == 1 and len(found[0][1]) == 2,
          f"les deux copies se retrouvent ({found})")

    # Le rejet a la souris passe par la corbeille de session, donc revient.
    # Sur un dossier fabrique pour l'occasion : ecarter un element dont les
    # etapes suivantes ont besoin rendrait la mesure — et la suite — fausses.
    rejets = base / "rejets"
    shutil.rmtree(rejets, ignore_errors=True)
    for nom in ("un", "deux", "trois"):
        (rejets / nom).mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, rejets / nom / "clip.mp4")
    window.set_tab(TAB_FOLDERS)
    window.start_root(rejets, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    window.toggle_board(True)
    pump(app, 0.4)
    before = window.stats["deleted"]
    position = 0
    target = window.items[position].name
    window.discard_at(position)
    # Le transfert part en tache de fond : on le laisse demarrer, puis l'on
    # attend qu'il aboutisse plutot que de parier sur un delai.
    pump(app, 1.0)
    ok = wait_for(app, lambda: window.stats["deleted"] == before + 1, 30)
    settle(app, window, 15)
    check(ok, f"un clic sur « ✕ » écarte la vignette ({target})")
    window.act_undo()
    ok = wait_for(app, lambda: window.stats["deleted"] == before, 30)
    settle(app, window, 15)
    check(ok, "et Ctrl+Z le ramène")

    print("\n[58] Langage de recherche et sélection")
    from videosorter.query import describe, matches_text, parse

    check(matches_text("plage ete 2019.mp4", "plage"), "un mot simple")
    check(matches_text("Plage Été.mp4", "plage ete"),
          "deux mots exigés, casse et accents ignorés")
    check(not matches_text("plage.mp4", "plage montagne"),
          "et il les faut tous les deux")
    check(matches_text("mer.mp4", "plage or mer"), "« or » ouvre le choix")
    check(matches_text("plage.mp4", "plage or mer"), "d'un côté comme de l'autre")
    check(not matches_text("plage hiver.mp4", "plage -hiver"),
          "« - » écarte")
    check(matches_text("plage ete.mp4", "plage -hiver"),
          "sans écarter le reste")
    check(matches_text("saison 2 final.mp4", '"saison 2"'),
          "les guillemets tiennent l'expression")
    check(not matches_text("saison 3.mp4", '"saison 2"'),
          "et n'attrapent qu'elle")
    check(matches_text("n importe quoi.mp4", ""), "une recherche vide laisse tout")

    groups, excluded = parse("plage or mer -hiver")
    check(groups == [["plage", "mer"]] and excluded == ["hiver"],
          f"l'analyse rend groupes et exclusions ({groups}, {excluded})")
    check("sans" in describe("plage -hiver"), "et se résume en clair")

    # La selection au clavier porte sur toute la liste, pas sur la page.
    window.set_tab(TAB_FOLDERS)
    window.start_root(tri, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    window.toggle_board(True)
    pump(app, 0.4)
    window.pick_all()
    check(len(window.board.picked_ids) == len(window.items),
          f"Ctrl+A coche tout ({len(window.board.picked_ids)})")
    window.pick_invert()
    check(not window.board.picked_ids, "Ctrl+I inverse, donc décoche tout")
    window.pick_invert()
    check(len(window.board.picked_ids) == len(window.items),
          "et le rétablit")
    window.clear_picked()
    check(not window.board.picked_ids, "Ctrl+N décoche")

    print("\n[59] Fiche des raccourcis")
    import re as _re
    from videosorter.help import SEARCH_HELP, SHORTCUTS, HelpDialog, documented_keys

    sheet = HelpDialog()
    check(sheet.windowTitle().startswith("Raccourcis"), "la fiche s'ouvre")
    check(len(SHORTCUTS) >= 4, f"elle compte plusieurs sections ({len(SHORTCUTS)})")
    check(all(rows for _t, rows in SHORTCUTS), "aucune section vide")
    check(len(SEARCH_HELP) >= 4, "et la syntaxe de recherche y figure")
    sheet.deleteLater()

    # Le garde-fou : toute touche traitee sous Ctrl doit etre citee. Sans lui,
    # la fiche vieillirait en silence des le prochain raccourci ajoute.
    source = (Path(__file__).resolve().parents[1]
              / "videosorter" / "window.py").read_text(encoding="utf-8")
    handled = set(_re.findall(r"key == Qt\.Key_([A-Z])\b", source))
    documented = documented_keys()
    missing = sorted(handled - documented)
    check(not missing,
          f"toutes les touches traitées sont documentées (manquent : {missing})")

    print("\n[60] État des vignettes")
    from videosorter.backfill import ThumbAudit, ThumbBackfill

    audite = base / "audit"
    shutil.rmtree(audite, ignore_errors=True)
    (audite / "lot").mkdir(parents=True, exist_ok=True)
    # Une source prise a l'instant : les etapes precedentes ont pu deplacer
    # celles qu'elles utilisaient.
    modele = next(iter(sorted(root.rglob("*.mp4"))), None)
    check(modele is not None, "une vidéo modèle est disponible")
    for index in range(3):
        shutil.copy2(modele, audite / "lot" / f"clip_{index}.mp4")

    def run_audit():
        out = {}
        worker = ThumbAudit(audite, window.cfg["thumb_width"], True)
        worker.done.connect(lambda seen, ready: out.update(seen=seen, ready=ready))
        worker.start()
        wait_for(app, lambda: "seen" in out, 120)
        return out

    before = run_audit()
    check(before.get("seen") == 3, f"l'audit voit les trois vidéos ({before})")
    check(before.get("ready") == 0, "et aucune n'a encore sa vignette")

    made = {}
    worker = ThumbBackfill(audite, window.cfg["thumb_width"], True)
    worker.done.connect(lambda m, k, whole: made.update(m=m, k=k))
    worker.start()
    wait_for(app, lambda: "m" in made, 180)

    after = run_audit()
    check(after.get("ready") == 3,
          f"après préparation, les trois sont prêtes ({after})")
    check(after.get("seen") == 3, "et le total ne bouge pas")

    print("\n[61] Double lecteur, reprise, sauvegarde différée")
    import json as _json
    # Les trois clips de l'audit : « flat » a ete vide par les sections d'avant.
    window.set_tab(TAB_FOLDERS)
    window.start_root(audite / "lot", MODE_FLAT)
    check(wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60),
          f"trois clips a plat ({len(window.items)})")
    window.toggle_board(False)
    pump(app, 0.3)
    sp = window.single
    window.show_item(0)
    pump(app, 0.2)
    nxt = str(window.items[1].path)
    check(sp.spare.path == nxt, "la vidéo suivante part en réserve")
    check(wait_for(app, lambda: sp.spare.primed, 20),
          "et s'arrête sur sa première image")
    window.show_item(1)
    check(not sp._blackout and not sp.video.isHidden(),
          "flèche : l'image est là, sans noir")
    check(Path(sp.player.source().toLocalFile()) == Path(nxt),
          "le lecteur actif lit bien la suivante")
    pump(app, 0.3)
    check(sp.spare.path == str(window.items[2].path), "et la réserve recharge")
    window.show_item(0)
    check(sp._blackout, "en arrière, le chemin classique avec son noir")

    wanted = window.items[2].item_id
    window._resume_id = wanted
    window._resume_hop = False
    window.set_tab(TAB_VIDEOS)
    pump(app, 0.3)
    check(window._resume_id == "" and window.items[window.index].item_id == wanted,
          "reprise sur le dernier élément regardé")
    deep = next(iter(sorted(root.rglob("*.mp4"))))
    window._resume_id = str(deep)
    window._resume_hop = False
    window.start_root(root, MODE_FOLDERS)
    check(wait_for(app, lambda: not window.scanning and window._resume_id == "", 60),
          "la racine ne le contient pas : on descend une fois")
    check(window.root == deep.parent, "dans son dossier")

    window.cfg["tab"] = "zzz"
    window.cfg.save_soon()
    read = lambda: _json.loads(window.cfg.path.read_text(encoding="utf-8")).get("tab")
    check(read() != "zzz", "la sauvegarde différée n'écrit pas tout de suite")
    pump(app, 0.7)
    check(read() == "zzz", "mais une demi-seconde plus tard")
    window.cfg["tab"] = TAB_FOLDERS
    window.cfg.save()

    print("\n[62] Mur, peek, scrub, non-vus, rafale, recherches, doublons par image")
    import subprocess as _sp
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent
    from PySide6.QtMultimedia import QMediaPlayer as _QMP
    from PySide6.QtWidgets import QInputDialog
    from videosorter.dupes import ImageDuplicateScan, dhash, group_by_look
    from videosorter.split import grid_for
    from videosorter.index import INDEX
    from videosorter.backfill import ThumbBackfill

    # -- le mur : grille, nombre, orientation, plein ecran --------------------
    check(grid_for(3, "vertical") == (1, 3) and grid_for(6, "vertical") == (2, 3),
          "verticales : une ligne jusqu'à cinq")
    check(grid_for(9, "any") == (3, 3) and grid_for(10, "horizontal") == (2, 5),
          "horizontales : le carré, ou presque")
    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 4, 60)
    window.set_tab(TAB_SPLIT)
    pump(app, 0.3)
    check(len(window.wall.panes) == 3, "le mur arrive avec trois panneaux")
    window.set_wall_count(6)
    pump(app, 0.2)
    check(len(window.wall.panes) == 6 and window.cfg["wall_panes"] == 6,
          "six panneaux, et c'est retenu")
    window.set_wall_orientation("any")
    pump(app, 0.3)
    check(len(window.wall.pool) >= 4, f"« Toutes » : le vivier prend tout ({len(window.wall.pool)})")
    window.toggle_wall_fullscreen()
    pump(app, 0.4)
    check(window.wall_full and window.tabs.isHidden() and window.wall.controls.isHidden()
          and window.wall.panes[0].bar.isHidden(), "plein écran : plus rien autour")
    window.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    pump(app, 0.4)
    check(not window.wall_full and not window.tabs.isHidden(), "Échap : tout revient")
    window.set_wall_count(3)
    window.set_wall_orientation("vertical")

    # -- la fiche : peek, scrub, compteur, non-vus, rafale ----------------------
    window.set_tab(TAB_FOLDERS)
    window.start_root(audite / "lot", MODE_FLAT)
    check(wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60),
          "trois clips à plat")
    window.toggle_board(False)
    pump(app, 0.3)
    sp = window.single
    window.show_item(0)
    pump(app, 0.3)
    window.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Shift, Qt.ShiftModifier))
    pump(app, 0.1)
    check(sp.peeking and not sp.peek.isHidden(), "Maj enfoncée : la mosaïque est là")
    check(sp.peek.cells[0].caption.text().startswith("6"),
          f"les cases portent les destinations ({sp.peek.cells[0].caption.text()!r})")
    peek_key = f"peek@{window.current.item_id}"
    check(wait_for(app, lambda: len(window.peek_thumbs.get(peek_key, {})) == 9, 60),
          "neuf images arrivent")
    window.keyReleaseEvent(QKeyEvent(QEvent.KeyRelease, Qt.Key_Shift, Qt.NoModifier))
    pump(app, 0.1)
    check(not sp.peeking and sp.peek.isHidden(), "Maj relâchée : elle disparaît")

    check(wait_for(app, lambda: sp.player.duration() > 0, 30), "durée connue")
    duration = sp.player.duration()
    width = sp.video_area.width()

    def _mouse(kind, x, buttons=Qt.LeftButton):
        return QMouseEvent(kind, QPointF(x, 50), QPointF(x, 50), Qt.LeftButton,
                           buttons, Qt.NoModifier)
    sp.player.setPosition(0)
    pump(app, 0.2)
    sp._scrub_press(_mouse(QEvent.MouseButtonPress, 100))
    sp._scrub_move(_mouse(QEvent.MouseMove, 100 + width // 2))
    pump(app, 0.3)
    check(abs(sp.player.position() - duration // 2) < duration * 0.15,
          f"glisser d'une demi-largeur = une demi-durée ({sp.player.position()} / {duration})")
    sp._scrub_release(_mouse(QEvent.MouseButtonRelease, 100 + width // 2, Qt.NoButton))
    sp._scrub_press(_mouse(QEvent.MouseButtonPress, 100))
    sp._scrub_release(_mouse(QEvent.MouseButtonRelease, 101, Qt.NoButton))
    pump(app, 0.2)
    check(sp.player.playbackState() == _QMP.PlaybackState.PausedState, "un clic met en pause")
    sp.toggle_pause()

    decisions = window._decisions
    window.act_skip()
    pump(app, 0.2)
    check(window._decisions == decisions + 1 and "/min" in window.controls.count.text(),
          "une décision compte, et la ligne le dit")

    before = len(window.items)
    seen_id = window.items[-1].item_id
    INDEX.mark_seen(seen_id)
    window.set_only_unseen(True)
    pump(app, 0.2)
    check(len(window.items) == before - 2,
          f"« Non vus » écarte le passé et le vu ({len(window.items)} sur {before})")
    window.set_only_unseen(False)
    pump(app, 0.2)
    check(len(window.items) == before and INDEX.is_seen(seen_id), "et tout revient")

    window.burst_timer.setInterval(300)
    window.toggle_burst()
    window.show_item(0)
    check(wait_for(app, lambda: window.index >= 1, 5),
          f"rafale : la suivante arrive toute seule ({window.index})")
    window.toggle_burst()

    # -- recherches enregistrees -------------------------------------------------
    kept_text, kept_item = QInputDialog.getText, QInputDialog.getItem
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Mes clips", True))
    QInputDialog.getItem = staticmethod(lambda *a, **k: ("Mes clips", True))
    window.controls.include.setText("clip")
    window.on_controls_changed()
    window.set_only_unseen(True)
    window.save_search()
    saved = window.cfg["searches"]
    check(len(saved) == 1 and saved[0]["query"] == "clip" and saved[0]["unseen"],
          "la recherche s'enregistre avec sa requête et « Non vus »")
    window.controls.include.setText("")
    window.on_controls_changed()
    window.set_only_unseen(False)
    window.apply_search(saved[0])
    pump(app, 0.3)
    check(window.controls.include.text() == "clip" and window.cfg["only_unseen"],
          "et se repose d'un clic")
    window.forget_search()
    check(not window.cfg["searches"], "puis s'oublie")
    QInputDialog.getText, QInputDialog.getItem = kept_text, kept_item
    window.set_only_unseen(False)
    window.controls.include.setText("")
    window.on_controls_changed()

    # -- doublons par image -----------------------------------------------------
    img = QImage(64, 64, QImage.Format.Format_RGB32)
    for y in range(64):
        for x in range(64):
            img.setPixelColor(x, y, QColor(x * 4 % 256, (x * y) % 256, y * 4 % 256))
    pa, pb, pc = base / "_appdata" / "a.png", base / "_appdata" / "b.jpg", base / "_appdata" / "c.png"
    img.save(str(pa))
    img.scaled(200, 120).save(str(pb), "JPG", 60)
    flat_img = QImage(64, 64, QImage.Format.Format_RGB32)
    flat_img.fill(QColor(0, 0, 0))
    flat_img.save(str(pc))
    ha, hb = dhash(pa), dhash(pb)
    check(ha is not None and bin(ha ^ hb).count("1") <= 6,
          "redimensionnée et recompressée, l'empreinte reste proche")
    check(dhash(pc) is None, "une image plate n'en a pas")
    groups = group_by_look([(pa, 10, ha), (pb, 20, hb), (Path("z"), 5, ha ^ 0xFFFFFFFF)])
    check(len(groups) == 1 and set(groups[0][1]) == {pa, pb}, "deux proches, un groupe")

    look = base / "look" / "lot"
    shutil.rmtree(base / "look", ignore_errors=True)
    look.mkdir(parents=True)
    model = next(iter(sorted(root.rglob("*.mp4"))))
    shutil.copy2(model, look / "un.mp4")
    shutil.copy2(model, look / "deux.mp4")
    # Les clips du jeu se ressemblent tous : il en faut un vraiment autre.
    _sp.run([Tools.ffmpeg, "-y", "-v", "error", "-f", "lavfi",
             "-i", "mandelbrot=size=320x180:rate=10", "-t", "30",
             "-pix_fmt", "yuv420p", str(look / "autre.mp4")], check=True)
    made = {}
    worker = ThumbBackfill(base / "look", window.cfg["thumb_width"], True)
    worker.done.connect(lambda m, k, whole: made.update(m=m))
    worker.start()
    check(wait_for(app, lambda: "m" in made, 120), "vignettes fabriquées")
    out = {}
    scan = ImageDuplicateScan(base / "look", window.cfg["thumb_width"], True)
    scan.found.connect(lambda g: out.update(groups=g))
    scan.start()
    check(wait_for(app, lambda: "groups" in out, 60), "balayage terminé")
    found = out["groups"]
    check(len(found) == 1 and {q.name for q in found[0][1]} == {"un.mp4", "deux.mp4"},
          f"les deux copies se retrouvent, la troisième non ({found})")

    print("\n[63] État de la collection en haut, et noir au survol")
    window.set_tab(TAB_FOLDERS)
    window.origin = root
    window.start_root(root, MODE_FOLDERS, new_origin=True)
    # Les sections d'avant ont deplace des videos : on ne presume plus du compte.
    check(wait_for(app, lambda: not window.scanning and len(window.items) >= 1, 60),
          "analyse de la racine")
    text = window.state_button.text()
    state = window.cfg["collection"]
    check(int(state.get("videos") or 0) > 0 and "vidéos" in text and "analysé" in text,
          f"la racine analysée donne un compte et une date ({text!r})")
    window._told_count(root, 19)
    check(window.state_button.text().startswith("19 vidéos")
          and window.cfg["collection"].get("counted_at"),
          "le comptage met le nombre à jour")
    window._told_audit(19, 5)
    check("5 vignettes (26 %)" in window.state_button.text(),
          f"la vérification dit combien de vignettes ({window.state_button.text()!r})")
    window.on_backfill_progress(3, 2, 19)
    check("préparation 5 / 19" in window.state_button.text(),
          "la préparation s'affiche en direct")
    window.on_backfill_done(3, 2, False)
    check("5 vignettes" in window.state_button.text(), "et laisse son bilan")
    window._audit_after_count = True
    window._told_count(root, 19)
    check(window.audit is not None, "un clic enchaîne comptage puis vérification")
    wait_for(app, lambda: window.audit is None, 60)
    check("Cliquer" in window.state_button.toolTip(), "l'infobulle explique")

    window.toggle_board(True)
    pump(app, 0.3)
    b = window.board
    b.hover_timer.stop()
    b.hovered = 0
    b._play(0)
    check(b._blackout and b.video.isHidden(), "au survol, un noir franc d'abord")
    check(wait_for(app, lambda: not b._blackout and b._loaded, 10), "le média se charge, le noir se lève")
    check(wait_for(app, lambda: not b.video.isHidden(), 20), "puis l'image vient — la sienne")
    b.stop()
    b.hovered = -1
    b.hover_timer.start()

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
    from videosorter.index import INDEX
    INDEX.reopen(sandbox / "index.db")

    app = QApplication.instance() or QApplication(sys.argv)
    cfg = Config(path=sandbox / "config.json")
    cfg["delete_mode"] = "local_trash"
    cfg.set_destinations([
        {"key": "6", "label": "2019", "path": str(tri / "2019")},
        {"key": "7", "label": "2020", "path": str(tri / "2020")},
    ])
    window = MainWindow(cfg)
    window.resize(1400, 900)
    window.show()
    pump(app, 0.2)

    # ---------------------------------------------------------------- scan
    print("\n[1] Analyse du dossier racine (mode dossiers)")
    window.start_root(root, MODE_FOLDERS)
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
    # L'édition n'est plus un onglet : l'application s'ouvre sur la planche, et
    # l'on descend à la fiche en ouvrant un élément. Ce sont les aperçus de la
    # fiche que l'on éprouve ici, il faut donc commencer par y entrer.
    window.toggle_board(False)
    window.show_item([i.name for i in window.items].index("Anniversaire"))
    pump(app, 0.5)
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
    window.start_root(flat, MODE_FLAT)
    ok = wait_for(app, lambda: not window.scanning and len(window.items) == 4, 60)
    check(ok, f"4 vidéos listées (obtenu {len(window.items)})")
    check(window.mode == MODE_FLAT,
          f"un dossier sans sous-dossier montre ses vidéos (obtenu {window.mode})")
    ok = wait_for(app, lambda: bool(window.current.info.get("duration")), 30)
    check(ok, "durée lue par ffprobe")
    check(window.current.info.get("width") == 320, "résolution lue par ffprobe")
    check(window.viewer.currentWidget() is window.single, "lecteur plein cadre affiché")

    # Cinq reperes suffisent a se deplacer dans une video, et la pellicule est
    # passee a droite du lecteur : en bas, elle lui volait quatre-vingt-dix
    # pixels sur toute la largeur.
    from videosorter.widgets import SinglePlayer
    width = SinglePlayer.STRIP_COUNT
    ok = wait_for(
        app, lambda: sum(1 for t in window.single.tiles if t._pixmap) >= width, 90)
    check(ok, f"pellicule de {width} images pour la vidéo courante")
    plan_file = window.plans.get(f"{window.current.path}@0", [])
    check(len({round(entry[1], 2) for entry in plan_file}) == width,
          f"{width} instants distincts dans la même vidéo")
    check(len(window.single.tiles) == width, "et autant de cases sous la main")
    tiles = window.single.tiles
    check(tiles[1].y() > tiles[0].y() and tiles[1].x() == tiles[0].x(),
          "empilées verticalement, donc posées sur le côté")

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
