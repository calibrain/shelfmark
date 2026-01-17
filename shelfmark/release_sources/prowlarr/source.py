"""Prowlarr release source - searches indexers for book releases (torrents/usenet)."""

import re
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from shelfmark.release_sources.search_plan import ReleaseSearchPlan

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
    LeadingCellConfig,
    LeadingCellType,
)
from shelfmark.release_sources.prowlarr.api import ProwlarrClient
from shelfmark.release_sources.prowlarr.cache import cache_release
from shelfmark.release_sources.prowlarr.utils import get_preferred_download_url, get_protocol

logger = setup_logger(__name__)


def _parse_size(size_bytes: Optional[int]) -> Optional[str]:
    """Convert bytes to human-readable size string."""
    if size_bytes is None or size_bytes <= 0:
        return None

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"

    return f"{size:.1f} {units[unit_index]}"


# Common ebook formats in priority order
EBOOK_FORMATS = ["epub", "mobi", "azw3", "azw", "pdf", "cbz", "cbr", "fb2", "djvu", "lit", "pdb", "txt"]

# Common audiobook formats
AUDIOBOOK_FORMATS = ["m4b", "mp3", "m4a", "flac", "ogg", "wma", "aac", "wav", "opus"]

# Combined list for format detection (audiobook formats first for priority)
ALL_BOOK_FORMATS = AUDIOBOOK_FORMATS + EBOOK_FORMATS


def _extract_format(title: str) -> Optional[str]:
    """Extract ebook/audiobook format from release title (extension, bracketed, or standalone)."""
    title_lower = title.lower()

    # Pattern priority: file extension > bracketed > standalone word
    # Use %s placeholder since {fmt} conflicts with regex syntax
    pattern_templates = [
        r'\.%s(?:["\'\s\]\)]|$)',   # .format at end or followed by delimiter
        r'[\[\(\{]%s[\]\)\}]',       # [EPUB], (PDF), {mobi}
        r'\b%s\b',                    # standalone word
    ]

    for template in pattern_templates:
        for fmt in ALL_BOOK_FORMATS:
            if re.search(template % fmt, title_lower):
                return fmt

    return None


def _extract_language(title: str) -> Optional[str]:
    """Extract language code from release title (e.g., [German] -> 'de')."""
    title_lower = title.lower()

    # Common language names and their codes
    languages = {
        "english": "en", "eng": "en", "[en]": "en", "(en)": "en",
        "german": "de", "deutsch": "de", "[de]": "de", "(de)": "de", "ger": "de",
        "french": "fr", "français": "fr", "[fr]": "fr", "(fr)": "fr", "fra": "fr",
        "spanish": "es", "español": "es", "[es]": "es", "(es)": "es", "spa": "es",
        "italian": "it", "italiano": "it", "[it]": "it", "(it)": "it", "ita": "it",
        "portuguese": "pt", "[pt]": "pt", "(pt)": "pt", "por": "pt",
        "dutch": "nl", "nederlands": "nl", "[nl]": "nl", "(nl)": "nl", "nld": "nl",
        "russian": "ru", "[ru]": "ru", "(ru)": "ru", "rus": "ru",
        "polish": "pl", "polski": "pl", "[pl]": "pl", "(pl)": "pl", "pol": "pl",
        "chinese": "zh", "[zh]": "zh", "(zh)": "zh", "chi": "zh",
        "japanese": "ja", "[ja]": "ja", "(ja)": "ja", "jpn": "ja",
        "korean": "ko", "[ko]": "ko", "(ko)": "ko", "kor": "ko",
    }

    for lang_pattern, lang_code in languages.items():
        if lang_pattern in title_lower:
            return lang_code

    return None


# Prowlarr category IDs for content type detection
# See: https://wiki.servarr.com/prowlarr/cardigann-yml-definition#categories
AUDIOBOOK_CATEGORY_IDS = {3000, 3030}  # 3000 = Audio, 3030 = Audio/Audiobook
EBOOK_CATEGORY_IDS = {7000, 7020}  # 7000 = Books, 7020 = Books/Ebook


def _detect_content_type_from_categories(categories: list, fallback: str = "book") -> str:
    """Detect content type from Prowlarr category IDs. Returns 'audiobook' or 'book'."""
    # Normalize fallback - convert "ebook" to "book" for display consistency
    normalized_fallback = "book" if fallback == "ebook" else fallback

    if not categories:
        return normalized_fallback

    # Extract category IDs from the nested structure
    cat_ids = {
        cat.get("id") if isinstance(cat, dict) else cat
        for cat in categories
        if (isinstance(cat, dict) and cat.get("id") is not None) or isinstance(cat, int)
    }

    # Check for audiobook categories first (more specific), then ebook
    if cat_ids & AUDIOBOOK_CATEGORY_IDS:
        return "audiobook"
    if cat_ids & EBOOK_CATEGORY_IDS:
        return "book"

    return normalized_fallback


