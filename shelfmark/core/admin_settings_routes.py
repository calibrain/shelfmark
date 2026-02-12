"""Admin settings-introspection routes and settings validation helpers."""

from typing import Any, Callable

from flask import Flask, jsonify, request

from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.user_db import UserDB


def _get_settings_registry():
    # Ensure settings modules are loaded before reading registry metadata.
    import shelfmark.config.settings  # noqa: F401
    import shelfmark.config.security  # noqa: F401
    from shelfmark.core import settings_registry

    return settings_registry


def _get_ordered_user_overridable_fields(tab_name: str) -> list[tuple[str, Any]]:
    settings_registry = _get_settings_registry()
    tab = settings_registry.get_settings_tab(tab_name)
    if not tab:
        return []
    overridable_map = settings_registry.get_user_overridable_fields(tab_name=tab_name)
    return [(field.key, field) for field in tab.fields if field.key in overridable_map]


def validate_user_settings(settings: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    settings_registry = _get_settings_registry()
    field_map = settings_registry.get_settings_field_map()
    overridable_map = settings_registry.get_user_overridable_fields()

    valid: dict[str, Any] = {}
    errors: list[str] = []
    for key, value in settings.items():
        if key not in field_map:
            errors.append(f"Unknown setting: {key}")
        elif key not in overridable_map:
            errors.append(f"Setting not user-overridable: {key}")
        else:
            valid[key] = value

    return valid, errors


def register_admin_settings_routes(
    app: Flask,
    user_db: UserDB,
    require_admin: Callable[[Callable[..., Any]], Callable[..., Any]],
) -> None:
    @app.route("/api/admin/download-defaults", methods=["GET"])
    @require_admin
    def admin_download_defaults():
        config = load_config_file("downloads")
        defaults = {
            key: ("" if (value := config.get(key, field.default)) is None else value)
            for key, field in _get_ordered_user_overridable_fields("downloads")
        }

        security_config = load_config_file("security")
        defaults["OIDC_ADMIN_GROUP"] = security_config.get("OIDC_ADMIN_GROUP", "")
        defaults["OIDC_USE_ADMIN_GROUP"] = security_config.get("OIDC_USE_ADMIN_GROUP", True)
        defaults["OIDC_AUTO_PROVISION"] = security_config.get("OIDC_AUTO_PROVISION", True)
        return jsonify(defaults)

    @app.route("/api/admin/booklore-options", methods=["GET"])
    @require_admin
    def admin_booklore_options():
        from shelfmark.core import admin_routes

        return jsonify({
            "libraries": admin_routes.get_booklore_library_options(),
            "paths": admin_routes.get_booklore_path_options(),
        })

    @app.route("/api/admin/users/<int:user_id>/delivery-preferences", methods=["GET"])
    @require_admin
    def admin_get_delivery_preferences(user_id):
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        from shelfmark.core import settings_registry
        from shelfmark.core.config import config as app_config

        ordered_fields = _get_ordered_user_overridable_fields("downloads")
        if not ordered_fields:
            return jsonify({"error": "Downloads settings tab not found"}), 500

        download_config = load_config_file("downloads")
        user_settings = user_db.get_user_settings(user_id)
        ordered_keys = [key for key, _ in ordered_fields]

        fields_payload: list[dict[str, Any]] = []
        global_values: dict[str, Any] = {}
        effective: dict[str, dict[str, Any]] = {}

        for key, field in ordered_fields:
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

            effective[key] = {"value": value, "source": source}

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
    @require_admin
    def admin_settings_overrides_summary():
        settings_registry = _get_settings_registry()

        tab_name = (request.args.get("tab") or "downloads").strip()
        if not settings_registry.get_settings_tab(tab_name):
            return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

        overridable_keys = list(settings_registry.get_user_overridable_fields(tab_name=tab_name))
        keys_payload: dict[str, dict[str, Any]] = {}

        for user_record in user_db.list_users():
            user_settings = user_db.get_user_settings(user_record["id"])
            if not isinstance(user_settings, dict):
                continue

            for key in overridable_keys:
                if key not in user_settings or user_settings[key] is None:
                    continue
                entry = keys_payload.setdefault(key, {"count": 0, "users": []})
                entry["users"].append({
                    "userId": user_record["id"],
                    "username": user_record["username"],
                    "value": user_settings[key],
                })

        for summary in keys_payload.values():
            summary["count"] = len(summary["users"])

        return jsonify({"tab": tab_name, "keys": keys_payload})

    @app.route("/api/admin/users/<int:user_id>/effective-settings", methods=["GET"])
    @require_admin
    def admin_get_effective_settings(user_id):
        user = user_db.get_user(user_id=user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

        from shelfmark.core.config import config as app_config
        from shelfmark.core.settings_registry import is_value_from_env

        field_map = _get_settings_registry().get_user_overridable_fields()
        user_settings = user_db.get_user_settings(user_id)
        tab_config_cache: dict[str, dict[str, Any]] = {}
        effective: dict[str, dict[str, Any]] = {}

        for key, (field, tab_name) in sorted(field_map.items()):
            value = app_config.get(key, field.default, user_id=user_id)
            source = "default"

            if field.env_supported and is_value_from_env(field):
                source = "env_var"
            elif key in user_settings and user_settings[key] is not None:
                source = "user_override"
                value = user_settings[key]
            else:
                tab_config = tab_config_cache.setdefault(tab_name, load_config_file(tab_name))
                if key in tab_config:
                    source = "global_config"

            effective[key] = {"value": value, "source": source}

        return jsonify(effective)
