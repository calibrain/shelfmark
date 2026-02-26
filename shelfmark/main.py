"""Flask app - routes, WebSocket handlers, and middleware."""

import io
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Dict, Tuple, Union

from flask import Flask, jsonify, request, send_file, send_from_directory, session
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash
from werkzeug.wrappers import Response

from shelfmark.download import orchestrator as backend
from shelfmark.release_sources.direct_download import SearchUnavailable
from shelfmark.config.settings import _SUPPORTED_BOOK_LANGUAGE
from shelfmark.config.env import (
    BUILD_VERSION, CONFIG_DIR, CWA_DB_PATH, DEBUG, HIDE_LOCAL_AUTH,
    FLASK_HOST, FLASK_PORT, OIDC_AUTO_REDIRECT, RELEASE_VERSION,
    _is_config_dir_writable,
)
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import SearchFilters, QueueStatus
from shelfmark.core.prefix_middleware import PrefixMiddleware
from shelfmark.core.auth_modes import (
    determine_auth_mode,
    get_auth_check_admin_status,
    has_local_password_admin,
    is_settings_or_onboarding_path,
    requires_admin_for_settings_access,
)
from shelfmark.core.cwa_user_sync import upsert_cwa_user
from shelfmark.core.external_user_linking import upsert_external_user
from shelfmark.core.request_policy import (
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
from shelfmark.core.activity_service import ActivityService, build_download_item_key
from shelfmark.core.notifications import NotificationContext, NotificationEvent, notify_admin, notify_user
from shelfmark.core.utils import normalize_base_path
from shelfmark.api.websocket import ws_manager

logger = setup_logger(__name__)

# Project root is one level up from this package
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIST = os.path.join(PROJECT_ROOT, 'frontend-dist')

BASE_PATH = normalize_base_path(app_config.get("URL_BASE", ""))

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching
app.config['APPLICATION_ROOT'] = BASE_PATH or '/'
app.wsgi_app = ProxyFix(app.wsgi_app)  # type: ignore
if BASE_PATH:
    app.wsgi_app = PrefixMiddleware(app.wsgi_app, BASE_PATH, bypass_paths={"/api/health"})

# Socket.IO async mode.
# We run this app under Gunicorn with a gevent websocket worker (even when DEBUG=true),
# so Socket.IO should always use gevent here.
async_mode = 'gevent'

# Initialize Flask-SocketIO with reverse proxy support
socketio_path = f"{BASE_PATH}/socket.io" if BASE_PATH else "/socket.io"
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode=async_mode,
    logger=False,
    engineio_logger=False,
    # Reverse proxy / Traefik compatibility settings
    path=socketio_path,
    ping_timeout=60,  # Time to wait for pong response
    ping_interval=25,  # Send ping every 25 seconds
    # Allow both websocket and polling for better compatibility
    transports=['websocket', 'polling'],
    # Enable CORS for all origins (you can restrict this in production)
    allow_upgrades=True,
    # Important for proxies that buffer
    http_compression=True
)

# Initialize WebSocket manager
ws_manager.init_app(app, socketio)
ws_manager.set_queue_status_fn(backend.queue_status)
logger.info(f"Flask-SocketIO initialized with async_mode='{async_mode}'")

# Ensure all plugins are loaded before starting the download coordinator.
# This prevents a race condition where the download loop could try to process
# a queued task before its handler (e.g., prowlarr) is registered.
try:
    import shelfmark.metadata_providers  # noqa: F401
    import shelfmark.release_sources  # noqa: F401
    logger.debug("Plugin modules loaded successfully")
except ImportError as e:
    logger.warning(f"Failed to import plugin modules: {e}")

# Migrate legacy security settings if needed
from shelfmark.config.security import _migrate_security_settings
_migrate_security_settings()

# Initialize user database and register multi-user routes
# If CONFIG_DIR doesn't exist or is read-only, multi-user features will be disabled
import os as _os
from shelfmark.core.user_db import UserDB
_user_db_path = _os.path.join(_os.environ.get("CONFIG_DIR", "/config"), "users.db")
user_db: UserDB | None = None
activity_service: ActivityService | None = None
try:
    user_db = UserDB(_user_db_path)
    user_db.initialize()
    activity_service = ActivityService(_user_db_path)
    import shelfmark.config.users_settings as _  # noqa: F401 - registers users tab
    from shelfmark.core.oidc_routes import register_oidc_routes
    from shelfmark.core.admin_routes import register_admin_routes
    from shelfmark.core.self_user_routes import register_self_user_routes
    register_oidc_routes(app, user_db)
    register_admin_routes(app, user_db)
    register_self_user_routes(app, user_db)
except (sqlite3.OperationalError, OSError) as e:
    logger.warning(
        f"User database initialization failed: {e}. "
        f"Multi-user authentication features will be disabled. "
        f"Ensure CONFIG_DIR ({_os.environ.get('CONFIG_DIR', '/config')}) exists and is writable."
    )
    user_db = None

# Start download coordinator
backend.start()

# Rate limiting for login attempts
# Structure: {username: {'count': int, 'lockout_until': datetime}}
failed_login_attempts: Dict[str, Dict[str, Any]] = {}
MAX_LOGIN_ATTEMPTS = 10
LOCKOUT_DURATION_MINUTES = 30

def cleanup_old_lockouts() -> None:
    """Remove expired lockout entries to prevent memory buildup."""
    current_time = datetime.now()
    expired_users = [
        username for username, data in failed_login_attempts.items()
        if 'lockout_until' in data and data['lockout_until'] < current_time
    ]
    for username in expired_users:
        logger.info(f"Lockout expired for user: {username}")
        del failed_login_attempts[username]

def is_account_locked(username: str) -> bool:
    """Check if an account is currently locked due to failed login attempts."""
    cleanup_old_lockouts()

    if username not in failed_login_attempts:
        return False

    lockout_until = failed_login_attempts[username].get('lockout_until')
    return lockout_until is not None and datetime.now() < lockout_until

def record_failed_login(username: str, ip_address: str) -> bool:
    """Record a failed login attempt and lock account if threshold is reached.

    Returns True if account is now locked, False otherwise.
    """
    if username not in failed_login_attempts:
        failed_login_attempts[username] = {'count': 0}

    failed_login_attempts[username]['count'] += 1
    count = failed_login_attempts[username]['count']

    logger.warning(f"Failed login attempt {count}/{MAX_LOGIN_ATTEMPTS} for user '{username}' from IP {ip_address}")

    if count >= MAX_LOGIN_ATTEMPTS:
        lockout_until = datetime.now() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
        failed_login_attempts[username]['lockout_until'] = lockout_until
        logger.warning(f"Account locked for user '{username}' until {lockout_until.strftime('%Y-%m-%d %H:%M:%S')} due to {count} failed login attempts")
        return True

    return False

def clear_failed_logins(username: str) -> None:
    """Clear failed login attempts for a user after successful login."""
    if username in failed_login_attempts:
        del failed_login_attempts[username]
        logger.debug(f"Cleared failed login attempts for user: {username}")


def get_client_ip() -> str:
    """Extract client IP address from request, handling reverse proxy forwarding."""
    ip_address = request.headers.get('X-Forwarded-For', request.remote_addr) or 'unknown'
    # X-Forwarded-For can contain multiple IPs, take the first one
    if ',' in ip_address:
        ip_address = ip_address.split(',')[0].strip()
    return ip_address


def get_auth_mode() -> str:
    """Determine which authentication mode is active.

    Uses configured AUTH_METHOD plus runtime prerequisites.
    Returns "none" when config is invalid or unavailable.
    """
    from shelfmark.core.settings_registry import load_config_file

    try:
        security_config = load_config_file("security")
        return determine_auth_mode(
            security_config,
            CWA_DB_PATH,
            has_local_admin=has_local_password_admin(user_db),
        )
    except Exception:
        return "none"


def _load_users_request_policy_settings() -> dict[str, Any]:
    """Load global request policy settings from users config."""
    from shelfmark.core.settings_registry import load_config_file

    return load_config_file("users")


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


_AUDIOBOOK_CATEGORY_RANGE = (3030, 3049)
_AUDIOBOOK_FORMAT_HINTS = frozenset(
    {
        "m4b",
        "mp3",
        "m4a",
        "flac",
        "ogg",
        "wma",
        "aac",
        "wav",
        "opus",
    }
)


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
            except (TypeError, ValueError):
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
    if session.get("is_admin", True):
        return None
    if user_db is None:
        return None

    global_settings = _load_users_request_policy_settings()
    db_user_id = session.get("db_user_id")
    user_settings: dict[str, Any] | None = None
    if db_user_id is not None:
        try:
            user_settings = user_db.get_user_settings(int(db_user_id))
        except (TypeError, ValueError):
            user_settings = None

    effective = merge_request_policy_settings(global_settings, user_settings)
    if not _as_bool(effective.get("REQUESTS_ENABLED"), False):
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


def _policy_block_response(mode: PolicyMode):
    logger.debug(
        "download policy guard user=%s db_user_id=%s mode=%s",
        session.get("user_id"),
        session.get("db_user_id"),
        mode.value,
    )
    if mode == PolicyMode.BLOCKED:
        return (
            jsonify({
                "error": "Download not allowed by policy",
                "code": "policy_blocked",
                "required_mode": PolicyMode.BLOCKED.value,
            }),
            403,
        )
    return (
        jsonify({
            "error": "Download not allowed by policy",
            "code": "policy_requires_request",
            "required_mode": mode.value,
        }),
        403,
    )


