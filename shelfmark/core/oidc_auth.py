"""OIDC authentication helpers.

Handles group claim parsing, user info extraction, and user provisioning.
Flask route handlers are registered separately in main.py.
"""

from typing import Any, Dict, List, Optional

from shelfmark.core.external_user_linking import upsert_external_user
from shelfmark.core.user_db import UserDB

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
    allow_email_link: bool = False,
    allow_create: bool = True,
) -> Optional[Dict[str, Any]]:
    """Create or update a user from OIDC claims.

    Matching and collision handling use the shared external user linker:
    - OIDC subject first
    - optionally unique email linking (when `allow_email_link=True`)
    - username conflict resolution via numeric suffix.

    Returns None when no existing user is matchable and `allow_create=False`.
    """
    oidc_subject = user_info["oidc_subject"]
    user, _ = upsert_external_user(
        db,
        auth_source="oidc",
        username=user_info["username"] or oidc_subject,
        role="admin" if is_admin else "user",
        email=user_info.get("email"),
        display_name=user_info.get("display_name"),
        subject_field="oidc_subject",
        subject=oidc_subject,
        allow_email_link=allow_email_link,
        sync_role=is_admin is not None,
        allow_create=allow_create,
        collision_strategy="suffix",
        context="oidc_login",
    )
    return user
