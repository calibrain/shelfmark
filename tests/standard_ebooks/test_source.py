"""Tests for StandardEbooksSource: availability, search plan formulation, and Release mapping."""

import types
from unittest.mock import patch

from shelfmark.release_sources import BrowseRecord, ReleaseProtocol
from shelfmark.release_sources.standard_ebooks import source as se_source
from shelfmark.release_sources.standard_ebooks.source import (
    StandardEbooksSource,
    _build_query_candidates,
)
from tests.standard_ebooks import sample_html as html


def _plan(manual_query=None, variants=None):
    return types.SimpleNamespace(
        manual_query=manual_query, title_variants=variants or [], author=""
    )


def _variant(title, author):
    return types.SimpleNamespace(title=title, author=author)


def _book(title):
    return types.SimpleNamespace(title=title)


class TestIsAvailable:
    def test_enabled(self):
        with patch.object(
            se_source.config,
            "get",
            side_effect=lambda k, d=None: True if k == "STANDARD_EBOOKS_ENABLED" else d,
        ):
            assert StandardEbooksSource().is_available() is True

    def test_disabled(self):
        with patch.object(
            se_source.config,
            "get",
            side_effect=lambda k, d=None: False if k == "STANDARD_EBOOKS_ENABLED" else d,
        ):
            assert StandardEbooksSource().is_available() is False

    def test_truthy_string_does_not_enable(self):
        with patch.object(
            se_source.config,
            "get",
            side_effect=lambda k, d=None: "true" if k == "STANDARD_EBOOKS_ENABLED" else d,
        ):
            assert StandardEbooksSource().is_available() is False


def test_build_query_candidates_manual_query_wins():
    plan = _plan(manual_query="  frankenstein  ")
    assert _build_query_candidates(plan, _book("ignored")) == ["frankenstein"]


def test_build_query_candidates_combined_then_title_only():
    plan = _plan(variants=[_variant("Frankenstein", "Mary Shelley")])
    assert _build_query_candidates(plan, _book("x")) == [
        "Frankenstein Mary Shelley",
        "Frankenstein",
    ]


def test_build_query_candidates_dedups_when_no_author():
    plan = _plan(variants=[_variant("Frankenstein", "")])
    assert _build_query_candidates(plan, _book("x")) == ["Frankenstein"]


def test_build_query_candidates_book_title_fallback():
    assert _build_query_candidates(_plan(), _book("Fallback Title")) == ["Fallback Title"]


def test_search_non_ebook_returns_empty():
    with patch.object(StandardEbooksSource, "is_available", return_value=True):
        result = StandardEbooksSource().search(
            _book("Frankenstein"), _plan(manual_query="Frankenstein"), content_type="audiobook"
        )
    assert result == []


def test_search_unavailable_returns_empty():
    with patch.object(StandardEbooksSource, "is_available", return_value=False):
        assert (
            StandardEbooksSource().search(_book("x"), _plan(manual_query="x")) == []
        )


def test_search_maps_records_to_releases():
    record = BrowseRecord(
        id=html.SLUG_2_SEG,
        title="Frankenstein",
        source="standard_ebooks",
        format="epub",
        language="en",
        author="Mary Shelley",
        source_url=f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}",
        download_urls=[
            f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}/downloads/mary-shelley_frankenstein.epub"
        ],
    )
    with (
        patch.object(StandardEbooksSource, "is_available", return_value=True),
        patch.object(se_source.config, "get", side_effect=lambda k, d=None: d),
        patch.object(
            se_source.scraper, "search_standard_ebooks", return_value=[record]
        ) as mock_search,
    ):
        releases = StandardEbooksSource().search(
            _book("Frankenstein"), _plan(manual_query="Frankenstein")
        )

    assert len(releases) == 1
    rel = releases[0]
    assert rel.source == "standard_ebooks"
    assert rel.source_id == f"standard_ebooks:{html.SLUG_2_SEG}"
    assert rel.title == "Frankenstein"
    assert rel.format == "epub"
    assert rel.language == "en"
    assert rel.protocol == ReleaseProtocol.HTTP
    assert rel.indexer == "Standard Ebooks"
    assert rel.content_type == "ebook"
    assert rel.download_url == record.download_urls[0]
    assert rel.info_url == record.source_url
    assert rel.extra["author"] == "Mary Shelley"
    assert rel.extra["slug"] == html.SLUG_2_SEG
    mock_search.assert_called_once()


def test_search_results_are_releases():
    assert StandardEbooksSource().search_results_are_releases() is True


def test_get_column_config():
    config = StandardEbooksSource().get_column_config()
    keys = [c.key for c in config.columns]
    assert "extra.language" in keys
    assert "format" in keys
    assert "size" in keys
