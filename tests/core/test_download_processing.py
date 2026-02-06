"""Tests for download post-processing functionality.

Covers:
- _atomic_copy: atomic file copy with collision handling
- _post_process_download: main post-processing logic
- process_directory: directory processing with archive extraction
- Custom script execution
"""

import json
import os
import pytest
import shutil
import tempfile
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch, call

from shelfmark.core.models import DownloadTask, SearchMode


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_task():
    """Create a sample DownloadTask for testing."""
    return DownloadTask(
        task_id="test123",
        source="direct_download",
        title="The Way of Kings",
        author="Brandon Sanderson",
        format="epub",
        search_mode=SearchMode.UNIVERSAL,
    )


@pytest.fixture
def sample_direct_task():
    """Create a sample DownloadTask in Direct mode."""
    return DownloadTask(
        task_id="test123",
        source="direct_download",
        title="The Way of Kings",
        author="Brandon Sanderson",
        format="epub",
        search_mode=SearchMode.DIRECT,
    )


@pytest.fixture
def temp_dirs(tmp_path):
    """Create temp, staging, and ingest directories."""
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()
    return {
        "base": tmp_path,
        "staging": staging,
        "ingest": ingest,
    }


def _mock_destination_config(ingest_dir: Path, extra=None):
    values = {
        "DESTINATION": str(ingest_dir),
        "INGEST_DIR": str(ingest_dir),
    }
    if extra:
        values.update(extra)
    return MagicMock(side_effect=lambda key, default=None: values.get(key, default))


def _sync_core_config(mock_config, mock_core_config, mock_archive_config=None):
    mock_core_config.get = mock_config.get
    mock_core_config.CUSTOM_SCRIPT = getattr(mock_config, "CUSTOM_SCRIPT", None)
    if mock_archive_config is not None:
        mock_archive_config.get = mock_config.get


# =============================================================================
# _atomic_copy Tests
# =============================================================================

class TestAtomicCopy:
    """Tests for _atomic_copy() function."""

    def test_copies_file(self, tmp_path):
        """Copies file to destination."""
        from shelfmark.download.fs import atomic_copy as _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        result = _atomic_copy(source, dest)

        assert result == dest
        assert result.exists()
        assert result.read_text() == "content"
        # Source should still exist (copy, not move)
        assert source.exists()

    def test_preserves_source(self, tmp_path):
        """Source file is preserved after copy."""
        from shelfmark.download.fs import atomic_copy as _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("original content")
        dest = tmp_path / "dest.txt"

        _atomic_copy(source, dest)

        assert source.exists()
        assert source.read_text() == "original content"

    def test_handles_collision_with_counter(self, tmp_path):
        """Appends counter suffix when destination exists."""
        from shelfmark.download.fs import atomic_copy as _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("new content")
        dest = tmp_path / "dest.txt"
        dest.write_text("existing")

        result = _atomic_copy(source, dest)

        assert result == tmp_path / "dest_1.txt"
        assert result.read_text() == "new content"
        # Original destination preserved
        assert dest.read_text() == "existing"

    def test_multiple_collisions(self, tmp_path):
        """Increments counter until finding free slot."""
        from shelfmark.download.fs import atomic_copy as _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("new")
        (tmp_path / "dest.txt").touch()
        (tmp_path / "dest_1.txt").touch()
        (tmp_path / "dest_2.txt").touch()

        result = _atomic_copy(source, tmp_path / "dest.txt")

        assert result == tmp_path / "dest_3.txt"

    def test_preserves_extension(self, tmp_path):
        """Keeps extension when adding counter suffix."""
        from shelfmark.download.fs import atomic_copy as _atomic_copy

        source = tmp_path / "book.epub"
        source.write_bytes(b"epub content")
        (tmp_path / "book.epub").touch()

        result = _atomic_copy(source, tmp_path / "book.epub")

        assert result.suffix == ".epub"
        assert result.name == "book_1.epub"

    def test_creates_distinct_file(self, tmp_path):
        """Copy creates a distinct file (not hardlink)."""
        from shelfmark.download.fs import atomic_copy as _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        result = _atomic_copy(source, dest)

        # Should be different inodes (not a hardlink)
        assert os.stat(source).st_ino != os.stat(result).st_ino

    def test_copy_preserves_permissions(self, tmp_path):
        """Copy preserves file permissions (copy2 behavior)."""
        from shelfmark.download.fs import atomic_copy as _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("content")
        os.chmod(source, 0o644)

        dest = tmp_path / "dest.txt"
        result = _atomic_copy(source, dest)

        # Permissions should be preserved
        assert (os.stat(result).st_mode & 0o777) == 0o644

    def test_atomic_no_partial_file(self, tmp_path):
        """If copy fails, no partial file remains."""
        from shelfmark.download.fs import atomic_copy as _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        # Simulate shutil.copy2 failure mid-copy
        with patch('shutil.copy2', side_effect=IOError("Disk full")):
            with pytest.raises(IOError):
                _atomic_copy(source, dest)

        # No partial file should exist
        assert not dest.exists()

    def test_max_attempts_exceeded(self, tmp_path):
        """Raises after max collision attempts."""
        from shelfmark.download.fs import atomic_copy as _atomic_copy

        source = tmp_path / "source.txt"
        source.write_text("content")

        # Create 100 existing files
        for i in range(100):
            if i == 0:
                (tmp_path / "dest.txt").touch()
            else:
                (tmp_path / f"dest_{i}.txt").touch()

        with pytest.raises(RuntimeError, match="Could not copy file after 100 attempts"):
            _atomic_copy(source, tmp_path / "dest.txt", max_attempts=100)


