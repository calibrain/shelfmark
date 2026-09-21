"""Debrid-Link seedbox client tests.

Two things set this client apart from the other debrid services and both are
covered here: every v2 endpoint wraps its payload in a ``{success, value}``
envelope that can report failure over an HTTP 200, and a completed torrent
already carries a direct ``downloadUrl`` per file, so there is no unrestrict
step to mock.
"""

import hashlib
from unittest.mock import MagicMock

import pytest

from shelfmark.download.clients import DownloadState
from shelfmark.download.clients.debridlink import (
    DebridLinkClient,
    _as_float,
    _DownloadState,
)
from shelfmark.download.clients.torrent_utils import bencode_encode

_PROWLARR_PROXY_URL = "https://prowlarr.example/api/v1/indexer/1/download?apikey=k&link=abc"
_MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Dune"


def _valid_torrent() -> tuple[bytes, str]:
    info_dict = {
        b"name": b"book.epub",
        b"length": 100,
        b"piece length": 16384,
        b"pieces": b"\x00" * 20,
    }
    return (
        bencode_encode({b"info": info_dict}),
        hashlib.sha1(bencode_encode(info_dict)).hexdigest().lower(),
    )


def _mock_fetch(monkeypatch, *, content=b"", status_code=200, error=None):
    """Stand in for the .torrent prefetch inside extract_torrent_info."""
    if error is not None:
        mock_get = MagicMock(side_effect=error)
    else:
        response = MagicMock(status_code=status_code, content=content)
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
    monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)
    return mock_get


def _mock_request(monkeypatch, payload, *, status_code=200):
    """Patch requests.request with a single canned Debrid-Link envelope."""
    response = MagicMock(status_code=status_code)
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    request = MagicMock(return_value=response)
    monkeypatch.setattr("shelfmark.download.clients.debridlink.requests.request", request)
    return request


def _client(monkeypatch, api_key="dl-key"):
    monkeypatch.setattr(
        "shelfmark.download.clients.debridlink.config.get",
        lambda key, default="": {"DEBRIDLINK_API_KEY": api_key}.get(key, default),
    )
    return DebridLinkClient()


class TestEnvelope:
    """A failed call can still be an HTTP 200, so the body decides."""

    def test_success_false_raises_even_on_http_200(self, monkeypatch):
        _mock_request(
            monkeypatch,
            {"success": False, "error": "badToken", "error_description": "Invalid token"},
        )
        client = _client(monkeypatch)

        with pytest.raises(RuntimeError, match="Invalid token"):
            client._request_value("GET", "/account/infos")

    def test_error_code_is_used_when_no_description_is_given(self, monkeypatch):
        _mock_request(monkeypatch, {"success": False, "error": "floodDetected"})
        client = _client(monkeypatch)

        with pytest.raises(RuntimeError, match="floodDetected"):
            client._request_value("GET", "/account/infos")

    def test_success_unwraps_the_value(self, monkeypatch):
        _mock_request(monkeypatch, {"success": True, "value": {"id": "DL1"}})
        client = _client(monkeypatch)

        assert client._request_value("GET", "/seedbox/list") == {"id": "DL1"}

    def test_a_non_object_body_is_rejected(self, monkeypatch):
        _mock_request(monkeypatch, ["unexpected"])
        client = _client(monkeypatch)

        with pytest.raises(RuntimeError, match="Unexpected Debrid-Link response"):
            client._request_value("GET", "/account/infos")


