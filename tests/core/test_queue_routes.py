"""Characterization tests for the queue routes, written before they moved out of main.py.

They pin what the routes do today: the status codes, the bodies and who may do what. The
guardrail tests cover the owner and non-owner cases for cancel, retry, priority and reorder,
so these fill in the rest: the local download route, the admin paths, the validation errors
and the failure responses.
"""

from __future__ import annotations

import importlib
import uuid
import zipfile
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core import queue_routes
from shelfmark.core.delivered_files import file_identity
from shelfmark.core.models import DownloadTask
from shelfmark.core.viewer_redaction import viewer_download_id


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _login(client, *, user_id="alice", db_user_id: int | None = 7, is_admin=False) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["is_admin"] = is_admin
        if db_user_id is not None:
            sess["db_user_id"] = db_user_id


def _create_user(main_module, *, prefix: str) -> dict:
    return main_module.user_db.create_user(username=f"{prefix}-{uuid.uuid4().hex[:8]}", role="user")


def _task(task_id="task-1", *, user=None, request_id=None) -> DownloadTask:
    return DownloadTask(
        task_id=task_id,
        source="direct_download",
        title="A Book",
        author="An Author",
        user_id=user["id"] if user else None,
        username=user["username"] if user else None,
        request_id=request_id,
    )


def _delivered(task: DownloadTask, *paths) -> DownloadTask:
    """Record the files as delivered, the way the queue does when a download finishes."""
    task.download_identities = {str(path): file_identity(path) for path in paths}
    return task


def _builtin(main_module):
    return patch.object(main_module, "get_auth_mode", return_value="builtin")


