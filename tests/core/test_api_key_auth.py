"""Tests for Bearer API key authentication on existing routes."""

from __future__ import annotations

import importlib
import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

from shelfmark.core import api_keys


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def user_db():
    from shelfmark.core.user_db import UserDB

    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "users.db"))
        db.initialize()
        yield db


@pytest.fixture
def wired(main_module, user_db, monkeypatch):
    monkeypatch.setattr(main_module, "user_db", user_db)
    monkeypatch.setattr(api_keys, "is_api_keys_enabled", lambda: True)
    api_keys.reset_last_used_throttle()
    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        yield main_module


def _issue(user_db, user):
    raw = api_keys.generate_key()
    user_db.create_api_key(user["id"], "t", api_keys.key_prefix(raw), api_keys.hash_key(raw), None)
    return raw


def _bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


class TestKeyedRequests:
    def test_valid_key_reaches_login_required_route(self, wired, user_db):
        user = user_db.create_user(username="alice")
        raw = _issue(user_db, user)

        # /api/auth/check is exempt (auth path); use a guarded route instead.
        response = wired.app.test_client().get("/api/downloads/active", headers=_bearer(raw))

        assert response.status_code == 200

    def test_no_set_cookie_on_keyed_request(self, wired, user_db):
        user = user_db.create_user(username="alice")
        raw = _issue(user_db, user)
        client = wired.app.test_client()
        # An existing session cookie must be neither refreshed nor deleted.
        with client.session_transaction() as sess:
            sess["some_preexisting_flag"] = True

        response = client.get("/api/downloads/active", headers=_bearer(raw))

        assert response.status_code == 200
        assert "Set-Cookie" not in response.headers

    def test_no_set_cookie_when_handler_dirties_session(self, wired, user_db, monkeypatch):
        from flask import session as flask_session

        user = user_db.create_user(username="alice")
        raw = _issue(user_db, user)

        def _dirty_session_view():
            # Simulate a handler that writes to the session; the after_request
            # hook must still strip any resulting Set-Cookie on a keyed request.
            flask_session["handler_wrote_this"] = True
            return wired.jsonify({"ok": True})

        monkeypatch.setitem(wired.app.view_functions, "api_active_downloads", _dirty_session_view)

        response = wired.app.test_client().get("/api/downloads/active", headers=_bearer(raw))

        assert response.status_code == 200
        assert "Set-Cookie" not in response.headers

    def test_x_api_key_header_accepted(self, wired, user_db):
        user = user_db.create_user(username="alice")
        raw = _issue(user_db, user)

        response = wired.app.test_client().get("/api/downloads/active", headers={"X-Api-Key": raw})

        assert response.status_code == 200

    def test_invalid_key_is_401_with_www_authenticate(self, wired):
        response = wired.app.test_client().get(
            "/api/downloads/active", headers=_bearer("smk_" + "x" * 43)
        )

        assert response.status_code == 401
        assert response.get_json() == {"error": "Invalid or expired API key"}
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    def test_invalid_key_does_not_fall_back_to_cookie(self, wired, user_db):
        admin = user_db.create_user(username="root", role="admin")
        client = wired.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = admin["username"]
            sess["is_admin"] = True
            sess["db_user_id"] = admin["id"]

        response = client.get("/api/downloads/active", headers=_bearer("smk_" + "x" * 43))

        assert response.status_code == 401

    def test_key_identity_overrides_cookie_identity(self, wired, user_db):
        admin = user_db.create_user(username="root", role="admin")
        alice = user_db.create_user(username="alice")
        raw = _issue(user_db, alice)
        client = wired.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = admin["username"]
            sess["is_admin"] = True
            sess["db_user_id"] = admin["id"]

        # Settings tabs require admin; alice's key must not inherit the admin cookie.
        response = client.get("/api/settings", headers=_bearer(raw))

        assert response.status_code == 403

    def test_admin_key_reaches_admin_route(self, wired, user_db):
        admin = user_db.create_user(username="root", role="admin")
        raw = _issue(user_db, admin)

        response = wired.app.test_client().get("/api/settings", headers=_bearer(raw))

        assert response.status_code == 200

    def test_role_change_applies_immediately(self, wired, user_db):
        admin = user_db.create_user(username="root", role="admin")
        raw = _issue(user_db, admin)
        client = wired.app.test_client()
        assert client.get("/api/settings", headers=_bearer(raw)).status_code == 200

        user_db.update_user(admin["id"], role="user")

        assert client.get("/api/settings", headers=_bearer(raw)).status_code == 403

    def test_login_endpoint_ignores_key(self, wired, user_db):
        user = user_db.create_user(username="alice")
        raw = _issue(user_db, user)

        response = wired.app.test_client().post(
            "/api/auth/login", headers=_bearer(raw), json={"username": "x", "password": "y"}
        )

        # Reaches the login handler (bad credentials), not the key middleware.
        assert response.status_code in (401, 403)
        assert response.get_json() != {"error": "Invalid or expired API key"}

    def test_health_ignores_bad_key(self, wired):
        response = wired.app.test_client().get("/api/health", headers=_bearer("smk_bad"))

        assert response.status_code == 200

    def test_disabled_switch_rejects_valid_key(self, wired, user_db, monkeypatch):
        user = user_db.create_user(username="alice")
        raw = _issue(user_db, user)
        monkeypatch.setattr(api_keys, "is_api_keys_enabled", lambda: False)

        response = wired.app.test_client().get("/api/downloads/active", headers=_bearer(raw))

        assert response.status_code == 401
        assert response.get_json() == {"error": "Invalid or expired API key"}

    def test_authenticate_error_is_500(self, wired, user_db, monkeypatch):
        user = user_db.create_user(username="alice")
        raw = _issue(user_db, user)

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise sqlite3.OperationalError("boom")

        monkeypatch.setattr(wired, "authenticate_api_key", _raise)

        response = wired.app.test_client().get("/api/downloads/active", headers=_bearer(raw))

        assert response.status_code == 500
        assert response.get_json() == {"error": "Authentication error"}

    def test_static_route_is_unaffected_by_bad_key(self, wired):
        response = wired.app.test_client().get("/", headers=_bearer("smk_bad"))

        assert response.status_code != 401

    def test_security_headers_present_on_keyed_response(self, wired, user_db):
        user = user_db.create_user(username="alice")
        raw = _issue(user_db, user)

        response = wired.app.test_client().get("/api/downloads/active", headers=_bearer(raw))

        assert response.status_code == 200
        assert response.headers.get("X-Content-Type-Options") == "nosniff"


