"""Tests for the explicit self-settings contract."""

import os
import tempfile
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.self_user_routes import register_self_user_routes
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
    register_self_user_routes(test_app, user_db)
    return test_app


def _client(app: Flask, user: dict[str, Any]):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = user["username"]
        session["db_user_id"] = user["id"]
    return client


def test_self_settings_exposes_only_identity_and_personal_preferences(app, user_db):
    user = user_db.create_user(username="alice", email="alice@example.com", display_name="Alice")
    user_db.update_personal_preferences(
        user["id"],
        kindle_address="alice@kindle.com",
        notifications_enabled=True,
        notification_transport="email",
        notification_destination="notify@example.com",
    )
    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        response = _client(app, user).get("/api/users/me")
    assert response.status_code == 200
    assert response.json == {
        "username": "alice",
        "email": "alice@example.com",
        "display_name": "Alice",
        "kindle_address": "alice@kindle.com",
        "notifications_enabled": True,
        "notification_transport": "email",
        "notification_destination": "notify@example.com",
    }


def test_self_settings_updates_email_and_personal_values_but_rejects_account_access_fields(
    app, user_db
):
    user = user_db.create_user(username="alice", email="alice@example.com")
    client = _client(app, user)
    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        response = client.put(
            "/api/users/me",
            json={
                "display_name": "Alice",
                "email": "new@example.com",
                "kindle_address": "alice@kindle.com",
                "notifications_enabled": True,
                "notification_transport": "apprise",
                "notification_destination": "jsons://example.test/token",
            },
        )
        rejected = client.put("/api/users/me", json={"password": "secret"})
    assert response.status_code == 200
    assert response.json["display_name"] == "Alice"
    assert user_db.get_personal_preferences(user["id"])["kindle_address"] == "alice@kindle.com"
    assert rejected.status_code == 400
    assert "password" in rejected.json["error"]
    assert user_db.get_user(user_id=user["id"])["email"] == "new@example.com"


def test_self_settings_validates_email_and_requires_it_for_email_notifications(app, user_db):
    user = user_db.create_user(username="alice", email="alice@example.com")
    client = _client(app, user)
    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        invalid = client.put("/api/users/me", json={"email": "not an email"})
        rejected = client.put("/api/users/me", json={"email": None, "notifications_enabled": True})
    assert invalid.status_code == 400
    assert invalid.json["error"] == "email must be a valid email address or null"
    assert rejected.status_code == 400
    assert user_db.get_user(user_id=user["id"])["email"] == "alice@example.com"


def test_self_settings_validates_notification_shape(app, user_db):
    user = user_db.create_user(username="alice")
    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        response = _client(app, user).put("/api/users/me", json={"notification_transport": "sms"})
    assert response.status_code == 400
    assert response.json["error"] == "notification_transport must be 'apprise' or null"


def test_enabled_notifications_require_valid_destination_and_transport_switch_clears_old_value(
    app, user_db
):
    user = user_db.create_user(username="alice")
    client = _client(app, user)
    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        rejected = client.put(
            "/api/users/me",
            json={
                "notifications_enabled": True,
                "notification_transport": "email",
            },
        )
        saved = client.put(
            "/api/users/me",
            json={
                "notification_transport": "apprise",
                "notification_destination": "ntfys://ntfy.sh/alice",
            },
        )
        switched = client.put("/api/users/me", json={"notification_transport": None})
    assert rejected.status_code == 400
    assert saved.status_code == 200
    assert switched.json["notification_destination"] is None


def test_enabled_preferences_validate_partial_updates_against_saved_destination(app, user_db):
    user = user_db.create_user(username="alice")
    user_db.update_personal_preferences(
        user["id"],
        notifications_enabled=True,
        notification_transport="email",
        notification_destination="alice@example.com",
    )
    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        response = _client(app, user).put(
            "/api/users/me", json={"notification_destination": "not-an-email"}
        )
    assert response.status_code == 400


def test_active_destination_test_endpoint_is_authenticated_and_uses_saved_preferences(app, user_db):
    user = user_db.create_user(username="alice")
    user_db.update_personal_preferences(
        user["id"],
        notifications_enabled=True,
        notification_transport="email",
        notification_destination="alice@example.com",
    )
    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.send_personal_test_notification",
            return_value={"success": True, "message": "sent"},
        ) as send:
            response = _client(app, user).post("/api/users/me/notifications/test")
    assert response.status_code == 200
    assert response.json == {"success": True, "message": "sent"}
    send.assert_called_once_with(user_db, user["id"])
