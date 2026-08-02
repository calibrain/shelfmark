"""Supported source-member format configuration."""

from __future__ import annotations

import shelfmark.core.config as core_config


def _normalize_format_list(value: object, default: list[str]) -> list[str]:
    if isinstance(value, str):
        return [fmt.strip().lower() for fmt in value.split(",") if fmt.strip()]
    if isinstance(value, (list, tuple, set)):
        formats = [str(fmt).strip().lower() for fmt in value if str(fmt).strip()]
        return formats or default
    return default


def get_supported_formats() -> list[str]:
    """Get supported Book file formats."""
    default_formats = ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"]
    return _normalize_format_list(
        core_config.config.get("SUPPORTED_FORMATS", default_formats), default_formats
    )


def get_supported_audiobook_formats() -> list[str]:
    """Get supported audiobook file formats."""
    default_formats = ["m4b", "mp3"]
    return _normalize_format_list(
        core_config.config.get("SUPPORTED_AUDIOBOOK_FORMATS", default_formats), default_formats
    )
