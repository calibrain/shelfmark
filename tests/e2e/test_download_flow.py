"""
E2E Download Flow Tests.

These tests verify the complete download journey from search to file retrieval.
They require external services to be available and may take longer to run.

Run with: uv run pytest tests/e2e/test_download_flow.py -v -m e2e
"""

import time

import pytest

from .conftest import (
    DOWNLOAD_TIMEOUT,
    SUCCESS_DOWNLOAD_STATES,
    APIClient,
    DownloadTracker,
    assert_queue_order_response,
    assert_queued_download_response,
)


def _assert_terminal_download_result(
    result: dict[str, object],
    *,
    source_id: str,
    expected_title: str,
    expected_source: str | None = None,
) -> None:
    """Assert that a finished download produced a structured queue payload."""
    state = result["state"]
    entry = result["data"]
    assert isinstance(entry, dict)
    if state == "error":
        error_message = str(
            entry.get("status_message") or entry.get("last_error_message") or ""
        ).strip()
        pytest.fail(
            f"{expected_source or source_id} download failed"
            f"{f': {error_message}' if error_message else f': {entry!r}'}"
        )

    assert state in SUCCESS_DOWNLOAD_STATES, (
        f"{expected_source or source_id} ended in unexpected state {state!r}: {entry!r}"
    )
    assert entry.get("id") == source_id
    assert entry.get("title") == expected_title
    if expected_source is not None:
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


