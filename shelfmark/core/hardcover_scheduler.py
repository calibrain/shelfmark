"""Background scheduler for Hardcover wishlist sync + auto-download.

Runs an in-process daemon thread (mirroring the download coordinator pattern in
``shelfmark.download.orchestrator``) that periodically syncs configured Hardcover
shelves into requests and auto-downloads them. Also exposes a manual trigger used
by the "Sync now" settings action button.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.search_deadline import SearchDeadline

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_WARMUP_SECONDS = 60  # Delay before the first cycle after startup.
_MIN_INTERVAL_SECONDS = 60  # Floor so a misconfigured interval can't hammer the API.
_UNIT_SECONDS = {"minutes": 60.0, "hours": 3600.0}
_FALLBACK_WAIT_SECONDS = 6 * 3600.0  # Wait used when the interval cannot be read.

_ctx_lock = threading.Lock()
_ctx: dict[str, Any] = {}

_thread: threading.Thread | None = None
_thread_lock = threading.Lock()

# Prevents a scheduled cycle and a manual "Sync now" from overlapping.
_run_lock = threading.Lock()

_OVERDUE_INTERVALS = 2  # A cycle this many intervals late is reported as a problem.

# A cycle still running after this long is stuck, not slow. Every call it makes has its
# own timeout (Hardcover and Audiobookshelf requests, each request's release search), so a
# healthy cycle finishes well inside it; what this catches is the call no timeout reaches,
# such as a hung DNS lookup or a dead network mount under the Calibre database. When it
# trips, run_once stops waiting and releases _run_lock, so the next scheduled cycle and
# "Sync now" work again, and the cycle's own code stops at its next unit of work (see
# cycle_expired). A thread cannot be killed, so that is the whole of the recovery.
_CYCLE_TIMEOUT_SECONDS = 3600.0
# A cycle may use most of the gap between cycles: a long auto-download backlog is slow, not
# stuck, and tripping the guard on it would report an error every cycle.
_CYCLE_TIMEOUT_SHARE_OF_INTERVAL = 0.8


def _cycle_timeout() -> float:
    """Seconds one cycle may run: most of the interval, never less than an hour."""
    return max(_CYCLE_TIMEOUT_SECONDS, _CYCLE_TIMEOUT_SHARE_OF_INTERVAL * _interval_seconds())


# The budget of the cycle running in this thread. Only the cycle's worker thread sets it;
# a route or a test calling the sync directly sees None and is never cut short.
_cycle: ContextVar[SearchDeadline | None] = ContextVar("hardcover_cycle", default=None)


@contextmanager
def _in_cycle(deadline: SearchDeadline) -> Iterator[None]:
    token = _cycle.set(deadline)
    try:
        yield
    finally:
        _cycle.reset(token)


def cycle_expired() -> bool:
    """Whether the scheduler has given up on the cycle this thread is part of.

    The sync and the auto-download pass ask between units of work (an account, a shelf
    page, a request, a source) so an abandoned cycle winds down at its next boundary
    instead of running on beside the cycle that replaced it. False outside a cycle.
    """
    deadline = _cycle.get()
    return deadline is not None and deadline.expired


def cycle_remaining() -> float | None:
    """Seconds left in the cycle this thread is part of, or None outside a cycle."""
    deadline = _cycle.get()
    return deadline.remaining if deadline is not None else None


def _now() -> float:
    return time.time()


def _new_state() -> dict[str, Any]:
    return {
        "started_at": _now(),
        "last_cycle_at": None,
        "last_cycle_errors": 0,
        "consecutive_failed_cycles": 0,
    }


# What the last cycle did, for /api/stats and /api/health. In memory: a restart starts clean.
_state_lock = threading.Lock()
_state: dict[str, Any] = _new_state()


def _cycle_errors(result: dict[str, Any]) -> int:
    """Errors a finished cycle reported, across the sync and the auto-download pass."""
    total = 0
    for key, field in (("sync", "errors"), ("auto_download", "error")):
        part = result.get(key)
        if isinstance(part, dict):
            total += int(part.get(field, 0) or 0)
    return total


def _record_cycle(errors: int) -> None:
    with _state_lock:
        _state["last_cycle_at"] = _now()
        _state["last_cycle_errors"] = errors
        _state["consecutive_failed_cycles"] = (
            _state["consecutive_failed_cycles"] + 1 if errors else 0
        )


def status() -> dict[str, Any]:
    """The background cycle's health: when it last ran, what it hit, and what is wrong."""
    enabled = _enabled()
    interval = _interval_seconds()
    now = _now()
    with _state_lock:
        state = dict(_state)
    thread_alive = _thread is not None and _thread.is_alive()
    last = state["last_cycle_at"]

    problems: list[str] = []
    if enabled:
        if not thread_alive:
            problems.append("scheduler thread is not running")
        if state["last_cycle_errors"]:
            problems.append(f"last cycle had {state['last_cycle_errors']} error(s)")
        reference = last if last is not None else state["started_at"] + _WARMUP_SECONDS
        if now - reference > _OVERDUE_INTERVALS * interval:
            problems.append(f"no cycle in {int((now - reference) // 3600)}h")

    return {
        "enabled": enabled,
        "thread_alive": thread_alive,
        "interval_seconds": int(interval),
        "last_cycle_at": datetime.fromtimestamp(last, UTC).isoformat() if last else None,
        "seconds_since_last_cycle": int(now - last) if last is not None else None,
        "last_cycle_errors": state["last_cycle_errors"],
        "consecutive_failed_cycles": state["consecutive_failed_cycles"],
        "healthy": not problems,
        "problems": problems,
    }


