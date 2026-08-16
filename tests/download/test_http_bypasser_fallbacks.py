"""Tests for HTTP bypasser fallback handling."""

import requests

from shelfmark.bypass import BypassCancelledError


class _FakeResponse:
    def __init__(self, status_code: int, *, url: str = "") -> None:
        self.status_code = status_code
        self.url = url


def test_external_bypasser_clearance_is_presented_on_the_next_request(monkeypatch):
    """Clearance is read from the shared store whichever bypasser filled it.

    Guards the regression where the external path returned {} unconditionally: every
    request re-paid a 403 plus a full solve, and a download - which the solver cannot
    proxy - presented no clearance at all.
    """
    import shelfmark.bypass.cookie_store as cookie_store
    import shelfmark.download.http as http

    monkeypatch.setattr(cookie_store, "_cf_cookies", {})
    monkeypatch.setattr(cookie_store, "_cf_user_agents", {})
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_is_using_external_bypasser", lambda: True)

    cookie_store.store_extracted_cookies(
        url="https://annas-archive.gl/search",
        cookies=[{"name": "__ddg1_", "value": "clearance"}],
        user_agent="Mozilla/5.0 (solver)",
    )

    headers: dict[str, str] = {}
    cookies = http._apply_cf_bypass("https://annas-archive.gl/md5/abc", headers)

    assert cookies == {"__ddg1_": "clearance"}
    assert headers["User-Agent"] == "Mozilla/5.0 (solver)"


def test_external_bypasser_solve_is_reused_instead_of_re_solved(monkeypatch):
    """One solve should clear the following requests, not just the one that paid for it.

    A solve is tens of seconds of real browser, so re-running it per request is what
    made direct download unusable behind an external bypasser.
    """
    import shelfmark.bypass.cookie_store as cookie_store
    import shelfmark.download.http as http

    monkeypatch.setattr(cookie_store, "_cf_cookies", {})
    monkeypatch.setattr(cookie_store, "_cf_user_agents", {})
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_is_using_external_bypasser", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 100.0)
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: False)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)

    class _Cleared:
        is_redirect = False
        status_code = 200
        cookies: dict[str, str] = {}
        text = "<table>results</table>"
        url = "https://annas-archive.gl/search?q=dune"

        def raise_for_status(self) -> None:
            return None

    def gated_get(url: str, **kwargs):
        if kwargs.get("cookies", {}).get("cf_clearance") != "token":
            error = requests.exceptions.HTTPError("forbidden")
            error.response = _FakeResponse(403, url=url)
            raise error
        return _Cleared()

    solves: list[str] = []

    def fake_solve(url: str, *_args, **_kwargs):
        solves.append(url)
        cookie_store.store_extracted_cookies(
            url=url,
            cookies=[{"name": "cf_clearance", "value": "token"}],
            user_agent="Mozilla/5.0 (solver)",
        )
        return "<table>results</table>"

    monkeypatch.setattr(http.requests, "get", gated_get)
    monkeypatch.setattr(http, "get_bypassed_page", fake_solve)

    url = "https://annas-archive.gl/search?q=dune"
    first = http.html_get_page(url, retry=2, allow_bypasser_fallback=True, success_delay=0)
    second = http.html_get_page(url, retry=2, allow_bypasser_fallback=True, success_delay=0)

    assert first == "<table>results</table>"
    assert second == "<table>results</table>"
    # The second request rode the stored clearance instead of paying for another solve.
    assert solves == [url]


def test_403_with_a_concurrently_won_clearance_still_reaches_the_bypasser(monkeypatch):
    """The last attempt must hand off, not `continue` into the end of the loop.

    Another worker's solve can land between our request and its 403, which used to
    send this branch back round the retry loop - but on the final attempt (and
    MAX_RETRY=1 is the supported setting) `continue` just ends it, abandoning the
    request without ever offering the URL to the bypasser.
    """
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 100.0)
    # A concurrent solve has filled the store, but this request went out before it did.
    monkeypatch.setattr(http, "get_cf_cookies_for_domain", lambda _hostname: {"__ddg1_": "fresh"})
    monkeypatch.setattr(http, "_apply_cf_bypass", lambda _url, _headers: {})
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: False)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)

    def gated(url: str, **_kwargs):
        error = requests.exceptions.HTTPError("forbidden")
        error.response = _FakeResponse(403, url=url)
        raise error

    bypassed: list[str] = []
    monkeypatch.setattr(http.requests, "get", gated)
    monkeypatch.setattr(
        http,
        "get_bypassed_page",
        lambda url, *_a, **_k: bypassed.append(url) or "<table>results</table>",
    )

    url = "https://annas-archive.gl/search?q=dune"
    html = http.html_get_page(url, retry=1, allow_bypasser_fallback=True, success_delay=0)

    assert html == "<table>results</table>"
    assert bypassed == [url]


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

    def fake_clear(domain: str) -> None:
        cleared.append(domain)
        stale.clear()

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_is_using_external_bypasser", lambda: False)
    monkeypatch.setattr(http.cookie_store, "clear_cf_cookies", fake_clear)
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


