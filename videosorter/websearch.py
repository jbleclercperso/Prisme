"""Recherche video sur le web : la recherche de chaque site, d'un seul geste.

On donne une liste de sites (un par ligne) et des mots-cles. Pour chaque site,
Prisme se sert de **sa propre recherche** -- celle qu'on utiliserait a la main --
puis lit les pages de resultats, l'une apres l'autre, jusqu'au nombre voulu :
titre, vignette, duree et qualite de chaque video. Les filtres (duree minimum,
resolution) s'appliquent a ce que le site affiche.

L'ancienne version partait de la page d'accueil et y cherchait des titres
contenant les mots : quelques dizaines de videos « vues », et zero resultat des
qu'un filtre s'y ajoutait. Elle dependait aussi d'un service payant (SerpAPI)
pour decouvrir des sites : il n'y en a plus besoin.

Comment on trouve la recherche d'un site, dans l'ordre :
1. l'adresse donnee sur la ligne, si elle contient ``{q}`` (par exemple
   ``exemple.com/search?q={q}``) ;
2. le formulaire de recherche de la page d'accueil ;
3. les adresses de recherche les plus courantes, essayees une a une.
L'adresse trouvee est retenue (`templates`), et les fois suivantes ne la
cherchent plus.

Politesse : un delai separe deux pages d'un meme site, le nombre de pages lues
par site est borne, et un site qui refuse (erreur, protection anti-robot) est
signale et laisse de cote -- jamais force.
"""
from __future__ import annotations

import html
import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote, urlencode, urljoin, urlparse, parse_qsl, urlunparse

import requests
from bs4 import BeautifulSoup

from .mediafind import resolve as resolve_media, safe_session

# Les certificats auxquels Windows (et donc le navigateur) fait confiance.
# Python a sa propre liste : sur ce PC, elle refusait github.com et la
# plupart des sites -- « SSLError », alors que le navigateur les ouvre. Un
# antivirus qui inspecte les connexions chiffrees ne declare son certificat
# qu'a Windows. Rien n'est moins verifie : on verifie comme le navigateur.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:                               # noqa: BLE001
    pass

# Un navigateur ordinaire : beaucoup de sites servent une page vide, ou une
# erreur, a un robot qui s'annonce comme tel -- alors que c'est bien vous qui
# cherchez, depuis Prisme, comme vous le feriez dans le navigateur.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
REQUEST_TIMEOUT = 12
DISCOVERY_SECONDS = 60      # au plus, pour trouver la recherche d'un site (une fois : elle est retenue)
MAX_PAGE_BYTES = 4_000_000
DOMAIN_DELAY = 0.8          # secondes entre deux pages d'un meme site
MAX_PAGES_PER_SITE = 12     # pages de resultats lues par site et par recherche
PARALLEL_SITES = 16         # sites interroges en meme temps (le reseau attend, pas le PC)
VERIFY_PER_SITE = 3         # resultats d'un site verifies en meme temps
CHANNEL_TAKE = 12           # videos prises dans une chaine ou une categorie trouvee

VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".mkv", ".avi", ".wmv", ".flv", ".webm",
    ".mpg", ".mpeg", ".m2ts", ".mts", ".ts", ".ogv",
}

# Du plus haut au plus bas : le premier seuil atteint donne l'appellation.
RESOLUTION_STEPS = [("4K", 2160), ("1080p", 1080), ("720p", 720), ("480p", 480), ("360p", 360)]

SEARCH_FIELDS = ("q", "k", "s", "query", "search", "search_query", "keyword",
                 "keywords", "term", "text", "search_term", "recherche", "w")


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


# « 12:34 », « 1:02:03 », « 12 min », « 1h 05min », « 45 sec »
_CLOCK_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")
_WORDS_RE = re.compile(
    r"(?:(\d{1,2})\s*h(?:ours?|eures?)?\s*)?(\d{1,3})\s*(?:min|mn|m)\b(?:\s*(\d{1,2})\s*s)?"
    r"|(\d{1,4})\s*(?:sec|s)\b", re.I)


def parse_duration_text(text: str) -> int | None:
    """La duree ecrite sur une vignette, en secondes, ou None."""
    text = text or ""
    match = _CLOCK_RE.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
    match = _WORDS_RE.search(text)
    if match:
        hours, minutes, seconds, only = match.groups()
        if only:
            return int(only)
        return int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)
    return None


_HEIGHT_RE = re.compile(r"\b(2160|1440|1080|720|480|360)p\b|\b(4k|uhd|fhd|full\s*hd|hd)\b", re.I)
_HEIGHT_WORDS = {"4k": 2160, "uhd": 2160, "fhd": 1080, "fullhd": 1080, "hd": 720}


def parse_height_text(text: str) -> int | None:
    """La qualite annoncee par une vignette (« 1080p », « 4K », « HD »), ou None."""
    best = None
    for match in _HEIGHT_RE.finditer(text or ""):
        pixels, word = match.groups()
        value = int(pixels) if pixels else _HEIGHT_WORDS.get(re.sub(r"\s", "", word.lower()))
        if value and (best is None or value > best):
            best = value
    return best


@dataclass
class SearchFilters:
    """Ce qu'on cherche, et ou.

    `queries` : une recherche par ligne (« plage », « coucher de soleil ») ;
    leurs resultats se cumulent. `sites` : un site par ligne, nu
    (« exemple.com ») ou avec son adresse de recherche (« …?q={q} »).
    """

    queries: list = field(default_factory=list)
    sites: list = field(default_factory=list)
    min_duration_s: int = 0
    min_height: int = 0
    max_total_results: int = 0          # 0 : pas de plafond global
    max_pages_per_site: int = MAX_PAGES_PER_SITE
    max_per_site: int = 40              # resultats gardes par site et par recherche
    # Strict : le titre doit contenir *chacun* des mots de la recherche, dans
    # n'importe quel ordre. Non par defaut : un site trouve aussi par ses
    # mots-cles, que le titre ne repete pas. (« Un des mots » ne filtrait
    # presque rien des qu'on en tapait quatre.)
    title_must_match: bool = False
    # Une video dont le site ne dit pas la duree passe-t-elle un filtre de
    # duree ? Oui par defaut : on ne rejette pas ce qu'on ne peut pas mesurer.
    # La qualite, elle, n'a pas ce benefice : sur ces sites, pas de badge
    # « HD » veut dire pas en HD.
    keep_unknown: bool = True

    def matches_duration(self, duration_s: int | None) -> bool:
        if not self.min_duration_s:
            return True
        if duration_s is None:
            return self.keep_unknown
        return duration_s >= self.min_duration_s

    def matches_title(self, title: str, query: str) -> bool:
        return not self.title_must_match or has_all_words(title, query)

    def matches_height(self, height: int | None) -> bool:
        if not self.min_height:
            return True
        if height is None:
            return False
        return height >= self.min_height


def has_all_words(title: str, query: str) -> bool:
    """Chaque mot de la recherche commence un mot du titre, dans n'importe
    quel ordre : « blonde » trouve « blondes », mais « ass » ne se cache pas
    dans « class »."""
    terms = [w for w in re.split(r"\W+", normalize(query)) if len(w) >= 2]
    words = [w for w in re.split(r"\W+", normalize(title)) if w]
    return all(any(w.startswith(t) for w in words) for t in terms)


def split_lines(text: str) -> list:
    """Les lignes non vides d'un champ, sans doublon, dans l'ordre."""
    seen, found = set(), []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and line.lower() not in seen:
            seen.add(line.lower())
            found.append(line)
    return found


@dataclass
class VideoResult:
    """Une video trouvee : de quoi l'afficher et l'ouvrir. `media` : son
    fichier entier, trouve et mesure (mediafind.Media)."""

    title: str
    page_url: str
    source_domain: str
    thumbnail_url: str = ""
    duration_s: int | None = None
    height: int | None = None
    snippet: str = ""
    query: str = ""
    media: object = None
    size: int = 0
    found_on: str = ""          # la page de resultats ou on l'a trouve

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
# Lire une page
# ---------------------------------------------------------------------------

def _soup(html_text: str) -> BeautifulSoup:
    return BeautifulSoup(html_text, "html.parser")


def _meta(soup: BeautifulSoup, prop: str) -> str:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
    return (tag.get("content") or "").strip() if tag else ""


