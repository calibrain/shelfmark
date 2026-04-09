"""Prowlarr release source - searches indexers for book releases (torrents/usenet)."""

import re
import time
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.search_plan import ReleaseSearchVariant
from shelfmark.core.utils import normalize_http_url
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import (
    ColumnAlign,
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    LeadingCellConfig,
    LeadingCellType,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    SortOption,
    register_source,
)
from shelfmark.release_sources.prowlarr.api import ProwlarrClient
from shelfmark.release_sources.prowlarr.cache import cache_release
from shelfmark.release_sources.prowlarr.utils import (
    get_preferred_download_url,
    get_protocol,
)

logger = setup_logger(__name__)

_SIZE_UNIT_BASE = 1024
_TWO_FORMATS = 2


def _raise_timeout_error(message: str):
    raise TimeoutError(message)


def _parse_size(size_bytes: int | None) -> str | None:
    """Convert bytes to human-readable size string."""
    if size_bytes is None or size_bytes <= 0:
        return None

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    unit_index = 0

    while size >= _SIZE_UNIT_BASE and unit_index < len(units) - 1:
        size /= _SIZE_UNIT_BASE
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"

    return f"{size:.1f} {units[unit_index]}"


# Common ebook formats in priority order
EBOOK_FORMATS = [
    "epub",
    "mobi",
    "azw3",
    "azw",
    "pdf",
    "cbz",
    "cbr",
    "fb2",
    "djvu",
    "lit",
    "pdb",
    "txt",
]

# Common audiobook formats
AUDIOBOOK_FORMATS = ["m4b", "mp3", "m4a", "flac", "ogg", "wma", "aac", "wav", "opus"]

# Combined list for format detection (audiobook formats first for priority)
ALL_BOOK_FORMATS = AUDIOBOOK_FORMATS + EBOOK_FORMATS

# Map 3-char MAM language codes to 2-char ISO codes used by frontend color maps
MAM_LANGUAGE_MAP = {
    "eng": "en",
    "ita": "it",
    "spa": "es",
    "fra": "fr",
    "fre": "fr",
    "ger": "de",
    "deu": "de",
    "por": "pt",
    "rus": "ru",
    "jpn": "ja",
    "jap": "ja",
    "chi": "zh",
    "zho": "zh",
    "dut": "nl",
    "nld": "nl",
    "swe": "sv",
    "nor": "no",
    "dan": "da",
    "fin": "fi",
    "pol": "pl",
    "cze": "cs",
    "ces": "cs",
    "hun": "hu",
    "kor": "ko",
    "ara": "ar",
    "heb": "he",
    "tur": "tr",
    "gre": "el",
    "ell": "el",
    "hin": "hi",
    "tha": "th",
    "vie": "vi",
    "ind": "id",
    "ukr": "uk",
    "rom": "ro",
    "ron": "ro",
    "bul": "bg",
    "cat": "ca",
    "hrv": "hr",
    "slv": "sl",
    "srp": "sr",
}

# Backend safeguard: cap total Prowlarr search time per request.
PROWLARR_SEARCH_TIMEOUT_SECONDS = 120.0


def _extract_format(title: str) -> str | None:
    """Extract ebook/audiobook format from release title (extension, bracketed, or standalone)."""
    title_lower = title.lower()

    # Pattern priority: file extension > bracketed > standalone word
    # Use %s placeholder since {fmt} conflicts with regex syntax
    pattern_templates = [
        r'\.%s(?:["\'\s\]\)]|$)',  # .format at end or followed by delimiter
        r"[\[\(\{]%s[\]\)\}]",  # [EPUB], (PDF), {mobi}
        r"\b%s\b",  # standalone word
    ]

    for template in pattern_templates:
        for fmt in ALL_BOOK_FORMATS:
            if re.search(template % fmt, title_lower):
                return fmt

    return None


