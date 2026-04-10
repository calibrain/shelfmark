from shelfmark.release_sources.irc import parser


def test_parse_results_file_uses_audiobook_format_settings(monkeypatch):
    values = {
        "SUPPORTED_FORMATS": ["epub"],
        "SUPPORTED_AUDIOBOOK_FORMATS": ["zip", "mp3"],
    }

    monkeypatch.setattr(parser.config, "get", lambda key, default=None: values.get(key, default))

    content = "\n".join(
        [
            "!AudioBot Author Name - Great Audio Book.zip ::INFO:: 1.2GB",
            "!AudioBot Author Name - Great Audio Book.mp3 ::INFO:: 1.1GB",
            "!AudioBot Author Name - Great Audio Book.epub ::INFO:: 5MB",
        ]
    )

    results = parser.parse_results_file(content, content_type="audiobook")

    assert [result.format for result in results] == ["zip", "mp3"]


def test_parse_results_file_uses_book_format_settings_for_ebooks(monkeypatch):
    values = {
        "SUPPORTED_FORMATS": ["epub"],
        "SUPPORTED_AUDIOBOOK_FORMATS": ["zip", "mp3"],
    }

    monkeypatch.setattr(parser.config, "get", lambda key, default=None: values.get(key, default))

    content = "\n".join(
        [
            "!BookBot Author Name - Great Book.zip ::INFO:: 50MB",
            "!BookBot Author Name - Great Book.epub ::INFO:: 5MB",
        ]
    )

    results = parser.parse_results_file(content, content_type="ebook")

    assert [result.format for result in results] == ["epub"]
