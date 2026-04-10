"""
E2E Test Configuration and Fixtures.

These tests require the full application stack to be running.
Run with: uv run pytest tests/e2e/ -v -m e2e
"""

from __future__ import annotations

import os
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
import requests

if TYPE_CHECKING:
    from collections.abc import Iterator


# Default test configuration
DEFAULT_BASE_URL = "http://localhost:8084"
DEFAULT_TIMEOUT = 10
POLL_INTERVAL = 2
DOWNLOAD_TIMEOUT = 300  # 5 minutes max for downloads
E2E_USERNAME_ENV = "E2E_USERNAME"
E2E_PASSWORD_ENV = "E2E_PASSWORD"


@dataclass
class APIClient:
    """HTTP client for E2E API testing."""

    base_url: str
    timeout: int = DEFAULT_TIMEOUT
    session: requests.Session = field(default_factory=requests.Session)

    def get(self, path: str, **kwargs) -> requests.Response:
        """Make a GET request."""
        kwargs.setdefault("timeout", self.timeout)
        return self.session.get(f"{self.base_url}{path}", **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        """Make a POST request."""
        kwargs.setdefault("timeout", self.timeout)
        return self.session.post(f"{self.base_url}{path}", **kwargs)

    def put(self, path: str, **kwargs) -> requests.Response:
        """Make a PUT request."""
        kwargs.setdefault("timeout", self.timeout)
        return self.session.put(f"{self.base_url}{path}", **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        """Make a DELETE request."""
        kwargs.setdefault("timeout", self.timeout)
        return self.session.delete(f"{self.base_url}{path}", **kwargs)

    def wait_for_health(self, max_wait: int = 30) -> bool:
        """Wait for the server to be healthy."""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                resp = self.get("/api/health")
                if resp.status_code == 200:
                    return True
            except requests.exceptions.ConnectionError:
                pass
            time.sleep(1)
        return False


def _get_auth_state(client: APIClient) -> dict[str, object]:
    """Read the live server auth state for auth-sensitive E2E tests."""
    try:
        response = client.get("/api/auth/check")
    except requests.exceptions.RequestException:
        return {}

    if response.status_code != 200:
        return {}

    try:
        payload = response.json()
    except ValueError:
        return {}

    return payload if isinstance(payload, dict) else {}


def _login_with_env_credentials(client: APIClient) -> bool:
    """Try authenticating an E2E client with env-provided credentials."""
    username = os.environ.get(E2E_USERNAME_ENV, "").strip()
    password = os.environ.get(E2E_PASSWORD_ENV, "")
    if not username or not password:
        return False

    response = client.post(
        "/api/auth/login",
        json={
            "username": username,
            "password": password,
            "remember_me": False,
        },
    )
    return response.status_code == 200


def _require_authenticated_client(client: APIClient) -> APIClient:
    """Require an authenticated client for protected-route E2E tests."""
    auth_state = _get_auth_state(client)
    if not auth_state or not auth_state.get("auth_required"):
        return client

    if auth_state.get("authenticated"):
        return client

    username = os.environ.get(E2E_USERNAME_ENV, "").strip()
    password = os.environ.get(E2E_PASSWORD_ENV, "")
    if not username or not password:
        pytest.fail(
            "Live server requires authentication for this E2E test. "
            f"Set {E2E_USERNAME_ENV}/{E2E_PASSWORD_ENV} or run against a no-auth instance."
        )

    if not _login_with_env_credentials(client):
        pytest.fail(
            "Failed to authenticate the E2E client with "
            f"{E2E_USERNAME_ENV}/{E2E_PASSWORD_ENV}. "
            "Check the credentials or run against a no-auth instance."
        )

    refreshed_auth_state = _get_auth_state(client)
    if refreshed_auth_state.get("authenticated"):
        return client

    pytest.fail(
        "Login request completed but the live server still reports an unauthenticated session."
    )


@dataclass
class DownloadTracker:
    """Tracks downloads for cleanup after tests."""

    client: APIClient
    queued_ids: list[str] = field(default_factory=list)

    def track(self, book_id: str) -> str:
        """Track a book ID for cleanup."""
        self.queued_ids.append(book_id)
        return book_id

    def cleanup(self) -> None:
        """Cancel all tracked downloads."""
        for book_id in self.queued_ids:
            with suppress(Exception):
                self.client.delete(f"/api/download/{book_id}/cancel")
        self.queued_ids.clear()

    def wait_for_status(
        self,
        book_id: str,
        target_states: list[str],
        timeout: int = DOWNLOAD_TIMEOUT,
    ) -> dict | None:
        """
        Poll status until book reaches one of the target states.

        Args:
            book_id: The book/task ID to check
            target_states: List of states to wait for (e.g., ["complete", "error"])
            timeout: Maximum seconds to wait

        Returns:
            Status dict if target state reached, None if timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = self.client.get("/api/status")
                if resp.status_code != 200:
                    time.sleep(POLL_INTERVAL)
                    continue

                status_data = resp.json()

                # Check each status category
                for state in target_states:
                    if state in status_data and book_id in status_data[state]:
                        return {
                            "state": state,
                            "data": status_data[state][book_id],
                        }

                # Check for error state
                if "error" in status_data and book_id in status_data["error"]:
                    return {
                        "state": "error",
                        "data": status_data["error"][book_id],
                    }

            except Exception:
                pass

            time.sleep(POLL_INTERVAL)

        return None


@pytest.fixture(scope="session")
def base_url() -> str:
    """Get the base URL for the API server."""
    return os.environ.get("E2E_BASE_URL", DEFAULT_BASE_URL)


@pytest.fixture(scope="session")
def healthy_base_url(base_url: str) -> str:
    """Ensure the live server is reachable before creating per-test clients."""
    client = APIClient(base_url=base_url)
    try:
        if not client.wait_for_health():
            pytest.skip("Server not available - ensure the app is running")
        return base_url
    finally:
        client.session.close()


@pytest.fixture
def api_client(healthy_base_url: str) -> Iterator[APIClient]:
    """Create a fresh API client for each E2E test."""
    client = APIClient(base_url=healthy_base_url)
    yield client
    client.session.close()


@pytest.fixture
def protected_api_client(api_client: APIClient) -> APIClient:
    """Create an authenticated client for protected-route E2E tests."""
    return _require_authenticated_client(api_client)


@pytest.fixture
def download_tracker(request: pytest.FixtureRequest) -> Iterator[DownloadTracker]:
    """Create a download tracker that cleans up after each test."""
    client_fixture_name = (
        "protected_api_client" if "protected_api_client" in request.fixturenames else "api_client"
    )
    client = request.getfixturevalue(client_fixture_name)
    tracker = DownloadTracker(client=client)
    yield tracker
    tracker.cleanup()


@pytest.fixture(scope="session")
def server_config(healthy_base_url: str) -> dict:
    """Get server configuration."""
    client = APIClient(base_url=healthy_base_url)
    try:
        resp = client.get("/api/config")
        if resp.status_code != 200:
            return {}
        return resp.json()
    finally:
        client.session.close()
