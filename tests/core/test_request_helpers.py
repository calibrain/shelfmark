"""Tests for shared request helper normalization utilities."""

from shelfmark.core.request_helpers import normalize_optional_text, populate_request_reviewers


def test_normalize_optional_text_trims_strings():
    assert normalize_optional_text("  hello  ") == "hello"


def test_normalize_optional_text_returns_none_for_empty_or_non_string_values():
    assert normalize_optional_text("") is None
    assert normalize_optional_text(None) is None
    assert normalize_optional_text(123) is None
    assert normalize_optional_text(False) is None


class _Users:
    def __init__(self):
        self.lookups = 0

    def get_user(self, *, user_id):
        self.lookups += 1
        return {1: {"username": "admin@example.com"}}.get(user_id)


def test_populate_request_reviewers_names_the_reviewer_once_per_user():
    users = _Users()
    rows = [{"reviewed_by": 1}, {"reviewed_by": 1}, {"reviewed_by": None}, {"reviewed_by": 99}]

    populate_request_reviewers(rows, users)

    assert [row["reviewer_username"] for row in rows] == [
        "admin@example.com",
        "admin@example.com",
        "",
        "",
    ]
    assert users.lookups == 2
