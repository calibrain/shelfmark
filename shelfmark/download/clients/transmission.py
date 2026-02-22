"""
Transmission download client for Prowlarr integration.

Uses the transmission-rpc library to communicate with Transmission's RPC API.
"""

from contextlib import contextmanager
from typing import Any, Iterator, Optional, Tuple


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
    parse_transmission_url,
)

logger = setup_logger(__name__)


@contextmanager
def _transmission_session_verify_override(url: str) -> Iterator[None]:
    """Temporarily override transmission-rpc's session factory when verify is disabled.

    transmission-rpc performs an RPC call inside Client.__init__, so verify must be
    set before the client is constructed.
    """
    verify = get_ssl_verify(url)
    if verify:
        yield
        return

    try:
        import transmission_rpc.client as transmission_rpc_client
    except Exception:
        # If internals differ, gracefully fall back to default behavior.
        yield
        return

    original_session_factory = transmission_rpc_client.requests.Session

    def _session_factory(*args: Any, **kwargs: Any) -> Any:
        session = original_session_factory(*args, **kwargs)
        session.verify = False
        return session

    transmission_rpc_client.requests.Session = _session_factory
    try:
        yield
    finally:
        transmission_rpc_client.requests.Session = original_session_factory


def _apply_transmission_ssl_verify(client: Any, url: str) -> None:
    """Apply global certificate validation policy to transmission-rpc client."""
    session = getattr(client, "_http_session", None)
    if session is None:
        return
    try:
        session.verify = get_ssl_verify(url)
    except Exception as e:
        logger.debug("Unable to apply Transmission TLS verify setting: %s", e)


