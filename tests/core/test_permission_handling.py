import errno
from pathlib import Path
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask, SearchMode
from shelfmark.download.postprocess.pipeline import collect_directory_files, validate_destination


def test_validate_destination_success_cleans_up_probe(tmp_path):
    destination = tmp_path / "dest"
    status_cb = MagicMock()

    assert validate_destination(destination, status_cb) is True
    assert list(destination.glob(".shelfmark_write_test_*")) == []


def test_validate_destination_write_probe_permission_error(tmp_path):
    destination = tmp_path / "dest"
    destination.mkdir()
    status_cb = MagicMock()

    real_write_text = Path.write_text

    def fake_write_text(self, data, *args, **kwargs):
        if ".shelfmark_write_test_" in self.name:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_write_text(self, data, *args, **kwargs)

    with patch("pathlib.Path.write_text", new=fake_write_text):
        assert validate_destination(destination, status_cb) is False

    status_cb.assert_called()
    assert status_cb.call_args[0][0] == "error"
    assert "Destination not writable" in status_cb.call_args[0][1]


def test_collect_directory_files_ignores_permission_errors(tmp_path):
    directory = tmp_path / "download"
    directory.mkdir()
    (directory / "book.epub").write_text("content")

    task = DownloadTask(
        task_id="scan-test",
        source="direct_download",
        title="Test",
        format="epub",
        search_mode=SearchMode.UNIVERSAL,
    )

    def fake_walk(top, onerror=None):
        if onerror:
            onerror(PermissionError(errno.EACCES, "Permission denied", str(Path(top) / "secret")))
        yield str(top), [], ["book.epub"]

    with patch("shelfmark.download.postprocess.scan.os.walk", side_effect=fake_walk):
        files, rejected, cleanup, error = collect_directory_files(
            directory,
            task,
            allow_archive_extraction=True,
            status_callback=None,
        )

    assert error is None
    assert rejected == []
    assert cleanup == []
    assert (directory / "book.epub") in files


def test_collect_directory_files_permission_denied_root(tmp_path):
    directory = tmp_path / "download"
    directory.mkdir()

    task = DownloadTask(
        task_id="scan-test",
        source="direct_download",
        title="Test",
        format="epub",
        search_mode=SearchMode.UNIVERSAL,
    )

    with patch(
        "shelfmark.download.postprocess.scan.os.scandir",
        side_effect=PermissionError(errno.EACCES, "Permission denied", str(directory)),
    ):
        files, rejected, cleanup, error = collect_directory_files(
            directory,
            task,
            allow_archive_extraction=True,
            status_callback=None,
        )

    assert files == []
    assert rejected == []
    assert cleanup == []
    assert error is not None
    assert error.startswith("Permission denied")
