"""Prowlarr settings registration."""

from typing import Any, Dict, List, Optional

from shelfmark.core.settings_registry import (
    register_settings,
    CheckboxField,
    HeadingField,
    TextField,
    PasswordField,
    ActionButton,
    MultiSelectField,
)
from shelfmark.core.utils import normalize_http_url


# ==================== Dynamic Options Loaders ====================


def _get_indexer_options() -> List[Dict[str, str]]:
    """
    Fetch available indexers from Prowlarr for the multi-select field.

    Returns list of {value: "id", label: "name (protocol)"} options.
    """
    from shelfmark.core.config import config
    from shelfmark.core.logger import setup_logger

    logger = setup_logger(__name__)

    raw_url = config.get("PROWLARR_URL", "")
    api_key = config.get("PROWLARR_API_KEY", "")

    if not raw_url or not api_key:
        return []

    url = normalize_http_url(raw_url)
    if not url:
        return []

    try:
        from shelfmark.release_sources.prowlarr.api import ProwlarrClient

        client = ProwlarrClient(url, api_key)
        indexers = client.get_enabled_indexers()

        options = []
        for idx in indexers:
            idx_id = idx.get("id")
            name = idx.get("name", "Unknown")
            protocol = idx.get("protocol", "")
            has_books = idx.get("has_books", False)

            # Add indicator for book support
            label = f"{name} ({protocol})"
            if has_books:
                label += " 📚"

            options.append({
                "value": str(idx_id),
                "label": label,
            })

        return options

    except Exception as e:
        logger.error(f"Failed to fetch Prowlarr indexers: {e}")
        return []


# ==================== Test Connection Callback ====================


def _test_prowlarr_connection(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test the Prowlarr connection using current form values."""
    from shelfmark.core.config import config
    from shelfmark.release_sources.prowlarr.api import ProwlarrClient

    current_values = current_values or {}

    raw_url = current_values.get("PROWLARR_URL") or config.get("PROWLARR_URL", "")
    api_key = current_values.get("PROWLARR_API_KEY") or config.get("PROWLARR_API_KEY", "")

    if not raw_url:
        return {"success": False, "message": "Prowlarr URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "Prowlarr URL is invalid"}
    if not api_key:
        return {"success": False, "message": "API key is required"}

    try:
        client = ProwlarrClient(url, api_key)
        success, message = client.test_connection()
        return {"success": success, "message": message}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


# ==================== Configuration Tab ====================


@register_settings(
    name="prowlarr_config",
    display_name="Prowlarr",
    icon="download",
    order=41,
)
def prowlarr_config_settings():
    """Prowlarr connection and indexer settings."""
    return [
        HeadingField(
            key="prowlarr_heading",
            title="Prowlarr Integration",
            description="Search for books across your indexers via Prowlarr.",
            link_url="https://prowlarr.com",
            link_text="prowlarr.com",
        ),
        CheckboxField(
            key="PROWLARR_ENABLED",
            label="Enable Prowlarr source",
            default=False,
            description="Enable searching for books via Prowlarr indexers",
        ),
        TextField(
            key="PROWLARR_URL",
            label="Prowlarr URL",
            description="Base URL of your Prowlarr instance",
            placeholder="http://prowlarr:9696",
            required=True,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        PasswordField(
            key="PROWLARR_API_KEY",
            label="API Key",
            description="Found in Prowlarr: Settings > General > API Key",
            required=True,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_prowlarr",
            label="Test Connection",
            description="Verify your Prowlarr configuration",
            style="primary",
            callback=_test_prowlarr_connection,
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        MultiSelectField(
            key="PROWLARR_INDEXERS",
            label="Indexers to Search",
            description="Select which indexers to search. 📚 = has book categories. Leave empty to search all.",
            options=_get_indexer_options,
            default=[],
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
        CheckboxField(
            key="PROWLARR_AUTO_EXPAND",
            label="Auto-expand search on no results",
            default=False,
            description="Automatically retry search without category filtering if no results are found",
            show_when={"field": "PROWLARR_ENABLED", "value": True},
        ),
    ]
