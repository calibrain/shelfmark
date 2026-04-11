from __future__ import annotations

import json
from threading import Event
from types import SimpleNamespace

from shelfmark.core.models import DownloadTask
from shelfmark.download.outputs.cwa_sidecar import (
    CWA_SIDECAR_MANIFEST_SETTING,
    build_cwa_manifest,
    cwa_sidecar_manifest_enabled,
    sidecar_path_for,
    write_cwa_sidecar,
)
from shelfmark.download.outputs.folder import (
    _ProcessingPlan,
    process_folder_output,
)
from shelfmark.download.staging import STAGE_NONE


def test_build_cwa_manifest_requires_exact_hardcover_provenance():
    task = DownloadTask(
        task_id="task-1",
        source="prowlarr",
        title="No Manifest",
        metadata_provider="openlibrary",
        metadata_provider_id="ol-123",
    )

    assert build_cwa_manifest(task) is None


def test_build_cwa_manifest_requires_hardcover_id_even_if_slug_is_present():
    task = DownloadTask(
        task_id="task-1b",
        source="prowlarr",
        title="No Exact Identifier",
        metadata_provider="hardcover",
        metadata_provider_id="   ",
        hardcover_slug="mort",
    )

    assert build_cwa_manifest(task) is None


def test_build_cwa_manifest_does_not_invent_slug_from_untrusted_url():
    task = DownloadTask(
        task_id="task-1c",
        source="prowlarr",
        title="Trusted Id Only",
        metadata_provider="hardcover",
        metadata_provider_id="379631",
        metadata_source_url="https://example.com/books/mort",
    )

    assert build_cwa_manifest(task) == {
        "identifiers": {
            "hardcover-id": "379631",
        },
        "provenance": {
            "provider": "hardcover",
            "provider_id": "379631",
        },
    }


def test_write_cwa_sidecar_uses_delivered_filename_and_partial_exact_provenance(tmp_path):
    delivered_path = tmp_path / "Mort.epub"
    delivered_path.write_text("epub")

    task = DownloadTask(
        task_id="task-2",
        source="prowlarr",
        title="Mort",
        metadata_provider="hardcover",
        metadata_provider_id="379631",
        metadata_source_url="https://hardcover.app/books/mort",
    )

    sidecar_path = write_cwa_sidecar(delivered_path, task)

    assert sidecar_path == tmp_path / "Mort.epub.cwa.json"
    assert sidecar_path_for(delivered_path) == sidecar_path
    payload = json.loads(sidecar_path.read_text())
    assert payload == {
        "identifiers": {
            "hardcover-id": "379631",
            "hardcover-slug": "mort",
        },
        "provenance": {
            "provider": "hardcover",
            "provider_id": "379631",
            "hardcover_slug": "mort",
        },
    }


def test_write_cwa_sidecar_includes_explicit_edition_when_available(tmp_path):
    delivered_path = tmp_path / "Wyrd Sisters.epub"
    delivered_path.write_text("epub")

    task = DownloadTask(
        task_id="task-3",
        source="prowlarr",
        title="Wyrd Sisters",
        metadata_provider="hardcover",
        metadata_provider_id="10001",
        hardcover_edition="20002",
        hardcover_slug="wyrd-sisters",
    )

    sidecar_path = write_cwa_sidecar(delivered_path, task)
    payload = json.loads(sidecar_path.read_text())

    assert payload["identifiers"]["hardcover-id"] == "10001"
    assert payload["identifiers"]["hardcover-edition"] == "20002"
    assert payload["identifiers"]["hardcover-slug"] == "wyrd-sisters"


def test_cwa_sidecar_manifest_setting_defaults_disabled(monkeypatch):
    def _config_get(key, default=None, user_id=None):  # noqa: ANN001
        assert key == CWA_SIDECAR_MANIFEST_SETTING
        return default

    monkeypatch.setattr("shelfmark.core.config.config.get", _config_get)

    assert cwa_sidecar_manifest_enabled() is False


