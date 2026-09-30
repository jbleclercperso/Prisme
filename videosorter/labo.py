"""Labo IA (essai) : la fenêtre où l'on essaie la recherche par description.

Un bac a sable, a part du reste : on y choisit un moteur, on analyse un
dossier, on decrit une scene, on essaie des « tags IA ». Rien de ce qu'on y
fait ne touche a la collection, a ses notes ou a ses mots-cles ; le labo a
son propre index (voir ia.py). Il s'ouvre par ⋯ › Collection › Labo IA (essai)…

La fenetre n'est pas modale et survit a sa fermeture : on la cache, une
analyse en cours continue, et la rouvrir retrouve tout en l'etat. Le repli
(Ctrl+K) la ferme comme tout dialogue ouvert — elle montre des images.
"""
from __future__ import annotations

import sys
import time

import shiboken6
from PySide6.QtCore import QEvent, QProcess, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QMenu, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider,
    QSpinBox, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from . import ia

LAB_STYLE = """
QLabel#labBadge { background: #3a2c12; color: #ffcf7a; border: 1px solid #6b5220;
                  border-radius: 5px; padding: 2px 9px; font-weight: 600;
                  font-size: 12px; }
QLabel#labSection { color: #cdd5df; font-weight: 600; font-size: 14px;
                    padding-top: 8px; }
QLabel#labNote { color: #8b94a1; font-size: 12px; }
QLabel#labEngine { color: #b6c0cc; }
QListWidget#labGrid { background: #101216; border: 1px solid #232932;
                      border-radius: 8px; }
QListWidget#labGrid::item { color: #cdd5df; border-radius: 6px; padding: 3px; }
QListWidget#labGrid::item:selected { background: #1d2a40; color: #ffffff; }
QListWidget#labTags { background: #101216; border: 1px solid #232932;
                      border-radius: 6px; }
QPlainTextEdit#labLog { font-family: Consolas, monospace; font-size: 12px;
                        background: #0d0f12; }
"""

THUMB = QSize(240, 135)


def open_lab(window) -> "LaboDialog":
    """Ouvre le labo — le même à chaque fois, avec son état."""
    lab = getattr(window, "_ai_lab", None)
    if lab is None or not shiboken6.isValid(lab):
        lab = LaboDialog(window)
        window._ai_lab = lab
    lab.recheck()
    lab.refresh_scope()
    lab.show()
    lab.raise_()
    lab.activateWindow()
    return lab


