"""Tests for listing the files inside a .torrent without downloading it."""

from shelfmark.download.clients.torrent_utils import (
    bencode_encode,
    extract_file_list_from_torrent,
)
from shelfmark.download.postprocess.packs import PackFile


def _torrent(info: dict) -> bytes:
    return bencode_encode({b"announce": b"http://t/announce", b"info": info})


def test_multi_file_torrent_lists_release_relative_paths():
    data = _torrent(
        {
            b"name": b"Sun Eater",
            b"piece length": 16384,
            b"pieces": b"x" * 20,
            b"files": [
                {b"length": 10, b"path": [b"Book 1 - Empire of Silence", b"empire.m4b"]},
                {b"length": 20, b"path": [b"Book 2 - Howling Dark", b"howling.m4b"]},
                {b"length": 1, b"path": [b"cover.jpg"]},
            ],
        }
    )
    assert extract_file_list_from_torrent(data) == [
        PackFile("Sun Eater/Book 1 - Empire of Silence/empire.m4b", 10),
        PackFile("Sun Eater/Book 2 - Howling Dark/howling.m4b", 20),
        PackFile("Sun Eater/cover.jpg", 1),
    ]


def test_single_file_torrent_lists_its_one_file():
    data = _torrent(
        {b"name": b"Book.m4b", b"length": 42, b"piece length": 16384, b"pieces": b"x" * 20}
    )
    assert extract_file_list_from_torrent(data) == [PackFile("Book.m4b", 42)]


def test_unparseable_data_returns_none():
    assert extract_file_list_from_torrent(b"not a torrent") is None


class TestProwlarrHandlerListFiles:
    def _handler(self):
        from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler

        return ProwlarrHandler()

    def test_lists_files_from_torrent_url(self):
        from unittest.mock import patch

        from shelfmark.download.clients.torrent_utils import TorrentInfo

        data = _torrent(
            {b"name": b"Book.m4b", b"length": 42, b"piece length": 16384, b"pieces": b"x" * 20}
        )
        release = {
            "protocol": "torrent",
            "downloadUrl": "http://prowlarr/dl.torrent",
            "magnetUrl": "magnet:?xt=urn:btih:abc",
            "infoHash": "abc",
        }
        with (
            patch("shelfmark.release_sources.prowlarr.handler.get_release", return_value=release),
            patch(
                "shelfmark.release_sources.prowlarr.handler.extract_torrent_info",
                return_value=TorrentInfo(info_hash="abc", torrent_data=data, is_magnet=False),
            ) as extract,
        ):
            files = self._handler().list_files({"source_id": "rel-1"})
        assert files == [PackFile("Book.m4b", 42)]
        extract.assert_called_once_with("http://prowlarr/dl.torrent", expected_hash="abc")

    def test_magnet_only_release_cannot_be_listed(self):
        from unittest.mock import patch

        release = {"protocol": "torrent", "magnetUrl": "magnet:?xt=urn:btih:abc"}
        with (
            patch("shelfmark.release_sources.prowlarr.handler.get_release", return_value=release),
            patch("shelfmark.release_sources.prowlarr.handler.extract_torrent_info") as extract,
        ):
            assert self._handler().list_files({"source_id": "rel-1"}) is None
        extract.assert_not_called()

    def test_usenet_release_cannot_be_listed(self):
        from unittest.mock import patch

        release = {"protocol": "usenet", "downloadUrl": "http://prowlarr/dl.nzb"}
        with patch("shelfmark.release_sources.prowlarr.handler.get_release", return_value=release):
            assert self._handler().list_files({"source_id": "rel-1"}) is None

    def test_unknown_release_cannot_be_listed(self):
        from unittest.mock import patch

        with patch("shelfmark.release_sources.prowlarr.handler.get_release", return_value=None):
            assert self._handler().list_files({"source_id": "missing"}) is None
