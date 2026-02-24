"""Shared utility functions for the Shelfmark."""

import base64
import importlib
import os
import re
from threading import Lock
from types import ModuleType
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


def normalize_http_url(
    url: Optional[str],
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

    if (normalized.startswith("\"") and normalized.endswith("\"")) or (
        normalized.startswith("'") and normalized.endswith("'")
    ):
        normalized = normalized[1:-1].strip()
        if not normalized:
            return ""

    if allow_special:
        special_map = {
            value.lower(): value
            for value in allow_special
            if isinstance(value, str)
        }
        special_match = special_map.get(normalized.lower())
        if special_match is not None:
            return special_match

    if normalized.startswith(("/", "./", "../")):
        return normalized

    if "://" not in normalized:
        scheme = default_scheme.strip().rstrip(":/")
        if scheme:
            normalized = f"{scheme}://{normalized}"

    if strip_trailing_slash:
        normalized = normalized.rstrip("/")

    return normalized


_xmlrpc_patch_lock = Lock()
_xmlrpc_patch_applied = False


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
                except Exception:
                    # Keep runtime behavior unchanged if defusedxml is unavailable.
                    _xmlrpc_patch_applied = False

    return importlib.import_module("xmlrpc.client")


def normalize_base_path(value: Optional[str]) -> str:
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


def is_audiobook(content_type: Optional[str]) -> bool:
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

# Maps AA content types to their config keys for content-type routing
# Used when AA_CONTENT_TYPE_ROUTING is enabled
_AA_CONTENT_TYPE_TO_CONFIG_KEY = {
    "book (fiction)": "AA_CONTENT_TYPE_DIR_FICTION",
    "book (non-fiction)": "AA_CONTENT_TYPE_DIR_NON_FICTION",
    "book (unknown)": "AA_CONTENT_TYPE_DIR_UNKNOWN",
    "magazine": "AA_CONTENT_TYPE_DIR_MAGAZINE",
    "comic book": "AA_CONTENT_TYPE_DIR_COMIC",
    "audiobook": "AA_CONTENT_TYPE_DIR_AUDIOBOOK",
    "standards document": "AA_CONTENT_TYPE_DIR_STANDARDS",
    "musical score": "AA_CONTENT_TYPE_DIR_MUSICAL_SCORE",
    "other": "AA_CONTENT_TYPE_DIR_OTHER",
}

# Legacy mapping - kept for backwards compatibility during migration
_LEGACY_CONTENT_TYPE_TO_CONFIG_KEY = {
    "book (fiction)": "INGEST_DIR_BOOK_FICTION",
    "book (non-fiction)": "INGEST_DIR_BOOK_NON_FICTION",
    "book (unknown)": "INGEST_DIR_BOOK_UNKNOWN",
    "magazine": "INGEST_DIR_MAGAZINE",
    "comic book": "INGEST_DIR_COMIC_BOOK",
    "audiobook": "INGEST_DIR_AUDIOBOOK",
    "standards document": "INGEST_DIR_STANDARDS_DOCUMENT",
    "musical score": "INGEST_DIR_MUSICAL_SCORE",
    "other": "INGEST_DIR_OTHER",
}

_USER_PLACEHOLDER_PATTERN = re.compile(r"\{user\}", re.IGNORECASE)
_INVALID_USER_PATH_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize_user_for_path(username: str) -> str:
    """Sanitize username for path usage in destination placeholders."""
    sanitized = _INVALID_USER_PATH_CHARS.sub("_", username.strip())
    return sanitized.strip(" .")


def _resolve_destination_username(
    user_id: Optional[int] = None,
    username: Optional[str] = None,
) -> str:
    explicit = str(username or "").strip()
    if explicit:
        return explicit

    if user_id is None:
        return ""

    try:
        from shelfmark.core.user_db import UserDB

        user_db = UserDB(os.path.join(os.environ.get("CONFIG_DIR", "/config"), "users.db"))
        user_db.initialize()
        user = user_db.get_user(user_id=user_id)
        if not user:
            return ""
        return str(user.get("username") or "").strip()
    except Exception:
        return ""


def _expand_user_destination_placeholder(
    path_value: str,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
) -> str:
    """Expand `{User}` placeholders in destination paths."""
    if not isinstance(path_value, str):
        return path_value

    if not _USER_PLACEHOLDER_PATTERN.search(path_value):
        return path_value

    resolved_username = _sanitize_user_for_path(
        _resolve_destination_username(user_id=user_id, username=username)
    )
    return _USER_PLACEHOLDER_PATTERN.sub(resolved_username, path_value)


def get_destination(
    is_audiobook: bool = False,
    user_id: Optional[int] = None,
    username: Optional[str] = None,
) -> Path:
    """Get base destination directory. Audiobooks fall back to main destination."""
    from shelfmark.core.config import config

    if is_audiobook:
        # Audiobook destination with fallback to main destination
        audiobook_dest = config.get("DESTINATION_AUDIOBOOK", "", user_id=user_id)
        if audiobook_dest:
            return Path(
                _expand_user_destination_placeholder(
                    str(audiobook_dest),
                    user_id=user_id,
                    username=username,
                )
            )

    # Main destination (also fallback for audiobooks)
    # Check new setting first, then legacy INGEST_DIR
    destination = config.get("DESTINATION", "", user_id=user_id) or config.get("INGEST_DIR", "/books")
    return Path(
        _expand_user_destination_placeholder(
            str(destination),
            user_id=user_id,
            username=username,
        )
    )


def get_aa_content_type_dir(content_type: Optional[str] = None) -> Optional[Path]:
    """Get override directory for AA content-type routing if configured."""
    from shelfmark.core.config import config

    # Check if content-type routing is enabled (new or legacy setting)
    if not config.get("AA_CONTENT_TYPE_ROUTING", False) and not config.get("USE_CONTENT_TYPE_DIRECTORIES", False):
        return None

    if not content_type:
        return None

    content_type_lower = content_type.lower().strip()

    # Try new AA-specific config keys first, then legacy keys
    for mapping in (_AA_CONTENT_TYPE_TO_CONFIG_KEY, _LEGACY_CONTENT_TYPE_TO_CONFIG_KEY):
        config_key = mapping.get(content_type_lower)
        if config_key:
            custom_dir = config.get(config_key, "")
            if custom_dir:
                return Path(custom_dir)

    return None


def get_ingest_dir(content_type: Optional[str] = None) -> Path:
    """DEPRECATED: Use get_destination() and get_aa_content_type_dir() instead."""
    from shelfmark.core.config import config

    # Check new DESTINATION setting first, then legacy INGEST_DIR
    default_ingest_dir = Path(config.get("DESTINATION", "") or config.get("INGEST_DIR", "/books"))

    if not content_type:
        return default_ingest_dir

    # Check for content-type override
    override_dir = get_aa_content_type_dir(content_type)
    if override_dir:
        return override_dir

    return default_ingest_dir


def transform_cover_url(cover_url: Optional[str], cache_id: str) -> Optional[str]:
    """Transform external cover URL to local proxy URL when caching is enabled."""
    if not cover_url:
        return cover_url

    # Skip if already a local URL (starts with /)
    if cover_url.startswith('/'):
        return cover_url

    # Check if cover caching is enabled
    from shelfmark.config.env import is_covers_cache_enabled
    if not is_covers_cache_enabled():
        return cover_url

    from shelfmark.core.config import config as app_config

    # Encode the original URL and create a proxy URL
    encoded_url = base64.urlsafe_b64encode(cover_url.encode()).decode()
    base_path = normalize_base_path(app_config.get("URL_BASE", ""))
    if base_path:
        return f"{base_path}/api/covers/{cache_id}?url={encoded_url}"
    return f"/api/covers/{cache_id}?url={encoded_url}"
