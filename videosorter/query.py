"""Le champ de recherche comprend quelques mots de plus que les siens.

Un seul champ, et la même syntaxe partout :

    plage montagne     les deux mots sont exigés
    plage or mer       l'un ou l'autre suffit
    plage -hiver       « plage », mais pas « hiver »
    "saison 2"         l'expression exacte, espaces compris
    ~montagne          à peu près : « mongagne » et « Montaigne » aussi

Casse et accents sont ignorés, comme dans les mots-clés : « Été » trouve
« ete ». Écrire plusieurs conditions dans un champ vaut mieux que d'aligner
autant de champs à l'écran — c'est ce qui a permis de retirer la barre de
filtres sans rien perdre.
"""
from __future__ import annotations

import re
from pathlib import Path

from .tagging import fold

try:                                    # pragma: no cover - selon l'installation
    from rapidfuzz import fuzz
except ImportError:                     # pragma: no cover
    fuzz = None

# Au-dessus de ce score sur cent, deux mots sont « a peu pres » les memes.
# Quatre-vingts laisse passer une lettre fausse ou manquante dans un mot de
# cinq ; plus bas, « mer » attrapait « mur », « mar » et la moitie du reste.
NEAR = 82
# Le signe qui demande l'a-peu-pres pour un terme.
FUZZY = "~"


def approximate(term: str, folded_name: str) -> bool:
    """Vrai si `term` figure a peu pres dans ce nom deja replie.

    Sans rapidfuzz, on retombe sur la recherche exacte : l'application
    fonctionne, elle est seulement moins indulgente.
    """
    if term in folded_name:
        return True
    if fuzz is None:
        return False
    return fuzz.partial_ratio(term, folded_name, score_cutoff=NEAR) > 0


def available() -> bool:
    """Dit si l'a-peu-pres est disponible, pour le signaler a l'ecran."""
    return fuzz is not None

# Un terme : soit une expression entre guillemets, soit un mot. Le « - » qui
# l'introduit eventuellement est capte a part.
_TOKEN = re.compile(r'(-?)(?:"([^"]*)"|(\S+))')


def parse(text: str) -> tuple:
    """Rend (groupes exiges, termes exclus), tous replies.

    Un « groupe » est une liste d'alternatives : il suffit que l'une d'elles
    figure. Les groupes, eux, sont tous exiges.
    """
    required: list = []
    excluded: list = []
    pending_or = False
    for sign, quoted, bare in _TOKEN.findall(text or ""):
        word = quoted if quoted else bare
        folded = fold(word.strip())
        if not folded:
            continue
        # « ~mot » demande l'a-peu-pres pour ce terme seul. Le signe est garde
        # dans le terme : `matches` le lit, et `parse` garde ainsi sa forme.
        if folded.startswith(FUZZY) and len(folded) > 1 and not sign:
            folded = FUZZY + folded.lstrip(FUZZY)
        if folded in ("or", "|", "ou") and not sign:
            # Le mot lie le terme precedent au suivant ; seul, il ne veut rien
            # dire et vaut alors pour lui-meme.
            pending_or = bool(required)
            continue
        if sign == "-":
            excluded.append(folded)
            pending_or = False
            continue
        if pending_or and required:
            required[-1].append(folded)
        else:
            required.append([folded])
        pending_or = False
    return required, excluded


def _hit(term: str, folded: str, loose: bool) -> bool:
    if term.startswith(FUZZY):
        return approximate(term[1:], folded)
    if loose:
        return approximate(term, folded)
    return term in folded


def matches(name: str, parsed: tuple, loose: bool = False) -> bool:
    """Vrai si ce nom satisfait la recherche analysee.

    `loose` rend tous les termes approximatifs d'un coup : c'est le repli
    qu'on tente quand la recherche exacte ne rend rien, plutot que de laisser
    l'ecran vide sur une lettre de travers.
    """
    required, excluded = parsed
    folded = fold(name)
    # Les exclusions restent litterales : ecarter « a peu pres hiver »
    # ferait disparaitre des videos sans qu'on comprenne pourquoi.
    if any(term.lstrip(FUZZY) in folded for term in excluded):
        return False
    return all(any(_hit(term, folded, loose) for term in group)
               for group in required)


def matches_text(name: str, text: str, loose: bool = False) -> bool:
    """Raccourci : analyse et compare d'un coup."""
    return matches(name, parse(text), loose)


def describe(text: str) -> str:
    """Resume la recherche en clair, pour l'infobulle du champ."""
    required, excluded = parse(text)
    if not required and not excluded:
        return "Tout est affiché"
    def say(term: str) -> str:
        return f"à peu près {term[1:]}" if term.startswith(FUZZY) else term

    parts = [" ou ".join(say(term) for term in group) for group in required]
    if excluded:
        parts.append("sans " + ", ".join(excluded))
    return " · ".join(parts)


def suggest(path: Path) -> str:
    """Propose une recherche a partir d'un chemin, pour les tests et l'aide."""
    return f'"{path.stem}"'
