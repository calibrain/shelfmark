"""Validation shared by the Book-level Request API."""

from __future__ import annotations

from typing import Any

MAX_REQUEST_NOTE_LENGTH = 1000


class RequestServiceError(ValueError):
    """Structured API validation error."""

    def __init__(self, message: str, *, status_code: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def normalize_note(note: object) -> str | None:
    """Validate a requester or administrator note."""
    if note is None:
        return None
    if not isinstance(note, str):
        raise RequestServiceError("note must be a string")
    normalized = note.strip()
    if len(normalized) > MAX_REQUEST_NOTE_LENGTH:
        raise RequestServiceError(f"note must be <= {MAX_REQUEST_NOTE_LENGTH} characters")
    return normalized or None


def sync_delivery_states_from_queue_status(
    _user_db: Any, *, queue_status: dict[str, dict[str, Any]], user_id: int | None = None
) -> list[dict[str, Any]]:
    """Retain Activity's callback seam until its Book-level rewrite.

    Requests are availability signals, not download progress, so queue state
    never changes them. File finalization is the only fulfilment transition.
    """
    del queue_status, user_id
    return []
