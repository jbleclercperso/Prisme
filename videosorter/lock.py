"""Le code PIN : Prisme ne se montre qu'à qui le connaît.

Posé, il est demandé à l'ouverture de Prisme et au retour du repli
(Ctrl+K) : quelqu'un qui double-clique sur la page neutre, ou qui lance
Prisme en votre absence, tombe sur un cadenas, pas sur votre collection.

Le code n'est jamais gardé en clair : seule son empreinte PBKDF2 est
écrite dans les réglages. Quatre chiffres se devinent en dix mille essais :
après cinq erreurs de suite, il faut attendre, de plus en plus longtemps,
même en fermant et rouvrant Prisme.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from PySide6.QtCore import QRegularExpression, Qt, QTimer, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

PIN_LENGTH = 4
# Cinq erreurs de suite, puis une attente qui double a chaque nouvelle serie,
# jusqu'a dix minutes : dix mille codes deviennent des jours d'essais.
FREE_TRIES = 5
FIRST_WAIT = 30
LONGEST_WAIT = 600

LOCK_STYLE = """
QWidget#lockPage { background: #f3f4f6; }
QLabel#lockHead { color: #1f2937; font-size: 16px; font-weight: 600; }
QLabel#lockLine { color: #6b7280; font-size: 13px; }
QLabel#lockDot { background: transparent; border: 2px solid #9ca3af;
                 border-radius: 8px; }
QLabel#lockDot[filled="true"] { background: #4b5563; border-color: #4b5563; }
QPushButton#lockKey { background: #ffffff; border: 1px solid #e5e7eb;
                      border-radius: 10px; color: #1f2937; font-size: 18px;
                      min-width: 64px; min-height: 48px; }
QPushButton#lockKey:hover { background: #f9fafb; border-color: #d1d5db; }
QPushButton#lockKey:disabled { color: #d1d5db; }
QPushButton#lockForgot { background: transparent; border: 0; color: #9ca3af;
                         font-size: 11px; padding: 2px 6px; }
QPushButton#lockForgot:hover { color: #6b7280; }
"""


def hash_pin(pin: str, salt: str = "") -> tuple:
    """(sel, empreinte) du code — PBKDF2, deux cent mille tours."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return salt, digest.hex()


def pin_ok(pin: str, salt: str, expected: str) -> bool:
    if not salt or not expected:
        return False
    _salt, found = hash_pin(pin, salt)
    return hmac.compare_digest(found, expected)


def valid_pin(text: str) -> bool:
    return len(text) == PIN_LENGTH and text.isdigit()


def wait_after(failures: int) -> int:
    """Secondes d'attente imposees apres tant d'erreurs de suite (0 : aucune)."""
    if failures < FREE_TRIES or failures % FREE_TRIES:
        return 0
    rounds = failures // FREE_TRIES - 1
    return min(FIRST_WAIT * 2 ** rounds, LONGEST_WAIT)


def ask_pin(parent, title: str, label: str) -> str | None:
    """Quatre chiffres, masques ; None si l'on renonce ou si ce n'en est pas."""
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextEchoMode(QLineEdit.Password)
    field = dialog.findChild(QLineEdit)
    if field is not None:
        field.setMaxLength(PIN_LENGTH)
        field.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"\d{0,%d}" % PIN_LENGTH), field))
        field.setInputMethodHints(Qt.ImhDigitsOnly)
    if not dialog.exec():
        return None
    text = dialog.textValue().strip()
    if not valid_pin(text):
        QMessageBox.information(parent, title,
                                f"Le code doit compter {PIN_LENGTH} chiffres.")
        return None
    return text


