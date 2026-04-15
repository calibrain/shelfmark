"""Tests for HTTP bypasser fallback handling."""

import requests

from shelfmark.bypass import BypassCancelledError


class _FakeResponse:
    def __init__(self, status_code: int, *, url: str = "") -> None:
        self.status_code = status_code
        self.url = url


class _ImmediateThread:
    def __init__(self, *args, **kwargs) -> None:
        self._target = kwargs["target"]

    def start(self) -> None:
        self._target()

    def join(self, timeout: float | None = None) -> None:
        del timeout


def test_html_get_page_ignores_heartbeat_callback_failure(monkeypatch):
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "Thread", _ImmediateThread)
    monkeypatch.setattr(http, "get_bypassed_page", lambda *_args, **_kwargs: "OK")

    calls: list[tuple[str, str | None]] = []

    def status_callback(status: str, message: str | None) -> None:
        calls.append((status, message))
        if len(calls) > 1:
            raise RuntimeError("callback failed")

    html = http.html_get_page(
        "https://example.com",
        retry=1,
        use_bypasser=True,
        status_callback=status_callback,
    )

    assert html == "OK"
    assert calls == [
        ("resolving", "Bypassing protection..."),
        ("resolving", "Bypassing protection..."),
    ]


def test_html_get_page_returns_empty_on_bypass_cancellation(monkeypatch):
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)

    def failing_bypasser(*_args, **_kwargs):
        raise BypassCancelledError("Bypass cancelled")

    monkeypatch.setattr(http, "get_bypassed_page", failing_bypasser)

    html = http.html_get_page("https://example.com", retry=1, use_bypasser=True)

    assert html == ""


def test_download_url_ignores_zlib_cookie_refresh_failure(monkeypatch):
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_is_configured_zlib_host", lambda hostname: hostname == "z-lib.fm")
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)

    def fake_get(_url: str, **_kwargs):
        error = requests.exceptions.HTTPError("forbidden")
        error.response = _FakeResponse(403, url=_url)
        raise error

    def failing_bypasser(*_args, **_kwargs):
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(http.requests, "get", fake_get)
    monkeypatch.setattr(http, "get_bypassed_page", failing_bypasser)

    result = http.download_url(
        "https://z-lib.fm/download/book",
        referer="https://z-lib.fm/books/example",
    )

    assert result is None


def test_get_bypassed_page_uses_external_bypasser_when_enabled(monkeypatch):
    import shelfmark.download.http as http

    calls: list[tuple] = []

    class FakeExternalBypasser:
        def get_bypassed_page(self, url, selector, cancel_flag):
            calls.append((url, selector, cancel_flag))
            return "EXT"

    monkeypatch.setattr(http, "_is_using_external_bypasser", lambda: True)
    monkeypatch.setattr(http, "_get_external_bypasser", lambda: FakeExternalBypasser())

    selector = object()
    cancel_flag = object()

    assert http.get_bypassed_page("https://example.com", selector, cancel_flag) == "EXT"
    assert calls == [("https://example.com", selector, cancel_flag)]
