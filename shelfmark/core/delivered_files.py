"""Identity of a delivered file, so a local download sends only what Shelfmark delivered.

The path a download was recorded under is not enough on its own: a different file can
appear there later, or the path can lead somewhere the download never wrote. The identity
is taken when the download finishes and checked against the open file before it is sent,
so a file swapped in after the check cannot be sent in its place.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import BinaryIO

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)


def _identity_of(result: os.stat_result) -> str:
    # Inode, size and modification time. The device number is left out: it can change
    # when a filesystem is mounted again, which would refuse every earlier download.
    return f"{result.st_ino}:{result.st_size}:{result.st_mtime_ns}"


def file_identity(path: str | os.PathLike[str]) -> str | None:
    """Return the identity of the regular file at ``path``, or None if there is none."""
    try:
        result = Path(path).stat()
    except OSError:
        return None
    if not stat.S_ISREG(result.st_mode):
        return None
    return _identity_of(result)


def open_delivered_file(path: str | None, identity: str | None) -> BinaryIO | None:
    """Open ``path`` for reading only if it is still the file recorded as ``identity``.

    Returns None, after logging why, when there is no recorded identity, the file is gone,
    or it is no longer the same file. The caller sends the returned file and closes it.
    """
    if not path or not identity:
        logger.warning("Local download refused: no delivered file is recorded for %s", path)
        return None
    try:
        # Non-blocking, so a pipe swapped in at the path cannot hang the request; it
        # changes nothing for a regular file, and the check below refuses anything else.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        logger.warning("Local download refused: cannot open %s: %s", path, exc)
        return None
    try:
        current = os.fstat(fd)
        if stat.S_ISREG(current.st_mode) and _identity_of(current) == identity:
            return os.fdopen(fd, "rb")  # handed to the caller, who closes it
    except OSError:
        pass
    os.close(fd)
    logger.warning("Local download refused: %s is not the file that was delivered", path)
    return None
