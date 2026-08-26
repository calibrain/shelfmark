"""Tests for multi-book pack planning (shelfmark.download.postprocess.packs)."""

from pathlib import Path

import pytest

from shelfmark.download.postprocess.packs import (
    PackBook,
    PackFile,
    group_files_into_books,
    match_plan_to_files,
    parse_pack_book_name,
    plan_pack,
)

AUDIO = {"m4b", "mp3"}


class TestParsePackBookName:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Book 3 - Howling Dark", ("Howling Dark", 3.0, None)),
            ("Book 03: Howling Dark", ("Howling Dark", 3.0, None)),
            ("03 - Empire of Silence", ("Empire of Silence", 3.0, None)),
            ("2.5 - Interlude", ("Interlude", 2.5, None)),
            ("[03] Empire of Silence", ("Empire of Silence", 3.0, None)),
            ("#3 Empire of Silence", ("Empire of Silence", 3.0, None)),
            ("3. Empire of Silence", ("Empire of Silence", 3.0, None)),
            ("Empire of Silence", ("Empire of Silence", None, None)),
            ("Empire of Silence (2018)", ("Empire of Silence", None, 2018)),
        ],
    )
    def test_strips_series_markers(self, name, expected):
        assert parse_pack_book_name(name, series_name=None) == expected

    def test_strips_leading_series_name_and_trailing_year(self):
        assert parse_pack_book_name(
            "The Expanse 1.0 - Leviathan Wakes (2011)", series_name="The Expanse"
        ) == ("Leviathan Wakes", 1.0, 2011)

    def test_series_name_match_is_case_insensitive(self):
        assert parse_pack_book_name(
            "the expanse 2.5 - Gods of Risk", series_name="The Expanse"
        ) == (
            "Gods of Risk",
            2.5,
            None,
        )

    @pytest.mark.parametrize(
        "name",
        [
            "The Expanse 0.2 - An Expanse Novella - The Churn (2014)",
            "The Expanse 0.2 - The Expanse Novella - The Churn (2014)",
            "The Expanse 0.2 - An Expanse Short Story - The Churn (2014)",
        ],
    )
    def test_strips_series_novella_label(self, name):
        assert parse_pack_book_name(name, series_name="The Expanse") == ("The Churn", 0.2, 2014)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Gods of Risk 2.5 - Gods of Risk", ("Gods of Risk", 2.5, None)),
            ("Cibola Burn 4 - Cibola Burn (2014)", ("Cibola Burn", 4.0, 2014)),
            ("cibola burn 4 - Cibola Burn", ("Cibola Burn", 4.0, None)),
            # Different text on each side is a real "Series N - Title" name, not a repeat.
            ("Sun Eater 2 - Howling Dark", ("Sun Eater 2 - Howling Dark", None, None)),
        ],
    )
    def test_collapses_title_repeated_around_the_position(self, name, expected):
        assert parse_pack_book_name(name, series_name=None) == expected

    def test_bare_numeric_title_is_left_alone(self):
        assert parse_pack_book_name("1984", series_name=None) == ("1984", None, None)

    def test_marker_that_would_leave_nothing_is_left_alone(self):
        assert parse_pack_book_name("Book 3", series_name=None) == ("Book 3", None, None)


