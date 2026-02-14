"""Baseline guardrail tests for download API endpoints.

These tests lock current behavior for `/api/download`, `/api/releases/download`,
and `/api/status` so policy work in later phases cannot accidentally change
existing contracts.
"""

from __future__ import annotations

import importlib
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
        assert captured["release_data"] == payload
        assert captured["priority"] == 3
        assert captured["user_id"] == 19
        assert captured["username"] == "bob"

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
