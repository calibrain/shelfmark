"""Admin user management API routes.

Registers /api/admin/users CRUD endpoints for managing users.
All endpoints require admin session.
"""

from functools import wraps
from typing import Any

from flask import Flask, jsonify, request, session
from werkzeug.security import generate_password_hash

from shelfmark.config.booklore_settings import (
    get_booklore_library_options,
    get_booklore_path_options,
)
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_DOWNLOAD_DEFAULTS = {
    "BOOKS_OUTPUT_MODE": "folder",
    "DESTINATION": "/books",
    "BOOKLORE_LIBRARY_ID": "",
    "BOOKLORE_PATH_ID": "",
    "EMAIL_RECIPIENTS": [],
}


def _get_settings_field_map() -> dict[str, tuple[Any, str]]:
    """Return map of setting key -> (field, tab_name) for value-bearing fields."""
    from shelfmark.core import settings_registry

    # Ensure settings modules are loaded before reading registry metadata.
    import shelfmark.config.settings  # noqa: F401
    import shelfmark.config.security  # noqa: F401

    field_map: dict[str, tuple[Any, str]] = {}

    for tab in settings_registry.get_all_settings_tabs():
        for field in tab.fields:
            if isinstance(field, (settings_registry.ActionButton, settings_registry.HeadingField)):
                continue

            field_map[field.key] = (field, tab.name)

    return field_map


