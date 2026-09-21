"""Unit tests for proxy auth middleware and admin access checks."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

pytestmark = pytest.mark.e2e


def _as_response(result: Any):
    if isinstance(result, tuple) and len(result) == 2:
        resp, status = result
        resp.status_code = status
        return resp
    return result


def _config_getter(values: dict[str, Any]):
    def _get(key: str, default: Any = None, user_id: Any = None):
        return values.get(key, default)

    return _get


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture(autouse=True)
def _empty_user_table(main_module):
    """Give every test an instance with no accounts yet.

    Proxy provisioning keys off whether the instance already has an admin, so
    two tests sharing a user table are really asserting the order they happened
    to run in. The suite runs with xdist, where each worker gets its own
    CONFIG_DIR, so that order changes whenever the test count does.
    """

    def _clear() -> None:
        for user in main_module.user_db.list_users():
            main_module.user_db.delete_user(user["id"])

    _clear()
    yield
    _clear()


class TestProxyAuthMiddleware:
    def test_skips_for_non_proxy_mode(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            main_module.app.test_request_context("/api/releases"),
        ):
            result = main_module.proxy_auth_middleware()
            assert result is None
            assert "user_id" not in main_module.session

    def test_skips_health_endpoint(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            main_module.app.test_request_context("/api/health"),
        ):
            result = main_module.proxy_auth_middleware()
            assert result is None

    def test_allows_auth_check_without_header(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"PROXY_AUTH_USER_HEADER": "X-Auth-User"}),
            ),
            main_module.app.test_request_context("/api/auth/check"),
        ):
            result = main_module.proxy_auth_middleware()
            assert result is None
            assert "user_id" not in main_module.session

    def test_sets_session_from_header(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"PROXY_AUTH_USER_HEADER": "X-Auth-User"}),
            ),
            main_module.app.test_request_context(
                "/api/releases",
                headers={"X-Auth-User": "proxyuser"},
            ),
        ):
            result = main_module.proxy_auth_middleware()
            assert result is None
            assert main_module.session.get("user_id") == "proxyuser"
            assert main_module.session.get("is_admin") is True
            db_user_id = main_module.session.get("db_user_id")
            assert db_user_id is not None
            db_user = main_module.user_db.get_user(user_id=db_user_id)
            assert db_user is not None
            assert db_user["username"] == "proxyuser"
            assert db_user["auth_source"] == "proxy"
            assert main_module.session.permanent is False

    def test_reads_remote_user_wsgi_fallback(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"PROXY_AUTH_USER_HEADER": "Remote-User"}),
            ),
            main_module.app.test_request_context(
                "/api/releases",
                environ_base={"REMOTE_USER": "proxyremote"},
            ),
        ):
            result = main_module.proxy_auth_middleware()
            assert result is None
            assert main_module.session.get("user_id") == "proxyremote"
            assert main_module.session.get("is_admin") is True
            assert main_module.session.permanent is False

    def test_proxy_takes_over_existing_local_username(self, main_module):
        existing = main_module.user_db.create_user(
            username="proxy_takeover_local",
            role="user",
            auth_source="builtin",
        )

        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"PROXY_AUTH_USER_HEADER": "X-Auth-User"}),
            ),
            main_module.app.test_request_context(
                "/api/releases",
                headers={"X-Auth-User": "proxy_takeover_local"},
            ),
        ):
            result = main_module.proxy_auth_middleware()
            assert result is None

            db_user_id = main_module.session.get("db_user_id")
            db_user = main_module.user_db.get_user(user_id=db_user_id)
            assert db_user is not None
            assert db_user["id"] == existing["id"]
            assert db_user["username"] == "proxy_takeover_local"
            assert db_user["auth_source"] == "proxy"

    def test_reprovisions_when_proxy_identity_changes(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"PROXY_AUTH_USER_HEADER": "X-Auth-User"}),
            ),
            main_module.app.test_request_context(
                "/api/releases",
                headers={"X-Auth-User": "proxyuser2"},
            ),
        ):
            main_module.session["user_id"] = "old-user"
            main_module.session["db_user_id"] = 999999

            result = main_module.proxy_auth_middleware()
            assert result is None
            assert main_module.session.get("user_id") == "proxyuser2"
            db_user_id = main_module.session.get("db_user_id")
            db_user = main_module.user_db.get_user(user_id=db_user_id)
            assert db_user["username"] == "proxyuser2"

    def test_reprovisions_when_session_db_user_is_stale(self, main_module):
        stale_user_id = 99999999
        username = f"proxy_stale_{uuid4().hex[:8]}"
        assert main_module.user_db.get_user(user_id=stale_user_id) is None

        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"PROXY_AUTH_USER_HEADER": "X-Auth-User"}),
            ),
            main_module.app.test_request_context(
                "/api/releases",
                headers={"X-Auth-User": username},
            ),
        ):
            main_module.session["user_id"] = username
            main_module.session["db_user_id"] = stale_user_id

            result = main_module.proxy_auth_middleware()
            assert result is None
            assert main_module.session.get("user_id") == username

            db_user_id = main_module.session.get("db_user_id")
            assert db_user_id is not None
            assert db_user_id != stale_user_id

            db_user = main_module.user_db.get_user(user_id=db_user_id)
            assert db_user is not None
            assert db_user["username"] == username

    def test_reprovisions_when_session_db_user_points_to_other_username(self, main_module):
        username = f"proxy_target_{uuid4().hex[:8]}"
        other_user = main_module.user_db.create_user(
            username=f"proxy_other_{uuid4().hex[:8]}",
            role="user",
            auth_source="proxy",
        )

        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"PROXY_AUTH_USER_HEADER": "X-Auth-User"}),
            ),
            main_module.app.test_request_context(
                "/api/releases",
                headers={"X-Auth-User": username},
            ),
        ):
            main_module.session["user_id"] = username
            main_module.session["db_user_id"] = other_user["id"]

            result = main_module.proxy_auth_middleware()
            assert result is None
            assert main_module.session.get("user_id") == username

            db_user_id = main_module.session.get("db_user_id")
            assert db_user_id is not None
            assert db_user_id != other_user["id"]

            db_user = main_module.user_db.get_user(user_id=db_user_id)
            assert db_user is not None
            assert db_user["username"] == username

    def test_first_account_is_admin_and_later_ones_are_not(self, main_module):
        """The bootstrap account is an admin; after that the default role decides."""
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"PROXY_AUTH_USER_HEADER": "X-Auth-User"}),
            ),
        ):
            with main_module.app.test_request_context(
                "/api/releases",
                headers={"X-Auth-User": "first_proxy_user"},
            ):
                assert main_module.proxy_auth_middleware() is None
                assert main_module.session.get("is_admin") is True

            # The instance now has an admin, so nobody is at risk of being locked
            # out and the next account follows PROXY_AUTH_DEFAULT_ROLE.
            with main_module.app.test_request_context(
                "/api/releases",
                headers={"X-Auth-User": "second_proxy_user"},
            ):
                assert main_module.proxy_auth_middleware() is None
                assert main_module.session.get("is_admin") is False

    def test_default_role_admin_promotes_later_accounts(self, main_module):
        main_module.user_db.create_user(
            username="existing_admin",
            role="admin",
            auth_source="proxy",
        )

        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter(
                    {
                        "PROXY_AUTH_USER_HEADER": "X-Auth-User",
                        "PROXY_AUTH_DEFAULT_ROLE": "admin",
                    }
                ),
            ),
            main_module.app.test_request_context(
                "/api/releases",
                headers={"X-Auth-User": "later_proxy_admin"},
            ),
        ):
            assert main_module.proxy_auth_middleware() is None
            assert main_module.session.get("is_admin") is True

    def test_existing_user_keeps_its_stored_role(self, main_module):
        main_module.user_db.create_user(
            username="existing_admin",
            role="admin",
            auth_source="proxy",
        )
        main_module.user_db.create_user(
            username="known_plain_user",
            role="user",
            auth_source="proxy",
        )

        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter(
                    {
                        "PROXY_AUTH_USER_HEADER": "X-Auth-User",
                        "PROXY_AUTH_DEFAULT_ROLE": "admin",
                    }
                ),
            ),
            main_module.app.test_request_context(
                "/api/releases",
                headers={"X-Auth-User": "known_plain_user"},
            ),
        ):
            assert main_module.proxy_auth_middleware() is None
            # The stored role wins; the default only applies to new accounts.
            assert main_module.session.get("is_admin") is False

    def test_returns_401_when_header_missing_on_protected_path(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter({"PROXY_AUTH_USER_HEADER": "X-Auth-User"}),
            ),
            main_module.app.test_request_context("/api/releases"),
        ):
            resp = _as_response(main_module.proxy_auth_middleware())
            data = resp.get_json()

        assert resp.status_code == 401
        assert data == {"error": "Authentication required. Proxy header not set."}

    def test_admin_group_membership(self, main_module):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=_config_getter(
                    {
                        "PROXY_AUTH_USER_HEADER": "X-Auth-User",
                        "PROXY_AUTH_ADMIN_GROUP_HEADER": "X-Auth-Groups",
                        "PROXY_AUTH_ADMIN_GROUP_NAME": "admins",
                    }
                ),
            ),
            main_module.app.test_request_context(
                "/api/releases",
                headers={
                    "X-Auth-User": "adminuser",
                    "X-Auth-Groups": "users,admins,devs",
                },
            ),
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
        with (
            patch.object(main_module, "get_auth_mode", return_value="none"),
            main_module.app.test_request_context("/api/releases"),
        ):
            decorated = main_module.login_required(view)
            resp = decorated()

        assert resp[0]["success"] is True

    def test_blocks_when_not_authenticated(self, main_module, view):
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            main_module.app.test_request_context("/api/releases"),
        ):
            decorated = main_module.login_required(view)
            resp = _as_response(decorated())

        assert resp.status_code == 401

    def test_allows_authenticated(self, main_module, view):
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            main_module.app.test_request_context("/api/releases"),
        ):
            main_module.session["user_id"] = "user"
            decorated = main_module.login_required(view)
            resp = decorated()

        assert resp[0]["success"] is True

    def test_settings_access_requires_admin_even_when_legacy_toggle_off(self, main_module, view):
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            main_module.app.test_request_context("/api/settings/general"),
        ):
            main_module.session["user_id"] = "user"
            main_module.session["is_admin"] = False
            decorated = main_module.login_required(view)
            resp = _as_response(decorated())
            data = resp.get_json()

        assert resp.status_code == 403
        assert "Admin access required" in (data.get("error") or "")

    def test_security_tab_always_blocks_non_admin_even_when_toggle_off(self, main_module, view):
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            main_module.app.test_request_context("/api/settings/security"),
        ):
            main_module.session["user_id"] = "user"
            main_module.session["is_admin"] = False
            decorated = main_module.login_required(view)
            resp = _as_response(decorated())
            data = resp.get_json()

        assert resp.status_code == 403
        assert "Admin access required" in (data.get("error") or "")

    def test_users_tab_always_blocks_non_admin_even_when_toggle_off(self, main_module, view):
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            main_module.app.test_request_context("/api/settings/users"),
        ):
            main_module.session["user_id"] = "user"
            main_module.session["is_admin"] = False
            decorated = main_module.login_required(view)
            resp = _as_response(decorated())
            data = resp.get_json()

        assert resp.status_code == 403
        assert "Admin access required" in (data.get("error") or "")

    def test_proxy_admin_restriction_blocks_non_admin(self, main_module, view):
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            main_module.app.test_request_context("/api/settings/general"),
        ):
            main_module.session["user_id"] = "user"
            main_module.session["is_admin"] = False
            decorated = main_module.login_required(view)
            resp = _as_response(decorated())
            data = resp.get_json()

        assert resp.status_code == 403
        assert "Admin access required" in (data.get("error") or "")

    def test_cwa_admin_restriction_blocks_non_admin(self, main_module, view):
        with (
            patch.object(main_module, "get_auth_mode", return_value="cwa"),
            main_module.app.test_request_context("/api/settings/general"),
        ):
            main_module.session["user_id"] = "user"
            main_module.session["is_admin"] = False
            decorated = main_module.login_required(view)
            resp = _as_response(decorated())

        assert resp.status_code == 403
