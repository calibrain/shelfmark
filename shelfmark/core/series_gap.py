"""Series-gap report: the volumes of a collected series that are not on the shelf.

For every series an enabled library holds (Calibre for ebooks, Audiobookshelf for
audiobooks), Hardcover's series listing is the reference for which volumes exist. Each
volume is then, per format, owned (the library matcher says so, or the library holds
that series at the same position), requested (a request exists in any status), or
missing. Chosen missing volumes are requested through the path shelf sync uses, so
automatic downloads, dedup and request policy apply unchanged.

Hardcover answers live in the metadata cache for a day, so the report costs one series
search and one series fetch per library series the first time and nothing after. Each
build also runs under a time budget and reports the series it did not get to as
pending; the caller asks again until nothing is pending. The Hardcover provider's own
pacing and 429 back-off apply to every call made here.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from shelfmark.core import library_index
from shelfmark.core.cache import get_metadata_cache
from shelfmark.core.hardcover_sync import _book_to_book_data, _build_provider, resolve_request_owner
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_policy import normalize_content_type
from shelfmark.core.text_match import author_surname, title_tokens_match, tokens

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from shelfmark.core.library_providers import LibraryEntry
    from shelfmark.core.user_db import UserDB
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

FORMATS = ("ebook", "audiobook")
VOLUME_CACHE_TTL = 24 * 60 * 60
# A series Hardcover does not know is asked about again after an hour, not a day: a
# library series renamed to match Hardcover should show up the same morning.
UNRESOLVED_CACHE_TTL = 60 * 60
BUILD_BUDGET_SECONDS = 20.0
MAX_REQUEST_ITEMS = 200
_MAX_VOLUMES = 500
_CACHE_PREFIX = "series-gap:"
# Words naming the packaging rather than the series: "Dune Series" and "Dune" are one.
_SERIES_NOISE = frozenset({"series", "the", "a", "an"})
# A bracketed tail on a library series name ("The Vampire Chronicles (Frank Muller)") is the
# narrator or the edition, which Hardcover's series name does not carry.
_BRACKETED = re.compile(r"\s*[(\[][^)\]]*[)\]]")
# Hardcover often lists the same series twice (a stray duplicate with no books, a spelling
# variant, a wrong credit), so a few candidates are tried before the answer is "not found".
MAX_CANDIDATES = 4

_build_lock = threading.Lock()


class SeriesGapError(ValueError):
    """A request the report cannot act on, with the HTTP status it maps to."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def series_key(name: str | None) -> str:
    """The grouping key for a series name: its words, minus packaging words."""
    return " ".join(tok for tok in tokens(name) if tok not in _SERIES_NOISE)


@dataclass
class LibrarySeries:
    """One series as the libraries hold it, across formats."""

    key: str
    name: str
    author: str | None
    # Content type -> positions held. A format is collected when its key is present,
    # even with no positions (the library filed the books without numbering them).
    positions: dict[str, set[float]] = field(default_factory=dict)


def library_series(entries_by_type: Mapping[str, list[LibraryEntry]]) -> list[LibrarySeries]:
    """The series the libraries hold, grouped across formats by name, sorted by name."""
    by_key: dict[str, LibrarySeries] = {}
    for content_type in FORMATS:
        for entry in entries_by_type.get(content_type, ()):
            key = series_key(entry.series_name)
            if not key or not entry.series_name:
                continue
            series = by_key.get(key)
            if series is None:
                series = LibrarySeries(key, entry.series_name.strip(), entry.author)
                by_key[key] = series
            elif series.author is None and entry.author:
                series.author = entry.author
            held = series.positions.setdefault(content_type, set())
            if entry.series_index is not None:
                held.add(float(entry.series_index))
    return sorted(by_key.values(), key=lambda series: series.name.casefold())


@dataclass
class HardcoverSeries:
    """Hardcover's listing for a series: one volume per position, released only."""

    id: int
    name: str
    volumes: list[BookMetadata]

    @property
    def author(self) -> str | None:
        for book in self.volumes:
            if book.authors:
                return book.authors[0]
        return None


@dataclass
class Listing:
    """Hardcover's answer for a library series.

    ``series`` when Hardcover lists it, else ``message`` says why not. ``pending`` means
    the answer is not cached and this build did not have time to fetch it.
    """

    series: HardcoverSeries | None = None
    message: str | None = None
    pending: bool = False


