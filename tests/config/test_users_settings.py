"""Tests for users/request settings registration."""

import shelfmark.config.users_settings  # noqa: F401
from shelfmark.config import users_settings as users_settings_module
from shelfmark.core import settings_registry


def _field_map(tab_name: str):
    tab = settings_registry.get_settings_tab(tab_name)
    assert tab is not None
    return {field.key: field for field in tab.fields if hasattr(field, "key")}


def test_users_tab_is_renamed_to_users_and_requests():
    tab = settings_registry.get_settings_tab("users")
    assert tab is not None
    assert tab.display_name == "Users & Requests"


def test_users_tab_registers_request_fields():
    fields = _field_map("users")
    expected_keys = {
        "users_management",
        "VISIBLE_SELF_SETTINGS_SECTIONS",
        "REQUESTS_ENABLED",
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


def test_request_fields_are_user_overridable():
    overridable_map = settings_registry.get_user_overridable_fields(tab_name="users")
    expected_keys = {
        "REQUESTS_ENABLED",
        "MAX_PENDING_REQUESTS_PER_USER",
        "REQUESTS_ALLOW_NOTES",
    }
    assert expected_keys.issubset(set(overridable_map))
    assert "RESTRICT_SETTINGS_TO_ADMIN" not in overridable_map
    assert "VISIBLE_SELF_SETTINGS_SECTIONS" not in overridable_map


def test_visible_self_settings_sections_field_defaults_and_options():
    fields = _field_map("users")
    field = fields["VISIBLE_SELF_SETTINGS_SECTIONS"]

    assert field.default == ["delivery", "search", "notifications"]
    assert field.variant == "dropdown"
    assert field.env_supported is False
    assert field.options == [
        {
            "value": "delivery",
            "label": "Delivery Preferences",
            "description": "Show personal delivery output and destination settings.",
        },
        {
            "value": "search",
            "label": "Search Preferences",
            "description": "Show personal search mode and provider settings.",
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
    assert users_management.get_field_type() == "CustomComponentField"
    assert users_management.component == "users_management"


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


def test_on_save_users_normalizes_search_mode_override():
    result = users_settings_module._on_save_users({"SEARCH_MODE": " UNIVERSAL "})

    assert result["error"] is False
    assert result["values"]["SEARCH_MODE"] == "universal"


def test_on_save_users_rejects_invalid_metadata_provider_override(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.metadata_providers.is_provider_registered",
        lambda provider_name: provider_name == "openlibrary",
    )

    result = users_settings_module._on_save_users({"METADATA_PROVIDER": "unknown-provider"})

    assert result["error"] is True
    assert "METADATA_PROVIDER must be a valid metadata provider name or empty" in result["message"]


def test_on_save_users_rejects_invalid_default_release_source_override(monkeypatch):
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
            {
                "name": "audiobookbay",
                "display_name": "AudiobookBay",
                "enabled": True,
                "supported_content_types": ["audiobook"],
            },
        ],
    )

    result = users_settings_module._on_save_users({"DEFAULT_RELEASE_SOURCE": "unknown-source"})

    assert result["error"] is True
    assert (
        "DEFAULT_RELEASE_SOURCE must be a valid release source name or empty" in result["message"]
    )


def test_on_save_users_rejects_audiobook_only_source_for_book_default(monkeypatch):
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
                "name": "audiobookbay",
                "display_name": "AudiobookBay",
                "enabled": True,
                "supported_content_types": ["audiobook"],
            },
        ],
    )

    result = users_settings_module._on_save_users({"DEFAULT_RELEASE_SOURCE": "audiobookbay"})

    assert result["error"] is True
    assert (
        "DEFAULT_RELEASE_SOURCE must be a valid release source name or empty" in result["message"]
    )


def test_on_save_users_rejects_book_only_source_for_audiobook_default(monkeypatch):
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
                "name": "audiobookbay",
                "display_name": "AudiobookBay",
                "enabled": True,
                "supported_content_types": ["audiobook"],
            },
        ],
    )

    result = users_settings_module._on_save_users(
        {"DEFAULT_RELEASE_SOURCE_AUDIOBOOK": "direct_download"}
    )

    assert result["error"] is True
    assert (
        "DEFAULT_RELEASE_SOURCE_AUDIOBOOK must be a valid release source name or empty"
        in result["message"]
    )
