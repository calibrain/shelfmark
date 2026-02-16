"""Prowlarr download handler - resolves releases and delegates lifecycle to shared clients."""

from typing import Callable, Optional

from shelfmark.core.config import config  # noqa: F401 (compat patch target in tests)
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.download.clients import DownloadClient, get_client, list_configured_clients
from shelfmark.download.clients.base_handler import (
    COMPLETED_PATH_MAX_ATTEMPTS as _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS,
    COMPLETED_PATH_RETRY_INTERVAL as _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL,
    POLL_INTERVAL as _DEFAULT_POLL_INTERVAL,
    DownloadRequest,
    ExternalClientHandler,
)
from shelfmark.release_sources import register_handler
from shelfmark.release_sources.prowlarr.cache import get_release, remove_release
from shelfmark.release_sources.prowlarr.utils import get_preferred_download_url, get_protocol

logger = setup_logger(__name__)

# Backwards-compat constants for tests patching this module.
POLL_INTERVAL = _DEFAULT_POLL_INTERVAL
COMPLETED_PATH_RETRY_INTERVAL = _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL
COMPLETED_PATH_MAX_ATTEMPTS = _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS


@register_handler("prowlarr")
class ProwlarrHandler(ExternalClientHandler):
    """Handler for Prowlarr downloads via configured torrent or usenet client."""

    def _get_client(self, protocol: str) -> Optional[DownloadClient]:
        """Compatibility shim so module-level patching still works in tests."""
        return get_client(protocol)

    def _list_configured_clients(self) -> list[str]:
        """Compatibility shim so module-level patching still works in tests."""
        return list_configured_clients()

    def _poll_interval(self) -> float:
        return POLL_INTERVAL

    def _completed_path_retry_interval(self) -> float:
        return COMPLETED_PATH_RETRY_INTERVAL

    def _completed_path_max_attempts(self) -> int:
        return COMPLETED_PATH_MAX_ATTEMPTS

    def _resolve_download(
        self,
        task: DownloadTask,
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[DownloadRequest]:
        """Resolve Prowlarr cache entry into download request parameters."""
        # Look up the cached release
        prowlarr_result = get_release(task.task_id)
        if not prowlarr_result:
            logger.warning(f"Release cache miss: {task.task_id}")
            status_callback("error", "Release not found in cache (may have expired)")
            return None

        # Extract download URL
        download_url = get_preferred_download_url(prowlarr_result)
        if not download_url:
            status_callback("error", "No download URL available")
            return None

        # Determine protocol
        protocol = get_protocol(prowlarr_result)
        if protocol == "unknown":
            status_callback("error", "Could not determine download protocol")
            return None

        release_name = prowlarr_result.get("title") or task.title or "Unknown"
        expected_hash = str(prowlarr_result.get("infoHash") or "").strip() or None

        return DownloadRequest(
            url=download_url,
            protocol=protocol,
            release_name=release_name,
            expected_hash=expected_hash,
        )

    def _on_download_complete(self, task: DownloadTask) -> None:
        """Remove completed release from the Prowlarr cache."""
        remove_release(task.task_id)

    def cancel(self, task_id: str) -> bool:
        """Cancel download and clean up cache. Primary cancellation is via cancel_flag."""
        logger.debug(f"Cancel requested for Prowlarr task: {task_id}")
        remove_release(task_id)
        return super().cancel(task_id)
