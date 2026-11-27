import asyncio
import errno
import json
import os
import threading
from pathlib import Path

import pytest

from shelfmark.download.network import get_session as _shared_session


def test_diamwall_is_detected_until_the_challenge_clears():
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    class DiamWallPage:
        async def get_title(self):
            return "DiamWall"

        async def evaluate(self, _expression):
            return "Please wait while DiamWall checks your browser"

        async def get_current_url(self):
            return "https://example.com"

    assert asyncio.run(internal_bypasser._detect_challenge_type(DiamWallPage())) == "other"
    assert not internal_bypasser._is_bypassed_content(
        "DiamWall",
        "Please wait while DiamWall checks your browser",
        "https://example.com",
    )


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

    from shelfmark.bypass import cookie_store

    monkeypatch.setattr(cookie_store, "_get_full_cookie_domains", lambda: {"z-lib.fm"})

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

    from shelfmark.bypass import cookie_store

    stored = cookie_store._cf_cookies.get("example.com", {})
    assert stored["cf_clearance"]["expiry"] is None
    assert internal_bypasser.get_cf_cookies_for_domain("example.com") == {"cf_clearance": "abc"}

    # Verify fallback to "expires" key for expiry checks
    cookie_store._cf_cookies["example.com"]["cf_clearance"]["expires"] = int(time.time()) - 10
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


def _stub_browser_start(monkeypatch, *, setting, euid, start):
    """Run `_create_cdp_browser` against a fake browser; return the start arguments."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    started = []

    async def _start(*_args, **kwargs):
        started.append(kwargs)
        return await start()

    def _get(key, default=None):
        """Answer the sandbox setting, skip the startup sleep, default the rest."""
        if key == "BYPASS_BROWSER_SANDBOX":
            return setting
        return 0 if key == "DEFAULT_SLEEP" else default

    monkeypatch.setattr(internal_bypasser.app_config, "get", _get)
    monkeypatch.setattr(internal_bypasser.os, "geteuid", lambda: euid)
    monkeypatch.setattr(internal_bypasser.cdp_driver, "start_async", _start)
    monkeypatch.setattr(internal_bypasser, "_get_browser_args", lambda: [])
    monkeypatch.setattr(internal_bypasser, "get_screen_size", lambda: (1280, 800))
    monkeypatch.setattr(internal_bypasser, "_get_proxy_string", lambda _url: None)
    monkeypatch.setattr(internal_bypasser.env, "DOCKERMODE", False)
    return started


@pytest.mark.parametrize(
    ("setting", "euid", "expected"),
    [(False, 1000, False), (True, 1000, True), (True, 0, False), (False, 0, False)],
)
def test_browser_sandbox_follows_setting_unless_root(monkeypatch, setting, euid, expected):
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    monkeypatch.setattr(internal_bypasser.app_config, "get", lambda *_args: setting)
    monkeypatch.setattr(internal_bypasser.os, "geteuid", lambda: euid)

    assert internal_bypasser._use_browser_sandbox() is expected


@pytest.mark.parametrize(("setting", "euid"), [(False, 1000), (True, 1000), (True, 0)])
def test_create_cdp_browser_passes_sandbox_choice_to_the_driver(monkeypatch, setting, euid):
    """The driver gets the sandbox choice, and only a sandboxed failure is explained."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    async def _fail():
        raise Exception("Failed to connect to the browser")

    started = _stub_browser_start(monkeypatch, setting=setting, euid=euid, start=_fail)
    explained = []
    monkeypatch.setattr(
        internal_bypasser, "_log_sandbox_startup_failure", lambda: explained.append(True)
    )

    with pytest.raises(RuntimeError, match="Pure CDP browser startup failed"):
        asyncio.run(internal_bypasser._create_cdp_browser("https://example.com"))

    sandboxed = setting and euid != 0
    assert started[0]["sandbox"] is sandboxed
    assert explained == ([True] if sandboxed else [])


