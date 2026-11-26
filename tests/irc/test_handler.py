"""What the IRC download handler joins and where it sends the request line."""

from threading import Event
from unittest.mock import MagicMock

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.irc import handler as irc_handler


def test_download_with_hash_prefixed_channel_setting_uses_the_bare_channel(monkeypatch):
    """A stored "#ebooks" sent the download request to "##ebooks", where no bot answers.

    The handler reads IRC_CHANNEL itself, so it needs the same normalisation as search.
    """
    client = MagicMock()
    client.wait_for_dcc.return_value = None
    manager = MagicMock()
    manager.get_connection.return_value = client
    monkeypatch.setattr(irc_handler, "connection_manager", manager)
    values = {"IRC_SERVER": "irc.example.test", "IRC_CHANNEL": "#ebooks", "IRC_NICK": "reader"}
    monkeypatch.setattr(irc_handler.config, "get", lambda key, default="": values.get(key, default))
    task = DownloadTask(task_id="!bot Author - Title.epub", source="irc", title="t")

    irc_handler.IRCDownloadHandler().download(task, Event(), lambda _p: None, lambda _s, _m: None)

    assert manager.get_connection.call_args.kwargs["channel"] == "ebooks"
    client.send_message.assert_called_once_with("#ebooks", "!bot Author - Title.epub")


def _download_channel(monkeypatch, values, content_type):
    """Run a download that gets no DCC offer and return the channel it asked in."""
    client = MagicMock()
    client.wait_for_dcc.return_value = None
    manager = MagicMock()
    manager.get_connection.return_value = client
    monkeypatch.setattr(irc_handler, "connection_manager", manager)
    monkeypatch.setattr(irc_handler.config, "get", lambda key, default="": values.get(key, default))
    task = DownloadTask(
        task_id="!bot Author - Title.m4b", source="irc", title="t", content_type=content_type
    )

    irc_handler.IRCDownloadHandler().download(task, Event(), lambda _p: None, lambda _s, _m: None)

    channel = manager.get_connection.call_args.kwargs["channel"]
    client.send_message.assert_called_once_with(f"#{channel}", "!bot Author - Title.m4b")
    return channel


def test_audiobook_download_asks_in_the_audiobook_channel(monkeypatch):
    """Search sends an audiobook to IRC_AUDIOBOOK_CHANNEL; the download asked in IRC_CHANNEL (#1753)."""
    values = {
        "IRC_SERVER": "irc.example.test",
        "IRC_CHANNEL": "ebooks",
        "IRC_AUDIOBOOK_CHANNEL": "#bookz",
        "IRC_NICK": "reader",
    }

    assert _download_channel(monkeypatch, values, "audiobook") == "bookz"


def test_ebook_download_stays_in_the_main_channel_when_an_audiobook_channel_is_set(monkeypatch):
    values = {
        "IRC_SERVER": "irc.example.test",
        "IRC_CHANNEL": "ebooks",
        "IRC_AUDIOBOOK_CHANNEL": "bookz",
        "IRC_NICK": "reader",
    }

    assert _download_channel(monkeypatch, values, "book (fiction)") == "ebooks"


def test_audiobook_download_uses_the_main_channel_without_an_audiobook_channel(monkeypatch):
    values = {"IRC_SERVER": "irc.example.test", "IRC_CHANNEL": "ebooks", "IRC_NICK": "reader"}

    assert _download_channel(monkeypatch, values, "audiobook") == "ebooks"


def test_download_holds_the_connection_lock_while_requesting_the_file(monkeypatch):
    """A search and a download on one socket steal each other's replies, so they take turns."""
    from shelfmark.release_sources.irc.connection_manager import connection_manager

    held: list[bool] = []
    client = MagicMock()
    client.wait_for_dcc.return_value = None

    def get_connection(**_kwargs):
        held.append(connection_manager.io_lock.locked())
        return client

    monkeypatch.setattr(connection_manager, "get_connection", get_connection)
    monkeypatch.setattr(connection_manager, "release_connection", lambda _client: None)
    values = {"IRC_SERVER": "irc.example.test", "IRC_CHANNEL": "ebooks", "IRC_NICK": "reader"}
    monkeypatch.setattr(irc_handler.config, "get", lambda key, default="": values.get(key, default))
    task = DownloadTask(task_id="!bot Author - Title.epub", source="irc", title="t")

    irc_handler.IRCDownloadHandler().download(task, Event(), lambda _p: None, lambda _s, _m: None)

    assert held == [True]
    assert not connection_manager.io_lock.locked()


def test_download_releases_the_connection_lock_when_it_fails(monkeypatch):
    from shelfmark.release_sources.irc.connection_manager import connection_manager

    def get_connection(**_kwargs):
        msg = "connection refused"
        raise OSError(msg)

    monkeypatch.setattr(connection_manager, "get_connection", get_connection)
    values = {"IRC_SERVER": "irc.example.test", "IRC_CHANNEL": "ebooks", "IRC_NICK": "reader"}
    monkeypatch.setattr(irc_handler.config, "get", lambda key, default="": values.get(key, default))
    task = DownloadTask(task_id="!bot Author - Title.epub", source="irc", title="t")

    irc_handler.IRCDownloadHandler().download(task, Event(), lambda _p: None, lambda _s, _m: None)

    assert not connection_manager.io_lock.locked()


def test_download_frees_the_connection_lock_before_the_transfer(monkeypatch):
    from shelfmark.release_sources.irc.connection_manager import connection_manager

    held: list[bool] = []
    client = MagicMock()
    offer = MagicMock(filename="Book.epub")
    client.wait_for_dcc.return_value = offer
    monkeypatch.setattr(connection_manager, "get_connection", lambda **_: client)
    monkeypatch.setattr(connection_manager, "release_connection", lambda _c: None)
    monkeypatch.setattr(
        irc_handler, "download_dcc", lambda **_: held.append(connection_manager.io_lock.locked())
    )
    values = {"IRC_SERVER": "irc.example.test", "IRC_CHANNEL": "ebooks", "IRC_NICK": "reader"}
    monkeypatch.setattr(irc_handler.config, "get", lambda key, default="": values.get(key, default))
    task = DownloadTask(task_id="!bot Author - Title.epub", source="irc", title="t")

    irc_handler.IRCDownloadHandler().download(task, Event(), lambda _p: None, lambda _s, _m: None)

    assert held == [False]
    assert not connection_manager.io_lock.locked()
