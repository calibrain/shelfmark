"""Tests for the direct-download Libgen ads.php resolution (AA-md5 -> libgen fallback)."""

from unittest.mock import patch

from shelfmark.release_sources.direct_download import annas_archive
from tests.libgen import sample_html as html


def test_extract_libgen_download_url_sends_same_origin_referer():
    # libgen.li's ads.php returns an empty 200 without a Referer; the resolver must send a
    # same-origin one or it never finds the get.php link and the download silently fails.
    captured = {}

    class FakeResponse:
        status_code = 200
        text = html.ADS_HTML
        url = "https://libgen.li/ads.php?md5=" + html.MD5_A

    def fake_get(link, **kwargs):
        captured["headers"] = kwargs["headers"]
        return FakeResponse()

    with (
        patch.object(annas_archive.requests, "get", side_effect=fake_get),
        patch.object(annas_archive.network, "get_proxies", return_value=None),
        patch.object(annas_archive.network, "get_ssl_verify", return_value=True),
    ):
        url = annas_archive._extract_libgen_download_url(
            f"https://libgen.li/ads.php?md5={html.MD5_A}"
        )

    assert captured["headers"]["Referer"] == "https://libgen.li/"
    assert url == f"https://libgen.li/get.php?md5={html.MD5_A}&key={html.GET_KEY}"
