"""
Tests for AudiobookBay download handler.
"""

from threading import Event
from unittest.mock import Mock, patch, MagicMock
import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.audiobookbay.handler import AudiobookBayHandler
from shelfmark.release_sources.prowlarr.clients import (
    DownloadStatus,
    DownloadState,
)


class ProgressRecorder:
    """Records progress and status updates during download."""

    def __init__(self):
        self.progress_values = []
        self.status_updates = []

    def progress_callback(self, progress: float):
        self.progress_values.append(progress)

    def status_callback(self, status: str, message=None):
        self.status_updates.append((status, message))

    @property
    def last_status(self):
        return self.status_updates[-1][0] if self.status_updates else None

    @property
    def last_message(self):
        return self.status_updates[-1][1] if self.status_updates else None

    @property
    def statuses(self):
        return [s[0] for s in self.status_updates]


class TestAudiobookBayHandlerDownload:
    """Tests for AudiobookBayHandler.download()."""

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    def test_download_success(self, mock_get_client, mock_extract_magnet):
        """Test successful download initiation."""
        mock_extract_magnet.return_value = "magnet:?xt=urn:btih:abc123"
        
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id_123"
        mock_get_client.return_value = mock_client
        
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="audiobook",
        )
        cancel_flag = Event()
        recorder = ProgressRecorder()
        
        result = handler.download(
            task=task,
            cancel_flag=cancel_flag,
            progress_callback=recorder.progress_callback,
            status_callback=recorder.status_callback,
        )
        
        assert result is None  # Torrents don't return path immediately
        mock_extract_magnet.assert_called_once_with(
            "https://audiobookbay.lu/abss/test-book/",
            "audiobookbay.lu"
        )
        mock_client.add_download.assert_called_once()
        assert "downloading" in recorder.statuses

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    def test_download_existing_complete(self, mock_get_client, mock_extract_magnet):
        """Test handling existing complete download."""
        mock_extract_magnet.return_value = "magnet:?xt=urn:btih:abc123"
        
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = (
            "existing_id",
            DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path="/path/to/book.m4b",
            ),
        )
        mock_client.get_download_path.return_value = "/path/to/book.m4b"
        mock_get_client.return_value = mock_client
        
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="audiobook",
        )
        cancel_flag = Event()
        recorder = ProgressRecorder()
        
        result = handler.download(
            task=task,
            cancel_flag=cancel_flag,
            progress_callback=recorder.progress_callback,
            status_callback=recorder.status_callback,
        )
        
        assert result == "/path/to/book.m4b"
        mock_client.add_download.assert_not_called()

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    def test_download_existing_in_progress(self, mock_get_client, mock_extract_magnet):
        """Test handling existing in-progress download."""
        mock_extract_magnet.return_value = "magnet:?xt=urn:btih:abc123"
        
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = (
            "existing_id",
            DownloadStatus(
                progress=50,
                state=DownloadState.DOWNLOADING,
                message="Downloading",
                complete=False,
                file_path=None,
            ),
        )
        mock_get_client.return_value = mock_client
        
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="audiobook",
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
        assert "downloading" in recorder.statuses
        mock_client.add_download.assert_not_called()

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    def test_download_cancellation(self, mock_get_client, mock_extract_magnet):
        """Test that cancellation is respected."""
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="audiobook",
        )
        cancel_flag = Event()
        cancel_flag.set()  # Set immediately
        recorder = ProgressRecorder()
        
        result = handler.download(
            task=task,
            cancel_flag=cancel_flag,
            progress_callback=recorder.progress_callback,
            status_callback=recorder.status_callback,
        )
        
        assert result is None
        assert "cancelled" in recorder.statuses
        mock_extract_magnet.assert_not_called()

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    def test_download_no_magnet_link(self, mock_get_client, mock_extract_magnet):
        """Test handling when magnet link extraction fails."""
        mock_extract_magnet.return_value = None
        
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="audiobook",
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
        assert "magnet link" in recorder.last_message.lower()

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    @patch('shelfmark.release_sources.audiobookbay.handler.list_configured_clients')
    def test_download_no_client_configured(self, mock_list_clients, mock_get_client, mock_extract_magnet):
        """Test handling when no torrent client is configured."""
        mock_extract_magnet.return_value = "magnet:?xt=urn:btih:abc123"
        mock_get_client.return_value = None
        mock_list_clients.return_value = []
        
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="audiobook",
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
        assert "client" in recorder.last_message.lower()

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    def test_download_client_add_failure(self, mock_get_client, mock_extract_magnet):
        """Test handling when client.add_download fails."""
        mock_extract_magnet.return_value = "magnet:?xt=urn:btih:abc123"
        
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.side_effect = Exception("Client error")
        mock_get_client.return_value = mock_client
        
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="audiobook",
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

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    def test_download_existing_no_path(self, mock_get_client, mock_extract_magnet):
        """Test handling when existing download has no path."""
        mock_extract_magnet.return_value = "magnet:?xt=urn:btih:abc123"
        
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = (
            "existing_id",
            DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path=None,
            ),
        )
        mock_client.get_download_path.return_value = None
        mock_get_client.return_value = mock_client
        
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="audiobook",
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
        assert "path" in recorder.last_message.lower()


