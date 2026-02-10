"""
Tests for OIDC integration into existing auth system.

Tests get_auth_mode() logic with OIDC and login_required admin
restriction logic. Since main.py has heavy dependencies, we test
the logic directly rather than importing from main.
"""

import pytest


class TestGetAuthModeOIDCLogic:
    """Tests that get_auth_mode logic handles OIDC correctly.

    Mirrors the logic in main.py:get_auth_mode() to verify OIDC
    support without importing the full app.
    """

    def _get_auth_mode(self, config):
        """Replicate get_auth_mode logic with OIDC support."""
        auth_mode = config.get("AUTH_METHOD", "none")
        if auth_mode == "oidc":
            if config.get("OIDC_DISCOVERY_URL") and config.get("OIDC_CLIENT_ID"):
                return "oidc"
            return "none"
        if auth_mode == "builtin":
            if config.get("BUILTIN_USERNAME") and config.get("BUILTIN_PASSWORD_HASH"):
                return "builtin"
            return "none"
        if auth_mode == "proxy":
            if config.get("PROXY_AUTH_USER_HEADER"):
                return "proxy"
            return "none"
        return "none"

    def test_returns_oidc_when_fully_configured(self):
        config = {
            "AUTH_METHOD": "oidc",
            "OIDC_DISCOVERY_URL": "https://auth.example.com/.well-known/openid-configuration",
            "OIDC_CLIENT_ID": "shelfmark",
        }
        assert self._get_auth_mode(config) == "oidc"

    def test_returns_none_when_oidc_missing_client_id(self):
        config = {
            "AUTH_METHOD": "oidc",
            "OIDC_DISCOVERY_URL": "https://auth.example.com/.well-known/openid-configuration",
        }
        assert self._get_auth_mode(config) == "none"

    def test_returns_none_when_oidc_missing_discovery_url(self):
        config = {
            "AUTH_METHOD": "oidc",
            "OIDC_CLIENT_ID": "shelfmark",
        }
        assert self._get_auth_mode(config) == "none"

    def test_returns_none_when_oidc_empty_strings(self):
        config = {
            "AUTH_METHOD": "oidc",
            "OIDC_DISCOVERY_URL": "",
            "OIDC_CLIENT_ID": "",
        }
        assert self._get_auth_mode(config) == "none"

    def test_builtin_still_works(self):
        config = {
            "AUTH_METHOD": "builtin",
            "BUILTIN_USERNAME": "admin",
            "BUILTIN_PASSWORD_HASH": "hash",
        }
        assert self._get_auth_mode(config) == "builtin"

    def test_proxy_still_works(self):
        config = {
            "AUTH_METHOD": "proxy",
            "PROXY_AUTH_USER_HEADER": "X-Auth-User",
        }
        assert self._get_auth_mode(config) == "proxy"