def _extract_mam_language(raw_title: str) -> str | None:
    """Extract the language code from MyAnonamouse titles.

    Prowlarr's MAM parser appends a structured bracket segment like:
      [ENG / EPUB MOBI PDF]

    The language code appears before the "/" - we extract it and map to
    the 2-char ISO code used by the frontend color maps.
    """
    if not raw_title:
        return None

    for bracket in re.findall(r"\[([^\]]+)\]", raw_title):
        if "/" not in bracket:
            continue

        before_slash, _ = bracket.split("/", 1)
        # Extract the language token (should be a 3-char code like ENG, ITA, etc.)
        tokens = re.findall(r"[A-Za-z]+", before_slash.strip())

        for token in tokens:
            lang_code = token.lower()
            if lang_code in MAM_LANGUAGE_MAP:
                return MAM_LANGUAGE_MAP[lang_code]

    return None


def _extract_mam_formats(raw_title: str) -> list[str]:
    """Extract a list of formats from MyAnonamouse titles.

    Prowlarr's MAM parser appends a structured bracket segment like:
      [ENG / EPUB MOBI PDF]

    We only trust this structured segment (and do not attempt generic title
    heuristics for other indexers).
    """
    if not raw_title:
        return []

    format_set = set(ALL_BOOK_FORMATS)
    for bracket in re.findall(r"\[([^\]]+)\]", raw_title):
        if "/" not in bracket:
            continue

        _, after_slash = bracket.split("/", 1)
        tokens = re.findall(r"[A-Za-z0-9]+", after_slash)

        formats: list[str] = []
        for token in tokens:
            fmt = token.lower()
            if fmt in format_set and fmt not in formats:
                formats.append(fmt)

        if formats:
            return formats

    return []


def _formats_display(formats: list[str]) -> str | None:
    if not formats:
        return None
    if len(formats) == 1:
        return formats[0]
    if len(formats) == _TWO_FORMATS:
        return f"{formats[0]}, {formats[1]}"
    # Show first two formats + count of others to prevent overflow
    return f"{formats[0]}, {formats[1]} +{len(formats) - 2}"


# Prowlarr category IDs for content type detection
# See: https://wiki.servarr.com/prowlarr/cardigann-yml-definition#categories
AUDIOBOOK_CATEGORY_IDS = {3000, 3030}  # 3000 = Audio, 3030 = Audio/Audiobook
BOOK_CATEGORY_RANGE = range(7000, 8000)  # 7000-7999 = Books (all subcategories)


def _detect_content_type_from_categories(
    categories: list, fallback: str = "book"
) -> str:
    """Detect content type from Prowlarr category IDs. Returns 'audiobook', 'book', or 'other'."""
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

    if not cat_ids:
        return normalized_fallback

    # Check for audiobook categories first (more specific), then any book range
    if cat_ids & AUDIOBOOK_CATEGORY_IDS:
        return "audiobook"
    if any(cat_id in BOOK_CATEGORY_RANGE for cat_id in cat_ids):
        return "book"

    # Categories are present but not book/audiobook
    return "other"


def _extract_capability_category_ids(categories: list[dict]) -> set[int]:
    """Flatten capability categories and subcategories into a single ID set."""
    category_ids: set[int] = set()

    for category in categories:
        if not isinstance(category, dict):
            continue

        category_id = category.get("id")
        if isinstance(category_id, int):
            category_ids.add(category_id)

        for subcategory in category.get("subCategories", []):
            if not isinstance(subcategory, dict):
                continue
            subcategory_id = subcategory.get("id")
            if isinstance(subcategory_id, int):
                category_ids.add(subcategory_id)

    return category_ids


def _indexer_supports_search_categories(
    indexer: dict, categories: list[int] | None
) -> bool:
    """Return whether an indexer should be queried for the requested categories."""
    if not categories:
        return True

    capability_categories = indexer.get("capabilities", {}).get("categories", [])
    category_ids = _extract_capability_category_ids(capability_categories)
    if not category_ids:
        return True

    for requested_category in categories:
        if requested_category in BOOK_CATEGORY_RANGE:
            if any(cat_id in BOOK_CATEGORY_RANGE for cat_id in category_ids):
                return True
            continue

        if requested_category in category_ids:
            return True

    return False


