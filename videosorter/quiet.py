"""L'écran de repli : ce que l'on montre quand on ne veut rien montrer.

Un geste, et Prisme disparaît derrière une page qui n'a rien à voir avec lui
— ni image, ni nom de fichier, ni titre qui rappelle ce qu'on faisait. Un
autre geste, et tout revient là où on l'avait laissé.

La page est délibérément ennuyeuse : un utilitaire d'indexation qui compte
des fichiers. Rien qui attire l'œil, rien qui invite à cliquer, rien qui
ressemble à un logiciel connu — c'est une page neutre, pas un déguisement.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

QUIET_TITLE = "Indexation des sauvegardes"

QUIET_STYLE = """
QWidget#quietPage { background: #f3f4f6; }
QLabel#quietHead { color: #1f2937; font-size: 16px; font-weight: 600; }
QLabel#quietLine { color: #4b5563; font-size: 13px; }
QLabel#quietFoot { color: #9ca3af; font-size: 11px; }
QProgressBar { background: #e5e7eb; border: 0; border-radius: 3px;
               height: 6px; text-align: center; color: transparent; }
QProgressBar::chunk { background: #9ca3af; border-radius: 3px; }
QTreeWidget { background: #ffffff; border: 1px solid #e5e7eb; color: #374151;
              font-size: 12px; }
QHeaderView::section { background: #f9fafb; color: #6b7280; border: 0;
                       border-bottom: 1px solid #e5e7eb; padding: 5px; }
QPushButton#quietBack { background: transparent; border: 0; color: #d1d5db;
                        font-size: 11px; padding: 2px 6px; }
QPushButton#quietBack:hover { color: #6b7280; }
"""

# Des noms de volumes et d'archives, sans rapport avec quoi que ce soit.
VOLUMES = ("Volume principal", "Archives 2023", "Archives 2024",
           "Documents partagés", "Sauvegarde système", "Modèles",
           "Correspondance", "Comptabilité", "Ressources")
KINDS = (".docx", ".xlsx", ".pdf", ".csv", ".zip", ".log", ".json")


class QuietPage(QWidget):
    """Une page qui ne dit rien de ce qu'on faisait.

    On en sort par tous les gestes qu'on tenterait : Échap, un double-clic
    n'importe où, le point en bas, ou le raccourci. Une page dont on ne sait
    plus sortir n'est pas discrète, elle est piégeuse.
    """

    leave = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("quietPage")
        # Sans cet attribut, le fond clair de la feuille de style ne se peint
        # pas sur un QWidget : la page restait sur le fond sombre de Prisme,
        # titre illisible -- l'air d'un utilitaire casse, pas d'un banal.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(QUIET_STYLE)

        box = QVBoxLayout(self)
        box.setContentsMargins(38, 32, 38, 24)
        box.setSpacing(12)

        head = QLabel(QUIET_TITLE, self)
        head.setObjectName("quietHead")
        box.addWidget(head)

        self.line = QLabel("", self)
        self.line.setObjectName("quietLine")
        box.addWidget(self.line)

        self.bar = QProgressBar(self)
        self.bar.setRange(0, 100)
        box.addWidget(self.bar)

        self.table = QTreeWidget(self)
        self.table.setHeaderLabels(["Volume", "Éléments", "Taille", "Dernier passage"])
        self.table.setRootIsDecorated(False)
        self.table.header().setSectionResizeMode(0, QHeaderView.Stretch)
        # Le tableau couvre les trois quarts de la page et gardait pour lui
        # les double-clics : « n'importe ou » ne valait que dans la marge. On
        # les intercepte. Ni selection ni focus : une page neutre ne garde
        # pas de ligne surlignee apres un clic.
        self._table_area = self.table.viewport()
        self._table_area.installEventFilter(self)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        box.addWidget(self.table, 1)

        foot = QHBoxLayout()
        self.foot = QLabel("", self)
        self.foot.setObjectName("quietFoot")
        foot.addWidget(self.foot, 1)
        # Discret jusqu'a l'effacement : c'est la sortie, et elle ne doit pas
        # se lire par-dessus l'epaule.
        self.back = QPushButton("·", self)
        self.back.setObjectName("quietBack")
        self.back.setToolTip("")
        self.back.setFocusPolicy(Qt.NoFocus)
        self.back.setFixedWidth(22)
        foot.addWidget(self.back, 0)
        box.addLayout(foot)

        self.step = 0
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self._tick)
        self.fill()

    def fill(self) -> None:
        """Repeuple la page — des chiffres plausibles, tirés au hasard."""
        self.table.clear()
        moment = datetime.now()
        for name in random.sample(VOLUMES, k=min(6, len(VOLUMES))):
            count = random.randint(400, 98000)
            size = random.uniform(0.4, 380.0)
            past = moment - timedelta(minutes=random.randint(3, 4000))
            QTreeWidgetItem(self.table, [
                name, f"{count:,}".replace(",", " "),
                f"{size:.1f} Go", past.strftime("%d/%m %H:%M")])
        for column in (1, 2, 3):
            self.table.resizeColumnToContents(column)
        self.step = random.randint(10, 70)
        self._tick()

    def _tick(self) -> None:
        self.step = (self.step + random.randint(1, 4)) % 100
        self.bar.setValue(self.step)
        kind = random.choice(KINDS)
        self.line.setText(
            f"Analyse en cours — {random.randint(120, 9800)} éléments "
            f"examinés, dernier type traité : {kind}")
        self.foot.setText(
            f"Prochain passage planifié à "
            f"{(datetime.now() + timedelta(hours=random.randint(1, 9))):%H:%M}")

    def start(self) -> None:
        self.fill()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def mouseDoubleClickEvent(self, event):
        self.leave.emit()
        event.accept()

    def eventFilter(self, watched, event):
        if (watched is self._table_area
                and event.type() == QEvent.Type.MouseButtonDblClick):
            self.leave.emit()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        # Echap seulement. F5 ramenait Prisme sans que rien ne l'annonce :
        # c'est la touche qu'un collegue presse pour « rafraichir » une page
        # qui ne l'interesse pas.
        if event.key() == Qt.Key_Escape:
            # Une repetition, c'est la touche encore tenue d'avant le repli :
            # elle ne doit pas en faire ressortir aussitot.
            if not event.isAutoRepeat():
                self.leave.emit()
            return
        super().keyPressEvent(event)
