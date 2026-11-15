"""Tests for general settings definitions."""

from shelfmark.config.settings import general_settings
from shelfmark.core import settings_registry


def test_supported_formats_stay_admin_only():
    """The format lists describe the library, not a reader, so they are not overridable."""
    fields = {field.key: field for field in general_settings() if hasattr(field, "key")}

    assert fields["SUPPORTED_FORMATS"].user_overridable is False
    assert fields["SUPPORTED_AUDIOBOOK_FORMATS"].user_overridable is False


def _fields():
    return {field.key: field for field in general_settings() if hasattr(field, "key")}


def test_the_audiobookshelf_library_check_is_off_by_default():
    fields = _fields()

    assert fields["LIBRARY_CHECK_AUDIOBOOKSHELF_ENABLED"].default is False
    assert fields["AUDIOBOOKSHELF_URL"].placeholder
    assert fields["AUDIOBOOKSHELF_TOKEN"].key == "AUDIOBOOKSHELF_TOKEN"
    assert fields["test_audiobookshelf_library"].callback is not None


def test_the_audiobookshelf_test_button_checks_the_unsaved_form_values(monkeypatch):
    seen = {}

    def _fake_test_connection(provider_name, current_values):
        seen["call"] = (provider_name, current_values)
        return {"success": True, "message": "Connected. Indexed 3 library item(s)."}

    monkeypatch.setattr("shelfmark.core.library_index.test_connection", _fake_test_connection)
    form = {"AUDIOBOOKSHELF_URL": "http://abs:13378", "AUDIOBOOKSHELF_TOKEN": "t"}

    result = settings_registry.execute_action("general", "test_audiobookshelf_library", form)

    assert result["success"] is True
    assert seen["call"] == ("audiobookshelf", form)


def test_the_bookorbit_library_check_is_off_by_default():
    fields = _fields()

    assert fields["LIBRARY_CHECK_BOOKORBIT_EBOOK_ENABLED"].default is False
    assert fields["LIBRARY_CHECK_BOOKORBIT_AUDIOBOOK_ENABLED"].default is False
    assert fields["BOOKORBIT_URL"].placeholder == "http://bookorbit:3000"
    assert isinstance(fields["BOOKORBIT_API_KEY"], settings_registry.PasswordField)
    assert fields["test_bookorbit_library"].callback is not None


def test_the_bookorbit_test_button_reads_the_unsaved_form_values(monkeypatch):
    seen = {}

    def _fake_check_connection(current_values):
        seen["values"] = current_values
        return {"success": True, "message": "BookOrbit ebooks at http://bookorbit:3000: indexed 2"}

    monkeypatch.setattr(
        "shelfmark.core.library_providers.bookorbit.check_connection", _fake_check_connection
    )
    form = {"BOOKORBIT_URL": "http://bookorbit:3000", "BOOKORBIT_API_KEY": "k"}

    result = settings_registry.execute_action("general", "test_bookorbit_library", form)

    assert seen["values"] == form
    assert result["success"] is True
