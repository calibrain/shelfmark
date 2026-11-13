"""Open Library requests must honor Network HTTP proxy settings (#1524)."""

import shelfmark.metadata_providers.openlibrary as openlibrary_module
from shelfmark.metadata_providers import MetadataSearchOptions, SearchType
from shelfmark.metadata_providers.openlibrary import OpenLibraryProvider


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {"docs": []}
        self.status_code = status_code
        self.ok = status_code < 400

    def raise_for_status(self):
        if not self.ok:
            raise openlibrary_module.requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return self._payload


def test_openlibrary_search_passes_configured_http_proxy(monkeypatch, sent_requests):
    """Universal Open Library search must send traffic through get_proxies()."""
    expected = {
        "http": "http://10.0.80.3:8118",
        "https": "http://10.0.80.3:8118",
    }
    monkeypatch.setattr(
        "shelfmark.download.network.get_proxies",
        lambda url: expected if "openlibrary.org" in url else {},
    )
    monkeypatch.setattr("shelfmark.download.network.get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(openlibrary_module._rate_limiter, "wait_if_needed", lambda: None)

    from shelfmark.core.config import config as app_config

    monkeypatch.setattr(
        app_config,
        "get",
        lambda key, default=None: False if key == "METADATA_CACHE_ENABLED" else default,
    )

    provider = OpenLibraryProvider()
    sent_requests.respond = _FakeResponse({"docs": []})

    provider.search(MetadataSearchOptions(query="Mistborn", search_type=SearchType.GENERAL))

    assert sent_requests.calls
    assert all(call["proxies"] == expected for call in sent_requests.calls)


def test_openlibrary_connection_test_passes_configured_http_proxy(monkeypatch, sent_requests):
    """Settings connection test for Open Library must also use the proxy."""
    expected = {
        "http": "http://proxy.local:8118",
        "https": "http://proxy.local:8118",
    }
    monkeypatch.setattr("shelfmark.download.network.get_proxies", lambda _url: expected)
    monkeypatch.setattr("shelfmark.download.network.get_ssl_verify", lambda _url: True)

    sent_requests.respond = _FakeResponse({"docs": [{"title": "ok"}]})

    result = openlibrary_module._test_openlibrary_connection()

    assert result["success"] is True
    assert sent_requests.last()["proxies"] == expected
