"""Tests for the current destination and file-organization policy helpers."""

from pathlib import Path


def test_get_destination_uses_current_destination(monkeypatch):
    import shelfmark.core.utils as utils
    from shelfmark.core.config import config

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None, **_kwargs: {
            "DESTINATION": "/srv/books",
        }.get(key, default),
    )

    assert utils.get_destination() == Path("/srv/books")


def test_get_destination_audiobook_falls_back_to_books_destination(monkeypatch):
    import shelfmark.core.utils as utils
    from shelfmark.core.config import config

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None, **_kwargs: {
            "DESTINATION": "/srv/books",
            "DESTINATION_AUDIOBOOK": "",
        }.get(key, default),
    )

    assert utils.get_destination(is_audiobook=True) == Path("/srv/books")


def test_get_destination_falls_back_to_legacy_ingest_dir(monkeypatch):
    import shelfmark.core.utils as utils
    from shelfmark.core.config import config

    monkeypatch.setattr(
        config,
        "get",
        lambda key, default=None, **_kwargs: {
            "DESTINATION": "",
            "INGEST_DIR": "/legacy/ingest",
        }.get(key, default),
    )

    assert utils.get_destination() == Path("/legacy/ingest")


def test_get_file_organization_uses_current_keys(monkeypatch):
    import shelfmark.download.postprocess.policy as policy

    monkeypatch.setattr(
        policy.core_config.config,
        "get",
        lambda key, default=None: {
            "FILE_ORGANIZATION": "organize",
            "FILE_ORGANIZATION_AUDIOBOOK": "none",
        }.get(key, default),
    )

    assert policy.get_file_organization(is_audiobook=False) == "organize"
    assert policy.get_file_organization(is_audiobook=True) == "none"


def test_get_file_organization_ignores_pre_release_processing_mode_keys(monkeypatch):
    import shelfmark.download.postprocess.policy as policy

    monkeypatch.setattr(
        policy.core_config.config,
        "get",
        lambda key, default=None: {
            "FILE_ORGANIZATION": "",
            "PROCESSING_MODE": "library",
        }.get(key, default),
    )

    assert policy.get_file_organization(is_audiobook=False) == "rename"


def test_get_template_uses_current_template_keys(monkeypatch):
    import shelfmark.download.postprocess.policy as policy

    monkeypatch.setattr(
        policy.core_config.config,
        "get",
        lambda key, default=None: {
            "TEMPLATE_RENAME": "{Author} - {Title}",
            "TEMPLATE_AUDIOBOOK_ORGANIZE": "{Author}/{Title}{ - PartNumber}",
        }.get(key, default),
    )

    assert (
        policy.get_template(is_audiobook=False, organization_mode="rename") == "{Author} - {Title}"
    )
    assert (
        policy.get_template(is_audiobook=True, organization_mode="organize")
        == "{Author}/{Title}{ - PartNumber}"
    )


def test_get_template_defaults_when_missing_and_ignores_pre_release_library_templates(monkeypatch):
    import shelfmark.download.postprocess.policy as policy

    monkeypatch.setattr(
        policy.core_config.config,
        "get",
        lambda key, default=None: {
            "TEMPLATE_ORGANIZE": "",
            "TEMPLATE_RENAME": "",
            "LIBRARY_TEMPLATE": "{Legacy}/{Template}",
            "TEMPLATE_AUDIOBOOK_RENAME": "",
            "LIBRARY_TEMPLATE_AUDIOBOOK": "{LegacyAudio}/{Template}",
        }.get(key, default),
    )

    assert (
        policy.get_template(is_audiobook=False, organization_mode="organize")
        == "{Author}/{Title} ({Year})"
    )
    assert (
        policy.get_template(is_audiobook=False, organization_mode="rename")
        == "{Author} - {Title} ({Year})"
    )
    assert (
        policy.get_template(is_audiobook=True, organization_mode="rename")
        == "{Author} - {Title} ({Year})"
    )