def web_url(base: str, raw) -> str:
    """Une adresse de la page, rendue absolue -- ou rien si elle ne mene pas au web.

    `urljoin` garde tel quel un « file://hote/partage/x.jpg » ecrit par la
    page : charger cette vignette, ou ouvrir ce lien, faisait presenter par
    Windows l'empreinte du compte a un serveur etranger. Seuls http et https
    sortent d'ici.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    url = urljoin(base, text)
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc else ""


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
        for key in ("itemListElement", "item"):
            if key in data:
                yield from _iter_jsonld_objects(data[key])
        yield data


def _jsonld_type(obj: dict) -> str:
    value = obj.get("@type") or ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return normalize(str(value))


def extract_meta_videos(html_text: str, page_url: str) -> list:
    """Videos que la page decrit elle-meme : JSON-LD ``VideoObject`` (y compris
    en liste), puis, a defaut, balises Open Graph."""
    soup = _soup(html_text)
    domain = urlparse(page_url).netloc
    results: list = []
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
            link = web_url(page_url, obj.get("url") or obj.get("embedUrl") or "") or page_url
            results.append(VideoResult(
                title=title, page_url=link, source_domain=domain,
                thumbnail_url=web_url(page_url, thumb),
                duration_s=parse_iso8601_duration(str(obj.get("duration") or "")),
                height=_to_int(obj.get("height")),
                snippet=str(obj.get("description") or "")[:240],
            ))
    if results:
        return results
    og_title = _meta(soup, "og:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    og_video = _meta(soup, "og:video") or _meta(soup, "og:video:url")
    if og_video and og_title:
        results.append(VideoResult(
            title=og_title, page_url=page_url, source_domain=domain,
            thumbnail_url=web_url(page_url, _meta(soup, "og:image")),
            height=_to_int(_meta(soup, "og:video:height")),
            snippet=_meta(soup, "og:description")[:240],
        ))
    return results


# Ce qui, dans une adresse, dit « page d'une video » plutot que « categorie ».
_VIDEO_PATH = re.compile(r"(video|watch|view|/v/|/embed/|\d{4,}|[a-z0-9]{8,}\.html?$)", re.I)
_NOT_VIDEO_PATH = re.compile(
    r"/(tags?|categor|channels?|models?|pornstars?|users?|profile|search|login|"
    r"signup|register|page/\d|premium|upload|playlists?|"
    # Les galeries de photos (xhamster rendait ses « /photos/gallery/… »
    # comme resultats d'une recherche de videos).
    r"photos?|gallery|galleries|pics|pictures|images?)(/|$|\?)", re.I)
# Les blocs de navigation : menu, barre laterale de categories, fil d'Ariane.
_MENU = re.compile(r"(^|[\s_-])(menu|navbar|nav|sidebar|breadcrumb|footer|header|"
                   r"categories-mobile|dropdown|megamenu)([\s_-]|$)", re.I)
_IMG_ATTRS = ("data-src", "data-original", "data-thumb", "data-thumb_url",
              "data-lazy-src", "data-mediumthumb", "data-image", "src")


_BACKGROUND = re.compile(r"background(?:-image)?\s*:[^;]*url\(\s*['\"]?([^'\")]+)", re.I)


def _background_of(tag) -> str:
    """Une vignette posee en fond (« style="background-image: url(…)" »), ou
    dans un attribut « data-bg » : txxx n'a pas une seule <img> dans ses
    resultats."""
    if tag is None:
        return ""
    for node in [tag] + tag.find_all(True, limit=40):
        for attr in ("data-bg", "data-background", "data-bg-src"):
            if node.get(attr):
                return node.get(attr)
        found = _BACKGROUND.search(node.get("style") or "")
        if found and not found.group(1).startswith("data:"):
            return found.group(1)
    return ""


def _image_of(tag) -> str:
    img = tag.find("img") if tag is not None else None
    if img is None:
        return _background_of(tag)
    for attr in _IMG_ATTRS:
        value = img.get(attr) or ""
        if value and not value.startswith("data:"):
            return value
    srcset = img.get("srcset") or img.get("data-srcset") or ""
    return srcset.split(",")[0].strip().split(" ")[0] if srcset else ""


def path_shape(path: str) -> tuple:
    """La forme d'un chemin : « /a/TzRk8Yj8 » -> (2, "a", "code")."""
    parts = [p for p in path.split("/") if p]
    if not parts:
        return (0, "", "")
    last = parts[-1]
    kind = "nombre" if last.isdigit() else ("code" if re.fullmatch(r"[A-Za-z0-9]{5,16}", last)
                                            else "titre")
    return (len(parts), parts[0] if len(parts) > 1 else "", kind)


