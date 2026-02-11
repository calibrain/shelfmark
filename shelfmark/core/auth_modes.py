"""Authentication mode and admin access policy helpers."""

from typing import Any, Mapping


def determine_auth_mode(
    security_config: Mapping[str, Any],
    cwa_db_path: Any | None,
) -> str:
    """Determine active auth mode from security config and runtime prerequisites."""
    auth_mode = security_config.get("AUTH_METHOD", "none")

    if auth_mode == "cwa" and cwa_db_path:
        return "cwa"

    if auth_mode == "builtin":
        return "builtin"

    if auth_mode == "proxy" and security_config.get("PROXY_AUTH_USER_HEADER"):
        return "proxy"

    if (
        auth_mode == "oidc"
        and security_config.get("OIDC_DISCOVERY_URL")
        and security_config.get("OIDC_CLIENT_ID")
    ):
        return "oidc"

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
    if auth_mode == "builtin":
        # Builtin multi-user sessions include db_user_id.
        return "db_user_id" in session_data

    if auth_mode == "proxy":
        return security_config.get("PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN", False)

    if auth_mode in ("cwa", "oidc"):
        return True

    return False


def get_auth_check_admin_status(
    auth_mode: str,
    security_config: Mapping[str, Any],
    session_data: Mapping[str, Any],
) -> bool:
    """Resolve admin status for /api/auth/check response."""
    if auth_mode == "builtin":
        return session_data.get("is_admin", True)

    if auth_mode == "cwa":
        restrict_to_admin = security_config.get("CWA_RESTRICT_SETTINGS_TO_ADMIN", False)
        if restrict_to_admin:
            return session_data.get("is_admin", False)
        return True

    if auth_mode == "proxy":
        restrict_to_admin = security_config.get("PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN", False)
        return session_data.get("is_admin", not restrict_to_admin)

    if auth_mode == "oidc":
        return session_data.get("is_admin", False)

    return False
