"""Standard Ebooks release source - searches the standardebooks.org public catalogue."""

from typing import TYPE_CHECKING, ClassVar

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.release_sources import (
    BrowseRecord,
    ColumnAlign,
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    register_source,
)
from shelfmark.release_sources.standard_ebooks import scraper

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

_DEFAULT_MAX_RESULTS = 12


def _coerce_positive_int(value: object, default: int) -> int:
    """Return a positive integer config value or the provided default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def _build_query_candidates(plan: ReleaseSearchPlan, book: BookMetadata) -> list[str]:
    """Build ordered, de-duplicated search queries from the plan (mirrors Libgen)."""
    candidates: list[str] = []
    if plan.manual_query:
        candidates.append(plan.manual_query.strip())
    elif plan.title_variants:
        variant = plan.title_variants[0]
        combined = f"{variant.title} {variant.author}".strip()
        title_only = (variant.title or "").strip()
        if combined:
            candidates.append(combined)
        if title_only and title_only.lower() != combined.lower():
            candidates.append(title_only)
    elif book.title:
        candidates.append(book.title.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        deduped.append(normalized)
    return deduped


@register_source("standard_ebooks")
class StandardEbooksSource(ReleaseSource):
    """Release source that searches the Standard Ebooks catalogue for downloadable books."""

    name = "standard_ebooks"
    display_name = "Standard Ebooks"
    supported_content_types: ClassVar[list[str]] = ["ebook"]

    def is_available(self) -> bool:
        """Available when explicitly enabled in settings."""
        return config.get("STANDARD_EBOOKS_ENABLED", False) is True

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search Standard Ebooks for releases of a book."""
        if content_type != "ebook":
            return []
        if not self.is_available():
            return []

        queries = _build_query_candidates(plan, book)
        if not queries:
            return []

        max_results = _coerce_positive_int(
            config.get("STANDARD_EBOOKS_MAX_RESULTS", _DEFAULT_MAX_RESULTS),
            _DEFAULT_MAX_RESULTS,
        )

        for query in queries:
            logger.info("Searching Standard Ebooks for: %s", query)
            records = scraper.search_standard_ebooks(query, max_results=max_results)
            if records:
                return [self._record_to_release(record) for record in records]
        return []

    def _record_to_release(self, record: BrowseRecord) -> Release:
        """Normalize a Standard Ebooks browse record into a Release."""
        return Release(
            source="standard_ebooks",
            source_id=f"standard_ebooks:{record.id}",
            title=record.title,
            format="epub",
            language="en",
            size=record.size,
            download_url=record.download_urls[0] if record.download_urls else None,
            info_url=record.source_url,
            protocol=ReleaseProtocol.HTTP,
            indexer="Standard Ebooks",
            content_type="ebook",
            extra={
                "author": record.author,
                "preview": record.preview,
                "slug": record.id,
                "language": "en",
            },
        )

    def search_results_are_releases(self) -> bool:
        """Standard Ebooks search results are concrete, directly downloadable releases."""
        return True

    def get_column_config(self) -> ReleaseColumnConfig:
        """Language and format badges -- same standard layout as Libgen."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.language",
                    label="Language",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                ),
            ],
            grid_template="minmax(0,2fr) 60px 80px 80px",
            supported_filters=["format", "language"],
        )
