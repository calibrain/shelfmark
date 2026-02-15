"""Tests for self-service notification test endpoint."""

import os
import tempfile
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


class TestSelfNotificationPreferencesTestAction:
    @pytest.fixture(autouse=True)
    def setup_config(self, tmp_path, monkeypatch):
        import json
        from pathlib import Path

        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", Path(config_dir))

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        notifications_config = {
            "USER_NOTIFICATION_ROUTES": [
                {"event": "all", "url": "ntfys://ntfy.sh/default-user"},
            ],
        }
        (plugins_dir / "notifications.json").write_text(json.dumps(notifications_config))

        from shelfmark.core.config import config as app_config
        app_config.refresh()

    def test_users_me_notification_test_uses_payload_routes(self, app, user_db):
        user = user_db.create_user(username="alice")
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["user_id"] = user["username"]
            sess["db_user_id"] = user["id"]
            sess["is_admin"] = False

        with patch(
            "shelfmark.config.notifications_settings.send_test_notification",
            return_value={"success": True, "message": "ok"},
        ) as mock_send:
            resp = client.post(
                "/api/users/me/notification-preferences/test",
                json={
                    "USER_NOTIFICATION_ROUTES": [
                        {"event": "all", "url": " ntfys://ntfy.sh/alice "},
                        {"event": "download_failed", "url": "ntfys://ntfy.sh/alice-errors"},
                    ]
                },
            )

        assert resp.status_code == 200
        assert resp.json["success"] is True
        mock_send.assert_called_once_with(
            ["ntfys://ntfy.sh/alice", "ntfys://ntfy.sh/alice-errors"]
        )

    def test_users_me_notification_test_requires_user_context(self, app):
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = "alice"

        resp = client.post("/api/users/me/notification-preferences/test", json={})
        assert resp.status_code == 403
        assert resp.json["error"] == "Authenticated session is missing local user context"

    def test_users_me_notification_test_requires_at_least_one_url(self, app, user_db):
        user = user_db.create_user(username="alice")
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["user_id"] = user["username"]
            sess["db_user_id"] = user["id"]
            sess["is_admin"] = False

        resp = client.post(
            "/api/users/me/notification-preferences/test",
            json={"USER_NOTIFICATION_ROUTES": [{"event": "all", "url": ""}]},
        )

        assert resp.status_code == 400
        assert "Add at least one personal notification URL route first." in resp.json["message"]
