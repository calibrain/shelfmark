"""Tests for hardlinking and staging functionality.

Two approaches to preserve torrent files for seeding:

1. **Ingest mode**: Copy to staging → Move to ingest
   - Uses `stage_file(copy=True)` to preserve original
   - Less efficient (creates temp copy)

2. **Library mode with hardlink**: Hardlink from torrent path → library
   - Uses `_atomic_hardlink()` - same inode, no extra disk space
   - More efficient but requires same filesystem
"""

import os
import pytest
import shutil
import tempfile
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

from shelfmark.core.naming import same_filesystem


def _run_organize_post_process(
    temp_file: Path,
    task,
    library: Path,
    hardlink_enabled: bool = True,
    same_fs: bool = True,
):
    from shelfmark.download.postprocess.router import post_process_download as _post_process_download

    status_cb = MagicMock()
    cancel_flag = Event()

    with patch('shelfmark.core.config.config') as mock_config, \
         patch('shelfmark.download.postprocess.transfer.same_filesystem', return_value=same_fs):

        mock_config.CUSTOM_SCRIPT = None
        mock_config.get = MagicMock(side_effect=lambda key, default=None, **_kwargs: {
            "DESTINATION": str(library),
            "FILE_ORGANIZATION": "organize",
            "HARDLINK_TORRENTS": hardlink_enabled,
            "HARDLINK_TORRENTS_AUDIOBOOK": hardlink_enabled,
            "SUPPORTED_FORMATS": ["epub", "mp3"],
        }.get(key, default))


        result = _post_process_download(
            temp_file=temp_file,
            task=task,
            cancel_flag=cancel_flag,
            status_callback=status_cb,
        )

    return result, status_cb


class TestStageFile:
    """Tests for stage_file() - the ingest mode approach for torrents."""

    def test_copy_mode_preserves_original(self, tmp_path):
        """copy=True preserves original file (for torrent seeding)."""
        from shelfmark.download.staging import stage_file, get_staging_dir

        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"content")

        with patch('shelfmark.config.env.TMP_DIR', tmp_path / "staging"):
            staged = stage_file(source, "task123", copy=True)

        assert staged.exists()
        assert source.exists()  # Original preserved
        assert staged.read_bytes() == b"content"

    def test_move_mode_removes_original(self, tmp_path):
        """copy=False moves file (original deleted)."""
        from shelfmark.download.staging import stage_file

        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"content")

        with patch('shelfmark.config.env.TMP_DIR', tmp_path / "staging"):
            staged = stage_file(source, "task123", copy=False)

        assert staged.exists()
        assert not source.exists()  # Original deleted

    def test_handles_filename_collision(self, tmp_path):
        """Adds counter suffix on collision."""
        from shelfmark.download.staging import stage_file

        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "book.epub").touch()  # Pre-existing file

        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"new content")

        with patch('shelfmark.config.env.TMP_DIR', staging):
            staged = stage_file(source, "task123", copy=True)

        assert staged.name == "book_1.epub"


class TestSameFilesystem:
    """Tests for same_filesystem() detection."""

    def test_same_directory(self, tmp_path):
        """Two paths in same temp directory are on same filesystem."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.touch()
        file2.touch()

        assert same_filesystem(file1, file2) is True

    def test_same_filesystem_different_dirs(self, tmp_path):
        """Subdirectories of same temp are on same filesystem."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        assert same_filesystem(dir1, dir2) is True

    def test_nonexistent_paths_same_parent(self, tmp_path):
        """Non-existent paths with same parent are on same filesystem."""
        path1 = tmp_path / "nonexistent1"
        path2 = tmp_path / "nonexistent2"

        assert same_filesystem(path1, path2) is True

    def test_nonexistent_nested_paths(self, tmp_path):
        """Deeply nested non-existent paths check parent filesystem."""
        path1 = tmp_path / "a" / "b" / "c" / "file.txt"
        path2 = tmp_path / "x" / "y" / "z" / "file.txt"

        assert same_filesystem(path1, path2) is True

    def test_string_paths(self, tmp_path):
        """Accepts string paths as well as Path objects."""
        file1 = tmp_path / "file1.txt"
        file1.touch()

        assert same_filesystem(str(file1), str(tmp_path)) is True

    def test_permission_error_returns_false(self, tmp_path):
        """Returns False when permission denied (safe fallback)."""
        with patch('os.stat', side_effect=PermissionError("denied")):
            assert same_filesystem(tmp_path, tmp_path) is False

    def test_oserror_returns_false(self, tmp_path):
        """Returns False on OS errors (safe fallback)."""
        with patch('os.stat', side_effect=OSError("error")):
            assert same_filesystem(tmp_path, tmp_path) is False


