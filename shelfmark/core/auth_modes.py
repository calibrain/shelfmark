"""Authentication mode, auth-source normalization, and admin access policy helpers."""

import os
from typing import Any, Mapping

AUTH_SOURCE_BUILTIN = "builtin"
AUTH_SOURCE_OIDC = "oidc"
AUTH_SOURCE_PROXY = "proxy"
AUTH_SOURCE_CWA = "cwa"
AUTH_SOURCES = (
    AUTH_SOURCE_BUILTIN,
    AUTH_SOURCE_OIDC,
    AUTH_SOURCE_PROXY,
    AUTH_SOURCE_CWA,
)
AUTH_SOURCE_SET = frozenset(AUTH_SOURCES)
_ALWAYS_ADMIN_SETTINGS_TABS = frozenset({"security", "users"})


def has_local_password_admin(user_db: Any | None = None) -> bool:
    """Return True when at least one local admin with a password exists."""
    try:
        db = user_db
        if db is None:
            from shelfmark.core.user_db import UserDB

            config_root = os.environ.get("CONFIG_DIR", "/config")
            db = UserDB(os.path.join(config_root, "users.db"))
            db.initialize()

        return any(
            user.get("password_hash") and user.get("role") == "admin"
            for user in db.list_users()
        )
    except Exception:
        return False


def normalize_auth_source(
    source: Any,
    oidc_subject: Any = None,
) -> str:
    """Resolve a stable auth source value from persisted fields."""
    normalized = str(source or "").strip().lower()
    if normalized in AUTH_SOURCE_SET:
        return normalized
    if oidc_subject:
        return AUTH_SOURCE_OIDC
    return AUTH_SOURCE_BUILTIN


def determine_auth_mode(
    security_config: Mapping[str, Any],
    cwa_db_path: Any | None,
    *,
    has_local_admin: bool = True,
) -> str:
    """Determine active auth mode from security config and runtime prerequisites."""
    auth_mode = security_config.get("AUTH_METHOD", "none")

    if auth_mode == AUTH_SOURCE_CWA and cwa_db_path:
        return AUTH_SOURCE_CWA

    if auth_mode == AUTH_SOURCE_BUILTIN and has_local_admin:
        return AUTH_SOURCE_BUILTIN

    if auth_mode == AUTH_SOURCE_PROXY and security_config.get("PROXY_AUTH_USER_HEADER"):
        return AUTH_SOURCE_PROXY

    if (
        auth_mode == AUTH_SOURCE_OIDC
        and has_local_admin
        and security_config.get("OIDC_DISCOVERY_URL")
        and security_config.get("OIDC_CLIENT_ID")
    ):
        return AUTH_SOURCE_OIDC

    return "none"


def is_settings_or_onboarding_path(path: str) -> bool:
    """Return True when request path targets protected admin settings routes."""
    return path.startswith("/api/settings") or path.startswith("/api/onboarding")


def get_settings_tab_from_path(path: str) -> str | None:
    """Extract tab name from /api/settings/<tab>[...] paths."""
    if not path.startswith("/api/settings/"):
        return None

    suffix = path[len("/api/settings/"):]
    if not suffix:
        return None

    return suffix.split("/", 1)[0] or None


def should_restrict_settings_to_admin(
    _users_config: Mapping[str, Any],
) -> bool:
    """Settings/onboarding is always admin-only."""
    return True


def requires_admin_for_settings_access(
    path: str,
    users_config: Mapping[str, Any],
) -> bool:
    """Return whether this settings/onboarding request requires admin privileges."""
    tab_name = get_settings_tab_from_path(path)
    if tab_name in _ALWAYS_ADMIN_SETTINGS_TABS:
        return True

    return should_restrict_settings_to_admin(users_config)


def get_auth_check_admin_status(
    _auth_mode: str,
    _users_config: Mapping[str, Any],
    session_data: Mapping[str, Any],
) -> bool:
    """Resolve /api/auth/check `is_admin` as the session's real admin role."""
    if "user_id" not in session_data:
        return False

    return bool(session_data.get("is_admin", False))
