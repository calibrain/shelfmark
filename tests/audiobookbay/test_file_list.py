"""Tests for reading the torrent file list off an AudiobookBay detail page."""

from shelfmark.download.postprocess.packs import PackFile
from shelfmark.release_sources.audiobookbay import scraper

# Trimmed from a real detail page (2026-08): the file rows sit between the
# "Multifile Torrent" marker and the "Combined File Size" row.
MULTIFILE_DETAIL_HTML = """
<table>
<tr><td>Tracker:</td><td>udp://tracker.torrent.eu.org:451/announce</td></tr>
<tr><td>Creation Date:</td><td>Sun, 29 Mar 2026 21:09:39 +0200</td></tr>
<tr><td colspan='2'>This is a Multifile Torrent</td></tr>
<tr><td colspan='2'>The Expanse 9.0 - Leviathan Falls (2021).m4b 1.05 GBs</td></tr>
<tr><td colspan='2'>The Expanse 0.1 - An Expanse Novella - Drive (2012).txt 340 Bytes</td></tr>
<tr><td colspan='2'>The Expanse 0.2 - An Expanse Novella - The Churn (2014).m4b 125.72 MBs</td></tr>
<tr><td colspan='2'>The Expanse 2.0 - Caliban’s War (2012).m4b 578.97 MBs</td></tr>
<tr><td>Combined File Size:</td><td><span style='color:#00f;'>7.87</span> GBs</td></tr>
<tr><td>Info Hash:</td><td>e4a5538e26987ee58a43aa629ec2c4f2b2d46526</td></tr>
</table>
"""

SINGLE_FILE_DETAIL_HTML = """
<table>
<tr><td>Creation Date:</td><td>Sun, 29 Mar 2026 21:09:39 +0200</td></tr>
<tr><td colspan='2'>Drive.m4b 41.55 MBs</td></tr>
<tr><td>Combined File Size:</td><td><span style='color:#00f;'>41.55</span> MBs</td></tr>
<tr><td>Info Hash:</td><td>e4a5538e26987ee58a43aa629ec2c4f2b2d46526</td></tr>
</table>
"""


def test_extracts_multifile_rows_with_byte_sizes():
    files = scraper.extract_file_list(MULTIFILE_DETAIL_HTML)
    assert files == [
        PackFile("The Expanse 9.0 - Leviathan Falls (2021).m4b", int(1.05 * 1024**3)),
        PackFile("The Expanse 0.1 - An Expanse Novella - Drive (2012).txt", 340),
        PackFile(
            "The Expanse 0.2 - An Expanse Novella - The Churn (2014).m4b", int(125.72 * 1024**2)
        ),
        PackFile("The Expanse 2.0 - Caliban’s War (2012).m4b", int(578.97 * 1024**2)),
    ]


def test_single_file_torrent_lists_the_row_before_combined_size():
    assert scraper.extract_file_list(SINGLE_FILE_DETAIL_HTML) == [
        PackFile("Drive.m4b", int(41.55 * 1024**2))
    ]


def test_page_without_file_table_returns_none():
    assert scraper.extract_file_list("<html><body><p>nothing here</p></body></html>") is None


class TestHandlerListFiles:
    def test_lists_files_from_detail_page(self):
        from unittest.mock import patch

        from shelfmark.release_sources.audiobookbay.handler import AudiobookBayHandler

        with patch(
            "shelfmark.release_sources.audiobookbay.handler.scraper.fetch_detail_html",
            return_value=SINGLE_FILE_DETAIL_HTML,
        ) as fetch:
            files = AudiobookBayHandler().list_files(
                {"source_id": "abc", "download_url": "https://audiobookbay.lu/abss/drive/"}
            )
        assert files == [PackFile("Drive.m4b", int(41.55 * 1024**2))]
        fetch.assert_called_once_with("https://audiobookbay.lu/abss/drive/", "audiobookbay.lu")

    def test_rejects_detail_url_on_other_host(self):
        from unittest.mock import patch

        from shelfmark.release_sources.audiobookbay.handler import AudiobookBayHandler

        with patch(
            "shelfmark.release_sources.audiobookbay.handler.scraper.fetch_detail_html"
        ) as fetch:
            files = AudiobookBayHandler().list_files(
                {"source_id": "abc", "download_url": "https://evil.example/abss/drive/"}
            )
        assert files is None
        fetch.assert_not_called()


def test_extract_magnet_link_and_file_list_share_one_page_fetch():
    from unittest.mock import patch

    page = MULTIFILE_DETAIL_HTML
    with (
        patch(
            "shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page",
            return_value=page,
        ) as get_page,
        patch("shelfmark.release_sources.audiobookbay.scraper._bootstrap_abb_session"),
    ):
        scraper.clear_detail_page_cache()
        url = "https://audiobookbay.lu/abss/expanse/"
        assert scraper.fetch_detail_html(url, "audiobookbay.lu") == page
        magnet = scraper.extract_magnet_link(url, "audiobookbay.lu")
    assert magnet is not None
    assert "e4a5538e26987ee58a43aa629ec2c4f2b2d46526".upper() in magnet
    assert get_page.call_count == 1
