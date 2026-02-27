"""API tests for activity snapshot/dismiss/history routes."""

from __future__ import annotations

import importlib
import uuid
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

        main_module.activity_service.record_terminal_snapshot(
            user_id=user["id"],
            item_type="download",
            item_key="download:test-task",
            origin="requested",
            final_status="complete",
            source_id="test-task",
            snapshot={"title": "Dismiss Me"},
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
        assert history_response.json[0]["snapshot"] == {"title": "Dismiss Me"}

        assert clear_history_response.status_code == 200
        assert clear_history_response.json["status"] == "cleared"
        assert clear_history_response.json["deleted_count"] == 1

        assert history_after_clear.status_code == 200
        assert history_after_clear.json == []

    def test_admin_snapshot_includes_admin_viewer_dismissals(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)

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
            delivery_state="unknown",
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
            response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "download", "item_key": "download:test-task"},
            )

        assert response.status_code == 403
        assert response.json["code"] == "user_identity_unavailable"

    def test_dismiss_emits_activity_update_to_user_room(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    response = client.post(
                        "/api/activity/dismiss",
                        json={"item_type": "download", "item_key": "download:test-task"},
                    )

        assert response.status_code == 200
        mock_emit.assert_called_once_with(
            "activity_update",
            ANY,
            to=f"user_{user['id']}",
        )

    def test_no_auth_dismiss_many_and_history_use_shared_identity(self, main_module):
        item_key = f"download:no-auth-{uuid.uuid4().hex[:10]}"

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

    def test_queue_clear_does_not_set_request_delivery_state_to_cleared(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        request_row = main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Queue Clear Book",
                "author": "Queue Clear Author",
                "provider": "openlibrary",
                "provider_id": "clear-1",
            },
            release_data={
                "source": "prowlarr",
                "source_id": "clear-task-1",
                "title": "Queue Clear Book.epub",
            },
            status="fulfilled",
            delivery_state="complete",
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "clear_completed", return_value=1):
                response = client.delete("/api/queue/clear")

        assert response.status_code == 200
        updated_request = main_module.user_db.get_request(request_row["id"])
        assert updated_request is not None
        assert updated_request["delivery_state"] == "complete"

    def test_snapshot_backfills_undismissed_terminal_download_from_activity_log(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        main_module.activity_service.record_terminal_snapshot(
            user_id=user["id"],
            item_type="download",
            item_key="download:expired-task-1",
            origin="direct",
            final_status="complete",
            source_id="expired-task-1",
            snapshot={
                "kind": "download",
                "download": {
                    "id": "expired-task-1",
                    "title": "Expired Task",
                    "author": "Expired Author",
                    "added_time": 123,
                    "status_message": "Finished",
                    "source": "direct_download",
                },
            },
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

        main_module.activity_service.record_terminal_snapshot(
            user_id=request_owner["id"],
            item_type="download",
            item_key="download:cross-user-expired-task",
            origin="requested",
            final_status="complete",
            source_id="cross-user-expired-task",
            snapshot={
                "kind": "download",
                "download": {
                    "id": "cross-user-expired-task",
                    "title": "Cross User Task",
                    "author": "Another User",
                    "added_time": 123,
                    "status_message": "Finished",
                    "source": "direct_download",
                    "user_id": request_owner["id"],
                },
            },
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                response = client.get("/api/activity/snapshot")

        assert response.status_code == 200
        assert "cross-user-expired-task" in response.json["status"]["complete"]
        assert response.json["status"]["complete"]["cross-user-expired-task"]["id"] == "cross-user-expired-task"

    def test_snapshot_clears_stale_download_dismissal_when_same_task_is_active(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "download", "item_key": "download:task-reused-1"},
            )
            assert dismiss_response.status_code == 200

            active_status = _sample_status_payload()
            active_status["downloading"] = {
                "task-reused-1": {
                    "id": "task-reused-1",
                    "title": "Reused Task",
                    "author": "Author",
                    "source": "direct_download",
                    "added_time": 1,
                }
            }

            with patch.object(main_module.backend, "queue_status", return_value=active_status):
                snapshot_response = client.get("/api/activity/snapshot")

        assert snapshot_response.status_code == 200
        assert {
            "item_type": "download",
            "item_key": "download:task-reused-1",
        } not in snapshot_response.json["dismissed"]
        assert main_module.activity_service.get_dismissal_set(user["id"]) == []

    def test_dismiss_state_is_isolated_per_user(self, main_module, client):
        user_one = _create_user(main_module, prefix="reader-one")
        user_two = _create_user(main_module, prefix="reader-two")

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

    def test_admin_request_dismissal_is_shared_across_admin_users(self, main_module, client):
        admin_one = _create_user(main_module, prefix="admin-one", role="admin")
        admin_two = _create_user(main_module, prefix="admin-two", role="admin")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            _set_session(client, user_id=admin_one["username"], db_user_id=admin_one["id"], is_admin=True)
            dismiss_response = client.post(
                "/api/activity/dismiss",
                json={"item_type": "request", "item_key": "request:999999"},
            )
            assert dismiss_response.status_code == 200

            _set_session(client, user_id=admin_two["username"], db_user_id=admin_two["id"], is_admin=True)
            with patch.object(main_module.backend, "queue_status", return_value=_sample_status_payload()):
                snapshot_response = client.get("/api/activity/snapshot")
            history_response = client.get("/api/activity/history?limit=50&offset=0")

        assert snapshot_response.status_code == 200
        assert {"item_type": "request", "item_key": "request:999999"} in snapshot_response.json["dismissed"]

        assert history_response.status_code == 200
        assert any(row["item_key"] == "request:999999" for row in history_response.json)

    def test_history_paging_is_stable_and_non_overlapping(self, main_module, client):
        user = _create_user(main_module, prefix="history-user")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        for index in range(5):
            item_key = f"download:history-task-{index}"
            main_module.activity_service.record_terminal_snapshot(
                user_id=user["id"],
                item_type="download",
                item_key=item_key,
                origin="direct",
                final_status="complete",
                source_id=f"history-task-{index}",
                snapshot={"kind": "download", "download": {"id": f"history-task-{index}"}},
            )
            main_module.activity_service.dismiss_item(
                user_id=user["id"],
                item_type="download",
                item_key=item_key,
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
        main_module.activity_service.dismiss_item(
            user_id=user["id"],
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
