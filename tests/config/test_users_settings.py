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
        "request_auto_selection_heading",
        "REQUEST_AUTO_SELECT_ENABLED",
        "REQUEST_AUTO_APPROVE_ENABLED",
        "REQUEST_AUTO_PREFERRED_SOURCE",
        "REQUEST_AUTO_PREFERRED_INDEXER",
        "REQUEST_AUTO_CONTENT_TYPES",
        "REQUEST_AUTO_FORMATS",
        "REQUEST_AUTO_SELECTION_POLICY",
        "REQUEST_AUTO_FALLBACK_STRATEGY",
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
        "REQUEST_AUTO_SELECT_ENABLED",
        "REQUEST_AUTO_APPROVE_ENABLED",
        "REQUEST_AUTO_PREFERRED_SOURCE",
        "REQUEST_AUTO_PREFERRED_INDEXER",
        "REQUEST_AUTO_CONTENT_TYPES",
        "REQUEST_AUTO_FORMATS",
        "REQUEST_AUTO_SELECTION_POLICY",
        "REQUEST_AUTO_FALLBACK_STRATEGY",
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
    assert fields["request_auto_selection_heading"].show_when == {
        "field": "REQUESTS_ENABLED",
        "value": True,
    }
    assert fields["REQUEST_AUTO_SELECT_ENABLED"].show_when == {
        "field": "REQUESTS_ENABLED",
        "value": True,
    }
    assert fields["REQUEST_AUTO_APPROVE_ENABLED"].show_when == [
        {"field": "REQUESTS_ENABLED", "value": True},
        {"field": "REQUEST_AUTO_SELECT_ENABLED", "value": True},
    ]
    assert fields["REQUEST_AUTO_PREFERRED_INDEXER"].show_when == [
        {"field": "REQUESTS_ENABLED", "value": True},
        {"field": "REQUEST_AUTO_SELECT_ENABLED", "value": True},
        {"field": "REQUEST_AUTO_PREFERRED_SOURCE", "value": "prowlarr"},
    ]


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
    assert serialized_fields["REQUEST_AUTO_SELECT_ENABLED"]["userOverridable"] is True
    assert serialized_fields["REQUEST_AUTO_SELECTION_POLICY"]["default"] == "best_match"


def test_request_auto_selection_options_are_dynamic(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.release_sources.list_available_sources",
        lambda: [
            {"name": "direct_download", "display_name": "Direct Download", "enabled": True},
            {"name": "prowlarr", "display_name": "Prowlarr", "enabled": True},
        ],
    )
    monkeypatch.setattr(
        "shelfmark.config.users_settings.get_available_request_auto_indexers",
        lambda: ["MyAnonamouse", "Readarr"],
    )

    assert users_settings_module._get_request_auto_source_options() == [
        {
            "value": "",
            "label": "Use request/default source",
            "description": (
                "Prefer the request source first, then the default release source for that content type."
            ),
        },
        {"value": "direct_download", "label": "Direct Download"},
        {"value": "prowlarr", "label": "Prowlarr"},
    ]
    assert users_settings_module._get_request_auto_indexer_options() == [
        {
            "value": "",
            "label": "Any indexer",
            "description": "Use any enabled indexer within the preferred source.",
        },
        {"value": "MyAnonamouse", "label": "MyAnonamouse"},
        {"value": "Readarr", "label": "Readarr"},
    ]
    assert users_settings_module._get_request_auto_format_options()[0] == {
        "value": "epub",
        "label": "EPUB",
    }


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
            {
                "name": "irc",
                "display_name": "IRC",
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
        {"value": "irc", "label": "IRC"},
    ]

    content_type_column = columns[1]
    content_type_options = content_type_column["options"]
    assert content_type_column["filterByField"] == "source"

    assert {"value": "ebook", "label": "Ebook", "childOf": "direct_download"} in content_type_options
    assert {"value": "ebook", "label": "Ebook", "childOf": "prowlarr"} in content_type_options
    assert {"value": "audiobook", "label": "Audiobook", "childOf": "prowlarr"} in content_type_options
    assert {"value": "ebook", "label": "Ebook", "childOf": "irc"} in content_type_options
    assert {"value": "audiobook", "label": "Audiobook", "childOf": "irc"} in content_type_options
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


def test_on_save_users_normalizes_request_auto_selection_settings(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.core.request_auto_selection.get_available_request_auto_sources",
        lambda: {"prowlarr", "direct_download"},
    )
    monkeypatch.setattr(
        "shelfmark.core.request_auto_selection.get_available_request_auto_indexers",
        lambda: ["MyAnonamouse"],
    )

    result = users_settings_module._on_save_users(
        {
            "REQUEST_AUTO_SELECT_ENABLED": "true",
            "REQUEST_AUTO_APPROVE_ENABLED": "1",
            "REQUEST_AUTO_PREFERRED_SOURCE": " Prowlarr ",
            "REQUEST_AUTO_PREFERRED_INDEXER": "MyAnonamouse",
            "REQUEST_AUTO_CONTENT_TYPES": ["ebook", "Audiobook"],
            "REQUEST_AUTO_FORMATS": ["EPUB", "pdf"],
            "REQUEST_AUTO_SELECTION_POLICY": "MOST_SEEDERS",
            "REQUEST_AUTO_FALLBACK_STRATEGY": "SAME_SOURCE_THEN_ANY_SOURCE",
        }
    )

    assert result["error"] is False
    assert result["values"]["REQUEST_AUTO_SELECT_ENABLED"] is True
    assert result["values"]["REQUEST_AUTO_APPROVE_ENABLED"] is True
    assert result["values"]["REQUEST_AUTO_PREFERRED_SOURCE"] == "prowlarr"
    assert result["values"]["REQUEST_AUTO_PREFERRED_INDEXER"] == "MyAnonamouse"
    assert result["values"]["REQUEST_AUTO_CONTENT_TYPES"] == ["ebook", "audiobook"]
    assert result["values"]["REQUEST_AUTO_FORMATS"] == ["epub", "pdf"]
    assert result["values"]["REQUEST_AUTO_SELECTION_POLICY"] == "most_seeders"
    assert result["values"]["REQUEST_AUTO_FALLBACK_STRATEGY"] == "same_source_then_any_source"


def test_on_save_users_rejects_request_auto_indexer_without_prowlarr(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.core.request_auto_selection.get_available_request_auto_sources",
        lambda: {"prowlarr", "direct_download"},
    )

    result = users_settings_module._on_save_users(
        {
            "REQUEST_AUTO_PREFERRED_SOURCE": "direct_download",
            "REQUEST_AUTO_PREFERRED_INDEXER": "MyAnonamouse",
        }
    )

    assert result["error"] is True
    assert "only supported when REQUEST_AUTO_PREFERRED_SOURCE is Prowlarr" in result["message"]


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
    assert "DEFAULT_RELEASE_SOURCE must be a valid release source name or empty" in result["message"]


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
    assert "DEFAULT_RELEASE_SOURCE must be a valid release source name or empty" in result["message"]


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
    assert "DEFAULT_RELEASE_SOURCE_AUDIOBOOK must be a valid release source name or empty" in result["message"]
