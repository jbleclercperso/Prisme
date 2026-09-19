"""Recherche video sur le web : moteur de recherche + exploration de sites.

L'idee n'est pas de « scanner tout Internet » — personne ne reproduit un moteur
de recherche a l'echelle d'une appli perso. On s'appuie a la place sur un
moteur de recherche officiel (Bing Web Search API, gratuite jusqu'a un certain
quota) pour obtenir des pages de depart a partir des mots-cles, puis on
explore chacun de ces sites — accueil, categories, fiches — a la recherche de
videos qui correspondent aux filtres (mots-cles, duree, resolution).

Regles de politesse, non negociables :
- le fichier robots.txt de chaque site est respecte ;
- aucun contournement de CAPTCHA ni de detection anti-robot : un site qui
  bloque le robot est simplement abandonne, pas force ;
- un delai separe deux requetes vers le meme domaine, et le nombre de pages
  explorees par site est borne.

Rien n'est telecharge : chaque resultat est un lien a ouvrir soi-meme.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "VideoSorterBot/1.0 (+usage personnel ; recherche de contenu video)"
REQUEST_TIMEOUT = 10
MAX_PAGE_BYTES = 3_000_000
DOMAIN_DELAY = 0.6  # secondes entre deux requetes vers le meme domaine
MAX_LINKS_PER_PAGE = 12
MAX_CRAWL_DEPTH = 2

VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".webm",
    ".mpg", ".mpeg", ".m2ts", ".mts", ".ts", ".ogv",
}

# Du plus haut au plus bas : le premier seuil atteint donne l'appellation.
RESOLUTION_STEPS = [("4K", 2160), ("1080p", 1080), ("720p", 720), ("480p", 480), ("360p", 360)]

CATEGORY_HINTS = (
    "categorie", "category", "categories", "tag", "tags", "genre", "genres",
    "rubrique", "rubriques", "theme", "themes", "collection", "collections",
    "archive", "archives", "sujet", "sujets", "forum", "forums", "topic",
    "topics", "videos", "video",
)


def normalize(text: str) -> str:
    """Minuscules, sans accents : pour comparer sans se soucier de la casse."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


_ISO8601_RE = re.compile(
    r"^P(?:\d+Y)?(?:\d+M)?(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$"
)


def parse_iso8601_duration(value: str) -> int | None:
    """« PT5M30S » -> 330. Renvoie None si la chaine ne ressemble a rien."""
    if not value:
        return None
    match = _ISO8601_RE.match(value.strip())
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds = match.groups()
    total = int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)
    return int(total) or None


@dataclass
class SearchFilters:
    """Ce que l'utilisateur cherche : mots-cles et seuils de duree/resolution."""

    keywords: str = ""
    min_duration_s: int = 0
    min_height: int = 0
    max_sites: int = 15
    max_pages_per_site: int = 8

    def keyword_terms(self) -> list[str]:
        return [t for t in normalize(self.keywords).split() if t]

    def matches_text(self, *texts: str) -> bool:
        terms = self.keyword_terms()
        if not terms:
            return True
        haystack = normalize(" ".join(t for t in texts if t))
        return any(term in haystack for term in terms)

    def matches_duration(self, duration_s: int | None) -> bool:
        if not self.min_duration_s:
            return True
        if duration_s is None:
            return True  # inconnue : on ne rejette pas ce qu'on ne peut pas mesurer
        return duration_s >= self.min_duration_s

    def matches_height(self, height: int | None) -> bool:
        if not self.min_height:
            return True
        if height is None:
            return True
        return height >= self.min_height


