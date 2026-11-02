"""Pin the API-key and proxy-auth request hooks registered on the Flask app."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import patch

import pytest
from flask import Response, g, session

from shelfmark.core import api_key


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture(autouse=True)
def _empty_user_table(main_module):
    def _clear() -> None:
        for user in main_module.user_db.list_users():
            main_module.user_db.delete_user(user["id"])

    _clear()
    yield
    _clear()


def _hook(app, kind: str, name: str):
    funcs = app.before_request_funcs if kind == "before" else app.after_request_funcs
    for func in funcs[None]:
        if func.__name__ == name:
            return func
    raise AssertionError(f"{kind}_request hook {name} is not registered")


def _config_getter(values: dict[str, Any]):
    def _get(key: str, default: Any = None, user_id: Any = None):
        return values.get(key, default)

    return _get


def _proxy(main_module, path="/api/releases", *, headers=None, environ=None, config=None):
    """Run the proxy hook in a request context and return (result, session dict)."""
    values = {"PROXY_AUTH_USER_HEADER": "X-Auth-User", **(config or {})}
    with (
        patch.object(main_module, "get_auth_mode", return_value="proxy"),
        patch.object(main_module.app_config, "get", side_effect=_config_getter(values)),
        main_module.app.test_request_context(path, headers=headers, environ_base=environ),
    ):
        result = _hook(main_module.app, "before", "proxy_auth_middleware")()
        return result, dict(session), session.permanent


class TestHookOrder:
    def test_before_request_order(self, main_module):
        names = [f.__name__ for f in main_module.app.before_request_funcs[None]]
        assert names.index("api_key_auth_middleware") < names.index("proxy_auth_middleware")

    def test_after_request_order(self, main_module):
        names = [f.__name__ for f in main_module.app.after_request_funcs[None]]
        assert names.index("set_security_headers") < names.index(
            "strip_cookie_for_api_key_requests"
        )


class TestProxyGroups:
    @pytest.mark.parametrize(
        ("groups", "expected"),
        [
            ("admins", True),
            ("users,admins", True),
            ("users, admins ,other", True),
            ("users|admins", True),
            ("users", False),
            ("", False),
            ("administrators", False),
        ],
    )
    def test_group_header_maps_to_admin(self, main_module, groups, expected):
        headers = {"X-Auth-User": "gina"}
        if groups:
            headers["X-Auth-Groups"] = groups
        result, sess, permanent = _proxy(
            main_module, headers=headers, config={"PROXY_AUTH_ADMIN_GROUP_NAME": "admins"}
        )
        assert result is None
        assert sess["is_admin"] is expected
        assert permanent is False
        db_user = main_module.user_db.get_user(user_id=sess["db_user_id"])
        assert db_user["role"] == ("admin" if expected else "user")

    def test_custom_group_header(self, main_module):
        result, sess, _ = _proxy(
            main_module,
            headers={"X-Auth-User": "gina", "Remote-Groups": "ops"},
            config={
                "PROXY_AUTH_ADMIN_GROUP_NAME": "ops",
                "PROXY_AUTH_ADMIN_GROUP_HEADER": "Remote-Groups",
            },
        )
        assert result is None
        assert sess["is_admin"] is True

    def test_group_header_wsgi_environ_fallback(self, main_module):
        result, sess, _ = _proxy(
            main_module,
            headers={"X-Auth-User": "gina"},
            environ={"HTTP_X_AUTH_GROUPS": "admins"},
            config={"PROXY_AUTH_ADMIN_GROUP_NAME": "admins"},
        )
        assert result is None
        assert sess["is_admin"] is True

    def test_configured_group_overrides_stored_role(self, main_module):
        main_module.user_db.create_user(username="gina", role="admin", auth_source="proxy")
        result, sess, _ = _proxy(
            main_module,
            headers={"X-Auth-User": "gina", "X-Auth-Groups": "users"},
            config={"PROXY_AUTH_ADMIN_GROUP_NAME": "admins"},
        )
        assert result is None
        assert sess["is_admin"] is False

    def test_blank_user_header_setting_falls_back_to_default(self, main_module):
        result, sess, _ = _proxy(
            main_module,
            headers={"X-Auth-User": "gina"},
            config={"PROXY_AUTH_USER_HEADER": "   "},
        )
        assert result is None
        assert sess["user_id"] == "gina"


class TestProxyHeaderHandling:
    def test_missing_header_on_protected_path_is_401(self, main_module):
        result, sess, _ = _proxy(main_module)
        resp, status = result
        assert status == 401
        assert resp.get_json() == {"error": "Authentication required. Proxy header not set."}
        assert "user_id" not in sess

    def test_missing_header_on_auth_path_passes(self, main_module):
        result, sess, _ = _proxy(main_module, "/api/auth/login")
        assert result is None
        assert "user_id" not in sess

    def test_non_api_path_without_header_is_401(self, main_module):
        result, _, _ = _proxy(main_module, "/")
        assert result[1] == 401

    def test_header_ignored_outside_proxy_mode(self, main_module):
        for mode in ("builtin", "none", "cwa"):
            with (
                patch.object(main_module, "get_auth_mode", return_value=mode),
                main_module.app.test_request_context(
                    "/api/releases", headers={"X-Auth-User": "mallory"}
                ),
            ):
                assert _hook(main_module.app, "before", "proxy_auth_middleware")() is None
                assert "user_id" not in session
        assert main_module.user_db.get_user(username="mallory") is None

    def test_custom_user_header_is_case_insensitive(self, main_module):
        result, sess, _ = _proxy(
            main_module,
            headers={"x-forwarded-user": "gina"},
            config={"PROXY_AUTH_USER_HEADER": "X-Forwarded-User"},
        )
        assert result is None
        assert sess["user_id"] == "gina"

    def test_provisioned_once_per_session(self, main_module):
        _, sess, _ = _proxy(main_module, headers={"X-Auth-User": "gina"})
        first_id = sess["db_user_id"]
        with (
            patch.object(main_module, "get_auth_mode", return_value="proxy"),
            main_module.app.test_request_context("/api/releases", headers={"X-Auth-User": "gina"}),
        ):
            session["user_id"] = "gina"
            session["db_user_id"] = first_id
            assert _hook(main_module.app, "before", "proxy_auth_middleware")() is None
            assert session["db_user_id"] == first_id
        assert len(main_module.user_db.list_users()) == 1

    def test_store_error_is_500(self, main_module):
        with patch.object(main_module.user_db, "get_user_cached", side_effect=RuntimeError("boom")):
            result, _, _ = _proxy(main_module, headers={"X-Auth-User": "gina"})
        resp, status = result
        assert status == 500
        assert resp.get_json() == {"error": "Authentication error"}


class TestApiKeyScope:
    @pytest.fixture
    def keyed(self, main_module, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "s3cret")
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY_READONLY", "ro-key")
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            yield main_module

    def _run(self, main_module, path, key="s3cret", method="GET"):
        with main_module.app.test_request_context(path, method=method, headers={"X-Api-Key": key}):
            result = _hook(main_module.app, "before", "api_key_auth_middleware")()
            return result, dict(session), g.get("api_key_auth"), g.get("api_key_scope")

    @pytest.mark.parametrize("path", ["/", "/index.html", "/static/app.js", "/apix", "/api"])
    def test_non_api_paths_ignore_key(self, keyed, path):
        result, sess, flag, scope = self._run(keyed, path)
        assert result is None
        assert sess == {}
        assert flag is None
        assert scope is None

    @pytest.mark.parametrize("path", ["/api/health", "/api/auth/check", "/api/auth/login"])
    def test_exempt_paths_ignore_key(self, keyed, path):
        result, sess, flag, _ = self._run(keyed, path)
        assert result is None
        assert sess == {}
        assert flag is None

    def test_admin_key_marks_request_and_sets_admin_session(self, keyed):
        admin = keyed.user_db.create_user(username="root", role="admin")
        result, sess, flag, scope = self._run(keyed, "/api/downloads/active")
        assert result is None
        assert flag is True
        assert scope == "admin"
        assert sess["user_id"] == "root"
        assert sess["is_admin"] is True
        assert sess["db_user_id"] == admin["id"]

    def test_admin_key_without_admin_account_uses_api_identity(self, keyed):
        _, sess, _, _ = self._run(keyed, "/api/downloads/active")
        assert sess["user_id"] == "api"
        assert sess["is_admin"] is True
        assert "db_user_id" not in sess

    def test_admin_key_session_is_not_persisted(self, keyed):
        with keyed.app.test_request_context(
            "/api/downloads/active", headers={"X-Api-Key": "s3cret"}
        ):
            _hook(keyed.app, "before", "api_key_auth_middleware")()
            assert session.permanent is False
            assert session.modified is False

    def test_wrong_key_falls_through_untouched(self, keyed):
        result, sess, flag, scope = self._run(keyed, "/api/downloads/active", key="nope")
        assert result is None
        assert sess == {}
        assert flag is None
        assert scope is None

    def test_readonly_key_allowed_on_stats_get_only(self, keyed):
        result, sess, flag, scope = self._run(keyed, "/api/stats", key="ro-key")
        assert result is None
        assert (sess, flag, scope) == ({}, True, "readonly")

    @pytest.mark.parametrize(
        ("path", "method"),
        [("/api/stats", "POST"), ("/api/downloads/active", "GET"), ("/api/stats/x", "GET")],
    )
    def test_readonly_key_refused_elsewhere(self, keyed, path, method):
        result, _, flag, scope = self._run(keyed, path, key="ro-key", method=method)
        resp, status = result
        assert status == 403
        assert resp.get_json() == {"error": "This API key is read-only"}
        assert flag is True
        assert scope == "readonly"

    def test_none_mode_ignores_key(self, main_module, monkeypatch):
        monkeypatch.setattr(api_key, "SHELFMARK_API_KEY", "s3cret")
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            result, sess, flag, _ = self._run(main_module, "/api/downloads/active")
        assert (result, sess, flag) == (None, {}, None)

    def test_store_error_is_500_and_still_marked_keyed(self, keyed):
        with patch.object(keyed.user_db, "get_first_admin", side_effect=RuntimeError("boom")):
            result, _, flag, _ = self._run(keyed, "/api/downloads/active")
        resp, status = result
        assert status == 500
        assert resp.get_json() == {"error": "Authentication error"}
        assert flag is True

    def test_proxy_mode_keyed_request_skips_proxy_hook(self, keyed):
        with (
            patch.object(keyed, "get_auth_mode", return_value="proxy"),
            keyed.app.test_request_context(
                "/api/downloads/active", headers={"X-Api-Key": "s3cret"}
            ),
        ):
            assert _hook(keyed.app, "before", "api_key_auth_middleware")() is None
            assert _hook(keyed.app, "before", "proxy_auth_middleware")() is None
            assert session["is_admin"] is True


class TestCookieStripping:
    def _strip(self, main_module, *, keyed: bool):
        with main_module.app.test_request_context("/api/downloads/active"):
            if keyed:
                g.api_key_auth = True
            session["dirty"] = True
            session.permanent = True
            resp = _hook(main_module.app, "after", "strip_cookie_for_api_key_requests")(
                Response("ok")
            )
            return resp, session.modified, session.permanent

    def test_keyed_request_resets_session_flags(self, main_module):
        resp, modified, permanent = self._strip(main_module, keyed=True)
        assert resp.get_data(as_text=True) == "ok"
        assert modified is False
        assert permanent is False

    def test_unkeyed_request_is_untouched(self, main_module):
        _, modified, permanent = self._strip(main_module, keyed=False)
        assert modified is True
        assert permanent is True
