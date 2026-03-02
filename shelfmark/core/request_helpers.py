"""Shared request-related helper functions used by routes and services."""

from __future__ import annotations

from typing import Any

from shelfmark.core.settings_registry import load_config_file


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
    """Return a trimmed string or None for empty/non-string input."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def extract_release_source_id(release_data: Any) -> str | None:
    """Extract and normalize release_data.source_id."""
    if not isinstance(release_data, dict):
        return None
    source_id = release_data.get("source_id")
    if not isinstance(source_id, str):
        return None
    normalized = source_id.strip()
    return normalized or None
