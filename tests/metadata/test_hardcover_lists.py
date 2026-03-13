from shelfmark.core.cache import cache_key
from shelfmark.metadata_providers import MetadataSearchOptions, SearchResult
from shelfmark.metadata_providers.hardcover import (
    HARDCOVER_STATUS_GROUP,
    HardcoverProvider,
)


class CacheStub:
    """Minimal cache stub that records invalidation calls."""

    def __init__(self):
        self.invalidated: list[str] = []
        self.invalidated_prefixes: list[str] = []

    def invalidate(self, key: str) -> bool:
        self.invalidated.append(key)
        return True

    def invalidate_prefix(self, prefix: str) -> int:
        self.invalidated_prefixes.append(prefix)
        return 1


class TestHardcoverLists:
    def test_fetch_user_lists_includes_all_status_shelves(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")

        monkeypatch.setattr(
            provider,
            "_execute_query",
            lambda query, variables: {
                "me": {
                    "username": "alex",
                    "want_to_read_count": {"aggregate": {"count": 7}},
                    "currently_reading_count": {"aggregate": {"count": 3}},
                    "read_count": {"aggregate": {"count": 20}},
                    "did_not_finish_count": {"aggregate": {"count": 1}},
                    "lists": [
                        {
                            "id": 42,
                            "name": "Sci-Fi Favourites",
                            "slug": "sci-fi-favourites",
                            "books_count": 12,
                        }
                    ],
                    "followed_lists": [],
                }
            },
        )

        options = provider._fetch_user_lists()

        assert options[0] == {
            "value": "status:1",
            "label": "Want to Read (7)",
            "group": HARDCOVER_STATUS_GROUP,
        }
        assert options[1] == {
            "value": "status:2",
            "label": "Currently Reading (3)",
            "group": HARDCOVER_STATUS_GROUP,
        }
        assert options[2] == {
            "value": "status:3",
            "label": "Read (20)",
            "group": HARDCOVER_STATUS_GROUP,
        }
        assert options[3] == {
            "value": "status:5",
            "label": "Did Not Finish (1)",
            "group": HARDCOVER_STATUS_GROUP,
        }
        assert options[4] == {
            "value": "id:42",
            "label": "Sci-Fi Favourites (12)",
            "group": "My Lists",
        }

    def test_search_paginated_uses_status_field_as_list_source(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        expected = SearchResult(books=[], page=2, total_found=14, has_more=True)
        captured: dict[str, int] = {}

        def fake_fetch(status_id: int, page: int, limit: int) -> SearchResult:
            captured["status_id"] = status_id
            captured["page"] = page
            captured["limit"] = limit
            return expected

        monkeypatch.setattr(provider, "_fetch_current_user_books_by_status", fake_fetch)

        result = provider.search_paginated(
            MetadataSearchOptions(
                query="",
                page=2,
                limit=20,
                fields={"hardcover_list": "status:1"},
            )
        )

        assert result == expected
        assert captured == {
            "status_id": 1,
            "page": 2,
            "limit": 20,
        }

    def test_fetch_current_user_books_by_status_returns_books(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        captured: dict[str, object] = {}

        monkeypatch.setattr(provider, "_resolve_current_user_id", lambda: "123")

        def fake_execute(query: str, variables):
            captured["query"] = query
            captured["variables"] = variables
            return {
                "me": {
                    "status_books": [
                        {
                            "book": {
                                "id": 9000,
                                "title": "Dune",
                                "subtitle": None,
                                "slug": "dune",
                                "release_date": "1965-08-01",
                                "headline": None,
                                "description": "Arrakis.",
                                "rating": 4.6,
                                "ratings_count": 100,
                                "users_count": 200,
                                "cached_image": {"url": "https://example.com/dune.jpg"},
                                "cached_contributors": [{"name": "Frank Herbert"}],
                                "contributions": [],
                                "featured_book_series": None,
                            }
                        }
                    ],
                    "status_books_aggregate": {
                        "aggregate": {
                            "count": 1,
                        }
                    },
                }
            }

        monkeypatch.setattr(provider, "_execute_query", fake_execute)

        result = provider._fetch_current_user_books_by_status(
            1,
            page=1,
            limit=10,
        )

        assert captured["variables"] == {
            "statusId": 1,
            "limit": 10,
            "offset": 0,
        }
        assert "distinct_on: [book_id]" in str(captured["query"])
        assert result.total_found == 1
        assert result.has_more is False
        assert len(result.books) == 1
        assert result.books[0].title == "Dune"
        assert result.books[0].authors == ["Frank Herbert"]

    def test_get_book_targets_marks_checked_memberships(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")

        monkeypatch.setattr(
            provider,
            "get_user_lists",
            lambda: [
                {
                    "value": "status:1",
                    "label": "Want to Read (7)",
                    "group": HARDCOVER_STATUS_GROUP,
                },
                {
                    "value": "id:42",
                    "label": "Sci-Fi Favourites (12)",
                    "group": "My Lists",
                },
                {
                    "value": "id:99",
                    "label": "Followed List (3)",
                    "group": "Followed Lists",
                },
            ],
        )

        monkeypatch.setattr(
            provider,
            "_execute_query",
            lambda query, variables, raise_on_error=False: {
                "me": {
                    "user_books": [{"id": 55, "status_id": 1}],
                    "lists": [
                        {"id": 42, "list_books": [{"id": 500}]},
                        {"id": 99, "list_books": [{"id": 900}]},
                    ],
                }
            },
        )

        options = provider.get_book_targets("123")

        assert options == [
            {
                "value": "status:1",
                "label": "Want to Read (7)",
                "group": HARDCOVER_STATUS_GROUP,
                "checked": True,
                "writable": True,
            },
            {
                "value": "id:42",
                "label": "Sci-Fi Favourites (12)",
                "group": "My Lists",
                "checked": True,
                "writable": True,
            },
        ]

    def test_set_book_target_state_updates_existing_status(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        captured: dict[str, object] = {}
        cache_stub = CacheStub()

        monkeypatch.setattr(
            provider,
            "get_user_lists",
            lambda: [
                {
                    "value": "status:1",
                    "label": "Want to Read (7)",
                    "group": HARDCOVER_STATUS_GROUP,
                }
            ],
        )
        monkeypatch.setattr(provider, "_resolve_current_user_id", lambda: "user-123")
        monkeypatch.setattr("shelfmark.metadata_providers.hardcover.get_metadata_cache", lambda: cache_stub)

        def fake_execute(query: str, variables, raise_on_error: bool = False):
            if "query GetBookTargetMembership" in query:
                return {
                    "me": {
                        "user_books": [{"id": 77, "status_id": 2}],
                        "lists": [],
                    }
                }
            if "mutation UpdateBookStatus" in query:
                captured["variables"] = variables
                return {
                    "update_user_book": {
                        "id": 77,
                        "error": None,
                        "user_book": {"id": 77, "book_id": 123, "status_id": 1},
                    }
                }
            raise AssertionError(f"Unexpected query: {query}")

        monkeypatch.setattr(provider, "_execute_query", fake_execute)

        result = provider.set_book_target_state(
            "123",
            "status:1",
            True,
        )

        assert result == {"changed": True, "deselected_target": "status:2"}
        assert captured["variables"] == {
            "userBookId": 77,
            "statusId": 1,
        }
        assert cache_stub.invalidated == [
            cache_key("hardcover:user_lists", "user-123"),
        ]
        assert set(cache_stub.invalidated_prefixes) == {
            cache_key("hardcover:user_books:status", "user-123", 2),
            cache_key("hardcover:user_books:status", "user-123", 1),
        }

    def test_set_book_target_state_removes_list_membership(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        cache_stub = CacheStub()

        monkeypatch.setattr(
            provider,
            "get_user_lists",
            lambda: [
                {
                    "value": "id:42",
                    "label": "Sci-Fi Favourites (12)",
                    "group": "My Lists",
                }
            ],
        )
        monkeypatch.setattr(provider, "_resolve_current_user_id", lambda: "user-123")
        monkeypatch.setattr("shelfmark.metadata_providers.hardcover.get_metadata_cache", lambda: cache_stub)

        def fake_execute(query: str, variables, raise_on_error: bool = False):
            if "query GetBookTargetMembership" in query:
                return {
                    "me": {
                        "user_books": [],
                        "lists": [{"id": 42, "list_books": [{"id": 500}]}],
                    }
                }
            if "mutation RemoveBookFromList" in query:
                return {
                    "delete_list_book": {
                        "id": 500,
                        "list_id": 42,
                    }
                }
            raise AssertionError(f"Unexpected query: {query}")

        monkeypatch.setattr(provider, "_execute_query", fake_execute)

        result = provider.set_book_target_state("123", "id:42", False)

        assert result == {"changed": True}
        assert cache_stub.invalidated == [
            cache_key("hardcover:user_lists", "user-123"),
        ]
        assert cache_stub.invalidated_prefixes == [
            cache_key("hardcover:list:id", 42),
        ]

    def test_set_book_target_state_rejects_non_writable_targets(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")

        monkeypatch.setattr(
            provider,
            "get_user_lists",
            lambda: [
                {
                    "value": "id:99",
                    "label": "Followed List (3)",
                    "group": "Followed Lists",
                }
            ],
        )

        try:
            provider.set_book_target_state("123", "id:99", True)
        except ValueError as exc:
            assert str(exc) == "Unsupported Hardcover target"
        else:
            raise AssertionError("Expected ValueError for followed list target")
