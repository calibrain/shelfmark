"""Fetch the e2e platform's mock Prowlarr release through shelfmark's torrent fetch.

The client-* e2e profiles (nightly only) drive real torrent clients from the mock
Prowlarr's one release. Shelfmark fetches a .torrent only from the configured
Prowlarr origin or from public addresses, and every mock lives on a private
address, so a release link that leaves the Prowlarr origin is refused. Deluge
then fails, while qBittorrent and Transmission quietly hand the URL to the client
instead. This runs the real ``extract_torrent_info`` against the mock on localhost
in normal CI, so the link cannot drift off the Prowlarr origin unnoticed.
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

import pytest
import requests

from shelfmark.download.clients.torrent_utils import extract_torrent_info
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_xml

MOCK_PATH = Path(__file__).resolve().parents[1] / "e2e" / "platform" / "mocks" / "mock_services.py"


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    # The Prowlarr download handler fetches the .torrent from this same server.
    daemon_threads = True


@pytest.fixture
def prowlarr_url(monkeypatch):
    """Serve the mock with every role, its Prowlarr reading .torrent files from itself."""
    if not MOCK_PATH.exists():
        pytest.skip(f"platform mock not found at {MOCK_PATH}")
    server = make_server("127.0.0.1", 0, None, server_class=_ThreadingWSGIServer)
    url = f"http://127.0.0.1:{server.server_port}"
    # The module wires its routes and reads its upstream URL at import time. Another
    # host name for this server stands in for mock-aa, a private non-Prowlarr origin.
    monkeypatch.setenv("MOCK_ROLE", "all")
    monkeypatch.setenv("AA_INTERNAL_URL", f"http://localhost:{server.server_port}")
    spec = importlib.util.spec_from_file_location("mock_prowlarr_services", MOCK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server.set_app(module.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield url
    finally:
        server.shutdown()


def test_mock_prowlarr_release_torrent_is_fetchable(prowlarr_url, monkeypatch) -> None:
    config_values = {"PROWLARR_URL": prowlarr_url}
    monkeypatch.setattr(
        "shelfmark.download.clients.torrent_utils.config.get",
        lambda key, default="": config_values.get(key, default),
    )
    search = requests.get(
        f"{prowlarr_url}/api/v1/indexer/1/newznab",
        params={"t": "search", "q": "Moby Dick"},
        timeout=10,
    )
    search.raise_for_status()
    (release,) = parse_torznab_xml(search.text)

    info = extract_torrent_info(release["downloadUrl"])

    assert info.fetch_error is None
    assert info.torrent_data
    assert info.info_hash
