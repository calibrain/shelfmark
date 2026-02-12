"""Users settings tab registration.

This registers a 'users' tab in the settings sidebar.
The actual user management is handled by a custom frontend component
that talks to /api/admin/users endpoints.
"""

from shelfmark.core.settings_registry import (
    HeadingField,
    register_settings,
)


@register_settings("users", "Users", icon="users", order=6)
def users_settings():
    """User management tab - rendered as a custom component on the frontend."""
    return [
        HeadingField(
            key="users_heading",
            title="User Accounts",
            description="Manage user accounts for multi-user authentication.",
        ),
    ]
