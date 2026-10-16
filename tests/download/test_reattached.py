"""A download that picks up work already under way says so, so nobody wonders why it started at 40%."""

from __future__ import annotations

from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.download.clients import DownloadState, DownloadStatus
from shelfmark.download.orchestrator import _task_to_dict
from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler

_STATUS = DownloadStatus(
    progress=40.0, state=DownloadState.DOWNLOADING, message=None, complete=False, file_path=None
)


def _run(*, existing: bool) -> DownloadTask:
    client = MagicMock()
    client.name = "qbittorrent"
    client.find_existing.return_value = ("dl", _STATUS) if existing else None
    client.add_download.return_value = "dl"
    cancel = Event()

    def get_status(_id: str) -> DownloadStatus:
        cancel.set()
        return _STATUS

    client.get_status.side_effect = get_status
    release = {"protocol": "torrent", "magnetUrl": "magnet:?xt=urn:btih:abc123"}
    task = DownloadTask(task_id="t", source="prowlarr", title="Book")
    with (
        patch("shelfmark.release_sources.prowlarr.handler.get_release", return_value=release),
        patch("shelfmark.release_sources.prowlarr.handler.get_client", return_value=client),
        patch("shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL", 0.01),
    ):
        ProwlarrHandler().download(
            task=task,
            cancel_flag=cancel,
            progress_callback=lambda _p: None,
            status_callback=lambda *_a: None,
        )
    return task


def test_a_task_that_joins_a_torrent_the_client_had_is_reattached() -> None:
    assert _run(existing=True).reattached is True


def test_a_task_that_adds_its_own_torrent_is_not() -> None:
    assert _run(existing=False).reattached is False


@pytest.mark.parametrize("flag", [True, False])
def test_the_flag_reaches_the_status_payload(flag: bool) -> None:
    task = DownloadTask(task_id="t", source="prowlarr", title="Book", reattached=flag)

    assert _task_to_dict(task, current_status="downloading")["reattached"] is flag
