"""Harness for the Shelfmark e2e Docker platform.

These tests run against a *live* Shelfmark booted by ``run-e2e.sh`` under a
particular config profile (env file). The active profile is read from
``E2E_PROFILE``; tests select which profiles they apply to with the
``@pytest.mark.profiles(...)`` marker. Unmarked tests are profile-agnostic
invariants and run under every profile — that is how the same cluster test
becomes the config matrix (search must succeed whether egress is direct, via a
proxy, via custom DNS, or through the bypasser).

Run (handled by run-e2e.sh):
    E2E_PROFILE=baseline uv run pytest tests/e2e/platform/suite -m platform
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import pytest
import requests

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8084")
ACTIVE_PROFILE = os.environ.get("E2E_PROFILE", "baseline")
DEFAULT_TIMEOUT = 15
DOWNLOAD_TIMEOUT = int(os.environ.get("E2E_DOWNLOAD_TIMEOUT", "120"))
TERMINAL_OK = {"complete", "done", "available"}
TERMINAL_ERR = {"error", "cancelled"}


@dataclass
class PlatformClient:
    base_url: str = BASE_URL
    timeout: int = DEFAULT_TIMEOUT
    session: requests.Session = field(default_factory=requests.Session)

    def get(self, path: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        return self.session.get(f"{self.base_url}{path}", **kw)

    def post(self, path: str, **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        return self.session.post(f"{self.base_url}{path}", **kw)

    # --- domain helpers -------------------------------------------------- #
    def wait_for_health(self, max_wait: int = 90) -> bool:
        deadline = time.time() + max_wait
        while time.time() < deadline:
            try:
                if self.get("/api/health").status_code == 200:
                    return True
            except requests.RequestException:
                pass
            time.sleep(2)
        return False

    def direct_search(
        self, query: str, *, inject: str | None = None, **params
    ) -> requests.Response:
        """Source-native (hermetic) release search — no external metadata provider.

        Hits GET /api/releases?source=direct_download&query=... which drives
        direct_download.search_books against the mock Anna's Archive.

        Fault injection rides *inside* the query text (the app builds the AA URL
        itself and only forwards the query as ``q=``); the mock origin parses the
        ``E2EINJECT:<name>`` token. See mock_services.aa_search.
        """
        effective_query = f"E2EINJECT:{inject} {query}" if inject else query
        qp = {"source": "direct_download", "query": effective_query, **params}
        return self.get("/api/releases", params=qp, timeout=60)

    def releases_from(self, resp: requests.Response) -> list[dict]:
        if resp.status_code != 200:
            return []
        data = resp.json()
        if isinstance(data, dict):
            rel = data.get("releases")
            return rel if isinstance(rel, list) else []
        return data if isinstance(data, list) else []

    def queue_download(self, release: dict) -> requests.Response:
        return self.post("/api/releases/download", json=release, timeout=30)

    def wait_for_terminal(self, book_id: str, timeout: int = DOWNLOAD_TIMEOUT) -> tuple[str, dict]:
        deadline = time.time() + timeout
        last: dict = {}
        while time.time() < deadline:
            resp = self.get("/api/status")
            if resp.status_code == 200 and isinstance(resp.json(), dict):
                status = resp.json()
                for state, entries in status.items():
                    if isinstance(entries, dict) and book_id in entries:
                        last = entries[book_id]
                        if state in TERMINAL_OK or state in TERMINAL_ERR:
                            return state, last
            time.sleep(2)
        return "timeout", last


@pytest.fixture(scope="session")
def client() -> PlatformClient:
    c = PlatformClient()
    if not c.wait_for_health():
        pytest.fail(f"Shelfmark not healthy at {BASE_URL} (profile={ACTIVE_PROFILE})")
    return c


@pytest.fixture(scope="session")
def active_profile() -> str:
    return ACTIVE_PROFILE


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "platform: Shelfmark e2e docker platform test")
    config.addinivalue_line("markers", "profiles(*names): only run under these E2E_PROFILE values")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip tests whose declared profiles don't include the active profile.

    A test with no ``profiles`` marker is a profile-agnostic invariant and runs
    everywhere (this is the matrix: invariants x profiles).
    """
    for item in items:
        item.add_marker(pytest.mark.platform)
        marker = item.get_closest_marker("profiles")
        if marker and ACTIVE_PROFILE not in marker.args:
            item.add_marker(
                pytest.mark.skip(reason=f"profile={ACTIVE_PROFILE!r} not in {marker.args}")
            )
