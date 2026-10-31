"""Sign-in routes: ``POST /api/auth/login``, ``POST /api/auth/logout``, ``GET /api/auth/check``.

The failed-login counters and their lockout helpers live here too, as module state
shared by everything that imports them (``main`` re-exports the names).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from flask import jsonify, request, session

from shelfmark.core.config import config as app_config
from shelfmark.core.cwa_user_sync import upsert_cwa_user
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from flask import Flask
    from werkzeug.wrappers import Response

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

# Rate limiting for login attempts
# Map usernames to their failed-attempt counters and lockout timestamps.
failed_login_attempts: dict[str, dict[str, Any]] = {}
MAX_LOGIN_ATTEMPTS = 10
LOCKOUT_DURATION_MINUTES = 30
LOGIN_ATTEMPT_WARNING_THRESHOLD = 5


def cleanup_old_lockouts() -> None:
    """Remove expired lockout entries to prevent memory buildup."""
    current_time = datetime.now(UTC)
    expired_users = []
    for username in list(failed_login_attempts):
        lockout_until = _get_lockout_until(username, repair_if_locked=True)
        if lockout_until is not None and lockout_until < current_time:
            expired_users.append(username)
    for username in expired_users:
        logger.info("Lockout expired for user: %s", username)
        del failed_login_attempts[username]


def _get_lockout_until(username: str, *, repair_if_locked: bool = False) -> datetime | None:
    """Return a valid lockout timestamp for the user when one exists.

    When a user has already crossed the lockout threshold but the timestamp is
    missing or malformed, optionally repair the state to keep the lockout in
    force rather than silently letting the user through.
    """
    lockout_state = failed_login_attempts.get(username)
    if lockout_state is None:
        return None

    lockout_until = lockout_state.get("lockout_until")
    if isinstance(lockout_until, datetime):
        return lockout_until

    attempt_count = lockout_state.get("count")
    if repair_if_locked and isinstance(attempt_count, int) and attempt_count >= MAX_LOGIN_ATTEMPTS:
        repaired_lockout_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
        lockout_state["lockout_until"] = repaired_lockout_until
        logger.warning("Repaired missing lockout timestamp for locked account '%s'", username)
        return repaired_lockout_until

    if lockout_until is not None:
        logger.warning("Ignoring invalid lockout timestamp for user '%s'", username)

    return None


def is_account_locked(username: str) -> bool:
    """Check if an account is currently locked due to failed login attempts."""
    cleanup_old_lockouts()

    if username not in failed_login_attempts:
        return False

    lockout_until = _get_lockout_until(username, repair_if_locked=True)
    return lockout_until is not None and datetime.now(UTC) < lockout_until


def record_failed_login(username: str, ip_address: str) -> bool:
    """Record a failed login attempt and lock account if threshold is reached.

    Returns True if account is now locked, False otherwise.
    """
    if username not in failed_login_attempts:
        failed_login_attempts[username] = {"count": 0}

    failed_login_attempts[username]["count"] += 1
    count = failed_login_attempts[username]["count"]

    logger.warning(
        "Failed login attempt %s/%s for user '%s' from IP %s",
        count,
        MAX_LOGIN_ATTEMPTS,
        username,
        ip_address,
    )

    if count >= MAX_LOGIN_ATTEMPTS:
        lockout_until = datetime.now(UTC) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
        failed_login_attempts[username]["lockout_until"] = lockout_until
        logger.warning(
            "Account locked for user '%s' until %s due to %s failed login attempts",
            username,
            lockout_until.strftime("%Y-%m-%d %H:%M:%S"),
            count,
        )
        return True

    return False


def clear_failed_logins(username: str) -> None:
    """Clear failed login attempts for a user after successful login."""
    if username in failed_login_attempts:
        del failed_login_attempts[username]
        logger.debug("Cleared failed login attempts for user: %s", username)


def _failed_login_response(username: str, ip_address: str) -> tuple[Response, int]:
    """Handle a failed login attempt by recording it and returning the appropriate response."""
    is_now_locked = record_failed_login(username, ip_address)

    if is_now_locked:
        return jsonify(
            {
                "error": f"Account locked due to {MAX_LOGIN_ATTEMPTS} failed login attempts. Try again in {LOCKOUT_DURATION_MINUTES} minutes."
            }
        ), 429

    attempts_remaining = MAX_LOGIN_ATTEMPTS - failed_login_attempts[username]["count"]
    if attempts_remaining <= LOGIN_ATTEMPT_WARNING_THRESHOLD:
        return jsonify(
            {"error": f"Invalid username or password. {attempts_remaining} attempts remaining."}
        ), 401

    return jsonify({"error": "Invalid username or password."}), 401


def register_auth_routes(
    app: Flask,
    *,
    get_user_db: Callable[[], UserDB | None],
    resolve_auth_mode: Callable[[], str],
    get_client_ip: Callable[[], str],
    account_is_locked: Callable[[str], bool],
    disable_local_auth: Callable[[], bool],
    hide_local_auth: Callable[[], bool],
    oidc_auto_redirect: Callable[[], bool],
    get_cwa_db_path: Callable[[], Path | None],
    check_password: Callable[[str, str], bool],
    get_auth_check_admin_status: Callable[..., bool],
    operational_errors: tuple[type[BaseException], ...],
) -> None:
    """Register the three auth routes.

    Everything that lives in ``main.py`` or is patched there by tests is passed as a callable
    that is called per request, so a runtime patch of any of them still takes effect.
    ``account_is_locked`` is one of them: ``main.is_account_locked`` is looked up on each login.
    """

    @app.route("/api/auth/login", methods=["POST"])
    def api_login() -> Response | tuple[Response, int]:
        """Login endpoint that validates credentials and creates a session.

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
            data = request.get_json(silent=True)
            if not data:
                return jsonify({"error": "No data provided"}), 400

            auth_mode = resolve_auth_mode()
            if auth_mode == "proxy":
                return jsonify({"error": "Proxy authentication is enabled"}), 401

            if auth_mode in ("builtin", "oidc") and disable_local_auth():
                return jsonify({"error": "Local authentication is disabled"}), 403

            if auth_mode == "oidc" and hide_local_auth():
                return jsonify({"error": "Local authentication is disabled"}), 403

            username = data.get("username", "").strip()
            password = data.get("password", "")
            remember_me = data.get("remember_me", False)

            if not username or not password:
                return jsonify({"error": "Username and password are required"}), 400

            # Check if account is locked due to failed login attempts
            if account_is_locked(username):
                lockout_until = _get_lockout_until(username, repair_if_locked=True)
                if lockout_until is None:
                    logger.error("Locked account '%s' is missing a lockout timestamp", username)
                    return jsonify(
                        {
                            "error": f"Account temporarily locked due to multiple failed login attempts. Try again in {LOCKOUT_DURATION_MINUTES} minutes."
                        }
                    ), 429
                remaining_time = (lockout_until - datetime.now(UTC)).total_seconds() / 60
                logger.warning(
                    "Login attempt blocked for locked account '%s' from IP %s", username, ip_address
                )
                return jsonify(
                    {
                        "error": f"Account temporarily locked due to multiple failed login attempts. Try again in {int(remaining_time)} minutes."
                    }
                ), 429

            # If no authentication is configured, authentication always succeeds
            if auth_mode == "none":
                session["user_id"] = username
                session.permanent = remember_me
                clear_failed_logins(username)
                logger.info(
                    "Login successful for user '%s' from IP %s (no auth configured)",
                    username,
                    ip_address,
                )
                return jsonify({"success": True})

            # Password authentication (builtin and OIDC modes)
            # OIDC mode also allows password login as a fallback so admins don't get locked out
            if auth_mode in ("builtin", "oidc"):
                user_db = get_user_db()
                if user_db is None:
                    logger.error("User database not available for %s auth", auth_mode)
                    return jsonify({"error": "Authentication service unavailable"}), 503
                try:
                    db_user = user_db.get_user(username=username)

                    if not db_user:
                        return _failed_login_response(username, ip_address)

                    # Authenticate against DB user
                    if db_user:
                        if not db_user.get("password_hash") or not check_password(
                            db_user["password_hash"], password
                        ):
                            return _failed_login_response(username, ip_address)

                        is_admin = db_user["role"] == "admin"
                        session["user_id"] = username
                        session["db_user_id"] = db_user["id"]
                        session["is_admin"] = is_admin
                        session.permanent = remember_me
                        clear_failed_logins(username)
                        logger.info(
                            "Login successful for user '%s' from IP %s (%s auth, is_admin=%s, remember_me=%s)",
                            username,
                            ip_address,
                            auth_mode,
                            is_admin,
                            remember_me,
                        )
                        return jsonify({"success": True})

                    return _failed_login_response(username, ip_address)

                except operational_errors as e:
                    logger.error_trace(f"Built-in auth error: {e}")
                    return jsonify({"error": "Authentication system error"}), 500

            # CWA database authentication mode
            if auth_mode == "cwa":
                # Verify database still exists (it was validated at startup)
                cwa_db_path = get_cwa_db_path()
                if not cwa_db_path or not cwa_db_path.exists():
                    logger.error("CWA database at %s is no longer accessible", cwa_db_path)
                    return jsonify({"error": "Database configuration error"}), 500

                try:
                    db_path = os.fspath(cwa_db_path)
                    db_uri = f"file:{db_path}?mode=ro&immutable=1"
                    conn = sqlite3.connect(db_uri, uri=True)
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT password, role, email FROM user WHERE name = ?", (username,)
                    )
                    row = cur.fetchone()
                    conn.close()

                    # Check if user exists and password is correct
                    if not row or not row[0] or not check_password(row[0], password):
                        return _failed_login_response(username, ip_address)

                    # Check if user has admin role (ROLE_ADMIN = 1, bit flag)
                    user_role = row[1] if row[1] is not None else 0
                    is_admin = (user_role & 1) == 1
                    cwa_email = row[2] or None

                    db_user_id = None
                    user_db = get_user_db()
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
                    session["user_id"] = username
                    session["is_admin"] = is_admin
                    if db_user_id is not None:
                        session["db_user_id"] = db_user_id
                    session.permanent = remember_me
                    clear_failed_logins(username)
                    logger.info(
                        "Login successful for user '%s' from IP %s (CWA auth, is_admin=%s, remember_me=%s)",
                        username,
                        ip_address,
                        is_admin,
                        remember_me,
                    )
                    return jsonify({"success": True})

                except operational_errors as e:
                    logger.error_trace(f"CWA database error during login: {e}")
                    return jsonify({"error": "Authentication system error"}), 500

            # Should not reach here, but handle gracefully
            return jsonify({"error": "Unknown authentication mode"}), 500

        except operational_errors as e:
            logger.error_trace(f"Login error: {e}")
            return jsonify({"error": "Login failed"}), 500

    @app.route("/api/auth/logout", methods=["POST"])
    def api_logout() -> Response | tuple[Response, int]:
        """Logout endpoint that clears the session.

        For proxy auth, returns the logout URL if configured.

        Returns:
            flask.Response: JSON with success status and optional logout_url.

        """
        try:
            auth_mode = resolve_auth_mode()
            ip_address = get_client_ip()
            username = session.get("user_id", "unknown")
            session.clear()
            logger.info("Logout successful for user '%s' from IP %s", username, ip_address)

            # For proxy auth, include logout URL if configured
            if auth_mode == "proxy":
                logout_url = app_config.get("PROXY_AUTH_LOGOUT_URL", "")
                if logout_url:
                    return jsonify({"success": True, "logout_url": logout_url})

            return jsonify({"success": True})
        except operational_errors as e:
            logger.error_trace(f"Logout error: {e}")
            return jsonify({"error": "Logout failed"}), 500

    @app.route("/api/auth/check", methods=["GET"])
    def api_auth_check() -> Response | tuple[Response, int]:
        """Check if user has a valid session.

        Returns:
            flask.Response: JSON with authentication status, whether auth is required,
            which auth mode is active, and whether user has admin privileges.

        """
        try:
            auth_mode = resolve_auth_mode()

            # If no authentication is configured, access is allowed (full admin)
            if auth_mode == "none":
                return jsonify(
                    {
                        "authenticated": True,
                        "auth_required": False,
                        "auth_mode": "none",
                        "is_admin": True,
                    }
                )

            # Check if user has a valid session
            is_authenticated = "user_id" in session

            is_admin = get_auth_check_admin_status(auth_mode, {}, session)

            display_name = None
            user_db = get_user_db()
            if is_authenticated and session.get("db_user_id") and user_db is not None:
                try:
                    db_user = user_db.get_user(user_id=session["db_user_id"])
                    if db_user:
                        display_name = db_user.get("display_name") or None
                except (sqlite3.Error, TypeError, ValueError) as exc:
                    logger.debug("Could not load display name for session user: %s", exc)

            response_data = {
                "authenticated": is_authenticated,
                "auth_required": True,
                "auth_mode": auth_mode,
                "is_admin": is_admin if is_authenticated else False,
                "username": session.get("user_id") if is_authenticated else None,
                "display_name": display_name,
            }

            # Add logout URL for proxy auth if configured
            if auth_mode == "proxy" and app_config.get("PROXY_AUTH_USER_HEADER", ""):
                logout_url = app_config.get("PROXY_AUTH_LOGOUT_URL", "")
                if logout_url:
                    response_data["logout_url"] = logout_url

            if auth_mode in ("builtin", "oidc") and disable_local_auth():
                response_data["hide_local_auth"] = True

            # Add custom OIDC button label and SSO enforcement flags if configured
            if auth_mode == "oidc":
                oidc_button_label = app_config.get("OIDC_BUTTON_LABEL", "")
                if oidc_button_label:
                    response_data["oidc_button_label"] = oidc_button_label
                if hide_local_auth():
                    response_data["hide_local_auth"] = True
                if oidc_auto_redirect():
                    response_data["oidc_auto_redirect"] = True

            return jsonify(response_data)
        except operational_errors as e:
            logger.error_trace(f"Auth check error: {e}")
            return jsonify(
                {
                    "authenticated": False,
                    "auth_required": True,
                    "auth_mode": "unknown",
                    "is_admin": False,
                }
            )
