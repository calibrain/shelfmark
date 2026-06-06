"""Shared text-normalization + fuzzy token-matching helpers for book matching.

Used by both the release matcher (``auto_download``) and the Audiobookshelf library
ownership check (``library_index``) so matching behaviour stays consistent.
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
