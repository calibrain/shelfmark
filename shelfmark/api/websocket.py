"""WebSocket manager for real-time status updates."""

import logging
import threading
from typing import Optional, Dict, Any, Callable, List

from flask_socketio import SocketIO, join_room, leave_room

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and broadcasts."""

    def __init__(self):
        self.socketio: Optional[SocketIO] = None
        self._enabled = False
        self._connection_count = 0
        self._connection_lock = threading.Lock()
        self._on_first_connect_callbacks: List[Callable[[], None]] = []
        self._on_all_disconnect_callbacks: List[Callable[[], None]] = []
        self._needs_rewarm = False  # Flag to trigger warmup callbacks on next connect
        self._user_rooms: Dict[str, int] = {}  # room_name -> ref count
        self._rooms_lock = threading.Lock()
        self._queue_status_fn: Optional[Callable] = None  # Reference to queue_status()

    def init_app(self, app, socketio: SocketIO):
        """Initialize the WebSocket manager with Flask-SocketIO instance."""
        self.socketio = socketio
        self._enabled = True
        logger.info("WebSocket manager initialized")

    def register_on_first_connect(self, callback: Callable[[], None]):
        """Register a callback for when the first client connects."""
        self._on_first_connect_callbacks.append(callback)
        logger.debug(f"Registered on_first_connect callback: {callback.__name__}")

    def register_on_all_disconnect(self, callback: Callable[[], None]):
        """Register a callback for when all clients disconnect."""
        self._on_all_disconnect_callbacks.append(callback)
        logger.debug(f"Registered on_all_disconnect callback: {callback.__name__}")

    def request_warmup_on_next_connect(self):
        """Request warmup callbacks on the next client connect (e.g., after idle shutdown)."""
        with self._connection_lock:
            self._needs_rewarm = True
            logger.debug("Warmup requested for next client connect")

    def client_connected(self):
        """Track a new client connection. Call this from the connect event handler."""
        with self._connection_lock:
            was_zero = self._connection_count == 0
            needs_rewarm = self._needs_rewarm
            self._connection_count += 1
            current_count = self._connection_count
            # Clear rewarm flag if we're going to trigger warmup
            if was_zero or needs_rewarm:
                self._needs_rewarm = False

        logger.debug(f"Client connected. Active connections: {current_count}")

        # Trigger warmup callbacks if this is the first connection OR if rewarm was requested
        # (rewarm is requested when bypasser shuts down due to idle while clients are connected)
        if was_zero or needs_rewarm:
            reason = "First client connected" if was_zero else "Rewarm requested after idle shutdown"
            logger.info(f"{reason}, triggering warmup callbacks...")
            for callback in self._on_first_connect_callbacks:
                try:
                    # Run callbacks in a separate thread to not block the connection
                    thread = threading.Thread(target=callback, daemon=True)
                    thread.start()
                except Exception as e:
                    logger.error(f"Error in on_first_connect callback {callback.__name__}: {e}")

    def client_disconnected(self):
        """Track a client disconnection. Call this from the disconnect event handler."""
        with self._connection_lock:
            self._connection_count = max(0, self._connection_count - 1)
            current_count = self._connection_count
            is_now_zero = current_count == 0

        logger.debug(f"Client disconnected. Active connections: {current_count}")

        # If all clients have disconnected, trigger cleanup callbacks
        if is_now_zero:
            logger.info("All clients disconnected, triggering disconnect callbacks...")
            for callback in self._on_all_disconnect_callbacks:
                try:
                    callback()
                except Exception as e:
                    logger.error(f"Error in on_all_disconnect callback {callback.__name__}: {e}")

    def get_connection_count(self) -> int:
        """Get the current number of active WebSocket connections."""
        with self._connection_lock:
            return self._connection_count

    def has_active_connections(self) -> bool:
        """Check if there are any active WebSocket connections."""
        return self.get_connection_count() > 0

    def is_enabled(self) -> bool:
        """Check if WebSocket is enabled and ready."""
        return self._enabled and self.socketio is not None

    def set_queue_status_fn(self, fn: Callable):
        """Set the queue_status function reference for per-room filtering."""
        self._queue_status_fn = fn

    def join_user_room(self, sid: str, is_admin: bool, db_user_id: Optional[int] = None):
        """Join the appropriate room based on user role."""
        if is_admin or db_user_id is None:
            join_room("admins", sid=sid)
        else:
            room = f"user_{db_user_id}"
            join_room(room, sid=sid)
            with self._rooms_lock:
                self._user_rooms[room] = self._user_rooms.get(room, 0) + 1

    def leave_user_room(self, sid: str, is_admin: bool, db_user_id: Optional[int] = None):
        """Leave the user's room on disconnect."""
        if is_admin or db_user_id is None:
            leave_room("admins", sid=sid)
        else:
            room = f"user_{db_user_id}"
            leave_room(room, sid=sid)
            with self._rooms_lock:
                count = self._user_rooms.get(room, 1) - 1
                if count <= 0:
                    self._user_rooms.pop(room, None)
                else:
                    self._user_rooms[room] = count

    def broadcast_status_update(self, status_data: Dict[str, Any]):
        """Broadcast status update to all connected clients, filtered by user room."""
        if not self.is_enabled():
            return

        try:
            # Admins (and no-auth users) get full status
            self.socketio.emit('status_update', status_data, to="admins")

            # Each user room gets filtered status
            with self._rooms_lock:
                active_rooms = list(self._user_rooms.keys())

            if active_rooms and self._queue_status_fn:
                for room in active_rooms:
                    try:
                        # Extract user_id from room name "user_123"
                        uid = int(room.split("_", 1)[1])
                        filtered = self._queue_status_fn(user_id=uid)
                        self.socketio.emit('status_update', filtered, to=room)
                    except Exception:
                        pass

            logger.debug("Broadcasted status update to all rooms")
        except Exception as e:
            logger.error(f"Error broadcasting status update: {e}")

    def broadcast_download_progress(self, book_id: str, progress: float, status: str, user_id: Optional[int] = None):
        """Broadcast download progress update for a specific book."""
        if not self.is_enabled():
            return

        try:
            data = {
                'book_id': book_id,
                'progress': progress,
                'status': status
            }
            # Admins always see all progress
            self.socketio.emit('download_progress', data, to="admins")
            # If task belongs to a specific user, send to their room too
            if user_id is not None:
                room = f"user_{user_id}"
                with self._rooms_lock:
                    if room in self._user_rooms:
                        self.socketio.emit('download_progress', data, to=room)
            logger.debug(f"Broadcasted progress for book {book_id}: {progress}%")
        except Exception as e:
            logger.error(f"Error broadcasting download progress: {e}")

    def broadcast_notification(self, message: str, notification_type: str = 'info'):
        """Broadcast a notification message to all clients."""
        if not self.is_enabled():
            return

        try:
            data = {
                'message': message,
                'type': notification_type
            }
            # When calling socketio.emit() outside event handlers, it broadcasts by default
            self.socketio.emit('notification', data)
            logger.debug(f"Broadcasted notification: {message}")
        except Exception as e:
            logger.error(f"Error broadcasting notification: {e}")

    def broadcast_search_status(
        self,
        source: str,
        provider: str,
        book_id: str,
        message: str,
        phase: str = 'searching'
    ):
        """Broadcast search status update for a release source search."""
        if not self.is_enabled():
            return

        try:
            data = {
                'source': source,
                'provider': provider,
                'book_id': book_id,
                'message': message,
                'phase': phase,
            }
            self.socketio.emit('search_status', data)
        except Exception as e:
            logger.error(f"Error broadcasting search status: {e}")


# Global WebSocket manager instance
ws_manager = WebSocketManager()