class TestAudiobookBayHandlerCategory:
    """Tests for category selection."""

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    @patch('shelfmark.release_sources.audiobookbay.handler.config.get')
    def test_category_selection_qbittorrent_audiobook(self, mock_config_get, mock_get_client, mock_extract_magnet):
        """Test audiobook category selection for qBittorrent."""
        mock_extract_magnet.return_value = "magnet:?xt=urn:btih:abc123"
        
        def config_get(key, default=""):
            if key == "QBITTORRENT_CATEGORY_AUDIOBOOK":
                return "audiobooks"
            return default
        
        mock_config_get.side_effect = config_get
        
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_get_client.return_value = mock_client
        
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="audiobook",
        )
        cancel_flag = Event()
        recorder = ProgressRecorder()
        
        handler.download(
            task=task,
            cancel_flag=cancel_flag,
            progress_callback=recorder.progress_callback,
            status_callback=recorder.status_callback,
        )
        
        # Verify category was passed
        call_kwargs = mock_client.add_download.call_args.kwargs
        assert call_kwargs['category'] == "audiobooks"

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    @patch('shelfmark.release_sources.audiobookbay.handler.config.get')
    def test_category_selection_transmission_general(self, mock_config_get, mock_get_client, mock_extract_magnet):
        """Test fallback to general category for Transmission."""
        mock_extract_magnet.return_value = "magnet:?xt=urn:btih:abc123"
        
        def config_get(key, default=""):
            if key == "TRANSMISSION_CATEGORY":
                return "books"
            return default
        
        mock_config_get.side_effect = config_get
        
        mock_client = MagicMock()
        mock_client.name = "transmission"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_get_client.return_value = mock_client
        
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="audiobook",
        )
        cancel_flag = Event()
        recorder = ProgressRecorder()
        
        handler.download(
            task=task,
            cancel_flag=cancel_flag,
            progress_callback=recorder.progress_callback,
            status_callback=recorder.status_callback,
        )
        
        # Verify general category was used
        call_kwargs = mock_client.add_download.call_args.kwargs
        assert call_kwargs['category'] == "books"

    @patch('shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link')
    @patch('shelfmark.release_sources.audiobookbay.handler.get_client')
    @patch('shelfmark.release_sources.audiobookbay.handler.config.get')
    def test_category_selection_non_audiobook(self, mock_config_get, mock_get_client, mock_extract_magnet):
        """Test that non-audiobook content types don't get category."""
        mock_extract_magnet.return_value = "magnet:?xt=urn:btih:abc123"
        
        mock_config_get.return_value = ""
        
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_get_client.return_value = mock_client
        
        handler = AudiobookBayHandler()
        task = DownloadTask(
            task_id="https://audiobookbay.lu/abss/test-book/",
            source="audiobookbay",
            title="Test Book",
            content_type="ebook",  # Not audiobook
        )
        cancel_flag = Event()
        recorder = ProgressRecorder()
        
        handler.download(
            task=task,
            cancel_flag=cancel_flag,
            progress_callback=recorder.progress_callback,
            status_callback=recorder.status_callback,
        )
        
        # Verify no category was passed
        call_kwargs = mock_client.add_download.call_args.kwargs
        assert call_kwargs['category'] is None


class TestAudiobookBayHandlerCancel:
    """Tests for AudiobookBayHandler.cancel()."""

    def test_cancel_returns_false(self):
        """Test that cancel always returns False (torrents can't be cancelled)."""
        handler = AudiobookBayHandler()
        result = handler.cancel("test-task-id")
        assert result is False
