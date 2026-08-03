"""Routes-level tests for the library API (#04 contract / #06 implementation)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.import_activity_service import ImportActivityService
from shelfmark.core.library_routes import register_library_routes
from shelfmark.core.library_service import LibraryService
from shelfmark.core.request_routes import register_request_routes
from shelfmark.core.user_db import UserDB


def _always_builtin_auth_mode() -> str:
    return "builtin"


def _no_auth_mode() -> str:
    return "none"


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "users.db")


@pytest.fixture
def user_db(db_path):
    db = UserDB(db_path)
    db.initialize()
    return db


@pytest.fixture
def download_history_service(db_path):
    return DownloadHistoryService(db_path)


@pytest.fixture
def import_activity_service(db_path):
    return ImportActivityService(db_path)


@pytest.fixture
def library_service(user_db, db_path):
    return LibraryService(db_path)


@pytest.fixture
def app(
    user_db,
    library_service,
    download_history_service,
    import_activity_service,
    tmp_path,
):
    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True
    cancelled_tasks: list[str] = []
    cleared_completed_tasks: list[str] = []
    queued_releases: list[tuple[dict[str, Any], int, int | None, str | None]] = []
    test_app.extensions["cancelled_tasks"] = cancelled_tasks
    test_app.extensions["cleared_completed_tasks"] = cleared_completed_tasks
    test_app.extensions["queued_releases"] = queued_releases
    test_app.extensions["cancel_should_fail"] = False

    def _clear_completed_download(task_id: str) -> bool:
        cleared_completed_tasks.append(task_id)
        return True

    def _queue_release(
        release: dict[str, Any], priority: int, *, user_id: int | None, username: str | None
    ) -> tuple[bool, None]:
        queued_releases.append((release, priority, user_id, username))
        return True, None

    def _resolve_metadata_book(provider: str, provider_book_id: str) -> dict[str, Any] | None:
        # Deterministic stub for tests; mirrors the live _resolve_metadata_book_for_library
        # output shape: title/author/cover_url + metadata_json raw payload.
        return {
            "provider": provider,
            "provider_id": provider_book_id,
            "title": f"Book {provider_book_id}",
            "authors": ["Author A"],
            "author": "Author A",
            "isbn_13": None,
            "cover_url": None,
            "publish_year": 2024,
            "series_name": None,
            "series_position": None,
            "subtitle": None,
            "language": "en",
            "metadata_json": {"provider": provider, "provider_id": provider_book_id},
        }

    register_library_routes(
        test_app,
        user_db,
        library_service=library_service,
        download_history_service=download_history_service,
        resolve_auth_mode=_always_builtin_auth_mode,
        resolve_metadata_book=_resolve_metadata_book,
        cancel_download=lambda task_id: (
            cancelled_tasks.append(task_id) is None
            and not test_app.extensions["cancel_should_fail"]
        ),
        clear_completed_download=_clear_completed_download,
        import_activity_service=import_activity_service,
        storage_root=tmp_path / "books",
    )
    register_request_routes(
        test_app,
        user_db,
        resolve_auth_mode=_always_builtin_auth_mode,
        queue_release=_queue_release,
    )
    return test_app


def _authed_client(app: Flask, user: dict, *, is_admin: bool = False) -> Any:
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["db_user_id"] = user["id"]
        sess["is_admin"] = is_admin
    return client


def _seed_history_row(
    user_db: UserDB,
    *,
    task_id: str,
    user_id: int,
    username: str,
    book_id: int,
    fmt: str,
    download_path: str,
    final_status: str = "complete",
) -> int:
    conn = user_db._connect()
    try:
        conn.execute(
            """
            INSERT INTO download_history (
                task_id, user_id, username, source, title, format, content_type,
                origin, final_status, download_path, terminal_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                user_id,
                username,
                "direct_download",
                "Book",
                fmt,
                "ebook",
                "direct",
                final_status,
                download_path,
                "2026-01-01T00:00:00+00:00",
            ),
        )
        row = conn.execute(
            "SELECT id FROM download_history WHERE task_id = ?", (task_id,)
        ).fetchone()
        history_id = int(row["id"])
        conn.execute(
            "UPDATE download_history SET book_id = ? WHERE id = ?",
            (book_id, history_id),
        )
        conn.commit()
        return history_id
    finally:
        conn.close()


def test_add_book_requires_authentication(app):
    client = app.test_client()
    resp = client.post(
        "/api/library/books",
        json={"metadata_provider": "hardcover", "provider_book_id": "1"},
    )
    assert resp.status_code == 401


def test_add_book_returns_idempotent_payload_and_caches_metadata(app, user_db):
    alice = user_db.create_user(username="alice")
    client = _authed_client(app, alice)
    payload = {
        "metadata_provider": "hardcover",
        "provider_book_id": "123",
    }

    first = client.post("/api/library/books", json=payload).json
    second = client.post("/api/library/books", json=payload).json

    assert first["book_id"] == second["book_id"]
    assert first["in_my_library"] is True
    assert first["files_exist_globally"] is False
    assert first["in_flight_globally"] is False


def test_request_only_user_can_add_a_book_and_request_it_when_no_files_exist(app, user_db):
    requester = user_db.create_user(username="requester", library_capability="request-only")
    client = _authed_client(app, requester)

    added = client.post(
        "/api/library/books",
        json={"metadata_provider": "hardcover", "provider_book_id": "request-only-book"},
    )
    created = client.post("/api/requests", json={"book_id": added.json["book_id"]})

    assert added.status_code == 200
    assert added.json["files_exist_globally"] is False
    assert created.status_code == 201
    assert created.json["book_id"] == added.json["book_id"]
    assert created.json["status"] == "pending"


