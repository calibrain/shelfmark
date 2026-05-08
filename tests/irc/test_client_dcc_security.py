from ipaddress import IPv4Address

from shelfmark.release_sources.irc.client import IRCClient, IRCEvent, IRCMessage


def _dcc_raw(sender: str, filename: str, ip: str, port: int = 443) -> str:
    ip_int = int(IPv4Address(ip))
    return f':{sender}!user@example.test PRIVMSG reader :\x01DCC SEND "{filename}" {ip_int} {port} 1\x01'


def test_wait_for_dcc_ignores_unexpected_sender(monkeypatch) -> None:
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    client.online_servers = {"BookBot"}

    messages = [
        IRCMessage(
            raw=_dcc_raw("Mallory", "evil.epub", "8.8.8.8"),
            prefix="Mallory!user@example.test",
            event=IRCEvent.BOOK_RESULT,
        ),
        IRCMessage(
            raw=_dcc_raw("BookBot", "book.epub", "8.8.8.8"),
            prefix="BookBot!user@example.test",
            event=IRCEvent.BOOK_RESULT,
        ),
    ]
    monkeypatch.setattr(client, "read_messages", lambda: iter(messages))

    offer = client.wait_for_dcc(timeout=1.0, result_type=False)

    assert offer is not None
    assert offer.filename == "book.epub"


def test_wait_for_dcc_uses_expected_sender_over_online_server_list(monkeypatch) -> None:
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    client.online_servers = {"OtherBot"}

    messages = [
        IRCMessage(
            raw=_dcc_raw("BookBot", "book.epub", "8.8.8.8"),
            prefix="BookBot!user@example.test",
            event=IRCEvent.BOOK_RESULT,
        )
    ]
    monkeypatch.setattr(client, "read_messages", lambda: iter(messages))

    offer = client.wait_for_dcc(
        timeout=1.0,
        result_type=False,
        expected_senders={"BookBot"},
    )

    assert offer is not None
    assert offer.filename == "book.epub"


def test_wait_for_dcc_ignores_unsafe_offer_and_keeps_waiting(monkeypatch) -> None:
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    client.online_servers = {"BookBot"}

    messages = [
        IRCMessage(
            raw=_dcc_raw("BookBot", "../outside.epub", "8.8.8.8"),
            prefix="BookBot!user@example.test",
            event=IRCEvent.BOOK_RESULT,
        ),
        IRCMessage(
            raw=_dcc_raw("BookBot", "internal.epub", "127.0.0.1"),
            prefix="BookBot!user@example.test",
            event=IRCEvent.BOOK_RESULT,
        ),
        IRCMessage(
            raw=_dcc_raw("BookBot", "book.epub", "8.8.8.8"),
            prefix="BookBot!user@example.test",
            event=IRCEvent.BOOK_RESULT,
        ),
    ]
    monkeypatch.setattr(client, "read_messages", lambda: iter(messages))

    offer = client.wait_for_dcc(timeout=1.0, result_type=False)

    assert offer is not None
    assert offer.filename == "book.epub"
