"""slskd settings registration."""

from typing import Any

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    NumberField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)
from shelfmark.core.utils import normalize_http_url


def _test_slskd_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the slskd connection using unsaved form values when available."""
    from shelfmark.core.config import config
    from shelfmark.release_sources.slskd.api import SlskdClient

    current_values = current_values or {}
    raw_url = str(current_values.get("SLSKD_URL") or config.get("SLSKD_URL", "") or "")
    api_key = str(current_values.get("SLSKD_API_KEY") or config.get("SLSKD_API_KEY", "") or "")

    if not raw_url.strip():
        return {"success": False, "message": "slskd URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "slskd URL is invalid"}

    if not api_key.strip():
        return {"success": False, "message": "slskd API key is required"}

    try:
        success, message = SlskdClient(url, api_key.strip()).test_connection()
    except Exception as e:  # noqa: BLE001 — surface any unexpected error to the UI
        return {"success": False, "message": f"Connection failed: {e!s}"}
    else:
        return {"success": success, "message": message}


@register_settings(
    name="slskd_config",
    display_name="Soulseek (slskd)",
    icon="download",
    order=43,
)
def slskd_config_settings() -> list[SettingsField]:
    """slskd connection and download settings."""
    return [
        HeadingField(
            key="slskd_heading",
            title="Soulseek via slskd",
            description=(
                "Search the Soulseek network and download files through a "
                "[slskd](https://github.com/slskd/slskd) instance. slskd must be logged in "
                "to Soulseek, and its downloads folder must be visible to Shelfmark."
            ),
        ),
        CheckboxField(
            key="SLSKD_ENABLED",
            label="Enable Soulseek source",
            default=False,
            description="Enable searching and downloading books through slskd",
        ),
        TextField(
            key="SLSKD_URL",
            label="slskd URL",
            description="Base URL of the slskd web interface",
            placeholder="http://slskd:5030",
            required=False,
            show_when={"field": "SLSKD_ENABLED", "value": True},
        ),
        PasswordField(
            key="SLSKD_API_KEY",
            label="API Key",
            description=(
                "An API key from slskd's configuration (web.authentication.api_keys) with "
                "the readwrite role"
            ),
            required=False,
            show_when={"field": "SLSKD_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_slskd",
            label="Test Connection",
            description="Verify the URL and API key, and that slskd is logged in to Soulseek",
            style="primary",
            callback=_test_slskd_connection,
            show_when={"field": "SLSKD_ENABLED", "value": True},
        ),
        TextField(
            key="SLSKD_DOWNLOAD_PATH",
            label="Downloads Path",
            description=(
                "Where slskd's completed downloads folder is mounted inside Shelfmark. "
                "Leave empty when both containers see it at the same path, or use "
                "Settings > Advanced > Remote Path Mappings with client 'slskd'."
            ),
            placeholder="/downloads/slskd/complete",
            required=False,
            show_when={"field": "SLSKD_ENABLED", "value": True},
        ),
        NumberField(
            key="SLSKD_SEARCH_TIMEOUT",
            label="Search Wait (seconds)",
            description=(
                "How long a Soulseek search collects peer responses. Longer waits find "
                "more peers but make every search slower."
            ),
            default=15,
            min_value=3,
            max_value=120,
            show_when={"field": "SLSKD_ENABLED", "value": True},
        ),
        NumberField(
            key="SLSKD_RESPONSE_LIMIT",
            label="Max Peer Responses",
            description="Stop a search early once this many peers have answered",
            default=100,
            min_value=10,
            max_value=1000,
            show_when={"field": "SLSKD_ENABLED", "value": True},
        ),
        NumberField(
            key="SLSKD_QUEUE_TIMEOUT_MINUTES",
            label="Queue Timeout (minutes)",
            description=(
                "Give up when the peer has not started sending within this long. "
                "Set to 0 to wait indefinitely."
            ),
            default=60,
            min_value=0,
            max_value=1440,
            show_when={"field": "SLSKD_ENABLED", "value": True},
        ),
        CheckboxField(
            key="SLSKD_REMOVE_COMPLETED",
            label="Remove completed transfers from slskd",
            default=True,
            description="Clear the transfer from slskd's download list after Shelfmark imports it",
            show_when={"field": "SLSKD_ENABLED", "value": True},
        ),
    ]
