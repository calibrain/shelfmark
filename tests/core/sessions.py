"""Seed a test client's session the way a real sign-in does.

The app checks every session against users.db (``shelfmark.core.session_auth``): the session
must name a user that exists, carry that user's session nonce, and its admin flag follows the
user's role. A hand-written session cookie therefore needs a matching user row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from shelfmark.core.session_auth import SESSION_NONCE_KEY

if TYPE_CHECKING:
    from flask.testing import FlaskClient

    from shelfmark.core.user_db import UserDB


def _main_user_db() -> UserDB:
    import shelfmark.main as main

    if main.user_db is None:
        msg = "shelfmark.main has no user database"
        raise RuntimeError(msg)
    return main.user_db


def ensure_user(
    user_db: UserDB,
    *,
    username: str,
    db_user_id: int | None = None,
    is_admin: bool = False,
) -> dict[str, Any]:
    """Return the users.db row a session for ``username`` / ``db_user_id`` would belong to.

    With ``db_user_id`` that user must exist. Without it a user named ``username`` is reused
    or created. Either way the row's role is set to match ``is_admin``.
    """
    role = "admin" if is_admin else "user"
    if db_user_id is None:
        user = user_db.get_user(username=username) or user_db.create_user(
            username=username, role=role
        )
    else:
        user = user_db.get_user(user_id=db_user_id)
        if user is None:
            msg = f"No user {db_user_id} in users.db; create it first or pass no db_user_id"
            raise LookupError(msg)
    if user.get("role") != role:
        user_db.update_user(user["id"], role=role)
        user = {**user, "role": role}
    return user


def sign_in(
    client: FlaskClient,
    *,
    user_id: str = "someone",
    db_user_id: int | None = None,
    is_admin: bool = False,
) -> dict[str, Any]:
    """Give ``client`` a valid signed-in session as a user of ``shelfmark.main``'s users.db.

    Returns the user's row.
    """
    user = ensure_user(
        _main_user_db(),
        username=user_id,
        db_user_id=db_user_id,
        is_admin=is_admin,
    )
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["db_user_id"] = user["id"]
        sess["is_admin"] = is_admin
        sess[SESSION_NONCE_KEY] = user["session_nonce"]
    return user


def forge_session(client: FlaskClient, **fields: Any) -> None:
    """Replace the client's session with exactly ``fields``, with no user row behind it.

    What a stale or hand-made cookie looks like: the session check signs it out, unless a
    test patches the check away with ``skip_session_check`` to reach a route's own guard.
    """
    with client.session_transaction() as sess:
        sess.clear()
        sess.update(fields)


def skip_session_check() -> Any:
    """Patch out the per-request session check of ``shelfmark.main``.

    For tests of the identity guards routes keep behind it: with the check in place a
    session without a valid user never reaches them.
    """
    return patch("shelfmark.core.auth_middleware.revalidate_session", return_value=True)
