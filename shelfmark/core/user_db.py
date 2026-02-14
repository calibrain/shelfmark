"""SQLite user database for multi-user support."""

import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from shelfmark.core.auth_modes import AUTH_SOURCE_BUILTIN, AUTH_SOURCE_SET
from shelfmark.core.logger import setup_logger
from shelfmark.core.requests_service import (
    normalize_policy_mode,
    normalize_request_status,
    validate_request_level_payload,
    validate_status_transition,
)

logger = setup_logger(__name__)

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT,
    display_name  TEXT,
    password_hash TEXT,
    oidc_subject  TEXT UNIQUE,
    auth_source   TEXT NOT NULL DEFAULT 'builtin',
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    settings_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS download_requests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'pending',
    source_hint    TEXT,
    content_type   TEXT NOT NULL,
    request_level  TEXT NOT NULL,
    policy_mode    TEXT NOT NULL,
    book_data      TEXT NOT NULL,
    release_data   TEXT,
    note           TEXT,
    admin_note     TEXT,
    reviewed_by    INTEGER REFERENCES users(id),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at    TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_download_requests_user_status_created_at
ON download_requests (user_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_download_requests_status_created_at
ON download_requests (status, created_at DESC);
"""


def get_users_db_path(config_dir: Optional[str] = None) -> str:
    """Return the configured users database path."""
    root = config_dir or os.environ.get("CONFIG_DIR", "/config")
    return os.path.join(root, "users.db")


def sync_builtin_admin_user(
    username: str,
    password_hash: str,
    db_path: Optional[str] = None,
) -> None:
    """Ensure a local admin user exists for configured builtin credentials."""
    normalized_username = (username or "").strip()
    normalized_hash = password_hash or ""
    if not normalized_username or not normalized_hash:
        return

    user_db = UserDB(db_path or get_users_db_path())
    user_db.initialize()

    existing = user_db.get_user(username=normalized_username)
    if existing:
        updates: dict[str, Any] = {}
        if existing.get("password_hash") != normalized_hash:
            updates["password_hash"] = normalized_hash
        if existing.get("role") != "admin":
            updates["role"] = "admin"
        if existing.get("auth_source") != AUTH_SOURCE_BUILTIN:
            updates["auth_source"] = AUTH_SOURCE_BUILTIN
        if updates:
            user_db.update_user(existing["id"], **updates)
            logger.info(f"Updated local admin user '{normalized_username}' from builtin settings")
        return

    user_db.create_user(
        username=normalized_username,
        password_hash=normalized_hash,
        auth_source=AUTH_SOURCE_BUILTIN,
        role="admin",
    )
    logger.info(f"Created local admin user '{normalized_username}' from builtin settings")


class UserDB:
    """Thread-safe SQLite user database."""

    _VALID_AUTH_SOURCES = set(AUTH_SOURCE_SET)

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        """Create database and tables if they don't exist."""
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_CREATE_TABLES_SQL)
                self._migrate_auth_source_column(conn)
                conn.commit()
                # WAL mode must be changed outside an open transaction.
                conn.execute("PRAGMA journal_mode=WAL")
            finally:
                conn.close()
        logger.info(f"User database initialized at {self._db_path}")

    def _migrate_auth_source_column(self, conn: sqlite3.Connection) -> None:
        """Ensure users.auth_source exists and backfill historical rows."""
        columns = conn.execute("PRAGMA table_info(users)").fetchall()
        column_names = {str(col["name"]) for col in columns}

        if "auth_source" not in column_names:
            conn.execute(
                "ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'builtin'"
            )

        # Backfill OIDC-origin users created before auth_source existed.
        conn.execute(
            "UPDATE users SET auth_source = 'oidc' WHERE oidc_subject IS NOT NULL"
        )
        # Defensive cleanup for any legacy null/blank values.
        conn.execute(
            "UPDATE users SET auth_source = 'builtin' WHERE auth_source IS NULL OR auth_source = ''"
        )

    def create_user(
        self,
        username: str,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
        password_hash: Optional[str] = None,
        oidc_subject: Optional[str] = None,
        auth_source: str = "builtin",
        role: str = "user",
    ) -> Dict[str, Any]:
        """Create a new user. Raises ValueError if username or oidc_subject already exists."""
        if auth_source not in self._VALID_AUTH_SOURCES:
            raise ValueError(f"Invalid auth_source: {auth_source}")
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """INSERT INTO users (
                           username, email, display_name, password_hash, oidc_subject, auth_source, role
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        username,
                        email,
                        display_name,
                        password_hash,
                        oidc_subject,
                        auth_source,
                        role,
                    ),
                )
                conn.commit()
                user_id = cursor.lastrowid
                return self._get_user_by_id(conn, user_id)
            except sqlite3.IntegrityError as e:
                raise ValueError(f"User already exists: {e}")
            finally:
                conn.close()

    def get_user(
        self,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        oidc_subject: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get a user by id, username, or oidc_subject. Returns None if not found."""
        conn = self._connect()
        try:
            if user_id is not None:
                return self._get_user_by_id(conn, user_id)
            elif username is not None:
                row = conn.execute(
                    "SELECT * FROM users WHERE username = ?", (username,)
                ).fetchone()
            elif oidc_subject is not None:
                row = conn.execute(
                    "SELECT * FROM users WHERE oidc_subject = ?", (oidc_subject,)
                ).fetchone()
            else:
                return None
            return dict(row) if row else None
        finally:
            conn.close()

    def _get_user_by_id(self, conn: sqlite3.Connection, user_id: int) -> Optional[Dict[str, Any]]:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    _ALLOWED_UPDATE_COLUMNS = {
        "email",
        "display_name",
        "password_hash",
        "oidc_subject",
        "auth_source",
        "role",
    }

    def update_user(self, user_id: int, **kwargs) -> None:
        """Update user fields. Raises ValueError if user not found or invalid column."""
        if not kwargs:
            return
        for k in kwargs:
            if k not in self._ALLOWED_UPDATE_COLUMNS:
                raise ValueError(f"Invalid column: {k}")
        if "auth_source" in kwargs and kwargs["auth_source"] not in self._VALID_AUTH_SOURCES:
            raise ValueError(f"Invalid auth_source: {kwargs['auth_source']}")
        with self._lock:
            conn = self._connect()
            try:
                # Verify user exists
                if not self._get_user_by_id(conn, user_id):
                    raise ValueError(f"User {user_id} not found")
                sets = ", ".join(f"{k} = ?" for k in kwargs)
                values = list(kwargs.values()) + [user_id]
                conn.execute(f"UPDATE users SET {sets} WHERE id = ?", values)
                conn.commit()
            finally:
                conn.close()

    def delete_user(self, user_id: int) -> None:
        """Delete a user and their settings."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
                conn.commit()
            finally:
                conn.close()

    def list_users(self) -> List[Dict[str, Any]]:
        """List all users."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_user_settings(self, user_id: int) -> Dict[str, Any]:
        """Get per-user settings. Returns empty dict if none set."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT settings_json FROM user_settings WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row:
                return json.loads(row["settings_json"])
            return {}
        finally:
            conn.close()

    def set_user_settings(self, user_id: int, settings: Dict[str, Any]) -> None:
        """Merge settings into user's existing settings."""
        with self._lock:
            conn = self._connect()
            try:
                existing = {}
                row = conn.execute(
                    "SELECT settings_json FROM user_settings WHERE user_id = ?", (user_id,)
                ).fetchone()
                if row:
                    existing = json.loads(row["settings_json"])

                existing.update(settings)
                # Remove keys set to None (meaning "clear this override")
                existing = {k: v for k, v in existing.items() if v is not None}
                settings_json = json.dumps(existing)

                conn.execute(
                    """INSERT INTO user_settings (user_id, settings_json) VALUES (?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET settings_json = ?""",
                    (user_id, settings_json, settings_json),
                )
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _serialize_json(value: Any, field: str) -> Optional[str]:
        if value is None:
            return None
        try:
            return json.dumps(value)
        except TypeError as exc:
            raise ValueError(f"{field} must be JSON-serializable") from exc

    @staticmethod
    def _parse_request_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None

        payload = dict(row)
        for key in ("book_data", "release_data"):
            raw_value = payload.get(key)
            if raw_value is None:
                payload[key] = None
                continue
            try:
                payload[key] = json.loads(raw_value)
            except (ValueError, TypeError):
                payload[key] = None
        return payload

    def create_request(
        self,
        *,
        user_id: int,
        content_type: str,
        request_level: str,
        policy_mode: str,
        book_data: Dict[str, Any],
        release_data: Optional[Dict[str, Any]] = None,
        status: str = "pending",
        source_hint: Optional[str] = None,
        note: Optional[str] = None,
        admin_note: Optional[str] = None,
        reviewed_by: Optional[int] = None,
        reviewed_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a download request row and return the created record."""
        if not isinstance(book_data, dict):
            raise ValueError("book_data must be an object")
        if release_data is not None and not isinstance(release_data, dict):
            raise ValueError("release_data must be an object when provided")
        if not content_type:
            raise ValueError("content_type is required")

        normalized_status = normalize_request_status(status)
        normalized_policy_mode = normalize_policy_mode(policy_mode)
        normalized_request_level = validate_request_level_payload(request_level, release_data)

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO download_requests (
                        user_id,
                        status,
                        source_hint,
                        content_type,
                        request_level,
                        policy_mode,
                        book_data,
                        release_data,
                        note,
                        admin_note,
                        reviewed_by,
                        reviewed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        normalized_status,
                        source_hint,
                        content_type,
                        normalized_request_level,
                        normalized_policy_mode,
                        self._serialize_json(book_data, "book_data"),
                        self._serialize_json(release_data, "release_data"),
                        note,
                        admin_note,
                        reviewed_by,
                        reviewed_at,
                    ),
                )
                conn.commit()
                request_id = cursor.lastrowid
                row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                parsed = self._parse_request_row(row)
                if parsed is None:
                    raise ValueError(f"Request {request_id} not found after creation")
                return parsed
            finally:
                conn.close()

    def get_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Get a request row by ID."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM download_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            return self._parse_request_row(row)
        finally:
            conn.close()

    def list_requests(
        self,
        *,
        user_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List requests with optional user/status filters."""
        where_clauses: List[str] = []
        params: List[Any] = []

        if user_id is not None:
            where_clauses.append("user_id = ?")
            params.append(user_id)

        if status is not None:
            where_clauses.append("status = ?")
            params.append(normalize_request_status(status))

        query = "SELECT * FROM download_requests"
        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)
        query += " ORDER BY created_at DESC, id DESC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
            if offset:
                query += " OFFSET ?"
                params.append(offset)
        elif offset:
            query += " LIMIT -1 OFFSET ?"
            params.append(offset)

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
            results: List[Dict[str, Any]] = []
            for row in rows:
                parsed = self._parse_request_row(row)
                if parsed is not None:
                    results.append(parsed)
            return results
        finally:
            conn.close()

    _ALLOWED_REQUEST_UPDATE_COLUMNS = {
        "status",
        "source_hint",
        "content_type",
        "request_level",
        "policy_mode",
        "book_data",
        "release_data",
        "note",
        "admin_note",
        "reviewed_by",
        "reviewed_at",
    }

    def update_request(self, request_id: int, **kwargs) -> Dict[str, Any]:
        """Update request fields and return the updated record."""
        if not kwargs:
            request = self.get_request(request_id)
            if request is None:
                raise ValueError(f"Request {request_id} not found")
            return request

        for key in kwargs:
            if key not in self._ALLOWED_REQUEST_UPDATE_COLUMNS:
                raise ValueError(f"Invalid request column: {key}")

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                current = self._parse_request_row(row)
                if current is None:
                    raise ValueError(f"Request {request_id} not found")

                updates = dict(kwargs)

                if "status" in updates:
                    _, normalized_status = validate_status_transition(
                        current["status"],
                        updates["status"],
                    )
                    updates["status"] = normalized_status

                if "policy_mode" in updates:
                    updates["policy_mode"] = normalize_policy_mode(updates["policy_mode"])

                if "content_type" in updates and not updates["content_type"]:
                    raise ValueError("content_type is required")

                candidate_request_level = updates.get("request_level", current["request_level"])
                candidate_release_data = (
                    updates["release_data"] if "release_data" in updates else current["release_data"]
                )
                normalized_request_level = validate_request_level_payload(
                    candidate_request_level,
                    candidate_release_data,
                )
                if "request_level" in updates:
                    updates["request_level"] = normalized_request_level

                if "book_data" in updates:
                    if not isinstance(updates["book_data"], dict):
                        raise ValueError("book_data must be an object")
                    updates["book_data"] = self._serialize_json(updates["book_data"], "book_data")

                if "release_data" in updates:
                    if updates["release_data"] is not None and not isinstance(updates["release_data"], dict):
                        raise ValueError("release_data must be an object when provided")
                    updates["release_data"] = self._serialize_json(
                        updates["release_data"],
                        "release_data",
                    )

                set_clause = ", ".join(f"{column} = ?" for column in updates)
                values = list(updates.values()) + [request_id]
                conn.execute(
                    f"UPDATE download_requests SET {set_clause} WHERE id = ?",
                    values,
                )
                conn.commit()

                updated_row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                parsed = self._parse_request_row(updated_row)
                if parsed is None:
                    raise ValueError(f"Request {request_id} not found after update")
                return parsed
            finally:
                conn.close()

    def count_pending_requests(self) -> int:
        """Count all pending requests."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM download_requests WHERE status = 'pending'"
            ).fetchone()
            return int(row["count"]) if row else 0
        finally:
            conn.close()

    def count_user_pending_requests(self, user_id: int) -> int:
        """Count pending requests for a specific user."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM download_requests WHERE user_id = ? AND status = 'pending'",
                (user_id,),
            ).fetchone()
            return int(row["count"]) if row else 0
        finally:
            conn.close()
