"""Point d'entrée de Prisme.

Tout est enveloppé dans un rapport d'incident : une application sans console
qui échoue au démarrage ne dit rien du tout, et l'on reste devant un bureau
muet. Ici, la moindre erreur s'écrit dans « prisme-erreur.log », à côté du
programme, et une fenêtre la montre quand elle le peut.
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path


def _report_to() -> Path:
    """À côté du programme, sinon dans le dossier courant."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "prisme-erreur.log"
    return Path(__file__).resolve().parent / "prisme-erreur.log"


def _report(problem: BaseException) -> None:
    """Écrit ce qui s'est passé, puis tente de le dire à l'écran."""
    text = "".join(traceback.format_exception(problem))
    line = (f"--- {datetime.now():%d/%m %H:%M:%S} ---\n"
            f"Python {sys.version}\n"
            f"Programme : {sys.executable}\n"
            f"Gelé : {getattr(sys, 'frozen', False)}\n\n{text}\n")
    try:
        with open(_report_to(), "a", encoding="utf-8-sig") as out:
            out.write(line)
    except OSError:
        pass
    # Une fenêtre vaut mieux qu'un fichier qu'on ne pense pas à ouvrir — mais
    # si Qt est justement ce qui manque, il ne faut pas échouer une seconde fois.
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(
            None, "Prisme n'a pas pu démarrer",
            f"{type(problem).__name__} : {problem}\n\n"
            f"Le détail est dans :\n{_report_to()}")
    except Exception:                                  # noqa: BLE001
        print(line, file=sys.stderr)


def run() -> int:
    from PySide6.QtWidgets import QApplication

    from videosorter.config import ADOPTED, APP_NAME, Config
    from videosorter.media import Tools
    from videosorter.widgets import app_icon
    from videosorter.window import MainWindow, check_tools

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())

    cfg = Config()
    Tools.resolve(cfg)

    window = MainWindow(cfg)
    window.show()
    if ADOPTED:
        window.show_banner(
            f"Cache repris depuis {ADOPTED} : vignettes, index et réglages "
            "sont conservés.", "done")

    if not check_tools(window):
        return 1

    return app.exec()


def main() -> int:
    try:
        return run()
    except BaseException as problem:                   # noqa: BLE001
        # Y compris les erreurs d'import : c'est là que se logent les greffons
        # Qt manquants et les bibliothèques système absentes.
        if isinstance(problem, SystemExit):
            raise
        _report(problem)
        return 2


if __name__ == "__main__":
    sys.exit(main())
