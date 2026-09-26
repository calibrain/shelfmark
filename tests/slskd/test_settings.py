"""Tests for the slskd settings tab and its Test Connection action."""

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    NumberField,
    PasswordField,
    TextField,
)
from shelfmark.release_sources.slskd.settings import (
    _test_slskd_connection,
    slskd_config_settings,
)


def _field(key: str):
    return next(f for f in slskd_config_settings() if getattr(f, "key", None) == key)


def test_settings_expose_expected_fields():
    assert isinstance(_field("SLSKD_ENABLED"), CheckboxField)
    assert isinstance(_field("SLSKD_URL"), TextField)
    assert isinstance(_field("SLSKD_API_KEY"), PasswordField)
    assert isinstance(_field("test_slskd"), ActionButton)
    assert isinstance(_field("SLSKD_DOWNLOAD_PATH"), TextField)
    assert isinstance(_field("SLSKD_SEARCH_TIMEOUT"), NumberField)
    assert isinstance(_field("SLSKD_RESPONSE_LIMIT"), NumberField)
    assert isinstance(_field("SLSKD_QUEUE_TIMEOUT_MINUTES"), NumberField)
    assert isinstance(_field("SLSKD_REMOVE_COMPLETED"), CheckboxField)


def test_dependent_fields_hide_until_enabled():
    for key in ("SLSKD_URL", "SLSKD_API_KEY", "test_slskd", "SLSKD_DOWNLOAD_PATH"):
        assert _field(key).show_when == {"field": "SLSKD_ENABLED", "value": True}


def test_defaults():
    assert _field("SLSKD_ENABLED").default is False
    assert _field("SLSKD_SEARCH_TIMEOUT").default == 15
    assert _field("SLSKD_RESPONSE_LIMIT").default == 100
    assert _field("SLSKD_QUEUE_TIMEOUT_MINUTES").default == 60
    assert _field("SLSKD_REMOVE_COMPLETED").default is True


def test_connection_action_uses_form_values(monkeypatch):
    import shelfmark.release_sources.slskd.api as api_module

    created: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, url, api_key):
            created.append((url, api_key))

        def test_connection(self):
            return True, "Connected to slskd 0.26.0 (logged in to Soulseek)"

    monkeypatch.setattr(api_module, "SlskdClient", FakeClient)

    result = _test_slskd_connection({"SLSKD_URL": "slskd:5030/", "SLSKD_API_KEY": " key "})

    assert result == {
        "success": True,
        "message": "Connected to slskd 0.26.0 (logged in to Soulseek)",
    }
    assert created == [("http://slskd:5030", "key")]


def test_connection_action_requires_url(monkeypatch):
    import shelfmark.release_sources.slskd.settings as mod

    monkeypatch.setattr(mod, "normalize_http_url", lambda url: url)
    from shelfmark.core.config import config

    monkeypatch.setattr(config, "get", lambda k, d=None: d)
    result = _test_slskd_connection({"SLSKD_URL": "", "SLSKD_API_KEY": "k"})
    assert result == {"success": False, "message": "slskd URL is required"}


def test_connection_action_requires_api_key(monkeypatch):
    from shelfmark.core.config import config

    monkeypatch.setattr(config, "get", lambda k, d=None: d)
    result = _test_slskd_connection({"SLSKD_URL": "http://slskd:5030", "SLSKD_API_KEY": ""})
    assert result == {"success": False, "message": "slskd API key is required"}


def test_connection_action_reports_failure(monkeypatch):
    import shelfmark.release_sources.slskd.api as api_module

    class FakeClient:
        def __init__(self, url, api_key):
            pass

        def test_connection(self):
            return False, "Invalid API key"

    monkeypatch.setattr(api_module, "SlskdClient", FakeClient)
    result = _test_slskd_connection({"SLSKD_URL": "http://slskd:5030", "SLSKD_API_KEY": "bad"})
    assert result == {"success": False, "message": "Invalid API key"}


def test_connection_action_wraps_unexpected_errors(monkeypatch):
    import shelfmark.release_sources.slskd.api as api_module

    class FakeClient:
        def __init__(self, url, api_key):
            raise RuntimeError("kaboom")

    monkeypatch.setattr(api_module, "SlskdClient", FakeClient)
    result = _test_slskd_connection({"SLSKD_URL": "http://slskd:5030", "SLSKD_API_KEY": "k"})
    assert result["success"] is False
    assert "kaboom" in result["message"]


def test_remote_path_mapping_offers_slskd_host():
    from shelfmark.config.settings import advanced_settings

    table = next(
        f for f in advanced_settings() if getattr(f, "key", None) == "PROWLARR_REMOTE_PATH_MAPPINGS"
    )
    host_column = next(c for c in table.columns if c["key"] == "host")
    assert {"value": "slskd", "label": "slskd"} in host_column["options"]
