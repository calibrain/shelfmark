"""Tests for the library service data layer (#06)."""

from __future__ import annotations

import os
import tempfile

import pytest

from shelfmark.core.library_service import LibraryService
from shelfmark.core.user_db import UserDB


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
def library_service(user_db, db_path):
    """LibraryService bound to an initialized user_db (schema must exist)."""
    return LibraryService(db_path)


def _insert_book(
    service: LibraryService,
    *,
    provider: str = "hardcover",
    provider_book_id: str = "42",
    title: str = "Ender's Game",
    author: str | None = "Orson Scott Card",
    metadata_json: dict | None = None,
) -> dict:
    return service.upsert_book_from_metadata(
        metadata_provider=provider,
        provider_book_id=provider_book_id,
        title=title,
        author=author,
        subtitle=None,
        publish_year=1985,
        isbn_13="978-0-812-54040-1",
        cover_url=None,
        series_name=None,
        series_position=None,
        language="en",
        metadata_json=metadata_json or {"raw": "payload"},
    )


def test_upsert_is_idempotent_on_provider_natural_key(library_service):
    first = _insert_book(library_service)
    second = _insert_book(library_service)
    assert first["id"] == second["id"]
    assert first["title"] == second["title"]


def test_upsert_distinct_provider_book_id_creates_separate_rows(library_service):
    first = _insert_book(library_service, provider_book_id="1")
    second = _insert_book(library_service, provider_book_id="2")
    assert first["id"] != second["id"]


def test_add_to_library_is_idempotent(library_service, user_db):
    user = user_db.create_user(username="alice")
    book = _insert_book(library_service)

    newly_linked = library_service.add_to_library(user_id=user["id"], book_id=book["id"])
    repeat_link = library_service.add_to_library(user_id=user["id"], book_id=book["id"])
    assert newly_linked is True
    assert repeat_link is False
    assert library_service.is_in_library(user_id=user["id"], book_id=book["id"])


