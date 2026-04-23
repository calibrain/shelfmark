"""Tests for targeted image cache safety and fetch fallbacks."""

from io import BytesIO

import requests
from PIL import Image

from shelfmark.core.image_cache import (
    ImageCacheService,
    build_variant_cache_id,
    create_image_variant,
    normalize_variant_dimension,
    normalize_variant_format,
)


def _make_image_bytes(
    *, width: int = 400, height: int = 600, image_format: str = "JPEG", color: str = "navy"
) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format=image_format)
    return buffer.getvalue()


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


def test_create_image_variant_resizes_and_transcodes_to_webp() -> None:
    variant = create_image_variant(
        _make_image_bytes(),
        width=120,
        height=180,
        image_format="webp",
    )

    assert variant is not None
    variant_bytes, content_type = variant
    assert content_type == "image/webp"

    with Image.open(BytesIO(variant_bytes)) as image:
        assert image.size == (120, 180)


def test_create_image_variant_preserves_aspect_ratio_for_single_dimension() -> None:
    variant = create_image_variant(
        _make_image_bytes(),
        width=120,
        image_format="jpeg",
    )

    assert variant is not None
    variant_bytes, content_type = variant
    assert content_type == "image/jpeg"

    with Image.open(BytesIO(variant_bytes)) as image:
        assert image.size == (120, 180)


def test_create_image_variant_returns_none_when_no_change_needed() -> None:
    image_bytes = _make_image_bytes(image_format="WEBP")

    assert create_image_variant(image_bytes, image_format="webp") is None


def test_variant_helpers_normalize_requested_variant_values() -> None:
    assert normalize_variant_dimension("240") == 240
    assert normalize_variant_dimension("0") is None
    assert normalize_variant_dimension("99999") == 1024
    assert normalize_variant_format("jpg") == "jpeg"
    assert normalize_variant_format("weBp") == "webp"
    assert normalize_variant_format("gif") is None
    assert (
        build_variant_cache_id(
            "cover-123",
            width=120,
            height=180,
            image_format="webp",
        )
        == "cover-123__w120_h180_fwebp"
    )
