"""Hardcover.app metadata provider. Requires API key."""

import re
import requests
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from shelfmark.core.cache import cache_key, cacheable, get_metadata_cache
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    register_settings,
    CheckboxField,
    PasswordField,
    SelectField,
    ActionButton,
    HeadingField,
)
from shelfmark.core.config import config as app_config
from shelfmark.core.request_helpers import coerce_int
from shelfmark.download.network import get_ssl_verify
from shelfmark.metadata_providers import (
    BookMetadata,
    DisplayField,
    MetadataCapability,
    MetadataProvider,
    MetadataSearchOptions,
    SearchResult,
    SearchType,
    SortOrder,
    register_provider,
    register_provider_kwargs,
    DynamicSelectSearchField,
    TextSearchField,
)

logger = setup_logger(__name__)

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"
HARDCOVER_PAGE_SIZE = 25  # Hardcover API returns max 25 results per page
HARDCOVER_LIST_URL_PATTERN = re.compile(
    r"^/(?:@([\w.-]+)/)?lists?/([\w-]+)/?$",
    re.IGNORECASE,
)

LIST_LOOKUP_QUERY = """
query LookupListsBySlug($slug: String!) {
    lists(where: {slug: {_eq: $slug}}, limit: 20) {
        id
        slug
        user {
            username
        }
    }
}
"""

LIST_BOOKS_BY_ID_QUERY = """
query GetListBooksById($id: Int!, $limit: Int!, $offset: Int!) {
    lists(where: {id: {_eq: $id}}, limit: 1) {
        slug
        user {
            username
        }
        books_count
        list_books(order_by: {position: asc}, limit: $limit, offset: $offset) {
            book {
                id
                title
                subtitle
                slug
                release_date
                headline
                description
                pages
                rating
                ratings_count
                users_count
                cached_image
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                featured_book_series {
                    position
                    series {
                        id
                        name
                        primary_books_count
                    }
                }
            }
        }
    }
}
"""

USER_LISTS_QUERY = """
query GetUserLists {
    me {
        id
        username
        want_to_read_books: user_books_aggregate(where: {status_id: {_eq: 1}}) {
            aggregate {
                count(columns: [book_id], distinct: true)
            }
        }
        lists(order_by: {name: asc}) {
            id
            name
            slug
            books_count
        }
        followed_lists(order_by: {created_at: desc}) {
            list {
                id
                name
                slug
                books_count
                user {
                    username
                }
            }
        }
    }
}
"""

USER_BOOKS_BY_STATUS_QUERY = """
query GetCurrentUserBooksByStatus($statusId: Int!, $limit: Int!, $offset: Int!) {
    me {
        status_books: user_books(
            where: {status_id: {_eq: $statusId}}
            distinct_on: [book_id]
            order_by: [{book_id: asc}, {created_at: desc}]
            limit: $limit
            offset: $offset
        ) {
            book {
                id
                title
                subtitle
                slug
                release_date
                headline
                description
                pages
                rating
                ratings_count
                users_count
                cached_image
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                featured_book_series {
                    position
                    series {
                        id
                        name
                        primary_books_count
                    }
                }
            }
        }
        status_books_aggregate: user_books_aggregate(where: {status_id: {_eq: $statusId}}) {
            aggregate {
                count(columns: [book_id], distinct: true)
            }
        }
    }
}
"""

BOOK_TARGET_MEMBERSHIP_QUERY = """
query GetBookTargetMembership($bookId: Int!) {
    me {
        user_books(where: {book_id: {_eq: $bookId}}, limit: 1, order_by: [{created_at: desc}]) {
            id
            status_id
        }
        lists {
            id
            list_books(where: {book_id: {_eq: $bookId}}, limit: 1) {
                id
            }
        }
    }
}
"""

BOOK_TARGET_MEMBERSHIP_BATCH_QUERY = """
query GetBookTargetMembershipBatch($bookIds: [Int!]!) {
    me {
        user_books(where: {book_id: {_in: $bookIds}}, order_by: [{created_at: desc}]) {
            id
            book_id
            status_id
        }
        lists {
            id
            list_books(where: {book_id: {_in: $bookIds}}) {
                id
                book_id
            }
        }
    }
}
"""

INSERT_USER_BOOK_MUTATION = """
mutation AddBookToStatus($bookId: Int!, $statusId: Int!) {
    insert_user_book(object: {book_id: $bookId, status_id: $statusId}) {
        id
        error
        user_book {
            id
            book_id
            status_id
        }
    }
}
"""

UPDATE_USER_BOOK_MUTATION = """
mutation UpdateBookStatus($userBookId: Int!, $statusId: Int!) {
    update_user_book(id: $userBookId, object: {status_id: $statusId}) {
        id
        error
        user_book {
            id
            book_id
            status_id
        }
    }
}
"""

DELETE_USER_BOOK_MUTATION = """
mutation RemoveBookStatus($userBookId: Int!) {
    delete_user_book(id: $userBookId) {
        id
        book_id
        user_id
    }
}
"""

INSERT_LIST_BOOK_MUTATION = """
mutation AddBookToList($bookId: Int!, $listId: Int!) {
    insert_list_book(object: {book_id: $bookId, list_id: $listId}) {
        id
        list_book {
            id
            book_id
            list_id
        }
    }
}
"""

DELETE_LIST_BOOK_MUTATION = """
mutation RemoveBookFromList($listBookId: Int!) {
    delete_list_book(id: $listBookId) {
        id
        list_id
    }
}
"""

SEARCH_FIELD_OPTIONS_QUERY = """
query SearchFieldOptions(
    $query: String!,
    $queryType: String!,
    $limit: Int!,
    $page: Int!,
    $sort: String,
    $fields: String,
    $weights: String
) {
    search(
        query: $query,
        query_type: $queryType,
        per_page: $limit,
        page: $page,
        sort: $sort,
        fields: $fields,
        weights: $weights
    ) {
        results
    }
}
"""

SERIES_BY_AUTHOR_IDS_QUERY = """
query SeriesByAuthorIds($authorIds: [Int!], $limit: Int!) {
    series(
        where: {
            author_id: {_in: $authorIds},
            canonical_id: {_is_null: true},
            state: {_eq: "active"}
        },
        limit: $limit,
        order_by: [{primary_books_count: desc_nulls_last}, {books_count: desc}, {name: asc}]
    ) {
        id
        name
        primary_books_count
        books_count
        author {
            name
        }
    }
}
"""

SERIES_BOOKS_BY_ID_QUERY = """
query GetSeriesBooks($seriesId: Int!) {
    series(where: {id: {_eq: $seriesId}}, limit: 1) {
        id
        name
        primary_books_count
        book_series(
            where: {
                book: {
                    canonical_id: {_is_null: true},
                    state: {_in: ["normalized", "normalizing"]}
                }
            }
            order_by: [{position: asc_nulls_last}, {book_id: asc}]
        ) {
            position
            book {
                id
                title
                subtitle
                slug
                release_date
                headline
                description
                pages
                rating
                ratings_count
                users_count
                compilation
                editions_count
                cached_image
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                featured_book_series {
                    position
                    series {
                        id
                        name
                        primary_books_count
                    }
                }
            }
        }
    }
}
"""

HARDCOVER_WANT_TO_READ_STATUS_ID = 1
HARDCOVER_STATUS_PREFIX = "status:"
HARDCOVER_STATUS_URL_SLUGS: dict[int, str] = {
    1: "want-to-read",
    2: "currently-reading",
    3: "read",
    5: "did-not-finish",
}
HARDCOVER_LIST_ID_PREFIX = "id:"
HARDCOVER_WRITABLE_TARGET_GROUPS = {"My Books", "My Lists"}


@dataclass(frozen=True)
class HardcoverBookTargetState:
    """Current Hardcover target state for a specific book."""
    user_book_id: Optional[int]
    status_id: Optional[int]
    list_book_ids: Dict[int, int]


class HardcoverGraphQLError(ValueError):
    """GraphQL request was rejected by Hardcover."""


def _extract_graphql_error_message(payload: Any) -> str:
    """Extract a readable message from a GraphQL error payload."""
    if not isinstance(payload, dict):
        return ""

    errors = payload.get("errors", [])
    if not isinstance(errors, list):
        return ""

    messages: List[str] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        message = str(error.get("message") or "").strip()
        if message:
            messages.append(message)

    return "; ".join(messages)


# Mapping from abstract sort order to Hardcover sort parameter
# Note: release_year is more consistently populated than release_date_i
SORT_MAPPING: Dict[SortOrder, str] = {
    SortOrder.RELEVANCE: "_text_match:desc,users_count:desc",
    SortOrder.POPULARITY: "users_count:desc",
    SortOrder.RATING: "rating:desc",
    SortOrder.NEWEST: "release_year:desc",
    SortOrder.OLDEST: "release_year:asc",
}

# Mapping from abstract search type to Hardcover fields parameter
SEARCH_TYPE_FIELDS: Dict[SearchType, str] = {
    SearchType.GENERAL: "title,isbns,series_names,author_names,alternative_titles",
    SearchType.TITLE: "title,alternative_titles",
    SearchType.AUTHOR: "author_names",
    # ISBN is handled separately via search_by_isbn()
}

SERIES_SEARCH_FIELDS = "name,books,author_name"
SERIES_SEARCH_WEIGHTS = "2,1,1"
SERIES_SEARCH_SORT = "_text_match:desc,readers_count:desc"
AUTHOR_SUGGESTION_FIELDS = "name,name_personal,alternate_names"
AUTHOR_SUGGESTION_WEIGHTS = "4,3,2"
AUTHOR_SUGGESTION_SORT = "_text_match:desc,books_count:desc"
TITLE_SUGGESTION_FIELDS = "title,alternative_titles"
TITLE_SUGGESTION_WEIGHTS = "5,2"
TITLE_SUGGESTION_SORT = "_text_match:desc,users_count:desc"


