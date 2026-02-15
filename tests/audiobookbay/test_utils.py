"""
Tests for AudiobookBay utility functions.
"""

import pytest

from shelfmark.release_sources.audiobookbay.utils import parse_size, sanitize_title


class TestParseSize:
    """Tests for the parse_size function."""

    def test_parse_size_bytes(self):
        """Test parsing byte sizes."""
        assert parse_size("100 B") == 100
        assert parse_size("512 B") == 512
        assert parse_size("100B") == 100  # No space

    def test_parse_size_kilobytes(self):
        """Test parsing kilobyte sizes."""
        assert parse_size("1 KB") == 1024
        assert parse_size("2 KB") == 2048
        assert parse_size("1.5 KB") == int(1.5 * 1024)
        assert parse_size("1KBs") == 1024  # Handles "KBs" suffix

    def test_parse_size_megabytes(self):
        """Test parsing megabyte sizes."""
        assert parse_size("1 MB") == 1024 ** 2
        assert parse_size("500 MB") == 500 * (1024 ** 2)
        assert parse_size("1.5 MB") == int(1.5 * (1024 ** 2))
        assert parse_size("500.00 MBs") == int(500.00 * (1024 ** 2))  # Handles "MBs" suffix

    def test_parse_size_gigabytes(self):
        """Test parsing gigabyte sizes."""
        assert parse_size("1 GB") == 1024 ** 3
        assert parse_size("11.68 GB") == int(11.68 * (1024 ** 3))
        assert parse_size("1.01 GBs") == int(1.01 * (1024 ** 3))  # Handles "GBs" suffix

    def test_parse_size_terabytes(self):
        """Test parsing terabyte sizes."""
        assert parse_size("1 TB") == 1024 ** 4
        assert parse_size("2.5 TB") == int(2.5 * (1024 ** 4))

    def test_parse_size_case_insensitive(self):
        """Test that size parsing is case insensitive."""
        assert parse_size("1 gb") == 1024 ** 3
        assert parse_size("1 Gb") == 1024 ** 3
        assert parse_size("1 GB") == 1024 ** 3
        assert parse_size("1 gbs") == 1024 ** 3

    def test_parse_size_none(self):
        """Test that None returns None."""
        assert parse_size(None) is None

    def test_parse_size_empty_string(self):
        """Test that empty string returns None."""
        assert parse_size("") is None

    def test_parse_size_invalid_format(self):
        """Test that invalid formats return None."""
        assert parse_size("invalid") is None
        assert parse_size("123") is None  # No unit
        assert parse_size("abc MB") is None  # Invalid number

    def test_parse_size_with_whitespace(self):
        """Test parsing with various whitespace."""
        assert parse_size("  1 GB  ") == 1024 ** 3
        assert parse_size("1.5\tMB") == int(1.5 * (1024 ** 2))


class TestSanitizeTitle:
    """Tests for the sanitize_title function."""

    def test_sanitize_title_removes_invalid_chars(self):
        """Test removing invalid filename characters."""
        assert sanitize_title("Test<Book>") == "TestBook"
        assert sanitize_title("Test:Book") == "TestBook"
        assert sanitize_title("Test/Book") == "TestBook"
        assert sanitize_title("Test\\Book") == "TestBook"
        assert sanitize_title("Test|Book") == "TestBook"
        assert sanitize_title("Test?Book") == "TestBook"
        assert sanitize_title("Test*Book") == "TestBook"
        assert sanitize_title('Test"Book') == "TestBook"

    def test_sanitize_title_preserves_valid_chars(self):
        """Test that valid characters are preserved."""
        assert sanitize_title("Test Book - Author") == "Test Book - Author"
        assert sanitize_title("Test Book (2024)") == "Test Book (2024)"
        assert sanitize_title("Test Book [Special]") == "Test Book [Special]"
        assert sanitize_title("Test Book's Title") == "Test Book's Title"

    def test_sanitize_title_strips_whitespace(self):
        """Test that leading/trailing whitespace is stripped."""
        assert sanitize_title("  Test Book  ") == "Test Book"
        assert sanitize_title("\tTest Book\n") == "Test Book"

    def test_sanitize_title_empty(self):
        """Test that empty string returns empty."""
        assert sanitize_title("") == ""

    def test_sanitize_title_multiple_invalid_chars(self):
        """Test removing multiple invalid characters."""
        assert sanitize_title("Test<Book>:Author/Title") == "TestBookAuthorTitle"
