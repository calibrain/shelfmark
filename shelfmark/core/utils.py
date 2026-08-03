"""Shared utility functions for the Shelfmark."""

import base64
import importlib
import os
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from shelfmark.core.request_helpers import normalize_optional_text

if TYPE_CHECKING:
    from types import ModuleType


def normalize_http_url(
    url: str | None,
    *,
    default_scheme: str = "http",
    strip_trailing_slash: bool = True,
    allow_special: tuple[str, ...] = (),
) -> str:
    """Normalize a configured HTTP URL for requests and links."""
    if not isinstance(url, str):
        return ""

    normalized = url.strip()
    if not normalized:
        return ""

    if (normalized.startswith('"') and normalized.endswith('"')) or (
        normalized.startswith("'") and normalized.endswith("'")
    ):
        normalized = normalized[1:-1].strip()
        if not normalized:
            return ""

    if allow_special:
        special_map = {value.lower(): value for value in allow_special if isinstance(value, str)}
        special_match = special_map.get(normalized.lower())
        if special_match is not None:
            return special_match

    if normalized.startswith(("/", "./", "../")):
        return normalized

    if "://" not in normalized:
        scheme = default_scheme.strip().rstrip(":/")
        if scheme:
            normalized = f"{scheme}://{normalized}"

    # Strip query string and fragment — mirrors are used as base URLs for
    # constructing search requests; params/fragments on the configured URL
    # produce malformed URLs when paths are appended (issue #999).
    parsed = urlparse(normalized)
    if parsed.query or parsed.fragment:
        normalized = parsed._replace(query="", fragment="").geturl()

    if strip_trailing_slash:
        normalized = normalized.rstrip("/")

    return normalized


_xmlrpc_patch_lock = Lock()
_xmlrpc_patch_applied = False
_XMLRPC_PATCH_ERRORS = (ImportError, AttributeError, OSError, RuntimeError)


def get_hardened_xmlrpc_client() -> ModuleType:
    """Return ``xmlrpc.client`` after best-effort defusedxml monkey patching."""
    global _xmlrpc_patch_applied
    if not _xmlrpc_patch_applied:
        with _xmlrpc_patch_lock:
            if not _xmlrpc_patch_applied:
                try:
                    from defusedxml.xmlrpc import monkey_patch

                    monkey_patch()
                    _xmlrpc_patch_applied = True
                except _XMLRPC_PATCH_ERRORS:
                    # Keep runtime behavior unchanged if defusedxml is unavailable.
                    _xmlrpc_patch_applied = False

    return importlib.import_module("xmlrpc.client")


def normalize_base_path(value: str | None) -> str:
    """Normalize a URL base path for reverse proxy subpath deployments."""
    if not isinstance(value, str):
        return ""

    path = value.strip()
    if not path:
        return ""

    if "://" in path:
        parsed = urlparse(path)
        path = parsed.path or ""

    if not path or path == "/":
        return ""

    if not path.startswith("/"):
        path = "/" + path

    return path.rstrip("/")


def is_audiobook(content_type: str | None) -> bool:
    """Check if content type indicates an audiobook."""
    return bool(content_type and "audiobook" in content_type.lower())


CONTENT_TYPES = [
    "book (fiction)",
    "book (non-fiction)",
    "book (unknown)",
    "magazine",
    "comic book",
    "audiobook",
    "standards document",
    "musical score",
    "other",
]


def get_destination() -> Path:
    """Get the single immutable library storage root."""
    from shelfmark.core.config import config

    destination = config.get("DESTINATION", "/books")
    return Path(str(destination))


def transform_cover_url(cover_url: str | None, cache_id: str) -> str | None:
    """Transform external cover URL to local proxy URL when caching is enabled."""
    if not cover_url:
        return cover_url

    # Skip if already a local URL (starts with /)
    if cover_url.startswith("/"):
        return cover_url

    # Check if cover caching is enabled
    from shelfmark.config.env import is_covers_cache_enabled

    if not is_covers_cache_enabled():
        return cover_url

    from shelfmark.core.config import config as app_config

    # Encode the original URL and create a proxy URL
    encoded_url = base64.urlsafe_b64encode(cover_url.encode()).decode()
    base_path = normalize_base_path(normalize_optional_text(app_config.get("URL_BASE", "")))
    if base_path:
        return f"{base_path}/api/covers/{cache_id}?url={encoded_url}"
    return f"/api/covers/{cache_id}?url={encoded_url}"


def _coerce_config_path(value: object) -> Path | None:
    if isinstance(value, os.PathLike):
        path_value = os.fspath(value)
        if isinstance(path_value, str):
            normalized = path_value.strip()
            if normalized:
                return Path(normalized)
        return None

    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None

    return Path(normalized)