def _combine_headline_description(headline: Optional[str], description: Optional[str]) -> Optional[str]:
    """Combine headline (tagline) and description into a single description."""
    if headline and description:
        return f"{headline}\n\n{description}"
    return headline or description


def _extract_cover_url(data: Dict, *keys: str) -> Optional[str]:
    """Extract cover URL from data dict, trying multiple keys.

    Handles both string URLs and dict with 'url' key.
    """
    for key in keys:
        value = data.get(key)
        if value:
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                return value.get("url")
    return None


def _extract_publish_year(data: Dict) -> Optional[int]:
    """Extract publish year from release_year or release_date fields."""
    if data.get("release_year"):
        try:
            return int(data["release_year"])
        except (ValueError, TypeError):
            pass
    if data.get("release_date"):
        try:
            return int(str(data["release_date"])[:4])
        except (ValueError, TypeError):
            pass
    return None


def _parse_release_date(value: Any) -> Optional[datetime]:
    """Parse Hardcover release dates stored as YYYY-MM-DD strings."""
    if not value:
        return None

    normalized_value = str(value).strip()
    if not normalized_value:
        return None

    try:
        return datetime.fromisoformat(normalized_value[:10])
    except ValueError:
        return None


def _normalize_series_position(value: Any) -> Optional[float]:
    """Normalize a series position to a float for sorting and grouping."""
    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_search_text(value: str) -> str:
    """Normalize free-text search input for matching and caching."""
    return " ".join(value.split()).strip()


def _unwrap_hit_document(hit: Any) -> Optional[Dict[str, Any]]:
    """Extract the document dict from a Typesense hit, or return None."""
    if not isinstance(hit, dict):
        return None
    item = hit.get("document", hit)
    return item if isinstance(item, dict) else None


def _search_tokens(value: str) -> List[str]:
    """Tokenize search text for lightweight prefix matching."""
    return re.findall(r"[a-z0-9']+", value.casefold())


def _query_matches_author_name(query: str, author_name: str) -> bool:
    """Return True when the query looks like an author-name search."""
    normalized_query = _normalize_search_text(query)
    normalized_author_name = _normalize_search_text(author_name)
    if not normalized_query or not normalized_author_name:
        return False

    query_folded = normalized_query.casefold()
    author_folded = normalized_author_name.casefold()
    if query_folded in author_folded:
        return True

    query_tokens = _search_tokens(normalized_query)
    author_tokens = _search_tokens(normalized_author_name)
    if not query_tokens or not author_tokens:
        return False

    return all(
        any(author_token.startswith(query_token) for author_token in author_tokens)
        for query_token in query_tokens
    )


def _split_part_base_title(title: str) -> Optional[str]:
    """Extract the base title from segmented part releases like ', Part 2'."""
    normalized_title = _normalize_search_text(title)
    if not normalized_title:
        return None

    match = re.match(r"^(?P<base>.+?),\s*Part\s+\d+$", normalized_title, re.IGNORECASE)
    if not match:
        return None

    base_title = str(match.group("base") or "").strip()
    return base_title or None


def _series_allows_split_parts(series_name: str) -> bool:
    """Return True for series that intentionally organize split-part releases."""
    normalized_name = _normalize_search_text(series_name).casefold()
    if not normalized_name:
        return False

    markers = (
        "dramatized adaptation",
        "graphicaudio",
        "graphic audio",
        "(3 parts)",
        "(2 parts)",
        "(4 parts)",
    )
    return any(marker in normalized_name for marker in markers)


def _extract_typesense_hits(result: Dict[str, Any]) -> tuple[List[Dict[str, Any]], int]:
    """Extract hit documents + total count from Hardcover search output."""
    root = result.get("search", result) if isinstance(result, dict) else {}
    results_obj = root.get("results", {}) if isinstance(root, dict) else {}
    if isinstance(results_obj, dict):
        hits = results_obj.get("hits", [])
        found_count = results_obj.get("found", 0)
    else:
        hits = results_obj if isinstance(results_obj, list) else []
        found_count = 0
    return hits, found_count


def _build_source_url(slug: str) -> Optional[str]:
    """Build Hardcover source URL from book slug."""
    return f"https://hardcover.app/books/{slug}" if slug else None


def _is_probably_series_position(subtitle: str) -> bool:
    normalized = subtitle.strip().lower()

    # Common patterns: "Book One", "Book 1", "Part 2", "Volume III", etc.
    if re.match(r"^(book|part|volume|vol\.?|episode)\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b", normalized):
        return True

    # e.g. "A Novel", "An Epic Fantasy", etc. These add noise to indexer queries.
    if normalized in {"a novel", "a novella", "a story", "a memoir"}:
        return True

    # Descriptive subtitles like "A [Name] Novel", "An [Name] Mystery", etc.
    genre_words = (
        "novel", "novella", "story", "memoir", "tale", "thriller", "mystery",
        "romance", "adventure", "epic", "saga", "chronicle", "fantasy",
        "novel-in-stories",
    )
    genre_pattern = "|".join(re.escape(w) for w in genre_words)
    if re.match(rf"^an?\s+.+\s+({genre_pattern})$", normalized):
        return True

    return False


def _strip_parenthetical_suffix(title: str) -> str:
    # Drop trailing qualifiers like "(Unabridged)", "(Illustrated Edition)", etc.
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()


def _simplify_author_for_search(author: str) -> Optional[str]:
    """Return a looser author string for indexer searches.

    Primary goal: reduce mismatch between metadata providers and indexers.
    Indexers store author names inconsistently ("R.A.", "R. A.", "Salvatore, R.A.")
    so initials add noise and hurt recall.

    Heuristics:
    - Strip all initials (single or compound), keeping only full names
      e.g. "R. A. Salvatore" -> "Salvatore", "George R.R. Martin" -> "George Martin"
    - Preserve suffixes like "Jr."/"Sr."/"III" as they sometimes matter
    """
    if not author:
        return None

    normalized = " ".join(author.split()).strip()
    if not normalized:
        return None

    # Handle "Last, First ..." -> "First ... Last"
    if "," in normalized:
        parts = [p.strip() for p in normalized.split(",") if p.strip()]
        if len(parts) >= 2:
            normalized = " ".join(parts[1:] + [parts[0]]).strip()

    tokens = normalized.split(" ")
    if len(tokens) < 2:
        return None

    keep_suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

    simplified: list[str] = []
    for idx, token in enumerate(tokens):
        t = token.strip()
        if not t:
            continue

        t_lower = t.lower()
        is_suffix = (idx == len(tokens) - 1) and (t_lower in keep_suffixes)
        if is_suffix:
            simplified.append(t)
            continue

        # Drop all initials: "R.", "R", "R.R.", "J.K.", etc.
        if re.match(r"^[A-Za-z]$|^([A-Za-z]\.)+[A-Za-z]?$", t):
            continue

        simplified.append(t)

    if not simplified:
        return None

    candidate = " ".join(simplified).strip()
    if candidate.lower() == normalized.lower():
        return None

    return candidate


def _compute_search_title(
    title: str,
    subtitle: Optional[str],
    *,
    series_name: Optional[str] = None,
) -> Optional[str]:
    """Compute a provider-specific, *looser* title for indexer searching.

    Goal: produce a string that maximizes recall in downstream sources (Prowlarr,
    IRC bots, etc.). Being too detailed is counterproductive.

    Hardcover often stores titles in a "Series: Book Title" format and places the
    standalone book title in `subtitle`. When this appears to be the case, prefer
    the subtitle (unless it looks like a series position or other noise).

    Additional heuristics:
    - If Hardcover prefixes the series in the title, remove it.
    - Drop trailing parenthetical qualifiers.
    """
    if not title:
        return None

    original_title = " ".join(title.split()).strip()

    normalized_title = _strip_parenthetical_suffix(original_title)

    normalized_subtitle = " ".join(subtitle.split()).strip() if subtitle else ""
    normalized_subtitle = _strip_parenthetical_suffix(normalized_subtitle) if normalized_subtitle else ""

    if normalized_subtitle and normalized_subtitle.lower() == normalized_title.lower():
        normalized_subtitle = ""

    # If subtitle is noise, strip it from the title and use just the prefix.
    if normalized_subtitle and _is_probably_series_position(normalized_subtitle):
        match = re.match(r"^(.+?)\s*:\s*(.+)$", normalized_title)
        if match:
            suffix = _strip_parenthetical_suffix(match.group(2).strip())
            if normalized_subtitle.lower() == suffix.lower() or normalized_subtitle.lower() in suffix.lower():
                return None

    # Prefer subtitle when it looks like the real title.
    if normalized_subtitle and not _is_probably_series_position(normalized_subtitle):
        match = re.match(r"^(.+?)\s*:\s*(.+)$", normalized_title)
        if match:
            prefix = match.group(1).strip()
            suffix = _strip_parenthetical_suffix(match.group(2).strip())

            prefix_words = len(prefix.split()) if prefix else 0
            subtitle_words = len(normalized_subtitle.split())

            series_normalized = " ".join(series_name.split()).strip() if series_name else ""
            if series_normalized and prefix.lower() == series_normalized.lower():
                return normalized_subtitle

            # If the subtitle is much longer than the prefix, treat it as a descriptive subtitle.
            if prefix and subtitle_words >= (prefix_words + 4):
                return prefix

            # Otherwise assume "Series: Book Title" and prefer the subtitle.
            if normalized_subtitle.lower() == suffix.lower() or normalized_subtitle.lower() in suffix.lower():
                return normalized_subtitle

        # Fallback: if title contains the subtitle, this is likely "Series: Subtitle".
        if normalized_subtitle.lower() in normalized_title.lower():
            return normalized_subtitle

    # If we know the series name (from full book fetch), strip it.
    if series_name:
        series_normalized = " ".join(series_name.split()).strip()
        if series_normalized:
            # Common Hardcover format: "Series: Book Title".
            prefix = f"{series_normalized}:"
            if normalized_title.lower().startswith(prefix.lower()):
                candidate = normalized_title[len(prefix):].strip()
                candidate = _strip_parenthetical_suffix(candidate)
                if candidate and candidate.lower() != normalized_title.lower():
                    return candidate

    # Last resort: return a cleaned version of the title if we removed noise.
    if normalized_title and normalized_title.lower() != original_title.lower():
        return normalized_title

    return None


