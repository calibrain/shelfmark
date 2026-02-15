"""
Tests for AudiobookBay release source.
"""

from unittest.mock import Mock, patch
import pytest

from shelfmark.metadata_providers import BookMetadata
from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.release_sources.audiobookbay.source import (
    AudiobookBaySource,
    _map_language,
    _generate_source_id,
)


class TestMapLanguage:
    """Tests for the _map_language function."""

    def test_map_language_english(self):
        """Test mapping English language."""
        assert _map_language("English") == "en"
        assert _map_language("english") == "en"
        assert _map_language("ENGLISH") == "en"

    def test_map_language_spanish(self):
        """Test mapping Spanish language."""
        assert _map_language("Spanish") == "es"
        assert _map_language("spanish") == "es"

    def test_map_language_french(self):
        """Test mapping French language."""
        assert _map_language("French") == "fr"
        assert _map_language("french") == "fr"

    def test_map_language_german(self):
        """Test mapping German language."""
        assert _map_language("German") == "de"
        assert _map_language("german") == "de"

    def test_map_language_unknown(self):
        """Test mapping unknown language returns lowercase."""
        assert _map_language("UnknownLanguage") == "unknownlanguage"
        assert _map_language("Klingon") == "klingon"

    def test_map_language_empty(self):
        """Test empty language returns None."""
        assert _map_language("") is None
        assert _map_language(None) is None

    def test_map_language_with_whitespace(self):
        """Test language with whitespace is trimmed."""
        assert _map_language("  English  ") == "en"
        assert _map_language("\tFrench\n") == "fr"


class TestGenerateSourceId:
    """Tests for the _generate_source_id function."""

    def test_generate_source_id_consistent(self):
        """Test that same URL generates same ID."""
        url = "https://audiobookbay.lu/abss/test-book/"
        id1 = _generate_source_id(url)
        id2 = _generate_source_id(url)
        assert id1 == id2
        assert len(id1) == 32  # MD5 hex digest length

    def test_generate_source_id_different_urls(self):
        """Test that different URLs generate different IDs."""
        url1 = "https://audiobookbay.lu/abss/book1/"
        url2 = "https://audiobookbay.lu/abss/book2/"
        id1 = _generate_source_id(url1)
        id2 = _generate_source_id(url2)
        assert id1 != id2


