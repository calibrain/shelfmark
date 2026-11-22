from ipaddress import IPv4Address

from shelfmark.release_sources.irc.client import (
    IRCClient,
    IRCEvent,
    IRCMessage,
    notice_query,
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
    client._stale_queries.add("Anne of Green Gables Montgomery")  # its wait timed out
    messages = [
        _results_offer("SearchBot_results_for__Anne_of_Green_Gables_Montgomery.txt.zip"),
        _results_offer("SearchBot_results_for__Anne_of_Avonlea_Montgomery.txt.zip"),
    ]
    monkeypatch.setattr(client, "read_messages", lambda **_: iter(messages))

    offer = client.wait_for_dcc(
        timeout=1.0,
        result_type=True,
        expected_query="Anne of Avonlea Montgomery",
    )

    assert offer is not None
    assert "Avonlea" in offer.filename


def test_wait_for_dcc_accepts_a_results_file_named_differently_from_the_query(monkeypatch) -> None:
    """An accent the bot dropped or replaced is not a late answer to anything."""
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    client.online_servers = {"SearchBot"}
    messages = [_results_offer("SearchBot_results_for__Les_Mis_rables_Hugo.txt.zip")]
    monkeypatch.setattr(client, "read_messages", lambda **_: iter(messages))

    offer = client.wait_for_dcc(timeout=1.0, result_type=True, expected_query="Les Misérables Hugo")

    assert offer is not None


def test_wait_for_dcc_skips_a_late_no_results_notice_for_an_earlier_query(monkeypatch) -> None:
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    client.online_servers = {"SearchBot"}
    client._stale_queries.add("Old Title")
    messages = [
        IRCMessage(
            raw="",
            prefix="SearchBot!x@y",
            command="NOTICE",
            params=["reader"],
            trailing='\x031,9\x02<<SearchBot>>\x02 Sorry, your search for "\x0312,09Old Title\x0301,09" returned \x02no\x02 matches.',
            event=IRCEvent.NO_RESULTS,
        ),
        _results_offer("SearchBot_results_for__New_Title.txt.zip"),
    ]
    monkeypatch.setattr(client, "read_messages", lambda **_: iter(messages))

    offer = client.wait_for_dcc(timeout=1.0, result_type=True, expected_query="New Title")

    assert offer is not None


def test_wait_for_dcc_skips_a_late_notice_for_a_query_containing_this_one(monkeypatch) -> None:
    """A late "Sorry" for "Emma Austen" is not the answer to "Emma", though one contains the other."""
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    client.online_servers = {"SearchBot"}
    client._stale_queries.add("Emma Austen")
    messages = [
        IRCMessage(
            raw="",
            prefix="SearchBot!x@y",
            command="NOTICE",
            params=["reader"],
            trailing='Sorry, your search for "\x0312,09Emma Austen\x0301,09" returned no matches.',
            event=IRCEvent.NO_RESULTS,
        ),
        _results_offer("SearchBot_results_for__Emma.txt.zip"),
    ]
    monkeypatch.setattr(client, "read_messages", lambda **_: iter(messages))

    offer = client.wait_for_dcc(timeout=1.0, result_type=True, expected_query="Emma")

    assert offer is not None


def test_wait_for_dcc_raises_when_the_bot_denies_the_search(monkeypatch) -> None:
    """A denied search was never run, so it must not come back as "no results"."""
    import pytest

    from shelfmark.release_sources.irc.client import IRCError

    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    denied = IRCMessage(
        raw=":Search!Search@host NOTICE reader :x",
        prefix="Search!Search@host",
        command="NOTICE",
        params=["reader"],
        # Seen on irchighway, typo included.
        trailing="Search results already waiting to be recieved. Search denied.",
    )
    denied.event = client._classify_event(denied)
    monkeypatch.setattr(client, "read_messages", lambda **_: iter([denied]))

    with pytest.raises(IRCError, match="Search denied"):
        client.wait_for_dcc(timeout=1.0, result_type=True, expected_query="Q")


def test_notice_query_reads_the_query_through_colour_codes() -> None:
    real = (
        "\x031,9\x16\x02<<SearchBot>>\x02\x16 Sorry, your search for "
        '"\x0312,091984 Orwell\x0301,09" returned \x02no\x02 matches. Search took 1.25 seconds.'
    )
    assert notice_query(real) == "1984 Orwell"
    assert notice_query("Sorry, the bot is busy") is None


def test_wait_for_dcc_timeout_marks_the_query_stale(monkeypatch) -> None:
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    monkeypatch.setattr(client, "read_messages", lambda **_: iter([]))

    assert client.wait_for_dcc(timeout=0, result_type=True, expected_query="Q") is None
    assert client.last_wait_timed_out
    assert "Q" in client._stale_queries


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

    short = "The Secret Garden Burnett"
    long = "The Secret Garden: Centenary Edition Burnett"
    assert results_file_matches_query(name(short), short)
    assert results_file_matches_query(name(long), long)  # punctuation does not matter
    assert not results_file_matches_query(name(short), long)
    assert not results_file_matches_query(name(long), short)
    # Results for a longer title are not the answer to a search for its start.
    assert not results_file_matches_query(name("Emma Austen"), "Emma")
    assert results_file_matches_query(name("三体"), "三体")  # any script
    # A name that is not a results file has nothing to compare against.
    assert results_file_matches_query("Author - Book.epub", short)
    assert results_file_matches_query(name("Les Miserables Hugo"), "Les Misérables Hugo")
    # The real bot's name for this query, seen on irchighway.
    assert results_file_matches_query(
        "SearchBot_results_for__Les_Mis_rables_Hugo.txt.zip", "Les Misérables Hugo"
    )
    assert results_file_matches_query(name("Les Mise\u0301rables Hugo"), "Les Misérables Hugo")


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
