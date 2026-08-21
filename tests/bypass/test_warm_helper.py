"""Tests for keeping the bypass helper process alive between requests.

The browser is deliberately not kept: every bypass starts and closes its own Chrome. What
survives is the helper process, whose interpreter start and imports are pure overhead.
"""

import asyncio
import json

import pytest


class _FakeStdin:
    def __init__(self) -> None:
        self.closed = False
        self.written: list[str] = []

    def write(self, data: str) -> None:
        if self.closed:
            raise BrokenPipeError("stdin is closed")
        self.written.append(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    """Enough of subprocess.Popen for the helper's process bookkeeping."""

    _next_pid = 90001

    def __init__(self) -> None:
        self.stdin = _FakeStdin()
        self.returncode: int | None = None
        self.waited = False
        # A pid nothing may actually be signalled by: _terminate_helper_session is patched
        # out in these tests, and a stray killpg on a live pid would take out the test run.
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def _helper_with_fake_spawn(monkeypatch, procs: list[_FakeProc], terminated=None):
    """Build a helper that hands out fake processes and never arms a real timer."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    def _spawn(_self) -> _FakeProc:
        proc = _FakeProc()
        procs.append(proc)
        return proc

    def _terminate(proc) -> None:
        if terminated is not None:
            terminated.append(proc)

    monkeypatch.setattr(internal_bypasser._BypassHelper, "_spawn", _spawn)
    monkeypatch.setattr(internal_bypasser._BypassHelper, "_idle_timeout", lambda _self: 0.0)
    monkeypatch.setattr(internal_bypasser, "_terminate_helper_session", _terminate)
    return internal_bypasser._BypassHelper()


def _answered_payload(tmp_path, name: str = "result.json") -> dict:
    """A request whose result file already exists, so the helper resolves immediately."""
    result_path = tmp_path / name
    result_path.write_text(json.dumps({"ok": True, "html": "<html/>"}), encoding="utf-8")
    return {"url": "https://example.com", "retry": 1, "result_path": str(result_path)}


def test_helper_serves_consecutive_requests_from_one_process(monkeypatch, tmp_path):
    """The point of the whole thing: request two and three must not re-pay the spawn."""
    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)

    for i in range(3):
        result = helper.run(_answered_payload(tmp_path, f"r{i}.json"), timeout=5, cancel_flag=None)
        assert result["ok"] is True

    assert len(procs) == 1, "each request spawned its own helper"
    assert len(procs[0].stdin.written) == 3
    assert all(line.endswith("\n") for line in procs[0].stdin.written), (
        "requests must be newline-delimited or the helper's loop cannot split them"
    )


def test_helper_respawns_after_the_previous_one_died(monkeypatch, tmp_path):
    """A helper can be reaped while idle; the next request must not fail on it."""
    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)

    helper.run(_answered_payload(tmp_path, "a.json"), timeout=5, cancel_flag=None)
    procs[0].returncode = 1  # died between requests

    result = helper.run(_answered_payload(tmp_path, "b.json"), timeout=5, cancel_flag=None)

    assert result["ok"] is True
    assert len(procs) == 2


def test_helper_retries_once_when_the_pipe_breaks_on_write(monkeypatch, tmp_path):
    """poll() can still say alive when the far end is already gone."""
    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)

    helper.run(_answered_payload(tmp_path, "a.json"), timeout=5, cancel_flag=None)
    procs[0].stdin.closed = True  # pipe gone, but poll() still reports running

    result = helper.run(_answered_payload(tmp_path, "b.json"), timeout=5, cancel_flag=None)

    assert result["ok"] is True
    assert len(procs) == 2


def test_helper_reports_a_helper_that_exits_without_answering(monkeypatch, tmp_path):
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)

    payload = {
        "url": "https://example.com",
        "retry": 1,
        "result_path": str(tmp_path / "never-written.json"),
    }

    def _die_on_write(_self, proc, _line) -> None:
        proc.returncode = 3

    monkeypatch.setattr(internal_bypasser._BypassHelper, "_write", _die_on_write)

    with pytest.raises(RuntimeError, match="exited without a result"):
        helper.run(payload, timeout=5, cancel_flag=None)


def test_helper_times_out_and_discards_the_wedged_process(monkeypatch, tmp_path):
    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)

    payload = {
        "url": "https://example.com",
        "retry": 1,
        "result_path": str(tmp_path / "never-written.json"),
    }

    with pytest.raises(TimeoutError):
        helper.run(payload, timeout=0.05, cancel_flag=None)

    assert helper._proc is None, "a wedged helper must not be handed to the next request"


def test_idle_reaper_rearms_when_work_arrived_while_it_waited(monkeypatch, tmp_path):
    """The timer fires on its own thread and can lose the race against a new request."""
    import time

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)
    helper.run(_answered_payload(tmp_path), timeout=5, cancel_flag=None)

    rearmed: list[bool] = []
    monkeypatch.setattr(internal_bypasser._BypassHelper, "_idle_timeout", lambda _self: 3600.0)
    monkeypatch.setattr(
        internal_bypasser._BypassHelper, "_arm_idle_timer", lambda _self: rearmed.append(True)
    )
    helper._last_used = time.monotonic()

    helper._reap_if_idle()

    assert rearmed == [True]
    assert helper._proc is not None, "helper was killed despite recent work"


def test_idle_reaper_closes_a_genuinely_idle_helper(monkeypatch, tmp_path):
    import time

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)
    helper.run(_answered_payload(tmp_path), timeout=5, cancel_flag=None)

    monkeypatch.setattr(internal_bypasser._BypassHelper, "_idle_timeout", lambda _self: 60.0)
    helper._last_used = time.monotonic() - 120

    helper._reap_if_idle()

    assert helper._proc is None
    assert procs[0].stdin.closed


def test_discard_tears_down_the_whole_session(monkeypatch, tmp_path):
    """Dropping the helper must reach its browser tree, not just the helper itself.

    The helper is a session leader (start_new_session), so a Chrome left behind by one
    killed mid-bypass would keep a process group alive that the cleanup sweep is then not
    allowed to reclaim - the leak #1231 was about.
    """
    procs: list[_FakeProc] = []
    terminated: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs, terminated)

    helper.run(_answered_payload(tmp_path), timeout=5, cancel_flag=None)
    helper._discard()

    assert terminated == [procs[0]]


def test_helper_asks_before_it_kills(monkeypatch, tmp_path):
    """An idle helper should get to exit on its own; the kill is the fallback."""
    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)

    helper.run(_answered_payload(tmp_path), timeout=5, cancel_flag=None)
    helper._discard()

    assert procs[0].stdin.closed, "stdin must be closed to end the helper's request loop"
    assert procs[0].returncode == 0, "an idle helper should have exited on its own"


def _bypass_with_recorded_driver(monkeypatch, get_impl):
    """Wire up a bypass whose browser creation and closing are observable."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    driver = object()
    closed: list[object] = []

    async def _create(_url):
        return driver

    async def _close(drv):
        closed.append(drv)

    monkeypatch.setattr(internal_bypasser, "_create_cdp_browser", _create)
    monkeypatch.setattr(internal_bypasser, "_get", get_impl)
    monkeypatch.setattr(internal_bypasser, "_close_cdp_driver", _close)
    return driver, closed


def test_successful_bypass_closes_its_browser(monkeypatch):
    """A living helper must not accumulate browsers: each bypass ends with Chrome gone."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    async def _get(_url, _driver, _cancel=None):
        return "<html>ok</html>"

    driver, closed = _bypass_with_recorded_driver(monkeypatch, _get)

    result = internal_bypasser._run_bypass_in_current_process("https://example.com", 1)

    assert result == "<html>ok</html>"
    assert closed == [driver]


def test_failed_bypass_closes_its_browser(monkeypatch):
    """The same has to hold when the bypass raises on its way out."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    async def _get(_url, _driver, _cancel=None):
        raise internal_bypasser.BypassCancelledError("cancelled")

    driver, closed = _bypass_with_recorded_driver(monkeypatch, _get)

    with pytest.raises(internal_bypasser.BypassCancelledError):
        internal_bypasser._run_bypass_in_current_process("https://example.com", 1)

    assert closed == [driver]


def test_child_process_serves_every_line_it_is_given(monkeypatch, tmp_path):
    """One helper, several requests: the loop is what saves the repeated process start."""
    import io

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    urls: list[str] = []

    def _fake_get(url, retry=None, cancel_flag=None):
        urls.append(url)
        return f"<html>{url}</html>"

    requests = [
        {"url": "https://example.com/one", "retry": 1, "result_path": str(tmp_path / "1.json")},
        {"url": "https://example.com/two", "retry": 1, "result_path": str(tmp_path / "2.json")},
    ]
    stdin = io.StringIO("\n".join(json.dumps(request) for request in requests) + "\n")

    monkeypatch.setattr(internal_bypasser, "get", _fake_get)
    monkeypatch.setattr(internal_bypasser.sys, "stdin", stdin)

    assert internal_bypasser._run_child_process() == 0
    assert urls == ["https://example.com/one", "https://example.com/two"]

    for index, request in enumerate(requests, start=1):
        result = json.loads((tmp_path / f"{index}.json").read_text(encoding="utf-8"))
        assert result["ok"] is True
        assert result["html"] == f"<html>{request['url']}</html>"


def test_child_process_keeps_serving_after_a_failed_request(monkeypatch, tmp_path):
    """One failing URL must not take the helper - and everything queued - down."""
    import io

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    def _fake_get(url, retry=None, cancel_flag=None):
        if url.endswith("boom"):
            raise RuntimeError("bypass exploded")
        return "<html>ok</html>"

    requests = [
        {"url": "https://example.com/boom", "retry": 1, "result_path": str(tmp_path / "1.json")},
        {"url": "https://example.com/fine", "retry": 1, "result_path": str(tmp_path / "2.json")},
    ]
    stdin = io.StringIO("\n".join(json.dumps(request) for request in requests) + "\n")

    monkeypatch.setattr(internal_bypasser, "get", _fake_get)
    monkeypatch.setattr(internal_bypasser.sys, "stdin", stdin)

    assert internal_bypasser._run_child_process() == 0

    failed = json.loads((tmp_path / "1.json").read_text(encoding="utf-8"))
    assert failed["ok"] is False
    assert failed["error"] == "bypass exploded"

    served = json.loads((tmp_path / "2.json").read_text(encoding="utf-8"))
    assert served["ok"] is True


def test_result_file_becomes_visible_only_when_complete(tmp_path):
    """The parent treats the file's existence as the answer, so no partial writes."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    result_path = tmp_path / "result.json"
    internal_bypasser._publish_result(result_path, {"ok": True, "html": "<html/>"})

    assert json.loads(result_path.read_text(encoding="utf-8"))["ok"] is True
    assert list(tmp_path.iterdir()) == [result_path], "temporary file was left behind"


def test_child_bypass_runs_on_the_long_lived_worker_loop(monkeypatch):
    """A helper serving many requests must not build and close a loop per bypass.

    asyncio.run() owns the loop for one call and closes it on the way out, which is why the
    child goes through the worker unconditionally: one loop for the process's lifetime.
    """
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    monkeypatch.setenv("SHELFMARK_INTERNAL_BYPASSER_CHILD", "1")

    loops: list[asyncio.AbstractEventLoop] = []

    async def _record_loop(_url, _driver, _cancel=None):
        loops.append(asyncio.get_running_loop())
        return "<html>ok</html>"

    _bypass_with_recorded_driver(monkeypatch, _record_loop)

    internal_bypasser._run_bypass_in_current_process("https://example.com", 1)
    internal_bypasser._run_bypass_in_current_process("https://example.com", 1)

    assert len(loops) == 2
    assert loops[0] is loops[1], "second bypass ran on a different loop than the first"
    assert not loops[0].is_closed()


def test_child_bypass_carries_its_own_deadline(monkeypatch):
    """The child bounds itself, rather than relying only on the parent's deadline."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    monkeypatch.setenv("SHELFMARK_INTERNAL_BYPASSER_CHILD", "1")

    timeouts: list[float | None] = []
    real_run = internal_bypasser._CDP_WORKER.run

    def _record_timeout(coro, timeout=None):
        timeouts.append(timeout)
        return real_run(coro, timeout=timeout)

    async def _get(_url, _driver, _cancel=None):
        return "<html>ok</html>"

    _bypass_with_recorded_driver(monkeypatch, _get)
    monkeypatch.setattr(internal_bypasser._CDP_WORKER, "run", _record_timeout)

    internal_bypasser._run_bypass_in_current_process("https://example.com", 1)

    assert timeouts == [internal_bypasser._CHILD_BYPASS_TIMEOUT_SECONDS]


def test_child_deadline_leaves_the_parent_room_to_hear_the_answer(monkeypatch):
    """If the parent gave up first it could only kill the helper, losing a warm process."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    # The child's worst case is its deadline plus the grace it is given to close the
    # browser after that deadline cancels the bypass, and all of it has to fit inside the
    # parent's wait - otherwise the parent gives up first and kills a helper that was
    # about to answer.
    assert (
        internal_bypasser._CHILD_BYPASS_TIMEOUT_SECONDS
        + internal_bypasser._CDP_UNWIND_GRACE_SECONDS
        < internal_bypasser._BYPASS_SUBPROCESS_TIMEOUT_SECONDS
    )


def test_in_process_bypass_keeps_the_parents_budget(monkeypatch):
    """Non-Docker installs run in-process, where there is no helper to outlive anything."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    monkeypatch.delenv("SHELFMARK_INTERNAL_BYPASSER_CHILD", raising=False)

    timeouts: list[float | None] = []
    real_run = internal_bypasser._CDP_WORKER.run

    def _record_timeout(coro, timeout=None):
        timeouts.append(timeout)
        return real_run(coro, timeout=timeout)

    async def _get(_url, _driver, _cancel=None):
        return "<html>ok</html>"

    _bypass_with_recorded_driver(monkeypatch, _get)
    monkeypatch.setattr(internal_bypasser._CDP_WORKER, "run", _record_timeout)

    internal_bypasser._run_bypass_in_current_process("https://example.com", 1)

    assert timeouts == [internal_bypasser._IN_PROCESS_BYPASS_TIMEOUT_SECONDS]


def test_timed_out_bypass_finishes_unwinding_before_the_call_returns():
    """A helper serving the next request must not race the browser teardown of the last.

    The deadline cancels the bypass, but cancelling from the calling thread only schedules
    that - it returns while `finally: await _close_cdp_driver(driver)` is still running.
    In a helper that now outlives the request, the next bypass would open its Chrome on the
    same loop while the abandoned one was still closing its own, sharing the DISPLAY
    globals and one process group.
    """
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    events: list[str] = []

    async def _wedged():
        try:
            await asyncio.sleep(30)
        finally:
            # Teardown that yields, the way closing websockets and Chrome does.
            await asyncio.sleep(0.05)
            events.append("browser closed")

    with pytest.raises(TimeoutError):
        internal_bypasser._CDP_WORKER.run(_wedged(), timeout=0.1)

    assert events == ["browser closed"], "run() returned before the bypass had unwound"


def test_unwind_that_wedges_does_not_hold_the_caller_forever(monkeypatch):
    """The grace is a bound, not a promise: cleanup can hang on a dead browser too."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    monkeypatch.setattr(internal_bypasser, "_CDP_UNWIND_GRACE_SECONDS", 0.1)

    async def _wedged_on_both_ends():
        try:
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(30)

    with pytest.raises(TimeoutError):
        internal_bypasser._CDP_WORKER.run(_wedged_on_both_ends(), timeout=0.1)


def test_cancelling_does_not_wait_out_the_shutdown_grace(monkeypatch, tmp_path):
    """The grace only helps a helper that can still read its stdin.

    One dropped mid-bypass is blocked inside the solve and will never reach its read loop,
    so waiting it out cannot end in anything but the kill - while the user who asked to
    cancel, and every bypass queued behind them on LOCKED, waits for it.
    """
    import threading

    import shelfmark.bypass.internal_bypasser as internal_bypasser

    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)

    cancel_flag = threading.Event()
    cancel_flag.set()
    payload = {
        "url": "https://example.com",
        "retry": 1,
        "result_path": str(tmp_path / "never-written.json"),
    }

    with pytest.raises(internal_bypasser.BypassCancelledError):
        helper.run(payload, timeout=5, cancel_flag=cancel_flag)

    assert not procs[0].waited, "a helper wedged mid-bypass was given the full exit grace"
    assert procs[0].stdin.closed


def test_idle_helper_still_gets_its_grace(monkeypatch, tmp_path):
    """The reaper drops a helper that *is* in its read loop, and that one gets to exit."""
    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)

    helper.run(_answered_payload(tmp_path), timeout=5, cancel_flag=None)
    helper._discard()

    assert procs[0].waited, "an idle helper should be asked to exit before being killed"


def test_failed_request_leaves_no_result_files_behind(monkeypatch, tmp_path):
    """Result paths are unique per request, so anything left is left for good."""
    procs: list[_FakeProc] = []
    helper = _helper_with_fake_spawn(monkeypatch, procs)

    result_path = tmp_path / "result.json"
    # A helper killed part-way through _publish_result leaves the staging file.
    (tmp_path / "result.json.part").write_text('{"ok": tr', encoding="utf-8")
    payload = {"url": "https://example.com", "retry": 1, "result_path": str(result_path)}

    with pytest.raises(TimeoutError):
        helper.run(payload, timeout=0.05, cancel_flag=None)

    assert list(tmp_path.iterdir()) == []


def test_child_does_not_export_cookies_left_by_an_earlier_request(monkeypatch, tmp_path):
    """The parent owns the store; a warm helper must not push its own history back over it.

    http.py purges a host's clearance the moment that host challenges a request carrying
    it. A helper that kept its store across requests would still be holding the purged
    cookies, and the next solve - for some entirely different host - would export them and
    the parent would merge them straight back in.
    """
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    def _solve_host(url, retry=None, cancel_flag=None):
        # A solve fills the store for the host it solved, which is all it should report.
        host = url.rsplit("/", 1)[-1]
        internal_bypasser.import_store({host: {"cf_clearance": "fresh"}}, {host: "UA"})
        return "<html>ok</html>"

    monkeypatch.setattr(internal_bypasser, "get", _solve_host)
    internal_bypasser.clear_cf_cookies()

    for index, host in enumerate(("first.example", "second.example")):
        internal_bypasser._handle_child_request(
            json.dumps(
                {
                    "url": f"https://example.com/{host}",
                    "retry": 1,
                    "result_path": str(tmp_path / f"{index}.json"),
                }
            )
        )

    second = json.loads((tmp_path / "1.json").read_text(encoding="utf-8"))
    assert list(second["cookies"]) == ["second.example"], (
        "the helper exported clearance won by an earlier request"
    )
    assert list(second["user_agents"]) == ["second.example"]

    internal_bypasser.clear_cf_cookies()


def _record_dns_calls(monkeypatch):
    """Stand in for the network module: report a resolver state, record changes to it.

    A helper starts on system DNS, which is what the parent reports as "auto".
    """
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    calls: list[tuple] = []
    state = {"provider": "auto", "servers": [], "doh_enabled": False}

    def _set(provider, servers=None, use_doh=None):
        calls.append((provider, servers, use_doh))
        state.update({"provider": provider, "servers": servers or [], "doh_enabled": bool(use_doh)})

    monkeypatch.setattr(internal_bypasser.network, "set_dns_provider", _set)
    monkeypatch.setattr(internal_bypasser.network, "get_dns_config", lambda: dict(state))
    return internal_bypasser, calls


def test_helper_follows_the_parent_back_to_auto_dns(monkeypatch):
    """A user flipping CUSTOM_DNS back to auto applies live - the helper has to hear it.

    The old early-return on "auto" was correct only because a fresh helper had never been
    told anything else. One that outlives the request has, and would go on resolving AA
    through a resolver the parent has already abandoned.
    """
    internal_bypasser, calls = _record_dns_calls(monkeypatch)

    internal_bypasser._apply_parent_dns_config(
        {"provider": "cloudflare", "servers": [], "doh_enabled": True}
    )
    internal_bypasser._apply_parent_dns_config(
        {"provider": "auto", "servers": [], "doh_enabled": False}
    )

    assert calls == [("cloudflare", None, True), ("auto", None, False)]


def test_helper_does_not_reinitialize_dns_for_an_unchanged_config(monkeypatch):
    """set_dns_provider() rebuilds resolvers; every request would pay for it otherwise."""
    internal_bypasser, calls = _record_dns_calls(monkeypatch)

    for _ in range(3):
        internal_bypasser._apply_parent_dns_config(
            {"provider": "quad9", "servers": [], "doh_enabled": True}
        )

    assert calls == [("quad9", None, True)]


def test_fresh_helper_leaves_auto_dns_alone(monkeypatch):
    """A helper starts on system DNS, which is what the parent reports as auto."""
    internal_bypasser, calls = _record_dns_calls(monkeypatch)

    internal_bypasser._apply_parent_dns_config(
        {"provider": "auto", "servers": [], "doh_enabled": False}
    )

    assert calls == []


def test_failed_dns_apply_is_retried_on_the_next_request(monkeypatch):
    """A provider that did not land leaves the resolver where it was, so the next request
    sees the same mismatch and tries again."""
    internal_bypasser, calls = _record_dns_calls(monkeypatch)

    def _explode(provider, servers=None, use_doh=None):
        calls.append((provider, servers, use_doh))
        msg = "resolver unreachable"
        raise OSError(msg)

    monkeypatch.setattr(internal_bypasser.network, "set_dns_provider", _explode)

    config = {"provider": "google", "servers": [], "doh_enabled": True}
    internal_bypasser._apply_parent_dns_config(config)
    internal_bypasser._apply_parent_dns_config(config)

    assert calls == [("google", None, True), ("google", None, True)]
