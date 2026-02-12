"""Admin user management API routes.

Registers /api/admin/users CRUD endpoints for managing users.
All endpoints require admin session.
"""

from functools import wraps
import os
import sqlite3
from typing import Any

from flask import Flask, jsonify, request, session
from werkzeug.security import generate_password_hash

from shelfmark.config.booklore_settings import (
    get_booklore_library_options,
    get_booklore_path_options,
)
from shelfmark.config.env import CWA_DB_PATH
from shelfmark.core.admin_settings_routes import (
    register_admin_settings_routes,
    validate_user_settings,
)
from shelfmark.core.auth_modes import (
    AUTH_SOURCE_BUILTIN,
    AUTH_SOURCE_CWA,
    AUTH_SOURCE_OIDC,
    AUTH_SOURCE_PROXY,
    determine_auth_mode,
    has_local_password_admin,
    normalize_auth_source,
)
from shelfmark.core.cwa_user_sync import sync_cwa_users_from_rows
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)


def _get_user_edit_capabilities(
    user: dict[str, Any],
    security_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return backend-authored capability flags for the user edit form."""
    auth_source = normalize_auth_source(
        user.get("auth_source"),
        user.get("oidc_subject"),
    )
    if security_config is None and auth_source == AUTH_SOURCE_OIDC:
        security_config = load_config_file("security")

    oidc_use_admin_group = bool((security_config or {}).get("OIDC_USE_ADMIN_GROUP", True))
    role_managed_by_oidc_group = auth_source == AUTH_SOURCE_OIDC and oidc_use_admin_group
    can_edit_role = auth_source == AUTH_SOURCE_BUILTIN or (
        auth_source == AUTH_SOURCE_OIDC and not role_managed_by_oidc_group
    )

    return {
        "authSource": auth_source,
        "canSetPassword": auth_source == AUTH_SOURCE_BUILTIN,
        "canEditRole": can_edit_role,
        "canEditEmail": auth_source in {AUTH_SOURCE_BUILTIN, AUTH_SOURCE_PROXY},
        "canEditDisplayName": auth_source != AUTH_SOURCE_OIDC,
    }


def _get_auth_mode():
    """Get current auth mode from config."""
    try:
        config = load_config_file("security")
        return determine_auth_mode(
            config,
            CWA_DB_PATH,
            has_local_admin=has_local_password_admin(),
        )
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


def _oidc_role_management_message(security_config: dict[str, Any]) -> str:
    admin_group = security_config.get("OIDC_ADMIN_GROUP", "")
    if admin_group:
        return (
            "Admin roles for OIDC users are managed by the "
            f"'{admin_group}' group in your identity provider"
        )
    return (
        "Disable 'Use Admin Group for Authorization' in security settings "
        "to manage roles manually"
    )


def _is_user_active(user: dict[str, Any], auth_method: str) -> bool:
    """Determine whether a user can authenticate in the current auth mode."""
    source = normalize_auth_source(user.get("auth_source"), user.get("oidc_subject"))
    if source == AUTH_SOURCE_BUILTIN:
        return auth_method in (AUTH_SOURCE_BUILTIN, AUTH_SOURCE_OIDC)
    return source == auth_method


def _serialize_user(
    user: dict[str, Any],
    auth_method: str,
    security_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Sanitize and enrich a user payload for API responses."""
    payload = _sanitize_user(user)
    payload["auth_source"] = normalize_auth_source(
        payload.get("auth_source"),
        payload.get("oidc_subject"),
    )
    payload["is_active"] = _is_user_active(payload, auth_method)
    payload["edit_capabilities"] = _get_user_edit_capabilities(
        payload,
        security_config=security_config,
    )
    return payload


def _sync_all_cwa_users(user_db: UserDB) -> dict[str, int]:
    """Sync all users from the Calibre-Web database into users.db."""
    if not CWA_DB_PATH or not CWA_DB_PATH.exists():
        raise FileNotFoundError("Calibre-Web database is not available")

    db_path = os.fspath(CWA_DB_PATH)
    db_uri = f"file:{db_path}?mode=ro&immutable=1"
    conn = sqlite3.connect(db_uri, uri=True)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name, role, email FROM user")
        rows = cur.fetchall()
    finally:
        conn.close()

    return sync_cwa_users_from_rows(user_db, rows)


def register_admin_routes(app: Flask, user_db: UserDB) -> None:
    """Register admin user management routes on the Flask app."""

    @app.route("/api/admin/users", methods=["GET"])
    @_require_admin
    def admin_list_users():
        """List all users."""
        users = user_db.list_users()
        auth_mode = _get_auth_mode()
        security_config = load_config_file("security")
        return jsonify([
            _serialize_user(u, auth_mode, security_config=security_config)
            for u in users
        ])

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

        if auth_mode in {AUTH_SOURCE_PROXY, AUTH_SOURCE_CWA}:
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
                auth_source=AUTH_SOURCE_BUILTIN,
                role=role,
            )
        except ValueError:
            return jsonify({"error": "Username already exists"}), 409
        logger.info(
            "Shelfmark user created "
            f"(source=manual_admin_create, created_by={session.get('user_id', 'unknown')}, "
            f"username={username}, role={role}, auth_source={AUTH_SOURCE_BUILTIN})"
        )
        return jsonify(
            _serialize_user(
                user,
                _get_auth_mode(),
                security_config=load_config_file("security"),
            )
        ), 201

    @app.route("/api/admin/users/<int:user_id>", methods=["GET"])
    @_require_admin
    def admin_get_user(user_id):
        """Get a user by ID with their settings."""
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        result = _serialize_user(
            user,
            _get_auth_mode(),
            security_config=load_config_file("security"),
        )
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
        security_config = load_config_file("security")
        auth_source = normalize_auth_source(
            user.get("auth_source"),
            user.get("oidc_subject"),
        )
        capabilities = _get_user_edit_capabilities(user, security_config=security_config)

        # Handle optional password update
        password = data.get("password", "")
        if password:
            if not capabilities["canSetPassword"]:
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

        if role_changed and not capabilities["canEditRole"]:
            if auth_source == AUTH_SOURCE_OIDC:
                return jsonify({
                    "error": "Cannot change role for OIDC user when group-based authorization is enabled",
                    "message": _oidc_role_management_message(security_config),
                }), 400

            return jsonify({
                "error": f"Cannot change role for {auth_source.upper()} users",
                "message": "Role is managed by the external authentication source.",
            }), 400

        if email_changed and not capabilities["canEditEmail"]:
            if auth_source == AUTH_SOURCE_CWA:
                return jsonify({
                    "error": "Cannot change email for CWA users",
                    "message": "Email is synced from Calibre-Web.",
                }), 400

            return jsonify({
                "error": "Cannot change email for OIDC users",
                "message": "Email is managed by your identity provider.",
            }), 400

        if display_name_changed and not capabilities["canEditDisplayName"]:
            return jsonify({
                "error": "Cannot change display name for OIDC users",
                "message": "Display name is managed by your identity provider.",
            }), 400

        # Allow demoting the last admin account.
        # Auth mode resolution automatically falls back to "none" when no
        # local password admin remains.

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

            validated_settings, validation_errors = validate_user_settings(data["settings"])
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
        result = _serialize_user(
            updated,
            _get_auth_mode(),
            security_config=security_config,
        )
        result["settings"] = user_db.get_user_settings(user_id)
        logger.info(f"Admin updated user {user_id}")
        return jsonify(result)

    @app.route("/api/admin/users/sync-cwa", methods=["POST"])
    @_require_admin
    def admin_sync_cwa_users():
        """Manually sync users from Calibre-Web into users.db."""
        auth_mode = _get_auth_mode()
        if auth_mode != AUTH_SOURCE_CWA:
            return jsonify({
                "error": "CWA sync is only available when CWA authentication is enabled",
            }), 400

        try:
            summary = _sync_all_cwa_users(user_db)
        except FileNotFoundError:
            return jsonify({
                "error": "Calibre-Web database is not available",
                "message": "Verify app.db is mounted and readable at /auth/app.db.",
            }), 503
        except Exception as exc:
            logger.error(f"Failed to sync CWA users: {exc}")
            return jsonify({
                "error": "Failed to sync users from Calibre-Web",
            }), 500

        message = (
            f"Synced {summary['total']} CWA users "
            f"({summary['created']} created, {summary['updated']} updated, "
            f"{summary.get('deleted', 0)} deleted)."
        )
        logger.info(message)
        return jsonify({
            "success": True,
            "message": message,
            **summary,
        })

    register_admin_settings_routes(app, user_db, _require_admin)

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
        auth_source = normalize_auth_source(
            user.get("auth_source"),
            user.get("oidc_subject"),
        )
        if auth_source == AUTH_SOURCE_CWA and auth_source == auth_mode:
            return jsonify({
                "error": f"Cannot delete active {auth_source.upper()} users",
                "message": f"{auth_source.upper()} users are automatically re-provisioned on login.",
            }), 400

        # Allow deleting the last local admin account.
        # Auth mode resolution automatically falls back to "none" when no
        # local password admin remains.

        user_db.delete_user(user_id)
        logger.info(f"Admin deleted user {user_id}: {user['username']}")
        return jsonify({"success": True})
