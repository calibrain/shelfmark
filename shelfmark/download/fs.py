"""Atomic filesystem operations for concurrent-safe file handling.

These utilities handle file collisions atomically, avoiding TOCTOU race conditions
when multiple workers may try to write to the same path simultaneously.
"""

import contextlib
import errno
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

from shelfmark.core.logger import setup_logger
from shelfmark.download.permissions_debug import log_transfer_permission_context

if TYPE_CHECKING:
    from collections.abc import Callable

    from gevent.threadpool import ThreadPool

logger = setup_logger(__name__)

try:
    from gevent import monkey as _gevent_monkey
    from gevent.threadpool import ThreadPool as _GeventThreadPool
except ImportError:
    _gevent_monkey = None
    _GeventThreadPool = None

T = TypeVar("T")
_IO_THREADPOOL: ThreadPool | None = None


def _use_gevent_threadpool() -> bool:
    return bool(
        _gevent_monkey and _GeventThreadPool and _gevent_monkey.is_module_patched("threading")
    )


def _get_io_threadpool() -> ThreadPool:
    global _IO_THREADPOOL
    if _IO_THREADPOOL is None:
        pool_size = max(2, min(8, os.cpu_count() or 2))
        threadpool_cls = _GeventThreadPool
        if threadpool_cls is None:
            msg = "gevent threadpool is unavailable"
            raise RuntimeError(msg)
        _IO_THREADPOOL = threadpool_cls(pool_size)
    return _IO_THREADPOOL


