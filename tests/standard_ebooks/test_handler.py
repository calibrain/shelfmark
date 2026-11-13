"""Tests for StandardEbooksHandler: staging, primary download, 404 fallback, and cancellation."""

import io
from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.standard_ebooks import handler as se_handler
from shelfmark.release_sources.standard_ebooks.handler import StandardEbooksHandler
from tests.standard_ebooks import sample_html as html


def _task(task_id=f"standard_ebooks:{html.SLUG_2_SEG}", title="Frankenstein", fmt="epub"):
    return DownloadTask(
        task_id=task_id,
        source="standard_ebooks",
        title=title,
        author="Mary Shelley",
        format=fmt,
        size="1 MB",
        source_url=f"https://standardebooks.org/ebooks/{html.SLUG_2_SEG}",
    )


def _buf(nbytes=5000):
    buf = io.BytesIO(b"x" * nbytes)
    buf.seek(0, io.SEEK_END)
    return buf


def _run(task, tmp_path, *, download_url_side_effect, fetch_page_side_effect=None):
    status = MagicMock()
    progress = MagicMock()
    cancel = threading.Event()
    with (
        patch.object(se_handler, "TMP_DIR", tmp_path),
        patch.object(
            se_handler.config,
            "get",
            side_effect=lambda k, d=None: "none" if k == "FILE_ORGANIZATION" else d,
        ),
        patch.object(se_handler.downloader, "download_url", side_effect=download_url_side_effect),
        patch.object(
            se_handler.scraper, "fetch_page", side_effect=fetch_page_side_effect or (lambda u: None)
        ),
    ):
        result = StandardEbooksHandler().download(task, cancel, progress, status)
    return result, status


def test_download_success(tmp_path):
    captured = {}

    def fake_download(link, size, prog, cancel, sel, status, referer=None):
        captured["link"] = link
        captured["referer"] = referer
        return _buf(5000)

    task = _task()
    result, status = _run(task, tmp_path, download_url_side_effect=fake_download)

    expected = tmp_path / "mary-shelley_frankenstein.epub"
    assert result == str(expected)
    assert expected.exists()
    assert expected.stat().st_size == 5000
    assert "mary-shelley_frankenstein.epub" in captured["link"]
    status.assert_any_call("resolving", "Connecting to Standard Ebooks")


def test_download_fallback_when_primary_fails(tmp_path):
    calls = []

    def fake_download(link, size, prog, cancel, sel, status, referer=None):
        calls.append(link)
        if len(calls) == 1:
            return None  # First call (primary URL) fails
        return _buf(5000)  # Second call (fallback) succeeds

    def fake_fetch_page(url):
        return html.BOOK_PAGE_HTML

    task = _task()
    result, status = _run(
        task,
        tmp_path,
        download_url_side_effect=fake_download,
        fetch_page_side_effect=fake_fetch_page,
    )

    expected = tmp_path / "mary-shelley_frankenstein.epub"
    assert result == str(expected)
    assert expected.exists()
    assert len(calls) == 2
    status.assert_any_call("resolving", "Checking book page for download link")


def test_download_all_attempts_fail_returns_none(tmp_path):
    def fake_download(*args, **kwargs):
        return None

    def fake_fetch_page(url):
        return None

    task = _task()
    result, status = _run(
        task,
        tmp_path,
        download_url_side_effect=fake_download,
        fetch_page_side_effect=fake_fetch_page,
    )

    assert result is None
    status.assert_any_call("error", "Failed to download from Standard Ebooks")


def test_download_too_small_file_rejected(tmp_path):
    def fake_download(*args, **kwargs):
        return _buf(50)  # below _MIN_VALID_FILE_SIZE (1024)

    task = _task()
    result, status = _run(task, tmp_path, download_url_side_effect=fake_download)

    assert result is None
    status.assert_any_call("error", "Standard Ebooks download was incomplete or invalid")


def test_download_cancelled_before_start(tmp_path):
    status = MagicMock()
    cancel = threading.Event()
    cancel.set()

    with patch.object(se_handler, "TMP_DIR", tmp_path):
        result = StandardEbooksHandler().download(_task(), cancel, MagicMock(), status)

    assert result is None
    status.assert_any_call("cancelled", "Cancelled")


def test_download_cancelled_during_download(tmp_path):
    cancel = threading.Event()
    status = MagicMock()

    def fake_download(*args, **kwargs):
        cancel.set()
        return None

    with (
        patch.object(se_handler, "TMP_DIR", tmp_path),
        patch.object(se_handler.downloader, "download_url", side_effect=fake_download),
    ):
        result = StandardEbooksHandler().download(_task(), cancel, MagicMock(), status)

    assert result is None
    status.assert_any_call("cancelled", "Cancelled")


def test_hostile_slug_cannot_escape_tmp_dir(tmp_path):
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    task = _task(task_id="standard_ebooks:../../evil", title="Evil")

    def fake_download(*args, **kwargs):
        return _buf()

    result, _ = _run(task, tmp_dir, download_url_side_effect=fake_download)
    assert not (tmp_path / "evil.epub").exists()
    if result:
        assert Path(result).parent == tmp_dir