def extract_cards(html_text: str, page_url: str) -> list:
    """Les vignettes d'une page de resultats : lien, titre, image, duree,
    qualite -- lues sur la carte de chaque video, quelle que soit la mise en
    page du site. Complete ce que la page decrit en JSON-LD.

    La carte, c'est le plus grand bloc autour du lien qui ne parle que de
    cette video : la duree et la qualite y sont ecrites a cote de l'image,
    rarement dans le lien lui-meme."""
    soup = _soup(html_text)
    domain = urlparse(page_url).netloc
    base_host = domain.split(":")[0].removeprefix("www.")

    # Chaque lien n'est juge qu'une fois, et chaque bloc ne compte ses liens
    # qu'une fois : la carte de chaque lien se cherchait en rejugeant tous les
    # liens de ses blocs parents -- 144 000 jugements, six secondes, pour une
    # page de bigporn (le parseur, lui, en prend 0,16).
    judged: dict = {}
    below: dict = {}

    def own_path(tag) -> tuple:
        key = id(tag)
        if key not in judged:
            judged[key] = _own_path(tag)
        return judged[key]

    def _own_path(tag) -> tuple:
        """(adresse, chemin) d'un lien vers une page du site, ou ("", "")."""
        href = web_url(page_url, tag.get("href") or "")
        if not href:
            return "", ""
        parsed = urlparse(href)
        host = parsed.netloc.split(":")[0].removeprefix("www.")
        path = parsed.path
        if host != base_host or not path or path == "/" or _NOT_VIDEO_PATH.search(path):
            return "", ""
        if in_menu(tag):
            return "", ""
        return href.split("#", 1)[0], path

    def in_menu(tag) -> bool:
        """Un lien du menu, de l'en-tete ou du pied de page n'est jamais un
        resultat : sur pisshamster, les cinquante categories du menu (avec
        vignettes, et de la meme forme que les videos) passaient devant les
        videos trouvees."""
        for parent in tag.parents:
            if parent.name in ("nav", "header", "footer"):
                return not holds_page(parent)
            # Le nom du bloc, sans sa variante (« thumb-list--sidebar » : la
            # grille des resultats de xhamster, dans sa mise en page a colonne,
            # pas un menu).
            classes = " ".join(c.split("--")[0] for c in (parent.get("class") or [])) \
                if parent.name else ""
            if parent.name in ("div", "ul", "aside") and _MENU.search(classes):
                return not holds_page(parent)
        return False

    pictured = [a for a in soup.find_all("a", href=True) if a.find("img") is not None]
    holding: dict = {}

    def holds_page(block) -> bool:
        """Un bloc qui porte la plupart des vignettes de la page n'est pas un
        menu, c'est la page elle-meme : bdsmx.tube enveloppe tout dans
        « app__wrapper menu-small » (un etat d'affichage, pas un menu)."""
        key = id(block)
        if key not in holding:
            inside = sum(1 for a in block.find_all("a", href=True) if a.find("img") is not None)
            holding[key] = inside > max(8, len(pictured) // 2)
        return holding[key]

    # La forme des liens de ce site-ci. Les mots attendus (« video »,
    # « watch »…) ne disent pas tout : erome.com mene a ses videos par
    # « /a/TzRk8Yj8 ». Les liens qui portent une vignette, s'ils ont presque
    # tous la meme forme, sont les resultats -- quels que soient leurs mots.
    shapes: dict = {}
    for a in soup.find_all("a", href=True):
        href, path = own_path(a)
        if href and (a.find("img") is not None or _background_of(a)):
            shape = path_shape(path)
            shapes[shape] = shapes.get(shape, 0) + 1
    learned = max(shapes, key=shapes.get) if shapes else None
    if learned is not None and shapes[learned] < 4:
        learned = None

    def video_link(tag) -> str:
        href, path = own_path(tag)
        if not href:
            return ""
        if _VIDEO_PATH.search(path) or path_shape(path) == learned:
            return href
        return ""

    found: dict = {}
    for a in soup.find_all("a", href=True):
        href = video_link(a)
        if not href:
            continue
        key = url_key(href)
        card = a
        for _ in range(4):
            parent = card.parent
            if parent is None or parent.name in ("body", "html", "[document]"):
                break
            others = below.get(id(parent))
            if others is None:
                others = below[id(parent)] = {
                    url_key(link) for link in (video_link(x) for x in
                                               parent.find_all("a", href=True)) if link}
            if others - {key}:
                break
            card = parent
        thumb = _image_of(card)
        if not thumb:
            # Vignette ajoutee plus tard par un script (xhamster : 39 cartes
            # sur 46) : le lien compte quand meme s'il a la forme des videos
            # de ce site et un vrai titre -- sa carte s'affichera sans image.
            named = (a.get("aria-label") or a.get("title") or "").strip()
            path = urlparse(href).path
            # Une page de video a un identifiant : des chiffres, ou une
            # adresse a deux niveaux (« /videos/… »). « /gays/ » (bigporn),
            # une categorie, n'en a pas.
            identified = bool(re.search(r"\d", path)) or len([p for p in path.split("/") if p]) >= 2
            if not (learned is not None and path_shape(path) == learned
                    and len(named) >= 12 and identified):
                continue
        img = card.find("img")
        title = (a.get("title") or a.get("aria-label") or (img.get("alt") if img else "") or
                 a.get_text(" ", strip=True) or "").strip()
        if len(title) < 3:
            title = card.get_text(" ", strip=True)[:120]
        text = card.get_text(" ", strip=True)
        video = VideoResult(
            title=title[:200] or Path(urlparse(href).path).name, page_url=href,
            source_domain=domain, thumbnail_url=web_url(page_url, thumb) if thumb else "",
            duration_s=parse_duration_text(text), height=parse_height_text(text),
        )
        known = found.get(key)
        if known is None:
            found[key] = video
            continue
        # Le meme lien, deux fois sur la carte (l'image, puis le titre) : on
        # garde l'adresse la plus nette et le titre le plus parlant.
        if len(video.title) > len(known.title):
            known.title = video.title
        if len(video.page_url) < len(known.page_url):
            known.page_url = video.page_url
        known.duration_s = known.duration_s or video.duration_s
        known.height = known.height or video.height
    return list(found.values())


def beeg_videos(text: str) -> list:
    """Les videos d'une reponse de l'API de beeg (une liste JSON)."""
    try:
        items = json.loads(text)
    except ValueError:
        return []
    found = []
    for item in items if isinstance(items, list) else []:
        file = item.get("file") if isinstance(item, dict) else None
        if not isinstance(file, dict) or not file.get("id"):
            continue
        title = next((d.get("cd_value") for d in file.get("data") or []
                      if isinstance(d, dict) and d.get("cd_column") == "sf_name"), "")
        facts = item.get("fc_facts") or [{}]
        thumbs = (facts[0] or {}).get("fc_thumbs") or [0]
        found.append(VideoResult(
            title=str(title or file["id"]),
            page_url=f"https://beeg.com/-0{file['id']}",
            source_domain="beeg.com",
            thumbnail_url=f"https://thumbs.externulls.com/videos/{file['id']}/{thumbs[0]}.webp",
            duration_s=int(file["fl_duration"]) if file.get("fl_duration") else None,
            height=int(file["fl_height"]) if file.get("fl_height") else None))
    return found


def page_videos(html_text: str, page_url: str) -> list:
    """Tout ce qu'une page de resultats montre comme videos, sans doublon."""
    if urlparse(page_url).netloc == "store.externulls.com":
        return beeg_videos(html_text)
    merged: dict = {}
    for video in extract_meta_videos(html_text, page_url) + extract_cards(html_text, page_url):
        if video.page_url == page_url:
            continue            # la page elle-meme (Open Graph), pas un resultat
        known = merged.get(url_key(video.page_url))
        if known is None:
            merged[url_key(video.page_url)] = video
        else:
            known.duration_s = known.duration_s or video.duration_s
            known.height = known.height or video.height
            known.thumbnail_url = known.thumbnail_url or video.thumbnail_url
    return list(merged.values())


def next_page_url(html_text: str, page_url: str) -> str:
    """Le lien « page suivante » d'une page de resultats, ou ""."""
    parsed = urlparse(page_url)
    if parsed.netloc == "store.externulls.com":
        # L'API de beeg : la page suivante, tant que la page est pleine.
        if len(beeg_videos(html_text)) < BEEG_PAGE:
            return ""
        query = dict(parse_qsl(parsed.query))
        query["offset"] = str(int(query.get("offset") or 0) + BEEG_PAGE)
        return parsed._replace(query=urlencode(query)).geturl()
    soup = _soup(html_text)
    tag = soup.find("link", rel="next") or soup.find("a", rel="next")
    if tag is not None and tag.get("href"):
        return web_url(page_url, tag["href"])
    labels = ("next", "suivant", "suivante", "»", "›", "→", "next page", "page suivante")
    for a in soup.find_all("a", href=True):
        text = normalize(a.get_text(" ", strip=True))
        classes = " ".join(a.get("class") or []) + " " + " ".join(
            (a.parent.get("class") or []) if a.parent is not None else [])
        if text in labels or "next" in normalize(classes) or \
                normalize(a.get("aria-label") or "") in ("next", "suivant", "page suivante"):
            link = web_url(page_url, a["href"])
            if link and link != page_url:
                return link
    return ""


def numbered_page(url: str, number: int) -> str:
    """La page `number` d'une recherche, faute de lien « suivant » : on essaie
    le parametre le plus courant (« page=2 »)."""
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k not in ("page", "p")]
    query.append(("page", str(number)))
    return urlunparse(parsed._replace(query=urlencode(query)))


# ---------------------------------------------------------------------------
# Une video, une seule fois
# ---------------------------------------------------------------------------

# Les parametres d'adresse qui designent la video elle-meme ; tous les autres
# (suivi, page d'origine, tri) changent d'une page de resultats a l'autre pour
# la meme video, qui revenait alors vingt fois.
_ID_PARAMS = {"v", "id", "vid", "video", "video_id", "viewkey", "watch", "key", "item"}


def url_key(url: str) -> str:
    """L'adresse ramenee a ce qui designe la video : sans « www », sans
    parametres de suivi, sans « / » final, sans ancre."""
    parsed = urlparse(url or "")
    host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = parsed.path.rstrip("/").lower()
    kept = sorted((k.lower(), v) for k, v in parse_qsl(parsed.query) if k.lower() in _ID_PARAMS)
    return host + path + ("?" + urlencode(kept) if kept else "")


def title_key(video) -> str:
    """Meme site, meme titre (assez long pour ne pas etre banal) : meme video."""
    title = re.sub(r"\W+", " ", normalize(video.title)).strip()
    if len(title) < 12:
        return ""
    return urlparse(video.page_url).netloc.lower().removeprefix("www.") + "|" + title


def thumb_key(video) -> str:
    """La meme vignette, c'est la meme video (sauf images generiques)."""
    if not video.thumbnail_url:
        return ""
    parsed = urlparse(video.thumbnail_url)
    path = parsed.path.lower()
    if len(path) < 12:
        return ""
    return parsed.netloc.lower() + path


class Seen:
    """Les videos deja rendues, reconnues par adresse, titre ou vignette."""

    def __init__(self):
        self.keys: set = set()
        self.lock = threading.Lock()

    def claim(self, video) -> bool:
        """Vrai si la video est nouvelle (et la retient), faux si deja vue."""
        keys = [k for k in (url_key(video.page_url), title_key(video), thumb_key(video)) if k]
        with self.lock:
            if any(k in self.keys for k in keys):
                return False
            self.keys.update(keys)
            return True


# ---------------------------------------------------------------------------
# Trouver la recherche d'un site
# ---------------------------------------------------------------------------

def site_root(line: str) -> str:
    """« exemple.com », « https://exemple.com/videos » -> « https://exemple.com »."""
    line = line.strip()
    if "://" not in line:
        line = "https://" + line
    parsed = urlparse(line)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""


def given_template(line: str) -> str:
    """L'adresse de recherche donnee sur la ligne (avec « {q} »), ou ""."""
    line = line.strip()
    if "{q}" not in line:
        return ""
    return line if "://" in line else "https://" + line


_SEARCH_HINT = re.compile(r"search|recherch|cherch|find|query|keyword|busca|suche", re.I)


