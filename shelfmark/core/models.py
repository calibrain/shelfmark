"""Data structures and models used across the application."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum
import re
import time


def build_filename(
    title: str,
    author: Optional[str] = None,
    year: Optional[str] = None,
    fmt: Optional[str] = None,
) -> str:
    parts = []
    if author:
        parts.append(author)
        parts.append(" - ")
    parts.append(title)
    if year:
        parts.append(f" ({year})")

    filename = "".join(parts)
    filename = re.sub(r'[\\/:*?"<>|]', '_', filename.strip())[:245]

    if fmt:
        filename = f"{filename}.{fmt}"

    return filename


class QueueStatus(str, Enum):
    """Enum for possible book queue statuses."""
    QUEUED = "queued"
    RESOLVING = "resolving"
    LOCATING = "locating"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"


TERMINAL_QUEUE_STATUSES: frozenset[QueueStatus] = frozenset({
    QueueStatus.COMPLETE, QueueStatus.ERROR, QueueStatus.CANCELLED,
})

ACTIVE_QUEUE_STATUSES: frozenset[QueueStatus] = frozenset({
    QueueStatus.QUEUED, QueueStatus.RESOLVING, QueueStatus.LOCATING, QueueStatus.DOWNLOADING,
})


class SearchMode(str, Enum):
    DIRECT = "direct"
    UNIVERSAL = "universal"


@dataclass
class QueueItem:
    """Queue item with priority and metadata."""
    book_id: str
    priority: int
    added_time: float

    def __lt__(self, other):
        """Compare items for priority queue (lower priority number = higher precedence)."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.added_time < other.added_time


@dataclass
class DownloadTask:
    task_id: str                                # Unique ID (e.g., AA MD5 hash, Prowlarr GUID)
    source: str                                 # Handler name ("direct_download", "prowlarr")
    title: str                                  # Display title for queue sidebar

    # Display info for queue sidebar
    author: Optional[str] = None
    year: Optional[str] = None
    format: Optional[str] = None
    size: Optional[str] = None
    preview: Optional[str] = None
    content_type: Optional[str] = None  # "book (fiction)", "audiobook", "magazine", etc.
    source_url: Optional[str] = None  # Original release URL used by source-specific handlers

    # Series info (for library naming templates)
    series_name: Optional[str] = None
    series_position: Optional[float] = None  # Float for novellas (e.g., 1.5)
    subtitle: Optional[str] = None  # Book subtitle for naming templates

    # Hardlinking support
    original_download_path: Optional[str] = None  # Path in download client (for hardlinking)

    # Search mode - determines post-download processing behavior
    # See SearchMode enum for behavioral differences
    search_mode: Optional[SearchMode] = None

    # Output selection for post-processing.
    # This is captured at queue time so in-flight tasks are not affected if the user changes settings later.
    output_mode: Optional[str] = None  # e.g. "folder", "booklore", "email"
    output_args: Dict[str, Any] = field(default_factory=dict)  # Per-output parameters (e.g. email recipient)

    # User association (multi-user support)
    user_id: Optional[int] = None  # DB user ID who queued this download
    username: Optional[str] = None  # Username for {User} template variable
    request_id: Optional[int] = None  # Origin request ID when queued from request fulfilment

    # Runtime state
    priority: int = 0
    added_time: float = field(default_factory=time.time)
    progress: float = 0.0
    status: QueueStatus = QueueStatus.QUEUED
    status_message: Optional[str] = None
    download_path: Optional[str] = None
    last_error_message: Optional[str] = None
    last_error_type: Optional[str] = None
    staged_path: Optional[str] = None

    def __lt__(self, other):
        """Compare tasks for priority queue (lower priority number = higher precedence)."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.added_time < other.added_time

    def get_filename(self) -> str:
        """Build sanitized filename from task metadata."""
        if self.download_path:
            return Path(self.download_path).name
        return build_filename(self.title, self.author, self.year, self.format)


@dataclass
class SearchFilters:
    """Filters for book search queries."""
    isbn: Optional[List[str]] = None
    author: Optional[List[str]] = None
    title: Optional[List[str]] = None
    lang: Optional[List[str]] = None
    sort: Optional[str] = None
    content: Optional[List[str]] = None
    format: Optional[List[str]] = None
