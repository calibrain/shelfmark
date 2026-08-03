"""Folder output handoff for durable Book imports."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shelfmark.download.outputs import StatusCallback, register_output

if TYPE_CHECKING:
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import DownloadTask


FOLDER_OUTPUT_MODE = "folder"


def _supports_folder_output(task: DownloadTask) -> bool:
    return task.library_book_id is not None


@register_output(FOLDER_OUTPUT_MODE, supports_task=_supports_folder_output, priority=0)
def process_folder_output(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback: StatusCallback,
    *,
    preserve_source_on_failure: bool = False,
) -> str | None:
    """Leave a completed source intact for terminal import planning and transfer."""
    del task, cancel_flag, preserve_source_on_failure
    status_callback("complete", "Downloaded; importing selected files")
    return str(temp_file)
