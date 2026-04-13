"""Typed data containers used by the post-processing pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from shelfmark.download.staging import StageAction


@dataclass(frozen=True)
class TransferPlan:
    """Plan describing how files should move from source to output."""

    source_path: Path
    use_hardlink: bool
    allow_archive_extraction: bool
    hardlink_enabled: bool


@dataclass(frozen=True)
class OutputPlan:
    """Resolved output mode, staging strategy, and transfer settings."""

    mode: str
    stage_action: StageAction
    staging_dir: Path
    allow_archive_extraction: bool
    transfer_plan: TransferPlan | None = None


@dataclass(frozen=True)
class PreparedFiles:
    """Prepared file set ready for transfer or output handling."""

    output_plan: OutputPlan
    working_path: Path
    files: list[Path]
    rejected_files: list[Path]
    cleanup_paths: list[Path]


@dataclass(frozen=True)
class PlanStep:
    """Recorded post-processing step and its debug metadata."""

    name: str
    details: dict[str, Any]
