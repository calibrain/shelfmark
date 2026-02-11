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
from shelfmark.config.env import CWA_DB_PATH
from shelfmark.core.auth_modes import determine_auth_mode
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_DOWNLOAD_DEFAULTS = {
    "BOOKS_OUTPUT_MODE": "folder",
    "DESTINATION": "/books",
    "BOOKLORE_LIBRARY_ID": "",
    "BOOKLORE_PATH_ID": "",
    "EMAIL_RECIPIENT": "",
}

_DOWNLOAD_DEFAULT_KEYS = tuple(_DOWNLOAD_DEFAULTS.keys())
_DELIVERY_PREFERENCE_KEYS = _DOWNLOAD_DEFAULT_KEYS


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
        return determine_auth_mode(config, CWA_DB_PATH)
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
    sanitized = dict(user)
    sanitized.pop("password_hash", None)
    return sanitized


def _normalize_auth_source(user: dict[str, Any]) -> str:
    """Resolve a stable auth source for a user record."""
    source = user.get("auth_source")
    if source in {"builtin", "oidc", "proxy", "cwa"}:
        return source
    if user.get("oidc_subject"):
        return "oidc"
    return "builtin"


def _is_user_active(user: dict[str, Any], auth_method: str) -> bool:
    """Determine whether a user can authenticate in the current auth mode."""
    source = _normalize_auth_source(user)
    if source == "builtin":
        return auth_method in ("builtin", "oidc")
    return source == auth_method


def _serialize_user(user: dict[str, Any], auth_method: str) -> dict[str, Any]:
    """Sanitize and enrich a user payload for API responses."""
    payload = _sanitize_user(user)
    payload["auth_source"] = _normalize_auth_source(payload)
    payload["is_active"] = _is_user_active(payload, auth_method)
    return payload


