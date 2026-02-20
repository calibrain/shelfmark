"""AudiobookBay release source - searches AudiobookBay for audiobook torrents."""

import hashlib
import re
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
from shelfmark.release_sources.audiobookbay.utils import normalize_hostname, parse_size

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


def _split_title_and_author(raw_title: str) -> tuple[str, Optional[str]]:
    """Split titles in the form 'Title - Author' into title and author.

    Args:
        raw_title: The raw title string from the scrape.

    Returns:
        (title, author) where author is None if split is unavailable.
    """
    if not raw_title:
        return "", None

    cleaned_title = raw_title.strip()
    if " - " not in cleaned_title:
        return cleaned_title, None

    title_part, author_part = cleaned_title.rsplit(" - ", 1)
    title_part = title_part.strip()
    author_part = author_part.strip()
    if not title_part or not author_part:
        return cleaned_title, None

    return title_part, author_part


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


def _parse_bitrate_to_kbps(bitrate: Optional[str]) -> Optional[int]:
    """Parse bitrate string to an integer Kbps value.

    Args:
        bitrate: Human-readable bitrate (e.g., "128 Kbps")

    Returns:
        Bitrate value in Kbps as integer, or None if parsing fails.
    """
    if not bitrate:
        return None

    match = re.search(r"(\d+(?:\.\d+)?)\s*kbps", bitrate, re.IGNORECASE)
    if not match:
        return None

    try:
        return int(float(match.group(1)))
    except ValueError:
        return None


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
        
        hostname = normalize_hostname(config.get("ABB_HOSTNAME", ""))
        if not hostname:
            logger.debug("AudiobookBay hostname is not configured")
            return []
        max_pages = config.get("ABB_PAGE_LIMIT", 1)
        exact_phrase = bool(config.get("ABB_EXACT_PHRASE", False))
        
        # Build search query candidates from plan.
        query_candidates: list[str] = []
        if plan.manual_query:
            query_candidates.append(plan.manual_query.strip())
        elif plan.title_variants:
            variant = plan.title_variants[0]
            combined_query = f"{variant.title} {variant.author}".strip()
            title_only_query = (variant.title or "").strip()
            if combined_query:
                query_candidates.append(combined_query)
            if title_only_query and title_only_query.lower() != combined_query.lower():
                query_candidates.append(title_only_query)
        elif book.title:
            query_candidates.append(book.title.strip())

        # Remove empty and duplicate queries while preserving order.
        deduped_queries: list[str] = []
        seen_queries: set[str] = set()
        for candidate in query_candidates:
            normalized = candidate.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            deduped_queries.append(normalized)

        if not deduped_queries:
            logger.debug("No search query available")
            return []

        results = []
        query_lower = deduped_queries[0].lower()
        
        try:
            for index, query in enumerate(deduped_queries):
                query_lower = query.lower()
                logger.info(f"Searching AudiobookBay for: {query_lower}")

                # Search AudiobookBay
                results = scraper.search_audiobookbay(
                    query=query_lower,
                    max_pages=max_pages,
                    hostname=hostname,
                    exact_phrase=exact_phrase,
                )

                # For auto-generated queries, fallback to broad matching if exact phrase returns nothing.
                if exact_phrase and not results and not plan.manual_query:
                    logger.info("No exact phrase results, retrying AudiobookBay search without quotes")
                    results = scraper.search_audiobookbay(
                        query=query_lower,
                        max_pages=max_pages,
                        hostname=hostname,
                        exact_phrase=False,
                    )

                if results:
                    break

                if index < len(deduped_queries) - 1:
                    logger.info(
                        "No AudiobookBay results for '%s', retrying with '%s'",
                        query_lower,
                        deduped_queries[index + 1].lower(),
                    )
            
            # Extract query words for relevance checking
            query_words = set(word.lower() for word in query_lower.split() if len(word) > 2)
            
            releases = []
            for result in results:
                try:
                    raw_title = result['title']
                    title, author = _split_title_and_author(raw_title)
                    title_for_filter = raw_title.lower()
                    
                    # Basic relevance check: ensure title contains at least one query word
                    # This filters out homepage "Latest" feed items that may leak through
                    if query_words:
                        if not any(word in title_for_filter for word in query_words):
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
                    bitrate = result.get('bitrate')
                    bitrate_kbps = _parse_bitrate_to_kbps(bitrate)
                    
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
                            "bitrate": bitrate,
                            "bitrate_value": bitrate_kbps,
                            "posted_date": result.get('posted_date'),
                            "title_raw": raw_title,
                            "language_raw": language_raw,  # Keep original for reference
                            "author": author,  # Parsed author from title pattern
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
        """Check if AudiobookBay source is enabled and configured."""
        return config.get("ABB_ENABLED", False) is True and bool(normalize_hostname(config.get("ABB_HOSTNAME", "")))
    
    def get_column_config(self) -> ReleaseColumnConfig:
        """Get column configuration for AudiobookBay releases.
        
        Shows title, language, format, bitrate, and size columns.
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
                    key="extra.bitrate",
                    label="Bitrate",
                    render_type=ColumnRenderType.NUMBER,
                    align=ColumnAlign.CENTER,
                    width="72px",
                    hide_mobile=False,
                    fallback="",
                    sortable=True,
                    sort_key="extra.bitrate_value",
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,
                    sortable=True,
                    sort_key="size_bytes",
                ),
            ],
            grid_template="minmax(0,2fr) 60px 80px 72px 80px",
            supported_filters=["format", "language"],  # Enable format and language filters
        )