def form_template(html_text: str, page_url: str) -> str:
    """L'adresse de recherche que decrit un formulaire de la page, ou "".

    Le champ n'a pas toujours un nom attendu (« q », « s »…) : un champ texte
    dont le nom, l'indication ou l'etiquette parle de recherche fait l'affaire,
    et, faute de mieux, le seul champ texte d'un formulaire en GET."""
    soup = _soup(html_text)
    candidates = []
    for form in soup.find_all("form"):
        method = (form.get("method") or "get").lower()
        if method != "get":
            continue
        fields = [tag for tag in form.find_all("input") if tag.get("name")
                  and (tag.get("type") or "text").lower() in ("search", "text", "")]
        if not fields:
            continue
        best, score = None, -1
        for tag in fields:
            name = tag.get("name")
            hints = " ".join(str(tag.get(a) or "") for a in
                             ("placeholder", "aria-label", "id", "class", "title"))
            points = (3 if (tag.get("type") or "").lower() == "search" else 0) + \
                     (3 if name.lower() in SEARCH_FIELDS else 0) + \
                     (2 if _SEARCH_HINT.search(hints + " " + name) else 0)
            if points > score:
                best, score = tag, points
        form_hints = " ".join(str(form.get(a) or "") for a in ("action", "id", "class", "role"))
        score += 2 if _SEARCH_HINT.search(form_hints) else 0
        if score <= 0 and len(fields) > 1:
            continue
        action = web_url(page_url, form.get("action") or page_url) or page_url
        hidden = [(tag.get("name"), tag.get("value") or "") for tag in form.find_all("input")
                  if (tag.get("type") or "").lower() == "hidden" and tag.get("name")]
        parsed = urlparse(action)
        pairs = [(k, v) for k, v in parse_qsl(parsed.query)] + hidden
        pairs.append((best.get("name"), "{q}"))
        query = urlencode(pairs, safe="{}")
        candidates.append((score, urlunparse(parsed._replace(query=query))))
    candidates.sort(key=lambda pair: -pair[0])
    return candidates[0][1] if candidates else ""


def opensearch_link(html_text: str, page_url: str) -> str:
    """L'adresse de la description OpenSearch d'un site, ou "" : beaucoup de
    sites y declarent leur adresse de recherche, pour les navigateurs."""
    for tag in _soup(html_text).find_all("link"):
        rel = " ".join(tag.get("rel") or []).lower()
        if "search" in rel and "opensearch" in (tag.get("type") or "").lower():
            return web_url(page_url, tag.get("href"))
    return ""


def opensearch_template(xml_text: str) -> str:
    """L'adresse de recherche (avec « {q} ») d'une description OpenSearch."""
    for match in re.finditer(r"<Url\b([^>]*)>", xml_text, re.I):
        attrs = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', match.group(1)))
        kind = attrs.get("type", "")
        template = html.unescape(attrs.get("template", ""))
        if "{searchTerms}" in template and ("html" in kind or not kind):
            template = template.replace("{searchTerms}", "{q}")
            return re.sub(r"[?&][^=&]+=\{[^}]*\?\}", "", template)
    return ""


# Les sites dont la recherche ne passe pas par une adresse, mais qui ont une
# API lisible : leur adresse de recherche est celle de l'API.
SITE_APIS = {
    "beeg.com": "https://store.externulls.com/tag/videos/{q}?limit=48&offset=0",
}
BEEG_PAGE = 48


def fill(template: str, query: str) -> str:
    """L'adresse d'une recherche. Dans un chemin (« /search/{q}/ »), les
    espaces deviennent des tirets, comme sur la plupart de ces sites."""
    if "{q}" not in template:
        return template
    if urlparse(template).netloc == "store.externulls.com":
        # Les mots-cles de beeg s'ecrivent attaches (« BigTits » -> bigtits).
        return template.replace("{q}", re.sub(r"[^a-z0-9]", "", normalize(query)))
    before = template.split("{q}", 1)[0]
    if "?" in before or "=" in before.rsplit("/", 1)[-1]:
        return template.replace("{q}", quote_plus(query))
    return template.replace("{q}", quote_plus(query.strip()).replace("+", "-"))


def query_terms(query: str) -> list:
    """Les mots d'une recherche qui doivent se retrouver dans les resultats."""
    return [w for w in re.split(r"\W+", normalize(query)) if len(w) >= 3]


