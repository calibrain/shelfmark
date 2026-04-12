"""Post-download processing policy.

This module holds configuration-driven *policy* decisions that are shared across
post-download processing components, but are not specific to archive extraction.

Examples:
- Which file formats are enabled
- How files should be organized (none/rename/organize)
- Which naming templates to use

Implementation note:
Keep this module free of dependencies on archive extraction mechanics to avoid
circular imports (`archive` is used by the pipeline).

"""

from __future__ import annotations

import shelfmark.core.config as core_config
from shelfmark.core.request_helpers import coerce_bool


def _normalize_format_list(value: object, default: list[str]) -> list[str]:
    if isinstance(value, str):
        return [fmt.strip().lower() for fmt in value.split(",") if fmt.strip()]
    if isinstance(value, (list, tuple, set)):
        normalized = [str(fmt).strip().lower() for fmt in value if str(fmt).strip()]
        return normalized or default
    return default


def _config_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


def get_supported_formats() -> list[str]:
    """Get current supported formats from config singleton."""
    default_formats = ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"]
    formats = core_config.config.get("SUPPORTED_FORMATS", default_formats)
    return _normalize_format_list(formats, default_formats)


def get_supported_audiobook_formats() -> list[str]:
    """Get current supported audiobook formats from config singleton."""
    default_formats = ["m4b", "mp3"]
    formats = core_config.config.get("SUPPORTED_AUDIOBOOK_FORMATS", default_formats)
    return _normalize_format_list(formats, default_formats)


def get_file_organization(*, is_audiobook: bool) -> str:
    """Get the file organization mode for the content type."""
    key = "FILE_ORGANIZATION_AUDIOBOOK" if is_audiobook else "FILE_ORGANIZATION"
    mode = _config_text(core_config.config.get(key, "rename")).strip().lower()

    # Handle legacy settings migration
    if mode not in ("none", "rename", "organize"):
        legacy_key = "PROCESSING_MODE_AUDIOBOOK" if is_audiobook else "PROCESSING_MODE"
        legacy_mode = _config_text(core_config.config.get(legacy_key, "ingest")).strip().lower()
        if legacy_mode == "library":
            return "organize"
        if coerce_bool(core_config.config.get("USE_BOOK_TITLE", True), default=True):
            return "rename"
        return "none"

    return mode


def get_template(*, is_audiobook: bool, organization_mode: str) -> str:
    """Get the template for the content type and organization mode."""
    # Determine the correct key based on content type and organization mode
    if is_audiobook:
        if organization_mode == "organize":
            key = "TEMPLATE_AUDIOBOOK_ORGANIZE"
        else:
            key = "TEMPLATE_AUDIOBOOK_RENAME"
    else:
        key = "TEMPLATE_ORGANIZE" if organization_mode == "organize" else "TEMPLATE_RENAME"

    template = _config_text(core_config.config.get(key, ""))

    # Fallback to legacy keys if new keys are empty
    if not template:
        legacy_key = "TEMPLATE_AUDIOBOOK" if is_audiobook else "TEMPLATE"
        template = _config_text(core_config.config.get(legacy_key, ""))

    if not template:
        legacy_key = "LIBRARY_TEMPLATE_AUDIOBOOK" if is_audiobook else "LIBRARY_TEMPLATE"
        template = _config_text(core_config.config.get(legacy_key, ""))

    if not template:
        if organization_mode == "organize":
            return "{Author}/{Title} ({Year})"
        return "{Author} - {Title} ({Year})"

    return template
