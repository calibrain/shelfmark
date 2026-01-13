"""
Onboarding wizard configuration.

Defines the steps and fields for the first-run onboarding experience.
Reuses field definitions from the settings registry where possible.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional

from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    HeadingField,
    SettingsField,
    get_settings_tab,
    serialize_field,
    save_config_file,
    get_setting_value,
)

logger = setup_logger(__name__)


ONBOARDING_STORAGE_KEY = "onboarding_complete"


def _get_config_dir() -> Path:
    """Get the config directory path."""
    from shelfmark.config.env import CONFIG_DIR
    return Path(CONFIG_DIR)


def is_onboarding_complete() -> bool:
    """
    Check if onboarding has been completed.

    For existing users updating from a version without onboarding,
    we check if they have existing settings configured - if so,
    we consider onboarding complete and mark it as such.
    """
    config_file = _get_config_dir() / "settings.json"
    if not config_file.exists():
        return False

    try:
        with open(config_file, 'r') as f:
            config = json.load(f)

            # Explicit flag set - they've completed onboarding
            if config.get(ONBOARDING_STORAGE_KEY, False):
                return True

            # Check if this is an existing user (has other settings configured)
            # If settings.json has any keys besides onboarding_complete, they're existing
            other_keys = [k for k in config.keys() if k != ONBOARDING_STORAGE_KEY]
            if other_keys:
                # Existing user - auto-mark onboarding complete
                logger.info(f"Existing user detected (has settings: {other_keys}), marking onboarding complete")
                mark_onboarding_complete()
                return True

            return False
    except (json.JSONDecodeError, OSError):
        return False


def mark_onboarding_complete() -> bool:
    """Mark onboarding as complete."""
    try:
        return save_config_file("general", {ONBOARDING_STORAGE_KEY: True})
    except Exception as e:
        logger.error(f"Failed to mark onboarding complete: {e}")
        return False


def _get_field_from_tab(tab_name: str, field_key: str) -> Optional[SettingsField]:
    """
    Extract a specific field from a registered settings tab.

    Args:
        tab_name: Name of the settings tab (e.g., 'search_mode', 'hardcover')
        field_key: Key of the field to extract (e.g., 'SEARCH_MODE', 'HARDCOVER_API_KEY')

    Returns:
        The field if found, None otherwise
    """
    tab = get_settings_tab(tab_name)
    if not tab:
        logger.warning(f"Settings tab not found: {tab_name}")
        return None

    for field in tab.fields:
        if hasattr(field, 'key') and field.key == field_key:
            return field

    logger.warning(f"Field {field_key} not found in tab {tab_name}")
    return None


def _clone_field_with_overrides(field: SettingsField, **overrides) -> SettingsField:
    """
    Clone a field with optional attribute overrides.

    Useful for customizing labels, descriptions, or defaults for onboarding context.
    """
    return replace(field, **overrides)


# =============================================================================
# Step Definitions
# =============================================================================


def get_search_mode_fields() -> List[SettingsField]:
    """Step 1: Choose search mode - uses actual SEARCH_MODE field from settings."""
    fields: List[SettingsField] = [
        HeadingField(
            key="welcome_heading",
            title="Welcome to Shelfmark",
            description="Let's configure how you want to search for and download books.",
        ),
    ]

    # Get the actual SEARCH_MODE field from settings
    search_mode_field = _get_field_from_tab("search_mode", "SEARCH_MODE")
    if search_mode_field:
        # Clone with onboarding-specific description
        fields.append(_clone_field_with_overrides(
            search_mode_field,
            description="Choose how you want to find books.",
        ))

    return fields


def get_metadata_provider_fields() -> List[SettingsField]:
    """Step 2: Choose metadata provider - uses actual METADATA_PROVIDER field."""
    fields: List[SettingsField] = [
        HeadingField(
            key="metadata_heading",
            title="Metadata Provider",
            description="Choose where to search for book information. You can enable more providers in Settings later.",
        ),
    ]

    # Get the actual METADATA_PROVIDER field from settings
    provider_field = _get_field_from_tab("search_mode", "METADATA_PROVIDER")
    if provider_field:
        # Custom options with Hardcover marked as recommended
        onboarding_options = [
            {
                "value": "hardcover",
                "label": "Hardcover (Recommended)",
                "description": "Modern book tracking platform with excellent metadata, ratings, and series information. Requires free API key.",
            },
            {
                "value": "openlibrary",
                "label": "Open Library",
                "description": "Free, open-source library catalog from the Internet Archive. No API key required.",
            },
            {
                "value": "googlebooks",
                "label": "Google Books",
                "description": "Google's book database with good coverage. Requires free API key.",
            },
        ]

        # Clone with onboarding-specific options and default
        fields.append(_clone_field_with_overrides(
            provider_field,
            default="hardcover",
            options=onboarding_options,
        ))

    return fields


def get_hardcover_setup_fields() -> List[SettingsField]:
    """Step 3a: Configure Hardcover - uses actual API key and test connection fields."""
    fields: List[SettingsField] = [
        HeadingField(
            key="hardcover_setup_heading",
            title="Hardcover Setup",
            description="Get your free API key from hardcover.app/account/api",
            link_url="https://hardcover.app/account/api",
            link_text="Get API Key",
        ),
    ]

    # Get the actual HARDCOVER_API_KEY field
    api_key_field = _get_field_from_tab("hardcover", "HARDCOVER_API_KEY")
    if api_key_field:
        fields.append(api_key_field)

    # Get the test connection button
    test_button = _get_field_from_tab("hardcover", "test_connection")
    if test_button:
        fields.append(test_button)

    return fields


def get_googlebooks_setup_fields() -> List[SettingsField]:
    """Step 3b: Configure Google Books - uses actual API key and test connection fields."""
    fields: List[SettingsField] = [
        HeadingField(
            key="googlebooks_setup_heading",
            title="Google Books Setup",
            description="Get your free API key from Google Cloud Console (APIs & Services > Credentials).",
            link_url="https://console.cloud.google.com/apis/library/books.googleapis.com",
            link_text="Get API Key",
        ),
    ]

    # Get the actual GOOGLEBOOKS_API_KEY field
    api_key_field = _get_field_from_tab("googlebooks", "GOOGLEBOOKS_API_KEY")
    if api_key_field:
        fields.append(api_key_field)

    # Get the test connection button
    test_button = _get_field_from_tab("googlebooks", "test_connection")
    if test_button:
        fields.append(test_button)

    return fields


def get_prowlarr_fields() -> List[SettingsField]:
    """Step 4: Configure Prowlarr connection - uses actual Prowlarr fields."""
    fields: List[SettingsField] = [
        HeadingField(
            key="prowlarr_heading",
            title="Prowlarr Integration (Optional)",
            description="Connect to Prowlarr to search your indexers for torrents and NZBs. Skip this step if you only want to use Direct Download.",
        ),
    ]

    # Get actual Prowlarr connection fields
    prowlarr_fields = ["PROWLARR_ENABLED", "PROWLARR_URL", "PROWLARR_API_KEY", "test_prowlarr"]
    for field_key in prowlarr_fields:
        field = _get_field_from_tab("prowlarr_config", field_key)
        if field:
            fields.append(field)

    return fields


def get_prowlarr_indexers_fields() -> List[SettingsField]:
    """Step 5: Select Prowlarr indexers to search."""
    fields: List[SettingsField] = [
        HeadingField(
            key="prowlarr_indexers_heading",
            title="Select Indexers",
            description="Choose which indexers to search for books. Leave empty to search all available indexers.",
        ),
    ]

    # Get the indexers multi-select field
    indexers_field = _get_field_from_tab("prowlarr_config", "PROWLARR_INDEXERS")
    if indexers_field:
        fields.append(indexers_field)

    return fields


# =============================================================================
# Step Configuration
# =============================================================================


ONBOARDING_STEPS = [
    {
        "id": "search_mode",
        "title": "Search Mode",
        "tab": "search_mode",
        "get_fields": get_search_mode_fields,
    },
    {
        "id": "metadata_provider",
        "title": "Metadata Provider",
        "tab": "search_mode",
        "get_fields": get_metadata_provider_fields,
        "show_when": [{"field": "SEARCH_MODE", "value": "universal"}],
    },
    {
        "id": "hardcover_setup",
        "title": "Hardcover Setup",
        "tab": "hardcover",
        "get_fields": get_hardcover_setup_fields,
        # Must be universal mode AND hardcover selected
        "show_when": [
            {"field": "SEARCH_MODE", "value": "universal"},
            {"field": "METADATA_PROVIDER", "value": "hardcover"},
        ],
    },
    {
        "id": "googlebooks_setup",
        "title": "Google Books Setup",
        "tab": "googlebooks",
        "get_fields": get_googlebooks_setup_fields,
        # Must be universal mode AND googlebooks selected
        "show_when": [
            {"field": "SEARCH_MODE", "value": "universal"},
            {"field": "METADATA_PROVIDER", "value": "googlebooks"},
        ],
    },
    {
        "id": "prowlarr",
        "title": "Prowlarr",
        "tab": "prowlarr_config",
        "get_fields": get_prowlarr_fields,
        "show_when": [{"field": "SEARCH_MODE", "value": "universal"}],
        "optional": True,
    },
    {
        "id": "prowlarr_indexers",
        "title": "Indexers",
        "tab": "prowlarr_config",
        "get_fields": get_prowlarr_indexers_fields,
        # Only show when Prowlarr is enabled
        "show_when": [
            {"field": "SEARCH_MODE", "value": "universal"},
            {"field": "PROWLARR_ENABLED", "value": True},
        ],
        "optional": True,
    },
]


def get_onboarding_config() -> Dict[str, Any]:
    """
    Get the full onboarding configuration including steps and current values.
    """
    steps = []
    all_values = {}

    for step_config in ONBOARDING_STEPS:
        fields = step_config["get_fields"]()
        tab_name = step_config["tab"]

        # Serialize fields with current values
        serialized_fields = []
        for field in fields:
            serialized = serialize_field(field, tab_name, include_value=True)
            serialized_fields.append(serialized)

            # Collect values (skip HeadingFields)
            if hasattr(field, 'key') and field.key and not isinstance(field, HeadingField):
                value = get_setting_value(field, tab_name)
                all_values[field.key] = value if value is not None else getattr(field, 'default', '')

        step = {
            "id": step_config["id"],
            "title": step_config["title"],
            "tab": tab_name,
            "fields": serialized_fields,
        }

        if "show_when" in step_config:
            step["showWhen"] = step_config["show_when"]
        if step_config.get("optional"):
            step["optional"] = True

        steps.append(step)

    return {
        "steps": steps,
        "values": all_values,
        "complete": is_onboarding_complete(),
    }


def save_onboarding_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    """
    Save onboarding settings and mark as complete.

    Args:
        values: Dict of field key -> value

    Returns:
        Dict with success status and message
    """
    try:
        # Group values by their target tab
        tab_values: Dict[str, Dict[str, Any]] = {}

        for step_config in ONBOARDING_STEPS:
            tab_name = step_config["tab"]
            fields = step_config["get_fields"]()

            for field in fields:
                if isinstance(field, HeadingField):
                    continue

                key = field.key
                if key in values:
                    if tab_name not in tab_values:
                        tab_values[tab_name] = {}
                    tab_values[tab_name][key] = values[key]

        # Save each tab's values
        for tab_name, tab_data in tab_values.items():
            if tab_data:
                save_config_file(tab_name, tab_data)
                logger.info(f"Saved onboarding settings to {tab_name}: {list(tab_data.keys())}")

        # Enable the selected metadata provider
        search_mode = values.get("SEARCH_MODE", "direct")
        if search_mode == "universal":
            provider = values.get("METADATA_PROVIDER", "hardcover")
            if provider:
                # Map provider name to its enabled key
                enabled_key_map = {
                    "hardcover": "HARDCOVER_ENABLED",
                    "openlibrary": "OPENLIBRARY_ENABLED",
                    "googlebooks": "GOOGLEBOOKS_ENABLED",
                }
                enabled_key = enabled_key_map.get(provider, f"{provider.upper()}_ENABLED")

                # Get existing provider config and add enabled flag
                provider_config = {enabled_key: True}

                # Include API key if provided for that provider
                if provider == "hardcover" and values.get("HARDCOVER_API_KEY"):
                    provider_config["HARDCOVER_API_KEY"] = values["HARDCOVER_API_KEY"]
                elif provider == "googlebooks" and values.get("GOOGLEBOOKS_API_KEY"):
                    provider_config["GOOGLEBOOKS_API_KEY"] = values["GOOGLEBOOKS_API_KEY"]

                save_config_file(provider, provider_config)
                logger.info(f"Enabled metadata provider: {provider} with keys: {list(provider_config.keys())}")

        # Mark onboarding as complete
        mark_onboarding_complete()

        # Refresh config
        try:
            from shelfmark.core.config import config
            config.refresh()
        except ImportError:
            pass

        return {"success": True, "message": "Onboarding complete!"}

    except Exception as e:
        logger.error(f"Failed to save onboarding settings: {e}")
        return {"success": False, "message": str(e)}
