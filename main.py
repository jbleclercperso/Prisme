"""Point d'entrée de Prisme."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from videosorter.config import ADOPTED, APP_NAME, Config
from videosorter.media import Tools
from videosorter.widgets import app_icon
from videosorter.window import MainWindow, check_tools


def main() -> int:
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


if __name__ == "__main__":
    sys.exit(main())
