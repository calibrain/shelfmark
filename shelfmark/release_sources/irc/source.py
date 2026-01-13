"""IRC release source plugin.

Searches IRC ebook channels for book releases.
"""

import tempfile
import time
from pathlib import Path
from typing import List, Optional

from shelfmark.api.websocket import ws_manager
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import (
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    LeadingCellConfig,
    LeadingCellType,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    SourceActionButton,
    register_source,
)

from .connection_manager import connection_manager
from .dcc import DCCError, download_dcc
from .parser import SearchResult, extract_results_from_zip, parse_results_file

logger = setup_logger(__name__)


def _emit_status(message: str, phase: str = 'searching') -> None:
    """Emit search status to frontend via WebSocket."""
    ws_manager.broadcast_search_status(
        source='irc',
        provider='',
        book_id='',
        message=message,
        phase=phase,
    )

# Rate limiting to avoid server throttling
MIN_SEARCH_INTERVAL = 15.0
_last_search_time: float = 0


def _enforce_rate_limit() -> None:
    """Ensure minimum time between searches."""
    global _last_search_time

    elapsed = time.time() - _last_search_time
    if elapsed < MIN_SEARCH_INTERVAL:
        wait_time = MIN_SEARCH_INTERVAL - elapsed
        logger.info(f"Rate limiting: waiting {wait_time:.1f}s")
        time.sleep(wait_time)

    _last_search_time = time.time()


