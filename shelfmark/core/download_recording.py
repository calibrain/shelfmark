"""Download recording: write each queued download to the history and, when it reaches a
terminal status, finalize the row, notify, push the activity update and reopen a failed request.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.models import TERMINAL_QUEUE_STATUSES, QueueStatus
from shelfmark.core.notifications import (
    NotificationContext,
    NotificationEvent,
    notify_admin,
    notify_user,
)
from shelfmark.core.request_helpers import (
    emit_ws_event,
    normalize_optional_text,
    normalize_positive_int,
)
from shelfmark.core.request_policy import normalize_content_type, normalize_source
from shelfmark.core.requests_service import reopen_failed_request
from shelfmark.core.viewer_redaction import viewer_download_id
from shelfmark.download import orchestrator as backend
from shelfmark.release_sources import get_source_display_name

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.activity_view_state_service import ActivityViewStateService
    from shelfmark.core.download_history_service import DownloadHistoryService
    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_OPERATIONAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)


def _queue_status_to_final_activity_status(status: QueueStatus) -> str | None:
    """The status to record in download history, or None when the download has not finished."""
    return status.value if status in TERMINAL_QUEUE_STATUSES else None


def _queue_status_to_notification_event(status: QueueStatus) -> NotificationEvent | None:
    """The notification for a finished download, or None for a status that sends none."""
    if status == QueueStatus.COMPLETE:
        return NotificationEvent.DOWNLOAD_COMPLETE
    if status == QueueStatus.ERROR:
        return NotificationEvent.DOWNLOAD_FAILED
    return None


def _notify_admin_for_terminal_download_status(
    *, task_id: str, status: QueueStatus, task: Any
) -> None:
    """Notify the admins, and the user who owns the task, that a download completed or failed.

    A notifier that raises is logged and does not stop the other.
    """
    event = _queue_status_to_notification_event(status)
    if event is None:
        return

    raw_owner_user_id = getattr(task, "user_id", None)
    try:
        owner_user_id = int(raw_owner_user_id) if raw_owner_user_id is not None else None
    except TypeError, ValueError:
        owner_user_id = None

    content_type = normalize_optional_text(getattr(task, "content_type", None))
    context = NotificationContext(
        event=event,
        title=str(getattr(task, "title", "Unknown title") or "Unknown title"),
        author=str(getattr(task, "author", "Unknown author") or "Unknown author"),
        username=normalize_optional_text(getattr(task, "username", None)),
        content_type=normalize_content_type(content_type) if content_type is not None else None,
        format=normalize_optional_text(getattr(task, "format", None)),
        source=normalize_source(getattr(task, "source", None)),
        error_message=(
            normalize_optional_text(getattr(task, "status_message", None))
            if event == NotificationEvent.DOWNLOAD_FAILED
            else None
        ),
    )
    try:
        notify_admin(event, context)
    except (RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Failed to trigger admin notification for download %s (%s): %s",
            task_id,
            status.value,
            exc,
        )
    if owner_user_id is None:
        return
    try:
        notify_user(owner_user_id, event, context)
    except (RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Failed to trigger user notification for download %s (%s, user_id=%s): %s",
            task_id,
            status.value,
            owner_user_id,
            exc,
        )


def _delivered_identity(task: Any) -> str | None:
    """The identity recorded for the task's download_path when it finished, if any."""
    path = getattr(task, "download_path", None)
    identities = getattr(task, "download_identities", None)
    if not isinstance(path, str) or not isinstance(identities, dict):
        return None
    identity = identities.get(path)
    return identity if isinstance(identity, str) else None


