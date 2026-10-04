"""Aller jusqu'a la video : de la page d'un resultat au fichier entier.

Le parcours de quelqu'un qui regarde : on ouvre la page de la video, on
trouve son lecteur (dans la page, ou integre depuis un autre site), et l'on
prend le fichier qu'il joue -- le plus net, le plus lourd. Les pages portent
aussi des apercus (survol, bande-annonce : quelques secondes, 100 Ko) ; chaque
fichier trouve est donc mesure a distance par ffprobe, sans le telecharger :
duree, hauteur d'image, poids. Seule une video entiere est retenue.

Sans rien de Qt : la recherche s'en sert pour ne montrer que ce qui se
telecharge vraiment, et le telechargement pour prendre le bon fichier.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

# Les certificats de Windows (voir websearch) : yt-dlp et les mesures en ont
# besoin aussi, meme quand ce module sert seul.
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:                                   # noqa: BLE001
    pass

MEDIA_EXT = ("mp4", "m4v", "mkv", "webm", "mov", "m3u8", "mpd", "avi", "wmv", "flv")
_MEDIA_URL = re.compile(
    r"(?:https?:)?//[^\s\"'<>()\\]+?\.(?:" + "|".join(MEDIA_EXT) +
    # « …/634070_360p.mp4/?br=730 » : un « / » apres l'extension (lecteurs KVS).
    r")/?(?:\?[^\s\"'<>\\]*)?(?=[\s\"'<>\\]|$)", re.I)
# Les apercus (survol, bande-annonce, vignette animee) : ce n'est pas la video.
_SMALL = re.compile(r"preview|trailer|teaser|thumb|sprite|hover|_small|"
                    r"[/_-]promo|[/_-]intro|/ads?/|advert", re.I)
_HEIGHT = re.compile(r"(?<!\d)(2160|1440|1080|720|540|480|360|240)p?(?!\d)")
_EXT_SCORE = {"mp4": 3, "m4v": 3, "mkv": 3, "webm": 2, "mov": 2,
              "m3u8": 2.5, "mpd": 1.5}

# Ce que la page declare comme etant SA video (schema.org, Open Graph) : les
# autres fichiers de la page sont souvent ceux des « videos similaires ».
MAIN = 8


def identity(url: str) -> str:
    """Ce qui designe une video, quelle que soit sa qualite :
    « …/8bd14…/stream/720p/index.m3u8 » et « …/8bd14…/stream/1080p/… » sont
    la meme video ; « …/26a6d…/stream/1080p/… » en est une autre."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if parts:
        parts = parts[:-1]                      # le fichier lui-meme
    # Les jetons d'acces (« key=…,end=… ») changent d'une qualite a l'autre.
    parts = [p for p in parts if "=" not in p and "," not in p]
    kept = [p for p in parts if not re.fullmatch(
        r"(\d{3,4}p|hd|sd|high|low|med(ium)?|mobile|stream|hls|dash|mp4)", p, re.I)]
    return parsed.netloc.split(".", 1)[-1] + "/" + "/".join(kept)


# Sans duree annoncee : en dessous, c'est un apercu, pas la video.
MIN_UNKNOWN_S = 20
# Une video entiere dure au moins cette part de la duree annoncee.
FULL_SHARE = 0.6
MAX_PROBES = 8


def _unescape_scripts(text: str) -> str:
    """Les adresses que les lecteurs gardent en JSON : « https:\\/\\/… »."""
    return (text.replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")
            .replace("\\u0026", "&").replace("&amp;", "&"))


def media_candidates(page: str, page_url: str, scored: bool = False) -> list:
    """Les fichiers video d'une page, du plus probable au moins probable,
    en adresses completes. Les apercus passent en dernier. `scored` : des
    paires (adresse, note)."""
    from bs4 import BeautifulSoup

    scores: dict = {}

    def add(raw, bonus: float) -> None:
        raw = html.unescape(str(raw or "")).strip()
        if not raw or raw.startswith(("data:", "blob:", "javascript:")):
            return
        url = urljoin(page_url, raw)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return
        leaf = parsed.path.lower().rstrip("/").rsplit("/", 1)[-1]   # « .mp4/ » aussi
        ext = leaf.rsplit(".", 1)[-1] if "." in leaf else ""
        if bonus >= MAIN and ext not in MEDIA_EXT:
            # « Sa video », selon la page -- mais une page de lecteur
            # (tube8 : « /embed/81924661/ »), pas un fichier : on l'ouvrira
            # comme un lecteur integre (`embedded_players`).
            return
        score = bonus + _EXT_SCORE.get(ext, 0)
        height = _HEIGHT.search(url)
        if height:
            score += int(height.group(1)) / 1000
        if _SMALL.search(url):
            score -= 20
        scores[url] = max(scores.get(url, -99.0), score)

    soup = BeautifulSoup(page, "html.parser")
    for tag in soup.find_all(["video", "source"]):
        for attr in ("src", "data-src", "data-hd-src", "data-video-src"):
            if tag.get(attr):
                add(tag.get(attr), 4)
    for prop in ("og:video", "og:video:url", "og:video:secure_url",
                 "twitter:player:stream"):
        for tag in (soup.find_all("meta", attrs={"property": prop}) +
                    soup.find_all("meta", attrs={"name": prop})):
            add(tag.get("content"), MAIN)
    for tag in soup.find_all(attrs={"itemprop": "contentUrl"}):
        add(tag.get("content") or tag.get("href"), MAIN)
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        for obj in data if isinstance(data, list) else [data]:
            if isinstance(obj, dict) and obj.get("contentUrl"):
                add(obj["contentUrl"], MAIN)
    for tag in soup.find_all("a", href=True):
        if re.search(r"\.(mp4|mkv|webm|mov|m4v)(\?|$)", tag["href"], re.I):
            add(tag["href"], 2)
    for match in _MEDIA_URL.finditer(_unescape_scripts(page)):
        add(match.group(0), 1)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return ranked if scored else [url for url, _score in ranked]


