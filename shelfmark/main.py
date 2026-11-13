"""Flask app - routes, WebSocket handlers, and middleware."""

import os
import sqlite3
import time
from functools import wraps
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NoReturn, cast

from flask import Flask, jsonify, request, session
from flask_cors import CORS
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash

from shelfmark.api.websocket import ws_manager
from shelfmark.config.env import (
    CONFIG_DIR,
    CWA_DB_PATH,
    DISABLE_LOCAL_AUTH,
    FLASK_HOST,
    FLASK_PORT,
    HIDE_LOCAL_AUTH,
    OIDC_AUTO_REDIRECT,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE_ENV,
    SOCKETIO_ALLOWED_ORIGINS,
    _is_config_dir_writable,
    string_to_bool,
)
from shelfmark.config.security import _migrate_security_settings
from shelfmark.config.settings import (
    migrate_audiobook_format_settings,
)
from shelfmark.core.activity_view_state_service import ActivityViewStateService
from shelfmark.core.app_setup import (
    configure_app_logging,
    configure_secret_key_and_session,
    register_security_headers,
)
from shelfmark.core.auth_middleware import register_auth_middleware
from shelfmark.core.auth_modes import (
    get_auth_check_admin_status,
    is_settings_or_onboarding_path,
    load_active_auth_mode,
    requires_admin_for_settings_access,
)

# The lockout names are re-exported: tests and other callers reach them as `shelfmark.main.<name>`.
from shelfmark.core.auth_routes import (  # noqa: F401
    LOCKOUT_DURATION_MINUTES,
    LOGIN_ATTEMPT_WARNING_THRESHOLD,
    MAX_LOGIN_ATTEMPTS,
    clear_failed_logins,
    failed_login_attempts,
    is_account_locked,
    record_failed_login,
    register_auth_routes,
)
from shelfmark.core.config import config as app_config
from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.download_recording import register_download_recording
from shelfmark.core.logger import setup_logger
from shelfmark.core.metadata_routes import register_metadata_routes
from shelfmark.core.onboarding_routes import register_onboarding_routes
from shelfmark.core.prefix_middleware import PrefixMiddleware
from shelfmark.core.queue_routes import register_queue_routes
from shelfmark.core.release_routes import register_release_routes
from shelfmark.core.request_helpers import (
    emit_ws_event,
    get_session_db_user_id,
    load_users_request_policy_settings,
    normalize_optional_text,
)
from shelfmark.core.requests_service import (
    sync_delivery_states_from_queue_status,
)
from shelfmark.core.settings_routes import register_settings_routes
from shelfmark.core.socket_handlers import register_socket_handlers
from shelfmark.core.socketio_origins import build_cors_allowed_origins
from shelfmark.core.stats_routes import register_stats_routes
from shelfmark.core.system_routes import DebugHooks, register_system_routes
from shelfmark.core.user_db import UserDB
from shelfmark.core.utils import normalize_base_path
from shelfmark.core.viewer_redaction import (
    configure_public_id_key,
    redact_status,
)
from shelfmark.download import orchestrator as backend
from shelfmark.download import warmup

if TYPE_CHECKING:
    from werkzeug.wrappers import Response


logger = setup_logger(__name__)
_OPERATIONAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)
_IMPORT_OPERATIONAL_ERRORS = (ImportError, *_OPERATIONAL_ERRORS)


def _is_debug_enabled() -> bool:
    debug_value = app_config.get("DEBUG", False)
    if isinstance(debug_value, str):
        return string_to_bool(debug_value)
    return bool(debug_value)


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


# Project root is the repository root above the package directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = PROJECT_ROOT / "frontend-dist"

BASE_PATH = normalize_base_path(normalize_optional_text(app_config.get("URL_BASE", "")))

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # Disable caching
app.config["APPLICATION_ROOT"] = BASE_PATH or "/"
wsgi_app = cast(Any, ProxyFix(app.wsgi_app, x_host=1, x_port=1))
if BASE_PATH:
    wsgi_app = cast(Any, PrefixMiddleware(wsgi_app, BASE_PATH, bypass_paths={"/api/health"}))
app.wsgi_app = wsgi_app

# Socket.IO async mode.
# We run this app under Gunicorn with a gevent websocket worker (even when DEBUG=true),
# so Socket.IO should always use gevent here.
async_mode = "gevent"
# Same-origin only (python-engineio default), plus any SOCKETIO_ALLOWED_ORIGINS.
socketio_cors_allowed_origins = build_cors_allowed_origins(SOCKETIO_ALLOWED_ORIGINS)