def register_download_recording(
    *,
    get_user_db: Callable[[], UserDB | None],
    get_download_history_service: Callable[[], DownloadHistoryService | None],
    get_activity_view_state_service: Callable[[], ActivityViewStateService | None],
    emit_request_update_events: Callable[[list[dict[str, Any]]], None],
    ws_manager: object,
) -> None:
    """Hook the download queue so every queued and finished download is recorded."""

    def _emit_activity_update_for_task(*, payload: dict[str, Any], task: Any) -> None:
        """Send an activity update to the admins and to the user who owns the task."""
        owner_user_id = normalize_positive_int(getattr(task, "user_id", None))
        emit_ws_event(
            ws_manager,
            event_name="activity_update",
            room="admins",
            payload=payload,
        )
        if owner_user_id is None:
            return
        # The owner knows a request-linked download by its opaque id, as /api/status
        # shows it, since the task id can carry a private tracker's passkey (#1418).
        task_id = payload.get("task_id")
        if isinstance(task_id, str):
            payload = {
                **payload,
                "task_id": viewer_download_id(task_id, getattr(task, "request_id", None)),
            }
        emit_ws_event(
            ws_manager,
            event_name="activity_update",
            room=f"user_{owner_user_id}",
            payload=payload,
        )

    def _record_download_queued(task_id: str, task: Any) -> None:
        """Persist initial download record when a task enters the queue."""
        history_service = get_download_history_service()
        if history_service is None:
            return

        owner_user_id = normalize_positive_int(getattr(task, "user_id", None))
        request_id = normalize_positive_int(getattr(task, "request_id", None))
        origin = "requested" if request_id else "direct"

        source_name = normalize_source(getattr(task, "source", None))
        source_display = get_source_display_name(source_name)

        try:
            history_service.record_download(
                task_id=task_id,
                user_id=owner_user_id,
                username=normalize_optional_text(getattr(task, "username", None)),
                request_id=request_id,
                source=source_name,
                source_display_name=source_display,
                title=str(getattr(task, "title", "Unknown title") or "Unknown title"),
                author=normalize_optional_text(getattr(task, "author", None)),
                file_format=normalize_optional_text(getattr(task, "format", None)),
                size=normalize_optional_text(getattr(task, "size", None)),
                preview=normalize_optional_text(getattr(task, "preview", None)),
                content_type=normalize_optional_text(getattr(task, "content_type", None)),
                downloads=getattr(task, "downloads", None),
                origin=origin,
                retry_payload=backend.serialize_task_for_retry(task),
            )
        except _OPERATIONAL_ERRORS as exc:
            logger.warning("Failed to record download at queue time for task %s: %s", task_id, exc)
            return

        view_state_service = get_activity_view_state_service()
        if view_state_service is None:
            return

        try:
            cleared_view_state = 0
            cleared_view_state += view_state_service.clear_item_for_all_viewers(
                item_type="download",
                item_key=f"download:{task_id}",
            )
            if request_id is not None:
                cleared_view_state += view_state_service.clear_item_for_all_viewers(
                    item_type="request",
                    item_key=f"request:{request_id}",
                )
            if cleared_view_state > 0:
                _emit_activity_update_for_task(
                    task=task,
                    payload={
                        "kind": "activity_reset",
                        "task_id": task_id,
                    },
                )
        except _OPERATIONAL_ERRORS as exc:
            logger.warning("Failed to reset activity viewer state for task %s: %s", task_id, exc)

    def _record_download_terminal_snapshot(task_id: str, status: QueueStatus, task: Any) -> None:
        """Record a finished download: notify, finalize its history and tell the Activity view.

        A failed download that cannot be retried also reopens the request it was fulfilling.
        """
        _notify_admin_for_terminal_download_status(task_id=task_id, status=status, task=task)

        final_status = _queue_status_to_final_activity_status(status)
        if final_status is None:
            return

        history_service = get_download_history_service()
        view_state_service = get_activity_view_state_service()
        user_db = get_user_db()
        finalized_download = False
        if history_service is not None:
            try:
                history_service.finalize_download(
                    task_id=task_id,
                    final_status=final_status,
                    status_message=normalize_optional_text(getattr(task, "status_message", None)),
                    download_path=normalize_optional_text(getattr(task, "download_path", None)),
                    download_identity=_delivered_identity(task),
                    retry_payload=backend.serialize_task_for_retry(task),
                )
                finalized_download = True
            except _OPERATIONAL_ERRORS as exc:
                logger.warning("Failed to finalize download history for task %s: %s", task_id, exc)

        if finalized_download:
            _emit_activity_update_for_task(
                task=task,
                payload={
                    "kind": "download_terminal",
                    "task_id": task_id,
                    "status": final_status,
                },
            )

        if user_db is None or status != QueueStatus.ERROR:
            return

        request_id = normalize_positive_int(getattr(task, "request_id", None))
        if request_id is None:
            return
        if backend.can_retry_download_task(task, status):
            return

        raw_error_message = getattr(task, "status_message", None)
        fallback_reason = (
            raw_error_message.strip()
            if isinstance(raw_error_message, str) and raw_error_message.strip()
            else "Download failed"
        )
        try:
            reopened_request = reopen_failed_request(
                user_db,
                request_id=request_id,
                failure_reason=fallback_reason,
            )
            if reopened_request is not None:
                if view_state_service is not None:
                    view_state_service.clear_item_for_all_viewers(
                        item_type="request",
                        item_key=f"request:{request_id}",
                    )
                emit_request_update_events([reopened_request])
        except _OPERATIONAL_ERRORS as exc:
            logger.warning(
                "Failed to reopen request %s after terminal download error %s: %s",
                request_id,
                task_id,
                exc,
            )

    backend.book_queue.set_queue_hook(_record_download_queued)
    backend.book_queue.set_terminal_status_hook(_record_download_terminal_snapshot)
