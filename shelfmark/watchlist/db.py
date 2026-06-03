"""Watchlist database for Pulsarr author monitoring."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS watchlist_authors (
    id                  INTEGER  PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    author_name         TEXT     NOT NULL,
    hardcover_author_id TEXT,
    ol_author_key       TEXT,
    watch_content_types TEXT     NOT NULL DEFAULT '["ebook","audiobook"]',
    is_active           INTEGER  NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    deleted_at          TIMESTAMP,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_watchlist_authors_hardcover
ON watchlist_authors (user_id, hardcover_author_id)
WHERE hardcover_author_id IS NOT NULL AND deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_watchlist_authors_ol
ON watchlist_authors (user_id, ol_author_key)
WHERE ol_author_key IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_watchlist_authors_user_active
ON watchlist_authors (user_id, is_active, deleted_at);

CREATE INDEX IF NOT EXISTS idx_watchlist_authors_hardcover_id
ON watchlist_authors (hardcover_author_id)
WHERE hardcover_author_id IS NOT NULL AND deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_watchlist_authors_ol_key
ON watchlist_authors (ol_author_key)
WHERE ol_author_key IS NOT NULL AND deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS watchlist_releases (
    id                  INTEGER  PRIMARY KEY AUTOINCREMENT,
    watch_id            INTEGER  NOT NULL REFERENCES watchlist_authors(id) ON DELETE CASCADE,
    user_id             INTEGER  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider            TEXT     NOT NULL,
    provider_book_id    TEXT     NOT NULL,
    book_data           TEXT     NOT NULL,
    publish_date        TEXT,
    content_type        TEXT     NOT NULL,
    action_status       TEXT     NOT NULL DEFAULT 'detected'
                            CHECK (action_status IN ('detected','queued','skipped','ignored')),
    request_id          INTEGER  REFERENCES download_requests(id) ON DELETE SET NULL,
    detected_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actioned_at         TIMESTAMP,
    UNIQUE (watch_id, provider_book_id)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_releases_watch_id
ON watchlist_releases (watch_id, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_watchlist_releases_user_calendar
ON watchlist_releases (user_id, publish_date DESC, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_watchlist_releases_action_status
ON watchlist_releases (user_id, action_status, detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_watchlist_releases_pending
ON watchlist_releases (action_status, detected_at DESC)
WHERE action_status = 'detected';
"""

_VALID_CONTENT_TYPES: frozenset[str] = frozenset({"ebook", "audiobook"})
_VALID_ACTION_STATUSES: frozenset[str] = frozenset(
    {"detected", "queued", "skipped", "ignored"}
)


def _validate_content_types(value: list[str]) -> None:
    """Raise ValueError if content type list is invalid."""
    if not value:
        msg = "watch_content_types must not be empty"
        raise ValueError(msg)
    invalid = [v for v in value if v not in _VALID_CONTENT_TYPES]
    if invalid:
        msg = f"Invalid content types: {invalid}. Must be 'ebook' or 'audiobook'."
        raise ValueError(msg)


def _serialize_json(value: object, field_name: str) -> str:
    """Serialize value to JSON string, raising TypeError on failure."""
    try:
        return json.dumps(value)
    except (TypeError, ValueError) as e:
        msg = f"Failed to serialize {field_name} to JSON"
        raise TypeError(msg) from e


def _parse_author_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row to a plain dict, deserializing JSON fields."""
    if row is None:
        return None
    result = dict(row)
    raw_types = result.get("watch_content_types")
    if isinstance(raw_types, str):
        result["watch_content_types"] = json.loads(raw_types)
    return result


def _parse_release_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row to a plain dict, deserializing JSON fields."""
    if row is None:
        return None
    result = dict(row)
    raw_book = result.get("book_data")
    if isinstance(raw_book, str):
        result["book_data"] = json.loads(raw_book)
    return result


