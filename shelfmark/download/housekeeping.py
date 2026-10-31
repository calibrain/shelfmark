"""Periodic housekeeping for downloads, independent of the Hardcover sync.

One in-process daemon thread (the same pattern as the Hardcover scheduler) runs
two sweeps every ``INTERVAL_SECONDS``: the dead-torrent sweep and the Soulseek
leftovers sweep. Each is a no-op when its feature is off or its client is not
configured, so the thread costs nothing for installs that use neither.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from shelfmark.core.download_history_service import DownloadHistoryService
    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

STARTUP_DELAY_SECONDS = 30  # Lets the download coordinator and startup reconcile run first.
INTERVAL_SECONDS = 15 * 60

_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_ctx: dict[str, Any] = {}


def run_once(
    user_db: UserDB | None = None, history_service: DownloadHistoryService | None = None
) -> dict[str, Any]:
    """Run both sweeps. One failing never skips the other.

    Returns what was done, keyed by sweep; a sweep that did nothing is absent.
    A sweep that raised is reported under ``errors``.
    """
    summary: dict[str, Any] = {}
    errors: list[str] = []

    try:
        from shelfmark.download import stale_torrents

        removed = stale_torrents.run_for_shelfmark().removed
        if removed:
            summary["torrents_removed"] = list(removed)
    except Exception:
        logger.exception("Housekeeping: dead-torrent sweep failed")
        errors.append("stale_torrents")

    try:
        if user_db is not None and history_service is not None:
            from shelfmark.release_sources.slskd import leftovers

            report = leftovers.run_with_backend(history_service=history_service, user_db=user_db)
            slskd = {
                name: list(items)
                for name, items in (
                    ("adopted", report.adopted),
                    ("removed", report.removed),
                    ("reattached", report.reattached),
                    ("cancelled", report.cancelled),
                )
                if items
            }
            if slskd:
                summary["slskd"] = slskd
    except Exception:
        logger.exception("Housekeeping: slskd leftovers sweep failed")
        errors.append("slskd_leftovers")

    if errors:
        summary["errors"] = errors
    return summary


def _loop() -> None:
    logger.info("Housekeeping thread started")
    time.sleep(STARTUP_DELAY_SECONDS)
    while True:
        try:
            summary = run_once(_ctx.get("user_db"), _ctx.get("history_service"))
            if summary:
                logger.info("Housekeeping: %s", summary)
        except Exception:
            logger.exception("Housekeeping: cycle failed")
        time.sleep(INTERVAL_SECONDS)


def start(user_db: UserDB, history_service: DownloadHistoryService) -> None:
    """Start the housekeeping thread (idempotent)."""
    global _thread
    with _thread_lock:
        _ctx["user_db"] = user_db
        _ctx["history_service"] = history_service
        if _thread is not None and _thread.is_alive():
            logger.debug("Housekeeping already running")
            return
        _thread = threading.Thread(target=_loop, daemon=True, name="DownloadHousekeeping")
        _thread.start()
