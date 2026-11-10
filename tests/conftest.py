"""
Pytest configuration and shared fixtures.
"""

import os
import sys
import tempfile

# Set environment variables BEFORE importing the application
# These override the defaults that try to use system paths like /var/log
_temp_base = tempfile.mkdtemp(prefix="cwabd_test_")

# LOG_ROOT is the base - LOG_DIR is computed as LOG_ROOT / "shelfmark"
# So we set LOG_ROOT to our temp directory to get LOG_DIR = _temp_base/shelfmark
os.environ["LOG_ROOT"] = _temp_base
os.environ["CONFIG_DIR"] = os.path.join(_temp_base, "config")
os.environ["INGEST_DIR"] = os.path.join(_temp_base, "ingest")
os.environ["TMP_DIR"] = os.path.join(_temp_base, "tmp")

# Create the directories that will be used
os.makedirs(os.path.join(_temp_base, "shelfmark"), exist_ok=True)  # LOG_DIR
os.makedirs(os.path.join(_temp_base, "config"), exist_ok=True)
os.makedirs(os.path.join(_temp_base, "ingest"), exist_ok=True)
os.makedirs(os.path.join(_temp_base, "tmp"), exist_ok=True)

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(autouse=True)
def _reset_config_dir_writable_cache():
    """The writable-config-dir probe is remembered; each test starts without it."""
    from shelfmark.config.env import reset_config_dir_writable_cache

    reset_config_dir_writable_cache()
    yield
    reset_config_dir_writable_cache()


@pytest.fixture(autouse=True)
def _fresh_shared_session(monkeypatch):
    """Each test gets its own shared network session, so a patched method cannot leak."""
    from shelfmark.download import network

    monkeypatch.setattr(network, "_shared_session", None)


@pytest.fixture
def sent_requests(monkeypatch):
    """Record what a ``NetworkSession`` hands to ``requests`` after the network rules ran.

    Patches ``requests.Session.request`` itself, below ``NetworkRules``, so a test sees the
    ``proxies`` and ``verify`` the session chose for each URL. Each call is recorded as a
    dict of ``method``, ``url`` and the keyword arguments; ``respond`` sets the response
    (a value, or a callable taking the recorded call), by default an empty 200.
    """
    import requests

    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.respond = None

        def last(self) -> dict:
            """The most recent call."""
            return self.calls[-1]

    recorder = _Recorder()

    def _request(self, method, url, *args, **kwargs):
        call = {"method": method, "url": url, **kwargs}
        recorder.calls.append(call)
        if callable(recorder.respond):
            return recorder.respond(call)
        if recorder.respond is not None:
            return recorder.respond
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.url = url
        return response

    monkeypatch.setattr(requests.Session, "request", _request)
    return recorder


@pytest.fixture(autouse=True)
def _clear_torrent_fetch_cache():
    """Keep the shared torrent fetch cache from leaking between tests."""
    from shelfmark.download.clients.torrent_utils import clear_torrent_fetch_cache

    clear_torrent_fetch_cache()
    yield
    clear_torrent_fetch_cache()


@pytest.fixture(autouse=True)
def _public_destination_dns(monkeypatch):
    """Resolve every host to a public address for the destination guard.

    The guard looks hosts up before a request. Tests use made-up and real hostnames and
    must not depend on the DNS of the machine running them (a filtering resolver answers
    ``::`` for some mirrors), so the guard's resolver is stubbed. Tests of the guard
    itself replace it again.
    """
    from shelfmark.download import network

    monkeypatch.setattr(
        network,
        "_system_getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )


@pytest.fixture
def sample_prowlarr_result():
    """Sample Prowlarr API search result."""
    return {
        "guid": "abc123-guid",
        "title": "The Great Book by Author Name.epub",
        "indexer": "MyIndexer",
        "protocol": "torrent",
        "size": 5242880,  # 5 MB
        "downloadUrl": "magnet:?xt=urn:btih:abc123",
        "infoUrl": "https://example.com/book/123",
        "seeders": 10,
        "leechers": 2,
        "publishDate": "2024-01-15T12:00:00Z",
        "categories": [{"id": 7020, "name": "Books/EBook"}],
        "indexerId": 1,
    }


@pytest.fixture
def sample_nzb_result():
    """Sample Prowlarr API NZB result."""
    return {
        "guid": "nzb456-guid",
        "title": "Another Book [PDF] by Writer",
        "indexer": "NZBIndexer",
        "protocol": "usenet",
        "size": 10485760,  # 10 MB
        "downloadUrl": "https://example.com/download.nzb",
        "infoUrl": "https://example.com/nzb/456",
        "grabs": 50,
        "publishDate": "2024-02-20T10:30:00Z",
        "categories": [{"id": 7020, "name": "Books/EBook"}],
        "indexerId": 2,
    }


@pytest.fixture
def mock_config(monkeypatch):
    """Fixture to mock config values."""
    config_values = {}

    def mock_get(key, default=""):
        return config_values.get(key, default)

    def set_config(key, value):
        config_values[key] = value

    # Create a mock config module
    class MockConfig:
        get = staticmethod(mock_get)
        set = staticmethod(set_config)
        _values = config_values

    return MockConfig
