"""Behaviour of the `/api/metadata/...` routes that no other test pins down."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.metadata_providers import BookMetadata


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


class _Provider:
    name = "stub"
    display_name = "Stub"
    search_fields: list = []
    account_search_fields: frozenset = frozenset()

    def __init__(self) -> None:
        self.available = True
        self.book: BookMetadata | None = BookMetadata(
            provider="stub", provider_id="7", title="Dune", authors=["Frank Herbert"]
        )
        self.raise_on_targets: Exception | None = None

    def is_available(self) -> bool:
        return self.available

    def get_book(self, book_id: str) -> BookMetadata | None:
        return self.book

    def get_book_targets(self, book_id: str) -> list:
        if self.raise_on_targets:
            raise self.raise_on_targets
        return [{"value": "list:1"}]

    def get_book_targets_batch(self, ids: list) -> dict:
        return {i: [] for i in ids}

    def set_book_target_state(self, book_id: str, target: str, *, selected: bool) -> dict:
        return {"changed": False, "deselected_target": "list:2"}


@pytest.fixture
def stub() -> _Provider:
    return _Provider()


@pytest.fixture
def client(main_module, stub):
    registered = {"stub": True}
    with (
        patch("shelfmark.metadata_providers.get_provider", return_value=stub),
        patch(
            "shelfmark.metadata_providers.is_provider_registered",
            side_effect=lambda name: registered.get(name, False),
        ),
        patch("shelfmark.metadata_providers.get_provider_kwargs", return_value={}),
        patch.object(main_module, "get_auth_mode", return_value="none"),
    ):
        yield main_module.app.test_client()


class TestAuth:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/metadata/providers"),
            ("get", "/api/metadata/config"),
            ("get", "/api/metadata/search?query=x"),
            ("get", "/api/metadata/field-options?field=a"),
            ("get", "/api/metadata/book/stub/7"),
            ("get", "/api/metadata/book/stub/7/targets"),
            ("post", "/api/metadata/book/stub/targets/batch"),
            ("put", "/api/metadata/book/stub/7/targets"),
        ],
    )
    def test_every_route_needs_a_session(self, main_module, method, path) -> None:
        client = main_module.app.test_client()
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = getattr(client, method)(path)

        assert resp.status_code == 401


class TestProviders:
    def test_lists_providers_with_availability_and_configured_names(self, client, stub) -> None:
        stub.available = False
        info = {"name": "stub", "display_name": "Stub", "requires_auth": True}
        with (
            patch("shelfmark.metadata_providers.list_providers", return_value=[info]),
            patch(
                "shelfmark.metadata_providers.get_configured_provider_name",
                side_effect=lambda content_type, **_: {"ebook": "stub", "audiobook": ""}.get(
                    content_type, ""
                ),
            ),
        ):
            resp = client.get("/api/metadata/providers")

        assert resp.status_code == 200
        assert resp.json == {
            "providers": [
                {
                    "name": "stub",
                    "display_name": "Stub",
                    "requires_auth": True,
                    "enabled": False,
                    "available": False,
                }
            ],
            "configured_provider": "stub",
            "configured_provider_audiobook": None,
            "configured_provider_combined": None,
        }

    def test_a_provider_that_fails_its_check_is_listed_unavailable(self, client) -> None:
        info = {"name": "stub", "display_name": "Stub", "requires_auth": False}
        with (
            patch("shelfmark.metadata_providers.list_providers", return_value=[info]),
            patch("shelfmark.metadata_providers.get_provider", side_effect=RuntimeError("boom")),
            patch("shelfmark.metadata_providers.get_configured_provider_name", return_value=""),
        ):
            resp = client.get("/api/metadata/providers")

        assert resp.status_code == 200
        assert resp.json["providers"][0]["available"] is False


class TestConfig:
    def test_no_provider_configured_returns_the_empty_shape(self, client) -> None:
        with patch("shelfmark.metadata_providers.get_configured_provider_name", return_value=""):
            resp = client.get("/api/metadata/config")

        assert resp.status_code == 200
        assert resp.json["provider"] is None
        assert resp.json["search_fields"] == []
        assert resp.json["default_sort"] == "relevance"

    def test_unknown_provider_is_a_400(self, client) -> None:
        resp = client.get("/api/metadata/config?provider=nope")

        assert resp.status_code == 400
        assert resp.json == {"error": "Unknown metadata provider: nope"}

    def test_known_provider_returns_its_search_config(self, client) -> None:
        with (
            patch("shelfmark.metadata_providers.get_provider_search_fields", return_value=[1]),
            patch("shelfmark.metadata_providers.get_provider_capabilities", return_value=["c"]),
            patch("shelfmark.metadata_providers.get_provider_sort_options", return_value=[2]),
            patch("shelfmark.metadata_providers.get_provider_default_sort", return_value="newest"),
        ):
            resp = client.get("/api/metadata/config?provider=stub")

        assert resp.json == {
            "provider": "stub",
            "display_name": "Stub",
            "enabled": False,
            "available": True,
            "search_fields": [1],
            "capabilities": ["c"],
            "sort_options": [2],
            "default_sort": "newest",
        }


class TestSearchErrors:
    def test_unknown_provider_is_a_400(self, client) -> None:
        resp = client.get("/api/metadata/search?provider=nope&query=x")

        assert resp.status_code == 400
        assert resp.json["error"] == "Unknown metadata provider: nope"

    def test_disabled_provider_is_a_503(self, client) -> None:
        with patch("shelfmark.metadata_providers.is_provider_enabled", return_value=False):
            resp = client.get("/api/metadata/search?provider=stub&query=x")

        assert resp.status_code == 503
        assert resp.json["error"] == "Metadata provider 'stub' is not enabled"

    def test_no_configured_provider_is_a_503(self, client) -> None:
        with patch("shelfmark.metadata_providers.get_configured_provider", return_value=None):
            resp = client.get("/api/metadata/search?query=x")

        assert resp.status_code == 503
        assert resp.json["error"] == "No metadata provider configured"

    def test_unavailable_provider_is_a_503(self, client, stub) -> None:
        stub.available = False
        with patch("shelfmark.metadata_providers.is_provider_enabled", return_value=True):
            resp = client.get("/api/metadata/search?provider=stub&query=x")

        assert resp.status_code == 503
        assert resp.json["error"] == "Metadata provider 'stub' is not available"

    def test_no_query_and_no_fields_is_a_400(self, client) -> None:
        with patch("shelfmark.metadata_providers.is_provider_enabled", return_value=True):
            resp = client.get("/api/metadata/search?provider=stub")

        assert resp.status_code == 400
        assert resp.json == {"error": "Either 'query' or search field values are required"}


class TestFieldOptions:
    def test_no_field_gives_no_options(self, client) -> None:
        assert client.get("/api/metadata/field-options").json == {"options": []}

    def test_unknown_provider_gives_no_options(self, client) -> None:
        resp = client.get("/api/metadata/field-options?provider=nope&field=a")

        assert resp.json == {"options": []}

    def test_unavailable_provider_gives_no_options(self, client, stub) -> None:
        stub.available = False

        assert client.get("/api/metadata/field-options?provider=stub&field=a").json == {
            "options": []
        }


class TestBook:
    def test_returns_the_book(self, client) -> None:
        resp = client.get("/api/metadata/book/stub/7")

        assert resp.status_code == 200
        assert resp.json["title"] == "Dune"
        assert resp.json["provider_id"] == "7"

    def test_missing_book_is_a_404(self, client, stub) -> None:
        stub.book = None

        resp = client.get("/api/metadata/book/stub/7")

        assert resp.status_code == 404
        assert resp.json == {"error": "Book not found"}

    def test_unknown_provider_is_a_400(self, client) -> None:
        resp = client.get("/api/metadata/book/nope/7")

        assert resp.status_code == 400
        assert resp.json == {"error": "Unknown metadata provider: nope"}

    def test_unavailable_provider_is_a_503(self, client, stub) -> None:
        stub.available = False

        resp = client.get("/api/metadata/book/stub/7")

        assert resp.status_code == 503
        assert resp.json == {"error": "Provider 'stub' is not available"}

    def test_a_library_ownership_check_adds_the_library_key(self, client) -> None:
        with patch("shelfmark.core.library_index.ownership", return_value={"epub": True}):
            resp = client.get("/api/metadata/book/stub/7")

        assert resp.json["library"] == {"epub": True}


class TestTargetErrors:
    def test_unknown_provider_is_a_400(self, client) -> None:
        resp = client.get("/api/metadata/book/nope/7/targets")

        assert resp.status_code == 400

    def test_unavailable_provider_is_a_502(self, client, stub) -> None:
        stub.available = False

        resp = client.get("/api/metadata/book/stub/7/targets")

        assert resp.status_code == 502
        assert resp.json == {"error": "Provider 'stub' is not available"}

    def test_not_implemented_is_a_400(self, client, stub) -> None:
        stub.raise_on_targets = NotImplementedError("no targets here")

        resp = client.get("/api/metadata/book/stub/7/targets")

        assert resp.status_code == 400
        assert resp.json == {"error": "no targets here"}

    def test_an_operational_error_gets_the_fallback_message(self, client, stub) -> None:
        stub.raise_on_targets = OSError("secret detail")

        resp = client.get("/api/metadata/book/stub/7/targets")

        assert resp.status_code == 500
        assert resp.json == {"error": "Failed to load book targets"}

    def test_the_update_route_uses_its_own_fallback_message(self, client, stub) -> None:
        with patch.object(stub, "set_book_target_state", side_effect=TypeError("x")):
            resp = client.put(
                "/api/metadata/book/stub/7/targets", json={"target": "list:1", "selected": True}
            )

        assert resp.status_code == 500
        assert resp.json == {"error": "Failed to update book targets"}


class TestTargetPayloads:
    @pytest.mark.parametrize("body", [{}, {"book_ids": []}, {"book_ids": "x"}, []])
    def test_batch_needs_a_non_empty_list(self, client, body) -> None:
        resp = client.post("/api/metadata/book/stub/targets/batch", json=body)

        assert resp.status_code == 400
        assert resp.json == {"error": "book_ids must be a non-empty array"}

    def test_batch_caps_the_ids_at_fifty_and_stringifies_them(self, client) -> None:
        resp = client.post(
            "/api/metadata/book/stub/targets/batch", json={"book_ids": list(range(60))}
        )

        assert resp.status_code == 200
        assert set(resp.json["results"]) == {str(i) for i in range(50)}

    def test_update_needs_a_target(self, client) -> None:
        resp = client.put("/api/metadata/book/stub/7/targets", json={"selected": True})

        assert resp.status_code == 400
        assert resp.json == {"error": "target is required"}

    def test_update_needs_a_boolean_selected(self, client) -> None:
        resp = client.put(
            "/api/metadata/book/stub/7/targets", json={"target": "list:1", "selected": "yes"}
        )

        assert resp.status_code == 400
        assert resp.json == {"error": "selected must be a boolean"}

    def test_update_reports_the_change_and_the_deselected_target(self, client) -> None:
        resp = client.put(
            "/api/metadata/book/stub/7/targets", json={"target": "list:1", "selected": True}
        )

        assert resp.json == {
            "success": True,
            "changed": False,
            "selected": True,
            "deselected_target": "list:2",
        }
