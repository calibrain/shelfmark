import pytest

from shelfmark.metadata_providers.hardcover import _compute_search_title


class TestHardcoverComputeSearchTitle:
    def test_prefers_subtitle_when_title_contains_subtitle(self):
        assert (
            _compute_search_title("Mistborn: The Final Empire", "The Final Empire")
            == "The Final Empire"
        )

    def test_does_not_use_subtitle_when_it_looks_like_series_position(self):
        assert _compute_search_title("The Stormlight Archive: Book 1", "Book 1") is None
        assert _compute_search_title("Some Series: Volume II", "Volume II") is None

    def test_strips_series_prefix_when_series_name_available(self):
        assert (
            _compute_search_title("Mistborn: The Final Empire", None, series_name="Mistborn")
            == "The Final Empire"
        )

    def test_strips_parenthetical_suffix(self):
        assert _compute_search_title("The Martian (Unabridged)", None) == "The Martian"
        assert _compute_search_title("The Martian", None) is None

    def test_returns_none_when_no_useful_simplification(self):
        assert _compute_search_title("Dune", None) is None
