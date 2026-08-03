"""Persistence for source provenance and Book-scoped import activities."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from shelfmark.core.naming import sanitize_filename
from shelfmark.core.request_helpers import now_utc_iso

_STATES = frozenset({"matching", "needs review", "importing", "completed", "failed", "cancelled"})
_NON_TERMINAL_STATES = frozenset({"matching", "needs review", "importing"})


class ImportActivityService:
    """Store immutable source releases and the activities that import from them."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _json(value: object) -> str:
        try:
            return json.dumps(value, separators=(",", ":"), sort_keys=True)
        except (TypeError, ValueError) as exc:
            msg = "value must be JSON-serializable"
            raise ValueError(msg) from exc

    @staticmethod
    def _decoded(value: object) -> dict[str, Any]:
        if not isinstance(value, str):
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _activity(self, conn: sqlite3.Connection, activity_id: int) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT activity.*, source.source_key, source.source, source.metadata_json, source.source_root
            FROM import_activities AS activity
            JOIN source_releases AS source ON source.id = activity.source_release_id
            WHERE activity.id = ?
            """,
            (activity_id,),
        ).fetchone()
        if row is None:
            msg = "import activity not found"
            raise ValueError(msg)
        result = dict(row)
        result["book_snapshot"] = self._decoded(result.pop("book_snapshot_json"))
        result["error_context"] = self._decoded(result.pop("error_context_json"))
        result["source_release"] = {
            "id": result["source_release_id"],
            "source_key": result.pop("source_key"),
            "source": result.pop("source"),
            "metadata": self._decoded(result.pop("metadata_json")),
            "source_root": result.pop("source_root"),
        }
        selections = conn.execute(
            """
            SELECT id, source_member_id, evidence_json, planned_output_path
            FROM import_activity_selections WHERE import_activity_id = ? ORDER BY id
            """,
            (activity_id,),
        ).fetchall()
        result["selections"] = [
            {**dict(selection), "evidence": self._decoded(selection["evidence_json"])}
            for selection in selections
        ]
        for selection in result["selections"]:
            selection.pop("evidence_json")
        return result

    def accept_book_targeted_release(
        self,
        *,
        source_key: str,
        source: str,
        source_metadata: dict[str, Any],
        task_id: str,
        book_id: int,
    ) -> dict[str, Any]:
        """Create an immutable matching activity, retaining its source independently."""
        if not all(
            isinstance(value, str) and value.strip() for value in (source_key, source, task_id)
        ):
            msg = "source_key, source, and task_id must be non-empty strings"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
                if book is None:
                    msg = "book not found"
                    raise ValueError(msg)
                conn.execute(
                    """
                    INSERT INTO source_releases (source_key, source, metadata_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(source_key) DO NOTHING
                    """,
                    (source_key.strip(), source.strip(), self._json(source_metadata)),
                )
                source_release = conn.execute(
                    "SELECT id FROM source_releases WHERE source_key = ?", (source_key.strip(),)
                ).fetchone()
                if source_release is None:
                    msg = "source release was not created"
                    raise RuntimeError(msg)
                cursor = conn.execute(
                    """
                    INSERT INTO import_activities
                    (task_id, source_release_id, book_id, book_snapshot_json, state, updated_at)
                    VALUES (?, ?, ?, ?, 'matching', ?)
                    """,
                    (
                        task_id.strip(),
                        source_release["id"],
                        book_id,
                        self._json(dict(book)),
                        now_utc_iso(),
                    ),
                )
                conn.commit()
                if cursor.lastrowid is None:
                    msg = "import activity was not created"
                    raise RuntimeError(msg)
                return self._activity(conn, cursor.lastrowid)
            finally:
                conn.close()

    def record_source_member(
        self,
        *,
        source_release_id: int,
        relative_path: str,
        size: int | None,
        file_format: str | None,
        discovery_status: str,
    ) -> dict[str, Any]:
        """Upsert a discovered original member without tying it to a Book."""
        if (
            not relative_path
            or Path(relative_path).is_absolute()
            or ".." in Path(relative_path).parts
        ):
            msg = "relative_path must be a safe relative path"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT INTO source_release_members
                    (source_release_id, relative_path, size, format, discovery_status)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(source_release_id, relative_path) DO UPDATE SET
                    size = excluded.size, format = excluded.format,
                    discovery_status = excluded.discovery_status
                    """,
                    (source_release_id, relative_path, size, file_format, discovery_status),
                )
                row = conn.execute(
                    "SELECT * FROM source_release_members WHERE source_release_id = ? AND relative_path = ?",
                    (source_release_id, relative_path),
                ).fetchone()
                conn.commit()
                return dict(row) if row is not None else {}
            finally:
                conn.close()

    def set_source_root(self, *, source_release_id: int, source_root: Path) -> None:
        """Persist the retained original root used for later manual corrections."""
        if not source_root.is_absolute():
            msg = "source_root must be absolute"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "UPDATE source_releases SET source_root = ? WHERE id = ?",
                    (str(source_root), source_release_id),
                )
                if cursor.rowcount != 1:
                    msg = "source release not found"
                    raise ValueError(msg)
                conn.commit()
            finally:
                conn.close()

    def create_manual_correction(
        self,
        *,
        source_release_id: int,
        book_id: int,
        task_id: str,
        selected_by_user_id: int,
    ) -> dict[str, Any]:
        """Create a Book-scoped correction from a retained completed source release."""
        if not isinstance(selected_by_user_id, int) or selected_by_user_id < 1:
            msg = "selected_by_user_id must be a positive integer"
            raise ValueError(msg)
        if not isinstance(task_id, str) or not task_id.strip():
            msg = "task_id must be a non-empty string"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
                source_release = conn.execute(
                    "SELECT id FROM source_releases WHERE id = ?", (source_release_id,)
                ).fetchone()
                if book is None or source_release is None:
                    msg = "book or source release not found"
                    raise ValueError(msg)
                cursor = conn.execute(
                    """
                    INSERT INTO import_activities
                    (task_id, source_release_id, book_id, book_snapshot_json, state, selected_by_user_id, updated_at)
                    VALUES (?, ?, ?, ?, 'matching', ?, ?)
                    """,
                    (
                        task_id.strip(),
                        source_release_id,
                        book_id,
                        self._json(dict(book)),
                        selected_by_user_id,
                        now_utc_iso(),
                    ),
                )
                conn.commit()
                if cursor.lastrowid is None:
                    msg = "manual correction was not created"
                    raise RuntimeError(msg)
                return self._activity(conn, cursor.lastrowid)
            finally:
                conn.close()

    def get_by_task_id(self, task_id: str) -> dict[str, Any] | None:
        """Return the activity associated with a queue task, if it exists."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM import_activities WHERE task_id = ?", (task_id,)
            ).fetchone()
            return self._activity(conn, row["id"]) if row is not None else None
        finally:
            conn.close()

    def get_book_activity(self, *, activity_id: int, book_id: int) -> dict[str, Any] | None:
        """Return one activity only when it remains attached to the requested Book."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM import_activities WHERE id = ? AND book_id = ?",
                (activity_id, book_id),
            ).fetchone()
            return self._activity(conn, row["id"]) if row is not None else None
        finally:
            conn.close()

    def source_members(self, *, source_release_id: int) -> list[dict[str, Any]]:
        """Return the retained members available for an activity's default selection."""
        conn = self._connect()
        try:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM source_release_members WHERE source_release_id = ? ORDER BY id",
                    (source_release_id,),
                ).fetchall()
            ]
        finally:
            conn.close()

    def plan_import(
        self,
        *,
        activity_id: int,
        storage_root: Path,
        selections: list[dict[str, Any]],
        allow_existing_book_members: bool = False,
    ) -> dict[str, Any]:
        """Persist selection evidence and exact output paths before transfer starts."""
        with self._lock:
            conn = self._connect()
            try:
                activity = self._activity(conn, activity_id)
                if activity["state"] != "matching" or activity["selections"]:
                    msg = "import activity cannot be planned"
                    raise ValueError(msg)
                for selection in selections:
                    member_id = selection.get("source_member_id")
                    if not isinstance(member_id, int):
                        msg = "each selection needs a source member"
                        raise TypeError(msg)
                    member = conn.execute(
                        "SELECT relative_path FROM source_release_members WHERE id = ? AND source_release_id = ?",
                        (member_id, activity["source_release_id"]),
                    ).fetchone()
                    if member is None:
                        msg = "source member does not belong to this source release"
                        raise ValueError(msg)
                    duplicate = conn.execute(
                        """
                        SELECT 1
                        FROM import_activity_selections AS selection
                        JOIN import_activities AS prior ON prior.id = selection.import_activity_id
                        WHERE prior.book_id = ? AND selection.source_member_id = ?
                        """,
                        (activity["book_id"], member_id),
                    ).fetchone()
                    if duplicate is not None and not allow_existing_book_members:
                        msg = "source member is already selected for this Book"
                        raise ValueError(msg)
                    path = self._planned_output_path(
                        storage_root=storage_root,
                        book_id=activity["book_id"],
                        source_release_id=activity["source_release_id"],
                        activity_id=activity_id
                        if activity["selected_by_user_id"] is not None
                        else None,
                        relative_path=member["relative_path"],
                    )
                    conn.execute(
                        """
                        INSERT INTO import_activity_selections
                        (import_activity_id, source_member_id, evidence_json, planned_output_path)
                        VALUES (?, ?, ?, ?)
                        """,
                        (activity_id, member_id, self._json(selection.get("evidence", {})), path),
                    )
                conn.execute(
                    "UPDATE import_activities SET state = 'importing', updated_at = ? WHERE id = ?",
                    (now_utc_iso(), activity_id),
                )
                conn.commit()
                return self._activity(conn, activity_id)
            finally:
                conn.close()

    @staticmethod
    def _planned_output_path(
        *,
        storage_root: Path,
        book_id: int | None,
        source_release_id: int,
        activity_id: int | None,
        relative_path: str,
    ) -> str:
        if book_id is None:
            msg = "import activity has no Book target"
            raise ValueError(msg)
        components = Path(relative_path).parts
        sanitized = [sanitize_filename(component) for component in components]
        if not components or any(not component for component in sanitized):
            msg = "source member path has an unusable component"
            raise ValueError(msg)
        root = storage_root / "books" / str(book_id) / str(source_release_id)
        if activity_id is not None:
            root /= str(activity_id)
        return str(root / Path(*sanitized))

    def fail(self, *, activity_id: int, error_context: dict[str, Any]) -> dict[str, Any]:
        """Retain a retryable failure without changing the activity context."""
        return self._set_state(activity_id, "failed", error_context=error_context)

    def cancel(self, *, activity_id: int) -> dict[str, Any]:
        """Terminally cancel an incomplete activity without touching its source."""
        return self._set_state(activity_id, "cancelled")

    def complete(self, *, activity_id: int) -> dict[str, Any]:
        """Mark an importing activity complete after File history is finalized."""
        return self._set_state(activity_id, "completed")

    def retry(self, *, activity_id: int) -> dict[str, Any]:
        """Restart a failed activity using its unchanged selection and output plan."""
        with self._lock:
            conn = self._connect()
            try:
                activity = self._activity(conn, activity_id)
                if activity["state"] != "failed":
                    msg = "import activity cannot be retried"
                    raise ValueError(msg)
                state = "importing" if activity["selections"] else "matching"
                conn.execute(
                    """
                    UPDATE import_activities SET state = ?, retry_count = retry_count + 1,
                    error_context_json = NULL, updated_at = ? WHERE id = ?
                    """,
                    (state, now_utc_iso(), activity_id),
                )
                conn.commit()
                return self._activity(conn, activity_id)
            finally:
                conn.close()

    def reconcile(self, *, activity_id: int) -> dict[str, Any]:
        """Report planned artifacts still missing after a restart without creating Files."""
        with self._lock:
            conn = self._connect()
            try:
                activity = self._activity(conn, activity_id)
                missing = [
                    selection["planned_output_path"]
                    for selection in activity["selections"]
                    if not Path(selection["planned_output_path"]).is_file()
                ]
                return {"activity": activity, "missing_output_paths": missing}
            finally:
                conn.close()

    def _set_state(
        self, activity_id: int, state: str, *, error_context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if state not in _STATES:
            msg = "invalid import activity state"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                activity = self._activity(conn, activity_id)
                if activity["state"] not in _NON_TERMINAL_STATES:
                    msg = "import activity is already terminal"
                    raise ValueError(msg)
                conn.execute(
                    "UPDATE import_activities SET state = ?, error_context_json = ?, updated_at = ? WHERE id = ?",
                    (
                        state,
                        self._json(error_context) if error_context is not None else None,
                        now_utc_iso(),
                        activity_id,
                    ),
                )
                conn.commit()
                return self._activity(conn, activity_id)
            finally:
                conn.close()