def test_create_cdp_browser_cleans_up_before_explaining_off_the_event_loop(monkeypatch):
    """Leftover processes go first, and the namespace check does not block the loop."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    async def _fail():
        raise Exception("Failed to connect to the browser")

    _stub_browser_start(monkeypatch, setting=True, euid=1000, start=_fail)
    monkeypatch.setattr(internal_bypasser.env, "DOCKERMODE", True)
    steps = []
    monkeypatch.setattr(
        internal_bypasser, "_cleanup_orphan_processes", lambda: steps.append(("cleanup", None))
    )
    monkeypatch.setattr(
        internal_bypasser,
        "_log_sandbox_startup_failure",
        lambda: steps.append(("explain", threading.get_ident())),
    )

    with pytest.raises(RuntimeError, match="Pure CDP browser startup failed"):
        asyncio.run(internal_bypasser._create_cdp_browser("https://example.com"))

    assert [name for name, _thread in steps] == ["cleanup", "explain"]
    assert steps[1][1] != threading.get_ident()


def test_create_cdp_browser_explains_sandbox_startup_timeout(monkeypatch):
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    async def _hang():
        await asyncio.Event().wait()

    _stub_browser_start(monkeypatch, setting=True, euid=1000, start=_hang)
    monkeypatch.setattr(internal_bypasser, "_BROWSER_START_TIMEOUT_SECONDS", 0.01)
    explained = []
    monkeypatch.setattr(
        internal_bypasser, "_log_sandbox_startup_failure", lambda: explained.append(True)
    )

    with pytest.raises(TimeoutError):
        asyncio.run(internal_bypasser._create_cdp_browser("https://example.com"))

    assert explained == [True]


@pytest.mark.parametrize(
    ("available", "names_profile"), [(False, True), (True, False), (None, False)]
)
def test_sandbox_startup_failure_names_the_profile_only_when_namespaces_are_refused(
    monkeypatch, available, names_profile
):
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    errors = []
    monkeypatch.setattr(internal_bypasser, "_can_create_sandbox_namespaces", lambda: available)
    monkeypatch.setattr(internal_bypasser.logger, "error", errors.append)

    internal_bypasser._log_sandbox_startup_failure()

    assert len(errors) == 1
    assert ("shelfmark-browser-seccomp.json" in errors[0]) is names_profile
    assert ("host restricts user namespaces" in errors[0]) is names_profile
    assert "Run Browser with Sandbox" in errors[0]


@pytest.mark.parametrize(("returncode", "expected"), [(0, True), (3, False), (1, None), (-9, None)])
def test_sandbox_namespace_check_reports_the_child_result(monkeypatch, returncode, expected):
    import subprocess

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    monkeypatch.setattr(
        internal_bypasser.subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, returncode),
    )

    assert internal_bypasser._can_create_sandbox_namespaces() is expected


# The child code reads the `os.CLONE_*` flags, which only Linux has.
@pytest.mark.skipif(not hasattr(os, "unshare"), reason="needs Linux os.unshare")
@pytest.mark.parametrize(
    ("error", "refused"), [(errno.EPERM, True), (errno.EACCES, True), (errno.ENOMEM, False)]
)
def test_sandbox_namespace_check_counts_only_permission_errors_as_refused(
    monkeypatch, error, refused
):
    """Run the real child code with `unshare` failing, and read its exit status."""
    import subprocess

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    real_run = subprocess.run
    results = []

    def _run_with_failing_unshare(args, **kwargs):
        stub = (
            "import os, sys\n"
            "def _fail(_flags):\n"
            "    print('stub reached', file=sys.stderr, flush=True)\n"
            f"    raise OSError({error}, 'refused')\n"
            "os.unshare = _fail\n"
        )
        result = real_run([*args[:-1], stub + args[-1]], **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(internal_bypasser.subprocess, "run", _run_with_failing_unshare)

    answer = internal_bypasser._can_create_sandbox_namespaces()

    assert answer is (False if refused else None)
    # The child got as far as the failing call, so a None answer is about that error.
    assert b"stub reached" in results[0].stderr
    assert (results[0].returncode == internal_bypasser._NAMESPACES_REFUSED_EXIT) is refused


def test_sandbox_namespace_check_is_unknown_when_it_cannot_run(monkeypatch):
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    def _cannot_run(*_args, **_kwargs):
        raise OSError("no fork")

    monkeypatch.setattr(internal_bypasser.subprocess, "run", _cannot_run)

    assert internal_bypasser._can_create_sandbox_namespaces() is None


_CLONE_NEWNS = 0x00020000
_CLONE_NEWTIME = 0x00000080
_CLONE_NEWCGROUP = 0x02000000
_CLONE_NEWUTS = 0x04000000
_CLONE_NEWIPC = 0x08000000
_CLONE_NEWUSER = 0x10000000
_CLONE_NEWPID = 0x20000000
_CLONE_NEWNET = 0x40000000
_SANDBOX_FLAGS = (_CLONE_NEWUSER, _CLONE_NEWPID, _CLONE_NEWNET)
_OTHER_FLAGS = (_CLONE_NEWNS, _CLONE_NEWCGROUP, _CLONE_NEWUTS, _CLONE_NEWIPC)


def _browser_profile() -> dict:
    """The seccomp profile shipped under compose/."""
    path = Path(__file__).parents[2] / "compose" / "shelfmark-browser-seccomp.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _allowed_without_capabilities(syscall: str, flags: int) -> bool:
    """Whether the profile lets an unprivileged x86-64 process make this call.

    `flags` is the call's first argument; the others are zero.
    """
    arguments = (flags, 0, 0, 0, 0, 0)
    for rule in _browser_profile()["syscalls"]:
        includes = rule.get("includes", {})
        if syscall not in rule["names"] or rule["action"] != "SCMP_ACT_ALLOW":
            continue
        if includes.get("caps") or "amd64" not in includes.get("arches", ["amd64"]):
            continue
        if "amd64" in rule.get("excludes", {}).get("arches", []):
            continue
        if all(
            arg["op"] == "SCMP_CMP_MASKED_EQ"
            and arguments[arg["index"]] & arg["value"] == arg.get("valueTwo", 0)
            for arg in rule.get("args", [])
        ):
            return True
    return False


@pytest.mark.parametrize("syscall", ["clone", "unshare"])
def test_browser_seccomp_profile_allows_only_the_sandbox_namespaces(syscall):
    """User, PID and network namespaces are open; every other namespace stays shut."""
    assert _allowed_without_capabilities(syscall, sum(_SANDBOX_FLAGS))
    for flag in _OTHER_FLAGS:
        assert not _allowed_without_capabilities(syscall, flag)
        assert not _allowed_without_capabilities(syscall, _CLONE_NEWUSER | flag)


def test_browser_seccomp_profile_allows_chroot_without_the_capability():
    """Chrome's sandbox calls chroot, also in a container that dropped CAP_SYS_CHROOT."""
    assert _allowed_without_capabilities("chroot", 0)


