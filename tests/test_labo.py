"""Le Labo IA, avec un moteur factice (sans rien telecharger).

    python tests/test_labo.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BOX = os.path.join(tempfile.gettempdir(), "prisme-test-labo")
shutil.rmtree(BOX, ignore_errors=True)
os.environ["PRISME_SANDBOX"] = BOX
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from videosorter import ia  # noqa: E402

FAILS = []


def check(condition, label: str) -> None:
    print(("  ok   " if condition else "  ÉCHEC ") + label)
    if not condition:
        FAILS.append(label)


FRAMES = Path(BOX) / "images"
COLORS = {"plage_rouge.mp4": "#e02020", "foret_verte.mp4": "#20c040", "mer_bleue.mp4": "#2040e0"}


def frame_of(video: str, ts: float):
    """L'image d'une video factice : de la couleur de son nom."""
    FRAMES.mkdir(parents=True, exist_ok=True)
    name = Path(video).name
    if name not in COLORS:
        return None
    out = FRAMES / f"{name}-{int(ts)}.png"
    if not out.exists():
        image = QImage(64, 36, QImage.Format_RGB32)
        image.fill(QColor(COLORS[name]))
        image.save(str(out))
    return out


class Item:
    def __init__(self, videos):
        self.videos = videos
        self.is_tag = self.locked = False


class FakeWindow:
    def __init__(self, videos):
        self.cfg = {}
        self.all_items = [Item(videos)]
        self.played = []
        self.keywords = {}
        self.tags = []

    def add_to_keyword(self, keyword, videos):
        self.keywords[keyword] = list(videos)
        return len(videos)

    def _aside_play(self, video, title):
        self.played.append(video)


def main() -> int:
    videos = [str(Path(BOX) / "coll" / n) for n in COLORS] + [str(Path(BOX) / "coll" / "abime.mp4")]
    engine = ia.FakeEngine()

    print("\n[1] L'index")
    index = ia.SceneIndex("factice", Path(BOX) / "labo")
    added = ia.build(index, engine, videos, frame_of, duration_of=lambda v: 100.0)
    check(added == 3 and len(index.rows) == 9, "trois images par vidéo lisible (9 en tout)")
    check(str(videos[3]) in index.done, "une vidéo illisible n'est pas réessayée à chaque fois")
    again = ia.build(index, engine, videos, frame_of, duration_of=lambda v: 100.0)
    check(again == 0, "une seconde indexation ne refait rien")
    reloaded = ia.SceneIndex("factice", Path(BOX) / "labo")
    check(len(reloaded.rows) == 9 and len(reloaded.vectors[0]) == 4,
          "l'index se garde sur le disque, et se relit")

    check([ia.frame_count(d) for d in (40, 300, 600, 7200)] == [3, 7, 12, 16],
          "le nombre d'images suit la durée : 3 pour un clip court, 16 au plus")
    check(ia.spread([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], 4, 110) == [10, 40, 70, 100],
          "les instants des aperçus servent d'abord, bien répartis")
    import json
    meta_path = Path(BOX) / "labo" / "factice.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sampling"] = 1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    old = ia.SceneIndex("factice", Path(BOX) / "labo")
    check(not old.done and len(old.rows) == 9,
          "un index de l'ancien échantillonnage est à refaire (gardé en attendant)")
    ia.build(old, engine, videos, frame_of, duration_of=lambda v: 100.0)
    check(len(old.rows) == 9 and len(old.done) == 4,
          "refaite, une vidéo remplace ses anciennes images (pas de doublons)")

    print("\n[2] Chercher une scène")
    top = reloaded.search(engine.embed_texts(["une plage rouge"])[0], 3)
    check(top and Path(top[0][0]).name == "plage_rouge.mp4", "« rouge » trouve la vidéo rouge d'abord")
    top = reloaded.search(engine.embed_texts(["la mer bleue"])[0], 3)
    check(top and Path(top[0][0]).name == "mer_bleue.mp4", "« bleue » trouve la bleue")

    print("\n[2b] Une demande comprise idée par idée")
    check(ia.parse_query("amatrice qui pisse en extérieur -plage")
          == ("amatrice qui pisse en extérieur", ["amatrice", "pisse", "en extérieur"], ["plage"]),
          "« amatrice qui pisse en extérieur -plage » : trois idées exigées, une écartée")
    mixed = ia.SceneIndex("melange", Path(BOX) / "melange")
    shades = {"violet": (1, 0, 1, .05), "rouge": (1, 0, 0, .05), "bleu": (0, 0, 1, .05),
              "vert": (0, 1, 0, .05), "gris": (.3, .3, .3, .05)}
    for name, color in shades.items():
        for copy in range(3):
            mixed.add(f"{name}{copy}.mp4", [10.0], [ia._normalized(color)])

    def first(rows):
        return rows[0][0][:-5] if rows else ""
    check(first(ia.smart_search(mixed, engine, "rouge, bleu")) == "violet",
          "deux idées exigées : la vidéo qui a les deux passe devant celles qui n'en ont qu'une")
    check(first(ia.smart_search(mixed, engine, "rouge -bleu")) == "rouge",
          "« -bleu » écarte ce qui est bleu : le rouge pur passe devant le violet")
    liked = mixed.frame_vectors([("vert0.mp4", 10.0)])
    plain = [v for v, _t, _s in ia.smart_search(mixed, engine, "rouge", top=15)]
    taught = [v for v, _t, _s in ia.smart_search(mixed, engine, "rouge", top=15, liked=liked)]

    def place(rows):
        return min(at for at, v in enumerate(rows) if v.startswith("vert"))
    check(place(taught) < place(plain) and place(taught) < min(
              at for at, v in enumerate(taught) if v.startswith("violet")),
          f"« plus comme ça » : un exemple coché fait remonter ce qui lui ressemble "
          f"(vert {place(plain) + 1}e puis {place(taught) + 1}e)")
    check(len(set(plain)) == len(plain), "une vidéo par résultat")

    print("\n[3] Les tags IA")
    found = ia.tag_matches(reloaded, engine, ["vert", "bleu clair"], margin=0.1)
    greens = [Path(v).name for v, _t, _s in found["vert"]]
    check(greens and greens[0] == "foret_verte.mp4", "le tag « vert » va d'abord à la forêt")
    check(all(Path(v).name != "plage_rouge.mp4" for v, _t, _s in found["bleu clair"]),
          "un tag ne va pas à ce qui ne lui ressemble pas")

    print("\n[4] Les bibliothèques")
    commands = ia.install_commands()
    check(all(c[:3] == [sys.executable, "-m", "pip"] for c in commands),
          "l'installation passe par pip, pour ce Python-ci")

    print("\n[5] La fenêtre du labo")
    from videosorter.labo import LaboWindow
    shutil.rmtree(Path(BOX) / "labo", ignore_errors=True)
    ia.LAB_DIR = Path(BOX) / "labo"
    host = FakeWindow(videos)
    lab = LaboWindow(host_widget(host), engine=engine, frame_of=frame_of)
    lab._duration = lambda v: 100.0
    lab.index = ia.SceneIndex("factice", Path(BOX) / "labo")
    lab.show()
    lab._index()
    wait_idle(lab)
    check(len(lab.index.done) == 4, f"« Indexer la collection » passe tout ({lab.index_state.text()})")
    lab.query.setText("rouge")
    lab._search()
    wait_idle(lab)
    first = lab.results.item(0)
    check(first is not None and first.data(Qt.UserRole).endswith("plage_rouge.mp4"),
          "la recherche remplit la grille, la plus proche d'abord")
    lab._play(first)
    check(host.played and host.played[0].endswith("plage_rouge.mp4"),
          "un double-clic la lit dans le lecteur de côté")

    print("\n[6] Des résultats aux mots-clés")
    check(first.flags() & Qt.ItemIsUserCheckable, "chaque résultat se coche")
    lab._check_all(lab.results)
    checked = lab._checked(lab.results)
    check(len(checked) == len(set(checked)) and len(checked) >= 3,
          "tout cocher : chaque vidéo une fois, même trouvée à plusieurs instants")
    from PySide6.QtWidgets import QInputDialog
    original = QInputDialog.getItem
    QInputDialog.getItem = staticmethod(lambda *a, **k: ("couleurs vives", True))
    try:
        lab._add_checked(lab.results, "rouge")
    finally:
        QInputDialog.getItem = original
    check(host.keywords.get("couleurs vives") == checked,
          "« Ajouter au mot-clé » verse les cochées dans le mot choisi")
    check(lab._checked(lab.results) == [], "et les décoche")

    # La case, en haut a droite de la vignette : un clic dessus coche.
    from PySide6.QtCore import QEvent as _QE, QPointF as _QPF
    from PySide6.QtGui import QMouseEvent as _QME
    from PySide6.QtWidgets import QStyleOptionViewItem as _Opt
    from videosorter.labo import _CheckCorner
    delegate = lab.results.itemDelegate()
    check(isinstance(delegate, _CheckCorner), "la case est posée sur la vignette")
    index = lab.results.model().index(0, 0)
    option = _Opt()
    option.rect = lab.results.visualRect(index)
    option.widget = lab.results
    box = delegate._box(option, index)
    check(box.right() > option.rect.center().x() and box.top() < option.rect.center().y(),
          f"en haut à droite ({box.x()},{box.y()} dans {option.rect.width()}×{option.rect.height()})")
    spot = _QPF(box.center())
    delegate.editorEvent(_QME(_QE.MouseButtonRelease, spot, spot, Qt.LeftButton,
                              Qt.NoButton, Qt.NoModifier),
                         lab.results.model(), option, index)
    check(lab.results.item(0).checkState() == Qt.Checked, "un clic sur la case coche")

    # La suite arrive en descendant : rien ne s'efface, les cases restent.
    rows = [(lab.results.item(at).data(Qt.UserRole),
             float(lab.results.item(at).data(Qt.UserRole + 1) or 0), 0.0)
            for at in range(lab.results.count())]
    before = lab.results.count()
    lab._loading_more = True
    lab._fill("search", rows + [(str(Path(BOX) / "suite.mp4"), 1.0, 0.0)])
    check(lab.results.count() == before + 1
          and lab.results.item(0).checkState() == Qt.Checked,
          "la suite s'ajoute dessous, sans décocher ce qui l'était")
    check(not hasattr(lab, "more_button"), "plus de bouton « Afficher 60 de plus »")
    lab._check_all(lab.results, False)

    print("\n[6b] La recherche se souvient de ses exemples")
    lab.query.setText("rouge")
    lab._search()
    wait_idle(lab)
    lab.results.item(0).setCheckState(Qt.Checked)
    lab._learn(True)
    wait_idle(lab)
    saved = lab.window.cfg.get("labo_examples", {}).get("rouge", {})
    check(len(saved.get("liked", [])) == 1, "« Plus comme ça » garde l'exemple avec la phrase")
    lab._asked = ""
    lab.query.setText("  Rouge ")
    lab._search()
    wait_idle(lab)
    check(len(lab._liked) == 1 and not lab.feedback_state.isHidden(),
          "la même phrase, plus tard, repart de ses exemples (casse et espaces ignorés)")
    check(lab.results.item(0).text().startswith("✓ exemple"),
          "le bon exemple passe en tête, marqué ✓")
    last = lab.results.item(lab.results.count() - 1)
    gone = last.data(Qt.UserRole)
    last.setCheckState(Qt.Checked)
    lab._learn(False)
    wait_idle(lab)
    check(all(lab.results.item(i).data(Qt.UserRole) != gone for i in range(lab.results.count())),
          "« Moins comme ça » : la vidéo écartée disparaît des résultats")
    lab.query.setText("bleu")
    lab._search()
    wait_idle(lab)
    shown = [lab.results.item(i).data(Qt.UserRole) for i in range(lab.results.count())]
    fresh = [v for v, _t, _s in ia.smart_search(lab.index, engine, "bleu", len(shown))]
    check(shown == fresh and not lab._liked,
          "une recherche ne garde rien de la précédente : « bleu » après « rouge » appris = « bleu » à neuf")
    lab.query.setText("rouge")
    lab._search()
    wait_idle(lab)
    lab._forget_examples()
    wait_idle(lab)
    check("rouge" not in lab.window.cfg.get("labo_examples", {}),
          "« Repartir des seuls mots » les efface pour de bon")

    print("\n[6c] Les collections reconnues à leur nom")
    from videosorter.namegroups import find_groups, home_of
    names = ["Cum Fantasy, Night out.mp4", "Cum Fantasy, Beach day.mp4", "Cum Fantasy, Office 2.mp4",
             "cum fantasy beach.mp4", "Hot Studio - Scene 01 a8f3k.mp4", "Hot Studio - Scene 02 zz.mp4",
             "Hot Studio - Another q1.mp4", "VID_20240101_1200.mp4", "VID_20240101_1300.mp4",
             "VID_20240101_1400.mp4", "Random title.mp4"]
    coll = [str(Path(BOX) / "coll" / f"d{i % 3}" / n) for i, n in enumerate(names)]
    found = {g["name"]: g for g in find_groups(coll, "fin")}
    check(set(found) == {"Cum Fantasy", "Hot Studio"},
          f"le même début, à la lettre près, fait une collection ({sorted(found)})")
    check(len(found["Cum Fantasy"]["videos"]) == 3,
          "« cum fantasy » en minuscules n'en est pas : la casse compte")
    fine = {g["name"] for g in find_groups(coll, "tres_fin")}
    check("Hot Studio - Scene" in fine or "Hot Studio" in fine, f"« Très fin » serre davantage ({fine})")
    together = [str(Path(BOX) / "Hot Studio" / n) for n in names[4:7]]
    check(not find_groups(together, "fin"),
          "une collection déjà dans un dossier à son nom n'est pas proposée")
    moved = []
    lab.window.move_one = lambda path, dest: moved.append((path, dest["path"])) or True
    from PySide6.QtWidgets import QMessageBox
    original_question = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        lab._show_groups(find_groups(coll, "fin"))
        row = next(i for i, g in enumerate(lab._groups) if g["name"] == "Cum Fantasy")
        lab.group_list.setCurrentRow(row)
        check(lab.group_view.count() == 3 and lab.group_view.item(0).checkState() == Qt.Checked,
              "la collection s'affiche, ses fichiers cochés d'office")
        check(str(home_of(found["Cum Fantasy"]["videos"])) in lab.group_target.text(),
              "le dossier se crée là où sont la plupart de ses fichiers")
        lab.group_view.item(2).setCheckState(Qt.Unchecked)
        lab._keep_group()
    finally:
        QMessageBox.question = original_question
    check(len(moved) == 2 and all(Path(d).name == "Cum Fantasy" for _p, d in moved),
          "« Créer le dossier et y ranger » range les fichiers cochés, et eux seuls")
    middle = [str(Path(BOX) / "coll" / n) for n in
              ("Anna - Cum Fantasy - Office.mp4", "Lisa - Cum Fantasy - Pool.mp4",
               "Cum Fantasy, Night.mp4")]
    check(any(g["name"] == "Cum Fantasy" and len(g["videos"]) == 3
              for g in find_groups(middle, "fin")),
          "le morceau commun peut être au milieu du nom")
    seen = {}
    lab.window.wall_videos = lambda v: seen.__setitem__("mur", list(v))
    lab.window.playlist_videos = lambda v: seen.__setitem__("playlist", list(v))
    lab.window.browse_videos = lambda n, v: seen.__setitem__("dossier", (n, list(v)))
    lab._show_groups(find_groups(coll, "fin"))
    lab.group_list.setCurrentRow(next(i for i, g in enumerate(lab._groups) if g["name"] == "Hot Studio"))
    lab._group_wall()
    lab._group_playlist()
    lab._group_browse()
    lab._group_tag()
    check(len(seen.get("mur", [])) == 3 and len(seen.get("playlist", [])) == 3
          and seen.get("dossier", ("", []))[0] == "Hot Studio",
          "mur, playlist et « Parcourir » reçoivent les fichiers cochés")
    check(len(host.keywords.get("Hot Studio", [])) == 3,
          "« Créer un groupe » en fait un dossier virtuel (mot-clé ✦), sans rien déplacer")

    print("\n[6d] L'index suit les fichiers rangés")
    follow = ia.SceneIndex("suivi", Path(BOX) / "suivi")
    follow.add(r"C:\a\x.mp4", [1.0], [[1, 0, 0, 0]])
    follow.add(r"C:\dossier\y.mp4", [2.0], [[0, 1, 0, 0]])
    follow.save()
    ia.ENGINES["suivi"] = {"short": "suivi"}
    try:
        ia.rename_videos({r"C:\a\x.mp4": r"C:\b\x.mp4", r"C:\dossier": r"C:\rangé\dossier"},
                         Path(BOX) / "suivi")
    finally:
        ia.ENGINES.pop("suivi", None)
    again = ia.SceneIndex("suivi", Path(BOX) / "suivi")
    check({v for v, _t in again.rows} == {r"C:\b\x.mp4", r"C:\rangé\dossier\y.mp4"}
          and r"C:\b\x.mp4" in again.done,
          "un fichier rangé, ou son dossier, garde ses images dans l'index")

    print("\n[6e] On sait toujours ce que fait l'ordinateur")
    lab._tick_activity()
    check(lab.now.text().startswith("Prêt"), f"au repos : « {lab.now.text()} »")

    def slow() -> None:
        while not lab._stop:
            time.sleep(0.05)
        time.sleep(0.3)                  # la fin d'un lot, comme en vrai
    lab._run(slow, indexing=True, label="Indexation test")
    app.processEvents()
    check("Indexation test" in lab.now.text() and lab.spin.text() != "✓"
          and not lab.index_button.isEnabled(),
          f"pendant un travail : il est nommé, et l'indicateur tourne (« {lab.now.text()} »)")
    lab._halt()
    check(lab.now.text().startswith("Arrêt en cours") and not lab.stop_button.isEnabled()
          and lab.stop_button.text() == "Arrêt en cours…",
          "« Arrêter » : l'arrêt en cours se voit, le bouton le dit")
    wait_idle(lab)
    check(lab.now.text().startswith("Prêt") and lab.state.text().startswith("Arrêté")
          and lab.index_button.isEnabled(),
          "l'arrêt fini : « Prêt », et ce qui a été calculé est gardé")
    lab._halt()
    check(lab.now.text().startswith("Rien à arrêter"),
          "un clic qui ne peut rien faire le dit, en clair")

    print("\n[7] Comparer les moteurs")
    lab.bench_queries.setPlainText("rouge\nbleu")
    lab._bench_run()
    wait_idle(lab)
    view = lab.bench_views.get("factice") or next(iter(lab.bench_views.values()))
    check("rouge" in lab._bench["results"] and lab.bench_pick.count() == 2,
          "chaque recherche de l'essai a ses résultats")
    check(view.count() > 0 or any(v.count() for v in lab.bench_views.values()),
          "les résultats s'affichent en colonne")
    column = next(v for v in lab.bench_views.values() if v.count())
    column.item(0).setCheckState(Qt.Checked)
    app.processEvents()
    check(lab._bench["right"].get(lab.bench_pick.currentText()) and "juste" in lab.bench_total.text()
          or "%" in lab.bench_total.text(),
          f"cocher un résultat juste donne une note ({lab.bench_total.text()})")
    check(lab.window.cfg.get("labo_bench", {}).get("right"),
          "les jugements se gardent dans les réglages")
    lab.close()

    from videosorter.tagging import build_tag_items
    coll = [Path(BOX) / "coll" / n for n in ("plage_rouge.mp4", "foret_verte.mp4", "mer_bleue.mp4")]
    items = build_tag_items(["foret", "Couleurs vives"], coll,
                            members={"couleurs vives": [str(coll[0]), str(coll[1])]})
    names = {i.path.name: sorted(p.name for p in i.videos) for i in items}
    check(names.get("Couleurs vives") == ["foret_verte.mp4", "plage_rouge.mp4"],
          f"un mot-clé reçoit les vidéos rangées à la main, quel que soit leur nom ({names})")
    check("foret" not in names, "rangée à la main, une vidéo n'est plus disputée par son nom")
    marks = {i.path.name: i.name[0] for i in items}
    check(marks.get("Couleurs vives") == "✦",
          f"un mot-clé nourri par l'IA porte ✦ au lieu de # ({marks})")

    print("\n" + ("tout est vert" if not FAILS else f"{len(FAILS)} échec(s)"))
    return 1 if FAILS else 0


def host_widget(fake):
    """Une vraie fenetre Qt qui porte ce que le labo lit de Prisme."""
    from PySide6.QtWidgets import QWidget
    widget = QWidget()
    widget.cfg, widget.all_items, widget.played = fake.cfg, fake.all_items, fake.played
    widget._aside_play = fake._aside_play
    widget.add_to_keyword, widget.tags = fake.add_to_keyword, fake.tags
    host_widget.keep = widget
    return widget


def wait_idle(lab, limit: float = 20.0) -> None:
    deadline = time.time() + limit
    app.processEvents()
    while (lab._busy or not lab.mail.empty()) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    app.processEvents()


if __name__ == "__main__":
    sys.exit(main())
