"""Tests for the stall-detection activity grace signalling."""

import pytest

from shelfmark.core.models import QueueStatus
from shelfmark.download.activity import (
    ACTIVITY_GRACE_STATUS,
    parse_activity_grace,
    release_activity_grace,
    request_activity_grace,
)


def test_request_and_parse_round_trip():
    calls: list[tuple[str, str | None]] = []
    request_activity_grace(lambda status, message: calls.append((status, message)), 330)

    assert len(calls) == 1
    assert parse_activity_grace(*calls[0]) == 330.0


def test_release_emits_zero_grace():
    calls: list[tuple[str, str | None]] = []
    release_activity_grace(lambda status, message: calls.append((status, message)))

    assert parse_activity_grace(*calls[0]) == 0.0


def test_parse_returns_none_for_real_status_events():
    assert parse_activity_grace("resolving", "Bypassing protection...") is None
    assert parse_activity_grace("downloading", None) is None


@pytest.mark.parametrize("status", [s.value for s in QueueStatus])
def test_sentinel_cannot_collide_with_a_queue_status(status):
    """The sentinel must never be mistaken for a real status, or vice versa."""
    assert status != ACTIVITY_GRACE_STATUS
    assert parse_activity_grace(status, "anything") is None


def test_parse_tolerates_a_malformed_sentinel():
    """A bad emitter must not take down the status pipeline."""
    assert parse_activity_grace(ACTIVITY_GRACE_STATUS, "not-a-number") == 0.0
    assert parse_activity_grace(ACTIVITY_GRACE_STATUS, None) == 0.0


def test_parse_clamps_negative_grace():
    assert parse_activity_grace(ACTIVITY_GRACE_STATUS, "-5") == 0.0


def test_emitters_are_noops_without_a_callback():
    request_activity_grace(None, 30)
    release_activity_grace(None)


def test_emitters_swallow_a_raising_callback():
    def boom(_status: str, _message: str | None) -> None:
        raise RuntimeError("callback failed")

    request_activity_grace(boom, 30)
    release_activity_grace(boom)
