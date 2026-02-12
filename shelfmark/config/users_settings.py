"""Users settings tab registration.

This registers a 'users' tab in the settings sidebar.
The actual user management is handled by a custom frontend component
that talks to /api/admin/users endpoints.
"""

from shelfmark.core.settings_registry import (
    CheckboxField,
    HeadingField,
    register_settings,
)


@register_settings("users", "Users", icon="users", order=6)
def users_settings():
    """User management tab - rendered as a custom component on the frontend."""
    return [
        HeadingField(
            key="users_access_heading",
            title="Options",
        ),
        CheckboxField(
            key="RESTRICT_SETTINGS_TO_ADMIN",
            label="Restrict Settings and Onboarding to Admins",
            description=(
                "When enabled, only admin users can access Settings and Onboarding. "
                "When disabled, any authenticated user can access them. "
                "Security and Users are always admin-only."
            ),
            default=True,
            env_supported=False,
        ),
    ]
