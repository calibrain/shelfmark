"""
Tests for the Prowlarr source module.

Tests the utility functions for parsing release metadata.
"""

import pytest

# Import the functions to test
from shelfmark.release_sources.prowlarr.source import (
    ProwlarrSource,
    _parse_size,
    _extract_format,
    _detect_content_type_from_categories,
)
from shelfmark.release_sources.prowlarr.utils import get_protocol_display, sanitize_download_url
from shelfmark.metadata_providers import BookMetadata


class TestParseSize:
    """Tests for the _parse_size function."""

    def test_parse_size_bytes(self):
        """Test parsing small byte sizes."""
        assert _parse_size(100) == "100 B"
        assert _parse_size(512) == "512 B"

    def test_parse_size_kilobytes(self):
        """Test parsing kilobyte sizes."""
        assert _parse_size(1024) == "1.0 KB"
        assert _parse_size(2048) == "2.0 KB"
        assert _parse_size(1536) == "1.5 KB"

    def test_parse_size_megabytes(self):
        """Test parsing megabyte sizes."""
        assert _parse_size(1048576) == "1.0 MB"
        assert _parse_size(5242880) == "5.0 MB"
        assert _parse_size(1572864) == "1.5 MB"

    def test_parse_size_gigabytes(self):
        """Test parsing gigabyte sizes."""
        assert _parse_size(1073741824) == "1.0 GB"
        assert _parse_size(2147483648) == "2.0 GB"

    def test_parse_size_terabytes(self):
        """Test parsing terabyte sizes."""
        assert _parse_size(1099511627776) == "1.0 TB"

    def test_parse_size_none(self):
        """Test that None returns None."""
        assert _parse_size(None) is None

    def test_parse_size_zero(self):
        """Test that zero returns None."""
        assert _parse_size(0) is None

    def test_parse_size_negative(self):
        """Test that negative values return None."""
        assert _parse_size(-100) is None


class TestExtractFormat:
    """Tests for the _extract_format function."""

    def test_extract_format_from_extension(self):
        """Test extracting format from file extension."""
        assert _extract_format("The Book.epub") == "epub"
        assert _extract_format("The Book.mobi") == "mobi"
        assert _extract_format("The Book.pdf") == "pdf"
        assert _extract_format("The Book.azw3") == "azw3"

    def test_extract_format_from_brackets(self):
        """Test extracting format from brackets."""
        assert _extract_format("The Book [EPUB]") == "epub"
        assert _extract_format("The Book (PDF)") == "pdf"
        assert _extract_format("The Book {MOBI}") == "mobi"

    def test_extract_format_from_word(self):
        """Test extracting format as standalone word."""
        assert _extract_format("The Book epub version") == "epub"
        assert _extract_format("mobi edition of the book") == "mobi"

    def test_extract_format_priority_extension_over_bracket(self):
        """Test that file extension takes priority over brackets."""
        # Extension is more reliable
        assert _extract_format("The Book [PDF].epub") == "epub"

    def test_extract_format_case_insensitive(self):
        """Test that format extraction is case insensitive."""
        assert _extract_format("The Book.EPUB") == "epub"
        assert _extract_format("The Book [PDF]") == "pdf"
        assert _extract_format("The Book.Mobi") == "mobi"

    def test_extract_format_none_when_no_format(self):
        """Test that None is returned when no format found."""
        assert _extract_format("The Book by Author") is None
        assert _extract_format("") is None

    def test_extract_format_cbz_cbr(self):
        """Test comic book formats."""
        assert _extract_format("Comic Issue 1.cbz") == "cbz"
        assert _extract_format("Comic Issue 2.cbr") == "cbr"

    def test_extract_format_fb2(self):
        """Test FB2 format (common in Russian ebooks)."""
        assert _extract_format("Russian Book.fb2") == "fb2"
        assert _extract_format("Book [FB2]") == "fb2"

    def test_extract_format_djvu(self):
        """Test DjVu format."""
        assert _extract_format("Scanned Book.djvu") == "djvu"

    def test_extract_format_avoids_false_positives(self):
        """Test that format extraction doesn't match partial words."""
        # "republic" should not match "pdf" or other formats
        assert _extract_format("The Republic by Plato") is None
        # "literal" should not match "lit"
        assert _extract_format("Literal Translation") is None


class TestGetProtocolDisplay:
    """Tests for the get_protocol_display function."""

    def test_get_protocol_from_protocol_field_torrent(self):
        """Test extracting torrent protocol from protocol field."""
        result = {"protocol": "torrent", "downloadUrl": "https://example.com"}
        assert get_protocol_display(result) == "torrent"

    def test_get_protocol_from_protocol_field_usenet(self):
        """Test extracting usenet protocol from protocol field."""
        result = {"protocol": "usenet", "downloadUrl": "https://example.com"}
        assert get_protocol_display(result) == "nzb"

    def test_get_protocol_from_magnet_url(self):
        """Test inferring torrent from magnet URL."""
        result = {"downloadUrl": "magnet:?xt=urn:btih:abc123"}
        assert get_protocol_display(result) == "torrent"

    def test_get_protocol_from_torrent_url(self):
        """Test inferring torrent from .torrent URL."""
        result = {"downloadUrl": "https://example.com/file.torrent"}
        assert get_protocol_display(result) == "torrent"

    def test_get_protocol_from_nzb_url(self):
        """Test inferring NZB from .nzb URL."""
        result = {"downloadUrl": "https://example.com/file.nzb"}
        assert get_protocol_display(result) == "nzb"

    def test_get_protocol_fallback_to_magnet_url(self):
        """Test fallback to magnetUrl field."""
        result = {"magnetUrl": "magnet:?xt=urn:btih:abc123"}
        assert get_protocol_display(result) == "torrent"

    def test_get_protocol_unknown(self):
        """Test unknown protocol for unclear URLs."""
        result = {"downloadUrl": "https://example.com/download"}
        assert get_protocol_display(result) == "unknown"

    def test_get_protocol_case_insensitive(self):
        """Test protocol detection is case insensitive."""
        result = {"protocol": "TORRENT"}
        assert get_protocol_display(result) == "torrent"

        result = {"protocol": "Usenet"}
        assert get_protocol_display(result) == "nzb"


