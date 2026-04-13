"""Newznab settings registration."""

from typing import Any

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)
from shelfmark.core.utils import normalize_http_url


def _test_newznab_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the Newznab connection using current form values."""
    from shelfmark.core.config import config
    from shelfmark.release_sources.newznab.api import NewznabClient

    current_values = current_values or {}

    raw_url = str(current_values.get("NEWZNAB_URL") or config.get("NEWZNAB_URL", "") or "")
    api_key = str(current_values.get("NEWZNAB_API_KEY") or config.get("NEWZNAB_API_KEY", "") or "")

    if not raw_url:
        return {"success": False, "message": "Newznab URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "Newznab URL is invalid"}

    try:
        client = NewznabClient(url, api_key)
        success, message = client.test_connection()
    except Exception as e:  # noqa: BLE001 — surface any unexpected error to the UI
        return {"success": False, "message": f"Connection failed: {e!s}"}
    else:
        return {"success": success, "message": message}


@register_settings(
    name="newznab_config",
    display_name="Newznab",
    icon="download",
    order=42,
)
def newznab_config_settings() -> list[SettingsField]:
    """Newznab connection settings."""
    return [
        HeadingField(
            key="newznab_heading",
            title="Newznab Integration",
            description=(
                "Search for books via any Newznab-compatible indexer or aggregator "
                "(e.g. NZBHydra2, NZBGeek, Drunkenslug)."
            ),
        ),
        CheckboxField(
            key="NEWZNAB_ENABLED",
            label="Enable Newznab source",
            default=False,
            description="Enable searching for books via a Newznab-compatible indexer",
        ),
        TextField(
            key="NEWZNAB_URL",
            label="Newznab URL",
            description="Base URL of your Newznab indexer or aggregator",
            placeholder="http://nzbhydra:5076",
            required=True,
            show_when={"field": "NEWZNAB_ENABLED", "value": True},
        ),
        PasswordField(
            key="NEWZNAB_API_KEY",
            label="API Key",
            description="Your Newznab API key (leave blank if not required)",
            required=False,
            show_when={"field": "NEWZNAB_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_newznab",
            label="Test Connection",
            description="Verify your Newznab configuration",
            style="primary",
            callback=_test_newznab_connection,
            show_when={"field": "NEWZNAB_ENABLED", "value": True},
        ),
        CheckboxField(
            key="NEWZNAB_AUTO_EXPAND",
            label="Auto-expand search on no results",
            default=False,
            description="Automatically retry search without category filtering if no results are found",
            show_when={"field": "NEWZNAB_ENABLED", "value": True},
        ),
    ]
