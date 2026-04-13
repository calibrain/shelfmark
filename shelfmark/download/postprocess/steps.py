"""Helpers for recording debug steps in the post-processing pipeline."""

from __future__ import annotations

from shelfmark.core.logger import setup_logger

from .types import PlanStep

logger = setup_logger("shelfmark.download.postprocess.pipeline")


def record_step(steps: list[PlanStep], name: str, **details: object) -> None:
    """Append a named debug step to the processing plan."""
    steps.append(PlanStep(name=name, details=details))


def log_plan_steps(task_id: str, steps: list[PlanStep]) -> None:
    """Log a compact summary of recorded post-processing steps."""
    if not steps:
        return
    summary = " -> ".join(step.name for step in steps)
    logger.debug("Processing plan for %s: %s", task_id, summary)
