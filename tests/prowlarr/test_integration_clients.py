"""Integration tests for download clients.

These tests require the Docker test stack to be running:
    docker compose -f docker-compose.test-clients.yml up -d

Run with: docker compose -f docker-compose.test-clients.yml exec shelfmark uv run pytest /app/tests/prowlarr/test_integration_clients.py -v -m integration

These tests use the actual Docker stack configuration. Before running:
1. Start the test stack: docker compose -f docker-compose.test-clients.yml up -d
2. Configure clients via the cwabd UI at http://localhost:8084/settings
"""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shelfmark.core.config import config
from shelfmark.core.settings_registry import save_config_file
from shelfmark.download.clients import DownloadStatus

# Test magnet link (Ubuntu ISO - legal, small metadata)
TEST_MAGNET = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=ubuntu-22.04.3-live-server-amd64.iso"

_MINIMAL_NZB = b"""<?xml version="1.0" encoding="utf-8"?>
<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">
  <file poster="Shelfmark" date="1710000000" subject="Integration_Book.nzb">
    <groups>
      <group>alt.binaries.test</group>
    </groups>
    <segments>
      <segment bytes="1" number="1">integration-message-id</segment>
    </segments>
  </file>
</nzb>
"""


def _make_nzb_handler(request_paths: list[str]):
    class NZBFixtureHandler(BaseHTTPRequestHandler):
        response_body = _MINIMAL_NZB

        def do_GET(self):
            request_paths.append(self.path)
            if not self.path.endswith(".nzb"):
                self.send_response(404)
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/x-nzb")
            self.send_header("Content-Length", str(len(self.response_body)))
            self.end_headers()
            self.wfile.write(self.response_body)

        def log_message(self, format, *args):  # noqa: A002
            return

    return NZBFixtureHandler


