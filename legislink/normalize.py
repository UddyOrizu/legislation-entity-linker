"""Title normalisation and string similarity.

Resolution compares a *normalised* form of titles so that punctuation,
ampersands, curly quotes and a leading "the" don't defeat an otherwise exact
match. Keep this module free of regexes that belong to the citation grammar --
those live in :mod:`legislink.patterns`.
"""

from __future__ import annotations

import re
import unicodedata

_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
})

_KIND_NOUNS = (
    "act", "acts", "measure", "regulations", "regulation", "rules", "rule",
    "order", "orders", "scheme", "code",
)

_DROP = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
_LEADING_THE = re.compile(r"^the\s+")
_TRAILING_YEAR = re.compile(r"\s+(1[0-9]{3}|20[0-9]{2})$")


def clean(text: str) -> str:
    """Normalise unicode punctuation and whitespace, preserving case."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return _WS.sub(" ", text).strip()


def normalize_title(title: str) -> str:
    """Fold a work title to its comparison key.

    ``"The Land Reform (Scotland) Act 2003"`` -> ``"land reform scotland act"``.
    The year is stripped because it is matched separately and structurally.
    """
    text = clean(title).lower()
    text = text.replace("&", " and ")
    text = _DROP.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    text = _LEADING_THE.sub("", text)
    text = _TRAILING_YEAR.sub("", text)
    return _WS.sub(" ", text).strip()


def title_stem(title: str) -> str:
    """Normalised title with the trailing kind noun removed.

    Lets "Companies Act" and "Companies" compare equal when scoring, which
    matters for short forms like "the Companies Acts".
    """
    norm = normalize_title(title)
    parts = norm.split()
    while parts and parts[-1] in _KIND_NOUNS:
        parts.pop()
    return " ".join(parts)


def normalize_alias(alias: str) -> str:
    """Comparison key for acronym aliases: ``"the D.P.A."`` -> ``"dpa"``."""
    text = clean(alias).lower()
    text = _LEADING_THE.sub("", text)
    return _DROP.sub("", text)


try:  # pragma: no cover - depends on whether the optional extra is installed
    from rapidfuzz import fuzz as _fuzz

    def similarity(a: str, b: str) -> float:
        """Token-order-insensitive similarity in [0, 1]."""
        return max(_fuzz.token_sort_ratio(a, b), _fuzz.partial_ratio(a, b) * 0.95) / 100.0

    FUZZY_BACKEND = "rapidfuzz"
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher

    def similarity(a: str, b: str) -> float:
        """Token-order-insensitive similarity in [0, 1] (stdlib fallback)."""
        direct = SequenceMatcher(None, a, b).ratio()
        sorted_ = SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
        return max(direct, sorted_)

    FUZZY_BACKEND = "difflib"
