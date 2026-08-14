"""Tests for HTTP bypasser fallback handling."""

import requests

from shelfmark.bypass import BypassCancelledError


class _FakeResponse:
    def __init__(self, status_code: int, *, url: str = "") -> None:
        self.status_code = status_code
        self.url = url


def test_html_get_page_ignores_status_callback_failure(monkeypatch):
    """A raising status_callback must not break the bypass it was reporting on."""
    import shelfmark.download.http as http
    from shelfmark.download.activity import ACTIVITY_GRACE_STATUS

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 330.0)
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
        (ACTIVITY_GRACE_STATUS, "330.0"),
        (ACTIVITY_GRACE_STATUS, "0.0"),
    ]


def test_html_get_page_requests_and_releases_activity_grace(monkeypatch):
    """The bypass declares its budget before blocking and releases it afterwards."""
    import shelfmark.download.http as http
    from shelfmark.download.activity import ACTIVITY_GRACE_STATUS

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 424.0)

    calls: list[tuple[str, str | None]] = []

    def fake_bypass(*_args, **_kwargs):
        # The grace must already be in place before the long blocking call starts.
        assert calls[-1] == (ACTIVITY_GRACE_STATUS, "424.0")
        return "OK"

    monkeypatch.setattr(http, "get_bypassed_page", fake_bypass)

    html = http.html_get_page(
        "https://example.com",
        retry=1,
        use_bypasser=True,
        status_callback=lambda status, message: calls.append((status, message)),
    )

    assert html == "OK"
    assert calls[-1] == (ACTIVITY_GRACE_STATUS, "0.0")


def test_html_get_page_releases_grace_and_reports_error_when_bypasser_fails(monkeypatch):
    """A failing bypasser surfaces its real error instead of a silent empty result."""
    import shelfmark.download.http as http
    from shelfmark.download.activity import ACTIVITY_GRACE_STATUS

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 100.0)

    def failing_bypasser(*_args, **_kwargs):
        raise requests.exceptions.RequestException("500 Server Error")

    monkeypatch.setattr(http, "get_bypassed_page", failing_bypasser)

    calls: list[tuple[str, str | None]] = []
    html = http.html_get_page(
        "https://example.com",
        retry=1,
        use_bypasser=True,
        status_callback=lambda status, message: calls.append((status, message)),
    )

    assert html == ""
    errors = [message for status, message in calls if status == "error"]
    assert len(errors) == 1
    assert "500 Server Error" in (errors[0] or "")
    # The grace is always released, even on the failure path.
    assert calls[-1] == (ACTIVITY_GRACE_STATUS, "0.0")


def test_html_get_page_does_not_report_error_when_bypass_is_cancelled(monkeypatch):
    """Cancellation is a user action, not a failure worth surfacing as an error."""
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 100.0)

    def cancelled_bypasser(*_args, **_kwargs):
        raise BypassCancelledError("Bypass cancelled")

    monkeypatch.setattr(http, "get_bypassed_page", cancelled_bypasser)

    calls: list[tuple[str, str | None]] = []
    html = http.html_get_page(
        "https://example.com",
        retry=1,
        use_bypasser=True,
        status_callback=lambda status, message: calls.append((status, message)),
    )

    assert html == ""
    assert [status for status, _message in calls if status == "error"] == []


def test_html_get_page_returns_empty_on_bypass_cancellation(monkeypatch):
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)

    def failing_bypasser(*_args, **_kwargs):
        raise BypassCancelledError("Bypass cancelled")

    monkeypatch.setattr(http, "get_bypassed_page", failing_bypasser)

    html = http.html_get_page("https://example.com", retry=1, use_bypasser=True)

    assert html == ""


def test_challenged_search_switches_to_bypasser(monkeypatch):
    """AA gates /search behind DDoS-Guard; the 403 must reach the bypasser, not a 503.

    Guards the regression where search passed allow_bypasser_fallback=False, so a
    challenge on every mirror surfaced as "mirrors are blocked" with no solve attempted.
    """
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 100.0)
    monkeypatch.setattr(http, "get_cf_cookies_for_domain", lambda _hostname: {})
    monkeypatch.setattr(http, "_apply_cf_bypass", lambda _url, _headers: {})
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: False)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)

    def ddos_guarded(url: str, **_kwargs):
        error = requests.exceptions.HTTPError("forbidden")
        error.response = _FakeResponse(403, url=url)
        raise error

    bypassed: list[str] = []
    monkeypatch.setattr(http.requests, "get", ddos_guarded)
    monkeypatch.setattr(
        http,
        "get_bypassed_page",
        lambda url, *_a, **_k: bypassed.append(url) or "<table>results</table>",
    )

    html = http.html_get_page(
        "https://annas-archive.gl/search?q=dune",
        retry=10,
        allow_bypasser_fallback=True,
        success_delay=0,
    )

    assert html == "<table>results</table>"
    assert bypassed == ["https://annas-archive.gl/search?q=dune"]


def test_redirect_loop_purges_stale_cookies_and_switches_to_bypasser(monkeypatch):
    """A stale clearance cookie turns the gate into a `?check=1` redirect loop.

    Guards the regression where TooManyRedirects carried no status code, so the
    403-only rescue never fired and every retry re-sent the dead cookie.
    """
    import shelfmark.download.http as http

    stale = {"__ddg8_": "stale"}
    cleared: list[str] = []

    class _FakeInternalBypasser:
        @staticmethod
        def clear_cf_cookies(domain: str) -> None:
            cleared.append(domain)
            stale.clear()

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_is_using_external_bypasser", lambda: False)
    monkeypatch.setattr(http, "_get_internal_bypasser", lambda: _FakeInternalBypasser)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 100.0)
    monkeypatch.setattr(http, "_apply_cf_bypass", lambda _url, _headers: dict(stale))
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)
    monkeypatch.setattr(http.network, "is_aa_auto_mode", lambda: True)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)

    sent_cookies: list[dict[str, str]] = []

    def check_redirect(url: str, **kwargs):
        sent_cookies.append(kwargs["cookies"])
        response = _FakeResponse(302, url=url)
        response.is_redirect = True
        response.headers = {"Location": f"{url}&check=1"}
        return response

    bypassed: list[str] = []
    monkeypatch.setattr(http.requests, "get", check_redirect)
    monkeypatch.setattr(
        http,
        "get_bypassed_page",
        lambda url, *_a, **_k: bypassed.append(url) or "<table>results</table>",
    )

    html = http.html_get_page(
        "https://annas-archive.gl/search?q=dune",
        retry=10,
        allow_bypasser_fallback=True,
        success_delay=0,
    )

    assert html == "<table>results</table>"
    assert cleared == ["annas-archive.gl"]
    assert len(bypassed) == 1
    # Escaped on the first exception, not retried with the dead cookie.
    assert sent_cookies[0] == {"__ddg8_": "stale"}


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
