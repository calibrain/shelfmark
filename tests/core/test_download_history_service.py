"""Tests for persisted download-history helpers."""

from __future__ import annotations

import os
import tempfile

from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.user_db import UserDB


def test_iso_to_epoch_treats_naive_sqlite_timestamp_as_utc():
    epoch = DownloadHistoryService._iso_to_epoch("2026-01-02 03:04:05")
    assert epoch == 1767323045.0


def test_record_download_stores_utc_iso_timestamps():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = DownloadHistoryService(db_path)

        service.record_download(
            task_id="task-1",
            user_id=None,
            username=None,
            request_id=None,
            source="direct_download",
            source_display_name="Direct Download",
            title="Example",
            author=None,
            file_format=None,
            size=None,
            preview=None,
            content_type="ebook",
            origin="direct",
        )

        conn = user_db._connect()
        try:
            row = conn.execute(
                "SELECT queued_at, terminal_at FROM download_history WHERE task_id = ?",
                ("task-1",),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert "+00:00" in row["queued_at"]
        assert "+00:00" in row["terminal_at"]


def test_finalize_download_files_preserves_library_book_id():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = DownloadHistoryService(db_path)

        conn = user_db._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO books (metadata_provider, provider_book_id, title, metadata_json) VALUES (?, ?, ?, ?)",
                ("hardcover", "42", "Example", "{}"),
            )
            conn.commit()
            book_id = cursor.lastrowid
        finally:
            conn.close()

        service.record_download(
            task_id="task-library-book",
            user_id=None,
            username=None,
            request_id=None,
            source="direct_download",
            source_display_name="Direct Download",
            title="Example",
            author=None,
            file_format=None,
            size=None,
            preview=None,
            content_type="ebook",
            origin="direct",
            book_id=book_id,
        )
        service.finalize_download_files(
            task_id="task-library-book",
            final_status="complete",
            file_rows=[{"download_path": "/tmp/example.epub", "format": "epub", "size": "1"}],
        )

        assert _fetch_rows(db_path, "task-library-book")[0]["book_id"] == book_id


def test_finalize_download_files_fulfils_pending_book_requests_and_links_each_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        alice = user_db.create_user(username="alice", password_hash="x")
        bob = user_db.create_user(username="bob", password_hash="x")
        service = DownloadHistoryService(db_path)

        conn = user_db._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO books (metadata_provider, provider_book_id, title, metadata_json) "
                "VALUES (?, ?, ?, ?)",
                ("hardcover", "42", "Example", "{}"),
            )
            conn.commit()
            book_id = int(cursor.lastrowid)
        finally:
            conn.close()
        conn = user_db._connect()
        try:
            conn.executemany(
                "INSERT INTO user_library (user_id, book_id) VALUES (?, ?)",
                [(alice["id"], book_id), (bob["id"], book_id)],
            )
            conn.commit()
        finally:
            conn.close()
        alice_request = user_db.create_library_request(user_id=alice["id"], book_id=book_id)
        bob_request = user_db.create_library_request(user_id=bob["id"], book_id=book_id)
        user_db.update_request(bob_request["id"], status="cancelled")

        service.record_download(
            task_id="shared-book",
            user_id=None,
            username=None,
            request_id=None,
            source="direct_download",
            source_display_name="Direct Download",
            title="Example",
            author=None,
            file_format=None,
            size=None,
            preview=None,
            content_type="ebook",
            origin="direct",
            book_id=book_id,
        )
        service.finalize_download_files(
            task_id="shared-book",
            final_status="complete",
            file_rows=[
                {"download_path": "/tmp/example.epub", "format": "epub", "size": "1"},
                {"download_path": "/tmp/example.pdf", "format": "pdf", "size": "2"},
            ],
        )

        assert user_db.get_request(alice_request["id"])["status"] == "fulfilled"
        assert user_db.get_request(bob_request["id"])["status"] == "cancelled"
        links = _fetch_links(db_path)
        assert {(link["user_id"], link["history_id"]) for link in links} == {
            (alice["id"], row["id"]) for row in _fetch_rows(db_path, "shared-book")
        }