# =============================================================================
# process_directory Tests
# =============================================================================

class TestProcessDirectory:
    """Tests for process_directory() function."""

    def test_finds_book_files(self, temp_dirs, sample_task):
        """Finds and moves book files to ingest."""
        from shelfmark.download.postprocess.pipeline import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "book.epub").write_bytes(b"epub content")

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):
            mock_config.USE_BOOK_TITLE = False
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "SUPPORTED_FORMATS": ["epub"],
                "FILE_ORGANIZATION": "none",
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        assert len(final_paths) == 1
        assert final_paths[0].exists()
        assert final_paths[0].name == "book.epub"
        # Source directory cleaned up
        assert not directory.exists()

    def test_multiple_book_files(self, temp_dirs, sample_task):
        """Handles multiple book files in directory."""
        from shelfmark.download.postprocess.pipeline import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "book1.epub").write_bytes(b"epub1")
        (directory / "book2.epub").write_bytes(b"epub2")

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):
            mock_config.USE_BOOK_TITLE = False
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "SUPPORTED_FORMATS": ["epub"],
                "FILE_ORGANIZATION": "none",
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        assert len(final_paths) == 2

    def test_no_book_files_returns_error(self, temp_dirs, sample_task):
        """Returns error when no book files found."""
        from shelfmark.download.postprocess.pipeline import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        # Use a file type that isn't trackable (not epub, pdf, txt, etc.)
        (directory / "readme.log").write_text("not a book")

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "SUPPORTED_FORMATS": ["epub"],
                "FILE_ORGANIZATION": "none",
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert final_paths == []
        assert error is not None
        assert "No book files found" in error

    def test_unsupported_format_error_message(self, temp_dirs, sample_task):
        """Returns helpful error when files exist but format unsupported."""
        from shelfmark.download.postprocess.pipeline import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "book.pdf").write_bytes(b"pdf content")

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "SUPPORTED_FORMATS": ["epub"],  # PDF not supported
                "FILE_ORGANIZATION": "none",
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert final_paths == []
        assert error is not None
        assert "format not supported" in error
        assert ".pdf" in error

    def test_uses_book_title_for_single_file(self, temp_dirs, sample_task):
        """Uses formatted title for single file when USE_BOOK_TITLE enabled."""
        from shelfmark.download.postprocess.pipeline import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "random_name.epub").write_bytes(b"content")

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):
            mock_config.USE_BOOK_TITLE = True
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "SUPPORTED_FORMATS": ["epub"],
                "FILE_ORGANIZATION": "rename",
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        assert len(final_paths) == 1
        # Should use task title, not original filename
        assert "The Way of Kings" in final_paths[0].name

    def test_preserves_filenames_for_multifile(self, temp_dirs, sample_task):
        """Preserves original filenames for multi-file downloads."""
        from shelfmark.download.postprocess.pipeline import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "Part 1.epub").write_bytes(b"part1")
        (directory / "Part 2.epub").write_bytes(b"part2")

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):
            mock_config.USE_BOOK_TITLE = True  # Ignored for multi-file
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "SUPPORTED_FORMATS": ["epub"],
                "FILE_ORGANIZATION": "none",
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        names = [p.name for p in final_paths]
        assert "Part 1.epub" in names
        assert "Part 2.epub" in names

    def test_nested_directory_files(self, temp_dirs, sample_task):
        """Finds book files in nested subdirectories."""
        from shelfmark.download.postprocess.pipeline import process_directory

        directory = temp_dirs["staging"] / "download"
        subdir = directory / "subdir"
        subdir.mkdir(parents=True)
        (subdir / "book.epub").write_bytes(b"content")

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):
            mock_config.USE_BOOK_TITLE = False
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "SUPPORTED_FORMATS": ["epub"],
                "FILE_ORGANIZATION": "none",
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert error is None
        assert len(final_paths) == 1

    def test_cleans_up_on_error(self, temp_dirs, sample_task):
        """Cleans up directory even on error."""
        from shelfmark.download.postprocess.pipeline import process_directory

        directory = temp_dirs["staging"] / "download"
        directory.mkdir()
        (directory / "book.epub").write_bytes(b"content")

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]), \
             patch('shelfmark.download.postprocess.transfer.atomic_move', side_effect=Exception("Move failed")):

            mock_config.USE_BOOK_TITLE = False
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "SUPPORTED_FORMATS": ["epub"],
                "FILE_ORGANIZATION": "none",
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            final_paths, error = process_directory(
                directory=directory,
                ingest_dir=temp_dirs["ingest"],
                task=sample_task,
            )

        assert final_paths == []
        assert error is not None
        assert "Move failed" in error
        # Directory should be cleaned up
        assert not directory.exists()


