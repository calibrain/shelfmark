"""Request hooks for the two ways a request can be authenticated without logging in: the
SHELFMARK_API_KEY credentials and the reverse proxy's identity headers.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, NoReturn

from flask import Response, g, jsonify, request, session

from shelfmark.core import api_key as api_key_module  # module access lets tests monkeypatch the key
from shelfmark.core.config import config as app_config
from shelfmark.core.external_user_linking import upsert_external_user
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.stats_routes import STATS_PATH

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_OPERATIONAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


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


def register_auth_middleware(
    app: Flask,
    *,
    resolve_auth_mode: Callable[[], str],
    get_user_db: Callable[[], UserDB | None],
) -> None:
    """Register the API-key and proxy-auth ``before_request`` hooks and the cookie-stripping
    ``after_request`` hook.

    Call this at the place the hooks should take in the hook order: ``before_request`` hooks run
    in registration order, ``after_request`` hooks in reverse. The API-key hook must come first
    because the proxy hook skips requests it already authenticated.
    """

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
        if request.path in _API_KEY_EXEMPT_PATHS or request.path.startswith(
            _API_KEY_EXEMPT_PREFIXES
        ):
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
        if resolve_auth_mode() == "none":
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
            user_db = get_user_db()
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
        auth_mode = resolve_auth_mode()

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

        user_db = get_user_db()

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

                logger.warning(
                    "Proxy auth enabled but no username found in header '%s'", user_header
                )
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
                        _raise_runtime_error("Unexpected proxy user sync result: no user returned")

                    session["db_user_id"] = db_user["id"]

            session.permanent = False
        except _OPERATIONAL_ERRORS:
            logger.exception("Proxy auth middleware error")
            return jsonify({"error": "Authentication error"}), 500
        else:
            return None

    @app.after_request
    def strip_cookie_for_api_key_requests(response: Response) -> Response:
        """Keyed requests never mint or refresh a session cookie, even if a handler dirties the session."""
        if g.get("api_key_auth"):
            # Setting `permanent` mutates the session dict (re-marking it
            # modified), so it must be reset before `modified`, not after.
            session.permanent = False
            session.modified = False
        return response
