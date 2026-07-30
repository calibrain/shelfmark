from unittest.mock import patch


def test_download_settings_destination_test_buttons_exist():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    books_button = next(
        field for field in fields if getattr(field, "key", None) == "test_destination"
    )
    audiobook_button = next(
        field for field in fields if getattr(field, "key", None) == "test_destination_audiobook"
    )

    assert books_button.label == "Test Destination"
    assert books_button.style == "primary"
    assert audiobook_button.label == "Test Destination"
    assert audiobook_button.style == "primary"
    assert audiobook_button.universal_only is True


def test_download_settings_naming_templates_use_wrapped_custom_component():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    fields_by_key = {getattr(field, "key", None): field for field in fields}

    expected = {
        "template_rename_editor": "TEMPLATE_RENAME",
        "template_organize_editor": "TEMPLATE_ORGANIZE",
        "template_audiobook_rename_editor": "TEMPLATE_AUDIOBOOK_RENAME",
        "template_audiobook_organize_editor": "TEMPLATE_AUDIOBOOK_ORGANIZE",
    }

    for editor_key, value_key in expected.items():
        editor = fields_by_key[editor_key]

        assert editor.get_field_type() == "CustomComponentField"
        assert editor.component == "naming_template"
        assert editor.wrap_in_field_wrapper is True
        assert editor.get_bind_keys() == [value_key]
        assert [field.key for field in editor.value_fields] == [value_key]
        assert editor.label == editor.value_fields[0].label

    for value_key in expected.values():
        assert value_key not in fields_by_key


def test_download_settings_naming_template_value_fields_are_registered():
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core import settings_registry

    field_map = settings_registry.get_settings_field_map(tab_name="downloads")

    for value_key in (
        "TEMPLATE_RENAME",
        "TEMPLATE_ORGANIZE",
        "TEMPLATE_AUDIOBOOK_RENAME",
        "TEMPLATE_AUDIOBOOK_ORGANIZE",
    ):
        assert value_key in field_map


def test_download_settings_naming_template_serialization_keeps_value_fields_hidden():
    from shelfmark.config.settings import download_settings
    from shelfmark.core import settings_registry
    from shelfmark.core.settings_registry import SettingsTab

    tab = SettingsTab(name="downloads", display_name="Downloads", fields=download_settings())
    serialized_tab = settings_registry.serialize_tab(tab)
    serialized_fields = {field["key"]: field for field in serialized_tab["fields"]}

    editor = serialized_fields["template_organize_editor"]
    bound_fields = editor.get("boundFields", [])

    assert editor["component"] == "naming_template"
    assert editor["wrapInFieldWrapper"] is True
    assert editor["bindKeys"] == ["TEMPLATE_ORGANIZE"]
    assert [field["key"] for field in bound_fields] == ["TEMPLATE_ORGANIZE"]
    assert bound_fields[0]["hiddenInUi"] is True
    assert bound_fields[0]["placeholder"] == "{Author}/{Series/}{Title} ({Year})"


def test_test_books_destination_uses_current_values(tmp_path):
    from shelfmark.config.download_settings_handlers import check_books_destination

    destination = tmp_path / "books"

    result = check_books_destination({"DESTINATION": str(destination)})

    assert result["success"] is True
    assert result["message"] == f"Books destination is writable: {destination}"
    assert destination.exists()


def test_test_audiobook_destination_falls_back_to_books_destination(tmp_path):
    from shelfmark.config.download_settings_handlers import check_audiobook_destination

    destination = tmp_path / "books"

    result = check_audiobook_destination(
        {
            "DESTINATION": str(destination),
            "DESTINATION_AUDIOBOOK": "",
        }
    )

    assert result["success"] is True
    assert result["message"] == (
        f"Audiobook destination is writable: {destination} (using the Books destination)"
    )


def test_test_books_destination_uses_base_path_for_user_placeholder(tmp_path):
    from shelfmark.config.download_settings_handlers import check_books_destination

    destination = tmp_path / "books"

    result = check_books_destination({"DESTINATION": f"{destination}/{{User}}"})

    assert result["success"] is True
    assert result["message"] == (
        f"Books destination is writable: {destination} "
        f"(tested base path {destination} from configured template {destination}/{{User}})"
    )
    assert not (destination / "{User}").exists()


def test_test_books_destination_uses_base_path_for_lowercase_user_placeholder(tmp_path):
    from shelfmark.config.download_settings_handlers import check_books_destination

    destination = tmp_path / "books"

    result = check_books_destination({"DESTINATION": f"{destination}/{{user}}"})

    assert result["success"] is True
    assert result["message"] == (
        f"Books destination is writable: {destination} "
        f"(tested base path {destination} from configured template {destination}/{{user}})"
    )
    assert not (destination / "{user}").exists()


