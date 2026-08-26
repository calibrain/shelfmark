"""Tests for named Newznab indexer settings."""

from shelfmark.core.settings_registry import TableField
from shelfmark.release_sources.newznab.settings import (
    _test_newznab_connection,
    newznab_config_settings,
)


def test_settings_include_named_indexer_table():
    field = next(
        field
        for field in newznab_config_settings()
        if getattr(field, "key", None) == "NEWZNAB_INDEXERS"
    )

    assert isinstance(field, TableField)
    assert [column["key"] for column in field.columns] == ["name", "url", "api_key"]
    assert field.columns[2]["type"] == "password"


def test_connection_action_tests_every_named_indexer(monkeypatch):
    import shelfmark.release_sources.newznab.api as api_module

    tested: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, url, api_key):
            tested.append((url, api_key))

        def test_connection(self):
            return True, "Connected"

    monkeypatch.setattr(api_module, "NewznabClient", FakeClient)

    result = _test_newznab_connection(
        {
            "NEWZNAB_INDEXERS": [
                {"name": "NZBGeek", "url": "https://geek.example", "api_key": "one"},
                {"name": "DrunkenSlug", "url": "https://slug.example", "api_key": "two"},
            ]
        }
    )

    assert result == {
        "success": True,
        "message": "Connected to all 2 indexers",
        "details": ["NZBGeek: Connected", "DrunkenSlug: Connected"],
    }
    assert tested == [("https://geek.example", "one"), ("https://slug.example", "two")]


def test_connection_action_reports_each_failure(monkeypatch):
    import shelfmark.release_sources.newznab.api as api_module

    class FakeClient:
        def __init__(self, url, _api_key):
            self.url = url

        def test_connection(self):
            if "down" in self.url:
                return False, "Could not connect"
            return True, "Connected"

    monkeypatch.setattr(api_module, "NewznabClient", FakeClient)

    result = _test_newznab_connection(
        {
            "NEWZNAB_INDEXERS": [
                {"name": "Working", "url": "https://working.example"},
                {"name": "Unavailable", "url": "https://down.example"},
            ]
        }
    )

    assert result["success"] is False
    assert result["message"] == "One or more Newznab indexers failed"
    assert result["details"] == ["Working: Connected", "Unavailable: Could not connect"]
