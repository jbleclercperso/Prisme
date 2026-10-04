"""Replier un texte pour comparer : sans accents ni majuscules.

A part, sans Qt : le serveur de partage qui tourne sur le NAS s'en sert pour
chercher, et n'a que la bibliotheque standard de Python.
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache


@lru_cache(maxsize=300_000)
def fold(text: str) -> str:
    """Ramène un texte à une forme comparable : sans accents ni majuscules."""
    stripped = unicodedata.normalize("NFKD", text)
    without_marks = "".join(c for c in stripped if not unicodedata.combining(c))
    return without_marks.casefold()