_ADS = re.compile(r"adtng|adserver|doubleclick|exosrv|exoclick|juicyads|trafficjunky|"
                  r"tsyndicate|mavrtrack|popads|popunder|smartpop|banner|/ads?/|"
                  r"googletagmanager|analytics", re.I)
_DEFINITIONS = re.compile(r'"mediaDefinitions?"\s*:\s*(\[.*?\])\s*[,}]', re.S)
_VIDEO_URL = re.compile(r'"videoUrl"\s*:\s*"([^"]+)"')


_KVS_URL = re.compile(r"\b(video_url|video_alt_url\d*)\s*:\s*'([^']+)'")
_KVS_LICENSE = re.compile(r"\blicense_code\s*:\s*'([^']+)'")


def _kvs_token(license_code: str) -> list:
    """La cle que le lecteur KVS tire de `license_code` (meme calcul que lui,
    et que yt-dlp)."""
    license_code = license_code.replace("$", "")
    values = [int(char) for char in license_code]
    modified = license_code.replace("0", "1")
    center = len(modified) // 2
    front, back = int(modified[:center + 1]), int(modified[center:])
    modified = str(4 * abs(front - back))[:center + 1]
    return [(values[index + offset] + current) % 10
            for index, current in enumerate(map(int, modified))
            for offset in range(4)]


def kvs_real_url(video_url: str, license_code: str) -> str:
    """L'adresse que le lecteur KVS joue vraiment. « function/0/… » : le
    lecteur remet dans l'ordre les 32 caracteres de la cle de l'adresse,
    selon `license_code` -- ce que fait le navigateur de chaque visiteur."""
    if not video_url.startswith("function/0/"):
        return video_url
    parsed = urlparse(video_url[len("function/0/"):])
    token = _kvs_token(license_code)
    parts = parsed.path.split("/")
    if len(parts) < 4 or len(parts[3]) < 32:
        return parsed.geturl()
    digest = parts[3][:32]
    order = list(range(32))
    total = 0
    for src in reversed(range(32)):
        total += token[src]
        dest = (src + total) % 32
        order[src], order[dest] = order[dest], order[src]
    parts[3] = "".join(digest[i] for i in order) + parts[3][32:]
    return parsed._replace(path="/".join(parts)).geturl()


def kvs_files(page: str, page_url: str) -> list:
    """Les fichiers du lecteur KVS (zbporn, pornid, sexvid, porntrex…) : son
    bloc « flashvars » donne `video_url` et ses autres qualites."""
    if "flashvars" not in page and "kt_player" not in page:
        return []
    license_match = _KVS_LICENSE.search(page)
    found = []
    for _name, raw in _KVS_URL.findall(page):
        if not re.search(r"\.(mp4|m4v|webm|flv|m3u8)", raw, re.I):
            continue
        url = raw
        if url.startswith("function/0/"):
            if not license_match:
                continue
            url = kvs_real_url(url, license_match.group(1))
        url = urljoin(page_url, url)
        if url not in found:
            found.append(url)
    return found


# Les pages qui sont des albums (plusieurs videos) : tout l'album se telecharge.
ALBUM_PAGES = {"erome.com": re.compile(r"^/a/[A-Za-z0-9]+/?$")}


def is_album_page(page_url: str) -> bool:
    parsed = urlparse(page_url)
    shape = ALBUM_PAGES.get(parsed.netloc.lower().removeprefix("www."))
    return bool(shape and shape.match(parsed.path))


def album_files(page: str, page_url: str) -> list:
    """Les videos d'un album (erome), dans l'ordre, chacune une fois ; [] si
    la page n'est pas un album ou n'en porte qu'une."""
    if not is_album_page(page_url) or not page:
        return []
    found = re.findall(r"<(?:source|video)\b[^>]*?\ssrc=[\"']([^\"']+\.(?:mp4|webm|m4v)[^\"']*)",
                       page, re.I)
    files = list(dict.fromkeys(urljoin(page_url, html.unescape(u)) for u in found))
    return files if len(files) >= 2 else []