class TestAtomicHardlink:
    """Tests for _atomic_hardlink() function."""

    def test_creates_hardlink(self, tmp_path):
        """Creates hardlink to source file."""
        from shelfmark.download.fs import atomic_hardlink as _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        result = _atomic_hardlink(source, dest)

        assert result == dest
        assert result.exists()
        assert result.read_text() == "content"
        # Verify it's a hardlink (same inode)
        assert os.stat(source).st_ino == os.stat(result).st_ino

    def test_handles_collision_with_counter(self, tmp_path):
        """Appends counter suffix when destination exists."""
        from shelfmark.download.fs import atomic_hardlink as _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("new content")
        dest = tmp_path / "dest.txt"
        dest.write_text("existing")

        result = _atomic_hardlink(source, dest)

        assert result == tmp_path / "dest_1.txt"
        assert result.read_text() == "new content"
        assert dest.read_text() == "existing"

    def test_multiple_collisions(self, tmp_path):
        """Increments counter until finding free slot."""
        from shelfmark.download.fs import atomic_hardlink as _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("new")
        (tmp_path / "dest.txt").touch()
        (tmp_path / "dest_1.txt").touch()
        (tmp_path / "dest_2.txt").touch()

        result = _atomic_hardlink(source, tmp_path / "dest.txt")

        assert result == tmp_path / "dest_3.txt"

    def test_preserves_extension(self, tmp_path):
        """Keeps extension when adding counter suffix."""
        from shelfmark.download.fs import atomic_hardlink as _atomic_hardlink

        source = tmp_path / "book.epub"
        source.write_bytes(b"epub content")
        (tmp_path / "book.epub").touch()

        result = _atomic_hardlink(source, tmp_path / "book.epub")

        assert result.suffix == ".epub"
        assert result.name == "book_1.epub"

    def test_falls_back_to_copy_on_permission_error(self, tmp_path, monkeypatch):
        """Falls back to copy when hardlink is not permitted."""
        from shelfmark.download.fs import atomic_hardlink as _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        original_link = os.link

        def _raise_only_for_initial_link(src, dst, *_args, **_kwargs):
            # Force hardlink -> copy fallback while allowing atomic_copy publish step.
            if Path(src) == source:
                raise PermissionError("hardlink not permitted")
            return original_link(src, dst)

        monkeypatch.setattr(os, "link", _raise_only_for_initial_link)

        result = _atomic_hardlink(source, dest)

        assert result == dest
        assert result.read_text() == "content"
        assert source.exists()
        assert os.stat(source).st_ino != os.stat(result).st_ino


class TestAtomicMove:
    """Tests for _atomic_move() function."""

    def test_moves_file(self, tmp_path):
        """Moves file from source to destination."""
        from shelfmark.download.fs import atomic_move as _atomic_move

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        result = _atomic_move(source, dest)

        assert result == dest
        assert not source.exists()
        assert result.read_text() == "content"

    def test_handles_collision(self, tmp_path):
        """Appends counter on collision."""
        from shelfmark.download.fs import atomic_move as _atomic_move

        source = tmp_path / "source.txt"
        source.write_text("new")
        dest = tmp_path / "dest.txt"
        dest.write_text("existing")

        result = _atomic_move(source, dest)

        assert result == tmp_path / "dest_1.txt"
        assert not source.exists()
        assert dest.read_text() == "existing"
        assert result.read_text() == "new"

    def test_false_positive_exists_probe(self, tmp_path, monkeypatch):
        """Moves file even if exists() falsely reports a collision."""
        from shelfmark.download.fs import atomic_move as _atomic_move

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        original_exists = Path.exists

        def _fake_exists(self):
            if self == dest:
                return True
            return original_exists(self)

        monkeypatch.setattr(Path, "exists", _fake_exists)

        result = _atomic_move(source, dest)

        assert result == dest
        assert not source.exists()
        assert result.read_text() == "content"

    def test_cross_filesystem_fallback(self):
        """Falls back to copy when cross-filesystem."""
        from shelfmark.download.fs import atomic_move as _atomic_move
        import errno

        with tempfile.TemporaryDirectory() as dir1, tempfile.TemporaryDirectory() as dir2:
            source = Path(dir1) / "source.txt"
            source.write_text("content")
            dest = Path(dir2) / "dest.txt"

            # This should work even if dirs are on different filesystems
            # (uses fallback to copy)
            result = _atomic_move(source, dest)

            assert not source.exists()
            assert result.read_text() == "content"

    def test_cross_filesystem_permission_fallback(self, tmp_path, monkeypatch):
        """Falls back to copy when cross-filesystem move hits permission error."""
        from shelfmark.download.fs import atomic_move as _atomic_move
        import errno

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        def _raise_exdev(*_args, **_kwargs):
            raise OSError(errno.EXDEV, "Cross-device link")

        def _fallback_copy(src, dst, is_move):
            shutil.copyfile(str(src), str(dst))
            if is_move:
                Path(src).unlink()

        monkeypatch.setattr(os, "rename", _raise_exdev)

        with patch("shelfmark.download.fs.shutil.copy2", side_effect=PermissionError("no")) as mock_copy, \
             patch("shelfmark.download.fs._perform_nfs_fallback", side_effect=_fallback_copy) as mock_fallback:
            result = _atomic_move(source, dest)

        assert result == dest
        assert not source.exists()
        assert dest.read_text() == "content"
        assert mock_copy.called
        assert mock_fallback.called

    def test_cross_filesystem_move_recovers_when_metadata_step_hits_enoent(self, tmp_path, monkeypatch):
        """Completes EXDEV fallback move when copy2 metadata fails with ENOENT."""
        from shelfmark.download.fs import atomic_move as _atomic_move
        import errno

        source = tmp_path / "source.txt"
        source.write_text("content")
        dest = tmp_path / "dest.txt"

        def _raise_exdev(*_args, **_kwargs):
            raise OSError(errno.EXDEV, "Cross-device link")

        real_copyfile = shutil.copyfile

        def _copy_then_enoent(src, dst):
            real_copyfile(src, dst)
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", src)

        monkeypatch.setattr(os, "rename", _raise_exdev)

        with patch("shelfmark.download.fs.shutil.copy2", side_effect=_copy_then_enoent):
            result = _atomic_move(source, dest)

        assert result == dest
        assert not source.exists()
        assert dest.exists()
        assert dest.read_text() == "content"


