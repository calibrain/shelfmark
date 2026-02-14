"""Tests for users/request settings registration."""

from shelfmark.config import users_settings as users_settings_module
import shelfmark.config.users_settings  # noqa: F401
from shelfmark.core import settings_registry


def _field_map(tab_name: str):
    tab = settings_registry.get_settings_tab(tab_name)
    assert tab is not None
    return {field.key: field for field in tab.fields if hasattr(field, "key")}


def test_users_tab_is_renamed_to_users_and_requests():
    tab = settings_registry.get_settings_tab("users")
    assert tab is not None
    assert tab.display_name == "Users & Requests"


def test_users_tab_registers_request_policy_fields():
    fields = _field_map("users")
    expected_keys = {
        "REQUESTS_ENABLED",
        "REQUEST_POLICY_DEFAULT_EBOOK",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK",
        "REQUEST_POLICY_RULES",
        "MAX_PENDING_REQUESTS_PER_USER",
        "REQUESTS_ALLOW_NOTES",
    }
    assert expected_keys.issubset(set(fields))


def test_request_policy_fields_are_user_overridable():
    overridable_map = settings_registry.get_user_overridable_fields(tab_name="users")
    expected_keys = {
        "REQUESTS_ENABLED",
        "REQUEST_POLICY_DEFAULT_EBOOK",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK",
        "REQUEST_POLICY_RULES",
        "MAX_PENDING_REQUESTS_PER_USER",
        "REQUESTS_ALLOW_NOTES",
    }
    assert expected_keys.issubset(set(overridable_map))
    assert "RESTRICT_SETTINGS_TO_ADMIN" not in overridable_map


def test_request_policy_rules_field_has_expected_columns():
    fields = _field_map("users")
    rules_field = fields["REQUEST_POLICY_RULES"]

    columns = rules_field.columns() if callable(rules_field.columns) else rules_field.columns
    column_keys = [column["key"] for column in columns]
    assert column_keys == ["source", "content_type", "mode"]


def test_request_policy_rules_source_options_are_dynamic(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.release_sources.list_available_sources",
        lambda: [
            {
                "name": "direct_download",
                "display_name": "Direct Download",
                "enabled": True,
                "supported_content_types": ["ebook"],
            },
            {
                "name": "prowlarr",
                "display_name": "Prowlarr",
                "enabled": True,
                "supported_content_types": ["ebook", "audiobook"],
            },
        ],
    )

    columns = users_settings_module._get_request_policy_rule_columns()
    source_options = columns[0]["options"]

    assert source_options == [
        {"value": "direct_download", "label": "Direct Download"},
        {"value": "prowlarr", "label": "Prowlarr"},
    ]

    content_type_column = columns[1]
    content_type_options = content_type_column["options"]
    assert content_type_column["filterByField"] == "source"

    assert {"value": "ebook", "label": "Ebook", "childOf": "direct_download"} in content_type_options
    assert {"value": "ebook", "label": "Ebook", "childOf": "prowlarr"} in content_type_options
    assert {"value": "audiobook", "label": "Audiobook", "childOf": "prowlarr"} in content_type_options
    assert {"value": "*", "label": "Any Type (*)", "childOf": "prowlarr"} not in content_type_options
    assert {"value": "*", "label": "Any Type (*)", "childOf": "direct_download"} not in content_type_options

    mode_options = columns[2]["options"]
    assert mode_options[0] == {"value": "download", "label": "Download", "description": "Allow direct downloads."}
    assert {opt["value"] for opt in mode_options} == {"download", "request_release", "blocked"}


def test_on_save_users_rejects_unsupported_source_content_type_pair(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.users_settings.validate_policy_rules",
        lambda rules: (
            [],
            ["Rule 1: source 'direct_download' does not support content_type 'audiobook'"],
        ),
    )

    result = users_settings_module._on_save_users(
        {
            "REQUEST_POLICY_RULES": [
                {
                    "source": "direct_download",
                    "content_type": "audiobook",
                    "mode": "request_release",
                }
            ]
        }
    )

    assert result["error"] is True
    assert "does not support content_type" in result["message"]


def test_on_save_users_rejects_blank_source_rule():
    result = users_settings_module._on_save_users(
        {
            "REQUEST_POLICY_RULES": [
                {
                    "source": "",
                    "content_type": "ebook",
                    "mode": "request_release",
                }
            ]
        }
    )

    assert result["error"] is True
    assert "source is required" in result["message"]


def test_on_save_users_rejects_blank_content_type_rule():
    result = users_settings_module._on_save_users(
        {
            "REQUEST_POLICY_RULES": [
                {
                    "source": "direct_download",
                    "content_type": "",
                    "mode": "request_release",
                }
            ]
        }
    )

    assert result["error"] is True
    assert "content_type is required" in result["message"]


def test_on_save_users_rejects_blank_mode_rule():
    result = users_settings_module._on_save_users(
        {
            "REQUEST_POLICY_RULES": [
                {
                    "source": "direct_download",
                    "content_type": "ebook",
                    "mode": "",
                }
            ]
        }
    )

    assert result["error"] is True
    assert "mode is required" in result["message"]


def test_on_save_users_normalizes_rules(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.config.users_settings.validate_policy_rules",
        lambda rules: (
            [
                {"source": "direct_download", "content_type": "ebook", "mode": "request_release"},
            ],
            [],
        ),
    )

    result = users_settings_module._on_save_users(
        {
            "REQUEST_POLICY_RULES": [
                {
                    "source": "DIRECT_DOWNLOAD",
                    "content_type": "BOOK",
                    "mode": "REQUEST_RELEASE",
                }
            ]
        }
    )

    assert result["error"] is False
    assert result["values"]["REQUEST_POLICY_RULES"] == [
        {"source": "direct_download", "content_type": "ebook", "mode": "request_release"},
    ]
