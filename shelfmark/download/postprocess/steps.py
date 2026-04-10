from __future__ import annotations

from shelfmark.core.logger import setup_logger

from .types import PlanStep

logger = setup_logger("shelfmark.download.postprocess.pipeline")


def record_step(steps: list[PlanStep], name: str, **details: object) -> None:
    steps.append(PlanStep(name=name, details=details))


def log_plan_steps(task_id: str, steps: list[PlanStep]) -> None:
    if not steps:
        return
    summary = " -> ".join(step.name for step in steps)
    logger.debug("Processing plan for %s: %s", task_id, summary)
