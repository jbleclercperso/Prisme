"""L'écran de repli : ce que l'on montre quand on ne veut rien montrer.

Un geste, et Prisme disparaît derrière une page qui n'a rien à voir avec lui
— ni image, ni nom de fichier, ni titre qui rappelle ce qu'on faisait. Un
autre geste, et tout revient là où on l'avait laissé.

Cinq écrans au choix (⋯ › Confidentialité › Écran de repli), chacun dans le
style de Windows 11 et chacun vivant -- un écran figé ne trompe personne :

- Windows Update : des mises à jour qui se téléchargent ;
- un tableur : un budget dont la cellule active se déplace ;
- une copie de fichiers : la fenêtre de copie, sa courbe de vitesse ;
- un document : un compte rendu qui s'écrit, curseur clignotant ;
- le Gestionnaire des tâches : l'onglet Performances, en sombre.

L'ancien écran (« Indexation des sauvegardes ») faisait daté sur Windows 11.
"""
from __future__ import annotations

import random
from collections import deque
from datetime import datetime, timedelta

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QProgressBar, QPushButton, QStackedLayout, QStyle, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

# Le titre par defaut (le premier ecran) : le cadenas du lancement s'en sert.
QUIET_TITLE = "Paramètres"

FONT = "'Segoe UI Variable Text', 'Segoe UI', sans-serif"
ICONS = "'Segoe Fluent Icons', 'Segoe MDL2 Assets'"
ACCENT = "#005fb8"


def _label(text: str, parent, style: str = "", name: str = "") -> QLabel:
    label = QLabel(text, parent)
    if style:
        label.setStyleSheet(f"QLabel {{ {style} }}")
    if name:
        label.setObjectName(name)
    return label


def _card(parent, style: str = "background: #ffffff; border: 1px solid #e5e5e5;"
          " border-radius: 8px;") -> QFrame:
    card = QFrame(parent)
    card.setAttribute(Qt.WA_StyledBackground, True)
    card.setStyleSheet(f"QFrame {{ {style} }} QLabel {{ border: 0; background: transparent; }}")
    return card