def test_browser_seccomp_profile_keeps_the_rest_of_dockers_limits():
    """No setns or time namespace, and clone3 still answers ENOSYS."""
    profile = _browser_profile()

    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert not _allowed_without_capabilities("setns", 0)
    assert not _allowed_without_capabilities("unshare", _CLONE_NEWTIME)
    # glibc falls back to clone() only while clone3() answers ENOSYS.
    assert {
        "names": ["clone3"],
        "action": "SCMP_ACT_ERRNO",
        "errnoRet": 38,
        "excludes": {"caps": ["CAP_SYS_ADMIN"]},
    } in profile["syscalls"]


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


def test_run_child_process_applies_parent_dns_config(monkeypatch, tmp_path):
    """Regression test for issue #1028: the helper subprocess must mirror the parent's
    DNS provider, otherwise it pre-resolves AA hostnames against (possibly hijacked)
    system DNS and Chrome loads the wrong page."""
    import io
    import json

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    result_path = tmp_path / "result.json"
    request = {
        "url": "https://annas-archive.pk/slow_download/abc/0/0",
        "retry": 1,
        "result_path": str(result_path),
        "dns_config": {
            "provider": "cloudflare",
            "servers": ["1.1.1.1", "1.0.0.1"],
            "doh_url": "https://cloudflare-dns.com/dns-query",
            "doh_enabled": True,
            "is_auto_mode": True,
        },
    }

    applied: list[tuple] = []
    monkeypatch.setattr(
        internal_bypasser.network,
        "set_dns_provider",
        lambda provider, manual=None, *, use_doh=None: applied.append((provider, manual, use_doh)),
    )
    monkeypatch.setattr(internal_bypasser, "get", lambda *_a, **_k: "<html>ok</html>")
    monkeypatch.setattr(internal_bypasser.sys, "stdin", io.StringIO(json.dumps(request)))

    assert internal_bypasser._run_child_process() == 0
    assert applied == [("cloudflare", None, True)]


def test_apply_parent_dns_config_skips_auto_and_empty(monkeypatch):
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    calls: list = []
    monkeypatch.setattr(
        internal_bypasser.network,
        "set_dns_provider",
        lambda *a, **k: calls.append((a, k)),
    )

    internal_bypasser._apply_parent_dns_config({"provider": "auto"})
    internal_bypasser._apply_parent_dns_config({})

    assert calls == []


