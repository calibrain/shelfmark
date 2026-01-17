"""Tests for remote path mappings.

This focuses on integration of mapping logic into the Prowlarr handler.
"""

import tempfile
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.prowlarr.clients import DownloadState, DownloadStatus
from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler


class ProgressRecorder:
    def __init__(self):
        self.progress_values = []
        self.status_updates = []

    def progress_callback(self, progress: float):
        self.progress_values.append(progress)

    def status_callback(self, status: str, message: str | None):
        self.status_updates.append((status, message))


def test_remaps_completed_path_when_remote_path_missing():
    with tempfile.TemporaryDirectory() as tmp_dir:
        local_file = Path(tmp_dir) / "local" / "book.epub"
        local_file.parent.mkdir(parents=True)
        local_file.write_text("test content")

        remote_path = "/remote/downloads/book.epub"

        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=100,
            state=DownloadState.COMPLETE,
            message="Complete",
            complete=True,
            file_path=remote_path,
        )
        mock_client.get_download_path.return_value = remote_path

        def config_get(key: str, default=""):
            if key == "PROWLARR_REMOTE_PATH_MAPPINGS":
                return [
                    {
                        "host": "qbittorrent",
                        "remotePath": "/remote/downloads",
                        "localPath": str(local_file.parent),
                    }
                ]
            return default

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
            "shelfmark.release_sources.prowlarr.handler.config.get",
            side_effect=config_get,
        ), patch(
            "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
            0.01,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(task_id="poll-mapping-test", source="prowlarr", title="Test Book")
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result == str(local_file)
            assert task.original_download_path == str(local_file)