@register_provider_kwargs("hardcover")
def _hardcover_kwargs() -> Dict[str, Any]:
    """Provide Hardcover-specific constructor kwargs."""
    return {"api_key": app_config.get("HARDCOVER_API_KEY", "")}


@register_provider("hardcover")
class HardcoverProvider(MetadataProvider):
    """Hardcover.app metadata provider using GraphQL API."""

    name = "hardcover"
    display_name = "Hardcover"
    requires_auth = True
    supported_sorts = [
        SortOrder.RELEVANCE,
        SortOrder.POPULARITY,
        SortOrder.RATING,
        SortOrder.NEWEST,
        SortOrder.OLDEST,
        SortOrder.SERIES_ORDER,
    ]
    capabilities = [
        MetadataCapability(
            key="view_series",
            field_key="series",
            sort=SortOrder.SERIES_ORDER,
        ),
    ]
    search_fields = [
        TextSearchField(
            key="author",
            label="Author",
            placeholder="Search author...",
            description="Search by author name",
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
    ]

    def __init__(self, api_key: Optional[str] = None):
        """Initialize provider with optional API key (falls back to config)."""
        raw_key = api_key or app_config.get("HARDCOVER_API_KEY", "")
        # Strip "Bearer " prefix if user pasted the full auth header from Hardcover
        self.api_key = raw_key.removeprefix("Bearer ").strip() if raw_key else ""
        self.session = requests.Session()
        if self.api_key:
            self.session.headers.update({
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            })

    def is_available(self) -> bool:
        """Check if provider is configured with an API key."""
        return bool(self.api_key)

    def _build_search_params(
        self, default_query: str, author: str, title: str, series: str
    ) -> tuple[str, Optional[str], Optional[str]]:
        """Build search query, fields, and weights based on provided values.

        Returns (query, fields, weights) tuple. Fields/weights are None for general search.
        """
        if author and not title and not series:
            return author, "author_names", "1"
        if title and not author and not series:
            return title, "title,alternative_titles", "5,1"
        if author and title and not series:
            return f"{title} {author}", "title,alternative_titles,author_names", "5,1,3"
        return default_query, None, None

    def _detect_list_url(self, query: str) -> Optional[tuple[Optional[str], str]]:
        """Detect and extract optional owner username + list slug from a URL string."""
        candidate = query.strip()
        if not candidate:
            return None

        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"}:
            return None

        hostname = (parsed.hostname or "").lower()
        if hostname not in {"hardcover.app", "www.hardcover.app"}:
            return None

        match = HARDCOVER_LIST_URL_PATTERN.match(parsed.path or "")
        if not match:
            return None

        owner_username = match.group(1).strip() if match.group(1) else None
        slug = match.group(2).strip()
        if not slug:
            return None

        return owner_username, slug

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="hardcover:list:id")
    def _fetch_list_books_by_id(self, list_id: int, page: int, limit: int) -> SearchResult:
        """Fetch list books by unique Hardcover list ID."""
        if not self.api_key:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        offset = (page - 1) * limit

        result = self._execute_query(
            LIST_BOOKS_BY_ID_QUERY,
            {
                "id": list_id,
                "limit": limit,
                "offset": offset,
            },
        )
        if not result:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        lists = result.get("lists", [])
        if not lists:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        list_data = lists[0] if isinstance(lists[0], dict) else {}
        list_books = list_data.get("list_books", []) if isinstance(list_data, dict) else []
        books_count_raw = list_data.get("books_count", 0) if isinstance(list_data, dict) else 0

        # Build source URL from slug and owner username
        source_url = None
        list_slug = str(list_data.get("slug") or "").strip()
        user_data = list_data.get("user", {})
        owner_username = str(user_data.get("username") or "").strip() if isinstance(user_data, dict) else ""
        if list_slug and owner_username:
            source_url = f"https://hardcover.app/@{owner_username}/lists/{list_slug}"

        try:
            books_count = int(books_count_raw)
        except (TypeError, ValueError):
            books_count = 0

        books: List[BookMetadata] = []
        for item in list_books:
            if not isinstance(item, dict):
                continue
            book_data = item.get("book", {})
            if not isinstance(book_data, dict) or not book_data:
                continue
            try:
                parsed_book = self._parse_book(book_data)
                if parsed_book:
                    books.append(parsed_book)
            except Exception as exc:
                logger.debug(f"Failed to parse Hardcover list book for list_id={list_id}: {exc}")

        has_more = offset + len(list_books) < books_count
        return SearchResult(books=books, page=page, total_found=books_count, has_more=has_more, source_url=source_url)

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="hardcover:list:slug")
    def _fetch_list_books(self, slug: str, owner_username: Optional[str], page: int, limit: int) -> SearchResult:
        """Fetch list books by slug, optionally disambiguating by owner username."""
        if not self.api_key:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        lookup = self._execute_query(LIST_LOOKUP_QUERY, {"slug": slug})
        if not lookup:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        lists = lookup.get("lists", [])
        if not isinstance(lists, list) or not lists:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        selected: Optional[Dict[str, Any]] = None
        normalized_owner = owner_username.lower() if owner_username else None
        if normalized_owner:
            for item in lists:
                if not isinstance(item, dict):
                    continue
                owner_data = item.get("user", {})
                if not isinstance(owner_data, dict):
                    continue
                candidate_owner = str(owner_data.get("username") or "").strip().lower()
                if candidate_owner == normalized_owner:
                    selected = item
                    break

        if selected is None:
            first_item = lists[0]
            selected = first_item if isinstance(first_item, dict) else None

        if not selected:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        list_id_raw = selected.get("id")
        try:
            list_id = int(list_id_raw)
        except (TypeError, ValueError):
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        return self._fetch_list_books_by_id(list_id, page, limit)

    def _resolve_current_user_id(self) -> Optional[str]:
        """Resolve current Hardcover user id from saved settings or API me query."""
        connected_user_id = _get_connected_user_id()
        if connected_user_id:
            return connected_user_id

        result = self._execute_query("query { me { id, username } }", {})
        if not result:
            return None

        me_data = result.get("me", {})
        if isinstance(me_data, list) and me_data:
            me_data = me_data[0]
        if not isinstance(me_data, dict):
            return None

        user_id_raw = me_data.get("id")
        if user_id_raw is None:
            return None

        user_id = str(user_id_raw)
        username_raw = me_data.get("username")
        username = str(username_raw).strip() if username_raw else _get_connected_username()
        _save_connected_user(user_id, username)
        return user_id

    def get_user_lists(self) -> List[Dict[str, str]]:
        """Get authenticated user's own and followed Hardcover lists."""
        if not self.api_key:
            return []

        connected_user_id = self._resolve_current_user_id()
        if not connected_user_id:
            return self._fetch_user_lists()

        return self._get_user_lists_cached(connected_user_id)

    def get_search_field_options(
        self,
        field_key: str,
        query: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Provide dynamic options for Hardcover-specific advanced fields."""
        if field_key == "author":
            return self._search_author_options(query or "")
        if field_key == "title":
            return self._search_title_options(query or "")
        if field_key == "series":
            return self._search_series_options(query or "")
        if field_key == "hardcover_list":
            return self.get_user_lists()
        return []

    def _search_field_hits(
        self,
        *,
        query: str,
        query_type: str,
        limit: int,
        sort: Optional[str],
        fields: Optional[str],
        weights: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Run a Hardcover search request for field-level typeahead options."""
        normalized_query = _normalize_search_text(query)
        if not self.api_key or len(normalized_query) < 2:
            return []

        result = self._execute_query(
            SEARCH_FIELD_OPTIONS_QUERY,
            {
                "query": normalized_query,
                "queryType": query_type,
                "limit": limit,
                "page": 1,
                "sort": sort,
                "fields": fields,
                "weights": weights,
            },
        )
        if not result:
            return []

        hits, _found_count = _extract_typesense_hits(result)
        return hits

    def _search_series_by_matching_author(self, query: str) -> List[Dict[str, Any]]:
        """Return direct series rows when the query clearly matches an author."""
        author_hits = self._search_field_hits(
            query=query,
            query_type="Author",
            limit=2,
            sort=AUTHOR_SUGGESTION_SORT,
            fields=AUTHOR_SUGGESTION_FIELDS,
            weights=AUTHOR_SUGGESTION_WEIGHTS,
        )

        author_ids: List[int] = []
        for hit in author_hits:
            item = _unwrap_hit_document(hit)
            if item is None:
                continue

            author_name = str(item.get("name") or "").strip()
            if not _query_matches_author_name(query, author_name):
                continue

            try:
                author_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue

            if author_id not in author_ids:
                author_ids.append(author_id)

        if not author_ids:
            return []

        result = self._execute_query(
            SERIES_BY_AUTHOR_IDS_QUERY,
            {
                "authorIds": author_ids,
                "limit": 7,
            },
        )
        if not result:
            return []

        series_rows = result.get("series", [])
        return [row for row in series_rows if isinstance(row, dict)]

    @cacheable(ttl=120, key_prefix="hardcover:author:options")
    def _search_author_options(self, query: str) -> List[Dict[str, str]]:
        """Return typeahead options for Hardcover author search."""
        hits = self._search_field_hits(
            query=query,
            query_type="Author",
            limit=7,
            sort=AUTHOR_SUGGESTION_SORT,
            fields=AUTHOR_SUGGESTION_FIELDS,
            weights=AUTHOR_SUGGESTION_WEIGHTS,
        )
        options: List[Dict[str, str]] = []
        seen_labels: set[str] = set()

        for hit in hits:
            item = _unwrap_hit_document(hit)
            if item is None:
                continue

            label = str(item.get("name") or "").strip()
            normalized_label = label.casefold()
            if not label or normalized_label in seen_labels:
                continue

            seen_labels.add(normalized_label)
            options.append({"value": label, "label": label})

        return options

    @cacheable(ttl=120, key_prefix="hardcover:title:options")
    def _search_title_options(self, query: str) -> List[Dict[str, str]]:
        """Return typeahead options for Hardcover title search."""
        hits = self._search_field_hits(
            query=query,
            query_type="Book",
            limit=7,
            sort=TITLE_SUGGESTION_SORT,
            fields=TITLE_SUGGESTION_FIELDS,
            weights=TITLE_SUGGESTION_WEIGHTS,
        )

        exclude_compilations = app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False)
        exclude_unreleased = app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False)
        current_year = datetime.now().year

        options: List[Dict[str, str]] = []
        seen_labels: set[str] = set()

        for hit in hits:
            item = _unwrap_hit_document(hit)
            if item is None:
                continue

            if exclude_compilations and item.get("compilation"):
                continue

            if exclude_unreleased:
                release_year = item.get("release_year")
                try:
                    if release_year is not None and int(release_year) > current_year:
                        continue
                except (TypeError, ValueError):
                    pass

            label = str(item.get("title") or "").strip()
            normalized_label = label.casefold()
            if not label or normalized_label in seen_labels:
                continue

            seen_labels.add(normalized_label)
            options.append({"value": label, "label": label})

        return options

    def _format_series_option_description(self, item: Dict[str, Any]) -> Optional[str]:
        """Build a short description for a series suggestion option."""
        author_name = item.get("author_name")
        if not author_name:
            author_data = item.get("author")
            if isinstance(author_data, dict):
                author_name = author_data.get("name")

        parts: List[str] = []
        if author_name:
            parts.append(f"by {author_name}")

        books_count = item.get("primary_books_count")
        if books_count is None:
            books_count = item.get("books_count")

        try:
            if books_count is not None:
                books_count_int = int(books_count)
                parts.append(f"{books_count_int} book{'s' if books_count_int != 1 else ''}")
        except (TypeError, ValueError):
            pass

        return " • ".join(parts) if parts else None

    @cacheable(ttl=120, key_prefix="hardcover:series:options")
    def _search_series_options(self, query: str) -> List[Dict[str, str]]:
        """Return typeahead options for Hardcover series search."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=2) as executor:
            author_future = executor.submit(self._search_series_by_matching_author, query)
            series_future = executor.submit(
                self._search_field_hits,
                query=query,
                query_type="Series",
                limit=7,
                sort=SERIES_SEARCH_SORT,
                fields=SERIES_SEARCH_FIELDS,
                weights=SERIES_SEARCH_WEIGHTS,
            )

        author_series = author_future.result()
        hits = series_future.result()
        options: List[Dict[str, str]] = []
        seen_values: set[str] = set()

        series_items: List[Dict[str, Any]] = []
        series_items.extend(author_series)
        series_items.extend(
            doc for hit in hits
            if (doc := _unwrap_hit_document(hit)) is not None
        )

        for item in series_items:

            series_id = item.get("id")
            name = str(item.get("name") or "").strip()
            if series_id is None or not name:
                continue

            value = f"id:{series_id}"
            if value in seen_values:
                continue
            seen_values.add(value)

            option: Dict[str, str] = {
                "value": value,
                "label": name,
            }
            description = self._format_series_option_description(item)
            if description:
                option["description"] = description
            options.append(option)
            if len(options) >= 7:
                break

        return options

    def _resolve_series_search_value(self, series_value: str) -> Optional[Dict[str, Any]]:
        """Resolve a series field value to a canonical Hardcover series."""
        normalized_value = _normalize_search_text(series_value)
        if not normalized_value:
            return None

        if normalized_value.startswith(HARDCOVER_LIST_ID_PREFIX):
            try:
                return {"id": self._parse_prefixed_int(normalized_value, "series id")}
            except ValueError:
                logger.debug(f"Invalid Hardcover series id field value: {normalized_value}")
                return None

        result = self._execute_query(
            SEARCH_FIELD_OPTIONS_QUERY,
            {
                "query": normalized_value,
                "queryType": "Series",
                "limit": 10,
                "page": 1,
                "sort": SERIES_SEARCH_SORT,
                "fields": SERIES_SEARCH_FIELDS,
                "weights": SERIES_SEARCH_WEIGHTS,
            },
        )
        if not result:
            return None

        hits, _found_count = _extract_typesense_hits(result)
        if not hits:
            return None

        normalized_lookup = normalized_value.lower()
        candidates: List[Dict[str, Any]] = []
        for hit in hits:
            item = _unwrap_hit_document(hit)
            if item is None:
                continue
            try:
                series_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            candidates.append({"id": series_id, "name": name})

        if not candidates:
            return None

        exact_match = next(
            (candidate for candidate in candidates if candidate["name"].lower() == normalized_lookup),
            None,
        )
        return exact_match or candidates[0]

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="hardcover:series:rows:v4")
    def _fetch_series_ordered_rows(
        self,
        series_id: int,
        exclude_compilations: bool,
        exclude_unreleased: bool,
    ) -> Dict[str, Any]:
        """Fetch and process all books for a series (cached independently of page)."""
        empty: Dict[str, Any] = {"rows": [], "series_name": "", "total": 0}
        if not self.api_key:
            return empty

        result = self._execute_query(
            SERIES_BOOKS_BY_ID_QUERY,
            {"seriesId": series_id},
        )
        if not result:
            return empty

        series_items = result.get("series", [])
        if not isinstance(series_items, list) or not series_items:
            return empty

        series_data = series_items[0] if isinstance(series_items[0], dict) else {}
        series_name = str(series_data.get("name") or "").strip() if isinstance(series_data, dict) else ""
        allow_split_parts = _series_allows_split_parts(series_name)
        today = datetime.now().date()

        book_series_rows = series_data.get("book_series", []) if isinstance(series_data, dict) else []
        rows_by_position: Dict[float, Dict[str, Any]] = {}
        for row in book_series_rows:
            if not isinstance(row, dict):
                continue
            book_data = row.get("book", {})
            if not isinstance(book_data, dict) or not book_data:
                continue
            if exclude_compilations and book_data.get("compilation"):
                continue
            if not allow_split_parts and _split_part_base_title(str(book_data.get("title") or "")):
                continue

            position = _normalize_series_position(row.get("position"))
            if position is None:
                continue

            release_date = _parse_release_date(book_data.get("release_date"))
            if exclude_unreleased and (release_date is None or release_date.date() > today):
                continue

            sort_key = (
                1 if release_date and release_date.date() <= today else 0,
                0 if book_data.get("compilation") else 1,
                coerce_int(book_data.get("users_count"), 0),
                coerce_int(book_data.get("ratings_count"), 0),
                coerce_int(book_data.get("editions_count"), 0),
                -coerce_int(book_data.get("id"), 0),
            )
            existing_row = rows_by_position.get(position)
            if existing_row is None:
                rows_by_position[position] = {"row": row, "sort_key": sort_key}
                continue
            if sort_key > existing_row["sort_key"]:
                rows_by_position[position] = {"row": row, "sort_key": sort_key}

        ordered_rows = [
            entry["row"]
            for _position, entry in sorted(rows_by_position.items(), key=lambda item: item[0])
        ]
        return {"rows": ordered_rows, "series_name": series_name, "total": len(ordered_rows)}

    def _fetch_series_books_by_id(
        self,
        series_id: int,
        page: int,
        limit: int,
        exclude_compilations: bool,
        exclude_unreleased: bool,
    ) -> SearchResult:
        """Fetch books for a Hardcover series in canonical series order."""
        cached = self._fetch_series_ordered_rows(series_id, exclude_compilations, exclude_unreleased)
        ordered_rows = cached["rows"]
        series_name = cached["series_name"]
        total_found = cached["total"]

        offset = (page - 1) * limit
        page_rows = ordered_rows[offset:offset + limit]

        books: List[BookMetadata] = []
        for row in page_rows:
            book_data = row.get("book", {})
            if not isinstance(book_data, dict) or not book_data:
                continue
            try:
                parsed_book = self._parse_book(book_data)
                if not parsed_book:
                    continue
                parsed_book.series_id = str(series_id)
                if series_name:
                    parsed_book.series_name = series_name
                parsed_book.series_position = row.get("position")
                parsed_book.series_count = total_found
                books.append(parsed_book)
            except Exception as exc:
                logger.debug(f"Failed to parse Hardcover series book for series_id={series_id}: {exc}")

        has_more = offset + len(page_rows) < total_found
        return SearchResult(books=books, page=page, total_found=total_found, has_more=has_more)

    @cacheable(ttl=120, key_prefix="hardcover:user_lists")
    def _get_user_lists_cached(self, _cache_user_id: str) -> List[Dict[str, str]]:
        """Cached wrapper keyed by Hardcover user id to avoid cross-user cache leakage."""
        return self._fetch_user_lists()

    def _fetch_current_user_books_by_status(self, status_id: int, page: int, limit: int) -> SearchResult:
        """Fetch the current user's Hardcover books for a specific status shelf."""
        if not self.api_key:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        connected_user_id = self._resolve_current_user_id()
        if not connected_user_id:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        return self._fetch_user_books_by_status_cached(connected_user_id, status_id, page, limit)

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="hardcover:user_books:status")
    def _fetch_user_books_by_status_cached(
        self,
        _cache_user_id: str,
        status_id: int,
        page: int,
        limit: int,
    ) -> SearchResult:
        """Cached wrapper keyed by Hardcover user id and status shelf."""
        return self._fetch_user_books_by_status(status_id, page, limit)

    def _fetch_user_books_by_status(self, status_id: int, page: int, limit: int) -> SearchResult:
        """Fetch books from the current user's Hardcover status shelf."""
        if not self.api_key:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        offset = (page - 1) * limit
        result = self._execute_query(
            USER_BOOKS_BY_STATUS_QUERY,
            {
                "statusId": status_id,
                "limit": limit,
                "offset": offset,
            },
        )
        if not result:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        me_data = result.get("me", {})
        if isinstance(me_data, list) and me_data:
            me_data = me_data[0]
        if not isinstance(me_data, dict):
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        status_books = me_data.get("status_books", [])
        aggregate_data = me_data.get("status_books_aggregate", {})
        aggregate = aggregate_data.get("aggregate", {}) if isinstance(aggregate_data, dict) else {}
        count_raw = aggregate.get("count", 0) if isinstance(aggregate, dict) else 0

        try:
            total_found = int(count_raw)
        except (TypeError, ValueError):
            total_found = 0

        books: List[BookMetadata] = []
        for item in status_books:
            if not isinstance(item, dict):
                continue
            book_data = item.get("book", {})
            if not isinstance(book_data, dict) or not book_data:
                continue
            try:
                parsed_book = self._parse_book(book_data)
                if parsed_book:
                    books.append(parsed_book)
            except Exception as exc:
                logger.debug(f"Failed to parse Hardcover status book for status_id={status_id}: {exc}")

        has_more = offset + len(status_books) < total_found

        # Build source URL for the status shelf
        source_url = None
        url_slug = HARDCOVER_STATUS_URL_SLUGS.get(status_id)
        username = _get_connected_username()
        if url_slug and username:
            source_url = f"https://hardcover.app/@{username}/books/{url_slug}"

        return SearchResult(books=books, page=page, total_found=total_found, has_more=has_more, source_url=source_url)

    def _fetch_user_lists(self) -> List[Dict[str, str]]:
        """Fetch raw list options from Hardcover me query."""
        result = self._execute_query(USER_LISTS_QUERY, {})
        if not result:
            return []

        me_data = result.get("me", {})
        if isinstance(me_data, list) and me_data:
            me_data = me_data[0]
        if not isinstance(me_data, dict):
            return []

        options: List[Dict[str, str]] = []
        seen_values: set[str] = set()
        current_username = str(me_data.get("username") or "").strip()

        def _format_label(name: str, books_count: Any) -> str:
            try:
                return f"{name} ({int(books_count)})"
            except (TypeError, ValueError):
                return name

        want_to_read_count_data = me_data.get("want_to_read_books", {})
        want_to_read_aggregate = (
            want_to_read_count_data.get("aggregate", {})
            if isinstance(want_to_read_count_data, dict)
            else {}
        )
        want_to_read_count = (
            want_to_read_aggregate.get("count")
            if isinstance(want_to_read_aggregate, dict)
            else None
        )
        want_to_read_value = f"{HARDCOVER_STATUS_PREFIX}{HARDCOVER_WANT_TO_READ_STATUS_ID}"
        seen_values.add(want_to_read_value)
        options.append(
            {
                "value": want_to_read_value,
                "label": _format_label("Want to Read", want_to_read_count),
                "group": "My Books",
            }
        )

        for list_item in me_data.get("lists", []):
            if not isinstance(list_item, dict):
                continue
            list_id = list_item.get("id")
            slug = str(list_item.get("slug") or "").strip()
            name = str(list_item.get("name") or "").strip()
            value = f"id:{list_id}" if list_id is not None else slug
            if not value or not name or value in seen_values:
                continue
            seen_values.add(value)
            options.append(
                {
                    "value": value,
                    "label": _format_label(name, list_item.get("books_count")),
                    "group": "My Lists",
                }
            )

        for followed_item in me_data.get("followed_lists", []):
            if not isinstance(followed_item, dict):
                continue

            list_item = followed_item.get("list", {})
            if not isinstance(list_item, dict):
                continue

            list_id = list_item.get("id")
            slug = str(list_item.get("slug") or "").strip()
            name = str(list_item.get("name") or "").strip()
            value = f"id:{list_id}" if list_id is not None else slug
            if not value or not name or value in seen_values:
                continue
            seen_values.add(value)

            option: Dict[str, str] = {
                "value": value,
                "label": _format_label(name, list_item.get("books_count")),
                "group": "Followed Lists",
            }
            owner_data = list_item.get("user", {})
            if isinstance(owner_data, dict):
                owner_username = str(owner_data.get("username") or "").strip()
                if owner_username:
                    option["description"] = f"by @{owner_username}"
            elif current_username:
                option["description"] = f"by @{current_username}"
            options.append(option)

        return options

    def get_book_targets(self, book_id: str) -> List[Dict[str, Any]]:
        """Get writable Hardcover list/status targets for a specific book."""
        if not self.api_key:
            return []

        book_id_int = coerce_int(book_id, 0)
        if book_id_int < 1:
            raise ValueError("book_id must be a valid Hardcover book id")

        state = self._fetch_book_target_state(book_id_int)
        options = [
            dict(option)
            for option in self.get_user_lists()
            if option.get("group") in HARDCOVER_WRITABLE_TARGET_GROUPS
        ]

        for option in options:
            value = str(option.get("value") or "").strip()
            option["checked"] = self._is_target_checked(value, state)
            option["writable"] = True

        return options

    def set_book_target_state(self, book_id: str, target: str, selected: bool) -> Dict[str, Any]:
        """Set whether a Hardcover book belongs to a status shelf or user list."""
        if not self.api_key:
            raise ValueError("Hardcover is not configured")

        book_id_int = coerce_int(book_id, 0)
        if book_id_int < 1:
            raise ValueError("book_id must be a valid Hardcover book id")

        selected_target = str(target or "").strip()
        if not selected_target:
            raise ValueError("target is required")

        if selected_target not in self._get_writable_targets():
            raise ValueError("Unsupported Hardcover target")

        state = self._fetch_book_target_state(book_id_int)
        status_ids_to_invalidate: set[int] = set()
        list_ids_to_invalidate: set[int] = set()

        if selected_target.startswith(HARDCOVER_STATUS_PREFIX):
            status_id = self._parse_prefixed_int(selected_target, "status target")
            previous_status_id = state.status_id
            changed = self._set_status_target_state(book_id_int, status_id, selected, state)
            if changed:
                if previous_status_id is not None:
                    status_ids_to_invalidate.add(previous_status_id)
                status_ids_to_invalidate.add(status_id)
        elif selected_target.startswith(HARDCOVER_LIST_ID_PREFIX):
            list_id = self._parse_prefixed_int(selected_target, "list target")
            changed = self._set_list_target_state(book_id_int, list_id, selected, state)
            if changed:
                list_ids_to_invalidate.add(list_id)
        else:
            raise ValueError("Unsupported Hardcover target")

        if changed:
            self._invalidate_book_target_caches(
                connected_user_id=self._resolve_current_user_id(),
                status_ids=status_ids_to_invalidate,
                list_ids=list_ids_to_invalidate,
            )

        return {"changed": changed}

    @staticmethod
    def _unwrap_me_data(result: Optional[Dict]) -> Dict:
        """Extract and validate the ``me`` payload from a GraphQL result."""
        if not isinstance(result, dict):
            raise RuntimeError("Hardcover could not load book targets")

        me_data = result.get("me", {})
        if isinstance(me_data, list) and me_data:
            me_data = me_data[0]
        if not isinstance(me_data, dict):
            raise RuntimeError("Hardcover returned an invalid target payload")
        return me_data

    def _fetch_book_target_state(self, book_id: int) -> HardcoverBookTargetState:
        """Load current Hardcover membership state for a specific book."""
        result = self._execute_query(
            BOOK_TARGET_MEMBERSHIP_QUERY,
            {"bookId": book_id},
            raise_on_error=True,
        )
        me_data = self._unwrap_me_data(result)

        user_book_id: Optional[int] = None
        status_id: Optional[int] = None
        user_books = me_data.get("user_books", [])
        if isinstance(user_books, list) and user_books:
            latest_user_book = user_books[0] if isinstance(user_books[0], dict) else {}
            user_book_id = coerce_int(latest_user_book.get("id"), 0) or None
            status_id = coerce_int(latest_user_book.get("status_id"), 0) or None

        list_book_ids: Dict[int, int] = {}
        for user_list in me_data.get("lists", []):
            if not isinstance(user_list, dict):
                continue
            list_id = coerce_int(user_list.get("id"), 0)
            if list_id < 1:
                continue

            list_books = user_list.get("list_books", [])
            if not isinstance(list_books, list) or not list_books:
                continue

            list_book = list_books[0] if isinstance(list_books[0], dict) else {}
            list_book_id = coerce_int(list_book.get("id"), 0)
            if list_book_id > 0:
                list_book_ids[list_id] = list_book_id

        return HardcoverBookTargetState(
            user_book_id=user_book_id,
            status_id=status_id,
            list_book_ids=list_book_ids,
        )

    def _fetch_book_target_states_batch(
        self, book_ids: List[int],
    ) -> Dict[int, HardcoverBookTargetState]:
        """Load Hardcover membership state for multiple books in one query."""
        result = self._execute_query(
            BOOK_TARGET_MEMBERSHIP_BATCH_QUERY,
            {"bookIds": book_ids},
            raise_on_error=True,
        )
        me_data = self._unwrap_me_data(result)

        # Group user_books by book_id (keep only the latest per book)
        user_book_by_book: Dict[int, Dict] = {}
        for ub in me_data.get("user_books", []):
            if not isinstance(ub, dict):
                continue
            bid = coerce_int(ub.get("book_id"), 0)
            if bid > 0 and bid not in user_book_by_book:
                user_book_by_book[bid] = ub

        # Group list_book memberships by book_id
        list_book_ids_by_book: Dict[int, Dict[int, int]] = {}
        for user_list in me_data.get("lists", []):
            if not isinstance(user_list, dict):
                continue
            list_id = coerce_int(user_list.get("id"), 0)
            if list_id < 1:
                continue
            for lb in user_list.get("list_books", []):
                if not isinstance(lb, dict):
                    continue
                bid = coerce_int(lb.get("book_id"), 0)
                lb_id = coerce_int(lb.get("id"), 0)
                if bid > 0 and lb_id > 0:
                    list_book_ids_by_book.setdefault(bid, {})[list_id] = lb_id

        states: Dict[int, HardcoverBookTargetState] = {}
        for bid in book_ids:
            ub = user_book_by_book.get(bid)
            states[bid] = HardcoverBookTargetState(
                user_book_id=coerce_int(ub.get("id"), 0) or None if ub else None,
                status_id=coerce_int(ub.get("status_id"), 0) or None if ub else None,
                list_book_ids=list_book_ids_by_book.get(bid, {}),
            )
        return states

    def get_book_targets_batch(self, book_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        """Get writable Hardcover list/status targets for multiple books."""
        if not self.api_key or not book_ids:
            return {bid: [] for bid in book_ids}

        int_ids = []
        id_map: Dict[int, str] = {}
        for bid in book_ids:
            int_id = coerce_int(bid, 0)
            if int_id > 0:
                int_ids.append(int_id)
                id_map[int_id] = bid

        if not int_ids:
            return {bid: [] for bid in book_ids}

        states = self._fetch_book_target_states_batch(int_ids)
        writable_options = [
            dict(option)
            for option in self.get_user_lists()
            if option.get("group") in HARDCOVER_WRITABLE_TARGET_GROUPS
        ]

        results: Dict[str, List[Dict[str, Any]]] = {}
        for int_id, str_id in id_map.items():
            state = states.get(int_id, HardcoverBookTargetState(
                user_book_id=None, status_id=None, list_book_ids={},
            ))
            options = [dict(opt) for opt in writable_options]
            for option in options:
                value = str(option.get("value") or "").strip()
                option["checked"] = self._is_target_checked(value, state)
                option["writable"] = True
            results[str_id] = options

        # Fill in any book_ids that didn't parse as valid ints
        for bid in book_ids:
            if bid not in results:
                results[bid] = []

        return results

    def _get_writable_targets(self) -> set[str]:
        """Return the set of writable Hardcover targets for the current user."""
        writable_targets: set[str] = set()
        for option in self.get_user_lists():
            value = str(option.get("value") or "").strip()
            if (
                option.get("group") in HARDCOVER_WRITABLE_TARGET_GROUPS
                and value
                and (
                    value.startswith(HARDCOVER_STATUS_PREFIX)
                    or value.startswith(HARDCOVER_LIST_ID_PREFIX)
                )
            ):
                writable_targets.add(value)
        return writable_targets

    def _is_target_checked(self, target: str, state: HardcoverBookTargetState) -> bool:
        """Return whether a target is currently selected for the book."""
        if target.startswith(HARDCOVER_STATUS_PREFIX):
            return state.status_id == self._parse_prefixed_int(target)
        if target.startswith(HARDCOVER_LIST_ID_PREFIX):
            return self._parse_prefixed_int(target) in state.list_book_ids
        return False

    def _set_status_target_state(
        self,
        book_id: int,
        status_id: int,
        selected: bool,
        state: HardcoverBookTargetState,
    ) -> bool:
        """Set whether the book belongs to a Hardcover status shelf."""
        if selected:
            if state.user_book_id is None:
                result = self._execute_query(
                    INSERT_USER_BOOK_MUTATION,
                    {"bookId": book_id, "statusId": status_id},
                    raise_on_error=True,
                )
                self._check_mutation_result(result, "insert_user_book")
                return True

            if state.status_id == status_id:
                return False

            result = self._execute_query(
                UPDATE_USER_BOOK_MUTATION,
                {"userBookId": state.user_book_id, "statusId": status_id},
                raise_on_error=True,
            )
            self._check_mutation_result(result, "update_user_book")
            return True

        if state.user_book_id is None or state.status_id != status_id:
            return False

        result = self._execute_query(
            DELETE_USER_BOOK_MUTATION,
            {"userBookId": state.user_book_id},
            raise_on_error=True,
        )
        self._check_mutation_result(result, "delete_user_book", check_error=False)
        return True

    def _set_list_target_state(
        self,
        book_id: int,
        list_id: int,
        selected: bool,
        state: HardcoverBookTargetState,
    ) -> bool:
        """Set whether the book belongs to a Hardcover list."""
        list_book_id = state.list_book_ids.get(list_id)

        if selected:
            if list_book_id is not None:
                return False

            result = self._execute_query(
                INSERT_LIST_BOOK_MUTATION,
                {"bookId": book_id, "listId": list_id},
                raise_on_error=True,
            )
            self._check_mutation_result(result, "insert_list_book")
            return True

        if list_book_id is None:
            return False

        result = self._execute_query(
            DELETE_LIST_BOOK_MUTATION,
            {"listBookId": list_book_id},
            raise_on_error=True,
        )
        self._check_mutation_result(result, "delete_list_book", check_error=False)
        return True

    def _invalidate_book_target_caches(
        self,
        *,
        connected_user_id: Optional[str],
        status_ids: set[int],
        list_ids: set[int],
    ) -> None:
        """Invalidate caches affected by a target membership change."""
        metadata_cache = get_metadata_cache()

        if connected_user_id:
            metadata_cache.invalidate(cache_key("hardcover:user_lists", connected_user_id))
            for status_id in status_ids:
                metadata_cache.invalidate_prefix(
                    cache_key("hardcover:user_books:status", connected_user_id, status_id)
                )

        for list_id in list_ids:
            metadata_cache.invalidate_prefix(cache_key("hardcover:list:id", list_id))

    @staticmethod
    def _parse_prefixed_int(value: str, label: str = "target") -> int:
        """Parse an integer from a colon-prefixed value like 'status:1' or 'id:42'."""
        try:
            return int(value.split(":", 1)[1])
        except (IndexError, ValueError) as exc:
            raise ValueError(f"Invalid Hardcover {label}") from exc

    @staticmethod
    def _check_mutation_result(result: Any, key: str, *, check_error: bool = True) -> None:
        """Raise if a Hardcover mutation failed.

        When *check_error* is True (the default) the ``error`` field inside
        the payload is inspected and surfaced as a ``ValueError``.  Pass
        ``check_error=False`` for delete mutations that don't return an
        error field.
        """
        payload = result.get(key, {}) if isinstance(result, dict) else {}
        if isinstance(payload, dict):
            if check_error:
                error_text = str(payload.get("error") or "").strip()
                if error_text:
                    raise ValueError(error_text)
            if payload.get("id") is not None:
                return
        raise RuntimeError("Hardcover could not complete this action")

    def search(self, options: MetadataSearchOptions) -> List[BookMetadata]:
        """Search for books using Hardcover's search API."""
        return self.search_paginated(options).books

    def search_paginated(self, options: MetadataSearchOptions) -> SearchResult:
        """Search for books with pagination info."""
        if not self.api_key:
            logger.warning("Hardcover API key not configured")
            return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

        # Allow pasting a Hardcover list URL directly in the search input
        list_url_parts = self._detect_list_url(options.query)
        if list_url_parts:
            owner_username, list_slug = list_url_parts
            return self._fetch_list_books(list_slug, owner_username, options.page, options.limit)

        # Advanced filter list selector (shared fetch path with URL detection)
        list_value_from_field = str(options.fields.get("hardcover_list", "")).strip()
        if list_value_from_field:
            if list_value_from_field.startswith(HARDCOVER_STATUS_PREFIX):
                try:
                    status_id = self._parse_prefixed_int(list_value_from_field, "status")
                    return self._fetch_current_user_books_by_status(status_id, options.page, options.limit)
                except ValueError:
                    logger.debug(f"Invalid Hardcover status field value: {list_value_from_field}")
                    return SearchResult(books=[], page=options.page, total_found=0, has_more=False)
            if list_value_from_field.startswith(HARDCOVER_LIST_ID_PREFIX):
                try:
                    list_id = self._parse_prefixed_int(list_value_from_field, "list")
                    return self._fetch_list_books_by_id(list_id, options.page, options.limit)
                except ValueError:
                    logger.debug(f"Invalid hardcover_list field value: {list_value_from_field}")
                    return SearchResult(books=[], page=options.page, total_found=0, has_more=False)
            return self._fetch_list_books(list_value_from_field, None, options.page, options.limit)

        series_value_from_field = str(options.fields.get("series", "")).strip()
        if series_value_from_field:
            resolved_series = self._resolve_series_search_value(series_value_from_field)
            if not resolved_series:
                return SearchResult(books=[], page=options.page, total_found=0, has_more=False)
            exclude_compilations = app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False)
            exclude_unreleased = app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False)
            return self._fetch_series_books_by_id(
                int(resolved_series["id"]),
                options.page,
                options.limit,
                exclude_compilations,
                exclude_unreleased,
            )

        # Handle ISBN search separately
        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            books = [result] if result else []
            return SearchResult(books=books, page=1, total_found=len(books), has_more=False)

        # Build cache key from options (include fields and settings for cache differentiation)
        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        exclude_compilations = app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False)
        exclude_unreleased = app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False)
        cache_key = f"{options.query}:{options.search_type.value}:{options.sort.value}:{options.limit}:{options.page}:{fields_key}:excl_comp={exclude_compilations}:excl_unrel={exclude_unreleased}"
        return self._search_cached(cache_key, options)

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="hardcover:search")
    def _search_cached(self, cache_key: str, options: MetadataSearchOptions) -> SearchResult:
        """Cached search implementation."""
        # Determine query and fields based on custom search fields
        # Note: Hardcover API requires 'weights' when using 'fields' parameter
        author_value = options.fields.get("author", "").strip()
        title_value = options.fields.get("title", "").strip()

        # Build query and field configuration based on which fields are provided
        query, search_fields, search_weights = self._build_search_params(
            options.query, author_value, title_value, ""
        )

        # Build GraphQL query - include fields/weights parameters only when needed
        if search_fields:
            graphql_query = """
            query SearchBooks($query: String!, $limit: Int!, $page: Int!, $sort: String, $fields: String, $weights: String) {
                search(query: $query, query_type: "Book", per_page: $limit, page: $page, sort: $sort, fields: $fields, weights: $weights) {
                    results
                }
            }
            """
        else:
            graphql_query = """
            query SearchBooks($query: String!, $limit: Int!, $page: Int!, $sort: String) {
                search(query: $query, query_type: "Book", per_page: $limit, page: $page, sort: $sort) {
                    results
                }
            }
            """

        # Map abstract sort order to Hardcover's sort parameter
        sort_param = SORT_MAPPING.get(options.sort, SORT_MAPPING[SortOrder.RELEVANCE])

        variables = {
            "query": query,
            "limit": options.limit,
            "page": options.page,
            "sort": sort_param,
        }

        if search_fields:
            variables["fields"] = search_fields
            variables["weights"] = search_weights

        try:
            result = self._execute_query(graphql_query, variables)
            if not result:
                logger.debug("Hardcover search: No result from API")
                return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

            # Extract hits from Typesense response
            hits, found_count = _extract_typesense_hits(result)

            # Parse hits, filtering compilations and unreleased books if enabled
            exclude_compilations = app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False)
            exclude_unreleased = app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False)
            current_year = datetime.now().year
            books = []
            for hit in hits:
                item = _unwrap_hit_document(hit)
                if item is None:
                    continue
                if exclude_compilations and item.get("compilation"):
                    continue
                if exclude_unreleased:
                    release_year = item.get("release_year")
                    if release_year is not None and release_year > current_year:
                        continue
                book = self._parse_search_result(item)
                if book:
                    books.append(book)

            logger.info(f"Hardcover search '{query}' (fields={search_fields}) returned {len(books)} results")

            # Calculate if there are more results
            results_so_far = (options.page - 1) * HARDCOVER_PAGE_SIZE + len(hits)
            has_more = results_so_far < found_count

            return SearchResult(
                books=books,
                page=options.page,
                total_found=found_count,
                has_more=has_more
            )

        except Exception as e:
            logger.error(f"Hardcover search error: {e}")
            return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="hardcover:book")
    def get_book(self, book_id: str) -> Optional[BookMetadata]:
        """Get book details by Hardcover ID."""
        if not self.api_key:
            logger.warning("Hardcover API key not configured")
            return None

        # Query for specific book by ID
        # Use contributions with filter to get only primary authors (not translators/narrators)
        # Also include cached_contributors as fallback if contributions is empty
        # Include featured_book_series for series info
        # Include editions with titles and languages for localized search support
        graphql_query = """
        query GetBook($id: Int!) {
            books(where: {id: {_eq: $id}}, limit: 1) {
                id
                title
                subtitle
                slug
                release_date
                headline
                description
                pages
                cached_image
                cached_tags
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                default_physical_edition {
                    isbn_10
                    isbn_13
                }
                featured_book_series {
                    position
                    series {
                        id
                        name
                        primary_books_count
                    }
                }
                editions(
                    distinct_on: language_id
                    order_by: [{language_id: asc}, {users_count: desc}]
                    limit: 200
                ) {
                    title
                    language {
                        language
                        code2
                        code3
                    }
                }
            }
        }
        """

        try:
            book_id_int = int(book_id)
            result = self._execute_query(graphql_query, {"id": book_id_int})
            if not result:
                return None

            books = result.get("books", [])
            if not books:
                return None

            return self._parse_book(books[0])

        except ValueError:
            logger.error(f"Invalid book ID: {book_id}")
            return None
        except Exception as e:
            logger.error(f"Hardcover get_book error: {e}")
            return None

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="hardcover:isbn")
    def search_by_isbn(self, isbn: str) -> Optional[BookMetadata]:
        """Search for a book by ISBN-10 or ISBN-13."""
        if not self.api_key:
            logger.warning("Hardcover API key not configured")
            return None

        # Clean ISBN (remove hyphens)
        clean_isbn = isbn.replace("-", "").strip()

        # Search for editions with matching ISBN
        # Use contributions with filter to get only primary authors (not translators/narrators)
        graphql_query = """
        query SearchByISBN($isbn: String!) {
            editions(
                where: {
                    _or: [
                        {isbn_10: {_eq: $isbn}},
                        {isbn_13: {_eq: $isbn}}
                    ]
                },
                limit: 1
            ) {
                isbn_10
                isbn_13
                book {
                    id
                    title
                    subtitle
                    slug
                    release_date
                    headline
                    description
                    pages
                    cached_image
                    cached_tags
                    contributions(where: {contribution: {_eq: "Author"}}) {
                        author {
                            name
                        }
                    }
                }
            }
        }
        """

        try:
            result = self._execute_query(graphql_query, {"isbn": clean_isbn})
            if not result:
                return None

            editions = result.get("editions", [])
            if not editions:
                logger.debug(f"No Hardcover book found for ISBN: {isbn}")
                return None

            edition = editions[0]
            book_data = edition.get("book", {})
            if not book_data:
                return None

            # Add ISBN data from edition to book data
            book_data["isbn_10"] = edition.get("isbn_10")
            book_data["isbn_13"] = edition.get("isbn_13")

            return self._parse_book(book_data)

        except Exception as e:
            logger.error(f"Hardcover ISBN search error: {e}")
            return None

    def _execute_query(
        self,
        query: str,
        variables: Dict[str, Any],
        *,
        raise_on_error: bool = False,
    ) -> Optional[Dict]:
        """Execute a GraphQL query and return data or None on error."""
        try:
            response = self.session.post(
                HARDCOVER_API_URL,
                json={"query": query, "variables": variables},
                timeout=15,
                verify=get_ssl_verify(HARDCOVER_API_URL),
            )
            response.raise_for_status()

            data = response.json()

            if "errors" in data:
                logger.error(f"GraphQL errors: {data['errors']}")
                if raise_on_error:
                    message = _extract_graphql_error_message(data) or "Hardcover rejected this request"
                    raise HardcoverGraphQLError(message)
                return None

            return data.get("data")

        except requests.Timeout:
            logger.warning("Hardcover API request timed out")
            if raise_on_error:
                raise RuntimeError("Hardcover API request timed out")
            return None
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                logger.error("Hardcover API key is invalid")
                if raise_on_error:
                    raise RuntimeError("Hardcover API key is invalid")
            else:
                logger.error(f"Hardcover API HTTP error: {e}")
                if raise_on_error:
                    raise RuntimeError(f"Hardcover API HTTP error: {e}")
            return None
        except HardcoverGraphQLError:
            raise
        except ValueError as e:
            logger.error(f"Hardcover API returned invalid JSON: {e}")
            if raise_on_error:
                raise RuntimeError("Hardcover API returned an invalid response") from e
            return None
        except Exception as e:
            logger.error(f"Hardcover API request failed: {e}")
            if raise_on_error:
                raise RuntimeError("Hardcover API request failed") from e
            return None

    def _parse_search_result(self, item: Dict) -> Optional[BookMetadata]:
        """Parse a search result item into BookMetadata."""
        try:
            book_id = item.get("id") or item.get("document", {}).get("id")
            title = item.get("title") or item.get("document", {}).get("title")

            if not book_id or not title:
                return None

            # Extract authors - use contribution_types to filter author_names if available
            authors = []

            author_names = item.get("author_names", [])
            if isinstance(author_names, str):
                author_names = [author_names]

            contribution_types = item.get("contribution_types", [])

            # If we have parallel arrays, filter to only "Author" contributions
            if contribution_types and len(contribution_types) == len(author_names):
                for name, contrib_type in zip(author_names, contribution_types):
                    if contrib_type == "Author":
                        authors.append(name)
            elif author_names:
                # No contribution_types or length mismatch - use all names as fallback
                authors = author_names

            # Normalize whitespace in author names (some API data has multiple spaces)
            authors = [" ".join(name.split()) for name in authors]

            search_author = _simplify_author_for_search(authors[0]) if authors else None

            cover_url = _extract_cover_url(item, "image")
            publish_year = _extract_publish_year(item)
            source_url = _build_source_url(item.get("slug", ""))

            # Build display fields from Hardcover-specific data
            display_fields = []

            # Rating (e.g., "4.5 (3,764)")
            rating = item.get("rating")
            ratings_count = item.get("ratings_count")
            if rating is not None:
                rating_str = f"{rating:.1f}"
                if ratings_count:
                    rating_str += f" ({ratings_count:,})"
                display_fields.append(DisplayField(label="Rating", value=rating_str, icon="star"))

            # Readers (users who have this book)
            users_count = item.get("users_count")
            if users_count:
                display_fields.append(DisplayField(label="Readers", value=f"{users_count:,}", icon="users"))

            # Combine headline and description if both present
            headline = item.get("headline")
            description = item.get("description")
            full_description = _combine_headline_description(headline, description)

            # Extract subtitle if available in search results
            subtitle = item.get("subtitle")

            return BookMetadata(
                provider="hardcover",
                provider_id=str(book_id),
                title=title,
                subtitle=subtitle,
                search_title=_compute_search_title(title, subtitle),
                search_author=search_author,
                provider_display_name="Hardcover",
                authors=authors,
                cover_url=cover_url,
                description=full_description,
                publish_year=publish_year,
                source_url=source_url,
                display_fields=display_fields,
            )


        except Exception as e:
            logger.debug(f"Failed to parse Hardcover search result: {e}")
            return None

    def _parse_book(self, book: Dict) -> BookMetadata:
        """Parse a book object into BookMetadata."""
        title = str(book.get("title") or "")
        subtitle = book.get("subtitle")

        # Extract authors - try contributions first (filtered), fall back to cached_contributors
        authors = []
        contributions = book.get("contributions") or []
        cached_contributors = book.get("cached_contributors") or []

        # Try contributions first (filtered to "Author" role only - cleaner data)
        for contrib in contributions:
            author = contrib.get("author", {})
            if author and author.get("name"):
                authors.append(author["name"])

        # Fallback to cached_contributors if no authors found
        if not authors:
            for contrib in cached_contributors:
                if isinstance(contrib, dict):
                    # Handle nested structure: {"author": {"name": "..."}, "contribution": ...}
                    if contrib.get("author", {}).get("name"):
                        authors.append(contrib["author"]["name"])
                    # Handle flat structure: {"name": "..."}
                    elif contrib.get("name"):
                        authors.append(contrib["name"])
                elif isinstance(contrib, str):
                    authors.append(contrib)

        # Normalize whitespace in author names (some API data has multiple spaces)
        authors = [" ".join(name.split()) for name in authors]

        search_author = _simplify_author_for_search(authors[0]) if authors else None

        cover_url = _extract_cover_url(book, "cached_image", "image")
        publish_year = _extract_publish_year(book)

        # Extract genres from cached_tags
        genres = []
        for tag in book.get("cached_tags", []):
            if isinstance(tag, dict) and tag.get("tag"):
                genres.append(tag["tag"])
            elif isinstance(tag, str):
                genres.append(tag)

        # Get ISBN from direct fields, default_physical_edition, or editions
        isbn_10 = book.get("isbn_10")
        isbn_13 = book.get("isbn_13")

        if not isbn_10 and not isbn_13:
            # Try default_physical_edition first
            edition = book.get("default_physical_edition")
            if edition:
                isbn_10 = edition.get("isbn_10")
                isbn_13 = edition.get("isbn_13")

            # Fallback to editions array
            if not isbn_10 and not isbn_13 and book.get("editions"):
                for ed in book["editions"]:
                    if not isbn_10 and ed.get("isbn_10"):
                        isbn_10 = ed["isbn_10"]
                    if not isbn_13 and ed.get("isbn_13"):
                        isbn_13 = ed["isbn_13"]
                    if isbn_10 and isbn_13:
                        break

        source_url = _build_source_url(book.get("slug", ""))

        # Combine headline and description if both present
        headline = book.get("headline")
        description = book.get("description")
        full_description = _combine_headline_description(headline, description)

        # Extract series info from featured_book_series
        series_id = None
        series_name = None
        series_position = None
        series_count = None
        featured_series = book.get("featured_book_series")
        if featured_series:
            series_position = featured_series.get("position")
            series_data = featured_series.get("series")
            if series_data:
                if series_data.get("id") is not None:
                    series_id = str(series_data.get("id"))
                series_name = series_data.get("name")
                series_count = series_data.get("primary_books_count")

        # Extract titles by language from editions
        # This allows searching with localized titles when language filter is active
        titles_by_language: Dict[str, str] = {}
        editions = book.get("editions", [])
        for edition in editions:
            edition_title = edition.get("title")
            lang_data = edition.get("language")
            if edition_title and lang_data:
                # Store by various language identifiers for flexible matching
                # Language name (e.g., "German", "English")
                lang_name = lang_data.get("language")
                # 2-letter code (e.g., "de", "en")
                code2 = lang_data.get("code2")
                # 3-letter code (e.g., "deu", "eng")
                code3 = lang_data.get("code3")

                # Store with all available keys (first title wins for each language)
                if lang_name and lang_name not in titles_by_language:
                    titles_by_language[lang_name] = edition_title
                if code2 and code2 not in titles_by_language:
                    titles_by_language[code2] = edition_title
                if code3 and code3 not in titles_by_language:
                    titles_by_language[code3] = edition_title

        # Build display fields from Hardcover-specific metrics
        display_fields: List[DisplayField] = []

        rating = book.get("rating")
        ratings_count = book.get("ratings_count")
        if rating is not None:
            try:
                rating_str = f"{float(rating):.1f}"
            except (TypeError, ValueError):
                rating_str = str(rating)

            if ratings_count:
                try:
                    rating_str += f" ({int(ratings_count):,})"
                except (TypeError, ValueError):
                    pass

            display_fields.append(DisplayField(label="Rating", value=rating_str, icon="star"))

        users_count = book.get("users_count")
        if users_count:
            try:
                readers_value = f"{int(users_count):,}"
            except (TypeError, ValueError):
                readers_value = str(users_count)
            display_fields.append(DisplayField(label="Readers", value=readers_value, icon="users"))

        return BookMetadata(
             provider="hardcover",
             provider_id=str(book["id"]),
             title=title,
             subtitle=subtitle,
             search_title=_compute_search_title(title, subtitle, series_name=series_name),
             search_author=search_author,
             provider_display_name="Hardcover",
             authors=authors,
             isbn_10=isbn_10,
             isbn_13=isbn_13,
             cover_url=cover_url,
             description=full_description,
             publish_year=publish_year,
             genres=genres,
             source_url=source_url,
             series_id=series_id,
             series_name=series_name,
             series_position=series_position,
             series_count=series_count,
             titles_by_language=titles_by_language,
             display_fields=display_fields,
         )