class TestHardlinkWithLibraryMode:
    """Tests for hardlinking in library mode context."""

    @pytest.fixture
    def mock_config(self):
        """Mock config for library mode."""
        with patch('shelfmark.core.config.config') as mock:
            mock.get = MagicMock(side_effect=lambda key, default=None, **_kwargs: {
                "LIBRARY_PATH": None,
                "LIBRARY_PATH_AUDIOBOOK": None,
                "LIBRARY_TEMPLATE": "{Author}/{Title}",
                "LIBRARY_TEMPLATE_AUDIOBOOK": "{Author}/{Title}",
                "TORRENT_HARDLINK": True,
                "PROCESSING_MODE": "library",
                "PROCESSING_MODE_AUDIOBOOK": "library",
            }.get(key, default))
            yield mock

    @pytest.fixture
    def sample_task(self):
        """Create a sample DownloadTask for testing."""
        from shelfmark.core.models import DownloadTask, SearchMode

        return DownloadTask(
            task_id="test123",
            source="prowlarr",
            title="The Way of Kings",
            author="Brandon Sanderson",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

    def test_transfer_file_hardlink(self, tmp_path, sample_task):
        """Single file transferred via hardlink."""
        from shelfmark.download.postprocess.pipeline import transfer_file_to_library

        library = tmp_path / "library"
        library.mkdir()
        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"epub content")
        temp_file = tmp_path / "staging" / "book.epub"
        temp_file.parent.mkdir()
        temp_file.write_bytes(b"staged content")

        status_cb = MagicMock()

        with patch('shelfmark.config.env.TMP_DIR', temp_file.parent):
            result = transfer_file_to_library(
                source_path=source,
                library_base=str(library),
                template="{Author}/{Title}",
                metadata={"Author": "Brandon Sanderson", "Title": "Mistborn"},
                task=sample_task,
                temp_file=temp_file,
                status_callback=status_cb,
                use_hardlink=True,
            )

        assert result is not None
        result_path = Path(result)
        assert result_path.exists()
        assert result_path.parent.name == "Brandon Sanderson"
        assert result_path.name == "Mistborn.epub"
        # Source should still exist (hardlink)
        assert source.exists()
        # Temp file should be cleaned up
        assert not temp_file.exists()
        status_cb.assert_called_with("complete", "Complete")

    def test_transfer_file_move(self, tmp_path, sample_task):
        """Single file transferred via move."""
        from shelfmark.download.postprocess.pipeline import transfer_file_to_library

        library = tmp_path / "library"
        library.mkdir()
        source = tmp_path / "staging" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"epub content")

        status_cb = MagicMock()

        result = transfer_file_to_library(
            source_path=source,
            library_base=str(library),
            template="{Author}/{Title}",
            metadata={"Author": "Brandon Sanderson", "Title": "Mistborn"},
            task=sample_task,
            temp_file=source,
            status_callback=status_cb,
            use_hardlink=False,
        )

        assert result is not None
        result_path = Path(result)
        assert result_path.exists()
        # Source should NOT exist (moved)
        assert not source.exists()
        status_cb.assert_called_with("complete", "Complete")

    def test_transfer_directory_hardlink_multifile(self, tmp_path, sample_task):
        """Directory with multiple files transferred via hardlinks."""
        from shelfmark.download.postprocess.pipeline import transfer_directory_to_library

        library = tmp_path / "library"
        library.mkdir()
        source_dir = tmp_path / "downloads" / "audiobook"
        source_dir.mkdir(parents=True)

        # Create source audio files
        (source_dir / "Part 1.mp3").write_bytes(b"audio1")
        (source_dir / "Part 2.mp3").write_bytes(b"audio2")
        (source_dir / "Part 10.mp3").write_bytes(b"audio10")

        # Create temp staging dir
        temp_dir = tmp_path / "staging" / "audiobook"
        temp_dir.mkdir(parents=True)
        (temp_dir / "Part 1.mp3").write_bytes(b"staged1")
        (temp_dir / "Part 2.mp3").write_bytes(b"staged2")
        (temp_dir / "Part 10.mp3").write_bytes(b"staged10")

        sample_task.content_type = "audiobook"
        status_cb = MagicMock()

        with patch('shelfmark.download.postprocess.scan.get_supported_formats', return_value=["mp3"]), \
             patch('shelfmark.config.env.TMP_DIR', temp_dir.parent):
            result = transfer_directory_to_library(
                source_dir=source_dir,
                library_base=str(library),
                template="{Author}/{Title}{ - PartNumber}",  # Correct token format
                metadata={"Author": "Brandon Sanderson", "Title": "The Way of Kings"},
                task=sample_task,
                temp_file=temp_dir,
                status_callback=status_cb,
                use_hardlink=True,
            )

        assert result is not None
        result_path = Path(result)
        assert result_path.parent.name == "Brandon Sanderson"

        # Check all 3 files created with sequential part numbers
        author_dir = library / "Brandon Sanderson"
        files = sorted(author_dir.glob("*.mp3"))
        assert len(files) == 3
        assert files[0].name == "The Way of Kings - 01.mp3"
        assert files[1].name == "The Way of Kings - 02.mp3"
        assert files[2].name == "The Way of Kings - 03.mp3"

        # Source files should still exist (hardlinks)
        assert (source_dir / "Part 1.mp3").exists()
        assert (source_dir / "Part 2.mp3").exists()
        assert (source_dir / "Part 10.mp3").exists()

        # Temp dir should be cleaned up
        assert not temp_dir.exists()

    def test_transfer_directory_move(self, tmp_path, sample_task):
        """Directory transferred via move (non-torrent)."""
        from shelfmark.download.postprocess.pipeline import transfer_directory_to_library

        library = tmp_path / "library"
        library.mkdir()
        source_dir = tmp_path / "staging" / "audiobook"
        source_dir.mkdir(parents=True)

        (source_dir / "Chapter 01.mp3").write_bytes(b"audio1")
        (source_dir / "Chapter 02.mp3").write_bytes(b"audio2")

        sample_task.content_type = "audiobook"
        status_cb = MagicMock()

        with patch('shelfmark.download.postprocess.scan.get_supported_formats', return_value=["mp3"]), \
             patch('shelfmark.config.env.TMP_DIR', source_dir.parent):
            result = transfer_directory_to_library(
                source_dir=source_dir,
                library_base=str(library),
                template="{Author}/{Title}{ - Part PartNumber}",
                metadata={"Author": "Brandon Sanderson", "Title": "The Way of Kings"},
                task=sample_task,
                temp_file=source_dir,
                status_callback=status_cb,
                use_hardlink=False,
            )

        assert result is not None
        author_dir = library / "Brandon Sanderson"
        files = list(author_dir.glob("*.mp3"))
        assert len(files) == 2

        # Source dir should be cleaned up
        assert not source_dir.exists()

    def test_single_file_in_directory_no_part_number(self, tmp_path, sample_task):
        """Single file in directory doesn't get part number."""
        from shelfmark.download.postprocess.pipeline import transfer_directory_to_library

        library = tmp_path / "library"
        library.mkdir()
        source_dir = tmp_path / "downloads" / "book"
        source_dir.mkdir(parents=True)
        (source_dir / "book.epub").write_bytes(b"content")

        temp_dir = tmp_path / "staging" / "book"
        temp_dir.mkdir(parents=True)
        (temp_dir / "book.epub").write_bytes(b"staged")

        status_cb = MagicMock()

        with patch('shelfmark.download.postprocess.scan.get_supported_formats', return_value=["epub"]):
            result = transfer_directory_to_library(
                source_dir=source_dir,
                library_base=str(library),
                template="{Author}/{Title}{ - PartNumber}",  # Correct token format
                metadata={"Author": "Brandon Sanderson", "Title": "Mistborn"},
                task=sample_task,
                temp_file=temp_dir,
                status_callback=status_cb,
                use_hardlink=True,
            )

        result_path = Path(result)
        # Single file should NOT have part number (conditional prefix stripped)
        assert result_path.name == "Mistborn.epub"