def test_manual_request_fulfilment_finalizes_files_for_the_requester(
    app, user_db, download_history_service
):
    requester = user_db.create_user(username="requester", library_capability="request-only")
    admin = user_db.create_user(
        username="admin", role="admin", library_capability="download-capable"
    )
    requester_client = _authed_client(app, requester)
    added = requester_client.post(
        "/api/library/books",
        json={"metadata_provider": "hardcover", "provider_book_id": "manual-request-book"},
    )
    book_id = added.json["book_id"]
    pending = requester_client.post("/api/requests", json={"book_id": book_id})
    admin_client = _authed_client(app, admin, is_admin=True)
    release_data = {"source": "test", "source_id": "manual-release", "title": "Requested Book"}

    fulfil = admin_client.post(
        f"/api/admin/requests/books/{book_id}/fulfil", json={"release_data": release_data}
    )

    assert added.status_code == 200
    assert pending.status_code == 201
    assert fulfil.status_code == 200
    assert fulfil.json == {"status": "queued", "book_id": book_id}
    assert app.extensions["queued_releases"] == [
        ({**release_data, "library_book_id": book_id}, 0, admin["id"], "admin")
    ]

    for task_id, final_status in (
        ("empty-completed-release", "complete"),
        ("failed-release", "error"),
        ("cancelled-release", "cancelled"),
    ):
        download_history_service.record_download(
            task_id=task_id,
            user_id=None,
            username=None,
            request_id=None,
            source="test",
            source_display_name="Test",
            title="Requested Book",
            author="Author A",
            file_format=None,
            size=None,
            preview=None,
            content_type="ebook",
            origin="direct",
            book_id=book_id,
        )
        download_history_service.finalize_download_files(
            task_id=task_id, final_status=final_status, file_rows=[]
        )
        assert requester_client.get("/api/requests").json == [{**pending.json, "status": "pending"}]

    download_history_service.record_download(
        task_id="completed-release",
        user_id=None,
        username=None,
        request_id=None,
        source="test",
        source_display_name="Test",
        title="Requested Book",
        author="Author A",
        file_format=None,
        size=None,
        preview=None,
        content_type="ebook",
        origin="direct",
        book_id=book_id,
    )
    download_history_service.finalize_download_files(
        task_id="completed-release",
        final_status="complete",
        file_rows=[
            {"download_path": "/tmp/requested.epub", "format": "epub", "size": "1"},
            {"download_path": "/tmp/requested.pdf", "format": "pdf", "size": "2"},
        ],
    )

    assert requester_client.get("/api/requests").json[0]["status"] == "fulfilled"
    detail = requester_client.get(f"/api/library/books/{book_id}")
    assert detail.status_code == 200
    assert {file["format"] for file in detail.json["files"]} == {"epub", "pdf"}
    conn = user_db._connect()
    try:
        links = conn.execute(
            "SELECT user_id, history_id FROM user_downloads ORDER BY history_id"
        ).fetchall()
    finally:
        conn.close()
    assert [link["user_id"] for link in links] == [requester["id"], requester["id"]]


def test_add_book_returns_503_when_metadata_provider_unavailable(user_db, db_path):
    alice = user_db.create_user(username="alice")
    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True

    def _resolve_none(_provider: str, _provider_book_id: str) -> None:
        return None

    new_service = LibraryService(db_path)
    new_dhs = DownloadHistoryService(db_path)
    register_library_routes(
        test_app,
        user_db,
        library_service=new_service,
        download_history_service=new_dhs,
        resolve_auth_mode=_always_builtin_auth_mode,
        resolve_metadata_book=_resolve_none,
        cancel_download=lambda _task_id: True,
        clear_completed_download=lambda _task_id: True,
    )
    client = _authed_client(test_app, alice)
    resp = client.post(
        "/api/library/books",
        json={"metadata_provider": "hardcover", "provider_book_id": "999"},
    )
    assert resp.status_code == 503


def test_add_book_rejects_missing_payload_fields(app, user_db):
    alice = user_db.create_user(username="alice")
    client = _authed_client(app, alice)
    resp = client.post("/api/library/books", json={"metadata_provider": "hardcover"})
    assert resp.status_code == 400


def test_list_books_scoped_to_own_library(app, user_db):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    alice_book = client_post_book(app, alice, "hardcover", "alice-1")
    bob_book = client_post_book(app, bob, "hardcover", "bob-1")

    alice_view = _authed_client(app, alice).get("/api/library/books").json
    bob_view = _authed_client(app, bob).get("/api/library/books").json

    alice_ids = [b["book_id"] for b in alice_view["books"]]
    bob_ids = [b["book_id"] for b in bob_view["books"]]
    assert alice_book in alice_ids and bob_book not in alice_ids
    assert bob_book in bob_ids and alice_book not in bob_ids


