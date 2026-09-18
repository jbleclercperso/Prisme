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
from .board import COLUMN_CHOICES, BoardView
from .actions import ActionError, HistoryEntry
from .config import Config
from .media import PreviewManager, Tools, page_count, probe
from .ratings import Ratings
from .scan import (
    MODE_FILES, MODE_FOLDERS, ScanThread, detect_mode, human_duration,
    human_resolution, human_size, known_media, list_entries,
)
from .transfer import Transfer, TransferQueue
from .trash import SessionTrash
from .tree import TreePanel
from .widgets import (
    STYLESHEET, CommandBar, DestinationsDialog, FilterBar, PageBar, PreviewGrid,
    AdvancedFilterBar, FocusPlayer, RootBar, SinglePlayer, StarStrip,
    TrashDialog,
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
        self.plans: dict = {}       # cle "chemin@page" -> plan d'apercus
        self.pages: dict = {}       # page d'apercus courante par element
        self.sort_mode = ""         # "" | "desc" | "asc" : classement des apercus
        self.criteria: dict = {}    # filtres chiffres de la planche
        self.history: list = []
        self.stats = {"moved": 0, "deleted": 0, "skipped": 0}
        self.scan_thread: ScanThread | None = None
        self.scanning = False
        # Pile des dossiers traverses, pour pouvoir remonter d'ou l'on vient.
        self.levels: list = []
        self._restore_id = ""
        # Historique de navigation, distinct de la pile des niveaux : il retient
        # les endroits visites, y compris lateralement, pour un vrai « Precedent ».
        self.visited: list = []

        self.ratings = Ratings(parent=self)
        self.board_view = self.cfg["board_view"]

        self.trash = SessionTrash(self)
        self.trash.changed.connect(lambda count: self.root_bar.set_trash(count))

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
        self.root_bar.openTrash.connect(self.open_trash)
        self.root_bar.toggleBoard.connect(self.toggle_board)
        self.root_bar.pickRandom.connect(self.pick_random)
        self.root_bar.goBack.connect(self.go_back)
        self.root_bar.columnsChanged.connect(self.set_board_columns)
        self.root_bar.set_columns_choices(COLUMN_CHOICES, self.cfg["board_columns"])
        self.root_bar.set_board(self.board_view)
        self.root_bar.set_muted(self.cfg["muted"])
        layout.addWidget(self.root_bar)

        self.filter_bar = FilterBar(sort_page)
        self.filter_bar.changed.connect(self.apply_filter)
        self.filter_bar.released.connect(self.setFocus)
        self.filter_bar.set_terms(self.cfg["filter_include"], self.cfg["filter_exclude"])
        layout.addWidget(self.filter_bar)

        self.advanced_filter = AdvancedFilterBar(sort_page)
        self.advanced_filter.changed.connect(self.on_advanced_filter)
        self.advanced_filter.hide()
        layout.addWidget(self.advanced_filter)

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

        self.page_bar = PageBar(sort_page)
        self.page_bar.previousPage.connect(lambda: self.change_page(-1))
        self.page_bar.nextPage.connect(lambda: self.change_page(1))
        self.page_bar.toggleSort.connect(self.cycle_sort)
        self.sort_mode = self.cfg["sort_mode"]
        self.page_bar.set_sort(self.sort_mode)
        self.page_bar.hide()
        layout.addWidget(self.page_bar)

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
        self.grid.playRequested.connect(self.play_in_app)
        self.single = SinglePlayer(
            self.cfg["thumb_count"], self.cfg["scroll_seconds"], self.viewer
        )
        self.board = BoardView(
            self.cfg["preview_seconds"], self.cfg["board_columns"], self.viewer
        )
        self.board.openRequested.connect(self.on_board_open)
        self.board.rateRequested.connect(self.on_board_rate)
        self.board.previewNeeded.connect(self.on_board_preview)
        self.board.playRequested.connect(self.play_in_app)
        self.viewer.addWidget(self.grid)
        self.viewer.addWidget(self.single)
        self.viewer.addWidget(self.board)
        middle.addWidget(self.viewer, 1)
        layout.addLayout(middle, 1)

        self.stars = StarStrip(22, sort_page)
        self.stars.rated.connect(self.rate_current)

        self.commands = CommandBar(sort_page)
        # Les memes actions qu'au clavier, accessibles a la souris.
        self.commands.deleteRequested.connect(self.on_command_delete)
        self.commands.skipRequested.connect(self.on_command_skip)
        self.commands.moveRequested.connect(self.on_command_move)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addWidget(self.commands, 1)
        bottom.addWidget(self.stars, 0, Qt.AlignBottom)
        layout.addLayout(bottom)

        hint = QLabel(
            "←/→ naviguer   ·   molette avancer/reculer   ·   Ctrl+Z annuler   "
            "·   Ctrl+F filtrer   ·   Ctrl+T arborescence   ·   Ctrl+M son   "
            "·   Ctrl+O ouvrir   ·   Ctrl+D destinations   ·   Entrée pause   "
            "·   Ctrl+←/→ page d'aperçus   ·   Ctrl+↓ entrer dans le dossier   "
            "·   Ctrl+P planche   ·   Ctrl+H au hasard   ·   0…5 noter   "
            "·   Ctrl+molette zoomer   ·   Ctrl+B corbeille   ·   Échap remonter",
            sort_page,
        )
        hint.setObjectName("hint")
        layout.addWidget(hint)

        self.stack.addWidget(sort_page)

        self.done_page = DonePage(self)
        self.done_page.rescan.clicked.connect(self.refresh_root)
        self.done_page.change.clicked.connect(self.choose_root)
        self.stack.addWidget(self.done_page)

        self.focus = FocusPlayer(self.cfg["scroll_seconds"], sort_page)
        self.focus.closed.connect(self.close_focus)
        self.focus.hide()

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
                   reset_levels: bool = True, restore_id: str = "",
                   use_cache: bool = True) -> None:
        if root is None or not Path(root).is_dir():
            QMessageBox.warning(self, "Dossier introuvable", f"{root} n'existe plus.")
            return
        self.stop_scan()
        if self.root is not None and Path(root) != self.root:
            self.visited.append({
                "root": self.root, "mode": self.mode,
                "levels": list(self.levels), "board": self.board_view,
                "item_id": self.current.item_id if self.current else "",
            })
            del self.visited[:-40]
            self.root_bar.set_can_go_back(True)
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

        self.preview.tune_for(self.root)
        self.trash.set_base(self.levels[0]["root"] if self.levels else self.root)
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
        self.scan_thread = ScanThread(
            self.root, self.mode, self.cfg["skip_hidden"],
            use_cache and self.cfg["use_scan_cache"], self,
        )
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

    # ------------------------------------------------------------------
    # Corbeille de session
    # ------------------------------------------------------------------
    def open_trash(self) -> None:
        """Liste ce qui a été écarté, avec de quoi le remettre en place."""
        if not self.trash.count:
            self.show_banner("La corbeille de session est vide", "#2a2f38")
            return
        dialog = TrashDialog(self.trash, self)
        dialog.exec()
        self.setFocus()
        # Un élément restauré redevient triable.
        for entry_path, item in list(self._restored_items()):
            item.status = ""
            item.status_detail = ""
        self.update_counter()
        if self.current is not None:
            self.show_item(self.index)

    def _restored_items(self):
        """Éléments dont la suppression a été défaite depuis la corbeille."""
        still_gone = {str(entry.origin) for entry in self.trash.entries}
        for item in self.all_items:
            if item.status == "deleted" and item.item_id not in still_gone:
                if Path(item.path).exists():
                    self.stats["deleted"] = max(0, self.stats["deleted"] - 1)
                    yield item.item_id, item

    def _flush_trash_on_close(self) -> None:
        """Envoie le contenu de la corbeille de session vers celle de Windows.

        Sans rien demander : la corbeille de Windows rend l'opération réversible
        depuis l'explorateur, et une question posée à chaque fermeture finirait
        par être approuvée sans être lue.
        """
        _done, problem = self.trash.flush(self.cfg["delete_mode"])
        if problem:
            QMessageBox.warning(
                self, "Corbeille incomplète",
                "Certains éléments écartés n'ont pas pu rejoindre la corbeille de "
                f"Windows. Ils restent dans « {self.trash.FOLDER_NAME} ».\n\n"
                f"Dernière erreur : {problem}",
            )

    def refresh_root(self) -> None:
        """Relit tout le disque, sans se fier a l'analyse precedente."""
        if self.root is None:
            return
        self.show_banner("Réanalyse complète en cours…", "#22303f")
        self.start_root(self.root, self.mode, reset_levels=False, use_cache=False)

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

    def go_back(self) -> bool:
        """Revient a l'endroit precedemment visite, quel qu'en soit le niveau."""
        if not self.visited:
            self.show_banner("Rien avant cet endroit", "#2a2f38")
            return False
        previous = self.visited.pop()
        self.root_bar.set_can_go_back(bool(self.visited))
        self.levels = list(previous["levels"])
        if previous["board"] != self.board_view:
            self.board_view = previous["board"]
            self.cfg["board_view"] = self.board_view
            self.root_bar.set_board(self.board_view)
        # start_root empilerait a nouveau : on neutralise le temps du retour.
        target, self.root = previous["root"], None
        self.start_root(target, previous["mode"], reset_levels=False,
                        restore_id=previous["item_id"])
        return True

    def set_board_columns(self, columns: int) -> None:
        self.cfg["board_columns"] = columns
        self.cfg.save()
        self.board.set_columns(columns)

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
        if self.board_view:
            self.refresh_board()
            return
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
        thread = self.scan_thread
        if thread is not None and thread.reused:
            self.show_banner(
                f"{thread.reused} dossier(s) relu(s) depuis l'analyse précédente, "
                f"{thread.rescanned} réanalysé(s).   Ctrl+R pour tout revérifier.",
                "#22303f",
            )
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

    def _breadcrumb(self, item) -> str:
        """Chaîne des dossiers depuis la racine du tri jusqu'à celui de l'élément.

        En mode fichier, le seul nom du dossier parent ne suffit pas à se situer :
        plusieurs dossiers portent souvent le même nom à des endroits différents.
        """
        top = Path(self.levels[0]["root"] if self.levels else (self.root or item.path))
        parts = []
        current = Path(item.path).parent
        while True:
            parts.append(current.name or str(current))
            if current == top or current.parent == current or len(parts) > 12:
                break
            current = current.parent
        parts.reverse()
        if len(parts) > 6:
            parts = parts[:2] + ["…"] + parts[-3:]
        return "  ›  ".join(parts)

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
            info = item.info or probe(item.path)
            item.info = info
            # La durée rejoint le titre : c'est ce qu'on veut savoir en premier
            # d'une vidéo, et la ligne d'informations est déjà chargée.
            duration = human_duration(info["duration"]) if info.get("duration") else ""
            self.item_title.setText(
                f"{item.name}   —   {duration}" if duration else item.name
            )
            parts = []
            if info.get("height"):
                parts.append(human_resolution(info["height"]))
            if info.get("width"):
                parts.append(f"{info['width']}×{info['height']}")
            parts.append(human_size(item.size))
            if info.get("codec"):
                parts.append(info["codec"])
        if item.mtime:
            parts.append("modifié le " + datetime.fromtimestamp(item.mtime).strftime("%d/%m/%Y"))
        self.item_subtitle.setText("   ·   ".join(parts))

        self.stars.show()
        self.stars.set_value(self.ratings.get(item.path))
        crumbs = self._breadcrumb(item)
        self.item_parent.setText(crumbs)
        self.item_parent.setToolTip(str(Path(item.path).parent))

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
            self.grid.set_item(self._plan_key(item, self.page_of(item)), "…")
            if not item.videos:
                self.grid.set_no_videos("aucune vidéo")
        else:
            self.single.set_item(str(item.path), "…")

        self._sort_videos(item)
        self._request_previews(item, current=True)
        self._update_page_bar()
        keep = {self._plan_key(item, self.page_of(item))}
        for offset in (1, 2):
            if self.index + offset < len(self.items):
                nxt = self.items[self.index + offset]
                keep.add(self._plan_key(nxt, self.page_of(nxt)))
                self._request_previews(nxt, current=False)
        self.preview.cancel_except(keep)

    # ------------------------------------------------------------------
    # Apercus, par pages de `thumb_count`
    # ------------------------------------------------------------------
    def page_of(self, item) -> int:
        return self.pages.get(item.item_id, 0)

    def total_pages(self, item) -> int:
        if item.kind != MODE_FOLDERS:
            return 1
        return page_count(item.videos, self.cfg["thumb_count"])

    def _plan_key(self, item, page: int) -> str:
        """Identifiant porte par les signaux : il doit distinguer les pages."""
        return f"{item.item_id}@{page}"

    def _request_previews(self, item, current: bool, page: int | None = None) -> None:
        if not item.videos or item.locked:
            return
        page = self.page_of(item) if page is None else page
        key = self._plan_key(item, page)
        plan = self.plans.get(key)
        if plan is None:
            self.preview.request_plan(
                key, item.videos, self.cfg["thumb_count"],
                page=page, one_per_video=item.kind == MODE_FOLDERS,
            )
            return
        if current:
            self._apply_plan(item, plan)
        for slot, entry in enumerate(plan):
            self.preview.request_thumb(key, slot, entry[0], entry[1])

    def _apply_plan(self, item, plan: list) -> None:
        viewer = self.grid if item.kind == MODE_FOLDERS else self.single
        viewer.set_plan(plan)

    def _current_key(self) -> str:
        current = self.current
        return self._plan_key(current, self.page_of(current)) if current else ""

    def on_plan_ready(self, key: str, plan: list) -> None:
        self.plans[key] = plan
        if key.startswith("board@"):
            position = self._board_position_of(key)
            if position >= 0 and plan:
                self.board.set_source(position, plan[0])
                self.preview.request_thumb(key, 0, plan[0][0], plan[0][1])
            return
        current = self.current
        if current is not None and key == self._current_key():
            self._apply_plan(current, plan)
            self._update_page_bar()
        for slot, entry in enumerate(plan):
            self.preview.request_thumb(key, slot, entry[0], entry[1])

    def on_thumb_ready(self, key: str, slot: int, path: str) -> None:
        if key.startswith("board@"):
            position = self._board_position_of(key)
            if position >= 0:
                self.board.set_thumb(position, path)
            return
        current = self.current
        if current is None or key != self._current_key():
            return
        viewer = self.grid if current.kind == MODE_FOLDERS else self.single
        viewer.set_thumb(slot, path)

    def on_thumb_failed(self, key: str, slot: int) -> None:
        if key.startswith("board@"):
            return
        current = self.current
        if current is None or key != self._current_key():
            return
        viewer = self.grid if current.kind == MODE_FOLDERS else self.single
        viewer.set_failed(slot)

    def change_page(self, step: int) -> None:
        """Affiche les dix apercus suivants ou precedents du dossier courant."""
        item = self.current
        if item is None or item.kind != MODE_FOLDERS:
            return
        total = self.total_pages(item)
        page = max(0, min(self.page_of(item) + step, total - 1))
        if page == self.page_of(item):
            return
        self.pages[item.item_id] = page
        self.grid.stop()
        self.grid.set_item(self._plan_key(item, page), "…")
        self._request_previews(item, current=True, page=page)
        self._update_page_bar()

    def cycle_sort(self) -> None:
        """Ordre du dossier, puis les plus longues d'abord, puis les plus courtes."""
        self.sort_mode = {"": "desc", "desc": "asc", "asc": ""}[self.sort_mode]
        self.page_bar.set_sort(self.sort_mode)
        self.cfg["sort_mode"] = self.sort_mode
        self.cfg.save()
        # Les plans deja calcules suivaient l'ancien ordre.
        self.plans = {}
        self.pages = {}
        item = self.current
        if item is not None:
            self._sort_videos(item)
            self.grid.set_item(self._plan_key(item, 0), "…")
            self._request_previews(item, current=True, page=0)
            self._update_page_bar()

    def _sort_videos(self, item) -> None:
        """Reclasse les vidéos d'un dossier selon le mode choisi.

        Le tri s'appuie sur les durées déjà sondées ; celles qui ne le sont pas
        encore comptent pour zéro et remonteront au prochain affichage.
        """
        if item.kind != MODE_FOLDERS or not item.videos:
            return
        if not self.sort_mode:
            item.videos.sort(key=lambda path: str(path).lower())
            return
        from .media import PROBE_CACHE
        def duration_of(path):
            cached = PROBE_CACHE.get(Path(path))
            return (cached or {}).get("duration", 0.0)
        item.videos.sort(key=duration_of, reverse=self.sort_mode == "desc")

    def _update_page_bar(self) -> None:
        item = self.current
        if self.board_view or item is None or item.kind != MODE_FOLDERS                 or not item.videos:
            self.page_bar.hide()
            return
        page = self.page_of(item)
        total = self.total_pages(item)
        size = self.cfg["thumb_count"]
        first = page * size + 1
        last = min((page + 1) * size, len(item.videos))
        more = " (les 400 premières)" if len(item.videos) < item.video_count else ""
        self.page_bar.set_state(
            f"aperçus {first}–{last} sur {len(item.videos)}{more}",
            page > 0, page < total - 1,
        )
        self.page_bar.setVisible(total > 1)

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
    # Vue planche et notation
    # ------------------------------------------------------------------
    def toggle_board(self, visible: bool | None = None) -> None:
        """Bascule entre la fiche unique et la planche de cartes."""
        self.board_view = (not self.board_view) if visible is None else visible
        self.cfg["board_view"] = self.board_view
        self.cfg.save()
        self.root_bar.set_board(self.board_view)
        self.advanced_filter.setVisible(self.board_view)
        self._release_media()
        if self.board_view:
            self.refresh_board()
        elif self.items:
            self.show_item(self.index)
        self.setFocus()

    def refresh_board(self) -> None:
        if not self.board_view:
            return
        self.viewer.setCurrentWidget(self.board)
        self.board.set_muted(self.cfg["muted"])
        self.board.set_items(self.items, self.ratings.get)
        self.item_title.setText(
            f"{len(self.items)} élément(s)" if self.items else "Rien à afficher"
        )
        self.item_parent.setText(str(self.root) if self.root else "")
        self.item_subtitle.setText(
            "Survolez une carte pour la lire, cliquez pour l'ouvrir, "
            "notez d'un clic sur les étoiles."
        )
        self.stars.hide()
        # Pagination et tri portent sur les apercus d'un dossier : sans objet ici.
        self.page_bar.hide()

    def on_board_preview(self, position: int) -> None:
        """Une carte réclame son image : on lui construit sa première vignette."""
        if not (0 <= position < len(self.items)):
            return
        item = self.items[position]
        if not item.videos or item.locked:
            return
        key = f"board@{item.item_id}"
        plan = self.plans.get(key)
        if plan is None:
            self.preview.request_plan(
                key, item.videos, 1, page=0,
                one_per_video=item.kind == MODE_FOLDERS,
            )
            return
        self.board.set_source(position, plan[0])
        self.preview.request_thumb(key, 0, plan[0][0], plan[0][1])

    def _board_position_of(self, key: str) -> int:
        item_id = key[len("board@"):]
        for position, item in enumerate(self.items):
            if item.item_id == item_id:
                return position
        return -1

    def on_board_open(self, position: int) -> None:
        """Un clic sur une carte ouvre l'élément, sans rien déplacer."""
        if not (0 <= position < len(self.items)):
            return
        self.index = position
        item = self.items[position]
        if item.kind == MODE_FOLDERS:
            self.enter_current()
        else:
            self.toggle_board(False)
            self.show_item(position)

    def on_board_rate(self, position: int, stars: int) -> None:
        if 0 <= position < len(self.items):
            value = self.ratings.set(self.items[position].path, stars)
            self.board.set_stars(position, value)
            self.ratings.flush()

    def rate_current(self, stars: int) -> None:
        item = self.current
        if item is None:
            return
        value = self.ratings.set(item.path, stars)
        self.stars.set_value(value)
        self.ratings.flush()
        self.show_banner(
            f"« {item.name} » : {value} étoile(s)" if value
            else f"« {item.name} » : note effacée", "#2a2f38",
        )

    def pick_random(self) -> None:
        """Se place sur un élément au hasard parmi ceux qui restent à voir."""
        candidates = [i for i, item in enumerate(self.items) if not item.status]
        if not candidates:
            self.show_banner("Plus rien à tirer au sort ici", "#2a2f38")
            return
        import random
        position = random.choice(candidates)
        if self.board_view:
            self.board.scroll_to(position)
            self.index = position
        else:
            self.show_item(position)

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
        """Le clic envoie l'élément en fiche, et ouvre le dossier en planche.

        C'est la seule différence d'intention entre les deux vues : parcourir
        d'un côté, décider de l'autre.
        """
        if self.board_view:
            self.levels.append({
                "root": self.root, "mode": self.mode,
                "item_id": self.current.item_id if self.current else "",
            })
            self.start_root(Path(path), reset_levels=False)
            return
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
        if any(term in name for term in exclude):
            return False
        return self._matches_numeric(item)

    def _matches_numeric(self, item) -> bool:
        """Durée, résolution et note. Ce qu'on ignore encore passe le filtre."""
        rules = self.criteria
        if not rules:
            return True

        stars_min = rules.get("stars", -1)
        if stars_min >= 0 and self.ratings.get(item.path) < stars_min:
            return False

        needs_media = rules.get("duration_op") or rules.get("resolution", 0) > 0
        if not needs_media:
            return True
        duration, height = known_media(item)

        op = rules.get("duration_op")
        if op and duration > 0:
            wanted = rules.get("duration_s", 0)
            if op == "gt" and duration <= wanted:
                return False
            if op == "lt" and duration >= wanted:
                return False

        wanted_height = rules.get("resolution", 0)
        if wanted_height and height > 0:
            if rules.get("resolution_op") == "gte" and height < wanted_height:
                return False
            if rules.get("resolution_op") == "lte" and height > wanted_height:
                return False
        return True

    def on_advanced_filter(self, criteria: dict) -> None:
        self.criteria = criteria
        self.apply_filter(self.cfg["filter_include"], self.cfg["filter_exclude"])

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
        if self.board_view:
            self.refresh_board()
        else:
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
        """Écarte l'élément sans rien détruire : il part dans la corbeille de session."""
        item = self.current
        if item is None or item.locked:
            return self.advance()
        if not Path(item.path).exists():
            self.show_banner(f"Introuvable : {item.name}", "#3a2226")
            return self.advance()
        self._release_media()
        item.status = "pending_delete"
        item.status_detail = "Corbeille"
        self._enqueue(
            Transfer(kind="move", purpose="delete", src=item.path,
                     dest=self.trash.folder_for(item.path),
                     label="Corbeille", item_id=item.item_id),
            f"« {item.name} » → corbeille  ·  Ctrl+Z ou Ctrl+B pour la rouvrir",
            "#3a2226",
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

        if job.kind == "move" and job.purpose == "delete":
            if item is not None:
                item.status = "deleted"
            self.stats["deleted"] += 1
            size = item.size if item is not None else 0
            self.trash.record(Path(job.src), job.result, size)
            self.ratings.rename(job.src, job.result)
            self.history.append(
                HistoryEntry("delete", Path(job.src), job.result, job.label, True)
            )
        elif job.kind == "move":
            if item is not None:
                item.status = "moved"
            self.stats["moved"] += 1
            self.ratings.rename(job.src, job.result)
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
            if getattr(entry, "action", "") == "delete" and entry.dst:
                self.trash.forget(Path(entry.dst))
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
        if self.board_view and item is not None:
            for position, listed in enumerate(self.items):
                if listed is item:
                    self.board.set_state(position, item.status)
                    break

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

    def play_focused(self) -> None:
        """Ouvre en grand ce qui est sous la souris, ou la vidéo courante."""
        slot = self.grid.hovered_slot
        if self.viewer.currentWidget() is self.grid and slot >= 0:
            tile = self.grid.tiles[slot]
            if tile.video:
                return self.play_in_app(tile.video, tile.ts)
        if self.board_view and self.board.hovered >= 0:
            card = self.board.cards[self.board.hovered]
            if card.video:
                return self.play_in_app(card.video, card.ts)
        item = self.current
        if item is not None and item.kind == MODE_FILES:
            return self.play_in_app(str(item.path))
        self.show_banner("Survolez une vidéo, ou double-cliquez dessus", "#2a2f38")

    def play_in_app(self, path: str, start_s: float = 0.0) -> None:
        """Ouvre la vidéo en grand dans l'application, sans passer la main au système."""
        if not path or not Path(path).exists():
            self.show_banner("Vidéo introuvable", "#3a2226")
            return
        self.grid.stop()
        self.board.stop()
        self.single.player.pause()
        page = self.stack.widget(PAGE_SORT)
        self.focus.setGeometry(page.rect())
        self.focus.play(path, start_s, self.cfg["muted"])
        self.focus.setFocus()

    def close_focus(self) -> None:
        # Differe : on ne demonte pas un lecteur depuis son propre gestionnaire
        # d'evenement, sous peine de bloquer le moteur multimedia.
        QTimer.singleShot(0, self._finish_close_focus)

    def _finish_close_focus(self) -> None:
        self.focus.stop()
        self.setFocus()
        if not self.board_view and self.viewer.currentWidget() is self.single:
            self.single.player.play()

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
        if event.modifiers() & Qt.AltModifier and key == Qt.Key_Left:
            self.go_back()
            return

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
            if key == Qt.Key_B:
                return self.open_trash()
            if key == Qt.Key_P:
                return self.toggle_board()
            if key == Qt.Key_H:
                return self.pick_random()

            if key == Qt.Key_R:
                return self.refresh_root()
            if key == Qt.Key_Right:
                return self.change_page(1)
            if key == Qt.Key_Left:
                return self.change_page(-1)
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
        if key == Qt.Key_F:
            return self.play_focused()

        text = event.text().lower().strip()
        if text in ("0", "1", "2", "3", "4", "5"):
            return self.rate_current(int(text))
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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self.focus.isHidden():
            self.focus.setGeometry(self.stack.widget(PAGE_SORT).rect())

    def closeEvent(self, event):
        self.focus.stop()
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
        self._flush_trash_on_close()
        self.preview.shutdown()
        self.ratings.flush()
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
