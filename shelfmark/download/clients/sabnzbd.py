"""
SABnzbd download client for Prowlarr integration.

Uses SABnzbd's REST API directly via requests (no external dependency).
"""

from typing import Any, Optional, Tuple
from urllib.parse import urlparse

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify
from shelfmark.download.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
    with_retry,
)

logger = setup_logger(__name__)


def _parse_eta(eta_str: str) -> Optional[int]:
    """Parse SABnzbd ETA string (format: 'H:MM:SS') to seconds."""
    if not eta_str or eta_str == "0:00:00":
        return None
    try:
        parts = eta_str.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except (ValueError, IndexError):
        pass
    return None


def _parse_speed(slot: dict) -> Optional[int]:
    """Parse download speed from SABnzbd slot data, returning bytes/sec."""
    # Prefer kbpersec field (more reliable numeric value)
    kbpersec_str = slot.get("kbpersec", "")
    if kbpersec_str:
        try:
            return int(float(kbpersec_str) * 1024)
        except (ValueError, TypeError):
            pass

    # Fall back to human-readable speed field
    speed_str = slot.get("speed", "")
    if not speed_str:
        return None

    try:
        speed_parts = speed_str.split()
        if len(speed_parts) < 2:
            return None
        speed_val = float(speed_parts[0])
        unit = speed_parts[1].upper()
        multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3}
        for prefix, mult in multipliers.items():
            if prefix in unit:
                return int(speed_val * mult)
        return int(speed_val)
    except (ValueError, IndexError):
        return None


