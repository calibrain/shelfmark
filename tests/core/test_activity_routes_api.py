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
) -> None:
    main_module.download_history_service.record_terminal(
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
        final_status=final_status,
        status_message=status_message,
        download_path=None,
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
        assert clear_history_response.json["deleted_count"] == 1

        assert history_after_clear.status_code == 200
        assert history_after_clear.json == []

    def test_clear_history_deletes_dismissed_requests_from_snapshot(self, main_module, client):
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

        assert history_after_clear.status_code == 200
        assert history_after_clear.json == []

        assert snapshot_after_clear.status_code == 200
        assert all(row["id"] != request_row["id"] for row in snapshot_after_clear.json["requests"])
        assert {"item_type": "request", "item_key": request_key} not in snapshot_after_clear.json["dismissed"]

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
        )

        row = main_module.download_history_service.get_by_task_id(task_id)
        assert row is not None
        assert main_module.download_history_service is not None
        main_module.download_history_service.record_terminal(
            task_id=task_id,
            user_id=user["id"],
            username=user["username"],
            request_id=row.get("request_id"),
            source=row.get("source") or "direct_download",
            source_display_name=row.get("source_display_name"),
            title=row.get("title") or "History Local Download",
            author=row.get("author"),
            format=row.get("format"),
            size=row.get("size"),
            preview=row.get("preview"),
            content_type=row.get("content_type"),
            origin=row.get("origin") or "direct",
            final_status=row.get("final_status") or "complete",
            status_message=row.get("status_message"),
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

        dismissals = main_module.download_history_service.get_dismissed_keys(user_id=None)
        assert task_id in dismissals

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

    def test_snapshot_clears_stale_download_dismissal_when_same_task_is_active(self, main_module, client):
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
        assert "task-reused-1" not in main_module.download_history_service.get_dismissed_keys(user_id=user["id"])

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
            main_module.download_history_service.dismiss(task_id=task_id, user_id=user["id"])

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
        main_module.download_history_service.dismiss(task_id="history-clear-task", user_id=user["id"])

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
