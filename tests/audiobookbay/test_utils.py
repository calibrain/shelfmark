"""
Tests for AudiobookBay utility functions.
"""

from shelfmark.release_sources.audiobookbay.utils import normalize_search_punctuation, parse_size


class TestNormalizeSearchPunctuation:
    """Tests for the normalize_search_punctuation function."""

    def test_curly_apostrophe_becomes_ascii(self):
        """ABB matches the stored ASCII apostrophe, not the rendered curly one."""
        assert normalize_search_punctuation("The Stranger’s Wife") == "The Stranger's Wife"

    def test_all_single_quote_variants(self):
        """Every single-quote lookalike collapses to the ASCII apostrophe."""
        for variant in ("‘", "’", "‚", "‛", "′", "´", "`"):
            assert normalize_search_punctuation(f"don{variant}t") == "don't"

    def test_all_double_quote_variants(self):
        """Every double-quote lookalike collapses to the ASCII double quote."""
        for variant in ("“", "”", "„", "‟", "″"):
            assert normalize_search_punctuation(f"{variant}quoted{variant}") == '"quoted"'

    def test_all_dash_variants(self):
        """Every dash lookalike collapses to the ASCII hyphen."""
        for variant in ("‐", "‑", "‒", "–", "—", "―", "−", "﹘", "﹣", "－"):
            assert normalize_search_punctuation(f"anna{variant}lou") == "anna-lou"

    def test_ellipsis_expands_to_three_dots(self):
        """WordPress renders '...' as a single ellipsis character."""
        assert normalize_search_punctuation("And Then…") == "And Then..."

    def test_ascii_query_is_unchanged(self):
        """A query that is already ASCII passes through untouched."""
        assert normalize_search_punctuation("The Stranger's Wife") == "The Stranger's Wife"

    def test_ascii_dash_runs_are_not_collapsed(self):
        """Only characters ABB cannot have stored are rewritten."""
        assert normalize_search_punctuation("Book -- Subtitle") == "Book -- Subtitle"

    def test_other_punctuation_is_preserved(self):
        """Colons and commas carry search signal and are left alone."""
        assert normalize_search_punctuation("Weatherley: Book 3, Part 1") == (
            "Weatherley: Book 3, Part 1"
        )

    def test_empty_query(self):
        """An empty query is returned as-is."""
        assert normalize_search_punctuation("") == ""


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
        assert parse_size("1 MB") == 1024**2
        assert parse_size("500 MB") == 500 * (1024**2)
        assert parse_size("1.5 MB") == int(1.5 * (1024**2))
        assert parse_size("500.00 MBs") == int(500.00 * (1024**2))  # Handles "MBs" suffix

    def test_parse_size_gigabytes(self):
        """Test parsing gigabyte sizes."""
        assert parse_size("1 GB") == 1024**3
        assert parse_size("11.68 GB") == int(11.68 * (1024**3))
        assert parse_size("1.01 GBs") == int(1.01 * (1024**3))  # Handles "GBs" suffix

    def test_parse_size_terabytes(self):
        """Test parsing terabyte sizes."""
        assert parse_size("1 TB") == 1024**4
        assert parse_size("2.5 TB") == int(2.5 * (1024**4))

    def test_parse_size_case_insensitive(self):
        """Test that size parsing is case insensitive."""
        assert parse_size("1 gb") == 1024**3
        assert parse_size("1 Gb") == 1024**3
        assert parse_size("1 GB") == 1024**3
        assert parse_size("1 gbs") == 1024**3

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
        assert parse_size("  1 GB  ") == 1024**3
        assert parse_size("1.5\tMB") == int(1.5 * (1024**2))
