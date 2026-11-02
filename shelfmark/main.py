"""Flask app - routes, WebSocket handlers, and middleware."""

import os
import re
import sqlite3
import time
from functools import wraps
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, NoReturn, cast

from flask import Flask, g, jsonify, request, session
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
from shelfmark.core import api_key as api_key_module  # module access lets tests monkeypatch the key
from shelfmark.core import search_deadline
from shelfmark.core.activity_view_state_service import ActivityViewStateService
from shelfmark.core.app_setup import (
    configure_app_logging,
    configure_secret_key_and_session,
    register_security_headers,
)
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
from shelfmark.core.external_user_linking import upsert_external_user
from shelfmark.core.logger import setup_logger
from shelfmark.core.metadata_routes import register_metadata_routes
from shelfmark.core.models import (
    TERMINAL_QUEUE_STATUSES,
    QueueStatus,
    SearchFilters,
)
from shelfmark.core.notifications import (
    NotificationContext,
    NotificationEvent,
    notify_admin,
    notify_user,
)
from shelfmark.core.onboarding_routes import register_onboarding_routes
from shelfmark.core.prefix_middleware import PrefixMiddleware
from shelfmark.core.queue_routes import register_queue_routes
from shelfmark.core.release_inspect_routes import register_release_inspect_routes
from shelfmark.core.request_helpers import (
    coerce_bool,
    emit_ws_event,
    get_session_db_user_id,
    load_users_request_policy_settings,
    normalize_optional_text,
    normalize_positive_int,
)
from shelfmark.core.request_policy import (
    DEFAULT_SUPPORTED_CONTENT_TYPES,
    PolicyMode,
    get_source_content_type_capabilities,
    merge_request_policy_settings,
    normalize_content_type,
    normalize_source,
    resolve_policy_mode,
)
from shelfmark.core.requests_service import (
    reopen_failed_request,
    sync_delivery_states_from_queue_status,
)
from shelfmark.core.settings_routes import register_settings_routes
from shelfmark.core.socket_handlers import register_socket_handlers
from shelfmark.core.socketio_origins import build_cors_allowed_origins
from shelfmark.core.stats_routes import STATS_PATH, register_stats_routes
from shelfmark.core.system_routes import DebugHooks, register_system_routes
from shelfmark.core.user_db import UserDB
from shelfmark.core.utils import AUDIOBOOK_FORMATS, normalize_base_path
from shelfmark.core.viewer_redaction import (
    configure_public_id_key,
    redact_status,
    viewer_download_id,
)
from shelfmark.download import orchestrator as backend
from shelfmark.download import warmup
from shelfmark.release_sources import (
    BrowseRecord,
    Release,
    SourceUnavailableError,
    get_source_display_name,
)

if TYPE_CHECKING:
    from werkzeug.wrappers import Response

    from shelfmark.metadata_providers import BookMetadata

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


_AUDIOBOOK_CATEGORY_RANGE = (3030, 3049)
_AUDIOBOOK_FORMAT_HINTS = frozenset(AUDIOBOOK_FORMATS)


def _contains_audiobook_format_hint(value: Any) -> bool:
    if not isinstance(value, str):
        return False

    normalized = value.strip().lower()
    if not normalized:
        return False

    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    return any(token in _AUDIOBOOK_FORMAT_HINTS for token in tokens)


def _resolve_release_content_type(data: dict[str, Any], source: Any) -> tuple[str, bool]:
    """Resolve release content type for policy checks and queue payload normalization."""
    extra = data.get("extra")
    if not isinstance(extra, dict):
        extra = {}

    explicit_content_type = data.get("content_type")
    if explicit_content_type is None:
        explicit_content_type = extra.get("content_type")
    if explicit_content_type is not None:
        return normalize_content_type(explicit_content_type), False

    categories = extra.get("categories")
    if isinstance(categories, list):
        min_cat, max_cat = _AUDIOBOOK_CATEGORY_RANGE
        for raw_category in categories:
            try:
                category_id = int(raw_category)
            except TypeError, ValueError:
                continue
            if min_cat <= category_id <= max_cat:
                return "audiobook", True

    candidates: list[Any] = [
        data.get("format"),
        extra.get("format"),
        extra.get("formats_display"),
        data.get("title"),
    ]
    formats = extra.get("formats")
    if isinstance(formats, list):
        candidates.extend(formats)
    else:
        candidates.append(formats)

    if any(_contains_audiobook_format_hint(candidate) for candidate in candidates):
        return "audiobook", True

    capabilities = get_source_content_type_capabilities()
    supported = capabilities.get(normalize_source(source))
    if supported and len(supported) == 1:
        return normalize_content_type(next(iter(supported))), True

    return "ebook", False


