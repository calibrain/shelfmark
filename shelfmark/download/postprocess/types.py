"""Typed containers for optional post-processing integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from shelfmark.download.staging import StageAction


@dataclass(frozen=True)
class TransferPlan:
    """A staged transfer source and its operation policy."""

    source_path: Path
    use_hardlink: bool
    allow_archive_extraction: bool
    hardlink_enabled: bool


@dataclass(frozen=True)
class OutputPlan:
    """Resolved staging plan for an optional output integration."""

    mode: str
    stage_action: StageAction
    staging_dir: Path
    allow_archive_extraction: bool
    transfer_plan: TransferPlan | None = None


@dataclass(frozen=True)
class PreparedFiles:
    """Files prepared for an optional output integration."""

    output_plan: OutputPlan
    working_path: Path
    files: list[Path]
    rejected_files: list[Path]
    cleanup_paths: list[Path]


@dataclass(frozen=True)
class PlanStep:
    """Recorded optional post-processing step."""

    name: str
    details: dict[str, Any]
