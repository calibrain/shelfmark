from __future__ import annotations

from typing import Any

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download.outputs.booklore import (
    BookloreConfig,
    BookloreError,
    booklore_list_libraries,
    booklore_login,
)

logger = setup_logger(__name__)

_BOOKLORE_OPTIONS_CACHE: dict[str, Any] = {
    "key": None,
    "library_options": [],
    "path_options": [],
}


def _get_booklore_cache_key(base_url: str, username: str, password: str) -> str:
    return f"{base_url}|{username}|{hash(password)}"


def _get_booklore_select_options(
    base_url: str,
    username: str,
    password: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # library_id/path_id are not used for login/library listing
    booklore_config = BookloreConfig(
        base_url=base_url.rstrip("/"),
        username=username,
        password=password,
        library_id=1,
        path_id=1,
        verify_tls=True,
        refresh_after_upload=True,
    )

    token = booklore_login(booklore_config)
    libraries = booklore_list_libraries(booklore_config, token) or []
    logger.debug("Booklore libraries response: %s", libraries)

    library_options: list[dict[str, Any]] = []
    path_options: list[dict[str, Any]] = []

    for library in libraries:
        if not isinstance(library, dict):
            continue

        library_id = library.get("id")
        if library_id is None:
            continue

        library_name = str(library.get("name") or f"Library {library_id}")
        library_id_str = str(library_id)

        library_options.append({"value": library_id_str, "label": library_name})

        paths = library.get("paths") or []
        if not isinstance(paths, list):
            continue

        for path in paths:
            if not isinstance(path, dict):
                continue

            path_id = path.get("id")
            if path_id is None:
                continue

            path_label = str(path.get("path") or f"Path {path_id}")
            path_options.append(
                {
                    "value": str(path_id),
                    "label": f"{library_name}: {path_label}",
                    "childOf": library_id_str,
                }
            )

    logger.debug(
        "Booklore options built: libraries=%d paths=%d",
        len(library_options),
        len(path_options),
    )

    cache_key = _get_booklore_cache_key(base_url, username, password)
    _BOOKLORE_OPTIONS_CACHE.update(
        {
            "key": cache_key,
            "library_options": library_options,
            "path_options": path_options,
        }
    )

    return library_options, path_options


def _get_booklore_cached_options(
    base_url: str,
    username: str,
    password: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cache_key = _get_booklore_cache_key(base_url, username, password)
    if _BOOKLORE_OPTIONS_CACHE.get("key") == cache_key:
        return (
            _BOOKLORE_OPTIONS_CACHE.get("library_options", []),
            _BOOKLORE_OPTIONS_CACHE.get("path_options", []),
        )

    return _get_booklore_select_options(base_url, username, password)


def get_booklore_library_options() -> list[dict[str, Any]]:
    """Build Booklore library options dynamically from config."""
    if config.get("BOOKS_OUTPUT_MODE", "folder") != "booklore":
        return []

    base_url = str(config.get("BOOKLORE_HOST", "") or "").strip().rstrip("/")
    username = str(config.get("BOOKLORE_USERNAME", "") or "").strip()
    password = config.get("BOOKLORE_PASSWORD", "") or ""

    if not base_url or not username or not password:
        return []

    cache_key = _get_booklore_cache_key(base_url, username, password)

    try:
        library_options, _ = _get_booklore_cached_options(base_url, username, password)
        return library_options
    except Exception as exc:
        logger.error(f"Failed to fetch Booklore libraries: {exc}")
        if _BOOKLORE_OPTIONS_CACHE.get("key") == cache_key:
            return _BOOKLORE_OPTIONS_CACHE.get("library_options", [])
        return []


def get_booklore_path_options() -> list[dict[str, Any]]:
    """Build Booklore path options dynamically from config."""
    if config.get("BOOKS_OUTPUT_MODE", "folder") != "booklore":
        return []

    base_url = str(config.get("BOOKLORE_HOST", "") or "").strip().rstrip("/")
    username = str(config.get("BOOKLORE_USERNAME", "") or "").strip()
    password = config.get("BOOKLORE_PASSWORD", "") or ""

    if not base_url or not username or not password:
        return []

    cache_key = _get_booklore_cache_key(base_url, username, password)

    try:
        _, path_options = _get_booklore_cached_options(base_url, username, password)
        return path_options
    except Exception as exc:
        logger.error(f"Failed to fetch Booklore paths: {exc}")
        if _BOOKLORE_OPTIONS_CACHE.get("key") == cache_key:
            return _BOOKLORE_OPTIONS_CACHE.get("path_options", [])
        return []


def test_booklore_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test the Booklore connection using current form values."""
    current_values = current_values or {}

    def _get_value(key: str, default: Any = None) -> Any:
        value = current_values.get(key)
        if value not in (None, ""):
            return value
        if default is None:
            return config.get(key)
        return config.get(key, default)

    base_url = str(_get_value("BOOKLORE_HOST", "") or "").strip().rstrip("/")
    username = str(_get_value("BOOKLORE_USERNAME", "") or "").strip()
    password = _get_value("BOOKLORE_PASSWORD", "") or ""

    if not base_url:
        return {"success": False, "message": "Booklore URL is required"}
    if not username:
        return {"success": False, "message": "Booklore username is required"}
    if not password:
        return {"success": False, "message": "Booklore password is required"}

    try:
        library_options, _ = _get_booklore_select_options(base_url, username, password)

        message = "Connected to Booklore"
        if library_options:
            message = f"Connected to Booklore ({len(library_options)} libraries)"

        return {"success": True, "message": message}
    except BookloreError as exc:
        return {"success": False, "message": str(exc)}