class TestForeignBearerTokens:
    def test_foreign_bearer_token_falls_through_to_cookie_auth(self, wired, user_db):
        user = user_db.create_user(username="alice")
        client = wired.app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = user["username"]
            sess["is_admin"] = False
            sess["db_user_id"] = user["id"]

        response = client.get("/api/downloads/active", headers=_bearer("eyJhbGciOi.foo.bar"))

        assert response.status_code == 200

    def test_foreign_bearer_without_session_is_plain_unauthorized(self, wired):
        response = wired.app.test_client().get(
            "/api/downloads/active", headers=_bearer("eyJhbGciOi.foo.bar")
        )

        assert response.status_code == 401
        assert response.get_json() == {"error": "Unauthorized"}


class TestAuthModeInteraction:
    def test_none_mode_ignores_header(self, main_module, user_db, monkeypatch):
        monkeypatch.setattr(main_module, "user_db", user_db)
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            response = main_module.app.test_client().get(
                "/api/downloads/active", headers=_bearer("smk_bad")
            )

        assert response.status_code == 200

    def test_proxy_mode_keyed_request_needs_no_proxy_headers(
        self, main_module, user_db, monkeypatch
    ):
        monkeypatch.setattr(main_module, "user_db", user_db)
        monkeypatch.setattr(api_keys, "is_api_keys_enabled", lambda: True)
        api_keys.reset_last_used_throttle()
        user = user_db.create_user(username="alice", auth_source="proxy")
        raw = _issue(user_db, user)
        with patch.object(main_module, "get_auth_mode", return_value="proxy"):
            response = main_module.app.test_client().get(
                "/api/downloads/active", headers=_bearer(raw)
            )

        assert response.status_code == 200

    def test_no_user_db_rejects_key(self, main_module, monkeypatch):
        monkeypatch.setattr(main_module, "user_db", None)
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            response = main_module.app.test_client().get(
                "/api/downloads/active", headers=_bearer("smk_" + "x" * 43)
            )

        assert response.status_code == 401
        assert response.get_json() == {"error": "Invalid or expired API key"}
