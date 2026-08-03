"""SQLite user database for multi-user support."""

import json
import os
import sqlite3
import threading
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Any, ClassVar

from shelfmark.core.activity_view_state_service import user_viewer_scope
from shelfmark.core.auth_modes import AUTH_SOURCE_BUILTIN, AUTH_SOURCE_SET
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_validation import (
    normalize_request_status,
    validate_status_transition,
)

logger = setup_logger(__name__)


def _is_valid_email(value: str) -> bool:
    parsed = parseaddr(value)[1]
    return bool(parsed) and "@" in parsed


_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT,
    identity_email TEXT,
    display_name  TEXT,
    password_hash TEXT,
    oidc_subject  TEXT UNIQUE,
    auth_source   TEXT NOT NULL DEFAULT 'builtin',
    role          TEXT NOT NULL DEFAULT 'user',
    is_active     INTEGER NOT NULL DEFAULT 1,
    library_capability TEXT NOT NULL DEFAULT 'download-capable',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id                  INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    kindle_address           TEXT,
    notifications_enabled    INTEGER NOT NULL DEFAULT 0,
    notification_transport   TEXT,
    notification_destination TEXT
);

CREATE TABLE IF NOT EXISTS download_requests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id        INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'pending',
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

CREATE TABLE IF NOT EXISTS download_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    username TEXT,
    request_id INTEGER,
    source TEXT NOT NULL,
    source_display_name TEXT,
    title TEXT NOT NULL,
    author TEXT,
    format TEXT,
    size TEXT,
    preview TEXT,
    content_type TEXT,
    origin TEXT NOT NULL DEFAULT 'book',
    final_status TEXT NOT NULL,
    status_message TEXT,
    download_path TEXT,
    retry_payload TEXT,
    queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    terminal_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_download_history_user_status
ON download_history (user_id, final_status, terminal_at DESC);

CREATE INDEX IF NOT EXISTS idx_download_history_recent
ON download_history (user_id, terminal_at DESC, id DESC);

-- One row = one file under #13's schema (b); files sharing a task_id belong
-- to the same release. The index is non-unique because a release can span
-- multiple file rows. Fresh databases get it here; upgrades get it via
-- _migrate_download_history_task_id_nonunique().
CREATE INDEX IF NOT EXISTS idx_download_history_task_id
ON download_history (task_id);

-- download_history.book_id and idx_download_history_book_id are added by
-- _migrate_download_history_book_id() so the column exists before the index
-- is created on upgraded pre-library databases. Fresh databases get both via
-- the CREATE TABLE / CREATE INDEX calls inside that migration.

CREATE TABLE IF NOT EXISTS activity_view_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    viewer_scope TEXT NOT NULL,
    item_type TEXT NOT NULL,
    item_key TEXT NOT NULL,
    dismissed_at TIMESTAMP,
    cleared_at TIMESTAMP,
    UNIQUE(viewer_scope, item_type, item_key)
);

CREATE INDEX IF NOT EXISTS idx_activity_view_state_history
ON activity_view_state (viewer_scope, dismissed_at DESC, id DESC)
WHERE dismissed_at IS NOT NULL AND cleared_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_activity_view_state_hidden
ON activity_view_state (viewer_scope, item_type, item_key)
WHERE dismissed_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS books (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_provider  TEXT    NOT NULL,
    provider_book_id   TEXT    NOT NULL,
    title              TEXT    NOT NULL,
    author             TEXT,
    subtitle           TEXT,
    publish_year       INTEGER,
    isbn_13            TEXT,
    cover_url          TEXT,
    series_name        TEXT,
    series_position    REAL,
    language           TEXT,
    metadata_json      TEXT    NOT NULL DEFAULT '{}',
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (metadata_provider, provider_book_id)
);

CREATE INDEX IF NOT EXISTS idx_books_provider
ON books (metadata_provider, provider_book_id);

CREATE TABLE IF NOT EXISTS user_library (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id    INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    added_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, book_id)
);

CREATE INDEX IF NOT EXISTS idx_user_library_book_id_added_at
ON user_library (book_id, added_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_library_user_added_at_book_id
ON user_library (user_id, added_at DESC, book_id DESC);

CREATE TABLE IF NOT EXISTS user_downloads (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    history_id  INTEGER NOT NULL REFERENCES download_history(id) ON DELETE CASCADE,
    added_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, history_id)
);

