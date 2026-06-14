"""IRC release source plugin.

Searches IRC channels for ebook and audiobook releases.
"""

import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

from shelfmark.api.websocket import ws_manager
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
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
from .dcc import DCCError, download_dcc, safe_dcc_filename
from .parser import SearchResult, extract_results_from_zip, parse_results_file

logger = setup_logger(__name__)


def _config_text(key: str) -> str:
    """Read a string config value with whitespace trimmed."""
    value = config.get(key, "")
    if value is None:
        return ""
    return str(value).strip()


def _config_port(key: str, default: int) -> int:
    """Read an IRC port value from config, accepting ints and numeric strings."""
    value = config.get(key, default)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return int(stripped)
            except ValueError:
                return default
    return default


def _config_bool(key: str, default: bool) -> bool:
    """Read a boolean config value from config."""
    value = config.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _emit_status(message: str, phase: str = "searching") -> None:
    """Emit search status to frontend via WebSocket."""
    ws_manager.broadcast_search_status(
        source="irc",
        provider="",
        book_id="",
        message=message,
        phase=phase,
    )


# Rate limiting to avoid server throttling
MIN_SEARCH_INTERVAL = 15.0
_last_search_time: float = 0

# Per-query cooldown: never re-post an identical search to the channel within this
# window, even when a user hits "Refresh" or retries a book that returned nothing.
# This stops retry loops from flooding the channel with the same message over and over.
# One day: results don't change minute-to-minute, so there's no reason to re-ask sooner.
QUERY_COOLDOWN_SECONDS = 24 * 60 * 60  # 1 day
_recent_query_times: dict[str, float] = {}


def _enforce_rate_limit() -> None:
    """Ensure minimum time between searches."""
    global _last_search_time

    elapsed = time.time() - _last_search_time
    if elapsed < MIN_SEARCH_INTERVAL:
        wait_time = MIN_SEARCH_INTERVAL - elapsed
        logger.info("Rate limiting: waiting %.1fs", wait_time)
        time.sleep(wait_time)

    _last_search_time = time.time()


def _query_cooldown_key(channel: str, query: str) -> str:
    """Build a normalized key identifying a search posted to a channel."""
    return f"{channel.casefold()}:{query.casefold().strip()}"


def _query_on_cooldown(key: str) -> bool:
    """Return True if an identical query was posted to the channel recently."""
    last = _recent_query_times.get(key)
    if last is None:
        return False
    return (time.time() - last) < QUERY_COOLDOWN_SECONDS


def _record_query_sent(key: str) -> None:
    """Record that a query was just posted to the channel (for cooldown)."""
    now = time.time()
    _recent_query_times[key] = now
    # Opportunistically prune stale entries so the dict can't grow unbounded.
    stale = [
        k for k, sent_at in _recent_query_times.items() if now - sent_at > QUERY_COOLDOWN_SECONDS
    ]
    for k in stale:
        _recent_query_times.pop(k, None)