def beeg_files(page_url: str, user_agent: str) -> list:
    """La liste HLS (240p a 1080p) d'une video de beeg : sa page n'a rien,
    le lecteur demande l'adresse a son API (« play_url »), et yt-dlp ne sait
    plus la lire. L'identifiant s'ecrit « -0<nombre> » dans l'adresse de la
    page ; l'API veut le nombre seul."""
    parsed = urlparse(page_url)
    match = re.fullmatch(r"/(?:video/)?-?(\d+)/?", parsed.path)
    if not match or parsed.netloc.lower().removeprefix("www.") != "beeg.com":
        return []
    number = int(match.group(1))
    try:
        answer = _session().get(f"https://store.externulls.com/video/play_url/{number}",
                                headers={"User-Agent": user_agent,
                                         "Referer": "https://beeg.com/",
                                         "Origin": "https://beeg.com"}, timeout=10)
    except Exception:                                       # noqa: BLE001
        return []
    text = answer.text.strip() if answer.ok else ""
    if not text or "<" in text or not text.endswith(".m3u8"):
        return []
    return ["https://video.beeg.com/" + text.lstrip("/")]


def family_files(page_url: str, user_agent: str) -> list:
    """Les fichiers d'une video des sites du moteur « txxx » (hclips, txxx,
    bdsmx.tube…) : le lecteur les demande a « /api/videofile.php » et en
    decode les adresses (decodage repris de yt-dlp)."""
    match = re.search(r"/videos?/(\d+)/", urlparse(page_url).path)
    if not match:
        return []
    try:
        answer = _session().get(
            urljoin(page_url, f"/api/videofile.php?video_id={match.group(1)}&lifetime=8640000"),
            headers={"User-Agent": user_agent, "Referer": page_url,
                     "X-Requested-With": "XMLHttpRequest"}, timeout=10)
        data = answer.json() if "json" in answer.headers.get("Content-Type", "") else None
    except Exception:                                       # noqa: BLE001
        return []
    if not isinstance(data, list) or not any(isinstance(v, dict) and v.get("video_url")
                                             for v in data):
        return []
    try:
        from yt_dlp.extractor.txxx import decode_base64
        return [urljoin(page_url, decode_base64(v["video_url"]))
                for v in data if isinstance(v, dict) and v.get("video_url")]
    except Exception:                                       # noqa: BLE001
        return []


def player_files(page: str, page_url: str, user_agent: str) -> list:
    """Les fichiers que decrit le lecteur de la page : son bloc
    « mediaDefinitions » -- des adresses directes, ou des adresses internes
    (« /media/mp4/?s=… ») qui rendent la liste des versions, et que le
    lecteur ouvre avant de jouer (thumbzilla, tube8…) --, ou les
    « flashvars » d'un lecteur KVS."""
    found: list = kvs_files(page, page_url)
    block = _DEFINITIONS.search(page)
    if not block:
        return found
    for raw in _VIDEO_URL.findall(block.group(1))[:4]:
        url = urljoin(page_url, _unescape_scripts(raw))
        leaf = urlparse(url).path.lower().rsplit("/", 1)[-1]
        if "." in leaf and leaf.rsplit(".", 1)[-1] in MEDIA_EXT:
            found.append(url)
            continue
        try:
            answer = _session().get(url, headers={"User-Agent": user_agent,
                                                  "Referer": page_url}, timeout=10)
        except Exception:                                   # noqa: BLE001
            continue
        if "json" not in answer.headers.get("Content-Type", ""):
            continue
        for inner in _VIDEO_URL.findall(answer.text):
            inner = urljoin(url, _unescape_scripts(inner))
            if inner not in found:
                found.append(inner)
    return found


