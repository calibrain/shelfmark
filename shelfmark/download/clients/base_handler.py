"""Shared download handler for external torrent/usenet clients."""

import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Optional

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.core.utils import is_audiobook
from shelfmark.download.clients import (
    DownloadClient,
    DownloadState,
    get_client,
    list_configured_clients,
)
from shelfmark.download.fs import run_blocking_io
from shelfmark.release_sources import DownloadHandler

logger = setup_logger(__name__)

# How often to poll the download client for status (seconds)
POLL_INTERVAL = 2
# How long to wait for completed files to appear (seconds)
COMPLETED_PATH_RETRY_INTERVAL = 5
COMPLETED_PATH_MAX_ATTEMPTS = 12  # 12 attempts * 5s = 60s grace period


@dataclass(frozen=True)
class DownloadRequest:
    """Source-specific download parameters resolved before sending to a client."""

    url: str
    protocol: str
    release_name: str
    expected_hash: Optional[str]


def _diagnose_path_issue(path: str) -> str:
    """
    Analyze a path and return diagnostic hints for common issues.

    Args:
        path: The path that failed to be accessed

    Returns:
        A hint string to help users diagnose the issue.
    """
    # Detect Windows-style paths (won't work in Linux containers)
    if len(path) >= 2 and path[1] == ':':
        return (
            f"Path '{path}' appears to be a Windows path. "
            f"Shelfmark runs in Linux and cannot access Windows paths directly. "
            f"Ensure your download client uses Linux-style paths (/path/to/files)."
        )

    # Detect backslashes (Windows path separators)
    if "\\" in path:
        return (
            f"Path '{path}' contains backslashes. "
            f"This may indicate a Windows path or incorrect path escaping. "
            f"Linux paths should use forward slashes (/)."
        )

    # Generic hint for Linux paths
    return (
        f"Path '{path}' is not accessible from Shelfmark's container. "
        f"Ensure both containers have matching volume mounts for this directory, "
        f"or configure Remote Path Mappings in Settings > Advanced."
    )


