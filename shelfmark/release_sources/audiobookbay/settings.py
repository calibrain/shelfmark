"""AudiobookBay settings registration."""

from shelfmark.core.settings_registry import (
    register_group,
    register_settings,
    CheckboxField,
    TextField,
    NumberField,
    HeadingField,
)


# ==================== Register Group ====================

register_group(
    name="audiobookbay",
    display_name="AudiobookBay",
    icon="download",
    order=45,  # After Prowlarr (order 40)
)


# ==================== Register Settings ====================

@register_settings("audiobookbay_config", "Configuration", group="audiobookbay", order=1)
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
            description="AudiobookBay domain (e.g., audiobookbay.lu, audiobookbay.is)",
            placeholder="audiobookbay.lu",
            default="audiobookbay.lu",
            show_when={"field": "ABB_ENABLED", "value": True},
        ),
        NumberField(
            key="ABB_PAGE_LIMIT",
            label="Max Pages to Search",
            description="Maximum number of search result pages to fetch (1-10).",
            default=5,
            min_value=1,
            max_value=10,
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


# ==================== Download Clients Tab ====================

@register_settings("audiobookbay_clients", "Download Clients", group="audiobookbay", order=2)
def audiobookbay_clients_settings():
    """AudiobookBay download client settings."""
    return [
        HeadingField(
            key="abb_torrent_heading",
            title="Torrent Client",
            description="The AudiobookBay integration uses the torrent client that is configured under 'Prowlarr' > 'Download Clients'.",
        ),
    ]
