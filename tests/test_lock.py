"""Le code PIN : le cadenas à l'ouverture, au retour du repli, et ses essais.

Lancement : python tests/test_lock.py
Aucune vidéo : l'interface en mode « offscreen », des réglages à part.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

SANDBOX = os.path.join(tempfile.gettempdir(), "prisme-tests-lock")
shutil.rmtree(SANDBOX, ignore_errors=True)
os.environ["PRISME_SANDBOX"] = SANDBOX
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from videosorter.config import Config  # noqa: E402
from videosorter.lock import (  # noqa: E402
    FIRST_WAIT, LONGEST_WAIT, hash_pin, pin_ok, valid_pin, wait_after,
)

FAILS: list = []


def check(condition, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


def settle(app, seconds: float = 0.3) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def type_pin(app, page, pin: str) -> None:
    for digit in pin:
        QTest.keyClick(page, getattr(Qt, f"Key_{digit}"))
    settle(app, 0.5)


def main() -> int:
    print("\n[1] L'empreinte, jamais le code")
    salt, digest = hash_pin("4821")
    check(pin_ok("4821", salt, digest), "le bon code est reconnu")
    check(not pin_ok("4822", salt, digest), "un autre code est refusé")
    check(not pin_ok("4821", "", ""), "sans code posé, rien n'ouvre")
    check("4821" not in salt + digest, "le code n'apparaît pas en clair")
    check(valid_pin("0007") and not valid_pin("123") and not valid_pin("12a4"),
          "quatre chiffres exactement")
    check([wait_after(n) for n in (1, 4, 5, 6, 10, 15)]
          == [0, 0, FIRST_WAIT, 0, FIRST_WAIT * 2, FIRST_WAIT * 4],
          "l'attente double à chaque série de cinq erreurs")
    check(wait_after(500) == LONGEST_WAIT, "et plafonne")

    app = QApplication(sys.argv)
    from videosorter.window import PAGE_LOCK, PAGE_QUIET, MainWindow

    print("\n[2] Sans code : le repli se quitte d'un geste")
    window = MainWindow(Config())
    window.show()
    settle(app)
    check(not window._quiet, "Prisme s'ouvre normalement")
    window.enter_quiet()
    window.leave_quiet()
    check(not window._quiet, "Échap ou Ctrl+K ramène directement")
    window.close()
    settle(app)

    print("\n[3] Code posé : Prisme s'ouvre sur le cadenas")
    cfg = Config()
    cfg["pin_salt"], cfg["pin_digest"] = hash_pin("4821")
    cfg.save()
    window = MainWindow(Config())
    window.show()
    settle(app)
    page = window.lock_page
    check(window._quiet, "rien de la collection ne se montre")
    check(window.stack.currentIndex() == PAGE_LOCK, "le cadenas est à l'écran")
    check(window.windowTitle() != "Prisme", "le titre de la fenêtre est neutre")

    print("\n[4] Un mauvais code ne fait rien entrer")
    type_pin(app, page, "1111")
    check(window._quiet and window.stack.currentIndex() == PAGE_LOCK,
          "toujours verrouillé")
    check(window.cfg["pin_failures"] == 1, "l'erreur est comptée")
    check(page.entry == "", "la saisie est effacée")

    print("\n[5] Échap ramène à la page neutre, pas à la collection")
    QTest.keyClick(page, Qt.Key_Escape)
    settle(app)
    check(window._quiet and window.stack.currentIndex() == PAGE_QUIET,
          "la page neutre revient")
    window.leave_quiet()
    check(window.stack.currentIndex() == PAGE_LOCK,
          "en sortir redemande le code")

    print("\n[6] Le bon code rouvre là où l'on était")
    type_pin(app, page, "4821")
    check(not window._quiet, "Prisme est déverrouillé")
    check(window.windowTitle() == "Prisme", "le titre revient")
    check(window.cfg["pin_failures"] == 0, "le compteur repart de zéro")

    print("\n[7] Ctrl+K puis retour : le cadenas encore")
    window.enter_quiet()
    window.leave_quiet()
    check(window._quiet and window.stack.currentIndex() == PAGE_LOCK,
          "le retour du repli passe par le code")
    type_pin(app, window.lock_page, "4821")
    check(not window._quiet, "et le bon code suffit")

    print("\n[8] Cinq erreurs : il faut attendre, même après un redémarrage")
    window.enter_quiet()
    window.leave_quiet()
    for _ in range(5):
        type_pin(app, window.lock_page, "0000")
    check(window.lock_page.waiting() > 0, "une attente est imposée")
    type_pin(app, window.lock_page, "4821")
    check(window._quiet, "même le bon code attend la fin du délai")
    window.close()
    settle(app)
    again = MainWindow(Config())
    again.show()
    settle(app)
    check(again._quiet and again.lock_page.waiting() > 0,
          "rouvrir Prisme ne remet pas le délai à zéro")
    again.cfg["pin_wait_until"] = 0
    again.lock_page.reset()
    type_pin(app, again.lock_page, "4821")
    check(not again._quiet, "le délai passé, le bon code ouvre")
    again.close()
    settle(app)

    print(f"\n{'ÉCHEC' if FAILS else 'Tout est bon'} "
          f"({len(FAILS)} échec(s))")
    for label in FAILS:
        print("  -", label)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
