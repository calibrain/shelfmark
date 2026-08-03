"""Terminal snapshots for durable Book imports."""

from __future__ import annotations

import importlib
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from shelfmark.core.models import DownloadTask, QueueStatus


@pytest.fixture(scope="module")
def main_module():
    import shelfmark.download.orchestrator as orchestrator

    with patch.object(orchestrator, "start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


def _book(main_module) -> dict:
    return main_module.library_service.upsert_book_from_metadata(
        metadata_provider="hardcover",
        provider_book_id=uuid.uuid4().hex,
        title="Import Activity Snapshot",
        author="Test Author",
        subtitle=None,
        publish_year=None,
        isbn_13=None,
        cover_url=None,
        series_name=None,
        series_position=None,
        language=None,
        metadata_json={},
    )


def test_terminal_snapshot_transfers_direct_download_to_immutable_plan(
    main_module, monkeypatch, tmp_path
):
    owner = main_module.user_db.create_user(username=f"owner-{uuid.uuid4().hex[:8]}", role="user")
    book = _book(main_module)
    source = tmp_path / "download.epub"
    source.write_bytes(b"ebook")
    destination = tmp_path / "destination"
    monkeypatch.setattr(
        main_module.app_config,
        "get",
        lambda key, default=None, **_kwargs: str(destination) if key == "DESTINATION" else default,
    )
    task = DownloadTask(
        task_id=f"import-{uuid.uuid4().hex[:8]}",
        source="direct_download",
        source_release_key=f"direct:{uuid.uuid4().hex}",
        title=book["title"],
        user_id=owner["id"],
        username=owner["username"],
        library_book_id=book["id"],
        download_path=str(source),
    )

    assert main_module.backend.book_queue.add(task) is True
    try:
        main_module.backend.book_queue.update_status(task.task_id, QueueStatus.COMPLETE)
        activity = main_module.import_activity_service.get_by_task_id(task.task_id)
        assert activity["state"] == "completed"
        assert [selection["planned_output_path"] for selection in activity["selections"]] == [
            str(
                destination
                / "books"
                / str(book["id"])
                / str(activity["source_release_id"])
                / "download.epub"
            )
        ]
        assert Path(activity["selections"][0]["planned_output_path"]).read_bytes() == b"ebook"
    finally:
        main_module.backend.book_queue.cancel_download(task.task_id)


def test_terminal_snapshot_fails_when_a_planned_member_is_unavailable(
    main_module, monkeypatch, tmp_path
):
    owner = main_module.user_db.create_user(username=f"owner-{uuid.uuid4().hex[:8]}", role="user")
    book = _book(main_module)
    source = tmp_path / "retained.epub"
    source.write_bytes(b"ebook")
    monkeypatch.setattr(
        main_module.app_config,
        "get",
        lambda key, default=None, **_kwargs: (
            str(tmp_path / "destination") if key == "DESTINATION" else default
        ),
    )
    task = DownloadTask(
        task_id=f"missing-{uuid.uuid4().hex[:8]}",
        source="prowlarr",
        source_release_key=f"torrent:{uuid.uuid4().hex}",
        title=book["title"],
        user_id=owner["id"],
        username=owner["username"],
        library_book_id=book["id"],
        original_download_path=str(source),
    )

    assert main_module.backend.book_queue.add(task) is True
    try:
        activity = main_module.import_activity_service.get_by_task_id(task.task_id)
        member = main_module.import_activity_service.record_source_member(
            source_release_id=activity["source_release_id"],
            relative_path="retained.epub",
            size=source.stat().st_size,
            file_format="epub",
            discovery_status="discovered",
        )
        main_module.import_activity_service.plan_import(
            activity_id=activity["id"],
            storage_root=tmp_path / "destination",
            selections=[{"source_member_id": member["id"]}],
        )
        source.unlink()
        main_module.backend.book_queue.update_status(task.task_id, QueueStatus.COMPLETE)
        assert main_module.import_activity_service.get_by_task_id(task.task_id)["state"] == "failed"
        assert (
            main_module.download_history_service.get_by_task_id(task.task_id)["final_status"]
            == "error"
        )
    finally:
        main_module.backend.book_queue.cancel_download(task.task_id)
