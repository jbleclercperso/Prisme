"""Le code PIN : empreinte, attentes, cadenas au lancement et au retour du repli.

    python tests/test_lock.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

BOX = os.path.join(tempfile.gettempdir(), "prisme-test-lock")
shutil.rmtree(BOX, ignore_errors=True)
os.environ["PRISME_SANDBOX"] = BOX
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.stdout.reconfigure(encoding="utf-8")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from videosorter import lock  # noqa: E402
from videosorter.config import Config  # noqa: E402

FAILS = []


def check(condition, label: str) -> None:
    print(("  ok   " if condition else "  ÉCHEC ") + label)
    if not condition:
        FAILS.append(label)


def main() -> int:
    print("\n[1] L'empreinte et les essais")
    cfg = Config()
    check(not lock.pin_is_set(cfg), "sans code, rien n'est demandé")
    lock.set_pin(cfg, "4827")
    check(lock.pin_is_set(cfg) and "4827" not in str(cfg["pin_digest"])
          and len(cfg["pin_salt"]) == 32, "le code n'est gardé que sous forme d'empreinte salée")
    now = 1_000_000.0
    check(lock.check_pin(cfg, "4827", now) == (True, 0), "le bon code ouvre")
    for _ in range(4):
        lock.check_pin(cfg, "0000", now)
    good, wait = lock.check_pin(cfg, "0000", now)
    check(not good and wait == 30, "cinq erreurs : trente secondes d'attente")
    check(lock.check_pin(cfg, "4827", now + 10) == (False, 20),
          "même le bon code attend la fin de l'attente")
    again = Config()
    check(lock.wait_left(again, now + 10) == 20, "l'attente survit à une relance")
    for _ in range(5):
        _g, wait = lock.check_pin(cfg, "1111", now + 31)
    check(wait == 60, "la série suivante : le double")
    cfg["pin_failures"] = 5 * 20
    cfg["pin_wait_until"] = 0
    for _ in range(5):
        _g, wait = lock.check_pin(cfg, "2222", now + 1000)
    check(wait == 600, "jamais plus de dix minutes")
    cfg["pin_wait_until"] = 0
    check(lock.check_pin(cfg, "4827", now + 5000)[0] and cfg["pin_failures"] == 0,
          "le bon code remet le compte à zéro")

    print("\n[2] Dans la fenêtre")
    from videosorter.window import MainWindow, PAGE_LOCK, PAGE_QUIET
    from videosorter.quiet import QUIET_TITLE
    lock.set_pin(cfg, "4827")
    window = MainWindow(Config())
    window.show()
    app.processEvents()
    check(window.stack.currentIndex() == PAGE_LOCK and window.windowTitle() == QUIET_TITLE,
          "au lancement : le cadenas, sous un titre neutre")
    window.lock_page.field.setText("0000")
    app.processEvents()
    check(window.stack.currentIndex() == PAGE_LOCK and "incorrect" in window.lock_page.error.text(),
          "un mauvais code laisse le cadenas, et le dit")
    window.lock_page.field.setText("4827")
    app.processEvents()
    check(window.stack.currentIndex() not in (PAGE_LOCK, PAGE_QUIET) and not window._quiet,
          "le bon code ouvre Prisme")
    window.enter_quiet()
    app.processEvents()
    check(window.stack.currentIndex() == PAGE_QUIET, "Ctrl+K : la page neutre")
    window.leave_quiet()
    check(window.stack.currentIndex() == PAGE_LOCK, "en revenir passe par le cadenas")
    window.leave_quiet()
    check(window.stack.currentIndex() == PAGE_QUIET, "depuis le cadenas, Ctrl+K ramène à la page neutre")
    window.lock_page.hide_me.emit()
    check(window.stack.currentIndex() == PAGE_QUIET, "« Retour » aussi")
    window.leave_quiet()
    window.lock_page.field.setText("4827")
    app.processEvents()
    check(not window._quiet, "puis le bon code, et l'on est revenu")
    asked = []
    window._confirm_pin = lambda reason: asked.append(reason) or False
    shown = window.cfg["show_veiled"]
    window.toggle_veiled()
    check(asked and window.cfg["show_veiled"] == shown,
          "afficher les dossiers masqués redemande le code (refusé : rien ne change)")
    lock.set_pin(window.cfg, "")
    window.close()

    print("\n" + ("tout est vert" if not FAILS else f"{len(FAILS)} échec(s)"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