def test_list_books_paginates_in_membership_order_and_returns_page_metadata(app, user_db):
    alice = user_db.create_user(username="alice")
    book_ids = [client_post_book(app, alice, "hardcover", str(index)) for index in range(3)]
    conn = user_db._connect()
    try:
        for index, book_id in enumerate(book_ids):
            conn.execute(
                "UPDATE user_library SET added_at = ? WHERE user_id = ? AND book_id = ?",
                (f"2026-01-0{index + 1}T00:00:00+00:00", alice["id"], book_id),
            )
        conn.commit()
    finally:
        conn.close()

    response = _authed_client(app, alice).get("/api/library/books?limit=2&offset=1")

    assert response.status_code == 200
    assert [book["book_id"] for book in response.json["books"]] == [book_ids[1], book_ids[0]]
    assert response.json["total"] == 3
    assert response.json["limit"] == 2
    assert response.json["offset"] == 1


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "limit=nope", "offset=-1", "offset=nope"]
)
def test_list_books_rejects_invalid_pagination_parameters(app, user_db, query):
    alice = user_db.create_user(username="alice")

    response = _authed_client(app, alice).get(f"/api/library/books?{query}")

    assert response.status_code == 400


def test_list_books_filters_search_and_availability_before_pagination(
    app, user_db, library_service
):
    alice = user_db.create_user(username="alice")
    available_book = client_post_book(app, alice, "hardcover", "available")
    unavailable_book = client_post_book(app, alice, "hardcover", "unavailable")
    _seed_history_row(
        user_db,
        task_id="available-file",
        user_id=alice["id"],
        username="alice",
        book_id=available_book,
        fmt="epub",
        download_path="/tmp/available.epub",
    )

    with patch.object(library_service, "get_files_on_disk", side_effect=AssertionError):
        with_files = _authed_client(app, alice).get(
            "/api/library/books?availability=with-files&limit=1"
        )
    needs_files = _authed_client(app, alice).get(
        "/api/library/books?availability=needs-files&q=unavailable"
    )

    assert [book["book_id"] for book in with_files.json["books"]] == [available_book]
    assert with_files.json["total"] == 1
    assert with_files.json["books"][0]["formats_on_disk"] == [{"format": "epub", "size": None}]
    assert [book["book_id"] for book in needs_files.json["books"]] == [unavailable_book]
    assert needs_files.json["total"] == 1


def test_list_books_admin_paginates_shared_books_once(app, user_db):
    admin = user_db.create_user(username="admin", role="admin")
    reader = user_db.create_user(username="reader")
    shared_book = client_post_book(app, admin, "hardcover", "shared")
    client_post_book(app, reader, "hardcover", "shared")
    admin_book = client_post_book(app, admin, "hardcover", "admin")
    reader_book = client_post_book(app, reader, "hardcover", "reader")
    conn = user_db._connect()
    try:
        for index, book_id in enumerate([shared_book, admin_book, reader_book]):
            conn.execute(
                "UPDATE user_library SET added_at = ? WHERE book_id = ?",
                (f"2026-01-0{index + 1}T00:00:00+00:00", book_id),
            )
        conn.commit()
    finally:
        conn.close()

    client = _authed_client(app, admin, is_admin=True)
    first_page = client.get("/api/library/books?scope=all&limit=2").json
    second_page = client.get("/api/library/books?scope=all&limit=2&offset=2").json

    assert first_page["total"] == 3
    assert {book["book_id"] for book in first_page["books"] + second_page["books"]} == {
        shared_book,
        admin_book,
        reader_book,
    }


def test_list_books_admin_defaults_to_own_library(app, user_db):
    alice = user_db.create_user(username="alice", role="user")
    admin = user_db.create_user(username="admin", role="admin")
    alice_book_id = client_post_book(app, alice, "hardcover", "a-1")
    admin_book_id = client_post_book(app, admin, "hardcover", "admin-1")

    response = _authed_client(app, admin, is_admin=True).get("/api/library/books")

    assert [book["book_id"] for book in response.json["books"]] == [admin_book_id]
    assert alice_book_id not in [book["book_id"] for book in response.json["books"]]


def test_list_books_admin_can_request_all_libraries(app, user_db):
    alice = user_db.create_user(username="alice", role="user")
    admin = user_db.create_user(username="admin", role="admin")
    alice_book_id = client_post_book(app, alice, "hardcover", "a-1")
    admin_book_id = client_post_book(app, admin, "hardcover", "admin-1")

    response = _authed_client(app, admin, is_admin=True).get("/api/library/books?scope=all")

    assert {book["book_id"] for book in response.json["books"]} == {alice_book_id, admin_book_id}
    assert all(
        set(book) == {"book_id", "title", "author", "cover_url", "formats_on_disk", "added_at"}
        for book in response.json["books"]
    )


def test_list_books_ignores_all_scope_for_non_admin(app, user_db):
    alice = user_db.create_user(username="alice", role="user")
    bob = user_db.create_user(username="bob", role="user")
    alice_book_id = client_post_book(app, alice, "hardcover", "alice-1")
    bob_book_id = client_post_book(app, bob, "hardcover", "bob-1")

    response = _authed_client(app, alice).get("/api/library/books?scope=all")

    assert [book["book_id"] for book in response.json["books"]] == [alice_book_id]
    assert bob_book_id not in [book["book_id"] for book in response.json["books"]]