def embedded_players(page: str, page_url: str) -> list:
    """Les lecteurs d'autres pages integres a celle-ci : les iframes, et la
    page de lecteur que la page declare comme sa video (og:video, embedUrl)
    quand ce n'est pas un fichier."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page, "html.parser")
    found = []

    def add(raw) -> None:
        url = urljoin(page_url, html.unescape(str(raw or "")).strip())
        leaf = urlparse(url).path.lower().rsplit("/", 1)[-1]
        ext = leaf.rsplit(".", 1)[-1] if "." in leaf else ""
        if (urlparse(url).scheme in ("http", "https") and url not in found
                and url.split("#")[0] != page_url.split("#")[0] and ext not in MEDIA_EXT):
            found.append(url)

    for prop in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player"):
        for tag in (soup.find_all("meta", attrs={"property": prop}) +
                    soup.find_all("meta", attrs={"name": prop})):
            add(tag.get("content"))
    for tag in soup.find_all(attrs={"itemprop": "embedUrl"}):
        add(tag.get("content") or tag.get("href"))
    players = []
    for tag in soup.find_all("iframe"):
        before = len(found)
        add(tag.get("src") or tag.get("data-src") or "")
        if len(found) > before:
            players.append((found.pop(), tag))
    # Les iframes : les lecteurs d'abord (plein ecran permis, « embed »,
    # « player », « video » dans l'adresse), les publicites a la fin.
    def worth(pair) -> int:
        url, tag = pair
        score = 2 if tag.has_attr("allowfullscreen") else 0
        score += 1 if re.search(r"embed|player|video", url, re.I) else 0
        score -= 5 if _ADS.search(url) else 0
        return score
    found += [url for url, _tag in sorted(players, key=worth, reverse=True)
              if not _ADS.search(url)]
    return found


def page_title(page: str) -> str:
    """Le titre de la video, pour nommer le fichier."""
    for pattern in (r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)",
                    r"<title[^>]*>([^<]+)</title>"):
        match = re.search(pattern, page, re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def ytdlp_media(page_url: str, expect_s=None, cookies_browser: str = "",
                ffprobe: str = "") -> tuple:
    """(Media, raison) par yt-dlp, qui connait des centaines de sites : leurs
    lecteurs, leurs adresses codees (KVS, « mediaDefinitions »…). La ou la
    lecture de la page ne trouvait qu'un apercu, il trouve souvent la video
    entiere (redtube, youporn en 1080p, eporner, hclips…)."""
    try:
        import yt_dlp
    except ImportError:
        return None, "yt-dlp absent"

    class Quiet:
        def debug(self, _msg):
            pass
        warning = error = info = debug

    options = {"quiet": True, "no_warnings": True, "skip_download": True,
               "logger": Quiet(), "socket_timeout": 20, "noplaylist": True,
               "format": "bv*+ba/b/bv*/b*",
               "format_sort": ["res", "fps", "br", "size"]}
    if cookies_browser:
        options["cookiesfrombrowser"] = (cookies_browser,)
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(page_url, download=False)
    except Exception as exc:                                    # noqa: BLE001
        return None, "yt-dlp : " + str(exc).strip().splitlines()[-1][:120]
    if not info or info.get("_type") == "playlist":
        return None, "yt-dlp : pas une vidéo seule"
    if info.get("is_live"):
        return None, "un direct, pas une vidéo à télécharger"
    parts = info.get("requested_formats") or [info]
    video = next((f for f in parts if f.get("vcodec") not in (None, "none")), parts[0])
    duration = float(info.get("duration") or 0)
    height = int(video.get("height") or info.get("height") or 0)
    size = sum(int(f.get("filesize") or f.get("filesize_approx") or 0) for f in parts)
    if not size and duration:
        rate = sum(float(f.get("tbr") or 0) for f in parts)
        size = int(rate * 1000 / 8 * duration)
    url = video.get("url") or info.get("url") or ""
    if not url:
        return None, "yt-dlp : aucun fichier"
    if not duration:
        # yt-dlp ne dit pas la duree : on mesure le fichier lui-meme -- sans
        # cela, un apercu (ou rien) passait pour une video entiere (« 0 s »).
        headers = video.get("http_headers") or {}
        measured = probe_remote(url, headers.get("Referer") or page_url, ffprobe,
                                headers.get("User-Agent") or "Mozilla/5.0")
        if not measured:
            return None, "yt-dlp : durée inconnue, fichier non mesurable"
        duration = measured["duration"]
        height = height or measured["height"]
        size = size or measured["size"]
    elif not height or not size:
        # La duree est connue, pas la qualite ni le poids (txxx) : on les
        # mesure, pour la carte ; faute de mieux, on garde ce qu'on sait.
        headers = video.get("http_headers") or {}
        measured = probe_remote(url, headers.get("Referer") or page_url, ffprobe,
                                headers.get("User-Agent") or "Mozilla/5.0")
        if measured:
            height = height or measured["height"]
            size = size or measured["size"]
    if not is_full(duration, expect_s):
        return None, f"seulement un aperçu ({int(duration)} s)"
    media = Media(url, page_url, duration, height, size, info.get("title") or "")
    media.format_id = info.get("format_id") or ""
    return media, ""


@dataclass
class Media:
    """Le fichier d'une video, mesure : de quoi la telecharger telle quelle."""
    url: str
    referer: str
    duration: float
    height: int
    size: int
    title: str = ""
    # Quand on l'a trouve : ces adresses portent souvent une cle qui expire.
    found_at: float = field(default_factory=time.time)
    # Trouve par yt-dlp : c'est lui qui le telechargera (sa page, ce format).
    format_id: str = ""
    # L'image de la page de la video : pour une carte arrivee sans vignette.
    poster: str = ""


_LOCAL = threading.local()


def safe_session():
    """Une session dont les en-tetes acceptent toute adresse : une page au
    nom en arabe ou en cyrillique, donnee comme « Referer », faisait echouer
    la demande (les en-tetes n'admettent que le latin-1)."""
    import requests
    from requests.utils import requote_uri

    class _Session(requests.Session):
        def request(self, method, url, **kwargs):
            headers = kwargs.get("headers")
            if headers:
                kwargs["headers"] = {k: (requote_uri(v) if isinstance(v, str)
                                         and not v.isascii() else v)
                                     for k, v in headers.items()}
            return super().request(method, url, **kwargs)
    return _Session()


