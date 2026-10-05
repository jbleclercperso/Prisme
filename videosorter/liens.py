"""Les liens envoyés du téléphone : la vidéo se télécharge d'elle-même, et
rejoint les favoris de la personne qui l'a envoyée.

Le serveur qui sert la page s'en charge -- le NAS (même PC éteint), ou le PC
quand c'est lui qui sert. Le téléchargement passe par le même chemin que la
recherche web de Prisme (`downloader.py` : la page, son lecteur, le fichier
entier, yt-dlp en dernier recours), ici sans fenêtre.

Les vidéos arrivent d'abord dans un dossier de travail, puis, finies et
vérifiées, à leur place : un fichier qu'on voit est un fichier complet.

Sans Qt : le NAS s'en sert aussi.
"""
from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

# Le dossier de la collection ou arrivent les videos envoyees du telephone.
FOLDER_NAME = "Téléchargés depuis le téléphone"
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".wmv", ".flv", ".ts")
# Au plus tant de liens par personne et par jour, et tant en attente en tout.
PER_DAY = 10
QUEUE_MAX = 40
# Il reste au moins cela sur le disque, apres le telechargement.
FREE_MIN = 3 * 1024 ** 3
# Une video plus longue a telecharger est abandonnee.
JOB_TIMEOUT_S = 3 * 3600
# Les liens finis restent visibles (sur la page) tant de temps.
KEEP_S = 3 * 86400
WORK_DIR = "_en-cours"


def temp_mark(name: str) -> str:
    """L'empreinte d'une video arrivee sur le NAS, le temps que Prisme la
    range dans la collection (elle en prend alors une autre : `renommes`)."""
    import hashlib
    return "t" + hashlib.sha1(("telecharges/" + name).encode("utf-8", "replace")).hexdigest()[:15]


def first_url(text: str) -> str:
    for word in str(text or "").split():
        if word.lower().startswith(("http://", "https://")):
            return word.strip("<>\"'()[]")
    return ""


