from io import BytesIO

from shelfmark.release_sources import BrowseRecord


def _book() -> BrowseRecord:
    return BrowseRecord(
        id="abc123",
        title="Test Book",
        source="direct_download",
        size="20 KB",
    )


def _enable_welib_only(monkeypatch, dd):
    monkeypatch.setattr(dd, "_get_source_priority", lambda: [{"id": "welib", "enabled": True}])
    monkeypatch.setattr(dd, "_is_source_enabled", lambda source_id: source_id == "welib")
    monkeypatch.setattr(dd.config, "USE_CF_BYPASS", True)
    monkeypatch.setattr(
        "shelfmark.core.mirrors.get_welib_url_template",
        lambda: "https://welib.example/md5/{md5}",
    )
    monkeypatch.setattr(
        "shelfmark.core.mirrors.get_welib_mirrors",
        lambda: ["https://welib.example"],
    )
    monkeypatch.setattr("shelfmark.core.mirrors.get_aa_mirrors", lambda: [])
    monkeypatch.setattr(dd.network, "get_aa_base_url", lambda: "https://annas.example")


def test_welib_rejects_hostile_returned_url_before_fetch(monkeypatch, tmp_path):
    import shelfmark.release_sources.direct_download as dd

    _enable_welib_only(monkeypatch, dd)
    fetched_urls: list[str] = []

    def fake_html_get_page(url: str, **_kwargs):
        fetched_urls.append(url)
        if url == "https://welib.example/md5/abc123":
            return '<a href="http://169.254.169.254/slow_download/abc123">Download</a>'
        raise AssertionError(f"unexpected fetch: {url}")

    def unexpected_download(*_args, **_kwargs):
        raise AssertionError("hostile URL must not reach file download")

    monkeypatch.setattr(dd.downloader, "html_get_page", fake_html_get_page)
    monkeypatch.setattr(dd.downloader, "download_url", unexpected_download)

    result = dd._download_book(_book(), tmp_path / "book.epub")

    assert result is None
    assert fetched_urls == ["https://welib.example/md5/abc123"]


def test_welib_allows_configured_host_returned_url(monkeypatch, tmp_path):
    import shelfmark.release_sources.direct_download as dd

    _enable_welib_only(monkeypatch, dd)
    fetched_pages: list[str] = []
    downloaded: list[tuple[str, str | None]] = []

    def fake_html_get_page(url: str, **_kwargs):
        fetched_pages.append(url)
        if url == "https://welib.example/md5/abc123":
            return '<a href="/files/book.epub">Download</a>'
        raise AssertionError(f"unexpected fetch: {url}")

    def fake_download_url(url: str, *_args, referer: str | None = None, **_kwargs):
        downloaded.append((url, referer))
        payload = BytesIO(b"x" * (11 * 1024))
        payload.seek(0, 2)
        return payload

    book_path = tmp_path / "book.epub"

    monkeypatch.setattr(dd.downloader, "html_get_page", fake_html_get_page)
    monkeypatch.setattr(dd.downloader, "download_url", fake_download_url)

    result = dd._download_book(_book(), book_path)

    assert result == "https://welib.example/files/book.epub"
    assert fetched_pages == ["https://welib.example/md5/abc123"]
    assert downloaded == [
        ("https://welib.example/files/book.epub", "https://welib.example/md5/abc123")
    ]
    assert book_path.read_bytes() == b"x" * (11 * 1024)
