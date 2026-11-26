"""Tests for targeted image cache safety and fetch fallbacks."""

import pytest
import requests

from shelfmark.core.image_cache import ImageCacheService
from shelfmark.download.network import get_session as _shared_session


def test_fetch_and_cache_rejects_backslash_authority_bypass_before_request(
    tmp_path, monkeypatch
) -> None:
    cache = ImageCacheService(tmp_path)
    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        raise AssertionError("unsafe URL should not be requested")

    monkeypatch.setattr(_shared_session(), "get", fake_get)

    assert cache.fetch_and_cache("cover-ssrf", "http://127.0.0.1:6666\\@1.1.1.1") is None
    assert calls == []
    assert "cover-ssrf" not in cache._index


def test_is_safe_url_rejects_encoded_separator_in_authority() -> None:
    assert ImageCacheService._is_safe_url("http://127.0.0.1:6666%5c@1.1.1.1") is False
    assert ImageCacheService._is_safe_url("http://127.0.0.1:6666%2f@1.1.1.1") is False


def test_is_safe_url_rejects_invalid_ipv6_url() -> None:
    assert ImageCacheService._is_safe_url("http://[") is False


def test_fetch_and_cache_blocks_unsafe_redirect(tmp_path, monkeypatch) -> None:
    cache = ImageCacheService(tmp_path)

    def fake_getaddrinfo(hostname, *_args, **_kwargs):
        addresses = {
            "example.com": "93.184.216.34",
            "127.0.0.1": "127.0.0.1",
        }
        return [(None, None, None, None, (addresses[hostname], 0))]

    class RedirectResponse:
        is_redirect = True
        headers = {"location": "http://127.0.0.1/cover.jpg"}

        def close(self):
            return None

    calls = []

    def fake_get(url, **_kwargs):
        calls.append(url)
        return RedirectResponse()

    monkeypatch.setattr("shelfmark.download.network.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(_shared_session(), "get", fake_get)

    assert cache.fetch_and_cache("cover-redirect", "https://example.com/cover.jpg") is None
    assert calls == ["https://example.com/cover.jpg"]
    assert "cover-redirect" not in cache._index


def test_fetch_and_cache_returns_none_on_request_exception(tmp_path, monkeypatch) -> None:
    cache = ImageCacheService(tmp_path)

    def fake_get(*_args, **_kwargs):
        raise requests.exceptions.TooManyRedirects("too many redirects")

    monkeypatch.setattr(_shared_session(), "get", fake_get)

    assert cache.fetch_and_cache("cover-1", "https://example.com/cover.jpg") is None
    assert "cover-1" not in cache._index


class _StreamedImage:
    """A final image response that records whether it was closed."""

    is_redirect = False

    def __init__(self, status=200, content_type="image/png", chunks=(b"x",)):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._chunks = chunks
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError("bad", response=self)

    def iter_content(self, chunk_size=8192):
        yield from self._chunks

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    "response",
    [
        _StreamedImage(status=404),
        _StreamedImage(content_type="text/html"),
        _StreamedImage(chunks=(b"",)),
        _StreamedImage(chunks=(b"x" * 8192,)),
        _StreamedImage(),
    ],
    ids=["http-error", "not-an-image", "empty-body", "too-large", "cached"],
)
def test_fetch_and_cache_closes_the_final_response_on_every_exit(
    tmp_path, monkeypatch, response
) -> None:
    cache = ImageCacheService(tmp_path)
    monkeypatch.setattr("shelfmark.core.image_cache.MAX_IMAGE_SIZE", 100)
    monkeypatch.setattr(cache, "_prepare_safe_url", lambda url: url)
    monkeypatch.setattr(_shared_session(), "get", lambda *_a, **_k: response)

    cache.fetch_and_cache("cover-close", "https://example.com/cover.png")

    assert response.closed is True


def test_cover_cache_key_depends_on_the_url_only() -> None:
    from shelfmark.core.image_cache import cover_cache_key

    key = cover_cache_key("https://example.com/cover.jpg")

    assert key == cover_cache_key("  https://example.com/cover.jpg \n")
    assert key != cover_cache_key("https://example.com/other.jpg")
    assert key != cover_cache_key("https://example.com/cover.jpg?size=large")
    assert key.isalnum() and key == key.lower()
    assert "/" not in key and "." not in key


def test_cache_hits_do_not_rewrite_the_index_each_time(tmp_path, monkeypatch) -> None:
    """A hit only moves the last-access time, so the index is flushed on a timer."""
    cache = ImageCacheService(tmp_path)
    assert cache.put("cover-a", b"\xff\xd8\xff" + b"0" * 64, "image/jpeg")
    saves: list[int] = []
    real_save = cache._save_index
    monkeypatch.setattr(cache, "_save_index", lambda: (saves.append(1), real_save())[1])

    before = cache._index["cover-a"]["accessed_at"]
    for _ in range(5):
        assert cache.get("cover-a") is not None

    assert saves == []
    assert cache._index["cover-a"]["accessed_at"] >= before


def test_cache_hit_flushes_the_index_once_the_window_has_passed(tmp_path, monkeypatch) -> None:
    from shelfmark.core import image_cache

    cache = ImageCacheService(tmp_path)
    assert cache.put("cover-a", b"\xff\xd8\xff" + b"0" * 64, "image/jpeg")
    saves: list[int] = []
    real_save = cache._save_index
    monkeypatch.setattr(cache, "_save_index", lambda: (saves.append(1), real_save())[1])

    now = cache._last_saved
    # One second past the window. Exactly on it, (now + 30) - now can come out a hair under 30
    # for the small monotonic values of a freshly booted runner.
    monkeypatch.setattr(
        image_cache.time, "monotonic", lambda: now + image_cache.ACCESS_FLUSH_SECONDS + 1
    )
    assert cache.get("cover-a") is not None
    assert saves == [1]

    # The flush restarts the window.
    assert cache.get("cover-a") is not None
    assert saves == [1]
