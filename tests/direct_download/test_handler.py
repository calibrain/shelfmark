from threading import Event

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.direct_download import DirectDownloadHandler


def test_direct_download_handler_builds_staging_filename_from_browse_record(monkeypatch):
    captured = {}

    def fake_download_book(book_info, book_path, progress_callback, cancel_flag, status_callback):  # noqa: ANN001
        captured["title"] = book_info.title
        captured["year"] = book_info.year
        captured["path"] = book_path
        return "https://example.com/file.epub"

    import shelfmark.release_sources.direct_download as dd

    monkeypatch.setattr(dd, "_download_book", fake_download_book)
    monkeypatch.setattr(dd.config, "get", lambda key, default=None: "rename" if key == "FILE_ORGANIZATION" else default)

    task = DownloadTask(
        task_id="92c7879138d18678b763118250228955",
        source="direct_download",
        title="Project Hail Mary: A Novel",
        author="Andy Weir",
        year="2021",
        format="epub",
    )

    handler = DirectDownloadHandler()
    result = handler.download(task, Event(), lambda _progress: None, lambda _status, _message: None)

    assert result is not None
    assert captured["title"] == "Project Hail Mary: A Novel"
    assert captured["year"] == "2021"
    assert captured["path"].name == "Andy Weir - Project Hail Mary_ A Novel (2021).epub"