def test_add_to_library_links_existing_completed_files_only_when_membership_is_new(
    library_service, user_db
):
    owner = user_db.create_user(username="owner")
    member = user_db.create_user(username="member")
    book = _insert_book(library_service)
    conn = user_db._connect()
    try:
        for task_id, download_path in [("release-a", "/lib/a.epub"), ("release-b", "/lib/b.pdf")]:
            conn.execute(
                """
                INSERT INTO download_history (
                    task_id, user_id, source, title, origin, final_status, download_path, book_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    owner["id"],
                    "direct_download",
                    "Example",
                    "direct",
                    "complete",
                    download_path,
                    book["id"],
                ),
            )
        conn.execute(
            """
            INSERT INTO download_history (
                task_id, user_id, source, title, origin, final_status, book_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "in-flight",
                owner["id"],
                "direct_download",
                "Example",
                "direct",
                "active",
                book["id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert library_service.add_to_library(user_id=member["id"], book_id=book["id"]) is True

    files = library_service.get_files_on_disk(book["id"])
    assert all(
        library_service.download_linked_to_user(user_id=member["id"], history_id=file["id"])
        for file in files
    )

    assert (
        library_service.unlink_download_from_user(
            user_id=member["id"], book_id=book["id"], history_id=files[0]["id"]
        )
        is True
    )
    assert library_service.add_to_library(user_id=member["id"], book_id=book["id"]) is False
    assert not library_service.download_linked_to_user(
        user_id=member["id"], history_id=files[0]["id"]
    )

    assert library_service.remove_from_library(user_id=member["id"], book_id=book["id"]) is True
    assert library_service.add_to_library(user_id=member["id"], book_id=book["id"]) is True
    assert all(
        library_service.download_linked_to_user(user_id=member["id"], history_id=file["id"])
        for file in files
    )


def test_remove_from_library_hard_deletes_membership(library_service, user_db):
    user = user_db.create_user(username="alice")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=user["id"], book_id=book["id"])

    removed = library_service.remove_from_library(user_id=user["id"], book_id=book["id"])
    repeat = library_service.remove_from_library(user_id=user["id"], book_id=book["id"])

    assert removed is True
    assert repeat is False
    assert not library_service.is_in_library(user_id=user["id"], book_id=book["id"])


def test_list_library_books_admin_sees_all_others_see_own(library_service, user_db):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    book_a = _insert_book(library_service, provider_book_id="A", title="Alpha")
    book_b = _insert_book(library_service, provider_book_id="B", title="Beta")
    library_service.add_to_library(user_id=alice["id"], book_id=book_a["id"])
    library_service.add_to_library(user_id=bob["id"], book_id=book_b["id"])

    alice_view = library_service.list_library_books(user_id=alice["id"], is_admin=False)
    bob_view = library_service.list_library_books(user_id=bob["id"], is_admin=False)
    admin_view = library_service.list_library_books(user_id=None, is_admin=True)

    assert [b["provider_book_id"] for b in alice_view] == ["A"]
    assert [b["provider_book_id"] for b in bob_view] == ["B"]
    assert sorted(b["provider_book_id"] for b in admin_view) == ["A", "B"]


def test_list_library_books_fuzzy_query_matches_title_or_author(library_service, user_db):
    alice = user_db.create_user(username="alice")
    book_a = _insert_book(
        library_service, provider_book_id="A", title="Ender's Game", author="Card"
    )
    book_b = _insert_book(library_service, provider_book_id="B", title="Dune", author="Herbert")
    library_service.add_to_library(user_id=alice["id"], book_id=book_a["id"])
    library_service.add_to_library(user_id=alice["id"], book_id=book_b["id"])

    matches = library_service.list_library_books(user_id=alice["id"], is_admin=False, query="ender")
    assert [b["provider_book_id"] for b in matches] == ["A"]

    author_matches = library_service.list_library_books(
        user_id=alice["id"], is_admin=False, query="herbert"
    )
    assert [b["provider_book_id"] for b in author_matches] == ["B"]


def test_files_on_disk_returns_complete_rows_globally(library_service, user_db, db_path):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    # Seed download_history + user_downloads directly.
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
                "task-1",
                bob["id"],
                "bob",
                "direct_download",
                "Ender's Game",
                "epub",
                "ebook",
                "direct",
                "complete",
                "/tmp/enders.epub",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        history_id = conn.execute(
            "SELECT id FROM download_history WHERE task_id = ?", ("task-1",)
        ).fetchone()["id"]
        conn.execute(
            "UPDATE download_history SET book_id = ? WHERE id = ?",
            (book["id"], history_id),
        )
        conn.commit()
    finally:
        conn.close()

    files = library_service.get_files_on_disk(book["id"])
    assert len(files) == 1
    assert files[0]["format"] == "epub"
    assert library_service.files_exist_globally(book["id"]) is True
    assert library_service.in_flight_globally(book["id"]) is False


def test_in_flight_globally_detects_active_rows(library_service, db_path, user_db):
    alice = user_db.create_user(username="alice")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    conn = user_db._connect()
    try:
        conn.execute(
            """
            INSERT INTO download_history (
                task_id, user_id, source, title, format, content_type,
                origin, final_status, terminal_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-active",
                alice["id"],
                "direct_download",
                "Ender's Game",
                "epub",
                "ebook",
                "direct",
                "active",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        history_id = conn.execute(
            "SELECT id FROM download_history WHERE task_id = ?", ("task-active",)
        ).fetchone()["id"]
        conn.execute(
            "UPDATE download_history SET book_id = ? WHERE id = ?",
            (book["id"], history_id),
        )
        conn.commit()
    finally:
        conn.close()

    in_flight = library_service.get_in_flight_files(book["id"])
    assert len(in_flight) == 1
    assert library_service.in_flight_globally(book["id"]) is True
    # Active rows don't count as files-on-disk, per #04 sub-decision 12.
    assert library_service.files_exist_globally(book["id"]) is False


def test_release_library_states_are_batched_by_task_id(library_service, user_db):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    conn = user_db._connect()
    try:
        conn.execute(
            """
            INSERT INTO download_history (
                task_id, user_id, source, title, origin, final_status, download_path, book_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "complete-task",
                bob["id"],
                "direct_download",
                "Example",
                "direct",
                "complete",
                "/tmp/example.epub",
                book["id"],
            ),
        )
        conn.execute(
            """
            INSERT INTO download_history (
                task_id, user_id, source, title, origin, final_status, download_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("missing-task", bob["id"], "direct_download", "Other", "direct", "complete", None),
        )
        conn.commit()
    finally:
        conn.close()

    states = library_service.get_release_library_states(
        task_ids=["complete-task", "missing-task"], user_id=alice["id"]
    )

    assert states["complete-task"] == {
        "is_on_disk": True,
        "book_id": book["id"],
        "in_my_library": True,
    }
    assert states["missing-task"] == {
        "is_on_disk": False,
        "book_id": None,
        "in_my_library": False,
    }


def test_metadata_library_states_match_provider_natural_keys(library_service, user_db):
    alice = user_db.create_user(username="alice")
    book = _insert_book(library_service, provider_book_id="provider-42")
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    states = library_service.get_metadata_library_states(
        book_keys=[("hardcover", "provider-42"), ("hardcover", "not-added")],
        user_id=alice["id"],
    )

    assert states == {("hardcover", "provider-42"): {"book_id": book["id"], "in_my_library": True}}


def test_link_and_unlink_download_idempotent(library_service, user_db, db_path):
    alice = user_db.create_user(username="alice")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    conn = user_db._connect()
    try:
        conn.execute(
            """
            INSERT INTO download_history (
                task_id, user_id, source, title, format, content_type,
                origin, final_status, download_path, terminal_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-1",
                alice["id"],
                "direct_download",
                "Ender's Game",
                "epub",
                "ebook",
                "direct",
                "complete",
                "/tmp/enders.epub",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        history_id = conn.execute(
            "SELECT id FROM download_history WHERE task_id = ?", ("task-1",)
        ).fetchone()["id"]
        conn.execute(
            "UPDATE download_history SET book_id = ? WHERE id = ?",
            (book["id"], history_id),
        )
        conn.commit()
    finally:
        conn.close()

    newly_linked = library_service.link_download_to_user(
        user_id=alice["id"], book_id=book["id"], history_id=history_id
    )
    repeat = library_service.link_download_to_user(
        user_id=alice["id"], book_id=book["id"], history_id=history_id
    )
    assert newly_linked is True
    assert repeat is False
    assert library_service.download_linked_to_user(user_id=alice["id"], history_id=history_id)

    removed = library_service.unlink_download_from_user(
        user_id=alice["id"], book_id=book["id"], history_id=history_id
    )
    assert removed is True
    assert not library_service.download_linked_to_user(user_id=alice["id"], history_id=history_id)

    # download_history row is untouched by unlinking (#04 sub-decision 3).
    row = library_service.get_download_history_row(history_id)
    assert row is not None
    assert row["task_id"] == "task-1"


def test_resolve_kindle_format_uses_priority_list(library_service, user_db, db_path):
    alice = user_db.create_user(username="alice")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    conn = user_db._connect()
    try:
        conn.execute(
            """
            INSERT INTO download_history (
                task_id, user_id, source, title, format, content_type,
                origin, final_status, download_path, terminal_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?),
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-mobi",
                alice["id"],
                "direct_download",
                "Ender's Game",
                "mobi",
                "ebook",
                "direct",
                "complete",
                "/tmp/enders.mobi",
                "2026-01-01T00:00:00+00:00",
                "task-epub",
                alice["id"],
                "direct_download",
                "Ender's Game",
                "epub",
                "ebook",
                "direct",
                "complete",
                "/tmp/enders.epub",
                "2026-01-02T00:00:00+00:00",
            ),
        )
        book_ids = conn.execute(
            "SELECT id FROM download_history WHERE task_id IN (?, ?)", ("task-mobi", "task-epub")
        ).fetchall()
        for row in book_ids:
            conn.execute(
                "UPDATE download_history SET book_id = ? WHERE id = ?",
                (book["id"], row["id"]),
            )
        conn.commit()
    finally:
        conn.close()

    # Default: epub wins (Kindle priority), even though mobi is on disk too.
    resolved_default = library_service.resolve_kindle_format(book_id=book["id"])
    assert resolved_default is not None
    assert resolved_default["format"] == "epub"

    # Explicit override handles any format on disk.
    resolved_override = library_service.resolve_kindle_format(
        book_id=book["id"], requested_format="mobi"
    )
    assert resolved_override is not None
    assert resolved_override["format"] == "mobi"

    # Unavailable format → None → route returns 404.
    resolved_missing = library_service.resolve_kindle_format(
        book_id=book["id"], requested_format="pdf"
    )
    assert resolved_missing is None


def test_resolve_kindle_format_returns_none_when_no_files(library_service, user_db):
    alice = user_db.create_user(username="alice")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    assert library_service.resolve_kindle_format(book_id=book["id"]) is None


def test_get_files_on_disk_returns_one_row_per_file_with_task_id(library_service, user_db):
    """#13 schema (b): a multi-file release spans N download_history rows
    sharing a task_id; get_files_on_disk returns one row per file, each
    carrying task_id for frontend grouping per #13 API (3-a)."""
    alice = user_db.create_user(username="alice")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    conn = user_db._connect()
    try:
        # Three file rows sharing task_id 'release-A' (one download → 3 files).
        for i, (fmt, path) in enumerate(
            [("epub", "/lib/a.epub"), ("mobi", "/lib/a.mobi"), ("pdf", "/lib/a.pdf")]
        ):
            conn.execute(
                """
                INSERT INTO download_history (
                    task_id, user_id, source, title, format, content_type,
                    origin, final_status, download_path, terminal_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "release-A",
                    alice["id"],
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
        conn.execute(
            "UPDATE download_history SET book_id = ? WHERE task_id = ?",
            (book["id"], "release-A"),
        )
        conn.commit()
    finally:
        conn.close()

    files = library_service.get_files_on_disk(book["id"])
    assert len(files) == 3
    # All three rows share the task_id (release grouping key).
    assert {f["task_id"] for f in files} == {"release-A"}
    assert {f["format"] for f in files} == {"epub", "mobi", "pdf"}


def test_unlink_download_fans_out_across_sibling_file_rows_sharing_task_id(
    library_service, user_db, db_path
):
    """#13 unlink (4-a-strict): DELETE /downloads/:history_id fans out across
    every sibling file row in the release, deleting user_downloads links for
    all of them for the requesting user (release-atomic)."""
    alice = user_db.create_user(username="alice")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        history_ids = []
        for i, (fmt, path) in enumerate(
            [("epub", "/lib/a.epub"), ("mobi", "/lib/a.mobi"), ("pdf", "/lib/a.pdf")]
        ):
            cur = conn.execute(
                """
                INSERT INTO download_history (
                    task_id, user_id, source, title, format, content_type,
                    origin, final_status, download_path, terminal_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "release-unlink",
                    alice["id"],
                    "prowlarr",
                    "Unlink Fanout",
                    fmt,
                    "ebook",
                    "direct",
                    "complete",
                    path,
                    f"2026-01-0{i + 1}T00:00:00+00:00",
                ),
            )
            history_ids.append(cur.lastrowid)
        conn.execute(
            "UPDATE download_history SET book_id = ? WHERE task_id = ?",
            (book["id"], "release-unlink"),
        )
        # Link all three file rows to alice.
        for hid in history_ids:
            conn.execute(
                "INSERT INTO user_downloads (user_id, history_id, added_at) VALUES (?, ?, ?)",
                (alice["id"], hid, "2026-01-01T00:00:00+00:00"),
            )
        conn.commit()
    finally:
        conn.close()

    # Unlink the MIDDLE file row's history_id — should fan out to all three.
    middle_id = history_ids[1]
    removed = library_service.unlink_download_from_user(
        user_id=alice["id"], book_id=book["id"], history_id=middle_id
    )
    assert removed is True

    # All three links are gone (release-atomic).
    for hid in history_ids:
        assert not library_service.download_linked_to_user(user_id=alice["id"], history_id=hid), (
            f"link for history_id={hid} should be gone"
        )

    # download_history rows are untouched (file + row preserved per #04 sub-decision 7).
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM download_history WHERE task_id = ?",
            ("release-unlink",),
        ).fetchone()
        assert remaining["n"] == 3, "download_history rows must survive unlink"
    finally:
        conn.close()


def test_unlink_mid_flight_returns_false_when_release_has_no_links_yet(
    library_service, user_db, db_path
):
    """#13 link-timing (4-mid-1-ii): user_downloads links are inserted at
    finalize only. An in-flight release (sentinel 'active' row, no links) has
    nothing to unlink — unlink returns False (route surfaces as 404 — no row
    to delete), preserving #08's 'Unlink disabled while in-flight' UX via a
    different mechanism."""
    alice = user_db.create_user(username="alice")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO download_history (
                task_id, user_id, source, title, content_type,
                origin, final_status, terminal_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "release-inflight",
                alice["id"],
                "prowlarr",
                "In Flight",
                "ebook",
                "direct",
                "active",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        sentinel_id = cur.lastrowid
        conn.execute(
            "UPDATE download_history SET book_id = ? WHERE id = ?",
            (book["id"], sentinel_id),
        )
        conn.commit()
    finally:
        conn.close()

    # No user_downloads link exists for the sentinel — unlink is a no-op.
    removed = library_service.unlink_download_from_user(
        user_id=alice["id"], book_id=book["id"], history_id=sentinel_id
    )
    assert removed is False


def test_unlink_unknown_history_id_returns_false(library_service, user_db):
    """A history_id that doesn't exist has no task_id to fan out from —
    unlink is a no-op (surfaces as 404 in the route)."""
    alice = user_db.create_user(username="alice")
    book = _insert_book(library_service)
    library_service.add_to_library(user_id=alice["id"], book_id=book["id"])

    removed = library_service.unlink_download_from_user(
        user_id=alice["id"], book_id=book["id"], history_id=999_999
    )
    assert removed is False
