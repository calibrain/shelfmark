"""Tests for Standard Ebooks scraper: URL construction, HTML parsing, and download resolution."""

from unittest.mock import MagicMock, patch

from shelfmark.release_sources.standard_ebooks import scraper
from tests.standard_ebooks import sample_html as html


def test_build_epub_url_two_segment():
    url = scraper.build_epub_url(html.SLUG_2_SEG)
    expected = (
        "https://standardebooks.org/ebooks/mary-shelley/frankenstein/downloads/"
        "mary-shelley_frankenstein.epub"
    )
    assert url == expected


def test_build_epub_url_three_segment():
    url = scraper.build_epub_url(html.SLUG_3_SEG)
    expected = (
        "https://standardebooks.org/ebooks/maurice-leblanc/the-hollow-needle/"
        "alexander-teixeira-de-mattos/downloads/"
        "maurice-leblanc_the-hollow-needle_alexander-teixeira-de-mattos.epub"
    )
    assert url == expected


def test_build_epub_url_from_full_url():
    full_url = f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}/"
    url = scraper.build_epub_url(full_url)
    expected = (
        "https://standardebooks.org/ebooks/mary-shelley/frankenstein/downloads/"
        "mary-shelley_frankenstein.epub"
    )
    assert url == expected


def test_build_epub_url_strips_existing_downloads_path():
    url_with_downloads = f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}/downloads/other.epub"
    url = scraper.build_epub_url(url_with_downloads)
    expected = (
        "https://standardebooks.org/ebooks/mary-shelley/frankenstein/downloads/"
        "mary-shelley_frankenstein.epub"
    )
    assert url == expected


def test_parse_results_extracts_records():
    records = scraper._parse_results(html.SEARCH_HTML, "https://standardebooks.org")
    assert len(records) == 3

    # First record: 2-segment
    r1 = records[0]
    assert r1.id == html.SLUG_2_SEG
    assert r1.title == "Frankenstein"
    assert r1.author == "Mary Shelley"
    assert r1.format == "epub"
    assert r1.language == "en"
    assert r1.preview == f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}/cover-thumbnail.jpg"
    assert r1.source_url == f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}"
    assert r1.download_urls == [
        f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}/downloads/mary-shelley_frankenstein.epub"
    ]

    # Second record: 3-segment
    r2 = records[1]
    assert r2.id == html.SLUG_3_SEG
    assert r2.title == "The Hollow Needle"
    assert r2.author == "Maurice Leblanc"
    assert r2.download_urls == [
        f"https://standardebooks.org/ebooks/{html.SLUG_3_SEG}/downloads/maurice-leblanc_the-hollow-needle_alexander-teixeira-de-mattos.epub"
    ]

    # Third record: missing author, fallback title
    r3 = records[2]
    assert r3.id == "anonymous/beowulf"
    assert r3.title == "Beowulf"
    assert r3.author is None


def test_parse_results_respects_max_results():
    records = scraper._parse_results(html.SEARCH_HTML, "https://standardebooks.org", max_results=2)
    assert len(records) == 2
    assert records[0].id == html.SLUG_2_SEG
    assert records[1].id == html.SLUG_3_SEG


def test_parse_results_empty_list():
    records = scraper._parse_results(html.EMPTY_SEARCH_HTML, "https://standardebooks.org")
    assert records == []


def test_parse_results_no_container():
    records = scraper._parse_results(html.NO_CONTAINER_HTML, "https://standardebooks.org")
    assert records == []


def test_resolve_download_url_prefers_compatible_epub():
    page_url = f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}"
    resolved = scraper.resolve_download_url(html.BOOK_PAGE_HTML, page_url)
    expected = (
        f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}/downloads/"
        "mary-shelley_frankenstein.epub"
    )
    assert resolved == expected


def test_resolve_download_url_falls_back_to_advanced_if_no_compatible():
    page_url = f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}"
    resolved = scraper.resolve_download_url(html.BOOK_PAGE_ADVANCED_ONLY_HTML, page_url)
    expected = (
        f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}/downloads/"
        "mary-shelley_frankenstein_advanced.epub"
    )
    assert resolved == expected


def test_resolve_download_url_returns_none_if_no_epub():
    page_url = f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}"
    resolved = scraper.resolve_download_url(html.BOOK_PAGE_NO_EPUB_HTML, page_url)
    assert resolved is None


def test_search_standard_ebooks_empty_query():
    assert scraper.search_standard_ebooks("   ") == []


def test_fetch_page_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html>ok</html>"
    with patch("requests.get", return_value=mock_resp):
        res = scraper.fetch_page("https://standardebooks.org/ebooks")
    assert res == "<html>ok</html>"


def test_fetch_page_http_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("requests.get", return_value=mock_resp):
        res = scraper.fetch_page("https://standardebooks.org/notfound")
    assert res is None
