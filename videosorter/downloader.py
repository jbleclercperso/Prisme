"""Telecharger une video trouvee sur le web, directement dans la collection.

Tout passe par yt-dlp, l'outil de reference : il sait lire des centaines de
sites de videos, choisit le meilleur format et fait assembler par ffmpeg
l'image et le son quand le site les sert a part. Les fichiers arrivent dans le
dossier choisi ; Prisme les voit a la prochaine analyse.

Les sites qui demandent une verification d'age (ou une connexion) : yt-dlp
peut reprendre les cookies du navigateur ou l'on a deja passe cette
verification (option `browser`). Rien d'autre n'est contourne.

Quand yt-dlp ne connait pas le site, il ne trouvait rien (« No video formats
found ») ou enregistrait la page elle-meme. Prisme lit alors la page comme le
ferait le navigateur -- balises <video>, adresses que le lecteur garde dans
ses scripts, lecteur integre d'un autre site -- et telecharge le fichier video
lui-meme, en se presentant comme venant de la page. Ce qui n'est pas une
video (une page HTML) est refuse et efface.
"""
from __future__ import annotations

import itertools
import json
import time
import queue
import re
import threading
from pathlib import Path

try:
    from PySide6.QtCore import QObject, Signal
except ImportError:
    # Sur le NAS, sans Qt (`liens.py`) : les memes signaux, en simples
    # rappels, appeles dans le fil qui les emet.
    class _Bound:
        def __init__(self):
            self.slots = []

        def connect(self, slot) -> None:
            self.slots.append(slot)

        def emit(self, *args) -> None:
            for slot in list(self.slots):
                slot(*args)

    class Signal:                                   # noqa: D101
        def __init__(self, *_types):
            self.name = ""

        def __set_name__(self, _owner, name):
            self.name = name

        def __get__(self, obj, _owner=None):
            if obj is None:
                return self
            return obj.__dict__.setdefault("_signal_" + self.name, _Bound())

    class QObject:                                  # noqa: D101
        def __init__(self, parent=None):
            pass

try:
    from .websearch import USER_AGENT
except ImportError:
    # Le NAS n'a pas BeautifulSoup : seule l'identite du navigateur servait.
    USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Au plus deux telechargements a la fois : un NAS et une connexion ont leurs
# limites, et les sites aussi.
PARALLEL = 2
# Une adresse de fichier trouvee il y a plus longtemps est recherchee de
# nouveau avant le telechargement : ces adresses portent souvent une cle qui
# expire.
MEDIA_FRESH_S = 600


def is_stream(url: str) -> bool:
    """Un flux (liste de morceaux) : c'est ffmpeg, via yt-dlp, qui l'assemble."""
    low = url.lower().split("?", 1)[0]
    return low.endswith((".m3u8", ".mpd")) or ".m3u8" in low


def available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def explain(error: str) -> str:
    """Un message de yt-dlp, dit simplement."""
    low = (error or "").lower()
    if "unsupported url" in low:
        return "site non reconnu par l'outil de téléchargement"
    if "no video formats" in low or "requested format is not available" in low:
        return "aucun format vidéo reconnu par l'outil de téléchargement"
    if "age" in low and ("confirm" in low or "restricted" in low or "verif" in low):
        return ("vérification d'âge demandée : choisissez votre navigateur dans "
                "« Cookies du navigateur », après avoir passé la vérification sur le site")
    if "sign in" in low or "login" in low or "log in" in low or "private" in low:
        return ("le site demande d'être connecté : choisissez votre navigateur dans "
                "« Cookies du navigateur »")
    if "cookie" in low and ("decrypt" in low or "could not copy" in low or "dpapi" in low):
        return ("cookies du navigateur illisibles : fermez-le, ou essayez Firefox "
                "(Chrome et Edge chiffrent désormais leurs cookies)")
    if "drm" in low:
        return "vidéo protégée (DRM) : elle ne se télécharge pas"
    if "http error 403" in low:
        return "le site refuse le téléchargement (HTTP 403)"
    if "http error 400" in low:
        return "le site refuse la demande de téléchargement (HTTP 400)"
    if "http error 404" in low or "not found" in low:
        return "vidéo introuvable (retirée ?)"
    if "ffmpeg" in low:
        return "assemblage impossible : ffmpeg introuvable"
    return (error or "échec").strip().splitlines()[-1][:300]


