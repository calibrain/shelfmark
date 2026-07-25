"""Users settings tab registration.

This registers a 'users' tab in the settings sidebar.
The actual user management is handled by a custom frontend component
that talks to /api/admin/users endpoints.
"""

from typing import Any

from shelfmark.core.settings_registry import (
    CheckboxField,
    CustomComponentField,
    HeadingField,
    MultiSelectField,
    NumberField,
    SettingsField,
    register_on_save,
    register_settings,
)

_SELF_SETTINGS_SECTION_OPTIONS = [
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
_SELF_SETTINGS_SECTION_VALUES = {option["value"] for option in _SELF_SETTINGS_SECTION_OPTIONS}
_SELF_SETTINGS_SECTION_DEFAULTS = [option["value"] for option in _SELF_SETTINGS_SECTION_OPTIONS]
_SEARCH_MODE_VALUES = {"direct", "universal"}
_SEARCH_PREFERENCE_PROVIDER_KEYS = {
    "METADATA_PROVIDER",
    "METADATA_PROVIDER_AUDIOBOOK",
    "METADATA_PROVIDER_COMBINED",
}
_SEARCH_PREFERENCE_VALIDATABLE_KEYS = {
    "SEARCH_MODE",
    "DEFAULT_RELEASE_SOURCE",
    "DEFAULT_RELEASE_SOURCE_AUDIOBOOK",
    "SHOW_COMBINED_SELECTOR",
    "FORCE_COMBINED_SEARCH",
    *_SEARCH_PREFERENCE_PROVIDER_KEYS,
}

_USERS_HEADING_DESCRIPTION_BY_AUTH_MODE = {
    "builtin": (
        "Create and manage user accounts directly. Passwords are stored locally and users sign in "
        "with their username and password."
    ),
    "oidc": (
        "Users sign in through your identity provider. New accounts can be created automatically on "
        "first login when auto-provisioning is enabled, or you can pre-create users here and they\u2019ll "
        "be linked by email on first sign-in."
    ),
    "proxy": (
        "Users are authenticated by your reverse proxy. Accounts are automatically created on first "
        "sign-in. If a local user with a matching username already exists, it will be linked instead."
    ),
    "cwa": (
        "User accounts are synced from your Calibre-Web database. Users are matched by email, and new "
        "accounts are created here when new CWA users are found."
    ),
    "none": "Authentication is disabled. Anyone can access Shelfmark without signing in.",
    "default": "Authentication is disabled. Anyone can access Shelfmark without signing in.",
}


def _get_valid_release_source_names_for_content_type(content_type: str) -> set[str]:
    """Return registered release source names that support the requested content type."""
    from shelfmark.release_sources import list_available_sources

    valid_sources: set[str] = set()
    for source in list_available_sources():
        supported_types = source.get("supported_content_types", ["ebook", "audiobook"])
        if content_type in supported_types:
            valid_sources.add(source["name"])
    return valid_sources


def validate_search_preference_value(key: str, value: Any) -> tuple[Any, str | None]:
    """Validate and normalize a search preference value for user overrides."""
    if key not in _SEARCH_PREFERENCE_VALIDATABLE_KEYS:
        return value, None

    if value is None:
        return None, None

    normalized_value = str(value).strip()

    if key == "SEARCH_MODE":
        normalized_mode = normalized_value.lower()
        if normalized_mode not in _SEARCH_MODE_VALUES:
            return value, "SEARCH_MODE must be 'direct' or 'universal'"
        return normalized_mode, None

    if key in _SEARCH_PREFERENCE_PROVIDER_KEYS:
        if normalized_value == "":
            return "", None
        from shelfmark.metadata_providers import is_provider_registered

        if not is_provider_registered(normalized_value):
            return (
                value,
                f"{key} must be a valid metadata provider name or empty",
            )
        return normalized_value, None

    if key in {"DEFAULT_RELEASE_SOURCE", "DEFAULT_RELEASE_SOURCE_AUDIOBOOK"}:
        if normalized_value == "":
            return "", None
        valid_sources = _get_valid_release_source_names_for_content_type(
            "audiobook" if key == "DEFAULT_RELEASE_SOURCE_AUDIOBOOK" else "ebook"
        )
        if normalized_value not in valid_sources:
            return (
                value,
                f"{key} must be a valid release source name or empty",
            )
        return normalized_value, None

    if key == "SHOW_COMBINED_SELECTOR":
        if isinstance(value, bool):
            return value, None
        return bool(value), None

    if key == "FORCE_COMBINED_SEARCH":
        if isinstance(value, bool):
            return value, None
        return bool(value), None

    return value, None


def _on_save_users(values: dict[str, object]) -> dict[str, object]:
    """Validate users settings before persistence."""
    if "VISIBLE_SELF_SETTINGS_SECTIONS" in values:
        raw_sections = values["VISIBLE_SELF_SETTINGS_SECTIONS"]
        if raw_sections is None:
            candidate_sections: list[str] = []
        elif isinstance(raw_sections, str):
            candidate_sections = [s.strip() for s in raw_sections.split(",") if s.strip()]
        elif isinstance(raw_sections, (list, tuple, set)):
            candidate_sections = [
                str(section).strip() for section in raw_sections if str(section).strip()
            ]
        else:
            return {
                "error": True,
                "message": "VISIBLE_SELF_SETTINGS_SECTIONS must be a list of section identifiers",
                "values": values,
            }

        normalized_sections: list[str] = []
        for section in candidate_sections:
            if section not in _SELF_SETTINGS_SECTION_VALUES:
                allowed = ", ".join(sorted(_SELF_SETTINGS_SECTION_VALUES))
                return {
                    "error": True,
                    "message": (
                        "VISIBLE_SELF_SETTINGS_SECTIONS contains an unsupported section "
                        f"'{section}'. Supported values: {allowed}"
                    ),
                    "values": values,
                }
            if section not in normalized_sections:
                normalized_sections.append(section)

        values["VISIBLE_SELF_SETTINGS_SECTIONS"] = normalized_sections

    for key in _SEARCH_PREFERENCE_VALIDATABLE_KEYS:
        if key not in values:
            continue
        normalized_value, validation_error = validate_search_preference_value(key, values[key])
        if validation_error:
            return {
                "error": True,
                "message": validation_error,
                "values": values,
            }
        values[key] = normalized_value

    return {"error": False, "values": values}


register_on_save("users", _on_save_users)


@register_settings("users", "Users & Requests", icon="users", order=6)
def users_settings() -> list[SettingsField]:
    """User management tab - rendered as a custom component on the frontend."""
    return [
        HeadingField(
            key="users_heading",
            title="Users",
            description=_USERS_HEADING_DESCRIPTION_BY_AUTH_MODE["default"],
            description_by_auth_mode=_USERS_HEADING_DESCRIPTION_BY_AUTH_MODE,
        ),
        CustomComponentField(
            key="users_management",
            component="users_management",
        ),
        MultiSelectField(
            key="VISIBLE_SELF_SETTINGS_SECTIONS",
            label="Visible Self-Settings Sections",
            description=(
                "Choose which personal settings sections are shown in My Account for non-admin users."
            ),
            options=_SELF_SETTINGS_SECTION_OPTIONS,
            default=_SELF_SETTINGS_SECTION_DEFAULTS,
            variant="dropdown",
            env_supported=False,
        ),
        HeadingField(
            key="requests_heading",
            title="Requests",
            description=("Choose what users can download directly and what needs approval first."),
        ),
        CheckboxField(
            key="REQUESTS_ENABLED",
            label="Enable Requests",
            description=(
                "Turn this off to let everyone download directly without needing approval."
            ),
            default=False,
            user_overridable=True,
        ),
        NumberField(
            key="MAX_PENDING_REQUESTS_PER_USER",
            label="Max pending requests per user",
            description="How many open requests a user can have at a time.",
            default=20,
            min_value=1,
            max_value=1000,
            user_overridable=True,
            show_when={"field": "REQUESTS_ENABLED", "value": True},
        ),
        CheckboxField(
            key="REQUESTS_ALLOW_NOTES",
            label="Allow notes on requests",
            description="Let users add a note when they submit a request.",
            default=True,
            user_overridable=True,
            show_when={"field": "REQUESTS_ENABLED", "value": True},
        ),
    ]
