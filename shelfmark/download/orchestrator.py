"""Download queue orchestration and worker management.

Two-stage architecture: handlers stage to TMP_DIR, orchestrator moves to INGEST_DIR
with archive extraction and custom script support.
"""

import os
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from typing import Any, Dict, List, Optional, Tuple

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import BookInfo, DownloadTask, QueueStatus, SearchFilters, SearchMode
from shelfmark.core.queue import book_queue
from shelfmark.core.utils import transform_cover_url
from shelfmark.download.fs import run_blocking_io
from shelfmark.download.postprocess.pipeline import is_torrent_source, safe_cleanup_path
from shelfmark.download.postprocess.router import post_process_download
from shelfmark.release_sources import direct_download, get_handler, get_source_display_name
from shelfmark.release_sources.direct_download import SearchUnavailable

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
# De-duplicate status updates (keep-alive updates shouldn't spam clients)
_last_status_event: Dict[str, Tuple[str, Optional[str]]] = {}
STALL_TIMEOUT = 300  # 5 minutes without progress/status update = stalled

def search_books(query: str, filters: SearchFilters) -> List[Dict[str, Any]]:
    """Search for books matching the query."""
    try:
        books = direct_download.search_books(query, filters)
        return [_book_info_to_dict(book) for book in books]
    except SearchUnavailable:
        raise
    except Exception as e:
        logger.error_trace(f"Error searching books: {e}")
        raise

def get_book_info(book_id: str) -> Optional[Dict[str, Any]]:
    """Get detailed information for a specific book."""
    try:
        book = direct_download.get_book_info(book_id)
        return _book_info_to_dict(book)
    except Exception as e:
        logger.error_trace(f"Error getting book info: {e}")
        raise

def queue_book(book_id: str, priority: int = 0, source: str = "direct_download") -> Tuple[bool, Optional[str]]:
    """Add a book to the download queue. Returns (success, error_message)."""
    try:
        book_info = direct_download.get_book_info(book_id, fetch_download_count=False)
        if not book_info:
            error_msg = f"Could not fetch book info for {book_id}"
            logger.warning(error_msg)
            return False, error_msg

        # Create a source-agnostic download task
        task = DownloadTask(
            task_id=book_id,
            source=source,
            title=book_info.title,
            author=book_info.author,
            format=book_info.format,
            size=book_info.size,
            preview=book_info.preview,
            content_type=book_info.content,
            search_mode=SearchMode.DIRECT,
            priority=priority,
        )

        if not book_queue.add(task):
            logger.info(f"Book already in queue: {book_info.title}")
            return False, "Book is already in the download queue"

        logger.info(f"Book queued with priority {priority}: {book_info.title}")

        # Broadcast status update via WebSocket
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

        return True, None
    except SearchUnavailable as e:
        error_msg = f"Search service unavailable: {e}"
        logger.warning(error_msg)
        return False, error_msg
    except Exception as e:
        error_msg = f"Error queueing book: {e}"
        logger.error_trace(error_msg)
        return False, error_msg


def queue_release(release_data: dict, priority: int = 0) -> Tuple[bool, Optional[str]]:
    """Add a release to the download queue. Returns (success, error_message)."""
    try:
        source = release_data.get('source', 'direct_download')
        extra = release_data.get('extra', {})

        # Get author, year, preview, and content_type from top-level (preferred) or extra (fallback)
        author = release_data.get('author') or extra.get('author')
        year = release_data.get('year') or extra.get('year')
        preview = release_data.get('preview') or extra.get('preview')
        content_type = release_data.get('content_type') or extra.get('content_type')

        # Get series info for library naming templates
        series_name = release_data.get('series_name') or extra.get('series_name')
        series_position = release_data.get('series_position') or extra.get('series_position')
        subtitle = release_data.get('subtitle') or extra.get('subtitle')

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
            series_name=series_name,
            series_position=series_position,
            subtitle=subtitle,
            search_mode=SearchMode.UNIVERSAL,
            priority=priority,
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
        # Handler not found for this source
        error_msg = f"Unknown release source: {e}"
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

