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


class _RecordingGoogleBooksSession:
    def __init__(self):
        self.params = []

    def get(self, *args, **kwargs):
        self.params.append(dict(kwargs["params"]))
        return _GoogleBooksResponse({"items": []})


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


def test_googlebooks_paginated_search_does_not_cache_request_failures():
    get_metadata_cache().clear()
    provider = GoogleBooksProvider(api_key="test-key")
    session = _FlakyGoogleBooksSession()
    provider.session = session
    options = MetadataSearchOptions(query="Recovered Book")

    assert provider.search_paginated(options).books == []

    result = provider.search_paginated(options)

    assert session.calls == 2
    assert [book.title for book in result.books] == ["Recovered Book"]


def test_googlebooks_isbn_lookup_is_cached():
    get_metadata_cache().clear()
    provider = GoogleBooksProvider(api_key="test-key")
    session = _FlakyGoogleBooksSession()
    session.calls = 1  # skip the simulated timeout
    provider.session = session

    first = provider.search_by_isbn("978-0-00-000000-2")
    second = provider.search_by_isbn("978-0-00-000000-2")

    assert first is not None
    assert second == first
    assert session.calls == 2


def test_googlebooks_pages_tile_when_limit_exceeds_api_maximum():
    get_metadata_cache().clear()
    provider = GoogleBooksProvider(api_key="test-key")
    session = _RecordingGoogleBooksSession()
    provider.session = session

    for page in (1, 2):
        provider.search(MetadataSearchOptions(query="Dune", limit=50, page=page))

    first, second = session.params
    assert first["maxResults"] == 40
    assert first["startIndex"] == 0
    assert second["startIndex"] == first["startIndex"] + first["maxResults"]


def test_googlebooks_page_stride_follows_limit_below_api_maximum():
    get_metadata_cache().clear()
    provider = GoogleBooksProvider(api_key="test-key")
    session = _RecordingGoogleBooksSession()
    provider.session = session

    provider.search(MetadataSearchOptions(query="Dune", limit=25, page=3))

    assert session.params[0]["maxResults"] == 25
    assert session.params[0]["startIndex"] == 50


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

    @staticmethod
    def _description_of(description):
        provider = GoogleBooksProvider(api_key="test-key")
        result = provider._parse_volume(
            {
                "id": "volume-3",
                "volumeInfo": {"title": "Described Book", "description": description},
            }
        )
        assert result is not None
        return result.description

    def test_parse_volume_turns_html_description_into_plain_text(self):
        """Google Books sends HTML and the UI renders it as text: users saw a literal <p>."""
        description = self._description_of(
            "<p>First paragraph.</p><p>Second, with <b>bold</b> &amp; <i>italic</i>."
            "<br>A new line.</p>"
        )

        assert description == "First paragraph.\n\nSecond, with bold & italic.\nA new line."

    def test_parse_volume_keeps_a_line_break_written_with_attributes(self):
        """A `<br class="x">` was dropped as a plain tag and joined the lines ("FirstSecond")."""
        assert self._description_of('First<br class="x">Second') == "First\nSecond"

    def test_parse_volume_keeps_an_uppercase_self_closing_line_break(self):
        """Tag names are case-insensitive in HTML, and `<BR/>` is common in older blurbs."""
        assert self._description_of("First<BR/>Second") == "First\nSecond"

    def test_parse_volume_keeps_angle_brackets_that_are_not_tags(self):
        """Only something shaped like a tag is removed, never text around a < or >.

        A looser `<[^>]+>` would eat "< 3 and a >" here along with the real tags.
        """
        assert self._description_of("<p>2 < 3 and a > b</p>") == "2 < 3 and a > b"

    def test_parse_volume_reports_an_empty_description_as_missing(self):
        """A description with nothing left to show must not render as a blank block."""
        assert self._description_of("") is None
        assert self._description_of("<p> </p><br/>") is None


class _FullPageSession:
    def __init__(self, count):
        self.count = count

    def get(self, *args, **kwargs):
        items = [
            {"id": f"v{i}", "volumeInfo": {"title": f"Book {i}", "authors": ["Alice Author"]}}
            for i in range(self.count)
        ]
        return _GoogleBooksResponse({"items": items})


def test_googlebooks_reports_more_pages_when_limit_exceeds_api_maximum():
    get_metadata_cache().clear()
    provider = GoogleBooksProvider(api_key="test-key")
    provider.session = _FullPageSession(40)

    result = provider.search_paginated(MetadataSearchOptions(query="Dune", limit=100))

    assert len(result.books) == 40
    assert result.has_more is True


def test_googlebooks_reports_no_more_pages_after_a_short_page():
    get_metadata_cache().clear()
    provider = GoogleBooksProvider(api_key="test-key")
    provider.session = _FullPageSession(12)

    result = provider.search_paginated(MetadataSearchOptions(query="Dune", limit=100))

    assert result.has_more is False