def _require_queue_entry(
    api_client: APIClient,
    book_id: str,
    *,
    context: str,
    timeout: int = 20,
) -> dict[str, object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        queue_resp = api_client.get("/api/queue/order")
        if queue_resp.status_code == 200:
            queue_order = assert_queue_order_response(queue_resp)
            for entry in queue_order:
                if entry.get("id") == book_id:
                    return entry
        elif queue_resp.status_code == 503:
            pytest.skip(f"{context} queue endpoint unavailable")
        else:
            pytest.fail(
                f"{context} queue lookup failed: {queue_resp.status_code} {queue_resp.text}"
            )

        status_resp = api_client.get("/api/status")
        if status_resp.status_code == 200:
            status_data = status_resp.json()
            if isinstance(status_data, dict):
                for state in ("complete", "done", "available", "error", "cancelled"):
                    state_entries = status_data.get(state)
                    if isinstance(state_entries, dict) and book_id in state_entries:
                        pytest.fail(
                            f"{context} reached terminal state {state} before it was observed in the queue: "
                            f"{state_entries[book_id]!r}"
                        )

        time.sleep(1)

    pytest.fail(f"{context} never appeared in the queue")


def _wait_for_queue_absence(
    api_client: APIClient,
    book_id: str,
    *,
    context: str,
    timeout: int = 20,
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        queue_resp = api_client.get("/api/queue/order")
        if queue_resp.status_code == 200:
            queue_order = assert_queue_order_response(queue_resp)
            if all(entry.get("id") != book_id for entry in queue_order):
                return
        elif queue_resp.status_code == 503:
            pytest.skip(f"{context} queue endpoint unavailable")
        else:
            pytest.fail(
                f"{context} queue lookup failed: {queue_resp.status_code} {queue_resp.text}"
            )

        time.sleep(1)

    pytest.fail(f"{context} still appeared in the queue after cancellation")


def _find_available_provider(api_client: APIClient) -> str | None:
    """Find a working metadata provider."""
    resp = api_client.get("/api/metadata/providers")
    if resp.status_code == 503:
        pytest.skip("Metadata providers unavailable")
    assert resp.status_code == 200, f"Metadata providers failed: {resp.status_code} {resp.text}"

    providers_data = resp.json()
    if not isinstance(providers_data, (dict, list)):
        pytest.fail(f"Metadata providers returned an unexpected payload: {providers_data!r}")

    providers = (
        providers_data.get("providers", []) if isinstance(providers_data, dict) else providers_data
    )
    provider_names = [
        provider.get("name")
        for provider in providers
        if (
            isinstance(provider, dict)
            and provider.get("name")
            and provider.get("enabled") is True
            and provider.get("available") is True
        )
    ]

    for name in provider_names:
        test_resp = api_client.get(
            "/api/metadata/search",
            params={"query": "test", "provider": name},
            timeout=30,
        )
        if test_resp.status_code == 200:
            return name
        if test_resp.status_code != 503:
            pytest.fail(
                f"Metadata provider {name} failed during availability check: "
                f"{test_resp.status_code} {test_resp.text}"
            )

    pytest.skip("No working metadata providers available")


@pytest.mark.e2e
@pytest.mark.slow
class TestMetadataToReleaseFlow:
    """Test the flow from metadata search to release listing."""

    def test_search_to_releases_flow(self, protected_api_client: APIClient):
        """Test searching metadata then finding releases."""
        # Find a working provider
        provider = _find_available_provider(protected_api_client)

        # Search for a public domain book
        search_resp = protected_api_client.get(
            "/api/metadata/search",
            params={"query": "Moby Dick Herman Melville", "provider": provider},
            timeout=30,
        )

        search_data = _require_json_object(search_resp, context="metadata search")
        results = _extract_result_list(search_data, context="metadata search")

        # Get the first result
        first_result = results[0]
        book_id = first_result.get("id") or first_result.get("provider_id")
        assert book_id, "Search result missing ID"

        # Now search for releases
        releases_resp = protected_api_client.get(
            "/api/releases",
            params={
                "provider": provider,
                "book_id": book_id,
                "title": first_result.get("title", ""),
                "author": first_result.get("author", ""),
            },
            timeout=60,
        )

        releases_data = _require_json_object(releases_resp, context="release lookup")
        releases = releases_data.get("releases")
        if not isinstance(releases, list):
            pytest.fail(f"Release lookup returned an invalid releases payload: {releases_data!r}")
        if not releases:
            pytest.skip("No releases available")
        assert "book" in releases_data


@pytest.mark.e2e
@pytest.mark.slow
class TestFullDownloadJourney:
    """
    Test the complete download journey.

    This test:
    1. Searches for a book
    2. Finds releases
    3. Queues a download
    4. Waits for completion
    5. Verifies the file exists
    """

    def test_complete_download_flow(
        self, protected_api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test the complete search -> download -> verify flow."""
        # Find a working provider
        provider = _find_available_provider(protected_api_client)
        if not provider:
            pytest.skip("No metadata providers available")

        # Search for a public domain book
        search_resp = protected_api_client.get(
            "/api/metadata/search",
            params={"query": "Pride and Prejudice Jane Austen", "provider": provider},
            timeout=30,
        )

        search_data = _require_json_object(search_resp, context="metadata search")
        results = _extract_result_list(search_data, context="metadata search")

        first_result = results[0]
        book_id = first_result.get("id") or first_result.get("provider_id")

        # Get releases
        releases_resp = protected_api_client.get(
            "/api/releases",
            params={
                "provider": provider,
                "book_id": book_id,
                "title": first_result.get("title", ""),
            },
            timeout=60,
        )

        releases_data = _require_json_object(releases_resp, context="release lookup")
        releases = releases_data.get("releases", [])
        if not releases:
            pytest.skip("No releases available")

        # Find an epub release (prefer smaller files)
        target_release = None
        for release in releases:
            fmt = release.get("format", "").lower()
            if fmt == "epub":
                target_release = release
                break

        if not target_release:
            # Fall back to first release
            target_release = releases[0]

        # Queue the download
        source_id = target_release.get("source_id") or target_release.get("id")
        download_tracker.track(source_id)

        queue_resp = protected_api_client.post(
            "/api/releases/download",
            json={
                "source": target_release.get("source", "direct_download"),
                "source_id": source_id,
                "title": target_release.get("title", "Test Book"),
                "format": target_release.get("format"),
                "size": target_release.get("size"),
            },
        )

        if not _is_duplicate_queue_error(queue_resp):
            assert_queued_download_response(queue_resp)

        # Wait for download to complete (or error)
        result = download_tracker.wait_for_status(
            source_id,
            target_states=["complete", "done", "available"],
            timeout=DOWNLOAD_TIMEOUT,
        )

        if result is None:
            # Check if it errored
            status_resp = protected_api_client.get("/api/status")
            if status_resp.status_code == 200:
                status_data = status_resp.json()
                error_info = status_data.get("error", {}).get(source_id)
                if error_info:
                    pytest.fail(f"Download failed: {error_info}")
            pytest.fail("Download timed out")

        _assert_terminal_download_result(
            result,
            source_id=source_id,
            expected_title=target_release.get("title", "Test Book"),
            expected_source=target_release.get("source", "direct_download"),
        )


@pytest.mark.e2e
@pytest.mark.slow
class TestDirectSourceReleaseFlow:
    """Test direct-mode search, record lookup, and download via shared release APIs."""

    def test_direct_source_search_and_download(
        self, protected_api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test the shared direct-mode source query -> record -> release download flow."""
        search_resp = protected_api_client.get(
            "/api/releases",
            params={"source": "direct_download", "query": "Frankenstein Mary Shelley"},
            timeout=30,
        )

        if search_resp.status_code == 503:
            pytest.skip("Direct source query unavailable")

        assert search_resp.status_code == 200, (
            f"Direct source query failed: {search_resp.status_code} {search_resp.text}"
        )

        payload = search_resp.json()
        assert isinstance(payload, dict), (
            f"Direct source query returned an unexpected payload: {payload!r}"
        )
        results = payload.get("releases") or []
        if not isinstance(results, list):
            pytest.fail(f"Direct source query returned an unexpected payload: {payload!r}")
        if not results:
            pytest.skip("No direct source query results")

        first_result = results[0]
        source = first_result.get("source")
        source_id = first_result.get("source_id")
        assert source == "direct_download", "Result missing direct source context"
        assert source_id, "Result missing source_id"

        info_resp = protected_api_client.get(f"/api/release-sources/{source}/records/{source_id}")

        if info_resp.status_code != 200:
            pytest.fail(f"Source record endpoint failed: {info_resp.status_code}")

        # Queue download from the shared release payload
        download_tracker.track(source_id)
        download_resp = protected_api_client.post(
            "/api/releases/download",
            json={**first_result, "content_type": "ebook", "search_mode": "direct"},
        )

        if not _is_duplicate_queue_error(download_resp):
            assert_queued_download_response(download_resp)

        result = download_tracker.wait_for_status(
            source_id,
            target_states=["complete", "done", "available"],
            timeout=DOWNLOAD_TIMEOUT,
        )
        assert result is not None, "Direct source download did not reach a terminal state"
        _assert_terminal_download_result(
            result,
            source_id=source_id,
            expected_title=first_result.get("title", "Unknown title"),
            expected_source="direct_download",
        )


@pytest.mark.e2e
class TestDownloadCancellation:
    """Test download cancellation functionality."""

    def test_cancel_queued_download(
        self, protected_api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test cancelling a queued download."""
        # Queue a fake download
        test_id = f"cancel-test-{int(time.time())}"
        download_tracker.track(test_id)

        queue_resp = protected_api_client.post(
            "/api/releases/download",
            json={
                "source": "test_source",
                "source_id": test_id,
                "title": "Cancel Test Book",
            },
        )

        assert_queued_download_response(queue_resp)

        _require_queue_entry(
            protected_api_client,
            test_id,
            context="cancel download precondition",
        )

        # Cancel it
        cancel_resp = protected_api_client.delete(f"/api/download/{test_id}/cancel")

        assert cancel_resp.status_code == 200
        cancel_data = cancel_resp.json()
        assert cancel_data == {"status": "cancelled", "book_id": test_id}

        _wait_for_queue_absence(
            protected_api_client,
            test_id,
            context="cancel download",
        )

    def test_cancel_removes_from_queue(
        self, protected_api_client: APIClient, download_tracker: DownloadTracker
    ):
        """Test that cancellation removes item from queue."""
        test_id = f"cancel-verify-{int(time.time())}"
        download_tracker.track(test_id)

        # Queue it
        protected_api_client.post(
            "/api/releases/download",
            json={
                "source": "test_source",
                "source_id": test_id,
                "title": "Cancel Verify Test",
            },
        )

        _require_queue_entry(
            protected_api_client,
            test_id,
            context="cancel verification precondition",
        )

        # Cancel it
        cancel_resp = protected_api_client.delete(f"/api/download/{test_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json() == {"status": "cancelled", "book_id": test_id}

        # Check it's not in the queue
        _wait_for_queue_absence(
            protected_api_client,
            test_id,
            context="cancel verification",
        )


@pytest.mark.e2e
class TestQueuePriority:
    """Test queue priority functionality."""

    def test_set_priority(self, protected_api_client: APIClient, download_tracker: DownloadTracker):
        """Test setting download priority."""
        test_id = f"priority-test-{int(time.time())}"
        download_tracker.track(test_id)

        # Queue it
        queue_resp = protected_api_client.post(
            "/api/releases/download",
            json={
                "source": "test_source",
                "source_id": test_id,
                "title": "Priority Test",
                "priority": 0,
            },
        )

        assert_queued_download_response(queue_resp)

        _require_queue_entry(
            protected_api_client,
            test_id,
            context="priority update precondition",
        )

        # Update priority
        priority_resp = protected_api_client.put(
            f"/api/queue/{test_id}/priority",
            json={"priority": 10},
        )

        assert priority_resp.status_code == 200
        assert priority_resp.json() == {"status": "updated", "book_id": test_id, "priority": 10}

        queue_resp = protected_api_client.get("/api/queue/order")
        queue_order = assert_queue_order_response(queue_resp)
        matching_entries = [entry for entry in queue_order if entry.get("id") == test_id]
        assert len(matching_entries) == 1
        assert matching_entries[0]["priority"] == 10
