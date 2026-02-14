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
        "notifications_help",
        "NOTIFICATIONS_ENABLED",
        "ADMIN_NOTIFICATION_URLS",
        "ADMIN_NOTIFICATION_EVENTS",
        "test_admin_notification",
    }
    assert expected.issubset(fields.keys())


def test_on_save_notifications_rejects_invalid_urls(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.notifications_settings.load_config_file",
        lambda _tab: {},
    )

    result = notifications_settings_module._on_save_notifications(
        {
            "NOTIFICATIONS_ENABLED": True,
            "ADMIN_NOTIFICATION_URLS": ["not-a-valid-url"],
            "ADMIN_NOTIFICATION_EVENTS": ["request_created"],
        }
    )

    assert result["error"] is True
    assert "invalid notification URL" in result["message"]


def test_on_save_notifications_normalizes_urls_and_events(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.notifications_settings.load_config_file",
        lambda _tab: {},
    )

    values = {
        "NOTIFICATIONS_ENABLED": True,
        "ADMIN_NOTIFICATION_URLS": [
            " discord://Webhook/Token ",
            "",
            "discord://Webhook/Token",
            "ntfys://ntfy.sh/shelfmark",
        ],
        "ADMIN_NOTIFICATION_EVENTS": [
            "download_failed",
            "invalid_event",
            "request_created",
            "download_failed",
        ],
    }

    result = notifications_settings_module._on_save_notifications(values)

    assert result["error"] is False
    assert result["values"]["ADMIN_NOTIFICATION_URLS"] == [
        "discord://Webhook/Token",
        "ntfys://ntfy.sh/shelfmark",
    ]
    assert result["values"]["ADMIN_NOTIFICATION_EVENTS"] == [
        "request_created",
        "download_failed",
    ]


def test_on_save_notifications_requires_event_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.notifications_settings.load_config_file",
        lambda _tab: {},
    )

    result = notifications_settings_module._on_save_notifications(
        {
            "NOTIFICATIONS_ENABLED": True,
            "ADMIN_NOTIFICATION_URLS": ["discord://Webhook/Token"],
            "ADMIN_NOTIFICATION_EVENTS": [],
        }
    )

    assert result["error"] is True
    assert "Select at least one notification event" in result["message"]


def test_test_admin_notification_action_uses_current_unsaved_values(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.notifications_settings.load_config_file",
        lambda _tab: {"NOTIFICATIONS_ENABLED": False, "ADMIN_NOTIFICATION_URLS": []},
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
            "NOTIFICATIONS_ENABLED": True,
            "ADMIN_NOTIFICATION_URLS": [" ntfys://ntfy.sh/shelfmark "],
        }
    )

    assert result["success"] is True
    assert captured["urls"] == ["ntfys://ntfy.sh/shelfmark"]

