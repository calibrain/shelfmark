"""Hardcover list and status-shelf workflows."""

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from shelfmark.core.cache import cacheable
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_int
from shelfmark.metadata_providers import BookMetadata, SearchResult

from .auth import _get_connected_user_id, _get_connected_username, _save_connected_user
from .constants import (
    HARDCOVER_LIST_URL_PATTERN,
    HARDCOVER_STATUS_GROUP,
    HARDCOVER_STATUS_PREFIX,
    HARDCOVER_STATUS_URL_SLUGS,
    HARDCOVER_STATUSES,
)
from .queries import (
    LIST_BOOKS_BY_ID_QUERY,
    LIST_LOOKUP_QUERY,
    USER_BOOKS_BY_STATUS_QUERY,
    USER_LISTS_QUERY,
)

logger = setup_logger(__name__)


class HardcoverListsMixin:
    if TYPE_CHECKING:
        api_key: str

        def _execute_query(
            self,
            query: str,
            variables: dict[str, Any],
            *,
            raise_on_error: bool = False,
        ) -> dict[str, Any] | None: ...

        def _parse_book(self, book: dict[str, Any]) -> BookMetadata: ...

    def _detect_list_url(self, query: str) -> tuple[str | None, str] | None:
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

        # Build source URL and title from list metadata
        source_url = None
        source_title = str(list_data.get("name") or "").strip() or None
        list_slug = str(list_data.get("slug") or "").strip()
        user_data = list_data.get("user", {})
        owner_username = (
            str(user_data.get("username") or "").strip() if isinstance(user_data, dict) else ""
        )
        if list_slug and owner_username:
            source_url = f"https://hardcover.app/@{owner_username}/lists/{list_slug}"

        try:
            books_count = int(books_count_raw)
        except TypeError, ValueError:
            books_count = 0

        books: list[BookMetadata] = []
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
            except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                logger.debug("Failed to parse Hardcover list book for list_id=%s: %s", list_id, exc)

        has_more = offset + len(list_books) < books_count
        return SearchResult(
            books=books,
            page=page,
            total_found=books_count,
            has_more=has_more,
            source_url=source_url,
            source_title=source_title,
        )

    @cacheable(
        ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="hardcover:list:slug"
    )
    def _fetch_list_books(
        self, slug: str, owner_username: str | None, page: int, limit: int
    ) -> SearchResult:
        """Fetch list books by slug, optionally disambiguating by owner username."""
        if not self.api_key:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        lookup = self._execute_query(LIST_LOOKUP_QUERY, {"slug": slug})
        if not lookup:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        lists = lookup.get("lists", [])
        if not isinstance(lists, list) or not lists:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        selected: dict[str, Any] | None = None
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

        list_id = coerce_int(selected.get("id"), 0)
        if list_id < 1:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        return self._fetch_list_books_by_id(list_id, page, limit)

    def _resolve_current_user_id(self) -> str | None:
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

    def get_user_lists(self) -> list[dict[str, str]]:
        """Get authenticated user's own and followed Hardcover lists."""
        if not self.api_key:
            return []

        connected_user_id = self._resolve_current_user_id()
        if not connected_user_id:
            return self._fetch_user_lists()

        return self._get_user_lists_cached(connected_user_id)

    @cacheable(ttl=120, key_prefix="hardcover:user_lists")
    def _get_user_lists_cached(self, _cache_user_id: str) -> list[dict[str, str]]:
        """Return cached user lists keyed by Hardcover user id."""
        return self._fetch_user_lists()

    def _fetch_current_user_books_by_status(
        self, status_id: int, page: int, limit: int
    ) -> SearchResult:
        """Fetch the current user's Hardcover books for a specific status shelf."""
        if not self.api_key:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        connected_user_id = self._resolve_current_user_id()
        if not connected_user_id:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        return self._fetch_user_books_by_status_cached(connected_user_id, status_id, page, limit)

    @cacheable(
        ttl_key="METADATA_CACHE_SEARCH_TTL",
        ttl_default=300,
        key_prefix="hardcover:user_books:status",
    )
    def _fetch_user_books_by_status_cached(
        self,
        _cache_user_id: str,
        status_id: int,
        page: int,
        limit: int,
    ) -> SearchResult:
        """Return cached status-shelf books keyed by user id and shelf."""
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
        except TypeError, ValueError:
            total_found = 0

        books: list[BookMetadata] = []
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
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                logger.debug(
                    "Failed to parse Hardcover status book for status_id=%s: %s", status_id, exc
                )

        has_more = offset + len(status_books) < total_found

        # Build source URL for the status shelf
        source_url = None
        url_slug = HARDCOVER_STATUS_URL_SLUGS.get(status_id)
        username = _get_connected_username()
        if url_slug and username:
            source_url = f"https://hardcover.app/@{username}/books/{url_slug}"

        return SearchResult(
            books=books,
            page=page,
            total_found=total_found,
            has_more=has_more,
            source_url=source_url,
        )

    def _fetch_user_lists(self) -> list[dict[str, str]]:
        """Fetch raw list options from Hardcover me query."""
        result = self._execute_query(USER_LISTS_QUERY, {})
        if not result:
            return []

        me_data = result.get("me", {})
        if isinstance(me_data, list) and me_data:
            me_data = me_data[0]
        if not isinstance(me_data, dict):
            return []

        options: list[dict[str, str]] = []
        seen_values: set[str] = set()
        current_username = str(me_data.get("username") or "").strip()

        def _format_label(name: str, books_count: Any) -> str:
            try:
                return f"{name} ({int(books_count)})"
            except TypeError, ValueError:
                return name

        for status in HARDCOVER_STATUSES:
            count_data = me_data.get(status["query_key"], {})
            aggregate = count_data.get("aggregate", {}) if isinstance(count_data, dict) else {}
            count = aggregate.get("count") if isinstance(aggregate, dict) else None
            value = f"{HARDCOVER_STATUS_PREFIX}{status['id']}"
            seen_values.add(value)
            options.append(
                {
                    "value": value,
                    "label": _format_label(status["label"], count),
                    "group": HARDCOVER_STATUS_GROUP,
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

            option: dict[str, str] = {
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
