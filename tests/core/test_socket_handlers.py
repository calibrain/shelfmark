"""Characterization tests for the Socket.IO handlers, written before they moved out of main.py.

They pin what a client sees on connect, on a manual status request and on disconnect, and how
the room bookkeeping is called, for no auth, an admin, a plain user and an anonymous visitor.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


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


def _socket(main_module, client):
    return main_module.socketio.test_client(main_module.app, flask_test_client=client)


def _events(socket) -> list[tuple[str, dict]]:
    return [(item["name"], item["args"][0]) for item in socket.get_received()]


_STATUS = {"queued": {"task-1": {"title": "A Book", "source": "direct_download"}}}


class TestConnect:
    def test_no_auth_joins_as_admin_and_gets_the_whole_status(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_status", return_value=_STATUS) as status:
                with patch.object(main_module.ws_manager, "join_user_room") as join:
                    with patch.object(main_module.ws_manager, "client_connected") as connected:
                        socket = _socket(main_module, client)
                        events = _events(socket)
                        socket.disconnect()

        assert events == [("status_update", _STATUS)]
        status.assert_called_once_with(user_id=None)
        connected.assert_called_once_with()
        assert join.call_args.kwargs == {"is_admin": True, "db_user_id": None}

    def test_an_admin_gets_the_whole_status(self, main_module, client):
        _login(client, user_id="root", db_user_id=None, is_admin=True)
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=_STATUS):
                with patch.object(main_module.ws_manager, "join_user_room") as join:
                    socket = _socket(main_module, client)
                    events = _events(socket)
                    socket.disconnect()

        assert events == [("status_update", _STATUS)]
        assert join.call_args.kwargs == {"is_admin": True, "db_user_id": None}

    def test_a_user_gets_their_own_status_with_release_details_hidden(self, main_module, client):
        _login(client, user_id="alice", db_user_id=7)
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=_STATUS) as status:
                with patch.object(main_module, "redact_status", return_value={"redacted": {}}):
                    with patch.object(main_module.ws_manager, "join_user_room") as join:
                        socket = _socket(main_module, client)
                        events = _events(socket)
                        socket.disconnect()

        assert events == [("status_update", {"redacted": {}})]
        status.assert_called_once_with(user_id=7)
        assert join.call_args.kwargs == {"is_admin": False, "db_user_id": 7}

    def test_a_visitor_with_no_session_gets_an_empty_status(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status") as status:
                with patch.object(main_module.ws_manager, "join_user_room"):
                    socket = _socket(main_module, client)
                    events = _events(socket)
                    socket.disconnect()

        assert events == [("status_update", {})]
        status.assert_not_called()

    def test_a_failure_reading_the_status_sends_nothing_and_keeps_the_connection(
        self, main_module, client
    ):
        _login(client, user_id="root", db_user_id=None, is_admin=True)
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(
                main_module.backend, "queue_status", side_effect=RuntimeError("broke")
            ):
                with patch.object(main_module.ws_manager, "join_user_room"):
                    socket = _socket(main_module, client)
                    events = _events(socket)
                    connected = socket.is_connected()
                    socket.disconnect()

        assert events == []
        assert connected is True


class TestRequestStatus:
    def test_a_manual_request_syncs_the_room_and_answers_with_the_status(self, main_module, client):
        _login(client, user_id="root", db_user_id=None, is_admin=True)
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=_STATUS):
                with patch.object(main_module.ws_manager, "join_user_room"):
                    socket = _socket(main_module, client)
                    socket.get_received()
                    with patch.object(main_module.ws_manager, "sync_user_room") as sync:
                        socket.emit("request_status")
                        events = _events(socket)
                    socket.disconnect()

        assert events == [("status_update", _STATUS)]
        assert sync.call_args.kwargs == {"is_admin": True, "db_user_id": None}

    def test_a_visitor_with_no_session_gets_an_empty_status(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.ws_manager, "join_user_room"):
                socket = _socket(main_module, client)
                socket.get_received()
                with patch.object(main_module.ws_manager, "sync_user_room"):
                    socket.emit("request_status")
                    events = _events(socket)
                socket.disconnect()

        assert events == [("status_update", {})]

    def test_a_failure_answers_with_an_error_event(self, main_module, client):
        _login(client, user_id="root", db_user_id=None, is_admin=True)
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            with patch.object(main_module.backend, "queue_status", return_value=_STATUS):
                with patch.object(main_module.ws_manager, "join_user_room"):
                    socket = _socket(main_module, client)
                    socket.get_received()
                    with patch.object(
                        main_module.backend, "queue_status", side_effect=RuntimeError("broke")
                    ):
                        with patch.object(main_module.ws_manager, "sync_user_room"):
                            socket.emit("request_status")
                            events = _events(socket)
                    socket.disconnect()

        assert events == [("error", {"message": "Failed to get status"})]


class TestDisconnect:
    def test_leaves_the_room_and_counts_the_disconnect(self, main_module, client):
        with patch.object(main_module, "get_auth_mode", return_value="none"):
            with patch.object(main_module.backend, "queue_status", return_value={}):
                with patch.object(main_module.ws_manager, "join_user_room"):
                    socket = _socket(main_module, client)
                    with patch.object(main_module.ws_manager, "leave_user_room") as leave:
                        with patch.object(
                            main_module.ws_manager, "client_disconnected"
                        ) as disconnected:
                            socket.disconnect()

        leave.assert_called_once()
        assert isinstance(leave.call_args.args[0], str) and leave.call_args.args[0]
        disconnected.assert_called_once_with()
