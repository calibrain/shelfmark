"""Persistent file-based cache for IRC search results.

Stores search results in CONFIG_DIR to survive container restarts.
IRC searches are slow and resource-intensive, so we cache aggressively.
"""

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

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


def _generate_cache_key(provider: str, provider_id: str) -> str:
    """Generate a cache key from provider and provider_id."""
    return f"{provider}:{provider_id}"


def _load_cache() -> Dict[str, Any]:
    """Load cache from disk."""
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text())
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load IRC cache: {e}")
    return {"entries": {}, "version": 1}


def _save_cache(cache: Dict[str, Any]) -> None:
    """Save cache to disk."""
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except IOError as e:
        logger.error(f"Failed to save IRC cache: {e}")


def _release_to_dict(release: Release) -> Dict[str, Any]:
    """Convert Release to a JSON-serializable dict."""
    data = asdict(release)
    # Convert enum to string
    if data.get("protocol"):
        data["protocol"] = data["protocol"].value if hasattr(data["protocol"], "value") else str(data["protocol"])
    return data


def _dict_to_release(data: Dict[str, Any]) -> Release:
    """Convert dict back to Release object."""
    # Convert protocol string back to enum
    if data.get("protocol"):
        try:
            data["protocol"] = ReleaseProtocol(data["protocol"])
        except (ValueError, KeyError):
            data["protocol"] = None
    return Release(**data)


def get_cached_results(
    provider: str,
    provider_id: str,
    ttl_seconds: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """
    Get cached search results for a book.

    Args:
        provider: Metadata provider name (e.g., "hardcover", "openlibrary")
        provider_id: Book ID in the provider's system
        ttl_seconds: Cache TTL in seconds (from settings)

    Returns:
        Dict with 'releases' (List[Release]) and 'online_servers' (List[str]),
        or None if not cached or expired
    """
    from shelfmark.core.config import config

    if ttl_seconds is None:
        ttl_value = config.get("IRC_CACHE_TTL", DEFAULT_CACHE_TTL)
        # Config values are stored as strings, convert to int
        ttl_seconds = int(ttl_value) if ttl_value else DEFAULT_CACHE_TTL

    # TTL of 0 means cache forever
    if ttl_seconds == 0:
        ttl_seconds = float('inf')

    cache_key = _generate_cache_key(provider, provider_id)

    with _cache_lock:
        cache = _load_cache()
        entry = cache.get("entries", {}).get(cache_key)

        if not entry:
            return None

        # Check expiration
        cached_at = entry.get("cached_at", 0)
        age = time.time() - cached_at

        if age > ttl_seconds:
            logger.debug(f"IRC cache expired for '{title}' (age: {age:.0f}s > TTL: {ttl_seconds}s)")
            # Don't delete here - let cleanup handle it
            return None

        # Convert dicts back to Release objects
        releases = [_dict_to_release(r) for r in entry.get("releases", [])]
        online_servers = entry.get("online_servers", [])
        title = entry.get("title", "")

        logger.info(f"IRC cache hit for '{title}' ({len(releases)} releases, age: {age:.0f}s)")

        return {
            "releases": releases,
            "online_servers": online_servers,
            "cached_at": cached_at,
        }


def cache_results(
    provider: str,
    provider_id: str,
    title: str,
    releases: List[Release],
    online_servers: Optional[List[str]] = None
) -> None:
    """
    Cache search results for a book.

    Args:
        provider: Metadata provider name
        provider_id: Book ID in the provider's system
        title: Book title (for logging/display)
        releases: List of Release objects from search
        online_servers: List of online server nicks (optional)
    """
    cache_key = _generate_cache_key(provider, provider_id)

    with _cache_lock:
        cache = _load_cache()

        if "entries" not in cache:
            cache["entries"] = {}

        cache["entries"][cache_key] = {
            "provider": provider,
            "provider_id": provider_id,
            "title": title,
            "releases": [_release_to_dict(r) for r in releases],
            "online_servers": list(online_servers) if online_servers else [],
            "cached_at": time.time(),
        }

        _save_cache(cache)
        logger.info(f"Cached {len(releases)} IRC releases for '{title}'")


def invalidate_cache(provider: str, provider_id: str) -> bool:
    """
    Remove a specific entry from the cache.

    Args:
        provider: Metadata provider name
        provider_id: Book ID in the provider's system

    Returns:
        True if entry was found and removed
    """
    cache_key = _generate_cache_key(provider, provider_id)

    with _cache_lock:
        cache = _load_cache()
        entry = cache.get("entries", {}).get(cache_key)
        title = entry.get("title", cache_key) if entry else cache_key

        if cache_key in cache.get("entries", {}):
            del cache["entries"][cache_key]
            _save_cache(cache)
            logger.info(f"Invalidated IRC cache for '{title}'")
            return True

        return False


def clear_cache() -> int:
    """
    Clear all cached entries.

    Returns:
        Number of entries cleared
    """
    with _cache_lock:
        cache = _load_cache()
        count = len(cache.get("entries", {}))
        cache["entries"] = {}
        _save_cache(cache)
        logger.info(f"Cleared {count} IRC cache entries")
        return count


def cleanup_expired(ttl_seconds: Optional[int] = None) -> int:
    """
    Remove all expired entries from the cache.

    Returns:
        Number of entries removed
    """
    from shelfmark.core.config import config

    if ttl_seconds is None:
        ttl_value = config.get("IRC_CACHE_TTL", DEFAULT_CACHE_TTL)
        # Config values are stored as strings, convert to int
        ttl_seconds = int(ttl_value) if ttl_value else DEFAULT_CACHE_TTL

    current_time = time.time()
    removed = 0

    with _cache_lock:
        cache = _load_cache()
        entries = cache.get("entries", {})

        expired_keys = [
            key for key, entry in entries.items()
            if current_time - entry.get("cached_at", 0) > ttl_seconds
        ]

        for key in expired_keys:
            del entries[key]
            removed += 1

        if removed:
            _save_cache(cache)
            logger.info(f"Cleaned up {removed} expired IRC cache entries")

    return removed


def get_cache_stats() -> Dict[str, Any]:
    """
    Get cache statistics.

    Returns:
        Dict with cache stats
    """
    from shelfmark.core.config import config

    ttl_value = config.get("IRC_CACHE_TTL", DEFAULT_CACHE_TTL)
    # Config values are stored as strings, convert to int
    ttl_seconds = int(ttl_value) if ttl_value else DEFAULT_CACHE_TTL
    current_time = time.time()

    with _cache_lock:
        cache = _load_cache()
        entries = cache.get("entries", {})

        total = len(entries)
        expired = sum(
            1 for entry in entries.values()
            if current_time - entry.get("cached_at", 0) > ttl_seconds
        )

        # Calculate total releases cached
        total_releases = sum(
            len(entry.get("releases", []))
            for entry in entries.values()
        )

        return {
            "total_entries": total,
            "expired_entries": expired,
            "valid_entries": total - expired,
            "total_releases": total_releases,
            "ttl_seconds": ttl_seconds,
            "cache_file": str(CACHE_FILE),
        }
