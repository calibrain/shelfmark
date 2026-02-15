"""Request lifecycle helpers and service-level validation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable, TYPE_CHECKING

from shelfmark.core.request_policy import normalize_content_type, parse_policy_mode


VALID_REQUEST_STATUSES = frozenset({"pending", "fulfilled", "rejected", "cancelled"})
TERMINAL_REQUEST_STATUSES = frozenset({"fulfilled", "rejected", "cancelled"})
VALID_REQUEST_LEVELS = frozenset({"book", "release"})
VALID_DELIVERY_STATES = frozenset(
    {
        "none",
        "unknown",
        "queued",
        "resolving",
        "locating",
        "downloading",
        "complete",
        "error",
        "cancelled",
    }
)
MAX_REQUEST_NOTE_LENGTH = 1000
MAX_REQUEST_JSON_BLOB_BYTES = 10 * 1024


if TYPE_CHECKING:
    from shelfmark.core.user_db import UserDB


class RequestServiceError(ValueError):
    """Structured error raised by request lifecycle service methods."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        code: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def normalize_request_status(status: Any) -> str:
    """Validate and normalize request status values."""
    if not isinstance(status, str):
        raise ValueError(f"Invalid request status: {status}")
    normalized = status.strip().lower()
    if normalized not in VALID_REQUEST_STATUSES:
        raise ValueError(f"Invalid request status: {status}")
    return normalized


def normalize_policy_mode(mode: Any) -> str:
    """Validate and normalize policy mode values."""
    parsed = parse_policy_mode(mode)
    if parsed is None:
        raise ValueError(f"Invalid policy_mode: {mode}")
    return parsed.value


def normalize_request_level(request_level: Any) -> str:
    """Validate and normalize request level values."""
    if not isinstance(request_level, str):
        raise ValueError(f"Invalid request_level: {request_level}")
    normalized = request_level.strip().lower()
    if normalized not in VALID_REQUEST_LEVELS:
        raise ValueError(f"Invalid request_level: {request_level}")
    return normalized


def normalize_delivery_state(state: Any) -> str:
    """Validate and normalize delivery-state values."""
    if not isinstance(state, str):
        raise ValueError(f"Invalid delivery_state: {state}")
    normalized = state.strip().lower()
    if normalized not in VALID_DELIVERY_STATES:
        raise ValueError(f"Invalid delivery_state: {state}")
    return normalized


def validate_request_level_payload(request_level: Any, release_data: Any) -> str:
    """Validate request_level and release_data shape coupling."""
    normalized_level = normalize_request_level(request_level)
    if normalized_level == "release" and release_data is None:
        raise ValueError("request_level=release requires non-null release_data")
    if normalized_level == "book" and release_data is not None:
        raise ValueError("request_level=book requires null release_data")
    return normalized_level


def validate_status_transition(current_status: Any, new_status: Any) -> tuple[str, str]:
    """Validate request status transitions and terminal immutability."""
    current = normalize_request_status(current_status)
    new = normalize_request_status(new_status)
    if current in TERMINAL_REQUEST_STATUSES and new != current:
        raise ValueError("Terminal request statuses are immutable")
    return current, new


def _normalize_match_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def normalize_note(note: Any) -> str | None:
    """Validate request notes and normalize empty strings to None."""
    if note is None:
        return None
    if not isinstance(note, str):
        raise RequestServiceError("note must be a string", status_code=400)
    normalized = note.strip()
    if len(normalized) > MAX_REQUEST_NOTE_LENGTH:
        raise RequestServiceError(
            f"note must be <= {MAX_REQUEST_NOTE_LENGTH} characters",
            status_code=400,
        )
    return normalized or None