class TestHardlinkDecisionLogic:
    """Tests for the decision to use hardlinks vs moves."""

    @pytest.fixture
    def sample_task(self):
        from shelfmark.core.models import DownloadTask, SearchMode

        return DownloadTask(
            task_id="test123",
            source="prowlarr",
            title="Test Book",
            author="Test Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

    def test_hardlink_enabled_same_filesystem(self, tmp_path, sample_task):
        """Hardlink used when enabled and same filesystem."""
        library = tmp_path / "library"
        library.mkdir()
        staging = tmp_path / "staging"
        staging.mkdir()
        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"content")
        staged = staging / "book.epub"
        staged.write_bytes(b"staged")

        # Task has original_download_path (torrent scenario)
        sample_task.original_download_path = str(source)

        status_cb = MagicMock()

        result, _ = _run_organize_post_process(
            temp_file=staged,
            task=sample_task,
            library=library,
            hardlink_enabled=True,
            same_fs=True,
        )

        assert result is not None
        # Source should still exist (hardlinked)
        assert source.exists()

    def test_hardlink_disabled_falls_back_to_move(self, tmp_path, sample_task):
        """Move used when hardlink disabled in config."""
        library = tmp_path / "library"
        library.mkdir()
        source = tmp_path / "downloads" / "book.epub"
        source.parent.mkdir()
        source.write_bytes(b"content")
        staged = tmp_path / "staging" / "book.epub"
        staged.parent.mkdir()
        staged.write_bytes(b"staged")

        sample_task.original_download_path = str(source)

        status_cb = MagicMock()

        result, _ = _run_organize_post_process(
            temp_file=staged,
            task=sample_task,
            library=library,
            hardlink_enabled=False,
        )

        assert result is not None
        # Staged file should be moved (not exist)
        assert not staged.exists()

    def test_no_original_path_uses_staging(self, tmp_path, sample_task):
        """Non-prowlarr downloads move staged files into destination."""
        library = tmp_path / "library"
        library.mkdir()
        staged = tmp_path / "staging" / "book.epub"
        staged.parent.mkdir()
        staged.write_bytes(b"content")

        # Simulate a non-external download (e.g. direct download) where Shelfmark owns the
        # temp file in TMP_DIR and can safely move it.
        sample_task.source = "direct_download"
        sample_task.original_download_path = None

        result, _ = _run_organize_post_process(
            temp_file=staged,
            task=sample_task,
            library=library,
            hardlink_enabled=True,
        )

        assert result is not None
        assert not staged.exists()


class TestHardlinkInodeVerification:
    """Tests that verify hardlinks share the same inode."""

    def test_hardlink_shares_inode(self, tmp_path):
        """Hardlinked files share same inode."""
        from shelfmark.download.fs import atomic_hardlink as _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("shared content")
        dest = tmp_path / "dest.txt"

        result = _atomic_hardlink(source, dest)

        source_inode = os.stat(source).st_ino
        dest_inode = os.stat(result).st_ino
        assert source_inode == dest_inode

    def test_hardlink_reflects_changes(self, tmp_path):
        """Changes to source reflect in hardlink."""
        from shelfmark.download.fs import atomic_hardlink as _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("original")
        dest = tmp_path / "dest.txt"

        result = _atomic_hardlink(source, dest)

        # Modify source
        source.write_text("modified")

        # Hardlink should see the change
        assert result.read_text() == "modified"

    def test_hardlink_count_increases(self, tmp_path):
        """Link count increases with each hardlink."""
        from shelfmark.download.fs import atomic_hardlink as _atomic_hardlink

        source = tmp_path / "source.txt"
        source.write_text("content")

        # Initial link count is 1
        assert os.stat(source).st_nlink == 1

        dest1 = _atomic_hardlink(source, tmp_path / "link1.txt")
        assert os.stat(source).st_nlink == 2

        dest2 = _atomic_hardlink(source, tmp_path / "link2.txt")
        assert os.stat(source).st_nlink == 3


