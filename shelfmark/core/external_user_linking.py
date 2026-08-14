"""Shared external identity matching and provisioning helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

from shelfmark.core.auth_modes import normalize_auth_source
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from shelfmark.core.user_db import UserDB

UNSET = object()

CollisionStrategy = Literal["takeover", "suffix", "alias"]
MatchReason = Literal[
    "subject_match",
    "existing_source_username_match",
    "unique_email_match",
]

logger = setup_logger(__name__)


def _normalize_username(value: object) -> str:
    return str(value or "").strip()


def _normalize_email(value: object) -> str | None:
    if value is None:
        return None
    email = str(value).strip()
    return email or None


def _normalize_display_name(value: object) -> str | None:
    if value is None:
        return None
    name = str(value).strip()
    return name or None


def _email_key(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_role(value: object) -> str:
    return "admin" if str(value or "").strip().lower() == "admin" else "user"


def _get_by_subject(
    user_db: UserDB, subject_field: str | None, subject: str | None
) -> dict[str, Any] | None:
    if not subject_field or not subject:
        return None
    if subject_field == "oidc_subject":
        return user_db.get_user(oidc_subject=subject)
    return None


def find_unique_user_by_email(user_db: UserDB, email: str | None) -> dict[str, Any] | None:
    """Return the unique local user matching an email address, if any."""
    key = _email_key(_normalize_email(email))
    if not key:
        return None

    matches = [u for u in user_db.list_users() if _email_key(u.get("email")) == key]
    return matches[0] if len(matches) == 1 else None


def find_external_user_match(
    user_db: UserDB,
    *,
    auth_source: str,
    username: str,
    email: str | None,
    subject_field: str | None = None,
    subject: str | None = None,
    allow_email_link: bool = False,
) -> tuple[dict[str, Any] | None, MatchReason | None]:
    """Find an existing local user that should be linked to an external identity."""
    normalized_username = _normalize_username(username)
    normalized_email = _normalize_email(email)

    by_subject = _get_by_subject(user_db, subject_field, subject)
    if by_subject is not None:
        return by_subject, "subject_match"

    by_username = user_db.get_user(username=normalized_username)
    if (
        by_username
        and normalize_auth_source(
            by_username.get("auth_source"),
            by_username.get("oidc_subject"),
        )
        == auth_source
    ):
        return by_username, "existing_source_username_match"

    if allow_email_link:
        return find_unique_user_by_email(user_db, normalized_email), "unique_email_match"
    return None, None


def _build_updates(
    *,
    auth_source: str,
    role: str,
    sync_role: bool,
    username: str | object,
    email: str | None | object,
    display_name: str | None | object,
    subject_field: str | None,
    subject: str | None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {"auth_source": auth_source}
    if sync_role:
        updates["role"] = _normalize_role(role)
    if username is not UNSET:
        updates["username"] = _normalize_username(username)
    if email is not UNSET:
        updates["email"] = _normalize_email(email)
    if display_name is not UNSET:
        updates["display_name"] = _normalize_display_name(display_name)
    if subject_field == "oidc_subject" and subject:
        updates["oidc_subject"] = subject
    return updates


def _next_suffix_username(
    user_db: UserDB,
    base_username: str,
    *,
    exclude_user_id: int | None = None,
) -> str:
    candidate = base_username
    suffix = 1
    while existing := user_db.get_user(username=candidate):
        if exclude_user_id is not None and int(existing.get("id") or 0) == exclude_user_id:
            return candidate
        candidate = f"{base_username}_{suffix}"
        suffix += 1
    return candidate


def _find_existing_alias_user(
    user_db: UserDB,
    *,
    auth_source: str,
    alias_base: str,
) -> dict[str, Any] | None:
    pattern = re.compile(rf"^{re.escape(alias_base)}(?:_\d+)?$")
    candidates = [
        user
        for user in user_db.list_users()
        if pattern.match(str(user.get("username") or ""))
        and normalize_auth_source(user.get("auth_source"), user.get("oidc_subject")) == auth_source
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda user: int(user.get("id") or 0), default=None)


def _resolve_create_username(
    user_db: UserDB,
    *,
    auth_source: str,
    requested_username: str,
    strategy: CollisionStrategy,
    alias_suffix: str,
) -> tuple[str | None, dict[str, Any] | None, str]:
    existing = user_db.get_user(username=requested_username)
    if not existing:
        return requested_username, None, "new_username_available"

    if strategy == "takeover":
        return None, existing, "username_collision_takeover"

    if strategy == "suffix":
        return (
            _next_suffix_username(user_db, requested_username),
            None,
            "username_collision_suffix",
        )

    alias_base = f"{requested_username}{alias_suffix}"
    alias_existing = _find_existing_alias_user(
        user_db,
        auth_source=auth_source,
        alias_base=alias_base,
    )
    if alias_existing is not None:
        return None, alias_existing, "reuse_existing_alias"
    return _next_suffix_username(user_db, alias_base), None, "username_collision_alias"


def _resolve_update_username(
    user_db: UserDB,
    *,
    current_user: dict[str, Any],
    requested_username: str,
    strategy: CollisionStrategy,
    alias_suffix: str,
) -> str:
    current_user_id = int(current_user["id"])
    existing = user_db.get_user(username=requested_username)
    if existing is None or int(existing.get("id") or 0) == current_user_id:
        return requested_username

    if strategy == "suffix":
        return _next_suffix_username(
            user_db,
            requested_username,
            exclude_user_id=current_user_id,
        )
    if strategy == "alias":
        return _next_suffix_username(
            user_db,
            f"{requested_username}{alias_suffix}",
            exclude_user_id=current_user_id,
        )

    # `takeover` can select an existing row during creation, but once an
    # identity is already matched it must never replace a different username
    # owner. Preserve the matched row's current collision-free name instead.
    return str(current_user["username"])


def upsert_external_user(
    user_db: UserDB,
    *,
    auth_source: str,
    username: str,
    role: str,
    email: str | None | object = UNSET,
    display_name: str | None | object = UNSET,
    subject_field: str | None = None,
    subject: str | None = None,
    allow_email_link: bool = False,
    sync_role: bool = True,
    sync_username: bool = False,
    allow_create: bool = True,
    collision_strategy: CollisionStrategy = "takeover",
    alias_suffix: str | None = None,
    context: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Create/update a user from an external auth identity.

    Returns `(user, action)` where action is one of:
    - `"updated"`
    - `"created"`
    - `"not_found"` (when `allow_create=False` and no link target exists)
    """
    normalized_username = _normalize_username(username)
    if not normalized_username:
        msg = "External username is required"
        raise ValueError(msg)

    normalized_email = _normalize_email(email) if email is not UNSET else None
    normalized_display_name = (
        _normalize_display_name(display_name) if display_name is not UNSET else None
    )
    normalized_role = _normalize_role(role)

    matched, match_reason = find_external_user_match(
        user_db,
        auth_source=auth_source,
        username=normalized_username,
        email=normalized_email,
        subject_field=subject_field,
        subject=subject,
        allow_email_link=allow_email_link,
    )
    resolved_alias_suffix = alias_suffix or f"__{auth_source}"
    update_username: str | object = UNSET
    if (
        matched is not None
        and sync_username
        and normalize_auth_source(matched.get("auth_source"), matched.get("oidc_subject"))
        == auth_source
    ):
        update_username = _resolve_update_username(
            user_db,
            current_user=matched,
            requested_username=normalized_username,
            strategy=collision_strategy,
            alias_suffix=resolved_alias_suffix,
        )
    updates = _build_updates(
        auth_source=auth_source,
        role=normalized_role,
        sync_role=sync_role,
        username=update_username,
        email=normalized_email if email is not UNSET else UNSET,
        display_name=normalized_display_name if display_name is not UNSET else UNSET,
        subject_field=subject_field,
        subject=subject,
    )
    if matched is not None:
        user_db.update_user(matched["id"], **updates)
        mapped = user_db.get_user(user_id=matched["id"]) or matched
        logger.info(
            "External user mapped to existing Shelfmark user (source=%s, context=%s, reason=%s, external_username=%s, shelfmark_user_id=%s, shelfmark_username=%s)",
            auth_source,
            context or "unspecified",
            match_reason,
            normalized_username,
            mapped["id"],
            mapped["username"],
        )
        return mapped, "updated"

    if not allow_create:
        logger.info(
            "External user could not be mapped and creation is disabled (source=%s, context=%s, external_username=%s)",
            auth_source,
            context or "unspecified",
            normalized_username,
        )
        return None, "not_found"

    create_username, takeover_target, create_reason = _resolve_create_username(
        user_db,
        auth_source=auth_source,
        requested_username=normalized_username,
        strategy=collision_strategy,
        alias_suffix=resolved_alias_suffix,
    )
    if takeover_target is not None:
        user_db.update_user(takeover_target["id"], **updates)
        mapped = user_db.get_user(user_id=takeover_target["id"]) or takeover_target
        logger.info(
            "External user mapped to existing Shelfmark user (source=%s, context=%s, reason=%s, external_username=%s, shelfmark_user_id=%s, shelfmark_username=%s)",
            auth_source,
            context or "unspecified",
            create_reason,
            normalized_username,
            mapped["id"],
            mapped["username"],
        )
        return mapped, "updated"

    create_kwargs: dict[str, Any] = {
        "username": create_username,
        "auth_source": auth_source,
        "role": normalized_role,
    }
    if email is not UNSET:
        create_kwargs["email"] = normalized_email
    if display_name is not UNSET:
        create_kwargs["display_name"] = normalized_display_name
    if subject_field == "oidc_subject" and subject:
        create_kwargs["oidc_subject"] = subject

    created = user_db.create_user(**create_kwargs)
    logger.info(
        "External user created Shelfmark user (source=%s, context=%s, reason=%s, external_username=%s, shelfmark_user_id=%s, shelfmark_username=%s)",
        auth_source,
        context or "unspecified",
        create_reason,
        normalized_username,
        created["id"],
        created["username"],
    )
    return created, "created"