def _setup_service(tmpdir: str) -> tuple[str, UserDB, DownloadHistoryService, int]:
    """Shared fixture: initialized db + service + one user id."""
    db_path = os.path.join(tmpdir, "users.db")
    user_db = UserDB(db_path)
    user_db.initialize()
    user_db.create_user(username="alice", password_hash="x")
    service = DownloadHistoryService(db_path)
    return db_path, user_db, service, 1


def _fetch_rows(db_path: str, task_id: str) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM download_history WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


def _fetch_links(db_path: str) -> list[dict]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute("SELECT * FROM user_downloads ORDER BY history_id").fetchall()
        ]
    finally:
        conn.close()


def test_record_download_writes_per_file_cols_as_null_sentinel():
    with tempfile.TemporaryDirectory() as tmpdir:
        _, _, service, user_id = _setup_service(tmpdir)

        service.record_download(
            task_id="sentinel-1",
            user_id=user_id,
            username="alice",
            request_id=None,
            source="prowlarr",
            source_display_name="Prowlarr",
            title="Sentinel Test",
            author="Author",
            file_format="epub",
            size="1MB",
            preview=None,
            content_type="book",
            origin="direct",
        )

        rows = _fetch_rows(os.path.join(tmpdir, "users.db"), "sentinel-1")
        assert len(rows) == 1
        sentinel = rows[0]
        assert sentinel["final_status"] == "active"
        # Per #13: per-file columns are NULL on the sentinel.
        assert sentinel["format"] is None
        assert sentinel["size"] is None
        assert sentinel["download_path"] is None
        # Queue-time metadata is preserved.
        assert sentinel["title"] == "Sentinel Test"
        assert sentinel["source"] == "prowlarr"


def test_finalize_download_files_inserts_one_row_per_file_plus_user_downloads_links():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        _, _, service, user_id = _setup_service(tmpdir)

        service.record_download(
            task_id="multi-1",
            user_id=user_id,
            username="alice",
            request_id=None,
            source="prowlarr",
            source_display_name="Prowlarr",
            title="Multi File",
            author="Author",
            file_format=None,
            size=None,
            preview=None,
            content_type="book",
            origin="direct",
        )

        service.finalize_download_files(
            task_id="multi-1",
            final_status="complete",
            status_message=None,
            file_rows=[
                {"download_path": "/lib/a.epub", "format": "epub", "size": "1KB"},
                {"download_path": "/lib/a.mobi", "format": "mobi", "size": "2KB"},
                {"download_path": "/lib/a.pdf", "format": "pdf", "size": "3KB"},
            ],
        )

        rows = _fetch_rows(db_path, "multi-1")
        assert len(rows) == 3
        assert {r["format"] for r in rows} == {"epub", "mobi", "pdf"}
        assert {r["download_path"] for r in rows} == {
            "/lib/a.epub",
            "/lib/a.mobi",
            "/lib/a.pdf",
        }
        assert all(r["final_status"] == "complete" for r in rows)
        # Sentinel is gone — replaced by N file rows.
        assert all(r["final_status"] != "active" for r in rows)
        # One user_downloads link per file row, for the triggering user.
        links = _fetch_links(db_path)
        assert len(links) == 3
        assert all(l["user_id"] == user_id for l in links)