class ExternalClientHandler(DownloadHandler, ABC):
    """Shared lifecycle handler for sources that hand off to torrent/usenet clients."""

    def __init__(self):
        # Track downloads that may need client-side cleanup after Shelfmark completes import.
        # task_id -> (client, download_id, protocol)
        self._cleanup_refs: dict[str, tuple[DownloadClient, str, str]] = {}

    @abstractmethod
    def _resolve_download(
        self,
        task: DownloadTask,
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[DownloadRequest]:
        """Resolve source-specific task metadata into a client download request."""

    def _on_download_complete(self, task: DownloadTask) -> None:
        """Hook called after successful completion; override for source cleanup."""
        return

    def _get_client(self, protocol: str) -> Optional[DownloadClient]:
        """Resolve the active client for a protocol."""
        return get_client(protocol)

    def _list_configured_clients(self) -> list[str]:
        """List protocols with configured clients."""
        return list_configured_clients()

    def _poll_interval(self) -> float:
        """Polling interval for status checks (seconds)."""
        return POLL_INTERVAL

    def _completed_path_retry_interval(self) -> float:
        """Retry interval while waiting for completed files (seconds)."""
        return COMPLETED_PATH_RETRY_INTERVAL

    def _completed_path_max_attempts(self) -> int:
        """Maximum attempts when waiting for completed files."""
        return COMPLETED_PATH_MAX_ATTEMPTS

    def _get_category_for_task(self, client: DownloadClient, task: DownloadTask) -> Optional[str]:
        """Get audiobook category if configured and applicable, else None for default."""
        if not is_audiobook(task.content_type):
            return None

        # Client-specific audiobook category config keys
        audiobook_keys = {
            "qbittorrent": "QBITTORRENT_CATEGORY_AUDIOBOOK",
            "transmission": "TRANSMISSION_CATEGORY_AUDIOBOOK",
            "deluge": "DELUGE_CATEGORY_AUDIOBOOK",
            "nzbget": "NZBGET_CATEGORY_AUDIOBOOK",
            "sabnzbd": "SABNZBD_CATEGORY_AUDIOBOOK",
        }
        audiobook_key = audiobook_keys.get(client.name)
        return config.get(audiobook_key, "") or None if audiobook_key else None

    def post_process_cleanup(self, task: DownloadTask, success: bool) -> None:
        if not success:
            self._cleanup_refs.pop(task.task_id, None)
            return

        client_ref = self._cleanup_refs.pop(task.task_id, None)
        if client_ref is None:
            return

        client, download_id, protocol = client_ref
        if protocol != "usenet":
            return

        # "Move" means copy into ingest then let the usenet client delete its own files.
        if config.get("PROWLARR_USENET_ACTION", "move") != "move":
            return

        try:
            self._delete_local_download_data(client, download_id)
            self._remove_usenet_download(client, download_id, delete_files=True, archive=True)
        except Exception as e:
            logger.warning(
                f"Failed to cleanup usenet download {download_id} in {getattr(client, 'name', 'client')}: {e}"
            )

    def _remove_usenet_download(
        self,
        client: DownloadClient,
        download_id: str,
        *,
        delete_files: bool,
        archive: bool = True,
    ) -> None:
        """Remove a usenet download with SABnzbd-specific archive handling."""
        if getattr(client, "name", "") == "sabnzbd":
            client.remove(download_id, delete_files=delete_files, archive=archive)
        else:
            client.remove(download_id, delete_files=delete_files)

    def _delete_local_download_data(self, client: DownloadClient, download_id: str) -> None:
        """Best-effort local deletion of client download data."""
        try:
            raw_path = client.get_download_path(download_id)
        except Exception as e:
            logger.debug(f"Failed to resolve download path for {client.name} {download_id}: {e}")
            return

        if not raw_path:
            logger.debug(f"No download path available for {client.name} {download_id}")
            return

        from shelfmark.core.path_mappings import (
            get_client_host_identifier,
            parse_remote_path_mappings,
            remap_remote_to_local_with_match,
        )

        source_path_obj = Path(raw_path)
        host = get_client_host_identifier(client) or ""
        mapping_value = config.get("PROWLARR_REMOTE_PATH_MAPPINGS", [])
        mappings = parse_remote_path_mappings(mapping_value)
        remapped, matched_mapping = remap_remote_to_local_with_match(
            mappings=mappings,
            host=host,
            remote_path=source_path_obj,
        )

        delete_path = remapped if matched_mapping else source_path_obj

        if str(delete_path) in ("", "/"):
            logger.warning(f"Refusing to delete unsafe path for {client.name} {download_id}: {delete_path}")
            return

        if not run_blocking_io(delete_path.exists):
            logger.debug(f"Local download path does not exist for cleanup: {delete_path}")
            return

        try:
            if run_blocking_io(delete_path.is_dir):
                run_blocking_io(shutil.rmtree, delete_path)
            else:
                run_blocking_io(delete_path.unlink)
            logger.info(f"Deleted local download data for {client.name} {download_id}: {delete_path}")
        except Exception as e:
            logger.warning(f"Failed to delete local download data for {client.name} {download_id}: {e}")

    def _safe_remove_download(self, client, download_id: str, protocol: str, reason: str) -> None:
        """Best-effort removal of a failed/cancelled download from the client.

        Safety policy:
        - torrents: never remove or delete client data (avoid breaking seeding)
        - usenet: keep legacy behavior (delete client files on removal)
        """

        if protocol != "usenet":
            logger.info(
                "Skipping download client cleanup for protocol=%s after %s (client=%s id=%s)",
                protocol,
                reason,
                getattr(client, "name", "client"),
                download_id,
            )
            return

        try:
            # Permanent delete for failed usenet downloads (SABnzbd archive=0).
            self._delete_local_download_data(client, download_id)
            self._remove_usenet_download(client, download_id, delete_files=True, archive=False)
        except Exception as e:
            logger.warning(
                f"Failed to remove download {download_id} from {client.name} after {reason}: {e}"
            )

    def _handle_cancelled_download(
        self,
        client: DownloadClient,
        download_id: str,
        protocol: str,
        status_callback: Callable[[str, Optional[str]], None],
    ) -> None:
        if protocol == "usenet":
            logger.info(f"Download cancelled, removing from {client.name}: {download_id}")
            try:
                self._delete_local_download_data(client, download_id)
                self._remove_usenet_download(client, download_id, delete_files=True, archive=True)
            except Exception as e:
                logger.warning(
                    f"Failed to remove download {download_id} from {client.name} after cancellation: {e}"
                )
        else:
            logger.info(
                f"Download cancelled for protocol={protocol}; leaving in {client.name}: {download_id}"
            )
        status_callback("cancelled", "Cancelled")

    def _resolve_download_path_once(
        self,
        client: DownloadClient,
        download_id: str,
        *,
        log_details: bool,
    ) -> tuple[Optional[Path], Optional[str]]:
        """Resolve and validate the completed download path once."""
        try:
            raw_path = client.get_download_path(download_id)
        except Exception as e:
            message = (
                f"Could not locate completed download in {client.name} (path not returned). "
                f"Check volume mappings and category settings."
            )
            if log_details:
                logger.error(
                    f"Failed to resolve download path for {client.name} {download_id}: {e}"
                )
            else:
                logger.debug(
                    f"Failed to resolve download path for {client.name} {download_id}: {e}"
                )
            return None, message

        if not raw_path:
            message = (
                f"Could not locate completed download in {client.name} (path not returned). "
                f"Check volume mappings and category settings."
            )
            if log_details:
                logger.error(f"Download client returned empty path for {client.name} {download_id}")
            else:
                logger.debug(f"Download client returned empty path for {client.name} {download_id}")
            return None, message

        from shelfmark.core.path_mappings import (
            get_client_host_identifier,
            parse_remote_path_mappings,
            remap_remote_to_local_with_match,
        )

        source_path_obj = Path(raw_path)
        host = get_client_host_identifier(client) or ""
        mapping_value = config.get("PROWLARR_REMOTE_PATH_MAPPINGS", [])
        mappings = parse_remote_path_mappings(mapping_value)

        if log_details:
            logger.debug(
                "Attempting path remap: client=%s, host=%s, path=%s, mappings=%s",
                client.name,
                host,
                source_path_obj,
                [(m.host, m.remote_path, m.local_path) for m in mappings],
            )

        remapped, matched_mapping = remap_remote_to_local_with_match(
            mappings=mappings,
            host=host,
            remote_path=source_path_obj,
        )

        if log_details:
            remapped_exists = run_blocking_io(remapped.exists)
            logger.debug(
                "Remap result: %s -> %s (exists=%s, changed=%s, matched=%s)",
                source_path_obj,
                remapped,
                remapped_exists,
                remapped != source_path_obj,
                matched_mapping,
            )

        if matched_mapping:
            if run_blocking_io(remapped.exists):
                logger.info(
                    "Remapped download path for %s (%s): %s -> %s",
                    client.name,
                    download_id,
                    source_path_obj,
                    remapped,
                )
                return remapped, None

            message = (
                f"Remapped path '{remapped}' does not exist. "
                f"Check your Docker volume mounts match the Local Path in Settings > Advanced > Remote Path Mappings."
            )
            if log_details:
                logger.error(
                    f"Download path does not exist after remapping: {raw_path} -> {remapped}. "
                    f"Client: {client.name}, ID: {download_id}."
                )
            else:
                logger.debug(
                    f"Download path does not exist after remapping: {raw_path} -> {remapped}. "
                    f"Client: {client.name}, ID: {download_id}."
                )
            return None, message

        if mappings:
            if run_blocking_io(source_path_obj.exists):
                logger.info(
                    "No remote path mapping matched for %s (%s); using client path: %s",
                    client.name,
                    download_id,
                    source_path_obj,
                )
                return source_path_obj, None

            hint = _diagnose_path_issue(raw_path)
            message = f"{hint} No remote path mapping matched for client '{client.name}'."
            if log_details:
                logger.error(
                    f"Download path does not exist and no remote path mapping matched for {client.name} "
                    f"({download_id}): {raw_path}. {hint}"
                )
            else:
                logger.debug(
                    f"Download path does not exist and no remote path mapping matched for {client.name} "
                    f"({download_id}): {raw_path}. {hint}"
                )
            return None, message

        if not run_blocking_io(source_path_obj.exists):
            hint = _diagnose_path_issue(raw_path)
            message = hint
            if log_details:
                logger.error(
                    f"Download path does not exist: {raw_path}. "
                    f"Client: {client.name}, ID: {download_id}. {hint}"
                )
            else:
                logger.debug(
                    f"Download path does not exist: {raw_path}. "
                    f"Client: {client.name}, ID: {download_id}. {hint}"
                )
            return None, message

        return source_path_obj, None

    def _wait_for_completed_path(
        self,
        client: DownloadClient,
        download_id: str,
        *,
        cancel_flag: Optional[Event],
        status_callback: Callable[[str, Optional[str]], None],
    ) -> tuple[Optional[Path], Optional[str]]:
        """Wait briefly for completed files to appear on disk."""
        last_error: Optional[str] = None
        max_attempts = self._completed_path_max_attempts()
        retry_interval = self._completed_path_retry_interval()

        for attempt in range(1, max_attempts + 1):
            if cancel_flag and cancel_flag.is_set():
                return None, last_error

            log_details = attempt == max_attempts
            resolved_path, error = self._resolve_download_path_once(
                client,
                download_id,
                log_details=log_details,
            )
            if resolved_path:
                return resolved_path, None

            last_error = error

            if attempt < max_attempts:
                status_callback("locating", "Waiting for completed files...")
                logger.debug(
                    "Completed files not available yet for %s (%s) (attempt %d/%d)",
                    client.name,
                    download_id,
                    attempt,
                    max_attempts,
                )

                if cancel_flag:
                    if cancel_flag.wait(timeout=retry_interval):
                        return None, last_error
                else:
                    time.sleep(retry_interval)

        return None, last_error

    def _build_progress_message(self, status) -> str:
        """Build a progress message from download status."""
        msg = f"{status.progress:.0f}%"

        if status.download_speed and status.download_speed > 0:
            speed_mb = status.download_speed / 1024 / 1024
            msg += f" ({speed_mb:.1f} MB/s)"

        if status.eta and status.eta > 0:
            if status.eta < 60:
                msg += f" - {status.eta}s left"
            elif status.eta < 3600:
                msg += f" - {status.eta // 60}m left"
            else:
                msg += f" - {status.eta // 3600}h {(status.eta % 3600) // 60}m left"

        return msg

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        """Execute download via configured torrent/usenet client. Returns file path or None."""
        try:
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
                return None

            request = self._resolve_download(task, status_callback)
            if not request:
                return None

            client = self._get_client(request.protocol)
            if not client:
                configured = self._list_configured_clients()
                if not configured:
                    status_callback(
                        "error",
                        "No download clients configured. Configure qBittorrent or NZBGet in settings.",
                    )
                else:
                    status_callback("error", f"No {request.protocol} client configured")
                return None

            # Check if this download already exists in the client
            status_callback("resolving", f"Checking {client.name}")
            category = self._get_category_for_task(client, task)
            existing = client.find_existing(request.url, category=category)

            if existing:
                download_id, existing_status = existing
                logger.info(f"Found existing download in {client.name}: {download_id}")

                # If already complete, skip straight to file handling
                if existing_status.complete:
                    logger.info("Existing download is complete, copying file directly")
                    status_callback("resolving", "Found existing download, copying to library")

                    source_path_obj, path_error = self._wait_for_completed_path(
                        client=client,
                        download_id=download_id,
                        cancel_flag=cancel_flag,
                        status_callback=status_callback,
                    )
                    if not source_path_obj:
                        if cancel_flag.is_set():
                            return None
                        status_callback(
                            "error",
                            path_error
                            or f"Could not locate existing download in {client.name}. Check that the file still exists.",
                        )
                        return None

                    result = self._handle_completed_file(
                        source_path=source_path_obj,
                        protocol=request.protocol,
                        task=task,
                        status_callback=status_callback,
                    )

                    if result:
                        self._on_download_complete(task)
                        self._cleanup_refs[task.task_id] = (client, download_id, request.protocol)
                    return result

                # Existing but still downloading - join the progress polling
                logger.info("Existing download in progress, joining poll loop")
                status_callback("downloading", "Resuming existing download")
            else:
                # No existing download - add new
                status_callback("resolving", f"Sending to {client.name}")
                try:
                    download_id = client.add_download(
                        url=request.url,
                        name=request.release_name,
                        category=category,
                        expected_hash=request.expected_hash,
                    )
                except Exception as e:
                    logger.error(f"Failed to add to {client.name}: {e}")
                    status_callback("error", f"Failed to add to {client.name}: {e}")
                    return None

                logger.info(f"Added to {client.name}: {download_id} for '{request.release_name}'")

            # Poll for progress
            return self._poll_and_complete(
                client=client,
                download_id=download_id,
                protocol=request.protocol,
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=progress_callback,
                status_callback=status_callback,
            )

        except Exception as e:
            logger.error(f"External client download error: {e}")
            status_callback("error", str(e))
            return None

    def _poll_and_complete(
        self,
        client: DownloadClient,
        download_id: str,
        protocol: str,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        """Poll the download client for progress and handle completion."""
        poll_interval = self._poll_interval()
        # Track consecutive "not found" errors - torrents may take time to appear in client
        not_found_count = 0
        max_not_found_retries = 15  # 15 retries * poll interval ~= 30s grace period

        try:
            logger.debug(f"Starting poll for {download_id} (content_type={task.content_type})")
            while not cancel_flag.is_set():
                status = client.get_status(download_id)
                progress_callback(status.progress)

                # Check for completion
                if status.complete:
                    if status.state == DownloadState.ERROR:
                        logger.error(f"Download {download_id} completed with error: {status.message}")
                        status_callback("error", status.message or "Download failed")
                        self._safe_remove_download(client, download_id, protocol, "completion error")
                        return None
                    # Download complete - break to handle file
                    logger.debug(f"Download {download_id} complete, file_path={status.file_path}")
                    break

                # Check for error state
                if status.state == DownloadState.ERROR:
                    message = (status.message or "").strip()
                    message_lower = message.lower()

                    # Only treat *actual* "not found" as retryable.
                    # qBittorrent auth/network/API failures should surface immediately (more actionable)
                    # and must not be confused with "torrent missing".
                    retryable_not_found = any(
                        token in message_lower
                        for token in (
                            "torrent not found",
                            "not found in qbittorrent",
                            "download not found",
                        )
                    )

                    non_retryable = any(
                        token in message_lower
                        for token in (
                            "authentication failed",
                            "cannot connect",
                            "timed out",
                            "api request failed",
                        )
                    )

                    if retryable_not_found and not non_retryable:
                        not_found_count += 1
                        if not_found_count < max_not_found_retries:
                            logger.debug(
                                f"Download {download_id} not yet visible in client "
                                f"(attempt {not_found_count}/{max_not_found_retries})"
                            )
                            status_callback("resolving", "Waiting for download client...")
                            if cancel_flag.wait(timeout=poll_interval):
                                break
                            continue

                        logger.error(
                            f"Download {download_id} not found after {max_not_found_retries} attempts"
                        )
                    else:
                        # Fail fast on actionable errors (auth, connectivity, API issues)
                        logger.error(f"Download {download_id} error state: {status.message}")

                    status_callback("error", status.message or "Download failed")
                    self._safe_remove_download(client, download_id, protocol, "download error")
                    return None

                # Reset not-found counter on successful status check
                not_found_count = 0

                # Build status message - use client message if provided, else build progress
                msg = status.message or self._build_progress_message(status)
                if status.state == DownloadState.PROCESSING:
                    # Post-processing (e.g., SABnzbd verifying/extracting)
                    status_callback("resolving", msg)
                else:
                    status_callback("downloading", msg)

                # Wait for next poll (interruptible by cancel)
                if cancel_flag.wait(timeout=poll_interval):
                    break

            # Handle cancellation
            if cancel_flag.is_set():
                self._handle_cancelled_download(client, download_id, protocol, status_callback)
                return None

            # Handle completed file (wait briefly for files to appear)
            source_path_obj, path_error = self._wait_for_completed_path(
                client=client,
                download_id=download_id,
                cancel_flag=cancel_flag,
                status_callback=status_callback,
            )
            if not source_path_obj:
                if cancel_flag.is_set():
                    self._handle_cancelled_download(client, download_id, protocol, status_callback)
                    return None
                status_callback(
                    "error",
                    path_error
                    or f"Could not locate completed download in {client.name} (path not returned). Check volume mappings and category settings.",
                )
                return None

            result = self._handle_completed_file(
                source_path=source_path_obj,
                protocol=protocol,
                task=task,
                status_callback=status_callback,
            )

            # Clean up on success
            if result:
                self._on_download_complete(task)
                self._cleanup_refs[task.task_id] = (client, download_id, protocol)

            return result

        except Exception as e:
            logger.error(f"Error during download polling: {e}")
            status_callback("error", str(e))
            self._safe_remove_download(client, download_id, protocol, "polling exception")
            return None

    def _handle_completed_file(
        self,
        source_path: Path,
        protocol: str,
        task: DownloadTask,
        status_callback: Callable[[str, Optional[str]], None],
    ) -> Optional[str]:
        """Handle a completed download and return its path.

        For external download clients (torrents/usenet), staging large payloads into TMP_DIR
        is expensive (and can duplicate multi-GB files). Instead, return the client's
        completed path and let the orchestrator perform any required transfer (copy/move/
        hardlink) directly from that source.

        Torrents also set ``task.original_download_path`` so the orchestrator can detect
        seeding data and enable hardlinking when configured.
        """
        try:
            if protocol == "torrent":
                task.original_download_path = str(source_path)

            logger.debug(f"Download complete, returning original path: {source_path}")
            return str(source_path)

        except Exception as e:
            logger.error(f"Failed to finalize completed download at {source_path}: {e}")
            status_callback("error", f"Failed to finalize completed download: {e}")
            return None

    def cancel(self, task_id: str) -> bool:
        """Default cancellation (primary cancellation happens via cancel_flag)."""
        logger.debug(f"Cancel requested for external client task: {task_id}")
        return True
