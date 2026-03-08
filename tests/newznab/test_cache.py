"""Unit tests for the Newznab release cache."""

import time
from unittest.mock import patch

import pytest

from shelfmark.release_sources.newznab import cache as cache_module
from shelfmark.release_sources.newznab.cache import (
    cache_release,
    cleanup_expired,
    get_release,
    remove_release,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure the cache is empty before and after each test."""
    cache_module._cache.clear()
    yield
    cache_module._cache.clear()


class TestCacheRelease:
    def test_stores_and_retrieves_release(self):
        data = {"title": "My Book", "downloadUrl": "https://example.com/nzb/1"}
        cache_release("id-1", data)
        assert get_release("id-1") == data

    def test_overwrites_existing_entry(self):
        cache_release("id-1", {"title": "Old"})
        cache_release("id-1", {"title": "New"})
        assert get_release("id-1")["title"] == "New"


class TestGetRelease:
    def test_returns_none_for_unknown_id(self):
        assert get_release("no-such-id") is None

    def test_returns_none_after_ttl_expires(self):
        cache_release("id-ttl", {"title": "Expiring"})
        past = time.time() - cache_module.RELEASE_CACHE_TTL - 1
        cache_module._cache["id-ttl"] = (cache_module._cache["id-ttl"][0], past)
        assert get_release("id-ttl") is None

    def test_expired_entry_is_removed(self):
        cache_release("id-evict", {"title": "Gone"})
        past = time.time() - cache_module.RELEASE_CACHE_TTL - 1
        cache_module._cache["id-evict"] = (cache_module._cache["id-evict"][0], past)
        get_release("id-evict")
        assert "id-evict" not in cache_module._cache

    def test_fresh_entry_not_expired(self):
        cache_release("id-fresh", {"title": "Fresh"})
        # Advance time by less than TTL
        future = time.time() + cache_module.RELEASE_CACHE_TTL - 60
        with patch("shelfmark.release_sources.newznab.cache.time") as mock_time:
            mock_time.time.return_value = future
            result = get_release("id-fresh")
        assert result is not None


class TestRemoveRelease:
    def test_removes_existing_entry(self):
        cache_release("id-remove", {"title": "To Remove"})
        remove_release("id-remove")
        assert get_release("id-remove") is None

    def test_no_error_when_removing_absent_entry(self):
        remove_release("no-such-id")  # should not raise


class TestCleanupExpired:
    def test_removes_only_expired_entries(self):
        cache_release("fresh", {"title": "Fresh"})
        cache_release("stale", {"title": "Stale"})

        past = time.time() - cache_module.RELEASE_CACHE_TTL - 1
        cache_module._cache["stale"] = (cache_module._cache["stale"][0], past)

        removed = cleanup_expired()
        assert removed == 1
        assert get_release("fresh") is not None
        assert "stale" not in cache_module._cache

    def test_returns_zero_when_nothing_expired(self):
        cache_release("a", {})
        cache_release("b", {})
        assert cleanup_expired() == 0

    def test_returns_zero_when_cache_empty(self):
        assert cleanup_expired() == 0
