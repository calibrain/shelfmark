"""Output routing for post-download processing.

This module selects the appropriate output handler and invokes it.

Keeping this separate from `pipeline.py` avoids circular imports:

- output handlers depend on `pipeline`
- router depends on the output registry
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeGuard

from shelfmark.core.logger import setup_logger
from shelfmark.download.outputs import resolve_output_handler

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)


class _PostProcessHandler(Protocol):
    def __call__(
        self,
        temp_file: Path,
        task: DownloadTask,
        cancel_flag: Event,
        status_callback: Callable[[str, str | None], None],
        *,
        preserve_source_on_failure: bool = False,
    ) -> str | None: ...


def _is_post_process_handler(candidate: object) -> TypeGuard[_PostProcessHandler]:
    return callable(candidate)


def post_process_download(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback: Callable[[str, str | None], None],
    *,
    preserve_source_on_failure: bool = False,
) -> str | None:
    """Post-process download using the selected output handler."""
    output_handler = resolve_output_handler(task)
    if output_handler:
        if not _is_post_process_handler(output_handler):
            return None
        return output_handler(
            temp_file,
            task,
            cancel_flag,
            status_callback,
            preserve_source_on_failure=preserve_source_on_failure,
        )

    from shelfmark.download.outputs.folder import process_folder_output

    logger.info("Task %s: finalizing in shared storage", task.task_id)
    if not _is_post_process_handler(process_folder_output):
        return None
    return process_folder_output(
        temp_file,
        task,
        cancel_flag,
        status_callback,
        preserve_source_on_failure=preserve_source_on_failure,
    )
