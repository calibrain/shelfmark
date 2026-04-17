"""Litara output integration for uploading completed downloads."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import requests

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.outputs import StatusCallback, register_output
from shelfmark.download.staging import (
    STAGE_COPY,
    STAGE_MOVE,
    STAGE_NONE,
    build_staging_dir,
    get_staging_dir,
)

if TYPE_CHECKING:
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

LITARA_OUTPUT_MODE = "litara"
LITARA_SUPPORTED_EXTENSIONS = {
    ".azw",
    ".azw3",
    ".cb7",
    ".cbr",
    ".cbz",
    ".epub",
    ".fb2",
    ".mobi",
    ".pdf",
}
LITARA_SUPPORTED_FORMATS_LABEL = ", ".join(
    ext.lstrip(".").upper() for ext in sorted(LITARA_SUPPORTED_EXTENSIONS)
)
LITARA_DISPLAY_NAME = "Litara"


class LitaraError(Exception):
    """Raised when Litara integration fails."""


@dataclass(frozen=True)
class LitaraConfig:
    """Configuration required to upload files into Litara."""

    base_url: str
    email: str
    password: str
    verify_tls: bool = True


def build_litara_config(values: dict[str, Any]) -> LitaraConfig:
    """Build and validate the effective Litara configuration."""
    base_url = str(values.get("LITARA_HOST", "")).strip()
    email = str(values.get("LITARA_EMAIL", "")).strip()
    password = values.get("LITARA_PASSWORD", "") or ""

    if not base_url:
        msg = f"{LITARA_DISPLAY_NAME} URL is required"
        raise LitaraError(msg)
    if not email:
        msg = f"{LITARA_DISPLAY_NAME} email is required"
        raise LitaraError(msg)
    if not password:
        msg = f"{LITARA_DISPLAY_NAME} password is required"
        raise LitaraError(msg)

    return LitaraConfig(
        base_url=base_url.rstrip("/"),
        email=email,
        password=password,
        verify_tls=True,
    )


def litara_login(litara_config: LitaraConfig) -> str:
    """Authenticate with Litara and return an API token."""
    url = f"{litara_config.base_url}/api/v1/auth/login"
    payload = {
        "email": litara_config.email,
        "password": litara_config.password,
    }

    try:
        response = requests.post(url, json=payload, timeout=30, verify=litara_config.verify_tls)
    except requests.exceptions.ConnectionError as exc:
        msg = f"Could not connect to {LITARA_DISPLAY_NAME}"
        raise LitaraError(msg) from exc
    except requests.exceptions.Timeout as exc:
        msg = f"{LITARA_DISPLAY_NAME} connection timed out"
        raise LitaraError(msg) from exc
    except requests.exceptions.RequestException as exc:
        msg = f"{LITARA_DISPLAY_NAME} login failed: {exc}"
        raise LitaraError(msg) from exc

    if response.status_code in {401, 403}:
        msg = f"{LITARA_DISPLAY_NAME} authentication failed"
        raise LitaraError(msg)

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        msg = f"{LITARA_DISPLAY_NAME} login failed ({response.status_code})"
        raise LitaraError(msg) from exc

    try:
        data = response.json()
    except ValueError as exc:
        msg = f"Invalid {LITARA_DISPLAY_NAME} login response"
        raise LitaraError(msg) from exc

    token = data.get("access_token")
    if not token:
        msg = f"{LITARA_DISPLAY_NAME} did not return an access token"
        raise LitaraError(msg)

    return token


def litara_upload_file(litara_config: LitaraConfig, token: str, file_path: Path) -> None:
    """Upload a completed file into Litara's book drop."""
    url = f"{litara_config.base_url}/api/v1/book-drop/upload"
    headers = {"Authorization": f"Bearer {token}"}

    response = None

    try:
        with file_path.open("rb") as handle:
            response = requests.post(
                url,
                headers=headers,
                files={"files": (file_path.name, handle)},
                timeout=60,
                verify=litara_config.verify_tls,
            )
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        message = response.text.strip() if response is not None else ""
        if message:
            message = f": {message[:200]}"
        status_code = response.status_code if response is not None else "unknown"
        msg = f"{LITARA_DISPLAY_NAME} upload failed ({status_code}){message}"
        raise LitaraError(msg) from exc
    except requests.exceptions.ConnectionError as exc:
        msg = f"Could not connect to {LITARA_DISPLAY_NAME}"
        raise LitaraError(msg) from exc
    except requests.exceptions.Timeout as exc:
        msg = f"{LITARA_DISPLAY_NAME} upload timed out"
        raise LitaraError(msg) from exc
    except requests.exceptions.RequestException as exc:
        msg = f"{LITARA_DISPLAY_NAME} upload failed: {exc}"
        raise LitaraError(msg) from exc


