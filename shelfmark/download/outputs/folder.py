from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Optional, List

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
    from shelfmark.download.orchestrator import config as orchestrator_config

    if check_audiobook(task.content_type):
        return True
    return orchestrator_config.get("BOOKS_OUTPUT_MODE", FOLDER_OUTPUT_MODE) == FOLDER_OUTPUT_MODE


def _build_processing_plan(
    temp_file: Path,
    task: DownloadTask,
    status_callback,
) -> Optional[_ProcessingPlan]:
    from shelfmark.download.orchestrator import (
        _get_file_organization,
        _get_final_destination,
        _validate_destination,
        build_output_plan,
    )

    is_audiobook = check_audiobook(task.content_type)
    organization_mode = _get_file_organization(is_audiobook)
    destination = _get_final_destination(task)

    if not _validate_destination(destination, status_callback):
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
    from shelfmark.download.orchestrator import (
        _cleanup_output_staging,
        _is_torrent_source,
        _log_plan_steps,
        _record_step,
        _safe_cleanup_path,
        _transfer_book_files,
        config as orchestrator_config,
        prepare_output_files,
    )

    plan = _build_processing_plan(temp_file, task, status_callback)
    if not plan:
        return None

    logger.debug(
        "Processing plan details: mode=%s destination=%s hardlink=%s stage_action=%s extract_archives=%s",
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

    steps: List[object] = []
    if prepared.output_plan.stage_action != STAGE_NONE:
        step_name = f"stage_{prepared.output_plan.stage_action}"
        _record_step(steps, step_name, source=str(temp_file), dest=str(prepared.output_plan.staging_dir))

    # Run custom script only for non-archive single files (matches legacy behavior)
    if orchestrator_config.CUSTOM_SCRIPT and prepared.working_path.is_file() and not is_archive(prepared.working_path):
        _record_step(steps, "custom_script", script=str(orchestrator_config.CUSTOM_SCRIPT))
        _log_plan_steps(steps)
        logger.info(f"Running custom script: {orchestrator_config.CUSTOM_SCRIPT}")
        try:
            result = subprocess.run(
                [orchestrator_config.CUSTOM_SCRIPT, str(prepared.working_path)],
                check=True,
                timeout=300,  # 5 minute timeout
                capture_output=True,
                text=True,
            )
            if result.stdout:
                logger.debug(f"Custom script stdout: {result.stdout.strip()}")
        except FileNotFoundError:
            logger.error(f"Custom script not found: {orchestrator_config.CUSTOM_SCRIPT}")
            status_callback("error", f"Custom script not found: {orchestrator_config.CUSTOM_SCRIPT}")
            return None
        except PermissionError:
            logger.error(f"Custom script not executable: {orchestrator_config.CUSTOM_SCRIPT}")
            status_callback("error", f"Custom script not executable: {orchestrator_config.CUSTOM_SCRIPT}")
            return None
        except subprocess.TimeoutExpired:
            logger.error(f"Custom script timed out after 300s: {orchestrator_config.CUSTOM_SCRIPT}")
            status_callback("error", "Custom script timed out")
            return None
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.strip() if e.stderr else "No error output"
            logger.error(f"Custom script failed (exit code {e.returncode}): {stderr}")
            status_callback("error", f"Custom script failed: {stderr[:100]}")
            return None

    source_path = plan.hardlink_source or prepared.working_path
    is_torrent = _is_torrent_source(source_path, task)
    is_torrent_for_label = is_torrent or prepared.output_plan.stage_action != STAGE_NONE

    if cancel_flag.is_set():
        logger.info(f"Download cancelled before final transfer: {task.task_id}")
        if not is_torrent:
            _cleanup_output_staging(
                prepared.output_plan,
                prepared.working_path,
                task,
                prepared.cleanup_paths,
            )
        return None

    if plan.use_hardlink:
        op_label = "Hardlinking"
    elif is_torrent_for_label:
        op_label = "Copying"
    else:
        op_label = "Moving"

    status_callback("resolving", f"{op_label} file")
    _record_step(
        steps,
        "transfer",
        op=op_label.lower(),
        source=str(source_path),
        dest=str(plan.destination),
        hardlink=plan.use_hardlink,
        torrent=is_torrent_for_label,
    )
    if prepared.output_plan.stage_action != STAGE_NONE:
        _record_step(steps, "cleanup_staging", path=str(prepared.working_path))
    _log_plan_steps(steps)

    final_paths, error = _transfer_book_files(
        prepared.files,
        destination=plan.destination,
        task=task,
        use_hardlink=plan.use_hardlink,
        is_torrent=is_torrent,
        organization_mode=plan.organization_mode,
    )

    if error:
        status_callback("error", error)
        return None

    _cleanup_output_staging(
        prepared.output_plan,
        prepared.working_path,
        task,
        prepared.cleanup_paths,
    )

    message = "Complete" if len(final_paths) == 1 else f"Complete ({len(final_paths)} files)"
    status_callback("complete", message)

    return str(final_paths[0])