def _resolve_policy_mode_for_current_user(*, source: Any, content_type: Any) -> PolicyMode | None:
    """Resolve policy mode for current session, or None when policy guard is bypassed."""
    auth_mode = get_auth_mode()
    if auth_mode == "none":
        return None
    if session.get("is_admin", False):
        return None
    if user_db is None:
        return None

    global_settings = load_users_request_policy_settings()
    db_user_id = session.get("db_user_id")
    user_settings: dict[str, Any] | None = None
    if db_user_id is not None:
        try:
            user_settings = user_db.get_user_settings(int(db_user_id))
        except TypeError, ValueError:
            user_settings = None

    effective = merge_request_policy_settings(global_settings, user_settings)
    if not coerce_bool(effective.get("REQUESTS_ENABLED"), default=False):
        return None

    resolved_mode = resolve_policy_mode(
        source=source,
        content_type=content_type,
        global_settings=global_settings,
        user_settings=user_settings,
    )
    logger.debug(
        "download policy resolve user=%s db_user_id=%s is_admin=%s source=%s content_type=%s mode=%s",
        session.get("user_id"),
        db_user_id,
        bool(session.get("is_admin", False)),
        source,
        content_type,
        resolved_mode.value,
    )
    return resolved_mode


def _policy_block_response(
    mode: PolicyMode, *, error: str = "Download not allowed by policy"
) -> tuple[Response, int]:
    logger.debug(
        "download policy guard user=%s db_user_id=%s mode=%s",
        session.get("user_id"),
        session.get("db_user_id"),
        mode.value,
    )
    if mode == PolicyMode.BLOCKED:
        return (
            jsonify(
                {
                    "error": error,
                    "code": "policy_blocked",
                    "required_mode": PolicyMode.BLOCKED.value,
                }
            ),
            403,
        )
    return (
        jsonify(
            {
                "error": error,
                "code": "policy_requires_request",
                "required_mode": mode.value,
            }
        ),
        403,
    )


# Modes under which the user never sees a release list: request_book asks for the
# book as a whole and blocked gets nothing. request_release still lists releases,
# since that user picks the one they ask for. Listing goes through the shared
# source credentials (Prowlarr, torrent clients, scrapers), so the routes that
# list have to refuse these two modes just as the download route does.
_RELEASE_LISTING_REFUSED_MODES = frozenset({PolicyMode.REQUEST_BOOK, PolicyMode.BLOCKED})


def _release_listing_refusal(*, source: Any, content_type: Any) -> PolicyMode | None:
    """Return the policy mode when the current user may not list releases from `source`."""
    mode = _resolve_policy_mode_for_current_user(source=source, content_type=content_type)
    if mode is not None and mode in _RELEASE_LISTING_REFUSED_MODES:
        return mode
    return None


def _release_listing_block_response(mode: PolicyMode) -> tuple[Response, int]:
    return _policy_block_response(mode, error="Release listing not allowed by policy")


def _sources_allowed_for_listing(
    sources: list[str], content_type: Any
) -> tuple[list[str], PolicyMode | None]:
    """Drop the sources the current user may not list releases from.

    Returns the sources left and, when any was dropped, the mode of the first one
    dropped, which is what the response reports when nothing is left.
    """
    allowed: list[str] = []
    refused_mode: PolicyMode | None = None
    for source_name in sources:
        refusal = _release_listing_refusal(source=source_name, content_type=content_type)
        if refusal is None:
            allowed.append(source_name)
        elif refused_mode is None:
            refused_mode = refusal
    return allowed, refused_mode


def _release_source_record_refusal(source_name: str) -> PolicyMode | None:
    """Policy refusal for resolving one browse record of a release source.

    The record's content type is not known before it is fetched, so the user is
    refused only when every content type the source supports is refused.
    """
    capabilities = get_source_content_type_capabilities()
    content_types = capabilities.get(normalize_source(source_name)) or set(
        DEFAULT_SUPPORTED_CONTENT_TYPES
    )
    refused_mode: PolicyMode | None = None
    for content_type in sorted(content_types):
        refusal = _release_listing_refusal(source=source_name, content_type=content_type)
        if refusal is None:
            return None
        if refused_mode is None:
            refused_mode = refusal
    return refused_mode


def _release_payload_listing_gate(data: dict[str, Any]) -> tuple[Response, int] | None:
    """Policy gate for routes that take a release payload, shared with the inspect route."""
    source = data["source"]
    resolved_content_type, _inferred = _resolve_release_content_type(data, source)
    refusal = _release_listing_refusal(source=source, content_type=resolved_content_type)
    if refusal is None:
        return None
    return _release_listing_block_response(refusal)


def _resolve_download_user_context(
    db_user_id: Any,
    username: Any,
    on_behalf_of_user_id: Any,
) -> tuple[Any, Any, tuple[Response, int] | None]:
    """Resolve download queue user context, including optional admin on-behalf overrides."""
    if on_behalf_of_user_id in (None, ""):
        return db_user_id, username, None

    if not session.get("is_admin", False):
        return db_user_id, username, (jsonify({"error": "Admin required"}), 403)

    if user_db is None:
        return db_user_id, username, (jsonify({"error": "User database unavailable"}), 503)

    try:
        target_user_id = int(on_behalf_of_user_id)
    except TypeError, ValueError:
        return db_user_id, username, (jsonify({"error": "Invalid on_behalf_of_user_id"}), 400)

    if target_user_id <= 0:
        return db_user_id, username, (jsonify({"error": "Invalid on_behalf_of_user_id"}), 400)

    target_user = user_db.get_user(user_id=target_user_id)
    if not target_user:
        return db_user_id, username, (jsonify({"error": "User not found"}), 404)

    return target_user["id"], target_user["username"], None


