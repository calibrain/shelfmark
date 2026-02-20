"""Users settings tab registration.

This registers a 'users' tab in the settings sidebar.
The actual user management is handled by a custom frontend component
that talks to /api/admin/users endpoints.
"""

from shelfmark.core.settings_registry import (
    CheckboxField,
    CustomComponentField,
    HeadingField,
    MultiSelectField,
    NumberField,
    SelectField,
    TableField,
    register_on_save,
    register_settings,
)
from shelfmark.core.request_policy import (
    get_source_content_type_capabilities,
    parse_policy_mode,
    validate_policy_rules,
)


_REQUEST_DEFAULT_MODE_OPTIONS = [
    {
        "value": "download",
        "label": "Download",
        "description": "Everything can be downloaded directly.",
    },
    {
        "value": "request_release",
        "label": "Request Release",
        "description": "Users must request a specific release.",
    },
    {
        "value": "request_book",
        "label": "Request Book",
        "description": "Users request a book, admin picks the release.",
    },
    {
        "value": "blocked",
        "label": "Blocked",
        "description": "No downloads or requests allowed.",
    },
]

_REQUEST_MATRIX_MODE_OPTIONS = [
    option for option in _REQUEST_DEFAULT_MODE_OPTIONS if option["value"] != "request_book"
]

_SELF_SETTINGS_SECTION_OPTIONS = [
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
_SELF_SETTINGS_SECTION_VALUES = {option["value"] for option in _SELF_SETTINGS_SECTION_OPTIONS}
_SELF_SETTINGS_SECTION_DEFAULTS = [option["value"] for option in _SELF_SETTINGS_SECTION_OPTIONS]

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


def _get_request_source_options():
    """Build request-policy source options from registered release sources."""
    from shelfmark.release_sources import list_available_sources

    options = []
    for source in list_available_sources():
        options.append(
            {
                "value": source["name"],
                "label": source["display_name"],
            }
        )
    return options


def _get_request_policy_rule_columns():
    source_capabilities = get_source_content_type_capabilities()
    content_type_options = []

    for source_name, supported_types in source_capabilities.items():
        normalized_types = [t for t in ("ebook", "audiobook") if t in supported_types]
        for content_type in normalized_types:
            content_type_options.append(
                {
                    "value": content_type,
                    "label": "Ebook" if content_type == "ebook" else "Audiobook",
                    "childOf": source_name,
                }
            )

    return [
        {
            "key": "source",
            "label": "Source",
            "type": "select",
            "options": _get_request_source_options(),
            "defaultValue": "",
            "placeholder": "Select source...",
        },
        {
            "key": "content_type",
            "label": "Content Type",
            "type": "select",
            "options": content_type_options,
            "defaultValue": "",
            "placeholder": "Select content type...",
            "filterByField": "source",
        },
        {
            "key": "mode",
            "label": "Mode",
            "type": "select",
            "options": _REQUEST_MATRIX_MODE_OPTIONS,
            "defaultValue": "",
            "placeholder": "Select mode...",
        },
    ]


def _on_save_users(values):
    """Validate users/request-policy settings before persistence."""
    if "VISIBLE_SELF_SETTINGS_SECTIONS" in values:
        raw_sections = values["VISIBLE_SELF_SETTINGS_SECTIONS"]
        if raw_sections is None:
            candidate_sections: list[str] = []
        elif isinstance(raw_sections, str):
            candidate_sections = [s.strip() for s in raw_sections.split(",") if s.strip()]
        elif isinstance(raw_sections, (list, tuple, set)):
            candidate_sections = [str(section).strip() for section in raw_sections if str(section).strip()]
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

    if "REQUEST_POLICY_DEFAULT_EBOOK" in values:
        if parse_policy_mode(values["REQUEST_POLICY_DEFAULT_EBOOK"]) is None:
            return {
                "error": True,
                "message": "REQUEST_POLICY_DEFAULT_EBOOK must be a valid policy mode",
                "values": values,
            }

    if "REQUEST_POLICY_DEFAULT_AUDIOBOOK" in values:
        if parse_policy_mode(values["REQUEST_POLICY_DEFAULT_AUDIOBOOK"]) is None:
            return {
                "error": True,
                "message": "REQUEST_POLICY_DEFAULT_AUDIOBOOK must be a valid policy mode",
                "values": values,
            }

    if "REQUEST_POLICY_RULES" in values:
        normalized_rules, errors = validate_policy_rules(values["REQUEST_POLICY_RULES"])
        if errors:
            return {
                "error": True,
                "message": "; ".join(errors),
                "values": values,
            }
        values["REQUEST_POLICY_RULES"] = normalized_rules

    return {"error": False, "values": values}


register_on_save("users", _on_save_users)


@register_settings("users", "Users & Requests", icon="users", order=6)
def users_settings():
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
            description=(
                "Choose what users can download directly and what needs approval first."
            ),
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
        CustomComponentField(
            key="request_policy_editor",
            component="request_policy_grid",
            label="Request Rules",
            description=(
                "Fine-tune access per source. Source rules can only be the same or more restrictive than the default above."
            ),
            show_when={"field": "REQUESTS_ENABLED", "value": True},
            wrap_in_field_wrapper=True,
            value_fields=[
                SelectField(
                    key="REQUEST_POLICY_DEFAULT_EBOOK",
                    label="Default Ebook Mode",
                    description=(
                        "Sets the baseline for all ebook sources."
                    ),
                    options=_REQUEST_DEFAULT_MODE_OPTIONS,
                    default="download",
                    user_overridable=True,
                ),
                SelectField(
                    key="REQUEST_POLICY_DEFAULT_AUDIOBOOK",
                    label="Default Audiobook Mode",
                    description=(
                        "Sets the baseline for all audiobook sources."
                    ),
                    options=_REQUEST_DEFAULT_MODE_OPTIONS,
                    default="download",
                    user_overridable=True,
                ),
                TableField(
                    key="REQUEST_POLICY_RULES",
                    label="Request Rules",
                    description=(
                        "Fine-tune access per source. Source rules can only be the same or more restrictive than the default above."
                    ),
                    columns=_get_request_policy_rule_columns,
                    default=[],
                    add_label="Add Rule",
                    empty_message="No request policy rules configured.",
                    env_supported=False,
                    user_overridable=True,
                ),
            ],
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
