"""Tests for legacy download-setting migration into the current config model."""

from unittest.mock import MagicMock


def test_migrate_legacy_download_settings_from_ingest_dir_and_use_book_title(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saved: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        registry,
        "load_config_file",
        lambda tab_name: (
            {
                "INGEST_DIR": "/legacy/books",
                "USE_BOOK_TITLE": True,
                "TORRENT_HARDLINK": True,
            }
            if tab_name == "downloads"
            else {}
        ),
    )
    monkeypatch.setattr(
        registry,
        "save_config_file",
        lambda tab_name, values: saved.append((tab_name, values)) or True,
    )

    registry.migrate_legacy_settings()

    assert saved == [
        (
            "downloads",
            {
                "DESTINATION": "/legacy/books",
                "FILE_ORGANIZATION": "rename",
                "HARDLINK_TORRENTS": True,
                "HARDLINK_TORRENTS_AUDIOBOOK": True,
            },
        )
    ]


def test_migrate_legacy_download_settings_moves_content_type_routing(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.setattr(
        registry,
        "load_config_file",
        lambda tab_name: (
            {
                "INGEST_DIR": "/legacy/books",
                "USE_BOOK_TITLE": False,
                "USE_CONTENT_TYPE_DIRECTORIES": True,
                "INGEST_DIR_BOOK_FICTION": "/legacy/fiction",
                "INGEST_DIR_COMIC_BOOK": "/legacy/comics",
            }
            if tab_name == "downloads"
            else {}
        ),
    )
    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_legacy_settings()

    saves.assert_any_call(
        "downloads",
        {
            "DESTINATION": "/legacy/books",
            "FILE_ORGANIZATION": "none",
        },
    )
    saves.assert_any_call(
        "download_sources",
        {
            "AA_CONTENT_TYPE_ROUTING": True,
            "AA_CONTENT_TYPE_DIR_FICTION": "/legacy/fiction",
            "AA_CONTENT_TYPE_DIR_COMIC": "/legacy/comics",
        },
    )


def test_migrate_legacy_download_settings_ignores_pre_release_processing_mode_keys(monkeypatch):
    import shelfmark.core.settings_registry as registry

    saves = MagicMock(return_value=True)

    monkeypatch.setattr(
        registry,
        "load_config_file",
        lambda tab_name: (
            {
                "PROCESSING_MODE": "library",
                "LIBRARY_PATH": "/library",
                "LIBRARY_TEMPLATE": "{Author}/{Title}",
            }
            if tab_name == "downloads"
            else {}
        ),
    )
    monkeypatch.setattr(registry, "save_config_file", saves)

    registry.migrate_legacy_settings()

    saves.assert_not_called()
