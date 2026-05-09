from __future__ import annotations

from pathlib import Path

from shelfmark.download import archive as archive_mod


class _FakeZipInfo:
    filename = "book.epub"
    flag_bits = 0

    def is_dir(self) -> bool:
        return False


class _ChunkOnlyStream:
    def __init__(self, content: bytes) -> None:
        self._content = content
        self._offset = 0
        self.whole_read_called = False

    def __enter__(self) -> _ChunkOnlyStream:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            self.whole_read_called = True
            msg = "archive member was read into memory"
            raise AssertionError(msg)

        chunk = self._content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _FakeZipFile:
    stream: _ChunkOnlyStream

    def __init__(self, _path: Path, _mode: str) -> None:
        self.stream = _ChunkOnlyStream(b"streamed archive content")

    def __enter__(self) -> _FakeZipFile:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def infolist(self) -> list[_FakeZipInfo]:
        return [_FakeZipInfo()]

    def testzip(self) -> None:
        return None

    def open(self, _info: _FakeZipInfo) -> _ChunkOnlyStream:
        return self.stream


def test_extract_archive_raw_streams_members_without_whole_read(
    tmp_path: Path, monkeypatch
) -> None:
    fake_archives: list[_FakeZipFile] = []

    def fake_zip_file(path: Path, mode: str) -> _FakeZipFile:
        archive = _FakeZipFile(path, mode)
        fake_archives.append(archive)
        return archive

    monkeypatch.setattr(archive_mod.zipfile, "ZipFile", fake_zip_file)

    extracted_files, warnings = archive_mod.extract_archive_raw(tmp_path / "book.zip", tmp_path)

    assert warnings == []
    assert [path.name for path in extracted_files] == ["book.epub"]
    assert extracted_files[0].read_bytes() == b"streamed archive content"
    assert fake_archives[0].stream.whole_read_called is False