# Initialize Flask-SocketIO with reverse proxy support
socketio_path = f"{BASE_PATH}/socket.io" if BASE_PATH else "/socket.io"
socketio_init_kwargs: dict[str, Any] = {
    "cors_allowed_origins": socketio_cors_allowed_origins,
    "async_mode": async_mode,
    "logger": False,
    "engineio_logger": False,
    # Reverse proxy / Traefik compatibility settings
    "path": socketio_path,
    "ping_timeout": 60,
    "ping_interval": 25,
    # Allow both websocket and polling for better compatibility
    "transports": ["websocket", "polling"],
    "allow_upgrades": True,
    # Important for proxies that buffer
    "http_compression": True,
}
socketio = SocketIO(app, **socketio_init_kwargs)

# Initialize WebSocket manager
ws_manager.init_app(app, socketio)


def _user_room_queue_status(user_id: int | None = None) -> dict[str, dict[str, Any]]:
    """Queue status for a non-admin user room, with release details hidden (#1418)."""
    return redact_status(backend.queue_status(user_id=user_id))


ws_manager.set_queue_status_fn(_user_room_queue_status)
logger.info("Flask-SocketIO initialized with async_mode='%s'", async_mode)
logger.info(
    "Socket.IO origin check: same-origin plus %s configured origin(s)",
    len(SOCKETIO_ALLOWED_ORIGINS),
)

# Ensure all plugins are loaded before starting the download coordinator.
# This prevents a race condition where the download loop could try to process
# a queued task before its handler (e.g., prowlarr) is registered.
try:
    import_module("shelfmark.metadata_providers")
    import_module("shelfmark.release_sources")
    logger.debug("Plugin modules loaded successfully")
except ImportError as e:
    logger.warning("Failed to import plugin modules: %s", e)

# Migrate legacy security settings if needed
_migrate_security_settings()

# Widen audiobook formats for installs that still carry the old m4b/mp3-only default
migrate_audiobook_format_settings()

# Initialize user database and register multi-user routes
# If CONFIG_DIR doesn't exist or is read-only, multi-user features will be disabled
_user_db_path = str(Path(os.environ.get("CONFIG_DIR", "/config")) / "users.db")
user_db: UserDB | None = None
download_history_service: DownloadHistoryService | None = None
activity_view_state_service: ActivityViewStateService | None = None
try:
    user_db = UserDB(_user_db_path)
    user_db.initialize()
    download_history_service = DownloadHistoryService(_user_db_path)
    activity_view_state_service = ActivityViewStateService(_user_db_path)
    import_module("shelfmark.config.users_settings")
    from shelfmark.core.admin_routes import register_admin_routes
    from shelfmark.core.oidc_routes import register_oidc_routes
    from shelfmark.core.self_user_routes import (
        PasswordAttemptLimiter,
        register_self_user_routes,
    )
    from shelfmark.core.series_gap_routes import register_series_gap_routes

    register_oidc_routes(app, user_db)
    register_admin_routes(app, user_db)
    register_self_user_routes(
        app,
        user_db,
        # Looked up when called: the lockout helpers are defined further down this module.
        password_attempts=PasswordAttemptLimiter(
            is_locked=lambda username: is_account_locked(username),  # noqa: PLW0108
            record_failure=lambda username: record_failed_login(username, get_client_ip()),
            clear=lambda username: clear_failed_logins(username),  # noqa: PLW0108
        ),
    )
    register_series_gap_routes(app, user_db)
except (sqlite3.OperationalError, OSError) as e:
    logger.warning(
        "User database initialization failed: %s. Multi-user authentication features will be disabled. Ensure CONFIG_DIR (%s) exists and is writable.",
        e,
        os.environ.get("CONFIG_DIR", "/config"),
    )
    user_db = None
    download_history_service = None
    activity_view_state_service = None


def _warn_if_local_admin_missing() -> None:
    """Log a recovery hint when builtin/OIDC auth is active without a local admin."""
    if user_db is None or DISABLE_LOCAL_AUTH:
        return
    auth_mode = load_active_auth_mode()
    if auth_mode not in ("builtin", "oidc"):
        return
    try:
        if user_db.has_admin_with_password():
            return
    except sqlite3.Error:
        return
    logger.warning(
        "AUTH_METHOD=%s is active but no local admin account with a password exists. If no "
        "admin can sign in, start once with AUTH_METHOD=none (keep Shelfmark off the public "
        "internet meanwhile), create a local admin under Settings > Users, then remove the "
        "override.",
        auth_mode,
    )


