"""Password field values are masked in the API and never overwritten by onboarding."""

from shelfmark.core.settings_registry import (
    PasswordField,
    SettingsTab,
    TextField,
    serialize_field,
    serialize_tab,
)


def _settings_tab(monkeypatch, password_value="super-secret", text_value="stored-text"):
    """Build a tab with a TextField and a PasswordField, stubbing value resolution."""
    tab = SettingsTab(
        name="test_tab",
        display_name="Test Tab",
        fields=[
            TextField(key="TEXT_KEY", label="Text", default=""),
            PasswordField(key="PASS_KEY", label="Password"),
        ],
    )

    def _fake_get_setting_value(field, tab_name):
        if isinstance(field, PasswordField):
            return password_value
        if isinstance(field, TextField):
            return text_value
        return field.default

    monkeypatch.setattr(
        "shelfmark.core.settings_registry.get_setting_value", _fake_get_setting_value
    )
    return tab


def test_serialize_password_field_returns_empty_value(monkeypatch):
    tab = _settings_tab(monkeypatch, password_value="super-secret")
    field = tab.fields[1]

    serialized = serialize_field(field, "test_tab", include_value=True)

    assert serialized["key"] == "PASS_KEY"
    assert serialized["value"] == ""
    assert serialized["fromEnv"] is False


def test_serialize_text_field_still_returns_stored_value(monkeypatch):
    tab = _settings_tab(monkeypatch, text_value="stored-text")
    field = tab.fields[0]

    serialized = serialize_field(field, "test_tab", include_value=True)

    assert serialized["key"] == "TEXT_KEY"
    assert serialized["value"] == "stored-text"


def test_serialize_tab_masks_password_value(monkeypatch):
    tab = _settings_tab(monkeypatch, password_value="super-secret")

    serialized = serialize_tab(tab)
    fields = {field["key"]: field for field in serialized["fields"]}

    assert fields["PASS_KEY"]["value"] == ""
    assert fields["TEXT_KEY"]["value"] == "stored-text"


def _stub_onboarding_save(monkeypatch):
    import shelfmark.core.config as app_config
    import shelfmark.core.onboarding as onboarding

    save_calls: list[tuple[str, dict[str, object]]] = []

    def _fake_save(tab_name, values):
        save_calls.append((tab_name, dict(values)))
        return True

    monkeypatch.setattr(onboarding, "save_config_file", _fake_save)
    monkeypatch.setattr(onboarding, "mark_onboarding_complete", lambda: True)
    monkeypatch.setattr(app_config.config, "refresh", lambda: None, raising=False)

    return save_calls


def test_save_onboarding_skips_empty_password_value(monkeypatch):
    import shelfmark.core.onboarding as onboarding

    save_calls = _stub_onboarding_save(monkeypatch)

    password_field = PasswordField(key="PASS_KEY", label="Password")
    tab = SettingsTab(
        name="test_tab",
        display_name="Test Tab",
        fields=[password_field],
    )

    monkeypatch.setattr(
        onboarding,
        "get_onboarding_steps",
        lambda: [
            {
                "id": "test_step",
                "title": "Test Step",
                "tab": "test_tab",
                "get_fields": lambda: [password_field],
            }
        ],
    )
    monkeypatch.setattr(
        onboarding,
        "get_settings_tab",
        lambda name: tab if name == "test_tab" else None,
    )

    result = onboarding.save_onboarding_settings(
        {
            "SEARCH_MODE": "direct",
            "PASS_KEY": "",
        }
    )

    assert result == {"success": True, "message": "Onboarding complete!"}
    assert not any("PASS_KEY" in payload for _, payload in save_calls)


def test_save_onboarding_writes_non_empty_password_value(monkeypatch):
    import shelfmark.core.onboarding as onboarding

    save_calls = _stub_onboarding_save(monkeypatch)

    password_field = PasswordField(key="PASS_KEY", label="Password")
    tab = SettingsTab(
        name="test_tab",
        display_name="Test Tab",
        fields=[password_field],
    )

    monkeypatch.setattr(
        onboarding,
        "get_onboarding_steps",
        lambda: [
            {
                "id": "test_step",
                "title": "Test Step",
                "tab": "test_tab",
                "get_fields": lambda: [password_field],
            }
        ],
    )
    monkeypatch.setattr(
        onboarding,
        "get_settings_tab",
        lambda name: tab if name == "test_tab" else None,
    )

    result = onboarding.save_onboarding_settings(
        {
            "SEARCH_MODE": "direct",
            "PASS_KEY": "new-secret",
        }
    )

    assert result == {"success": True, "message": "Onboarding complete!"}
    assert any(payload.get("PASS_KEY") == "new-secret" for _, payload in save_calls)
