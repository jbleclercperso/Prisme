"""Point d'entrée de VideoSorter."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from videosorter.config import Config
from videosorter.media import Tools
from videosorter.window import MainWindow, check_tools


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("VideoSorter")

    cfg = Config()
    Tools.resolve(cfg)

    window = MainWindow(cfg)
    window.show()

    if not check_tools(window):
        return 1

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