def _session():
    """Une connexion par fil, gardee ouverte : chaque mesure en rouvrait une
    (poignee de main chiffree comprise), tres lent derriere un VPN."""
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = _LOCAL.session = safe_session()
    return session


def _boxes(data: bytes, start: int = 0, end: int | None = None):
    """Les boites MP4 (taille, type, debut du contenu, fin) d'un morceau lu."""
    end = len(data) if end is None else end
    at = start
    while at + 8 <= end:
        size = int.from_bytes(data[at:at + 4], "big")
        kind = data[at + 4:at + 8]
        head = 8
        if size == 1 and at + 16 <= end:
            size = int.from_bytes(data[at + 8:at + 16], "big")
            head = 16
        elif size == 0:
            size = end - at
        if size < head:
            return
        yield kind, at + head, at + size
        at += size


def _moov_facts(moov: bytes) -> dict | None:
    """Duree et hauteur d'image lues dans la boite « moov »."""
    duration, height = 0.0, 0
    for kind, body, stop in _boxes(moov):
        if kind == b"mvhd":
            version = moov[body]
            if version == 1:
                scale = int.from_bytes(moov[body + 20:body + 24], "big")
                length = int.from_bytes(moov[body + 24:body + 32], "big")
            else:
                scale = int.from_bytes(moov[body + 12:body + 16], "big")
                length = int.from_bytes(moov[body + 16:body + 20], "big")
            duration = length / scale if scale else 0.0
        elif kind == b"trak":
            track_height, is_video = 0, False
            for inner, ibody, istop in _boxes(moov, body, stop):
                if inner == b"tkhd":
                    track_height = int.from_bytes(moov[istop - 4:istop], "big") >> 16
                elif inner == b"mdia":
                    for leaf, lbody, _lstop in _boxes(moov, ibody, istop):
                        if leaf == b"hdlr" and moov[lbody + 8:lbody + 12] == b"vide":
                            is_video = True
            if is_video:
                height = max(height, track_height)
    return {"duration": duration, "height": height} if duration else None


def mp4_facts(url: str, referer: str, user_agent: str, timeout: float = 10.0) -> dict | None:
    """Duree, hauteur et poids d'un MP4 distant, lus comme le ferait le
    lecteur : l'en-tete du fichier (quelques Ko), ou sa fin quand l'en-tete y
    est range. Par de simples demandes web -- certains serveurs (pisshamster)
    refusent ffprobe (403) et acceptent le navigateur."""
    import requests
    headers = {"User-Agent": user_agent, "Referer": referer}

    def part(first: int, last: int) -> tuple:
        with _session().get(url, headers=dict(headers, Range=f"bytes={first}-{last}"),
                            timeout=timeout, stream=True) as resp:
            if resp.status_code not in (200, 206):
                return b"", 0
            total = resp.headers.get("Content-Range", "").rsplit("/", 1)[-1]
            if resp.status_code == 200 and first > 0:
                # Le serveur ne sert pas de morceaux : il renvoie le debut du
                # fichier, pas l'endroit demande. On s'arrete (ffprobe prendra
                # le relais) -- sinon on relirait le debut en boucle.
                return b"", 0
            if resp.status_code == 200:              # pas de morceaux : on lit le debut
                data, want = b"", last - first + 1
                for chunk in resp.iter_content(65536):
                    data += chunk
                    if len(data) >= want:
                        break
                return data[:want], int(resp.headers.get("Content-Length") or 0)
            return resp.content, int(total) if total.isdigit() else 0

    try:
        head, total = part(0, 262_143)
        if head[4:8] not in (b"ftyp", b"moov", b"free", b"skip", b"wide", b"mdat"):
            return None                          # pas un MP4
        at = 0
        steps = 0
        while (at < total or (not total and at < len(head))) and steps < 20:
            steps += 1                           # un MP4 n'a que quelques blocs
            if at + 16 > len(head):
                head_part, _t = part(at, at + 15)
                if len(head_part) < 8:
                    return None
                size = int.from_bytes(head_part[:4], "big")
                kind = head_part[4:8]
                if size == 1:
                    size = int.from_bytes(head_part[8:16], "big")
                block = head_part
                base = at
            else:
                size = int.from_bytes(head[at:at + 4], "big")
                kind = head[at + 4:at + 8]
                if size == 1:
                    size = int.from_bytes(head[at + 8:at + 16], "big")
                block, base = head, 0
            if size < 8:
                return None
            if kind == b"moov":
                if size > 40_000_000:
                    return None
                if base == 0 and at + size <= len(head):
                    moov = head[at + 8:at + size]
                else:
                    moov, _t = part(at + 8, at + size - 1)
                facts = _moov_facts(moov)
                if facts:
                    facts["size"] = total
                return facts
            at += size
            del block
    except requests.RequestException:
        return None
    return None