@dataclass
class VideoResult:
    """Une video trouvee : de quoi l'afficher, jamais de quoi la telecharger."""

    title: str
    page_url: str
    source_domain: str
    thumbnail_url: str = ""
    duration_s: int | None = None
    height: int | None = None
    snippet: str = ""

    @property
    def duration_label(self) -> str:
        if not self.duration_s:
            return "?"
        minutes, seconds = divmod(int(self.duration_s), 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"

    @property
    def resolution_label(self) -> str:
        if not self.height:
            return "?"
        for name, min_height in RESOLUTION_STEPS:
            if self.height >= min_height:
                return name
        return f"{self.height}p"


# ---------------------------------------------------------------------------
# Extraction : ce qu'une page dit d'elle-meme
# ---------------------------------------------------------------------------

def _soup(html_text: str) -> BeautifulSoup:
    return BeautifulSoup(html_text, "html.parser")


def _meta(soup: BeautifulSoup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return (tag.get("content") or "").strip() if tag else ""


def _to_int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _iter_jsonld_objects(data):
    if isinstance(data, list):
        for item in data:
            yield from _iter_jsonld_objects(item)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _iter_jsonld_objects(data["@graph"])
        yield data


def _jsonld_type(obj: dict) -> str:
    value = obj.get("@type") or ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return normalize(str(value))


def extract_meta_videos(html_text: str, page_url: str) -> list[VideoResult]:
    """Videos decrites explicitement par la page : JSON-LD ``VideoObject``
    puis, a defaut, balises Open Graph. C'est la source la plus fiable : le
    site l'a lui-meme renseignee pour ses propres apercus."""
    soup = _soup(html_text)
    domain = urlparse(page_url).netloc
    results: list[VideoResult] = []

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        for obj in _iter_jsonld_objects(data):
            if _jsonld_type(obj) != "videoobject":
                continue
            title = str(obj.get("name") or "").strip()
            if not title:
                continue
            thumb = obj.get("thumbnailUrl") or obj.get("thumbnail") or ""
            if isinstance(thumb, list):
                thumb = thumb[0] if thumb else ""
            if isinstance(thumb, dict):
                thumb = thumb.get("url", "")
            results.append(VideoResult(
                title=title,
                page_url=page_url,
                source_domain=domain,
                thumbnail_url=urljoin(page_url, thumb) if thumb else "",
                duration_s=parse_iso8601_duration(str(obj.get("duration") or "")),
                height=_to_int(obj.get("height")),
                snippet=str(obj.get("description") or "")[:240],
            ))

    if results:
        return results

    og_title = _meta(soup, "og:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    og_video = _meta(soup, "og:video") or _meta(soup, "og:video:url")
    if og_video and og_title:
        og_image = _meta(soup, "og:image")
        results.append(VideoResult(
            title=og_title,
            page_url=page_url,
            source_domain=domain,
            thumbnail_url=urljoin(page_url, og_image) if og_image else "",
            height=_to_int(_meta(soup, "og:video:height")),
            snippet=_meta(soup, "og:description")[:240],
        ))
    return results


def find_direct_video_links(html_text: str, page_url: str) -> list[VideoResult]:
    """Fichiers video lies directement (mp4, webm…) ou balises ``<video>``,
    sans fiche descriptive autour."""
    soup = _soup(html_text)
    domain = urlparse(page_url).netloc
    results: list[VideoResult] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        if Path(urlparse(href).path).suffix.lower() not in VIDEO_EXTS or href in seen:
            continue
        seen.add(href)
        title = (a.get_text() or "").strip() or Path(urlparse(href).path).name
        results.append(VideoResult(title=title, page_url=href, source_domain=domain))

    for video in soup.find_all("video"):
        src = video.get("src") or ""
        if not src:
            source_tag = video.find("source")
            src = (source_tag.get("src") if source_tag else "") or ""
        if not src:
            continue
        href = urljoin(page_url, src)
        if href in seen:
            continue
        seen.add(href)
        poster = video.get("poster") or ""
        results.append(VideoResult(
            title=video.get("title") or Path(urlparse(href).path).name,
            page_url=href,
            source_domain=domain,
            thumbnail_url=urljoin(page_url, poster) if poster else "",
        ))
    return results


def find_links(html_text: str, page_url: str) -> list[str]:
    """Liens internes au domaine, tries : ceux qui ressemblent a une
    categorie ou une liste de videos passent devant."""
    soup = _soup(html_text)
    domain = urlparse(page_url).netloc
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"]).split("#", 1)[0]
        if href in seen:
            continue
        seen.add(href)
        parsed = urlparse(href)
        if parsed.netloc != domain or parsed.scheme not in ("http", "https"):
            continue
        text = normalize(a.get_text() or "")
        path = normalize(href)
        score = 1 if any(hint in text or hint in path for hint in CATEGORY_HINTS) else 0
        scored.append((score, href))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [href for _, href in scored]


# ---------------------------------------------------------------------------
# Exploration d'un site : accueil -> categories -> fiches
# ---------------------------------------------------------------------------

class SiteCrawler:
    """Explore un site a partir d'une page de depart, a la recherche de
    videos qui correspondent aux filtres.

    Respecte ``robots.txt`` et une politesse minimale (delai entre requetes,
    nombre de pages borne). N'essaie jamais de contourner un blocage : une
    page qui repond une erreur ou qui ressemble a une protection anti-robot
    est simplement abandonnee.
    """

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._robots_cache: dict[str, robotparser.RobotFileParser] = {}
        self._last_request: dict[str, float] = {}

    def _robots_for(self, url: str) -> robotparser.RobotFileParser:
        domain = urlparse(url).netloc
        parser = self._robots_cache.get(domain)
        if parser is not None:
            return parser
        parser = robotparser.RobotFileParser()
        robots_url = f"{urlparse(url).scheme}://{domain}/robots.txt"
        try:
            resp = self.session.get(robots_url, timeout=REQUEST_TIMEOUT)
            parser.parse(resp.text.splitlines() if resp.ok else [])
        except requests.RequestException:
            parser.parse([])
        self._robots_cache[domain] = parser
        return parser

    def allowed(self, url: str) -> bool:
        try:
            return self._robots_for(url).can_fetch(USER_AGENT, url)
        except Exception:
            return True

    def _throttle(self, domain: str) -> None:
        last = self._last_request.get(domain, 0.0)
        wait = DOMAIN_DELAY - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        self._last_request[domain] = time.monotonic()

    def fetch(self, url: str) -> str | None:
        """Le HTML de la page, ou None si le robot n'a pas le droit d'y
        aller, si ce n'est pas du HTML, ou si la reponse est trop lourde."""
        if not self.allowed(url):
            return None
        self._throttle(urlparse(url).netloc)
        try:
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
        except requests.RequestException:
            return None
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            resp.close()
            return None
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in resp.iter_content(8192):
                total += len(chunk)
                if total > MAX_PAGE_BYTES:
                    break
                chunks.append(chunk)
        finally:
            resp.close()
        try:
            return b"".join(chunks).decode(resp.encoding or "utf-8", errors="ignore")
        except LookupError:
            return b"".join(chunks).decode("utf-8", errors="ignore")

    def explore(self, start_url: str, filters: SearchFilters, should_stop=lambda: False):
        """Genere les :class:`VideoResult` trouves, en largeur d'abord."""
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(start_url, 0)]
        pages_fetched = 0
        max_pages = max(1, filters.max_pages_per_site)
        while queue and pages_fetched < max_pages and not should_stop():
            url, depth = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            html_text = self.fetch(url)
            pages_fetched += 1
            if not html_text:
                continue
            candidates = extract_meta_videos(html_text, url) + find_direct_video_links(html_text, url)
            for video in candidates:
                if (
                    filters.matches_text(video.title, video.snippet)
                    and filters.matches_duration(video.duration_s)
                    and filters.matches_height(video.height)
                ):
                    yield video
            if depth < MAX_CRAWL_DEPTH:
                for link in find_links(html_text, url)[:MAX_LINKS_PER_PAGE]:
                    if link not in visited:
                        queue.append((link, depth + 1))


# ---------------------------------------------------------------------------
# Moteur de recherche : trouver les sites de depart
# ---------------------------------------------------------------------------

class SearchBackendError(RuntimeError):
    """Cle absente ou invalide, ou quota depasse : a afficher tel quel."""


@dataclass
class SeedResult:
    url: str
    title: str = ""
    snippet: str = ""


def bing_search(api_key: str, query: str, max_results: int = 15) -> list[SeedResult]:
    """Interroge Bing Web Search (cle Azure gratuite jusqu'a un petit quota).

    On ne scrape jamais directement une page de resultats d'un moteur : c'est
    contraire a ses conditions d'utilisation et ca declenche vite un blocage
    anti-robot. L'API officielle est le seul chemin retenu ici.
    """
    if not api_key:
        raise SearchBackendError(
            "Aucune cle API Bing renseignee. Voir le lien « Obtenir une cle "
            "gratuite » dans cette boite de dialogue."
        )
    seeds: list[SeedResult] = []
    offset = 0
    session = requests.Session()
    while len(seeds) < max_results and offset < 100:
        try:
            resp = session.get(
                "https://api.bing.microsoft.com/v7.0/search",
                headers={"Ocp-Apim-Subscription-Key": api_key},
                params={
                    "q": query,
                    "count": min(50, max_results - len(seeds)),
                    "offset": offset,
                    "mkt": "fr-FR",
                },
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise SearchBackendError(f"Recherche impossible : {exc}") from exc
        if resp.status_code == 401:
            raise SearchBackendError("Cle API Bing refusee (401) : verifiez qu'elle est correcte.")
        if resp.status_code == 403:
            raise SearchBackendError("Acces refuse par Bing (403) : quota sans doute depasse.")
        if not resp.ok:
            raise SearchBackendError(f"Bing a repondu {resp.status_code}.")
        data = resp.json()
        pages = (data.get("webPages") or {}).get("value") or []
        if not pages:
            break
        for page in pages:
            seeds.append(SeedResult(
                url=page.get("url", ""),
                title=page.get("name", ""),
                snippet=page.get("snippet", ""),
            ))
        offset += len(pages)
    return seeds[:max_results]


# ---------------------------------------------------------------------------
# Le tout enchaine : pense pour tourner dans un fil separe de l'interface
# ---------------------------------------------------------------------------

def run_search(
    api_key: str,
    filters: SearchFilters,
    should_stop=lambda: False,
    on_status=lambda text: None,
    on_result=lambda video: None,
) -> None:
    """Cherche des pages de depart puis explore chacune.

    Communique par callbacks plutot que par valeur de retour : c'est pense
    pour tourner dans un fil separe pendant que l'interface reste reactive.
    """
    on_status(f"Recherche « {filters.keywords} »…")
    try:
        seeds = bing_search(api_key, filters.keywords, filters.max_sites)
    except SearchBackendError as exc:
        on_status(str(exc))
        return
    on_status(f"{len(seeds)} site(s) a explorer.")
    crawler = SiteCrawler()
    seen_domains: set[str] = set()
    for seed in seeds:
        if should_stop():
            break
        domain = urlparse(seed.url).netloc
        if not domain or domain in seen_domains:
            continue
        seen_domains.add(domain)
        on_status(f"Exploration de {domain}…")
        found = 0
        for video in crawler.explore(seed.url, filters, should_stop=should_stop):
            found += 1
            on_result(video)
        on_status(f"{domain} : {found} resultat(s).")
    on_status("Recherche arretee." if should_stop() else "Recherche terminee.")