# =============================================================================
# _post_process_download Tests
# =============================================================================

class TestPostProcessDownload:
    """Tests for _post_process_download() function."""

    def test_simple_file_move_to_ingest(self, temp_dirs, sample_direct_task):
        """Simple file is moved to ingest directory."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"epub content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(temp_dirs["ingest"])
            _sync_core_config(mock_config, mock_config)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        result_path = Path(result)
        assert result_path.exists()
        assert result_path.parent == temp_dirs["ingest"]
        assert not temp_file.exists()  # Moved
        status_cb.assert_called_with("complete", "Complete")

    def test_uses_formatted_filename(self, temp_dirs, sample_direct_task):
        """Uses task title when USE_BOOK_TITLE enabled."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        temp_file = temp_dirs["staging"] / "random.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):

            mock_config.USE_BOOK_TITLE = True
            mock_config.CUSTOM_SCRIPT = None
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(temp_dirs["ingest"])
            _sync_core_config(mock_config, mock_config)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        result_path = Path(result)
        assert "The Way of Kings" in result_path.name

    def test_organize_mode_for_universal(self, temp_dirs, sample_task):
        """Universal mode organizes when configured."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        library = temp_dirs["base"] / "library"
        library.mkdir()
        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):

            mock_config.USE_BOOK_TITLE = True
            mock_config.CUSTOM_SCRIPT = None
            _sync_core_config(mock_config, mock_config)
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "DESTINATION": str(library),
                "FILE_ORGANIZATION": "organize",
                "TEMPLATE_ORGANIZE": "{Author}/{Title}",
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        result_path = Path(result)
        assert library in result_path.parents or result_path.parent == library
        status_cb.assert_called_with("complete", "Complete")

    def test_direct_mode_uses_ingest(self, temp_dirs, sample_direct_task):
        """Direct mode keeps ingest destination when not organizing."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        library = temp_dirs["base"] / "library"
        library.mkdir()
        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            _sync_core_config(mock_config, mock_config)
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "DESTINATION": str(temp_dirs["ingest"]),
                "FILE_ORGANIZATION": "none",
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        result_path = Path(result)
        # Should go to ingest, not library
        assert result_path.parent == temp_dirs["ingest"]

    def test_cancellation_before_ingest(self, temp_dirs, sample_direct_task):
        """Respects cancellation before final move."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()
        cancel_flag.set()  # Already cancelled

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(temp_dirs["ingest"])
            _sync_core_config(mock_config, mock_config)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is None
        # File should be cleaned up
        assert not temp_file.exists()

    # NOTE: archive extraction and torrent hardlink/copy behaviour are exercised via
    # black-box matrix scenarios in `tests/core/test_processing_integration.py`.

    def test_audiobook_uses_dedicated_ingest(self, temp_dirs, sample_task):
        """Audiobooks use dedicated ingest directory when configured."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        audiobook_ingest = temp_dirs["base"] / "audiobook_ingest"
        audiobook_ingest.mkdir()
        temp_file = temp_dirs["staging"] / "audiobook.mp3"
        temp_file.write_bytes(b"audio")

        sample_task.content_type = "audiobook"

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = None
            _sync_core_config(mock_config, mock_config)
            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "DESTINATION": str(temp_dirs["ingest"]),
                "INGEST_DIR": str(temp_dirs["ingest"]),
                "DESTINATION_AUDIOBOOK": str(audiobook_ingest),
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        result_path = Path(result)
        assert result_path.parent == audiobook_ingest


