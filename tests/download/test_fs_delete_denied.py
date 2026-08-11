"""Transfers into destinations that accept writes but refuse deletes.

Reproduces the Synology case from issue #1174: a share where "Delete subfolders
and files" is unticked. Creating files is allowed, but `os.replace()` cannot
publish a temp file into place because renaming removes a directory entry.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from shelfmark.download import fs
from shelfmark.download.fs import atomic_copy, atomic_move

CONTENT = b"a book" * 1024


@pytest.fixture(autouse=True)
def _reset_delete_denied():
    fs._DELETE_DENIED_DIRS.clear()
    yield
    fs._DELETE_DENIED_DIRS.clear()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "tmp_dir" / "staged.epub"
    src.parent.mkdir()
    src.write_bytes(CONTENT)
    return src


@pytest.fixture
def library(tmp_path: Path) -> Path:
    lib = tmp_path / "library"
    lib.mkdir()
    return lib


def _leftover_temps(directory: Path) -> list[Path]:
    return list(directory.glob(".shelfmark.*"))


def _deny_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make os.replace fail the way a no-delete share does."""

    def fake_replace(src, dst, *args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", str(dst))

    monkeypatch.setattr(fs.os, "replace", fake_replace)


def test_copy_writes_in_place_when_destination_is_known_undeletable(
    source: Path, library: Path
) -> None:
    fs.mark_delete_denied(library)

    final_path = atomic_copy(source, library / "book.epub")

    assert final_path == library / "book.epub"
    assert final_path.read_bytes() == CONTENT
    # The temp file is skipped entirely, so nothing is stranded in the library.
    assert _leftover_temps(library) == []
    assert source.exists()


def test_copy_falls_back_when_rename_is_refused(
    source: Path, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold cache: the denial is discovered at publish time and recovered from."""
    _deny_replace(monkeypatch)

    final_path = atomic_copy(source, library / "book.epub")

    assert final_path.read_bytes() == CONTENT
    assert _leftover_temps(library) == []
    # The destination is remembered so later transfers skip the temp file.
    assert fs.is_delete_denied(library) is True


def test_move_falls_back_to_copy_and_removes_source(source: Path, library: Path) -> None:
    fs.mark_delete_denied(library)

    final_path = atomic_move(source, library / "book.epub")

    assert final_path.read_bytes() == CONTENT
    assert not source.exists()
    assert _leftover_temps(library) == []


def test_move_falls_back_when_rename_is_refused(
    source: Path, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_rename = fs.os.rename

    def fake_rename(src, dst, *args, **kwargs):
        if str(dst).startswith(str(library)):
            raise PermissionError(errno.EACCES, "Permission denied", str(dst))
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(fs.os, "rename", fake_rename)
    _deny_replace(monkeypatch)

    final_path = atomic_move(source, library / "book.epub")

    assert final_path.read_bytes() == CONTENT
    assert not source.exists()
    assert fs.is_delete_denied(library) is True


def test_in_place_copy_still_resolves_collisions(source: Path, library: Path) -> None:
    fs.mark_delete_denied(library)
    (library / "book.epub").write_bytes(b"existing")

    final_path = atomic_copy(source, library / "book.epub")

    assert final_path == library / "book_1.epub"
    assert final_path.read_bytes() == CONTENT
    # The pre-existing file is never overwritten.
    assert (library / "book.epub").read_bytes() == b"existing"


def test_denial_applies_to_subdirectories(source: Path, library: Path) -> None:
    """`organize` mode writes into per-author subfolders under the library root."""
    fs.mark_delete_denied(library)
    nested = library / "Frank Herbert" / "Dune"
    nested.mkdir(parents=True)

    assert fs.is_delete_denied(nested) is True

    final_path = atomic_copy(source, nested / "book.epub")

    assert final_path.read_bytes() == CONTENT
    assert _leftover_temps(nested) == []


def test_clearing_a_denial_also_clears_subdirectories(library: Path) -> None:
    nested = library / "Frank Herbert"
    fs.mark_delete_denied(library)
    fs.mark_delete_denied(nested)

    fs.clear_delete_denied(library)

    assert fs.is_delete_denied(library) is False
    assert fs.is_delete_denied(nested) is False


def test_normal_destination_still_publishes_atomically(
    source: Path, library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: unaffected shares keep the temp-file + rename path."""
    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy_replace(src, dst, *args, **kwargs):
        replaced.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(fs.os, "replace", spy_replace)

    final_path = atomic_copy(source, library / "book.epub")

    assert final_path.read_bytes() == CONTENT
    assert len(replaced) == 1
    assert Path(replaced[0][0]).name.startswith(".shelfmark.")
    assert fs.is_delete_denied(library) is False