if user_db is not None:
    try:
        from shelfmark.core.request_routes import register_request_routes
        from shelfmark.core.activity_routes import register_activity_routes

        register_request_routes(
            app,
            user_db,
            resolve_auth_mode=lambda: get_auth_mode(),
            queue_release=lambda *args, **kwargs: backend.queue_release(*args, **kwargs),
            activity_service=activity_service,
            ws_manager=ws_manager,
        )
        if activity_service is not None:
            register_activity_routes(
                app,
                user_db,
                activity_service=activity_service,
                resolve_auth_mode=lambda: get_auth_mode(),
                resolve_status_scope=lambda: _resolve_status_scope(),
                queue_status=lambda user_id=None: backend.queue_status(user_id=user_id),
                sync_request_delivery_states=sync_delivery_states_from_queue_status,
                emit_request_updates=lambda rows: _emit_request_update_events(rows),
                ws_manager=ws_manager,
            )
    except Exception as e:
        logger.warning(f"Failed to register request routes: {e}")


# Enable CORS in development mode for local frontend development
if DEBUG:
    CORS(app, resources={
        r"/*": {
            "origins": ["http://localhost:5173", "http://127.0.0.1:5173"],
            "supports_credentials": True,
            "allow_headers": ["Content-Type", "Authorization"],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
        }
    })

# Custom log filter to exclude routine status endpoint polling and WebSocket noise
class LogNoiseFilter(logging.Filter):
    """Filter out routine status endpoint requests and WebSocket upgrade errors to reduce log noise.

    WebSocket upgrade errors are benign - Flask-SocketIO automatically falls back to polling transport.
    The error occurs because Werkzeug's built-in server doesn't fully support WebSocket upgrades.
    """
    def filter(self, record):
        message = record.getMessage() if hasattr(record, 'getMessage') else str(record.msg)

        # Exclude GET /api/status requests (polling noise)
        if 'GET /api/status' in message:
            return False

        # Exclude WebSocket upgrade errors (benign - falls back to polling)
        if 'write() before start_response' in message:
            return False

        # Exclude the Error on request line that precedes WebSocket errors
        if record.levelno == logging.ERROR:
            if 'Error on request:' in message:
                return False
            # Filter WebSocket-related AssertionError tracebacks
            if hasattr(record, 'exc_info') and record.exc_info:
                exc_type, exc_value = record.exc_info[0], record.exc_info[1]
                if exc_type and exc_type.__name__ == 'AssertionError':
                    if exc_value and 'write() before start_response' in str(exc_value):
                        return False

        return True

# Flask logger
app.logger.handlers = logger.handlers
app.logger.setLevel(logger.level)
# Also handle Werkzeug's logger
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.handlers = logger.handlers
werkzeug_logger.setLevel(logger.level)
# Add filter to suppress routine status endpoint polling logs and WebSocket upgrade errors
werkzeug_logger.addFilter(LogNoiseFilter())

# Set up authentication defaults
# The secret key will reset every time we restart, which will
# require users to authenticate again
from shelfmark.config.env import SESSION_COOKIE_NAME, SESSION_COOKIE_SECURE_ENV, string_to_bool

SESSION_COOKIE_SECURE = string_to_bool(SESSION_COOKIE_SECURE_ENV)

app.config.update(
    SECRET_KEY = os.urandom(64),
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = 'Lax',
    SESSION_COOKIE_SECURE = SESSION_COOKIE_SECURE,
    SESSION_COOKIE_NAME = SESSION_COOKIE_NAME,
    PERMANENT_SESSION_LIFETIME = 604800  # 7 days in seconds
)

logger.info(f"Session cookie secure setting: {SESSION_COOKIE_SECURE} (from env: {SESSION_COOKIE_SECURE_ENV})")
logger.info(f"Session cookie name: {SESSION_COOKIE_NAME}")

