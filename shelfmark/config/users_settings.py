"""Users settings tab registration.

This registers a 'users' tab in the settings sidebar.
The actual user management is handled by a custom frontend component
that talks to /api/admin/users endpoints.
"""

from shelfmark.core.settings_registry import (
    CheckboxField,
    HeadingField,
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
        "description": "Allow direct downloads.",
    },
    {
        "value": "request_release",
        "label": "Request Release",
        "description": "Block direct download; allow requesting a specific release.",
    },
    {
        "value": "request_book",
        "label": "Request Book",
        "description": "Block direct download; allow book-level requests only.",
    },
    {
        "value": "blocked",
        "label": "Blocked",
        "description": "Block both downloading and requesting.",
    },
]

_REQUEST_MATRIX_MODE_OPTIONS = [
    option for option in _REQUEST_DEFAULT_MODE_OPTIONS if option["value"] != "request_book"
]


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
        HeadingField(
            key="requests_heading",
            title="Request Policy",
            description=(
                "Configure when users can download directly and when they must create requests."
            ),
        ),
        CheckboxField(
            key="REQUESTS_ENABLED",
            label="Enable Request Workflow",
            description=(
                "When disabled, request actions are hidden and only direct downloads are used."
            ),
            default=False,
            user_overridable=True,
        ),
        SelectField(
            key="REQUEST_POLICY_DEFAULT_EBOOK",
            label="Default Ebook Mode",
            description=(
                "Global ceiling for ebook actions. Source rules can only match or restrict this mode."
            ),
            options=_REQUEST_DEFAULT_MODE_OPTIONS,
            default="download",
            user_overridable=True,
        ),
        SelectField(
            key="REQUEST_POLICY_DEFAULT_AUDIOBOOK",
            label="Default Audiobook Mode",
            description=(
                "Global ceiling for audiobook actions. Source rules can only match or restrict this mode."
            ),
            options=_REQUEST_DEFAULT_MODE_OPTIONS,
            default="download",
            user_overridable=True,
        ),
        TableField(
            key="REQUEST_POLICY_RULES",
            label="Request Policy Rules",
            description=(
                "Source/content-type rules can only restrict the content-type default ceiling."
            ),
            columns=_get_request_policy_rule_columns,
            default=[],
            add_label="Add Rule",
            empty_message="No request policy rules configured.",
            env_supported=False,
            user_overridable=True,
        ),
        NumberField(
            key="MAX_PENDING_REQUESTS_PER_USER",
            label="Max Pending Requests Per User",
            description="Maximum number of pending requests a user can have at once.",
            default=20,
            min_value=1,
            max_value=1000,
            user_overridable=True,
        ),
        CheckboxField(
            key="REQUESTS_ALLOW_NOTES",
            label="Allow Request Notes",
            description="Allow users to include notes when creating requests.",
            default=True,
            user_overridable=True,
        ),
    ]
