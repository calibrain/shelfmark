"""DDoS-Guard cookie reuse between requests.

Anna's Archive issues ten cookies after a solve, and they are not equivalent:

    __ddg1_/__ddg2_/__ddgid_   ~1 year   clearance
    __ddgmark_                 ~1 day
    __ddg5_                    session
    __ddg8_/__ddg9_/__ddg10_   ~40 min   one check: token, CLIENT IP, TIMESTAMP
    aa_ddg_check               ~90 days  Anna's Archive's own pass for its ?check=1 hop

Replaying the last three is what produces the ?check=1 redirect loop. They describe a
single check, so once the timestamp ages out - or the egress IP changes, routine
behind a VPN - DDoS-Guard stops recognising the caller and re-arms the challenge on
every request. Storing an expired cookie and sending it forever has the same effect.
"""

import time

import pytest

import shelfmark.bypass.cookie_store as cs
import shelfmark.bypass.internal_bypasser as ib


@pytest.fixture(autouse=True)
def _clean_cookie_store(monkeypatch):
    monkeypatch.setattr(cs, "_cf_cookies", {})
    monkeypatch.setattr(cs, "_cf_user_agents", {})


class _Cookie:
    """Stand-in for the CDP cookie objects the bypasser extracts."""

    def __init__(self, name, value="v", expires=None, domain="annas-archive.gl"):
        self.name = name
        self.value = value
        self.expires = expires
        self.domain = domain
        self.path = "/"
        self.secure = True


def _store(cookies, url="https://annas-archive.gl/search"):
    cs.store_extracted_cookies(url=url, cookies=cookies, user_agent="UA/1.0")


@pytest.fixture
def cookie_store_logs():
    """Collect cookie-store log messages.

    The store's logger is built outside the standard hierarchy, so its records never
    reach the root handler caplog installs.
    """
    import logging

    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Capture()
    cs.logger.addHandler(handler)
    previous = cs.logger.level
    cs.logger.setLevel(logging.DEBUG)
    # setup_logger builds its loggers with CustomLogger(name) rather than getLogger, so
    # they are not in the manager's hierarchy - and Logger.setLevel only invalidates the
    # is-enabled cache *through* the manager. Without this the logger keeps answering
    # "DEBUG is off" from a cache entry made while it was at INFO.
    cs.logger._cache.clear()
    try:
        yield messages
    finally:
        cs.logger.removeHandler(handler)
        cs.logger.setLevel(previous)


# --------------------------------------------------------------------------- #
# Per-check cookies must not be persisted for replay
# --------------------------------------------------------------------------- #
def test_per_check_cookies_are_not_stored():
    """The IP/timestamp trio describes one check and must not outlive it."""
    _store(
        [
            _Cookie("__ddg1_", "clearance"),
            _Cookie("__ddg2_", "clearance2"),
            _Cookie("__ddg8_", "opaque"),
            _Cookie("__ddg9_", "203.0.113.7"),
            _Cookie("__ddg10_", "1786826304"),
            _Cookie("ddg_last_challenge", "1786826304"),
        ]
    )

    stored = ib.get_cf_cookies_for_domain("annas-archive.gl")

    assert set(stored) == {"__ddg1_", "__ddg2_"}
    for ephemeral in ("__ddg8_", "__ddg9_", "__ddg10_", "ddg_last_challenge"):
        assert ephemeral not in stored


def test_aa_check_cookie_is_stored():
    """Without aa_ddg_check the ?check=1 hop loops, whatever __ddg* is replayed."""
    _store([_Cookie("__ddg1_", "a"), _Cookie("__ddg5_", "b"), _Cookie("aa_ddg_check", "ok")])

    stored = ib.get_cf_cookies_for_domain("annas-archive.gl")

    assert stored == {"__ddg1_": "a", "__ddg5_": "b", "aa_ddg_check": "ok"}


def test_clearance_cookies_survive():
    _store([_Cookie("__ddg1_", "a"), _Cookie("__ddg2_", "b"), _Cookie("__ddgid_", "c")])

    stored = ib.get_cf_cookies_for_domain("annas-archive.gl")

    assert stored == {"__ddg1_": "a", "__ddg2_": "b", "__ddgid_": "c"}


