"""NZBGet download client for Prowlarr integration.

Uses NZBGet's JSON-RPC API directly via requests (no external dependency).
"""

import json

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
    with_retry,
)
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)


@register_client("usenet")
class NZBGetClient(DownloadClient):
    """NZBGet download client using JSON-RPC API."""

    protocol = "usenet"
    name = "nzbget"

    def __init__(self) -> None:
        """Initialize NZBGet client with settings from config."""
        raw_url = config.get("NZBGET_URL", "")
        if not raw_url:
            msg = "NZBGET_URL is required"
            raise ValueError(msg)

        self.url = normalize_http_url(raw_url)
        if not self.url:
            msg = "NZBGET_URL is invalid"
            raise ValueError(msg)
        self.username = config.get("NZBGET_USERNAME", "nzbget")
        self.password = config.get("NZBGET_PASSWORD", "")
        self._category = config.get("NZBGET_CATEGORY", "Books")

    @staticmethod
    def is_configured() -> bool:
        """Check if NZBGet is configured and selected as the usenet client."""
        client = config.get("PROWLARR_USENET_CLIENT", "")
        url = normalize_http_url(config.get("NZBGET_URL", ""))
        return client == "nzbget" and bool(url)

    def _try_remove_command(
        self, command: str, nzb_id: int, download_id: str
    ) -> tuple[bool, Exception | None]:
        """Try one NZBGet delete command and return any error."""
        try:
            result = self._rpc_call("editqueue", [command, 0, "", nzb_id])
            if result:
                logger.info("Removed NZB from NZBGet (%s): %s", command, download_id)
                return True, None
        except Exception as e:
            return False, e
        return False, None

    @with_retry()
    def _rpc_call(self, method: str, params: list | None = None) -> object:
        """Make a JSON-RPC call to NZBGet.

        Args:
            method: RPC method name
            params: Method parameters

        Returns:
            Result from NZBGet.

        Raises:
            Exception: If RPC call fails after retries.

        """
        rpc_url = f"{self.url}/jsonrpc"

        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params or [],
            },
            separators=(",", ":"),
        )

        response = requests.post(
            rpc_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            auth=(self.username, self.password),
            timeout=30,
            verify=get_ssl_verify(rpc_url),
        )
        response.raise_for_status()

        result = response.json()
        if result.get("error"):
            raise RuntimeError(result["error"].get("message", "RPC error"))

        return result.get("result")

    def test_connection(self) -> tuple[bool, str]:
        """Test connection to NZBGet."""
        try:
            status = self._rpc_call("status")
            version = status.get("Version", "unknown")
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to NZBGet"
        except requests.exceptions.Timeout:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Connection failed: {e!s}"
        else:
            return True, f"Connected to NZBGet {version}"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: object,
    ) -> str:
        """Add NZB by URL.

        Fetches the NZB content from the URL (e.g., Prowlarr proxy) and sends
        it base64-encoded to NZBGet, since NZBGet may not handle redirects well.

        Args:
            url: NZB URL (can be Prowlarr proxy URL)
            name: Display name for the download
            category: Category for organization (uses configured default if not specified)
            expected_hash: Optional info_hash hint (unused)
            **kwargs: Client-specific options passed through to the implementation.

        Returns:
            NZBGet download ID (NZBID).

        Raises:
            Exception: If adding fails.

        """
        import base64

        # Use configured category if not explicitly provided
        category = category or self._category

        def _raise_invalid_nzb_id() -> None:
            msg = "NZBGet returned invalid ID"
            raise RuntimeError(msg)

        try:
            # Fetch NZB content from the URL (handles Prowlarr proxy redirects)
            logger.debug("Fetching NZB from: %s", url)
            response = requests.get(url, timeout=30, verify=get_ssl_verify(url))
            response.raise_for_status()
            nzb_content = base64.b64encode(response.content).decode("ascii")

            # Ensure filename has .nzb extension
            nzb_filename = name if name.endswith(".nzb") else f"{name}.nzb"

            # NZBGet append method parameters (all 10 required):
            # NZBFilename, Content, Category, Priority, AddToTop, AddPaused,
            # DupeKey, DupeScore, DupeMode, PPParameters
            nzb_id = self._rpc_call(
                "append",
                [
                    nzb_filename,  # NZBFilename
                    nzb_content,  # Content (base64-encoded NZB)
                    category,  # Category
                    0,  # Priority (0 = normal)
                    False,  # AddToTop
                    False,  # AddPaused
                    "",  # DupeKey
                    0,  # DupeScore
                    "SCORE",  # DupeMode
                    [],  # PPParameters (empty array)
                ],
            )

            if nzb_id and nzb_id > 0:
                logger.info("Added NZB to NZBGet: %s", nzb_id)
                return str(nzb_id)

            _raise_invalid_nzb_id()
        except requests.RequestException as e:
            logger.exception("Failed to fetch NZB from URL")
            msg = f"Failed to fetch NZB: {e}"
            raise RuntimeError(msg) from e
        except Exception:
            logger.exception("NZBGet add failed")
            raise

    def get_status(self, download_id: str) -> DownloadStatus:
        """Get NZB status by ID.

        Args:
            download_id: NZBGet NZBID

        Returns:
            Current download status.

        """
        try:
            nzb_id = int(download_id)

            # Check active downloads (queue)
            groups = self._rpc_call("listgroups", [0])

            for group in groups:
                if group.get("NZBID") == nzb_id:
                    # Calculate progress
                    # NZBGet uses Hi/Lo for 64-bit values on 32-bit systems
                    file_size = (group.get("FileSizeHi", 0) << 32) + group.get("FileSizeLo", 0)
                    remaining = (group.get("RemainingSizeHi", 0) << 32) + group.get(
                        "RemainingSizeLo", 0
                    )

                    progress = ((file_size - remaining) / file_size * 100) if file_size > 0 else 0
                    status = group.get("Status", "")

                    # Map NZBGet status to our states
                    if "DOWNLOADING" in status:
                        state = "downloading"
                    elif "PAUSED" in status:
                        state = "paused"
                    elif "QUEUED" in status:
                        state = "queued"
                    elif "POST-PROCESSING" in status or "UNPACKING" in status:
                        state = "processing"
                    else:
                        state = "unknown"

                    return DownloadStatus(
                        progress=progress,
                        state=state,
                        message=status.replace("-", " ").title(),
                        complete=False,
                        file_path=None,
                        download_speed=group.get("DownloadRate"),
                        eta=(
                            group.get("RemainingSec") if group.get("RemainingSec", 0) > 0 else None
                        ),
                    )

            # Check history for completed downloads
            history = self._rpc_call("history", [False])

            for item in history:
                if item.get("NZBID") == nzb_id:
                    status = item.get("Status", "")
                    # Prefer FinalDir (post-processing result) over DestDir (original)
                    final_dir = item.get("FinalDir", "") or None
                    dest_dir = item.get("DestDir", "") or None
                    file_path = final_dir or dest_dir  # Use FinalDir if available

                    # Normalize for consistent downstream use.
                    if isinstance(file_path, str) and file_path:
                        import os

                        file_path = os.path.normpath(file_path)
                    else:
                        file_path = None

                    if "SUCCESS" in status:
                        return DownloadStatus(
                            progress=100,
                            state="complete",
                            message="Complete",
                            complete=True,
                            file_path=file_path,
                        )
                    return DownloadStatus(
                        progress=100,
                        state="error",
                        message=f"Download failed: {status}",
                        complete=True,
                        file_path=file_path,
                    )

            # Not found in queue or history
            return DownloadStatus.error("Download not found")
        except Exception as e:
            return DownloadStatus.error(self._log_error("get_status", e))

    def remove(self, download_id: str, *, delete_files: bool = False) -> bool:
        """Remove a download from NZBGet.

        NZBGet can remove items from either the active queue (Group* commands) or from
        history (History* commands). Completed downloads are typically in history.

        Args:
            download_id: NZBGet NZBID
            delete_files: Whether to permanently delete downloaded files

        Returns:
            True if successful.

        """
        try:
            nzb_id = int(download_id)
        except (TypeError, ValueError) as e:
            self._log_error("remove", e)
            return False

        if delete_files:
            # Keep HistoryDelete as a fallback for
            # older NZBGet versions where HistoryFinalDelete may not exist.
            commands = ["GroupFinalDelete", "HistoryFinalDelete", "HistoryDelete"]
        else:
            commands = ["GroupDelete", "HistoryDelete"]

        last_error: Exception | None = None
        for command in commands:
            success, error = self._try_remove_command(command, nzb_id, download_id)
            if success:
                return True
            if error is not None:
                last_error = error

        if last_error is not None:
            self._log_error("remove", last_error)
        return False

    def get_download_path(self, download_id: str) -> str | None:
        """Get the path where NZB files are located.

        Args:
            download_id: NZBGet NZBID

        Returns:
            Destination directory, or None.

        """
        status = self.get_status(download_id)
        return status.file_path
