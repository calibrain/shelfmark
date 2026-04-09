"""Download queue orchestration and worker management.

Two-stage architecture: handlers stage to TMP_DIR, orchestrator moves to INGEST_DIR
with archive extraction and custom script support.
"""

import os
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from email.utils import parseaddr
from pathlib import Path
from threading import Event, Lock
from typing import Any, Dict, List, Optional, Tuple

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask, QueueStatus, SearchMode
from shelfmark.core.queue import book_queue
from shelfmark.core.request_helpers import normalize_optional_text, normalize_positive_int
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.core.utils import transform_cover_url
from shelfmark.download.fs import run_blocking_io
from shelfmark.download.postprocess.pipeline import is_torrent_source, safe_cleanup_path
from shelfmark.download.postprocess.router import post_process_download
from shelfmark.release_sources import (
    get_handler,
    get_source_display_name,
)

logger = setup_logger(__name__)


# =============================================================================
# Task Download and Processing
# =============================================================================
#
# Post-download processing (staging, extraction, transfers, cleanup) lives in
# `shelfmark.download.postprocess`.


# WebSocket manager (initialized by app.py)
# Track whether WebSocket is available for status reporting
WEBSOCKET_AVAILABLE = True
try:
    from shelfmark.api.websocket import ws_manager
except ImportError:
    logger.error("WebSocket unavailable - real-time updates disabled")
    ws_manager = None
    WEBSOCKET_AVAILABLE = False

# Progress update throttling - track last broadcast time per book
_progress_last_broadcast: Dict[str, float] = {}
_progress_lock = Lock()

# Stall detection - track last activity time per download
_last_activity: Dict[str, float] = {}
_last_progress_value: Dict[str, float] = {}
# De-duplicate status updates (keep-alive updates shouldn't spam clients)
_last_status_event: Dict[str, Tuple[str, Optional[str]]] = {}
STALL_TIMEOUT = 300  # 5 minutes without progress/status update = stalled
COORDINATOR_LOOP_ERROR_RETRY_DELAY = 1.0

def _is_plain_email_address(value: str) -> bool:
    parsed = parseaddr(value or "")[1]
    return bool(parsed) and "@" in parsed and parsed == value


