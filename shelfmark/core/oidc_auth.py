"""OIDC authentication helpers.

Handles group claim parsing, user info extraction, and user provisioning.
Flask route handlers are registered separately in main.py.
"""

from typing import Any, Dict, List, Optional

from shelfmark.core.logger import setup_logger
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)


def parse_group_claims(id_token: Dict[str, Any], group_claim: str) -> List[str]:
    """Extract group list from an ID token claim.

    Supports list, comma-separated string, or pipe-separated string.
    Returns empty list if claim is missing.
    """
    raw = id_token.get(group_claim)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(g).strip() for g in raw if str(g).strip()]
    if isinstance(raw, str):
        delimiter = "," if "," in raw else "|"
        return [g.strip() for g in raw.split(delimiter) if g.strip()]
    return []


def extract_user_info(id_token: Dict[str, Any]) -> Dict[str, Any]:
    """Extract user info from OIDC ID token claims.

    Returns a dict with keys: oidc_subject, username, email, display_name.
    Falls back through preferred_username -> email -> sub for username.
    """
    sub = id_token.get("sub", "")
    email = id_token.get("email")
    display_name = id_token.get("name")
    username = id_token.get("preferred_username") or email or sub

    return {
        "oidc_subject": sub,
        "username": username,
        "email": email,
        "display_name": display_name,
    }


def provision_oidc_user(
    db: UserDB,
    user_info: Dict[str, Any],
    is_admin: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create or update a user from OIDC claims.

    If a user with the same oidc_subject exists, updates their info.
    If the username is taken by a different user, appends a numeric suffix.

    Admin role is synced from IdP when group-based auth is enabled (is_admin is not None):
    - is_admin=True → DB role = admin
    - is_admin=False → DB role = user (downgrade if needed)

    When group-based auth is disabled (is_admin=None), preserve existing DB role.
    The database is always the single source of truth for auth checks.
    """
    oidc_subject = user_info["oidc_subject"]

    # Check if user already exists by OIDC subject
    existing = db.get_user(oidc_subject=oidc_subject)
    if existing:
        updates: Dict[str, Any] = {
            "email": user_info.get("email"),
            "display_name": user_info.get("display_name"),
        }
        # Sync role from IdP when group-based auth is enabled
        if is_admin is not None:
            updates["role"] = "admin" if is_admin else "user"
        db.update_user(existing["id"], **updates)
        return db.get_user(user_id=existing["id"])

    role = "admin" if is_admin else "user"

    # New user — resolve username conflicts
    username = user_info["username"]
    if db.get_user(username=username):
        # Username taken, append suffix
        suffix = 1
        while db.get_user(username=f"{username}_{suffix}"):
            suffix += 1
        username = f"{username}_{suffix}"

    user = db.create_user(
        username=username,
        email=user_info.get("email"),
        display_name=user_info.get("display_name"),
        oidc_subject=oidc_subject,
        role=role,
    )
    logger.info(f"Provisioned OIDC user: {username} (sub={oidc_subject})")
    return user
