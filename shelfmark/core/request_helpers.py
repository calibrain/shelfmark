"""Shared request-related helper functions used by routes and services."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import load_config_file

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
    return load_config_file("users")


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


def coerce_int(value: Any, default: int) -> int:
    """Best-effort integer coercion with fallback to default."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_optional_text(value: Any) -> str | None:
    """Return a trimmed string or None for empty/missing input.

    Non-string values are coerced via ``str()`` before stripping;
    ``None`` short-circuits to ``None``.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
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


def extract_release_source_id(release_data: Any) -> str | None:
    """Extract and normalize release_data.source_id."""
    if not isinstance(release_data, dict):
        return None
    source_id = release_data.get("source_id")
    if not isinstance(source_id, str):
        return None
    normalized = source_id.strip()
    return normalized or None
