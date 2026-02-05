from __future__ import annotations

from pathlib import Path

from shelfmark.core.models import DownloadTask
from shelfmark.download.postprocess import scan as scan_mod


def test_scan_directory_tree_runs_walk_via_run_blocking_io(tmp_path, monkeypatch) -> None:
    (tmp_path / "book.epub").write_text("x", encoding="utf-8")

    inside_run_blocking = False
    walk_called = False

    original_walk = scan_mod.os.walk

    def walk_wrapper(*args, **kwargs):
        nonlocal walk_called
        walk_called = True
        assert inside_run_blocking, "os.walk should run within run_blocking_io"
        return original_walk(*args, **kwargs)

    def run_blocking_io_stub(func, *args, **kwargs):
        nonlocal inside_run_blocking
        inside_run_blocking = True
        try:
            return func(*args, **kwargs)
        finally:
            inside_run_blocking = False

    monkeypatch.setattr(scan_mod.os, "walk", walk_wrapper)
    monkeypatch.setattr(scan_mod, "run_blocking_io", run_blocking_io_stub)

    scan_mod.scan_directory_tree(tmp_path, content_type=None)

    assert walk_called, "Expected scan_directory_tree to call os.walk"


def test_extract_archive_files_runs_extract_via_run_blocking_io(tmp_path, monkeypatch) -> None:
    inside_run_blocking = False
    extract_called = False

    def extract_archive_stub(*args, **kwargs):
        nonlocal extract_called
        extract_called = True
        assert inside_run_blocking, "extract_archive should run within run_blocking_io"
        return [], [], []

    def run_blocking_io_stub(func, *args, **kwargs):
        nonlocal inside_run_blocking
        inside_run_blocking = True
        try:
            return func(*args, **kwargs)
        finally:
            inside_run_blocking = False

    monkeypatch.setattr(scan_mod, "extract_archive", extract_archive_stub)
    monkeypatch.setattr(scan_mod, "run_blocking_io", run_blocking_io_stub)

    task = DownloadTask(task_id="t", source="prowlarr", title="Test", content_type="book")
    scan_mod.extract_archive_files(
        archive_path=Path("/tmp/fake.zip"),
        output_dir=tmp_path,
        task=task,
        cleanup_archive=False,
    )

    assert extract_called, "Expected extract_archive_files to call extract_archive"

