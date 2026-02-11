"""
Tests for per-user download scoping.

Tests that DownloadTask has a user_id field and that the queue
can be filtered by user.
"""

from shelfmark.core.models import DownloadTask
from shelfmark.core.queue import BookQueue


class TestDownloadTaskUserId:
    """Tests that DownloadTask supports user_id."""

    def test_download_task_has_user_id_field(self):
        task = DownloadTask(
            task_id="test-123",
            source="direct_download",
            title="Test Book",
            user_id=42,
        )
        assert task.user_id == 42

    def test_download_task_user_id_defaults_to_none(self):
        task = DownloadTask(
            task_id="test-123",
            source="direct_download",
            title="Test Book",
        )
        assert task.user_id is None

    def test_download_task_preserves_user_id_in_queue(self):
        q = BookQueue()
        task = DownloadTask(
            task_id="test-123",
            source="direct_download",
            title="Test Book",
            user_id=42,
        )
        q.add(task)
        retrieved = q.get_task("test-123")
        assert retrieved.user_id == 42


class TestQueueFilterByUser:
    """Tests for filtering queue status by user."""

    def _make_task(self, task_id, user_id=None):
        return DownloadTask(
            task_id=task_id,
            source="direct_download",
            title=f"Book {task_id}",
            user_id=user_id,
        )

    def test_get_status_returns_all_when_no_filter(self):
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=1))
        q.add(self._make_task("book-2", user_id=2))
        q.add(self._make_task("book-3", user_id=1))

        status = q.get_status()
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)
        assert len(all_tasks) == 3

    def test_get_status_for_user_filters(self):
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=1))
        q.add(self._make_task("book-2", user_id=2))
        q.add(self._make_task("book-3", user_id=1))

        status = q.get_status(user_id=1)
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)
        assert len(all_tasks) == 2
        assert "book-1" in all_tasks
        assert "book-3" in all_tasks
        assert "book-2" not in all_tasks

    def test_get_status_for_user_returns_empty_when_none(self):
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=1))

        status = q.get_status(user_id=999)
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)
        assert len(all_tasks) == 0

    def test_get_status_no_user_id_filter_includes_legacy_tasks(self):
        """Tasks without user_id (legacy) are visible to everyone."""
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=None))
        q.add(self._make_task("book-2", user_id=1))

        # No filter - see all
        status = q.get_status()
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)
        assert len(all_tasks) == 2

    def test_get_status_user_filter_includes_legacy_tasks(self):
        """Tasks without user_id are visible to any user (backward compat)."""
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=None))
        q.add(self._make_task("book-2", user_id=1))

        status = q.get_status(user_id=1)
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)
        # User 1 sees their own + legacy (no user_id)
        assert len(all_tasks) == 2


# ---------------------------------------------------------------------------
# Per-user destination override in get_final_destination
# ---------------------------------------------------------------------------


class TestPerUserDestination:
    """get_final_destination should resolve destination via config user context."""

    def test_passes_user_id_to_get_destination(self, monkeypatch):
        """When task has a user_id, destination resolution should receive it."""
        from pathlib import Path

        captured: dict[str, int | None] = {"user_id": None}

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
            user_id=42,
        )

        def fake_get_destination(is_audiobook: bool = False, user_id=None):
            captured["user_id"] = user_id
            return Path("/user-books/alice")

        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_destination",
            fake_get_destination,
        )
        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_aa_content_type_dir",
            lambda ct: None,
        )

        from shelfmark.download.postprocess.destination import get_final_destination

        result = get_final_destination(task)
        assert result == Path("/user-books/alice")
        assert captured["user_id"] == 42

    def test_without_user_id_uses_global_context(self, monkeypatch):
        """When task has no user_id, destination resolution should use global context."""
        from pathlib import Path

        captured: dict[str, int | None] = {"user_id": 99}

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
        )

        def fake_get_destination(is_audiobook: bool = False, user_id=None):
            captured["user_id"] = user_id
            return Path("/global/books")

        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_destination",
            fake_get_destination,
        )
        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_aa_content_type_dir",
            lambda ct: None,
        )

        from shelfmark.download.postprocess.destination import get_final_destination

        result = get_final_destination(task)
        assert result == Path("/global/books")
        assert captured["user_id"] is None

    def test_content_type_routing_still_wins(self, monkeypatch):
        """Direct mode content-type routing should take priority over destination lookup."""
        from pathlib import Path

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
            content_type="book (fiction)",
            user_id=42,
        )

        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_destination",
            lambda is_audiobook=False, user_id=None: Path("/global/books"),
        )
        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_aa_content_type_dir",
            lambda ct: Path("/routed/books"),
        )

        from shelfmark.download.postprocess.destination import get_final_destination

        result = get_final_destination(task)
        assert result == Path("/routed/books")


class TestTaskToDictUsername:
    """Tests that _task_to_dict includes username for frontend display."""

    def test_task_to_dict_includes_username(self):
        """Username should be included in serialized task dict."""
        from shelfmark.download.orchestrator import _task_to_dict

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
            user_id=5,
            username="alice",
        )
        result = _task_to_dict(task)
        assert result["username"] == "alice"

    def test_task_to_dict_username_none_when_no_auth(self):
        """Username should be None when no user is set (no-auth mode)."""
        from shelfmark.download.orchestrator import _task_to_dict

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
        )
        result = _task_to_dict(task)
        assert result["username"] is None