def test_list_books_no_auth_mode_keeps_instance_wide_view(
    app, user_db, library_service, download_history_service
):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "alice-1")
    no_auth_app = Flask(__name__)
    no_auth_app.config["SECRET_KEY"] = "test-secret"
    no_auth_app.config["TESTING"] = True
    register_library_routes(
        no_auth_app,
        user_db,
        library_service=library_service,
        download_history_service=download_history_service,
        resolve_auth_mode=_no_auth_mode,
        resolve_metadata_book=lambda _provider, _provider_book_id: None,
        cancel_download=lambda _task_id: True,
        clear_completed_download=lambda _task_id: True,
    )

    response = no_auth_app.test_client().get("/api/library/books")

    assert [book["book_id"] for book in response.json["books"]] == [book_id]


def test_book_detail_403_for_non_member(app, user_db):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    alice_book = client_post_book(app, alice, "hardcover", "alice-1")

    # Bob is not in alice's library and is not admin — must get 403.
    bob_client = _authed_client(app, bob)
    resp = bob_client.get(f"/api/library/books/{alice_book}")
    assert resp.status_code == 403


def test_book_detail_returns_full_metadata_for_member(app, user_db):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "42")

    resp = _authed_client(app, alice).get(f"/api/library/books/{book_id}").json
    assert resp["book_id"] == book_id
    assert resp["title"] == "Book 42"
    assert resp["in_my_library"] is True
    assert resp["metadata_json"] == {"provider": "hardcover", "provider_id": "42"}


def test_admin_can_review_and_replace_a_completed_source_release(
    app, user_db, import_activity_service, download_history_service, library_service, tmp_path
):
    admin = user_db.create_user(username="admin", role="admin")
    book_id = client_post_book(app, admin, "hardcover", "review-source")
    source_root = tmp_path / "retained"
    source_root.mkdir()
    source_file = source_root / "Book.epub"
    source_file.write_bytes(b"replacement")
    original = import_activity_service.accept_book_targeted_release(
        source_key="prowlarr:review-source",
        source="prowlarr",
        source_metadata={},
        task_id="original-release",
        book_id=book_id,
    )
    import_activity_service.set_source_root(
        source_release_id=original["source_release_id"], source_root=source_root
    )
    member = import_activity_service.record_source_member(
        source_release_id=original["source_release_id"],
        relative_path="Book.epub",
        size=len(b"replacement"),
        file_format="epub",
        discovery_status="discovered",
    )
    import_activity_service.plan_import(
        activity_id=original["id"],
        storage_root=tmp_path / "books",
        selections=[{"source_member_id": member["id"], "evidence": {"match": "default"}}],
    )
    import_activity_service.complete(activity_id=original["id"])
    old_path = tmp_path / "old.epub"
    old_path.write_bytes(b"old")
    download_history_service.record_download(
        task_id="original-release",
        user_id=admin["id"],
        username="admin",
        request_id=None,
        source="prowlarr",
        source_display_name="Prowlarr",
        title="Book review-source",
        author="Author A",
        file_format=None,
        size=None,
        preview=None,
        content_type="ebook",
        origin="book",
        book_id=book_id,
        import_activity_id=original["id"],
    )
    download_history_service.finalize_download_files(
        task_id="original-release",
        final_status="complete",
        file_rows=[{"download_path": str(old_path), "format": "epub", "size": "3"}],
    )
    client = _authed_client(app, admin, is_admin=True)

    review = client.get(f"/api/library/books/{book_id}/releases/{original['id']}/review")
    replacement = client.post(
        f"/api/library/books/{book_id}/releases/{original['id']}/review",
        json={"member_ids": [member["id"]]},
    )

    assert review.status_code == 200
    assert review.json["members"] == [
        {
            "available": True,
            "evidence": {"match": "default"},
            "evidence_summary": "Previously selected",
            "format": "epub",
            "id": member["id"],
            "relative_path": "Book.epub",
            "size": len(b"replacement"),
        }
    ]
    assert replacement.status_code == 200, replacement.json
    files = library_service.get_files_on_disk(book_id)
    assert len(files) == 1
    assert Path(files[0]["download_path"]).read_bytes() == b"replacement"
    assert not old_path.exists()


def test_request_only_member_gets_existing_files_and_can_send_them_to_kindle(
    app, user_db, tmp_path
):
    owner = user_db.create_user(username="owner")
    requester = user_db.create_user(username="requester", library_capability="request-only")
    user_db.update_personal_preferences(requester["id"], kindle_address="reader@example.test")
    book_id = client_post_book(app, owner, "hardcover", "shared-book")
    epub_path = tmp_path / "shared.epub"
    epub_path.write_bytes(b"shared-book")
    history_id = _seed_history_row(
        user_db,
        task_id="shared-release",
        user_id=owner["id"],
        username="owner",
        book_id=book_id,
        fmt="epub",
        download_path=str(epub_path),
    )

    requester_client = _authed_client(app, requester)
    added = requester_client.post(
        "/api/library/books",
        json={"metadata_provider": "hardcover", "provider_book_id": "shared-book"},
    )
    detail = requester_client.get(f"/api/library/books/{book_id}")

    assert added.status_code == 200
    assert added.json["files_exist_globally"] is True
    assert detail.status_code == 200
    assert len(detail.json["files"]) == 1
    assert detail.json["files"][0]["history_id"] == history_id
    assert detail.json["files"][0]["downloadable_by_me"] is True

    downloaded = requester_client.get(
        f"/api/library/books/{book_id}/download?history_id={history_id}"
    )
    assert downloaded.status_code == 200
    assert downloaded.data == b"shared-book"

    with patch(
        "shelfmark.download.outputs.email.send_file_to_email", return_value="r***@example.test"
    ) as fake_send:
        sent = requester_client.post(f"/api/library/books/{book_id}/send-to-kindle")
        unlinked = requester_client.delete(f"/api/library/books/{book_id}/downloads/{history_id}")
        sent_after_unlink = requester_client.post(f"/api/library/books/{book_id}/send-to-kindle")

    assert sent.status_code == 200
    assert sent.json["status"] == "sent"
    assert unlinked.status_code == 403
    assert sent_after_unlink.status_code == 200
    assert fake_send.call_count == 2


