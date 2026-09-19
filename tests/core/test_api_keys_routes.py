"""Tests for self-service and admin API key routes."""

from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core import api_keys
from shelfmark.core.admin_routes import register_admin_routes
from shelfmark.core.self_user_routes import register_self_user_routes
from shelfmark.core.user_db import UserDB


@pytest.fixture
def user_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "shelfmark.db"))
        db.initialize()
        yield db


@pytest.fixture
def app(user_db):
    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True
    register_self_user_routes(test_app, user_db)
    register_admin_routes(test_app, user_db)
    return test_app


@pytest.fixture(autouse=True)
def _builtin_mode(monkeypatch):
    monkeypatch.setattr(api_keys, "is_api_keys_enabled", lambda: True)
    with (
        patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"),
        patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"),
    ):
        yield


def _client_for(app: Flask, user: dict[str, Any], *, is_admin: bool = False) -> Any:
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["db_user_id"] = user["id"]
        sess["is_admin"] = is_admin
    return client


class TestSelfRoutes:
    def test_list_empty(self, app, user_db):
        alice = user_db.create_user(username="alice")
        resp = _client_for(app, alice).get("/api/users/me/api-keys")
        assert resp.status_code == 200
        assert resp.get_json() == {"keys": []}

    def test_create_returns_token_once_and_lists_without_it(self, app, user_db):
        alice = user_db.create_user(username="alice")
        client = _client_for(app, alice)

        resp = client.post("/api/users/me/api-keys", json={"name": "laptop"})

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["token"].startswith("smk_")
        assert body["key"]["name"] == "laptop"
        assert body["key"]["key_prefix"] == body["token"][:12]
        assert "key_hash" not in body["key"]
        assert body["key"]["expires_at"] is None

        listed = client.get("/api/users/me/api-keys").get_json()["keys"]
        assert [k["id"] for k in listed] == [body["key"]["id"]]
        assert "token" not in listed[0]

    def test_create_with_expiry(self, app, user_db):
        alice = user_db.create_user(username="alice")
        resp = _client_for(app, alice).post(
            "/api/users/me/api-keys", json={"name": "short", "expires_in_days": 30}
        )
        assert resp.status_code == 201
        assert resp.get_json()["key"]["expires_at"] is not None

    def test_created_token_authenticates(self, app, user_db):
        alice = user_db.create_user(username="alice")
        token = (
            _client_for(app, alice)
            .post("/api/users/me/api-keys", json={"name": "laptop"})
            .get_json()["token"]
        )
        assert api_keys.authenticate(user_db, token, "builtin") is not None

    @pytest.mark.parametrize(
        "payload",
        [{}, {"name": ""}, {"name": "x", "expires_in_days": 0}, {"name": "x" * 65}],
    )
    def test_create_rejects_invalid(self, app, user_db, payload):
        alice = user_db.create_user(username="alice")
        resp = _client_for(app, alice).post("/api/users/me/api-keys", json=payload)
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_create_rejects_non_json(self, app, user_db):
        alice = user_db.create_user(username="alice")
        resp = _client_for(app, alice).post("/api/users/me/api-keys", data="nope")
        assert resp.status_code == 400

    def test_create_enforces_active_limit(self, app, user_db):
        alice = user_db.create_user(username="alice")
        client = _client_for(app, alice)
        for i in range(api_keys.MAX_ACTIVE_KEYS_PER_USER):
            assert client.post("/api/users/me/api-keys", json={"name": f"k{i}"}).status_code == 201

        resp = client.post("/api/users/me/api-keys", json={"name": "one-too-many"})

        assert resp.status_code == 409

    def test_create_blocked_when_disabled(self, app, user_db, monkeypatch):
        alice = user_db.create_user(username="alice")
        monkeypatch.setattr(api_keys, "is_api_keys_enabled", lambda: False)
        resp = _client_for(app, alice).post("/api/users/me/api-keys", json={"name": "x"})
        assert resp.status_code == 403
        assert resp.get_json() == {"error": "API keys are disabled"}

    def test_list_and_revoke_still_work_when_disabled(self, app, user_db, monkeypatch):
        alice = user_db.create_user(username="alice")
        client = _client_for(app, alice)
        key_id = client.post("/api/users/me/api-keys", json={"name": "x"}).get_json()["key"]["id"]
        monkeypatch.setattr(api_keys, "is_api_keys_enabled", lambda: False)

        assert client.get("/api/users/me/api-keys").status_code == 200
        assert client.delete(f"/api/users/me/api-keys/{key_id}").status_code == 200

    def test_revoke_own_key(self, app, user_db):
        alice = user_db.create_user(username="alice")
        client = _client_for(app, alice)
        created = client.post("/api/users/me/api-keys", json={"name": "x"}).get_json()

        resp = client.delete(f"/api/users/me/api-keys/{created['key']['id']}")

        assert resp.status_code == 200
        assert resp.get_json() == {"success": True}
        listed = client.get("/api/users/me/api-keys").get_json()["keys"]
        assert listed[0]["revoked_at"] is not None
        assert api_keys.authenticate(user_db, created["token"], "builtin") is None

    def test_revoke_other_users_key_is_404(self, app, user_db):
        alice = user_db.create_user(username="alice")
        bob = user_db.create_user(username="bob")
        key_id = (
            _client_for(app, alice)
            .post("/api/users/me/api-keys", json={"name": "x"})
            .get_json()["key"]["id"]
        )

        resp = _client_for(app, bob).delete(f"/api/users/me/api-keys/{key_id}")

        assert resp.status_code == 404
        assert user_db.get_api_key(key_id, alice["id"])["revoked_at"] is None

    def test_requires_session(self, app):
        resp = app.test_client().get("/api/users/me/api-keys")
        assert resp.status_code == 401


