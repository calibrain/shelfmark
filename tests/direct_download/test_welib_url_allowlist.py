from io import BytesIO

import pytest
import requests

from shelfmark.release_sources import BrowseRecord


def _book() -> BrowseRecord:
    return BrowseRecord(
        id="abc123",
        title="Test Book",
        source="direct_download",
        size="20 KB",
    )


def _enable_welib_only(
    monkeypatch,
    dd,
    *,
    template: str = "https://welib.example/md5/{md5}",
    mirrors: list[str] | None = None,
):
    monkeypatch.setattr(dd, "_get_source_priority", lambda: [{"id": "welib", "enabled": True}])
    monkeypatch.setattr(dd, "_is_source_enabled", lambda source_id: source_id == "welib")
    monkeypatch.setattr(dd.config, "USE_CF_BYPASS", True)
    monkeypatch.setattr(
        "shelfmark.core.mirrors.get_welib_url_template",
        lambda: template,
    )
    monkeypatch.setattr(
        "shelfmark.core.mirrors.get_welib_mirrors",
        lambda: mirrors or ["https://welib.example"],
    )
    monkeypatch.setattr("shelfmark.core.mirrors.get_aa_mirrors", lambda: [])
    monkeypatch.setattr(dd.network, "get_aa_base_url", lambda: "https://annas.example")


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        headers: dict[str, str] | None = None,
        text: str = "",
        chunks: list[bytes] | None = None,
        url: str = "",
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self._chunks = chunks or []
        self.url = url

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308) and bool(self.headers.get("Location"))

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 8192):
        del chunk_size
        yield from self._chunks

    def close(self) -> None:
        return None


class _DummyProgressBar:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def update(self, amount: int) -> None:
        del amount

    def close(self) -> None:
        return None


