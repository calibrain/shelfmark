"""AA download-count lookups must honor the Network proxy settings.

`_fetch_download_count_inline` runs for every search result, on its own session, so a
lookup that skips `get_proxies` sends the whole list straight from the host's IP while
the search page itself goes through the configured proxy.
"""

from unittest.mock import MagicMock

import pytest
import requests

from shelfmark.release_sources.direct_download import annas_archive as aa

_PROXIES = {"http": "http://10.0.80.3:8118", "https": "http://10.0.80.3:8118"}


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    aa._download_count_cache.clear()
    monkeypatch.setattr(aa.network, "get_aa_base_url", lambda: "https://annas-archive.gl")
    yield
    aa._download_count_cache.clear()


def test_download_count_lookup_passes_configured_proxies(monkeypatch):
    asked_for: list[str] = []
    captured: dict[str, object] = {}

    def fake_get_proxies(url=""):
        asked_for.append(url)
        return _PROXIES

    def fake_request(_self, _method, _url, *_args, **kwargs):
        captured.update(kwargs)
        return MagicMock(status_code=200, json=MagicMock(return_value={"downloads_total": 7}))

    monkeypatch.setattr("shelfmark.download.network.get_proxies", fake_get_proxies)
    monkeypatch.setattr(requests.Session, "request", fake_request)

    assert aa._fetch_download_count_inline("aaa") == 7
    assert captured.get("proxies") == _PROXIES
    assert asked_for == ["https://annas-archive.gl/dyn/md5/inline_info/aaa"]
