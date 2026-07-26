"""Tests for the administrator user-management settings tab."""

import shelfmark.config.users_settings  # noqa: F401
from shelfmark.core import settings_registry


def test_users_tab_contains_only_administrator_management_surface():
    tab = settings_registry.get_settings_tab("users")
    assert tab is not None
    assert tab.display_name == "Users"
    assert {field.key for field in tab.fields} == {"users_heading", "users_management"}
    assert settings_registry.get_user_overridable_fields(tab_name="users") == {}