class TestPlanPack:
    def test_nested_subfolders_become_separate_books(self):
        files = [
            PackFile("Sun Eater/Book 1 - Empire of Silence/Empire of Silence.m4b", 10),
            PackFile("Sun Eater/Book 2 - Howling Dark/Howling Dark.m4b", 20),
            PackFile("Sun Eater/cover.jpg", 1),
        ]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name=None)
        assert plan.is_pack
        assert [b.title for b in plan.books] == ["Empire of Silence", "Howling Dark"]
        assert [b.series_position for b in plan.books] == [1.0, 2.0]
        assert plan.books[0].files == ["Sun Eater/Book 1 - Empire of Silence/Empire of Silence.m4b"]
        assert plan.ignored == ["Sun Eater/cover.jpg"]

    def test_flat_pack_becomes_one_book_per_file_and_ignores_sidecars(self):
        files = [
            PackFile("The Expanse 1.0 - Leviathan Wakes (2011).m4b", 100),
            PackFile("The Expanse 1.0 - Leviathan Wakes (2011).txt", 1),
            PackFile("The Expanse 2.0 - Caliban's War (2012).m4b", 100),
        ]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name="The Expanse")
        assert plan.is_pack
        assert [(b.title, b.series_position, b.year) for b in plan.books] == [
            ("Leviathan Wakes", 1.0, 2011),
            ("Caliban's War", 2.0, 2012),
        ]
        assert plan.ignored == ["The Expanse 1.0 - Leviathan Wakes (2011).txt"]

    def test_deeper_nesting_collapses_onto_book_folder(self):
        files = [
            PackFile("Book 1/CD1/01.mp3"),
            PackFile("Book 1/CD2/01.mp3"),
            PackFile("Book 2/01.mp3"),
        ]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name=None)
        assert [b.files for b in plan.books] == [
            ["Book 1/CD1/01.mp3", "Book 1/CD2/01.mp3"],
            ["Book 2/01.mp3"],
        ]

    def test_single_wrapping_folder_is_not_a_book_boundary(self):
        # A torrent named "Series" containing one multi-part book is a single book.
        files = [PackFile("Series/Book 1/01.mp3"), PackFile("Series/Book 1/02.mp3")]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name=None)
        assert not plan.is_pack
        assert len(plan.books) == 1

    def test_root_files_and_subfolders_coexist(self):
        files = [PackFile("Novella.m4b"), PackFile("Book 1/a.m4b"), PackFile("Book 1/b.m4b")]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name=None)
        assert [b.files for b in plan.books] == [["Novella.m4b"], ["Book 1/a.m4b", "Book 1/b.m4b"]]

    def test_single_file_is_not_a_pack(self):
        plan = plan_pack([PackFile("Book.m4b")], supported_extensions=AUDIO, series_name=None)
        assert not plan.is_pack
        assert plan.books[0].title == "Book"

    def test_empty_input(self):
        plan = plan_pack([], supported_extensions=AUDIO, series_name=None)
        assert plan.books == []
        assert not plan.is_pack


class TestGroupFilesIntoBooks:
    def test_groups_on_disk_files_by_top_level_folder(self, tmp_path: Path):
        a = tmp_path / "Book 1 - A" / "a.m4b"
        b = tmp_path / "Book 2 - B" / "b.m4b"
        for f in (a, b):
            f.parent.mkdir(parents=True)
            f.write_bytes(b"x")
        groups = group_files_into_books([a, b], series_name=None)
        assert [(g.title, g.series_position, g.files) for g in groups] == [
            ("A", 1.0, [a]),
            ("B", 2.0, [b]),
        ]


class TestMatchPlanToFiles:
    def test_matches_by_relative_path_then_basename(self, tmp_path: Path):
        root = tmp_path / "staging" / "Sun Eater"
        a = root / "Book 1 - A" / "a.m4b"
        b = root / "Book 2 - B" / "b.m4b"
        for f in (a, b):
            f.parent.mkdir(parents=True)
            f.write_bytes(b"x")
        plan = [
            PackBook(title="Alpha", series_position=1.0, year=2001, files=["Book 1 - A/a.m4b"]),
            PackBook(title="Beta", series_position=2.0, year=None, files=["b.m4b"]),
        ]
        groups = match_plan_to_files(plan, [a, b])
        assert [(g.title, g.series_position, g.year, g.files) for g in groups] == [
            ("Alpha", 1.0, 2001, [a]),
            ("Beta", 2.0, None, [b]),
        ]

    def test_unmatched_files_fall_back_to_heuristic_groups(self, tmp_path: Path):
        a = tmp_path / "Book 1 - A" / "a.m4b"
        c = tmp_path / "Book 3 - C" / "c.m4b"
        for f in (a, c):
            f.parent.mkdir(parents=True)
            f.write_bytes(b"x")
        plan = [PackBook(title="Alpha", series_position=1.0, year=None, files=["Book 1 - A/a.m4b"])]
        groups = match_plan_to_files(plan, [a, c])
        assert [(g.title, g.files) for g in groups] == [("Alpha", [a]), ("C", [c])]
