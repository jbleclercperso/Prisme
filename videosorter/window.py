"""Fenêtre principale : enchaînement des éléments et exécution des actions."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QMainWindow, QMessageBox, QProgressBar, QProgressDialog, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from . import actions
from .actions import ActionError, HistoryEntry
from .config import Config
from .media import PreviewManager, Tools, probe
from .scan import (
    MODE_FILES, MODE_FOLDERS, ScanThread, detect_mode, human_duration,
    human_size, list_entries,
)
from .transfer import Transfer, TransferQueue
from .tree import TreePanel
from .widgets import (
    STYLESHEET, CommandBar, DestinationsDialog, FilterBar, PreviewGrid, RootBar,
    SinglePlayer,
)

PAGE_WELCOME, PAGE_SORT, PAGE_DONE = 0, 1, 2

DELETE_LABELS = {
    "recycle": "Corbeille",
    "permanent": "Supprimer définitivement",
    "local_trash": "Dossier _TRASH",
}


class WelcomePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 50, 60, 50)
        layout.setSpacing(14)

        title = QLabel("VideoSorter", self)
        title.setObjectName("title")
        subtitle = QLabel(
            "Choisissez un dossier racine. S'il contient des sous-dossiers, ils sont "
            "triés un par un ; s'il ne contient que des vidéos, elles le sont une par une.",
            self,
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        self.choose = QPushButton("Choisir un dossier racine…", self)
        self.choose.setObjectName("primary")

        recent_label = QLabel("Racines récentes", self)
        recent_label.setObjectName("hint")
        self.recent = QListWidget(self)

        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(10)
        layout.addWidget(self.choose, 0, Qt.AlignLeft)
        layout.addSpacing(20)
        layout.addWidget(recent_label)
        layout.addWidget(self.recent, 1)

    def set_recent(self, roots: list) -> None:
        self.recent.clear()
        for root in roots:
            self.recent.addItem(root)


class DonePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 50, 60, 50)
        layout.setSpacing(14)
        self.title = QLabel("Tri terminé", self)
        self.title.setObjectName("title")
        self.summary = QLabel("", self)
        self.summary.setObjectName("subtitle")
        self.rescan = QPushButton("Réanalyser cette racine", self)
        self.rescan.setObjectName("primary")
        self.change = QPushButton("Changer de racine", self)
        layout.addWidget(self.title)
        layout.addWidget(self.summary)
        layout.addSpacing(16)
        row = QHBoxLayout()
        row.addWidget(self.rescan)
        row.addWidget(self.change)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("VideoSorter")
        self.resize(cfg["window"].get("w", 1400), cfg["window"].get("h", 900))
        self.setStyleSheet(STYLESHEET)

        self.all_items: list = []      # tout ce que l'analyse a trouve
        self.items: list = []          # ce que le filtre laisse passer
        self.index = 0                 # index dans self.items
        self.mode = MODE_FOLDERS
        self.root: Path | None = None
        self.plans: dict = {}
        self.history: list = []
        self.stats = {"moved": 0, "deleted": 0, "skipped": 0}
        self.scan_thread: ScanThread | None = None
        self.scanning = False
        # Pile des dossiers traverses, pour pouvoir remonter d'ou l'on vient.
        self.levels: list = []
        self._restore_id = ""

        self.transfers = TransferQueue(self)
        self.transfers.finished.connect(self.on_transfer_finished)
        self.transfers.changed.connect(self.on_transfers_changed)

        self.preview = PreviewManager(cfg["thumb_width"], self)
        self.preview.plan_ready.connect(self.on_plan_ready)
        self.preview.thumb_ready.connect(self.on_thumb_ready)
        self.preview.thumb_failed.connect(self.on_thumb_failed)

        self._build_ui()
        self.welcome.set_recent(cfg["recent_roots"])
        self.stack.setCurrentIndex(PAGE_WELCOME)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.stack = QStackedWidget(self)
        self.setCentralWidget(self.stack)

        self.welcome = WelcomePage(self)
        self.welcome.choose.clicked.connect(self.choose_root)
        self.welcome.recent.itemActivated.connect(
            lambda item: self.start_root(Path(item.text()))
        )
        self.stack.addWidget(self.welcome)

        sort_page = QWidget(self)
        layout = QVBoxLayout(sort_page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        self.root_bar = RootBar(sort_page)
        self.root_bar.changeRoot.connect(self.choose_root)
        self.root_bar.openSettings.connect(self.edit_destinations)
        self.root_bar.toggleMode.connect(self.toggle_mode)
        self.root_bar.toggleTree.connect(self.toggle_tree)
        self.root_bar.toggleMute.connect(self.toggle_mute)
        self.root_bar.enterItem.connect(self.enter_current)
        self.root_bar.goUp.connect(self.go_up)
        self.root_bar.set_muted(self.cfg["muted"])
        layout.addWidget(self.root_bar)

        self.filter_bar = FilterBar(sort_page)
        self.filter_bar.changed.connect(self.apply_filter)
        self.filter_bar.released.connect(self.setFocus)
        self.filter_bar.set_terms(self.cfg["filter_include"], self.cfg["filter_exclude"])
        layout.addWidget(self.filter_bar)

        self.progress = QProgressBar(sort_page)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        layout.addWidget(self.progress)

        header = QFrame(sort_page)
        header.setObjectName("card")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(4)
        self.item_title = QLabel("—", header)
        self.item_title.setObjectName("title")
        self.item_parent = QLabel("", header)
        self.item_parent.setObjectName("parentPath")
        self.item_subtitle = QLabel("", header)
        self.item_subtitle.setObjectName("subtitle")
        header_layout.addWidget(self.item_title)
        header_layout.addWidget(self.item_parent)
        header_layout.addWidget(self.item_subtitle)
        layout.addWidget(header)

        self.banner = QLabel("", sort_page)
        self.banner.setObjectName("statusBanner")
        self.banner.hide()
        layout.addWidget(self.banner)

        # Le panneau d'arborescence occupe la gauche, le lecteur le reste.
        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(10)

        self.tree = TreePanel(sort_page)
        self.tree.folderChosen.connect(self.on_tree_folder)
        self.tree.rootChanged.connect(self.on_tree_root_changed)
        self.tree.hide()
        middle.addWidget(self.tree)

        self.viewer = QStackedWidget(sort_page)
        self.grid = PreviewGrid(
            self.cfg["thumb_count"], self.cfg["preview_seconds"],
            self.cfg["scroll_seconds"], self.viewer,
        )
        self.grid.openRequested.connect(self.open_external)
        self.single = SinglePlayer(
            self.cfg["thumb_count"], self.cfg["scroll_seconds"], self.viewer
        )
        self.viewer.addWidget(self.grid)
        self.viewer.addWidget(self.single)
        middle.addWidget(self.viewer, 1)
        layout.addLayout(middle, 1)

        self.commands = CommandBar(sort_page)
        # Les memes actions qu'au clavier, accessibles a la souris.
        self.commands.deleteRequested.connect(self.on_command_delete)
        self.commands.skipRequested.connect(self.on_command_skip)
        self.commands.moveRequested.connect(self.on_command_move)
        layout.addWidget(self.commands)

        hint = QLabel(
            "←/→ naviguer   ·   molette avancer/reculer   ·   Ctrl+Z annuler   "
            "·   Ctrl+F filtrer   ·   Ctrl+T arborescence   ·   Ctrl+M son   "
            "·   Ctrl+O ouvrir   ·   Ctrl+D destinations   ·   Entrée pause   "
            "·   Ctrl+↓ entrer dans le dossier   ·   Échap remonter",
            sort_page,
        )
        hint.setObjectName("hint")
        layout.addWidget(hint)

        self.stack.addWidget(sort_page)

        self.done_page = DonePage(self)
        self.done_page.rescan.clicked.connect(
            lambda: self.start_root(self.root, self.mode, reset_levels=False)
        )
        self.done_page.change.clicked.connect(self.choose_root)
        self.stack.addWidget(self.done_page)

        self.banner_timer = QTimer(self)
        self.banner_timer.setSingleShot(True)
        self.banner_timer.timeout.connect(self.banner.hide)

    # ------------------------------------------------------------------
    # Racine et analyse
    # ------------------------------------------------------------------
    def choose_root(self) -> None:
        start = self.cfg["root"] or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choisir le dossier racine", start)
        if chosen:
            self.start_root(Path(chosen))

    def start_root(self, root: Path | None, mode: str = "",
                   reset_levels: bool = True, restore_id: str = "") -> None:
        if root is None or not Path(root).is_dir():
            QMessageBox.warning(self, "Dossier introuvable", f"{root} n'existe plus.")
            return
        self.stop_scan()
        if reset_levels:
            self.levels = []
        self._restore_id = restore_id
        self.root = Path(root)
        self.mode = mode or detect_mode(self.root, self.cfg["skip_hidden"])
        self.all_items = []
        self.items = []
        self.plans = {}
        self.history = []
        self.index = 0
        self.stats = {"moved": 0, "deleted": 0, "skipped": 0}
        self.preview.cancel_all()

        self.cfg.push_recent_root(str(self.root))
        self.cfg.save()
        self.welcome.set_recent(self.cfg["recent_roots"])

        depth = f"   ·   niveau {len(self.levels) + 1}" if self.levels else ""
        mode_name = "dossiers" if self.mode == MODE_FOLDERS else "fichiers"
        self.root_bar.root_label.setText(f"{self.root}   ·   mode {mode_name}{depth}")
        self.root_bar.root_label.setToolTip(str(self.root))
        self.root_bar.set_navigation(
            can_enter=self.mode == MODE_FOLDERS, nested=bool(self.levels)
        )
        self.commands.rebuild(self.cfg.destinations, DELETE_LABELS.get(self.cfg["delete_mode"], "Supprimer"))
        self.viewer.setCurrentWidget(self.grid if self.mode == MODE_FOLDERS else self.single)
        self.item_title.setText("Analyse en cours…")
        self.item_subtitle.setText("")
        self.progress.setRange(0, 0)
        self.stack.setCurrentIndex(PAGE_SORT)
        self.setFocus()

        self.scanning = True
        self.scan_thread = ScanThread(self.root, self.mode, self.cfg["skip_hidden"], self)
        self.scan_thread.progress.connect(self.on_scan_progress)
        self.scan_thread.item_ready.connect(self.on_item_ready)
        self.scan_thread.finished_scan.connect(self.on_scan_finished)
        self.scan_thread.start()

    def stop_scan(self) -> None:
        if self.scan_thread is not None:
            self.scan_thread.stop()
            self.scan_thread.wait(3000)
            self.scan_thread = None
        self.scanning = False

    def toggle_mode(self) -> None:
        """Force l'autre mode sur la racine courante, sans changer de dossier."""
        if self.root is None:
            return
        new_mode = MODE_FILES if self.mode == MODE_FOLDERS else MODE_FOLDERS
        self.start_root(self.root, new_mode, reset_levels=False)

    # ------------------------------------------------------------------
    # Descendre dans un dossier, et en revenir
    # ------------------------------------------------------------------
    def enter_current(self) -> None:
        """Ouvre le dossier affiche pour en trier le contenu piece par piece.

        Le cas vise : un dossier trop melange pour recevoir une seule etiquette.
        On y entre en mode fichier s'il contient des videos directement, sinon
        on laisse la detection choisir — un dossier de dossiers se parcourt.
        """
        item = self.current
        if item is None or item.kind != MODE_FOLDERS or item.locked:
            return
        target = Path(item.path)
        if not target.is_dir():
            self.show_banner(f"Introuvable : {item.name}", "#3a2226")
            return

        direct = list_entries(target, MODE_FILES, self.cfg["skip_hidden"])
        mode = MODE_FILES if direct else ""
        self.levels.append({
            "root": self.root,
            "mode": self.mode,
            "item_id": item.item_id,
        })
        self.start_root(target, mode, reset_levels=False)

    def go_up(self) -> bool:
        """Remonte d'un niveau, en retrouvant le dossier d'ou l'on etait parti."""
        if not self.levels:
            return False
        level = self.levels.pop()
        self.start_root(
            level["root"], level["mode"],
            reset_levels=False, restore_id=level["item_id"],
        )
        return True

    def on_scan_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        if not self.items:
            self.item_subtitle.setText(f"Analyse {done}/{total} — {name}")

    def on_item_ready(self, item) -> None:
        self.all_items.append(item)
        self.filter_bar.set_count(len(self.items), len(self.all_items))
        if not self._matches(item):
            return
        first = not self.items
        self.items.append(item)
        if self._restore_id and item.item_id == self._restore_id:
            self._restore_id = ""
            self.show_item(len(self.items) - 1)
            return
        if first:
            self.show_item(0)
            return
        self.update_counter()
        # L'analyse alimente la liste en continu : un élément qui arrive juste
        # après celui affiché doit être préchargé lui aussi.
        if len(self.items) - 1 <= self.index + 2:
            self._request_previews(item, current=False)

    def on_scan_finished(self, mode: str, total: int) -> None:
        self.scanning = False
        self.filter_bar.set_count(len(self.items), len(self.all_items))
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(total)
        if not self.items:
            self.item_title.setText("Rien à trier")
            self.item_subtitle.setText(
                "Aucun sous-dossier trouvé." if mode == MODE_FOLDERS
                else "Aucune vidéo trouvée directement dans ce dossier."
            )
            self.grid.set_no_videos("—")
        else:
            self.update_counter()

    # ------------------------------------------------------------------
    # Affichage de l'élément courant
    # ------------------------------------------------------------------
    @property
    def current(self):
        if 0 <= self.index < len(self.items):
            return self.items[self.index]
        return None

    def update_counter(self) -> None:
        total = len(self.items)
        suffix = " (analyse…)" if self.scanning else ""
        hidden = len(self.all_items) - total
        if hidden > 0:
            suffix += f" · {hidden} filtrés"
        done = self.stats["moved"] + self.stats["deleted"]
        self.root_bar.counter.setText(
            f"{min(self.index + 1, total)} / {total}{suffix}   ·   "
            f"{self.stats['moved']} déplacés · {self.stats['deleted']} supprimés · "
            f"{self.stats['skipped']} passés"
        )
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(self.index + done, total))

    def show_item(self, index: int) -> None:
        if not self.items:
            return
        self.index = max(0, min(index, len(self.items) - 1))
        item = self.items[self.index]
        self.update_counter()
        self.commands.rebuild(
            self.cfg.destinations, DELETE_LABELS.get(self.cfg["delete_mode"], "Supprimer")
        )

        if item.kind == MODE_FOLDERS:
            self.item_title.setText(item.name)
            parts = [
                human_size(item.size),
                f"{item.file_count} fichier{'s' if item.file_count > 1 else ''}",
                f"{item.video_count} vidéo{'s' if item.video_count > 1 else ''}",
            ]
            if item.subdir_count:
                parts.append(f"{item.subdir_count} sous-dossier{'s' if item.subdir_count > 1 else ''}")
        else:
            self.item_title.setText(item.name)
            info = item.info or probe(item.path)
            item.info = info
            parts = [human_size(item.size)]
            if info.get("duration"):
                parts.append(human_duration(info["duration"]))
            if info.get("width"):
                parts.append(f"{info['width']}×{info['height']}")
            if info.get("codec"):
                parts.append(info["codec"])
        if item.mtime:
            parts.append("modifié le " + datetime.fromtimestamp(item.mtime).strftime("%d/%m/%Y"))
        self.item_subtitle.setText("   ·   ".join(parts))

        # Le dossier qui contient l'élément : en mode fichier, c'est la seule
        # façon de savoir quel dossier on est en train de vider.
        parent = Path(item.path).parent
        self.item_parent.setText("dans  " + (parent.name or str(parent)))
        self.item_parent.setToolTip(str(parent))

        if item.pending:
            self.show_banner(f"Transfert en cours vers {item.status_detail}…", "#2a3340")
        elif item.processed:
            tone = "#22331f" if item.status == "moved" else "#3a2226"
            self.show_banner(f"Déjà traité : {item.status_detail}", tone)

        self.root_bar.set_navigation(
            can_enter=item.kind == MODE_FOLDERS and not item.locked,
            nested=bool(self.levels),
        )

        viewer = self.grid if item.kind == MODE_FOLDERS else self.single
        self.viewer.setCurrentWidget(viewer)
        viewer.set_muted(self.cfg["muted"])

        if item.locked:
            # Inutile de relancer des ffmpeg sur un élément qui n'est plus là.
            self.grid.set_no_videos("—")
            self.single.stop()
            return

        if item.kind == MODE_FOLDERS:
            self.grid.set_item(item.item_id, "…")
            if not item.videos:
                self.grid.set_no_videos("aucune vidéo")
        else:
            self.single.set_item(str(item.path), "…")

        self._request_previews(item, current=True)
        keep = {item.item_id}
        for offset in (1, 2):
            if self.index + offset < len(self.items):
                nxt = self.items[self.index + offset]
                keep.add(nxt.item_id)
                self._request_previews(nxt, current=False)
        self.preview.cancel_except(keep)

    def _request_previews(self, item, current: bool) -> None:
        if not item.videos or item.locked:
            return
        plan = self.plans.get(item.item_id)
        if plan is None:
            self.preview.request_plan(item.item_id, item.videos, self.cfg["thumb_count"])
            return
        if current:
            self._apply_plan(item, plan)
        for slot, entry in enumerate(plan):
            self.preview.request_thumb(item.item_id, slot, entry[0], entry[1])

    def _apply_plan(self, item, plan: list) -> None:
        viewer = self.grid if item.kind == MODE_FOLDERS else self.single
        viewer.set_plan(plan)

    def on_plan_ready(self, item_id: str, plan: list) -> None:
        self.plans[item_id] = plan
        current = self.current
        if current is not None and current.item_id == item_id:
            self._apply_plan(current, plan)
        for slot, entry in enumerate(plan):
            self.preview.request_thumb(item_id, slot, entry[0], entry[1])

    def on_thumb_ready(self, item_id: str, slot: int, path: str) -> None:
        current = self.current
        if current is None or current.item_id != item_id:
            return
        viewer = self.grid if current.kind == MODE_FOLDERS else self.single
        viewer.set_thumb(slot, path)

    def on_thumb_failed(self, item_id: str, slot: int) -> None:
        current = self.current
        if current is None or current.item_id != item_id:
            return
        viewer = self.grid if current.kind == MODE_FOLDERS else self.single
        viewer.set_failed(slot)

    def show_banner(self, text: str, color: str = "#22303f") -> None:
        self.banner.setText(text)
        self.banner.setStyleSheet(f"background: {color}; color: #e9eef4;")
        self.banner.show()
        self.banner_timer.start(4000)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _release_media(self) -> None:
        """Relâche tous les handles sur les fichiers avant une opération disque.

        Deux sources de verrous sous Windows : le lecteur Qt, et les ffmpeg de
        préchargement qui fabriquent les vignettes des éléments suivants.
        """
        for player in (self.grid.player, self.single.player):
            player.stop()
            player.setSource(QUrl())
        self.grid.video.hide()
        self.preview.quiesce(1200)
        QApplication.processEvents()

    # ------------------------------------------------------------------
    # Panneau d'arborescence
    # ------------------------------------------------------------------
    def toggle_tree(self, visible: bool | None = None) -> None:
        show = (not self.tree.isVisible()) if visible is None else visible
        if show and not self.tree.root:
            # Par défaut, on montre les voisins de la racine triée : c'est là
            # que se trouvent presque toujours les dossiers de destination.
            default = self.cfg["tree_root"] or (
                str(self.root.parent) if self.root else ""
            )
            self.tree.set_root(default)
            if not self.tree.root:
                self.tree.choose_root()
                if not self.tree.root:
                    return
        self.tree.setVisible(show)
        self.cfg["tree_visible"] = show
        self.cfg.save()
        self.setFocus()

    def on_tree_folder(self, path: str) -> None:
        """Un clic dans l'arbre vaut décision : pas de confirmation."""
        self.act_move({"path": path, "label": Path(path).name})

    def on_tree_root_changed(self, path: str) -> None:
        self.cfg["tree_root"] = path
        self.cfg.save()

    # ------------------------------------------------------------------
    # Filtre par nom
    # ------------------------------------------------------------------
    @staticmethod
    def _terms(text: str) -> list:
        return [term.strip().lower() for term in text.split(",") if term.strip()]

    def _matches(self, item) -> bool:
        name = item.name.lower()
        include = self._terms(self.cfg["filter_include"])
        exclude = self._terms(self.cfg["filter_exclude"])
        if include and not any(term in name for term in include):
            return False
        return not any(term in name for term in exclude)

    def apply_filter(self, include: str, exclude: str) -> None:
        self.cfg["filter_include"] = include
        self.cfg["filter_exclude"] = exclude
        self.cfg.save()

        current = self.current
        self.items = [item for item in self.all_items if self._matches(item)]
        self.filter_bar.set_count(len(self.items), len(self.all_items))

        if not self.items:
            self.preview.cancel_all()
            self._release_media()
            self.index = 0
            self.item_title.setText("Aucun élément ne correspond au filtre")
            self.item_parent.setText("")
            self.item_subtitle.setText(
                f"{len(self.all_items)} élément(s) masqué(s). Modifiez ou effacez le filtre."
            )
            self.grid.set_no_videos("—")
            self.update_counter()
            return

        # On reste sur le même élément s'il passe encore le filtre.
        if current is not None and current in self.items:
            self.index = self.items.index(current)
        else:
            self.index = min(self.index, len(self.items) - 1)
        if self.stack.currentIndex() == PAGE_DONE:
            self.stack.setCurrentIndex(PAGE_SORT)
        self.show_item(self.index)

    def focus_filter(self) -> None:
        self.filter_bar.include.setFocus()
        self.filter_bar.include.selectAll()

    def _item_by_id(self, item_id: str):
        # Sur self.all_items : un transfert peut aboutir alors que le filtre a
        # entre-temps ecarte l'element de la liste visible.
        for item in self.all_items:
            if item.item_id == item_id:
                return item
        return None

    def _enqueue(self, job: Transfer, banner: str, tone: str) -> None:
        """Lance l'operation en tache de fond et passe tout de suite a la suite."""
        self.transfers.submit(job)
        self.show_banner(banner, tone)
        self.advance()

    def act_delete(self) -> None:
        item = self.current
        if item is None or item.locked:
            return self.advance()
        mode = self.cfg["delete_mode"]
        if not Path(item.path).exists():
            self.show_banner(f"Introuvable : {item.name}", "#3a2226")
            return self.advance()
        self._release_media()
        label = DELETE_LABELS.get(mode, "supprime")
        item.status = "pending_delete"
        item.status_detail = label
        self._enqueue(
            Transfer(kind="delete", src=item.path, mode=mode,
                     label=label, item_id=item.item_id),
            f"« {item.name} » → {label}", "#3a2226",
        )

    def act_move(self, dest: dict) -> None:
        item = self.current
        if item is None or item.locked:
            return self.advance()
        dest_dir = Path(dest["path"])
        label = dest.get("label") or dest_dir.name
        problem = self._move_objection(item, dest_dir)
        if problem:
            self.show_banner(problem, "#3a2226")
            return
        self._release_media()
        item.status = "pending_move"
        item.status_detail = label
        self._enqueue(
            Transfer(kind="move", src=item.path, dest=dest_dir,
                     label=label, item_id=item.item_id),
            f"« {item.name} » → {label}", "#22331f",
        )

    def _move_objection(self, item, dest_dir: Path) -> str:
        """Verifie d'avance ce qui condamnerait le transfert, pour ne pas avancer."""
        if not Path(item.path).exists():
            return f"Introuvable : {item.name}"
        try:
            if Path(item.path).samefile(dest_dir):
                return "C'est deja ce dossier."
        except OSError:
            pass
        if Path(item.path).is_dir():
            try:
                dest_dir.resolve().relative_to(Path(item.path).resolve())
                return "Impossible : la destination est dans le dossier a deplacer."
            except ValueError:
                pass
        return ""

    def act_skip(self) -> None:
        item = self.current
        if item is not None and not item.processed and item.status != "skipped":
            item.status = "skipped"
            self.stats["skipped"] += 1
        self.advance()

    def act_undo(self) -> None:
        if self.transfers.busy:
            # L'historique ne contient que les transferts aboutis : annuler
            # maintenant risquerait de défaire autre chose que la dernière action.
            self.show_banner(
                "Transfert en cours — l'annulation sera possible dans un instant.",
                "#3a3226",
            )
            return
        if not self.history:
            self.show_banner("Rien à annuler", "#2a2f38")
            return
        entry = self.history[-1]
        if not entry.reversible:
            self.show_banner(
                "Suppression envoyée à la corbeille Windows : restaurez-la depuis l'explorateur.",
                "#3a3226",
            )
            return
        self._release_media()
        self.history.pop()
        item = self._item_by_path(entry.src)
        if item is not None:
            item.status = "pending_undo"
        self.transfers.submit(Transfer(
            kind="undo", src=entry.dst or entry.src, entry=entry,
            label=entry.label, item_id=item.item_id if item else "",
        ))
        self.show_banner(f"Restauration de « {Path(entry.src).name} »…", "#22303f")

    def _item_by_path(self, path: Path):
        for item in self.all_items:
            if item.path == path:
                return item
        return None

    # ------------------------------------------------------------------
    # Retour des transferts
    # ------------------------------------------------------------------
    def on_transfer_finished(self, job: Transfer) -> None:
        item = self._item_by_id(job.item_id) if job.item_id else None

        if job.state == "failed":
            if item is not None:
                item.status = ""
                item.status_detail = ""
            self.show_banner(f"Échec sur « {job.name} » : {job.error}", "#4a1f24")
            self.update_counter()
            return

        if job.kind == "move":
            if item is not None:
                item.status = "moved"
            self.stats["moved"] += 1
            self.history.append(
                HistoryEntry("move", Path(job.src), job.result, job.label, True)
            )
        elif job.kind == "delete":
            if item is not None:
                item.status = "deleted"
            self.stats["deleted"] += 1
            self.history.append(
                HistoryEntry("delete", Path(job.src), job.result, job.label, job.reversible)
            )
        else:  # annulation
            entry = job.entry
            if item is not None:
                item.status = ""
                item.status_detail = ""
            key = "deleted" if getattr(entry, "action", "") == "delete" else "moved"
            self.stats[key] = max(0, self.stats[key] - 1)
            self.show_banner(
                f"Annulé : « {Path(entry.src).name} » est revenu à sa place", "#22303f"
            )
            if item is not None and self.current is item:
                self.show_item(self.index)

        self.update_counter()

    def on_transfers_changed(self, active: int) -> None:
        self.root_bar.set_pending(active)

    def advance(self) -> None:
        if self.index + 1 >= len(self.items):
            if self.scanning:
                self.item_title.setText("En attente de la suite de l'analyse…")
                self.index = len(self.items) - 1
                QTimer.singleShot(400, self._advance_when_ready)
                return
            self.finish()
            return
        self.show_item(self.index + 1)

    def _advance_when_ready(self) -> None:
        if self.index + 1 < len(self.items):
            self.show_item(self.index + 1)
        elif self.scanning:
            QTimer.singleShot(400, self._advance_when_ready)
        else:
            self.finish()

    def finish(self) -> None:
        self._release_media()
        summary = (
            f"{self.stats['moved']} déplacés   ·   {self.stats['deleted']} supprimés   ·   "
            f"{self.stats['skipped']} laissés de côté   ·   {len(self.items)} éléments vus"
        )
        if self.transfers.busy:
            summary += f"\n\n{self.transfers.active} transfert(s) encore en cours."
        self.done_page.summary.setText(summary)
        self.stack.setCurrentIndex(PAGE_DONE)

    def open_external(self, path: str = "") -> None:
        target = Path(path) if path else (self.current.path if self.current else None)
        if target:
            actions.reveal(target)

    def edit_destinations(self) -> None:
        dialog = DestinationsDialog(self.cfg.destinations, self)
        if dialog.exec() == dialog.DialogCode.Accepted:
            self.cfg.set_destinations(dialog.result_destinations())
            self.cfg.save()
            self.commands.rebuild(
                self.cfg.destinations, DELETE_LABELS.get(self.cfg["delete_mode"], "Supprimer")
            )
        self.setFocus()

    # ------------------------------------------------------------------
    # Clavier
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        key = event.key()
        ctrl = bool(event.modifiers() & Qt.ControlModifier)

        if self.stack.currentIndex() == PAGE_DONE:
            # Le tri d'un sous-dossier fini, on revient d'ou l'on venait.
            if key == Qt.Key_Escape or (ctrl and key == Qt.Key_Up):
                if self.go_up():
                    return
            return super().keyPressEvent(event)
        if self.stack.currentIndex() != PAGE_SORT:
            return super().keyPressEvent(event)

        # Les commandes de l'application sont toutes sur Ctrl ou sur une touche
        # de navigation : chiffres et lettres restent libres pour les destinations.
        if ctrl:
            if key == Qt.Key_Z:
                return self.act_undo()
            if key == Qt.Key_T:
                return self.toggle_tree()
            if key == Qt.Key_M:
                return self.toggle_mute()
            if key == Qt.Key_O:
                return self.open_external()
            if key == Qt.Key_D:
                return self.edit_destinations()
            if key == Qt.Key_F:
                return self.focus_filter()
            if key == Qt.Key_Down:
                return self.enter_current()
            if key == Qt.Key_Up:
                self.go_up()
                return
            return super().keyPressEvent(event)

        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            return self.act_delete()
        if key == Qt.Key_Space:
            return self.act_skip()
        if key == Qt.Key_Right:
            return self.show_item(self.index + 1)
        if key == Qt.Key_Left:
            return self.show_item(self.index - 1)
        if key == Qt.Key_Escape:
            if self.go_up():
                return
            self.stop_scan()
            self._release_media()
            self.stack.setCurrentIndex(PAGE_WELCOME)
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self.viewer.currentWidget() is self.single:
                self.single.toggle_pause()
            return

        text = event.text().lower().strip()
        if text:
            dest = self.cfg.destination_for_key(text)
            if dest:
                return self.act_move(dest)
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Barre de commandes a la souris
    # ------------------------------------------------------------------
    def on_command_delete(self) -> None:
        self.act_delete()
        self.setFocus()

    def on_command_skip(self) -> None:
        self.act_skip()
        self.setFocus()

    def on_command_move(self, dest: dict) -> None:
        self.act_move(dest)
        self.setFocus()

    def toggle_mute(self) -> None:
        muted = not self.cfg["muted"]
        self.cfg["muted"] = muted
        self.cfg.save()
        self.grid.set_muted(muted)
        self.single.set_muted(muted)
        self.root_bar.set_muted(muted)
        self.show_banner("Son coupé" if muted else "Son activé", "#2a2f38")

    def closeEvent(self, event):
        self.stop_scan()
        self._release_media()
        # Un transfert interrompu laisserait un dossier à moitié copié : on
        # attend, en le disant, plutôt que de couper net.
        if self.transfers.busy:
            waiter = QProgressDialog(
                f"{self.transfers.active} transfert(s) en cours — "
                "fermeture dès qu'ils sont terminés.",
                "", 0, 0, self,
            )
            waiter.setWindowTitle("VideoSorter")
            waiter.setCancelButton(None)
            waiter.setMinimumDuration(0)
            waiter.show()
            while self.transfers.busy:
                QApplication.processEvents()
                self.transfers.wait(200)
            waiter.close()
        self.preview.shutdown()
        self.cfg["window"] = {"w": self.width(), "h": self.height()}
        self.cfg.save()
        super().closeEvent(event)


def check_tools(parent=None) -> bool:
    if Tools.ffmpeg and Tools.ffprobe:
        return True
    QMessageBox.critical(
        parent, "ffmpeg introuvable",
        "VideoSorter a besoin de ffmpeg et ffprobe pour fabriquer les aperçus.\n\n"
        "Installez-les (winget install Gyan.FFmpeg) ou renseignez leur chemin "
        "dans le fichier de configuration.",
    )
    return False