def _prowlarr_result_to_release(result: dict, search_content_type: str = "ebook") -> Release:
    """Convert a Prowlarr API result to a Release object."""
    title = result.get("title", "Unknown")
    size_bytes = result.get("size")
    indexer = result.get("indexer", "Unknown")
    protocol = get_protocol(result)
    seeders = result.get("seeders")
    leechers = result.get("leechers")
    categories = result.get("categories", [])
    is_torrent = protocol == ReleaseProtocol.TORRENT

    # Format peers display string: "seeders / leechers"
    peers_display = (
        f"{seeders} / {leechers}"
        if is_torrent and seeders is not None and leechers is not None
        else None
    )

    # For format detection, prefer fileName over title (often cleaner)
    file_name = result.get("fileName", "")
    format_detected = _extract_format(file_name) if file_name else _extract_format(title)

    # Build the source_id from GUID or generate from indexer + title
    source_id = result.get("guid") or f"{indexer}:{hash(title)}"

    # Cache the raw Prowlarr result so handler can look it up by source_id
    cache_release(source_id, result)

    return Release(
        source="prowlarr",
        source_id=source_id,
        title=title,
        format=format_detected,
        language=_extract_language(title),
        size=_parse_size(size_bytes),
        size_bytes=size_bytes,
        download_url=get_preferred_download_url(result),
        info_url=result.get("infoUrl") or result.get("guid"),
        protocol=(
            ReleaseProtocol.TORRENT
            if protocol == "torrent"
            else ReleaseProtocol.NZB
            if protocol == "usenet"
            else None
        ),
        indexer=indexer,
        seeders=seeders if is_torrent else None,
        peers=peers_display,
        content_type=_detect_content_type_from_categories(categories, search_content_type),
        extra={
            "publish_date": result.get("publishDate"),
            "categories": categories,
            "indexer_id": result.get("indexerId"),
            "files": result.get("files"),
            "grabs": result.get("grabs"),
        },
    )


