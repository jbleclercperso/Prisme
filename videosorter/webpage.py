"""Un navigateur invisible, pour les sites qui se construisent en JavaScript.

Beaucoup de sites n'envoient qu'une coquille vide : leur case de recherche et
leurs resultats n'existent qu'une fois leurs scripts executes. Lire la page
comme un texte n'y trouve rien. Ici, un vrai moteur de navigateur (celui de
Qt, Chromium) ouvre la page sans fenetre, tape les mots dans la case de
recherche comme on le ferait, valide, et rend la page une fois affichee.

Il ne sert qu'aux sites qui nous laissent entrer : un site qui refuse les
robots (HTTP 403, verification anti-robot) reste refuse -- on ne contourne
aucune protection. Aucune donnee n'est gardee : profil ephemere, sans son,
sans video.

Les recherches tournent dans des fils a part ; ce moteur, lui, ne vit que
sur le fil de l'interface. Les demandes passent donc par une file que le fil
de l'interface releve, et le fil qui demande attend sa reponse.
"""
from __future__ import annotations

import json
import queue
import re
import threading
import time
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QTimer, QUrl

try:
    from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor
except ImportError:                                         # pragma: no cover
    QWebEngineUrlRequestInterceptor = object


class _Listener(QWebEngineUrlRequestInterceptor):
    """Note chaque fichier video que demandent les pages : ce que le lecteur
    joue vraiment, meme quand le site cache son adresse dans ses scripts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lock = threading.Lock()
        self.seen: list = []                    # (hote de la page, adresse)

    def interceptRequest(self, info):  # noqa: N802
        url = info.requestUrl().toString()
        media = False
        try:
            media = info.resourceType() == info.ResourceType.ResourceTypeMedia
        except AttributeError:
            pass
        if (media or MEDIA_REQUEST.search(url)) and not CHUNK.search(url):
            host = urlparse(info.firstPartyUrl().toString()).netloc
            with self.lock:
                self.seen.append((host, url))
                del self.seen[:-400]

class _Light(QWebEngineUrlRequestInterceptor):
    """Pour lire une page ou y chercher : ni video, ni son, ni polices. Les
    sites de videos lancaient leurs lecteurs et leurs publicites animees
    dans chaque page invisible -- le PC entier ramait pendant la recherche."""

    def interceptRequest(self, info):  # noqa: N802
        try:
            kinds = info.ResourceType
            if info.resourceType() in (kinds.ResourceTypeMedia, kinds.ResourceTypeFontResource):
                info.block(True)
        except AttributeError:
            pass


SETTLE_POLL_MS = 500
PAGES = 3                        # pages a la fois (lecture et ecoute confondues)

# Trouve la case de recherche, y tape les mots et valide. Rend ce qu'il a fait.
TYPE_SCRIPT = r"""
(function (words) {
  function shown(e) {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  }
  const pick = 'input[type=search], input[name=q], input[name=query], input[name=search],' +
    'input[name=k], input[name=s], input[name=keyword], input[name=keywords], input[name=term],' +
    'input[placeholder*=earch i], input[placeholder*=echerch i], input[aria-label*=earch i],' +
    'input[id*=search i], input[class*=search i], input[name*=search i]';
  const texty = e => !e.disabled && /^(search|text|)$/i.test(e.getAttribute('type') || '');
  let boxes = Array.from(document.querySelectorAll(pick)).filter(texty);
  if (!boxes.filter(shown).length) {
    // Une case sans nom parlant, apparue en haut de page (apres un clic sur
    // la loupe, souvent) : c'est elle.
    const loose = Array.from(document.querySelectorAll('input')).filter(e =>
        texty(e) && shown(e) && e.getBoundingClientRect().top < 260);
    if (loose.length) boxes = loose;
  }
  if (!boxes.length) return 'no-input';
  boxes.sort((a, b) => shown(b) - shown(a));
  const box = boxes[0];
  if (!shown(box)) {
    const opener = document.querySelector('button[class*=search i], [aria-label*=earch i],' +
      '[class*=search-toggle i], [class*=icon-search i], [class*=search-btn i]');
    if (opener) opener.click();
  }
  box.focus();
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  set.call(box, words);
  box.dispatchEvent(new Event('input', {bubbles: true}));
  box.dispatchEvent(new Event('change', {bubbles: true}));
  const key = {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true};
  ['keydown', 'keypress', 'keyup'].forEach(t => box.dispatchEvent(new KeyboardEvent(t, key)));
  const form = box.form;
  setTimeout(() => {
    try {
      if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); return; }
      const near = box.closest('div, header, nav, section');
      const button = near && near.querySelector('button, [type=submit]');
      if (button) button.click();
    } catch (e) {}
  }, 400);
  return form ? 'form' : 'typed';
})(%s)
"""

# Les boutons qui peuvent ouvrir une recherche cachee : les petites icones du
# haut de page (la loupe n'a souvent ni texte ni nom). Ceux qui parlent de
# recherche d'abord, puis de droite a gauche -- la loupe est souvent a droite.
OPENERS_SCRIPT = r"""
(function () {
  document.querySelectorAll('[data-prisme-o]').forEach(e => e.removeAttribute('data-prisme-o'));
  const talks = e => /search|loupe|magnif|find|cherch|busca|suche/i.test(
      e.outerHTML.slice(0, 600));
  const all = Array.from(document.querySelectorAll(
      'button, [role=button], a, [onclick], label, span, div, i')).filter(e => {
    const r = e.getBoundingClientRect();
    // Dans la barre du haut, et a l'ecran : les vignettes d'un carrousel
    // (a 14 000 px a droite) ne sont pas des boutons.
    if (!(r.top < 130 && r.bottom > 0 && r.left >= 0 && r.right <= innerWidth &&
          r.width > 8 && r.width < 110 && r.height > 8 && r.height < 90))
      return false;
    if ((e.textContent || '').trim().length > 25) return false;
    // Ce qui se clique vraiment : un bouton, un lien, ou ce qui montre la
    // main -- pas le bloc qui contient les boutons (le clic s'y perdait).
    const real = /^(BUTTON|A)$/.test(e.tagName) || e.getAttribute('role') === 'button' ||
                 e.hasAttribute('onclick') || getComputedStyle(e).cursor === 'pointer';
    if (!real || e.querySelector('button, a, [role=button]')) return false;
    const h = e.getAttribute('href') || '';
    if (h && h !== '#' && !h.startsWith('javascript') && !/search|recherch/i.test(h)) return false;
    return talks(e) || e.querySelector('svg, i, img');
  });
  const seen = new Set(), picks = [];
  for (const e of all) {
    const r = e.getBoundingClientRect();
    const spot = Math.round(r.x / 12) + ':' + Math.round(r.y / 12);
    if (seen.has(spot)) continue;
    seen.add(spot);
    picks.push(e);
  }
  // La barre tout en haut d'abord ; le compte, la connexion, le menu en
  // dernier (ils ouvrent autre chose qu'une recherche).
  const aside = e => /login|log-in|sign|account|user|avatar|profile|menu|lang|burger/i.test(
      e.outerHTML.slice(0, 600));
  const score = e => (talks(e) ? 100000 : 0) + (e.getBoundingClientRect().top < 60 ? 50000 : 0)
                     - (aside(e) ? 40000 : 0) + e.getBoundingClientRect().x;
  picks.sort((a, b) => score(b) - score(a));
  picks.slice(0, 10).forEach((e, i) => e.setAttribute('data-prisme-o', i));
  return Math.min(picks.length, 10);
})()
"""

CLICK_SCRIPT = r"""
(function (i) {
  const e = document.querySelector('[data-prisme-o="' + i + '"]');
  if (!e) return 'gone';
  const h = e.getAttribute('href') || '';
  if (h && !h.startsWith('#') && !h.startsWith('javascript')) { location.href = h; return 'nav'; }
  e.click();
  return 'clicked';
})(%d)
"""

# De quoi juger qu'une page a fini de s'afficher : ses liens a vignette.
# En JSON : les tableaux JavaScript arrivent vides de ce cote-ci.
COUNT_SCRIPT = ("JSON.stringify([document.querySelectorAll('a img, a [style*=background]')"
                ".length, document.documentElement.outerHTML.length, location.href])")
HTML_SCRIPT = "document.documentElement.outerHTML"

# Lancer la lecture : les <video> de la page (sans le son), et le bouton
# « lecture » du lecteur. Rend, en JSON, le rectangle du plus grand lecteur
# (video, iframe ou bloc « player ») : on y clique ensuite pour de vrai, ce qui
# atteint aussi un lecteur integre depuis un autre site.
PLAY_SCRIPT = r"""
(function () {
  document.querySelectorAll('video').forEach(v => { try { v.muted = true; v.play(); } catch (e) {} });
  const button = document.querySelector('.vjs-big-play-button, .jw-display-icon-container,' +
    '.fp-play, .plyr__control--overlaid, [class*=play-button i], [class*=btn-play i],' +
    '[aria-label*=play i], [title*=play i]');
  if (button) { try { button.click(); } catch (e) {} }
  let best = null, area = 0;
  document.querySelectorAll('video, iframe, [id*=player i], [class*=player i]').forEach(e => {
    const r = e.getBoundingClientRect();
    const a = Math.max(0, r.width) * Math.max(0, r.height);
    if (a > area && r.width > 150 && r.height > 100 && r.top < innerHeight) { area = a; best = r; }
  });
  return JSON.stringify(best ? [best.left + best.width / 2, best.top + best.height / 2] : []);
})()
"""

# Ce que le lecteur demande : des fichiers video, pas leurs morceaux (.ts,
# .m4s) -- la liste (.m3u8, .mpd) ou le fichier entier suffit.
MEDIA_REQUEST = re.compile(r"\.(mp4|m4v|webm|mkv|mov|m3u8|mpd|flv)(\?|$|/)", re.I)
CHUNK = re.compile(r"\.(ts|m4s|aac|m4a|vtt|jpg|png|webp)(\?|$)", re.I)


class _Job:
    def __init__(self, url: str, words: str | None, timeout: float, sniff: bool = False):
        self.url = url
        self.words = words
        self.sniff = sniff
        self.media: list = []
        self.timeout = timeout
        self.done = threading.Event()
        self.html = None
        self.final_url = ""
        self.reason = ""


class PageReader(QObject):
    """Ouvre des pages dans un navigateur invisible, a la demande de fils de
    recherche. `render` et `type_search` s'appellent depuis n'importe quel
    fil ; tout le reste vit sur le fil de l'interface."""

    def __init__(self, user_agent: str = "", parent=None):
        super().__init__(parent)
        self._user_agent = user_agent
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        self._free: dict = {False: [], True: []}    # ecoute ? -> pages libres
        self._kind: dict = {}               # page -> ecoute ?
        self._profiles: dict = {}
        self._busy = 0
        self._active: dict = {}             # travail -> (page, echeance)
        self._views: dict = {}              # page -> sa vue invisible
        self._profile = None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._pump)
        self._timer.start()

    # -- depuis les fils de recherche ------------------------------------------
    def render(self, url: str, timeout: float = 25.0) -> tuple:
        """(html, adresse finale, raison) d'une page, une fois affichee."""
        return self._ask(_Job(url, None, timeout))

    def type_search(self, url: str, words: str, timeout: float = 45.0) -> tuple:
        """Ouvre `url`, tape `words` dans sa case de recherche, valide, et rend
        (html, adresse finale, raison) de la page de resultats."""
        return self._ask(_Job(url, words, timeout))

    def sniff(self, url: str, timeout: float = 25.0) -> tuple:
        """(fichiers video demandes par le lecteur, html, raison) : on ouvre la
        page, on lance la lecture (sans le son), et l'on note ce qu'il charge."""
        job = _Job(url, None, timeout, sniff=True)
        html_text, _final, why = self._ask(job)
        return job.media, html_text, why

    def _ask(self, job: _Job) -> tuple:
        if self._closed:
            return None, "", "navigateur fermé"
        self._jobs.put(job)
        if not job.done.wait(job.timeout + 10):
            return None, "", "le navigateur n'a pas répondu à temps"
        return job.html, job.final_url, job.reason

    def close(self) -> None:
        self._closed = True
        self._timer.stop()
        # Les vues invisibles s'en vont avec : elles gardaient le moteur du
        # navigateur (et ses processus) en vie apres la fermeture.
        for view in list(self._views.values()):
            try:
                view.close()
                view.deleteLater()
            except RuntimeError:
                pass
        self._views.clear()
        self._free = {False: [], True: []}

    # -- sur le fil de l'interface ---------------------------------------------
    def _profile_for(self, sniff: bool):
        """Deux profils ephemeres : l'un pour ecouter le lecteur (la video y
        joue, sans le son), l'autre, leger, pour lire les pages."""
        from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
        profile = self._profiles.get(sniff)
        if profile is None:
            profile = self._profiles[sniff] = QWebEngineProfile(self)   # rien d'enregistre
            if self._user_agent:
                profile.setHttpUserAgent(self._user_agent)
            settings = profile.settings()
            settings.setAttribute(QWebEngineSettings.AutoLoadIconsForPage, False)
            if sniff:
                self._listener = _Listener(self)
                profile.setUrlRequestInterceptor(self._listener)
                # Les pages sont muettes (setAudioMuted) : la lecture peut
                # partir seule, c'est elle qui revele le fichier video.
                settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, False)
                self._profile = profile
            else:
                self._light = _Light(self)
                profile.setUrlRequestInterceptor(self._light)
                settings.setAttribute(QWebEngineSettings.PlaybackRequiresUserGesture, True)
                settings.setAttribute(QWebEngineSettings.WebGLEnabled, False)
                settings.setAttribute(QWebEngineSettings.PluginsEnabled, False)
        return profile

    def _page(self, sniff: bool = False):
        from PySide6.QtWebEngineCore import QWebEnginePage
        profile = self._profile_for(sniff)
        if self._free[sniff]:
            return self._free[sniff].pop()
        from PySide6.QtCore import Qt
        from PySide6.QtWebEngineWidgets import QWebEngineView
        # Une vue jamais montree, a la taille d'un ecran de bureau : sans
        # elle la page n'avait aucune taille (0 x 0), et les sites se
        # dessinaient comme sur un tout petit ecran -- loupe comprise.
        view = QWebEngineView()
        view.setAttribute(Qt.WA_DontShowOnScreen, True)
        # Invisible, mais « montree » : sans cela Qt la comptait parmi les
        # fenetres ouvertes, et Prisme ne s'arretait plus a la fermeture.
        view.setAttribute(Qt.WA_QuitOnClose, False)
        view.resize(1366, 900)
        page = QWebEnginePage(profile, view)
        self._kind[page] = sniff
        view.setPage(page)
        view.show()
        page.setAudioMuted(True)
        self._views[page] = view
        return page

    def _pump(self) -> None:
        # Le chien de garde : une page qui ne repond plus (un script dont la
        # reponse s'est perdue pendant un changement de page) occupait sa
        # place pour toujours, et plus rien ne passait. Elle est remplacee.
        now = time.monotonic()
        for job, (page, deadline) in list(self._active.items()):
            if now > deadline:
                self._finish(page, job, None, "la page ne répond plus", fresh=True)
        while self._busy < PAGES:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                return
            self._busy += 1
            try:
                self._start(job)
            except Exception as exc:                        # noqa: BLE001
                self._finish(None, job, None, f"navigateur indisponible ({exc})")

    def _start(self, job: _Job) -> None:
        page = self._page(job.sniff)
        self._active[job] = (page, time.monotonic() + job.timeout + 5)
        state = {"loaded": False, "typed": job.words is None, "last": None,
                 "stable": 0, "deadline": time.monotonic() + job.timeout,
                 "typed_at": 0.0}

        def loaded(_ok: bool) -> None:
            state["loaded"] = True

        page.loadFinished.connect(loaded)
        state["hook"] = loaded
        page.load(QUrl(job.url))

        def poll() -> None:
            if job.done.is_set():
                return          # travail rendu (ou abandonne) : on ne touche plus a rien
            if self._closed:
                return self._finish(page, job, None, "navigateur fermé")
            if time.monotonic() > state["deadline"]:
                return grab("la page a mis trop de temps à s'afficher")
            if not state["loaded"]:
                return QTimer.singleShot(SETTLE_POLL_MS, poll)
            if job.sniff:
                return listen()
            if not state["typed"]:
                state["typed"] = True
                page.runJavaScript(TYPE_SCRIPT % json.dumps(job.words), 0, typed)
                return
            page.runJavaScript(COUNT_SCRIPT, 0, measured)

        def heard() -> list:
            host = urlparse(page.url().toString()).netloc
            with self._listener.lock:
                return [u for h, u in self._listener.seen
                        if h == host and u not in job.media and u != job.url]

        def listen() -> None:
            if "played" not in state:
                state["played"] = 0
                state["listen_until"] = min(state["deadline"], time.monotonic() + 10)
            if state["played"] < 2 and time.monotonic() > state.get("next_play", 0):
                state["played"] += 1
                state["next_play"] = time.monotonic() + 3
                page.runJavaScript(PLAY_SCRIPT, 0, click_player)
            job.media += heard()
            if job.media and time.monotonic() > state.get("first_heard", 1e18) + 1.5:
                return grab("")
            if job.media and "first_heard" not in state:
                state["first_heard"] = time.monotonic()
            if time.monotonic() > state["listen_until"]:
                return grab("" if job.media else "le lecteur n'a demandé aucun fichier vidéo")
            QTimer.singleShot(SETTLE_POLL_MS, listen)

        def click_player(spot) -> None:
            """Un vrai clic au milieu du lecteur : il compte comme un geste de
            quelqu'un, et atteint un lecteur integre depuis un autre site."""
            if job.done.is_set():
                return
            try:
                spot = json.loads(spot) if isinstance(spot, str) else []
            except ValueError:
                spot = []
            view = self._views.get(page)
            if len(spot) == 2 and view is not None:
                from PySide6.QtCore import QEvent, QPointF, Qt
                from PySide6.QtGui import QMouseEvent
                from PySide6.QtWidgets import QApplication
                target = view.focusProxy() or view
                point = QPointF(float(spot[0]), float(spot[1]))
                for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                    QApplication.sendEvent(target, QMouseEvent(
                        kind, point, point, Qt.LeftButton,
                        Qt.LeftButton if kind == QEvent.MouseButtonPress else Qt.NoButton,
                        Qt.NoModifier))

        def typed(result) -> None:
            if job.done.is_set():
                return          # travail rendu (ou abandonne) : on ne touche plus a rien
            if result == "no-input":
                return hunt()
            state["typed_at"] = time.monotonic()
            state["loaded"] = False          # on attend la page de resultats
            # Une recherche qui ne recharge pas la page (tout en JavaScript) :
            # on ne compte pas sur « loadFinished », on laisse le temps venir.
            QTimer.singleShot(2500, lambda: state.__setitem__("loaded", True))
            QTimer.singleShot(SETTLE_POLL_MS, poll)

        def hunt() -> None:
            """Pas de case visible : on clique les icones du haut de page, une
            a une, jusqu'a ce qu'une case apparaisse (la loupe de beeg)."""
            if job.done.is_set():
                return
            if "home" not in state:
                state["home"] = page.url().toString()
                state["try"] = 0
            if state["try"] >= 10 or time.monotonic() > state["deadline"] - 3:
                return grab("pas de case de recherche sur la page")
            page.runJavaScript(OPENERS_SCRIPT, 0, click)

        def click(count) -> None:
            if job.done.is_set():
                return
            index = state["try"]
            if not isinstance(count, (int, float)) or index >= int(count):
                return grab("pas de case de recherche sur la page")
            state["try"] += 1
            page.runJavaScript(CLICK_SCRIPT % index, 0,
                               lambda _r: QTimer.singleShot(1200, look_again))

        def look_again() -> None:
            if job.done.is_set():
                return
            page.runJavaScript(TYPE_SCRIPT % json.dumps(job.words), 0, after_click)

        def after_click(result) -> None:
            if job.done.is_set():
                return
            if result != "no-input":
                return typed(result)
            # Ce clic n'a pas ouvert de recherche (il menait ailleurs, ou a
            # ouvert la connexion) : retour a l'accueil, icone suivante.
            state["loaded"] = False
            page.load(QUrl(state["home"]))
            QTimer.singleShot(SETTLE_POLL_MS, back_home)

        def back_home() -> None:
            if job.done.is_set():
                return
            if not state["loaded"] and time.monotonic() < state["deadline"]:
                return QTimer.singleShot(SETTLE_POLL_MS, back_home)
            QTimer.singleShot(800, hunt)

        def measured(result) -> None:
            if job.done.is_set():
                return          # travail rendu (ou abandonne) : on ne touche plus a rien
            try:
                result = json.loads(result) if isinstance(result, str) else result
            except ValueError:
                result = None
            if not isinstance(result, list) or len(result) < 3:
                return QTimer.singleShot(SETTLE_POLL_MS, poll)
            now = (int(result[0]), int(result[1]) // 2000, result[2])
            state["stable"] = state["stable"] + 1 if now == state["last"] else 0
            state["last"] = now
            # Deux releves identiques avec des vignettes, ou trois secondes
            # sans rien qui bouge : la page est prete.
            if (state["stable"] >= 2 and now[0]) or state["stable"] >= 6:
                return grab("")
            QTimer.singleShot(SETTLE_POLL_MS, poll)

        def grab(reason: str) -> None:
            if job.done.is_set():
                return          # travail rendu (ou abandonne) : on ne touche plus a rien
            final = page.url().toString()
            page.runJavaScript(HTML_SCRIPT, 0,
                               lambda html: self._finish(page, job, html, reason, final))

        QTimer.singleShot(SETTLE_POLL_MS, poll)

    def _finish(self, page, job: _Job, html, reason: str, final: str = "",
                fresh: bool = False) -> None:
        if job not in self._active and job.done.is_set():
            return
        self._active.pop(job, None)
        job.html = html if isinstance(html, str) and html else None
        job.final_url = final or job.url
        job.reason = reason if job.html is None else (reason or "ok")
        if job.html is not None and reason.startswith("pas de case"):
            job.reason = reason
        job.done.set()
        self._busy = max(0, self._busy - 1)
        if page is not None and fresh:
            self._kind.pop(page, None)
            view = self._views.pop(page, None)   # une page coincee ne resservira pas
            if view is not None:
                view.deleteLater()
        elif page is not None:
            try:
                page.loadFinished.disconnect()
            except (RuntimeError, TypeError):
                pass
            page.load(QUrl("about:blank"))
            self._free[self._kind.get(page, False)].append(page)
