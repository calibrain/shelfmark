"""Prowlarr Torznab timeout handling (#1249).

A Torznab search is Prowlarr proxying a live request to the tracker, so for a
Cloudflare-fronted indexer it waits on FlareSolverr. Those searches need their
own budget, and when one runs out the caller has to hear about it instead of
receiving an empty list that reads as "this book has no releases".
"""

import pytest
import requests

import shelfmark.release_sources.prowlarr.api as prowlarr_api
from shelfmark.release_sources.prowlarr.api import (
    DEFAULT_INDEXER_TIMEOUT_SECONDS,
    MAX_INDEXER_TIMEOUT_SECONDS,
    MIN_INDEXER_TIMEOUT_SECONDS,
    ProwlarrClient,
    ProwlarrSearchError,
    resolve_indexer_timeout,
)

_TORZNAB_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel></channel></rss>"""


class _Response:
    def __init__(self, text="", status_code=200, reason="OK"):
        self.text = text
        self.status_code = status_code
        self.reason = reason
        self.ok = status_code < 400

    def raise_for_status(self):
        if not self.ok:
            raise requests.exceptions.HTTPError(f"{self.status_code} {self.reason}", response=self)

    def json(self):
        return {}


@pytest.fixture
def no_config(monkeypatch):
    """Keep the client off any persisted PROWLARR_INDEXER_TIMEOUT."""
    monkeypatch.setattr(prowlarr_api.config, "get", lambda key, default=None: default)


class TestResolveIndexerTimeout:
    def test_defaults_when_unset(self, no_config):
        assert resolve_indexer_timeout() == DEFAULT_INDEXER_TIMEOUT_SECONDS

    def test_reads_config(self, monkeypatch):
        monkeypatch.setattr(
            prowlarr_api.config,
            "get",
            lambda key, default=None: 150 if key == "PROWLARR_INDEXER_TIMEOUT" else default,
        )
        assert resolve_indexer_timeout() == 150

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1, MIN_INDEXER_TIMEOUT_SECONDS),
            (9000, MAX_INDEXER_TIMEOUT_SECONDS),
            ("120", 120),
        ],
    )
    def test_clamps_and_coerces(self, value, expected):
        assert resolve_indexer_timeout(value) == expected

    def test_unparsable_value_falls_back_rather_than_raising(self):
        assert resolve_indexer_timeout("soon") == DEFAULT_INDEXER_TIMEOUT_SECONDS


class TestTorznabSearchFailures:
    def _client(self, monkeypatch, response_or_error):
        client = ProwlarrClient("http://prowlarr:9696", "apikey", indexer_timeout=90)
        captured: dict[str, object] = {}

        def fake_get(**kwargs):
            captured.update(kwargs)
            if isinstance(response_or_error, Exception):
                raise response_or_error
            return response_or_error

        monkeypatch.setattr(client._session, "get", fake_get)
        return client, captured

    def test_read_timeout_raises_instead_of_returning_empty(self, monkeypatch):
        client, _ = self._client(monkeypatch, requests.exceptions.ReadTimeout("read timeout=90"))

        with pytest.raises(ProwlarrSearchError, match="did not respond within 90s"):
            client.torznab_search(indexer_id=1, query="Dune")

    def test_http_error_raises_instead_of_returning_empty(self, monkeypatch):
        """Prowlarr answers 429 once it has disabled an indexer for recent failures."""
        client, _ = self._client(monkeypatch, _Response("", status_code=429, reason="Too Many"))

        with pytest.raises(ProwlarrSearchError, match="indexer 1 search failed"):
            client.torznab_search(indexer_id=1, query="Dune")

    def test_an_indexer_with_nothing_still_returns_empty(self, monkeypatch):
        client, _ = self._client(monkeypatch, _Response(_TORZNAB_EMPTY))

        assert client.torznab_search(indexer_id=1, query="Dune") == []

    def test_search_uses_the_indexer_timeout_with_a_short_connect_timeout(self, monkeypatch):
        client, captured = self._client(monkeypatch, _Response(_TORZNAB_EMPTY))
        client.torznab_search(indexer_id=1, query="Dune")

        connect_timeout, read_timeout = captured["timeout"]
        assert read_timeout == 90
        assert connect_timeout < read_timeout

    def test_json_endpoints_keep_the_short_timeout(self, monkeypatch, no_config):
        client = ProwlarrClient("http://prowlarr:9696", "apikey")
        captured: dict[str, object] = {}

        def fake_request(**kwargs):
            captured.update(kwargs)
            return _Response("{}")

        monkeypatch.setattr(client._session, "request", fake_request)
        client.test_connection()

        assert captured["timeout"] == client.timeout
        assert client.timeout < client.indexer_timeout
