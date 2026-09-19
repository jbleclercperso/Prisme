"""Mesure, sur une vraie racine, ce que coute chaque etape de l'analyse.

    python tests/bench_root.py X:\\

Repond a la question « est-ce l'application ou le disque ? » en separant les
trois temps qui composent une ouverture :

1. **l'inventaire** — une enumeration du dossier racine ;
2. **la restitution** — ce que l'index rend sans toucher au disque, c'est-a-dire
   ce que vous voyez a l'ecran des le lancement ;
3. **le parcours** — la traversee recursive des dossiers, la seule etape chere,
   mesuree sur un echantillon et extrapolee.

Aucune ecriture : la collection n'est pas modifiee, l'index non plus.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videosorter.index import INDEX                                # noqa: E402
from videosorter.media import is_network_path                      # noqa: E402
from videosorter.scan import (                                     # noqa: E402
    MODE_FOLDERS, cached_items, item_id_for, list_entries, scan_folder,
    signature,
)

SAMPLE = 12


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("VS_ROOT", "."))
    if not root.is_dir():
        print(f"{root} n'est pas un dossier.")
        return 1
    print(f"Racine  : {root}")
    print(f"Support : {'partage reseau' if is_network_path(root) else 'disque local'}")

    start = time.perf_counter()
    known = cached_items(root, MODE_FOLDERS, False)
    restore = time.perf_counter() - start
    print(f"\n1. Restitution depuis l'index : {restore:7.3f} s   "
          f"({len(known)} elements, aucune lecture disque)")
    if not known:
        print("   (rien d'indexe pour cette racine : la premiere analyse reste a faire)")

    start = time.perf_counter()
    paths = list_entries(root, MODE_FOLDERS, True, False, {}, None)
    inventory = time.perf_counter() - start
    print(f"2. Inventaire de la racine    : {inventory:7.3f} s   "
          f"({len(paths)} dossiers a trier)")

    ids = [item_id_for(path, MODE_FOLDERS, False) for path in paths]
    start = time.perf_counter()
    stored = INDEX.signatures(ids)
    check = time.perf_counter() - start
    fresh = sum(1 for path, key in zip(paths, ids)
                if stored.get(key) == signature(path))
    print(f"3. Verification des dates     : {check:7.3f} s   "
          f"({fresh}/{len(paths)} inchanges, donc a ne pas reparcourir)")

    todo = [path for path, key in zip(paths, ids)
            if stored.get(key) != signature(path)]
    if not todo:
        print("\nRien a reparcourir : une ouverture ne coute que les lignes 1 a 3.")
        print(f"Soit {restore + inventory + check:.2f} s au total.")
        return 0

    sample = todo[:SAMPLE]
    print(f"\n4. Parcours recursif, sur {len(sample)} dossiers de l'echantillon :")
    start = time.perf_counter()
    heaviest = (0.0, "")
    for path in sample:
        one = time.perf_counter()
        item = scan_folder(path)
        cost = time.perf_counter() - one
        heaviest = max(heaviest, (cost, path.name))
        print(f"   {cost:7.3f} s  {item.file_count:6d} fichiers  {path.name[:48]}")
    walk = time.perf_counter() - start
    average = walk / len(sample)
    print(f"\n   moyenne {average:.3f} s par dossier, le plus lourd "
          f"{heaviest[0]:.3f} s ({heaviest[1][:40]})")
    print(f"   soit environ {average * len(todo) / 8:.0f} s pour les {len(todo)} "
          f"dossiers a reparcourir, a huit de front.")
    print("\nC'est cette ligne 4, et elle seule, qui se paie a la premiere "
          "analyse.\nUne fois faite, une ouverture ne coute que les lignes 1 a 3.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
