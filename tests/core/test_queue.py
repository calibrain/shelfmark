"""Tests for queue hook failure handling."""

from unittest.mock import patch

from shelfmark.core.models import DownloadTask
from shelfmark.core.queue import BookQueue


def _make_task(task_id: str = "task-1") -> DownloadTask:
    return DownloadTask(
        task_id=task_id,
        source="direct_download",
        title="Example Title",
        user_id=1,
        username="alice",
    )


def test_add_logs_queue_hook_failures():
    queue = BookQueue()

    def broken_hook(task_id: str, task: DownloadTask) -> None:
        raise RuntimeError("boom")

    queue.set_queue_hook(broken_hook)

    with patch("shelfmark.core.queue.logger.warning") as mock_warning:
        assert queue.add(_make_task()) is True

    mock_warning.assert_called_once()
    args = mock_warning.call_args.args
    assert args[0] == "Queue hook failed while adding task %s: %s"
    assert args[1] == "task-1"
    assert str(args[2]) == "boom"


def test_enqueue_existing_logs_queue_hook_failures():
    queue = BookQueue()
    assert queue.add(_make_task("task-2")) is True

    def broken_hook(task_id: str, task: DownloadTask) -> None:
        raise RuntimeError("boom")

    queue.set_queue_hook(broken_hook)

    with patch("shelfmark.core.queue.logger.warning") as mock_warning:
        assert queue.enqueue_existing("task-2") is True

    mock_warning.assert_called_once()
    args = mock_warning.call_args.args
    assert args[0] == "Queue hook failed while requeueing task %s: %s"
    assert args[1] == "task-2"
    assert str(args[2]) == "boom"
