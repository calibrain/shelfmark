"""The title MyAnonamouse is searched by.

MAM requires every query word to match its catalogue, so a word the tracker does not
have turns a book it holds into zero results, and metadata providers put words in the
title that MAM's own titles leave out: a series prefix and a volume number. Hardcover's
"Magic: The Gathering - Ravnica - War of the Spark: 1" finds nothing on MAM, where the
book is "War of the Spark: Ravnica"; "War of the Spark" finds it.
"""

from __future__ import annotations

import re

from shelfmark.release_sources.prowlarr.mam import prowlarr_search_text

_SERIES_WORD = r"(?:book|vol(?:ume)?|part|no)\.?"
_NUMBER = r"\d+(?:\.\d+)?"

# Only a number set off from the title counts: "Fahrenheit 451" and "Catch-22" keep theirs.
_SERIES_NUMBER = re.compile(
    rf"""(?ix)
    (?:
        \s+[-\u2013\u2014]\s+(?:{_SERIES_WORD}\s*|\#\s*)?{_NUMBER}
      | \s*[:,]\s*(?:{_SERIES_WORD}\s*|\#\s*)?{_NUMBER}
      | \s*[(\[]\s*(?:{_SERIES_WORD}\s*|\#\s*)?{_NUMBER}\s*[)\]]
      | \s*\#\s*{_NUMBER}
      | \s+(?:book|vol(?:ume)?)\.?\s*{_NUMBER}
    )\s*$
    """
)
# Spaced dashes only, so hyphenated words survive.
_SEGMENT_SEPARATOR = re.compile(r"\s+[-–—]\s+")
_SERIES_PREFIX_SEPARATOR = r"\s*(?::|\s[-–—])\s*"


def core_title(title: str, series_name: str | None = None) -> str:
    """The title without its series prefix or trailing series number.

    Returns the input unchanged when stripping would leave nothing.
    """
    original = " ".join(title.split())
    stripped = original
    if series_name:
        series = re.escape(" ".join(series_name.split()))
        stripped = re.sub(rf"(?i)^{series}{_SERIES_PREFIX_SEPARATOR}", "", stripped)
    # The volume number first: as the last segment, "Dune - #1" would become "#1".
    stripped = _SERIES_NUMBER.sub("", stripped)
    stripped = _SEGMENT_SEPARATOR.split(stripped)[-1].strip()
    return stripped or original


def mam_fallback_queries(
    title: str,
    isbns: list[str],
    *,
    series_name: str | None = None,
    already_sent: list[str] | None = None,
) -> list[str]:
    """Looser MAM queries to try, in order, once the first ones found nothing.

    The title without its subtitle, then one ISBN. Each is another MAM request, and
    Prowlarr backs an indexer off after repeated failures, so the list stays short.
    Queries are compared as Prowlarr sends them, so punctuation alone never makes
    a query new.
    """
    candidates = [core_title(title, series_name).split(":", 1)[0].strip()]
    if isbns:
        candidates.append(isbns[0])

    sent = {prowlarr_search_text(q).lower() for q in already_sent or []}
    queries: list[str] = []
    for query in candidates:
        key = prowlarr_search_text(query).lower()
        if key and key not in sent:
            sent.add(key)
            queries.append(query)
    return queries
