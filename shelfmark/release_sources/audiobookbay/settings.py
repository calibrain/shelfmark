"""AudiobookBay settings registration."""

from shelfmark.core.settings_registry import (
    register_settings,
    CheckboxField,
    TextField,
    NumberField,
)


# ==================== Register Settings ====================

@register_settings("audiobookbay_config", "AudiobookBay", icon="download", order=45)
def audiobookbay_config_settings():
    """AudiobookBay configuration settings."""
    return [
        CheckboxField(
            key="ABB_ENABLED",
            label="Enable AudiobookBay",
            description="Enable AudiobookBay as a release source for audiobooks.",
            default=False,
        ),
        TextField(
            key="ABB_HOSTNAME",
            label="Hostname",
            description="AudiobookBay domain (e.g., audiobookbay.lu, audiobookbay.is). Required to enable searches.",
            placeholder="",
            default="",
            required=True,
            show_when={"field": "ABB_ENABLED", "value": True},
        ),
        NumberField(
            key="ABB_PAGE_LIMIT",
            label="Max Pages to Search",
            description="Maximum number of search result pages to fetch (1-10).",
            default=1,
            min_value=1,
            max_value=10,
            show_when={"field": "ABB_ENABLED", "value": True},
        ),
        CheckboxField(
            key="ABB_EXACT_PHRASE",
            label="Prefer Exact-Phrase Search",
            description="Wrap generated queries in quotes for stricter matching. If no results are found, Shelfmark retries without quotes.",
            default=False,
            show_when={"field": "ABB_ENABLED", "value": True},
        ),
        NumberField(
            key="ABB_RATE_LIMIT_DELAY",
            label="Rate Limit Delay (seconds)",
            description="Delay between requests in seconds to avoid rate limiting (0-10).",
            default=1.0,
            min_value=0.0,
            max_value=10.0,
            show_when={"field": "ABB_ENABLED", "value": True},
        ),
    ]