_warn_if_local_admin_missing()

# Nothing is downloading yet, so anything the last process left "active" or "queued" was
# interrupted. Close those rows out and put the recently interrupted requests back in the
# queue, before the coordinator starts. Assumes one worker process (entrypoint.sh).
if user_db is not None and download_history_service is not None:
    try:
        from shelfmark.core.startup_reconcile import reconcile_interrupted_downloads

        _reconciled = reconcile_interrupted_downloads(user_db, download_history_service)
        if _reconciled["marked"] or _reconciled["reopened"]:
            logger.info(
                "Startup: closed out %d interrupted download(s), reopened %d request(s)",
                _reconciled["marked"],
                _reconciled["reopened"],
            )
    except _IMPORT_OPERATIONAL_ERRORS as exc:
        logger.warning("Could not reconcile interrupted downloads at startup: %s", exc)

# Start download coordinator
backend.start()

# Start the Hardcover wishlist sync + auto-download scheduler (in-process daemon).
if user_db is not None:
    try:
        from shelfmark.core import hardcover_scheduler

        hardcover_scheduler.start(user_db, backend.queue_release, _user_db_path)
    except (ImportError, RuntimeError, OSError) as exc:
        logger.warning("Failed to start Hardcover scheduler: %s", exc)

# Dead-torrent and Soulseek-leftover sweeps run on their own timer, whether or not
# Hardcover sync is on. Each is a no-op unless its setting and client are configured.
if user_db is not None and download_history_service is not None:
    try:
        from shelfmark.download import housekeeping

        housekeeping.start(user_db, download_history_service)
    except (ImportError, RuntimeError, OSError) as exc:
        logger.warning("Failed to start download housekeeping: %s", exc)

# Pre-solve the direct-download source's protection challenge in the background so the
# first user search does not pay for a cold Chrome bypass. Never blocks startup.
warmup.start()


def get_client_ip() -> str:
    """Extract client IP address from request, handling reverse proxy forwarding."""
    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    # X-Forwarded-For can contain multiple IPs, take the first one
    if "," in ip_address:
        ip_address = ip_address.split(",")[0].strip()
    return ip_address


def get_auth_mode() -> str:
    """Determine which authentication mode is active.

    Returns "none" only when AUTH_METHOD is explicitly "none"; an unreadable or
    unrecognized AUTH_METHOD fails closed.
    """
    return load_active_auth_mode()


def _emit_request_updates(rows: list[dict[str, Any]]) -> None:
    """Defer request update emission until the runtime hook is available."""
    _emit_request_update_events(rows)


def _resolve_auth_mode_for_routes() -> str:
    """Resolve auth mode lazily so tests and runtime patches still take effect."""
    return get_auth_mode()


def _load_policy_settings_for_routes() -> dict[str, Any]:
    """Load the request policy lazily so tests and runtime patches still take effect."""
    return load_users_request_policy_settings()


def _queue_release_for_routes(*args: Any, **kwargs: Any) -> Any:
    """Queue a release via the current backend instance."""
    return backend.queue_release(*args, **kwargs)


def _queue_status_for_routes(user_id: int | None = None) -> dict[str, dict[str, Any]]:
    """Read queue status via the current backend instance."""
    return backend.queue_status(user_id=user_id)


if user_db is not None:
    try:
        from shelfmark.core.activity_routes import register_activity_routes
        from shelfmark.core.request_routes import register_request_routes

        register_request_routes(
            app,
            user_db,
            resolve_auth_mode=_resolve_auth_mode_for_routes,
            queue_release=_queue_release_for_routes,
            ws_manager=ws_manager,
            activity_view_state_service=activity_view_state_service,
        )
        if download_history_service is not None and activity_view_state_service is not None:
            register_activity_routes(
                app,
                user_db,
                activity_view_state_service=activity_view_state_service,
                download_history_service=download_history_service,
                resolve_auth_mode=_resolve_auth_mode_for_routes,
                queue_status=_queue_status_for_routes,
                sync_request_delivery_states=sync_delivery_states_from_queue_status,
                emit_request_updates=_emit_request_updates,
                ws_manager=ws_manager,
            )
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.warning("Failed to register request routes: %s", e)


