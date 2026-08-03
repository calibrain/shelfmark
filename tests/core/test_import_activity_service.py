"""Tests for durable source releases and Book import activities."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.import_activity_service import ImportActivityService
from shelfmark.core.library_service import LibraryService
from shelfmark.core.user_db import UserDB


def _create_book(user_db: UserDB, provider_id: str, title: str) -> int:
    conn = user_db._connect()
    try:
        cursor = conn.execute(
            "INSERT INTO books (metadata_provider, provider_book_id, title, author, metadata_json) "
            "VALUES (?, ?, ?, ?, ?)",
            ("hardcover", provider_id, title, "Author", '{"authors":["Author"]}'),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def test_accepting_release_creates_source_and_immutable_matching_activity():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = ImportActivityService(db_path)
        book_id = _create_book(user_db, "42", "Example")

        activity = service.accept_book_targeted_release(
            source_key="prowlarr:abc123",
            source="prowlarr",
            source_metadata={"title": "Example release"},
            task_id="activity-1",
            book_id=book_id,
        )

        assert activity["state"] == "matching"
        assert activity["task_id"] == "activity-1"
        assert activity["book_snapshot"]["title"] == "Example"
        assert activity["source_release"]["source_key"] == "prowlarr:abc123"

        member = service.record_source_member(
            source_release_id=activity["source_release_id"],
            relative_path="collection/Example.epub",
            size=123,
            file_format="epub",
            discovery_status="discovered",
        )
        assert member["relative_path"] == "collection/Example.epub"


def test_one_source_release_can_create_multiple_book_activities():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = ImportActivityService(db_path)
        first_book = _create_book(user_db, "42", "Example")
        second_book = _create_book(user_db, "43", "Another")

        first = service.accept_book_targeted_release(
            source_key="prowlarr:abc123",
            source="prowlarr",
            source_metadata={},
            task_id="activity-1",
            book_id=first_book,
        )
        second = service.accept_book_targeted_release(
            source_key="prowlarr:abc123",
            source="prowlarr",
            source_metadata={},
            task_id="activity-2",
            book_id=second_book,
        )

        assert first["source_release_id"] == second["source_release_id"]
        assert first["id"] != second["id"]


def test_retry_and_recovery_preserve_the_activity_output_plan():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = ImportActivityService(db_path)
        activity = service.accept_book_targeted_release(
            source_key="prowlarr:abc123",
            source="prowlarr",
            source_metadata={},
            task_id="activity-1",
            book_id=_create_book(user_db, "42", "Example"),
        )
        member = service.record_source_member(
            source_release_id=activity["source_release_id"],
            relative_path="Example.epub",
            size=7,
            file_format="epub",
            discovery_status="discovered",
        )

        planned = service.plan_import(
            activity_id=activity["id"],
            storage_root=Path(tmpdir) / "library",
            selections=[
                {
                    "source_member_id": member["id"],
                    "evidence": {"reason": "exact-title-author"},
                }
            ],
        )
        output_path = Path(planned["selections"][0]["planned_output_path"])
        failed = service.fail(activity_id=activity["id"], error_context={"message": "disk full"})
        retried = service.retry(activity_id=activity["id"])

        assert failed["state"] == "failed"
        assert retried["state"] == "importing"
        assert retried["retry_count"] == 1
        assert retried["selections"][0]["planned_output_path"] == str(output_path)
        assert service.reconcile(activity_id=activity["id"])["missing_output_paths"] == [
            str(output_path)
        ]

        output_path.parent.mkdir(parents=True)
        output_path.write_bytes(b"content")
        assert service.reconcile(activity_id=activity["id"])["missing_output_paths"] == []

        with pytest.raises(ValueError, match="cannot be planned"):
            service.plan_import(
                activity_id=activity["id"], storage_root=Path(tmpdir) / "library", selections=[]
            )


def test_cancelled_activity_cannot_be_retried():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = ImportActivityService(db_path)
        activity = service.accept_book_targeted_release(
            source_key="prowlarr:abc123",
            source="prowlarr",
            source_metadata={},
            task_id="activity-1",
            book_id=_create_book(user_db, "42", "Example"),
        )

        assert service.cancel(activity_id=activity["id"])["state"] == "cancelled"
        with pytest.raises(ValueError, match="cannot be retried"):
            service.retry(activity_id=activity["id"])


def test_plan_import_derives_safe_fixed_paths_and_rejects_duplicate_book_members():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        storage_root = Path(tmpdir) / "library"
        user_db = UserDB(db_path)
        user_db.initialize()
        service = ImportActivityService(db_path)
        book_id = _create_book(user_db, "42", "Example")
        activity = service.accept_book_targeted_release(
            source_key="prowlarr:abc123",
            source="prowlarr",
            source_metadata={},
            task_id="activity-1",
            book_id=book_id,
        )
        member = service.record_source_member(
            source_release_id=activity["source_release_id"],
            relative_path="collection/Chapter: 1/Example?.epub",
            size=7,
            file_format="epub",
            discovery_status="discovered",
        )

        planned = service.plan_import(
            activity_id=activity["id"],
            storage_root=storage_root,
            selections=[{"source_member_id": member["id"], "evidence": {"match": "exact"}}],
        )

        assert planned["state"] == "importing"
        assert planned["selections"][0]["planned_output_path"] == str(
            storage_root
            / "books"
            / str(book_id)
            / str(activity["source_release_id"])
            / "collection"
            / "Chapter_ 1"
            / "Example_.epub"
        )

        duplicate = service.accept_book_targeted_release(
            source_key="prowlarr:abc123",
            source="prowlarr",
            source_metadata={},
            task_id="activity-2",
            book_id=book_id,
        )
        with pytest.raises(ValueError, match="already selected for this Book"):
            service.plan_import(
                activity_id=duplicate["id"],
                storage_root=storage_root,
                selections=[{"source_member_id": member["id"], "evidence": {"match": "exact"}}],
            )


def test_reselecting_deleted_release_member_creates_a_new_final_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        storage_root = Path(tmpdir) / "library"
        user_db = UserDB(db_path)
        user_db.initialize()
        imports = ImportActivityService(db_path)
        history = DownloadHistoryService(db_path)
        library = LibraryService(db_path)
        user = user_db.create_user(username="owner")
        book_id = _create_book(user_db, "42", "Example")
        first = imports.accept_book_targeted_release(
            source_key="prowlarr:abc123",
            source="prowlarr",
            source_metadata={},
            task_id="activity-1",
            book_id=book_id,
        )
        member = imports.record_source_member(
            source_release_id=first["source_release_id"],
            relative_path="Example.epub",
            size=7,
            file_format="epub",
            discovery_status="discovered",
        )
        first = imports.plan_import(
            activity_id=first["id"],
            storage_root=storage_root,
            selections=[{"source_member_id": member["id"]}],
        )
        first_path = Path(first["selections"][0]["planned_output_path"])
        first_path.parent.mkdir(parents=True)
        first_path.write_bytes(b"first")
        history.record_download(
            task_id="activity-1",
            user_id=user["id"],
            username=user["username"],
            request_id=None,
            source="prowlarr",
            source_display_name="Prowlarr",
            title="Example",
            author="Author",
            file_format=None,
            size=None,
            preview=None,
            content_type="ebook",
            origin="book",
            book_id=book_id,
            import_activity_id=first["id"],
        )
        history.finalize_download_files(
            task_id="activity-1",
            final_status="complete",
            file_rows=[{"download_path": str(first_path), "format": "epub", "size": "5"}],
        )
        imports.complete(activity_id=first["id"])
        first_history_id = library.get_files_on_disk(book_id)[0]["id"]

        assert library.delete_release(book_id=book_id, history_id=first_history_id)
        deleted = imports.get_by_task_id("activity-1")
        assert deleted is not None
        assert deleted["book_id"] is None
        assert [selection["source_member_id"] for selection in deleted["selections"]] == [
            member["id"]
        ]

        second = imports.accept_book_targeted_release(
            source_key="prowlarr:abc123",
            source="prowlarr",
            source_metadata={},
            task_id="activity-2",
            book_id=book_id,
        )
        second = imports.plan_import(
            activity_id=second["id"],
            storage_root=storage_root,
            selections=[{"source_member_id": member["id"]}],
        )
        second_path = Path(second["selections"][0]["planned_output_path"])
        second_path.parent.mkdir(parents=True, exist_ok=True)
        second_path.write_bytes(b"second")
        history.record_download(
            task_id="activity-2",
            user_id=user["id"],
            username=user["username"],
            request_id=None,
            source="prowlarr",
            source_display_name="Prowlarr",
            title="Example",
            author="Author",
            file_format=None,
            size=None,
            preview=None,
            content_type="ebook",
            origin="book",
            book_id=book_id,
            import_activity_id=second["id"],
        )
        history.finalize_download_files(
            task_id="activity-2",
            final_status="complete",
            file_rows=[{"download_path": str(second_path), "format": "epub", "size": "6"}],
        )
        imports.complete(activity_id=second["id"])

        files = library.get_files_on_disk(book_id)
        assert [(file["task_id"], file["download_path"]) for file in files] == [
            ("activity-2", str(second_path))
        ]
