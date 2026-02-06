from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Any, Dict, List, Mapping, Optional

import requests

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.outputs import register_output
from shelfmark.download.staging import STAGE_MOVE, STAGE_NONE, build_staging_dir

logger = setup_logger(__name__)

BOOKLORE_OUTPUT_MODE = "booklore"
BOOKLORE_SUPPORTED_EXTENSIONS = {".azw", ".azw3", ".cb7", ".cbr", ".cbz", ".epub", ".fb2", ".mobi", ".pdf"}
BOOKLORE_SUPPORTED_FORMATS_LABEL = ", ".join(
    ext.lstrip(".").upper() for ext in sorted(BOOKLORE_SUPPORTED_EXTENSIONS)
)


class BookloreError(Exception):
    """Raised when Booklore integration fails."""


@dataclass(frozen=True)
class BookloreConfig:
    base_url: str
    username: str
    password: str
    library_id: int
    path_id: int
    verify_tls: bool = True
    refresh_after_upload: bool = False


def _parse_int(value: Any, label: str) -> int:
    if value is None or value == "":
        raise BookloreError(f"{label} is required")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BookloreError(f"{label} must be a number") from exc


def build_booklore_config(values: Mapping[str, Any]) -> BookloreConfig:
    base_url = str(values.get("BOOKLORE_HOST", "")).strip()
    username = str(values.get("BOOKLORE_USERNAME", "")).strip()
    password = values.get("BOOKLORE_PASSWORD", "") or ""

    if not base_url:
        raise BookloreError("Booklore URL is required")
    if not username:
        raise BookloreError("Booklore username is required")
    if not password:
        raise BookloreError("Booklore password is required")

    library_id = _parse_int(values.get("BOOKLORE_LIBRARY_ID"), "Booklore library ID")
    path_id = _parse_int(values.get("BOOKLORE_PATH_ID"), "Booklore path ID")

    return BookloreConfig(
        base_url=base_url.rstrip("/"),
        username=username,
        password=password,
        library_id=library_id,
        path_id=path_id,
        verify_tls=True,
        refresh_after_upload=True,  # Always refresh library after upload
    )


def booklore_login(booklore_config: BookloreConfig) -> str:
    url = f"{booklore_config.base_url}/api/v1/auth/login"
    payload = {"username": booklore_config.username, "password": booklore_config.password}

    try:
        response = requests.post(url, json=payload, timeout=30, verify=booklore_config.verify_tls)
    except requests.exceptions.ConnectionError as exc:
        raise BookloreError("Could not connect to Booklore") from exc
    except requests.exceptions.Timeout as exc:
        raise BookloreError("Booklore connection timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise BookloreError(f"Booklore login failed: {exc}") from exc

    if response.status_code in {401, 403}:
        raise BookloreError("Booklore authentication failed")

    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise BookloreError(f"Booklore login failed ({response.status_code})") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise BookloreError("Invalid Booklore login response") from exc

    token = data.get("accessToken")
    if not token:
        raise BookloreError("Booklore did not return an access token")

    return token


def booklore_list_libraries(booklore_config: BookloreConfig, token: str) -> list[dict[str, Any]]:
    url = f"{booklore_config.base_url}/api/v1/libraries"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.get(url, headers=headers, timeout=30, verify=booklore_config.verify_tls)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise BookloreError(f"Failed to fetch Booklore libraries: {exc}") from exc

    try:
        return response.json()
    except ValueError as exc:
        raise BookloreError("Invalid Booklore libraries response") from exc


def booklore_upload_file(booklore_config: BookloreConfig, token: str, file_path: Path) -> None:
    url = f"{booklore_config.base_url}/api/v1/files/upload"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"libraryId": booklore_config.library_id, "pathId": booklore_config.path_id}

    response = None

    try:
        with file_path.open("rb") as handle:
            response = requests.post(
                url,
                headers=headers,
                params=params,
                files={"file": (file_path.name, handle)},
                timeout=60,
                verify=booklore_config.verify_tls,
            )
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        message = response.text.strip() if response is not None else ""
        if message:
            message = f": {message[:200]}"
        status_code = response.status_code if response is not None else "unknown"
        raise BookloreError(f"Booklore upload failed ({status_code}){message}") from exc
    except requests.exceptions.ConnectionError as exc:
        raise BookloreError("Could not connect to Booklore") from exc
    except requests.exceptions.Timeout as exc:
        raise BookloreError("Booklore upload timed out") from exc
    except requests.exceptions.RequestException as exc:
        raise BookloreError(f"Booklore upload failed: {exc}") from exc


def booklore_refresh_library(booklore_config: BookloreConfig, token: str) -> None:
    url = f"{booklore_config.base_url}/api/v1/libraries/{booklore_config.library_id}/refresh"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.put(url, headers=headers, timeout=30, verify=booklore_config.verify_tls)
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise BookloreError(f"Booklore refresh failed: {exc}") from exc


def _supports_booklore(task: DownloadTask) -> bool:
    if check_audiobook(task.content_type):
        return False
    return core_config.config.get("BOOKS_OUTPUT_MODE", "folder") == BOOKLORE_OUTPUT_MODE