def speaks_of(videos: list, query: str) -> bool:
    """Les resultats parlent-ils de la recherche ? Au moins un titre sur dix
    doit contenir un de ses mots -- sinon, le site a renvoye autre chose
    (sa page d'accueil, ses videos du jour) : sa recherche n'a pas ete comprise."""
    terms = query_terms(query)
    if not terms or not videos:
        return bool(videos)
    hits = sum(1 for v in videos if any(t in normalize(v.title) for t in terms))
    return hits >= max(1, len(videos) // 10)


# ---------------------------------------------------------------------------
# Le lecteur de pages : politesse et diagnostics
# ---------------------------------------------------------------------------

_GEOBLOCK = re.compile(
    r"suspendu en france|not available in your (country|region)|unavailable in your "
    r"(country|region)|blocked in your (country|region)|pas disponible dans votre pays|"
    r"accès .{0,30}(suspendu|bloqué).{0,30}(france|pays)", re.I)
_AGE_GATE = re.compile(
    r"age verification|vérifi\w* (de )?votre âge|verify your age|vérifier votre âge|"
    r"confirm your age with|estimation de l'âge", re.I)


class Fetcher:
    """Lit des pages, poliment : un delai par site, et la raison d'un echec.
    S'arrete des qu'on le lui demande (`should_stop`)."""

    def __init__(self, should_stop=lambda: False, browser: str = "", renderer=None):
        # Une connexion par fil : les sites sont interroges en parallele, et
        # une meme `Session` partagee entre fils melangeait ses connexions --
        # des pages qui ne finissaient jamais d'arriver.
        self._local = threading.local()
        self._last: dict = {}
        self._lock = threading.Lock()
        self._pages: dict = {}                  # les dernieres pages lues
        self.should_stop = should_stop
        # Les cookies du navigateur choisi : la verification d'age deja passee
        # sur un site y est inscrite, et vaut aussi pour la recherche.
        self.browser = browser
        self._jar = None
        # Le navigateur invisible (webpage.PageReader), pour les sites qui
        # cherchent en JavaScript. Sans lui, on s'en passe.
        self.renderer = renderer

    def _cookies(self):
        with self._lock:
            if self._jar is None:
                self._jar = False
                if self.browser:
                    try:
                        from yt_dlp.cookies import load_cookies
                        self._jar = load_cookies(None, (self.browser,), None)
                    except Exception:                       # noqa: BLE001
                        self._jar = False
            return self._jar or None

    @property
    def session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = safe_session()
            session.headers.update({
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            })
            jar = self._cookies()
            if jar is not None:
                for cookie in jar:
                    session.cookies.set_cookie(cookie)
            self._local.session = session
        return session

    def get_page(self, url: str) -> tuple:
        """Comme `get`, mais une adresse « js:… » s'ouvre dans le navigateur
        invisible : ses resultats n'existent qu'une fois ses scripts passes."""
        mode, plain = split_mode(url)
        if mode != "js":
            return self.get(plain)
        if self.renderer is None:
            return None, "ce site demande le navigateur invisible"
        # Une page qui n'existe pas (la page 2 d'une recherche qui n'en a
        # qu'une) : une simple demande le dit tout de suite, la ou le
        # navigateur attendait qu'elle s'affiche.
        _text, why = self.get(plain)
        if _text is None:
            return None, why
        html_text, final, why = self.renderer.render(plain)
        self._local.last_url = final
        return (html_text, "ok") if html_text else (None, why or "page vide")

    def _throttle(self, domain: str) -> None:
        with self._lock:
            last = self._last.get(domain, 0.0)
            wait = DOMAIN_DELAY - (time.monotonic() - last)
            self._last[domain] = time.monotonic() + max(0.0, wait)
        end = time.monotonic() + max(0.0, wait)
        while time.monotonic() < end and not self.should_stop():
            time.sleep(0.05)

    def get(self, url: str, referer: str = "") -> tuple:
        """(html, raison) : raison vaut « ok », ou dit ce qui n'a pas marche."""
        if self.should_stop():
            return None, "arrêté"
        with self._lock:
            kept = self._pages.get(url + "|" + referer)
        if kept is not None:
            return kept, "ok"                   # deja lue : ni attente ni reseau
        self._throttle(urlparse(url).netloc)
        if self.should_stop():
            return None, "arrêté"
        text, reason = self._fetch_page(url, referer)
        if text is not None:
            with self._lock:
                self._pages[url + "|" + referer] = text
                while len(self._pages) > 48:
                    self._pages.pop(next(iter(self._pages)))
        return text, reason

    def _fetch_page(self, url: str, referer: str = "") -> tuple:
        text, reason = self._get_once(url, referer)
        if text is None and reason.startswith(("injoignable (ConnectionError",
                                               "page interrompue")):
            # Une connexion coupee net, avant ou pendant la lecture (pas un
            # site muet) : une seconde chance, sur une connexion neuve. Sans
            # elle, un simple hoquet sur la bonne adresse faisait conclure
            # « aucune recherche trouvée », et le site passait au rouge.
            self._local.session = None
            time.sleep(0.4)
            if self.should_stop():
                return None, "arrêté"
            text, reason = self._get_once(url, referer)
        return text, reason

    def _get_once(self, url: str, referer: str = "") -> tuple:
        try:
            # Un lecteur integre s'ouvre en disant de quelle page il vient,
            # comme dans le navigateur : sans cela, beaucoup se refusent
            # (mydaddy.cc, le lecteur de hqporner : « domain blocked »).
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT, stream=True,
                                    headers={"Referer": referer} if referer else None)
        except requests.RequestException as exc:
            said = str(exc)
            if "NameResolution" in said or "getaddrinfo" in said:
                return None, name_verdict(urlparse(url).netloc.split(":")[0])
            if "CN du certificat" in said or "doesn't match" in said or "hostname" in said:
                return None, ("le certificat n'est pas celui du site : probablement "
                              "bloqué par votre fournisseur d'accès")
            return None, f"injoignable ({type(exc).__name__})"
        self._local.last_url = resp.url
        if resp.status_code in (401, 403, 429, 503):
            resp.close()
            if resp.headers.get("cf-mitigated") == "challenge":
                # Une verification anti-robot (Cloudflare) : Prisme ne la
                # contourne pas. Le navigateur, lui, la passe.
                return None, ("protégé par une vérification anti-robot (Cloudflare) : "
                              "cherchez dans votre navigateur, puis collez le lien de "
                              "la vidéo dans Prisme")
            return None, (f"bloque les recherches automatiques (HTTP {resp.status_code})"
                          if resp.status_code != 429 else
                          "trop de demandes, le site demande d'attendre (HTTP 429)")
        if not resp.ok:
            resp.close()
            return None, f"HTTP {resp.status_code}"
        kind = resp.headers.get("Content-Type", "")
        api = urlparse(url).netloc in {urlparse(t).netloc for t in SITE_APIS.values()}
        if "html" not in kind and "xml" not in kind and not (api and "json" in kind):
            resp.close()
            return None, "pas une page web"
        chunks, total = [], 0
        try:
            for chunk in resp.iter_content(16384):
                total += len(chunk)
                if total > MAX_PAGE_BYTES or self.should_stop():
                    break
                chunks.append(chunk)
        except requests.RequestException as exc:
            # Une page qui s'interrompt en route : on le dit, et le site
            # continue avec la suivante -- l'erreur abandonnait tout le site.
            return None, f"page interrompue ({type(exc).__name__})"
        finally:
            resp.close()
        raw = b"".join(chunks)
        try:
            text = raw.decode(resp.encoding or "utf-8", errors="ignore")
        except LookupError:
            text = raw.decode("utf-8", errors="ignore")
        lowered = text.lower()
        empty = None

        def nothing() -> bool:
            """Une page-barriere : presque pas de liens, pas de videos. Un
            avertissement d'age en bas d'un vrai site (erome) n'en est pas une."""
            nonlocal empty
            if empty is None:
                empty = (len(page_videos(text, url)) < 3
                         and lowered.count("<a ") < 80)
            return empty

        head = lowered[:20000]
        if ("captcha" in head or "cf-challenge" in head or
                "just a moment" in head) and nothing():
            return None, "protégé contre les robots (vérification à la main demandée)"
        if _GEOBLOCK.search(lowered) and len(page_videos(text, url)) < 3:
            return None, "le site refuse lui-même l'accès depuis la France : rien à en tirer"
        if _AGE_GATE.search(head) and nothing():
            # Un avertissement d'age n'arrete rien : erome en affiche un sur
            # son accueil, et sa recherche marche. On le note ; il ne servira
            # d'explication que si l'on ne trouve vraiment rien.
            self._local.gate = ("vérification d'âge exigée par la loi : passez-la dans "
                                "votre navigateur, puis choisissez-le dans « Cookies du "
                                "navigateur »")
        return text, "ok"

    def gate(self, reset: bool = False) -> str:
        """La barriere d'age vue sur ce fil depuis le dernier `reset`."""
        said = getattr(self._local, "gate", "")
        if reset:
            self._local.gate = ""
        return said

    def last_url(self) -> str:
        """L'adresse ou la derniere page lue a fini (apres redirections)."""
        return getattr(self._local, "last_url", "")


COMMON_SEARCHES = (
    "/search?q={q}", "/search/?q={q}", "/?k={q}", "/search/{q}/", "/search/{q}",
    "/?s={q}", "/?q={q}", "/search?query={q}", "/search?search={q}", "/?search={q}",
    "/search?search_query={q}", "/search?keyword={q}", "/search?k={q}",
    "/search.php?q={q}", "/s/{q}/", "/s/{q}", "/videos?q={q}", "/video/search?search={q}",
    "/search/videos?search={q}", "/tags/{q}/", "/tag/{q}/", "/recherche?q={q}",
    "/videos/{q}", "/video/{q}", "/search/videos/{q}", "/results?search_query={q}",
    "/?search_query={q}", "/find/{q}", "/search.php?what={q}", "/en/search/{q}/",
    "/search/video/?s={q}", "/categories/{q}/",
)


# Ce que Prisme dit d'un nom de domaine qui n'existe plus : la fenetre s'y
# fie pour proposer de retirer le site (« mort », avec certitude).
GONE = "ce nom de domaine n'existe plus (confirmé par deux annuaires publics)"


def public_dns(host: str) -> str:
    """Ce que deux annuaires publics (Cloudflare, Google) disent de ce nom :
    « absent » s'ils le disent tous deux inexistant, « present » si l'un le
    connait, « » s'ils ne repondent pas. Passer par eux, et non par
    l'annuaire du fournisseur d'acces, distingue un site ferme d'un site que
    le fournisseur bloque."""
    import requests
    verdicts = []
    for server in ("https://cloudflare-dns.com/dns-query", "https://dns.google/resolve"):
        try:
            answer = requests.get(server, params={"name": host, "type": "A"},
                                  headers={"Accept": "application/dns-json"}, timeout=6).json()
        except (requests.RequestException, ValueError):
            continue
        status = answer.get("Status")
        if status == 0 and answer.get("Answer"):
            return "present"
        if status == 3:                     # NXDOMAIN : le nom n'existe pas
            verdicts.append("absent")
    return "absent" if len(verdicts) == 2 else ""


def name_verdict(host: str) -> str:
    """La raison a donner quand le nom d'un site ne se trouve pas."""
    said = public_dns(host)
    if said == "absent":
        return GONE
    if said == "present":
        return ("nom introuvable chez votre fournisseur d'accès, mais le site existe : "
                "il est bloqué (un VPN ou un autre DNS le rend joignable)")
    return "nom introuvable : site fermé, ou bloqué par votre fournisseur d'accès"


_PARKED = re.compile(r"site web est à vendre|domain (name )?(is )?for sale|buy this domain|"
                     r"this domain may be for sale|domaine est à vendre|parked (free|domain)|"
                     r"domain parking", re.I)


def split_mode(template: str) -> tuple:
    """(« js », adresse) pour une recherche a ouvrir dans le navigateur
    invisible, (« », adresse) sinon."""
    if template.startswith("js:"):
        return "js", template[3:]
    if template.startswith("cat:"):
        return "cat", template[4:]
    return "", template


