"""Persistence helpers for Activity dismissals and terminal snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Iterable


VALID_ITEM_TYPES = frozenset({"download", "request"})
VALID_ORIGINS = frozenset({"direct", "request", "requested"})
VALID_FINAL_STATUSES = frozenset({"complete", "error", "cancelled", "rejected"})


def _now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_item_type(item_type: Any) -> str:
    if not isinstance(item_type, str):
        raise ValueError("item_type must be a string")
    normalized = item_type.strip().lower()
    if normalized not in VALID_ITEM_TYPES:
        raise ValueError("item_type must be one of: download, request")
    return normalized


def _normalize_item_key(item_key: Any) -> str:
    if not isinstance(item_key, str):
        raise ValueError("item_key must be a string")
    normalized = item_key.strip()
    if not normalized:
        raise ValueError("item_key must not be empty")
    return normalized


def _normalize_origin(origin: Any) -> str:
    if not isinstance(origin, str):
        raise ValueError("origin must be a string")
    normalized = origin.strip().lower()
    if normalized not in VALID_ORIGINS:
        raise ValueError("origin must be one of: direct, request, requested")
    return normalized


def _normalize_final_status(final_status: Any) -> str:
    if not isinstance(final_status, str):
        raise ValueError("final_status must be a string")
    normalized = final_status.strip().lower()
    if normalized not in VALID_FINAL_STATUSES:
        raise ValueError("final_status must be one of: complete, error, cancelled, rejected")
    return normalized


def build_item_key(item_type: str, raw_id: Any) -> str:
    """Build a stable item key used by dismiss/history APIs."""
    normalized_type = _normalize_item_type(item_type)
    if normalized_type == "request":
        try:
            request_id = int(raw_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("request item IDs must be integers") from exc
        if request_id < 1:
            raise ValueError("request item IDs must be positive integers")
        return f"request:{request_id}"

    if not isinstance(raw_id, str):
        raise ValueError("download item IDs must be strings")
    task_id = raw_id.strip()
    if not task_id:
        raise ValueError("download item IDs must not be empty")
    return f"download:{task_id}"


def build_request_item_key(request_id: int) -> str:
    """Build a request item key."""
    return build_item_key("request", request_id)


def build_download_item_key(task_id: str) -> str:
    """Build a download item key."""
    return build_item_key("download", task_id)


def _parse_request_id_from_item_key(item_key: Any) -> int | None:
    if not isinstance(item_key, str) or not item_key.startswith("request:"):
        return None
    raw_value = item_key.split(":", 1)[1].strip()
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _request_final_status(request_status: Any, delivery_state: Any) -> str | None:
    status = str(request_status or "").strip().lower()
    if status == "pending":
        return None
    if status == "rejected":
        return "rejected"
    if status == "cancelled":
        return "cancelled"
    if status != "fulfilled":
        return None

    delivery = str(delivery_state or "").strip().lower()
    if delivery in {"error", "cancelled"}:
        return delivery
    return "complete"


class ActivityService:
    """Service for per-user activity dismissals and terminal history snapshots."""

    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _coerce_positive_int(value: Any, field: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be an integer") from exc
        if parsed < 1:
            raise ValueError(f"{field} must be a positive integer")
        return parsed

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _parse_json_column(value: Any) -> Any:
        if not isinstance(value, str):
            return None
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None

    def _build_legacy_request_snapshot(
        self,
        conn: sqlite3.Connection,
        request_id: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        request_row = conn.execute(
            """
            SELECT
                id,
                user_id,
                status,
                delivery_state,
                request_level,
                book_data,
                release_data,
                note,
                admin_note,
                created_at,
                reviewed_at
            FROM download_requests
            WHERE id = ?
            """,
            (request_id,),
        ).fetchone()
        if request_row is None:
            return None, None

        row_dict = dict(request_row)
        book_data = self._parse_json_column(row_dict.get("book_data"))
        release_data = self._parse_json_column(row_dict.get("release_data"))
        if not isinstance(book_data, dict):
            book_data = {}
        if not isinstance(release_data, dict):
            release_data = {}

        snapshot = {
            "kind": "request",
            "request": {
                "id": int(row_dict["id"]),
                "user_id": row_dict.get("user_id"),
                "status": row_dict.get("status"),
                "delivery_state": row_dict.get("delivery_state"),
                "request_level": row_dict.get("request_level"),
                "book_data": book_data,
                "release_data": release_data,
                "note": row_dict.get("note"),
                "admin_note": row_dict.get("admin_note"),
                "created_at": row_dict.get("created_at"),
                "updated_at": row_dict.get("reviewed_at") or row_dict.get("created_at"),
            },
        }
        final_status = _request_final_status(row_dict.get("status"), row_dict.get("delivery_state"))
        return snapshot, final_status

    def record_terminal_snapshot(
        self,
        *,
        user_id: int | None,
        item_type: str,
        item_key: str,
        origin: str,
        final_status: str,
        snapshot: dict[str, Any],
        request_id: int | None = None,
        source_id: str | None = None,
        terminal_at: str | None = None,
    ) -> dict[str, Any]:
        """Record a durable terminal-state snapshot for an activity item."""
        normalized_item_type = _normalize_item_type(item_type)
        normalized_item_key = _normalize_item_key(item_key)
        normalized_origin = _normalize_origin(origin)
        normalized_final_status = _normalize_final_status(final_status)
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot must be an object")

        if user_id is not None:
            user_id = self._coerce_positive_int(user_id, "user_id")
        if request_id is not None:
            request_id = self._coerce_positive_int(request_id, "request_id")
        if source_id is not None and not isinstance(source_id, str):
            raise ValueError("source_id must be a string when provided")
        if source_id is not None:
            source_id = source_id.strip() or None

        effective_terminal_at = terminal_at if isinstance(terminal_at, str) and terminal_at.strip() else _now_timestamp()
        serialized_snapshot = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)

        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO activity_log (
                    user_id,
                    item_type,
                    item_key,
                    request_id,
                    source_id,
                    origin,
                    final_status,
                    snapshot_json,
                    terminal_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    normalized_item_type,
                    normalized_item_key,
                    request_id,
                    source_id,
                    normalized_origin,
                    normalized_final_status,
                    serialized_snapshot,
                    effective_terminal_at,
                ),
            )
            snapshot_id = int(cursor.lastrowid)
            conn.commit()
            row = conn.execute(
                "SELECT * FROM activity_log WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            payload = self._row_to_dict(row)
            if payload is None:
                raise ValueError("Failed to read back recorded activity snapshot")
            return payload
        finally:
            conn.close()

    def get_latest_activity_log_id(self, *, item_type: str, item_key: str) -> int | None:
        """Get the newest snapshot ID for an item key."""
        normalized_item_type = _normalize_item_type(item_type)
        normalized_item_key = _normalize_item_key(item_key)
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT id
                FROM activity_log
                WHERE item_type = ? AND item_key = ?
                ORDER BY terminal_at DESC, id DESC
                LIMIT 1
                """,
                (normalized_item_type, normalized_item_key),
            ).fetchone()
            if row is None:
                return None
            return int(row["id"])
        finally:
            conn.close()

    def dismiss_item(
        self,
        *,
        user_id: int,
        item_type: str,
        item_key: str,
        activity_log_id: int | None = None,
    ) -> dict[str, Any]:
        """Dismiss an item for a specific user (upsert)."""
        normalized_user_id = self._coerce_positive_int(user_id, "user_id")
        normalized_item_type = _normalize_item_type(item_type)
        normalized_item_key = _normalize_item_key(item_key)
        normalized_log_id = (
            self._coerce_positive_int(activity_log_id, "activity_log_id")
            if activity_log_id is not None
            else self.get_latest_activity_log_id(
                item_type=normalized_item_type,
                item_key=normalized_item_key,
            )
        )

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO activity_dismissals (
                    user_id,
                    item_type,
                    item_key,
                    activity_log_id,
                    dismissed_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, item_type, item_key)
                DO UPDATE SET
                    activity_log_id = excluded.activity_log_id,
                    dismissed_at = excluded.dismissed_at
                """,
                (
                    normalized_user_id,
                    normalized_item_type,
                    normalized_item_key,
                    normalized_log_id,
                    _now_timestamp(),
                ),
            )
            conn.commit()
            row = conn.execute(
                """
                SELECT *
                FROM activity_dismissals
                WHERE user_id = ? AND item_type = ? AND item_key = ?
                """,
                (normalized_user_id, normalized_item_type, normalized_item_key),
            ).fetchone()
            payload = self._row_to_dict(row)
            if payload is None:
                raise ValueError("Failed to read back dismissal row")
            return payload
        finally:
            conn.close()

    def dismiss_many(self, *, user_id: int, items: Iterable[dict[str, Any]]) -> int:
        """Dismiss many items for one user."""
        normalized_user_id = self._coerce_positive_int(user_id, "user_id")
        normalized_items: list[tuple[str, str, int | None]] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("items must contain objects")
            normalized_item_type = _normalize_item_type(item.get("item_type"))
            normalized_item_key = _normalize_item_key(item.get("item_key"))
            raw_log_id = item.get("activity_log_id")
            normalized_log_id = (
                self._coerce_positive_int(raw_log_id, "activity_log_id")
                if raw_log_id is not None
                else self.get_latest_activity_log_id(
                    item_type=normalized_item_type,
                    item_key=normalized_item_key,
                )
            )
            normalized_items.append((normalized_item_type, normalized_item_key, normalized_log_id))

        if not normalized_items:
            return 0

        conn = self._connect()
        try:
            timestamp = _now_timestamp()
            for item_type, item_key, activity_log_id in normalized_items:
                conn.execute(
                    """
                    INSERT INTO activity_dismissals (
                        user_id,
                        item_type,
                        item_key,
                        activity_log_id,
                        dismissed_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, item_type, item_key)
                    DO UPDATE SET
                        activity_log_id = excluded.activity_log_id,
                        dismissed_at = excluded.dismissed_at
                    """,
                    (
                        normalized_user_id,
                        item_type,
                        item_key,
                        activity_log_id,
                        timestamp,
                    ),
                )
            conn.commit()
            return len(normalized_items)
        finally:
            conn.close()

    def get_dismissal_set(self, user_id: int) -> list[dict[str, str]]:
        """Return dismissed item keys for one user."""
        normalized_user_id = self._coerce_positive_int(user_id, "user_id")
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT item_type, item_key
                FROM activity_dismissals
                WHERE user_id = ?
                ORDER BY dismissed_at DESC, id DESC
                """,
                (normalized_user_id,),
            ).fetchall()
            return [
                {
                    "item_type": str(row["item_type"]),
                    "item_key": str(row["item_key"]),
                }
                for row in rows
            ]
        finally:
            conn.close()

    def clear_dismissals_for_item_keys(
        self,
        *,
        user_id: int,
        item_type: str,
        item_keys: Iterable[str],
    ) -> int:
        """Clear dismissals for one user + item type + item keys."""
        normalized_user_id = self._coerce_positive_int(user_id, "user_id")
        normalized_item_type = _normalize_item_type(item_type)
        normalized_keys = {
            _normalize_item_key(item_key)
            for item_key in item_keys
            if isinstance(item_key, str) and item_key.strip()
        }
        if not normalized_keys:
            return 0

        conn = self._connect()
        try:
            cursor = conn.executemany(
                """
                DELETE FROM activity_dismissals
                WHERE user_id = ? AND item_type = ? AND item_key = ?
                """,
                (
                    (normalized_user_id, normalized_item_type, item_key)
                    for item_key in normalized_keys
                ),
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        finally:
            conn.close()

    def get_history(self, user_id: int, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return paged dismissal history for one user."""
        normalized_user_id = self._coerce_positive_int(user_id, "user_id")
        normalized_limit = max(1, min(int(limit), 200))
        normalized_offset = max(0, int(offset))

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    d.id,
                    d.user_id,
                    d.item_type,
                    d.item_key,
                    d.activity_log_id,
                    d.dismissed_at,
                    l.snapshot_json,
                    l.origin,
                    l.final_status,
                    l.terminal_at,
                    l.request_id,
                    l.source_id
                FROM activity_dismissals d
                LEFT JOIN activity_log l ON l.id = d.activity_log_id
                WHERE d.user_id = ?
                ORDER BY d.dismissed_at DESC, d.id DESC
                LIMIT ? OFFSET ?
                """,
                (normalized_user_id, normalized_limit, normalized_offset),
            ).fetchall()

            payload: list[dict[str, Any]] = []
            for row in rows:
                row_dict = dict(row)
                raw_snapshot_json = row_dict.pop("snapshot_json", None)
                snapshot_payload = None
                if isinstance(raw_snapshot_json, str):
                    try:
                        snapshot_payload = json.loads(raw_snapshot_json)
                    except (ValueError, TypeError):
                        snapshot_payload = None

                if snapshot_payload is None and row_dict.get("item_type") == "request":
                    request_id = row_dict.get("request_id")
                    if request_id is None:
                        request_id = _parse_request_id_from_item_key(row_dict.get("item_key"))
                    try:
                        normalized_request_id = int(request_id) if request_id is not None else None
                    except (TypeError, ValueError):
                        normalized_request_id = None

                    if normalized_request_id and normalized_request_id > 0:
                        fallback_snapshot, fallback_final_status = self._build_legacy_request_snapshot(
                            conn,
                            normalized_request_id,
                        )
                        if fallback_snapshot is not None:
                            snapshot_payload = fallback_snapshot
                            if not row_dict.get("origin"):
                                row_dict["origin"] = "request"
                            if not row_dict.get("final_status") and fallback_final_status is not None:
                                row_dict["final_status"] = fallback_final_status

                row_dict["snapshot"] = snapshot_payload
                payload.append(row_dict)
            return payload
        finally:
            conn.close()

    def get_undismissed_terminal_downloads(
        self,
        viewer_user_id: int,
        *,
        owner_user_id: int | None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return latest undismissed terminal download snapshots for a viewer.

        `viewer_user_id` controls which dismissals are applied.
        `owner_user_id` scopes activity rows to one owner when provided; when
        omitted, rows across all owners are considered.
        """
        normalized_viewer_user_id = self._coerce_positive_int(viewer_user_id, "viewer_user_id")
        normalized_owner_user_id = (
            self._coerce_positive_int(owner_user_id, "owner_user_id")
            if owner_user_id is not None
            else None
        )
        normalized_limit = max(1, min(int(limit), 500))

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT
                    l.id,
                    l.user_id,
                    l.item_type,
                    l.item_key,
                    l.request_id,
                    l.source_id,
                    l.origin,
                    l.final_status,
                    l.snapshot_json,
                    l.terminal_at
                FROM activity_log l
                LEFT JOIN activity_dismissals d
                    ON d.user_id = ?
                    AND d.item_type = l.item_type
                    AND d.item_key = l.item_key
                WHERE (? IS NULL OR l.user_id = ?)
                    AND l.item_type = 'download'
                    AND l.final_status IN ('complete', 'error', 'cancelled')
                    AND d.id IS NULL
                ORDER BY l.terminal_at DESC, l.id DESC
                LIMIT ?
                """,
                (
                    normalized_viewer_user_id,
                    normalized_owner_user_id,
                    normalized_owner_user_id,
                    normalized_limit * 2,
                ),
            ).fetchall()

            payload: list[dict[str, Any]] = []
            seen_item_keys: set[str] = set()
            for row in rows:
                row_dict = dict(row)
                item_key = str(row_dict.get("item_key") or "")
                if not item_key or item_key in seen_item_keys:
                    continue
                seen_item_keys.add(item_key)

                raw_snapshot_json = row_dict.pop("snapshot_json", None)
                snapshot_payload = None
                if isinstance(raw_snapshot_json, str):
                    try:
                        snapshot_payload = json.loads(raw_snapshot_json)
                    except (ValueError, TypeError):
                        snapshot_payload = None
                row_dict["snapshot"] = snapshot_payload
                payload.append(row_dict)
                if len(payload) >= normalized_limit:
                    break

            return payload
        finally:
            conn.close()

    def clear_history(self, user_id: int) -> int:
        """Delete all dismissals for a user and return deleted row count."""
        normalized_user_id = self._coerce_positive_int(user_id, "user_id")
        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM activity_dismissals WHERE user_id = ?",
                (normalized_user_id,),
            )
            conn.commit()
            return int(cursor.rowcount or 0)
        finally:
            conn.close()