class TestAudiobookBaySource:
    """Tests for the AudiobookBaySource class."""

    def test_is_available_enabled(self, monkeypatch):
        """Test is_available when enabled."""
        def mock_get(key, default=False):
            if key == "ABB_ENABLED":
                return True
            return default
        
        import shelfmark.release_sources.audiobookbay.source as source_module
        monkeypatch.setattr(source_module.config, "get", mock_get)
        
        source = AudiobookBaySource()
        assert source.is_available() is True

    def test_is_available_disabled(self, monkeypatch):
        """Test is_available when disabled."""
        def mock_get(key, default=False):
            if key == "ABB_ENABLED":
                return False
            return default
        
        import shelfmark.release_sources.audiobookbay.source as source_module
        monkeypatch.setattr(source_module.config, "get", mock_get)
        
        source = AudiobookBaySource()
        assert source.is_available() is False

    def test_search_non_audiobook_content_type(self):
        """Test that non-audiobook content types return empty."""
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )
        
        results = source.search(book, plan, content_type="ebook")
        assert results == []

    @patch('shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay')
    def test_search_query_generation_manual(self, mock_search):
        """Test search with manual query."""
        mock_search.return_value = []
        
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
            manual_query="custom search query",
        )
        
        source.search(book, plan, content_type="audiobook")
        
        mock_search.assert_called_once()
        call_args = mock_search.call_args
        assert call_args.kwargs['query'] == "custom search query"

    @patch('shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay')
    def test_search_query_generation_from_variants(self, mock_search):
        """Test search query generation from title variants."""
        mock_search.return_value = []
        
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )
        
        source.search(book, plan, content_type="audiobook")
        
        mock_search.assert_called_once()
        call_args = mock_search.call_args
        assert call_args.kwargs['query'] == "test book test author"

    @patch('shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay')
    def test_search_query_generation_from_title_only(self, mock_search):
        """Test search query generation when only title available."""
        mock_search.return_value = []
        
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=[],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="",
            title_variants=[],
            grouped_title_variants=[],
        )
        
        source.search(book, plan, content_type="audiobook")
        
        mock_search.assert_called_once()
        call_args = mock_search.call_args
        assert call_args.kwargs['query'] == "test book"

    @patch('shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay')
    def test_search_empty_query(self, mock_search):
        """Test search with empty query returns empty."""
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="",
            authors=[],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="",
            title_variants=[],
            grouped_title_variants=[],
        )
        
        results = source.search(book, plan, content_type="audiobook")
        
        assert results == []
        mock_search.assert_not_called()

    @patch('shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay')
    def test_search_relevance_filtering(self, mock_search):
        """Test that irrelevant results are filtered out."""
        mock_search.return_value = [
            {
                'title': 'Test Book by Test Author',
                'link': 'https://audiobookbay.lu/abss/test-book/',
                'format': 'M4B',
                'size': '500 MB',
                'language': 'English',
            },
            {
                'title': 'Something Completely Different',
                'link': 'https://audiobookbay.lu/abss/unrelated/',
                'format': 'MP3',
                'size': '1 GB',
                'language': 'English',
            },
        ]
        
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )
        
        results = source.search(book, plan, content_type="audiobook")
        
        # Should filter out "Unrelated Book Title" as it doesn't contain query words
        assert len(results) == 1
        assert results[0].title == 'Test Book by Test Author'

    @patch('shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay')
    def test_search_result_mapping(self, mock_search):
        """Test conversion of scraper results to Release objects."""
        mock_search.return_value = [
            {
                'title': 'Test Book - Test Author',
                'link': 'https://audiobookbay.lu/abss/test-book/',
                'format': 'M4B',
                'size': '500.00 MBs',
                'language': 'English',
                'bitrate': '128 Kbps',
                'posted_date': '01 Jan 2024',
                'cover': 'https://example.com/cover.jpg',
            },
        ]
        
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )
        
        results = source.search(book, plan, content_type="audiobook")
        
        assert len(results) == 1
        release = results[0]
        assert release.source == "audiobookbay"
        assert release.title == "Test Book - Test Author"
        assert release.format == "m4b"
        assert release.language == "en"
        assert release.size == "500.00 MBs"
        assert release.download_url == "https://audiobookbay.lu/abss/test-book/"
        assert release.info_url == "https://audiobookbay.lu/abss/test-book/"
        assert release.protocol.value == "torrent"
        assert release.indexer == "AudiobookBay"
        assert release.content_type == "audiobook"
        assert release.extra['preview'] == "https://example.com/cover.jpg"
        assert release.extra['detail_url'] == "https://audiobookbay.lu/abss/test-book/"
        assert release.extra['bitrate'] == "128 Kbps"
        assert release.extra['posted_date'] == "01 Jan 2024"
        assert release.extra['language_raw'] == "English"

    @patch('shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay')
    def test_search_handles_scraper_exception(self, mock_search):
        """Test that scraper exceptions are handled gracefully."""
        mock_search.side_effect = Exception("Scraper error")
        
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )
        
        results = source.search(book, plan, content_type="audiobook")
        
        assert results == []

    @patch('shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay')
    def test_search_handles_invalid_result(self, mock_search):
        """Test that invalid results are skipped."""
        mock_search.return_value = [
            {
                'title': 'Relevant Book',
                'link': 'https://audiobookbay.lu/abss/valid/',
                'format': 'M4B',
            },
            {
                # Missing required fields
                'title': 'Relevant But Invalid',
            },
        ]
        
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Relevant",
            authors=["Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Author",
            title_variants=[ReleaseSearchVariant(title="Relevant", author="Author")],
            grouped_title_variants=[],
        )
        
        results = source.search(book, plan, content_type="audiobook")
        
        # Should only include valid result
        assert len(results) == 1
        assert results[0].title == "Relevant Book"

    @patch('shelfmark.release_sources.audiobookbay.source.scraper.search_audiobookbay')
    def test_search_config_hostname(self, mock_search, monkeypatch):
        """Test that custom hostname is used from config."""
        mock_search.return_value = []
        
        def mock_get(key, default=None):
            if key == "ABB_HOSTNAME":
                return "audiobookbay.is"
            if key == "ABB_PAGE_LIMIT":
                return 3
            return default
        
        import shelfmark.release_sources.audiobookbay.source as source_module
        monkeypatch.setattr(source_module.config, "get", mock_get)
        
        source = AudiobookBaySource()
        book = BookMetadata(
            provider="test",
            provider_id="123",
            title="Test Book",
            authors=["Test Author"],
        )
        plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Test Author",
            title_variants=[ReleaseSearchVariant(title="Test Book", author="Test Author")],
            grouped_title_variants=[],
        )
        
        source.search(book, plan, content_type="audiobook")
        
        call_args = mock_search.call_args
        assert call_args.kwargs['hostname'] == "audiobookbay.is"
        assert call_args.kwargs['max_pages'] == 3

    def test_get_column_config(self):
        """Test column configuration."""
        source = AudiobookBaySource()
        config = source.get_column_config()
        
        assert config is not None
        assert len(config.columns) == 3
        column_keys = [col.key for col in config.columns]
        assert "language" in column_keys
        assert "format" in column_keys
        assert "size" in column_keys
        assert "seeders" not in column_keys  # ABB doesn't show seeders
        assert config.supported_filters == ["format", "language"]
