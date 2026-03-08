"""Newznab download handler - resolves releases and delegates to shared clients."""

from typing import Callable, Optional

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
from shelfmark.release_sources.newznab.cache import get_release, remove_release

logger = setup_logger(__name__)

# Backwards-compat constants for tests patching this module.
POLL_INTERVAL = _DEFAULT_POLL_INTERVAL
COMPLETED_PATH_RETRY_INTERVAL = _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL
COMPLETED_PATH_MAX_ATTEMPTS = _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS


def _get_protocol(result: dict) -> str:
    """Infer download protocol from a Newznab result dict."""
    protocol = str(result.get("protocol", "")).lower()
    if protocol in ("torrent", "usenet"):
        return protocol

    download_url = str(result.get("downloadUrl") or "").lower()
    magnet_url = str(result.get("magnetUrl") or "").lower()

    if magnet_url.startswith("magnet:"):
        return "torrent"
    if download_url.startswith("magnet:") or ".torrent" in download_url:
        return "torrent"
    if ".nzb" in download_url:
        return "usenet"

    # Newznab indexers are usenet-native; default to usenet when ambiguous.
    return "usenet"


def _get_download_url(result: dict) -> str:
    """Pick the best URL to hand to a download client."""
    protocol = _get_protocol(result)
    magnet_url = str(result.get("magnetUrl") or "").strip()
    download_url = str(result.get("downloadUrl") or "").strip()

    if protocol == "torrent":
        return magnet_url or download_url
    return download_url or magnet_url


@register_handler("newznab")
class NewznabHandler(ExternalClientHandler):
    """Handler for Newznab downloads via configured usenet/torrent client."""

    def _get_client(self, protocol: str) -> Optional[DownloadClient]:
        return get_client(protocol)

    def _list_configured_clients(self) -> list[str]:
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
        result = get_release(task.task_id)
        if not result:
            logger.warning(f"Newznab release cache miss: {task.task_id}")
            status_callback("error", "Release not found in cache (may have expired)")
            return None

        download_url = _get_download_url(result)
        if not download_url:
            status_callback("error", "No download URL available")
            return None

        protocol = _get_protocol(result)
        if protocol not in ("torrent", "usenet"):
            status_callback("error", "Could not determine download protocol")
            return None

        release_name = result.get("title") or task.title or "Unknown"
        expected_hash = str(result.get("infoHash") or "").strip() or None

        return DownloadRequest(
            url=download_url,
            protocol=protocol,
            release_name=release_name,
            expected_hash=expected_hash,
        )

    def _on_download_complete(self, task: DownloadTask) -> None:
        remove_release(task.task_id)

    def cancel(self, task_id: str) -> bool:
        logger.debug(f"Cancel requested for Newznab task: {task_id}")
        remove_release(task_id)
        return super().cancel(task_id)