# Enable CORS in development mode for local frontend development
if _is_debug_enabled():
    CORS(
        app,
        resources={
            r"/*": {
                "origins": ["http://localhost:5173", "http://127.0.0.1:5173"],
                "supports_credentials": True,
                "allow_headers": ["Content-Type", "Authorization", "X-Api-Key"],
                "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            }
        },
    )


configure_app_logging(app, logger)

# Set up authentication defaults
SESSION_COOKIE_SECURE = string_to_bool(SESSION_COOKIE_SECURE_ENV)


configure_secret_key_and_session(
    app,
    CONFIG_DIR,
    cookie_secure=SESSION_COOKIE_SECURE,
    cookie_name=SESSION_COOKIE_NAME,
)
configure_public_id_key(app.config["SECRET_KEY"])

logger.info(
    "Session cookie secure setting: %s (from env: %s)",
    SESSION_COOKIE_SECURE,
    SESSION_COOKIE_SECURE_ENV,
)
logger.info("Session cookie name: %s", SESSION_COOKIE_NAME)


register_security_headers(app)
register_auth_middleware(
    app,
    resolve_auth_mode=lambda: get_auth_mode(),  # noqa: PLW0108
    get_user_db=lambda: user_db,
)


def login_required(
    f: Callable[..., Response | tuple[Response, int]],
) -> Callable[..., Response | tuple[Response, int]]:
    """Require authentication for a Flask route."""

    @wraps(f)
    def decorated_function(*args: object, **kwargs: object) -> Response | tuple[Response, int]:
        auth_mode = get_auth_mode()

        # If no authentication is configured, allow access
        if auth_mode == "none":
            return f(*args, **kwargs)

        # If CWA mode and database disappeared after startup, return error
        if auth_mode == "cwa" and CWA_DB_PATH and not CWA_DB_PATH.exists():
            logger.error("CWA database at %s is no longer accessible", CWA_DB_PATH)
            return jsonify({"error": "Internal Server Error"}), 500

        # Check if user has a valid session
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401

        # Check admin access for settings/onboarding endpoints.
        if is_settings_or_onboarding_path(request.path):
            try:
                if requires_admin_for_settings_access(request.path, {}) and not session.get(
                    "is_admin", False
                ):
                    return jsonify({"error": "Admin access required"}), 403

            except RuntimeError, TypeError, ValueError:
                logger.exception("Admin access check error")
                return jsonify({"error": "Internal Server Error"}), 500

        return f(*args, **kwargs)

    return decorated_function


_debug_hooks: DebugHooks | None = None
if _is_debug_enabled():
    import subprocess

    def _stop_gui() -> None:
        return None

    if app_config.get("USING_EXTERNAL_BYPASSER", False):
        pass
    else:
        from shelfmark.bypass.internal_bypasser import _cleanup_orphan_processes

        def _stop_gui() -> None:
            _cleanup_orphan_processes()

    def _run_debug_script() -> subprocess.CompletedProcess[str]:
        time.sleep(1)
        return subprocess.run(["/app/genDebug.sh"], capture_output=True, text=True, check=True)

    _debug_hooks = DebugHooks(stop_gui=_stop_gui, run_script=_run_debug_script)

register_system_routes(
    app,
    login_required,
    resolve_auth_mode=_resolve_auth_mode_for_routes,
    resolve_db_user_id=lambda: get_session_db_user_id(session),
    get_base_path=lambda: BASE_PATH,
    get_frontend_dist=lambda: FRONTEND_DIST,
    debug_hooks=_debug_hooks,
)


register_stats_routes(
    app,
    get_user_db=lambda: user_db,
    resolve_auth_mode=_resolve_auth_mode_for_routes,
)


def _resolve_status_scope(*, require_authenticated: bool = True) -> tuple[bool, int | None, bool]:
    """Resolve queue-status visibility from session state.

    Returns:
        (is_admin, db_user_id, can_access_status)

    """
    auth_mode = get_auth_mode()
    if auth_mode == "none":
        return True, None, True

    if require_authenticated and "user_id" not in session:
        return False, None, False

    is_admin = bool(session.get("is_admin", False))
    if is_admin:
        return True, None, True

    db_user_id = get_session_db_user_id(session)

    if db_user_id is None:
        return False, None, False

    return False, db_user_id, True


register_download_recording(
    get_user_db=lambda: user_db,
    get_download_history_service=lambda: download_history_service,
    get_activity_view_state_service=lambda: activity_view_state_service,
    # Looked up when called: _emit_request_update_events is defined just below.
    emit_request_update_events=lambda rows: _emit_request_update_events(rows),  # noqa: PLW0108
    ws_manager=ws_manager,
)


def _emit_request_update_events(updated_requests: list[dict[str, Any]]) -> None:
    """Broadcast request_update events for rows changed by delivery-state sync."""
    if not updated_requests or ws_manager is None:
        return

    for updated in updated_requests:
        payload = {
            "request_id": updated["id"],
            "status": updated["status"],
            "delivery_state": updated.get("delivery_state"),
            "title": (updated.get("book_data") or {}).get("title") or "Unknown title",
        }
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            room=f"user_{updated['user_id']}",
            payload=payload,
        )
        emit_ws_event(
            ws_manager,
            event_name="request_update",
            room="admins",
            payload=payload,
        )


