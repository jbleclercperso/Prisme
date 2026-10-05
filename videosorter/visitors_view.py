"""Les fiches des visiteurs : une par personne, tout ce qu'on sait d'elle.

Un onglet de « Partage à distance ». Une personne, c'est un profil de la page
mobile (prénom, email, préférences, `profils.py`) et les appareils qu'il a
réunis ; un appareil venu sans profil a sa propre fiche. Les chiffres viennent
des journaux du PC et du NAS (`access.summarize`) : connexions, dernière
venue et son adresse, vidéos et temps de visionnage, favoris -- et le
compteur du bon d'achat, le parrainage, le code de verrouillage.
"""
from __future__ import annotations

import hashlib
import time

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from . import profils
from .access import spell, when

STYLE = """
QFrame#visitorCard { background: #141a22; border: 1px solid #232c38; border-radius: 12px; }
QFrame#visitorCard[online="true"] { border-color: #2f7d4f; }
QLabel#vName { color: #ffffff; font-size: 15px; font-weight: 700; }
QLabel#vSub { color: #8b94a1; font-size: 12px; }
QLabel#vInfo { color: #aab4c0; font-size: 12px; }
QLabel#vPill { border-radius: 10px; padding: 3px 9px; font-size: 11px; font-weight: 600; }
QLabel#vPill[online="true"] { background: #173524; color: #7bd88f; }
QLabel#vPill[online="false"] { background: #1b212a; color: #8b94a1; }
QFrame#vTile { background: #10141a; border: 1px solid #1f2731; border-radius: 9px; }
QLabel#vNum { color: #ffffff; font-size: 18px; font-weight: 700; }
QLabel#vCap { color: #8b94a1; font-size: 11px; }
QLabel#vHead { color: #cdd5df; font-size: 12px; font-weight: 600; }
QLabel#vChip { background: #1d2a40; color: #dbe7ff; border-radius: 10px; padding: 3px 9px;
               font-size: 11px; }
QLabel#vDim { color: #8b94a1; font-size: 12px; }
QProgressBar#vGift { background: #1d2530; border: 0; border-radius: 4px; height: 8px; }
QProgressBar#vGift::chunk { border-radius: 4px; background: qlineargradient(x1:0, y1:0,
    x2:1, y2:0, stop:0 #e8c068, stop:1 #c98a3a); }
QListWidget#vRecent { background: #10141a; border: 1px solid #1f2731; border-radius: 8px;
                      color: #cdd5df; font-size: 12px; }
QListWidget#vRecent::item { padding: 3px 6px; }
QPushButton#vButton { background: #1b222c; border: 1px solid #2b3542; border-radius: 6px;
                      padding: 5px 11px; color: #cdd5df; font-size: 12px; }
QPushButton#vButton:hover { background: #232c38; }
"""

AVATAR_COLORS = ("#3a6fe0", "#8a4fe8", "#d0569a", "#e0884a", "#3aa57a", "#2f9fc0",
                 "#c0a030", "#7a68d8")


def ago(moment: float) -> str:
    """« il y a 5 min », « il y a 3 h », « il y a 2 j »."""
    if not moment:
        return "jamais"
    gap = max(0, time.time() - moment)
    if gap < 60:
        return "à l'instant"
    if gap < 3600:
        return f"il y a {int(gap // 60)} min"
    if gap < 86400:
        return f"il y a {int(gap // 3600)} h"
    return f"il y a {int(gap // 86400)} j"


