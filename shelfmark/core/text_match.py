"""Shared text-normalization + fuzzy token-matching helpers for book matching.

Used by the library ownership check (``library_index``) and its providers, so every
library matches titles, authors and ISBNs the same way.
"""

from __future__ import annotations

import re

DEFAULT_TITLE_MATCH_THRESHOLD = 0.85

# Short/common words that add noise to title token matching.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "and",
        "or",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "by",
    }
)


def tokens(text: str | None) -> list[str]:
    """Lowercase alphanumeric tokens from arbitrary text."""
    if not text:
        return []
    return [tok for tok in re.split(r"[^a-z0-9]+", text.lower()) if tok]


def significant_tokens(text: str | None) -> list[str]:
    """Tokens with stopwords and 1-char noise removed."""
    return [tok for tok in tokens(text) if len(tok) >= 2 and tok not in STOPWORDS]


def author_surname(author: str | None) -> str | None:
    """Return the most distinctive author token (the surname), or None."""
    value = author or ""
    if "," in value:  # "Last, First" -> keep the "Last" portion
        value = value.split(",")[0]
    toks = significant_tokens(value)
    return toks[-1] if toks else None


def title_tokens_match(
    title: str | None,
    haystack_tokens: set[str],
    threshold: float = DEFAULT_TITLE_MATCH_THRESHOLD,
) -> bool:
    """True when enough significant title tokens appear in ``haystack_tokens``."""
    title_toks = significant_tokens(title)
    if not title_toks:
        return False
    present = sum(1 for tok in title_toks if tok in haystack_tokens)
    return (present / len(title_toks)) >= threshold


def normalize_isbn(value: object) -> str:
    """Normalize an ISBN to comparable form (digits + trailing X, uppercased)."""
    if not value:
        return ""
    return re.sub(r"[^0-9xX]", "", str(value)).upper()


def _isbn10_to_isbn13(isbn10: str) -> str | None:
    """ISBN-13 form of a normalized ISBN-10: 978 prefix plus a recomputed check digit."""
    core = isbn10[:9]
    if len(isbn10) != 10 or not core.isdigit():
        return None
    digits = f"978{core}"
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits))
    return f"{digits}{(10 - total % 10) % 10}"


def isbn_variants(value: object) -> frozenset[str]:
    """Comparable forms of an ISBN: normalized, plus the ISBN-13 form of an ISBN-10."""
    isbn = normalize_isbn(value)
    if not isbn:
        return frozenset()
    variants = {isbn}
    if len(isbn) == 10 and (isbn13 := _isbn10_to_isbn13(isbn)):
        variants.add(isbn13)
    return frozenset(variants)
