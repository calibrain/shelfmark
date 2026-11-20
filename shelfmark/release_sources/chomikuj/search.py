"""Paged NFC/NFD Chomikuj document search with a self-imposed budget.

Ported from a validated Chomikuj implementation's ``search.mjs``. The client owns the
wire format: it always adds ``MediaType=Documents`` and ``PageNumber``, and
adds ``Extension`` only when this module passes one. :func:`search_files`
returns the raw ``Results`` rows in API order; :func:`select_rows` then applies
the local format/archive filter and :func:`order_and_dedupe` drops duplicate
``FileId`` values and orders the survivors newest-first. Mapping to Shelfmark
releases belongs to later modules.

A later-page failure, an exhausted budget, or a missing next page returns the
rows collected so far instead of raising, so one search attempt never spends
the host's whole deadline. Only a failed or unusable first page is raised. A
query is searched in both its NFC and NFD forms because Chomikuj's index does
not normalize filenames.

Shelfmark adaptation: :data:`EXPANDED_SEARCH_BUDGET_SECONDS` (20 s) and
:data:`EXPANDED_MAX_PAGES` (5) are the ``expand_search`` escalation. The
validated implementation has no expand search and keeps one module-level
12 s / 3 page budget, so this pair is a Shelfmark-only extension, not a port.
"""

from __future__ import annotations

import re
import time
import unicodedata
from typing import TYPE_CHECKING

