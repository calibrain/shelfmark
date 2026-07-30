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


def test_admin_can_manage_username_password_email_role_and_library_capability(app, user_db):
    user = user_db.create_user(
        username="alice", password_hash="old", library_capability="download-capable"
    )
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        response = _admin_client(app).put(
            f"/api/admin/users/{user['id']}",
            json={
                "username": "alice-reader",
                "password": "new-password",
                "email": "new@example.com",
                "role": "admin",
                "library_capability": "request-only",
            },
        )
    assert response.status_code == 200
    assert response.json["username"] == "alice-reader"
    assert response.json["role"] == "admin"
    assert response.json["email"] == "new@example.com"
    assert response.json["library_capability"] == "request-only"
    assert "settings" not in response.json


@pytest.mark.parametrize(
    ("payload", "expected_capability"),
    [
        ({"username": "default", "password": "password"}, "download-capable"),
        (
            {
                "username": "requester",
                "password": "password",
                "library_capability": "request-only",
            },
            "request-only",
        ),
    ],
)
def test_admin_create_user_persists_library_capability(app, user_db, payload, expected_capability):
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        response = _admin_client(app).post("/api/admin/users", json=payload)

    assert response.status_code == 201
    assert response.json["library_capability"] == expected_capability
    assert (
        user_db.get_user(user_id=response.json["id"])["library_capability"] == expected_capability
    )


@pytest.mark.parametrize("method", ["post", "put"])
def test_admin_rejects_invalid_library_capability(app, user_db, method):
    user = user_db.create_user(username="alice")
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        if method == "post":
            response = _admin_client(app).post(
                "/api/admin/users",
                json={
                    "username": "invalid",
                    "password": "password",
                    "library_capability": "invalid",
                },
            )
        else:
            response = _admin_client(app).put(
                f"/api/admin/users/{user['id']}",
                json={"library_capability": "invalid"},
            )

    assert response.status_code == 400
    assert response.json == {"error": "Invalid library_capability"}


def test_admin_can_edit_email_for_externally_authenticated_user(app, user_db):
    user = user_db.create_user(
        username="alice",
        email="old@example.com",
        identity_email="source@example.com",
        auth_source="oidc",
    )
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        response = _admin_client(app).put(
            f"/api/admin/users/{user['id']}", json={"email": "new@example.com"}
        )
    assert response.status_code == 200
    assert response.json["email"] == "new@example.com"
    assert user_db.get_user(user_id=user["id"])["identity_email"] == "source@example.com"


def test_admin_can_clear_email_with_an_empty_value(app, user_db):
    user = user_db.create_user(username="alice", email="old@example.com")
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        response = _admin_client(app).put(f"/api/admin/users/{user['id']}", json={"email": "  "})
    assert response.status_code == 200
    assert response.json["email"] is None


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
