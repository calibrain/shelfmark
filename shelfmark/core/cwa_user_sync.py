"""Helpers for provisioning and syncing Calibre-Web users into users.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from shelfmark.core.auth_modes import AUTH_SOURCE_CWA, normalize_auth_source
from shelfmark.core.external_user_linking import upsert_external_user
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    import os
    from collections.abc import Iterable

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_CWA_ALIAS_SUFFIX = "__cwa"

# Logins and user syncs open app.db every time, so the warning about a stale
# read is logged once per process and at debug level after that.
_stale_read_warned = False


def _warn_stale_read(message: str, *args: object) -> None:
    global _stale_read_warned
    log = logger.debug if _stale_read_warned else logger.warning
    _stale_read_warned = True
    log(message, *args)


def _is_mounted_alone(db_path: Path) -> bool:
    """Return True when app.db is bind-mounted as a file without its WAL files.

    A file mount hides the app.db-wal and app.db-shm that CWA writes next to it.
    It shows up as the file sitting on another device than its directory. If
    app.db-wal and app.db-shm were mounted too, they sit on the same device as
    app.db and the WAL can still be read.
    """
    try:
        db_dev = db_path.stat().st_dev
        if db_dev == db_path.parent.stat().st_dev:
            return False
    except OSError:
        return False
    for suffix in ("-wal", "-shm"):
        try:
            if db_path.with_name(db_path.name + suffix).stat().st_dev != db_dev:
                return True
        except OSError:
            return True
    return False


def connect_cwa_db(db_path: str | os.PathLike[str] | Path) -> sqlite3.Connection:
    """Open Calibre-Web's app.db read-only, including writes still in its WAL.

    Calibre-Web Automated keeps app.db in WAL mode, so a password change or a
    renamed user can sit in app.db-wal until the next checkpoint. ``immutable=1``
    makes SQLite skip the WAL entirely, so it would read the old password hash.
    A plain ``mode=ro`` open reads the WAL when the folder that holds app.db is
    mounted, read-only or not.

    ``immutable=1`` is still used when the WAL cannot be reached:

    - app.db is mounted as a file without its WAL files. SQLite cannot see
      CWA's app.db-wal, and a plain open would create an empty one of its own
      and read the same stale data, so this logs a warning telling the user to
      mount the folder.
    - the plain open fails. On a read-only folder mount this happens when CWA
      is stopped and has removed app.db-wal and app.db-shm; nothing is waiting
      in the WAL then, so the snapshot is current.
    """
    path = Path(db_path)
    # Escape the path so characters such as "#" or "?" stay part of the filename.
    uri = f"file:{quote(str(path))}?mode=ro"

    if _is_mounted_alone(path):
        _warn_stale_read(
            "CWA database %s is mounted as a single file, so Shelfmark cannot see "
            "app.db-wal and misses account changes CWA has not checkpointed yet "
            "(a new password, a renamed user). Mount the folder that holds app.db "
            "to /auth instead.",
            path,
        )
        return sqlite3.connect(f"{uri}&immutable=1", uri=True)

    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA schema_version").fetchone()
    except sqlite3.OperationalError as exc:
        if conn is not None:
            conn.close()
        if path.with_name(f"{path.name}-wal").exists():
            _warn_stale_read(
                "CWA database %s cannot be read in place (%s); using an immutable "
                "snapshot, which misses account changes still in app.db-wal.",
                path,
                exc,
            )
        else:
            logger.debug("CWA database %s opened as an immutable snapshot (%s)", path, exc)
        conn = sqlite3.connect(f"{uri}&immutable=1", uri=True)
    return conn


def _normalize_email(value: object) -> str | None:
    if value is None:
        return None
    email = str(value).strip()
    return email or None


def upsert_cwa_user(
    user_db: UserDB,
    cwa_username: str,
    cwa_email: str | None,
    role: str,
    context: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Create/update a CWA-backed user with collision-safe matching."""
    normalized_email = _normalize_email(cwa_email)
    collision_strategy = "alias" if normalized_email else "takeover"
    user, action = upsert_external_user(
        user_db,
        auth_source="cwa",
        username=cwa_username,
        email=normalized_email,
        role=role,
        allow_email_link=True,
        sync_username=True,
        collision_strategy=collision_strategy,
        alias_suffix=_CWA_ALIAS_SUFFIX,
        context=context,
    )
    if user is None:
        msg = "Unexpected CWA user sync result: no user returned"
        raise RuntimeError(msg)
    return user, action


def sync_cwa_users_from_rows(
    user_db: UserDB,
    rows: Iterable[tuple[Any, Any, Any]],
) -> dict[str, int]:
    """Sync CWA users from raw `(name, role_flags, email)` rows."""
    created = 0
    updated = 0
    active_cwa_user_ids: set[int] = set()
    for username, role_flags, email in rows:
        normalized_username = str(username or "").strip()
        if not normalized_username:
            continue

        role = "admin" if (int(role_flags or 0) & 1) == 1 else "user"
        user, action = upsert_cwa_user(
            user_db,
            cwa_username=normalized_username,
            cwa_email=_normalize_email(email),
            role=role,
            context="cwa_manual_sync",
        )
        active_cwa_user_ids.add(int(user["id"]))
        if action == "created":
            created += 1
        else:
            updated += 1

    deleted = 0
    for existing_user in user_db.list_users():
        if (
            normalize_auth_source(
                existing_user.get("auth_source"),
                existing_user.get("oidc_subject"),
            )
            != AUTH_SOURCE_CWA
        ):
            continue

        existing_id = int(existing_user.get("id") or 0)
        if existing_id in active_cwa_user_ids:
            continue

        user_db.delete_user(existing_id)
        deleted += 1

    return {
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "total": created + updated,
    }