def _emit_request_updates(rows: list[dict[str, Any]]) -> None:
    """Defer request update emission until the runtime hook is available."""
    _emit_request_update_events(rows)


def _resolve_auth_mode_for_routes() -> str:
    """Resolve auth mode lazily so tests and runtime patches still take effect."""
    return get_auth_mode()


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


def _proxy_default_is_admin(db: UserDB) -> bool:
    """Role for a proxy user seen for the first time when no admin group is configured.

    The first account ever provisioned is an admin so the instance is never left without
    one; later accounts follow PROXY_AUTH_DEFAULT_ROLE (default: user).
    """
    if not db.has_admin():
        return True
    role = str(app_config.get("PROXY_AUTH_DEFAULT_ROLE", "user") or "user").strip().lower()
    return role == "admin"


_API_KEY_EXEMPT_PREFIXES = ("/api/auth/",)
_API_KEY_EXEMPT_PATHS = frozenset({"/api/health"})
_READ_ONLY_KEY_PATHS = frozenset({STATS_PATH})


@app.before_request
def api_key_auth_middleware() -> Response | tuple[Response, int] | None:
    """Authenticate requests that present the configured SHELFMARK_API_KEY.

    Both Authorization: Bearer and X-Api-Key are checked, and either
    matching authenticates the request as an admin for this request only:
    any session cookie is ignored and none is written back. Checking both
    means a reverse proxy's own Authorization header never shadows an
    operator-supplied X-Api-Key. No matching candidate is ignored so bearer
    tokens forwarded by reverse proxies keep working; the request then
    continues on the normal session path.
    """
    if not request.path.startswith("/api/"):
        return None
    if request.path in _API_KEY_EXEMPT_PATHS or request.path.startswith(_API_KEY_EXEMPT_PREFIXES):
        return None
    if not api_key_module.SHELFMARK_API_KEY and not api_key_module.SHELFMARK_API_KEY_READONLY:
        return None

    candidates = api_key_module.extract_api_key_candidates(
        request.headers.get("Authorization"), request.headers.get("X-Api-Key")
    )
    if not candidates:
        return None

    scopes = {api_key_module.key_scope(candidate) for candidate in candidates}
    scope = "admin" if "admin" in scopes else "readonly" if "readonly" in scopes else None
    if scope is None:
        return None
    if get_auth_mode() == "none":
        return None

    # Mark the request as keyed before the lookup so the after-request cookie
    # reset also covers the error path below.
    g.api_key_auth = True

    if scope == "readonly":
        # A read-only key is a dashboard credential: one GET endpoint, no session.
        g.api_key_scope = "readonly"
        if request.method != "GET" or request.path not in _READ_ONLY_KEY_PATHS:
            return jsonify({"error": "This API key is read-only"}), 403
        return None
    g.api_key_scope = "admin"

    try:
        admin = user_db.get_first_admin() if user_db is not None else None
    except _OPERATIONAL_ERRORS:
        logger.exception("API key auth middleware error")
        return jsonify({"error": "Authentication error"}), 500

    session.clear()
    session["user_id"] = admin["username"] if admin else "api"
    session["is_admin"] = True
    if admin:
        session["db_user_id"] = admin["id"]
    session.permanent = False
    # Identity is per request; never persist it as a cookie.
    session.modified = False
    return None