@pytest.fixture
def nzb_fixture_server():
    """Serve a tiny NZB file for live usenet client integration tests."""
    request_paths: list[str] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_nzb_handler(request_paths))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield {
            "base_url": f"http://127.0.0.1:{server.server_address[1]}",
            "request_paths": request_paths,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _wait_for_live_status(client, download_id: str, *, attempts: int = 10, delay: float = 0.5):
    """Give live usenet clients a short window to register a queued job."""
    last_status = None
    for _ in range(attempts):
        last_status = client.get_status(download_id)
        if last_status.state_value != "error":
            return last_status
        time.sleep(delay)
    return last_status


# ============ Configuration Setup Functions ============


def _setup_transmission_config():
    """Set up Transmission configuration via config files and refresh config."""
    save_config_file(
        "prowlarr_clients",
        {
            "PROWLARR_TORRENT_CLIENT": "transmission",
            "TRANSMISSION_URL": "http://transmission:9091",
            "TRANSMISSION_USERNAME": "admin",
            "TRANSMISSION_PASSWORD": "admin",
            "TRANSMISSION_CATEGORY": "test",
        },
    )
    config.refresh()


def _setup_qbittorrent_config():
    """Set up qBittorrent configuration via config files and refresh config."""
    save_config_file(
        "prowlarr_clients",
        {
            "PROWLARR_TORRENT_CLIENT": "qbittorrent",
            "QBITTORRENT_URL": "http://qbittorrent:8080",
            "QBITTORRENT_USERNAME": "admin",
            "QBITTORRENT_PASSWORD": "admin123",
            "QBITTORRENT_CATEGORY": "test",
        },
    )
    config.refresh()


def _setup_deluge_config():
    """Set up Deluge configuration via config files and refresh config."""
    save_config_file(
        "prowlarr_clients",
        {
            "PROWLARR_TORRENT_CLIENT": "deluge",
            "DELUGE_HOST": "deluge",
            "DELUGE_PORT": "8112",
            "DELUGE_PASSWORD": "deluge",
            "DELUGE_CATEGORY": "test",
        },
    )
    config.refresh()


def _setup_nzbget_config():
    """Set up NZBGet configuration via config files and refresh config."""
    save_config_file(
        "prowlarr_clients",
        {
            "PROWLARR_USENET_CLIENT": "nzbget",
            "NZBGET_URL": "http://nzbget:6789",
            "NZBGET_USERNAME": "nzbget",
            "NZBGET_PASSWORD": "tegbzn6789",
            "NZBGET_CATEGORY": "test",
        },
    )
    config.refresh()


def _setup_sabnzbd_config():
    """Set up SABnzbd configuration via config files and refresh config."""
    api_key = _get_sabnzbd_api_key()
    if not api_key:
        return False
    save_config_file(
        "prowlarr_clients",
        {
            "PROWLARR_USENET_CLIENT": "sabnzbd",
            "SABNZBD_URL": "http://sabnzbd:8080",
            "SABNZBD_API_KEY": api_key,
            "SABNZBD_CATEGORY": "test",
        },
    )
    config.refresh()
    return True


def _get_sabnzbd_api_key():
    """Extract SABnzbd API key from config file."""
    import re

    # Try mounted config paths (from docker-compose volumes)
    config_paths = [
        "/sabnzbd-config/sabnzbd.ini",
        "/config/sabnzbd.ini",
    ]
    for config_path in config_paths:
        try:
            with open(config_path) as f:
                content = f.read()
                match = re.search(r"api_key\s*=\s*(\S+)", content)
                if match:
                    return match.group(1)
        except Exception:
            continue
    return None


# ============ Client Factory Functions ============


def _try_get_transmission_client():
    """Try to get a working Transmission client, or None if unavailable."""
    _setup_transmission_config()
    try:
        from shelfmark.download.clients.transmission import TransmissionClient

        client = TransmissionClient()
        client.test_connection()
        return client
    except Exception:
        return None


def _try_get_qbittorrent_client():
    """Try to get a working qBittorrent client, or None if unavailable."""
    _setup_qbittorrent_config()
    try:
        from shelfmark.download.clients.qbittorrent import QBittorrentClient

        client = QBittorrentClient()
        success, _ = client.test_connection()
        if success:
            return client
    except Exception:
        pass
    return None


def _try_get_deluge_client():
    """Try to get a working Deluge client, or None if unavailable."""
    _setup_deluge_config()
    try:
        from shelfmark.download.clients.deluge import DelugeClient

        client = DelugeClient()
        success, _ = client.test_connection()
        if success:
            return client
    except Exception:
        pass
    return None


def _try_get_nzbget_client():
    """Try to get a working NZBGet client, or None if unavailable."""
    _setup_nzbget_config()
    try:
        from shelfmark.download.clients.nzbget import NZBGetClient

        client = NZBGetClient()
        success, _ = client.test_connection()
        if success:
            return client
        return None
    except Exception:
        return None


def _try_get_sabnzbd_client():
    """Try to get a working SABnzbd client, or None if unavailable."""
    if not _setup_sabnzbd_config():
        return None
    try:
        from shelfmark.download.clients.sabnzbd import SABnzbdClient

        client = SABnzbdClient()
        success, _ = client.test_connection()
        if success:
            return client
    except Exception:
        pass
    return None


# ============ Fixtures ============


@pytest.fixture(scope="module")
def transmission_client():
    """Get Transmission client if available, skip test otherwise."""
    client = _try_get_transmission_client()
    if client is None:
        pytest.skip(
            "Transmission not available - ensure docker-compose.test-clients.yml is running"
        )
    return client


@pytest.fixture(scope="module")
def qbittorrent_client():
    """Get qBittorrent client if available, skip test otherwise."""
    client = _try_get_qbittorrent_client()
    if client is None:
        pytest.skip(
            "qBittorrent not available - ensure docker-compose.test-clients.yml is running and check temp password"
        )
    return client


@pytest.fixture(scope="module")
def deluge_client():
    """Get Deluge client if available, skip test otherwise."""
    client = _try_get_deluge_client()
    if client is None:
        pytest.skip("Deluge not available - ensure docker-compose.test-clients.yml is running")
    return client


@pytest.fixture(scope="module")
def nzbget_client():
    """Get NZBGet client if available, skip test otherwise."""
    client = _try_get_nzbget_client()
    if client is None:
        pytest.skip("NZBGet not available - ensure docker-compose.test-clients.yml is running")
    return client


@pytest.fixture(scope="module")
def sabnzbd_client():
    """Get SABnzbd client if available, skip test otherwise."""
    client = _try_get_sabnzbd_client()
    if client is None:
        pytest.skip(
            "SABnzbd not available - ensure docker-compose.test-clients.yml is running and setup wizard completed"
        )
    return client


@pytest.mark.integration
class TestTransmissionIntegration:
    """Integration tests for Transmission client.

    Uses the Docker test stack's Transmission instance (http://transmission:9091).
    """

    def test_test_connection(self, transmission_client):
        """Test connection to Transmission."""
        success, message = transmission_client.test_connection()

        assert success, f"Connection failed: {message}"
        assert "Transmission" in message

    def test_add_and_remove_torrent(self, transmission_client):
        """Test adding and removing a torrent."""
        client = transmission_client

        # Add torrent
        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO",
        )

        assert download_id is not None

        # Wait a moment
        time.sleep(2)

        try:
            # Check status
            status = client.get_status(download_id)
            assert isinstance(status, DownloadStatus)
            assert status.progress >= 0
        finally:
            # Remove it
            result = client.remove(download_id, delete_files=True)
            assert result is True

    def test_find_existing_torrent(self, transmission_client):
        """Test finding an existing torrent."""
        client = transmission_client

        # Add torrent
        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO Find",
        )
        time.sleep(2)

        try:
            result = client.find_existing(TEST_MAGNET)
            assert result is not None
            found_id, status = result
            assert found_id == download_id
            assert isinstance(status, DownloadStatus)
        finally:
            client.remove(download_id, delete_files=True)

    def test_status_fields(self, transmission_client):
        """Test that status contains all required fields."""
        client = transmission_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Status Fields",
        )
        time.sleep(2)

        try:
            status = client.get_status(download_id)

            # Check all required fields exist
            assert hasattr(status, "progress")
            assert hasattr(status, "state")
            assert hasattr(status, "message")
            assert hasattr(status, "complete")
            assert hasattr(status, "file_path")
            assert hasattr(status, "download_speed")
            assert hasattr(status, "eta")

            # Progress should be a number between 0 and 100
            assert 0 <= status.progress <= 100

            # State should be a known value
            valid_states = {
                "downloading",
                "complete",
                "error",
                "seeding",
                "paused",
                "queued",
                "fetching_metadata",
            }
            assert status.state.value in valid_states

            # Complete should be boolean
            assert isinstance(status.complete, bool)
        finally:
            client.remove(download_id, delete_files=True)