def _validate_book_data(book_data: Any) -> dict[str, Any]:
    if not isinstance(book_data, dict):
        raise RequestServiceError("book_data must be an object", status_code=400)

    required_fields = ("title", "author", "provider", "provider_id")
    missing = [field for field in required_fields if not _normalize_match_text(book_data.get(field))]
    if missing:
        raise RequestServiceError(
            f"book_data missing required field(s): {', '.join(missing)}",
            status_code=400,
        )
    return dict(book_data)


def _validate_json_blob_size(field: str, payload: Any) -> None:
    if payload is None:
        return

    try:
        serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise RequestServiceError(f"{field} must be JSON-serializable", status_code=400) from exc

    payload_size = len(serialized.encode("utf-8"))
    if payload_size > MAX_REQUEST_JSON_BLOB_BYTES:
        raise RequestServiceError(
            f"{field} must be <= {MAX_REQUEST_JSON_BLOB_BYTES} bytes",
            status_code=400,
            code="request_payload_too_large",
        )


def _find_duplicate_pending_request(
    user_db: "UserDB",
    *,
    user_id: int,
    title: str,
    author: str,
    content_type: str,
) -> dict[str, Any] | None:
    pending_rows = user_db.list_requests(user_id=user_id, status="pending")
    for row in pending_rows:
        row_book_data = row.get("book_data") or {}
        if not isinstance(row_book_data, dict):
            continue

        row_title = _normalize_match_text(row_book_data.get("title"))
        row_author = _normalize_match_text(row_book_data.get("author"))
        row_content_type = normalize_content_type(
            row.get("content_type") or row_book_data.get("content_type")
        )
        if row_title == title and row_author == author and row_content_type == content_type:
            return row
    return None


def _now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _extract_release_source_id(release_data: Any) -> str | None:
    if not isinstance(release_data, dict):
        return None
    source_id = release_data.get("source_id")
    if not isinstance(source_id, str):
        return None
    normalized = source_id.strip()
    return normalized or None


def _existing_delivery_state(request_row: dict[str, Any]) -> str:
    raw_state = request_row.get("delivery_state")
    if not isinstance(raw_state, str):
        return "none"
    normalized = raw_state.strip().lower()
    return normalized if normalized in VALID_DELIVERY_STATES else "none"


