"""Tests for the cross-format markers in the General settings tab."""

from __future__ import annotations

from typing import Any

import pytest

from shelfmark.config.settings import general_settings
from shelfmark.core import settings_registry
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    TextField,
)


def _field_map() -> dict[str, Any]:
    return {field.key: field for field in general_settings() if hasattr(field, "key")}


def test_cross_format_fields_and_defaults() -> None:
    fields = _field_map()

    heading = fields["cross_format_heading"]
    assert isinstance(heading, HeadingField)
    assert "calibredb" in heading.description

    enabled = fields["CROSS_FORMAT_ABS_TAGGING_ENABLED"]
    assert isinstance(enabled, CheckboxField)
    assert enabled.default is False

    tag = fields["CROSS_FORMAT_ABS_TAG"]
    assert isinstance(tag, TextField)
    assert tag.default == "ebook-in-library"

    assert isinstance(fields["preview_cross_format_tags"], ActionButton)
    assert isinstance(fields["apply_cross_format_tags"], ActionButton)


def test_preview_button_dry_runs_with_the_form_values(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[bool, dict[str, Any] | None]] = []

    def fake_sync(*, dry_run: bool, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        seen.append((dry_run, overrides))
        return {"summary": True}

    monkeypatch.setattr("shelfmark.core.cross_format.sync_audiobookshelf_tags", fake_sync)
    monkeypatch.setattr("shelfmark.core.cross_format.describe_summary", lambda s: "2 pair(s).")

    result = settings_registry.execute_action(
        "general", "preview_cross_format_tags", {"CROSS_FORMAT_ABS_TAG": "x"}
    )

    assert result == {"success": True, "message": "Dry run: 2 pair(s)."}
    assert seen == [(True, {"CROSS_FORMAT_ABS_TAG": "x"})]


def test_apply_button_writes_with_saved_settings_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake_sync(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs)
        return {"forbidden": False}

    monkeypatch.setattr("shelfmark.core.cross_format.sync_audiobookshelf_tags", fake_sync)
    monkeypatch.setattr("shelfmark.core.cross_format.describe_summary", lambda s: "done")

    result = settings_registry.execute_action(
        "general", "apply_cross_format_tags", {"CROSS_FORMAT_ABS_TAG": "x"}
    )

    assert result == {"success": True, "message": "done"}
    assert seen == [{"dry_run": False}]


def test_buttons_report_a_refusal_as_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from shelfmark.core.cross_format import CrossFormatDisabledError

    def fake_sync(**_kwargs: Any) -> dict[str, Any]:
        raise CrossFormatDisabledError("tagging is off")

    monkeypatch.setattr("shelfmark.core.cross_format.sync_audiobookshelf_tags", fake_sync)

    result = settings_registry.execute_action("general", "apply_cross_format_tags")

    assert result == {"success": False, "message": "tagging is off"}
