"""Helpers for provisioning and syncing Calibre-Web users into users.db."""

from __future__ import annotations

from typing import Any, Iterable

from shelfmark.core.auth_modes import AUTH_SOURCE_CWA, normalize_auth_source
from shelfmark.core.external_user_linking import upsert_external_user
from shelfmark.core.user_db import UserDB

_CWA_ALIAS_SUFFIX = "__cwa"


def _normalize_email(value: Any) -> str | None:
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
        collision_strategy=collision_strategy,
        alias_suffix=_CWA_ALIAS_SUFFIX,
        context=context,
    )
    if user is None:
        raise RuntimeError("Unexpected CWA user sync result: no user returned")
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
        if normalize_auth_source(
            existing_user.get("auth_source"),
            existing_user.get("oidc_subject"),
        ) != AUTH_SOURCE_CWA:
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
