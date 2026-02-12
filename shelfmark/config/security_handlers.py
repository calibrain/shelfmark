"""Operational handlers for security settings (save/actions)."""

import os
from typing import Any, Callable

from shelfmark.core.user_db import UserDB


_OIDC_LOCKOUT_MESSAGE = "Create a local admin account first (Users tab) before enabling OIDC. This ensures you can still log in with a password if SSO is unavailable."


def _has_local_password_admin() -> bool:
    root = os.environ.get("CONFIG_DIR", "/config")
    user_db = UserDB(os.path.join(root, "users.db"))
    user_db.initialize()
    return any(user.get("password_hash") and user.get("role") == "admin" for user in user_db.list_users())


def on_save_security(
    values: dict[str, Any],
    *,
    load_security_config: Callable[[], dict[str, Any]],
    hash_password: Callable[[str], str],
    sync_builtin_admin_user: Callable[[str, str], None],
    logger: Any,
) -> dict[str, Any]:
    """Validate/process security values before persistence."""
    if values.get("AUTH_METHOD") == "oidc" and not _has_local_password_admin():
        return {"error": True, "message": _OIDC_LOCKOUT_MESSAGE, "values": values}

    password = values.pop("BUILTIN_PASSWORD", "")
    password_confirm = values.pop("BUILTIN_PASSWORD_CONFIRM", "")

    if password:
        if not values.get("BUILTIN_USERNAME"):
            return {"error": True, "message": "Username cannot be empty", "values": values}
        if password != password_confirm:
            return {"error": True, "message": "Passwords do not match", "values": values}
        if len(password) < 4:
            return {"error": True, "message": "Password must be at least 4 characters", "values": values}

        values["BUILTIN_PASSWORD_HASH"] = hash_password(password)
        logger.info("Password hash updated")
    elif "BUILTIN_USERNAME" in values:
        existing = load_security_config()
        if "BUILTIN_PASSWORD_HASH" in existing:
            values["BUILTIN_PASSWORD_HASH"] = existing["BUILTIN_PASSWORD_HASH"]

    if values.get("AUTH_METHOD") == "builtin":
        try:
            sync_builtin_admin_user(
                values.get("BUILTIN_USERNAME", ""),
                values.get("BUILTIN_PASSWORD_HASH", ""),
            )
        except Exception as exc:
            logger.error(f"Failed to sync builtin admin user: {exc}")
            return {"error": True, "message": "Failed to create/update local admin user from builtin credentials", "values": values}

    return {"error": False, "values": values}


def test_oidc_connection(
    *,
    load_security_config: Callable[[], dict[str, Any]],
    logger: Any,
) -> dict[str, Any]:
    """Fetch and validate the configured OIDC discovery document."""
    import requests

    try:
        discovery_url = load_security_config().get("OIDC_DISCOVERY_URL", "")
        if not discovery_url:
            return {"success": False, "message": "Discovery URL is not configured."}

        response = requests.get(discovery_url, timeout=10)
        response.raise_for_status()
        document = response.json()

        required_fields = ["issuer", "authorization_endpoint", "token_endpoint"]
        missing_fields = [field for field in required_fields if field not in document]
        if missing_fields:
            return {"success": False, "message": f"Discovery document missing fields: {', '.join(missing_fields)}"}

        return {"success": True, "message": f"Connected to {document['issuer']}"}
    except Exception as exc:
        logger.error(f"OIDC connection test failed: {exc}")
        return {"success": False, "message": f"Connection failed: {str(exc)}"}
