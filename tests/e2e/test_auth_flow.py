"""
E2E tests for authentication endpoints.

Tests the full authentication flow including login, logout, and auth check
with various authentication modes.

Run with: uv run pytest tests/e2e/ -v -m e2e
"""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from .conftest import APIClient


def _auth_check(api_client: APIClient) -> dict:
    """Fetch the current auth state and assert the response shape."""
    resp = api_client.get("/api/auth/check")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "authenticated" in data
    assert "auth_required" in data
    assert "auth_mode" in data
    assert "is_admin" in data
    return data


@pytest.mark.e2e
class TestAuthenticationFlow:
    """Tests for the authentication endpoints in a real environment."""

    def test_auth_check_endpoint_exists(self, api_client: APIClient):
        """Test that auth check returns the stable contract fields."""
        data = _auth_check(api_client)

        if data["auth_mode"] == "none":
            assert data == {
                "authenticated": True,
                "auth_required": False,
                "auth_mode": "none",
                "is_admin": True,
            }
            return

        assert isinstance(data["authenticated"], bool)
        assert data["auth_required"] is True
        assert data["auth_mode"] in ["builtin", "cwa", "proxy", "oidc"]
        assert isinstance(data["is_admin"], bool)
        assert data["username"] is None or isinstance(data["username"], str)
        assert "display_name" in data
        assert data["display_name"] is None or isinstance(data["display_name"], str)

    def test_auth_check_returns_auth_mode(self, api_client: APIClient):
        """Test that auth check reports a known auth mode."""
        data = _auth_check(api_client)
        assert data["auth_mode"] in ["none", "builtin", "cwa", "proxy", "oidc"]

    def test_auth_check_includes_admin_status(self, api_client: APIClient):
        """Test that auth check exposes a boolean admin flag."""
        data = _auth_check(api_client)
        assert isinstance(data["is_admin"], bool)

    def test_logout_endpoint_exists(self, api_client: APIClient):
        """Test that logout returns the stable success contract."""
        resp = api_client.post("/api/auth/logout")

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert set(data).issubset({"success", "logout_url"})
        if "logout_url" in data:
            assert isinstance(data["logout_url"], str)
            assert data["logout_url"].startswith("http")

    def test_logout_may_return_logout_url(self, api_client: APIClient):
        """Test that logout may return a logout URL for proxy auth."""
        resp = api_client.post("/api/auth/logout")

        data = resp.json()
        if "logout_url" in data:
            assert isinstance(data["logout_url"], str)
            assert data["logout_url"].startswith("http")

    def test_login_endpoint_exists(self, api_client: APIClient):
        """Test that login obeys the current authentication contract."""
        auth_data = _auth_check(api_client)
        username = f"e2e-auth-{uuid4().hex[:8]}"
        resp = api_client.post(
            "/api/auth/login",
            json={"username": username, "password": "wrong-password", "remember_me": False},
        )

        auth_mode = auth_data.get("auth_mode")
        if auth_mode == "none":
            assert resp.status_code == 200
            assert resp.json() == {"success": True}
        elif auth_mode == "proxy":
            assert resp.status_code == 401
            assert resp.json() == {"error": "Proxy authentication is enabled"}
        elif auth_mode in {"builtin", "cwa"}:
            assert resp.status_code == 401
            assert resp.json() == {"error": "Invalid username or password."}
        elif auth_mode == "oidc":
            if auth_data.get("hide_local_auth"):
                assert resp.status_code == 403
                assert resp.json() == {"error": "Local authentication is disabled"}
            else:
                assert resp.status_code == 401
                assert resp.json() == {"error": "Invalid username or password."}
        else:
            pytest.fail(f"Unexpected auth mode: {auth_mode}")

    def test_login_with_no_auth_succeeds(self, api_client: APIClient):
        """Test that login succeeds when no authentication is required."""
        auth_data = _auth_check(api_client)

        if not auth_data.get("auth_required"):
            resp = api_client.post(
                "/api/auth/login",
                json={"username": "anyuser", "password": "anypass", "remember_me": False},
            )

            assert resp.status_code == 200
            assert resp.json() == {"success": True}
            assert api_client.get("/api/auth/check").json() == {
                "authenticated": True,
                "auth_required": False,
                "auth_mode": "none",
                "is_admin": True,
            }