def _resolve_email_destination(
    user_id: Optional[int] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the destination email address for email output mode.

    Returns:
      (email_to, error_message)
    """
    configured_recipient = str(config.get("EMAIL_RECIPIENT", "", user_id=user_id) or "").strip()
    if configured_recipient:
        if _is_plain_email_address(configured_recipient):
            return configured_recipient, None
        return None, "Configured email recipient is invalid"

    return None, None


def _parse_release_search_mode(value: Any) -> SearchMode:
    if isinstance(value, SearchMode):
        return value
    if value is None:
        return SearchMode.UNIVERSAL
    if isinstance(value, str):
        try:
            return SearchMode(value.strip().lower())
        except ValueError as exc:
            raise ValueError(f"Invalid search_mode: {value}") from exc
    raise ValueError(f"Invalid search_mode: {value}")

def _optional_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _seed_time_seconds_to_minutes(value: Any) -> Optional[int]:
    seed_time_seconds = _optional_positive_int(value)
    if seed_time_seconds is None:
        return None
    return (seed_time_seconds + 59) // 60


def _build_retry_resolution_fields(
    release_data: dict[str, Any],
) -> Dict[str, Any]:
    """Persist generic resolved-download data needed for restart-safe retries."""
    extra = release_data.get("extra")
    if not isinstance(extra, dict):
        extra = {}

    protocol = normalize_optional_text(release_data.get("protocol"))
    ratio_limit = _optional_number(release_data.get("ratio_limit"))
    if ratio_limit is None:
        ratio_limit = _optional_number(extra.get("minimum_ratio"))

    seeding_time_limit_minutes = _optional_positive_int(
        release_data.get("seeding_time_limit_minutes")
    )
    if seeding_time_limit_minutes is None:
        seeding_time_limit_minutes = _seed_time_seconds_to_minutes(
            extra.get("minimum_seed_time")
        )

    return {
        "retry_download_url": normalize_optional_text(release_data.get("download_url")),
        "retry_download_protocol": protocol.lower() if protocol is not None else None,
        "retry_release_name": normalize_optional_text(release_data.get("title")),
        "retry_expected_hash": normalize_optional_text(
            release_data.get("expected_hash") or extra.get("info_hash")
        ),
        "retry_ratio_limit": ratio_limit,
        "retry_seeding_time_limit_minutes": seeding_time_limit_minutes,
        "can_retry_without_staged_source": True,
    }


def queue_release(
    release_data: dict,
    priority: int = 0,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Add a release to the download queue. Returns (success, error_message)."""
    try:
        source = release_data['source']
        extra = release_data.get('extra', {})
        raw_request_id = release_data.get('_request_id')
        request_id: Optional[int] = None
        if isinstance(raw_request_id, int) and raw_request_id > 0:
            request_id = raw_request_id
        search_mode = _parse_release_search_mode(release_data.get("search_mode"))

        # Get author, year, preview, and content_type from top-level (preferred) or extra (fallback)
        author = release_data.get('author') or extra.get('author')
        year = release_data.get('year') or extra.get('year')
        preview = release_data.get('preview') or extra.get('preview')
        content_type = release_data.get('content_type') or extra.get('content_type')
        source_url_raw = (
            release_data.get('download_url')
            or release_data.get('source_url')
            or release_data.get('info_url')
            or extra.get('detail_url')
            or extra.get('source_url')
        )
        source_url = source_url_raw.strip() if isinstance(source_url_raw, str) else None
        if source_url == "":
            source_url = None

        # Get series info for library naming templates
        series_name = release_data.get('series_name') or extra.get('series_name')
        series_position = release_data.get('series_position') or extra.get('series_position')
        subtitle = release_data.get('subtitle') or extra.get('subtitle')

        books_output_mode = str(
            config.get("BOOKS_OUTPUT_MODE", "folder", user_id=user_id) or "folder"
        ).strip().lower()
        is_audiobook = check_audiobook(content_type)

        output_mode = "folder" if is_audiobook else books_output_mode
        output_args: Dict[str, Any] = {}
        retry_resolution_fields = _build_retry_resolution_fields(release_data)

        if output_mode == "email" and not is_audiobook:
            email_to, email_error = _resolve_email_destination(user_id=user_id)
            if email_error:
                return False, email_error
            if email_to:
                output_args = {"to": email_to}

        # Create a source-agnostic download task from release data
        task = DownloadTask(
            task_id=release_data['source_id'],
            source=source,
            title=release_data.get('title', 'Unknown'),
            author=author,
            year=year,
            format=release_data.get('format'),
            size=release_data.get('size'),
            preview=preview,
            content_type=content_type,
            source_url=source_url,
            series_name=series_name,
            series_position=series_position,
            subtitle=subtitle,
            search_mode=search_mode,
            output_mode=output_mode,
            output_args=output_args,
            priority=priority,
            user_id=user_id,
            username=username,
            request_id=request_id,
            **retry_resolution_fields,
        )

        if not book_queue.add(task):
            logger.info(f"Release already in queue: {task.title}")
            return False, "Release is already in the download queue"

        logger.info(f"Release queued with priority {priority}: {task.title}")

        # Broadcast status update via WebSocket
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

        return True, None

    except ValueError as e:
        error_msg = str(e)
        logger.warning(error_msg)
        return False, error_msg
    except KeyError as e:
        error_msg = f"Missing required field in release data: {e}"
        logger.warning(error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"Error queueing release: {e}"
        logger.error_trace(error_msg)
        return False, error_msg

def queue_status(user_id: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """Get current status of the download queue."""
    status = book_queue.get_status(user_id=user_id)
    for _, tasks in status.items():
        for _, task in tasks.items():
            if task.download_path and not run_blocking_io(os.path.exists, task.download_path):
                task.download_path = None

    # Convert Enum keys to strings and DownloadTask objects to dicts for JSON serialization
    return {
        status_type.value: {
            task_id: _task_to_dict(task, current_status=status_type)
            for task_id, task in tasks.items()
        }
        for status_type, tasks in status.items()
    }

def get_book_data(task_id: str) -> Tuple[Optional[bytes], Optional[DownloadTask]]:
    """Get downloaded file data for a specific task."""
    task = None
    try:
        task = book_queue.get_task(task_id)
        if not task:
            return None, None

        path = task.download_path
        if not path:
            return None, task

        with open(path, "rb") as f:
            return f.read(), task
    except Exception as e:
        logger.error_trace(f"Error getting book data: {e}")
        if task:
            task.download_path = None
        return None, task

def _has_staged_retry_source(task: DownloadTask) -> bool:
    """Whether a failed task still has a staged file available for retry."""
    staged_path = task.staged_path.strip() if isinstance(task.staged_path, str) else ""
    if not staged_path:
        return False
    try:
        return run_blocking_io(Path(staged_path).exists)
    except OSError:
        return False


def _has_fresh_retry_context(task: DownloadTask) -> bool:
    """Whether the task can restart without relying on a staged file."""
    return bool(getattr(task, "can_retry_without_staged_source", True))


def can_retry_download_task(
    task: Optional[DownloadTask],
    status: Optional[QueueStatus],
) -> bool:
    """Whether the task can be manually retried from the Activity UI."""
    if task is None or status not in (QueueStatus.ERROR, QueueStatus.CANCELLED):
        return False

    if task.request_id is None:
        return _has_staged_retry_source(task) or _has_fresh_retry_context(task)

    if status == QueueStatus.CANCELLED:
        return _has_fresh_retry_context(task)

    return _has_staged_retry_source(task)


def serialize_task_for_retry(task: DownloadTask) -> Dict[str, Any]:
    """Serialize the task state needed for restart-safe retries."""
    raw_search_mode = getattr(task, "search_mode", None)
    search_mode: Optional[str] = None
    if isinstance(raw_search_mode, SearchMode):
        search_mode = raw_search_mode.value
    elif isinstance(raw_search_mode, str):
        normalized_search_mode = raw_search_mode.strip().lower()
        search_mode = normalized_search_mode or None

    raw_output_args = getattr(task, "output_args", None)

    return {
        "task_id": getattr(task, "task_id", None),
        "source": getattr(task, "source", None),
        "title": getattr(task, "title", None),
        "author": getattr(task, "author", None),
        "year": getattr(task, "year", None),
        "format": getattr(task, "format", None),
        "size": getattr(task, "size", None),
        "preview": getattr(task, "preview", None),
        "content_type": getattr(task, "content_type", None),
        "source_url": getattr(task, "source_url", None),
        "series_name": getattr(task, "series_name", None),
        "series_position": getattr(task, "series_position", None),
        "subtitle": getattr(task, "subtitle", None),
        "search_mode": search_mode,
        "output_mode": getattr(task, "output_mode", None),
        "output_args": dict(raw_output_args) if isinstance(raw_output_args, dict) else {},
        "user_id": getattr(task, "user_id", None),
        "username": getattr(task, "username", None),
        "request_id": getattr(task, "request_id", None),
        "staged_path": getattr(task, "staged_path", None),
        "retry_download_url": getattr(task, "retry_download_url", None),
        "retry_download_protocol": getattr(task, "retry_download_protocol", None),
        "retry_release_name": getattr(task, "retry_release_name", None),
        "retry_expected_hash": getattr(task, "retry_expected_hash", None),
        "retry_ratio_limit": getattr(task, "retry_ratio_limit", None),
        "retry_seeding_time_limit_minutes": getattr(task, "retry_seeding_time_limit_minutes", None),
        "can_retry_without_staged_source": bool(
            getattr(task, "can_retry_without_staged_source", True)
        ),
    }


def _restore_task_from_retry_payload(payload: Any) -> Optional[DownloadTask]:
    if not isinstance(payload, dict):
        return None

    task_id = normalize_optional_text(payload.get("task_id"))
    source = normalize_optional_text(payload.get("source"))
    title = normalize_optional_text(payload.get("title"))
    if task_id is None or source is None or title is None:
        return None

    search_mode = None
    raw_search_mode = payload.get("search_mode")
    if raw_search_mode is not None:
        try:
            search_mode = _parse_release_search_mode(raw_search_mode)
        except ValueError:
            search_mode = None

    output_args = payload.get("output_args")

    return DownloadTask(
        task_id=task_id,
        source=source,
        title=title,
        author=normalize_optional_text(payload.get("author")),
        year=normalize_optional_text(payload.get("year")),
        format=normalize_optional_text(payload.get("format")),
        size=normalize_optional_text(payload.get("size")),
        preview=normalize_optional_text(payload.get("preview")),
        content_type=normalize_optional_text(payload.get("content_type")),
        source_url=normalize_optional_text(payload.get("source_url")),
        series_name=normalize_optional_text(payload.get("series_name")),
        series_position=_optional_number(payload.get("series_position")),
        subtitle=normalize_optional_text(payload.get("subtitle")),
        search_mode=search_mode,
        output_mode=normalize_optional_text(payload.get("output_mode")),
        output_args=dict(output_args) if isinstance(output_args, dict) else {},
        user_id=normalize_positive_int(payload.get("user_id")),
        username=normalize_optional_text(payload.get("username")),
        request_id=normalize_positive_int(payload.get("request_id")),
        staged_path=normalize_optional_text(payload.get("staged_path")),
        retry_download_url=normalize_optional_text(payload.get("retry_download_url")),
        retry_download_protocol=normalize_optional_text(payload.get("retry_download_protocol")),
        retry_release_name=normalize_optional_text(payload.get("retry_release_name")),
        retry_expected_hash=normalize_optional_text(payload.get("retry_expected_hash")),
        retry_ratio_limit=_optional_number(payload.get("retry_ratio_limit")),
        retry_seeding_time_limit_minutes=_optional_positive_int(
            payload.get("retry_seeding_time_limit_minutes")
        ),
        can_retry_without_staged_source=bool(
            payload.get("can_retry_without_staged_source", True)
        ),
    )


def retry_persisted_download(
    payload: Any,
    *,
    final_status: Any,
    priority: int = -10,
) -> Tuple[bool, Optional[str]]:
    """Retry a persisted download row after the in-memory task has been lost."""
    task = _restore_task_from_retry_payload(payload)
    if task is None:
        return False, "Download cannot be retried"

    normalized_status = normalize_optional_text(final_status)
    if normalized_status is None:
        return False, "Download cannot be retried"
    normalized_status = normalized_status.lower()
    if normalized_status not in {"active", "error", "cancelled"}:
        return False, "Download cannot be retried"

    has_staged_retry_source = _has_staged_retry_source(task)
    has_fresh_retry_context = _has_fresh_retry_context(task)

    if normalized_status in {"active", "cancelled"} and not has_fresh_retry_context:
        return False, "Download cannot be retried"

    if (
        task.request_id is not None
        and normalized_status == "error"
        and not has_staged_retry_source
    ):
        if task.request_id is not None:
            return False, "Request-linked downloads must be retried from requests"

    if (
        task.request_id is None
        and normalized_status == "error"
        and not has_staged_retry_source
        and not has_fresh_retry_context
    ):
        return False, "Download cannot be retried"

    task.priority = priority
    task.status_message = None
    _clear_task_error_state(task)

    if not book_queue.add(task):
        return False, "Failed to requeue download"

    book_queue.update_status_message(task.task_id, "Retrying now")

    if ws_manager:
        ws_manager.broadcast_status_update(queue_status())

    return True, None


def _task_to_dict(
    task: DownloadTask,
    current_status: Optional[QueueStatus] = None,
) -> Dict[str, Any]:
    """Convert DownloadTask to dict for frontend, transforming cover URLs."""
    # Transform external preview URLs to local proxy URLs
    preview = transform_cover_url(task.preview, task.task_id)
    retry_status = current_status or book_queue.get_task_status(task.task_id)

    return {
        'id': task.task_id,
        'title': task.title,
        'author': task.author,
        'format': task.format,
        'size': task.size,
        'preview': preview,
        'content_type': task.content_type,
        'source': task.source,
        'source_display_name': get_source_display_name(task.source),
        'priority': task.priority,
        'added_time': task.added_time,
        'progress': task.progress,
        'status': task.status,
        'status_message': task.status_message,
        'download_path': task.download_path,
        'user_id': task.user_id,
        'username': task.username,
        'request_id': task.request_id,
        'retry_available': can_retry_download_task(task, retry_status),
    }


def _clear_task_error_state(task: DownloadTask) -> None:
    task.last_error_message = None
    task.last_error_type = None


def _capture_task_error(
    task: DownloadTask,
    *,
    message: Optional[str] = None,
    exc_type: Optional[str] = None,
) -> None:
    if isinstance(message, str):
        normalized = message.strip()
        if normalized:
            task.last_error_message = normalized
            book_queue.update_status_message(task.task_id, normalized)
    if isinstance(exc_type, str):
        normalized_type = exc_type.strip()
        if normalized_type:
            task.last_error_type = normalized_type


def _format_download_exception_message(exc: Exception) -> str:
    if isinstance(exc, PermissionError) and "/cwa-book-ingest" in str(exc):
        return "Destination misconfigured. Go to Settings → Downloads to update."
    if isinstance(exc, PermissionError):
        return f"Permission denied: {exc}"
    return f"Download failed: {type(exc).__name__}"


def _download_task(task_id: str, cancel_flag: Event) -> Optional[str]:
    """Download a task via appropriate handler, then post-process to ingest."""
    try:
        # Check for cancellation before starting
        if cancel_flag.is_set():
            logger.info("Task %s: cancelled before starting", task_id)
            return None

        task = book_queue.get_task(task_id)
        if not task:
            logger.error("Task not found in queue: %s", task_id)
            return None

        title_label = task.title or "Unknown title"
        logger.info(
            "Task %s: starting download (%s) - %s",
            task_id,
            get_source_display_name(task.source),
            title_label,
        )

        def progress_callback(progress: float) -> None:
            update_download_progress(task_id, progress)

        def status_callback(status: str, message: Optional[str] = None) -> None:
            status_key = status.lower()
            if status_key == "error":
                _capture_task_error(
                    task,
                    message=message or "Download failed",
                    exc_type="StatusCallbackError",
                )
                return
            # Don't propagate terminal statuses to the queue here. Output modules
            # call status_callback("complete") before returning the download path,
            # but _process_single_download needs to set download_path on the task
            # first so the terminal hook captures it for history persistence.
            if status_key in ("complete", "cancelled"):
                if message is not None:
                    book_queue.update_status_message(task_id, message)
                return
            update_download_status(task_id, status, message)

        # Get the download handler based on the task's source
        handler = get_handler(task.source)
        temp_file: Optional[Path] = None

        if task.staged_path:
            staged_file = Path(task.staged_path)
            if run_blocking_io(staged_file.exists):
                temp_file = staged_file
                logger.info("Task %s: reusing staged file for retry: %s", task_id, staged_file)
            else:
                task.staged_path = None

        if temp_file is None:
            temp_path = handler.download(
                task,
                cancel_flag,
                progress_callback,
                status_callback,
            )

            # Handler returns temp path - orchestrator handles post-processing
            if not temp_path:
                return None

            temp_file = Path(temp_path)
            if not run_blocking_io(temp_file.exists):
                logger.error(f"Handler returned non-existent path: {temp_path}")
                _capture_task_error(
                    task,
                    message=f"Download file missing: {temp_path}",
                    exc_type="MissingDownloadPath",
                )
                return None

        # Check cancellation before post-processing
        if cancel_flag.is_set():
            logger.info("Task %s: cancelled before post-processing", task_id)
            if not is_torrent_source(temp_file, task):
                safe_cleanup_path(temp_file, task)
            return None

        logger.info("Task %s: download finished; starting post-processing", task_id)
        logger.debug("Task %s: post-processing input path: %s", task_id, temp_file)
        task.staged_path = str(temp_file)
        preserve_source_on_failure = True

        # Post-processing: output routing + file processing pipeline
        result = post_process_download(
            temp_file,
            task,
            cancel_flag,
            status_callback,
            preserve_source_on_failure=preserve_source_on_failure,
        )

        if cancel_flag.is_set():
            logger.info("Task %s: post-processing cancelled", task_id)
        elif result:
            logger.info("Task %s: post-processing complete", task_id)
            logger.debug("Task %s: post-processing result: %s", task_id, result)
        else:
            logger.warning("Task %s: post-processing failed", task_id)
            if not task.last_error_message:
                _capture_task_error(
                    task,
                    message="Download failed",
                    exc_type="UnknownFailure",
                )

        try:
            handler.post_process_cleanup(task, success=bool(result))
        except Exception as e:
            logger.warning("Post-processing cleanup hook failed for %s: %s", task_id, e)

        if result:
            task.staged_path = None
            _clear_task_error_state(task)

        return result

    except Exception as e:
        if cancel_flag.is_set():
            logger.info("Task %s: cancelled during error handling", task_id)
        else:
            logger.error_trace("Task %s: error downloading: %s", task_id, e)
            task = book_queue.get_task(task_id)
            if task:
                _capture_task_error(
                    task,
                    message=_format_download_exception_message(e),
                    exc_type=type(e).__name__,
                )
        return None



def update_download_progress(book_id: str, progress: float) -> None:
    """Update download progress with throttled WebSocket broadcasts."""
    book_queue.update_progress(book_id, progress)

    # Only real progress changes should reset stall detection. Repeated keep-alive
    # polls at the same percentage must not hide a stuck download forever.
    with _progress_lock:
        last_progress = _last_progress_value.get(book_id)
        if last_progress is None or progress != last_progress:
            _last_activity[book_id] = time.time()
        _last_progress_value[book_id] = progress
    
    # Broadcast progress via WebSocket with throttling
    if ws_manager:
        current_time = time.time()
        should_broadcast = False
        
        with _progress_lock:
            last_broadcast = _progress_last_broadcast.get(book_id, 0)
            last_progress = _progress_last_broadcast.get(f"{book_id}_progress", 0)
            time_elapsed = current_time - last_broadcast
            
            # Always broadcast at start (0%) or completion (>=99%)
            if progress <= 1 or progress >= 99:
                should_broadcast = True
            # Broadcast if enough time has passed (convert interval from seconds)
            elif time_elapsed >= config.DOWNLOAD_PROGRESS_UPDATE_INTERVAL:
                should_broadcast = True
            # Broadcast on significant progress jumps (>10%)
            elif progress - last_progress >= 10:
                should_broadcast = True
            
            if should_broadcast:
                _progress_last_broadcast[book_id] = current_time
                _progress_last_broadcast[f"{book_id}_progress"] = progress
        
        if should_broadcast:
            task = book_queue.get_task(book_id)
            task_user_id = task.user_id if task else None
            ws_manager.broadcast_download_progress(book_id, progress, 'downloading', user_id=task_user_id)

def update_download_status(book_id: str, status: str, message: Optional[str] = None) -> None:
    """Update download status with optional message for UI display."""
    status_key = status.lower()
    try:
        queue_status_enum = QueueStatus(status_key)
    except ValueError:
        return

    with _progress_lock:
        status_event = (status_key, message)
        if _last_status_event.get(book_id) == status_event:
            return
        _last_activity[book_id] = time.time()
        _last_status_event[book_id] = status_event

    # Update status message first so terminal snapshots capture the final message
    # (for example, "Complete" or "Sent to ...") instead of a stale in-progress one.
    if message is not None:
        book_queue.update_status_message(book_id, message)

    book_queue.update_status(book_id, queue_status_enum)

    # Broadcast status update via WebSocket
    if ws_manager:
        ws_manager.broadcast_status_update(queue_status())

def cancel_download(book_id: str) -> bool:
    """Cancel a download."""
    result = book_queue.cancel_download(book_id)
    
    # Broadcast status update via WebSocket
    if result and ws_manager and ws_manager.is_enabled():
        ws_manager.broadcast_status_update(queue_status())
    
    return result


def retry_download(book_id: str) -> Tuple[bool, Optional[str]]:
    """Retry a failed or cancelled download.

    Request-linked downloads can only be manually retried when cancelled or
    when a staged post-processing retry is available.
    """
    task = book_queue.get_task(book_id)
    if task is None:
        return False, "Download not found"

    status = book_queue.get_task_status(book_id)
    if status not in (QueueStatus.ERROR, QueueStatus.CANCELLED):
        return False, "Download is not in an error or cancelled state"

    if not can_retry_download_task(task, status):
        return False, "Request-linked downloads must be retried from requests"

    task.last_error_message = None
    task.last_error_type = None
    task.priority = -10

    if not book_queue.enqueue_existing(book_id, priority=-10):
        return False, "Failed to requeue download"

    book_queue.update_status_message(book_id, "Retrying now")

    if ws_manager:
        ws_manager.broadcast_status_update(queue_status())

    return True, None

def set_book_priority(book_id: str, priority: int) -> bool:
    """Set priority for a queued book (lower = higher priority)."""
    return book_queue.set_priority(book_id, priority)

def reorder_queue(book_priorities: Dict[str, int]) -> bool:
    """Bulk reorder queue by mapping book_id to new priority."""
    return book_queue.reorder_queue(book_priorities)

def get_queue_order() -> List[Dict[str, Any]]:
    """Get current queue order for display."""
    return book_queue.get_queue_order()

def get_active_downloads() -> List[str]:
    """Get list of currently active downloads."""
    return book_queue.get_active_downloads()

def _cleanup_progress_tracking(task_id: str) -> None:
    """Clean up progress tracking data for a completed/cancelled download."""
    with _progress_lock:
        _progress_last_broadcast.pop(task_id, None)
        _progress_last_broadcast.pop(f"{task_id}_progress", None)
        _last_activity.pop(task_id, None)
        _last_progress_value.pop(task_id, None)
        _last_status_event.pop(task_id, None)


def _finalize_download_failure(task_id: str) -> None:
    task = book_queue.get_task(task_id)
    if not task:
        return

    message = task.last_error_message or task.status_message or ""
    normalized_message = message.strip()
    if not normalized_message:
        normalized_message = (
            f"Download failed: {task.last_error_type}"
            if task.last_error_type
            else "Download failed"
        )

    book_queue.update_status_message(task_id, normalized_message)
    book_queue.update_status(task_id, QueueStatus.ERROR)


def _process_single_download(task_id: str, cancel_flag: Event) -> None:
    """Process a single download job."""
    try:
        # Status will be updated through callbacks during download process
        # (resolving -> downloading -> complete)
        download_path = _download_task(task_id, cancel_flag)

        # Clean up progress tracking
        _cleanup_progress_tracking(task_id)

        if cancel_flag.is_set():
            book_queue.update_status(task_id, QueueStatus.CANCELLED)
            # Broadcast cancellation
            if ws_manager:
                ws_manager.broadcast_status_update(queue_status())
            return

        if download_path:
            book_queue.update_download_path(task_id, download_path)
            book_queue.update_status(task_id, QueueStatus.COMPLETE)
        else:
            _finalize_download_failure(task_id)

        # Broadcast final status (completed or error)
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

    except Exception as e:
        # Clean up progress tracking even on error
        _cleanup_progress_tracking(task_id)

        if not cancel_flag.is_set():
            logger.error_trace(f"Error in download processing: {e}")
            task = book_queue.get_task(task_id)
            if task:
                _capture_task_error(
                    task,
                    message=f"Download failed: {type(e).__name__}: {str(e)}",
                    exc_type=type(e).__name__,
                )
            _finalize_download_failure(task_id)
        else:
            logger.info(f"Download cancelled: {task_id}")
            book_queue.update_status(task_id, QueueStatus.CANCELLED)

        # Broadcast error/cancelled status
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

def concurrent_download_loop() -> None:
    """Main download coordinator using ThreadPoolExecutor for concurrent downloads."""
    max_workers = config.MAX_CONCURRENT_DOWNLOADS
    logger.info(f"Starting concurrent download loop with {max_workers} workers")

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="Download") as executor:
        active_futures: Dict[Future, str] = {}  # Track active download futures
        stalled_tasks: set[str] = set()  # Track tasks already cancelled due to stall

        while True:
            try:
                # Clean up completed futures
                completed_futures = [f for f in active_futures if f.done()]
                for future in completed_futures:
                    task_id = active_futures.pop(future)
                    stalled_tasks.discard(task_id)
                    try:
                        future.result()  # This will raise any exceptions from the worker
                    except Exception as e:
                        logger.error_trace(f"Future exception for {task_id}: {e}")

                # Check for stalled downloads (no activity in STALL_TIMEOUT seconds)
                current_time = time.time()
                with _progress_lock:
                    for future, task_id in list(active_futures.items()):
                        if task_id in stalled_tasks:
                            continue
                        last_active = _last_activity.get(task_id, current_time)
                        if current_time - last_active > STALL_TIMEOUT:
                            logger.warning(f"Download stalled for {task_id}, cancelling")
                            book_queue.cancel_download(task_id)
                            book_queue.update_status_message(task_id, f"Download stalled (no activity for {STALL_TIMEOUT}s)")
                            stalled_tasks.add(task_id)

                # Start new downloads if we have capacity
                while len(active_futures) < max_workers:
                    next_download = book_queue.get_next()
                    if not next_download:
                        break

                    # Stagger concurrent downloads to avoid rate limiting on shared download servers
                    # Only delay if other downloads are already active
                    if active_futures:
                        stagger_delay = random.uniform(2, 5)
                        logger.debug(f"Staggering download start by {stagger_delay:.1f}s")
                        time.sleep(stagger_delay)

                    task_id, cancel_flag = next_download

                    # Submit download job to thread pool
                    future = executor.submit(_process_single_download, task_id, cancel_flag)
                    active_futures[future] = task_id

                # Brief sleep to prevent busy waiting
                time.sleep(config.MAIN_LOOP_SLEEP_TIME)
            except Exception as e:
                logger.error_trace("Download coordinator loop error: %s", e)
                time.sleep(COORDINATOR_LOOP_ERROR_RETRY_DELAY)

# Download coordinator thread (started explicitly via start())
_coordinator_thread: Optional[threading.Thread] = None
_coordinator_lock = Lock()


def start() -> None:
    """Start the download coordinator thread. Safe to call multiple times."""
    global _coordinator_thread

    with _coordinator_lock:
        if _coordinator_thread is not None and _coordinator_thread.is_alive():
            logger.debug("Download coordinator already started")
            return

        if _coordinator_thread is not None:
            logger.warning("Download coordinator thread is not alive; starting a new one")

        _coordinator_thread = threading.Thread(
            target=concurrent_download_loop,
            daemon=True,
            name="DownloadCoordinator"
        )
        _coordinator_thread.start()

    logger.info(f"Download coordinator started with {config.MAX_CONCURRENT_DOWNLOADS} concurrent workers")