def test_apply_parent_dns_config_forwards_manual_servers(monkeypatch):
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    calls: list = []
    monkeypatch.setattr(
        internal_bypasser.network,
        "set_dns_provider",
        lambda provider, manual=None, *, use_doh=None: calls.append((provider, manual, use_doh)),
    )

    internal_bypasser._apply_parent_dns_config(
        {"provider": "manual", "servers": ["9.9.9.9"], "doh_enabled": False}
    )

    assert calls == [("manual", ["9.9.9.9"], False)]


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
    from shelfmark.bypass import cookie_store

    cookie_store._cf_cookies["example.com"] = {
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

    monkeypatch.setattr(_shared_session(), "get", _raise)

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


def test_max_duration_seconds_allows_for_a_mirror_rotation_retry():
    """get_bypassed_page() may call get() twice, so the budget must cover both."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    assert internal_bypasser.max_duration_seconds() == (
        2 * internal_bypasser._BYPASS_SUBPROCESS_TIMEOUT_SECONDS
    )


def test_cdp_worker_run_times_out_and_cancels_the_orphaned_coroutine(monkeypatch):
    """A wedged in-process bypass must not block forever holding LOCKED.

    Regression guard: _CDP_WORKER.run() used to wait with timeout=None, so one hung CDP
    session blocked every subsequent bypass in the process indefinitely.
    """
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    started = threading.Event()

    async def _never_finish():
        started.set()
        await asyncio.Event().wait()

    coro = _never_finish()
    with pytest.raises(TimeoutError):
        internal_bypasser._CDP_WORKER.run(coro, timeout=0.05)

    assert started.is_set(), "coroutine should have been scheduled before timing out"


def test_run_bypass_in_current_process_bounds_its_wait(monkeypatch):
    """The in-process path passes a deadline rather than waiting forever."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    observed: dict[str, float | None] = {}

    class _FakeWorker:
        def run(self, coro, timeout=None):
            observed["timeout"] = timeout
            coro.close()
            return "html"

    monkeypatch.setattr(internal_bypasser, "_CDP_WORKER", _FakeWorker())
    monkeypatch.delenv("SHELFMARK_INTERNAL_BYPASSER_CHILD", raising=False)

    result = internal_bypasser._run_bypass_in_current_process("https://example.com", 1)

    assert result == "html"
    assert observed["timeout"] == internal_bypasser._IN_PROCESS_BYPASS_TIMEOUT_SECONDS


def _write_fake_proc_entry(proc_root, pid: int, pgid: int, argv: list[str]) -> None:
    """Create a /proc-shaped entry for a fake process."""
    entry = proc_root / str(pid)
    entry.mkdir()
    (entry / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in argv) + b"\0")
    # pid (comm) state ppid pgrp ... - comm is parenthesised and may contain spaces.
    (entry / "stat").write_text(f"{pid} (some (odd) name) S 1 {pgid} {pgid} 0 -1 4194304 0 0")


def test_cleanup_only_kills_own_and_abandoned_browser_sessions(monkeypatch, tmp_path):
    """Regression test for issue #1231: the sweep used a container-wide `pkill -f chrome`,
    so every worker that started a bypass killed the browsers the other workers were
    still driving. Only our own process group and groups whose leader is gone are ours."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _write_fake_proc_entry(proc_root, 1000, 1000, ["python", "-m", "shelfmark.bypass"])
    _write_fake_proc_entry(proc_root, 1001, 1000, ["/usr/bin/chromium", "--headless"])
    _write_fake_proc_entry(proc_root, 1002, 1000, ["Xvfb", ":99"])
    # Live sibling session: another worker is solving a challenge with these right now.
    _write_fake_proc_entry(proc_root, 2000, 2000, ["python", "-m", "shelfmark.bypass"])
    _write_fake_proc_entry(proc_root, 2001, 2000, ["/usr/bin/chromium", "--headless"])
    # Abandoned session: its leader (pid 3000) is gone, so its browser really is an orphan.
    _write_fake_proc_entry(proc_root, 3001, 3000, ["/usr/bin/chromium", "--headless"])

    killed: list[int] = []

    monkeypatch.setattr(internal_bypasser.env, "DOCKERMODE", True)
    monkeypatch.setattr(internal_bypasser, "_PROC_ROOT", proc_root)
    monkeypatch.setattr(internal_bypasser.os, "getpid", lambda: 1000)
    monkeypatch.setattr(internal_bypasser.os, "getpgrp", lambda: 1000)
    monkeypatch.setattr(internal_bypasser.os, "kill", lambda pid, _sig: killed.append(pid))
    monkeypatch.setattr(internal_bypasser.time, "sleep", lambda _seconds: None)

    assert internal_bypasser._cleanup_orphan_processes() == 3
    assert sorted(killed) == [1001, 1002, 3001]


def test_cleanup_is_skipped_without_proc(monkeypatch, tmp_path):
    """Without /proc there is no way to tell sessions apart, so kill nothing."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    monkeypatch.setattr(internal_bypasser.env, "DOCKERMODE", True)
    monkeypatch.setattr(internal_bypasser, "_PROC_ROOT", tmp_path / "missing")
    monkeypatch.setattr(
        internal_bypasser.os, "kill", lambda *_args: pytest.fail("must not kill anything")
    )

    assert internal_bypasser._cleanup_orphan_processes() == 0


