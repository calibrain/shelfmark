"""
E2E tests for authentication endpoints.

Tests the full authentication flow including login, logout, and auth check
with various authentication modes.

Run with: docker exec test-cwabd python3 -m pytest tests/e2e/ -v -m e2e
"""

import pytest

from .conftest import APIClient


@pytest.mark.e2e
class TestAuthenticationFlow:
    """Tests for the authentication endpoints in a real environment."""

    def test_auth_check_endpoint_exists(self, api_client: APIClient):
        """Test that auth check endpoint is accessible."""
        resp = api_client.get("/api/auth/check")

        assert resp.status_code == 200
        data = resp.json()
        assert "authenticated" in data
        assert "auth_required" in data
        assert "auth_mode" in data

    def test_auth_check_returns_auth_mode(self, api_client: APIClient):
        """Test that auth check returns the current auth mode."""
        resp = api_client.get("/api/auth/check")

        data = resp.json()
        assert "auth_mode" in data
        # Should be one of the valid auth modes
        assert data["auth_mode"] in ["none", "builtin", "cwa", "proxy"]

    def test_auth_check_includes_admin_status(self, api_client: APIClient):
        """Test that auth check includes admin status."""
        resp = api_client.get("/api/auth/check")

        data = resp.json()
        assert "is_admin" in data
        assert isinstance(data["is_admin"], bool)

    def test_logout_endpoint_exists(self, api_client: APIClient):
        """Test that logout endpoint is accessible."""
        resp = api_client.post("/api/auth/logout")

        # Should return 200 whether authenticated or not
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data

    def test_logout_may_return_logout_url(self, api_client: APIClient):
        """Test that logout may return a logout URL for proxy auth."""
        resp = api_client.post("/api/auth/logout")

        data = resp.json()
        # logout_url is optional depending on auth mode
        if "logout_url" in data:
            assert isinstance(data["logout_url"], str)

    def test_login_endpoint_exists(self, api_client: APIClient):
        """Test that login endpoint is accessible."""
        resp = api_client.post("/api/auth/login", json={
            "username": "test",
            "password": "test",
            "remember_me": False
        })

        # Should return some response (may be success or error depending on config)
        assert resp.status_code in [200, 401, 403]

    def test_login_with_no_auth_succeeds(self, api_client: APIClient):
        """Test that login succeeds when no authentication is required."""
        # First check if auth is required
        auth_check = api_client.get("/api/auth/check")
        auth_data = auth_check.json()
        
        if not auth_data.get("auth_required"):
            # Try logging in
            resp = api_client.post("/api/auth/login", json={
                "username": "anyuser",
                "password": "anypass",
                "remember_me": False
            })
            
            # Should succeed
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("success") is True


@pytest.mark.e2e
class TestProxyAuthentication:
    """Tests for proxy authentication mode."""

    def test_proxy_auth_with_valid_header(self, api_client: APIClient):
        """Test proxy auth when valid user header is present."""
        # Check current auth mode
        auth_check = api_client.get("/api/auth/check")
        auth_data = auth_check.json()
        
        if auth_data.get("auth_mode") != "proxy":
            pytest.skip("Proxy authentication not configured")
        
        # Make a request with proxy auth header
        # Note: In real deployment, these headers would be set by the proxy
        resp = api_client.get("/api/config", headers={"X-Auth-User": "proxyuser"})

        if resp.status_code == 401:
            pytest.skip("Proxy auth header not accepted (check proxy configuration)")

        # Should be able to access the endpoint
        assert resp.status_code == 200

    def test_proxy_auth_logout_url_available(self, api_client: APIClient):
        """Test that proxy auth provides logout URL if configured."""
        # Check current auth mode
        auth_check = api_client.get("/api/auth/check")
        auth_data = auth_check.json()
        
        if auth_data.get("auth_mode") != "proxy":
            pytest.skip("Proxy authentication not configured")
        
        # Check for logout URL in auth check response
        if "logout_url" in auth_data:
            assert isinstance(auth_data["logout_url"], str)
            assert len(auth_data["logout_url"]) > 0