def probe_remote(url: str, referer: str, ffprobe: str, user_agent: str,
                 timeout: float = 10.0) -> dict | None:
    """Duree, hauteur et poids d'un fichier video distant, lus par ffprobe
    sans le telecharger (il ne lit que l'en-tete). None s'il ne lit rien.

    Dix secondes au plus : un fichier se mesure en une ou deux, et un direct
    (une webcam) ne finit jamais -- il retenait chaque resultat trente
    secondes, pour rien a telecharger."""
    if ".m3u8" not in url.lower() and ".mpd" not in url.lower():
        # Un fichier : on le lit soi-meme, comme un lecteur (plus rapide, et
        # accepte la ou ffprobe est refuse).
        facts = mp4_facts(url, referer, user_agent, timeout)
        if facts:
            return facts
    elif ".m3u8" in url.lower():
        # Une liste HLS : ffprobe ouvrait chacune de ses versions (vingt
        # secondes pour beeg) ; la liste dit deja tout de la meilleure.
        facts = hls_measure(url, referer, user_agent, timeout)
        if facts:
            return facts
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-user_agent", user_agent,
             "-headers", f"Referer: {referer}\r\n", "-rw_timeout", str(int(timeout * 1e6)),
             "-show_entries", "format=duration,size:stream=codec_type,height",
             "-of", "json", url],
            capture_output=True, timeout=timeout + 5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(out.stdout or b"{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    streams = data.get("streams") or []
    if not any(s.get("codec_type") == "video" for s in streams):
        return None
    form = data.get("format") or {}
    try:
        duration = float(form.get("duration") or 0)
        size = int(form.get("size") or 0)
    except ValueError:
        duration, size = 0.0, 0
    height = max((int(s.get("height") or 0) for s in streams), default=0)
    if ".m3u8" in url.lower():
        # Un flux (HLS) : un direct n'est pas une video a telecharger -- ses
        # vingt secondes en cours passaient pour une video (stripchat). Et
        # son poids ne s'annonce pas : on l'estime par son debit.
        live, rate = hls_facts(url, referer, user_agent)
        if live:
            return None
        # ffprobe donne ici le poids de la liste (254 octets), pas de la video.
        size = int(rate / 8 * duration) if rate else 0
    return {"duration": duration, "height": height, "size": size}


def hls_measure(url: str, referer: str, user_agent: str, timeout: float = 10.0) -> dict | None:
    """Duree, hauteur et poids estime de la meilleure version d'une liste
    HLS, lus dans les listes elles-memes (la duree : la somme des morceaux).
    None pour un direct, ou si la liste ne dit pas sa hauteur."""
    import requests
    headers = {"User-Agent": user_agent, "Referer": referer}
    try:
        text = _session().get(url, headers=headers, timeout=timeout).text[:300_000]
        height = rate = 0
        if "#EXT-X-STREAM-INF" in text:
            best, lines = None, text.splitlines()
            for at, line in enumerate(lines):
                if not line.startswith("#EXT-X-STREAM-INF"):
                    continue
                found = re.search(r"RESOLUTION=\d+x(\d+)", line)
                band = re.search(r"[^-]BANDWIDTH=(\d+)", line) or re.search(r"BANDWIDTH=(\d+)", line)
                target = next((l.strip() for l in lines[at + 1:]
                               if l.strip() and not l.startswith("#")), "")
                rank = (int(found.group(1)) if found else 0, int(band.group(1)) if band else 0)
                if target and (best is None or rank > best[0]):
                    best = (rank, target)
            if best is None:
                return None
            (height, rate), variant = best
            text = _session().get(urljoin(url, variant), headers=headers,
                                  timeout=timeout).text[:2_000_000]
    except requests.RequestException:
        return None
    if "#EXT-X-ENDLIST" not in text or not height:
        return None
    duration = sum(float(d) for d in re.findall(r"#EXTINF:\s*([\d.]+)", text))
    if duration <= 0:
        return None
    return {"duration": duration, "height": height, "size": int(rate * duration / 8)}


def hls_facts(url: str, referer: str, user_agent: str) -> tuple:
    """(direct ?, debit le plus haut en bits/s) d'un flux HLS. Une liste qui
    ne finit pas (sans « #EXT-X-ENDLIST ») est un direct."""
    import requests
    headers = {"User-Agent": user_agent, "Referer": referer}
    try:
        text = _session().get(url, headers=headers, timeout=10).text[:300_000]
        rates = [int(r) for r in re.findall(r"BANDWIDTH=(\d+)", text)]
        if "#EXT-X-STREAM-INF" in text:
            # La liste des versions : on ouvre la premiere pour voir si elle finit.
            variant = next((line.strip() for line in text.splitlines()
                            if line.strip() and not line.startswith("#")), "")
            if variant:
                text = _session().get(urljoin(url, variant), headers=headers,
                                      timeout=10).text[:300_000]
    except requests.RequestException:
        return False, 0
    return "#EXT-X-ENDLIST" not in text, max(rates, default=0)


def is_full(duration: float, expect_s) -> bool:
    """La video entiere : entre 60 % et 150 % de la duree annoncee (ou au
    moins 20 s sans duree annoncee)."""
    if expect_s:
        expect = float(expect_s)
        # Ni un apercu (trop court), ni une autre video (bien plus longue).
        # Large vers le haut : les durees affichees sont souvent arrondies ou
        # fausses (xnxx annonce 5 min pour 8) ; le regroupement par video
        # evite deja de prendre la voisine.
        return FULL_SHARE * expect <= duration <= 3 * expect + 60
    return duration >= MIN_UNKNOWN_S


def best_media(page: str, page_url: str, ffprobe: str, user_agent: str,
               expect_s=None, heard=None) -> tuple:
    """(Media, raison) : le meilleur fichier entier d'une page deja lue.

    Tous les fichiers trouves sont mesures (en parallele) ; on garde les
    videos entieres, et parmi elles la plus haute image, puis la plus lourde.
    """
    # `heard` : les fichiers que le lecteur a demandes en jouant -- ce que la
    # page joue vraiment, ils passent avant tout.
    ranked = ([(url, MAIN) for url in dict.fromkeys(heard)] if heard
              else media_candidates(page, page_url, scored=True))
    if not heard:
        # Le lecteur decrit ses fichiers par des adresses internes (le bloc
        # « mediaDefinitions ») : on les ouvre comme lui, et la liste
        # qu'elles rendent (1080p, 720p…) passe devant tout le reste.
        declared = [(url, MAIN + 1) for url in player_files(page, page_url, user_agent)]
        ranked = declared + [pair for pair in ranked if pair[0] not in dict(declared)]
    if not ranked:
        return None, "aucun fichier vidéo sur la page"
    # Les fichiers, regroupes par video (ses differentes qualites). La page
    # porte souvent les flux de ses « videos similaires » : prendre le plus
    # beau fichier de la page, c'etait parfois telecharger une autre video.
    groups: dict = {}
    for url, score in ranked:
        # Ce que le lecteur liste lui-meme (MAIN + 1) : toutes des versions de
        # la video de la page, quelle que soit leur adresse (flux 720p et
        # fichier 1080p) -- comparees ensemble, la meilleure gagne.
        key = "lecteur" if score > MAIN else identity(url)
        groups.setdefault(key, []).append((url, score))
    order = list(groups)                    # du groupe le mieux place au moins bien
    main = [key for key in order if max(sc for _u, sc in groups[key]) >= MAIN]
    # La page dit quelle est sa video : on s'y tient, meme si elle n'est
    # qu'en 720p et qu'une voisine est en 1080p.
    order = main[:1] if main else [key for key in order
                                   if max(sc for _u, sc in groups[key]) > 0][:3]
    if not order:
        order = list(groups)[:1]
    measured = []
    for key in order:
        urls = [u for u, _s in groups[key]]
        if key == "lecteur":
            # Le lecteur dit la qualite de chaque version (« 1080P_4000K ») :
            # on ne mesure que les deux meilleures, le fichier avant le flux.
            # Toutes mesurees, c'etait trente secondes par video.
            def rank(url: str) -> tuple:
                found = _HEIGHT.search(url)
                return (-(int(found.group(1)) if found else 0), ".m3u8" in url.lower())
            urls = sorted(urls, key=rank)[:2]
        urls = urls[:MAX_PROBES]
        with ThreadPoolExecutor(max_workers=min(4, len(urls))) as pool:
            here = list(pool.map(
                lambda url: (url, probe_remote(url, page_url, ffprobe, user_agent)), urls))
        measured += here
        if any(m and is_full(m["duration"], expect_s) for _u, m in here):
            break
    full = [(url, m) for url, m in measured if m and is_full(m["duration"], expect_s)]
    if not full:
        read = [m for _u, m in measured if m]
        if read:
            longest = max(m["duration"] for m in read)
            if expect_s and longest > FULL_SHARE * float(expect_s):
                return None, (f"la vidéo trouvée ({int(longest)} s) ne correspond pas à "
                              f"la durée annoncée ({int(expect_s)} s)")
            return None, f"seulement un aperçu ({int(longest)} s)"
        return None, "les fichiers vidéo de la page ne se lisent pas"
    url, m = max(full, key=lambda pair: (pair[1]["height"], pair[1]["size"],
                                         pair[1]["duration"]))
    return Media(url, page_url, m["duration"], m["height"], m["size"],
                 page_title(page)), ""


def resolve(get_page, page_url: str, ffprobe: str, user_agent: str,
            expect_s=None, sniff=None, origin: str = "") -> tuple:
    """(Media, raison) : le parcours jusqu'a la video. La page d'abord ; si
    elle n'a que des apercus ou rien, les lecteurs qu'elle integre.

    `get_page(url)` rend (texte, raison), comme `websearch.Fetcher.get`.
    """
    # On ouvre le resultat comme en cliquant depuis la page ou on l'a trouve :
    # bigporn ne mene a la video (sur le site qui l'heberge) qu'a ce prix.
    if origin:
        try:
            page, why = get_page(page_url, referer=origin)
        except TypeError:
            page, why = get_page(page_url)
    else:
        page, why = get_page(page_url)
    if page is None:
        return None, why
    page_url = real_address(page, page_url)
    for _ in range(2):
        # Une page de relais (bigporn : un formulaire qui s'envoie tout seul,
        # 572 octets) : on la suit comme le navigateur, jusqu'a la vraie page.
        onward = relay_target(page, page_url)
        if not onward:
            break
        next_page, _why = get_page(onward)
        if next_page is None:
            break
        page, page_url = next_page, real_address(next_page, onward)
    media, why = _resolve_page(get_page, page, page_url, ffprobe, user_agent, expect_s, sniff)
    if media is not None and not media.poster:
        media.poster = page_poster(page, page_url)
    return media, why


def real_address(page: str, page_url: str) -> str:
    """L'adresse reelle d'une page (« canonical », og:url) : apres une
    redirection vers un autre site, les liens de la page se lisent depuis
    elle, pas depuis l'adresse demandee."""
    for pattern in (r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)",
                    r"<meta[^>]+property=[\"']og:url[\"'][^>]+content=[\"']([^\"']+)"):
        match = re.search(pattern, page[:60000], re.I)
        if match:
            found = urljoin(page_url, html.unescape(match.group(1)))
            if urlparse(found).scheme in ("http", "https"):
                return found
    return page_url


