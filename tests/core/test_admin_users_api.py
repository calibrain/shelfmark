"""
Tests for admin user management API routes.

Tests CRUD endpoints for managing users from the admin panel.
"""

import json
import os
import tempfile

import pytest
from flask import Flask

from shelfmark.core.user_db import UserDB


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "shelfmark.db")


@pytest.fixture
def user_db(db_path):
    db = UserDB(db_path)
    db.initialize()
    return db


@pytest.fixture
def app(user_db):
    from shelfmark.core.admin_routes import register_admin_routes

    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True

    register_admin_routes(test_app, user_db)
    return test_app


@pytest.fixture
def admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "admin"
        sess["is_admin"] = True
    return client


@pytest.fixture
def regular_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "user"
        sess["is_admin"] = False
    return client


class TestAdminUsersListEndpoint:
    """Tests for GET /api/admin/users."""

    def test_list_users_empty(self, admin_client):
        resp = admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        assert resp.json == []

    def test_list_users_returns_all(self, admin_client, user_db):
        user_db.create_user(username="alice", email="alice@example.com")
        user_db.create_user(username="bob", email="bob@example.com")

        resp = admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        assert len(resp.json) == 2
        usernames = [u["username"] for u in resp.json]
        assert "alice" in usernames
        assert "bob" in usernames

    def test_list_users_excludes_password_hash(self, admin_client, user_db):
        user_db.create_user(username="alice", password_hash="secret_hash")

        resp = admin_client.get("/api/admin/users")
        users = resp.json
        assert "password_hash" not in users[0]

    def test_list_users_requires_admin(self, regular_client):
        resp = regular_client.get("/api/admin/users")
        assert resp.status_code == 403


class TestAdminUserGetEndpoint:
    """Tests for GET /api/admin/users/<id>."""

    def test_get_user(self, admin_client, user_db):
        user = user_db.create_user(username="alice", email="alice@example.com")

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 200
        assert resp.json["username"] == "alice"
        assert resp.json["email"] == "alice@example.com"

    def test_get_user_includes_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"booklore_library_id": 5})

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert resp.json["settings"]["booklore_library_id"] == 5

    def test_get_nonexistent_user(self, admin_client):
        resp = admin_client.get("/api/admin/users/9999")
        assert resp.status_code == 404


class TestAdminUserUpdateEndpoint:
    """Tests for PUT /api/admin/users/<id>."""

    def test_update_user_role(self, admin_client, user_db):
        user = user_db.create_user(username="alice", role="user")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )
        assert resp.status_code == 200
        updated = user_db.get_user(user_id=user["id"])
        assert updated["role"] == "admin"

    def test_update_user_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"booklore_library_id": 3}},
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings["booklore_library_id"] == 3

    def test_update_nonexistent_user(self, admin_client):
        resp = admin_client.put(
            "/api/admin/users/9999",
            json={"role": "admin"},
        )
        assert resp.status_code == 404


class TestAdminUserDeleteEndpoint:
    """Tests for DELETE /api/admin/users/<id>."""

    def test_delete_user(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.delete(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 200
        assert user_db.get_user(user_id=user["id"]) is None

    def test_delete_nonexistent_user(self, admin_client):
        resp = admin_client.delete("/api/admin/users/9999")
        assert resp.status_code == 404

    def test_delete_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.delete(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 403
