"""Tests for durable source releases and Book import activities."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from shelfmark.core.import_activity_service import ImportActivityService
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
        output_path = Path(tmpdir) / "library" / "Example.epub"
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

        service.plan_import(
            activity_id=activity["id"],
            selections=[
                {
                    "source_member_id": member["id"],
                    "evidence": {"reason": "exact-title-author"},
                    "planned_output_path": str(output_path),
                }
            ],
        )
        failed = service.fail(activity_id=activity["id"], error_context={"message": "disk full"})
        retried = service.retry(activity_id=activity["id"])

        assert failed["state"] == "failed"
        assert retried["state"] == "importing"
        assert retried["retry_count"] == 1
        assert retried["selections"][0]["planned_output_path"] == str(output_path)
        assert service.reconcile(activity_id=activity["id"])["missing_output_paths"] == [
            str(output_path)
        ]

        output_path.parent.mkdir()
        output_path.write_bytes(b"content")
        assert service.reconcile(activity_id=activity["id"])["missing_output_paths"] == []

        with pytest.raises(ValueError, match="cannot be planned"):
            service.plan_import(activity_id=activity["id"], selections=[])


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
