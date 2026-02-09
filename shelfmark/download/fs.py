"""Atomic filesystem operations for concurrent-safe file handling.

These utilities handle file collisions atomically, avoiding TOCTOU race conditions
when multiple workers may try to write to the same path simultaneously.
"""

import errno
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, cast

from shelfmark.core.logger import setup_logger
from shelfmark.download.permissions_debug import log_transfer_permission_context

logger = setup_logger(__name__)

try:
    from gevent import monkey as _gevent_monkey
    from gevent.threadpool import ThreadPool as _GeventThreadPool
except Exception:
    _gevent_monkey = None
    _GeventThreadPool = None

T = TypeVar("T")
_IO_THREADPOOL: Optional["_GeventThreadPool"] = None


def _use_gevent_threadpool() -> bool:
    return bool(
        _gevent_monkey
        and _GeventThreadPool
        and _gevent_monkey.is_module_patched("threading")
    )


def _get_io_threadpool() -> "_GeventThreadPool":
    global _IO_THREADPOOL
    if _IO_THREADPOOL is None:
        pool_size = max(2, min(8, os.cpu_count() or 2))
        _IO_THREADPOOL = _GeventThreadPool(pool_size)
    return _IO_THREADPOOL


def _call_and_capture(func: Callable[..., T], args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[bool, T | Exception]:
    try:
        return True, func(*args, **kwargs)
    except Exception as exc:
        return False, exc


def run_blocking_io(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run blocking I/O in a native thread when under gevent.

    gevent's threadpool will eagerly log exceptions raised inside worker threads,
    even when the caller expects and handles those errors (e.g. FileExistsError for
    collision retries, EXDEV for cross-device moves). Capture and re-raise in the
    caller to avoid noisy, misleading tracebacks.
    """
    if _use_gevent_threadpool():
        ok, result = _get_io_threadpool().apply(_call_and_capture, (func, args, kwargs))
        if ok:
            return cast(T, result)
        exc = cast(Exception, result)
        raise exc
    return func(*args, **kwargs)



_VERIFY_IO_WAIT_SECONDS = 3.0


def _verify_transfer_size(
    dest: Path,
    expected_size: int,
    action: str,
) -> None:
    """Verify file transfer completed successfully.

    Some filesystems (especially remote NAS/CIFS/NFS) can report stale sizes briefly
    after large writes. Do a second stat after a short delay before declaring failure.
    """
    # On network filesystems, `stat()` can block long enough to starve the gevent hub.
    actual_size = run_blocking_io(dest.stat).st_size
    if actual_size == expected_size:
        return

    logger.debug(
        f"File {action} size mismatch, waiting for filesystem sync: {dest} "
        f"({actual_size} != {expected_size})"
    )
    time.sleep(_VERIFY_IO_WAIT_SECONDS)

    actual_size = run_blocking_io(dest.stat).st_size
    if actual_size != expected_size:
        raise IOError(
            f"File {action} incomplete, data loss may have occurred. "
            f"'{dest}' was {actual_size} bytes instead of expected {expected_size}."
        )


def atomic_write(dest_path: Path, data: bytes, max_attempts: int = 100) -> Path:
    """Write data to a file with atomic collision detection.

    If the destination already exists, retries with counter suffix (_1, _2, etc.)
    until a unique path is found.

    Args:
        dest_path: Desired destination path
        data: Bytes to write
        max_attempts: Maximum collision retries before raising error

    Returns:
        Path where file was actually written (may differ from dest_path)

    Raises:
        RuntimeError: If no unique path found after max_attempts
    """
    base = dest_path.stem
    ext = dest_path.suffix
    parent = dest_path.parent

    for attempt in range(max_attempts):
        try_path = dest_path if attempt == 0 else parent / f"{base}_{attempt}{ext}"
        try:
            # O_CREAT | O_EXCL fails atomically if file exists
            fd = run_blocking_io(
                os.open,
                str(try_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o666,
            )
            try:
                run_blocking_io(os.write, fd, data)
            finally:
                run_blocking_io(os.close, fd)
            if attempt > 0:
                logger.info(f"File collision resolved: {try_path.name}")
            return try_path
        except FileExistsError:
            continue

    raise RuntimeError(f"Could not write file after {max_attempts} attempts: {dest_path}")


def _is_permission_error(e: Exception) -> bool:
    """Check if exception is a permission error (including NFS/SMB issues)."""
    return isinstance(e, PermissionError) or (isinstance(e, OSError) and e.errno == errno.EPERM)


def _system_op(op: str, source: Path, dest: Path) -> None:
    """Execute system command (mv or cp) as final fallback."""
    logger.warning("Attempting system %s as final fallback: %s -> %s", op, source, dest)
    run_blocking_io(
        subprocess.run,
        [op, "-f", str(source), str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )


def _perform_nfs_fallback(source: Path, dest: Path, is_move: bool) -> None:
    """Handle NFS/SMB permission errors by falling back to copyfile -> system op."""
    expected_size = run_blocking_io(source.stat).st_size

    try:
        # Fallback 1: copy content only
        run_blocking_io(shutil.copyfile, str(source), str(dest))
        _verify_transfer_size(dest, expected_size, "copy")

        if is_move:
            run_blocking_io(source.unlink)
        return

    except Exception as copy_error:
        # Clean up failed copy attempt if it exists
        run_blocking_io(dest.unlink, missing_ok=True)

        if _is_permission_error(copy_error):
            log_transfer_permission_context("nfs_fallback_copyfile", source=source, dest=dest, error=copy_error)
        logger.error("Fallback copyfile failed (%s -> %s): %s", source, dest, copy_error)

        # Fallback 2: system command
        op = "mv" if is_move else "cp"
        try:
            _system_op(op, source, dest)
            # Best-effort verify after external command.
            if run_blocking_io(dest.exists):
                _verify_transfer_size(dest, expected_size, op)
            if is_move:
                run_blocking_io(source.unlink, missing_ok=True)
        except subprocess.CalledProcessError as sys_error:
            log_transfer_permission_context("nfs_fallback_system", source=source, dest=dest, error=sys_error)
            logger.error("System %s failed (%s -> %s): %s", op, source, dest, sys_error.stderr)
            run_blocking_io(dest.unlink, missing_ok=True)
            raise


def _claim_destination(path: Path) -> bool:
    """Atomically claim a destination path by creating a placeholder file.

    Returns True if the placeholder was created. Caller must replace or unlink it.
    """
    try:
        fd = run_blocking_io(
            os.open,
            str(path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o666,
        )
    except FileExistsError:
        return False
    else:
        run_blocking_io(os.close, fd)
        return True


def _hardlink_not_supported(error: OSError) -> bool:
    err = error.errno
    return err in {
        errno.EXDEV,
        errno.EMLINK,
        errno.EPERM,
        errno.EACCES,
        getattr(errno, "ENOTSUP", errno.EPERM),
        getattr(errno, "EOPNOTSUPP", errno.EPERM),
        errno.EINVAL,
    }


def _create_temp_path(dest_path: Path) -> Path:
    fd, temp_path = run_blocking_io(
        tempfile.mkstemp,
        prefix=f".{dest_path.name}.",
        suffix=".tmp",
        dir=str(dest_path.parent),
    )
    run_blocking_io(os.close, fd)
    return Path(temp_path)


def _publish_temp_file(temp_path: Path, dest_path: Path) -> bool:
    """Publish a temp file to its final path without overwriting existing files.

    Returns True on success, False if the destination already exists.
    """
    try:
        run_blocking_io(os.link, str(temp_path), str(dest_path))
        run_blocking_io(temp_path.unlink, missing_ok=True)
        return True
    except FileExistsError:
        return False
    except OSError as e:
        if _is_permission_error(e):
            log_transfer_permission_context(
                "publish_hardlink",
                source=temp_path,
                dest=dest_path,
                error=e,
            )
        if _hardlink_not_supported(e):
            logger.debug(
                "Hardlink publish unsupported; falling back to claim+replace: %s -> %s (%s)",
                temp_path,
                dest_path,
                e,
            )
            claimed = _claim_destination(dest_path)
            if not claimed:
                return False
            try:
                run_blocking_io(os.replace, str(temp_path), str(dest_path))
            except Exception:
                run_blocking_io(dest_path.unlink, missing_ok=True)
                raise
            return True
        raise


def atomic_move(source_path: Path, dest_path: Path, max_attempts: int = 100) -> Path:
    """Move a file with collision detection.

    Uses os.rename() for same-filesystem moves (atomic, triggers inotify events),
    falls back to copy-then-publish for cross-filesystem moves.

    Note: We use os.rename() instead of hardlink+unlink because os.rename()
    triggers proper inotify IN_MOVED_TO events that file watchers (like Calibre's
    auto-add) rely on to detect new files.

    Args:
        source_path: Source file to move
        dest_path: Desired destination path
        max_attempts: Maximum collision retries before raising error

    Returns:
        Path where file was actually moved (may differ from dest_path)

    Raises:
        RuntimeError: If no unique path found after max_attempts
    """
    base = dest_path.stem
    ext = dest_path.suffix
    parent = dest_path.parent

    for attempt in range(max_attempts):
        try_path = dest_path if attempt == 0 else parent / f"{base}_{attempt}{ext}"

        # Check for existing file (os.rename would overwrite on Unix)
        claimed = False
        if run_blocking_io(try_path.exists):
            # Some filesystems can report false positives for exists() with
            # special characters. Probe with O_EXCL to confirm.
            claimed = _claim_destination(try_path)
            if not claimed:
                continue

        try:
            # os.rename is atomic on same filesystem and triggers inotify events
            if claimed:
                run_blocking_io(os.replace, str(source_path), str(try_path))
            else:
                run_blocking_io(os.rename, str(source_path), str(try_path))
            if attempt > 0:
                logger.info(f"File collision resolved: {try_path.name}")
            return try_path
        except FileExistsError:
            # Race condition: file created between exists() check and rename()
            if claimed:
                run_blocking_io(try_path.unlink, missing_ok=True)
            continue
        except OSError as e:
            # Cross-filesystem - copy to temp and publish atomically.
            if e.errno != errno.EXDEV:
                if claimed:
                    run_blocking_io(try_path.unlink, missing_ok=True)
                raise

            expected_size = run_blocking_io(source_path.stat).st_size
            if claimed:
                run_blocking_io(try_path.unlink, missing_ok=True)
                claimed = False

            temp_path: Optional[Path] = None
            try:
                try:
                    temp_path = _create_temp_path(try_path)
                    try:
                        run_blocking_io(shutil.copy2, str(source_path), str(temp_path))
                    except (PermissionError, OSError) as copy_error:
                        if _is_permission_error(copy_error):
                            logger.debug(
                                "Permission error during move-copy, falling back to copyfile (%s -> %s): %s",
                                source_path,
                                temp_path,
                                copy_error,
                            )
                            _perform_nfs_fallback(source_path, temp_path, is_move=False)
                        else:
                            raise

                    _verify_transfer_size(temp_path, expected_size, "move")
                    published = _publish_temp_file(temp_path, try_path)
                    if not published:
                        run_blocking_io(temp_path.unlink, missing_ok=True)
                        continue

                    try:
                        _verify_transfer_size(try_path, expected_size, "move")
                    except Exception:
                        run_blocking_io(try_path.unlink, missing_ok=True)
                        raise

                    run_blocking_io(source_path.unlink)

                    if attempt > 0:
                        logger.info(f"File collision resolved: {try_path.name}")
                    return try_path

                except FileExistsError:
                    if temp_path:
                        run_blocking_io(temp_path.unlink, missing_ok=True)
                    continue
                except Exception:
                    if temp_path:
                        run_blocking_io(temp_path.unlink, missing_ok=True)
                    raise

            except (PermissionError, OSError) as e:
                if _is_permission_error(e):
                    log_transfer_permission_context(
                        "atomic_move",
                        source=source_path,
                        dest=try_path,
                        error=e,
                    )
                    logger.debug(
                        "Permission error during move, falling back to copyfile (%s -> %s): %s",
                        source_path,
                        try_path,
                        e,
                    )
                    try:
                        _perform_nfs_fallback(source_path, try_path, is_move=True)
                        if attempt > 0:
                            logger.info(f"File collision resolved (fallback): {try_path.name}")
                        return try_path
                    except Exception as fallback_error:
                        logger.error(
                            "NFS fallback also failed (%s -> %s): %s",
                            source_path,
                            try_path,
                            fallback_error,
                        )
                        raise e from fallback_error
                raise

    raise RuntimeError(f"Could not move file after {max_attempts} attempts: {dest_path}")


def atomic_hardlink(source_path: Path, dest_path: Path, max_attempts: int = 100) -> Path:
    """Create a hardlink with atomic collision detection.

    Args:
        source_path: Source file to link from
        dest_path: Desired destination path for the link
        max_attempts: Maximum collision retries before raising error

    Returns:
        Path where link was actually created (may differ from dest_path)

    Raises:
        RuntimeError: If no unique path found after max_attempts
    """
    base = dest_path.stem
    ext = dest_path.suffix
    parent = dest_path.parent

    for attempt in range(max_attempts):
        try_path = dest_path if attempt == 0 else parent / f"{base}_{attempt}{ext}"
        try:
            run_blocking_io(os.link, str(source_path), str(try_path))
            if attempt > 0:
                logger.info(f"File collision resolved: {try_path.name}")
            return try_path
        except FileExistsError:
            continue
        except OSError as e:
            if _is_permission_error(e) or e.errno in (errno.EXDEV, errno.EMLINK):
                if _is_permission_error(e):
                    log_transfer_permission_context(
                        "atomic_hardlink",
                        source=source_path,
                        dest=try_path,
                        error=e,
                    )
                logger.debug(
                    "Hardlink failed (%s), falling back to copy: %s -> %s",
                    e,
                    source_path,
                    dest_path,
                )
                return atomic_copy(source_path, dest_path, max_attempts=max_attempts)
            raise

    raise RuntimeError(f"Could not create hardlink after {max_attempts} attempts: {dest_path}")


def atomic_copy(source_path: Path, dest_path: Path, max_attempts: int = 100) -> Path:
    """Copy a file with atomic collision detection.

    Uses a temp file in the destination directory and publishes it atomically,
    avoiding partial files on failure.

    Args:
        source_path: Source file to copy
        dest_path: Desired destination path
        max_attempts: Maximum collision retries before raising error

    Returns:
        Path where file was actually copied (may differ from dest_path)

    Raises:
        RuntimeError: If no unique path found after max_attempts
    """
    base = dest_path.stem
    ext = dest_path.suffix
    parent = dest_path.parent
    expected_size = run_blocking_io(source_path.stat).st_size

    for attempt in range(max_attempts):
        try_path = dest_path if attempt == 0 else parent / f"{base}_{attempt}{ext}"
        if run_blocking_io(try_path.exists):
            continue
        temp_path: Optional[Path] = None
        try:
            temp_path = _create_temp_path(try_path)
            try:
                run_blocking_io(shutil.copy2, str(source_path), str(temp_path))
            except (PermissionError, OSError) as e:
                # Handle NFS permission errors immediately here
                if _is_permission_error(e):
                    log_transfer_permission_context(
                        "atomic_copy",
                        source=source_path,
                        dest=temp_path,
                        error=e,
                    )
                    logger.debug(
                        "Permission error during copy, falling back to copyfile (%s -> %s): %s",
                        source_path,
                        temp_path,
                        e,
                    )
                    try:
                        _perform_nfs_fallback(source_path, temp_path, is_move=False)
                    except Exception as fallback_error:
                        logger.error(
                            "NFS fallback also failed (%s -> %s): %s",
                            source_path,
                            temp_path,
                            fallback_error,
                        )
                        raise e from fallback_error
                else:
                    raise

            _verify_transfer_size(temp_path, expected_size, "copy")
            published = _publish_temp_file(temp_path, try_path)
            if not published:
                run_blocking_io(temp_path.unlink, missing_ok=True)
                continue

            try:
                _verify_transfer_size(try_path, expected_size, "copy")
            except Exception:
                run_blocking_io(try_path.unlink, missing_ok=True)
                raise

            if attempt > 0:
                logger.info(f"File collision resolved: {try_path.name}")
            return try_path
        except Exception:
            if temp_path:
                run_blocking_io(temp_path.unlink, missing_ok=True)
            raise

    raise RuntimeError(f"Could not copy file after {max_attempts} attempts: {dest_path}")
