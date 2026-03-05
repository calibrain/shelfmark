"""Tests for per-viewer activity visibility state."""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "shelfmark.db")


@pytest.fixture
def activity_view_state_service(db_path):
    from shelfmark.core.activity_view_state_service import ActivityViewStateService
    from shelfmark.core.user_db import UserDB

    user_db = UserDB(db_path)
    user_db.initialize()
    return ActivityViewStateService(db_path)


class TestActivityViewStateService:
    def test_dismiss_and_clear_history_are_viewer_scoped(self, activity_view_state_service):
        activity_view_state_service.dismiss(
            viewer_scope="user:1",
            item_type="download",
            item_key="download:first-task",
        )
        activity_view_state_service.dismiss(
            viewer_scope="user:1",
            item_type="request",
            item_key="request:12",
        )
        activity_view_state_service.dismiss(
            viewer_scope="admin:shared",
            item_type="download",
            item_key="download:first-task",
        )

        user_hidden = activity_view_state_service.list_hidden(viewer_scope="user:1")
        assert {row["item_key"] for row in user_hidden} == {"download:first-task", "request:12"}

        user_history = activity_view_state_service.list_history(viewer_scope="user:1", limit=10, offset=0)
        assert [row["item_key"] for row in user_history] == ["request:12", "download:first-task"]
        assert all(isinstance(row["dismissed_at"], str) for row in user_history)

        cleared_count = activity_view_state_service.clear_history(viewer_scope="user:1")
        assert cleared_count == 2
        assert activity_view_state_service.list_history(viewer_scope="user:1", limit=10, offset=0) == []

        user_hidden_after_clear = activity_view_state_service.list_hidden(viewer_scope="user:1")
        assert {row["item_key"] for row in user_hidden_after_clear} == {"download:first-task", "request:12"}

        admin_history = activity_view_state_service.list_history(
            viewer_scope="admin:shared",
            limit=10,
            offset=0,
        )
        assert [row["item_key"] for row in admin_history] == ["download:first-task"]

    def test_clear_item_for_all_viewers_removes_every_scope(self, activity_view_state_service):
        activity_view_state_service.dismiss_many(
            viewer_scope="user:1",
            items=[
                {"item_type": "download", "item_key": "download:shared-task"},
                {"item_type": "download", "item_key": "download:shared-task"},
                {"item_type": "request", "item_key": "request:7"},
            ],
        )
        activity_view_state_service.dismiss(
            viewer_scope="admin:shared",
            item_type="download",
            item_key="download:shared-task",
        )

        removed = activity_view_state_service.clear_item_for_all_viewers(
            item_type="download",
            item_key="download:shared-task",
        )
        assert removed == 2

        user_hidden = activity_view_state_service.list_hidden(viewer_scope="user:1")
        assert [row["item_key"] for row in user_hidden] == ["request:7"]

        admin_hidden = activity_view_state_service.list_hidden(viewer_scope="admin:shared")
        assert admin_hidden == []

    def test_delete_viewer_scope_validates_and_removes_rows(self, activity_view_state_service):
        activity_view_state_service.dismiss(
            viewer_scope="user:99",
            item_type="download",
            item_key="download:task-99",
        )

        deleted = activity_view_state_service.delete_viewer_scope(viewer_scope="user:99")
        assert deleted == 1
        assert activity_view_state_service.list_hidden(viewer_scope="user:99") == []

        with pytest.raises(ValueError, match="positive integer"):
            activity_view_state_service.list_hidden(viewer_scope="user:0")

    def test_list_hidden_returns_all_rows_by_default(self, activity_view_state_service):
        items = [
            {"item_type": "request", "item_key": f"request:{index}"}
            for index in range(1, 5002)
        ]
        activity_view_state_service.dismiss_many(
            viewer_scope="user:1",
            items=items,
        )

        all_hidden = activity_view_state_service.list_hidden(viewer_scope="user:1")
        limited_hidden = activity_view_state_service.list_hidden(viewer_scope="user:1", limit=10)

        assert len(all_hidden) == 5001
        assert len(limited_hidden) == 10
