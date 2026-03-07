"""Persistence helpers for canonical download activity rows."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.models import TERMINAL_QUEUE_STATUSES
from shelfmark.core.request_helpers import normalize_optional_positive_int, normalize_optional_text, now_utc_iso

logger = setup_logger(__name__)


VALID_TERMINAL_STATUSES = frozenset(s.value for s in TERMINAL_QUEUE_STATUSES)
ACTIVE_DOWNLOAD_STATUS = "active"
VALID_ORIGINS = frozenset({"direct", "requested"})


def _normalize_task_id(task_id: Any) -> str:
    normalized = normalize_optional_text(task_id)
    if normalized is None:
        raise ValueError("task_id must be a non-empty string")
    return normalized


def _normalize_origin(origin: Any) -> str:
    normalized = normalize_optional_text(origin)
    if normalized is None:
        return "direct"
    lowered = normalized.lower()
    if lowered not in VALID_ORIGINS:
        raise ValueError("origin must be one of: direct, requested")
    return lowered


def _normalize_final_status(final_status: Any) -> str:
    normalized = normalize_optional_text(final_status)
    if normalized is None:
        raise ValueError("final_status must be a non-empty string")
    lowered = normalized.lower()
    if lowered not in VALID_TERMINAL_STATUSES:
        raise ValueError("final_status must be one of: complete, error, cancelled")
    return lowered


def _normalize_limit(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


class DownloadHistoryService:
    """Service for persisted canonical download activity rows."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _to_item_key(task_id: str) -> str:
        return f"download:{task_id}"

    @staticmethod
    def _resolve_existing_download_path(value: Any) -> str | None:
        normalized = normalize_optional_text(value)
        if normalized is None:
            return None
        return normalized if os.path.exists(normalized) else None

    @staticmethod
    def to_download_payload(row: dict[str, Any]) -> dict[str, Any]:
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
            "download_path": DownloadHistoryService._resolve_existing_download_path(row.get("download_path")),
            "added_time": DownloadHistoryService._iso_to_epoch(row.get("queued_at")),
            "user_id": row.get("user_id"),
            "username": row.get("username"),
            "request_id": row.get("request_id"),
        }

    @staticmethod
    def _iso_to_epoch(value: Any) -> float | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    @classmethod
    def to_history_row(cls, row: dict[str, Any], *, dismissed_at: str) -> dict[str, Any]:
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
        format: str | None,
        size: str | None,
        preview: str | None,
        content_type: str | None,
        origin: str,
    ) -> None:
        """Record a download at queue time with final_status='active'.

        On first queue: inserts a new row.
        On retry (row already exists): resets the row back to 'active'
        so the normal finalize path works when the retry completes.
        """
        normalized_task_id = _normalize_task_id(task_id)
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        normalized_request_id = normalize_optional_positive_int(request_id, "request_id")
        normalized_source = normalize_optional_text(source)
        if normalized_source is None:
            raise ValueError("source must be a non-empty string")
        normalized_title = normalize_optional_text(title)
        if normalized_title is None:
            raise ValueError("title must be a non-empty string")
        normalized_origin = _normalize_origin(origin)
        recorded_at = now_utc_iso()

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                INSERT INTO download_history (
                    task_id, user_id, username, request_id,
                    source, source_display_name,
                    title, author, format, size, preview, content_type,
                    origin, final_status,
                    status_message, download_path,
                    queued_at, terminal_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    final_status = 'active',
                    status_message = NULL,
                    download_path = NULL,
                    terminal_at = ?
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
                        normalize_optional_text(format),
                        normalize_optional_text(size),
                        normalize_optional_text(preview),
                        normalize_optional_text(content_type),
                        normalized_origin,
                        recorded_at,
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
    ) -> None:
        """Update an existing download row to its terminal state."""
        normalized_task_id = _normalize_task_id(task_id)
        normalized_final_status = _normalize_final_status(final_status)
        normalized_status_message = normalize_optional_text(status_message)
        normalized_download_path = normalize_optional_text(download_path)
        effective_terminal_at = now_utc_iso()

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    UPDATE download_history
                    SET final_status = ?,
                        status_message = ?,
                        download_path = ?,
                        terminal_at = ?
                    WHERE task_id = ? AND final_status = 'active'
                    """,
                    (
                        normalized_final_status,
                        normalized_status_message,
                        normalized_download_path,
                        effective_terminal_at,
                        normalized_task_id,
                    ),
                )
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                if rowcount < 1:
                    logger.warning(
                        "finalize_download: no active row found for task_id=%s (may have been missed at queue time)",
                        normalized_task_id,
                    )
                conn.commit()
            finally:
                conn.close()

    def get_by_task_id(self, task_id: str) -> dict[str, Any] | None:
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
            return [dict(row) for row in rows]
        finally:
            conn.close()
