"""Tests for handing a 503 that carries a browser challenge to the bypasser."""

import requests

_CHALLENGE_HTML = (
    "<html><head><title>Checking your browser before accessing z-lib.gd</title>"
    "<script src='/.well-known/ddos-guard/check.js'></script></head>"
    "<body>Please wait...</body></html>"
)


class _FakeResponse:
    """Minimal stand-in for requests.Response covering what html_get_page touches."""

    def __init__(
        self,
        status_code: int,
        *,
        url: str = "https://z-lib.gd/md5/abc",
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


def test_503_challenge_is_handed_to_the_bypasser(monkeypatch):
    """The reissued-cookie 503 from #1233 reaches the bypasser instead of retrying."""
    import shelfmark.download.http as http

    _neutralize_network(monkeypatch, http)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)

    attempts: list[dict[str, str]] = []
    bypassed: list[str] = []

    def fake_get(_url: str, **kwargs):
        attempts.append(dict(kwargs["cookies"]))
        # Hit 1 issues the cookie; every later hit re-serves the challenge unchanged,
        # which is what leaves the handshake with nothing to echo back.
        if len(attempts) == 1:
            return _FakeResponse(503, cookies={"bsrv": "1"})
        return _FakeResponse(503, text=_CHALLENGE_HTML, cookies={"bsrv": "1"})

    def fake_bypass(url: str, _selector=None, _cancel_flag=None):
        bypassed.append(url)
        return "<html>real page</html>"

    monkeypatch.setattr(http.requests, "get", fake_get)
    monkeypatch.setattr(http, "get_bypassed_page", fake_bypass)

    html = http.html_get_page("https://z-lib.gd/md5/abc", retry=10, success_delay=0)

    assert html == "<html>real page</html>"
    assert bypassed == ["https://z-lib.gd/md5/abc"]
    # The handshake still gets its echo; the challenge ends the loop on the second hit
    # rather than burning all ten attempts.
    assert attempts == [{}, {"bsrv": "1"}]


def test_plain_503_still_retries_without_bypassing(monkeypatch):
    """An overloaded origin has no challenge marker, so its retry path is untouched."""
    import shelfmark.download.http as http

    _neutralize_network(monkeypatch, http)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)

    attempts: list[dict[str, str]] = []
    bypassed: list[str] = []

    def fake_get(_url: str, **kwargs):
        attempts.append(dict(kwargs["cookies"]))
        return _FakeResponse(503, text="<html><body>Service Unavailable</body></html>")

    monkeypatch.setattr(http.requests, "get", fake_get)
    monkeypatch.setattr(
        http, "get_bypassed_page", lambda url, *_a, **_k: bypassed.append(url) or ""
    )

    html = http.html_get_page("https://z-lib.gd/md5/abc", retry=3, success_delay=0)

    assert html == ""
    assert bypassed == []
    assert attempts == [{}, {}, {}]


def test_503_challenge_respects_disabled_bypasser_fallback(monkeypatch):
    """Best-effort fetches must not stall on a minutes-long solve."""
    import shelfmark.download.http as http

    _neutralize_network(monkeypatch, http)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)

    bypassed: list[str] = []
    monkeypatch.setattr(
        http.requests,
        "get",
        lambda _url, **_kwargs: _FakeResponse(503, text=_CHALLENGE_HTML),
    )
    monkeypatch.setattr(
        http, "get_bypassed_page", lambda url, *_a, **_k: bypassed.append(url) or ""
    )

    html = http.html_get_page(
        "https://z-lib.gd/md5/abc", retry=1, success_delay=0, allow_bypasser_fallback=False
    )

    assert html == ""
    assert bypassed == []
