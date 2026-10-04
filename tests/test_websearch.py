"""La recherche video sur le web, contre deux faux sites servis sur le PC.

Le premier a un formulaire de recherche et des resultats sur plusieurs pages
(20 videos par page, lien « suivant ») ; le second n'a pas de formulaire, mais
repond a l'adresse la plus courante (« /?k=mots »). Rien ne sort sur Internet.
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
                      os.path.join(tempfile.gettempdir(), "prisme-tests-websearch"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from videosorter import websearch  # noqa: E402
from videosorter.websearch import (  # noqa: E402
    SearchFilters, form_template, parse_duration_text, parse_height_text,
    parse_iso8601_duration, run_search, split_lines, web_url,
)

FAILS: list = []


def check(condition: bool, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILS.append(label)


PER_PAGE, PAGES = 20, 3


def card(number: int, word: str, page: int = 1) -> str:
    # Une video sur deux dure plus de dix minutes ; une sur trois est en HD.
    minutes = 14 if number % 2 else 4
    quality = "<span class='hd'>1080p</span>" if number % 3 == 0 else ""
    return (f"<div class='thumb-block'><a href='/video/{1000 + number}/{word}-{number}?from=p{page}'>"
            f"<img data-src='/t/{number}.jpg' src='data:,' alt='{word} numéro {number}'></a>"
            f"<span class='duration'>{minutes}:05</span>{quality}"
            f"<p><a href='/video/{1000 + number}/{word}-{number}'>{word} numéro {number}</a></p></div>")


class TubeA(http.server.BaseHTTPRequestHandler):
    """Formulaire en page d'accueil, resultats pagines."""

    def log_message(self, *_args):
        pass

    def _html(self, body: str) -> None:
        data = f"<!doctype html><html><body>{body}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/":
            return self._html("<header><form action='/search' method='get'>"
                              "<input type='search' name='q'><button>OK</button>"
                              "</form></header><a href='/tags/plage'>Plage</a>")
        if url.path == "/search":
            query = parse_qs(url.query)
            word = query.get("q", [""])[0].split()[0]
            page = int(query.get("page", ["1"])[0])
            if page > PAGES:
                return self._html("<p>Aucun résultat</p>")
            start = (page - 1) * PER_PAGE
            cards = "".join(card(start + i, word, page) for i in range(PER_PAGE))
            # La meme video « a la une » sur chaque page, sous une adresse de
            # suivi differente : elle revenait autant de fois qu'il y a de pages.
            cards += (f"<div><a href='/video/9999/{word}-a-la-une?ref=top{page}'>"
                      f"<img src='/t/top.jpg' alt='{word} à la une'></a>"
                      f"<span>9:59</span></div>")
            following = (f"<a rel='next' href='/search?q={word}&page={page + 1}'>Suivant</a>"
                         if page < PAGES else "")
            return self._html(f"<div class='videos'>{cards}</div>{following}")
        self.send_response(404)
        self.end_headers()


class TubeC(TubeA):
    """Repond a toute recherche par les memes videos du jour."""

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/":
            return self._html("<form action='/find'><input type='search' name='q'></form>")
        if url.path == "/find":
            cards = "".join(card(700 + i, "populaire") for i in range(10))
            return self._html(f"<div>{cards}</div>")
        self.send_response(404)
        self.end_headers()


class TubeB(TubeA):
    """Pas de formulaire : la recherche est a « /?k=mots »."""

    def do_GET(self):  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path == "/" and "k" in query:
            word = query["k"][0].split()[0]
            cards = "".join(card(500 + i, word) for i in range(8))
            return self._html(f"<div>{cards}</div>")
        if url.path == "/":
            return self._html("<p>Bienvenue</p>")
        self.send_response(404)
        self.end_headers()


def serve(handler) -> str:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"127.0.0.1:{server.server_address[1]}"


def search(**kw) -> tuple:
    found, said = [], []
    filters = SearchFilters(**kw)
    templates = run_search(filters, on_status=said.append, on_result=found.append)
    return found, said, templates