def hardcover_provider() -> Any | None:
    """The Hardcover provider on the app-level token, or None when none is configured."""
    return _build_provider()


def clear_cache() -> int:
    """Forget every cached Hardcover answer; returns how many entries were dropped."""
    return get_metadata_cache().invalidate_prefix(_CACHE_PREFIX)


def _resolution_key(series: LibrarySeries) -> str:
    return f"{_CACHE_PREFIX}resolve:{series.key}|{author_surname(series.author) or ''}"


def _volumes_key(series_id: int) -> str:
    return f"{_CACHE_PREFIX}volumes:{series_id}"


def _hit_document(hit: object) -> dict[str, Any] | None:
    if not isinstance(hit, dict):
        return None
    document = hit.get("document", hit)
    return document if isinstance(document, dict) else None


def _int(value: object) -> int:
    try:
        return int(str(value))
    except TypeError, ValueError:
        return 0


def _series_keys(name: str) -> set[str]:
    """The keys a series name goes by: whole, and without a bracketed narrator or edition."""
    return {key for key in (series_key(name), series_key(_BRACKETED.sub("", name))) if key}


def _search_series(provider: Any, series: LibrarySeries) -> list[dict[str, Any]]:
    """Hardcover's series that could be this one, best first, as ``{"id", "name"}`` dicts.

    A name that matches exactly (packaging words and a bracketed narrator aside) comes before
    one that merely holds the library's words ("Dungeon Crawler Carl" finds "Dungeon Crawler
    Carl (DCC)"), and within each the series with the most primary books first, which puts the
    real listing ahead of an empty duplicate. A candidate credited to another author is never
    offered. The caller checks the volumes, so the first candidate that holds up wins.
    """
    from shelfmark.metadata_providers.hardcover import (
        SERIES_SEARCH_FIELDS,
        SERIES_SEARCH_SORT,
        SERIES_SEARCH_WEIGHTS,
    )

    hits = provider._search_field_hits(
        query=series.name,
        query_type="Series",
        limit=10,
        sort=SERIES_SEARCH_SORT,
        fields=SERIES_SEARCH_FIELDS,
        weights=SERIES_SEARCH_WEIGHTS,
    )
    surname = author_surname(series.author)
    wanted = _series_keys(series.name) | {series.key}
    bare_words = set(tokens(_BRACKETED.sub("", series.name)))
    exact: list[tuple[int, dict[str, Any]]] = []
    partial: list[tuple[int, dict[str, Any]]] = []
    for hit in hits or []:
        document = _hit_document(hit)
        if document is None:
            continue
        series_id = _int(document.get("id"))
        name = str(document.get("name") or "").strip()
        if series_id < 1 or not name:
            continue
        author_name = document.get("author_name")
        if not author_name and isinstance(document.get("author"), dict):
            author_name = document["author"].get("name")
        if surname and author_name and surname not in tokens(str(author_name)):
            continue
        candidate = {"id": series_id, "name": name}
        weight = _int(document.get("primary_books_count")) or _int(document.get("books_count"))
        if series_key(name) in wanted:
            exact.append((weight, candidate))
        elif title_tokens_match(series.name, set(tokens(name))) or title_tokens_match(
            " ".join(sorted(bare_words)), set(tokens(name))
        ):
            partial.append((weight, candidate))
    # sorted() is stable, so equal weights keep Hardcover's own ranking.
    ranked = sorted(exact, key=lambda item: -item[0]) + sorted(partial, key=lambda item: -item[0])
    return [candidate for _weight, candidate in ranked]