def sync_delivery_states_from_queue_status(
    user_db: "UserDB",
    *,
    queue_status: dict[str, dict[str, Any]],
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Persist delivery-state transitions for fulfilled requests based on queue status."""
    source_delivery_states: dict[str, str] = {}
    for status_key in ("queued", "resolving", "locating", "downloading", "complete", "error", "cancelled"):
        status_bucket = queue_status.get(status_key)
        if not isinstance(status_bucket, dict):
            continue
        for source_id in status_bucket:
            source_delivery_states[source_id] = status_key

    if not source_delivery_states:
        return []

    fulfilled_rows = user_db.list_requests(user_id=user_id, status="fulfilled")
    updated: list[dict[str, Any]] = []

    for row in fulfilled_rows:
        source_id = _extract_release_source_id(row.get("release_data"))
        if source_id is None:
            continue

        delivery_state = source_delivery_states.get(source_id)
        if delivery_state is None:
            continue

        if _existing_delivery_state(row) == delivery_state:
            continue

        updated.append(
            user_db.update_request(
                row["id"],
                delivery_state=delivery_state,
                delivery_updated_at=_now_timestamp(),
            )
        )

    return updated


def create_request(
    user_db: "UserDB",
    *,
    user_id: int,
    source_hint: str | None,
    content_type: Any,
    request_level: Any,
    policy_mode: Any,
    book_data: Any,
    release_data: Any = None,
    note: Any = None,
    max_pending_per_user: int | None = None,
) -> dict[str, Any]:
    """Create a pending request after service-level validation."""
    validated_book_data = _validate_book_data(book_data)
    normalized_note = normalize_note(note)
    normalized_content_type = normalize_content_type(
        content_type or validated_book_data.get("content_type")
    )
    validated_book_data["content_type"] = normalized_content_type

    try:
        normalized_request_level = validate_request_level_payload(request_level, release_data)
        normalized_policy_mode = normalize_policy_mode(policy_mode)
    except ValueError as exc:
        raise RequestServiceError(str(exc), status_code=400) from exc

    _validate_json_blob_size("book_data", validated_book_data)
    _validate_json_blob_size("release_data", release_data)

    if max_pending_per_user is not None:
        pending_count = user_db.count_user_pending_requests(user_id)
        if pending_count >= max_pending_per_user:
            raise RequestServiceError(
                "Maximum pending requests reached for this user",
                status_code=409,
                code="max_pending_reached",
            )

    duplicate = _find_duplicate_pending_request(
        user_db,
        user_id=user_id,
        title=_normalize_match_text(validated_book_data.get("title")),
        author=_normalize_match_text(validated_book_data.get("author")),
        content_type=normalized_content_type,
    )
    if duplicate is not None:
        raise RequestServiceError(
            "Duplicate pending request exists for this title/author/content_type",
            status_code=409,
            code="duplicate_pending_request",
        )

    try:
        return user_db.create_request(
            user_id=user_id,
            source_hint=source_hint,
            content_type=normalized_content_type,
            request_level=normalized_request_level,
            policy_mode=normalized_policy_mode,
            book_data=validated_book_data,
            release_data=release_data,
            note=normalized_note,
        )
    except ValueError as exc:
        raise RequestServiceError(str(exc), status_code=400) from exc


def ensure_request_access(
    user_db: "UserDB",
    *,
    request_id: int,
    actor_user_id: int | None,
    is_admin: bool,
) -> dict[str, Any]:
    """Get request by ID and enforce ownership for non-admin actors."""
    request_row = user_db.get_request(request_id)
    if request_row is None:
        raise RequestServiceError("Request not found", status_code=404)

    if not is_admin:
        if actor_user_id is None or request_row["user_id"] != actor_user_id:
            raise RequestServiceError("Forbidden", status_code=403)

    return request_row


def cancel_request(
    user_db: "UserDB",
    *,
    request_id: int,
    actor_user_id: int,
) -> dict[str, Any]:
    """Cancel a pending request owned by the actor."""
    request_row = ensure_request_access(
        user_db,
        request_id=request_id,
        actor_user_id=actor_user_id,
        is_admin=False,
    )
    if request_row["status"] != "pending":
        raise RequestServiceError(
            "Request is already in a terminal state",
            status_code=409,
            code="stale_transition",
        )

    try:
        return user_db.update_request(
            request_id,
            expected_current_status="pending",
            status="cancelled",
        )
    except ValueError as exc:
        raise RequestServiceError(str(exc), status_code=409, code="stale_transition") from exc


def reject_request(
    user_db: "UserDB",
    *,
    request_id: int,
    admin_user_id: int,
    admin_note: Any = None,
) -> dict[str, Any]:
    """Reject a pending request as admin."""
    request_row = ensure_request_access(
        user_db,
        request_id=request_id,
        actor_user_id=admin_user_id,
        is_admin=True,
    )
    if request_row["status"] != "pending":
        raise RequestServiceError(
            "Request is already in a terminal state",
            status_code=409,
            code="stale_transition",
        )

    normalized_admin_note = None
    if admin_note is not None:
        if not isinstance(admin_note, str):
            raise RequestServiceError("admin_note must be a string", status_code=400)
        normalized_admin_note = admin_note.strip() or None

    try:
        return user_db.update_request(
            request_id,
            expected_current_status="pending",
            status="rejected",
            admin_note=normalized_admin_note,
            reviewed_by=admin_user_id,
            reviewed_at=_now_timestamp(),
        )
    except ValueError as exc:
        raise RequestServiceError(str(exc), status_code=409, code="stale_transition") from exc


def fulfil_request(
    user_db: "UserDB",
    *,
    request_id: int,
    admin_user_id: int,
    queue_release: Callable[..., tuple[bool, str | None]],
    release_data: Any = None,
    admin_note: Any = None,
) -> dict[str, Any]:
    """Fulfil a pending request and queue the release under requesting-user identity."""
    request_row = ensure_request_access(
        user_db,
        request_id=request_id,
        actor_user_id=admin_user_id,
        is_admin=True,
    )
    if request_row["status"] != "pending":
        raise RequestServiceError(
            "Request is already in a terminal state",
            status_code=409,
            code="stale_transition",
        )

    normalized_admin_note = None
    if admin_note is not None:
        if not isinstance(admin_note, str):
            raise RequestServiceError("admin_note must be a string", status_code=400)
        normalized_admin_note = admin_note.strip() or None

    selected_release_data = release_data if release_data is not None else request_row.get("release_data")
    if selected_release_data is not None and not isinstance(selected_release_data, dict):
        raise RequestServiceError("release_data must be an object", status_code=400)

    if request_row["request_level"] == "book" and selected_release_data is None:
        raise RequestServiceError(
            "release_data is required to fulfil book-level requests",
            status_code=400,
        )
    if request_row["request_level"] == "release" and selected_release_data is None:
        raise RequestServiceError(
            "release_data is required to fulfil release-level requests",
            status_code=400,
        )

    _validate_json_blob_size("release_data", selected_release_data)

    requester = user_db.get_user(user_id=request_row["user_id"])
    if requester is None:
        raise RequestServiceError("Requesting user not found", status_code=404)

    queued_release_data = dict(selected_release_data)
    queued_release_data["_request_id"] = request_id

    success, error = queue_release(
        queued_release_data,
        0,
        user_id=request_row["user_id"],
        username=requester.get("username"),
    )
    if not success:
        raise RequestServiceError(
            error or "Failed to queue release",
            status_code=409,
            code="queue_failed",
        )

    try:
        return user_db.update_request(
            request_id,
            expected_current_status="pending",
            status="fulfilled",
            release_data=selected_release_data,
            delivery_state="queued",
            delivery_updated_at=_now_timestamp(),
            last_failure_reason=None,
            admin_note=normalized_admin_note,
            reviewed_by=admin_user_id,
            reviewed_at=_now_timestamp(),
        )
    except ValueError as exc:
        raise RequestServiceError(str(exc), status_code=409, code="stale_transition") from exc


def reopen_failed_request(
    user_db: "UserDB",
    *,
    request_id: int,
    failure_reason: str | None = None,
) -> dict[str, Any] | None:
    """Reopen a failed fulfilled request so admins can re-approve with a new release."""
    normalized_failure_reason = None
    if isinstance(failure_reason, str):
        normalized_failure_reason = failure_reason.strip() or None

    with user_db._lock:
        conn = user_db._connect()
        try:
            current_row = conn.execute(
                "SELECT * FROM download_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            current_request = user_db._parse_request_row(current_row)
            if current_request is None:
                return None

            if current_request.get("status") != "fulfilled":
                return None
            current_delivery_state = _existing_delivery_state(current_request)
            # Terminal hook callbacks can run before delivery-state sync persists "error".
            # Allow reopening fulfilled requests unless they are already complete.
            if current_delivery_state == "complete":
                return None
            if current_delivery_state not in {"error", "cancelled"} and normalized_failure_reason is None:
                return None

            conn.execute(
                """
                UPDATE download_requests
                SET status = 'pending',
                    delivery_state = 'none',
                    delivery_updated_at = NULL,
                    release_data = NULL,
                    last_failure_reason = ?,
                    reviewed_by = NULL,
                    reviewed_at = NULL
                WHERE id = ?
                """,
                (normalized_failure_reason, request_id),
            )
            updated_row = conn.execute(
                "SELECT * FROM download_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            conn.commit()
            return user_db._parse_request_row(updated_row)
        finally:
            conn.close()