# Les outils pour trouver le fichier dans une page vivent dans `mediafind`
# (sans Qt : la recherche s'en sert aussi, pour ne montrer que ce qui se
# telecharge vraiment).
from .mediafind import (  # noqa: E402,F401
    MIN_UNKNOWN_S, Media, album_files, embedded_players, is_album_page, is_full,
    media_candidates, page_title, resolve, safe_session,
)


def is_page(path) -> bool:
    """Une page HTML (ou un message d'erreur) enregistree sous un nom de video."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(1024)
    except OSError:
        return True
    return head.lstrip()[:1] in (b"<", b"{", b"[") or not head


def is_video_file(path) -> bool:
    """A defaut de ffprobe : pas une page, et d'une taille de video."""
    try:
        return not is_page(path) and Path(path).stat().st_size >= 32 * 1024
    except OSError:
        return False


def probe(path, ffprobe: str) -> dict | None:
    """{"duration": s, "video": bool} lus par ffprobe ; None s'il ne lit rien."""
    import subprocess
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries",
             "format=duration:stream=codec_type", "-of", "json", str(path)],
            capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(out.stdout or b"{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    streams = data.get("streams") or []
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except ValueError:
        duration = 0.0
    if not streams:
        return None
    return {"duration": duration,
            "video": any(s.get("codec_type") == "video" for s in streams)}


def human_seconds(seconds) -> str:
    seconds = int(round(seconds or 0))
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def _safe_name(title: str) -> str:
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1f]+", " ", title or "").strip(" .")
    return re.sub(r"\s+", " ", name)[:150] or "video"


class _Silent:
    """yt-dlp ecrit ses erreurs dans la console meme en mode discret ; elles
    reviennent de toute facon en exception, et on les dit a l'ecran."""

    def debug(self, _msg):
        pass

    warning = error = info = debug


