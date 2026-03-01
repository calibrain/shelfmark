from __future__ import annotations

from pathlib import Path
from typing import Optional

from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.download.staging import STAGE_COPY, STAGE_NONE, get_staging_dir, stage_path

from .scan import collect_staged_files
from .transfer import resolve_hardlink_source
from .types import OutputPlan, PreparedFiles
from .workspace import cleanup_output_staging, is_managed_workspace_path

logger = setup_logger("shelfmark.download.postprocess.pipeline")


def build_output_plan(
    temp_file: Path,
    task: DownloadTask,
    output_mode: str,
    destination: Optional[Path] = None,
    status_callback=None,
) -> OutputPlan:
    """Build an output plan that describes staging behavior for file-based outputs."""

    transfer_plan = resolve_hardlink_source(temp_file, task, destination, status_callback)
    staging_dir = get_staging_dir()

    return OutputPlan(
        mode=output_mode,
        stage_action=STAGE_NONE,
        staging_dir=staging_dir,
        allow_archive_extraction=transfer_plan.allow_archive_extraction,
        transfer_plan=transfer_plan,
    )


def prepare_output_files(
    temp_file: Path,
    task: DownloadTask,
    output_mode: str,
    status_callback,
    destination: Optional[Path] = None,
    output_plan: Optional[OutputPlan] = None,
    preserve_source_on_failure: bool = False,
) -> Optional[PreparedFiles]:
    if output_plan is None:
        output_plan = build_output_plan(
            temp_file,
            task,
            output_mode=output_mode,
            destination=destination,
            status_callback=status_callback,
        )

    working_path = temp_file
    if output_plan.stage_action != STAGE_NONE:
        step_label = "Staging torrent files" if output_plan.stage_action == STAGE_COPY else "Staging files"
        status_callback("resolving", step_label)
        working_path = stage_path(working_path, output_plan.staging_dir, output_plan.stage_action)

    can_delete_source_archives = output_plan.stage_action != STAGE_NONE or is_managed_workspace_path(
        working_path
    )
    cleanup_archives = can_delete_source_archives and not preserve_source_on_failure

    files, rejected_files, cleanup_paths, error = collect_staged_files(
        working_path=working_path,
        task=task,
        allow_archive_extraction=output_plan.allow_archive_extraction,
        status_callback=status_callback,
        cleanup_archives=cleanup_archives,
    )

    if error:
        status_callback("error", error)
        if not preserve_source_on_failure:
            cleanup_output_staging(output_plan, working_path, task, cleanup_paths)
        return None

    if output_plan.stage_action == STAGE_NONE and is_managed_workspace_path(working_path):
        cleanup_paths = [*cleanup_paths, working_path]

    return PreparedFiles(
        output_plan=output_plan,
        working_path=working_path,
        files=files,
        rejected_files=rejected_files,
        cleanup_paths=cleanup_paths,
    )
