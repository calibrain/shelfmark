"""Tests for request lifecycle validation helpers."""

import os
import tempfile

import pytest

from shelfmark.core.request_policy import PolicyMode
from shelfmark.core.requests_service import (
    MAX_REQUEST_NOTE_LENGTH,
    RequestServiceError,
    cancel_request,
    create_request,
    fulfil_request,
    normalize_policy_mode,
    normalize_request_level,
    normalize_request_status,
    reject_request,
    validate_request_level_payload,
    validate_status_transition,
)
from shelfmark.core.user_db import UserDB


@pytest.fixture
def user_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "users.db"))
        db.initialize()
        yield db


def _book_data(content_type: str = "ebook"):
    return {
        "title": "Example Book",
        "author": "Jane Doe",
        "content_type": content_type,
        "provider": "openlibrary",
        "provider_id": "ol-123",
    }


def _release_data():
    return {
        "source": "prowlarr",
        "source_id": "release-123",
        "title": "Example Book Release",
    }


def test_normalize_request_status_accepts_known_values():
    assert normalize_request_status("pending") == "pending"
    assert normalize_request_status("FULFILLED") == "fulfilled"
    assert normalize_request_status(" rejected ") == "rejected"
    assert normalize_request_status("cancelled") == "cancelled"


def test_normalize_request_status_rejects_unknown_values():
    with pytest.raises(ValueError, match="Invalid request status"):
        normalize_request_status("queued")


def test_normalize_policy_mode_accepts_strings_and_enum():
    assert normalize_policy_mode("download") == "download"
    assert normalize_policy_mode("REQUEST_BOOK") == "request_book"
    assert normalize_policy_mode(PolicyMode.BLOCKED) == "blocked"


def test_normalize_policy_mode_rejects_unknown_values():
    with pytest.raises(ValueError, match="Invalid policy_mode"):
        normalize_policy_mode("allow")


def test_normalize_request_level_accepts_valid_values():
    assert normalize_request_level("book") == "book"
    assert normalize_request_level(" RELEASE ") == "release"


def test_normalize_request_level_rejects_invalid_values():
    with pytest.raises(ValueError, match="Invalid request_level"):
        normalize_request_level("chapter")


def test_validate_request_level_payload_requires_release_data_for_release_level():
    validated_level = validate_request_level_payload("release", {"title": "x"})
    assert validated_level == "release"

    with pytest.raises(ValueError, match="request_level=release requires non-null release_data"):
        validate_request_level_payload("release", None)


def test_validate_request_level_payload_requires_null_release_data_for_book_level():
    validated_level = validate_request_level_payload("book", None)
    assert validated_level == "book"

    with pytest.raises(ValueError, match="request_level=book requires null release_data"):
        validate_request_level_payload("book", {"title": "x"})


def test_validate_status_transition_allows_pending_to_terminal():
    assert validate_status_transition("pending", "fulfilled") == ("pending", "fulfilled")
    assert validate_status_transition("pending", "rejected") == ("pending", "rejected")
    assert validate_status_transition("pending", "cancelled") == ("pending", "cancelled")


def test_validate_status_transition_rejects_terminal_mutation():
    with pytest.raises(ValueError, match="Terminal request statuses are immutable"):
        validate_status_transition("fulfilled", "rejected")

    # No-op re-write to same status is allowed.
    assert validate_status_transition("cancelled", "cancelled") == ("cancelled", "cancelled")


def test_create_request_rejects_overlong_note(user_db):
    user = user_db.create_user(username="alice")

    with pytest.raises(RequestServiceError, match="note must be <="):
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="prowlarr",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=_book_data(),
            note="x" * (MAX_REQUEST_NOTE_LENGTH + 1),
        )


def test_create_request_rejects_duplicate_pending(user_db):
    user = user_db.create_user(username="alice")

    created = create_request(
        user_db,
        user_id=user["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )
    assert created["status"] == "pending"

    with pytest.raises(RequestServiceError, match="Duplicate pending request exists"):
        create_request(
            user_db,
            user_id=user["id"],
            source_hint="prowlarr",
            content_type="ebook",
            request_level="book",
            policy_mode="request_book",
            book_data=_book_data(),
        )


def test_cancel_request_enforces_ownership(user_db):
    alice = user_db.create_user(username="alice")
    bob = user_db.create_user(username="bob")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    with pytest.raises(RequestServiceError, match="Forbidden"):
        cancel_request(
            user_db,
            request_id=created["id"],
            actor_user_id=bob["id"],
        )


def test_reject_request_marks_review_metadata(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    rejected = reject_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        admin_note="Not available",
    )
    assert rejected["status"] == "rejected"
    assert rejected["reviewed_by"] == admin["id"]
    assert rejected["admin_note"] == "Not available"
    assert rejected["reviewed_at"] is not None


def test_fulfil_request_requires_release_data_for_book_level(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data=_book_data(),
    )

    with pytest.raises(RequestServiceError, match="release_data is required to fulfil book-level requests"):
        fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=lambda *_args, **_kwargs: (True, None),
        )


def test_fulfil_request_queues_as_requesting_user(user_db):
    alice = user_db.create_user(username="alice")
    admin = user_db.create_user(username="admin", role="admin")
    created = create_request(
        user_db,
        user_id=alice["id"],
        source_hint="prowlarr",
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=_book_data(),
        release_data=_release_data(),
    )

    captured: dict[str, object] = {}

    def fake_queue_release(release_data, priority, user_id=None, username=None):
        captured["release_data"] = release_data
        captured["priority"] = priority
        captured["user_id"] = user_id
        captured["username"] = username
        return True, None

    fulfilled = fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=fake_queue_release,
        admin_note="Approved",
    )

    assert fulfilled["status"] == "fulfilled"
    assert fulfilled["reviewed_by"] == admin["id"]
    assert captured["priority"] == 0
    assert captured["user_id"] == alice["id"]
    assert captured["username"] == "alice"
    assert isinstance(captured["release_data"], dict)
