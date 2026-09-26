"""Tests for handing a DiamWall 513 challenge to the browser bypasser."""

import requests


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.status_code = 513
        self.url = "https://z-lib.gd/book/abc"
        self.text = text
        self.cookies: dict[str, str] = {}
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self.is_redirect = False

    def raise_for_status(self) -> None:
        error = requests.exceptions.HTTPError("513 Error")
        error.response = self
        raise error


def _neutralize_network(monkeypatch, http) -> None:
    monkeypatch.setattr(http, "_apply_cf_bypass", lambda _url, _headers: {})
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: False)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 1.0)


def test_diamwall_513_is_handed_to_the_bypasser(monkeypatch) -> None:
    import shelfmark.download.http as http

    _neutralize_network(monkeypatch, http)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)

    attempts: list[str] = []
    bypassed: list[str] = []
    monkeypatch.setattr(
        http.requests,
        "get",
        lambda url, **_kwargs: (
            attempts.append(url)
            or _FakeResponse(
                "<html><title>DiamWall</title><body>Browser verification</body></html>"
            )
        ),
    )
    monkeypatch.setattr(
        http,
        "get_bypassed_page",
        lambda url, *_args, **_kwargs: bypassed.append(url) or "<html>book page</html>",
    )

    url = "https://z-lib.gd/book/abc"
    html = http.html_get_page(url, retry=10, success_delay=0)

    assert html == "<html>book page</html>"
    assert attempts == [url]
    assert bypassed == [url]


def test_plain_513_does_not_start_a_browser(monkeypatch) -> None:
    import shelfmark.download.http as http

    _neutralize_network(monkeypatch, http)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)

    bypassed: list[str] = []
    monkeypatch.setattr(
        http.requests,
        "get",
        lambda _url, **_kwargs: _FakeResponse("<html><body>Unknown server error</body></html>"),
    )
    monkeypatch.setattr(
        http,
        "get_bypassed_page",
        lambda url, *_args, **_kwargs: bypassed.append(url) or "",
    )

    html = http.html_get_page("https://z-lib.gd/book/abc", retry=1, success_delay=0)

    assert html == ""
    assert bypassed == []
