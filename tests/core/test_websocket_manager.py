"""Tests for WebSocket room scoping and SID room synchronization."""

from shelfmark.api import websocket as websocket_module
from shelfmark.api.websocket import WebSocketManager


def test_join_user_room_without_db_user_id_does_not_join_admin_room(monkeypatch):
    joined: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        websocket_module,
        "join_room",
        lambda room, sid=None: joined.append((room, sid)),
    )

    manager = WebSocketManager()
    manager.join_user_room("sid-1", is_admin=False, db_user_id=None)

    assert joined == []
    assert manager._user_rooms == {}


def test_sync_user_room_moves_sid_between_rooms(monkeypatch):
    joined: list[tuple[str, str | None]] = []
    left: list[tuple[str, str | None]] = []

    monkeypatch.setattr(
        websocket_module,
        "join_room",
        lambda room, sid=None: joined.append((room, sid)),
    )
    monkeypatch.setattr(
        websocket_module,
        "leave_room",
        lambda room, sid=None: left.append((room, sid)),
    )

    manager = WebSocketManager()

    manager.sync_user_room("sid-1", is_admin=True, db_user_id=None)
    assert joined == [("admins", "sid-1")]
    assert left == []
    assert manager._user_rooms == {}

    manager.sync_user_room("sid-1", is_admin=False, db_user_id=42)
    assert joined[-1] == ("user_42", "sid-1")
    assert left[-1] == ("admins", "sid-1")
    assert manager._user_rooms == {"user_42": 1}

    manager.leave_user_room("sid-1")
    assert left[-1] == ("user_42", "sid-1")
    assert manager._user_rooms == {}


def test_sync_user_room_tracks_ref_counts_across_multiple_sids(monkeypatch):
    monkeypatch.setattr(websocket_module, "join_room", lambda *_, **__: None)
    monkeypatch.setattr(websocket_module, "leave_room", lambda *_, **__: None)

    manager = WebSocketManager()

    manager.sync_user_room("sid-a", is_admin=False, db_user_id=7)
    manager.sync_user_room("sid-b", is_admin=False, db_user_id=7)
    assert manager._user_rooms == {"user_7": 2}

    manager.sync_user_room("sid-a", is_admin=False, db_user_id=8)
    assert manager._user_rooms == {"user_7": 1, "user_8": 1}

    manager.leave_user_room("sid-a")
    manager.leave_user_room("sid-b")
    assert manager._user_rooms == {}
