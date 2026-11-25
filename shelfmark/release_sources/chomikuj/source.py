"""Chomikuj.pl release source - searches the Documents collection for ebooks.

Chomikuj exposes a mobile JSON API whose ``files/search`` endpoint returns raw
file rows for the Documents media type. A row's ``FileName``/``FileType`` pair
is the only metadata there is: the parsed name is the title (and sometimes the
author), the type is the format, and ``Size`` is reported in kilobytes. Results
are filtered down to the formats Shelfmark can import and never include
archives.

The source is config-only available: it needs ``CHOMIKUJ_ENABLED`` plus both
credentials, and it never logs in from :meth:`ChomikujSource.is_available`.
A search tries the planned queries in order and stops at the first one that
produces a selectable release. ``expand_search`` is a Shelfmark-only
escalation that widens the per-query budget to 20 s and 5 pages; the default is
12 s and 3 pages.

Language is inferred from the file name for display only. Chomikuj has no
server-side language filter, so ``plan.source_filters.language`` is
deliberately ignored and a row with an unknown language is never dropped.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, ClassVar

from shelfmark.core.config import config
from shelfmark.core.formats import get_supported_formats
from shelfmark.core.logger import setup_logger
from shelfmark.release_sources import (
    ColumnAlign,
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    SourceActionButton,
    SourceUnavailableError,
    register_source,
)
from shelfmark.release_sources.chomikuj.client import (
    ChomikujClient,
    ChomikujError,
    normalize_base_url,
)
from shelfmark.release_sources.chomikuj.filename import (
    ARCHIVE_EXTENSIONS,
    IMPORTABLE_EBOOK_FORMATS,
    guess_language,
    normalize_format,
    parse_file_name,
)
from shelfmark.release_sources.chomikuj.search import (
    EXPANDED_MAX_PAGES,
    EXPANDED_SEARCH_BUDGET_SECONDS,
    MAX_PAGES,
    SEARCH_BUDGET_SECONDS,
    order_and_dedupe,
    search_files,
    select_rows,
)
from shelfmark.release_sources.chomikuj.session import clear_session, get_session

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from shelfmark.core.models import SearchFilters
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

_DEFAULT_MAX_RESULTS = 50
_MAX_RESULTS_LIMIT = 200


def get_credentials() -> tuple[str, str]:
    """Return the configured ``(username, password)``, possibly empty."""
    username = config.get("CHOMIKUJ_USERNAME", "")
    password = config.get("CHOMIKUJ_PASSWORD", "")
    return (
        username.strip() if isinstance(username, str) else "",
        password if isinstance(password, str) else "",
    )


def _coerce_number(value: object) -> float | None:
    """Return a finite float for an int, float, or numeric string, else ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def get_max_results() -> int:
    """Return the configured result cap, defaulting to 50 and clamping to 1..200."""
    number = _coerce_number(config.get("CHOMIKUJ_MAX_RESULTS", _DEFAULT_MAX_RESULTS))
    if number is None:
        return _DEFAULT_MAX_RESULTS
    return min(_MAX_RESULTS_LIMIT, max(1, int(number)))


def get_base_url() -> str:
    """Return the configured API root, normalized to ``.../api/v3``."""
    return normalize_base_url(config.get("CHOMIKUJ_BASE_URL", ""))


def _normalize_formats(values: Iterable[object]) -> set[str]:
    """Return the non-empty normalized formats from ``values``."""
    return {normalized for value in values if (normalized := normalize_format(value))}


def get_allowed_formats(source_filters: SearchFilters | None = None) -> set[str]:
    """Return the importable ebook formats this source may offer.

    The set is the user's Supported Book Formats intersected with the formats
    this source can import, with archives always removed. A non-empty
    ``source_filters.format`` narrows it further; archives can never be added
    back by a filter.
    """
    allowed = _normalize_formats(get_supported_formats()) & set(IMPORTABLE_EBOOK_FORMATS)
    allowed -= set(ARCHIVE_EXTENSIONS)
    if source_filters is not None and source_filters.format:
        requested = _normalize_formats(source_filters.format) - set(ARCHIVE_EXTENSIONS)
        allowed &= requested
    return allowed


def _build_query_candidates(plan: ReleaseSearchPlan, book: BookMetadata) -> list[str]:
    """Build the ordered, de-duplicated query list from the plan.

    A manual query wins verbatim. Otherwise the title+author query is tried,
    then the title alone when it differs, then the first ISBN, then the book
    title. No other query shapes are invented.
    """
    candidates: list[str] = []
    manual = (plan.manual_query or "").strip()
    if manual:
        candidates.append(manual)
    else:
        primary = plan.primary_query.strip()
        if primary:
            candidates.append(primary)
        if plan.title_variants:
            title_only = (plan.title_variants[0].title or "").strip()
            if title_only and title_only.casefold() != primary.casefold():
                candidates.append(title_only)
        if not candidates and plan.isbn_candidates:
            isbn = str(plan.isbn_candidates[0]).strip()
            if isbn:
                candidates.append(isbn)
        if not candidates:
            title = (book.title or "").strip()
            if title:
                candidates.append(title)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.casefold()
        if not candidate or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _format_size(size_bytes: int | None) -> str | None:
    """Format a byte count for the release row, or ``None`` when unknown."""
    if size_bytes is None:
        return None
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return None


