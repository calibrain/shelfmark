"""Tests for Config.get per-user override precedence."""

from types import SimpleNamespace

from shelfmark.core.config import config


class _DummyField:
    def __init__(self, env_supported: bool, user_overridable: bool):
        self.env_supported = env_supported
        self.user_overridable = user_overridable


def test_get_prefers_env_over_user_override(monkeypatch):
    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"DESTINATION": "/env/books"})
    monkeypatch.setattr(
        config,
        "_field_map",
        {"DESTINATION": (_DummyField(env_supported=True, user_overridable=True), "downloads")},
    )
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "/user/books")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: True),
    )

    assert config.get("DESTINATION", "/default", user_id=10) == "/env/books"


def test_get_uses_user_override_when_not_env(monkeypatch):
    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"DESTINATION": "/global/books"})
    monkeypatch.setattr(
        config,
        "_field_map",
        {"DESTINATION": (_DummyField(env_supported=True, user_overridable=True), "downloads")},
    )
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "/user/books")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: False),
    )

    assert config.get("DESTINATION", "/default", user_id=10) == "/user/books"


def test_get_ignores_user_override_for_non_overridable_field(monkeypatch):
    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"FILE_ORGANIZATION": "rename"})
    monkeypatch.setattr(
        config,
        "_field_map",
        {"FILE_ORGANIZATION": (_DummyField(env_supported=True, user_overridable=False), "downloads")},
    )
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "organize")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: False),
    )

    assert config.get("FILE_ORGANIZATION", "rename", user_id=10) == "rename"


def test_get_respects_empty_user_override_for_destination_audiobook(monkeypatch):
    monkeypatch.setattr(config, "_ensure_loaded", lambda: None)
    monkeypatch.setattr(config, "_cache", {"DESTINATION_AUDIOBOOK": "/global/audiobooks"})
    monkeypatch.setattr(
        config,
        "_field_map",
        {"DESTINATION_AUDIOBOOK": (_DummyField(env_supported=True, user_overridable=True), "downloads")},
    )
    monkeypatch.setattr(config, "_get_user_override", lambda user_id, key: "")
    monkeypatch.setattr(
        "shelfmark.core.config._get_registry",
        lambda: SimpleNamespace(is_value_from_env=lambda field: False),
    )

    assert config.get("DESTINATION_AUDIOBOOK", "/default", user_id=10) == ""
