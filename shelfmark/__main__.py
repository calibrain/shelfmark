"""Package entry point for `python -m shelfmark`."""

from shelfmark.main import app, socketio
from shelfmark.config.env import FLASK_HOST, FLASK_PORT
from shelfmark.core.config import config

if __name__ == "__main__":
    socketio.run(app, host=FLASK_HOST, port=FLASK_PORT, debug=config.get("DEBUG", False))
