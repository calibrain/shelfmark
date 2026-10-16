"""Remove torrents in Shelfmark's categories that are going nowhere and that nothing is watching.

A torrent outlives the task that added it: a restart, a stall timeout that left it in the client
because it had some data. It then holds a download slot and a partial copy for ever. A torrent that
is still downloading is none of this sweep's business; one that is stalled or stuck fetching its
metadata, with no activity for hours, and that no running download owns, is dead.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = setup_logger(__name__)

STALE_HOURS_SETTING = "STALE_TORRENT_HOURS"

# qBittorrent states of a torrent that is not moving: no peers gave it data, or it never got the
# metadata. A queued torrent is waiting for a slot, not dead, and is left alone.
_STUCK_STATES = frozenset({"stalledDL", "metaDL"})


@dataclass
class StaleReport:
    removed: list[str] = field(default_factory=list)


def _normalise(text: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def stale_hours() -> float:
    try:
        return max(float(config.get(STALE_HOURS_SETTING, 0) or 0), 0.0)
    except TypeError, ValueError:
        return 0.0


def find_stale(
    torrents: list[Any],
    *,
    owned_titles: set[str],
    idle_hours: float,
    now: float | None = None,
) -> list[Any]:
    """The torrents that are stuck, idle for ``idle_hours``, and owned by no running download."""
    if idle_hours <= 0:
        return []
    moment = time.time() if now is None else now
    owned = {_normalise(t) for t in owned_titles}
    stale: list[Any] = []
    for torrent in torrents:
        if float(getattr(torrent, "progress", 0.0) or 0.0) >= 1.0:
            continue
        if getattr(torrent, "state", "") not in _STUCK_STATES:
            continue
        last = float(getattr(torrent, "last_activity", 0) or 0)
        if last <= 0:
            last = float(getattr(torrent, "added_on", 0) or 0)
        if last <= 0 or moment - last < idle_hours * 3600:
            continue
        if _normalise(getattr(torrent, "name", "")) in owned:
            continue
        stale.append(torrent)
    return stale


def run(
    client: Any,
    *,
    categories: list[str],
    owned_titles: Callable[[], set[str]],
    now: float | None = None,
) -> StaleReport:
    """Remove the stale torrents (and their partial data) from each category."""
    report = StaleReport()
    idle = stale_hours()
    if idle <= 0:
        return report
    owned = owned_titles()
    for category in dict.fromkeys(c for c in categories if c):
        for torrent in find_stale(
            client.list_torrents(category), owned_titles=owned, idle_hours=idle, now=now
        ):
            if client.remove(torrent.hash, delete_files=True):
                report.removed.append(str(getattr(torrent, "name", torrent.hash)))
                logger.info(
                    "Removed a dead torrent nothing is watching: %s (%.0f%%, %s)",
                    getattr(torrent, "name", torrent.hash),
                    float(torrent.progress) * 100,
                    torrent.state,
                )
    return report


def run_for_shelfmark(*, now: float | None = None) -> StaleReport:
    """``run`` against the configured qBittorrent, in Shelfmark's own categories."""
    from shelfmark.download import orchestrator as backend
    from shelfmark.download.clients import get_client

    report = StaleReport()
    if stale_hours() <= 0:
        return report
    client = get_client("torrent")
    if client is None or not hasattr(client, "list_torrents"):
        return report

    def owned_titles() -> set[str]:
        titles: set[str] = set()
        for bucket, tasks in backend.queue_status().items():
            if bucket not in {"queued", "resolving", "locating", "downloading"}:
                continue
            titles.update(str(t.get("title") or "") for t in tasks.values() if isinstance(t, dict))
        return titles

    categories = [
        str(config.get("QBITTORRENT_CATEGORY", "") or ""),
        str(config.get("QBITTORRENT_CATEGORY_AUDIOBOOK", "") or ""),
    ]
    return run(client, categories=categories, owned_titles=owned_titles, now=now)