@register_client("torrent")
class TransmissionClient(DownloadClient):
    """Transmission download client using transmission-rpc library."""

    protocol = "torrent"
    name = "transmission"

    def __init__(self):
        """Initialize Transmission client with settings from config."""
        from transmission_rpc import Client

        raw_url = config.get("TRANSMISSION_URL", "")
        if not raw_url:
            raise ValueError("TRANSMISSION_URL is required")

        url = normalize_http_url(raw_url)
        if not url:
            raise ValueError("TRANSMISSION_URL is invalid")

        username = config.get("TRANSMISSION_USERNAME", "")
        password = config.get("TRANSMISSION_PASSWORD", "")

        # Parse URL to extract host, port, and path
        protocol, host, port, path = parse_transmission_url(url)

        client_kwargs = {
            "host": host,
            "port": port,
            "path": path,
            "username": username if username else None,
            "password": password if password else None,
            "protocol": protocol,
        }
        try:
            with _transmission_session_verify_override(url):
                self._client = Client(**client_kwargs)
        except TypeError as e:
            # Older transmission-rpc versions may not accept protocol as a kwarg.
            if "protocol" not in str(e):
                raise
            client_kwargs.pop("protocol", None)
            with _transmission_session_verify_override(url):
                self._client = Client(**client_kwargs)
            # Some versions expose protocol as an attribute rather than kwarg.
            if protocol == "https" and hasattr(self._client, "protocol"):
                try:
                    setattr(self._client, "protocol", protocol)
                except Exception:
                    pass
        _apply_transmission_ssl_verify(self._client, url)
        self._category = config.get("TRANSMISSION_CATEGORY", "books")
        self._download_dir = config.get("TRANSMISSION_DOWNLOAD_DIR", "")

    @staticmethod
    def is_configured() -> bool:
        """Check if Transmission is configured and selected as the torrent client."""
        client = config.get("PROWLARR_TORRENT_CLIENT", "")
        url = normalize_http_url(config.get("TRANSMISSION_URL", ""))
        return client == "transmission" and bool(url)

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Transmission."""
        try:
            session = self._client.get_session()
            version = session.version
            return True, f"Connected to Transmission {version}"
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
            category: Category for organization (uses configured default if not specified)
            expected_hash: Optional info_hash hint (from Prowlarr)

        Returns:
            Torrent hash (info_hash).

        Raises:
            Exception: If adding fails.
        """
        try:
            resolved_category = category or self._category or ""

            torrent_info = extract_torrent_info(url, expected_hash=expected_hash)
            add_kwargs = {}

            if resolved_category:
                add_kwargs["labels"] = [resolved_category]
            if self._download_dir:
                add_kwargs["download_dir"] = self._download_dir

            if torrent_info.torrent_data:
                torrent = self._client.add_torrent(
                    torrent=torrent_info.torrent_data,
                    **add_kwargs,
                )
            else:
                # Use magnet URL if available, otherwise original URL
                add_url = torrent_info.magnet_url or url
                torrent = self._client.add_torrent(
                    torrent=add_url,
                    **add_kwargs,
                )

            torrent_hash = torrent.hashString.lower()
            logger.info(f"Added torrent to Transmission: {torrent_hash}")

            return torrent_hash

        except Exception as e:
            logger.error(f"Transmission add failed: {e}")
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
            torrent = self._client.get_torrent(download_id)

            # Transmission status values:
            # 0: stopped
            # 1: check pending
            # 2: checking
            # 3: download pending
            # 4: downloading
            # 5: seed pending
            # 6: seeding
            # torrent.status is an enum with .value as string
            status_value = torrent.status.value if hasattr(torrent.status, 'value') else str(torrent.status)
            status_map = {
                "stopped": ("paused", "Paused"),
                "check pending": ("checking", "Waiting to check"),
                "checking": ("checking", "Checking files"),
                "download pending": ("queued", "Waiting to download"),
                "downloading": ("downloading", "Downloading"),
                "seed pending": ("processing", "Moving files"),
                "seeding": ("seeding", "Seeding"),
            }

            state, message = status_map.get(status_value, ("downloading", "Downloading"))
            progress = torrent.percent_done * 100
            # Only mark complete when seeding - seed pending means files still being moved
            complete = progress >= 100 and status_value == "seeding"

            if complete:
                message = "Complete"

            # Get ETA if available and reasonable (less than 1 week)
            eta = None
            if hasattr(torrent, 'eta') and torrent.eta:
                eta_seconds = torrent.eta.total_seconds()
                if 0 < eta_seconds < 604800:
                    eta = int(eta_seconds)

            # Get download speed
            download_speed = torrent.rate_download if hasattr(torrent, 'rate_download') else None

            # Get file path for completed downloads
            file_path = None
            if complete:
                # Output path is downloadDir + torrent name (with ':' replaced)
                torrent_name = getattr(torrent, 'name', '')
                if isinstance(torrent_name, str):
                    torrent_name = torrent_name.replace(':', '_')
                file_path = self._build_path(
                    getattr(torrent, 'download_dir', ''),
                    torrent_name,
                )

            return DownloadStatus(
                progress=progress,
                state="complete" if complete else state,
                message=message,
                complete=complete,
                file_path=file_path,
                download_speed=download_speed,
                eta=eta,
            )

        except KeyError:
            return DownloadStatus.error("Torrent not found")
        except Exception as e:
            return DownloadStatus.error(self._log_error("get_status", e))

    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        """
        Remove a torrent from Transmission.

        Args:
            download_id: Torrent info_hash
            delete_files: Whether to also delete files

        Returns:
            True if successful.
        """
        try:
            self._client.remove_torrent(
                download_id,
                delete_data=delete_files,
            )
            logger.info(
                f"Removed torrent from Transmission: {download_id}"
                + (" (with files)" if delete_files else "")
            )
            return True
        except Exception as e:
            self._log_error("remove", e)
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
             torrent = self._client.get_torrent(download_id)
             torrent_name = getattr(torrent, 'name', '')
             if isinstance(torrent_name, str):
                 torrent_name = torrent_name.replace(':', '_')
             return self._build_path(
                 getattr(torrent, 'download_dir', ''),
                 torrent_name,
             )
        except Exception as e:
            self._log_error("get_download_path", e, level="debug")
            return None

    def find_existing(
        self, url: str, category: Optional[str] = None
    ) -> Optional[Tuple[str, DownloadStatus]]:
        """Check if a torrent for this URL already exists in Transmission."""
        try:
            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            try:
                self._client.get_torrent(torrent_info.info_hash)
                status = self.get_status(torrent_info.info_hash)
                return (torrent_info.info_hash, status)
            except KeyError:
                return None
        except Exception as e:
            logger.debug(f"Error checking for existing torrent: {e}")
            return None