@app.before_request
def proxy_auth_middleware() -> Response | tuple[Response, int] | None:
    """Middleware to handle proxy authentication.

    When AUTH_METHOD is set to "proxy", this middleware automatically
    authenticates users based on headers set by the reverse proxy.
    """
    auth_mode = get_auth_mode()

    # Only run for proxy auth mode
    if auth_mode != "proxy":
        return None

    # A request already authenticated by SHELFMARK_API_KEY needs no proxy headers.
    if g.get("api_key_auth"):
        return None

    # Skip for public endpoints that don't need auth
    if request.path == "/api/health":
        return None

    def get_proxy_header(header_name: str) -> str | None:
        """Resolve proxy auth values from headers with WSGI env fallbacks."""
        value = request.headers.get(header_name)
        if value:
            return value

        env_key = f"HTTP_{header_name.upper().replace('-', '_')}"
        value = request.environ.get(env_key)
        if value:
            return value

        # Some proxies set authenticated username in REMOTE_USER (not as a header).
        if header_name.lower().replace("_", "-") == "remote-user":
            return request.environ.get("REMOTE_USER")

        return None

    try:
        user_header = (
            normalize_optional_text(app_config.get("PROXY_AUTH_USER_HEADER", "X-Auth-User"))
            or "X-Auth-User"
        )

        # Extract username from proxy header
        username = get_proxy_header(user_header)

        if not username:
            if request.path.startswith("/api/auth/"):
                return None

            logger.warning("Proxy auth enabled but no username found in header '%s'", user_header)
            return jsonify({"error": "Authentication required. Proxy header not set."}), 401

        # Resolve admin role for proxy sessions.
        # If an admin group is configured, derive from groups header.
        # Otherwise preserve the existing DB role for known users; a first-time user
        # is an admin only while the instance has none (so nobody is locked out),
        # after that PROXY_AUTH_DEFAULT_ROLE decides (default: user).
        admin_group_header = (
            normalize_optional_text(
                app_config.get("PROXY_AUTH_ADMIN_GROUP_HEADER", "X-Auth-Groups")
            )
            or "X-Auth-Groups"
        )
        admin_group_name = (
            normalize_optional_text(app_config.get("PROXY_AUTH_ADMIN_GROUP_NAME", "")) or ""
        )
        is_admin = True

        if admin_group_name:
            groups_header = get_proxy_header(admin_group_header) or ""
            user_groups_delimiter = "," if "," in groups_header else "|"
            user_groups = [
                g.strip() for g in groups_header.split(user_groups_delimiter) if g.strip()
            ]
            is_admin = admin_group_name in user_groups
        elif user_db is not None:
            existing_db_user = user_db.get_user_cached(username=username)
            if existing_db_user:
                is_admin = existing_db_user.get("role") == "admin"
            else:
                is_admin = _proxy_default_is_admin(user_db)

        # Create or update session
        previous_username = session.get("user_id")
        if previous_username and previous_username != username:
            # Header identity changed mid-session; force reprovision for the new user.
            session.pop("db_user_id", None)

        session["user_id"] = username
        session["is_admin"] = is_admin

        # Provision proxy-authenticated users into users.db for multi-user features.
        # Re-provision when db_user_id is missing/stale/mismatched to avoid broken
        # sessions after DB resets or auth-mode transitions.
        if user_db is not None:
            raw_db_user_id = session.get("db_user_id")
            session_db_user = None

            if raw_db_user_id is not None:
                try:
                    session_db_user = user_db.get_user_cached(user_id=int(raw_db_user_id))
                except TypeError, ValueError:
                    session_db_user = None

            session_db_username = (
                str(session_db_user.get("username") or "").strip() if session_db_user else ""
            )
            needs_db_user_sync = (
                raw_db_user_id is None or session_db_user is None or session_db_username != username
            )

            if needs_db_user_sync:
                role = "admin" if is_admin else "user"
                db_user, _ = upsert_external_user(
                    user_db,
                    auth_source="proxy",
                    username=username,
                    role=role,
                    collision_strategy="takeover",
                    context="proxy_request",
                )
                if db_user is None:
                    _raise_runtime_error("Unexpected proxy user sync result: no user returned")

                session["db_user_id"] = db_user["id"]

        session.permanent = False
    except _OPERATIONAL_ERRORS:
        logger.exception("Proxy auth middleware error")
        return jsonify({"error": "Authentication error"}), 500
    else:
        return None


register_security_headers(app)


@app.after_request
def strip_cookie_for_api_key_requests(response: Response) -> Response:
    """Keyed requests never mint or refresh a session cookie, even if a handler dirties the session."""
    if g.get("api_key_auth"):
        # Setting `permanent` mutates the session dict (re-marking it
        # modified), so it must be reset before `modified`, not after.
        session.permanent = False
        session.modified = False
    return response


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


def _parse_search_filters_from_request() -> SearchFilters:
    """Parse direct/source browse filters from query parameters."""
    return SearchFilters(
        isbn=request.args.getlist("isbn"),
        author=request.args.getlist("author"),
        title=request.args.getlist("title"),
        lang=request.args.getlist("lang"),
        sort=request.args.get("sort"),
        content=request.args.getlist("content"),
        format=request.args.getlist("format"),
    )


def _build_source_query_book(query_text: str, filters: SearchFilters) -> BookMetadata:
    """Build a synthetic book context for source-native browse searches."""
    from shelfmark.metadata_providers import BookMetadata

    author_values = [value.strip() for value in (filters.author or []) if str(value).strip()]
    title_values = [value.strip() for value in (filters.title or []) if str(value).strip()]
    isbn_values = [value.strip() for value in (filters.isbn or []) if str(value).strip()]
    title = (
        title_values[0]
        if title_values
        else query_text
        or (isbn_values[0] if isbn_values else "")
        or (author_values[0] if author_values else "Direct Search")
    )
    author = author_values[0] if author_values else ""

    return BookMetadata(
        provider="manual",
        provider_id=query_text or title,
        provider_display_name="Manual Search",
        title=title,
        search_title=title,
        search_author=author or None,
        authors=author_values,
    )


def _serialize_browse_record(record: BrowseRecord) -> dict:
    """Serialize a source-native browse record for the frontend."""
    result = {key: value for key, value in record.__dict__.items() if value is not None}

    preview = result.get("preview")
    if isinstance(preview, str) and preview:
        from shelfmark.core.utils import transform_cover_url

        result["preview"] = transform_cover_url(preview, record.id)

    return result


def _serialize_release(release: Release) -> dict:
    """Serialize a release for the frontend, normalizing preview URLs."""
    from dataclasses import asdict

    from shelfmark.core.utils import transform_cover_url

    result = asdict(release)
    extra = result.get("extra")
    if isinstance(extra, dict):
        preview = extra.get("preview")
        if isinstance(preview, str) and preview:
            extra = dict(extra)
            extra["preview"] = transform_cover_url(preview, release.source_id)
            result["extra"] = extra

    return result


register_release_inspect_routes(app, login_required, policy_gate=_release_payload_listing_gate)


