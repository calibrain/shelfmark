"""Persistence helpers for canonical download activity rows."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, SupportsIndex, SupportsInt, TypeGuard

from shelfmark.core.logger import setup_logger
from shelfmark.core.models import TERMINAL_QUEUE_STATUSES
from shelfmark.core.request_helpers import (
    normalize_optional_positive_int,
    normalize_optional_text,
    now_utc_iso,
)

logger = setup_logger(__name__)


VALID_TERMINAL_STATUSES = frozenset(s.value for s in TERMINAL_QUEUE_STATUSES)
ACTIVE_DOWNLOAD_STATUS = "active"
VALID_ORIGINS = frozenset({"direct", "requested"})


@dataclass(frozen=True)
class FinalizeDownloadFilesResult:
    fulfilled_requests: list[dict[str, Any]]
    files_finalized: bool


def _is_convertible_to_int(
    value: object,
) -> TypeGuard[str | bytes | bytearray | SupportsInt | SupportsIndex]:
    """Return True when *value* can be safely passed to ``int``."""
    return (
        isinstance(value, (str, bytes, bytearray))
        or hasattr(value, "__int__")
        or hasattr(value, "__index__")
    )


def _coerce_int_value(value: object) -> int:
    """Normalize int-like values and raise TypeError for unsupported inputs."""
    if isinstance(value, bool) or not _is_convertible_to_int(value):
        msg = "limit must be an integer"
        raise TypeError(msg)
    return int(value)


def _normalize_task_id(task_id: object) -> str:
    normalized = normalize_optional_text(task_id)
    if normalized is None:
        msg = "task_id must be a non-empty string"
        raise ValueError(msg)
    return normalized


def _normalize_origin(origin: object) -> str:
    normalized = normalize_optional_text(origin)
    if normalized is None:
        return "direct"
    lowered = normalized.lower()
    if lowered not in VALID_ORIGINS:
        msg = "origin must be one of: direct, requested"
        raise ValueError(msg)
    return lowered


def _normalize_final_status(final_status: object) -> str:
    normalized = normalize_optional_text(final_status)
    if normalized is None:
        msg = "final_status must be a non-empty string"
        raise ValueError(msg)
    lowered = normalized.lower()
    if lowered not in VALID_TERMINAL_STATUSES:
        msg = "final_status must be one of: complete, error, cancelled"
        raise ValueError(msg)
    return lowered


def _normalize_limit(value: object, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = _coerce_int_value(value)
    except (TypeError, ValueError) as exc:
        msg = "limit must be an integer"
        raise ValueError(msg) from exc
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


class DownloadHistoryService:
    """Service for persisted canonical download activity rows."""

    def __init__(self, db_path: str) -> None:
        """Initialize the service with the SQLite history database path."""
        self._db_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @classmethod
    def _normalize_row_dict(cls, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        normalized = dict(row)
        normalized["retry_payload"] = cls._deserialize_retry_payload(
            normalized.get("retry_payload")
        )
        return normalized

    @classmethod
    def _row_to_dict(cls, row: sqlite3.Row | None) -> dict[str, Any] | None:
        return cls._normalize_row_dict(dict(row) if row is not None else None)

    @staticmethod
    def _to_item_key(task_id: str) -> str:
        return f"download:{task_id}"

    @staticmethod
    def _resolve_existing_download_path(value: object) -> str | None:
        normalized = normalize_optional_text(value)
        if normalized is None:
            return None
        return normalized if Path(normalized).exists() else None

    @staticmethod
    def _serialize_retry_payload(payload: object) -> str | None:
        if payload is None:
            return None
        try:
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            msg = "retry_payload must be JSON-serializable"
            raise ValueError(msg) from exc

    @staticmethod
    def _deserialize_retry_payload(value: object) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return dict(value)
        normalized = normalize_optional_text(value)
        if normalized is None:
            return None
        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _has_staged_retry_source(retry_payload: dict[str, Any]) -> bool:
        staged_path = retry_payload.get("staged_path")
        normalized_staged_path = normalize_optional_text(staged_path)
        if normalized_staged_path is None:
            return False
        return Path(normalized_staged_path).exists()

    @staticmethod
    def _can_retry_without_staged_source(retry_payload: dict[str, Any]) -> bool:
        return bool(retry_payload.get("can_retry_without_staged_source", True))

    @staticmethod
    def is_retry_available(row: dict[str, Any]) -> bool:
        """Return whether a persisted download row can be retried."""
        final_status = (
            str(row.get("retry_final_status") or row.get("final_status") or "").strip().lower()
        )
        retry_payload = DownloadHistoryService._deserialize_retry_payload(row.get("retry_payload"))
        if retry_payload is None:
            return False

        has_staged_retry_source = DownloadHistoryService._has_staged_retry_source(retry_payload)
        can_retry_without_staged_source = DownloadHistoryService._can_retry_without_staged_source(
            retry_payload
        )
        request_id = normalize_optional_positive_int(row.get("request_id"), "request_id")
        if request_id is None:
            if final_status in {ACTIVE_DOWNLOAD_STATUS, "cancelled"}:
                return can_retry_without_staged_source
            if final_status == "error":
                return has_staged_retry_source or can_retry_without_staged_source
            return False

        if final_status in {ACTIVE_DOWNLOAD_STATUS, "cancelled"}:
            return can_retry_without_staged_source

        if final_status != "error":
            return False

        return has_staged_retry_source

    @staticmethod
    def to_download_payload(row: dict[str, Any]) -> dict[str, Any]:
        """Build the sidebar/history download payload for a persisted row."""
        return {
            "id": row.get("task_id"),
            "title": row.get("title"),
            "author": row.get("author"),
            "format": row.get("format"),
            "size": row.get("size"),
            "preview": row.get("preview"),
            "content_type": row.get("content_type"),
            "source": row.get("source"),
            "source_display_name": row.get("source_display_name"),
            "status_message": row.get("status_message"),
            "download_path": DownloadHistoryService._resolve_existing_download_path(
                row.get("download_path")
            ),
            "added_time": DownloadHistoryService._iso_to_epoch(row.get("queued_at")),
            "user_id": row.get("user_id"),
            "username": row.get("username"),
            "request_id": row.get("request_id"),
            "retry_available": DownloadHistoryService.is_retry_available(row),
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

    @classmethod
    def to_history_row(cls, row: dict[str, Any], *, dismissed_at: str) -> dict[str, Any]:
        """Build the activity-history payload for a persisted download row."""
        task_id = str(row.get("task_id") or "").strip()
        item_key = cls._to_item_key(task_id)
        download_payload = cls.to_download_payload(row)
        # Clear stale progress messages for non-error terminal states.
        if row.get("final_status") in ("complete", "cancelled"):
            download_payload["status_message"] = None
        return {
            "id": item_key,
            "user_id": row.get("user_id"),
            "item_type": "download",
            "item_key": item_key,
            "dismissed_at": dismissed_at,
            "snapshot": {
                "kind": "download",
                "download": download_payload,
            },
            "origin": row.get("origin"),
            "final_status": row.get("final_status"),
            "terminal_at": row.get("terminal_at"),
            "request_id": row.get("request_id"),
            "source_id": task_id or None,
        }

    def record_download(
        self,
        *,
        task_id: str,
        user_id: int | None,
        username: str | None,
        request_id: int | None,
        source: str,
        source_display_name: str | None,
        title: str,
        author: str | None,
        file_format: str | None,
        size: str | None,
        preview: str | None,
        content_type: str | None,
        origin: str,
        book_id: int | None = None,
        retry_payload: dict[str, Any] | None = None,
    ) -> None:
        """Record a download at queue time with ``final_status='active'``.

        Per #13's multi-file release contract: inserts one sentinel row per
        ``task_id`` with the per-file columns (``format``, ``size``,
        ``download_path``) NULL — they are populated at finalize by
        :meth:`finalize_download_files` when the transfer completes. On
        retry, any partial multi-file rows from a crashed previous attempt
        are deleted before the fresh sentinel is inserted (atomic under
        the existing lock — the legacy ``ON CONFLICT`` is gone because
        ``task_id`` is no longer UNIQUE under schema (b)).
        """
        normalized_task_id = _normalize_task_id(task_id)
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        normalized_request_id = normalize_optional_positive_int(request_id, "request_id")
        normalized_book_id = normalize_optional_positive_int(book_id, "book_id")
        normalized_source = normalize_optional_text(source)
        if normalized_source is None:
            msg = "source must be a non-empty string"
            raise ValueError(msg)
        normalized_title = normalize_optional_text(title)
        if normalized_title is None:
            msg = "title must be a non-empty string"
            raise ValueError(msg)
        normalized_origin = _normalize_origin(origin)
        normalized_retry_payload = self._serialize_retry_payload(retry_payload)
        recorded_at = now_utc_iso()

        with self._lock:
            conn = self._connect()
            try:
                # Retry path: clear any partial multi-file rows from a crashed
                # previous attempt before inserting a fresh sentinel. Under
                # schema (b) a single task_id may have multiple rows, so the
                # legacy ON CONFLICT(task_id) path is invalid; the DELETE is
                # safe under the lock + commit.
                conn.execute(
                    "DELETE FROM download_history WHERE task_id = ?",
                    (normalized_task_id,),
                )
                conn.execute(
                    """
                INSERT INTO download_history (
                    task_id, user_id, username, request_id,
                    source, source_display_name,
                    title, author, format, size, preview, content_type,
                    origin, final_status, book_id,
                    status_message, download_path, retry_payload,
                    queued_at, terminal_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, 'active', ?, NULL, NULL, ?, ?, ?)
                """,
                    (
                        normalized_task_id,
                        normalized_user_id,
                        normalize_optional_text(username),
                        normalized_request_id,
                        normalized_source,
                        normalize_optional_text(source_display_name),
                        normalized_title,
                        normalize_optional_text(author),
                        normalize_optional_text(preview),
                        normalize_optional_text(content_type),
                        normalized_origin,
                        normalized_book_id,
                        normalized_retry_payload,
                        recorded_at,
                        recorded_at,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def finalize_download(
        self,
        *,
        task_id: str,
        final_status: str,
        status_message: str | None = None,
        download_path: str | None = None,
        retry_payload: dict[str, Any] | None = None,
    ) -> None:
        """Update an existing download row to its terminal state.

        Thin delegate to :meth:`finalize_download_files` with a one-element
        file list — preserves the single-path call signature for direct-mode
        downloads and any non-library caller. Library callers that surface
        multiple files per release call :meth:`finalize_download_files`
        directly.
        """
        file_rows: list[dict[str, Any]] = []
        normalized_path = normalize_optional_text(download_path)
        if normalized_path is not None:
            file_rows.append({"download_path": normalized_path})
        self.finalize_download_files(
            task_id=task_id,
            final_status=final_status,
            status_message=status_message,
            file_rows=file_rows,
            retry_payload=retry_payload,
        )

    def finalize_download_files(
        self,
        *,
        task_id: str,
        final_status: str,
        status_message: str | None = None,
        file_rows: list[dict[str, Any]] | None = None,
        retry_payload: dict[str, Any] | None = None,
    ) -> FinalizeDownloadFilesResult:
        """Finalize a release: delete the queue-time sentinel and insert one
        ``download_history`` row per transferred file plus one
        ``user_downloads`` link per row for the triggering user.

        Per #13's write path (2-a): the queue-time sentinel (per-file cols
        NULL) is deleted; N terminal file rows sharing ``task_id`` are
        inserted with their own ``format``/``size``/``download_path``. Per
        link-timing decision (4-mid-1-ii), ``user_downloads`` links are
        inserted here — not at queue time — so an in-flight release has no
        link row (mid-flight unlink returns 404 by finding nothing). The
        triggering user is read off the sentinel's ``user_id``.

        When ``file_rows`` is empty (a failed/cancelled finalize with no
        transferred file) the sentinel is still removed and no file rows
        are inserted; the caller's ``final_status`` (e.g. ``error`` /
        ``cancelled``) is recorded on no rows — matching today's behaviour
        where a terminal failure leaves no complete row.
        """
        normalized_task_id = _normalize_task_id(task_id)
        normalized_final_status = _normalize_final_status(final_status)
        normalized_status_message = normalize_optional_text(status_message)
        normalized_retry_payload = self._serialize_retry_payload(retry_payload)
        effective_terminal_at = now_utc_iso()

        normalized_file_rows: list[dict[str, str | None]] = []
        for raw_row in file_rows or []:
            path_value = normalize_optional_text(raw_row.get("download_path"))
            if path_value is None:
                continue
            normalized_file_rows.append(
                {
                    "download_path": path_value,
                    "format": normalize_optional_text(raw_row.get("format")),
                    "size": normalize_optional_text(raw_row.get("size")),
                }
            )

        with self._lock:
            conn = self._connect()
            try:
                # Capture the queue-time sentinel row. Under schema (b) the
                # sentinel is the single 'active' row for this task_id; it
                # carries the immutable release-level columns (source, title,
                # author, user_id, request_id, ...) that every file row
                # inherits. Per-file columns (format/size/download_path) are
                # NULL on the sentinel and populated per-row at finalize.
                sentinel = conn.execute(
                    "SELECT * FROM download_history WHERE task_id = ? AND final_status = 'active'"
                    " ORDER BY id DESC LIMIT 1",
                    (normalized_task_id,),
                ).fetchone()
                if sentinel is None:
                    logger.warning(
                        "finalize_download_files: no active sentinel found for task_id=%s "
                        "(may have been missed at queue time)",
                        normalized_task_id,
                    )
                    return FinalizeDownloadFilesResult(fulfilled_requests=[], files_finalized=False)

                triggering_user_id = (
                    int(sentinel["user_id"]) if sentinel["user_id"] is not None else None
                )
                # Per #13 link-timing (4-mid-1-ii): user_downloads links are
                # inserted at finalize, one per file row, for the triggering
                # user only. The sentinel had no link (mid-flight unlink
                # returns 404 by finding nothing).

                if not normalized_file_rows:
                    # No transferred files (error / cancelled / completed but
                    # yielded no path): preserve the sentinel as the terminal
                    # row in place rather than deleting it, so activity/retry
                    # lookups by task_id still resolve. This matches the
                    # pre-#13 behaviour where finalize_download UPDATE'd the
                    # single row to its terminal status.
                    conn.execute(
                        """
                        UPDATE download_history
                        SET final_status = ?,
                            status_message = ?,
                            retry_payload = COALESCE(?, retry_payload),
                            terminal_at = ?
                        WHERE task_id = ? AND final_status = 'active'
                        """,
                        (
                            normalized_final_status,
                            normalized_status_message,
                            normalized_retry_payload,
                            effective_terminal_at,
                            normalized_task_id,
                        ),
                    )
                    conn.commit()
                    return FinalizeDownloadFilesResult(fulfilled_requests=[], files_finalized=False)

                # Drop the sentinel (and, defensively, any partial multi-file
                # rows from a crashed previous finalize attempt for the same
                # task_id) before inserting the terminal file rows.
                conn.execute(
                    "DELETE FROM download_history WHERE task_id = ?",
                    (normalized_task_id,),
                )

                inserted_history_ids: list[int] = []
                for row in normalized_file_rows:
                    cursor = conn.execute(
                        """
                        INSERT INTO download_history (
                            task_id, user_id, username, request_id,
                            source, source_display_name,
                            title, author, format, size, preview, content_type,
                            origin, final_status, book_id,
                            status_message, download_path, retry_payload,
                            queued_at, terminal_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized_task_id,
                            triggering_user_id,
                            sentinel["username"],
                            sentinel["request_id"],
                            sentinel["source"],
                            sentinel["source_display_name"],
                            sentinel["title"],
                            sentinel["author"],
                            row["format"],
                            row["size"],
                            sentinel["preview"],
                            sentinel["content_type"],
                            sentinel["origin"],
                            normalized_final_status,
                            sentinel["book_id"],
                            normalized_status_message,
                            row["download_path"],
                            normalized_retry_payload,
                            sentinel["queued_at"],
                            effective_terminal_at,
                        ),
                    )
                    last_row_id = cursor.lastrowid
                    if last_row_id is not None:
                        inserted_history_ids.append(int(last_row_id))

                # One user_downloads link per inserted file row for the
                # triggering user. INSERT OR IGNORE keeps it idempotent under
                # any unexpected re-finalize.
                if triggering_user_id is not None:
                    conn.executemany(
                        """
                        INSERT OR IGNORE INTO user_downloads (user_id, history_id, added_at)
                        VALUES (?, ?, ?)
                        """,
                        [
                            (triggering_user_id, hid, effective_terminal_at)
                            for hid in inserted_history_ids
                        ],
                    )

                book_id = sentinel["book_id"]
                pending_requests: list[Any] = []
                if (
                    normalized_final_status == "complete"
                    and book_id is not None
                    and inserted_history_ids
                ):
                    # A shared release becomes available atomically: every
                    # requester still pending for this exact Book gains every
                    # File and transitions to fulfilled. Cancelled requests
                    # are deliberately excluded.
                    pending_requests = list(
                        conn.execute(
                            """
                        SELECT id, user_id FROM download_requests
                        WHERE book_id = ? AND status = 'pending'
                        """,
                            (book_id,),
                        ).fetchall()
                    )
                    if pending_requests:
                        conn.executemany(
                            """
                            INSERT OR IGNORE INTO user_downloads (user_id, history_id, added_at)
                            VALUES (?, ?, ?)
                            """,
                            [
                                (int(request["user_id"]), history_id, effective_terminal_at)
                                for request in pending_requests
                                for history_id in inserted_history_ids
                            ],
                        )
                        conn.execute(
                            """
                            UPDATE download_requests
                            SET status = 'fulfilled', reviewed_at = ?
                            WHERE book_id = ? AND status = 'pending'
                            """,
                            (effective_terminal_at, book_id),
                        )

                if not inserted_history_ids:
                    logger.info(
                        "finalize_download_files: task_id=%s finalized as %s with no file rows "
                        "(no transferred paths)",
                        normalized_task_id,
                        normalized_final_status,
                    )
                conn.commit()
                fulfilled_requests = [dict(request) for request in pending_requests]
                for fulfilled_request in fulfilled_requests:
                    fulfilled_request["status"] = "fulfilled"
                    fulfilled_request["reviewed_at"] = effective_terminal_at
                return FinalizeDownloadFilesResult(
                    fulfilled_requests=(
                        fulfilled_requests
                        if (
                            normalized_final_status == "complete"
                            and book_id is not None
                            and inserted_history_ids
                        )
                        else []
                    ),
                    files_finalized=bool(inserted_history_ids),
                )
            finally:
                conn.close()

    def get_by_task_id(self, task_id: str) -> dict[str, Any] | None:
        """Return a persisted download row for the given task id."""
        normalized_task_id = _normalize_task_id(task_id)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM download_history WHERE task_id = ?",
                (normalized_task_id,),
            ).fetchone()
            return self._row_to_dict(row)
        finally:
            conn.close()

    def list_recent(
        self,
        *,
        user_id: int | None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return recent persisted download rows, optionally scoped to one user."""
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        normalized_limit = _normalize_limit(limit, default=200, minimum=1, maximum=1000)
        query = "SELECT * FROM download_history"
        params: list[Any] = []
        if normalized_user_id is not None:
            query += " WHERE user_id = ?"
            params.append(normalized_user_id)
        query += " ORDER BY terminal_at DESC, id DESC LIMIT ?"
        params.append(normalized_limit)

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                normalized = self._normalize_row_dict(dict(row))
                if normalized is not None:
                    result.append(normalized)
            return result
        finally:
            conn.close()
