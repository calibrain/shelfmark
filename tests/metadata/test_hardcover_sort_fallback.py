from typing import Any

import pytest

from shelfmark.metadata_providers import MetadataSearchOptions
from shelfmark.metadata_providers.hardcover import HardcoverProvider

# Hardcover answers a rejected search with HTTP 200, no GraphQL errors, and a
# null results body. A search that genuinely matched nothing still returns a
# results object with found: 0.
REJECTED = {"search": {"results": None}}
EMPTY = {"search": {"results": {"hits": [], "found": 0}}}
ONE_HIT = {"search": {"results": {"hits": [{"document": {"id": 7, "title": "Dune"}}], "found": 1}}}


@pytest.fixture(autouse=True)
def _reset_sort_fallback(monkeypatch):
    """Keep the process-wide sort fallback from leaking between tests."""
    monkeypatch.setattr("shelfmark.metadata_providers.hardcover._sort_fallback_until", 0.0)


def _reject_sorted(calls: list[dict[str, Any]], *, success=ONE_HIT):
    """Build an _execute_query stand-in that rejects any request carrying a sort."""

    def fake_execute(query: str, variables):
        calls.append(dict(variables))
        return REJECTED if variables.get("sort") else success

    return fake_execute


class TestHardcoverSortFallback:
    def test_retries_without_sort_when_hardcover_rejects_the_sort(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(provider, "_execute_query", _reject_sorted(calls))

        result = provider._execute_search_query("query", {"query": "dune", "sort": "relevance"})

        assert result == ONE_HIT
        assert [call["sort"] for call in calls] == ["relevance", ""]

    def test_treats_an_empty_result_set_as_success(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            provider, "_execute_query", lambda query, variables: calls.append(variables) or EMPTY
        )

        result = provider._execute_search_query("query", {"query": "dune", "sort": "rating:desc"})

        assert result == EMPTY
        assert len(calls) == 1

    def test_reports_failure_when_the_retry_is_also_rejected(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            provider, "_execute_query", lambda query, variables: calls.append(variables) or REJECTED
        )

        result = provider._execute_search_query("query", {"query": "dune", "sort": "rating:desc"})

        assert result is None
        assert len(calls) == 2

    def test_reports_failure_for_an_unsorted_rejection(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            provider, "_execute_query", lambda query, variables: calls.append(variables) or REJECTED
        )

        result = provider._execute_search_query("query", {"query": "dune", "sort": ""})

        assert result is None
        assert len(calls) == 1

    def test_skips_the_doomed_request_on_later_searches(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(provider, "_execute_query", _reject_sorted(calls))

        provider._execute_search_query("query", {"query": "dune", "sort": "relevance"})
        provider._execute_search_query("query", {"query": "hyperion", "sort": "relevance"})

        assert [call["sort"] for call in calls] == ["relevance", "", ""]

    def test_search_returns_results_despite_a_rejected_sort(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(provider, "_execute_query", _reject_sorted(calls))

        result = provider.search_paginated(
            MetadataSearchOptions(query="dune sort fallback", page=1, limit=25)
        )

        assert result.total_found == 1
        assert [book.title for book in result.books] == ["Dune"]
        assert [call["sort"] for call in calls] == ["_text_match:desc,users_count:desc", ""]


class TestSearchPayloadRejection:
    def test_distinguishes_a_null_body_from_an_empty_result_set(self):
        from shelfmark.metadata_providers.hardcover import _search_payload_rejected

        assert _search_payload_rejected(REJECTED) is True
        assert _search_payload_rejected(EMPTY) is False
        assert _search_payload_rejected(ONE_HIT) is False
        assert _search_payload_rejected(None) is False
        assert _search_payload_rejected({}) is False
        # Non-search payloads (list lookups, book fetches) must pass through.
        assert _search_payload_rejected({"series": [{"id": 1}]}) is False
