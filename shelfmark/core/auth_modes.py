"""Authentication mode, auth-source normalization, and admin access policy helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = setup_logger(__name__)

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
AUTH_MODE_NONE = "none"
# Resolved when AUTH_METHOD is unrecognized or unreadable. It is not "none", so
# every guard still requires a session, and no login flow accepts it.
AUTH_MODE_UNAVAILABLE = "unavailable"
_ALWAYS_ADMIN_SETTINGS_TABS = frozenset({"security", "users"})


def normalize_auth_source(
    source: object,
    oidc_subject: object = None,
) -> str:
    """Resolve a stable auth source value from persisted fields."""
    normalized = str(source or "").strip().lower()
    if normalized in AUTH_SOURCE_SET:
        return normalized
    if oidc_subject:
        return AUTH_SOURCE_OIDC
    return AUTH_SOURCE_BUILTIN


def determine_auth_mode(auth_method: object) -> str:
    """Resolve the active auth mode from the configured AUTH_METHOD.

    Only an explicit "none" disables authentication. A configured method stays
    active even when its prerequisites (a local admin, OIDC settings, the CWA
    database, a proxy header) are missing, so sign-in fails instead of the
    instance silently opening up to anonymous admin access.
    """
    normalized = str(auth_method or "").strip().lower() or AUTH_MODE_NONE
    if normalized == AUTH_MODE_NONE or normalized in AUTH_SOURCE_SET:
        return normalized
    return AUTH_MODE_UNAVAILABLE


def load_active_auth_mode() -> str:
    """Resolve the active auth mode from the current security config."""
    try:
        from shelfmark.core.config import config as app_config

        auth_method = app_config.get("AUTH_METHOD", AUTH_MODE_NONE)
    except ImportError, OSError, RuntimeError, TypeError, ValueError:
        logger.exception("Could not read AUTH_METHOD; denying access until it can be read")
        return AUTH_MODE_UNAVAILABLE

    auth_mode = determine_auth_mode(auth_method)
    if auth_mode == AUTH_MODE_UNAVAILABLE:
        logger.error(
            "Unrecognized AUTH_METHOD %r; denying access. Use one of: none, %s",
            auth_method,
            ", ".join(AUTH_SOURCES),
        )
    return auth_mode


def is_user_active_for_auth_mode(user: Mapping[str, Any], auth_mode: str) -> bool:
    """Return whether a user can authenticate under the current auth mode."""
    source = normalize_auth_source(user.get("auth_source"), user.get("oidc_subject"))
    if source == AUTH_SOURCE_BUILTIN:
        return auth_mode in (AUTH_SOURCE_BUILTIN, AUTH_SOURCE_OIDC)
    return source == auth_mode


def is_settings_or_onboarding_path(path: str) -> bool:
    """Return True when request path targets protected admin settings routes."""
    return path.startswith(("/api/settings", "/api/onboarding"))


def get_settings_tab_from_path(path: str) -> str | None:
    """Extract tab name from /api/settings/<tab>[...] paths."""
    if not path.startswith("/api/settings/"):
        return None

    suffix = path[len("/api/settings/") :]
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
