"""La fenetre « Réparer des vidéos abîmées » (repair.py fait le travail)."""
from __future__ import annotations

import queue
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton,
    QRadioButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from . import repair, resync

STYLE = """
QDialog { background: #0e1116; }
QLabel { color: #c9d1db; }
QLabel#repHead { color: #ffffff; font-size: 18px; font-weight: 700; }
QLabel#repLead { color: #aab4c0; font-size: 12px; }
QTreeWidget { background: #0b0e12; border: 1px solid #242b35; border-radius: 8px;
              color: #e6e8ea; font-size: 13px; }
QTreeWidget::item { padding: 5px 4px; }
QTreeWidget::item:selected { background: #1d2a40; color: #ffffff; }
QHeaderView::section { background: #141922; color: #aab4c0; border: 0; padding: 5px; }
QRadioButton { color: #dfe6ee; padding: 2px 0; background: transparent; }
QRadioButton::indicator { width: 12px; height: 12px; border-radius: 8px;
                          border: 2px solid #5c6573; background: #0b0e12; }
QRadioButton::indicator:checked { border: 2px solid #2f6fed; background: #2f6fed; }
QLabel#repHint { color: #8b94a1; font-size: 12px; padding-left: 22px; }
QPushButton { background: #232a34; border: 1px solid #364050; border-radius: 6px;
              padding: 7px 14px; color: #eef1f4; }
QPushButton:hover { background: #2c3541; }
QPushButton:disabled { color: #6f7a87; background: #1a1f27; border-color: #262d37; }
QPushButton#repPrimary { background: #2f6fed; border-color: #2f6fed; font-weight: 600; }
QPushButton#repPrimary:disabled { background: #1a1f27; border-color: #262d37; color: #6f7a87; }
QProgressBar { background: #161b22; border: 1px solid #2a313b; border-radius: 4px;
               color: #dfe6ee; text-align: center; height: 16px; }
QProgressBar::chunk { background: #2f6fed; border-radius: 3px; }
"""

def _log(path: str, text: str) -> None:
    """Chaque echec, avec son message, dans reparation.log : la ligne de la
    fenetre se lit mal et disparait avec elle."""
    try:
        import time
        from .config import APP_DIR
        with open(APP_DIR / "reparation.log", "a", encoding="utf-8") as out:
            out.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {path}\n    {text}\n")
    except OSError:
        pass


METHODS = (
    ("rebuild", "Reconstruire les images — recommandé, la vraie réparation",
     "Souvent, les images ne sont pas perdues : des octets parasites les décalent et le "
     "lecteur les lit de travers. Prisme les retrouve dans le fichier et les remet en "
     "place — ce sont les images d'origine, sans perte, en quelques secondes. Si le "
     "fichier n'a pas ce défaut, Prisme le dit : « Figer » reste alors possible."),
    ("freeze", "Figer les passages abîmés",
     "Sur chaque passage abîmé, la dernière image saine reste à l'écran ; le son "
     "continue, la durée ne change pas. La vidéo est réencodée (quelques minutes)."),
    ("cut", "Couper les passages abîmés — instantané, sans perte",
     "Les passages abîmés sont retirés, tout le reste est copié tel quel : très "
     "rapide, aucune perte de qualité, mais la vidéo saute à chaque dégât."),
    ("conceal", "Masquer — rien ne saute, des traces peuvent rester",
     "Le décodeur remplit les blocs perdus avec le mouvement des images voisines. "
     "Utile pour de petits dégâts ; une zone peut rester floue."),
    ("remux", "Recoller l'enveloppe — la vidéo ne s'ouvre pas, ou sa durée est fausse",
     "L'index du fichier est refait, les images copiées telles quelles. Ne retire "
     "pas les pavés de couleur."),
)


