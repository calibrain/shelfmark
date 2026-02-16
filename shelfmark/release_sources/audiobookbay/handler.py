"""AudiobookBay download handler - extracts magnet links and sends to torrent clients."""

from threading import Event
from typing import Callable, Optional

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.core.utils import is_audiobook
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.audiobookbay import scraper
from shelfmark.release_sources.prowlarr.clients import (
    DownloadClient,
    get_client,
    list_configured_clients,
)

logger = setup_logger(__name__)


@register_handler("audiobookbay")
class AudiobookBayHandler(DownloadHandler):
    """Handler for AudiobookBay downloads via configured torrent client."""
    
    def _get_category_for_task(self, client: DownloadClient, task: DownloadTask) -> Optional[str]:
        """Get audiobook category if configured and applicable, else None for default."""
        if not is_audiobook(task.content_type):
            return None
        
        # Client-specific audiobook category config keys
        audiobook_keys = {
            "qbittorrent": "QBITTORRENT_CATEGORY_AUDIOBOOK",
            "transmission": "TRANSMISSION_CATEGORY_AUDIOBOOK",
            "deluge": "DELUGE_CATEGORY_AUDIOBOOK",
        }
        audiobook_key = audiobook_keys.get(client.name)
        if audiobook_key:
            category = config.get(audiobook_key, "")
            if category:
                return category
        
        # Fallback to general category
        general_keys = {
            "qbittorrent": "QBITTORRENT_CATEGORY",
            "transmission": "TRANSMISSION_CATEGORY",
            "deluge": "DELUGE_CATEGORY",
        }
        general_key = general_keys.get(client.name)
        if general_key:
            return config.get(general_key, "") or None
        
        return None
    
    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        """Execute download by extracting magnet link and sending to torrent client.
        
        Args:
            task: Download task with task_id containing detail URL
            cancel_flag: Event to check for cancellation
            progress_callback: Called with progress percentage (0-100)
            status_callback: Called with (status, message) for status updates
            
        Returns:
            None (torrents don't return file path immediately)
        """
        try:
            # Check for cancellation before starting
            if cancel_flag.is_set():
                logger.info(f"Download cancelled before starting: {task.task_id}")
                status_callback("cancelled", "Cancelled")
                return None
            
            # task.task_id contains the detail page URL
            detail_url = task.task_id
            hostname = config.get("ABB_HOSTNAME", "audiobookbay.lu")
            
            # Extract magnet link from detail page
            status_callback("resolving", "Extracting magnet link")
            magnet_link = scraper.extract_magnet_link(detail_url, hostname)
            
            if not magnet_link:
                status_callback("error", "Failed to extract magnet link from detail page")
                return None
            
            logger.info(f"Extracted magnet link: {magnet_link[:100]}...")
            
            # Get torrent client
            client = get_client("torrent")
            if not client:
                configured = list_configured_clients()
                if not configured:
                    status_callback("error", "No torrent clients configured. Configure qBittorrent or Transmission in settings.")
                else:
                    status_callback("error", "No torrent client configured")
                return None
            
            # Check if this download already exists in the client
            status_callback("resolving", f"Checking {client.name}")
            category = self._get_category_for_task(client, task)
            existing = client.find_existing(magnet_link, category=category)
            
            if existing:
                download_id, existing_status = existing
                logger.info(f"Found existing download in {client.name}: {download_id}")
                
                if existing_status.complete:
                    logger.info("Existing download is complete")
                    status_callback("resolving", "Found existing download")
                    # Return the path from the existing download
                    file_path = client.get_download_path(download_id)
                    if file_path:
                        return file_path
                    else:
                        status_callback("error", "Could not locate existing download path")
                        return None
                else:
                    logger.info("Existing download in progress")
                    status_callback("downloading", "Resuming existing download")
                    # Poll for completion (simplified - could reuse Prowlarr's polling logic)
                    # For now, just return None and let the orchestrator handle it
                    return None
            
            # Add new download
            status_callback("resolving", f"Sending to {client.name}")
            try:
                release_name = task.title or "Unknown"
                category = self._get_category_for_task(client, task)
                download_id = client.add_download(
                    url=magnet_link,
                    name=release_name,
                    category=category,
                    expected_hash=None,  # Extract from magnet if needed
                )
                logger.info(f"Added to {client.name}: {download_id} for '{release_name}'")
                status_callback("downloading", "Download started")
            except Exception as e:
                logger.error(f"Failed to add to {client.name}: {e}")
                status_callback("error", f"Failed to add to {client.name}: {e}")
                return None
            
            # Torrents don't return file path immediately
            # The orchestrator will handle polling via the download client
            return None
            
        except Exception as e:
            logger.error(f"AudiobookBay download error: {e}")
            status_callback("error", str(e))
            return None
    
    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download.
        
        Torrents can't be cancelled from Shelfmark side.
        User must cancel in torrent client.
        """
        logger.debug(f"Cancel requested for AudiobookBay task: {task_id}")
        # Torrents are managed by the client, we can't cancel them here
        return False
