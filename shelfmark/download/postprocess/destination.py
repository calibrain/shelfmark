"""Destination planning helpers for post-processing outputs."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import (
    get_destination,
)
from shelfmark.core.utils import (
    is_audiobook as check_audiobook,
)
from shelfmark.download.fs import (
    clear_delete_denied,
    mark_delete_denied,
    run_blocking_io,
)
from shelfmark.download.permissions_debug import log_path_permission_context
from shelfmark.release_sources import get_source

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shelfmark.core.models import DownloadTask

logger = setup_logger("shelfmark.download.postprocess.pipeline")

_WRITE_PROBE_NAME = ".shelfmark_write_test.tmp"


def validate_destination(
    destination: Path, status_callback: Callable[[str, str | None], None]
) -> bool:
    """Validate destination path is absolute, exists, and writable."""
    if not destination.is_absolute():
        logger.warning("Destination must be absolute: %s", destination)
        status_callback("error", f"Destination must be absolute: {destination}")
        return False

    destination_exists = run_blocking_io(destination.exists)
    if destination_exists and not run_blocking_io(destination.is_dir):
        logger.warning("Destination is not a directory: %s", destination)
        status_callback("error", f"Destination is not a directory: {destination}")
        return False

    created_by_us = False
    if not destination_exists:
        try:
            run_blocking_io(destination.mkdir, parents=True, exist_ok=True)
            created_by_us = True
        except (OSError, PermissionError) as exc:
            log_path_permission_context("destination_create", destination)
            logger.warning("Cannot create destination: %s (%s)", destination, exc)
            status_callback("error", f"Cannot create destination: {destination} ({exc})")
            return False

    # Stable name: on shares that refuse deletes the probe file cannot be cleaned
    # up, so reusing one name bounds the leftovers at a single hidden file.
    test_path = destination / _WRITE_PROBE_NAME

    try:
        test_content = (
            f"This file was created to verify if '{destination}' is writable. "
            "It should've been automatically deleted. Feel free to delete it.\n"
        )
        run_blocking_io(test_path.write_text, test_content)
    except OSError as exc:
        logger.debug("Destination write probe path: %s", test_path)
        log_path_permission_context("destination_write_probe", destination)
        logger.warning("Destination not writable: %s (%s)", destination, exc)
        status_callback("error", f"Destination not writable: {destination} ({exc})")
        if created_by_us:
            with contextlib.suppress(OSError):
                run_blocking_io(destination.rmdir)
        return False

    try:
        run_blocking_io(test_path.unlink, missing_ok=True)
    except OSError as exc:
        # Writable but not deletable, e.g. a Synology share with "Delete
        # subfolders and files" unticked. Not fatal: record it so transfers write
        # files in place instead of publishing a temp file via rename.
        mark_delete_denied(destination)
        logger.warning(
            "Destination %s is writable but refuses deletes (%s); leaving probe file %s "
            "behind and writing files in place",
            destination,
            exc,
            test_path.name,
        )
    else:
        clear_delete_denied(destination)

    return True


def get_final_destination(task: DownloadTask) -> Path:
    """Get final destination directory, with content-type routing support."""
    is_audiobook = check_audiobook(task.content_type)

    try:
        override = get_source(task.source).get_destination_override(task)
    except ValueError:
        override = None

    if override:
        return override

    return get_destination(
        is_audiobook=is_audiobook,
        user_id=task.user_id,
        username=task.username,
    )