@register_client("usenet")
class SABnzbdClient(DownloadClient):
    """SABnzbd download client using REST API."""

    @staticmethod
    def _resolve_completed_storage_path(storage: str, title: str) -> str:
        """Normalize SABnzbd's `storage` into a stable "job root" folder.

        Walks up parent directories looking for a directory named exactly like the
        job `title`.

        This helps when SABnzbd reports a nested path (e.g. sorting/post-processing)
        but we want the root folder for the completed job.
        """
        from pathlib import Path

        storage = storage or ""
        title = (title or "").strip()
        if not storage or not title:
            return storage

        # SAB returns absolute paths; don't require existence on disk.
        path = Path(storage)
        best_match: Path | None = None

        for parent in [path, *path.parents]:
            if parent.name == title:
                best_match = parent

        return str(best_match) if best_match is not None else storage

    protocol = "usenet"
    name = "sabnzbd"

    def __init__(self):
        """Initialize SABnzbd client with settings from config."""
        raw_url = config.get("SABNZBD_URL", "")
        if not raw_url:
            raise ValueError("SABNZBD_URL is required")

        api_key = config.get("SABNZBD_API_KEY", "")
        if not api_key:
            raise ValueError("SABNZBD_API_KEY is required")

        self.url = normalize_http_url(raw_url)
        if not self.url:
            raise ValueError("SABNZBD_URL is invalid")
        self.api_key = api_key
        self._category = config.get("SABNZBD_CATEGORY", "books")

    @staticmethod
    def is_configured() -> bool:
        """Check if SABnzbd is configured and selected as the usenet client."""
        client = config.get("PROWLARR_USENET_CLIENT", "")
        url = normalize_http_url(config.get("SABNZBD_URL", ""))
        api_key = config.get("SABNZBD_API_KEY", "")
        return client == "sabnzbd" and bool(url) and bool(api_key)

    @with_retry()
    def _api_call(self, mode: str, params: Optional[dict] = None) -> Any:
        """
        Make an API call to SABnzbd.

        Args:
            mode: API mode (e.g., "version", "addurl", "queue", "history")
            params: Additional parameters

        Returns:
            JSON response from SABnzbd.

        Raises:
            Exception: If API call fails after retries.
        """
        api_url = f"{self.url}/api"

        request_params = {
            "apikey": self.api_key,
            "mode": mode,
            "output": "json",
        }
        if params:
            request_params.update(params)

        response = requests.get(api_url, params=request_params, timeout=30, verify=get_ssl_verify(api_url))
        response.raise_for_status()

        result = response.json()

        # Check for error in response
        if isinstance(result, dict) and result.get("status") is False:
            error = result.get("error", "Unknown error")
            raise Exception(f"SABnzbd error: {error}")

        return result

    def _api_post_file(self, nzb_content: bytes, filename: str, nzb_name: str, category: str) -> Any:
        """
        Upload an NZB file to SABnzbd using addfile.

        Returns:
            JSON response from SABnzbd.
        """
        api_url = f"{self.url}/api"
        request_params = {
            "apikey": self.api_key,
            "mode": "addfile",
            "output": "json",
            "nzbname": nzb_name,
            "cat": category,
        }
        files = {"name": (filename, nzb_content, "application/x-nzb")}

        response = requests.post(api_url, params=request_params, files=files, timeout=30, verify=get_ssl_verify(api_url))
        response.raise_for_status()
        result = response.json()

        if isinstance(result, dict) and result.get("status") is False:
            error = result.get("error", "Unknown error")
            raise Exception(f"SABnzbd error: {error}")

        return result

    def _fetch_nzb_content(self, url: str) -> bytes:
        """Fetch NZB content, including Prowlarr auth headers when appropriate."""
        headers = self._get_prowlarr_headers(url)
        response = requests.get(url, timeout=30, headers=headers, verify=get_ssl_verify(url))
        response.raise_for_status()
        return response.content

    def _get_prowlarr_headers(self, url: str) -> dict:
        # TODO: Move this source-specific Prowlarr auth handling into a source hook.
        api_key = str(config.get("PROWLARR_API_KEY", "") or "").strip()
        if not api_key:
            return {}

        prowlarr_url = normalize_http_url(config.get("PROWLARR_URL", ""))
        if not prowlarr_url:
            return {}

        try:
            target = urlparse(url)
            base = urlparse(prowlarr_url)
        except ValueError:
            return {}

        if target.hostname and base.hostname and target.hostname.lower() == base.hostname.lower():
            return {"X-Api-Key": api_key}

        return {}

    @staticmethod
    def _build_nzb_filename(name: str, url: str) -> str:
        base_name = (name or "download").strip() or "download"
        parsed = urlparse(url)
        path = parsed.path or ""
        lower_path = path.lower()

        if lower_path.endswith(".nzb.gz"):
            suffix = ".nzb.gz"
        elif lower_path.endswith(".nzb"):
            suffix = ".nzb"
        else:
            suffix = ""

        lower_base = base_name.lower()
        if suffix and lower_base.endswith(suffix):
            return base_name
        if suffix == ".nzb.gz" and lower_base.endswith(".nzb"):
            return f"{base_name}.gz"
        if suffix:
            return f"{base_name}{suffix}"
        if lower_base.endswith((".nzb", ".nzb.gz")):
            return base_name
        return f"{base_name}.nzb"

    @staticmethod
    def _extract_nzo_id(result: Any) -> str:
        if not isinstance(result, dict):
            raise Exception("SABnzbd returned invalid response")

        nzo_ids = result.get("nzo_ids") or result.get("nzo_id")
        if isinstance(nzo_ids, list) and nzo_ids:
            return str(nzo_ids[0])
        if isinstance(nzo_ids, str) and nzo_ids:
            return nzo_ids
        if isinstance(nzo_ids, int):
            return str(nzo_ids)

        raise Exception("SABnzbd returned no nzo_id")

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to SABnzbd."""
        try:
            result = self._api_call("version")
            version = result.get("version", "unknown")
            return True, f"Connected to SABnzbd {version}"
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to SABnzbd"
        except requests.exceptions.Timeout:
            return False, "Connection timed out"
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
        Add NZB by URL.

        Args:
            url: NZB URL (can be Prowlarr proxy URL)
            name: Display name for the download
            category: Category for organization (uses configured default if not specified)
            expected_hash: Optional info_hash hint (unused)

        Returns:
            SABnzbd nzo_id.

        Raises:
            Exception: If adding fails.
        """
        # Use configured category if not explicitly provided
        category = category or self._category

        try:
            logger.debug(f"Adding NZB to SABnzbd: {name}")
            nzb_filename = self._build_nzb_filename(name, url)
            nzb_content = self._fetch_nzb_content(url)
            result = self._api_post_file(nzb_content, nzb_filename, name, category)
            nzo_id = self._extract_nzo_id(result)
            logger.info(f"Added NZB to SABnzbd: {nzo_id}")
            return nzo_id
        except Exception as e:
            logger.warning(f"SABnzbd addfile failed, falling back to addurl: {e}")

        try:
            result = self._api_call(
                "addurl",
                {
                    "name": url,
                    "nzbname": name,
                    "cat": category,
                },
            )
            nzo_id = self._extract_nzo_id(result)
            logger.info(f"Added NZB to SABnzbd via addurl: {nzo_id}")
            return nzo_id
        except Exception as e:
            logger.error(f"SABnzbd add failed: {e}")
            raise

    def get_status(self, download_id: str) -> DownloadStatus:
        """
        Get NZB status by nzo_id.

        Args:
            download_id: SABnzbd nzo_id

        Returns:
            Current download status.
        """
        try:
            # Check active queue first
            queue_result = self._api_call("queue")
            queue = queue_result.get("queue", {})
            slots = queue.get("slots", [])

            for slot in slots:
                if slot.get("nzo_id") == download_id:
                    # Found in queue
                    status_text = slot.get("status", "").upper()
                    percentage = float(slot.get("percentage", 0))

                    # Map SABnzbd status to our states
                    status_mapping = {
                        "DOWNLOADING": "downloading",
                        "PAUSED": "paused",
                        "QUEUED": "queued",
                        "IDLE": "queued",
                        "PROPAGATING": "queued",
                        "FETCHING": "queued",
                        "GRABBING": "queued",
                        "VERIFYING": "processing",
                        "REPAIRING": "processing",
                        "EXTRACTING": "processing",
                        "MOVING": "processing",
                        "RUNNING": "processing",
                        "FAILED": "error",
                    }
                    state = status_mapping.get(status_text, "downloading")

                    return DownloadStatus(
                        progress=percentage,
                        state=state,
                        message=status_text.lower().replace("_", " ").title(),
                        complete=False,
                        file_path=None,
                        download_speed=_parse_speed(slot),
                        eta=_parse_eta(slot.get("timeleft", "")),
                    )

            # Not in queue, check history
            history_result = self._api_call("history", {"limit": 100})
            history = history_result.get("history", {})
            history_slots = history.get("slots", [])

            for slot in history_slots:
                if slot.get("nzo_id") == download_id:
                    status_text = slot.get("status", "").upper()
                    storage = slot.get("storage", "")
                    if storage is None:
                        storage = ""
                    logger.debug(f"SABnzbd history: {download_id} status={status_text} storage='{storage}'")

                    if status_text == "COMPLETED":
                        title = slot.get("name") or slot.get("nzb_name") or ""
                        resolved_storage = self._resolve_completed_storage_path(storage, title)

                        return DownloadStatus(
                            progress=100,
                            state="complete",
                            message="Complete",
                            complete=True,
                            file_path=resolved_storage,
                        )
                    elif status_text == "FAILED":
                        fail_message = slot.get("fail_message", "Download failed")
                        title = slot.get("name") or slot.get("nzb_name") or ""
                        resolved_storage = self._resolve_completed_storage_path(storage, title)
                        return DownloadStatus(
                            progress=100,
                            state="error",
                            message=fail_message,
                            complete=True,
                            file_path=resolved_storage,
                        )
                    else:
                        # Post-processing states: Queued, QuickCheck, Verifying,
                        # Repairing, Fetching, Extracting, Moving, Running
                        # Keep polling - not yet complete
                        return DownloadStatus(
                            progress=100,
                            state="processing",
                            message=status_text.title(),
                            complete=False,
                            file_path=None,
                        )

            # Not found
            logger.warning(f"SABnzbd: download {download_id} not found in queue or history")
            return DownloadStatus.error("Download not found")
        except Exception as e:
            return DownloadStatus.error(self._log_error("get_status", e))

    def remove(self, download_id: str, delete_files: bool = False, archive: bool = True) -> bool:
        """
        Remove a download from SABnzbd.

        Args:
            download_id: SABnzbd nzo_id
            delete_files: Whether to delete the files
            archive: If True, move to archive instead of permanent delete (history only)

        Returns:
            True if successful.
        """
        # First try to remove from queue. If it isn't there (common for completed jobs),
        # fall back to history removal instead of failing fast on a SABnzbd error response.
        try:
            result = self._api_call(
                "queue",
                {
                    "name": "delete",
                    "value": download_id,
                    "del_files": 1 if delete_files else 0,
                },
            )

            if result.get("status"):
                logger.info(f"Removed NZB from SABnzbd queue: {download_id}")
                return True
        except Exception as e:
            logger.debug(f"SABnzbd queue delete skipped for {download_id}: {e}")

        # If not in queue (or queue delete failed), try to remove from history.
        try:
            result = self._api_call(
                "history",
                {
                    "name": "delete",
                    "value": download_id,
                    "del_files": 1 if delete_files else 0,
                    "archive": 1 if archive else 0,
                },
            )

            if result.get("status"):
                action = "archived" if archive else "removed"
                logger.info(f"NZB {action} from SABnzbd history: {download_id}")
                return True
        except Exception as e:
            self._log_error("remove", e)
            return False

        return False

    def get_download_path(self, download_id: str) -> Optional[str]:
        """
        Get the path where NZB files are located.

        Args:
            download_id: SABnzbd nzo_id

        Returns:
            Storage directory, or None.
        """
        status = self.get_status(download_id)
        return status.file_path

    def find_existing(
        self, url: str, category: Optional[str] = None
    ) -> Optional[Tuple[str, DownloadStatus]]:
        """
        Check if an NZB for this URL already exists in SABnzbd.

        Note: Unlike torrents which have a unique info_hash, usenet NZBs don't have
        a universal unique identifier. SABnzbd generates an nzo_id when adding,
        but there's no way to derive it from the URL. This method searches by
        NZB name extracted from the URL, which may not always be accurate.

        Args:
            url: NZB URL
            category: Category to filter by (defaults to configured category)

        Returns:
            Tuple of (nzo_id, status) if found, None if not found.
        """
        try:
            # Extract NZB name from URL (last path component without extension)
            from urllib.parse import unquote, urlparse
            parsed = urlparse(url)
            path = unquote(parsed.path)

            # Get filename from path
            if "/" in path:
                filename = path.rsplit("/", 1)[-1]
            else:
                filename = path

            # Remove common NZB extensions
            for ext in [".nzb", ".nzb.gz"]:
                if filename.lower().endswith(ext):
                    filename = filename[:-len(ext)]
                    break

            if not filename:
                return None

            # Use provided category or fall back to configured default
            search_category = category or self._category

            # Search queue (SABnzbd uses "cat" field for category in queue)
            queue_result = self._api_call("queue")
            queue = queue_result.get("queue", {})
            for slot in queue.get("slots", []):
                if slot.get("cat", "") != search_category:
                    continue
                slot_name = slot.get("filename", "")
                if filename.lower() in slot_name.lower():
                    nzo_id = slot.get("nzo_id")
                    if nzo_id:
                        status = self.get_status(nzo_id)
                        logger.debug(f"Found existing NZB in SABnzbd queue: {nzo_id}")
                        return (nzo_id, status)

            # Search history (SABnzbd uses "category" field in history)
            history_result = self._api_call("history", {"limit": 100})
            history = history_result.get("history", {})
            for slot in history.get("slots", []):
                if slot.get("category", "") != search_category:
                    continue
                slot_name = slot.get("name", "")
                if filename.lower() in slot_name.lower():
                    nzo_id = slot.get("nzo_id")
                    if nzo_id:
                        status = self.get_status(nzo_id)
                        logger.debug(f"Found existing NZB in SABnzbd history: {nzo_id}")
                        return (nzo_id, status)

            return None

        except Exception as e:
            logger.debug(f"Error checking for existing NZB: {e}")
            return None
