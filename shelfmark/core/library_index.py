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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shelfmark.core.library_providers import all_providers
from shelfmark.core.logger import setup_logger
from shelfmark.core.text_match import author_surname, isbn_variants, title_tokens_match

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shelfmark.core.library_providers import LibraryEntry, LibraryProvider
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

_CACHE_TTL_SECONDS = 600  # Re-index a library at most every 10 minutes unless it changed.


@dataclass
class _CacheSlot:
    entries: list[LibraryEntry] | None = None
    fetched_at: float = 0.0
    fingerprint: object | None = None


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


def _entries_for(provider: LibraryProvider) -> list[LibraryEntry]:
    """Cached entries for one provider, re-indexed past the TTL or when the library changed."""
    slot = _slot(provider.name)
    try:
        fingerprint = provider.fingerprint()
        with _lock:
            cached = slot.entries
            fresh = time.monotonic() - slot.fetched_at < _CACHE_TTL_SECONDS
            unchanged = fingerprint == slot.fingerprint
        if cached is not None and fresh and unchanged:
            return cached
        entries = provider.fetch_entries()
    except Exception as exc:  # noqa: BLE001 - any failure must fail open
        logger.warning("library check: %s unavailable (%s); failing open", provider.describe(), exc)
        with _lock:
            return slot.entries or []  # Use the stale cache if we have one.

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


def book_matches_entries(book: BookMetadata, entries: list[LibraryEntry]) -> bool:
    """Pure matcher: True if ``book`` matches any library entry (identifier or fuzzy)."""
    if not entries:
        return False

    external_id = (book.provider, str(book.provider_id)) if book.provider_id else None
    book_isbns = isbn_variants(book.isbn_13) | isbn_variants(book.isbn_10)
    for entry in entries:
        if external_id in entry.external_ids or entry.isbns & book_isbns:
            return True

    title = book.search_title or book.title
    surname = author_surname(book.search_author or (book.authors[0] if book.authors else ""))
    for entry in entries:
        if title_tokens_match(title, set(entry.tokens)) and (
            surname is None or surname in entry.tokens
        ):
            return True

    return False


def is_in_library(book: BookMetadata, content_type: str | None = None) -> bool:
    """True if an enabled library holding ``content_type`` already has ``book`` (fail-open).

    ``content_type`` None consults every enabled provider.
    """
    return any(
        book_matches_entries(book, _entries_for(provider))
        for provider in _enabled_providers(content_type)
    )


def ownership(book: BookMetadata) -> dict[str, bool] | None:
    """Per-format ownership for the UI: ``{"ebook": bool, "audiobook": bool}``.

    Only content types with an enabled library are reported; None when no library
    check is enabled at all. Fail-open like ``is_in_library``.
    """
    result = {
        content_type: is_in_library(book, content_type)
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
