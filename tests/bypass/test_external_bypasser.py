"""Tests for the external bypasser flow."""

import pytest


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_fetch_via_bypasser_posts_expected_payload_and_uses_ssl_verify(monkeypatch):
    import shelfmark.bypass.external_bypasser as external_bypasser

    calls: list[dict] = []

    def fake_get(key, default=""):
        values = {
            "EXT_BYPASSER_URL": "https://bypass.example",
            "EXT_BYPASSER_PATH": "/v1",
            "EXT_BYPASSER_TIMEOUT": 60000,
        }
        return values.get(key, default)

    def fake_post(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return _FakeResponse(
            {
                "status": "ok",
                "message": "done",
                "solution": {"response": "<html>ok</html>"},
            }
        )

    monkeypatch.setattr(external_bypasser.config, "get", fake_get)
    monkeypatch.setattr(external_bypasser.requests, "post", fake_post)
    monkeypatch.setattr(external_bypasser, "get_ssl_verify", lambda _url: False)

    assert external_bypasser._fetch_via_bypasser("https://example.com/book") == "<html>ok</html>"
    assert calls == [
        {
            "url": "https://bypass.example/v1",
            "headers": {"Content-Type": "application/json"},
            "json": {
                "cmd": "request.get",
                "url": "https://example.com/book",
                "maxTimeout": 60000,
            },
            "timeout": (10, 75.0),
            "verify": False,
        }
    ]


def _stub_solution(monkeypatch, external_bypasser, solution: dict) -> None:
    """Answer one bypass with `solution`, with config and SSL stubbed out."""

    def fake_get(key, default=""):
        values = {
            "EXT_BYPASSER_URL": "https://bypass.example",
            "EXT_BYPASSER_PATH": "/v1",
            "EXT_BYPASSER_TIMEOUT": 60000,
        }
        return values.get(key, default)

    monkeypatch.setattr(external_bypasser.config, "get", fake_get)
    monkeypatch.setattr(
        external_bypasser.requests,
        "post",
        lambda *_a, **_k: _FakeResponse({"status": "ok", "solution": solution}),
    )
    monkeypatch.setattr(external_bypasser, "get_ssl_verify", lambda _url: False)


def test_solved_clearance_is_stored_for_reuse(monkeypatch):
    """A solve costs tens of seconds of real browser; its clearance must be kept.

    Without this every request paid a 403 plus a full solve, and the file download -
    which the solver cannot proxy - presented no clearance at all.
    """
    import shelfmark.bypass.cookie_store as cookie_store
    import shelfmark.bypass.external_bypasser as external_bypasser

    monkeypatch.setattr(cookie_store, "_cf_cookies", {})
    monkeypatch.setattr(cookie_store, "_cf_user_agents", {})
    _stub_solution(
        monkeypatch,
        external_bypasser,
        {
            "response": "<html>ok</html>",
            "userAgent": "Mozilla/5.0 (solver)",
            "cookies": [
                {"name": "__ddg1_", "value": "clearance", "domain": ".annas-archive.gl"},
                {"name": "__ddg2_", "value": "c2", "domain": ".annas-archive.gl"},
                # Per-check cookies: kept out of the store, same as the internal path.
                {"name": "__ddg9_", "value": "203.0.113.7", "domain": ".annas-archive.gl"},
            ],
        },
    )

    external_bypasser._fetch_via_bypasser("https://annas-archive.gl/search?q=dune")

    assert cookie_store.get_cf_cookies_for_domain("annas-archive.gl") == {
        "__ddg1_": "clearance",
        "__ddg2_": "c2",
    }
    # Cloudflare ties clearance to the solving UA, so replaying one without the other fails.
    assert cookie_store.get_cf_user_agent_for_domain("annas-archive.gl") == "Mozilla/5.0 (solver)"


@pytest.mark.parametrize(
    ("field", "shape"),
    [
        # Byparr drives Playwright/camoufox, whose cookies spell it "expires".
        ("expires", "playwright"),
        # FlareSolverr assigns driver.get_cookies() - the WebDriver cookie object,
        # which spells it "expiry". Reading only "expires" made every FlareSolverr
        # cookie immortal, so dead clearance was replayed forever.
        ("expiry", "webdriver"),
    ],
)
def test_expired_solution_cookie_is_not_replayed(monkeypatch, field, shape):
    import time

    import shelfmark.bypass.cookie_store as cookie_store
    import shelfmark.bypass.external_bypasser as external_bypasser

    monkeypatch.setattr(cookie_store, "_cf_cookies", {})
    monkeypatch.setattr(cookie_store, "_cf_user_agents", {})
    _stub_solution(
        monkeypatch,
        external_bypasser,
        {
            "response": "<html>ok</html>",
            "cookies": [{"name": "__ddg1_", "value": "dead", field: int(time.time()) - 60}],
        },
    )

    external_bypasser._fetch_via_bypasser("https://annas-archive.gl/search?q=dune")

    assert cookie_store.get_cf_cookies_for_domain("annas-archive.gl") == {}, (
        f"a dead {shape} cookie was kept for replay"
    )


def test_solution_cookie_expiry_is_coerced_not_trusted(monkeypatch):
    """The solver is not ours; a stringified expiry must be read, not raised on."""
    import time

    import shelfmark.bypass.cookie_store as cookie_store
    import shelfmark.bypass.external_bypasser as external_bypasser

    monkeypatch.setattr(cookie_store, "_cf_cookies", {})
    monkeypatch.setattr(cookie_store, "_cf_user_agents", {})
    _stub_solution(
        monkeypatch,
        external_bypasser,
        {
            "response": "<html>ok</html>",
            "cookies": [
                {"name": "__ddg1_", "value": "live", "expires": str(int(time.time()) + 3600)},
                {"name": "__ddg2_", "value": "dead", "expires": str(int(time.time()) - 60)},
            ],
        },
    )

    result = external_bypasser._fetch_via_bypasser("https://annas-archive.gl/search?q=dune")

    assert result == "<html>ok</html>"
    assert cookie_store.get_cf_cookies_for_domain("annas-archive.gl") == {"__ddg1_": "live"}


def test_storing_clearance_can_never_discard_the_solved_page(monkeypatch):
    """A solve costs ~30s; a surprise in the cookie shape must not throw it away.

    The store call sits inside the request try/except, whose handler returns None -
    so without its own guard a raising store turned a good page into a failed fetch
    and sent the caller round for up to MAX_RETRY more solves.
    """
    import shelfmark.bypass.external_bypasser as external_bypasser

    _stub_solution(
        monkeypatch,
        external_bypasser,
        {"response": "<html>ok</html>", "cookies": [{"name": "__ddg1_", "value": "v"}]},
    )

    def boom(*_args, **_kwargs):
        raise TypeError("unexpected cookie shape")

    monkeypatch.setattr(external_bypasser, "store_extracted_cookies", boom)

    assert (
        external_bypasser._fetch_via_bypasser("https://annas-archive.gl/search?q=dune")
        == "<html>ok</html>"
    )


def test_solution_without_cookies_is_still_returned(monkeypatch):
    """A solver that returns no cookie list must not break the page fetch."""
    import shelfmark.bypass.cookie_store as cookie_store
    import shelfmark.bypass.external_bypasser as external_bypasser

    monkeypatch.setattr(cookie_store, "_cf_cookies", {})
    monkeypatch.setattr(cookie_store, "_cf_user_agents", {})
    _stub_solution(monkeypatch, external_bypasser, {"response": "<html>ok</html>"})

    result = external_bypasser._fetch_via_bypasser("https://annas-archive.gl/search?q=dune")

    assert result == "<html>ok</html>"
    assert cookie_store.get_cf_cookies_for_domain("annas-archive.gl") == {}


def test_get_bypassed_page_retries_and_rotates_selector_between_attempts(monkeypatch):
    import shelfmark.bypass.external_bypasser as external_bypasser

    class FakeRng:
        def random(self) -> float:
            return 0.0

    class FakeSelector:
        def __init__(self) -> None:
            self.current_base = "https://mirror-one.example"
            self.rewrite_calls: list[str] = []
            self.rotate_calls = 0

        def rewrite(self, url: str) -> str:
            self.rewrite_calls.append(url)
            return url.replace("https://orig.example", self.current_base, 1)

        def next_mirror_or_rotate_dns(self) -> tuple[str | None, str]:
            self.rotate_calls += 1
            self.current_base = "https://mirror-two.example"
            return self.current_base, "mirror"

    fetch_calls: list[str] = []
    sleeps: list[float] = []
    responses = [None, "<html>ok</html>"]

    def fake_fetch(url: str) -> str | None:
        fetch_calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(external_bypasser, "_fetch_via_bypasser", fake_fetch)
    monkeypatch.setattr(
        external_bypasser, "_sleep_with_cancellation", lambda seconds, _flag: sleeps.append(seconds)
    )
    monkeypatch.setattr(external_bypasser, "_RNG", FakeRng())

    selector = FakeSelector()
    result = external_bypasser.get_bypassed_page("https://orig.example/book", selector=selector)

    assert result == "<html>ok</html>"
    assert fetch_calls == [
        "https://mirror-one.example/book",
        "https://mirror-two.example/book",
    ]
    assert selector.rotate_calls == 1
    assert sleeps == [1.0]


def _stub_ext_bypasser_timeout(monkeypatch, external_bypasser, value: int) -> None:
    """Override only EXT_BYPASSER_TIMEOUT on the shared config singleton."""
    real_get = external_bypasser.config.get
    monkeypatch.setattr(
        external_bypasser.config,
        "get",
        lambda key, default="": value if key == "EXT_BYPASSER_TIMEOUT" else real_get(key, default),
    )


def test_max_duration_seconds_covers_every_attempt_and_backoff(monkeypatch):
    """The declared budget must not undercut what get_bypassed_page() can actually take."""
    import shelfmark.bypass.external_bypasser as external_bypasser

    _stub_ext_bypasser_timeout(monkeypatch, external_bypasser, 60000)

    budget = external_bypasser.max_duration_seconds()

    # 5 attempts at min(60 + 15, 120) = 75s, plus the 1+2+4+8 backoff and its jitter.
    assert budget == 5 * 75.0 + (1 + 1) + (2 + 1) + (4 + 1) + (8 + 1)


def test_max_duration_seconds_respects_the_read_timeout_ceiling(monkeypatch):
    import shelfmark.bypass.external_bypasser as external_bypasser

    _stub_ext_bypasser_timeout(monkeypatch, external_bypasser, 300000)

    budget = external_bypasser.max_duration_seconds()

    # 300s + 15s buffer is clamped to MAX_READ_TIMEOUT, not used raw.
    assert budget == 5 * external_bypasser.MAX_READ_TIMEOUT + 19.0