@pytest.mark.integration
class TestQBittorrentIntegration:
    """Integration tests for qBittorrent client.

    Uses the Docker test stack's qBittorrent instance (http://qbittorrent:8080).
    Note: qBittorrent generates a temporary password on startup.
    """

    def test_test_connection(self, qbittorrent_client):
        """Test connection to qBittorrent."""
        success, message = qbittorrent_client.test_connection()

        assert success, f"Connection failed: {message}"
        assert "qBittorrent" in message

    def test_add_and_remove_torrent(self, qbittorrent_client):
        """Test adding and removing a torrent."""
        client = qbittorrent_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO qBit",
        )

        assert download_id is not None

        time.sleep(3)  # qBittorrent needs a moment to process

        try:
            status = client.get_status(download_id)
            assert isinstance(status, DownloadStatus)
            assert status.progress >= 0
        finally:
            result = client.remove(download_id, delete_files=True)
            assert result is True

    def test_find_existing_torrent(self, qbittorrent_client):
        """Test finding an existing torrent."""
        client = qbittorrent_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO Find qBit",
        )
        time.sleep(3)

        try:
            result = client.find_existing(TEST_MAGNET)
            assert result is not None
            found_id, status = result
            assert found_id == download_id
            assert isinstance(status, DownloadStatus)
        finally:
            client.remove(download_id, delete_files=True)

    def test_status_fields(self, qbittorrent_client):
        """Test that status contains all required fields."""
        client = qbittorrent_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Status Fields qBit",
        )
        time.sleep(3)

        try:
            status = client.get_status(download_id)

            assert hasattr(status, "progress")
            assert hasattr(status, "state")
            assert hasattr(status, "message")
            assert hasattr(status, "complete")
            assert hasattr(status, "file_path")

            assert 0 <= status.progress <= 100

            valid_states = {
                "downloading",
                "complete",
                "error",
                "seeding",
                "paused",
                "queued",
                "fetching_metadata",
                "stalled",
                "checking",
            }
            state_value = status.state.value if hasattr(status.state, "value") else status.state
            assert state_value in valid_states

            assert isinstance(status.complete, bool)
        finally:
            client.remove(download_id, delete_files=True)


