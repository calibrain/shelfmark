"""Small Hardcover-specific models and errors."""

from dataclasses import dataclass


@dataclass(frozen=True)
class HardcoverBookTargetState:
    """Current Hardcover target state for a specific book."""

    user_book_id: int | None
    status_id: int | None
    list_book_ids: dict[int, int]


class HardcoverGraphQLError(ValueError):
    """GraphQL request was rejected by Hardcover."""


class HardcoverTargetPayloadError(RuntimeError):
    """Hardcover returned an invalid payload while loading book targets."""
