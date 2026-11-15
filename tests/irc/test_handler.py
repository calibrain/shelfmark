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
