from shelfmark.core.models import SearchMode


def test_queue_release_uses_user_specific_books_output_mode(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    captured: dict[str, object] = {}
    config_calls: list[tuple[str, object]] = []

    def fake_config_get(key, default=None, user_id=None):
        config_calls.append((key, user_id))
        if key == "BOOKS_OUTPUT_MODE":
            return "email" if user_id == 42 else "folder"
        if key == "EMAIL_RECIPIENT":
            return "alice@example.com" if user_id == 42 else ""
        return default

    def fake_add(task):
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.config, "get", fake_config_get)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    release_data = {
        "source": "direct_download",
        "source_id": "release-1",
        "title": "Release Title",
        "content_type": "book (fiction)",
        "format": "epub",
        "size": "1 MB",
        "download_url": "https://audiobookbay.lu/abss/release-title/",
    }

    success, error = orchestrator.queue_release(release_data, user_id=42, username="alice")

    assert success is True
    assert error is None
    task = captured["task"]
    assert task.output_mode == "email"
    assert task.output_args == {"to": "alice@example.com"}
    assert task.source_url == "https://audiobookbay.lu/abss/release-title/"
    assert task.search_mode == SearchMode.UNIVERSAL
    assert ("BOOKS_OUTPUT_MODE", 42) in config_calls


def test_queue_release_preserves_direct_search_mode_from_payload(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    captured: dict[str, object] = {}

    def fake_add(task):
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    success, error = orchestrator.queue_release(
        {
            "source": "direct_download",
            "source_id": "release-direct",
            "title": "Direct Title",
            "content_type": "ebook",
            "search_mode": "direct",
        },
        user_id=42,
        username="alice",
    )

    assert success is True
    assert error is None
    assert captured["task"].search_mode == SearchMode.DIRECT


def test_queue_release_email_mode_without_recipient_is_queued(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    captured: dict[str, object] = {}

    def fake_config_get(key, default=None, user_id=None):
        if key == "BOOKS_OUTPUT_MODE":
            return "email" if user_id == 42 else "folder"
        if key == "EMAIL_RECIPIENT":
            return ""
        return default

    def fake_add(task):
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.config, "get", fake_config_get)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    release_data = {
        "source": "direct_download",
        "source_id": "release-1",
        "title": "Release Title",
        "content_type": "book (fiction)",
        "format": "epub",
        "size": "1 MB",
    }

    success, error = orchestrator.queue_release(release_data, user_id=42, username="alice")

    assert success is True
    assert error is None
    task = captured["task"]
    assert task.output_mode == "email"
    assert task.output_args == {}
