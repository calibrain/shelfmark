"""API tests for activity snapshot/dismiss/history routes."""

from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace
from unittest.mock import ANY, patch

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


def _set_session(client, *, user_id: str, db_user_id: int | None, is_admin: bool) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["is_admin"] = is_admin
        if db_user_id is not None:
            sess["db_user_id"] = db_user_id
        elif "db_user_id" in sess:
            del sess["db_user_id"]


def _create_user(main_module, *, prefix: str, role: str = "user") -> dict:
    username = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return main_module.user_db.create_user(username=username, role=role)


def _record_terminal_download(
    main_module,
    *,
    task_id: str,
    user_id: int | None,
    username: str | None,
    title: str = "Recorded Download",
    author: str = "Recorded Author",
    source: str = "direct_download",
    source_display_name: str = "Direct Download",
    origin: str = "direct",
    final_status: str = "complete",
    request_id: int | None = None,
    status_message: str | None = None,
    download_path: str | None = None,
) -> None:
    svc = main_module.download_history_service
    svc.record_download(
        task_id=task_id,
        user_id=user_id,
        username=username,
        request_id=request_id,
        source=source,
        source_display_name=source_display_name,
        title=title,
        author=author,
        format="epub",
        size="1 MB",
        preview=None,
        content_type="ebook",
        origin=origin,
    )
    svc.finalize_download(
        task_id=task_id,
        final_status=final_status,
        status_message=status_message,
        download_path=download_path,
    )


def _sample_status_payload() -> dict:
    return {
        "queued": {},
        "resolving": {},
        "locating": {},
        "downloading": {},
        "complete": {},
        "available": {},
        "done": {},
        "error": {},
        "cancelled": {},
    }


def _hidden_item_keys(main_module, *, viewer_scope: str) -> set[str]:
    return {
        row["item_key"]
        for row in main_module.activity_view_state_service.list_hidden(viewer_scope=viewer_scope)
    }