def test_process_folder_output_emits_sidecar_for_final_delivered_path_when_setting_enabled(
    monkeypatch, tmp_path
):
    import shelfmark.download.outputs.folder as folder_output
    import shelfmark.download.postprocess.pipeline as pipeline

    destination = tmp_path / "ingest"
    destination.mkdir()
    temp_file = tmp_path / "download.tmp"
    temp_file.write_text("payload")
    delivered_path = destination / "Mort (1987).epub"
    delivered_path.write_text("book")

    task = DownloadTask(
        task_id="task-4",
        source="prowlarr",
        title="Mort",
        metadata_provider="hardcover",
        metadata_provider_id="379631",
        metadata_source_url="https://hardcover.app/books/mort",
    )

    plan = _ProcessingPlan(
        destination=destination,
        organization_mode="rename",
        use_hardlink=False,
        allow_archive_extraction=True,
        stage_action=STAGE_NONE,
        staging_dir=tmp_path / "staging",
        hardlink_source=None,
    )

    monkeypatch.setattr(folder_output, "_build_processing_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(folder_output, "cwa_sidecar_manifest_enabled", lambda: True)
    monkeypatch.setattr(
        pipeline,
        "prepare_output_files",
        lambda *_args, **_kwargs: SimpleNamespace(
            output_plan=SimpleNamespace(stage_action=STAGE_NONE),
            working_path=temp_file,
            files=[temp_file],
            cleanup_paths=[],
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "transfer_book_files",
        lambda *_args, **_kwargs: ([delivered_path], None, {"move": 1}),
    )
    monkeypatch.setattr(pipeline, "is_torrent_source", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(pipeline, "maybe_run_custom_script", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(pipeline, "cleanup_output_staging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "log_plan_steps", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "record_step", lambda *_args, **_kwargs: None)

    statuses: list[tuple[str, str | None]] = []
    result = process_folder_output(
        temp_file,
        task,
        Event(),
        lambda status, message=None: statuses.append((status, message)),
    )

    assert result == str(delivered_path)
    assert (destination / "Mort (1987).epub.cwa.json").exists()
    assert statuses[-1] == ("complete", "Complete")


def test_process_folder_output_keeps_delivery_unchanged_when_sidecar_setting_disabled(monkeypatch, tmp_path):
    import shelfmark.download.outputs.folder as folder_output
    import shelfmark.download.postprocess.pipeline as pipeline

    destination = tmp_path / "ingest"
    destination.mkdir()
    temp_file = tmp_path / "download.tmp"
    temp_file.write_text("payload")
    delivered_path = destination / "Mort.epub"
    delivered_path.write_text("book")

    task = DownloadTask(
        task_id="task-5",
        source="prowlarr",
        title="Mort",
        metadata_provider="hardcover",
        metadata_provider_id="379631",
        metadata_source_url="https://hardcover.app/books/mort",
    )

    plan = _ProcessingPlan(
        destination=destination,
        organization_mode="rename",
        use_hardlink=False,
        allow_archive_extraction=True,
        stage_action=STAGE_NONE,
        staging_dir=tmp_path / "staging",
        hardlink_source=None,
    )

    called = {"write_cwa_sidecar": 0}

    def _unexpected_sidecar_write(*_args, **_kwargs):
        called["write_cwa_sidecar"] += 1
        raise AssertionError("write_cwa_sidecar should not run when sidecar emission is disabled")

    monkeypatch.setattr(folder_output, "_build_processing_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(folder_output, "cwa_sidecar_manifest_enabled", lambda: False)
    monkeypatch.setattr(folder_output, "write_cwa_sidecar", _unexpected_sidecar_write)
    monkeypatch.setattr(
        pipeline,
        "prepare_output_files",
        lambda *_args, **_kwargs: SimpleNamespace(
            output_plan=SimpleNamespace(stage_action=STAGE_NONE),
            working_path=temp_file,
            files=[temp_file],
            cleanup_paths=[],
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "transfer_book_files",
        lambda *_args, **_kwargs: ([delivered_path], None, {"move": 1}),
    )
    monkeypatch.setattr(pipeline, "is_torrent_source", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(pipeline, "maybe_run_custom_script", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(pipeline, "cleanup_output_staging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "log_plan_steps", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "record_step", lambda *_args, **_kwargs: None)

    statuses: list[tuple[str, str | None]] = []
    result = process_folder_output(
        temp_file,
        task,
        Event(),
        lambda status, message=None: statuses.append((status, message)),
    )

    assert result == str(delivered_path)
    assert called["write_cwa_sidecar"] == 0
    assert not (destination / "Mort.epub.cwa.json").exists()
    assert statuses[-1] == ("complete", "Complete")


def test_process_folder_output_logs_sidecar_failure_but_keeps_delivery(monkeypatch, tmp_path):
    import shelfmark.download.outputs.folder as folder_output
    import shelfmark.download.postprocess.pipeline as pipeline

    destination = tmp_path / "ingest"
    destination.mkdir()
    temp_file = tmp_path / "download.tmp"
    temp_file.write_text("payload")
    delivered_path = destination / "Mort.epub"
    delivered_path.write_text("book")

    task = DownloadTask(
        task_id="task-6",
        source="prowlarr",
        title="Mort",
        metadata_provider="hardcover",
        metadata_provider_id="379631",
    )

    plan = _ProcessingPlan(
        destination=destination,
        organization_mode="rename",
        use_hardlink=False,
        allow_archive_extraction=True,
        stage_action=STAGE_NONE,
        staging_dir=tmp_path / "staging",
        hardlink_source=None,
    )

    monkeypatch.setattr(folder_output, "_build_processing_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(folder_output, "cwa_sidecar_manifest_enabled", lambda: True)
    monkeypatch.setattr(
        pipeline,
        "prepare_output_files",
        lambda *_args, **_kwargs: SimpleNamespace(
            output_plan=SimpleNamespace(stage_action=STAGE_NONE),
            working_path=temp_file,
            files=[temp_file],
            cleanup_paths=[],
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "transfer_book_files",
        lambda *_args, **_kwargs: ([delivered_path], None, {"move": 1}),
    )
    monkeypatch.setattr(pipeline, "is_torrent_source", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(pipeline, "maybe_run_custom_script", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(pipeline, "cleanup_output_staging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "log_plan_steps", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "record_step", lambda *_args, **_kwargs: None)

    def _fail_sidecar(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(folder_output, "write_cwa_sidecar", _fail_sidecar)
    result = process_folder_output(
        temp_file,
        task,
        Event(),
        lambda *_args, **_kwargs: None,
    )

    assert result == str(delivered_path)
    assert not (destination / "Mort.epub.cwa.json").exists()
