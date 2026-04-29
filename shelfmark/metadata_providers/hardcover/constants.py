"""Constants for the Hardcover metadata provider."""

import re

from shelfmark.metadata_providers import SearchType, SortOrder

HARDCOVER_API_URL = "https://api.hardcover.app/v1/graphql"
HARDCOVER_PAGE_SIZE = 25  # Hardcover API returns max 25 results per page
HARDCOVER_MIN_AUTHOR_PARTS = 2
HARDCOVER_MIN_TYPEAHEAD_QUERY_LENGTH = 2
HARDCOVER_MAX_SERIES_OPTIONS = 7
HARDCOVER_API_KEY_MIN_LENGTH = 100
HARDCOVER_LIST_URL_PATTERN = re.compile(
    r"^/(?:@([\w.-]+)/)?lists?/([\w-]+)/?$",
    re.IGNORECASE,
)

HARDCOVER_STATUS_PREFIX = "status:"
HARDCOVER_STATUSES: list[dict] = [
    {"id": 1, "label": "Want to Read", "slug": "want-to-read", "query_key": "want_to_read_count"},
    {
        "id": 2,
        "label": "Currently Reading",
        "slug": "currently-reading",
        "query_key": "currently_reading_count",
    },
    {"id": 3, "label": "Read", "slug": "read", "query_key": "read_count"},
    {
        "id": 5,
        "label": "Did Not Finish",
        "slug": "did-not-finish",
        "query_key": "did_not_finish_count",
    },
]
HARDCOVER_STATUS_URL_SLUGS: dict[int, str] = {s["id"]: s["slug"] for s in HARDCOVER_STATUSES}
HARDCOVER_STATUS_GROUP = "Reading Status"
HARDCOVER_LIST_ID_PREFIX = "id:"
HARDCOVER_WRITABLE_TARGET_GROUPS = {HARDCOVER_STATUS_GROUP, "My Lists"}

SORT_MAPPING: dict[SortOrder, str] = {
    SortOrder.RELEVANCE: "_text_match:desc,users_count:desc",
    SortOrder.POPULARITY: "users_count:desc",
    SortOrder.RATING: "rating:desc",
    SortOrder.NEWEST: "release_year:desc",
    SortOrder.OLDEST: "release_year:asc",
}
SEARCH_TYPE_FIELDS: dict[SearchType, str] = {
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
