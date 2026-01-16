from __future__ import annotations

from typing import Any, List

from shelfmark.core.logger import setup_logger

from .types import PlanStep

logger = setup_logger("shelfmark.download.postprocess.pipeline")


def record_step(steps: List[PlanStep], name: str, **details: Any) -> None:
    steps.append(PlanStep(name=name, details=details))


def log_plan_steps(task_id: str, steps: List[PlanStep]) -> None:
    if not steps:
        return
    summary = " -> ".join(step.name for step in steps)
    logger.debug("Processing plan for %s: %s", task_id, summary)
