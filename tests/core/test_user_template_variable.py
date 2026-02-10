"""Tests for {User} template variable in folder destination paths."""

import pytest

from shelfmark.core.naming import KNOWN_TOKENS, parse_naming_template


class TestUserInKnownTokens:
    """User should be a recognized template token."""

    def test_user_in_known_tokens(self):
        assert "user" in KNOWN_TOKENS

    def test_user_token_parsed(self):
        result = parse_naming_template("{User}", {"User": "alice"})
        assert result == "alice"

    def test_user_token_case_insensitive(self):
        result = parse_naming_template("{user}", {"User": "alice"})
        assert result == "alice"


class TestUserTemplateSubstitution:
    """User variable should work in organize templates with path separators."""

    def test_user_in_organize_template(self):
        metadata = {"Author": "Author1", "Title": "Book1", "Year": "2024", "User": "alice"}
        result = parse_naming_template("{User}/{Author}/{Title} ({Year})", metadata)
        assert result == "alice/Author1/Book1 (2024)"

    def test_user_empty_when_not_set(self):
        metadata = {"Author": "Author1", "Title": "Book1", "User": None}
        result = parse_naming_template("{User}/{Author}/{Title}", metadata)
        # Empty user should be cleaned up, no leading slash
        assert result == "Author1/Book1"

    def test_user_with_prefix_suffix(self):
        metadata = {"Author": "Author1", "Title": "Book1", "User": "bob"}
        result = parse_naming_template("{User}/books/{Author}/{Title}", metadata)
        assert result == "bob/books/Author1/Book1"

    def test_user_sanitized(self):
        metadata = {"User": "user:with*special", "Title": "Book1"}
        result = parse_naming_template("{User}/{Title}", metadata)
        # Special chars should be replaced with underscores
        assert ":" not in result
        assert "*" not in result

    def test_user_missing_from_metadata(self):
        metadata = {"Author": "Author1", "Title": "Book1"}
        result = parse_naming_template("{User}/{Author}/{Title}", metadata)
        assert result == "Author1/Book1"


class TestBuildMetadataWithUser:
    """build_metadata_dict should include User when task has user_id."""

    def test_build_metadata_includes_user(self):
        from shelfmark.core.models import DownloadTask
        from shelfmark.download.postprocess.transfer import build_metadata_dict

        task = DownloadTask(
            task_id="test-1",
            source="direct_download",
            title="Book1",
            author="Author1",
            user_id=1,
            username="alice",
        )
        metadata = build_metadata_dict(task)
        assert metadata["User"] == "alice"

    def test_build_metadata_user_none_when_no_user_id(self):
        from shelfmark.core.models import DownloadTask
        from shelfmark.download.postprocess.transfer import build_metadata_dict

        task = DownloadTask(
            task_id="test-2",
            source="direct_download",
            title="Book1",
            author="Author1",
            user_id=None,
            username=None,
        )
        metadata = build_metadata_dict(task)
        assert metadata.get("User") is None
