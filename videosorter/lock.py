"""Le code PIN : quatre chiffres pour ouvrir Prisme.

Demande a l'ouverture, au retour du repli (Ctrl+K) et pour afficher les
dossiers masques. La page du cadenas est aussi neutre que celle du repli :
ni nom, ni image, ni titre qui trahirait Prisme -- on y tape le code, ou
l'on repart vers la page neutre.

Le code n'est jamais garde en clair : seulement son empreinte (PBKDF2, sel
propre a chaque installation). Apres cinq erreurs, il faut attendre trente
secondes, puis le double a chaque nouvelle serie, jusqu'a dix minutes -- et
l'attente survit a une relance de Prisme.

Code oublie : Prisme ferme, vider « pin_salt » et « pin_digest » dans
config.json.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QWidget,
)

PIN_LENGTH = 4
ROUNDS = 200_000
TRIES = 5                    # erreurs permises avant d'attendre
FIRST_WAIT_S = 30
LONGEST_WAIT_S = 600


def digest_of(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt),
                               ROUNDS).hex()


def pin_is_set(cfg) -> bool:
    return bool(cfg["pin_salt"] and cfg["pin_digest"])


def set_pin(cfg, pin: str) -> None:
    """Pose (ou change) le code. Une chaine vide le retire."""
    if not pin:
        cfg["pin_salt"] = cfg["pin_digest"] = ""
    else:
        salt = secrets.token_hex(16)
        cfg["pin_salt"], cfg["pin_digest"] = salt, digest_of(pin, salt)
    cfg["pin_failures"] = 0
    cfg["pin_wait_until"] = 0
    cfg.save()


def wait_left(cfg, now: float | None = None) -> int:
    """Secondes a attendre avant de pouvoir reessayer (0 : on peut)."""
    now = time.time() if now is None else now
    return max(0, int(float(cfg["pin_wait_until"] or 0) - now + 0.999))


def check_pin(cfg, pin: str, now: float | None = None) -> tuple:
    """(bon ?, secondes a attendre). Compte les erreurs, et impose l'attente
    au bout de chaque serie de cinq."""
    now = time.time() if now is None else now
    if not pin_is_set(cfg):
        return True, 0
    left = wait_left(cfg, now)
    if left:
        return False, left
    good = hmac.compare_digest(digest_of(pin, cfg["pin_salt"]), cfg["pin_digest"])
    if good:
        cfg["pin_failures"] = 0
        cfg["pin_wait_until"] = 0
        cfg.save_soon()
        return True, 0
    failures = int(cfg["pin_failures"] or 0) + 1
    cfg["pin_failures"] = failures
    wait = 0
    if failures % TRIES == 0:
        series = failures // TRIES
        wait = min(LONGEST_WAIT_S, FIRST_WAIT_S * 2 ** (series - 1))
        cfg["pin_wait_until"] = now + wait
    cfg.save()
    return False, wait


LOCK_STYLE = """
QWidget#lockPage { background: #f3f4f6; }
QLabel#lockHead { color: #1f2937; font-size: 16px; font-weight: 600; }
QLabel#lockLine { color: #6b7280; font-size: 13px; }
QLabel#lockError { color: #b45309; font-size: 12px; }
QLineEdit#lockField { background: #ffffff; border: 1px solid #d1d5db; border-radius: 6px;
                      color: #111827; font-size: 22px; letter-spacing: 10px;
                      padding: 6px 10px; }
QLineEdit#lockField:focus { border-color: #9ca3af; }
QPushButton#lockBack { background: transparent; border: 0; color: #9ca3af;
                       font-size: 11px; padding: 2px 6px; }