def _prowlarr_result_to_release(
    result: dict,
    search_content_type: str = "ebook",
    *,
    enable_format_detection: bool = False,
) -> Release:
    """Convert a Prowlarr API result to a Release object."""
    raw_title = result.get("title", "Unknown")
    title = raw_title
    size_bytes = result.get("size")
    indexer = result.get("indexer", "Unknown")
    protocol = get_protocol(result)
    seeders = result.get("seeders")
    leechers = result.get("leechers")
    categories = result.get("categories", [])
    is_torrent = protocol == ReleaseProtocol.TORRENT
    raw_indexer_flags = result.get("indexerFlags") or []
    indexer_flags: list[str] = []
    seen_flags: set[str] = set()

    def add_indexer_flag(flag: object) -> None:
        if flag is None:
            return
        flag_str = str(flag).strip()
        if not flag_str:
            return
        lowered = flag_str.lower()
        if lowered in seen_flags:
            return
        seen_flags.add(lowered)
        indexer_flags.append(flag_str)

    if isinstance(raw_indexer_flags, list):
        for flag in raw_indexer_flags:
            add_indexer_flag(flag)
    elif isinstance(raw_indexer_flags, str):
        add_indexer_flag(raw_indexer_flags)

    # Format peers display string: "seeders / leechers"
    peers_display = (
        f"{seeders} / {leechers}"
        if is_torrent and seeders is not None and leechers is not None
        else None
    )

    format_detected: str | None = None
    formats: list[str] = []
    formats_display: str | None = None
    language_detected: str | None = None
    if enable_format_detection:
        book_title = str(result.get("bookTitle") or "").strip()
        if book_title:
            title = book_title

        formats = _extract_mam_formats(str(raw_title or ""))
        format_detected = formats[0] if formats else None
        formats_display = _formats_display(formats)
        language_detected = _extract_mam_language(str(raw_title or ""))

    # Build the source_id from GUID or generate from indexer + title
    source_id = result.get("guid") or f"{indexer}:{hash(raw_title)}"

    # Cache the raw Prowlarr result so handler can look it up by source_id
    cache_release(source_id, result)

    # Derive common indicators from torznab/newznab attrs when present.
    download_volume_factor = result.get("downloadVolumeFactor")
    is_freeleech = False
    try:
        if download_volume_factor is not None and float(download_volume_factor) == 0.0:
            is_freeleech = True
    except (TypeError, ValueError):
        pass

    if any(flag.lower() in {"freeleech", "fl"} for flag in indexer_flags):
        is_freeleech = True

    is_vip = "[vip]" in str(raw_title).lower()
    if is_vip:
        add_indexer_flag("VIP")
    if is_freeleech:
        add_indexer_flag("FreeLeech")

    return Release(
        source="prowlarr",
        source_id=source_id,
        title=title,
        format=format_detected,
        language=language_detected,
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
        content_type=_detect_content_type_from_categories(
            categories, search_content_type
        ),
        extra={
            "publish_date": result.get("publishDate"),
            "categories": categories,
            "indexer_id": result.get("indexerId"),
            "files": result.get("files"),
            "grabs": result.get("grabs"),
            "author": result.get("author"),
            "book_title": result.get("bookTitle"),
            "indexer_flags": indexer_flags,
            "vip": is_vip,
            "freeleech": is_freeleech,
            "download_volume_factor": result.get("downloadVolumeFactor"),
            "upload_volume_factor": result.get("uploadVolumeFactor"),
            "minimum_ratio": result.get("minimumRatio"),
            "minimum_seed_time": result.get("minimumSeedTime"),
            "info_hash": result.get("infoHash"),
            "formats": formats or None,
            "formats_display": formats_display,
            # Raw torznab attributes for rich tooltips (enriched indexers)
            "torznab_attrs": result.get("torznabAttrs"),
        },
    )


