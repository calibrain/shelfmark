"""Shared request validation and normalization helpers."""

from __future__ import annotations

from enum import Enum
from typing import Any

from shelfmark.core.models import QueueStatus
from shelfmark.core.request_policy import parse_policy_mode


class RequestStatus(str, Enum):
    """Enum for request lifecycle statuses."""
    PENDING = "pending"
    FULFILLED = "fulfilled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


DELIVERY_STATE_NONE = "none"

VALID_REQUEST_STATUSES = frozenset(RequestStatus)
TERMINAL_REQUEST_STATUSES = frozenset({
    RequestStatus.FULFILLED, RequestStatus.REJECTED, RequestStatus.CANCELLED,
})
VALID_REQUEST_LEVELS = frozenset({"book", "release"})
VALID_DELIVERY_STATES = frozenset({DELIVERY_STATE_NONE} | set(QueueStatus))


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
