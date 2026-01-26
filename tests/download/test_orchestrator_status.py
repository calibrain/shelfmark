from unittest.mock import MagicMock


def test_update_download_status_dedupes_identical_events(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    book_id = "test-book-id"

    # Ensure clean module-level state
    orchestrator._last_activity.clear()
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

    # Activity timestamp should still be updated on the duplicate keep-alive call.
    assert orchestrator._last_activity[book_id] == 2.0

