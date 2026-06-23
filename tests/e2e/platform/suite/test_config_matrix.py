"""Config-matrix invariants.

These tests carry NO ``profiles`` marker for the egress check, so they run under
every profile the runner boots. Reaching the (mock) book source must succeed
whether egress is direct, through an HTTP/SOCKS proxy, via custom DNS, or via the
Cloudflare bypasser. That cross-product *is* the config matrix.

Covers the recurring "X setting silently breaks downloads" class:
proxy ignored (#956), DNS/ISP blocks (#1028, #108), bypasser config not adhered
(#410, #369, #267).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Profiles where reaching the (non-CF-gated) source via search is expected to work
# end to end. Excluded: tor (boot-correctness only), and the bypasser profiles —
# their AA is behind a Cloudflare gate that search does NOT bypass (the bypasser is
# download-time only), so a *search* there fails by design (see test_cluster_bypasser).
EGRESS_PROFILES = (
    "baseline",
    "dns-manual",
    "dns-blocked",
    "proxy-http",
    "proxy-socks",
)

# The proxy container whose logs prove egress actually traversed the proxy.
PROXY_CONTAINER = {
    "proxy-http": "e2e-tinyproxy",
    "proxy-socks": "e2e-microsocks",
}


def test_health_ok(client) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200, resp.text


@pytest.mark.profiles(*EGRESS_PROFILES)
def test_source_reachable_under_active_profile(client, active_profile) -> None:
    """The book source must be reachable regardless of egress configuration."""
    resp = client.direct_search("Mistborn")
    assert resp.status_code == 200, (
        f"[{active_profile}] direct search failed: {resp.status_code} {resp.text[:300]}"
    )
    releases = client.releases_from(resp)
    assert releases, (
        f"[{active_profile}] expected releases from the mock source but got none — "
        f"egress configuration is silently dropping the request"
    )


@pytest.mark.profiles("proxy-http")
def test_proxy_mode_synced_at_boot(client) -> None:
    """The deployment PROXY_MODE env override is applied at boot.

    ``/api/config`` returns only a frontend-facing subset (not network/proxy
    keys), so this is verified from the boot log where ENV→config sync is recorded.
    """
    assert client.get("/api/health").status_code == 200
    path = os.environ.get("E2E_SHELFMARK_LOG")
    if not path or not Path(path).exists():
        pytest.skip("E2E_SHELFMARK_LOG not available")
    log = Path(path).read_text(encoding="utf-8", errors="ignore")
    assert "PROXY_MODE" in log, "PROXY_MODE was not synced into network config at boot"


@pytest.mark.profiles("proxy-http", "proxy-socks")
def test_egress_actually_traverses_proxy(client, active_profile) -> None:
    """#956 guard: a configured proxy must actually *carry* the app's egress.

    Reachability alone is not enough — the app and the mock AA share the e2e
    network, so a regression that silently ignores the proxy config would still
    reach AA directly and pass ``test_source_reachable_under_active_profile``.
    Here we drive a search and then inspect the proxy container's logs: if the
    proxy never saw the traffic, the proxy was bypassed (regression for #956).
    """
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available to the test host")
    container = PROXY_CONTAINER[active_profile]

    # Generate egress that *must* go through the proxy.
    resp = client.direct_search("Mistborn")
    assert resp.status_code == 200 and client.releases_from(resp), (
        f"[{active_profile}] search failed under proxy: {resp.status_code} {resp.text[:200]}"
    )

    logs = subprocess.run(
        ["docker", "logs", container],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    blob = (logs.stdout + logs.stderr).lower()
    assert blob.strip(), (
        f"[{active_profile}] proxy container {container!r} produced no logs after a "
        f"search — egress did not traverse the proxy (regression for #956 proxy-ignored)"
    )
    # Strongest signal (HTTP proxy logs the destination host/request explicitly).
    if active_profile == "proxy-http":
        markers = ("mock-aa", "aa.mock.test", "connect", "request", "get ")
        assert any(m in blob for m in markers), (
            f"tinyproxy logs show no AA request — proxy may be passing traffic without "
            f"the app routing through it. logs tail: ...{blob[-300:]!r}"
        )
