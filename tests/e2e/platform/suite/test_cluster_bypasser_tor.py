"""Cluster 1/6 — Tor boot correctness.

Tor has repeatedly boot-looped or pegged CPU on startup (#1021 loops on 1.3.0,
#940 USING_TOR loop, #801 gosu 100% CPU, #937 gunicorn missing). The hermetic,
fast assertion is: with USING_TOR=true the container reaches a healthy
/api/health and does NOT crash-loop. Real Tor egress (slow/flaky in CI) is left
to an opt-in 'tor-full' profile.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.profiles("tor")


def test_app_becomes_healthy_under_tor(client) -> None:
    """tor.sh + entrypoint must bring the app up, not boot-loop."""
    assert client.get("/api/health").status_code == 200


def test_container_did_not_crash_loop(client) -> None:
    """Restart count is captured by the runner into E2E_SHELFMARK_RESTARTS.

    A boot-loop shows up as repeated restarts; a healthy boot is 0.
    """
    restarts = os.environ.get("E2E_SHELFMARK_RESTARTS")
    if restarts is None:
        pytest.skip("runner did not provide E2E_SHELFMARK_RESTARTS")
    assert int(restarts) == 0, f"shelfmark restarted {restarts} times under Tor (boot-loop)"
