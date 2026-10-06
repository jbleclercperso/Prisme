"""Telegram, version perso : recherche de channels publics, et alertes.

Le compte est celui de l'utilisateur (api_id / api_hash créés sur
my.telegram.org). La session et les identifiants restent dans le dossier
de configuration de Prisme, jamais dans le dépôt.

Deux usages :
- Recherche : mot-clé Telegram, ou une liste d'URL collée depuis un annuaire.
  Chaque channel est noté (abonnés, médias récents, dernier message, contenu
  protégé) pour garder ceux qui publient vraiment.
- Alertes : les channels enregistrés. Au rafraîchissement, le nombre de
  nouveaux médias depuis la dernière vue, et le total.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .config import APP_DIR

STATE_PATH = APP_DIR / "telegram.json"
SESSION_PATH = APP_DIR / "telegram"
URL_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]{4,})", re.I)

STYLE = """
QDialog { background: #0e1116; }
QLabel { color: #c9d1db; }
QLabel#tgHead { color: #ffffff; font-size: 18px; font-weight: 700; }
QLabel#tgLead { color: #aab4c0; font-size: 12px; }
QLabel#tgTotal { color: #8fd0ff; font-size: 14px; font-weight: 600; }
QLineEdit, QTextEdit, QSpinBox {
    background: #0b0e12; border: 1px solid #242b35; border-radius: 6px;
    color: #e6e8ea; padding: 6px; }
QTableWidget { background: #0b0e12; border: 1px solid #242b35; border-radius: 8px;
               color: #e6e8ea; gridline-color: #1c2430; }
QHeaderView::section { background: #161c24; color: #c9d1db; border: none; padding: 6px; }
QTableWidget::item:selected { background: #1d2a40; }
QPushButton { background: #232a34; border: 1px solid #364050; border-radius: 6px;
              padding: 7px 14px; color: #eef1f4; }
QPushButton:hover { background: #2c3541; }
QPushButton:disabled { color: #6f7a87; background: #1a1f27; }
QPushButton#tgPrimary { background: #2f6fed; border-color: #2f6fed; font-weight: 600; }
QCheckBox { color: #c9d1db; }
QTabWidget::pane { border: 1px solid #242b35; }
QTabBar::tab { background: #161c24; color: #c9d1db; padding: 8px 14px; }
QTabBar::tab:selected { background: #2f6fed; color: white; }
"""


def load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("api_id", "")
    data.setdefault("api_hash", "")
    data.setdefault("phone", "")
    data.setdefault("channels", [])
    data.setdefault("last_id", {})
    data.setdefault("unread", {})
    return data


def save_state(data: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def username_of(text: str) -> str:
    text = (text or "").strip()
    found = URL_RE.search(text)
    if found:
        return found.group(1)
    return text.lstrip("@").split("/")[0]


def _alive(days: int | None) -> str:
    if days is None:
        return "?"
    if days <= 2:
        return "vivant"
    if days <= 14:
        return "calme"
    return "endormi"


def _score(row: dict) -> float:
    if row.get("protege") or row.get("restreint") or not row.get("username"):
        return -1
    subs = int(row.get("abonnes") or 0)
    medias = int(row.get("medias") or 0)
    days = row.get("jours")
    freshness = 1.0 if days is None else 1.0 / (1 + days / 7)
    return subs * (1 + medias) * freshness


class TelegramWorker(QThread):
    """Un client Telethon, hors du fil de l'interface."""

    status = Signal(str)
    need_code = Signal()
    need_password = Signal()
    search_done = Signal(list)
    alerts_done = Signal(dict)
    failed = Signal(str)

    def __init__(self, state: dict):
        super().__init__()
        self.state = state
        self.job = "login"
        self.query = ""
        self.pasted: list[str] = []
        self.min_subs = 0
        self.only_open = True
        self._code = ""
        self._password = ""
        self._wait = threading.Event()

    def give_code(self, code: str) -> None:
        self._code = code.strip()
        self._wait.set()

    def give_password(self, password: str) -> None:
        self._password = password
        self._wait.set()

    def _ask(self, kind: str) -> str:
        self._wait.clear()
        (self.need_code if kind == "code" else self.need_password).emit()
        if not self._wait.wait(180):
            raise TimeoutError("code Telegram non saisi")
        return self._code if kind == "code" else self._password

    def run(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as exc:  # noqa: BLE001 — remonté à l'interface
            self.failed.emit(f"{type(exc).__name__} : {exc}")

    async def _run(self) -> None:
        try:
            from telethon import TelegramClient
            from telethon.errors import FloodWaitError, SessionPasswordNeededError
            from telethon.tl.functions.contacts import SearchRequest
            from telethon.tl.types import Channel, InputMessagesFilterDocument, InputMessagesFilterVideo
        except ImportError:
            self.failed.emit("Telethon n'est pas installé. Lance : pip install telethon")
            return
        api_id = int(self.state.get("api_id") or 0)
        api_hash = self.state.get("api_hash") or ""
        if not api_id or not api_hash:
            self.failed.emit("api_id et api_hash manquent (my.telegram.org).")
            return
        client = TelegramClient(str(SESSION_PATH), api_id, api_hash)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                phone = self.state.get("phone") or ""
                if not phone:
                    self.failed.emit("Numéro manquant, au format international.")
                    return
                await client.send_code_request(phone)
                self.status.emit("Code envoyé dans Telegram.")
                try:
                    await client.sign_in(phone, self._ask("code"))
                except SessionPasswordNeededError:
                    await client.sign_in(password=self._ask("password"))
            me = await client.get_me()
            self.status.emit(f"Connecté : {me.first_name or me.username or me.id}")
            if self.job == "search":
                rows = await self._search(client, SearchRequest, Channel,
                                           InputMessagesFilterVideo, FloodWaitError)
                self.search_done.emit(rows)
            elif self.job == "alerts":
                report = await self._alerts(client, InputMessagesFilterVideo,
                                             InputMessagesFilterDocument, FloodWaitError)
                self.alerts_done.emit(report)
        finally:
            await client.disconnect()

    async def _inspect(self, client, chat, video_filter, flood) -> dict:
        from telethon.tl.types import Channel
        username = getattr(chat, "username", None)
        title = getattr(chat, "title", None) or username or "?"
        row = {
            "title": title,
            "username": username or "",
            "url": f"https://t.me/{username}" if username else "",
            "kind": "channel" if getattr(chat, "broadcast", False) else "groupe",
            "abonnes": int(getattr(chat, "participants_count", 0) or 0),
            "protege": bool(getattr(chat, "noforwards", False)),
            "restreint": bool(getattr(chat, "restricted", False)),
            "medias": 0,
            "jours": None,
            "dernier": "",
        }
        if not username:
            return row
        try:
            last = None
            medias = 0
            async for msg in client.iter_messages(username, limit=40):
                if last is None:
                    last = msg
                if msg.video or msg.file:
                    medias += 1
            row["medias"] = medias
            if last is not None and last.date is not None:
                age = (time.time() - last.date.timestamp()) / 86400
                row["jours"] = int(age)
                row["dernier"] = last.date.strftime("%d/%m %H:%M")
        except flood as exc:
            await asyncio.sleep(min(exc.seconds, 20))
        except Exception:  # noqa: BLE001 — channel illisible, on garde la fiche
            row["dernier"] = "illisible"
        row["vivant"] = _alive(row["jours"])
        row["score"] = round(_score(row))
        return row

    async def _search(self, client, SearchRequest, Channel, video_filter, flood) -> list:
        seen: dict[str, object] = {}
        query = self.query.strip()
        if query:
            self.status.emit(f"Recherche Telegram : {query}")
            try:
                found = await client(SearchRequest(q=query, limit=40))
                for chat in found.chats:
                    if isinstance(chat, Channel):
                        seen[str(chat.id)] = chat
            except flood as exc:
                self.status.emit(f"Telegram demande d'attendre {exc.seconds} s.")
                await asyncio.sleep(min(exc.seconds, 30))
        for raw in self.pasted:
            name = username_of(raw)
            if not name:
                continue
            try:
                entity = await client.get_entity(name)
                if isinstance(entity, Channel):
                    seen[str(entity.id)] = entity
            except Exception as exc:  # noqa: BLE001
                seen[name] = {"title": name, "username": name, "error": str(exc)}
        rows = []
        for chat in seen.values():
            if isinstance(chat, dict):
                rows.append({
                    "title": chat["title"], "username": chat["username"],
                    "url": f"https://t.me/{chat['username']}", "kind": "?",
                    "abonnes": 0, "protege": False, "restreint": False,
                    "medias": 0, "jours": None, "dernier": chat["error"][:40],
                    "vivant": "?", "score": -1,
                })
                continue
            row = await self._inspect(client, chat, video_filter, flood)
            if self.min_subs and row["abonnes"] < self.min_subs:
                continue
            if self.only_open and (row["protege"] or row["restreint"] or not row["username"]):
                continue
            rows.append(row)
            self.status.emit(f"{len(rows)} channels lus")
        rows.sort(key=lambda r: r.get("score") or 0, reverse=True)
        return rows

    async def _alerts(self, client, video_filter, doc_filter, flood) -> dict:
        total = 0
        lines = []
        for entry in self.state.get("channels") or []:
            username = entry.get("username") or ""
            if not username:
                continue
            last = int((self.state.get("last_id") or {}).get(username) or 0)
            count = 0
            max_id = last
            try:
                async for msg in client.iter_messages(username, min_id=last, limit=80):
                    if msg.video or (msg.file and not msg.web_preview):
                        count += 1
                    max_id = max(max_id, msg.id)
            except flood as exc:
                await asyncio.sleep(min(exc.seconds, 20))
                lines.append({"username": username, "title": entry.get("title") or username,
                              "nouveaux": 0, "note": f"attente {exc.seconds}s"})
                continue
            except Exception as exc:  # noqa: BLE001
                lines.append({"username": username, "title": entry.get("title") or username,
                              "nouveaux": 0, "note": str(exc)[:60]})
                continue
            if last == 0:
                # Premier passage : on arme le curseur, sans compter l'historique.
                self.state.setdefault("last_id", {})[username] = max_id
                self.state.setdefault("unread", {})[username] = 0
                lines.append({"username": username, "title": entry.get("title") or username,
                              "nouveaux": 0, "note": "armé"})
                continue
            self.state.setdefault("last_id", {})[username] = max_id
            unread = int((self.state.get("unread") or {}).get(username) or 0) + count
            self.state.setdefault("unread", {})[username] = unread
            total += count
            lines.append({"username": username, "title": entry.get("title") or username,
                          "nouveaux": count, "note": ""})
        save_state(self.state)
        return {"total": total, "lines": lines}


class TelegramDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Telegram — recherche et alertes")
        self.setStyleSheet(STYLE)
        self.resize(980, 640)
        self.state = load_state()
        self.worker: TelegramWorker | None = None
        self.rows: list[dict] = []
        box = QVBoxLayout(self)
        head = QLabel("Telegram", self)
        head.setObjectName("tgHead")
        box.addWidget(head)
        lead = QLabel(
            "Compte perso uniquement. api_id et api_hash se créent sur my.telegram.org. "
            "La recherche Telegram est un échantillon : colle aussi des liens t.me trouvés "
            "sur un annuaire pour les noter (abonnés, médias récents, channel vivant).",
            self)
        lead.setObjectName("tgLead")
        lead.setWordWrap(True)
        box.addWidget(lead)
        self.status = QLabel("Pas encore connecté.", self)
        box.addWidget(self.status)
        tabs = QTabWidget(self)
        tabs.addTab(self._account_tab(), "Compte")
        tabs.addTab(self._search_tab(), "Recherche")
        tabs.addTab(self._alert_tab(), "Alertes")
        box.addWidget(tabs, 1)
        self._refresh_alerts_table()
        self.timer = QTimer(self)
        self.timer.setInterval(10 * 60 * 1000)
        self.timer.timeout.connect(self.refresh_alerts)
        self.timer.start()

    def _account_tab(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        self.api_id = QLineEdit(str(self.state.get("api_id") or ""), page)
        self.api_hash = QLineEdit(self.state.get("api_hash") or "", page)
        self.api_hash.setEchoMode(QLineEdit.Password)
        self.phone = QLineEdit(self.state.get("phone") or "", page)
        self.phone.setPlaceholderText("+33…")
        form.addRow("api_id", self.api_id)
        form.addRow("api_hash", self.api_hash)
        form.addRow("Téléphone", self.phone)
        row = QHBoxLayout()
        save = QPushButton("Enregistrer", page)
        save.clicked.connect(self._save_account)
        row.addWidget(save)
        login = QPushButton("Connecter", page)
        login.setObjectName("tgPrimary")
        login.clicked.connect(self._login)
        row.addWidget(login)
        row.addStretch(1)
        form.addRow(row)
        return page

    def _search_tab(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        self.query = QLineEdit(page)
        self.query.setPlaceholderText("mot-clé")
        row.addWidget(self.query, 1)
        self.min_subs = QSpinBox(page)
        self.min_subs.setRange(0, 1_000_000)
        self.min_subs.setPrefix("min abonnés ")
        row.addWidget(self.min_subs)
        self.only_open = QCheckBox("publics, non protégés", page)
        self.only_open.setChecked(True)
        row.addWidget(self.only_open)
        go = QPushButton("Chercher", page)
        go.setObjectName("tgPrimary")
        go.clicked.connect(self.search)
        row.addWidget(go)
        lay.addLayout(row)
        self.pasted = QTextEdit(page)
        self.pasted.setPlaceholderText("ou colle des URL t.me/… , une par ligne")
        self.pasted.setFixedHeight(70)
        lay.addWidget(self.pasted)
        self.table = QTableWidget(0, 8, page)
        self.table.setHorizontalHeaderLabels(
            ["Score", "Titre", "Type", "Abonnés", "Médias /40", "Activité", "Protégé", "Lien"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.table, 1)
        keep = QPushButton("Enregistrer la sélection dans les alertes", page)
        keep.clicked.connect(self.save_selected)
        lay.addWidget(keep)
        return page

    def _alert_tab(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        self.total = QLabel("Aucun passage encore.", page)
        self.total.setObjectName("tgTotal")
        lay.addWidget(self.total)
        add = QHBoxLayout()
        self.add_url = QLineEdit(page)
        self.add_url.setPlaceholderText("https://t.me/nom_du_channel")
        add.addWidget(self.add_url, 1)
        plus = QPushButton("Ajouter", page)
        plus.clicked.connect(self.add_channel)
        add.addWidget(plus)
        lay.addLayout(add)
        self.alerts = QTableWidget(0, 4, page)
        self.alerts.setHorizontalHeaderLabels(["Channel", "Nouveaux", "Non lus", "Note"])
        self.alerts.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.alerts, 1)
        row = QHBoxLayout()
        refresh = QPushButton("Vérifier maintenant", page)
        refresh.setObjectName("tgPrimary")
        refresh.clicked.connect(self.refresh_alerts)
        row.addWidget(refresh)
        seen = QPushButton("Marquer comme vus", page)
        seen.clicked.connect(self.mark_seen)
        row.addWidget(seen)
        drop = QPushButton("Retirer", page)
        drop.clicked.connect(self.remove_channel)
        row.addWidget(drop)
        row.addStretch(1)
        lay.addLayout(row)
        return page

    def _save_account(self) -> None:
        self.state["api_id"] = self.api_id.text().strip()
        self.state["api_hash"] = self.api_hash.text().strip()
        self.state["phone"] = self.phone.text().strip()
        save_state(self.state)
        self.status.setText("Identifiants enregistrés localement.")

    def _start(self, job: str) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("Une opération Telegram est déjà en cours.")
            return
        self._save_account()
        self.worker = TelegramWorker(self.state)
        self.worker.job = job
        self.worker.query = self.query.text()
        self.worker.pasted = [line for line in self.pasted.toPlainText().splitlines() if line.strip()]
        self.worker.min_subs = self.min_subs.value()
        self.worker.only_open = self.only_open.isChecked()
        self.worker.status.connect(self.status.setText)
        self.worker.failed.connect(self._failed)
        self.worker.need_code.connect(self._ask_code)
        self.worker.need_password.connect(self._ask_password)
        self.worker.search_done.connect(self._show_search)
        self.worker.alerts_done.connect(self._show_alerts)
        self.worker.start()

    def _login(self) -> None:
        self._start("login")

    def search(self) -> None:
        self._start("search")

    def refresh_alerts(self) -> None:
        self._start("alerts")

    def _ask_code(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        code, ok = QInputDialog.getText(self, "Code Telegram", "Code reçu dans Telegram :")
        if self.worker is not None:
            self.worker.give_code(code if ok else "")

    def _ask_password(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        password, ok = QInputDialog.getText(
            self, "Mot de passe Telegram", "Mot de passe 2FA :", QLineEdit.Password)
        if self.worker is not None:
            self.worker.give_password(password if ok else "")

    def _failed(self, text: str) -> None:
        self.status.setText(text)
        QMessageBox.warning(self, "Telegram", text)

    def _show_search(self, rows: list) -> None:
        self.rows = rows
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            values = [
                str(row.get("score") or 0),
                row.get("title") or "",
                row.get("kind") or "",
                str(row.get("abonnes") or 0),
                str(row.get("medias") or 0),
                f"{row.get('vivant') or '?'} · {row.get('dernier') or ''}",
                "oui" if row.get("protege") else "non",
                row.get("url") or "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i, col, item)
        self.status.setText(f"{len(rows)} channels, triés par score.")

    def save_selected(self) -> None:
        indexes = sorted({i.row() for i in self.table.selectedIndexes()})
        if not indexes:
            indexes = list(range(len(self.rows)))
        known = {c.get("username") for c in self.state["channels"]}
        added = 0
        for i in indexes:
            if i >= len(self.rows):
                continue
            row = self.rows[i]
            name = row.get("username")
            if not name or name in known:
                continue
            self.state["channels"].append({"username": name, "title": row.get("title") or name})
            known.add(name)
            added += 1
        save_state(self.state)
        self._refresh_alerts_table()
        self.status.setText(f"{added} channel(s) ajoutés aux alertes.")

    def add_channel(self) -> None:
        name = username_of(self.add_url.text())
        if not name:
            return
        if not any(c.get("username") == name for c in self.state["channels"]):
            self.state["channels"].append({"username": name, "title": name})
            save_state(self.state)
        self.add_url.clear()
        self._refresh_alerts_table()

    def remove_channel(self) -> None:
        row = self.alerts.currentRow()
        channels = self.state["channels"]
        if 0 <= row < len(channels):
            name = channels.pop(row).get("username")
            self.state.get("unread", {}).pop(name, None)
            save_state(self.state)
            self._refresh_alerts_table()

    def mark_seen(self) -> None:
        for entry in self.state["channels"]:
            name = entry.get("username")
            if name:
                self.state.setdefault("unread", {})[name] = 0
        save_state(self.state)
        self._refresh_alerts_table()
        self.total.setText("Marqués comme vus.")

    def _show_alerts(self, report: dict) -> None:
        self.state = load_state()
        self._refresh_alerts_table(report.get("lines") or [])
        self.total.setText(
            f"{report.get('total', 0)} nouveaux médias sur ce passage — "
            f"{sum(int(v or 0) for v in self.state.get('unread', {}).values())} non lus au total.")

    def _refresh_alerts_table(self, notes: list | None = None) -> None:
        by_name = {line["username"]: line for line in (notes or [])}
        channels = self.state.get("channels") or []
        self.alerts.setRowCount(len(channels))
        for i, entry in enumerate(channels):
            name = entry.get("username") or ""
            note = by_name.get(name, {})
            values = [
                entry.get("title") or name,
                str(note.get("nouveaux", "")),
                str((self.state.get("unread") or {}).get(name) or 0),
                note.get("note") or "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.alerts.setItem(i, col, item)
