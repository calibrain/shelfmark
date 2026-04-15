"""API tests for the frontend config endpoint."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _set_session(client, *, user_id: str, db_user_id: int, is_admin: bool) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["db_user_id"] = db_user_id
        sess["is_admin"] = is_admin


def test_config_endpoint_uses_user_scope_and_runtime_flags(main_module, client):
    _set_session(client, user_id="reader-1", db_user_id=42, is_admin=False)

    calls: list[tuple[str, int | None]] = []

    def fake_get(key, default=None, user_id=None):
        calls.append((key, user_id))
        values = {
            "SHOW_RELEASE_SOURCE_LINKS": False,
            "SHOW_COMBINED_SELECTOR": False,
            "SEARCH_MODE": "universal",
            "SEARCH_PAGE_TITLE": "Custom Shelfmark",
            "METADATA_PROVIDER": "openlibrary",
            "METADATA_PROVIDER_AUDIOBOOK": "",
            "DEFAULT_RELEASE_SOURCE": "prowlarr",
            "DEFAULT_RELEASE_SOURCE_AUDIOBOOK": "audiobookbay",
            "DOWNLOAD_TO_BROWSER_CONTENT_TYPES": ["book", "audiobook"],
            "AUTO_OPEN_DOWNLOADS_SIDEBAR": False,
            "HARDCOVER_AUTO_REMOVE_ON_DOWNLOAD": True,
            "AA_DEFAULT_SORT": "newest",
        }
        return values.get(key, default)

    with (
        patch.object(main_module.app_config, "get", side_effect=fake_get),
        patch("shelfmark.config.env._is_config_dir_writable", return_value=True),
        patch("shelfmark.core.onboarding.is_onboarding_complete", return_value=True),
        patch("shelfmark.metadata_providers.get_provider_sort_options", return_value=["sort-a"]),
        patch("shelfmark.metadata_providers.get_provider_search_fields", return_value=["field-a"]),
        patch("shelfmark.metadata_providers.get_provider_default_sort", return_value="relevance"),
    ):
        resp = client.get("/api/config")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["show_release_source_links"] is False
    assert data["show_combined_selector"] is False
    assert data["search_mode"] == "universal"
    assert data["search_page_title"] == "Custom Shelfmark"
    assert data["metadata_sort_options"] == ["sort-a"]
    assert data["metadata_search_fields"] == ["field-a"]
    assert data["default_release_source"] == "prowlarr"
    assert data["default_release_source_audiobook"] == "audiobookbay"
    assert data["download_to_browser_content_types"] == ["book", "audiobook"]
    assert data["settings_enabled"] is True
    assert data["metadata_default_sort"] == "relevance"

    assert ("SHOW_RELEASE_SOURCE_LINKS", None) in calls
    assert ("SHOW_COMBINED_SELECTOR", 42) in calls
    assert ("DOWNLOAD_TO_BROWSER_CONTENT_TYPES", 42) in calls


def test_config_endpoint_falls_back_to_audiobook_metadata_provider(main_module, client):
    _set_session(client, user_id="reader-2", db_user_id=77, is_admin=False)

    provider_calls: list[str] = []

    def fake_get(key, default=None, user_id=None):
        values = {
            "METADATA_PROVIDER": "",
            "METADATA_PROVIDER_AUDIOBOOK": "audiobook-search",
            "SHOW_RELEASE_SOURCE_LINKS": True,
        }
        return values.get(key, default)

    def sort_options(provider: str):
        provider_calls.append(provider)
        return [f"{provider}-sort"]

    def search_fields(provider: str):
        provider_calls.append(provider)
        return [f"{provider}-field"]

    def default_sort(provider: str):
        provider_calls.append(provider)
        return f"{provider}-default"

    with (
        patch.object(main_module.app_config, "get", side_effect=fake_get),
        patch("shelfmark.config.env._is_config_dir_writable", return_value=True),
        patch("shelfmark.core.onboarding.is_onboarding_complete", return_value=True),
        patch("shelfmark.metadata_providers.get_provider_sort_options", side_effect=sort_options),
        patch("shelfmark.metadata_providers.get_provider_search_fields", side_effect=search_fields),
        patch("shelfmark.metadata_providers.get_provider_default_sort", side_effect=default_sort),
    ):
        resp = client.get("/api/config")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["metadata_sort_options"] == ["audiobook-search-sort"]
    assert data["metadata_search_fields"] == ["audiobook-search-field"]
    assert data["metadata_default_sort"] == "audiobook-search-default"
    assert provider_calls == ["audiobook-search", "audiobook-search", "audiobook-search"]


def test_frontend_dist_resolves_from_repo_root(main_module):
    expected_project_root = Path(main_module.__file__).resolve().parent.parent

    assert main_module.PROJECT_ROOT == expected_project_root
    assert main_module.FRONTEND_DIST == expected_project_root / "frontend-dist"