def typed_template(fetcher: Fetcher, root: str, probes: list) -> tuple:
    """Dernier recours : taper le mot dans la case de recherche du site, dans
    le navigateur invisible, comme on le ferait. L'adresse ou l'on arrive
    donne la recherche du site (le mot y est) ; si ses resultats n'existent
    qu'en JavaScript, elle sera ouverte de meme a chaque fois (« js: »)."""
    if fetcher.renderer is None or fetcher.should_stop():
        return "", ""
    word = probes[0]
    html_text, final, why = fetcher.renderer.type_search(root + "/", word)
    if why.startswith("pas de case"):
        return "", ("pas de case de recherche sur ce site, même une fois affiché "
                    "(il se parcourt par catégories ou par modèles)")
    if not html_text:
        return "", why
    template = ""
    for shape in (quote_plus(word), quote(word), word, word.replace(" ", "-")):
        if shape and shape.lower() in final.lower():
            at = final.lower().index(shape.lower())
            template = final[:at] + "{q}" + final[at + len(shape):]
            break
    if not template:
        return "", ("la case de recherche ne mène à aucune adresse réutilisable "
                    "(le site cherche sans changer de page)")
    # Les resultats sont-ils deja dans la page, sans script ?
    plain, _why = fetcher.get(fill(template, word))
    videos = page_videos(plain, final) if plain else []
    if len(videos) >= 3 and speaks_of(videos, word):
        return template, "trouvée en tapant dans sa case de recherche"
    videos = page_videos(html_text, final)
    accepted = len(videos) >= 3 and speaks_of(videos, word)
    if len(videos) >= 3 and not accepted:
        # Les titres ne repetent pas le mot (txxx) : la recherche est bonne si
        # ses resultats changent avec le mot -- verifie dans le navigateur.
        other = next((w for w in CONTRAST_WORDS if w != word), "")
        other_html, other_final, _why = fetcher.renderer.render(fill(template, other))
        first = {url_key(v.page_url) for v in videos}
        second = {url_key(v.page_url) for v in page_videos(other_html or "", other_final)}
        accepted = len(second) >= 3 and len(first & second) <= len(first) // 2
    if accepted:
        return "js:" + template, ("trouvée en tapant dans sa case de recherche "
                                  "(résultats affichés en JavaScript)")
    return "", "sa recherche ne rend pas de vidéos lisibles (des profils, ou rien)"


CATEGORY_PAGES = ("/categories/", "/categories", "/tags/", "/tags", "/channels/")


def _slug(text: str) -> str:
    return re.sub(r"[\s_]+", "-", normalize(unquote(text))).strip("-")


def search_address(fetcher: Fetcher, template: str, words: str) -> str:
    """L'adresse d'une recherche : les mots dans l'adresse de recherche du
    site, ou -- pour un site cherche par ses categories -- la categorie qui
    porte ces mots ("" s'il n'y en a pas)."""
    mode, plain = split_mode(template)
    if mode == "cat":
        return category_url(fetcher, plain, words)
    return fill(template, words)


def category_url(fetcher: Fetcher, root: str, words: str) -> str:
    """L'adresse de la categorie qui porte ces mots, dans la liste des
    categories du site (son accueil, sa page « categories »), ou ""."""
    slug = _slug(words)
    host = urlparse(root).netloc.split(":")[0].removeprefix("www.")
    best = ""
    for extra in ("/",) + CATEGORY_PAGES:
        text, _why = fetcher.get(root + extra)
        if not text:
            continue
        for tag in _soup(text).find_all("a", href=True):
            href = web_url(root + "/", tag["href"])
            parsed = urlparse(href)
            if not href or parsed.netloc.split(":")[0].removeprefix("www.") != host:
                continue
            parts = [_slug(p.rsplit(".", 1)[0]) for p in parsed.path.split("/") if p]
            if slug in parts:
                # La plus courte : « /fr/21/amateur/1.html » plutot que
                # « /fr/136598/spanish_amateur/… » (qui ne passe d'ailleurs pas).
                if not best or len(href) < len(best):
                    best = href.split("#")[0]
        if best:
            return best
    return ""


def category_template(fetcher: Fetcher, root: str, home, probes: list, works) -> tuple:
    """Une recherche par les categories : un lien du site dont le dernier
    morceau est le mot cherche (« /categories/amateur/ », « /tags/amateur »)
    donne l'adresse de toutes les categories (« /categories/{q}/ »). On la
    garde si elle rend bien des videos pour ce mot. Rend (adresse, comment)."""
    word = probes[0] if probes else ""
    if not word:
        return "", ""
    slug = normalize(word).replace(" ", "-")
    pages = [home] if home else []
    for extra in CATEGORY_PAGES:
        if fetcher.should_stop():
            return "", ""
        text, _why = fetcher.get(root + extra)
        if text:
            pages.append(text)
            break
    tried = set()
    for text in pages:
        for tag in _soup(text).find_all("a", href=True):
            href = web_url(root + "/", tag["href"])
            parsed = urlparse(href)
            if not href or parsed.netloc.split(":")[0].removeprefix("www.") != \
                    urlparse(root).netloc.split(":")[0].removeprefix("www."):
                continue
            parts = [p for p in parsed.path.split("/") if p]
            if not parts or normalize(unquote(parts[-1])).replace(" ", "-") != slug:
                continue
            template = href.replace(parts[-1], "{q}", 1).split("#")[0]
            if template in tried:
                continue
            tried.add(template)
            if works(template, 1):
                return template, "par ses catégories (sa recherche est protégée ou absente)"
            if len(tried) >= 4:
                break
    # Les categories ont un numero dans leur adresse (largehdtube) : on
    # gardera la liste des categories, et l'on y cherchera chaque fois celle
    # qui porte les mots de la recherche.
    listing = category_url(fetcher, root, word)
    if listing:
        text, _why = fetcher.get(listing)
        if text and len(page_videos(text, listing)) >= 3:
            return "cat:" + root, "par ses catégories (sa recherche est protégée ou absente)"
    return "", ""


# Un mot que tous ces sites connaissent : pour savoir si une adresse de
# recherche marche encore, quand la recherche du jour n'a rien donne.
ALIVE_WORD = "amateur"


def template_alive(fetcher: Fetcher, template: str) -> bool:
    """L'adresse retenue rend-elle encore des videos, pour un mot courant ?
    Juste ce qu'il faut pour distinguer « rien pour ces mots » d'« adresse
    cassee ». (On exigeait autrefois que les titres contiennent le mot : bien
    des sites ne le repetent pas, et chaque recherche reapprenait le site.)"""
    if split_mode(template)[0] == "cat":
        return True
    url = search_address(fetcher, template, ALIVE_WORD)
    if not url:
        return False
    html_text, _why = fetcher.get_page(url)
    return bool(html_text) and len(page_videos(html_text, split_mode(url)[1])) >= 3


# Un second mot, tres courant, pour voir si les resultats changent avec le mot.
CONTRAST_WORDS = ("blonde", "amateur", "music")


def discovery_probes(queries: list) -> list:
    """Les mots pour decouvrir la recherche d'un site : un seul a la fois.

    On l'essayait avec la premiere recherche entiere : avec quatre mots,
    beaucoup de sites ne rendent presque rien, et l'on concluait « aucune
    recherche trouvée » -- le site passait au rouge alors que sa recherche
    marchait tres bien. Un mot rend toujours de quoi juger."""
    words = []
    for query in queries:
        for word in query_terms(query):
            if word not in words:
                words.append(word)
    return words[:3] or [q.strip() for q in queries[:1] if q.strip()]


def known_template(known: dict, host: str) -> str:
    """L'adresse de recherche apprise pour ce site, avec ou sans « www » : elle
    se range sous le nom ou le site a mene (« pornkai.com »), la liste dit
    souvent « www.pornkai.com » -- et Prisme reapprenait alors le site a
    chaque recherche (49 sites sur 275, jusqu'a deux minutes chacun)."""
    bare = host.lower().removeprefix("www.")
    for key in (host, bare, "www." + bare):
        if known.get(key):
            return known[key]
    return ""


MOVED = "déménagé vers "