def _use_direct_welib_http(monkeypatch, dd) -> None:
    monkeypatch.setattr(dd.downloader, "_is_cf_bypass_enabled", lambda: False)
    monkeypatch.setattr(dd.downloader, "get_proxies", lambda _url: {})
    monkeypatch.setattr(dd.downloader, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(dd.downloader.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(dd.downloader, "tqdm", _DummyProgressBar)


@pytest.mark.parametrize("preflight_result", ["non_redirect", "request_error"])
def test_html_get_page_with_allowlist_still_uses_bypasser_after_preflight(
    monkeypatch, preflight_result
):
    from shelfmark.download import http as downloader

    preflighted_urls: list[str] = []
    bypassed_urls: list[str] = []

    def fake_get(url: str, **kwargs):
        preflighted_urls.append(url)
        assert kwargs["allow_redirects"] is False
        if preflight_result == "request_error":
            raise requests.exceptions.ConnectionError("preflight failed")
        return _FakeResponse(200, text="direct response should not be used", url=url)

    def fake_get_bypassed_page(url: str, *_args, **_kwargs):
        bypassed_urls.append(url)
        return "<html>bypassed</html>"

    monkeypatch.setattr(downloader, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(downloader, "get_proxies", lambda _url: {})
    monkeypatch.setattr(downloader, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(downloader.requests, "get", fake_get)
    monkeypatch.setattr(downloader, "get_bypassed_page", fake_get_bypassed_page)

    result = downloader.html_get_page(
        "https://welib.example/md5/abc123",
        use_bypasser=True,
        redirect_allowed=lambda url: url.startswith("https://welib.example/"),
    )

    assert result == "<html>bypassed</html>"
    assert preflighted_urls == ["https://welib.example/md5/abc123"]
    assert bypassed_urls == ["https://welib.example/md5/abc123"]


def test_html_get_page_with_allowlist_blocks_redirect_before_bypasser(monkeypatch):
    from shelfmark.download import http as downloader

    preflighted_urls: list[str] = []

    def fake_get(url: str, **kwargs):
        preflighted_urls.append(url)
        assert kwargs["allow_redirects"] is False
        return _FakeResponse(
            302,
            headers={"Location": "https://untrusted.example/md5/abc123"},
            url=url,
        )

    def unexpected_bypasser(*_args, **_kwargs):
        raise AssertionError("disallowed redirect must block before bypasser")

    monkeypatch.setattr(downloader, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(downloader, "get_proxies", lambda _url: {})
    monkeypatch.setattr(downloader, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(downloader.requests, "get", fake_get)
    monkeypatch.setattr(downloader, "get_bypassed_page", unexpected_bypasser)

    result = downloader.html_get_page(
        "https://welib.example/md5/abc123",
        use_bypasser=True,
        redirect_allowed=lambda url: url.startswith("https://welib.example/"),
    )

    assert result == ""
    assert preflighted_urls == ["https://welib.example/md5/abc123"]


def test_html_get_page_with_allowlist_passes_preflight_redirect_url_to_bypasser(monkeypatch):
    from shelfmark.download import http as downloader

    preflighted_urls: list[str] = []
    bypassed_urls: list[str] = []

    def fake_get(url: str, **kwargs):
        preflighted_urls.append(url)
        assert kwargs["allow_redirects"] is False
        if url == "https://welib.example/md5/abc123":
            return _FakeResponse(302, headers={"Location": "/landing/abc123"}, url=url)
        if url == "https://welib.example/landing/abc123":
            return _FakeResponse(200, text="direct response should not be used", url=url)
        raise AssertionError(f"unexpected preflight URL: {url}")

    def fake_get_bypassed_page(url: str, *_args, **_kwargs):
        bypassed_urls.append(url)
        return "<html>bypassed redirect target</html>"

    monkeypatch.setattr(downloader, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(downloader, "get_proxies", lambda _url: {})
    monkeypatch.setattr(downloader, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(downloader.requests, "get", fake_get)
    monkeypatch.setattr(downloader, "get_bypassed_page", fake_get_bypassed_page)

    result = downloader.html_get_page(
        "https://welib.example/md5/abc123",
        use_bypasser=True,
        redirect_allowed=lambda url: url.startswith("https://welib.example/"),
    )

    assert result == "<html>bypassed redirect target</html>"
    assert preflighted_urls == [
        "https://welib.example/md5/abc123",
        "https://welib.example/landing/abc123",
    ]
    assert bypassed_urls == ["https://welib.example/landing/abc123"]


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


def test_welib_allows_configured_origin_with_default_https_port(monkeypatch, tmp_path):
    import shelfmark.release_sources.direct_download as dd

    _enable_welib_only(monkeypatch, dd)
    fetched_pages: list[str] = []
    downloaded: list[tuple[str, str | None]] = []

    def fake_html_get_page(url: str, **_kwargs):
        fetched_pages.append(url)
        if url == "https://welib.example/md5/abc123":
            return '<a href="https://welib.example:443/files/book.epub">Download</a>'
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

    assert result == "https://welib.example:443/files/book.epub"
    assert fetched_pages == ["https://welib.example/md5/abc123"]
    assert downloaded == [
        ("https://welib.example:443/files/book.epub", "https://welib.example/md5/abc123")
    ]
    assert book_path.read_bytes() == b"x" * (11 * 1024)


@pytest.mark.parametrize(
    "returned_url",
    [
        "http://welib.example/files/book.epub",
        "https://welib.example:444/files/book.epub",
        "http://welib.example:8080/files/book.epub",
    ],
)
def test_welib_rejects_same_host_different_origin_before_download(
    monkeypatch, tmp_path, returned_url
):
    import shelfmark.release_sources.direct_download as dd

    _enable_welib_only(monkeypatch, dd)
    fetched_pages: list[str] = []

    def fake_html_get_page(url: str, **_kwargs):
        fetched_pages.append(url)
        if url == "https://welib.example/md5/abc123":
            return f'<a href="{returned_url}">Download</a>'
        raise AssertionError(f"unexpected fetch: {url}")

    def unexpected_download(*_args, **_kwargs):
        raise AssertionError("different-origin URL must not reach file download")

    monkeypatch.setattr(dd.downloader, "html_get_page", fake_html_get_page)
    monkeypatch.setattr(dd.downloader, "download_url", unexpected_download)

    result = dd._download_book(_book(), tmp_path / "book.epub")

    assert result is None
    assert fetched_pages == ["https://welib.example/md5/abc123"]


def test_welib_allows_explicit_non_default_origin(monkeypatch, tmp_path):
    import shelfmark.release_sources.direct_download as dd

    _enable_welib_only(
        monkeypatch,
        dd,
        template="http://welib.example:8080/md5/{md5}",
        mirrors=["http://welib.example:8080"],
    )
    fetched_pages: list[str] = []
    downloaded: list[tuple[str, str | None]] = []

    def fake_html_get_page(url: str, **_kwargs):
        fetched_pages.append(url)
        if url == "http://welib.example:8080/md5/abc123":
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

    assert result == "http://welib.example:8080/files/book.epub"
    assert fetched_pages == ["http://welib.example:8080/md5/abc123"]
    assert downloaded == [
        ("http://welib.example:8080/files/book.epub", "http://welib.example:8080/md5/abc123")
    ]
    assert book_path.read_bytes() == b"x" * (11 * 1024)


@pytest.mark.parametrize(
    "redirect_url",
    [
        "http://169.254.169.254/latest",
        "https://welib.example:444/md5/abc123",
    ],
)
def test_welib_rejects_page_resolution_redirect_outside_allowed_origin(
    monkeypatch, tmp_path, redirect_url
):
    import shelfmark.release_sources.direct_download as dd

    _enable_welib_only(monkeypatch, dd)
    _use_direct_welib_http(monkeypatch, dd)
    fetched_urls: list[str] = []

    def fake_get(url: str, **_kwargs):
        fetched_urls.append(url)
        if url == "https://welib.example/md5/abc123":
            return _FakeResponse(302, headers={"Location": redirect_url}, url=url)
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(dd.downloader.requests, "get", fake_get)

    result = dd._download_book(_book(), tmp_path / "book.epub")

    assert result is None
    assert fetched_urls == ["https://welib.example/md5/abc123"]


def test_welib_rejects_final_file_redirect_outside_allowed_origin(monkeypatch, tmp_path):
    import shelfmark.release_sources.direct_download as dd

    _enable_welib_only(monkeypatch, dd)
    _use_direct_welib_http(monkeypatch, dd)
    fetched_urls: list[str] = []

    def fake_get(url: str, **_kwargs):
        fetched_urls.append(url)
        if url == "https://welib.example/md5/abc123":
            return _FakeResponse(
                200,
                text='<a href="/files/book.epub">Download</a>',
                url=url,
            )
        if url == "https://welib.example/files/book.epub":
            return _FakeResponse(
                302,
                headers={"Location": "https://untrusted.example/files/book.epub"},
                url=url,
            )
        raise AssertionError(f"unexpected fetch: {url}")

    monkeypatch.setattr(dd.downloader.requests, "get", fake_get)

    result = dd._download_book(_book(), tmp_path / "book.epub")

    assert result is None
    assert fetched_urls == [
        "https://welib.example/md5/abc123",
        "https://welib.example/files/book.epub",
    ]
    assert not (tmp_path / "book.epub").exists()


def test_welib_allows_same_origin_redirects_during_resolution_and_download(monkeypatch, tmp_path):
    import shelfmark.release_sources.direct_download as dd

    _enable_welib_only(monkeypatch, dd)
    _use_direct_welib_http(monkeypatch, dd)
    fetched_urls: list[str] = []

    def fake_get(url: str, **_kwargs):
        fetched_urls.append(url)
        if url == "https://welib.example/md5/abc123":
            return _FakeResponse(302, headers={"Location": "/landing/abc123"}, url=url)
        if url == "https://welib.example/landing/abc123":
            return _FakeResponse(
                200,
                text='<a href="/files/book.epub">Download</a>',
                url=url,
            )
        if url == "https://welib.example/files/book.epub":
            return _FakeResponse(302, headers={"Location": "/files/book-v2.epub"}, url=url)
        if url == "https://welib.example/files/book-v2.epub":
            return _FakeResponse(
                200,
                headers={
                    "content-length": str(11 * 1024),
                    "content-type": "application/epub+zip",
                },
                chunks=[b"x" * (11 * 1024)],
                url=url,
            )
        raise AssertionError(f"unexpected fetch: {url}")

    book_path = tmp_path / "book.epub"
    monkeypatch.setattr(dd.downloader.requests, "get", fake_get)

    result = dd._download_book(_book(), book_path)

    assert result == "https://welib.example/files/book.epub"
    assert fetched_urls == [
        "https://welib.example/md5/abc123",
        "https://welib.example/landing/abc123",
        "https://welib.example/files/book.epub",
        "https://welib.example/files/book-v2.epub",
    ]
    assert book_path.read_bytes() == b"x" * (11 * 1024)
