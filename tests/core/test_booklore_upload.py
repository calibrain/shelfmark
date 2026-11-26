from unittest.mock import MagicMock, patch

import pytest

from shelfmark.download.network import get_session as _shared_session
from shelfmark.download.outputs.booklore import BookloreConfig, booklore_upload_file


def _booklore_config(upload_to_bookdrop: bool) -> BookloreConfig:
    return BookloreConfig(
        base_url="http://booklore:6060",
        username="admin",
        password="secret",
        library_id=7,
        path_id=21,
        upload_to_bookdrop=upload_to_bookdrop,
        refresh_after_upload=not upload_to_bookdrop,
    )


def test_booklore_upload_file_uses_library_endpoint_with_query_params(tmp_path):
    file_path = tmp_path / "book.epub"
    file_path.write_bytes(b"content")
    response = MagicMock()
    response.raise_for_status.return_value = None

    with patch.object(
        _shared_session(),
        "post",
        return_value=response,
    ) as mock_post:
        booklore_upload_file(_booklore_config(upload_to_bookdrop=False), "token", file_path)

    assert mock_post.call_count == 1
    args, kwargs = mock_post.call_args
    assert args[0] == "http://booklore:6060/api/v1/files/upload"
    assert kwargs["params"] == {"libraryId": 7, "pathId": 21}
    assert kwargs["headers"] == {"Authorization": "Bearer token"}


def test_booklore_upload_file_uses_bookdrop_endpoint_without_query_params(tmp_path):
    file_path = tmp_path / "book.epub"
    file_path.write_bytes(b"content")
    response = MagicMock()
    response.raise_for_status.return_value = None

    with patch.object(
        _shared_session(),
        "post",
        return_value=response,
    ) as mock_post:
        booklore_upload_file(_booklore_config(upload_to_bookdrop=True), "token", file_path)

    assert mock_post.call_count == 1
    args, kwargs = mock_post.call_args
    assert args[0] == "http://booklore:6060/api/v1/files/upload/bookdrop"
    assert kwargs["params"] is None
    assert kwargs["headers"] == {"Authorization": "Bearer token"}


def _login_reply(_call):
    """A Grimmory login answer carrying a token."""
    import json

    import requests

    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"accessToken": "t"}).encode()
    return response


@pytest.mark.parametrize(
    ("mode", "base_url", "verify"),
    [
        ("enabled", "https://192.168.1.20:6060", True),
        ("disabled_local", "https://192.168.1.20:6060", False),
        ("disabled_local", "https://grimmory.example.com", True),
        ("disabled", "https://grimmory.example.com", False),
    ],
)
def test_login_follows_certificate_validation_by_default(
    monkeypatch, sent_requests, mode, base_url, verify
):
    """With no verify_tls of its own, Grimmory gets the Certificate Validation setting."""
    from shelfmark.download.outputs.booklore import booklore_login

    monkeypatch.setattr(
        "shelfmark.download.network.app_config.get",
        lambda key, default=None: mode if key == "CERTIFICATE_VALIDATION" else default,
    )
    sent_requests.respond = _login_reply
    config = BookloreConfig(
        base_url=base_url, username="admin", password="secret", library_id=1, path_id=1
    )

    booklore_login(config)

    assert sent_requests.last()["verify"] is verify


def test_an_explicit_verify_tls_still_wins(monkeypatch, sent_requests):
    """A BookloreConfig that sets verify_tls keeps it whatever the global setting says."""
    from shelfmark.download.outputs.booklore import booklore_login

    monkeypatch.setattr(
        "shelfmark.download.network.app_config.get",
        lambda key, default=None: "disabled" if key == "CERTIFICATE_VALIDATION" else default,
    )
    sent_requests.respond = _login_reply
    config = BookloreConfig(
        base_url="https://grimmory.example.com",
        username="admin",
        password="secret",
        library_id=1,
        path_id=1,
        verify_tls=True,
    )

    booklore_login(config)

    assert sent_requests.last()["verify"] is True
