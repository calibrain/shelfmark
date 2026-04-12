"""
E2E API Tests.

Tests the full application flow through the HTTP API.

Run with: uv run pytest tests/e2e/ -v -m e2e
"""

import pytest

from .conftest import (
    APIClient,
    DownloadTracker,
    assert_queue_order_response,
    assert_queued_download_response,
)


def _assert_json_object(response, *, status_code: int = 200) -> dict:
    assert response.status_code == status_code
    data = response.json()
    assert isinstance(data, dict)
    return data


@pytest.mark.e2e
class TestHealthEndpoint:
    """Tests for the health check endpoint."""

    def test_health_returns_ok(self, api_client: APIClient):
        """Test that health endpoint returns 200."""
        resp = api_client.get("/api/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"

    def test_health_includes_status(self, api_client: APIClient):
        """Test that health endpoint includes status field."""
        resp = api_client.get("/api/health")

        data = resp.json()
        assert "status" in data
        assert data["status"] == "ok"


@pytest.mark.e2e
class TestConfigEndpoint:
    """Tests for the configuration endpoint."""

    def test_config_returns_expected_fields(self, protected_api_client: APIClient):
        """Test that config exposes the stable frontend contract."""
        data = _assert_json_object(protected_api_client.get("/api/config"))
        assert isinstance(data["supported_formats"], list)
        assert isinstance(data["supported_audiobook_formats"], list)
        assert isinstance(data["book_languages"], list)
        assert isinstance(data["settings_enabled"], bool)
        assert isinstance(data["onboarding_complete"], bool)
        assert isinstance(data["search_mode"], str)
        assert isinstance(data["default_release_source"], str)

    def test_config_returns_supported_formats(self, protected_api_client: APIClient):
        """Test that config includes supported formats."""
        data = _assert_json_object(protected_api_client.get("/api/config"))
        formats = data["supported_formats"]
        assert formats
        assert all(isinstance(fmt, str) for fmt in formats)
        assert "epub" in {fmt.lower() for fmt in formats}


@pytest.mark.e2e
class TestReleaseSourcesEndpoint:
    """Tests for the release sources endpoint."""

    def test_release_sources_returns_list(self, protected_api_client: APIClient):
        """Test that release sources endpoint returns available sources."""
        resp = protected_api_client.get("/api/release-sources")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_release_sources_have_required_fields(self, protected_api_client: APIClient):
        """Test that each release source has required fields."""
        data = protected_api_client.get("/api/release-sources").json()
        for source in data:
            assert set(source) == {
                "name",
                "display_name",
                "enabled",
                "supported_content_types",
                "browse_results_are_releases",
                "can_be_default",
            }
            assert isinstance(source["name"], str)
            assert isinstance(source["display_name"], str)
            assert isinstance(source["enabled"], bool)
            assert isinstance(source["supported_content_types"], list)
            assert isinstance(source["browse_results_are_releases"], bool)
            assert isinstance(source["can_be_default"], bool)


@pytest.mark.e2e
class TestMetadataProvidersEndpoint:
    """Tests for the metadata providers endpoint."""

    def test_providers_returns_data(self, protected_api_client: APIClient):
        """Test that providers endpoint returns the documented object contract."""
        data = _assert_json_object(protected_api_client.get("/api/metadata/providers"))
        assert set(data) == {
            "providers",
            "configured_provider",
            "configured_provider_audiobook",
            "configured_provider_combined",
        }
        assert isinstance(data["providers"], list)

    def test_providers_have_required_fields(self, protected_api_client: APIClient):
        """Test that each provider has required fields."""
        data = _assert_json_object(protected_api_client.get("/api/metadata/providers"))
        for provider in data["providers"]:
            assert set(provider) == {
                "name",
                "display_name",
                "requires_auth",
                "enabled",
                "available",
            }
            assert isinstance(provider["name"], str)
            assert isinstance(provider["display_name"], str)
            assert isinstance(provider["requires_auth"], bool)
            assert isinstance(provider["enabled"], bool)
            assert isinstance(provider["available"], bool)


@pytest.mark.e2e
class TestMetadataSearch:
    """Tests for metadata search functionality."""

    def test_search_requires_query(self, protected_api_client: APIClient):
        """Test that search requires a query parameter."""
        resp = protected_api_client.get("/api/metadata/search")

        assert resp.status_code == 400
        assert resp.json() == {"error": "Either 'query' or search field values are required"}

    def test_search_returns_results(self, protected_api_client: APIClient):
        """Test that search returns results for a known book."""
        resp = protected_api_client.get("/api/metadata/search", params={"query": "1984 Orwell"})

        if resp.status_code == 200:
            data = _assert_json_object(resp)
            assert isinstance(data["books"], list)
            assert isinstance(data["provider"], str)
            assert data["query"] == "1984 Orwell"
            assert isinstance(data["page"], int)
            assert isinstance(data["total_found"], int)
            assert isinstance(data["has_more"], bool)
        else:
            assert resp.status_code == 503
            data = resp.json()
            assert isinstance(data, dict)
            assert "error" in data
            assert "message" in data

    def test_search_with_provider_filter(self, protected_api_client: APIClient):
        """Test searching with a specific provider."""
        providers_resp = protected_api_client.get("/api/metadata/providers")
        if providers_resp.status_code != 200:
            pytest.skip("Could not get providers")

        providers_data = providers_resp.json()
        providers = providers_data.get("providers", [])
        if not providers:
            pytest.skip("No providers available")

        provider_name = providers[0]["name"]

        resp = protected_api_client.get(
            "/api/metadata/search",
            params={"query": "Moby Dick", "provider": provider_name},
        )

        if resp.status_code == 200:
            data = _assert_json_object(resp)
            assert data["provider"] == provider_name
            assert data["query"] == "Moby Dick"
            assert isinstance(data["books"], list)
        else:
            assert resp.status_code == 503
            data = resp.json()
            assert isinstance(data, dict)
            assert "error" in data


@pytest.mark.e2e
class TestStatusEndpoint:
    """Tests for the status endpoint."""

    def test_status_returns_categories(self, protected_api_client: APIClient):
        """Test that status endpoint returns expected categories."""
        resp = protected_api_client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        for status_name, tasks in data.items():
            assert isinstance(status_name, str)
            assert isinstance(tasks, dict)

    def test_active_downloads_endpoint(self, protected_api_client: APIClient):
        """Test the active downloads endpoint."""
        resp = protected_api_client.get("/api/downloads/active")

        assert resp.status_code == 200
        data = resp.json()
        assert data == {"active_downloads": data["active_downloads"]}
        assert isinstance(data["active_downloads"], list)


@pytest.mark.e2e
class TestQueueEndpoint:
    """Tests for queue management endpoints."""

    def test_queue_order_returns_data(self, protected_api_client: APIClient):
        """Test that queue order endpoint returns queue data."""
        resp = protected_api_client.get("/api/queue/order")

        queue = assert_queue_order_response(resp)
        assert isinstance(queue, list)


@pytest.mark.e2e
class TestSettingsEndpoint:
    """Tests for settings endpoints."""

    def test_settings_returns_tabs(self, protected_api_client: APIClient):
        """Test that settings endpoint returns tab structure."""
        resp = protected_api_client.get("/api/settings")

        # Settings may be disabled if config dir not writable
        if resp.status_code == 403:
            pytest.skip("Settings disabled (config dir not writable)")

        assert resp.status_code == 200
        data = resp.json()
        assert data == {"tabs": data["tabs"], "groups": data["groups"]}
        assert isinstance(data["tabs"], list)
        assert isinstance(data["groups"], list)
        for tab in data["tabs"]:
            assert isinstance(tab, dict)
            assert "name" in tab
            assert "fields" in tab
        for group in data["groups"]:
            assert isinstance(group, dict)
            assert "name" in group

    def test_get_specific_settings_tab(self, protected_api_client: APIClient):
        """Test getting a specific settings tab."""
        # First get available tabs
        resp = protected_api_client.get("/api/settings")
        if resp.status_code == 403:
            pytest.skip("Settings disabled")

        data = resp.json()
        tabs = data.get("tabs", []) if isinstance(data, dict) else []
        if not tabs:
            pytest.skip("No settings tabs available")

        tab_name = tabs[0].get("name")
        if not tab_name:
            pytest.skip("Could not determine tab name")

        resp = protected_api_client.get(f"/api/settings/{tab_name}")
        assert resp.status_code == 200
        tab_data = resp.json()
        assert isinstance(tab_data, dict)
        assert tab_data.get("name") == tab_name
        assert isinstance(tab_data.get("fields"), list)


@pytest.mark.e2e
class TestDownloadFlow:
    """Tests for the complete download flow."""

    def test_cancel_nonexistent_download(self, protected_api_client: APIClient):
        """Test cancelling a download that doesn't exist."""
        resp = protected_api_client.delete("/api/download/nonexistent-id-xyz/cancel")

        assert resp.status_code == 404
        data = resp.json()
        assert data.get("error") == "Failed to cancel download or book not found"


@pytest.mark.e2e
class TestReleaseDownloadFlow:
    """Tests for the release-based download flow (new API)."""

    def test_release_download_requires_source_id(self, protected_api_client: APIClient):
        """Test that release download requires source_id."""
        resp = protected_api_client.post(
            "/api/releases/download",
            json={"source": "test_source"},
        )

        assert resp.status_code == 400
        data = resp.json()
        assert data == {"error": "source_id is required"}

    def test_release_download_with_minimal_data(
        self, protected_api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test queueing a release with minimal valid data."""
        # This will queue but likely fail during download (no real source)
        test_id = "e2e-test-release-minimal"
        resp = protected_api_client.post(
            "/api/releases/download",
            json={
                "source": "test_source",
                "source_id": test_id,
                "title": "E2E Test Book",
            },
        )

        download_tracker.track(test_id)
        assert_queued_download_response(resp)

    def test_cancel_release_with_slash_id(
        self, protected_api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Cancelling/clearing should work for IDs containing slashes."""
        test_id = "e2e-test-release/with-slash"

        resp = protected_api_client.post(
            "/api/releases/download",
            json={
                "source": "test_source",
                "source_id": test_id,
                "title": "E2E Test Book",
            },
        )

        if resp.status_code != 200:
            pytest.skip("Release download endpoint not available")

        download_tracker.track(test_id)
        assert resp.json() == {"status": "queued", "priority": 0}

        cancel_resp = protected_api_client.delete(f"/api/download/{test_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json() == {"status": "cancelled", "book_id": test_id}


@pytest.mark.e2e
class TestReleasesSearch:
    """Tests for searching releases."""

    def test_releases_requires_params(self, protected_api_client: APIClient):
        """Test that releases endpoint requires provider and book_id."""
        resp = protected_api_client.get("/api/releases")

        assert resp.status_code == 400
        assert resp.json() == {"error": "Parameters 'provider' and 'book_id' are required"}

    def test_releases_with_invalid_provider(self, protected_api_client: APIClient):
        """Test releases with invalid provider."""
        resp = protected_api_client.get(
            "/api/releases",
            params={"provider": "nonexistent_provider", "book_id": "123"},
        )

        assert resp.status_code == 400
        assert resp.json() == {"error": "Unknown metadata provider: nonexistent_provider"}


@pytest.mark.e2e
class TestCoverProxy:
    """Tests for the cover image proxy."""

    def test_cover_without_url_returns_error(self, protected_api_client: APIClient):
        """Test that cover endpoint without URL returns error."""
        resp = protected_api_client.get("/api/covers/test-id")

        assert resp.status_code == 404
        assert resp.json() in [
            {"error": "Cover caching is disabled"},
            {"error": "Cover URL not provided"},
        ]


@pytest.mark.e2e
class TestDirectSourceQueryEndpoint:
    """Tests for direct-mode source query search on the shared releases API."""

    def test_direct_source_query_requires_browse_context(self, protected_api_client: APIClient):
        """Source query mode requires a query or browse filters."""
        resp = protected_api_client.get("/api/releases", params={"source": "direct_download"})

        assert resp.status_code == 400
        data = resp.json()
        assert data == {"error": "Parameters 'provider' and 'book_id' are required"}

    def test_direct_source_query_returns_results(self, protected_api_client: APIClient):
        """Direct mode uses /api/releases source query mode."""
        resp = protected_api_client.get(
            "/api/releases",
            params={"source": "direct_download", "query": "Pride Prejudice"},
        )

        if resp.status_code == 200:
            data = resp.json()
            expected_keys = {"releases", "book", "sources_searched", "column_config", "search_info"}
            assert expected_keys <= set(data)
            assert data["sources_searched"] == ["direct_download"]
            assert isinstance(data["releases"], list)
            assert isinstance(data["book"], dict)
            assert isinstance(data["search_info"], dict)
            if "errors" in data:
                assert isinstance(data["errors"], list)
        else:
            assert resp.status_code == 503
            data = resp.json()
            assert "error" in data


@pytest.mark.e2e
class TestSourceRecordEndpoint:
    """Tests for source-native record lookup on the shared source-record API."""

    def test_source_record_invalid_id(self, protected_api_client: APIClient):
        """Unknown source records should return a not-found style response."""
        resp = protected_api_client.get(
            "/api/release-sources/direct_download/records/invalid-id-xyz"
        )

        if resp.status_code == 503:
            pytest.skip("Direct source record lookup unavailable")
        assert resp.status_code == 404
        assert resp.json() == {"error": "Record not found"}
