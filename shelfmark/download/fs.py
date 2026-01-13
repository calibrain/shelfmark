"""Atomic filesystem operations for concurrent-safe file handling.

These utilities handle file collisions atomically, avoiding TOCTOU race conditions
when multiple workers may try to write to the same path simultaneously.
"""

import errno
import os
import shutil
import subprocess
from pathlib import Path

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)


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
            fd = os.open(str(try_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
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
    logger.info(f"Attempting system {op} as final fallback: {source} -> {dest}")
    subprocess.run(
        [op, "-f", str(source), str(dest)],
        check=True,
        capture_output=True,
        text=True
    )


def _perform_nfs_fallback(source: Path, dest: Path, is_move: bool) -> None:
    """Handle NFS permission errors by falling back to copyfile -> system op."""
    try:
        # Fallback 1: copy content only
        shutil.copyfile(str(source), str(dest))
        
        if is_move:
             # Verify copy success before removing source
            if dest.exists() and dest.stat().st_size == source.stat().st_size:
                source.unlink()
                return
            else:
                raise IOError(f"Copy verification failed for {source} -> {dest}")

    except Exception as copy_error:
        # Clean up failed copy attempt if it exists
        if dest.exists():
            dest.unlink(missing_ok=True)
            
        logger.error(f"Fallback copyfile failed: {copy_error}")
        
        # Fallback 2: system command
        op = "mv" if is_move else "cp"
        try:
            _system_op(op, source, dest)
        except subprocess.CalledProcessError as sys_error:
            logger.error(f"System {op} failed: {sys_error.stderr}")
            dest.unlink(missing_ok=True)
            raise


def atomic_move(source_path: Path, dest_path: Path, max_attempts: int = 100) -> Path:
    """Move a file with collision detection.

    Uses os.rename() for same-filesystem moves (atomic, triggers inotify events),
    falls back to exclusive create + shutil.move for cross-filesystem moves.

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
        if try_path.exists():
            continue

        try:
            # os.rename is atomic on same filesystem and triggers inotify events
            os.rename(str(source_path), str(try_path))
            if attempt > 0:
                logger.info(f"File collision resolved: {try_path.name}")
            return try_path
        except FileExistsError:
            # Race condition: file created between exists() check and rename()
            continue
        except OSError as e:
            # Cross-filesystem - fall back to exclusive create + move
            if e.errno != errno.EXDEV:
                raise
            try:
                fd = os.open(str(try_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                try:
                    shutil.move(str(source_path), str(try_path))
                    if attempt > 0:
                        logger.info(f"File collision resolved: {try_path.name}")
                    return try_path
                except Exception:
                    # Clean up the placeholder if move failed
                    if try_path.exists() and try_path.stat().st_size == 0:
                         try_path.unlink(missing_ok=True)
                    raise
            except FileExistsError:
                continue
            except (PermissionError, OSError) as e:
                # Handle NFS permission errors (e.g. inability to set metadata)
                if _is_permission_error(e):
                    logger.debug(f"Permission error during move, falling back to copyfile: {e}")
                    try:
                        _perform_nfs_fallback(source_path, try_path, is_move=True)
                        if attempt > 0:
                            logger.info(f"File collision resolved (fallback): {try_path.name}")
                        return try_path
                    except Exception as fallback_error:
                        # Fallback failed, raise original error
                        raise e
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
            os.link(str(source_path), str(try_path))
            if attempt > 0:
                logger.info(f"File collision resolved: {try_path.name}")
            return try_path
        except FileExistsError:
            continue

    raise RuntimeError(f"Could not create hardlink after {max_attempts} attempts: {dest_path}")


def atomic_copy(source_path: Path, dest_path: Path, max_attempts: int = 100) -> Path:
    """Copy a file with atomic collision detection.

    Uses exclusive create to claim destination, then copies via temp file
    to avoid partial files on failure.

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

    for attempt in range(max_attempts):
        try_path = dest_path if attempt == 0 else parent / f"{base}_{attempt}{ext}"
        try:
            # Atomically claim the destination by creating an exclusive file
            fd = os.open(str(try_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            
            # Copy to temp file first, then replace to avoid partial files
            temp_path = try_path.parent / f".{try_path.name}.tmp"
            try:
                try:
                    shutil.copy2(str(source_path), str(temp_path))
                except (PermissionError, OSError) as e:
                    # Handle NFS permission errors immediately here
                    if _is_permission_error(e):
                        logger.debug(f"Permission error during copy, falling back to copyfile: {e}")
                        try:
                            _perform_nfs_fallback(source_path, temp_path, is_move=False)
                        except Exception as fallback_error:
                             raise e
                    else:
                        raise
                
                temp_path.replace(try_path)
                if attempt > 0:
                    logger.info(f"File collision resolved: {try_path.name}")
                return try_path
            except Exception:
                try_path.unlink(missing_ok=True)
                temp_path.unlink(missing_ok=True)
                raise
        except FileExistsError:
            continue

    raise RuntimeError(f"Could not copy file after {max_attempts} attempts: {dest_path}")
