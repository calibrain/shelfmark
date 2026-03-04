"""Persistence helpers for flat download terminal history."""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any

from shelfmark.core.request_helpers import normalize_optional_positive_int, normalize_optional_text, now_utc_iso


VALID_FINAL_STATUSES = frozenset({"complete", "error", "cancelled"})
VALID_ORIGINS = frozenset({"direct", "request", "requested"})


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
        raise ValueError("origin must be one of: direct, request, requested")
    return lowered


def _normalize_final_status(final_status: Any) -> str:
    normalized = normalize_optional_text(final_status)
    if normalized is None:
        raise ValueError("final_status must be a non-empty string")
    lowered = normalized.lower()
    if lowered not in VALID_FINAL_STATUSES:
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


def _normalize_offset(value: Any, *, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("offset must be an integer") from exc
    if parsed < 0:
        return 0
    return parsed


class DownloadHistoryService:
    """Service for persisted terminal download history and dismissals."""

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
            "user_id": row.get("user_id"),
            "username": row.get("username"),
            "request_id": row.get("request_id"),
        }

    @classmethod
    def _to_history_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        task_id = str(row.get("task_id") or "").strip()
        return {
            "id": row.get("id"),
            "user_id": row.get("user_id"),
            "item_type": "download",
            "item_key": cls._to_item_key(task_id),
            "dismissed_at": row.get("dismissed_at"),
            "snapshot": {
                "kind": "download",
                "download": cls.to_download_payload(row),
            },
            "origin": row.get("origin"),
            "final_status": row.get("final_status"),
            "terminal_at": row.get("terminal_at"),
            "request_id": row.get("request_id"),
            "source_id": task_id or None,
        }

    def record_terminal(
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
        final_status: str,
        status_message: str | None,
        download_path: str | None,
        terminal_at: str | None = None,
    ) -> None:
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
        normalized_final_status = _normalize_final_status(final_status)
        effective_terminal_at = (
            terminal_at
            if isinstance(terminal_at, str) and terminal_at.strip()
            else now_utc_iso()
        )

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                INSERT INTO download_history (
                    task_id,
                    user_id,
                    username,
                    request_id,
                    source,
                    source_display_name,
                    title,
                    author,
                    format,
                    size,
                    preview,
                    content_type,
                    origin,
                    final_status,
                    status_message,
                    download_path,
                    terminal_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    username = excluded.username,
                    request_id = excluded.request_id,
                    source = excluded.source,
                    source_display_name = excluded.source_display_name,
                    title = excluded.title,
                    author = excluded.author,
                    format = excluded.format,
                    size = excluded.size,
                    preview = excluded.preview,
                    content_type = excluded.content_type,
                    origin = excluded.origin,
                    final_status = excluded.final_status,
                    status_message = excluded.status_message,
                    download_path = excluded.download_path,
                    terminal_at = excluded.terminal_at,
                    dismissed_at = NULL
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
                        normalized_final_status,
                        normalize_optional_text(status_message),
                        normalize_optional_text(download_path),
                        effective_terminal_at,
                    ),
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

    def get_undismissed_terminal(
        self,
        *,
        user_id: int | None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        normalized_limit = _normalize_limit(limit, default=200, minimum=1, maximum=1000)
        query = "SELECT * FROM download_history WHERE dismissed_at IS NULL"
        params: list[Any] = []
        if normalized_user_id is not None:
            query += " AND user_id = ?"
            params.append(normalized_user_id)
        query += " ORDER BY terminal_at DESC, id DESC LIMIT ?"
        params.append(normalized_limit)

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_dismissed_keys(self, *, user_id: int | None, limit: int = 5000) -> list[str]:
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        normalized_limit = _normalize_limit(limit, default=5000, minimum=1, maximum=10000)
        query = "SELECT task_id FROM download_history WHERE dismissed_at IS NOT NULL"
        params: list[Any] = []
        if normalized_user_id is not None:
            query += " AND user_id = ?"
            params.append(normalized_user_id)
        query += " ORDER BY dismissed_at DESC, id DESC LIMIT ?"
        params.append(normalized_limit)

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            keys: list[str] = []
            for row in rows:
                task_id = normalize_optional_text(row["task_id"])
                if task_id is not None:
                    keys.append(task_id)
            return keys
        finally:
            conn.close()

    def dismiss(self, *, task_id: str, user_id: int | None) -> int:
        normalized_task_id = _normalize_task_id(task_id)
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        query = "UPDATE download_history SET dismissed_at = ? WHERE task_id = ?"
        params: list[Any] = [now_utc_iso(), normalized_task_id]
        if normalized_user_id is not None:
            query += " AND user_id = ?"
            params.append(normalized_user_id)

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(query, params)
                conn.commit()
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                return max(rowcount, 0)
            finally:
                conn.close()

    def dismiss_many(self, *, task_ids: list[str], user_id: int | None) -> int:
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        normalized_task_ids = [_normalize_task_id(task_id) for task_id in task_ids]
        if not normalized_task_ids:
            return 0

        placeholders = ",".join("?" for _ in normalized_task_ids)
        query = f"UPDATE download_history SET dismissed_at = ? WHERE task_id IN ({placeholders})"
        params: list[Any] = [now_utc_iso(), *normalized_task_ids]
        if normalized_user_id is not None:
            query += " AND user_id = ?"
            params.append(normalized_user_id)

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(query, params)
                conn.commit()
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                return max(rowcount, 0)
            finally:
                conn.close()

    def get_history(self, *, user_id: int | None, limit: int, offset: int) -> list[dict[str, Any]]:
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        normalized_limit = _normalize_limit(limit, default=50, minimum=1, maximum=5000)
        normalized_offset = _normalize_offset(offset, default=0)

        query = "SELECT * FROM download_history WHERE dismissed_at IS NOT NULL"
        params: list[Any] = []
        if normalized_user_id is not None:
            query += " AND user_id = ?"
            params.append(normalized_user_id)
        query += " ORDER BY dismissed_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([normalized_limit, normalized_offset])

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            payload: list[dict[str, Any]] = []
            for row in rows:
                row_dict = dict(row)
                payload.append(self._to_history_row(row_dict))
            return payload
        finally:
            conn.close()

    def clear_dismissed(self, *, user_id: int | None) -> int:
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        query = "DELETE FROM download_history WHERE dismissed_at IS NOT NULL"
        params: list[Any] = []
        if normalized_user_id is not None:
            query += " AND user_id = ?"
            params.append(normalized_user_id)

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(query, params)
                conn.commit()
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                return max(rowcount, 0)
            finally:
                conn.close()

    def clear_dismissals_for_active(self, *, task_ids: set[str], user_id: int | None) -> int:
        normalized_user_id = normalize_optional_positive_int(user_id, "user_id")
        normalized_task_ids = [_normalize_task_id(task_id) for task_id in task_ids]
        if not normalized_task_ids:
            return 0

        placeholders = ",".join("?" for _ in normalized_task_ids)
        query = f"UPDATE download_history SET dismissed_at = NULL WHERE task_id IN ({placeholders})"
        params: list[Any] = [*normalized_task_ids]
        if normalized_user_id is not None:
            query += " AND user_id = ?"
            params.append(normalized_user_id)

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(query, params)
                conn.commit()
                rowcount = int(cursor.rowcount) if cursor.rowcount is not None else 0
                return max(rowcount, 0)
            finally:
                conn.close()
