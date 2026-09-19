"""Personal API keys: generation, hashing, request parsing and authentication.

A key is a bearer credential that acts exactly as the user who created it.
Only a SHA-256 hash is stored; the raw key is shown once at creation.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from shelfmark.core.auth_modes import is_user_active_for_auth_mode
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

KEY_PREFIX = "smk_"
_KEY_PREFIX_DISPLAY_LENGTH = 12  # "smk_" + 8 characters, enough to identify a key in lists
_KEY_RANDOM_BYTES = 32
MAX_ACTIVE_KEYS_PER_USER = 25
MAX_KEY_NAME_LENGTH = 64
MAX_EXPIRES_IN_DAYS = 3650
_LAST_USED_WRITE_INTERVAL_SECONDS = 60.0
_SQLITE_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

_PUBLIC_FIELDS = (
    "id",
    "name",
    "key_prefix",
    "created_at",
    "expires_at",
    "last_used_at",
    "revoked_at",
)


@dataclass(frozen=True)
class ApiKeyAuthResult:
    """A successfully authenticated key: the owning user row and the key id."""

    user: dict[str, Any]
    key_id: int


def is_api_keys_enabled() -> bool:
    """Whether admins allow API keys at all (Security settings)."""
    return bool(app_config.get("API_KEYS_ENABLED", True))


def generate_key() -> str:
    """Return a new raw key with 256 bits of entropy."""
    return f"{KEY_PREFIX}{secrets.token_urlsafe(_KEY_RANDOM_BYTES)}"


def hash_key(raw_key: str) -> str:
    """Hash a raw key for storage. SHA-256 is sufficient for a 256-bit random secret."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_prefix(raw_key: str) -> str:
    """The displayable, indexable prefix of a raw key."""
    return raw_key[:_KEY_PREFIX_DISPLAY_LENGTH]


def extract_api_key(authorization_header: str | None, api_key_header: str | None) -> str | None:
    """Pull a raw key out of ``Authorization: Bearer`` or ``X-Api-Key``. Bearer wins.

    Only a token carrying the ``smk_`` prefix is recognised as ours. A foreign
    Bearer token (e.g. one a reverse proxy forwards for its own auth) is
    treated as if the header were absent, so the request falls through to
    normal session authentication instead of being rejected as an invalid key.
    """
    if authorization_header:
        scheme, _, token = authorization_header.strip().partition(" ")
        token = token.strip()
        if scheme.lower() == "bearer" and token.startswith(KEY_PREFIX):
            return token
    if api_key_header:
        token = api_key_header.strip()
        if token.startswith(KEY_PREFIX):
            return token
    return None


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime(_SQLITE_TIMESTAMP_FORMAT)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, _SQLITE_TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_create_request(data: object) -> tuple[str, str | None]:
    """Validate a create-key payload. Returns ``(name, expires_at)``; raises ``ValueError``."""
    payload = data if isinstance(data, Mapping) else None
    if payload is None:
        msg = "Request body must be a JSON object"
        raise ValueError(msg)

    name = str(payload.get("name") or "").strip()
    if not name:
        msg = "Key name is required"
        raise ValueError(msg)
    if len(name) > MAX_KEY_NAME_LENGTH:
        msg = f"Key name must be at most {MAX_KEY_NAME_LENGTH} characters"
        raise ValueError(msg)

    expires_in_days = payload.get("expires_in_days")
    if expires_in_days is None:
        return name, None
    days = (
        expires_in_days
        if isinstance(expires_in_days, int) and not isinstance(expires_in_days, bool)
        else None
    )
    if days is None:
        msg = "expires_in_days must be a whole number of days"
        raise ValueError(msg)
    if days < 1 or days > MAX_EXPIRES_IN_DAYS:
        msg = f"expires_in_days must be between 1 and {MAX_EXPIRES_IN_DAYS}"
        raise ValueError(msg)

    expires_at = datetime.now(UTC) + timedelta(days=days)
    return name, _format_timestamp(expires_at)


def serialize_api_key(row: Mapping[str, Any]) -> dict[str, Any]:
    """Public representation of a key row. Never includes the hash."""
    return {field: row.get(field) for field in _PUBLIC_FIELDS}


_last_used_writes: dict[int, float] = {}
_last_used_lock = threading.Lock()


def reset_last_used_throttle() -> None:
    """Clear the last-used write throttle (tests)."""
    with _last_used_lock:
        _last_used_writes.clear()


def _should_write_last_used(key_id: int, now: float) -> bool:
    with _last_used_lock:
        previous = _last_used_writes.get(key_id)
        if previous is not None and now - previous < _LAST_USED_WRITE_INTERVAL_SECONDS:
            return False
        _last_used_writes[key_id] = now
        return True


def authenticate(user_db: UserDB, raw_key: str, auth_mode: str) -> ApiKeyAuthResult | None:
    """Resolve a raw key to its user. Returns ``None`` for every refusal cause."""
    if not is_api_keys_enabled():
        return None
    if not raw_key.startswith(KEY_PREFIX):
        return None

    prefix = key_prefix(raw_key)
    digest = hash_key(raw_key)
    matched: dict[str, Any] | None = None
    for candidate in user_db.get_api_keys_by_prefix(prefix):
        if hmac.compare_digest(str(candidate.get("key_hash", "")), digest):
            matched = candidate

    if matched is None:
        # An unrecognised prefix is unauthenticated noise (scanners, typos, stale
        # keys from another instance) rather than evidence of a real key, so it
        # doesn't warrant info-level logging like the cases below.
        logger.debug("API key authentication failed: unknown key %s", prefix)
        return None
    if matched.get("revoked_at") is not None:
        logger.info("API key authentication failed: revoked key %s", prefix)
        return None
    expires_at = _parse_timestamp(matched.get("expires_at"))
    if expires_at is not None and expires_at <= datetime.now(UTC):
        logger.info("API key authentication failed: expired key %s", prefix)
        return None

    user = user_db.get_user(user_id=int(matched["user_id"]))
    if not user:
        logger.info("API key authentication failed: no user for key %s", prefix)
        return None
    if not is_user_active_for_auth_mode(user, auth_mode):
        logger.info("API key authentication failed: user inactive for auth mode %s", auth_mode)
        return None

    key_id = int(matched["id"])
    if _should_write_last_used(key_id, time.monotonic()):
        user_db.touch_api_key_last_used(key_id)
    return ApiKeyAuthResult(user=user, key_id=key_id)
