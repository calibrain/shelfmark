"""Tests for terminal activity snapshot capture from queue transitions."""

from __future__ import annotations

import importlib
import json
import uuid
from unittest.mock import patch

import pytest

from shelfmark.core.models import DownloadTask, QueueStatus
from shelfmark.core.notifications import NotificationEvent


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


def _create_user(main_module, *, prefix: str) -> dict:
    username = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return main_module.user_db.create_user(username=username, role="user")


def _read_activity_log_row(main_module, snapshot_id: int):
    conn = main_module.user_db._connect()
    try:
        return conn.execute(
            "SELECT * FROM activity_log WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
    finally:
        conn.close()


class TestTerminalSnapshotCapture:
    def test_complete_transition_records_direct_snapshot_and_survives_queue_clear(self, main_module):
        user = _create_user(main_module, prefix="snap-direct")
        task_id = f"direct-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Direct Snapshot",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)
            item_key = f"download:{task_id}"
            snapshot_id = main_module.activity_service.get_latest_activity_log_id(
                item_type="download",
                item_key=item_key,
            )
            assert snapshot_id is not None

            removed = main_module.backend.book_queue.clear_completed(user_id=user["id"])
            assert removed >= 1

            row = _read_activity_log_row(main_module, snapshot_id)
            assert row is not None
            assert row["user_id"] == user["id"]
            assert row["item_key"] == item_key
            assert row["origin"] == "direct"
            assert row["final_status"] == "complete"
            snapshot = json.loads(row["snapshot_json"])
            assert snapshot["kind"] == "download"
            assert snapshot["download"]["id"] == task_id
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_complete_transition_records_requested_origin_for_graduated_request(self, main_module):
        user = _create_user(main_module, prefix="snap-requested")
        task_id = f"requested-{uuid.uuid4().hex[:8]}"
        request_row = main_module.user_db.create_request(
            user_id=user["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={
                "title": "Requested Snapshot",
                "author": "Snapshot Author",
                "provider": "openlibrary",
                "provider_id": "snapshot-req",
            },
            release_data={
                "source": "prowlarr",
                "source_id": task_id,
                "title": "Requested Snapshot.epub",
            },
            status="fulfilled",
            delivery_state="queued",
        )
        task = DownloadTask(
            task_id=task_id,
            source="prowlarr",
            title="Requested Snapshot",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)
            snapshot_id = main_module.activity_service.get_latest_activity_log_id(
                item_type="download",
                item_key=f"download:{task_id}",
            )
            assert snapshot_id is not None

            row = _read_activity_log_row(main_module, snapshot_id)
            assert row is not None
            assert row["origin"] == "requested"
            assert row["request_id"] == request_row["id"]
            assert row["source_id"] == task_id
            snapshot = json.loads(row["snapshot_json"])
            assert snapshot["download"]["id"] == task_id
            assert snapshot["request"]["id"] == request_row["id"]
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_complete_transition_snapshot_uses_latest_terminal_status_message(self, main_module):
        user = _create_user(main_module, prefix="snap-message")
        task_id = f"message-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Message Snapshot",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            # Simulate a stale in-progress message that used to leak into history snapshots.
            main_module.backend.book_queue.update_status_message(task_id, "Moving file")
            main_module.backend.update_download_status(task_id, "complete", "Complete")

            snapshot_id = main_module.activity_service.get_latest_activity_log_id(
                item_type="download",
                item_key=f"download:{task_id}",
            )
            assert snapshot_id is not None

            row = _read_activity_log_row(main_module, snapshot_id)
            assert row is not None
            snapshot = json.loads(row["snapshot_json"])
            assert snapshot["download"]["status_message"] == "Complete"
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_complete_transition_triggers_download_complete_notification(self, main_module):
        user = _create_user(main_module, prefix="snap-notify-complete")
        task_id = f"notify-complete-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Notify Complete Snapshot",
            author="Notify Author",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module, "notify_admin") as mock_notify:
                with patch.object(main_module, "notify_user") as mock_notify_user:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)

            mock_notify.assert_called_once()
            event, context = mock_notify.call_args.args
            assert event == NotificationEvent.DOWNLOAD_COMPLETE
            assert context.title == "Notify Complete Snapshot"
            assert context.author == "Notify Author"
            assert context.username == user["username"]
            mock_notify_user.assert_called_once()
            user_id, user_event, user_context = mock_notify_user.call_args.args
            assert user_id == user["id"]
            assert user_event == NotificationEvent.DOWNLOAD_COMPLETE
            assert user_context.title == "Notify Complete Snapshot"
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_error_transition_triggers_download_failed_notification(self, main_module):
        user = _create_user(main_module, prefix="snap-notify-error")
        task_id = f"notify-error-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Notify Error Snapshot",
            author="Notify Error Author",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            main_module.backend.book_queue.update_status_message(task_id, "Resolver timed out")
            with patch.object(main_module, "notify_admin") as mock_notify:
                with patch.object(main_module, "notify_user") as mock_notify_user:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.ERROR)

            mock_notify.assert_called_once()
            event, context = mock_notify.call_args.args
            assert event == NotificationEvent.DOWNLOAD_FAILED
            assert context.title == "Notify Error Snapshot"
            assert context.error_message == "Resolver timed out"
            mock_notify_user.assert_called_once()
            user_id, user_event, user_context = mock_notify_user.call_args.args
            assert user_id == user["id"]
            assert user_event == NotificationEvent.DOWNLOAD_FAILED
            assert user_context.error_message == "Resolver timed out"
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_cancelled_transition_does_not_trigger_notification(self, main_module):
        user = _create_user(main_module, prefix="snap-notify-cancel")
        task_id = f"notify-cancel-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Notify Cancel Snapshot",
            author="Notify Cancel Author",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module, "notify_admin") as mock_notify:
                with patch.object(main_module, "notify_user") as mock_notify_user:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.CANCELLED)

            mock_notify.assert_not_called()
            mock_notify_user.assert_not_called()
        finally:
            main_module.backend.book_queue.cancel_download(task_id)
