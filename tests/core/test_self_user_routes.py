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


def test_users_me_edit_context_respects_visible_sections(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    def build_preferences(_user_db, _user_id, tab_name):
        if tab_name == "downloads":
            return {
                "tab": "downloads",
                "keys": ["DESTINATION"],
                "fields": [],
                "globalValues": {},
                "userOverrides": {},
                "effective": {},
            }
        raise AssertionError(f"Unexpected tab requested: {tab_name}")

    with patch("shelfmark.core.self_user_routes._get_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.load_config_file",
            side_effect=lambda tab_name: {"VISIBLE_SELF_SETTINGS_SECTIONS": ["delivery"]} if tab_name == "users" else {},
        ):
            with patch(
                "shelfmark.core.self_user_routes._build_user_preferences_payload",
                side_effect=build_preferences,
            ):
                resp = client.get("/api/users/me/edit-context")

    assert resp.status_code == 200
    assert resp.json["visibleUserSettingsSections"] == ["delivery"]
    assert resp.json["deliveryPreferences"]["tab"] == "downloads"
    assert resp.json["notificationPreferences"] is None
    assert resp.json["userOverridableKeys"] == ["DESTINATION"]


def test_users_me_edit_context_includes_search_preferences_when_visible(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    def build_preferences(_user_db, _user_id, tab_name):
        payloads = {
            "downloads": {
                "tab": "downloads",
                "keys": ["DESTINATION"],
                "fields": [],
                "globalValues": {},
                "userOverrides": {},
                "effective": {},
            },
            "search_mode": {
                "tab": "search_mode",
                "keys": ["SEARCH_MODE", "METADATA_PROVIDER"],
                "fields": [],
                "globalValues": {},
                "userOverrides": {},
                "effective": {},
            },
        }
        if tab_name not in payloads:
            raise AssertionError(f"Unexpected tab requested: {tab_name}")
        return payloads[tab_name]

    with patch("shelfmark.core.self_user_routes._get_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.load_config_file",
            side_effect=lambda tab_name: {
                "VISIBLE_SELF_SETTINGS_SECTIONS": ["delivery", "search"]
            } if tab_name == "users" else {},
        ):
            with patch(
                "shelfmark.core.self_user_routes._build_user_preferences_payload",
                side_effect=build_preferences,
            ):
                resp = client.get("/api/users/me/edit-context")

    assert resp.status_code == 200
    assert resp.json["visibleUserSettingsSections"] == ["delivery", "search"]
    assert resp.json["deliveryPreferences"]["tab"] == "downloads"
    assert resp.json["searchPreferences"]["tab"] == "search_mode"
    assert resp.json["notificationPreferences"] is None
    assert resp.json["userOverridableKeys"] == ["DESTINATION", "METADATA_PROVIDER", "SEARCH_MODE"]


def test_users_me_update_rejects_hidden_section_settings(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    def ordered_overrides(tab_name: str):
        if tab_name == "downloads":
            return [("DESTINATION", object())]
        raise AssertionError(f"Unexpected tab requested: {tab_name}")

    with patch("shelfmark.core.self_user_routes._get_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.load_config_file",
            side_effect=lambda tab_name: {"VISIBLE_SELF_SETTINGS_SECTIONS": ["delivery"]} if tab_name == "users" else {},
        ):
            with patch(
                "shelfmark.core.self_user_routes._get_ordered_user_overridable_fields",
                side_effect=ordered_overrides,
            ):
                resp = client.put(
                    "/api/users/me",
                    json={
                        "settings": {
                            "USER_NOTIFICATION_ROUTES": [{"event": "all", "url": "ntfys://ntfy.sh/alice"}],
                        }
                    },
                )

    assert resp.status_code == 400
    assert resp.json["error"] == "Some settings are admin-only"
    assert "Setting not user-overridable: USER_NOTIFICATION_ROUTES" in resp.json["details"]


def test_users_me_update_accepts_visible_section_settings(app, user_db):
    user = user_db.create_user(username="alice")
    client = _authed_client_for_user(app, user)

    def ordered_overrides(tab_name: str):
        if tab_name == "downloads":
            return [("DESTINATION", object())]
        raise AssertionError(f"Unexpected tab requested: {tab_name}")

    with patch("shelfmark.core.self_user_routes._get_auth_mode", return_value="builtin"):
        with patch(
            "shelfmark.core.self_user_routes.load_config_file",
            side_effect=lambda tab_name: {"VISIBLE_SELF_SETTINGS_SECTIONS": ["delivery"]} if tab_name == "users" else {},
        ):
            with patch(
                "shelfmark.core.self_user_routes._get_ordered_user_overridable_fields",
                side_effect=ordered_overrides,
            ):
                with patch(
                    "shelfmark.core.self_user_routes.validate_user_settings",
                    side_effect=lambda payload: (payload, []),
                ):
                    resp = client.put(
                        "/api/users/me",
                        json={"settings": {"DESTINATION": "/books/alice"}},
                    )

    assert resp.status_code == 200
    assert user_db.get_user_settings(user["id"]).get("DESTINATION") == "/books/alice"
