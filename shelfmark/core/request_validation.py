"""Shared request validation and normalization helpers."""

from __future__ import annotations

from enum import StrEnum

from shelfmark.core.models import QueueStatus
from shelfmark.core.request_policy import parse_policy_mode


class RequestStatus(StrEnum):
    """Enum for request lifecycle statuses."""

    PENDING = "pending"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


DELIVERY_STATE_NONE = "none"

VALID_REQUEST_STATUSES = frozenset(RequestStatus)
TERMINAL_REQUEST_STATUSES = frozenset(
    {
        RequestStatus.FULFILLED,
        RequestStatus.REJECTED,
        RequestStatus.CANCELLED,
    }
)
VALID_REQUEST_LEVELS = frozenset({"book", "release"})
VALID_DELIVERY_STATES = frozenset({DELIVERY_STATE_NONE} | set(QueueStatus))


def normalize_request_status(status: object) -> str:
    """Validate and normalize request status values."""
    if not isinstance(status, str):
        msg = f"Invalid request status: {status}"
        raise TypeError(msg)
    normalized = status.strip().lower()
    if normalized not in VALID_REQUEST_STATUSES:
        msg = f"Invalid request status: {status}"
        raise ValueError(msg)
    return normalized


def normalize_policy_mode(mode: object) -> str:
    """Validate and normalize policy mode values."""
    parsed = parse_policy_mode(mode)
    if parsed is None:
        msg = f"Invalid policy_mode: {mode}"
        raise ValueError(msg)
    return parsed.value


def normalize_request_level(request_level: object) -> str:
    """Validate and normalize request level values."""
    if not isinstance(request_level, str):
        msg = f"Invalid request_level: {request_level}"
        raise TypeError(msg)
    normalized = request_level.strip().lower()
    if normalized not in VALID_REQUEST_LEVELS:
        msg = f"Invalid request_level: {request_level}"
        raise ValueError(msg)
    return normalized


def normalize_delivery_state(state: object) -> str:
    """Validate and normalize delivery-state values."""
    if not isinstance(state, str):
        msg = f"Invalid delivery_state: {state}"
        raise TypeError(msg)
    normalized = state.strip().lower()
    if normalized not in VALID_DELIVERY_STATES:
        msg = f"Invalid delivery_state: {state}"
        raise ValueError(msg)
    return normalized


def validate_request_level_payload(request_level: object, release_data: object) -> str:
    """Validate request_level and release_data shape coupling."""
    normalized_level = normalize_request_level(request_level)
    if normalized_level == "release" and release_data is None:
        msg = "request_level=release requires non-null release_data"
        raise ValueError(msg)
    if normalized_level == "book" and release_data is not None:
        msg = "request_level=book requires null release_data"
        raise ValueError(msg)
    return normalized_level


def validate_status_transition(current_status: object, new_status: object) -> tuple[str, str]:
    """Validate request status transitions and terminal immutability."""
    current = normalize_request_status(current_status)
    new = normalize_request_status(new_status)
    if current in TERMINAL_REQUEST_STATUSES and new != current:
        msg = "Terminal request statuses are immutable"
        raise ValueError(msg)
    return current, new