class RepairDialog(QDialog):
    """Une ligne par video : verifiee, puis reparee a la demande."""

    COL_NAME, COL_STATE, COL_RESULT = range(3)

    def __init__(self, window, paths: list):
        super().__init__(window)
        self.window = window
        self.paths = [str(p) for p in paths]
        self.reports: dict = {}
        self.repaired: dict = {}           # original -> fichier repare
        self.methods_used: dict = {}       # original -> methode employee
        self.compare = None
        self.mail: queue.Queue = queue.Queue()
        self._stop = False
        self._busy = False
        self.progress_state = (0, 0, 0.0, "")   # fait, total, fraction, quoi
        self.setWindowTitle("Réparer des vidéos abîmées")
        self.setStyleSheet(STYLE)
        self.resize(900, 600)
        box = QVBoxLayout(self)
        box.setContentsMargins(18, 16, 18, 14)
        box.setSpacing(10)
        head = QLabel("Réparer des vidéos abîmées", self)
        head.setObjectName("repHead")
        box.addWidget(head)
        lead = QLabel(
            "Les pavés de couleur et la mosaïque viennent d'un fichier abîmé. Souvent, "
            "les images sont toujours là, simplement décalées : « Reconstruire » les "
            "remet en place. Ce qui est vraiment perdu peut être figé, coupé ou masqué. "
            "Chaque vidéo est d'abord vérifiée en entier. L'original "
            "n'est jamais modifié : la réparation est un nouveau fichier, à côté.", self)
        lead.setObjectName("repLead")
        lead.setWordWrap(True)
        box.addWidget(lead)

        self.table = QTreeWidget(self)
        self.table.setRootIsDecorated(False)
        self.table.setHeaderLabels(["Vidéo", "État", "Réparation"])
        self.table.header().setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
        self.table.header().setSectionResizeMode(self.COL_STATE, QHeaderView.ResizeToContents)
        self.table.header().setSectionResizeMode(self.COL_RESULT, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._refresh_buttons)
        self.table.itemDoubleClicked.connect(lambda *_a: self._watch())
        self.rows = {}
        for path in self.paths:
            row = QTreeWidgetItem([Path(path).name, "en attente", ""])
            row.setToolTip(self.COL_NAME, path)
            self.table.addTopLevelItem(row)
            self.rows[path] = row
        box.addWidget(self.table, 1)

        self.methods = QButtonGroup(self)
        for at, (key, text, tip) in enumerate(METHODS):
            radio = QRadioButton(text, self)
            radio.setToolTip(tip)
            radio.setProperty("method", key)
            self.methods.addButton(radio, at)
            box.addWidget(radio)
        self.methods.button(0).setChecked(True)
        # Ce que fait la methode choisie, en une phrase, sous les choix.
        self.hint = QLabel(METHODS[0][2], self)
        self.hint.setObjectName("repHint")
        self.hint.setWordWrap(True)
        box.addWidget(self.hint)
        self.methods.buttonToggled.connect(lambda *_a: self._refresh_buttons())
        # Un clic sur une methode : on ne la change plus d'office.
        self._chose = False
        self.methods.buttonClicked.connect(lambda *_a: setattr(self, "_chose", True))

        self.bar = QProgressBar(self)
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFormat("")
        box.addWidget(self.bar)

        row = QHBoxLayout()
        self.go = QPushButton("Réparer", self)
        self.go.setObjectName("repPrimary")
        self.go.clicked.connect(self._repair)
        row.addWidget(self.go)
        self.watch = QPushButton("Regarder avant / après", self)
        self.watch.setToolTip("L'originale et la réparée côte à côte, d'un passage abîmé "
                              "à l'autre (double-clic sur une ligne)")
        self.watch.clicked.connect(self._watch)
        row.addWidget(self.watch)
        self.replace = QPushButton("Remplacer les originales", self)
        self.replace.setToolTip("Les originales partent dans la corbeille de séance (Ctrl+Z, "
                                "ou la corbeille, les reprend) ; les réparées prennent "
                                "leur nom")
        self.replace.clicked.connect(self._replace)
        row.addWidget(self.replace)
        row.addStretch(1)
        self.halt = QPushButton("Arrêter", self)
        self.halt.clicked.connect(self._halt)
        row.addWidget(self.halt)
        close = QPushButton("Fermer", self)
        close.clicked.connect(self.close)
        row.addWidget(close)
        box.addLayout(row)

        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self._read_mail)
        self.timer.start()
        self._refresh_buttons()
        self._start(self._check_all, "vérification")

    # -- le travail, hors du fil de l'interface ----------------------------
    @property
    def busy(self) -> bool:
        return self._busy

    def _start(self, work, label: str) -> None:
        self._busy = True
        self._stop = False
        self._label = label
        self._refresh_buttons()
        threading.Thread(target=self._guard, args=(work,), daemon=True,
                         name="prisme-reparation").start()

    def _guard(self, work) -> None:
        try:
            work()
        except Exception as exc:                            # noqa: BLE001
            self.mail.put(("error", f"{type(exc).__name__} : {exc}"))
        self.mail.put(("done", None))

    def _check_all(self) -> None:
        todo = [p for p in self.paths if p not in self.reports]
        for number, path in enumerate(todo):
            if self._stop:
                return
            self.mail.put(("state", (path, "vérification…")))

            def progress(fraction, n=number, total=len(todo), p=path):
                self.mail.put(("progress", (n, total, fraction, Path(p).name)))
            try:
                report = repair.diagnose(path, progress, lambda: self._stop)
            except Exception as exc:                        # noqa: BLE001
                self.mail.put(("state", (path, f"illisible : {exc}")))
                continue
            if self._stop:
                self.mail.put(("state", (path, "arrêtée")))
                return
            if report["errors"]:
                # Des images decalees (recuperables) ou des donnees effacees ?
                # On le dit avant de reparer : « Reconstruire » sur une video
                # qui n'a rien de decale finissait sur un message pris pour
                # un echec.
                try:
                    report["shifted"] = resync.analyse(path)["bad"]
                except Exception as exc:                    # noqa: BLE001
                    report["shifted"] = 0
                    _log(path, f"analyse : {type(exc).__name__} : {exc}")
            self.mail.put(("report", (path, report)))

    def _repair_all(self, method: str, paths: list) -> None:
        tools = {"freeze": repair.freeze, "cut": repair.cut_damage,
                 "conceal": repair.conceal}
        for number, path in enumerate(paths):
            if self._stop:
                return
            self.mail.put(("result", (path, "réparation…")))

            def progress(fraction, n=number, total=len(paths), p=path):
                self.mail.put(("progress", (n, total, fraction, Path(p).name)))
            note = ""
            try:
                if method == "rebuild":
                    target = repair.repaired_name(path, "rebuild")
                    stats = resync.rebuild(path, target, progress=progress,
                                           stop=lambda: self._stop)
                    if not stats["bad"]:
                        try:
                            Path(target).unlink()
                        except OSError:
                            pass
                        self.mail.put(("result", (path, "aucune image décalée ici : ces "
                                                        "dégâts sont des données effacées. "
                                                        "Choisissez « Figer » ou « Masquer »")))
                        self.mail.put(("suggest", None))
                        continue
                    note = (f"{stats['recovered']} image(s) retrouvée(s) sur "
                            f"{stats['bad']} illisibles")
                elif method == "remux":
                    target = repair.remux(path, progress=progress, stop=lambda: self._stop)
                else:
                    target = tools[method](path, self.reports[path], progress=progress,
                                           stop=lambda: self._stop)
                check = repair.diagnose(target, stop=lambda: self._stop)
            except Exception as exc:                        # noqa: BLE001
                _log(path, f"{method} : {type(exc).__name__} : {exc}")
                self.mail.put(("result", (path, f"échec : {exc} (détail dans "
                                                "reparation.log)")))
                continue
            self.mail.put(("repaired", (path, str(target), check, method, note)))

    # -- l'interface ---------------------------------------------------------
    def _read_mail(self) -> None:
        for _ in range(200):
            try:
                kind, value = self.mail.get_nowait()
            except queue.Empty:
                break
            if kind == "state":
                path, text = value
                self.rows[path].setText(self.COL_STATE, text)
            elif kind == "result":
                path, text = value
                self.rows[path].setText(self.COL_RESULT, text)
            elif kind == "progress":
                done, total, fraction, name = value
                whole = (done + fraction) / max(1, total)
                self.progress_state = (done, total, whole, name)
                self.bar.setValue(int(whole * 1000))
                self.bar.setFormat(f"{self._label} {done + 1} / {total} — {name} — "
                                   f"{int(fraction * 100)} %")
            elif kind == "report":
                path, report = value
                self.reports[path] = report
                row = self.rows[path]
                damaged = bool(report["errors"])
                text = ("⚠ " if damaged else "✓ ") + repair.summary(report)
                if damaged:
                    shifted = report.get("shifted", 0)
                    text += (f" — {shifted} image(s) décalée(s), récupérables" if shifted
                             else " — données effacées : à figer ou masquer")
                row.setText(self.COL_STATE, text)
                self._suggest()
                row.setToolTip(self.COL_STATE, "\n".join(report.get("messages", [])[:6])
                               or "Le décodeur n'a rien signalé.")
            elif kind == "repaired":
                path, target, check, method, note = value
                self.repaired[path] = target
                self.methods_used[path] = method
                row = self.rows[path]
                if not self.table.selectedItems():
                    # « Regarder » vise la ligne choisie : la premiere reparee
                    # l'est d'office.
                    row.setSelected(True)
                    self.table.setCurrentItem(row)
                clean = not check["errors"]
                if note:
                    # Apres reconstruction, il ne reste en general que de
                    # petites traces : les dater en « passages » de plusieurs
                    # secondes faisait croire a un echec.
                    rest = ("propre" if clean else
                            f"quelques traces restent ({len(check['errors'])} erreur(s) "
                            "du décodeur)")
                    row.setText(self.COL_RESULT, ("✓ " if clean else "◐ ")
                                + f"{note} — {rest}")
                else:
                    row.setText(self.COL_RESULT, ("✓ " if clean else "◐ ")
                                + f"{Path(target).name} — "
                                + ("propre" if clean else repair.summary(check)))
                row.setToolTip(self.COL_RESULT, target)
            elif kind == "suggest":
                self._suggest(force=True)
            elif kind == "error":
                self.window.show_banner(f"Réparation : {value}", "error")
            elif kind == "done":
                self._busy = False
                self.bar.setFormat("Terminé" if not self._stop else "Arrêté")
                self.bar.setValue(1000 if not self._stop else self.bar.value())
                self._refresh_buttons()
        self._refresh_buttons()

    def _suggest(self, force: bool = False) -> None:
        """La methode qui convient : Reconstruire s'il y a des images
        decalees, sinon Figer. Un choix fait a la main reste."""
        damaged = [r for r in self.reports.values() if r.get("errors")]
        if not damaged or (self._chose and not force):
            return
        wanted = "rebuild" if any(r.get("shifted") for r in damaged) else "freeze"
        if force:
            wanted = "freeze"
        for button in self.methods.buttons():
            if button.property("method") == wanted:
                self.methods.blockSignals(True)
                button.setChecked(True)
                self.methods.blockSignals(False)
        self._refresh_buttons()

    def _method(self) -> str:
        button = self.methods.checkedButton()
        return button.property("method") if button is not None else "freeze"

    def _targets(self) -> list:
        """Les videos a reparer : les selectionnees, sinon toutes les abimees
        (« recoller » : les selectionnees, sinon toutes)."""
        chosen = [p for p, row in self.rows.items() if row.isSelected()]
        method = self._method()
        # Sans selection, ce qui est deja repare n'est pas repropose.
        pool = chosen or [p for p in self.rows if p not in self.repaired]
        if method == "remux":
            return pool
        return [p for p in pool if self.reports.get(p, {}).get("errors")]

    def _refresh_buttons(self) -> None:
        method = self._method()
        for key, _text, tip in METHODS:
            if key == method and self.hint.text() != tip:
                self.hint.setText(tip)
        targets = self._targets()
        self.go.setEnabled(not self._busy and bool(targets))
        self.go.setText(f"Réparer ({len(targets)})" if targets else "Réparer")
        self.halt.setEnabled(self._busy)
        self.replace.setEnabled(not self._busy and bool(self.repaired))
        self.watch.setEnabled(bool(self._watch_target()))

    def _repair(self) -> None:
        targets = self._targets()
        if targets and not self._busy:
            self._start(lambda: self._repair_all(self._method(), targets), "réparation")

    def _halt(self) -> None:
        self._stop = True

    def _watch_target(self) -> str:
        """La ligne choisie, sinon la premiere reparee, sinon la premiere abimee."""
        rows = self.table.selectedItems()
        if rows:
            return next((p for p, row in self.rows.items() if row is rows[0]), "")
        if self.repaired:
            return next(iter(self.repaired))
        return next((p for p in self.rows if self.reports.get(p, {}).get("errors")), "")

    def _watch(self) -> None:
        path = self._watch_target()
        if not path:
            return
        from .repair_compare import RepairCompare
        if self.compare is not None:
            self.compare.close()
        repaired = self.repaired.get(path, "")
        self.compare = RepairCompare(
            self, path, repaired, self.reports.get(path),
            synced=self.methods_used.get(path) not in ("cut", "remux"))
        self.compare.show()
        self.compare.raise_()

    def _replace(self) -> None:
        pairs = [(p, t) for p, t in self.repaired.items()]
        if pairs:
            self.window.replace_with_repaired(pairs)
            for path, _target in pairs:
                self.rows[path].setText(self.COL_RESULT, "✓ remplacée (originale à la corbeille)")
            self.repaired = {}
            self._refresh_buttons()

    def closeEvent(self, event):
        self._stop = True
        self.timer.stop()
        super().closeEvent(event)