# =============================================================================
# Custom Script Execution Tests
# =============================================================================

class TestCustomScriptExecution:
    """Tests for custom script execution in post-processing."""

    def test_runs_custom_script(self, temp_dirs, sample_direct_task):
        """Runs custom script when configured."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        import subprocess

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]), \
             patch('subprocess.run') as mock_run:

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(temp_dirs["ingest"])
            _sync_core_config(mock_config, mock_config)

            mock_run.return_value = MagicMock(stdout="", returncode=0)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        result_path = Path(result)
        assert call_args[0][0] == ["/path/to/script.sh", str(result_path)]

    def test_runs_custom_script_with_json_payload_on_stdin(self, temp_dirs, sample_direct_task):
        """Sends a JSON payload to the custom script via stdin when enabled."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]), \
             patch('subprocess.run') as mock_run:

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(
                temp_dirs["ingest"],
                {"CUSTOM_SCRIPT_JSON_PAYLOAD": True},
            )
            _sync_core_config(mock_config, mock_config)

            mock_run.return_value = MagicMock(stdout="", returncode=0)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        result_path = Path(result)

        payload_json = mock_run.call_args.kwargs.get("input")
        assert payload_json
        payload = json.loads(payload_json)
        assert payload["version"] == 1
        assert payload["phase"] == "post_transfer"
        assert payload["task"]["task_id"] == sample_direct_task.task_id
        assert payload["paths"]["destination"] == str(temp_dirs["ingest"])
        assert payload["paths"]["target"] == str(result_path)
        assert payload["paths"]["final_paths"] == [str(result_path)]

    def test_runs_custom_script_for_booklore_output_with_json_payload(self, temp_dirs, sample_direct_task):
        """Runs the custom script hook after a successful Booklore upload."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        sample_direct_task.task_id = "direct-booklore"

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]), \
             patch('shelfmark.download.outputs.booklore.booklore_login', return_value="token"), \
             patch('shelfmark.download.outputs.booklore.booklore_upload_file'), \
             patch('shelfmark.download.outputs.booklore.booklore_refresh_library'), \
             patch('subprocess.run') as mock_run:

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            _sync_core_config(mock_config, mock_config)

            mock_config.get = MagicMock(side_effect=lambda key, default=None: {
                "BOOKS_OUTPUT_MODE": "booklore",
                "BOOKLORE_HOST": "http://booklore:6060",
                "BOOKLORE_USERNAME": "user",
                "BOOKLORE_PASSWORD": "pass",
                "BOOKLORE_LIBRARY_ID": 1,
                "BOOKLORE_PATH_ID": 2,
                "CUSTOM_SCRIPT_JSON_PAYLOAD": True,
            }.get(key, default))
            _sync_core_config(mock_config, mock_config)

            mock_run.return_value = MagicMock(stdout="", returncode=0)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result == "booklore://direct-booklore"

        payload_json = mock_run.call_args.kwargs.get("input")
        assert payload_json
        payload = json.loads(payload_json)
        assert payload["version"] == 1
        assert payload["phase"] == "post_upload"
        assert payload["output"]["mode"] == "booklore"
        assert payload["output"]["details"]["booklore"]["library_id"] == 1
        assert payload["output"]["details"]["booklore"]["path_id"] == 2
        assert payload["paths"]["target"].endswith("/book.epub")

    def test_runs_custom_script_with_relative_path_mode(self, temp_dirs, sample_direct_task):
        """Runs custom script with a destination-relative path when configured."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        import subprocess

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]), \
             patch('subprocess.run') as mock_run:

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(
                temp_dirs["ingest"],
                {"CUSTOM_SCRIPT_PATH_MODE": "relative"},
            )
            _sync_core_config(mock_config, mock_config)

            mock_run.return_value = MagicMock(stdout="", returncode=0)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        result_path = Path(result)
        expected_relative = result_path.relative_to(temp_dirs["ingest"])
        script_args = mock_run.call_args[0][0]
        assert script_args[0] == "/path/to/script.sh"
        assert script_args[1] == str(expected_relative)
        assert not Path(script_args[1]).is_absolute()

    def test_runs_custom_script_for_directory_download_once(self, temp_dirs):
        """Runs custom script once after transferring a directory download."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        download_dir = temp_dirs["staging"] / "release"
        download_dir.mkdir()
        (download_dir / "01.mp3").write_bytes(b"a")
        (download_dir / "02.mp3").write_bytes(b"b")

        task = DownloadTask(
            task_id="test-dir",
            source="direct_download",
            title="My Book",
            author="An Author",
            format="mp3",
            search_mode=SearchMode.DIRECT,
            content_type="audiobook",
        )

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]), \
             patch('subprocess.run') as mock_run:

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(temp_dirs["ingest"], {"FILE_ORGANIZATION_AUDIOBOOK": "none"})
            _sync_core_config(mock_config, mock_config)

            mock_run.return_value = MagicMock(stdout="", returncode=0)

            result = _post_process_download(
                temp_file=download_dir,
                task=task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is not None
        assert mock_run.call_count == 1
        script_args = mock_run.call_args[0][0]
        assert script_args[0] == "/path/to/script.sh"
        assert Path(script_args[1]) == temp_dirs["ingest"]

    def test_script_not_found_error(self, temp_dirs, sample_direct_task):
        """Returns error when script not found."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]), \
             patch('subprocess.run', side_effect=FileNotFoundError("not found")):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/nonexistent/script.sh"
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(temp_dirs["ingest"])
            _sync_core_config(mock_config, mock_config)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is None
        status_cb.assert_called_with("error", "Custom script not found: /nonexistent/script.sh")

    def test_script_not_executable_error(self, temp_dirs, sample_direct_task):
        """Returns error when script not executable."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]), \
             patch('subprocess.run', side_effect=PermissionError("not executable")):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(temp_dirs["ingest"])
            _sync_core_config(mock_config, mock_config)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is None
        status_cb.assert_called_with("error", "Custom script not executable: /path/to/script.sh")

    def test_script_timeout_error(self, temp_dirs, sample_direct_task):
        """Returns error when script times out."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        import subprocess

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]), \
             patch('subprocess.run', side_effect=subprocess.TimeoutExpired("script", 300)):

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(temp_dirs["ingest"])
            _sync_core_config(mock_config, mock_config)

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is None
        status_cb.assert_called_with("error", "Custom script timed out")

    def test_script_nonzero_exit_error(self, temp_dirs, sample_direct_task):
        """Returns error when script exits non-zero."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        import subprocess

        temp_file = temp_dirs["staging"] / "book.epub"
        temp_file.write_bytes(b"content")

        status_cb = MagicMock()
        cancel_flag = Event()

        with patch('shelfmark.core.config.config') as mock_config, \
             patch('shelfmark.config.env.TMP_DIR', temp_dirs["staging"]), \
             patch('subprocess.run') as mock_run:

            mock_config.USE_BOOK_TITLE = False
            mock_config.CUSTOM_SCRIPT = "/path/to/script.sh"
            _sync_core_config(mock_config, mock_config)
            mock_config.get = _mock_destination_config(temp_dirs["ingest"])
            _sync_core_config(mock_config, mock_config)

            error = subprocess.CalledProcessError(1, "script", stderr="Something failed")
            mock_run.side_effect = error

            result = _post_process_download(
                temp_file=temp_file,
                task=sample_direct_task,
                cancel_flag=cancel_flag,
                status_callback=status_cb,
            )

        assert result is None
        status_cb.assert_called_with("error", "Custom script failed: Something failed")


# Integration-style end-to-end processing scenarios live in
# `tests/core/test_processing_integration.py`.
