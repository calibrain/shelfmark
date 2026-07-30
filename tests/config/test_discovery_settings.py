"""Tests for release discovery settings definitions."""

from shelfmark.config.settings import discovery_settings


def test_discovery_settings_include_release_source_links_toggle():
    fields = {field.key: field for field in discovery_settings() if hasattr(field, "key")}

    field = fields["SHOW_RELEASE_SOURCE_LINKS"]

    assert field.label == "Show Release Source Links"
    assert field.default is True
    assert field.user_overridable is False