# -- rassembler : une personne, ses appareils, ses chiffres -------------------
def _person(where: str, labels: list, summary: dict, profile: dict | None, key: str,
            path, profiles: dict, aliases: dict) -> dict:
    videos: dict = {}
    recent, favorites = [], []
    visits, last, seconds, ip, ip_at = 0, 0.0, 0.0, "", 0.0
    for label in labels:
        one = summary.get(label)
        if not one:
            continue
        visits += one["visits"]
        last = max(last, one["last"])
        seconds += one["seconds"]
        if one["ip_at"] > ip_at:
            ip, ip_at = one["ip"], one["ip_at"]
        for video, spent in one["videos"].items():
            videos[video] = videos.get(video, 0.0) + spent
        recent += one["recent"]
        favorites += one["favorites"]
    recent.sort(key=lambda row: -row[0])
    favorites.sort(key=lambda row: -row[0])
    kept, marks = [], set()
    for row in favorites:
        if row[2] not in marks:
            marks.add(row[2])
            kept.append(row)
    profile = profile or {}
    friends = [one for one in profiles.values() if key and one.get("parrain") == key]
    godparent = profiles.get(profile.get("parrain") or "") or {}
    name = profile.get("name") or (aliases.get(labels[0]) if labels else "") or (
        labels[0] if labels else "Visiteur")
    return {
        "name": name, "email": profile.get("email", ""), "likes": profile.get("likes") or [],
        "labels": labels, "where": where, "key": key, "path": path,
        "has_profile": bool(key), "pin": bool(profile.get("pin_hash")),
        "joined": profile.get("at", 0.0), "bon_at": profile.get("bon_at", 0.0),
        "visits": visits, "last": last, "ip": ip, "seconds": seconds,
        "watched": len(videos),
        "seen": sum(1 for spent in videos.values() if spent >= profils.MIN_SECONDS),
        "recent": recent[:6], "favorites": kept,
        "friends": len(friends),
        "earned": sum(1 for one in friends if one.get("parrain_bon_at")),
        "godparent": godparent.get("name", ""),
        "online": False, "watching": "",
    }


def gather(sources: list, aliases: dict, live: list) -> list:
    """Les fiches. `sources` : [(« PC » ou « NAS », resume du journal,
    fichier des profils, profils)] ; `live` : les visiteurs du moment."""
    people, claimed = [], set()
    for where, summary, path, profiles in sources:
        for key, profile in profiles.items():
            labels = list(profile.get("labels") or [])
            claimed.update((where, label) for label in labels)
            people.append(_person(where, labels, summary, profile, key, path, profiles,
                                  aliases))
    for where, summary, path, _profiles in sources:
        for label in summary:
            if (where, label) not in claimed:
                people.append(_person(where, [label], summary, None, "", path, {},
                                      aliases))
    for person in people:
        for entry in live or []:
            if entry.get("where") == person["where"] and entry.get("label") in person["labels"]:
                person["online"] = True
                person["watching"] = entry.get("name", "")
    people.sort(key=lambda one: (not one["online"], -one["last"]))
    return people