def _get_booklore_settings() -> Dict[str, Any]:
    return {
        "BOOKLORE_HOST": core_config.config.get("BOOKLORE_HOST", ""),
        "BOOKLORE_USERNAME": core_config.config.get("BOOKLORE_USERNAME", ""),
        "BOOKLORE_PASSWORD": core_config.config.get("BOOKLORE_PASSWORD", ""),
        "BOOKLORE_LIBRARY_ID": core_config.config.get("BOOKLORE_LIBRARY_ID"),
        "BOOKLORE_PATH_ID": core_config.config.get("BOOKLORE_PATH_ID"),
    }


def _booklore_format_error(rejected_files: List[Path]) -> str:
    rejected_exts = sorted(set(f.suffix.lower() for f in rejected_files))
    rejected_list = ", ".join(rejected_exts)
    return (
        f"Booklore does not support {rejected_list}. "
        f"Supported formats: {BOOKLORE_SUPPORTED_FORMATS_LABEL}"
    )


def _post_process_booklore(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback,
) -> Optional[str]:
    from shelfmark.download.postprocess.pipeline import (
        CustomScriptContext,
        OutputPlan,
        cleanup_output_staging,
        is_managed_workspace_path,
        maybe_run_custom_script,
        prepare_output_files,
    )

    if cancel_flag.is_set():
        logger.info("Task %s: cancelled before Booklore upload", task.task_id)
        return None

    try:
        booklore_config = build_booklore_config(_get_booklore_settings())
    except BookloreError as e:
        logger.warning("Task %s: Booklore configuration error: %s", task.task_id, e)
        status_callback("error", str(e))
        return None

    status_callback("resolving", "Preparing Booklore upload")

    output_plan = OutputPlan(
        mode=BOOKLORE_OUTPUT_MODE,
        stage_action=STAGE_MOVE if is_managed_workspace_path(temp_file) else STAGE_NONE,
        staging_dir=build_staging_dir("booklore", task.task_id),
        allow_archive_extraction=True,
    )

    prepared = prepare_output_files(
        temp_file,
        task,
        BOOKLORE_OUTPUT_MODE,
        status_callback,
        output_plan=output_plan,
    )
    if not prepared:
        return None

    logger.debug("Task %s: prepared %d file(s) for Booklore upload", task.task_id, len(prepared.files))

    try:
        unsupported_files = [
            file_path
            for file_path in prepared.files
            if file_path.suffix.lower() not in BOOKLORE_SUPPORTED_EXTENSIONS
        ]
        if unsupported_files:
            error_message = _booklore_format_error(unsupported_files)
            logger.warning("Task %s: %s", task.task_id, error_message)
            status_callback("error", error_message)
            return None

        token = booklore_login(booklore_config)
        logger.info("Task %s: uploading %d file(s) to Booklore", task.task_id, len(prepared.files))

        for index, file_path in enumerate(prepared.files, start=1):
            if cancel_flag.is_set():
                logger.info("Task %s: cancelled during Booklore upload", task.task_id)
                return None
            status_callback("resolving", f"Uploading to Booklore ({index}/{len(prepared.files)})")
            booklore_upload_file(booklore_config, token, file_path)

        if booklore_config.refresh_after_upload:
            try:
                booklore_refresh_library(booklore_config, token)
            except BookloreError as e:
                logger.warning("Task %s: Booklore refresh failed: %s", task.task_id, e)

        logger.info("Task %s: uploaded %d file(s) to Booklore", task.task_id, len(prepared.files))

        destination: Optional[Path]
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
            output_mode=BOOKLORE_OUTPUT_MODE,
            destination=destination,
            final_paths=prepared.files,
            output_details={
                "booklore": {
                    "base_url": booklore_config.base_url,
                    "library_id": booklore_config.library_id,
                    "path_id": booklore_config.path_id,
                    "refresh_after_upload": bool(booklore_config.refresh_after_upload),
                }
            },
        )
        if not maybe_run_custom_script(script_context, status_callback=status_callback):
            return None

        message = "Uploaded to Booklore"
        if len(prepared.files) > 1:
            message = f"Uploaded to Booklore ({len(prepared.files)} files)"
        status_callback("complete", message)
        return f"booklore://{task.task_id}"

    except BookloreError as e:
        logger.warning("Task %s: Booklore upload failed: %s", task.task_id, e)
        status_callback("error", str(e))
        return None
    except Exception as e:
        logger.error_trace("Task %s: unexpected error uploading to Booklore: %s", task.task_id, e)
        status_callback("error", f"Booklore upload failed: {e}")
        return None
    finally:
        cleanup_output_staging(
            prepared.output_plan,
            prepared.working_path,
            task,
            prepared.cleanup_paths,
        )


@register_output(BOOKLORE_OUTPUT_MODE, supports_task=_supports_booklore, priority=10)
def process_booklore_output(
    temp_file: Path,
    task: DownloadTask,
    cancel_flag: Event,
    status_callback,
) -> Optional[str]:
    return _post_process_booklore(temp_file, task, cancel_flag, status_callback)