def relay_target(page: str, page_url: str) -> str:
    """L'adresse ou mene une page de relais -- une courte page qui ne fait
    que renvoyer ailleurs : formulaire envoye par script, « refresh »,
    « location.href = … ». "" pour une page ordinaire."""
    if len(page) > 6000:
        return ""
    from urllib.parse import urlencode
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page, "html.parser")
    meta = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
    if meta and "url=" in (meta.get("content") or "").lower():
        target = meta["content"].split("=", 1)[1].strip(" '\"")
        return urljoin(page_url, target)
    form = soup.find("form")
    if form is not None and re.search(r"\.submit\(\)", page) and \
            (form.get("method") or "get").lower() == "get":
        fields = [(tag.get("name"), tag.get("value") or "")
                  for tag in form.find_all("input") if tag.get("name")]
        action = urljoin(page_url, form.get("action") or page_url)
        return action + ("&" if "?" in action else "?") + urlencode(fields)
    moved = re.search(r"(?:window\.)?location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]"
                      r"|location\.replace\(\s*['\"]([^'\"]+)['\"]", page)
    if moved:
        return urljoin(page_url, moved.group(1) or moved.group(2))
    return ""


def page_poster(page: str, page_url: str) -> str:
    """L'image que la page donne a sa video (og:image)."""
    match = re.search(r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)",
                      page, re.I)
    return urljoin(page_url, html.unescape(match.group(1))) if match else ""


