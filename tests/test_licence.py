"""L'essai de 14 jours et les clés de licence.

Lancement : python tests/test_licence.py
Les clés ci-dessous ont été fabriquées par le code du site
(netlify/lib/licence.mjs), avec une paire jetée depuis : elles prouvent que
Prisme lit exactement ce que le site signe.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SANDBOX = os.path.join(tempfile.gettempdir(), "prisme-tests-licence")
shutil.rmtree(SANDBOX, ignore_errors=True)
os.environ["PRISME_SANDBOX"] = SANDBOX
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from videosorter import licence  # noqa: E402
from videosorter.config import Config  # noqa: E402

PUB = "MC4ZpJk1rPj3brdGHo8tdrhn0arwWVG/y/+Yjs4uOiI="
LIFE = ("PRISME1-eyJ2IjoxLCJwIjoibGlmZXRpbWUiLCJlIjoidGVzdEBleGVtcGxlLmZyIiwiYyI6"
        "IiIsImkiOjE3OTAwMDAwMDB9.izFEr1x8uOT8ZYd7PtBElItl2ov9IeuXyDnsb-zHGa4Mgkw"
        "D17qrZrVgs4n0GhWyD1s5nSCg0m_xD7i6XSkVBg")
SUB = ("PRISME1-eyJ2IjoxLCJwIjoieWVhcmx5IiwiZSI6ImFib0BleGVtcGxlLmZyIiwiYyI6ImN1"
       "c18xIiwicyI6InN1Yl8xIiwiaSI6MTc5MDAwMDAwMCwieCI6MTkwMDAwMDAwMH0.n2R1O0J7IE"
       "JZrCB7cMlnttTKM9x9hEvog1LQWnmdj0D97Nj8gkfWUJ-PIjY_ZtmsqtBu2GA6NesfF0NHfgIADw")
OLD = ("PRISME1-eyJ2IjoxLCJwIjoibW9udGhseSIsImUiOiJvbGRAZXhlbXBsZS5mciIsImMiOiJj"
       "dXNfMiIsInMiOiJzdWJfMiIsImkiOjE3MDAwMDAwMDAsIngiOjE3MDA2MDAwMDB9.S73DGt2K"
       "J5RhyKnxJknDcSO4PZ8ekiCXLookK_5gr5fgIcQTI79THMv0G64j2_EjNtOSbX3HDs_BtwTl1fNNCw")
SUB_END = 1900000000
DAY = licence.DAY

FAILS: list = []


def check(condition, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


def fresh() -> Config:
    path = Path(SANDBOX) / "config.json"
    for stale in (path, path.with_name(path.name + ".bak")):
        if stale.exists():
            stale.unlink()
    return Config()


def main() -> int:
    print("\n[0] Ed25519 : le vecteur de la norme (RFC 8032, test 1)")
    public = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8"
        "821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    check(licence.ed25519_verify(public, b"", signature), "la signature de la norme passe")
    check(not licence.ed25519_verify(public, b"x", signature),
          "le même, pour un autre message, non")

    print("\n[1] Les clés du site")
    check(licence.read_key(LIFE, PUB)["p"] == "lifetime", "la licence à vie est lue")
    check(licence.read_key(SUB, PUB)["x"] == SUB_END, "l'abonnement et sa date")
    spaced = LIFE[:30] + "\n  " + LIFE[30:90] + " " + LIFE[90:]
    check(licence.read_key(spaced, PUB) is not None,
          "un copier-coller coupé ou espacé passe")
    body, signature = LIFE[len("PRISME1-"):].split(".")
    forged = "PRISME1-" + body[:-2] + ("A" if body[-2] != "A" else "B") + body[-1] \
        + "." + signature
    check(licence.read_key(forged, PUB) is None, "une clé retouchée est refusée")
    check(licence.read_key("PRISME1-abc.def", PUB) is None, "du bruit est refusé")
    check(licence.read_key(LIFE, "") is None, "sans clé publique, rien n'est lu")
    other = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
    check(licence.read_key(LIFE, other) is None,
          "une clé signée par quelqu'un d'autre est refusée")

    print("\n[2] Sans clé publique : version de développement")
    cfg = fresh()
    state = licence.status(cfg, public_key="")
    check(state.kind == "dev" and state.open, "Prisme s'ouvre toujours")

    print("\n[3] L'essai")
    start = 1_800_000_000
    cfg = fresh()
    state = licence.status(cfg, start, PUB)
    check(state.kind == "trial" and state.days_left == 14, "14 jours au départ")
    check(cfg["trial_started"] == start, "le premier jour est noté")
    check(licence.status(cfg, start + 13.5 * DAY, PUB).days_left == 1,
          "le dernier jour compte pour un")
    state = licence.status(cfg, start + 14 * DAY + 60, PUB)
    check(state.kind == "expired" and not state.open, "le 15e jour, il faut une clé")
    check(licence.status(cfg, start + 2 * DAY, PUB).kind == "expired",
          "reculer l'horloge n'y change rien")

    print("\n[4] Activer une clé")
    ok, message = licence.activate(cfg, "n'importe quoi", start, PUB)
    check(not ok and "PRISME1-" in message, "une clé illisible est refusée, et dit pourquoi")
    ok, message = licence.activate(cfg, OLD, start, PUB)
    check(not ok and "expiré" in message, "une clé d'abonnement périmée est refusée")
    ok, message = licence.activate(cfg, LIFE, start, PUB)
    check(ok, "la licence à vie est acceptée")
    state = licence.status(cfg, start + 400 * DAY, PUB)
    check(state.kind == "licensed" and state.email == "test@exemple.fr",
          "et vaut pour toujours")
    check(Config()["licence_key"] == LIFE, "elle est gardée dans les réglages")

    print("\n[5] Un abonnement")
    cfg = fresh()
    licence.activate(cfg, SUB, start, PUB)
    check(licence.status(cfg, SUB_END - DAY, PUB).kind == "licensed",
          "valable jusqu'à sa date")
    state = licence.status(cfg, SUB_END + 1, PUB)
    check(state.kind == "expired" and "abonnement" in state.note,
          "au-delà, Prisme dit que l'abonnement a pris fin")

    print("\n[6] La prolongation")
    cfg = fresh()
    cfg["licence_key"] = SUB
    check(not licence.renew_due(cfg, SUB_END - 30 * DAY, PUB),
          "rien à demander un mois avant")
    check(licence.renew_due(cfg, SUB_END - 5 * DAY, PUB),
          "dans les dix derniers jours, on demande")
    cfg["licence_checked"] = SUB_END - 5 * DAY - 3600
    check(not licence.renew_due(cfg, SUB_END - 5 * DAY, PUB),
          "une fois par jour au plus")
    cfg["licence_key"] = LIFE
    check(not licence.renew_due(cfg, SUB_END - 5 * DAY, PUB),
          "une licence à vie ne se connecte jamais")

    answers = {"key": SUB}
    seen = []

    class Site(BaseHTTPRequestHandler):
        def do_POST(self):             # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            seen.append(json.loads(self.rfile.read(length)))
            data = json.dumps(answers).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_port}"
    answer = licence.ask_site(OLD, site)
    check(answer.get("key") == SUB, "le site répond une clé prolongée")
    check(seen and set(seen[-1]) == {"key"}, "seule la clé est envoyée")
    licence.PUBLIC_KEY = PUB
    cfg = fresh()
    cfg["licence_key"] = OLD
    check(licence.take_renewal(cfg, answer) == "" and cfg["licence_key"] == SUB,
          "la clé prolongée remplace l'ancienne")
    check("arrêté" in licence.take_renewal(cfg, {"status": "ended"}),
          "un abonnement arrêté se dit")
    check(cfg["licence_key"] == SUB, "sans effacer la clé encore valable")
    check(licence.take_renewal(cfg, {"key": "PRISME1-faux.faux"}) == ""
          and cfg["licence_key"] == SUB, "une réponse douteuse est ignorée")
    check("error" in licence.ask_site(OLD, "http://127.0.0.1:9"),
          "le site injoignable ne fait pas tomber Prisme")
    server.shutdown()

    print("\n[7] Dans Prisme")
    app = QApplication(sys.argv)
    from videosorter.licence_dialog import LicenceDialog, gate
    from videosorter.window import MainWindow

    cfg = fresh()
    cfg.save()
    window = MainWindow(Config())
    window.show()
    app.processEvents()
    check(not window.trial_badge.isHidden()
          and window.trial_badge.text() == "Essai · 14 jours",
          f"les jours d'essai en haut ({window.trial_badge.text()!r})")
    window.cfg["licence_key"] = LIFE
    window._show_trial()
    check(window.trial_badge.isHidden(), "rien avec une licence")
    window.close()
    app.processEvents()

    cfg = fresh()
    cfg["trial_started"] = time.time() - 20 * DAY
    cfg.save()
    QTimer.singleShot(200, lambda: QApplication.activeModalWidget()
                      and QApplication.activeModalWidget().reject())
    check(not gate(Config()), "essai fini : fermer le dialogue quitte Prisme")

    def paste_and_activate() -> None:
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, LicenceDialog):
            dialog.field.setPlainText(LIFE)
            dialog.activate()

    QTimer.singleShot(200, paste_and_activate)
    check(gate(Config()), "coller une bonne clé ouvre Prisme")
    check(licence.status(Config()).kind == "licensed", "et elle reste")

    print(f"\n{'ÉCHEC' if FAILS else 'Tout est bon'} ({len(FAILS)} échec(s))")
    for label in FAILS:
        print("  -", label)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