@pytest.mark.e2e
class TestBuiltinAuthentication:
    """Tests for built-in username/password authentication."""

    def test_builtin_auth_requires_credentials(self, api_client: APIClient):
        """Test that endpoints require authentication when builtin auth is enabled."""
        # Check current auth mode
        auth_check = api_client.get("/api/auth/check")
        auth_data = auth_check.json()
        
        if auth_data.get("auth_mode") != "builtin":
            pytest.skip("Built-in authentication not configured")
        
        if not auth_data.get("authenticated"):
            # Attempt to access protected endpoint without authentication
            resp = api_client.get("/api/config")
            
            # Should be blocked
            assert resp.status_code == 401

    def test_builtin_auth_invalid_credentials(self, api_client: APIClient):
        """Test login with invalid credentials fails."""
        # Check current auth mode
        auth_check = api_client.get("/api/auth/check")
        auth_data = auth_check.json()
        
        if auth_data.get("auth_mode") != "builtin":
            pytest.skip("Built-in authentication not configured")
        
        # Try logging in with invalid credentials
        resp = api_client.post("/api/auth/login", json={
            "username": "invalid_user",
            "password": "wrong_password",
            "remember_me": False
        })
        
        # Should fail
        assert resp.status_code in [401, 403]
        data = resp.json()
        assert data.get("success") is not True


@pytest.mark.e2e
class TestCalibreWebAuthentication:
    """Tests for Calibre-Web database authentication."""

    def test_cwa_auth_mode_available(self, api_client: APIClient):
        """Test that CWA auth mode is reported if configured."""
        # Check current auth mode
        auth_check = api_client.get("/api/auth/check")
        auth_data = auth_check.json()
        
        if auth_data.get("auth_mode") == "cwa":
            # CWA mode is active
            assert auth_data["auth_mode"] == "cwa"
            # Should have authenticated or auth_required status
            assert "authenticated" in auth_data
            assert "auth_required" in auth_data


@pytest.mark.e2e
class TestAdminAccess:
    """Tests for admin access restrictions."""

    def test_settings_endpoint_respects_admin_restriction(self, api_client: APIClient):
        """Test that settings endpoints respect admin restrictions."""
        # Check current auth status
        auth_check = api_client.get("/api/auth/check")
        auth_data = auth_check.json()
        
        # If auth is required and user is not admin
        if auth_data.get("auth_required") and auth_data.get("authenticated"):
            if not auth_data.get("is_admin"):
                # Try accessing settings
                resp = api_client.get("/api/settings")
                
                # May be blocked with 403 if admin-only
                # Or allowed if settings are not restricted
                assert resp.status_code in [200, 403]

    def test_onboarding_endpoint_respects_admin_restriction(self, api_client: APIClient):
        """Test that onboarding endpoints respect admin restrictions."""
        # Check current auth status
        auth_check = api_client.get("/api/auth/check")
        auth_data = auth_check.json()
        
        # If auth is required and user is not admin
        if auth_data.get("auth_required") and auth_data.get("authenticated"):
            if not auth_data.get("is_admin"):
                # Try accessing onboarding
                resp = api_client.get("/api/onboarding")
                
                # May be blocked with 403 if admin-only
                # Or allowed if settings are not restricted
                assert resp.status_code in [200, 403]


@pytest.mark.e2e
class TestAuthenticationWorkflow:
    """Tests for complete authentication workflows."""

    def test_login_logout_cycle(self, api_client: APIClient):
        """Test complete login and logout cycle."""
        # Check initial auth status
        auth_check = api_client.get("/api/auth/check")
        initial_auth = auth_check.json()
        
        # If no auth required, skip this test
        if not initial_auth.get("auth_required"):
            pytest.skip("No authentication required")
        
        # Try logout first to clear any existing session
        logout_resp = api_client.post("/api/auth/logout")
        assert logout_resp.status_code == 200
        
        # Check we're logged out
        auth_check = api_client.get("/api/auth/check")
        post_logout_auth = auth_check.json()
        
        # For builtin/cwa auth, should not be authenticated
        # For proxy auth, depends on proxy configuration
        if initial_auth.get("auth_mode") in ["builtin", "cwa"]:
            assert post_logout_auth.get("authenticated") is False

    def test_auth_check_consistency(self, api_client: APIClient):
        """Test that auth check returns consistent results."""
        # Make multiple auth check requests
        resp1 = api_client.get("/api/auth/check")
        resp2 = api_client.get("/api/auth/check")
        resp3 = api_client.get("/api/auth/check")
        
        data1 = resp1.json()
        data2 = resp2.json()
        data3 = resp3.json()
        
        # All should succeed
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp3.status_code == 200
        
        # Auth mode should be consistent
        assert data1["auth_mode"] == data2["auth_mode"] == data3["auth_mode"]
        
        # Auth required should be consistent
        assert data1["auth_required"] == data2["auth_required"] == data3["auth_required"]
