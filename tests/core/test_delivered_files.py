"""Tests for the delivered-file identity that local downloads are checked against."""

from __future__ import annotations

import os

from shelfmark.core.delivered_files import file_identity, open_delivered_file


def test_identity_of_a_regular_file(tmp_path):
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    assert file_identity(book)


def test_no_identity_for_a_missing_path_or_a_folder(tmp_path):
    assert file_identity(tmp_path / "missing.epub") is None
    assert file_identity(tmp_path) is None


def test_opens_the_file_that_was_delivered(tmp_path):
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    handle = open_delivered_file(str(book), file_identity(book))
    assert handle is not None
    with handle:
        assert handle.read() == b"epub"


def test_refuses_without_a_recorded_identity(tmp_path):
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    assert open_delivered_file(str(book), None) is None
    assert open_delivered_file(None, file_identity(book)) is None


def test_refuses_another_file_moved_into_the_path(tmp_path):
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    delivered = file_identity(book)
    other = tmp_path / "other.epub"
    other.write_bytes(b"epub")
    os.utime(other, ns=(book.stat().st_atime_ns, book.stat().st_mtime_ns))
    other.replace(book)
    assert open_delivered_file(str(book), delivered) is None


def test_refuses_a_file_changed_in_place(tmp_path):
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    delivered = file_identity(book)
    book.write_bytes(b"a longer epub")
    assert open_delivered_file(str(book), delivered) is None


def test_refuses_a_path_that_is_gone(tmp_path):
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    delivered = file_identity(book)
    book.unlink()
    assert open_delivered_file(str(book), delivered) is None


def test_a_symlink_is_checked_by_the_file_it_points_to(tmp_path):
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    delivered = file_identity(book)
    elsewhere = tmp_path / "elsewhere.epub"
    elsewhere.write_bytes(b"not the book")
    link = tmp_path / "link.epub"
    link.symlink_to(elsewhere)
    assert open_delivered_file(str(link), delivered) is None


def test_a_pipe_at_the_path_is_refused_without_waiting(tmp_path):
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    delivered = file_identity(book)
    book.unlink()
    os.mkfifo(book)
    assert open_delivered_file(str(book), delivered) is None


def test_a_folder_at_the_path_is_refused(tmp_path):
    book = tmp_path / "book.epub"
    book.write_bytes(b"epub")
    delivered = file_identity(book)
    book.unlink()
    book.mkdir()
    assert open_delivered_file(str(book), delivered) is None
