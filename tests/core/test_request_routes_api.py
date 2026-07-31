"""API tests for the canonical Book-level Request lifecycle."""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.request_routes import register_request_routes
from shelfmark.core.user_db import UserDB


@pytest.fixture
def main_module():
    """Register canonical routes against an isolated database."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        user_db = UserDB(os.path.join(tmpdir, "users.db"))
        user_db.initialize()
        app = Flask(__name__)
        app.secret_key = "test-secret"
        backend = SimpleNamespace(queue_release=lambda *_args, **_kwargs: (True, None))
        register_request_routes(
            app,
            user_db,
            resolve_auth_mode=lambda: "builtin",
            queue_release=lambda *args, **kwargs: backend.queue_release(*args, **kwargs),
        )
        yield SimpleNamespace(app=app, user_db=user_db, backend=backend)


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _set_session(client, *, user: dict) -> None:
    with client.session_transaction() as session:
        session["user_id"] = user["username"]
        session["db_user_id"] = user["id"]
        session["is_admin"] = user["role"] == "admin"


def _create_user(main_module, *, role: str = "user", capability: str = "request-only") -> dict:
    return main_module.user_db.create_user(
        username=f"request-{uuid.uuid4().hex[:12]}",
        role=role,
        library_capability=capability,
    )


def _create_book(main_module, *, member_ids: list[int]) -> int:
    conn = main_module.user_db._connect()
    try:
        cursor = conn.execute(
            "INSERT INTO books (metadata_provider, provider_book_id, title, author, metadata_json) "
            "VALUES (?, ?, ?, ?, ?)",
            ("test", uuid.uuid4().hex, "Canonical Request Book", "Shelfmark", "{}"),
        )
        book_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO user_library (user_id, book_id) VALUES (?, ?)",
            [(user_id, book_id) for user_id in member_ids],
        )
        conn.commit()
        return book_id
    finally:
        conn.close()


def _add_completed_file(main_module, *, book_id: int) -> int:
    conn = main_module.user_db._connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO download_history (
                task_id, source, title, origin, final_status, download_path, book_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                "test",
                "Canonical Request Book",
                "direct",
                "complete",
                "/tmp/book.epub",
                book_id,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _auth_mode():
    return nullcontext()


def test_create_request_requires_request_only_capability(main_module, client):
    user = _create_user(main_module, capability="download-capable")
    book_id = _create_book(main_module, member_ids=[user["id"]])
    _set_session(client, user=user)

    with _auth_mode():
        response = client.post("/api/requests", json={"book_id": book_id})

    assert response.status_code == 403
    assert response.json["error"] == "Request-only capability required"


def test_create_request_requires_membership_and_no_completed_files(main_module, client):
    user = _create_user(main_module)
    non_member_book_id = _create_book(main_module, member_ids=[])
    member_book_id = _create_book(main_module, member_ids=[user["id"]])
    _add_completed_file(main_module, book_id=member_book_id)
    _set_session(client, user=user)

    with _auth_mode():
        non_member_response = client.post("/api/requests", json={"book_id": non_member_book_id})
        files_response = client.post("/api/requests", json={"book_id": member_book_id})

    assert non_member_response.status_code == 409
    assert non_member_response.json["error"] == "Book is not in the user's library"
    assert files_response.status_code == 409
    assert files_response.json["error"] == "Book already has completed Files"


def test_create_request_uses_book_id_and_rejects_duplicate_pending_request(main_module, client):
    user = _create_user(main_module)
    book_id = _create_book(main_module, member_ids=[user["id"]])
    _set_session(client, user=user)

    with _auth_mode():
        created = client.post("/api/requests", json={"book_id": book_id, "note": "  Add it  "})
        duplicate = client.post("/api/requests", json={"book_id": book_id})

    assert created.status_code == 201
    assert created.json["book_id"] == book_id
    assert created.json["status"] == "pending"
    assert created.json["note"] == "Add it"
    assert duplicate.status_code == 409
    assert duplicate.json["error"] == "Duplicate pending Request exists for this Book"


def test_user_can_list_and_cancel_only_own_request(main_module, client):
    owner = _create_user(main_module)
    other = _create_user(main_module)
    book_id = _create_book(main_module, member_ids=[owner["id"], other["id"]])
    owner_request = main_module.user_db.create_library_request(user_id=owner["id"], book_id=book_id)
    other_request = main_module.user_db.create_library_request(user_id=other["id"], book_id=book_id)
    _set_session(client, user=owner)

    with _auth_mode():
        listed = client.get("/api/requests")
        forbidden = client.delete(f"/api/requests/{other_request['id']}")
        cancelled = client.delete(f"/api/requests/{owner_request['id']}")

    assert [request["id"] for request in listed.json] == [owner_request["id"]]
    assert forbidden.status_code == 403
    assert cancelled.status_code == 200
    assert cancelled.json["status"] == "cancelled"


def test_admin_can_reject_pending_request(main_module, client):
    requester = _create_user(main_module)
    admin = _create_user(main_module, role="admin", capability="download-capable")
    book_id = _create_book(main_module, member_ids=[requester["id"]])
    pending = main_module.user_db.create_library_request(user_id=requester["id"], book_id=book_id)
    _set_session(client, user=admin)

    with patch("shelfmark.core.request_routes.notify_user") as notify:
        with _auth_mode():
            response = client.post(
                f"/api/admin/requests/{pending['id']}/reject",
                json={"admin_note": "  Unavailable  "},
            )

    assert response.status_code == 200
    assert response.json["status"] == "rejected"
    assert response.json["reviewed_by"] == admin["id"]
    assert response.json["admin_note"] == "Unavailable"
    assert notify.call_args.args[2].value == "request_rejected"
    assert notify.call_args.args[3].title == "Canonical Request Book"


def test_admin_cannot_fulfil_requests_without_a_release(main_module, client):
    first = _create_user(main_module)
    second = _create_user(main_module)
    admin = _create_user(main_module, role="admin", capability="download-capable")
    book_id = _create_book(main_module, member_ids=[first["id"], second["id"]])
    first_request = main_module.user_db.create_library_request(user_id=first["id"], book_id=book_id)
    second_request = main_module.user_db.create_library_request(
        user_id=second["id"], book_id=book_id
    )
    _set_session(client, user=admin)

    with patch("shelfmark.core.request_routes.notify_user") as notify:
        with _auth_mode():
            response = client.post(f"/api/admin/requests/books/{book_id}/fulfil", json={})

    assert response.status_code == 400
    assert response.json == {"error": "release_data is required"}
    assert main_module.user_db.get_request(first_request["id"])["status"] == "pending"
    assert main_module.user_db.get_request(second_request["id"])["status"] == "pending"
    conn = main_module.user_db._connect()
    try:
        links = conn.execute("SELECT user_id, history_id FROM user_downloads").fetchall()
    finally:
        conn.close()
    assert links == []
    notify.assert_not_called()


def test_shared_release_queue_failure_keeps_all_requests_pending(main_module, client):
    first = _create_user(main_module)
    second = _create_user(main_module)
    admin = _create_user(main_module, role="admin", capability="download-capable")
    book_id = _create_book(main_module, member_ids=[first["id"], second["id"]])
    first_request = main_module.user_db.create_library_request(user_id=first["id"], book_id=book_id)
    second_request = main_module.user_db.create_library_request(
        user_id=second["id"], book_id=book_id
    )
    _set_session(client, user=admin)

    with _auth_mode():
        with patch.object(
            main_module.backend, "queue_release", return_value=(False, "Queue unavailable")
        ):
            response = client.post(
                f"/api/admin/requests/books/{book_id}/fulfil",
                json={"release_data": {"source": "test", "source_id": "shared-release"}},
            )

    assert response.status_code == 409
    assert response.json == {"error": "Queue unavailable", "code": "queue_failed"}
    assert main_module.user_db.get_request(first_request["id"])["status"] == "pending"
    assert main_module.user_db.get_request(second_request["id"])["status"] == "pending"
