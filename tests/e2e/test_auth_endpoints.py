"""Unit tests for authentication endpoints.

These tests exercise the Flask route functions in `shelfmark.main` using Flask
request contexts. They do not require the full application stack.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from typing import Any, Tuple
from unittest.mock import Mock, patch

import pytest


def _as_response(result: Any):
    """Normalize Flask view return values to a Response-like object."""
    if isinstance(result, tuple) and len(result) == 2:
        resp, status = result
        resp.status_code = status
        return resp
    return result


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background thread startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        # Reload to ensure patched orchestrator.start is used even if imported elsewhere.
        importlib.reload(main)
        return main


class TestGetAuthMode:
    def test_get_auth_mode_none(self, main_module):
        with patch("shelfmark.core.settings_registry.load_config_file", return_value={"AUTH_METHOD": "none"}):
            assert main_module.get_auth_mode() == "none"

    def test_get_auth_mode_builtin(self, main_module):
        with patch(
            "shelfmark.core.settings_registry.load_config_file",
            return_value={
                "AUTH_METHOD": "builtin",
                "BUILTIN_USERNAME": "admin",
                "BUILTIN_PASSWORD_HASH": "hashed_password",
            },
        ):
            assert main_module.get_auth_mode() == "builtin"

    def test_get_auth_mode_proxy(self, main_module):
        with patch(
            "shelfmark.core.settings_registry.load_config_file",
            return_value={"AUTH_METHOD": "proxy", "PROXY_AUTH_USER_HEADER": "X-Auth-User"},
        ):
            assert main_module.get_auth_mode() == "proxy"

    def test_get_auth_mode_cwa(self, main_module):
        with patch("shelfmark.core.settings_registry.load_config_file", return_value={"AUTH_METHOD": "cwa"}):
            with patch.object(main_module, "CWA_DB_PATH", object()):
                assert main_module.get_auth_mode() == "cwa"

    def test_get_auth_mode_default_on_error(self, main_module):
        with patch("shelfmark.core.settings_registry.load_config_file", side_effect=Exception("boom")):
            assert main_module.get_auth_mode() == "none"


class TestAuthCheckEndpoint:
    def test_auth_check_no_auth(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch("shelfmark.core.settings_registry.load_config_file", return_value={}):
                with main_module.app.test_request_context("/api/auth/check"):
                    resp = _as_response(main_module.api_auth_check())
                    data = resp.get_json()

        assert resp.status_code == 200
        assert data == {
            "authenticated": True,
            "auth_required": False,
            "auth_mode": "none",
            "is_admin": True,
        }

    def test_auth_check_builtin_not_authenticated(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch("shelfmark.core.settings_registry.load_config_file", return_value={}):
                with main_module.app.test_request_context("/api/auth/check"):
                    resp = _as_response(main_module.api_auth_check())
                    data = resp.get_json()

        assert resp.status_code == 200
        assert data["authenticated"] is False
        assert data["auth_required"] is True
        assert data["auth_mode"] == "builtin"
        assert data["is_admin"] is False
        assert data["username"] is None

    def test_auth_check_builtin_authenticated(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch("shelfmark.core.settings_registry.load_config_file", return_value={}):
                with main_module.app.test_request_context("/api/auth/check"):
                    main_module.session["user_id"] = "admin"
                    resp = _as_response(main_module.api_auth_check())
                    data = resp.get_json()

        assert resp.status_code == 200
        assert data["authenticated"] is True
        assert data["auth_required"] is True
        assert data["auth_mode"] == "builtin"
        assert data["is_admin"] is True
        assert data["username"] == "admin"

    def test_auth_check_proxy_includes_logout_url(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            with patch(
                "shelfmark.core.settings_registry.load_config_file",
                return_value={
                    "PROXY_AUTH_USER_HEADER": "X-Auth-User",
                    "PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN": True,
                    "PROXY_AUTH_LOGOUT_URL": "https://auth.example.com/logout",
                },
            ):
                with main_module.app.test_request_context("/api/auth/check"):
                    main_module.session["user_id"] = "proxyuser"
                    main_module.session["is_admin"] = True
                    resp = _as_response(main_module.api_auth_check())
                    data = resp.get_json()

        assert resp.status_code == 200
        assert data["authenticated"] is True
        assert data["auth_mode"] == "proxy"
        assert data["username"] == "proxyuser"
        assert data["logout_url"] == "https://auth.example.com/logout"


class TestLoginEndpoint:
    def test_login_proxy_mode_disabled(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            with main_module.app.test_request_context(
                "/api/auth/login",
                method="POST",
                json={"anything": "x"},
            ):
                resp = _as_response(main_module.api_login())
                data = resp.get_json()

        assert resp.status_code == 401
        assert "Proxy authentication" in (data.get("error") or "")

    def test_login_no_auth_success(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module, "is_account_locked", return_value=False):
                with main_module.app.test_request_context(
                    "/api/auth/login",
                    method="POST",
                    json={"username": "anyuser", "password": "anypass", "remember_me": True},
                ):
                    resp = _as_response(main_module.api_login())
                    data = resp.get_json()
                    assert main_module.session.get("user_id") == "anyuser"
                    assert main_module.session.permanent is True

        assert resp.status_code == 200
        assert data.get("success") is True

    def test_login_builtin_success(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "is_account_locked", return_value=False):
                with patch(
                    "shelfmark.core.settings_registry.load_config_file",
                    return_value={
                        "BUILTIN_USERNAME": "admin",
                        "BUILTIN_PASSWORD_HASH": "hash",
                    },
                ):
                    with patch.object(main_module, "check_password_hash", return_value=True):
                        with main_module.app.test_request_context(
                            "/api/auth/login",
                            method="POST",
                            json={"username": "admin", "password": "correct", "remember_me": False},
                        ):
                            resp = _as_response(main_module.api_login())
                            data = resp.get_json()
                            assert main_module.session.get("user_id") == "admin"

        assert resp.status_code == 200
        assert data.get("success") is True


class TestLogoutEndpoint:
    def test_logout_proxy_returns_logout_url(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            with patch(
                "shelfmark.core.settings_registry.load_config_file",
                return_value={"PROXY_AUTH_LOGOUT_URL": "https://auth.example.com/logout"},
            ):
                with main_module.app.test_request_context("/api/auth/logout", method="POST"):
                    main_module.session["user_id"] = "proxyuser"
                    resp = _as_response(main_module.api_logout())
                    data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert data["logout_url"] == "https://auth.example.com/logout"

    def test_logout_basic(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch("shelfmark.core.settings_registry.load_config_file", return_value={}):
                with main_module.app.test_request_context("/api/auth/logout", method="POST"):
                    main_module.session["user_id"] = "admin"
                    resp = _as_response(main_module.api_logout())
                    data = resp.get_json()

        assert resp.status_code == 200
        assert data["success"] is True
        assert "logout_url" not in data


class TestRateLimiting:
    def test_record_failed_login_increments_count(self, main_module):
        main_module.failed_login_attempts.clear()

        is_locked = main_module.record_failed_login("testuser", "127.0.0.1")

        assert is_locked is False
        assert main_module.failed_login_attempts["testuser"]["count"] == 1

    def test_account_locked_after_max_attempts(self, main_module):
        main_module.failed_login_attempts.clear()

        for _ in range(main_module.MAX_LOGIN_ATTEMPTS):
            is_locked = main_module.record_failed_login("testuser", "127.0.0.1")

        assert is_locked is True
        assert "lockout_until" in main_module.failed_login_attempts["testuser"]

    def test_is_account_locked(self, main_module):
        main_module.failed_login_attempts.clear()
        main_module.failed_login_attempts["testuser"] = {
            "count": 10,
            "lockout_until": datetime.now() + timedelta(hours=1),
        }

        assert main_module.is_account_locked("testuser") is True

    def test_clear_failed_logins(self, main_module):
        main_module.failed_login_attempts["testuser"] = {"count": 5}

        main_module.clear_failed_logins("testuser")

        assert "testuser" not in main_module.failed_login_attempts