def test_cloudflare_cookies_are_unaffected():
    _store([_Cookie("cf_clearance", "token"), _Cookie("__cf_bm", "bm")])

    stored = ib.get_cf_cookies_for_domain("annas-archive.gl")

    assert stored == {"cf_clearance": "token", "__cf_bm": "bm"}


def test_per_check_cookies_are_excluded_even_for_full_session_domains(monkeypatch):
    """extract_all exists for Z-Library sessions; it must not resurrect the trio."""
    monkeypatch.setattr(cs, "_get_full_cookie_domains", lambda: {"annas-archive.gl"})
    _store([_Cookie("sessionid", "s"), _Cookie("__ddg9_", "203.0.113.7")])

    stored = ib.get_cf_cookies_for_domain("annas-archive.gl")

    assert "sessionid" in stored
    assert "__ddg9_" not in stored


# --------------------------------------------------------------------------- #
# Expiry must be honoured for every cookie, not only cf_clearance
# --------------------------------------------------------------------------- #
def test_expired_ddg_cookies_are_dropped():
    """The old code only expiry-checked cf_clearance, so DDoS-Guard domains - which
    have none - replayed dead cookies forever."""
    past = int(time.time()) - 60
    _store([_Cookie("__ddg1_", "live"), _Cookie("__ddgmark_", "dead", expires=past)])

    stored = ib.get_cf_cookies_for_domain("annas-archive.gl")

    assert stored == {"__ddg1_": "live"}


def test_all_cookies_expired_returns_empty_so_caller_re_solves():
    past = int(time.time()) - 60
    _store([_Cookie("__ddg1_", "dead", expires=past)])

    assert ib.get_cf_cookies_for_domain("annas-archive.gl") == {}
    assert cs.has_valid_cf_cookies("annas-archive.gl") is False


def test_unexpired_cookies_are_kept():
    future = int(time.time()) + 3600
    _store([_Cookie("__ddg1_", "live", expires=future)])

    assert ib.get_cf_cookies_for_domain("annas-archive.gl") == {"__ddg1_": "live"}


def test_session_cookies_never_expire():
    """expires<=0 means a session cookie, not an already-expired one."""
    _store([_Cookie("__ddg5_", "s", expires=0), _Cookie("__ddg1_", "a", expires=None)])

    assert ib.get_cf_cookies_for_domain("annas-archive.gl") == {"__ddg5_": "s", "__ddg1_": "a"}


def test_expired_cf_clearance_still_drops_the_whole_domain():
    """Pre-existing Cloudflare behaviour must not regress."""
    past = int(time.time()) - 60
    _store([_Cookie("cf_clearance", "dead", expires=past), _Cookie("__cf_bm", "bm")])

    assert ib.get_cf_cookies_for_domain("annas-archive.gl") == {}


def test_expired_cookies_are_pruned_from_the_store():
    """A dropped cookie must not linger and be re-evaluated on every request."""
    past = int(time.time()) - 60
    _store([_Cookie("__ddg1_", "live"), _Cookie("__ddgmark_", "dead", expires=past)])

    ib.get_cf_cookies_for_domain("annas-archive.gl")

    assert set(cs._cf_cookies["annas-archive.gl"]) == {"__ddg1_"}


def test_solve_that_yields_only_per_check_cookies_stores_nothing():
    """No clearance means no reuse - the caller must go back to the bypasser rather
    than believe it holds a valid session."""
    _store([_Cookie("__ddg9_", "203.0.113.7"), _Cookie("__ddg10_", "1786826304")])

    assert ib.get_cf_cookies_for_domain("annas-archive.gl") == {}


# --------------------------------------------------------------------------- #
# Rejected cookies are discarded, never retried forever
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, status_code, text="page"):
        self.status_code = status_code
        self.text = text


def _seed(monkeypatch):
    _store([_Cookie("__ddg1_", "clearance"), _Cookie("__ddg2_", "c2")])
    monkeypatch.setattr(ib, "get_proxies", lambda _url: None)
    monkeypatch.setattr(ib, "get_ssl_verify", lambda _url: True)
    assert ib.get_cf_cookies_for_domain("annas-archive.gl")


