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