# -- l'affichage --------------------------------------------------------------
class FlowLayout(QLayout):
    """Des etiquettes qui passent a la ligne quand la place manque."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):                       # noqa: N802
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):                       # noqa: N802
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):                       # noqa: N802
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):                 # noqa: N802
        return Qt.Orientations(0)

    def hasHeightForWidth(self):                   # noqa: N802
        return True

    def heightForWidth(self, width):               # noqa: N802
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect):                   # noqa: N802
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self):                            # noqa: N802
        return self.minimumSize()

    def minimumSize(self):                         # noqa: N802
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _place(self, rect, move: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        gap = self.spacing()
        for item in self.items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line:
                x, y, line = rect.x(), y + line + gap, 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + gap
            line = max(line, hint.height())
        return y + line - rect.y()


def _label(text: str, name: str, parent) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName(name)
    return label


class VisitorCard(QFrame):
    """Une fiche."""

    def __init__(self, person: dict, page: "VisitorsPage"):
        super().__init__(page.inner)
        self.page = page
        self.person = person
        self.setObjectName("visitorCard")
        self.setProperty("online", "true" if person["online"] else "false")
        box = QVBoxLayout(self)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(9)

        # Qui, et s'il est la.
        top = QHBoxLayout()
        top.setSpacing(11)
        name = person["name"]
        color = AVATAR_COLORS[int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)
                              % len(AVATAR_COLORS)]
        avatar = QLabel((name[:1] or "?").upper(), self)
        avatar.setFixedSize(42, 42)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(f"background: {color}; color: white; border-radius: 21px;"
                             " font-size: 18px; font-weight: 700;")
        top.addWidget(avatar)
        names = QVBoxLayout()
        names.setSpacing(1)
        names.addWidget(_label(name, "vName", self))
        sub = [person["email"] or ("sans email" if person["has_profile"]
                                   else "pas encore inscrit")]
        sub.append(f"{len(person['labels'])} appareil(s)")
        if person["joined"]:
            sub.append(f"inscrit le {when(person['joined'])}")
        names.addWidget(_label("  ·  ".join(sub), "vSub", self))
        top.addLayout(names, 1)
        pill = _label("● En ligne" + (f" — « {person['watching']} »"
                                       if person["watching"] else "")
                      if person["online"] else f"Vu {ago(person['last'])}", "vPill", self)
        pill.setProperty("online", "true" if person["online"] else "false")
        pill.setMaximumWidth(320)
        top.addWidget(pill, 0, Qt.AlignTop)
        box.addLayout(top)

        info = [f"IP {person['ip']}" if person["ip"] else "IP inconnue",
                f"dernière venue {when(person['last'])}" if person["last"] else "",
                f"par le {person['where']}"]
        box.addWidget(_label("  ·  ".join(part for part in info if part), "vInfo", self))

        # Les chiffres.
        tiles = QHBoxLayout()
        tiles.setSpacing(8)
        for number, caption in ((person["visits"], "connexions"),
                                (person["watched"], "vidéos regardées"),
                                (spell(person["seconds"]), "de visionnage"),
                                (len(person["favorites"]), "favoris")):
            tile = QFrame(self)
            tile.setObjectName("vTile")
            inside = QVBoxLayout(tile)
            inside.setContentsMargins(10, 7, 10, 7)
            inside.setSpacing(0)
            inside.addWidget(_label(str(number), "vNum", tile))
            inside.addWidget(_label(caption, "vCap", tile))
            tiles.addWidget(tile, 1)
        box.addLayout(tiles)

        # Le bon d'achat.
        goal = profils.GOAL
        seen = min(person["seen"], goal)
        gift = QHBoxLayout()
        gift.setSpacing(10)
        gift.addWidget(_label("Bon d'achat", "vHead", self))
        bar = QProgressBar(self)
        bar.setObjectName("vGift")
        bar.setRange(0, goal)
        bar.setValue(seen)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        gift.addWidget(bar, 1)
        if person["bon_at"]:
            said = f"✓ atteint le {when(person['bon_at'])} : {profils.PRIZE} € à envoyer"
        elif not person["email"]:
            said = f"{person['seen']} / {goal} — pas d'email"
        else:
            said = f"{person['seen']} / {goal} vidéos"
        gift.addWidget(_label(said, "vDim", self))
        # Pas inscrit : pas de bon a suivre.
        if person["has_profile"]:
            box.addLayout(gift)
        else:
            for index in reversed(range(gift.count())):
                widget = gift.itemAt(index).widget()
                if widget is not None:
                    widget.deleteLater()
        if person["friends"] or person["godparent"]:
            parts = []
            if person["friends"]:
                parts.append(f"Parrain de {person['friends']} personne(s) · "
                             f"{person['earned'] * profils.FRIEND_PRIZE} € gagnés")
            if person["godparent"]:
                parts.append(f"filleul de {person['godparent']}")
            box.addWidget(_label("  ·  ".join(parts), "vInfo", self))

        # Les themes epingles.
        if person["likes"]:
            box.addWidget(_label("Thèmes épinglés", "vHead", self))
            holder = QWidget(self)
            flow = FlowLayout(holder)
            for like in person["likes"]:
                flow.addWidget(_label(like, "vChip", holder))
            box.addWidget(holder)

        # Ce qui a ete regarde, et les favoris.
        if person["recent"]:
            box.addWidget(_label("Dernières vidéos regardées — double-clic pour la voir",
                                 "vHead", self))
            recent = QListWidget(self)
            recent.setObjectName("vRecent")
            for at, video_name, spent, mark in person["recent"]:
                item = QListWidgetItem(f"{when(at)}   {video_name}   ({spell(spent)})")
                item.setData(Qt.UserRole, mark)
                recent.addItem(item)
            recent.setFixedHeight(min(6, recent.count()) * 24 + 8)
            recent.itemDoubleClicked.connect(lambda item: page.play(item.data(Qt.UserRole)))
            box.addWidget(recent)
        if person["favorites"]:
            names = [row[1] for row in person["favorites"][:3]]
            more = len(person["favorites"]) - len(names)
            text = "Favoris : " + " · ".join(names) + (f"  (+{more})" if more > 0 else "")
            fav = _label(text, "vDim", self)
            fav.setWordWrap(True)
            box.addWidget(fav)

        # Les gestes.
        actions = QHBoxLayout()
        actions.addStretch(1)
        if not person["has_profile"]:
            rename = QPushButton("Nommer l'appareil…", self)
            rename.setObjectName("vButton")
            rename.clicked.connect(lambda: page.rename(person["labels"][0]))
            actions.addWidget(rename)
        if person["pin"]:
            unlock = QPushButton("Retirer son code (oublié)", self)
            unlock.setObjectName("vButton")
            unlock.setToolTip("La personne a choisi un code à 4 chiffres sur son "
                              "téléphone ; s'il est oublié, on le retire ici.")
            unlock.clicked.connect(lambda: page.clear_pin(person))
            actions.addWidget(unlock)
        if actions.count() > 1:
            box.addLayout(actions)


class VisitorsPage(QWidget):
    """L'onglet : un en-tete, puis les fiches, la plus recente en haut."""

    def __init__(self, window, parent=None, on_change=None):
        super().__init__(parent)
        self.window = window
        self.on_change = on_change
        self.setStyleSheet(STYLE)
        box = QVBoxLayout(self)
        self.head = _label("", "vInfo", self)
        box.addWidget(self.head)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.inner = QWidget(scroll)
        self.inner.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.column = QVBoxLayout(self.inner)
        self.column.setContentsMargins(0, 0, 6, 0)
        self.column.setSpacing(10)
        scroll.setWidget(self.inner)
        box.addWidget(scroll, 1)
        self._shown = None

    def show_people(self, people: list) -> None:
        # Refaites seulement si quelque chose a change : la liste ne saute
        # pas sous la souris toutes les quatre secondes.
        key = repr(people)
        if key == self._shown:
            return
        self._shown = key
        while self.column.count():
            item = self.column.takeAt(0)
            if item.widget() is not None:
                # Cachee tout de suite : detruite plus tard, l'ancienne fiche
                # restait dessinee sous la nouvelle.
                item.widget().hide()
                item.widget().deleteLater()
        online = sum(1 for one in people if one["online"])
        self.head.setText(f"{len(people)} visiteur(s)" + (
            f" · {online} en ligne" if online else "") +
            " — une fiche par personne ; ses appareils y sont réunis.")
        if not people:
            empty = _label("Personne n'est encore venu par le lien d'invitation.",
                           "vDim", self.inner)
            empty.setAlignment(Qt.AlignCenter)
            self.column.addWidget(empty)
        for person in people:
            self.column.addWidget(VisitorCard(person, self))
        self.column.addStretch(1)

    def play(self, mark: str) -> None:
        path = self.window.video_for_mark(mark) if mark else None
        if path is None:
            QMessageBox.information(self, "Vidéo introuvable",
                                    "Cette vidéo n'est plus dans la collection.")
            return
        self.window.play_floating_path(path)

    def rename(self, label: str) -> None:
        if label and self.window.name_device(label, self):
            self._changed()

    def clear_pin(self, person: dict) -> None:
        if QMessageBox.question(
                self, "Retirer son code",
                f"Retirer le code de verrouillage de {person['name']} ? Prisme "
                "s'ouvrira de nouveau sans code sur ses appareils ; la personne "
                "pourra en choisir un autre.") != QMessageBox.Yes:
            return
        if not profils.clear_pin_of(person["path"], person["key"]):
            QMessageBox.warning(self, "Retirer son code",
                                "Le fichier des profils n'a pas pu être modifié.")
        self._changed()

    def _changed(self) -> None:
        self._shown = None
        if self.on_change is not None:
            self.on_change()
