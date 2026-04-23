"""Tests for subtitle-less title template variables."""

from pathlib import Path

from shelfmark.core.models import DownloadTask
from shelfmark.download.postprocess.transfer import build_metadata_dict, transfer_book_files


def test_transfer_metadata_includes_primary_title():
    task = DownloadTask(
        task_id="primary-title",
        source="prowlarr",
        title="Salt, Fat, Acid, Heat: Mastering the Elements of Good Cooking",
        subtitle="Mastering the Elements of Good Cooking",
    )

    metadata = build_metadata_dict(task)

    assert metadata["PrimaryTitle"] == "Salt, Fat, Acid, Heat"


def test_single_file_rename_can_use_primary_title(tmp_path: Path, monkeypatch):
    source_dir = tmp_path / "source"
    destination = tmp_path / "destination"
    source_dir.mkdir()
    destination.mkdir()

    source_file = source_dir / "download.epub"
    source_file.write_text("book")

    monkeypatch.setattr(
        "shelfmark.download.postprocess.transfer.get_template",
        lambda *, is_audiobook, organization_mode: "{Author} - {PrimaryTitle}",
    )

    task = DownloadTask(
        task_id="primary-title-rename",
        source="prowlarr",
        title="Salt, Fat, Acid, Heat: Mastering the Elements of Good Cooking",
        author="Samin Nosrat",
        subtitle="Mastering the Elements of Good Cooking",
        format="epub",
        content_type="ebook",
    )

    final_paths, error, _op_counts = transfer_book_files(
        [source_file],
        destination=destination,
        task=task,
        use_hardlink=False,
        is_torrent=False,
        organization_mode="rename",
    )

    assert error is None
    assert len(final_paths) == 1
    assert final_paths[0].name == "Samin Nosrat - Salt, Fat, Acid, Heat.epub"
