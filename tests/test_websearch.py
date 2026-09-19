"""Verifie l'extraction et les filtres du module de recherche web, hors ligne.

Lancement : python tests/test_websearch.py

Aucune requete reseau ici : on nourrit les fonctions d'extraction avec du HTML
fabrique a la main, comme le ferait une vraie page.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import videosorter.websearch as ws  # noqa: E402
from videosorter.websearch import (  # noqa: E402
    SearchFilters, build_query, extract_meta_videos, find_direct_video_links,
    find_links, parse_iso8601_duration,
)

FAILURES: list = []


def check(condition: bool, label: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'} {label}")
    if not condition:
        FAILURES.append(label)


JSONLD_PAGE = """
<html><head>
<script type="application/ld+json">
{"@type": "VideoObject", "name": "Course de cote 1987", "duration": "PT5M30S",
 "height": 720, "thumbnailUrl": "/img/thumb.jpg",
 "description": "Une vieille course de cote en Auvergne."}
</script>
</head><body></body></html>
"""

OG_PAGE = """
<html><head>
<meta property="og:title" content="Rallye regional 1992">
<meta property="og:video" content="https://exemple.test/video.mp4">
<meta property="og:video:height" content="1080">
<meta property="og:image" content="/img/rallye.jpg">
<meta property="og:description" content="Un rallye d'epoque.">
</head><body></body></html>
"""

LINKS_PAGE = """
<html><body>
<a href="/a-propos">A propos</a>
<a href="/categorie/course-de-cote">Course de cote</a>
<a href="/tag/rallye">#rallye</a>
<a href="https://autre-domaine.test/x">Externe</a>
</body></html>
"""

DIRECT_VIDEO_PAGE = """
<html><body>
<a href="/videos/finale.mp4">Finale 1987</a>
<video src="/videos/entrainement.webm" poster="/img/poster.jpg"></video>
</body></html>
"""


def main() -> int:
    print("\n[1] JSON-LD VideoObject")
    videos = extract_meta_videos(JSONLD_PAGE, "https://exemple.test/fiche")
    check(len(videos) == 1, "une video extraite")
    if videos:
        v = videos[0]
        check(v.title == "Course de cote 1987", "titre lu")
        check(v.duration_s == 330, "duree ISO8601 convertie (5 min 30)")
        check(v.height == 720, "hauteur lue")
        check(v.thumbnail_url == "https://exemple.test/img/thumb.jpg", "miniature resolue en absolu")
        check(v.resolution_label == "720p", "etiquette de resolution")
        check(v.duration_label == "5:30", "etiquette de duree")

    print("\n[2] Repli sur Open Graph")
    videos = extract_meta_videos(OG_PAGE, "https://exemple.test/fiche2")
    check(len(videos) == 1, "une video extraite via og:")
    if videos:
        check(videos[0].title == "Rallye regional 1992", "titre og:title")
        check(videos[0].height == 1080, "hauteur og:video:height")

    print("\n[3] Liens directs vers des fichiers video")
    videos = find_direct_video_links(DIRECT_VIDEO_PAGE, "https://exemple.test/liste")
    check(len(videos) == 2, "deux videos directes trouvees")
    titles = {v.title for v in videos}
    check("Finale 1987" in titles, "titre du lien <a>")
    check(any(v.thumbnail_url.endswith("poster.jpg") for v in videos), "poster de la balise <video>")

    print("\n[4] Tri des liens internes (categories en tete)")
    links = find_links(LINKS_PAGE, "https://exemple.test/")
    check(all("autre-domaine.test" not in link for link in links), "liens externes ecartes")
    check(links and "categorie" in links[0], "un lien-categorie passe devant")

    print("\n[5] Filtres de recherche")
    filters = SearchFilters(keywords="course cote", min_duration_s=300, min_height=480)
    check(filters.matches_text("Course de cote 1987", ""), "mot-cle trouve dans le titre")
    check(not filters.matches_text("Vacances a la mer", ""), "mot-cle absent rejete")
    check(filters.matches_duration(330), "duree suffisante acceptee")
    check(not filters.matches_duration(60), "duree insuffisante rejetee")
    check(filters.matches_duration(None), "duree inconnue non rejetee")
    check(filters.matches_height(720), "resolution suffisante acceptee")
    check(not filters.matches_height(360), "resolution insuffisante rejetee")

    print("\n[6] Correspondance stricte (par defaut) vs souple")
    strict = SearchFilters(keywords="course cote auvergne")
    check(strict.require_all_keywords, "stricte par defaut")
    check(not strict.matches_text("Course de cote en Bretagne", ""), "un mot manquant rejete en strict")
    check(strict.matches_text("La course de cote d'Auvergne 1987", ""), "les trois mots presents acceptes")
    souple = SearchFilters(keywords="course cote auvergne", require_all_keywords=False)
    check(souple.matches_text("Course de cote en Bretagne", ""), "un seul mot suffit en souple")

    print("\n[7] Domaines de confiance")
    check(build_query("course de cote", []) == "course de cote", "requete inchangee sans domaine")
    query = build_query("course de cote", ["forum-exemple.net", "archives-exemple.org"])
    check("site:forum-exemple.net" in query and "site:archives-exemple.org" in query, "les deux domaines restreignent la requete")
    check(SearchFilters(known_domains="a.net, b.org  c.net").domain_list() == ["a.net", "b.org", "c.net"], "liste de domaines eclatee sur virgules/espaces")

    # Bug vecu : des URLs completes collees plutot que des domaines nus
    # produisaient toutes le meme "https:" une fois qu'on rajoutait un second
    # https:// par-dessus — 57 sites ecrases en une seule entree invalide.
    full_urls = "https://forum-exemple.net/videos https://archives-exemple.org/tag/x"
    domains = SearchFilters(known_domains=full_urls).domain_list()
    check(domains == ["forum-exemple.net", "archives-exemple.org"], "URLs completes ramenees a des domaines nus")
    check(all(build_query("x", [d]) and "https:" not in d for d in domains), "aucun domaine ne garde un schema")
    from urllib.parse import urlparse as _urlparse
    seed_netlocs = {_urlparse(f"https://{d}/").netloc for d in domains}
    check(len(seed_netlocs) == 2, "les sites de depart restent distincts (pas ecrases en un seul)")

    print("\n[8] Duree ISO 8601")
    check(parse_iso8601_duration("PT1H2M3S") == 3723, "heures + minutes + secondes")
    check(parse_iso8601_duration("PT45S") == 45, "secondes seules")
    check(parse_iso8601_duration("n'importe quoi") is None, "chaine invalide -> None")

    print("\n[9] Diagnostic d'exploration (CrawlStats)")
    blocked = ws.CrawlStats(blocked_robots=5)
    check("robots.txt" in blocked.summary(), "signale un blocage robots.txt")
    unreachable = ws.CrawlStats(fetch_failed=3)
    check("injoignable" in unreachable.summary(), "signale des pages injoignables")
    no_video = ws.CrawlStats(pages_read=4, candidates_found=0)
    check("JavaScript" in no_video.summary(), "signale des pages lues sans aucune video reconnue")
    filtered_out = ws.CrawlStats(pages_read=4, candidates_found=6, candidates_matched=0)
    check("passe les filtres" in filtered_out.summary(), "signale des videos vues mais filtrees")

    print("\n[10] run_search : sites de confiance vs decouverte SerpAPI")
    calls = []

    def fake_serpapi(api_key, query, max_results=15, domains=None):
        calls.append((query, tuple(domains or ())))
        return []

    class FakeCrawler:
        def __init__(self):
            self.last_stats = ws.CrawlStats()

        def explore(self, start_url, filters, should_stop=lambda: False):
            return iter([])

    real_serpapi, real_crawler = ws.serpapi_search, ws.SiteCrawler
    ws.serpapi_search, ws.SiteCrawler = fake_serpapi, FakeCrawler
    try:
        statuses = []
        only_domains = SearchFilters(known_domains="exemple.net", discover_new_sites=False)
        ws.run_search("cle", only_domains, on_status=statuses.append)
        check(not calls, "site de confiance seul : SerpAPI jamais appele")
        check(any("exemple.net" in s for s in statuses), "le statut mentionne le site direct")

        calls.clear()
        only_keywords = SearchFilters(keywords="course cote", known_domains="")
        ws.run_search("cle", only_keywords, on_status=lambda s: None)
        check(len(calls) == 1, "sans site connu : SerpAPI appele une seule fois")
    finally:
        ws.serpapi_search, ws.SiteCrawler = real_serpapi, real_crawler

    print(f"\n{'TOUT PASSE' if not FAILURES else f'{len(FAILURES)} ECHEC(S)'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
