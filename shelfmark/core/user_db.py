"""SQLite user database for multi-user support."""

import json
import sqlite3
import threading
from typing import Any, Dict, List, Optional

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT,
    display_name  TEXT,
    password_hash TEXT,
    oidc_subject  TEXT UNIQUE,
    role          TEXT NOT NULL DEFAULT 'user',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id       INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    settings_json TEXT NOT NULL DEFAULT '{}'
);
"""


class UserDB:
    """Thread-safe SQLite user database."""

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
                conn.execute("PRAGMA journal_mode=WAL")
                conn.commit()
            finally:
                conn.close()
        logger.info(f"User database initialized at {self._db_path}")

    def create_user(
        self,
        username: str,
        email: Optional[str] = None,
        display_name: Optional[str] = None,
        password_hash: Optional[str] = None,
        oidc_subject: Optional[str] = None,
        role: str = "user",
    ) -> Dict[str, Any]:
        """Create a new user. Raises ValueError if username or oidc_subject already exists."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """INSERT INTO users (username, email, display_name, password_hash, oidc_subject, role)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (username, email, display_name, password_hash, oidc_subject, role),
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

    def update_user(self, user_id: int, **kwargs) -> None:
        """Update user fields. Raises ValueError if user not found."""
        if not kwargs:
            return
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
