from shelfmark.config.settings import download_settings


def test_download_settings_expose_only_immutable_storage_controls():
    keys = {field.key for field in download_settings()}

    assert {"DESTINATION", "test_destination", "HARDLINK_TORRENTS"} <= keys
    assert (
        not {
            "DESTINATION_AUDIOBOOK",
            "FILE_ORGANIZATION",
            "FILE_ORGANIZATION_AUDIOBOOK",
            "TEMPLATE_RENAME",
            "TEMPLATE_ORGANIZE",
            "TEMPLATE_AUDIOBOOK_RENAME",
            "TEMPLATE_AUDIOBOOK_ORGANIZE",
            "HARDLINK_TORRENTS_AUDIOBOOK",
        }
        & keys
    )