def _fr(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _count(value: int) -> str:
    return f"{int(value):,}".replace(",", " ")


def _clock(seconds: float) -> str:
    seconds = int(max(0.0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def _size(num: int) -> str:
    if num >= 1024 ** 3:
        return f"{_fr(num / 1024 ** 3)} Go"
    return f"{_fr(num / 1024 ** 2)} Mo"


def _duration_text(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)} s"
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    return f"{int(seconds // 3600)} h {int(seconds % 3600 // 60):02d}"


def thumb_icon(path: str, size: QSize = THUMB) -> QIcon:
    """La vignette réduite, posée sur un fond sombre à taille fixe : la grille
    reste alignee quel que soit le format de l'image."""
    canvas = QPixmap(size)
    canvas.fill(QColor("#0b0d10"))
    if path:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        found = reader.size()
        if found.isValid():
            # Decodee directement a la bonne taille : un JPEG se lit alors
            # bien plus vite, et la grille se remplit sans a-coup.
            reader.setScaledSize(found.scaled(size, Qt.KeepAspectRatio))
        image = reader.read()
        if not image.isNull():
            painter = QPainter(canvas)
            painter.drawImage((size.width() - image.width()) // 2,
                              (size.height() - image.height()) // 2, image)
            painter.end()
    return QIcon(canvas)


class Installer:
    """Lance les commandes pip l'une après l'autre, et en rapporte la sortie.

    QProcess plutot qu'un fil : la sortie arrive par evenements dans le fil
    de l'interface, ligne a ligne, sans rien bloquer.
    """

    def __init__(self, commands: list, on_line, on_progress, on_done, parent):
        self.commands = list(commands)
        self.on_line = on_line
        self.on_progress = on_progress
        self.on_done = on_done
        self.parent = parent
        self.process = None
        self._rest = ""

    def start(self) -> None:
        self._next()

    def running(self) -> bool:
        return self.process is not None

    def stop(self) -> None:
        if self.process is not None:
            self.commands = []
            self.process.kill()

    def _next(self) -> None:
        if not self.commands:
            self.process = None
            self.on_done(True)
            return
        command = self.commands.pop(0)
        self.on_line("> " + " ".join(command))
        process = QProcess(self.parent)
        process.setProcessChannelMode(QProcess.MergedChannels)
        env = process.processEnvironment()
        if env.isEmpty():
            from PySide6.QtCore import QProcessEnvironment
            env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        process.setProcessEnvironment(env)
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._error)
        self.process = process
        process.start(command[0], command[1:])

    def _read(self) -> None:
        if self.process is None:
            return
        text = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        text = (self._rest + text).replace("\r\n", "\n")
        lines = text.split("\n")
        self._rest = lines.pop()
        for line in lines:
            # pip redessine sa barre de progression par retours chariot : on
            # n'en garde que le dernier etat, a part, pour ne pas noyer le
            # journal sous mille lignes.
            last = line.split("\r")[-1]
            if "━" in last or "eta" in last:
                self.on_progress(last.strip())
            elif last.strip():
                self.on_line(last.rstrip())
        if "\r" in self._rest:
            self.on_progress(self._rest.split("\r")[-1].strip())

    def _finished(self, code, _status=None) -> None:
        self._read()
        if self._rest.strip():
            self.on_line(self._rest.rstrip())
        self._rest = ""
        process, self.process = self.process, None
        if process is not None:
            process.deleteLater()
        if code != 0:
            self.commands = []
            self.on_done(False)
            return
        self._next()

    def _error(self, error) -> None:
        if error == QProcess.FailedToStart and self.process is not None:
            self.on_line("Impossible de lancer la commande.")
            process, self.process = self.process, None
            process.deleteLater()
            self.commands = []
            self.on_done(False)


class LaboDialog(QDialog):
    """Le labo : moteur, analyse, recherche par description, tags IA."""

    def __init__(self, window, store: "ia.Store | None" = None):
        super().__init__(window)
        self.main = window
        self.setWindowTitle("Labo IA — essai")
        self.setModal(False)
        self.setStyleSheet(LAB_STYLE)
        self.resize(1280, 820)
        self.store = store or ia.Store()
        self.encoder = None
        self.loader = None
        self.indexer = None
        self.installer = None
        self._loading_preset = None
        self._download_base = 0
        self._jobs: set = set()
        self._search_serial = 0
        self._shown_matrix = None
        self._thumb_queue: list = []
        self._tag_vectors: dict = {}
        self._tag_results: dict = {}
        self._neutral = None
        self._auto_tried = False
        self.last_title = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        self.pages = QStackedWidget(self)
        outer.addWidget(self.pages)
        self.missing_page = self._build_missing_page()
        self.lab_page = self._build_lab_page()
        self.pages.addWidget(self.missing_page)
        self.pages.addWidget(self.lab_page)

        self.thumb_timer = QTimer(self)
        self.thumb_timer.setInterval(0)
        self.thumb_timer.timeout.connect(self._load_some_thumbs)
        self.download_timer = QTimer(self)
        self.download_timer.setInterval(1000)
        self.download_timer.timeout.connect(self._show_download)
        self.recheck()

    # ------------------------------------------------------------------
    # Dependances manquantes
    # ------------------------------------------------------------------
    def _build_missing_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        head = QHBoxLayout()
        title = QLabel("Labo IA", page)
        title.setObjectName("title")
        badge = QLabel("ESSAI", page)
        badge.setObjectName("labBadge")
        head.addWidget(title)
        head.addWidget(badge)
        head.addStretch(1)
        layout.addLayout(head)
        self.missing_text = QLabel(page)
        self.missing_text.setWordWrap(True)
        self.missing_text.setTextFormat(Qt.RichText)
        layout.addWidget(self.missing_text)
        row = QHBoxLayout()
        self.install_kind = QComboBox(page)
        self.install_kind.addItem("Processeur seul (≈ 300 Mo à télécharger)", False)
        self.install_kind.addItem(
            "Carte graphique NVIDIA — CUDA 12.8 (≈ 3 Go à télécharger)", True)
        self.install_kind.currentIndexChanged.connect(self._show_command)
        row.addWidget(self.install_kind, 1)
        self.install_button = QPushButton("Installer", page)
        self.install_button.setObjectName("enter")
        self.install_button.clicked.connect(self.install)
        row.addWidget(self.install_button)
        layout.addLayout(row)
        self.command_line = QLineEdit(page)
        self.command_line.setReadOnly(True)
        self.command_line.setToolTip("La même chose, à lancer soi-même dans un terminal")
        layout.addWidget(self.command_line)
        self.install_progress = QLabel("", page)
        self.install_progress.setObjectName("labNote")
        layout.addWidget(self.install_progress)
        self.install_log = QPlainTextEdit(page)
        self.install_log.setObjectName("labLog")
        self.install_log.setReadOnly(True)
        self.install_log.setMaximumBlockCount(4000)
        layout.addWidget(self.install_log, 1)
        bottom = QHBoxLayout()
        self.install_state = QLabel("", page)
        self.install_state.setWordWrap(True)
        bottom.addWidget(self.install_state, 1)
        self.reopen_button = QPushButton("Rouvrir le labo", page)
        self.reopen_button.setEnabled(False)
        self.reopen_button.clicked.connect(self.recheck)
        bottom.addWidget(self.reopen_button)
        layout.addLayout(bottom)
        return page

    def _show_command(self, *_args) -> None:
        commands = ia.install_commands(bool(self.install_kind.currentData()))
        self.command_line.setText("   puis   ".join(" ".join(c) for c in commands))

    def recheck(self) -> None:
        """Choisit la page : ce qu'il manque, ou le labo lui-même."""
        if self.installer is not None and self.installer.running():
            return
        missing = ia.missing_packages()
        if not missing:
            if self.pages.currentWidget() is not self.lab_page:
                self.pages.setCurrentWidget(self.lab_page)
                self._preset_changed()
            return
        self.pages.setCurrentWidget(self.missing_page)
        why = ia.can_install()
        names = ", ".join(f"<b>{name}</b>" for name in missing)
        self.missing_text.setText(
            "<p>Le labo essaie une recherche par <b>description de scène</b> "
            "(« fille rousse sous la douche »), calculée entièrement sur cette "
            "machine par un modèle de la famille CLIP.</p>"
            f"<p>Il lui manque : {names}. Ces bibliothèques ne font pas partie "
            "de Prisme, qui fonctionne sans elles ; elles s'installent dans ce "
            f"Python-ci (<code>{sys.executable}</code>).</p>"
            + (f"<p style='color:#ff9a9a'>{why}</p>" if why else
               "<p>Sans carte NVIDIA, prenez « Processeur seul ». Le modèle "
               "lui-même (≈ 1,5 Go) se télécharge plus tard, au premier "
               "chargement.</p>"))
        self.install_button.setEnabled(not why)
        self.install_kind.setEnabled(not why)
        self._show_command()

    def install(self, commands: list | None = None) -> None:
        """Lance pip en tâche de fond, sortie en direct dans le journal."""
        if self.installer is not None and self.installer.running():
            return
        if not commands:
            commands = ia.install_commands(bool(self.install_kind.currentData()))
        self.install_log.clear()
        self.install_button.setEnabled(False)
        self.reopen_button.setEnabled(False)
        self.install_state.setText("Installation en cours… (plusieurs minutes)")
        self.installer = Installer(commands, self._install_line,
                                   self.install_progress.setText,
                                   self._install_done, self)
        self.installer.start()

    def _install_line(self, line: str) -> None:
        self.install_log.appendPlainText(line)

    def _install_done(self, ok: bool) -> None:
        self.install_button.setEnabled(True)
        self.install_progress.setText("")
        if ok:
            self.install_state.setText(
                "Installation terminée. Rouvrez le labo pour vous en servir.")
            self.reopen_button.setEnabled(True)
        else:
            self.install_state.setText(
                "L'installation a échoué : voir le journal ci-dessus.")

    # ------------------------------------------------------------------
    # Le labo
    # ------------------------------------------------------------------
    def _section(self, text: str, parent) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("labSection")
        return label

    def _note(self, text: str, parent) -> QLabel:
        label = QLabel(text, parent)
        label.setObjectName("labNote")
        label.setWordWrap(True)
        return label

    def _build_lab_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        head = QHBoxLayout()
        title = QLabel("Labo IA", page)
        title.setObjectName("title")
        badge = QLabel("ESSAI", page)
        badge.setObjectName("labBadge")
        badge.setToolTip("Un banc d'essai : rien ici ne touche à la collection, "
                         "et tout peut changer.")
        head.addWidget(title)
        head.addWidget(badge)
        head.addSpacing(12)
        self.status_label = QLabel("", page)
        self.status_label.setObjectName("labEngine")
        head.addWidget(self.status_label, 1)
        layout.addLayout(head)
        layout.addWidget(self._note(
            "Tout est calculé sur cette machine : ni image ni description ne "
            "quitte l'ordinateur. Seul le modèle se télécharge, une fois, "
            "depuis Hugging Face.", page))

        splitter = QSplitter(Qt.Horizontal, page)
        layout.addWidget(splitter, 1)

        # -- a gauche : les reglages ----------------------------------------
        scroll = QScrollArea(splitter)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side = QWidget(scroll)
        scroll.setWidget(side)
        left = QVBoxLayout(side)
        left.setContentsMargins(0, 0, 10, 0)
        left.setSpacing(6)

        left.addWidget(self._section("Moteur", side))
        row = QHBoxLayout()
        self.preset_box = QComboBox(side)
        for preset in ia.PRESETS:
            self.preset_box.addItem(preset.label, preset.key)
        chosen = self.store.meta("preset", ia.PRESETS[0].key)
        at = self.preset_box.findData(chosen)
        self.preset_box.setCurrentIndex(max(0, at))
        self.preset_box.currentIndexChanged.connect(self._preset_changed)
        row.addWidget(self.preset_box, 1)
        self.load_button = QPushButton("Charger", side)
        self.load_button.clicked.connect(self.load_engine)
        row.addWidget(self.load_button)
        left.addLayout(row)
        self.engine_label = self._note("", side)
        left.addWidget(self.engine_label)

        left.addWidget(self._section("Portée", side))
        self.scope_box = QComboBox(side)
        self.scope_box.setToolTip(
            "Ce qu'on analyse, et où l'on cherche. Chercher ne voit que ce qui "
            "a déjà été analysé.")
        self.scope_box.currentIndexChanged.connect(self._scope_changed)
        left.addWidget(self.scope_box)

        left.addWidget(self._section("1. Analyser", side))
        row = QHBoxLayout()
        row.addWidget(QLabel("Images par vidéo", side))
        self.frames_spin = QSpinBox(side)
        self.frames_spin.setRange(3, 16)
        self.frames_spin.setValue(ia.DEFAULT_FRAMES)
        self.frames_spin.setToolTip(
            "Instants échelonnés dans chaque vidéo. Les vignettes que Prisme a "
            "déjà faites servent d'abord ; les autres sont extraites par "
            "ffmpeg dans le même cache, où Prisme les retrouvera.\n"
            "Neuf : ce sont les instants de l'aperçu au survol.")
        row.addWidget(self.frames_spin)
        row.addStretch(1)
        self.analyze_button = QPushButton("Analyser", side)
        self.analyze_button.setObjectName("enter")
        self.analyze_button.clicked.connect(self.toggle_analysis)
        row.addWidget(self.analyze_button)
        left.addLayout(row)
        self.progress = QProgressBar(side)
        self.progress.setTextVisible(True)
        self.progress.hide()
        left.addWidget(self.progress)
        self.run_label = self._note("", side)
        left.addWidget(self.run_label)
        self.index_label = self._note("", side)
        left.addWidget(self.index_label)

        left.addWidget(self._section("2. Décrire la scène", side))
        row = QHBoxLayout()
        self.query = QLineEdit(side)
        self.query.setClearButtonEnabled(True)
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1)
        self.search_button = QPushButton("Chercher", side)
        self.search_button.clicked.connect(self.search)
        row.addWidget(self.search_button)
        left.addLayout(row)
        row = QHBoxLayout()
        self.calibrate = QCheckBox("Calibrer", side)
        self.calibrate.setChecked(self.store.meta("calibrate", "1") == "1")
        self.calibrate.setToolTip(
            "Retire à chaque image sa ressemblance avec une phrase neutre "
            "(« une photo », « une image »).\nLes images qui ressemblent un peu "
            "à tout (sombres, floues) cessent alors de remonter partout.\n"
            "Décochez pour comparer avec le score brut.")
        self.calibrate.toggled.connect(self._calibration_changed)
        row.addWidget(self.calibrate)
        row.addStretch(1)
        row.addWidget(QLabel("Résultats", side))
        self.limit_spin = QSpinBox(side)
        self.limit_spin.setRange(12, 400)
        self.limit_spin.setSingleStep(12)
        self.limit_spin.setValue(60)
        row.addWidget(self.limit_spin)
        left.addLayout(row)

        left.addWidget(self._section("3. Tags IA", side))
        left.addWidget(self._note(
            "Une description par ligne. Un tag retient les vidéos qui se "
            "détachent nettement des autres pour cette description.", side))
        self.tags_edit = QPlainTextEdit(side)
        self.tags_edit.setPlaceholderText(
            "fille rousse sous la douche\nen extérieur, plage\nlingerie noire")
        self.tags_edit.setPlainText("\n".join(self.store.tags()))
        self.tags_edit.setFixedHeight(96)
        left.addWidget(self.tags_edit)
        row = QHBoxLayout()
        row.addWidget(QLabel("Seuil", side))
        self.z_slider = QSlider(Qt.Horizontal, side)
        self.z_slider.setRange(5, 40)
        self.z_slider.setValue(int(round(10 * float(
            self.store.meta("tag_z", "1.5") or 1.5))))
        self.z_slider.setToolTip(
            "De combien d'écarts-types une vidéo doit dépasser la moyenne de "
            "la portée, pour CETTE description.\nLes scores bruts de CLIP ne "
            "se comparent pas d'une description à l'autre ; celui-ci, si.\n"
            "1,5 : de l'ordre des 5 à 10 % qui se détachent le plus.")
        self.z_slider.valueChanged.connect(self._thresholds_changed)
        row.addWidget(self.z_slider, 1)
        self.z_label = QLabel("", side)
        self.z_label.setMinimumWidth(52)
        row.addWidget(self.z_label)
        left.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Au plus", side))
        self.share_spin = QSpinBox(side)
        self.share_spin.setRange(1, 100)
        self.share_spin.setSuffix(" % de la portée")
        self.share_spin.setValue(int(self.store.meta("tag_share", "10") or 10))
        self.share_spin.setToolTip(
            "Plafond : une description si vague qu'elle « trouverait » la "
            "moitié des vidéos n'est plus un tag.")
        self.share_spin.valueChanged.connect(self._thresholds_changed)
        row.addWidget(self.share_spin)
        row.addStretch(1)
        self.count_button = QPushButton("Compter", side)
        self.count_button.clicked.connect(self.count_tags)
        row.addWidget(self.count_button)
        left.addLayout(row)
        self.tags_list = QListWidget(side)
        self.tags_list.setObjectName("labTags")
        self.tags_list.setMinimumHeight(120)
        self.tags_list.itemClicked.connect(self.show_tag)
        left.addWidget(self.tags_list)

        self.hide_on_open = QCheckBox("Masquer le labo en ouvrant une vidéo", side)
        self.hide_on_open.setChecked(self.store.meta("hide_on_open", "1") == "1")
        self.hide_on_open.setToolTip(
            "Il revient tel quel par ⋯ › Collection › Labo IA (essai)…")
        self.hide_on_open.toggled.connect(
            lambda on: self.store.set_meta("hide_on_open", "1" if on else "0"))
        left.addWidget(self.hide_on_open)
        left.addStretch(1)

        # -- a droite : les resultats ----------------------------------------
        right = QWidget(splitter)
        column = QVBoxLayout(right)
        column.setContentsMargins(10, 0, 0, 0)
        self.results_title = QLabel("Aucune recherche pour l'instant", right)
        self.results_title.setObjectName("subtitle")
        self.results_title.setWordWrap(True)
        column.addWidget(self.results_title)
        self.results_note = self._note(
            "Double-clic ou Entrée : ouvrir la vidéo à l'instant trouvé. "
            "Clic droit : plus comme celle-ci.", right)
        column.addWidget(self.results_note)
        self.grid = QListWidget(right)
        self.grid.setObjectName("labGrid")
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(THUMB)
        self.grid.setGridSize(QSize(THUMB.width() + 18, THUMB.height() + 58))
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(4)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._grid_menu)
        self.grid.itemActivated.connect(self.open_item)
        column.addWidget(self.grid, 1)

        splitter.addWidget(scroll)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 900])
        self._thresholds_changed(save=False)
        return page

    # ------------------------------------------------------------------
    # Moteur
    # ------------------------------------------------------------------
    def preset(self) -> ia.Preset:
        return ia.preset_by_key(self.preset_box.currentData())

    def _preset_changed(self, *_args) -> None:
        preset = self.preset()
        self.store.set_meta("preset", preset.key)
        self.query.setPlaceholderText(
            "ex. fille rousse sous la douche" if preset.lang == "fr"
            else "e.g. redhead woman in the shower")
        if self.encoder is not None and self.encoder.name != preset.name:
            # Un seul modele en memoire : chacun pese plus d'un gigaoctet, et
            # leurs vecteurs ne se comparent pas.
            self.encoder = None
        loaded = self.encoder is not None
        self._tag_vectors = {}
        self._tag_results = {}
        self._refresh_tag_list()
        self._update_engine_text()
        self._update_index_text()
        self._update_enabled()
        if (not loaded and self.loader is None and ia.is_downloaded(preset)
                and self.isVisible()):
            # Deja telecharge : on le charge sans le demander, c'est ce que
            # l'on vient faire ici.
            self.load_engine()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if (event.type() == QEvent.ActivationChange and self.isActiveWindow()
                and self.indexer is None):
            # On a pu changer de dossier dans Prisme entre-temps : la portee
            # suit ce qu'il montre maintenant.
            self.refresh_scope()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if (not self._auto_tried and self.pages.currentWidget() is self.lab_page
                and self.encoder is None and ia.is_downloaded(self.preset())):
            self._auto_tried = True
            QTimer.singleShot(0, self.load_engine)

    def load_engine(self, factory=None) -> None:
        """Charge le moteur choisi, hors du fil de l'interface."""
        if self.loader is not None:
            return
        preset = self.preset()
        if self.encoder is not None and self.encoder.name == preset.name:
            return
        self.encoder = None
        self._loading_preset = preset
        self._download_base = ia.folder_size(ia.MODELS_DIR) if ia.MODELS_DIR.exists() else 0
        self.loader = ia.EngineLoader(preset, self, factory=factory)
        self.loader.said.connect(self._loader_said)
        self.loader.loaded.connect(self._engine_loaded)
        self.loader.failed.connect(self._engine_failed)
        self.loader.start()
        self.download_timer.start()
        self._update_enabled()
        self.engine_label.setText("Chargement…")

    def use_encoder(self, encoder) -> None:
        """Branche un moteur déjà prêt (les tests y mettent un moteur factice)."""
        self.encoder = encoder
        self._tag_vectors = {}
        self._tag_results = {}
        self._update_engine_text()
        self._update_index_text()
        self._update_enabled()

    def _loader_said(self, text: str) -> None:
        self._loader_text = text
        self.engine_label.setText(text)

    def _show_download(self) -> None:
        if self.loader is None or not ia.MODELS_DIR.exists():
            return
        grown = ia.folder_size(ia.MODELS_DIR) - self._download_base
        if grown > 2 * 1024 ** 2 and self._loading_preset is not None:
            self.engine_label.setText(
                f"Téléchargement : {_size(grown)} sur ≈ "
                f"{self._loading_preset.size_text}…")

    def _engine_loaded(self, encoder) -> None:
        self.loader = None
        self.download_timer.stop()
        if encoder.name != self.preset().name:
            # On a change de moteur pendant le chargement : on prend l'autre,
            # s'il est deja la. Sinon on attend « Charger » : pas de
            # telechargement d'un gigaoctet et demi sans qu'on l'ait demande.
            self.encoder = None
            self._update_engine_text()
            self._update_enabled()
            if ia.is_downloaded(self.preset()):
                self.load_engine()
            return
        self.use_encoder(encoder)

    def _engine_failed(self, text: str) -> None:
        self.loader = None
        self.download_timer.stop()
        self.engine_label.setText(f"<span style='color:#ff9a9a'>Échec du "
                                  f"chargement.</span> {text}")
        self._update_enabled()

    def _update_engine_text(self) -> None:
        preset = self.preset()
        if self.loader is not None:
            return
        if self.encoder is not None:
            ms = self.store.meta(f"ms_image|{self.encoder.name}", "")
            speed = (f" · {ms.replace('.', ',')} ms par image à la dernière analyse"
                     if ms else "")
            self.engine_label.setText(
                f"Prêt — {self.encoder.device} · {self.encoder.dim} dimensions · "
                f"descriptions en {'français' if self.encoder.lang == 'fr' else 'anglais'}"
                f"{speed}.<br>{preset.note}")
        else:
            where = ("déjà téléchargé" if ia.is_downloaded(preset) else
                     f"≈ {preset.size_text} à télécharger une fois")
            self.engine_label.setText(f"Non chargé ({where}).<br>{preset.note}")
        self._update_status()

    def _update_status(self) -> None:
        """L'en-tête dit tout d'un coup d'œil : quel moteur, où il calcule,
        combien de vidéos il connaît, à quelle vitesse."""
        if self.encoder is None:
            self.status_label.setText("Aucun moteur chargé")
            return
        videos, frames, _failed = self.store.counts(self.encoder.name)
        parts = [self.encoder.label, self.encoder.device,
                 f"index : {_count(videos)} vidéos, {_count(frames)} images"]
        ms = self.store.meta(f"ms_image|{self.encoder.name}", "")
        if ms:
            parts.append(f"{ms.replace('.', ',')} ms par image")
        self.status_label.setText(" · ".join(parts))

    def _update_enabled(self) -> None:
        ready = self.encoder is not None
        busy = self.indexer is not None
        self.load_button.setEnabled(self.loader is None and not ready and not busy)
        self.preset_box.setEnabled(not busy)
        has_scope = self.scope_box.count() > 0
        self.analyze_button.setEnabled(ready and (has_scope or busy))
        self.frames_spin.setEnabled(not busy)
        for widget in (self.search_button, self.count_button):
            widget.setEnabled(ready)
        self.query.setEnabled(ready)

    # ------------------------------------------------------------------
    # Portee
    # ------------------------------------------------------------------
    def refresh_scope(self) -> None:
        """Le dossier ouvert et la racine, tels que Prisme les montre à l'instant."""
        if not hasattr(self, "scope_box"):
            return
        keep = self.scope_box.currentData()
        self.scope_box.blockSignals(True)
        self.scope_box.clear()
        here = getattr(self.main, "root", None)
        top = self.main.top_root() if here is not None else None
        from pathlib import Path
        if here is not None:
            self.scope_box.addItem(f"Dossier ouvert : {Path(here).name or here}",
                                   str(here))
        if top is not None and Path(top) != Path(here):
            self.scope_box.addItem(f"Toute la racine : {Path(top).name or top}",
                                   str(top))
        at = self.scope_box.findData(keep) if keep else -1
        self.scope_box.setCurrentIndex(at if at >= 0 else 0)
        self.scope_box.blockSignals(False)
        if not self.scope_box.count():
            self.run_label.setText("Ouvrez d'abord un dossier dans Prisme.")
        self._update_enabled()
        if self.scope_box.currentData() != keep:
            self._scope_changed()

    def scope_root(self) -> str:
        return self.scope_box.currentData() or ""

    def _scope_changed(self, *_args) -> None:
        self._recount()
        self._update_index_text()

    # ------------------------------------------------------------------
    # Analyse
    # ------------------------------------------------------------------
    def toggle_analysis(self) -> None:
        if self.indexer is not None:
            self.stop_analysis()
        else:
            self.start_analysis()

    def start_analysis(self) -> None:
        if self.indexer is not None or self.encoder is None or not self.scope_root():
            return
        from . import media
        cfg = getattr(self.main, "cfg", None)
        width = int(cfg["thumb_width"]) if cfg is not None else 480
        skip = bool(cfg["skip_hidden"]) if cfg is not None else True
        if not media.Tools.ffmpeg:
            media.Tools.resolve(cfg)
        self.indexer = ia.Indexer(self.encoder, self.store, self.scope_root(),
                                  self.frames_spin.value(), width, skip, self)
        self.indexer.progress.connect(self._analysis_progress)
        self.indexer.said.connect(self.run_label.setText)
        self.indexer.finished_run.connect(self._analysis_done)
        self.indexer.finished.connect(self.indexer.deleteLater)
        self._run_started = time.monotonic()
        self.progress.setRange(0, 0)
        self.progress.show()
        self.analyze_button.setText("Arrêter")
        self._update_enabled()
        self.indexer.start()

    def stop_analysis(self) -> None:
        if self.indexer is not None:
            self.indexer.stop()
            self.run_label.setText("Arrêt demandé…")

    def _analysis_progress(self, info: dict) -> None:
        todo = info["todo"]
        self.progress.setRange(0, max(1, todo))
        self.progress.setValue(info["done"])
        self.progress.setFormat(f"{info['done']} / {todo}")
        parts = [f"{_count(info['done'])} / {_count(todo)} vidéos à analyser"]
        if info["skipped"]:
            parts.append(f"{_count(info['skipped'])} déjà à jour")
        parts.append(f"{_count(info['frames'])} images")
        if info.get("ms_image"):
            parts.append(f"{_fr(info['ms_image'], 0)} ms par image")
        if info.get("eta") is not None and info["done"] < todo:
            parts.append(f"reste ≈ {_duration_text(info['eta'])}")
        self.run_label.setText(" · ".join(parts))
        if info["done"] and info["done"] % 20 == 0:
            self._update_index_text()

    def _analysis_done(self, stats: dict) -> None:
        self.indexer = None
        self.progress.hide()
        self.analyze_button.setText("Analyser")
        self._update_enabled()
        if stats.get("error"):
            text = f"Analyse interrompue par une erreur : {stats['error']}"
        else:
            head = "Analyse arrêtée" if stats["stopped"] else "Analyse terminée"
            text = (f"{head} en {_duration_text(stats['seconds'])} : "
                    f"{_count(stats['done'])} vidéo(s) analysée(s), "
                    f"{_count(stats['skipped'])} déjà à jour")
            if stats["failed"]:
                text += f", {_count(stats['failed'])} illisible(s)"
            if stats["removed"]:
                text += f", {_count(stats['removed'])} disparue(s) retirée(s)"
            if stats["images_encoded"]:
                text += (f". {_fr(1000 * stats['encode_s'] / stats['images_encoded'], 0)}"
                         " ms par image à l'encodage")
            text += "."
        self.run_label.setText(text)
        self.last_stats = stats
        self._update_index_text()
        self._update_engine_text()
        # Les tags comptes avant l'analyse ne voyaient pas les nouvelles videos.
        self._recount()

    def _update_index_text(self) -> None:
        name = (self.encoder.name if self.encoder is not None
                else self.preset().name)
        videos, frames, failed = self.store.counts(name)
        text = (f"Index de ce moteur : {_count(videos)} vidéo(s), "
                f"{_count(frames)} images")
        if failed:
            text += f", {_count(failed)} illisible(s)"
        text += f" · {_size(self.store.size_on_disk())} sur le disque (tous moteurs)."
        self.index_label.setText(text)
        self._update_status()

    # ------------------------------------------------------------------
    # Recherche
    # ------------------------------------------------------------------
    def _matrix(self):
        if self.encoder is None:
            return None
        return self.store.matrix(self.encoder.name, self.encoder.dim)

    def _mask(self, matrix):
        root = self.scope_root()
        return matrix.mask_under(root) if root else matrix.mask_under("")

    def _encode(self, queries: list, then) -> None:
        """Encode des descriptions en tâche de fond, puis appelle `then`."""
        job = ia.TextJob(self.encoder, queries, self)
        self._jobs.add(job)

        def done(vectors, neutral, seconds):
            self._jobs.discard(job)
            job.deleteLater()
            self._neutral = neutral
            then(vectors, neutral, seconds)

        def failed(text):
            self._jobs.discard(job)
            job.deleteLater()
            self.results_title.setText(f"Échec de l'encodage : {text}")

        job.done.connect(done)
        job.failed.connect(failed)
        job.start()

    def search(self) -> None:
        text = self.query.text().strip()
        if not text or self.encoder is None:
            return
        self._search_serial += 1
        serial = self._search_serial
        self.results_title.setText(f"Recherche de « {text} »…")
        started = time.perf_counter()

        def then(vectors, neutral, seconds):
            if serial != self._search_serial:
                return          # une recherche plus recente est partie
            self._show_search(text, vectors[0], neutral, seconds, started)

        self._encode([text], then)

    def _show_search(self, text, described, neutral, text_s, started) -> None:
        matrix = self._matrix()
        if matrix is None or not len(matrix):
            self._show_hits([], f"« {text} » : rien d'analysé pour ce moteur",
                            "Lancez d'abord « Analyser ».", matrix)
            return
        rank_started = time.perf_counter()
        q = ia.query_vector(described, neutral, self.calibrate.isChecked())
        mask = self._mask(matrix)
        hits, _scores = matrix.rank(q, self.limit_spin.value(), mask)
        rank_s = time.perf_counter() - rank_started
        seen = int(mask.sum()) if mask is not None else len(matrix)
        self._show_hits(
            hits, f"« {text} » : les {len(hits)} vidéos les plus proches",
            f"Sur {_count(seen)} vidéos analysées dans la portée "
            f"({_count(matrix.frames)} images en tout) · description encodée en "
            f"{_fr(text_s, 2)} s · classement en {_fr(rank_s * 1000, 1)} ms · "
            f"score {'calibré' if self.calibrate.isChecked() else 'brut'}.",
            matrix)

    def _calibration_changed(self, on: bool) -> None:
        self.store.set_meta("calibrate", "1" if on else "0")
        if self.query.text().strip() and self.encoder is not None:
            self.search()
        self._recount()

    def _show_hits(self, hits: list, title: str, note: str, matrix) -> None:
        self._shown_matrix = matrix
        self.last_title = title
        self.results_title.setText(title)
        self.results_note.setText(
            note + "  Double-clic ou Entrée : ouvrir à l'instant trouvé ; "
            "clic droit : plus comme celle-ci.")
        self.grid.clear()
        self._thumb_queue = []
        blank = thumb_icon("")
        from pathlib import Path
        for rank, hit in enumerate(hits, 1):
            path = Path(hit.path)
            item = QListWidgetItem(
                blank, f"{rank}. {hit.score:+.3f} · {_clock(hit.ts)}\n{path.name}")
            item.setData(Qt.UserRole, hit)
            item.setToolTip(f"{hit.path}\nà {_clock(hit.ts)} · score {hit.score:+.4f}")
            item.setSizeHint(self.grid.gridSize() - QSize(6, 6))
            self.grid.addItem(item)
            self._thumb_queue.append(item)
        if hits:
            self.grid.setCurrentRow(0)
        self.thumb_timer.start()

    def _load_some_thumbs(self) -> None:
        """Quelques vignettes à chaque tour : la grille paraît tout de suite, les
        images la remplissent sans figer la fenetre."""
        from . import media
        from pathlib import Path
        for _ in range(6):
            if not self._thumb_queue:
                self.thumb_timer.stop()
                return
            item = self._thumb_queue.pop(0)
            try:
                hit = item.data(Qt.UserRole)
            except RuntimeError:
                continue
            thumb = hit.thumb
            if not thumb or not Path(thumb).exists():
                cfg = getattr(self.main, "cfg", None)
                width = int(cfg["thumb_width"]) if cfg is not None else 480
                found = media.cached_thumb(Path(hit.path), hit.ts, width)
                thumb = str(found) if found else ""
            item.setIcon(thumb_icon(thumb))

    def selected_hit(self):
        item = self.grid.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def open_item(self, item) -> None:
        if item is not None:
            self.open_hit(item.data(Qt.UserRole))

    def open_hit(self, hit) -> None:
        """Ouvre la vidéo dans Prisme, à l'instant de l'image trouvée."""
        if hit is None:
            return
        if self.hide_on_open.isChecked():
            self.hide()
        self.main.play_in_app(hit.path, hit.ts)
        self.main.raise_()
        self.main.activateWindow()

    def _grid_menu(self, pos) -> None:
        item = self.grid.itemAt(pos)
        if item is None:
            return
        self.grid.setCurrentItem(item)
        hit = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction("Plus comme cette image", lambda: self.more_like(hit, False))
        menu.addAction("Plus comme cette vidéo (toutes ses images)",
                       lambda: self.more_like(hit, True))
        menu.addSeparator()
        menu.addAction(f"Ouvrir à {_clock(hit.ts)}", lambda: self.open_hit(hit))
        reveal = getattr(self.main, "reveal_path", None)
        if reveal is not None:
            menu.addAction("Montrer dans l'explorateur", lambda: reveal(hit.path))
        menu.exec(self.grid.viewport().mapToGlobal(pos))

    def more_like(self, hit, whole_video: bool = False) -> None:
        """Les vidéos dont une image ressemble à celle-ci (ou à toute la vidéo).

        Image contre image : pas de reference neutre ici, elle ne vaut que
        pour une phrase.
        """
        matrix = self._matrix()
        if matrix is None or hit is None:
            return
        video, frame = hit.video, hit.frame
        if matrix is not self._shown_matrix:
            # L'index a bouge depuis l'affichage : on retrouve la video.
            try:
                video = matrix.paths.index(hit.path)
            except ValueError:
                return
            start = int(matrix.starts[video])
            count = int(matrix.counts[video])
            gaps = [abs(float(t) - hit.ts) for t in matrix.ts[start:start + count]]
            frame = start + gaps.index(min(gaps))
        q = matrix.video_vector(video) if whole_video else matrix.frame_vector(frame)
        started = time.perf_counter()
        hits, _ = matrix.rank(q, self.limit_spin.value(), self._mask(matrix),
                              exclude=[video])
        from pathlib import Path
        what = "cette vidéo" if whole_video else f"cette image ({_clock(hit.ts)})"
        self._show_hits(hits, f"Plus comme {what} de « {Path(hit.path).name} »",
                        f"Image contre image, score brut · classement en "
                        f"{_fr((time.perf_counter() - started) * 1000, 1)} ms.",
                        matrix)

    # ------------------------------------------------------------------
    # Tags IA
    # ------------------------------------------------------------------
    def tag_lines(self) -> list:
        lines = [line.strip() for line in self.tags_edit.toPlainText().splitlines()]
        return list(dict.fromkeys(line for line in lines if line))

    def count_tags(self) -> None:
        tags = self.tag_lines()
        self.store.set_tags(tags)
        if not tags or self.encoder is None:
            self._tag_vectors = {}
            self._refresh_tag_list()
            return
        self.count_button.setEnabled(False)
        self.count_button.setText("…")

        def then(vectors, _neutral, _seconds):
            self.count_button.setText("Compter")
            self._update_enabled()
            self._tag_vectors = dict(zip(tags, vectors))
            self._recount()

        self._encode(tags, then)

    def z_min(self) -> float:
        return self.z_slider.value() / 10.0

    def _thresholds_changed(self, *_args, save: bool = True) -> None:
        self.z_label.setText(f"z ≥ {_fr(self.z_min())}")
        if save:
            self.store.set_meta("tag_z", f"{self.z_min():.1f}")
            self.store.set_meta("tag_share", str(self.share_spin.value()))
            self._recount()

    def _recount(self) -> None:
        """Recompte chaque tag : aucun encodage, un produit par tag."""
        self._tag_results = {}
        matrix = self._matrix() if self._tag_vectors else None
        if matrix is not None and len(matrix):
            import numpy as np
            mask = self._mask(matrix)
            inside = np.flatnonzero(mask)
            for tag, described in self._tag_vectors.items():
                q = ia.query_vector(described, self._neutral,
                                    self.calibrate.isChecked())
                fs = matrix.frame_scores(q)
                vs = matrix.video_scores(fs)
                kept = ia.tag_matches(vs[inside], self.z_min(),
                                      self.share_spin.value() / 100.0)
                self._tag_results[tag] = (matrix, vs, fs,
                                          [int(inside[k]) for k in kept],
                                          len(inside))
        self._refresh_tag_list()

    def _refresh_tag_list(self) -> None:
        current = self.tags_list.currentItem()
        keep = current.data(Qt.UserRole) if current is not None else None
        self.tags_list.clear()
        for tag in self.tag_lines():
            found = self._tag_results.get(tag)
            if found is None:
                text = f"{tag} — ?"
            else:
                text = f"{tag} — {_count(len(found[3]))} vidéo(s)"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, tag)
            self.tags_list.addItem(item)
            if tag == keep:
                self.tags_list.setCurrentItem(item)

    def tag_count(self, tag: str) -> int | None:
        found = self._tag_results.get(tag)
        return None if found is None else len(found[3])

    def show_tag(self, item) -> None:
        tag = item.data(Qt.UserRole) if item is not None else None
        found = self._tag_results.get(tag)
        if found is None:
            self.results_title.setText(f"« {tag} » : cliquez d'abord « Compter »")
            return
        matrix, vs, fs, kept, seen = found
        rule = (f"z ≥ {_fr(self.z_min())}, au plus {self.share_spin.value()} % "
                f"de {_count(seen)} vidéos")
        small = " (peu de vidéos : seuil peu fiable)" if seen < 30 else ""
        if kept:
            hits = matrix.hits_from(vs, fs, len(kept), only=kept)
            self._show_hits(hits, f"Tag « {tag} » : {len(kept)} vidéo(s)",
                            f"Retenues : {rule}{small}.", matrix)
        else:
            hits = matrix.hits_from(vs, fs, 24, self._mask(matrix))
            self._show_hits(hits, f"Tag « {tag} » : aucune vidéo ne se détache",
                            f"Voici les plus proches, sous le seuil ({rule}){small}.",
                            matrix)
