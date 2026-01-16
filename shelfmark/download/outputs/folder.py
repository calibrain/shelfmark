from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Optional, List

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.archive import is_archive
from shelfmark.download.outputs import register_output
from shelfmark.download.staging import StageAction, STAGE_NONE

logger = setup_logger(__name__)

FOLDER_OUTPUT_MODE = "folder"


@dataclass(frozen=True)
class _ProcessingPlan:
    destination: Path
    organization_mode: str
    use_hardlink: bool
    allow_archive_extraction: bool
    stage_action: StageAction
    staging_dir: Path
    hardlink_source: Optional[Path]
    output_mode: str = FOLDER_OUTPUT_MODE


def _supports_folder_output(task: DownloadTask) -> bool:
    if check_audiobook(task.content_type):
        return True
    return core_config.config.get("BOOKS_OUTPUT_MODE", FOLDER_OUTPUT_MODE) == FOLDER_OUTPUT_MODE


def _build_processing_plan(
    temp_file: Path,
    task: DownloadTask,
    status_callback,
) -> Optional[_ProcessingPlan]:
    from shelfmark.download.postprocess.pipeline import (
        build_output_plan,
        get_final_destination,
        validate_destination,
    )
    from shelfmark.download.postprocess.policy import get_file_organization

    is_audiobook = check_audiobook(task.content_type)
    organization_mode = get_file_organization(is_audiobook)
    destination = get_final_destination(task)

    if not validate_destination(destination, status_callback):
        return None

    output_plan = build_output_plan(
        temp_file,
        task,
        output_mode=FOLDER_OUTPUT_MODE,
        destination=destination,
        status_callback=status_callback,
    )
    if not output_plan.transfer_plan:
        return None

    transfer_plan = output_plan.transfer_plan
    hardlink_source = transfer_plan.source_path if transfer_plan.use_hardlink else None

    return _ProcessingPlan(
        destination=destination,
        organization_mode=organization_mode,
        use_hardlink=transfer_plan.use_hardlink,
        allow_archive_extraction=transfer_plan.allow_archive_extraction,
        stage_action=output_plan.stage_action,
        staging_dir=output_plan.staging_dir,
        hardlink_source=hardlink_source,
    )


