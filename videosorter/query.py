"""Le champ de recherche comprend quelques mots de plus que les siens.

Un seul champ, et la même syntaxe partout :

    plage montagne     les deux mots sont exigés
    plage or mer       l'un ou l'autre suffit
    plage -hiver       « plage », mais pas « hiver »
    "saison 2"         l'expression exacte, espaces compris

Casse et accents sont ignorés, comme dans les mots-clés : « Été » trouve
« ete ». Écrire plusieurs conditions dans un champ vaut mieux que d'aligner
autant de champs à l'écran — c'est ce qui a permis de retirer la barre de
filtres sans rien perdre.
"""
from __future__ import annotations

import re
from pathlib import Path

from .tagging import fold

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


def matches(name: str, parsed: tuple) -> bool:
    """Vrai si ce nom satisfait la recherche analysee."""
    required, excluded = parsed
    folded = fold(name)
    if any(term in folded for term in excluded):
        return False
    return all(any(term in folded for term in group) for group in required)


def matches_text(name: str, text: str) -> bool:
    """Raccourci : analyse et compare d'un coup."""
    return matches(name, parse(text))


def describe(text: str) -> str:
    """Resume la recherche en clair, pour l'infobulle du champ."""
    required, excluded = parse(text)
    if not required and not excluded:
        return "Tout est affiché"
    parts = [" ou ".join(group) for group in required]
    if excluded:
        parts.append("sans " + ", ".join(excluded))
    return " · ".join(parts)


def suggest(path: Path) -> str:
    """Propose une recherche a partir d'un chemin, pour les tests et l'aide."""
    return f'"{path.stem}"'
