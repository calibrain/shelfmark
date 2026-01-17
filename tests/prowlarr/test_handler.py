"""
Unit tests for the Prowlarr download handler.

These tests mock the download clients to test the handler logic
without requiring running services.
"""

import os
import tempfile
from pathlib import Path
from threading import Event
from typing import List, Optional, Tuple
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler
from shelfmark.release_sources.prowlarr.utils import get_protocol
from shelfmark.release_sources.prowlarr.clients import (
    DownloadStatus,
    DownloadState,
)


class ProgressRecorder:
    """Records progress and status updates during download."""

    def __init__(self):
        self.progress_values: List[float] = []
        self.status_updates: List[Tuple[str, Optional[str]]] = []

    def progress_callback(self, progress: float):
        self.progress_values.append(progress)

    def status_callback(self, status: str, message: Optional[str]):
        self.status_updates.append((status, message))

    @property
    def last_status(self) -> Optional[str]:
        return self.status_updates[-1][0] if self.status_updates else None

    @property
    def last_message(self) -> Optional[str]:
        return self.status_updates[-1][1] if self.status_updates else None

    @property
    def statuses(self) -> List[str]:
        return [s[0] for s in self.status_updates]


class TestGetProtocol:
    """Tests for the get_protocol function."""

    def test_get_protocol_torrent(self):
        """Test detecting torrent protocol."""
        result = {"protocol": "torrent"}
        assert get_protocol(result) == "torrent"

    def test_get_protocol_usenet(self):
        """Test detecting usenet protocol."""
        result = {"protocol": "usenet"}
        assert get_protocol(result) == "usenet"

    def test_get_protocol_unknown(self):
        """Test unknown protocol."""
        result = {"protocol": "ftp"}
        assert get_protocol(result) == "unknown"

    def test_get_protocol_empty(self):
        """Test empty protocol."""
        result = {}
        assert get_protocol(result) == "unknown"

    def test_get_protocol_case_insensitive(self):
        """Test protocol detection is case insensitive."""
        assert get_protocol({"protocol": "TORRENT"}) == "torrent"
        assert get_protocol({"protocol": "Usenet"}) == "usenet"
        assert get_protocol({"protocol": "USENET"}) == "usenet"


class TestProwlarrHandlerDownloadErrors:
    """Tests for error handling in ProwlarrHandler.download()."""

    def test_download_fails_without_cached_release(self):
        """Test that download fails when release is not in cache."""
        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value=None,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="non-existent-id",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert recorder.last_message is not None
            assert "cache" in recorder.last_message.lower()

    def test_download_fails_without_download_url(self):
        """Test that download fails when release has no download URL."""
        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "title": "Test Release",
                # No downloadUrl or magnetUrl
            },
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="no-url-release",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert recorder.last_message is not None
            assert "url" in recorder.last_message.lower()

    def test_download_fails_unknown_protocol(self):
        """Test that download fails with unknown protocol."""
        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "ftp",
                "downloadUrl": "ftp://example.com/file.zip",
            },
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="unknown-protocol",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert recorder.last_message is not None
            assert "protocol" in recorder.last_message.lower()

    def test_download_fails_no_client_configured(self):
        """Test that download fails when no client is configured."""
        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "downloadUrl": "magnet:?xt=urn:btih:abc123",
            },
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.get_client",
            return_value=None,
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.list_configured_clients",
            return_value=[],
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="no-client",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert recorder.last_message is not None
            assert "client" in recorder.last_message.lower()


