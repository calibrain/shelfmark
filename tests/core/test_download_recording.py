"""Pin the download recording hooks that main.py registers on the download queue."""

from __future__ import annotations

import importlib
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core import download_recording
from shelfmark.core.models import QueueStatus
from shelfmark.core.notifications import NotificationEvent
from shelfmark.download import orchestrator as backend


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


def _recording():
    """The module that holds the recording code (notify_*, reopen_failed_request live there)."""
    return download_recording


def _queued_hook():
    hook = backend.book_queue._queue_hook
    assert hook is not None
    return hook


def _terminal_hook():
    hook = backend.book_queue._terminal_status_hook
    assert hook is not None
    return hook


def _task(**overrides):
    values = {
        "user_id": 7,
        "username": "alice",
        "request_id": None,
        "source": "direct_download",
        "title": "A Title",
        "author": "An Author",
        "format": "epub",
        "size": "1 MB",
        "preview": "http://x/p.jpg",
        "content_type": "ebook",
        "downloads": 3,
        "status_message": None,
        "download_path": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def services(main_module):
    history = MagicMock()
    view_state = MagicMock()
    view_state.clear_item_for_all_viewers.return_value = 0
    user_db = MagicMock()
    with (
        patch.object(main_module, "download_history_service", history),
        patch.object(main_module, "activity_view_state_service", view_state),
        patch.object(main_module, "user_db", user_db),
        patch.object(backend, "serialize_task_for_retry", return_value={"k": "v"}),
    ):
        yield SimpleNamespace(history=history, view_state=view_state, user_db=user_db)


@pytest.fixture
def ws_emit(main_module):
    with (
        patch.object(main_module.ws_manager, "is_enabled", return_value=True),
        patch.object(main_module.ws_manager.socketio, "emit") as emit,
    ):
        yield emit


class TestHookRegistration:
    def test_hooks_are_registered_on_the_book_queue(self, main_module):
        assert callable(backend.book_queue._queue_hook)
        assert callable(backend.book_queue._terminal_status_hook)


class TestQueuedRecord:
    def test_direct_task_is_recorded_with_all_fields(self, main_module, services):
        _queued_hook()("t1", _task())

        services.history.record_download.assert_called_once_with(
            task_id="t1",
            user_id=7,
            username="alice",
            request_id=None,
            source="direct_download",
            source_display_name=services.history.record_download.call_args.kwargs[
                "source_display_name"
            ],
            title="A Title",
            author="An Author",
            file_format="epub",
            size="1 MB",
            preview="http://x/p.jpg",
            content_type="ebook",
            downloads=3,
            origin="direct",
            retry_payload={"k": "v"},
        )

    def test_request_linked_task_has_requested_origin(self, main_module, services):
        _queued_hook()("t2", _task(request_id=42))

        kwargs = services.history.record_download.call_args.kwargs
        assert kwargs["origin"] == "requested"
        assert kwargs["request_id"] == 42

    def test_missing_title_and_bad_user_id_are_normalised(self, main_module, services):
        _queued_hook()("t3", _task(title="", user_id="bogus", author="  "))

        kwargs = services.history.record_download.call_args.kwargs
        assert kwargs["title"] == "Unknown title"
        assert kwargs["user_id"] is None
        assert kwargs["author"] is None

    def test_no_history_service_is_a_noop(self, main_module, services):
        with patch.object(main_module, "download_history_service", None):
            _queued_hook()("t4", _task())

        services.view_state.clear_item_for_all_viewers.assert_not_called()

    def test_record_failure_is_swallowed_and_skips_view_state_reset(self, main_module, services):
        services.history.record_download.side_effect = sqlite3.OperationalError("locked")

        _queued_hook()("t5", _task())

        services.view_state.clear_item_for_all_viewers.assert_not_called()

    def test_unexpected_error_type_propagates(self, main_module, services):
        services.history.record_download.side_effect = KeyError("boom")

        with pytest.raises(KeyError):
            _queued_hook()("t6", _task())

    def test_no_view_state_service_skips_reset(self, main_module, services, ws_emit):
        with patch.object(main_module, "activity_view_state_service", None):
            _queued_hook()("t7", _task())

        services.history.record_download.assert_called_once()
        ws_emit.assert_not_called()

    def test_view_state_cleared_for_download_and_request(self, main_module, services, ws_emit):
        services.view_state.clear_item_for_all_viewers.return_value = 1

        _queued_hook()("t8", _task(request_id=42))

        calls = services.view_state.clear_item_for_all_viewers.call_args_list
        assert [c.kwargs for c in calls] == [
            {"item_type": "download", "item_key": "download:t8"},
            {"item_type": "request", "item_key": "request:42"},
        ]
        rooms = [c.kwargs["to"] for c in ws_emit.call_args_list]
        assert rooms == ["admins", "user_7"]
        assert ws_emit.call_args_list[0].args[0] == "activity_update"
        assert ws_emit.call_args_list[0].args[1] == {"kind": "activity_reset", "task_id": "t8"}

    def test_nothing_cleared_means_no_ws_event(self, main_module, services, ws_emit):
        _queued_hook()("t9", _task())

        ws_emit.assert_not_called()

    def test_ownerless_task_only_notifies_admins(self, main_module, services, ws_emit):
        services.view_state.clear_item_for_all_viewers.return_value = 1

        _queued_hook()("t10", _task(user_id=None))

        assert [c.kwargs["to"] for c in ws_emit.call_args_list] == ["admins"]

    def test_view_state_failure_is_swallowed(self, main_module, services, ws_emit):
        services.view_state.clear_item_for_all_viewers.side_effect = RuntimeError("x")

        _queued_hook()("t11", _task())

        ws_emit.assert_not_called()


class TestTerminalSnapshot:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (QueueStatus.COMPLETE, "complete"),
            (QueueStatus.ERROR, "error"),
            (QueueStatus.CANCELLED, "cancelled"),
        ],
    )
    def test_each_terminal_status_finalizes_history(
        self, main_module, services, ws_emit, status, expected
    ):
        task = _task(
            status_message="  msg ",
            download_path="/tmp/x.epub",
            download_identities={"/tmp/x.epub": "1:2:3"},
        )
        with (
            patch.object(_recording(), "notify_admin"),
            patch.object(_recording(), "notify_user"),
            patch.object(backend, "can_retry_download_task", return_value=True),
        ):
            _terminal_hook()("t1", status, task)

        services.history.finalize_download.assert_called_once_with(
            task_id="t1",
            final_status=expected,
            status_message="msg",
            download_path="/tmp/x.epub",
            download_identity="1:2:3",
            retry_payload={"k": "v"},
        )
        assert [c.kwargs["to"] for c in ws_emit.call_args_list] == ["admins", "user_7"]
        assert ws_emit.call_args_list[0].args[1] == {
            "kind": "download_terminal",
            "task_id": "t1",
            "status": expected,
        }

    @pytest.mark.parametrize(
        "status",
        [QueueStatus.QUEUED, QueueStatus.DOWNLOADING, QueueStatus.RESOLVING],
    )
    def test_non_terminal_status_is_ignored(self, main_module, services, ws_emit, status):
        with (
            patch.object(_recording(), "notify_admin") as notify_admin,
            patch.object(_recording(), "notify_user") as notify_user,
        ):
            _terminal_hook()("t1", status, _task())

        services.history.finalize_download.assert_not_called()
        notify_admin.assert_not_called()
        notify_user.assert_not_called()
        ws_emit.assert_not_called()

    def test_finalize_failure_is_swallowed_and_emits_nothing(self, main_module, services, ws_emit):
        services.history.finalize_download.side_effect = sqlite3.OperationalError("locked")
        with patch.object(_recording(), "notify_admin"), patch.object(_recording(), "notify_user"):
            _terminal_hook()("t1", QueueStatus.COMPLETE, _task())

        ws_emit.assert_not_called()

    def test_no_history_service_still_notifies_but_emits_nothing(
        self, main_module, services, ws_emit
    ):
        with (
            patch.object(main_module, "download_history_service", None),
            patch.object(_recording(), "notify_admin") as notify_admin,
            patch.object(_recording(), "notify_user"),
        ):
            _terminal_hook()("t1", QueueStatus.COMPLETE, _task())

        notify_admin.assert_called_once()
        ws_emit.assert_not_called()

    def test_unexpected_finalize_error_propagates(self, main_module, services):
        services.history.finalize_download.side_effect = KeyError("boom")
        with patch.object(_recording(), "notify_admin"), patch.object(_recording(), "notify_user"):
            with pytest.raises(KeyError):
                _terminal_hook()("t1", QueueStatus.COMPLETE, _task())