def find_template(fetcher: Fetcher, line: str, known: dict, probe, _hops: int = 0) -> tuple:
    """(adresse de recherche, comment on l'a trouvee) pour cette ligne.

    Une adresse n'est retenue que si les resultats qu'elle rend parlent de la
    recherche d'essai : beaucoup de sites repondent a n'importe quelle adresse
    par leur page d'accueil, et l'on prenait cela pour des resultats."""
    template = given_template(line)
    if template:
        return template, "donnée"
    root = site_root(line)
    if not root:
        return "", "adresse illisible"
    host = urlparse(root).netloc
    if host.lower().removeprefix("www.") in SITE_APIS:
        return SITE_APIS[host.lower().removeprefix("www.")], "par l'API du site"
    found = known_template(known, host)
    if found:
        return found, "retenue"

    # Un site qui ne repond pas n'immobilise plus la recherche : trois
    # silences, ou une minute d'essais, et il passe au rouge. Chaque adresse
    # essayee pouvait attendre 12 s -- plus de six minutes pour un seul site.
    deadline = time.monotonic() + DISCOVERY_SECONDS
    silences = {"n": 0, "why": ""}

    probes = [probe] if isinstance(probe, str) else list(probe)
    home_keys = {"keys": set()}

    def works(candidate: str, tries: int = len(probes)) -> bool:
        for word in probes[:max(1, tries)]:
            url = fill(candidate, word)
            html_text, why = fetcher.get(url)
            if not html_text:
                if why.startswith(("injoignable", "page interrompue", "bloque", "protégé",
                                   "trop de demandes")):
                    silences["n"] += 1
                    silences["why"] = why
                return False
            videos = page_videos(html_text, url)
            if len(videos) >= 3 and speaks_of(videos, word):
                return True
            if len(videos) >= 3:
                # Les titres ne repetent pas le mot : beaucoup de sites
                # cherchent dans leurs mots-cles (« amateur » rend trente
                # videos dont aucun titre ne dit « amateur »). Le vrai signe
                # d'une recherche : ses resultats changent avec le mot, et ce
                # ne sont pas ceux de l'accueil.
                return changes_with_word(candidate, word, videos)
            # Presque rien : peut-etre ce mot-la seulement ; on essaie le suivant.
        return False

    def changes_with_word(candidate: str, word: str, videos: list) -> bool:
        other = next((w for w in CONTRAST_WORDS if w != word), "")
        url = fill(candidate, other)
        html_text, _why = fetcher.get(url)
        if not html_text:
            return False
        others = page_videos(html_text, url)
        first = {url_key(v.page_url) for v in videos}
        second = {url_key(v.page_url) for v in others}
        if len(others) < 3 or first & home_keys["keys"] == first:
            return False
        return len(first & second) <= len(first) // 2

    def give_up() -> str:
        if silences["n"] >= 3:
            return silences["why"]
        if time.monotonic() > deadline:
            return "le site répond trop lentement (une minute sans trouver sa recherche)"
        return ""

    fetcher.gate(reset=True)
    home, reason = fetcher.get(root + "/")
    if home is None and reason.startswith(("le certificat", "nom introuvable", "injoignable")):
        # « www.pornkai.com » : certificat casse ; « pornkai.com » : ouvert.
        # L'autre forme de l'adresse, avant de renoncer.
        parsed = urlparse(root)
        other = (parsed.netloc[4:] if parsed.netloc.startswith("www.")
                 else "www." + parsed.netloc)
        other_root = f"{parsed.scheme}://{other}"
        other_home, other_reason = fetcher.get(other_root + "/")
        if other_home is not None:
            root, host, home, reason = other_root, other, other_home, other_reason
    home_keys["keys"] = {url_key(v.page_url) for v in page_videos(home, root + "/")} \
        if home else set()
    plain_refusal = home is None and reason.startswith("bloque les recherches")
    if home is None and not plain_refusal and not reason.startswith(("HTTP 4", "pas une page")):
        return "", reason               # injoignable, anti-robot, age, pays… : inutile d'insister
    landed = urlparse(fetcher.last_url()).netloc.split(":")[0].removeprefix("www.")
    base = host.split(":")[0].removeprefix("www.")
    same = (landed == base or landed.endswith("." + base) or base.endswith("." + landed))
    if home and landed and not same:          # fr.cam4.com, c'est encore cam4.com
        # Le site a demenage (goldmaal.com -> goldmaal.cc) : on le suit, et
        # c'est a sa nouvelle adresse qu'on apprend sa recherche. La fenetre
        # remplace l'ancienne adresse dans la liste : plus de redirection la
        # fois suivante. Un seul saut : jamais de ronde entre deux sites.
        if _hops:
            return "", (f"l'adresse mène à un autre site ({landed}) : ce site a changé "
                        "de nom ou n'existe plus")
        final = urlparse(fetcher.last_url())
        moved = f"{final.scheme}://{final.netloc}"
        template, how = find_template(fetcher, moved, known, probe, _hops=1)
        return template, f"{MOVED}{final.netloc.removeprefix('www.')} — {how}"
    if home and _PARKED.search(home[:30000]) and not page_videos(home, root + "/"):
        return "", ("ce nom de domaine est à vendre : le site n'est pas (ou plus) "
                    "à cette adresse — vérifiez l'orthographe")
    if home:
        described = opensearch_link(home, root + "/")
        if described:
            xml_text, _why = fetcher.get(described)
            template = opensearch_template(xml_text or "")
            if template and works(template):
                return template, "déclarée par le site"
        template = form_template(home, root + "/")
        if template and works(template):
            return template, "formulaire du site"
    elif "protégé" in reason or "trop de demandes" in reason:
        return "", reason
    elif fetcher.should_stop():
        return "", "arrêté"
    for pattern in COMMON_SEARCHES:
        if fetcher.should_stop():
            return "", "arrêté"
        stop = give_up()
        if stop:
            # La recherche se refuse (anti-robot, lenteur) : ses categories,
            # elles, s'ouvrent parfois (largehdtube).
            folder, how = category_template(fetcher, root, home, probes, works)
            return (folder, how) if folder else ("", stop)
        if works(root + pattern):
            return root + pattern, "adresse courante"
    # Pas de recherche utilisable : les categories et mots-cles du site, comme
    # des dossiers (« /categories/amateur/ »). largehdtube protege sa
    # recherche, pas ses categories.
    folder, how = category_template(fetcher, root, home, probes, works)
    if folder:
        return folder, how
    if home is None:
        return "", reason
    # Rien par les adresses connues : on tape dans sa case de recherche,
    # dans le navigateur invisible, comme on le ferait a la main.
    typed, how = typed_template(fetcher, root, probes)
    if typed:
        return typed, how
    if fetcher.gate():
        return "", fetcher.gate()
    if how:
        return "", how
    return "", ("aucune recherche trouvée sur ce site (il cherche peut-être en "
                "JavaScript : donnez l'adresse d'une recherche avec {q})")


# ---------------------------------------------------------------------------
# Le tout, dans un fil a part
# ---------------------------------------------------------------------------

WHY_DROPPED = {
    "durée": "trop courtes ou sans durée affichée",
    "qualité": "sans la qualité demandée",
    "titre": "dont le titre ne contient pas tous les mots (Strict)",
    "aperçu": "dont la page ne donne qu'un aperçu",
    "fichier": "sans fichier vidéo téléchargeable (payant, direct, ou protégé)",
}


