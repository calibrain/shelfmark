from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import (
    BookMetadata,
    MetadataSearchOptions,
    SearchType,
    SortOrder,
    get_provider_capabilities,
    get_provider_kwargs,
)
from shelfmark.metadata_providers.audible import AudibleProvider


@pytest.fixture(autouse=True)
def clear_metadata_cache():
    get_metadata_cache().clear()
    yield
    get_metadata_cache().clear()


def _sample_book(
    *,
    asin: str = "B017V4IM1G",
    title: str = "Harry Potter and the Sorcerer's Stone, Book 1",
    release_date: str = "2015-11-20T00:00:00.000+00:00",
    isbn: str = "978-0590353427",
    series_position: str = "1",
    narrators: list[dict[str, str]] | None = None,
) -> dict:
    return {
        "asin": asin,
        "title": title,
        "subtitle": "Book 1",
        "description": "<p>Wizard boy.</p>",
        "summary": "<p>Summary text.</p>",
        "publisher": "Pottermore Publishing",
        "isbn": isbn,
        "language": "english",
        "rating": 4.8,
        "bookFormat": "unabridged",
        "releaseDate": release_date,
        "imageUrl": "https://example.test/cover.jpg",
        "link": f"https://audible.com/pd/{asin}",
        "lengthMinutes": 498,
        "authors": [{"name": "J.K. Rowling"}],
        "narrators": narrators or [{"name": "Jim Dale"}],
        "genres": [{"name": "Fantasy"}, {"name": "Adventure"}],
        "series": [
            {
                "asin": "SERIES1",
                "name": "Harry Potter",
                "position": series_position,
            }
        ],
    }