CREATE INDEX IF NOT EXISTS idx_user_downloads_history
ON user_downloads (history_id);

CREATE TABLE IF NOT EXISTS source_releases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key      TEXT NOT NULL UNIQUE,
    source          TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    source_root     TEXT,
    accepted_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_release_members (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_release_id   INTEGER NOT NULL REFERENCES source_releases(id) ON DELETE CASCADE,
    relative_path       TEXT NOT NULL,
    size                INTEGER,
    format              TEXT,
    discovery_status    TEXT NOT NULL,
    discovered_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_release_id, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_source_release_members_release
ON source_release_members (source_release_id);

CREATE TABLE IF NOT EXISTS import_activities (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT NOT NULL UNIQUE,
    source_release_id   INTEGER NOT NULL REFERENCES source_releases(id),
    book_id             INTEGER REFERENCES books(id) ON DELETE SET NULL,
    book_snapshot_json  TEXT NOT NULL,
    state               TEXT NOT NULL,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    error_context_json  TEXT,
    selected_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_import_activities_source_release
ON import_activities (source_release_id);

CREATE INDEX IF NOT EXISTS idx_import_activities_book_state
ON import_activities (book_id, state);

CREATE TABLE IF NOT EXISTS import_activity_selections (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    import_activity_id  INTEGER NOT NULL REFERENCES import_activities(id) ON DELETE CASCADE,
    source_member_id    INTEGER NOT NULL REFERENCES source_release_members(id),
    evidence_json       TEXT NOT NULL DEFAULT '{}',
    planned_output_path TEXT NOT NULL,
    UNIQUE (import_activity_id, source_member_id)
);

CREATE INDEX IF NOT EXISTS idx_import_activity_selections_activity
ON import_activity_selections (import_activity_id);
"""


def _require_loaded_user(user: dict[str, Any] | None) -> dict[str, Any]:
    """Return a loaded user row or raise when the DB insert result is inconsistent."""
    if user is None:
        msg = "Failed to load newly created user"
        raise RuntimeError(msg)
    return user


def get_users_db_path(config_dir: str | None = None) -> str:
    """Return the configured users database path."""
    root = config_dir or os.environ.get("CONFIG_DIR", "/config")
    return str(Path(root) / "users.db")


def sync_builtin_admin_user(
    username: str,
    password_hash: str,
    db_path: str | None = None,
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
        existing_auth_source = (
            str(existing.get("auth_source") or AUTH_SOURCE_BUILTIN).strip().lower()
        )
        if existing_auth_source != AUTH_SOURCE_BUILTIN:
            logger.warning(
                "Skipped builtin admin sync for username '%s' because it belongs to auth_source='%s'",
                normalized_username,
                existing_auth_source,
            )
            return
        updates: dict[str, Any] = {}
        if existing.get("password_hash") != normalized_hash:
            updates["password_hash"] = normalized_hash
        if existing.get("role") != "admin":
            updates["role"] = "admin"
        if existing.get("auth_source") != AUTH_SOURCE_BUILTIN:
            updates["auth_source"] = AUTH_SOURCE_BUILTIN
        if updates:
            user_db.update_user(existing["id"], **updates)
            logger.info("Updated local admin user '%s' from builtin settings", normalized_username)
        return

    user_db.create_user(
        username=normalized_username,
        password_hash=normalized_hash,
        auth_source=AUTH_SOURCE_BUILTIN,
        role="admin",
    )
    logger.info("Created local admin user '%s' from builtin settings", normalized_username)


class UserDB:
    """Thread-safe SQLite user database."""

    _VALID_AUTH_SOURCES: ClassVar[frozenset[str]] = frozenset(AUTH_SOURCE_SET)
    _VALID_LIBRARY_CAPABILITIES: ClassVar[frozenset[str]] = frozenset(
        {"download-capable", "request-only"}
    )

    def __init__(self, db_path: str) -> None:
        """Initialize the user database wrapper for the given SQLite path."""
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
                self._reset_legacy_requests(conn)
                conn.executescript(_CREATE_TABLES_SQL)
                self._migrate_auth_source_column(conn)
                self._migrate_identity_email_and_notifications(conn)
                self._migrate_library_capability_column(conn)
                self._migrate_download_history_queued_at(conn)
                self._migrate_download_history_retry_payload(conn)
                self._migrate_download_history_book_id(conn)
                self._migrate_download_history_task_id_nonunique(conn)
                self._migrate_download_history_import_activity_id(conn)
                self._migrate_source_release_review_columns(conn)
                conn.commit()
                # WAL mode must be changed outside an open transaction.
                conn.execute("PRAGMA journal_mode=WAL")
            finally:
                conn.close()

    def _migrate_auth_source_column(self, conn: sqlite3.Connection) -> None:
        """Ensure users.auth_source exists and backfill historical rows."""
        columns = conn.execute("PRAGMA table_info(users)").fetchall()
        column_names = {str(col["name"]) for col in columns}

        if "auth_source" not in column_names:
            conn.execute("ALTER TABLE users ADD COLUMN auth_source TEXT NOT NULL DEFAULT 'builtin'")

        # Backfill OIDC-origin users created before auth_source existed.
        if "oidc_subject" in column_names:
            conn.execute("UPDATE users SET auth_source = 'oidc' WHERE oidc_subject IS NOT NULL")
        # Defensive cleanup for any legacy null/blank values.
        conn.execute(
            "UPDATE users SET auth_source = 'builtin' WHERE auth_source IS NULL OR auth_source = ''"
        )

    def _migrate_identity_email_and_notifications(self, conn: sqlite3.Connection) -> None:
        """Separate source identity email from the user's notification contact email."""
        user_columns = {str(column["name"]) for column in conn.execute("PRAGMA table_info(users)")}
        if "email" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if "identity_email" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN identity_email TEXT")
        conn.execute(
            "UPDATE users SET identity_email = email "
            "WHERE identity_email IS NULL AND auth_source IN ('oidc', 'cwa', 'proxy')"
        )
        rows = conn.execute(
            """
            SELECT user_preferences.user_id, notification_destination, users.auth_source
            FROM user_preferences JOIN users ON users.id = user_preferences.user_id
            WHERE notification_transport = 'email'
            """
        ).fetchall()
        for row in rows:
            destination = row["notification_destination"]
            if isinstance(destination, str) and _is_valid_email(destination):
                if row["auth_source"] in {"oidc", "cwa", "proxy"}:
                    conn.execute(
                        "UPDATE users SET identity_email = COALESCE(identity_email, email) WHERE id = ?",
                        (row["user_id"],),
                    )
                conn.execute(
                    "UPDATE users SET email = ? WHERE id = ?",
                    (destination.strip(), row["user_id"]),
                )
                conn.execute(
                    "UPDATE user_preferences SET notification_transport = NULL, "
                    "notification_destination = NULL WHERE user_id = ?",
                    (row["user_id"],),
                )
            else:
                conn.execute(
                    "UPDATE user_preferences SET notifications_enabled = 0, "
                    "notification_transport = NULL, notification_destination = NULL WHERE user_id = ?",
                    (row["user_id"],),
                )

    def _migrate_library_capability_column(self, conn: sqlite3.Connection) -> None:
        """Ensure each user has one explicit Library Capability."""
        columns = conn.execute("PRAGMA table_info(users)").fetchall()
        column_names = {str(col["name"]) for col in columns}
        if "library_capability" not in column_names:
            conn.execute(
                "ALTER TABLE users ADD COLUMN library_capability TEXT "
                "NOT NULL DEFAULT 'download-capable'"
            )
        conn.execute(
            "UPDATE users SET library_capability = 'download-capable' "
            "WHERE library_capability NOT IN ('download-capable', 'request-only') "
            "OR library_capability IS NULL"
        )

    def _reset_legacy_requests(self, conn: sqlite3.Connection) -> None:
        """Replace legacy request storage without migrating its incompatible rows."""
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'download_requests'"
        ).fetchone()
        if table is None:
            return
        columns = conn.execute("PRAGMA table_info(download_requests)").fetchall()
        expected = {
            "id",
            "user_id",
            "book_id",
            "status",
            "note",
            "admin_note",
            "reviewed_by",
            "created_at",
            "reviewed_at",
        }
        if {str(column["name"]) for column in columns} == expected:
            return
        conn.execute("DROP TABLE download_requests")

    def _migrate_download_history_queued_at(self, conn: sqlite3.Connection) -> None:
        """Ensure download_history.queued_at exists for queue-time recording."""
        columns = conn.execute("PRAGMA table_info(download_history)").fetchall()
        column_names = {str(col["name"]) for col in columns}
        if "queued_at" not in column_names:
            conn.execute("ALTER TABLE download_history ADD COLUMN queued_at TIMESTAMP")
            conn.execute(
                "UPDATE download_history SET queued_at = CURRENT_TIMESTAMP WHERE queued_at IS NULL"
            )

    def _migrate_download_history_retry_payload(self, conn: sqlite3.Connection) -> None:
        """Ensure download_history.retry_payload exists for restart-safe retries."""
        columns = conn.execute("PRAGMA table_info(download_history)").fetchall()
        column_names = {str(col["name"]) for col in columns}
        if "retry_payload" not in column_names:
            conn.execute("ALTER TABLE download_history ADD COLUMN retry_payload TEXT")

    def _migrate_download_history_book_id(self, conn: sqlite3.Connection) -> None:
        """Ensure download_history.book_id exists to link Files to Books.

        Fresh databases get the column via ``_CREATE_TABLES_SQL``; this migration
        covers upgrades of pre-library ``users.db`` files. The library always
        starts from a fresh database (no legacy ``download_history`` rows to
        backfill), so this only adds the nullable column — book rows are minted
        on Add-to-Library going forward and legacy rows keep ``book_id IS NULL``.
        """
        columns = conn.execute("PRAGMA table_info(download_history)").fetchall()
        column_names = {str(col["name"]) for col in columns}
        if "book_id" not in column_names:
            conn.execute(
                "ALTER TABLE download_history ADD COLUMN book_id "
                "INTEGER REFERENCES books(id) ON DELETE SET NULL"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_download_history_book_id "
            "ON download_history (book_id, final_status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_download_history_book_completed_path "
            "ON download_history (book_id) WHERE final_status = 'complete' "
            "AND download_path IS NOT NULL"
        )

    def _migrate_source_release_review_columns(self, conn: sqlite3.Connection) -> None:
        """Add retained-source and selector audit fields to existing library databases."""
        source_columns = {
            str(column["name"]) for column in conn.execute("PRAGMA table_info(source_releases)")
        }
        if "source_root" not in source_columns:
            conn.execute("ALTER TABLE source_releases ADD COLUMN source_root TEXT")

        activity_columns = {
            str(column["name"]) for column in conn.execute("PRAGMA table_info(import_activities)")
        }
        if "selected_by_user_id" not in activity_columns:
            conn.execute(
                "ALTER TABLE import_activities ADD COLUMN selected_by_user_id "
                "INTEGER REFERENCES users(id) ON DELETE SET NULL"
            )

    def _migrate_download_history_task_id_nonunique(self, conn: sqlite3.Connection) -> None:
        """Drop the legacy UNIQUE constraint on ``download_history.task_id``.

        Per #13's resolution (schema shape (b)), ``download_history`` is one
        row = one file; the files of a single release share a non-unique
        ``task_id``. SQLite cannot drop a UNIQUE constraint in place, so when
        the legacy constraint is detected the table is rebuilt via the
        copy-to-temp / drop / recreate / copy-back idiom. Legacy rows keep
        their existing values (single-file releases trivially form
        well-formed one-member groups). Fresh databases get the non-unique
        column directly from ``_CREATE_TABLES_SQL`` and skip the rebuild.
        """
        indexes = conn.execute("PRAGMA index_list(download_history)").fetchall()
        has_unique_task_id = any(
            str(idx["name"]) == "sqlite_autoindex_download_history_1" and bool(idx["unique"])
            for idx in indexes
        )
        if not has_unique_task_id:
            # Fresh DB (no auto-index) or already migrated. Ensure the
            # non-unique index exists for both paths.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_download_history_task_id "
                "ON download_history (task_id)"
            )
            return

        conn.executescript(
            """
            CREATE TABLE download_history_migration AS
                SELECT * FROM download_history;

            DROP TABLE download_history;

            CREATE TABLE download_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                username TEXT,
                request_id INTEGER,
                source TEXT NOT NULL,
                source_display_name TEXT,
                title TEXT NOT NULL,
                author TEXT,
                format TEXT,
                size TEXT,
                preview TEXT,
                content_type TEXT,
                origin TEXT NOT NULL DEFAULT 'book',
                final_status TEXT NOT NULL,
                status_message TEXT,
                download_path TEXT,
                retry_payload TEXT,
                queued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                terminal_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                book_id INTEGER REFERENCES books(id) ON DELETE SET NULL,
                import_activity_id INTEGER REFERENCES import_activities(id) ON DELETE SET NULL
            );

            INSERT INTO download_history (
                id, task_id, user_id, username, request_id, source,
                source_display_name, title, author, format, size, preview,
                content_type, origin, final_status, status_message,
                download_path, retry_payload, queued_at, terminal_at, book_id,
                import_activity_id
            )
            SELECT
                id, task_id, user_id, username, request_id, source,
                source_display_name, title, author, format, size, preview,
                content_type, origin, final_status, status_message,
                download_path, retry_payload, queued_at, terminal_at, book_id,
                NULL
            FROM download_history_migration;

            DROP TABLE download_history_migration;

            CREATE INDEX idx_download_history_user_status
                ON download_history (user_id, final_status, terminal_at DESC);
            CREATE INDEX idx_download_history_recent
                ON download_history (user_id, terminal_at DESC, id DESC);
            CREATE INDEX idx_download_history_task_id
                ON download_history (task_id);
            CREATE INDEX idx_download_history_book_id
                ON download_history (book_id, final_status);
            CREATE INDEX idx_download_history_book_completed_path
                ON download_history (book_id)
                WHERE final_status = 'complete' AND download_path IS NOT NULL;
            """
        )

    def _migrate_download_history_import_activity_id(self, conn: sqlite3.Connection) -> None:
        """Link Files to their durable Import Activity when one exists."""
        column_names = {
            str(column["name"]) for column in conn.execute("PRAGMA table_info(download_history)")
        }
        if "import_activity_id" not in column_names:
            conn.execute(
                "ALTER TABLE download_history ADD COLUMN import_activity_id "
                "INTEGER REFERENCES import_activities(id) ON DELETE SET NULL"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_download_history_import_activity "
            "ON download_history (import_activity_id)"
        )

    def create_user(
        self,
        username: str,
        email: str | None = None,
        identity_email: str | None = None,
        display_name: str | None = None,
        password_hash: str | None = None,
        oidc_subject: str | None = None,
        auth_source: str = "builtin",
        role: str = "user",
        library_capability: str = "download-capable",
    ) -> dict[str, Any]:
        """Create a new user. Raises ValueError if username or oidc_subject already exists."""
        if auth_source not in self._VALID_AUTH_SOURCES:
            msg = f"Invalid auth_source: {auth_source}"
            raise ValueError(msg)
        if library_capability not in self._VALID_LIBRARY_CAPABILITIES:
            msg = f"Invalid library_capability: {library_capability}"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """INSERT INTO users (
                            username, email, identity_email, display_name, password_hash, oidc_subject, auth_source, role,
                           library_capability
                        )
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        username,
                        email,
                        identity_email,
                        display_name,
                        password_hash,
                        oidc_subject,
                        auth_source,
                        role,
                        library_capability,
                    ),
                )
                conn.commit()
                user_id = cursor.lastrowid
                if not isinstance(user_id, int):
                    msg = "Failed to create user"
                    raise TypeError(msg)
                created_user = self._get_user_by_id(conn, user_id)
                return _require_loaded_user(created_user)
            except sqlite3.IntegrityError as e:
                msg = f"User already exists: {e}"
                raise ValueError(msg) from e
            finally:
                conn.close()

    def get_user(
        self,
        user_id: int | None = None,
        username: str | None = None,
        oidc_subject: str | None = None,
    ) -> dict[str, Any] | None:
        """Get a user by id, username, or oidc_subject. Returns None if not found."""
        conn = self._connect()
        try:
            if user_id is not None:
                return self._get_user_by_id(conn, user_id)
            if username is not None:
                row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            elif oidc_subject is not None:
                row = conn.execute(
                    "SELECT * FROM users WHERE oidc_subject = ?", (oidc_subject,)
                ).fetchone()
            else:
                return None
            return dict(row) if row else None
        finally:
            conn.close()

    def _get_user_by_id(self, conn: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    _ALLOWED_UPDATE_COLUMNS: ClassVar[frozenset[str]] = frozenset(
        {
            "username",
            "email",
            "identity_email",
            "display_name",
            "password_hash",
            "oidc_subject",
            "auth_source",
            "role",
            "is_active",
            "library_capability",
        }
    )
    _USER_UPDATE_STATEMENTS: ClassVar[dict[str, str]] = {
        "username": "UPDATE users SET username = ? WHERE id = ?",
        "email": "UPDATE users SET email = ? WHERE id = ?",
        "identity_email": "UPDATE users SET identity_email = ? WHERE id = ?",
        "display_name": "UPDATE users SET display_name = ? WHERE id = ?",
        "password_hash": "UPDATE users SET password_hash = ? WHERE id = ?",
        "oidc_subject": "UPDATE users SET oidc_subject = ? WHERE id = ?",
        "auth_source": "UPDATE users SET auth_source = ? WHERE id = ?",
        "role": "UPDATE users SET role = ? WHERE id = ?",
        "is_active": "UPDATE users SET is_active = ? WHERE id = ?",
        "library_capability": "UPDATE users SET library_capability = ? WHERE id = ?",
    }

    def update_user(self, user_id: int, **kwargs: object) -> None:
        """Update user fields. Raises ValueError if user not found or invalid column."""
        if not kwargs:
            return
        for k in kwargs:
            if k not in self._ALLOWED_UPDATE_COLUMNS:
                msg = f"Invalid column: {k}"
                raise ValueError(msg)
        if "auth_source" in kwargs and kwargs["auth_source"] not in self._VALID_AUTH_SOURCES:
            msg = f"Invalid auth_source: {kwargs['auth_source']}"
            raise ValueError(msg)
        if (
            "library_capability" in kwargs
            and kwargs["library_capability"] not in self._VALID_LIBRARY_CAPABILITIES
        ):
            msg = f"Invalid library_capability: {kwargs['library_capability']}"
            raise ValueError(msg)
        with self._lock:
            conn = self._connect()
            try:
                # Verify user exists
                if not self._get_user_by_id(conn, user_id):
                    msg = f"User {user_id} not found"
                    raise ValueError(msg)
                for column, value in kwargs.items():
                    conn.execute(self._USER_UPDATE_STATEMENTS[column], (value, user_id))
                conn.commit()
            finally:
                conn.close()

    def delete_user(self, user_id: int) -> None:
        """Delete a user and their settings."""
        with self._lock:
            conn = self._connect()
            try:
                request_rows = conn.execute(
                    "SELECT id FROM download_requests WHERE user_id = ?",
                    (user_id,),
                ).fetchall()
                request_item_keys = [f"request:{row['id']}" for row in request_rows]
                if request_item_keys:
                    conn.executemany(
                        "DELETE FROM activity_view_state WHERE item_type = 'request' AND item_key = ?",
                        [(item_key,) for item_key in request_item_keys],
                    )
                conn.execute(
                    "DELETE FROM activity_view_state WHERE viewer_scope = ?",
                    (user_viewer_scope(user_id),),
                )
                conn.execute(
                    "UPDATE download_requests SET reviewed_by = NULL WHERE reviewed_by = ?",
                    (user_id,),
                )
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
                conn.commit()
            finally:
                conn.close()

    def list_users(self) -> list[dict[str, Any]]:
        """List all users."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def has_admin_with_password(self) -> bool:
        """Return True when at least one admin user with a password hash exists."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM users WHERE role = 'admin'"
                " AND password_hash IS NOT NULL AND password_hash != ''"
                " LIMIT 1",
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_personal_preferences(self, user_id: int) -> dict[str, Any]:
        """Return the explicit personal preferences for one user."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT kindle_address, notifications_enabled, notification_transport, "
                "notification_destination FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if row:
                preferences = dict(row)
                preferences["notifications_enabled"] = bool(preferences["notifications_enabled"])
                return preferences
            return {
                "kindle_address": None,
                "notifications_enabled": False,
                "notification_transport": None,
                "notification_destination": None,
            }
        finally:
            conn.close()

    def update_personal_preferences(self, user_id: int, **preferences: object) -> None:
        """Update only the supported personal preference fields."""
        allowed = {
            "kindle_address",
            "notifications_enabled",
            "notification_transport",
            "notification_destination",
        }
        if not set(preferences).issubset(allowed):
            msg = "Invalid personal preference"
            raise ValueError(msg)
        if not preferences:
            return
        with self._lock:
            conn = self._connect()
            try:
                if not self._get_user_by_id(conn, user_id):
                    msg = f"User {user_id} not found"
                    raise ValueError(msg)
                existing = self.get_personal_preferences(user_id)
                existing.update(preferences)
                conn.execute(
                    """INSERT INTO user_preferences (
                        user_id, kindle_address, notifications_enabled,
                        notification_transport, notification_destination
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        kindle_address = excluded.kindle_address,
                        notifications_enabled = excluded.notifications_enabled,
                        notification_transport = excluded.notification_transport,
                        notification_destination = excluded.notification_destination""",
                    (
                        user_id,
                        existing["kindle_address"],
                        bool(existing["notifications_enabled"]),
                        existing["notification_transport"],
                        existing["notification_destination"],
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def create_library_request(
        self, *, user_id: int, book_id: int, note: str | None = None
    ) -> dict[str, Any]:
        """Create one pending request for a member's Book without title matching.

        Membership, global File availability, and duplicate detection share the
        insert transaction so a Request cannot race a completed File into
        existence.
        """
        with self._lock:
            conn = self._connect()
            try:
                book = conn.execute(
                    "SELECT id, title, author FROM books WHERE id = ?", (book_id,)
                ).fetchone()
                if book is None:
                    raise ValueError("Book not found")
                member = conn.execute(
                    "SELECT 1 FROM user_library WHERE user_id = ? AND book_id = ?",
                    (user_id, book_id),
                ).fetchone()
                if member is None:
                    raise ValueError("Book is not in the user's library")
                has_files = conn.execute(
                    """
                    SELECT 1 FROM download_history
                    WHERE book_id = ? AND final_status = 'complete' AND download_path IS NOT NULL
                    LIMIT 1
                    """,
                    (book_id,),
                ).fetchone()
                if has_files is not None:
                    raise ValueError("Book already has completed Files")
                duplicate = conn.execute(
                    """
                    SELECT 1 FROM download_requests
                    WHERE user_id = ? AND book_id = ? AND status = 'pending'
                    LIMIT 1
                    """,
                    (user_id, book_id),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError("Duplicate pending Request exists for this Book")
                cursor = conn.execute(
                    "INSERT INTO download_requests (user_id, book_id, note) VALUES (?, ?, ?)",
                    (user_id, book_id, note),
                )
                created = dict(
                    conn.execute(
                        "SELECT * FROM download_requests WHERE id = ?", (cursor.lastrowid,)
                    ).fetchone()
                )
                conn.commit()
                return created
            finally:
                conn.close()

    def fulfil_pending_book_requests(
        self, *, book_id: int, reviewed_by: int | None = None
    ) -> list[dict[str, Any]]:
        """Fulfil and link every pending Request for a Book with its Files."""
        with self._lock:
            conn = self._connect()
            try:
                pending = conn.execute(
                    "SELECT * FROM download_requests WHERE book_id = ? AND status = 'pending'",
                    (book_id,),
                ).fetchall()
                file_rows = conn.execute(
                    """
                    SELECT id FROM download_history
                    WHERE book_id = ? AND final_status = 'complete' AND download_path IS NOT NULL
                    """,
                    (book_id,),
                ).fetchall()
                if not pending or not file_rows:
                    return []
                now = datetime.now(UTC).isoformat(timespec="seconds")
                conn.executemany(
                    "INSERT OR IGNORE INTO user_downloads (user_id, history_id, added_at) VALUES (?, ?, ?)",
                    [
                        (int(request["user_id"]), int(file_row["id"]), now)
                        for request in pending
                        for file_row in file_rows
                    ],
                )
                request_ids = [int(request["id"]) for request in pending]
                conn.execute(
                    """
                    UPDATE download_requests
                    SET status = 'fulfilled', reviewed_by = COALESCE(?, reviewed_by),
                        reviewed_at = ?
                    WHERE book_id = ? AND status = 'pending'
                    """,
                    (reviewed_by, now, book_id),
                )
                rows = conn.execute(
                    "SELECT * FROM download_requests WHERE id IN (SELECT value FROM json_each(?))",
                    (json.dumps(request_ids),),
                ).fetchall()
                conn.commit()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def list_pending_book_requests(self, book_id: int) -> list[dict[str, Any]]:
        """Return pending Requests identified by one canonical Book."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM download_requests
                WHERE book_id = ? AND status = 'pending'
                ORDER BY created_at ASC, id ASC
                """,
                (book_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_request(self, request_id: int) -> dict[str, Any] | None:
        """Get a request row by ID."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM download_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_book_notification_context(self, book_id: int) -> dict[str, Any] | None:
        """Return the canonical Book projection used by notifications.

        Includes the rich Hardcover snapshot (cover, subtitle, series, year,
        ISBN, description/display fields) so notification emails can render a
        book card without a live metadata fetch. ``metadata_json`` is parsed
        from its stored JSON text.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT id, metadata_provider, provider_book_id, title, author,
                       subtitle, publish_year, isbn_13, cover_url, series_name,
                       series_position, language, metadata_json
                FROM books WHERE id = ?
                """,
                (book_id,),
            ).fetchone()
            if row is None:
                return None
            book = dict(row)
            raw_metadata = book.get("metadata_json")
            if isinstance(raw_metadata, str):
                try:
                    parsed = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    parsed = {}
                book["metadata_json"] = parsed if isinstance(parsed, dict) else {}
            return book
        finally:
            conn.close()

    def list_requests(
        self,
        *,
        user_id: int | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        include_book_details: bool = False,
    ) -> list[dict[str, Any]]:
        """List requests with optional user/status filters."""
        where_clauses: list[str] = []
        params: list[Any] = []

        if user_id is not None:
            where_clauses.append("user_id = ?")
            params.append(user_id)

        if status is not None:
            where_clauses.append("status = ?")
            params.append(normalize_request_status(status))

        query = "SELECT * FROM download_requests"
        if include_book_details:
            query = """
                SELECT download_requests.*, books.title AS book_title,
                       books.author AS book_author, books.cover_url AS book_cover_url,
                       books.metadata_provider, books.provider_book_id
                FROM download_requests
                LEFT JOIN books ON books.id = download_requests.book_id
            """
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
            return [dict(row) for row in rows]
        finally:
            conn.close()

    _ALLOWED_REQUEST_UPDATE_COLUMNS: ClassVar[frozenset[str]] = frozenset(
        {
            "status",
            "note",
            "admin_note",
            "reviewed_by",
            "reviewed_at",
        }
    )
    _REQUEST_UPDATE_STATEMENTS: ClassVar[dict[str, str]] = {
        "status": "UPDATE download_requests SET status = ? WHERE id = ?",
        "note": "UPDATE download_requests SET note = ? WHERE id = ?",
        "admin_note": "UPDATE download_requests SET admin_note = ? WHERE id = ?",
        "reviewed_by": "UPDATE download_requests SET reviewed_by = ? WHERE id = ?",
        "reviewed_at": "UPDATE download_requests SET reviewed_at = ? WHERE id = ?",
    }

    def update_request(
        self,
        request_id: int,
        expected_current_status: str | None = None,
        **kwargs: object,
    ) -> dict[str, Any]:
        """Update request fields and return the updated record."""
        if not kwargs:
            request = self.get_request(request_id)
            if request is None:
                msg = f"Request {request_id} not found"
                raise ValueError(msg)
            if expected_current_status is not None:
                normalized_expected_status = normalize_request_status(expected_current_status)
                if request["status"] != normalized_expected_status:
                    msg = "Request state changed before update"
                    raise ValueError(msg)
            return request

        for key in kwargs:
            if key not in self._ALLOWED_REQUEST_UPDATE_COLUMNS:
                msg = f"Invalid request column: {key}"
                raise ValueError(msg)

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                current = dict(row) if row else None
                if current is None:
                    msg = f"Request {request_id} not found"
                    raise ValueError(msg)

                if expected_current_status is not None:
                    normalized_expected_status = normalize_request_status(expected_current_status)
                    if current["status"] != normalized_expected_status:
                        msg = "Request state changed before update"
                        raise ValueError(msg)

                updates = dict(kwargs)

                if "status" in updates:
                    _, normalized_status = validate_status_transition(
                        current["status"],
                        updates["status"],
                    )
                    updates["status"] = normalized_status

                for column, value in updates.items():
                    conn.execute(self._REQUEST_UPDATE_STATEMENTS[column], (value, request_id))
                conn.commit()

                updated_row = conn.execute(
                    "SELECT * FROM download_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                parsed = dict(updated_row) if updated_row else None
                if parsed is None:
                    msg = f"Request {request_id} not found after update"
                    raise ValueError(msg)
                return parsed
            finally:
                conn.close()
