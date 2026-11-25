"""Tie a signed-in session to the current state of its user in users.db.

The session cookie is signed but kept by the browser, so whatever sign-in copied into it
(the user, the admin flag) stays readable until the cookie expires. ``revalidate_session``
checks a session against users.db on each request: it signs the session out when the user
was deleted, can no longer sign in under the active auth mode, or had their sessions revoked
(a password change rotates ``users.session_nonce``), and it takes the admin flag from the
user's current role, so a demotion applies to sessions that already exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shelfmark.core.auth_modes import (
    AUTH_MODE_NONE,
    AUTH_SOURCE_CWA,
    AUTH_SOURCE_PROXY,
    is_user_active_for_auth_mode,
)
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import get_session_db_user_id

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

SESSION_NONCE_KEY = "session_nonce"

# "none" has no users to check, and proxy auth rebuilds the session from the proxy's
# headers on every request (see auth_middleware.proxy_auth_middleware).
_UNCHECKED_AUTH_MODES = frozenset({AUTH_MODE_NONE, AUTH_SOURCE_PROXY})


def stamp_session_nonce(sess: MutableMapping[str, Any], user: Mapping[str, Any]) -> None:
    """Record the user's current session nonce in a session that is signing in as them."""
    sess[SESSION_NONCE_KEY] = user.get("session_nonce")


def _sign_out(sess: MutableMapping[str, Any], reason: str) -> bool:
    logger.info("Signed out session for user '%s': %s", sess.get("user_id"), reason)
    sess.clear()
    return False


def revalidate_session(
    sess: MutableMapping[str, Any],
    *,
    user_db: UserDB | None,
    auth_mode: str,
) -> bool:
    """Check a signed-in session against users.db, refreshing or clearing it in place.

    Returns False when the session was signed out, True otherwise (including when there
    is no signed-in session to check). Database errors propagate to the caller.
    """
    if auth_mode in _UNCHECKED_AUTH_MODES or "user_id" not in sess:
        return True

    if user_db is None:
        # CWA sign-in works without users.db and then links no user. The other modes
        # cannot sign anyone in without it, so a session from before it went away is stale.
        if auth_mode == AUTH_SOURCE_CWA:
            return True
        return _sign_out(sess, "the user database is unavailable")

    db_user_id = get_session_db_user_id(sess)
    if db_user_id is None:
        return _sign_out(sess, "the session is not linked to a user")

    user = user_db.get_user_cached(user_id=db_user_id)
    if user is None:
        return _sign_out(sess, f"user {db_user_id} no longer exists")
    if not is_user_active_for_auth_mode(user, auth_mode):
        return _sign_out(sess, f"user {db_user_id} cannot sign in with auth mode '{auth_mode}'")

    nonce = user.get("session_nonce")
    if not nonce or sess.get(SESSION_NONCE_KEY) != nonce:
        return _sign_out(sess, f"the sessions of user {db_user_id} were revoked")

    is_admin = user.get("role") == "admin"
    if sess.get("is_admin") != is_admin:
        logger.info(
            "Session for user '%s' now has is_admin=%s from the user's current role",
            sess.get("user_id"),
            is_admin,
        )
        sess["is_admin"] = is_admin
    return True
