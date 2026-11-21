"""A Hardcover connection that fails to open is tried once more (#1736), but a request
that reached Hardcover is never sent twice, since some GraphQL calls are mutations."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import urllib3.util.connection

from shelfmark.metadata_providers import hardcover
from shelfmark.metadata_providers.hardcover import HardcoverProvider, _TokenBucket

PAT = "hc_pat_" + "a" * 32


class FakeHardcover(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.posts = 0
        self.drop = False


class _Handler(BaseHTTPRequestHandler):
    server: FakeHardcover

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        self.server.posts += 1
        if self.server.drop:
            self.close_connection = True
            return
        body = json.dumps({"data": {"me": [{"id": 1}]}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


class Connects:
    """Counts connection attempts and times out the first ``failures`` of them."""

    def __init__(self) -> None:
        self.attempts = 0
        self.failures = 0


@pytest.fixture
def server(monkeypatch) -> Iterator[FakeHardcover]:
    fake = FakeHardcover()
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    monkeypatch.setattr(
        hardcover, "HARDCOVER_API_URL", f"http://127.0.0.1:{fake.server_port}/v1/graphql"
    )
    monkeypatch.setattr(hardcover, "_rate_limiter", _TokenBucket(5, 1.0))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    yield fake
    fake.shutdown()
    fake.server_close()


@pytest.fixture
def connects(monkeypatch) -> Connects:
    real = urllib3.util.connection.create_connection
    state = Connects()

    def create_connection(address, *args, **kwargs):
        state.attempts += 1
        if state.attempts <= state.failures:
            raise TimeoutError("timed out")
        return real(address, *args, **kwargs)

    monkeypatch.setattr(urllib3.util.connection, "create_connection", create_connection)
    return state


def test_a_connect_that_times_out_is_retried_on_a_new_connection(server, connects):
    connects.failures = 1

    result = HardcoverProvider(api_key=PAT)._execute_query("query { me { id } }", {})

    assert result == {"me": [{"id": 1}]}
    assert connects.attempts == 2
    assert server.posts == 1


def test_gives_up_after_one_retry(server, connects):
    connects.failures = 2

    result = HardcoverProvider(api_key=PAT)._execute_query("query { me { id } }", {})

    assert result is None
    assert connects.attempts == 2
    assert server.posts == 0


def test_a_request_that_reached_hardcover_is_not_sent_again(server, connects):
    server.drop = True

    result = HardcoverProvider(api_key=PAT)._execute_query("mutation { noop }", {})

    assert result is None
    assert server.posts == 1
    assert connects.attempts == 1