@register_source("irc")
class IRCReleaseSource(ReleaseSource):
    """Search IRC channels for ebook and audiobook releases."""

    name = "irc"
    display_name = "IRC"
    supported_content_types: ClassVar[list[str]] = ["ebook", "audiobook"]
    can_be_default = False  # Exclude from default source options (requires deliberate selection)

    def __init__(self) -> None:
        """Initialize per-search IRC source state."""
        # Track online servers from most recent search
        self._online_servers: set[str] | None = None

    def is_available(self) -> bool:
        """Check if IRC is configured (server, channel, nick, and search bot are set).

        The search bot is required: without it we would post bare queries straight
        to the channel, which reads as spam and gets the nick banned.
        """
        server = _config_text("IRC_SERVER")
        channel = _config_text("IRC_CHANNEL")
        nick = _config_text("IRC_NICK")
        search_bot = _config_text("IRC_SEARCH_BOT")
        return bool(server and channel and nick and search_bot)

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
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search IRC for books matching metadata.

        The expand_search parameter is repurposed for IRC as a "refresh" flag.
        When True, it bypasses the cache and forces a fresh search.
        """
        from .cache import cache_results, get_cached_results

        if not self.is_available():
            logger.debug("IRC source is disabled, skipping search")
            return []

        # Check cache first (unless expand_search/refresh is requested)
        if not expand_search:
            cached = get_cached_results(book.provider, book.provider_id, content_type=content_type)
            if cached:
                _emit_status("Using cached results", phase="complete")
                self._online_servers = set(cached.get("online_servers", []))
                return cached["releases"]

        # Build search query
        query = plan.primary_query or self._build_query(book)
        if not query:
            logger.warning("No search query could be built")
            return []

        # Get IRC settings
        server = _config_text("IRC_SERVER")
        port = _config_port("IRC_PORT", 6697)
        use_tls = _config_bool("IRC_USE_TLS", True)
        channel = _config_text("IRC_CHANNEL")
        nick = _config_text("IRC_NICK")
        search_bot = _config_text("IRC_SEARCH_BOT")

        # Never post an unaddressed query to the channel. A bare book title looks like
        # spam to everyone else in the channel and gets the nick banned. Searches must
        # be addressed to a search bot ("@<bot> <query>").
        if not search_bot:
            logger.warning(
                "IRC search bot not configured; refusing to post unaddressed query to channel"
            )
            _emit_status("IRC search bot not configured", phase="error")
            return []

        # Don't re-post an identical query to the channel within the cooldown window,
        # even on refresh. This is what stops a frustrated user (or a retry loop) from
        # spamming the same title over and over. Fall back to whatever is cached.
        cooldown_key = _query_cooldown_key(channel, query)
        if _query_on_cooldown(cooldown_key):
            logger.info("IRC query on cooldown, not re-posting to channel: %s", query)
            _emit_status("Search sent recently — showing latest results", phase="complete")
            cached = get_cached_results(book.provider, book.provider_id, content_type=content_type)
            if cached:
                self._online_servers = set(cached.get("online_servers", []))
                return cached["releases"]
            return []

        logger.info("IRC search: %s", query)

        # Enforce rate limit
        _enforce_rate_limit()

        client = None
        try:
            # Get or reuse IRC connection
            _emit_status(f"Connecting to {server}...", phase="connecting")
            client = connection_manager.get_connection(
                server=server,
                port=port,
                nick=nick,
                use_tls=use_tls,
                channel=channel,
            )

            # Capture online servers (elevated users in channel)
            self._online_servers = client.online_servers

            # Send search request (always addressed to the search bot, never bare)
            search_msg = f"@{search_bot} {query}"
            client.send_message(f"#{channel}", search_msg)
            _record_query_sent(cooldown_key)

            # Wait for results DCC - this is the long wait
            _emit_status(f"Connected to #{channel} - Waiting for results...", phase="searching")
            wait_kwargs = {"expected_senders": {search_bot}} if search_bot else {}
            offer = client.wait_for_dcc(timeout=60.0, result_type=True, **wait_kwargs)
            if not offer:
                logger.info("No search results received")
                _emit_status("No results found", phase="complete")
                # Release connection for reuse (don't close it)
                connection_manager.release_connection(client)
                # Cache empty result to avoid repeated failed searches
                cache_results(
                    book.provider,
                    book.provider_id,
                    book.title,
                    [],
                    content_type=content_type,
                    online_servers=list(self._online_servers) if self._online_servers else None,
                )
                return []

            # Download results file
            _emit_status(f"Connected to #{channel} - Downloading results...", phase="downloading")
            with tempfile.TemporaryDirectory() as tmpdir:
                result_path = Path(tmpdir) / safe_dcc_filename(offer.filename)
                download_dcc(offer, result_path, timeout=30.0)

                # Parse results
                if result_path.suffix.lower() == ".zip":
                    content = extract_results_from_zip(result_path)
                else:
                    content = result_path.read_text(errors="replace")

            # Release connection for reuse (don't close it)
            connection_manager.release_connection(client)

            # Convert to Release objects
            results = parse_results_file(content, content_type=content_type)
            releases = self._convert_to_releases(results, content_type=content_type)

            # Cache results
            cache_results(
                book.provider,
                book.provider_id,
                book.title,
                releases,
                content_type=content_type,
                online_servers=list(self._online_servers) if self._online_servers else None,
            )

        except DCCError as e:
            logger.exception("DCC error during search")
            _emit_status(f"DCC error: {e}", phase="error")
            if client:
                connection_manager.close_connection(client)
            return []
        except Exception as e:
            logger.exception("IRC search failed")
            _emit_status(f"Search failed: {e}", phase="error")
            if client:
                connection_manager.close_connection(client)
            return []

        else:
            return releases

    def _build_query(self, book: BookMetadata) -> str:
        """Build search query from book metadata."""
        parts = []

        if book.search_title or book.title:
            parts.append(book.search_title or book.title)

        if book.search_author:
            parts.append(book.search_author)
        elif book.authors:
            # Use first author
            author = book.authors[0] if isinstance(book.authors, list) else book.authors
            parts.append(author)

        return " ".join(parts)

    # Format priority for sorting (lower = higher priority)
    EBOOK_FORMAT_PRIORITY: ClassVar[dict[str, int]] = {
        "epub": 0,
        "mobi": 1,
        "azw3": 2,
        "azw": 3,
        "fb2": 4,
        "djvu": 5,
        "pdf": 6,
        "cbr": 7,
        "cbz": 8,
        "doc": 9,
        "docx": 10,
        "rtf": 11,
        "txt": 12,
        "html": 13,
        "htm": 14,
        "rar": 15,
        "zip": 16,
    }

    AUDIOBOOK_FORMAT_PRIORITY: ClassVar[dict[str, int]] = {
        "m4b": 0,
        "mp3": 1,
        "m4a": 2,
        "flac": 3,
        "opus": 4,
        "ogg": 5,
        "aac": 6,
        "wav": 7,
        "wma": 8,
        "rar": 9,
        "zip": 10,
    }

    def _convert_to_releases(
        self,
        results: list[SearchResult],
        content_type: str = "ebook",
    ) -> list[Release]:
        """Convert parsed results to Release objects, sorted by online/format/server."""
        releases = []
        online_servers = self._online_servers or set()
        format_priority_map = (
            self.AUDIOBOOK_FORMAT_PRIORITY
            if content_type == "audiobook"
            else self.EBOOK_FORMAT_PRIORITY
        )

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
                content_type=content_type,
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
            format_priority = format_priority_map.get(fmt, 99)
            return (
                0 if is_online else 1,  # Online first
                format_priority,  # Then by format
                server.lower(),  # Then alphabetically by server
            )

        releases.sort(key=sort_key)

        return releases

    @staticmethod
    def _parse_size(size_str: str) -> int | None:
        """Parse human-readable size (e.g., '1.2MB', '500K') to bytes."""
        if not size_str:
            return None

        size_str = size_str.strip().upper()

        # Map suffixes to multipliers (check longer suffixes first)
        multipliers = [
            ("GB", 1024 * 1024 * 1024),
            ("MB", 1024 * 1024),
            ("KB", 1024),
            ("G", 1024 * 1024 * 1024),
            ("M", 1024 * 1024),
            ("K", 1024),
            ("B", 1),
        ]

        for suffix, mult in multipliers:
            if size_str.endswith(suffix):
                try:
                    num = float(size_str[: -len(suffix)].strip())
                    return int(num * mult)
                except ValueError:
                    return None

        # Try parsing as plain number (bytes)
        try:
            return int(float(size_str))
        except ValueError:
            return None
