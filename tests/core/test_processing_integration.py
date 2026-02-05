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


def _sync_config(mock_config, mock_core):
    mock_core.get = mock_config.get
    mock_core.CUSTOM_SCRIPT = mock_config.CUSTOM_SCRIPT


def test_direct_download_rename_moves_file(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

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

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", staging):
        mock_config.get = _build_config(ingest, organization="rename")
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

        result = _post_process_download(temp_file, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.parent == ingest
    assert result_path.name == "Brandon Sanderson - The Way of Kings.epub"
    assert not temp_file.exists()
    assert any("Moving" in msg for _, msg in statuses)


def test_torrent_hardlink_preserves_source(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

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

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", tmp_path / "staging"):
        mock_config.get = _build_config(ingest, organization="organize", hardlink=True)
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

        result = _post_process_download(original, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert original.exists()
    assert os.stat(original).st_ino == os.stat(result_path).st_ino


def test_torrent_hardlink_enabled_archive_is_hardlinked_without_extraction(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

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

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", tmp_path / "staging"):
        mock_config.get = _build_config(
            ingest,
            organization="none",
            hardlink=True,
            supported_formats=["zip"],
        )
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

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


def test_torrent_hardlink_enabled_copy_fallback_does_not_extract_archives(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

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

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", staging), \
         patch("shelfmark.download.postprocess.transfer.same_filesystem", return_value=False):
        mock_config.get = _build_config(ingest, organization="none", hardlink=True)
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

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


def test_torrent_hardlink_enabled_copy_fallback_directory_archive_kept_when_zip_supported(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

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

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", staging), \
         patch("shelfmark.download.postprocess.transfer.same_filesystem", return_value=False):
        mock_config.get = _build_config(
            ingest,
            organization="none",
            hardlink=True,
            supported_formats=["zip"],
        )
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

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


def test_torrent_copy_when_hardlink_disabled(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

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

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", staging):
        mock_config.get = _build_config(ingest, organization="none", hardlink=False)
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

        result = _post_process_download(original, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.name == "Seed.epub"
    assert original.exists()
    assert os.stat(original).st_ino != os.stat(result_path).st_ino
    assert list(staging.iterdir()) == []


def test_archive_extraction_flow(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

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

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", staging):
        mock_config.get = _build_config(ingest, organization="rename")
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

        result = _post_process_download(archive_path, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.parent == ingest


def test_archive_extraction_organize_creates_directories(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

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

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", staging):
        mock_config.get = _build_config(ingest, organization="organize")
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

        result = _post_process_download(archive_path, task, Event(), status_cb)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()
    assert result_path.parent == ingest / "Tester"
    assert result_path.name == "Archive Test.epub"


def test_archive_extraction_organize_multifile_assigns_part_numbers(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

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

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", staging):
        mock_config.get = _build_config(ingest, organization="organize")
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

        result = _post_process_download(archive_path, task, Event(), status_cb)

    assert result is not None
    author_dir = ingest / "Tester"
    files = sorted(author_dir.glob("*.mp3"))
    assert len(files) == 2
    assert files[0].name == "Archive Audio - 01.mp3"
    assert files[1].name == "Archive Audio - 02.mp3"


def test_booklore_mode_uploads_and_cleans_staging(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

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
    }

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.download.outputs.booklore.booklore_login", return_value="token"), \
         patch("shelfmark.download.outputs.booklore.booklore_upload_file", side_effect=_upload_stub), \
         patch("shelfmark.config.env.TMP_DIR", staging):
        mock_config.get = MagicMock(side_effect=lambda key, default=None: booklore_values.get(key, default))

        result = _post_process_download(temp_file, task, Event(), status_cb)

    assert result is not None
    assert uploaded_files
    assert not temp_file.exists()
    assert list(staging.iterdir()) == []
    assert any("Booklore" in (message or "") for _, message in statuses)


def test_booklore_mode_rejects_unsupported_files(tmp_path):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

    staging = tmp_path / "staging"
    staging.mkdir()

    temp_file = staging / "book.djvu"
    temp_file.write_text("content")

    task = DownloadTask(
        task_id="direct-booklore-unsupported",
        source="direct_download",
        title="Unsupported Book",
        author="Tester",
        format="djvu",
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
    }

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.download.outputs.booklore.booklore_login") as mock_login, \
         patch("shelfmark.download.outputs.booklore.booklore_upload_file") as mock_upload, \
         patch("shelfmark.config.env.TMP_DIR", staging):
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


@pytest.mark.parametrize("organization", ["none", "rename", "organize"])
@pytest.mark.parametrize("input_kind", ["file", "directory", "archive"])
@pytest.mark.parametrize("source_kind", ["direct", "usenet"])
@pytest.mark.parametrize("content_kind", ["book", "audiobook"])

def test_postprocess_folder_blackbox_matrix(
    tmp_path,
    source_kind: str,
    input_kind: str,
    organization: str,
    content_kind: str,
):
    """Black-box matrix test over common pipeline knobs.

    Goals:
    - Exercise the real `post_process_download` flow end-to-end
    - Vary key knobs (source semantics, input shape, organization mode)
    - Assert invariants (TMP cleanup, external source preservation)

    This intentionally avoids mocking internal pipeline helpers.
    """

    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    downloads = tmp_path / "downloads"
    staging.mkdir()
    ingest.mkdir()
    downloads.mkdir()

    author = "Tester"
    title = "Matrix Book"

    if content_kind == "audiobook":
        extension = "mp3"
        content_type = "audiobook"
    else:
        extension = "epub"
        content_type = None

    task = DownloadTask(
        task_id=f"matrix-{source_kind}-{input_kind}-{organization}-{content_kind}",
        source="direct_download" if source_kind == "direct" else "prowlarr",
        title=title,
        author=author,
        format=extension,
        content_type=content_type,
        search_mode=SearchMode.DIRECT,
        original_download_path=None,
    )

    base_dir = staging if source_kind == "direct" else downloads

    if input_kind == "file":
        input_path = base_dir / f"random.{extension}"
        input_path.write_text("content")
        expected_original_name = input_path.name
    elif input_kind == "directory":
        input_path = base_dir / "release"
        input_path.mkdir()
        (input_path / f"random.{extension}").write_text("content")
        expected_original_name = f"random.{extension}"
    elif input_kind == "archive":
        input_path = base_dir / "release.zip"
        with zipfile.ZipFile(input_path, "w") as zf:
            zf.writestr(f"book.{extension}", "content")
        expected_original_name = f"book.{extension}"
    else:
        raise AssertionError(f"Unknown input_kind: {input_kind}")

    status_cb = lambda *_args: None

    supported_formats = [extension] if extension != "mp3" else ["epub"]
    supported_audiobook_formats = [extension] if extension == "mp3" else ["mp3"]

    with patch("shelfmark.core.config.config") as mock_config, patch("shelfmark.config.env.TMP_DIR", staging):
        mock_config.get = _build_config(
            ingest,
            organization=organization,
            supported_formats=supported_formats,
            supported_audiobook_formats=supported_audiobook_formats,
        )
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

        result = _post_process_download(input_path, task, Event(), status_cb)

    assert result is not None

    result_path = Path(result)
    assert result_path.exists()

    if organization == "organize":
        assert result_path.parent == ingest / author
        assert result_path.name == f"{title}.{extension}"
    elif organization == "rename":
        assert result_path.parent == ingest
        assert result_path.name == f"{author} - {title}.{extension}"
    else:
        assert result_path.parent == ingest
        assert result_path.name == expected_original_name

    # TMP workspace should be cleaned up fully.
    assert list(staging.iterdir()) == []

    # Source preservation depends on whether Shelfmark owns the workspace.
    if source_kind == "direct":
        assert not input_path.exists()
    else:
        assert input_path.exists()


@pytest.mark.parametrize("input_kind", ["file", "directory"])
@pytest.mark.parametrize("content_kind", ["book", "audiobook"])
@pytest.mark.parametrize("organization", ["none", "organize"])
@pytest.mark.parametrize("hardlink_enabled", [False, True])
@pytest.mark.parametrize("same_filesystem", [True, False])

def test_postprocess_torrent_blackbox_matrix(
    tmp_path,
    input_kind: str,
    content_kind: str,
    organization: str,
    hardlink_enabled: bool,
    same_filesystem: bool,
):
    """Torrent-like (original_download_path set) black-box test matrix.

    This exercises:
    - hardlink enabled/disabled
    - same-filesystem hardlink vs copy fallback
    - content type differences (book vs audiobook)

    Assertions focus on invariants:
    - source is never deleted (seeding safety)
    - output is imported with expected naming
    - hardlink shares inode when expected
    - TMP workspace stays clean
    """

    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    downloads.mkdir()
    staging.mkdir()
    ingest.mkdir()

    author = "Tester"
    title = "Torrent Matrix"

    if content_kind == "audiobook":
        extension = "mp3"
        content_type = "audiobook"
        supported_formats = ["epub"]
        supported_audiobook_formats = ["mp3"]
    else:
        extension = "epub"
        content_type = None
        supported_formats = ["epub"]
        supported_audiobook_formats = ["mp3"]

    if input_kind == "file":
        input_path = downloads / f"random.{extension}"
        input_path.write_text("content")
        source_file = input_path
    else:
        input_path = downloads / "release"
        input_path.mkdir()
        source_file = input_path / f"random.{extension}"
        source_file.write_text("content")

    task = DownloadTask(
        task_id=f"torrent-matrix-{input_kind}-{content_kind}-{organization}-{hardlink_enabled}-{same_filesystem}",
        source="prowlarr",
        title=title,
        author=author,
        format=extension,
        content_type=content_type,
        search_mode=SearchMode.UNIVERSAL,
        original_download_path=str(input_path),
    )

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", staging), \
         patch("shelfmark.download.postprocess.transfer.same_filesystem", return_value=same_filesystem):
        mock_config.get = _build_config(
            ingest,
            organization=organization,
            hardlink=hardlink_enabled,
            supported_formats=supported_formats,
            supported_audiobook_formats=supported_audiobook_formats,
        )
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

        result = _post_process_download(input_path, task, Event(), lambda *_args: None)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()

    # Source must always remain for seeding.
    assert input_path.exists()
    assert source_file.exists()

    if organization == "organize":
        assert result_path.parent == ingest / author
        assert result_path.name == f"{title}.{extension}"
    else:
        assert result_path.parent == ingest
        assert result_path.name == f"random.{extension}"

    # Hardlink only when enabled and same filesystem.
    if hardlink_enabled and same_filesystem:
        assert os.stat(source_file).st_ino == os.stat(result_path).st_ino
    else:
        assert os.stat(source_file).st_ino != os.stat(result_path).st_ino

    # TMP workspace should be cleaned.
    assert list(staging.iterdir()) == []



def test_custom_script_external_source_stages_copy_and_preserves_source(tmp_path):
    """Custom script should run against the final imported file; external source must be preserved."""

    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    downloads.mkdir()
    staging.mkdir()
    ingest.mkdir()

    original = downloads / "Seed.epub"
    original.write_text("content")

    task = DownloadTask(
        task_id="usenet-custom-script",
        source="prowlarr",
        title="Seed",
        author="Seeder",
        format="epub",
        search_mode=SearchMode.UNIVERSAL,
        original_download_path=None,
    )

    with patch("shelfmark.core.config.config") as mock_config, \
         patch("shelfmark.config.env.TMP_DIR", staging), \
         patch("subprocess.run") as mock_run:
        mock_config.get = _build_config(ingest, organization="none")
        mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
        _sync_config(mock_config, mock_config)

        mock_run.return_value = MagicMock(stdout="", returncode=0)

        result = _post_process_download(original, task, Event(), lambda *_args: None)

    assert result is not None
    result_path = Path(result)
    assert result_path.exists()

    # Original external file must be preserved.
    assert original.exists()

    # Script should have run against the final imported file.
    assert mock_run.call_count == 1
    script_args = mock_run.call_args[0][0]
    assert script_args[0] == "/path/to/script.sh"
    assert Path(script_args[1]) == result_path

    # Staging directory should be cleaned.
    assert list(staging.iterdir()) == []



@pytest.mark.parametrize("content_kind", ["book", "audiobook"])

def test_external_directory_multiple_archives_extracts_all_and_keeps_source(tmp_path, content_kind: str):
    """External directories with only archives should extract into TMP and not touch source archives."""

    # This case is meant to model a usenet-like client "completed" directory containing
    # one or more archive releases, where Shelfmark must treat the source as read-only.

    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    downloads.mkdir()
    staging.mkdir()
    ingest.mkdir()

    source_dir = downloads / "release"
    source_dir.mkdir()

    if content_kind == "audiobook":
        extension = "mp3"
        content_type = "audiobook"
        supported_formats = ["epub"]
        supported_audiobook_formats = ["mp3"]
    else:
        extension = "epub"
        content_type = None
        supported_formats = ["epub"]
        supported_audiobook_formats = ["mp3"]

    archive_1 = source_dir / "a.zip"
    archive_2 = source_dir / "b.zip"

    with zipfile.ZipFile(archive_1, "w") as zf:
        zf.writestr(f"a.{extension}", f"content-a-{extension}")
    with zipfile.ZipFile(archive_2, "w") as zf:
        zf.writestr(f"b.{extension}", f"content-b-{extension}")

    task = DownloadTask(
        task_id=f"usenet-dir-archives-{content_kind}",
        source="prowlarr",
        title="Ignored",
        author="Ignored",
        format=extension,
        content_type=content_type,
        search_mode=SearchMode.DIRECT,
        original_download_path=None,
    )

    with patch("shelfmark.core.config.config") as mock_config, patch("shelfmark.config.env.TMP_DIR", staging):
        mock_config.get = _build_config(
            ingest,
            organization="none",
            supported_formats=supported_formats,
            supported_audiobook_formats=supported_audiobook_formats,
        )
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

        result = _post_process_download(source_dir, task, Event(), lambda *_args: None)

    assert result is not None

    # Both archives remain in the external source directory.
    assert archive_1.exists()
    assert archive_2.exists()

    # Extracted files should have been imported.
    assert (ingest / f"a.{extension}").exists()
    assert (ingest / f"b.{extension}").exists()

    # TMP staging should be cleaned.
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("content_kind", ["book", "audiobook"])

def test_external_directory_prefers_files_over_archives_and_keeps_source(tmp_path, content_kind: str):
    """If supported files exist in an external directory, archives are ignored.

    This models a usenet-like client directory that contains both a usable file and
    an archive. Shelfmark should import the usable file and leave the archive alone.
    """

    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

    downloads = tmp_path / "downloads"
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    downloads.mkdir()
    staging.mkdir()
    ingest.mkdir()

    source_dir = downloads / "release"
    source_dir.mkdir()

    if content_kind == "audiobook":
        extension = "mp3"
        content_type = "audiobook"
        supported_formats = ["epub"]
        supported_audiobook_formats = ["mp3"]
    else:
        extension = "epub"
        content_type = None
        supported_formats = ["epub"]
        supported_audiobook_formats = ["mp3"]

    primary_file = source_dir / f"keep.{extension}"
    primary_file.write_text("primary")

    archive = source_dir / "extra.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"from_archive.{extension}", "archive")

    task = DownloadTask(
        task_id=f"usenet-dir-mixed-{content_kind}",
        source="prowlarr",
        title="Ignored",
        author="Ignored",
        format=extension,
        content_type=content_type,
        search_mode=SearchMode.DIRECT,
        original_download_path=None,
    )

    with patch("shelfmark.core.config.config") as mock_config, patch("shelfmark.config.env.TMP_DIR", staging):
        mock_config.get = _build_config(
            ingest,
            organization="none",
            supported_formats=supported_formats,
            supported_audiobook_formats=supported_audiobook_formats,
        )
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)

        result = _post_process_download(source_dir, task, Event(), lambda *_args: None)

    assert result is not None

    # External source directory and files must remain untouched.
    assert source_dir.exists()
    assert primary_file.exists()
    assert archive.exists()

    # Import should use the existing supported file, not extract the archive.
    assert (ingest / f"keep.{extension}").exists()
    assert not (ingest / f"from_archive.{extension}").exists()

    # TMP staging should be cleaned.
    assert list(staging.iterdir()) == []
