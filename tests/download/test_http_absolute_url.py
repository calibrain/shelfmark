"""Tests for resolving a scraped link against the page it came from."""


def test_get_absolute_url_keeps_a_protocol_relative_host():
    import shelfmark.download.http as http

    result = http.get_absolute_url("https://annas-archive.org/md5/abc", "//cdn.example.org/f.epub")

    assert result == "https://cdn.example.org/f.epub"


def test_get_absolute_url_resolves_a_relative_path_against_the_base():
    import shelfmark.download.http as http

    result = http.get_absolute_url("https://annas-archive.org/md5/abc", "/slow_download/abc/0/1")

    assert result == "https://annas-archive.org/slow_download/abc/0/1"
