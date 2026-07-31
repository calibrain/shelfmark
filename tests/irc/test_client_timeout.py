"""A silent channel must still time out.

An empty or misnamed channel sends nothing at all, so a timeout that is only
checked when a message arrives never fires and the search hangs forever.
"""

import time

import pytest

from shelfmark.release_sources.irc.client import IRCClient


class _SilentSocket:
    """A socket that never delivers a line, only recv timeouts."""

    def __init__(self) -> None:
        self.timeout: float | None = 300.0
        self.recv_calls = 0

    def gettimeout(self) -> float | None:
        return self.timeout

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def recv(self, _bufsize: int) -> bytes:
        self.recv_calls += 1
        # A real socket blocks for the full timeout before raising; sleep a
        # sliver of it so the test stays fast but the deadline still advances.
        time.sleep(min(self.timeout or 0.0, 0.01))
        raise TimeoutError


def test_wait_for_dcc_times_out_on_silent_channel() -> None:
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    sock = _SilentSocket()
    client._socket = sock
    client._connected = True

    start = time.time()
    offer = client.wait_for_dcc(timeout=0.5, result_type=True)
    elapsed = time.time() - start

    assert offer is None
    assert elapsed == pytest.approx(0.5, abs=0.5)
    assert sock.recv_calls > 0
    # The long connection-wide timeout is put back for the next caller
    assert sock.timeout == 300.0


def test_recv_lines_caps_socket_timeout_at_the_deadline() -> None:
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    sock = _SilentSocket()
    client._socket = sock
    client._connected = True

    lines = list(client._recv_lines(deadline=time.time() + 0.05))

    assert lines == []
    # Never waits past the deadline on a single recv
    assert sock.timeout == 300.0
