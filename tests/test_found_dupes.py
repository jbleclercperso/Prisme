"""Doublons retenus d'une séance à l'autre, empreintes retrouvées après relance.

    python tests/test_found_dupes.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

BOX = os.path.join(tempfile.gettempdir(), "prisme_sandbox_dupes")
shutil.rmtree(BOX, ignore_errors=True)
os.environ["PRISME_SANDBOX"] = BOX
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from videosorter import config  # noqa: E402
from videosorter.dupes import SIG_METHOD, DupeGroup, sig_current  # noqa: E402
from videosorter.dupes_memory import FoundDupes  # noqa: E402
from videosorter.index import INDEX, Index  # noqa: E402

FAILS = []


def check(condition, label: str) -> None:
    print(("  ok   " if condition else "  ÉCHEC ") + label)
    if not condition:
        FAILS.append(label)


class _Stub:
    """Juste ce dont les methodes de la fenetre ont besoin ici."""

    def __init__(self):
        self.root = "X:\\Films"
        self.dupes = None
        self._pending_dupes = None
        self._pending_partial = False
        self.named = None
        self.banners = []

    def top_root(self):
        return self.root

    def _name_dupes_action(self, count=0, partial=False):
        self.named = (count, partial)

    def show_banner(self, text, kind="info", **_kw):
        self.banners.append((kind, text))


def main() -> int:
    check(str(config.APP_DIR) == BOX, "tout se passe dans le bac à sable")

    print("\n[1] Le résultat survit à la fermeture")
    store = FoundDupes()
    first = DupeGroup(["X:\\Films\\a.mkv", "X:\\Films\\copie\\a.mkv"],
                      [2_000_000, 1_500_000], [600.0, 600.0], sure=True)
    second = DupeGroup(["X:\\Films\\b.mkv", "X:\\Films\\b (2).mkv"],
                       [3_000_000, 3_000_000], [0.0, 0.0], sure=False)
    check(store.save([first, second], True, partial=True, root="X:\\Films"),
          "écrit sur le disque")
    check(store.path.parent == config.PRIVATE_DIR and store.path.exists(),
          "à côté des « pas des doublons »")
    found = FoundDupes().load()
    check(found is not None and len(found[0]) == 2, "relu par une autre instance")
    groups, by_image, partial = found
    check(by_image and partial, "par image, et marqué comme une étape")
    check([str(p) for p in groups[0].paths] == [str(p) for p in first.paths]
          and groups[0].sure and not groups[1].sure,
          "même ordre, le meilleur en tête, « sûr » et « à comparer » conservés")
    check(groups[0].sizes == first.sizes and groups[0].gain == first.gain,
          "tailles et place à récupérer conservées")

    print("\n[2] La fenêtre le retient, et le retrouve au lancement")
    from videosorter.window import MainWindow
    stub = _Stub()
    MainWindow._keep_found_dupes(stub, [first], False)
    check(stub._pending_dupes is not None and stub.named == (1, False),
          "le résultat final est en attente, l'entrée nommée")
    relaunched = _Stub()
    MainWindow._reload_found_dupes(relaunched)
    check(relaunched._pending_dupes is not None
          and len(relaunched._pending_dupes[0]) == 1
          and relaunched.named == (1, False),
          "après relance, « Afficher les doublons trouvés » revient")
    MainWindow._keep_found_dupes(stub, None, False)
    check(stub._pending_dupes is None and stub.named == (0, False),
          "« aucun doublon » remplace l'ancien résultat")
    empty = _Stub()
    MainWindow._reload_found_dupes(empty)
    check(empty._pending_dupes is None and store.path.exists(),
          "plus rien à recharger, et le fichier n'est pas effacé")

    print("\n[3] Un fichier abîmé est mis de côté, jamais effacé")
    store.path.write_text("{pas du json", encoding="utf-8")
    check(FoundDupes().load() is None, "illisible : rien n'est inventé")
    aside = [p for p in store.path.parent.iterdir() if ".abime-" in p.name]
    check(len(aside) == 1, "le fichier abîmé est gardé à côté")

    print("\n[4] Les empreintes survivent à une relance de l'index")
    path_db = os.path.join(BOX, "relance.db")
    first_run = Index(path_db)
    video = "\\\\NAS\\Volume 3\\Films\\c.mkv"
    first_run.put_sig(video, f"1000|500|{SIG_METHOD}", [0x0F0F0F0F0F0F0F0F, 0x3C3C3C3C3C3C3C3C], 500)
    first_run.close()
    second_run = Index(path_db)
    check(second_run.sig_stamp(video) == f"1000|500|{SIG_METHOD}",
          "la date de l'empreinte est relue")
    check(any(p == video and len(v) == 2 for p, v, _s in second_run.all_sigs()),
          "les valeurs aussi")
    check(second_run.sig_spelling("\\\\nas\\volume 3\\films\\C.MKV") == video,
          "retrouvée sous une autre casse")
    second_run.close()

    print("\n[5] Une empreinte vaut tant que le fichier n'a pas changé")
    INDEX.put_sig(video, f"1000|500|{SIG_METHOD}", [1, 2], 500)
    check(sig_current(video, "1000|500"), "même date, même taille")
    check(sig_current(video, "1002|500"), "deux secondes d'écart (FAT, NAS)")
    check(sig_current(video, "4600|500"), "une heure tout rond (changement d'heure)")
    check(not sig_current(video, "1000|501"), "une autre taille : à refaire")
    check(not sig_current(video, "1300|500"), "une autre date : à refaire")
    check(sig_current("\\\\nas\\VOLUME 3\\Films\\c.mkv", "1000|500"),
          "la même vidéo sous une autre casse n'est pas refaite")
    INDEX.forget_sig(video)
    check(not sig_current(video, "1000|500"), "oubliée : à refaire")

    INDEX.close()
    print("\n" + ("Tout est bon." if not FAILS else f"{len(FAILS)} échec(s)."))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
