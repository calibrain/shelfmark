"""Admin user management API routes.

Registers /api/admin/users CRUD endpoints for managing users.
All endpoints require admin session.
"""

from functools import wraps

from flask import Flask, jsonify, request, session
from werkzeug.security import generate_password_hash

from shelfmark.core.logger import setup_logger
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)


def _require_admin(f):
    """Decorator to require admin session for admin routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
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

        # Check if username already exists
        if user_db.get_user(username=username):
            return jsonify({"error": "Username already exists"}), 409

        password_hash = generate_password_hash(password)
        user = user_db.create_user(
            username=username,
            password_hash=password_hash,
            email=email,
            display_name=display_name,
            role=role,
        )
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

        # Update user fields
        user_fields = {}
        for field in ("role", "email", "display_name"):
            if field in data:
                user_fields[field] = data[field]

        if user_fields:
            user_db.update_user(user_id, **user_fields)

        # Update per-user settings
        if "settings" in data and isinstance(data["settings"], dict):
            user_db.set_user_settings(user_id, data["settings"])

        updated = user_db.get_user(user_id=user_id)
        result = _sanitize_user(updated)
        result["settings"] = user_db.get_user_settings(user_id)
        logger.info(f"Admin updated user {user_id}")
        return jsonify(result)

    @app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
    @_require_admin
    def admin_delete_user(user_id):
        """Delete a user."""
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        user_db.delete_user(user_id)
        logger.info(f"Admin deleted user {user_id}: {user['username']}")
        return jsonify({"success": True})
