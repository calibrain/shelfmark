"""Sync a Hardcover reading shelf (e.g. "Want to Read") into Shelfmark requests.

This replaces the standalone ``scripts/sync_hardcover_wishlist.py`` with an in-app
service that reuses the registered Hardcover metadata provider (so synced requests
carry full metadata incl. covers) and the request-service validation/dedup path.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from shelfmark.core import library_index
from shelfmark.core.config import config as app_config
from shelfmark.core.download_history_service import ACTIVE_DOWNLOAD_STATUS
from shelfmark.core.hardcover_scheduler import cycle_expired
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_policy import normalize_content_type
from shelfmark.core.utils import transform_cover_url

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shelfmark.core.user_db import UserDB
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

# Hardcover status_id -> shelf. Default sync target is "Want to Read" (1).
DEFAULT_SYNC_STATUSES = ("1",)
_PAGE_LIMIT = 25  # Hardcover API page size.
_MAX_PAGES = 40  # Safety bound (~1000 books) so a bad response can't loop forever.


def _configured_token(user_id: int | None = None) -> str:
    """Token for this sync: the user's own if they connected one, else the app-level token.

    ``HARDCOVER_SYNC_TOKEN`` is user overridable, so passing a user id resolves their
    value first and falls back to the global one. Without a user id this is the
    single-account behaviour it has always had.
    """
    token = app_config.get("HARDCOVER_SYNC_TOKEN", "", user_id=user_id) or app_config.get(
        "HARDCOVER_API_KEY", "", user_id=user_id
    )
    return str(token or "").strip()


def _user_token(user_id: int) -> str:
    """The token this user connected themselves, ignoring the app-level fallback."""
    token = app_config.get_user_override("HARDCOVER_SYNC_TOKEN", user_id=user_id)
    return str(token or "").strip()


def users_with_hardcover_token(user_db: UserDB) -> list[int]:
    """Ids of users who connected their own Hardcover token, lowest id first."""
    user_ids: list[int] = []
    for user in user_db.list_users():
        raw_id = user.get("id")
        if raw_id is None:
            continue
        user_id = int(raw_id)
        if _user_token(user_id):
            user_ids.append(user_id)
    return sorted(user_ids)


def _configured_statuses() -> list[int]:
    raw = app_config.get("HARDCOVER_SYNC_STATUSES", list(DEFAULT_SYNC_STATUSES))
    values: list[object]
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, (list, tuple)):
        values = list(raw)
    else:
        values = []
    statuses: list[int] = []
    for value in values:
        try:
            statuses.append(int(str(value).strip()))
        except ValueError:
            continue
    return statuses or [int(s) for s in DEFAULT_SYNC_STATUSES]


_CONTENT_TYPES = {"ebook": ["ebook"], "audiobook": ["audiobook"], "both": ["ebook", "audiobook"]}
DEFAULT_SYNC_CONTENT_TYPE = "audiobook"


def _configured_content_types() -> list[str]:
    """Content types to request for every shelf book ("both" → an ebook and an audiobook)."""
    raw = str(app_config.get("HARDCOVER_SYNC_CONTENT_TYPE", DEFAULT_SYNC_CONTENT_TYPE) or "")
    return list(_CONTENT_TYPES.get(raw.strip().lower(), _CONTENT_TYPES[DEFAULT_SYNC_CONTENT_TYPE]))


def _build_provider(user_id: int | None = None) -> Any | None:
    from shelfmark.metadata_providers import get_provider

    token = _configured_token(user_id)
    if not token:
        logger.warning("hardcover-sync: no Hardcover token configured")
        return None
    provider = get_provider("hardcover", api_key=token)
    if not provider.is_available():
        logger.warning("hardcover-sync: Hardcover provider unavailable (bad token?)")
        return None
    return provider


def resolve_request_owner(user_db: UserDB) -> int | None:
    """Return the lowest-id admin, who owns synced requests and approves auto-downloads."""
    admin = user_db.get_first_admin()
    return int(admin["id"]) if admin else None


def _primary_author(book: BookMetadata) -> str:
    if book.search_author:
        return book.search_author
    if book.authors:
        return book.authors[0]
    return "Unknown"


def _book_to_book_data(book: BookMetadata, content_type: str) -> dict[str, Any]:
    book_data: dict[str, Any] = {
        "title": book.title,
        "author": _primary_author(book),
        "provider": "hardcover",
        "provider_id": str(book.provider_id),
        "content_type": content_type,
    }
    # Proxy the cover the same way search results are, so the row is ready for the UI.
    if book.cover_url:
        book_data["preview"] = transform_cover_url(book.cover_url, f"hardcover_{book.provider_id}")
    if book.publish_year:
        book_data["year"] = book.publish_year
    if book.subtitle:
        book_data["subtitle"] = book.subtitle
    return book_data


def _existing_request_keys(user_db: UserDB, user_id: int | None = None) -> set[tuple[str, str]]:
    """``(hardcover id, content_type)`` pairs already requested, in any status.

    Other providers' ids are not counted, so one that happens to equal a Hardcover id
    cannot skip a book. Scoped to one owner when syncing that user's own shelf, matching
    the per-user duplicate check in requests_service, so one user's request does not
    silently swallow another user's. The app-level path stays instance-wide.
    """
    keys: set[tuple[str, str]] = set()
    for row in user_db.list_requests(user_id=user_id):
        book_data = row.get("book_data") or {}
        if isinstance(book_data, dict) and book_data.get("provider") == "hardcover":
            pid = book_data.get("provider_id") or book_data.get("id")
            if pid is not None:
                # Normalized the way requests_service's duplicate check does, so a row
                # with no content type counts as the ebook request it is treated as.
                content_type = normalize_content_type(
                    row.get("content_type") or book_data.get("content_type")
                )
                keys.add((str(pid), content_type))
    return keys


# An "active" history row older than this is a download the app lost track of (the
# process died before the row was finalised), not one still running.
_ACTIVE_DOWNLOAD_MAX_AGE = timedelta(hours=24)


def _history_timestamp(value: object) -> datetime | None:
    """A stored history stamp (SQLite's default or ISO with an offset) as an aware datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@contextmanager
def _history_connection(db_path: str | None) -> Iterator[sqlite3.Connection | None]:
    """One read connection to the history for a whole sync pass, None when there is none.

    The history check runs for every book on the shelf, so opening a connection per book
    is a lot of work for a slow disk or a small board.
    """
    conn: sqlite3.Connection | None = None
    if db_path:
        try:
            conn = sqlite3.connect(db_path)
        except sqlite3.Error as exc:
            logger.debug("hardcover-sync: history dedup connection failed: %s", exc)
    try:
        yield conn
    finally:
        if conn is not None:
            conn.close()


def _already_downloaded(
    conn: sqlite3.Connection | None,
    provider: str,
    provider_id: str,
    content_type: str,
    *,
    now: datetime | None = None,
) -> bool:
    """True when a request for this book and format has a download finished or running.

    Keyed on ``download_history.request_id`` and the request's ``book_data``: the history
    row itself holds the release's title and the source's own content label (Anna's
    Archive writes "book (fiction)"), neither of which compares to the book. The format
    is the request's, normalised the way requests_service stores it.

    The request-key check in front of this covers every request the sync can see, so
    this only decides anything for a request it cannot: another user's, on a per-user
    sync. Failed and cancelled downloads don't count, and neither does an active row
    older than :data:`_ACTIVE_DOWNLOAD_MAX_AGE`, so a download that broke or was lost
    does not hold the book back for good.
    """
    if conn is None:
        return False
    wanted = normalize_content_type(content_type)
    cutoff = (now or datetime.now(UTC)) - _ACTIVE_DOWNLOAD_MAX_AGE
    try:
        rows = conn.execute(
            "SELECT h.final_status, h.terminal_at, r.content_type "
            "FROM download_history AS h "
            "JOIN download_requests AS r ON r.id = h.request_id "
            "WHERE h.final_status IN ('complete', ?) "
            "AND json_extract(r.book_data, '$.provider') = ? "
            "AND CAST(json_extract(r.book_data, '$.provider_id') AS TEXT) = ?",
            (ACTIVE_DOWNLOAD_STATUS, provider, str(provider_id)),
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug("hardcover-sync: history dedup query failed: %s", exc)
        return False
    for final_status, terminal_at, request_content_type in rows:
        if normalize_content_type(request_content_type) != wanted:
            continue
        if final_status == ACTIVE_DOWNLOAD_STATUS:
            stamp = _history_timestamp(terminal_at)
            if stamp is None or stamp < cutoff:
                continue
        return True
    return False


def _fetch_status_books(provider: Any, status_id: int) -> tuple[list[BookMetadata], bool]:
    """Page through a Hardcover status shelf: the books read, and whether a fetch failed.

    Each page is one Hardcover request, bounded by the provider's own timeout and retry
    cap. A shelf left unfinished because the scheduler gave up on the cycle counts as a
    failed fetch: the books read so far are still returned, so they can be requested.
    """
    books: list[BookMetadata] = []
    failed = False
    for page in range(1, _MAX_PAGES + 1):
        if cycle_expired():
            logger.warning(
                "hardcover-sync: cycle out of time; shelf %s not read past page %s",
                status_id,
                page - 1,
            )
            failed = True
            break
        try:
            result = provider._fetch_current_user_books_by_status(status_id, page, _PAGE_LIMIT)
        except Exception as exc:  # noqa: BLE001 - network/provider errors shouldn't abort the sync
            logger.warning(
                "hardcover-sync: fetch failed (status=%s page=%s): %s", status_id, page, exc
            )
            failed = True
            break
        page_books = list(result.books or [])
        books.extend(page_books)
        if not result.has_more or not page_books:
            break
    return books, failed


def sync_wishlist(
    user_db: UserDB,
    *,
    db_path: str | None = None,
    user_id: int | None = None,
) -> dict[str, int]:
    """Sync configured Hardcover shelves into pending requests.

    One request is created per shelf book and configured content type ("both" means an
    ebook and an audiobook request each), so each format is checked against its own
    library. Returns ``{"added", "skipped", "in_library", "errors"}``. Requires a
    configured token; enable-gating is the caller's responsibility (see hardcover_scheduler).

    Pass ``user_id`` to sync that user's own Hardcover account: their token is used and
    the requests are theirs. Omit it for the app-level token, whose requests are owned
    by :func:`resolve_request_owner`.
    """
    summary = {"added": 0, "skipped": 0, "in_library": 0, "errors": 0}

    # A caller-supplied id is the user whose own shelf this is, so their token and
    # their existing requests are the ones that matter. Without one this is the
    # app-level pass, owned by an admin and deduped instance-wide as before.
    per_user = user_id is not None
    if user_id is None:
        user_id = resolve_request_owner(user_db)
    if user_id is None:
        logger.warning("hardcover-sync: no admin user exists to own synced requests")
        summary["errors"] += 1
        return summary

    provider = _build_provider(user_id if per_user else None)
    if provider is None:
        summary["errors"] += 1
        return summary

    content_types = _configured_content_types()

    try:
        for content_type in content_types:
            library_index.require_ready(content_type)
    except library_index.LibraryUnavailableError as exc:
        # Requesting now would queue books the library may already hold.
        logger.warning("hardcover-sync: %s; skipping this sync", exc)
        summary["errors"] += 1
        return summary
    known_requests = _existing_request_keys(user_db, user_id if per_user else None)

    from shelfmark.core.requests_service import RequestServiceError, create_request

    with _history_connection(db_path) as history:
        for status_id in _configured_statuses():
            shelf, fetch_failed = _fetch_status_books(provider, status_id)
            if fetch_failed:
                summary["errors"] += 1
            for book in shelf:
                provider_id = str(book.provider_id)
                author = _primary_author(book)

                for content_type in content_types:
                    if (provider_id, content_type) in known_requests:
                        summary["skipped"] += 1
                        continue
                    if _already_downloaded(history, book.provider, provider_id, content_type):
                        summary["skipped"] += 1
                        continue
                    if library_index.is_in_library(book, content_type):
                        summary["in_library"] += 1
                        logger.info(
                            "hardcover-sync: '%s' (%s) already in library; skipping",
                            book.title,
                            content_type,
                        )
                        continue

                    try:
                        create_request(
                            user_db,
                            user_id=user_id,
                            source_hint=None,
                            content_type=content_type,
                            request_level="book",
                            policy_mode="request_book",
                            book_data=_book_to_book_data(book, content_type),
                            note=None,
                        )
                    except RequestServiceError as exc:
                        # Duplicate / max-pending / validation: treat as skip, not failure.
                        if exc.code in {"duplicate_pending_request", "max_pending_reached"}:
                            summary["skipped"] += 1
                        else:
                            logger.warning(
                                "hardcover-sync: could not add '%s' (%s): %s",
                                book.title,
                                content_type,
                                exc,
                            )
                            summary["errors"] += 1
                        continue
                    except Exception:
                        logger.exception(
                            "hardcover-sync: unexpected error adding '%s' (%s)",
                            book.title,
                            content_type,
                        )
                        summary["errors"] += 1
                        continue

                    known_requests.add((provider_id, content_type))
                    summary["added"] += 1
                    logger.info(
                        "hardcover-sync: added %s request for '%s' by %s",
                        content_type,
                        book.title,
                        author,
                    )

    logger.info("hardcover-sync complete: %s", summary)
    return summary
