"""Utility functions for AudiobookBay integration."""

import re
from typing import Optional


def normalize_hostname(raw: Optional[str]) -> str:
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
            cleaned = cleaned[len(prefix):]
            break
    # Strip path and trailing slashes
    cleaned = cleaned.split("/")[0].strip()
    return cleaned


def parse_size(size_str: Optional[str]) -> Optional[int]:
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
        "MB": 1024 ** 2,
        "GB": 1024 ** 3,
        "TB": 1024 ** 4,
    }

    return int(value * multipliers.get(unit, 1))
