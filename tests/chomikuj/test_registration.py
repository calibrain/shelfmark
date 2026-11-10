"""Tests for the Chomikuj built-in source registration."""

from __future__ import annotations

import requests

from shelfmark.core.settings_registry import get_settings_tab
from shelfmark.release_sources import (
    _BUILTIN_SOURCE_MODULES,
    _ensure_builtin_sources_registered,
    get_handler,
    get_source,
    list_available_sources,
)


def test_chomikuj_source_handler_and_settings_resolve():
    """Test chomikuj source handler and settings resolve."""
    _ensure_builtin_sources_registered()

    source = get_source("chomikuj")
    assert source.name == "chomikuj"
    assert source.display_name == "Chomikuj.pl"

    handler = get_handler("chomikuj")
    assert handler is not None

    tab = get_settings_tab("chomikuj_config")
    assert tab is not None
    assert tab.display_name == "Chomikuj.pl"


def test_list_available_sources_exposes_chomikuj():
    """Test list available sources exposes chomikuj."""
    rows = [row for row in list_available_sources() if row["name"] == "chomikuj"]
    assert len(rows) == 1

    row = rows[0]
    assert row["display_name"] == "Chomikuj.pl"
    assert row["supported_content_types"] == ["ebook"]
    assert row["browse_results_are_releases"] is False


def test_builtin_source_modules_lists_chomikuj_once():
    """Test builtin source modules lists chomikuj once."""
    assert _BUILTIN_SOURCE_MODULES.count("shelfmark.release_sources.chomikuj") == 1
    assert list(_BUILTIN_SOURCE_MODULES) == sorted(_BUILTIN_SOURCE_MODULES)


def test_no_network_on_availability(monkeypatch):
    """Test no network on availability."""

    def _boom(*args, **kwargs):
        """Fail when any HTTP request is attempted."""
        raise AssertionError("network access during registration/availability")

    monkeypatch.setattr(requests.Session, "request", _boom)

    assert get_source("chomikuj").is_available() is False
    list_available_sources()
