from unittest.mock import patch


def _base_email_mode_values() -> dict[str, object]:
    return {
        "BOOKS_OUTPUT_MODE": "email",
        "EMAIL_SMTP_HOST": "smtp.example.com",
        "EMAIL_FROM": "Shelfmark <mail@example.com>",
    }


def test_on_save_downloads_allows_empty_default_email_recipient(monkeypatch):
    from shelfmark.config.settings import _on_save_downloads

    monkeypatch.setattr("shelfmark.config.settings.load_config_file", lambda _tab: {})

    values = {
        **_base_email_mode_values(),
        "EMAIL_RECIPIENT": "",
    }

    result = _on_save_downloads(values)

    assert result["error"] is False
    assert result["values"]["EMAIL_RECIPIENT"] == ""


def test_on_save_downloads_validates_default_email_recipient_format(monkeypatch):
    from shelfmark.config.settings import _on_save_downloads

    monkeypatch.setattr("shelfmark.config.settings.load_config_file", lambda _tab: {})

    values = {
        **_base_email_mode_values(),
        "EMAIL_RECIPIENT": "Reader <reader@example.com>",
    }

    result = _on_save_downloads(values)

    assert result["error"] is True
    assert "valid plain email address" in result["message"]


def test_download_settings_email_recipient_field_uses_default_label():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    email_field = next(field for field in fields if getattr(field, "key", None) == "EMAIL_RECIPIENT")

    assert email_field.label == "Default Email Recipient"
    assert "Optional fallback" in email_field.description


def test_download_settings_booklore_destination_field_defaults_to_library():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    destination_field = next(field for field in fields if getattr(field, "key", None) == "BOOKLORE_DESTINATION")

    assert destination_field.default == "library"
    option_values = {option["value"] for option in destination_field.options}
    assert option_values == {"library", "bookdrop"}


def test_download_settings_grimmory_copy_is_exposed_in_ui_metadata():
    from shelfmark.config.settings import download_settings

    fields = download_settings()

    output_mode_field = next(field for field in fields if getattr(field, "key", None) == "BOOKS_OUTPUT_MODE")
    grimmory_option = next(option for option in output_mode_field.options if option["value"] == "booklore")
    heading_field = next(field for field in fields if getattr(field, "key", None) == "booklore_heading")
    url_field = next(field for field in fields if getattr(field, "key", None) == "BOOKLORE_HOST")

    assert grimmory_option["label"] == "Grimmory (API)"
    assert grimmory_option["description"] == "Upload files directly to Grimmory"
    assert heading_field.title == "Grimmory"
    assert "(Formerly Booklore)" in heading_field.description
    assert url_field.label == "Grimmory URL"


def test_download_settings_booklore_library_and_path_depend_on_library_destination():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    library_field = next(field for field in fields if getattr(field, "key", None) == "BOOKLORE_LIBRARY_ID")
    path_field = next(field for field in fields if getattr(field, "key", None) == "BOOKLORE_PATH_ID")

    assert library_field.show_when == [
        {"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        {"field": "BOOKLORE_DESTINATION", "value": "library"},
    ]
    assert path_field.show_when == [
        {"field": "BOOKS_OUTPUT_MODE", "value": "booklore"},
        {"field": "BOOKLORE_DESTINATION", "value": "library"},
    ]


def test_download_settings_destination_test_buttons_exist():
    from shelfmark.config.settings import download_settings

    fields = download_settings()
    books_button = next(field for field in fields if getattr(field, "key", None) == "test_destination")
    audiobook_button = next(
        field for field in fields if getattr(field, "key", None) == "test_destination_audiobook"
    )

    assert books_button.label == "Test Destination"
    assert books_button.style == "primary"
    assert books_button.show_when == {"field": "BOOKS_OUTPUT_MODE", "value": "folder"}
    assert audiobook_button.label == "Test Destination"
    assert audiobook_button.style == "primary"
    assert audiobook_button.universal_only is True


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


def test_test_books_destination_uses_persisted_value_when_current_values_missing(monkeypatch, tmp_path):
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