@register_source("irc")
class IRCReleaseSource(ReleaseSource):
    """Search IRC channels for book releases."""

    name = "irc"
    display_name = "IRC"
    supported_content_types = ["ebook"]  # IRC only supports ebooks
    can_be_default = False  # Exclude from default source options (requires deliberate selection)

    def __init__(self):
        # Track online servers from most recent search
        self._online_servers: Optional[set[str]] = None

    @classmethod
    def is_available(cls) -> bool:
        """Check if IRC is configured (server, channel, and nick are set)."""
        server = config.get("IRC_SERVER", "")
        channel = config.get("IRC_CHANNEL", "")
        nick = config.get("IRC_NICK", "")
        return bool(server and channel and nick)

    def get_column_config(self) -> ReleaseColumnConfig:
        """Configure UI columns for IRC results."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.server",
                    label="Server",
                    render_type=ColumnRenderType.TEXT,
                    width="100px",
                    sortable=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    color_hint=ColumnColorHint(type="map", value="format"),
                    width="70px",
                    uppercase=True,
                    sortable=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.TEXT,
                    width="70px",
                    sortable=True,
                    sort_key="size_bytes",
                ),
            ],
            grid_template="minmax(0,2fr) 100px 70px 70px",
            leading_cell=LeadingCellConfig(type=LeadingCellType.NONE),
            online_servers=list(self._online_servers) if self._online_servers else None,
            cache_ttl_seconds=1800,  # 30 minutes - IRC searches are slow, cache longer
            supported_filters=["format"],  # IRC has no language metadata
            action_button=SourceActionButton(label="Refresh search"),
        )

    def search(
        self,
        book: BookMetadata,
        expand_search: bool = False,
        languages: Optional[List[str]] = None,
        content_type: str = "ebook"
    ) -> List[Release]:
        """Search IRC for books matching metadata.

        The expand_search parameter is repurposed for IRC as a "refresh" flag.
        When True, it bypasses the cache and forces a fresh search.
        """
        from .cache import get_cached_results, cache_results

        if not self.is_available():
            logger.debug("IRC source is disabled, skipping search")
            return []

        # Check cache first (unless expand_search/refresh is requested)
        if not expand_search:
            cached = get_cached_results(book.provider, book.provider_id)
            if cached:
                _emit_status("Using cached results", phase='complete')
                self._online_servers = set(cached.get("online_servers", []))
                return cached["releases"]

        # Build search query
        query = self._build_query(book)
        if not query:
            logger.warning("No search query could be built")
            return []

        logger.info(f"IRC search: {query}")

        # Enforce rate limit
        _enforce_rate_limit()

        # Get IRC settings
        server = config.get("IRC_SERVER", "")
        port = config.get("IRC_PORT", 6697)
        use_tls = config.get("IRC_USE_TLS", True)
        channel = config.get("IRC_CHANNEL", "")
        nick = config.get("IRC_NICK", "")
        search_bot = config.get("IRC_SEARCH_BOT", "")

        client = None
        try:
            # Get or reuse IRC connection
            _emit_status(f"Connecting to {server}...", phase='connecting')
            client = connection_manager.get_connection(
                server=server,
                port=port,
                nick=nick,
                use_tls=use_tls,
                channel=channel,
            )

            # Capture online servers (elevated users in channel)
            self._online_servers = client.online_servers

            # Send search request
            search_msg = f"@{search_bot} {query}" if search_bot else query
            client.send_message(f"#{channel}", search_msg)

            # Wait for results DCC - this is the long wait
            _emit_status(f"Connected to #{channel} - Waiting for results...", phase='searching')
            offer = client.wait_for_dcc(timeout=60.0, result_type=True)
            if not offer:
                logger.info("No search results received")
                _emit_status("No results found", phase='complete')
                # Release connection for reuse (don't close it)
                connection_manager.release_connection(client)
                # Cache empty result to avoid repeated failed searches
                cache_results(
                    book.provider,
                    book.provider_id,
                    book.title,
                    [],
                    list(self._online_servers) if self._online_servers else None
                )
                return []

            # Download results file
            _emit_status(f"Connected to #{channel} - Downloading results...", phase='downloading')
            with tempfile.TemporaryDirectory() as tmpdir:
                result_path = Path(tmpdir) / offer.filename
                download_dcc(offer, result_path, timeout=30.0)

                # Parse results
                if result_path.suffix.lower() == '.zip':
                    content = extract_results_from_zip(result_path)
                else:
                    content = result_path.read_text(errors='replace')

            # Release connection for reuse (don't close it)
            connection_manager.release_connection(client)

            # Convert to Release objects
            results = parse_results_file(content)
            releases = self._convert_to_releases(results)

            # Cache results
            cache_results(
                book.provider,
                book.provider_id,
                book.title,
                releases,
                list(self._online_servers) if self._online_servers else None
            )

            return releases

        except DCCError as e:
            logger.error(f"DCC error during search: {e}")
            _emit_status(f"DCC error: {e}", phase='error')
            if client:
                connection_manager.close_connection(client)
            return []
        except Exception as e:
            logger.error(f"IRC search failed: {e}")
            _emit_status(f"Search failed: {e}", phase='error')
            if client:
                connection_manager.close_connection(client)
            return []

    def _build_query(self, book: BookMetadata) -> str:
        """Build search query from book metadata."""
        parts = []

        if book.title:
            parts.append(book.title)

        if book.authors:
            # Use first author
            author = book.authors[0] if isinstance(book.authors, list) else book.authors
            parts.append(author)

        return ' '.join(parts)

    # Format priority for sorting (lower = higher priority)
    FORMAT_PRIORITY = {
        'epub': 0,
        'mobi': 1,
        'azw3': 2,
        'azw': 3,
        'fb2': 4,
        'djvu': 5,
        'pdf': 6,
        'cbr': 7,
        'cbz': 8,
        'doc': 9,
        'docx': 10,
        'rtf': 11,
        'txt': 12,
        'html': 13,
        'htm': 14,
        'rar': 15,
        'zip': 16,
    }

    def _convert_to_releases(self, results: List[SearchResult]) -> List[Release]:
        """Convert parsed results to Release objects, sorted by online/format/server."""
        releases = []
        online_servers = self._online_servers if self._online_servers else set()

        for result in results:
            release = Release(
                source="irc",
                source_id=result.download_request,  # Full line for download
                title=result.title,
                format=result.format,
                size=result.size,
                size_bytes=self._parse_size(result.size) if result.size else None,
                protocol=ReleaseProtocol.DCC,
                indexer=f"IRC:{result.server}",
                extra={
                    "server": result.server,
                    "author": result.author,
                    "full_line": result.full_line,
                },
            )
            releases.append(release)

        # Tiered sort: online first, then by format priority, then by server name
        def sort_key(release: Release) -> tuple:
            server = release.extra.get("server", "")
            is_online = server in online_servers
            fmt = release.format.lower() if release.format else ""
            format_priority = self.FORMAT_PRIORITY.get(fmt, 99)
            return (
                0 if is_online else 1,  # Online first
                format_priority,         # Then by format
                server.lower(),          # Then alphabetically by server
            )

        releases.sort(key=sort_key)

        return releases

    @staticmethod
    def _parse_size(size_str: str) -> Optional[int]:
        """Parse human-readable size (e.g., '1.2MB', '500K') to bytes."""
        if not size_str:
            return None

        size_str = size_str.strip().upper()

        # Map suffixes to multipliers (check longer suffixes first)
        multipliers = [
            ('GB', 1024 * 1024 * 1024),
            ('MB', 1024 * 1024),
            ('KB', 1024),
            ('G', 1024 * 1024 * 1024),
            ('M', 1024 * 1024),
            ('K', 1024),
            ('B', 1),
        ]

        for suffix, mult in multipliers:
            if size_str.endswith(suffix):
                try:
                    num = float(size_str[:-len(suffix)].strip())
                    return int(num * mult)
                except ValueError:
                    return None

        # Try parsing as plain number (bytes)
        try:
            return int(float(size_str))
        except ValueError:
            return None
