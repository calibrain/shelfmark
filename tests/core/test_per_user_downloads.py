"""
Tests for per-user download scoping.

Tests that DownloadTask has a user_id field and that the queue
can be filtered by user.
"""

from shelfmark.core.models import DownloadTask, QueueStatus
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

    def test_clear_completed_for_user_only_removes_user_terminal_tasks(self):
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=1))
        q.add(self._make_task("book-2", user_id=2))
        q.add(self._make_task("book-3", user_id=1))

        q.update_status("book-1", QueueStatus.COMPLETE)
        q.update_status("book-2", QueueStatus.ERROR)
        q.update_status("book-3", QueueStatus.QUEUED)

        removed = q.clear_completed(user_id=1)
        assert removed == 1

        status = q.get_status()
        all_tasks = {}
        for tasks_by_status in status.values():
            all_tasks.update(tasks_by_status)

        assert "book-1" not in all_tasks
        assert "book-2" in all_tasks
        assert "book-3" in all_tasks

    def test_clear_completed_for_user_includes_legacy_tasks(self):
        q = BookQueue()
        q.add(self._make_task("legacy-book", user_id=None))
        q.add(self._make_task("user-book", user_id=1))

        q.update_status("legacy-book", QueueStatus.COMPLETE)
        q.update_status("user-book", QueueStatus.COMPLETE)

        removed = q.clear_completed(user_id=1)
        assert removed == 2

    def test_enqueue_existing_deduplicates_queue_entries(self):
        q = BookQueue()
        q.add(self._make_task("book-1", user_id=1))

        assert q.enqueue_existing("book-1")
        assert q.enqueue_existing("book-1", priority=-10)

        queue_order = q.get_queue_order()
        assert len(queue_order) == 1
        assert queue_order[0]["id"] == "book-1"
        assert q.get_task("book-1").priority == -10
        assert q.get_task_status("book-1") == QueueStatus.QUEUED


# ---------------------------------------------------------------------------
# Per-user destination override in get_final_destination
# ---------------------------------------------------------------------------


class TestPerUserDestination:
    """get_final_destination should resolve destination via config user context."""

    def test_passes_user_id_to_get_destination(self, monkeypatch):
        """When task has a user_id, destination resolution should receive it."""
        from pathlib import Path

        captured: dict[str, object] = {"user_id": None, "username": None}

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
            user_id=42,
            username="alice",
        )

        def fake_get_destination(is_audiobook: bool = False, user_id=None, username=None):
            captured["user_id"] = user_id
            captured["username"] = username
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
        assert captured["username"] == "alice"

    def test_without_user_id_uses_global_context(self, monkeypatch):
        """When task has no user_id, destination resolution should use global context."""
        from pathlib import Path

        captured: dict[str, object] = {"user_id": 99, "username": "someone"}

        task = DownloadTask(
            task_id="book1",
            source="direct_download",
            title="Test Book",
        )

        def fake_get_destination(is_audiobook: bool = False, user_id=None, username=None):
            captured["user_id"] = user_id
            captured["username"] = username
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
        assert captured["username"] is None

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
            lambda is_audiobook=False, user_id=None, username=None: Path("/global/books"),
        )
        monkeypatch.setattr(
            "shelfmark.download.postprocess.destination.get_aa_content_type_dir",
            lambda ct: Path("/routed/books"),
        )

        from shelfmark.download.postprocess.destination import get_final_destination

        result = get_final_destination(task)
        assert result == Path("/routed/books")


class TestUserDestinationTemplate:
    """Destination settings should support {User} placeholder expansion."""

    def test_get_destination_expands_user_for_books(self, monkeypatch):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.utils import get_destination

        def fake_config_get(key, default=None, user_id=None):
            if key == "DESTINATION":
                return "/books/{User}"
            if key == "INGEST_DIR":
                return "/books"
            return default

        monkeypatch.setattr(config, "get", fake_config_get)
        result = get_destination(is_audiobook=False, user_id=42, username="alice")
        assert result == Path("/books/alice")

    def test_get_destination_expands_user_for_audiobooks(self, monkeypatch):
        from pathlib import Path

        from shelfmark.core.config import config
        from shelfmark.core.utils import get_destination

        def fake_config_get(key, default=None, user_id=None):
            if key == "DESTINATION_AUDIOBOOK":
                return "/audiobooks/{User}"
            if key == "DESTINATION":
                return "/books/{User}"
            if key == "INGEST_DIR":
                return "/books"
            return default

        monkeypatch.setattr(config, "get", fake_config_get)
        result = get_destination(is_audiobook=True, user_id=42, username="alice")
        assert result == Path("/audiobooks/alice")


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
