"""Prowlarr download handler - resolves releases and delegates lifecycle to shared clients."""

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.search_plan import build_release_search_plan
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
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import register_handler
from shelfmark.release_sources.prowlarr.cache import cache_release, get_release, remove_release
from shelfmark.release_sources.prowlarr.source import ProwlarrSource
from shelfmark.release_sources.prowlarr.utils import (
    coerce_int_like,
    get_preferred_download_url,
    get_protocol,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)
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
EXPIRED_LINK_REFRESH_ERROR = (
    "The indexer download link expired and the release could not be refreshed. "
    "Search again for a fresh result."
)
HASH_DETECTION_ERROR = "Could not determine torrent hash from URL"


def _coerce_positive_minutes(raw_minutes: object) -> int | None:
    minutes = coerce_int_like(raw_minutes)
    if minutes is None:
        return None
    return minutes if minutes > 0 else None


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

    def build_retry_resolution_fields(self, release_data: dict[str, Any]) -> dict[str, Any]:
        source_id = normalize_optional_text(release_data.get("source_id"))
        extra = release_data.get("extra")
        if not isinstance(extra, dict):
            extra = {}

        retry_source_context: dict[str, Any] = {}
        indexer_id = release_data.get("indexer_id") or extra.get("indexer_id")
        if indexer_id is not None:
            retry_source_context["indexer_id"] = indexer_id

        indexer = normalize_optional_text(release_data.get("indexer") or extra.get("indexer"))
        if indexer is not None and indexer.lower() != "unknown":
            retry_source_context["indexer"] = indexer

        info_url = normalize_optional_text(release_data.get("info_url") or extra.get("info_url"))
        if info_url is not None:
            retry_source_context["info_url"] = info_url

        if source_id is not None:
            retry_source_context["source_id"] = source_id

        return {
            "retry_download_url": None,
            "retry_download_protocol": None,
            "retry_source_context": retry_source_context,
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
            logger.info("Prowlarr release cache miss, refreshing: %s", task.task_id)
            prowlarr_result = self._refresh_release(task)
            if prowlarr_result is None:
                logger.warning("Prowlarr release refresh failed: %s", task.task_id)
                status_callback("error", EXPIRED_LINK_REFRESH_ERROR)
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

        seeding_time_limit = None
        ratio_limit = None
        if config.get("PROWLARR_USE_SEED_PREFERENCES", False):
            raw_configured_seed_time = prowlarr_result.get("configuredSeedTimeMinutes")
            raw_configured_ratio = prowlarr_result.get("configuredRatioLimit")

            seeding_time_limit = _coerce_positive_minutes(raw_configured_seed_time)
            ratio_limit = float(raw_configured_ratio) if raw_configured_ratio is not None else None

        return DownloadRequest(
            url=download_url,
            protocol=protocol,
            release_name=release_name,
            expected_hash=expected_hash,
            seeding_time_limit=seeding_time_limit,
            ratio_limit=ratio_limit,
        )

    def _refresh_release(self, task: DownloadTask) -> dict[str, Any] | None:
        """Re-query Prowlarr and cache the exact original release if it still exists."""
        title = normalize_optional_text(task.title)
        if title is None:
            return None

        context = getattr(task, "retry_source_context", None)
        if not isinstance(context, dict):
            context = {}

        indexer = normalize_optional_text(context.get("indexer"))
        book = BookMetadata(
            provider="shelfmark",
            provider_id=task.task_id,
            title=title,
            authors=[task.author] if task.author else [],
            search_title=title,
            search_author=task.author,
        )
        plan = build_release_search_plan(
            book,
            indexers=[indexer] if indexer is not None else None,
        )

        source = ProwlarrSource()
        results = source.search(book, plan, content_type=task.content_type or "ebook")
        for release in results:
            raw_release = get_release(release.source_id)
            if raw_release is None:
                continue
            if not self._raw_release_matches_task(raw_release, task.task_id):
                continue

            cache_release(task.task_id, raw_release)
            logger.info("Refreshed Prowlarr release: %s", task.task_id)
            return raw_release

        return None

    @staticmethod
    def _raw_release_matches_task(raw_release: dict[str, Any], task_id: str) -> bool:
        identities = (
            normalize_optional_text(raw_release.get("guid")),
            normalize_optional_text(raw_release.get("infoUrl")),
        )
        return any(identity == task_id for identity in identities)

    def _refresh_download_request_after_add_failure(
        self,
        *,
        task: DownloadTask,
        request: DownloadRequest,
        error: Exception,
        status_callback: Callable[[str, str | None], None],
    ) -> DownloadRequest | None:
        """Refresh once when a cached Prowlarr torrent proxy URL has expired."""
        if request.protocol != "torrent":
            return None
        if HASH_DETECTION_ERROR not in str(error):
            return None

        parsed = urlparse(request.url)
        if parsed.scheme.lower() not in {"http", "https"}:
            return None

        logger.info("Refreshing stale Prowlarr torrent URL for %s", task.task_id)
        remove_release(task.task_id)
        refreshed_request = self._resolve_download(task, status_callback)
        if refreshed_request is None:
            raise RuntimeError(EXPIRED_LINK_REFRESH_ERROR) from error
        return refreshed_request

    def _on_download_complete(self, task: DownloadTask) -> None:
        """Remove completed release from the Prowlarr cache."""
        remove_release(task.task_id)

    def cancel(self, task_id: str) -> bool:
        """Cancel download and clean up cache. Primary cancellation is via cancel_flag."""
        logger.debug("Cancel requested for Prowlarr task: %s", task_id)
        remove_release(task_id)
        return super().cancel(task_id)
