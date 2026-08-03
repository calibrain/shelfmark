"""Baseline HTTP API regression contract for the hermetic E2E stack."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.profiles("baseline")


def test_no_auth_contract(client) -> None:
    """The baseline stack grants unauthenticated API access."""
    response = client.get("/api/auth/check")

    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "auth_required": False,
        "auth_mode": "none",
        "is_admin": True,
        "library_capability": None,
    }


def test_health_contract(client) -> None:
    """The owned stack exposes the health API after the runner's readiness check."""
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_config_and_release_sources_contract(client) -> None:
    """Frontend configuration and release-source metadata have stable shapes."""
    config = client.get("/api/config")
    sources = client.get("/api/release-sources")

    assert config.status_code == 200
    config_data = config.json()
    assert isinstance(config_data["supported_formats"], list)
    assert isinstance(config_data["supported_audiobook_formats"], list)
    assert isinstance(config_data["book_languages"], list)
    assert isinstance(config_data["settings_enabled"], bool)
    assert isinstance(config_data["onboarding_complete"], bool)
    assert isinstance(config_data["default_release_source"], str)

    assert sources.status_code == 200
    source_data = sources.json()
    assert isinstance(source_data, list)
    for source in source_data:
        assert set(source) == {
            "name",
            "display_name",
            "enabled",
            "supported_content_types",
            "browse_results_are_releases",
            "can_be_default",
        }


def test_status_and_queue_validation_contract(client) -> None:
    """Empty queue status and malformed queue requests return documented results."""
    status = client.get("/api/status")
    missing_release_id = client.post("/api/releases/download", json={"source": "test_source"})
    missing_priority = client.put(f"/api/queue/{uuid4()}/priority", json={})

    assert status.status_code == 200
    assert all(isinstance(tasks, dict) for tasks in status.json().values())
    assert missing_release_id.status_code == 400
    assert missing_release_id.json() == {"error": "source_id is required"}
    assert missing_priority.status_code == 400
    assert missing_priority.json() == {"error": "Priority not provided"}


def test_queue_priority_and_cancellation_contract(client) -> None:
    """A queued release can be reprioritized and then cancelled by its identifier."""
    source_id = f"api-contract-{uuid4()}"
    queued = client.post(
        "/api/releases/download",
        json={
            "source": "direct_download",
            "source_id": source_id,
            "title": "API Contract Book",
            "library_book_id": os.environ["E2E_DIRECT_SOURCE_BOOK_ID"],
        },
    )

    assert queued.status_code == 200
    assert queued.json() == {"status": "queued", "priority": 0}

    task_id = client.wait_for_task_id("API Contract Book")
    assert task_id, "queued release did not appear in the download status"

    priority = client.put(f"/api/queue/{task_id}/priority", json={"priority": 3})
    assert priority.status_code == 200
    assert priority.json() == {"status": "updated", "book_id": task_id, "priority": 3}

    cancelled = client.delete(f"/api/download/{task_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json() == {"status": "cancelled", "book_id": task_id}


def test_direct_source_query_contract(client) -> None:
    """The direct source searches the runner's deterministic Anna's Archive mock."""
    response = client.direct_search()

    assert response.status_code == 200
    data = response.json()
    assert data["sources_searched"] == ["direct_download"]
    assert data["releases"]
