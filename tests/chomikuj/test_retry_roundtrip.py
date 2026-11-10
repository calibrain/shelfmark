"""Restart-safe retry round trip for Chomikuj through the real queue path.

The download handler is only useful across a restart if the release that was
queued carries the source-private context the handler needs to re-resolve the
file: the numeric Chomikuj file id, the byte size (used by the transfer-quota
preflight), and the free/paid flag. This module proves that contract end to end
without touching the network or starting a download:

* call the real :func:`shelfmark.download.orchestrator.queue_release` with a
  realistic Chomikuj release dict (as produced by ``source.to_release``);
* capture the :class:`DownloadTask` at the ``book_queue.add`` seam;
* round-trip that task through the orchestrator's own
  ``serialize_task_for_retry`` / ``_restore_task_from_retry_payload`` pair;
* prove a release without ``extra.file_id`` still resolves the numeric id from
  the ``chomikuj:<id>`` task id.
"""

import pytest

from shelfmark.release_sources.chomikuj.handler import ChomikujHandler

FILE_ID = "987654"
FILE_NAME = "Książka - Autor.pdf"
SIZE_BYTES = 200 * 1024  # Chomikuj reports kilobytes; the release carries bytes
TITLE = "Książka"


def _release(*, file_id: str | None = FILE_ID) -> dict[str, object]:
    """Build the release dict ``ChomikujSource`` hands to ``queue_release``."""
    extra: dict[str, object] = {
        "file_name": FILE_NAME,
        "file_type": "pdf",
        "is_free_for_user": False,
        "freeleech": False,
        "author": "Autor",
        "language": "pl",
        "access": "paid",
    }
    if file_id is not None:
        extra["file_id"] = file_id

    return {
        "source": "chomikuj",
        "source_id": f"chomikuj:{FILE_ID}",
        "title": TITLE,
        "format": "pdf",
        "size": "200.0 KB",
        "size_bytes": SIZE_BYTES,
        "content_type": "ebook",
        "protocol": "http",
        "indexer": "Chomikuj.pl",
        "download_url": None,
        "info_url": None,
        "extra": extra,
    }


def _queue_release(monkeypatch, release_data: dict[str, object]):
    """Run the real queue path and return the task captured at ``book_queue.add``."""
    import shelfmark.download.orchestrator as orchestrator

    captured: dict[str, object] = {}

    def fake_add(task):
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    # Availability depends on credentials/settings; the queue seam is what we test.
    monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _source: None)
    monkeypatch.setattr(orchestrator, "ws_manager", None)
    monkeypatch.setattr(
        orchestrator.config,
        "get",
        lambda key, default=None, user_id=None: default,
    )

    success, error = orchestrator.queue_release(release_data)

    assert success is True
    assert error is None
    task = captured["task"]
    assert task.source == "chomikuj"
    assert task.task_id == f"chomikuj:{FILE_ID}"
    return task


def test_retry_roundtrip_preserves_chomikuj_context(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    task = _queue_release(monkeypatch, _release())

    context = task.retry_source_context
    assert context["file_id"] == FILE_ID
    assert context["size_bytes"] == SIZE_BYTES
    assert isinstance(context["size_bytes"], int)
    assert not isinstance(context["size_bytes"], bool)
    assert context["is_free_for_user"] is False
    assert context["file_name"] == FILE_NAME

    # The handler re-resolves from the context it just persisted.
    assert ChomikujHandler._resolve_file_id(task) == FILE_ID

    payload = orchestrator.serialize_task_for_retry(task)
    assert payload["retry_source_context"] == context

    restored = orchestrator._restore_task_from_retry_payload(payload)
    assert restored is not None
    assert restored.source == "chomikuj"
    assert restored.task_id == task.task_id
    assert restored.retry_source_context == payload["retry_source_context"]
    assert restored.retry_source_context == context

    # Types survive the JSON-shaped payload, not just the values.
    assert isinstance(restored.retry_source_context["size_bytes"], int)
    assert restored.retry_source_context["is_free_for_user"] is False
    assert ChomikujHandler._resolve_file_id(restored) == FILE_ID


def test_missing_file_id_falls_back_to_task_id_prefix(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    task = _queue_release(monkeypatch, _release(file_id=None))

    # No extra.file_id: the context has no file id, but the release still queued.
    assert "file_id" not in task.retry_source_context
    assert task.retry_source_context["size_bytes"] == SIZE_BYTES
    assert task.retry_source_context["is_free_for_user"] is False

    # The handler falls back to the "chomikuj:<id>" task id, before and after a restart.
    assert ChomikujHandler._resolve_file_id(task) == FILE_ID

    payload = orchestrator.serialize_task_for_retry(task)
    restored = orchestrator._restore_task_from_retry_payload(payload)
    assert restored is not None
    assert restored.retry_source_context == task.retry_source_context
    assert ChomikujHandler._resolve_file_id(restored) == FILE_ID


@pytest.mark.parametrize("is_free", [True, False])
def test_is_free_for_user_stays_a_bool_through_the_roundtrip(monkeypatch, is_free):
    import shelfmark.download.orchestrator as orchestrator

    release = _release()
    extra = release["extra"]
    assert isinstance(extra, dict)
    extra["is_free_for_user"] = is_free
    extra["access"] = "free" if is_free else "paid"

    task = _queue_release(monkeypatch, release)
    assert task.retry_source_context["is_free_for_user"] is is_free

    restored = orchestrator._restore_task_from_retry_payload(
        orchestrator.serialize_task_for_retry(task)
    )
    assert restored is not None
    assert restored.retry_source_context["is_free_for_user"] is is_free
