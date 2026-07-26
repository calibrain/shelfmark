"""Administrator user-management settings tab."""

from shelfmark.core.settings_registry import (
    CustomComponentField,
    HeadingField,
    SettingsField,
    register_settings,
)

_USERS_HEADING_DESCRIPTION_BY_AUTH_MODE = {
    "builtin": "Create and manage local accounts and library access.",
    "oidc": "Manage provisioned accounts and library access.",
    "proxy": "Manage provisioned accounts and library access.",
    "cwa": "Manage synced accounts and library access.",
    "none": "Authentication is disabled. Anyone can access Shelfmark without signing in.",
    "default": "Authentication is disabled. Anyone can access Shelfmark without signing in.",
}


@register_settings("users", "Users", icon="users", order=6)
def users_settings() -> list[SettingsField]:
    """Register the administrator-only account management surface."""
    return [
        HeadingField(
            key="users_heading",
            title="Users",
            description=_USERS_HEADING_DESCRIPTION_BY_AUTH_MODE["default"],
            description_by_auth_mode=_USERS_HEADING_DESCRIPTION_BY_AUTH_MODE,
        ),
        CustomComponentField(key="users_management", component="users_management"),
    ]