def _test_hardcover_connection(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test the Hardcover API connection using current form values."""
    from shelfmark.core.config import config as app_config

    current_values = current_values or {}

    # Use current form values first, fall back to saved config
    raw_key = current_values.get("HARDCOVER_API_KEY") or app_config.get("HARDCOVER_API_KEY", "")
    # Strip "Bearer " prefix if user pasted the full auth header from Hardcover
    api_key = raw_key.removeprefix("Bearer ").strip() if raw_key else ""

    key_len = len(api_key) if api_key else 0
    logger.debug(f"Hardcover test: key length={key_len}")

    if not api_key:
        # Clear any stored connection metadata since there's no key
        _save_connected_user(None, None)
        return {"success": False, "message": "API key is required"}

    if key_len < 100:
        return {"success": False, "message": f"API key seems too short ({key_len} chars). Expected 500+ chars."}

    try:
        provider = HardcoverProvider(api_key=api_key)
        # Use the 'me' query to test connection (recommended by API docs)
        result = provider._execute_query(
            "query { me { id, username } }",
            {}
        )
        if result is not None:
            # Handle both single object and array response formats
            me_data = result.get("me", {})
            if isinstance(me_data, list) and me_data:
                me_data = me_data[0]
            user_id = str(me_data.get("id")) if isinstance(me_data, dict) and me_data.get("id") is not None else None
            username = me_data.get("username", "Unknown") if isinstance(me_data, dict) else "Unknown"

            # Save connected user metadata for persistent display + per-user list caching
            _save_connected_user(user_id, username)

            return {"success": True, "message": f"Connected as: {username}"}
        else:
            _save_connected_user(None, None)
            return {"success": False, "message": "API request failed - check your API key"}
    except Exception as e:
        logger.exception("Hardcover connection test failed")
        _save_connected_user(None, None)
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _save_connected_user(user_id: Optional[str], username: Optional[str]) -> None:
    """Save or clear connected user metadata in config."""
    from shelfmark.core.settings_registry import save_config_file, load_config_file

    config = load_config_file("hardcover")
    if user_id:
        config["_connected_user_id"] = user_id
    else:
        config.pop("_connected_user_id", None)

    if username:
        config["_connected_username"] = username
    else:
        config.pop("_connected_username", None)

    save_config_file("hardcover", config)


def _get_connected_username() -> Optional[str]:
    """Get the stored connected username."""
    from shelfmark.core.settings_registry import load_config_file

    config = load_config_file("hardcover")
    return config.get("_connected_username")


def _get_connected_user_id() -> Optional[str]:
    """Get the stored connected Hardcover user id."""
    from shelfmark.core.settings_registry import load_config_file

    config = load_config_file("hardcover")
    value = config.get("_connected_user_id")
    return str(value) if value is not None else None


# Hardcover sort options for settings UI
_HARDCOVER_SORT_OPTIONS = [
    {"value": "relevance", "label": "Most relevant"},
    {"value": "popularity", "label": "Most popular"},
    {"value": "rating", "label": "Highest rated"},
    {"value": "newest", "label": "Newest"},
    {"value": "oldest", "label": "Oldest"},
]


@register_settings("hardcover", "Hardcover", icon="book", order=51, group="metadata_providers")
def hardcover_settings():
    """Hardcover metadata provider settings."""
    # Check for connected username to show status
    connected_user = _get_connected_username()
    test_button_description = f"Connected as: {connected_user}" if connected_user else "Verify your API key works"

    return [
        HeadingField(
            key="hardcover_heading",
            title="Hardcover",
            description="A modern book tracking and discovery platform with a comprehensive API.",
            link_url="https://hardcover.app",
            link_text="hardcover.app",
        ),
        CheckboxField(
            key="HARDCOVER_ENABLED",
            label="Enable Hardcover",
            description="Enable Hardcover as a metadata provider for book searches",
            default=False,
        ),
        PasswordField(
            key="HARDCOVER_API_KEY",
            label="API Key",
            description="Get your API key from hardcover.app/account/api",
            required=True,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description=test_button_description,
            style="primary",
            callback=_test_hardcover_connection,
        ),
        SelectField(
            key="HARDCOVER_DEFAULT_SORT",
            label="Default Sort Order",
            description="Default sort order for Hardcover search results.",
            options=_HARDCOVER_SORT_OPTIONS,
            default="relevance",
        ),
        CheckboxField(
            key="HARDCOVER_EXCLUDE_COMPILATIONS",
            label="Exclude Compilations",
            description="Filter out compilations, anthologies, and omnibus editions from search results",
            default=False,
        ),
        CheckboxField(
            key="HARDCOVER_EXCLUDE_UNRELEASED",
            label="Exclude Unreleased Books",
            description="Filter out books with a release year in the future",
            default=False,
        ),
    ]
