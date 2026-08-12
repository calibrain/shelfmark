"""Tests for the Z-Library 503 cookie handshake."""

import requests


class _FakeResponse:
    """Minimal stand-in for requests.Response covering what html_get_page touches."""

    def __init__(
        self,
        status_code: int,
        *,
        url: str = "https://z-lib.fm/md5/abc",
        text: str = "",
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.text = text
        self.cookies = cookies or {}
        self.headers = {"Content-Type": "text/html;charset=utf-8"}
        self.is_redirect = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.exceptions.HTTPError(f"{self.status_code} Error")
            error.response = self
            raise error


def _neutralize_network(monkeypatch, http):
    monkeypatch.setattr(http, "_apply_cf_bypass", lambda _url, _headers: {})
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: False)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)


def test_html_get_page_echoes_503_cookie(monkeypatch):
    """A 503 that only sets a cookie is cleared by sending that cookie back."""
    import shelfmark.download.http as http

    _neutralize_network(monkeypatch, http)

    sent_cookies: list[dict[str, str]] = []

    def fake_get(_url: str, **kwargs):
        sent_cookies.append(dict(kwargs["cookies"]))
        if len(sent_cookies) == 1:
            return _FakeResponse(503, cookies={"zlib_sid": "s3cr3t"})
        return _FakeResponse(200, text="<html>real page</html>")

    monkeypatch.setattr(http.requests, "get", fake_get)

    html = http.html_get_page("https://z-lib.fm/md5/abc", retry=3, success_delay=0)

    assert html == "<html>real page</html>"
    # The first hit carries nothing; the retry echoes back exactly what the 503 issued.
    assert sent_cookies == [{}, {"zlib_sid": "s3cr3t"}]


def test_html_get_page_stops_echoing_when_cookie_is_reissued(monkeypatch):
    """A server repeating the same cookie must not spin the request loop forever."""
    import shelfmark.download.http as http

    _neutralize_network(monkeypatch, http)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: False)

    sent_cookies: list[dict[str, str]] = []

    def fake_get(_url: str, **kwargs):
        sent_cookies.append(dict(kwargs["cookies"]))
        return _FakeResponse(503, cookies={"zlib_sid": "same"})

    monkeypatch.setattr(http.requests, "get", fake_get)

    html = http.html_get_page("https://z-lib.fm/md5/abc", retry=1, success_delay=0)

    assert html == ""
    # One initial hit plus one echo; the reissued identical cookie yields no third request.
    assert sent_cookies == [{}, {"zlib_sid": "same"}]


def test_html_get_page_leaves_cookieless_503_on_the_retry_path(monkeypatch):
    """A plain overloaded-server 503 keeps its existing retry behaviour."""
    import shelfmark.download.http as http

    _neutralize_network(monkeypatch, http)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: False)

    attempts: list[dict[str, str]] = []

    def fake_get(_url: str, **kwargs):
        attempts.append(dict(kwargs["cookies"]))
        return _FakeResponse(503)

    monkeypatch.setattr(http.requests, "get", fake_get)

    html = http.html_get_page("https://z-lib.fm/md5/abc", retry=2, success_delay=0)

    assert html == ""
    # Two ordinary attempts, no extra in-place retry and no cookies invented.
    assert attempts == [{}, {}]
