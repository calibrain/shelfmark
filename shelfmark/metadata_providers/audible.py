"""Audible metadata provider backed by the AudiMeta API."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from shelfmark.core.cache import cacheable
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_bool
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    NumberField,
    SelectField,
    TextField,
    register_settings,
)
from shelfmark.core.utils import normalize_http_url
from shelfmark.metadata_providers import (
    BookMetadata,
    DisplayField,
    MetadataCapability,
    MetadataProvider,
    MetadataSearchOptions,
    SearchResult,
    SearchType,
    SortOrder,
    TextSearchField,
    register_provider,
    register_provider_kwargs,
)

logger = setup_logger(__name__)

AUDIBLE_DEFAULT_BASE_URL = "https://audimeta.de"
AUDIBLE_DEFAULT_REGION = "us"
AUDIBLE_DEFAULT_TIMEOUT = 15
AUDIBLE_DEFAULT_USER_AGENT = (
    "Shelfmark Audible Provider/1.0 "
    "(+https://github.com/calibrain/shelfmark; metadata-provider)"
)
AUDIBLE_MAX_LIMIT = 50
AUDIBLE_SERIES_SUGGESTION_LIMIT = 7

_ISBN_CLEAN_RE = re.compile(r"[^0-9Xx]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")

_AUDIBLE_REGION_OPTIONS = [
    {"value": "us", "label": "United States"},
    {"value": "ca", "label": "Canada"},
    {"value": "uk", "label": "United Kingdom"},
    {"value": "au", "label": "Australia"},
    {"value": "fr", "label": "France"},
    {"value": "de", "label": "Germany"},
    {"value": "jp", "label": "Japan"},
    {"value": "it", "label": "Italy"},
    {"value": "in", "label": "India"},
    {"value": "es", "label": "Spain"},
    {"value": "br", "label": "Brazil"},
]
_AUDIBLE_REGION_VALUES = {option["value"] for option in _AUDIBLE_REGION_OPTIONS}

_AUDIBLE_SORT_OPTIONS = [
    {"value": "relevance", "label": "Most relevant"},
    {"value": "popularity", "label": "Best sellers"},
    {"value": "rating", "label": "Highest rated"},
    {"value": "newest", "label": "Newest"},
    {"value": "oldest", "label": "Oldest"},
]

_AUDIBLE_SORT_MAPPING: Dict[SortOrder, Optional[str]] = {
    SortOrder.RELEVANCE: "Relevance",
    SortOrder.POPULARITY: "BestSellers",
    SortOrder.RATING: "AvgRating",
    SortOrder.NEWEST: "-ReleaseDate",
    SortOrder.OLDEST: "ReleaseDate",
    SortOrder.SERIES_ORDER: None,
}


def _normalize_base_url(value: Any) -> str:
    normalized = normalize_http_url(value, default_scheme="https")
    return normalized or AUDIBLE_DEFAULT_BASE_URL


def _normalize_region(value: Any) -> str:
    normalized = str(value or AUDIBLE_DEFAULT_REGION).strip().lower()
    return normalized if normalized in _AUDIBLE_REGION_VALUES else AUDIBLE_DEFAULT_REGION


def _coerce_timeout(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return AUDIBLE_DEFAULT_TIMEOUT
    return parsed if parsed > 0 else AUDIBLE_DEFAULT_TIMEOUT


def _normalize_user_agent(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized or AUDIBLE_DEFAULT_USER_AGENT


def _clean_isbn(value: Any) -> str:
    if value is None:
        return ""
    return _ISBN_CLEAN_RE.sub("", str(value)).upper().strip()


def _extract_publish_year(release_date: Any) -> Optional[int]:
    if not release_date:
        return None

    try:
        return datetime.fromisoformat(str(release_date)).year
    except (TypeError, ValueError):
        return None


def _is_future_release(release_date: Any) -> bool:
    if not release_date:
        return False

    try:
        parsed = datetime.fromisoformat(str(release_date))
    except (TypeError, ValueError):
        return False

    now = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.date() > now.date()
    return parsed.astimezone(timezone.utc).date() > now.date()


def _parse_series_position(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _format_runtime(minutes: Any) -> Optional[str]:
    try:
        total_minutes = int(round(float(minutes)))
    except (TypeError, ValueError):
        return None

    if total_minutes <= 0:
        return None

    hours, remainder = divmod(total_minutes, 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def _format_narrator_value(names: List[str]) -> Optional[str]:
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    return f"{names[0]} +{len(names) - 1}"


def _sanitize_description(value: Any) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)<p[^>]*>", "", text)
    text = html.unescape(_HTML_TAG_RE.sub("", text))
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip() or None


def _coerce_list_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def _dedupe_texts(values: List[str]) -> List[str]:
    seen: set[str] = set()
    deduped: List[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _effective_audible_kwargs(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    current_values = current_values or {}
    return {
        "base_url": _normalize_base_url(
            current_values.get("AUDIBLE_BASE_URL")
            or app_config.get("AUDIBLE_BASE_URL", AUDIBLE_DEFAULT_BASE_URL)
        ),
        "region": _normalize_region(
            current_values.get("AUDIBLE_REGION")
            or app_config.get("AUDIBLE_REGION", AUDIBLE_DEFAULT_REGION)
        ),
        "timeout": _coerce_timeout(
            current_values.get("AUDIBLE_REQUEST_TIMEOUT")
            or app_config.get("AUDIBLE_REQUEST_TIMEOUT", AUDIBLE_DEFAULT_TIMEOUT)
        ),
        "user_agent": _normalize_user_agent(
            current_values.get("AUDIBLE_USER_AGENT")
            or app_config.get("AUDIBLE_USER_AGENT", AUDIBLE_DEFAULT_USER_AGENT)
        ),
        "use_upstream_cache": coerce_bool(
            current_values.get("AUDIBLE_USE_UPSTREAM_CACHE"),
            app_config.get("AUDIBLE_USE_UPSTREAM_CACHE", True),
        ),
        "exclude_unreleased": coerce_bool(
            current_values.get("AUDIBLE_EXCLUDE_UNRELEASED"),
            app_config.get("AUDIBLE_EXCLUDE_UNRELEASED", False),
        ),
    }


@register_provider_kwargs("audible")
def _audible_kwargs() -> Dict[str, Any]:
    """Provide Audible-specific constructor kwargs."""
    return _effective_audible_kwargs()


@register_provider("audible")
class AudibleProvider(MetadataProvider):
    """Audible metadata provider using the AudiMeta REST API."""

    name = "audible"
    display_name = "Audible"
    requires_auth = False
    supported_sorts = [
        SortOrder.RELEVANCE,
        SortOrder.POPULARITY,
        SortOrder.RATING,
        SortOrder.NEWEST,
        SortOrder.OLDEST,
        SortOrder.SERIES_ORDER,
    ]
    search_fields = [
        TextSearchField(
            key="author",
            label="Author",
            description="Search Audible by author name",
        ),
        TextSearchField(
            key="title",
            label="Title",
            description="Search Audible by title",
        ),
        TextSearchField(
            key="narrator",
            label="Narrator",
            description="Search Audible by narrator",
        ),
        TextSearchField(
            key="publisher",
            label="Publisher",
            description="Search Audible by publisher",
        ),
        TextSearchField(
            key="keywords",
            label="Keywords",
            description="Search Audible by keywords",
        ),
        TextSearchField(
            key="series",
            label="Series",
            description="Browse a series in reading order",
            suggestions_endpoint="/api/metadata/field-options?provider=audible&field=series",
            suggestions_min_query_length=2,
        ),
    ]
    capabilities = [
        MetadataCapability(
            key="view_series",
            field_key="series",
            sort=SortOrder.SERIES_ORDER,
        )
    ]

    def __init__(
        self,
        base_url: Optional[str] = None,
        region: Optional[str] = None,
        timeout: Optional[int] = None,
        user_agent: Optional[str] = None,
        use_upstream_cache: Optional[bool] = None,
        exclude_unreleased: Optional[bool] = None,
    ):
        self.base_url = _normalize_base_url(
            base_url or app_config.get("AUDIBLE_BASE_URL", AUDIBLE_DEFAULT_BASE_URL)
        )
        self.region = _normalize_region(
            region or app_config.get("AUDIBLE_REGION", AUDIBLE_DEFAULT_REGION)
        )
        self.timeout = _coerce_timeout(
            timeout if timeout is not None else app_config.get("AUDIBLE_REQUEST_TIMEOUT", AUDIBLE_DEFAULT_TIMEOUT)
        )
        self.user_agent = _normalize_user_agent(
            user_agent or app_config.get("AUDIBLE_USER_AGENT", AUDIBLE_DEFAULT_USER_AGENT)
        )
        self.use_upstream_cache = coerce_bool(
            use_upstream_cache,
            app_config.get("AUDIBLE_USE_UPSTREAM_CACHE", True),
        )
        self.exclude_unreleased = coerce_bool(
            exclude_unreleased,
            app_config.get("AUDIBLE_EXCLUDE_UNRELEASED", False),
        )
        self.session = requests.Session()

    def is_available(self) -> bool:
        """Audible is available when the configured Audimeta base URL is present."""
        return bool(self.base_url)

    def search(self, options: MetadataSearchOptions) -> List[BookMetadata]:
        """Search Audible and return books only."""
        return self.search_paginated(options).books

    def search_paginated(self, options: MetadataSearchOptions) -> SearchResult:
        """Search Audible with pagination metadata."""
        if not self.is_available():
            return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            books = [result] if result else []
            return SearchResult(
                books=books,
                page=1,
                total_found=len(books),
                has_more=False,
            )

        series_query = str(options.fields.get("series") or "").strip()
        if series_query:
            return self._search_series_paginated(series_query, options)

        search_cache_key = self._build_search_cache_key(options)
        return self._search_paginated_cached(search_cache_key, options)

    @cacheable(
        ttl_key="METADATA_CACHE_SEARCH_TTL",
        ttl_default=300,
        key_prefix="audible:search",
    )
    def _search_paginated_cached(
        self,
        search_cache_key: str,
        options: MetadataSearchOptions,
    ) -> SearchResult:
        params = self._build_search_params(options)
        if not params:
            return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

        requested_limit = min(max(options.limit, 1), AUDIBLE_MAX_LIMIT)
        params["limit"] = requested_limit
        params["page"] = max(0, options.page - 1)

        response = self._make_request("/search", params=params, include_region=True)
        items = _coerce_list_payload(response)
        raw_has_more = len(items) >= requested_limit

        if self.exclude_unreleased:
            items = [item for item in items if not _is_future_release(item.get("releaseDate"))]

        books = [
            book
            for item in items
            if (book := self._parse_book(item)) is not None
        ]

        return SearchResult(
            books=books,
            page=options.page,
            total_found=0,
            has_more=raw_has_more,
        )

    def get_book(self, book_id: str) -> Optional[BookMetadata]:
        """Get Audible book details by ASIN."""
        cache_key = self._build_book_cache_key(book_id)
        return self._get_book_cached(cache_key, book_id)

    @cacheable(
        ttl_key="METADATA_CACHE_BOOK_TTL",
        ttl_default=600,
        key_prefix="audible:book",
    )
    def _get_book_cached(self, book_cache_key: str, book_id: str) -> Optional[BookMetadata]:
        response = self._make_request(f"/book/{book_id}", params={}, include_region=True)
        items = _coerce_list_payload(response)
        if not items:
            return None
        return self._parse_book(items[0])

    def search_by_isbn(self, isbn: str) -> Optional[BookMetadata]:
        """Search Audible by ISBN using AudiMeta's database endpoint."""
        clean_isbn = _clean_isbn(isbn)
        if not clean_isbn:
            return None

        cache_key = self._build_isbn_cache_key(clean_isbn)
        return self._search_by_isbn_cached(cache_key, clean_isbn)

    @cacheable(
        ttl_key="METADATA_CACHE_BOOK_TTL",
        ttl_default=600,
        key_prefix="audible:isbn",
    )
    def _search_by_isbn_cached(self, isbn_cache_key: str, clean_isbn: str) -> Optional[BookMetadata]:
        response = self._make_request(
            "/db/book",
            params={"isbn": clean_isbn, "limit": 1, "page": 1},
            include_region=True,
        )
        items = _coerce_list_payload(response)
        for item in items:
            if _clean_isbn(item.get("isbn")) == clean_isbn:
                return self._parse_book(item)

        fallback = self._make_request(
            "/search",
            params={"query": clean_isbn, "limit": 5, "page": 0},
            include_region=True,
        )
        for item in _coerce_list_payload(fallback):
            if _clean_isbn(item.get("isbn")) == clean_isbn:
                return self._parse_book(item)
        return None

    def get_search_field_options(
        self,
        field_key: str,
        query: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Return dynamic field options for series suggestions."""
        if field_key != "series":
            return []

        normalized_query = str(query or "").strip()
        if len(normalized_query) < 2:
            return []

        cache_key = f"{self.base_url}:{normalized_query.casefold()}"
        return self._get_series_options_cached(cache_key, normalized_query)

    @cacheable(ttl=120, key_prefix="audible:series:options")
    def _get_series_options_cached(
        self,
        series_cache_key: str,
        query: str,
    ) -> List[Dict[str, str]]:
        response = self._make_request(
            "/series",
            params={"name": query},
            include_region=False,
        )
        options: List[Dict[str, str]] = []
        seen_values: set[str] = set()

        for item in _coerce_list_payload(response):
            series_asin = str(item.get("asin") or "").strip()
            series_name = str(item.get("name") or "").strip()
            if not series_asin or not series_name or series_asin in seen_values:
                continue

            seen_values.add(series_asin)
            option: Dict[str, str] = {
                "value": f"id:{series_asin}",
                "label": series_name,
            }
            region = str(item.get("region") or "").strip()
            if region:
                option["description"] = region.upper()
            options.append(option)
            if len(options) >= AUDIBLE_SERIES_SUGGESTION_LIMIT:
                break

        return options

    def _search_series_paginated(
        self,
        series_query: str,
        options: MetadataSearchOptions,
    ) -> SearchResult:
        resolved_series = self._resolve_series(series_query)
        if not resolved_series:
            return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

        series_asin = str(resolved_series.get("asin") or "").strip()
        series_name = str(resolved_series.get("name") or "").strip()
        books = self._fetch_series_books(series_asin, preferred_series_asin=series_asin)

        start = max(0, (options.page - 1) * options.limit)
        end = start + options.limit
        page_books = books[start:end]
        return SearchResult(
            books=page_books,
            page=options.page,
            total_found=len(books),
            has_more=end < len(books),
            source_title=series_name or None,
        )

    def _resolve_series(self, series_query: str) -> Optional[Dict[str, Any]]:
        normalized_query = str(series_query).strip()
        if not normalized_query:
            return None

        normalized_lower = normalized_query.lower()
        if normalized_lower.startswith("asin:") or normalized_lower.startswith("id:"):
            series_asin = normalized_query.split(":", 1)[1].strip()
            if not series_asin:
                return None
            return {"asin": series_asin, "name": series_asin}

        response = self._make_request(
            "/series",
            params={"name": normalized_query},
            include_region=False,
        )
        candidates = _coerce_list_payload(response)
        if not candidates:
            return None

        exact_match = next(
            (
                candidate
                for candidate in candidates
                if str(candidate.get("name") or "").strip().casefold() == normalized_query.casefold()
            ),
            None,
        )
        return exact_match or candidates[0]

    def _fetch_series_books(
        self,
        series_asin: str,
        *,
        preferred_series_asin: Optional[str] = None,
    ) -> List[BookMetadata]:
        cache_key = (
            f"{self.base_url}:{self.region}:{self.use_upstream_cache}:"
            f"{self.exclude_unreleased}:{series_asin}"
        )
        return self._fetch_series_books_cached(cache_key, series_asin, preferred_series_asin or "")

    @cacheable(
        ttl_key="METADATA_CACHE_SEARCH_TTL",
        ttl_default=300,
        key_prefix="audible:series:books",
    )
    def _fetch_series_books_cached(
        self,
        series_cache_key: str,
        series_asin: str,
        preferred_series_asin: str,
    ) -> List[BookMetadata]:
        response = self._make_request(
            f"/series/books/{series_asin}",
            params={},
            include_region=True,
        )
        items = _coerce_list_payload(response)
        if self.exclude_unreleased:
            items = [item for item in items if not _is_future_release(item.get("releaseDate"))]

        books = [
            book
            for item in items
            if (book := self._parse_book(item, preferred_series_asin=preferred_series_asin or None)) is not None
        ]
        books.sort(
            key=lambda book: (
                book.series_position is None,
                book.series_position if book.series_position is not None else float("inf"),
                book.title.casefold(),
            )
        )
        return books

    def _make_request(
        self,
        endpoint: str,
        *,
        params: Dict[str, Any],
        include_region: bool,
    ) -> Any:
        url = f"{self.base_url}{endpoint}"
        request_params = dict(params)
        request_params["cache"] = "true" if self.use_upstream_cache else "false"
        if include_region:
            request_params["region"] = self.region

        try:
            from shelfmark.download.network import get_ssl_verify

            response = self.session.get(
                url,
                params=request_params,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                verify=get_ssl_verify(url),
            )
            response.raise_for_status()
            return response.json()
        except requests.Timeout:
            logger.warning("Audible request timed out for %s", endpoint)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            logger.warning("Audible HTTP error for %s: %s", endpoint, status_code)
        except Exception as exc:
            logger.error("Audible request failed for %s: %s", endpoint, exc)
        return None

    def _build_search_params(self, options: MetadataSearchOptions) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        search_type = options.search_type
        query = options.query.strip()
        fields = options.fields

        author_value = str(fields.get("author") or "").strip()
        title_value = str(fields.get("title") or "").strip()
        narrator_value = str(fields.get("narrator") or "").strip()
        publisher_value = str(fields.get("publisher") or "").strip()
        keywords_value = str(fields.get("keywords") or "").strip()

        if author_value:
            params["author"] = author_value
        elif search_type == SearchType.AUTHOR and query:
            params["author"] = query

        if title_value:
            params["title"] = title_value
        elif search_type == SearchType.TITLE and query:
            params["title"] = query

        if narrator_value:
            params["narrator"] = narrator_value

        if publisher_value:
            params["publisher"] = publisher_value

        if keywords_value:
            params["keywords"] = keywords_value

        if not params and query:
            params["keywords"] = query
        elif query and search_type == SearchType.GENERAL:
            params["keywords"] = query

        sort = _AUDIBLE_SORT_MAPPING.get(options.sort)
        if options.sort == SortOrder.SERIES_ORDER:
            logger.debug("Audible series_order requested without series field; falling back to relevance")
        elif sort:
            params["products_sort_by"] = sort

        return params

    def _build_search_cache_key(self, options: MetadataSearchOptions) -> str:
        fields_key = ":".join(
            f"{key}={value}"
            for key, value in sorted(options.fields.items())
            if value not in ("", None)
        )
        return (
            f"{self.base_url}:{self.region}:{self.timeout}:{self.use_upstream_cache}:"
            f"{self.exclude_unreleased}:{options.query}:{options.search_type.value}:"
            f"{options.sort.value}:{options.limit}:{options.page}:{fields_key}"
        )

    def _build_book_cache_key(self, book_id: str) -> str:
        return f"{self.base_url}:{self.region}:{self.use_upstream_cache}:{book_id}"

    def _build_isbn_cache_key(self, isbn: str) -> str:
        return f"{self.base_url}:{self.region}:{self.use_upstream_cache}:{isbn}"

    def _parse_book(
        self,
        item: Dict[str, Any],
        *,
        preferred_series_asin: Optional[str] = None,
    ) -> Optional[BookMetadata]:
        asin = str(item.get("asin") or "").strip()
        title = str(item.get("title") or "").strip()
        if not asin or not title:
            return None

        authors = _dedupe_texts(
            [
                str(author.get("name") or "")
                for author in item.get("authors", [])
                if isinstance(author, dict)
            ]
        )
        narrators = _dedupe_texts(
            [
                str(narrator.get("name") or "")
                for narrator in item.get("narrators", [])
                if isinstance(narrator, dict)
            ]
        )
        genres = _dedupe_texts(
            [
                str(genre.get("name") or "")
                for genre in item.get("genres", [])
                if isinstance(genre, dict)
            ]
        )[:5]

        selected_series = self._select_series_entry(
            item.get("series", []),
            preferred_series_asin=preferred_series_asin,
        )
        series_asin = str(selected_series.get("asin") or "").strip() if selected_series else None
        series_name = str(selected_series.get("name") or "").strip() if selected_series else None
        series_position = _parse_series_position(
            selected_series.get("position") if selected_series else None
        )

        raw_isbn = _clean_isbn(item.get("isbn"))
        isbn_10 = raw_isbn if len(raw_isbn) == 10 else None
        isbn_13 = raw_isbn if len(raw_isbn) == 13 else None

        display_fields: List[DisplayField] = []
        rating = item.get("rating")
        try:
            rating_value = float(rating)
        except (TypeError, ValueError):
            rating_value = 0.0
        if rating_value > 0:
            display_fields.append(
                DisplayField(label="Rating", value=f"{rating_value:.1f}", icon="star")
            )

        runtime_value = _format_runtime(item.get("lengthMinutes"))
        if runtime_value:
            display_fields.append(DisplayField(label="Length", value=runtime_value, icon="book"))

        narrator_value = _format_narrator_value(narrators)
        if narrator_value:
            label = "Narrator" if len(narrators) == 1 else "Narrators"
            display_fields.append(DisplayField(label=label, value=narrator_value, icon="users"))

        book_format = str(item.get("bookFormat") or "").strip()
        if book_format:
            display_fields.append(
                DisplayField(label="Format", value=book_format.replace("_", " ").title(), icon="editions")
            )

        return BookMetadata(
            provider=self.name,
            provider_id=asin,
            title=title,
            provider_display_name=self.display_name,
            authors=authors,
            isbn_10=isbn_10,
            isbn_13=isbn_13,
            cover_url=str(item.get("imageUrl") or "").strip() or None,
            description=_sanitize_description(item.get("description"))
            or _sanitize_description(item.get("summary")),
            publisher=str(item.get("publisher") or "").strip() or None,
            publish_year=_extract_publish_year(item.get("releaseDate")),
            language=str(item.get("language") or "").strip() or None,
            genres=genres,
            source_url=str(item.get("link") or "").strip() or None,
            subtitle=str(item.get("subtitle") or "").strip() or None,
            search_author=authors[0] if authors else None,
            series_id=series_asin or None,
            series_name=series_name or None,
            series_position=series_position,
            display_fields=display_fields,
        )

    def _select_series_entry(
        self,
        entries: Any,
        *,
        preferred_series_asin: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(entries, list):
            return None

        normalized_preferred = str(preferred_series_asin or "").strip()
        if normalized_preferred:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("asin") or "").strip() == normalized_preferred:
                    return entry

        for entry in entries:
            if isinstance(entry, dict):
                return entry
        return None

def _test_audible_connection(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test connectivity to the configured AudiMeta instance."""
    provider = AudibleProvider(**_effective_audible_kwargs(current_values))
    if not provider.is_available():
        return {"success": False, "message": "Audible base URL is required"}

    result = provider._make_request(
        "/search",
        params={"query": "harry potter", "limit": 1, "page": 0},
        include_region=True,
    )
    payload = _coerce_list_payload(result)
    if payload:
        return {"success": True, "message": "Successfully connected to AudiMeta"}
    if result is not None:
        return {"success": True, "message": "AudiMeta is reachable but returned no results"}
    return {"success": False, "message": "Unable to reach AudiMeta with the current settings"}


@register_settings("audible", "Audible", icon="book", order=54, group="metadata_providers")
def audible_settings():
    """Audible metadata provider settings."""
    return [
        HeadingField(
            key="audible_heading",
            title="Audible",
            description=(
                "Search Audible audiobook metadata via the AudiMeta API. "
                "Supports region-aware search, series browsing, and provider-specific fields."
            ),
            link_url="https://audimeta.de/api-docs",
            link_text="AudiMeta API Docs",
        ),
        CheckboxField(
            key="AUDIBLE_ENABLED",
            label="Enable Audible",
            description="Enable Audible as a metadata provider for book and audiobook searches",
            default=False,
        ),
        TextField(
            key="AUDIBLE_BASE_URL",
            label="AudiMeta Base URL",
            description=(
                "Audimeta instance URL. Defaults to the public instance. "
                "You can also point this at https://beta.audimeta.de or another compatible host."
            ),
            default=AUDIBLE_DEFAULT_BASE_URL,
            placeholder=AUDIBLE_DEFAULT_BASE_URL,
            requires_restart=False,
        ),
        TextField(
            key="AUDIBLE_USER_AGENT",
            label="User-Agent",
            description=(
                "User-Agent sent to AudiMeta. The public API rejects generic clients, "
                "so keep this as a meaningful identifier if you override it."
            ),
            default=AUDIBLE_DEFAULT_USER_AGENT,
            placeholder=AUDIBLE_DEFAULT_USER_AGENT,
            requires_restart=False,
        ),
        SelectField(
            key="AUDIBLE_REGION",
            label="Default Region",
            description="Audible storefront region to use for searches and book lookups.",
            options=_AUDIBLE_REGION_OPTIONS,
            default=AUDIBLE_DEFAULT_REGION,
        ),
        NumberField(
            key="AUDIBLE_REQUEST_TIMEOUT",
            label="Request Timeout (seconds)",
            description="Timeout for outgoing AudiMeta API requests.",
            default=AUDIBLE_DEFAULT_TIMEOUT,
            min_value=1,
            max_value=60,
            step=1,
        ),
        CheckboxField(
            key="AUDIBLE_USE_UPSTREAM_CACHE",
            label="Use AudiMeta Cache",
            description="Allow AudiMeta to serve cached upstream results when available.",
            default=True,
        ),
        SelectField(
            key="AUDIBLE_DEFAULT_SORT",
            label="Default Sort Order",
            description="Default sort order for Audible search results.",
            options=_AUDIBLE_SORT_OPTIONS,
            default="relevance",
        ),
        CheckboxField(
            key="AUDIBLE_EXCLUDE_UNRELEASED",
            label="Exclude Unreleased Titles",
            description=(
                "Filter out titles with a release date in the future. "
                "This is applied after search results are fetched and may reduce the number of items shown on a page."
            ),
            default=False,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description="Verify the configured AudiMeta instance is reachable",
            style="primary",
            callback=_test_audible_connection,
        ),
    ]