class TestTorrentOptimization:
    """Tests for optimized torrent handling - skip staging when possible."""

    @pytest.fixture
    def sample_task(self):
        from shelfmark.core.models import DownloadTask, SearchMode

        return DownloadTask(
            task_id="test123",
            source="prowlarr",
            title="Test Book",
            author="Test Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

    def testis_torrent_source_true(self, tmp_path, sample_task):
        """Detects when source is the torrent client path."""
        from shelfmark.download.postprocess.pipeline import is_torrent_source

        torrent_path = tmp_path / "downloads" / "book.epub"
        torrent_path.parent.mkdir()
        torrent_path.touch()
        sample_task.original_download_path = str(torrent_path)

        assert is_torrent_source(torrent_path, sample_task) is True

    def testis_torrent_source_false_no_original(self, tmp_path, sample_task):
        """Returns False when no original_download_path set."""
        from shelfmark.download.postprocess.pipeline import is_torrent_source

        some_path = tmp_path / "staging" / "book.epub"
        sample_task.original_download_path = None

        assert is_torrent_source(some_path, sample_task) is False

    def testis_torrent_source_false_different_path(self, tmp_path, sample_task):
        """Returns False when paths don't match."""
        from shelfmark.download.postprocess.pipeline import is_torrent_source

        torrent_path = tmp_path / "downloads" / "book.epub"
        staging_path = tmp_path / "staging" / "book.epub"
        sample_task.original_download_path = str(torrent_path)

        assert is_torrent_source(staging_path, sample_task) is False

    def test_library_mode_torrent_no_hardlink_copies(self, tmp_path, sample_task):
        """Library mode copies (not moves) torrent files when hardlink unavailable."""
        from shelfmark.download.postprocess.pipeline import transfer_file_to_library

        library = tmp_path / "library"
        library.mkdir()
        torrent_path = tmp_path / "downloads" / "book.epub"
        torrent_path.parent.mkdir()
        torrent_path.write_bytes(b"content")

        # Set up as torrent source
        sample_task.original_download_path = str(torrent_path)

        status_cb = MagicMock()

        result = transfer_file_to_library(
            source_path=torrent_path,
            library_base=str(library),
            template="{Author}/{Title}",
            metadata={"Author": "Test Author", "Title": "Test Book"},
            task=sample_task,
            temp_file=torrent_path,
            status_callback=status_cb,
            use_hardlink=False,  # No hardlink
        )

        assert result is not None
        assert Path(result).exists()
        # Original should still exist (copied, not moved)
        assert torrent_path.exists()

    def test_library_mode_non_torrent_moves(self, tmp_path, sample_task):
        """Library mode moves (not copies) non-torrent files."""
        from shelfmark.download.postprocess.pipeline import transfer_file_to_library

        library = tmp_path / "library"
        library.mkdir()
        staging_path = tmp_path / "staging" / "book.epub"
        staging_path.parent.mkdir()
        staging_path.write_bytes(b"content")

        # No original_download_path = not a torrent
        sample_task.original_download_path = None

        status_cb = MagicMock()

        result = transfer_file_to_library(
            source_path=staging_path,
            library_base=str(library),
            template="{Author}/{Title}",
            metadata={"Author": "Test Author", "Title": "Test Book"},
            task=sample_task,
            temp_file=staging_path,
            status_callback=status_cb,
            use_hardlink=False,
        )

        assert result is not None
        assert Path(result).exists()
        # Original should be gone (moved)
        assert not staging_path.exists()

    def test_directory_torrent_copies_all_files(self, tmp_path, sample_task):
        """Multi-file torrent directory copies all files to library."""
        from shelfmark.download.postprocess.pipeline import transfer_directory_to_library

        library = tmp_path / "library"
        library.mkdir()
        torrent_dir = tmp_path / "downloads" / "audiobook"
        torrent_dir.mkdir(parents=True)
        (torrent_dir / "part1.mp3").write_bytes(b"audio1")
        (torrent_dir / "part2.mp3").write_bytes(b"audio2")

        sample_task.original_download_path = str(torrent_dir)
        sample_task.content_type = "audiobook"
        status_cb = MagicMock()

        with patch('shelfmark.download.postprocess.scan.get_supported_formats', return_value=["mp3"]):
            result = transfer_directory_to_library(
                source_dir=torrent_dir,
                library_base=str(library),
                template="{Author}/{Title}{ - PartNumber}",
                metadata={"Author": "Test Author", "Title": "Test Book"},
                task=sample_task,
                temp_file=torrent_dir,
                status_callback=status_cb,
                use_hardlink=False,
            )

        assert result is not None
        # Original files should still exist
        assert (torrent_dir / "part1.mp3").exists()
        assert (torrent_dir / "part2.mp3").exists()
        # Library files should exist
        author_dir = library / "Test Author"
        assert len(list(author_dir.glob("*.mp3"))) == 2


class TestTorrentSourceCleanupProtection:
    """Integration tests simulating real torrent download flows.

    These tests simulate actual production scenarios where:
    - Torrent client (qBittorrent/Transmission) downloads to /downloads/complete/
    - Prowlarr handler returns that path directly (no staging for torrents)
    - Orchestrator processes via _post_process_download()
    - Source files must remain intact for seeding

    Each test simulates a specific real-world content type and file structure.
    """

    def _make_config_mock(self, library_path: str, hardlink: bool = True):
        """Create config mock for library/organize mode with hardlinking."""
        return MagicMock(side_effect=lambda key, default=None, **_kwargs: {
            # Destination paths (what _get_final_destination uses)
            "DESTINATION": library_path,
            "DESTINATION_AUDIOBOOK": library_path,
            # Templates (what _get_template uses)
            "TEMPLATE_ORGANIZE": "{Author}/{Title}",
            "TEMPLATE_AUDIOBOOK_ORGANIZE": "{Author}/{Title}{ - PartNumber}",
            # File organization mode
            "FILE_ORGANIZATION": "organize",
            "FILE_ORGANIZATION_AUDIOBOOK": "organize",
            # Hardlink toggle
            "HARDLINK_TORRENTS": hardlink,
            "HARDLINK_TORRENTS_AUDIOBOOK": hardlink,
            # Supported formats
            "SUPPORTED_FORMATS": ["epub", "mobi", "cbz", "cbr", "azw3", "fb2", "djvu", "pdf"],
            "SUPPORTED_AUDIOBOOK_FORMATS": ["mp3", "m4a", "m4b", "flac"],
        }.get(key, default))

    # ==================== EPUB EBOOK TESTS ====================

    def test_torrent_epub_single_file_hardlink(self, tmp_path):
        """Torrent: Single .epub ebook - hardlink preserves source for seeding.

        Simulates: User downloads "The Way of Kings.epub" via qBittorrent.
        qBittorrent saves to /downloads/complete/The Way of Kings.epub
        Prowlarr handler returns this path directly.
        Library mode hardlinks to /library/Brandon Sanderson/The Way of Kings.epub
        Original MUST remain for seeding.
        """
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        from shelfmark.core.models import DownloadTask, SearchMode

        # Simulate qBittorrent's download location
        downloads = tmp_path / "downloads" / "complete"
        downloads.mkdir(parents=True)
        torrent_file = downloads / "The Way of Kings.epub"
        torrent_file.write_bytes(b"PK\x03\x04" + b"epub content" * 1000)  # Fake epub

        library = tmp_path / "library"
        library.mkdir()

        # Task as returned by Prowlarr handler (original_download_path = torrent location)
        task = DownloadTask(
            task_id="prowlarr_12345",
            source="prowlarr",
            title="The Way of Kings",
            author="Brandon Sanderson",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
            original_download_path=str(torrent_file),  # Handler sets this for torrents
        )

        status_cb = MagicMock()

        # Patch config used by postprocess pipeline
        with patch('shelfmark.core.config.config') as mock_orch:
            mock_orch.get = self._make_config_mock(str(library), hardlink=True)
            mock_orch.CUSTOM_SCRIPT = None
            result = _post_process_download(torrent_file, task, Event(), status_cb)

        assert result is not None
        result_path = Path(result)
        assert result_path.exists()
        assert result_path.suffix == ".epub"
        assert "Brandon Sanderson" in str(result_path)

        # CRITICAL: Torrent source must exist for seeding
        assert torrent_file.exists(), "Torrent epub was deleted! qBittorrent seeding will fail."

        # Verify hardlink (same inode = no extra disk space)
        assert os.stat(torrent_file).st_ino == os.stat(result_path).st_ino

    def test_torrent_mobi_single_file_hardlink(self, tmp_path):
        """Torrent: Single .mobi ebook - hardlink preserves source.

        Same flow as epub but with .mobi format.
        """
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        from shelfmark.core.models import DownloadTask, SearchMode

        downloads = tmp_path / "downloads" / "complete"
        downloads.mkdir(parents=True)
        torrent_file = downloads / "Dune.mobi"
        torrent_file.write_bytes(b"BOOKMOBI" + b"mobi content" * 1000)

        library = tmp_path / "library"
        library.mkdir()

        task = DownloadTask(
            task_id="prowlarr_67890",
            source="prowlarr",
            title="Dune",
            author="Frank Herbert",
            format="mobi",
            search_mode=SearchMode.UNIVERSAL,
            original_download_path=str(torrent_file),
        )

        status_cb = MagicMock()

        with patch('shelfmark.core.config.config') as mock_orch:
            mock_orch.get = self._make_config_mock(str(library), hardlink=True)
            mock_orch.CUSTOM_SCRIPT = None
            result = _post_process_download(torrent_file, task, Event(), status_cb)

        assert result is not None
        assert torrent_file.exists(), "Torrent mobi was deleted!"
        assert os.stat(torrent_file).st_ino == os.stat(result).st_ino

    # ==================== AUDIOBOOK TESTS ====================

    def test_torrent_audiobook_multifile_hardlink(self, tmp_path):
        """Torrent: Multi-file audiobook - all source files preserved for seeding.

        Simulates: User downloads "Project Hail Mary Audiobook" torrent.
        qBittorrent saves to /downloads/complete/Project Hail Mary/
        Contains: Part 01.mp3, Part 02.mp3, ... Part 12.mp3
        Handler returns directory path.
        Library mode hardlinks all mp3s to /library/Andy Weir/Project Hail Mary - 01.mp3, etc.
        ALL original files must remain for seeding.
        """
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        from shelfmark.core.models import DownloadTask, SearchMode

        # Simulate torrent audiobook structure
        downloads = tmp_path / "downloads" / "complete"
        torrent_dir = downloads / "Project Hail Mary Audiobook"
        torrent_dir.mkdir(parents=True)

        # Create realistic audiobook files
        audio_files = []
        for i in range(1, 13):
            audio_file = torrent_dir / f"Part {i:02d}.mp3"
            audio_file.write_bytes(b"ID3" + f"audio content part {i}".encode() * 500)
            audio_files.append(audio_file)

        # Also include cover art and nfo (should be ignored)
        (torrent_dir / "cover.jpg").write_bytes(b"fake jpg")
        (torrent_dir / "info.nfo").write_text("release info")

        library = tmp_path / "library"
        library.mkdir()

        task = DownloadTask(
            task_id="prowlarr_audiobook_001",
            source="prowlarr",
            title="Project Hail Mary",
            author="Andy Weir",
            format="mp3",
            content_type="audiobook",
            search_mode=SearchMode.UNIVERSAL,
            original_download_path=str(torrent_dir),
        )

        status_cb = MagicMock()

        with patch('shelfmark.core.config.config') as mock_orch:
            mock_orch.get = self._make_config_mock(str(library), hardlink=True)
            mock_orch.CUSTOM_SCRIPT = None
            result = _post_process_download(torrent_dir, task, Event(), status_cb)

        assert result is not None

        # CRITICAL: All torrent source files must exist for seeding
        assert torrent_dir.exists(), "Torrent audiobook directory was deleted!"
        for audio_file in audio_files:
            assert audio_file.exists(), f"Torrent file {audio_file.name} was deleted!"

        # Verify library has all 12 files
        library_files = list((library / "Andy Weir").glob("*.mp3"))
        assert len(library_files) == 12

    # ==================== COMIC/CBZ TESTS ====================

    def test_torrent_cbz_comic_hardlink(self, tmp_path):
        """Torrent: Single .cbz comic - hardlink preserves source.

        Simulates: User downloads comic via torrent.
        """
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        from shelfmark.core.models import DownloadTask, SearchMode

        downloads = tmp_path / "downloads" / "complete"
        downloads.mkdir(parents=True)
        torrent_file = downloads / "Batman 001.cbz"
        torrent_file.write_bytes(b"PK\x03\x04" + b"cbz content" * 500)

        library = tmp_path / "library"
        library.mkdir()

        task = DownloadTask(
            task_id="prowlarr_comic_001",
            source="prowlarr",
            title="Batman 001",
            author="DC Comics",
            format="cbz",
            content_type="comic_book",
            search_mode=SearchMode.UNIVERSAL,
            original_download_path=str(torrent_file),
        )

        status_cb = MagicMock()

        with patch('shelfmark.core.config.config') as mock_orch:
            mock_orch.get = self._make_config_mock(str(library), hardlink=True)
            mock_orch.CUSTOM_SCRIPT = None
            result = _post_process_download(torrent_file, task, Event(), status_cb)

        assert result is not None
        assert torrent_file.exists(), "Torrent cbz was deleted!"

    # ==================== NON-TORRENT TESTS (USENET/DIRECT) ====================

    def test_usenet_epub_no_original_path_copies_file(self, tmp_path):
        """Usenet: files are copied into destination and source is preserved.

        For external usenet downloads, Shelfmark treats the client path as read-only and
        avoids deleting anything itself. Client-side cleanup is handled separately.
        """
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        from shelfmark.core.models import DownloadTask, SearchMode

        downloads = tmp_path / "downloads" / "complete"
        downloads.mkdir(parents=True)
        usenet_file = downloads / "book.epub"
        usenet_file.write_bytes(b"usenet epub content")

        library = tmp_path / "library"
        library.mkdir()

        task = DownloadTask(
            task_id="nzbget_12345",
            source="prowlarr",
            title="Test Book",
            author="Test Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
            original_download_path=None,
        )

        status_cb = MagicMock()

        with patch('shelfmark.core.config.config') as mock_orch:
            mock_orch.get = self._make_config_mock(str(library), hardlink=True)
            mock_orch.CUSTOM_SCRIPT = None
            result = _post_process_download(usenet_file, task, Event(), status_cb)

        assert result is not None
        assert usenet_file.exists(), "Usenet source file should be preserved"
        assert Path(result).exists()

    def test_direct_download_moves_file(self, tmp_path):
        """Direct download (Anna's Archive): File should be MOVED.

        Simulates: User downloads directly from Anna's Archive.
        No torrent client involved, no seeding needed.
        """
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        from shelfmark.core.models import DownloadTask, SearchMode

        staging = tmp_path / "staging"
        staging.mkdir()
        staged_file = staging / "direct_download.epub"
        staged_file.write_bytes(b"direct download content")

        library = tmp_path / "library"
        library.mkdir()

        task = DownloadTask(
            task_id="direct_12345",
            source="annas_archive",
            title="Direct Book",
            author="Direct Author",
            format="epub",
            search_mode=SearchMode.DIRECT,
            original_download_path=None,
        )

        status_cb = MagicMock()

        with patch('shelfmark.core.config.config') as mock_orch:
            mock_orch.get = self._make_config_mock(str(library), hardlink=True)
            mock_orch.CUSTOM_SCRIPT = None
            result = _post_process_download(staged_file, task, Event(), status_cb)

        assert result is not None
        assert not staged_file.exists(), "Direct download should be moved"

    # ==================== HARDLINK DISABLED TESTS ====================

    def test_torrent_with_hardlink_disabled_copies_file(self, tmp_path):
        """Torrent with hardlink disabled: Should COPY (not move) to preserve seeding.

        When user disables hardlinking but downloads via torrent,
        the file must still be preserved for seeding (via copy).
        """
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        from shelfmark.core.models import DownloadTask, SearchMode

        downloads = tmp_path / "downloads" / "complete"
        downloads.mkdir(parents=True)
        torrent_file = downloads / "book.epub"
        torrent_file.write_bytes(b"torrent content")

        library = tmp_path / "library"
        library.mkdir()

        task = DownloadTask(
            task_id="prowlarr_no_hardlink",
            source="prowlarr",
            title="Test Book",
            author="Test Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
            original_download_path=str(torrent_file),
        )

        status_cb = MagicMock()

        with patch('shelfmark.core.config.config') as mock_orch:
            # Hardlink DISABLED
            mock_orch.get = self._make_config_mock(str(library), hardlink=False)
            mock_orch.CUSTOM_SCRIPT = None
            result = _post_process_download(torrent_file, task, Event(), status_cb)

        assert result is not None
        # Even without hardlink, torrent source must be preserved (copied)
        assert torrent_file.exists(), "Torrent source deleted even with hardlink disabled!"

        # Verify NOT a hardlink (different inodes = separate copy)
        assert os.stat(torrent_file).st_ino != os.stat(result).st_ino

    # ==================== EDGE CASE TESTS ====================

    def test_torrent_cross_filesystem_falls_back_to_copy(self, tmp_path):
        """Torrent on different filesystem: Falls back to copy, preserves source.

        When torrent is on different filesystem than library,
        hardlink fails and should fall back to copy (not move).
        """
        from shelfmark.download.postprocess.pipeline import transfer_file_to_library
        from shelfmark.core.models import DownloadTask, SearchMode

        # Simulate by directly calling transfer_file_to_library with use_hardlink=False
        # (this is what happens after same_filesystem check fails)
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        torrent_file = downloads / "book.epub"
        torrent_file.write_bytes(b"content")

        library = tmp_path / "library"
        library.mkdir()

        task = DownloadTask(
            task_id="test",
            source="prowlarr",
            title="Test",
            author="Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
            original_download_path=str(torrent_file),
        )

        status_cb = MagicMock()

        result = transfer_file_to_library(
            source_path=torrent_file,
            library_base=str(library),
            template="{Author}/{Title}",
            metadata={"Author": "Author", "Title": "Test"},
            task=task,
            temp_file=torrent_file,
            status_callback=status_cb,
            use_hardlink=False,  # Simulating cross-filesystem fallback
        )

        assert result is not None
        # Torrent source preserved (copied, not moved)
        assert torrent_file.exists()

    def testis_torrent_source_detection(self, tmp_path):
        """Unit test: is_torrent_source correctly identifies torrent paths."""
        from shelfmark.download.postprocess.pipeline import is_torrent_source
        from shelfmark.core.models import DownloadTask, SearchMode

        torrent_path = tmp_path / "downloads" / "book.epub"
        torrent_path.parent.mkdir()
        torrent_path.touch()

        staging_path = tmp_path / "staging" / "book.epub"
        staging_path.parent.mkdir()
        staging_path.touch()

        task = DownloadTask(
            task_id="test",
            source="prowlarr",
            title="Test",
            author="Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

        # No original path = not a torrent source
        task.original_download_path = None
        assert is_torrent_source(torrent_path, task) is False
        assert is_torrent_source(staging_path, task) is False

        # With original path set
        task.original_download_path = str(torrent_path)
        assert is_torrent_source(torrent_path, task) is True
        assert is_torrent_source(staging_path, task) is False


class TestEdgeCases:
    """Edge cases and error handling."""

    def test_empty_directory_returns_none(self, tmp_path):
        """Empty source directory returns None."""
        from shelfmark.download.postprocess.pipeline import transfer_directory_to_library
        from shelfmark.core.models import DownloadTask, SearchMode

        task = DownloadTask(
            task_id="test",
            source="prowlarr",
            title="Test",
            author="Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

        library = tmp_path / "library"
        library.mkdir()
        source_dir = tmp_path / "empty"
        source_dir.mkdir()

        status_cb = MagicMock()

        with patch('shelfmark.download.postprocess.scan.get_supported_formats', return_value=["epub"]):
            result = transfer_directory_to_library(
                source_dir=source_dir,
                library_base=str(library),
                template="{Title}",
                metadata={"Title": "Test"},
                task=task,
                temp_file=source_dir,
                status_callback=status_cb,
                use_hardlink=False,
            )

        assert result is None

    def test_nonexistent_source_for_hardlink(self, tmp_path):
        """Missing source file prevents hardlink creation."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        from shelfmark.core.models import DownloadTask, SearchMode

        task = DownloadTask(
            task_id="test",
            source="prowlarr",
            title="Test",
            author="Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
            original_download_path=str(tmp_path / "nonexistent.epub"),
        )

        library = tmp_path / "library"
        library.mkdir()
        staged = tmp_path / "staging" / "book.epub"
        staged.parent.mkdir()
        staged.write_bytes(b"content")

        status_cb = MagicMock()

        with patch('shelfmark.core.config.config') as mock_config:
            mock_config.get = MagicMock(side_effect=lambda key, default=None, **_kwargs: {
                "DESTINATION": str(library),
                "TEMPLATE_ORGANIZE": "{Title}",
                "FILE_ORGANIZATION": "organize",
                "HARDLINK_TORRENTS": True,
            }.get(key, default))
            mock_config.CUSTOM_SCRIPT = None

            result = _post_process_download(staged, task, Event(), status_cb)

        # Should fall back to move since original doesn't exist
        assert result is not None
        assert not staged.exists()

    def test_permission_denied_library_path(self, tmp_path):
        """Handles permission denied on library path."""
        from shelfmark.download.postprocess.router import post_process_download as _post_process_download
        from shelfmark.core.models import DownloadTask, SearchMode

        task = DownloadTask(
            task_id="test",
            source="prowlarr",
            title="Test",
            author="Author",
            format="epub",
            search_mode=SearchMode.UNIVERSAL,
        )

        staged = tmp_path / "staging" / "book.epub"
        staged.parent.mkdir()
        staged.write_bytes(b"content")

        status_cb = MagicMock()

        with patch('shelfmark.core.config.config') as mock_config:
            mock_config.get = MagicMock(side_effect=lambda key, default=None, **_kwargs: {
                "DESTINATION": "/nonexistent/protected/path",
                "TEMPLATE_ORGANIZE": "{Title}",
                "FILE_ORGANIZATION": "organize",
            }.get(key, default))

            result = _post_process_download(staged, task, Event(), status_cb)

        # Should return None (fall back to ingest)
        assert result is None
