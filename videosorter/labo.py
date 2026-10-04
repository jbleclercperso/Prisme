"""La fenetre du Labo IA : chercher une scene par sa description, et des tags
IA de plusieurs mots (voir ia.py).

Deux temps, dits comme tels a l'ecran : preparer (les bibliotheques, puis
l'index de la collection), puis chercher ou taguer. Tout tourne en fond :
l'installation (pip, sortie en direct), la lecture de l'index, l'indexation,
les recherches. La fenetre releve ce qui arrive par une boite aux lettres, et
reste vive.
"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QProcess, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from . import ia

# Tout est ecrit en clair sur fond sombre : les onglets et les boutons natifs
# de Windows sortaient en gris pale sur gris, illisibles.
LAB_STYLE = """
QWidget#labo { background: #0e1116; }
QWidget#labo QWidget { color: #e6e8ea; }
QWidget#labo QLabel { background: transparent; }
QWidget#labo QLabel#labTitle { color: #ffffff; font-size: 20px; font-weight: 700; }
QWidget#labo QLabel#labLead { color: #b8c1cc; font-size: 13px; }
QWidget#labo QLabel#labStep { color: #ffffff; font-size: 14px; font-weight: 600; }
QWidget#labo QLabel#labDim { color: #aab4c0; font-size: 12px; }
QWidget#labo QLabel#labHint { color: #aab4c0; font-size: 12px; }
QWidget#labo QLabel#labCount { color: #ffffff; font-size: 13px; font-weight: 600; }
QFrame#labActivity { background: #151a21; border: 1px solid #242b35; border-radius: 8px; }
QFrame#labActivity[mood="work"] { border-color: #2f6fed; background: #121b2c; }
QFrame#labActivity[mood="stop"] { border-color: #e0a030; background: #221b10; }
QFrame#labActivity[mood="refuse"] { border-color: #e0a030; background: #221b10; }
QFrame#labActivity[mood="idle"] { border-color: #2e6b45; }
QWidget#labo QLabel#labSpin { color: #7fb0ff; font-size: 16px; font-weight: 700; min-width: 22px; }
QWidget#labo QLabel#labNow { color: #ffffff; font-size: 13px; font-weight: 600; }
QWidget#labo QLabel#labClock { color: #8b94a1; font-size: 12px; }
QWidget#labo QLabel#labState { color: #d7dde4; font-size: 12px; padding: 6px 10px;
                  background: #151a21; border: 1px solid #242b35; border-radius: 6px; }
