from unittest.mock import MagicMock


def test_update_download_status_dedupes_identical_events(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-book-id"

    # Ensure clean module-level state
    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()

    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "queue_status", lambda: {})

    mock_ws = MagicMock()
    monkeypatch.setattr(orchestrator, "ws_manager", mock_ws)

    times = iter([1.0, 2.0])
    monkeypatch.setattr(orchestrator.time, "time", lambda: next(times))

    orchestrator.update_download_status(book_id, "resolving", "Bypassing protection...")
    orchestrator.update_download_status(book_id, "resolving", "Bypassing protection...")

    # Status + message should only be applied/broadcast once.
    assert mock_queue.update_status.call_count == 1
    assert mock_queue.update_status_message.call_count == 1
    assert mock_ws.broadcast_status_update.call_count == 1

    # Duplicate keep-alives should not refresh stall activity.
    assert orchestrator._last_activity[book_id] == 1.0


def test_update_download_progress_dedupes_identical_progress_for_activity(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-progress-book"

    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()

    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    times = iter([10.0, 20.0])
    monkeypatch.setattr(orchestrator.time, "time", lambda: next(times))

    orchestrator.update_download_progress(book_id, 0.0)
    orchestrator.update_download_progress(book_id, 0.0)

    assert mock_queue.update_progress.call_count == 2
    assert orchestrator._last_activity[book_id] == 10.0
    assert orchestrator._last_progress_value[book_id] == 0.0


def test_update_download_progress_refreshes_activity_when_progress_changes(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-progress-change"

    orchestrator._last_activity.clear()
    orchestrator._last_progress_value.clear()
    orchestrator._last_status_event.clear()

    mock_queue = MagicMock()
    monkeypatch.setattr(orchestrator, "book_queue", mock_queue)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    times = iter([30.0, 40.0])
    monkeypatch.setattr(orchestrator.time, "time", lambda: next(times))

    orchestrator.update_download_progress(book_id, 0.0)
    orchestrator.update_download_progress(book_id, 0.5)

    assert orchestrator._last_activity[book_id] == 40.0
    assert orchestrator._last_progress_value[book_id] == 0.5
