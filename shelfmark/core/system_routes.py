"""System routes: the config and health endpoints, the debug tools, the frontend files and the SPA fallback."""

from __future__ import annotations

import os
import sqlite3
import subprocess
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, NoReturn

from flask import jsonify, send_file, send_from_directory, session
from werkzeug.wrappers import Response

from shelfmark.config.env import BUILD_VERSION, RELEASE_VERSION
from shelfmark.config.settings import _SUPPORTED_BOOK_LANGUAGE
from shelfmark.core import search_deadline
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.download import orchestrator as backend

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask

logger = setup_logger(__name__)

_OPERATIONAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)
_IMPORT_OPERATIONAL_ERRORS = (ImportError, *_OPERATIONAL_ERRORS)

_BASE_TAG = '<base href="/" data-shelfmark-base />'


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


class DebugHooks(NamedTuple):
    """What the DEBUG-only routes need from the app: stop the GUI, then run the debug script.

    ``run_script`` waits a second for the GUI to stop before it starts the script.
    """

    stop_gui: Callable[[], None]
    run_script: Callable[[], subprocess.CompletedProcess[str]]


def register_system_routes(
    app: Flask,
    login_required: Callable[..., Callable[..., Response | tuple[Response, int]]],
    *,
    resolve_auth_mode: Callable[[], str],
    resolve_db_user_id: Callable[[], int | None],
    get_base_path: Callable[[], str],
    get_frontend_dist: Callable[[], Path],
    debug_hooks: DebugHooks | None,
) -> None:
    """Register the system routes, the frontend files and the catch-all for client-side routes.

    ``login_required`` is the app's decorator. ``resolve_auth_mode``, ``resolve_db_user_id``,
    ``get_base_path`` and ``get_frontend_dist`` are called per request so a runtime patch of any of them still takes
    effect. ``/api/debug`` and ``/api/restart`` are registered only when ``debug_hooks`` is given.
    """

    def admin_required(
        f: Callable[..., Response | tuple[Response, int]],
    ) -> Callable[..., Response | tuple[Response, int]]:
        """Require an authenticated admin session for a Flask route.

        Builds on `login_required`, so auth mode "none" (no users, the single
        operator owns the instance) still has access.
        """

        @login_required
        @wraps(f)
        def decorated_function(*args: object, **kwargs: object) -> Response | tuple[Response, int]:
            if resolve_auth_mode() != "none" and not session.get("is_admin", False):
                return jsonify({"error": "Admin access required"}), 403
            return f(*args, **kwargs)

        return decorated_function

    def _base_href() -> str:
        base_path = get_base_path()
        if not base_path:
            return "/"
        return f"{base_path}/"

    def _serve_index_html() -> Response:
        """Serve index.html with an adjusted base tag for subpath deployments."""
        frontend_dist = get_frontend_dist()
        index_path = frontend_dist / "index.html"
        try:
            with index_path.open(encoding="utf-8") as handle:
                html = handle.read()
        except OSError:
            return send_from_directory(frontend_dist, "index.html")

        if get_base_path() and _BASE_TAG in html:
            html = html.replace(_BASE_TAG, f'<base href="{_base_href()}" data-shelfmark-base />', 1)

        return Response(html, mimetype="text/html")

    # Serve frontend static files
    @app.route("/assets/<path:filename>")
    def serve_frontend_assets(filename: str) -> Response:
        """Serve static assets from the built frontend."""
        return send_from_directory(get_frontend_dist() / "assets", filename)

    @app.route("/")
    def index() -> Response:
        """Serve the React frontend application.

        Authentication is handled by the React app itself.
        """
        return _serve_index_html()

    @app.route("/theme-init.js")
    def theme_init_js() -> Response:
        """Serve the blocking theme-init script."""
        return send_from_directory(
            get_frontend_dist(), "theme-init.js", mimetype="application/javascript"
        )

    @app.route("/logo.png")
    def logo() -> Response:
        """Serve logo from built frontend assets."""
        return send_from_directory(get_frontend_dist(), "logo.png", mimetype="image/png")

    @app.route("/favicon.ico")
    @app.route("/favico<path:_>")
    def favicon(_: Any = None) -> Response:
        """Serve favicon from built frontend assets."""
        return send_from_directory(
            get_frontend_dist(), "favicon.ico", mimetype="image/vnd.microsoft.icon"
        )

    @app.route("/api/config", methods=["GET"])
    @login_required
    def api_config() -> Response | tuple[Response, int]:
        """Get application configuration for frontend.

        Uses the dynamic config singleton to ensure settings changes
        are reflected without requiring a container restart.
        """
        try:
            from shelfmark.config.env import _is_config_dir_writable
            from shelfmark.core.onboarding import is_onboarding_complete as _get_onboarding_complete
            from shelfmark.metadata_providers import (
                get_provider_default_sort,
                get_provider_search_fields,
                get_provider_sort_options,
            )

            db_user_id = resolve_db_user_id()

            search_mode = app_config.get("SEARCH_MODE", "universal", user_id=db_user_id)
            default_release_source = app_config.get(
                "DEFAULT_RELEASE_SOURCE",
                "",
                user_id=db_user_id,
            )
            default_release_source_audiobook = app_config.get(
                "DEFAULT_RELEASE_SOURCE_AUDIOBOOK",
                "",
                user_id=db_user_id,
            )
            configured_metadata_provider = normalize_optional_text(
                app_config.get(
                    "METADATA_PROVIDER",
                    "",
                    user_id=db_user_id,
                )
            )
            _configured_metadata_provider_audiobook = normalize_optional_text(
                app_config.get(
                    "METADATA_PROVIDER_AUDIOBOOK",
                    "",
                    user_id=db_user_id,
                )
            )
            metadata_ui_provider = (
                configured_metadata_provider or _configured_metadata_provider_audiobook
            )

            config = {
                "calibre_web_url": app_config.get("CALIBRE_WEB_URL", ""),
                "audiobook_library_url": app_config.get("AUDIOBOOK_LIBRARY_URL", ""),
                "search_page_title": app_config.get("SEARCH_PAGE_TITLE", "Shelfmark"),
                "debug": app_config.get("DEBUG", False),
                "build_version": BUILD_VERSION,
                "release_version": RELEASE_VERSION,
                "book_languages": _SUPPORTED_BOOK_LANGUAGE,
                "default_language": app_config.get("BOOK_LANGUAGE", ["en"], user_id=db_user_id),
                "default_content_type": app_config.get(
                    "DEFAULT_CONTENT_TYPE", "ebook", user_id=db_user_id
                ),
                "supported_formats": app_config.SUPPORTED_FORMATS,
                "supported_audiobook_formats": app_config.SUPPORTED_AUDIOBOOK_FORMATS,
                "search_mode": search_mode,
                "metadata_sort_options": get_provider_sort_options(metadata_ui_provider),
                "metadata_search_fields": get_provider_search_fields(metadata_ui_provider),
                "default_release_source": default_release_source,
                "default_release_source_audiobook": default_release_source_audiobook,
                "show_release_source_links": app_config.get("SHOW_RELEASE_SOURCE_LINKS", True),
                "show_combined_selector": app_config.get(
                    "SHOW_COMBINED_SELECTOR", True, user_id=db_user_id
                ),
                "force_combined_search": app_config.get(
                    "FORCE_COMBINED_SEARCH", False, user_id=db_user_id
                ),
                "books_output_mode": app_config.get("BOOKS_OUTPUT_MODE", "folder"),
                "auto_open_downloads_sidebar": app_config.get("AUTO_OPEN_DOWNLOADS_SIDEBAR", True),
                "hardcover_auto_remove_on_download": app_config.get(
                    "HARDCOVER_AUTO_REMOVE_ON_DOWNLOAD", True
                ),
                "download_to_browser_content_types": app_config.get(
                    "DOWNLOAD_TO_BROWSER_CONTENT_TYPES",
                    [],
                    user_id=db_user_id,
                ),
                # The client must not give up before this budget does. `/api/releases`
                # answers a spent budget with a message naming the real cause (a protection
                # challenge nobody could solve); a browser that aborted first replaces it
                # with a generic network/proxy error and RELEASE_SEARCH_TIMEOUT becomes a
                # setting the user can raise with no visible effect. See issue #1285.
                "release_search_timeout": search_deadline.budget_seconds(),
                "settings_enabled": _is_config_dir_writable(),
                "onboarding_complete": _get_onboarding_complete(),
                # Default sort orders
                "default_sort": app_config.get(
                    "AA_DEFAULT_SORT", ""
                ),  # For direct mode (Anna's Archive) — empty means use local downloads sort
                "metadata_default_sort": get_provider_default_sort(
                    metadata_ui_provider
                ),  # For universal mode
            }
            return jsonify(config)
        except _IMPORT_OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Config error: {e}")
            return jsonify({"error": "Could not load the configuration"}), 500

    @app.route("/api/health", methods=["GET"])
    def api_health() -> Response | tuple[Response, int]:
        """Health check endpoint for container orchestration.

        No authentication required.

        Returns:
            flask.Response: JSON with status "ok" and optional degraded features. Answers 503 with
            status "error" when the download coordinator is not running, since nothing is
            downloaded without it.

        """
        response: dict[str, object] = {"status": "ok"}

        # Report degraded features
        if not backend.WEBSOCKET_AVAILABLE:
            response["degraded"] = {
                "websocket": "WebSocket unavailable - real-time updates disabled"
            }

        coordinator_up = backend.coordinator_running()
        response["coordinator"] = "running" if coordinator_up else "stopped"
        if not coordinator_up:
            response["status"] = "error"
            return jsonify(response), 503

        return jsonify(response)

    if debug_hooks is not None:

        @app.route("/api/debug", methods=["GET"])
        @admin_required
        def debug() -> Response | tuple[Response, int]:
            """Run `/app/genDebug.sh`, generate a debug zip, and return it.

            The file is written to `/tmp/shelfmark-debug.zip` before being returned.
            """
            try:
                logger.info("Debug endpoint called, stopping GUI and generating debug info...")
                debug_hooks.stop_gui()
                result = debug_hooks.run_script()
                if result.returncode != 0:
                    _raise_runtime_error(f"Debug script failed: {result.stderr}")
                logger.info("Debug script executed: %s", result.stdout)
                debug_file_path = result.stdout.strip().split("\n")[-1]
                if not Path(debug_file_path).exists():
                    logger.error("Debug zip file not found at: %s", debug_file_path)
                    return jsonify({"error": "Failed to generate debug information"}), 500

                logger.info("Sending debug file: %s", debug_file_path)
                return send_file(
                    debug_file_path,
                    mimetype="application/zip",
                    download_name=Path(debug_file_path).name,
                    as_attachment=True,
                )
            except subprocess.CalledProcessError as e:
                logger.error_trace(
                    f"Debug script error: {e}, stdout: {e.stdout}, stderr: {e.stderr}"
                )
                return jsonify({"error": f"Debug script failed: {e.stderr}"}), 500
            except _OPERATIONAL_ERRORS as e:
                logger.error_trace(f"Debug endpoint error: {e}")
                return jsonify({"error": str(e)}), 500

        @app.route("/api/restart", methods=["GET"])
        @admin_required
        def restart() -> Response | tuple[Response, int]:
            """Restart the application."""
            os._exit(0)

    # Catch-all route for React Router. Werkzeug tries static rules before dynamic ones, so it
    # does not have to be registered last.
    # This handles client-side routing by serving index.html for any unmatched routes
    @app.route("/<path:path>")
    def catch_all(path: str) -> Response | tuple[Response, int]:
        """Serve the React app for any route not matched by API endpoints.

        This allows React Router to handle client-side routing.
        Authentication is handled by the React app itself.
        """
        # If the request is for an API endpoint or static file, let it 404
        if path.startswith(("api/", "assets/")):
            return jsonify({"error": "Resource not found"}), 404
        # Otherwise serve the React app
        return _serve_index_html()
