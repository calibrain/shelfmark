from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Optional

from shelfmark.core.models import DownloadTask

StatusCallback = Callable[[str, Optional[str]], None]
OutputHandler = Callable[[Path, DownloadTask, Event, StatusCallback], Optional[str]]


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
    from . import folder  # noqa: F401

    _OUTPUTS_LOADED = True


def resolve_output_handler(task: DownloadTask) -> Optional[OutputRegistration]:
    load_output_handlers()
    for entry in _OUTPUT_REGISTRY:
        if entry.supports_task(task):
            return entry
    return None
