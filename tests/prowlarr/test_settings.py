"""Tests for Prowlarr settings registration."""

from shelfmark.release_sources.prowlarr.settings import prowlarr_config_settings


def test_prowlarr_settings_include_seed_preferences_toggle():
    fields = {field.key: field for field in prowlarr_config_settings()}

    field = fields["PROWLARR_USE_SEED_PREFERENCES"]
    assert field.default is False
    assert field.show_when == {"field": "PROWLARR_ENABLED", "value": True}
