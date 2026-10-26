"""The retry URL comes from server data, never from the queue request."""

import pytest

from shelfmark.download import orchestrator


@pytest.mark.parametrize("source", ["newznab", "jackett"])
def test_cache_miss_drops_client_supplied_download_url(source):
    fields = orchestrator._build_retry_resolution_fields(
        {
            "source": source,
            "source_id": "not-in-the-cache",
            "download_url": "http://169.254.169.254/latest/meta-data/",
            "protocol": "torrent",
            "title": "Some Book",
        }
    )

    assert fields["retry_download_url"] is None
    assert fields["retry_download_protocol"] is None


@pytest.mark.parametrize("source", ["newznab", "jackett"])
def test_missing_source_id_drops_client_supplied_download_url(source):
    fields = orchestrator._build_retry_resolution_fields(
        {"source": source, "download_url": "http://10.0.0.1/x", "protocol": "usenet"}
    )

    assert fields["retry_download_url"] is None
