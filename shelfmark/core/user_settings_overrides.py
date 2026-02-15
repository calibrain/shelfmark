"""Shared helpers for user-overridable settings metadata and payloads."""

from typing import Any

from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.user_db import UserDB


def get_settings_registry():
    # Ensure settings modules are loaded before reading registry metadata.
    import shelfmark.config.settings  # noqa: F401
    import shelfmark.config.security  # noqa: F401
    import shelfmark.config.notifications_settings  # noqa: F401
    import shelfmark.config.users_settings  # noqa: F401
    from shelfmark.core import settings_registry

    return settings_registry


def get_ordered_user_overridable_fields(tab_name: str) -> list[tuple[str, Any]]:
    settings_registry = get_settings_registry()
    tab = settings_registry.get_settings_tab(tab_name)
    if not tab:
        return []
    overridable_map = settings_registry.get_user_overridable_fields(tab_name=tab_name)
    return [(field.key, field) for field in tab.fields if field.key in overridable_map]


def build_user_preferences_payload(user_db: UserDB, user_id: int, tab_name: str) -> dict[str, Any]:
    from shelfmark.core.config import config as app_config

    settings_registry = get_settings_registry()
    ordered_fields = get_ordered_user_overridable_fields(tab_name)
    if not ordered_fields:
        tab_label = tab_name.capitalize()
        raise ValueError(f"{tab_label} settings tab not found")

    tab_config = load_config_file(tab_name)
    user_settings = user_db.get_user_settings(user_id)
    ordered_keys = [key for key, _ in ordered_fields]

    fields_payload: list[dict[str, Any]] = []
    global_values: dict[str, Any] = {}
    effective: dict[str, dict[str, Any]] = {}

    for key, field in ordered_fields:
        serialized = settings_registry.serialize_field(field, tab_name, include_value=False)
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
        elif key in tab_config:
            source = "global_config"

        effective[key] = {"value": value, "source": source}

    user_overrides = {
        key: user_settings[key]
        for key in ordered_keys
        if key in user_settings and user_settings[key] is not None
    }

    return {
        "tab": tab_name,
        "keys": ordered_keys,
        "fields": fields_payload,
        "globalValues": global_values,
        "userOverrides": user_overrides,
        "effective": effective,
    }
