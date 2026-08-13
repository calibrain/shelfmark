"""Tests for widening the audiobook format list on existing installs.

`initialize_default_configs()` only writes field defaults when a tab has no config file
yet, so widening the default alone would have reached fresh installs only - exactly not
the installs already carrying the narrow m4b/mp3 list that loses FLAC/OPUS releases.
"""

import logging

import pytest

from shelfmark.config.migrations import migrate_audiobook_formats
from shelfmark.core.utils import ARCHIVE_FORMATS, AUDIOBOOK_FORMATS

WIDENED = [*AUDIOBOOK_FORMATS, *ARCHIVE_FORMATS]


@pytest.fixture
def migrate():
    """Run the migration over an in-memory config, returning the resulting config."""

    def run(config: dict | None) -> dict:
        stored = {} if config is None else dict(config)
        saved: dict = {}

        def save(values: dict) -> None:
            saved.update(values)
            stored.update(values)

        migrate_audiobook_formats(
            load_general_config=lambda: stored,
            save_general_config=save,
            widened_formats=WIDENED,
            logger=logging.getLogger("test"),
        )
        return stored

    return run


def test_legacy_default_is_widened(migrate):
    result = migrate({"SUPPORTED_AUDIOBOOK_FORMATS": ["m4b", "mp3"]})

    assert result["SUPPORTED_AUDIOBOOK_FORMATS"] == WIDENED


def test_legacy_default_is_widened_regardless_of_order_or_case(migrate):
    result = migrate({"SUPPORTED_AUDIOBOOK_FORMATS": ["MP3", " m4b "]})

    assert result["SUPPORTED_AUDIOBOOK_FORMATS"] == WIDENED


def test_other_settings_are_preserved(migrate):
    result = migrate({"SUPPORTED_AUDIOBOOK_FORMATS": ["m4b", "mp3"], "SUPPORTED_FORMATS": ["epub"]})

    assert result["SUPPORTED_FORMATS"] == ["epub"]


@pytest.mark.parametrize(
    "customized",
    [
        ["m4b"],  # deliberately narrowed - re-enabling formats would override the choice
        ["mp3", "flac"],
        ["m4b", "mp3", "zip"],
        [],
    ],
)
def test_customized_lists_are_left_alone(migrate, customized):
    result = migrate({"SUPPORTED_AUDIOBOOK_FORMATS": customized})

    assert result["SUPPORTED_AUDIOBOOK_FORMATS"] == customized


def test_absent_key_is_left_alone(migrate):
    """Nothing persisted means the field default already applies - don't write one."""
    result = migrate({"SUPPORTED_FORMATS": ["epub"]})

    assert "SUPPORTED_AUDIOBOOK_FORMATS" not in result


def test_unexpected_type_is_left_alone(migrate):
    result = migrate({"SUPPORTED_AUDIOBOOK_FORMATS": "m4b,mp3"})

    assert result["SUPPORTED_AUDIOBOOK_FORMATS"] == "m4b,mp3"


def test_migration_is_idempotent(migrate):
    once = migrate({"SUPPORTED_AUDIOBOOK_FORMATS": ["m4b", "mp3"]})
    twice = migrate(once)

    assert twice["SUPPORTED_AUDIOBOOK_FORMATS"] == WIDENED
