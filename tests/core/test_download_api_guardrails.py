"""Baseline guardrail tests for download API endpoints.

These tests lock current behavior for `/api/download`, `/api/releases/download`,
and `/api/status` so policy work in later phases cannot accidentally change
existing contracts.
"""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import patch

import pytest

from shelfmark.core.models import DownloadTask


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


def _set_authenticated_session(
    client,
    *,
    user_id: str = "alice",
    db_user_id: int | None = 7,
    is_admin: bool = False,
) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["is_admin"] = is_admin
        if db_user_id is not None:
            sess["db_user_id"] = db_user_id


def _create_user(main_module, *, prefix: str, role: str = "user") -> dict:
    username = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return main_module.user_db.create_user(username=username, role=role)


class TestDownloadEndpointGuardrails:
    def test_missing_book_id_returns_400_and_does_not_queue(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_book") as mock_queue_book:
                resp = client.get("/api/download")

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "No book ID provided"}
        mock_queue_book.assert_not_called()

    def test_success_returns_queued_payload_and_forwards_user_context(self, main_module, client):
        captured: dict[str, object] = {}

        def fake_queue_book(book_id, priority, user_id=None, username=None):
            captured.update(
                {
                    "book_id": book_id,
                    "priority": priority,
                    "user_id": user_id,
                    "username": username,
                }
            )
            return True, None

        _set_authenticated_session(
            client,
            user_id="alice",
            db_user_id=42,
            is_admin=False,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_book", side_effect=fake_queue_book):
                resp = client.get("/api/download?id=book-123&priority=5")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "priority": 5}
        assert captured == {
            "book_id": "book-123",
            "priority": 5,
            "user_id": 42,
            "username": "alice",
        }

    def test_malformed_priority_returns_500_current_behavior(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_book") as mock_queue_book:
                resp = client.get("/api/download?id=book-123&priority=high")

        body = resp.get_json()
        assert resp.status_code == 500
        assert "invalid literal for int()" in body["error"]
        mock_queue_book.assert_not_called()

    def test_auth_enabled_without_session_returns_401(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.get("/api/download?id=book-123")

        assert resp.status_code == 401
        assert resp.get_json() == {"error": "Unauthorized"}

    def test_admin_can_queue_book_on_behalf_of_another_user(self, main_module, client):
        target_user = _create_user(main_module, prefix="target")
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        captured: dict[str, object] = {}

        def fake_queue_book(book_id, priority, user_id=None, username=None):
            captured.update(
                {
                    "book_id": book_id,
                    "priority": priority,
                    "user_id": user_id,
                    "username": username,
                }
            )
            return True, None

        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_book", side_effect=fake_queue_book):
                resp = client.get(
                    f"/api/download?id=book-123&priority=4&on_behalf_of_user_id={target_user['id']}"
                )

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "priority": 4}
        assert captured == {
            "book_id": "book-123",
            "priority": 4,
            "user_id": target_user["id"],
            "username": target_user["username"],
        }

    def test_non_admin_cannot_queue_book_on_behalf_of_user(self, main_module, client):
        target_user = _create_user(main_module, prefix="target")
        actor_user = _create_user(main_module, prefix="actor")
        _set_authenticated_session(
            client,
            user_id=actor_user["username"],
            db_user_id=actor_user["id"],
            is_admin=False,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_book") as mock_queue_book:
                resp = client.get(
                    f"/api/download?id=book-123&on_behalf_of_user_id={target_user['id']}"
                )

        assert resp.status_code == 403
        assert resp.get_json() == {"error": "Admin required"}
        mock_queue_book.assert_not_called()

    @pytest.mark.parametrize("raw_user_id", ["abc", "-1", "0"])
    def test_invalid_on_behalf_user_id_returns_400_for_book_download(
        self, main_module, client, raw_user_id
    ):
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_book") as mock_queue_book:
                resp = client.get(
                    f"/api/download?id=book-123&on_behalf_of_user_id={raw_user_id}"
                )

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Invalid on_behalf_of_user_id"}
        mock_queue_book.assert_not_called()

    def test_unknown_on_behalf_user_returns_404_for_book_download(self, main_module, client):
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_book") as mock_queue_book:
                resp = client.get("/api/download?id=book-123&on_behalf_of_user_id=99999999")

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "User not found"}
        mock_queue_book.assert_not_called()

    def test_on_behalf_book_download_returns_503_when_user_db_unavailable(self, main_module, client):
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "user_db", None):
                with patch.object(main_module.backend, "queue_book") as mock_queue_book:
                    resp = client.get("/api/download?id=book-123&on_behalf_of_user_id=7")

        assert resp.status_code == 503
        assert resp.get_json() == {"error": "User database unavailable"}
        mock_queue_book.assert_not_called()