class TestAdd:
    def test_magnet_is_sent_as_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        request = _mock_request(monkeypatch, {"success": True, "value": {"id": "DL1"}})
        client = _client(monkeypatch)

        assert client.add_download(_MAGNET, "Dune") == "DL1"

        request.assert_called_once()
        assert request.call_args.args[0] == "POST"
        assert request.call_args.args[1].endswith("/seedbox/add")
        assert request.call_args.kwargs["json"] == {"url": _MAGNET}
        assert "files" not in request.call_args.kwargs

    def test_proxy_url_is_uploaded_as_a_torrent_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        torrent_data, _ = _valid_torrent()
        _mock_fetch(monkeypatch, content=torrent_data)
        request = _mock_request(monkeypatch, {"success": True, "value": {"id": "DL2"}})
        client = _client(monkeypatch)

        assert client.add_download(_PROWLARR_PROXY_URL, "Dune") == "DL2"

        # A .torrent only goes up as multipart; the JSON body takes a magnet.
        assert "json" not in request.call_args.kwargs
        assert request.call_args.kwargs["files"]["file"][1] == torrent_data

    def test_missing_id_is_an_error_not_a_silent_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_request(monkeypatch, {"success": True, "value": {}})
        client = _client(monkeypatch)

        with pytest.raises(RuntimeError, match="No torrent ID returned"):
            client.add_download(_MAGNET, "Dune")

    def test_unresolvable_url_reports_the_reason(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_fetch(monkeypatch, error=OSError("tracker unreachable"))
        request = _mock_request(monkeypatch, {"success": True, "value": {"id": "DL3"}})
        client = _client(monkeypatch)

        with pytest.raises(ValueError, match="Could not resolve a torrent to send"):
            client.add_download(_PROWLARR_PROXY_URL, "Dune")

        request.assert_not_called()

    def test_without_an_api_key_it_refuses_before_any_call(self, monkeypatch):
        request = _mock_request(monkeypatch, {"success": True, "value": {"id": "DL4"}})
        client = _client(monkeypatch, api_key="")

        with pytest.raises(RuntimeError, match="API key is not configured"):
            client.add_download(_MAGNET, "Dune")

        request.assert_not_called()


class TestStatus:
    @staticmethod
    def _bare_client():
        return DebridLinkClient.__new__(DebridLinkClient)

    @staticmethod
    def _state(tmp_path):
        return _DownloadState(
            torrent_id="DL1",
            name="Dune",
            target_dir=tmp_path,
            phase="waiting_dl",
        )

    def test_partial_progress_is_halved_and_stays_downloading(self, tmp_path):
        client = self._bare_client()
        state = self._state(tmp_path)

        status = client._handle_torrent_info(
            {"downloadPercent": 40, "name": "Dune.epub", "downloadSpeed": 1234},
            state,
        )

        # The seedbox fetch is the first half of the bar; HTTP is the second.
        assert status.progress == 20.0
        assert status.state == DownloadState.DOWNLOADING
        assert status.complete is False
        assert status.download_speed == 1234
        assert state.phase == "waiting_dl"
        assert state.error_message is None

    def test_torrent_error_is_terminal_and_cached(self, tmp_path):
        client = self._bare_client()
        state = self._state(tmp_path)

        status = client._handle_torrent_info(
            {"error": 9, "errorString": "tracker rejected"},
            state,
        )

        assert status.state == DownloadState.ERROR
        assert state.phase == "error"
        assert state.error_message == "Debrid-Link torrent error: tracker rejected"

    def test_completion_hands_the_files_off_for_download(self, tmp_path, monkeypatch):
        client = self._bare_client()
        state = self._state(tmp_path)
        started = MagicMock()
        monkeypatch.setattr(client, "_maybe_start_download_thread", started)

        files = [{"name": "Dune.epub", "downloadUrl": "https://dl.example/Dune.epub"}]
        status = client._handle_torrent_info(
            {"downloadPercent": 100, "downloaded": True, "files": files},
            state,
        )

        assert status.progress == 50.0
        assert status.state == DownloadState.DOWNLOADING
        assert status.complete is False
        started.assert_called_once_with(state, files)

    def test_downloaded_flag_alone_is_enough(self, tmp_path, monkeypatch):
        client = self._bare_client()
        state = self._state(tmp_path)
        started = MagicMock()
        monkeypatch.setattr(client, "_maybe_start_download_thread", started)

        client._handle_torrent_info({"downloaded": True, "files": []}, state)

        started.assert_called_once()

    def test_a_vanished_torrent_becomes_an_error_rather_than_a_stall(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shelfmark.download.clients.debridlink.TMP_DIR", tmp_path)
        _mock_request(monkeypatch, {"success": True, "value": []})
        client = _client(monkeypatch)

        status = client.get_status("GONE")

        assert status.state == DownloadState.ERROR
        assert "no longer on Debrid-Link" in (status.message or "")


class TestFetchTorrent:
    def test_the_matching_id_is_picked_out_of_the_page(self, monkeypatch):
        _mock_request(
            monkeypatch,
            {
                "success": True,
                "value": [{"id": "OTHER"}, {"id": "DL1", "downloadPercent": 12}],
            },
        )
        client = _client(monkeypatch)

        assert client._fetch_torrent("DL1") == {"id": "DL1", "downloadPercent": 12}

    def test_an_absent_id_returns_none(self, monkeypatch):
        _mock_request(monkeypatch, {"success": True, "value": [{"id": "OTHER"}]})
        client = _client(monkeypatch)

        assert client._fetch_torrent("DL1") is None

    def test_the_request_asks_for_just_that_torrent(self, monkeypatch):
        request = _mock_request(monkeypatch, {"success": True, "value": []})
        client = _client(monkeypatch)

        client._fetch_torrent("DL1")

        assert request.call_args.kwargs["params"] == {"ids": "DL1"}


class TestConnection:
    def test_missing_key_is_reported_without_a_call(self, monkeypatch):
        request = _mock_request(monkeypatch, {"success": True, "value": {}})
        client = _client(monkeypatch, api_key="")

        assert client.test_connection() == (False, "Debrid-Link API Key is required")
        request.assert_not_called()

    def test_an_expired_account_is_rejected(self, monkeypatch):
        _mock_request(
            monkeypatch,
            {"success": True, "value": {"username": "rob", "premiumLeft": 0}},
        )
        client = _client(monkeypatch)

        ok, message = client.test_connection()

        assert ok is False
        assert "does not have an active" in message

    def test_a_premium_account_is_accepted(self, monkeypatch):
        _mock_request(
            monkeypatch,
            {"success": True, "value": {"username": "rob", "premiumLeft": 86400}},
        )
        client = _client(monkeypatch)

        ok, message = client.test_connection()

        assert ok is True
        assert "rob" in message

    def test_an_api_failure_is_surfaced_not_raised(self, monkeypatch):
        _mock_request(monkeypatch, {"success": False, "error": "badToken"})
        client = _client(monkeypatch)

        ok, message = client.test_connection()

        assert ok is False
        assert "badToken" in message


class TestIsConfigured:
    @staticmethod
    def _config(monkeypatch, values):
        monkeypatch.setattr(
            "shelfmark.download.clients.debridlink.config.get",
            lambda key, default="": values.get(key, default),
        )

    def test_requires_both_the_selection_and_the_key(self, monkeypatch):
        self._config(
            monkeypatch,
            {"PROWLARR_TORRENT_CLIENT": "debridlink", "DEBRIDLINK_API_KEY": "dl-key"},
        )
        assert DebridLinkClient.is_configured() is True

    def test_another_client_selected_means_not_configured(self, monkeypatch):
        self._config(
            monkeypatch,
            {"PROWLARR_TORRENT_CLIENT": "torbox", "DEBRIDLINK_API_KEY": "dl-key"},
        )
        assert DebridLinkClient.is_configured() is False

    def test_no_key_means_not_configured(self, monkeypatch):
        self._config(
            monkeypatch,
            {"PROWLARR_TORRENT_CLIENT": "debridlink", "DEBRIDLINK_API_KEY": ""},
        )
        assert DebridLinkClient.is_configured() is False


class TestDownloadPipeline:
    def test_book_files_are_preferred_over_the_rest(self, tmp_path, monkeypatch):
        client = DebridLinkClient.__new__(DebridLinkClient)
        state = _DownloadState(
            torrent_id="DL1",
            name="Dune",
            target_dir=tmp_path,
            phase="downloading_http",
        )
        fetched: list[str] = []

        def fake_download(url, referer=None):
            fetched.append(url)
            buf = MagicMock()
            buf.getvalue = MagicMock(return_value=b"data")
            return buf

        monkeypatch.setattr("shelfmark.download.clients.debridlink.download_url", fake_download)

        client._process_and_download(
            state,
            [
                {"name": "cover.jpg", "downloadUrl": "https://dl.example/cover.jpg"},
                {"name": "Dune.epub", "downloadUrl": "https://dl.example/Dune.epub"},
            ],
        )

        assert fetched == ["https://dl.example/Dune.epub"]
        assert (tmp_path / "Dune.epub").read_bytes() == b"data"
        assert state.phase == "complete"
        assert state.progress == 100.0

    def test_everything_is_taken_when_nothing_looks_like_a_book(self, tmp_path, monkeypatch):
        client = DebridLinkClient.__new__(DebridLinkClient)
        state = _DownloadState(
            torrent_id="DL1", name="Dune", target_dir=tmp_path, phase="downloading_http"
        )

        def fake_download(url, referer=None):
            buf = MagicMock()
            buf.getvalue = MagicMock(return_value=b"data")
            return buf

        monkeypatch.setattr("shelfmark.download.clients.debridlink.download_url", fake_download)

        client._process_and_download(
            state, [{"name": "readme.nfo", "downloadUrl": "https://dl.example/readme.nfo"}]
        )

        assert (tmp_path / "readme.nfo").exists()
        assert state.phase == "complete"

    def test_no_links_is_an_error(self, tmp_path):
        client = DebridLinkClient.__new__(DebridLinkClient)
        state = _DownloadState(
            torrent_id="DL1", name="Dune", target_dir=tmp_path, phase="downloading_http"
        )

        client._process_and_download(state, [{"name": "Dune.epub"}])

        assert state.phase == "error"
        assert "No download links" in (state.error_message or "")


class TestAsFloat:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (42, 42.0),
            (4.5, 4.5),
            ("17", 17.0),
            ("not a number", 0.0),
            (None, 0.0),
            (True, 0.0),
        ],
    )
    def test_api_numbers_are_coerced_without_raising(self, value, expected):
        assert _as_float(value) == expected