def test_book_detail_reports_non_membership_for_cross_library_admin(app, user_db):
    owner = user_db.create_user(username="owner")
    admin = user_db.create_user(username="admin", role="admin")
    book_id = client_post_book(app, owner, "hardcover", "owner-book")

    response = _authed_client(app, admin, is_admin=True).get(f"/api/library/books/{book_id}")

    assert response.status_code == 200
    assert response.json["in_my_library"] is False


def test_book_detail_404_for_missing_book_id(app, user_db):
    alice = user_db.create_user(username="alice")
    resp = _authed_client(app, alice).get("/api/library/books/99999")
    assert resp.status_code == 404


def test_delete_book_scoped_to_own_library(app, user_db):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    book_id = client_post_book(app, alice, "hardcover", "1")

    # Bob can't delete from alice's library.
    bob_client = _authed_client(app, bob)
    resp = bob_client.delete(f"/api/library/books/{book_id}")
    assert resp.status_code == 404

    resp = _authed_client(app, alice).delete(f"/api/library/books/{book_id}")
    assert resp.status_code == 200
    assert resp.json["status"] == "removed"


def test_admin_add_and_remove_remain_scoped_to_own_library(app, user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    alice_book_id = client_post_book(app, alice, "hardcover", "alice-1")
    admin_client = _authed_client(app, admin, is_admin=True)
    add_response = admin_client.post(
        "/api/library/books",
        json={"metadata_provider": "hardcover", "provider_book_id": "admin-1"},
    )
    admin_book_id = int(add_response.json["book_id"])

    assert add_response.status_code == 200
    assert [book["book_id"] for book in admin_client.get("/api/library/books").json["books"]] == [
        admin_book_id
    ]
    assert admin_client.delete(f"/api/library/books/{alice_book_id}").status_code == 404
    assert admin_client.delete(f"/api/library/books/{admin_book_id}").status_code == 200


def test_final_member_removal_detaches_activity_and_removes_visibility(
    app, user_db, library_service
):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "final-member")
    history_id = _seed_history_row(
        user_db,
        task_id="final-member-release",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        fmt="epub",
        download_path="/tmp/retained.epub",
    )
    library_service.link_download_to_user(
        user_id=alice["id"], book_id=book_id, history_id=history_id
    )

    response = _authed_client(app, alice).delete(f"/api/library/books/{book_id}")

    assert response.status_code == 200
    assert library_service.get_book(book_id) is None
    history = library_service.get_download_history_row(history_id)
    assert history is not None
    assert history["book_id"] is None
    assert history["download_path"] == "/tmp/retained.epub"
    assert not library_service.download_linked_to_user(user_id=alice["id"], history_id=history_id)


def test_personal_removal_keeps_shared_book_files_and_other_member_visibility(
    app, user_db, library_service
):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    book_id = client_post_book(app, alice, "hardcover", "shared-removal")
    client_post_book(app, bob, "hardcover", "shared-removal")
    history_id = _seed_history_row(
        user_db,
        task_id="shared-removal-release",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        fmt="epub",
        download_path="/tmp/shared.epub",
    )
    library_service.link_download_to_user(user_id=bob["id"], book_id=book_id, history_id=history_id)

    response = _authed_client(app, alice).delete(f"/api/library/books/{book_id}")

    assert response.status_code == 200
    assert library_service.get_book(book_id) is not None
    assert library_service.get_download_history_row(history_id)["book_id"] == book_id
    assert library_service.download_linked_to_user(user_id=bob["id"], history_id=history_id)


def test_admin_purge_preview_is_protected_and_uses_display_name(app, user_db):
    alice = user_db.create_user(username="alice", display_name="Alice Reader")
    admin = user_db.create_user(username="admin", role="admin")
    book_id = client_post_book(app, alice, "hardcover", "purge-preview")

    assert (
        _authed_client(app, alice).get(f"/api/library/books/{book_id}/purge-preview").status_code
        == 403
    )
    assert (
        _authed_client(app, alice).delete(f"/api/library/books/{book_id}/purge").status_code == 403
    )
    response = _authed_client(app, admin, is_admin=True).get(
        f"/api/library/books/{book_id}/purge-preview"
    )

    assert response.status_code == 200
    assert response.json == {"users": [{"display_name": "Alice Reader", "username": "alice"}]}


