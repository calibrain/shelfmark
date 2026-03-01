"""Tests for {OriginalName} template variable support."""

from pathlib import Path

from shelfmark.core.models import DownloadTask
from shelfmark.core.naming import KNOWN_TOKENS, parse_naming_template
from shelfmark.download.postprocess.transfer import transfer_book_files


class TestOriginalNameInKnownTokens:
    def test_original_name_in_known_tokens(self):
        assert "originalname" in KNOWN_TOKENS

    def test_original_name_token_parsed(self):
        result = parse_naming_template("{OriginalName}", {"OriginalName": "Part 1 of 2"})
        assert result == "Part 1 of 2"

    def test_original_name_token_case_insensitive(self):
        result = parse_naming_template("{originalname}", {"OriginalName": "Chapter 01"})
        assert result == "Chapter 01"


class TestOriginalNameTransferTemplates:
    def test_single_file_rename_can_use_original_name(self, tmp_path: Path, monkeypatch):
        source_dir = tmp_path / "source"
        destination = tmp_path / "destination"
        source_dir.mkdir()
        destination.mkdir()

        source_file = source_dir / "Part 1 of 2.mp3"
        source_file.write_text("audio")

        monkeypatch.setattr(
            "shelfmark.download.postprocess.transfer.get_template",
            lambda _is_audiobook, mode: "{OriginalName}" if mode == "rename" else "{Author}/{Title}",
        )

        task = DownloadTask(
            task_id="original-name-rename",
            source="direct_download",
            title="Archive Audio",
            author="Tester",
            format="mp3",
            content_type="audiobook",
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
        assert final_paths[0].name == "Part 1 of 2.mp3"

    def test_multifile_organize_can_use_original_name(self, tmp_path: Path, monkeypatch):
        source_dir = tmp_path / "source"
        destination = tmp_path / "destination"
        source_dir.mkdir()
        destination.mkdir()

        part2 = source_dir / "Part 2 of 2.mp3"
        part1 = source_dir / "Part 1 of 2.mp3"
        part2.write_text("audio2")
        part1.write_text("audio1")

        monkeypatch.setattr(
            "shelfmark.download.postprocess.transfer.get_template",
            lambda _is_audiobook, mode: "{Author}/{Title}/{OriginalName}",
        )

        task = DownloadTask(
            task_id="original-name-organize",
            source="direct_download",
            title="Archive Audio",
            author="Tester",
            format="mp3",
            content_type="audiobook",
        )

        final_paths, error, _op_counts = transfer_book_files(
            [part2, part1],
            destination=destination,
            task=task,
            use_hardlink=False,
            is_torrent=False,
            organization_mode="organize",
        )

        assert error is None
        assert len(final_paths) == 2
        assert {path.name for path in final_paths} == {"Part 1 of 2.mp3", "Part 2 of 2.mp3"}
        assert all(path.parent == destination / "Tester" / "Archive Audio" for path in final_paths)