class Downloader(QObject):
    """Une file de telechargements, hors du fil de l'interface."""

    progress = Signal(int, float, str)      # travail, 0..1 (ou -1), texte
    finished = Signal(int, str, str)        # travail, fichier, erreur

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: queue.Queue = queue.Queue()
        self._ids = itertools.count(1)
        self._cancelled: set = set()
        self._workers: list = []
        self._expect: dict = {}                 # travail -> duree annoncee (s)
        self._media: dict = {}                  # travail -> fichier deja trouve
        self._origin: dict = {}                 # travail -> page de resultats d'origine
        # Ou en est chaque telechargement (octets recus, octets en tout) et
        # ceux qui viennent de finir : le recapitulatif de la barre de Prisme.
        self._sizes: dict = {}                  # travail -> [recus, total]
        self._live: set = set()
        self._ended: list = []                  # (heure, reussi ?)
        self._sizes_lock = threading.Lock()
        self.ffmpeg = ""
        # Le navigateur invisible (webpage.PageReader) : pour ecouter ce que
        # le lecteur charge quand la page ne dit rien. Facultatif.
        self.renderer = None

    @property
    def ffprobe(self) -> str:
        """ffprobe, a cote de ffmpeg."""
        import shutil
        if self.ffmpeg:
            beside = Path(self.ffmpeg).with_name("ffprobe" + Path(self.ffmpeg).suffix)
            if beside.exists():
                return str(beside)
        return shutil.which("ffprobe") or ""

    def add(self, url: str, folder: str, browser: str = "",
            expect_s: int | None = None, media: Media | None = None,
            origin: str = "") -> int:
        """Met une video en file ; rend son numero. `expect_s` : sa duree
        annoncee par le site, pour ne pas garder un simple apercu. `media` :
        le fichier deja trouve et mesure par la recherche (le meilleur)."""
        job = next(self._ids)
        with self._sizes_lock:
            self._live.add(job)
            self._sizes[job] = [0, int(getattr(media, "size", 0) or 0)]
        self._expect[job] = expect_s
        self._media[job] = media
        self._origin[job] = origin
        self._jobs.put((job, url, folder, browser))
        self._workers = [w for w in self._workers if w.is_alive()]
        if len(self._workers) < PARALLEL:
            worker = threading.Thread(target=self._run, name="prisme-telechargement",
                                      daemon=True)
            self._workers.append(worker)
            worker.start()
        return job

    def cancel(self, job: int) -> None:
        self._cancelled.add(job)

    def _run(self) -> None:
        while True:
            try:
                job, url, folder, browser = self._jobs.get(timeout=2)
            except queue.Empty:
                return
            if job in self._cancelled:
                self._emit_done(job, "", "annulé")
                continue
            self._download(job, url, folder, browser)

    def _emit(self, job: int, fraction: float, text: str) -> None:
        try:
            self.progress.emit(job, fraction, text)
        except RuntimeError:
            pass                                # la fenetre est fermee

    def _measure(self, job: int, done: int, total: int) -> None:
        with self._sizes_lock:
            if job in self._live:
                known = self._sizes.setdefault(job, [0, 0])
                known[0] = int(done)
                known[1] = int(total) or known[1]

    def summary(self, recent_s: float = 60.0) -> dict:
        """Les telechargements en cours (octets recus et en tout, pour ceux
        dont on connait le poids), et ceux finis depuis `recent_s` secondes."""
        now = time.time()
        with self._sizes_lock:
            live = [self._sizes.get(job, [0, 0]) for job in self._live]
            self._ended = [e for e in self._ended if now - e[0] < recent_s]
            ended = list(self._ended)
        return {"active": len(live),
                "done": sum(d for d, _t in live),
                "total": sum(t for _d, t in live),
                "unknown": sum(1 for _d, t in live if not t),
                "finished": sum(1 for _at, ok in ended if ok),
                "failed": sum(1 for _at, ok in ended if not ok)}

    def _emit_done(self, job: int, path: str, error: str) -> None:
        with self._sizes_lock:
            if job in self._live:
                self._live.discard(job)
                self._sizes.pop(job, None)
                if error != "annulé":
                    self._ended.append((time.time(), bool(path)))
        try:
            self.finished.emit(job, path, error)
        except RuntimeError:
            pass

    def _download(self, job: int, url: str, folder: str, browser: str) -> None:
        """Le parcours jusqu'a la video, puis son meilleur fichier.

        yt-dlp passait en premier : pour bien des sites, il ne trouvait rien
        (« No video formats found ») ou prenait un apercu de quelques
        secondes. On suit desormais le chemin de la page -- son lecteur, ses
        fichiers, mesures un a un -- et l'on prend la video entiere la plus
        nette et la plus lourde. yt-dlp reste le dernier recours."""
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return self._emit_done(job, "", "l'outil yt-dlp n'est pas installé")
        Path(folder).mkdir(parents=True, exist_ok=True)
        if is_album_page(url):
            # Un album (erome) : toutes ses videos, dans un dossier a son nom.
            page, _why = self._read_page(url, browser)
            files = album_files(page or "", url)
            if files:
                self._media.pop(job, None)
                self._expect.pop(job, None)
                return self._album(job, url, files, folder, page_title(page) or "Album")
        expect = self._expect.get(job)
        media = self._media.pop(job, None)
        errors = []
        for attempt in range(3):
            if job in self._cancelled:
                return self._emit_done(job, "", "annulé")
            if media is None:
                self._emit(job, -1.0, "recherche du fichier vidéo dans la page…")
                reader = self.renderer
                media, why = resolve(lambda u, referer="": self._read_page(u, browser, referer),
                                     url,
                                     self.ffprobe, USER_AGENT, expect,
                                     sniff=reader.sniff if reader is not None else None,
                                     origin=self._origin.get(job, ""))
                if media is None:
                    errors.append(why)
                    break
            if time.time() - media.found_at > MEDIA_FRESH_S:
                # Trouvee pendant la verification, il y a longtemps : sa cle a
                # pu expirer. On refait le chemin, pour une adresse fraiche.
                media = None
                continue
            self._emit(job, -1.0, f"vidéo entière trouvée ({media.height}p, "
                                  f"{media.size / 1e6:.0f} Mo) : téléchargement…")
            if media.format_id:
                # Trouvee par yt-dlp : il la telecharge lui-meme, depuis la
                # page, dans ce format (image et son assembles au besoin).
                path, why = self._fetch(job, media.referer, folder, browser,
                                        title=media.title, expect_s=media.duration)
            elif is_stream(media.url):
                path, why = self._fetch(job, media.url, folder, browser,
                                        referer=media.referer, title=media.title,
                                        expect_s=media.duration)
            else:
                path, why = self._direct(job, media, folder)
            if path or why == "annulé":
                self._expect.pop(job, None)
                return self._emit_done(job, path, why if not path else "")
            errors.append(why)
            media = None        # l'adresse a pu expirer : on refait le parcours
        # Dernier recours : yt-dlp sur la page, pour les sites qu'il connait.
        self._emit(job, -1.0, "essai avec l'outil de téléchargement…")
        path, why = self._fetch(job, url, folder, browser)
        self._expect.pop(job, None)
        if path or why == "annulé":
            return self._emit_done(job, path, why if not path else "")
        errors.append(why)
        # « Seulement un aperçu » en dit plus qu'un refus : on le garde.
        said = next((e for e in errors if e and "aperçu" in e),
                    next((e for e in errors if e), "échec"))
        self._emit_done(job, "", said)

    def _album(self, job: int, url: str, files: list, folder: str, title: str) -> None:
        """Chaque video de l'album, l'une apres l'autre, dans un dossier au nom
        de l'album. Une video refusee n'arrete pas les autres."""
        where = Path(folder) / _safe_name(title)
        where.mkdir(parents=True, exist_ok=True)
        got, failed = 0, []
        for number, address in enumerate(files, 1):
            if job in self._cancelled:
                return self._emit_done(job, "", "annulé")
            media = Media(address, url, 0, 0, 0, f"{number:02d} - {title}")
            share = ((number - 1) / len(files), number / len(files))
            path, why = self._direct(job, media, str(where), share=share,
                                     prefix=f"vidéo {number}/{len(files)} — ", short_ok=True)
            if why == "annulé":
                return self._emit_done(job, "", "annulé")
            if path:
                got += 1
            else:
                failed.append(why)
        if not got:
            return self._emit_done(job, "", failed[0] if failed else "échec")
        self._emit(job, 1.0, f"album : {got} vidéo(s) sur {len(files)}"
                   + (f", {len(failed)} refusée(s)" if failed else ""))
        self._emit_done(job, str(where), "")

    def _direct(self, job: int, media: Media, folder: str, share=(0.0, 1.0),
                prefix: str = "", short_ok: bool = False) -> tuple:
        """Telecharge un fichier video tel quel, par la meme sorte de demande
        que celle qui l'a mesure -- et que le site accepte. yt-dlp, avec ses
        propres en-tetes (et les cookies du navigateur), se faisait refuser
        par certains serveurs (pisshamster : HTTP 400). Par morceaux, avec
        reprise si la connexion coupe. Rend (fichier, erreur)."""
        import requests
        from urllib.parse import urlparse
        leaf = Path(urlparse(media.url).path)
        ext = leaf.suffix.lower() if leaf.suffix.lower() in (
            ".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv") else ".mp4"
        name = _safe_name(media.title or leaf.stem)
        final = Path(folder) / f"{name}{ext}"
        number = 2
        while final.exists():
            try:
                same = bool(media.size) and final.stat().st_size == media.size
            except OSError:
                same = False
            if same:
                # Deja la, entiere : un telechargement fini juste avant une
                # fermeture, repris a la relance. Pas de doublon « (2) ».
                self._emit(job, share[1], prefix + "déjà téléchargée")
                return str(final), ""
            final = Path(folder) / f"{name} ({number}){ext}"
            number += 1
        part = final.with_name(final.name + ".part")
        session = safe_session()
        headers = {"User-Agent": USER_AGENT, "Referer": media.referer}
        done, total, tries = 0, media.size or 0, 0
        if part.exists():
            # Un telechargement interrompu (Prisme ferme en route) : on repart
            # de la ou il s'etait arrete. Un serveur qui ne sait pas reprendre
            # (HTTP 200 au lieu de 206) fait recommencer, plus bas.
            try:
                done = part.stat().st_size
            except OSError:
                done = 0
        last_report = 0.0
        while True:
            if job in self._cancelled:
                part.unlink(missing_ok=True)
                return "", "annulé"
            wanted = dict(headers, Range=f"bytes={done}-") if done else headers
            try:
                with session.get(media.url, headers=wanted, stream=True,
                                 timeout=(20, 60)) as resp:
                    if resp.status_code == 416 and done:
                        break                       # le fichier commence etait deja complet
                    if resp.status_code not in (200, 206):
                        part.unlink(missing_ok=True)
                        host = urlparse(media.url).netloc
                        return "", f"le serveur {host} refuse le fichier (HTTP {resp.status_code})"
                    if resp.status_code == 200 and done:
                        done = 0                    # pas de reprise : on recommence
                    length = int(resp.headers.get("Content-Length") or 0)
                    if length:
                        total = done + length
                    with open(part, "ab" if done else "wb") as out:
                        for chunk in resp.iter_content(1 << 20):
                            if job in self._cancelled:
                                break
                            out.write(chunk)
                            done += len(chunk)
                            now = time.monotonic()
                            if now - last_report > 0.4:
                                last_report = now
                                self._measure(job, done, total)
                                fraction = (share[0] + (share[1] - share[0]) * done / total
                                            if total else -1.0)
                                self._emit(job, fraction,
                                           prefix + f"{done / 1e6:.0f} Mo"
                                           + (f" / {total / 1e6:.0f} Mo" if total else ""))
                if job in self._cancelled:
                    continue
                if not total or done >= total:
                    break
            except requests.RequestException:
                pass
            tries += 1
            if tries > 5:
                return "", "la connexion a coupé trop souvent pendant le téléchargement"
            time.sleep(1.5)                      # un instant, et l'on reprend ou l'on en etait
        try:
            part.replace(final)
        except OSError as exc:
            return "", f"écriture impossible ({exc})"
        verdict = self.judge(str(final), media.duration, short_ok)
        if verdict:
            final.unlink(missing_ok=True)
            return "", verdict
        self._emit(job, share[1], prefix + "terminé")
        return str(final), ""

    def _read_page(self, url: str, browser: str, referer: str = "") -> tuple:
        """(texte de la page, raison) ; le texte vaut None si elle ne se lit pas."""
        import requests
        session = safe_session()
        session.headers.update({"User-Agent": USER_AGENT,
                                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"})
        if browser:
            try:
                from yt_dlp.cookies import load_cookies
                session.cookies = load_cookies(None, (browser,), None)
            except Exception:                           # noqa: BLE001
                pass
        try:
            resp = session.get(url, timeout=20,
                               headers={"Referer": referer} if referer else None)
        except requests.RequestException as exc:
            return None, f"page injoignable ({type(exc).__name__})"
        if resp.status_code in (401, 403, 429, 503):
            return None, f"le site refuse la lecture de la page (HTTP {resp.status_code})"
        if not resp.ok:
            return None, f"page introuvable (HTTP {resp.status_code})"
        return resp.text[:3_000_000], "ok"

    def _fetch(self, job: int, url: str, folder: str, browser: str,
               referer: str = "", title: str = "", expect_s=None) -> tuple:
        """Un telechargement par yt-dlp. Rend (fichier, erreur) ; le fichier
        n'est rendu que si c'est bien une video."""
        import yt_dlp
        files: list = []

        def hook(state: dict) -> None:
            if job in self._cancelled:
                raise yt_dlp.utils.DownloadCancelled("annulé")
            status = state.get("status")
            if status == "downloading":
                total = state.get("total_bytes") or state.get("total_bytes_estimate") or 0
                done = state.get("downloaded_bytes") or 0
                speed = state.get("speed") or 0
                fraction = done / total if total else -1.0
                self._measure(job, done, total)
                text = f"{done / 1e6:.0f} Mo" + (f" / {total / 1e6:.0f} Mo" if total else "")
                if speed:
                    text += f" · {speed / 1e6:.1f} Mo/s"
                self._emit(job, fraction, text)
            elif status == "finished":
                files.append(state.get("filename") or "")
                self._emit(job, 1.0, "assemblage…")

        name = (_safe_name(title).replace("%", "%%") + " [%(id)s].%(ext)s") if title \
            else "%(title).150B [%(id)s].%(ext)s"
        options = {
            "outtmpl": str(Path(folder) / name),
            # La meilleure image, quel que soit son format : le MP4 passait
            # avant, meme en plus basse definition. Image et son a part sont
            # assembles par ffmpeg, en MP4 si possible, en MKV sinon. Faute de
            # mieux, ce qu'il y a (un site aux flux mal decrits faisait tout
            # echouer : « Requested format is not available »).
            "format": "bv*+ba/b/bv*/b*",
            "format_sort": ["res", "fps", "br", "size"],
            "merge_output_format": "mp4/mkv",
            "noplaylist": True,
            "windowsfilenames": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _Silent(),
            "progress_hooks": [hook],
            "retries": 3,
            "socket_timeout": 20,
        }
        if referer:
            options["http_headers"] = {"Referer": referer, "User-Agent": USER_AGENT}
        if self.ffmpeg:
            options["ffmpeg_location"] = str(Path(self.ffmpeg).parent)
        if browser:
            options["cookiesfrombrowser"] = (browser,)
        final = ""
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                if info:
                    final = (info.get("requested_downloads") or [{}])[0].get("filepath") \
                        or ydl.prepare_filename(info)
                final = final or (files[-1] if files else "")
        except yt_dlp.utils.DownloadCancelled:
            return "", "annulé"
        except Exception as exc:                        # noqa: BLE001
            return "", explain(str(exc))
        verdict = self.judge(final, expect_s or self._expect.get(job)) if final else \
            "le lien mène à une page, pas à un fichier vidéo"
        if not verdict:
            return final, ""
        # Une page HTML, un apercu de quelques secondes, un fichier casse :
        # on l'efface -- ouvert, un tel fichier faisait planter le lecteur.
        for leftover in {final, *files}:
            try:
                if leftover and Path(leftover).exists():
                    Path(leftover).unlink()
            except OSError:
                pass
        return "", verdict

    def judge(self, path: str, expect_s, short_ok: bool = False) -> str:
        """"" si `path` est bien la video attendue, sinon ce qui ne va pas.

        La duree annoncee par le site sert d'etalon : les pages portent aussi
        leurs apercus (survol, bande-annonce) -- quelques centaines de Ko,
        quelques secondes --, qu'on prenait pour la video."""
        if is_page(path):
            return "le lien mène à une page, pas à un fichier vidéo"
        info = probe(path, self.ffprobe)
        if info is None:
            if self.ffprobe:
                return "fichier illisible : ce n'est pas une vidéo complète"
            return "" if is_video_file(path) else "fichier trop petit pour être une vidéo"
        if not info["video"]:
            return "le fichier n'a pas d'image (son seul)"
        seconds = info["duration"]
        if expect_s and seconds < 0.6 * expect_s:
            return (f"seulement un aperçu de {human_seconds(seconds)} "
                    f"(la vidéo fait {human_seconds(expect_s)})")
        if not expect_s and seconds < MIN_UNKNOWN_S and not short_ok:
            return f"seulement un aperçu de {human_seconds(seconds)}"
        return ""