def test_admin_purge_cancels_active_work_deletes_artifact_and_detaches_activity(
    app, user_db, library_service, tmp_path
):
    owner = user_db.create_user(username="owner")
    admin = user_db.create_user(username="admin", role="admin")
    book_id = client_post_book(app, owner, "hardcover", "purge-book")
    artifact = tmp_path / "books" / str(book_id) / "release" / "epub" / "purge.epub"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")
    history_id = _seed_history_row(
        user_db,
        task_id="purge-complete",
        user_id=owner["id"],
        username="owner",
        book_id=book_id,
        fmt="epub",
        download_path=str(artifact),
    )
    library_service.link_download_to_user(
        user_id=owner["id"], book_id=book_id, history_id=history_id
    )
    _seed_history_row(
        user_db,
        task_id="purge-active",
        user_id=owner["id"],
        username="owner",
        book_id=book_id,
        fmt="epub",
        download_path="/tmp/not-yet-created.epub",
        final_status="active",
    )

    response = _authed_client(app, admin, is_admin=True).delete(
        f"/api/library/books/{book_id}/purge"
    )

    assert response.status_code == 200
    assert not artifact.exists()
    assert not (tmp_path / "books" / str(book_id)).exists()
    assert app.extensions["cancelled_tasks"] == ["purge-active"]
    history = library_service.get_download_history_row(history_id)
    assert history is not None
    assert history["book_id"] is None
    assert history["download_path"] is None
    assert not library_service.download_linked_to_user(user_id=owner["id"], history_id=history_id)


def test_admin_purge_continues_when_active_download_is_already_unavailable(
    app, user_db, library_service
):
    owner = user_db.create_user(username="owner")
    admin = user_db.create_user(username="admin", role="admin")
    book_id = client_post_book(app, owner, "hardcover", "cancel-failure")
    _seed_history_row(
        user_db,
        task_id="cannot-cancel",
        user_id=owner["id"],
        username="owner",
        book_id=book_id,
        fmt="epub",
        download_path="/tmp/cannot-cancel.epub",
        final_status="active",
    )
    app.extensions["cancel_should_fail"] = True

    response = _authed_client(app, admin, is_admin=True).delete(
        f"/api/library/books/{book_id}/purge"
    )

    assert response.status_code == 200
    assert library_service.get_book(book_id) is None


def test_admin_purge_surfaces_artifact_cleanup_failures(app, user_db, library_service, tmp_path):
    owner = user_db.create_user(username="owner")
    admin = user_db.create_user(username="admin", role="admin")
    book_id = client_post_book(app, owner, "hardcover", "cleanup-failure")
    artifact_directory = tmp_path / "artifact-directory"
    artifact_directory.mkdir()
    _seed_history_row(
        user_db,
        task_id="cannot-unlink",
        user_id=owner["id"],
        username="owner",
        book_id=book_id,
        fmt="epub",
        download_path=str(artifact_directory),
    )

    response = _authed_client(app, admin, is_admin=True).delete(
        f"/api/library/books/{book_id}/purge"
    )

    assert response.status_code == 500
    assert library_service.get_book(book_id) is not None


def test_download_file_gates_on_library_membership(app, user_db, library_service, db_path):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    book_id = client_post_book(app, alice, "hardcover", "1")
    history_id = _seed_history_row(
        user_db,
        task_id="task-1",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        fmt="epub",
        download_path="/tmp/does-not-exist.epub",
    )
    library_service.link_download_to_user(
        user_id=alice["id"], book_id=book_id, history_id=history_id
    )

    # Bob is not in the library — 403 even if he owns the row.
    bob_client = _authed_client(app, bob)
    resp = bob_client.get(f"/api/library/books/{book_id}/download?format=epub")
    assert resp.status_code == 403


def test_download_file_returns_404_when_no_matching_format_on_disk(app, user_db):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")

    resp = _authed_client(app, alice).get(f"/api/library/books/{book_id}/download?format=pdf")
    assert resp.status_code == 404


def test_download_file_serves_exact_history_id(app, user_db, tmp_path):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")
    epub_path = tmp_path / "book.epub"
    pdf_path = tmp_path / "book.pdf"
    epub_path.write_bytes(b"epub")
    pdf_path.write_bytes(b"pdf")
    history_ids = _seed_multi_file_release(
        user_db,
        task_id="release-download",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        files=[("epub", str(epub_path)), ("pdf", str(pdf_path))],
    )

    resp = _authed_client(app, alice).get(
        f"/api/library/books/{book_id}/download?history_id={history_ids[1]}"
    )

    assert resp.status_code == 200
    assert resp.data == b"pdf"


def test_download_file_uses_book_metadata_for_attachment_name(app, user_db, tmp_path):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")
    uuid_path = tmp_path / "79dff41c-49b7-4f97-9f04-1a6515e8e964.epub"
    uuid_path.write_bytes(b"epub")
    _seed_history_row(
        user_db,
        task_id="prowlarr-completed-release",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        fmt="epub",
        download_path=str(uuid_path),
    )

    resp = _authed_client(app, alice).get(f"/api/library/books/{book_id}/download")

    assert resp.status_code == 200
    assert resp.data == b"epub"
    assert resp.headers["Content-Disposition"] == 'attachment; filename="Book 1 - Author A.epub"'


def test_download_file_uses_safe_deterministic_name_for_incomplete_metadata(app, user_db, tmp_path):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")
    epub_path = tmp_path / "d31b7cea-2f26-4956-8c24-5e03ab385ad4.epub"
    epub_path.write_bytes(b"epub")
    _seed_history_row(
        user_db,
        task_id="prowlarr-incomplete-book-metadata",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        fmt="epub",
        download_path=str(epub_path),
    )
    conn = user_db._connect()
    try:
        conn.execute("UPDATE books SET title = '', author = NULL WHERE id = ?", (book_id,))
        conn.commit()
    finally:
        conn.close()

    resp = _authed_client(app, alice).get(f"/api/library/books/{book_id}/download")

    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"] == 'attachment; filename="Book 1.epub"'


