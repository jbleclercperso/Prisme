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


def _always(_folded: str) -> bool:
    return True


def tester(parsed: tuple, loose: bool = False):
    """La recherche analysee, prete a courir sur des noms DEJA replies.

    Rend une fonction `nom_replie -> bool`. Tout ce qui ne depend pas du nom
    — trier les termes exacts des alternatives, retirer le « ~ » des
    exclusions — est fait ici, une fois par frappe, et non cent mille fois.
    Qui garde ses noms replies (la liste de la fenetre, le catalogue du
    partage) n'a plus rien a replier du tout : c'etait l'essentiel du cout
    d'une recherche.

    `loose` rend tous les termes approximatifs d'un coup : c'est le repli
    qu'on tente quand la recherche exacte ne rend rien, plutot que de laisser
    l'ecran vide sur une lettre de travers.
    """
    required, excluded = parsed
    # Les exclusions restent litterales : ecarter « a peu pres hiver »
    # ferait disparaitre des videos sans qu'on comprenne pourquoi. Un « -~ »
    # seul, vide une fois le signe retire, n'ecarte rien (il ecartait tout).
    banned = tuple(term for term in (t.lstrip(FUZZY) for t in excluded) if term)
    plain: list = []          # mots exacts exiges un par un : le cas courant
    groups: list = []         # le reste : « ou », « ~ », repli approximatif
    for group in required:
        if not loose and len(group) == 1 and not group[0].startswith(FUZZY):
            plain.append(group[0])
        else:
            groups.append(tuple(group))

    if not banned and not groups:
        # Un ou deux mots exacts : un test de sous-chaine nu, sans boucle.
        if not plain:
            return _always
        if len(plain) == 1:
            only = plain[0]
            return lambda folded: only in folded
        if len(plain) == 2:
            one, two = plain
            return lambda folded: one in folded and two in folded
    plain_t, groups_t = tuple(plain), tuple(groups)

    def test(folded: str) -> bool:
        for term in banned:
            if term in folded:
                return False
        for term in plain_t:
            if term not in folded:
                return False
        for group in groups_t:
            for term in group:
                if _hit(term, folded, loose):
                    break
            else:
                return False
        return True
    return test


# La derniere recherche preparee, pour `matches` : appele cent mille fois de
# suite avec la meme recherche, il ne la reprepare pas a chaque nom. Un seul
# tuple, remplace d'un bloc : deux fils qui cherchent en meme temps se
# genent au pire, ils ne se trompent pas.
_LAST: tuple = (None, False, _always)


def matches(name: str, parsed: tuple, loose: bool = False) -> bool:
    """Vrai si ce nom (non replie) satisfait la recherche analysee.

    Le nom est replie ici, par le cache de `fold` : pour une liste entiere,
    mieux vaut garder ses noms replies et passer par `tester`.
    """
    global _LAST
    last = _LAST
    if last[0] is parsed and last[1] == loose:
        test = last[2]
    else:
        test = tester(parsed, loose)
        _LAST = (parsed, loose, test)
    return test(fold(name))


def matches_text(name: str, text: str, loose: bool = False) -> bool:
    """Raccourci : analyse et compare d'un coup."""
    return tester(parse(text), loose)(fold(name))


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
