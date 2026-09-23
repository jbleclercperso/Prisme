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


def _read_setting(name: str) -> bool:
    """Lit un reglage avant que Qt ne demarre.

    La mise a l'echelle se decide a la toute premiere ligne, bien avant que
    la configuration ne soit chargee comme il faut : on va donc la lire a la
    main, et l'on se passe d'elle si quoi que ce soit resiste.
    """
    import json
    try:
        from videosorter.config import CONFIG_PATH
        return bool(json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get(name))
    except Exception:                                  # noqa: BLE001
        return False


def run() -> int:
    import os

    # Avant toute chose : Qt fige sa politique d'echelle des sa creation.
    # A 200 %, l'application suit Windows et devient deux fois plus grande —
    # ce qui est juste sur un ecran lointain, mais fait deborder la fenetre
    # d'un petit ecran. Ce reglage lui fait ignorer l'agrandissement.
    if _read_setting("ignore_dpi"):
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ["QT_SCALE_FACTOR"] = "1"

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
