from __future__ import annotations

from pathlib import Path
from typing import Any


def _get_download_setting_value(
    current_values: dict[str, Any] | None,
    key: str,
    *,
    default: object = None,
) -> object:
    """Read a downloads setting from unsaved form values first, then persisted config."""
    from shelfmark.core.config import config

    current_values = current_values or {}
    if key in current_values:
        return current_values[key]
    if default is None:
        return config.get(key)
    return config.get(key, default)


def _resolve_destination_test_path(
    configured_path: str,
) -> tuple[Path, list[str]]:
    """Resolve a safe path to validate for destination test actions."""
    stripped_path = configured_path.strip()
    details: list[str] = []

    if "{User}" not in stripped_path:
        return Path(stripped_path), details

    base_prefix = stripped_path.split("{User}", 1)[0].rstrip("/")
    if not base_prefix and not stripped_path.startswith("/"):
        return Path(stripped_path), details

    base_path = base_prefix or "/"
    details.append(f"Configured path: {stripped_path}")
    details.append(
        f"Tested base path: {base_path}. The final destination depends on the user name."
    )
    return Path(base_path), details


def _test_folder_destination(
    *,
    current_values: dict[str, Any] | None = None,
    is_audiobook: bool,
) -> dict[str, Any]:
    """Validate a folder destination using current form values."""
    from shelfmark.download.postprocess.destination import validate_destination

    destination_value = _get_download_setting_value(
        current_values,
        "DESTINATION",
        default="/books",
    )
    destination = str(destination_value or "").strip()

    details: list[str] = []
    label = "Books destination"

    if is_audiobook:
        audiobook_value = _get_download_setting_value(
            current_values,
            "DESTINATION_AUDIOBOOK",
            default="",
        )
        audiobook_destination = str(audiobook_value or "").strip()
        if audiobook_destination:
            destination = audiobook_destination
            label = "Audiobook destination"
        else:
            label = "Audiobook destination"
            details.append(
                "Audiobook destination is empty, so Shelfmark will use the Books destination."
            )

    if not destination:
        return {"success": False, "message": f"{label} is required"}

    test_path, path_details = _resolve_destination_test_path(destination)
    details.extend(path_details)

    errors: list[str] = []

    def _status_callback(status: str, message: str | None) -> None:
        if status == "error" and message:
            errors.append(message)

    if not validate_destination(test_path, _status_callback):
        result = {
            "success": False,
            "message": errors[-1] if errors else f"Cannot access destination: {test_path}",
        }
        if details:
            result["details"] = details
        return result

    result = {
        "success": True,
        "message": f"{label} is writable: {test_path}",
    }
    if details:
        result["details"] = details
    return result


def test_books_destination(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the configured books destination."""
    return _test_folder_destination(current_values=current_values, is_audiobook=False)


def test_audiobook_destination(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the configured audiobook destination."""
    return _test_folder_destination(current_values=current_values, is_audiobook=True)