@app.route("/api/releases/download", methods=["POST"])
@login_required
def api_download_release() -> Response | tuple[Response, int]:
    """Queue a release for download.

    This endpoint is used when downloading from the ReleaseModal where the
    frontend already has all the release data from the search results.

    Request Body (JSON):
        source (str): Release source (e.g., "direct_download")
        source_id (str): ID within the source (e.g., AA MD5 hash)
        title (str): Book title
        format (str, optional): File format
        size (str, optional): Human-readable size
        extra (dict, optional): Additional metadata

    Returns:
        flask.Response: JSON status object indicating success or failure.

    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "No data provided"}), 400

        if "source_id" not in data:
            return jsonify({"error": "source_id is required"}), 400
        if "source" not in data:
            return jsonify({"error": "source is required"}), 400

        source = data["source"]
        resolved_content_type, inferred_content_type = _resolve_release_content_type(data, source)
        policy_mode = _resolve_policy_mode_for_current_user(
            source=source,
            content_type=resolved_content_type,
        )
        if policy_mode is not None and policy_mode != PolicyMode.DOWNLOAD:
            return _policy_block_response(policy_mode)

        release_payload = data
        if inferred_content_type and data.get("content_type") is None:
            release_payload = dict(data)
            release_payload["content_type"] = resolved_content_type

        logger.info(
            "Download request received. keys=%s downloads=%s extra.downloads=%s",
            list(data.keys()),
            data.get("downloads"),
            data.get("extra", {}).get("downloads") if isinstance(data.get("extra"), dict) else None,
        )

        priority = data.get("priority", 0)
        # Per-user download overrides
        db_user_id = session.get("db_user_id")
        _username = session.get("user_id")
        db_user_id, _username, on_behalf_error = _resolve_download_user_context(
            db_user_id,
            _username,
            data.get("on_behalf_of_user_id"),
        )
        if on_behalf_error:
            return on_behalf_error
        success, error_msg = backend.queue_release(
            release_payload,
            priority,
            user_id=db_user_id,
            username=_username,
        )

        if success:
            return jsonify({"status": "queued", "priority": priority})
        return jsonify({"error": error_msg or "Failed to queue release"}), 500
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Release download error: {e}")
        return jsonify({"error": str(e)}), 500


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


def _queue_status_to_final_activity_status(status: QueueStatus) -> str | None:
    return status.value if status in TERMINAL_QUEUE_STATUSES else None


def _queue_status_to_notification_event(status: QueueStatus) -> NotificationEvent | None:
    if status == QueueStatus.COMPLETE:
        return NotificationEvent.DOWNLOAD_COMPLETE
    if status == QueueStatus.ERROR:
        return NotificationEvent.DOWNLOAD_FAILED
    return None


def _notify_admin_for_terminal_download_status(
    *, task_id: str, status: QueueStatus, task: Any
) -> None:
    event = _queue_status_to_notification_event(status)
    if event is None:
        return

    raw_owner_user_id = getattr(task, "user_id", None)
    try:
        owner_user_id = int(raw_owner_user_id) if raw_owner_user_id is not None else None
    except TypeError, ValueError:
        owner_user_id = None

    content_type = normalize_optional_text(getattr(task, "content_type", None))
    context = NotificationContext(
        event=event,
        title=str(getattr(task, "title", "Unknown title") or "Unknown title"),
        author=str(getattr(task, "author", "Unknown author") or "Unknown author"),
        username=normalize_optional_text(getattr(task, "username", None)),
        content_type=normalize_content_type(content_type) if content_type is not None else None,
        format=normalize_optional_text(getattr(task, "format", None)),
        source=normalize_source(getattr(task, "source", None)),
        error_message=(
            normalize_optional_text(getattr(task, "status_message", None))
            if event == NotificationEvent.DOWNLOAD_FAILED
            else None
        ),
    )
    try:
        notify_admin(event, context)
    except (RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Failed to trigger admin notification for download %s (%s): %s",
            task_id,
            status.value,
            exc,
        )
    if owner_user_id is None:
        return
    try:
        notify_user(owner_user_id, event, context)
    except (RuntimeError, TypeError, ValueError) as exc:
        logger.warning(
            "Failed to trigger user notification for download %s (%s, user_id=%s): %s",
            task_id,
            status.value,
            owner_user_id,
            exc,
        )


def _emit_activity_update_for_task(*, payload: dict[str, Any], task: Any) -> None:
    owner_user_id = normalize_positive_int(getattr(task, "user_id", None))
    emit_ws_event(
        ws_manager,
        event_name="activity_update",
        room="admins",
        payload=payload,
    )
    if owner_user_id is None:
        return
    # The owner knows a request-linked download by its opaque id, as /api/status
    # shows it, since the task id can carry a private tracker's passkey (#1418).
    task_id = payload.get("task_id")
    if isinstance(task_id, str):
        payload = {
            **payload,
            "task_id": viewer_download_id(task_id, getattr(task, "request_id", None)),
        }
    emit_ws_event(
        ws_manager,
        event_name="activity_update",
        room=f"user_{owner_user_id}",
        payload=payload,
    )


def _record_download_queued(task_id: str, task: Any) -> None:
    """Persist initial download record when a task enters the queue."""
    if download_history_service is None:
        return

    owner_user_id = normalize_positive_int(getattr(task, "user_id", None))
    request_id = normalize_positive_int(getattr(task, "request_id", None))
    origin = "requested" if request_id else "direct"

    source_name = normalize_source(getattr(task, "source", None))
    source_display = get_source_display_name(source_name)

    try:
        download_history_service.record_download(
            task_id=task_id,
            user_id=owner_user_id,
            username=normalize_optional_text(getattr(task, "username", None)),
            request_id=request_id,
            source=source_name,
            source_display_name=source_display,
            title=str(getattr(task, "title", "Unknown title") or "Unknown title"),
            author=normalize_optional_text(getattr(task, "author", None)),
            file_format=normalize_optional_text(getattr(task, "format", None)),
            size=normalize_optional_text(getattr(task, "size", None)),
            preview=normalize_optional_text(getattr(task, "preview", None)),
            content_type=normalize_optional_text(getattr(task, "content_type", None)),
            downloads=getattr(task, "downloads", None),
            origin=origin,
            retry_payload=backend.serialize_task_for_retry(task),
        )
    except _OPERATIONAL_ERRORS as exc:
        logger.warning("Failed to record download at queue time for task %s: %s", task_id, exc)
        return

    if activity_view_state_service is None:
        return

    try:
        cleared_view_state = 0
        cleared_view_state += activity_view_state_service.clear_item_for_all_viewers(
            item_type="download",
            item_key=f"download:{task_id}",
        )
        if request_id is not None:
            cleared_view_state += activity_view_state_service.clear_item_for_all_viewers(
                item_type="request",
                item_key=f"request:{request_id}",
            )
        if cleared_view_state > 0:
            _emit_activity_update_for_task(
                task=task,
                payload={
                    "kind": "activity_reset",
                    "task_id": task_id,
                },
            )
    except _OPERATIONAL_ERRORS as exc:
        logger.warning("Failed to reset activity viewer state for task %s: %s", task_id, exc)


def _record_download_terminal_snapshot(task_id: str, status: QueueStatus, task: Any) -> None:
    _notify_admin_for_terminal_download_status(task_id=task_id, status=status, task=task)

    final_status = _queue_status_to_final_activity_status(status)
    if final_status is None:
        return

    finalized_download = False
    if download_history_service is not None:
        try:
            download_history_service.finalize_download(
                task_id=task_id,
                final_status=final_status,
                status_message=normalize_optional_text(getattr(task, "status_message", None)),
                download_path=normalize_optional_text(getattr(task, "download_path", None)),
                retry_payload=backend.serialize_task_for_retry(task),
            )
            finalized_download = True
        except _OPERATIONAL_ERRORS as exc:
            logger.warning("Failed to finalize download history for task %s: %s", task_id, exc)

    if finalized_download:
        _emit_activity_update_for_task(
            task=task,
            payload={
                "kind": "download_terminal",
                "task_id": task_id,
                "status": final_status,
            },
        )

    if user_db is None or status != QueueStatus.ERROR:
        return

    request_id = normalize_positive_int(getattr(task, "request_id", None))
    if request_id is None:
        return
    if backend.can_retry_download_task(task, status):
        return

    raw_error_message = getattr(task, "status_message", None)
    fallback_reason = (
        raw_error_message.strip()
        if isinstance(raw_error_message, str) and raw_error_message.strip()
        else "Download failed"
    )
    try:
        reopened_request = reopen_failed_request(
            user_db,
            request_id=request_id,
            failure_reason=fallback_reason,
        )
        if reopened_request is not None:
            if activity_view_state_service is not None:
                activity_view_state_service.clear_item_for_all_viewers(
                    item_type="request",
                    item_key=f"request:{request_id}",
                )
            _emit_request_update_events([reopened_request])
    except _OPERATIONAL_ERRORS as exc:
        logger.warning(
            "Failed to reopen request %s after terminal download error %s: %s",
            request_id,
            task_id,
            exc,
        )


backend.book_queue.set_queue_hook(_record_download_queued)
backend.book_queue.set_terminal_status_hook(_record_download_terminal_snapshot)


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


@app.route("/api/releases", methods=["GET"])
@login_required
def api_releases() -> Response | tuple[Response, int]:
    """Search for downloadable releases of a book.

    This endpoint takes book metadata and searches available release sources
    (e.g., Anna's Archive, Libgen) for downloadable files.

    Query Parameters:
        provider (str): Metadata provider name (required)
        book_id (str): Book ID from metadata provider (required)
        source (str): Release source to search (optional, default: all)

    Returns:
        flask.Response: JSON with list of available releases.

    """
    try:
        from dataclasses import asdict

        from shelfmark.core.release_search import search_source_releases
        from shelfmark.metadata_providers import (
            BookMetadata,
            get_provider,
            get_provider_kwargs,
            is_provider_registered,
        )
        from shelfmark.release_sources import (
            apply_column_visibility,
            browse_record_to_book_metadata,
            get_source,
            list_available_sources,
            serialize_column_config,
            source_results_are_releases,
        )

        def _search_source_releases(
            source_name: str, search_book: BookMetadata
        ) -> tuple[Any | None, list[Any], str | None]:
            """Search one source and return any error message instead of raising."""
            return search_source_releases(
                source_name,
                search_book,
                languages=(browse_filters.lang if source_query_filters is not None else languages),
                manual_query=(query_text if source_query_filters is not None else manual_query),
                indexers=indexers,
                expand_search=expand_search,
                content_type=content_type,
                source_filters=source_query_filters,
                user_id=db_user_id,
                include_error_detail=_resolve_status_scope(require_authenticated=False)[0],
            )

        provider = request.args.get("provider", "").strip()
        book_id = request.args.get("book_id", "").strip()
        source_filter = request.args.get("source", "").strip()
        query_text = request.args.get("query", "").strip()
        # Accept title/author from frontend to avoid re-fetching metadata
        title_param = request.args.get("title", "").strip()
        author_param = request.args.get("author", "").strip()
        expand_search = request.args.get("expand_search", "").lower() == "true"
        # Accept language codes for filtering (comma-separated)
        languages_param = request.args.get("languages", "").strip()
        languages = (
            [lang.strip() for lang in languages_param.split(",") if lang.strip()]
            if languages_param
            else None
        )
        # Without an explicit filter the plan falls back to this user's default languages.
        db_user_id = get_session_db_user_id(session)
        # Content type for audiobook vs ebook search
        content_type = request.args.get("content_type", "ebook").strip()

        manual_query = request.args.get("manual_query", "").strip()

        # Accept indexer names for Prowlarr filtering (comma-separated)
        indexers_param = request.args.get("indexers", "").strip()
        indexers = (
            [idx.strip() for idx in indexers_param.split(",") if idx.strip()]
            if indexers_param
            else None
        )
        browse_filters = _parse_search_filters_from_request()
        has_browse_filters = bool(query_text or any(vars(browse_filters).values()))

        source_query_filters = None
        is_source_provider = bool(provider) and source_results_are_releases(provider)

        if not provider or not book_id:
            if not source_filter or not has_browse_filters:
                return jsonify({"error": "Parameters 'provider' and 'book_id' are required"}), 400
            if not source_results_are_releases(source_filter):
                return jsonify(
                    {"error": f"Source does not support browse release search: {source_filter}"}
                ), 400
            source_query_filters = browse_filters

        # Determine which release sources to search
        if source_filter:
            sources_to_search = [source_filter]
        elif is_source_provider:
            # Source-backed browse flows stay within the source that produced the record.
            sources_to_search = [provider]
        else:
            # Search only enabled sources
            sources_to_search = [src["name"] for src in list_available_sources() if src["enabled"]]

        # The policy gate comes before anything is asked of a source: listing goes
        # through the shared source credentials, so a user whose policy never shows
        # a release list (request_book, blocked) must not reach them.
        sources_to_search, refused_mode = _sources_allowed_for_listing(
            sources_to_search, content_type
        )
        if not sources_to_search and refused_mode is not None:
            return _release_listing_block_response(refused_mode)

        book: BookMetadata

        if source_query_filters is not None:
            book = _build_source_query_book(query_text, browse_filters)
        elif is_source_provider:
            # Source-backed browse flows can reopen the release modal with provider=<source name>.
            # In that flow, treat the source-native record as release-search context instead of
            # requiring a metadata provider registration.
            # A source named in `source` was gated above. When provider names another one,
            # its record lookup needs the refusal the record route applies.
            if normalize_source(provider) not in {normalize_source(s) for s in sources_to_search}:
                refused_record_mode = _release_source_record_refusal(provider)
                if refused_record_mode is not None:
                    return _release_listing_block_response(refused_record_mode)
            source = get_source(provider)
            direct_record = source.get_record(book_id)
            if direct_record is None:
                return jsonify({"error": "Book not found in release source"}), 404

            book = browse_record_to_book_metadata(
                direct_record,
                title_override=title_param or None,
                author_override=author_param or None,
            )
        elif provider == "manual":
            resolved_title = title_param or manual_query or "Manual Search"
            resolved_author = author_param or ""
            # The release modal sends `authors.join(', ')` as `author`, so the commas here
            # are joins between contributors, not part of one name. This split is the only
            # place that knows that, so `search_author` comes from it rather than from the
            # joined text - see issue #1252.
            authors = [a.strip() for a in resolved_author.split(",") if a.strip()]

            book = BookMetadata(
                provider="manual",
                provider_id=book_id,
                provider_display_name="Manual Search",
                title=resolved_title,
                search_title=resolved_title,
                search_author=authors[0] if authors else None,
                authors=authors,
            )
        else:
            if not is_provider_registered(provider):
                return jsonify({"error": f"Unknown metadata provider: {provider}"}), 400

            # Get book metadata from provider
            kwargs = get_provider_kwargs(provider)
            prov = get_provider(provider, **kwargs)
            resolved_book = prov.get_book(book_id)

            if not resolved_book:
                return jsonify({"error": "Book not found in metadata provider"}), 404
            book = resolved_book

            # Override title from frontend if available (search results may have better data)
            # Note: We intentionally DON'T override authors here - get_book() now returns
            # filtered authors (primary authors only, excluding translators/narrators),
            # which gives better release search results than the unfiltered search data
            if title_param:
                if title_param != book.title:
                    # A different title is one the search result carried and the book
                    # record does not, typically a translation's ("Kinder des Nebels" for
                    # "Mistborn: The Final Empire"). The provider's search_title derives
                    # from the original and would otherwise win the release search.
                    book.search_title = title_param
                book.title = title_param

        # Search each source for releases.
        #
        # Under a wall-clock budget: this endpoint is synchronous, and the bypass path it
        # can reach used to be allowed minutes per URL with nothing bounding the request
        # as a whole. A search that ran into an unsolvable protection challenge therefore
        # outlived every reverse proxy in front of it and surfaced to the user as
        # "Server unavailable (504)" - a gateway timeout that blames their proxy for a
        # challenge failure. The budget is shared across sources, so a stuck first source
        # cannot spend the whole request on its own. See issue #1276.
        all_releases = []
        errors = []
        source_instances = {}  # Keep source instances for column config

        # A real search is under way, so a warm-up still sitting on its start-up delay
        # should stand down rather than queue its throwaway solve in front of this one.
        warmup.note_user_search()

        with search_deadline.search_deadline():
            for index, source_name in enumerate(sources_to_search):
                if search_deadline.expired():
                    logger.warning("Release search budget spent; %s not searched", source_name)
                    errors.append(f"{source_name}: {search_deadline.deadline_message()}")
                    continue

                with search_deadline.unit_deadline(waiting=len(sources_to_search) - index - 1):
                    source, releases, error = _search_source_releases(source_name, book)
                if source is not None:
                    source_instances[source_name] = source
                    all_releases.extend(releases)
                if error is not None:
                    errors.append(error)

        # Convert Release objects to dicts
        releases_data = [_serialize_release(release) for release in all_releases]

        # Get column config from the first source searched
        # Reuse the same instance to get any dynamic data (e.g., online_servers for IRC)
        column_config = None
        if sources_to_search and sources_to_search[0] in source_instances:
            try:
                first_source = source_instances[sources_to_search[0]]
                column_config = serialize_column_config(
                    apply_column_visibility(
                        first_source.get_column_config(),
                        content_type=normalize_content_type(content_type),
                        is_setting_enabled=lambda key: bool(
                            app_config.get(key, True, user_id=db_user_id)
                        ),
                    )
                )
            except _OPERATIONAL_ERRORS as e:
                logger.warning("Failed to get column config: %s", e)

        # Convert book to dict and transform cover_url
        book_dict = asdict(book)
        _attach_library_ownership(book, book_dict)
        from shelfmark.core.utils import transform_cover_url

        if book_dict.get("cover_url"):
            cache_id = f"{provider}_{book_id}"
            book_dict["cover_url"] = transform_cover_url(book_dict["cover_url"], cache_id)

        search_info = {}
        for source_name, source_instance in source_instances.items():
            info: dict[str, str | int | None] = {}
            if hasattr(source_instance, "last_search_type") and source_instance.last_search_type:
                info["search_type"] = source_instance.last_search_type
            if hasattr(source_instance, "total_results"):
                info["total_results"] = source_instance.total_results
            if info:
                search_info[source_name] = info

        response = {
            "releases": releases_data,
            "book": book_dict,
            "sources_searched": sources_to_search,
            "column_config": column_config,
            "search_info": search_info,
        }

        if errors:
            response["errors"] = errors

        # If no releases found and there were errors, return 503 with the first
        # source failure message so direct-mode source query searches surface the
        # same unavailable-state messaging as release modal searches.
        if not releases_data and errors:
            # Use the first error message (typically the most relevant)
            error_message = errors[0]
            # Strip the source prefix if present (e.g., "direct_download: message" -> "message")
            if ": " in error_message:
                error_message = error_message.split(": ", 1)[1]
            return jsonify({"error": error_message}), 503

        return jsonify(response)
    except SourceUnavailableError as e:
        logger.warning("Release search unavailable: %s", e)
        return jsonify({"error": str(e)}), 503
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Releases search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/release-sources", methods=["GET"])
@login_required
def api_release_sources() -> Response | tuple[Response, int]:
    """Get available release sources from the plugin registry.

    Returns:
        flask.Response: JSON list of available release sources.

    """
    try:
        from shelfmark.release_sources import list_available_sources

        sources = list_available_sources()
        return jsonify(sources)
    except _IMPORT_OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Release sources error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/release-sources/<source_name>/records/<path:record_id>", methods=["GET"])
@login_required
def api_release_source_record(source_name: str, record_id: str) -> Response | tuple[Response, int]:
    """Resolve a source-native browse record for a release source."""
    try:
        from shelfmark.release_sources import get_source

        source = get_source(source_name)
        refused_mode = _release_source_record_refusal(source_name)
        if refused_mode is not None:
            return _release_listing_block_response(refused_mode)
        record = source.get_record(record_id)
        if record is None:
            return jsonify({"error": "Record not found"}), 404
        return jsonify(_serialize_browse_record(record))
    except ValueError:
        return jsonify({"error": f"Unknown release source: {source_name}"}), 400
    except SourceUnavailableError as e:
        logger.warning("Release source record unavailable: %s", e)
        return jsonify({"error": str(e)}), 503
    except _OPERATIONAL_ERRORS as e:
        logger.error_trace(f"Release source record error: {e}")
        return jsonify({"error": str(e)}), 500


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
