"""Remove torrents this instance added that are going nowhere and that nothing is watching.

A torrent outlives the task that added it: a restart, a stall timeout that left it in the client
because it had some data. It then holds a download slot and a partial copy for ever. A torrent that
is still downloading is none of this sweep's business; one that is stalled or stuck fetching its
metadata, with no activity for hours, and that no running task owns, is dead.

Ownership never rests on a category: only a torrent whose hash is in the ledger of torrents this
instance added (``torrent_ledger``) can be removed, and only when the task that added it is not
running. A torrent from before the ledger, or one somebody else put in the same category, is never
touched.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download import torrent_ledger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = setup_logger(__name__)

STALE_HOURS_SETTING = "STALE_TORRENT_HOURS"
STALE_HOURS_DEFAULT = 6.0
STALE_HOURS_MIN = 3.0
STALE_HOURS_MAX = 72.0

# qBittorrent states of a torrent that is not moving: no peers gave it data, or it never got the
# metadata. A queued torrent is waiting for a slot, not dead, and is left alone.
_STUCK_STATES = frozenset({"stalledDL", "metaDL"})

# Task states that mean a task is still working with its torrent.
_RUNNING_BUCKETS = frozenset({"queued", "resolving", "locating", "downloading"})


@dataclass
class StaleReport:
    removed: list[str] = field(default_factory=list)


def stale_hours() -> float:
    """The idle limit in hours: 0 turns the sweep off, anything else is held to 3 through 72."""
    raw = config.get(STALE_HOURS_SETTING, STALE_HOURS_DEFAULT)
    try:
        hours = float(raw) if isinstance(raw, (int, float, str)) else STALE_HOURS_DEFAULT
    except ValueError:
        return STALE_HOURS_DEFAULT
    if hours <= 0:
        return 0.0
    return min(max(hours, STALE_HOURS_MIN), STALE_HOURS_MAX)


def _ledger_hash(torrent: Any, ledger: dict[str, str]) -> str | None:
    """The ledger key this torrent answers to, matched on every identity the client exposes."""
    from shelfmark.download.clients.qbittorrent import _torrent_matches_download_id

    for known in ledger:
        if _torrent_matches_download_id(torrent, known):
            return known
    return None


def find_stale(
    torrents: list[Any],
    *,
    ledger: dict[str, str],
    running_task_ids: set[str],
    idle_hours: float,
    now: float | None = None,
) -> list[tuple[str, Any]]:
    """``(ledger hash, torrent)`` for each torrent that is ours, stuck, idle and owned by nobody."""
    if idle_hours <= 0:
        return []
    moment = time.time() if now is None else now
    stale: list[tuple[str, Any]] = []
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
        known = _ledger_hash(torrent, ledger)
        if known is None or ledger[known] in running_task_ids:
            continue
        stale.append((known, torrent))
    return stale


def run(
    client: Any,
    *,
    running_task_ids: Callable[[], set[str]],
    now: float | None = None,
) -> StaleReport:
    """Remove the stale torrents (and their partial data) that this instance added."""
    report = StaleReport()
    idle = stale_hours()
    if idle <= 0:
        return report
    ledger = torrent_ledger.entries()
    if not ledger:
        return report
    torrents = client.list_torrents()
    # A listing that came back empty may be a failed read, so it never prunes the ledger.
    if torrents:
        listed = {_ledger_hash(t, ledger) for t in torrents}
        torrent_ledger.forget(*(known for known in ledger if known not in listed))
    for known, torrent in find_stale(
        torrents,
        ledger=ledger,
        running_task_ids=running_task_ids(),
        idle_hours=idle,
        now=now,
    ):
        if client.remove(torrent.hash, delete_files=True):
            torrent_ledger.forget(known)
            report.removed.append(str(getattr(torrent, "name", torrent.hash)))
            logger.info(
                "Removed a dead torrent nothing is watching: %s (%.0f%%, %s)",
                getattr(torrent, "name", torrent.hash),
                float(torrent.progress) * 100,
                torrent.state,
            )
    return report


def run_for_shelfmark(*, now: float | None = None) -> StaleReport:
    """``run`` against the configured qBittorrent."""
    from shelfmark.download import orchestrator as backend
    from shelfmark.download.clients import get_client

    report = StaleReport()
    if stale_hours() <= 0:
        return report
    client = get_client("torrent")
    if client is None or not hasattr(client, "list_torrents"):
        return report

    def running_task_ids() -> set[str]:
        ids: set[str] = set()
        for bucket, tasks in backend.queue_status().items():
            if bucket in _RUNNING_BUCKETS:
                ids.update(str(task_id) for task_id in tasks)
        return ids

    return run(client, running_task_ids=running_task_ids, now=now)
