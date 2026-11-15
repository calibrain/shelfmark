"""How long the internal bypasser is allowed to spend, and on what.

Issue #1276: MAX_RETRY drove *both* the outer page-load loop and the per-page method
loop, so the default of 10 meant ~40 solve attempts on one browser. That overran the
worker deadline, and the failure reached the user as `RuntimeError: TimeoutError` - a
message that says nothing about a protection challenge and sent people looking at their
reverse proxy instead.

Also covered: the undisturbed window a passive challenge gets before anything touches the
page. Anna's Archive's DDoS-Guard check has no click target and clears itself; going
straight to the click/reload methods meant the one thing that solves it was never tried.
"""

import asyncio

import pytest


@pytest.fixture
def bypass(monkeypatch):
    """internal_bypasser with sleeps and jitter removed."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    async def _no_sleep(_seconds) -> None:
        return None

    monkeypatch.setattr(internal_bypasser.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(internal_bypasser._RNG, "uniform", lambda _a, _b: 0)
    return internal_bypasser


def _recording_methods(calls: list[str], count: int = 4):
    def _make(name: str):
        async def _method(_page) -> bool:
            calls.append(name)
            return False

        _method.__name__ = name
        return _method

    return [_make(f"m{i}") for i in range(count)]


def _stub_page_state(monkeypatch, bypass, *, bypassed=False, challenge="ddos_guard"):
    async def _is_bypassed(*_args, **_kwargs) -> bool:
        return bypassed

    async def _detect(*_args, **_kwargs) -> str:
        return challenge

    monkeypatch.setattr(bypass, "_is_bypassed", _is_bypassed)
    monkeypatch.setattr(bypass, "_detect_challenge_type", _detect)


# --------------------------------------------------------------------------- #
# The method loop must not read MAX_RETRY
# --------------------------------------------------------------------------- #
def test_method_loop_budget_is_independent_of_max_retry(monkeypatch, bypass):
    """MAX_RETRY is the outer page-load retry; reading it here squared the budget.

    Exercised against a challenge whose *type* keeps changing, because that is the case
    where max_retries is what bounds the loop: the stuck-challenge guard only fires on a
    run of the same type, so with a stable challenge it hid the real budget entirely.
    """
    monkeypatch.setattr(type(bypass.app_config), "MAX_RETRY", 50, raising=False)

    types = iter(["ddos_guard", "cloudflare"] * 100)

    async def _alternating(*_args, **_kwargs) -> str:
        return next(types)

    calls: list[str] = []
    monkeypatch.setattr(bypass, "BYPASS_METHODS", _recording_methods(calls))
    monkeypatch.setattr(bypass, "_wait_for_passive_solve", _never_passes)
    _stub_page_state(monkeypatch, bypass)
    monkeypatch.setattr(bypass, "_detect_challenge_type", _alternating)

    assert asyncio.run(bypass._bypass(object())) is False
    assert len(calls) == bypass._BYPASS_METHOD_ATTEMPTS
    assert len(calls) < 50, "MAX_RETRY must not reach the method loop"


def test_method_attempt_budget_is_reachable(bypass):
    """The number reported as `attempt N/X` must be a number the loop can reach.

    It used to be MAX_RETRY (10) while the stuck-challenge guard capped the loop at 5,
    so logs showed `4/10` and stopped, which reads like six lost attempts.
    """
    assert bypass._BYPASS_METHOD_ATTEMPTS == len(bypass.BYPASS_METHODS) + 1
    assert bypass._BYPASS_METHOD_ATTEMPTS >= (
        max(bypass.MAX_CONSECUTIVE_SAME_CHALLENGE, len(bypass.BYPASS_METHODS) + 1)
    )


# --------------------------------------------------------------------------- #
# A passive challenge gets an undisturbed window first
# --------------------------------------------------------------------------- #
async def _never_passes(*_args, **_kwargs) -> bool:
    return False


def test_passive_challenge_is_given_time_before_any_method_runs(monkeypatch, bypass):
    """DDoS-Guard's JS check clears itself; nothing should click or reload first."""
    calls: list[str] = []
    monkeypatch.setattr(bypass, "BYPASS_METHODS", _recording_methods(calls))
    _stub_page_state(monkeypatch, bypass)

    async def _passes(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr(bypass, "_wait_for_passive_solve", _passes)

    assert asyncio.run(bypass._bypass(object())) is True
    assert calls == [], "the page must not be touched while the check can still pass"


def test_passive_wait_happens_once_not_before_every_method(monkeypatch, bypass):
    """It is a settling window, not a delay bolted onto each attempt."""
    waits: list[int] = []
    calls: list[str] = []

    async def _count_wait(*_args, **_kwargs) -> bool:
        waits.append(1)
        return False

    monkeypatch.setattr(bypass, "BYPASS_METHODS", _recording_methods(calls))
    monkeypatch.setattr(bypass, "_wait_for_passive_solve", _count_wait)
    _stub_page_state(monkeypatch, bypass)

    asyncio.run(bypass._bypass(object()))

    assert len(waits) == 1
    assert calls == ["m0", "m1", "m2", "m3"]


def test_no_passive_wait_when_no_challenge_is_detected(monkeypatch, bypass):
    """The 'none' branch has its own settle-and-refresh handling."""
    waits: list[int] = []

    async def _count_wait(*_args, **_kwargs) -> bool:
        waits.append(1)
        return False

    monkeypatch.setattr(bypass, "_wait_for_passive_solve", _count_wait)
    _stub_page_state(monkeypatch, bypass, challenge="none")

    class _Page:
        async def reload(self, **_kwargs) -> None:
            return None

    asyncio.run(bypass._bypass(_Page(), max_retries=1))

    assert waits == []


def test_wait_for_passive_solve_returns_as_soon_as_the_page_clears(monkeypatch, bypass):
    polls = {"n": 0}

    async def _is_bypassed(*_args, **_kwargs) -> bool:
        polls["n"] += 1
        return polls["n"] >= 3

    monkeypatch.setattr(bypass, "_is_bypassed", _is_bypassed)

    assert asyncio.run(bypass._wait_for_passive_solve(object())) is True
    assert polls["n"] == 3


def test_wait_for_passive_solve_gives_up_at_the_window(monkeypatch, bypass):
    """It must not poll forever - the methods still need their share of the budget."""
    clock = {"now": 0.0}
    monkeypatch.setattr(bypass.time, "monotonic", lambda: clock["now"])

    async def _tick(*_args, **_kwargs) -> bool:
        clock["now"] += 1.0
        return False

    monkeypatch.setattr(bypass, "_is_bypassed", _tick)

    assert asyncio.run(bypass._wait_for_passive_solve(object())) is False
    assert clock["now"] >= bypass._PASSIVE_SOLVE_SECONDS


def test_wait_for_passive_solve_honours_cancellation(monkeypatch, bypass):
    import threading

    from shelfmark.bypass import BypassCancelledError

    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(bypass, "_is_bypassed", _never_passes)

    with pytest.raises(BypassCancelledError):
        asyncio.run(bypass._wait_for_passive_solve(object(), cancel))


# --------------------------------------------------------------------------- #
# The page-load loop stops while there is still time to report a real failure
# --------------------------------------------------------------------------- #
def test_page_load_loop_stops_before_the_worker_deadline(monkeypatch, bypass):
    """A stubborn challenge must produce "bypass failed", not a cancelled coroutine."""
    clock = {"now": 0.0}
    monkeypatch.setattr(bypass.time, "monotonic", lambda: clock["now"])
    monkeypatch.delenv(bypass._BYPASS_CHILD_ENV, raising=False)

    attempts = {"n": 0}

    async def _create(_url):
        return object()

    async def _get(_url, _driver, _cancel=None) -> str:
        attempts["n"] += 1
        # Each pass eats a realistic slice of the budget.
        clock["now"] += 120.0
        return ""

    async def _close(_driver) -> None:
        return None

    monkeypatch.setattr(bypass, "_create_cdp_browser", _create)
    monkeypatch.setattr(bypass, "_get", _get)
    monkeypatch.setattr(bypass, "_close_cdp_driver", _close)

    class _RealWorker:
        def run(self, coro, timeout=None):
            return asyncio.run(coro)

    monkeypatch.setattr(bypass, "_CDP_WORKER", _RealWorker())

    result = bypass._run_bypass_in_current_process("https://example.com", 10)

    assert result == ""
    # Well short of the 10 it was asked for, and short of the deadline it had.
    assert attempts["n"] < 10
    budget = bypass._IN_PROCESS_BYPASS_TIMEOUT_SECONDS
    assert clock["now"] < budget, "the loop must leave room to report the failure"


def test_page_load_loop_still_makes_one_attempt_on_a_spent_budget(monkeypatch, bypass):
    """The deadline check must never skip the request entirely."""
    clock = {"now": 10_000.0}
    monkeypatch.setattr(bypass.time, "monotonic", lambda: clock["now"])
    monkeypatch.delenv(bypass._BYPASS_CHILD_ENV, raising=False)

    attempts = {"n": 0}

    async def _create(_url):
        return object()

    async def _get(_url, _driver, _cancel=None) -> str:
        attempts["n"] += 1
        return "<html>solved</html>"

    async def _close(_driver) -> None:
        return None

    monkeypatch.setattr(bypass, "_create_cdp_browser", _create)
    monkeypatch.setattr(bypass, "_get", _get)
    monkeypatch.setattr(bypass, "_close_cdp_driver", _close)

    class _RealWorker:
        def run(self, coro, timeout=None):
            return asyncio.run(coro)

    monkeypatch.setattr(bypass, "_CDP_WORKER", _RealWorker())

    assert bypass._run_bypass_in_current_process("https://example.com", 10) == "<html>solved</html>"
    assert attempts["n"] == 1


_LOADED_DOCUMENT_EXPRESSION = (
    "document.readyState === 'loading' ? '' : document.documentElement.outerHTML"
)


class _FakeElement:
    def __init__(self, html: str = "<html>solved</html>") -> None:
        self.html = html

    async def get_html_async(self) -> str:
        return self.html


class _FakePage:
    """A page that only produces its document after `ready_after` seconds of waiting.

    `states` scripts the document's readyState, one per read, the last one repeating. An
    entry that is an exception is raised instead. A read while "loading" answers "", as
    the real expression does; any other state answers `full`. The `html` element always
    holds `partial`, so a test that gets `full` cannot have passed through the fallback.
    The fixture removes sleeps, so the page progresses per read rather than per second.
    Any other expression raises AssertionError, which the code under test does not catch,
    so a typo in the expression fails the test instead of being absorbed.
    """

    def __init__(
        self,
        ready_after: float = 0.0,
        *,
        states: list[object] | None = None,
        partial: str = "<html>solved</html>",
        full: str = "<html>solved</html>",
    ) -> None:
        self.ready_after = ready_after
        self.waited_with: list[float] = []
        self.states = states if states is not None else ["complete"]
        self.partial = partial
        self.full = full
        self.state_polls = 0

    async def find(self, selector: str, timeout: float = 1):
        self.waited_with.append(timeout)
        if timeout < self.ready_after:
            msg = f"Time ran out while waiting for: {{{selector}}}"
            raise TimeoutError(msg)
        return _FakeElement(self.partial)

    async def evaluate(self, expression: str) -> object:
        if expression != _LOADED_DOCUMENT_EXPRESSION:
            msg = f"unexpected expression: {expression}"
            raise AssertionError(msg)
        state = self.states[min(self.state_polls, len(self.states) - 1)]
        self.state_polls += 1
        if isinstance(state, Exception):
            raise state
        return "" if state == "loading" else self.full


def test_page_source_waits_longer_than_seleniumbases_one_second(bypass):
    """A page still navigating after a solve must not lose the solve.

    SeleniumBase's get_page_source() allows one second for the document. Anna's Archive
    hands back a redirect to the real content instead, so the read raised TimeoutError
    while the challenge had in fact been cleared.
    """
    page = _FakePage(ready_after=5.0)

    assert asyncio.run(bypass._read_page_source(page)) == "<html>solved</html>"
    assert page.waited_with == [bypass._PAGE_SOURCE_TIMEOUT_DEFAULT]


def test_page_source_timeout_is_configurable(bypass, monkeypatch):
    """BYPASS_PAGE_SOURCE_TIMEOUT overrides the default for slow or fast setups."""
    monkeypatch.setattr(
        bypass.app_config,
        "get",
        lambda key, default=None: 45 if key == "BYPASS_PAGE_SOURCE_TIMEOUT" else default,
    )
    page = _FakePage()

    asyncio.run(bypass._read_page_source(page))

    assert page.waited_with == [45.0]


_PARTIAL_SEARCH_PAGE = "<html><head><title>Search</title></head><body><div>filters"
_FULL_SEARCH_PAGE = _PARTIAL_SEARCH_PAGE + "<table>results</table></div></body></html>"


def test_page_source_waits_for_the_document_to_finish_loading(bypass):
    """The `html` element exists before the page has arrived.

    On 1.7.0 a cleared DDoS-Guard check returned 122,986 bytes of an Anna's Archive search
    page of about 745,000, read while the results were still streaming in, and the user
    was shown "No results found".
    """
    page = _FakePage(
        states=["loading", "loading", "loading", "interactive"],
        partial=_PARTIAL_SEARCH_PAGE,
        full=_FULL_SEARCH_PAGE,
    )

    assert asyncio.run(bypass._read_page_source(page)) == _FULL_SEARCH_PAGE
    assert page.state_polls == 4


def test_page_source_falls_back_to_the_element_when_loading_outlasts_the_budget(
    monkeypatch, bypass
):
    """A page that never finishes loading must be read as before, not raise or hang."""
    clock = {"now": 0.0}
    monkeypatch.setattr(bypass.time, "monotonic", lambda: clock["now"])
    page = _FakePage(states=["loading"], partial=_PARTIAL_SEARCH_PAGE, full=_FULL_SEARCH_PAGE)
    original_evaluate = page.evaluate

    async def _evaluate_slowly(expression: str) -> object:
        clock["now"] += 1.0
        return await original_evaluate(expression)

    page.evaluate = _evaluate_slowly

    assert asyncio.run(bypass._read_page_source(page)) == _PARTIAL_SEARCH_PAGE
    assert clock["now"] >= bypass._PAGE_SOURCE_TIMEOUT_DEFAULT
    assert page.state_polls <= bypass._PAGE_SOURCE_TIMEOUT_DEFAULT + 1
    # The budget is spent, so the second lookup gets the floor, not a negative timeout.
    assert page.waited_with == [
        bypass._PAGE_SOURCE_TIMEOUT_DEFAULT,
        bypass._PAGE_SOURCE_FALLBACK_FIND_SECONDS,
    ]


class _StaleNodeElement:
    """An element of a document the page has left: CDP no longer knows its node."""

    async def get_html_async(self) -> str:
        from seleniumbase.undetected.cdp_driver.connection import ProtocolException

        raise ProtocolException("No node with given id found")


def test_page_source_fallback_reads_the_document_the_page_is_on_now(monkeypatch, bypass):
    """A navigation during the wait leaves the first `html` element stale.

    Reading it raised a CDP error out of _read_page_source and failed the attempt, when
    the page had a document to return.
    """
    clock = {"now": 0.0}
    monkeypatch.setattr(bypass.time, "monotonic", lambda: clock["now"])
    page = _FakePage(states=["loading"], partial=_PARTIAL_SEARCH_PAGE, full=_FULL_SEARCH_PAGE)
    original_evaluate = page.evaluate
    elements: list[object] = [_StaleNodeElement(), _FakeElement("<html>new page</html>")]

    async def _evaluate_slowly(expression: str) -> object:
        clock["now"] += 1.0
        return await original_evaluate(expression)

    async def _find(selector: str, timeout: float = 1) -> object:
        page.waited_with.append(timeout)
        return elements.pop(0)

    page.evaluate = _evaluate_slowly
    page.find = _find

    assert asyncio.run(bypass._read_page_source(page)) == "<html>new page</html>"
    assert elements == []


def test_page_source_survives_a_cdp_error_while_waiting(bypass):
    """One failed state read must not drop the page back to its half-loaded prefix."""
    from seleniumbase.undetected.cdp_driver.connection import ProtocolException

    page = _FakePage(
        states=[ProtocolException("Execution context was destroyed"), "loading", "complete"],
        partial=_PARTIAL_SEARCH_PAGE,
        full=_FULL_SEARCH_PAGE,
    )

    assert asyncio.run(bypass._read_page_source(page)) == _FULL_SEARCH_PAGE
    assert page.state_polls == 3