class TestActivityRoutes:
    def test_snapshot_returns_status_requests_and_dismissed(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data={
                "title": "Snapshot Book",
                "author": "Snapshot Author",
                "provider": "openlibrary",
                "provider_id": "snap-1",
            },
            status="pending",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                response = client.get("/api/activity/snapshot")

        assert response.status_code == 200
        assert "status" in response.json
        assert "requests" in response.json
        assert "dismissed" in response.json
        assert response.json["dismissed"] == []
        assert any(item["user_id"] == user["id"] for item in response.json["requests"])

    def test_dismiss_and_history_flow(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        _record_terminal_download(
            main_module,
            task_id="test-task",
            user_id=user["id"],
            username=user["username"],
            title="Dismiss Me",
            origin="requested",
            request_id=12,
            status_message="Complete",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "download", "item_key": "download:test-task"},
            )
            snapshot_response = client.get("/api/activity/snapshot")
            history_response = client.get("/api/activity/history?limit=10&offset=0")
            clear_history_response = client.delete("/api/activity/history")
            history_after_clear = client.get("/api/activity/history?limit=10&offset=0")

        assert dismiss_response.status_code == 200
        assert dismiss_response.json["status"] == "dismissed"

        assert snapshot_response.status_code == 200
        assert {"item_type": "download", "item_key": "download:test-task"} in snapshot_response.json["dismissed"]

        assert history_response.status_code == 200
        assert len(history_response.json) == 1
        assert history_response.json[0]["item_key"] == "download:test-task"
        assert history_response.json[0]["snapshot"]["kind"] == "download"
        assert history_response.json[0]["snapshot"]["download"]["title"] == "Dismiss Me"

        assert clear_history_response.status_code == 200
        assert clear_history_response.json["status"] == "cleared"
        assert clear_history_response.json["cleared_count"] == 1

        assert history_after_clear.status_code == 200
        assert history_after_clear.json == []
        assert main_module.download_history_service.get_by_task_id("test-task") is not None

    def test_dismiss_preserves_terminal_snapshot_without_live_queue_merge(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        task_id = "dismiss-preserve-task"
        _record_terminal_download(
            main_module,
            task_id=task_id,
            user_id=user["id"],
            username=user["username"],
            title="Recorded Title",
            author="Recorded Author",
            status_message="Complete",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "download", "item_key": f"download:{task_id}"},
            )
            history_response = client.get("/api/activity/history?limit=10&offset=0")

        assert dismiss_response.status_code == 200
        assert history_response.status_code == 200
        assert history_response.json[0]["item_key"] == f"download:{task_id}"
        snapshot_download = history_response.json[0]["snapshot"]["download"]
        assert snapshot_download["title"] == "Recorded Title"
        assert snapshot_download["author"] == "Recorded Author"
        assert snapshot_download["status_message"] is None

    def test_clear_history_hides_dismissed_requests_without_deleting_them(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        request_row = main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data={
                "title": "Dismissed Request",
                "author": "Request Author",
                "provider": "openlibrary",
                "provider_id": "dismissed-request",
            },
            status="rejected",
        )
        request_key = f"request:{request_row['id']}"

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "request", "item_key": request_key},
            )
            history_before_clear = client.get("/api/activity/history?limit=10&offset=0")
            clear_history_response = client.delete("/api/activity/history")
            history_after_clear = client.get("/api/activity/history?limit=10&offset=0")
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                snapshot_after_clear = client.get("/api/activity/snapshot")

        assert dismiss_response.status_code == 200
        assert history_before_clear.status_code == 200
        assert any(row["item_key"] == request_key for row in history_before_clear.json)

        assert clear_history_response.status_code == 200
        assert clear_history_response.json["status"] == "cleared"
        assert clear_history_response.json["cleared_count"] == 1

        assert history_after_clear.status_code == 200
        assert history_after_clear.json == []

        assert snapshot_after_clear.status_code == 200
        assert all(row["id"] != request_row["id"] for row in snapshot_after_clear.json["requests"])
        assert {"item_type": "request", "item_key": request_key} in snapshot_after_clear.json["dismissed"]
        assert main_module.user_db.get_request(request_row["id"]) is not None

    def test_admin_snapshot_includes_admin_viewer_dismissals(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)

        _record_terminal_download(
            main_module,
            task_id="admin-visible-task",
            user_id=admin["id"],
            username=admin["username"],
            title="Admin Visible",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "download", "item_key": "download:admin-visible-task"},
            )
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                snapshot_response = client.get("/api/activity/snapshot")

        assert dismiss_response.status_code == 200
        assert snapshot_response.status_code == 200
        assert {
            "item_type": "download",
            "item_key": "download:admin-visible-task",
        } in snapshot_response.json["dismissed"]

    def test_localdownload_falls_back_to_download_history_file(self, main_module, client, tmp_path):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        task_id = "history-localdownload-task"
        file_path = tmp_path / "history-fallback.epub"
        file_bytes = b"history download payload"
        file_path.write_bytes(file_bytes)

        _record_terminal_download(
            main_module,
            task_id=task_id,
            user_id=user["id"],
            username=user["username"],
            title="History Local Download",
            download_path=str(file_path),
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            response = client.get(f"/api/localdownload?id={task_id}")

        assert response.status_code == 200
        assert response.data == file_bytes
        assert "attachment" in response.headers.get("Content-Disposition", "").lower()

    def test_dismiss_legacy_fulfilled_request_creates_minimal_history_snapshot(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        request_row = main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data={
                "title": "Legacy Fulfilled Request",
                "author": "Legacy Author",
                "provider": "openlibrary",
                "provider_id": "legacy-fulfilled-1",
            },
            status="fulfilled",
            delivery_state="none",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "request", "item_key": f"request:{request_row['id']}"},
            )
            history_response = client.get("/api/activity/history?limit=10&offset=0")

        assert dismiss_response.status_code == 200
        assert history_response.status_code == 200
        assert len(history_response.json) == 1

        history_entry = history_response.json[0]
        assert history_entry["item_type"] == "request"
        assert history_entry["item_key"] == f"request:{request_row['id']}"
        assert history_entry["final_status"] == "complete"
        assert history_entry["snapshot"]["kind"] == "request"
        assert history_entry["snapshot"]["request"]["id"] == request_row["id"]
        assert history_entry["snapshot"]["request"]["book_data"]["title"] == "Legacy Fulfilled Request"

    def test_dismiss_requires_db_identity(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=None, is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch("shelfmark.core.activity_routes.logger.warning") as mock_warning:
                response = client.post(
                    "/api/activity/dismiss",
                    json={"item_type": "download", "item_key": "download:test-task"},
                )

        assert response.status_code == 403
        assert response.json["code"] == "user_identity_unavailable"
        mock_warning.assert_called_once()
        log_message = mock_warning.call_args.args[0]
        assert "Activity dismiss rejected" in log_message
        assert "status=403" in log_message
        assert "reason=User identity unavailable for activity workflow" in log_message
        assert "path=/api/activity/dismiss" in log_message
        assert f"user={user['username']}" in log_message
        assert "db_user_id=-" in log_message

    def test_dismiss_returns_404_when_download_history_row_is_missing(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "download", "item_key": "download:missing-task"},
            )

        assert response.status_code == 404
        assert response.json["error"] == "Download not found"

    def test_dismiss_rejects_active_download(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        main_module.download_history_service.record_download(
            task_id="active-dismiss-task",
            user_id=user["id"],
            username=user["username"],
            request_id=None,
            source="direct_download",
            source_display_name="Direct Download",
            title="Active Dismiss Task",
            author="Author",
            format="epub",
            size="1 MB",
            preview=None,
            content_type="ebook",
            origin="direct",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "download", "item_key": "download:active-dismiss-task"},
            )

        assert response.status_code == 409
        assert response.json["error"] == "Only terminal downloads can be dismissed"

    def test_dismiss_rejects_pending_request(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        request_row = main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data={
                "title": "Pending Request",
                "author": "Pending Author",
                "provider": "openlibrary",
                "provider_id": "pending-dismiss-1",
            },
            status="pending",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "request", "item_key": f"request:{request_row['id']}"},
            )

        assert response.status_code == 409
        assert response.json["error"] == "Only terminal requests can be dismissed"

    def test_dismiss_emits_activity_update_to_user_room(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        _record_terminal_download(
            main_module,
            task_id="emit-task",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    response = client.post(
                        "/api/activity/dismiss",
                        json={"item_type": "download", "item_key": "download:emit-task"},
                    )

        assert response.status_code == 200
        mock_emit.assert_called_once_with(
            "activity_update",
            ANY,
            to=f"user_{user['id']}",
        )

    def test_admin_dismiss_emits_activity_update_to_admin_room(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        owner = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
        _record_terminal_download(
            main_module,
            task_id="admin-dismiss-room-task",
            user_id=owner["id"],
            username=owner["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    response = client.post(
                        "/api/activity/dismiss",
                        json={"item_type": "download", "item_key": "download:admin-dismiss-room-task"},
                    )

        assert response.status_code == 200
        mock_emit.assert_called_once_with(
            "activity_update",
            ANY,
            to="admins",
        )

    def test_dismiss_many_preserves_terminal_snapshots_without_live_queue_merge(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        first_task_id = "dismiss-many-preserve-1"
        second_task_id = "dismiss-many-preserve-2"
        _record_terminal_download(
            main_module,
            task_id=first_task_id,
            user_id=user["id"],
            username=user["username"],
            title="First Title",
            author="First Author",
        )
        _record_terminal_download(
            main_module,
            task_id=second_task_id,
            user_id=user["id"],
            username=user["username"],
            title="Second Title",
            author="Second Author",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            dismiss_many_response = client.post(
                "/api/activity/dismiss-many",
                json={
                    "items": [
                        {"item_type": "download", "item_key": f"download:{first_task_id}"},
                        {"item_type": "download", "item_key": f"download:{second_task_id}"},
                    ]
                },
            )
            history_response = client.get("/api/activity/history?limit=20&offset=0")

        assert dismiss_many_response.status_code == 200
        assert dismiss_many_response.json["count"] == 2
        assert history_response.status_code == 200

        rows_by_key = {row["item_key"]: row for row in history_response.json}
        assert rows_by_key[f"download:{first_task_id}"]["snapshot"]["download"]["title"] == "First Title"
        assert rows_by_key[f"download:{first_task_id}"]["snapshot"]["download"]["author"] == "First Author"
        assert rows_by_key[f"download:{second_task_id}"]["snapshot"]["download"]["title"] == "Second Title"
        assert rows_by_key[f"download:{second_task_id}"]["snapshot"]["download"]["author"] == "Second Author"

    def test_dismiss_many_returns_404_without_partial_dismiss_when_any_item_is_missing(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        existing_task_id = "dismiss-many-existing"
        _record_terminal_download(
            main_module,
            task_id=existing_task_id,
            user_id=user["id"],
            username=user["username"],
            title="Existing Title",
            author="Existing Author",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch("shelfmark.core.activity_routes.logger.warning") as mock_warning:
                response = client.post(
                    "/api/activity/dismiss-many",
                    json={
                        "items": [
                            {"item_type": "download", "item_key": f"download:{existing_task_id}"},
                            {"item_type": "download", "item_key": "download:missing-bulk-task"},
                        ]
                    },
                )

        assert response.status_code == 404
        assert response.json["error"] == "One or more activity items were not found"
        assert response.json["missing_item_keys"] == ["download:missing-bulk-task"]
        assert f"download:{existing_task_id}" not in _hidden_item_keys(
            main_module,
            viewer_scope=f"user:{user['id']}",
        )
        mock_warning.assert_called_once()
        log_message = mock_warning.call_args.args[0]
        assert "Activity dismiss_many rejected" in log_message
        assert "status=404" in log_message
        assert "reason=One or more activity items were not found" in log_message
        assert "path=/api/activity/dismiss-many" in log_message
        assert "item_count=2" in log_message
        assert "missing_item_keys=download:missing-bulk-task" in log_message

    def test_no_auth_dismiss_many_and_history_use_shared_identity(self, main_module):
        task_id = f"no-auth-{uuid.uuid4().hex[:10]}"
        item_key = f"download:{task_id}"
        _record_terminal_download(
            main_module,
            task_id=task_id,
            user_id=None,
            username=None,
            title="No Auth",
        )

        client_one = main_module.app.test_client()
        client_two = main_module.app.test_client()

        with patch.object(main_module, "get_auth_mode", return_value="none"):
            dismiss_many_response = client_one.post(
                "/api/activity/dismiss-many",
                json={"items": [{"item_type": "download", "item_key": item_key}]},
            )
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                snapshot_one = client_one.get("/api/activity/snapshot")
                snapshot_two = client_two.get("/api/activity/snapshot")
            history_one = client_one.get("/api/activity/history?limit=10&offset=0")

        assert dismiss_many_response.status_code == 200
        assert dismiss_many_response.json["status"] == "dismissed"
        assert dismiss_many_response.json["count"] == 1

        assert snapshot_one.status_code == 200
        assert {"item_type": "download", "item_key": item_key} in snapshot_one.json["dismissed"]

        assert snapshot_two.status_code == 200
        assert {"item_type": "download", "item_key": item_key} in snapshot_two.json["dismissed"]

        assert history_one.status_code == 200
        assert any(row["item_key"] == item_key for row in history_one.json)

    def test_no_auth_dismiss_many_ignores_stale_session_db_identity(self, main_module, client):
        stale_db_user_id = 999999999
        _set_session(client, user_id="stale-session-user", db_user_id=stale_db_user_id, is_admin=False)

        task_id = f"no-auth-stale-{uuid.uuid4().hex[:8]}"
        item_key = f"download:{task_id}"
        _record_terminal_download(
            main_module,
            task_id=task_id,
            user_id=None,
            username=None,
            title="No Auth Stale",
        )

        with patch.object(main_module, "get_auth_mode", return_value="none"):
            response = client.post(
                "/api/activity/dismiss-many",
                json={"items": [{"item_type": "download", "item_key": item_key}]},
            )

        assert response.status_code == 200
        assert response.json["status"] == "dismissed"

        dismissals = _hidden_item_keys(main_module, viewer_scope="noauth:shared")
        assert item_key in dismissals

    def test_no_auth_dismiss_many_uses_shared_identity_even_with_valid_session_db_user(
        self,
        main_module,
        client,
    ):
        existing_user = _create_user(main_module, prefix="legacy-reader")
        _set_session(
            client,
            user_id=existing_user["username"],
            db_user_id=existing_user["id"],
            is_admin=False,
        )

        task_id = f"no-auth-valid-{uuid.uuid4().hex[:8]}"
        item_key = f"download:{task_id}"
        _record_terminal_download(
            main_module,
            task_id=task_id,
            user_id=None,
            username=None,
            title="No Auth Valid",
        )
        other_client = main_module.app.test_client()

        with patch.object(main_module, "get_auth_mode", return_value="none"):
            dismiss_response = client.post(
                "/api/activity/dismiss-many",
                json={"items": [{"item_type": "download", "item_key": item_key}]},
            )
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                snapshot_response = other_client.get("/api/activity/snapshot")

        assert dismiss_response.status_code == 200
        assert snapshot_response.status_code == 200
        assert {"item_type": "download", "item_key": item_key} in snapshot_response.json["dismissed"]

    def test_dismiss_many_with_stale_db_identity_returns_identity_unavailable(self, main_module, client):
        _set_session(client, user_id="stale-session-user", db_user_id=999999999, is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            response = client.post(
                "/api/activity/dismiss-many",
                json={"items": [{"item_type": "download", "item_key": "download:test-stale"}]},
            )

        assert response.status_code == 403
        assert response.json["code"] == "user_identity_unavailable"

    def test_clear_history_logs_identity_failure(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=None, is_admin=True)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch("shelfmark.core.activity_routes.logger.warning") as mock_warning:
                response = client.delete("/api/activity/history")

        assert response.status_code == 403
        assert response.json["code"] == "user_identity_unavailable"
        mock_warning.assert_called_once()
        log_message = mock_warning.call_args.args[0]
        assert "Activity history_clear rejected" in log_message
        assert "status=403" in log_message
        assert "reason=User identity unavailable for activity workflow" in log_message
        assert "path=/api/activity/history" in log_message
        assert f"user={admin['username']}" in log_message
        assert "is_admin=True" in log_message

    def test_dismiss_many_logs_actor_and_row_context_for_forbidden_download(self, main_module, client):
        owner = _create_user(main_module, prefix="owner")
        intruder = _create_user(main_module, prefix="intruder")
        _set_session(client, user_id=intruder["username"], db_user_id=intruder["id"], is_admin=False)

        _record_terminal_download(
            main_module,
            task_id="forbidden-download-task",
            user_id=owner["id"],
            username=owner["username"],
            request_id=321,
            final_status="complete",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch("shelfmark.core.activity_routes.logger.warning") as mock_warning:
                response = client.post(
                    "/api/activity/dismiss-many",
                    json={"items": [{"item_type": "download", "item_key": "download:forbidden-download-task"}]},
                )

        assert response.status_code == 403
        assert response.json["error"] == "Forbidden"
        mock_warning.assert_called_once()
        log_message = mock_warning.call_args.args[0]
        assert "Activity dismiss_many rejected" in log_message
        assert "status=403" in log_message
        assert "reason=Forbidden" in log_message
        assert "auth_mode=builtin" in log_message
        assert f"viewer_scope=user:{intruder['id']}" in log_message
        assert f"owner_user_id={owner['id']}" in log_message
        assert "final_status=complete" in log_message
        assert "request_id=321" in log_message

    def test_snapshot_backfills_undismissed_terminal_download_from_download_history(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        _record_terminal_download(
            main_module,
            task_id="expired-task-1",
            user_id=user["id"],
            username=user["username"],
            title="Expired Task",
            author="Expired Author",
            status_message="Finished",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                response = client.get("/api/activity/snapshot")

        assert response.status_code == 200
        assert "expired-task-1" in response.json["status"]["complete"]
        assert response.json["status"]["complete"]["expired-task-1"]["id"] == "expired-task-1"

    def test_admin_snapshot_backfills_terminal_downloads_across_users(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        request_owner = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)

        _record_terminal_download(
            main_module,
            task_id="cross-user-expired-task",
            user_id=request_owner["id"],
            username=request_owner["username"],
            title="Cross User Task",
            author="Another User",
            origin="requested",
            request_id=123,
            status_message="Finished",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                response = client.get("/api/activity/snapshot")

        assert response.status_code == 200
        assert "cross-user-expired-task" in response.json["status"]["complete"]
        assert response.json["status"]["complete"]["cross-user-expired-task"]["id"] == "cross-user-expired-task"

    def test_snapshot_shows_stale_active_download_as_interrupted_error(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        # Record a download at queue time (active status) but don't put it in the queue
        main_module.download_history_service.record_download(
            task_id="stale-active-task",
            user_id=user["id"],
            username=user["username"],
            request_id=None,
            source="direct_download",
            source_display_name="Direct Download",
            title="Stale Active Task",
            author="Stale Author",
            format="epub",
            size="1 MB",
            preview=None,
            content_type="ebook",
            origin="direct",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                response = client.get("/api/activity/snapshot")

        assert response.status_code == 200
        assert "stale-active-task" in response.json["status"]["error"]
        assert response.json["status"]["error"]["stale-active-task"]["status_message"] == "Interrupted"

    def test_snapshot_active_download_with_queue_entry_shows_in_correct_bucket(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        # Record a download at queue time
        main_module.download_history_service.record_download(
            task_id="active-downloading-task",
            user_id=user["id"],
            username=user["username"],
            request_id=None,
            source="direct_download",
            source_display_name="Direct Download",
            title="Active Downloading Task",
            author="Active Author",
            format="epub",
            size="2 MB",
            preview=None,
            content_type="ebook",
            origin="direct",
        )

        # Simulate it being active in the queue
        active_status = _sample_status_payload()
        active_status["downloading"] = {
            "active-downloading-task": {
                "id": "active-downloading-task",
                "title": "Active Downloading Task",
                "author": "Active Author",
                "source": "direct_download",
                "progress": 0.5,
                "status_message": "Downloading 50%",
            }
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=active_status):
                response = client.get("/api/activity/snapshot")

        assert response.status_code == 200
        assert "active-downloading-task" in response.json["status"]["downloading"]
        assert response.json["status"]["downloading"]["active-downloading-task"]["progress"] == 0.5

    def test_snapshot_ignores_queue_only_active_download_without_history_row(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        active_status = _sample_status_payload()
        active_status["downloading"] = {
            "queue-only-task": {
                "id": "queue-only-task",
                "title": "Queue Only Task",
                "author": "Queue Author",
                "source": "direct_download",
                "progress": 0.5,
                "status_message": "Downloading 50%",
                "user_id": user["id"],
                "username": user["username"],
            }
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=active_status):
                response = client.get("/api/activity/snapshot")

        assert response.status_code == 200
        assert "queue-only-task" not in response.json["status"]["downloading"]

    def test_queue_hook_clears_download_view_state_when_task_is_requeued(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        _record_terminal_download(
            main_module,
            task_id="task-reused-1",
            user_id=user["id"],
            username=user["username"],
            title="Reused Task",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "download", "item_key": "download:task-reused-1"},
            )
            assert dismiss_response.status_code == 200
        assert "download:task-reused-1" in _hidden_item_keys(
            main_module,
            viewer_scope=f"user:{user['id']}",
        )

        main_module._record_download_queued(
            "task-reused-1",
            SimpleNamespace(
                user_id=user["id"],
                username=user["username"],
                request_id=None,
                source="direct_download",
                title="Reused Task",
                author="Author",
                format="epub",
                size="1 MB",
                preview=None,
                content_type="ebook",
            ),
        )

        assert "download:task-reused-1" not in _hidden_item_keys(
            main_module,
            viewer_scope=f"user:{user['id']}",
        )

    def test_dismiss_state_is_isolated_per_user(self, main_module, client):
        user_one = _create_user(main_module, prefix="reader-one")
        user_two = _create_user(main_module, prefix="reader-two")

        _record_terminal_download(
            main_module,
            task_id="shared-task",
            user_id=user_one["id"],
            username=user_one["username"],
            title="Shared Task",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            _set_session(client, user_id=user_one["username"], db_user_id=user_one["id"], is_admin=False)
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "download", "item_key": "download:shared-task"},
            )
            assert dismiss_response.status_code == 200

            snapshot_one = client.get("/api/activity/snapshot")
            assert snapshot_one.status_code == 200
            assert {"item_type": "download", "item_key": "download:shared-task"} in snapshot_one.json["dismissed"]

            _set_session(client, user_id=user_two["username"], db_user_id=user_two["id"], is_admin=False)
            snapshot_two = client.get("/api/activity/snapshot")
            assert snapshot_two.status_code == 200
            assert {"item_type": "download", "item_key": "download:shared-task"} not in snapshot_two.json["dismissed"]

    def test_admin_dismiss_and_clear_do_not_affect_owner_view(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        owner = _create_user(main_module, prefix="reader")
        task_id = f"admin-owned-{uuid.uuid4().hex[:8]}"

        _record_terminal_download(
            main_module,
            task_id=task_id,
            user_id=owner["id"],
            username=owner["username"],
            title="Admin Owned Task",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "download", "item_key": f"download:{task_id}"},
            )
            assert dismiss_response.status_code == 200

            admin_history = client.get("/api/activity/history?limit=10&offset=0")
            assert admin_history.status_code == 200
            assert any(row["item_key"] == f"download:{task_id}" for row in admin_history.json)

            _set_session(client, user_id=owner["username"], db_user_id=owner["id"], is_admin=False)
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                owner_snapshot_after_admin_dismiss = client.get("/api/activity/snapshot")
            assert owner_snapshot_after_admin_dismiss.status_code == 200
            assert task_id in owner_snapshot_after_admin_dismiss.json["status"]["complete"]
            assert {
                "item_type": "download",
                "item_key": f"download:{task_id}",
            } not in owner_snapshot_after_admin_dismiss.json["dismissed"]

            _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
            clear_response = client.delete("/api/activity/history")
            assert clear_response.status_code == 200
            assert clear_response.json["cleared_count"] >= 1

            _set_session(client, user_id=owner["username"], db_user_id=owner["id"], is_admin=False)
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                owner_snapshot_after_admin_clear = client.get("/api/activity/snapshot")
            owner_history = client.get("/api/activity/history?limit=10&offset=0")

        assert owner_snapshot_after_admin_clear.status_code == 200
        assert task_id in owner_snapshot_after_admin_clear.json["status"]["complete"]
        assert {
            "item_type": "download",
            "item_key": f"download:{task_id}",
        } not in owner_snapshot_after_admin_clear.json["dismissed"]
        assert owner_history.status_code == 200
        assert owner_history.json == []

    def test_admin_request_dismissal_is_shared_across_admin_users(self, main_module, client):
        admin_one = _create_user(main_module, prefix="admin-one", role="admin")
        admin_two = _create_user(main_module, prefix="admin-two", role="admin")
        request_owner = _create_user(main_module, prefix="request-owner")
        request_row = main_module.user_db.create_request(
            user_id=request_owner["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data={
                "title": "Dismiss Me Request",
                "author": "Request Author",
                "provider": "openlibrary",
                "provider_id": "dismiss-request-1",
            },
            status="rejected",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            _set_session(client, user_id=admin_one["username"], db_user_id=admin_one["id"], is_admin=True)
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "request", "item_key": f"request:{request_row['id']}"},
            )
            assert dismiss_response.status_code == 200

            _set_session(client, user_id=admin_two["username"], db_user_id=admin_two["id"], is_admin=True)
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                snapshot_response = client.get("/api/activity/snapshot")
            history_response = client.get("/api/activity/history?limit=50&offset=0")

        assert snapshot_response.status_code == 200
        assert {"item_type": "request", "item_key": f"request:{request_row['id']}"} in snapshot_response.json["dismissed"]

        assert history_response.status_code == 200
        assert any(row["item_key"] == f"request:{request_row['id']}" for row in history_response.json)

    def test_admin_request_history_includes_requester_username(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        owner = _create_user(main_module, prefix="request-owner")
        request_row = main_module.user_db.create_request(
            user_id=owner["id"],
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data={
                "title": "History Username Request",
                "author": "History Username Author",
                "provider": "openlibrary",
                "provider_id": "history-username-request-1",
            },
            status="rejected",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "request", "item_key": f"request:{request_row['id']}"},
            )
            history_response = client.get("/api/activity/history?limit=10&offset=0")

        assert dismiss_response.status_code == 200
        assert history_response.status_code == 200
        matching_rows = [row for row in history_response.json if row["item_key"] == f"request:{request_row['id']}"]
        assert len(matching_rows) == 1
        assert matching_rows[0]["snapshot"]["request"]["username"] == owner["username"]

    def test_history_paging_is_stable_and_non_overlapping(self, main_module, client):
        user = _create_user(main_module, prefix="history-user")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        for index in range(5):
            task_id = f"history-task-{index}"
            _record_terminal_download(
                main_module,
                task_id=task_id,
                user_id=user["id"],
                username=user["username"],
                title=f"History Task {index}",
            )
            main_module.activity_view_state_service.dismiss(
                viewer_scope=f"user:{user['id']}",
                item_type="download",
                item_key=f"download:{task_id}",
            )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            page_one = client.get("/api/activity/history?limit=2&offset=0")
            page_two = client.get("/api/activity/history?limit=2&offset=2")
            page_three = client.get("/api/activity/history?limit=2&offset=4")
            full = client.get("/api/activity/history?limit=10&offset=0")

        assert page_one.status_code == 200
        assert page_two.status_code == 200
        assert page_three.status_code == 200
        assert full.status_code == 200

        page_one_ids = [row["id"] for row in page_one.json]
        page_two_ids = [row["id"] for row in page_two.json]
        page_three_ids = [row["id"] for row in page_three.json]
        combined_ids = page_one_ids + page_two_ids + page_three_ids
        full_ids = [row["id"] for row in full.json]

        assert len(set(page_one_ids).intersection(page_two_ids)) == 0
        assert len(set(page_one_ids).intersection(page_three_ids)) == 0
        assert len(set(page_two_ids).intersection(page_three_ids)) == 0
        assert combined_ids == full_ids[: len(combined_ids)]

    def test_dismiss_many_emits_activity_update_only_to_acting_user_room(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        _record_terminal_download(
            main_module,
            task_id="test-task-many",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    response = client.post(
                        "/api/activity/dismiss-many",
                        json={
                            "items": [
                                {"item_type": "download", "item_key": "download:test-task-many"},
                            ]
                        },
                    )

        assert response.status_code == 200
        mock_emit.assert_called_once_with(
            "activity_update",
            ANY,
            to=f"user_{user['id']}",
        )

    def test_clear_history_emits_activity_update_only_to_acting_user_room(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        _record_terminal_download(
            main_module,
            task_id="history-clear-task",
            user_id=user["id"],
            username=user["username"],
        )
        main_module.activity_view_state_service.dismiss(
            viewer_scope=f"user:{user['id']}",
            item_type="download",
            item_key="download:history-clear-task",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    response = client.delete("/api/activity/history")

        assert response.status_code == 200
        mock_emit.assert_called_once_with(
            "activity_update",
            ANY,
            to=f"user_{user['id']}",
        )

    def test_admin_clear_history_emits_activity_update_to_admin_room(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        owner = _create_user(main_module, prefix="reader")
        task_id = f"admin-history-clear-{uuid.uuid4().hex[:8]}"
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
        _record_terminal_download(
            main_module,
            task_id=task_id,
            user_id=owner["id"],
            username=owner["username"],
        )
        main_module.activity_view_state_service.dismiss(
            viewer_scope="admin:shared",
            item_type="download",
            item_key=f"download:{task_id}",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    response = client.delete("/api/activity/history")

        assert response.status_code == 200
        mock_emit.assert_called_once_with(
            "activity_update",
            ANY,
            to="admins",
        )
