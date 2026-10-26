"""First-run onboarding: ``/api/onboarding`` and ``/api/onboarding/skip``."""

from __future__ import annotations

import sqlite3
from importlib import import_module
from typing import TYPE_CHECKING

from flask import jsonify, request

from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask
    from werkzeug.wrappers import Response

logger = setup_logger(__name__)

_ONBOARDING_ERRORS = (ImportError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)


def register_onboarding_routes(
    app: Flask,
    login_required: Callable[..., Callable[..., Response | tuple[Response, int]]],
) -> None:
    """Register the onboarding routes.

    ``login_required`` is the app's decorator. It is also what limits these paths to an
    admin session when authentication is on, so it has to be the same one the other routes use.
    """

    @app.route("/api/onboarding", methods=["GET"])
    @login_required
    def api_onboarding_get() -> Response | tuple[Response, int]:
        """Get onboarding configuration including steps, fields, and current values.

        Returns:
            flask.Response: JSON with onboarding steps and values.

        """
        try:
            # Ensure settings are registered
            import_module("shelfmark.config.settings")
            from shelfmark.core.onboarding import get_onboarding_config

            config = get_onboarding_config()
            return jsonify(config)
        except _ONBOARDING_ERRORS as e:
            logger.error_trace(f"Onboarding get error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/onboarding", methods=["POST"])
    @login_required
    def api_onboarding_save() -> Response | tuple[Response, int]:
        """Save onboarding settings and mark as complete.

        Request Body:
            JSON object with all onboarding field values

        Returns:
            flask.Response: JSON with success/error status.

        """
        try:
            # Ensure settings are registered
            import_module("shelfmark.config.settings")
            from shelfmark.core.onboarding import save_onboarding_settings

            data = request.get_json(silent=True)
            if not data:
                return jsonify({"success": False, "message": "No data provided"}), 400

            result = save_onboarding_settings(data)

            if result["success"]:
                return jsonify(result)
            return jsonify(result), 400
        except _ONBOARDING_ERRORS as e:
            logger.error_trace(f"Onboarding save error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/onboarding/skip", methods=["POST"])
    @login_required
    def api_onboarding_skip() -> Response | tuple[Response, int]:
        """Skip onboarding and mark as complete without saving any settings.

        Returns:
            flask.Response: JSON with success status.

        """
        try:
            from shelfmark.core.onboarding import mark_onboarding_complete

            mark_onboarding_complete()
            return jsonify({"success": True, "message": "Onboarding skipped"})
        except _ONBOARDING_ERRORS as e:
            logger.error_trace(f"Onboarding skip error: {e}")
            return jsonify({"error": str(e)}), 500
