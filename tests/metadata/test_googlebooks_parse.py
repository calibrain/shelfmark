import requests

from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import MetadataSearchOptions
from shelfmark.metadata_providers.googlebooks import GoogleBooksProvider


class _GoogleBooksResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FlakyGoogleBooksSession:
    def __init__(self):
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise requests.Timeout
        return _GoogleBooksResponse(
            {
                "items": [
                    {
                        "id": "volume-1",
                        "volumeInfo": {
                            "title": "Recovered Book",
                            "authors": ["Alice Author"],
                        },
                    }
                ]
            }
        )


def test_googlebooks_search_does_not_cache_request_failures():
    get_metadata_cache().clear()
    provider = GoogleBooksProvider(api_key="test-key")
    session = _FlakyGoogleBooksSession()
    provider.session = session
    options = MetadataSearchOptions(query="Recovered Book")

    assert provider.search(options) == []

    result = provider.search(options)

    assert session.calls == 2
    assert [book.title for book in result] == ["Recovered Book"]


class TestGoogleBooksParseVolume:
    def test_parse_volume_returns_metadata_for_valid_payload(self):
        provider = GoogleBooksProvider(api_key="test-key")

        result = provider._parse_volume(
            {
                "id": "volume-1",
                "volumeInfo": {
                    "title": "Test Book",
                    "authors": ["Alice Author"],
                    "industryIdentifiers": [
                        {"type": "ISBN_10", "identifier": "1234567890"},
                        {"type": "ISBN_13", "identifier": "9781234567897"},
                    ],
                    "imageLinks": {
                        "thumbnail": "http://example.com/cover.jpg&edge=curl",
                    },
                    "publisher": "Test Publisher",
                    "publishedDate": "2024-03-01",
                    "language": "en",
                    "categories": ["Fiction", "Fantasy"],
                    "description": "A book.",
                    "infoLink": "https://example.com/books/volume-1",
                    "averageRating": 4.2,
                    "ratingsCount": 1200,
                },
            }
        )

        assert result is not None
        assert result.provider_id == "volume-1"
        assert result.title == "Test Book"
        assert result.authors == ["Alice Author"]
        assert result.isbn_10 == "1234567890"
        assert result.isbn_13 == "9781234567897"
        assert result.cover_url == "https://example.com/cover.jpg"
        assert result.publish_year == 2024
        assert result.display_fields[0].value == "4.2 (1,200)"

    def test_parse_volume_returns_none_for_malformed_rating_payload(self):
        provider = GoogleBooksProvider(api_key="test-key")

        result = provider._parse_volume(
            {
                "id": "volume-2",
                "volumeInfo": {
                    "title": "Broken Book",
                    "averageRating": "not-a-number",
                },
            }
        )

        assert result is None
