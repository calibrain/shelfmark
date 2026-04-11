"""Package entry point for `python -m shelfmark`."""

from shelfmark.config.env import FLASK_HOST, FLASK_PORT
from shelfmark.core.config import config
from shelfmark.main import app, socketio


def _resolve_debug_flag(value: object) -> bool:
    """Normalize DEBUG config values for Flask-SocketIO startup."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


if __name__ == "__main__":
    socketio.run(
        app,
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=_resolve_debug_flag(config.get("DEBUG", False)),
    )