class TestNotifications:
    def test_complete_notifies_admin_and_owner(self, main_module, services):
        task = _task(title="T", author="A", format="epub", content_type="ebook")
        with (
            patch.object(_recording(), "notify_admin") as notify_admin,
            patch.object(_recording(), "notify_user") as notify_user,
        ):
            _terminal_hook()("t1", QueueStatus.COMPLETE, task)

        event, context = notify_admin.call_args.args
        assert event == NotificationEvent.DOWNLOAD_COMPLETE
        assert (context.title, context.author, context.username) == ("T", "A", "alice")
        assert context.format == "epub"
        assert context.error_message is None
        user_id, user_event, user_context = notify_user.call_args.args
        assert (user_id, user_event) == (7, NotificationEvent.DOWNLOAD_COMPLETE)
        assert user_context is context

    def test_error_carries_the_error_message(self, main_module, services):
        task = _task(status_message="disk full")
        with (
            patch.object(_recording(), "notify_admin") as notify_admin,
            patch.object(_recording(), "notify_user"),
            patch.object(backend, "can_retry_download_task", return_value=True),
        ):
            _terminal_hook()("t1", QueueStatus.ERROR, task)

        event, context = notify_admin.call_args.args
        assert event == NotificationEvent.DOWNLOAD_FAILED
        assert context.error_message == "disk full"

    def test_cancelled_sends_no_notification(self, main_module, services):
        with (
            patch.object(_recording(), "notify_admin") as notify_admin,
            patch.object(_recording(), "notify_user") as notify_user,
        ):
            _terminal_hook()("t1", QueueStatus.CANCELLED, _task())

        notify_admin.assert_not_called()
        notify_user.assert_not_called()

    def test_ownerless_task_notifies_admin_only(self, main_module, services):
        with (
            patch.object(_recording(), "notify_admin") as notify_admin,
            patch.object(_recording(), "notify_user") as notify_user,
        ):
            _terminal_hook()("t1", QueueStatus.COMPLETE, _task(user_id=None))

        notify_admin.assert_called_once()
        notify_user.assert_not_called()

    def test_unparseable_owner_id_notifies_admin_only(self, main_module, services):
        with (
            patch.object(_recording(), "notify_admin") as notify_admin,
            patch.object(_recording(), "notify_user") as notify_user,
        ):
            _terminal_hook()("t1", QueueStatus.COMPLETE, _task(user_id="nope"))

        notify_admin.assert_called_once()
        notify_user.assert_not_called()

    def test_missing_fields_fall_back_to_unknown(self, main_module, services):
        with (
            patch.object(_recording(), "notify_admin") as notify_admin,
            patch.object(_recording(), "notify_user"),
        ):
            _terminal_hook()("t1", QueueStatus.COMPLETE, SimpleNamespace())

        context = notify_admin.call_args.args[1]
        assert context.title == "Unknown title"
        assert context.author == "Unknown author"
        assert context.content_type is None

    def test_admin_notify_failure_does_not_stop_user_notify_or_finalize(
        self, main_module, services
    ):
        with (
            patch.object(_recording(), "notify_admin", side_effect=RuntimeError("down")),
            patch.object(_recording(), "notify_user") as notify_user,
        ):
            _terminal_hook()("t1", QueueStatus.COMPLETE, _task())

        notify_user.assert_called_once()
        services.history.finalize_download.assert_called_once()

    def test_user_notify_failure_is_swallowed(self, main_module, services):
        with (
            patch.object(_recording(), "notify_admin"),
            patch.object(_recording(), "notify_user", side_effect=ValueError("bad")),
        ):
            _terminal_hook()("t1", QueueStatus.COMPLETE, _task())

        services.history.finalize_download.assert_called_once()

    def test_unexpected_notify_error_propagates(self, main_module, services):
        with (
            patch.object(_recording(), "notify_admin", side_effect=KeyError("boom")),
            patch.object(_recording(), "notify_user"),
            pytest.raises(KeyError),
        ):
            _terminal_hook()("t1", QueueStatus.COMPLETE, _task())


