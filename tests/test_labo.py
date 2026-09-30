"""Le labo IA (essai) : isolement, analyse, recherche, tags, ouverture, arrêt.

Lancement : python tests/test_labo.py
Quelques vraies videos de couleur unie (ffmpeg), l'interface en mode
« offscreen ». Aucun modele a telecharger : un moteur factice lit la couleur
des images, et comprend « rouge », « vert », « bleu ». Si torch et open_clip
sont installes, un petit modele open_clip aux poids aleatoires fait en plus
tout le chemin reel (sans poids, ses resultats ne veulent rien dire).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SANDBOX = os.path.join(tempfile.gettempdir(), "prisme-tests-labo")
shutil.rmtree(SANDBOX, ignore_errors=True)
os.environ["PRISME_SANDBOX"] = SANDBOX
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HEAVY = ("torch", "open_clip", "transformers")
TORCH_HERE = __import__("importlib.util").util.find_spec("torch") is not None

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from videosorter.config import Config  # noqa: E402
from videosorter.media import NO_WINDOW, Tools  # noqa: E402
from videosorter.scan import MODE_FILES  # noqa: E402

FAILS: list = []
SHOT = os.environ.get("LABO_SHOT", "")


def check(condition, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


def wait_for(app, predicate, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def settle(app, seconds: float = 0.3) -> None:
    wait_for(app, lambda: False, seconds)


def make_colour(path: Path, colours: list, seconds_each: int = 6) -> None:
    """Une vidéo faite de plans unis, l'un après l'autre."""
    path.parent.mkdir(parents=True, exist_ok=True)
    inputs, labels = [], []
    for index, colour in enumerate(colours):
        inputs += ["-f", "lavfi", "-i",
                   f"color=c={colour}:s=160x90:r=5:d={seconds_each}"]
        labels.append(f"[{index}:v]")
    graph = "".join(labels) + f"concat=n={len(colours)}:v=1:a=0[v]"
    subprocess.run([Tools.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    *inputs, "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   creationflags=NO_WINDOW, check=True)


class ColourEncoder:
    """Un moteur factice : l'image vaut sa couleur moyenne, la phrase ses
    mots de couleur. De quoi verifier le classement sans aucun modele."""

    WORDS = {"rouge": (1, 0, 0), "vert": (0, 1, 0), "bleu": (0, 0, 1),
             "red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1)}

    def __new__(cls, delay: float = 0.0):
        from videosorter import ia

        class _Colour(ia.Encoder):
            name = "factice/couleurs"
            label = "Factice (couleurs)"
            device = "factice"
            dim = 8
            lang = "fr"
            batch = 4

            def __init__(self):
                super().__init__()
                self.delay = delay
                self.images = 0
                self.texts: list = []

            def _vec(self, rgb):
                import numpy as np
                v = np.zeros(self.dim, dtype=np.float32)
                v[:3] = np.asarray(rgb, dtype=np.float32) - np.mean(rgb)
                v[7] = 0.3          # ce que toutes les images ont en commun
                return v / max(float(np.linalg.norm(v)), 1e-8)

            def encode_images(self, paths):
                import numpy as np
                out, ok = [], []
                for path in paths:
                    if self.delay:
                        time.sleep(self.delay)
                    image = QImage(path)
                    if image.isNull():
                        out.append(np.zeros(self.dim, dtype=np.float32))
                        ok.append(False)
                        continue
                    colour = image.scaled(1, 1).pixelColor(0, 0)
                    out.append(self._vec((colour.redF(), colour.greenF(),
                                          colour.blueF())))
                    ok.append(True)
                self.images += len(paths)
                return np.stack(out) if out else np.zeros((0, self.dim)), ok

            def encode_texts(self, texts):
                import numpy as np
                self.texts.extend(texts)
                out = []
                for text in texts:
                    rgb = [0.0, 0.0, 0.0]
                    for word, value in ColourEncoder.WORDS.items():
                        if word in text.lower():
                            rgb = [a + b for a, b in zip(rgb, value)]
                    v = self._vec(rgb) if any(rgb) else np.zeros(self.dim, np.float32)
                    v[7] += 0.5     # la tournure (« une photo de ») : commune
                    out.append(v / max(float(np.linalg.norm(v)), 1e-8))
                return np.stack(out)

        return _Colour()


def search(app, lab, text: str) -> bool:
    """Lance une recherche et attend ses résultats (pas seulement l'annonce)."""
    lab.query.setText(text)
    lab.search()
    return wait_for(app, lambda: lab.results_title.text().startswith(f"« {text} » :"), 20)


def main() -> int:
    app = QApplication(sys.argv)
    Tools.resolve()
    if not Tools.ffmpeg:
        print("ffmpeg introuvable")
        return 1

    print("\n[1] Prisme ne charge rien du labo au lancement")
    from videosorter.window import MainWindow
    from videosorter import ia, labo
    window = MainWindow(Config())
    window.show()
    settle(app)
    check(not any(name in sys.modules for name in HEAVY),
          "ni torch, ni open_clip, ni transformers importés")
    entries = [a.text() for a in window._menu_actions()]
    check("Labo IA (essai)…" in entries, "⋯ › Collection › Labo IA (essai)… existe")
    top = [a for a in window.overflow.actions() if not a.isSeparator()]
    check(len(top) <= 9, f"le menu ⋯ garde ses {len(top)} lignes")

    print("\n[2] Dépendances manquantes : ce qu'il faut, et un bouton pour l'installer")
    real_missing = ia.missing_packages
    ia.missing_packages = lambda: ["torch", "open_clip_torch"]
    base = Path(tempfile.gettempdir()) / "prisme-labo-fixture"
    shutil.rmtree(base, ignore_errors=True)
    folder = base / "films"
    make_colour(folder / "rouge.mp4", ["red"], 12)
    make_colour(folder / "vert.mp4", ["green"], 12)
    make_colour(folder / "bleu.mp4", ["blue"], 12)
    # Bleue d'abord, puis rouge : le meilleur instant pour « rouge » est
    # dans la seconde moitie.
    make_colour(folder / "bleu-puis-rouge.mp4", ["blue", "red"], 10)
    window.start_root(folder, MODE_FILES)
    wait_for(app, lambda: not window.scanning and len(window.items) == 4)
    window.open_ai_lab()
    lab = window._ai_lab
    settle(app)
    check(lab.isVisible() and not lab.isModal(), "le labo s'ouvre, non modal")
    check(lab.pages.currentWidget() is lab.missing_page, "la page des dépendances paraît")
    check("torch" in lab.missing_text.text(), "elle nomme ce qui manque")
    command = lab.command_line.text()
    check(sys.executable in command and "open_clip_torch" in command
          and "torch" in command, f"la commande pip est la bonne ({command[:60]}…)")
    lab.install_kind.setCurrentIndex(1)
    check("download.pytorch.org" in lab.command_line.text(),
          "la version carte graphique passe par l'index de PyTorch")
    lab.install([[sys.executable, "-c",
                  "import sys; print('ligne un'); print('ligne deux'); sys.stdout.flush()"]])
    check(wait_for(app, lambda: lab.reopen_button.isEnabled(), 20),
          "l'installation se termine, sans figer la fenêtre")
    log = lab.install_log.toPlainText()
    check("ligne un" in log and "ligne deux" in log, "sa sortie s'affiche en direct")
    lab.install([[sys.executable, "-c", "import sys; sys.exit(3)"]])
    check(wait_for(app, lambda: "échoué" in lab.install_state.text(), 20),
          "un échec de pip est dit")
    # Ici, torch manque peut-etre vraiment : on fait comme s'il etait la, le
    # moteur factice n'en a pas besoin.
    ia.missing_packages = lambda: []
    lab.recheck()
    check(lab.pages.currentWidget() is lab.lab_page,
          "« Rouvrir le labo » montre le labo une fois tout installé")
    check(not any(name in sys.modules for name in HEAVY),
          "toujours rien de lourd en mémoire, labo ouvert")

    print("\n[3] Analyser le dossier ouvert, avec un moteur factice")
    encoder = ColourEncoder()
    lab.use_encoder(encoder)
    check(lab.analyze_button.isEnabled(), "« Analyser » est disponible")
    check(lab.scope_root() == str(folder), "la portée par défaut est le dossier ouvert")
    lab.frames_spin.setValue(6)
    lab.start_analysis()
    check(lab.indexer is not None and lab.analyze_button.text() == "Arrêter",
          "l'analyse part en tâche de fond")
    check(wait_for(app, lambda: lab.indexer is None, 120), "et se termine")
    stats = lab.last_stats
    check(stats["done"] == 4 and not stats["error"],
          f"quatre vidéos analysées ({stats})")
    videos, frames, failed = lab.store.counts(encoder.name)
    check(videos == 4 and failed == 0, "l'index du labo en garde quatre")
    check(frames >= 4 * 5, f"plusieurs images par vidéo ({frames})")
    matrix = lab._matrix()
    check(str(matrix.emb.dtype) == "float32" and matrix.dim == 8,
          "les vecteurs sont en mémoire, prêts à comparer")
    check(all(Path(matrix.thumb(v, int(matrix.starts[v]))).exists()
              for v in range(len(matrix))),
          "les images viennent du cache de vignettes de Prisme")
    check("ms par image" in lab.run_label.text(), f"le temps par image est dit "
          f"({lab.run_label.text()[-60:]})")
    check("4 vidéo(s)" in lab.index_label.text(), "la taille de l'index est dite")

    photo = base / "photos" / "rouge.png"
    photo.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(64, 48, QImage.Format_RGB32)
    image.fill(Qt.red)
    image.save(str(photo))
    duration, shots = ia.gather_frames(photo, 9, 480)
    check(len(shots) == 1 and shots[0][0] == 0.0 and Path(shots[0][1]).exists(),
          "une photo : une seule image, par le même cache")

    print("\n[4] Relancer ne refait rien ; une vidéo changée est reprise")
    before = encoder.images
    lab.start_analysis()
    wait_for(app, lambda: lab.indexer is None, 60)
    check(lab.last_stats["done"] == 0 and lab.last_stats["skipped"] == 4,
          "tout est déjà à jour")
    check(encoder.images == before, "aucune image réencodée")
    later = time.time() + 10
    os.utime(folder / "vert.mp4", (later, later))
    lab.start_analysis()
    wait_for(app, lambda: lab.indexer is None, 60)
    check(lab.last_stats["done"] == 1 and lab.last_stats["skipped"] == 3,
          "seule la vidéo modifiée est reprise")

    print("\n[5] Décrire la scène : classement et instants")
    lab.query.setText("rouge")
    QTest.keyClick(lab.query, Qt.Key_Return)
    check(wait_for(app, lambda: lab.results_title.text().startswith("« rouge » :"), 20)
          and lab.grid.count() > 0, "Entrée lance la recherche, des résultats paraissent")
    hits = [lab.grid.item(i).data(Qt.UserRole) for i in range(lab.grid.count())]
    names = [Path(h.path).name for h in hits]
    check(set(names[:2]) == {"rouge.mp4", "bleu-puis-rouge.mp4"},
          f"les vidéos rouges d'abord ({names})")
    check(all(a.score >= b.score for a, b in zip(hits, hits[1:])),
          "les scores décroissent")
    mixed = next(h for h in hits if Path(h.path).name == "bleu-puis-rouge.mp4")
    check(mixed.ts >= 10.0, f"l'instant trouvé est dans la partie rouge ({mixed.ts:.1f} s)")
    check("classement en" in lab.results_note.text(), "le temps de recherche est dit")
    check(any(t.startswith("une photo de") for t in encoder.texts),
          "la description passe par les gabarits")
    wait_for(app, lambda: not lab.thumb_timer.isActive(), 5)
    icon = lab.grid.item(0).icon().pixmap(labo.THUMB)
    colour = icon.toImage().pixelColor(labo.THUMB.width() // 2, labo.THUMB.height() // 2)
    check(colour.red() > 150 and colour.green() < 90,
          "la vignette montrée est l'image trouvée")
    search(app, lab, "bleu")
    first = lab.grid.item(0).data(Qt.UserRole)
    second = lab.grid.item(1).data(Qt.UserRole)
    check({Path(first.path).name, Path(second.path).name}
          == {"bleu.mp4", "bleu-puis-rouge.mp4"}, "« bleu » trouve les deux bleues")
    check(Path(second.path).name != "bleu-puis-rouge.mp4" or second.ts < 10.0,
          "et l'instant bleu de la vidéo mixte")

    print("\n[6] Calibrage : la référence neutre")
    import numpy as np
    matrix = lab._matrix()
    described = encoder.describe(["rouge"])[0]
    neutral = encoder.neutral()
    q_raw = ia.query_vector(described, neutral, calibrate=False)
    q_cal = ia.query_vector(described, neutral, calibrate=True)
    raw = matrix.emb @ q_raw
    cal = matrix.emb @ q_cal
    direct = raw - np.mean(np.stack([matrix.emb @ v for v in
                                     encoder._cached_texts(list(encoder.neutral_prompts))]),
                           axis=0)
    check(np.allclose(cal, direct, atol=1e-5),
          "chercher avec (description − neutre) = retirer la similarité neutre moyenne")

    print("\n[7] Plus comme celle-ci")
    search(app, lab, "rouge")
    red = next(lab.grid.item(i).data(Qt.UserRole) for i in range(lab.grid.count())
               if Path(lab.grid.item(i).data(Qt.UserRole).path).name == "rouge.mp4")
    lab.more_like(red, whole_video=False)
    similar = [Path(lab.grid.item(i).data(Qt.UserRole).path).name
               for i in range(lab.grid.count())]
    check("rouge.mp4" not in similar, "la vidéo elle-même n'y est pas")
    check(similar[:1] == ["bleu-puis-rouge.mp4"], f"la plus proche est l'autre rouge ({similar})")
    lab.more_like(red, whole_video=True)
    check(lab.grid.count() == 3, "par vidéo entière aussi")

    print("\n[8] Tags IA")
    scores = np.array([0.1] * 95 + [0.9] * 5, dtype=np.float32)
    check(len(ia.tag_matches(scores, 1.5, 0.10)) == 5, "cinq vidéos qui se détachent")
    check(len(ia.tag_matches(scores, 1.5, 0.03)) == 3, "le plafond les borne")
    check(ia.tag_matches(np.full(50, 0.3), 1.0, 0.5) == [], "un score plat ne retient rien")
    lab.tags_edit.setPlainText("rouge\nvert\nviolet\nrouge")
    lab.z_slider.setValue(5)
    lab.share_spin.setValue(50)
    lab.count_tags()
    check(wait_for(app, lambda: lab.tag_count("vert") is not None, 20), "les tags sont comptés")
    check(lab.tag_count("rouge") == 2, f"« rouge » : deux vidéos ({lab.tag_count('rouge')})")
    check(lab.tag_count("vert") == 1, f"« vert » : une vidéo ({lab.tag_count('vert')})")
    check(lab.tags_list.count() == 3, "les doublons de la liste sont retirés")
    check(lab.store.tags() == ["rouge", "vert", "violet"], "la liste est gardée par le labo")
    lab.share_spin.setValue(25)
    check(lab.tag_count("rouge") == 1, "le plafond se règle sans réencoder")
    lab.share_spin.setValue(50)
    lab.show_tag(lab.tags_list.item(0))
    check(lab.grid.count() == 2 and "Tag « rouge »" in lab.results_title.text(),
          "cliquer un tag montre ses vidéos")
    lab.show_tag(lab.tags_list.item(2))
    check("aucune" in lab.results_title.text(), "un tag sans vidéo le dit")
    before = len(encoder.texts)
    lab.z_slider.setValue(8)
    check(len(encoder.texts) == before, "bouger le seuil ne réencode rien")

    if SHOT:
        search(app, lab, "rouge")
        wait_for(app, lambda: not lab.thumb_timer.isActive(), 5)
        settle(app, 0.3)
        lab.grab().save(SHOT)
        print(f"  capture : {SHOT}")

    print("\n[9] Ouvrir un résultat : la fiche, à l'instant trouvé")
    search(app, lab, "rouge")
    row = next(i for i in range(lab.grid.count())
               if Path(lab.grid.item(i).data(Qt.UserRole).path).name
               == "bleu-puis-rouge.mp4")
    lab.grid.setCurrentRow(row)
    target = lab.grid.item(row).data(Qt.UserRole)
    QTest.keyClick(lab.grid, Qt.Key_Return)
    check(wait_for(app, lambda: window.current is not None
                   and str(window.current.path) == target.path, 20),
          "Entrée ouvre la vidéo dans Prisme")
    pending = window._pending_start
    check(pending is None or abs(pending[1] - int(target.ts * 1000)) < 5,
          "l'instant est demandé à la fiche")
    check(wait_for(app, lambda: abs(window.single.player.position()
                                    - target.ts * 1000) < 1500, 20),
          f"la fiche s'y place ({window.single.player.position()} ms, "
          f"attendu {int(target.ts * 1000)})")
    check(not lab.isVisible(), "le labo s'efface")
    window.open_ai_lab()
    check(window._ai_lab is lab and lab.grid.count() > 0,
          "le rouvrir retrouve tout en l'état")

    print("\n[10] Arrêter une analyse")
    many = base / "beaucoup"
    many.mkdir(parents=True, exist_ok=True)
    for index in range(10):
        shutil.copy(folder / "vert.mp4", many / f"v{index}.mp4")
    window.start_root(many, MODE_FILES)
    wait_for(app, lambda: not window.scanning and len(window.items) == 10)
    lab.refresh_scope()
    slow = ColourEncoder(delay=0.25)
    lab.use_encoder(slow)
    lab.start_analysis()
    wait_for(app, lambda: lab.progress.value() >= 1, 60)
    asked = time.time()
    lab.analyze_button.click()
    check(wait_for(app, lambda: lab.indexer is None, 10), "l'analyse s'arrête")
    check(time.time() - asked < 5, f"vite ({time.time() - asked:.1f} s)")
    check(lab.last_stats["stopped"] and 0 < lab.last_stats["done"] < 10,
          f"en gardant ce qui était fait ({lab.last_stats['done']} / 10)")
    check("arrêtée" in lab.run_label.text(), "et le dit")

    broken = ColourEncoder()

    def fail(_paths):
        raise MemoryError("plus de mémoire sur la carte")

    broken.encode_images = fail
    lab.use_encoder(broken)
    lab.start_analysis()
    check(wait_for(app, lambda: lab.indexer is None, 30),
          "une erreur d'encodage arrête l'analyse")
    check("plus de mémoire" in lab.run_label.text(), "et la dit")

    print("\n[11] Ctrl+K : le labo disparaît avec le reste")
    window.open_ai_lab()
    check(lab.isVisible(), "labo ouvert")
    window.enter_quiet()
    settle(app)
    check(not lab.isVisible(), "le repli le ferme")
    window.leave_quiet()
    settle(app)

    print("\n[12] Recherche instantanée sur 100 000 images")
    rng = np.random.default_rng(1)
    emb = rng.standard_normal((100_000, 512)).astype(np.float16)
    emb /= np.linalg.norm(emb.astype(np.float32), axis=1, keepdims=True).astype(np.float16)
    films = os.path.join(os.sep, "films")
    paths = [os.path.join(films, f"v{i:05d}.mp4") for i in range(10_000)]
    big = ia.Matrix("essai", paths, [10] * 10_000,
                    np.tile(np.arange(10, dtype=np.float32), 10_000), emb,
                    ["[]"] * 10_000)
    query = emb[12345].astype(np.float32)
    started = time.perf_counter()
    mask = big.mask_under(films)
    found, _ = big.rank(query, 60, mask)
    spent = time.perf_counter() - started
    check(found[0].path == paths[1234] and found[0].frame == 12345,
          "l'image elle-même arrive en tête")
    check(spent < 0.5, f"en moins d'une demi-seconde ({spent * 1000:.0f} ms)")

    if TORCH_HERE:
        print("\n[13] Le vrai chemin open_clip (petit modèle, poids aléatoires)")
        preset = ia.Preset("essai", "Essai", "ViT-B-32", "", "fr", 0, "")
        lab.preset_box.addItem("Essai (aléatoire)", "essai")
        real_by_key = ia.preset_by_key
        ia.preset_by_key = lambda key: preset if key == "essai" else real_by_key(key)
        lab.preset_box.setCurrentIndex(lab.preset_box.count() - 1)
        lab.load_engine()
        check(wait_for(app, lambda: lab.loader is None, 180), "le moteur se charge")
        clip = lab.encoder
        check(clip is not None and clip.dim == 512,
              f"ViT-B-32 prêt ({getattr(clip, 'device', '?')})")
        if clip is not None:
            window.start_root(folder, MODE_FILES)
            wait_for(app, lambda: not window.scanning and len(window.items) == 4)
            lab.refresh_scope()
            lab.start_analysis()
            check(wait_for(app, lambda: lab.indexer is None, 300), "l'analyse aboutit")
            check(lab.last_stats["done"] == 4 and not lab.last_stats["error"],
                  f"quatre vidéos encodées ({lab.last_stats})")
            vectors = lab._matrix().emb
            check(np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-2),
                  "vecteurs normés")
            check(search(app, lab, "une fille rousse") and lab.grid.count() == 4,
                  "la recherche rend un classement")
        ia.preset_by_key = real_by_key
        check("torch" in sys.modules, "torch n'est chargé qu'à ce moment-là")

    ia.missing_packages = real_missing
    window.close()
    settle(app)
    print(f"\n{'ÉCHEC' if FAILS else 'Tout est bon'} "
          f"({len(FAILS)} échec(s))")
    for label in FAILS:
        print("  -", label)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
