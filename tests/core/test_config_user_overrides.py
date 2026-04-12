"""Tests for user override precedence and effective user-preference payloads."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from shelfmark.core.config import config


def _download_field(key: str):
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core import settings_registry

    return settings_registry.get_settings_field_map()[key][0]


def test_get_prefers_env_over_user_override(monkeypatch):
    field = _download_field("DESTINATION")

    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"DESTINATION": "/env/books"})
    monkeypatch.setattr(config, "_field_map", {"DESTINATION": (field, "downloads")})
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "/user/books")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: True),
    )

    assert config.get("DESTINATION", "/default", user_id=10) == "/env/books"


def test_get_uses_user_override_when_not_from_env(monkeypatch):
    field = _download_field("DESTINATION")

    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"DESTINATION": "/global/books"})
    monkeypatch.setattr(config, "_field_map", {"DESTINATION": (field, "downloads")})
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "/user/books")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: False),
    )

    assert config.get("DESTINATION", "/default", user_id=10) == "/user/books"


def test_get_ignores_user_override_for_non_overridable_field(monkeypatch):
    field = _download_field("FILE_ORGANIZATION")

    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"FILE_ORGANIZATION": "rename"})
    monkeypatch.setattr(
        config,
        "_field_map",
        {"FILE_ORGANIZATION": (field, "downloads")},
    )
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "organize")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: False),
    )

    assert config.get("FILE_ORGANIZATION", "rename", user_id=10) == "rename"


def test_get_keeps_empty_string_override(monkeypatch):
    field = _download_field("DESTINATION_AUDIOBOOK")

    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"DESTINATION_AUDIOBOOK": "/global/audiobooks"})
    monkeypatch.setattr(
        config,
        "_field_map",
        {"DESTINATION_AUDIOBOOK": (field, "downloads")},
    )
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: False),
    )

    assert config.get("DESTINATION_AUDIOBOOK", "/default", user_id=10) == ""


def test_build_user_preferences_payload_reports_effective_sources(monkeypatch):
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core import settings_registry
    from shelfmark.core.user_settings_overrides import build_user_preferences_payload

    user_db = SimpleNamespace(
        get_user_settings=lambda user_id: {
            "DESTINATION": "/user/books",
        }
    )

    def fake_get(key, default=None, user_id=None):
        values = {
            "DESTINATION": "/global/books",
            "BOOKS_OUTPUT_MODE": "folder",
            "EMAIL_RECIPIENT": "global@example.com",
        }
        return values.get(key, default)

    with (
        patch(
            "shelfmark.core.user_settings_overrides.get_settings_registry",
            return_value=settings_registry,
        ),
        patch(
            "shelfmark.core.user_settings_overrides.load_config_file",
            return_value={
                "DESTINATION": "/global/books",
                "BOOKS_OUTPUT_MODE": "folder",
                "EMAIL_RECIPIENT": "global@example.com",
            },
        ),
        patch.object(config, "get", side_effect=fake_get),
        patch.object(
            settings_registry,
            "is_value_from_env",
            side_effect=lambda field: field.key == "BOOKS_OUTPUT_MODE",
        ),
    ):
        payload = build_user_preferences_payload(user_db, 7, "downloads")

    assert payload["tab"] == "downloads"
    assert payload["userOverrides"] == {"DESTINATION": "/user/books"}
    assert payload["globalValues"]["DESTINATION"] == "/global/books"
    assert payload["effective"]["DESTINATION"] == {
        "value": "/user/books",
        "source": "user_override",
    }
    assert payload["effective"]["BOOKS_OUTPUT_MODE"] == {
        "value": "folder",
        "source": "env_var",
    }
    assert payload["effective"]["EMAIL_RECIPIENT"] == {
        "value": "global@example.com",
        "source": "global_config",
    }

    fields_by_key = {field["key"]: field for field in payload["fields"]}
    assert fields_by_key["DESTINATION"]["fromEnv"] is False
    assert fields_by_key["BOOKS_OUTPUT_MODE"]["fromEnv"] is True
