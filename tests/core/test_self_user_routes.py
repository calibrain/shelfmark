"""Tests for self-service account edit context and update endpoints."""

import os
import tempfile
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.self_user_routes import register_self_user_routes
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
    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True

    register_self_user_routes(test_app, user_db)
    return test_app


def _authed_client_for_user(app: Flask, user: dict) -> Any:
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["db_user_id"] = user["id"]
        sess["is_admin"] = False
    return client


def _visible_sections_config_get(
    visible_sections: object,
):
    def _get(key: str, default: object = None, user_id: int | None = None) -> object:
        del user_id
        if key == "VISIBLE_SELF_SETTINGS_SECTIONS":
            return visible_sections
        return default

    return _get


def test_users_me_edit_context_respects_visible_sections(app, user_db, monkeypatch):
    user = user_db.create_user(username="alice")
    user_db.set_user_settings(user["id"], {"DESTINATION": "/books/alice"})
    client = _authed_client_for_user(app, user)
    monkeypatch.delenv("INGEST_DIR", raising=False)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.get("/api/users/me/edit-context")

    assert resp.status_code == 200
    assert resp.json["visibleUserSettingsSections"] == ["delivery"]
    assert resp.json["deliveryPreferences"]["tab"] == "downloads"
    assert resp.json["deliveryPreferences"]["effective"]["DESTINATION"]["value"] == "/books/alice"
    assert resp.json["deliveryPreferences"]["effective"]["DESTINATION"]["source"] == "user_override"
    assert "DESTINATION" in resp.json["userOverridableKeys"]
    assert resp.json["notificationPreferences"] is None


def test_users_me_edit_context_includes_search_preferences_when_visible(app, user_db):
    user = user_db.create_user(username="alice")
    user_db.set_user_settings(
        user["id"],
        {
            "SEARCH_MODE": "universal",
            "METADATA_PROVIDER": "openlibrary",
        },
    )
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery", "search"]),
        ):
            resp = client.get("/api/users/me/edit-context")

    assert resp.status_code == 200
    assert resp.json["visibleUserSettingsSections"] == ["delivery", "search"]
    assert resp.json["deliveryPreferences"]["tab"] == "downloads"
    assert resp.json["searchPreferences"]["tab"] == "search_mode"
    assert resp.json["searchPreferences"]["effective"]["SEARCH_MODE"]["value"] == "universal"
    assert resp.json["searchPreferences"]["effective"]["SEARCH_MODE"]["source"] == "user_override"
    assert "SEARCH_MODE" in resp.json["userOverridableKeys"]
    assert "METADATA_PROVIDER" in resp.json["userOverridableKeys"]
    assert resp.json["notificationPreferences"] is None
    assert resp.json["userOverridableKeys"] == sorted(resp.json["userOverridableKeys"])


def test_users_me_edit_context_falls_back_to_default_sections_for_invalid_config(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get("bogus"),
        ):
            resp = client.get("/api/users/me/edit-context")

    assert resp.status_code == 200
    assert resp.json["visibleUserSettingsSections"] == ["delivery", "search", "notifications"]
    assert resp.json["deliveryPreferences"] is not None
    assert resp.json["searchPreferences"] is not None
    assert resp.json["notificationPreferences"] is not None


def test_users_me_update_rejects_hidden_section_settings(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.put(
                "/api/users/me",
                json={
                    "settings": {
                        "USER_NOTIFICATION_ROUTES": [
                            {"event": "all", "url": "ntfys://ntfy.sh/alice"}
                        ],
                    }
                },
            )

    assert resp.status_code == 400
    assert resp.json["error"] == "Some settings are admin-only"
    assert "Setting not user-overridable: USER_NOTIFICATION_ROUTES" in resp.json["details"]


def test_users_me_update_accepts_visible_section_settings(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.put(
                "/api/users/me",
                json={"settings": {"DESTINATION": "/books/alice"}},
            )

    assert resp.status_code == 200
    assert user_db.get_user_settings(user["id"]).get("DESTINATION") == "/books/alice"
    assert resp.json["settings"]["DESTINATION"] == "/books/alice"


def test_users_me_update_rejects_non_object_settings_payload(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.app_config.get",
            side_effect=_visible_sections_config_get(["delivery"]),
        ):
            resp = client.put("/api/users/me", json={"settings": ["DESTINATION"]})

    assert resp.status_code == 400
    assert resp.json["error"] == "Settings must be an object"


def test_users_me_update_rejects_oidc_email_change(app, user_db):
    user = user_db.create_user(
        username="oidc-user",
        oidc_subject="oidc-sub-123",
        auth_source="oidc",
    )
    client = _authed_client_for_user(app, user)

    with patch("shelfmark.core.self_user_routes.load_active_auth_mode", return_value="builtin"):
        resp = client.put("/api/users/me", json={"email": "new@example.com"})

    assert resp.status_code == 400
    assert resp.json["error"] == "Cannot change email for OIDC users"
    assert user_db.get_user(user_id=user["id"])["email"] is None
