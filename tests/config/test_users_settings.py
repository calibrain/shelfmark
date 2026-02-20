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
        "users_management",
        "VISIBLE_SELF_SETTINGS_SECTIONS",
        "REQUESTS_ENABLED",
        "request_policy_editor",
        "MAX_PENDING_REQUESTS_PER_USER",
        "REQUESTS_ALLOW_NOTES",
    }
    assert expected_keys.issubset(set(fields))
    assert "REQUEST_POLICY_DEFAULT_EBOOK" not in fields
    assert "REQUEST_POLICY_DEFAULT_AUDIOBOOK" not in fields
    assert "REQUEST_POLICY_RULES" not in fields


def test_users_heading_contains_auth_mode_specific_descriptions():
    fields = _field_map("users")
    heading = fields["users_heading"]

    assert heading.description_by_auth_mode["builtin"] == (
        "Create and manage user accounts directly. Passwords are stored locally and users sign in "
        "with their username and password."
    )
    assert heading.description_by_auth_mode["oidc"] == (
        "Users sign in through your identity provider. New accounts can be created automatically on "
        "first login when auto-provisioning is enabled, or you can pre-create users here and they\u2019ll "
        "be linked by email on first sign-in."
    )
    assert heading.description_by_auth_mode["proxy"] == (
        "Users are authenticated by your reverse proxy. Accounts are automatically created on first "
        "sign-in. If a local user with a matching username already exists, it will be linked instead."
    )
    assert heading.description_by_auth_mode["cwa"] == (
        "User accounts are synced from your Calibre-Web database. Users are matched by email, and new "
        "accounts are created here when new CWA users are found."
    )
    assert heading.description_by_auth_mode["none"] == (
        "Authentication is disabled. Anyone can access Shelfmark without signing in."
    )


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
    assert "VISIBLE_SELF_SETTINGS_SECTIONS" not in overridable_map


def test_visible_self_settings_sections_field_defaults_and_options():
    fields = _field_map("users")
    field = fields["VISIBLE_SELF_SETTINGS_SECTIONS"]

    assert field.default == ["delivery", "notifications"]
    assert field.variant == "dropdown"
    assert field.env_supported is False
    assert field.options == [
        {
            "value": "delivery",
            "label": "Delivery Preferences",
            "description": "Show personal delivery output and destination settings.",
        },
        {
            "value": "notifications",
            "label": "Notifications",
            "description": "Show personal notification route settings.",
        },
    ]


def test_users_tab_registers_custom_components():
    fields = _field_map("users")

    users_management = fields["users_management"]
    request_policy_editor = fields["request_policy_editor"]

    assert users_management.get_field_type() == "CustomComponentField"
    assert users_management.component == "users_management"

    assert request_policy_editor.get_field_type() == "CustomComponentField"
    assert request_policy_editor.component == "request_policy_grid"
    assert request_policy_editor.wrap_in_field_wrapper is True
    assert request_policy_editor.get_bind_keys() == [
        "REQUEST_POLICY_DEFAULT_EBOOK",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK",
        "REQUEST_POLICY_RULES",
    ]
    assert [field.key for field in request_policy_editor.value_fields] == [
        "REQUEST_POLICY_DEFAULT_EBOOK",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK",
        "REQUEST_POLICY_RULES",
    ]
    assert request_policy_editor.show_when == {"field": "REQUESTS_ENABLED", "value": True}


def test_request_policy_raw_fields_are_scoped_to_custom_component():
    fields = _field_map("users")
    request_policy_editor = fields["request_policy_editor"]

    assert "REQUEST_POLICY_DEFAULT_EBOOK" not in fields
    assert "REQUEST_POLICY_DEFAULT_AUDIOBOOK" not in fields
    assert "REQUEST_POLICY_RULES" not in fields
    assert [field.key for field in request_policy_editor.value_fields] == [
        "REQUEST_POLICY_DEFAULT_EBOOK",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK",
        "REQUEST_POLICY_RULES",
    ]


def test_request_policy_rules_field_has_expected_columns():
    fields = _field_map("users")
    request_policy_editor = fields["request_policy_editor"]
    rules_field = next(
        field for field in request_policy_editor.value_fields if field.key == "REQUEST_POLICY_RULES"
    )

    columns = rules_field.columns() if callable(rules_field.columns) else rules_field.columns
    column_keys = [column["key"] for column in columns]
    assert column_keys == ["source", "content_type", "mode"]


def test_request_workflow_dependent_fields_are_gated_by_toggle():
    fields = _field_map("users")

    assert fields["MAX_PENDING_REQUESTS_PER_USER"].show_when == {
        "field": "REQUESTS_ENABLED",
        "value": True,
    }
    assert fields["REQUESTS_ALLOW_NOTES"].show_when == {
        "field": "REQUESTS_ENABLED",
        "value": True,
    }


def test_users_tab_serialization_scopes_request_policy_to_bound_fields():
    tab = settings_registry.get_settings_tab("users")
    assert tab is not None

    serialized_tab = settings_registry.serialize_tab(tab)
    serialized_fields = {field["key"]: field for field in serialized_tab["fields"]}

    assert "REQUEST_POLICY_DEFAULT_EBOOK" not in serialized_fields
    assert "REQUEST_POLICY_DEFAULT_AUDIOBOOK" not in serialized_fields
    assert "REQUEST_POLICY_RULES" not in serialized_fields

    request_policy_editor = serialized_fields["request_policy_editor"]
    bound_fields = request_policy_editor.get("boundFields", [])

    assert [field["key"] for field in bound_fields] == [
        "REQUEST_POLICY_DEFAULT_EBOOK",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK",
        "REQUEST_POLICY_RULES",
    ]
    assert all(field.get("hiddenInUi") is True for field in bound_fields)
    assert serialized_fields["users_heading"].get("descriptionByAuthMode", {}).get("builtin")


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
    # This test verifies dynamic source/content-type option wiring; keep mode-copy checks non-brittle.
    assert mode_options[0]["value"] == "download"
    assert mode_options[0]["label"] == "Download"
    assert isinstance(mode_options[0].get("description"), str) and mode_options[0]["description"].strip()
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
