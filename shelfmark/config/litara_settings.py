"""Helpers for Litara settings validation and connection tests."""

from __future__ import annotations

from typing import Any

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download.outputs.litara import (
    LitaraConfig,
    LitaraError,
    litara_login,
)

logger = setup_logger(__name__)


def check_litara_connection(
    current_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Test the Litara connection using current form values."""
    current_values = current_values or {}

    def _get_value(key: str, default: object = None) -> object:
        value = current_values.get(key)
        if value not in (None, ""):
            return value
        if default is None:
            return config.get(key)
        return config.get(key, default)

    base_url = str(_get_value("LITARA_HOST", "") or "").strip().rstrip("/")
    email = str(_get_value("LITARA_EMAIL", "") or "").strip()
    password = str(_get_value("LITARA_PASSWORD", "") or "")

    if not base_url:
        return {"success": False, "message": "Litara URL is required"}
    if not email:
        return {"success": False, "message": "Litara email is required"}
    if not password:
        return {"success": False, "message": "Litara password is required"}

    litara_config = LitaraConfig(
        base_url=base_url,
        email=email,
        password=password,
        verify_tls=True,
    )

    try:
        litara_login(litara_config)
    except LitaraError as exc:
        return {"success": False, "message": str(exc)}
    else:
        return {"success": True, "message": "Connected to Litara"}