def _call_and_capture[T](
    func: Callable[..., T], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[bool, T | Exception]:
    try:
        return True, func(*args, **kwargs)
    except (
        AttributeError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        return False, exc


def _must_avoid_gevent_threadpool(func: Callable[..., Any]) -> bool:
    """Return True when `func` is unsafe to execute inside gevent's threadpool."""
    if not _use_gevent_threadpool() or not _gevent_monkey:
        return False

    # gevent.subprocess requires child watchers on the default event loop.
    # Executing patched subprocess functions in a worker thread can raise:
    # "TypeError: child watchers are only available on the default loop".
    return _gevent_monkey.is_object_patched("subprocess", "run") and func is subprocess.run


def run_blocking_io[T](func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run blocking I/O in a native thread when under gevent.

    gevent's threadpool will eagerly log exceptions raised inside worker threads,
    even when the caller expects and handles those errors (e.g. FileExistsError for
    collision retries, EXDEV for cross-device moves). Capture and re-raise in the
    caller to avoid noisy, misleading tracebacks.
    """
    if _must_avoid_gevent_threadpool(func):
        return func(*args, **kwargs)

    if _use_gevent_threadpool():
        ok, result = _get_io_threadpool().apply(_call_and_capture, (func, args, kwargs))
        if ok:
            return cast("T", result)
        exc = cast("Exception", result)
        raise exc
    return func(*args, **kwargs)


_VERIFY_IO_WAIT_SECONDS = 3.0
_PUBLISH_VERIFY_RETRY_SECONDS = 0.25
_TEMPFILE_PREFIX = ".shelfmark."
_TEMPFILE_SUFFIX = ".tmp"

# Destinations that accept writes but reject unlink/rename, e.g. a Synology share
# with "Delete subfolders and files" unticked. Publishing a temp file into place
# removes a directory entry, so those paths must be written in place instead.
_DELETE_DENIED_DIRS: set[str] = set()


class _PublishDeniedError(Exception):
    """A fully-written temp file could not be renamed onto its final path."""


def _is_delete_denied_error(error: Exception) -> bool:
    return isinstance(error, OSError) and error.errno in {errno.EACCES, errno.EPERM}


def mark_delete_denied(directory: Path) -> None:
    """Record that `directory` rejects deletes so later writes skip the temp file."""
    key = str(directory)
    if key in _DELETE_DENIED_DIRS:
        return
    _DELETE_DENIED_DIRS.add(key)
    logger.warning(
        "Destination %s rejects delete/rename; writing files in place instead of "
        "publishing atomically. Grant delete permission to restore atomic writes.",
        directory,
    )


def clear_delete_denied(directory: Path) -> None:
    """Forget recorded denials for `directory` and anything beneath it.

    Subdirectories get marked independently (an `organize` layout publishes into
    per-author folders), so clearing only the exact key would leave a fixed
    destination writing in place until restart.
    """
    if not _DELETE_DENIED_DIRS:
        return
    key = str(directory)
    prefix = f"{key}{os.sep}"
    _DELETE_DENIED_DIRS.difference_update(
        {marked for marked in _DELETE_DENIED_DIRS if marked == key or marked.startswith(prefix)}
    )


def is_delete_denied(directory: Path) -> bool:
    """True if `directory` or one of its ancestors is known to reject deletes."""
    if not _DELETE_DENIED_DIRS:
        return False
    if str(directory) in _DELETE_DENIED_DIRS:
        return True
    return any(str(parent) in _DELETE_DENIED_DIRS for parent in directory.parents)


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
        "File %s size mismatch, waiting for filesystem sync: %s (%s != %s)",
        action,
        dest,
        actual_size,
        expected_size,
    )
    time.sleep(_VERIFY_IO_WAIT_SECONDS)

    actual_size = run_blocking_io(dest.stat).st_size
    if actual_size != expected_size:
        msg = (
            f"File {action} incomplete, data loss may have occurred. "
            f"'{dest}' was {actual_size} bytes instead of expected {expected_size}."
        )
        raise OSError(msg)


def _is_stale_handle_error(error: Exception) -> bool:
    return isinstance(error, OSError) and error.errno == getattr(errno, "ESTALE", 116)


def _verify_published_file(
    dest: Path,
    expected_size: int,
    action: str,
) -> None:
    """Best-effort verify after publishing a temp file into place.

    The temp file was already verified before publish. Some NFS mounts can report
    a transient stale handle immediately after `os.replace()` makes the final path
    visible, so retry once and then trust the successful publish instead of
    turning the handoff into a false failure.
    """
    try:
        _verify_transfer_size(dest, expected_size, action)
    except OSError as error:
        if not _is_stale_handle_error(error):
            raise
    else:
        return

    time.sleep(_PUBLISH_VERIFY_RETRY_SECONDS)

    try:
        _verify_transfer_size(dest, expected_size, action)
    except OSError as retry_error:
        if not _is_stale_handle_error(retry_error):
            raise
        logger.warning(
            "Skipping post-publish verification for %s after stale handle on %s: %s",
            action,
            dest,
            retry_error,
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
                logger.info("File collision resolved: %s", try_path.name)
        except FileExistsError:
            continue
        else:
            return try_path

    msg = f"Could not write file after {max_attempts} attempts: {dest_path}"
    raise RuntimeError(msg)


def _is_permission_error(e: Exception) -> bool:
    """Check if exception is a permission error (including NFS/SMB issues)."""
    return isinstance(e, PermissionError) or (isinstance(e, OSError) and e.errno == errno.EPERM)


def _should_fallback_to_content_copy(error: Exception) -> bool:
    return _is_permission_error(error) or (isinstance(error, OSError) and error.errno == errno.EIO)


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


def _perform_nfs_fallback(source: Path, dest: Path, *, is_move: bool) -> None:
    """Handle NFS/SMB permission errors by falling back to copyfile -> system op."""
    expected_size = run_blocking_io(source.stat).st_size

    try:
        # Fallback 1: copy content only
        run_blocking_io(shutil.copyfile, str(source), str(dest))
        _verify_transfer_size(dest, expected_size, "copy")

        if is_move:
            run_blocking_io(source.unlink)

    except Exception as copy_error:
        # Clean up failed copy attempt if it exists
        run_blocking_io(dest.unlink, missing_ok=True)

        if _is_permission_error(copy_error):
            log_transfer_permission_context(
                "nfs_fallback_copyfile", source=source, dest=dest, error=copy_error
            )
        logger.exception("Fallback copyfile failed (%s -> %s)", source, dest)

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
            log_transfer_permission_context(
                "nfs_fallback_system", source=source, dest=dest, error=sys_error
            )
            logger.exception("System %s failed (%s -> %s): %s", op, source, dest, sys_error.stderr)
            run_blocking_io(dest.unlink, missing_ok=True)
            raise
    else:
        return


def _is_enoent_error(error: Exception) -> bool:
    return isinstance(error, FileNotFoundError) or (
        isinstance(error, OSError) and error.errno == errno.ENOENT
    )


def _can_use_partial_copy_after_enoent(
    temp_path: Path | None,
    expected_size: int,
    action: str,
) -> bool:
    """Recover when copy2 writes bytes but fails while copying source metadata."""
    if not temp_path or not run_blocking_io(temp_path.exists):
        return False

    try:
        _verify_transfer_size(temp_path, expected_size, action)
    except OSError:
        return False
    else:
        return True


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
        errno.EIO,
        errno.EPERM,
        errno.EACCES,
        getattr(errno, "ENOTSUP", errno.EPERM),
        getattr(errno, "EOPNOTSUPP", errno.EPERM),
        getattr(errno, "ENOSYS", errno.EPERM),
        errno.EINVAL,
    }


def _create_temp_path(dest_path: Path) -> Path:
    """Create a destination-adjacent temp file without inheriting the full basename.

    Reusing the entire destination filename in the temp prefix can push otherwise
    valid long names over the filesystem component limit once `tempfile` adds its
    random suffix.
    """
    fd, temp_path = run_blocking_io(
        tempfile.mkstemp,
        prefix=_TEMPFILE_PREFIX,
        suffix=_TEMPFILE_SUFFIX,
        dir=str(dest_path.parent),
    )
    run_blocking_io(os.close, fd)
    return Path(temp_path)


def _discard_path(path: Path) -> None:
    """Best-effort unlink that tolerates destinations which reject deletes."""
    try:
        run_blocking_io(path.unlink, missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove %s: %s", path, exc)


def _copy_into_claimed(source_path: Path, dest_path: Path, expected_size: int) -> None:
    """Copy content straight into an already-claimed destination path.

    Used when the destination rejects rename/unlink: there is no temp file to
    publish, so the final name is written in place. This is not atomic - a
    watcher can observe a partial file - but it is the only way to deliver on
    such a share. `copyfile` (not `copy2`) because metadata copying needs chmod,
    which those shares also tend to refuse.
    """
    try:
        run_blocking_io(shutil.copyfile, str(source_path), str(dest_path))
        _verify_transfer_size(dest_path, expected_size, "copy")
    except Exception:
        with contextlib.suppress(OSError):
            run_blocking_io(dest_path.unlink, missing_ok=True)
        raise


def _publish_temp_file(temp_path: Path, dest_path: Path) -> bool:
    """Publish a temp file to its final path without overwriting existing files.

    Returns True on success, False if the destination already exists.

    Raises `_PublishDeniedError` when the rename is refused for lack of delete
    permission. The claimed destination is left in place so the caller can write
    into it directly instead.
    """
    claimed = _claim_destination(dest_path)
    if not claimed:
        return False

    try:
        # Publish by renaming the fully-written temp file into place. This gives
        # watchers an IN_MOVED_TO-style event on the final path instead of relying
        # on hardlink support in the destination filesystem.
        try:
            run_blocking_io(os.replace, str(temp_path), str(dest_path))
        except OSError as e:
            if _is_delete_denied_error(e):
                log_transfer_permission_context(
                    "publish_replace",
                    source=temp_path,
                    dest=dest_path,
                    error=e,
                )
                mark_delete_denied(dest_path.parent)
                raise _PublishDeniedError(str(e)) from e
            raise

        # Best-effort nudge for watchers that only react to close-write on the
        # final filename rather than rename/move events.
        try:
            fd = run_blocking_io(os.open, str(dest_path), os.O_WRONLY)
            run_blocking_io(os.close, fd)
        except OSError:
            pass
    except _PublishDeniedError:
        raise
    except Exception as e:
        if _is_permission_error(e):
            log_transfer_permission_context(
                "publish_replace",
                source=temp_path,
                dest=dest_path,
                error=e,
            )
        _discard_path(dest_path)
        raise
    else:
        return True


def _move_via_copy(source_path: Path, dest_path: Path, max_attempts: int) -> Path:
    """Deliver a move as copy + source unlink.

    For destinations that reject rename. The source lives in TMP_DIR (which we
    own and can delete), so only the destination-side semantics change.
    """
    final_path = atomic_copy(source_path, dest_path, max_attempts=max_attempts)
    _discard_path(source_path)
    return final_path


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

    # rename() removes a directory entry, so a destination that refuses deletes
    # cannot be moved into. Deliver it as copy + source unlink instead.
    if is_delete_denied(parent):
        return _move_via_copy(source_path, dest_path, max_attempts)

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
                logger.info("File collision resolved: %s", try_path.name)
        except FileExistsError:
            # Race condition: file created between exists() check and rename()
            if claimed:
                run_blocking_io(try_path.unlink, missing_ok=True)
            continue
        except OSError as e:
            if _is_delete_denied_error(e):
                # Destination refuses the rename; fall back to copy + unlink source.
                mark_delete_denied(parent)
                if claimed:
                    _discard_path(try_path)
                return _move_via_copy(source_path, dest_path, max_attempts)

            # Cross-filesystem - copy to temp and publish atomically.
            if e.errno != errno.EXDEV:
                if claimed:
                    run_blocking_io(try_path.unlink, missing_ok=True)
                raise

            expected_size = run_blocking_io(source_path.stat).st_size
            if claimed:
                run_blocking_io(try_path.unlink, missing_ok=True)
                claimed = False

            temp_path: Path | None = None
            try:
                try:
                    temp_path = _create_temp_path(try_path)
                    try:
                        run_blocking_io(shutil.copy2, str(source_path), str(temp_path))
                    except (PermissionError, OSError) as copy_error:
                        if _should_fallback_to_content_copy(copy_error):
                            logger.debug(
                                "copy2 failed during move-copy, falling back to copyfile (%s -> %s): %s",
                                source_path,
                                temp_path,
                                copy_error,
                            )
                            _perform_nfs_fallback(source_path, temp_path, is_move=False)
                        elif _is_enoent_error(copy_error) and _can_use_partial_copy_after_enoent(
                            temp_path,
                            expected_size,
                            "move",
                        ):
                            logger.warning(
                                "Source vanished during move-copy metadata step; preserving copied data: %s -> %s",
                                source_path,
                                temp_path,
                            )
                        else:
                            raise

                    _verify_transfer_size(temp_path, expected_size, "move")
                    published = _publish_temp_file(temp_path, try_path)
                    if not published:
                        run_blocking_io(temp_path.unlink, missing_ok=True)
                        continue

                    try:
                        _verify_published_file(try_path, expected_size, "move")
                    except Exception:
                        _discard_path(try_path)
                        raise

                    run_blocking_io(source_path.unlink)

                    if attempt > 0:
                        logger.info("File collision resolved: %s", try_path.name)
                except FileExistsError:
                    if temp_path:
                        run_blocking_io(temp_path.unlink, missing_ok=True)
                    continue
                except _PublishDeniedError:
                    # Destination is claimed but unrenameable; write into it directly.
                    _copy_into_claimed(source_path, try_path, expected_size)
                    if temp_path:
                        _discard_path(temp_path)
                    _discard_path(source_path)
                    if attempt > 0:
                        logger.info("File collision resolved: %s", try_path.name)
                    return try_path
                except Exception:
                    if temp_path:
                        _discard_path(temp_path)
                    raise
                else:
                    return try_path

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
                            logger.info("File collision resolved (fallback): %s", try_path.name)
                    except Exception as fallback_error:
                        logger.exception(
                            "NFS fallback also failed (%s -> %s)",
                            source_path,
                            try_path,
                        )
                        raise e from fallback_error
                    else:
                        return try_path
                raise
        else:
            return try_path

    msg = f"Could not move file after {max_attempts} attempts: {dest_path}"
    raise RuntimeError(msg)


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
                logger.info("File collision resolved: %s", try_path.name)
        except FileExistsError:
            continue
        except OSError as e:
            permission_error = _is_permission_error(e)
            if permission_error:
                log_transfer_permission_context(
                    "atomic_hardlink",
                    source=source_path,
                    dest=try_path,
                    error=e,
                )
            if permission_error or _hardlink_not_supported(e):
                logger.warning(
                    "Hardlink failed (%s), falling back to copy: %s -> %s",
                    e,
                    source_path,
                    dest_path,
                )
                return atomic_copy(source_path, dest_path, max_attempts=max_attempts)
            raise
        else:
            return try_path

    msg = f"Could not create hardlink after {max_attempts} attempts: {dest_path}"
    raise RuntimeError(msg)


def atomic_copy(source_path: Path, dest_path: Path, max_attempts: int = 100) -> Path:
    """Copy a file with atomic collision detection.

    Uses a temp file in the destination directory and publishes it via rename,
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

        # Known-undeletable destination: skip the temp file entirely, otherwise
        # every transfer would strand a `.shelfmark.*.tmp` we cannot clean up.
        if is_delete_denied(parent):
            if not _claim_destination(try_path):
                continue
            _copy_into_claimed(source_path, try_path, expected_size)
            if attempt > 0:
                logger.info("File collision resolved: %s", try_path.name)
            return try_path

        temp_path: Path | None = None
        try:
            temp_path = _create_temp_path(try_path)
            try:
                run_blocking_io(shutil.copy2, str(source_path), str(temp_path))
            except (PermissionError, OSError) as e:
                if _should_fallback_to_content_copy(e):
                    if _is_permission_error(e):
                        log_transfer_permission_context(
                            "atomic_copy",
                            source=source_path,
                            dest=temp_path,
                            error=e,
                        )
                    logger.debug(
                        "copy2 failed during copy, falling back to copyfile (%s -> %s): %s",
                        source_path,
                        temp_path,
                        e,
                    )
                    try:
                        _perform_nfs_fallback(source_path, temp_path, is_move=False)
                    except Exception as fallback_error:
                        logger.exception(
                            "NFS fallback also failed (%s -> %s)",
                            source_path,
                            temp_path,
                        )
                        raise e from fallback_error
                elif _is_enoent_error(e) and _can_use_partial_copy_after_enoent(
                    temp_path,
                    expected_size,
                    "copy",
                ):
                    logger.warning(
                        "Source vanished during copy2 metadata step; preserving copied data: %s -> %s",
                        source_path,
                        temp_path,
                    )
                else:
                    raise

            _verify_transfer_size(temp_path, expected_size, "copy")
            published = _publish_temp_file(temp_path, try_path)
            if not published:
                run_blocking_io(temp_path.unlink, missing_ok=True)
                continue

            try:
                _verify_published_file(try_path, expected_size, "copy")
            except Exception:
                _discard_path(try_path)
                raise

            if attempt > 0:
                logger.info("File collision resolved: %s", try_path.name)
        except _PublishDeniedError:
            # The destination is claimed but unrenameable; write into it directly.
            _copy_into_claimed(source_path, try_path, expected_size)
            if temp_path:
                _discard_path(temp_path)
            if attempt > 0:
                logger.info("File collision resolved: %s", try_path.name)
            return try_path
        except Exception:
            if temp_path:
                _discard_path(temp_path)
            raise
        else:
            return try_path

    msg = f"Could not copy file after {max_attempts} attempts: {dest_path}"
    raise RuntimeError(msg)