def run_search(filters: SearchFilters, templates: dict | None = None,
               should_stop=lambda: False, on_status=lambda text: None,
               on_result=lambda video: None,
               on_site=lambda name, state, text: None,
               browser: str = "", renderer=None, ffprobe: str = "") -> dict:
    """Interroge chaque site pour chaque recherche, et rend les resultats au
    fil de l'eau (`on_result`). `on_site(nom, etat, texte)` dit ou en est
    chaque site : « cherche », « ok », « vide » ou « erreur ».

    Chaque site a sa part (`max_per_site` par recherche) : un plafond global
    etait atteint par les premiers sites, et les suivants n'etaient jamais
    interroges. Rend les adresses de recherche trouvees, a retenir."""
    templates = dict(templates or {})
    queries = [q for q in filters.queries if q.strip()]
    sites = [s for s in filters.sites if s.strip()]
    if not queries or not sites:
        on_status("Indiquez au moins un site et une recherche.")
        return templates
    fetcher = Fetcher(should_stop, browser=browser, renderer=renderer)
    lock = threading.Lock()
    seen = Seen()
    total = {"kept": 0}
    cap = filters.max_total_results or 0          # 0 : pas de plafond global

    def checked(batch: list, enough) -> list:
        """Le parcours jusqu'a la video, pour chaque resultat : sa page, son
        lecteur, ses fichiers mesures. Seuls ceux dont la video entiere se
        telecharge gardent `media` -- avec leur vraie duree et leur vraie
        qualite. Sans ffprobe, rien n'est verifie. Rend la liste dans
        l'ordre ; s'arrete quand `enough()` le dit."""
        if not ffprobe or not batch:
            for video in batch:
                video.media = True          # rien a verifier : on montre
            return batch

        listening = {"tries": 0, "worked": False}

        def one(video):
            if enough() or should_stop():
                video.media = None
                return video
            # L'ecoute du lecteur coute une dizaine de secondes : deux essais
            # par site, et, si elle y marche, pour tous ses resultats.
            listen = None
            if fetcher.renderer is not None and (listening["worked"] or listening["tries"] < 2):
                def listen(url):
                    listening["tries"] += 1
                    heard = fetcher.renderer.sniff(url)
                    if heard[0]:
                        listening["worked"] = True
                    return heard
            media, why = resolve_media(fetcher.get, video.page_url, ffprobe, USER_AGENT,
                                       video.duration_s, sniff=listen,
                                       origin=video.found_on)
            video.media = media
            if media is not None:
                video.duration_s = int(media.duration)
                video.height = media.height
                video.size = media.size
            else:
                video.snippet = why
            return video

        with ThreadPoolExecutor(max_workers=VERIFY_PER_SITE) as pool:
            first = list(pool.map(one, batch))
        # Un resultat sans video a lui, mais plein de vignettes : une chaine,
        # un modele, une categorie (beeg ne rend que cela). On y entre, comme
        # on le ferait en deux clics, et l'on verifie les videos qu'elle montre.
        out = []
        for video in first:
            if video.media is not None or not video.snippet.startswith("aucun fichier"):
                out.append(video)
                continue
            page, _why = fetcher.get(video.page_url)          # deja lue : en memoire
            inside = [v for v in page_videos(page or "", video.page_url)
                      if v.page_url != video.page_url and seen.claim(v)][:CHANNEL_TAKE]
            if len(inside) < 3:
                out.append(video)
                continue
            for child in inside:
                child.snippet = f"dans « {video.title[:40]} »"
            with ThreadPoolExecutor(max_workers=VERIFY_PER_SITE) as pool:
                out += list(pool.map(one, inside))
        return out

    def full() -> bool:
        return should_stop() or (cap and total["kept"] >= cap)

    def one_site(line: str) -> None:
        name = urlparse(site_root(line) or line).netloc or line
        on_site(name, "cherche", "recherche de sa page de recherche…")
        probes = discovery_probes(queries)
        template, how = find_template(fetcher, line, templates, probes)
        # L'adresse retenue sert telle quelle : elle etait reverifiee avant
        # chaque recherche, en exigeant que les titres contiennent le mot --
        # bien des sites ne le repetent pas, et Prisme reapprenait alors le
        # site (jusqu'a deux minutes), a chaque recherche, pour retrouver la
        # meme adresse. Elle n'est remise en cause que si elle ne rend rien.
        if not template:
            on_site(name, "erreur", how)
            on_status(f"{name} : {how}.")
            return
        host = urlparse(split_mode(template)[1]).netloc
        with lock:
            templates[host] = template
        kept_site, notes, usable = 0, [], False
        # Le site a demenage (redirection suivie) : la fenetre corrige la liste.
        moved_note = how.split(" — ")[0] if how.startswith(MOVED) else ""
        first_pages: list = []
        relearned = {"done": False}
        for query in queries:
            if full():
                break
            url = search_address(fetcher, template, query)
            if not url:
                notes.append(f"« {query} » : pas de catégorie de ce nom sur ce site")
                usable = True
                continue
            kept = seen_here = 0
            dropped = {"durée": 0, "qualité": 0, "titre": 0, "aperçu": 0, "fichier": 0}
            # Plusieurs mots que le site ne trouve pas ensemble : on lui
            # demande le plus parlant seul, et c'est Prisme qui garde les
            # titres ou figurent tous les mots.
            terms = query_terms(query)
            wider = max(terms, key=len) if len(terms) >= 2 else ""
            widened = False
            number = 0
            while number < max(1, filters.max_pages_per_site):
                number += 1
                if full() or kept >= filters.max_per_site:
                    break
                on_site(name, "cherche", f"« {query} », page {number}…")
                html_text, reason = fetcher.get_page(url)
                if not html_text:
                    if number == 1 and reason != "arrêté":
                        notes.append(f"« {query} » : {reason}")
                    break
                videos = page_videos(html_text, split_mode(url)[1])
                for video in videos:
                    video.found_on = split_mode(url)[1]
                if number == 1 and not videos and wider and not widened:
                    widened = True
                    number = 0
                    url = search_address(fetcher, template, wider) or url
                    continue
                if number == 1 and not videos and how == "retenue" and not relearned["done"]:
                    # Rien : les mots, ou l'adresse ? Un mot courant le dit.
                    # Cassee, on la reapprend -- une fois -- et l'on reprend.
                    relearned["done"] = True
                    if not template_alive(fetcher, template):
                        with lock:
                            templates.pop(host, None)
                        fresh_template, fresh_how = find_template(fetcher, line, {}, probes)
                        if fresh_template:
                            template, how = fresh_template, fresh_how
                            host = urlparse(split_mode(template)[1]).netloc
                            with lock:
                                templates[host] = template
                            url = search_address(fetcher, template, query) or url
                            number = 0
                            widened = False
                            continue
                if number == 1 and not videos:
                    # La recherche du site marche (on l'a trouvee avec un
                    # mot), elle n'a simplement rien pour ces mots-la : orange,
                    # pas rouge.
                    notes.append(f"« {query} » : aucun résultat sur ce site")
                    usable = True
                    break
                if number == 1:
                    marks = {url_key(v.page_url) for v in videos}
                    # La meme premiere page pour deux recherches differentes, ou
                    # des resultats sans rapport : la recherche n'est pas comprise.
                    if marks and marks in first_pages:
                        notes.append(f"« {query} » : résultats sans rapport avec la "
                                     "recherche (le site ne l'a pas comprise)")
                        with lock:
                            if how == "retenue":
                                templates.pop(host, None)   # a rechercher la prochaine fois
                        break
                    first_pages.append(marks)
                    usable = True
                fresh = 0
                batch = []
                for video in videos:
                    if not seen.claim(video):
                        continue
                    fresh += 1
                    seen_here += 1
                    # Ce que la page de resultats dit deja : duree, titre. La
                    # qualite annoncee ne compte que si l'on ne verifie pas --
                    # sinon c'est celle du vrai fichier qui juge.
                    if not filters.matches_duration(video.duration_s):
                        dropped["durée"] += 1
                        continue
                    if not ffprobe and not filters.matches_height(video.height):
                        dropped["qualité"] += 1
                        continue
                    if not (filters.matches_title(video.title, query) if not widened
                            else has_all_words(video.title, query)):
                        dropped["titre"] += 1
                        continue
                    batch.append(video)
                for video in checked(batch, lambda: kept >= filters.max_per_site or full()):
                    if kept >= filters.max_per_site or full():
                        break
                    if video.media is None:
                        dropped["fichier" if "aperçu" not in video.snippet else "aperçu"] += 1
                        continue
                    if not filters.matches_height(video.height):
                        dropped["qualité"] += 1
                        continue
                    if not filters.matches_duration(video.duration_s):
                        dropped["durée"] += 1
                        continue
                    with lock:
                        if cap and total["kept"] >= cap:
                            break
                        total["kept"] += 1
                    video.query = query
                    kept += 1
                    on_result(video)
                if not fresh and number > 1:
                    break              # plus rien de neuf : fin des resultats
                mode, plain = split_mode(url)
                following = next_page_url(html_text, plain) or numbered_page(plain, number + 1)
                following = ("js:" + following) if mode and following else following
                if following == url:
                    break
                url = following
            kept_site += kept
            if seen_here or kept:
                # Pourquoi les autres ont ete ecartees : « 0 gardee sur 31 »
                # laissait croire que le site ne marchait pas.
                why = [f"{n} {WHY_DROPPED[k]}" for k, n in dropped.items() if n]
                notes.append(f"« {query} »"
                             + (f" (cherché « {wider} », puis tous les mots dans le titre)"
                                if widened else "")
                             + f" : {kept} gardée(s) sur {seen_here} vue(s)"
                             + (f" — écartées : {', '.join(why)}" if why else ""))
        text = " ; ".join(notes) or "aucun résultat"
        if moved_note:
            text = f"{moved_note} ; {text}"
        if kept_site:
            state = "ok"
        elif should_stop():
            state = "arrêté"
        elif usable:
            state = "vide"          # la recherche marche ; rien n'a passe les filtres
        else:
            state = "erreur"        # aucune page de resultats lisible : inutilisable
        on_site(name, state, text)
        on_status(f"{name} — {text}.")

    on_status(f"{len(sites)} site(s), {len(queries)} recherche(s)…")
    with ThreadPoolExecutor(max_workers=min(PARALLEL_SITES, len(sites))) as pool:
        futures = [pool.submit(one_site, line) for line in sites]
        for future in futures:
            try:
                future.result()
            except Exception as exc:                   # noqa: BLE001
                on_status(f"Erreur inattendue : {exc}")
    on_status("Recherche arrêtée." if should_stop() else
              f"Recherche terminée : {total['kept']} résultat(s).")
    return templates
