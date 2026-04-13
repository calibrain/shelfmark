"""
E2E Tests for Prowlarr Integration.

These tests verify the Prowlarr release source and download client flow.
Requires Prowlarr and a download client (qBittorrent, Transmission, etc.) to be configured.

Run with: uv run pytest tests/e2e/test_prowlarr_flow.py -v -m e2e
"""

import pytest

from .conftest import (
    DOWNLOAD_TIMEOUT,
    SUCCESS_DOWNLOAD_STATES,
    APIClient,
    DownloadTracker,
    assert_queued_download_response,
)


def _assert_terminal_download_result(
    result: dict[str, object],
    *,
    source_id: str,
    expected_title: str,
    expected_source: str,
) -> None:
    """Assert that a finished Prowlarr download produced a structured payload."""
    state = result["state"]
    entry = result["data"]
    assert isinstance(entry, dict)
    if state == "error":
        error_message = str(
            entry.get("status_message") or entry.get("last_error_message") or ""
        ).strip()
        normalized_error = error_message.lower()
        if any(
            marker in normalized_error
            for marker in (
                "failed to connect",
                "connection error",
                "connection refused",
                "name or service not known",
                "max retries exceeded",
                "timed out",
            )
        ):
            pytest.skip(f"{expected_source} dependency unavailable: {error_message}")
        pytest.fail(
            f"{expected_source} download failed"
            f"{f': {error_message}' if error_message else f': {entry!r}'}"
        )

    assert state in SUCCESS_DOWNLOAD_STATES, (
        f"{expected_source} ended in unexpected state {state!r}: {entry!r}"
    )
    assert entry.get("id") == source_id
    assert entry.get("title") == expected_title
    assert entry.get("source") == expected_source
    status = entry.get("status")
    assert status is None or status in SUCCESS_DOWNLOAD_STATES | {"queued"}


def _is_duplicate_queue_error(response) -> bool:
    """Whether the API refused to queue a release because it already exists."""
    if response.status_code != 500:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return payload == {"error": "Release is already in the download queue"}


def _require_json_object(
    response, *, context: str, skip_statuses: set[int] | frozenset[int] = frozenset({503})
) -> dict[str, object]:
    if response.status_code in skip_statuses:
        pytest.skip(f"{context} unavailable: {response.status_code}")
    assert response.status_code == 200, f"{context} failed: {response.status_code} {response.text}"
    payload = response.json()
    assert isinstance(payload, dict), f"{context} did not return a JSON object: {payload!r}"
    return payload


def _extract_result_list(payload: object, *, context: str) -> list[dict[str, object]]:
    if isinstance(payload, dict):
        if "books" in payload:
            results = payload["books"]
        elif "releases" in payload:
            results = payload["releases"]
        else:
            results = payload.get("results", payload)
    else:
        results = payload

    if isinstance(results, dict):
        list_values = [value for value in results.values() if isinstance(value, list)]
        if not list_values:
            pytest.fail(f"{context} returned an unexpected result structure: {payload!r}")
        if not any(list_values):
            pytest.skip(f"{context} returned no results")
        results = next(value for value in list_values if value)

    if not isinstance(results, list):
        pytest.fail(f"{context} did not return a result list: {payload!r}")
    if not results:
        pytest.skip(f"{context} returned no results")
    return results


def _is_prowlarr_configured(api_client: APIClient) -> bool:
    """Check if Prowlarr is configured and available."""
    resp = api_client.get("/api/release-sources")
    if resp.status_code == 503:
        pytest.skip("Release sources unavailable")
    assert resp.status_code == 200, f"Release sources failed: {resp.status_code} {resp.text}"

    sources = resp.json()
    if not isinstance(sources, list):
        pytest.fail(f"Release sources returned an unexpected payload: {sources!r}")
    if not all(isinstance(source, dict) for source in sources):
        pytest.fail(f"Release sources returned a malformed payload: {sources!r}")
    return any(source.get("name") == "prowlarr" for source in sources)


def _get_first_provider_name(api_client: APIClient) -> str | None:
    """Get the first available provider name."""
    providers_resp = api_client.get("/api/metadata/providers")
    if providers_resp.status_code == 503:
        pytest.skip("Metadata providers unavailable")
    assert providers_resp.status_code == 200, (
        f"Metadata providers failed: {providers_resp.status_code} {providers_resp.text}"
    )

    providers_data = providers_resp.json()
    if not isinstance(providers_data, (dict, list)):
        pytest.fail(f"Metadata providers returned an unexpected payload: {providers_data!r}")
    providers = (
        providers_data.get("providers", []) if isinstance(providers_data, dict) else providers_data
    )
    for provider in providers:
        if (
            isinstance(provider, dict)
            and provider.get("name")
            and provider.get("enabled") is True
            and provider.get("available") is True
        ):
            return provider["name"]
    return None


