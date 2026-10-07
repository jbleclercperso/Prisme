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

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from .config import APP_DIR

STATE_PATH = APP_DIR / "telegram.json"
SESSION_PATH = APP_DIR / "telegram"
THUMBS = APP_DIR / "telegram-thumbs"
DOWNLOADS = APP_DIR / "telegram-downloads"
URL_RE = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]{4,})", re.I)

STYLE = """
QDialog { background: #101318; color: #e8edf2; }
QLabel { color: #c9d1db; }
QLabel#tgHead { color: #ffffff; font-size: 20px; font-weight: 700; }
QLabel#tgLead { color: #8b97a6; font-size: 12px; }
QLabel#tgStatus { color: #d5e6ff; background: #1a2433; border-radius: 8px; padding: 6px 10px; }
QLabel#tgTotal { color: #9fd0ff; font-size: 14px; font-weight: 600; }
QLineEdit, QTextEdit, QSpinBox {
    background: #0c0f14; border: 1px solid #2a3340; border-radius: 8px;
    color: #eef2f6; padding: 7px 8px; }
QLineEdit:focus, QTextEdit:focus { border-color: #3d7eff; }
QTableWidget, QListWidget {
    background: #0c0f14; border: 1px solid #2a3340; border-radius: 10px;
    color: #e8edf2; }
QHeaderView::section { background: #171d26; color: #9aa6b4; border: none; padding: 8px; }
QTableWidget::item { padding: 4px; }
QTableWidget::item:selected, QListWidget::item:selected { background: #243652; color: white; }
QPushButton { background: #1c2430; border: 1px solid #334052; border-radius: 8px;
              padding: 8px 14px; color: #eef2f6; }
QPushButton:hover { background: #273140; }
QPushButton:disabled { color: #66717e; background: #161b22; }
QPushButton#tgPrimary { background: #2f6fed; border-color: #2f6fed; font-weight: 650; }
QPushButton#tgPrimary:hover { background: #3d7cff; }
QCheckBox { color: #d5dde6; spacing: 6px; }
QTabWidget::pane { border: 1px solid #2a3340; border-radius: 10px; top: -1px; }
QTabBar::tab { background: transparent; color: #9aa6b4; padding: 8px 16px; margin-right: 4px; }
QTabBar::tab:selected { color: white; border-bottom: 2px solid #2f6fed; }
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



VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts", ".wmv"}


def library_keys() -> set[tuple[str, int]]:
    """Nom + taille des vidéos déjà dans les racines Prisme."""
    keys: set[tuple[str, int]] = set()
    try:
        cfg = json.loads((APP_DIR / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return keys
    roots = list(cfg.get("roots") or [])
    if cfg.get("root"):
        roots.append(cfg["root"])
    seen = set()
    for raw in roots:
        root = Path(str(raw))
        if not root.exists():
            continue
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        for path in root.rglob("*"):
            if path.suffix.lower() not in VIDEO_EXT:
                continue
            try:
                keys.add((path.name.lower(), path.stat().st_size))
            except OSError:
                continue
    return keys


def already_in_library(name: str, size: int, keys: set[tuple[str, int]]) -> bool:
    if not name or not size:
        return False
    return (name.lower(), int(size)) in keys



def public_links(query: str) -> tuple[list[dict], str]:
    """Liens t.me et discord.gg déjà publiés. Renvoie aussi pourquoi c'est vide."""
    import urllib.parse, urllib.request
    query = query.strip()
    if not query:
        return [], "Mot-clé vide."
    rows = []
    seen = set()
    notes = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}

    def harvest(html: str) -> None:
        html = urllib.parse.unquote(html)
        for kind, pattern in (
            ("telegram", r"https?://t\.me/(?:s/)?([A-Za-z0-9_]{5,})"),
            ("discord", r"https?://discord\.gg/([A-Za-z0-9-]{4,})"),
        ):
            for name in re.findall(pattern, html):
                if name.lower() in {"joinchat", "share", "addstickers", "telegram"} or name in seen:
                    continue
                seen.add(name)
                if kind == "telegram":
                    rows.append({
                        "title": name, "username": name, "url": f"https://t.me/{name}",
                        "kind": "telegram", "abonnes": 0, "medias": 0, "protege": False,
                        "demande": False, "acces": "lien public", "vivant": "?",
                        "dernier": "publié sur le web", "score": 0,
                    })
                else:
                    rows.append({
                        "title": name, "username": name, "url": f"https://discord.gg/{name}",
                        "kind": "discord", "abonnes": 0, "medias": 0, "protege": False,
                        "demande": True, "acces": "invitation", "vivant": "?",
                        "dernier": "publié sur le web", "score": 0,
                    })

    searches = [
        ("Bing", "https://www.bing.com/search?" + urllib.parse.urlencode({"q": f"site:t.me {query}"})),
        ("Bing Discord", "https://www.bing.com/search?" + urllib.parse.urlencode({"q": f"discord.gg {query}"})),
        ("DuckDuckGo", "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": f"site:t.me {query}"})),
    ]
    for name, url in searches:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", "replace")
        except Exception as exc:
            notes.append(f"{name} injoignable")
            continue
        if "anomaly" in html.lower() or "captcha" in html.lower():
            notes.append(f"{name} a bloqué le script")
            continue
        before = len(rows)
        harvest(html)
        if len(rows) == before:
            notes.append(f"{name} : aucun lien")
    if not rows:
        return [], "Aucun lien. " + " ; ".join(notes) + ". Colle des liens depuis tgstat.com."
    return rows, ""


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
    bank_done = Signal(list)
    file_ready = Signal(str)
    batch_done = Signal(int)
    failed = Signal(str)

    def __init__(self, state: dict):
        super().__init__()
        self.state = state
        self.job = "login"
        self.query = ""
        self.pasted: list[str] = []
        self.min_subs = 0
        self.min_medias = 5
        self.only_open = True
        self.channel = ""
        self.before_id = 0
        self.message_id = 0
        self.message_ids: list[int] = []
        self.skip_known = True
        self.known: set[tuple[str, int]] = set()
        self.skipped = 0
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
            elif self.job == "content":
                rows = await self._content(client)
                self.search_done.emit(rows)
            elif self.job == "dialogs":
                rows = await self._dialogs(client)
                self.search_done.emit(rows)
            elif self.job == "bank":
                items = await self._bank(client, FloodWaitError)
                self.bank_done.emit(items)
            elif self.job == "download":
                path = await self._download(client)
                self.file_ready.emit(path or "")
            elif self.job == "download_many":
                n = await self._download_many(client)
                self.batch_done.emit(n)
            elif self.job == "follow":
                n = await self._follow(client)
                self.batch_done.emit(n)
            elif self.job == "export":
                n = await self._export(client)
                self.batch_done.emit(n)
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
            "demande": bool(getattr(chat, "join_request", False)),
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


    async def _content(self, client) -> list:
        """Posts publics qui contiennent le mot, avec le lien du message."""
        from telethon.tl.functions.messages import SearchGlobalRequest
        from telethon.tl.types import Channel, InputMessagesFilterEmpty, InputPeerEmpty
        query = self.query.strip()
        rows = []
        seen = set()
        offset_rate, offset_peer, offset_id = 0, InputPeerEmpty(), 0
        for _page in range(6):
            try:
                page = await client(SearchGlobalRequest(
                    q=query, filter=InputMessagesFilterEmpty(),
                    min_date=None, max_date=None,
                    offset_rate=offset_rate, offset_peer=offset_peer,
                    offset_id=offset_id, limit=50,
                ))
            except Exception as exc:
                self.status.emit(str(exc)[:80])
                break
            chats = {c.id: c for c in page.chats if isinstance(c, Channel)}
            if not page.messages:
                break
            for msg in page.messages:
                chat = chats.get(getattr(msg.peer_id, "channel_id", 0))
                if chat is None or not getattr(chat, "username", None):
                    continue
                key = (chat.username, msg.id)
                if key in seen:
                    continue
                seen.add(key)
                text = (msg.message or "").replace("\n", " ")[:80]
                rows.append({
                    "title": chat.title or chat.username,
                    "username": chat.username,
                    "url": f"https://t.me/{chat.username}/{msg.id}",
                    "kind": "message",
                    "abonnes": int(getattr(chat, "participants_count", 0) or 0),
                    "medias": 1 if (msg.video or msg.photo or msg.file) else 0,
                    "protege": bool(getattr(chat, "noforwards", False)),
                    "demande": bool(getattr(chat, "join_request", False)),
                    "acces": "à demander" if getattr(chat, "join_request", False) else "entrée libre",
                    "vivant": "?",
                    "dernier": text,
                    "score": 1,
                })
            last = page.messages[-1]
            offset_id = last.id
            when = getattr(last, "date", None)
            offset_rate = int(when.timestamp()) if when else 0
            offset_peer = await client.get_input_entity(last.peer_id)
            self.status.emit(f"{len(rows)} messages publics")
        return rows

    async def _collect(self, client, SearchRequest, Channel, flood, seen: dict) -> None:
        """Élargit au-delà d'une page : recherche contacts + recherche globale."""
        from telethon.tl.functions.messages import SearchGlobalRequest
        from telethon.tl.types import InputMessagesFilterEmpty, InputPeerEmpty
        query = self.query.strip()
        words = [query] + [w for w in query.split() if len(w) > 2 and w != query]
        for word in words[:4]:
            self.status.emit(f"Recherche : {word}")
            try:
                found = await client(SearchRequest(q=word, limit=100))
                for chat in found.chats:
                    if isinstance(chat, Channel) and chat.username:
                        seen[str(chat.id)] = chat
            except flood as exc:
                await asyncio.sleep(min(exc.seconds, 25))
            offset_rate, offset_peer, offset_id = 0, InputPeerEmpty(), 0
            for _page in range(4):
                try:
                    page = await client(SearchGlobalRequest(
                        q=word, filter=InputMessagesFilterEmpty(),
                        min_date=None, max_date=None,
                        offset_rate=offset_rate, offset_peer=offset_peer,
                        offset_id=offset_id, limit=50,
                    ))
                except flood as exc:
                    await asyncio.sleep(min(exc.seconds, 25))
                    break
                except Exception:
                    break
                for chat in page.chats:
                    if isinstance(chat, Channel) and getattr(chat, "username", None):
                        seen[str(chat.id)] = chat
                if not page.messages:
                    break
                last = page.messages[-1]
                offset_rate = getattr(last, "date", None)
                offset_rate = int(offset_rate.timestamp()) if offset_rate else 0
                offset_id = last.id
                offset_peer = await client.get_input_entity(last.peer_id)
                self.status.emit(f"{len(seen)} channels publics trouvés")

    async def _search(self, client, SearchRequest, Channel, video_filter, flood) -> list:
        seen: dict[str, object] = {}
        if self.query.strip():
            await self._collect(client, SearchRequest, Channel, flood, seen)
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
            # On garde tout ce qui a un lien : entrée libre ET demande d'acceptation.
            # Le privé sans username n'est pas trouvable. Le filtre se fait après.
            if not row["username"]:
                continue
            row["acces"] = "à demander" if row["demande"] else "entrée libre"
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




    async def _entity(self, client):
        raw = str(self.channel)
        if raw.lstrip("-").isdigit():
            from telethon.tl.types import PeerChannel
            return await client.get_entity(PeerChannel(int(raw)))
        return raw

    async def _dialogs(self, client) -> list:
        rows = []
        async for dialog in client.iter_dialogs():
            ent = dialog.entity
            if not (getattr(ent, "megagroup", False) or getattr(ent, "broadcast", False) or getattr(ent, "gigagroup", False)):
                continue
            username = getattr(ent, "username", None) or ""
            rows.append({
                "title": dialog.name or username or str(ent.id),
                "username": username or str(ent.id),
                "peer": str(ent.id),
                "url": f"https://t.me/{username}" if username else "",
                "kind": "groupe" if getattr(ent, "megagroup", False) else "channel",
                "abonnes": int(getattr(ent, "participants_count", 0) or 0),
                "medias": 0,
                "protege": bool(getattr(ent, "noforwards", False)),
                "demande": False,
                "acces": "déjà dedans" if not username else "déjà dedans, public",
                "vivant": "?",
                "dernier": "",
                "score": int(getattr(ent, "participants_count", 0) or 0),
            })
        rows.sort(key=lambda r: r["title"].lower())
        return rows

    async def _bank(self, client, flood) -> list:
        """Les médias du channel, lus dans Telegram, vignette en local."""
        THUMBS.mkdir(parents=True, exist_ok=True)
        items = []
        kwargs = {"limit": 36}
        if self.before_id:
            kwargs["offset_id"] = self.before_id
        try:
            async for msg in client.iter_messages(await self._entity(client), **kwargs):
                if not (msg.video or msg.photo or msg.file):
                    continue
                thumb = THUMBS / f"{self.channel}_{msg.id}.jpg"
                if not thumb.exists():
                    try:
                        got = await msg.download_media(file=thumb, thumb=-1)
                        if not got and msg.photo:
                            await msg.download_media(file=thumb)
                    except flood as exc:
                        await asyncio.sleep(min(exc.seconds, 15))
                    except Exception:
                        pass
                name = ""
                size = 0
                mime = ""
                if msg.file:
                    name = msg.file.name or ""
                    size = msg.file.size or 0
                    mime = msg.file.mime_type or ""
                items.append({
                    "id": msg.id,
                    "username": self.channel,
                    "date": msg.date.strftime("%d/%m %H:%M") if msg.date else "",
                    "title": (msg.text or name or mime or "média")[:140],
                    "size": size,
                    "mime": mime,
                    "video": bool(msg.video) or mime.startswith("video"),
                    "thumb": str(thumb) if thumb.exists() else "",
                })
        except flood as exc:
            self.status.emit(f"Telegram demande {exc.seconds} s.")
        return items

    async def _download(self, client) -> str:
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        msg = await client.get_messages(await self._entity(client), ids=self.message_id)
        if msg is None:
            return ""
        name = (msg.file.name if msg.file else "") or ""
        size = (msg.file.size if msg.file else 0) or 0
        if self.skip_known and already_in_library(name, size, self.known):
            self.skipped += 1
            self.status.emit(f"Déjà dans la bibliothèque : {name or msg.id}")
            return ""
        folder = DOWNLOADS / self.channel
        folder.mkdir(parents=True, exist_ok=True)
        path = await msg.download_media(file=folder)
        if path and name and size:
            self.known.add((name.lower(), int(size)))
        return str(path or "")

    async def _download_many(self, client) -> int:
        if self.skip_known and not self.known:
            self.status.emit("Lecture de la bibliothèque…")
            self.known = library_keys()
        n = 0
        self.skipped = 0
        for mid in self.message_ids:
            self.message_id = mid
            self.status.emit(f"Téléchargement {n + 1}/{len(self.message_ids)}")
            if await self._download(client):
                n += 1
            await asyncio.sleep(0.4)
        self.status.emit(f"{n} nouveaux, {self.skipped} déjà présents")
        return n




    async def _export(self, client) -> int:
        """Photos et vidéos du channel, sans reprendre ce qui est déjà sur le disque."""
        if not self.known:
            self.known = library_keys()
        n = 0
        self.skipped = 0
        self.channel = self.channel
        async for msg in client.iter_messages(await self._entity(client), limit=None):
            if not (msg.video or msg.photo or (msg.file and (msg.file.mime_type or "").startswith(("video", "image")))):
                continue
            self.message_id = msg.id
            if await self._download(client):
                n += 1
            if n and n % 20 == 0:
                self.status.emit(f"{n} nouveaux, {self.skipped} déjà présents")
            await asyncio.sleep(0.3)
        self.status.emit(f"Export fini : {n} nouveaux, {self.skipped} déjà sur le disque")
        return n


    async def _follow(self, client) -> int:
        """Télécharge les vidéos nouvelles des chaînes suivies."""
        if not self.known:
            self.known = library_keys()
        n = 0
        self.skipped = 0
        for entry in self.state.get("channels") or []:
            if not entry.get("follow"):
                continue
            username = entry.get("username") or ""
            if not username:
                continue
            last = int((self.state.get("last_id") or {}).get(username) or 0)
            max_id = last
            fresh = []
            async for msg in client.iter_messages(username, min_id=last, limit=40):
                max_id = max(max_id, msg.id)
                if msg.video or (msg.file and (msg.file.mime_type or "").startswith("video")):
                    fresh.append(msg.id)
            self.state.setdefault("last_id", {})[username] = max_id
            if last == 0:
                # Premier passage : on arme, on ne vide pas l'historique.
                continue
            self.channel = username
            for mid in reversed(fresh):
                self.message_id = mid
                if await self._download(client):
                    n += 1
                await asyncio.sleep(0.4)
        save_state(self.state)
        return n


class TelegramDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Prisme — Telegram")
        self.setStyleSheet(STYLE)
        self.resize(1080, 700)
        self.state = load_state()
        self.worker: TelegramWorker | None = None
        self.rows: list[dict] = []
        box = QVBoxLayout(self)
        head = QLabel("Telegram", self)
        head.setObjectName("tgHead")
        box.addWidget(head)
        lead = QLabel(
            "Compte perso. La recherche Telegram est un échantillon : colle des liens t.me "
            "pour élargir. Discord ne se cherche pas par mot-clé, seulement par invitation.",
            self)
        lead.setObjectName("tgLead")
        lead.setWordWrap(True)
        box.addWidget(lead)
        self.status = QLabel("Pas encore connecté.", self)
        self.status.setObjectName("tgStatus")
        box.addWidget(self.status)
        tabs = QTabWidget(self)
        tabs.addTab(self._account_tab(), "Compte")
        tabs.addTab(self._search_tab(), "Recherche")
        tabs.addTab(self._joined_tab(), "Mes groupes")
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
        self.src_tg = QCheckBox("Telegram", page)
        self.src_tg.setChecked(True)
        row.addWidget(self.src_tg)
        self.src_dc = QCheckBox("Discord", page)
        self.src_dc.setChecked(False)
        row.addWidget(self.src_dc)
        self.query = QLineEdit(page)
        self.query.setPlaceholderText("mot-clé Telegram, ou invitations Discord")
        row.addWidget(self.query, 1)
        self.min_subs = QSpinBox(page)
        self.min_subs.setRange(0, 1_000_000)
        self.min_subs.setPrefix("min abonnés ")
        row.addWidget(self.min_subs)
        self.min_medias = QSpinBox(page)
        self.min_medias.setRange(0, 40)
        self.min_medias.setValue(5)
        self.min_medias.setPrefix("min médias ")
        row.addWidget(self.min_medias)
        go = QPushButton("Chercher", page)
        go.setObjectName("tgPrimary")
        go.clicked.connect(self.search)
        row.addWidget(go)
        web = QPushButton("Dans les messages", page)
        web.clicked.connect(lambda: self._start("content"))
        row.addWidget(web)
        self.min_subs.valueChanged.connect(lambda _v: self._apply_filter())
        self.min_medias.valueChanged.connect(lambda _v: self._apply_filter())
        lay.addLayout(row)
        self.pasted = QTextEdit(page)
        self.pasted.setPlaceholderText("Telegram : une URL t.me par ligne. Discord : une invitation discord.gg/… par ligne")
        self.pasted.setFixedHeight(70)
        lay.addWidget(self.pasted)
        filt = QHBoxLayout()
        self.f_free = QCheckBox("entrée libre", page)
        self.f_free.setChecked(True)
        self.f_ask = QCheckBox("à demander", page)
        self.f_ask.setChecked(True)
        self.f_hide_protected = QCheckBox("masquer les protégés", page)
        self.f_hide_protected.setChecked(True)
        self.f_alive = QCheckBox("vivants seulement", page)
        for box in (self.f_free, self.f_ask, self.f_hide_protected, self.f_alive):
            box.toggled.connect(self._apply_filter)
            filt.addWidget(box)
        filt.addStretch(1)
        lay.addLayout(filt)
        self.table = QTableWidget(0, 9, page)
        self.table.setHorizontalHeaderLabels(
            ["Score", "Titre", "Accès", "Type", "Abonnés", "Médias /40", "Activité", "Protégé", "Lien"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.table, 1)
        row2 = QHBoxLayout()
        keep = QPushButton("Enregistrer la sélection dans les alertes", page)
        keep.clicked.connect(self.save_selected)
        row2.addWidget(keep)
        bank = QPushButton("Ouvrir la banque de médias", page)
        bank.setObjectName("tgPrimary")
        bank.clicked.connect(self.open_selected_bank)
        row2.addWidget(bank)
        exp = QPushButton("Exporter photos et vidéos", page)
        exp.clicked.connect(self.export_selected)
        row2.addWidget(exp)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.table.itemDoubleClicked.connect(lambda _item: self.open_selected_bank())
        return page


    def _joined_tab(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lead = QLabel("Les groupes et chaînes où ton compte est déjà entré, publics ou privés. "
                      "Ouvre la banque, ou exporte photos et vidéos. Ce qui est déjà sur le disque est sauté.", page)
        lead.setObjectName("tgLead")
        lead.setWordWrap(True)
        lay.addWidget(lead)
        self.joined = QTableWidget(0, 4, page)
        self.joined.setHorizontalHeaderLabels(["Titre", "Accès", "Type", "Lien"])
        self.joined.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.joined.setSelectionBehavior(QTableWidget.SelectRows)
        lay.addWidget(self.joined, 1)
        row = QHBoxLayout()
        load = QPushButton("Charger mes groupes", page)
        load.setObjectName("tgPrimary")
        load.clicked.connect(lambda: self._start("dialogs"))
        row.addWidget(load)
        bank = QPushButton("Ouvrir la banque", page)
        bank.clicked.connect(self.open_joined_bank)
        row.addWidget(bank)
        exp = QPushButton("Exporter photos et vidéos", page)
        exp.clicked.connect(self.export_joined)
        row.addWidget(exp)
        row.addStretch(1)
        lay.addLayout(row)
        self.joined.itemDoubleClicked.connect(lambda _i: self.open_joined_bank())
        return page

    def _joined_peer(self) -> str:
        row = self.joined.currentRow()
        rows = getattr(self, "joined_rows", [])
        if not (0 <= row < len(rows)):
            return ""
        return rows[row].get("peer") or rows[row].get("username") or ""

    def open_joined_bank(self) -> None:
        peer = self._joined_peer()
        if not peer:
            self.status.setText("Choisis un groupe dans la liste.")
            return
        self.open_bank(peer)

    def export_joined(self) -> None:
        peer = self._joined_peer()
        if not peer:
            self.status.setText("Choisis un groupe dans la liste.")
            return
        self._export_channel(peer)

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
        follow = QPushButton("Suivre et télécharger", page)
        follow.setObjectName("tgPrimary")
        follow.clicked.connect(self.toggle_follow)
        row.addWidget(follow)
        bank = QPushButton("Ouvrir la banque", page)
        bank.setObjectName("tgPrimary")
        bank.clicked.connect(self.open_alert_bank)
        row.addWidget(bank)
        row.addStretch(1)
        lay.addLayout(row)
        self.alerts.itemDoubleClicked.connect(lambda _item: self.open_alert_bank())
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
        self.worker.min_medias = self.min_medias.value()
        self.worker.only_open = True
        self.worker.status.connect(self.status.setText)
        self.worker.failed.connect(self._failed)
        self.worker.need_code.connect(self._ask_code)
        self.worker.need_password.connect(self._ask_password)
        self.worker.search_done.connect(self._show_search)
        self.worker.alerts_done.connect(self._show_alerts)
        self.worker.bank_done.connect(self._open_bank)
        self.worker.file_ready.connect(self._play)
        self.worker.batch_done.connect(self._batch_done)
        self.worker.start()

    def _login(self) -> None:
        self._start("login")

    def search_public(self) -> None:
        rows, why = public_links(self.query.text())
        if not rows:
            self.status.setText(why)
            return
        self.found = rows
        self._apply_filter()
        self.status.setText(f"{len(rows)} liens déjà publiés sur le web. Ouvre la banque pour les noter.")

    def search(self) -> None:
        if self.src_dc.isChecked():
            self._discord_invites()
        if self.src_tg.isChecked():
            self._start("search")
        elif not self.src_dc.isChecked():
            self.status.setText("Coche Telegram, Discord, ou les deux.")

    def _discord_invites(self) -> None:
        import json, urllib.request
        rows = []
        lines = [self.query.text()] + self.pasted.toPlainText().splitlines()
        for raw in lines:
            raw = raw.strip()
            if "discord.gg/" not in raw and "discord.com/invite/" not in raw:
                continue
            code = raw.rstrip("/").split("/")[-1].split("?")[0]
            if not code:
                continue
            try:
                req = urllib.request.Request(
                    f"https://discord.com/api/v10/invites/{code}?with_counts=true",
                    headers={"User-Agent": "Prisme"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
            except Exception as exc:
                rows.append({"title": code, "username": code, "url": raw, "kind": "discord",
                             "abonnes": 0, "medias": 0, "protege": False, "demande": True,
                             "acces": "invitation", "vivant": "?", "dernier": str(exc)[:40],
                             "score": 0})
                continue
            guild = data.get("guild") or {}
            rows.append({
                "title": guild.get("name") or code,
                "username": code,
                "url": f"https://discord.gg/{code}",
                "kind": "discord",
                "abonnes": int(data.get("approximate_member_count") or 0),
                "medias": 0,
                "protege": False,
                "demande": True,
                "acces": "invitation",
                "vivant": "?",
                "dernier": f"{data.get('approximate_presence_count') or 0} en ligne",
                "score": int(data.get("approximate_member_count") or 0),
            })
        if not rows:
            if not self.src_tg.isChecked():
                self.status.setText("Discord n'a pas de recherche par mot-clé. Colle des invitations discord.gg/…")
            return
        self.found = list(getattr(self, "found", [])) + rows
        self._apply_filter()

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
        if rows and rows[0].get("acces", "").startswith("déjà dedans"):
            self.joined_rows = rows
            self.joined.setRowCount(len(rows))
            for i, row in enumerate(rows):
                values = [row.get("title") or "", row.get("acces") or "", row.get("kind") or "", row.get("url") or "privé"]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.joined.setItem(i, col, item)
            self.status.setText(f"{len(rows)} groupes où tu es déjà entré.")
            return
        self.found = rows
        self._apply_filter()

    def _apply_filter(self) -> None:
        if not hasattr(self, "table"):
            return
        rows = []
        min_subs = self.min_subs.value()
        min_medias = self.min_medias.value()
        for row in getattr(self, "found", []):
            ask = bool(row.get("demande"))
            if ask and not self.f_ask.isChecked():
                continue
            if not ask and not self.f_free.isChecked():
                continue
            if self.f_hide_protected.isChecked() and row.get("protege"):
                continue
            if self.f_alive.isChecked() and row.get("vivant") == "endormi":
                continue
            if min_subs and (row.get("abonnes") or 0) < min_subs:
                continue
            if (row.get("medias") or 0) < min_medias:
                continue
            rows.append(row)
        self.rows = rows
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            values = [
                str(row.get("score") or 0),
                row.get("title") or "",
                row.get("acces") or ("à demander" if row.get("demande") else "entrée libre"),
                row.get("kind") or "",
                f"{int(row.get('abonnes') or 0):,}".replace(",", " "),
                str(row.get("medias") or 0),
                f"{row.get('vivant') or '?'} · {row.get('dernier') or ''}",
                "oui" if row.get("protege") else "non",
                row.get("url") or "",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(i, col, item)
        self.status.setText(f"{len(rows)} affichés sur {len(getattr(self, 'found', []))} trouvés.")

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

    def toggle_follow(self) -> None:
        row = self.alerts.currentRow()
        channels = self.state.get("channels") or []
        if not (0 <= row < len(channels)):
            self.status.setText("Choisis une chaîne dans les alertes.")
            return
        channels[row]["follow"] = not channels[row].get("follow")
        save_state(self.state)
        self._refresh_alerts_table()
        self.status.setText("Suivi activé : les nouvelles vidéos se téléchargent à l'ouverture de Prisme."
                            if channels[row]["follow"] else "Suivi retiré.")

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
                ("↓ " if entry.get("follow") else "") + (entry.get("title") or name),
                str(note.get("nouveaux", "")),
                str((self.state.get("unread") or {}).get(name) or 0),
                "suivi" if entry.get("follow") else (note.get("note") or ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.alerts.setItem(i, col, item)


    def _selected_username(self) -> str:
        indexes = sorted({i.row() for i in self.table.selectedIndexes()})
        if not indexes or indexes[0] >= len(self.rows):
            return ""
        return self.rows[indexes[0]].get("username") or ""

    def export_selected(self) -> None:
        name = self._selected_username()
        if not name:
            self.status.setText("Choisis un channel dans le tableau.")
            return
        self._export_channel(name)

    def _export_channel(self, username: str) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("Une opération Telegram est déjà en cours.")
            return
        self._save_account()
        self.worker = TelegramWorker(self.state)
        self.worker.job = "export"
        self.worker.channel = username
        self.worker.skip_known = True
        self.worker.known = library_keys()
        self.worker.status.connect(self.status.setText)
        self.worker.failed.connect(self._failed)
        self.worker.need_code.connect(self._ask_code)
        self.worker.need_password.connect(self._ask_password)
        self.worker.batch_done.connect(self._batch_done)
        self.worker.start()
        self.status.setText(f"Export de {username}…")

    def open_selected_bank(self) -> None:
        name = self._selected_username()
        if not name:
            self.status.setText("Choisis un channel dans le tableau.")
            return
        self.open_bank(name)

    def open_alert_bank(self) -> None:
        row = self.alerts.currentRow()
        channels = self.state.get("channels") or []
        if not (0 <= row < len(channels)):
            self.status.setText("Choisis un channel dans les alertes.")
            return
        self.open_bank(channels[row].get("username") or "")

    def open_bank(self, username: str, before_id: int = 0) -> None:
        if not username:
            return
        self._bank_user = username
        self._bank_before = before_id
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("Une opération Telegram est déjà en cours.")
            return
        self._save_account()
        self.worker = TelegramWorker(self.state)
        self.worker.job = "bank"
        self.worker.channel = username
        self.worker.before_id = before_id
        self.worker.status.connect(self.status.setText)
        self.worker.failed.connect(self._failed)
        self.worker.need_code.connect(self._ask_code)
        self.worker.need_password.connect(self._ask_password)
        self.worker.bank_done.connect(self._open_bank)
        self.worker.start()
        self.status.setText(f"Lecture de {username}…")

    def _open_bank(self, items: list) -> None:
        if not items and not getattr(self, "_bank_window", None):
            self.status.setText("Aucun média lisible dans ce channel (privé, protégé, ou vide).")
            return
        window = getattr(self, "_bank_window", None)
        if window is None or not window.isVisible() or window.username != getattr(self, "_bank_user", ""):
            window = MediaBank(self, getattr(self, "_bank_user", ""))
            self._bank_window = window
        window.add_items(items)
        window.show()
        window.raise_()
        window.activateWindow()

    def download_and_play(self, username: str, message_id: int) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("Attends la fin de l'opération en cours.")
            return
        self.worker = TelegramWorker(self.state)
        self.worker.job = "download"
        self.worker.channel = username
        self.worker.message_id = message_id
        self.worker.failed.connect(self._failed)
        self.worker.file_ready.connect(self._play)
        self.worker.batch_done.connect(self._batch_done)
        self.worker.start()
        self.status.setText("Téléchargement du média…")

    def _play(self, path: str) -> None:
        if not path:
            self.status.setText("Téléchargement impossible.")
            return
        self.status.setText(path)
        Player(self, path).exec()

    def download_many(self, username: str, ids: list[int]) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("Attends la fin de l'opération en cours.")
            return
        self.worker = TelegramWorker(self.state)
        self.worker.job = "download_many"
        self.worker.channel = username
        self.worker.message_ids = list(ids)
        bank = getattr(self, "_bank_window", None)
        self.worker.skip_known = bool(getattr(bank, "skip_known", None) and bank.skip_known.isChecked())
        self.worker.status.connect(self.status.setText)
        self.worker.failed.connect(self._failed)
        self.worker.batch_done.connect(self._batch_done)
        self.worker.start()

    def _batch_done(self, n: int) -> None:
        skipped = getattr(self.worker, "skipped", 0)
        self.status.setText(f"{n} nouveau(x), {skipped} déjà dans la bibliothèque. Dossier : {DOWNLOADS}")


class MediaBank(QDialog):
    """La banque du channel : vignettes et fiches, sans quitter Prisme."""

    def __init__(self, owner, username: str):
        super().__init__(owner)
        self.owner = owner
        self.username = username
        self.items: list[dict] = []
        self.setWindowTitle(f"Banque — {username}")
        self.setStyleSheet(STYLE)
        self.resize(980, 680)
        box = QVBoxLayout(self)
        head = QLabel(f"t.me/{username}", self)
        head.setObjectName("tgHead")
        box.addWidget(head)
        lead = QLabel("Coche les vignettes. Ce qui est déjà dans la bibliothèque, même nom et même taille, n'est pas retéléchargé.", self)
        lead.setObjectName("tgLead")
        box.addWidget(lead)
        self.grid = QListWidget(self)
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(QSize(220, 124))
        self.grid.setUniformItemSizes(False)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setSpacing(8)
        self.grid.setWordWrap(True)
        self.grid.itemDoubleClicked.connect(self._open)
        self.grid.currentRowChanged.connect(self._detail)
        box.addWidget(self.grid, 1)
        self.detail = QLabel("Choisis un média.", self)
        self.detail.setWordWrap(True)
        box.addWidget(self.detail)
        row = QHBoxLayout()
        more = QPushButton("Charger la suite", self)
        more.clicked.connect(self._more)
        row.addWidget(more)
        play = QPushButton("Lire dans Prisme", self)
        play.setObjectName("tgPrimary")
        play.clicked.connect(self._open_current)
        row.addWidget(play)
        self.skip_known = QCheckBox("Sauter ce qui est déjà dans la bibliothèque", self)
        self.skip_known.setChecked(True)
        row.addWidget(self.skip_known)
        bulk = QPushButton("Télécharger les cochés", self)
        bulk.clicked.connect(self._download_checked)
        row.addWidget(bulk)
        all_ = QPushButton("Tout cocher", self)
        all_.clicked.connect(lambda: self._check_all(True))
        row.addWidget(all_)
        none = QPushButton("Tout décocher", self)
        none.clicked.connect(lambda: self._check_all(False))
        row.addWidget(none)
        row.addStretch(1)
        box.addLayout(row)

    def add_items(self, items: list) -> None:
        known = {item["id"] for item in self.items}
        for item in items:
            if item["id"] in known:
                continue
            self.items.append(item)
            label = f"{item.get('date') or ''}\n{(item.get('title') or '')[:42]}"
            row = QListWidgetItem(label)
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Unchecked)
            thumb = item.get("thumb") or ""
            if thumb:
                pix = QPixmap(thumb)
                if not pix.isNull():
                    row.setIcon(QIcon(pix.scaled(220, 124, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)))
            row.setData(Qt.UserRole, item)
            self.grid.addItem(row)
        self.setWindowTitle(f"Banque — {self.username} ({len(self.items)})")

    def _current(self):
        row = self.grid.currentItem()
        return row.data(Qt.UserRole) if row is not None else None

    def _detail(self, _row: int) -> None:
        item = self._current()
        if not item:
            return
        size = item.get("size") or 0
        mo = f"{size / 1048576:.1f} Mo" if size else "?"
        self.detail.setText(
            f"{item.get('date') or ''}  ·  {mo}  ·  {item.get('mime') or ''}\n{item.get('title') or ''}")

    def _more(self) -> None:
        if not self.items:
            return
        oldest = min(item["id"] for item in self.items)
        self.owner.open_bank(self.username, before_id=oldest)

    def _open_current(self) -> None:
        self._open(self.grid.currentItem())

    def _open(self, row) -> None:
        if row is None:
            return
        item = row.data(Qt.UserRole)
        if item:
            self.owner.download_and_play(self.username, item["id"])

    def _download_checked(self) -> None:
        ids = []
        for i in range(self.grid.count()):
            row = self.grid.item(i)
            if row.checkState() == Qt.Checked:
                item = row.data(Qt.UserRole) or {}
                if item.get("id"):
                    ids.append(item["id"])
        if not ids:
            self.detail.setText("Coche au moins une vignette.")
            return
        self.owner.download_many(self.username, ids)

    def _check_all(self, on: bool) -> None:
        state = Qt.Checked if on else Qt.Unchecked
        for i in range(self.grid.count()):
            self.grid.item(i).setCheckState(state)


class Player(QDialog):
    def __init__(self, parent, path: str):
        super().__init__(parent)
        self.setWindowTitle(Path(path).name)
        self.resize(900, 560)
        self.setStyleSheet(STYLE)
        box = QVBoxLayout(self)
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
            screen = QVideoWidget(self)
            box.addWidget(screen, 1)
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.player.setVideoOutput(screen)
            self.player.setSource(path if hasattr(self.player, "setSource") else path)
            from PySide6.QtCore import QUrl
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()
        except Exception as exc:  # noqa: BLE001
            box.addWidget(QLabel(f"Lu, mais le lecteur n'a pas démarré ({exc}).\n{path}", self))
        close = QPushButton("Fermer", self)
        close.clicked.connect(self.accept)
        box.addWidget(close)

    def reject(self) -> None:
        player = getattr(self, "player", None)
        if player is not None:
            player.stop()
        super().reject()


def start_follow(window=None) -> None:
    """Au lancement de Prisme : télécharge les nouveautés des chaînes suivies."""
    state = load_state()
    if not any(c.get("follow") for c in state.get("channels") or []):
        return
    if not state.get("api_id") or not state.get("api_hash"):
        return
    worker = TelegramWorker(state)
    worker.job = "follow"
    worker.skip_known = True
    worker.known = library_keys()

    def done(n: int) -> None:
        if window is not None and n:
            try:
                window.show_banner(f"Telegram : {n} nouvelle(s) vidéo(s) téléchargée(s).", "done")
            except Exception:
                pass

    worker.batch_done.connect(done)
    worker.start()
    if window is not None:
        window._telegram_follow_worker = worker
