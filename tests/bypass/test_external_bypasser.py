"""Tests for the external bypasser flow."""


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
