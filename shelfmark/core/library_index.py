"""Audiobookshelf (ABS) library ownership check.

Fetches the user's ABS library (title/author/isbn/asin per item), caches it, and
answers whether a given book is already owned — so the Hardcover auto-sync /
auto-download pipeline can skip re-downloading books already on the server.

Matching mirrors the release matcher in ``auto_download``: ISBN exact match as a
bonus, otherwise fuzzy title-token overlap plus the author surname. ABS library
metadata here is messy (titles are often raw folder names, author frequently empty),
so we tokenize title + author + folder path for each item.

Fail-open by design: if the check is disabled or ABS is unreachable, ``is_in_library``
returns False so the pipeline proceeds rather than stalling.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import requests

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.text_match import (
    author_surname,
    normalize_isbn,
    title_tokens_match,
    tokens,
)

if TYPE_CHECKING:
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

_CACHE_TTL_SECONDS = 600  # Re-fetch the library at most every 10 minutes.
_REQUEST_TIMEOUT = 15
_ITEMS_PAGE_LIMIT = 500


@dataclass(frozen=True)
class LibraryEntry:
    """Normalized, matchable representation of one ABS library item."""

    tokens: frozenset[str]
    isbns: frozenset[str]
    asins: frozenset[str]


_lock = threading.Lock()
_cache_entries: list[LibraryEntry] | None = None
_cache_time: float = 0.0


def _config() -> tuple[bool, str, str, list[str]]:
    enabled = bool(app_config.get("LIBRARY_CHECK_ENABLED", False))
    url = str(app_config.get("AUDIOBOOKSHELF_URL", "") or "").strip().rstrip("/")
    token = str(app_config.get("AUDIOBOOKSHELF_TOKEN", "") or "").strip()
    lib_ids_raw = str(app_config.get("AUDIOBOOKSHELF_LIBRARY_IDS", "") or "")
    lib_ids = [s.strip() for s in lib_ids_raw.split(",") if s.strip()]
    return enabled, url, token, lib_ids


def _session(token: str) -> requests.Session:
    from shelfmark.download.network import get_ssl_verify

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    session.verify = get_ssl_verify()
    return session


def _item_to_entry(item: dict[str, Any]) -> LibraryEntry:
    media = item.get("media") or {}
    meta = media.get("metadata") or {}
    title = meta.get("title") or ""
    author = meta.get("authorName") or ""
    rel_path = item.get("relPath") or ""

    tok = set(tokens(title)) | set(tokens(author)) | set(tokens(rel_path))

    isbns = {v for v in (normalize_isbn(meta.get("isbn")),) if v}
    asin = str(meta.get("asin") or "").strip().upper()
    asins = {asin} if asin else set()

    return LibraryEntry(frozenset(tok), frozenset(isbns), frozenset(asins))


def _fetch_library_entries(url: str, token: str, lib_ids: list[str]) -> list[LibraryEntry]:
    session = _session(token)

    resp = session.get(f"{url}/api/libraries", timeout=_REQUEST_TIMEOUT)
    resp.raise_for_status()
    libraries = resp.json().get("libraries", [])

    selected: list[str] = []
    for lib in libraries:
        lib_id = lib.get("id")
        if not lib_id:
            continue
        if lib_ids:
            if lib_id in lib_ids:
                selected.append(lib_id)
        elif lib.get("mediaType") == "book":
            selected.append(lib_id)

    entries: list[LibraryEntry] = []
    for lib_id in selected:
        page = 0
        while True:
            resp = session.get(
                f"{url}/api/libraries/{lib_id}/items",
                params={"limit": _ITEMS_PAGE_LIMIT, "page": page},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", []) or []
            entries.extend(_item_to_entry(item) for item in results)
            total = int(data.get("total", 0) or 0)
            page += 1
            if not results or page * _ITEMS_PAGE_LIMIT >= total:
                break

    return entries


def _get_entries() -> list[LibraryEntry]:
    """Return cached library entries, refreshing past the TTL. Fail-open on error."""
    global _cache_entries, _cache_time

    enabled, url, token, lib_ids = _config()
    if not enabled or not url or not token:
        return []

    now = time.monotonic()
    with _lock:
        if _cache_entries is not None and (now - _cache_time) < _CACHE_TTL_SECONDS:
            return _cache_entries

    try:
        entries = _fetch_library_entries(url, token, lib_ids)
    except Exception as exc:  # noqa: BLE001 - any failure must fail open
        logger.warning("library check: failed to fetch ABS library (%s); failing open", exc)
        with _lock:
            return _cache_entries or []  # Use stale cache if we have one.

    with _lock:
        _cache_entries = entries
        _cache_time = time.monotonic()
    logger.info("library check: indexed %d Audiobookshelf item(s)", len(entries))
    return entries


def book_matches_entries(book: BookMetadata, entries: list[LibraryEntry]) -> bool:
    """Pure matcher: True if ``book`` matches any library entry (ISBN or fuzzy)."""
    if not entries:
        return False

    book_isbns = {normalize_isbn(book.isbn_13), normalize_isbn(book.isbn_10)} - {""}
    if book_isbns:
        for entry in entries:
            if entry.isbns & book_isbns:
                return True

    title = book.search_title or book.title
    surname = author_surname(book.search_author or (book.authors[0] if book.authors else ""))
    for entry in entries:
        if title_tokens_match(title, set(entry.tokens)) and (
            surname is None or surname in entry.tokens
        ):
            return True

    return False


def is_in_library(book: BookMetadata) -> bool:
    """True if ``book`` appears to already exist in the ABS library (fail-open)."""
    return book_matches_entries(book, _get_entries())


def refresh_cache(entries: list[LibraryEntry]) -> None:
    """Replace the cached entries (used after an explicit fetch)."""
    global _cache_entries, _cache_time
    with _lock:
        _cache_entries = entries
        _cache_time = time.monotonic()


def test_connection() -> dict[str, Any]:
    """Settings action: verify ABS connectivity and report the indexed item count."""
    _enabled, url, token, lib_ids = _config()
    if not url or not token:
        return {"success": False, "message": "Set the Audiobookshelf URL and token first."}
    try:
        entries = _fetch_library_entries(url, token, lib_ids)
    except Exception as exc:  # noqa: BLE001 - surface any error to the user
        return {"success": False, "message": f"Could not reach Audiobookshelf: {exc}"}
    refresh_cache(entries)
    return {"success": True, "message": f"Connected. Indexed {len(entries)} library item(s)."}