def _validate_user_settings(settings: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate per-user settings against declared overridable settings fields."""
    field_map = _get_settings_field_map()

    valid: dict[str, Any] = {}
    errors: list[str] = []

    for key, value in settings.items():
        if key not in field_map:
            errors.append(f"Unknown setting: {key}")
            continue
        field, _ = field_map[key]
        if not getattr(field, "user_overridable", False):
            errors.append(f"Setting not user-overridable: {key}")
            continue
        valid[key] = value

    return valid, errors


def _get_auth_mode():
    """Get current auth mode from config."""
    try:
        config = load_config_file("security")
        return config.get("AUTH_METHOD", "none")
    except Exception:
        return "none"


def _require_admin(f):
    """Decorator to require admin session for admin routes.

    In no-auth mode, everyone has access (is_admin defaults True).
    In auth-required modes, requires an authenticated session with admin role.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_mode = _get_auth_mode()
        if auth_mode != "none":
            if "user_id" not in session:
                return jsonify({"error": "Authentication required"}), 401
            if not session.get("is_admin", False):
                return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


def _sanitize_user(user: dict) -> dict:
    """Remove sensitive fields from user dict before returning to client."""
    user.pop("password_hash", None)
    return user


def register_admin_routes(app: Flask, user_db: UserDB) -> None:
    """Register admin user management routes on the Flask app."""

    @app.route("/api/admin/users", methods=["GET"])
    @_require_admin
    def admin_list_users():
        """List all users."""
        users = user_db.list_users()
        return jsonify([_sanitize_user(u) for u in users])

    @app.route("/api/admin/users", methods=["POST"])
    @_require_admin
    def admin_create_user():
        """Create a new user with password authentication."""
        data = request.get_json() or {}

        username = (data.get("username") or "").strip()
        password = data.get("password", "")
        email = (data.get("email") or "").strip() or None
        display_name = (data.get("display_name") or "").strip() or None
        role = data.get("role", "user")

        if not username:
            return jsonify({"error": "Username is required"}), 400
        if not password or len(password) < 4:
            return jsonify({"error": "Password must be at least 4 characters"}), 400
        if role not in ("admin", "user"):
            return jsonify({"error": "Role must be 'admin' or 'user'"}), 400

        # First user is always admin
        if not user_db.list_users():
            role = "admin"

        # Check if username already exists
        if user_db.get_user(username=username):
            return jsonify({"error": "Username already exists"}), 409

        password_hash = generate_password_hash(password)
        try:
            user = user_db.create_user(
                username=username,
                password_hash=password_hash,
                email=email,
                display_name=display_name,
                role=role,
            )
        except ValueError:
            return jsonify({"error": "Username already exists"}), 409
        logger.info(f"Admin created user: {username} (role={role})")
        return jsonify(_sanitize_user(user)), 201

    @app.route("/api/admin/users/<int:user_id>", methods=["GET"])
    @_require_admin
    def admin_get_user(user_id):
        """Get a user by ID with their settings."""
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        result = _sanitize_user(user)
        result["settings"] = user_db.get_user_settings(user_id)
        return jsonify(result)

    @app.route("/api/admin/users/<int:user_id>", methods=["PUT"])
    @_require_admin
    def admin_update_user(user_id):
        """Update user fields and/or settings."""
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        data = request.get_json() or {}

        # Handle optional password update
        password = data.get("password", "")
        if password:
            if len(password) < 4:
                return jsonify({"error": "Password must be at least 4 characters"}), 400
            user_db.update_user(user_id, password_hash=generate_password_hash(password))

        # Update user fields
        user_fields = {}
        for field in ("role", "email", "display_name"):
            if field in data:
                user_fields[field] = data[field]

        if "role" in user_fields and user_fields["role"] not in ("admin", "user"):
            return jsonify({"error": "Role must be 'admin' or 'user'"}), 400

        # Prevent changing OIDC user role when group-based auth is enabled
        if "role" in user_fields and user.get("oidc_subject") and user_fields["role"] != user.get("role"):
            security_config = load_config_file("security")
            use_admin_group = security_config.get("OIDC_USE_ADMIN_GROUP", True)
            if use_admin_group:
                admin_group = security_config.get("OIDC_ADMIN_GROUP", "")
                msg = (
                    f"Admin roles for OIDC users are managed by the '{admin_group}' group in your identity provider"
                    if admin_group
                    else "Disable 'Use Admin Group for Authorization' in security settings to manage roles manually"
                )
                return jsonify({
                    "error": "Cannot change role for OIDC user when group-based authorization is enabled",
                    "message": msg,
                }), 400

        # Prevent demoting the last admin
        if "role" in user_fields and user_fields["role"] != "admin":
            if user.get("role") == "admin":
                other_admins = [
                    u for u in user_db.list_users()
                    if u["role"] == "admin" and u["id"] != user_id
                ]
                if not other_admins:
                    return jsonify({"error": "Cannot remove admin role from the last admin account"}), 400

        if user_fields:
            user_db.update_user(user_id, **user_fields)

        # Update per-user settings
        if "settings" in data:
            if not isinstance(data["settings"], dict):
                return jsonify({"error": "Settings must be an object"}), 400

            validated_settings, validation_errors = _validate_user_settings(data["settings"])
            if validation_errors:
                return jsonify({
                    "error": "Invalid settings payload",
                    "details": validation_errors,
                }), 400

            user_db.set_user_settings(user_id, validated_settings)

        updated = user_db.get_user(user_id=user_id)
        result = _sanitize_user(updated)
        result["settings"] = user_db.get_user_settings(user_id)
        logger.info(f"Admin updated user {user_id}")
        return jsonify(result)

    @app.route("/api/admin/download-defaults", methods=["GET"])
    @_require_admin
    def admin_download_defaults():
        """Return global download settings relevant to per-user overrides."""
        config = load_config_file("downloads")
        keys = [
            "BOOKS_OUTPUT_MODE",
            "DESTINATION",
            "BOOKLORE_LIBRARY_ID",
            "BOOKLORE_PATH_ID",
            "EMAIL_RECIPIENTS",
        ]
        defaults = {k: config.get(k, _DOWNLOAD_DEFAULTS.get(k)) for k in keys}

        # Include OIDC settings for UI warnings (e.g., when admin tries to set OIDC user role)
        security_config = load_config_file("security")
        defaults["OIDC_ADMIN_GROUP"] = security_config.get("OIDC_ADMIN_GROUP", "")
        defaults["OIDC_USE_ADMIN_GROUP"] = security_config.get("OIDC_USE_ADMIN_GROUP", True)
        defaults["OIDC_AUTO_PROVISION"] = security_config.get("OIDC_AUTO_PROVISION", True)

        return jsonify(defaults)

    @app.route("/api/admin/booklore-options", methods=["GET"])
    @_require_admin
    def admin_booklore_options():
        """Return available BookLore library and path options."""
        return jsonify({
            "libraries": get_booklore_library_options(),
            "paths": get_booklore_path_options(),
        })

    @app.route("/api/admin/users/<int:user_id>/effective-settings", methods=["GET"])
    @_require_admin
    def admin_get_effective_settings(user_id):
        """Return effective per-user overridable settings with source attribution."""
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        from shelfmark.core.config import config as app_config
        from shelfmark.core.settings_registry import is_value_from_env

        field_map = _get_settings_field_map()
        user_settings = user_db.get_user_settings(user_id)
        tab_config_cache: dict[str, dict[str, Any]] = {}
        effective: dict[str, dict[str, Any]] = {}

        for key in sorted(field_map.keys()):
            field, tab_name = field_map[key]
            if not getattr(field, "user_overridable", False):
                continue

            source = "default"
            value = app_config.get(key, field.default, user_id=user_id)
            if field.env_supported and is_value_from_env(field):
                source = "env_var"
            elif key in user_settings and user_settings[key] is not None:
                source = "user_override"
                value = user_settings[key]
            else:
                if tab_name not in tab_config_cache:
                    tab_config_cache[tab_name] = load_config_file(tab_name)
                if key in tab_config_cache[tab_name]:
                    source = "global_config"

            effective[key] = {
                "value": value,
                "source": source,
            }

        return jsonify(effective)

    @app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
    @_require_admin
    def admin_delete_user(user_id):
        """Delete a user."""
        # Prevent self-deletion
        if session.get("db_user_id") == user_id:
            return jsonify({"error": "Cannot delete your own account"}), 400

        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        # Prevent deleting the last local admin
        if user.get("role") == "admin" and user.get("password_hash"):
            local_admins = [
                u for u in user_db.list_users()
                if u["role"] == "admin" and u.get("password_hash") and u["id"] != user_id
            ]
            if not local_admins:
                return jsonify({"error": "Cannot delete the last local admin account"}), 400

        user_db.delete_user(user_id)
        logger.info(f"Admin deleted user {user_id}: {user['username']}")
        return jsonify({"success": True})