class TestReleaseDownloadEndpointGuardrails:
    def test_empty_json_payload_returns_400(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json={})

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "No data provided"}
        mock_queue_release.assert_not_called()

    def test_missing_source_id_returns_400(self, main_module, client):
        payload = {
            "source": "direct_download",
            "title": "Example",
        }
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "source_id is required"}
        mock_queue_release.assert_not_called()

    def test_success_returns_queued_payload_and_forwards_user_context(self, main_module, client):
        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured.update(
                {
                    "release_data": release_data,
                    "priority": priority,
                    "user_id": user_id,
                    "username": username,
                }
            )
            return True, None

        _set_authenticated_session(
            client,
            user_id="bob",
            db_user_id=19,
            is_admin=False,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-xyz",
            "title": "Release Title",
            "priority": 3,
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release):
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "priority": 3}
        assert captured["release_data"] == {**payload, "content_type": "ebook"}
        assert captured["priority"] == 3
        assert captured["user_id"] == 19
        assert captured["username"] == "bob"

    def test_missing_content_type_infers_audiobook_from_format(self, main_module, client):
        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured.update(
                {
                    "release_data": release_data,
                    "priority": priority,
                    "user_id": user_id,
                    "username": username,
                }
            )
            return True, None

        payload = {
            "source": "prowlarr",
            "source_id": "release-audio",
            "title": "Audio Title [m4b]",
            "format": "m4b",
            "priority": 1,
        }

        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release):
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "priority": 1}
        assert captured["release_data"] == {**payload, "content_type": "audiobook"}
        assert captured["priority"] == 1

    def test_non_json_payload_returns_500_current_behavior(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post(
                    "/api/releases/download",
                    data="not-json",
                    content_type="text/plain",
                )

        body = resp.get_json()
        assert resp.status_code == 500
        assert "Unsupported Media Type" in body["error"]
        mock_queue_release.assert_not_called()

    def test_admin_can_queue_release_on_behalf_of_another_user(self, main_module, client):
        target_user = _create_user(main_module, prefix="target")
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured.update(
                {
                    "release_data": release_data,
                    "priority": priority,
                    "user_id": user_id,
                    "username": username,
                }
            )
            return True, None

        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-admin-on-behalf",
            "title": "Release Title",
            "priority": 2,
            "on_behalf_of_user_id": target_user["id"],
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release):
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "priority": 2}
        assert captured["priority"] == 2
        assert captured["user_id"] == target_user["id"]
        assert captured["username"] == target_user["username"]
        assert captured["release_data"] == {
            **payload,
            "content_type": "ebook",
        }

    def test_non_admin_cannot_queue_release_on_behalf_of_user(self, main_module, client):
        target_user = _create_user(main_module, prefix="target")
        actor_user = _create_user(main_module, prefix="actor")
        _set_authenticated_session(
            client,
            user_id=actor_user["username"],
            db_user_id=actor_user["id"],
            is_admin=False,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-forbidden",
            "title": "Release Title",
            "on_behalf_of_user_id": target_user["id"],
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 403
        assert resp.get_json() == {"error": "Admin required"}
        mock_queue_release.assert_not_called()

    @pytest.mark.parametrize("raw_user_id", ["abc", "-1", "0"])
    def test_invalid_on_behalf_user_id_returns_400_for_release_download(
        self, main_module, client, raw_user_id
    ):
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-invalid",
            "title": "Release Title",
            "on_behalf_of_user_id": raw_user_id,
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Invalid on_behalf_of_user_id"}
        mock_queue_release.assert_not_called()

    def test_unknown_on_behalf_user_returns_404_for_release_download(self, main_module, client):
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-missing-user",
            "title": "Release Title",
            "on_behalf_of_user_id": 99999999,
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "User not found"}
        mock_queue_release.assert_not_called()

    def test_on_behalf_release_download_returns_503_when_user_db_unavailable(self, main_module, client):
        admin_user = _create_user(main_module, prefix="admin", role="admin")
        _set_authenticated_session(
            client,
            user_id=admin_user["username"],
            db_user_id=admin_user["id"],
            is_admin=True,
        )
        payload = {
            "source": "direct_download",
            "source_id": "release-user-db-missing",
            "title": "Release Title",
            "on_behalf_of_user_id": 7,
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "user_db", None):
                with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                    resp = client.post("/api/releases/download", json=payload)

        assert resp.status_code == 503
        assert resp.get_json() == {"error": "User database unavailable"}
        mock_queue_release.assert_not_called()


