"""La recherche web et la verification, dans un processus a part.

Chercher, c'est surtout decortiquer des pages HTML -- du Python pur, qui
calcule sans relache. Python n'execute qu'un fil a la fois : avec dix sites
interroges ensemble, la fenetre n'avait plus la main qu'une fraction du temps,
et Prisme entier gelait pendant la recherche (une seconde pour poser une
carte, huit secondes d'affilee parfois). Ici, tout ce travail vit dans un
autre processus, a basse priorite ; la fenetre garde le sien pour elle.

Le navigateur invisible, lui, ne vit que sur le fil de la fenetre : quand la
recherche en a besoin, elle le demande a la fenetre (« render »), qui repond.

Sans Qt : ce module est aussi ce que le processus fils importe.
"""
from __future__ import annotations

import itertools
import multiprocessing
import os
import queue
import threading
import time

# -- le processus fils -------------------------------------------------------

VERIFY_WORKERS = 4


class _RemoteRenderer:
    """Le navigateur invisible de la fenetre, vu du processus fils : chaque
    appel part a la fenetre et attend sa reponse."""

    def __init__(self, events, answers: dict, lock: threading.Lock):
        self._events = events
        self._answers = answers
        self._lock = lock
        self._ids = itertools.count(1)

    def _call(self, method: str, *args, timeout: float = 60.0):
        ident = next(self._ids)
        waiter = threading.Event()
        with self._lock:
            self._answers[ident] = [waiter, None]
        self._events.put(("render", ident, method, args))
        if not waiter.wait(timeout + 15):
            with self._lock:
                self._answers.pop(ident, None)
            return None
        with self._lock:
            return self._answers.pop(ident, [None, None])[1]

    def render(self, url: str, timeout: float = 25.0) -> tuple:
        return self._call("render", url, timeout, timeout=timeout) or (
            None, "", "le navigateur n'a pas répondu à temps")

    def type_search(self, url: str, words: str, timeout: float = 45.0) -> tuple:
        return self._call("type_search", url, words, timeout, timeout=timeout) or (
            None, "", "le navigateur n'a pas répondu à temps")

    def sniff(self, url: str, timeout: float = 25.0) -> tuple:
        return self._call("sniff", url, timeout, timeout=timeout) or (
            [], None, "le navigateur n'a pas répondu à temps")


def ensure_streams() -> None:
    """Sans console (pythonw, le programme empaquete), sys.stdout et
    sys.stderr valent None : la moindre barre de progression d'une
    bibliotheque -- le telechargement d'un modele -- y ecrivait et tombait,
    des le depart et sans rien dire. Ils vont dans le vide, ouverts."""
    import sys
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def _lower_priority() -> None:
    """Le fils passe apres la fenetre quand le processeur est pris."""
    try:
        import ctypes
        below_normal = 0x4000
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),
                                                below_normal)
    except Exception:                                       # noqa: BLE001
        try:
            os.nice(5)
        except Exception:                                   # noqa: BLE001
            pass