@register_source("prowlarr")
class ProwlarrSource(ReleaseSource):
    """Prowlarr release source for ebooks and audiobooks."""

    name = "prowlarr"
    display_name = "Prowlarr"
    supported_content_types = [
        "ebook",
        "audiobook",
    ]  # Explicitly declare support for both

    def __init__(self):
        self.last_search_type: str | None = None

    def get_column_config(self) -> ReleaseColumnConfig:
        """Column configuration for Prowlarr releases."""
        # Fetch available indexers from Prowlarr
        available_indexers: list[str] | None = None
        default_indexers: list[str] | None = None
        client = self._get_client()
        if client:
            try:
                enabled_indexers = client.get_enabled_indexers_detailed()
                # Get user-selected indexer IDs if configured
                selected_ids = self._get_selected_indexer_ids()

                all_indexer_names = []
                selected_indexer_names = []

                for idx in enabled_indexers:
                    idx_id = idx.get("id")
                    idx_name = idx.get("name")
                    if not idx_name:
                        continue

                    # Add to all indexers list
                    all_indexer_names.append(idx_name)

                    # If user has selected specific indexers, track those separately
                    if selected_ids is not None:
                        try:
                            if int(idx_id) in selected_ids:
                                selected_indexer_names.append(idx_name)
                        except (TypeError, ValueError):
                            pass

                available_indexers = (
                    sorted(all_indexer_names) if all_indexer_names else None
                )
                # Only set default_indexers if user has selected specific ones
                default_indexers = (
                    sorted(selected_indexer_names) if selected_indexer_names else None
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to fetch indexer list for column config: %s", e)

        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="indexer",
                    label="Indexer",
                    render_type=ColumnRenderType.INDEXER_PROTOCOL,
                    align=ColumnAlign.LEFT,
                    width="minmax(140px, 1fr)",
                    hide_mobile=False,
                    sortable=True,
                ),
                ColumnSchema(
                    key="extra.indexer_flags",
                    label="Flags",
                    render_type=ColumnRenderType.TAGS,
                    align=ColumnAlign.CENTER,
                    width="50px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="flags"),
                    fallback="",
                    uppercase=True,
                ),
                ColumnSchema(
                    key="language",
                    label="Lang",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="50px",
                    hide_mobile=True,
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                    fallback="",
                ),
                ColumnSchema(
                    key="extra.formats_display",
                    label="Format",
                    render_type=ColumnRenderType.FORMAT_CONTENT_TYPE,
                    align=ColumnAlign.CENTER,
                    width="90px",
                    hide_mobile=False,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                    fallback="",
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
            extra_sort_options=[
                SortOption(label="Peers", sort_key="seeders"),
            ],
            grid_template="minmax(0,2fr) minmax(140px,1fr) 50px 50px 90px 80px",
            leading_cell=LeadingCellConfig(
                type=LeadingCellType.NONE
            ),  # No leading cell for Prowlarr
            available_indexers=available_indexers,
            default_indexers=default_indexers,
            supported_filters=[
                "language",
                "indexer",
            ],  # Enables multi-language query expansion and indexer filtering
        )

    def _get_client(self) -> ProwlarrClient | None:
        """Get a configured Prowlarr client or None if not configured."""
        raw_url = config.get("PROWLARR_URL", "")
        api_key = config.get("PROWLARR_API_KEY", "")

        if not raw_url or not api_key:
            return None

        url = normalize_http_url(raw_url)
        if not url:
            return None

        return ProwlarrClient(url, api_key)

    def _get_selected_indexer_ids(self) -> list[int] | None:
        """Get list of selected indexer IDs from config.

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
        except (ValueError, TypeError) as e:
            logger.warning("Invalid PROWLARR_INDEXERS format: %s (%s)", selected, e)
            return None
        else:
            return ids or None

    def _resolve_indexer_ids_from_names(
        self, client: ProwlarrClient, names: list[str]
    ) -> list[int] | None:
        """Convert indexer names to IDs by looking up enabled indexers.

        Returns None if no names could be resolved.
        """
        if not names:
            return None

        try:
            enabled_indexers = client.get_enabled_indexers_detailed()
            name_to_id = {
                idx.get("name"): idx.get("id")
                for idx in enabled_indexers
                if idx.get("name") and idx.get("id") is not None
            }

            ids = []
            for name in names:
                idx_id = name_to_id.get(name)
                if idx_id is not None:
                    with suppress(TypeError, ValueError):
                        ids.append(int(idx_id))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to resolve indexer names to IDs: %s", e)
            return None
        else:
            return ids or None

    def _get_search_indexer_ids(
        self,
        client: ProwlarrClient,
        selected_indexer_ids: list[int] | None,
        categories: list[int] | None,
    ) -> list[int]:
        """Resolve the concrete indexer IDs to query via Torznab."""
        if selected_indexer_ids is not None:
            return selected_indexer_ids

        try:
            enabled_indexers = client.get_enabled_indexers_detailed()
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load enabled Prowlarr indexers: %s", e)
            return []

        indexer_ids: list[int] = []
        for indexer in enabled_indexers:
            if not _indexer_supports_search_categories(indexer, categories):
                continue

            indexer_id = indexer.get("id")
            try:
                indexer_ids.append(int(indexer_id))
            except (TypeError, ValueError):
                continue

        return indexer_ids

    def search(
        self,
        book: BookMetadata,
        plan: "ReleaseSearchPlan",
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search Prowlarr indexers for releases matching the book."""
        client = self._get_client()
        if not client:
            logger.warning("Prowlarr not configured - skipping search")
            return []

        variants = [v for v in plan.title_variants if v.title]

        if not variants and plan.isbn_candidates:
            variants = [
                ReleaseSearchVariant(title=isbn, author="", languages=None)
                for isbn in plan.isbn_candidates
            ]

        if not variants:
            logger.warning("No search query available for book")
            return []

        # Get indexer IDs: prefer plan.indexers (from filter), else use settings
        if plan.indexers:
            indexer_ids = self._resolve_indexer_ids_from_names(client, plan.indexers)
            logger.debug(
                "Using filter-specified indexers: %s -> IDs %s",
                plan.indexers,
                indexer_ids,
            )
        else:
            indexer_ids = self._get_selected_indexer_ids()

        # Get search categories based on content type
        # Audiobooks use 3030 (Audio/Audiobook), ebooks use 7000 (Books)
        search_categories = [3030] if content_type == "audiobook" else [7000]

        # Manual query override should behave like normal Prowlarr searches:
        # - default: search within the content-type categories
        # - expand: rerun without categories
        if plan.manual_query:
            categories = None if expand_search else search_categories
            self.last_search_type = (
                "manual_expanded" if expand_search else "manual_query"
            )
        else:
            categories = None if expand_search else search_categories
            self.last_search_type = "expanded" if expand_search else "categories"

        if plan.manual_query:
            query_type = "manual"
        elif not plan.title_variants and plan.isbn_candidates:
            query_type = "isbn"
        else:
            query_type = "title"

        indexer_desc = (
            f"indexers={indexer_ids}" if indexer_ids else "all enabled indexers"
        )
        if len(variants) == 1:
            logger.debug(
                "Searching Prowlarr: %s='%s', %s, categories=%s",
                query_type,
                variants[0].title,
                indexer_desc,
                categories,
            )
        else:
            logger.debug(
                "Searching Prowlarr: %s (%s variants), %s, categories=%s",
                query_type,
                len(variants),
                indexer_desc,
                categories,
            )

        try:
            auto_expand_enabled = config.get("PROWLARR_AUTO_EXPAND", False)
            deadline = time.monotonic() + PROWLARR_SEARCH_TIMEOUT_SECONDS
            # Some indexers benefit from title+author queries and extra format detection.
            enriched_indexer_ids = client.get_enriched_indexer_ids(
                restrict_to=indexer_ids
            )
            enriched_indexer_ids_set = set(enriched_indexer_ids)

            def _check_timeout() -> None:
                if time.monotonic() > deadline:
                    _raise_timeout_error(
                        f"Prowlarr search timed out after {int(PROWLARR_SEARCH_TIMEOUT_SECONDS)}s"
                    )

            def search_indexers(
                query: str, cats: list[int] | None, *, enriched_query: str | None = None
            ) -> list[dict]:
                """Search indexers with given categories via Torznab/Newznab."""
                results: list[dict] = []
                target_indexer_ids = self._get_search_indexer_ids(
                    client, indexer_ids, cats
                )
                if not target_indexer_ids:
                    return results

                for indexer_id in target_indexer_ids:
                    _check_timeout()
                    indexer_query = (
                        enriched_query
                        if indexer_id in enriched_indexer_ids_set and enriched_query
                        else query
                    )
                    raw = client.torznab_search(
                        indexer_id=indexer_id,
                        query=indexer_query,
                        categories=cats,
                        search_type="book",
                    )
                    if raw:
                        results.extend(raw)

                return results

            seen_keys: set[str] = set()
            all_results: list[dict] = []

            for idx, variant in enumerate(variants, start=1):
                _check_timeout()
                query = variant.title
                enriched_query = variant.query  # title + author

                if len(variants) > 1:
                    logger.debug(
                        "Prowlarr query %s/%s: '%s'", idx, len(variants), query
                    )

                raw_results = search_indexers(
                    query=query, cats=categories, enriched_query=enriched_query
                )

                # Auto-expand: if no results with categories and auto-expand enabled, retry without
                if not raw_results and categories and auto_expand_enabled:
                    _check_timeout()
                    logger.info(
                        "Prowlarr: no results for query '%s' with category filter, auto-expanding search",  # noqa: E501
                    )
                    raw_results = search_indexers(
                        query=query, cats=None, enriched_query=enriched_query
                    )
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

            results: list[Release] = []
            enriched_source_ids: set[str] = set()

            for r in all_results:
                idx_id = r.get("indexerId")
                try:
                    idx_id_int = int(idx_id) if idx_id is not None else None
                except (TypeError, ValueError):
                    idx_id_int = None

                is_enriched = bool(
                    idx_id_int is not None and idx_id_int in enriched_indexer_ids_set
                )
                release = _prowlarr_result_to_release(
                    r,
                    content_type,
                    enable_format_detection=is_enriched,
                )
                results.append(release)

                if is_enriched:
                    enriched_source_ids.add(release.source_id)

            # Sort results: enriched indexers first, then others
            results.sort(key=lambda r: 0 if r.source_id in enriched_source_ids else 1)

            if results:
                torrent_count = sum(
                    1 for r in results if r.protocol == ReleaseProtocol.TORRENT
                )
                nzb_count = sum(1 for r in results if r.protocol == ReleaseProtocol.NZB)
                indexers = sorted({r.indexer for r in results if r.indexer})
                indexer_str = ", ".join(indexers) if indexers else "unknown"
                logger.info(
                    "Prowlarr: %s results (%s torrent, %s nzb) from %s",
                    len(results),
                    torrent_count,
                    nzb_count,
                    indexer_str,
                )
            else:
                logger.debug("Prowlarr: no results found")

        except TimeoutError as e:
            logger.warning("Prowlarr search timed out: %s", e)
            raise
        except Exception:
            logger.exception("Prowlarr search failed")
            return []
        else:
            return results

    def is_available(self) -> bool:
        """Check if Prowlarr is enabled and configured."""
        if not config.get("PROWLARR_ENABLED", False):
            return False
        url = normalize_http_url(config.get("PROWLARR_URL", ""))
        api_key = config.get("PROWLARR_API_KEY", "")
        return bool(url and api_key)
