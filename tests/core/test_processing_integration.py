"""Integration tests for real filesystem processing flows."""

import os
import zipfile
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.models import DownloadTask, SearchMode


def _build_config(
    destination: Path,
    organization: str,
    hardlink: bool = False,
    rename_template: str = "{Author} - {Title}",
    supported_formats: list[str] | None = None,
    supported_audiobook_formats: list[str] | None = None,
):
    values = {
        "DESTINATION": str(destination),
        "INGEST_DIR": str(destination),
        "DESTINATION_AUDIOBOOK": str(destination),
        "FILE_ORGANIZATION": organization,
        "FILE_ORGANIZATION_AUDIOBOOK": organization,
        "TEMPLATE_RENAME": rename_template,
        "TEMPLATE_ORGANIZE": "{Author}/{Title}",
        "TEMPLATE_AUDIOBOOK_RENAME": rename_template,
        "TEMPLATE_AUDIOBOOK_ORGANIZE": "{Author}/{Title}{ - PartNumber}",
        "SUPPORTED_FORMATS": supported_formats or ["epub"],
        "SUPPORTED_AUDIOBOOK_FORMATS": supported_audiobook_formats or ["mp3"],
        "HARDLINK_TORRENTS": hardlink,
        "HARDLINK_TORRENTS_AUDIOBOOK": hardlink,
    }
    return MagicMock(side_effect=lambda key, default=None: values.get(key, default))


def _sync_config(mock_config, mock_core, mock_archive):
    mock_core.get = mock_config.get
    mock_core.CUSTOM_SCRIPT = mock_config.CUSTOM_SCRIPT
    mock_archive.get = mock_config.get


