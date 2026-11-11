"""Helpers for provisioning and syncing Calibre-Web users into users.db."""

from __future__ import annotations

import os
import sqlite3
from typing import TYPE_CHECKING, Any

from shelfmark.core.auth_modes import AUTH_SOURCE_CWA, normalize_auth_source
from shelfmark.core.external_user_linking import upsert_external_user
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_CWA_ALIAS_SUFFIX = "__cwa"


def connect_cwa_db(db_path: str | os.PathLike[str] | Path) -> sqlite3.Connection:
    """Open Calibre-Web's app.db read-only, including writes still in its WAL.

    Calibre-Web Automated keeps app.db in WAL mode, so a password change or a
    renamed user can sit in app.db-wal until the next checkpoint. ``immutable=1``
    makes SQLite skip the WAL entirely, so it would read the old password hash.
    A plain ``mode=ro`` open reads the WAL. ``immutable=1`` is only used as a
    fallback when that open fails, for example on a read-only mount where
    SQLite cannot use the -shm file.
    """
    uri = f"file:{os.fspath(db_path)}?mode=ro"
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.execute("PRAGMA schema_version").fetchone()
    except sqlite3.OperationalError as exc:
        if conn is not None:
            conn.close()
        logger.warning(
            "CWA database %s cannot be read in place (%s); using an immutable snapshot, "
            "which misses account changes CWA has not checkpointed yet. Mount the folder "
            "that holds app.db, not the file alone, so SQLite can read app.db-wal.",
            db_path,
            exc,
        )
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