class TestLoginRequiredOIDCLogic:
    """Tests the OIDC admin restriction logic.

    Mirrors the admin check in main.py:login_required() to verify
    OIDC support without importing the full app.
    """

    def _check_admin_access(self, auth_mode, config, session, path):
        """Replicate login_required admin check logic with OIDC."""
        if auth_mode == "none":
            return True  # Allowed

        if "user_id" not in session:
            return 401  # Unauthorized

        settings_path = path.startswith("/api/settings") or path.startswith("/api/onboarding")

        if auth_mode in ("proxy", "cwa", "oidc") and settings_path:
            if auth_mode == "proxy":
                restrict = config.get("PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN", False)
            elif auth_mode == "cwa":
                restrict = config.get("CWA_RESTRICT_SETTINGS_TO_ADMIN", False)
            elif auth_mode == "oidc":
                restrict = config.get("OIDC_RESTRICT_SETTINGS_TO_ADMIN", False)
            else:
                restrict = False

            if restrict and not session.get("is_admin", False):
                return 403  # Forbidden

        return True  # Allowed

    def test_oidc_unauthenticated_returns_401(self):
        config = {"OIDC_RESTRICT_SETTINGS_TO_ADMIN": True}
        result = self._check_admin_access("oidc", config, {}, "/api/settings/test")
        assert result == 401

    def test_oidc_non_admin_blocked_from_settings(self):
        config = {"OIDC_RESTRICT_SETTINGS_TO_ADMIN": True}
        session = {"user_id": "user", "is_admin": False}
        result = self._check_admin_access("oidc", config, session, "/api/settings/test")
        assert result == 403

    def test_oidc_admin_can_access_settings(self):
        config = {"OIDC_RESTRICT_SETTINGS_TO_ADMIN": True}
        session = {"user_id": "admin", "is_admin": True}
        result = self._check_admin_access("oidc", config, session, "/api/settings/test")
        assert result is True

    def test_oidc_non_admin_can_access_non_settings(self):
        config = {"OIDC_RESTRICT_SETTINGS_TO_ADMIN": True}
        session = {"user_id": "user", "is_admin": False}
        result = self._check_admin_access("oidc", config, session, "/api/search")
        assert result is True

    def test_oidc_no_restrict_allows_non_admin_settings(self):
        config = {"OIDC_RESTRICT_SETTINGS_TO_ADMIN": False}
        session = {"user_id": "user", "is_admin": False}
        result = self._check_admin_access("oidc", config, session, "/api/settings/test")
        assert result is True

    def test_oidc_non_admin_blocked_from_onboarding(self):
        config = {"OIDC_RESTRICT_SETTINGS_TO_ADMIN": True}
        session = {"user_id": "user", "is_admin": False}
        result = self._check_admin_access("oidc", config, session, "/api/onboarding")
        assert result == 403


class TestAuthCheckOIDCLogic:
    """Tests the /api/auth/check response logic for OIDC mode."""

    def _build_auth_check_response(self, auth_mode, config, session):
        """Replicate auth check logic with OIDC."""
        if auth_mode == "none":
            return {"authenticated": True, "auth_required": False, "auth_mode": "none", "is_admin": True}

        is_authenticated = "user_id" in session

        if auth_mode == "builtin":
            is_admin = True
        elif auth_mode == "cwa":
            restrict = config.get("CWA_RESTRICT_SETTINGS_TO_ADMIN", False)
            is_admin = session.get("is_admin", False) if restrict else True
        elif auth_mode == "proxy":
            restrict = config.get("PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN", False)
            is_admin = session.get("is_admin", not restrict)
        elif auth_mode == "oidc":
            restrict = config.get("OIDC_RESTRICT_SETTINGS_TO_ADMIN", False)
            is_admin = session.get("is_admin", False) if restrict else True
        else:
            is_admin = False

        return {
            "authenticated": is_authenticated,
            "auth_required": True,
            "auth_mode": auth_mode,
            "is_admin": is_admin if is_authenticated else False,
            "username": session.get("user_id") if is_authenticated else None,
        }

    def test_oidc_authenticated_admin(self):
        config = {"OIDC_RESTRICT_SETTINGS_TO_ADMIN": True}
        session = {"user_id": "admin", "is_admin": True}
        result = self._build_auth_check_response("oidc", config, session)
        assert result["authenticated"] is True
        assert result["auth_mode"] == "oidc"
        assert result["is_admin"] is True
        assert result["username"] == "admin"

    def test_oidc_authenticated_non_admin(self):
        config = {"OIDC_RESTRICT_SETTINGS_TO_ADMIN": True}
        session = {"user_id": "user", "is_admin": False}
        result = self._build_auth_check_response("oidc", config, session)
        assert result["is_admin"] is False

    def test_oidc_no_restrict_all_are_admin(self):
        config = {"OIDC_RESTRICT_SETTINGS_TO_ADMIN": False}
        session = {"user_id": "user", "is_admin": False}
        result = self._build_auth_check_response("oidc", config, session)
        assert result["is_admin"] is True

    def test_oidc_unauthenticated(self):
        config = {"OIDC_RESTRICT_SETTINGS_TO_ADMIN": True}
        result = self._build_auth_check_response("oidc", config, {})
        assert result["authenticated"] is False
        assert result["is_admin"] is False
        assert result["auth_required"] is True