class TestProwlarrHandlerExistingDownload:
    """Tests for handling existing downloads."""

    def test_prefers_magnet_url_for_torrents(self):
        """If both downloadUrl and magnetUrl exist, torrents should use magnetUrl."""
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None

        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "downloadUrl": "https://prowlarr.example.com/api/v1/indexer/1/download/123",
                "magnetUrl": "magnet:?xt=urn:btih:abc123&dn=test",
                "title": "Test Release",
            },
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.remove_release",
        ), patch.object(
            ProwlarrHandler,
            "_poll_and_complete",
            return_value=None,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(task_id="torrent-prefers-magnet", source="prowlarr", title="Test Book")
            cancel_flag = Event()
            recorder = ProgressRecorder()

            handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert mock_client.find_existing.call_count == 1
            called_url = mock_client.find_existing.call_args.args[0]
            assert called_url == "magnet:?xt=urn:btih:abc123&dn=test"

    def test_prefers_download_url_for_usenet(self):
        """If both downloadUrl and magnetUrl exist, usenet should use downloadUrl."""
        mock_client = MagicMock()
        mock_client.name = "sabnzbd"
        mock_client.find_existing.return_value = None

        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "usenet",
                "downloadUrl": "https://prowlarr.example.com/api/v1/indexer/1/download/456",
                "magnetUrl": "magnet:?xt=urn:btih:abc123&dn=test",
                "title": "Test Release",
            },
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.remove_release",
        ), patch.object(
            ProwlarrHandler,
            "_poll_and_complete",
            return_value=None,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(task_id="usenet-prefers-download", source="prowlarr", title="Test Book")
            cancel_flag = Event()
            recorder = ProgressRecorder()

            handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert mock_client.find_existing.call_count == 1
            called_url = mock_client.find_existing.call_args.args[0]
            assert called_url == "https://prowlarr.example.com/api/v1/indexer/1/download/456"

    def test_uses_existing_complete_download(self):
        """Test that handler uses existing complete download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a test file
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = (
                "existing_id",
                DownloadStatus(
                    progress=100,
                    state=DownloadState.COMPLETE,
                    message="Complete",
                    complete=True,
                    file_path=str(source_file),
                ),
            )
            mock_client.get_download_path.return_value = str(source_file)

            with patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "shelfmark.download.staging.get_staging_dir",
                return_value=staging_dir,
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="existing-complete",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                assert "resolving" in recorder.statuses
                # Should NOT have called add_download
                mock_client.add_download.assert_not_called()


class TestProwlarrHandlerPolling:
    """Tests for download polling behavior."""

    def test_retries_torrent_not_found_errors(self):
        """"Torrent not found" should be treated as transient."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            poll_count = [0]

            def mock_get_status(download_id):
                poll_count[0] += 1
                if poll_count[0] <= 2:
                    return DownloadStatus(
                        progress=0,
                        state=DownloadState.ERROR,
                        message="Torrent not found in qBittorrent",
                        complete=False,
                        file_path=None,
                    )

                return DownloadStatus(
                    progress=100,
                    state=DownloadState.COMPLETE,
                    message="Complete",
                    complete=True,
                    file_path=str(source_file),
                )

            mock_client = MagicMock()
            mock_client.name = "qbittorrent"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.side_effect = mock_get_status
            mock_client.get_download_path.return_value = str(source_file)

            with patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "shelfmark.download.staging.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="poll-not-found-test",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                assert poll_count[0] >= 3
                assert "resolving" in recorder.statuses

    def test_fails_fast_on_auth_errors(self):
        """Auth/API errors should not be retried as "not found"."""
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=0,
            state=DownloadState.ERROR,
            message="qBittorrent authentication failed (HTTP 403)",
            complete=False,
            file_path=None,
        )

        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "magnetUrl": "magnet:?xt=urn:btih:abc123",
            },
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
            0.01,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="poll-auth-fail-test",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert "authentication failed" in (recorder.last_message or "").lower()

    def test_polls_until_complete(self):
        """Test that handler polls until download is complete."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            poll_count = [0]

            def mock_get_status(download_id):
                poll_count[0] += 1
                if poll_count[0] >= 3:
                    return DownloadStatus(
                        progress=100,
                        state=DownloadState.COMPLETE,
                        message="Complete",
                        complete=True,
                        file_path=str(source_file),
                    )
                return DownloadStatus(
                    progress=poll_count[0] * 30,
                    state=DownloadState.DOWNLOADING,
                    message=None,
                    complete=False,
                    file_path=None,
                    download_speed=1024000,
                    eta=60,
                )

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.side_effect = mock_get_status
            mock_client.get_download_path.return_value = str(source_file)

            with patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "shelfmark.download.staging.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,  # Speed up tests
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="poll-test",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                assert poll_count[0] >= 3
                assert len(recorder.progress_values) >= 3

    def test_handles_error_during_download(self):
        """Test that handler handles error state during download."""
        mock_client = MagicMock()
        mock_client.name = "test_client"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=50,
            state=DownloadState.ERROR,
            message="Disk full",
            complete=False,
            file_path=None,
        )

        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "magnetUrl": "magnet:?xt=urn:btih:abc123",
            },
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
            0.01,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="error-test",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            mock_client.remove.assert_not_called()


class TestProwlarrHandlerCancellation:
    """Tests for download cancellation."""

    def test_cancellation_does_not_remove_torrent(self):
        """Test that torrent cancellation does not remove from client."""
        mock_client = MagicMock()
        mock_client.name = "test_client"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=50,
            state=DownloadState.DOWNLOADING,
            message="Downloading",
            complete=False,
            file_path=None,
        )

        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "magnetUrl": "magnet:?xt=urn:btih:abc123",
            },
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.get_client",
            return_value=mock_client,
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
            0.01,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="cancel-test",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            # Set cancel immediately
            cancel_flag.set()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert "cancelled" in recorder.statuses
            mock_client.remove.assert_not_called()


class TestProwlarrHandlerCancel:
    """Tests for ProwlarrHandler.cancel()."""

    def test_cancel_removes_from_cache(self):
        """Test that cancel removes release from cache."""
        with patch(
            "shelfmark.release_sources.prowlarr.handler.remove_release"
        ) as mock_remove:
            handler = ProwlarrHandler()
            result = handler.cancel("test-task-id")

            assert result is True
            mock_remove.assert_called_once_with("test-task-id")

    def test_cancel_handles_missing_task(self):
        """Test that cancel handles non-existent task gracefully."""
        with patch(
            "shelfmark.release_sources.prowlarr.handler.remove_release"
        ):
            handler = ProwlarrHandler()
            result = handler.cancel("nonexistent-task-id")

            assert result is True


class TestProwlarrHandlerFileStaging:
    """Tests for file staging behavior."""

    def test_stages_single_file(self):
        """Test staging a single file download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.return_value = DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path=str(source_file),
            )
            mock_client.get_download_path.return_value = str(source_file)

            with patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "shelfmark.download.staging.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="staging-test",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                staged_file = Path(result)
                assert staged_file.exists()
                assert staged_file.read_text() == "test content"

    def test_stages_directory(self):
        """Test staging a directory download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_dir = Path(tmp_dir) / "source" / "book_folder"
            source_dir.mkdir(parents=True)
            (source_dir / "book.epub").write_text("epub content")
            (source_dir / "cover.jpg").write_bytes(b"image data")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.return_value = DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path=str(source_dir),
            )
            mock_client.get_download_path.return_value = str(source_dir)

            with patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "shelfmark.download.staging.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="dir-staging-test",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                staged_dir = Path(result)
                assert staged_dir.is_dir()
                assert (staged_dir / "book.epub").exists()
                assert (staged_dir / "cover.jpg").exists()

    def test_handles_duplicate_filename(self):
        """Usenet downloads return the original file path (no staging)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("new content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()
            # Create existing file with same name
            (staging_dir / "book.epub").write_text("old content")

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.return_value = DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path=str(source_file),
            )
            mock_client.get_download_path.return_value = str(source_file)

            # Use usenet protocol - torrents skip staging and return original path directly
            with patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "usenet",
                    "downloadUrl": "https://indexer.example.com/download/123",
                },
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ), patch(
                "shelfmark.download.staging.get_staging_dir",
                return_value=staging_dir,
            ), patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="dup-staging-test",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                returned_file = Path(result)
                assert returned_file == source_file
                assert returned_file.exists()
                assert returned_file.read_text() == "new content"


class TestProwlarrHandlerPostProcessCleanup:
    def test_usenet_move_triggers_client_cleanup(self):
        handler = ProwlarrHandler()
        task = DownloadTask(task_id="cleanup-test", source="prowlarr", title="Test")

        mock_client = MagicMock()
        mock_client.name = "nzbget"
        handler._cleanup_refs[task.task_id] = (mock_client, "123", "usenet")

        with patch("shelfmark.release_sources.prowlarr.handler.config.get", return_value="move"):
            handler.post_process_cleanup(task, success=True)

        mock_client.remove.assert_called_once_with("123", delete_files=True)

    def test_usenet_copy_does_not_cleanup(self):
        handler = ProwlarrHandler()
        task = DownloadTask(task_id="cleanup-test", source="prowlarr", title="Test")

        mock_client = MagicMock()
        mock_client.name = "nzbget"
        handler._cleanup_refs[task.task_id] = (mock_client, "123", "usenet")

        with patch("shelfmark.release_sources.prowlarr.handler.config.get", return_value="copy"):
            handler.post_process_cleanup(task, success=True)

        mock_client.remove.assert_not_called()
