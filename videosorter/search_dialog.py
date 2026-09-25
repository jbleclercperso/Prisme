"""Boite de dialogue de recherche video sur le web.

Cherche des videos sur des sites autres que les grandes plateformes, a partir
de mots-cles : une recherche Bing donne des sites de depart, puis chacun est
explore a la recherche de fiches video. Rien n'est telecharge — chaque
resultat reste un lien a ouvrir soi-meme dans le navigateur.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .websearch import SearchFilters, VideoResult, run_search

RESOLUTION_CHOICES = [
    ("Peu importe", 0), ("360p", 360), ("480p", 480), ("720p", 720),
    ("1080p", 1080), ("4K", 2160),
]

COL_THUMB, COL_TITLE, COL_DURATION, COL_RES, COL_SOURCE, COL_LINK = range(6)

SERPAPI_KEY_URL = "https://serpapi.com/users/sign_up"

# Au-dela, ce n'est plus une vignette : on coupe plutot que de tout lire.
MAX_THUMB_BYTES = 4 * 1024 * 1024


def web_address(url: str) -> QUrl | None:
    """L'adresse si elle mene au web (http, https), None sinon.

    Dernier rempart, au moment de charger ou d'ouvrir : un « file://hote/… »
    ferait ouvrir un partage de fichiers etranger, et presenter l'empreinte
    du compte Windows a qui le tient.
    """
    address = QUrl(str(url or ""))
    if address.scheme().lower() not in ("http", "https") or not address.host():
        return None
    return address


class _SearchWorker(QObject):
    """Fait tourner :func:`run_search` hors du fil de l'interface."""

    status = Signal(str)
    result = Signal(object)
    finished = Signal()

    def __init__(self, api_key: str, filters: SearchFilters):
        super().__init__()
        self.api_key = api_key
        self.filters = filters
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        run_search(
            self.api_key,
            self.filters,
            should_stop=lambda: self._stop,
            on_status=self.status.emit,
            on_result=self.result.emit,
        )
        self.finished.emit()


