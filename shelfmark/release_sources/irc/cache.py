"""Persistent file-based cache for IRC search results.

Stores search results in CONFIG_DIR to survive container restarts.
IRC searches are slow and resource-intensive, so we cache aggressively.
"""

import json
import time
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any

from shelfmark.config import env
from shelfmark.core.logger import setup_logger
from shelfmark.release_sources import Release, ReleaseProtocol

logger = setup_logger(__name__)

# Cache file location
CACHE_FILE = Path(env.CONFIG_DIR) / "irc_cache.json"

# Default TTL: 30 days (in seconds)
DEFAULT_CACHE_TTL = 30 * 24 * 60 * 60

# Lock for thread-safe file access
_cache_lock = Lock()


def _coerce_cache_ttl(value: object, default: int) -> int:
    """Coerce a cache TTL value from config into a non-negative integer."""
    if isinstance(value, int) and not isinstance(value, bool):
        return max(value, 0)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return max(int(stripped), 0)
            except ValueError:
                return default
    return default


def _coerce_timestamp(value: object) -> float:
    """Coerce cached timestamps into floats for age calculations."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return float(stripped)
            except ValueError:
                return 0.0
    return 0.0


def _load_cache() -> dict[str, Any]:
    """Load cache from disk."""
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load IRC cache: %s", e)
    return {"entries": {}, "version": 1}


def _save_cache(cache: dict[str, Any]) -> None:
    """Save cache to disk."""
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except OSError:
        logger.exception("Failed to save IRC cache")


def _release_to_dict(release: Release) -> dict[str, Any]:
    """Convert Release to a JSON-serializable dict."""
    data = asdict(release)
    # Convert enum to string
    if data.get("protocol"):
        data["protocol"] = (
            data["protocol"].value if hasattr(data["protocol"], "value") else str(data["protocol"])
        )
    return data


def _dict_to_release(data: dict[str, Any]) -> Release:
    """Convert dict back to Release object."""
    # Convert protocol string back to enum
    if data.get("protocol"):
        try:
            data["protocol"] = ReleaseProtocol(data["protocol"])
        except ValueError, KeyError:
            data["protocol"] = None
    return Release(**data)


def get_cached_results(
    cache_key: str,
    ttl_seconds: int | None = None,
) -> dict[str, Any] | None:
    """Get the cached IRC answer for a query identity (server:channel:query).

    The cache stores the whole answer (releases for all content types) under the query
    identity, so it is not isolated by book or content type. Callers filter by content
    type after reading.

    Args:
        cache_key: Query identity (e.g. "server:channel:query")
        ttl_seconds: Cache TTL in seconds (from settings)

    Returns:
        Dict with 'releases' (List[Release]) and 'online_servers' (List[str]),
        or None if not cached or expired

    """
    from shelfmark.core.config import config

    if ttl_seconds is None:
        ttl_value = config.get("IRC_CACHE_TTL", DEFAULT_CACHE_TTL)
        ttl_seconds = _coerce_cache_ttl(ttl_value, DEFAULT_CACHE_TTL)

    with _cache_lock:
        cache = _load_cache()
        entry = cache.get("entries", {}).get(cache_key)

        if not entry:
            return None

        # Check expiration
        cached_at = _coerce_timestamp(entry.get("cached_at", 0))
        age = time.time() - cached_at

        if ttl_seconds != 0 and age > ttl_seconds:
            logger.debug(
                "IRC cache expired for '%s' (age: %.0fs > TTL: %ss)",
                entry.get("title", cache_key),
                age,
                ttl_seconds,
            )
            # Don't delete here - let cleanup handle it
            return None

        # Convert dicts back to Release objects
        releases = [_dict_to_release(r) for r in entry.get("releases", [])]

        logger.info(
            "IRC cache hit for '%s' (%s releases, age: %.0fs)",
            entry.get("title", ""),
            len(releases),
            age,
        )

        return {
            "releases": releases,
            "online_servers": entry.get("online_servers", []),
            "cached_at": cached_at,
        }


def cache_results(
    cache_key: str,
    title: str,
    releases: list[Release],
    online_servers: list[str] | None = None,
) -> None:
    """Cache the whole IRC answer for a query identity.

    Args:
        cache_key: Query identity (e.g. "server:channel:query")
        title: Query text (for logging/display)
        releases: All Release objects from the search (every content type)
        online_servers: List of online server nicks (optional)

    """
    with _cache_lock:
        cache = _load_cache()

        if "entries" not in cache:
            cache["entries"] = {}

        cache["entries"][cache_key] = {
            "title": title,
            "releases": [_release_to_dict(r) for r in releases],
            "online_servers": list(online_servers) if online_servers else [],
            "cached_at": time.time(),
        }

        _save_cache(cache)
        logger.info("Cached %s IRC releases for '%s'", len(releases), title)


def invalidate_cache(cache_key: str) -> bool:
    """Remove a specific entry from the cache.

    Args:
        cache_key: Query identity to remove

    Returns:
        True if entry was found and removed

    """
    with _cache_lock:
        cache = _load_cache()
        entries = cache.get("entries", {})

        if cache_key in entries:
            title = entries[cache_key].get("title", cache_key)
            del entries[cache_key]
            _save_cache(cache)
            logger.info("Invalidated IRC cache for '%s'", title)
            return True

        return False


def clear_cache() -> int:
    """Clear all cached entries.

    Returns:
        Number of entries cleared

    """
    with _cache_lock:
        cache = _load_cache()
        count = len(cache.get("entries", {}))
        cache["entries"] = {}
        _save_cache(cache)
        logger.info("Cleared %s IRC cache entries", count)
        return count


def cleanup_expired(ttl_seconds: int | None = None) -> int:
    """Remove all expired entries from the cache.

    Returns:
        Number of entries removed

    """
    from shelfmark.core.config import config

    if ttl_seconds is None:
        ttl_value = config.get("IRC_CACHE_TTL", DEFAULT_CACHE_TTL)
        ttl_seconds = _coerce_cache_ttl(ttl_value, DEFAULT_CACHE_TTL)

    current_time = time.time()
    removed = 0

    with _cache_lock:
        cache = _load_cache()
        entries = cache.get("entries", {})

        expired_keys = [
            key
            for key, entry in entries.items()
            if ttl_seconds != 0
            and current_time - _coerce_timestamp(entry.get("cached_at", 0)) > ttl_seconds
        ]

        for key in expired_keys:
            del entries[key]
            removed += 1

        if removed:
            _save_cache(cache)
            logger.info("Cleaned up %s expired IRC cache entries", removed)

    return removed


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics.

    Returns:
        Dict with cache stats

    """
    from shelfmark.core.config import config

    ttl_value = config.get("IRC_CACHE_TTL", DEFAULT_CACHE_TTL)
    ttl_seconds = _coerce_cache_ttl(ttl_value, DEFAULT_CACHE_TTL)
    current_time = time.time()

    with _cache_lock:
        cache = _load_cache()
        entries = cache.get("entries", {})

        total = len(entries)
        expired = sum(
            1
            for entry in entries.values()
            if ttl_seconds != 0
            and current_time - _coerce_timestamp(entry.get("cached_at", 0)) > ttl_seconds
        )

        # Calculate total releases cached
        total_releases = sum(len(entry.get("releases", [])) for entry in entries.values())

        return {
            "total_entries": total,
            "expired_entries": expired,
            "valid_entries": total - expired,
            "total_releases": total_releases,
            "ttl_seconds": ttl_seconds,
            "cache_file": str(CACHE_FILE),
        }
