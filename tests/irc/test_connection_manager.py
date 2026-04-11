from unittest.mock import MagicMock

import pytest

from shelfmark.release_sources.irc.connection_manager import IRCConnectionManager


@pytest.fixture
def manager():
    IRCConnectionManager._instance = None
    connection_manager = IRCConnectionManager()
    yield connection_manager
    connection_manager._running = False
    IRCConnectionManager._instance = None


def test_cleanup_idle_connections_tolerates_disconnect_error(manager):
    client = MagicMock()
    client.disconnect.side_effect = OSError("socket closed")
    key = manager._connection_key("irc.example.com", 6697, "reader")

    manager._connections[key] = client
    manager._last_used[key] = 0
    manager._channels[key] = "#books"

    manager._cleanup_idle_connections()

    assert key not in manager._connections
    client.disconnect.assert_called_once()


def test_get_connection_clears_connecting_flag_on_failure(manager, monkeypatch):
    class FailingIRCClient:
        def __init__(self, nick, server, port, *, use_tls):
            self.nick = nick
            self.server = server
            self.port = port

        def connect(self):
            raise RuntimeError("connect failed")

    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.IRCClient",
        FailingIRCClient,
    )

    with pytest.raises(RuntimeError, match="connect failed"):
        manager.get_connection(
            server="irc.example.com",
            port=6697,
            nick="reader",
            use_tls=True,
            channel="books",
        )

    key = manager._connection_key("irc.example.com", 6697, "reader")
    assert key not in manager._connecting


def test_close_connection_tolerates_disconnect_error(manager):
    client = MagicMock()
    client.server = "irc.example.com"
    client.port = 6697
    client.nick = "reader"
    client.disconnect.side_effect = RuntimeError("disconnect failed")
    key = manager._connection_key(client.server, client.port, client.nick)

    manager._connections[key] = client
    manager._last_used[key] = 1.0
    manager._channels[key] = "#books"

    manager.close_connection(client)

    assert key not in manager._connections
    client.disconnect.assert_called_once()
