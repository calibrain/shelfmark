"""Operational handlers for security settings (save/actions)."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shelfmark.core.user_db import UserDB
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify

if TYPE_CHECKING:
    from collections.abc import Callable

_OIDC_LOCKOUT_MESSAGE = "A local admin account with a password is required before enabling OIDC. Use the 'Go to Users' button above to create one. This ensures you can still sign in if your identity provider is unavailable."
_OIDC_REQUIRED_FIELDS = (
    ("OIDC_DISCOVERY_URL", "Discovery URL"),
    ("OIDC_CLIENT_ID", "Client ID"),
    ("OIDC_CLIENT_SECRET", "Client Secret"),
)


def _has_local_password_admin() -> bool:
    root = os.environ.get("CONFIG_DIR", "/config")
    user_db = UserDB(str(Path(root) / "users.db"))
    user_db.initialize()
    return any(
        user.get("password_hash") and user.get("role") == "admin" for user in user_db.list_users()
    )


def _load_effective_security_values(values: dict[str, Any]) -> dict[str, Any]:
    """Merge the current save payload onto the persisted security config."""
    from shelfmark.core.settings_registry import load_config_file

    effective_values = load_config_file("security")
    effective_values.update(values)
    return effective_values


def _get_missing_oidc_required_fields(effective_values: dict[str, Any]) -> list[str]:
    """Return missing required OIDC field labels from the effective config."""
    missing_fields: list[str] = []

    for key, label in _OIDC_REQUIRED_FIELDS:
        value = effective_values.get(key)
        if value is None:
            missing_fields.append(label)
            continue
        if isinstance(value, str) and not value.strip():
            missing_fields.append(label)

    return missing_fields


def on_save_security(
    values: dict[str, Any],
) -> dict[str, Any]:
    """Validate security values before persistence."""
    normalized_values = values.copy()

    discovery_url = normalized_values.get("OIDC_DISCOVERY_URL")
    if discovery_url is not None:
        normalized_values["OIDC_DISCOVERY_URL"] = normalize_http_url(
            str(discovery_url),
            default_scheme="https",
            strip_trailing_slash=False,
        )

    proxy_logout_url = normalized_values.get("PROXY_AUTH_LOGOUT_URL")
    if proxy_logout_url is not None:
        normalized_values["PROXY_AUTH_LOGOUT_URL"] = normalize_http_url(
            str(proxy_logout_url),
            default_scheme="https",
            strip_trailing_slash=False,
        )

    effective_values = _load_effective_security_values(normalized_values)
    auth_method = str(effective_values.get("AUTH_METHOD", "") or "").strip().lower()

    if auth_method == "oidc":
        if not _has_local_password_admin():
            return {"error": True, "message": _OIDC_LOCKOUT_MESSAGE, "values": normalized_values}

        missing_fields = _get_missing_oidc_required_fields(effective_values)
        if missing_fields:
            missing_fields_text = ", ".join(missing_fields)
            return {
                "error": True,
                "message": f"OIDC configuration is incomplete: missing {missing_fields_text}.",
                "values": normalized_values,
            }

    return {"error": False, "values": normalized_values}


def check_oidc_connection(
    *,
    load_security_config: Callable[[], dict[str, Any]],
    current_values: dict[str, Any] | None = None,
    logger: Any,
) -> dict[str, Any]:
    """Fetch and validate the configured OIDC discovery document."""
    import requests

    try:
        # Prefer the current (unsaved) form value over the saved config
        discovery_url = (current_values or {}).get(
            "OIDC_DISCOVERY_URL"
        ) or load_security_config().get("OIDC_DISCOVERY_URL", "")
        if not discovery_url:
            return {"success": False, "message": "Discovery URL is not configured."}

        response = requests.get(discovery_url, timeout=10, verify=get_ssl_verify(discovery_url))
        response.raise_for_status()
        document = response.json()

        required_fields = ["issuer", "authorization_endpoint", "token_endpoint"]
        missing_fields = [field for field in required_fields if field not in document]
        if missing_fields:
            return {
                "success": False,
                "message": f"Discovery document missing fields: {', '.join(missing_fields)}",
            }

        return {"success": True, "message": f"Connected to {document['issuer']}"}
    except Exception as exc:
        logger.exception("OIDC connection test failed")
        return {"success": False, "message": f"Connection failed: {exc!s}"}
