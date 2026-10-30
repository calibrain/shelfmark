"""Settings: ``/api/settings`` and its per-tab read, update and action routes."""

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

_SETTINGS_ERRORS = (ImportError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)


def register_settings_routes(
    app: Flask,
    login_required: Callable[..., Callable[..., Response | tuple[Response, int]]],
) -> None:
    """Register the settings routes.

    ``login_required`` is the app's decorator. It is also what limits these paths to an
    admin session when authentication is on, so it has to be the same one the other routes use.
    """

    @app.route("/api/settings", methods=["GET"])
    @login_required
    def api_settings_get_all() -> Response | tuple[Response, int]:
        """Get all settings tabs with their fields and current values.

        Returns:
            flask.Response: JSON with all settings tabs.

        """
        try:
            import_module("shelfmark.config.notifications_settings")
            import_module("shelfmark.config.security")

            # Ensure settings are registered by importing settings modules
            # This triggers the @register_settings decorators
            import_module("shelfmark.config.settings")
            import_module("shelfmark.config.users_settings")
            from shelfmark.core.settings_registry import serialize_all_settings

            data = serialize_all_settings(include_values=True)
            return jsonify(data)
        except _SETTINGS_ERRORS as e:
            logger.error_trace(f"Settings get error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/<tab_name>", methods=["GET"])
    @login_required
    def api_settings_get_tab(tab_name: str) -> Response | tuple[Response, int]:
        """Get settings for a specific tab.

        Path Parameters:
            tab_name (str): Settings tab name (e.g., "general", "hardcover")

        Returns:
            flask.Response: JSON with tab settings and values.

        """
        try:
            import_module("shelfmark.config.notifications_settings")
            import_module("shelfmark.config.security")

            # Ensure settings are registered
            import_module("shelfmark.config.settings")
            import_module("shelfmark.config.users_settings")
            from shelfmark.core.settings_registry import (
                get_settings_tab,
                serialize_tab,
            )

            tab = get_settings_tab(tab_name)
            if not tab:
                return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

            return jsonify(serialize_tab(tab, include_values=True))
        except _SETTINGS_ERRORS as e:
            logger.error_trace(f"Settings get tab error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/<tab_name>", methods=["PUT"])
    @login_required
    def api_settings_update_tab(tab_name: str) -> Response | tuple[Response, int]:
        """Update settings for a specific tab.

        Path Parameters:
            tab_name (str): Settings tab name

        Request Body:
            JSON object with setting keys and values to update.

        Returns:
            flask.Response: JSON with update result.

        """
        try:
            import_module("shelfmark.config.notifications_settings")
            import_module("shelfmark.config.security")

            # Ensure settings are registered
            import_module("shelfmark.config.settings")
            import_module("shelfmark.config.users_settings")
            from shelfmark.core.settings_registry import (
                get_settings_tab,
                update_settings,
            )

            tab = get_settings_tab(tab_name)
            if not tab:
                return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

            values = request.get_json(silent=True)
            if values is None or not isinstance(values, dict):
                return jsonify({"error": "Request body must be a JSON object"}), 400

            # If no values to update, return success with empty updated list
            if not values:
                return jsonify({"success": True, "message": "No changes to save", "updated": []})

            result = update_settings(tab_name, values)

            if result["success"]:
                return jsonify(result)
            return jsonify(result), 400
        except _SETTINGS_ERRORS as e:
            logger.error_trace(f"Settings update error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/settings/<tab_name>/action/<action_key>", methods=["POST"])
    @login_required
    def api_settings_execute_action(
        tab_name: str, action_key: str
    ) -> Response | tuple[Response, int]:
        """Execute a settings action (e.g., test connection).

        Path Parameters:
            tab_name (str): Settings tab name
            action_key (str): Action key to execute

        Request Body (optional):
            JSON object with current form values (unsaved)

        Returns:
            flask.Response: JSON with action result.

        """
        try:
            import_module("shelfmark.config.notifications_settings")
            import_module("shelfmark.config.security")

            # Ensure settings are registered
            import_module("shelfmark.config.settings")
            import_module("shelfmark.config.users_settings")
            from shelfmark.core.settings_registry import execute_action

            # Get current form values if provided (for testing with unsaved values)
            current_values = request.get_json(silent=True) or {}

            result = execute_action(tab_name, action_key, current_values)

            if result["success"]:
                return jsonify(result)
            return jsonify(result), 400
        except _SETTINGS_ERRORS as e:
            logger.error_trace(f"Settings action error: {e}")
            return jsonify({"error": str(e)}), 500