@pytest.mark.e2e
class TestProwlarrConfiguration:
    """Tests for Prowlarr configuration."""

    def test_prowlarr_in_release_sources(self, protected_api_client: APIClient):
        """Test that Prowlarr appears in release sources."""
        resp = protected_api_client.get("/api/release-sources")

        assert resp.status_code == 200
        sources = resp.json()
        assert isinstance(sources, list), f"Unexpected release sources payload: {sources!r}"
        assert all(isinstance(source, dict) for source in sources), (
            f"Unexpected release sources payload: {sources!r}"
        )
        source_names = [s.get("name") for s in sources]
        assert "prowlarr" in source_names

    def test_prowlarr_settings_tab_exists(self, protected_api_client: APIClient):
        """Test that Prowlarr settings tab exists."""
        resp = protected_api_client.get("/api/settings")

        if resp.status_code == 403:
            pytest.skip("Settings disabled")

        assert resp.status_code == 200
        data = resp.json()

        # Settings may have nested structure with groups/tabs
        if isinstance(data, dict):
            # Could have groups containing tabs, or be flat
            if "groups" in data:
                # Nested: look in groups for prowlarr tabs
                all_tab_names = []
                for group in data.get("groups", []):
                    if isinstance(group, dict):
                        all_tab_names.extend(
                            tab.get("name") or tab.get("id", "")
                            for tab in group.get("tabs", [])
                            if isinstance(tab, dict)
                        )
                tab_names = all_tab_names
            else:
                tab_names = list(data.keys())
        else:
            tab_names = [t.get("name") or t.get("id") for t in data if isinstance(t, dict)]

        # Prowlarr settings should exist (may be under different name)
        prowlarr_tabs = [n for n in tab_names if n and "prowlarr" in n.lower()]
        # Also check if we can directly access the prowlarr_clients settings
        prowlarr_resp = protected_api_client.get("/api/settings/prowlarr_clients")
        has_prowlarr_settings = prowlarr_resp.status_code == 200

        assert prowlarr_tabs or has_prowlarr_settings, (
            f"No prowlarr settings found. Tab names: {tab_names}"
        )


@pytest.mark.e2e
@pytest.mark.slow
class TestProwlarrSearch:
    """Tests for searching via Prowlarr."""

    def test_prowlarr_search_with_metadata(self, protected_api_client: APIClient):
        """Test searching Prowlarr with metadata from a provider."""
        if not _is_prowlarr_configured(protected_api_client):
            pytest.skip("Prowlarr not configured")

        provider = _get_first_provider_name(protected_api_client)
        if not provider:
            pytest.skip("No metadata providers")

        # Search for a book
        search_resp = protected_api_client.get(
            "/api/metadata/search",
            params={"query": "The Great Gatsby", "provider": provider},
            timeout=30,
        )

        search_data = _require_json_object(search_resp, context="metadata search")
        results = _extract_result_list(search_data, context="metadata search")
        book = results[0]

        book_id = book.get("id") or book.get("provider_id")
        assert book_id, "Metadata search result missing ID"

        # Now search releases specifically from Prowlarr
        releases_resp = protected_api_client.get(
            "/api/releases",
            params={
                "provider": provider,
                "book_id": book_id,
                "source": "prowlarr",
                "title": book.get("title", ""),
                "author": book.get("author", ""),
            },
            timeout=60,
        )

        data = _require_json_object(releases_resp, context="Prowlarr release search")
        assert data.get("sources_searched") == ["prowlarr"]
        releases = data.get("releases")
        assert isinstance(releases, list), f"Unexpected Prowlarr release payload: {data!r}"
        if not releases:
            pytest.skip("No Prowlarr releases found")
        assert "book" in data


