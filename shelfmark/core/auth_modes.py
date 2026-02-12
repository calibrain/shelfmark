"""Authentication mode, auth-source normalization, and admin access policy helpers."""

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
) -> str:
    """Determine active auth mode from security config and runtime prerequisites."""
    auth_mode = security_config.get("AUTH_METHOD", "none")

    if auth_mode == AUTH_SOURCE_CWA and cwa_db_path:
        return AUTH_SOURCE_CWA

    if auth_mode == AUTH_SOURCE_BUILTIN:
        return AUTH_SOURCE_BUILTIN

    if auth_mode == AUTH_SOURCE_PROXY and security_config.get("PROXY_AUTH_USER_HEADER"):
        return AUTH_SOURCE_PROXY

    if (
        auth_mode == AUTH_SOURCE_OIDC
        and security_config.get("OIDC_DISCOVERY_URL")
        and security_config.get("OIDC_CLIENT_ID")
    ):
        return AUTH_SOURCE_OIDC

    return "none"


def is_settings_or_onboarding_path(path: str) -> bool:
    """Return True when request path targets protected admin settings routes."""
    return path.startswith("/api/settings") or path.startswith("/api/onboarding")


def should_restrict_settings_to_admin(
    auth_mode: str,
    security_config: Mapping[str, Any],
    session_data: Mapping[str, Any],
) -> bool:
    """Return whether settings access must be restricted to admin users."""
    if auth_mode == AUTH_SOURCE_BUILTIN:
        # Builtin multi-user sessions include db_user_id.
        return "db_user_id" in session_data

    if auth_mode == AUTH_SOURCE_PROXY:
        return security_config.get("PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN", False)

    if auth_mode in (AUTH_SOURCE_CWA, AUTH_SOURCE_OIDC):
        return True

    return False


def get_auth_check_admin_status(
    auth_mode: str,
    security_config: Mapping[str, Any],
    session_data: Mapping[str, Any],
) -> bool:
    """Resolve admin status for /api/auth/check response."""
    if auth_mode == AUTH_SOURCE_BUILTIN:
        return session_data.get("is_admin", True)

    if auth_mode == AUTH_SOURCE_CWA:
        restrict_to_admin = security_config.get("CWA_RESTRICT_SETTINGS_TO_ADMIN", False)
        if restrict_to_admin:
            return session_data.get("is_admin", False)
        return True

    if auth_mode == AUTH_SOURCE_PROXY:
        restrict_to_admin = security_config.get("PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN", False)
        return session_data.get("is_admin", not restrict_to_admin)

    if auth_mode == AUTH_SOURCE_OIDC:
        return session_data.get("is_admin", False)

    return False