class WatchlistDB:
    """Thread-safe SQLite watchlist database."""

    def __init__(self, db_path: str) -> None:
        """Initialize the watchlist database wrapper for the given SQLite path."""
        self._db_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        """Create watchlist tables if they don't exist."""
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_CREATE_TABLES_SQL)
                conn.commit()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Author watch CRUD
    # ------------------------------------------------------------------

    def add_author(
        self,
        *,
        user_id: int,
        author_name: str,
        hardcover_author_id: str | None = None,
        ol_author_key: str | None = None,
        watch_content_types: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add an author to a user's watchlist.

        At least one of hardcover_author_id or ol_author_key must be provided.
        Raises ValueError on invalid input or duplicate watch.
        """
        if not author_name or not author_name.strip():
            msg = "author_name is required"
            raise ValueError(msg)
        if hardcover_author_id is None and ol_author_key is None:
            msg = "At least one of hardcover_author_id or ol_author_key must be provided"
            raise ValueError(msg)

        content_types = watch_content_types or ["ebook", "audiobook"]
        _validate_content_types(content_types)

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO watchlist_authors (
                        user_id, author_name, hardcover_author_id, ol_author_key,
                        watch_content_types
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        author_name.strip(),
                        hardcover_author_id,
                        ol_author_key,
                        _serialize_json(content_types, "watch_content_types"),
                    ),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM watchlist_authors WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
                result = _parse_author_row(row)
                if result is None:
                    msg = "Failed to load newly created watch entry"
                    raise RuntimeError(msg)
                return result
            except sqlite3.IntegrityError as e:
                msg = f"Watch entry already exists: {e}"
                raise ValueError(msg) from e
            finally:
                conn.close()

    def get_author(self, watch_id: int) -> dict[str, Any] | None:
        """Return a single watch entry by ID, or None if not found."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM watchlist_authors WHERE id = ? AND deleted_at IS NULL",
                (watch_id,),
            ).fetchone()
            return _parse_author_row(row)
        finally:
            conn.close()

    def list_authors(
        self,
        user_id: int,
        *,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """Return all non-deleted watch entries for a user."""
        query = """
            SELECT * FROM watchlist_authors
            WHERE user_id = ? AND deleted_at IS NULL
        """
        params: list[Any] = [user_id]
        if not include_inactive:
            query += " AND is_active = 1"
        query += " ORDER BY author_name ASC"

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [r for row in rows if (r := _parse_author_row(row)) is not None]
        finally:
            conn.close()

    def list_all_active_authors(self) -> list[dict[str, Any]]:
        """Return all active, non-deleted watch entries across all users.

        Used by the scheduler to know what to check.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM watchlist_authors
                WHERE is_active = 1 AND deleted_at IS NULL
                ORDER BY user_id ASC, author_name ASC
                """,
            ).fetchall()
            return [r for row in rows if (r := _parse_author_row(row)) is not None]
        finally:
            conn.close()

    def update_author(
        self,
        watch_id: int,
        *,
        is_active: bool | None = None,
        watch_content_types: list[str] | None = None,
        author_name: str | None = None,
    ) -> dict[str, Any] | None:
        """Update mutable fields on a watch entry. Returns updated row or None."""
        if watch_content_types is not None:
            _validate_content_types(watch_content_types)

        with self._lock:
            conn = self._connect()
            try:
                if is_active is not None:
                    conn.execute(
                        """
                        UPDATE watchlist_authors
                        SET is_active = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND deleted_at IS NULL
                        """,
                        (1 if is_active else 0, watch_id),
                    )
                if watch_content_types is not None:
                    conn.execute(
                        """
                        UPDATE watchlist_authors
                        SET watch_content_types = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND deleted_at IS NULL
                        """,
                        (
                            _serialize_json(watch_content_types, "watch_content_types"),
                            watch_id,
                        ),
                    )
                if author_name is not None:
                    if not author_name.strip():
                        msg = "author_name must not be blank"
                        raise ValueError(msg)
                    conn.execute(
                        """
                        UPDATE watchlist_authors
                        SET author_name = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND deleted_at IS NULL
                        """,
                        (author_name.strip(), watch_id),
                    )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM watchlist_authors WHERE id = ?",
                    (watch_id,),
                ).fetchone()
                return _parse_author_row(row)
            finally:
                conn.close()

    def remove_author(self, watch_id: int) -> bool:
        """Soft-delete a watch entry. Returns True if a row was affected."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    UPDATE watchlist_authors
                    SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND deleted_at IS NULL
                    """,
                    (watch_id,),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Release CRUD
    # ------------------------------------------------------------------

    def upsert_release(
        self,
        *,
        watch_id: int,
        user_id: int,
        provider: str,
        provider_book_id: str,
        book_data: dict[str, Any],
        content_type: str,
        publish_date: str | None = None,
    ) -> dict[str, Any]:
        """Insert a detected release, or return the existing row if already known.

        Idempotent: safe to call repeatedly from the scheduler.
        Raises ValueError on invalid input.
        """
        if content_type not in _VALID_CONTENT_TYPES:
            msg = f"Invalid content_type: {content_type!r}"
            raise ValueError(msg)
        if not book_data:
            msg = "book_data must not be empty"
            raise ValueError(msg)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO watchlist_releases (
                        watch_id, user_id, provider, provider_book_id,
                        book_data, content_type, publish_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        watch_id,
                        user_id,
                        provider,
                        provider_book_id,
                        _serialize_json(book_data, "book_data"),
                        content_type,
                        publish_date,
                    ),
                )
                conn.commit()
                row = conn.execute(
                    """
                    SELECT * FROM watchlist_releases
                    WHERE watch_id = ? AND provider_book_id = ?
                    """,
                    (watch_id, provider_book_id),
                ).fetchone()
                result = _parse_release_row(row)
                if result is None:
                    msg = "Failed to load release row after upsert"
                    raise RuntimeError(msg)
                return result
            finally:
                conn.close()

    def list_releases(
        self,
        user_id: int,
        *,
        action_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return detected releases for a user, newest first."""
        query = """
            SELECT * FROM watchlist_releases
            WHERE user_id = ?
        """
        params: list[Any] = [user_id]
        if action_status is not None:
            if action_status not in _VALID_ACTION_STATUSES:
                msg = f"Invalid action_status: {action_status!r}"
                raise ValueError(msg)
            query += " AND action_status = ?"
            params.append(action_status)
        query += " ORDER BY detected_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [r for row in rows if (r := _parse_release_row(row)) is not None]
        finally:
            conn.close()

    def update_release_action(
        self,
        release_id: int,
        *,
        action_status: str,
        request_id: int | None = None,
    ) -> dict[str, Any] | None:
        """Update the action status on a release (e.g. after auto-queuing)."""
        if action_status not in _VALID_ACTION_STATUSES:
            msg = f"Invalid action_status: {action_status!r}"
            raise ValueError(msg)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    UPDATE watchlist_releases
                    SET action_status = ?,
                        request_id = COALESCE(?, request_id),
                        actioned_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (action_status, request_id, release_id),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM watchlist_releases WHERE id = ?",
                    (release_id,),
                ).fetchone()
                return _parse_release_row(row)
            finally:
                conn.close()