register_queue_routes(
    app,
    login_required,
    get_user_db=lambda: user_db,
    get_download_history_service=lambda: download_history_service,
    resolve_status_scope=_resolve_status_scope,
    emit_request_update_events=_emit_request_update_events,
)


@app.errorhandler(404)
def not_found_error(error: Exception) -> Response | tuple[Response, int]:
    """Handle 404 (Not Found) errors.

    Args:
        error (HTTPException): The 404 error raised by Flask.

    Returns:
        flask.Response: JSON error message with 404 status.

    """
    logger.warning("404 error: %s : %s", request.url, error)
    return jsonify({"error": "Resource not found"}), 404


@app.errorhandler(500)
def internal_error(error: Exception) -> Response | tuple[Response, int]:
    """Handle 500 (Internal Server) errors.

    Args:
        error (HTTPException): The 500 error raised by Flask.

    Returns:
        flask.Response: JSON error message with 500 status.

    """
    logger.error_trace(f"500 error: {error}")
    return jsonify({"error": "Internal server error"}), 500


register_auth_routes(
    app,
    get_user_db=lambda: user_db,
    resolve_auth_mode=_resolve_auth_mode_for_routes,
    get_client_ip=lambda: get_client_ip(),  # noqa: PLW0108
    account_is_locked=lambda username: is_account_locked(username),  # noqa: PLW0108
    disable_local_auth=lambda: DISABLE_LOCAL_AUTH,
    hide_local_auth=lambda: HIDE_LOCAL_AUTH,
    oidc_auto_redirect=lambda: OIDC_AUTO_REDIRECT,
    get_cwa_db_path=lambda: CWA_DB_PATH,
    check_password=lambda pwhash, password: check_password_hash(pwhash, password),  # noqa: PLW0108
    get_auth_check_admin_status=lambda auth_mode, headers, sess: get_auth_check_admin_status(  # noqa: PLW0108
        auth_mode, headers, sess
    ),
    operational_errors=_OPERATIONAL_ERRORS,
)
# The views as plain module attributes, for tests/e2e/test_auth_endpoints.py, which calls them directly.
api_login = app.view_functions["api_login"]
api_logout = app.view_functions["api_logout"]
api_auth_check = app.view_functions["api_auth_check"]


def _attach_library_ownership(book: Any, book_dict: dict[str, Any]) -> None:
    """Add per-format ownership under ``library`` when a library check is enabled."""
    from shelfmark.core import library_index

    owned = library_index.ownership(book)
    if owned is not None:
        book_dict["library"] = owned


register_metadata_routes(
    app,
    login_required=login_required,
    resolve_auth_mode=_resolve_auth_mode_for_routes,
    attach_library_ownership=_attach_library_ownership,
)


register_release_routes(
    app,
    login_required,
    resolve_auth_mode=_resolve_auth_mode_for_routes,
    get_user_db=lambda: user_db,
    load_policy_settings=_load_policy_settings_for_routes,
    resolve_status_scope=_resolve_status_scope,
    attach_library_ownership=_attach_library_ownership,
)


register_settings_routes(app, login_required)


register_onboarding_routes(app, login_required)


register_socket_handlers(
    socketio, ws_manager=ws_manager, resolve_status_scope=_resolve_status_scope
)


logger.log_resource_usage()

# Warn if config directory is not writable (settings won't persist)
if not _is_config_dir_writable():
    logger.warning(
        "Config directory %s is not writable. Settings will not persist. Mount a config volume to enable settings persistence (see docs for details).",
        CONFIG_DIR,
    )

if __name__ == "__main__":
    debug_enabled = _is_debug_enabled()
    logger.info(
        "Starting Flask application with WebSocket support on %s:%s (debug=%s)",
        FLASK_HOST,
        FLASK_PORT,
        debug_enabled,
    )
    socketio.run(
        app,
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=debug_enabled,
        allow_unsafe_werkzeug=True,  # For development only
    )