@pytest.mark.e2e
class TestProwlarrClientSettings:
    """Tests for Prowlarr download client settings."""

    def test_client_settings_structure(self, protected_api_client: APIClient):
        """Test that client settings have expected structure."""
        resp = protected_api_client.get("/api/settings/prowlarr_clients")

        if resp.status_code == 403:
            pytest.skip("Settings disabled")
        if resp.status_code == 404:
            pytest.skip("Prowlarr clients settings tab not found")

        assert resp.status_code == 200
        data = resp.json()

        # Should have fields for client configuration
        assert isinstance(data, (dict, list))

    def test_can_save_client_settings(self, protected_api_client: APIClient):
        """Test that client settings can be saved."""
        # Get current settings
        get_resp = protected_api_client.get("/api/settings/prowlarr_clients")

        if get_resp.status_code in [403, 404]:
            pytest.skip("Settings not available")

        current = get_resp.json()
        assert isinstance(current, dict), f"Unexpected prowlarr_clients payload: {current!r}"
        fields = current.get("fields")
        assert isinstance(fields, list) and fields, (
            f"Prowlarr client settings payload missing fields: {current!r}"
        )

        values = {}
        for field in fields:
            if not isinstance(field, dict):
                continue
            key = field.get("key") or field.get("name")
            if key:
                values[key] = field.get("value", "")

        assert values, f"No editable values found in prowlarr_clients payload: {current!r}"

        put_resp = protected_api_client.put(
            "/api/settings/prowlarr_clients",
            json=values,
        )
        assert put_resp.status_code in [200, 204]
        if put_resp.status_code == 200:
            payload = put_resp.json()
            assert isinstance(payload, dict)


@pytest.mark.e2e
@pytest.mark.slow
class TestProwlarrDownload:
    """Tests for downloading via Prowlarr."""

    def test_queue_prowlarr_release(
        self, protected_api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test queueing a Prowlarr release for download."""
        if not _is_prowlarr_configured(protected_api_client):
            pytest.skip("Prowlarr not configured")

        provider = _get_first_provider_name(protected_api_client)
        if not provider:
            pytest.skip("No providers")

        # Search metadata
        search_resp = protected_api_client.get(
            "/api/metadata/search",
            params={"query": "Dracula Bram Stoker", "provider": provider},
            timeout=30,
        )

        search_data = _require_json_object(search_resp, context="metadata search")
        results = _extract_result_list(search_data, context="metadata search")
        book = results[0]
        book_id = book.get("id") or book.get("provider_id")
        assert book_id, "Metadata search result missing ID"

        # Search Prowlarr releases
        releases_resp = protected_api_client.get(
            "/api/releases",
            params={
                "provider": provider,
                "book_id": book_id,
                "source": "prowlarr",
                "title": book.get("title", ""),
            },
            timeout=60,
        )

        releases_data = _require_json_object(releases_resp, context="Prowlarr release search")
        releases = releases_data.get("releases")
        assert isinstance(releases, list), f"Unexpected Prowlarr release payload: {releases_data!r}"
        if not releases:
            pytest.skip("No Prowlarr releases found")

        # Get the first release
        release = releases[0]
        source_id = release.get("source_id") or release.get("id")
        download_tracker.track(source_id)

        # Queue it
        queue_resp = protected_api_client.post(
            "/api/releases/download",
            json={
                "source": "prowlarr",
                "source_id": source_id,
                "title": release.get("title", book.get("title", "Test")),
                "format": release.get("format"),
                "size": release.get("size"),
                "extra": release.get("extra", {}),
            },
        )

        if not _is_duplicate_queue_error(queue_resp):
            assert_queued_download_response(queue_resp)

        result = download_tracker.wait_for_status(
            source_id,
            target_states=["complete", "done", "available"],
            timeout=DOWNLOAD_TIMEOUT,
        )
        if result is None:
            status_resp = protected_api_client.get("/api/status")
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                error_info = status_data.get("error", {}).get(source_id)
                if error_info:
                    pytest.fail(f"Prowlarr download failed: {error_info}")
            pytest.fail("Prowlarr download timed out")

        _assert_terminal_download_result(
            result,
            source_id=source_id,
            expected_title=release.get("title", book.get("title", "Test")),
            expected_source="prowlarr",
        )


@pytest.mark.e2e
class TestProwlarrClientConnection:
    """Tests for testing download client connections."""

    def test_connection_test_action(self, protected_api_client: APIClient):
        """Test the connection test action for download clients."""
        # This tests the action button functionality in settings
        resp = protected_api_client.post(
            "/api/settings/prowlarr_clients/action/test_torrent_connection"
        )

        # May succeed, fail, or not exist depending on configuration.
        # A 500 is a real server error and should fail the test.
        assert resp.status_code in [200, 400, 404]

        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, dict)
            assert "success" in data or "message" in data
            if "message" in data:
                assert isinstance(data["message"], str)
