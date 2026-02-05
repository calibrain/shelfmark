from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shelfmark.core.logger import setup_logger
from shelfmark.download.fs import run_blocking_io

logger = setup_logger(__name__)

DEFAULT_CUSTOM_SCRIPT_TIMEOUT_SECONDS = 300  # 5 minutes


def resolve_custom_script_target(target_path: Path, destination: Path, path_mode: str) -> Path:
    """Resolve the path that should be passed as the custom script argument.

    In absolute mode, we pass the full target path.

    In relative mode, we pass a path relative to the destination folder. If the
    target is not within the destination, fall back to just the filename to
    avoid leaking unrelated absolute paths.
    """

    mode = (path_mode or "absolute").strip().lower()
    if mode != "relative":
        return target_path

    try:
        return target_path.relative_to(destination)
    except ValueError:
        if target_path.is_absolute():
            return Path(target_path.name)
    return target_path


@dataclass(frozen=True)
class CustomScriptExecution:
    script_path: str
    target_arg: Path
    target_abs: Path
    destination: Path
    mode: str
    phase: str
    env: dict[str, str]


def prepare_custom_script_execution(
    script_path: str,
    *,
    target_path: Path,
    destination: Path,
    path_mode: str,
    phase: str,
) -> CustomScriptExecution:
    mode = (path_mode or "absolute").strip().lower()
    if mode != "relative":
        mode = "absolute"

    target_arg = resolve_custom_script_target(target_path, destination, mode)
    env = {
        **os.environ,
        "SHELFMARK_CUSTOM_SCRIPT_TARGET": str(target_path),
        "SHELFMARK_CUSTOM_SCRIPT_RELATIVE": str(resolve_custom_script_target(target_path, destination, "relative")),
        "SHELFMARK_CUSTOM_SCRIPT_DESTINATION": str(destination),
        "SHELFMARK_CUSTOM_SCRIPT_MODE": str(mode),
        "SHELFMARK_CUSTOM_SCRIPT_PHASE": str(phase),
    }

    return CustomScriptExecution(
        script_path=str(script_path),
        target_arg=target_arg,
        target_abs=target_path,
        destination=destination,
        mode=mode,
        phase=phase,
        env=env,
    )


def run_custom_script(
    execution: CustomScriptExecution,
    *,
    task_id: str,
    status_callback,
    timeout_seconds: int = DEFAULT_CUSTOM_SCRIPT_TIMEOUT_SECONDS,
) -> bool:
    cwd: Optional[str] = None
    if execution.mode == "relative":
        # Make relative paths unambiguous by running the script from the destination folder.
        cwd = str(execution.destination)

    logger.info(
        "Task %s: running custom script %s on %s (%s)",
        task_id,
        execution.script_path,
        execution.target_arg,
        execution.phase,
    )

    try:
        result = run_blocking_io(
            subprocess.run,
            [execution.script_path, str(execution.target_arg)],
            check=True,
            timeout=timeout_seconds,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=execution.env,
        )
        if result.stdout:
            logger.debug("Task %s: custom script stdout: %s", task_id, result.stdout.strip())
        return True
    except FileNotFoundError:
        logger.error("Task %s: custom script not found: %s", task_id, execution.script_path)
        status_callback("error", f"Custom script not found: {execution.script_path}")
        return False
    except PermissionError:
        logger.error("Task %s: custom script not executable: %s", task_id, execution.script_path)
        status_callback("error", f"Custom script not executable: {execution.script_path}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(
            "Task %s: custom script timed out after %ss: %s",
            task_id,
            timeout_seconds,
            execution.script_path,
        )
        status_callback("error", "Custom script timed out")
        return False
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "No error output"
        logger.error(
            "Task %s: custom script failed (exit code %s): %s",
            task_id,
            exc.returncode,
            stderr,
        )
        status_callback("error", f"Custom script failed: {stderr[:100]}")
        return False
