"""Tests for general settings definitions."""

from shelfmark.config.settings import general_settings


def test_supported_formats_stay_admin_only():
    """The format lists describe the library, not a reader, so they are not overridable."""
    fields = {field.key: field for field in general_settings() if hasattr(field, "key")}

    assert fields["SUPPORTED_FORMATS"].user_overridable is False
    assert fields["SUPPORTED_AUDIOBOOK_FORMATS"].user_overridable is False
