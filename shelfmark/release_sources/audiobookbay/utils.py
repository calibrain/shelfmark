"""Utility functions for AudiobookBay integration."""

import re

# WordPress texturizes punctuation on output only: a post stored as "The
# Stranger's Wife" is rendered as "The Stranger’s Wife". ABB's search matches the
# stored value, so a query carrying the typographic form matches nothing -- and
# because ABB ANDs its search terms, one such term empties the entire result set.
# Book metadata and phone keyboards both hand us the typographic forms, so map
# them back before they reach a search or a title comparison.
_ASCII_PUNCTUATION = str.maketrans(
    {
        # Single quotes
        "‘": "'",  # left single quotation mark
        "’": "'",  # right single quotation mark
        "‚": "'",  # single low-9 quotation mark
        "‛": "'",  # single high-reversed-9 quotation mark
        "′": "'",  # prime
        "´": "'",  # acute accent
        "`": "'",  # grave accent
        # Double quotes
        "“": '"',  # left double quotation mark
        "”": '"',  # right double quotation mark
        "„": '"',  # double low-9 quotation mark
        "‟": '"',  # double high-reversed-9 quotation mark
        "″": '"',  # double prime
        # Dashes
        "‐": "-",  # hyphen
        "‑": "-",  # non-breaking hyphen
        "‒": "-",  # figure dash
        "–": "-",  # en dash
        "—": "-",  # em dash
        "―": "-",  # horizontal bar
        "−": "-",  # minus sign
        "﹘": "-",  # small em dash
        "﹣": "-",  # small hyphen-minus
        "－": "-",  # fullwidth hyphen-minus
        # Ellipsis
        "…": "...",  # horizontal ellipsis
    }
)


def normalize_search_punctuation(text: str) -> str:
    """Replace typographic punctuation with the ASCII forms ABB stores.

    Each character is mapped individually rather than collapsing runs, so an
    ASCII "--" is left alone: only characters ABB cannot have stored are
    rewritten.

    Args:
        text: A search query, or a scraped title being compared against one.

    Returns:
        The text with curly quotes, dashes and ellipses mapped to ASCII.

    """
    if not text:
        return text
    return text.translate(_ASCII_PUNCTUATION)


def normalize_hostname(raw: str | None) -> str:
    """Normalize a user-supplied hostname for URL construction.

    Strips whitespace, scheme prefixes, trailing slashes, and paths so that
    values like "https://audiobookbay.lu/" or " audiobookbay.lu/ " all
    resolve to "audiobookbay.lu".
    """
    if not raw or not isinstance(raw, str):
        return ""
    cleaned = raw.strip()
    # Strip scheme
    for prefix in ("https://", "http://"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    # Strip path and trailing slashes
    return cleaned.split("/")[0].strip()


def parse_size(size_str: str | None) -> int | None:
    """Parse size string to bytes.

    Args:
        size_str: Size string (e.g., "1.5 GB", "500 MB", "11.68 GBs")

    Returns:
        Size in bytes, or None if parsing fails

    """
    if not size_str:
        return None

    # Match number and unit, handling "GBs" as well as "GB" (case-insensitive)
    match = re.search(r"([\d.]+)\s*([BKMGT]B?)S?", size_str.upper())
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2)

    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }

    return int(value * multipliers.get(unit, 1))
