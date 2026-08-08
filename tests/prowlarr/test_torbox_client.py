"""Unit tests for the Torbox download client."""

from io import BytesIO
from unittest.mock import MagicMock

from shelfmark.download.clients import DownloadState
from shelfmark.download.clients.torbox import TorboxClient


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def _client(monkeypatch):
    config_values = {
        "PROWLARR_TORRENT_CLIENT": "torbox",
        "TORBOX_API_KEY": "api-key",
    }
    monkeypatch.setattr(
        "shelfmark.download.clients.torbox.config.get",
        lambda key, default="": config_values.get(key, default),
    )
    TorboxClient._downloads.clear()
    return TorboxClient()


def test_is_configured(monkeypatch):
    assert TorboxClient.is_configured() is False
    _client(monkeypatch)
    assert TorboxClient.is_configured() is True


def test_connection_accepts_active_subscription(monkeypatch):
    client = _client(monkeypatch)
    mock_get = MagicMock(return_value=_response({
        "success": True,
        "data": {"email": "reader@example.com", "is_subscribed": True, "plan": 2},
    }))
    monkeypatch.setattr("shelfmark.download.clients.torbox.requests.get", mock_get)

    assert client.test_connection() == (True, "Connected to Torbox as 'reader@example.com'")
    assert mock_get.call_args.kwargs["headers"] == {"Authorization": "Bearer api-key"}


def test_add_download_posts_magnet_and_stores_state(monkeypatch):
    client = _client(monkeypatch)
    mock_post = MagicMock(return_value=_response({"success": True, "data": {"torrent_id": 42}}))
    monkeypatch.setattr("shelfmark.download.clients.torbox.requests.post", mock_post)

    assert client.add_download("magnet:?xt=urn:btih:hash", "A Book") == "42"
    assert mock_post.call_args.kwargs["data"] == {
        "magnet": "magnet:?xt=urn:btih:hash",
        "name": "A Book",
    }
    assert "42" in client._downloads


def test_get_status_starts_http_download_when_torrent_is_cached(monkeypatch):
    client = _client(monkeypatch)
    state = client._ensure_state("42")
    mock_get = MagicMock(return_value=_response({
        "success": True,
        "data": {"download_state": "cached", "files": [{"id": 7, "name": "book.epub"}]},
    }))
    monkeypatch.setattr("shelfmark.download.clients.torbox.requests.get", mock_get)
    started_with = []
    monkeypatch.setattr(client, "_maybe_start_download_thread", lambda *args: started_with.extend(args))

    status = client.get_status("42")

    assert status.state == DownloadState.DOWNLOADING
    assert status.progress == 50.0
    assert started_with == [state, [{"id": 7, "name": "book.epub"}]]
    assert mock_get.call_args.kwargs["params"] == {"id": "42", "bypass_cache": "true"}


def test_download_files_requests_tokenized_link_and_filters_books(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    state = client._ensure_state("42")
    state.target_dir = tmp_path
    mock_get = MagicMock(return_value=_response({"success": True, "data": "https://cdn.example/book"}))
    monkeypatch.setattr("shelfmark.download.clients.torbox.requests.get", mock_get)
    monkeypatch.setattr(
        "shelfmark.download.clients.torbox.download_url",
        lambda *args, **kwargs: BytesIO(b"contents"),
    )

    client._download_files(state, [{"id": 1, "name": "cover.jpg"}, {"id": 2, "name": "book.epub"}])

    assert (tmp_path / "book.epub").read_bytes() == b"contents"
    assert not (tmp_path / "cover.jpg").exists()
    assert mock_get.call_args.kwargs["params"] == {
        "token": "api-key",
        "torrent_id": "42",
        "file_id": 2,
    }
    assert state.phase == "complete"


def test_remove_uses_torbox_delete_operation(monkeypatch):
    client = _client(monkeypatch)
    mock_post = MagicMock(return_value=_response({"success": True, "data": None}))
    monkeypatch.setattr("shelfmark.download.clients.torbox.requests.post", mock_post)

    assert client.remove("42") is True
    assert mock_post.call_args.kwargs["json"] == {"torrent_id": 42, "operation": "delete"}
