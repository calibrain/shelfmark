"""Explicit self-service account and personal preference routes."""

from functools import wraps
from typing import TYPE_CHECKING, Any, Mapping

from flask import Flask, Response, g, jsonify, request, session

from shelfmark.config.env import CWA_DB_PATH
from shelfmark.core.auth_modes import load_active_auth_mode
from shelfmark.core.logger import setup_logger
from shelfmark.core.notifications import (
    is_valid_apprise_destination,
    is_valid_email_destination,
    send_personal_test_notification,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_PERSONAL_PREFERENCE_FIELDS = {
    "kindle_address",
    "notifications_enabled",
    "notification_transport",
    "notification_destination",
}
_NOTIFICATION_TRANSPORTS = {"email", "apprise"}


def _get_current_user(
    user_db: UserDB,
) -> tuple[int | None, dict[str, Any] | None, tuple[Response, int] | None]:
    raw_user_id = session.get("db_user_id")
    if raw_user_id is None:
        return None, None, (jsonify({"error": "Invalid user context"}), 400)
    try:
        user_id = int(raw_user_id)
    except TypeError, ValueError:
        return None, None, (jsonify({"error": "Invalid user context"}), 400)
    user = user_db.get_user(user_id=user_id)
    if not user:
        return None, None, (jsonify({"error": "User not found"}), 404)
    return user_id, user, None


def _serialize_self_settings(
    user: Mapping[str, Any], preferences: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the intentionally narrow self-settings contract."""
    return {
        "username": user["username"],
        "email": user.get("email"),
        "display_name": user.get("display_name"),
        "kindle_address": preferences.get("kindle_address"),
        "notifications_enabled": bool(preferences.get("notifications_enabled")),
        "notification_transport": preferences.get("notification_transport"),
        "notification_destination": preferences.get("notification_destination"),
    }


def _normalize_preferences(data: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    preferences: dict[str, Any] = {}
    for field in _PERSONAL_PREFERENCE_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field == "notifications_enabled":
            if not isinstance(value, bool):
                return {}, "notifications_enabled must be a boolean"
            preferences[field] = value
        elif value is None:
            preferences[field] = None
        elif isinstance(value, str):
            preferences[field] = value.strip() or None
        else:
            return {}, f"{field} must be a string or null"

    transport = preferences.get("notification_transport")
    if transport == "email":
        return {}, "notification_transport must be 'apprise' or null"
    if transport is not None and transport not in _NOTIFICATION_TRANSPORTS:
        return {}, "notification_transport must be 'apprise' or null"
    if transport != "apprise" and "notification_destination" in preferences:
        return {}, "notification_destination is only supported for Apprise"
    return preferences, None


def register_self_user_routes(app: Flask, user_db: UserDB) -> None:
    """Register the authenticated user's explicit settings contract."""

    def _require_authenticated_user(
        f: Callable[..., Response | tuple[Response, int]],
    ) -> Callable[..., Response | tuple[Response, int]]:
        @wraps(f)
        def decorated(*args: object, **kwargs: object) -> Response | tuple[Response, int]:
            g.auth_mode = load_active_auth_mode(CWA_DB_PATH, user_db=user_db)
            if g.auth_mode != "none" and "user_id" not in session:
                return jsonify({"error": "Authentication required"}), 401
            if "db_user_id" not in session:
                return jsonify(
                    {"error": "Authenticated session is missing local user context"}
                ), 403
            return f(*args, **kwargs)

        return decorated

    @app.route("/api/users/me", methods=["GET"])
    @_require_authenticated_user
    def users_me_get() -> Response | tuple[Response, int]:
        user_id, user, user_error = _get_current_user(user_db)
        if user_error:
            return user_error
        if user_id is None or user is None:
            return jsonify({"error": "User not found"}), 404
        return jsonify(_serialize_self_settings(user, user_db.get_personal_preferences(user_id)))

    @app.route("/api/users/me", methods=["PUT"])
    @_require_authenticated_user
    def users_me_update() -> Response | tuple[Response, int]:
        user_id, user, user_error = _get_current_user(user_db)
        if user_error:
            return user_error
        if user_id is None or user is None:
            return jsonify({"error": "User not found"}), 404
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        allowed_fields = {"display_name", "email", *_PERSONAL_PREFERENCE_FIELDS}
        unknown_fields = sorted(set(data) - allowed_fields)
        if unknown_fields:
            return jsonify(
                {"error": f"Unsupported self-settings fields: {', '.join(unknown_fields)}"}
            ), 400

        email = user.get("email")
        display_name = user.get("display_name")
        if "email" in data:
            value = data["email"]
            if value is None:
                email = None
            elif isinstance(value, str) and (not value.strip() or is_valid_email_destination(value.strip())):
                email = value.strip() or None
            else:
                return jsonify({"error": "email must be a valid email address or null"}), 400

        if "display_name" in data:
            value = data["display_name"]
            if value is not None and not isinstance(value, str):
                return jsonify({"error": "display_name must be a string or null"}), 400
            display_name = value.strip() or None if isinstance(value, str) else None
        preferences, error = _normalize_preferences(data)
        if error:
            return jsonify({"error": error}), 400
        if "notification_transport" in preferences and "notification_destination" not in preferences:
            # A destination belongs to one transport; selecting another clears the old one.
            preferences["notification_destination"] = None
        effective_preferences = user_db.get_personal_preferences(user_id)
        effective_preferences.update(preferences)
        if effective_preferences["notifications_enabled"]:
            effective_transport = effective_preferences["notification_transport"]
            effective_destination = effective_preferences["notification_destination"]
            if effective_transport == "apprise":
                valid_destination = isinstance(effective_destination, str) and is_valid_apprise_destination(
                    effective_destination
                )
            else:
                valid_destination = isinstance(email, str) and is_valid_email_destination(email)
            if not valid_destination:
                return jsonify(
                    {
                        "error": "A valid notification destination is required when notifications are enabled"
                    }
                ), 400
        user_updates: dict[str, Any] = {}
        if email != user.get("email"):
            user_updates["email"] = email
        if "display_name" in data and display_name != user.get("display_name"):
            user_updates["display_name"] = display_name
        if user_updates:
            user_db.update_user(user_id, **user_updates)
        if preferences:
            user_db.update_personal_preferences(user_id, **preferences)

        updated = user_db.get_user(user_id=user_id)
        if not updated:
            return jsonify({"error": "User not found"}), 404
        logger.info("User %s updated their personal settings", user_id)
        return jsonify(_serialize_self_settings(updated, user_db.get_personal_preferences(user_id)))

    @app.route("/api/users/me/notifications/test", methods=["POST"])
    @_require_authenticated_user
    def users_me_notification_test() -> Response | tuple[Response, int]:
        user_id, _user, user_error = _get_current_user(user_db)
        if user_error:
            return user_error
        if user_id is None:
            return jsonify({"error": "User not found"}), 404
        result = send_personal_test_notification(user_db, user_id)
        return jsonify(result), 200 if result.get("success") else 400