def _child_main(commands, events, answers_in) -> None:
    """Le processus fils : recoit les ordres, fait le travail, renvoie tout."""
    ensure_streams()
    _lower_priority()
    from .mediafind import resolve
    from .websearch import USER_AGENT, Fetcher, run_search

    answers: dict = {}
    answer_lock = threading.Lock()
    renderer = _RemoteRenderer(events, answers, answer_lock)
    parent = multiprocessing.parent_process()

    def read_answers() -> None:
        while True:
            try:
                ident, value = answers_in.get(timeout=2)
            except queue.Empty:
                if parent is not None and not parent.is_alive():
                    os._exit(0)
                continue
            except (EOFError, OSError):
                os._exit(0)
            with answer_lock:
                slot = answers.get(ident)
                if slot is not None:
                    slot[1] = value
                    slot[0].set()

    threading.Thread(target=read_answers, daemon=True).start()

    # La recherche : une a la fois, arretee par son numero.
    stopped: set = set()

    def search(gen, filters, templates, browser, use_renderer) -> None:
        post = events.put
        found = None
        try:
            found = run_search(filters, templates,
                               should_stop=lambda: gen in stopped,
                               on_result=lambda video: post(("result", gen, video)),
                               on_site=lambda n, st, t: post(("site", gen, (n, st, t))),
                               browser=browser, renderer=renderer if use_renderer else None)
        except Exception as exc:                            # noqa: BLE001
            post(("site", gen, ("recherche", "erreur", f"la recherche a échoué ({exc})")))
        finally:
            post(("finished", gen, found))

    # La verification : les cartes a l'ecran d'abord.
    verify = {"gen": 0, "pending": [], "visible": set(), "threads": [], "ffprobe": "",
              "browser": ""}
    verify_lock = threading.Lock()

    def next_item():
        with verify_lock:
            pending = verify["pending"]
            if not pending:
                return None
            for index, item in enumerate(pending):
                if item[1] in verify["visible"]:
                    return pending.pop(index)
            return pending.pop(0)

    def verify_worker() -> None:
        fetchers: dict = {}
        while True:
            item = next_item()
            if item is None:
                return
            gen, key, url, expect, origin = item
            if gen != verify["gen"]:
                continue
            fetcher = fetchers.get(gen)
            if fetcher is None:
                fetcher = fetchers[gen] = Fetcher(lambda g=gen: g != verify["gen"],
                                                  browser=verify["browser"])
            try:
                media, why = resolve(fetcher.get, url, verify["ffprobe"], USER_AGENT, expect,
                                     origin=origin)
            except Exception as exc:                        # noqa: BLE001
                media, why = None, f"vérification impossible ({exc})"
            if gen == verify["gen"]:
                events.put(("verified", gen, (key, media, why)))

    while True:
        try:
            order = commands.get(timeout=2)
        except queue.Empty:
            if parent is not None and not parent.is_alive():
                break
            continue
        except (EOFError, OSError):
            break
        kind = order[0]
        if kind == "quit":
            break
        if kind == "search":
            _, gen, filters, templates, browser, use_renderer = order
            threading.Thread(target=search, args=(gen, filters, templates, browser, use_renderer),
                             daemon=True, name="recherche-web").start()
        elif kind == "stop_search":
            stopped.add(order[1])
        elif kind == "verify":
            _, gen, key, url, expect, origin, ffprobe, browser = order
            with verify_lock:
                if gen != verify["gen"]:
                    verify["gen"], verify["pending"] = gen, []
                verify["ffprobe"], verify["browser"] = ffprobe, browser
                verify["pending"].append((gen, key, url, expect, origin))
                verify["threads"] = [t for t in verify["threads"] if t.is_alive()]
                if len(verify["threads"]) < VERIFY_WORKERS:
                    thread = threading.Thread(target=verify_worker, daemon=True,
                                              name="prisme-verification")
                    verify["threads"].append(thread)
                    thread.start()
        elif kind == "prefer":
            with verify_lock:
                verify["visible"] = set(order[1])
        elif kind == "stop_verify":
            with verify_lock:
                verify["gen"] = order[1]
                verify["pending"] = []
    os._exit(0)


# -- du cote de la fenetre ---------------------------------------------------

class Engine:
    """Le processus fils, vu de la fenetre. `render_handler(methode, args)`
    ouvre une page dans le navigateur invisible (appele depuis un fil a part,
    jamais depuis celui de la fenetre)."""

    def __init__(self, render_handler):
        self._render = render_handler
        self._process = None
        self._lock = threading.Lock()
        self._routes: dict = {}             # (genre, numero) -> boite aux lettres

    def _ensure(self) -> None:
        with self._lock:
            if self._process is not None and self._process.is_alive():
                return
            context = multiprocessing.get_context("spawn")
            self._commands = context.Queue()
            self._events = context.Queue()
            self._answers = context.Queue()
            self._process = context.Process(target=_child_main, daemon=True,
                                            args=(self._commands, self._events, self._answers),
                                            name="prisme-recherche")
            self._process.start()
            threading.Thread(target=self._route, args=(self._events,), daemon=True,
                             name="prisme-moteur").start()

    def listen(self, kind: str, gen: int, mailbox) -> None:
        """Ce qui arrive pour cette recherche (« search ») ou cette
        verification (« verify ») ira dans `mailbox`."""
        self._routes[(kind, gen)] = mailbox

    def send(self, *order) -> None:
        self._ensure()
        self._commands.put(order)

    def _route(self, events) -> None:
        while True:
            try:
                item = events.get()
            except (EOFError, OSError, ValueError):
                return
            kind = item[0]
            if kind == "render":
                _, ident, method, args = item
                threading.Thread(target=self._answer, args=(ident, method, args),
                                 daemon=True).start()
                continue
            _, gen, value = item
            box = self._routes.get(("verify" if kind == "verified" else "search", gen))
            if box is None:
                continue
            if kind == "verified":
                box.put(value)
            else:
                box.put((kind, value))

    def _answer(self, ident, method: str, args) -> None:
        try:
            value = self._render(method, args)
        except Exception:                                   # noqa: BLE001
            value = None
        try:
            self._answers.put((ident, value))
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
        if process is None:
            return
        try:
            self._commands.put(("quit",))
            process.join(1.5)
        except Exception:                                   # noqa: BLE001
            pass
        if process.is_alive():
            process.terminate()


_numbers = itertools.count(1)


def next_number() -> int:
    """Un numero unique par recherche ou par verification."""
    return next(_numbers)