def configure(
    user_db: UserDB,
    queue_release: Callable[..., tuple[bool, str | None]],
    db_path: str | None,
) -> None:
    """Store the runtime context the background work needs."""
    with _ctx_lock:
        _ctx["user_db"] = user_db
        _ctx["queue_release"] = queue_release
        _ctx["db_path"] = db_path


def _interval_seconds() -> float:
    raw = app_config.get("HARDCOVER_SYNC_INTERVAL", 6)
    try:
        value = float(raw) if isinstance(raw, (int, float, str)) else 6.0
    except ValueError:
        value = 6.0
    unit = str(app_config.get("HARDCOVER_SYNC_INTERVAL_UNIT", "hours") or "hours").strip().lower()
    multiplier = _UNIT_SECONDS.get(unit, _UNIT_SECONDS["hours"])
    return max(value * multiplier, _MIN_INTERVAL_SECONDS)


def _enabled() -> bool:
    return bool(app_config.get("HARDCOVER_SYNC_ENABLED", False)) or bool(
        app_config.get("AUTO_DOWNLOAD_ENABLED", False)
    )


def _sync_all_accounts(user_db: UserDB, *, db_path: str | None) -> dict[str, Any]:
    """Sync every connected Hardcover account, then the app-level token.

    Each user is synced on their own so a bad token, a rate limit or an API outage on
    one account cannot stop the rest of the sweep. Totals are summed across the runs
    and ``accounts`` reports how many were attempted.
    """
    from shelfmark.core.hardcover_sync import sync_wishlist, users_with_hardcover_token

    totals: dict[str, Any] = {"added": 0, "skipped": 0, "in_library": 0, "errors": 0}
    per_user_ids = users_with_hardcover_token(user_db)
    attempted = 0

    def merge(summary: dict[str, int]) -> None:
        for key in ("added", "skipped", "in_library", "errors"):
            totals[key] += int(summary.get(key, 0))

    for user_id in per_user_ids:
        if cycle_expired():
            break
        attempted += 1
        try:
            merge(sync_wishlist(user_db, db_path=db_path, user_id=user_id))
        except Exception:
            logger.exception("hardcover-sync: sync failed for user %s", user_id)
            totals["errors"] += 1

    # The app-level token still runs, for instances where nobody connected their own.
    if cycle_expired():
        logger.warning(
            "hardcover-sync: cycle out of time; %d of %d account(s) not synced",
            len(per_user_ids) + 1 - attempted,
            len(per_user_ids) + 1,
        )
        totals["errors"] += 1
    else:
        attempted += 1
        try:
            merge(sync_wishlist(user_db, db_path=db_path))
        except Exception:
            logger.exception("hardcover-sync: app-level sync failed")
            totals["errors"] += 1

    totals["accounts"] = attempted
    return totals


def _cycle_body(
    user_db: UserDB,
    queue_release: Callable[..., tuple[bool, str | None]],
    db_path: str | None,
    *,
    force: bool,
) -> dict[str, Any]:
    from shelfmark.core.auto_download import auto_download_pending

    sync_summary: dict[str, Any] | None = None
    if force or bool(app_config.get("HARDCOVER_SYNC_ENABLED", False)):
        sync_summary = _sync_all_accounts(user_db, db_path=db_path)
    auto_summary = auto_download_pending(
        user_db, queue_release=queue_release, db_path=db_path, ignore_backoff=force
    )
    return {"status": "ok", "sync": sync_summary, "auto_download": auto_summary}


def _run_cycle(deadline: SearchDeadline, box: dict[str, Any], **kwargs: Any) -> None:
    """Worker-thread entry: run one cycle under its budget and leave the outcome in ``box``."""
    with _in_cycle(deadline):
        try:
            box["result"] = _cycle_body(**kwargs)
        except BaseException as exc:  # noqa: BLE001 - handed back to run_once, which re-raises
            box["error"] = exc


