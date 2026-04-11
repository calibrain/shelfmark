"""Unit tests for the Deluge client."""

from unittest.mock import MagicMock, patch

from shelfmark.download.clients import DownloadStatus
from shelfmark.download.clients.torrent_utils import TorrentInfo


def make_config_getter(values):
    """Create a config.get function that returns values from a dict."""

    def getter(key, default=""):
        return values.get(key, default)

    return getter


class TestDelugeClientAddDownload:
    """Tests for DelugeClient.add_download()."""

    def test_add_download_uses_configured_download_dir(self, monkeypatch):
        """Add torrent should pass configured download location option."""
        config_values = {
            "DELUGE_HOST": "http://localhost",
            "DELUGE_PORT": "8112",
            "DELUGE_PASSWORD": "password",
            "DELUGE_CATEGORY": "books",
            "DELUGE_DOWNLOAD_DIR": "/downloads/books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.deluge.config.get",
            make_config_getter(config_values),
        )

        from shelfmark.download.clients.deluge import DelugeClient

        client = DelugeClient()
        monkeypatch.setattr(client, "_ensure_connected", lambda: None)
        mock_rpc_call = MagicMock(return_value="abcdef1234567890abcdef1234567890abcdef12")
        monkeypatch.setattr(client, "_rpc_call", mock_rpc_call)
        mock_try_set_label = MagicMock()
        monkeypatch.setattr(client, "_try_set_label", mock_try_set_label)

        magnet = "magnet:?xt=urn:btih:ABCDEF1234567890ABCDEF1234567890ABCDEF12&dn=test"
        with patch("shelfmark.download.clients.deluge.extract_torrent_info", autospec=True) as mock_extract:
            mock_extract.return_value = TorrentInfo(
                info_hash="abcdef1234567890abcdef1234567890abcdef12",
                torrent_data=None,
                is_magnet=True,
                magnet_url=magnet,
            )
            result = client.add_download(magnet, "Test")

        assert result == "abcdef1234567890abcdef1234567890abcdef12"
        mock_rpc_call.assert_called_once_with(
            "core.add_torrent_magnet",
            magnet,
            {"download_location": "/downloads/books"},
        )
        mock_try_set_label.assert_called_once_with(
            "abcdef1234567890abcdef1234567890abcdef12",
            "books",
        )

    def test_add_download_uses_empty_options_without_download_dir(self, monkeypatch):
        """Add torrent should keep options empty when no directory is configured."""
        config_values = {
            "DELUGE_HOST": "http://localhost",
            "DELUGE_PORT": "8112",
            "DELUGE_PASSWORD": "password",
            "DELUGE_CATEGORY": "books",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.deluge.config.get",
            make_config_getter(config_values),
        )

        from shelfmark.download.clients.deluge import DelugeClient

        client = DelugeClient()
        monkeypatch.setattr(client, "_ensure_connected", lambda: None)
        mock_rpc_call = MagicMock(return_value="abcdef1234567890abcdef1234567890abcdef12")
        monkeypatch.setattr(client, "_rpc_call", mock_rpc_call)
        monkeypatch.setattr(client, "_try_set_label", MagicMock())

        magnet = "magnet:?xt=urn:btih:ABCDEF1234567890ABCDEF1234567890ABCDEF12&dn=test"
        with patch("shelfmark.download.clients.deluge.extract_torrent_info", autospec=True) as mock_extract:
            mock_extract.return_value = TorrentInfo(
                info_hash="abcdef1234567890abcdef1234567890abcdef12",
                torrent_data=None,
                is_magnet=True,
                magnet_url=magnet,
            )
            client.add_download(magnet, "Test")

        mock_rpc_call.assert_called_once_with(
            "core.add_torrent_magnet",
            magnet,
            {},
        )


class TestDelugeClientErrors:
    """Tests for Deluge error handling fallbacks."""

    def test_test_connection_failure_returns_false(self, monkeypatch):
        """Operational client errors should return a failure tuple."""
        config_values = {
            "DELUGE_HOST": "http://localhost",
            "DELUGE_PORT": "8112",
            "DELUGE_PASSWORD": "password",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.deluge.config.get",
            make_config_getter(config_values),
        )

        from shelfmark.download.clients.deluge import DelugeClient

        client = DelugeClient()
        monkeypatch.setattr(client, "_ensure_connected", MagicMock(side_effect=RuntimeError("offline")))

        success, message = client.test_connection()

        assert success is False
        assert "offline" in message
        assert client._authenticated is False
        assert client._connected is False

    def test_get_status_failure_returns_error_status(self, monkeypatch):
        """Status lookup failures should degrade to DownloadStatus.error()."""
        config_values = {
            "DELUGE_HOST": "http://localhost",
            "DELUGE_PORT": "8112",
            "DELUGE_PASSWORD": "password",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.deluge.config.get",
            make_config_getter(config_values),
        )

        from shelfmark.download.clients.deluge import DelugeClient

        client = DelugeClient()
        monkeypatch.setattr(client, "_ensure_connected", lambda: None)
        monkeypatch.setattr(client, "_rpc_call", MagicMock(side_effect=RuntimeError("status failed")))

        status = client.get_status("torrent-id")

        assert isinstance(status, DownloadStatus)
        assert status.state_value == "error"
        assert "status failed" in status.message

    def test_remove_failure_returns_false(self, monkeypatch):
        """Removal failures should return False rather than raising."""
        config_values = {
            "DELUGE_HOST": "http://localhost",
            "DELUGE_PORT": "8112",
            "DELUGE_PASSWORD": "password",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.deluge.config.get",
            make_config_getter(config_values),
        )

        from shelfmark.download.clients.deluge import DelugeClient

        client = DelugeClient()
        monkeypatch.setattr(client, "_ensure_connected", lambda: None)
        monkeypatch.setattr(client, "_rpc_call", MagicMock(side_effect=RuntimeError("remove failed")))

        assert client.remove("torrent-id") is False

    def test_find_existing_failure_returns_none_and_resets_connection(self, monkeypatch):
        """Lookup failures should clear client state and return no match."""
        config_values = {
            "DELUGE_HOST": "http://localhost",
            "DELUGE_PORT": "8112",
            "DELUGE_PASSWORD": "password",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.deluge.config.get",
            make_config_getter(config_values),
        )

        from shelfmark.download.clients.deluge import DelugeClient

        client = DelugeClient()
        client._authenticated = True
        client._connected = True
        monkeypatch.setattr(client, "_ensure_connected", lambda: None)
        monkeypatch.setattr(client, "_rpc_call", MagicMock(side_effect=RuntimeError("lookup failed")))

        magnet = "magnet:?xt=urn:btih:ABCDEF1234567890ABCDEF1234567890ABCDEF12&dn=test"
        with patch("shelfmark.download.clients.deluge.extract_torrent_info", autospec=True) as mock_extract:
            mock_extract.return_value = TorrentInfo(
                info_hash="abcdef1234567890abcdef1234567890abcdef12",
                torrent_data=None,
                is_magnet=True,
                magnet_url=magnet,
            )
            assert client.find_existing(magnet) is None

        assert client._authenticated is False
        assert client._connected is False
