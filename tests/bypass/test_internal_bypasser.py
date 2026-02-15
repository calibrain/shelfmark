import asyncio


def test_bypass_tries_all_methods_before_abort(monkeypatch):
    """Regression test for issue #524: don't abort before cycling through bypass methods."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    calls: list[str] = []

    def _make_method(name: str):
        async def _method(_sb) -> bool:
            calls.append(name)
            return False

        _method.__name__ = name
        return _method

    methods = [_make_method(f"m{i}") for i in range(6)]

    async def _always_false(*_args, **_kwargs) -> bool:
        return False

    async def _always_ddos_guard(*_args, **_kwargs) -> str:
        return "ddos_guard"

    async def _no_sleep(_seconds) -> None:
        return None

    monkeypatch.setattr(internal_bypasser, "BYPASS_METHODS", methods)
    monkeypatch.setattr(internal_bypasser, "_is_bypassed", _always_false)
    monkeypatch.setattr(internal_bypasser, "_detect_challenge_type", _always_ddos_guard)
    monkeypatch.setattr(internal_bypasser.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(internal_bypasser.random, "uniform", lambda _a, _b: 0)

    assert asyncio.run(internal_bypasser._bypass(object(), max_retries=10)) is False
    assert calls == [f"m{i}" for i in range(6)]


def test_extract_cookies_from_cdp_filters_and_stores_ua():
    import time
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    class FakeCookie:
        def __init__(self, name, value, domain, path, expires, secure=True):
            self.name = name
            self.value = value
            self.domain = domain
            self.path = path
            self.expires = expires
            self.secure = secure

    class FakeCookies:
        async def get_all(self, requests_cookie_format=False):
            assert requests_cookie_format is True
            return [
                FakeCookie("cf_clearance", "abc", "example.com", "/", int(time.time()) + 3600),
                FakeCookie("sessionid", "zzz", "example.com", "/", int(time.time()) + 3600),
            ]

    class FakeDriver:
        cookies = FakeCookies()

    class FakePage:
        async def evaluate(self, _expr):
            return "TestUA/1.0"

    internal_bypasser.clear_cf_cookies()
    asyncio.run(
        internal_bypasser._extract_cookies_from_cdp(
            FakeDriver(),
            FakePage(),
            "https://www.example.com/path",
        )
    )

    cookies = internal_bypasser.get_cf_cookies_for_domain("example.com")
    assert cookies == {"cf_clearance": "abc"}
    assert internal_bypasser.get_cf_user_agent_for_domain("example.com") == "TestUA/1.0"


def test_extract_cookies_from_cdp_normalizes_session_expiry():
    import time
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    class FakeCookie:
        def __init__(self, name, value, domain, path, expires, secure=True):
            self.name = name
            self.value = value
            self.domain = domain
            self.path = path
            self.expires = expires
            self.secure = secure

    class FakeCookies:
        async def get_all(self, requests_cookie_format=False):
            assert requests_cookie_format is True
            return [
                FakeCookie("cf_clearance", "abc", "example.com", "/", 0),
            ]

    class FakeDriver:
        cookies = FakeCookies()

    class FakePage:
        async def evaluate(self, _expr):
            return "TestUA/1.0"

    internal_bypasser.clear_cf_cookies()
    asyncio.run(
        internal_bypasser._extract_cookies_from_cdp(
            FakeDriver(),
            FakePage(),
            "https://example.com",
        )
    )

    stored = internal_bypasser._cf_cookies.get("example.com", {})
    assert stored["cf_clearance"]["expiry"] is None
    assert internal_bypasser.get_cf_cookies_for_domain("example.com") == {"cf_clearance": "abc"}

    # Verify fallback to "expires" key for expiry checks
    internal_bypasser._cf_cookies["example.com"]["cf_clearance"]["expires"] = int(time.time()) - 10
    assert internal_bypasser.get_cf_cookies_for_domain("example.com") == {}
