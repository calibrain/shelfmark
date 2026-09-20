"""Tests for queue hook failure handling and cancellation."""

import threading
from unittest.mock import patch

from shelfmark.core.models import DownloadTask, QueueStatus
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


def test_cancel_does_not_overwrite_a_download_that_finished_first():
    """A download that completes while a cancel is in flight must stay complete."""
    queue = BookQueue()
    assert queue.add(_make_task("race-task")) is True
    assert queue.get_next() is not None
    queue.update_status("race-task", QueueStatus.DOWNLOADING)

    terminal_events: list[QueueStatus] = []
    queue.set_terminal_status_hook(lambda _task_id, status, _task: terminal_events.append(status))

    original_update_status = queue.update_status
    cancel_yielded = threading.Event()
    worker_finished = threading.Event()

    def update_status_yielding_to_the_worker(task_id: str, status: QueueStatus) -> None:
        # Hand the worker the queue whenever the cancel path lets go of it before writing.
        if status == QueueStatus.CANCELLED:
            cancel_yielded.set()
            worker_finished.wait(timeout=5)
        original_update_status(task_id, status)

    queue.update_status = update_status_yielding_to_the_worker  # type: ignore[method-assign]

    def finish_download() -> None:
        cancel_yielded.wait(timeout=5)
        original_update_status("race-task", QueueStatus.COMPLETE)
        worker_finished.set()

    worker = threading.Thread(target=finish_download, daemon=True, name="TestDownloadWorker")
    worker.start()
    try:
        queue.cancel_download("race-task")
    finally:
        cancel_yielded.set()
        worker.join(timeout=5)

    assert queue.get_task_status("race-task") == QueueStatus.COMPLETE
    assert terminal_events[-1] == QueueStatus.COMPLETE