class WebSearchDialog(QDialog):
    """Recherche de videos sur le web, avec filtres et resultats en lien seul."""

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Recherche video sur le web")
        self.resize(880, 640)
        self._thread: QThread | None = None
        self._worker: _SearchWorker | None = None
        self._net = QNetworkAccessManager(self)
        self._thumb_replies: dict[object, int] = {}

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Cherche des videos sur des sites de niche (forums, sites "
            "communautaires…), pas sur les grandes plateformes. Les sites de "
            "confiance sont explores directement, sans quota ni limite ; "
            "SerpAPI ne sert qu'a decouvrir de nouveaux sites a partir de "
            "mots-cles (250 recherches gratuites par mois). Aucun "
            "telechargement automatique — chaque resultat reste un lien a "
            "ouvrir soi-meme.",
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        keywords_row = QHBoxLayout()
        keywords_row.addWidget(QLabel("Mots-cles :", self))
        self.keywords = QLineEdit(self)
        self.keywords.setPlaceholderText("ex. course de cote 1987 auvergne")
        self.keywords.returnPressed.connect(self.start_search)
        keywords_row.addWidget(self.keywords, 1)
        self.strict_keywords = QCheckBox("Tous les mots-cles (plus strict)", self)
        self.strict_keywords.setToolTip(
            "Coche : chaque mot doit apparaitre. Decoche : un seul suffit, "
            "au prix de resultats moins precis."
        )
        self.strict_keywords.setChecked(True)
        keywords_row.addWidget(self.strict_keywords)
        layout.addLayout(keywords_row)

        domains_row = QHBoxLayout()
        domains_row.addWidget(QLabel("Sites de confiance :", self))
        self.known_domains = QLineEdit(self)
        self.known_domains.setPlaceholderText(
            "ex. forum-exemple.net collection-exemple.org — explores directement, sans quota"
        )
        self.known_domains.textChanged.connect(self._on_domains_changed)
        domains_row.addWidget(self.known_domains, 1)
        self.discover_new_sites = QCheckBox("Decouvrir aussi de nouveaux sites (SerpAPI)", self)
        self.discover_new_sites.setToolTip(
            "Consomme le quota gratuit (250 recherches/mois). Les sites de "
            "confiance ci-contre, eux, n'en consomment jamais."
        )
        domains_row.addWidget(self.discover_new_sites)
        layout.addLayout(domains_row)

        filters_row = QHBoxLayout()
        filters_row.addWidget(QLabel("Duree min. (min) :", self))
        self.min_duration = QSpinBox(self)
        self.min_duration.setRange(0, 600)
        filters_row.addWidget(self.min_duration)

        filters_row.addWidget(QLabel("Resolution min. :", self))
        self.min_resolution = QComboBox(self)
        for label, _ in RESOLUTION_CHOICES:
            self.min_resolution.addItem(label)
        filters_row.addWidget(self.min_resolution)

        filters_row.addWidget(QLabel("Sites a explorer :", self))
        self.max_sites = QSpinBox(self)
        self.max_sites.setRange(1, 50)
        filters_row.addWidget(self.max_sites)

        filters_row.addWidget(QLabel("Resultats max. :", self))
        self.max_results = QSpinBox(self)
        self.max_results.setRange(1, 200)
        self.max_results.setToolTip(
            "La recherche s'arrete des que ce nombre de resultats est atteint : "
            "mieux vaut peu et pertinent que beaucoup et bruyant."
        )
        filters_row.addWidget(self.max_results)
        filters_row.addStretch(1)
        layout.addLayout(filters_row)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Cle API SerpAPI :", self))
        self.api_key = QLineEdit(self)
        # Masquee sauf pendant la saisie : une capture d'ecran ne l'emporte pas.
        self.api_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.api_key.setPlaceholderText("colle ici ta cle SerpAPI (gratuite, 250 recherches/mois)")
        key_row.addWidget(self.api_key, 1)
        howto = QLabel(f'<a href="{SERPAPI_KEY_URL}">Obtenir une cle gratuite</a>', self)
        howto.setOpenExternalLinks(True)
        key_row.addWidget(howto)
        layout.addLayout(key_row)

        buttons_row = QHBoxLayout()
        self.search_button = QPushButton("Rechercher", self)
        self.search_button.setObjectName("primary")
        self.search_button.clicked.connect(self.start_search)
        self.stop_button = QPushButton("Arreter", self)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_search)
        buttons_row.addWidget(self.search_button)
        buttons_row.addWidget(self.stop_button)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)

        splitter = QSplitter(Qt.Vertical, self)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            ["", "Titre", "Duree", "Resolution", "Source", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(64)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        self.table.setColumnWidth(COL_THUMB, 96)
        self.table.setColumnWidth(COL_LINK, 90)
        self.table.cellDoubleClicked.connect(self._open_row)
        splitter.addWidget(self.table)

        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setFixedHeight(110)
        splitter.addWidget(self.log)
        splitter.setStretchFactor(0, 1)
        layout.addWidget(splitter, 1)

        box = QDialogButtonBox(QDialogButtonBox.Close, self)
        box.button(QDialogButtonBox.Close).setText("Fermer")
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self._load_settings()

    # -- reglages persistes ----------------------------------------------
    def _on_domains_changed(self, text: str) -> None:
        """Sans domaine de confiance, SerpAPI est la seule source possible :
        la case se coche et se verrouille plutot que de risquer une recherche
        qui ne trouve aucun site."""
        if text.strip():
            self.discover_new_sites.setEnabled(True)
        else:
            self.discover_new_sites.setChecked(True)
            self.discover_new_sites.setEnabled(False)

    def _load_settings(self) -> None:
        self.api_key.setText(self.cfg.get("web_search_api_key", ""))
        self.min_duration.setValue(int(self.cfg.get("web_search_min_duration_min", 0) or 0))
        self.max_sites.setValue(int(self.cfg.get("web_search_max_sites", 15) or 15))
        self.max_results.setValue(int(self.cfg.get("web_search_max_results", 30) or 30))
        self.strict_keywords.setChecked(bool(self.cfg.get("web_search_strict_keywords", True)))
        self.known_domains.setText(self.cfg.get("web_search_known_domains", ""))
        if self.known_domains.text().strip():
            self.discover_new_sites.setChecked(bool(self.cfg.get("web_search_discover_new_sites", False)))
        # setText() n'emet textChanged que si la valeur change : un champ deja
        # vide au demarrage ne declencherait jamais la synchronisation.
        self._on_domains_changed(self.known_domains.text())
        want_height = int(self.cfg.get("web_search_min_height", 0) or 0)
        for index, (_, height) in enumerate(RESOLUTION_CHOICES):
            if height == want_height:
                self.min_resolution.setCurrentIndex(index)
                break

    def _save_settings(self) -> None:
        self.cfg["web_search_api_key"] = self.api_key.text().strip()
        self.cfg["web_search_min_duration_min"] = self.min_duration.value()
        self.cfg["web_search_max_sites"] = self.max_sites.value()
        self.cfg["web_search_max_results"] = self.max_results.value()
        self.cfg["web_search_strict_keywords"] = self.strict_keywords.isChecked()
        self.cfg["web_search_known_domains"] = self.known_domains.text().strip()
        self.cfg["web_search_discover_new_sites"] = self.discover_new_sites.isChecked()
        self.cfg["web_search_min_height"] = RESOLUTION_CHOICES[self.min_resolution.currentIndex()][1]
        self.cfg.save()

    # -- recherche ---------------------------------------------------------
    def start_search(self) -> None:
        if self._thread is not None:
            return
        keywords = self.keywords.text().strip()
        domains = self.known_domains.text().strip()
        if not keywords and not domains:
            self._log(
                "Indiquez des mots-cles, des sites de confiance, ou les deux."
            )
            return
        self._save_settings()
        self.table.setRowCount(0)
        self.log.clear()
        if self.discover_new_sites.isChecked() and not keywords:
            self._log(
                "« Decouvrir de nouveaux sites » est coche mais aucun mot-cle "
                "n'est indique : cette partie sera ignoree."
            )
        filters = SearchFilters(
            keywords=keywords,
            min_duration_s=self.min_duration.value() * 60,
            min_height=RESOLUTION_CHOICES[self.min_resolution.currentIndex()][1],
            max_sites=self.max_sites.value(),
            max_total_results=self.max_results.value(),
            require_all_keywords=self.strict_keywords.isChecked(),
            known_domains=domains,
            discover_new_sites=self.discover_new_sites.isChecked(),
        )
        self._worker = _SearchWorker(self.api_key.text().strip(), filters)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.status.connect(self._log)
        self._worker.result.connect(self._add_result)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()
        self.search_button.setEnabled(False)
        self.stop_button.setEnabled(True)

    def stop_search(self) -> None:
        if self._worker:
            self._worker.stop()
            self._log("Arret demande…")

    def _on_finished(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None
        self.search_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _add_result(self, video: VideoResult) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, COL_THUMB, QTableWidgetItem())
        title_item = QTableWidgetItem(video.title)
        title_item.setToolTip(video.snippet or video.title)
        title_item.setData(Qt.UserRole, video.page_url)
        self.table.setItem(row, COL_TITLE, title_item)
        self.table.setItem(row, COL_DURATION, QTableWidgetItem(video.duration_label))
        self.table.setItem(row, COL_RES, QTableWidgetItem(video.resolution_label))
        self.table.setItem(row, COL_SOURCE, QTableWidgetItem(video.source_domain))
        open_button = QPushButton("Ouvrir", self.table)
        open_button.setFocusPolicy(Qt.NoFocus)
        open_button.clicked.connect(lambda _checked=False, url=video.page_url: self._open_url(url))
        self.table.setCellWidget(row, COL_LINK, open_button)
        if video.thumbnail_url:
            self._fetch_thumbnail(row, video.thumbnail_url)

    def _fetch_thumbnail(self, row: int, url: str) -> None:
        address = web_address(url)
        if address is None:
            return
        request = QNetworkRequest(address)
        # Une redirection ne mene que vers http ou https, jamais vers un
        # partage de fichiers.
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                             QNetworkRequest.NoLessSafeRedirectPolicy)
        reply = self._net.get(request)
        self._thumb_replies[reply] = row
        reply.downloadProgress.connect(
            lambda got, _total: self._cap_thumbnail(reply, got))
        reply.finished.connect(lambda: self._on_thumbnail(reply))

    @staticmethod
    def _cap_thumbnail(reply, got: int) -> None:
        if got > MAX_THUMB_BYTES:
            reply.abort()

    def _on_thumbnail(self, reply) -> None:
        row = self._thumb_replies.pop(reply, None)
        reply.deleteLater()
        if row is None or row >= self.table.rowCount():
            return
        if reply.error() != QNetworkReply.NoError:
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(bytes(reply.readAll())):
            item = self.table.item(row, COL_THUMB)
            if item:
                item.setData(Qt.DecorationRole, pixmap.scaledToWidth(88, Qt.SmoothTransformation))

    def _open_row(self, row: int, _column: int) -> None:
        item = self.table.item(row, COL_TITLE)
        if item:
            self._open_url(item.data(Qt.UserRole))

    def _open_url(self, url: str) -> None:
        address = web_address(url)
        if address is not None:
            QDesktopServices.openUrl(address)

    def _stop_thread(self) -> None:
        if self._worker:
            self._worker.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait(2000)
        self._thread = None
        self._worker = None

    def closeEvent(self, event) -> None:
        self._stop_thread()
        super().closeEvent(event)

    def reject(self) -> None:
        self._stop_thread()
        super().reject()