class TestAdminRoutes:
    def test_admin_lists_user_keys(self, app, user_db):
        root = user_db.create_user(username="root", role="admin")
        alice = user_db.create_user(username="alice")
        _client_for(app, alice).post("/api/users/me/api-keys", json={"name": "laptop"})

        resp = _client_for(app, root, is_admin=True).get(f"/api/admin/users/{alice['id']}/api-keys")

        assert resp.status_code == 200
        keys = resp.get_json()["keys"]
        assert [k["name"] for k in keys] == ["laptop"]
        assert "key_hash" not in keys[0]

    def test_admin_list_unknown_user_is_404(self, app, user_db):
        root = user_db.create_user(username="root", role="admin")
        resp = _client_for(app, root, is_admin=True).get("/api/admin/users/9999/api-keys")
        assert resp.status_code == 404

    def test_admin_revokes_user_key(self, app, user_db):
        root = user_db.create_user(username="root", role="admin")
        alice = user_db.create_user(username="alice")
        created = (
            _client_for(app, alice).post("/api/users/me/api-keys", json={"name": "x"}).get_json()
        )

        resp = _client_for(app, root, is_admin=True).delete(
            f"/api/admin/users/{alice['id']}/api-keys/{created['key']['id']}"
        )

        assert resp.status_code == 200
        assert api_keys.authenticate(user_db, created["token"], "builtin") is None

    def test_admin_revoke_wrong_user_is_404(self, app, user_db):
        root = user_db.create_user(username="root", role="admin")
        alice = user_db.create_user(username="alice")
        bob = user_db.create_user(username="bob")
        key_id = (
            _client_for(app, alice)
            .post("/api/users/me/api-keys", json={"name": "x"})
            .get_json()["key"]["id"]
        )

        resp = _client_for(app, root, is_admin=True).delete(
            f"/api/admin/users/{bob['id']}/api-keys/{key_id}"
        )

        assert resp.status_code == 404

    def test_non_admin_cannot_use_admin_routes(self, app, user_db):
        alice = user_db.create_user(username="alice")
        resp = _client_for(app, alice).get(f"/api/admin/users/{alice['id']}/api-keys")
        assert resp.status_code == 403
