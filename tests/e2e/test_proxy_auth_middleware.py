"""Unit tests for proxy auth middleware and admin access checks."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import patch

import pytest


def _as_response(result: Any):
    if isinstance(result, tuple) and len(result) == 2:
        resp, status = result
        resp.status_code = status
        return resp
    return result


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


class TestProxyAuthMiddleware:
    def test_skips_for_non_proxy_mode(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with main_module.app.test_request_context("/api/search"):
                result = main_module.proxy_auth_middleware()
                assert result is None
                assert "user_id" not in main_module.session

    def test_skips_health_endpoint(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            with main_module.app.test_request_context("/api/health"):
                result = main_module.proxy_auth_middleware()
                assert result is None

    def test_allows_auth_check_without_header(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            with patch(
                "shelfmark.core.settings_registry.load_config_file",
                return_value={"PROXY_AUTH_USER_HEADER": "X-Auth-User"},
            ):
                with main_module.app.test_request_context("/api/auth/check"):
                    result = main_module.proxy_auth_middleware()
                    assert result is None
                    assert "user_id" not in main_module.session

    def test_sets_session_from_header(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            with patch(
                "shelfmark.core.settings_registry.load_config_file",
                return_value={
                    "PROXY_AUTH_USER_HEADER": "X-Auth-User",
                    "PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN": False,
                },
            ):
                with main_module.app.test_request_context(
                    "/api/search",
                    headers={"X-Auth-User": "proxyuser"},
                ):
                    result = main_module.proxy_auth_middleware()
                    assert result is None
                    assert main_module.session.get("user_id") == "proxyuser"
                    assert main_module.session.get("is_admin") is True
                    assert main_module.session.permanent is False

    def test_returns_401_when_header_missing_on_protected_path(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            with patch(
                "shelfmark.core.settings_registry.load_config_file",
                return_value={"PROXY_AUTH_USER_HEADER": "X-Auth-User"},
            ):
                with main_module.app.test_request_context("/api/search"):
                    resp = _as_response(main_module.proxy_auth_middleware())
                    data = resp.get_json()

        assert resp.status_code == 401
        assert "Authentication required" in (data.get("error") or "")

    def test_admin_group_membership(self, main_module):
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            with patch(
                "shelfmark.core.settings_registry.load_config_file",
                return_value={
                    "PROXY_AUTH_USER_HEADER": "X-Auth-User",
                    "PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN": True,
                    "PROXY_AUTH_ADMIN_GROUP_HEADER": "X-Auth-Groups",
                    "PROXY_AUTH_ADMIN_GROUP_NAME": "admins",
                },
            ):
                with main_module.app.test_request_context(
                    "/api/search",
                    headers={
                        "X-Auth-User": "adminuser",
                        "X-Auth-Groups": "users,admins,devs",
                    },
                ):
                    result = main_module.proxy_auth_middleware()
                    assert result is None
                    assert main_module.session.get("is_admin") is True


class TestLoginRequiredDecorator:
    @pytest.fixture
    def view(self):
        def _view():
            return {"success": True}, 200

        return _view

    def test_allows_no_auth(self, main_module, view):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with main_module.app.test_request_context("/api/search"):
                decorated = main_module.login_required(view)
                resp = decorated()

        assert resp[0]["success"] is True

    def test_blocks_when_not_authenticated(self, main_module, view):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with main_module.app.test_request_context("/api/search"):
                decorated = main_module.login_required(view)
                resp = _as_response(decorated())

        assert resp.status_code == 401

    def test_allows_authenticated(self, main_module, view):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with main_module.app.test_request_context("/api/search"):
                main_module.session["user_id"] = "user"
                decorated = main_module.login_required(view)
                resp = decorated()

        assert resp[0]["success"] is True

    def test_builtin_mode_does_not_apply_cwa_admin_setting(self, main_module, view):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch(
                "shelfmark.core.settings_registry.load_config_file",
                return_value={"CWA_RESTRICT_SETTINGS_TO_ADMIN": True},
            ):
                with main_module.app.test_request_context("/api/settings/general"):
                    main_module.session["user_id"] = "admin"
                    decorated = main_module.login_required(view)
                    resp = decorated()

        assert resp[0]["success"] is True

    def test_proxy_admin_restriction_blocks_non_admin(self, main_module, view):
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            with patch(
                "shelfmark.core.settings_registry.load_config_file",
                return_value={"PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN": True},
            ):
                with main_module.app.test_request_context("/api/settings/general"):
                    main_module.session["user_id"] = "user"
                    main_module.session["is_admin"] = False
                    decorated = main_module.login_required(view)
                    resp = _as_response(decorated())
                    data = resp.get_json()

        assert resp.status_code == 403
        assert "Admin access required" in (data.get("error") or "")

    def test_cwa_admin_restriction_blocks_non_admin(self, main_module, view):
        with patch.object(main_module, "get_auth_mode", return_value="cwa"):
            with patch(
                "shelfmark.core.settings_registry.load_config_file",
                return_value={"CWA_RESTRICT_SETTINGS_TO_ADMIN": True},
            ):
                with main_module.app.test_request_context("/api/settings/general"):
                    main_module.session["user_id"] = "user"
                    main_module.session["is_admin"] = False
                    decorated = main_module.login_required(view)
                    resp = _as_response(decorated())

        assert resp.status_code == 403
