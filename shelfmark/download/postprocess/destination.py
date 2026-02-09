from __future__ import annotations

import uuid
from pathlib import Path

from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.core.utils import (
    get_aa_content_type_dir,
    get_destination,
    is_audiobook as check_audiobook,
)
from shelfmark.download.fs import run_blocking_io
from shelfmark.download.permissions_debug import log_path_permission_context

logger = setup_logger("shelfmark.download.postprocess.pipeline")


def validate_destination(destination: Path, status_callback) -> bool:
    """Validate destination path is absolute, exists, and writable."""

    if not destination.is_absolute():
        logger.warning(f"Destination must be absolute: {destination}")
        status_callback("error", f"Destination must be absolute: {destination}")
        return False

    destination_exists = run_blocking_io(destination.exists)
    if destination_exists and not run_blocking_io(destination.is_dir):
        logger.warning(f"Destination is not a directory: {destination}")
        status_callback("error", f"Destination is not a directory: {destination}")
        return False

    if not destination_exists:
        try:
            run_blocking_io(destination.mkdir, parents=True, exist_ok=True)
        except (OSError, PermissionError) as exc:
            log_path_permission_context("destination_create", destination)
            logger.warning(f"Cannot create destination: {destination} ({exc})")
            status_callback("error", f"Cannot create destination: {destination} ({exc})")
            return False

    test_path = destination / f".shelfmark_write_test_{uuid.uuid4().hex}.tmp"

    try:
        test_content = (
            f"This file was created to verify if '{destination}' is writable. "
            "It should've been automatically deleted. Feel free to delete it.\n"
        )
        run_blocking_io(test_path.write_text, test_content)
        run_blocking_io(test_path.unlink, missing_ok=True)
    except Exception as exc:
        logger.debug("Destination write probe path: %s", test_path)
        log_path_permission_context("destination_write_probe", destination)
        logger.warning(f"Destination not writable: {destination} ({exc})")
        status_callback("error", f"Destination not writable: {destination} ({exc})")
        return False

    return True


def get_final_destination(task: DownloadTask) -> Path:
    """Get final destination directory, with content-type routing support."""

    is_audiobook = check_audiobook(task.content_type)

    if task.source == "direct_download" and not is_audiobook:
        override = get_aa_content_type_dir(task.content_type)
        if override:
            return override

    return get_destination(is_audiobook)
