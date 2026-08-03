"""Tests for the immutable Book storage root."""

from pathlib import Path


def test_get_destination_uses_the_configured_storage_root(monkeypatch):
    import shelfmark.core.utils as utils
    from shelfmark.core.config import config

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None, **_kwargs: {"DESTINATION": "/srv/books"}.get(key, default),
    )

    assert utils.get_destination() == Path("/srv/books")