@app.before_request
def proxy_auth_middleware():
    """
    Middleware to handle proxy authentication.
    
    When AUTH_METHOD is set to "proxy", this middleware automatically
    authenticates users based on headers set by the reverse proxy.
    """
    auth_mode = get_auth_mode()
    
    # Only run for proxy auth mode
    if auth_mode != "proxy":
        return None
    
    # Skip for public endpoints that don't need auth
    if request.path == '/api/health':
        return None

    from shelfmark.core.settings_registry import load_config_file

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
        security_config = load_config_file("security")
        user_header = security_config.get("PROXY_AUTH_USER_HEADER", "X-Auth-User")

        # Extract username from proxy header
        username = get_proxy_header(user_header)

        if not username:
            if request.path.startswith('/api/auth/'):
                return None

            logger.warning(f"Proxy auth enabled but no username found in header '{user_header}'")
            return jsonify({"error": "Authentication required. Proxy header not set."}), 401
        
        # Resolve admin role for proxy sessions.
        # If an admin group is configured, derive from groups header.
        # Otherwise preserve existing DB role for known users and default
        # first-time users to admin (to avoid lockouts).
        admin_group_header = security_config.get("PROXY_AUTH_ADMIN_GROUP_HEADER", "X-Auth-Groups")
        admin_group_name = str(security_config.get("PROXY_AUTH_ADMIN_GROUP_NAME", "") or "").strip()
        is_admin = True

        if admin_group_name:
            groups_header = get_proxy_header(admin_group_header) or ""
            user_groups_delimiter = "," if "," in groups_header else "|"
            user_groups = [g.strip() for g in groups_header.split(user_groups_delimiter) if g.strip()]
            is_admin = admin_group_name in user_groups
        elif user_db is not None:
            existing_db_user = user_db.get_user(username=username)
            if existing_db_user:
                is_admin = existing_db_user.get("role") == "admin"
        
        # Create or update session
        previous_username = session.get('user_id')
        if previous_username and previous_username != username:
            # Header identity changed mid-session; force reprovision for the new user.
            session.pop('db_user_id', None)

        session['user_id'] = username
        session['is_admin'] = is_admin

        # Provision proxy-authenticated users into users.db for multi-user features.
        # Re-provision when db_user_id is missing/stale/mismatched to avoid broken
        # sessions after DB resets or auth-mode transitions.
        if user_db is not None:
            raw_db_user_id = session.get('db_user_id')
            session_db_user = None

            if raw_db_user_id is not None:
                try:
                    session_db_user = user_db.get_user(user_id=int(raw_db_user_id))
                except (TypeError, ValueError):
                    session_db_user = None

            session_db_username = str(session_db_user.get("username") or "").strip() if session_db_user else ""
            needs_db_user_sync = (
                raw_db_user_id is None
                or session_db_user is None
                or session_db_username != username
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
                    raise RuntimeError("Unexpected proxy user sync result: no user returned")

                session['db_user_id'] = db_user["id"]

        session.permanent = False

        return None
        
    except Exception as e:
        logger.error(f"Proxy auth middleware error: {e}")
        return jsonify({"error": "Authentication error"}), 500

@app.after_request
def set_security_headers(response: Response) -> Response:
    """Add baseline security headers to every response."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' ws: wss:; frame-ancestors 'none'",
    )
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Embedder-Policy", "credentialless")
    return response


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_mode = get_auth_mode()

        # If no authentication is configured, allow access
        if auth_mode == "none":
            return f(*args, **kwargs)

        # If CWA mode and database disappeared after startup, return error
        if auth_mode == "cwa" and CWA_DB_PATH and not CWA_DB_PATH.exists():
            logger.error(f"CWA database at {CWA_DB_PATH} is no longer accessible")
            return jsonify({"error": "Internal Server Error"}), 500

        # Check if user has a valid session
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized"}), 401

        # Check admin access for settings/onboarding endpoints.
        if is_settings_or_onboarding_path(request.path):
            from shelfmark.core.settings_registry import load_config_file

            try:
                users_config = load_config_file("users")
                if (
                    requires_admin_for_settings_access(request.path, users_config)
                    and not session.get('is_admin', False)
                ):
                    return jsonify({"error": "Admin access required"}), 403

            except Exception as e:
                logger.error(f"Admin access check error: {e}")
                return jsonify({"error": "Internal Server Error"}), 500

        return f(*args, **kwargs)
    return decorated_function


_BASE_TAG = '<base href="/" data-shelfmark-base />'


def _base_href() -> str:
    if not BASE_PATH:
        return "/"
    return f"{BASE_PATH}/"


def _serve_index_html() -> Response:
    """Serve index.html with an adjusted base tag for subpath deployments."""
    index_path = os.path.join(FRONTEND_DIST, 'index.html')
    try:
        with open(index_path, 'r', encoding='utf-8') as handle:
            html = handle.read()
    except OSError:
        return send_from_directory(FRONTEND_DIST, 'index.html')

    if BASE_PATH and _BASE_TAG in html:
        html = html.replace(_BASE_TAG, f'<base href="{_base_href()}" data-shelfmark-base />', 1)

    return Response(html, mimetype='text/html')


# Serve frontend static files
@app.route('/assets/<path:filename>')
def serve_frontend_assets(filename: str) -> Response:
    """
    Serve static assets from the built frontend.
    """
    return send_from_directory(os.path.join(FRONTEND_DIST, 'assets'), filename)

@app.route('/')
def index() -> Response:
    """
    Serve the React frontend application.
    Authentication is handled by the React app itself.
    """
    return _serve_index_html()

@app.route('/logo.png')
def logo() -> Response:
    """
    Serve logo from built frontend assets.
    """
    return send_from_directory(FRONTEND_DIST, 'logo.png', mimetype='image/png')

@app.route('/favicon.ico')
@app.route('/favico<path:_>')
def favicon(_: Any = None) -> Response:
    """
    Serve favicon from built frontend assets.
    """
    return send_from_directory(FRONTEND_DIST, 'favicon.ico', mimetype='image/vnd.microsoft.icon')

if DEBUG:
    import subprocess

    if app_config.get("USING_EXTERNAL_BYPASSER", False):
        _stop_gui = lambda: None
    else:
        from shelfmark.bypass.internal_bypasser import _cleanup_orphan_processes as _stop_gui

    @app.route('/api/debug', methods=['GET'])
    @login_required
    def debug() -> Union[Response, Tuple[Response, int]]:
        """
        This will run the /app/genDebug.sh script, which will generate a debug zip with all the logs
        The file will be named /tmp/shelfmark-debug.zip
        And then return it to the user
        """
        try:
            logger.info("Debug endpoint called, stopping GUI and generating debug info...")
            _stop_gui()
            time.sleep(1)
            result = subprocess.run(['/app/genDebug.sh'], capture_output=True, text=True, check=True)
            if result.returncode != 0:
                raise Exception(f"Debug script failed: {result.stderr}")
            logger.info(f"Debug script executed: {result.stdout}")
            debug_file_path = result.stdout.strip().split('\n')[-1]
            if not os.path.exists(debug_file_path):
                logger.error(f"Debug zip file not found at: {debug_file_path}")
                return jsonify({"error": "Failed to generate debug information"}), 500

            logger.info(f"Sending debug file: {debug_file_path}")
            return send_file(
                debug_file_path,
                mimetype='application/zip',
                download_name=os.path.basename(debug_file_path),
                as_attachment=True
            )
        except subprocess.CalledProcessError as e:
            logger.error_trace(f"Debug script error: {e}, stdout: {e.stdout}, stderr: {e.stderr}")
            return jsonify({"error": f"Debug script failed: {e.stderr}"}), 500
        except Exception as e:
            logger.error_trace(f"Debug endpoint error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/restart', methods=['GET'])
    @login_required
    def restart() -> Union[Response, Tuple[Response, int]]:
        """
        Restart the application
        """
        os._exit(0)

@app.route('/api/search', methods=['GET'])
@login_required
def api_search() -> Union[Response, Tuple[Response, int]]:
    """
    Search for books matching the provided query.

    Query Parameters:
        query (str): Search term (ISBN, title, author, etc.)
        isbn (str): Book ISBN
        author (str): Book Author
        title (str): Book Title
        lang (str): Book Language
        sort (str): Order to sort results
        content (str): Content type of book
        format (str): File format filter (pdf, epub, mobi, azw3, fb2, djvu, cbz, cbr)

    Returns:
        flask.Response: JSON array of matching books or error response.
    """
    query = request.args.get('query', '')

    filters = SearchFilters(
        isbn = request.args.getlist('isbn'),
        author = request.args.getlist('author'),
        title = request.args.getlist('title'),
        lang = request.args.getlist('lang'),
        sort = request.args.get('sort'),
        content = request.args.getlist('content'),
        format = request.args.getlist('format'),
    )

    if not query and not any(vars(filters).values()):
        return jsonify([])

    try:
        books = backend.search_books(query, filters)
        return jsonify(books)
    except SearchUnavailable as e:
        logger.warning(f"Search unavailable: {e}")
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        logger.error_trace(f"Search error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/info', methods=['GET'])
@login_required
def api_info() -> Union[Response, Tuple[Response, int]]:
    """
    Get detailed book information.

    Query Parameters:
        id (str): Book identifier (MD5 hash)

    Returns:
        flask.Response: JSON object with book details, or an error message.
    """
    book_id = request.args.get('id', '')
    if not book_id:
        return jsonify({"error": "No book ID provided"}), 400

    try:
        book = backend.get_book_info(book_id)
        if book:
            return jsonify(book)
        return jsonify({"error": "Book not found"}), 404
    except Exception as e:
        logger.error_trace(f"Info error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/download', methods=['GET'])
@login_required
def api_download() -> Union[Response, Tuple[Response, int]]:
    """
    Queue a book for download.

    Query Parameters:
        id (str): Book identifier (MD5 hash)

    Returns:
        flask.Response: JSON status object indicating success or failure.
    """
    book_id = request.args.get('id', '')
    if not book_id:
        return jsonify({"error": "No book ID provided"}), 400

    try:
        policy_mode = _resolve_policy_mode_for_current_user(
            source="direct_download",
            content_type="ebook",
        )
        if policy_mode is not None and policy_mode != PolicyMode.DOWNLOAD:
            return _policy_block_response(policy_mode)

        priority = int(request.args.get('priority', 0))
        # Per-user download overrides
        db_user_id = session.get('db_user_id')
        _username = session.get('user_id')
        success, error_msg = backend.queue_book(
            book_id, priority,
            user_id=db_user_id, username=_username,
        )
        if success:
            return jsonify({"status": "queued", "priority": priority})
        return jsonify({"error": error_msg or "Failed to queue book"}), 500
    except Exception as e:
        logger.error_trace(f"Download error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/releases/download', methods=['POST'])
@login_required
def api_download_release() -> Union[Response, Tuple[Response, int]]:
    """
    Queue a release for download.

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
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        if 'source_id' not in data:
            return jsonify({"error": "source_id is required"}), 400

        source = data.get('source', 'direct_download')
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

        priority = data.get('priority', 0)
        # Per-user download overrides
        db_user_id = session.get('db_user_id')
        _username = session.get('user_id')
        success, error_msg = backend.queue_release(
            release_payload, priority,
            user_id=db_user_id, username=_username,
        )

        if success:
            return jsonify({"status": "queued", "priority": priority})
        return jsonify({"error": error_msg or "Failed to queue release"}), 500
    except Exception as e:
        logger.error_trace(f"Release download error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/config', methods=['GET'])
@login_required
def api_config() -> Union[Response, Tuple[Response, int]]:
    """
    Get application configuration for frontend.

    Uses the dynamic config singleton to ensure settings changes
    are reflected without requiring a container restart.
    """
    try:
        from shelfmark.metadata_providers import (
            get_provider_sort_options,
            get_provider_search_fields,
            get_provider_default_sort,
        )
        from shelfmark.config.env import _is_config_dir_writable
        from shelfmark.core.onboarding import is_onboarding_complete as _get_onboarding_complete

        config = {
            "calibre_web_url": app_config.get("CALIBRE_WEB_URL", ""),
            "audiobook_library_url": app_config.get("AUDIOBOOK_LIBRARY_URL", ""),
            "debug": app_config.get("DEBUG", False),
            "build_version": BUILD_VERSION,
            "release_version": RELEASE_VERSION,
            "book_languages": _SUPPORTED_BOOK_LANGUAGE,
            "default_language": app_config.BOOK_LANGUAGE,
            "supported_formats": app_config.SUPPORTED_FORMATS,
            "supported_audiobook_formats": app_config.SUPPORTED_AUDIOBOOK_FORMATS,
            "search_mode": app_config.get("SEARCH_MODE", "direct"),
            "metadata_sort_options": get_provider_sort_options(),
            "metadata_search_fields": get_provider_search_fields(),
            "default_release_source": app_config.get("DEFAULT_RELEASE_SOURCE", "direct_download"),
            "books_output_mode": app_config.get("BOOKS_OUTPUT_MODE", "folder"),
            "auto_open_downloads_sidebar": app_config.get("AUTO_OPEN_DOWNLOADS_SIDEBAR", True),
            "download_to_browser": app_config.get("DOWNLOAD_TO_BROWSER", False),
            "settings_enabled": _is_config_dir_writable(),
            "onboarding_complete": _get_onboarding_complete(),
            # Default sort orders
            "default_sort": app_config.get("AA_DEFAULT_SORT", "relevance"),  # For direct mode (Anna's Archive)
            "metadata_default_sort": get_provider_default_sort(),  # For universal mode
        }
        return jsonify(config)
    except Exception as e:
        logger.error_trace(f"Config error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def api_health() -> Union[Response, Tuple[Response, int]]:
    """
    Health check endpoint for container orchestration.
    No authentication required.

    Returns:
        flask.Response: JSON with status "ok" and optional degraded features.
    """
    response = {"status": "ok"}

    # Report degraded features
    if not backend.WEBSOCKET_AVAILABLE:
        response["degraded"] = {"websocket": "WebSocket unavailable - real-time updates disabled"}

    return jsonify(response)


def _resolve_status_scope(*, require_authenticated: bool = True) -> tuple[bool, int | None, bool]:
    """Resolve queue-status visibility from session state.

    Returns:
        (is_admin, db_user_id, can_access_status)
    """
    auth_mode = get_auth_mode()
    if auth_mode == "none":
        return True, None, True

    if require_authenticated and 'user_id' not in session:
        return False, None, False

    is_admin = bool(session.get('is_admin', False))
    if is_admin:
        return True, None, True

    raw_db_user_id = session.get('db_user_id')
    try:
        db_user_id = int(raw_db_user_id) if raw_db_user_id is not None else None
    except (TypeError, ValueError):
        db_user_id = None

    if db_user_id is None:
        return False, None, False

    return False, db_user_id, True


def _extract_release_source_id(release_data: Any) -> str | None:
    if not isinstance(release_data, dict):
        return None
    source_id = release_data.get("source_id")
    if not isinstance(source_id, str):
        return None
    normalized = source_id.strip()
    return normalized or None


def _queue_status_to_final_activity_status(status: QueueStatus) -> str | None:
    if status == QueueStatus.COMPLETE:
        return "complete"
    if status == QueueStatus.ERROR:
        return "error"
    if status == QueueStatus.CANCELLED:
        return "cancelled"
    return None


def _normalize_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _queue_status_to_notification_event(status: QueueStatus) -> NotificationEvent | None:
    if status in {QueueStatus.COMPLETE, QueueStatus.AVAILABLE, QueueStatus.DONE}:
        return NotificationEvent.DOWNLOAD_COMPLETE
    if status == QueueStatus.ERROR:
        return NotificationEvent.DOWNLOAD_FAILED
    return None


def _notify_admin_for_terminal_download_status(*, task_id: str, status: QueueStatus, task: Any) -> None:
    event = _queue_status_to_notification_event(status)
    if event is None:
        return

    raw_owner_user_id = getattr(task, "user_id", None)
    try:
        owner_user_id = int(raw_owner_user_id) if raw_owner_user_id is not None else None
    except (TypeError, ValueError):
        owner_user_id = None

    content_type = _normalize_optional_text(getattr(task, "content_type", None))
    context = NotificationContext(
        event=event,
        title=str(getattr(task, "title", "Unknown title") or "Unknown title"),
        author=str(getattr(task, "author", "Unknown author") or "Unknown author"),
        username=_normalize_optional_text(getattr(task, "username", None)),
        content_type=normalize_content_type(content_type) if content_type is not None else None,
        format=_normalize_optional_text(getattr(task, "format", None)),
        source=normalize_source(getattr(task, "source", None)),
        error_message=(
            _normalize_optional_text(getattr(task, "status_message", None))
            if event == NotificationEvent.DOWNLOAD_FAILED
            else None
        ),
    )
    try:
        notify_admin(event, context)
    except Exception as exc:
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
    except Exception as exc:
        logger.warning(
            "Failed to trigger user notification for download %s (%s, user_id=%s): %s",
            task_id,
            status.value,
            owner_user_id,
            exc,
        )


def _record_download_terminal_snapshot(task_id: str, status: QueueStatus, task: Any) -> None:
    _notify_admin_for_terminal_download_status(task_id=task_id, status=status, task=task)

    final_status = _queue_status_to_final_activity_status(status)
    if final_status is None:
        return

    raw_owner_user_id = getattr(task, "user_id", None)
    try:
        owner_user_id = int(raw_owner_user_id) if raw_owner_user_id is not None else None
    except (TypeError, ValueError):
        owner_user_id = None

    linked_request: dict[str, Any] | None = None
    request_id: int | None = None
    origin = "direct"
    if user_db is not None and owner_user_id is not None:
        fulfilled_rows = user_db.list_requests(user_id=owner_user_id, status="fulfilled")
        for row in fulfilled_rows:
            source_id = _extract_release_source_id(row.get("release_data"))
            if source_id == task_id:
                linked_request = row
                origin = "requested"
                try:
                    request_id = int(row.get("id"))
                except (TypeError, ValueError):
                    request_id = None
                break

    try:
        download_payload = backend._task_to_dict(task)
    except Exception as exc:
        logger.warning("Failed to serialize task payload for terminal snapshot: %s", exc)
        download_payload = {
            "id": task_id,
            "title": getattr(task, "title", "Unknown title"),
            "author": getattr(task, "author", "Unknown author"),
            "source": getattr(task, "source", "direct_download"),
            "added_time": getattr(task, "added_time", 0),
            "status_message": getattr(task, "status_message", None),
            "download_path": getattr(task, "download_path", None),
            "user_id": getattr(task, "user_id", None),
            "username": getattr(task, "username", None),
        }

    snapshot: dict[str, Any] = {"kind": "download", "download": download_payload}
    if linked_request is not None:
        snapshot["request"] = linked_request

    if activity_service is not None:
        try:
            activity_service.record_terminal_snapshot(
                user_id=owner_user_id,
                item_type="download",
                item_key=build_download_item_key(task_id),
                origin=origin,
                final_status=final_status,
                snapshot=snapshot,
                request_id=request_id,
                source_id=task_id,
            )
        except Exception as exc:
            logger.warning("Failed to record terminal download snapshot for task %s: %s", task_id, exc)

    if user_db is None or linked_request is None or request_id is None or status != QueueStatus.ERROR:
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
            _emit_request_update_events([reopened_request])
    except Exception as exc:
        logger.warning(
            "Failed to reopen request %s after terminal download error %s: %s",
            request_id,
            task_id,
            exc,
        )


def _task_owned_by_actor(task: Any, *, actor_user_id: int | None, actor_username: str | None) -> bool:
    raw_task_user_id = getattr(task, "user_id", None)
    try:
        task_user_id = int(raw_task_user_id) if raw_task_user_id is not None else None
    except (TypeError, ValueError):
        task_user_id = None

    if actor_user_id is not None and task_user_id is not None:
        return task_user_id == actor_user_id

    task_username = getattr(task, "username", None)
    if isinstance(task_username, str) and task_username.strip() and isinstance(actor_username, str):
        return task_username.strip() == actor_username.strip()

    return False


def _is_graduated_request_download(task_id: str, *, user_id: int) -> bool:
    if user_db is None:
        return False

    fulfilled_rows = user_db.list_requests(user_id=user_id, status="fulfilled")
    for row in fulfilled_rows:
        source_id = _extract_release_source_id(row.get("release_data"))
        if source_id == task_id:
            return True
    return False


backend.book_queue.set_terminal_status_hook(_record_download_terminal_snapshot)


def _emit_request_update_events(updated_requests: list[dict[str, Any]]) -> None:
    """Broadcast request_update events for rows changed by delivery-state sync."""
    if not updated_requests or ws_manager is None:
        return

    try:
        socketio_ref = getattr(ws_manager, "socketio", None)
        is_enabled = getattr(ws_manager, "is_enabled", None)
        if socketio_ref is None or not callable(is_enabled) or not is_enabled():
            return

        for updated in updated_requests:
            payload = {
                "request_id": updated["id"],
                "status": updated["status"],
                "delivery_state": updated.get("delivery_state"),
                "title": (updated.get("book_data") or {}).get("title") or "Unknown title",
            }
            socketio_ref.emit("request_update", payload, to=f"user_{updated['user_id']}")
            socketio_ref.emit("request_update", payload, to="admins")
    except Exception as exc:
        logger.warning(f"Failed to emit delivery request_update events: {exc}")


@app.route('/api/status', methods=['GET'])
@login_required
def api_status() -> Union[Response, Tuple[Response, int]]:
    """
    Get current download queue status.

    Returns:
        flask.Response: JSON object with queue status.
    """
    try:
        is_admin, db_user_id, can_access_status = _resolve_status_scope()
        if not can_access_status:
            return jsonify({})

        user_id = None if is_admin else db_user_id
        status = backend.queue_status(user_id=user_id)
        if user_db is not None:
            updated_requests = sync_delivery_states_from_queue_status(
                user_db,
                queue_status=status,
                user_id=user_id,
            )
            _emit_request_update_events(updated_requests)
        return jsonify(status)
    except Exception as e:
        logger.error_trace(f"Status error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/localdownload', methods=['GET'])
@login_required
def api_local_download() -> Union[Response, Tuple[Response, int]]:
    """
    Download an EPUB file from local storage if available.

    Query Parameters:
        id (str): Book identifier (MD5 hash)

    Returns:
        flask.Response: The EPUB file if found, otherwise an error response.
    """
    book_id = request.args.get('id', '')
    if not book_id:
        return jsonify({"error": "No book ID provided"}), 400

    try:
        file_data, book_info = backend.get_book_data(book_id)
        if file_data is None:
            # Book data not found or not available
            return jsonify({"error": "File not found"}), 404
        file_name = book_info.get_filename()
        # Prepare the file for sending to the client
        data = io.BytesIO(file_data)
        return send_file(
            data,
            download_name=file_name,
            as_attachment=True
        )

    except Exception as e:
        logger.error_trace(f"Local download error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/covers/<cover_id>', methods=['GET'])
def api_cover(cover_id: str) -> Union[Response, Tuple[Response, int]]:
    """
    Serve a cached book cover image.

    This endpoint proxies and caches cover images from external sources.
    Images are cached to disk for faster subsequent requests.

    Path Parameters:
        cover_id (str): Cover identifier (book ID or composite key for universal mode)

    Query Parameters:
        url (str): Base64-encoded original image URL (required on first request)

    Returns:
        flask.Response: Binary image data with appropriate Content-Type, or 404.
    """
    try:
        import base64
        from shelfmark.core.image_cache import get_image_cache
        from shelfmark.config.env import is_covers_cache_enabled

        # Check if caching is enabled
        if not is_covers_cache_enabled():
            return jsonify({"error": "Cover caching is disabled"}), 404

        cache = get_image_cache()

        # Try to get from cache first
        cached = cache.get(cover_id)
        if cached:
            image_data, content_type = cached
            response = app.response_class(
                response=image_data,
                status=200,
                mimetype=content_type
            )
            response.headers['Cache-Control'] = 'public, max-age=86400'
            response.headers['X-Cache'] = 'HIT'
            return response

        # Cache miss - get URL from query parameter
        encoded_url = request.args.get('url')
        if not encoded_url:
            return jsonify({"error": "Cover URL not provided"}), 404

        try:
            original_url = base64.urlsafe_b64decode(encoded_url).decode()
        except Exception as e:
            logger.warning(f"Failed to decode cover URL: {e}")
            return jsonify({"error": "Invalid cover URL encoding"}), 400

        # Fetch and cache the image
        result = cache.fetch_and_cache(cover_id, original_url)
        if not result:
            return jsonify({"error": "Failed to fetch cover image"}), 404

        image_data, content_type = result
        response = app.response_class(
            response=image_data,
            status=200,
            mimetype=content_type
        )
        response.headers['Cache-Control'] = 'public, max-age=86400'
        response.headers['X-Cache'] = 'MISS'
        return response

    except Exception as e:
        logger.error_trace(f"Cover fetch error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/download/<path:book_id>/cancel', methods=['DELETE'])
@login_required
def api_cancel_download(book_id: str) -> Union[Response, Tuple[Response, int]]:
    """
    Cancel a download.

    Path Parameters:
        book_id (str): Book identifier to cancel

    Returns:
        flask.Response: JSON status indicating success or failure.
    """
    try:
        task = backend.book_queue.get_task(book_id)
        if task is None:
            return jsonify({"error": "Failed to cancel download or book not found"}), 404

        is_admin, db_user_id, can_access_status = _resolve_status_scope()
        if not is_admin:
            if not can_access_status or db_user_id is None:
                return jsonify({"error": "User identity unavailable", "code": "user_identity_unavailable"}), 403

            actor_username = session.get("user_id")
            normalized_actor_username = actor_username if isinstance(actor_username, str) else None
            if not _task_owned_by_actor(
                task,
                actor_user_id=db_user_id,
                actor_username=normalized_actor_username,
            ):
                return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403

            if _is_graduated_request_download(book_id, user_id=db_user_id):
                return jsonify({"error": "Forbidden", "code": "requested_download_cancel_forbidden"}), 403

        success = backend.cancel_download(book_id)
        if success:
            return jsonify({"status": "cancelled", "book_id": book_id})
        return jsonify({"error": "Failed to cancel download or book not found"}), 404
    except Exception as e:
        logger.error_trace(f"Cancel download error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/queue/<path:book_id>/priority', methods=['PUT'])
@login_required
def api_set_priority(book_id: str) -> Union[Response, Tuple[Response, int]]:
    """
    Set priority for a queued book.

    Path Parameters:
        book_id (str): Book identifier

    Request Body:
        priority (int): New priority level (lower number = higher priority)

    Returns:
        flask.Response: JSON status indicating success or failure.
    """
    try:
        data = request.get_json()
        if not data or 'priority' not in data:
            return jsonify({"error": "Priority not provided"}), 400
            
        priority = int(data['priority'])
        success = backend.set_book_priority(book_id, priority)
        
        if success:
            return jsonify({"status": "updated", "book_id": book_id, "priority": priority})
        return jsonify({"error": "Failed to update priority or book not found"}), 404
    except ValueError:
        return jsonify({"error": "Invalid priority value"}), 400
    except Exception as e:
        logger.error_trace(f"Set priority error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/queue/reorder', methods=['POST'])
@login_required
def api_reorder_queue() -> Union[Response, Tuple[Response, int]]:
    """
    Bulk reorder queue by setting new priorities.

    Request Body:
        book_priorities (dict): Mapping of book_id to new priority

    Returns:
        flask.Response: JSON status indicating success or failure.
    """
    try:
        data = request.get_json()
        if not data or 'book_priorities' not in data:
            return jsonify({"error": "book_priorities not provided"}), 400
            
        book_priorities = data['book_priorities']
        if not isinstance(book_priorities, dict):
            return jsonify({"error": "book_priorities must be a dictionary"}), 400
            
        # Validate all priorities are integers
        for book_id, priority in book_priorities.items():
            if not isinstance(priority, int):
                return jsonify({"error": f"Invalid priority for book {book_id}"}), 400
                
        success = backend.reorder_queue(book_priorities)
        
        if success:
            return jsonify({"status": "reordered", "updated_count": len(book_priorities)})
        return jsonify({"error": "Failed to reorder queue"}), 500
    except Exception as e:
        logger.error_trace(f"Reorder queue error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/queue/order', methods=['GET'])
@login_required
def api_queue_order() -> Union[Response, Tuple[Response, int]]:
    """
    Get current queue order for display.

    Returns:
        flask.Response: JSON array of queued books with their order and priorities.
    """
    try:
        queue_order = backend.get_queue_order()
        return jsonify({"queue": queue_order})
    except Exception as e:
        logger.error_trace(f"Queue order error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/downloads/active', methods=['GET'])
@login_required
def api_active_downloads() -> Union[Response, Tuple[Response, int]]:
    """
    Get list of currently active downloads.

    Returns:
        flask.Response: JSON array of active download book IDs.
    """
    try:
        active_downloads = backend.get_active_downloads()
        return jsonify({"active_downloads": active_downloads})
    except Exception as e:
        logger.error_trace(f"Active downloads error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/queue/clear', methods=['DELETE'])
@login_required
def api_clear_completed() -> Union[Response, Tuple[Response, int]]:
    """
    Clear all completed, errored, or cancelled books from tracking.

    Returns:
        flask.Response: JSON with count of removed books.
    """
    try:
        is_admin, db_user_id, can_access_status = _resolve_status_scope()
        if not can_access_status:
            return jsonify({"error": "User identity unavailable", "code": "user_identity_unavailable"}), 403

        scoped_user_id = None if is_admin else db_user_id
        removed_count = backend.clear_completed(user_id=scoped_user_id)

        # Broadcast status update after clearing
        if ws_manager:
            ws_manager.broadcast_status_update(backend.queue_status())

        return jsonify({"status": "cleared", "removed_count": removed_count})
    except Exception as e:
        logger.error_trace(f"Clear completed error: {e}")
        return jsonify({"error": str(e)}), 500

@app.errorhandler(404)
def not_found_error(error: Exception) -> Union[Response, Tuple[Response, int]]:
    """
    Handle 404 (Not Found) errors.

    Args:
        error (HTTPException): The 404 error raised by Flask.

    Returns:
        flask.Response: JSON error message with 404 status.
    """
    logger.warning(f"404 error: {request.url} : {error}")
    return jsonify({"error": "Resource not found"}), 404

@app.errorhandler(500)
def internal_error(error: Exception) -> Union[Response, Tuple[Response, int]]:
    """
    Handle 500 (Internal Server) errors.

    Args:
        error (HTTPException): The 500 error raised by Flask.

    Returns:
        flask.Response: JSON error message with 500 status.
    """
    logger.error_trace(f"500 error: {error}")
    return jsonify({"error": "Internal server error"}), 500

def _failed_login_response(username: str, ip_address: str) -> Tuple[Response, int]:
    """Handle a failed login attempt by recording it and returning the appropriate response."""
    is_now_locked = record_failed_login(username, ip_address)

    if is_now_locked:
        return jsonify({
            "error": f"Account locked due to {MAX_LOGIN_ATTEMPTS} failed login attempts. Try again in {LOCKOUT_DURATION_MINUTES} minutes."
        }), 429

    attempts_remaining = MAX_LOGIN_ATTEMPTS - failed_login_attempts[username]['count']
    if attempts_remaining <= 5:
        return jsonify({
            "error": f"Invalid username or password. {attempts_remaining} attempts remaining."
        }), 401

    return jsonify({"error": "Invalid username or password."}), 401


@app.route('/api/auth/login', methods=['POST'])
def api_login() -> Union[Response, Tuple[Response, int]]:
    """
    Login endpoint that validates credentials and creates a session.
    Supports both built-in credentials and CWA database authentication.
    Includes rate limiting: 10 failed attempts = 30 minute lockout.

    Request Body:
        username (str): Username
        password (str): Password
        remember_me (bool): Whether to extend session duration

    Returns:
        flask.Response: JSON with success status or error message.
    """
    try:
        ip_address = get_client_ip()
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        auth_mode = get_auth_mode()
        if auth_mode == "proxy":
            return jsonify({"error": "Proxy authentication is enabled"}), 401

        if auth_mode == "oidc" and HIDE_LOCAL_AUTH:
            return jsonify({"error": "Local authentication is disabled"}), 403

        username = data.get('username', '').strip()
        password = data.get('password', '')
        remember_me = data.get('remember_me', False)

        if not username or not password:
            return jsonify({"error": "Username and password are required"}), 400

        # Check if account is locked due to failed login attempts
        if is_account_locked(username):
            lockout_until = failed_login_attempts[username].get('lockout_until')
            remaining_time = (lockout_until - datetime.now()).total_seconds() / 60
            logger.warning(f"Login attempt blocked for locked account '{username}' from IP {ip_address}")
            return jsonify({
                "error": f"Account temporarily locked due to multiple failed login attempts. Try again in {int(remaining_time)} minutes."
            }), 429

        # If no authentication is configured, authentication always succeeds
        if auth_mode == "none":
            session['user_id'] = username
            session.permanent = remember_me
            clear_failed_logins(username)
            logger.info(f"Login successful for user '{username}' from IP {ip_address} (no auth configured)")
            return jsonify({"success": True})

        # Password authentication (builtin and OIDC modes)
        # OIDC mode also allows password login as a fallback so admins don't get locked out
        if auth_mode in ("builtin", "oidc"):
            if user_db is None:
                logger.error(f"User database not available for {auth_mode} auth")
                return jsonify({"error": "Authentication service unavailable"}), 503
            try:
                db_user = user_db.get_user(username=username)

                if not db_user:
                    return _failed_login_response(username, ip_address)

                # Authenticate against DB user
                if db_user:
                    if not db_user.get("password_hash") or not check_password_hash(db_user["password_hash"], password):
                        return _failed_login_response(username, ip_address)

                    is_admin = db_user["role"] == "admin"
                    session['user_id'] = username
                    session['db_user_id'] = db_user["id"]
                    session['is_admin'] = is_admin
                    session.permanent = remember_me
                    clear_failed_logins(username)
                    logger.info(f"Login successful for user '{username}' from IP {ip_address} ({auth_mode} auth, is_admin={is_admin}, remember_me={remember_me})")
                    return jsonify({"success": True})

                return _failed_login_response(username, ip_address)

            except Exception as e:
                logger.error_trace(f"Built-in auth error: {e}")
                return jsonify({"error": "Authentication system error"}), 500

        # CWA database authentication mode
        if auth_mode == "cwa":
            # Verify database still exists (it was validated at startup)
            if not CWA_DB_PATH or not CWA_DB_PATH.exists():
                logger.error(f"CWA database at {CWA_DB_PATH} is no longer accessible")
                return jsonify({"error": "Database configuration error"}), 500

            try:
                db_path = os.fspath(CWA_DB_PATH)
                db_uri = f"file:{db_path}?mode=ro&immutable=1"
                conn = sqlite3.connect(db_uri, uri=True)
                cur = conn.cursor()
                cur.execute("SELECT password, role, email FROM user WHERE name = ?", (username,))
                row = cur.fetchone()
                conn.close()

                # Check if user exists and password is correct
                if not row or not row[0] or not check_password_hash(row[0], password):
                    return _failed_login_response(username, ip_address)

                # Check if user has admin role (ROLE_ADMIN = 1, bit flag)
                user_role = row[1] if row[1] is not None else 0
                is_admin = (user_role & 1) == 1
                cwa_email = row[2] or None

                db_user_id = None
                if user_db is not None:
                    role = "admin" if is_admin else "user"
                    db_user, _ = upsert_cwa_user(
                        user_db,
                        cwa_username=username,
                        cwa_email=cwa_email,
                        role=role,
                        context="cwa_login",
                    )
                    db_user_id = db_user["id"]

                # Successful authentication - create session and clear failed attempts
                session['user_id'] = username
                session['is_admin'] = is_admin
                if db_user_id is not None:
                    session['db_user_id'] = db_user_id
                session.permanent = remember_me
                clear_failed_logins(username)
                logger.info(f"Login successful for user '{username}' from IP {ip_address} (CWA auth, is_admin={is_admin}, remember_me={remember_me})")
                return jsonify({"success": True})

            except Exception as e:
                logger.error_trace(f"CWA database error during login: {e}")
                return jsonify({"error": "Authentication system error"}), 500

        # Should not reach here, but handle gracefully
        return jsonify({"error": "Unknown authentication mode"}), 500

    except Exception as e:
        logger.error_trace(f"Login error: {e}")
        return jsonify({"error": "Login failed"}), 500

@app.route('/api/auth/logout', methods=['POST'])
def api_logout() -> Union[Response, Tuple[Response, int]]:
    """
    Logout endpoint that clears the session.
    For proxy auth, returns the logout URL if configured.
    
    Returns:
        flask.Response: JSON with success status and optional logout_url.
    """
    from shelfmark.core.settings_registry import load_config_file
    
    try:
        auth_mode = get_auth_mode()
        ip_address = get_client_ip()
        username = session.get('user_id', 'unknown')
        session.clear()
        logger.info(f"Logout successful for user '{username}' from IP {ip_address}")
        
        # For proxy auth, include logout URL if configured
        if auth_mode == "proxy":
            security_config = load_config_file("security")
            logout_url = security_config.get("PROXY_AUTH_LOGOUT_URL", "")
            if logout_url:
                return jsonify({"success": True, "logout_url": logout_url})
        
        return jsonify({"success": True})
    except Exception as e:
        logger.error_trace(f"Logout error: {e}")
        return jsonify({"error": "Logout failed"}), 500

@app.route('/api/auth/check', methods=['GET'])
def api_auth_check() -> Union[Response, Tuple[Response, int]]:
    """
    Check if user has a valid session.

    Returns:
        flask.Response: JSON with authentication status, whether auth is required,
        which auth mode is active, and whether user has admin privileges.
    """
    from shelfmark.core.settings_registry import load_config_file

    try:
        security_config = load_config_file("security")
        users_config = load_config_file("users")
        auth_mode = get_auth_mode()

        # If no authentication is configured, access is allowed (full admin)
        if auth_mode == "none":
            return jsonify({
                "authenticated": True,
                "auth_required": False,
                "auth_mode": "none",
                "is_admin": True
            })

        # Check if user has a valid session
        is_authenticated = 'user_id' in session

        is_admin = get_auth_check_admin_status(auth_mode, users_config, session)

        display_name = None
        if is_authenticated and session.get('db_user_id') and user_db is not None:
            try:
                db_user = user_db.get_user(user_id=session['db_user_id'])
                if db_user:
                    display_name = db_user.get("display_name") or None
            except Exception:
                pass

        response_data = {
            "authenticated": is_authenticated,
            "auth_required": True,
            "auth_mode": auth_mode,
            "is_admin": is_admin if is_authenticated else False,
            "username": session.get('user_id') if is_authenticated else None,
            "display_name": display_name,
        }
        
        # Add logout URL for proxy auth if configured
        if auth_mode == "proxy" and security_config.get("PROXY_AUTH_USER_HEADER"):
            logout_url = security_config.get("PROXY_AUTH_LOGOUT_URL", "")
            if logout_url:
                response_data["logout_url"] = logout_url

        # Add custom OIDC button label and SSO enforcement flags if configured
        if auth_mode == "oidc":
            oidc_button_label = security_config.get("OIDC_BUTTON_LABEL", "")
            if oidc_button_label:
                response_data["oidc_button_label"] = oidc_button_label
            if HIDE_LOCAL_AUTH:
                response_data["hide_local_auth"] = True
            if OIDC_AUTO_REDIRECT:
                response_data["oidc_auto_redirect"] = True
        
        return jsonify(response_data)
    except Exception as e:
        logger.error_trace(f"Auth check error: {e}")
        return jsonify({
            "authenticated": False,
            "auth_required": True,
            "auth_mode": "unknown",
            "is_admin": False
        })


@app.route('/api/metadata/providers', methods=['GET'])
@login_required
def api_metadata_providers() -> Union[Response, Tuple[Response, int]]:
    """
    Get list of available metadata providers.

    Returns:
        flask.Response: JSON with list of providers and their status.
    """
    try:
        from shelfmark.metadata_providers import (
            list_providers,
            get_provider,
            get_provider_kwargs,
        )

        configured_metadata_provider = app_config.get("METADATA_PROVIDER", "")
        providers = []
        for info in list_providers():
            provider_info = {
                "name": info["name"],
                "display_name": info["display_name"],
                "requires_auth": info["requires_auth"],
                "configured": False,
                "available": False,
            }

            # Check if provider is configured and available
            try:
                kwargs = get_provider_kwargs(info["name"])
                provider = get_provider(info["name"], **kwargs)
                provider_info["available"] = provider.is_available()
                provider_info["configured"] = (info["name"] == configured_metadata_provider)
            except Exception:
                pass

            providers.append(provider_info)

        return jsonify({
            "providers": providers,
            "configured_provider": configured_metadata_provider or None
        })
    except Exception as e:
        logger.error_trace(f"Metadata providers error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/metadata/search', methods=['GET'])
@login_required
def api_metadata_search() -> Union[Response, Tuple[Response, int]]:
    """
    Search for books using the configured metadata provider.

    Query Parameters:
        query (str): Search query (required)
        limit (int): Maximum number of results (default: 40, max: 100)
        sort (str): Sort order - relevance, popularity, rating, newest, oldest (default: relevance)
        [dynamic fields]: Provider-specific search fields passed as query params

    Returns:
        flask.Response: JSON with list of books from metadata provider.
    """
    try:
        from shelfmark.metadata_providers import (
            get_configured_provider,
            MetadataSearchOptions,
            SortOrder,
            CheckboxSearchField,
            NumberSearchField,
        )
        from dataclasses import asdict

        query = request.args.get('query', '').strip()
        content_type = request.args.get('content_type', 'ebook').strip()

        try:
            limit = min(int(request.args.get('limit', 40)), 100)
        except ValueError:
            limit = 40

        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1

        # Parse sort parameter
        sort_value = request.args.get('sort', 'relevance').lower()
        try:
            sort_order = SortOrder(sort_value)
        except ValueError:
            sort_order = SortOrder.RELEVANCE

        provider = get_configured_provider(content_type=content_type)
        if not provider:
            return jsonify({
                "error": "No metadata provider configured",
                "message": "No metadata provider configured. Enable one in Settings."
            }), 503

        if not provider.is_available():
            return jsonify({
                "error": f"Metadata provider '{provider.name}' is not available",
                "message": f"{provider.display_name} is not available. Check configuration in Settings."
            }), 503

        # Extract custom search field values from query params
        fields: Dict[str, Any] = {}
        for search_field in provider.search_fields:
            value = request.args.get(search_field.key)
            if value is not None:
                # Strip string values to handle whitespace-only input
                value = value.strip()
                if value != "":
                    # Parse value based on field type
                    if isinstance(search_field, CheckboxSearchField):
                        fields[search_field.key] = value.lower() in ('true', '1', 'yes', 'on')
                    elif isinstance(search_field, NumberSearchField):
                        try:
                            fields[search_field.key] = int(value)
                        except ValueError:
                            pass  # Skip invalid numbers
                    else:
                        fields[search_field.key] = value

        # Require either a query or at least one field value
        if not query and not fields:
            return jsonify({"error": "Either 'query' or search field values are required"}), 400

        options = MetadataSearchOptions(query=query, limit=limit, page=page, sort=sort_order, fields=fields)
        search_result = provider.search_paginated(options)

        # Convert BookMetadata objects to dicts
        books_data = [asdict(book) for book in search_result.books]

        # Transform cover_url to local proxy URLs when caching is enabled
        from shelfmark.core.utils import transform_cover_url
        for book_dict in books_data:
            if book_dict.get('cover_url'):
                cache_id = f"{book_dict['provider']}_{book_dict['provider_id']}"
                book_dict['cover_url'] = transform_cover_url(book_dict['cover_url'], cache_id)

        return jsonify({
            "books": books_data,
            "provider": provider.name,
            "query": query,
            "page": search_result.page,
            "total_found": search_result.total_found,
            "has_more": search_result.has_more
        })
    except Exception as e:
        logger.error_trace(f"Metadata search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/metadata/book/<provider>/<book_id>', methods=['GET'])
@login_required
def api_metadata_book(provider: str, book_id: str) -> Union[Response, Tuple[Response, int]]:
    """
    Get detailed book information from a metadata provider.

    Path Parameters:
        provider (str): Provider name (e.g., "hardcover", "openlibrary")
        book_id (str): Book ID in the provider's system

    Returns:
        flask.Response: JSON with book details.
    """
    try:
        from shelfmark.metadata_providers import (
            get_provider,
            is_provider_registered,
            get_provider_kwargs,
        )
        from dataclasses import asdict

        if not is_provider_registered(provider):
            return jsonify({"error": f"Unknown metadata provider: {provider}"}), 400

        # Get provider instance with appropriate configuration
        kwargs = get_provider_kwargs(provider)
        prov = get_provider(provider, **kwargs)

        if not prov.is_available():
            return jsonify({"error": f"Provider '{provider}' is not available"}), 503

        book = prov.get_book(book_id)
        if not book:
            return jsonify({"error": "Book not found"}), 404

        book_dict = asdict(book)

        # Transform cover_url to local proxy URL when caching is enabled
        from shelfmark.core.utils import transform_cover_url
        if book_dict.get('cover_url'):
            cache_id = f"{provider}_{book_id}"
            book_dict['cover_url'] = transform_cover_url(book_dict['cover_url'], cache_id)

        return jsonify(book_dict)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error_trace(f"Metadata book error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/releases', methods=['GET'])
@login_required
def api_releases() -> Union[Response, Tuple[Response, int]]:
    """
    Search for downloadable releases of a book.

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
        from shelfmark.metadata_providers import (
            BookMetadata,
            get_provider,
            is_provider_registered,
            get_provider_kwargs,
        )
        from shelfmark.release_sources import get_source, list_available_sources, serialize_column_config
        from dataclasses import asdict

        provider = request.args.get('provider', '').strip()
        book_id = request.args.get('book_id', '').strip()
        source_filter = request.args.get('source', '').strip()
        # Accept title/author from frontend to avoid re-fetching metadata
        title_param = request.args.get('title', '').strip()
        author_param = request.args.get('author', '').strip()
        expand_search = request.args.get('expand_search', '').lower() == 'true'
        # Accept language codes for filtering (comma-separated)
        languages_param = request.args.get('languages', '').strip()
        languages = [lang.strip() for lang in languages_param.split(',') if lang.strip()] if languages_param else None
        # Content type for audiobook vs ebook search
        content_type = request.args.get('content_type', 'ebook').strip()

        manual_query = request.args.get('manual_query', '').strip()

        # Accept indexer names for Prowlarr filtering (comma-separated)
        indexers_param = request.args.get('indexers', '').strip()
        indexers = [idx.strip() for idx in indexers_param.split(',') if idx.strip()] if indexers_param else None

        if not provider or not book_id:
            return jsonify({"error": "Parameters 'provider' and 'book_id' are required"}), 400

        # Direct mode request approvals can open ReleaseModal with provider=direct_download.
        # In that flow, treat the direct result as release-search context instead of requiring
        # a metadata provider registration.
        if provider == "direct_download":
            direct_book = backend.get_book_info(book_id)
            if not isinstance(direct_book, dict):
                return jsonify({"error": "Book not found in direct source"}), 404

            resolved_title = title_param or str(direct_book.get("title") or "").strip() or "Unknown title"
            resolved_author = author_param or str(direct_book.get("author") or "").strip()
            authors = [part.strip() for part in resolved_author.split(",") if part.strip()]
            if not authors and resolved_author:
                authors = [resolved_author]

            raw_publish_year = direct_book.get("year")
            publish_year = None
            if isinstance(raw_publish_year, int):
                publish_year = raw_publish_year
            elif isinstance(raw_publish_year, str):
                normalized_year = raw_publish_year.strip()
                if normalized_year.isdigit():
                    publish_year = int(normalized_year)

            book = BookMetadata(
                provider="direct_download",
                provider_id=book_id,
                provider_display_name="Direct Download",
                title=resolved_title,
                search_title=resolved_title,
                search_author=resolved_author or None,
                authors=authors,
                cover_url=direct_book.get("preview"),
                description=direct_book.get("description"),
                publisher=direct_book.get("publisher"),
                publish_year=publish_year,
                language=direct_book.get("language"),
                source_url=direct_book.get("source_url"),
            )
        else:
            if not is_provider_registered(provider):
                return jsonify({"error": f"Unknown metadata provider: {provider}"}), 400

            # Get book metadata from provider
            kwargs = get_provider_kwargs(provider)
            prov = get_provider(provider, **kwargs)
            book = prov.get_book(book_id)

            if not book:
                return jsonify({"error": "Book not found in metadata provider"}), 404

            # Override title from frontend if available (search results may have better data)
            # Note: We intentionally DON'T override authors here - get_book() now returns
            # filtered authors (primary authors only, excluding translators/narrators),
            # which gives better release search results than the unfiltered search data
            if title_param:
                book.title = title_param

        # Determine which release sources to search
        if source_filter:
            sources_to_search = [source_filter]
        elif provider == "direct_download":
            # Direct mode has no metadata-provider fanout; keep release browsing focused
            # on Direct Download results (same dataset as legacy direct search).
            sources_to_search = ["direct_download"]
        else:
            # Search only enabled sources
            sources_to_search = [src["name"] for src in list_available_sources() if src["enabled"]]

        # Search each source for releases
        all_releases = []
        errors = []
        source_instances = {}  # Keep source instances for column config

        for source_name in sources_to_search:
            try:
                source = get_source(source_name)
                source_instances[source_name] = source

                from shelfmark.core.search_plan import build_release_search_plan

                plan = build_release_search_plan(book, languages=languages, manual_query=manual_query, indexers=indexers)

                if plan.manual_query:
                    planned_query = plan.manual_query
                    planned_query_type = "manual"
                elif not expand_search and plan.isbn_candidates:
                    planned_query = plan.isbn_candidates[0]
                    planned_query_type = "isbn"
                else:
                    planned_query = plan.primary_query
                    planned_query_type = "title_author"

                logger.debug(
                    f"Searching {source_name}: {planned_query_type}='{planned_query}' "
                    f"(title='{book.title}', authors={book.authors}, expand={expand_search}, content_type={content_type})"
                )

                releases = source.search(book, plan, expand_search=expand_search, content_type=content_type)
                all_releases.extend(releases)
            except ValueError:
                errors.append(f"Unknown source: {source_name}")
            except Exception as e:
                logger.warning(f"Release search failed for source {source_name}: {e}")
                errors.append(f"{source_name}: {str(e)}")

        # Convert Release objects to dicts
        releases_data = [asdict(release) for release in all_releases]

        # Get column config from the first source searched
        # Reuse the same instance to get any dynamic data (e.g., online_servers for IRC)
        column_config = None
        if sources_to_search and sources_to_search[0] in source_instances:
            try:
                first_source = source_instances[sources_to_search[0]]
                column_config = serialize_column_config(first_source.get_column_config())
            except Exception as e:
                logger.warning(f"Failed to get column config: {e}")

        # Convert book to dict and transform cover_url
        book_dict = asdict(book)
        from shelfmark.core.utils import transform_cover_url
        if book_dict.get('cover_url'):
            cache_id = f"{provider}_{book_id}"
            book_dict['cover_url'] = transform_cover_url(book_dict['cover_url'], cache_id)

        search_info = {}
        for source_name, source_instance in source_instances.items():
            if hasattr(source_instance, 'last_search_type') and source_instance.last_search_type:
                search_info[source_name] = {
                    "search_type": source_instance.last_search_type
                }

        response = {
            "releases": releases_data,
            "book": book_dict,
            "sources_searched": sources_to_search,
            "column_config": column_config,
            "search_info": search_info,
        }

        if errors:
            response["errors"] = errors

        # If no releases found and there were errors, return 503 with error message
        # This matches the behavior of /api/search when Anna's Archive is unreachable
        if not releases_data and errors:
            # Use the first error message (typically the most relevant)
            error_message = errors[0]
            # Strip the source prefix if present (e.g., "direct_download: message" -> "message")
            if ": " in error_message:
                error_message = error_message.split(": ", 1)[1]
            return jsonify({"error": error_message}), 503

        return jsonify(response)
    except Exception as e:
        logger.error_trace(f"Releases search error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/release-sources', methods=['GET'])
@login_required
def api_release_sources() -> Union[Response, Tuple[Response, int]]:
    """
    Get available release sources from the plugin registry.

    Returns:
        flask.Response: JSON list of available release sources.
    """
    try:
        from shelfmark.release_sources import list_available_sources
        sources = list_available_sources()
        return jsonify(sources)
    except Exception as e:
        logger.error_trace(f"Release sources error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings', methods=['GET'])
@login_required
def api_settings_get_all() -> Union[Response, Tuple[Response, int]]:
    """
    Get all settings tabs with their fields and current values.

    Returns:
        flask.Response: JSON with all settings tabs.
    """
    try:
        from shelfmark.core.settings_registry import serialize_all_settings

        # Ensure settings are registered by importing settings modules
        # This triggers the @register_settings decorators
        import shelfmark.config.settings  # noqa: F401
        import shelfmark.config.security  # noqa: F401
        import shelfmark.config.notifications_settings  # noqa: F401

        data = serialize_all_settings(include_values=True)
        return jsonify(data)
    except Exception as e:
        logger.error_trace(f"Settings get error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/<tab_name>', methods=['GET'])
@login_required
def api_settings_get_tab(tab_name: str) -> Union[Response, Tuple[Response, int]]:
    """
    Get settings for a specific tab.

    Path Parameters:
        tab_name (str): Settings tab name (e.g., "general", "hardcover")

    Returns:
        flask.Response: JSON with tab settings and values.
    """
    try:
        from shelfmark.core.settings_registry import (
            get_settings_tab,
            serialize_tab,
        )

        # Ensure settings are registered
        import shelfmark.config.settings  # noqa: F401
        import shelfmark.config.security  # noqa: F401
        import shelfmark.config.notifications_settings  # noqa: F401

        tab = get_settings_tab(tab_name)
        if not tab:
            return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

        return jsonify(serialize_tab(tab, include_values=True))
    except Exception as e:
        logger.error_trace(f"Settings get tab error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/<tab_name>', methods=['PUT'])
@login_required
def api_settings_update_tab(tab_name: str) -> Union[Response, Tuple[Response, int]]:
    """
    Update settings for a specific tab.

    Path Parameters:
        tab_name (str): Settings tab name

    Request Body:
        JSON object with setting keys and values to update.

    Returns:
        flask.Response: JSON with update result.
    """
    try:
        from shelfmark.core.settings_registry import (
            get_settings_tab,
            update_settings,
        )

        # Ensure settings are registered
        import shelfmark.config.settings  # noqa: F401
        import shelfmark.config.security  # noqa: F401
        import shelfmark.config.notifications_settings  # noqa: F401

        tab = get_settings_tab(tab_name)
        if not tab:
            return jsonify({"error": f"Unknown settings tab: {tab_name}"}), 404

        values = request.get_json()
        if values is None or not isinstance(values, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        # If no values to update, return success with empty updated list
        if not values:
            return jsonify({"success": True, "message": "No changes to save", "updated": []})

        result = update_settings(tab_name, values)

        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        logger.error_trace(f"Settings update error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings/<tab_name>/action/<action_key>', methods=['POST'])
@login_required
def api_settings_execute_action(tab_name: str, action_key: str) -> Union[Response, Tuple[Response, int]]:
    """
    Execute a settings action (e.g., test connection).

    Path Parameters:
        tab_name (str): Settings tab name
        action_key (str): Action key to execute

    Request Body (optional):
        JSON object with current form values (unsaved)

    Returns:
        flask.Response: JSON with action result.
    """
    try:
        from shelfmark.core.settings_registry import execute_action

        # Ensure settings are registered
        import shelfmark.config.settings  # noqa: F401
        import shelfmark.config.security  # noqa: F401
        import shelfmark.config.notifications_settings  # noqa: F401

        # Get current form values if provided (for testing with unsaved values)
        current_values = request.get_json(silent=True) or {}

        result = execute_action(tab_name, action_key, current_values)

        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        logger.error_trace(f"Settings action error: {e}")
        return jsonify({"error": str(e)}), 500


# =============================================================================
# Onboarding API
# =============================================================================


@app.route('/api/onboarding', methods=['GET'])
@login_required
def api_onboarding_get() -> Union[Response, Tuple[Response, int]]:
    """
    Get onboarding configuration including steps, fields, and current values.

    Returns:
        flask.Response: JSON with onboarding steps and values.
    """
    try:
        from shelfmark.core.onboarding import get_onboarding_config

        # Ensure settings are registered
        import shelfmark.config.settings  # noqa: F401

        config = get_onboarding_config()
        return jsonify(config)
    except Exception as e:
        logger.error_trace(f"Onboarding get error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/onboarding', methods=['POST'])
@login_required
def api_onboarding_save() -> Union[Response, Tuple[Response, int]]:
    """
    Save onboarding settings and mark as complete.

    Request Body:
        JSON object with all onboarding field values

    Returns:
        flask.Response: JSON with success/error status.
    """
    try:
        from shelfmark.core.onboarding import save_onboarding_settings

        # Ensure settings are registered
        import shelfmark.config.settings  # noqa: F401

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "message": "No data provided"}), 400

        result = save_onboarding_settings(data)

        if result["success"]:
            return jsonify(result)
        else:
            return jsonify(result), 400
    except Exception as e:
        logger.error_trace(f"Onboarding save error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/onboarding/skip', methods=['POST'])
@login_required
def api_onboarding_skip() -> Union[Response, Tuple[Response, int]]:
    """
    Skip onboarding and mark as complete without saving any settings.

    Returns:
        flask.Response: JSON with success status.
    """
    try:
        from shelfmark.core.onboarding import mark_onboarding_complete

        mark_onboarding_complete()
        return jsonify({"success": True, "message": "Onboarding skipped"})
    except Exception as e:
        logger.error_trace(f"Onboarding skip error: {e}")
        return jsonify({"error": str(e)}), 500


# Catch-all route for React Router (must be last)
# This handles client-side routing by serving index.html for any unmatched routes
@app.route('/<path:path>')
def catch_all(path: str) -> Response:
    """
    Serve the React app for any route not matched by API endpoints.
    This allows React Router to handle client-side routing.
    Authentication is handled by the React app itself.
    """
    # If the request is for an API endpoint or static file, let it 404
    if path.startswith('api/') or path.startswith('assets/'):
        return jsonify({"error": "Resource not found"}), 404
    # Otherwise serve the React app
    return _serve_index_html()

# WebSocket event handlers
@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    logger.info("WebSocket client connected")

    # Track the connection (triggers warmup callbacks on first connect)
    ws_manager.client_connected()

    # Join appropriate room based on authenticated user session
    is_admin, db_user_id, can_access_status = _resolve_status_scope()
    ws_manager.join_user_room(request.sid, is_admin, db_user_id)

    # Send initial status to the newly connected client (filtered)
    try:
        if not can_access_status:
            emit('status_update', {})
            return

        user_id = None if is_admin else db_user_id
        status = backend.queue_status(user_id=user_id)
        emit('status_update', status)
    except Exception as e:
        logger.error(f"Error sending initial status: {e}")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    logger.info("WebSocket client disconnected")

    # Leave room
    ws_manager.leave_user_room(request.sid)

    # Track the disconnection
    ws_manager.client_disconnected()

@socketio.on('request_status')
def handle_status_request():
    """Handle manual status request from client."""
    try:
        is_admin, db_user_id, can_access_status = _resolve_status_scope()
        ws_manager.sync_user_room(request.sid, is_admin, db_user_id)

        if not can_access_status:
            emit('status_update', {})
            return

        user_id = None if is_admin else db_user_id
        status = backend.queue_status(user_id=user_id)
        emit('status_update', status)
    except Exception as e:
        logger.error(f"Error handling status request: {e}")
        emit('error', {'message': 'Failed to get status'})

logger.log_resource_usage()

# Warn if config directory is not writable (settings won't persist)
if not _is_config_dir_writable():
    logger.warning(
        f"Config directory {CONFIG_DIR} is not writable. Settings will not persist. "
        "Mount a config volume to enable settings persistence (see docs for details)."
    )

if __name__ == '__main__':
    logger.info(f"Starting Flask application with WebSocket support on {FLASK_HOST}:{FLASK_PORT} (debug={DEBUG})")
    socketio.run(
        app,
        host=FLASK_HOST,
        port=FLASK_PORT,
        debug=DEBUG,
        allow_unsafe_werkzeug=True  # For development only
    )
