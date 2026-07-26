"""Tests for administrator-owned account access controls."""

import os
import tempfile
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.admin_routes import register_admin_routes
from shelfmark.core.user_db import UserDB


@pytest.fixture
def user_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "users.db"))
        db.initialize()
        yield db


@pytest.fixture
def app(user_db):
    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True
    register_admin_routes(test_app, user_db)
    return test_app


def _admin_client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "admin"
        session["is_admin"] = True
    return client


def test_admin_can_manage_username_password_role_and_library_capability(app, user_db):
    user = user_db.create_user(
        username="alice", password_hash="old", library_capability="request-only"
    )
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        response = _admin_client(app).put(
            f"/api/admin/users/{user['id']}",
            json={
                "username": "alice-reader",
                "password": "new-password",
                "role": "admin",
                "library_capability": "download-capable",
            },
        )
    assert response.status_code == 200
    assert response.json["username"] == "alice-reader"
    assert response.json["role"] == "admin"
    assert response.json["library_capability"] == "download-capable"
    assert "settings" not in response.json


def test_admin_cannot_edit_personal_preferences_through_user_api(app, user_db):
    user = user_db.create_user(username="alice")
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        response = _admin_client(app).put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"kindle_address": "alice@kindle.com"}},
        )
    assert response.status_code == 400
    assert user_db.get_personal_preferences(user["id"])["kindle_address"] is None


def test_removed_admin_override_endpoint_returns_not_found(app):
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        response = _admin_client(app).get("/api/admin/settings/overrides-summary")
    assert response.status_code == 404
