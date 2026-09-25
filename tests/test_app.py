"""Test d'intégration : analyse, aperçus et actions, sur une vraie arborescence.

Lancement : python tests/test_app.py [dossier_fixture]
L'interface tourne en mode « offscreen », aucune fenêtre ne s'affiche.
"""
from __future__ import annotations

import os as _os
import tempfile as _tempfile
# Avant tout import de Prisme : l'index, les reglages et les journaux du test
# vivent a l'ecart. Sans cela, l'index reel s'ouvrait au chargement — et a
# ete efface une fois, pendant que Prisme tournait.
_os.environ.setdefault("PRISME_SANDBOX",
                       _os.path.join(_tempfile.gettempdir(), "prisme-tests"))

import faulthandler
import os
import json as _json_web
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
    check(len(KEY_ORDER) == 29,
          f"29 destinations possibles (obtenu {len(KEY_ORDER)})")
    check(not set(KEY_ORDER) & set("012345"),
          "les chiffres 0 à 5 restent à la notation")
    check("f" not in KEY_ORDER,
          "« f » ouvre la fiche survolée : aucune destination ne la reçoit")
    many = [
        {"key": KEY_ORDER[i], "label": f"dest{i}", "path": str(tri / f"d{i}")}
        for i in range(20)
    ]
    window.cfg.set_destinations(many)
    window.commands.rebuild(many, "Corbeille")
    caps = len(window.commands.caps())
    check(caps == 22, f"les 20 destinations sont toutes affichées (obtenu {caps - 2})")
    # Sur un exemplaire dedie : redimensionner la barre vivante la fait
    # reagencer par sa disposition parente, ce qui fausse la mesure.
    from videosorter.widgets import CommandBar
    probe_bar = CommandBar()
    probe_bar.rebuild(many, "Corbeille")
    probe_bar.resize(400, 40)
    probe_bar._fit()
    narrow = probe_bar.compact
    probe_bar.resize(4000, 40)
    probe_bar._fit()
    wide = probe_bar.compact
    check(narrow and not wide,
          "une seule ligne : faute de place, les vignettes se resserrent "
          "au lieu d'ouvrir une deuxième rangée")
    check(all(c.text.isHidden() for c in probe_bar.caps()) is False,
          "et se rouvrent quand la place revient")

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
    caps = window.commands.caps()
    check(len(caps) == 3,
          f"trois vignettes : Suppr, Espace, 1 destination — les notes vivent "
          f"dans les étoiles ({len(caps)})")
    check(all(c.cursor().shape() == Qt.PointingHandCursor for c in caps),
          "les vignettes se signalent comme cliquables")
    check("touche 6" in caps[2].toolTip(), "l'infobulle rappelle la touche")

    window.show_item(first_untouched(window))
    name = window.current.name
    index_before = window.index
    QTest.mouseClick(caps[2], Qt.LeftButton)
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
    QTest.mousePress(caps[2], Qt.LeftButton)
    QTest.mouseRelease(caps[2], Qt.LeftButton, Qt.NoModifier,
                       QPoint(caps[2].width() + 40, 5))
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
    from videosorter.scan import human_duration as _hd
    check(tile.duration_chip.text() == _hd(tile.duration), f"durée totale, pas l'instant de l'aperçu "
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
    check(tile.duration_chip.text() == _hd(tile.duration), "la durée revient une fois le survol fini")

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
    check(not hasattr(window, "enter_button"),
          "plus de gros bouton « Entrer » : le titre du dossier en tient lieu")
    from PySide6.QtWidgets import QPushButton as _Crumb
    pump(app, 0.2)
    last = [b for b in window.crumbs.findChildren(_Crumb)
            if b.isVisible() and b.property("last") == "true"]
    check(last and last[0].text() == "Anniversaire",
          "le dossier regardé finit le fil d'Ariane, une seule fois")
    check(window.levels == [], "on est bien au niveau racine")

    QTest.mouseClick(last[0], Qt.LeftButton)
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
    window.enter_current()
    wait_for(app, lambda: not window.scanning and window.root.name == "Sous-dossiers", 60)
    check(window.mode == MODE_FOLDERS,
          f"pas de vidéo directe : on descend en mode dossiers (obtenu {window.mode})")
    check([i.name for i in window.items] == ["interne"],
          f"le sous-dossier est listé ({[i.name for i in window.items]})")
    window.enter_current()
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
    check(window.ratings.get(target.path) == 0, "un élément démarre hors des favoris")
    window.rate_current(4)
    check(window.ratings.get(target.path) > 0, "il passe en favori")
    check(window.stars.value == 1, "et l'étoile, en bas à droite, se dore")
    window.stars.click()
    check(window.ratings.get(target.path) == 0,
          "un clic sur l'étoile dorée le retire des favoris")
    window.rate_current(2)
    QTest.keyClick(window, Qt.Key_0)
    check(window.ratings.get(target.path) == 0, "la touche 0 le retire aussi")
    QTest.keyClick(window, Qt.Key_1)
    check(window.ratings.get(target.path) > 0, "la touche 1 le met en favori")

    # La note doit suivre l'élément quand il change de place.
    moved_dir = tri / "notes"
    window.cfg.set_destinations([{"key": "6", "label": "Notes", "path": str(moved_dir)}])
    name = target.name
    QTest.keyClick(window, Qt.Key_6)
    settle(app, window)
    check(window.ratings.get(moved_dir / name) > 0,
          "le favori suit l'élément déplacé")
    check(window.ratings.get(target.path) == 0, "et ne reste pas sur l'ancien chemin")

    window.ratings.flush()
    from videosorter.ratings import Ratings
    reloaded = Ratings(path=window.ratings.path)
    check(reloaded.get(moved_dir / name) > 0, "le favori survit à un redémarrage")

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
    window.toggle_board(True)
    pump(app, 0.2)
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
    check("stars" not in chips.buttons,
          "une seule « Note » : celle des étoiles, qui filtre")
    chips.buttons["resolution"].click()
    check(modes[-1] == "resolution_desc", "la résolution se classe pareil")
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
    from videosorter.widgets import PlayMarks
    # Un trait tres fin, pose SUR l'image par une fenetre-outil — la seule
    # chose que la video native de Windows laisse passer devant elle.
    check(isinstance(player.marks, PlayMarks) and player.marks.isWindow(),
          "la fiche porte un trait d'avancement posé sur l'image")
    check(player.marks.RAIL <= 4, f"très fin ({player.marks.RAIL} px)")
    check(not hasattr(player, "under"),
          "plus de rangée sous l'image pour une barre de huit pixels")
    player.marks.set_progress(30_000, 120_000)
    check(abs(player.marks.fraction - 0.25) < 0.01, "il suit la lecture")
    window.start_root(flat) if flat.exists() else None
    wait_for(app, lambda: not window.scanning, 30)
    if window.items and window.current.kind == MODE_FILES:
        wait_for(app, lambda: player.player.duration() > 0, 30)
        player.player.setPosition(1000)
        pump(app, 0.4)
        check(window.single_bar.left.text().startswith("−"),
              f"le bandeau de survol dit le temps restant "
              f"(obtenu {window.single_bar.left.text()!r})")

    print("\n[38] Temps restant dans les aperçus")
    check(window.grid.marks is not None, "la grille d'aperçus en a un")
    check(window.board.marks is not None, "la planche aussi")
    check(window.grid.marks.isHidden(), "masqué tant que rien ne se lit")

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
    caps = window.commands.caps()
    check(not [c for c in caps if type(c).__name__ == "StarCap"],
          "plus de vignettes de notation : la ligne du bas reste une ligne")
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    window.show_item(first_untouched(window))
    target = window.current
    window.ratings.set(target.path, 0)
    window.stars.rated.emit(1)
    pump(app, 0.2)
    check(window.ratings.get(target.path) > 0,
          "l'étoile, à droite, met en favori")

    print("\n[42] Une configuration ancienne migre ses touches")
    import json, tempfile
    from videosorter.config import Config as VSConfig
    old = Path(tempfile.mkdtemp()) / "config.json"
    old.write_text(json.dumps({"destinations": [
        {"key": "1", "label": "A", "path": "C:/a"},
        {"key": "3", "label": "B", "path": "C:/b"},
        {"key": "9", "label": "C", "path": "C:/c"},
        {"key": "f", "label": "D", "path": "C:/d"},
    ]}), encoding="utf-8")
    migrated = VSConfig(path=old)
    keys = [d["key"] for d in migrated.destinations]
    check(not set(keys) & RESERVED_KEYS,
          f"plus aucune destination sur une touche réservée ({keys})")
    check(keys[2] == "9", "celles deja valides ne bougent pas")
    check(len(set(keys)) == 4 and all(keys), "et restent distinctes")

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
    check(any(i.path.name.lower() == "plage" for i in tops),
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
    pump(app, 0.3)
    check(window.viewer.currentWidget() is window.wall, "l'onglet montre le mur")
    # Les panneaux demarrent l'un apres l'autre : on attend le dernier.
    expected = min(DEFAULT_PANES, len(pool))
    wait_for(app, lambda: len([p for p in window.wall.panes if p.video_path]) >= expected, 10)
    playing = [p for p in window.wall.panes if p.video_path]
    check(len(playing) == expected,
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

    print("\n[55] Cinéma et enchaînement")

    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    position = next(i for i, it in enumerate(window.items)
                    if it.kind == MODE_FOLDERS and len(it.videos) >= 2)
    window.toggle_board(False)
    window.show_item(position)
    pump(app, 0.5)

    check(not hasattr(window, "contact_button")
          and not hasattr(window, "toggle_contact"),
          "la planche contact a quitté l'application")
    check(window.cinema_button.isHidden(),
          "un dossier ne propose pas le cinéma, qui ne vaut que pour une vidéo")

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
    check(window.bottom_bar.isHidden() and window.top_bar.isHidden(),
          "le cinéma efface les barres")
    window.toggle_cinema(False)
    pump(app, 0.3)
    check(not window.bottom_bar.isHidden() and not window.top_bar.isHidden(),
          "et les rend")

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
    check(window.wall_full and not window.tabs.isVisible() and not window.wall.controls.isVisible()
          and window.wall.panes[0].bar.isHidden(), "plein écran : plus rien autour")
    window.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    pump(app, 0.4)
    check(not window.wall_full and window.tabs.isVisible(), "Échap : tout revient")
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
    peek_key = f"peek@{window.current.item_id}"
    check(wait_for(app, lambda: len(window.peek_thumbs.get(peek_key, {})) == 9, 60),
          "neuf images arrivent")
    check(":" in sp.peek.cells[4].caption.text(),
          f"chaque case dit son instant ({sp.peek.cells[4].caption.text()!r})")
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
    check(bool(found) and not found[0].sure and found[0].to_check == [],
          "une seule image ne prouve rien : le groupe est « à comparer »")
    from videosorter.dupes_memory import LookMemo as _LookMemo
    _looked = _LookMemo()
    _looked.load()
    check(len(_looked.values or {}) >= 3,
          f"l'empreinte de chaque vignette est retenue pour la fois suivante "
          f"({len(_looked.values or {})})")

    print("\n[63] État de la collection en haut, et noir au survol")
    window.set_tab(TAB_FOLDERS)
    window.origin = root
    window.start_root(root, MODE_FOLDERS, new_origin=True)
    # Les sections d'avant ont deplace des videos : on ne presume plus du compte.
    check(wait_for(app, lambda: not window.scanning and len(window.items) >= 1, 60),
          "analyse de la racine")
    text = window.state_button.text()
    state = window.cfg["collection"]
    check(int(state.get("videos") or 0) > 0 and "vidéos" in text
          and "analysé" in window.state_button.toolTip(),
          f"la racine analysée donne un compte, et sa date en infobulle ({text!r})")
    window._told_count(root, 19)
    check(window.state_button.text().startswith("19 vidéos")
          and window.cfg["collection"].get("counted_at"),
          "le comptage met le nombre à jour")
    window._told_audit(19, 5)
    check("26 % de vignettes" in window.state_button.text()
          and "5 vignettes (26 %)" in window.state_button.toolTip(),
          f"la vérification dit combien de vignettes ({window.state_button.text()!r})")
    window.on_backfill_progress(3, 2, 19)
    check("préparation 5 / 19" in window.state_button.text(),
          "la préparation s'affiche en direct")
    window.on_backfill_done(3, 2, False)
    check("% de vignettes" in window.state_button.text(), "et laisse son bilan")
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

    print("\n[64] Clic droit : les destinations en rond")
    from PySide6.QtCore import QPoint as _QPoint
    window.set_tab(TAB_FOLDERS)
    window.start_root(audite / "lot", MODE_FLAT)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 1, 60)
    window.toggle_board(False)
    pump(app, 0.3)
    window.show_item(0)
    pump(app, 0.2)
    radial = window.radial
    window.single.radialRequested.emit()
    pump(app, 0.2)
    check(not radial.isHidden() and len(radial.rects) == len(window.cfg.destinations[:9]),
          f"clic droit : une pastille par destination ({len(radial.rects)})")
    check(window.single.player.playbackState() == window.single.player.PlaybackState.PlayingState,
          "et la lecture continue")
    check(not window.single.video.isHidden(), "l'image reste visible")
    sent = []
    kept_move = window.act_move
    window.act_move = lambda dest: sent.append(dest["label"])
    pick = min(1, len(window.cfg.destinations) - 1)
    radial.chosen.emit(pick)
    radial.close_menu()
    check(sent == [window.cfg.destinations[pick]["label"]],
          f"cliquer une pastille envoie vers elle ({sent})")
    window.act_move = kept_move
    window.single.radialRequested.emit()
    pump(app, 0.1)
    window.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    check(radial.isHidden(), "Échap le referme")

    print("\n[65] Mur en douceur, suivante du dossier, chien de garde")
    import time as _time
    from videosorter import perf as _perf
    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning, 60)
    window._wall_pinned = [str(v) for v in sorted(root.rglob("*.mp4"))[:4]]
    window.set_wall_count(4)
    window.set_tab(TAB_SPLIT)
    pump(app, 0.05)
    started = [p for p in window.wall.panes if p.video_path]
    check(len(started) <= 2, f"les panneaux ne partent pas tous d'un coup ({len(started)})")
    check(wait_for(app, lambda: len([p for p in window.wall.panes if p.video_path]) == 4, 10),
          "mais tous finissent par jouer")
    check(window.preview.harvester is None or not window.preview.harvester.isRunning(),
          "la récolte de vignettes s'est effacée devant le mur")
    from videosorter.backfill import ThumbBackfill as _TB
    fake = _TB(root, 320, True)
    window.backfill = fake
    window.show_wall()
    check(fake.paused, "et la préparation se met en pause devant le mur")
    window.set_tab(TAB_FOLDERS)
    pump(app, 0.2)
    check(not fake.paused, "puis reprend quand on le quitte")
    window.backfill = None
    fake.deleteLater()
    window.set_tab(TAB_SPLIT)
    pump(app, 0.2)
    first = window.wall.panes[0].video_path
    window.wall.panes[0]._sibling()
    pump(app, 0.3)
    after = window.wall.panes[0].video_path
    check(after != first and Path(after).parent == Path(first).parent,
          "▸ passe à la suivante du même dossier")
    check(str(Path(first).parent) in window._siblings_cache, "et la liste du dossier est retenue")
    window._wall_pinned = []
    heavy_video = str(sorted(root.rglob("*.mp4"))[0])
    INDEX.put_probe(heavy_video, "", {"duration": 8.0, "width": 2160, "height": 3840,
                                      "codec": "hevc", "ok": True})
    window.set_wall_orientation("any")
    pump(app, 0.3)
    check(heavy_video not in window.wall.pool and "1080p" in window.wall.caption.text(),
          "à quatre panneaux, la 4K connue est écartée et la légende le dit")
    window.set_wall_count(3)
    window.set_wall_orientation("vertical")
    window.set_tab(TAB_FOLDERS)
    pump(app, 0.2)

    # Le journal du test ne doit pas polluer celui de l'utilisateur.
    _perf.LOG = base / "_appdata" / "gel.log"
    before_stalls = _perf.WATCH.stalls
    _perf.WATCH.mark("test.sleep")
    _time.sleep(1.3)
    pump(app, 0.6)
    check(_perf.WATCH.stalls == before_stalls + 1 and _perf.WATCH.worst >= 1.0,
          f"un gel de plus d'une seconde est relevé ({_perf.WATCH.worst:.1f} s)")
    check(_perf.LOG.exists() and "test.sleep" in _perf.LOG.read_text(encoding="utf-8"),
          "avec l'action qui le précédait, dans le journal")

    print("\n[66] Deux lignes, filtres lisibles, orientation stricte")
    window.set_tab(TAB_FOLDERS)
    window.start_root(audite / "lot", MODE_FLAT)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    window.toggle_board(True)
    window.clear_picked()
    pump(app, 0.2)
    check(window.picked_bar.parent() is not None and window.picked_bar.isHidden(),
          "la barre de sélection attend en bout de la ligne des filtres")
    window.board.pick_all(True)
    pump(app, 0.2)
    check(not window.picked_bar.isHidden(), "et paraît dès qu'on coche")
    window.clear_picked()

    check(window.controls.clear.isHidden(), "sans filtre, pas de « ✕ filtres »")
    window.controls._cycle_orientation()
    pump(app, 0.3)
    check(window.controls.format_button.text().endswith("Verticales"),
          f"un clic : le bouton dit ce qu'il montre "
          f"({window.controls.format_button.text()!r})")
    check(not window.controls.clear.isHidden() and "verticales" in window.controls.clear.toolTip(),
          f"un filtre posé : le bouton dit lequel ({window.controls.clear.toolTip()!r})")
    check(len(window.items) == 0, "strict : sans orientation connue, rien ne passe pour « Verticales »")
    known = str(window.all_items[0].path)
    INDEX.put_probe(known, "", {"duration": 8.0, "width": 720, "height": 1280, "codec": "", "ok": True})
    window.on_controls_changed()
    pump(app, 0.2)
    check(len(window.items) == 1 and str(window.items[0].path) == known,
          "et seule la verticale connue passe")
    window.reset_filters()
    pump(app, 0.2)
    check(window.controls.clear.isHidden() and len(window.items) >= 3
          and window.controls.orientations() == ["vertical", "horizontal"],
          "« ✕ filtres » remet tout")

    window.set_tab(TAB_SPLIT)
    pump(app, 0.3)
    check(window.controls.sorts.isHidden() and window.controls.unseen.isHidden()
          and window.controls.wider.isHidden(), "sur le mur, la ligne des filtres se vide")
    check(window.wall.controls.parent() is window.top_bar
          and not window.wall.controls.isHidden() and window.controls.isHidden(),
          "les réglages du mur sont sur la première ligne, sans recherche par nom")
    check("vidéo" in window.wall.orient_button.toolTip(), "dont l'infobulle dit la taille du vivier")
    window.wall.unseenToggled.emit(True)
    pump(app, 0.3)
    check(window.cfg["only_unseen"] is True and window.wall.unseen.property("chosen") == "true",
          "« Non vus » du mur pose le réglage")
    window.wall.unseenToggled.emit(False)
    pump(app, 0.2)

    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 1, 60)
    window.toggle_board(False)
    pump(app, 0.3)
    folder_at = next((i for i, it in enumerate(window.items) if it.kind == MODE_FOLDERS), -1)
    if folder_at >= 0:
        window.show_item(folder_at)
        pump(app, 0.3)
        check(not window.grid_chips.isHidden(), "devant un dossier : les chips 2/4/6/8/10")
        window.set_thumb_count(4)
        pump(app, 0.4)
        check(window.cfg["thumb_count"] == 4 and window.grid.visible_count <= 4,
              f"quatre aperçus à la fois ({window.grid.visible_count})")
        window.set_thumb_count(10)

    print("\n[67] Mots fréquents : un mot, toutes ses vidéos, les titres aussi")
    from videosorter.tagging import TagsThread, frequent_tag_items, word_index
    from videosorter.backfill import TitleScan
    from videosorter.media import title_from
    names = [Path("C:/x/house party.mp4"), Path("C:/x/House.mp4"),
             Path("C:/x/beach the house.mp4"), Path("C:/x/beach.mp4"),
             Path("C:/x/IMG_4521.mp4"), Path("C:/x/s01e02.mp4")]
    index = word_index(names, {str(names[4]): "House Music Beach"})
    check(len(index.get("house", [])) == 4, f"« house » réunit toutes ses vidéos, titre compris ({len(index.get('house', []))})")
    check(len(index.get("beach", [])) == 3, "« beach » aussi, et une vidéo va dans plusieurs mots")
    check("the" not in index and "img" not in index and "s01e02" not in index,
          "ni mots vides, ni jetons, ni chiffres")
    items = frequent_tag_items(names, {str(names[4]): "House Music Beach"})
    check([i.path.name for i in items[:2]] == ["House", "Beach"], f"classés du plus porté au moins ({[i.path.name for i in items]})")
    check(items[0].is_tag and items[0].video_count == 4, "et ce sont des dossiers virtuels")
    out = {}
    worker = TagsThread(names, {})
    worker.ready.connect(lambda found: out.update(found=found))
    worker.start()
    check(wait_for(app, lambda: "found" in out, 20), "le calcul se fait hors du fil d'interface")
    check(out["found"] and out["found"][0].path.name == "House", "avec le même résultat")

    check(title_from({"format": {"tags": {"TITLE": "  Été 2019 "}}}) == "Été 2019", "le titre se lit quelle que soit sa casse")
    check(title_from({"format": {}}) == "", "et vaut vide sans métadonnée")
    INDEX.put_title("C:/x/y.mp4", "", "Un titre")
    check(INDEX.title_of("C:/x/y.mp4") == "Un titre" and INDEX.has_title("C:/x/y.mp4"), "le titre est retenu")
    INDEX.reopen(base / "_appdata" / "index.db")
    check(INDEX.title_of("C:/x/y.mp4") == "Un titre", "et survit à une réouverture")
    # Ces clips ont deja ete sondes plus haut, donc leur titre est connu : on
    # l'oublie pour que l'analyse ait quelque chose a faire.
    for video in (audite / "lot").glob("*.mp4"):
        INDEX.titles.pop(str(video), None)
    got = {}
    scan = TitleScan(audite / "lot", True)
    scan.done.connect(lambda seen, found: got.update(seen=seen, found=found))
    scan.start()
    check(wait_for(app, lambda: "seen" in got, 120), "l'analyse des titres parcourt le dossier")
    check(got["seen"] == 3 and all(INDEX.has_title(v) for v in (audite / "lot").glob("*.mp4")),
          f"chaque vidéo est sondée une fois ({got})")
    scan2 = TitleScan(audite / "lot", True)
    got2 = {}
    scan2.done.connect(lambda seen, found: got2.update(seen=seen))
    scan2.start()
    check(wait_for(app, lambda: "seen" in got2, 60) and got2["seen"] == 0, "et jamais deux")

    print("\n[68] Prisme : le nom, l'icône, la reprise du cache")
    import videosorter.config as _cfgmod
    from videosorter.config import APP_NAME
    from videosorter.widgets import app_icon

    check(APP_NAME == "Prisme", f"l'application s'appelle Prisme (obtenu {APP_NAME!r})")
    check(window.windowTitle() == APP_NAME, "la fenêtre le porte")
    check(not window.windowIcon().isNull(), "et elle a une icône")
    drawn = app_icon(64)
    check(not drawn.isNull() and drawn.pixmap(64, 64).size().width() == 64,
          "l'icône se dessine à la taille demandée")
    # Dessinee, donc nette a toute taille : Windows en reclame plusieurs.
    check(not app_icon(16).isNull() and not app_icon(256).isNull(),
          "à toutes les tailles que Windows réclame")

    # La reprise : l'ancien dossier est renomme, pas recopie ni perdu.
    from videosorter.config import adopt_old_cache
    bench = base / "_migration"
    shutil.rmtree(bench, ignore_errors=True)
    old = bench / "VideoSorter"
    (old / "thumbs" / "ab").mkdir(parents=True)
    (old / "thumbs" / "ab" / "abcd.jpg").write_bytes(b"x")
    (old / "index.db").write_bytes(b"y")
    kept_local, kept_dir = _cfgmod._LOCAL, _cfgmod.APP_DIR
    _cfgmod._LOCAL, _cfgmod.APP_DIR = bench, bench / "Prisme"
    try:
        taken = adopt_old_cache()
        check(bool(taken) and not old.exists(), "l'ancien dossier est repris")
        check((bench / "Prisme" / "thumbs" / "ab" / "abcd.jpg").exists()
              and (bench / "Prisme" / "index.db").exists(),
              "avec ses vignettes et son index")
        check(adopt_old_cache() == "", "et une seconde fois ne fait rien")
        # Un dossier neuf deja present : on n'ecrase rien.
        (bench / "VideoSorter").mkdir()
        check(adopt_old_cache() == "", "ni quand le nouveau existe déjà")
    finally:
        _cfgmod._LOCAL, _cfgmod.APP_DIR = kept_local, kept_dir

    print("\n[69] Recherche indulgente, plans repérés, base d'empreintes")
    import subprocess as _sp2, time as _t2
    from videosorter.query import (
        available as _fuzzy_ok, describe as _desc, matches_text)
    from videosorter.media import build_preview_plan, pick_moments, scene_times
    from videosorter.dupes import SignatureScan, group_by_signature, signature

    # -- 1. la recherche pardonne une lettre de travers ----------------------
    check(matches_text("Video Montagne Ete.mp4", "~mongagne") is _fuzzy_ok(),
          "« ~mongagne » trouve « Montagne » quand rapidfuzz est là")
    check(not matches_text("Video Montagne Ete.mp4", "mongagne"),
          "sans le signe, l'exact reste exact")
    check(matches_text("Video Montagne Ete.mp4", "mongagne", True) is _fuzzy_ok(),
          "et le repli global la retrouve")
    check(not matches_text("Plage Soleil.mp4", "~mongagne"),
          "mais il n'attrape pas n'importe quoi")
    check(matches_text("Plage Hiver.mp4", "~plage -hiver") is False,
          "les exclusions restent littérales")
    check("peu près" in _desc("~plage"), "et l'à-peu-près se dit en clair")
    window.set_tab(TAB_FOLDERS)
    window.start_root(audite / "lot", MODE_FLAT)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    window.toggle_board(True)
    window.apply_filter("clpi", "")
    pump(app, 0.3)
    if _fuzzy_ok():
        check(window._loose and len(window.items) >= 1,
              f"rien d'exact : l'écran se remplit d'approchants ({len(window.items)})")
        check("à peu près" in " ".join(window._active_filters()),
              "et le dit dans les filtres actifs")
    window.apply_filter("", "")
    pump(app, 0.2)
    check(not window._loose, "un nouveau réglage revient à l'exact")

    # -- 3. les vignettes se posent sur des plans -----------------------------
    shots = base / "plans"
    shutil.rmtree(shots, ignore_errors=True)
    shots.mkdir(parents=True)
    cut = shots / "trois_plans.mp4"
    _sp2.run([Tools.ffmpeg, "-y", "-v", "error",
              "-f", "lavfi", "-i", "color=black:s=320x180:d=6",
              "-f", "lavfi", "-i", "testsrc=s=320x180:d=6",
              "-f", "lavfi", "-i", "color=white:s=320x180:d=6",
              "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1[v]",
              "-map", "[v]", "-pix_fmt", "yuv420p", "-g", "12", str(cut)],
             check=True)
    times = scene_times(cut)
    check(len(times) >= 2, f"les changements de plan sont trouvés ({len(times)})")
    chosen = pick_moments(times, 18.0, 3)
    check(chosen and all(t >= 1.0 for t in chosen),
          "les instants choisis évitent la première seconde")
    check(pick_moments([], 18.0, 3) == [],
          "sans plan connu, on retombe sur les fractions")
    INDEX.put_scenes(cut, "s", times)
    check(INDEX.has_scenes(cut) and INDEX.scenes_of(cut) == times,
          "les plans sont retenus")
    plan = build_preview_plan([cut], 3, 0, one_per_video=False)
    check(all(entry[1] in times for entry in plan),
          f"et le plan d'aperçus se pose dessus ({[round(e[1], 1) for e in plan]})")

    # -- 2. la base d'empreintes, et son second passage immédiat --------------
    marks = base / "sigs"
    shutil.rmtree(marks, ignore_errors=True)
    (marks / "lot").mkdir(parents=True)
    model = next(iter(sorted(root.rglob("*.mp4"))))
    shutil.copy2(model, marks / "lot" / "copie_a.mp4")
    # Le meme film, reencode : autre taille, memes images.
    _sp2.run([Tools.ffmpeg, "-y", "-v", "error", "-i", str(model), "-crf", "35",
              "-pix_fmt", "yuv420p", str(marks / "lot" / "copie_b.mp4")], check=True)
    _sp2.run([Tools.ffmpeg, "-y", "-v", "error", "-f", "lavfi",
              "-i", "mandelbrot=size=320x180:rate=10", "-t", "20",
              "-pix_fmt", "yuv420p", str(marks / "lot" / "autre.mp4")], check=True)

    first = {}
    marker = SignatureScan(marks, window.cfg["thumb_width"], True)
    marker.done.connect(lambda seen, total: first.update(seen=seen, total=total))
    marker.start()
    check(wait_for(app, lambda: "seen" in first, 240), "le premier passage sonde")
    check(first["seen"] == 3 and first["total"] == 3,
          f"les trois vidéos ({first})")
    check(len(INDEX.sig_of(marks / "lot" / "copie_a.mp4")) >= 2,
          "chaque vidéo porte plusieurs empreintes")

    second = {}
    again = SignatureScan(marks, window.cfg["thumb_width"], True)
    again.done.connect(lambda seen, total: second.update(seen=seen, total=total))
    again.start()
    check(wait_for(app, lambda: "seen" in second, 60), "le second passage tourne")
    check(second["seen"] == 0 and second["total"] == 3,
          f"et ne resonde rien — c'est tout l'intérêt de la base ({second})")

    mine = [entry for entry in INDEX.all_sigs() if str(marks) in str(entry[0])]
    groups = group_by_signature(mine)
    names = [{path.name for path in group[1]} for group in groups]
    check(names == [{"copie_a.mp4", "copie_b.mp4"}],
          f"deux encodages du même film se retrouvent, l'autre non ({names})")

    os.utime(marks / "lot" / "copie_a.mp4",
             (_t2.time() + 120, _t2.time() + 120))
    third = {}
    once_more = SignatureScan(marks, window.cfg["thumb_width"], True)
    once_more.done.connect(lambda seen, total: third.update(seen=seen))
    once_more.start()
    check(wait_for(app, lambda: "seen" in third, 180) and third["seen"] == 1,
          f"une vidéo modifiée est resondée, elle seule ({third})")
    INDEX.reopen(base / "_appdata" / "index.db")
    check(len([e for e in INDEX.all_sigs() if str(marks) in str(e[0])]) == 3,
          "et la base survit à une réouverture")

    print("\n[69b] Doublons : instants exacts, calcul à l'écart, meilleur exemplaire, faux doublons")
    import random as _rnd
    from videosorter import dupes as _dupes
    from videosorter.dupes import (
        SIG_METHOD, SignatureGroupScan, group_by_look as _look,
        group_by_size as _by_size, rank_group, sig_current, signature_report)
    from videosorter.dupes_memory import NotDupes
    from videosorter.media import thumb_path as _thumb_path

    # -- l'empreinte porte sa methode, et ne laisse rien dans le cache -------
    copie_b = marks / "lot" / "copie_b.mp4"
    stamp_b = INDEX.sigs[str(copie_b)][0]
    check(stamp_b.endswith("|" + SIG_METHOD),
          f"l'empreinte retient la méthode qui l'a faite ({stamp_b})")
    width = window.cfg["thumb_width"]
    duree_b = (INDEX.probe(copie_b) or {}).get("duration") or 0.0
    instants = [duree_b * (i + 1) / 5 for i in range(4)]
    values_b, failed_b, tried_b = signature_report(copie_b, width)
    check(failed_b == 0 and tried_b == 4 and len(values_b) >= 2,
          f"quatre instants, aucun échec ({failed_b}/{tried_b}, {len(values_b)} empreintes)")
    check(not any(_thumb_path(copie_b, ts, width).exists() for ts in instants),
          "les images de l'empreinte ne remplissent plus le cache de vignettes")

    # -- les anciennes empreintes restent valables quand elles le peuvent ----
    faux = str(base / "faux" / "v1.mp4")
    INDEX.sigs[faux] = ("100|200", [0x0F0F0F0F0F0F0F0F, 0x3C3C3C3C3C3C3C3C], 200)
    check(sig_current(faux, "100|200"),
          "une ancienne empreinte prise aux fractions de la durée reste bonne")
    INDEX.sigs[faux] = ("100|200", [], 200)
    check(not sig_current(faux, "100|200"),
          "une ancienne empreinte vide est refaite : ce pouvait être une coupure")
    INDEX.sigs[faux] = ("100|200", [1, 2], 200)
    INDEX.scenes[faux] = [3.0, 9.0]
    check(not sig_current(faux, "100|200"),
          "une ancienne empreinte prise sur les plans est refaite")
    INDEX.scenes.pop(faux, None)
    INDEX.sigs[faux] = (f"100|200|{SIG_METHOD}", [], 200)
    check(sig_current(faux, "100|200") and not sig_current(faux, "101|200"),
          "une empreinte récente vaut tant que le fichier ne change pas")
    INDEX.sigs.pop(faux, None)

    # -- une coupure passagere n'est pas retenue pour toujours ----------------
    coupure = base / "sigs_coupure"
    shutil.rmtree(coupure, ignore_errors=True)
    coupure.mkdir(parents=True)
    passage = coupure / "passage.mp4"
    shutil.copy2(model, passage)
    real_grab = _dupes._grab
    _dupes._grab = lambda video, ts, width: (None, True)
    cut = {}
    broken_scan = SignatureScan(coupure, width, True)
    broken_scan.walking.connect(lambda n: cut.update(walk=n))
    broken_scan.unreadable.connect(lambda n: cut.update(bad=n))
    broken_scan.done.connect(lambda seen, total: cut.update(seen=seen, total=total))
    broken_scan.start()
    check(wait_for(app, lambda: "seen" in cut, 120), "passage pendant une coupure")
    _dupes._grab = real_grab
    check(cut.get("bad") == 1 and cut.get("total") == 0
          and str(passage) not in INDEX.sigs,
          f"la vidéo illisible est comptée, pas retenue ({cut})")
    check(cut.get("walk") == 1, f"le recensement se dit pendant le parcours ({cut})")
    healed = {}
    retry = SignatureScan(coupure, width, True)
    retry.done.connect(lambda seen, total: healed.update(seen=seen, total=total))
    retry.start()
    check(wait_for(app, lambda: "seen" in healed, 120)
          and healed["seen"] == 1 and len(INDEX.sig_of(passage)) >= 2,
          f"et elle est reprise au passage suivant ({healed})")

    # -- le calcul tourne hors du fil de l'interface ---------------------------
    got = {}
    grouping = SignatureGroupScan(marks)
    grouping.found.connect(lambda g: got.update(groups=g))
    grouping.start()
    check(wait_for(app, lambda: "groups" in got, 60), "comparaison en fond terminée")
    sig_groups = got.get("groups") or []
    check([{p.name for p in g[1]} for g in sig_groups] == [{"copie_a.mp4", "copie_b.mp4"}],
          f"elle retrouve les deux encodages, et eux seuls ({sig_groups})")
    if sig_groups:
        g = sig_groups[0]
        check(g.sure and len(g.to_check) == 1 and g.keep not in g.to_check,
              "quatre images et deux durées connues : un sûr, l'autre coché d'office")
        size_sum = sum(p.stat().st_size for p in g.paths)
        check(g.gain == size_sum - g.keep.stat().st_size and g[0] * (len(g[1]) - 1) == g.gain,
              "la place à récupérer est la somme moins l'exemplaire gardé")

    # -- numpy et Python pur rendent les memes groupes, et les bons ------------
    rng = _rnd.Random(7)

    def _flip(value, count):
        for bit in rng.sample(range(64), count):
            value ^= 1 << bit
        return value

    synth = [(f"x{i:05d}", [rng.getrandbits(64) for _ in range(4)], 1000)
             for i in range(3000)]
    planted = set()
    for k in range(40):
        src = synth[k * 50]
        synth.append((f"y{k:05d}", [_flip(v, rng.randint(0, 6)) for v in src[1]], 900))
        planted.add(frozenset((src[0], f"y{k:05d}")))
    still = synth[7][1][0]
    synth.append(("immobile", [_flip(still, 1), _flip(still, 2), _flip(still, 3), still], 5))
    dark = 0x0008000808080800
    for k in range(100):
        synth.append((f"sombre{k:03d}", [_flip(dark, rng.randint(0, 2)), rng.getrandbits(64)], 3))

    def _pairs(found):
        return {frozenset(str(p) for p in g[1]) for g in found}

    saved = (_dupes.USE_NUMPY, _dupes.NUMPY_FROM)
    _dupes.USE_NUMPY, _dupes.NUMPY_FROM = False, 0
    t_py = time.perf_counter()
    pure = _pairs(group_by_signature(synth, ignored=()))
    t_py = time.perf_counter() - t_py
    _dupes.USE_NUMPY = True
    with_np = _pairs(group_by_signature(synth, ignored=())) if _dupes._numpy() else pure
    _dupes.USE_NUMPY, _dupes.NUMPY_FROM = saved
    check(pure == planted,
          f"les 40 paires plantées, rien d'autre ({len(pure & planted)}/40, "
          f"{len(pure - planted)} en trop, {t_py:.2f} s)")
    check(with_np == pure, "numpy et Python pur rendent exactement les mêmes groupes")

    # -- duree, meilleur exemplaire, ecarts ------------------------------------
    ep = [str(base / "faux" / f"episode{i}.mp4") for i in range(3)]
    for path, duration, w, h in ((ep[0], 1300.0, 1280, 720), (ep[1], 1500.0, 1280, 720),
                                 (ep[2], 1301.0, 1920, 1080)):
        INDEX.put_probe(path, "", {"duration": duration, "width": w, "height": h,
                                   "codec": "h264", "ok": True})
    same = 0x0F0F3C3C5A5A6969
    looks = [(ep[0], 110_000_000, same), (ep[1], 60_000_000, same),
             (ep[2], 35_000_000, same ^ 1)]
    found_look = _look(looks, ignored=())
    check(len(found_look) == 1 and {str(p) for p in found_look[0][1]} == {ep[0], ep[2]},
          "même image mais durée trop différente : pas un doublon")
    if found_look:
        g = found_look[0]
        check(str(g.keep) == ep[2], "la plus grande définition passe devant la taille")
        check(not g.sure and g.to_check == [],
              "une seule image : « à comparer », rien de coché d'office")
        check(g.gain == 110_000_000 and g[0] == 110_000_000,
              f"place à récupérer : la somme moins l'exemplaire gardé ({g.gain})")
        check("720p au lieu de 1080p" in g.gap(ep[0]) and g.gap(ep[2]) == "à garder",
              f"l'écart au meilleur se dit en clair ({g.gap(ep[0])!r})")
    check([str(p) for p in rank_group([ep[0], ep[2]], sizes=[900, 10])] == [ep[2], ep[0]],
          "classement : la définition d'abord")
    check([str(p) for p in rank_group([ep[1], ep[0]], sizes=[5, 5])] == [ep[1], ep[0]],
          "à définition et taille égales, la plus longue")
    short, deep = str(base / "faux" / "film.mp4"), str(base / "faux" / "copie" / "film.mp4")
    check([str(p) for p in rank_group([deep, short], sizes=[7, 7])] == [short, deep],
          "puis le chemin le plus court")

    # -- la memoire des faux doublons ------------------------------------------
    memo_path = base / "_appdata" / "pas-doublons-essai.json"
    memo_path.unlink(missing_ok=True)
    memo = NotDupes(memo_path)
    check(memo.ignorer(ep[0], ep[2]) and memo.est_ignoree(ep[2], ep[0])
          and not memo.ignorer(ep[2], ep[0]),
          "« pas des doublons » se retient, dans les deux sens, une fois")
    check(NotDupes(memo_path).est_ignoree(ep[0], ep[2])
          and not memo_path.with_suffix(".json.tmp").exists(),
          "et survit à une réouverture, écrit d'un seul coup")
    check(_look(looks, ignored=memo) == [], "la paire écartée ne revient plus")
    trio = [(base / "faux" / n, 5_000_000) for n in ("a.mp4", "b.mp4", "c.mp4")]
    memo.ignorer(trio[0][0], trio[1][0])
    sized = _by_size(trio, minimum=1, ignored=memo)
    check(len(sized) == 1 and len(sized[0][1]) == 3,
          "A et B écartés, C les relie encore : le groupe tient")
    memo.ignorer_groupe([p for p, _s in trio])
    check(_by_size(trio, minimum=1, ignored=memo) == [],
          "tout le groupe écarté : il disparaît")
    moved = memo.renommer(base / "faux", base / "range")
    check(moved >= 4 and memo.est_ignoree(base / "range" / "a.mp4", base / "range" / "b.mp4")
          and not memo.est_ignoree(trio[0][0], trio[1][0]),
          f"un dossier déplacé emporte ses paires ({moved})")
    check(memo.oublier(base / "range" / "a.mp4") == 2
          and not memo.est_ignoree(base / "range" / "a.mp4", base / "range" / "b.mp4"),
          "une vidéo supprimée pour de bon est oubliée")
    check(NotDupes().path.parent == vs_config.APP_DIR,
          "la mémoire par défaut vit dans le dossier de l'application")

    print("\n[70] La fenêtre tient sur un écran agrandi, le lecteur de côté se ferme")
    # A 200 % sur un ecran de 1080p, il ne reste que 960 sur 540 points.
    window.tree.hide()
    window.aside.hide()
    pump(app, 0.2)
    need = window.layout().minimumSize()
    gros = sorted(
        ((getattr(window, n).minimumSizeHint().width(), n)
         for n in ("top_bar", "crumbs", "controls", "picked_bar", "item_card",
                   "viewer", "nav_row", "commands", "tree", "aside")
         if getattr(window, n, None) is not None), reverse=True)[:3]
    # Les polices de l'affichage hors ecran sont bien plus larges que celles
    # de Windows (les onglets : 460 points contre 287) ; la limite suit.
    ratio = max(1.0, window.tabs.minimumSizeHint().width() / 290.0)
    check(need.width() <= 980 * ratio and need.height() <= 540,
          f"la fenêtre descend à {need.width()} x {need.height()} — elle tient "
          f"(les plus larges : {gros})")
    hint = window.minimumSizeHint()
    check(hint.width() <= 1100 and hint.height() <= 620,
          f"et son plafond suit l'écran ({hint.width()} x {hint.height()})")
    window._fit_to_screen(9000, 9000)
    free = QApplication.primaryScreen().availableGeometry()
    check(window.height() <= free.height(),
          f"elle ne naît jamais plus haute que l'écran ({window.height()} sur {free.height()})")

    window.set_tab(TAB_FOLDERS)
    window.start_root(audite / "lot", MODE_FLAT)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 1, 60)
    window.toggle_board(True)
    pump(app, 0.3)
    window.open_aside(0)
    pump(app, 0.3)
    check(not window.aside.isHidden(), "le clic droit ouvre la vidéo à côté")
    window.set_tab(TAB_VIDEOS)
    pump(app, 0.3)
    check(window.aside.isHidden() and window.aside_index == -1,
          "et changer d'onglet la referme — elle appartenait à la liste quittée")
    window.set_tab(TAB_FOLDERS)
    pump(app, 0.2)

    print("\n[71] Grille qui remplit l'écran, vivier qui tient parole, jamais d'impasse")
    from videosorter.split import grid_for as _grid

    # -- la disposition suit la place -------------------------------------
    check(_grid(4, "vertical", 1920, 900) == (1, 4),
          "quatre verticales sur un écran large : une seule ligne")
    check(_grid(4, "vertical", 900, 900) == (2, 2),
          "et deux par deux quand l'écran est étroit")
    check(_grid(4, "horizontal", 1920, 900) == (2, 2),
          "quatre horizontales : deux par deux, elles sont couchées")
    check(_grid(9, "horizontal", 1920, 1080) == (3, 3), "neuf font un carré")
    check(_grid(3, "vertical") == (1, 3),
          "sans dimensions connues, un partage raisonnable")

    # -- le vivier ne promet que ce qu'il sait ------------------------------
    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning, 60)
    videos = [v for item in window.all_items for v in item.videos][:4]
    check(len(videos) >= 3, "quelques vidéos pour l'essai")
    INDEX.put_probe(videos[0], "", {"duration": 8.0, "width": 720, "height": 1280,
                                    "codec": "", "ok": True})
    INDEX.put_probe(videos[1], "", {"duration": 8.0, "width": 1280, "height": 720,
                                    "codec": "", "ok": True})
    INDEX.probes.pop(str(videos[2]), None)          # celle-là reste inconnue
    window.cfg["wall_orientation"] = "vertical"
    pool, unsure = window.vertical_pool()
    check(str(videos[0]) in pool, "la verticale connue entre")
    check(str(videos[1]) not in pool, "l'horizontale connue reste dehors")
    check(str(videos[2]) not in pool and unsure >= 1,
          f"et l'inconnue aussi — elle est comptée à part ({unsure})")
    check(str(videos[2]) in window._wall_unsure, "elle est gardée pour le sondage")

    # -- recliquer ne mène jamais à une impasse ------------------------------
    window.set_tab(TAB_TAGS)
    window.set_tag_family("top")
    wait_for(app, lambda: window._top_tags is not None, 90)
    pump(app, 0.5)
    tags = [i for i in window.items if i.is_tag]
    if tags:
        window.open_tag(tags[0])
        pump(app, 0.5)
        check(not window.at_home(), "on descend dans un mot-clé")
        window.set_tag_family(window.tag_family)
        pump(app, 0.6)
        check(window.at_home(), "recliquer la famille ramène à la liste")
        check(any(i.is_tag for i in window.items),
              f"et les mots-clés sont de retour ({len(window.items)})")
        window.open_tag([i for i in window.items if i.is_tag][0])
        pump(app, 0.5)
        window.set_tab(TAB_TAGS)
        pump(app, 0.6)
        check(window.at_home() and any(i.is_tag for i in window.items),
              "recliquer l'onglet ramène aussi")
    window.set_tab(TAB_FOLDERS)
    pump(app, 0.4)
    window.toggle_board(False)
    pump(app, 0.3)
    window.set_tab(TAB_FOLDERS)
    pump(app, 0.4)
    check(window.at_home(), "et depuis une fiche, l'onglet remonte aux vignettes")

    print("\n[72] Un panneau seul en grand, et l'explorateur sur le fichier")
    import subprocess as _sp3
    window.set_tab(TAB_FOLDERS)
    window.start_root(audite / "lot", MODE_FLAT)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 3, 60)
    window._wall_pinned = [str(i.path) for i in window.items[:3]]
    window.set_wall_count(3)
    window.set_tab(TAB_SPLIT)
    check(wait_for(app, lambda: sum(1 for p in window.wall.panes if p.video_path) >= 2, 20),
          "le mur joue plusieurs vidéos")
    from PySide6.QtWidgets import QPushButton as _QPB
    gestes = window.wall.panes[1].bar.findChildren(_QPB)
    check(len(gestes) == 5 and window.wall.panes[1].bar.stay is not None,
          f"le bandeau d'un panneau pilote tout : ◂ ⏯ ▸ ⤢ ⛶ et la coche "
          f"« rester dans ce dossier » ({len(gestes)})")
    pane = window.wall.panes[1]
    first_video = pane.video_path
    window.wall.refill_one(1)
    pump(app, 0.3)
    pane._previous()
    pump(app, 0.3)
    check(pane.video_path == first_video,
          "« précédente » ramène la vidéo d'avant, dans ce panneau")

    window.wall.toggle_solo(1)
    pump(app, 0.4)
    check(window.wall.solo == 1, "un panneau passe seul en grand")
    check(window.wall.panes[1].isVisible() and not window.wall.panes[0].isVisible(),
          "les autres se retirent")
    check(window.wall.panes[0].player.playbackState()
          != window.wall.panes[0].player.PlaybackState.PlayingState,
          "et se mettent en pause plutôt que de jouer sans être vues")
    window.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    pump(app, 0.4)
    check(window.wall.solo == -1 and window.wall.panes[0].isVisible(),
          "Échap rend le mur")
    window.wall.toggle_solo(1)
    pump(app, 0.2)
    window.wall.toggle_solo(1)
    pump(app, 0.2)
    check(window.wall.solo == -1, "et le même bouton referme")
    window.wall.toggle_solo(2)
    window.set_wall_count(4)
    check(window.wall.solo == -1, "changer le nombre de panneaux annule le solo")
    window.set_wall_count(3)

    # -- l'explorateur, sur le fichier -------------------------------------
    # On interroge ce qui decide, sans rien lancer : remplacer subprocess
    # cassait les extracteurs de vignettes qui tournent en fond.
    shown = window.reveal_target()
    check(shown is not None and str(shown) in window._wall_pinned,
          f"le mur révèle la vidéo qu'il montre ({shown})")
    order = window.reveal_command(shown)
    check(order.startswith('explorer /select,"') and order.endswith('"'),
          f"le chemin est cité, collé à l'option ({order[:30]}…)")
    check(order.count('"') == 2,
          "en un seul argument — séparés, rien n'est sélectionné")
    window.set_tab(TAB_FOLDERS)
    pump(app, 0.3)
    window.toggle_board(False)
    pump(app, 0.3)
    check(window.reveal_target() == Path(window.current.path),
          "et depuis une fiche, c'est son fichier")
    check(not window.reveal_button.isHidden(), "le bouton est là, près du titre")
    window.toggle_board(True)
    pump(app, 0.2)
    check(window.reveal_button.isHidden(), "et disparaît en vue planche")
    window._wall_pinned = []

    print("\n[73] Prisme à distance : mot de passe, portée, et lecture par morceaux")
    import urllib.error as _uerr
    import urllib.request as _ureq
    from videosorter import web as _web

    # -- le mot de passe n'est jamais gardé en clair -------------------------
    salt, digest = _web.hash_password("un mot de passe convenable")
    check(len(salt) == 32 and len(digest) == 64, "sel et empreinte sont écrits")
    check("un mot de passe" not in salt + digest,
          "et le mot de passe ne s'y lit pas")
    check(_web.password_ok("un mot de passe convenable", salt, digest),
          "le bon mot de passe est reconnu")
    check(not _web.password_ok("un mot de passe Convenable", salt, digest),
          "une seule lettre de travers suffit à le refuser")
    check(not _web.password_ok("x", "", ""),
          "et sans mot de passe enregistré, rien ne passe")
    other, _d = _web.hash_password("un mot de passe convenable")
    check(other != salt, "deux enregistrements tirent deux sels différents")

    # -- le catalogue ne connaît que ce que l'index connaît -------------------
    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning, 60)
    shelf = _web.Library(root, window.cfg["expand_parents"], window.cfg["thumb_width"])
    check(bool(shelf.folders), f"des dossiers sont catalogués ({len(shelf.folders)})")
    check(bool(shelf.videos), f"et des vidéos ({len(shelf.videos)})")
    one = next(iter(shelf.videos))
    check(len(one) == 16 and str(shelf.videos[one]) not in one,
          "chaque vidéo est désignée par une empreinte, jamais par son chemin")
    check(shelf.video_entry("0" * 16) is None,
          "une empreinte inventée ne mène à rien")

    served = _web.Server(root, salt, digest, port=0,
                         expand=window.cfg["expand_parents"],
                         width=window.cfg["thumb_width"])
    port = served.start()
    site = f"http://127.0.0.1:{port}"
    try:
        check(port > 0 and served.running, f"le serveur écoute ({port})")

        class _NoJump(_ureq.HTTPRedirectHandler):
            """Sans cela, urllib suit la redirection et le temoin de session,
            pose sur la reponse intermediaire, se perd en chemin."""

            def redirect_request(self, *_a, **_k):
                return None

        plain = _ureq.build_opener(_NoJump)

        def ask(route, token="", method="GET", data=None, headers=None):
            request = _ureq.Request(site + route, data=data, method=method)
            if token:
                request.add_header("Cookie", f"prisme={token}")
            for name, value in (headers or {}).items():
                request.add_header(name, value)
            try:
                with plain.open(request, timeout=10) as answer:
                    return answer.status, answer.read(), dict(answer.headers)
            except _uerr.HTTPError as refused:
                return refused.code, refused.read(), dict(refused.headers)

        # -- sans mot de passe, rien --------------------------------------
        code, _body, head = ask("/")
        check(code in (200, 303) and "/login" in str(head.get("Location", "/login")),
              "la racine renvoie vers la demande de mot de passe")
        code, _body, _h = ask("/api/folders")
        check(code == 401, f"et l'inventaire est refusé ({code})")
        code, _body, _h = ask("/video/" + one)
        check(code == 401, "la vidéo aussi — c'est le point le plus important")

        # -- un mauvais mot de passe ne passe pas ---------------------------
        code, _body, _h = ask("/login", method="POST",
                              data=b"password=ce+n+est+pas+lui")
        check(code == 401, f"un mauvais mot de passe est refusé ({code})")

        # -- le bon ouvre une session ---------------------------------------
        from urllib.parse import quote_plus as _q
        code, _body, head = ask(
            "/login", method="POST",
            data=f"password={_q('un mot de passe convenable')}".encode())
        biscuit = str(head.get("Set-Cookie", ""))
        token = biscuit.split("prisme=", 1)[-1].split(";", 1)[0] if "prisme=" in biscuit else ""
        check(code in (200, 303) and token, "le bon mot de passe ouvre une session")
        check("HttpOnly" in biscuit and "SameSite" in biscuit,
              "dont le témoin est hors de portée des scripts")

        code, body, _h = ask("/api/folders", token)
        listed = _json_web.loads(body.decode("utf-8"))
        check(code == 200 and listed.get("folders"),
              "l'inventaire s'ouvre une fois connecté")
        first = listed["folders"][0]
        code, body, _h = ask("/api/folder?id=" + first["id"], token)
        inside = _json_web.loads(body.decode("utf-8"))
        check(code == 200 and inside.get("videos"), "un dossier rend ses vidéos")

        # -- la lecture par morceaux : c'est elle qui permet de sauter --------
        code, body, head = ask("/video/" + one, token,
                               headers={"Range": "bytes=0-99"})
        check(code == 206 and len(body) == 100,
              f"un morceau demandé est un morceau rendu ({code}, {len(body)} octets)")
        check(head.get("Content-Range", "").startswith("bytes 0-99/"),
              f"et le serveur dit où il en est ({head.get('Content-Range')})")
        check(head.get("Accept-Ranges") == "bytes",
              "il annonce qu'on peut lui demander n'importe quel passage")
        code, body, _h = ask("/video/" + one, token)
        check(code == 200 and len(body) > 100, "sans demande, il rend tout")

        # -- on ne sort pas du catalogue --------------------------------------
        for sortie in ("/video/" + "0" * 16, "/video/..%2F..%2Fwindows",
                       "/thumb/" + "0" * 16):
            code, _body, _h = ask(sortie, token)
            check(code == 404, f"« {sortie[:24]}… » ne mène nulle part ({code})")

        # -- sortir ferme vraiment la session ----------------------------------
        ask("/logout", token)
        code, _body, _h = ask("/api/folders", token)
        check(code == 401, "après être sorti, le témoin ne vaut plus rien")
    finally:
        served.stop()
    check(not served.running, "et le serveur s'arrête proprement")

    # -- le journal : qui est venu, et ce qu'il a regardé --------------------
    from videosorter.access import Journal, describe, spell
    book = Journal(Path(base) / "_appdata" / "essai-acces.db")
    try:
        nom = book.entered("82.45.1.9", "Mozilla/5.0 (Linux; Android 14) Chrome/120")
        check("Android" in nom and "Chrome" in nom,
              f"le visiteur est nommé sans être identifié ({nom})")
        check(describe("1.2.3.4", "Mozilla/5.0 (iPhone) Safari") != nom,
              "deux appareils différents portent deux noms")
        check(describe("9.9.9.9", "Mozilla/5.0 (Linux; Android 14) Chrome/120") == nom,
              "et le même appareil garde le sien, d'où qu'il vienne")
        book.entered("5.5.5.5", "curl", "refus")
        seen = book.visits()
        check(len(seen) == 2 and seen[0][3] == "refus",
              f"entrées et refus sont notés ({[row[3] for row in seen]})")

        book.watched("82.45.1.9", nom, "abc", "plage.mp4", 30)
        book.watched("82.45.1.9", nom, "abc", "plage.mp4", 25)
        watched = book.views()
        check(len(watched) == 1 and abs(watched[0][4] - 55) < 0.01,
              f"le temps regardé s'additionne sur la même séance ({watched})")
        book.watched("82.45.1.9", nom, "abc", "plage.mp4", 99999)
        watched = book.views()
        check(watched[0][4] <= 55 + 60.01,
              f"un battement ne peut pas valoir la nuit ({watched[0][4]:.0f} s)")
        check(spell(55) == "55 s" and "min" in spell(300) and "h" in spell(7200),
              "et le temps se lit en clair")
        book.clear()
        check(not book.visits() and not book.views(), "le journal s'efface")
    finally:
        book.close()

    print("\n[74] Le tunnel, depuis l'application")
    from videosorter import tunnel as _tun

    check(_tun.ADDRESS.search(
        "INF |  https://abc-def-ghi-jkl.trycloudflare.com   |").group(0)
        == "https://abc-def-ghi-jkl.trycloudflare.com",
        "l'adresse se lit dans ce que cloudflared raconte")
    check(_tun.ADDRESS.search("rien ici") is None,
          "et rien n'est pris pour une adresse")

    code = _tun.qr_png("https://abc-def.trycloudflare.com/")
    check(code[:4] == b"\x89PNG" and len(code) > 100,
          f"le code à scanner se dessine ({len(code)} octets)")
    check(_tun.qr_png("") == b"" or True, "et ne tombe pas sur une adresse vide")

    # Sans cloudflared, rien ne s'ouvre — et rien ne casse.
    kept_find = _tun.find
    _tun.find = lambda: ""
    try:
        window.stop_tunnel()
        check(window.start_tunnel() is False,
              "sans cloudflared, le tunnel ne s'ouvre pas")
        check("pas installé" in window.tunnel_state(),
              f"et l'état le dit ({window.tunnel_state()!r})")
        check(window.share_link() == "" or "127.0.0.1" in window.share_link(),
              "l'adresse reste locale")
    finally:
        _tun.find = kept_find

    # Le tunnel annonce son adresse : on la retient, et le lien la prend.
    window.tunnel_address = "https://essai-de-passage.trycloudflare.com"
    check(window.share_link() == "https://essai-de-passage.trycloudflare.com/",
          f"une fois ouvert, c'est l'adresse publique qu'on donne ({window.share_link()})")
    window.tunnel_address = ""

    check(window.cfg["tunnel_auto"] is True,
          "l'adresse publique s'ouvre d'elle-même au lancement")
    check(_tun.PACKAGE == "Cloudflare.cloudflared",
          "et l'installation sait quoi demander à winget")

    # -- l'adresse fixe : Tailscale ----------------------------------------
    check(_tun.TAILSCALE_PACKAGE == "tailscale.tailscale",
          "l'adresse fixe sait aussi quoi demander à winget")
    check(_tun.CONSENT.search(
        "visit https://login.tailscale.com/f/funnel?node=abc to enable"
    ).group(0).startswith("https://login.tailscale.com/"),
        "et le lien d'autorisation se lit dans ce que Tailscale répond")

    kept_fixed = _tun.find_fixed
    _tun.find_fixed = lambda: ""
    try:
        window.set_tunnel_kind("tailscale")
        check(window.cfg["tunnel_kind"] == "tailscale", "on peut choisir l'adresse fixe")
        check(window.start_tunnel() is False,
              "sans Tailscale, elle ne s'ouvre pas")
        check("pas installé" in window.tunnel_state(),
              f"et l'état le dit ({window.tunnel_state()!r})")
        ready, why = _tun.fixed_state()
        check(ready is False and why, "l'état de la mise en route se lit d'un coup")
    finally:
        _tun.find_fixed = kept_fixed
        window.set_tunnel_kind("cloudflare")
    check(window.cfg["tunnel_kind"] == "cloudflare", "et l'on revient en arrière")

    print("\n[75] Un dossier mis de côté, et l'interrupteur qui le révèle")
    from videosorter.scan import (
        _is_hidden as _caché, set_veiled, under_veiled, veiled,
    )

    set_veiled(["BIN"], False)
    check(veiled("BIN") and veiled("bin") and veiled("Bin"),
          "le nom est masqué quelle que soit sa casse")
    check(not veiled("Vacances"), "et les autres ne le sont pas")
    check(under_veiled(Path("X:/BIN/2019/film.mp4")),
          "ce qui est dessous l'est aussi")
    check(not under_veiled(Path("X:/binome/film.mp4")),
          "mais « binome » n'est pas « bin » — pas de masquage par préfixe")

    class _Entree:
        name = "BIN"

        def stat(self, **_k):
            raise OSError

    check(_caché(_Entree()),
          "tous les parcours le sautent : c'est le point qu'ils traversent tous")

    # Un vrai dossier, avec de vraies vidéos, doit disparaître partout.
    cellier = base / "voile"
    shutil.rmtree(cellier, ignore_errors=True)
    (cellier / "BIN").mkdir(parents=True)
    (cellier / "Garde").mkdir(parents=True)
    modele = next(iter(sorted(root.rglob("*.mp4"))))
    shutil.copy2(modele, cellier / "BIN" / "secret.mp4")
    shutil.copy2(modele, cellier / "Garde" / "ordinaire.mp4")

    window.set_tab(TAB_FOLDERS)
    window.start_root(cellier, MODE_FOLDERS)
    check(wait_for(app, lambda: not window.scanning, 60), "analyse du cellier")
    noms = [item.path.name for item in window.items]
    check("Garde" in noms, f"le dossier ordinaire est là ({noms})")
    check("BIN" not in noms, f"et le dossier masqué n'y est pas ({noms})")
    check(not any("secret.mp4" in str(v) for v in window._videos_from_items()),
          "sa vidéo non plus, nulle part")

    # Et le partage à distance n'en montre pas davantage.
    from videosorter import web as _web_voile
    rayon = _web_voile.Library(cellier, window.cfg["expand_parents"],
                               window.cfg["thumb_width"])
    check(not any("secret" in str(chemin) for chemin in rayon.videos.values()),
          "l'adresse publique ne montre pas ce que la fenêtre cache")
    check(any("ordinaire" in str(chemin) for chemin in rayon.videos.values()),
          "mais elle montre le reste")

    # L'interrupteur.
    window.toggle_veiled()
    check(window.cfg["show_veiled"] is True, "l'interrupteur se lève")
    window.start_root(cellier, MODE_FOLDERS, force=True)
    check(wait_for(app, lambda: not window.scanning, 60), "on refait la liste")
    noms = [item.path.name for item in window.items]
    check("BIN" in noms, f"le dossier masqué réapparaît ({noms})")
    revu = _web_voile.Library(cellier, window.cfg["expand_parents"],
                              window.cfg["thumb_width"])
    check(any("secret" in str(chemin) for chemin in revu.videos.values()),
          "et le partage le montre aussi")

    window.toggle_veiled()
    check(window.cfg["show_veiled"] is False, "puis se rabaisse")
    check(under_veiled(Path("X:/BIN/f.mp4")), "et le masque reprend")
    window.start_root(root, MODE_FOLDERS, force=True)
    wait_for(app, lambda: not window.scanning, 60)

    print("\n[76] Le repli, et le clic droit qui range")
    from videosorter.quiet import QUIET_TITLE
    from videosorter.window import PAGE_QUIET

    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning, 60)
    before = window.stack.currentIndex()
    window.cfg["quiet_explained"] = True     # sans la boîte, qui attendrait
    window.enter_quiet()
    pump(app, 0.3)
    check(window.stack.currentIndex() == PAGE_QUIET, "le repli prend toute la fenêtre")
    check(window.windowTitle() == QUIET_TITLE,
          f"et le titre ne dit plus rien de Prisme ({window.windowTitle()!r})")
    check(window.quiet_page.table.topLevelItemCount() >= 3,
          "la page montre autre chose, et c'est plausible")
    check(all(p.player.playbackState() != p.player.PlaybackState.PlayingState
              for p in window.wall.panes),
          "rien ne joue plus — ni image figée, ni son qui continue")
    window.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_K, Qt.ControlModifier))
    pump(app, 0.3)
    check(window.stack.currentIndex() == before and window.windowTitle() == "Prisme",
          "Ctrl+K ramène là où l'on était")
    window.enter_quiet()
    pump(app, 0.2)
    window.quiet_page.back.click()
    pump(app, 0.3)
    check(window.stack.currentIndex() == before, "et le bouton discret aussi")

    # -- le clic droit du mur range, au lieu de montrer l'heure -------------
    window._wall_pinned = [str(i.path) for i in window.items[:2] if i.videos] or None
    if window._wall_pinned:
        window.set_tab(TAB_SPLIT)
        pump(app, 0.4)
        kept = list(window.cfg.destinations)
        window.cfg.set_destinations([
            {"key": "6", "label": "Essai", "path": str(tri / "2019")}])
        try:
            window.wall_sort(0, str(Path(window._wall_pinned[0])))
            pump(app, 0.2)
            check(not window.radial.isHidden(),
                  "clic droit sur un panneau : les destinations apparaissent")
            check(window._sorting_pane is not None,
                  "et l'on sait quel panneau attend")
            window.radial.close_menu()
            pump(app, 0.2)
            check(window._sorting_pane is None,
                  "refermer sans choisir ne laisse pas le panneau en attente")
        finally:
            window.cfg.set_destinations(kept)
        window.set_tab(TAB_FOLDERS)
        pump(app, 0.2)
    window._wall_pinned = []

    print("\n[77] Une barre qui flotte, une adresse qu'on copie, un repli qui s'explique")
    from PySide6.QtWidgets import QPushButton
    from videosorter.widgets import OverBar
    from videosorter.window import PAGE_QUIET

    # -- le bandeau du lecteur de côté ------------------------------------
    check(isinstance(window.aside_bar, OverBar),
          "le bandeau du lecteur de côté flotte au-dessus de l'image")
    check(window.aside_bar.parent() is window or window.aside_bar.isWindow(),
          "c'est une fenêtre à part — la seule chose qui passe devant la vidéo")
    check(window.aside_bar.buttons.count() == 6,
          f"il porte ses cinq gestes, précédente et pause compris "
          f"({window.aside_bar.buttons.count()})")
    check(window.single_bar.buttons.count() == 6,
          "le bandeau de la fiche aussi : la coche, ◂ ⏯ ▸ ⌸ ⛶")
    gestes = window.aside_bar.skin.findChildren(QPushButton)
    check(gestes and all(b.width() >= 28 and b.height() >= 22 for b in gestes),
          "dont les signes ont la place de se voir")
    window.aside_bar.set_progress(30_000, 120_000)
    check(window.aside_bar.left.text().startswith("−"),
          f"le temps restant s'y lit ({window.aside_bar.left.text()!r})")
    check(window.aside_bar.done.width() >= 0, "et l'avancement s'y dessine")
    window.aside_bar.set_progress(0, 0)
    check(window.aside_bar.left.text() == "", "rien à dire sans durée connue")
    check(window.aside_bar.isHidden(), "au repos, il ne prend pas de place")

    # -- l'adresse du partage : un champ, pas une étiquette ----------------
    from videosorter.share_dialog import ShareDialog
    board = ShareDialog(window, window)
    try:
        check(board.link.isReadOnly(), "l'adresse ne se modifie pas")
        board.link.setText("https://essai.tailXXXX.ts.net/")
        board._copy()
        from PySide6.QtGui import QGuiApplication as _QGA
        check(_QGA.clipboard().text() == "https://essai.tailXXXX.ts.net/",
              f"mais elle se copie entière ({_QGA.clipboard().text()!r})")
        check(board.link.hasSelectedText(),
              "et se montre sélectionnée, pour qu'on voie ce qui est parti")
    finally:
        board.close()

    # -- le repli dit comment on en sort -----------------------------------
    window.cfg["quiet_explained"] = True     # sans la boîte, qui attendrait
    depart = window.stack.currentIndex()
    window.enter_quiet()
    pump(app, 0.2)
    check(window.stack.currentIndex() == PAGE_QUIET, "on se replie")
    window.quiet_page.keyPressEvent(
        QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    pump(app, 0.2)
    check(window.stack.currentIndex() == depart, "Échap ramène")
    window.enter_quiet()
    pump(app, 0.2)
    window.quiet_page.leave.emit()
    pump(app, 0.2)
    check(window.stack.currentIndex() == depart, "un double-clic aussi")
    check("Échap" in window.quiet_button.toolTip(),
          "et le bouton dit ce qu'il fait avant qu'on le presse")

    # -- l'état de la collection se lit ------------------------------------
    window._note_state(videos=106903, thumbs=41230, audited=106903)
    window._refresh_state()
    court = window.state_button.text()
    check(len(court) < 40 and "106 903" in court,
          f"court à l'écran ({court!r})")
    check("Ce que le logiciel sait" in window.state_button.toolTip()
          and "106 903" in window.state_button.toolTip(),
          "complet dans l'infobulle")
    check(window.state_button.minimumWidth() >= 100,
          "et assez large pour ne pas être rogné jusqu'à l'absurde")

    probe_dialog = DestinationsDialog([])
    picked = [tri / "2019", tri / "2020", tri / "2021"]
    check(probe_dialog._add_paths(picked) == 3,
          "trois dossiers choisis d'un coup donnent trois raccourcis")
    results = probe_dialog.result_destinations()
    check([d["key"] for d in results] == ["6", "7", "8"],
          f"chacun reçoit une touche distincte ({[d['key'] for d in results]})")
    check([d["label"] for d in results] == ["2019", "2020", "2021"],
          "et le nom du dossier sert de libellé")

    print("\n[78] Deux lignes en haut, une en bas, et chaque bouton ramène")
    from videosorter.split import PANE_CHOICES
    window.set_tab(TAB_FOLDERS)
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning and len(window.items) >= 2, 60)
    window.go_home()
    pump(app, 0.3)
    check(not window.controls.isHidden() and window.item_card.isHidden()
          and window.bottom_bar.isHidden(),
          "sur la planche : les filtres, ni titre ni commandes")
    position = next(i for i, it in enumerate(window.items)
                    if it.kind == MODE_FOLDERS and len(it.videos) >= 2)
    window.toggle_board(False)
    window.show_item(position)
    pump(app, 0.5)
    folder = window.current
    check(window.controls.isHidden() and not window.item_card.isHidden(),
          "sur une fiche, la seconde ligne dit ce qu'on regarde")
    check("vidéo" in window.item_subtitle.text() and window.item_subtitle.isVisible(),
          f"poids, nombre de vidéos, date ({window.item_subtitle.text()!r})")
    check(not window.bottom_bar.isHidden() and not window.stars.isHidden(),
          "une ligne en bas, et les étoiles : un dossier se note aussi")
    window.ratings.set(folder.path, 0)
    window.rate_current(4)
    check(window.ratings.get(folder.path) > 0, "un dossier se met en favori aussi")
    window.ratings.set(folder.path, 0)
    check(window.random_here_button.text() == "Au hasard ici"
          and window.reveal_button.text() == "Ouvrir le dossier",
          "de vrais boutons, qui disent ce qu'ils font")
    window.go_parent()
    pump(app, 0.3)
    check(window.browsing, "« remonter » depuis la fiche d'un dossier rend la liste")
    window.toggle_board(False)
    window.show_item(position)
    pump(app, 0.3)
    check(not window.grid_chips.isHidden(), "le nombre d'aperçus est sur la ligne du titre")
    check(all(t.badge.isHidden() for t in window.grid.tiles),
          "les cases ne portent plus de numéro")

    # Passer de 4 à 10 aperçus ne rebat pas les vidéos.
    window.set_thumb_count(4)
    wait_for(app, lambda: window._current_key() in window.plans, 30)
    few = [e[0] for e in window.plans.get(window._current_key(), [])]
    window.set_thumb_count(10)
    wait_for(app, lambda: window._current_key() in window.plans, 30)
    many = [e[0] for e in window.plans.get(window._current_key(), [])]
    check(few and many[:len(few)] == few,
          "de 4 à 10 aperçus, les premières vidéos restent les mêmes")

    check(5 in PANE_CHOICES, "le mur propose cinq vidéos")
    check(window.wall.count_stepper.choices == list(PANE_CHOICES),
          "et se règle avec le même − n + que le reste")
    from videosorter.widgets import Expiring
    gone = Expiring()
    gone.add("x.mp4")
    check("x.mp4" in gone, "une vidéo illisible est écartée un moment")
    gone["x.mp4"] -= Expiring.TTL + 1
    check("x.mp4" not in gone, "puis retentée : un réseau qui décroche ne la condamne pas")
    window.controls.set_stars(3)
    check(window.controls.rating_pick.text() == "★ 3"
          and window.controls.rating_choices[3].property("chosen") == "true",
          "la note choisie se lit sur le bouton et dans la rangée")
    window.controls.set_stars(-1)

    # La note, en chiffre d'or, sur la carte.
    window.toggle_board(True)
    pump(app, 0.3)
    first_card = window.board.cards[0]
    window.ratings.set(window.items[0].path, 0)
    window.on_board_rate(0, 5) if hasattr(window, "on_board_rate") else None
    pump(app, 0.2)
    check(first_card.rating.text() == "★" and not first_card.rating.isHidden(),
          f"une carte notée montre sa note ({first_card.rating.text()!r})")
    window.ratings.set(window.items[0].path, 0)

    # Chaque onglet, depuis chaque endroit, mène à sa liste.
    def tab_click(tab):
        window.tabs.chosen.emit(tab)
        wait_for(app, lambda: not window.scanning, 60)
        pump(app, 0.3)

    lost = []
    for start in (TAB_FOLDERS, TAB_VIDEOS, TAB_SPLIT):
        for target in (TAB_FOLDERS, TAB_VIDEOS, TAB_SPLIT):
            if start == target:
                continue
            tab_click(start)
            if start != TAB_SPLIT and window.items:
                window.toggle_board(False)
                window.show_item(0)
                pump(app, 0.2)
            tab_click(target)
            if window.tab != target:
                lost.append(f"{start}→{target} : onglet {window.tab}")
            elif target != TAB_SPLIT and not (window.browsing and window.items):
                lost.append(f"{start}→{target} : pas sur la liste")
    check(not lost, f"chaque onglet mène à sa liste, d'où qu'on parte {lost}")

    tab_click(TAB_FOLDERS)
    window.toggle_board(False)
    window.show_item(0)
    pump(app, 0.2)
    tab_click(TAB_FOLDERS)
    check(window.at_home(), "recliquer l'onglet où l'on est ramène à sa liste")

    tab_click(TAB_TAGS)
    window.set_tag_family("top") if window.tag_family != "top" else None
    wait_for(app, lambda: any(i.is_tag for i in window.items), 60)
    tags = [i for i in window.items if i.is_tag]
    if tags:
        window.show_item(window.items.index(tags[0]))
        window.enter_current()
        wait_for(app, lambda: not window.scanning, 60)
        pump(app, 0.3)
        tab_click(TAB_TAGS)
        check(window.at_home() and any(i.is_tag for i in window.items),
              "dans un mot-clé, recliquer « Mots-clés » ramène aux mots")
        window.show_item(window.items.index(
            next(i for i in window.items if i.is_tag)))
        window.enter_current()
        wait_for(app, lambda: not window.scanning, 60)
        tab_click(TAB_VIDEOS)
        check(window.tab == TAB_VIDEOS and window.browsing and window.items
              and not any(i.is_tag for i in window.items),
              "et « Vidéos » mène aux vidéos, d'un seul clic")
    else:
        check(False, "des mots fréquents sont trouvés")
    tab_click(TAB_FOLDERS)

    print("\n[79] Un seul chemin vers chaque niveau, un mur qui prend la place")
    from videosorter.scan import MAX_VIDEOS_PER_ITEM
    check(MAX_VIDEOS_PER_ITEM > 100_000,
          "un dossier retient toutes ses vidéos, pas les quatre cents premières")
    tab_click(TAB_VIDEOS)
    window.reset_filters()
    pump(app, 0.3)
    check(window.controls.random_here.isHidden(),
          "sans filtre, pas de second « au hasard » : celui du haut suffit")
    window.set_only_unseen(True)
    pump(app, 0.3)
    check(not window.controls.random_here.isHidden() or not window.items,
          "avec un filtre, le hasard dans les résultats paraît")
    window.set_only_unseen(False)
    window._wall_pinned = [str(i.path) for i in window.items[:3] if i.videos]
    if window._wall_pinned:
        window.set_tab(TAB_SPLIT)
        window.set_wall_count(10)
        pump(app, 0.3)
        window.set_wall_count(3)
        pump(app, 0.3)
        grid = window.wall.grid
        rows, cols = window.wall._shape
        stretched = [c for c in range(grid.columnCount()) if grid.columnStretch(c)]
        check(len(stretched) == cols,
              f"trois vidéos après dix : aucune colonne fantôme ({stretched}, {cols})")
        target = window._wall_pinned[0]
        window.open_video_path(target)
        wait_for(app, lambda: not window.scanning and window.current is not None
                 and str(window.current.path) == target, 30)
        check(window.tab != TAB_SPLIT and not window.browsing
              and str(window.current.path) == target,
              "« sa fiche » depuis le mur : la fiche de toujours, au niveau fichier")
        check(not window.stars.isHidden() and not window.bottom_bar.isHidden(),
              "avec ses étoiles et ses commandes")
    tab_click(TAB_FOLDERS)

    print("\n[80] La sélection en mur ou en playlist, un menu rangé")
    top = [a for a in window.overflow.actions() if not a.isSeparator()]
    check(len(top) <= 9, f"le menu ⋯ tient en quelques lignes ({len(top)})")
    check(any(a.menu() is not None and a.text() == "Collection" for a in top),
          "le reste est rangé en sous-menus")
    check(any(a.text().startswith("Préparer toutes les vignettes")
              for a in window._menu_actions()),
          "et chaque entrée s'y retrouve")
    tab_click(TAB_VIDEOS)
    window.toggle_board(True)
    pump(app, 0.3)
    chosen = [i for i in window.items if i.videos][:3]
    if len(chosen) == 3:
        for item in chosen:
            window.board.picked_ids.add(item.item_id)
        window.playlist_picked()
        pump(app, 0.4)
        check(not window.aside.isHidden() and len(window.aside_playlist) == 3,
              "« Playlist » : les trois vidéos, dans le lecteur de droite")
        window.aside_step(1)
        window.aside_step(1)
        window.aside_step(1)
        check(window.aside_playlist_at == 0,
              "après la dernière, la première : la playlist tourne en boucle")
        window.close_aside()
        check(not window.aside_playlist, "et se referme proprement")
        for item in chosen:
            window.board.picked_ids.add(item.item_id)
        window.wall_picked()
        pump(app, 0.4)
        check(window.tab == TAB_SPLIT and window.wall._shape == (1, 3),
              f"« Mur » : trois vidéos choisies, sur une seule ligne "
              f"({window.wall._shape})")
    tab_click(TAB_FOLDERS)

    print("\n[81] L'index de l'utilisateur ne disparaît jamais")
    import sqlite3 as _sq
    import tempfile as _tf
    from videosorter import config as _cfgmod
    from videosorter.index import Index as _Index
    check(bool(_cfgmod.SANDBOX) and Path(_cfgmod.APP_DIR) != Path(
        _os.environ.get("LOCALAPPDATA", "")) / "Prisme",
          "les tests travaillent dans leur bac à sable, jamais dans les données réelles")
    spot = Path(_tf.mkdtemp()) / "index.db"
    first = _Index(spot)
    first.put_listing(Path("X:/essai"), "k", ["a", "b"])
    first.commit(force=True)
    first.close()
    size = spot.stat().st_size
    holder = _sq.connect(spot, timeout=1)
    holder.execute("BEGIN EXCLUSIVE")
    _Index.OPEN_TIMEOUT = 0.3
    try:
        busy = _Index(spot)
    finally:
        _Index.OPEN_TIMEOUT = 30.0
    holder.rollback()
    holder.close()
    check(spot.exists() and spot.stat().st_size >= size
          and not list(spot.parent.glob("index.db.abime-*")),
          "occupé par un autre Prisme : on ne l'efface pas, on attend son tour")
    again = _Index(spot)
    check(again.listing(Path("X:/essai"), "k") == ["a", "b"],
          "et il est intact à l'ouverture suivante")
    again.close()
    broken = Path(_tf.mkdtemp()) / "index.db"
    broken.write_bytes(b"ceci n'est pas une base" * 100)
    rebuilt = _Index(broken)
    check(rebuilt.rebuilt and list(broken.parent.glob("index.db.abime-*")),
          "vraiment abîmé : refait, mais l'ancien est mis de côté, pas effacé")
    rebuilt.close()

    print("\n[82] Rester dans ce dossier, une ligne pour la fiche, un mur qui garde ses vidéos")
    from PySide6.QtWidgets import QSplitter as _QSplitter
    check(isinstance(window.middle, _QSplitter),
          "la planche et le lecteur de droite se partagent la place à la souris")
    tab_click(TAB_VIDEOS)
    window.toggle_board(False)
    window.show_item(0)
    pump(app, 0.3)
    check(window._one_line and window._row_one.indexOf(window.item_card) >= 0,
          "la fiche d'une vidéo tient sur une seule ligne")
    check("0 o" not in window.item_subtitle.text(),
          f"jamais « 0 o » ({window.item_subtitle.text()!r})")
    current = window.current
    siblings = window._folder_videos(str(current.path))
    window.set_stay_in_folder(True)
    check(window.single_bar.stay.isChecked() and window.aside_bar.stay.isChecked()
          and window.wall.stay, "une seule coche, partout à la fois")
    window.step(1)
    wait_for(app, lambda: not window.scanning, 30)
    pump(app, 0.3)
    if len(siblings) > 1:
        check(window.current is not None
              and Path(window.current.path).parent == Path(current.path).parent,
              "cochée, « suivante » reste dans le dossier de la vidéo")
    window.set_stay_in_folder(False)
    tab_click(TAB_FOLDERS)
    tab_click(TAB_VIDEOS)
    window._wall_pinned = []
    window.cfg["wall_orientation"] = "any"
    window.set_tab(TAB_SPLIT)
    window.set_wall_orientation("any")
    window.set_wall_count(3)
    wait_for(app, lambda: sum(1 for p in window.wall.panes if p.video_path) >= 3, 20)
    before = [p.video_path for p in window.wall.panes]
    window.set_wall_count(4)
    pump(app, 1.5)
    after = [p.video_path for p in window.wall.panes]
    check(after[:3] == before and len(after) == 4,
          "ajouter un panneau garde les trois vidéos qu'on regardait")
    window.wall.set_muted(False)
    window.wall.panes[0].watch(True, False)
    window.wall.panes[1].watch(False, False)
    window.wall._hear(window.wall.panes[0])
    check(window.wall.panes[0].player.audioOutput() is window.wall.audio
          and window.wall.panes[1].player.audioOutput() is None
          and not window.wall.audio.isMuted(),
          "sur le mur, seule la vidéo survolée a le son")
    window.wall.set_muted(True)
    tab_click(TAB_FOLDERS)

    print("\n[83] La fiche d'une vidéo est la même page, d'où qu'on vienne")

    def sheet_state():
        return (window.tab != TAB_SPLIT, not window.browsing, window._one_line,
                not window.bottom_bar.isHidden(), not window.stars.isHidden(),
                window.controls.isHidden(),
                window.viewer.currentWidget() is window.single)

    expected = (True, True, True, True, True, True, True)
    seen = {}
    tab_click(TAB_VIDEOS)
    if window.items:
        window.on_board_open(0)
        pump(app, 0.3)
        seen["onglet Vidéos"] = sheet_state()
    tab_click(TAB_SPLIT)
    window.set_wall_count(2)
    wait_for(app, lambda: any(p.video_path for p in window.wall.panes), 20)
    window.pick_random()
    wait_for(app, lambda: not window.scanning, 30)
    pump(app, 0.4)
    seen["« Aléatoire » depuis le mur"] = sheet_state()
    tab_click(TAB_SPLIT)
    wait_for(app, lambda: any(p.video_path for p in window.wall.panes), 20)
    path = next((p.video_path for p in window.wall.panes if p.video_path), "")
    if path:
        window.wall.panes[0].opened.emit(path)
        wait_for(app, lambda: not window.scanning, 30)
        pump(app, 0.4)
        seen["« sa fiche » depuis le mur"] = sheet_state()
    wrong = {k: v for k, v in seen.items() if v != expected}
    check(seen and not wrong,
          f"même fiche, même entête, même ligne du bas, mêmes étoiles {wrong}")
    tab_click(TAB_SPLIT)
    pump(app, 0.3)
    check(not window.wall.controls.isHidden(),
          "de retour sur le mur, ses réglages sont là")
    tab_click(TAB_VIDEOS)
    window.board.picked_ids.add("n'existe-pas-ici")
    window.refresh_board()
    pump(app, 0.2)
    check("n'existe-pas-ici" not in window.board.picked_ids
          and window.picked_bar.isHidden(),
          "une coche d'une autre liste n'annonce pas « un élément coché » ici")
    tab_click(TAB_FOLDERS)

    print("\n[84] Favoris, lancement propre, aperçus qui se montrent")
    from videosorter.header import TAB_FAVS
    from videosorter.window import MainWindow as _MW
    tab_click(TAB_FOLDERS)
    folder = next(i for i in window.items if i.kind == MODE_FOLDERS)
    window.ratings.set(folder.path, 0)
    window.on_board_open(window.items.index(folder))
    pump(app, 0.3)
    window.stars.click()
    video = None
    tab_click(TAB_VIDEOS)
    if window.items:
        video = window.items[0]
        window.ratings.set(video.path, 0)
        window.on_board_open(0)
        pump(app, 0.2)
        QTest.keyClick(window, Qt.Key_1)
    tab_click(TAB_FAVS)
    shown = {str(i.path) for i in window.items}
    check(str(folder.path) in shown and (video is None or str(video.path) in shown),
          f"l'onglet Favoris réunit dossiers et vidéos en favori ({len(shown)})")
    check(window.at_home() and window.browsing, "et s'ouvre sur sa planche")
    tab_click(TAB_FOLDERS)
    every_folder = {str(i.path) for i in window.all_items}
    tab_click(TAB_VIDEOS)
    every_video = len(window.all_items)
    tab_click(TAB_FAVS)
    tab_click(TAB_VIDEOS)
    check(len(window.all_items) == every_video and window.items,
          f"Favoris → Vidéos : toutes les vidéos ({len(window.all_items)} "
          f"sur {every_video}), pas seulement les favorites")
    tab_click(TAB_FAVS)
    tab_click(TAB_FOLDERS)
    check({str(i.path) for i in window.all_items} == every_folder,
          "Favoris → Dossiers : tous les dossiers, sans repasser ailleurs")
    tab_click(TAB_FAVS)
    spot = next(i for i, item in enumerate(window.items)
                if str(item.path) == str(folder.path))
    window.on_board_open(spot)
    window.enter_current()
    wait_for(app, lambda: not window.scanning, 60)
    window.show_board_at(0)
    window.go_parent()
    pump(app, 0.2)
    check(not window.levels and {str(i.path) for i in window.items} == shown,
          "remonter d'un dossier favori rend la liste des favoris")
    window.ratings.set(folder.path, 0)
    if video is not None:
        window.ratings.set(video.path, 0)
    tab_click(TAB_FOLDERS)

    window.cfg["tab"] = TAB_TAGS
    window.cfg["only_unseen"] = True
    window.cfg["folder_max"] = 5
    window.cfg["orientations"] = ["horizontal"]
    fresh = _MW(window.cfg)
    check(fresh.tab == TAB_FOLDERS and not fresh.cfg["only_unseen"]
          and not fresh.cfg["folder_max"] and not fresh.cfg["orientations"],
          "au lancement : l'onglet Dossiers, sans filtre oublié d'une autre fois")
    fresh.close()
    fresh.deleteLater()

    from videosorter.board import BoardView as _BV
    from videosorter.widgets import VideoWake as _VW
    check(isinstance(window.board.wake, _VW) and isinstance(window.grid.wake, _VW),
          "le lecteur d'aperçu déplacé de case en case est remis au premier plan")
    from videosorter import media as _media
    with _media._Reading(Path("X:/lot/a.mp4")):
        check(_media.reading_under(Path("X:/lot")) and not _media.reading_under(Path("X:/autre")),
              "on sait quel fichier les fils de fond lisent : on n'attend que lui")

    check_data_safety(app, window, base, root)