class _FakeHelperStdin:
    """The request pipe: a write is how the helper receives one request."""

    def __init__(self, process):
        self._process = process
        self.closed = False

    def write(self, data):
        self._process.serve(data)

    def flush(self):
        return None

    def close(self):
        self.closed = True


class _FakeHelperProcess:
    """Stand-in for the bypass helper subprocess.

    The helper serves one request per line of stdin and answers by writing the result file
    the request named, so that is what this fakes: a write produces an answer.
    """

    def __init__(self, *_args, **kwargs):
        self.kwargs = kwargs
        self.pid = 4242
        self.returncode = None
        self.answers = True
        self.killed = False
        self.waited = False
        self.stdin = _FakeHelperStdin(self)
        self.requests: list[dict] = []

    def serve(self, payload):
        request = json.loads(payload)
        self.requests.append(request)
        if not self.answers:
            return
        result = {"ok": True, "html": "<html>solved</html>", "cookies": {}, "user_agents": {}}
        Path(request["result_path"]).write_text(json.dumps(result), encoding="utf-8")

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.waited = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def _patch_helper_subprocess(monkeypatch, internal_bypasser, process, killed_groups):
    monkeypatch.setattr(internal_bypasser.subprocess, "Popen", lambda *a, **kw: process(*a, **kw))
    monkeypatch.setattr(internal_bypasser.network, "get_dns_config", dict)
    monkeypatch.setattr(
        internal_bypasser.os, "killpg", lambda pgid, _sig: killed_groups.append(pgid)
    )
    # A fresh helper per test: the module-level one is shared, and a process parked by one
    # test would be handed to the next.
    helper = internal_bypasser._BypassHelper()
    monkeypatch.setattr(internal_bypasser._BypassHelper, "_idle_timeout", lambda _self: 0.0)
    monkeypatch.setattr(internal_bypasser, "_BYPASS_HELPER", helper)
    return helper


def test_helper_runs_in_its_own_session_and_is_torn_down(monkeypatch):
    """Regression test for issue #1231: the helper's Chrome and Xvfb must belong to the
    helper's own process group, and the whole group must die with it - otherwise the
    leftovers break the next worker's browser and can only be cleared by a sweep broad
    enough to kill a concurrent worker's browser too.

    The helper outlives a single request, so the teardown happens when it is dropped rather
    than after every solve. Each bypass still closes its own browser, so what survives in
    between is the process, not a Chrome.
    """
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    processes: list[_FakeHelperProcess] = []
    killed_groups: list[int] = []

    def _make_process(*args, **kwargs):
        process = _FakeHelperProcess(*args, **kwargs)
        processes.append(process)
        return process

    helper = _patch_helper_subprocess(monkeypatch, internal_bypasser, _make_process, killed_groups)

    assert internal_bypasser._get_via_subprocess("https://example.com", 1) == "<html>solved</html>"
    assert processes[0].kwargs["start_new_session"] is True
    assert killed_groups == [], "the helper was torn down after a single request"

    helper._discard()

    assert killed_groups == [processes[0].pid]


