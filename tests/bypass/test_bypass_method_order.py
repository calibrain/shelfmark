"""No method in the list may be a step another method already takes first.

`BYPASS_METHODS` used to open with a solve-only entry that called `page.solve_captcha()`
and checked the result. `_bypass_method_cdp_gui_click`, the entry behind it, opens by
doing exactly that and returns the moment it works - so against a challenge that
`solve_captcha()` cannot clear, the first method could only repeat the half that had
already failed, then charge the loop's backoff before the method that does work started.
Measured on Anna's Archive at 0/19 successes and ~5.5s of the ~26s each solve cost
(issue #1285).

The passive-solve window added in v1.3.13 keeps most solves away from this loop entirely,
so this is about what the loop costs when it does run.
"""

import asyncio

import pytest

import shelfmark.bypass.internal_bypasser as ib


@pytest.fixture
def no_sleep(monkeypatch):
    async def _no_sleep(_seconds) -> None:
        return None

    monkeypatch.setattr(ib.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(ib._RNG, "uniform", lambda _a, _b: 0)


class _Page:
    """Records what a method asked the page to do."""

    def __init__(self, *, solve_clears: bool) -> None:
        self.solve_clears = solve_clears
        self.calls: list[str] = []

    async def solve_captcha(self) -> None:
        self.calls.append("solve_captcha")

    async def is_element_visible(self, selector: str) -> bool:
        self.calls.append(f"visible:{selector}")
        return False

    async def click_with_offset(self, selector: str, _x, _y, center=True) -> None:
        self.calls.append(f"click:{selector}")


def _stub_is_bypassed(monkeypatch, page: _Page) -> None:
    async def _is_bypassed(*_a, **_kw) -> bool:
        return page.solve_clears and "solve_captcha" in page.calls

    monkeypatch.setattr(ib, "_is_bypassed", _is_bypassed)


def test_no_solve_only_method_remains_in_the_list():
    names = [method.__name__ for method in ib.BYPASS_METHODS]
    assert "_bypass_method_cdp_solve" not in names
    assert names[0] == "_bypass_method_cdp_gui_click"


def test_the_first_method_still_tries_solve_captcha_first(monkeypatch, no_sleep):
    """Coverage is only preserved because gui_click opens with the same call."""
    page = _Page(solve_clears=True)
    _stub_is_bypassed(monkeypatch, page)

    assert asyncio.run(ib.BYPASS_METHODS[0](page)) is True
    assert page.calls == ["solve_captcha"], "it must return before touching any selector"


def test_it_falls_through_to_clicking_when_solve_does_not_clear(monkeypatch, no_sleep):
    """The half that actually works on DDoS-Guard still runs in the same attempt."""
    page = _Page(solve_clears=False)
    _stub_is_bypassed(monkeypatch, page)

    assert asyncio.run(ib.BYPASS_METHODS[0](page)) is False
    assert page.calls[0] == "solve_captcha"
    assert any(call.startswith("visible:") for call in page.calls), (
        "the selector pass should have been reached in the same attempt"
    )


def test_the_derived_budgets_follow_the_shortened_list():
    """Both budgets are computed from the list, so removing an entry must not strand them."""
    assert ib._BYPASS_METHOD_ATTEMPTS == len(ib.BYPASS_METHODS) + 1
    assert ib._BYPASS_METHOD_ATTEMPTS >= len(ib.BYPASS_METHODS), (
        "every method must still get a turn"
    )