class LockPage(QWidget):
    """Le cadenas : quatre points, un pavé, et rien de la collection.

    Même allure que la page neutre du repli, pour ne pas attirer l'œil. Les
    chiffres se tapent au clavier (pavé numérique compris) ou à la souris ;
    le quatrième chiffre valide de lui-même. Échap ramène à la page neutre.
    """

    unlocked = Signal()
    hide_me = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.entry = ""
        self.setObjectName("lockPage")
        # Sans cet attribut, le fond pose par la feuille de style ne se peint
        # pas sur un QWidget : on voyait le fond sombre de la fenetre.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(LOCK_STYLE)
        self.setFocusPolicy(Qt.StrongFocus)

        box = QVBoxLayout(self)
        box.setContentsMargins(38, 32, 38, 24)
        box.addStretch(2)

        head = QLabel("Session verrouillée", self)
        head.setObjectName("lockHead")
        head.setAlignment(Qt.AlignCenter)
        box.addWidget(head)

        self.line = QLabel("", self)
        self.line.setObjectName("lockLine")
        self.line.setAlignment(Qt.AlignCenter)
        box.addWidget(self.line)
        box.addSpacing(18)

        dots = QHBoxLayout()
        dots.setSpacing(16)
        dots.addStretch(1)
        self.dots = []
        for _ in range(PIN_LENGTH):
            dot = QLabel(self)
            dot.setObjectName("lockDot")
            dot.setFixedSize(16, 16)
            dots.addWidget(dot)
            self.dots.append(dot)
        dots.addStretch(1)
        box.addLayout(dots)
        box.addSpacing(22)

        grid = QGridLayout()
        grid.setSpacing(10)
        self.keys = []
        layout = ("123", "456", "789", "⌫0")
        for row, keys in enumerate(layout):
            for column, key in enumerate(keys):
                if row == 3:
                    # Rangee du bas : 0 au milieu, l'effacement a droite.
                    column = 2 if key == "⌫" else 1
                button = QPushButton(key, self)
                button.setObjectName("lockKey")
                button.setFocusPolicy(Qt.NoFocus)
                if key == "⌫":
                    button.clicked.connect(self.erase)
                else:
                    button.clicked.connect(lambda _c=False, k=key: self.press(k))
                grid.addWidget(button, row, column)
                self.keys.append(button)
        pad = QHBoxLayout()
        pad.addStretch(1)
        pad.addLayout(grid)
        pad.addStretch(1)
        box.addLayout(pad)
        box.addStretch(3)

        foot = QHBoxLayout()
        foot.addStretch(1)
        self.forgot = QPushButton("Code oublié ?", self)
        self.forgot.setObjectName("lockForgot")
        self.forgot.setFocusPolicy(Qt.NoFocus)
        self.forgot.clicked.connect(self._explain_forgotten)
        foot.addWidget(self.forgot, 0)
        box.addLayout(foot)

        # Le compte a rebours d'une attente imposee.
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

    # -- etat -------------------------------------------------------------
    def reset(self) -> None:
        self.entry = ""
        self._paint()
        self._tick()

    def waiting(self) -> int:
        """Secondes d'attente restantes, 0 si l'on peut taper."""
        until = float(self.cfg["pin_wait_until"] or 0)
        return max(0, int(until - time.time() + 0.999))

    def _tick(self) -> None:
        left = self.waiting()
        for button in self.keys:
            button.setEnabled(not left)
        if left:
            self.line.setText(f"Trop d'essais. Réessayez dans {left} s.")
            self.timer.start()
            return
        self.timer.stop()
        if self.line.text().startswith("Trop d'essais"):
            self.line.setText("")
        if not self.line.text():
            self.line.setText(f"Entrez votre code à {PIN_LENGTH} chiffres.")

    def _paint(self) -> None:
        for at, dot in enumerate(self.dots):
            dot.setProperty("filled", at < len(self.entry))
            dot.style().unpolish(dot)
            dot.style().polish(dot)

    # -- saisie -----------------------------------------------------------
    def press(self, digit: str) -> None:
        if self.waiting() or len(self.entry) >= PIN_LENGTH:
            return
        self.entry += digit
        self._paint()
        if len(self.entry) == PIN_LENGTH:
            # Le quatrieme point s'affiche avant la verification (un bon
            # dixieme de seconde de calcul).
            QTimer.singleShot(60, self._check)

    def erase(self) -> None:
        self.entry = self.entry[:-1]
        self._paint()

    def _check(self) -> None:
        given, self.entry = self.entry, ""
        if len(given) != PIN_LENGTH:
            return
        cfg = self.cfg
        if pin_ok(given, cfg["pin_salt"], cfg["pin_digest"]):
            cfg["pin_failures"] = 0
            cfg["pin_wait_until"] = 0
            cfg.save()
            self.line.setText("")
            self._paint()
            self.unlocked.emit()
            return
        failures = int(cfg["pin_failures"] or 0) + 1
        cfg["pin_failures"] = failures
        wait = wait_after(failures)
        if wait:
            cfg["pin_wait_until"] = time.time() + wait
        # Tout de suite sur le disque : fermer et rouvrir Prisme ne doit pas
        # remettre le compteur a zero.
        cfg.save()
        self.line.setText("Code incorrect.")
        self._paint()
        self._tick()

    def keyPressEvent(self, event):
        key = event.key()
        if event.modifiers() & Qt.ControlModifier:
            return super().keyPressEvent(event)
        if Qt.Key_0 <= key <= Qt.Key_9:
            if not event.isAutoRepeat():
                self.press(chr(key))
            return
        if key == Qt.Key_Backspace:
            self.erase()
            return
        if key == Qt.Key_Escape:
            if not event.isAutoRepeat():
                self.hide_me.emit()
            return
        event.accept()

    def _explain_forgotten(self) -> None:
        QMessageBox.information(
            self, "Code oublié",
            "Le code n'est gardé nulle part en clair : il ne peut pas être "
            "retrouvé.\n\nÉcrivez-nous depuis l'adresse utilisée pour votre "
            "achat : nous vous expliquerons comment le réinitialiser. Vos "
            "vidéos, vos favoris et vos réglages sont conservés.")
