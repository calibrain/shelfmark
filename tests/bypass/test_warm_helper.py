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
        # A pid nothing may actually be signalled by: _terminate_helper_session is patched
        # out in these tests, and a stray killpg on a live pid would take out the test run.
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
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

    assert (
        internal_bypasser._CHILD_BYPASS_TIMEOUT_SECONDS
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