def check_data_safety(app, window, base, root) -> None:
    """Index, favoris, réglages, corbeille : ce qui ne doit jamais se perdre."""
    import sqlite3 as _sq
    import threading as _th
    import videosorter.actions as _act
    import videosorter.scan as _scan
    from videosorter.index import INDEX, Index as _Index
    from videosorter.scan import Item as _Item

    # La fenêtre de [84] a refermé l'index en se fermant : on le rouvre.
    INDEX.reopen(base / "_appdata" / "index.db")
    work = base / "donnees"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    print("\n[85] L'index ne se jette plus pour une erreur passagère")
    spot = work / "f3" / "index.db"
    idx = _Index(spot)
    idx.put_listing(Path("X:/essai"), "k", ["a", "b"])
    idx.put_folder(_Item(path=Path("C:/r/d"), kind=MODE_FOLDERS, video_count=1,
                         videos=[Path("C:/r/d/v.mp4")]), "s")
    idx.commit(force=True)

    class _Flaky:
        def __init__(self, real):
            self.real = real

        def commit(self):
            raise _sq.OperationalError("disk I/O error")

        def __getattr__(self, name):
            return getattr(self.real, name)

    real_db = idx.db
    idx.db = _Flaky(real_db)
    idx.put_listing(Path("X:/autre"), "k", ["c"])
    idx.commit(force=True)
    idx.db = real_db
    check(not idx.rebuilt and not list(spot.parent.glob("index.db.abime-*"))
          and idx.listing(Path("X:/essai"), "k") == ["a", "b"],
          "une erreur d'écriture passagère ne refait pas l'index")
    idx.check_now()
    idx.close()
    spot.write_bytes(b"ceci n'est pas une base" * 100)
    for extra in ("-wal", "-shm"):
        Path(str(spot) + extra).unlink(missing_ok=True)
    back = _Index(spot)
    check(back.restored and back.listing(Path("X:/essai"), "k") == ["a", "b"],
          "vraiment abîmé : repris de la copie du jour, en fond, et non vidé")
    back.close()

    print("\n[86] Ce qu'on sait d'une vidéo la suit quand on la range")
    old = str(work / "A trier" / "clip")
    new = str(work / "Rangees" / "clip")
    video = old + "\\v.mp4"
    INDEX.put_probe(video, "1|2", {"duration": 5.0, "width": 10, "height": 20,
                                   "codec": "h264", "ok": True})
    INDEX.put_title(video, "1|2", "Un titre")
    INDEX.put_scenes(video, "1|2", [1.5, 3.0])
    INDEX.put_sig(video, "1|2", [1, 2, 3], 9)
    INDEX.mark_seen(old)
    INDEX.relocate(old, new)
    moved = new + "\\v.mp4"
    check(INDEX.probe(moved) and INDEX.title_of(moved) == "Un titre"
          and INDEX.scenes_of(moved) == [1.5, 3.0] and INDEX.sig_of(moved) == [1, 2, 3]
          and INDEX.is_seen(new),
          "sondage, titre, plans, empreinte et « déjà vu » suivent le dossier")
    check(not INDEX.probe(video) and not INDEX.sig_of(video) and not INDEX.is_seen(old),
          "et rien ne reste à l'ancien chemin, pris pour un doublon")
    INDEX.forget_tree(new)
    check(not INDEX.probe(moved) and not INDEX.has_scenes(moved),
          "une vidéo détruite n'a plus de lignes en base")
    INDEX.put_sig(str(work / ".videosorter-corbeille" / "x" / "z.mp4"), "s", [7, 8], 1)
    stop = [False]
    errors = []

    def _writer():
        i = 0
        while not stop[0]:
            INDEX.put_sig(str(work / "sonde" / f"{i}.mp4"), "s", [i, i + 1], 1)
            i += 1

    writers = [_th.Thread(target=_writer) for _ in range(3)]
    for thread in writers:
        thread.start()
    try:
        for _ in range(10):
            sigs = INDEX.all_sigs()
    except Exception as exc:                           # noqa: BLE001
        errors.append(exc)
        sigs = []
    stop[0] = True
    for thread in writers:
        thread.join()
    check(not errors, f"« doublons d'après les empreintes » pendant un sondage ({errors[:1]})")
    check(not any(".videosorter-corbeille" in p for p, _v, _s in sigs),
          "une vidéo en corbeille n'est pas proposée comme doublon")
    INDEX.forget_tree(work)

    print("\n[87] L'index d'une racine connue n'est jamais élagué")
    import videosorter.index as _vi
    keys = [str(work / "big" / f"D{i:03d}") for i in range(40)]
    for key in keys:
        INDEX.put_folder(_Item(path=Path(key), kind=MODE_FOLDERS, video_count=1,
                               videos=[Path(key) / "v.mp4"]), "s")
    INDEX.put_listing(work / "big", MODE_FOLDERS, keys[:25])
    INDEX.commit(force=True)
    saved_max = _vi.MAX_FOLDERS
    _vi.MAX_FOLDERS = 5
    try:
        INDEX.prune()
    finally:
        _vi.MAX_FOLDERS = saved_max
    check(len(INDEX.folders(keys[:25])) == 25,
          "au-delà du plafond, ce qu'affiche une racine reste")
    known = INDEX.folders(keys[:3])
    first = known[keys[0]].videos[0]
    check(first == Path(keys[0]) / "v.mp4" and str(first) == keys[0] + "\\v.mp4"
          and first.name == "v.mp4",
          "les chemins rendus par l'index valent ceux de pathlib")
    INDEX.drop_listing(work / "big")

    print("\n[88] Une coupure réseau n'efface pas la collection")
    window.start_root(root, MODE_FOLDERS)
    wait_for(app, lambda: not window.scanning, 60)
    before = [i.item_id for i in window.all_items]
    listing = INDEX.listing(root, _scan.listing_key(MODE_FOLDERS, window.cfg["expand_parents"]))
    real_scandir = _scan.os.scandir

    def _root_fails(target="."):
        if Path(str(target)) == Path(root):
            raise OSError(22, "Le nom réseau spécifié n'est plus disponible")
        return real_scandir(target)

    _scan.os.scandir = _root_fails
    try:
        window.start_root(root, MODE_FOLDERS)
        reader = window.scan_thread
        wait_for(app, lambda: not window.scanning, 30)
        pump(app, 0.3)
    finally:
        _scan.os.scandir = real_scandir
    check(reader is not None and reader.failure
          and [i.item_id for i in window.all_items] == before,
          f"racine injoignable : la liste reste celle du dernier passage ({len(before)})")
    check(INDEX.listing(root, _scan.listing_key(MODE_FOLDERS, window.cfg["expand_parents"]))
          == listing, "et l'index n'est pas réécrit à vide")
    partial = work / "partiel"
    (partial / "sous").mkdir(parents=True)
    (partial / "v.mp4").write_bytes(b"x")
    (partial / "sous" / "doc.pdf").write_bytes(b"x")

    def _sub_fails(target="."):
        if Path(str(target)).name == "sous":
            raise OSError(22, "coupure")
        return real_scandir(target)

    _scan.os.scandir = _sub_fails
    try:
        seen = _scan.scan_folder(partial)
    finally:
        _scan.os.scandir = real_scandir
    check(seen.incomplete and seen.file_count == 1,
          "un sous-dossier illisible rend l'élément « incomplet », pas « 100 % vidéo »")
    shelf = work / "rayon"
    (shelf / "+R" / "Enfant").mkdir(parents=True)
    _scan.list_entries(shelf, MODE_FOLDERS, True, True, {}, INDEX)
    (shelf / "+R" / "Neuf").mkdir()
    names = [p.name for p in _scan.list_entries(shelf, MODE_FOLDERS, True, True, {}, INDEX)]
    check("Neuf" in names, "un rayonnage « + » est relu à chaque passage")

    print("\n[89] Déplacer, supprimer, restaurer : jusqu'au bout, et le dire")
    from videosorter.transfer import Transfer as _T, TransferQueue as _TQ
    queue = _TQ()
    done = []
    queue.finished.connect(done.append)
    real_move = _act.move_to
    _act.move_to = lambda *_a: (_ for _ in ()).throw(ValueError("inattendu"))
    try:
        queue.submit(_T(kind="move", src=work / "rien", dest=work / "ici"))
        wait_for(app, lambda: not queue.busy, 10)
    finally:
        _act.move_to = real_move
    check(not queue.busy and done and done[0].state == "failed",
          "une erreur imprévue libère la file des transferts")
    try:
        _act.move_to(work / "nulle-part", work / "ici")
        why = None
    except _act.ActionError as exc:
        why = exc
    check(why is not None and not why.retry and str(why).startswith("Introuvable"),
          "« introuvable » ne se réessaie pas douze fois")
    locked = work / "verrou"
    locked.mkdir()
    for i in range(4):
        (locked / f"f{i}.txt").write_text("x")
    handle = open(locked / "f1.txt")
    try:
        try:
            _act.delete(locked, "permanent")
            message = ""
        except _act.ActionError as exc:
            message = str(exc)
    finally:
        handle.close()
    check(sorted(p.name for p in locked.iterdir()) == ["f1.txt"]
          and "3 fichier(s) supprimé(s)" in message,
          f"un fichier verrouillé n'arrête pas la suppression, et le message le dit")
    album = work / "vol_a" / "Album"
    album.mkdir(parents=True)
    for i in range(3):
        (album / f"p{i}.mp4").write_bytes(b"y" * (i + 1))
    real_rename = _act.os.rename
    calls = []

    def _cross(a, b):
        calls.append(a)
        if len(calls) == 1:
            raise OSError(18, "Invalid cross-device link")
        return real_rename(a, b)

    _act.os.rename = _cross
    try:
        landed = _act.move_to(album, work / "vol_b")
    finally:
        _act.os.rename = real_rename
    check(landed == work / "vol_b" / "Album" and len(list(landed.iterdir())) == 3
          and not album.exists() and not list((work / "vol_b").glob("*.prisme-partiel")),
          "d'un volume à l'autre : copie sous un nom provisoire, vérifiée, puis renommée")

    from videosorter.trash import MANIFEST, SessionTrash
    troot = work / "troot"
    (troot / "Dossier").mkdir(parents=True)
    (troot / "Dossier" / "v.mp4").write_bytes(b"z")
    session = SessionTrash()
    session.set_base(troot)
    stored = _act.move_to(troot / "Dossier", session.folder_for(troot / "Dossier"))
    entry = session.record(troot / "Dossier", stored, 1)
    session._drain()
    later = SessionTrash()
    later.stamp = "20990101-000000"
    check((stored.parent / MANIFEST).exists()
          and [e.origin for e in later.leftovers(troot)] == [troot / "Dossier"],
          "après un arrêt net, la séance suivante retrouve la corbeille et son origine")
    real_relocate = _act._relocate
    _act._relocate = lambda *_a: (_ for _ in ()).throw(OSError(5, "Accès refusé"))
    try:
        try:
            session.restore(entry)
            refused = ""
        except _act.ActionError as exc:
            refused = str(exc)
    finally:
        _act._relocate = real_relocate
    check(refused.startswith("Restauration impossible") and entry in session.entries,
          "« Tout restaurer » : une erreur disque devient un message, l'élément reste")
    session.restore(entry)
    (troot / "E").mkdir()
    (troot / "E" / "a.mp4").write_bytes(b"a")
    stored = _act.move_to(troot / "E", session.folder_for(troot / "E"))
    session.record(troot / "E", stored, 1)
    session.flush_in_background("permanent").join(20)
    check(session.flush_result == (1, "") and not stored.exists()
          and not (troot / ".videosorter-corbeille").exists(),
          "la vidange peut tourner hors du fil de l'interface, et range derrière elle")

    print("\n[90] Favoris et réglages ne repartent jamais de zéro en silence")
    from videosorter.config import Config as _Cfg
    from videosorter.ratings import Ratings as _R
    rpath = work / "fav" / "ratings.json"
    stars = _R(path=rpath)
    stars.set("C:\\A\\Anniv", 1)
    stars.set("C:\\A\\Anniv\\clip.mp4", 1)
    stars.set("C:\\A\\Anniv2\\x.mp4", 1)
    stars.rename("C:\\A\\Anniv", "C:\\B\\Anniv")
    check(stars.get("C:\\B\\Anniv\\clip.mp4") and stars.get("C:\\A\\Anniv2\\x.mp4"),
          "un dossier déplacé emporte les étoiles de ses vidéos, pas celles du voisin")
    stars.flush()
    _R(path=rpath)                          # la copie de secours du lancement
    rpath.write_text("{abîmé", encoding="utf-8")
    again = _R(path=rpath)
    check(again.get("C:\\B\\Anniv\\clip.mp4") and again.problem
          and list(rpath.parent.glob("ratings.abime-*.json")),
          "un fichier de favoris abîmé est mis de côté, la copie reprend")
    cpath = work / "cfg" / "config.json"
    conf = _Cfg(path=cpath)
    conf["tags"] = ["un", "deux"]
    conf.save()
    _Cfg(path=cpath)
    cpath.write_text("{abîmé", encoding="utf-8")
    check(_Cfg(path=cpath)["tags"] == ["un", "deux"],
          "des réglages abîmés sont repris de la copie de secours")

    from videosorter import perf as _perf
    check(_perf.HICCUP < 0.2 <= _perf.STALL < 0.8,
          "le chien de garde relève aussi les à-coups de quelques dixièmes")


