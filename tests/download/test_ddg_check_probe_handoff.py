"""A solver must never be handed DDoS-Guard's ?check=1 probe URL.

Regression for #1292. `html_get_page` follows Anna's Archive redirects by hand, and
DDoS-Guard's gate answers /search with a 302 to the same path plus `check=1`. Because
the follower walks that handshake by reassigning `current_url`, every downstream handoff
- the 403 branch, the 503-challenge branch, the redirect-loop rescues - passed the
*probe* URL to the bypasser rather than the page we wanted.

A solver opens that in a fresh browser holding none of the cookies the probe exists to
collect, so DDoS-Guard cannot verify it automatically and serves the manual CAPTCHA page
that nothing can solve. The reporter's log is exactly that: a 403 handed off on a
`&check=1` URL, FlareSolverr answering "Challenge solved!", and a 4.7 KB DDOS-GUARD
CAPTCHA page coming back.
"""

import requests


class _FakeResponse:
    """Minimal stand-in for requests.Response covering what html_get_page touches."""

    def __init__(
        self,
        status_code: int,
        *,
        url: str,
        text: str = "",
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.text = text
        self.cookies = cookies or {}
        self.headers = {"Content-Type": "text/html;charset=utf-8", **(headers or {})}
        self.is_redirect = 300 <= status_code < 400

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.exceptions.HTTPError(f"{self.status_code} Error")
            error.response = self
            raise error


def _aa_http(monkeypatch):
    """Import http with the network stubbed out and AA treated as an AA host."""
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_apply_cf_bypass", lambda _url, _headers: {})
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)
    monkeypatch.setattr(http.network, "is_aa_auto_mode", lambda: True)
    return http


SEARCH_URL = "https://annas-archive.pk/search?index=&display=table&q=Ken+follett"
PROBE_URL = f"{SEARCH_URL}&check=1"


def test_403_on_the_check_probe_hands_over_the_pre_probe_url(monkeypatch):
    """The reporter's exact sequence: 302 to ?check=1, then 403 on the probe."""
    http = _aa_http(monkeypatch)

    bypassed: list[str] = []

    def fake_get(url: str, **_kwargs):
        if "check=1" not in url:
            return _FakeResponse(302, url=url, headers={"Location": PROBE_URL})
        return _FakeResponse(403, url=url)

    monkeypatch.setattr(http.requests, "get", fake_get)
    monkeypatch.setattr(
        http, "get_bypassed_page", lambda url, *_a, **_k: bypassed.append(url) or "<html>ok</html>"
    )

    html = http.html_get_page(SEARCH_URL, retry=1, success_delay=0)

    assert html == "<html>ok</html>"
    # The page we wanted, not the handshake hop we happened to be standing on.
    assert bypassed == [SEARCH_URL]


def test_redirect_loop_hands_over_the_pre_probe_url(monkeypatch):
    """Stale clearance turns the gate into an endless ?check=1 bounce."""
    http = _aa_http(monkeypatch)

    bypassed: list[str] = []

    monkeypatch.setattr(
        http.requests,
        "get",
        lambda url, **_kwargs: _FakeResponse(302, url=url, headers={"Location": PROBE_URL}),
    )
    monkeypatch.setattr(
        http, "get_bypassed_page", lambda url, *_a, **_k: bypassed.append(url) or "<html>ok</html>"
    )

    html = http.html_get_page(SEARCH_URL, retry=1, success_delay=0)

    assert html == "<html>ok</html>"
    assert bypassed == [SEARCH_URL]


def test_only_the_check_parameter_is_dropped(monkeypatch):
    """Everything else about the URL survives - it is still the search we asked for."""
    http = _aa_http(monkeypatch)

    url = (
        "https://annas-archive.pk/search?index=&page=1&display=table&acc=aa_download"
        "&acc=external_download&ext=epub&q=Ken+follett&check=1"
    )

    assert http._solvable_url(url) == (
        "https://annas-archive.pk/search?index=&page=1&display=table&acc=aa_download"
        "&acc=external_download&ext=epub&q=Ken+follett"
    )


def test_a_url_without_the_probe_is_returned_untouched(monkeypatch):
    """No rewriting, no re-encoding: an unrelated URL must come back identical."""
    http = _aa_http(monkeypatch)

    url = "https://annas-archive.pk/md5/abc?q=a%20b&empty="

    assert http._solvable_url(url) is url


def test_non_aa_hosts_keep_their_check_parameter(monkeypatch):
    """Elsewhere `check` is an ordinary query parameter and none of our business."""
    import shelfmark.download.http as http

    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: False)

    url = "https://example.com/api?check=1"

    assert http._solvable_url(url) == url
