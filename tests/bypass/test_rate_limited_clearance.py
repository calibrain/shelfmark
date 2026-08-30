"""A 429 is throttling, not a dead clearance cookie.

Issue #1276: every rejection of the cached cookies took the same exit, which cleared the
host's clearance. That is right for a 403 and for the ?check=1 redirect loop - being
challenged while presenting a cookie proves the cookie is dead - and wrong for a 429,
where the origin is rate-limiting the IP and would answer a real browser holding the very
same cookies identically.

The cost in the reported bundle: a solve completed at 13:41:23 and stored five cookies;
six seconds later a 429 threw them away, and the next query bought its own 56-second
browser solve. Reuse rate across the whole log was 0 of 2.
"""

import pytest

import shelfmark.bypass.cookie_store as cs
import shelfmark.bypass.internal_bypasser as ib

URL = "https://annas-archive.gl/search?q=dune"
HOST = "annas-archive.gl"


class _Resp:
    def __init__(self, status_code, text="page"):
        self.status_code = status_code
        self.text = text


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(cs, "_cf_cookies", {})
    monkeypatch.setattr(cs, "_cf_user_agents", {})
    monkeypatch.setattr(cs, "_get_full_cookie_domains", set)
    monkeypatch.setattr(ib, "get_proxies", lambda _url: None)
    monkeypatch.setattr(ib, "get_ssl_verify", lambda _url: True)
    cs._cf_cookies[HOST] = {
        "__ddg1_": {"value": "clearance", "expiry": None},
        "__ddg2_": {"value": "c2", "expiry": None},
    }


def _cooldowns(monkeypatch):
    """Record note_rate_limited calls without arming the real per-host ladder."""
    armed: list[str] = []
    monkeypatch.setattr(ib.network, "note_rate_limited", lambda url: armed.append(url) or 120.0)
    return armed


def test_429_keeps_the_clearance(monkeypatch):
    armed = _cooldowns(monkeypatch)
    monkeypatch.setattr(ib.requests, "get", lambda *a, **k: _Resp(429))

    assert ib._try_with_cached_cookies(URL, HOST) is None
    assert ib.get_cf_cookies_for_domain(HOST) == {"__ddg1_": "clearance", "__ddg2_": "c2"}
    assert armed == [URL], "the backoff must still be armed"


def test_403_still_discards_the_clearance(monkeypatch):
    """The pre-existing behaviour for a genuine rejection must not regress."""
    _cooldowns(monkeypatch)
    monkeypatch.setattr(ib.requests, "get", lambda *a, **k: _Resp(403))

    assert ib._try_with_cached_cookies(URL, HOST) is None
    assert ib.get_cf_cookies_for_domain(HOST) == {}


def test_redirect_loop_still_discards_the_clearance(monkeypatch):
    _cooldowns(monkeypatch)

    def boom(*_a, **_k):
        raise ib.requests.exceptions.TooManyRedirects("Exceeded 30 redirects")

    monkeypatch.setattr(ib.requests, "get", boom)

    assert ib._try_with_cached_cookies(URL, HOST) is None
    assert ib.get_cf_cookies_for_domain(HOST) == {}


def test_a_throttled_host_is_not_handed_a_browser_solve(monkeypatch):
    """A solve cannot clear a throttle, and is itself more traffic at a host asking for
    less. get_bypassed_page checks the cooldown before the queue; get() has to re-check
    after it, because a request can hold for LOCKED while another collects the 429."""
    monkeypatch.setattr(ib.requests, "get", lambda *a, **k: _Resp(429))
    monkeypatch.setattr(ib.network, "note_rate_limited", lambda _url: 120.0)
    monkeypatch.setattr(ib.network, "host_cooldown_remaining", lambda _url: 118.0)

    solved: list[str] = []
    monkeypatch.setattr(
        ib, "_run_bypass_in_current_process", lambda url, *a, **k: solved.append(url) or "html"
    )
    monkeypatch.setattr(ib.env, "DOCKERMODE", False)

    with pytest.raises(ib.network.RateLimitedError) as excinfo:
        ib.get(URL, retry=1)

    assert solved == [], "no browser should have been started"
    assert "rate-limited" in str(excinfo.value)
    # And the clearance survives, ready for when the cooldown clears.
    assert ib.get_cf_cookies_for_domain(HOST) == {"__ddg1_": "clearance", "__ddg2_": "c2"}


def test_a_host_that_is_not_throttled_still_solves(monkeypatch):
    monkeypatch.setattr(ib.requests, "get", lambda *a, **k: _Resp(403))
    monkeypatch.setattr(ib.network, "host_cooldown_remaining", lambda _url: 0.0)

    solved: list[str] = []
    monkeypatch.setattr(
        ib, "_run_bypass_in_current_process", lambda url, *a, **k: solved.append(url) or "html"
    )
    monkeypatch.setattr(ib.env, "DOCKERMODE", False)

    assert ib.get(URL, retry=1) == "html"
    assert solved == [URL]
