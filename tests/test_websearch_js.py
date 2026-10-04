"""La recherche sur un site tout en JavaScript, par le navigateur invisible.

Court : un faux site en local, sans formulaire -- sa case de recherche ne
marche qu'en JavaScript, et ses resultats n'existent qu'une fois son script
passe. Lu comme un texte, il ne montre rien ; le navigateur invisible y tape
le mot, retient l'adresse de sa recherche, et en lit les resultats.
"""
from __future__ import annotations

import http.server
import os
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("PRISME_SANDBOX",
                      os.path.join(tempfile.gettempdir(), "prisme-tests-websearch-js"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QCoreApplication, Qt, QTimer  # noqa: E402

QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
from PySide6.QtWidgets import QApplication  # noqa: E402

from videosorter import websearch  # noqa: E402
from videosorter.websearch import SearchFilters, run_search  # noqa: E402

FAILS: list = []

HOME = """<html><head><title>Tube JS</title></head><body>
<header><input id="trouve" placeholder="Search videos"></header>
<main id="app"></main>
<script>
document.getElementById('trouve').addEventListener('keydown', e => {
  if (e.key === 'Enter') location.href = '/r/' + encodeURIComponent(e.target.value) + '/';
});
</script></body></html>"""

# La page de resultats : vide, remplie par son script une demi-seconde apres.
RESULTS = """<html><head><title>Resultats</title></head><body><main id="app"></main>
<script>
setTimeout(() => {
  const word = decodeURIComponent(location.pathname.split('/')[2]);
  let html = '';
  for (let i = 0; i < 12; i++) {
    html += '<div class="card"><a href="/watch/' + (1000 + i) + '">' +
      '<img src="/t/' + i + '.jpg"></a><a href="/watch/' + (1000 + i) + '">' +
      word + ' numero ' + i + '</a><span>12:0' + (i % 10) + '</span></div>';
  }
  document.getElementById('app').innerHTML = html;
}, 500);
</script></body></html>"""


def check(condition, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            return self._html(HOME)
        if path.startswith("/r/"):
            return self._html(RESULTS)
        self.send_response(404)
        self.end_headers()

    def _html(self, text):
        data = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    app = QApplication([])
    from videosorter.webpage import PageReader
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    site = f"http://127.0.0.1:{server.server_address[1]}"
    websearch.DOMAIN_DELAY = 0.0
    reader = PageReader(websearch.USER_AGENT)
    outcome: dict = {}

    def work():
        found, states = [], {}
        plain = run_search(SearchFilters(queries=["plage"], sites=[site]),
                           on_result=found.append,
                           on_site=lambda n, st, t: states.__setitem__(n, (st, t)))
        outcome["plain"] = (len(found), states.copy(), plain)
        found, states = [], {}
        templates = run_search(SearchFilters(queries=["plage"], sites=[site]),
                               on_result=found.append, renderer=reader,
                               on_site=lambda n, st, t: states.__setitem__(n, (st, t)))
        outcome["js"] = (found, states, templates)
        # La fois suivante : l'adresse retenue, plus besoin de taper.
        again = []
        said = {}
        run_search(SearchFilters(queries=["montagne"], sites=[site]), templates,
                   on_result=again.append, renderer=reader,
                   on_site=lambda n, st, t: (said.__setitem__(n, (st, t)),
                                             os.environ.get("TRACE") and print("   trace", st, t)),
                   on_status=lambda t: os.environ.get("TRACE") and print("   trace", t))
        outcome["again_said"] = said
        outcome["again"] = again
        finished.set()

    # Le fil de travail ne peut pas arreter l'application lui-meme (pas de
    # boucle Qt de son cote) : le fil de l'interface guette la fin.
    finished = threading.Event()
    watch = QTimer()
    watch.timeout.connect(lambda: finished.is_set() and app.quit())
    watch.start(100)
    threading.Thread(target=work, daemon=True).start()
    QTimer.singleShot(120_000, app.quit)
    app.exec()
    reader.close()

    print("\n[1] Lu comme un texte")
    count, states, _t = outcome.get("plain", (0, {}, {}))
    check(count == 0 and all(s[0] == "erreur" for s in states.values()),
          f"le site ne montre rien, et le dit ({list(states.values())[:1]})")

    print("\n[2] Par le navigateur invisible")
    found, states, templates = outcome.get("js", ([], {}, {}))
    template = next(iter(templates.values()), "")
    check(template.startswith("js:") and template.endswith("/r/{q}/"),
          f"il tape le mot et retient l'adresse de la recherche ({template})")
    check(len(found) == 12 and all("plage" in v.title for v in found),
          f"les douze résultats, écrits par le script du site ({len(found)})")
    check(all(v.duration_s and v.duration_s >= 720 for v in found),
          "avec leur durée")
    again = outcome.get("again", [])
    check(len(again) == 12 and all("montagne" in v.title for v in again),
          f"la fois suivante, l'adresse retenue suffit ({len(again)} ; "
          f"{outcome.get('again_said')})")

    print("\ntout est vert" if not FAILS else f"\n{len(FAILS)} échec(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