def _position(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def _fetch_volumes(provider: Any, series_id: int) -> list[BookMetadata]:
    """Hardcover's released, non-compilation volumes for a series, in series order."""
    result = provider._fetch_series_books_by_id(
        series_id,
        1,
        _MAX_VOLUMES,
        exclude_compilations=True,
        exclude_unreleased=True,
    )
    volumes: list[BookMetadata] = []
    for book in result.books or []:
        position = _position(book.series_position)
        if position is None:
            continue
        book.series_position = position
        volumes.append(book)
    return volumes


def _volumes_by_author(volumes: list[BookMetadata], author: str | None) -> bool:
    """True when some volume is credited to the library's author (or none is known)."""
    surname = author_surname(author)
    if surname is None:
        return True
    return any(surname in tokens(name) for book in volumes for name in book.authors)


def _cached_volumes(provider: Any, series_id: int, *, fetch: bool) -> list[BookMetadata] | None:
    """Cached volumes for a Hardcover series id, fetched when allowed and not cached.

    An empty answer is not cached: Hardcover answering nothing is a failed call (rate
    limit, outage) far more often than an empty series, and a day of that would hide
    the series.
    """
    cache = get_metadata_cache()
    cached = cache.get(_volumes_key(series_id))
    if isinstance(cached, list):
        return list(cached)
    if not fetch:
        return None
    volumes = _fetch_volumes(provider, series_id)
    if volumes:
        cache.set(_volumes_key(series_id), volumes, VOLUME_CACHE_TTL)
    return volumes


def lookup(provider: Any, series: LibrarySeries, *, fetch: bool = True) -> Listing:
    """Hardcover's listing for ``series``, from the cache or (when ``fetch``) Hardcover."""
    cache = get_metadata_cache()
    resolution = cache.get(_resolution_key(series))
    if not isinstance(resolution, dict):
        if not fetch:
            return Listing(pending=True)
        candidates = _search_series(provider, series)
        if not candidates:
            cache.set(_resolution_key(series), {"id": None}, UNRESOLVED_CACHE_TTL)
            return Listing(message="Not found on Hardcover")
        message: str | None = None
        refused: str | None = None
        for found in candidates[:MAX_CANDIDATES]:
            volumes = _cached_volumes(provider, found["id"], fetch=True) or []
            if not volumes:
                message = message or f"Hardcover listed no released volumes for '{found['name']}'"
                continue
            if not _volumes_by_author(volumes, series.author):
                credited = next(
                    (book.authors[0] for book in volumes if book.authors), "another author"
                )
                refused = (
                    refused
                    or f"Hardcover's '{found['name']}' is by {credited}, not {series.author}"
                )
                continue
            cache.set(_resolution_key(series), dict(found), VOLUME_CACHE_TTL)
            return Listing(series=HardcoverSeries(found["id"], found["name"], volumes))
        # Only a refusal is remembered: an empty answer is more often a failed call than an
        # empty series, and a stale "no volumes" would hide the series for the cache's life.
        if refused:
            cache.set(
                _resolution_key(series), {"id": None, "message": refused}, UNRESOLVED_CACHE_TTL
            )
            return Listing(message=refused)
        return Listing(message=message)

    series_id = _int(resolution.get("id"))
    if series_id < 1:
        return Listing(message=str(resolution.get("message") or "Not found on Hardcover"))
    volumes = _cached_volumes(provider, series_id, fetch=fetch)
    if volumes is None:
        return Listing(pending=True)
    name = str(resolution.get("name") or "")
    if not volumes:
        return Listing(message=f"Hardcover listed no released volumes for '{name}'")
    return Listing(series=HardcoverSeries(series_id, name, volumes))


@dataclass
class RequestedIndex:
    """Every request in any status, keyed the two ways a volume can match one."""

    keys: set[tuple[str, str]] = field(default_factory=set)  # (provider_id, content_type)
    titles: set[tuple[str, str, str]] = field(default_factory=set)  # (title, author, type)


def _match_text(value: object) -> str:
    return " ".join(tokens(str(value or "")))


def _author_of(book: BookMetadata) -> str:
    return book.search_author or (book.authors[0] if book.authors else "")


def requested_index(user_db: UserDB) -> RequestedIndex:
    index = RequestedIndex()
    for row in user_db.list_requests():
        book_data = row.get("book_data") or {}
        if not isinstance(book_data, dict):
            continue
        content_type = normalize_content_type(
            row.get("content_type") or book_data.get("content_type")
        )
        provider_id = book_data.get("provider_id") or book_data.get("id")
        if provider_id is not None:
            index.keys.add((str(provider_id), content_type))
        title = _match_text(book_data.get("title"))
        if title:
            index.titles.add((title, _match_text(book_data.get("author")), content_type))
    return index


def _is_requested(index: RequestedIndex, book: BookMetadata, content_type: str) -> bool:
    if (str(book.provider_id), content_type) in index.keys:
        return True
    return (_match_text(book.title), _match_text(_author_of(book)), content_type) in index.titles


def volume_status(
    book: BookMetadata,
    content_type: str,
    *,
    entries: list[LibraryEntry],
    positions: set[float],
    requested: RequestedIndex,
) -> str:
    """``owned``, ``collection``, ``requested`` or ``missing`` for one volume and format.

    Owned when the library matcher finds the book, or the library holds this series at
    the volume's position under whatever title it gave it. A collection (an omnibus
    holding the volume) is reported as such. Requested counts any status, so a rejected
    or cancelled request still stops the volume from being offered again.
    """
    holding = library_index.match_entries(book, entries)
    if holding == "owned" or book.series_position in positions:
        return "owned"
    if holding == "collection":
        return "collection"
    if _is_requested(requested, book, content_type):
        return "requested"
    return "missing"


def _volume_row(
    book: BookMetadata,
    series: LibrarySeries,
    *,
    entries_by_type: Mapping[str, list[LibraryEntry]],
    requested: RequestedIndex,
) -> dict[str, Any]:
    statuses: dict[str, str | None] = {}
    for content_type, entries in entries_by_type.items():
        # A format the series is not collected in still gets a status (nothing held, so
        # missing unless requested), so its volumes can be picked one by one.
        positions = series.positions.get(content_type, set())
        statuses[content_type] = volume_status(
            book, content_type, entries=entries, positions=positions, requested=requested
        )
    return {
        "position": book.series_position,
        "title": book.search_title or book.title,
        "provider_id": str(book.provider_id),
        "year": book.publish_year,
        "statuses": statuses,
    }


def _series_row(
    series: LibrarySeries,
    listing: Listing,
    *,
    entries_by_type: Mapping[str, list[LibraryEntry]],
    requested: RequestedIndex,
) -> dict[str, Any]:
    volumes: list[dict[str, Any]] = []
    hardcover: dict[str, Any] | None = None
    if listing.series is not None:
        hardcover = {
            "id": listing.series.id,
            "name": listing.series.name,
            "author": listing.series.author,
        }
        volumes = [
            _volume_row(book, series, entries_by_type=entries_by_type, requested=requested)
            for book in listing.series.volumes
        ]
    formats: dict[str, dict[str, Any]] = {}
    for content_type in entries_by_type:
        counts = {"owned": 0, "requested": 0, "missing": 0}
        for volume in volumes:
            status = volume["statuses"].get(content_type)
            if status in ("owned", "collection"):
                counts["owned"] += 1
            elif status in counts:
                counts[status] += 1
        formats[content_type] = {"collected": content_type in series.positions, **counts}
    if listing.pending:
        status = "pending"
    elif listing.series is None:
        status = "unresolved"
    else:
        status = "ok"
    return {
        "key": series.key,
        "name": series.name,
        "author": series.author,
        "status": status,
        "message": listing.message,
        "hardcover": hardcover,
        "formats": formats,
        "volumes": volumes,
    }


def _library_entries() -> dict[str, list[LibraryEntry]]:
    """Entries per enabled content type; raises LibraryUnavailableError when one is down."""
    return {
        content_type: library_index.entries_for_content_type(content_type, strict=True)
        for content_type in library_index.enabled_content_types()
    }


def build_report(
    user_db: UserDB,
    provider: Any,
    *,
    budget_seconds: float = BUILD_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """The report: every library series with its Hardcover volumes and their statuses.

    Series whose Hardcover answer is cached cost nothing. Uncached ones are fetched
    until ``budget_seconds`` is spent; the rest come back with status ``pending`` and
    are counted in ``pending`` so the caller knows to ask again.
    """
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    entries_by_type = _library_entries()
    if not entries_by_type:
        return {"formats": [], "series": [], "pending": 0, "generated_at": generated_at}
    requested = requested_index(user_db)
    deadline = clock() + budget_seconds
    rows: list[dict[str, Any]] = []
    pending = 0
    with _build_lock:
        for series in library_series(entries_by_type):
            try:
                listing = lookup(provider, series, fetch=clock() < deadline)
            except Exception as exc:  # noqa: BLE001 - one series failing must not hide the rest
                logger.warning("series-gap: Hardcover lookup failed for '%s': %s", series.name, exc)
                listing = Listing(message=f"Hardcover lookup failed: {exc}")
            if listing.pending:
                pending += 1
            rows.append(
                _series_row(series, listing, entries_by_type=entries_by_type, requested=requested)
            )
    return {
        "formats": list(entries_by_type),
        "series": rows,
        "pending": pending,
        "generated_at": generated_at,
    }


def _parse_item(raw: object) -> tuple[int, str, str]:
    if not isinstance(raw, dict):
        raise SeriesGapError("Each item must be an object")
    series_id = _int(raw.get("series_id"))
    provider_id = str(raw.get("provider_id") or "").strip()
    content_type = str(raw.get("content_type") or "").strip().lower()
    if series_id < 1:
        raise SeriesGapError("series_id must be a positive integer")
    if not provider_id:
        raise SeriesGapError("provider_id is required")
    if content_type not in FORMATS:
        raise SeriesGapError("content_type must be 'ebook' or 'audiobook'")
    return series_id, provider_id, content_type


def _note(book: BookMetadata) -> str:
    position = book.series_position
    number = f"{position:g}" if isinstance(position, (int, float)) else "?"
    return f"Series gap: {book.series_name or 'series'} #{number}"


def request_volumes(user_db: UserDB, provider: Any, items: object) -> dict[str, Any]:
    """Create a book-level request for each chosen volume, the way shelf sync does.

    Requests are owned by the lowest-id admin (the same owner as synced requests, so
    automatic downloads approve them). A volume the library already holds, or that any
    request already names, is skipped and reported as such; so is a duplicate the
    request service refuses. Raises SeriesGapError for a malformed payload and
    LibraryUnavailableError when a library cannot be read.
    """
    if not isinstance(items, list) or not items:
        raise SeriesGapError("items must be a non-empty list")
    if len(items) > MAX_REQUEST_ITEMS:
        raise SeriesGapError(f"At most {MAX_REQUEST_ITEMS} volumes per call")
    parsed = [_parse_item(raw) for raw in items]

    owner = resolve_request_owner(user_db)
    if owner is None:
        raise SeriesGapError("No admin user exists to own the requests", status_code=503)
    entries_by_type = _library_entries()
    requested = requested_index(user_db)

    from shelfmark.core.requests_service import RequestServiceError, create_request

    summary: dict[str, Any] = {"created": 0, "skipped": 0, "errors": 0, "items": []}
    for series_id, provider_id, content_type in parsed:
        outcome: dict[str, Any] = {
            "series_id": series_id,
            "provider_id": provider_id,
            "content_type": content_type,
            "title": None,
            "result": None,
        }
        summary["items"].append(outcome)
        volumes = _cached_volumes(provider, series_id, fetch=True) or []
        book = next((b for b in volumes if str(b.provider_id) == provider_id), None)
        if book is None:
            outcome["result"] = "unknown_volume"
            summary["errors"] += 1
            continue
        outcome["title"] = book.search_title or book.title
        entries = entries_by_type.get(content_type)
        status = (
            volume_status(book, content_type, entries=entries, positions=set(), requested=requested)
            if entries is not None
            else ("requested" if _is_requested(requested, book, content_type) else "missing")
        )
        if status != "missing":
            outcome["result"] = "owned" if status == "collection" else status
            summary["skipped"] += 1
            continue
        try:
            row = create_request(
                user_db,
                user_id=owner,
                source_hint=None,
                content_type=content_type,
                request_level="book",
                policy_mode="request_book",
                book_data=_book_to_book_data(book, content_type),
                note=_note(book),
            )
        except RequestServiceError as exc:
            if exc.code == "duplicate_pending_request":
                outcome["result"] = "requested"
                summary["skipped"] += 1
            else:
                outcome["result"] = "error"
                outcome["message"] = str(exc)
                summary["errors"] += 1
            continue
        requested.keys.add((provider_id, content_type))
        outcome["result"] = "created"
        outcome["request_id"] = row.get("id")
        summary["created"] += 1
        logger.info(
            "series-gap: requested %s '%s' (%s #%s)",
            content_type,
            outcome["title"],
            book.series_name,
            book.series_position,
        )
    return summary