class _Graph(QWidget):
    """Une courbe qui défile, comme dans le Gestionnaire des tâches ou la
    fenêtre de copie : la dernière valeur à droite."""

    def __init__(self, parent, line: str, fill: str, grid: str, back: str,
                 points: int = 60, border: str = ""):
        super().__init__(parent)
        self.values = deque([0.0] * points, maxlen=points)
        self.line, self.fill, self.grid, self.back = (QColor(line), QColor(fill),
                                                      QColor(grid), QColor(back))
        self.border = QColor(border or line)

    def push(self, value: float) -> None:
        self.values.append(max(0.0, min(1.0, value)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.fillRect(rect, self.back)
        painter.setPen(QPen(self.grid, 1))
        for i in range(1, 10):
            x = rect.left() + rect.width() * i / 10
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for i in range(1, 10):
            y = rect.top() + rect.height() * i / 10
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        count = len(self.values)
        path = QPainterPath()
        for i, value in enumerate(self.values):
            x = rect.left() + rect.width() * i / max(1, count - 1)
            y = rect.bottom() - rect.height() * value
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        area = QPainterPath(path)
        area.lineTo(rect.right(), rect.bottom())
        area.lineTo(rect.left(), rect.bottom())
        area.closeSubpath()
        painter.fillPath(area, self.fill)
        painter.setPen(QPen(self.line, 1.4))
        painter.drawPath(path)
        painter.setPen(QPen(self.border, 1))
        painter.drawRect(rect)
        painter.end()


# ---------------------------------------------------------------------------
# 1. Windows Update
# ---------------------------------------------------------------------------
class UpdateCover(QWidget):
    TITLE = "Paramètres"
    ICON = QStyle.SP_BrowserReload
    NAV = (("", "Accueil"), ("", "Système"), ("", "Bluetooth et appareils"),
           ("", "Réseau et Internet"), ("", "Personnalisation"),
           ("", "Applications"), ("", "Comptes"), ("", "Heure et langue"),
           ("", "Jeux"), ("", "Accessibilité"),
           ("", "Confidentialité et sécurité"), ("", "Windows Update"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget {{ background: #f3f3f3; font-family: {FONT}; color: #1a1a1a; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        nav = QWidget(self)
        nav.setFixedWidth(290)
        box = QVBoxLayout(nav)
        box.setContentsMargins(16, 18, 8, 16)
        box.setSpacing(2)
        who = QHBoxLayout()
        avatar = _label("M", nav, "background: #c7d9ef; color: #1a3a5f; border-radius: 30px;"
                        " font-size: 22px; font-weight: 600; qproperty-alignment: AlignCenter;")
        avatar.setFixedSize(60, 60)
        who.addWidget(avatar)
        names = QVBoxLayout()
        names.addWidget(_label("Utilisateur", nav, "font-size: 14px; font-weight: 600;"))
        names.addWidget(_label("Compte local", nav, "font-size: 12px; color: #5f5f5f;"))
        who.addLayout(names, 1)
        box.addLayout(who)
        box.addSpacing(14)
        search = _label("Rechercher un paramètre", nav,
                        "background: #ffffff; border: 1px solid #e0e0e0; border-bottom: 1px solid #8a8a8a;"
                        " border-radius: 4px; color: #6b6b6b; font-size: 13px; padding: 6px 10px;")
        box.addWidget(search)
        box.addSpacing(10)
        for glyph, text in self.NAV:
            on = text == "Windows Update"
            item = QWidget(nav)
            item.setAttribute(Qt.WA_StyledBackground, True)
            item.setStyleSheet("QWidget { background: %s; border-radius: 4px; }"
                               % ("#e8e8e8" if on else "transparent"))
            line = QHBoxLayout(item)
            line.setContentsMargins(12, 7, 8, 7)
            line.setSpacing(12)
            mark = _label("", item, f"background: {ACCENT if on else 'transparent'}; border-radius: 1px;")
            mark.setFixedSize(3, 16)
            line.addWidget(mark)
            icon = _label(glyph, item, f"font-family: {ICONS}; font-size: 15px; color: #1a1a1a;")
            icon.setFixedWidth(18)
            line.addWidget(icon)
            line.addWidget(_label(text, item, "font-size: 13px;"), 1)
            box.addWidget(item)
        box.addStretch(1)
        row.addWidget(nav)

        main = QWidget(self)
        body = QVBoxLayout(main)
        body.setContentsMargins(26, 22, 34, 22)
        body.setSpacing(8)
        body.addWidget(_label("Windows Update", main, "font-size: 28px; font-weight: 600;"))
        body.addSpacing(10)
        head = _card(main)
        line = QHBoxLayout(head)
        line.setContentsMargins(18, 16, 18, 16)
        line.setSpacing(16)
        line.addWidget(_label("", head, f"font-family: {ICONS}; font-size: 38px; color: {ACCENT};"))
        words = QVBoxLayout()
        words.addWidget(_label("Mises à jour disponibles", head, "font-size: 20px; font-weight: 600;"))
        self.checked = _label("", head, "font-size: 12px; color: #5f5f5f;")
        words.addWidget(self.checked)
        line.addLayout(words, 1)
        pause = _label("Suspendre pendant 1 semaine", head,
                       "background: #fbfbfb; border: 1px solid #e0e0e0; border-radius: 4px;"
                       " padding: 6px 12px; font-size: 13px;")
        line.addWidget(pause)
        body.addWidget(head)

        self.rows = []
        for name in (
                "2026-10 Mise à jour cumulative pour Windows 11 Version 24H2 pour les systèmes x64 (KB5066835)",
                "Mise à jour de la plateforme anti-programme malveillant Microsoft Defender - KB4052623 (version 4.18.25080.5)",
                "2026-10 Mise à jour cumulative pour .NET Framework 3.5 et 4.8.1 pour Windows 11, version 24H2 (KB5066131)",
                "Mise à jour de la pile de maintenance pour Windows 11 Version 24H2 (KB5068221)"):
            card = _card(main)
            grid = QGridLayout(card)
            grid.setContentsMargins(18, 12, 18, 14)
            grid.setHorizontalSpacing(18)
            title = _label(name, card, "font-size: 13px;")
            title.setWordWrap(True)
            grid.addWidget(title, 0, 0)
            state = _label("", card, "font-size: 12px; color: #5f5f5f;")
            grid.addWidget(state, 0, 1, Qt.AlignRight | Qt.AlignTop)
            bar = QProgressBar(card)
            bar.setRange(0, 1000)
            bar.setTextVisible(False)
            bar.setFixedHeight(4)
            bar.setStyleSheet(f"QProgressBar {{ background: #e6e6e6; border: 0; border-radius: 2px; }}"
                              f" QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}")
            grid.addWidget(bar, 1, 0, 1, 2)
            grid.setColumnStretch(0, 1)
            body.addWidget(card)
            self.rows.append({"state": state, "bar": bar, "phase": 0, "value": 0.0})
        body.addSpacing(12)
        body.addWidget(_label("Autres options", main, "font-size: 14px; font-weight: 600;"))
        for glyph, text, hint in (("", "Suspendre les mises à jour", ""),
                                  ("", "Historique des mises à jour", ""),
                                  ("", "Options avancées",
                                   "Optimisation de la distribution, mises à jour facultatives")):
            card = _card(main)
            line = QHBoxLayout(card)
            line.setContentsMargins(18, 12, 18, 12)
            line.setSpacing(16)
            line.addWidget(_label(glyph, card, f"font-family: {ICONS}; font-size: 18px;"))
            words = QVBoxLayout()
            words.setSpacing(0)
            words.addWidget(_label(text, card, "font-size: 13px;"))
            if hint:
                words.addWidget(_label(hint, card, "font-size: 12px; color: #5f5f5f;"))
            line.addLayout(words, 1)
            line.addWidget(_label("", card, f"font-family: {ICONS}; font-size: 12px; color: #5f5f5f;"))
            body.addWidget(card)
        body.addStretch(1)
        row.addWidget(main, 1)
        self.reset()

    def reset(self) -> None:
        moment = datetime.now() - timedelta(minutes=random.randint(2, 40))
        self.checked.setText(f"Dernière vérification : aujourd'hui, {moment:%H:%M}")
        for i, row in enumerate(self.rows):
            row["phase"] = 0 if i < 2 else 2
            row["value"] = random.uniform(5, 60) if i < 2 else 0.0
        self.tick()

    def tick(self) -> None:
        for i, row in enumerate(self.rows):
            if row["phase"] == 2:
                row["state"].setText("En attente du téléchargement")
                row["bar"].setValue(0)
                continue
            # Chaque mise a jour a son rythme : deux barres au meme pourcentage
            # se remarquaient.
            speed = 1.7 if i == 0 else 0.7
            row["value"] += random.uniform(0.0, speed) * (0.4 if row["phase"] else 1.0)
            if row["value"] >= 100:
                row["value"] = 0.0
                row["phase"] = 1 - row["phase"]
            verb = "Téléchargement" if row["phase"] == 0 else "Installation"
            row["state"].setText(f"{verb} - {int(row['value'])} %")
            row["bar"].setValue(int(row["value"] * 10))


# ---------------------------------------------------------------------------
# 2. Un tableur
# ---------------------------------------------------------------------------
class SheetCover(QWidget):
    TITLE = "Budget 2026.xlsx"
    ICON = QStyle.SP_FileDialogDetailedView
    MONTHS = ("Janvier", "Février", "Mars", "Avril", "Mai", "Juin", "Juillet",
              "Août", "Septembre", "Octobre", "Novembre", "Décembre")
    HEADERS = ("Mois", "Loyer", "Énergie", "Courses", "Transport", "Assurances",
               "Loisirs", "Total", "Épargne")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget {{ background: #ffffff; font-family: {FONT}; color: #1a1a1a; }}")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        tabs = QWidget(self)
        tabs.setAttribute(Qt.WA_StyledBackground, True)
        tabs.setStyleSheet("QWidget { background: #f3f3f3; }")
        line = QHBoxLayout(tabs)
        line.setContentsMargins(14, 6, 14, 0)
        line.setSpacing(22)
        for i, text in enumerate(("Fichier", "Accueil", "Insertion", "Mise en page", "Formules",
                                  "Données", "Révision", "Affichage", "Aide")):
            line.addWidget(_label(text, tabs, "font-size: 13px; padding-bottom: 6px;"
                                  + (" border-bottom: 3px solid #107c41; font-weight: 600;" if i == 1 else "")))
        line.addStretch(1)
        box.addWidget(tabs)

        ribbon = QWidget(self)
        ribbon.setAttribute(Qt.WA_StyledBackground, True)
        ribbon.setStyleSheet("QWidget { background: #f9f9f9; border-bottom: 1px solid #e1e1e1; }")
        ribbon.setFixedHeight(52)
        line = QHBoxLayout(ribbon)
        line.setContentsMargins(14, 6, 14, 6)
        line.setSpacing(16)
        for glyph in ("", "", "", "", "", "",
                      "", "", "", "", "", ""):
            line.addWidget(_label(glyph, ribbon, f"font-family: {ICONS}; font-size: 16px; color: #3b3b3b;"
                                  " border: 0; background: transparent;"))
        line.addWidget(_label("Calibri", ribbon, "font-size: 12px; border: 1px solid #d0d0d0; padding: 3px 26px 3px 6px;"
                              " background: #ffffff;"))
        line.addWidget(_label("11", ribbon, "font-size: 12px; border: 1px solid #d0d0d0; padding: 3px 10px;"
                              " background: #ffffff;"))
        line.addStretch(1)
        box.addWidget(ribbon)

        formula = QHBoxLayout()
        formula.setContentsMargins(8, 4, 8, 4)
        formula.setSpacing(8)
        self.cell_name = _label("A1", self, "font-size: 12px; border: 1px solid #d0d0d0; padding: 3px 8px;")
        self.cell_name.setFixedWidth(80)
        formula.addWidget(self.cell_name)
        formula.addWidget(_label("fx", self, "font-size: 13px; font-style: italic; color: #5f5f5f;"))
        self.formula = _label("", self, "font-size: 12px; border: 1px solid #d0d0d0; padding: 3px 8px;")
        formula.addWidget(self.formula, 1)
        box.addLayout(formula)

        self.table = QTableWidget(32, 12, self)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setHorizontalHeaderLabels([chr(65 + i) for i in range(12)])
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.horizontalHeader().setDefaultSectionSize(104)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.setStyleSheet(
            "QTableWidget { gridline-color: #e1e1e1; font-size: 12px; border: 0;"
            " selection-background-color: #ffffff; selection-color: #1a1a1a; }"
            "QTableWidget::item:selected { border: 2px solid #107c41; }"
            "QHeaderView::section { background: #f3f3f3; color: #444; border: 0;"
            " border-right: 1px solid #e1e1e1; border-bottom: 1px solid #e1e1e1; padding: 2px; }")
        box.addWidget(self.table, 1)

        foot = QWidget(self)
        foot.setAttribute(Qt.WA_StyledBackground, True)
        foot.setStyleSheet("QWidget { background: #f3f3f3; border-top: 1px solid #e1e1e1; }")
        line = QHBoxLayout(foot)
        line.setContentsMargins(10, 3, 14, 3)
        line.setSpacing(4)
        for i, name in enumerate(("Budget", "Épargne", "Factures", "Notes")):
            line.addWidget(_label(name, foot, "font-size: 12px; padding: 3px 12px; border: 0;"
                                  + (" background: #ffffff; color: #107c41; font-weight: 600;"
                                     " border-bottom: 2px solid #107c41;" if i == 0 else "")))
        line.addStretch(1)
        self.status = _label("", foot, "font-size: 11px; color: #444; border: 0;")
        line.addWidget(self.status)
        box.addWidget(foot)
        self.fill()

    def fill(self) -> None:
        bold = QFont()
        bold.setBold(True)
        for column, text in enumerate(self.HEADERS):
            item = QTableWidgetItem(text)
            item.setFont(bold)
            self.table.setItem(0, column, item)
        self.data = []
        for row, month in enumerate(self.MONTHS, start=1):
            values = [850.0, random.uniform(70, 160), random.uniform(380, 560),
                      random.uniform(90, 220), 96.4, random.uniform(40, 260)]
            self.data.append(values)
            self.table.setItem(row, 0, QTableWidgetItem(month))
            self._write_row(row)
        self.pos = (random.randint(1, 12), random.randint(1, 6))
        self.tick()

    @staticmethod
    def _money(value: float) -> str:
        return f"{value:,.2f} €".replace(",", " ").replace(".", ",")

    def _write_row(self, row: int) -> None:
        values = self.data[row - 1]
        for column, value in enumerate(values, start=1):
            item = QTableWidgetItem(self._money(value))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, column, item)
        total = sum(values)
        for column, value in ((7, total), (8, 2400 - total)):
            item = QTableWidgetItem(self._money(value))
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, column, item)

    def tick(self) -> None:
        if random.random() < 0.55:
            row, column = self.pos
            row = max(1, min(12, row + random.choice((-1, 0, 1, 1))))
            column = max(1, min(6, column + random.choice((-1, 0, 1))))
            if random.random() < 0.35:
                self.data[row - 1][column - 1] = round(
                    self.data[row - 1][column - 1] * random.uniform(0.94, 1.06), 2)
                self._write_row(row)
            self.pos = (row, column)
        row, column = self.pos
        self.table.setCurrentCell(row, column)
        self.cell_name.setText(f"{chr(65 + column)}{row + 1}")
        self.formula.setText(f"{self.data[row - 1][column - 1]:.2f}".replace(".", ","))
        values = [v for line in self.data for v in line]
        self.status.setText(f"Prêt      Moyenne : {self._money(sum(values) / len(values))}"
                            f"      Nombre : {len(values)}      Somme : {self._money(sum(values))}")


# ---------------------------------------------------------------------------
# 3. Une copie de fichiers
# ---------------------------------------------------------------------------
class CopyCover(QWidget):
    TITLE = "Copie en cours"
    ICON = QStyle.SP_DirIcon

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget {{ background: #f3f3f3; font-family: {FONT}; color: #1a1a1a; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        card = _card(self, "background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px;")
        card.setFixedWidth(620)
        box = QVBoxLayout(card)
        box.setContentsMargins(24, 20, 24, 22)
        box.setSpacing(10)
        self.head = _label("", card, "font-size: 13px; color: #1a1a1a;")
        self.head.setWordWrap(True)
        box.addWidget(self.head)
        line = QHBoxLayout()
        self.percent = _label("", card, "font-size: 22px; font-weight: 600;")
        line.addWidget(self.percent, 1)
        for glyph in ("", ""):
            line.addWidget(_label(glyph, card, f"font-family: {ICONS}; font-size: 14px; color: #3b3b3b;"
                                  " padding: 6px 8px; border: 1px solid #e5e5e5; border-radius: 4px;"))
        box.addLayout(line)
        self.graph = _Graph(card, line="#06b025", fill="#9be6a5", grid="#e9f7ea",
                            back="#f7fdf7", points=80, border="#bfe6c4")
        self.graph.setFixedHeight(110)
        box.addWidget(self.graph)
        self.speed = _label("", card, "font-size: 12px; color: #1a1a1a;")
        box.addWidget(self.speed)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.fields = {}
        for i, key in enumerate(("Nom :", "Temps restant :", "Éléments restants :")):
            grid.addWidget(_label(key, card, "font-size: 12px; color: #5f5f5f;"), i, 0)
            self.fields[key] = _label("", card, "font-size: 12px;")
            grid.addWidget(self.fields[key], i, 1)
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        box.addWidget(_label("  Moins de détails", card,
                             f"font-family: {ICONS}, {FONT}; font-size: 12px; color: #3b3b3b;"))
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)
        self.reset()

    def reset(self) -> None:
        self.total = random.randint(8000, 16000)
        self.done = int(self.total * random.uniform(0.2, 0.6))
        self.size_total = random.uniform(120, 360)
        self.rate = random.uniform(70, 120)
        self.head.setText(f"Copie de {self.total:,} éléments de Photos vers Sauvegarde (E:)".replace(",", " "))
        for _ in range(80):
            self.graph.push(random.uniform(0.45, 0.8))
        self.tick()

    def tick(self) -> None:
        self.rate = max(25.0, min(160.0, self.rate + random.uniform(-9, 9)))
        self.graph.push(self.rate / 170)
        self.done = min(self.total - 1, self.done + random.randint(1, 9))
        share = self.done / self.total
        self.percent.setText(f"{int(share * 100)} % terminé")
        self.speed.setText(f"Vitesse : {self.rate:.0f} Mo/s".replace(".", ","))
        left = self.total - self.done
        left_size = self.size_total * (1 - share)
        minutes = max(1, int(left_size * 1024 / self.rate / 60))
        self.fields["Nom :"].setText(
            f"IMG_{datetime.now():%Y%m%d}_{random.randint(100000, 235959)}.jpg")
        self.fields["Temps restant :"].setText(f"Environ {minutes} minute{'s' if minutes > 1 else ''}")
        self.fields["Éléments restants :"].setText(
            f"{left:,} ({left_size:.1f} Go)".replace(",", " ").replace(".", ","))
        self.window_title = f"{int(share * 100)} % terminé"


# ---------------------------------------------------------------------------
# 4. Un document
# ---------------------------------------------------------------------------
class DocCover(QWidget):
    TITLE = "Compte rendu de réunion.docx"
    ICON = QStyle.SP_FileIcon
    PARAGRAPHS = (
        "Présents : C. Martin, S. Leroy, A. Benali, J. Dupont. Excusée : M. Garnier.",
        "1. Point d'avancement. Le calendrier du trimestre est tenu dans l'ensemble. "
        "Deux livrables glissent d'une semaine en raison des congés ; l'équipe propose "
        "de décaler la revue intermédiaire au jeudi suivant, ce qui ne change pas la date finale.",
        "2. Budget. Les dépenses engagées représentent 64 % de l'enveloppe annuelle. "
        "Le poste déplacements reste en dessous des prévisions ; le poste prestations "
        "externes devra être surveillé jusqu'à la fin de l'exercice.",
        "3. Organisation. La permanence de décembre sera assurée par roulement. "
        "Chacun indique ses disponibilités dans le tableau partagé avant vendredi.",
    )
    TYPING = ("4. Questions diverses. La prochaine réunion est fixée au mardi 4 novembre à 10 h. "
              "Les supports de présentation seront envoyés la veille. Il est rappelé que les "
              "demandes de matériel doivent passer par le formulaire habituel, et que les "
              "comptes rendus sont archivés dans le dossier commun du service. ")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget {{ background: #e9e9e9; font-family: {FONT}; color: #1a1a1a; }}")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        tabs = QWidget(self)
        tabs.setAttribute(Qt.WA_StyledBackground, True)
        tabs.setStyleSheet("QWidget { background: #f3f3f3; border-bottom: 1px solid #e1e1e1; }")
        line = QHBoxLayout(tabs)
        line.setContentsMargins(14, 8, 14, 8)
        line.setSpacing(22)
        for i, text in enumerate(("Fichier", "Accueil", "Insertion", "Dessin", "Création",
                                  "Mise en page", "Références", "Révision", "Affichage")):
            line.addWidget(_label(text, tabs, "font-size: 13px; border: 0;"
                                  + (" color: #185abd; font-weight: 600;" if i == 1 else "")))
        line.addStretch(1)
        box.addWidget(tabs)

        canvas = QHBoxLayout()
        canvas.setContentsMargins(20, 24, 20, 0)
        canvas.addStretch(1)
        page = QFrame(self)
        page.setAttribute(Qt.WA_StyledBackground, True)
        page.setStyleSheet("QFrame { background: #ffffff; border: 1px solid #d6d6d6; }"
                           " QLabel { border: 0; background: transparent; }")
        page.setFixedWidth(680)
        text = QVBoxLayout(page)
        text.setContentsMargins(72, 64, 72, 40)
        text.setSpacing(14)
        text.addWidget(_label("Compte rendu de réunion", page,
                              "font-family: 'Aptos Display', 'Calibri Light', 'Segoe UI'; font-size: 24px; color: #0f4761;"))
        text.addWidget(_label(f"Réunion de service du {datetime.now():%d/%m/%Y}", page,
                              "font-size: 12px; color: #5f5f5f;"))
        for paragraph in self.PARAGRAPHS:
            label = _label(paragraph, page, "font-family: 'Aptos', 'Calibri', 'Segoe UI'; font-size: 13px; line-height: 150%;")
            label.setWordWrap(True)
            text.addWidget(label)
        self.live = _label("", page, "font-family: 'Aptos', 'Calibri', 'Segoe UI'; font-size: 13px;")
        self.live.setWordWrap(True)
        self.live.setTextFormat(Qt.RichText)
        text.addWidget(self.live)
        text.addStretch(1)
        canvas.addWidget(page)
        canvas.addStretch(1)
        box.addLayout(canvas, 1)

        foot = QWidget(self)
        foot.setAttribute(Qt.WA_StyledBackground, True)
        foot.setStyleSheet("QWidget { background: #f3f3f3; border-top: 1px solid #e1e1e1; }")
        line = QHBoxLayout(foot)
        line.setContentsMargins(14, 3, 14, 3)
        self.words = _label("", foot, "font-size: 11px; color: #444; border: 0;")
        line.addWidget(self.words)
        line.addStretch(1)
        line.addWidget(_label("Focus      100 %", foot, "font-size: 11px; color: #444; border: 0;"))
        box.addWidget(foot)
        self.reset()

    def reset(self) -> None:
        self.typed = random.randint(10, 60)
        self.blink = False
        self.tick()

    def tick(self) -> None:
        if random.random() < 0.7:
            self.typed += random.randint(1, 4)
        if self.typed >= len(self.TYPING):
            self.typed = 12
        self.blink = not self.blink
        caret = "<span style='color:#1a1a1a'>|</span>" if self.blink else "<span style='color:#ffffff'>|</span>"
        shown = self.TYPING[:self.typed].replace("&", "&amp;").replace("<", "&lt;")
        self.live.setText(shown + caret)
        words = sum(len(p.split()) for p in self.PARAGRAPHS) + len(self.TYPING[:self.typed].split())
        self.words.setText(f"Page 1 sur 2      {words} mots      Français (France)")


# ---------------------------------------------------------------------------
# 5. Le Gestionnaire des tâches
# ---------------------------------------------------------------------------
class TaskCover(QWidget):
    TITLE = "Gestionnaire des tâches"
    ICON = QStyle.SP_ComputerIcon
    SIDES = (("Processeur", "#2f96f3"), ("Mémoire", "#b05fcf"), ("Disque 0 (C:)", "#4cb050"),
             ("Ethernet", "#d18a2c"), ("GPU 0", "#2f96f3"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget {{ background: #202020; font-family: {FONT}; color: #ffffff; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        rail = QWidget(self)
        rail.setFixedWidth(48)
        rail.setAttribute(Qt.WA_StyledBackground, True)
        rail.setStyleSheet("QWidget { background: #1c1c1c; }")
        line = QVBoxLayout(rail)
        line.setContentsMargins(0, 14, 0, 14)
        line.setSpacing(18)
        for i, glyph in enumerate(("", "", "", "", "", "", "")):
            line.addWidget(_label(glyph, rail, f"font-family: {ICONS}; font-size: 16px; qproperty-alignment: AlignCenter;"
                                  + (" color: #60cdff;" if i == 2 else " color: #d0d0d0;")))
        line.addStretch(1)
        line.addWidget(_label("", rail, f"font-family: {ICONS}; font-size: 16px; color: #d0d0d0;"
                              " qproperty-alignment: AlignCenter;"))
        row.addWidget(rail)

        side = QWidget(self)
        side.setFixedWidth(250)
        box = QVBoxLayout(side)
        box.setContentsMargins(10, 14, 6, 14)
        box.setSpacing(4)
        box.addWidget(_label("Performances", side, "font-size: 18px; font-weight: 600; padding: 0 6px 10px 6px;"))
        self.side_values = []
        self.minis = []
        for i, (name, color) in enumerate(self.SIDES):
            item = QWidget(side)
            item.setAttribute(Qt.WA_StyledBackground, True)
            item.setStyleSheet("QWidget { background: %s; border-radius: 4px; }" % ("#2d2d2d" if i == 0 else "transparent"))
            grid = QHBoxLayout(item)
            grid.setContentsMargins(8, 8, 8, 8)
            grid.setSpacing(10)
            mini = _Graph(item, line=color, fill=QColor(color).darker(260).name(), grid="#202020",
                          back="#151515", points=30)
            mini.setFixedSize(70, 44)
            grid.addWidget(mini)
            words = QVBoxLayout()
            words.setSpacing(1)
            words.addWidget(_label(name, item, "font-size: 13px; background: transparent;"))
            value = _label("", item, "font-size: 11px; color: #c5c5c5; background: transparent;")
            words.addWidget(value)
            grid.addLayout(words, 1)
            box.addWidget(item)
            self.side_values.append(value)
            self.minis.append(mini)
        box.addStretch(1)
        row.addWidget(side)

        main = QWidget(self)
        body = QVBoxLayout(main)
        body.setContentsMargins(22, 18, 26, 18)
        body.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(_label("Processeur", main, "font-size: 26px; font-weight: 600;"), 1)
        head.addWidget(_label("Processeur 8 cœurs, 16 threads", main, "font-size: 14px; color: #d6d6d6;"))
        body.addLayout(head)
        legend = QHBoxLayout()
        legend.addWidget(_label("% d'utilisation sur 60 secondes", main, "font-size: 11px; color: #c5c5c5;"), 1)
        legend.addWidget(_label("100 %", main, "font-size: 11px; color: #c5c5c5;"))
        body.addLayout(legend)
        self.graph = _Graph(main, line="#2f96f3", fill="#14324d", grid="#163049",
                            back="#0f1720", points=60, border="#2f96f3")
        body.addWidget(self.graph, 1)
        body.addWidget(_label("0", main, "font-size: 11px; color: #c5c5c5; qproperty-alignment: AlignRight;"))
        stats = QGridLayout()
        stats.setHorizontalSpacing(34)
        stats.setVerticalSpacing(2)
        self.stats = {}
        for i, key in enumerate(("Utilisation", "Vitesse", "Processus", "Threads", "Handles",
                                 "Temps d'activité")):
            column, row_at = (i % 3), (i // 3) * 2
            stats.addWidget(_label(key, main, "font-size: 11px; color: #c5c5c5;"), row_at, column)
            self.stats[key] = _label("", main, "font-size: 20px;")
            stats.addWidget(self.stats[key], row_at + 1, column)
        body.addLayout(stats)
        row.addWidget(main, 1)
        self.reset()

    def reset(self) -> None:
        self.cpu = random.uniform(0.06, 0.18)
        self.levels = [random.uniform(0.05, 0.2), random.uniform(0.45, 0.62),
                       random.uniform(0.0, 0.05), random.uniform(0.0, 0.08), random.uniform(0.0, 0.1)]
        self.started = datetime.now() - timedelta(days=random.randint(0, 6),
                                                  hours=random.randint(0, 23),
                                                  minutes=random.randint(0, 59))
        self.processes = random.randint(180, 260)
        for _ in range(60):
            self._step()
        self.tick()

    def _step(self) -> None:
        # Un ordinateur au repos : autour de 10 %, avec de petits pics qui
        # retombent d'eux-memes.
        self.cpu += (0.11 - self.cpu) * 0.18 + random.uniform(-0.035, 0.035)
        if random.random() < 0.04:
            self.cpu += random.uniform(0.08, 0.2)
        self.cpu = max(0.02, min(0.6, self.cpu))
        self.graph.push(self.cpu)
        self.levels[0] = self.cpu
        self.levels[1] = max(0.4, min(0.7, self.levels[1] + random.uniform(-0.004, 0.004)))
        self.levels[2] = max(0.0, min(0.4, random.uniform(0.0, 0.06) if random.random() < 0.9 else 0.3))
        self.levels[3] = max(0.0, min(0.3, random.uniform(0.0, 0.08)))
        self.levels[4] = max(0.0, min(0.5, self.levels[4] + random.uniform(-0.03, 0.03)))
        for mini, level in zip(self.minis, self.levels):
            mini.push(level)

    def tick(self) -> None:
        self._step()
        self.processes = max(150, self.processes + random.randint(-2, 2))
        texts = (f"{int(self.levels[0] * 100)} %  {2.4 + self.levels[0]:.2f} GHz",
                 f"{self.levels[1] * 16:.1f}/15,7 Go ({int(self.levels[1] * 100)} %)",
                 f"SSD\n{int(self.levels[2] * 100)} %",
                 f"Ethernet\nE : {random.randint(0, 80)} R : {random.randint(0, 900)} Kbit/s",
                 f"Intégré\n{int(self.levels[4] * 100)} % ({random.randint(38, 49)} °C)")
        for label, text in zip(self.side_values, texts):
            label.setText(text.replace(".", ","))
        up = datetime.now() - self.started
        hours, rest = divmod(up.seconds, 3600)
        self.stats["Utilisation"].setText(f"{int(self.cpu * 100)} %")
        self.stats["Vitesse"].setText(f"{2.4 + self.cpu:.2f} GHz".replace(".", ","))
        self.stats["Processus"].setText(str(self.processes))
        self.stats["Threads"].setText(f"{self.processes * 14 + random.randint(0, 40):,}".replace(",", " "))
        self.stats["Handles"].setText(f"{self.processes * 420 + random.randint(0, 900):,}".replace(",", " "))
        self.stats["Temps d'activité"].setText(f"{up.days}:{hours:02d}:{rest // 60:02d}:{rest % 60:02d}")


# Les cinq écrans, dans l'ordre du menu : (clé, libellé, classe).
QUIET_COVERS = (
    ("update", "Windows Update", UpdateCover),
    ("sheet", "Tableur (budget)", SheetCover),
    ("copy", "Copie de fichiers", CopyCover),
    ("doc", "Document en cours", DocCover),
    ("tasks", "Gestionnaire des tâches", TaskCover),
)
COVER_KEYS = tuple(key for key, _label, _cls in QUIET_COVERS)


class QuietPage(QWidget):
    """Une page qui ne dit rien de ce qu'on faisait.

    On en sort par un double-clic n'importe où, le point en bas à droite, ou
    le raccourci (Ctrl+K). Pas par Échap : c'est la touche qu'on presse par
    réflexe, et une page de repli qui lui cède ne protège pas.
    """

    leave = Signal()
    titleChanged = Signal(str)        # la copie de fichiers change de titre
    TICK_MS = 700

    def __init__(self, parent=None, cover: str = "update"):
        super().__init__(parent)
        self.setObjectName("quietPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("QWidget#quietPage { background: #f3f3f3; }")
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.covers: dict = {}
        self.key = ""
        self.cover = None
        # Discret jusqu'a l'effacement : c'est la sortie, et elle ne doit pas
        # se lire par-dessus l'epaule.
        self.back = QPushButton("·", self)
        self.back.setFocusPolicy(Qt.NoFocus)
        self.back.setFixedSize(18, 18)
        self.back.setToolTip("")
        self.back.setStyleSheet("QPushButton { background: transparent; border: 0; color: rgba(128,128,128,90);"
                                " font-size: 11px; } QPushButton:hover { color: #6b7280; }")
        self.timer = QTimer(self)
        self.timer.setInterval(self.TICK_MS)
        self.timer.timeout.connect(self._tick)
        self.set_cover(cover)

    # -- le choix de l'écran --------------------------------------------------
    def set_cover(self, key: str) -> None:
        if key not in COVER_KEYS:
            key = COVER_KEYS[0]
        if key == self.key:
            return
        cover = self.covers.get(key)
        if cover is None:
            kind = next(cls for k, _label, cls in QUIET_COVERS if k == key)
            cover = kind(self)
            self.covers[key] = cover
            self.stack.addWidget(cover)
            # Un double-clic n'importe ou ramene : chaque recoin de l'ecran,
            # tableau et graphes compris, le transmet.
            for widget in [cover] + cover.findChildren(QWidget):
                widget.installEventFilter(self)
                if hasattr(widget, "viewport"):
                    widget.viewport().installEventFilter(self)
        self.key = key
        self.cover = cover
        self.stack.setCurrentWidget(cover)
        self.back.raise_()

    def title(self) -> str:
        """Le titre de la fenêtre, tel que l'écran choisi le montrerait."""
        return getattr(self.cover, "window_title", "") or self.cover.TITLE

    def icon_kind(self):
        return self.cover.ICON

    # -- la vie de l'écran ------------------------------------------------------
    def _tick(self) -> None:
        if self.cover is not None:
            before = self.title()
            self.cover.tick()
            if self.title() != before:
                self.titleChanged.emit(self.title())

    def start(self) -> None:
        if hasattr(self.cover, "reset"):
            self.cover.reset()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.back.move(self.width() - self.back.width() - 4, self.height() - self.back.height() - 4)
        self.back.raise_()

    def mouseDoubleClickEvent(self, event):
        self.leave.emit()
        event.accept()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonDblClick:
            self.leave.emit()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        # Aucune touche n'en fait sortir, Echap compris : c'est la touche
        # qu'on presse par reflexe. F5 ramenait deja Prisme sans que rien ne
        # l'annonce. Ctrl+K, lui, est pris par la fenetre.
        if event.key() == Qt.Key_Escape:
            return
        super().keyPressEvent(event)
