from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Optional

from shelfmark.core.models import DownloadTask

StatusCallback = Callable[[str, Optional[str]], None]
OutputHandler = Callable[[Path, DownloadTask, Event, StatusCallback, bool], Optional[str]]


@dataclass(frozen=True)
class OutputRegistration:
    mode: str
    supports_task: Callable[[DownloadTask], bool]
    handler: OutputHandler
    priority: int = 0


_OUTPUT_REGISTRY: list[OutputRegistration] = []
_OUTPUTS_LOADED = False


def register_output(
    mode: str,
    supports_task: Callable[[DownloadTask], bool],
    priority: int = 0,
) -> Callable[[OutputHandler], OutputHandler]:
    def decorator(handler: OutputHandler) -> OutputHandler:
        _OUTPUT_REGISTRY.append(
            OutputRegistration(
                mode=mode,
                supports_task=supports_task,
                handler=handler,
                priority=priority,
            )
        )
        _OUTPUT_REGISTRY.sort(key=lambda entry: entry.priority, reverse=True)
        return handler

    return decorator


def load_output_handlers() -> None:
    global _OUTPUTS_LOADED
    if _OUTPUTS_LOADED:
        return

    from . import booklore  # noqa: F401
    from . import email  # noqa: F401
    from . import folder  # noqa: F401

    _OUTPUTS_LOADED = True


def _normalize_output_mode(value: object) -> str:
    return str(value or "").strip().lower()


def _derive_output_mode(task: DownloadTask) -> str:
    """Return the desired output mode for a task.

    Prefer the mode captured at queue time. Fall back to current config for
    legacy tasks that do not have `output_mode` populated.
    """

    mode = _normalize_output_mode(getattr(task, "output_mode", None))
    if mode:
        return mode

    # Legacy / defensive fallback: derive from current config.
    from shelfmark.core.config import config
    from shelfmark.core.utils import is_audiobook as check_audiobook

    if check_audiobook(getattr(task, "content_type", None)):
        return "folder"

    return _normalize_output_mode(config.get("BOOKS_OUTPUT_MODE", "folder")) or "folder"


def resolve_output_handler(task: DownloadTask) -> Optional[OutputRegistration]:
    load_output_handlers()
    desired_mode = _derive_output_mode(task)

    # Prefer a direct mode match. `supports_task` becomes a capability check
    # (e.g., prevent email/booklore for audiobooks).
    for entry in _OUTPUT_REGISTRY:
        if entry.mode == desired_mode and entry.supports_task(task):
            return entry

    # If the requested output isn't supported for this task, fall back to folder.
    for entry in _OUTPUT_REGISTRY:
        if entry.mode == "folder" and entry.supports_task(task):
            return entry

    # Last-resort fallback: keep the legacy "first supporting handler" behavior.
    for entry in _OUTPUT_REGISTRY:
        if entry.supports_task(task):
            return entry

    return None