QFrame#labCard { background: #151a21; border: 1px solid #242b35; border-radius: 10px; }
QPushButton { background: #232a34; border: 1px solid #364050; border-radius: 6px;
              padding: 7px 14px; color: #eef1f4; }
QPushButton:hover { background: #2c3541; border-color: #4a5668; }
QPushButton:pressed { background: #384352; }
QPushButton:disabled { color: #6f7a87; background: #1a1f27; border-color: #262d37; }
QPushButton#labPrimary { background: #2f6fed; border-color: #2f6fed; color: #ffffff;
                         font-weight: 600; }
QPushButton#labPrimary:hover { background: #4280f5; }
QPushButton#labPrimary:disabled { background: #22324f; border-color: #22324f; color: #8da0bf; }
QLineEdit, QPlainTextEdit { background: #0b0e12; border: 1px solid #364050; border-radius: 6px;
                            padding: 7px 10px; color: #ffffff;
                            selection-background-color: #2f6fed; }
QLineEdit:focus, QPlainTextEdit:focus { border-color: #4c8dff; }
QLineEdit#labQuery { font-size: 15px; padding: 9px 12px; }
QPlainTextEdit#labLog { background: #07090c; color: #9fb0c2; font-family: Consolas, monospace;
                        font-size: 11px; }
QComboBox { background: #232a34; border: 1px solid #364050; border-radius: 6px;
            padding: 6px 10px; color: #eef1f4; }
QComboBox QAbstractItemView { background: #1b2028; color: #eef1f4; border: 1px solid #364050;
                              selection-background-color: #2f6fed; }
QProgressBar { background: #0b0e12; border: 1px solid #2a323d; border-radius: 6px;
               color: #ffffff; text-align: center; min-height: 18px; }
QProgressBar::chunk { background: #2f6fed; border-radius: 5px; }
QTabWidget::pane { border: 1px solid #242b35; border-radius: 10px; background: #151a21;
                   top: -1px; }
QTabBar::tab { background: #1a2029; color: #c9d1db; border: 1px solid #242b35;
               border-bottom: none; padding: 9px 18px; margin-right: 4px;
               border-top-left-radius: 8px; border-top-right-radius: 8px;
               font-size: 13px; font-weight: 600; }
QTabBar::tab:selected { background: #151a21; color: #ffffff; border-color: #2f6fed; }
QTabBar::tab:hover:!selected { color: #ffffff; background: #212935; }
QListWidget { background: #0b0e12; border: 1px solid #242b35; border-radius: 8px;
              color: #e6e8ea; padding: 6px; }
QListWidget::item { border-radius: 6px; padding: 4px; }
QListWidget::item:selected { background: #1d2a40; color: #ffffff; }
QListWidget::item:hover { background: #18202b; }
QListWidget::indicator { width: 18px; height: 18px; }
QWidget#labo QLabel#labScore { color: #ffffff; font-size: 13px; font-weight: 700;
                               padding: 4px 0; }
QListWidget#labFiles::item { padding: 6px 8px; border-bottom: 1px solid #161c24; }
QListWidget#labGroupList { padding: 4px; }
QSpinBox { background: #0b0e12; border: 1px solid #364050; border-radius: 6px;
           padding: 5px 8px; color: #ffffff; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #2b323d; border-radius: 4px; min-height: 36px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""

# Images par video, en moyenne (mesure sur une collection reelle) : de quoi
# annoncer la duree d'une indexation.
FRAMES_PER_VIDEO = 6.7
# L'essai compare les moteurs sur ce nombre de videos, et ce nombre de
# resultats par recherche.
BENCH_SIZE = 500
BENCH_TOP = 24
# Videos dont on extrait les images a la fois, pendant l'indexation.
WORKERS = 3

# Exigence des tags : ce qu'on voit, et ce que ia.STRICTNESS en fait.
STRICT_CHOICES = (("souple", "Souple — plus de vidéos"), ("normal", "Normale"),
                  ("strict", "Stricte — moins de vidéos, plus sûres"))


class _GroupDelegate(QStyledItemDelegate):
    """Une collection dans la liste : son nom, et dessous ses chiffres."""

    def sizeHint(self, option, index):
        return QSize(max(120, option.rect.width()), 50)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect.adjusted(2, 2, -2, -2)
        if option.state & QStyle.State_Selected:
            painter.setBrush(QColor("#1d2a40"))
            painter.setPen(QColor("#2f6fed"))
        elif option.state & QStyle.State_MouseOver:
            painter.setBrush(QColor("#18202b"))
            painter.setPen(Qt.NoPen)
        else:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 6, 6)
        font = QFont(option.font)
        font.setPixelSize(13)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        text = painter.fontMetrics().elidedText(index.data(Qt.DisplayRole) or "",
                                                Qt.ElideRight, rect.width() - 20)
        painter.drawText(rect.adjusted(10, 7, -10, 0), Qt.AlignLeft | Qt.AlignTop, text)
        font.setBold(False)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor("#9aa6b4"))
        painter.drawText(rect.adjusted(10, 0, -10, -7), Qt.AlignLeft | Qt.AlignBottom,
                         index.data(Qt.UserRole) or "")
        painter.restore()


def _card(parent) -> tuple:
    frame = QFrame(parent)
    frame.setObjectName("labCard")
    box = QVBoxLayout(frame)
    box.setContentsMargins(16, 14, 16, 14)
    box.setSpacing(8)
    return frame, box


def _label(text: str, name: str, parent) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName(name)
    label.setWordWrap(True)
    return label


class _CheckCorner(QStyledItemDelegate):
    """La case d'un resultat, posee sur la vignette en haut a droite : sur sa
    colonne a gauche, elle volait de la largeur a l'image. Un clic dessus
    coche ou decoche ; le reste de la vignette garde ses gestes (double-clic :
    lire)."""

    SIZE = 20

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # La case native n'est plus dessinee : l'image prend sa place.
        option.features &= ~QStyleOptionViewItem.HasCheckIndicator

    def _box(self, option, index) -> QRect:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        icon = style.subElementRect(QStyle.SE_ItemViewItemDecoration, opt, widget)
        if not icon.isValid() or icon.width() <= 0:
            icon = opt.rect
        size = self.SIZE
        return QRect(icon.right() - size - 4, icon.top() + 4, size, size)

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        state = index.data(Qt.CheckStateRole)
        if state is None:
            return
        checked = Qt.CheckState(state) == Qt.Checked
        box = self._box(option, index)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#f5c542" if checked else "#e9eef4"), 1.6))
        painter.setBrush(QColor("#f5c542") if checked else QColor(8, 10, 13, 170))
        painter.drawRoundedRect(box, 4, 4)
        if checked:
            pen = QPen(QColor("#16181d"), 2.4)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            x, y, w, h = box.x(), box.y(), box.width(), box.height()
            painter.drawPolyline([QPoint(x + int(w * 0.24), y + int(h * 0.52)),
                                  QPoint(x + int(w * 0.43), y + int(h * 0.72)),
                                  QPoint(x + int(w * 0.78), y + int(h * 0.30))])
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (event.type() in (QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick)
                and event.button() == Qt.LeftButton
                and index.flags() & Qt.ItemIsUserCheckable
                and self._box(option, index).adjusted(-4, -4, 4, 4).contains(
                    event.position().toPoint())):
            if event.type() == QEvent.MouseButtonRelease:
                state = Qt.CheckState(index.data(Qt.CheckStateRole))
                model.setData(index, Qt.Unchecked if state == Qt.Checked else Qt.Checked,
                              Qt.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)


class LaboWindow(QWidget):
    """Le labo : preparer (bibliotheques, index), puis chercher ou taguer."""

    def __init__(self, window, engine=None, frame_of=None):
        super().__init__(window, Qt.Window)
        self.window = window
        self.setObjectName("labo")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(LAB_STYLE)
        font = self.font()
        font.setPixelSize(13)
        self.setFont(font)
        self.setWindowTitle("Prisme — Labo IA")
        self.resize(1180, 820)
        cfg = window.cfg
        self._engine_given = engine
        self._frame_of = frame_of or self._default_frame
        self.engine = None
        self.index = None
        self.mail: queue.SimpleQueue = queue.SimpleQueue()
        self._stop = False
        self._busy = False
        self._process = None
        self._commands: list = []
        self._loading = 0              # numero de la lecture d'index en cours
        self._model_ready = False
        # « Plus comme ça » / « moins comme ça » : des empreintes d'images,
        # tant que la demande ne change pas.
        # Des images : (video, instant). Elles valent pour tous les moteurs, et
        # se gardent avec la phrase (`labo_examples`) : la meme recherche, des
        # semaines plus tard, repart de ce qu'on lui avait appris.
        self._liked: list = []
        self._disliked: list = []
        self._asked = ""
        self._after_idle: list = []
        self._bench_running = False
        self._top = 60                 # combien de videos on montre
        self._shown: set = set()       # celles de la recherche precedente
        self._tag_found: dict = {}

        box = QVBoxLayout(self)
        box.setContentsMargins(20, 18, 20, 16)
        box.setSpacing(12)

        # -- en-tete -------------------------------------------------------
        head = QHBoxLayout()
        head.addWidget(_label("Labo IA", "labTitle", self))
        self.lead = _label(
            "Retrouvez une scène en la décrivant, en français ou en anglais. Tout se passe "
            "sur ce PC : rien ne part sur Internet.", "labLead", self)
        self.lead.setWordWrap(False)
        head.addSpacing(14)
        head.addWidget(self.lead, 1)
        box.addLayout(head)

        # -- 1. preparer -----------------------------------------------------
        card, inner = _card(self)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.engine_pick = QComboBox(card)
        for key, spec in ia.ENGINES.items():
            self.engine_pick.addItem(spec["label"], key)
        chosen = cfg.get("labo_engine", ia.DEFAULT_ENGINE) if hasattr(cfg, "get") else ia.DEFAULT_ENGINE
        self.engine_pick.setCurrentIndex(max(0, self.engine_pick.findData(chosen)))
        self.engine_pick.currentIndexChanged.connect(self._engine_changed)
        # Un seul moteur : pas de choix a faire, donc pas de liste.
        self.engine_pick.setVisible(self.engine_pick.count() > 1)
        self.engine_pick.setMinimumWidth(330)
        row.addWidget(self.engine_pick)
        # Le compte s'ecrit dans la barre elle-meme : « 5 288 / 62 031 vidéos
        # indexées », qui avance pendant l'indexation. Une ligne de compte et
        # une barre a part disaient deux choses differentes.
        self.index_state = _label("", "labCount", card)
        self.index_state.hide()
        self.bar = QProgressBar(card)
        self.bar.setTextVisible(True)
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.bar.setFormat("")
        row.addWidget(self.bar, 1)
        self.install_button = QPushButton("Installer les bibliothèques", card)
        self.install_button.setObjectName("labPrimary")
        self.install_button.clicked.connect(self._install)
        row.addWidget(self.install_button)
        self.index_button = QPushButton("Indexer la collection", card)
        self.index_button.setObjectName("labPrimary")
        self.index_button.clicked.connect(self._index)
        row.addWidget(self.index_button)
        self.stop_button = QPushButton("Arrêter", card)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._halt)
        row.addWidget(self.stop_button)
        details = QPushButton("Détails", card)
        details.setCheckable(True)
        details.setToolTip("Ce que coûte ce moteur, et comment se passe l'indexation")
        row.addWidget(details)
        inner.addLayout(row)
        self.prep_details = QWidget(card)
        more = QVBoxLayout(self.prep_details)
        more.setContentsMargins(0, 0, 0, 0)
        self.engine_about = _label("", "labDim", card)
        more.addWidget(self.engine_about)
        self.prep_details.hide()
        details.toggled.connect(self.prep_details.setVisible)
        inner.addWidget(self.prep_details)
        row = QHBoxLayout()
        self.gpu_state = _label("", "labDim", card)
        self.gpu_state.hide()
        row.addWidget(self.gpu_state, 1)
        self.gpu_button = QPushButton("Utiliser la carte NVIDIA", card)
        self.gpu_button.setToolTip("Remplace PyTorch « processeur » par la même version pour "
                                   "carte NVIDIA (environ 2,5 Go). Prisme doit ensuite être "
                                   "relancé.")
        self.gpu_button.clicked.connect(self._use_gpu)
        self.gpu_button.hide()
        row.addWidget(self.gpu_button)
        inner.addLayout(row)
        self.prep_note = _label(
            "Une seule fois par vidéo : quelques images selon sa durée (3 pour un clip court, "
            "environ une par minute, 16 au plus). Chaque moteur a son propre index. Prisme "
            "reste prioritaire et utilisable ; on peut arrêter, fermer Prisme et reprendre "
            "plus tard sans rien perdre. Les recherches portent sur ce qui est déjà indexé.",
            "labDim", card)
        more.addWidget(self.prep_note)
        self.log = QPlainTextEdit(card)
        self.log.setObjectName("labLog")
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(130)
        self.log.hide()
        inner.addWidget(self.log)
        box.addWidget(card)

        # -- chercher, taguer, comparer --------------------------------------
        tabs = QTabWidget(self)
        self.tabs = tabs
        tabs.currentChanged.connect(self._tab_changed)

        search = QWidget(tabs)
        search_box = QVBoxLayout(search)
        search_box.setContentsMargins(14, 14, 14, 12)
        search_box.setSpacing(8)
        row = QHBoxLayout()
        self.query = QLineEdit(search)
        self.query.setObjectName("labQuery")
        self.query.setPlaceholderText("Décrivez la scène : « une femme qui court sur la plage »")
        self.query.returnPressed.connect(self._search)
        row.addWidget(self.query, 1)
        # Le micro, dans le champ : on decrit la scene a voix haute.
        from . import voice

        def spoken(text: str) -> None:
            self.query.setText(text)
            self._search()
        self.voice = voice.attach(self.query, spoken, self._refuse)
        go = QPushButton("Chercher", search)
        go.setObjectName("labPrimary")
        go.clicked.connect(self._search)
        row.addWidget(go)
        search_box.addLayout(row)
        row = QHBoxLayout()
        hint = _label("Chaque idée de la phrase doit se voir dans l'image. « -plage » écarte un "
                      "mot. Cochez des résultats, puis « Plus comme ça » ou « Moins comme ça ».",
                      "labHint", search)
        hint.setToolTip(
            "« amatrice qui pisse en extérieur » exige les trois idées : « amatrice », "
            "« pisse », « en extérieur ». Des virgules séparent les idées à la main ; "
            "« -plage », « sans plage » ou « pas de plage » écartent un mot.\n\n"
            "Le Labo compare votre phrase à TOUTES les images déjà indexées et garde les "
            "vidéos les plus proches, la meilleure d'abord. Plus la collection est indexée, "
            "plus il a de chances de trouver.")
        row.addWidget(hint, 1)
        row.addWidget(_label("Taille", "labDim", search))
        from PySide6.QtWidgets import QSlider
        self.thumb_size = QSlider(Qt.Horizontal, search)
        self.thumb_size.setRange(110, 320)
        self.thumb_size.setValue(170)
        self.thumb_size.setFixedWidth(130)
        self.thumb_size.setToolTip("La taille des vignettes : plus petites, on en voit davantage")
        row.addWidget(self.thumb_size)
        search_box.addLayout(row)
        self.feedback_state = _label("", "labDim", search)
        self.feedback_state.hide()
        search_box.addWidget(self.feedback_state)
        self.results = self._result_list(search)
        self.thumb_size.valueChanged.connect(self._resize_thumbs)
        self._resize_thumbs(self.thumb_size.value())
        search_box.addWidget(self.results, 1)
        row = QHBoxLayout()
        self.result_count = _label("", "labDim", search)
        row.addWidget(self.result_count, 1)
        # Plus de bouton « Afficher 60 de plus » : la suite arrive toute seule
        # quand on descend, de la plus proche a la moins proche.
        self._can_more = False
        self._loading_more = False
        self.results.verticalScrollBar().valueChanged.connect(self._near_end)
        search_box.addLayout(row)
        search_box.addLayout(self._keep_row(search, self.results,
                                            lambda: self.query.text().strip(), learn=True))
        tabs.addTab(search, "Chercher une scène")

        tabs.addTab(self._groups_tab(tabs), "Collections par nom")
        self._bench_page = self._bench_tab(tabs)
        tabs.addTab(self._bench_page, "Comparer les moteurs")
        box.addWidget(tabs, 1)

        # Ce que fait l'ordinateur, toujours sous les yeux : ce qui tourne (avec
        # depuis combien de temps), un arret en cours, ou « Prêt ». On ne
        # savait pas si un clic avait ete compris, ni si un arret avait pris.
        self.activity = QFrame(self)
        self.activity.setObjectName("labActivity")
        strip = QHBoxLayout(self.activity)
        strip.setContentsMargins(10, 6, 12, 6)
        strip.setSpacing(10)
        self.spin = QLabel("", self.activity)
        self.spin.setObjectName("labSpin")
        strip.addWidget(self.spin)
        self.now = QLabel("", self.activity)
        self.now.setObjectName("labNow")
        strip.addWidget(self.now)
        self.state = _label("", "labDim", self.activity)
        strip.addWidget(self.state, 1)
        self.clock = QLabel("", self.activity)
        self.clock.setObjectName("labClock")
        strip.addWidget(self.clock)
        box.addWidget(self.activity)
        self._tasks: dict = {}           # ce qui tourne : {clef: (texte, debut)}
        self._stopping = False
        self._refused_until = 0.0
        self._spin_at = 0
        self.spinner = QTimer(self)
        self.spinner.setInterval(180)
        self.spinner.timeout.connect(self._tick_activity)
        self.spinner.start()

        self.timer = QTimer(self)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self._read_mail)
        self.timer.start()
        self._engine_changed()
        if self._engine_given is None:
            threading.Thread(target=lambda: self.mail.put(("gpu", ia.gpu_status())),
                             daemon=True, name="prisme-labo-carte").start()

    # -- utilitaires ---------------------------------------------------------------
    def _result_list(self, parent) -> QListWidget:
        view = QListWidget(parent)
        view.setViewMode(QListView.IconMode)
        view.setResizeMode(QListView.Adjust)
        view.setIconSize(QSize(224, 126))
        view.setGridSize(QSize(240, 178))
        view.setSpacing(4)
        view.setWordWrap(True)
        view.setMovement(QListView.Static)
        view.setUniformItemSizes(True)
        view.itemDoubleClicked.connect(self._play)
        view.setItemDelegate(_CheckCorner(view))
        view.setToolTip("Double-clic : lire la vidéo au moment de l'image trouvée. "
                        "La case, en haut à droite : la choisir.")
        return view

    def _keep_row(self, parent, view: QListWidget, suggest, learn: bool = False) -> QHBoxLayout:
        """Sous des resultats : cocher, affiner (« plus / moins comme ça »), et
        verser ce qui est coche dans un mot-cle de Prisme."""
        row = QHBoxLayout()
        row.setSpacing(8)
        every = QPushButton("Tout cocher", parent)
        every.clicked.connect(lambda: self._check_all(view))
        row.addWidget(every)
        none = QPushButton("Tout décocher", parent)
        none.clicked.connect(lambda: self._check_all(view, False))
        row.addWidget(none)
        if learn:
            row.addSpacing(12)
            more = QPushButton("👍  Plus comme ça", parent)
            more.setToolTip("Les vidéos cochées sont de bons exemples : la recherche se "
                            "refait en cherchant ce qui leur ressemble.")
            more.clicked.connect(lambda: self._learn(True))
            row.addWidget(more)
            less = QPushButton("👎  Moins comme ça", parent)
            less.setToolTip("Les vidéos cochées sont à côté : la recherche se refait en "
                            "écartant ce qui leur ressemble.")
            less.clicked.connect(lambda: self._learn(False))
            row.addWidget(less)
            self.forget_button = QPushButton("Repartir des seuls mots", parent)
            self.forget_button.setToolTip(
                "Efface les 👍 et 👎 donnés pour cette phrase : la recherche ne se "
                "fie plus qu'aux mots tapés, comme au premier essai.")
            self.forget_button.clicked.connect(self._forget_examples)
            self.forget_button.hide()
            row.addWidget(self.forget_button)
        row.addStretch(1)
        add = QPushButton("Ajouter au mot-clé…", parent)
        add.setObjectName("labPrimary")
        add.setToolTip("Les vidéos cochées rejoignent un de vos mots-clés (onglet "
                       "Mots-clés, « Mes mots »). Rien ne bouge sur le disque.")
        add.clicked.connect(lambda: self._add_checked(view, suggest()))
        row.addWidget(add)
        return row

    def _check_all(self, view: QListWidget, on: bool = True) -> None:
        for at in range(view.count()):
            view.item(at).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _checked_items(self, view: QListWidget) -> list:
        return [view.item(at) for at in range(view.count())
                if view.item(at).checkState() == Qt.Checked]

    def _checked(self, view: QListWidget) -> list:
        """Les videos cochees, chacune une fois."""
        found = [item.data(Qt.UserRole) for item in self._checked_items(view)]
        return list(dict.fromkeys(v for v in found if v))

    def _add_checked(self, view: QListWidget, suggestion: str = "") -> None:
        videos = self._checked(view)
        if not videos:
            return self._say("Cochez d'abord les vidéos à ranger.")
        add = getattr(self.window, "add_to_keyword", None)
        if add is None:
            return self._say("Prisme n'est pas prêt à recevoir des mots-clés.")
        mine = list(getattr(self.window, "tags", []) or [])
        suggestion = ia.parse_query(suggestion)[0] if suggestion else ""
        choices = list(dict.fromkeys(([suggestion] if suggestion else []) + mine))
        keyword, ok = QInputDialog.getItem(
            self, "Ajouter au mot-clé",
            f"{len(videos)} vidéo(s) cochée(s). Le mot-clé (un des vôtres, ou un nouveau) :",
            choices or [""], 0, True)
        keyword = " ".join(keyword.split())
        if not ok or not keyword:
            return
        added = add(keyword, videos)
        self._check_all(view, False)
        self._say(f"« {keyword} » : {added} vidéo(s) ajoutée(s)"
                  + (f", {len(videos) - added} y étaient déjà." if added < len(videos) else ".")
                  + " Onglet Mots-clés, « Mes mots » (marqué ✦).")

    def _videos(self) -> list:
        """Toute la collection de la racine, d'ou que l'on regarde dans Prisme.

        La liste affichee ne suffisait pas : sous l'onglet Mots-clés, elle ne
        porte que des mots-cles, et le labo se croyait devant une collection
        vide ; dans un sous-dossier, il n'en voyait qu'une partie."""
        seen, out = set(), []
        whole = getattr(self.window, "_collection", None)
        try:
            items = whole() if callable(whole) else None
        except Exception:                                    # noqa: BLE001
            items = None
        if not items:
            items = getattr(self.window, "all_items", []) or []
        for item in items:
            if getattr(item, "is_tag", False) or getattr(item, "locked", False):
                continue
            for video in getattr(item, "videos", []) or []:
                key = str(video)
                if key not in seen:
                    seen.add(key)
                    out.append(key)
        return out

    def _width(self) -> int:
        try:
            return int(self.window.cfg["thumb_width"] or 480)
        except (KeyError, TypeError, ValueError):
            return 480

    def _default_frame(self, video: str, ts: float):
        """L'image a cet instant : deja faite (un apercu de la fiche), sinon
        extraite -- apres ce que Prisme est en train de montrer."""
        from . import media
        width = self._width()
        ready = media.cached_thumb(Path(video), ts, width)
        if ready is not None:
            return ready
        media.FOREGROUND.wait(stop=lambda: self._stop)
        if self._stop:
            return None
        return media.extract_thumb(Path(video), ts, width, keyframe=True)

    def _moments(self, video: str) -> list:
        """Les instants a prendre : d'abord ceux des apercus de la fiche (deja
        en cache s'ils ont ete vus ou prepares, et cales sur les plans quand
        Prisme les connait), completes selon la duree."""
        shared = getattr(self, "_shared_moments", {}).get(video)
        if shared:
            # Deja prises par un autre moteur : ses images sont en cache.
            return list(shared)
        from . import media
        duration = self._duration(video)
        wanted = ia.frame_count(duration)
        try:
            count = int(self.window.cfg["thumb_count"] or 10)
        except (KeyError, TypeError, ValueError):
            count = 10
        try:
            plan = media.build_preview_plan([video], count, one_per_video=False, blind=True)
            known = [ts for _v, ts, _d, _h in plan]
        except Exception:                                    # noqa: BLE001
            known = []
        return ia.spread(known, wanted, duration)

    def _duration(self, video: str) -> float:
        """La duree : celle que Prisme connait, sinon mesuree (sans elle, une
        seule image par video)."""
        from .index import INDEX
        known = float((INDEX.probe(video) or {}).get("duration") or 0.0)
        if known:
            return known
        try:
            from . import media
            return float((media.probe(Path(video)) or {}).get("duration") or 0.0)
        except Exception:                                    # noqa: BLE001
            return 0.0

    def _say(self, text: str) -> None:
        self.state.setText(text)

    # -- l'activite --------------------------------------------------------------
    SPIN = "◐◓◑◒"

    def _task(self, key: str, text) -> None:
        """Depuis n'importe quel fil : une tache commence ou change (texte),
        ou se termine (None). La barre du bas le montre."""
        self.mail.put(("task", (key, text)))

    def _set_task(self, key: str, text) -> None:
        import time as _time
        if text is None:
            self._tasks.pop(key, None)
        else:
            started = self._tasks.get(key, (None, _time.monotonic()))[1]
            self._tasks[key] = (text, started)
        self._tick_activity()

    def _refuse(self, text: str) -> None:
        """Un clic qui ne peut pas partir : on le dit, en orange, bien en vue."""
        import time as _time
        self._refused_until = _time.monotonic() + 5
        self._refused_text = text
        self._tick_activity()

    def pill_activity(self):
        """Pour la barre de Prisme, meme le Labo cache : (texte, avancement de
        0 a 1 ou None, infobulle), ou None quand rien ne tourne."""
        if not self._tasks:
            return None
        texts = [text for text, _start in self._tasks.values()]
        fraction = None
        if self._indexing and self.bar.maximum() > 1:
            fraction = self.bar.value() / self.bar.maximum()
        # Court : la barre de Prisme n'a pas la place d'une phrase ; le detail
        # est dans l'infobulle.
        lead = ("Arrêt — " if self._stopping else "") + (
            f"{int(fraction * 100)} %" if fraction is not None else texts[0])
        tip = ("Labo IA\n" + "\n".join(f"• {text}" for text in texts)
               + "\n\nCliquer pour ouvrir le Labo IA.")
        return f"✦ Labo IA : {lead}", fraction, tip

    def _tick_activity(self) -> None:
        import time as _time
        now = _time.monotonic()
        if self._tasks:
            self._spin_at = (self._spin_at + 1) % len(self.SPIN)
            texts = [text for text, _start in self._tasks.values()]
            oldest = min(start for _text, start in self._tasks.values())
            mood = "stop" if self._stopping else "work"
            self.spin.setText(self.SPIN[self._spin_at])
            self.now.setText(("Arrêt en cours — " if self._stopping else "") + "  ·  ".join(texts))
            seconds = int(now - oldest)
            self.clock.setText(f"depuis {seconds // 60} min {seconds % 60:02d} s" if seconds >= 60
                               else f"depuis {seconds} s")
        elif now < self._refused_until:
            mood = "refuse"
            self.spin.setText("!")
            self.now.setText(getattr(self, "_refused_text", ""))
            self.clock.setText("")
        else:
            mood = "idle"
            self.spin.setText("✓")
            self.now.setText("Prêt — rien ne tourne")
            self.clock.setText("")
        if self.activity.property("mood") != mood:
            self.activity.setProperty("mood", mood)
            self.activity.style().unpolish(self.activity)
            self.activity.style().polish(self.activity)
            self.spin.setStyleSheet("color: #e0a030;" if mood in ("stop", "refuse") else
                                    "color: #5fd38d;" if mood == "idle" else "")

    def _ready_to_search(self) -> bool:
        if getattr(self, "_lacking", ""):
            self._engine_changed()          # peut-etre installe depuis
        if getattr(self, "_lacking", ""):
            self._say(self._lacking + " Cliquez « Installer les bibliothèques ».")
            return False
        if self.index is None:
            self._say("L'index se lit encore, un instant…")
            return False
        if not self.index.size:
            self._say("Rien n'est encore indexé : cliquez « Indexer la collection ». Les "
                      "recherches portent sur ce qui est déjà fait, on peut chercher pendant "
                      "que ça avance.")
            return False
        if self._busy and not self._indexing:
            # Le travail d'avant se termine : la recherche partira juste apres,
            # au lieu d'etre refusee -- les anciens resultats restaient alors a
            # l'ecran, et l'on croyait voir la reponse a la nouvelle phrase.
            self._say("Recherche en attente : le travail précédent se termine…")
            return False
        return True

    # -- le moteur ---------------------------------------------------------------------
    def _engine_changed(self) -> None:
        name = self.engine_pick.currentData() or ia.DEFAULT_ENGINE
        if self._busy and self.engine is not None and getattr(self.engine, "name", name) != name \
                and self._engine_given is None:
            # On ne change pas de moteur au milieu d'un travail.
            self.engine_pick.blockSignals(True)
            self.engine_pick.setCurrentIndex(max(0, self.engine_pick.findData(self.engine.name)))
            self.engine_pick.blockSignals(False)
            return self._say("Arrêtez d'abord le travail en cours pour changer de moteur.")
        try:
            self.window.cfg["labo_engine"] = name
        except (KeyError, TypeError):
            pass
        if self._engine_given is not None:
            self.engine = self._engine_given
            ready = ""
        else:
            lacking = ia.missing()
            ready = (f"Il manque des bibliothèques : {', '.join(lacking)}." if lacking else "")
            if not ready and (self.engine is None or getattr(self.engine, "name", "") != name):
                if self.engine is not None and hasattr(self.engine, "close"):
                    self.engine.close()
                # Le modele vit dans son propre processus : la fenetre de
                # Prisme ne gele jamais, ni a son chargement ni pendant
                # l'indexation.
                self.engine = ia.RemoteClip(name)
                self._model_ready = False
                self._loaded_part = ""
        self._lacking = ready
        self.install_button.setVisible(bool(ready))
        blocked = ia.can_install() if ready else ""
        self.install_button.setEnabled(not blocked)
        self.index_button.setVisible(not ready)
        self.stop_button.setVisible(not ready)
        spec = ia.ENGINES.get(name, {})
        if ready:
            self._say(blocked or (ready + " Cliquez « Installer les bibliothèques » "
                                  "(plusieurs Go, une seule fois)."))
        elif ia.model_on_disk(name) or self._engine_given is not None:
            self._say(f"{spec.get('short', name)} est sur ce PC. Il se charge en mémoire à "
                      "l'ouverture du Labo (de 20 s à une minute), rien n'est retéléchargé.")
            self._preload()
        else:
            self._say(f"Ce moteur se télécharge une seule fois ({spec.get('download', '1 Go')}), "
                      "à sa première utilisation.")
        engine_name = getattr(self.engine, "name", name) if self.engine is not None else name
        if not ready and (self.index is None or self.index.engine_name != engine_name):
            self._load_index(engine_name)
        self._show_index_state()

    def _preload(self) -> None:
        """Le moteur se charge en memoire des l'ouverture, en fond : il est
        pret quand on cherche, au lieu de faire attendre la premiere fois."""
        if self._engine_given is not None or self.engine is None or self._busy:
            return
        engine = self.engine

        def work() -> None:
            try:
                if engine is self.engine and not self._busy:
                    self._ensure_model("text")
                    if engine is self.engine:
                        self.mail.put(("said", f"{ia.ENGINES.get(engine.name, {}).get('short', '')}"
                                               " chargé : prêt à chercher."))
            except Exception as exc:                         # noqa: BLE001
                self.mail.put(("error", str(exc)))
        threading.Thread(target=work, daemon=True, name="prisme-labo-prechargement").start()

    def _load_index(self, name: str) -> None:
        """L'index se lit en fond : des dizaines de Mo pour une grande
        collection, qui figeaient l'ouverture du labo."""
        self._loading += 1
        number = self._loading
        self.index = None
        self.index_state.setText("Lecture de l'index…")

        def work() -> None:
            try:
                found = ia.SceneIndex(name)
            except Exception as exc:                         # noqa: BLE001
                self.mail.put(("error", f"index illisible : {exc}"))
                return
            self.mail.put(("index", (number, found)))
        threading.Thread(target=work, daemon=True, name="prisme-labo-index").start()

    def _show_index_state(self) -> None:
        if self.index is None:
            return
        videos = self._videos()
        total = len(videos)
        done = len(self.index.done.intersection(videos)) if total else len(self.index.done)
        self._show_engine_about(max(0, total - done))
        part = f" ({done * 100 // total} %)" if total else ""
        said = f"{done:,} / {total:,} vidéos indexées{part}".replace(",", " ")
        self.index_state.setText(said)
        if not self._busy or not self._indexing:
            self.bar.setRange(0, max(1, total))
            self.bar.setValue(done)
            self.bar.setFormat(said)
        self.index_button.setText("Reprendre l'indexation" if 0 < done < total
                                  else "Indexer la collection")

    def _show_engine_about(self, left: int) -> None:
        """Ce que coute le moteur choisi, pour ce qui reste a indexer."""
        name = self.engine_pick.currentData() or ia.DEFAULT_ENGINE
        spec = ia.ENGINES.get(name, {})
        gpu = getattr(self.engine, "device", "cpu") == "cuda"
        # Mesure sur une GTX 1050 : le petit moteur n'y gagne que 1,6 fois (34 ms
        # au lieu de 56) ; un gros, seul sur la carte, autour de 4 (estimation).
        speedup = (1.6 if not spec.get("heavy") else 4.0) if gpu else 1.0
        per_image = spec.get("image_ms", 100) / 1000.0 / speedup
        seconds = left * FRAMES_PER_VIDEO * per_image
        hours = seconds / 3600
        if not left:
            when = "rien à indexer"
        elif hours < 1:
            when = f"environ {max(1, int(seconds // 60))} min"
        elif hours < 48:
            when = f"environ {hours:.0f} h"
        else:
            when = f"environ {hours / 24:.0f} jours"
        def spaced(number: int) -> str:
            return f"{number:,}".replace(",", " ")
        self.engine_about.setText(
            f"{spec.get('short', name)} : {spaced(spec.get('image_ms', 0))} ms par image sur le "
            f"processeur{' — la carte graphique est utilisée (gain estimé)' if gpu else ''}. Reste à indexer : "
            f"{spaced(left)} vidéos, {when} de calcul (plus l'extraction des images qui "
            "manquent).")

    # -- l'installation ------------------------------------------------------------------
    def _use_gpu(self) -> None:
        commands = ia.gpu_switch_commands()
        if not commands:
            return self._say("PyTorch utilise déjà la carte graphique.")
        if self._busy:
            return self._say("Arrêtez d'abord le travail en cours.")
        if self.engine is not None and hasattr(self.engine, "close"):
            self.engine.close()             # ses fichiers seront remplaces
            self._loaded_part = ""
        self._commands = list(commands)
        self.log.show()
        self.log.appendPlainText("PyTorch pour carte NVIDIA (environ 2,5 Go)… Relancez Prisme "
                                 "à la fin.")
        self.gpu_button.setEnabled(False)
        self._next_command()

    def _install(self) -> None:
        commands = ia.install_commands()
        if not commands:
            return self._engine_changed()
        self._commands = list(commands)
        self.log.show()
        self.log.appendPlainText("Installation (plusieurs Go, patience)…")
        self.install_button.setEnabled(False)
        self._next_command()

    def _next_command(self) -> None:
        if not self._commands:
            self.log.appendPlainText("Terminé.")
            return self._engine_changed()
        argv = self._commands.pop(0)
        self.log.appendPlainText("$ " + " ".join(argv))
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(
            lambda: self._pip_output(process))
        process.finished.connect(lambda code, _status: self._pip_done(code))
        self._process = process
        process.start(argv[0], argv[1:])

    def _pip_output(self, process) -> None:
        text = bytes(process.readAllStandardOutput()).decode("utf-8", "replace")
        # pip redessine sa barre par retours chariot : une ligne par etat.
        for line in text.replace("\r", "\n").splitlines():
            if line.strip():
                self.log.appendPlainText(line.rstrip())

    def _pip_done(self, code: int) -> None:
        if code != 0:
            self.log.appendPlainText(f"Échec (code {code}).")
            self.install_button.setEnabled(True)
            return
        self._next_command()

    # -- le travail de fond ---------------------------------------------------------------
    _indexing = False

    def _run(self, work, *args, indexing: bool = False, label: str = "") -> None:
        if self._busy:
            return
        self._busy = True
        self._indexing = indexing
        self._stop = False
        self._stopping = False
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Arrêter")
        self.index_button.setEnabled(False)
        self._set_task("job", label or ("Indexation" if indexing else "Travail en cours"))

        def body() -> None:
            try:
                work(*args)
            except Exception as exc:                         # noqa: BLE001
                self.mail.put(("error", str(exc)))
            finally:
                self.mail.put(("idle", None))
        threading.Thread(target=body, daemon=True, name="prisme-labo").start()

    def _halt(self) -> None:
        if not self._busy:
            return self._refuse("Rien à arrêter : aucun travail ne tourne.")
        self._stop = True
        self._stopping = True
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Arrêt en cours…")
        self._say("La vidéo en cours se termine et ce qui est déjà calculé est enregistré : "
                  "quelques secondes, jusqu'à une minute avec un gros moteur.")
        self._tick_activity()

    _loaded_part = ""

    def _ensure_model(self, part: str = "text") -> None:
        """Dans un fil : le moteur charge la partie utile (images pour
        indexer, texte pour chercher), et le dit quand ca prend du temps."""
        if self._loaded_part not in (part, "both"):
            name = getattr(self.engine, "name", "")
            spec = ia.ENGINES.get(name, {})
            if ia.model_on_disk(name) or self._engine_given is not None:
                self.mail.put(("said", f"Chargement du moteur {spec.get('short', '')} en mémoire… "
                                       "(il est déjà sur ce PC : rien à télécharger, de 20 s à "
                                       "une minute)"))
            else:
                self.mail.put(("said", f"Téléchargement du moteur {spec.get('short', '')} "
                                       f"({spec.get('download', '1 Go')}), une seule fois…"))
            self._task("model", f"Chargement du moteur {spec.get('short', '')}")
            try:
                self.engine.load(part)
            finally:
                self._task("model", None)
            heavy = spec.get("heavy", False)
            self._loaded_part = part if heavy else "both"
            self._model_ready = True
            self.mail.put(("device", getattr(self.engine, "device", "cpu")))

    def _read_mail(self) -> None:
        for _ in range(50):
            try:
                kind, value = self.mail.get_nowait()
            except queue.Empty:
                return
            if kind == "progress":
                done, total = value
                self.bar.setRange(0, max(1, total))
                self.bar.setValue(done)
                part = f" ({done * 100 // total} %)" if total else ""
                self.bar.setFormat(f"Indexation en cours : {done:,} / {total:,} vidéos{part}"
                                   .replace(",", " "))
                if "job" in self._tasks:
                    label = self._tasks["job"][0].split(" : ")[0]
                    self._set_task("job", f"{label} : {done:,} / {total:,}{part}".replace(",", " "))
            elif kind == "said":
                self._say(value)
            elif kind == "device":
                self._show_index_state()
            elif kind == "gpu":
                state, text = value
                self.gpu_state.setText(text)
                self.gpu_state.setVisible(bool(text))
                self.gpu_button.setVisible(state in ("switch", "driver")
                                           and bool(ia.gpu_switch_commands()))
            elif kind == "bench":
                self._bench_results(value)
            elif kind == "bench_ready":
                self._bench_state_text(value)
            elif kind == "error":
                self._say(f"Erreur : {value}")
            elif kind == "index":
                number, found = value
                if number == self._loading and self.index is None:
                    self.index = found
                    self._show_index_state()
            elif kind == "results":
                target, rows = value
                self._fill(target, rows)
            elif kind == "groups":
                self._show_groups(value)
            elif kind == "task":
                self._set_task(*value)
            elif kind == "refuse":
                self._refuse(value)
            elif kind == "idle":
                was_stopping = self._stopping
                self._busy = False
                self._indexing = False
                self._stopping = False
                self._set_task("job", None)
                self.stop_button.setEnabled(False)
                self.stop_button.setText("Arrêter")
                self.index_button.setEnabled(True)
                self._show_index_state()
                if was_stopping:
                    self._say("Arrêté. Tout ce qui avait été calculé est gardé.")
                if self._after_idle:
                    QTimer.singleShot(0, self._after_idle.pop(0))
            elif kind == "bench_said":
                self.bench_state.setText(value)
                self._say(value)
            elif kind == "bench_done":
                self._bench_running = False

    # -- indexer, chercher, attribuer ---------------------------------------------------
    def _index(self) -> None:
        if getattr(self, "_lacking", ""):
            self._engine_changed()
        if getattr(self, "_lacking", ""):
            self._say(self._lacking + " Cliquez « Installer les bibliothèques ».")
            self.install_button.setFocus()
            return
        if self.index is None:
            return self._say("L'index se lit encore, un instant…")
        if self._busy:
            return self._refuse("Un travail tourne déjà : attendez qu'il finisse, ou "
                                "« Arrêter ».")
        videos = self._videos()
        if not videos:
            return self._say("Prisme n'a pas encore lu la collection de la racine : attendez la "
                             "fin de son analyse (en haut de la fenêtre de Prisme), puis "
                             "réessayez.")

        # Le compteur part de ce qui est deja fait : « 3 749 / 62 031 », et non
        # « 29 / 58 311 » sur ce qui reste.
        already = len(self.index.done.intersection(videos))

        def work() -> None:
            self._shared_moments = ia.known_moments(getattr(self.engine, "name", ""),
                                                    self.index.folder)
            self._ensure_model("vision")
            self.mail.put(("said", "Indexation en cours. Prisme reste utilisable ; vous pouvez "
                                   "chercher dans ce qui est déjà fait."))
            default = self._frame_of == self._default_frame
            moments = self._moments if default else None
            added = ia.build(self.index, self.engine, videos, self._frame_of, self._duration,
                             progress=lambda d, t: self.mail.put(
                                 ("progress", (already + d, already + t))),
                             stop=lambda: self._stop, moments_of=moments,
                             workers=WORKERS if default else 1)
            self.mail.put(("said", f"{added} vidéo(s) ajoutée(s) à l'index."
                           + (" Arrêté : « Reprendre l'indexation » continue." if self._stop
                              else "")))
        self._run(work, indexing=True,
                  label=f"Indexation {ia.ENGINES.get(getattr(self.engine, 'name', ''), {}).get('short', '')}")

    def _resize_thumbs(self, width: int) -> None:
        height = int(width * 9 / 16)
        self.results.setIconSize(QSize(width, height))
        self.results.setGridSize(QSize(width + 14, height + 48))

    def _more_results(self) -> None:
        self._top += 60
        self._loading_more = True
        self._run_search(more=True)

    def _near_end(self, value: int) -> None:
        """Au bas de la liste : les 60 suivants, sans bouton a chercher."""
        bar = self.results.verticalScrollBar()
        if (self._can_more and not self._loading_more and bar.maximum() > 0
                and value >= bar.maximum() - bar.pageStep() // 2):
            self._more_results()

    def _search(self) -> None:
        text = self.query.text().strip()
        if not text:
            return
        if self._busy and not self._indexing and self.index is not None and self.index.size:
            self.results.clear()
            self.result_count.setText(f"Recherche de « {text} » en attente…")
            if self._search not in self._after_idle:
                self._after_idle.append(self._search)
        if not self._ready_to_search():
            return
        self._loading_more = False
        if text != self._asked:
            self._top = 60
            self._shown = set()
            saved = self._saved_examples().get(self._example_key(text), {})
            self._liked = [tuple(p) for p in saved.get("liked", [])]
            self._disliked = [tuple(p) for p in saved.get("disliked", [])]
            self._asked = text
        self._run_search()

    @staticmethod
    def _example_key(text: str) -> str:
        return " ".join(str(text).lower().split())

    def _saved_examples(self) -> dict:
        try:
            return dict(self.window.cfg.get("labo_examples", {}) or {}) if hasattr(
                self.window.cfg, "get") else {}
        except Exception:                                    # noqa: BLE001
            return {}

    def _keep_examples(self) -> None:
        saved = self._saved_examples()
        key = self._example_key(self._asked)
        if self._liked or self._disliked:
            saved[key] = {"liked": [list(p) for p in self._liked],
                          "disliked": [list(p) for p in self._disliked]}
        else:
            saved.pop(key, None)
        try:
            self.window.cfg["labo_examples"] = saved
        except (KeyError, TypeError):
            pass

    def _run_search(self, more: bool = False) -> None:
        if not more:
            self._loading_more = False     # une nouvelle liste, pas une suite
        text = self._asked
        liked = self.index.frame_vectors(self._liked) if self._liked else None
        disliked = self.index.frame_vectors(self._disliked) if self._disliked else None
        _whole, parts, negatives = ia.parse_query(text)
        self._show_feedback()
        args = (text, liked, disliked, parts, negatives)
        if self._busy and self._indexing:
            # La recherche part a cote de l'indexation : elle ne lit l'index
            # que tel qu'il est a cet instant.
            def searching() -> None:
                self._task("search", f"Recherche « {text} »")
                try:
                    self._search_work(*args)
                finally:
                    self._task("search", None)
            threading.Thread(target=searching, daemon=True, name="prisme-labo-recherche").start()
            return
        self._run(self._search_work, *args, label=f"Recherche « {text} »")

    def _search_work(self, text, liked, disliked, parts, negatives) -> None:
        if self._busy and self._indexing and ia.ENGINES.get(
                getattr(self.engine, "name", ""), {}).get("heavy"):
            self.mail.put(("said", "Ce moteur indexe en ce moment : sa partie texte n'est pas "
                                   "chargée. Arrêtez l'indexation pour chercher, ou cherchez avec "
                                   "le moteur rapide."))
            return
        self._ensure_model("text")
        self.mail.put(("said", "Recherche…"))
        good = {v for v, _t in self._liked}
        bad = {v for v, _t in self._disliked}
        rows = ia.smart_search(self.index, self.engine, text, self._top + len(bad), liked, disliked)
        # Les videos ecartees disparaissent ; les bons exemples passent devant,
        # marques : on voit ce que la recherche a retenu de ce qu'on lui a dit.
        rows = [row for row in rows if row[0] not in bad]
        examples = [(v, ts, 99.0) for v, ts in self._liked]
        seen = set()
        ordered = []
        for video, ts, score in examples + rows:
            if video in seen:
                continue
            seen.add(video)
            ordered.append((video, ts, score))
        rows = ordered[:self._top]
        fresh = len({r[0] for r in rows} - self._shown - good) if self._shown else 0
        self.mail.put(("results", ("search", rows)))
        said = f"« {text} » : {len(rows)} vidéo(s), la plus proche d'abord."
        if self._liked or self._disliked:
            said = (f"« {text} » : {len(good)} bon(s) exemple(s) en tête (✓), {len(bad)} vidéo(s) "
                    f"écartée(s) ; {fresh} nouvelle(s) vidéo(s) dans les résultats.")
        if parts:
            said += " Idées exigées : " + ", ".join(f"« {p} »" for p in parts) + "."
        if negatives:
            said += " Écarté : " + ", ".join(f"« {n} »" for n in negatives) + "."
        self.mail.put(("said", said))

    @staticmethod
    def _stack(parts: list):
        if not parts:
            return None
        import numpy as np
        return np.vstack(parts)

    def _learn(self, liked: bool) -> None:
        """« Plus comme ça » / « moins comme ça » : les cochees deviennent des
        exemples, et la recherche se refait."""
        if not self._asked:
            return self._say("Cherchez d'abord quelque chose.")
        picks = [(item.data(Qt.UserRole), float(item.data(Qt.UserRole + 1) or 0))
                 for item in self._checked_items(self.results)]
        if not picks:
            return self._say("Cochez d'abord des résultats à prendre en exemple.")
        target = self._liked if liked else self._disliked
        other = self._disliked if liked else self._liked
        for pick in picks:
            if pick not in target:
                target.append(pick)
            if pick in other:
                other.remove(pick)
        self._keep_examples()
        self._run_search()

    def _forget_examples(self) -> None:
        self._liked, self._disliked = [], []
        self._keep_examples()
        if self._asked:
            self._run_search()
        self._show_feedback()

    def _show_feedback(self) -> None:
        good = len(self._liked)
        bad = len(self._disliked)
        if good or bad:
            parts = []
            if good:
                parts.append(f"{good} vidéo(s) aimée(s)")
            if bad:
                parts.append(f"{bad} écartée(s)")
            self.feedback_state.setText(
                "Cette recherche tient compte de vos " + " et ".join(parts)
                + " (👍 / 👎), en plus des mots — elle s'en souviendra la "
                "prochaine fois. « Repartir des seuls mots » les efface.")
        self.feedback_state.setVisible(bool(good or bad))
        if hasattr(self, "forget_button"):
            self.forget_button.setVisible(bool(good or bad))

    def _fill(self, target: str, rows: list) -> None:
        view = self.results if target == "search" else self.group_view
        good = {v for v, _t in self._liked} if target == "search" else set()
        appending = target == "search" and self._loading_more
        if appending:
            # La suite : ce qui est deja la reste en place (cases cochees
            # comprises), on ajoute dessous, et la liste ne remonte pas.
            there = {view.item(at).data(Qt.UserRole) for at in range(view.count())}
            fresh = [row for row in rows if row[0] not in there]
        else:
            view.clear()
            fresh = rows
        if target == "search":
            self._loading_more = False
            self._shown = {row[0] for row in rows}
            self._can_more = len(rows) >= self._top
            self.result_count.setText(
                (f"{len(rows)} vidéos pour « {self._asked} », de la plus proche à la moins proche"
                 + (" — la suite arrive en descendant" if self._can_more else ""))
                if rows else "")
        for video, ts, _score in fresh:
            minutes, seconds = divmod(int(ts), 60)
            mark = "✓ exemple · " if video in good else ""
            item = QListWidgetItem(f"{mark}{Path(video).name}\nà {minutes}:{seconds:02d}")
            item.setData(Qt.UserRole, video)
            item.setData(Qt.UserRole + 1, float(ts))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            # Un groupe propose est coche d'office : on decoche ce qui n'y
            # a pas sa place, puis on cree le mot-cle.
            item.setCheckState(Qt.Checked if target == "groups" else Qt.Unchecked)
            item.setToolTip(video)
            path = self._cached_frame(video, ts)
            if path:
                item.setIcon(QIcon(QPixmap(str(path))))
            view.addItem(item)
        if not rows and target == "search":
            self._say("Aucune vidéo ne correspond. Essayez moins d'idées, ou d'autres mots.")
        if appending and not fresh:
            self._can_more = False

    def _cached_frame(self, video: str, ts: float):
        try:
            from . import media
            return media.cached_thumb(Path(video), ts, self._width())
        except Exception:                                    # noqa: BLE001
            return None

    def _play(self, item) -> None:
        """La video, au moment de l'image trouvee (lecteur flottant)."""
        video = item.data(Qt.UserRole)
        if not video:
            return
        start = int(float(item.data(Qt.UserRole + 1) or 0) * 1000)
        floating = getattr(self.window, "play_floating_path", None)
        if floating is not None:
            floating(video, start)
            return
        play = getattr(self.window, "_aside_play", None)
        if play is not None:
            play(video, Path(video).name)

    # -- les collections reconnues a leur nom ---------------------------------------------
    GRAINS = (("Fin — morceaux bornés par la ponctuation (conseillé)", "fin"),
              ("Très fin — le plus long morceau commun", "tres_fin"),
              ("Large — le plus court morceau commun", "large"))

    def _groups_tab(self, parent) -> QWidget:
        """Les fichiers d'une meme collection, reconnus a leur nom (un meme
        morceau, a la lettre pres), et ce qu'on peut en faire."""
        page = QWidget(parent)
        page.setObjectName("labGroups")
        box = QHBoxLayout(page)
        box.setContentsMargins(14, 14, 14, 12)
        box.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(_label(
            "Un même morceau de nom, à la lettre près (même casse, même ponctuation), au "
            "début ou au milieu : « Cum Fantasy, … », « Anna - Cum Fantasy - … ». Ces fichiers "
            "viennent d'une même collection.", "labHint", page))
        row = QHBoxLayout()
        self.grain = QComboBox(page)
        for text, key in self.GRAINS:
            self.grain.addItem(text, key)
        saved = self.window.cfg.get("labo_grain", "fin") if hasattr(self.window.cfg, "get") else "fin"
        self.grain.setCurrentIndex(max(0, self.grain.findData(saved)))
        row.addWidget(self.grain, 1)
        go = QPushButton("Chercher", page)
        go.setObjectName("labPrimary")
        go.clicked.connect(self._propose)
        row.addWidget(go)
        left.addLayout(row)
        self.group_filter = QLineEdit(page)
        self.group_filter.setPlaceholderText("Filtrer les collections…")
        self.group_filter.textChanged.connect(self._filter_groups)
        left.addWidget(self.group_filter)
        self.group_list = QListWidget(page)
        self.group_list.setObjectName("labGroupList")
        self.group_list.setItemDelegate(_GroupDelegate(self.group_list))
        self.group_list.currentRowChanged.connect(self._pick_group)
        left.addWidget(self.group_list, 1)
        box.addLayout(left, 2)

        card, right = _card(page)
        self.group_title = _label("Choisissez une collection à gauche", "labTitle", card)
        right.addWidget(self.group_title)
        self.group_info = _label("", "labDim", card)
        right.addWidget(self.group_info)
        row = QHBoxLayout()
        row.addWidget(_label("Nom", "labDim", card))
        self.group_name = QLineEdit(card)
        self.group_name.setPlaceholderText("Le nom du groupe ou du dossier, à corriger si besoin")
        self.group_name.textChanged.connect(self._show_target)
        row.addWidget(self.group_name, 1)
        right.addLayout(row)
        # Ce qu'on fait des fichiers coches : les regarder, ou les ranger.
        row = QHBoxLayout()
        row.setSpacing(8)
        for text, tip, slot in (
            ("▦  Voir sur le mur", "Les fichiers cochés, ensemble sur le mur", self._group_wall),
            ("▶  Playlist", "Les fichiers cochés, l'un après l'autre dans le lecteur de "
                            "droite", self._group_playlist),
            ("📂  Parcourir", "Les fichiers cochés comme s'ils étaient dans un dossier : "
                             "vignettes, fiches, Échap pour revenir", self._group_browse),
        ):
            button = QPushButton(text, card)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        right.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(8)
        make = QPushButton("✦  Créer un groupe", card)
        make.setObjectName("labPrimary")
        make.setToolTip("Un dossier virtuel : il apparaît dans l'onglet Mots-clés (« Mes "
                        "mots », marqué ✦). Rien ne bouge sur le disque.")
        make.clicked.connect(self._group_tag)
        row.addWidget(make)
        keep = QPushButton("⇢  Ranger dans un dossier", card)
        keep.setObjectName("labPrimary")
        keep.setToolTip("Les fichiers cochés sont déplacés dans un dossier de ce nom, créé "
                        "s'il n'existe pas. Comme tout rangement de Prisme : Ctrl+Z annule.")
        keep.clicked.connect(self._keep_group)
        row.addWidget(keep)
        row.addStretch(1)
        drop = QPushButton("Ignorer", card)
        drop.setToolTip("Ce n'est pas une collection : elle quitte la liste")
        drop.clicked.connect(self._drop_group)
        row.addWidget(drop)
        right.addLayout(row)
        row = QHBoxLayout()
        self.group_target = _label("", "labDim", card)
        row.addWidget(self.group_target, 1)
        where = QPushButton("Ailleurs…", card)
        where.setToolTip("Choisir où créer le dossier")
        where.clicked.connect(self._choose_home)
        row.addWidget(where)
        right.addLayout(row)
        self.group_view = QListWidget(card)
        self.group_view.setObjectName("labFiles")
        self.group_view.setToolTip("Décochez ce qui n'est pas de cette collection. "
                                   "Double-clic : lire la vidéo.")
        self.group_view.itemDoubleClicked.connect(self._play)
        right.addWidget(self.group_view, 1)
        box.addWidget(card, 3)
        self._groups: list = []
        self._home = None
        return page

    def _propose(self) -> None:
        videos = self._videos()
        if not videos:
            return self._say("Prisme n'a pas encore lu la collection de la racine : attendez la "
                             "fin de son analyse, puis réessayez.")
        grain = self.grain.currentData() or "fin"
        try:
            self.window.cfg["labo_grain"] = grain
        except (KeyError, TypeError):
            pass
        self.group_list.clear()
        self.group_view.clear()
        self.group_name.clear()
        self._say(f"Lecture de {len(videos):,} noms de fichiers…".replace(",", " "))

        self._set_task("names", f"Lecture de {len(videos):,} noms".replace(",", " "))

        def work() -> None:
            from .namegroups import find_groups
            try:
                found = find_groups(videos, grain)
            finally:
                self._task("names", None)
            self.mail.put(("groups", found))
            grouped = sum(len(g["videos"]) for g in found)
            self.mail.put(("said", f"{len(found)} collections reconnues ({grouped} fichiers), "
                                   "les plus fournies d'abord. Choisissez-en une à gauche."))
        threading.Thread(target=work, daemon=True, name="prisme-labo-collections").start()

    def _show_groups(self, groups: list) -> None:
        self._groups = list(groups)
        self.group_list.clear()
        for group in self._groups:
            spread_ = group.get("folders", 1)
            where = f"  ·  dans {spread_} dossiers" if spread_ > 1 else "  ·  un seul dossier"
            item = QListWidgetItem(group["name"])
            item.setData(Qt.UserRole, f"{len(group['videos'])} fichiers{where}")
            self.group_list.addItem(item)
        if self._groups:
            self.group_list.setCurrentRow(0)
        self._filter_groups(self.group_filter.text())

    def _filter_groups(self, text: str) -> None:
        wanted = text.strip().lower()
        for at in range(self.group_list.count()):
            item = self.group_list.item(at)
            item.setHidden(bool(wanted) and wanted not in item.text().lower())

    def _pick_group(self, row: int) -> None:
        if not (0 <= row < len(self._groups)):
            return
        from .namegroups import home_of
        group = self._groups[row]
        self._home = home_of(group["videos"])
        self.group_name.setText(group["name"])
        self.group_title.setText(group["name"])
        spread_ = group.get("folders", 1)
        self.group_info.setText(f"{len(group['videos'])} fichiers, "
                                + (f"répartis dans {spread_} dossiers" if spread_ > 1
                                   else "tous dans le même dossier")
                                + ". Décochez ce qui n'en fait pas partie.")
        self.group_view.clear()
        for video in group["videos"]:
            path = Path(video)
            item = QListWidgetItem(f"{path.name}\n{path.parent}")
            item.setData(Qt.UserRole, video)
            item.setData(Qt.UserRole + 1, 0.0)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setToolTip(video)
            self.group_view.addItem(item)
        self._show_target()

    def _group_files(self) -> list:
        videos = self._checked(self.group_view)
        if not videos:
            self._say("Cochez d'abord des fichiers de la collection.")
        return videos

    def _group_wall(self) -> None:
        videos = self._group_files()
        show = getattr(self.window, "wall_videos", None)
        if videos and show is not None:
            show(videos)
            self._say(f"{len(videos)} fichier(s) sur le mur de Prisme.")

    def _group_playlist(self) -> None:
        videos = self._group_files()
        play = getattr(self.window, "playlist_videos", None)
        if videos and play is not None:
            play(videos)
            self._say(f"{len(videos)} fichier(s) en playlist, dans le lecteur de droite de Prisme.")

    def _group_browse(self) -> None:
        videos = self._group_files()
        browse = getattr(self.window, "browse_videos", None)
        name = " ".join(self.group_name.text().split()) or "Collection"
        if videos and browse is not None:
            browse(name, videos)
            self._say(f"« {name} » s'ouvre dans Prisme comme un dossier (Échap pour revenir).")

    def _group_tag(self) -> None:
        videos = self._group_files()
        if not videos:
            return
        name = " ".join(self.group_name.text().split())
        if not name:
            self.group_name.setFocus()
            return self._say("Donnez un nom au groupe.")
        add = getattr(self.window, "add_to_keyword", None)
        if add is None:
            return self._say("Prisme n'est pas prêt à recevoir des groupes.")
        added = add(name, videos)
        self._say(f"Groupe « {name} » : {added} fichier(s). Onglet Mots-clés, « Mes mots » (✦) — "
                  "rien n'a bougé sur le disque.")

    def _target(self):
        from .namegroups import folder_name
        name = folder_name(self.group_name.text())
        return (Path(self._home) / name) if self._home is not None and name else None

    def _show_target(self, *_args) -> None:
        target = self._target()
        self.group_target.setText(f"Dossier de rangement : {target}" if target is not None else "")

    def _choose_home(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        start = str(self._home) if self._home is not None else ""
        chosen = QFileDialog.getExistingDirectory(self, "Où créer le dossier ?", start)
        if chosen:
            self._home = Path(chosen)
            self._show_target()

    def _keep_group(self) -> None:
        row = self.group_list.currentRow()
        if not (0 <= row < len(self._groups)):
            return self._say("Choisissez d'abord une collection à gauche.")
        target = self._target()
        if target is None:
            self.group_name.setFocus()
            return self._say("Donnez un nom au dossier.")
        videos = [v for v in self._checked(self.group_view)
                  if Path(v).parent != target]
        if not videos:
            return self._say("Rien à ranger : aucun fichier coché hors de ce dossier.")
        move = getattr(self.window, "move_one", None)
        if move is None:
            return self._say("Prisme n'est pas prêt à ranger des fichiers.")
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, "Ranger la collection",
            f"Déplacer {len(videos)} fichier(s) dans :\n{target}\n\nLe dossier est créé s'il "
            "n'existe pas. Ctrl+Z annule, un fichier à la fois.")
        if answer != QMessageBox.Yes:
            return
        label = target.name
        sent = 0
        for video in videos:
            if move(video, {"path": str(target), "label": label}):
                sent += 1
        self._say(f"« {label} » : {sent} fichier(s) en cours de rangement.")
        self._drop_group()

    def _drop_group(self) -> None:
        row = self.group_list.currentRow()
        if 0 <= row < len(self._groups):
            del self._groups[row]
            self.group_list.takeItem(row)
            if not self._groups:
                self.group_view.clear()
                self.group_name.clear()
                self.group_target.clear()
                self.group_title.setText("Choisissez une collection à gauche")
                self.group_info.clear()

    # -- l'index suit les fichiers ranges -------------------------------------------------
    def follow_moves(self, moves: dict) -> None:
        """Prisme a range des fichiers : l'index en memoire et ceux sur le
        disque suivent (apres le travail en cours, qui ecrit dans l'index)."""
        pending = self.__dict__.setdefault("_pending_moves", {})
        pending.update(moves)
        if self._busy:
            if self._apply_moves not in self._after_idle:
                self._after_idle.append(self._apply_moves)
            return
        self._apply_moves()

    def _apply_moves(self) -> None:
        moves, self._pending_moves = dict(self.__dict__.get("_pending_moves", {})), {}
        if not moves:
            return
        index = self.index
        folder = index.folder if index is not None else None
        threading.Thread(target=lambda: ia.rename_videos(moves, folder, live=[index]),
                         daemon=True, name="prisme-labo-suit").start()

    # -- comparer les moteurs ------------------------------------------------------------
    def _bench_tab(self, parent) -> QWidget:
        """Le meme essai pour chaque moteur : memes videos, memes recherches ;
        on coche ce qui est juste, et chaque moteur recoit sa note."""
        page = QWidget(parent)
        box = QVBoxLayout(page)
        box.setContentsMargins(14, 14, 14, 12)
        box.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(_label("Les mêmes vidéos et les mêmes recherches pour chaque moteur : "
                             "cochez ce qui est juste, chacun reçoit sa note.", "labHint", page), 1)
        how = QPushButton("Comment ça marche ?", page)
        how.setCheckable(True)
        top.addWidget(how)
        box.addLayout(top)
        self.bench_help = _label(
            "1) « Préparer l'essai » : les vidéos déjà vues par le moteur rapide sont indexées "
            "par chaque autre moteur, aux mêmes instants (images déjà en cache : seul le calcul "
            "de chaque moteur reste à faire). 2) Écrivez quelques recherches, une par ligne, et "
            "cliquez « Comparer ». 3) Choisissez une recherche dans la liste, et cochez les "
            "résultats justes, dans n'importe quelle colonne : une vidéo cochée l'est partout. "
            "Chaque moteur reçoit sa note : la part de ses résultats qui sont justes. Cocher ici "
            "ne fait rien apprendre aux moteurs : cela sert seulement à les noter.",
            "labHint", page)
        self.bench_help.hide()
        how.toggled.connect(self.bench_help.setVisible)
        box.addWidget(self.bench_help)
        row = QHBoxLayout()
        row.addWidget(_label("Vidéos de l'essai", "labDim", page))
        self.bench_size = QSpinBox(page)
        self.bench_size.setRange(100, 100000)
        self.bench_size.setSingleStep(100)
        self.bench_size.setValue(BENCH_SIZE)
        row.addWidget(self.bench_size)
        prepare = QPushButton("Préparer l'essai", page)
        prepare.setObjectName("labPrimary")
        prepare.clicked.connect(self._bench_prepare)
        row.addWidget(prepare)
        self.bench_state = _label("", "labDim", page)
        row.addWidget(self.bench_state, 1)
        box.addLayout(row)
        row = QHBoxLayout()
        self.bench_queries = QPlainTextEdit(page)
        self.bench_queries.setPlaceholderText("woman peeing outdoors\ncouple dans une voiture\n"
                                              "fille en uniforme")
        self.bench_queries.setMaximumHeight(78)
        saved = self._bench_saved()
        self.bench_queries.setPlainText("\n".join(saved.get("queries", [])))
        row.addWidget(self.bench_queries, 1)
        side = QVBoxLayout()
        run = QPushButton("Comparer", page)
        run.setObjectName("labPrimary")
        run.clicked.connect(self._bench_run)
        side.addWidget(run)
        self.bench_pick = QComboBox(page)
        self.bench_pick.currentIndexChanged.connect(self._bench_show)
        side.addWidget(self.bench_pick)
        nothing = QPushButton("Rien de juste ici", page)
        nothing.setToolTip("Cette recherche compte, même si aucun résultat n'est juste.")
        nothing.clicked.connect(self._bench_nothing)
        side.addWidget(nothing)
        row.addLayout(side)
        box.addLayout(row)
        self.bench_cols = QHBoxLayout()
        self.bench_cols.setSpacing(10)
        self.bench_views: dict = {}
        self.bench_heads: dict = {}
        for name in self._bench_engines():
            spec = ia.ENGINES.get(name, {})
            column = QVBoxLayout()
            head = _label(spec.get("short", name), "labScore", page)
            column.addWidget(head)
            view = QListWidget(page)
            view.setViewMode(QListView.IconMode)
            view.setResizeMode(QListView.Adjust)
            view.setIconSize(QSize(176, 99))
            view.setGridSize(QSize(188, 140))
            view.setMovement(QListView.Static)
            view.setWordWrap(True)
            view.itemDoubleClicked.connect(self._play)
            view.itemChanged.connect(lambda item, n=name: self._bench_mark(n, item))
            column.addWidget(view, 1)
            self.bench_cols.addLayout(column, 1)
            self.bench_views[name] = view
            self.bench_heads[name] = head
        box.addLayout(self.bench_cols, 1)
        self.bench_total = _label("", "labScore", page)
        box.addWidget(self.bench_total)
        self._bench = {"results": {}, "right": {q: set(v) for q, v in saved.get("right", {}).items()},
                       "judged": set(saved.get("judged", []))}
        self._bench_filling = False
        return page

    def _bench_saved(self) -> dict:
        try:
            return dict(self.window.cfg.get("labo_bench", {}) or {}) if hasattr(
                self.window.cfg, "get") else {}
        except Exception:                                    # noqa: BLE001
            return {}

    def _bench_keep(self) -> None:
        try:
            self.window.cfg["labo_bench"] = {
                "queries": [q for q in self.bench_queries.toPlainText().splitlines() if q.strip()],
                "right": {q: sorted(v) for q, v in self._bench["right"].items() if v},
                "judged": sorted(self._bench["judged"])}
        except (KeyError, TypeError):
            pass

    def _engine_for(self, name: str):
        if self._engine_given is not None:
            return self._engine_given
        if self.engine is not None and getattr(self.engine, "name", "") == name:
            return self.engine
        return ia.RemoteClip(name)

    def _index_for(self, name: str):
        if self.index is not None and self.index.engine_name == name:
            return self.index
        if self._engine_given is not None:
            return ia.SceneIndex(getattr(self._engine_given, "name", name),
                                 self.index.folder if self.index is not None else ia.LAB_DIR)
        return ia.SceneIndex(name)

    def _bench_engines(self) -> list:
        if self._engine_given is not None:
            return [getattr(self._engine_given, "name", ia.DEFAULT_ENGINE)]
        return list(ia.ENGINES)

    def _release_engine(self) -> None:
        """Le moteur de la fenetre rend la carte (il se rechargera au besoin)."""
        if self._engine_given is None and self.engine is not None and hasattr(self.engine, "close"):
            try:
                self.engine.close(wait=True)
            except TypeError:
                self.engine.close()
            self._loaded_part = ""
            self._model_ready = False

    def _bench_say(self, text: str) -> None:
        """Dit dans l'onglet de l'essai, et en bas : on ne voyait rien venir."""
        self.mail.put(("bench_said", text))

    def _bench_prepare(self) -> None:
        if getattr(self, "_lacking", ""):
            return self._say(self._lacking + " Cliquez « Installer les bibliothèques ».")
        if self._busy:
            from PySide6.QtWidgets import QMessageBox
            answer = QMessageBox.question(
                self, "Préparer l'essai",
                "Une indexation est en cours. La mettre en pause pour préparer l'essai ? "
                "Elle reprendra toute seule ensuite, là où elle s'est arrêtée.")
            if answer != QMessageBox.Yes:
                return
            resume = self._indexing
            self._after_idle.append(lambda: self._bench_prepare_now(resume))
            self._halt()
            self.bench_state.setText("Pause de l'indexation, puis préparation de l'essai…")
            return
        self._bench_prepare_now(False)

    def _bench_prepare_now(self, resume: bool = False) -> None:
        if self._busy:
            self._after_idle.append(lambda: self._bench_prepare_now(resume))
            return
        if resume:
            self._after_idle.append(self._index)
        size = self.bench_size.value()

        def work() -> None:
            base = self._index_for(ia.DEFAULT_ENGINE)
            moments: dict = {}
            for video, ts in base.rows:
                moments.setdefault(video, []).append(ts)
            # Toujours les memes videos, melangees une fois pour toutes.
            sample = sorted(moments, key=ia.fingerprint)[:size]
            if not sample:
                self.mail.put(("said", "Indexez d'abord quelques vidéos avec le moteur rapide."))
                return
            names = [n for n in self._bench_engines() if n != ia.DEFAULT_ENGINE]
            # Un seul moteur a la fois sur la carte : deux n'y tiennent pas
            # (4 Go sur une GTX 1050), et Windows debordait alors sur la
            # memoire du PC -- dix fois plus lent, et tout le PC ramait.
            self._release_engine()
            for number, name in enumerate(names, 1):
                if self._stop:
                    break
                index = self._index_for(name)
                todo = [v for v in sample if v not in index.done]
                if not todo:
                    continue
                engine = self._engine_for(name)
                short = ia.ENGINES.get(name, {}).get("short", name)
                self._bench_say(f"Essai : {short} ({number}/{len(names)}), chargement…")
                engine.load("vision")
                ia.build(index, engine, todo, self._frame_of,
                         progress=lambda d, t, s=short: self.mail.put(
                             ("progress", (d, t))) or self._bench_say(
                             f"Essai : {s}, {d} / {t} vidéos"),
                         stop=lambda: self._stop,
                         moments_of=lambda v: moments.get(v, []))
                if engine is not self.engine and engine is not self._engine_given:
                    engine.close(wait=True)
                if index is self.index:
                    self._release_engine()
            self.mail.put(("bench_ready", None))
            self._bench_say("Essai prêt : écrivez vos recherches et cliquez « Comparer »."
                            if not self._stop else "Essai arrêté : « Préparer l'essai » reprend.")
        self._run(work, indexing=True, label="Préparation de l'essai")

    def _bench_common(self, indexes: dict) -> set:
        common = None
        for index in indexes.values():
            have = index.videos()
            common = have if common is None else common & have
        return common or set()

    def _bench_state_text(self, _value=None) -> None:
        def work() -> None:
            indexes = {n: self._index_for(n) for n in self._bench_engines()}
            common = self._bench_common(indexes)
            self._bench_say(f"Essai : {len(common)} vidéos indexées par tous les moteurs.")
        threading.Thread(target=work, daemon=True, name="prisme-labo-essai").start()

    def _bench_run(self) -> None:
        queries = [q.strip() for q in self.bench_queries.toPlainText().splitlines() if q.strip()]
        if not queries:
            return self.bench_state.setText("Écrivez au moins une recherche, une par ligne.")
        if self._bench_running:
            self._refuse("La comparaison est déjà en cours : regardez la barre du bas.")
            return
        if self._busy and not self._indexing:
            # Un arret se termine : la comparaison partira juste apres.
            if self._bench_run not in self._after_idle:
                self._after_idle.append(self._bench_run)
            self.bench_state.setText("La comparaison partira dès que le travail en cours sera "
                                     "terminé.")
            self._refuse("Comparaison en attente : le travail en cours se termine.")
            return
        self._bench_keep()
        self._bench_running = True
        self.bench_state.setText("Comparaison en cours… (la barre du bas dit où elle en est)")
        self._set_task("bench", "Comparaison des moteurs")

        def work() -> None:
            indexes = {n: self._index_for(n) for n in self._bench_engines()}
            indexes = {n: i for n, i in indexes.items() if i.size}
            common = self._bench_common(indexes)
            if not common or len(indexes) < 2 and self._engine_given is None:
                said = ("Rien à comparer : il faut des vidéos indexées par au moins deux "
                        "moteurs. Cliquez « Préparer l'essai ».")
                self._bench_say(said)
                self.mail.put(("refuse", said))
                return
            if len(common) < 200 and self._engine_given is None:
                self._bench_say(f"Attention : seulement {len(common)} vidéos indexées par tous "
                                "les moteurs. Pour un essai juste, « Préparer l'essai » en "
                                "indexe davantage, aux mêmes instants.")
            results: dict = {q: {} for q in queries}
            for name, index in indexes.items():
                engine = self._engine_for(name)
                short = ia.ENGINES.get(name, {}).get("short", name)
                self._bench_say(f"Comparaison : {short}…")
                self._task("bench", f"Comparaison : {short} ({list(indexes).index(name) + 1}/"
                                    f"{len(indexes)})")
                engine.load("text")
                for query in queries:
                    rows = ia.smart_search(index, engine, query, BENCH_TOP, only=common)
                    results[query][name] = [(v, ts) for v, ts, _s in rows]
                if engine is not self.engine and engine is not self._engine_given:
                    engine.close(wait=True)
            self.mail.put(("bench", results))
            self._bench_say(f"Comparaison faite sur {len(common)} vidéos communes. Choisissez "
                            "une recherche dans la liste et cochez ce qui est juste.")

        def guarded() -> None:
            try:
                work()
            except Exception as exc:                         # noqa: BLE001
                self._bench_say(f"Erreur : {exc}")
            finally:
                self._task("bench", None)
                self.mail.put(("bench_done", None))
        # A cote d'une indexation : la comparaison ne lit les index que tels
        # qu'ils sont, et la partie texte des gros moteurs reste sur le
        # processeur.
        threading.Thread(target=guarded, daemon=True, name="prisme-labo-comparer").start()

    def _tab_changed(self, at: int) -> None:
        """Sur l'essai, la place va aux resultats : les explications du haut
        se replient."""
        bench = self.tabs.widget(at) is getattr(self, "_bench_page", None)
        self.lead.setVisible(not bench)

    def _bench_results(self, results: dict) -> None:
        self._bench["results"].update(results)
        self.bench_pick.blockSignals(True)
        self.bench_pick.clear()
        for query in self._bench["results"]:
            self.bench_pick.addItem(query)
        self.bench_pick.blockSignals(False)
        first = next(iter(results), "")
        self.bench_pick.setCurrentIndex(max(0, self.bench_pick.findText(first)))
        self._bench_show()

    def _bench_show(self, *_args) -> None:
        query = self.bench_pick.currentText()
        found = self._bench["results"].get(query, {})
        right = self._bench["right"].get(query, set())
        self._bench_filling = True
        for name, view in self.bench_views.items():
            view.clear()
            for video, ts in found.get(name, []):
                minutes, seconds = divmod(int(ts), 60)
                item = QListWidgetItem(f"{Path(video).name}\n{minutes}:{seconds:02d}")
                item.setData(Qt.UserRole, video)
                item.setData(Qt.UserRole + 1, float(ts))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if video in right else Qt.Unchecked)
                item.setToolTip(video)
                path = self._cached_frame(video, ts)
                if path:
                    item.setIcon(QIcon(QPixmap(str(path))))
                view.addItem(item)
        self._bench_filling = False
        self._bench_scores()

    def _bench_mark(self, _name: str, item) -> None:
        """Une video cochee juste l'est dans toutes les colonnes."""
        if self._bench_filling:
            return
        query = self.bench_pick.currentText()
        video = item.data(Qt.UserRole)
        right = self._bench["right"].setdefault(query, set())
        if item.checkState() == Qt.Checked:
            right.add(video)
        else:
            right.discard(video)
        self._bench["judged"].add(query)
        self._bench_filling = True
        for view in self.bench_views.values():
            for at in range(view.count()):
                other = view.item(at)
                if other.data(Qt.UserRole) == video:
                    other.setCheckState(item.checkState())
        self._bench_filling = False
        self._bench_scores()
        self._bench_keep()

    def _bench_nothing(self) -> None:
        query = self.bench_pick.currentText()
        if query:
            self._bench["judged"].add(query)
            self._bench_scores()
            self._bench_keep()

    def _bench_scores(self) -> None:
        """La note de chaque moteur : pour cette recherche, et en tout."""
        query = self.bench_pick.currentText()
        totals: dict = {name: [] for name in self.bench_views}
        for asked, found in self._bench["results"].items():
            if asked not in self._bench["judged"]:
                continue
            right = self._bench["right"].get(asked, set())
            for name, rows in found.items():
                if rows:
                    totals.setdefault(name, []).append(
                        sum(1 for v, _t in rows if v in right) / len(rows))
        found = self._bench["results"].get(query, {})
        right = self._bench["right"].get(query, set())
        for name, head in self.bench_heads.items():
            rows = found.get(name, [])
            short = ia.ENGINES.get(name, {}).get("short", name)
            if rows:
                good = sum(1 for v, _t in rows if v in right)
                head.setText(f"{short} — {good} / {len(rows)} justes")
            else:
                head.setText(f"{short} — pas encore indexé pour l'essai")
        parts = [f"{ia.ENGINES.get(n, {}).get('short', n)} {sum(v) / len(v):.0%}"
                 for n, v in totals.items() if v]
        judged = len([q for q in self._bench["results"] if q in self._bench["judged"]])
        self.bench_total.setText(
            f"Sur {judged} recherche(s) jugée(s) : " + " · ".join(parts) if parts else "")

    def closeEvent(self, event) -> None:
        self._stop = True
        if self._process is not None:
            self._process.kill()
        if self.engine is not None and hasattr(self.engine, "close"):
            self.engine.close()             # le modele libere sa memoire
        super().closeEvent(event)