from shelfmark.release_sources.chomikuj.client import ChomikujClient, ChomikujError
from shelfmark.release_sources.chomikuj.filename import (
    IMPORTABLE_EBOOK_FORMATS,
    is_archive_extension,
    normalize_format,
    parse_file_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable

    from shelfmark.core.search_deadline import SearchDeadline

SEARCH_BUDGET_SECONDS = 12.0
EXPANDED_SEARCH_BUDGET_SECONDS = 20.0
MAX_PAGES = 3
EXPANDED_MAX_PAGES = 5
MAX_RESULTS_TARGET_MULTIPLIER = 3

_EMPTY_QUERY_MESSAGE = "Chomikuj search needs a title, author, or ISBN"
_INVALID_RESULTS_MESSAGE = "Chomikuj search returned an invalid Results payload"

_DECIMAL_ID_RE = re.compile(r"[0-9]+")


def build_query_text(query: str) -> str:
    """Return the trimmed query; whitespace-only input stays empty."""
    return query.strip() if isinstance(query, str) else ""


def _single_extension(allowed_formats: Collection[str]) -> str | None:
    """Return the lone non-archive format, or ``None`` when zero or many exist."""
    formats = {
        normalized
        for value in allowed_formats
        if (normalized := normalize_format(value)) and not is_archive_extension(normalized)
    }
    if len(formats) != 1:
        return None
    return next(iter(formats))


def search_files(
    client: ChomikujClient,
    api_key: str,
    query_text: str,
    *,
    allowed_formats: Collection[str],
    limit: int,
    budget_seconds: float = SEARCH_BUDGET_SECONDS,
    max_pages: int = MAX_PAGES,
    now: Callable[[], float] = time.monotonic,
    deadline: SearchDeadline | None = None,
) -> list[dict[str, object]]:
    """Search Chomikuj Documents and merge the raw rows from every page.

    One deadline is computed before the NFC/NFD variants loop and shared by
    every request. Before each request the remaining budget is checked and then
    passed to :meth:`ChomikujClient.search_page` as its timeout, so a single
    hung request cannot overrun the budget. The first page of a variant must be
    fresh and well formed; a failure on a later page stops that variant and
    returns the rows collected so far.

    An ambient Shelfmark ``deadline`` (``search_deadline.current()``) caps both
    the effective budget, at ``min(budget_seconds, deadline.remaining)``, and
    every request timeout. The page loop stops without raising as soon as that
    deadline expires, so a search returns its collected rows instead of turning
    the request-level budget into a Chomikuj error.

    ``Extension`` is sent only when exactly one allowed non-archive format
    exists. Rows are returned exactly as received (duplicates included) for the
    selection step of todo 9.
    """
    text = build_query_text(query_text)
    if text == "":
        raise ChomikujError(_EMPTY_QUERY_MESSAGE, kind="error")

    extension = _single_extension(allowed_formats)
    target = max(1, int(limit)) * MAX_RESULTS_TARGET_MULTIPLIER
    effective_budget = float(budget_seconds)
    if deadline is not None:
        effective_budget = min(effective_budget, float(deadline.remaining))
    expires_at = now() + effective_budget

    # Chomikuj's index does not normalize filenames, and the same Polish text
    # can be stored composed or decomposed. Python's NFC form of an ASCII or
    # ISBN query equals its NFD form, so ``dict.fromkeys`` leaves one pass.
    variants = list(
        dict.fromkeys(
            (
                unicodedata.normalize("NFC", text),
                unicodedata.normalize("NFD", text),
            )
        )
    )

    rows: list[dict[str, object]] = []
    file_ids: set[str] = set()

    for variant in variants:
        page = 1
        while page <= max_pages:
            if deadline is not None and deadline.expired:
                break
            remaining = float(expires_at - now())
            if remaining <= 0:
                break
            request_timeout = remaining
            if deadline is not None:
                request_timeout = min(request_timeout, float(deadline.remaining))
                if request_timeout <= 0:
                    break

            try:
                if extension is None:
                    payload = client.search_page(
                        api_key,
                        query=variant,
                        page=page,
                        timeout=request_timeout,
                    )
                else:
                    payload = client.search_page(
                        api_key,
                        query=variant,
                        page=page,
                        extension=extension,
                        timeout=request_timeout,
                    )
            except Exception:
                if page == 1:
                    raise
                break

            results = payload.get("Results")
            if not isinstance(results, list):
                if page == 1:
                    raise ChomikujError(_INVALID_RESULTS_MESSAGE, kind="error")
                break

            rows.extend(results)
            for item in results:
                if not isinstance(item, dict):
                    continue
                file_id = item.get("FileId")
                if file_id is not None:
                    file_ids.add(str(file_id))

            if len(file_ids) >= target:
                break
            if payload.get("IsNextPageAvailable") is not True:
                break
            page += 1

    return rows


def _selected_formats(allowed_formats: Collection[str]) -> set[str] | None:
    """Return the normalized selection, or ``None`` when every format is allowed."""
    selected = {normalized for value in allowed_formats if (normalized := normalize_format(value))}
    return selected or None


def _positive_decimal_id(value: object) -> int | None:
    """Return a positive decimal ``FileId`` as an int, else ``None``.

    The live API sends ``FileId`` as a JSON number, while fixtures and older
    responses carry decimal strings. ``bool`` is rejected despite subclassing
    ``int``, and floats, zero, and negatives are rejected too.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if not isinstance(value, str) or _DECIMAL_ID_RE.fullmatch(value) is None:
        return None
    number = int(value)
    return number if number > 0 else None


def _file_id_text(value: object) -> str:
    """Return the text used to key and compare a ``FileId`` value."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    return ""


def select_rows(
    rows: Iterable[object],
    *,
    allowed_formats: Collection[str],
) -> list[dict[str, object]]:
    """Keep raw Chomikuj rows that can become importable ebook releases.

    A row needs a positive decimal integer string ``FileId`` and a non-empty
    string ``FileName``. Its parsed format must be importable, must not be an
    archive, and must be in ``allowed_formats`` unless the normalized selection
    is empty. The original row dicts are returned in input order; no row is
    mutated.
    """
    selected = _selected_formats(allowed_formats)
    kept: list[dict[str, object]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        if _positive_decimal_id(row.get("FileId")) is None:
            continue
        file_name = row.get("FileName")
        if not isinstance(file_name, str) or not file_name.strip():
            continue

        parsed_format = normalize_format(parse_file_name(file_name, row.get("FileType")).format)
        if is_archive_extension(parsed_format):
            continue
        if parsed_format not in IMPORTABLE_EBOOK_FORMATS:
            continue
        if selected is not None and parsed_format not in selected:
            continue
        kept.append(row)

    return kept


def order_and_dedupe(rows: Iterable[object]) -> list[dict[str, object]]:
    """Keep the first row per exact ``FileId`` and order the rest newest-first.

    Numeric ids compare as Python integers in descending order. Non-numeric
    ids follow every numeric id, then compare lexicographically descending.
    Equal sort keys keep their input order because both sorts are stable.
    """
    seen: set[str] = set()
    unique: list[dict[str, object]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        key = _file_id_text(row.get("FileId"))
        if key != "":
            if key in seen:
                continue
            seen.add(key)
        unique.append(row)

    numeric: list[tuple[int, dict[str, object]]] = []
    textual: list[tuple[str, dict[str, object]]] = []
    for row in unique:
        text = _file_id_text(row.get("FileId"))
        if _DECIMAL_ID_RE.fullmatch(text) is not None:
            numeric.append((int(text), row))
        else:
            textual.append((text, row))

    numeric.sort(key=lambda item: item[0], reverse=True)
    textual.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in numeric] + [row for _, row in textual]