class TestSanitizeDownloadUrl:
    """Tests for the sanitize_download_url helper."""

    def test_sanitizes_apikey_whitespace(self):
        """Strip whitespace around apikey separators."""
        url = "http://prowlarr:9696/5/download?apikey = 12345"
        assert sanitize_download_url(url) == "http://prowlarr:9696/5/download?apikey=12345"

    def test_sanitizes_multiple_query_params(self):
        """Sanitize all query pairs while keeping params."""
        url = "http://prowlarr:9696/5/download?apikey = 12345&indexer = 7"
        assert sanitize_download_url(url) == "http://prowlarr:9696/5/download?apikey=12345&indexer=7"

    def test_leaves_non_http_urls_untouched(self):
        """Do not modify magnet or other non-http URLs."""
        url = "magnet:?xt=urn:btih:abc123"
        assert sanitize_download_url(url) == url

    def test_leaves_clean_urls_untouched(self):
        """Return clean URLs as-is."""
        url = "https://prowlarr:9696/5/download?apikey=12345"
        assert sanitize_download_url(url) == url

class TestDetectContentType:
    """Tests for the _detect_content_type_from_categories function."""

    def test_fallback_without_categories(self):
        assert _detect_content_type_from_categories([], "ebook") == "book"
        assert _detect_content_type_from_categories([], "audiobook") == "audiobook"

    def test_audiobook_categories(self):
        assert _detect_content_type_from_categories([{"id": 3030}], "ebook") == "audiobook"
        assert _detect_content_type_from_categories([3000], "ebook") == "audiobook"

    def test_book_category_range(self):
        assert _detect_content_type_from_categories([{"id": 7000}], "ebook") == "book"
        assert _detect_content_type_from_categories([7020], "audiobook") == "book"
        assert _detect_content_type_from_categories([7030], "ebook") == "book"

    def test_non_book_categories_return_other(self):
        assert _detect_content_type_from_categories([{"id": 2000}], "ebook") == "other"


class TestProwlarrLocalizedQueries:
    def test_manual_query_still_applies_content_type_categories(self, monkeypatch):
        class FakeClient:
            def __init__(self):
                self.calls: list[tuple[str, object]] = []

            def search(self, query: str, indexer_ids=None, categories=None):
                self.calls.append((query, categories))
                return []

            def get_enriched_indexer_ids(self, restrict_to=None):
                return []

        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        fake_client = FakeClient()
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Anything",
            authors=["Someone"],
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en"], manual_query="my custom")
        source.search(book, plan, content_type="audiobook")

        assert fake_client.calls == [("my custom", [3030])]

    def test_manual_query_expand_removes_categories(self, monkeypatch):
        class FakeClient:
            def __init__(self):
                self.calls: list[tuple[str, object]] = []

            def search(self, query: str, indexer_ids=None, categories=None):
                self.calls.append((query, categories))
                return []

            def get_enriched_indexer_ids(self, restrict_to=None):
                return []

        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        fake_client = FakeClient()
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Anything",
            authors=["Someone"],
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en"], manual_query="my custom")
        source.search(book, plan, expand_search=True, content_type="audiobook")

        assert fake_client.calls == [("my custom", None)]

    def test_search_uses_localized_titles_when_available(self, monkeypatch):
        class FakeClient:
            def __init__(self):
                self.queries: list[str] = []

            def search(self, query: str, indexer_ids=None, categories=None):
                self.queries.append(query)
                return []

            def get_enriched_indexer_ids(self, restrict_to=None):
                return []

        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        fake_client = FakeClient()
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="219252",
            title="The Lightning Thief",
            authors=["Rick Riordan"],
            titles_by_language={"hu": "A villámtolvaj"},
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en", "hu"])
        source.search(book, plan, content_type="ebook")

        assert "The Lightning Thief" in fake_client.queries
        assert "A villámtolvaj" in fake_client.queries
        assert len(fake_client.queries) == 2

    def test_search_does_not_override_search_title_for_english(self, monkeypatch):
        class FakeClient:
            def __init__(self):
                self.queries: list[str] = []

            def search(self, query: str, indexer_ids=None, categories=None):
                self.queries.append(query)
                return []

            def get_enriched_indexer_ids(self, restrict_to=None):
                return []

        import shelfmark.release_sources.prowlarr.source as prowlarr_source

        def fake_get(key: str, default=None):
            values = {
                "PROWLARR_INDEXERS": "",
                "PROWLARR_AUTO_EXPAND": False,
            }
            return values.get(key, default)

        monkeypatch.setattr(prowlarr_source.config, "get", fake_get)

        fake_client = FakeClient()
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: fake_client)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        from shelfmark.core.search_plan import build_release_search_plan

        plan = build_release_search_plan(book, languages=["en", "hu"])
        source.search(book, plan, content_type="ebook")

        assert "The Final Empire" in fake_client.queries
        assert "A végső birodalom" in fake_client.queries
        assert "Mistborn: The Final Empire" not in fake_client.queries
