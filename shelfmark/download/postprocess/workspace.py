from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

from shelfmark.config import env as env_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.download.fs import run_blocking_io
from shelfmark.download.staging import STAGE_NONE

from .types import OutputPlan

logger = setup_logger("shelfmark.download.postprocess.pipeline")


def _tmp_dir() -> Path:
    return env_config.TMP_DIR


def is_within_tmp_dir(path: Path) -> bool:
    """Legacy helper: True if path is inside TMP_DIR."""

    try:
        path.resolve().relative_to(_tmp_dir().resolve())
        return True
    except (OSError, ValueError):
        return False


def is_managed_workspace_path(path: Path) -> bool:
    """True if Shelfmark should treat this path as mutable.

    The managed workspace is `TMP_DIR`. Anything outside it should be treated as
    read-only for safety (e.g. torrent seeding directories).
    """

    return is_within_tmp_dir(path)


def _is_original_download(path: Optional[Path], task: DownloadTask) -> bool:
    if not path or not task.original_download_path:
        return False
    try:
        return path.resolve() == Path(task.original_download_path).resolve()
    except (OSError, ValueError):
        return False


def safe_cleanup_path(path: Optional[Path], task: DownloadTask) -> None:
    """Remove a temp path only if it is safe and in our managed workspace."""

    if not path or _is_original_download(path, task):
        return

    if not is_managed_workspace_path(path):
        logger.debug("Skip cleanup (outside TMP_DIR) for task %s: %s", task.task_id, path)
        return

    try:
        if path.is_dir():
            run_blocking_io(shutil.rmtree, path, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)
    except (OSError, PermissionError) as exc:
        logger.warning("Cleanup failed for task %s (%s): %s", task.task_id, path, exc)


def cleanup_output_staging(
    output_plan: OutputPlan,
    working_path: Path,
    task: DownloadTask,
    cleanup_paths: Optional[List[Path]] = None,
) -> None:
    if output_plan.stage_action != STAGE_NONE:
        cleanup_target = output_plan.staging_dir
        if output_plan.staging_dir == _tmp_dir():
            cleanup_target = working_path
        safe_cleanup_path(cleanup_target, task)

    if cleanup_paths:
        for path in cleanup_paths:
            safe_cleanup_path(path, task)
