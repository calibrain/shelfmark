"""Standard Ebooks search settings registration."""

from shelfmark.core.settings_registry import (
    CheckboxField,
    NumberField,
    SettingsField,
    register_settings,
)


@register_settings("standard_ebooks_config", "Standard Ebooks", icon="book-open", order=47)
def standard_ebooks_config_settings() -> list[SettingsField]:
    """Standard Ebooks search configuration settings."""
    return [
        CheckboxField(
            key="STANDARD_EBOOKS_ENABLED",
            label="Enable Standard Ebooks",
            description=(
                "Search the Standard Ebooks catalogue for free, high-quality public domain ebooks."
            ),
            default=False,
        ),
        NumberField(
            key="STANDARD_EBOOKS_MAX_RESULTS",
            label="Max Results",
            description="Maximum number of results to request per search (1-48).",
            default=12,
            min_value=1,
            max_value=48,
            show_when={"field": "STANDARD_EBOOKS_ENABLED", "value": True},
        ),
    ]
