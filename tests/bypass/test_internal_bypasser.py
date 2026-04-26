import asyncio

import pytest


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


def test_extract_cookies_from_cdp_keeps_full_session_cookies_for_configured_zlib_domains(
    monkeypatch,
):
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
                FakeCookie("cf_clearance", "abc", "z-lib.fm", "/", int(time.time()) + 3600),
                FakeCookie("sessionid", "zzz", "z-lib.fm", "/", int(time.time()) + 3600),
            ]

    class FakeDriver:
        cookies = FakeCookies()

    class FakePage:
        async def evaluate(self, _expr):
            return "TestUA/1.0"

    monkeypatch.setattr(internal_bypasser, "_get_full_cookie_domains", lambda: {"z-lib.fm"})

    internal_bypasser.clear_cf_cookies()
    asyncio.run(
        internal_bypasser._extract_cookies_from_cdp(
            FakeDriver(),
            FakePage(),
            "https://z-lib.fm/books/example",
        )
    )

    cookies = internal_bypasser.get_cf_cookies_for_domain("z-lib.fm")
    assert cookies == {"cf_clearance": "abc", "sessionid": "zzz"}


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


def test_get_page_info_returns_safe_defaults_on_cdp_errors():
    from seleniumbase.undetected.cdp_driver.connection import ProtocolException

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    class FakePage:
        async def get_title(self):
            raise ProtocolException("no title")

        async def evaluate(self, _expr):
            raise ProtocolException("no body")

        async def get_current_url(self):
            raise ProtocolException("no url")

    title, body, current_url = asyncio.run(internal_bypasser._get_page_info(FakePage()))

    assert title == ""
    assert body == ""
    assert current_url == ""


def test_create_cdp_browser_times_out_and_cleans_up(monkeypatch):
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    async def _never_start(*_args, **_kwargs):
        await asyncio.Event().wait()

    cleanup_calls = []

    monkeypatch.setattr(internal_bypasser, "_BROWSER_START_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(internal_bypasser.cdp_driver, "start_async", _never_start)
    monkeypatch.setattr(internal_bypasser, "_get_browser_args", lambda: [])
    monkeypatch.setattr(internal_bypasser, "get_screen_size", lambda: (1280, 800))
    monkeypatch.setattr(internal_bypasser, "_get_proxy_string", lambda _url: None)
    monkeypatch.setattr(internal_bypasser.env, "DOCKERMODE", True)
    monkeypatch.setattr(
        internal_bypasser,
        "_cleanup_orphan_processes",
        lambda: cleanup_calls.append("cleanup") or 1,
    )

    with pytest.raises(TimeoutError):
        asyncio.run(internal_bypasser._create_cdp_browser("https://example.com"))

    assert cleanup_calls == ["cleanup"]


def test_create_cdp_browser_wraps_plain_startup_exception_and_cleans_up(monkeypatch):
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    async def _fail_to_start(*_args, **_kwargs):
        raise Exception("Failed to connect to the browser")

    cleanup_calls = []

    monkeypatch.setattr(internal_bypasser.cdp_driver, "start_async", _fail_to_start)
    monkeypatch.setattr(internal_bypasser, "_get_browser_args", lambda: [])
    monkeypatch.setattr(internal_bypasser, "get_screen_size", lambda: (1280, 800))
    monkeypatch.setattr(internal_bypasser, "_get_proxy_string", lambda _url: None)
    monkeypatch.setattr(internal_bypasser.env, "DOCKERMODE", True)
    monkeypatch.setattr(
        internal_bypasser,
        "_cleanup_orphan_processes",
        lambda: cleanup_calls.append("cleanup") or 1,
    )

    with pytest.raises(RuntimeError, match="Pure CDP browser startup failed"):
        asyncio.run(internal_bypasser._create_cdp_browser("https://example.com"))

    assert cleanup_calls == ["cleanup"]


def test_run_child_process_writes_failure_for_unexpected_exception(monkeypatch, tmp_path):
    import io
    import json

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    result_path = tmp_path / "result.json"
    request = {
        "url": "https://example.com",
        "retry": 1,
        "result_path": str(result_path),
    }

    def _raise_unexpected(*_args, **_kwargs):
        raise Exception("plain SeleniumBase startup failure")

    monkeypatch.setattr(internal_bypasser, "get", _raise_unexpected)
    monkeypatch.setattr(internal_bypasser.sys, "stdin", io.StringIO(json.dumps(request)))

    assert internal_bypasser._run_child_process() == 1

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert result["error_type"] == "Exception"
    assert result["error"] == "plain SeleniumBase startup failure"
    assert "plain SeleniumBase startup failure" in result["traceback"]


def test_prepare_child_browser_env_uses_writable_runtime_paths(monkeypatch, tmp_path):
    import stat

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    home_dir = tmp_path / "browser" / "home"
    runtime_dir = tmp_path / "browser" / "runtime"
    monkeypatch.setattr(internal_bypasser, "BROWSER_HOME_DIR", home_dir)
    monkeypatch.setattr(internal_bypasser, "BROWSER_XDG_RUNTIME_DIR", runtime_dir)

    env = internal_bypasser._prepare_child_browser_env({"HOME": "/app"})

    assert env["HOME"] == str(home_dir)
    assert env["XDG_CONFIG_HOME"] == str(home_dir / ".config")
    assert env["XDG_CACHE_HOME"] == str(home_dir / ".cache")
    assert env["XDG_RUNTIME_DIR"] == str(runtime_dir)
    assert home_dir.is_dir()
    assert (home_dir / ".config").is_dir()
    assert (home_dir / ".cache").is_dir()
    assert stat.S_IMODE(runtime_dir.stat().st_mode) == stat.S_IRWXU


def test_try_with_cached_cookies_returns_none_on_request_exception(monkeypatch):
    import time

    import requests

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    internal_bypasser.clear_cf_cookies()
    internal_bypasser._cf_cookies["example.com"] = {
        "cf_clearance": {
            "value": "abc",
            "domain": "example.com",
            "path": "/",
            "expiry": int(time.time()) + 3600,
            "secure": True,
            "httpOnly": True,
        }
    }

    def _raise(*_args, **_kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(internal_bypasser.requests, "get", _raise)

    assert internal_bypasser._try_with_cached_cookies("https://example.com", "example.com") is None


def test_get_bypassed_page_retries_next_mirror_after_runtime_error(monkeypatch):
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    class FakeSelector:
        def __init__(self):
            self.urls = ["https://mirror-one.example/book", "https://mirror-two.example/book"]
            self.index = 0

        def rewrite(self, _url):
            return self.urls[self.index]

        def next_mirror_or_rotate_dns(self, *, allow_dns=True):
            del allow_dns
            self.index = 1
            return "https://mirror-two.example", "mirror"

    calls: list[str] = []

    def _fake_get(url, retry=None, cancel_flag=None):
        del retry, cancel_flag
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("browser hiccup")
        return "<html>ok</html>"

    monkeypatch.setattr(
        internal_bypasser, "_try_with_cached_cookies", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(internal_bypasser, "get", _fake_get)

    selector = FakeSelector()
    result = internal_bypasser.get_bypassed_page("https://orig.example/book", selector=selector)

    assert result == "<html>ok</html>"
    assert calls == [
        "https://mirror-one.example/book",
        "https://mirror-two.example/book",
    ]