@register_output(FOLDER_OUTPUT_MODE, supports_task=_supports_folder_output, priority=0)
def process_folder_output(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback,
) -> Optional[str]:
    """Post-process download to the configured folder destination."""
    from shelfmark.download.postprocess.pipeline import (
        cleanup_output_staging,
        is_torrent_source,
        log_plan_steps,
        prepare_output_files,
        record_step,
        safe_cleanup_path,
        transfer_book_files,
    )

    plan = _build_processing_plan(temp_file, task, status_callback)
    if not plan:
        return None

    logger.debug(
        "Processing plan for task %s: mode=%s destination=%s hardlink=%s stage_action=%s extract_archives=%s",
        task.task_id,
        plan.organization_mode,
        plan.destination,
        plan.use_hardlink,
        plan.stage_action,
        plan.allow_archive_extraction,
    )

    prepared = prepare_output_files(
        temp_file,
        task,
        output_mode=plan.output_mode,
        status_callback=status_callback,
        destination=plan.destination,
    )
    if not prepared:
        return None

    steps: List[Any] = []
    if prepared.output_plan.stage_action != STAGE_NONE:
        step_name = f"stage_{prepared.output_plan.stage_action}"
        record_step(steps, step_name, source=str(temp_file), dest=str(prepared.output_plan.staging_dir))

    # Run custom script only for non-archive single files (matches legacy behavior)
    if core_config.config.CUSTOM_SCRIPT and prepared.working_path.is_file() and not is_archive(prepared.working_path):
        record_step(steps, "custom_script", script=str(core_config.config.CUSTOM_SCRIPT))
        log_plan_steps(task.task_id, steps)
        logger.info(
            "Task %s: running custom script %s on %s",
            task.task_id,
            core_config.config.CUSTOM_SCRIPT,
            prepared.working_path,
        )
        try:
            result = subprocess.run(
                [core_config.config.CUSTOM_SCRIPT, str(prepared.working_path)],
                check=True,
                timeout=300,  # 5 minute timeout
                capture_output=True,
                text=True,
            )
            if result.stdout:
                logger.debug("Task %s: custom script stdout: %s", task.task_id, result.stdout.strip())
        except FileNotFoundError:
            logger.error("Task %s: custom script not found: %s", task.task_id, core_config.config.CUSTOM_SCRIPT)
            status_callback("error", f"Custom script not found: {core_config.config.CUSTOM_SCRIPT}")
            return None
        except PermissionError:
            logger.error(
                "Task %s: custom script not executable: %s",
                task.task_id,
                core_config.config.CUSTOM_SCRIPT,
            )
            status_callback("error", f"Custom script not executable: {core_config.config.CUSTOM_SCRIPT}")
            return None
        except subprocess.TimeoutExpired:
            logger.error(
                "Task %s: custom script timed out after 300s: %s",
                task.task_id,
                core_config.config.CUSTOM_SCRIPT,
            )
            status_callback("error", "Custom script timed out")
            return None
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.strip() if e.stderr else "No error output"
            logger.error(
                "Task %s: custom script failed (exit code %s): %s",
                task.task_id,
                e.returncode,
                stderr,
            )
            status_callback("error", f"Custom script failed: {stderr[:100]}")
            return None

    # If we staged a copy into TMP_DIR (e.g. for custom script), transfer from the staged
    # path and disable hardlinking for this transfer.
    use_hardlink = plan.use_hardlink and prepared.output_plan.stage_action == STAGE_NONE
    source_path = plan.hardlink_source if use_hardlink and plan.hardlink_source else prepared.working_path
    is_torrent = is_torrent_source(source_path, task)

    usenet_action = core_config.config.get("PROWLARR_USENET_ACTION", "move")
    is_usenet = task.source == "prowlarr" and not task.original_download_path

    # For external usenet downloads, always copy from the client path.
    # "Move" is implemented as a client-side cleanup after import.
    preserve_source = is_usenet

    copy_for_label = is_torrent or preserve_source or prepared.output_plan.stage_action != STAGE_NONE

    if cancel_flag.is_set():
        logger.info("Task %s: cancelled before final transfer", task.task_id)
        cleanup_output_staging(
            prepared.output_plan,
            prepared.working_path,
            task,
            prepared.cleanup_paths,
        )
        return None

    if use_hardlink:
        op_label = "Hardlinking"
    elif is_usenet and usenet_action == "move" and prepared.output_plan.stage_action == STAGE_NONE:
        # Presented as a move, but implemented as copy + client cleanup.
        op_label = "Moving"
    elif copy_for_label:
        op_label = "Copying"
    else:
        op_label = "Moving"

    status_callback("resolving", f"{op_label} file")
    record_step(
        steps,
        "transfer",
        op=op_label.lower(),
        source=str(source_path),
        dest=str(plan.destination),
        hardlink=use_hardlink,
        torrent=copy_for_label,
    )
    if prepared.output_plan.stage_action != STAGE_NONE:
        record_step(steps, "cleanup_staging", path=str(prepared.working_path))
    log_plan_steps(task.task_id, steps)

    final_paths, error = transfer_book_files(
        prepared.files,
        destination=plan.destination,
        task=task,
        use_hardlink=use_hardlink,
        is_torrent=is_torrent,
        preserve_source=preserve_source,
        organization_mode=plan.organization_mode,
    )

    if error:
        logger.warning("Task %s: transfer failed: %s", task.task_id, error)
        status_callback("error", error)
        return None

    logger.info(
        "Task %s: transferred %d file(s) to %s (%s)",
        task.task_id,
        len(final_paths),
        plan.destination,
        op_label.lower(),
    )

    cleanup_output_staging(
        prepared.output_plan,
        prepared.working_path,
        task,
        prepared.cleanup_paths,
    )

    message = "Complete" if len(final_paths) == 1 else f"Complete ({len(final_paths)} files)"
    status_callback("complete", message)

    return str(final_paths[0])
