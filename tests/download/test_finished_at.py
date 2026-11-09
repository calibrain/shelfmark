"""A live queue entry says when it ended, so a card has its timing before history catches up."""

from __future__ import annotations

from shelfmark.core.models import DownloadTask, QueueStatus
from shelfmark.core.queue import BookQueue
from shelfmark.download.orchestrator import _task_to_dict


def _finished_task(status: QueueStatus) -> tuple[BookQueue, DownloadTask]:
    """A task driven to the given terminal status through the queue."""
    queue = BookQueue()
    task = DownloadTask(task_id="t", source="direct_download", title="Book")
    assert queue.add(task) is True
    assert queue.get_next() is not None
    queue.update_status("t", QueueStatus.DOWNLOADING)
    queue.update_status("t", status)
    return queue, task


def test_a_task_is_stamped_when_it_first_ends() -> None:
    _queue, task = _finished_task(QueueStatus.COMPLETE)

    assert task.finished_at is not None
    assert task.finished_at >= task.added_time


def test_the_stamp_is_set_before_the_terminal_hook_runs() -> None:
    queue = BookQueue()
    task = DownloadTask(task_id="t", source="direct_download", title="Book")
    seen: list[float | None] = []
    queue.set_terminal_status_hook(lambda _id, _status, hooked: seen.append(hooked.finished_at))
    assert queue.add(task) is True
    assert queue.get_next() is not None

    queue.update_status("t", QueueStatus.ERROR)

    assert seen and seen[0] is not None


def test_a_requeued_task_is_running_again_so_it_has_no_end() -> None:
    queue, task = _finished_task(QueueStatus.ERROR)

    assert queue.enqueue_existing("t") is True

    assert task.finished_at is None


def test_the_stamp_reaches_the_status_payload() -> None:
    _queue, task = _finished_task(QueueStatus.CANCELLED)

    assert (
        _task_to_dict(task, current_status=QueueStatus.CANCELLED)["finished_at"] == task.finished_at
    )
