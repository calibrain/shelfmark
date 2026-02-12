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
