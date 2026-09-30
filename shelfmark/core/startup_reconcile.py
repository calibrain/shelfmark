"""Startup reconciliation of downloads the previous process left unfinished.

The download queue lives in memory. When the process stops with a download in flight, its
history row stays "active" and its request stays "queued", with nothing working on either.
The activity API relabels such a row "Interrupted" when it is read, but nothing is stored
and nothing ever revisits it, so a request whose download was cut short is stranded.

This runs once at startup, before the download coordinator starts, when nothing can
legitimately be active. It assumes a single worker process (``entrypoint.sh`` starts
gunicorn with ``--workers 1``): a second worker booting later would see the first one's
live downloads as orphans.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.models import QueueStatus
from shelfmark.core.request_validation import RequestStatus

if TYPE_CHECKING:
    from shelfmark.core.download_history_service import DownloadHistoryService
    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

# The status message an interrupted row is given. The activity API uses the same word when
# it relabels a stale row that has none.
INTERRUPTED_MESSAGE = "Interrupted"

# Only what was queued this recently is put back for another attempt. Anything older was
# orphaned by an earlier restart nobody reconciled: it is closed out here, but reopening a
# backlog all at once (or filling an admin's approval queue with old requests) is not
# something a startup should do.
REOPEN_WINDOW = timedelta(hours=24)


def _parse_timestamp(value: object) -> datetime | None:
    """A stored timestamp (SQLite's or ISO with an offset) as an aware datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_recent(value: object, *, now: datetime, window: timedelta) -> bool:
    stamp = _parse_timestamp(value)
    return stamp is not None and stamp >= now - window


def _request_stamp(request: dict[str, Any]) -> object:
    for key in ("delivery_updated_at", "reviewed_at", "created_at"):
        if request.get(key):
            return request[key]
    return None


def reconcile_interrupted_downloads(
    user_db: UserDB,
    history_service: DownloadHistoryService,
    *,
    now: datetime | None = None,
    reopen_window: timedelta = REOPEN_WINDOW,
) -> dict[str, int]:
    """Close out downloads the last process left active, and reopen the recent ones.

    Every history row still "active" is finalised as an error with the message "Interrupted",
    which keeps its retry payload, so the manual retry it already offers keeps working. A
    fulfilled request whose download was queued within ``reopen_window`` goes back to pending
    through ``UserDB.reopen_failed_request``, upstream's own path for a failed request, so it
    is approved again or, where automatic downloads are on, tried again. A request whose own
    delivery state says it arrived is left alone, and so is its row.

    Returns how many rows were closed out and how many requests were reopened.
    """
    now = now or datetime.now(UTC)
    marked = 0
    reopened = 0
    seen_requests: set[int] = set()

    for row in history_service.list_active():
        request_id = row.get("request_id")
        request = user_db.get_request(int(request_id)) if request_id else None
        if request is not None and request.get("delivery_state") == QueueStatus.COMPLETE:
            continue  # the request says it arrived, so do not call it interrupted

        history_service.finalize_download(
            task_id=str(row["task_id"]),
            final_status=QueueStatus.ERROR.value,
            status_message=INTERRUPTED_MESSAGE,
        )
        marked += 1

        if request is None:
            continue
        seen_requests.add(int(request["id"]))
        if request.get("status") != RequestStatus.FULFILLED:
            continue
        if _is_recent(row.get("queued_at"), now=now, window=reopen_window) and (
            user_db.reopen_failed_request(
                int(request["id"]), failure_reason="Interrupted by a restart"
            )
            is not None
        ):
            reopened += 1

    # A request can be "queued" with no history row at all, for instance when the row was
    # never written. After a restart nothing is queued, so it is stranded the same way.
    for request in user_db.list_requests(status=RequestStatus.FULFILLED):
        if int(request["id"]) in seen_requests:
            continue
        if request.get("delivery_state") != QueueStatus.QUEUED:
            continue
        if _is_recent(_request_stamp(request), now=now, window=reopen_window) and (
            user_db.reopen_failed_request(
                int(request["id"]), failure_reason="Interrupted by a restart"
            )
            is not None
        ):
            reopened += 1

    return {"marked": marked, "reopened": reopened}
