"""API tests for request routes and policy enforcement guards."""

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


def _policy(
    *,
    requests_enabled: bool = True,
    default_ebook: str = "download",
    default_audiobook: str = "download",
    rules: list[dict] | None = None,
) -> dict:
    return {
        "REQUESTS_ENABLED": requests_enabled,
        "REQUEST_POLICY_DEFAULT_EBOOK": default_ebook,
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": default_audiobook,
        "REQUEST_POLICY_RULES": rules or [],
    }


class TestDownloadPolicyGuards:
    def test_download_endpoint_blocks_before_queue_when_policy_requires_request(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module,
                "_load_users_request_policy_settings",
                return_value=_policy(default_ebook="request_release"),
            ):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=_policy(default_ebook="request_release")):
                    with patch.object(main_module.backend, "queue_book") as mock_queue_book:
                        resp = client.get("/api/download?id=book-123")

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_requires_request"
        assert resp.json["required_mode"] == "request_release"
        mock_queue_book.assert_not_called()

    def test_release_download_endpoint_blocks_before_queue_when_policy_blocked(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module,
                "_load_users_request_policy_settings",
                return_value=_policy(default_ebook="blocked"),
            ):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=_policy(default_ebook="blocked")):
                    with patch.object(main_module.backend, "queue_release") as mock_queue_release:
                        resp = client.post(
                            "/api/releases/download",
                            json={"source": "direct_download", "source_id": "rel-1", "content_type": "ebook"},
                        )

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_blocked"
        assert resp.json["required_mode"] == "blocked"
        mock_queue_release.assert_not_called()

    def test_admin_bypasses_policy_guards(self, main_module, client):
        admin = _create_user(main_module, prefix="admin", role="admin")
        _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module,
                "_load_users_request_policy_settings",
                return_value=_policy(default_ebook="blocked"),
            ):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=_policy(default_ebook="blocked")):
                    with patch.object(main_module.backend, "queue_book", return_value=(True, None)) as mock_queue_book:
                        resp = client.get("/api/download?id=book-123")

        assert resp.status_code == 200
        assert resp.json["status"] == "queued"
        mock_queue_book.assert_called_once()

    def test_no_auth_mode_bypasses_policy_guards(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(
                main_module,
                "_load_users_request_policy_settings",
                return_value=_policy(default_ebook="blocked"),
            ):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=_policy(default_ebook="blocked")):
                    with patch.object(main_module.backend, "queue_book", return_value=(True, None)) as mock_queue_book:
                        resp = client.get("/api/download?id=book-123")

        assert resp.status_code == 200
        assert resp.json["status"] == "queued"
        mock_queue_book.assert_called_once()


