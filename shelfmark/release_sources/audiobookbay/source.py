"""AudiobookBay release source - searches AudiobookBay for audiobook torrents."""

import hashlib
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import (
    Release,
    ReleaseProtocol,
    ReleaseSource,
    register_source,
    ReleaseColumnConfig,
    ColumnSchema,
    ColumnRenderType,
    ColumnAlign,
    ColumnColorHint,
)
from shelfmark.release_sources.audiobookbay import scraper
from shelfmark.release_sources.audiobookbay.utils import parse_size

logger = setup_logger(__name__)


# Map language names to ISO 639-1 codes (matching frontend color maps)
LANGUAGE_MAP = {
    "english": "en",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "japanese": "ja",
    "chinese": "zh",
    "dutch": "nl",
    "swedish": "sv",
    "norwegian": "no",
    "danish": "da",
    "finnish": "fi",
    "polish": "pl",
    "czech": "cs",
    "hungarian": "hu",
    "korean": "ko",
    "arabic": "ar",
    "hebrew": "he",
    "turkish": "tr",
    "greek": "el",
    "hindi": "hi",
    "thai": "th",
    "vietnamese": "vi",
    "indonesian": "id",
    "ukrainian": "uk",
    "romanian": "ro",
    "bulgarian": "bg",
    "catalan": "ca",
    "croatian": "hr",
    "slovenian": "sl",
    "serbian": "sr",
}


def _map_language(language: str) -> Optional[str]:
    """Map language name to ISO 639-1 code.
    
    Args:
        language: Language name (e.g., "English")
        
    Returns:
        ISO 639-1 code (e.g., "en"), or original string if no mapping found, or None if input is empty
    """
    if not language:
        return None
    
    lang_lower = language.lower().strip()
    return LANGUAGE_MAP.get(lang_lower, lang_lower)


def _generate_source_id(detail_url: str) -> str:
    """Generate a unique source ID from detail URL."""
    return hashlib.md5(detail_url.encode()).hexdigest()


@register_source("audiobookbay")
class AudiobookBaySource(ReleaseSource):
    """Release source for AudiobookBay audiobook torrents."""
    
    name = "audiobookbay"
    display_name = "AudiobookBay"
    supported_content_types = ["audiobook"]  # ONLY audiobooks
    
    def search(
        self,
        book: BookMetadata,
        plan: "ReleaseSearchPlan",
        expand_search: bool = False,
        content_type: str = "ebook"
    ) -> List[Release]:
        """Search AudiobookBay for audiobook releases.
        
        Args:
            book: Book metadata
            plan: Search plan with query variants
            expand_search: Ignored (always searches)
            content_type: Must be "audiobook" for this source
            
        Returns:
            List of Release objects
        """
        # Only search for audiobooks
        if content_type != "audiobook":
            return []
        
        hostname = config.get("ABB_HOSTNAME", "audiobookbay.lu")
        max_pages = config.get("ABB_PAGE_LIMIT", 5)
        
        # Build search query from plan
        if plan.manual_query:
            query = plan.manual_query
        elif plan.title_variants:
            # Use first title variant with author
            variant = plan.title_variants[0]
            query = f"{variant.title} {variant.author}".strip()
        else:
            query = book.title or ""
        
        if not query:
            logger.debug("No search query available")
            return []
        
        # Convert to lowercase (matching audiobookbay-automated implementation)
        query_lower = query.lower()
        logger.info(f"Searching AudiobookBay for: {query_lower}")
        
        try:
            # Search AudiobookBay
            results = scraper.search_audiobookbay(
                query=query_lower,
                max_pages=max_pages,
                hostname=hostname
            )
            
            # Extract query words for relevance checking
            query_words = set(word.lower() for word in query_lower.split() if len(word) > 2)
            
            releases = []
            for result in results:
                try:
                    title = result['title']
                    
                    # Basic relevance check: ensure title contains at least one query word
                    # This filters out homepage "Latest" feed items that may leak through
                    if query_words:
                        title_lower = title.lower()
                        if not any(word in title_lower for word in query_words):
                            logger.debug(f"Filtering out irrelevant result: {title}")
                            continue
                    
                    # Generate unique source ID
                    source_id = _generate_source_id(result['link'])
                    
                    # Extract and parse metadata
                    format_type = result.get('format')
                    size_str = result.get('size')
                    size_bytes = parse_size(size_str) if size_str else None
                    language_raw = result.get('language')
                    language_code = _map_language(language_raw) if language_raw else None
                    
                    # Create Release object
                    release = Release(
                        source="audiobookbay",
                        source_id=source_id,
                        title=title,
                        format=format_type.lower() if format_type else None,
                        language=language_code,
                        size=size_str,
                        size_bytes=size_bytes,
                        download_url=result['link'],  # Detail page URL (used by handler)
                        info_url=result['link'],  # Make title clickable
                        protocol=ReleaseProtocol.TORRENT,
                        indexer="AudiobookBay",
                        seeders=None,  # Not available on search page
                        peers=None,
                        content_type="audiobook",
                        extra={
                            "preview": result.get('cover'),
                            "detail_url": result['link'],
                            "bitrate": result.get('bitrate'),
                            "posted_date": result.get('posted_date'),
                            "language_raw": language_raw,  # Keep original for reference
                        }
                    )
                    releases.append(release)
                except Exception as e:
                    logger.warning(f"Failed to create release from result: {e}")
                    continue
            
            logger.info(f"Found {len(releases)} releases from AudiobookBay")
            return releases
            
        except Exception as e:
            logger.error(f"AudiobookBay search error: {e}")
            return []
    
    def is_available(self) -> bool:
        """Check if AudiobookBay source is enabled."""
        return config.get("ABB_ENABLED", False) is True
    
    def get_column_config(self) -> ReleaseColumnConfig:
        """Get column configuration for AudiobookBay releases.
        
        Shows title, language, format, and size columns.
        No seeders/peers since ABB doesn't show this on search page.
        """
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="language",
                    label="Lang",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    hide_mobile=True,
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                    fallback="",
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,
                ),
            ],
            grid_template="minmax(0,2fr) 60px 80px 80px",
            supported_filters=["format", "language"],  # Enable format and language filters
        )
