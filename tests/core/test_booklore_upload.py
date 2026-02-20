from unittest.mock import MagicMock, patch

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

    with patch(
        "shelfmark.download.outputs.booklore.requests.post",
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

    with patch(
        "shelfmark.download.outputs.booklore.requests.post",
        return_value=response,
    ) as mock_post:
        booklore_upload_file(_booklore_config(upload_to_bookdrop=True), "token", file_path)

    assert mock_post.call_count == 1
    args, kwargs = mock_post.call_args
    assert args[0] == "http://booklore:6060/api/v1/files/upload/bookdrop"
    assert kwargs["params"] is None
    assert kwargs["headers"] == {"Authorization": "Bearer token"}
