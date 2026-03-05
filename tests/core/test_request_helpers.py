"""Tests for shared request helper normalization utilities."""

from shelfmark.core.request_helpers import normalize_optional_text


def test_normalize_optional_text_trims_strings():
    assert normalize_optional_text("  hello  ") == "hello"


def test_normalize_optional_text_returns_none_for_empty_or_non_string_values():
    assert normalize_optional_text("") is None
    assert normalize_optional_text(None) is None
    assert normalize_optional_text(123) is None
    assert normalize_optional_text(False) is None
