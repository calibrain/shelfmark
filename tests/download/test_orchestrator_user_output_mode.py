from unittest.mock import MagicMock

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


def test_queue_release_persists_generic_retry_resolution_fields(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    captured: dict[str, object] = {}

    def fake_add(task):
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    success, error = orchestrator.queue_release(
        {
            "source": "prowlarr",
            "source_id": "prowlarr-release-1",
            "title": "Queued Prowlarr Release",
            "download_url": "magnet:?xt=urn:btih:abc123",
            "protocol": "torrent",
            "indexer": "MyIndexer",
            "extra": {
                "minimum_ratio": 1.25,
                "minimum_seed_time": 5400,
                "info_hash": "ABC123",
            },
        },
        user_id=42,
        username="alice",
    )

    assert success is True
    assert error is None
    task = captured["task"]
    assert task.retry_download_url == "magnet:?xt=urn:btih:abc123"
    assert task.retry_download_protocol == "torrent"
    assert task.retry_release_name == "Queued Prowlarr Release"
    assert task.retry_expected_hash == "ABC123"
    assert task.retry_ratio_limit == 1.25
    assert task.retry_seeding_time_limit_minutes == 90
    assert task.can_retry_without_staged_source is True


def test_queue_release_prefers_configured_seed_time_minutes_for_retry(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    captured: dict[str, object] = {}

    def fake_add(task):
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    success, error = orchestrator.queue_release(
        {
            "source": "prowlarr",
            "source_id": "prowlarr-release-configured-seed-time",
            "title": "Queued Prowlarr Release",
            "download_url": "magnet:?xt=urn:btih:abc123",
            "protocol": "torrent",
            "extra": {
                "configured_ratio_limit": 2,
                "configured_seed_time_minutes": 7200,
                "minimum_ratio": 1,
                "minimum_seed_time": 259200,
            },
        },
        user_id=42,
        username="alice",
    )

    assert success is True
    assert error is None
    task = captured["task"]
    assert task.retry_ratio_limit == 2.0
    assert task.retry_seeding_time_limit_minutes == 7200


def test_queue_release_returns_error_for_operational_queue_failure(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    monkeypatch.setattr(
        orchestrator.book_queue,
        "add",
        MagicMock(side_effect=RuntimeError("queue offline")),
    )
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    success, error = orchestrator.queue_release(
        {
            "source": "direct_download",
            "source_id": "release-broken-1",
            "title": "Broken Queue",
            "content_type": "ebook",
        }
    )

    assert success is False
    assert error == "Error queueing release: queue offline"