class TestAudibleProvider:
    def test_provider_kwargs_factory_reads_normalized_settings(self, monkeypatch):
        monkeypatch.setattr(
            "shelfmark.metadata_providers.audible.app_config.get",
            lambda key, default=None: {
                "AUDIBLE_BASE_URL": "beta.audimeta.de/",
                "AUDIBLE_REGION": "DE",
                "AUDIBLE_REQUEST_TIMEOUT": "22",
                "AUDIBLE_USER_AGENT": "Shelfmark Test Agent/2.0",
                "AUDIBLE_USE_UPSTREAM_CACHE": False,
                "AUDIBLE_EXCLUDE_UNRELEASED": True,
            }.get(key, default),
        )

        kwargs = get_provider_kwargs("audible")

        assert kwargs == {
            "base_url": "https://beta.audimeta.de",
            "region": "de",
            "timeout": 22,
            "user_agent": "Shelfmark Test Agent/2.0",
            "use_upstream_cache": False,
            "exclude_unreleased": True,
        }

    def test_capabilities_expose_view_series(self):
        assert get_provider_capabilities("audible") == [
            {
                "key": "view_series",
                "field_key": "series",
                "sort": "series_order",
            }
        ]

    def test_search_fields_enable_typeahead_for_series_only(self):
        provider = AudibleProvider()
        fields_by_key = {field.key: field for field in provider.search_fields}

        assert fields_by_key["author"].suggestions_endpoint is None
        assert fields_by_key["series"].suggestions_endpoint == (
            "/api/metadata/field-options?provider=audible&field=series"
        )

    def test_get_book_accepts_object_payloads(self, monkeypatch):
        provider = AudibleProvider(region="us")

        monkeypatch.setattr(
            provider,
            "_make_request",
            lambda endpoint, *, params, include_region: _sample_book(),
        )

        book = provider.get_book("B017V4IM1G")

        assert book is not None
        assert book.provider == "audible"
        assert book.provider_id == "B017V4IM1G"
        assert book.isbn_10 is None
        assert book.isbn_13 == "9780590353427"
        assert book.description == "Wizard boy."
        assert book.series_name == "Harry Potter"
        assert book.series_position == 1.0
        assert [field.label for field in book.display_fields] == [
            "Rating",
            "Length",
            "Narrator",
            "Format",
        ]

    def test_get_book_falls_back_to_sanitized_summary_when_description_is_blank(self, monkeypatch):
        provider = AudibleProvider(region="us")

        payload = _sample_book()
        payload["description"] = "   "
        payload["summary"] = "<p>Summary <strong>text</strong>.</p>"

        monkeypatch.setattr(
            provider,
            "_make_request",
            lambda endpoint, *, params, include_region: payload,
        )

        book = provider.get_book("B017V4IM1G")

        assert book is not None
        assert book.description == "Summary text."

    def test_search_by_isbn_falls_back_to_search_when_db_lookup_is_empty(self, monkeypatch):
        provider = AudibleProvider(region="us")
        calls: list[tuple[str, dict]] = []

        def fake_make_request(endpoint, *, params, include_region):
            calls.append((endpoint, dict(params)))
            if endpoint == "/db/book":
                return []
            if endpoint == "/search":
                return [_sample_book(isbn="9781980036135")]
            return None

        monkeypatch.setattr(provider, "_make_request", fake_make_request)

        book = provider.search_by_isbn("978-1-980036-13-5")

        assert book is not None
        assert book.provider_id == "B017V4IM1G"
        assert calls[0] == ("/db/book", {"isbn": "9781980036135", "limit": 1, "page": 1})
        assert calls[1] == ("/search", {"query": "9781980036135", "limit": 5, "page": 0})

    def test_search_paginated_uses_expected_query_params(self, monkeypatch):
        provider = AudibleProvider(region="de", use_upstream_cache=False)
        captured: dict[str, object] = {}

        def fake_make_request(endpoint, *, params, include_region):
            captured["endpoint"] = endpoint
            captured["params"] = dict(params)
            captured["include_region"] = include_region
            return [_sample_book()]

        monkeypatch.setattr(provider, "_make_request", fake_make_request)

        result = provider.search_paginated(
            MetadataSearchOptions(
                query="The Hobbit",
                search_type=SearchType.AUTHOR,
                sort=SortOrder.NEWEST,
                limit=12,
                page=2,
                fields={"narrator": "Andy Serkis"},
            )
        )

        assert result.page == 2
        assert len(result.books) == 1
        assert captured == {
            "endpoint": "/search",
            "params": {
                "author": "The Hobbit",
                "narrator": "Andy Serkis",
                "products_sort_by": "-ReleaseDate",
                "limit": 12,
                "page": 1,
            },
            "include_region": True,
        }

    def test_general_search_uses_keywords_for_better_relevance(self, monkeypatch):
        provider = AudibleProvider(region="us")
        captured: dict[str, object] = {}

        def fake_make_request(endpoint, *, params, include_region):
            captured["endpoint"] = endpoint
            captured["params"] = dict(params)
            captured["include_region"] = include_region
            return [_sample_book()]

        monkeypatch.setattr(provider, "_make_request", fake_make_request)

        provider.search_paginated(
            MetadataSearchOptions(
                query="Discount Dan",
                search_type=SearchType.GENERAL,
                limit=10,
                page=1,
            )
        )

        assert captured == {
            "endpoint": "/search",
            "params": {
                "keywords": "Discount Dan",
                "products_sort_by": "Relevance",
                "limit": 10,
                "page": 0,
            },
            "include_region": True,
        }

    def test_make_request_sends_meaningful_user_agent(self, monkeypatch):
        provider = AudibleProvider(user_agent="Shelfmark Test Agent/2.0")

        class DummyResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return []

        captured: dict[str, object] = {}

        def fake_get(url, *, params, headers, timeout, verify):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            captured["timeout"] = timeout
            captured["verify"] = verify
            return DummyResponse()

        monkeypatch.setattr(provider.session, "get", fake_get)
        monkeypatch.setattr(
            "shelfmark.download.network.get_ssl_verify",
            lambda url: True,
        )

        provider._make_request("/search", params={"keywords": "Discount Dan"}, include_region=True)

        assert captured["url"] == "https://audimeta.de/search"
        assert captured["headers"] == {"User-Agent": "Shelfmark Test Agent/2.0"}
        assert captured["params"]["cache"] == "true"

    def test_search_paginated_filters_unreleased_titles(self, monkeypatch):
        provider = AudibleProvider(exclude_unreleased=True)
        future_release = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()

        monkeypatch.setattr(
            provider,
            "_make_request",
            lambda endpoint, *, params, include_region: [
                _sample_book(asin="PAST", release_date="2015-11-20T00:00:00.000+00:00"),
                _sample_book(asin="FUTURE", release_date=future_release),
            ],
        )

        result = provider.search_paginated(MetadataSearchOptions(query="harry potter", limit=20))

        assert [book.provider_id for book in result.books] == ["PAST"]

    def test_get_search_field_options_returns_deduplicated_series_suggestions(self, monkeypatch):
        provider = AudibleProvider()

        monkeypatch.setattr(
            provider,
            "_make_request",
            lambda endpoint, *, params, include_region: [
                {"asin": "SERIES1", "name": "Harry Potter", "region": "us"},
                {"asin": "SERIES1", "name": "Harry Potter", "region": "us"},
                {"asin": "SERIES2", "name": "Wizarding World", "region": None},
            ],
        )

        options = provider.get_search_field_options("series", query="harry")

        assert options == [
            {"value": "id:SERIES1", "label": "Harry Potter", "description": "US"},
            {"value": "id:SERIES2", "label": "Wizarding World"},
        ]

    def test_fetch_series_books_sorts_by_series_position(self, monkeypatch):
        provider = AudibleProvider()
        monkeypatch.setattr(
            provider,
            "_make_request",
            lambda endpoint, *, params, include_region: [
                _sample_book(asin="B3", title="Third", series_position="3"),
                _sample_book(asin="B1", title="First", series_position="1"),
                _sample_book(asin="B2", title="Second", series_position="2"),
            ],
        )

        books = provider._fetch_series_books("SERIES1", preferred_series_asin="SERIES1")

        assert [book.provider_id for book in books] == ["B1", "B2", "B3"]

    def test_series_browse_paginates_sorted_books(self, monkeypatch):
        provider = AudibleProvider()
        books = [
            BookMetadata(provider="audible", provider_id="B1", title="First", series_position=1.0),
            BookMetadata(provider="audible", provider_id="B2", title="Second", series_position=2.0),
            BookMetadata(provider="audible", provider_id="B3", title="Third", series_position=3.0),
        ]

        monkeypatch.setattr(provider, "_resolve_series", lambda query: {"asin": "SERIES1", "name": "Harry Potter"})
        monkeypatch.setattr(provider, "_fetch_series_books", lambda series_asin, preferred_series_asin=None: books)

        result = provider.search_paginated(
            MetadataSearchOptions(
                query="",
                sort=SortOrder.SERIES_ORDER,
                limit=2,
                page=1,
                fields={"series": "Harry Potter"},
            )
        )

        assert [book.provider_id for book in result.books] == ["B1", "B2"]
        assert result.total_found == 3
        assert result.has_more is True
        assert result.source_title == "Harry Potter"

    def test_series_browse_accepts_frontend_id_prefixed_series_values(self, monkeypatch):
        provider = AudibleProvider()

        monkeypatch.setattr(
            provider,
            "_fetch_series_books",
            lambda series_asin, preferred_series_asin=None: [
                BookMetadata(provider="audible", provider_id="B1", title="First", series_position=1.0),
            ],
        )

        result = provider.search_paginated(
            MetadataSearchOptions(
                query="",
                sort=SortOrder.SERIES_ORDER,
                limit=20,
                page=1,
                fields={"series": "id:SERIES1"},
            )
        )

        assert [book.provider_id for book in result.books] == ["B1"]
        assert result.source_title == "SERIES1"