def test_rejected_cached_cookies_are_discarded(monkeypatch):
    """A 403 while presenting cookies proves they are dead - keep them and every
    later request re-presents a known-rejected cookie."""
    _seed(monkeypatch)
    monkeypatch.setattr(ib.requests, "get", lambda *a, **k: _Resp(403))

    assert (
        ib._try_with_cached_cookies("https://annas-archive.gl/search", "annas-archive.gl") is None
    )
    assert ib.get_cf_cookies_for_domain("annas-archive.gl") == {}


def test_redirect_loop_on_cached_cookies_discards_them(monkeypatch):
    """DDoS-Guard answers dead clearance with an endless ?check=1 bounce, which
    surfaces as an exception rather than a status code."""
    _seed(monkeypatch)

    def boom(*_a, **_k):
        raise ib.requests.exceptions.TooManyRedirects("Exceeded 30 redirects")

    monkeypatch.setattr(ib.requests, "get", boom)

    assert (
        ib._try_with_cached_cookies("https://annas-archive.gl/search", "annas-archive.gl") is None
    )
    assert ib.get_cf_cookies_for_domain("annas-archive.gl") == {}


def test_working_cookies_are_kept(monkeypatch):
    _seed(monkeypatch)
    monkeypatch.setattr(ib.requests, "get", lambda *a, **k: _Resp(200, "the page"))

    result = ib._try_with_cached_cookies("https://annas-archive.gl/search", "annas-archive.gl")

    assert result == "the page"
    assert ib.get_cf_cookies_for_domain("annas-archive.gl") != {}


def test_failure_only_clears_the_failing_host(monkeypatch):
    """clear_cf_cookies('') means every host - a blank hostname must not wipe
    clearance for sites that are working fine."""
    _seed(monkeypatch)
    _store([_Cookie("__ddg1_", "other")], url="https://other-site.test/x")
    monkeypatch.setattr(ib.requests, "get", lambda *a, **k: _Resp(403))

    ib._try_with_cached_cookies("https://annas-archive.gl/search", "annas-archive.gl")

    assert ib.get_cf_cookies_for_domain("annas-archive.gl") == {}
    assert ib.get_cf_cookies_for_domain("other-site.test") == {"__ddg1_": "other"}


# --------------------------------------------------------------------------- #
# Settling what DDoS-Guard actually treats as clearance (issue #1276)
# --------------------------------------------------------------------------- #
def test_per_check_cookies_can_be_kept_for_a_field_test(monkeypatch):
    """Which __ddg* cookies are clearance is not settled, so it has to be testable.

    The store's premise - that __ddg8_/__ddg9_/__ddg10_ describe one check and must not
    be replayed - is contradicted by the field reports on #1276, where every request
    after a successful solve was challenged again. This env-only switch is how that gets
    answered against a live host without building a branch.
    """
    from shelfmark.config import env

    monkeypatch.setattr(env, "DDG_REPLAY_PER_CHECK_COOKIES", True)
    _store(
        [
            _Cookie("__ddg1_", "clearance"),
            _Cookie("__ddg8_", "opaque"),
            _Cookie("__ddg9_", "203.0.113.7"),
            _Cookie("__ddg10_", "1786826304"),
        ]
    )

    stored = ib.get_cf_cookies_for_domain("annas-archive.gl")

    assert set(stored) == {"__ddg1_", "__ddg8_", "__ddg9_", "__ddg10_"}


def test_dropping_per_check_cookies_is_the_default(monkeypatch):
    """The switch is for reproducing the question, not a behaviour change."""
    from shelfmark.config import env

    assert env.DDG_REPLAY_PER_CHECK_COOKIES is False
    _store([_Cookie("__ddg1_", "clearance"), _Cookie("__ddg9_", "203.0.113.7")])

    assert set(ib.get_cf_cookies_for_domain("annas-archive.gl")) == {"__ddg1_"}


def test_a_solve_logs_which_cookies_it_won_and_which_were_held_back(cookie_store_logs):
    """Without this, a debug log shows a solve succeed and the next request challenged,
    with nothing in between to explain why."""
    _store([_Cookie("__ddg1_", "clearance"), _Cookie("__ddg9_", "203.0.113.7")])

    messages = cookie_store_logs
    line = next((m for m in messages if "won" in m and "dropping" in m), None)
    assert line is not None, messages
    assert "__ddg1_" in line
    assert "__ddg9_" in line
    # Names only - a clearance cookie's value is a credential.
    assert "clearance" not in line
    assert "203.0.113.7" not in line
