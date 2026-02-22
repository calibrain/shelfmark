"""
rTorrent download client for Prowlarr integration.

Uses xmlrpc to communicate with rTorrent's RPC interface.
"""

import ssl
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify
from shelfmark.download.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
)
from shelfmark.download.clients.torrent_utils import (
    extract_torrent_info,
)

logger = setup_logger(__name__)


def _create_rtorrent_server_proxy(url: str) -> Any:
    """Create an XML-RPC ServerProxy honoring certificate validation mode."""
    from xmlrpc.client import SafeTransport, ServerProxy

    verify = get_ssl_verify(url)
    if url.startswith("https://") and not verify:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        transport = SafeTransport(context=ssl_context)
        return ServerProxy(url, transport=transport)

    return ServerProxy(url)


@register_client("torrent")
class RTorrentClient(DownloadClient):
    """rTorrent download client using xmlrpc."""

    protocol = "torrent"
    name = "rtorrent"

    def __init__(self):
        """Initialize rTorrent client with settings from config."""
        raw_url = config.get("RTORRENT_URL", "")
        if not raw_url:
            raise ValueError("RTORRENT_URL is required")

        self._base_url = normalize_http_url(raw_url)
        if not self._base_url:
            raise ValueError("RTORRENT_URL is invalid")

        username = config.get("RTORRENT_USERNAME", "")
        password = config.get("RTORRENT_PASSWORD", "")

        if username and password:
            parsed = urlparse(self._base_url)
            self._base_url = (
                f"{parsed.scheme}://{username}:{password}@{parsed.netloc}{parsed.path}"
            )

        self._rpc = _create_rtorrent_server_proxy(self._base_url)
        self._download_dir = config.get("RTORRENT_DOWNLOAD_DIR", "")
        self._label = config.get("RTORRENT_LABEL", "")

    @staticmethod
    def is_configured() -> bool:
        """Check if rTorrent is configured and selected as the torrent client."""
        client = config.get("PROWLARR_TORRENT_CLIENT", "")
        url = normalize_http_url(config.get("RTORRENT_URL", ""))
        return client == "rtorrent" and bool(url)

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to rTorrent."""
        try:
            version = self._rpc.system.client_version()
            return True, f"Connected to rTorrent {version}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def add_download(
        self,
        url: str,
        name: str,
        category: Optional[str] = None,
        expected_hash: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        Add torrent by URL (magnet or .torrent).

        Args:
            url: Magnet link or .torrent URL
            name: Display name for the torrent
            category: Category for organization (uses configured label if not specified)
            expected_hash: Optional info_hash hint (from Prowlarr)

        Returns:
            Torrent hash (info_hash).

        Raises:
            Exception: If adding fails.
        """
        try:
            torrent_info = extract_torrent_info(url, expected_hash=expected_hash)

            commands = []

            label = category or self._label
            if label:
                logger.debug(f"Setting rTorrent label: {label}")
                commands.append(f"d.custom1.set={label}")

            download_dir = self._download_dir or self._get_download_dir()
            if download_dir:
                logger.debug(f"Setting rTorrent download directory: {download_dir}")
                commands.append(f"d.directory_base.set={download_dir}")

            if torrent_info.torrent_data:
                logger.debug(f"Adding torrent data directly to rTorrent for: {name} with commands: {commands} with data size: {len(torrent_info.torrent_data)}")
                self._rpc.load.raw_start(
                    "", torrent_info.torrent_data, ";".join(commands)
                )
            else:
                logger.debug(f"Adding torrent URL to rTorrent for: {name} with commands: {commands} with URL: {url}")
                add_url = torrent_info.magnet_url or url
                self._rpc.load.start("", add_url, ";".join(commands))

            torrent_hash = torrent_info.info_hash or expected_hash
            if not torrent_hash:
                raise Exception("Could not determine torrent hash from URL")

            logger.debug(f"Added torrent to rTorrent: {torrent_hash}")
            return torrent_hash

        except Exception as e:
            logger.error(f"rTorrent add failed: {e}")
            raise

    def get_status(self, download_id: str) -> DownloadStatus:
        """
        Get torrent status by hash.

        Args:
            download_id: Torrent info_hash

        Returns:
            Current download status.
        """
        try:
            # rtorrent is somehow case sensitive and requires uppercase hashes for look
            download_id = download_id.upper()
            torrent_list = self._rpc.d.multicall.filtered(
                "",
                "default",
                f"equal={{d.hash=,cat={download_id}}}",
                "d.hash=",
                "d.state=",
                "d.completed_bytes=",
                "d.size_bytes=",
                "d.down.rate=",
                "d.up.rate=",
                "d.custom1=",
                "d.complete=",
            )
            logger.debug(f"Fetched torrent status from rTorrent for: {download_id} - {torrent_list}")
            if not torrent_list:
                logger.warning(f"Torrent not found in rTorrent: {download_id}")
                return DownloadStatus.error("Torrent not found")

            torrent = torrent_list[0]
            if not torrent:
                logger.warning(f"Torrent data is empty for: {download_id}")
                return DownloadStatus.error("Torrent not found")

            logger.debug(f"Torrent data for {download_id}: {torrent}")
            (
                torrent_hash,
                state,
                bytes_downloaded,
                bytes_total,
                down_rate,
                up_rate,
                custom_category,
                complete,
            ) = torrent

            try:
                state = int(state)
            except Exception:
                state = 0

            complete = bool(complete)

            if bytes_total > 0:
                progress = (bytes_downloaded / bytes_total) * 100
            else:
                progress = 0

            bytes_left = max(0, bytes_total - bytes_downloaded)

            state_map = {
                0: ("paused", "Paused"),
                1: ("downloading", "Downloading"),
                2: ("downloading", "Downloading"),
                3: ("downloading", "Downloading"),
                4: ("seeding", "Seeding"),
            }

            state_str, message = state_map.get(state, ("unknown", "Unknown state"))

            if complete and not message:
                message = "Complete"

            eta = None
            if down_rate > 0 and bytes_left > 0:
                eta_seconds = bytes_left / down_rate
                if eta_seconds < 604800:
                    eta = int(eta_seconds)

            file_path = None
            if complete:
                file_path = self._get_torrent_path(download_id)

            return DownloadStatus(
                progress=min(100, progress),
                state="complete" if complete else state_str,
                message=message,
                complete=complete,
                file_path=file_path,
                download_speed=down_rate if down_rate > 0 else None,
                eta=eta,
            )

        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"rTorrent get_status failed ({error_type}): {e}")
            return DownloadStatus.error(f"{error_type}: {e}")

    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        """
        Remove a torrent from rTorrent.

        Args:
            download_id: Torrent info_hash
            delete_files: Whether to also delete files

        Returns:
            True if successful.
        """
        try:
            if delete_files:
                self._rpc.d.delete_tied(download_id)
                self._rpc.d.erase(download_id)
            else:
                self._rpc.d.stop(download_id)
                self._rpc.d.erase(download_id)

            logger.info(
                f"Removed torrent from rTorrent: {download_id}"
                + (" (with files)" if delete_files else "")
            )
            return True
        except Exception as e:
            error_type = type(e).__name__
            logger.error(f"rTorrent remove failed ({error_type}): {e}")
            return False

    def get_download_path(self, download_id: str) -> Optional[str]:
        """
        Get the path where torrent files are located.

        Args:
            download_id: Torrent info_hash

        Returns:
            Content path (file or directory), or None.
        """
        try:
            return self._get_torrent_path(download_id)
        except Exception as e:
            error_type = type(e).__name__
            logger.debug(f"rTorrent get_download_path failed ({error_type}): {e}")
            return None

    def find_existing(
        self, url: str, category: Optional[str] = None
    ) -> Optional[Tuple[str, DownloadStatus]]:
        """Check if a torrent for this URL already exists in rTorrent."""
        try:
            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            try:
                status = self.get_status(torrent_info.info_hash)
                if status.state != DownloadStatus.error("").state:
                    return (torrent_info.info_hash, status)
            except Exception:
                pass

            return None
        except Exception as e:
            logger.debug(f"Error checking for existing torrent: {e}")
            return None

    def _get_download_dir(self) -> str:
        """Get the download directory from rTorrent config."""
        try:
            download_dir = self._rpc.directory.default()
            return download_dir
        except Exception:
            return "/downloads"

    def _get_torrent_path(self, download_id: str) -> Optional[str]:
        """Get the file path of a torrent by hash.

        Uses `d.base_path` for the item output path. In the xmlrpc interface
        this corresponds to `d.get_base_path()`.
        """
        try:
            # rTorrent is case sensitive for hashes; use uppercase as in get_status()
            download_hash = download_id.upper()
            details = self._rpc.d.multicall.filtered(
                "",
                "default",
                f"equal={{d.hash=,cat={download_hash}}}",
                "d.base_path=",
            )
            if not details:
                return None
            path = details[0][0]
            return path if path else None
        except Exception:
            return None