@pytest.mark.e2e
class TestProxyAuthentication:
    """Tests for proxy authentication mode."""

    def test_proxy_auth_with_valid_header(self, api_client: APIClient):
        """Test proxy auth creates and preserves a session from the proxy header."""
        auth_data = _auth_check(api_client)

        if auth_data.get("auth_mode") != "proxy":
            pytest.skip("Proxy authentication not configured")

        resp = api_client.get("/api/auth/check", headers={"X-Auth-User": "proxyuser"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["authenticated"] is True
        assert data["auth_mode"] == "proxy"
        assert data["username"] == "proxyuser"
        assert data["auth_required"] is True
        assert isinstance(data["is_admin"], bool)
        assert "display_name" in data

        follow_up = api_client.get("/api/auth/check")
        follow_up_data = follow_up.json()
        assert follow_up.status_code == 200
        assert follow_up_data["authenticated"] is True
        assert follow_up_data["username"] == "proxyuser"

    def test_proxy_auth_logout_url_available(self, api_client: APIClient):
        """Test that proxy auth provides logout URL if configured."""
        auth_data = _auth_check(api_client)

        if auth_data.get("auth_mode") != "proxy":
            pytest.skip("Proxy authentication not configured")

        if "logout_url" in auth_data:
            assert isinstance(auth_data["logout_url"], str)
            assert len(auth_data["logout_url"]) > 0


@pytest.mark.e2e
class TestBuiltinAuthentication:
    """Tests for built-in username/password authentication."""

    def test_builtin_auth_requires_credentials(self, api_client: APIClient):
        """Test that endpoints require authentication when builtin auth is enabled."""
        auth_data = _auth_check(api_client)

        if auth_data.get("auth_mode") != "builtin":
            pytest.skip("Built-in authentication not configured")

        if auth_data.get("authenticated"):
            pytest.skip("Built-in auth session already authenticated")

        resp = api_client.get("/api/config")
        assert resp.status_code == 401

    def test_builtin_auth_invalid_credentials(self, api_client: APIClient):
        """Test login with invalid credentials fails."""
        auth_data = _auth_check(api_client)

        if auth_data.get("auth_mode") != "builtin":
            pytest.skip("Built-in authentication not configured")

        username = f"builtin-e2e-{uuid4().hex[:8]}"
        resp = api_client.post(
            "/api/auth/login",
            json={"username": username, "password": "wrong_password", "remember_me": False},
        )

        assert resp.status_code == 401
        assert resp.json() == {"error": "Invalid username or password."}


@pytest.mark.e2e
class TestCalibreWebAuthentication:
    """Tests for Calibre-Web database authentication."""

    def test_cwa_auth_mode_available(self, api_client: APIClient):
        """Test that CWA auth mode is reported if configured."""
        auth_data = _auth_check(api_client)

        if auth_data.get("auth_mode") == "cwa":
            assert auth_data["auth_mode"] == "cwa"
            assert auth_data["auth_required"] is True
            assert isinstance(auth_data["authenticated"], bool)
            assert isinstance(auth_data["is_admin"], bool)


@pytest.mark.e2e
class TestAdminAccess:
    """Tests for admin access restrictions."""

    def test_admin_only_routes_require_auth(self, api_client: APIClient):
        """Test that admin-only routes are blocked before auth is established."""
        auth_data = _auth_check(api_client)

        if not auth_data.get("auth_required"):
            pytest.skip("Authentication is not required in this environment")

        for path in ("/api/settings/security", "/api/settings/users", "/api/onboarding"):
            resp = api_client.get(path)
            assert resp.status_code == 401


@pytest.mark.e2e
class TestAuthenticationWorkflow:
    """Tests for complete authentication workflows."""

    def test_login_logout_cycle(self, api_client: APIClient):
        """Test complete login and logout cycle."""
        initial_auth = _auth_check(api_client)

        if not initial_auth.get("auth_required"):
            pytest.skip("No authentication required")

        logout_resp = api_client.post("/api/auth/logout")
        assert logout_resp.status_code == 200
        assert logout_resp.json().get("success") is True

        post_logout_auth = _auth_check(api_client)

        if (
            initial_auth.get("auth_mode") in ["builtin", "cwa"]
            or initial_auth.get("auth_mode") == "proxy"
        ):
            assert post_logout_auth.get("authenticated") is False
            assert post_logout_auth.get("username") is None

    def test_auth_check_consistency(self, api_client: APIClient):
        """Test that auth check returns consistent results."""
        data1 = _auth_check(api_client)
        data2 = _auth_check(api_client)
        data3 = _auth_check(api_client)

        assert data1 == data2 == data3
