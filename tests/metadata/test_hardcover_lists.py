from shelfmark.metadata_providers import MetadataSearchOptions, SearchResult
from shelfmark.metadata_providers.hardcover import (
    HARDCOVER_WANT_TO_READ_STATUS_ID,
    HardcoverProvider,
)


class TestHardcoverLists:
    def test_fetch_user_lists_includes_want_to_read_shelf(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")

        monkeypatch.setattr(
            provider,
            "_execute_query",
            lambda query, variables: {
                "me": {
                    "username": "alex",
                    "want_to_read_books": {
                        "aggregate": {
                            "count": 7,
                        }
                    },
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
            "value": f"status:{HARDCOVER_WANT_TO_READ_STATUS_ID}",
            "label": "Want to Read (7)",
            "group": "My Books",
        }
        assert options[1] == {
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
                fields={"hardcover_list": f"status:{HARDCOVER_WANT_TO_READ_STATUS_ID}"},
            )
        )

        assert result == expected
        assert captured == {
            "status_id": HARDCOVER_WANT_TO_READ_STATUS_ID,
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
            HARDCOVER_WANT_TO_READ_STATUS_ID,
            page=1,
            limit=10,
        )

        assert captured["variables"] == {
            "statusId": HARDCOVER_WANT_TO_READ_STATUS_ID,
            "limit": 10,
            "offset": 0,
        }
        assert "distinct_on: [book_id]" in str(captured["query"])
        assert result.total_found == 1
        assert result.has_more is False
        assert len(result.books) == 1
        assert result.books[0].title == "Dune"
        assert result.books[0].authors == ["Frank Herbert"]
