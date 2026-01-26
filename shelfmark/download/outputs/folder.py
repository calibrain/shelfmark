from __future__ import annotations

import os
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


def _resolve_custom_script_target(target_path: Path, destination: Path, path_mode: str) -> Path:
    mode = (path_mode or "absolute").strip().lower()
    if mode != "relative":
        return target_path

    try:
        return target_path.relative_to(destination)
    except ValueError:
        if target_path.is_absolute():
            return Path(target_path.name)
    return target_path


def _format_op_counts(op_counts: dict[str, int]) -> str:
    parts = [f"{op}={count}" for op, count in op_counts.items() if count]
    return ", ".join(parts) if parts else "none"


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

    def run_custom_script(script_path: str, target_path: Path, phase: str) -> bool:
        path_mode = core_config.config.get("CUSTOM_SCRIPT_PATH_MODE", "absolute")
        script_target = _resolve_custom_script_target(target_path, plan.destination, path_mode)
        env = {
            **os.environ,
            "SHELFMARK_CUSTOM_SCRIPT_TARGET": str(target_path),
            "SHELFMARK_CUSTOM_SCRIPT_RELATIVE": str(_resolve_custom_script_target(target_path, plan.destination, "relative")),
            "SHELFMARK_CUSTOM_SCRIPT_DESTINATION": str(plan.destination),
            "SHELFMARK_CUSTOM_SCRIPT_MODE": str(path_mode),
            "SHELFMARK_CUSTOM_SCRIPT_PHASE": phase,
        }
        record_step(
            steps,
            "custom_script",
            script=str(script_path),
            target=str(script_target),
            target_abs=str(target_path),
            mode=str(path_mode),
            phase=phase,
        )
        log_plan_steps(task.task_id, steps)
        logger.info(
            "Task %s: running custom script %s on %s (%s)",
            task.task_id,
            script_path,
            script_target,
            phase,
        )
        try:
            result = subprocess.run(
                [script_path, str(script_target)],
                check=True,
                timeout=300,  # 5 minute timeout
                capture_output=True,
                text=True,
                env=env,
            )
            if result.stdout:
                logger.debug("Task %s: custom script stdout: %s", task.task_id, result.stdout.strip())
            return True
        except FileNotFoundError:
            logger.error("Task %s: custom script not found: %s", task.task_id, script_path)
            status_callback("error", f"Custom script not found: {script_path}")
            return False
        except PermissionError:
            logger.error("Task %s: custom script not executable: %s", task.task_id, script_path)
            status_callback("error", f"Custom script not executable: {script_path}")
            return False
        except subprocess.TimeoutExpired:
            logger.error("Task %s: custom script timed out after 300s: %s", task.task_id, script_path)
            status_callback("error", "Custom script timed out")
            return False
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.strip() if e.stderr else "No error output"
            logger.error(
                "Task %s: custom script failed (exit code %s): %s",
                task.task_id,
                e.returncode,
                stderr,
            )
            status_callback("error", f"Custom script failed: {stderr[:100]}")
            return False

    # Custom script is run post-transfer (see below).

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

    final_paths, error, op_counts = transfer_book_files(
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
        "Task %s: transferred %d file(s) to %s (ops: %s)",
        task.task_id,
        len(final_paths),
        plan.destination,
        _format_op_counts(op_counts),
    )
    if use_hardlink and op_counts.get("copy", 0):
        logger.warning(
            "Task %s: hardlink requested but %d of %d file(s) copied (fallback)",
            task.task_id,
            op_counts.get("copy", 0),
            len(final_paths),
        )

    # Run custom script once per successful task, after transfer.
    if core_config.config.CUSTOM_SCRIPT:
        if len(final_paths) == 1:
            target_path = final_paths[0]
        else:
            try:
                target_path = Path(os.path.commonpath([str(p.parent) for p in final_paths]))
            except ValueError:
                target_path = plan.destination

        if not run_custom_script(core_config.config.CUSTOM_SCRIPT, target_path, phase="post_transfer"):
            cleanup_output_staging(
                prepared.output_plan,
                prepared.working_path,
                task,
                prepared.cleanup_paths,
            )
            return None

    cleanup_output_staging(
        prepared.output_plan,
        prepared.working_path,
        task,
        prepared.cleanup_paths,
    )

    message = "Complete" if len(final_paths) == 1 else f"Complete ({len(final_paths)} files)"
    status_callback("complete", message)

    return str(final_paths[0])
