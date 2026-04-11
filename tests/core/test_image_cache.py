"""Tests for targeted image cache safety and fetch fallbacks."""

import requests

from shelfmark.core.image_cache import ImageCacheService


def test_is_safe_url_rejects_invalid_ipv6_url() -> None:
    assert ImageCacheService._is_safe_url("http://[") is False


def test_fetch_and_cache_returns_none_on_request_exception(tmp_path, monkeypatch) -> None:
    cache = ImageCacheService(tmp_path)
    monkeypatch.setattr(cache, "_is_safe_url", lambda _url: True)

    def fake_get(*args, **kwargs):
        raise requests.exceptions.TooManyRedirects("too many redirects")

    monkeypatch.setattr("shelfmark.core.image_cache.requests.get", fake_get)

    assert cache.fetch_and_cache("cover-1", "https://example.com/cover.jpg") is None
    assert "cover-1" not in cache._index
