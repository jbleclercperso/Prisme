"""Verification rapide du moteur et des onglets, en quelques secondes.

    python tests/smoke.py

Complement de `test_app.py`, qui fabrique de vraies videos et met plusieurs
minutes : ici l arborescence est minuscule et faite de fichiers vides, ce
qui suffit a verifier l inventaire, l index, les onglets, les mots-cles et
l etat de l analyse — ce qu on casse le plus souvent.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

base = Path(os.environ["TEMP"]) / "vs-smoke"
shutil.rmtree(base, ignore_errors=True)
root = base / "root"
for folder, names in {
    "+ Set": ["deja range"],
    "+ Beach": ["vrac beach z.mp4"],
    "Vacances beach 2019": ["beach sunset a.mp4", "beach party b.mp4"],
    "Soiree beach": ["BEACH night c.mp4", "road trip d.mp4"],
    "Divers": ["road movie e.mp4"],
}.items():
    (root / folder).mkdir(parents=True, exist_ok=True)
    for name in names:
        if name.endswith(".mp4"):
            (root / folder / name).write_bytes(b"\0" * 2048)
        else:
            (root / folder / name).mkdir(exist_ok=True)

sandbox = base / "_appdata"
sandbox.mkdir(parents=True, exist_ok=True)

from PySide6.QtWidgets import QApplication            # noqa: E402
from videosorter import config as vs_config           # noqa: E402
from videosorter import media as vs_media             # noqa: E402
from videosorter.config import Config                 # noqa: E402
from videosorter.index import INDEX                   # noqa: E402
from videosorter.header import TAB_FOLDERS, TAB_TAGS, TAB_VIDEOS  # noqa: E402
from videosorter.scan import MODE_FLAT, MODE_FOLDERS  # noqa: E402
from videosorter.window import MainWindow             # noqa: E402

vs_media.THUMB_DIR = sandbox / "thumbs"
vs_config.LOCAL_TRASH = sandbox / "_TRASH"
INDEX.reopen(sandbox / "index.db")

app = QApplication.instance() or QApplication(sys.argv)
cfg = Config(path=sandbox / "config.json")
window = MainWindow(cfg)
window.show()

FAILS = []


def check(cond, label):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


def pump(seconds=0.4):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def settle(limit=25):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        app.processEvents()
        if not window.scanning:
            break
        time.sleep(0.02)
    pump(0.3)


print("\n[1] Premier inventaire")
window.start_root(root, MODE_FOLDERS)
settle()
names = sorted(i.name for i in window.all_items)
check(not any(n == "+ Set" or n == "+ Beach" for n in names),
      f"les dossiers de tete ne se trient pas eux-memes ({names})")
check("deja range" in names,
      f"mais ce qu'ils contiennent, oui ({names})")
check(any("sans dossier" in n for n in names),
      f"et leurs videos en vrac ont leur propre entree ({names})")

print("\n[2] Relancement : rien a relire")
patches = []
window.start_root(root, MODE_FOLDERS)
shown_before = len(names)
check(len(window.all_items) == shown_before,
      f"la liste est deja la, avant le disque ({len(window.all_items)})")
window.scan_thread.patch.connect(lambda a, r, g: patches.append((len(a), len(r), len(g))))
settle()
check(patches == [], f"et la relecture n a rien corrige ({patches})")
check(window.scan_thread.rescanned == 0,
      f"aucun dossier reparcouru ({window.scan_thread.rescanned})")

print("\n[3] Les onglets repartent de la racine")
window.enter_current()
settle()
check(window.root != root, f"on peut descendre dans un dossier ({window.root.name})")
window.set_tab(TAB_FOLDERS)
settle()
check(window.root == root, f"recliquer l onglet ramene a la racine ({window.root})")
check(window.mode == MODE_FOLDERS, "et en mode dossiers")

window.set_tab(TAB_VIDEOS)
check(not window.scanning and bool(window.items),
      f"l onglet Videos repond sans relire le disque ({len(window.items)} videos)")
settle()
check(window.mode == MODE_FLAT, f"l onglet Videos montre les videos ({window.mode})")
check(window.root == root, "depuis la racine")
check(window.sort_mode == "random", "et dans un ordre aleatoire")
check(window.tree.action == "go",
      f"l arborescence passe en « aller dans » ({window.tree.action})")

window.set_tab(TAB_FOLDERS)
settle()
check(window.tree.action == "send",
      f"et revient a « envoyer vers » sur les dossiers ({window.tree.action})")

print("\n[4] Mots-cles : des categories, pas des doublons")
window.set_tag_family("top")
window.set_tab(TAB_TAGS)
settle()
tags = [i for i in window.all_items if i.is_tag]
check(bool(tags), f"des categories sont proposees ({[t.name for t in tags]})")
seen = [str(v) for t in tags for v in t.videos]
check(len(seen) == len(set(seen)),
      f"chaque video n apparait que dans une seule ({len(seen)} pour "
      f"{len(set(seen))} distinctes)")

window.set_tab(TAB_FOLDERS)
settle()
check("Analyser" in window.scan_button.text(),
      "au repos, le bouton propose d analyser (" + window.scan_button.text() + ")")
window.toggle_scan()
check(window.scanning, "un clic relance l analyse")
check("/" in window.scan_button.text() or "Analyse" in window.scan_button.text(),
      "et le bouton compte (" + window.scan_button.text() + ")")
settle()
check(not window.scanning and "Analyser" in window.scan_button.text(),
      "puis revient au repos (" + window.scan_button.text() + ")")
check("termin" in window.banner.text(),
      "en disant que c est fini (" + window.banner.text()[:55] + ")")

print()
print("[6] Un clic descend d un etage, et la pastille dit ce qu elle sait")
from videosorter.tagging import top_words, words_of          # noqa: E402

check(list(words_of("BigTitsAsianGirl 1080p.mp4"))[:4]
      == ["Big", "Tits", "Asian", "Girl"],
      "les mots colles sont separes ("
      + " ".join(list(words_of("BigTitsAsianGirl.mp4"))) + ")")
check("the" not in top_words(["the beach a.mp4", "the beach b.mp4"]),
      "les mots de grammaire sont ecartes ("
      + str(top_words(["the beach a.mp4", "the beach b.mp4"])) + ")")
check(all(" " not in w for w in top_words(["beach sun a.mp4", "beach sun b.mp4"])),
      "un mot-cle frequent est un seul mot")

window.set_tab(TAB_FOLDERS)
settle()
card = window.board.cards[0]
check(card.duration_chip.isVisible() and "vidéo" in card.duration_chip.text(),
      "la pastille d un dossier compte ses videos ("
      + card.duration_chip.text() + ")")

check(window.browsing, "on arrive sur les vignettes")
window.on_board_open(1)
check(not window.browsing and window.index == 1,
      "un clic ouvre la fiche de cette vignette (index " + str(window.index) + ")")
from PySide6.QtCore import Qt                                 # noqa: E402
from PySide6.QtTest import QTest                              # noqa: E402
QTest.keyClick(window, Qt.Key_Escape)
pump(0.3)
check(window.browsing and window.index == 1,
      "Echap remonte aux vignettes, la ou l on etait (index "
      + str(window.index) + ")")

from videosorter.board import PAGE_SIZE                        # noqa: E402
check(PAGE_SIZE == 40, "les vignettes vont par pages de " + str(PAGE_SIZE))

print()
print("[7] Arborescence : les destinations seulement")
window.tree.set_root(str(root))
pump(0.5)
model = window.tree.model
parent = model.index(str(root))
shown = [model.fileName(model.index(r, 0, parent)) for r in range(model.rowCount(parent))]
check(shown and all(n.startswith("+") for n in shown),
      f"seuls les dossiers maitres sont listes ({shown})")

window.close()
pump(0.2)
print(f"\n{'ECHECS: ' + str(FAILS) if FAILS else 'tout est vert'}")
sys.exit(1 if FAILS else 0)
