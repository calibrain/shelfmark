"""Self-service user account routes."""

from functools import wraps
from typing import Any, Callable, Mapping

from flask import Flask, jsonify, request, session
from werkzeug.security import generate_password_hash

from shelfmark.config.env import CWA_DB_PATH
from shelfmark.core.admin_settings_routes import (
    build_user_notification_test_response,
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
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.user_settings_overrides import (
    build_user_preferences_payload as _build_user_preferences_payload,
    get_ordered_user_overridable_fields as _get_ordered_user_overridable_fields,
)
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

MIN_PASSWORD_LENGTH = 4
_VISIBLE_SELF_SETTINGS_SECTIONS_KEY = "VISIBLE_SELF_SETTINGS_SECTIONS"
_SELF_SETTINGS_SECTION_DELIVERY = "delivery"
_SELF_SETTINGS_SECTION_NOTIFICATIONS = "notifications"
_VALID_SELF_SETTINGS_SECTIONS = (
    _SELF_SETTINGS_SECTION_DELIVERY,
    _SELF_SETTINGS_SECTION_NOTIFICATIONS,
)
_DEFAULT_VISIBLE_SELF_SETTINGS_SECTIONS = list(_VALID_SELF_SETTINGS_SECTIONS)


def _get_auth_mode() -> str:
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


def _require_authenticated_user(f: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator requiring an authenticated session linked to a local user row."""

    @wraps(f)
    def decorated(*args, **kwargs):
        auth_mode = _get_auth_mode()
        if auth_mode != "none" and "user_id" not in session:
            return jsonify({"error": "Authentication required"}), 401
        if "db_user_id" not in session:
            return jsonify({"error": "Authenticated session is missing local user context"}), 403
        return f(*args, **kwargs)

    return decorated


def _get_current_user(user_db: UserDB) -> tuple[int | None, dict[str, Any] | None, tuple[Any, int] | None]:
    raw_user_id = session.get("db_user_id")
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        return None, None, (jsonify({"error": "Invalid user context"}), 400)

    user = user_db.get_user(user_id=user_id)
    if not user:
        return None, None, (jsonify({"error": "User not found"}), 404)
    return user_id, user, None


def _is_user_active(user: Mapping[str, Any], auth_method: str) -> bool:
    source = normalize_auth_source(user.get("auth_source"), user.get("oidc_subject"))
    if source == AUTH_SOURCE_BUILTIN:
        return auth_method in (AUTH_SOURCE_BUILTIN, AUTH_SOURCE_OIDC)
    return source == auth_method


def _get_self_edit_capabilities(user: Mapping[str, Any]) -> dict[str, Any]:
    auth_source = normalize_auth_source(
        user.get("auth_source"),
        user.get("oidc_subject"),
    )

    return {
        "authSource": auth_source,
        "canSetPassword": auth_source == AUTH_SOURCE_BUILTIN,
        "canEditRole": False,
        "canEditEmail": auth_source in {AUTH_SOURCE_BUILTIN, AUTH_SOURCE_PROXY},
        "canEditDisplayName": auth_source != AUTH_SOURCE_OIDC,
    }


def _serialize_self_user(user: Mapping[str, Any], auth_mode: str) -> dict[str, Any]:
    payload = dict(user)
    payload.pop("password_hash", None)
    payload["auth_source"] = normalize_auth_source(
        payload.get("auth_source"),
        payload.get("oidc_subject"),
    )
    payload["is_active"] = _is_user_active(payload, auth_mode)
    payload["edit_capabilities"] = _get_self_edit_capabilities(payload)
    return payload


def _normalize_visible_self_settings_sections(raw_sections: Any) -> list[str]:
    """Normalize users.VISIBLE_SELF_SETTINGS_SECTIONS to a safe ordered list."""
    if raw_sections is None:
        return list(_DEFAULT_VISIBLE_SELF_SETTINGS_SECTIONS)

    if isinstance(raw_sections, str):
        candidate_sections = [s.strip() for s in raw_sections.split(",") if s.strip()]
    elif isinstance(raw_sections, (list, tuple, set)):
        candidate_sections = [str(section).strip() for section in raw_sections if str(section).strip()]
    else:
        return list(_DEFAULT_VISIBLE_SELF_SETTINGS_SECTIONS)

    normalized_sections: list[str] = []
    for section in candidate_sections:
        if section in _VALID_SELF_SETTINGS_SECTIONS and section not in normalized_sections:
            normalized_sections.append(section)

    if not normalized_sections and candidate_sections:
        # Invalid non-empty config should fail-safe to showing defaults.
        return list(_DEFAULT_VISIBLE_SELF_SETTINGS_SECTIONS)

    return normalized_sections


def _get_visible_self_settings_sections() -> list[str]:
    users_config = load_config_file("users")
    raw_sections = users_config.get(_VISIBLE_SELF_SETTINGS_SECTIONS_KEY)
    return _normalize_visible_self_settings_sections(raw_sections)


def _get_allowed_self_settings_keys(visible_sections: list[str]) -> set[str]:
    allowed_keys: set[str] = set()
    visible_sections_set = set(visible_sections)

    if _SELF_SETTINGS_SECTION_DELIVERY in visible_sections_set:
        allowed_keys |= {
            key for key, _field in _get_ordered_user_overridable_fields("downloads")
        }

    if _SELF_SETTINGS_SECTION_NOTIFICATIONS in visible_sections_set:
        allowed_keys |= {
            key for key, _field in _get_ordered_user_overridable_fields("notifications")
        }

    return allowed_keys


def register_self_user_routes(app: Flask, user_db: UserDB) -> None:
    """Register self-service user endpoints."""

    @app.route("/api/users/me/edit-context", methods=["GET"])
    @_require_authenticated_user
    def users_me_edit_context():
        user_id, user, user_error = _get_current_user(user_db)
        if user_error:
            return user_error

        auth_mode = _get_auth_mode()
        serialized_user = _serialize_self_user(user, auth_mode)
        serialized_user["settings"] = user_db.get_user_settings(user_id)
        visible_self_settings_sections = _get_visible_self_settings_sections()

        delivery_preferences = None
        if _SELF_SETTINGS_SECTION_DELIVERY in visible_self_settings_sections:
            try:
                delivery_preferences = _build_user_preferences_payload(user_db, user_id, "downloads")
            except ValueError:
                return jsonify({"error": "Downloads settings tab not found"}), 500
            except Exception as exc:
                logger.warning(f"Failed to build user delivery preferences for user_id={user_id}: {exc}")
                delivery_preferences = None

        notification_preferences = None
        if _SELF_SETTINGS_SECTION_NOTIFICATIONS in visible_self_settings_sections:
            try:
                notification_preferences = _build_user_preferences_payload(user_db, user_id, "notifications")
            except ValueError:
                return jsonify({"error": "Notifications settings tab not found"}), 500
            except Exception as exc:
                logger.warning(f"Failed to build user notification preferences for user_id={user_id}: {exc}")
                notification_preferences = None

        user_overridable_keys = sorted(
            set(delivery_preferences.get("keys", []) if delivery_preferences else [])
            | set(notification_preferences.get("keys", []) if notification_preferences else [])
        )

        return jsonify(
            {
                "user": serialized_user,
                "deliveryPreferences": delivery_preferences,
                "notificationPreferences": notification_preferences,
                "userOverridableKeys": user_overridable_keys,
                "visibleUserSettingsSections": visible_self_settings_sections,
            }
        )

    @app.route("/api/users/me/notification-preferences/test", methods=["POST"])
    @_require_authenticated_user
    def users_me_test_notification_preferences():
        user_id, _user, user_error = _get_current_user(user_db)
        if user_error:
            return user_error
        if user_id is None:
            return jsonify({"error": "User not found"}), 404

        payload = request.get_json(silent=True)
        result, status_code = build_user_notification_test_response(
            user_id=user_id,
            payload=payload,
        )
        return jsonify(result), status_code

    @app.route("/api/users/me", methods=["PUT"])
    @_require_authenticated_user
    def users_me_update():
        user_id, user, user_error = _get_current_user(user_db)
        if user_error:
            return user_error

        data = request.get_json() or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        capabilities = _get_self_edit_capabilities(user)
        auth_source = capabilities["authSource"]

        password = data.get("password", "")
        if password:
            if not capabilities["canSetPassword"]:
                return jsonify(
                    {
                        "error": f"Cannot set password for {auth_source.upper()} users",
                        "message": "Password authentication is only available for local users.",
                    }
                ), 400
            if len(password) < MIN_PASSWORD_LENGTH:
                return jsonify({"error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters"}), 400
            user_db.update_user(user_id, password_hash=generate_password_hash(password))

        user_fields: dict[str, Any] = {}
        if "email" in data:
            incoming_email = data.get("email")
            if incoming_email is None:
                user_fields["email"] = None
            else:
                user_fields["email"] = str(incoming_email).strip() or None
        if "display_name" in data:
            incoming_display_name = data.get("display_name")
            user_fields["display_name"] = (
                str(incoming_display_name).strip() or None
                if incoming_display_name is not None
                else None
            )

        email_changed = "email" in user_fields and user_fields["email"] != user.get("email")
        display_name_changed = (
            "display_name" in user_fields
            and user_fields["display_name"] != user.get("display_name")
        )

        if email_changed and not capabilities["canEditEmail"]:
            if auth_source == AUTH_SOURCE_CWA:
                return jsonify(
                    {
                        "error": "Cannot change email for CWA users",
                        "message": "Email is synced from Calibre-Web.",
                    }
                ), 400
            return jsonify(
                {
                    "error": "Cannot change email for OIDC users",
                    "message": "Email is managed by your identity provider.",
                }
            ), 400

        if display_name_changed and not capabilities["canEditDisplayName"]:
            return jsonify(
                {
                    "error": "Cannot change display name for OIDC users",
                    "message": "Display name is managed by your identity provider.",
                }
            ), 400

        for field in ("email", "display_name"):
            if field in user_fields and user_fields[field] == user.get(field):
                user_fields.pop(field)

        if user_fields:
            user_db.update_user(user_id, **user_fields)

        if "settings" in data:
            settings_payload = data["settings"]
            if not isinstance(settings_payload, dict):
                return jsonify({"error": "Settings must be an object"}), 400

            visible_self_settings_sections = _get_visible_self_settings_sections()
            allowed_user_settings_keys = _get_allowed_self_settings_keys(visible_self_settings_sections)
            disallowed_keys = sorted(
                key for key in settings_payload if key not in allowed_user_settings_keys
            )
            if disallowed_keys:
                return jsonify(
                    {
                        "error": "Some settings are admin-only",
                        "details": [
                            f"Setting not user-overridable: {key}" for key in disallowed_keys
                        ],
                    }
                ), 400

            validated_settings, validation_errors = validate_user_settings(settings_payload)
            if validation_errors:
                return jsonify(
                    {
                        "error": "Invalid settings payload",
                        "details": validation_errors,
                    }
                ), 400

            user_db.set_user_settings(user_id, validated_settings)
            try:
                from shelfmark.core.config import config as app_config

                app_config.refresh()
            except Exception:
                pass

        updated = user_db.get_user(user_id=user_id)
        if not updated:
            return jsonify({"error": "User not found"}), 404

        result = _serialize_self_user(updated, _get_auth_mode())
        result["settings"] = user_db.get_user_settings(user_id)
        logger.info(f"User {user_id} updated their own account")
        return jsonify(result)