@register_source("prowlarr")
class ProwlarrSource(ReleaseSource):
    """Prowlarr release source for ebooks and audiobooks."""

    name = "prowlarr"
    display_name = "Prowlarr"
    supported_content_types = ["ebook", "audiobook"]  # Explicitly declare support for both

    def __init__(self):
        self.last_search_type: Optional[str] = None

    def get_column_config(self) -> ReleaseColumnConfig:
        """Column configuration for Prowlarr releases."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="indexer",
                    label="Indexer",
                    render_type=ColumnRenderType.TEXT,
                    align=ColumnAlign.LEFT,
                    width="minmax(80px, 1fr)",
                    hide_mobile=True,
                    sortable=True,
                ),
                ColumnSchema(
                    key="protocol",
                    label="Type",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="download_type"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="peers",
                    label="Peers",
                    render_type=ColumnRenderType.PEERS,
                    align=ColumnAlign.CENTER,
                    width="70px",
                    hide_mobile=True,
                    fallback="-",
                    sortable=True,
                    sort_key="seeders",
                ),
                ColumnSchema(
                    key="content_type",
                    label="Type",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="90px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="content_type"),
                    uppercase=True,
                    fallback="-",
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
            grid_template="minmax(0,2fr) minmax(80px,1fr) 60px 70px 90px 80px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),  # No leading cell for Prowlarr
            supported_filters=["language"],  # Enables multi-language query expansion; Prowlarr language metadata is unreliable
        )

    def _get_client(self) -> Optional[ProwlarrClient]:
        """Get a configured Prowlarr client or None if not configured."""
        url = config.get("PROWLARR_URL", "")
        api_key = config.get("PROWLARR_API_KEY", "")

        if not url or not api_key:
            return None

        return ProwlarrClient(url, api_key)

    def _get_selected_indexer_ids(self) -> Optional[List[int]]:
        """
        Get list of selected indexer IDs from config.

        Returns None if no indexers are selected (search all).
        Returns list of IDs if specific indexers are selected.
        """
        selected = config.get("PROWLARR_INDEXERS", "")
        if not selected:
            return None

        # Handle both list (from JSON config) and string (from env var)
        try:
            if isinstance(selected, list):
                # Already a list from JSON config
                ids = [int(x) for x in selected if x]
            else:
                # Comma-separated string from env var
                ids = [int(x.strip()) for x in selected.split(",") if x.strip()]
            return ids if ids else None
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid PROWLARR_INDEXERS format: {selected} ({e})")
            return None

    def search(
        self,
        book: BookMetadata,
        plan: "ReleaseSearchPlan",  # noqa: F821
        expand_search: bool = False,
        content_type: str = "ebook"
    ) -> List[Release]:
        """Search Prowlarr indexers for releases matching the book."""
        client = self._get_client()
        if not client:
            logger.warning("Prowlarr not configured - skipping search")
            return []

        queries = [v.query for v in plan.title_variants if v.query]
        queries = [q for q in queries if q]

        if not queries and plan.isbn_candidates:
            queries = list(plan.isbn_candidates)

        if not queries:
            logger.warning("No search query available for book")
            return []

        # Get selected indexer IDs from config (None means search all)
        indexer_ids = self._get_selected_indexer_ids()

        # Get search categories based on content type
        # Audiobooks use 3030 (Audio/Audiobook), ebooks use 7000 (Books)
        search_categories = [3030] if content_type == "audiobook" else [7000]

        # Manual query override should behave like normal Prowlarr searches:
        # - default: search within the content-type categories
        # - expand: rerun without categories
        if plan.manual_query:
            categories = None if expand_search else search_categories
            self.last_search_type = "manual_expanded" if expand_search else "manual_query"
        else:
            categories = None if expand_search else search_categories
            self.last_search_type = "expanded" if expand_search else "categories"

        indexer_desc = f"indexers={indexer_ids}" if indexer_ids else "all enabled indexers"
        if len(queries) == 1:
            logger.debug(f"Searching Prowlarr: query='{queries[0]}', {indexer_desc}, categories={categories}")
        else:
            logger.debug(f"Searching Prowlarr: {len(queries)} queries, {indexer_desc}, categories={categories}")

        def search_indexers(query: str, cats: Optional[List[int]]) -> List[dict]:
            """Search indexers with given categories, collecting results."""
            results = []
            if indexer_ids:
                # Search specific indexers one at a time
                for indexer_id in indexer_ids:
                    try:
                        raw = client.search(query=query, indexer_ids=[indexer_id], categories=cats)
                        if raw:
                            results.extend(raw)
                    except Exception as e:
                        logger.warning(f"Search failed for indexer {indexer_id}: {e}")
            else:
                # Search all enabled indexers at once
                try:
                    raw = client.search(query=query, indexer_ids=None, categories=cats)
                    if raw:
                        results.extend(raw)
                except Exception as e:
                    logger.warning(f"Search failed for all indexers: {e}")
            return results

        try:
            auto_expand_enabled = config.get("PROWLARR_AUTO_EXPAND", False)

            seen_keys: set[str] = set()
            all_results: List[dict] = []

            for idx, query in enumerate(queries, start=1):
                if len(queries) > 1:
                    logger.debug(f"Prowlarr query {idx}/{len(queries)}: '{query}'")

                raw_results = search_indexers(query=query, cats=categories)

                # Auto-expand: if no results with categories and auto-expand enabled, retry without
                if not raw_results and categories and auto_expand_enabled:
                    logger.info(f"Prowlarr: no results for query '{query}' with category filter, auto-expanding search")
                    raw_results = search_indexers(query=query, cats=None)
                    self.last_search_type = "expanded"

                for r in raw_results:
                    key = (
                        r.get("guid")
                        or r.get("downloadUrl")
                        or r.get("magnetUrl")
                        or r.get("infoUrl")
                        or f"{r.get('indexerId')}:{r.get('title')}"
                    )
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    all_results.append(r)

            results = [_prowlarr_result_to_release(r, content_type) for r in all_results]

            if results:
                torrent_count = sum(1 for r in results if r.protocol == ReleaseProtocol.TORRENT)
                nzb_count = sum(1 for r in results if r.protocol == ReleaseProtocol.NZB)
                indexers = sorted(set(r.indexer for r in results if r.indexer))
                indexer_str = ", ".join(indexers) if indexers else "unknown"
                logger.info(f"Prowlarr: {len(results)} results ({torrent_count} torrent, {nzb_count} nzb) from {indexer_str}")
            else:
                logger.debug("Prowlarr: no results found")

            return results

        except Exception as e:
            logger.error(f"Prowlarr search failed: {e}")
            return []

    def is_available(self) -> bool:
        """Check if Prowlarr is enabled and configured."""
        if not config.get("PROWLARR_ENABLED", False):
            return False
        url = config.get("PROWLARR_URL", "")
        api_key = config.get("PROWLARR_API_KEY", "")
        return bool(url and api_key)
