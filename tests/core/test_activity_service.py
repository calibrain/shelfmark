"""Tests for activity service persistence helpers."""

from __future__ import annotations

import os
import tempfile

import pytest

from shelfmark.core.activity_service import (
    ActivityService,
    build_download_item_key,
    build_item_key,
    build_request_item_key,
)
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
def activity_service(db_path):
    return ActivityService(db_path)


class TestItemKeys:
    def test_build_request_item_key(self):
        assert build_request_item_key(42) == "request:42"
        assert build_item_key("request", 7) == "request:7"

    def test_build_download_item_key(self):
        assert build_download_item_key("abc123") == "download:abc123"
        assert build_item_key("download", "xyz") == "download:xyz"

    def test_build_item_key_validation(self):
        with pytest.raises(ValueError):
            build_item_key("bad", "x")
        with pytest.raises(ValueError):
            build_item_key("request", "nope")
        with pytest.raises(ValueError):
            build_item_key("download", "")


class TestActivityService:
    def test_record_snapshot_and_dismiss_and_history(self, user_db, activity_service):
        user = user_db.create_user(username="activity-user")
        snapshot = activity_service.record_terminal_snapshot(
            user_id=user["id"],
            item_type="download",
            item_key="download:task-1",
            origin="requested",
            final_status="complete",
            request_id=12,
            source_id="task-1",
            snapshot={"title": "My Book", "status": "complete"},
        )

        assert snapshot["item_type"] == "download"
        assert snapshot["item_key"] == "download:task-1"
        assert snapshot["origin"] == "requested"
        assert snapshot["final_status"] == "complete"

        dismissal = activity_service.dismiss_item(
            user_id=user["id"],
            item_type="download",
            item_key="download:task-1",
        )
        assert dismissal["item_type"] == "download"
        assert dismissal["item_key"] == "download:task-1"
        assert dismissal["activity_log_id"] == snapshot["id"]

        dismissed_set = activity_service.get_dismissal_set(user["id"])
        assert dismissed_set == [{"item_type": "download", "item_key": "download:task-1"}]

        history = activity_service.get_history(user["id"], limit=10, offset=0)
        assert len(history) == 1
        assert history[0]["item_type"] == "download"
        assert history[0]["item_key"] == "download:task-1"
        assert history[0]["origin"] == "requested"
        assert history[0]["final_status"] == "complete"
        assert history[0]["snapshot"] == {"title": "My Book", "status": "complete"}

    def test_dismiss_many_and_clear_history(self, user_db, activity_service):
        alice = user_db.create_user(username="alice")
        bob = user_db.create_user(username="bob")

        activity_service.record_terminal_snapshot(
            user_id=alice["id"],
            item_type="request",
            item_key="request:10",
            origin="request",
            final_status="rejected",
            request_id=10,
            snapshot={"title": "Rejected Book"},
        )
        activity_service.record_terminal_snapshot(
            user_id=alice["id"],
            item_type="download",
            item_key="download:task-2",
            origin="direct",
            final_status="error",
            source_id="task-2",
            snapshot={"title": "Failed Download"},
        )

        dismissed_count = activity_service.dismiss_many(
            user_id=alice["id"],
            items=[
                {"item_type": "request", "item_key": "request:10"},
                {"item_type": "download", "item_key": "download:task-2"},
            ],
        )
        assert dismissed_count == 2

        # Bob has independent dismiss state.
        activity_service.dismiss_item(
            user_id=bob["id"],
            item_type="request",
            item_key="request:10",
        )

        alice_history = activity_service.get_history(alice["id"])
        bob_history = activity_service.get_history(bob["id"])
        assert len(alice_history) == 2
        assert len(bob_history) == 1

        cleared = activity_service.clear_history(alice["id"])
        assert cleared == 2
        assert activity_service.get_history(alice["id"]) == []
        assert len(activity_service.get_history(bob["id"])) == 1

    def test_get_undismissed_terminal_downloads_returns_latest_per_item_and_excludes_dismissed(
        self,
        user_db,
        activity_service,
    ):
        user = user_db.create_user(username="snapshot-user")

        activity_service.record_terminal_snapshot(
            user_id=user["id"],
            item_type="download",
            item_key="download:task-1",
            origin="direct",
            final_status="error",
            source_id="task-1",
            terminal_at="2026-01-01T10:00:00+00:00",
            snapshot={"kind": "download", "download": {"id": "task-1", "status_message": "failed"}},
        )
        activity_service.record_terminal_snapshot(
            user_id=user["id"],
            item_type="download",
            item_key="download:task-1",
            origin="direct",
            final_status="complete",
            source_id="task-1",
            terminal_at="2026-01-01T11:00:00+00:00",
            snapshot={"kind": "download", "download": {"id": "task-1", "status_message": "done"}},
        )
        activity_service.record_terminal_snapshot(
            user_id=user["id"],
            item_type="download",
            item_key="download:task-2",
            origin="direct",
            final_status="cancelled",
            source_id="task-2",
            terminal_at="2026-01-01T09:00:00+00:00",
            snapshot={"kind": "download", "download": {"id": "task-2", "status_message": "stopped"}},
        )

        activity_service.dismiss_item(
            user_id=user["id"],
            item_type="download",
            item_key="download:task-2",
        )

        rows = activity_service.get_undismissed_terminal_downloads(user["id"])
        assert len(rows) == 1
        assert rows[0]["item_key"] == "download:task-1"
        assert rows[0]["final_status"] == "complete"
        assert rows[0]["snapshot"] == {
            "kind": "download",
            "download": {"id": "task-1", "status_message": "done"},
        }