def test_finalize_download_files_retry_while_partial_clears_stray_rows_before_sentinel_insert():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        _, _, service, user_id = _setup_service(tmpdir)

        service.record_download(
            task_id="retry-1",
            user_id=user_id,
            username="alice",
            request_id=None,
            source="prowlarr",
            source_display_name="Prowlarr",
            title="Retry",
            author="Author",
            file_format=None,
            size=None,
            preview=None,
            content_type="book",
            origin="direct",
        )

        # Simulate a crashed finalize that left a stray partial file row.
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO download_history (task_id, user_id, source, title, "
            "final_status, download_path, format) "
            "VALUES ('retry-1', ?, 'prowlarr', 'Retry', 'active', '/lib/partial.epub', 'epub')",
            (user_id,),
        )
        conn.commit()
        conn.close()

        # Retry: record_download should DELETE all rows for this task_id
        # (sentinel + stray partial) and insert a fresh sentinel.
        service.record_download(
            task_id="retry-1",
            user_id=user_id,
            username="alice",
            request_id=None,
            source="prowlarr",
            source_display_name="Prowlarr",
            title="Retry",
            author="Author",
            file_format=None,
            size=None,
            preview=None,
            content_type="book",
            origin="direct",
        )

        rows = _fetch_rows(db_path, "retry-1")
        assert len(rows) == 1, f"retry should leave exactly one sentinel, got {len(rows)}"
        assert rows[0]["final_status"] == "active"
        assert rows[0]["format"] is None
        assert rows[0]["download_path"] is None


def test_finalize_download_legacy_delegate_single_path_preserves_call_signature():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        _, _, service, user_id = _setup_service(tmpdir)

        service.record_download(
            task_id="legacy-1",
            user_id=user_id,
            username="alice",
            request_id=None,
            source="prowlarr",
            source_display_name="Prowlarr",
            title="Legacy",
            author="Author",
            file_format=None,
            size=None,
            preview=None,
            content_type="book",
            origin="direct",
        )

        # Legacy single-path finalize delegates to finalize_download_files
        # with a one-element list.
        service.finalize_download(
            task_id="legacy-1",
            final_status="complete",
            download_path="/lib/single.epub",
        )

        rows = _fetch_rows(db_path, "legacy-1")
        assert len(rows) == 1
        assert rows[0]["final_status"] == "complete"
        assert rows[0]["download_path"] == "/lib/single.epub"
        # One link for the single file row.
        links = _fetch_links(db_path)
        assert len(links) == 1


def test_finalize_download_files_no_file_rows_keeps_sentinel_as_terminal_in_place():
    """Error/cancelled finalize with no paths must preserve the sentinel row
    so activity/retry lookups by task_id still resolve (pre-#13 behaviour)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        _, _, service, user_id = _setup_service(tmpdir)

        service.record_download(
            task_id="error-1",
            user_id=user_id,
            username="alice",
            request_id=None,
            source="prowlarr",
            source_display_name="Prowlarr",
            title="Errored",
            author="Author",
            file_format=None,
            size=None,
            preview=None,
            content_type="book",
            origin="direct",
        )

        service.finalize_download_files(
            task_id="error-1",
            final_status="error",
            status_message="Download failed",
            file_rows=[],
        )

        rows = _fetch_rows(db_path, "error-1")
        assert len(rows) == 1, "error finalize with no paths must preserve the sentinel"
        assert rows[0]["final_status"] == "error"
        assert rows[0]["status_message"] == "Download failed"
        # No user_downloads links for an error finalize.
        assert _fetch_links(db_path) == []


def test_legacy_single_file_row_is_well_formed_one_member_release_group():
    """A pre-#13 single-file row trivially forms a one-member release group:
    it's a single download_history row whose task_id is its own group key."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        _, _, service, user_id = _setup_service(tmpdir)

        service.record_download(
            task_id="legacy-single-1",
            user_id=user_id,
            username="alice",
            request_id=None,
            source="direct_download",
            source_display_name="Direct",
            title="Legacy Single",
            author="Author",
            file_format=None,
            size=None,
            preview=None,
            content_type="book",
            origin="direct",
        )
        service.finalize_download(
            task_id="legacy-single-1",
            final_status="complete",
            download_path="/lib/legacy.epub",
        )

        rows = _fetch_rows(db_path, "legacy-single-1")
        assert len(rows) == 1
        assert rows[0]["task_id"] == "legacy-single-1"
        # get_by_task_id still resolves (returns the first row for the task_id).
        fetched = service.get_by_task_id("legacy-single-1")
        assert fetched is not None
        assert fetched["download_path"] == "/lib/legacy.epub"