class TestReopenAfterTerminalError:
    def _run(self, main_module, services, *, task, can_retry=False, status=QueueStatus.ERROR):
        emitted = MagicMock()
        with (
            patch.object(_recording(), "notify_admin"),
            patch.object(_recording(), "notify_user"),
            patch.object(backend, "can_retry_download_task", return_value=can_retry),
            patch.object(
                _recording(), "reopen_failed_request", return_value={"id": task.request_id}
            ) as reopen,
            patch.object(main_module, "_emit_request_update_events", emitted),
        ):
            _terminal_hook()("t1", status, task)
        return reopen, emitted

    def test_error_on_request_linked_task_reopens_and_broadcasts(self, main_module, services):
        task = _task(request_id=42, status_message=" kaput ")
        reopen, emitted = self._run(main_module, services, task=task)

        reopen.assert_called_once_with(services.user_db, request_id=42, failure_reason="kaput")
        services.view_state.clear_item_for_all_viewers.assert_called_once_with(
            item_type="request", item_key="request:42"
        )
        emitted.assert_called_once_with([{"id": 42}])

    def test_blank_message_uses_default_reason(self, main_module, services):
        reopen, _ = self._run(main_module, services, task=_task(request_id=42, status_message=" "))

        assert reopen.call_args.kwargs["failure_reason"] == "Download failed"

    def test_retryable_task_is_not_reopened(self, main_module, services):
        reopen, emitted = self._run(
            main_module, services, task=_task(request_id=42), can_retry=True
        )

        reopen.assert_not_called()
        emitted.assert_not_called()

    def test_task_without_request_is_not_reopened(self, main_module, services):
        reopen, _ = self._run(main_module, services, task=_task(request_id=None))

        reopen.assert_not_called()

    def test_complete_is_not_reopened(self, main_module, services):
        reopen, _ = self._run(
            main_module, services, task=_task(request_id=42), status=QueueStatus.COMPLETE
        )

        reopen.assert_not_called()

    def test_no_user_db_is_not_reopened(self, main_module, services):
        with patch.object(main_module, "user_db", None):
            reopen, _ = self._run(main_module, services, task=_task(request_id=42))

        reopen.assert_not_called()

    def test_reopen_returning_none_broadcasts_nothing(self, main_module, services):
        task = _task(request_id=42)
        emitted = MagicMock()
        with (
            patch.object(_recording(), "notify_admin"),
            patch.object(_recording(), "notify_user"),
            patch.object(backend, "can_retry_download_task", return_value=False),
            patch.object(_recording(), "reopen_failed_request", return_value=None),
            patch.object(main_module, "_emit_request_update_events", emitted),
        ):
            _terminal_hook()("t1", QueueStatus.ERROR, task)

        emitted.assert_not_called()
        services.view_state.clear_item_for_all_viewers.assert_not_called()

    def test_reopen_failure_is_swallowed(self, main_module, services):
        with (
            patch.object(_recording(), "notify_admin"),
            patch.object(_recording(), "notify_user"),
            patch.object(backend, "can_retry_download_task", return_value=False),
            patch.object(
                _recording(), "reopen_failed_request", side_effect=sqlite3.OperationalError("x")
            ),
        ):
            _terminal_hook()("t1", QueueStatus.ERROR, _task(request_id=42))


def test_history_gets_the_identity_recorded_for_the_delivered_path():
    _delivered_identity = download_recording._delivered_identity
    task = SimpleNamespace(
        download_path="/books/a.epub",
        download_identities={"/books/a.epub": "1:2:3", "/books/b.epub": "4:5:6"},
    )
    assert _delivered_identity(task) == "1:2:3"
    assert _delivered_identity(SimpleNamespace(download_path=None, download_identities={})) is None
    assert _delivered_identity(SimpleNamespace(download_path="/books/c.epub")) is None
