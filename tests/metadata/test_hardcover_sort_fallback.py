import logging
from typing import Any

import pytest

from shelfmark.metadata_providers import MetadataSearchOptions
from shelfmark.metadata_providers.hardcover import HardcoverProvider

# Hardcover answers a rejected search with HTTP 200, no GraphQL errors, and a
# null results body; the reason shows up in the sibling error field. A search
# that genuinely matched nothing still returns a results object with found: 0.
REJECTED = {"search": {"error": "Parameter `sort_by` is malformed.", "results": None}}
REJECTED_SILENTLY = {"search": {"results": None}}
EMPTY = {"search": {"results": {"hits": [], "found": 0}}}
ONE_HIT = {"search": {"results": {"hits": [{"document": {"id": 7, "title": "Dune"}}], "found": 1}}}


@pytest.fixture(autouse=True)
def _reset_sort_fallback(monkeypatch):
    """Keep the process-wide sort fallback from leaking between tests."""
    monkeypatch.setattr("shelfmark.metadata_providers.hardcover._sort_fallback_until", 0.0)


@pytest.fixture
def hardcover_logs():
    """Collect Hardcover log messages.

    The provider's logger is built outside the standard hierarchy, so its
    records never reach the root handler that caplog installs.
    """
    from shelfmark.metadata_providers import hardcover

    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Capture()
    hardcover.logger.addHandler(handler)
    try:
        yield messages
    finally:
        hardcover.logger.removeHandler(handler)


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
        # The retry drops sort entirely -- an empty sort is a value Hardcover can reject too.
        assert [call.get("sort") for call in calls] == ["relevance", None]
        assert "sort" not in calls[1]

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

    def test_keeps_sorting_when_the_sort_was_not_the_culprit(self, monkeypatch):
        """A rejection that survives dropping the sort must not disable sorting globally."""
        provider = HardcoverProvider(api_key="test-token")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            provider, "_execute_query", lambda query, variables: calls.append(variables) or REJECTED
        )

        provider._execute_search_query("query", {"query": "dune", "sort": "rating:desc"})
        provider._execute_search_query("query", {"query": "hyperion", "sort": "rating:desc"})

        assert [call.get("sort") for call in calls] == [
            "rating:desc",
            None,
            "rating:desc",
            None,
        ]

    def test_reports_failure_for_an_unsorted_rejection(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            provider, "_execute_query", lambda query, variables: calls.append(variables) or REJECTED
        )

        result = provider._execute_search_query("query", {"query": "dune", "sort": ""})

        assert result is None
        assert len(calls) == 1

    def test_logs_the_reason_hardcover_gave(self, monkeypatch, hardcover_logs):
        provider = HardcoverProvider(api_key="test-token")
        monkeypatch.setattr(provider, "_execute_query", lambda query, variables: REJECTED)

        provider._execute_search_query("query", {"query": "dune", "sort": ""})

        assert any("Parameter `sort_by` is malformed." in message for message in hardcover_logs)

    def test_falls_back_to_a_placeholder_when_hardcover_says_nothing(
        self, monkeypatch, hardcover_logs
    ):
        provider = HardcoverProvider(api_key="test-token")
        monkeypatch.setattr(provider, "_execute_query", lambda query, variables: REJECTED_SILENTLY)

        provider._execute_search_query("query", {"query": "dune", "sort": ""})

        assert any("no error message" in message for message in hardcover_logs)

    def test_skips_the_doomed_request_on_later_searches(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(provider, "_execute_query", _reject_sorted(calls))

        provider._execute_search_query("query", {"query": "dune", "sort": "relevance"})
        provider._execute_search_query("query", {"query": "hyperion", "sort": "relevance"})

        assert [call.get("sort") for call in calls] == ["relevance", None, None]

    def test_search_returns_results_despite_a_rejected_sort(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(provider, "_execute_query", _reject_sorted(calls))

        result = provider.search_paginated(
            MetadataSearchOptions(query="dune sort fallback", page=1, limit=25)
        )

        assert result.total_found == 1
        assert [book.title for book in result.books] == ["Dune"]
        assert [call.get("sort") for call in calls] == ["_text_match:desc,users_count:desc", None]


class TestSearchPayloadRejection:
    def test_distinguishes_a_null_body_from_an_empty_result_set(self):
        from shelfmark.metadata_providers.hardcover import _search_payload_rejected

        assert _search_payload_rejected(REJECTED) is True
        assert _search_payload_rejected(REJECTED_SILENTLY) is True
        assert _search_payload_rejected(EMPTY) is False
        assert _search_payload_rejected(ONE_HIT) is False
        assert _search_payload_rejected(None) is False
        assert _search_payload_rejected({}) is False
        # Non-search payloads (list lookups, book fetches) must pass through.
        assert _search_payload_rejected({"series": [{"id": 1}]}) is False

    def test_reads_the_error_hardcover_attached(self):
        from shelfmark.metadata_providers.hardcover import _search_rejection_reason

        assert _search_rejection_reason(REJECTED) == "Parameter `sort_by` is malformed."
        assert _search_rejection_reason(REJECTED_SILENTLY) == ""
        assert _search_rejection_reason(EMPTY) == ""
        assert _search_rejection_reason(None) == ""
        assert _search_rejection_reason({"search": {"error": None, "results": None}}) == ""