@pytest.mark.integration
class TestDelugeIntegration:
    """Integration tests for Deluge client.

    Uses the Docker test stack's Deluge Web UI instance (http://deluge:8112).
    Default password: deluge
    """

    def test_test_connection(self, deluge_client):
        """Test connection to Deluge."""
        success, message = deluge_client.test_connection()

        assert success, f"Connection failed: {message}"
        assert "Deluge" in message

    def test_add_and_remove_torrent(self, deluge_client):
        """Test adding and removing a torrent."""
        client = deluge_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO Deluge",
        )

        assert download_id is not None

        time.sleep(3)

        try:
            status = client.get_status(download_id)
            assert isinstance(status, DownloadStatus)
            assert status.progress >= 0
        finally:
            result = client.remove(download_id, delete_files=True)
            assert result is True

    def test_find_existing_torrent(self, deluge_client):
        """Test finding an existing torrent."""
        client = deluge_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Ubuntu ISO Find Deluge",
        )
        time.sleep(3)

        try:
            result = client.find_existing(TEST_MAGNET)
            assert result is not None
            found_id, status = result
            assert found_id == download_id
            assert isinstance(status, DownloadStatus)
        finally:
            client.remove(download_id, delete_files=True)

    def test_status_fields(self, deluge_client):
        """Test that status contains all required fields."""
        client = deluge_client

        download_id = client.add_download(
            url=TEST_MAGNET,
            name="Test Status Fields Deluge",
        )
        time.sleep(3)

        try:
            status = client.get_status(download_id)

            assert hasattr(status, "progress")
            assert hasattr(status, "state")
            assert hasattr(status, "message")
            assert hasattr(status, "complete")
            assert hasattr(status, "file_path")

            assert 0 <= status.progress <= 100

            valid_states = {
                "downloading",
                "complete",
                "error",
                "seeding",
                "paused",
                "queued",
                "fetching_metadata",
                "checking",
            }
            assert status.state.value in valid_states

            assert isinstance(status.complete, bool)
        finally:
            client.remove(download_id, delete_files=True)


@pytest.mark.integration
class TestNZBGetIntegration:
    """Integration tests for NZBGet client.

    Uses the Docker test stack's NZBGet instance (http://nzbget:6789).
    Default credentials: nzbget/tegbzn6789
    """

    def test_test_connection(self, nzbget_client):
        """Test connection to NZBGet."""
        success, message = nzbget_client.test_connection()

        assert success, f"Connection failed: {message}"
        assert "NZBGet" in message

    def test_add_status_and_remove_nzb(self, nzbget_client, nzb_fixture_server):
        """Exercise the live NZBGet contract with a real queued NZB."""
        client = nzbget_client
        url = f"{nzb_fixture_server['base_url']}/Integration_Book.nzb"

        download_id = client.add_download(url=url, name="Integration_Book")
        assert download_id.isdigit()

        status = _wait_for_live_status(client, download_id)
        assert status is not None
        assert status.complete is False
        assert 0 <= status.progress <= 100
        assert status.message
        assert status.state_value in {"queued", "downloading", "paused", "processing", "unknown"}
        assert nzb_fixture_server["request_paths"] == ["/Integration_Book.nzb"]

        assert client.remove(download_id, delete_files=True) is True


@pytest.mark.integration
class TestSABnzbdIntegration:
    """Integration tests for SABnzbd client.

    Uses the Docker test stack's SABnzbd instance (http://sabnzbd:8080).
    Requires API key from config after setup wizard completion.
    """

    def test_test_connection(self, sabnzbd_client):
        """Test connection to SABnzbd."""
        success, message = sabnzbd_client.test_connection()

        assert success, f"Connection failed: {message}"
        assert "SABnzbd" in message

    def test_add_find_status_and_remove_nzb(self, sabnzbd_client, nzb_fixture_server):
        """Exercise the live SABnzbd contract with queue and lookup behavior."""
        client = sabnzbd_client
        url = f"{nzb_fixture_server['base_url']}/Integration_Book.nzb"

        nzo_id = client.add_download(url=url, name="Integration_Book")
        assert nzo_id
        assert nzo_id.startswith("SABnzbd_nzo_")

        found = None
        for _ in range(10):
            found = client.find_existing(url)
            if found is not None:
                break
            time.sleep(0.5)

        assert found is not None
        found_id, found_status = found
        assert found_id == nzo_id
        assert isinstance(found_status, DownloadStatus)

        status = _wait_for_live_status(client, nzo_id)
        assert status is not None
        assert status.complete is False
        assert 0 <= status.progress <= 100
        assert status.message
        assert status.state_value in {"queued", "downloading", "processing", "paused"}
        assert nzb_fixture_server["request_paths"] == ["/Integration_Book.nzb"]

        assert client.remove(nzo_id, delete_files=True, archive=False) is True
