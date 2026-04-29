"""Search, typeahead, series, and book lookup workflows for Hardcover."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from shelfmark.core.cache import cacheable
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_bool, coerce_int
from shelfmark.metadata_providers import (
    BookMetadata,
    MetadataSearchOptions,
    SearchResult,
    SearchType,
    SortOrder,
)

from .constants import (
    AUTHOR_SUGGESTION_FIELDS,
    AUTHOR_SUGGESTION_SORT,
    AUTHOR_SUGGESTION_WEIGHTS,
    HARDCOVER_LIST_ID_PREFIX,
    HARDCOVER_MAX_SERIES_OPTIONS,
    HARDCOVER_MIN_TYPEAHEAD_QUERY_LENGTH,
    HARDCOVER_PAGE_SIZE,
    HARDCOVER_STATUS_PREFIX,
    SERIES_SEARCH_FIELDS,
    SERIES_SEARCH_SORT,
    SERIES_SEARCH_WEIGHTS,
    SORT_MAPPING,
    TITLE_SUGGESTION_FIELDS,
    TITLE_SUGGESTION_SORT,
    TITLE_SUGGESTION_WEIGHTS,
)
from .parsing import (
    _extract_typesense_hits,
    _normalize_search_text,
    _normalize_series_position,
    _parse_release_date,
    _query_matches_author_name,
    _series_allows_split_parts,
    _split_part_base_title,
    _unwrap_hit_document,
)
from .queries import (
    AUTHOR_BOOKS_BY_ID_QUERY,
    GET_BOOK_QUERY,
    SEARCH_BOOKS_QUERY,
    SEARCH_BOOKS_WITH_FIELDS_QUERY,
    SEARCH_BY_ISBN_QUERY,
    SEARCH_FIELD_OPTIONS_QUERY,
    SERIES_BOOKS_BY_ID_QUERY,
    SERIES_BY_AUTHOR_IDS_QUERY,
)

logger = setup_logger(__name__)


class HardcoverSearchMixin:
    if TYPE_CHECKING:
        api_key: str

        def _detect_list_url(self, query: str) -> tuple[str | None, str] | None: ...

        def _execute_query(
            self,
            query: str,
            variables: dict[str, Any],
            *,
            raise_on_error: bool = False,
        ) -> dict[str, Any] | None: ...

        def _fetch_current_user_books_by_status(
            self, status_id: int, page: int, limit: int
        ) -> SearchResult: ...

        def _fetch_list_books(
            self, slug: str, owner_username: str | None, page: int, limit: int
        ) -> SearchResult: ...

        def _fetch_list_books_by_id(self, list_id: int, page: int, limit: int) -> SearchResult: ...

        def _parse_book(self, book: dict[str, Any]) -> BookMetadata: ...

        @staticmethod
        def _parse_prefixed_int(value: str, label: str = "target") -> int: ...

        def _parse_search_result(self, item: dict[str, Any]) -> BookMetadata | None: ...

        def get_user_lists(self) -> list[dict[str, str]]: ...

    def _build_search_params(
        self, default_query: str, author: str, title: str, series: str
    ) -> tuple[str, str | None, str | None]:
        """Build search query, fields, and weights based on provided values.

        Returns (query, fields, weights) tuple. Fields/weights are None for general search.
        """
        if author and not title and not series:
            return author, None, None
        if title and not author and not series:
            return title, "title,alternative_titles", "5,1"
        if author and title and not series:
            return f"{title} {author}", "title,alternative_titles,author_names", "5,1,3"
        return default_query, None, None

    def get_search_field_options(
        self,
        field_key: str,
        query: str | None = None,
    ) -> list[dict[str, str]]:
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
        sort: str | None,
        fields: str | None,
        weights: str | None,
    ) -> list[dict[str, Any]]:
        """Run a Hardcover search request for field-level typeahead options."""
        normalized_query = _normalize_search_text(query)
        if not self.api_key or len(normalized_query) < HARDCOVER_MIN_TYPEAHEAD_QUERY_LENGTH:
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

    def _search_series_by_matching_author(self, query: str) -> list[dict[str, Any]]:
        """Return direct series rows when the query clearly matches an author."""
        author_hits = self._search_field_hits(
            query=query,
            query_type="Author",
            limit=2,
            sort=AUTHOR_SUGGESTION_SORT,
            fields=AUTHOR_SUGGESTION_FIELDS,
            weights=AUTHOR_SUGGESTION_WEIGHTS,
        )

        author_ids: list[int] = []
        for hit in author_hits:
            item = _unwrap_hit_document(hit)
            if item is None:
                continue

            author_name = str(item.get("name") or "").strip()
            if not _query_matches_author_name(query, author_name):
                continue

            author_id = coerce_int(item.get("id"), 0)
            if author_id < 1:
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
    def _search_author_options(self, query: str) -> list[dict[str, str]]:
        """Return typeahead options for Hardcover author search."""
        hits = self._search_field_hits(
            query=query,
            query_type="Author",
            limit=7,
            sort=AUTHOR_SUGGESTION_SORT,
            fields=AUTHOR_SUGGESTION_FIELDS,
            weights=AUTHOR_SUGGESTION_WEIGHTS,
        )
        options: list[dict[str, str]] = []
        seen_labels: set[str] = set()

        for hit in hits:
            item = _unwrap_hit_document(hit)
            if item is None:
                continue

            author_id = coerce_int(item.get("id"), 0)
            label = str(item.get("name") or "").strip()
            normalized_label = label.casefold()
            if author_id < 1 or not label or normalized_label in seen_labels:
                continue

            seen_labels.add(normalized_label)
            options.append({"value": f"id:{author_id}", "label": label})

        return options

    @cacheable(ttl=120, key_prefix="hardcover:title:options")
    def _search_title_options(self, query: str) -> list[dict[str, str]]:
        """Return typeahead options for Hardcover title search."""
        hits = self._search_field_hits(
            query=query,
            query_type="Book",
            limit=7,
            sort=TITLE_SUGGESTION_SORT,
            fields=TITLE_SUGGESTION_FIELDS,
            weights=TITLE_SUGGESTION_WEIGHTS,
        )

        exclude_compilations = coerce_bool(
            app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False),
            default=False,
        )
        exclude_unreleased = coerce_bool(
            app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False),
            default=False,
        )
        current_year = datetime.now(UTC).year

        options: list[dict[str, str]] = []
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
                except TypeError, ValueError:
                    pass

            label = str(item.get("title") or "").strip()
            normalized_label = label.casefold()
            if not label or normalized_label in seen_labels:
                continue

            seen_labels.add(normalized_label)
            options.append({"value": label, "label": label})

        return options

    def _format_series_option_description(self, item: dict[str, Any]) -> str | None:
        """Build a short description for a series suggestion option."""
        author_name = item.get("author_name")
        if not author_name:
            author_data = item.get("author")
            if isinstance(author_data, dict):
                author_name = author_data.get("name")

        parts: list[str] = []
        if author_name:
            parts.append(f"by {author_name}")

        books_count = item.get("primary_books_count")
        if books_count is None:
            books_count = item.get("books_count")

        try:
            if books_count is not None:
                books_count_int = int(books_count)
                parts.append(f"{books_count_int} book{'s' if books_count_int != 1 else ''}")
        except TypeError, ValueError:
            pass

        return " • ".join(parts) if parts else None

    @cacheable(ttl=120, key_prefix="hardcover:series:options")
    def _search_series_options(self, query: str) -> list[dict[str, str]]:
        """Return typeahead options for Hardcover series search."""
        from concurrent.futures import ThreadPoolExecutor

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
        options: list[dict[str, str]] = []
        seen_values: set[str] = set()

        series_items: list[dict[str, Any]] = []
        series_items.extend(author_series)
        series_items.extend(doc for hit in hits if (doc := _unwrap_hit_document(hit)) is not None)

        for item in series_items:
            series_id = item.get("id")
            name = str(item.get("name") or "").strip()
            if series_id is None or not name:
                continue

            value = f"id:{series_id}"
            if value in seen_values:
                continue
            seen_values.add(value)

            option: dict[str, str] = {
                "value": value,
                "label": name,
            }
            description = self._format_series_option_description(item)
            if description:
                option["description"] = description
            options.append(option)
            if len(options) >= HARDCOVER_MAX_SERIES_OPTIONS:
                break

        return options

    def _resolve_series_search_value(self, series_value: str) -> dict[str, Any] | None:
        """Resolve a series field value to a canonical Hardcover series."""
        normalized_value = _normalize_search_text(series_value)
        if not normalized_value:
            return None

        if normalized_value.startswith(HARDCOVER_LIST_ID_PREFIX):
            try:
                return {"id": self._parse_prefixed_int(normalized_value, "series id")}
            except ValueError:
                logger.debug("Invalid Hardcover series id field value: %s", normalized_value)
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
        candidates: list[dict[str, Any]] = []
        for hit in hits:
            item = _unwrap_hit_document(hit)
            if item is None:
                continue
            series_id = coerce_int(item.get("id"), 0)
            if series_id < 1:
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            candidates.append({"id": series_id, "name": name})

        if not candidates:
            return None

        exact_match = next(
            (
                candidate
                for candidate in candidates
                if candidate["name"].lower() == normalized_lookup
            ),
            None,
        )
        return exact_match or candidates[0]

    @cacheable(
        ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="hardcover:series:rows:v4"
    )
    def _fetch_series_ordered_rows(
        self,
        series_id: int,
        *,
        exclude_compilations: bool,
        exclude_unreleased: bool,
    ) -> dict[str, Any]:
        """Fetch and process all books for a series (cached independently of page)."""
        empty: dict[str, Any] = {"rows": [], "series_name": "", "total": 0}
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
        series_name = (
            str(series_data.get("name") or "").strip() if isinstance(series_data, dict) else ""
        )
        allow_split_parts = _series_allows_split_parts(series_name)
        today = datetime.now(UTC).date()

        book_series_rows = (
            series_data.get("book_series", []) if isinstance(series_data, dict) else []
        )
        rows_by_position: dict[float, dict[str, Any]] = {}
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
        *,
        exclude_compilations: bool,
        exclude_unreleased: bool,
    ) -> SearchResult:
        """Fetch books for a Hardcover series in canonical series order."""
        cached = self._fetch_series_ordered_rows(
            series_id,
            exclude_compilations=exclude_compilations,
            exclude_unreleased=exclude_unreleased,
        )
        ordered_rows = cached["rows"]
        series_name = cached["series_name"]
        total_found = cached["total"]

        offset = (page - 1) * limit
        page_rows = ordered_rows[offset : offset + limit]

        books: list[BookMetadata] = []
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
            except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                logger.debug(
                    "Failed to parse Hardcover series book for series_id=%s: %s", series_id, exc
                )

        has_more = offset + len(page_rows) < total_found
        return SearchResult(books=books, page=page, total_found=total_found, has_more=has_more)

    def _fetch_author_books_by_id(
        self,
        author_id: int,
        page: int,
        limit: int,
        *,
        exclude_compilations: bool,
        exclude_unreleased: bool,
    ) -> SearchResult:
        """Fetch books for a selected Hardcover author."""
        if not self.api_key:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        offset = (page - 1) * limit
        result = self._execute_query(
            AUTHOR_BOOKS_BY_ID_QUERY,
            {"authorId": author_id, "limit": limit, "offset": offset},
        )
        if not result:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        author_items = result.get("authors", [])
        if not isinstance(author_items, list) or not author_items:
            return SearchResult(books=[], page=page, total_found=0, has_more=False)

        author_data = author_items[0] if isinstance(author_items[0], dict) else {}
        contributions = (
            author_data.get("contributions", []) if isinstance(author_data, dict) else []
        )
        aggregate = (
            author_data.get("contributions_aggregate", {}) if isinstance(author_data, dict) else {}
        )
        total_found = coerce_int(
            aggregate.get("aggregate", {}).get("count") if isinstance(aggregate, dict) else 0,
            0,
        )
        today = datetime.now(UTC).date()

        books: list[BookMetadata] = []
        for row in contributions:
            if not isinstance(row, dict):
                continue
            contribution = str(row.get("contribution") or "").strip()
            if contribution and "author" not in contribution.casefold():
                continue
            book_data = row.get("book", {})
            if not isinstance(book_data, dict) or not book_data:
                continue
            if exclude_compilations and book_data.get("compilation"):
                continue
            release_date = _parse_release_date(book_data.get("release_date"))
            if exclude_unreleased and (release_date is None or release_date.date() > today):
                continue
            try:
                parsed_book = self._parse_book(book_data)
                books.append(parsed_book)
            except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
                logger.debug(
                    "Failed to parse Hardcover author book for author_id=%s: %s",
                    author_id,
                    exc,
                )

        has_more = offset + len(contributions) < total_found
        return SearchResult(books=books, page=page, total_found=total_found, has_more=has_more)

    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
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
                    return self._fetch_current_user_books_by_status(
                        status_id, options.page, options.limit
                    )
                except ValueError:
                    logger.debug("Invalid Hardcover status field value: %s", list_value_from_field)
                    return SearchResult(books=[], page=options.page, total_found=0, has_more=False)
            if list_value_from_field.startswith(HARDCOVER_LIST_ID_PREFIX):
                try:
                    list_id = self._parse_prefixed_int(list_value_from_field, "list")
                    return self._fetch_list_books_by_id(list_id, options.page, options.limit)
                except ValueError:
                    logger.debug("Invalid hardcover_list field value: %s", list_value_from_field)
                    return SearchResult(books=[], page=options.page, total_found=0, has_more=False)
            return self._fetch_list_books(list_value_from_field, None, options.page, options.limit)

        series_value_from_field = str(options.fields.get("series", "")).strip()
        if series_value_from_field:
            resolved_series = self._resolve_series_search_value(series_value_from_field)
            if not resolved_series:
                return SearchResult(books=[], page=options.page, total_found=0, has_more=False)
            exclude_compilations = coerce_bool(
                app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False),
                default=False,
            )
            exclude_unreleased = coerce_bool(
                app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False),
                default=False,
            )
            return self._fetch_series_books_by_id(
                int(resolved_series["id"]),
                options.page,
                options.limit,
                exclude_compilations=exclude_compilations,
                exclude_unreleased=exclude_unreleased,
            )

        author_value_from_field = str(options.fields.get("author", "")).strip()
        if author_value_from_field.startswith(HARDCOVER_LIST_ID_PREFIX):
            try:
                author_id = self._parse_prefixed_int(author_value_from_field, "author id")
            except ValueError:
                logger.debug("Invalid Hardcover author id field value: %s", author_value_from_field)
                return SearchResult(books=[], page=options.page, total_found=0, has_more=False)
            exclude_compilations = coerce_bool(
                app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False),
                default=False,
            )
            exclude_unreleased = coerce_bool(
                app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False),
                default=False,
            )
            return self._fetch_author_books_by_id(
                author_id,
                options.page,
                options.limit,
                exclude_compilations=exclude_compilations,
                exclude_unreleased=exclude_unreleased,
            )

        # Handle ISBN search separately
        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            books = [result] if result else []
            return SearchResult(books=books, page=1, total_found=len(books), has_more=False)

        # Build cache key from options (include fields and settings for cache differentiation)
        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        exclude_compilations = coerce_bool(
            app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False),
            default=False,
        )
        exclude_unreleased = coerce_bool(
            app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False),
            default=False,
        )
        cache_key = f"{options.query}:{options.search_type.value}:{options.sort.value}:{options.limit}:{options.page}:{fields_key}:excl_comp={exclude_compilations}:excl_unrel={exclude_unreleased}"
        return self._search_cached(cache_key, options)

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="hardcover:search")
    def _search_cached(self, cache_key: str, options: MetadataSearchOptions) -> SearchResult:
        """Return cached Hardcover search results."""
        # Determine query and fields based on custom search fields
        # Note: Hardcover API requires 'weights' when using 'fields' parameter
        author_value = options.fields.get("author", "").strip()
        title_value = options.fields.get("title", "").strip()

        # Build query and field configuration based on which fields are provided
        query, search_fields, search_weights = self._build_search_params(
            options.query, author_value, title_value, ""
        )

        graphql_query = SEARCH_BOOKS_WITH_FIELDS_QUERY if search_fields else SEARCH_BOOKS_QUERY

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
            exclude_compilations = coerce_bool(
                app_config.get("HARDCOVER_EXCLUDE_COMPILATIONS", False),
                default=False,
            )
            exclude_unreleased = coerce_bool(
                app_config.get("HARDCOVER_EXCLUDE_UNRELEASED", False),
                default=False,
            )
            current_year = datetime.now(UTC).year
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

            logger.info(
                "Hardcover search '%s' (fields=%s) returned %s results",
                query,
                search_fields,
                len(books),
            )

            # Calculate if there are more results
            results_so_far = (options.page - 1) * HARDCOVER_PAGE_SIZE + len(hits)
            has_more = results_so_far < found_count

            return SearchResult(
                books=books, page=options.page, total_found=found_count, has_more=has_more
            )

        except AttributeError, KeyError, TypeError, ValueError:
            logger.exception("Hardcover search error")
            return SearchResult(books=[], page=options.page, total_found=0, has_more=False)

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="hardcover:book")
    def get_book(self, book_id: str) -> BookMetadata | None:
        """Get book details by Hardcover ID."""
        if not self.api_key:
            logger.warning("Hardcover API key not configured")
            return None

        try:
            book_id_int = int(book_id)
            result = self._execute_query(GET_BOOK_QUERY, {"id": book_id_int})
            if not result:
                return None

            books = result.get("books", [])
            if not books:
                return None

            return self._parse_book(books[0])

        except ValueError:
            logger.exception("Invalid book ID: %s", book_id)
            return None
        except AttributeError, KeyError, TypeError:
            logger.exception("Hardcover get_book error")
            return None

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="hardcover:isbn")
    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        """Search for a book by ISBN-10 or ISBN-13."""
        if not self.api_key:
            logger.warning("Hardcover API key not configured")
            return None

        # Clean ISBN (remove hyphens)
        clean_isbn = isbn.replace("-", "").strip()

        try:
            result = self._execute_query(SEARCH_BY_ISBN_QUERY, {"isbn": clean_isbn})
            if not result:
                return None

            editions = result.get("editions", [])
            if not editions:
                logger.debug("No Hardcover book found for ISBN: %s", isbn)
                return None

            edition = editions[0]
            book_data = edition.get("book", {})
            if not book_data:
                return None

            # Add ISBN data from edition to book data
            book_data["isbn_10"] = edition.get("isbn_10")
            book_data["isbn_13"] = edition.get("isbn_13")

            return self._parse_book(book_data)

        except AttributeError, IndexError, KeyError, TypeError, ValueError:
            logger.exception("Hardcover ISBN search error")
            return None
