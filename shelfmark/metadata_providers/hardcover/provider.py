"""Hardcover.app metadata provider. Requires API key."""

from typing import Any, ClassVar

import requests

from shelfmark.core.config import config as app_config
from shelfmark.metadata_providers import (
    DynamicSelectSearchField,
    MetadataCapability,
    MetadataProvider,
    SearchField,
    SortOrder,
    TextSearchField,
    register_provider,
    register_provider_kwargs,
)

from .client import HardcoverClientMixin
from .lists import HardcoverListsMixin
from .parsing import HardcoverParsingMixin, _normalize_hardcover_api_key
from .search import HardcoverSearchMixin
from .targets import HardcoverTargetsMixin


@register_provider_kwargs("hardcover")
def _hardcover_kwargs() -> dict[str, Any]:
    """Provide Hardcover-specific constructor kwargs."""
    return {"api_key": app_config.get("HARDCOVER_API_KEY", "")}


@register_provider("hardcover")
class HardcoverProvider(
    HardcoverSearchMixin,
    HardcoverListsMixin,
    HardcoverTargetsMixin,
    HardcoverClientMixin,
    HardcoverParsingMixin,
    MetadataProvider,
):
    """Hardcover.app metadata provider using GraphQL API."""

    name = "hardcover"
    display_name = "Hardcover"
    requires_auth = True
    supported_sorts: ClassVar[tuple[SortOrder, ...]] = (
        SortOrder.RELEVANCE,
        SortOrder.POPULARITY,
        SortOrder.RATING,
        SortOrder.NEWEST,
        SortOrder.OLDEST,
        SortOrder.SERIES_ORDER,
    )
    capabilities: ClassVar[tuple[MetadataCapability, ...]] = (
        MetadataCapability(
            key="view_series",
            field_key="series",
            sort=SortOrder.SERIES_ORDER,
        ),
    )
    search_fields: ClassVar[tuple[SearchField, ...]] = (
        TextSearchField(
            key="author",
            label="Author",
            placeholder="Search author...",
            description="Search by author name",
            suggestions_endpoint="/api/metadata/field-options?provider=hardcover&field=author",
        ),
        TextSearchField(
            key="title",
            label="Title",
            placeholder="Search title...",
            description="Search by book title",
        ),
        TextSearchField(
            key="series",
            label="Series",
            placeholder="Search series...",
            description="Search by series name",
            suggestions_endpoint="/api/metadata/field-options?provider=hardcover&field=series",
        ),
        DynamicSelectSearchField(
            key="hardcover_list",
            label="List",
            options_endpoint="/api/metadata/field-options?provider=hardcover&field=hardcover_list",
            placeholder="Browse a list...",
            description="Browse books from a Hardcover list",
        ),
    )

    def __init__(self, api_key: str | None = None) -> None:
        """Initialize provider with optional API key (falls back to config)."""
        raw_key = api_key or app_config.get("HARDCOVER_API_KEY", "")
        self.api_key = _normalize_hardcover_api_key(raw_key)
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update(
                {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
            )

    def is_available(self) -> bool:
        """Check if provider is configured with an API key."""
        return bool(self.api_key)