def test_download_file_rejects_history_id_from_another_book(app, user_db, tmp_path):
    alice = user_db.create_user(username="alice")
    first_book = client_post_book(app, alice, "hardcover", "1")
    second_book = client_post_book(app, alice, "hardcover", "2")
    file_path = tmp_path / "book.epub"
    file_path.write_bytes(b"epub")
    history_id = _seed_history_row(
        user_db,
        task_id="other-book-release",
        user_id=alice["id"],
        username="alice",
        book_id=second_book,
        fmt="epub",
        download_path=str(file_path),
    )

    resp = _authed_client(app, alice).get(
        f"/api/library/books/{first_book}/download?history_id={history_id}"
    )

    assert resp.status_code == 404


def test_send_to_kindle_fail_fast_no_compatible_file(app, user_db):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")

    resp = _authed_client(app, alice).post(f"/api/library/books/{book_id}/send-to-kindle")
    # No files on disk → 404 "No compatible file found" (sub-decision 16).
    assert resp.status_code == 404
    assert resp.json["error"] == "No compatible file found"


def test_send_to_kindle_400_when_personal_recipient_unset(app, user_db, library_service):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")
    history_id = _seed_history_row(
        user_db,
        task_id="task-1",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        fmt="epub",
        download_path="/tmp/enders.epub",
    )
    library_service.link_download_to_user(
        user_id=alice["id"], book_id=book_id, history_id=history_id
    )

    # No per-user recipient is configured, so the route must reject before SMTP.
    resp = _authed_client(app, alice).post(f"/api/library/books/{book_id}/send-to-kindle")
    assert resp.status_code == 400
    assert resp.json["error"] == "No email recipient configured"


def test_send_to_kindle_success_path(app, user_db, library_service, tmp_path):
    alice = user_db.create_user(username="alice")
    user_db.update_personal_preferences(alice["id"], kindle_address="reader@example.test")
    book_id = client_post_book(app, alice, "hardcover", "1")
    epub_path = tmp_path / "enders.epub"
    epub_path.write_bytes(b"epub-bytes")
    history_id = _seed_history_row(
        user_db,
        task_id="task-1",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        fmt="epub",
        download_path=str(epub_path),
    )
    library_service.link_download_to_user(
        user_id=alice["id"], book_id=book_id, history_id=history_id
    )
    alternate_path = tmp_path / "enders-alternate.epub"
    alternate_path.write_bytes(b"alternate-epub-bytes")
    alternate_history_id = _seed_history_row(
        user_db,
        task_id="task-2",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        fmt="epub",
        download_path=str(alternate_path),
    )

    # Also patch send_file_to_email so no real SMTP network call is made.
    with patch(
        "shelfmark.download.outputs.email.send_file_to_email",
        return_value="r***@example.test",
    ) as fake_send:
        resp = _authed_client(app, alice).post(
            f"/api/library/books/{book_id}/send-to-kindle",
            json={"history_id": alternate_history_id},
        )

    assert resp.status_code == 200
    assert resp.json["status"] == "sent"
    assert resp.json["recipient"] == "r***@example.test"
    assert resp.json["format"] == "epub"
    fake_send.assert_called_once()
    args, _kwargs = fake_send.call_args
    assert str(args[0]) == str(alternate_path)
    assert args[1] == "reader@example.test"


def test_link_download_endpoint_inserts_user_downloads_row(app, user_db, library_service):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")
    history_id = _seed_history_row(
        user_db,
        task_id="task-1",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        fmt="epub",
        download_path="/tmp/enders.epub",
    )

    resp = _authed_client(app, alice).post(f"/api/library/books/{book_id}/downloads/{history_id}")
    assert resp.status_code == 200
    assert resp.json["status"] == "linked"
    assert library_service.download_linked_to_user(user_id=alice["id"], history_id=history_id)


def test_link_download_404_for_history_under_different_book(app, user_db, library_service):
    alice = user_db.create_user(username="alice")
    book_a = client_post_book(app, alice, "hardcover", "A")
    book_b = client_post_book(app, alice, "hardcover", "B")
    history_id = _seed_history_row(
        user_db,
        task_id="task-1",
        user_id=alice["id"],
        username="alice",
        book_id=book_a,
        fmt="epub",
        download_path="/tmp/enders.epub",
    )

    # The history row is tied to book_a, but we try to link it to book_b → 404.
    resp = _authed_client(app, alice).post(f"/api/library/books/{book_b}/downloads/{history_id}")
    assert resp.status_code == 404


def test_only_admin_can_delete_release(app, user_db, library_service):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")
    history_id = _seed_history_row(
        user_db,
        task_id="task-1",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        fmt="epub",
        download_path="/tmp/enders.epub",
    )
    library_service.link_download_to_user(
        user_id=alice["id"], book_id=book_id, history_id=history_id
    )

    response = _authed_client(app, alice).delete(
        f"/api/library/books/{book_id}/downloads/{history_id}"
    )

    assert response.status_code == 403
    assert library_service.download_linked_to_user(user_id=alice["id"], history_id=history_id)