def queue_status() -> Dict[str, Dict[str, Any]]:
    """Get current status of the download queue."""
    status = book_queue.get_status()
    for _, tasks in status.items():
        for _, task in tasks.items():
            if task.download_path and not run_blocking_io(os.path.exists, task.download_path):
                task.download_path = None

    # Convert Enum keys to strings and DownloadTask objects to dicts for JSON serialization
    return {
        status_type.value: {
            task_id: _task_to_dict(task)
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

def _book_info_to_dict(book: BookInfo) -> Dict[str, Any]:
    """Convert BookInfo to dict, transforming cover URLs for caching."""
    result = {
        key: value for key, value in book.__dict__.items()
        if value is not None
    }

    # Transform external preview URLs to local proxy URLs
    if result.get('preview'):
        result['preview'] = transform_cover_url(result['preview'], book.id)

    return result


def _task_to_dict(task: DownloadTask) -> Dict[str, Any]:
    """Convert DownloadTask to dict for frontend, transforming cover URLs."""
    # Transform external preview URLs to local proxy URLs
    preview = transform_cover_url(task.preview, task.task_id)

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
    }


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
            update_download_status(task_id, status, message)

        # Get the download handler based on the task's source
        handler = get_handler(task.source)
        temp_path = handler.download(
            task,
            cancel_flag,
            progress_callback,
            status_callback
        )

        # Handler returns temp path - orchestrator handles post-processing
        if not temp_path:
            return None

        temp_file = Path(temp_path)
        if not run_blocking_io(temp_file.exists):
            logger.error(f"Handler returned non-existent path: {temp_path}")
            return None

        # Check cancellation before post-processing
        if cancel_flag.is_set():
            logger.info("Task %s: cancelled before post-processing", task_id)
            if not is_torrent_source(temp_file, task):
                safe_cleanup_path(temp_file, task)
            return None

        logger.info("Task %s: download finished; starting post-processing", task_id)
        logger.debug("Task %s: post-processing input path: %s", task_id, temp_file)

        # Post-processing: output routing + file processing pipeline
        result = post_process_download(temp_file, task, cancel_flag, status_callback)

        if cancel_flag.is_set():
            logger.info("Task %s: post-processing cancelled", task_id)
        elif result:
            logger.info("Task %s: post-processing complete", task_id)
            logger.debug("Task %s: post-processing result: %s", task_id, result)
        else:
            logger.warning("Task %s: post-processing failed", task_id)

        try:
            handler.post_process_cleanup(task, success=bool(result))
        except Exception as e:
            logger.warning("Post-processing cleanup hook failed for %s: %s", task_id, e)

        return result

    except Exception as e:
        if cancel_flag.is_set():
            logger.info("Task %s: cancelled during error handling", task_id)
        else:
            logger.error_trace("Task %s: error downloading: %s", task_id, e)
            # Update task status so user sees the failure
            task = book_queue.get_task(task_id)
            if task:
                book_queue.update_status(task_id, QueueStatus.ERROR)
                # Check for known misconfiguration from earlier versions
                if isinstance(e, PermissionError) and "/cwa-book-ingest" in str(e):
                    book_queue.update_status_message(
                        task_id,
                        "Destination misconfigured. Go to Settings → Downloads to update."
                    )
                else:
                    if isinstance(e, PermissionError):
                        book_queue.update_status_message(task_id, f"Permission denied: {e}")
                    else:
                        book_queue.update_status_message(task_id, f"Download failed: {type(e).__name__}")
        return None



def update_download_progress(book_id: str, progress: float) -> None:
    """Update download progress with throttled WebSocket broadcasts."""
    book_queue.update_progress(book_id, progress)

    # Track activity for stall detection
    with _progress_lock:
        _last_activity[book_id] = time.time()
    
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
            ws_manager.broadcast_download_progress(book_id, progress, 'downloading')

def update_download_status(book_id: str, status: str, message: Optional[str] = None) -> None:
    """Update download status with optional message for UI display."""
    # Map string status to QueueStatus enum
    status_map = {
        'queued': QueueStatus.QUEUED,
        'resolving': QueueStatus.RESOLVING,
        'locating': QueueStatus.LOCATING,
        'downloading': QueueStatus.DOWNLOADING,
        'complete': QueueStatus.COMPLETE,
        'available': QueueStatus.AVAILABLE,
        'error': QueueStatus.ERROR,
        'done': QueueStatus.DONE,
        'cancelled': QueueStatus.CANCELLED,
    }
    
    status_key = status.lower()
    queue_status_enum = status_map.get(status_key)
    if not queue_status_enum:
        return

    # Always update activity timestamp (used by stall detection) even if the status
    # event is a duplicate keep-alive update.
    with _progress_lock:
        _last_activity[book_id] = time.time()
        status_event = (status_key, message)
        if _last_status_event.get(book_id) == status_event:
            return
        _last_status_event[book_id] = status_event

    book_queue.update_status(book_id, queue_status_enum)

    # Update status message if provided (empty string clears the message)
    if message is not None:
        book_queue.update_status_message(book_id, message)

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

def clear_completed() -> int:
    """Clear all completed downloads from tracking."""
    return book_queue.clear_completed()

def _cleanup_progress_tracking(task_id: str) -> None:
    """Clean up progress tracking data for a completed/cancelled download."""
    with _progress_lock:
        _progress_last_broadcast.pop(task_id, None)
        _progress_last_broadcast.pop(f"{task_id}_progress", None)
        _last_activity.pop(task_id, None)
        _last_status_event.pop(task_id, None)


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
            # Only update status if not already set (e.g., by archive extraction callback)
            task = book_queue.get_task(task_id)
            if not task or task.status != QueueStatus.COMPLETE:
                book_queue.update_status(task_id, QueueStatus.COMPLETE)
        else:
            book_queue.update_status(task_id, QueueStatus.ERROR)

        # Broadcast final status (completed or error)
        if ws_manager:
            ws_manager.broadcast_status_update(queue_status())

    except Exception as e:
        # Clean up progress tracking even on error
        _cleanup_progress_tracking(task_id)

        if not cancel_flag.is_set():
            logger.error_trace(f"Error in download processing: {e}")
            book_queue.update_status(task_id, QueueStatus.ERROR)
            # Set error message if not already set by handler
            task = book_queue.get_task(task_id)
            if task and not task.status_message:
                book_queue.update_status_message(task_id, f"Download failed: {type(e).__name__}: {str(e)}")
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

# Download coordinator thread (started explicitly via start())
_coordinator_thread: Optional[threading.Thread] = None
_started = False


def start() -> None:
    """Start the download coordinator thread. Safe to call multiple times."""
    global _coordinator_thread, _started

    if _started:
        logger.debug("Download coordinator already started")
        return

    _coordinator_thread = threading.Thread(
        target=concurrent_download_loop,
        daemon=True,
        name="DownloadCoordinator"
    )
    _coordinator_thread.start()
    _started = True

    logger.info(f"Download coordinator started with {config.MAX_CONCURRENT_DOWNLOADS} concurrent workers")