def main() -> int:
    websearch.DOMAIN_DELAY = 0.0          # pas de politesse envers soi-meme
    site_a, site_b = serve(TubeA), serve(TubeB)
    # Les faux sites parlent http, pas https.
    real_root = websearch.site_root
    websearch.site_root = lambda line: real_root(line).replace("https://", "http://")

    print("\n[1] Lire ce qu'affiche une vignette")
    check(parse_duration_text("12:34") == 754, "« 12:34 » : 12 min 34 s")
    check(parse_duration_text("1:02:03") == 3723, "« 1:02:03 » : une heure")
    check(parse_duration_text("14 min") == 840, "« 14 min »")
    check(parse_height_text("HD") == 720 and parse_height_text("1080p") == 1080
          and parse_height_text("4K") == 2160, "HD, 1080p, 4K")
    check(parse_iso8601_duration("PT5M30S") == 330, "durée ISO 8601 (JSON-LD)")
    check(split_lines(" a.com\n\nb.com\na.com ") == ["a.com", "b.com"],
          "une ligne par site, sans vide ni doublon")
    check(web_url("https://x.test/", "file://hote/partage/a.jpg") == "",
          "une adresse file:// ne sort jamais d'ici")
    check(form_template("<form action='/s'><input type='search' name='q'></form>",
                        "https://x.test/") == "https://x.test/s?q={q}",
          "le formulaire de recherche donne l'adresse")

    print("\n[2] Un site avec formulaire, sur plusieurs pages")
    found, said, templates = search(queries=["plage"], sites=[site_a], max_per_site=1000)
    check(len(found) == PER_PAGE * PAGES + 1,
          f"toutes les pages sont lues, et la vidéo « à la une » n'est comptée "
          f"qu'une fois : {len(found)} vidéos ({said[-2:]})")
    check(templates.get(site_a, "").endswith("/search?q={q}"),
          f"sa recherche est trouvée et retenue ({templates})")
    first = found[0]
    check(first.thumbnail_url.endswith("/t/0.jpg") and first.duration_s == 245
          and "plage" in first.title, "titre, vignette et durée de chaque vidéo")

    print("\n[3] Les filtres")
    found, said, _t = search(queries=["plage"], sites=[site_a], min_duration_s=600,
                             max_per_site=1000)
    found = [v for v in found if "une" not in v.title]
    check(len(found) == PER_PAGE * PAGES // 2 and all(v.duration_s >= 600 for v in found),
          f"durée minimum 10 min : la moitié passe ({len(found)})")
    found, said, _t = search(queries=["plage"], sites=[site_a], min_height=1080,
                             max_per_site=1000)
    check(len(found) == PER_PAGE * PAGES // 3, f"1080p minimum : un tiers ({len(found)})")
    found, said, _t = search(queries=["plage"], sites=[site_a, site_b], max_per_site=25)
    per_site = {}
    for v in found:
        per_site[v.source_domain] = per_site.get(v.source_domain, 0) + 1
    check(per_site.get(site_a) == 25 and per_site.get(site_b) == 8,
          f"chaque site a sa part, le premier ne prend pas tout ({per_site})")

    print("\n[4] Un site sans formulaire, et plusieurs recherches")
    found, said, templates = search(queries=["plage", "montagne"],
                                    sites=[site_a, site_b], max_per_site=1000)
    by_site = {v.source_domain for v in found}
    check(site_b in by_site and templates.get(site_b, "").endswith("/?k={q}"),
          f"l'adresse courante « /?k= » est essayée et trouvée ({templates.get(site_b)})")
    words = {v.query for v in found}
    check(words == {"plage", "montagne"},
          f"les deux recherches se cumulent ({len(found)} vidéos)")
    check(len({v.page_url for v in found}) == len(found), "sans doublon")

    print("\n[5] Un site qui ne comprend pas la recherche")
    site_c = serve(TubeC)
    found, said, templates = search(queries=["plage"], sites=[site_c], max_per_site=1000)
    check(not found and any("sans rapport" in line or "aucune recherche" in line
                            for line in said),
          f"ses vidéos du jour ne passent pas pour des résultats ({said[-2:]})")

    print("\n[6] Arrêter")
    import threading as _threading
    stop = _threading.Event()
    found, said = [], []
    runner = _threading.Thread(target=lambda: run_search(
        SearchFilters(queries=["plage"], sites=[site_a, site_b], max_per_site=1000),
        should_stop=stop.is_set, on_status=said.append, on_result=found.append))
    websearch.DOMAIN_DELAY = 0.5
    runner.start()
    import time as _time
    _time.sleep(0.8)
    stop.set()
    runner.join(10)
    if runner.is_alive():                 # ou elle reste prise : on le montre
        import faulthandler
        faulthandler.dump_traceback(all_threads=True)
    websearch.DOMAIN_DELAY = 0.0
    check(not runner.is_alive() and "Recherche arrêtée." in said,
          f"la recherche s'arrête vite ({len(found)} vidéos avant l'arrêt ; {said[-3:]} ; vivant={runner.is_alive()})")
    print("\ntout est vert" if not FAILS else f"\n{len(FAILS)} échec(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
