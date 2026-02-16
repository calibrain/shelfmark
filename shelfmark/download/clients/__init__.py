"""
Shared download client infrastructure for external release sources.

This module provides:
- DownloadState: Enum of valid download states
- DownloadStatus: Status dataclass for external download progress
- DownloadClient: Abstract base class for download clients
- Client registry and factory functions

Clients register themselves via the @register_client decorator.
"""

import logging
import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Callable, Dict, List, Optional, Tuple, Type, TypeVar, Union, cast, Any

import requests

_logger = logging.getLogger(__name__)

# Type variable for generic return type
T = TypeVar('T')

# Exceptions that should trigger a retry
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.HTTPError,
)


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    jitter: float = 0.5,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for retrying API calls with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (default 3)
        base_delay: Initial delay in seconds (default 1.0)
        max_delay: Maximum delay cap in seconds (default 10.0)
        jitter: Random jitter factor 0-1 to add to delay (default 0.5)

    Retries on:
        - Connection errors
        - Timeouts
        - HTTP 5xx server errors

    Does NOT retry on:
        - HTTP 4xx client errors (bad request, auth failures)
        - Other exceptions (programming errors)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.HTTPError as e:
                    # Only retry on server errors (5xx), not client errors (4xx)
                    if e.response is not None and e.response.status_code < 500:
                        raise
                    last_exception = e
                except RETRYABLE_EXCEPTIONS as e:
                    last_exception = e

                if attempt < max_attempts:
                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    # Add jitter to prevent thundering herd
                    delay += random.uniform(0, delay * jitter)
                    _logger.debug(
                        f"Retry {attempt}/{max_attempts} for {func.__name__} "
                        f"after {delay:.1f}s (error: {last_exception})"
                    )
                    time.sleep(delay)

            # All retries exhausted
            if last_exception is None:
                raise RuntimeError("Retry failed without exception")
            raise cast(Exception, last_exception)

        return wrapper
    return decorator


class DownloadState(Enum):
    """Valid states for a download."""

    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    ERROR = "error"
    SEEDING = "seeding"
    PAUSED = "paused"
    QUEUED = "queued"
    CHECKING = "checking"
    PROCESSING = "processing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DownloadStatus:
    """Status of an external download (immutable)."""

    progress: float  # 0-100
    state: Union[DownloadState, str]  # Prefer DownloadState enum; strings auto-normalized
    message: Optional[str]  # Status message
    complete: bool  # True when download finished
    file_path: Optional[str]  # Path in client's download dir (when complete)
    download_speed: Optional[int] = None  # Bytes per second
    eta: Optional[int] = None  # Seconds remaining

    @classmethod
    def error(cls, message: str) -> "DownloadStatus":
        """Create an error status."""
        return cls(
            progress=0,
            state=DownloadState.ERROR,
            message=message,
            complete=False,
            file_path=None,
        )

    def __post_init__(self):
        """Validate and normalize state."""
        # Normalize string states to enum
        if isinstance(self.state, str):
            try:
                normalized_state = DownloadState(self.state)
                object.__setattr__(self, 'state', normalized_state)
            except ValueError:
                # Unknown state string - keep as-is for backwards compatibility
                _logger.warning(f"Unknown download state '{self.state}', keeping as string")

        # Validate progress is in range
        if not 0 <= self.progress <= 100:
            _logger.debug(f"Progress {self.progress} out of range, clamping to [0, 100]")
            object.__setattr__(self, 'progress', max(0, min(100, self.progress)))

    @property
    def state_value(self) -> str:
        """Get the state as a string value (for JSON serialization)."""
        if isinstance(self.state, DownloadState):
            return self.state.value
        return self.state