def _to_size_bytes(value: object) -> int | None:
    """Convert Chomikuj's kilobyte ``Size`` to bytes, or ``None`` when unusable.

    Only a positive int, float, or numeric string is accepted; ``int(Size) *
    1024`` keeps the conversion in whole kilobytes and never guesses bytes.
    """
    number = _coerce_number(value)
    if number is None or number <= 0:
        return None
    kilobytes = int(number)
    if kilobytes <= 0:
        return None
    return kilobytes * 1024


def to_release(row: object, *, allowed_formats: Collection[str]) -> Release | None:
    """Map one raw Chomikuj row to a Release, or ``None`` when it is dropped.

    ``Size`` is kilobytes; it becomes ``Release.size_bytes`` only, never a
    duplicated ``extra`` key. Chomikuj has no reliable publication date and no
    per-file info page, so no date is invented and ``info_url`` stays ``None``.
    """
    if not isinstance(row, dict) or not select_rows([row], allowed_formats=allowed_formats):
        return None
    raw_name = row.get("FileName")
    if not isinstance(raw_name, str):
        return None
    file_name = raw_name.strip()
    if not file_name:
        return None

    file_type = row.get("FileType")
    parsed = parse_file_name(file_name, file_type)
    file_id = str(row.get("FileId"))
    free = bool(row.get("IsFileFreeForUser"))
    language = guess_language(file_name)
    size_bytes = _to_size_bytes(row.get("Size"))

    return Release(
        source="chomikuj",
        source_id=f"chomikuj:{file_id}",
        title=parsed.title or file_name,
        format=parsed.format,
        language=language,
        size=_format_size(size_bytes),
        size_bytes=size_bytes,
        download_url=None,
        info_url=None,
        protocol=ReleaseProtocol.HTTP,
        indexer="Chomikuj.pl",
        content_type="ebook",
        extra={
            "file_id": file_id,
            "file_name": file_name,
            "file_type": file_type,
            "is_free_for_user": free,
            "freeleech": free,
            "author": parsed.author,
            "language": language,
            "access": "free" if free else "paid",
        },
    )


@register_source("chomikuj")
class ChomikujSource(ReleaseSource):
    """Release source that searches Chomikuj.pl Documents for ebooks."""

    name = "chomikuj"
    display_name = "Chomikuj.pl"
    supported_content_types: ClassVar[list[str]] = ["ebook"]
    can_be_default = True

    def is_available(self) -> bool:
        """Available only when explicitly enabled and both credentials are set."""
        username, password = get_credentials()
        return config.get("CHOMIKUJ_ENABLED", False) is True and bool(username and password)

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search Chomikuj for the book in the user's supported formats."""
        if content_type not in self.supported_content_types or not self.is_available():
            return []

        allowed = get_allowed_formats(plan.source_filters)
        if not allowed:
            return []

        candidates = _build_query_candidates(plan, book)
        if not candidates:
            return []

        max_results = get_max_results()
        username, password = get_credentials()
        base_url = get_base_url()

        from shelfmark.core.search_deadline import current as current_search_deadline

        deadline = current_search_deadline()
        if deadline is not None and deadline.expired:
            return []

        budget = EXPANDED_SEARCH_BUDGET_SECONDS if expand_search else SEARCH_BUDGET_SECONDS
        pages = EXPANDED_MAX_PAGES if expand_search else MAX_PAGES

        try:
            client = ChomikujClient(base_url)
            session = get_session(client, username=username, password=password, base_url=base_url)
            for candidate in candidates:
                if deadline is not None and deadline.expired:
                    break
                logger.info("Searching Chomikuj for: %s", candidate)
                rows = search_files(
                    client,
                    session.api_key,
                    candidate,
                    allowed_formats=allowed,
                    limit=max_results,
                    budget_seconds=budget,
                    max_pages=pages,
                    deadline=deadline,
                )
                selected = select_rows(rows, allowed_formats=allowed)
                if not selected:
                    continue
                releases: list[Release] = []
                for row in order_and_dedupe(selected):
                    release = to_release(row, allowed_formats=allowed)
                    if release is not None:
                        releases.append(release)
                if releases:
                    return releases[:max_results]
        except ChomikujError as exc:
            if exc.kind == "unauthorized":
                clear_session(username=username, password=password, base_url=base_url)
                message = "Chomikuj login failed: check your username and password"
            elif exc.kind in ("unreachable", "throttled"):
                message = f"Chomikuj is unreachable: {exc}"
            else:
                message = f"Chomikuj search failed: {exc}"
            logger.warning("Chomikuj search failed: %s", exc)
            raise SourceUnavailableError(message) from exc

        return []

    def get_column_config(self) -> ReleaseColumnConfig:
        """Language, format, size, and the always-visible free/paid access badge."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.language",
                    label="Language",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    sortable=True,
                    sort_key="size_bytes",
                ),
                ColumnSchema(
                    key="extra.access",
                    label="Access",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    uppercase=True,
                    fallback="-",
                ),
            ],
            grid_template="minmax(0,2fr) 60px 80px 80px 80px",
            supported_filters=["format"],
            action_button=SourceActionButton(label="Search more"),
        )
