"""App setup that does not depend on any route: the log filter, the persisted secret key with
the session cookie settings, and the baseline security headers.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from pathlib import Path

    from flask import Flask
    from werkzeug.wrappers import Response

logger = setup_logger(__name__)

FLASK_SECRET_KEY_MIN_BYTES = 32


class LogNoiseFilter(logging.Filter):
    """Filter out routine status endpoint requests and WebSocket upgrade errors to reduce log noise.

    WebSocket upgrade errors are benign - Flask-SocketIO automatically falls back to polling transport.
    The error occurs because Werkzeug's built-in server doesn't fully support WebSocket upgrades.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Return whether a log record should be emitted."""
        message = record.getMessage() if hasattr(record, "getMessage") else str(record.msg)

        # Exclude GET /api/status requests (polling noise)
        if "GET /api/status" in message:
            return False

        # Exclude WebSocket upgrade errors (benign - falls back to polling)
        if "write() before start_response" in message:
            return False

        # Exclude the Error on request line that precedes WebSocket errors
        if record.levelno == logging.ERROR:
            if "Error on request:" in message:
                return False
            # Filter WebSocket-related AssertionError tracebacks
            if hasattr(record, "exc_info") and record.exc_info:
                exc_type, exc_value = record.exc_info[0], record.exc_info[1]
                if (
                    exc_type
                    and exc_type.__name__ == "AssertionError"
                    and exc_value
                    and "write() before start_response" in str(exc_value)
                ):
                    return False

        return True


def configure_app_logging(app: Flask, app_logger: logging.Logger) -> None:
    """Send Flask's and Werkzeug's logs through the app's handlers, minus the routine noise."""
    app.logger.handlers = app_logger.handlers
    app.logger.setLevel(app_logger.level)
    # Also handle Werkzeug's logger
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.handlers = app_logger.handlers
    werkzeug_logger.setLevel(app_logger.level)
    # Add filter to suppress routine status endpoint polling logs and WebSocket upgrade errors
    werkzeug_logger.addFilter(LogNoiseFilter())


def load_or_create_secret_key(config_dir: Path) -> bytes:
    """Load a persisted Flask secret key from config, or create one."""
    secret_path = config_dir / ".flask_secret"

    try:
        if secret_path.exists():
            secret_key = secret_path.read_bytes()
            if len(secret_key) >= FLASK_SECRET_KEY_MIN_BYTES:
                return secret_key
            logger.warning(
                "Invalid persisted Flask secret key at %s (length=%s). Regenerating.",
                secret_path,
                len(secret_key),
            )
    except OSError as exc:
        logger.warning("Failed to read Flask secret key at %s: %s", secret_path, exc)

    secret_key = os.urandom(64)
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        # Created private, and an existing file made private before the key goes in, so the key
        # is never in a file others can read.
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as secret_file:
            os.fchmod(secret_file.fileno(), 0o600)
            secret_file.write(secret_key)
    except OSError as exc:
        logger.warning(
            "Failed to persist Flask secret key at %s. Sessions may reset on restart: %s",
            secret_path,
            exc,
        )

    return secret_key


def configure_secret_key_and_session(
    app: Flask, config_dir: Path, *, cookie_secure: bool, cookie_name: str
) -> None:
    """Set the secret key and the session cookie settings."""
    app.config.update(
        SECRET_KEY=load_or_create_secret_key(config_dir),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_NAME=cookie_name,
        PERMANENT_SESSION_LIFETIME=604800,  # 7 days in seconds
    )


def register_security_headers(app: Flask) -> None:
    """Add the baseline security headers to every response.

    Call this at the place the hook should take in the ``after_request`` order, which runs
    the hooks in reverse.
    """

    @app.after_request
    def set_security_headers(response: Response) -> Response:
        """Add baseline security headers to every response."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' ws: wss:",
        )
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault("Cross-Origin-Embedder-Policy", "credentialless")
        return response
