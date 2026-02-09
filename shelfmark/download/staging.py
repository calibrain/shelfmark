from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Literal

from shelfmark.config import env as env_config
from shelfmark.core.logger import setup_logger
from shelfmark.download.fs import run_blocking_io

logger = setup_logger(__name__)

StageAction = Literal["none", "copy", "move"]
STAGE_NONE: StageAction = "none"
STAGE_COPY: StageAction = "copy"
STAGE_MOVE: StageAction = "move"


def get_staging_dir() -> Path:
    """Get the staging directory for downloads."""
    tmp_dir = env_config.TMP_DIR
    run_blocking_io(tmp_dir.mkdir, parents=True, exist_ok=True)
    return tmp_dir


def get_staging_path(task_id: str, extension: str) -> Path:
    """Get a staging path for a download."""
    staging_dir = get_staging_dir()
    safe_id = hashlib.md5(task_id.encode()).hexdigest()[:16]
    return staging_dir / f"{safe_id}.{extension.lstrip('.')}"


def build_staging_dir(prefix: str | None, task_id: str) -> Path:
    """Build a dedicated staging directory for output processing."""
    base_dir = get_staging_dir()
    if not prefix:
        return base_dir

    safe_id = hashlib.md5(task_id.encode()).hexdigest()[:8]
    staging_dir = base_dir / f"{prefix}_{safe_id}"
    counter = 1

    while run_blocking_io(staging_dir.exists):
        staging_dir = base_dir / f"{prefix}_{safe_id}_{counter}"
        counter += 1

    run_blocking_io(staging_dir.mkdir, parents=True, exist_ok=True)
    return staging_dir


def stage_file(source_path: Path, task_id: str, copy: bool = False) -> Path:
    """Stage a file for ingest processing. Use copy=True for torrents to preserve seeding."""
    staging_dir = get_staging_dir()
    return stage_path(source_path, staging_dir, STAGE_COPY if copy else STAGE_MOVE)


def stage_path(source: Path, staging_dir: Path, action: StageAction) -> Path:
    """Stage a file or directory into a staging dir."""
    if action == STAGE_NONE:
        return source

    staged_path = staging_dir / source.name
    counter = 1

    source_is_dir = run_blocking_io(source.is_dir)
    if source_is_dir:
        while run_blocking_io(staged_path.exists):
            staged_path = staging_dir / f"{source.name}_{counter}"
            counter += 1
        if action == STAGE_COPY:
            run_blocking_io(shutil.copytree, str(source), str(staged_path))
        else:
            run_blocking_io(shutil.move, str(source), str(staged_path))
    else:
        while run_blocking_io(staged_path.exists):
            staged_path = staging_dir / f"{source.stem}_{counter}{source.suffix}"
            counter += 1
        if action == STAGE_COPY:
            run_blocking_io(shutil.copy2, str(source), str(staged_path))
        else:
            run_blocking_io(shutil.move, str(source), str(staged_path))

    staged_kind = "directory" if source_is_dir else "file"
    logger.debug("Staged %s via %s: %s -> %s", staged_kind, action, source, staged_path)
    return staged_path
