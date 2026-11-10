"""Offline end-to-end test for the Chomikuj download handler.

Unlike ``test_handler.py``, which patches ``requests.get``, this module runs the
real HTTP client against a real loopback ``ThreadingHTTPServer``. That proves
the wire-level header contract the live service depends on:

* the mobile API ``User-Agent`` and ``Accept: */*``;
* no ``Range`` header (a ranged request gets ``206 text/html`` from the CDN);
* the server actually receives the request (not a cache or a proxy);
* the streamed bytes land unchanged in ``TMP_DIR``.

Only the Chomikuj API calls are patched (no credentials, no network): the URL
resolution returns a loopback URL, so all I/O stays on ``127.0.0.1``.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.chomikuj import handler as handler_module
from shelfmark.release_sources.chomikuj.client import DEFAULT_USER_AGENT, ChomikujClient
from shelfmark.release_sources.chomikuj.handler import ChomikujHandler

API_KEY = "test-api-key"
PASSWORD = "test-password"
FILE_ID = "123456"
RESPONSE_NAME = "Książka - Autor.pdf"
HTML_MESSAGE = "Chomikuj returned an HTML page instead of a book"

# 20 000 non-repeating bytes: a truncation or offset bug changes the content.
PDF_BODY = bytes(range(256)) * 78 + bytes(range(32))


class _LoopbackServer:
    """Serve one fixed ``(body, content-type)`` pair and record every request."""

    def __init__(self, *, body: bytes, content_type: str) -> None:
        self.body = body
        self.content_type = content_type
        self.requests: list[dict[str, object]] = []
        self._lock = threading.Lock()
        owner = self

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                with owner._lock:
                    owner.requests.append({"path": self.path, "headers": dict(self.headers)})
                self.send_response(200)
                self.send_header("Content-Type", owner.content_type)
                self.send_header("Content-Length", str(len(owner.body)))
                self.end_headers()
                try:
                    self.wfile.write(owner.body)
                except OSError:
                    # The handler may close an HTML response before reading it.
                    pass

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port: int = self._httpd.server_address[1]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="chomikuj-loopback-server",
            daemon=True,
        )

    @property
    def file_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/book.pdf"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
        assert not self._thread.is_alive(), "loopback server thread leaked"


@pytest.fixture(autouse=True)
def _force_loopback(monkeypatch):
    """Keep requests on the loopback interface even on a proxied dev machine."""
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture
def loopback_server():
    """Start loopback servers on demand and always shut them down."""
    servers: list[_LoopbackServer] = []

    def start(*, body: bytes, content_type: str) -> _LoopbackServer:
        server = _LoopbackServer(body=body, content_type=content_type)
        server.start()
        servers.append(server)
        return server

    yield start

    for server in servers:
        server.stop()


def _task(server: _LoopbackServer) -> DownloadTask:
    return DownloadTask(
        task_id=f"chomikuj:{FILE_ID}",
        source="chomikuj",
        title="Książka",
        author="Autor",
        year="2020",
        format="pdf",
        retry_source_context={
            "file_id": FILE_ID,
            "size_bytes": len(server.body),
            "is_free_for_user": True,
        },
    )


def _run_download(tmp_path, server: _LoopbackServer):
    """Run the real handler with only the Chomikuj API boundary patched."""
    statuses: list[tuple[str, str | None]] = []
    progress: list[float] = []

    def status_callback(state: str, message: str | None) -> None:
        statuses.append((state, message))

    def progress_callback(value: float) -> None:
        progress.append(value)

    payload = {"FileUrl": server.file_url, "Name": RESPONSE_NAME}
    account = {
        "HasUnlimitedTransfer": False,
        "Transfer": 1_000_000,
        "BalanceInfo": "1 MB",
    }
    settings = {
        "FILE_ORGANIZATION": "none",
        "CHOMIKUJ_ALLOW_TRANSFER_SPEND": True,
    }

    with (
        patch.object(handler_module, "TMP_DIR", tmp_path),
        patch.object(handler_module, "get_credentials", return_value=("reader", PASSWORD)),
        patch.object(
            handler_module,
            "get_base_url",
            return_value="https://mobile.chomikuj.pl/api/v3",
        ),
        patch.object(
            handler_module,
            "get_session",
            return_value=SimpleNamespace(api_key=API_KEY),
        ),
        patch.object(ChomikujClient, "download_url", return_value=payload) as download_url,
        patch.object(ChomikujClient, "account_info", return_value=account),
        patch.object(
            handler_module.config,
            "get",
            side_effect=lambda key, default=None: settings.get(key, default),
        ),
        patch(
            "shelfmark.download.network.get_proxies",
            return_value={},
        ) as get_proxies,
        patch("shelfmark.download.network.get_ssl_verify", return_value=True),
    ):
        result = ChomikujHandler().download(
            _task(server),
            threading.Event(),
            progress_callback,
            status_callback,
        )

    return SimpleNamespace(
        result=result,
        statuses=statuses,
        progress=progress,
        download_url=download_url,
        get_proxies=get_proxies,
    )


def test_pdf_download_streams_real_http_with_proven_headers(tmp_path, loopback_server):
    server = loopback_server(body=PDF_BODY, content_type="application/pdf")

    run = _run_download(tmp_path, server)

    expected = tmp_path / RESPONSE_NAME
    assert run.result == str(expected)
    assert expected.read_bytes() == PDF_BODY
    assert len(server.requests) == 1, "the loopback server was never hit"
    request = server.requests[0]
    assert request["path"] == "/book.pdf"

    headers = {
        str(name).lower(): str(value)
        for name, value in dict(request["headers"]).items()  # type: ignore[arg-type]
    }
    assert headers["user-agent"] == DEFAULT_USER_AGENT
    assert headers["accept"] == "*/*"
    assert "range" not in headers

    run.download_url.assert_called_once_with(API_KEY, FILE_ID)
    run.get_proxies.assert_called_once_with(server.file_url)
    assert ("downloading", "") in run.statuses
    assert run.progress[-1] == 100.0


def test_html_response_is_rejected_and_no_file_is_written(tmp_path, loopback_server):
    server = loopback_server(
        body=b"<html><body>human verification</body></html>",
        content_type="text/html; charset=utf-8",
    )

    run = _run_download(tmp_path, server)

    assert run.result is None
    assert not list(tmp_path.iterdir())
    assert ("error", HTML_MESSAGE) in run.statuses
    assert len(server.requests) == 1, "the loopback server was never hit"
    assert server.requests[0]["path"] == "/book.pdf"
    run.download_url.assert_called_once_with(API_KEY, FILE_ID)
