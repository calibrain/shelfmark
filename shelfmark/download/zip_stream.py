"""Stream several downloaded files to a client as one ZIP, without staging the archive."""

import os
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import BinaryIO

_ZIP_STREAM_CHUNK_SIZE = 1024 * 1024


class _ChunkSink:
    """Write-only ZipFile target that hands the written bytes back to the caller.

    Having no tell() or seek() makes ZipFile treat it as an unseekable stream: each
    entry's CRC and sizes go in a trailing data descriptor instead of being patched
    back into the local header, so nothing has to be held until the entry ends.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        """Nothing is buffered beyond what drain() hands out."""

    def close(self) -> None:
        """Nothing to release; the caller drains what is left after ZipFile closes."""

    def drain(self) -> Iterator[bytes]:
        chunks, self._chunks = self._chunks, []
        yield from chunks


def zip_entry_names(paths: list[str]) -> list[tuple[Path, str]]:
    """Pair each file with its name inside the archive.

    Names are relative to the files' common parent, so a book's own folders
    (``Title/Part 01.m4b``) survive while the library path above them does not.
    """
    files = [Path(path) for path in paths]
    root = Path(os.path.commonpath([str(file.parent) for file in files]))
    return [(file, file.relative_to(root).as_posix()) for file in files]


def _zip_info_from_stat(result: os.stat_result, arcname: str) -> zipfile.ZipInfo:
    """Build an entry the way ZipInfo.from_file does, from a stat of the open file."""
    date_time = time.localtime(result.st_mtime)[0:6]
    if date_time[0] < 1980:
        date_time = (1980, 1, 1, 0, 0, 0)
    elif date_time[0] > 2107:
        date_time = (2107, 12, 31, 23, 59, 59)
    info = zipfile.ZipInfo(arcname, date_time)
    info.external_attr = (result.st_mode & 0xFFFF) << 16
    info.file_size = result.st_size
    return info


def stream_zip(
    paths: list[str], opener: Callable[[str], BinaryIO] | None = None
) -> Iterator[bytes]:
    """Yield an uncompressed ZIP of ``paths``, reading each file once in chunks.

    Stored, not deflated: audiobooks and ebooks are already compressed, so deflating
    them would cost CPU for next to nothing. ZIP64 kicks in per entry from the file
    size, which a multi-gigabyte audiobook needs.

    ``opener``, when given, opens each path in place of a plain open, one file at a
    time; it raises OSError to stop the archive there.
    """
    sink = _ChunkSink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path, arcname in zip_entry_names(paths):
            opened = opener(str(path)) if opener is not None else path.open("rb")
            with opened as source:
                # From the open file, so a path swapped after it was opened cannot change
                # the entry's size or time, or vanish mid-stream.
                info = _zip_info_from_stat(os.fstat(source.fileno()), arcname)
                info.compress_type = zipfile.ZIP_STORED
                with archive.open(info, "w") as target:
                    while chunk := source.read(_ZIP_STREAM_CHUNK_SIZE):
                        target.write(chunk)
                        yield from sink.drain()
            yield from sink.drain()
    yield from sink.drain()
