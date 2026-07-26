"""Shared folder output handler."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import DownloadTask

StatusCallback = Callable[[str, str | None], None]


class OutputHandler(Protocol):
    """Callable contract for post-download output handlers."""

    def __call__(
        self,
        temp_file: Path,
        task: DownloadTask,
        cancel_flag: Event,
        status_callback: StatusCallback,
        *,
        preserve_source_on_failure: bool = False,
    ) -> str | None: ...


_OUTPUTS_LOADED = False


def register_output(*_args: object, **_kwargs: object) -> Callable[[OutputHandler], OutputHandler]:
    """Retain decorators for output helpers no longer selected by the router."""
    return lambda handler: handler


def load_output_handlers() -> None:
    """Load built-in output handlers exactly once."""
    global _OUTPUTS_LOADED
    if _OUTPUTS_LOADED:
        return

    from . import folder as folder

    _OUTPUTS_LOADED = True


def resolve_output_handler(task: DownloadTask) -> OutputHandler | None:
    """Return the sole shared-storage output handler."""
    load_output_handlers()
    from .folder import process_folder_output

    return process_folder_output