def test_redirect_loop_gives_up_immediately_when_bypasser_not_allowed(monkeypatch):
    """A loop the bypasser may not rescue must fail fast, not burn the retry budget.

    Guards the regression where the unrescued loop raised TooManyRedirects into the
    retry path: that error is not retryable and carries no status, so every attempt
    re-ran the full 6-redirect loop for ~60 requests to AA before giving up.
    """
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_apply_cf_bypass", lambda _url, _headers: {})
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)
    monkeypatch.setattr(http.network, "is_aa_auto_mode", lambda: True)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)

    requested: list[str] = []

    def check_redirect(url: str, **_kwargs):
        requested.append(url)
        response = _FakeResponse(302, url=url)
        response.is_redirect = True
        response.headers = {"Location": f"{url}&check=1"}
        return response

    def unreachable_bypasser(*_args, **_kwargs):
        msg = "bypasser must not run when allow_bypasser_fallback is False"
        raise AssertionError(msg)

    monkeypatch.setattr(http.requests, "get", check_redirect)
    monkeypatch.setattr(http, "get_bypassed_page", unreachable_bypasser)

    html = http.html_get_page(
        "https://annas-archive.gl/dyn/md5/summary/abc",
        retry=10,
        allow_bypasser_fallback=False,
        success_delay=0,
    )

    assert html == ""
    # One pass through the redirect cap, not one pass per retry attempt.
    assert len(requested) == http._MAX_REDIRECTS + 1


def test_html_get_page_redirect_loop_purges_cookies_and_bypasses(monkeypatch):
    """A redirect loop is the challenge served against stale cookies, not a retryable error.

    TooManyRedirects carries no status code, so without an explicit branch it falls through
    to the generic retry path and repeats the identical failure for the whole retry budget.
    """
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_is_using_external_bypasser", lambda: False)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 330.0)
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)
    monkeypatch.setattr(http.network, "get_aa_base_url", lambda: "https://annas-archive.li")
    monkeypatch.setattr(http.network, "is_aa_auto_mode", lambda: False)

    cleared: list[str] = []

    monkeypatch.setattr(http.cookie_store, "clear_cf_cookies", cleared.append)
    monkeypatch.setattr(
        http.cookie_store, "get_cf_cookies_for_domain", lambda _domain: {"__ddg2_": "stale"}
    )
    monkeypatch.setattr(http.cookie_store, "get_cf_user_agent_for_domain", lambda _domain: None)
    monkeypatch.setattr(http, "get_bypassed_page", lambda *_args, **_kwargs: "SOLVED")

    class _FakeRedirect:
        """A 302 that always points at the same ?check=1 URL, cookies unchanged."""

        is_redirect = True
        status_code = 302
        cookies = {"__ddg2_": "stale"}

        def __init__(self, url: str) -> None:
            self.url = url
            self.headers = {"Location": "https://annas-archive.li/search?q=test&check=1"}

    hits: list[str] = []

    def fake_get(url: str, **kwargs):
        hits.append(url)
        # Stale cookies: the server keeps re-issuing the same ?check=1 redirect.
        return _FakeRedirect(url)

    monkeypatch.setattr(http.requests, "get", fake_get)

    html = http.html_get_page(
        "https://annas-archive.li/search?q=test",
        retry=2,
        success_delay=0,
    )

    assert html == "SOLVED"
    assert cleared == ["annas-archive.li"]
    # The loop is cut short: no second attempt spent repeating the same redirects.
    assert len(hits) == http._MAX_REDIRECTS + 1


def test_html_get_page_redirect_loop_on_non_aa_host_is_left_alone(monkeypatch):
    """Only hosts whose redirects we follow manually get the challenge treatment.

    Elsewhere requests follows redirects itself, so a loop is an ordinary misconfiguration -
    purging that host's cookies and forcing a bypass would be the wrong response.
    """
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http, "_is_using_external_bypasser", lambda: False)
    monkeypatch.setattr(http, "_apply_cf_bypass", lambda _url, _headers: {})
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: False)

    bypassed: list[str] = []
    monkeypatch.setattr(
        http, "get_bypassed_page", lambda url, *_args, **_kwargs: bypassed.append(url) or "SOLVED"
    )

    def fake_get(_url: str, **_kwargs):
        raise requests.exceptions.TooManyRedirects("Exceeded 30 redirects.")

    monkeypatch.setattr(http.requests, "get", fake_get)

    html = http.html_get_page("https://example.com/loop", retry=2, success_delay=0)

    assert html == ""
    assert bypassed == []