class DownloadClient(ABC):
    """
    Base class for external download clients.

    Subclasses implement protocol-specific download management:
    - Torrent clients: qBittorrent, Transmission, Deluge
    - Usenet clients: NZBGet, SABnzbd

    Subclasses must define:
    - protocol: "torrent" or "usenet"
    - name: Unique client identifier (e.g., "qbittorrent", "nzbget")
    """

    # Class attributes that subclasses must define
    protocol: str
    name: str

    def _log_error(self, method: str, e: Exception, level: str = "error") -> str:
        """
        Log a client error with consistent formatting.

        Args:
            method: Name of the method that failed (e.g., "get_status")
            e: The exception that was raised
            level: Log level - "error" or "debug"

        Returns:
            Formatted error message string (for use in DownloadStatus.error())
        """
        error_type = type(e).__name__
        msg = f"{self.name} {method} failed ({error_type}): {e}"
        if level == "debug":
            _logger.debug(msg)
        else:
            _logger.error(msg)

        # Reset connection state if client tracks it (e.g., Deluge)
        if hasattr(self, "_connected"):
            self._connected = False

        return f"{error_type}: {e}"

    def _build_path(self, *components: str) -> Optional[str]:
        """
        Safely build a file path from components.

        Args:
            *components: Path components to join (e.g., save_path, name)

        Returns:
            Normalized path string, or None if any component is empty/None.
        """
        # Filter out empty/None components
        valid = [c for c in components if c]
        if len(valid) != len(components):
            return None

        # Join and normalize
        return os.path.normpath(os.path.join(*valid))

    def __init_subclass__(cls, **kwargs):
        """Validate that subclasses define required class attributes."""
        super().__init_subclass__(**kwargs)

        # Skip validation for abstract subclasses
        if ABC in cls.__bases__:
            return

        # Validate protocol attribute
        if not hasattr(cls, 'protocol') or not cls.protocol:
            raise TypeError(f"{cls.__name__} must define 'protocol' class attribute")
        if cls.protocol not in ('torrent', 'usenet'):
            raise TypeError(
                f"{cls.__name__}.protocol must be 'torrent' or 'usenet', got '{cls.protocol}'"
            )

        # Validate name attribute
        if not hasattr(cls, 'name') or not cls.name:
            raise TypeError(f"{cls.__name__} must define 'name' class attribute")

    @staticmethod
    @abstractmethod
    def is_configured() -> bool:
        """
        Check if this client is configured.

        Returns:
            True if required settings (URL, etc.) are present.
        """
        pass

    @abstractmethod
    def test_connection(self) -> Tuple[bool, str]:
        """
        Test connectivity to the client.

        Returns:
            Tuple of (success, message).
        """
        pass

    @abstractmethod
    def add_download(
        self,
        url: str,
        name: str,
        category: Optional[str] = None,
        expected_hash: Optional[str] = None,
        **kwargs: Any,
    ) -> str:

        """Add a download to the client.

        Args:
            url: Download URL (magnet link, .torrent URL, or NZB URL)
            name: Display name for the download
            category: Category/label for organization (None = client default)
            expected_hash: Optional info_hash hint (torrents only)

        Returns:
            Client-specific download ID (hash for torrents, ID for NZBGet).

        Raises:
            Exception: If adding fails.
        """
        pass

    @abstractmethod
    def get_status(self, download_id: str) -> DownloadStatus:
        """
        Get status of a download.

        Args:
            download_id: The ID returned by add_download()

        Returns:
            Current download status.
        """
        pass

    @abstractmethod
    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        """
        Remove a download from the client.

        Args:
            download_id: The ID returned by add_download()
            delete_files: Whether to also delete downloaded files

        Returns:
            True if removal succeeded.
        """
        pass

    @abstractmethod
    def get_download_path(self, download_id: str) -> Optional[str]:
        """
        Get the path where files were downloaded.

        Args:
            download_id: The ID returned by add_download()

        Returns:
            File or directory path, or None if not available.
        """
        pass

    def find_existing(
        self, url: str, category: Optional[str] = None
    ) -> Optional[Tuple[str, DownloadStatus]]:
        """
        Check if a download for this URL already exists in the client.

        This is useful for detecting already-completed downloads so we can
        skip re-downloading and just copy the existing file.

        Args:
            url: Download URL (magnet link, .torrent URL, or NZB URL)
            category: Category to filter by (usenet clients only)

        Returns:
            Tuple of (download_id, status) if found, None if not found.
            Default implementation returns None.
        """
        return None


# Client registry: protocol -> list of client classes
_CLIENTS: Dict[str, List[Type[DownloadClient]]] = {}


def register_client(protocol: str):
    """
    Decorator to register a download client for a protocol.

    Multiple clients can be registered for the same protocol.
    The `is_configured()` method determines which one is active.

    Args:
        protocol: The protocol this client handles ("torrent" or "usenet")

    Example:
        @register_client("torrent")
        class QBittorrentClient(DownloadClient):
            ...
    """

    def decorator(cls: Type[DownloadClient]) -> Type[DownloadClient]:
        if protocol not in _CLIENTS:
            _CLIENTS[protocol] = []
        _CLIENTS[protocol].append(cls)
        return cls

    return decorator


def get_client(protocol: str) -> Optional[DownloadClient]:
    """
    Get a configured client instance for the given protocol.

    Iterates through all registered clients for the protocol and
    returns the first one that is configured.

    Args:
        protocol: "torrent" or "usenet"

    Returns:
        Configured client instance, or None if not available/configured.
    """
    if protocol not in _CLIENTS:
        return None

    for client_cls in _CLIENTS[protocol]:
        if client_cls.is_configured():
            return client_cls()

    return None


def list_configured_clients() -> List[str]:
    """
    List protocols that have configured clients.

    Returns:
        List of protocol names (e.g., ["torrent", "usenet"]).
    """
    result = []
    for protocol, client_classes in _CLIENTS.items():
        for cls in client_classes:
            if cls.is_configured():
                result.append(protocol)
                break
    return result


def get_all_clients() -> Dict[str, List[Type[DownloadClient]]]:
    """
    Get all registered client classes.

    Returns:
        Dict of protocol -> list of client classes.
    """
    return dict(_CLIENTS)


# Import client implementations to trigger registration
# These imports are at the bottom to avoid circular imports
from shelfmark.download.clients import qbittorrent  # noqa: F401, E402
from shelfmark.download.clients import nzbget  # noqa: F401, E402
from shelfmark.download.clients import sabnzbd  # noqa: F401, E402
from shelfmark.download.clients import transmission  # noqa: F401, E402
from shelfmark.download.clients import deluge  # noqa: F401, E402
from shelfmark.download.clients import rtorrent  # noqa: F401, E402