class TestRequestRoutes:
    def test_request_endpoints_are_unavailable_in_no_auth_mode(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            resp = client.get("/api/requests")

        assert resp.status_code == 403
        assert resp.json["code"] == "requests_unavailable"

    def test_request_policy_endpoint_returns_effective_policy(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_release")

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "_load_users_request_policy_settings", return_value=policy):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=policy):
                    resp = client.get("/api/request-policy")

        assert resp.status_code == 200
        assert resp.json["requests_enabled"] is True
        assert resp.json["defaults"]["ebook"] == "request_release"
        assert "source_modes" in resp.json

    def test_create_list_and_cancel_request(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "The Pragmatic Programmer",
                "author": "Andrew Hunt",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-1",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
            "note": "Please add this",
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "_load_users_request_policy_settings", return_value=policy):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=policy):
                    create_resp = client.post("/api/requests", json=payload)
                    list_resp = client.get("/api/requests")

                    assert create_resp.status_code == 201
                    request_id = create_resp.json["id"]
                    assert create_resp.json["status"] == "pending"
                    assert any(item["id"] == request_id for item in list_resp.json)

                    cancel_resp = client.delete(f"/api/requests/{request_id}")

        assert cancel_resp.status_code == 200
        assert cancel_resp.json["status"] == "cancelled"

    def test_create_request_level_payload_mismatch_returns_400(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_release")

        payload = {
            "book_data": {
                "title": "Clean Code",
                "author": "Robert Martin",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-2",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "book",
            },
            "release_data": {
                "source": "prowlarr",
                "source_id": "rel-2",
                "title": "Clean Code.epub",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "_load_users_request_policy_settings", return_value=policy):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=policy):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 400
        assert "request_level=book requires null release_data" in resp.json["error"]

    def test_duplicate_pending_request_returns_409(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Domain-Driven Design",
                "author": "Eric Evans",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-3",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "_load_users_request_policy_settings", return_value=policy):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=policy):
                    first_resp = client.post("/api/requests", json=payload)
                    second_resp = client.post("/api/requests", json=payload)

        assert first_resp.status_code == 201
        assert second_resp.status_code == 409
        assert second_resp.json["code"] == "duplicate_pending_request"

    def test_request_book_policy_requires_book_level_request(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        policy = _policy(default_ebook="request_book")

        payload = {
            "book_data": {
                "title": "Refactoring",
                "author": "Martin Fowler",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-4",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "prowlarr",
                "source_id": "rel-4",
                "title": "Refactoring.epub",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "_load_users_request_policy_settings", return_value=policy):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=policy):
                    resp = client.post("/api/requests", json=payload)

        assert resp.status_code == 403
        assert resp.json["code"] == "policy_requires_request"
        assert resp.json["required_mode"] == "request_book"

    def test_non_admin_cannot_access_admin_request_routes(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = client.get("/api/admin/requests")

        assert resp.status_code == 403
        assert resp.json["error"] == "Admin access required"

    def test_admin_reject_and_terminal_conflict(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Working Effectively with Legacy Code",
                "author": "Michael Feathers",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-5",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "_load_users_request_policy_settings", return_value=policy):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=policy):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
                    count_resp = client.get("/api/admin/requests/count")
                    reject_resp = client.post(
                        f"/api/admin/requests/{request_id}/reject",
                        json={"admin_note": "Declined"},
                    )
                    reject_again_resp = client.post(
                        f"/api/admin/requests/{request_id}/reject",
                        json={"admin_note": "Declined again"},
                    )

        assert count_resp.status_code == 200
        assert count_resp.json["pending"] >= 1
        assert reject_resp.status_code == 200
        assert reject_resp.json["status"] == "rejected"
        assert reject_again_resp.status_code == 409
        assert reject_again_resp.json["code"] == "stale_transition"

    def test_admin_fulfil_queues_for_requesting_user(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_release")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Patterns of Enterprise Application Architecture",
                "author": "Martin Fowler",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-6",
            },
            "context": {
                "source": "prowlarr",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "prowlarr",
                "source_id": "rel-6",
                "title": "POEAA.epub",
            },
        }

        captured: dict[str, object] = {}

        def fake_queue_release(release_data, priority, user_id=None, username=None):
            captured["release_data"] = release_data
            captured["priority"] = priority
            captured["user_id"] = user_id
            captured["username"] = username
            return True, None

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "_load_users_request_policy_settings", return_value=policy):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=policy):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
                    with patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release):
                        fulfil_resp = client.post(
                            f"/api/admin/requests/{request_id}/fulfil",
                            json={"admin_note": "Approved"},
                        )

        assert fulfil_resp.status_code == 200
        assert fulfil_resp.json["status"] == "fulfilled"
        assert captured["priority"] == 0
        assert captured["user_id"] == user["id"]
        assert captured["username"] == user["username"]

    def test_admin_fulfil_book_level_request_requires_release_data(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_book")

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Designing Data-Intensive Applications",
                "author": "Martin Kleppmann",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-7",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "book",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "_load_users_request_policy_settings", return_value=policy):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=policy):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
                    fulfil_resp = client.post(f"/api/admin/requests/{request_id}/fulfil", json={})

        assert fulfil_resp.status_code == 400
        assert "release_data is required to fulfil book-level requests" in fulfil_resp.json["error"]

    def test_admin_fulfil_uses_real_queue_and_preserves_requesting_identity(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        other_user = _create_user(main_module, prefix="reader")
        admin = _create_user(main_module, prefix="admin", role="admin")
        policy = _policy(default_ebook="request_release")
        source_id = f"real-queue-{uuid.uuid4().hex[:10]}"

        _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)
        create_payload = {
            "book_data": {
                "title": "Building Microservices",
                "author": "Sam Newman",
                "content_type": "ebook",
                "provider": "openlibrary",
                "provider_id": "ol-8",
            },
            "context": {
                "source": "direct_download",
                "content_type": "ebook",
                "request_level": "release",
            },
            "release_data": {
                "source": "direct_download",
                "source_id": source_id,
                "title": "Building Microservices.epub",
            },
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module, "_load_users_request_policy_settings", return_value=policy):
                with patch("shelfmark.core.request_routes._load_users_request_policy_settings", return_value=policy):
                    create_resp = client.post("/api/requests", json=create_payload)
                    request_id = create_resp.json["id"]

                    _set_session(client, user_id=admin["username"], db_user_id=admin["id"], is_admin=True)
                    fulfil_resp = client.post(f"/api/admin/requests/{request_id}/fulfil", json={})

        assert fulfil_resp.status_code == 200
        assert fulfil_resp.json["status"] == "fulfilled"

        user_status = main_module.backend.queue_status(user_id=user["id"])
        assert source_id in user_status["queued"]
        assert user_status["queued"][source_id]["username"] == user["username"]

        other_status = main_module.backend.queue_status(user_id=other_user["id"])
        assert source_id not in other_status["queued"]