def test_admin_delete_release_removes_files_and_detaches_history(
    app, user_db, library_service, tmp_path
):
    admin = user_db.create_user(username="admin", role="admin")
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    book_id = client_post_book(app, alice, "hardcover", "1")
    paths = [
        tmp_path / "books" / str(book_id) / "release-delete" / "epub" / "release.epub",
        tmp_path / "books" / str(book_id) / "release-delete" / "pdf" / "release.pdf",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"release")
    history_ids = _seed_multi_file_release(
        user_db,
        task_id="release-delete",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        files=[("epub", str(paths[0])), ("pdf", str(paths[1]))],
    )
    for user in (alice, bob):
        for history_id in history_ids:
            library_service.link_download_to_user(
                user_id=user["id"], book_id=book_id, history_id=history_id
            )

    response = _authed_client(app, admin, is_admin=True).delete(
        f"/api/library/books/{book_id}/downloads/{history_ids[0]}"
    )

    assert response.status_code == 200
    assert response.json["status"] == "deleted"
    assert app.extensions["cleared_completed_tasks"] == ["release-delete"]
    assert all(not path.exists() for path in paths)
    assert not (tmp_path / "books" / str(book_id)).exists()
    for history_id in history_ids:
        row = library_service.get_download_history_row(history_id)
        assert row is not None
        assert row["book_id"] is None
        assert row["download_path"] is None
        assert not library_service.download_linked_to_user(
            user_id=alice["id"], history_id=history_id
        )
        assert not library_service.download_linked_to_user(user_id=bob["id"], history_id=history_id)
    assert _authed_client(app, alice).get(f"/api/library/books/{book_id}").json["files"] == []


def test_admin_cannot_delete_in_flight_release(app, user_db, library_service):
    admin = user_db.create_user(username="admin", role="admin")
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")
    conn = user_db._connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO download_history (
                task_id, user_id, source, title, content_type, origin, final_status, book_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "release-active",
                alice["id"],
                "prowlarr",
                "Active",
                "ebook",
                "direct",
                "active",
                book_id,
            ),
        )
        history_id = int(cursor.lastrowid or 0)
        conn.commit()
    finally:
        conn.close()

    response = _authed_client(app, admin, is_admin=True).delete(
        f"/api/library/books/{book_id}/downloads/{history_id}"
    )

    assert response.status_code == 409
    assert library_service.get_download_history_row(history_id)["book_id"] == book_id


def test_unauthenticated_user_gets_401_on_all_routes(app, user_db):
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")

    for method, path in [
        ("get", "/api/library/books"),
        ("get", f"/api/library/books/{book_id}"),
        ("delete", f"/api/library/books/{book_id}"),
        ("get", f"/api/library/books/{book_id}/download?format=epub"),
        ("post", f"/api/library/books/{book_id}/send-to-kindle"),
    ]:
        client = app.test_client()
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, f"{method.upper()} {path} → {resp.status_code}"


def _seed_multi_file_release(
    user_db: UserDB,
    *,
    task_id: str,
    user_id: int,
    username: str,
    book_id: int,
    files: list[tuple[str, str]],
) -> list[int]:
    """Seed N download_history rows sharing a task_id (#13 schema (b))."""
    conn = user_db._connect()
    try:
        history_ids: list[int] = []
        for i, (fmt, path) in enumerate(files):
            cur = conn.execute(
                """
                INSERT INTO download_history (
                    task_id, user_id, username, source, title, format, content_type,
                    origin, final_status, download_path, terminal_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    user_id,
                    username,
                    "prowlarr",
                    "Multi",
                    fmt,
                    "ebook",
                    "direct",
                    "complete",
                    path,
                    f"2026-01-0{i + 1}T00:00:00+00:00",
                ),
            )
            hid = int(cur.lastrowid or 0)
            history_ids.append(hid)
            conn.execute(
                "UPDATE download_history SET book_id = ? WHERE id = ?",
                (book_id, hid),
            )
        conn.commit()
        return history_ids
    finally:
        conn.close()


def test_book_detail_includes_task_id_per_file_in_payload(app, user_db):
    """#13 API (3-a): files[] stays flat, one entry per download_history row,
    with task_id added per entry — frontend groups by task_id for display."""
    alice = user_db.create_user(username="alice")
    book_id = client_post_book(app, alice, "hardcover", "1")
    _seed_multi_file_release(
        user_db,
        task_id="release-A",
        user_id=alice["id"],
        username="alice",
        book_id=book_id,
        files=[
            ("epub", "/lib/a.epub"),
            ("mobi", "/lib/a.mobi"),
            ("pdf", "/lib/a.pdf"),
        ],
    )

    detail = _authed_client(app, alice).get(f"/api/library/books/{book_id}").json
    assert len(detail["files"]) == 3
    # Every file entry carries task_id (the release grouping key).
    assert {f["task_id"] for f in detail["files"]} == {"release-A"}
    assert {f["format"] for f in detail["files"]} == {"epub", "mobi", "pdf"}
    assert all(f["downloadable_by_me"] is True for f in detail["files"])


# --- Helpers ------------------------------------------------------------- #


def client_post_book(app: Flask, user: dict, provider: str, provider_book_id: str) -> int:
    client = _authed_client(app, user)
    resp = client.post(
        "/api/library/books",
        json={"metadata_provider": provider, "provider_book_id": provider_book_id},
    )
    assert resp.status_code == 200, resp.json
    return int(resp.json["book_id"])


def _first_book_id_for_user(app: Flask, user: dict) -> int:
    resp = _authed_client(app, user).get("/api/library/books")
    assert resp.status_code == 200
    return int(resp.json["books"][0]["book_id"])
