"""IRC connection manager.

Maintains persistent IRC connections to avoid reconnecting between search and download.
"""

import threading
import time
from typing import Optional

from shelfmark.core.logger import setup_logger

from .client import IRCClient

logger = setup_logger(__name__)

# How long to keep an idle connection before closing it
IDLE_TIMEOUT = 300.0  # 5 minutes


class IRCConnectionManager:
    """Manages persistent IRC connections.

    Keeps connections alive between search and download operations to avoid
    the overhead of reconnecting. Connections are automatically closed after
    being idle for IDLE_TIMEOUT seconds.
    """

    _instance: Optional["IRCConnectionManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "IRCConnectionManager":
        """Singleton pattern - only one connection manager."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._connections: dict[str, IRCClient] = {}
        self._last_used: dict[str, float] = {}
        self._channels: dict[str, str] = {}  # connection_key -> joined channel
        self._conn_lock = threading.Lock()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._running = True
        self._initialized = True

        # Start background cleanup thread
        self._start_cleanup_thread()

    def _connection_key(self, server: str, port: int, nick: str) -> str:
        """Generate a unique key for a connection."""
        return f"{server}:{port}:{nick}"

    def _start_cleanup_thread(self) -> None:
        """Start background thread to clean up idle connections."""
        def cleanup_loop():
            while self._running:
                time.sleep(30)  # Check every 30 seconds
                self._cleanup_idle_connections()

        self._cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def _cleanup_idle_connections(self) -> None:
        """Close connections that have been idle too long."""
        now = time.time()
        to_remove = []

        with self._conn_lock:
            for key, last_used in list(self._last_used.items()):
                if now - last_used > IDLE_TIMEOUT:
                    to_remove.append(key)

            for key in to_remove:
                client = self._connections.pop(key, None)
                self._last_used.pop(key, None)
                self._channels.pop(key, None)

                if client:
                    logger.info(f"Closing idle IRC connection: {key}")
                    try:
                        client.disconnect()
                    except Exception as e:
                        logger.debug(f"Error closing idle connection: {e}")

    def get_connection(
        self,
        server: str,
        port: int,
        nick: str,
        use_tls: bool,
        channel: str,
    ) -> IRCClient:
        """Get or create an IRC connection.

        If an existing connection to the same server/port/nick exists and is
        still connected, it will be reused. Otherwise, a new connection is created.

        Args:
            server: IRC server hostname
            port: IRC server port
            nick: IRC nickname
            use_tls: Whether to use TLS
            channel: Channel to join (without # prefix)

        Returns:
            Connected IRCClient instance that has joined the channel
        """
        key = self._connection_key(server, port, nick)

        with self._conn_lock:
            # Check for existing connection
            existing = self._connections.get(key)

            if existing and existing.is_connected:
                logger.info(f"Reusing existing IRC connection to {server}")
                self._last_used[key] = time.time()

                # Check if we need to join a different channel
                current_channel = self._channels.get(key)
                if current_channel != channel:
                    logger.debug(f"Joining channel #{channel}")
                    existing.join_channel(channel)
                    self._channels[key] = channel

                return existing

            # Clean up dead connection if it exists
            if existing:
                logger.debug(f"Removing dead connection: {key}")
                self._connections.pop(key, None)
                self._last_used.pop(key, None)
                self._channels.pop(key, None)
                try:
                    existing.disconnect()
                except Exception:
                    pass

            # Create new connection
            logger.info(f"Creating new IRC connection to {server}:{port}")
            client = IRCClient(nick, server, port, use_tls=use_tls)
            client.connect()
            client.join_channel(channel)

            # Store connection
            self._connections[key] = client
            self._last_used[key] = time.time()
            self._channels[key] = channel

            return client

    def release_connection(self, client: IRCClient) -> None:
        """Mark a connection as available for reuse.

        This updates the last-used timestamp to prevent premature cleanup.
        The connection stays open for potential reuse.
        """
        key = self._connection_key(client.server, client.port, client.nick)

        with self._conn_lock:
            if key in self._connections:
                self._last_used[key] = time.time()
                logger.debug(f"Released IRC connection for reuse: {key}")

    def close_connection(self, client: IRCClient) -> None:
        """Explicitly close a connection (e.g., on error).

        Use this when you want to force-close a connection rather than
        releasing it for reuse.
        """
        key = self._connection_key(client.server, client.port, client.nick)

        with self._conn_lock:
            self._connections.pop(key, None)
            self._last_used.pop(key, None)
            self._channels.pop(key, None)

        try:
            client.disconnect()
        except Exception as e:
            logger.debug(f"Error closing connection: {e}")

        logger.debug(f"Closed IRC connection: {key}")

    def close_all(self) -> None:
        """Close all connections (for shutdown)."""
        with self._conn_lock:
            for key, client in list(self._connections.items()):
                try:
                    client.disconnect()
                except Exception as e:
                    logger.debug(f"Error closing connection {key}: {e}")

            self._connections.clear()
            self._last_used.clear()
            self._channels.clear()

        logger.info("Closed all IRC connections")


# Global singleton instance
connection_manager = IRCConnectionManager()
