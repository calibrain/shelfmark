"""The DDoS-Guard ?check=1 handshake must win over anything the clearance store holds.

DDoS-Guard reuses the same cookie names (__ddg1_/__ddg2_) for the probe it issues on the
302 and for what a solve leaves behind. When the store's copy was merged on top, the value
the server had just issued never left the process, the probe could never terminate, and
every request ended in the redirect-loop handoff and paid for a full browser solve - one
per query, which is exactly what was reported on issue #1276.
"""

import pytest
import requests

import shelfmark.bypass.cookie_store as cs
import shelfmark.download.http as http

URL = "https://annas-archive.gl/search?q=dune"
FRESH = {"__ddg1_": "FRESH1", "__ddg2_": "FRESH2"}


class _FakeResponse:
    def __init__(self, status_code, *, headers=None, cookies=None, text="", url=URL):
        self.status_code = status_code
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.text = text
        self.url = url

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.exceptions.HTTPError(f"{self.status_code} Error")
            error.response = self
            raise error


class _DummySelector:
    """AA selector stub, so these tests never elect a real mirror."""

    def __init__(self) -> None:
        self.current_base = "https://annas-archive.gl"
        self.attempts_this_dns = 0
        self.last_failure: str | None = None

    def rewrite(self, url: str) -> str:
        return url

    def next_mirror_or_rotate_dns(self, allow_dns=True, *, fatal=False, reason=""):
        return None, "exhausted"


@pytest.fixture
def ddos_guard(monkeypatch):
    """An AA mirror that grants the page only once the client echoes what it issued."""
    monkeypatch.setattr(cs, "_cf_cookies", {})
    monkeypatch.setattr(cs, "_cf_user_agents", {})
    # Keeps the store from importing the mirror registry (and its dependency graph)
    # for a lookup these tests do not exercise.
    monkeypatch.setattr(cs, "_get_full_cookie_domains", set)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)
    monkeypatch.setattr(http.network, "is_aa_auto_mode", lambda: True)
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)

    sent: list[dict[str, str]] = []

    def fake_get(_url, **kwargs):
        cookies = dict(kwargs.get("cookies") or {})
        sent.append(cookies)
        if all(cookies.get(name) == value for name, value in FRESH.items()):
            return _FakeResponse(200, text="<html>the real search page</html>")
        return _FakeResponse(
            302, headers={"Location": "/search?q=dune&check=1"}, cookies=dict(FRESH)
        )

    monkeypatch.setattr(http.requests, "get", fake_get)
    return sent


@pytest.fixture
def bypasser_calls(monkeypatch):
    """Record redirect-loop handoffs instead of starting a browser."""
    calls: list[str] = []

    def fake_get_bypassed_page(url, _selector=None, _cancel_flag=None):
        calls.append(url)
        return "<html>solved by the browser</html>"

    monkeypatch.setattr(http, "get_bypassed_page", fake_get_bypassed_page)
    monkeypatch.setattr(http, "_bypass_grace_seconds", lambda: 1.0)
    return calls


def test_handshake_completes_with_an_empty_store(ddos_guard, bypasser_calls):
    """The baseline: echoing the issued cookies back clears the probe in two hops."""
    html = http.html_get_page(URL, retry=1, selector=_DummySelector(), success_delay=0)

    assert html == "<html>the real search page</html>"
    assert ddos_guard == [{}, FRESH]
    assert not bypasser_calls, "no browser solve should have been needed"


def test_stored_clearance_does_not_mask_the_issued_cookies(ddos_guard, bypasser_calls):
    """A solve leaves __ddg1_/__ddg2_ behind; the next probe must still be answerable.

    This is the regression. With the store merged last, all six hops re-sent the stale
    pair, the loop never terminated and the request fell through to a browser solve.
    """
    cs._cf_cookies["annas-archive.gl"] = {
        "__ddg1_": {"value": "STALE1", "expiry": None},
        "__ddg2_": {"value": "STALE2", "expiry": None},
    }

    html = http.html_get_page(URL, retry=1, selector=_DummySelector(), success_delay=0)

    assert html == "<html>the real search page</html>"
    assert not bypasser_calls, "a stale cookie must not cost a browser solve"
    # First hop presents what the store had; the second answers with what was just issued.
    assert ddos_guard[0] == {"__ddg1_": "STALE1", "__ddg2_": "STALE2"}
    assert ddos_guard[-1] == FRESH


def test_store_still_applies_when_the_server_issues_nothing(monkeypatch, bypasser_calls):
    """Handshake cookies winning must not stop stored clearance being presented."""
    monkeypatch.setattr(cs, "_cf_cookies", {})
    monkeypatch.setattr(cs, "_cf_user_agents", {})
    # Keeps the store from importing the mirror registry (and its dependency graph)
    # for a lookup these tests do not exercise.
    monkeypatch.setattr(cs, "_get_full_cookie_domains", set)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)
    cs._cf_cookies["annas-archive.gl"] = {"__ddg1_": {"value": "CLEARANCE", "expiry": None}}

    sent: list[dict[str, str]] = []

    def fake_get(_url, **kwargs):
        sent.append(dict(kwargs.get("cookies") or {}))
        return _FakeResponse(200, text="<html>page</html>")

    monkeypatch.setattr(http.requests, "get", fake_get)

    assert (
        http.html_get_page(URL, retry=1, selector=_DummySelector(), success_delay=0)
        == "<html>page</html>"
    )
    assert sent == [{"__ddg1_": "CLEARANCE"}]