def check_url(url: str) -> str:
    """"" si l'adresse peut etre telechargee, sinon pourquoi pas.

    Seulement des sites publics : une adresse du reseau de la maison (la box,
    le NAS lui-meme) ne se demande pas depuis un telephone d'invite.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Collez un lien complet, qui commence par https://"
    try:
        found = socket.getaddrinfo(parsed.hostname, parsed.port or 443,
                                   proto=socket.IPPROTO_TCP)
    except OSError:
        return "Ce site est introuvable : vérifiez le lien."
    for *_rest, address in found:
        try:
            ip = ipaddress.ip_address(address[0].split("%")[0])
        except ValueError:
            return "Adresse refusée."
        if (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                or any(ip in net for net in HOME_NETS)):
            return "Seuls les liens vers des sites publics sont acceptés."
    return ""


# La maison et le reseau prive Tailscale : jamais demandes depuis un lien.
# (Pas « tout ce qui n'est pas public » : certains filtres DNS repondent par
# une adresse reservee, 192.0.0.x, pour tous les sites.)
HOME_NETS = tuple(ipaddress.ip_network(net) for net in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.168.0.0/16", "fc00::/7", "fe80::/10", "::1/128"))


class LinkJobs:
    """La file des liens, gardée dans un fichier : un redémarrage n'en perd
    aucun. Un seul téléchargement à la fois -- le NAS et la connexion ont
    leurs limites."""

    def __init__(self, target: Path, state: Path, on_ready, ffmpeg=lambda: "",
                 available=lambda: True):
        self.target = Path(target)
        self.state = Path(state)
        # on_ready(travail, fichier) -> empreinte : la video entre dans la
        # bibliotheque et dans les favoris de la personne.
        self.on_ready = on_ready
        self.ffmpeg = ffmpeg
        # Faux tant que l'outil de telechargement n'est pas pret (le NAS
        # l'installe au premier lancement) : les liens attendent.
        self.available = available
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.thread = None
        self.jobs = self._load()
        for job in self.jobs:
            if job.get("state") == "en cours":
                # Coupe par un redemarrage : il repart.
                job["state"] = "attente"
                job["progress"] = 0.0
        self._save()
        if any(job.get("state") == "attente" for job in self.jobs):
            self._ensure_worker()

    # -- le fichier -------------------------------------------------------
    def _load(self) -> list:
        try:
            told = json.loads(self.state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [job for job in told if isinstance(job, dict)] if isinstance(told, list) else []

    def _save(self) -> None:
        now = time.time()
        with self.lock:
            self.jobs = [job for job in self.jobs
                         if job.get("state") in ("attente", "en cours")
                         or now - float(job.get("ended") or job.get("at") or 0) < KEEP_S]
            data = json.dumps(self.jobs, ensure_ascii=False)
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            spare = self.state.with_name(self.state.name + ".tmp")
            spare.write_text(data, encoding="utf-8")
            os.replace(spare, self.state)
        except OSError:
            pass

    # -- ce que la page demande -----------------------------------------------
    def submit(self, url: str, label: str, who: str = "") -> dict:
        """Met un lien en file. Rend {"ok": True, "job": …} ou {"ok": False,
        "error": …}."""
        url = url.strip()
        trouble = check_url(url)
        if trouble:
            return {"ok": False, "error": trouble}
        now = time.time()
        with self.lock:
            waiting = sum(1 for job in self.jobs if job.get("state") in ("attente", "en cours"))
            today = sum(1 for job in self.jobs
                        if job.get("label") == label and now - float(job.get("at") or 0) < 86400)
            same = next((job for job in self.jobs if job.get("url") == url
                         and job.get("label") == label
                         and job.get("state") in ("attente", "en cours", "fait")), None)
        if same is not None:
            return {"ok": True, "job": same["id"], "already": True}
        if today >= PER_DAY:
            return {"ok": False, "error": f"Au plus {PER_DAY} liens par jour : réessayez demain."}
        if waiting >= QUEUE_MAX:
            return {"ok": False, "error": "Beaucoup de vidéos en attente : réessayez plus tard."}
        job = {"id": f"{int(now * 1000):x}", "url": url, "label": label, "who": who,
               "at": now, "state": "attente", "progress": 0.0, "text": "", "name": "",
               "error": "", "mark": ""}
        with self.lock:
            self.jobs.append(job)
        self._save()
        self._ensure_worker()
        return {"ok": True, "job": job["id"]}

    def mine(self, labels: list) -> list:
        """Les liens de cette personne, du plus recent au plus ancien."""
        keep = ("id", "url", "state", "progress", "text", "name", "error", "mark", "at")
        with self.lock:
            found = [{key: job.get(key) for key in keep}
                     for job in self.jobs if job.get("label") in labels]
        return sorted(found, key=lambda job: -float(job.get("at") or 0))

    # -- le travail --------------------------------------------------------------
    def close(self) -> None:
        """Plus rien ne part d'ici (le partage s'arrete) ; un telechargement
        en cours va a son terme."""
        self.stopped = True
        self.wake.set()

    def _ensure_worker(self) -> None:
        if getattr(self, "stopped", False):
            return
        self.wake.set()
        if self.thread is not None and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="prisme-liens", daemon=True)
        self.thread.start()

    def _next(self):
        with self.lock:
            return next((job for job in self.jobs if job.get("state") == "attente"), None)

    def _run(self) -> None:
        while not getattr(self, "stopped", False):
            job = self._next()
            if job is None or not self.available():
                # Rien a faire, ou l'outil pas encore pret : on attend un
                # nouveau lien, ou une minute.
                self.wake.clear()
                self.wake.wait(60)
                continue
            self._process(job)

    def _set(self, job: dict, **changes) -> None:
        with self.lock:
            job.update(changes)

    def _process(self, job: dict) -> None:
        self._set(job, state="en cours", progress=0.0, text="recherche de la vidéo…")
        self._save()
        work = self.target / WORK_DIR / job["id"]
        try:
            work.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(work).free < FREE_MIN:
                raise OSError("plus assez de place sur le disque")
        except OSError as trouble:
            return self._fail(job, f"Téléchargement impossible : {trouble}")
        try:
            from .downloader import Downloader
        except ImportError as trouble:
            return self._fail(job, f"Outil de téléchargement absent ({trouble})")
        loader = Downloader()
        loader.ffmpeg = self.ffmpeg() or ""
        done = threading.Event()
        result: dict = {}
        last = [0.0]

        def progress(_job, fraction, text) -> None:
            self._set(job, progress=max(0.0, float(fraction)), text=str(text)[:120])
            if time.time() - last[0] > 5:
                last[0] = time.time()
                self._save()

        def finished(_job, path, error) -> None:
            result.update(path=path, error=error)
            done.set()

        # Appeles directement, dans le fil du telechargement : les signaux de
        # Qt, sur le PC, attendaient une boucle d'evenements -- sans elle, la
        # fin n'arrivait jamais.
        loader._emit = progress
        loader._emit_done = finished
        # Une erreur dans le telechargement faisait tomber son fil sans rien
        # dire : la file attendait une fin qui ne venait jamais. Elle finit le
        # lien, avec sa raison, et le detail va dans `liens-pile.txt`.
        attempt = loader._download

        def guarded(number, url, folder, browser) -> None:
            try:
                attempt(number, url, folder, browser)
            except Exception as trouble:                # noqa: BLE001
                import traceback
                try:
                    self.state.with_name("liens-pile.txt").write_text(
                        f"{time.strftime('%d/%m %H:%M:%S')} {url}\n\n"
                        + traceback.format_exc(), encoding="utf-8")
                except OSError:
                    pass
                finished(number, "", f"erreur interne ({type(trouble).__name__}: {trouble})")

        loader._download = guarded
        number = loader.add(job["url"], str(work))
        # Un telechargement qui traine : ou il en est, ecrit a cote de la file
        # (`liens-pile.txt`), pour comprendre sans acces au serveur.
        waited = 0
        while not done.wait(60) and waited < JOB_TIMEOUT_S:
            waited += 60
            if waited in (120, 600):
                self._dump(job)
        if not done.is_set():
            loader.cancel(number)
            done.wait(60)
            shutil.rmtree(work, ignore_errors=True)
            return self._fail(job, "Téléchargement trop long : abandonné.")
        path = Path(result.get("path") or "")
        if not result.get("path") or not path.exists():
            shutil.rmtree(work, ignore_errors=True)
            return self._fail(job, "La vidéo n'a pas pu être téléchargée : "
                                   + (result.get("error") or "échec"))
        # Un album rend un dossier : chacune de ses videos.
        found = ([p for p in sorted(path.rglob("*")) if p.suffix.lower() in VIDEO_EXT]
                 if path.is_dir() else [path])
        marks, names = [], []
        for one in found:
            final = self._place(one)
            if final is None:
                continue
            try:
                marks.append(self.on_ready(job, final) or "")
            except Exception as trouble:                # noqa: BLE001
                print(f"lien {job['id']} : rangement impossible : {trouble}", flush=True)
            names.append(final.name)
        shutil.rmtree(work, ignore_errors=True)
        if not names:
            return self._fail(job, "La vidéo téléchargée n'a pas pu être rangée.")
        self._set(job, state="fait", progress=1.0, text="", name=names[0],
                  mark=marks[0] if marks else "", ended=time.time(),
                  count=len(names))
        self._save()

    def _dump(self, job: dict) -> None:
        import faulthandler
        try:
            with open(self.state.with_name("liens-pile.txt"), "w", encoding="utf-8") as out:
                out.write(f"{time.strftime('%d/%m %H:%M:%S')} {job.get('url')} — "
                          f"{job.get('text')}\n\n")
                out.flush()
                faulthandler.dump_traceback(file=out, all_threads=True)
        except (OSError, ValueError):
            pass

    def _place(self, source: Path) -> Path | None:
        """La video finie, a sa place, sans ecraser une homonyme."""
        self.target.mkdir(parents=True, exist_ok=True)
        final = self.target / source.name
        stem, suffix, n = final.stem, final.suffix, 2
        while final.exists():
            final = self.target / f"{stem} ({n}){suffix}"
            n += 1
        try:
            shutil.move(str(source), str(final))
        except OSError:
            return None
        return final

    def _fail(self, job: dict, error: str) -> None:
        self._set(job, state="échec", error=error[:300], text="", ended=time.time())
        self._save()