@pytest.mark.integration
def test_direct_download_rename_moves_file(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()

    temp_file = staging / "book.epub"
    temp_file.write_text("content")

    task = DownloadTask(
        task_id="direct-1",
        source="direct_download",
        title="The Way of Kings",
        author="Brandon Sanderson",
        format="epub",
        search_mode=SearchMode.DIRECT,
    )

    statuses = []
    status_cb = lambda status, message: statuses.append((status, message))

    with patch("shelfmark.download.orchestrator.config") as mock_config, \
         patch("shelfmark.core.config.config") as mock_core, \
         patch("shelfmark.download.archive.config") as mock_archive, \
         patch("shelfmark.download.orchestrator.TMP_DIR", staging):
        mock_config.get = _build_config(ingest, organization="rename")
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_core, mock_archive)

        result = _post_process_download(temp_file, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.parent == ingest
    assert result_path.name == "Brandon Sanderson - The Way of Kings.epub"
    assert not temp_file.exists()
    assert any("Moving" in msg for _, msg in statuses)


@pytest.mark.integration
def test_torrent_hardlink_preserves_source(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    downloads = tmp_path / "downloads"
    ingest = tmp_path / "ingest"
    downloads.mkdir()
    ingest.mkdir()

    original = downloads / "Stormlight.epub"
    original.write_text("content")

    task = DownloadTask(
        task_id="torrent-1",
        source="prowlarr",
        title="The Way of Kings",
        author="Brandon Sanderson",
        format="epub",
        search_mode=SearchMode.UNIVERSAL,
        original_download_path=str(original),
    )

    status_cb = lambda *_args: None

    with patch("shelfmark.download.orchestrator.config") as mock_config, \
         patch("shelfmark.core.config.config") as mock_core, \
         patch("shelfmark.download.archive.config") as mock_archive, \
         patch("shelfmark.download.orchestrator.TMP_DIR", tmp_path / "staging"):
        mock_config.get = _build_config(ingest, organization="organize", hardlink=True)
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_core, mock_archive)

        result = _post_process_download(original, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert original.exists()
    assert os.stat(original).st_ino == os.stat(result_path).st_ino


@pytest.mark.integration
def test_torrent_hardlink_enabled_archive_is_hardlinked_without_extraction(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    downloads = tmp_path / "downloads"
    ingest = tmp_path / "ingest"
    downloads.mkdir()
    ingest.mkdir()

    original = downloads / "Seed.zip"
    with zipfile.ZipFile(original, "w") as zf:
        zf.writestr("Seed.epub", "content")

    task = DownloadTask(
        task_id="torrent-zip-hardlink",
        source="prowlarr",
        title="Seed",
        author="Seeder",
        format="epub",
        search_mode=SearchMode.UNIVERSAL,
        original_download_path=str(original),
    )

    status_cb = lambda *_args: None

    with patch("shelfmark.download.orchestrator.config") as mock_config, \
         patch("shelfmark.core.config.config") as mock_core, \
         patch("shelfmark.download.archive.config") as mock_archive, \
         patch("shelfmark.download.orchestrator.TMP_DIR", tmp_path / "staging"):
        mock_config.get = _build_config(
            ingest,
            organization="none",
            hardlink=True,
            supported_formats=["zip"],
        )
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_core, mock_archive)

        result = _post_process_download(original, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.suffix == ".zip"

    # Torrent source preserved for seeding.
    assert original.exists()

    # Hardlink success (same inode).
    assert os.stat(original).st_ino == os.stat(result_path).st_ino

    # No extraction should occur.
    assert list(ingest.glob("*.epub")) == []


@pytest.mark.integration
def test_torrent_hardlink_enabled_copy_fallback_does_not_extract_archives(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    downloads.mkdir()
    staging.mkdir()
    ingest.mkdir()

    original = downloads / "Seed.zip"
    with zipfile.ZipFile(original, "w") as zf:
        zf.writestr("Seed.epub", "content")

    task = DownloadTask(
        task_id="torrent-zip-fallback",
        source="prowlarr",
        title="Seed",
        author="Seeder",
        format="epub",
        search_mode=SearchMode.UNIVERSAL,
        original_download_path=str(original),
    )

    statuses = []
    status_cb = lambda status, message: statuses.append((status, message))

    with patch("shelfmark.download.orchestrator.config") as mock_config, \
         patch("shelfmark.core.config.config") as mock_core, \
         patch("shelfmark.download.archive.config") as mock_archive, \
         patch("shelfmark.download.orchestrator.TMP_DIR", staging), \
         patch("shelfmark.download.orchestrator.same_filesystem", return_value=False):
        mock_config.get = _build_config(ingest, organization="none", hardlink=True)
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_core, mock_archive)

        result = _post_process_download(original, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.suffix == ".zip"

    # Torrent source must remain for seeding.
    assert original.exists()

    # Most importantly: hardlink-setting-enabled fallback to copy should NOT extract.
    assert list(ingest.glob("*.epub")) == []

    assert any(msg.startswith("Copying") for _, msg in statuses)


@pytest.mark.integration
def test_torrent_hardlink_enabled_copy_fallback_directory_archive_kept_when_zip_supported(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    downloads.mkdir()
    staging.mkdir()
    ingest.mkdir()

    original_dir = downloads / "release"
    original_dir.mkdir()

    archive_path = original_dir / "Seed.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("Seed.epub", "content")

    task = DownloadTask(
        task_id="torrent-zip-dir-fallback",
        source="prowlarr",
        title="Seed",
        author="Seeder",
        format="epub",
        search_mode=SearchMode.UNIVERSAL,
        original_download_path=str(original_dir),
    )

    status_cb = lambda *_args: None

    with patch("shelfmark.download.orchestrator.config") as mock_config, \
         patch("shelfmark.core.config.config") as mock_core, \
         patch("shelfmark.download.archive.config") as mock_archive, \
         patch("shelfmark.download.orchestrator.TMP_DIR", staging), \
         patch("shelfmark.download.orchestrator.same_filesystem", return_value=False):
        mock_config.get = _build_config(
            ingest,
            organization="none",
            hardlink=True,
            supported_formats=["zip"],
        )
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_core, mock_archive)

        result = _post_process_download(original_dir, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.parent == ingest
    assert result_path.name == "Seed.zip"

    # Torrent source must remain intact for seeding.
    assert archive_path.exists()

    # Staging copy should be cleaned up.
    assert list(staging.iterdir()) == []


@pytest.mark.integration
def test_torrent_copy_when_hardlink_disabled(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    downloads.mkdir()
    staging.mkdir()
    ingest.mkdir()

    original = downloads / "Seed.epub"
    original.write_text("content")

    task = DownloadTask(
        task_id="torrent-2",
        source="prowlarr",
        title="Seed",
        author="Seeder",
        format="epub",
        search_mode=SearchMode.UNIVERSAL,
        original_download_path=str(original),
    )

    status_cb = lambda *_args: None

    with patch("shelfmark.download.orchestrator.config") as mock_config, \
         patch("shelfmark.core.config.config") as mock_core, \
         patch("shelfmark.download.archive.config") as mock_archive, \
         patch("shelfmark.download.orchestrator.TMP_DIR", staging):
        mock_config.get = _build_config(ingest, organization="none", hardlink=False)
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_core, mock_archive)

        result = _post_process_download(original, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.name == "Seed.epub"
    assert original.exists()
    assert os.stat(original).st_ino != os.stat(result_path).st_ino
    assert list(staging.iterdir()) == []


@pytest.mark.integration
def test_archive_extraction_flow(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()

    archive_path = staging / "book.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("book.epub", "content")

    task = DownloadTask(
        task_id="direct-archive",
        source="direct_download",
        title="Archive Test",
        author="Tester",
        format="epub",
        search_mode=SearchMode.DIRECT,
    )

    status_cb = lambda *_args: None

    with patch("shelfmark.download.orchestrator.config") as mock_config, \
         patch("shelfmark.core.config.config") as mock_core, \
         patch("shelfmark.download.archive.config") as mock_archive, \
         patch("shelfmark.download.orchestrator.TMP_DIR", staging):
        mock_config.get = _build_config(ingest, organization="rename")
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_core, mock_archive)

        result = _post_process_download(archive_path, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.parent == ingest


@pytest.mark.integration
def test_archive_extraction_organize_creates_directories(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()

    archive_path = staging / "book.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("book.epub", "content")

    task = DownloadTask(
        task_id="direct-archive-organize",
        source="direct_download",
        title="Archive Test",
        author="Tester",
        format="epub",
        search_mode=SearchMode.DIRECT,
    )

    status_cb = lambda *_args: None

    with patch("shelfmark.download.orchestrator.config") as mock_config, \
         patch("shelfmark.core.config.config") as mock_core, \
         patch("shelfmark.download.archive.config") as mock_archive, \
         patch("shelfmark.download.orchestrator.TMP_DIR", staging):
        mock_config.get = _build_config(ingest, organization="organize")
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_core, mock_archive)

        result = _post_process_download(archive_path, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.parent == ingest / "Tester"
    assert result_path.name == "Archive Test.epub"


@pytest.mark.integration
def test_archive_extraction_organize_multifile_assigns_part_numbers(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()

    archive_path = staging / "audio.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("Part 2.mp3", "audio2")
        zf.writestr("Part 10.mp3", "audio10")

    task = DownloadTask(
        task_id="direct-archive-audio",
        source="direct_download",
        title="Archive Audio",
        author="Tester",
        format="mp3",
        content_type="audiobook",
        search_mode=SearchMode.DIRECT,
    )

    status_cb = lambda *_args: None

    with patch("shelfmark.download.orchestrator.config") as mock_config, \
         patch("shelfmark.core.config.config") as mock_core, \
         patch("shelfmark.download.archive.config") as mock_archive, \
         patch("shelfmark.download.orchestrator.TMP_DIR", staging):
        mock_config.get = _build_config(ingest, organization="organize")
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_core, mock_archive)

        result = _post_process_download(archive_path, task, Event(), status_cb)

    assert result is not None
    author_dir = ingest / "Tester"
    files = sorted(author_dir.glob("*.mp3"))
    assert len(files) == 2
    assert files[0].name == "Archive Audio - 01.mp3"
    assert files[1].name == "Archive Audio - 02.mp3"


@pytest.mark.integration
def test_booklore_mode_uploads_and_cleans_staging(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    staging = tmp_path / "staging"
    staging.mkdir()

    temp_file = staging / "book.epub"
    temp_file.write_text("content")

    task = DownloadTask(
        task_id="direct-booklore",
        source="direct_download",
        title="The Way of Kings",
        author="Brandon Sanderson",
        format="epub",
        search_mode=SearchMode.DIRECT,
    )

    statuses = []
    status_cb = lambda status, message: statuses.append((status, message))
    uploaded_files = []

    def _upload_stub(_config, _token, file_path):
        uploaded_files.append(file_path)
        assert file_path.exists()

    booklore_values = {
        "BOOKS_OUTPUT_MODE": "booklore",
        "BOOKLORE_HOST": "http://booklore:6060",
        "BOOKLORE_USERNAME": "booklore",
        "BOOKLORE_PASSWORD": "secret",
        "BOOKLORE_LIBRARY_ID": 1,
        "BOOKLORE_PATH_ID": 2,
        "BOOKLORE_REFRESH_AFTER_UPLOAD": False,
    }

    with patch("shelfmark.download.outputs.booklore.config") as mock_config, \
         patch("shelfmark.download.outputs.booklore.booklore_login", return_value="token"), \
         patch("shelfmark.download.outputs.booklore.booklore_upload_file", side_effect=_upload_stub), \
         patch("shelfmark.download.orchestrator.TMP_DIR", staging):
        mock_config.get = MagicMock(side_effect=lambda key, default=None: booklore_values.get(key, default))

        result = _post_process_download(temp_file, task, Event(), status_cb)

    assert result is not None
    assert uploaded_files
    assert not temp_file.exists()
    assert list(staging.iterdir()) == []
    assert any("Booklore" in (message or "") for _, message in statuses)


@pytest.mark.integration
def test_booklore_mode_rejects_unsupported_files(tmp_path):
    from shelfmark.download.orchestrator import _post_process_download

    staging = tmp_path / "staging"
    staging.mkdir()

    temp_file = staging / "book.mobi"
    temp_file.write_text("content")

    task = DownloadTask(
        task_id="direct-booklore-unsupported",
        source="direct_download",
        title="Unsupported Book",
        author="Tester",
        format="mobi",
        search_mode=SearchMode.DIRECT,
    )

    status_cb = MagicMock()

    booklore_values = {
        "BOOKS_OUTPUT_MODE": "booklore",
        "BOOKLORE_HOST": "http://booklore:6060",
        "BOOKLORE_USERNAME": "booklore",
        "BOOKLORE_PASSWORD": "secret",
        "BOOKLORE_LIBRARY_ID": 1,
        "BOOKLORE_PATH_ID": 2,
        "BOOKLORE_REFRESH_AFTER_UPLOAD": False,
    }

    with patch("shelfmark.download.outputs.booklore.config") as mock_config, \
         patch("shelfmark.download.outputs.booklore.booklore_login") as mock_login, \
         patch("shelfmark.download.outputs.booklore.booklore_upload_file") as mock_upload, \
         patch("shelfmark.download.orchestrator.TMP_DIR", staging):
        mock_config.get = MagicMock(side_effect=lambda key, default=None: booklore_values.get(key, default))

        result = _post_process_download(temp_file, task, Event(), status_cb)

    assert result is None
    assert mock_login.call_count == 0
    assert mock_upload.call_count == 0
    assert not temp_file.exists()
    assert list(staging.iterdir()) == []

    errors = [call for call in status_cb.call_args_list if call.args[0] == "error"]
    assert errors
    assert "Booklore does not support" in errors[-1].args[1]
