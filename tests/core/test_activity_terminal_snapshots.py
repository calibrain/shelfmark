"""Tests for terminal activity snapshot capture from queue transitions."""

from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace
from unittest.mock import ANY, patch

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


def _read_download_history_row(main_module, task_id: str):
    conn = main_module.user_db._connect()
    try:
        return conn.execute(
            "SELECT * FROM download_history WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()


class TestTerminalSnapshotCapture:
    def test_book_targeted_queue_creates_import_activity_before_completion(self, main_module):
        owner = _create_user(main_module, prefix="import-activity-owner")
        book = main_module.library_service.upsert_book_from_metadata(
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
        task = DownloadTask(
            task_id=f"import-activity-{uuid.uuid4().hex[:8]}",
            source="prowlarr",
            source_release_key="prowlarr:shared-source",
            title=book["title"],
            user_id=owner["id"],
            username=owner["username"],
            library_book_id=book["id"],
            download_path="/library/import-activity.epub",
        )

        assert main_module.backend.book_queue.add(task) is True
        try:
            activity = main_module.import_activity_service.get_by_task_id(task.task_id)
            assert activity is not None
            assert activity["state"] == "matching"
            assert activity["book_snapshot"]["title"] == book["title"]

            main_module.backend.book_queue.update_status(task.task_id, QueueStatus.COMPLETE)
            history = _read_download_history_row(main_module, task.task_id)
            assert history["import_activity_id"] == activity["id"]
            assert (
                main_module.import_activity_service.get_by_task_id(task.task_id)["state"]
                == "completed"
            )
        finally:
            main_module.backend.book_queue.cancel_download(task.task_id)

    def test_complete_library_download_notifies_fulfilled_requester(self, main_module):
        requester = main_module.user_db.create_user(
            username=f"snap-requester-{uuid.uuid4().hex[:8]}",
            email="requester@example.com",
            library_capability="request-only",
        )
        main_module.user_db.update_personal_preferences(requester["id"], notifications_enabled=True)
        book = main_module.library_service.upsert_book_from_metadata(
            metadata_provider="hardcover",
            provider_book_id=uuid.uuid4().hex,
            title="Requested Availability Snapshot",
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
        main_module.library_service.add_to_library(user_id=requester["id"], book_id=book["id"])
        main_module.user_db.create_library_request(user_id=requester["id"], book_id=book["id"])
        task = DownloadTask(
            task_id=f"request-notify-{uuid.uuid4().hex[:8]}",
            source="library_activity",
            title=book["title"],
            user_id=requester["id"],
            username=requester["username"],
            library_book_id=book["id"],
            download_path="/library/requested.epub",
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module, "notify_user") as mock_notify:
                main_module.backend.book_queue.update_status(task.task_id, QueueStatus.COMPLETE)

            mock_notify.assert_called_once()
            user_db, user_id, event, context = mock_notify.call_args.args
            assert user_db is main_module.user_db
            assert user_id == requester["id"]
            assert event == NotificationEvent.REQUEST_FULFILLED
            assert context.title == book["title"]
            assert context.book_id == book["id"]
        finally:
            main_module.backend.book_queue.cancel_download(task.task_id)

    def test_complete_library_download_emits_availability_after_finalization(self, main_module):
        owner = _create_user(main_module, prefix="snap-availability-owner")
        member = _create_user(main_module, prefix="snap-availability-member")
        outsider = _create_user(main_module, prefix="snap-availability-outsider")
        book = main_module.library_service.upsert_book_from_metadata(
            metadata_provider="hardcover",
            provider_book_id=uuid.uuid4().hex,
            title="Availability Snapshot",
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
        main_module.library_service.add_to_library(user_id=owner["id"], book_id=book["id"])
        main_module.library_service.add_to_library(user_id=member["id"], book_id=book["id"])
        task_id = f"availability-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Availability Snapshot",
            user_id=owner["id"],
            username=owner["username"],
            library_book_id=book["id"],
            download_path="/library/availability.epub",
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)

                    # The event is an invalidation signal only after files are queryable.
                    files = main_module.library_service.get_files_on_disk(book["id"])
                    assert [file["download_path"] for file in files] == [
                        "/library/availability.epub"
                    ]

            payload = {"book_id": book["id"], "task_id": task_id, "availability": "available"}
            mock_emit.assert_any_call("library_book_availability", payload, to="admins")
            mock_emit.assert_any_call(
                "library_book_availability", payload, to=f"user_{owner['id']}"
            )
            mock_emit.assert_any_call(
                "library_book_availability", payload, to=f"user_{member['id']}"
            )
            assert all(
                call.args[0] != "library_book_availability"
                or call.kwargs["to"] != f"user_{outsider['id']}"
                for call in mock_emit.call_args_list
            )
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    @pytest.mark.parametrize("status", [QueueStatus.ERROR, QueueStatus.CANCELLED])
    def test_non_complete_library_download_does_not_emit_availability(self, main_module, status):
        user = _create_user(main_module, prefix="snap-availability-terminal")
        task_id = f"availability-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Availability Terminal Snapshot",
            user_id=user["id"],
            username=user["username"],
            library_book_id=1,
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    main_module.backend.book_queue.update_status(task_id, status)

            assert not any(
                call.args[0] == "library_book_availability" for call in mock_emit.call_args_list
            )
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_pathless_complete_library_download_does_not_emit_availability(self, main_module):
        user = _create_user(main_module, prefix="snap-availability-pathless")
        task_id = f"availability-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Availability Pathless Snapshot",
            user_id=user["id"],
            username=user["username"],
            library_book_id=1,
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)

            assert not any(
                call.args[0] == "library_book_availability" for call in mock_emit.call_args_list
            )
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_unrecorded_complete_library_download_does_not_emit_availability(self, main_module):
        user = _create_user(main_module, prefix="snap-availability-unrecorded")
        book = main_module.library_service.upsert_book_from_metadata(
            metadata_provider="hardcover",
            provider_book_id=uuid.uuid4().hex,
            title="Unrecorded Availability Snapshot",
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
        main_module.library_service.add_to_library(user_id=user["id"], book_id=book["id"])
        task = DownloadTask(
            task_id=f"unrecorded-{uuid.uuid4().hex[:8]}",
            source="direct_download",
            title="Unrecorded Availability Snapshot",
            user_id=user["id"],
            username=user["username"],
            library_book_id=book["id"],
            download_path="/library/unrecorded.epub",
        )

        with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
            with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                main_module._record_download_terminal_snapshot(
                    task.task_id, QueueStatus.COMPLETE, task
                )

        assert not any(
            call.args[0] == "library_book_availability" for call in mock_emit.call_args_list
        )

    def test_complete_transition_records_direct_snapshot(self, main_module):
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
            row = _read_download_history_row(main_module, task_id)
            assert row is not None

            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["user_id"] == user["id"]
            assert row["task_id"] == task_id
            assert row["origin"] == "direct"
            assert row["final_status"] == "complete"
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

            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["status_message"] == "Complete"
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
            mock_notify_user.assert_not_called()
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_complete_transition_emits_activity_update_to_owner_and_admin_rooms(self, main_module):
        user = _create_user(main_module, prefix="snap-activity-update")
        task_id = f"activity-update-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Activity Update Snapshot",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
                with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                    main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)

            mock_emit.assert_any_call(
                "activity_update",
                ANY,
                to="admins",
            )
            mock_emit.assert_any_call(
                "activity_update",
                ANY,
                to=f"user_{user['id']}",
            )
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
            mock_notify_user.assert_not_called()
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_queue_hook_records_active_row_at_queue_time(self, main_module):
        user = _create_user(main_module, prefix="snap-queue")
        task_id = f"queue-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Queue Time Snapshot",
            author="Queue Author",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["final_status"] == "active"
            assert row["user_id"] == user["id"]
            assert row["task_id"] == task_id
            assert row["origin"] == "direct"
            assert row["title"] == "Queue Time Snapshot"
            assert row["author"] == "Queue Author"
            assert row["queued_at"] is not None
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_finalize_updates_active_row_to_terminal(self, main_module):
        user = _create_user(main_module, prefix="snap-finalize")
        task_id = f"finalize-{uuid.uuid4().hex[:8]}"
        task = DownloadTask(
            task_id=task_id,
            source="direct_download",
            title="Finalize Snapshot",
            user_id=user["id"],
            username=user["username"],
        )
        assert main_module.backend.book_queue.add(task) is True

        try:
            # Verify active row exists
            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["final_status"] == "active"

            # Transition to complete
            main_module.backend.book_queue.update_status(task_id, QueueStatus.COMPLETE)

            row = _read_download_history_row(main_module, task_id)
            assert row is not None
            assert row["final_status"] == "complete"
            # Metadata from queue-time should be preserved
            assert row["title"] == "Finalize Snapshot"
            assert row["user_id"] == user["id"]
        finally:
            main_module.backend.book_queue.cancel_download(task_id)

    def test_queue_hook_emits_activity_update_when_requeue_clears_view_state(self, main_module):
        user = _create_user(main_module, prefix="snap-reset")
        task_id = f"reset-{uuid.uuid4().hex[:8]}"
        main_module.activity_view_state_service.dismiss(
            viewer_scope=f"user:{user['id']}",
            item_type="download",
            item_key=f"download:{task_id}",
        )

        task = SimpleNamespace(
            user_id=user["id"],
            username=user["username"],
            request_id=None,
            source="direct_download",
            title="Reset Snapshot",
            author="Reset Author",
            format="epub",
            size="1 MB",
            preview=None,
            content_type="ebook",
        )

        with patch.object(main_module.ws_manager, "is_enabled", return_value=True):
            with patch.object(main_module.ws_manager.socketio, "emit") as mock_emit:
                main_module._record_download_queued(task_id, task)

        mock_emit.assert_any_call(
            "activity_update",
            ANY,
            to="admins",
        )
        mock_emit.assert_any_call(
            "activity_update",
            ANY,
            to=f"user_{user['id']}",
        )

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
