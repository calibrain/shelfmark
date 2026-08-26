"""Newznab settings registration."""

from typing import Any

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    PasswordField,
    SettingsField,
    TableField,
    TagListField,
    TextField,
    register_settings,
)
from shelfmark.core.utils import normalize_http_url


def _test_newznab_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test all named Newznab connections, or the legacy connection as fallback."""
    from shelfmark.core.config import config
    from shelfmark.release_sources.newznab.api import NewznabClient
    from shelfmark.release_sources.newznab.source import _parse_indexer_rows

    current_values = current_values or {}

    raw_indexers = current_values.get("NEWZNAB_INDEXERS")
    if raw_indexers is None:
        raw_indexers = config.get("NEWZNAB_INDEXERS", [])
    indexers = _parse_indexer_rows(raw_indexers)

    if indexers:
        details: list[str] = []
        all_successful = True
        for name, url, api_key in indexers:
            try:
                success, message = NewznabClient(url, api_key).test_connection()
            except Exception as e:  # noqa: BLE001 — surface unexpected errors to the UI
                success, message = False, f"Connection failed: {e!s}"
            all_successful = all_successful and success
            details.append(f"{name}: {message}")

        summary = (
            f"Connected to all {len(indexers)} indexers"
            if all_successful
            else "One or more Newznab indexers failed"
        )
        return {"success": all_successful, "message": summary, "details": details}

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
        TableField(
            key="NEWZNAB_INDEXERS",
            label="Named Indexers",
            description=(
                "Add each Newznab-compatible indexer separately. The configured name is shown "
                "beside every result from that indexer."
            ),
            columns=[
                {
                    "key": "name",
                    "label": "Name",
                    "type": "text",
                    "placeholder": "NZBGeek",
                },
                {
                    "key": "url",
                    "label": "URL",
                    "type": "text",
                    "placeholder": "https://api.nzbgeek.info",
                },
                {
                    "key": "api_key",
                    "label": "API Key",
                    "type": "password",
                    "placeholder": "Optional",
                },
            ],
            default=[],
            add_label="Add Indexer",
            empty_message=(
                "No named indexers configured. The legacy single-indexer fields below are used "
                "as a fallback."
            ),
            show_when={"field": "NEWZNAB_ENABLED", "value": True},
        ),
        TextField(
            key="NEWZNAB_URL",
            label="Legacy Newznab URL",
            description="Used only when the named indexer list is empty",
            placeholder="http://nzbhydra:5076",
            required=False,
            show_when={"field": "NEWZNAB_ENABLED", "value": True},
        ),
        PasswordField(
            key="NEWZNAB_API_KEY",
            label="Legacy API Key",
            description="Used only with the legacy Newznab URL",
            required=False,
            show_when={"field": "NEWZNAB_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_newznab",
            label="Test Connections",
            description="Verify every named indexer, or the legacy connection when the list is empty",
            style="primary",
            callback=_test_newznab_connection,
            show_when={"field": "NEWZNAB_ENABLED", "value": True},
        ),
        TagListField(
            key="NEWZNAB_EBOOK_CATEGORIES",
            label="Ebook Categories",
            description=(
                "Newznab category IDs searched for ebooks. Most indexers use the standard 7000, "
                "but some use custom IDs. Leave empty to use 7000."
            ),
            placeholder="7000",
            default=["7000"],
            normalize_urls=False,
            show_when={"field": "NEWZNAB_ENABLED", "value": True},
        ),
        TagListField(
            key="NEWZNAB_AUDIOBOOK_CATEGORIES",
            label="Audiobook Categories",
            description=(
                "Newznab category IDs searched for audiobooks. Most indexers use the standard "
                "3030, but some use custom IDs. Leave empty to use 3030."
            ),
            placeholder="3030",
            default=["3030"],
            normalize_urls=False,
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