class TestLocalDownload:
    def test_missing_id_is_a_400(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            resp = client.get("/api/localdownload")

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "No book ID provided"}

    def test_requires_a_login(self, main_module, client):
        with _builtin(main_module):
            resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 401

    def test_unknown_book_is_a_404_when_history_has_nothing(self, main_module, client):
        _login(client, is_admin=True)
        history = MagicMock()
        history.get_by_task_id.return_value = None
        with _builtin(main_module):
            with patch.object(main_module.backend, "get_book_paths", return_value=([], None)):
                with patch.object(main_module, "download_history_service", history):
                    resp = client.get("/api/localdownload?id=gone")

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "File not found"}

    def test_admin_gets_the_file_as_an_attachment(self, main_module, client, tmp_path):
        book = tmp_path / "book.epub"
        book.write_bytes(b"epub-bytes")
        _login(client, is_admin=True)
        task = _delivered(_task(), book)
        with _builtin(main_module):
            with patch.object(
                main_module.backend, "get_book_paths", return_value=([str(book)], task)
            ):
                resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 200
        assert resp.data == b"epub-bytes"
        assert resp.headers["Content-Disposition"].startswith("attachment")
        assert resp.headers["Content-Length"] == str(len(b"epub-bytes"))

    def test_a_range_request_gets_part_of_the_file(self, main_module, client, tmp_path):
        book = tmp_path / "book.epub"
        book.write_bytes(b"0123456789")
        _login(client, is_admin=True)
        task = _delivered(_task(), book)
        with _builtin(main_module):
            with patch.object(
                main_module.backend, "get_book_paths", return_value=([str(book)], task)
            ):
                resp = client.get("/api/localdownload?id=task-1", headers={"Range": "bytes=2-5"})

        assert resp.status_code == 206
        assert resp.data == b"2345"
        assert resp.headers["Content-Range"] == "bytes 2-5/10"

    def test_owner_gets_the_file(self, main_module, client, tmp_path):
        book = tmp_path / "book.epub"
        book.write_bytes(b"mine")
        user = _create_user(main_module, prefix="reader")
        _login(client, user_id=user["username"], db_user_id=user["id"])
        with _builtin(main_module):
            with patch.object(
                main_module.backend,
                "get_book_paths",
                return_value=([str(book)], _delivered(_task(user=user), book)),
            ):
                resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 200
        assert resp.data == b"mine"

    def test_non_owner_gets_a_404_not_a_403(self, main_module, client, tmp_path):
        book = tmp_path / "book.epub"
        book.write_bytes(b"theirs")
        owner = _create_user(main_module, prefix="owner")
        other = _create_user(main_module, prefix="other")
        _login(client, user_id=other["username"], db_user_id=other["id"])
        with _builtin(main_module):
            with patch.object(
                main_module.backend,
                "get_book_paths",
                return_value=([str(book)], _task(user=owner)),
            ):
                resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "File not found"}

    def test_several_files_come_back_as_one_zip(self, main_module, client, tmp_path):
        first = tmp_path / "01.mp3"
        second = tmp_path / "02.mp3"
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(
                main_module.backend,
                "get_book_paths",
                return_value=([str(first), str(second)], _delivered(_task(), first, second)),
            ):
                resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 200
        assert resp.mimetype == "application/zip"
        assert resp.headers["Content-Disposition"].startswith("attachment")
        with zipfile.ZipFile(BytesIO(resp.data)) as archive:
            assert sorted(archive.namelist()) == ["01.mp3", "02.mp3"]

    def test_a_finished_download_is_served_from_history_to_its_owner(
        self, main_module, client, tmp_path
    ):
        book = tmp_path / "old.epub"
        book.write_bytes(b"from-history")
        user = _create_user(main_module, prefix="reader")
        _login(client, user_id=user["username"], db_user_id=user["id"])
        history = MagicMock()
        history.get_by_task_id.return_value = {
            "user_id": user["id"],
            "download_path": str(book),
            "download_identity": file_identity(book),
        }
        with _builtin(main_module):
            with patch.object(main_module.backend, "get_book_paths", return_value=([], None)):
                with patch.object(main_module, "download_history_service", history):
                    resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 200
        assert resp.data == b"from-history"

    def test_a_history_entry_with_no_recorded_file_is_a_404(self, main_module, client, tmp_path):
        # Rows written before identities were recorded have none, and are not sent.
        book = tmp_path / "old.epub"
        book.write_bytes(b"from-history")
        _login(client, is_admin=True)
        history = MagicMock()
        history.get_by_task_id.return_value = {"user_id": 7, "download_path": str(book)}
        with _builtin(main_module):
            with patch.object(main_module.backend, "get_book_paths", return_value=([], None)):
                with patch.object(main_module, "download_history_service", history):
                    resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 404

    def test_a_history_path_that_now_holds_another_file_is_a_404(
        self, main_module, client, tmp_path
    ):
        book = tmp_path / "old.epub"
        book.write_bytes(b"what was delivered")
        delivered = file_identity(book)
        replacement = tmp_path / "other.epub"
        replacement.write_bytes(b"something else entirely")
        replacement.replace(book)
        _login(client, is_admin=True)
        history = MagicMock()
        history.get_by_task_id.return_value = {
            "user_id": 7,
            "download_path": str(book),
            "download_identity": delivered,
        }
        with _builtin(main_module):
            with patch.object(main_module.backend, "get_book_paths", return_value=([], None)):
                with patch.object(main_module, "download_history_service", history):
                    resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 404

    def test_a_live_file_replaced_after_delivery_is_a_404(self, main_module, client, tmp_path):
        book = tmp_path / "book.epub"
        book.write_bytes(b"delivered")
        task = _delivered(_task(), book)
        book.write_bytes(b"rewritten after the download finished")
        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(
                main_module.backend, "get_book_paths", return_value=([str(book)], task)
            ):
                resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "File not found"}

    def test_a_zip_is_refused_when_any_part_was_not_delivered(self, main_module, client, tmp_path):
        first = tmp_path / "01.mp3"
        second = tmp_path / "02.mp3"
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        task = _delivered(_task(), first)
        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(
                main_module.backend,
                "get_book_paths",
                return_value=([str(first), str(second)], task),
            ):
                resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 404

    def test_a_history_entry_of_someone_else_is_a_404(self, main_module, client, tmp_path):
        book = tmp_path / "old.epub"
        book.write_bytes(b"not-yours")
        user = _create_user(main_module, prefix="reader")
        _login(client, user_id=user["username"], db_user_id=user["id"])
        history = MagicMock()
        history.get_by_task_id.return_value = {
            "user_id": user["id"] + 1000,
            "download_path": str(book),
        }
        with _builtin(main_module):
            with patch.object(main_module.backend, "get_book_paths", return_value=([], None)):
                with patch.object(main_module, "download_history_service", history):
                    resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 404

    def test_a_read_failure_returns_a_fixed_message(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(
                main_module.backend,
                "get_book_paths",
                side_effect=OSError("/srv/secret/path.epub is unreadable"),
            ):
                resp = client.get("/api/localdownload?id=task-1")

        assert resp.status_code == 500
        assert resp.get_json() == {"error": "Could not read the file"}


class TestCancel:
    def test_unknown_task_is_a_404(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=None):
                resp = client.delete("/api/download/nope/cancel")

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Failed to cancel download or book not found"}

    def test_admin_can_cancel_anyones_download(self, main_module, client):
        owner = _create_user(main_module, prefix="owner")
        _login(client, user_id="root", db_user_id=None, is_admin=True)
        with _builtin(main_module):
            with patch.object(
                main_module.backend.book_queue, "get_task", return_value=_task(user=owner)
            ):
                with patch.object(
                    main_module.backend, "cancel_download", return_value=True
                ) as cancel:
                    resp = client.delete("/api/download/task-1/cancel")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "cancelled", "book_id": "task-1"}
        cancel.assert_called_once_with("task-1")

    def test_a_cancel_the_backend_refuses_is_a_404(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=_task()):
                with patch.object(main_module.backend, "cancel_download", return_value=False):
                    resp = client.delete("/api/download/task-1/cancel")

        assert resp.status_code == 404

    def test_a_user_without_a_database_identity_is_a_403(self, main_module, client):
        _login(client, user_id="ghost", db_user_id=None)
        with _builtin(main_module):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=_task()):
                resp = client.delete("/api/download/task-1/cancel")

        assert resp.status_code == 403
        assert resp.get_json() == {
            "error": "User identity unavailable",
            "code": "user_identity_unavailable",
        }

    def test_a_failure_in_the_backend_is_a_500_with_a_fixed_message(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(
                main_module.backend.book_queue, "get_task", side_effect=RuntimeError("queue broke")
            ):
                resp = client.delete("/api/download/task-1/cancel")

        assert resp.status_code == 500
        assert resp.get_json() == {"error": "Could not cancel the download"}

    def test_the_opaque_id_a_viewer_sees_cancels_the_real_task(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _login(client, user_id=user["username"], db_user_id=user["id"])
        real_id = "21:https://tracker.example/t/1"
        viewer_id = viewer_download_id(real_id, 5)
        assert viewer_id != real_id
        task = _task(real_id, user=user, request_id=None)
        live = MagicMock()
        live.get_status.return_value = {
            "queued": {real_id: _task(real_id, user=user, request_id=5)}
        }
        with _builtin(main_module):
            with patch.object(main_module.backend, "book_queue", live):
                live.get_task.return_value = task
                with patch.object(main_module, "download_history_service", None):
                    with patch.object(
                        main_module.backend, "cancel_download", return_value=True
                    ) as cancel:
                        resp = client.delete(f"/api/download/{viewer_id}/cancel")

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "cancelled", "book_id": viewer_id}
        cancel.assert_called_once_with(real_id)


class TestPriority:
    def test_a_missing_body_is_a_400(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            resp = client.put("/api/queue/task-1/priority", json={})

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Priority not provided"}

    def test_a_priority_that_is_not_a_number_is_a_400(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            resp = client.put("/api/queue/task-1/priority", json={"priority": "high"})

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Invalid priority value"}

    def test_a_user_without_a_database_identity_is_a_403(self, main_module, client):
        _login(client, user_id="ghost", db_user_id=None)
        with _builtin(main_module):
            resp = client.put("/api/queue/task-1/priority", json={"priority": 1})

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "user_identity_unavailable"

    def test_unknown_task_is_a_404(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(main_module.backend.book_queue, "get_task", return_value=None):
                resp = client.put("/api/queue/nope/priority", json={"priority": 1})

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Failed to update priority or book not found"}

    def test_admin_can_set_anyones_priority(self, main_module, client):
        owner = _create_user(main_module, prefix="owner")
        _login(client, user_id="root", db_user_id=None, is_admin=True)
        with _builtin(main_module):
            with patch.object(
                main_module.backend.book_queue, "get_task", return_value=_task(user=owner)
            ):
                with patch.object(
                    main_module.backend, "set_book_priority", return_value=True
                ) as set_priority:
                    resp = client.put("/api/queue/task-1/priority", json={"priority": "3"})

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "updated", "book_id": "task-1", "priority": 3}
        set_priority.assert_called_once_with("task-1", 3)


class TestReorder:
    def test_a_missing_body_is_a_400(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            resp = client.post("/api/queue/reorder", json={})

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "book_priorities not provided"}

    def test_priorities_that_are_not_a_dict_are_a_400(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            resp = client.post("/api/queue/reorder", json={"book_priorities": [1, 2]})

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "book_priorities must be a dictionary"}

    def test_a_priority_that_is_not_an_integer_names_the_book(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            resp = client.post("/api/queue/reorder", json={"book_priorities": {"a": "1"}})

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Invalid priority for book a"}

    def test_admin_reorders_everything_it_names(self, main_module, client):
        _login(client, user_id="root", db_user_id=None, is_admin=True)
        with _builtin(main_module):
            with patch.object(main_module.backend, "reorder_queue", return_value=True) as reorder:
                resp = client.post("/api/queue/reorder", json={"book_priorities": {"a": 0, "b": 1}})

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "reordered", "updated_count": 2}
        reorder.assert_called_once_with({"a": 0, "b": 1})

    def test_a_backend_that_refuses_is_a_500(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(main_module.backend, "reorder_queue", return_value=False):
                resp = client.post("/api/queue/reorder", json={"book_priorities": {"a": 0}})

        assert resp.status_code == 500
        assert resp.get_json() == {"error": "Failed to reorder queue"}

    def test_a_non_admin_skips_ids_that_name_no_task(self, main_module, client):
        user = _create_user(main_module, prefix="reader")
        _login(client, user_id=user["username"], db_user_id=user["id"])
        mine = _task("mine", user=user)
        with _builtin(main_module):
            with patch.object(
                main_module.backend.book_queue,
                "get_task",
                side_effect=lambda task_id: {"mine": mine}.get(task_id),
            ):
                with patch.object(
                    main_module.backend, "reorder_queue", return_value=True
                ) as reorder:
                    resp = client.post(
                        "/api/queue/reorder", json={"book_priorities": {"mine": 0, "gone": 1}}
                    )

        assert resp.status_code == 200
        assert resp.get_json() == {"status": "reordered", "updated_count": 1}
        reorder.assert_called_once_with({"mine": 0})


class TestQueueViews:
    def test_admin_sees_the_whole_queue_order(self, main_module, client):
        _login(client, user_id="root", db_user_id=None, is_admin=True)
        order = [{"id": "a", "priority": 0}, {"id": "b", "priority": 1}]
        with _builtin(main_module):
            with patch.object(main_module.backend, "get_queue_order", return_value=order):
                resp = client.get("/api/queue/order")

        assert resp.status_code == 200
        assert resp.get_json() == {"queue": order}

    def test_admin_sees_every_active_download(self, main_module, client):
        _login(client, user_id="root", db_user_id=None, is_admin=True)
        with _builtin(main_module):
            with patch.object(main_module.backend, "get_active_downloads", return_value=["a", "b"]):
                resp = client.get("/api/downloads/active")

        assert resp.status_code == 200
        assert resp.get_json() == {"active_downloads": ["a", "b"]}

    def test_both_views_refuse_a_user_without_a_database_identity(self, main_module, client):
        _login(client, user_id="ghost", db_user_id=None)
        with _builtin(main_module):
            order = client.get("/api/queue/order")
            active = client.get("/api/downloads/active")

        assert order.status_code == active.status_code == 403
        assert order.get_json()["code"] == active.get_json()["code"] == "user_identity_unavailable"

    def test_a_failure_is_a_500_with_a_fixed_message(self, main_module, client):
        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(
                main_module.backend, "get_queue_order", side_effect=RuntimeError("order broke")
            ):
                resp = client.get("/api/queue/order")

        assert resp.status_code == 500
        assert resp.get_json() == {"error": "Could not read the queue order"}


class TestStatus:
    def test_a_user_without_a_database_identity_gets_an_empty_object(self, main_module, client):
        _login(client, user_id="ghost", db_user_id=None)
        with _builtin(main_module):
            resp = client.get("/api/status")

        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_delivery_states_are_synced_and_announced_for_the_requests_that_changed(
        self, main_module, client
    ):
        _login(client, user_id="root", db_user_id=None, is_admin=True)
        changed = [
            {
                "id": 4,
                "status": "fulfilled",
                "delivery_state": "complete",
                "user_id": 9,
                "book_data": {"title": "Dune"},
            }
        ]
        with _builtin(main_module):
            with patch.object(main_module.backend, "queue_status", return_value={}):
                with patch(
                    "shelfmark.core.queue_routes.sync_delivery_states_from_queue_status",
                    return_value=changed,
                ):
                    with patch.object(main_module, "emit_ws_event") as emit:
                        resp = client.get("/api/status")

        assert resp.status_code == 200
        rooms = {call.kwargs["room"] for call in emit.call_args_list}
        assert rooms == {"user_9", "admins"}
        payload = emit.call_args_list[0].kwargs["payload"]
        assert payload == {
            "request_id": 4,
            "status": "fulfilled",
            "delivery_state": "complete",
            "title": "Dune",
        }


class TestLocalDownloadClosesFiles:
    def test_a_head_request_for_a_zip_closes_the_opened_files(self, main_module, client, tmp_path):
        first = tmp_path / "01.mp3"
        second = tmp_path / "02.mp3"
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        task = _delivered(_task(), first, second)
        opened = []
        real_open = queue_routes.open_delivered_file

        def recording_open(path, identity):
            handle = real_open(path, identity)
            opened.append(handle)
            return handle

        _login(client, is_admin=True)
        with _builtin(main_module):
            with patch.object(
                main_module.backend,
                "get_book_paths",
                return_value=([str(first), str(second)], task),
            ):
                with patch.object(queue_routes, "open_delivered_file", recording_open):
                    resp = client.head("/api/localdownload?id=task-1")
                    resp.close()

        assert resp.status_code == 200
        assert len(opened) == 2
        assert all(handle.closed for handle in opened)
