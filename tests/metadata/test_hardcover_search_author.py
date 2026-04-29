from shelfmark.metadata_providers import MetadataSearchOptions, SearchResult
from shelfmark.metadata_providers.hardcover import HardcoverProvider, _simplify_author_for_search


class TestHardcoverSimplifyAuthorForSearch:
    def test_removes_middle_initial(self):
        assert _simplify_author_for_search("Robert R. McCammon") == "Robert McCammon"

    def test_keeps_suffix(self):
        assert _simplify_author_for_search("Martin L. King Jr.") == "Martin King Jr."

    def test_handles_comma_format(self):
        assert _simplify_author_for_search("McCammon, Robert R.") == "Robert McCammon"

    def test_returns_none_when_no_change(self):
        assert _simplify_author_for_search("Frank Herbert") is None


class TestHardcoverAuthorSearch:
    def test_author_text_search_uses_default_book_search_fields(self):
        provider = HardcoverProvider(api_key="test-token")

        assert provider._build_search_params("", "Stephen King", "", "") == (
            "Stephen King",
            None,
            None,
        )

    def test_search_paginated_uses_selected_author_id(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        expected = SearchResult(books=[], page=2, total_found=14, has_more=True)
        captured: dict[str, int] = {}

        monkeypatch.setattr(
            "shelfmark.metadata_providers.hardcover.app_config.get",
            lambda key, default=None: {
                "HARDCOVER_EXCLUDE_COMPILATIONS": True,
                "HARDCOVER_EXCLUDE_UNRELEASED": False,
            }.get(key, default),
        )

        def fake_fetch(
            author_id: int,
            page: int,
            limit: int,
            *,
            exclude_compilations: bool,
            exclude_unreleased: bool,
        ) -> SearchResult:
            captured["author_id"] = author_id
            captured["page"] = page
            captured["limit"] = limit
            captured["exclude_compilations"] = int(exclude_compilations)
            captured["exclude_unreleased"] = int(exclude_unreleased)
            return expected

        monkeypatch.setattr(provider, "_fetch_author_books_by_id", fake_fetch)

        result = provider.search_paginated(
            MetadataSearchOptions(
                query="",
                page=2,
                limit=20,
                fields={"author": "id:42"},
            )
        )

        assert result == expected
        assert captured == {
            "author_id": 42,
            "page": 2,
            "limit": 20,
            "exclude_compilations": 1,
            "exclude_unreleased": 0,
        }

    def test_fetch_author_books_by_id_returns_books(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        captured: dict[str, object] = {}

        monkeypatch.setattr(
            provider,
            "_execute_query",
            lambda query, variables: (
                captured.update({"query": query, "variables": variables})
                or {
                    "authors": [
                        {
                            "name": "Stephen King",
                            "contributions": [
                                {
                                    "contribution": "Author, Narrator",
                                    "book": {
                                        "id": 1,
                                        "title": "The Shining",
                                        "subtitle": None,
                                        "slug": "the-shining",
                                        "release_date": "1977-01-28",
                                        "headline": None,
                                        "description": None,
                                        "pages": 447,
                                        "rating": 4.3,
                                        "ratings_count": 1000,
                                        "users_count": 2000,
                                        "compilation": False,
                                        "editions_count": 20,
                                        "cached_image": {},
                                        "cached_contributors": [{"name": "Stephen King"}],
                                        "contributions": [],
                                        "featured_book_series": None,
                                    },
                                }
                            ],
                            "contributions_aggregate": {"aggregate": {"count": 1}},
                        }
                    ]
                }
            ),
        )

        result = provider._fetch_author_books_by_id(
            42,
            page=1,
            limit=20,
            exclude_compilations=True,
            exclude_unreleased=True,
        )

        assert "contributions(" in str(captured["query"])
        assert (
            "contribution:"
            not in str(captured["query"]).split("contributions(", 1)[1].split(") {", 1)[0]
        )
        assert "canonical_id: {_is_null: true}" in str(captured["query"])
        assert captured["variables"] == {"authorId": 42, "limit": 20, "offset": 0}
        assert result.total_found == 1
        assert result.has_more is False
        assert [book.title for book in result.books] == ["The Shining"]
        assert result.books[0].authors == ["Stephen King"]
