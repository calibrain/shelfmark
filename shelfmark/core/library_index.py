"""Library ownership check: is this book already in one of the user's libraries?

Each ``LibraryProvider`` (see ``library_providers``) indexes its library into
``LibraryEntry`` rows. This module caches those rows per provider, routes a lookup to
the providers that hold the requested content type, and matches by identifier first
(the book's id in the same metadata provider, ISBN in either 10 or 13 form), otherwise
fuzzy title-token overlap plus the author surname (``text_match``) - the same rule the
release matcher in ``auto_download`` uses.

Fail-open by design: a disabled or unreachable library never stalls the pipeline.
``is_in_library`` answers False (or from the stale cache) and logs a warning.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from shelfmark.core.library_providers import all_providers
from shelfmark.core.logger import setup_logger
from shelfmark.core.text_match import (
    author_surname,
    bundle_markers,
    extra_work_tokens,
    isbn_variants,
    significant_tokens,
    significant_tokens_match,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shelfmark.core.library_providers import LibraryEntry, LibraryProvider
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

_CACHE_TTL_SECONDS = 600  # Re-index a library at most every 10 minutes unless it changed.
# After a failed index, answer from the stale cache for this long before trying again, so a
# search page with dozens of results costs one failed attempt and one warning, not one each.
_FAILURE_BACKOFF_SECONDS = 60


@dataclass
class _CacheSlot:
    entries: list[LibraryEntry] | None = None
    fetched_at: float = 0.0
    fingerprint: object | None = None
    failed_at: float | None = None


_lock = threading.Lock()
_cache: dict[str, _CacheSlot] = {}


def _slot(provider_name: str) -> _CacheSlot:
    with _lock:
        return _cache.setdefault(provider_name, _CacheSlot())


def _store(provider_name: str, entries: list[LibraryEntry], fingerprint: object | None) -> None:
    slot = _slot(provider_name)
    with _lock:
        slot.entries = entries
        slot.fetched_at = time.monotonic()
        slot.fingerprint = fingerprint
        slot.failed_at = None


class LibraryUnavailableError(RuntimeError):
    """An enabled library could not be read and there is no earlier index to fall back on."""


def _entries_for(provider: LibraryProvider, *, strict: bool = False) -> list[LibraryEntry]:
    """Cached entries for one provider, re-indexed past the TTL or when the library changed.

    A library that cannot be read answers from its stale cache when there is one. With
    nothing cached the default is to fail open (no entries, so nothing counts as owned),
    which suits badges. ``strict`` raises instead, for callers that act on the answer.
    """
    slot = _slot(provider.name)
    with _lock:
        backing_off = slot.failed_at is not None and (
            time.monotonic() - slot.failed_at < _FAILURE_BACKOFF_SECONDS
        )
        stale = slot.entries
    if backing_off:
        # Same answer the failure gave: the stale index, or (strict) a refusal to guess.
        if stale is not None:
            return stale
        if strict:
            msg = f"{provider.describe()} is unavailable (retrying after a short back-off)"
            raise LibraryUnavailableError(msg)
        return []
    try:
        fingerprint = provider.fingerprint()
        with _lock:
            cached = slot.entries
            fresh = time.monotonic() - slot.fetched_at < _CACHE_TTL_SECONDS
            unchanged = fingerprint == slot.fingerprint
        if cached is not None and fresh and unchanged:
            return cached
        entries = provider.fetch_entries()
        with _lock:
            had_entries = bool(slot.entries)
        if had_entries and not entries:
            # A library that held books a moment ago and now lists none is far more likely a
            # changed answer from the server than an emptied shelf. Indexing it would make
            # every owned book look missing, so it is treated like any other failed read
            # (the earlier index keeps answering, strict callers hold). A library that was
            # really emptied clears on the next restart.
            msg = "it listed no items although it held some at the last index"
            raise ValueError(msg)  # noqa: TRY301 - handled by the failure path below
    except Exception as exc:
        with _lock:
            slot.failed_at = time.monotonic()
            stale = slot.entries
        if stale is not None:
            logger.warning(
                "library check: %s unavailable (%s); using stale index", provider.describe(), exc
            )
            return stale
        if strict:
            msg = f"{provider.describe()} is unavailable: {exc}"
            raise LibraryUnavailableError(msg) from exc
        logger.warning("library check: %s unavailable (%s); failing open", provider.describe(), exc)
        return []

    _store(provider.name, entries, fingerprint)
    logger.info("library check: indexed %d %s item(s)", len(entries), provider.display_name)
    return entries


def _enabled_providers(content_type: str | None) -> list[LibraryProvider]:
    return [
        provider
        for provider in all_providers()
        if provider.is_enabled()
        and (content_type is None or content_type in provider.content_types)
    ]


def any_provider_enabled() -> bool:
    return any(provider.is_enabled() for provider in all_providers())


def entries_for_provider(provider: LibraryProvider, *, strict: bool = False) -> list[LibraryEntry]:
    """The cached index of one library; see ``_entries_for`` for the strict/fail-open rule."""
    return _entries_for(provider, strict=strict)


def enabled_content_types() -> list[str]:
    """The content types at least one enabled library holds, in a fixed order."""
    return [
        content_type for content_type in ("ebook", "audiobook") if _enabled_providers(content_type)
    ]


def entries_for_content_type(content_type: str, *, strict: bool = False) -> list[LibraryEntry]:
    """Every enabled library's entries for ``content_type``, concatenated.

    Same caching and fail-open rules as ``is_in_library``; ``strict`` raises
    LibraryUnavailableError instead of answering from nothing.
    """
    entries: list[LibraryEntry] = []
    for provider in _enabled_providers(content_type):
        entries.extend(_entries_for(provider, strict=strict))
    return entries


@dataclass(frozen=True)
class MatchQuery:
    """What the matcher needs to know about the book it is looking for.

    Built from a :class:`BookMetadata` (a request or search result) or from another
    library's :class:`LibraryEntry`, so both go through the same rules.
    """

    title: str
    surname: str | None
    isbns: frozenset[str] = frozenset()
    asins: frozenset[str] = frozenset()
    external_ids: frozenset[tuple[str, str]] = frozenset()


def _book_query(book: BookMetadata) -> MatchQuery:
    external_ids = (
        frozenset({(book.provider, str(book.provider_id))}) if book.provider_id else frozenset()
    )
    return MatchQuery(
        title=book.search_title or book.title,
        surname=author_surname(book.search_author or (book.authors[0] if book.authors else "")),
        isbns=isbn_variants(book.isbn_13) | isbn_variants(book.isbn_10),
        external_ids=external_ids,
    )


def entry_query(entry: LibraryEntry) -> MatchQuery:
    """The query one library's entry makes against another library.

    The title is rebuilt from the entry's title tokens, which already have the series
    label and numbered parenthetical stripped, so a shelf renamed "Alex Cross 02: Kiss
    the Girls" asks the other library for "Kiss the Girls". The matcher's text rules
    only ever look at token sets, so word order is irrelevant.
    """
    return MatchQuery(
        title=" ".join(sorted(entry.title_tokens)),
        surname=author_surname(entry.author),
        isbns=entry.isbns,
        asins=entry.asins,
        external_ids=entry.external_ids,
    )


def _identity_match(query: MatchQuery, entry: LibraryEntry) -> bool:
    """The same metadata-provider id, an ISBN in either form, or an ASIN."""
    return bool(
        query.external_ids & entry.external_ids
        or query.isbns & entry.isbns
        or query.asins & entry.asins
    )


def _text_match(query: MatchQuery, entry: LibraryEntry, title_toks: list[str]) -> str | None:
    """Fuzzy title-token overlap plus the author surname: "owned", "collection" or None."""
    title = query.title
    if not significant_tokens_match(title_toks, entry.tokens):
        return None
    if query.surname is not None and query.surname not in entry.tokens:
        return None
    # A shorter search title is a subset of every longer shelf title sharing its
    # words, so "Dune" matched "Dune Messiah". Words the shelf adds that name
    # another work disqualify it; packaging words do not.
    if extra_work_tokens(set(entry.title_tokens), title, set(entry.context_tokens)):
        return None
    if bundle_markers(entry.title_tokens, title):
        return "collection"
    return "owned"


def find_match(query: MatchQuery, entries: list[LibraryEntry]) -> tuple[str, LibraryEntry] | None:
    """The entry holding ``query`` and how: ``("owned", entry)``, ``("collection", entry)``.

    Identifiers decide first, then the text rules. ``"collection"`` means a shelf title
    that bundles several works matched, e.g. an omnibus. The reader has the book, but
    saying so plainly would misdescribe what is on the shelf.
    """
    for entry in entries:
        if _identity_match(query, entry):
            return "owned", entry

    collection: tuple[str, LibraryEntry] | None = None
    title_toks = significant_tokens(query.title)
    for entry in entries:
        kind = _text_match(query, entry, title_toks)
        if kind == "owned":
            return "owned", entry
        if kind == "collection":
            collection = collection or ("collection", entry)

    return collection


def match_entries(book: BookMetadata, entries: list[LibraryEntry]) -> str | None:
    """How ``book`` is held: ``"owned"``, ``"collection"``, or None when it is not."""
    # The search title is the looser of the two, so it goes first. Hardcover cuts "Series:
    # Book Title" down to the series name when it cannot tell that prefix from a title, and a
    # shelf title with the book's own words then reads as a longer, different work. The full
    # title is the more specific of the two and names the book as the shelf does.
    query = _book_query(book)
    queries = [query]
    if book.title and book.title != query.title:
        queries.append(replace(query, title=book.title))

    collection: str | None = None
    for candidate in queries:
        found = find_match(candidate, entries)
        if found is None:
            continue
        if found[0] == "owned":
            return "owned"
        collection = collection or found[0]
    return collection


def matching_entries(entry: LibraryEntry, entries: list[LibraryEntry]) -> list[LibraryEntry]:
    """Every entry in ``entries`` that is the same work as ``entry``, held on its own.

    Two libraries' copies of one work, which is what a cross-format marker names, so
    all of them count: two editions of an ebook both have the one audiobook. A
    collection is not a copy: the omnibus contains the work, but marking it as "the
    ebook is in the library" would overstate what the other side has.

    Both authors are known here, so the text rule checks the surname both ways. The
    one-way check reads any token as the surname, which lets Ethan Cross's "Cross"
    claim James Patterson's "Cross" through the title word; the other author's surname
    missing from this entry's tokens rules that out.
    """
    query = entry_query(entry)
    found = [other for other in entries if _identity_match(query, other)]
    title_toks = significant_tokens(query.title)
    for other in entries:
        if other in found:
            continue
        surname = author_surname(other.author)
        if surname is not None and surname not in entry.tokens:
            continue
        if _text_match(query, other, title_toks) == "owned":
            found.append(other)
    return found


def book_matches_entries(book: BookMetadata, entries: list[LibraryEntry]) -> bool:
    """True if ``book`` is on the shelf at all, however it is packaged."""
    return match_entries(book, entries) is not None


def require_ready(content_type: str | None = None) -> None:
    """Raise LibraryUnavailableError unless every enabled library can be read (or is cached).

    ``content_type`` limits the check to the libraries holding that type.
    """
    for provider in _enabled_providers(content_type):
        _entries_for(provider, strict=True)


def is_in_library(
    book: BookMetadata, content_type: str | None = None, *, strict: bool = False
) -> bool:
    """True if an enabled library holding ``content_type`` already has ``book``.

    ``content_type`` None consults every enabled provider. A library that cannot be read
    counts as not holding the book, unless ``strict``, which raises LibraryUnavailableError.
    """
    return any(
        book_matches_entries(book, _entries_for(provider, strict=strict))
        for provider in _enabled_providers(content_type)
    )


def _holding(book: BookMetadata, content_type: str) -> str | None:
    """Strongest holding across the enabled libraries for one content type."""
    kinds = {
        match_entries(book, _entries_for(provider)) for provider in _enabled_providers(content_type)
    }
    if "owned" in kinds:
        return "owned"
    return "collection" if "collection" in kinds else None


def ownership(book: BookMetadata) -> dict[str, str | None] | None:
    """Per-format holding for the UI: ``{"ebook": "owned" | "collection" | None}``.

    Only content types with an enabled library are reported; None when no library
    check is enabled at all. Fail-open like ``is_in_library``.
    """
    result = {
        content_type: _holding(book, content_type)
        for content_type in ("ebook", "audiobook")
        if _enabled_providers(content_type)
    }
    return result or None


def test_connection(provider_name: str, current_values: Mapping[str, Any] | None) -> dict[str, Any]:
    """Settings action: index one library now and report the item count.

    ``current_values`` are the unsaved form values, so the button works before Save.
    """
    provider = next((p for p in all_providers(current_values) if p.name == provider_name), None)
    if provider is None:
        return {"success": False, "message": f"Unknown library provider: {provider_name}"}
    try:
        fingerprint = provider.fingerprint()
        entries = provider.fetch_entries()
    except Exception as exc:  # noqa: BLE001 - surface any error to the user
        return {"success": False, "message": f"{provider.describe()}: {exc}"}
    _store(provider.name, entries, fingerprint)
    return {"success": True, "message": f"{provider.describe()}: indexed {len(entries)} item(s)."}
