"""Prowlarr download handler - resolves releases and delegates lifecycle to shared clients."""

from collections.abc import Callable

from shelfmark.core.config import config  # noqa: F401 (compat patch target in tests)
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.download.clients import (
    DownloadClient,
    get_client,
    list_configured_clients,
)
from shelfmark.download.clients.base_handler import (
    COMPLETED_PATH_MAX_ATTEMPTS as _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS,
)
from shelfmark.download.clients.base_handler import (
    COMPLETED_PATH_RETRY_INTERVAL as _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL,
)
from shelfmark.download.clients.base_handler import (
    POLL_INTERVAL as _DEFAULT_POLL_INTERVAL,
)
from shelfmark.download.clients.base_handler import (
    DownloadRequest,
    ExternalClientHandler,
)
from shelfmark.release_sources import register_handler
from shelfmark.release_sources.prowlarr.cache import get_release, remove_release
from shelfmark.release_sources.prowlarr.utils import (
    get_preferred_download_url,
    get_protocol,
)

logger = setup_logger(__name__)

# Backwards-compat constants for tests patching this module.
POLL_INTERVAL = _DEFAULT_POLL_INTERVAL
COMPLETED_PATH_RETRY_INTERVAL = _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL
COMPLETED_PATH_MAX_ATTEMPTS = _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS


def _coerce_seed_time_minutes(raw_seed_time: object) -> int | None:
    """Convert Prowlarr's minimum seed time from seconds to whole minutes."""
    if raw_seed_time is None:
        return None

    try:
        seed_time_seconds = int(raw_seed_time)
    except (TypeError, ValueError):
        logger.warning("Invalid Prowlarr minimumSeedTime value: %r", raw_seed_time)
        return None

    if seed_time_seconds < 0:
        logger.warning(
            "Ignoring negative Prowlarr minimumSeedTime value: %s", seed_time_seconds
        )
        return None

    # Round up so we never under-seed when a tracker uses a non-minute boundary.
    return (seed_time_seconds + 59) // 60


@register_handler("prowlarr")
class ProwlarrHandler(ExternalClientHandler):
    """Handler for Prowlarr downloads via configured torrent or usenet client."""

    def _get_client(self, protocol: str) -> DownloadClient | None:
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

    @classmethod
    def _restore_download_request_from_task(
        cls, task: DownloadTask
    ) -> DownloadRequest | None:
        """Rebuild a DownloadRequest when the in-memory Prowlarr cache is gone."""
        retry_download_url = normalize_optional_text(
            getattr(task, "retry_download_url", None)
        )
        retry_download_protocol = normalize_optional_text(
            getattr(task, "retry_download_protocol", None)
        )
        if retry_download_url is None or retry_download_protocol is None:
            return None

        protocol = retry_download_protocol.lower()
        if protocol not in {"torrent", "usenet"}:
            return None

        ratio_limit = getattr(task, "retry_ratio_limit", None)
        if not isinstance(ratio_limit, (int, float)) or isinstance(ratio_limit, bool):
            ratio_limit = None

        seeding_time_limit = getattr(task, "retry_seeding_time_limit_minutes", None)
        if not isinstance(seeding_time_limit, int) or isinstance(
            seeding_time_limit, bool
        ):
            seeding_time_limit = None

        return DownloadRequest(
            url=retry_download_url,
            protocol=protocol,
            release_name=(
                normalize_optional_text(getattr(task, "retry_release_name", None))
                or task.title
                or "Unknown"
            ),
            expected_hash=normalize_optional_text(
                getattr(task, "retry_expected_hash", None)
            ),
            seeding_time_limit=seeding_time_limit,
            ratio_limit=float(ratio_limit) if ratio_limit is not None else None,
        )

    def _resolve_download(
        self,
        task: DownloadTask,
        status_callback: Callable[[str, str | None], None],
    ) -> DownloadRequest | None:
        """Resolve Prowlarr cache entry into download request parameters."""
        # Look up the cached release
        prowlarr_result = get_release(task.task_id)
        if not prowlarr_result:
            restored_request = self._restore_download_request_from_task(task)
            if restored_request is None:
                logger.warning("Release cache miss: %s", task.task_id)
                status_callback(
                    "error", "Release not found in cache (may have expired)"
                )
                return None
            logger.info(
                "Restored Prowlarr download request for retry: %s", task.task_id
            )
            return restored_request

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

        # Seed criteria from the indexer (Torznab attributes)
        raw_seed_time = prowlarr_result.get("minimumSeedTime")
        raw_ratio = prowlarr_result.get("minimumRatio")

        seeding_time_limit = _coerce_seed_time_minutes(raw_seed_time)
        ratio_limit = float(raw_ratio) if raw_ratio is not None else None

        return DownloadRequest(
            url=download_url,
            protocol=protocol,
            release_name=release_name,
            expected_hash=expected_hash,
            seeding_time_limit=seeding_time_limit,
            ratio_limit=ratio_limit,
        )

    def _on_download_complete(self, task: DownloadTask) -> None:
        """Remove completed release from the Prowlarr cache."""
        remove_release(task.task_id)

    def cancel(self, task_id: str) -> bool:
        """Cancel download and clean up cache. Primary cancellation is via cancel_flag."""
        logger.debug("Cancel requested for Prowlarr task: %s", task_id)
        remove_release(task_id)
        return super().cancel(task_id)
