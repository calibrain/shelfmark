"""Settings registration for the Hardcover metadata provider."""

from typing import Any

import requests

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    PasswordField,
    SelectField,
    SettingsField,
    register_settings,
)

from .auth import _get_connected_username, _save_connected_user
from .constants import HARDCOVER_API_KEY_MIN_LENGTH
from .parsing import _normalize_hardcover_api_key
from .provider import HardcoverProvider

logger = setup_logger(__name__)


def _test_hardcover_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the Hardcover API connection using current form values."""
    current_values = current_values or {}

    # Use current form values first, fall back to saved config
    raw_key = current_values.get("HARDCOVER_API_KEY") or app_config.get("HARDCOVER_API_KEY", "")
    api_key = _normalize_hardcover_api_key(raw_key)

    key_len = len(api_key) if api_key else 0
    logger.debug("Hardcover test: key length=%s", key_len)

    if not api_key:
        # Clear any stored connection metadata since there's no key
        _save_connected_user(None, None)
        return {"success": False, "message": "API key is required"}

    if key_len < HARDCOVER_API_KEY_MIN_LENGTH:
        return {
            "success": False,
            "message": (
                f"API key seems too short ({key_len} chars). "
                f"Expected {HARDCOVER_API_KEY_MIN_LENGTH}+ chars."
            ),
        }

    connection_result = {"success": False, "message": "API request failed - check your API key"}
    try:
        provider = HardcoverProvider(api_key=api_key)
        # Use the 'me' query to test connection (recommended by API docs)
        result = provider._execute_query("query { me { id, username } }", {})
        if result is not None:
            # Handle both single object and array response formats
            me_data = result.get("me", {})
            if isinstance(me_data, list) and me_data:
                me_data = me_data[0]
            user_id = (
                str(me_data.get("id"))
                if isinstance(me_data, dict) and me_data.get("id") is not None
                else None
            )
            username = (
                me_data.get("username", "Unknown") if isinstance(me_data, dict) else "Unknown"
            )

            # Save connected user metadata for persistent display + per-user list caching
            _save_connected_user(user_id, username)
            connection_result = {"success": True, "message": f"Connected as: {username}"}
        else:
            _save_connected_user(None, None)
    except (AttributeError, KeyError, requests.RequestException, TypeError, ValueError) as e:
        logger.exception("Hardcover connection test failed")
        _save_connected_user(None, None)
        return {"success": False, "message": f"Connection failed: {e!s}"}

    return connection_result


_HARDCOVER_SORT_OPTIONS = [
    {"value": "relevance", "label": "Most relevant"},
    {"value": "popularity", "label": "Most popular"},
    {"value": "rating", "label": "Highest rated"},
    {"value": "newest", "label": "Newest"},
    {"value": "oldest", "label": "Oldest"},
]


@register_settings("hardcover", "Hardcover", icon="book", order=51, group="metadata_providers")
def hardcover_settings() -> list[SettingsField]:
    """Hardcover metadata provider settings."""
    # Check for connected username to show status
    connected_user = _get_connected_username()
    test_button_description = (
        f"Connected as: {connected_user}" if connected_user else "Verify your API key works"
    )

    return [
        HeadingField(
            key="hardcover_heading",
            title="Hardcover",
            description="A modern book tracking and discovery platform with a comprehensive API.",
            link_url="https://hardcover.app",
            link_text="hardcover.app",
        ),
        CheckboxField(
            key="HARDCOVER_ENABLED",
            label="Enable Hardcover",
            description="Enable Hardcover as a metadata provider for book searches",
            default=False,
        ),
        PasswordField(
            key="HARDCOVER_API_KEY",
            label="API Key",
            description="Get your API key from hardcover.app/account/api",
            required=True,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description=test_button_description,
            style="primary",
            callback=_test_hardcover_connection,
        ),
        SelectField(
            key="HARDCOVER_DEFAULT_SORT",
            label="Default Sort Order",
            description="Default sort order for Hardcover search results.",
            options=_HARDCOVER_SORT_OPTIONS,
            default="relevance",
        ),
        CheckboxField(
            key="HARDCOVER_EXCLUDE_COMPILATIONS",
            label="Exclude Compilations",
            description="Filter out compilations, anthologies, and omnibus editions from search results",
            default=False,
        ),
        CheckboxField(
            key="HARDCOVER_EXCLUDE_UNRELEASED",
            label="Exclude Unreleased Books",
            description="Filter out books with a release year in the future",
            default=False,
        ),
        CheckboxField(
            key="HARDCOVER_AUTO_REMOVE_ON_DOWNLOAD",
            label="Auto-Remove from List on Download",
            description="Automatically remove a book from the active Hardcover list when you download it",
            default=True,
        ),
    ]