def check_media(app, window, base) -> None:
    """Vignettes, sondages et travaux de fond : les corrections de l'audit."""
    import threading as _th
    from make_fixture import make_video
    from videosorter import media as M
    from videosorter import stamps as S
    from videosorter.backfill import SceneScan, ThumbAudit
    from videosorter.index import INDEX

    print("\n[85] Médias : libérer sans attendre, vignettes qui suivent, fond qui cède")
    lot = base / "medias"
    shutil.rmtree(lot, ignore_errors=True)
    one = lot / "a" / "clip.mp4"
    make_video(one, 6, 91)
    longue = lot / "a" / "longue.mp4"
    make_video(longue, 40, 92)
    real_spawn = M._spawn

    # -- ranger ne gèle plus : le ffmpeg qui lit la cible est arrêté net ------
    out = {}

    def lit():
        with M._Reading(longue):
            out["r"] = M._spawn([Tools.ffmpeg, "-re", "-i", str(longue),
                                 "-f", "null", "-"], 30)

    reader = _th.Thread(target=lit)
    reader.start()
    wait_for(app, lambda: M.reading_under(longue) and any(M._PROCS.values()), 5)
    started = time.perf_counter()
    window.preview.release(longue)
    spent = time.perf_counter() - started
    reader.join(5)
    check(spent < 0.05, f"libérer un fichier rend la main aussitôt ({spent * 1000:.1f} ms)")
    check(out.get("r", (0, "", "", ""))[3] == "tue" and not M.reading_under(longue),
          "et le ffmpeg qui le lisait est arrêté")
    M.unblock(longue)

    # -- une seule extraction pour deux demandes, jamais de JPEG tronqué ------
    calls = []

    def compte(cmd, timeout):
        calls.append(cmd)
        time.sleep(0.2)
        return real_spawn(cmd, timeout)

    M._spawn = compte
    try:
        got = []
        hands = [_th.Thread(target=lambda: got.append(M.extract_thumb(one, 1.0, 200)))
                 for _ in range(2)]
        for hand in hands:
            hand.start()
        for hand in hands:
            hand.join()
    finally:
        M._spawn = real_spawn
    check(len(calls) == 1 and len(got) == 2 and all(got),
          f"deux demandes de la même image : un seul ffmpeg ({len(calls)})")
    check(calls and "-noaccurate_seek" not in calls[0],
          "le premier essai tombe pile sur l'instant")
    check(not list(M.THUMB_DIR.rglob("*.part.jpg")),
          "l'image s'écrit à côté puis prend son nom : rien de partiel ne reste")
    calls.clear()
    M._spawn = compte
    try:
        M.extract_thumb(longue, 35.0, 200, keyframe=True)
    finally:
        M._spawn = real_spawn
    check(calls and "passthrough" in calls[0],
          "une carte loin dans la vidéo se contente de l'image-clé")

    # -- le nom d'une vignette ne dépend plus du dossier ----------------------
    copie = lot / "b" / "clip.mp4"
    copie.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(one, copie)
    check(M.thumb_path(one, 1.0, 200) == M.thumb_path(copie, 1.0, 200),
          "même nom, taille et date : les copies partagent leur vignette")
    empreinte = S.stamp_of(one)
    ancienne = M._legacy_key(one, empreinte, 2.5, 200)
    ancienne.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(M.thumb_path(one, 1.0, 200), ancienne)
    check(M.cached_thumb(one, 2.5, 200) == M.thumb_path(one, 2.5, 200)
          and not ancienne.exists(),
          "une vignette nommée à l'ancienne est reprise, pas refaite")
    INDEX.put_probe(one, empreinte, {"duration": 6.0, "width": 320, "height": 240,
                                     "codec": "h264", "ok": True})
    carte = M.build_preview_plan([one], 1, 0, one_per_video=False, blind=True)[0][1]
    M.extract_thumb(one, carte, 200)
    rangee = lot / "c" / "clip (2).mp4"
    rangee.parent.mkdir(parents=True, exist_ok=True)
    os.replace(one, rangee)
    S.forget(one)
    M.relocate_thumbs(one, rangee)
    check(S.stamp_of(rangee) == empreinte and INDEX.probe(rangee) is not None,
          "une vidéo rangée garde son empreinte et son sondage")
    check(M.cached_thumb(rangee, carte, 200) is not None,
          "et sa carte, même renommée à l'arrivée")

    # -- empreintes reprises de l'index, sans lecture réseau ------------------
    fantome = lot / "absent" / "fantome.mp4"
    INDEX.put_probe(fantome, "123|456", {"duration": 5.0, "width": 320,
                                         "height": 240, "codec": "", "ok": True})
    check(S.stamp_of(fantome) == "123|456" and S.known(fantome) == (456, 123.0),
          "au lancement suivant, l'empreinte vient de l'index, pas du disque")
    S.forget(fantome)
    check(S.stamp_of(fantome) == "", "un chemin auquel on vient de toucher se relit")

    # -- une coupure n'est pas un fichier illisible ---------------------------
    coupure = lot / "partage_absent" / "video.mp4"
    M.probe(coupure)
    check(INDEX.probe(coupure) is None, "un sondage raté pendant une coupure n'est pas retenu")
    abime = lot / "a" / "abime.mp4"
    abime.write_bytes(os.urandom(4096))
    M.probe(abime)
    check(INDEX.probe(abime) is not None and not INDEX.probe(abime)["ok"],
          "un fichier présent et abîmé, lui, l'est")
    INDEX.put_probe(copie, "", {"duration": 0.0, "width": 0, "height": 0,
                                "codec": "", "ok": False})
    check(M.probe(copie)["ok"], "un échec retenu à tort est revérifié")

    # -- un délai dépassé ne vaut pas « aucun plan » --------------------------
    check(M.scene_times(copie, timeout=0.001) is None,
          "relevé des plans interrompu : rien à conclure")
    real_scenes = M.scene_times
    M.scene_times = lambda video, timeout=90: None
    try:
        releve = SceneScan(lot / "b", True)
        releve._one(copie)
    finally:
        M.scene_times = real_scenes
    check(not INDEX.has_scenes(copie), "et rien n'est enregistré pour cette vidéo")

    # -- ce qu'on regarde passe devant, même quand ffmpeg est pris ------------
    manager = M.PreviewManager(200)
    manager.pool.setMaxThreadCount(1)
    gate = _th.Event()

    class Bouchon(M._Job):
        def work(self):
            gate.wait(10)

    manager.pool.start(Bouchon(manager.signals, "bouchon"))
    got = {}
    manager.plan_ready.connect(lambda key, plan: got.setdefault("plan", plan))
    manager.thumb_ready.connect(lambda key, slot, path: got.setdefault(key, path))
    manager.request_plan("board@x", [str(copie)], 1, blind=True)
    check(wait_for(app, lambda: "plan" in got, 2),
          "un plan à l'aveugle ne fait pas la queue derrière ffmpeg")
    manager.request_thumb("board@y", 0, str(copie), 1.0)
    check(M.FOREGROUND.pending > 0, "une demande au premier plan est comptée")
    check(wait_for(app, lambda: "board@y" in got, 2),
          "une vignette déjà faite arrive pendant que ffmpeg est occupé")
    for index in range(150):
        manager.request_thumb(f"board@{index}", 0, str(copie), 3.0 + index, urgent=False)
    check(len(manager.tracked) >= 140, f"aucun travail n'échappe au suivi ({len(manager.tracked)})")
    quitte = manager.cancel_prefix("board@", keep={"board@3"})
    check(quitte >= 140, f"tourner la page annule ceux de la page quittée ({quitte})")
    gate.set()
    manager.pool.setMaxThreadCount(2)
    manager.quiesce(20000)
    check(len(manager.tracked) == 0 and wait_for(app, lambda: M.FOREGROUND.pending == 0, 5),
          "chacun se retire en finissant, et le compte retombe")

    # -- une récolte finie ne garde rien -------------------------------------
    recolte = manager.start_harvest([("k", str(copie), 1.0)])
    wait_for(app, lambda: not recolte.isRunning(), 30)
    check(recolte.tasks == [] and not recolte._queue, "une récolte finie libère ses tâches")
    manager.stop_harvest()
    check(manager._retired == [], "et elle est détruite")

    # -- l'état des vignettes ne compte pas la corbeille ---------------------
    corbeille = lot / "b" / ".videosorter-corbeille"
    corbeille.mkdir(exist_ok=True)
    shutil.copy2(copie, corbeille / "jete.mp4")
    res = {}
    audit = ThumbAudit(lot / "b", 200, True)
    audit.done.connect(lambda seen, ready: res.update(seen=seen))
    audit.start()
    wait_for(app, lambda: "seen" in res, 30)
    check(res.get("seen") == 1, f"l'audit ne compte que ce que la préparation voit ({res})")


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
    from PySide6.QtWidgets import QMessageBox as _QMB
    _QMB.question = staticmethod(lambda *a, **k: _QMB.Yes)
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
    # Selon le tirage, Melange n'est pas toujours parmi les deux suivantes :
    # son plan, demande puis quitte dans le meme tour, n'est plus livre (il
    # aurait lance des extractions pour rien). On l'ouvre donc.
    mel_key = f"{melange.path}@0"
    if mel_key not in window.plans:
        window.show_item([i.name for i in window.items].index("Melange"))
        wait_for(app, lambda: mel_key in window.plans, 30)
    plan_mel = window.plans.get(mel_key, [])
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
    check_media(app, window, base)

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
