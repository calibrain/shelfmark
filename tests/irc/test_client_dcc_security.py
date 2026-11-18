from ipaddress import IPv4Address

from shelfmark.release_sources.irc.client import (
    IRCClient,
    IRCEvent,
    IRCMessage,
    results_file_matches_query,
)


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
    monkeypatch.setattr(client, "read_messages", lambda **_: iter(messages))

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
    monkeypatch.setattr(client, "read_messages", lambda **_: iter(messages))

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
    monkeypatch.setattr(client, "read_messages", lambda **_: iter(messages))

    offer = client.wait_for_dcc(timeout=1.0, result_type=False)

    assert offer is not None
    assert offer.filename == "book.epub"


def _results_offer(filename: str) -> IRCMessage:
    return IRCMessage(
        raw=_dcc_raw("SearchBot", filename, "8.8.8.8"),
        prefix="SearchBot!user@example.test",
        event=IRCEvent.SEARCH_RESULT,
    )


def test_wait_for_dcc_skips_a_results_file_made_for_another_query(monkeypatch) -> None:
    """A late file for an earlier search must not be taken as this search's answer."""
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    client.online_servers = {"SearchBot"}
    messages = [
        _results_offer("SearchBot_results_for__Skandar_and_the_Chaos_Trials_Steadman.txt.zip"),
        _results_offer("SearchBot_results_for__Skandar_and_the_Phantom_Rider_Steadman.txt.zip"),
    ]
    monkeypatch.setattr(client, "read_messages", lambda **_: iter(messages))

    offer = client.wait_for_dcc(
        timeout=1.0,
        result_type=True,
        expected_query="Skandar and the Phantom Rider Steadman",
    )

    assert offer is not None
    assert "Phantom_Rider" in offer.filename


def test_wait_for_dcc_without_an_expected_query_takes_the_first_results_file(monkeypatch) -> None:
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    client.online_servers = {"SearchBot"}
    messages = [_results_offer("SearchBot_results_for__Anything.txt.zip")]
    monkeypatch.setattr(client, "read_messages", lambda **_: iter(messages))

    offer = client.wait_for_dcc(timeout=1.0, result_type=True)

    assert offer is not None


def test_results_file_matches_query() -> None:
    def name(query: str) -> str:
        return f"SearchBot_results_for__{query.replace(' ', '_')}.txt.zip"

    short = "Skandar and the Secret Element Steadman"
    long = "Skandar and the Secret Element: World Book Day 2026 Steadman"
    assert results_file_matches_query(name(short), short)
    assert results_file_matches_query(name(long), long)  # punctuation does not matter
    assert not results_file_matches_query(name(short), long)
    assert not results_file_matches_query(name(long), short)
    # Results for a longer title are not the answer to a search for its start.
    assert not results_file_matches_query(name("Dune Messiah"), "Dune")
    assert results_file_matches_query(name("三体"), "三体")  # any script
    # A name that is not a results file has nothing to compare against.
    assert results_file_matches_query("Author - Book.epub", short)


def test_recv_lines_marks_the_client_disconnected_when_the_peer_closes() -> None:
    class ClosedSocket:
        def gettimeout(self) -> None:
            return None

        def recv(self, _size: int) -> bytes:
            return b""

    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    client._socket = ClosedSocket()  # type: ignore[assignment]
    client._connected = True

    assert list(client._recv_lines()) == []
    assert not client.is_connected