def test_helper_serves_a_second_request_without_respawning(monkeypatch):
    """The interpreter start and imports are paid once, not per protected request."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    processes: list[_FakeHelperProcess] = []
    killed_groups: list[int] = []

    def _make_process(*args, **kwargs):
        process = _FakeHelperProcess(*args, **kwargs)
        processes.append(process)
        return process

    _patch_helper_subprocess(monkeypatch, internal_bypasser, _make_process, killed_groups)

    internal_bypasser._get_via_subprocess("https://example.com/one", 1)
    internal_bypasser._get_via_subprocess("https://example.com/two", 1)

    assert len(processes) == 1
    assert [request["url"] for request in processes[0].requests] == [
        "https://example.com/one",
        "https://example.com/two",
    ]


def test_helper_timeout_kills_the_whole_session(monkeypatch):
    """A timed-out solve must not leave a live browser behind for the next worker."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    processes: list[_FakeHelperProcess] = []
    killed_groups: list[int] = []

    def _make_process(*args, **kwargs):
        process = _FakeHelperProcess(*args, **kwargs)
        process.answers = False  # accepts the request, never writes a result
        processes.append(process)
        return process

    _patch_helper_subprocess(monkeypatch, internal_bypasser, _make_process, killed_groups)
    monkeypatch.setattr(internal_bypasser, "_BYPASS_SUBPROCESS_TIMEOUT_SECONDS", 0.1)

    with pytest.raises(TimeoutError):
        internal_bypasser._get_via_subprocess("https://example.com", 1)

    assert killed_groups == [processes[0].pid]
    assert processes[0].killed is True


def test_helper_takes_the_browser_down_when_its_parent_dies(monkeypatch):
    """Cleanup only reclaims process groups whose leader is gone (#1231), so an orphaned
    helper must not sit there holding a browser no later bypass is allowed to touch."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    terminated: list[str] = []

    monkeypatch.setattr(internal_bypasser.os, "getppid", lambda: 1)
    monkeypatch.setattr(
        internal_bypasser, "_terminate_own_session", lambda: terminated.append("terminated")
    )
    monkeypatch.setattr(
        internal_bypasser.time, "sleep", lambda _seconds: pytest.fail("should not wait")
    )

    internal_bypasser._watch_parent_process(999, interval=0.0)

    assert terminated == ["terminated"]


def test_helper_watchdog_waits_while_its_parent_is_alive(monkeypatch):
    """The watchdog must only fire on a changed ppid, not on every poll."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    ppids = iter([999, 999, 1])
    sleeps: list[float] = []

    monkeypatch.setattr(internal_bypasser.os, "getppid", lambda: next(ppids))
    monkeypatch.setattr(internal_bypasser, "_terminate_own_session", lambda: None)
    monkeypatch.setattr(internal_bypasser.time, "sleep", sleeps.append)

    internal_bypasser._watch_parent_process(999, interval=0.5)

    assert sleeps == [0.5, 0.5]


def test_host_resolver_rules_use_the_system_resolvers_address_family(monkeypatch):
    """An IPv6-only host (e.g. NAT64) must not be pinned to an unreachable IPv4 address."""
    import socket

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    answers = {
        "aa.v6": [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("64:ff9b::ba02:a3ae", 443, 0, 0))],
        "aa.v4": [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("186.2.163.174", 443))],
    }
    calls: list[dict[str, object]] = []

    def _getaddrinfo(host, port, *args, **kwargs):
        calls.append({"args": args, **kwargs})
        return answers[host]

    monkeypatch.setattr(
        internal_bypasser.network,
        "get_available_aa_urls",
        lambda: ["https://aa.v6", "https://aa.v4"],
    )
    monkeypatch.setattr(internal_bypasser.socket, "getaddrinfo", _getaddrinfo)

    assert internal_bypasser._build_host_resolver_rules() == [
        "MAP aa.v6 [64:ff9b::ba02:a3ae]",
        "MAP aa.v4 186.2.163.174",
    ]
    assert all(socket.AF_INET not in call["args"] and "family" not in call for call in calls)


@pytest.mark.parametrize("wrapper", ["system_failover", "custom"])
def test_host_resolver_rules_work_with_the_custom_dns_wrappers(monkeypatch, wrapper):
    """With custom DNS on, ``socket.getaddrinfo`` is a wrapper that names its parameters.

    It takes ``socket_type``, not the stdlib's ``type``, so a ``type=`` keyword raised
    TypeError and no host was pre-resolved.
    """
    import socket

    import shelfmark.bypass.internal_bypasser as internal_bypasser
    from shelfmark.download import network

    answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("186.2.163.174", 443))]
    monkeypatch.setattr(network, "original_getaddrinfo", lambda *args: answer)
    if wrapper == "system_failover":
        resolver = network.create_system_failover_getaddrinfo()
    else:
        resolver = network.create_custom_getaddrinfo(
            lambda host: [], lambda host: [], lambda host: True
        )
    monkeypatch.setattr(
        internal_bypasser.network, "get_available_aa_urls", lambda: ["https://aa.v4"]
    )
    monkeypatch.setattr(internal_bypasser.socket, "getaddrinfo", resolver)

    assert internal_bypasser._build_host_resolver_rules() == ["MAP aa.v4 186.2.163.174"]