def _resolve_page(get_page, page: str, page_url: str, ffprobe: str, user_agent: str,
                  expect_s=None, sniff=None) -> tuple:
    media, why = best_media(page, page_url, ffprobe, user_agent, expect_s)
    if media is not None:
        return media, ""
    for player in embedded_players(page, page_url)[:3]:
        try:
            inner, _why = get_page(player, referer=page_url)
        except TypeError:                     # un lecteur de pages sans Referer
            inner, _why = get_page(player)
        if inner is None:
            continue
        found, inner_why = best_media(inner, player, ffprobe, user_agent, expect_s)
        if found is not None:
            found.title = found.title or page_title(page)
            return found, ""
        why = inner_why if "aperçu" in inner_why else why
    # Le moteur de hclips, txxx, bdsmx.tube… : son lecteur demande le fichier a
    # « /api/videofile.php » ; yt-dlp sait decoder la reponse, mais ne connait
    # pas tous les sites de la famille.
    family = family_files(page_url, user_agent) or beeg_files(page_url, user_agent)
    if family:
        found, family_why = best_media(page, page_url, ffprobe, user_agent, expect_s,
                                       heard=family)
        if found is not None:
            found.title = found.title or page_title(page)
            return found, ""
        why = family_why if "aperçu" in family_why else why
    # yt-dlp connait le lecteur de bien des sites : on le lui demande avant
    # d'ouvrir la page dans le navigateur invisible (plus long).
    found, ytdlp_why = ytdlp_media(page_url, expect_s, ffprobe=ffprobe)
    if found is not None:
        found.title = found.title or page_title(page)
        return found, ""
    if "aperçu" in ytdlp_why and "aperçu" not in why:
        why = ytdlp_why
    # Rien de lisible dans le texte de la page (adresse cachee, construite
    # par un script) : on l'ouvre dans le navigateur invisible, on lance la
    # lecture, et l'on prend ce que le lecteur charge -- comme le font les
    # extensions de telechargement video.
    if sniff is not None:
        heard, _html, heard_why = sniff(page_url)
        if heard:
            found, sniff_why = best_media(page, page_url, ffprobe, user_agent,
                                          expect_s, heard=heard)
            if found is not None:
                found.title = found.title or page_title(page)
                return found, ""
            why = sniff_why if "aperçu" in sniff_why or "durée" in sniff_why else why
    return None, why
