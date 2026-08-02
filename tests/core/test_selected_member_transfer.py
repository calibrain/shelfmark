"""Tests for the immutable selected-source-member transfer boundary."""

from __future__ import annotations

import os

import pytest

from shelfmark.download.postprocess.transfer import transfer_selected_source_members


def test_selected_members_preserve_hierarchy_and_leave_unselected_sources_untouched(tmp_path):
    source_root = tmp_path / "retained"
    selected = source_root / "ebook" / "nested" / "Book.epub"
    unselected = source_root / "audio" / "Book.m4b"
    selected.parent.mkdir(parents=True)
    unselected.parent.mkdir(parents=True)
    selected.write_bytes(b"ebook")
    unselected.write_bytes(b"audio")
    destination = tmp_path / "library" / "books" / "7" / "11" / "ebook" / "nested" / "Book.epub"

    final_paths, error, operations = transfer_selected_source_members(
        [(selected, destination)], use_hardlink=False
    )

    assert error is None
    assert final_paths == [destination]
    assert destination.read_bytes() == b"ebook"
    assert selected.read_bytes() == b"ebook"
    assert unselected.exists()
    assert operations == {"hardlink": 0, "copy": 1}


def test_selected_members_can_hardlink_and_reject_preexisting_artifacts(tmp_path):
    source = tmp_path / "retained" / "Book.epub"
    destination = tmp_path / "library" / "books" / "7" / "11" / "Book.epub"
    source.parent.mkdir()
    source.write_bytes(b"ebook")

    final_paths, error, operations = transfer_selected_source_members(
        [(source, destination)], use_hardlink=True
    )

    assert error is None
    assert final_paths == [destination]
    assert operations["hardlink"] == 1
    assert os.stat(source).st_ino == os.stat(destination).st_ino
    with pytest.raises(FileExistsError, match="already exists"):
        transfer_selected_source_members([(source, destination)], use_hardlink=True)
