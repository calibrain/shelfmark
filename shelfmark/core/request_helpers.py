"""Shared request-related helper functions used by routes and services."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger

_logger = setup_logger(__name__)


def now_utc_iso() -> str:
    """Return the current UTC time as a seconds-precision ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit_ws_event(
    ws_manager: Any,
    *,
    event_name: str,
    payload: dict[str, Any],
    room: str,
) -> None:
    """Emit a WebSocket event via the shared manager, swallowing failures."""
    if ws_manager is None:
        return
    try:
        socketio = getattr(ws_manager, "socketio", None)
        is_enabled = getattr(ws_manager, "is_enabled", None)
        if socketio is None or not callable(is_enabled) or not is_enabled():
            return
        socketio.emit(event_name, payload, to=room)
    except Exception as exc:
        _logger.warning("Failed to emit WebSocket event '%s' to room '%s': %s", event_name, room, exc)


def load_users_request_policy_settings() -> dict[str, Any]:
    """Load global request-policy settings from the users config file."""
    from shelfmark.core.request_policy import REQUEST_POLICY_KEYS

    return {
        key: app_config.get(key)
        for key in REQUEST_POLICY_KEYS
    }


def coerce_bool(value: Any, default: bool = False) -> bool:
    """Coerce arbitrary values into booleans with string-friendly semantics."""
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


def get_session_db_user_id(session_obj: Any) -> int | None:
    """Extract and coerce `db_user_id` from a Flask session to ``int | None``."""
    raw = session_obj.get("db_user_id") if session_obj is not None else None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def coerce_int(value: Any, default: int) -> int:
    """Best-effort integer coercion with fallback to default."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_optional_text(value: Any) -> str | None:
    """Return a trimmed string or None for empty/non-string input."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def normalize_positive_int(value: Any) -> int | None:
    """Parse *value* as a positive integer, returning ``None`` on failure."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_optional_positive_int(value: Any, field_name: str = "value") -> int | None:
    """Parse *value* as a positive integer or ``None``.

    Raises ``ValueError`` when *value* is present but not a valid
    positive integer.
    """
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer when provided") from exc
    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer when provided")
    return parsed


def populate_request_usernames(rows: list[dict[str, Any]], user_db: Any) -> None:
    """Add 'username' to each request row by looking up user_id."""
    cache: dict[int, str] = {}
    for row in rows:
        requester_id = row["user_id"]
        if requester_id not in cache:
            requester = user_db.get_user(user_id=requester_id)
            cache[requester_id] = requester.get("username", "") if requester else ""
        row["username"] = cache[requester_id]


def extract_release_source_id(release_data: Any) -> str | None:
    """Extract and normalize release_data.source_id."""
    if not isinstance(release_data, dict):
        return None
    source_id = release_data.get("source_id")
    if not isinstance(source_id, str):
        return None
    normalized = source_id.strip()
    return normalized or None


def normalize_optional_identifier(value: Any) -> str | None:
    """Normalize string/integer identifiers while rejecting booleans and empties."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        value = str(value)
    return normalize_optional_text(value)


def build_release_metadata_provenance(book_data: Any) -> dict[str, Any] | None:
    """Extract exact upstream metadata provenance from request book_data.

    The resulting payload is safe to carry inside internal release queue payloads.
    Only exact provider identifiers already present in request book_data are kept.
    """
    if not isinstance(book_data, dict):
        return None

    provider = normalize_optional_text(book_data.get("provider"))
    provider_id = normalize_optional_identifier(book_data.get("provider_id"))
    if provider is None or provider_id is None:
        return None

    provenance: dict[str, Any] = {
        "provider": provider,
        "provider_id": provider_id,
    }

    source_url = normalize_optional_text(book_data.get("source_url"))
    if source_url is not None:
        provenance["source_url"] = source_url

    hardcover_edition = normalize_optional_identifier(
        book_data.get("hardcover_edition")
        or book_data.get("hardcover_edition_id")
    )
    if provider == "hardcover" and hardcover_edition is not None:
        provenance["hardcover_edition"] = hardcover_edition

    hardcover_slug = normalize_optional_text(book_data.get("hardcover_slug"))
    if provider == "hardcover" and hardcover_slug is not None:
        provenance["hardcover_slug"] = hardcover_slug

    return provenance


def attach_release_metadata_provenance(
    release_data: Any,
    *,
    book_data: Any,
) -> dict[str, Any] | Any:
    """Attach exact metadata provenance to transient queue payloads.

    This is intentionally additive and internal-only. It should not mutate the
    caller's original release_data mapping, and it should not invent missing IDs.
    """
    if not isinstance(release_data, dict):
        return release_data

    enriched = dict(release_data)
    exact_provenance = build_release_metadata_provenance(book_data)
    if exact_provenance is None:
        return enriched

    existing = enriched.get("_metadata_provenance")
    merged = dict(exact_provenance)
    if isinstance(existing, dict):
        for key, value in existing.items():
            if key in {"provider", "provider_id", "source_url", "hardcover_edition", "hardcover_slug"}:
                normalized = normalize_optional_identifier(value) if key in {"provider_id", "hardcover_edition"} else normalize_optional_text(value)
                if normalized is not None:
                    merged[key] = normalized

    enriched["_metadata_provenance"] = merged
    return enriched
