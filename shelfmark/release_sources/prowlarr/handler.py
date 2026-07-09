"""Prowlarr download handler - resolves releases and delegates lifecycle to shared clients."""

from typing import TYPE_CHECKING, Any

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.utils import normalize_http_url
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
from shelfmark.release_sources.prowlarr.api import IndexerSeedSettings, ProwlarrClient
from shelfmark.release_sources.prowlarr.cache import get_release, remove_release
from shelfmark.release_sources.prowlarr.utils import (
    coerce_int_like,
    get_preferred_download_url,
    get_protocol,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

# Errors that ProwlarrClient can raise when fetching indexer settings.
_SEED_SETTINGS_FALLBACK_ERRORS = (
    requests.exceptions.RequestException,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

__all__ = [
    "ProwlarrHandler",
    "POLL_INTERVAL",
    "COMPLETED_PATH_RETRY_INTERVAL",
    "COMPLETED_PATH_MAX_ATTEMPTS",
    "config",
]

# Backwards-compat constants for tests patching this module.
POLL_INTERVAL = _DEFAULT_POLL_INTERVAL
COMPLETED_PATH_RETRY_INTERVAL = _DEFAULT_COMPLETED_PATH_RETRY_INTERVAL
COMPLETED_PATH_MAX_ATTEMPTS = _DEFAULT_COMPLETED_PATH_MAX_ATTEMPTS


def _coerce_positive_minutes(raw_minutes: object) -> int | None:
    minutes = coerce_int_like(raw_minutes)
    if minutes is None:
        return None
    return minutes if minutes > 0 else None


@register_handler("prowlarr")
class ProwlarrHandler(ExternalClientHandler):
    """Handler for Prowlarr downloads via configured torrent or usenet client."""

    @staticmethod
    def _build_prowlarr_client() -> ProwlarrClient | None:
        """Build a ProwlarrClient from config, or None if not configured."""
        raw_url = config.get("PROWLARR_URL", "")
        raw_api_key = config.get("PROWLARR_API_KEY", "")
        url = normalize_optional_text(raw_url) if isinstance(raw_url, str) else None
        api_key = normalize_optional_text(raw_api_key) if isinstance(raw_api_key, str) else None
        if not url or not api_key:
            return None
        normalized_url = normalize_http_url(url)
        if not normalized_url:
            return None
        return ProwlarrClient(normalized_url, api_key)

    def _fetch_seed_settings_fallback(self, raw_indexer_id: object) -> IndexerSeedSettings | None:
        """Fetch share limits for one indexer directly from Prowlarr.

        Used when the cached release is missing its search-time seed-limit
        enrichment so that transient failures during search cannot cause a
        torrent to be added without its configured share limits.
        """
        indexer_id = coerce_int_like(raw_indexer_id)
        if indexer_id is None:
            return None

        client = self._build_prowlarr_client()
        if client is None:
            return None

        try:
            settings = client.get_indexer_seed_settings(restrict_to=[indexer_id])
        except _SEED_SETTINGS_FALLBACK_ERRORS:
            logger.warning(
                "Grab-time seed settings fallback failed for indexerId=%s",
                indexer_id,
                exc_info=True,
            )
            return None

        return settings.get(indexer_id)

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

    def build_retry_resolution_fields(self, release_data: dict[str, Any]) -> dict[str, Any]:
        source_id = normalize_optional_text(release_data.get("source_id"))
        if source_id is None:
            return {}

        prowlarr_result = get_release(source_id)
        if prowlarr_result is None:
            return {}

        return {
            "retry_download_url": normalize_optional_text(
                get_preferred_download_url(prowlarr_result)
            ),
            "retry_download_protocol": normalize_optional_text(get_protocol(prowlarr_result)),
        }

    @classmethod
    def _restore_download_request_from_task(cls, task: DownloadTask) -> DownloadRequest | None:
        """Rebuild a DownloadRequest when the in-memory Prowlarr cache is gone."""
        retry_download_url = normalize_optional_text(getattr(task, "retry_download_url", None))
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
        if not isinstance(seeding_time_limit, int) or isinstance(seeding_time_limit, bool):
            seeding_time_limit = None

        return DownloadRequest(
            url=retry_download_url,
            protocol=protocol,
            release_name=(
                normalize_optional_text(getattr(task, "retry_release_name", None))
                or task.title
                or "Unknown"
            ),
            expected_hash=normalize_optional_text(getattr(task, "retry_expected_hash", None)),
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
                status_callback("error", "Release not found in cache (may have expired)")
                return None
            logger.info("Restored Prowlarr download request for retry: %s", task.task_id)
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

        seeding_time_limit = None
        ratio_limit = None
        if config.get("PROWLARR_USE_SEED_PREFERENCES", False):
            raw_configured_seed_time = prowlarr_result.get("configuredSeedTimeMinutes")
            raw_configured_ratio = prowlarr_result.get("configuredRatioLimit")

            seeding_time_limit = _coerce_positive_minutes(raw_configured_seed_time)
            ratio_limit = float(raw_configured_ratio) if raw_configured_ratio is not None else None

            # Fallback: search-time enrichment can be missing when the indexer
            # settings fetch transiently failed during the search (#795).
            # Re-resolve the limits from Prowlarr at grab time so torrents are
            # never sent to the client without their configured share limits.
            if seeding_time_limit is None and ratio_limit is None and protocol == "torrent":
                fallback = self._fetch_seed_settings_fallback(prowlarr_result.get("indexerId"))
                if fallback:
                    seeding_time_limit = _coerce_positive_minutes(
                        fallback.get("seeding_time_limit_minutes")
                    )
                    raw_ratio = fallback.get("ratio_limit")
                    ratio_limit = float(raw_ratio) if raw_ratio is not None else None

            if seeding_time_limit is None and ratio_limit is None and protocol == "torrent":
                logger.warning(
                    "Prowlarr seed preferences are enabled but no share limits "
                    "could be resolved for release '%s' (indexerId=%s); the "
                    "torrent will use the client's global limits",
                    release_name,
                    prowlarr_result.get("indexerId"),
                )

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