def register_admin_routes(app: Flask, user_db: UserDB) -> None:
    """Register admin user management routes on the Flask app."""

    @app.route("/api/admin/users", methods=["GET"])
    @_require_admin
    def admin_list_users():
        """List all users."""
        users = user_db.list_users()
        auth_mode = _get_auth_mode()
        return jsonify([_serialize_user(u, auth_mode) for u in users])

    @app.route("/api/admin/users", methods=["POST"])
    @_require_admin
    def admin_create_user():
        """Create a new user with password authentication."""
        data = request.get_json() or {}
        auth_mode = _get_auth_mode()

        username = (data.get("username") or "").strip()
        password = data.get("password", "")
        email = (data.get("email") or "").strip() or None
        display_name = (data.get("display_name") or "").strip() or None
        role = data.get("role", "user")

        if auth_mode in {"proxy", "cwa"}:
            return jsonify({
                "error": "Local user creation is disabled in this authentication mode",
                "message": (
                    "Users are provisioned by your external authentication source. "
                    "Switch to builtin or OIDC mode to create local users."
                ),
            }), 400

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
                auth_source="builtin",
                role=role,
            )
        except ValueError:
            return jsonify({"error": "Username already exists"}), 409
        logger.info(f"Admin created user: {username} (role={role})")
        return jsonify(_serialize_user(user, _get_auth_mode())), 201

    @app.route("/api/admin/users/<int:user_id>", methods=["GET"])
    @_require_admin
    def admin_get_user(user_id):
        """Get a user by ID with their settings."""
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        result = _serialize_user(user, _get_auth_mode())
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
        auth_source = _normalize_auth_source(user)

        # Handle optional password update
        password = data.get("password", "")
        if password:
            if auth_source != "builtin":
                return jsonify({
                    "error": f"Cannot set password for {auth_source.upper()} users",
                    "message": "Password authentication is only available for local users.",
                }), 400
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

        role_changed = "role" in user_fields and user_fields["role"] != user.get("role")
        email_changed = "email" in user_fields and user_fields["email"] != user.get("email")
        display_name_changed = (
            "display_name" in user_fields
            and user_fields["display_name"] != user.get("display_name")
        )

        if role_changed and auth_source in {"proxy", "cwa"}:
            return jsonify({
                "error": f"Cannot change role for {auth_source.upper()} users",
                "message": "Role is managed by the external authentication source.",
            }), 400

        if auth_source == "oidc":
            if email_changed:
                return jsonify({
                    "error": "Cannot change email for OIDC users",
                    "message": "Email is managed by your identity provider.",
                }), 400
            if display_name_changed:
                return jsonify({
                    "error": "Cannot change display name for OIDC users",
                    "message": "Display name is managed by your identity provider.",
                }), 400
            # Prevent changing OIDC user role when group-based auth is enabled
            if role_changed:
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

        if auth_source == "cwa" and email_changed:
            return jsonify({
                "error": "Cannot change email for CWA users",
                "message": "Email is synced from Calibre-Web.",
            }), 400

        # Prevent demoting the last admin
        if role_changed and user_fields["role"] != "admin":
            if user.get("role") == "admin":
                other_admins = [
                    u for u in user_db.list_users()
                    if u["role"] == "admin" and u["id"] != user_id
                ]
                if not other_admins:
                    return jsonify({"error": "Cannot remove admin role from the last admin account"}), 400

        # Avoid unnecessary writes for no-op field updates.
        for field in ("role", "email", "display_name"):
            if field in user_fields and user_fields[field] == user.get(field):
                user_fields.pop(field)

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
            # Ensure runtime reads see updated per-user overrides immediately.
            try:
                from shelfmark.core.config import config as app_config
                app_config.refresh()
            except Exception:
                pass

        updated = user_db.get_user(user_id=user_id)
        result = _serialize_user(updated, _get_auth_mode())
        result["settings"] = user_db.get_user_settings(user_id)
        logger.info(f"Admin updated user {user_id}")
        return jsonify(result)

    @app.route("/api/admin/download-defaults", methods=["GET"])
    @_require_admin
    def admin_download_defaults():
        """Return global download settings relevant to per-user overrides."""
        config = load_config_file("downloads")
        defaults = {
            key: config.get(key, _DOWNLOAD_DEFAULTS.get(key))
            for key in _DOWNLOAD_DEFAULT_KEYS
        }

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

    @app.route("/api/admin/users/<int:user_id>/delivery-preferences", methods=["GET"])
    @_require_admin
    def admin_get_delivery_preferences(user_id):
        """Return curated per-user delivery preference fields and effective values."""
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        from shelfmark.core import settings_registry
        from shelfmark.core.config import config as app_config

        # Ensure settings modules are registered.
        import shelfmark.config.settings  # noqa: F401
        import shelfmark.config.security  # noqa: F401

        downloads_tab = settings_registry.get_settings_tab("downloads")
        if not downloads_tab:
            return jsonify({"error": "Downloads settings tab not found"}), 500

        download_config = load_config_file("downloads")
        user_settings = user_db.get_user_settings(user_id)

        keyed_fields: dict[str, Any] = {}
        for field in downloads_tab.fields:
            if isinstance(field, (settings_registry.ActionButton, settings_registry.HeadingField)):
                continue
            if field.key in _DELIVERY_PREFERENCE_KEYS and getattr(field, "user_overridable", False):
                keyed_fields[field.key] = field

        # Keep declared order stable for frontend rendering.
        ordered_keys = [key for key in _DELIVERY_PREFERENCE_KEYS if key in keyed_fields]

        fields_payload: list[dict[str, Any]] = []
        global_values: dict[str, Any] = {}
        effective: dict[str, dict[str, Any]] = {}

        for key in ordered_keys:
            field = keyed_fields[key]
            serialized = settings_registry.serialize_field(field, "downloads", include_value=False)
            serialized["fromEnv"] = bool(field.env_supported and settings_registry.is_value_from_env(field))
            fields_payload.append(serialized)

            global_values[key] = app_config.get(key, field.default)

            source = "default"
            value = app_config.get(key, field.default, user_id=user_id)
            if field.env_supported and settings_registry.is_value_from_env(field):
                source = "env_var"
            elif key in user_settings and user_settings[key] is not None:
                source = "user_override"
                value = user_settings[key]
            elif key in download_config:
                source = "global_config"

            effective[key] = {
                "value": value,
                "source": source,
            }

        user_overrides = {
            key: user_settings[key]
            for key in ordered_keys
            if key in user_settings and user_settings[key] is not None
        }

        return jsonify({
            "tab": "downloads",
            "keys": ordered_keys,
            "fields": fields_payload,
            "globalValues": global_values,
            "userOverrides": user_overrides,
            "effective": effective,
        })

    @app.route("/api/admin/settings/overrides-summary", methods=["GET"])
    @_require_admin
    def admin_settings_overrides_summary():
        """Return per-key user override counts/details for a settings tab."""
        from shelfmark.core import settings_registry

        # Ensure settings modules are registered.
        import shelfmark.config.settings  # noqa: F401
        import shelfmark.config.security  # noqa: F401

        tab_name = (request.args.get("tab") or "downloads").strip()
        tab = settings_registry.get_settings_tab(tab_name)
        if not tab:
            return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

        overridable_keys: list[str] = []
        for field in tab.fields:
            if isinstance(field, (settings_registry.ActionButton, settings_registry.HeadingField)):
                continue
            if getattr(field, "user_overridable", False):
                overridable_keys.append(field.key)

        keys_summary: dict[str, dict[str, Any]] = {}
        for key in overridable_keys:
            keys_summary[key] = {"count": 0, "users": []}

        for user_record in user_db.list_users():
            user_settings = user_db.get_user_settings(user_record["id"])
            if not isinstance(user_settings, dict):
                continue

            for key in overridable_keys:
                if key not in user_settings or user_settings[key] is None:
                    continue
                keys_summary[key]["users"].append({
                    "userId": user_record["id"],
                    "username": user_record["username"],
                    "value": user_settings[key],
                })

        keys_payload: dict[str, dict[str, Any]] = {}
        for key, summary in keys_summary.items():
            if not summary["users"]:
                continue
            keys_payload[key] = {
                "count": len(summary["users"]),
                "users": summary["users"],
            }

        return jsonify({
            "tab": tab_name,
            "keys": keys_payload,
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

        auth_mode = _get_auth_mode()
        auth_source = _normalize_auth_source(user)
        if auth_source in {"proxy", "cwa"} and auth_source == auth_mode:
            return jsonify({
                "error": f"Cannot delete active {auth_source.upper()} users",
                "message": f"{auth_source.upper()} users are automatically re-provisioned on login.",
            }), 400
        if auth_source == "oidc" and auth_mode == "oidc":
            security_config = load_config_file("security")
            if security_config.get("OIDC_AUTO_PROVISION", True):
                return jsonify({
                    "error": "Cannot delete active OIDC users",
                    "message": "OIDC users are automatically re-provisioned on login while auto-provisioning is enabled.",
                }), 400

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