class TestCancelDownloadEndpointGuardrails:
    def test_owner_can_cancel_direct_download(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="direct-task-1",
            source="direct_download",
            title="Direct Task",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(main_module.backend, "cancel_download", return_value=True) as mock_cancel:
                    resp = client.delete("/api/download/direct-task-1/cancel")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "cancelled", "book_id": "direct-task-1"}
        mock_cancel.assert_called_once_with("direct-task-1")

    def test_non_owner_cannot_cancel_download(self, main_module, client):
        owner = _create_user(main_module, prefix="owner")
        actor = _create_user(main_module, prefix="actor")
        _set_authenticated_session(
            client,
            user_id=actor["username"],
            db_user_id=actor["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="owned-task-1",
            source="direct_download",
            title="Owned Task",
            user_id=owner["id"],
            username=owner["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(main_module.backend, "cancel_download", return_value=True) as mock_cancel:
                    resp = client.delete("/api/download/owned-task-1/cancel")

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "download_not_owned"
        mock_cancel.assert_not_called()

    def test_owner_cannot_cancel_graduated_request_download(self, main_module, client):
        user = _create_user(main_module, prefix="requester")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Requested Book",
                "author": "Request Author",
                "provider": "openlibrary",
                "provider_id": "req-guard-1",
            },
            release_data={
                "source": "prowlarr",
                "source_id": "requested-task-1",
                "title": "Requested Book.epub",
            },
            status="fulfilled",
            delivery_state="queued",
        )
        task = DownloadTask(
            task_id="requested-task-1",
            source="prowlarr",
            title="Requested Book",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(main_module.backend, "cancel_download", return_value=True) as mock_cancel:
                    resp = client.delete("/api/download/requested-task-1/cancel")

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "requested_download_cancel_forbidden"
        mock_cancel.assert_not_called()

    def test_admin_can_cancel_graduated_request_download(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        requester = _create_user(main_module, prefix="requester")
        _set_authenticated_session(
            client,
            user_id=admin["username"],
            db_user_id=admin["id"],
            is_admin=True,
        )
        main_module.user_db.create_request(
            user_id=requester["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Admin Requested Book",
                "author": "Admin Request Author",
                "provider": "openlibrary",
                "provider_id": "req-guard-2",
            },
            release_data={
                "source": "prowlarr",
                "source_id": "requested-task-2",
                "title": "Admin Requested Book.epub",
            },
            status="fulfilled",
            delivery_state="queued",
        )
        task = DownloadTask(
            task_id="requested-task-2",
            source="prowlarr",
            title="Admin Requested Book",
            user_id=requester["id"],
            username=requester["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(main_module.backend, "cancel_download", return_value=True) as mock_cancel:
                    resp = client.delete("/api/download/requested-task-2/cancel")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "cancelled", "book_id": "requested-task-2"}
        mock_cancel.assert_called_once_with("requested-task-2")


class TestRetryDownloadEndpointGuardrails:
    def test_retry_returns_404_when_task_missing(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=None):
                with patch.object(main_module.backend, "retry_download") as mock_retry:
                    resp = client.post("/api/download/missing-task/retry")

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Download not found"}
        mock_retry.assert_not_called()

    def test_owner_can_retry_direct_download(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="direct-task-retry-1",
            source="direct_download",
            title="Direct Task",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(main_module.backend, "retry_download", return_value=(True, None)) as mock_retry:
                    resp = client.post("/api/download/direct-task-retry-1/retry")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "queued", "book_id": "direct-task-retry-1"}
        mock_retry.assert_called_once_with("direct-task-retry-1")

    def test_non_owner_cannot_retry_download(self, main_module, client):
        owner = _create_user(main_module, prefix="owner")
        actor = _create_user(main_module, prefix="actor")
        _set_authenticated_session(
            client,
            user_id=actor["username"],
            db_user_id=actor["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="owned-task-retry-1",
            source="direct_download",
            title="Owned Task",
            user_id=owner["id"],
            username=owner["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(main_module.backend, "retry_download", return_value=(True, None)) as mock_retry:
                    resp = client.post("/api/download/owned-task-retry-1/retry")

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "download_not_owned"
        mock_retry.assert_not_called()

    def test_retry_forbidden_for_request_id_linked_download(self, main_module, client):
        user = _create_user(main_module, prefix="requester")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="requested-retry-1",
            source="prowlarr",
            title="Requested Book",
            user_id=user["id"],
            username=user["username"],
            request_id=123,
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(main_module.backend, "retry_download", return_value=(True, None)) as mock_retry:
                    resp = client.post("/api/download/requested-retry-1/retry")

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "requested_download_retry_forbidden"
        mock_retry.assert_not_called()

    def test_retry_forbidden_for_graduated_request_download(self, main_module, client):
        user = _create_user(main_module, prefix="requester")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Requested Book",
                "author": "Request Author",
                "provider": "openlibrary",
                "provider_id": "req-retry-1",
            },
            release_data={
                "source": "prowlarr",
                "source_id": "requested-retry-2",
                "title": "Requested Book.epub",
            },
            status="fulfilled",
            delivery_state="error",
        )
        task = DownloadTask(
            task_id="requested-retry-2",
            source="prowlarr",
            title="Requested Book",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(main_module.backend, "retry_download", return_value=(True, None)) as mock_retry:
                    resp = client.post("/api/download/requested-retry-2/retry")

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "requested_download_retry_forbidden"
        mock_retry.assert_not_called()

    def test_retry_returns_409_for_non_retryable_state(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_authenticated_session(
            client,
            user_id=user["username"],
            db_user_id=user["id"],
            is_admin=False,
        )
        task = DownloadTask(
            task_id="direct-task-retry-409",
            source="direct_download",
            title="Direct Task",
            user_id=user["id"],
            username=user["username"],
        )

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=task):
                with patch.object(
                    main_module.backend,
                    "retry_download",
                    return_value=(False, "Download is not in an error state"),
                ) as mock_retry:
                    resp = client.post("/api/download/direct-task-retry-409/retry")

        assert resp.status_code == 409
        assert resp.get_json() == {"error": "Download is not in an error state"}
        mock_retry.assert_called_once_with("direct-task-retry-409")


class TestStatusEndpointGuardrails:
    def test_no_auth_allows_without_session_and_returns_status(self, main_module, client):
        observed: dict[str, object] = {}
        expected_status = {
            "queued": {"book-1": {"title": "One"}},
            "downloading": {},
            "completed": {},
            "failed": {},
            "cancelled": {},
        }

        def fake_queue_status(user_id=None):
            observed["user_id"] = user_id
            return expected_status

        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_status", side_effect=fake_queue_status):
                resp = client.get("/api/status")

        assert resp.status_code == 200
        assert resp.get_json() == expected_status
        assert observed["user_id"] is None

    def test_auth_enabled_without_session_returns_401(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.get("/api/status")

        assert resp.status_code == 401
        assert resp.get_json() == {"error": "Unauthorized"}

    def test_non_admin_status_is_scoped_to_db_user(self, main_module, client):
        observed: dict[str, object] = {}

        def fake_queue_status(user_id=None):
            observed["user_id"] = user_id
            return {"queued": {}, "downloading": {}, "completed": {}, "failed": {}, "cancelled": {}}

        _set_authenticated_session(
            client,
            user_id="reader",
            db_user_id=55,
            is_admin=False,
        )
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", side_effect=fake_queue_status):
                resp = client.get("/api/status")

        assert resp.status_code == 200
        assert observed["user_id"] == 55

    def test_admin_status_is_unscoped(self, main_module, client):
        observed: dict[str, object] = {}

        def fake_queue_status(user_id=None):
            observed["user_id"] = user_id
            return {"queued": {}, "downloading": {}, "completed": {}, "failed": {}, "cancelled": {}}

        _set_authenticated_session(
            client,
            user_id="admin",
            db_user_id=1,
            is_admin=True,
        )
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", side_effect=fake_queue_status):
                resp = client.get("/api/status")

        assert resp.status_code == 200
        assert observed["user_id"] is None
