"""AudiobookBay download handler - resolves magnet links and uses shared client lifecycle."""

from typing import Callable, Optional

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.download.clients import DownloadClient, get_client, list_configured_clients
from shelfmark.download.clients.base_handler import DownloadRequest, ExternalClientHandler
from shelfmark.release_sources import register_handler
from shelfmark.release_sources.audiobookbay import scraper

logger = setup_logger(__name__)


@register_handler("audiobookbay")
class AudiobookBayHandler(ExternalClientHandler):
    """Handler for AudiobookBay downloads via configured torrent client."""

    def _get_client(self, protocol: str) -> Optional[DownloadClient]:
        """Compatibility shim so module-level patching still works in tests."""
        return get_client(protocol)

    def _list_configured_clients(self) -> list[str]:
        """Compatibility shim so module-level patching still works in tests."""
        return list_configured_clients()

    def _resolve_download(
        self,
        task: DownloadTask,
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[DownloadRequest]:
        """Resolve ABB detail page into a magnet-link download request."""
        detail_url = task.task_id
        hostname = config.get("ABB_HOSTNAME", "audiobookbay.lu")

        status_callback("resolving", "Extracting magnet link")
        magnet_link = scraper.extract_magnet_link(detail_url, hostname)

        if not magnet_link:
            status_callback("error", "Failed to extract magnet link from detail page")
            return None

        logger.info(f"Extracted magnet link for task {task.task_id}")

        return DownloadRequest(
            url=magnet_link,
            protocol="torrent",
            release_name=task.title or "Unknown",
            expected_hash=None,
        )

    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download (handled by cancel_flag in polling loop)."""
        logger.debug(f"Cancel requested for AudiobookBay task: {task_id}")
        return False
