"""API-level tests for notifications settings and action routes."""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _create_user(main_module, *, prefix: str, role: str) -> dict:
    username = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return main_module.user_db.create_user(username=username, role=role)


def _set_session(client, *, user_id: str, db_user_id: int, is_admin: bool) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["db_user_id"] = db_user_id
        sess["is_admin"] = is_admin


class TestNotificationsSettingsApi:
    def test_notifications_action_requires_admin(self, main_module, client):
        user = _create_user(main_module, prefix="reader", role="user")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.post(
                "/api/settings/notifications/action/test_admin_notification",
                json={"ADMIN_NOTIFICATION_ROUTES": [{"event": "all", "url": "ntfys://ntfy.sh/demo"}]},
            )

        assert resp.status_code == 403
        assert resp.json["error"] == "Admin access required"

    def test_notifications_action_returns_400_when_no_routes(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch("shelfmark.config.notifications_settings.load_config_file", return_value={}):
                resp = client.post(
                    "/api/settings/notifications/action/test_admin_notification",
                    json={"ADMIN_NOTIFICATION_ROUTES": []},
                )

        assert resp.status_code == 400
        assert resp.json["success"] is False
        assert "Add at least one global notification URL route" in resp.json["message"]

    def test_notifications_action_uses_unsaved_values(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)

        captured: dict[str, object] = {}

        def _fake_send_test_notification(urls):
            captured["urls"] = urls
            return {"success": True, "message": "test sent"}

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch("shelfmark.config.notifications_settings.load_config_file", return_value={}):
                with patch(
                    "shelfmark.config.notifications_settings.send_test_notification",
                    side_effect=_fake_send_test_notification,
                ):
                    resp = client.post(
                        "/api/settings/notifications/action/test_admin_notification",
                        json={
                            "ADMIN_NOTIFICATION_ROUTES": [
                                {"event": "all", "url": " ntfys://ntfy.sh/shelfmark "},
                                {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
                                {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
                            ],
                        },
                    )

        assert resp.status_code == 200
        assert resp.json["success"] is True
        assert captured["urls"] == ["ntfys://ntfy.sh/shelfmark", "ntfys://ntfy.sh/errors"]

    def test_notifications_put_and_get_round_trip_normalizes_values(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)

        payload = {
            "ADMIN_NOTIFICATION_ROUTES": [
                {"event": "all", "url": " ntfys://ntfy.sh/shelfmark "},
                {"event": "request_created", "url": ""},
                {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
                {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
            ],
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            put_resp = client.put("/api/settings/notifications", json=payload)
            get_resp = client.get("/api/settings/notifications")

        assert put_resp.status_code == 200
        assert put_resp.json["success"] is True
        assert get_resp.status_code == 200

        fields = {field["key"]: field for field in get_resp.json["fields"] if "key" in field}
        assert fields["ADMIN_NOTIFICATION_ROUTES"]["value"] == [
            {"event": ["all"], "url": "ntfys://ntfy.sh/shelfmark"},
            {"event": ["request_created"], "url": ""},
            {"event": ["download_failed"], "url": "ntfys://ntfy.sh/errors"},
        ]
