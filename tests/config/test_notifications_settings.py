"""Tests for notifications settings registration and validation."""

import shelfmark.config.notifications_settings as notifications_settings_module
from shelfmark.core import settings_registry


def _field_map(tab_name: str):
    tab = settings_registry.get_settings_tab(tab_name)
    assert tab is not None
    return {field.key: field for field in tab.fields if hasattr(field, "key")}


def test_notifications_tab_registers_expected_fields():
    fields = _field_map("notifications")
    expected = {
        "notifications_heading",
        "ADMIN_NOTIFICATION_ROUTES",
        "test_admin_notification",
        "USER_NOTIFICATION_ROUTES",
    }
    assert expected.issubset(fields.keys())

    assert fields["USER_NOTIFICATION_ROUTES"].user_overridable is True
    assert fields["USER_NOTIFICATION_ROUTES"].hidden_in_ui is True


def test_on_save_notifications_rejects_invalid_urls(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.notifications_settings.load_config_file",
        lambda _tab: {},
    )

    result = notifications_settings_module._on_save_notifications(
        {
            "ADMIN_NOTIFICATION_ROUTES": [
                {"event": "all", "url": "not-a-valid-url"},
            ],
        }
    )

    assert result["error"] is True
    assert "invalid global notification URL" in result["message"]


def test_on_save_notifications_normalizes_routes(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.notifications_settings.load_config_file",
        lambda _tab: {},
    )

    values = {
        "ADMIN_NOTIFICATION_ROUTES": [
            {"event": "all", "url": " ntfys://ntfy.sh/shelfmark "},
            {"event": "request_created", "url": ""},
            {"event": "request_created", "url": "ntfys://ntfy.sh/requests"},
            {"event": "request_created", "url": "ntfys://ntfy.sh/requests"},
        ],
    }

    result = notifications_settings_module._on_save_notifications(values)

    assert result["error"] is False
    assert result["values"]["ADMIN_NOTIFICATION_ROUTES"] == [
        {"event": ["all"], "url": "ntfys://ntfy.sh/shelfmark"},
        {"event": ["request_created"], "url": ""},
        {"event": ["request_created"], "url": "ntfys://ntfy.sh/requests"},
    ]


def test_on_save_notifications_normalizes_multiselect_event_rows(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.notifications_settings.load_config_file",
        lambda _tab: {},
    )

    values = {
        "ADMIN_NOTIFICATION_ROUTES": [
            {"event": ["download_complete", "request_created"], "url": "ntfys://ntfy.sh/mixed"},
            {"event": ["request_created", "download_complete"], "url": "ntfys://ntfy.sh/mixed"},
            {"event": ["all", "download_failed"], "url": "ntfys://ntfy.sh/all"},
        ],
    }

    result = notifications_settings_module._on_save_notifications(values)

    assert result["error"] is False
    assert result["values"]["ADMIN_NOTIFICATION_ROUTES"] == [
        {"event": ["request_created", "download_complete"], "url": "ntfys://ntfy.sh/mixed"},
        {"event": ["all"], "url": "ntfys://ntfy.sh/all"},
    ]


def test_on_save_notifications_allows_empty_routes(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.notifications_settings.load_config_file",
        lambda _tab: {},
    )

    result = notifications_settings_module._on_save_notifications(
        {
            "ADMIN_NOTIFICATION_ROUTES": [{"event": "all", "url": ""}],
        }
    )

    assert result["error"] is False
    assert result["values"]["ADMIN_NOTIFICATION_ROUTES"] == [{"event": ["all"], "url": ""}]


def test_test_admin_notification_action_uses_current_unsaved_values(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.notifications_settings.load_config_file",
        lambda _tab: {"ADMIN_NOTIFICATION_ROUTES": []},
    )

    captured: dict[str, object] = {}

    def _fake_send_test_notification(urls):
        captured["urls"] = urls
        return {"success": True, "message": "ok"}

    monkeypatch.setattr(
        "shelfmark.config.notifications_settings.send_test_notification",
        _fake_send_test_notification,
    )

    result = notifications_settings_module._test_admin_notification_action(
        {
            "ADMIN_NOTIFICATION_ROUTES": [
                {"event": "all", "url": " ntfys://ntfy.sh/shelfmark "},
                {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
                {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
            ],
        }
    )

    assert result["success"] is True
    assert captured["urls"] == [
        "ntfys://ntfy.sh/shelfmark",
        "ntfys://ntfy.sh/errors",
    ]