QPushButton#lockBack:hover { color: #4b5563; }
"""


class LockPage(QWidget):
    """Le cadenas : le code, ou retour a la page neutre (Echap, le lien).

    Aussi neutre que la page de repli : un utilitaire qui demande un code
    d'acces, sans rien qui rappelle Prisme."""

    unlocked = Signal()
    hide_me = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setObjectName("lockPage")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(LOCK_STYLE)
        outer = QVBoxLayout(self)
        outer.addStretch(2)
        column = QVBoxLayout()
        column.setSpacing(10)
        head = QLabel("Accès protégé", self)
        head.setObjectName("lockHead")
        head.setAlignment(Qt.AlignCenter)
        column.addWidget(head)
        line = QLabel("Saisissez le code d'accès.", self)
        line.setObjectName("lockLine")
        line.setAlignment(Qt.AlignCenter)
        column.addWidget(line)
        self.field = QLineEdit(self)
        self.field.setObjectName("lockField")
        self.field.setEchoMode(QLineEdit.Password)
        self.field.setMaxLength(PIN_LENGTH)
        self.field.setValidator(QIntValidator(0, 10 ** PIN_LENGTH - 1, self))
        self.field.setAlignment(Qt.AlignCenter)
        self.field.setFixedWidth(150)
        self.field.textChanged.connect(self._typed)
        self.field.installEventFilter(self)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.field)
        row.addStretch(1)
        column.addLayout(row)
        self.error = QLabel("", self)
        self.error.setObjectName("lockError")
        self.error.setAlignment(Qt.AlignCenter)
        column.addWidget(self.error)
        outer.addLayout(column)
        outer.addStretch(3)
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.back = QPushButton("Retour", self)
        self.back.setObjectName("lockBack")
        self.back.setFocusPolicy(Qt.NoFocus)
        self.back.clicked.connect(self.hide_me.emit)
        foot.addWidget(self.back)
        outer.addLayout(foot)
        # Le compte a rebours d'une attente imposee.
        self.clock = QTimer(self)
        self.clock.setInterval(500)
        self.clock.timeout.connect(self._show_wait)

    def reset(self) -> None:
        self.field.clear()
        self.error.clear()
        self._show_wait()

    def setFocus(self, *args):                       # noqa: N802
        self.field.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        self.reset()
        self.field.setFocus()

    def eventFilter(self, watched, event):
        if watched is self.field and event.type() == QEvent.KeyPress \
                and event.key() == Qt.Key_Escape:
            self.hide_me.emit()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            return self.hide_me.emit()
        super().keyPressEvent(event)

    def _typed(self, text: str) -> None:
        if len(text) < PIN_LENGTH:
            return
        good, wait = check_pin(self.cfg, text)
        self.field.clear()
        if good:
            self.error.clear()
            self.unlocked.emit()
            return
        if wait:
            self._show_wait()
        else:
            left = TRIES - int(self.cfg["pin_failures"] or 0) % TRIES
            self.error.setText(f"Code incorrect — encore {left} essai{'s' if left > 1 else ''}.")

    def _show_wait(self) -> None:
        left = wait_left(self.cfg)
        self.field.setEnabled(not left)
        if left:
            minutes, seconds = divmod(left, 60)
            self.error.setText(f"Trop d'essais : réessayez dans "
                               + (f"{minutes} min {seconds:02d} s" if minutes else f"{seconds} s")
                               + ".")
            if not self.clock.isActive():
                self.clock.start()
        else:
            if self.clock.isActive():
                self.clock.stop()
                self.error.clear()
                self.field.setFocus()


def ask_pin(cfg, parent, reason: str) -> bool:
    """Demande le code dans une petite fenetre (dossiers masques). Vrai s'il
    est bon -- ou s'il n'y a pas de code."""
    if not pin_is_set(cfg):
        return True
    dialog = QDialog(parent)
    dialog.setWindowTitle("Code d'accès")
    box = QVBoxLayout(dialog)
    box.addWidget(QLabel(reason, dialog))
    field = QLineEdit(dialog)
    field.setEchoMode(QLineEdit.Password)
    field.setMaxLength(PIN_LENGTH)
    field.setValidator(QIntValidator(0, 10 ** PIN_LENGTH - 1, dialog))
    box.addWidget(field)
    error = QLabel("", dialog)
    error.setStyleSheet("color: #e0a040;")
    box.addWidget(error)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
    box.addWidget(buttons)
    result = {"ok": False}

    def attempt() -> None:
        good, wait = check_pin(cfg, field.text())
        field.clear()
        if good:
            result["ok"] = True
            dialog.accept()
        elif wait or wait_left(cfg):
            error.setText(f"Trop d'essais : réessayez dans {wait_left(cfg)} s.")
        else:
            error.setText("Code incorrect.")

    buttons.accepted.connect(attempt)
    buttons.rejected.connect(dialog.reject)
    field.textChanged.connect(lambda text: attempt() if len(text) == PIN_LENGTH else None)
    dialog.exec()
    return result["ok"]


class PinDialog(QDialog):
    """Poser, changer ou retirer le code (le code actuel est demande d'abord)."""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Code PIN")
        box = QVBoxLayout(self)
        told = QLabel(
            "Quatre chiffres, demandés à l'ouverture de Prisme, au retour du repli "
            "(Ctrl+K) et pour afficher les dossiers masqués.\n"
            "Laissez le nouveau code vide pour le retirer.", self)
        told.setWordWrap(True)
        box.addWidget(told)
        form = QFormLayout()

        def digits() -> QLineEdit:
            field = QLineEdit(self)
            field.setEchoMode(QLineEdit.Password)
            field.setMaxLength(PIN_LENGTH)
            field.setValidator(QIntValidator(0, 10 ** PIN_LENGTH - 1, self))
            return field
        self.current = digits()
        if pin_is_set(cfg):
            form.addRow("Code actuel", self.current)
        self.new = digits()
        self.again = digits()
        form.addRow("Nouveau code", self.new)
        form.addRow("Encore une fois", self.again)
        box.addLayout(form)
        self.error = QLabel("", self)
        self.error.setStyleSheet("color: #e0a040;")
        box.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.said = ""

    def _save(self) -> None:
        if pin_is_set(self.cfg):
            good, wait = check_pin(self.cfg, self.current.text())
            if not good:
                self.error.setText(f"Code actuel incorrect{f' — attendez {wait} s' if wait else ''}.")
                return
        new, again = self.new.text(), self.again.text()
        if new != again:
            self.error.setText("Les deux nouveaux codes ne sont pas identiques.")
            return
        if new and len(new) != PIN_LENGTH:
            self.error.setText(f"Le code a {PIN_LENGTH} chiffres.")
            return
        set_pin(self.cfg, new)
        self.said = "Code PIN posé." if new else "Code PIN retiré."
        self.accept()
