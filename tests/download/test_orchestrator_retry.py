from __future__ import annotations

from threading import Event
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from shelfmark.core.models import DownloadTask, QueueStatus
from shelfmark.core.queue import BookQueue


def test_retry_download_requeues_error_task(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    task = DownloadTask(
        task_id="task-1",
        source="direct_download",
        title="Retryable",
        last_error_message="Timeout",
        last_error_type="TimeoutError",
    )

    mock_queue = MagicMock()
    mock_queue.get_task.return_value = task
    mock_queue.get_task_status.return_value = QueueStatus.ERROR
    mock_queue.enqueue_existing.return_value = True
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    ok, error = orchestrator.retry_download("task-1")

    assert ok is True
    assert error is None
    assert task.last_error_message is None
    assert task.last_error_type is None
    assert task.priority == -10
    mock_queue.enqueue_existing.assert_called_once_with("task-1", priority=-10)
    mock_queue.update_status_message.assert_called_once_with("task-1", "Retrying now")


def test_retry_download_rejects_request_linked_tasks(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    task = DownloadTask(
        task_id="task-2",
        source="prowlarr",
        title="Request linked",
        request_id=123,
    )

    mock_queue = MagicMock()
    mock_queue.get_task.return_value = task
    mock_queue.get_task_status.return_value = QueueStatus.ERROR
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    ok, error = orchestrator.retry_download("task-2")

    assert ok is False
    assert error == "Request-linked downloads must be retried from requests"
    mock_queue.enqueue_existing.assert_not_called()


def test_finalize_download_failure_sets_terminal_error(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    task = DownloadTask(
        task_id="task-3",
        source="direct_download",
        title="Terminal failure",
        last_error_message="Download timed out",
        last_error_type="TimeoutError",
    )

    mock_queue = MagicMock()
    mock_queue.get_task.return_value = task
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)

    orchestrator._finalize_download_failure("task-3")

    mock_queue.update_status_message.assert_called_once_with("task-3", "Download timed out")
    mock_queue.update_status.assert_called_once_with("task-3", QueueStatus.ERROR)


def test_finalize_download_failure_uses_fallback_message(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    task = DownloadTask(
        task_id="task-4",
        source="direct_download",
        title="No message",
        last_error_type="TimeoutError",
    )

    mock_queue = MagicMock()
    mock_queue.get_task.return_value = task
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)

    orchestrator._finalize_download_failure("task-4")

    mock_queue.update_status_message.assert_called_once_with(
        "task-4", "Download failed: TimeoutError"
    )
    mock_queue.update_status.assert_called_once_with("task-4", QueueStatus.ERROR)


def test_callback_error_results_in_terminal_error(monkeypatch):
    """When a handler signals error via status_callback, it should result in terminal ERROR."""
    import shelfmark.download.orchestrator as orchestrator

    task = DownloadTask(
        task_id="task-callback-1",
        source="direct_download",
        title="Callback Error",
    )

    queue = BookQueue()
    queue.add(task)
    terminal_calls: list[tuple[str, QueueStatus]] = []
    queue.set_terminal_status_hook(
        lambda task_id, status, _task: terminal_calls.append((task_id, status))
    )

    handler = MagicMock()

    def _download(_task, _cancel_flag, _progress_callback, status_callback):
        status_callback("error", "Download timed out")
        return None

    handler.download.side_effect = _download
    handler.post_process_cleanup = MagicMock()

    monkeypatch.setattr(orchestrator, "book_queue", queue)
    monkeypatch.setattr(orchestrator, "get_handler", lambda _source: handler)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    orchestrator._process_single_download(task.task_id, Event())

    assert task.last_error_message == "Download timed out"
    assert task.last_error_type == "StatusCallbackError"
    assert queue.get_task_status(task.task_id) == QueueStatus.ERROR
    assert len(terminal_calls) == 1
    assert terminal_calls[0] == (task.task_id, QueueStatus.ERROR)


def test_output_stage_retry_skips_redownload_when_staged_file_exists(monkeypatch, tmp_path):
    import shelfmark.download.orchestrator as orchestrator

    staged_file = tmp_path / "staged.epub"
    staged_file.write_text("staged")

    task = DownloadTask(
        task_id="task-staged-1",
        source="direct_download",
        title="Reuse Staged File",
        staged_path=str(staged_file),
    )

    handler = MagicMock()
    handler.download = MagicMock(return_value=str(tmp_path / "should-not-download.epub"))
    handler.post_process_cleanup = MagicMock()

    mock_queue = MagicMock()
    mock_queue.get_task.return_value = task
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "get_handler", lambda _source: handler)

    seen_temp_files: list[Path] = []

    def _post_process(temp_file, *_args, **_kwargs):
        seen_temp_files.append(temp_file)
        return "folder://done"

    monkeypatch.setattr(orchestrator, "post_process_download", _post_process)

    result = orchestrator._download_task(task.task_id, Event())

    assert result == "folder://done"
    handler.download.assert_not_called()
    assert seen_temp_files == [staged_file]
    assert task.staged_path is None


def test_output_stage_retry_falls_back_to_download_when_staged_file_missing(monkeypatch, tmp_path):
    import shelfmark.download.orchestrator as orchestrator

    missing_staged_file = tmp_path / "missing.epub"
    downloaded_file = tmp_path / "downloaded.epub"
    downloaded_file.write_text("downloaded")

    task = DownloadTask(
        task_id="task-staged-2",
        source="direct_download",
        title="Fallback Download",
        staged_path=str(missing_staged_file),
    )

    handler = MagicMock()
    handler.download = MagicMock(return_value=str(downloaded_file))
    handler.post_process_cleanup = MagicMock()

    mock_queue = MagicMock()
    mock_queue.get_task.return_value = task
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "get_handler", lambda _source: handler)

    seen_temp_files: list[Path] = []

    def _post_process(temp_file, *_args, **_kwargs):
        seen_temp_files.append(temp_file)
        return "folder://done"

    monkeypatch.setattr(orchestrator, "post_process_download", _post_process)

    result = orchestrator._download_task(task.task_id, Event())

    assert result == "folder://done"
    handler.download.assert_called_once()
    assert seen_temp_files == [downloaded_file]
    assert task.staged_path is None