def run_once(*, force: bool = False) -> dict[str, Any]:
    """Run one sync + auto-download pass. Returns a combined summary.

    ``force=True`` (the manual "Sync now" path) syncs regardless of the scheduler
    toggle; auto-download always remains gated by ``AUTO_DOWNLOAD_ENABLED`` inside
    ``auto_download_pending``. Skips (returns ``{"status": "busy"}``) if another run
    is in progress.

    The cycle runs in a worker thread and is waited on for at most
    ``_cycle_timeout()`` (most of the interval, at least an hour). Past that it is reported as ``{"status": "timeout"}``,
    recorded as a failed cycle, and the lock is released; the worker is told to stop at
    its next unit of work and whatever it reports later is ignored.
    """
    if not _run_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        with _ctx_lock:
            user_db = _ctx.get("user_db")
            queue_release = _ctx.get("queue_release")
            db_path = _ctx.get("db_path")

        if user_db is None or queue_release is None:
            return {"status": "unconfigured"}

        timeout = _cycle_timeout()
        deadline = SearchDeadline(timeout)
        box: dict[str, Any] = {}
        worker = threading.Thread(
            target=_run_cycle,
            args=(deadline, box),
            kwargs={
                "user_db": user_db,
                "queue_release": queue_release,
                "db_path": db_path,
                "force": force,
            },
            daemon=True,
            name="HardcoverSyncCycle",
        )
        try:
            deadline.start()
            worker.start()
            worker.join(timeout)
            if worker.is_alive():
                deadline.event.set()
                logger.error(
                    "Hardcover scheduler: cycle still running after %ds; giving up on it "
                    "so the next cycle can run",
                    int(timeout),
                )
                _record_cycle(1)
                return {"status": "timeout", "timeout_seconds": int(timeout), "errors": 1}
        finally:
            deadline.cancel()

        if "error" in box:
            _record_cycle(1)
            raise box["error"]
        result = box["result"]
        _record_cycle(_cycle_errors(result))
        return result
    finally:
        _run_lock.release()


def trigger_async(*, force: bool = True) -> bool:
    """Kick off a one-off run in a background thread. Returns False if one is active."""
    if _run_lock.locked():
        return False
    thread = threading.Thread(
        target=lambda: run_once(force=force), daemon=True, name="HardcoverSyncManual"
    )
    thread.start()
    return True


def _quick_retry_interval_seconds() -> float:
    """How often failed downloads are checked between cycles, 0 when switched off."""
    raw = app_config.get("AUTO_DOWNLOAD_FAILURE_RETRY_MINUTES", 0)
    try:
        minutes = float(raw) if isinstance(raw, (int, float, str)) else 0.0
    except ValueError:
        return 0.0
    return max(minutes * 60.0, _MIN_INTERVAL_SECONDS) if minutes > 0 else 0.0


def run_quick_retry() -> dict[str, int] | None:
    """Pick another release for downloads that failed since the last look.

    Skipped (None) while a cycle or a manual sync runs, and when no download failed.
    """
    if not _run_lock.acquire(blocking=False):
        return None
    try:
        with _ctx_lock:
            user_db = _ctx.get("user_db")
            queue_release = _ctx.get("queue_release")
            db_path = _ctx.get("db_path")
        if user_db is None or queue_release is None:
            return None
        from shelfmark.core.auto_download import retry_failed_downloads

        return retry_failed_downloads(user_db, queue_release=queue_release, db_path=db_path)
    finally:
        _run_lock.release()


def _wait_for_next_cycle() -> None:
    """Sleep until the next cycle, looking at failed downloads on the way when that is on."""
    deadline = time.monotonic() + _interval_seconds()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        step = _quick_retry_interval_seconds()
        if step <= 0:
            time.sleep(remaining)
            return
        time.sleep(min(step, remaining))
        if time.monotonic() >= deadline:
            return
        try:
            result = run_quick_retry()
            if result is not None:
                logger.info("Hardcover scheduler: quick retry result %s", result)
        except Exception:
            logger.exception("Hardcover scheduler: quick retry failed")


def _scheduler_loop() -> None:
    logger.info("Hardcover scheduler started")
    time.sleep(_WARMUP_SECONDS)
    while True:
        try:
            if _enabled():
                logger.info("Hardcover scheduler: starting cycle")
                result = run_once()
                logger.info("Hardcover scheduler: cycle result %s", result)
        except Exception:
            logger.exception("Hardcover scheduler: cycle failed")
        try:
            _wait_for_next_cycle()
        except Exception:
            # An unreadable interval setting must not end the thread: nothing would sync
            # again until a restart.
            logger.exception(
                "Hardcover scheduler: could not wait for the next cycle; retrying in %d s",
                int(_FALLBACK_WAIT_SECONDS),
            )
            time.sleep(_FALLBACK_WAIT_SECONDS)


def start(
    user_db: UserDB,
    queue_release: Callable[..., tuple[bool, str | None]],
    db_path: str | None,
) -> None:
    """Configure context and start the scheduler thread (idempotent)."""
    configure(user_db, queue_release, db_path)
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            logger.debug("Hardcover scheduler already running")
            return
        _thread = threading.Thread(target=_scheduler_loop, daemon=True, name="HardcoverScheduler")
        _thread.start()
    logger.info("Hardcover scheduler thread launched")
