"""
Newznab release cache.

Stores search results so the handler can look up releases by source_id.
"""

import time
from threading import Lock
from typing import Dict, Optional

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

RELEASE_CACHE_TTL = 3600  # 1 hour

_cache: Dict[str, tuple] = {}
_cache_lock = Lock()


def cache_release(source_id: str, release_data: dict) -> None:
    with _cache_lock:
        _cache[source_id] = (release_data, time.time())


def get_release(source_id: str) -> Optional[dict]:
    with _cache_lock:
        if source_id not in _cache:
            logger.debug(f"Newznab release not in cache: {source_id}")
            return None

        release_data, cached_at = _cache[source_id]
        if time.time() - cached_at > RELEASE_CACHE_TTL:
            del _cache[source_id]
            logger.debug(f"Newznab release expired: {source_id}")
            return None

        return release_data


def remove_release(source_id: str) -> None:
    with _cache_lock:
        if source_id in _cache:
            del _cache[source_id]
            logger.debug(f"Removed Newznab release from cache: {source_id}")


def cleanup_expired() -> int:
    current_time = time.time()
    removed = 0
    with _cache_lock:
        expired_ids = [
            sid for sid, (_, cached_at) in _cache.items()
            if current_time - cached_at > RELEASE_CACHE_TTL
        ]
        for sid in expired_ids:
            del _cache[sid]
            removed += 1
    if removed:
        logger.debug(f"Cleaned up {removed} expired Newznab cache entries")
    return removed