def test_test_books_destination_rejects_relative_user_placeholder_path():
    from shelfmark.config.download_settings_handlers import check_books_destination

    result = check_books_destination({"DESTINATION": "{User}/books"})

    assert result["success"] is False
    assert result["message"] == "Destination must be absolute: {User}/books"


def test_test_books_destination_requires_value():
    from shelfmark.config.download_settings_handlers import check_books_destination

    result = check_books_destination({"DESTINATION": ""})

    assert result["success"] is False
    assert result["message"] == "Books destination is required"


def test_test_books_destination_uses_persisted_value_when_current_values_missing(
    monkeypatch, tmp_path
):
    from shelfmark.config.download_settings_handlers import check_books_destination
    from shelfmark.core.config import config

    destination = tmp_path / "persisted-books"

    def _fake_get(key: str, default=None):
        if key == "DESTINATION":
            return str(destination)
        return default

    monkeypatch.setattr(config, "get", _fake_get)

    result = check_books_destination()

    assert result["success"] is True
    assert result["message"] == f"Books destination is writable: {destination}"


def test_test_audiobook_destination_preserves_books_fallback_suffix_on_failure(tmp_path):
    from shelfmark.config.download_settings_handlers import check_audiobook_destination

    destination = tmp_path / "books"

    def _fake_validate_destination(path, status_callback):
        status_callback("error", f"Destination not writable: {path}")
        return False

    with patch(
        "shelfmark.download.postprocess.destination.validate_destination",
        side_effect=_fake_validate_destination,
    ):
        result = check_audiobook_destination(
            {
                "DESTINATION": str(destination),
                "DESTINATION_AUDIOBOOK": "",
            }
        )

    assert result["success"] is False
    assert result["message"] == (
        f"Destination not writable: {destination} (using the Books destination)"
    )


def test_execute_action_passes_unsaved_values_to_destination_test(tmp_path):
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core.settings_registry import execute_action

    destination = tmp_path / "action-books"
    captured: dict[str, object] = {}

    def _fake_validate_destination(path, status_callback):
        captured["path"] = path
        return True

    with patch(
        "shelfmark.download.postprocess.destination.validate_destination",
        side_effect=_fake_validate_destination,
    ):
        result = execute_action(
            "downloads",
            "test_destination",
            {"DESTINATION": str(destination)},
        )

    assert result["success"] is True
    assert captured["path"] == destination


def test_download_source_settings_include_direct_download_toggle():
    from shelfmark.config.settings import download_source_settings

    fields = download_source_settings()
    toggle_field = next(
        field for field in fields if getattr(field, "key", None) == "DIRECT_DOWNLOAD_ENABLED"
    )

    assert toggle_field.default is False
    assert "Add your own mirror URLs" in toggle_field.description


def test_download_source_settings_include_distant_path_language_toggle():
    from shelfmark.config.settings import download_source_settings

    fields = download_source_settings()
    toggle_field = next(
        field
        for field in fields
        if getattr(field, "key", None) == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH"
    )

    assert toggle_field.default is False
    assert "distant path" in toggle_field.description.lower()


def test_fast_source_options_lock_entries_without_mirror_or_donator_requirements(monkeypatch):
    from shelfmark.config.settings import _get_fast_source_options

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        values = {
            "AA_DONATOR_KEY": "",
        }
        return values.get(key, default)

    monkeypatch.setattr("shelfmark.core.config.config.get", _fake_get)
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: False)
    monkeypatch.setattr("shelfmark.core.mirrors.has_libgen_mirror_configuration", lambda: True)

    options = {option["id"]: option for option in _get_fast_source_options()}

    assert options["aa-fast"]["isLocked"] is True
    assert (
        options["aa-fast"]["disabledReason"] == "Add at least one Anna's Archive mirror in Mirrors"
    )
    assert options["libgen"]["isLocked"] is False
    assert options["libgen"]["disabledReason"] is None


def test_slow_source_options_lock_entries_until_mirror_dependencies_exist(monkeypatch):
    from shelfmark.config.settings import _get_slow_source_options

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        values = {
            "USE_CF_BYPASS": True,
        }
        return values.get(key, default)

    monkeypatch.setattr("shelfmark.core.config.config.get", _fake_get)
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True)
    monkeypatch.setattr("shelfmark.core.mirrors.has_welib_mirror_configuration", lambda: False)
    monkeypatch.setattr("shelfmark.core.mirrors.has_zlib_mirror_configuration", lambda: False)

    options = {option["id"]: option for option in _get_slow_source_options()}

    assert options["aa-slow-nowait"]["isLocked"] is False
    assert options["aa-slow-wait"]["isLocked"] is False
    assert options["welib"]["isLocked"] is True
    assert options["welib"]["disabledReason"] == "Add at least one Welib mirror in Mirrors"
    assert options["zlib"]["isLocked"] is True
    assert options["zlib"]["disabledReason"] == "Add at least one Z-Library mirror in Mirrors"