def litara_scan_library(litara_config: LitaraConfig, token: str) -> None:
    """Trigger a Litara library scan after upload."""
    url = f"{litara_config.base_url}/api/v1/library/scan"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.post(
            url,
            headers=headers,
            params={"rescanMetadata": "false"},
            timeout=30,
            verify=litara_config.verify_tls,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        msg = f"{LITARA_DISPLAY_NAME} library scan failed: {exc}"
        raise LitaraError(msg) from exc


def _supports_litara(task: DownloadTask) -> bool:
    return not check_audiobook(task.content_type)


def _get_litara_settings() -> dict[str, Any]:
    return {
        "LITARA_HOST": core_config.config.get("LITARA_HOST", ""),
        "LITARA_EMAIL": core_config.config.get("LITARA_EMAIL", ""),
        "LITARA_PASSWORD": core_config.config.get("LITARA_PASSWORD", ""),
    }


def _litara_format_error(rejected_files: list[Path]) -> str:
    rejected_exts = sorted({f.suffix.lower() for f in rejected_files})
    rejected_list = ", ".join(rejected_exts)
    return (
        f"{LITARA_DISPLAY_NAME} does not support {rejected_list}. "
        f"Supported formats: {LITARA_SUPPORTED_FORMATS_LABEL}"
    )


def _post_process_litara(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback: StatusCallback,
    *,
    preserve_source_on_failure: bool = False,
) -> str | None:
    from shelfmark.download.postprocess.pipeline import (
        CustomScriptContext,
        OutputPlan,
        cleanup_output_staging,
        is_managed_workspace_path,
        maybe_run_custom_script,
        prepare_output_files,
        safe_cleanup_path,
    )

    if cancel_flag.is_set():
        logger.info("Task %s: cancelled before Litara upload", task.task_id)
        return None

    try:
        litara_config = build_litara_config(_get_litara_settings())
    except LitaraError as e:
        logger.warning("Task %s: Litara configuration error: %s", task.task_id, e)
        status_callback("error", str(e))
        return None

    status_callback("resolving", f"Preparing {LITARA_DISPLAY_NAME} upload")

    stage_action = STAGE_NONE
    if is_managed_workspace_path(temp_file):
        stage_action = STAGE_COPY if preserve_source_on_failure else STAGE_MOVE
    staging_dir = (
        build_staging_dir("litara", task.task_id)
        if stage_action != STAGE_NONE
        else get_staging_dir()
    )

    output_plan = OutputPlan(
        mode=LITARA_OUTPUT_MODE,
        stage_action=stage_action,
        staging_dir=staging_dir,
        allow_archive_extraction=True,
    )

    prepared = prepare_output_files(
        temp_file,
        task,
        LITARA_OUTPUT_MODE,
        status_callback,
        output_plan=output_plan,
        preserve_source_on_failure=preserve_source_on_failure,
    )
    if not prepared:
        return None

    logger.debug(
        "Task %s: prepared %d file(s) for Litara upload",
        task.task_id,
        len(prepared.files),
    )

    success = False
    try:
        unsupported_files = [
            file_path
            for file_path in prepared.files
            if file_path.suffix.lower() not in LITARA_SUPPORTED_EXTENSIONS
        ]
        if unsupported_files:
            error_message = _litara_format_error(unsupported_files)
            logger.warning("Task %s: %s", task.task_id, error_message)
            status_callback("error", error_message)
            return None

        token = litara_login(litara_config)
        logger.info(
            "Task %s: uploading %d file(s) to Litara",
            task.task_id,
            len(prepared.files),
        )

        for index, file_path in enumerate(prepared.files, start=1):
            if cancel_flag.is_set():
                logger.info("Task %s: cancelled during Litara upload", task.task_id)
                return None
            status_callback(
                "resolving",
                f"Uploading to {LITARA_DISPLAY_NAME} ({index}/{len(prepared.files)})",
            )
            litara_upload_file(litara_config, token, file_path)

        try:
            litara_scan_library(litara_config, token)
        except LitaraError as e:
            logger.warning("Task %s: Litara library scan failed: %s", task.task_id, e)

        logger.info(
            "Task %s: uploaded %d file(s) to Litara",
            task.task_id,
            len(prepared.files),
        )

        destination: Path | None
        if len(prepared.files) == 1:
            destination = prepared.files[0].parent
        else:
            try:
                destination = Path(os.path.commonpath([str(p.parent) for p in prepared.files]))
            except ValueError:
                destination = prepared.files[0].parent if prepared.files else None

        script_context = CustomScriptContext(
            task=task,
            phase="post_upload",
            output_mode=LITARA_OUTPUT_MODE,
            destination=destination,
            final_paths=prepared.files,
            output_details={
                "litara": {
                    "base_url": litara_config.base_url,
                }
            },
        )
        if not maybe_run_custom_script(script_context, status_callback=status_callback):
            return None

        message = f"Uploaded to {LITARA_DISPLAY_NAME}"
        if len(prepared.files) > 1:
            message = f"Uploaded to {LITARA_DISPLAY_NAME} ({len(prepared.files)} files)"
        status_callback("complete", message)
        success = True
        output_path = f"litara://{task.task_id}"

    except LitaraError as e:
        logger.warning("Task %s: Litara upload failed: %s", task.task_id, e)
        status_callback("error", str(e))
        return None
    except (OSError, TypeError, ValueError) as e:
        logger.error_trace("Task %s: unexpected error uploading to Litara: %s", task.task_id, e)
        status_callback("error", f"{LITARA_DISPLAY_NAME} upload failed: {e}")
        return None
    else:
        return output_path
    finally:
        cleanup_output_staging(
            prepared.output_plan,
            prepared.working_path,
            task,
            prepared.cleanup_paths,
        )
        if preserve_source_on_failure and success:
            safe_cleanup_path(temp_file, task)


@register_output(LITARA_OUTPUT_MODE, supports_task=_supports_litara, priority=10)
def process_litara_output(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback: StatusCallback,
    *,
    preserve_source_on_failure: bool = False,
) -> str | None:
    """Process a completed download through the Litara output."""
    return _post_process_litara(
        temp_file,
        task,
        cancel_flag,
        status_callback,
        preserve_source_on_failure=preserve_source_on_failure,
    )
