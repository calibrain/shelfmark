"""Library service — SQL queries against books / user_library / user_downloads.

Implements the data layer for the library API (#04 contract). Mirrors the
layering of :class:`DownloadHistoryService`: db-path + lock + ``_connect`` +
row-to-dict helpers. Models books as denormalized snapshots per ADR 0001 and
file visibility per ADR 0002.

"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import (
    normalize_optional_text,
    normalize_positive_int,
    now_utc_iso,
)

logger = setup_logger(__name__)


_ACTIVE_DOWNLOAD_STATUS = "active"
_COMPLETE_DOWNLOAD_STATUS = "complete"
# Send-to-Kindle accepted formats — see ticket #05.
KINDLE_FORMAT_PRIORITY: tuple[str, ...] = ("epub",)


def _now_utc_iso() -> str:
    return now_utc_iso()


def _parse_metadata_json(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _row_to_book(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = dict(row)
    payload["metadata_json"] = _parse_metadata_json(payload.get("metadata_json"))
    return payload


class LibraryService:
    """Service for library membership and book/file lookups."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _book_identity(book_id: Any) -> int:
        normalized = normalize_positive_int(book_id)
        if normalized is None:
            msg = "book_id must be a positive integer"
            raise ValueError(msg)
        return normalized

    @staticmethod
    def _history_identity(history_id: Any) -> int:
        normalized = normalize_positive_int(history_id)
        if normalized is None:
            msg = "history_id must be a positive integer"
            raise ValueError(msg)
        return normalized

    def upsert_book_from_metadata(
        self,
        *,
        metadata_provider: str,
        provider_book_id: str,
        title: str,
        author: str | None,
        subtitle: str | None,
        publish_year: int | None,
        isbn_13: str | None,
        cover_url: str | None,
        series_name: str | None,
        series_position: float | None,
        language: str | None,
        metadata_json: dict[str, Any],
    ) -> dict[str, Any]:
        """Insert or return the existing book row for a provider natural key.

        Idempotent on ``UNIQUE(metadata_provider, provider_book_id)``. Per
        ADR 0001, the snapshot is written at Add time and never refreshed.
        """
        normalized_provider = normalize_optional_text(metadata_provider)
        if not normalized_provider:
            msg = "metadata_provider must be a non-empty string"
            raise ValueError(msg)
        normalized_provider_book_id = normalize_optional_text(provider_book_id)
        if not normalized_provider_book_id:
            msg = "provider_book_id must be a non-empty string"
            raise ValueError(msg)
        normalized_title = normalize_optional_text(title)
        if not normalized_title:
            msg = "title must be a non-empty string"
            raise ValueError(msg)
        serialized_metadata = json.dumps(metadata_json, ensure_ascii=False)

        with self._lock:
            conn = self._connect()
            try:
                existing = conn.execute(
                    "SELECT * FROM books WHERE metadata_provider = ? AND provider_book_id = ?",
                    (normalized_provider, normalized_provider_book_id),
                ).fetchone()
                if existing is not None:
                    return _row_to_book(existing) or {}

                cursor = conn.execute(
                    """
                    INSERT INTO books (
                        metadata_provider, provider_book_id, title, author,
                        subtitle, publish_year, isbn_13, cover_url,
                        series_name, series_position, language, metadata_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_provider,
                        normalized_provider_book_id,
                        normalized_title,
                        normalize_optional_text(author),
                        normalize_optional_text(subtitle),
                        publish_year,
                        normalize_optional_text(isbn_13),
                        normalize_optional_text(cover_url),
                        normalize_optional_text(series_name),
                        series_position,
                        normalize_optional_text(language),
                        serialized_metadata,
                        _now_utc_iso(),
                        _now_utc_iso(),
                    ),
                )
                conn.commit()
                last_row_id = cursor.lastrowid
                if last_row_id is None:
                    msg = "Failed to insert book row"
                    raise RuntimeError(msg)
                book_id = int(last_row_id)
                row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
                return _row_to_book(row) or {}
            finally:
                conn.close()

    def add_to_library(self, *, user_id: int, book_id: int) -> bool:
        """Link a user to a book and its already-complete files atomically.

        Completed files are linked only when the membership is first created.
        This preserves an explicit release unlink until the Book is removed and
        deliberately re-added to the Library.
        """
        normalized_user_id = normalize_positive_int(user_id)
        normalized_book_id = self._book_identity(book_id)
        if normalized_user_id is None:
            msg = "user_id must be a positive integer"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO user_library (user_id, book_id, added_at)
                    VALUES (?, ?, ?)
                    """,
                    (normalized_user_id, normalized_book_id, _now_utc_iso()),
                )
                newly_linked = cursor.rowcount > 0
                if newly_linked:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO user_downloads (user_id, history_id, added_at)
                        SELECT ?, id, ?
                        FROM download_history
                        WHERE book_id = ?
                          AND final_status = ?
                          AND download_path IS NOT NULL
                        """,
                        (
                            normalized_user_id,
                            _now_utc_iso(),
                            normalized_book_id,
                            _COMPLETE_DOWNLOAD_STATUS,
                        ),
                    )
                conn.commit()
                return newly_linked
            finally:
                conn.close()

    def remove_from_library(self, *, user_id: int, book_id: int) -> bool:
        """Hard-delete the user's library row for a book. Returns True when deleted."""
        normalized_user_id = normalize_positive_int(user_id)
        normalized_book_id = self._book_identity(book_id)
        if normalized_user_id is None:
            msg = "user_id must be a positive integer"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM user_library WHERE user_id = ? AND book_id = ?",
                    (normalized_user_id, normalized_book_id),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def is_in_library(self, *, user_id: int, book_id: int) -> bool:
        normalized_user_id = normalize_positive_int(user_id)
        normalized_book_id = self._book_identity(book_id)
        if normalized_user_id is None:
            return False
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM user_library WHERE user_id = ? AND book_id = ? LIMIT 1",
                (normalized_user_id, normalized_book_id),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_book(self, book_id: int) -> dict[str, Any] | None:
        normalized_book_id = self._book_identity(book_id)
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM books WHERE id = ?", (normalized_book_id,)).fetchone()
            return _row_to_book(row)
        finally:
            conn.close()

    def list_library_books(
        self,
        *,
        user_id: int | None,
        is_admin: bool,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return library books for a user, or all users when explicitly authorized.

        Per #04 sub-decision 9: no pagination for MVP. Per sub-decision 10:
        ``?q=`` is a case-insensitive LIKE on title/author. Ordered by
        ``user_library.added_at DESC``.
        """
        params: list[Any] = []
        where_clauses: list[str] = []
        if not is_admin:
            normalized_user_id = normalize_positive_int(user_id)
            if normalized_user_id is None:
                return []
            where_clauses.append("ul.user_id = ?")
            params.append(normalized_user_id)

        normalized_query = normalize_optional_text(query)
        if normalized_query:
            where_clauses.append("(b.title LIKE ? OR COALESCE(b.author, '') LIKE ?)")
            like_pattern = f"%{normalized_query}%"
            params.extend([like_pattern, like_pattern])

        where_sql = ""
        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)
        # Query is assembled from static fragments + parameterized clauses; the
        # LIKE patterns flow through bound parameters, so string interpolation
        # here only joins fixed SQL text.
        sql = (
            "SELECT b.*, ul.added_at AS library_added_at "
            "FROM books b "
            "INNER JOIN user_library ul ON ul.book_id = b.id"
            + where_sql
            + " ORDER BY ul.added_at DESC, b.id DESC"
        )
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_book(row) or {} for row in rows]
        finally:
            conn.close()

    def get_files_on_disk(self, book_id: int) -> list[dict[str, Any]]:
        """Return ``download_history`` rows for a book with on-disk artifacts.

        Per #04 sub-decision 6, files are surfaced per-row (release-level
        metadata: format, size, indexer_display_name, protocol, downloaded_at).
        Per sub-decision 3, the union spans all users — no per-user attribution.
        ``downloadable_by_me`` is computed by the caller (route) from the
        union against ``user_downloads`` since it depends on the actor.
        """
        normalized_book_id = self._book_identity(book_id)
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, task_id, source, source_display_name, title, author,
                       format, size, content_type, download_path,
                       username, user_id AS triggering_user_id, queued_at,
                       terminal_at, final_status
                FROM download_history
                WHERE book_id = ?
                  AND final_status = ?
                  AND download_path IS NOT NULL
                ORDER BY terminal_at DESC, id DESC
                """,
                (normalized_book_id, _COMPLETE_DOWNLOAD_STATUS),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_in_flight_files(self, book_id: int) -> list[dict[str, Any]]:
        """Return in-flight ``download_history`` rows for a book (any user)."""
        normalized_book_id = self._book_identity(book_id)
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, task_id, source, source_display_name, title, author,
                       format, size, content_type, username, queued_at,
                       final_status
                FROM download_history
                WHERE book_id = ? AND final_status = ?
                ORDER BY queued_at DESC, id DESC
                """,
                (normalized_book_id, _ACTIVE_DOWNLOAD_STATUS),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def files_exist_globally(self, book_id: int) -> bool:
        """Per #04 sub-decision 12: complete rows with a non-null ``download_path``."""
        normalized_book_id = self._book_identity(book_id)
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT 1 FROM download_history
                WHERE book_id = ? AND final_status = ? AND download_path IS NOT NULL
                LIMIT 1
                """,
                (normalized_book_id, _COMPLETE_DOWNLOAD_STATUS),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def in_flight_globally(self, book_id: int) -> bool:
        """Per #04 sub-decision 12: any active row for the book."""
        normalized_book_id = self._book_identity(book_id)
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT 1 FROM download_history
                WHERE book_id = ? AND final_status = ?
                LIMIT 1
                """,
                (normalized_book_id, _ACTIVE_DOWNLOAD_STATUS),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_release_library_states(
        self, *, task_ids: list[str], user_id: int | None
    ) -> dict[str, dict[str, Any]]:
        """Return on-disk and current-user library state for release task ids."""
        normalized_task_ids = sorted({task_id.strip() for task_id in task_ids if task_id.strip()})
        if not normalized_task_ids:
            return {}
        normalized_user_id = normalize_positive_int(user_id)
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT task_id, MAX(book_id) AS book_id
                FROM download_history
                WHERE task_id IN (SELECT value FROM json_each(?))
                  AND final_status = ?
                  AND download_path IS NOT NULL
                GROUP BY task_id
                """,
                [json.dumps(normalized_task_ids), _COMPLETE_DOWNLOAD_STATUS],
            ).fetchall()
            states = {
                task_id: {"is_on_disk": False, "book_id": None, "in_my_library": False}
                for task_id in normalized_task_ids
            }
            for row in rows:
                task_id = str(row["task_id"])
                book_id = row["book_id"]
                states[task_id]["is_on_disk"] = True
                states[task_id]["book_id"] = int(book_id) if book_id is not None else None

            book_ids = [
                state["book_id"] for state in states.values() if state["book_id"] is not None
            ]
            if normalized_user_id is not None and book_ids:
                memberships = conn.execute(
                    """
                    SELECT book_id FROM user_library
                    WHERE user_id = ? AND book_id IN (SELECT value FROM json_each(?))
                    """,
                    [normalized_user_id, json.dumps(book_ids)],
                ).fetchall()
                member_book_ids = {int(row["book_id"]) for row in memberships}
                for state in states.values():
                    state["in_my_library"] = state["book_id"] in member_book_ids
            return states
        finally:
            conn.close()

    def get_metadata_library_states(
        self, *, book_keys: list[tuple[str, str]], user_id: int | None
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Return library membership for provider-backed metadata search results."""
        normalized_keys = sorted(
            {
                (provider.strip(), provider_book_id.strip())
                for provider, provider_book_id in book_keys
                if provider.strip() and provider_book_id.strip()
            }
        )
        if not normalized_keys:
            return {}
        normalized_user_id = normalize_positive_int(user_id)
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT b.metadata_provider, b.provider_book_id, b.id AS book_id,
                       ul.book_id IS NOT NULL AS in_my_library
                FROM books b
                LEFT JOIN user_library ul ON ul.book_id = b.id AND ul.user_id = ?
                WHERE EXISTS (
                    SELECT 1 FROM json_each(?) AS requested
                    WHERE b.metadata_provider = json_extract(requested.value, '$.provider')
                      AND b.provider_book_id = json_extract(requested.value, '$.provider_book_id')
                )
                """,
                [
                    normalized_user_id,
                    json.dumps(
                        [
                            {"provider": provider, "provider_book_id": provider_book_id}
                            for provider, provider_book_id in normalized_keys
                        ]
                    ),
                ],
            ).fetchall()
            return {
                (str(row["metadata_provider"]), str(row["provider_book_id"])): {
                    "book_id": int(row["book_id"]),
                    "in_my_library": bool(row["in_my_library"]),
                }
                for row in rows
            }
        finally:
            conn.close()

    def get_download_history_row(self, history_id: int) -> dict[str, Any] | None:
        normalized_history_id = self._history_identity(history_id)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM download_history WHERE id = ?",
                (normalized_history_id,),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def link_download_to_user(self, *, user_id: int, book_id: int, history_id: int) -> bool:
        """Idempotently link a ``download_history`` row to a user's library.

        Per #04 sub-decision 3: ``INSERT OR IGNORE`` into ``user_downloads``.
        The book_id argument is for ownership/membership validation by the
        caller; this insert only needs (user_id, history_id).
        """
        normalized_user_id = normalize_positive_int(user_id)
        normalized_history_id = self._history_identity(history_id)
        self._book_identity(book_id)
        if normalized_user_id is None:
            msg = "user_id must be a positive integer"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO user_downloads (user_id, history_id, added_at)
                    VALUES (?, ?, ?)
                    """,
                    (normalized_user_id, normalized_history_id, _now_utc_iso()),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def delete_release(self, *, book_id: int, history_id: int) -> bool:
        """Delete every on-disk File in a release and detach its history.

        ``history_id`` identifies any File in the release. All sibling rows
        sharing its ``task_id`` lose their Book and user-download links, while
        preserving their history metadata as an audit record.
        """
        normalized_history_id = self._history_identity(history_id)
        self._book_identity(book_id)
        with self._lock:
            conn = self._connect()
            try:
                task_id_row = conn.execute(
                    "SELECT task_id FROM download_history WHERE id = ?",
                    (normalized_history_id,),
                ).fetchone()
                if task_id_row is None:
                    return False
                task_id = task_id_row["task_id"]
                rows = conn.execute(
                    """
                    SELECT id, download_path, final_status FROM download_history WHERE task_id = ?
                    """,
                    (task_id,),
                ).fetchall()
                if not rows or any(
                    row["final_status"] != _COMPLETE_DOWNLOAD_STATUS
                    or normalize_optional_text(row["download_path"]) is None
                    for row in rows
                ):
                    return False
                deleted_history_ids: list[int] = []
                for row in rows:
                    path = normalize_optional_text(row["download_path"])
                    try:
                        if path:
                            Path(path).unlink(missing_ok=True)
                    except OSError:
                        # Filesystem deletion cannot be transactional. Detach
                        # artifacts already removed so retrying the remaining
                        # release cannot advertise missing files.
                        if deleted_history_ids:
                            history_ids_json = json.dumps(deleted_history_ids)
                            conn.execute(
                                "DELETE FROM user_downloads WHERE history_id "
                                "IN (SELECT value FROM json_each(?))",
                                (history_ids_json,),
                            )
                            conn.execute(
                                "UPDATE download_history SET book_id = NULL, download_path = NULL "
                                "WHERE id IN (SELECT value FROM json_each(?))",
                                (history_ids_json,),
                            )
                            conn.commit()
                        raise
                    deleted_history_ids.append(int(row["id"]))
                conn.execute(
                    "DELETE FROM user_downloads WHERE history_id IN "
                    "(SELECT id FROM download_history WHERE task_id = ?)",
                    (task_id,),
                )
                conn.execute(
                    "UPDATE download_history SET book_id = NULL, download_path = NULL "
                    "WHERE task_id = ?",
                    (task_id,),
                )
                conn.commit()
                return bool(rows)
            finally:
                conn.close()

    def download_linked_to_user(self, *, user_id: int, history_id: int) -> bool:
        """Whether a ``download_history`` row is linked to a user via ``user_downloads``."""
        normalized_user_id = normalize_positive_int(user_id)
        normalized_history_id = self._history_identity(history_id)
        if normalized_user_id is None:
            return False
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT 1 FROM user_downloads
                WHERE user_id = ? AND history_id = ?
                LIMIT 1
                """,
                (normalized_user_id, normalized_history_id),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def resolve_kindle_format(
        self, *, book_id: int, requested_format: str | None = None, user_id: int | None = None
    ) -> dict[str, Any] | None:
        """Resolve the file to send to Kindle per #05's priority algorithm.

        Returns ``{history_id, format, download_path, size}`` when a file
        is chosen, or ``None`` when no compatible file is on disk. The caller
        ``user_id`` limits candidates to that member's linked files; ``None``
        retains the instance-wide result used for administrators.
        """
        if requested_format:
            normalized_requested = normalize_optional_text(requested_format)
            if normalized_requested:
                row = self._fetch_book_history_row_for_format(
                    book_id, normalized_requested, user_id=user_id
                )
                if row is None:
                    return None
                return self._format_history_row_for_kindle(row)
        for candidate_format in KINDLE_FORMAT_PRIORITY:
            row = self._fetch_book_history_row_for_format(
                book_id, candidate_format, user_id=user_id
            )
            if row is None:
                continue
            return self._format_history_row_for_kindle(row)
        return None

    def _fetch_book_history_row_for_format(
        self, book_id: int, fmt: str, *, user_id: int | None
    ) -> sqlite3.Row | None:
        normalized_book_id = self._book_identity(book_id)
        normalized_user_id = normalize_positive_int(user_id)
        conn = self._connect()
        try:
            return conn.execute(
                """
                SELECT id, format, download_path, size
                FROM download_history
                WHERE book_id = ?
                  AND final_status = ?
                  AND download_path IS NOT NULL
                  AND LOWER(format) = LOWER(?)
                  AND (
                    ? IS NULL
                    OR EXISTS (
                        SELECT 1 FROM user_downloads
                        WHERE user_id = ? AND history_id = download_history.id
                    )
                  )
                ORDER BY terminal_at DESC
                LIMIT 1
                """,
                (
                    normalized_book_id,
                    _COMPLETE_DOWNLOAD_STATUS,
                    fmt,
                    normalized_user_id,
                    normalized_user_id,
                ),
            ).fetchone()
        finally:
            conn.close()

    @staticmethod
    def _format_history_row_for_kindle(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "history_id": int(row["id"]),
            "format": row["format"],
            "download_path": row["download_path"],
            "size": row["size"],
        }

    @staticmethod
    def _iso_to_epoch(value: object) -> float | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
