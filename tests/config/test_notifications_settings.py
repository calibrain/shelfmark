"""Tests for administrator notification target settings."""

import shelfmark.config.notifications_settings as notifications_settings_module
from shelfmark.core import settings_registry


def test_notifications_tab_exposes_only_administrator_operational_targets():
    tab = settings_registry.get_settings_tab("notifications")
    assert tab is not None
    fields = {field.key: field for field in tab.fields if hasattr(field, "key")}
    assert set(fields) == {
        "notifications_heading",
        "NOTIFICATION_BASE_URL",
        "ADMIN_NOTIFICATION_TARGETS",
        "test_admin_notification",
    }
    events = fields["ADMIN_NOTIFICATION_TARGETS"].columns[2]["options"]
    assert [event["value"] for event in events] == [
        "request_created",
        "download_complete",
        "download_failed",
    ]


def test_on_save_normalizes_email_and_apprise_targets(monkeypatch):
    monkeypatch.setattr(notifications_settings_module, "load_config_file", lambda _tab: {})
    result = notifications_settings_module._on_save_notifications(
        {
            "ADMIN_NOTIFICATION_TARGETS": [
                {
                    "transport": "email",
                    "destination": " admin@example.com ",
                    "events": ["request_created", "request_created"],
                },
                {
                    "transport": "apprise",
                    "destination": " ntfys://ntfy.sh/ops ",
                    "events": ["download_failed"],
                },
            ]
        }
    )
    assert result == {
        "error": False,
        "values": {
            "ADMIN_NOTIFICATION_TARGETS": [
                {
                    "transport": "email",
                    "destination": "admin@example.com",
                    "events": ["request_created"],
                },
                {
                    "transport": "apprise",
                    "destination": "ntfys://ntfy.sh/ops",
                    "events": ["download_failed"],
                },
            ]
        },
    }


def test_on_save_rejects_invalid_target_destination(monkeypatch):
    monkeypatch.setattr(notifications_settings_module, "load_config_file", lambda _tab: {})
    result = notifications_settings_module._on_save_notifications(
        {
            "ADMIN_NOTIFICATION_TARGETS": [
                {"transport": "apprise", "destination": "not a url", "events": []}
            ]
        }
    )
    assert result["error"] is True
