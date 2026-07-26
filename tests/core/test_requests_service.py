"""Tests for the small Book-level request service seam."""

import pytest

from shelfmark.core.requests_service import (
    MAX_REQUEST_NOTE_LENGTH,
    RequestServiceError,
    normalize_note,
    sync_delivery_states_from_queue_status,
)


def test_normalize_note_trims_empty_values_and_preserves_content():
    assert normalize_note(None) is None
    assert normalize_note("   ") is None
    assert normalize_note("  Please add this  ") == "Please add this"


def test_normalize_note_rejects_non_strings_and_overlong_notes():
    with pytest.raises(RequestServiceError, match="note must be a string"):
        normalize_note(["not", "a", "string"])

    with pytest.raises(RequestServiceError, match=f"<= {MAX_REQUEST_NOTE_LENGTH}"):
        normalize_note("x" * (MAX_REQUEST_NOTE_LENGTH + 1))


def test_sync_delivery_states_does_not_mutate_book_requests():
    user_db = object()

    updated = sync_delivery_states_from_queue_status(
        user_db,
        queue_status={"error": {"shared-release": {"id": "shared-release"}}},
        user_id=7,
    )

    assert updated == []
