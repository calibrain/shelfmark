"""qBittorrent download client for Prowlarr integration."""

import time
from types import SimpleNamespace
from typing import Optional, Tuple

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.release_sources.prowlarr.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
)
from shelfmark.release_sources.prowlarr.clients.torrent_utils import (
    extract_torrent_info,
)

logger = setup_logger(__name__)


def _hashes_match(hash1: str, hash2: str) -> bool:
    """Compare hashes, handling Amarr's 40-char zero-padded hashes vs 32-char ed2k hashes."""
    h1, h2 = hash1.lower(), hash2.lower()
    if h1 == h2:
        return True
    if len(h1) == 40 and len(h2) == 32 and h1.endswith("00000000"):
        return h1[:32] == h2
    if len(h2) == 40 and len(h1) == 32 and h2.endswith("00000000"):
        return h2[:32] == h1
    return False


@register_client("torrent")
class QBittorrentClient(DownloadClient):
    """qBittorrent download client."""

    def _is_torrent_loaded(self, torrent_hash: str) -> tuple[bool, Optional[str]]:
        """Check whether qBittorrent has registered a torrent yet.

        Uses `/api/v2/torrents/properties?hash=<hash>`.

        Returns:
            (loaded, error_message)

        Notes:
            A false result with no error means "not loaded yet".
        """
        import requests

        url = f"{self._base_url}/api/v2/torrents/properties"
        params = {"hash": torrent_hash}

        try:
            self._client.auth_log_in()
            response = self._client._session.get(url, params=params, timeout=10)

            # Re-authenticate and retry once on 403
            if response.status_code == 403:
                logger.debug("qBittorrent returned 403 for properties; re-authenticating and retrying")
                self._client.auth_log_in()
                response = self._client._session.get(url, params=params, timeout=10)

            if response.status_code == 403:
                return False, "qBittorrent authentication failed (HTTP 403)"

            # qBittorrent returns 404/409-ish responses depending on version when missing.
            if response.status_code == 404:
                return False, None

            response.raise_for_status()
            return True, None
        except requests.exceptions.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 404:
                return False, None
            if status:
                return False, f"qBittorrent API request failed (HTTP {status})"
            return False, "qBittorrent API request failed"
        except requests.exceptions.ConnectionError:
            return False, f"Cannot connect to qBittorrent at {self._base_url}"
        except requests.exceptions.Timeout:
            return False, f"qBittorrent request timed out at {self._base_url}"
        except Exception as e:
            return False, f"qBittorrent API error: {type(e).__name__}: {e}"

    protocol = "torrent"
    name = "qbittorrent"

    def __init__(self):
        """Initialize qBittorrent client with settings from config."""
        # Lazy import to avoid dependency issues if not using torrents
        from qbittorrentapi import Client

        raw_url = config.get("QBITTORRENT_URL", "")
        if not raw_url:
            raise ValueError("QBITTORRENT_URL is required")

        # We use `_base_url` for direct HTTP calls, so it must be a fully-qualified URL.
        self._base_url = normalize_http_url(raw_url)
        if not self._base_url:
            raise ValueError("QBITTORRENT_URL is invalid")

        # qbittorrent-api accepts either a full URL or host:port; prefer the normalized URL
        # for consistency.
        self._client = Client(
            host=self._base_url,
            username=config.get("QBITTORRENT_USERNAME", ""),
            password=config.get("QBITTORRENT_PASSWORD", ""),
        )
        self._category = config.get("QBITTORRENT_CATEGORY", "books")


    def _get_torrents_info(
        self, torrent_hash: Optional[str] = None
    ) -> tuple[list[SimpleNamespace], Optional[str]]:
        """Get torrent info using GET.

        Behaviors:
        - Retry once on HTTP 403 by re-authenticating.
        - Keep "API/auth/connect" errors distinct from "torrent missing".
        - If a hash-specific query returns empty, fall back to listing by category
          and matching locally.

        Returns:
            (torrents, error_message)
        """
        import requests

        url = f"{self._base_url}/api/v2/torrents/info"

        def do_request(params: dict[str, str]) -> requests.Response:
            # Ensure session is authenticated before using it directly
            self._client.auth_log_in()
            return self._client._session.get(url, params=params, timeout=10)

        def parse_response(
            response: requests.Response,
            *,
            request_params: dict[str, str],
        ) -> tuple[list[SimpleNamespace], Optional[str]]:
            if response.status_code == 403:
                logger.debug("qBittorrent returned 403; re-authenticating and retrying")
                self._client.auth_log_in()
                response = self._client._session.get(url, params=request_params, timeout=10)

            if response.status_code == 403:
                logger.warning("qBittorrent authentication failed (HTTP 403)")
                return [], "qBittorrent authentication failed (HTTP 403)"

            response.raise_for_status()
            torrents = response.json()
            return [SimpleNamespace(**t) for t in torrents], None

        try:
            primary_params: dict[str, str] = {}
            if torrent_hash:
                primary_params["hashes"] = torrent_hash

            response = do_request(primary_params)
            torrents, error = parse_response(response, request_params=primary_params)
            if error:
                return [], error

            if torrent_hash and not torrents:
                # Fallback 1: list by configured category
                category_params: dict[str, str] = {}
                if self._category:
                    category_params["category"] = self._category

                category_response = do_request(category_params)
                category_torrents, category_error = parse_response(
                    category_response, request_params=category_params
                )
                if category_error:
                    return [], category_error

                if category_torrents:
                    return category_torrents, None

                # Fallback 2: list everything (handles per-task categories like audiobooks)
                all_response = do_request({})
                all_torrents, all_error = parse_response(all_response, request_params={})
                if all_error:
                    return [], all_error

                return all_torrents, None

            return torrents, None

        except requests.exceptions.HTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status:
                logger.warning(f"qBittorrent API error (HTTP {status}): {e}")
                return [], f"qBittorrent API request failed (HTTP {status})"

            logger.warning(f"qBittorrent API error: {e}")
            return [], "qBittorrent API request failed"
        except requests.exceptions.ConnectionError:
            logger.warning(f"Cannot connect to qBittorrent at {self._base_url}")
            return [], f"Cannot connect to qBittorrent at {self._base_url}"
        except requests.exceptions.Timeout:
            logger.warning(f"qBittorrent request timed out at {self._base_url}")
            return [], f"qBittorrent request timed out at {self._base_url}"
        except Exception as e:
            logger.debug(f"Failed to get torrents info: {e}")
            # requests raises InvalidSchema when the base URL doesn't include http(s)
            if type(e).__name__ == "InvalidSchema":
                return (
                    [],
                    "qBittorrent URL is invalid (missing http:// or https://). "
                    f"Configured: {self._base_url}",
                )
            return [], f"qBittorrent API error: {type(e).__name__}: {e}"

    @staticmethod
    def is_configured() -> bool:
        """Check if qBittorrent is configured and selected as the torrent client."""
        client = config.get("PROWLARR_TORRENT_CLIENT", "")
        url = normalize_http_url(config.get("QBITTORRENT_URL", ""))
        return client == "qbittorrent" and bool(url)

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to qBittorrent."""
        try:
            self._client.auth_log_in()
            api_version = self._client.app.web_api_version
            return True, f"Connected to qBittorrent (API v{api_version})"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
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
            # Use configured category if not explicitly provided
            category = category or self._category

            # Ensure category exists (may already exist, which is fine)
            try:
                self._client.torrents_create_category(name=category)
            except Exception as e:
                # Conflict409Error means category exists - that's expected
                # Log other errors but continue since download may still work
                if "Conflict" not in type(e).__name__ and "409" not in str(e):
                    logger.debug(f"Could not create category '{category}': {type(e).__name__}: {e}")

            torrent_info = extract_torrent_info(url, expected_hash=expected_hash)
            expected_hash = torrent_info.info_hash
            torrent_data = torrent_info.torrent_data

            # Add the torrent - use file content if we have it, otherwise URL
            if torrent_data:
                result = self._client.torrents_add(
                    torrent_files=torrent_data,
                    category=category,
                    rename=name,
                )
            else:
                # Use magnet URL if available, otherwise original URL
                add_url = torrent_info.magnet_url or url
                result = self._client.torrents_add(
                    urls=add_url,
                    category=category,
                    rename=name,
                )

            logger.debug(f"qBittorrent add result: {result}")

            if result == "Ok.":
                if not expected_hash:
                    raise Exception("Could not determine torrent hash from URL")

                # Wait for torrent to appear in client.
                # Use `/torrents/properties?hash=` rather than relying on `torrents/info`
                # listing being immediately consistent.
                for _ in range(10):
                    loaded, error = self._is_torrent_loaded(expected_hash)
                    if error:
                        logger.debug(f"qBittorrent add_download: {error}")
                    if loaded:
                        logger.info(f"Added torrent: {expected_hash}")
                        return expected_hash.lower()
                    time.sleep(0.5)

                # Client said Ok, trust it
                logger.warning(f"Torrent not yet visible, returning expected hash")
                return expected_hash

            raise Exception(f"Failed to add torrent: {result}")
        except Exception as e:
            logger.error(f"qBittorrent add failed: {e}")
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
            torrents, error = self._get_torrents_info(download_id)
            if error:
                return DownloadStatus.error(error)

            torrent = next(
                (
                    t
                    for t in torrents
                    if isinstance(getattr(t, "hash", None), str)
                    and _hashes_match(getattr(t, "hash"), download_id)
                ),
                None,
            )
            if not torrent:
                return DownloadStatus.error("Torrent not found in qBittorrent")

            # Map qBittorrent states to our states and user-friendly messages
            state_info = {
                "downloading": ("downloading", None),  # None = use default progress message
                "stalledDL": ("downloading", "Stalled"),
                "metaDL": ("downloading", "Fetching metadata"),
                "forcedDL": ("downloading", None),
                "allocating": ("downloading", "Allocating space"),
                "uploading": ("seeding", "Seeding"),
                "stalledUP": ("seeding", "Seeding (stalled)"),
                "forcedUP": ("seeding", "Seeding"),
                "pausedDL": ("paused", "Paused"),
                "pausedUP": ("paused", "Paused"),
                "queuedDL": ("queued", "Queued"),
                "queuedUP": ("queued", "Queued"),
                "checkingDL": ("checking", "Checking files"),
                "checkingUP": ("checking", "Checking files"),
                "checkingResumeData": ("checking", "Checking resume data"),
                "moving": ("processing", "Moving files"),
                "error": ("error", "Error"),
                "missingFiles": ("error", "Missing files"),
                "unknown": ("unknown", "Unknown state"),
            }

            torrent_state = getattr(torrent, "state", "unknown")
            state, message = state_info.get(torrent_state, ("unknown", str(torrent_state)))

            torrent_progress = getattr(torrent, "progress", 0.0)
            # Don't mark complete while files are being moved to final location
            # (qBittorrent moves files from incomplete → complete folder)
            complete = torrent_progress >= 1.0 and torrent_state != "moving"

            # For active downloads without a special message, leave message as None
            # so the handler can build the progress message
            if complete:
                message = "Complete"

            torrent_eta = getattr(torrent, "eta", 0)
            eta = torrent_eta if isinstance(torrent_eta, int) and 0 < torrent_eta < 604800 else None

            # Get file path for completed downloads
            file_path = None
            if complete:
                file_path = self._resolve_completed_download_path(torrent)

            torrent_speed = getattr(torrent, "dlspeed", None)
            torrent_speed = torrent_speed if isinstance(torrent_speed, int) else None

            return DownloadStatus(
                progress=float(torrent_progress) * 100,
                state="complete" if complete else state,
                message=message,
                complete=complete,
                file_path=file_path,
                download_speed=torrent_speed,
                eta=eta,
            )
        except Exception as e:
            return DownloadStatus.error(self._log_error("get_status", e))

    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        """
        Remove a torrent from qBittorrent.

        Args:
            download_id: Torrent info_hash
            delete_files: Whether to also delete files

        Returns:
            True if successful.
        """
        try:
            self._client.torrents_delete(
                torrent_hashes=download_id, delete_files=delete_files
            )
            logger.info(
                f"Removed torrent from qBittorrent: {download_id}"
                + (" (with files)" if delete_files else "")
            )
            return True
        except Exception as e:
            self._log_error("remove", e)
            return False

    def get_download_path(self, download_id: str) -> Optional[str]:
        """Get the path where torrent files are located.

        Prefer `content_path` when available.

        When `content_path` is missing (commonly with qBittorrent-like emulators such
        as Amarr), derive the path using:
        - `/api/v2/torrents/properties?hash=<hash>` for `save_path`
        - `/api/v2/torrents/files?hash=<hash>` for the first file name
        - join `save_path` with the torrent's top-level directory
        """
        import os

        try:
            torrents, error = self._get_torrents_info(download_id)
            if error:
                logger.debug(f"qBittorrent get_download_path: {error}")
                return None

            torrent = next(
                (
                    t
                    for t in torrents
                    if isinstance(getattr(t, "hash", None), str)
                    and _hashes_match(getattr(t, "hash"), download_id)
                ),
                None,
            )
            if not torrent:
                return None

            return self._resolve_completed_download_path(torrent)
        except Exception as e:
            self._log_error("get_download_path", e, level="debug")
            return None

    def _resolve_completed_download_path(self, torrent: SimpleNamespace) -> Optional[str]:
        """Resolve the completed path for a torrent.

        Centralizes the logic shared by `get_status()` and `get_download_path()`:
        - accept `content_path` only when it's not equal to `save_path`
        - otherwise derive via properties+files
        - finally fall back to `save_path + name`
        """

        # Prefer content_path, but treat content_path == save_path as invalid.
        content_path = getattr(torrent, "content_path", "")
        save_path = getattr(torrent, "save_path", "")
        if content_path and (not save_path or str(content_path) != str(save_path)):
            return str(content_path)

        download_id = getattr(torrent, "hash", "")
        if isinstance(download_id, str) and download_id:
            derived = self._derive_download_path_from_files(download_id)
            if derived:
                return derived

        # Legacy fallback: save_path + name (for older clients/emulators)
        return self._build_path(
            getattr(torrent, "save_path", ""),
            getattr(torrent, "name", ""),
        )

    def _derive_download_path_from_files(self, download_id: str) -> Optional[str]:
        """Derive completed download path using `/torrents/properties` + `/torrents/files`.

        This mirrors how common automation apps derive the path when
        `content_path` isn't provided.
        """
        import os
        import requests

        def get_with_auth(url: str, params: dict[str, str]) -> requests.Response:
            self._client.auth_log_in()
            resp = self._client._session.get(url, params=params, timeout=10)
            if resp.status_code == 403:
                logger.debug("qBittorrent returned 403; re-authenticating and retrying")
                self._client.auth_log_in()
                resp = self._client._session.get(url, params=params, timeout=10)
            return resp

        try:
            properties_url = f"{self._base_url}/api/v2/torrents/properties"
            files_url = f"{self._base_url}/api/v2/torrents/files"

            props_resp = get_with_auth(properties_url, {"hash": download_id})
            if props_resp.status_code == 404:
                return None
            props_resp.raise_for_status()
            props = props_resp.json() if isinstance(props_resp.json(), dict) else {}

            save_path = props.get("save_path") or props.get("savePath") or ""
            if not isinstance(save_path, str) or not save_path:
                return None

            files_resp = get_with_auth(files_url, {"hash": download_id})
            if files_resp.status_code == 404:
                return None
            files_resp.raise_for_status()
            files = files_resp.json() if isinstance(files_resp.json(), list) else []
            if not files:
                return None

            first_name = files[0].get("name") if isinstance(files[0], dict) else None
            if not isinstance(first_name, str) or not first_name:
                return None

            # Get the first path segment (qBittorrent returns '/' even on Windows).
            first_name_norm = first_name.replace("\\", "/")
            top_level = first_name_norm.split("/", 1)[0]
            if not top_level:
                return None

            return os.path.normpath(os.path.join(save_path, top_level))
        except Exception as e:
            logger.debug(f"qBittorrent could not derive path from files: {type(e).__name__}: {e}")
            return None

    def find_existing(self, url: str) -> Optional[Tuple[str, DownloadStatus]]:
        """Check if a torrent for this URL already exists in qBittorrent."""
        try:
            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            torrents, error = self._get_torrents_info(torrent_info.info_hash)
            if error:
                logger.debug(f"qBittorrent find_existing: {error}")
                return None

            torrent = next(
                (
                    t
                    for t in torrents
                    if isinstance(getattr(t, "hash", None), str)
                    and _hashes_match(getattr(t, "hash"), torrent_info.info_hash)
                ),
                None,
            )
            if torrent and isinstance(getattr(torrent, "hash", None), str):
                torrent_hash = getattr(torrent, "hash")
                return (torrent_hash.lower(), self.get_status(torrent_hash.lower()))

            return None
        except Exception as e:
            logger.debug(f"Error checking for existing torrent: {e}")
            return None
