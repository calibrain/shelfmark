"""Tests for the cross-format markers: pair computation and the Audiobookshelf tag sync."""

from __future__ import annotations

from typing import Any

import pytest

from shelfmark.core import cross_format, library_index
from shelfmark.core.library_providers import LibraryEntry, audiobookshelf, calibre
from shelfmark.core.library_providers.audiobookshelf import AudiobookshelfForbiddenError
from tests.core.fakes import FakeConfig, capture_log

pytestmark = pytest.mark.usefixtures("fake_app_config")


# --- building entries the way the real providers do ---------------------------------


def _audiobook(
    item_id: str,
    title: str,
    author: str,
    *,
    series: str | None = None,
    tags: list[str] | None = None,
    **meta: Any,
) -> LibraryEntry:
    item = {
        "id": item_id,
        "relPath": f"{author}/{title}",
        "media": {
            "metadata": {"title": title, "authorName": author, **meta},
            "tags": list(tags or []),
        },
    }
    if series:
        item["media"]["metadata"]["seriesName"] = series
    return audiobookshelf._item_to_entry(item)


class _CalibreRows:
    """A stand-in for the metadata.db cursor ``calibre._read_entries`` reads from."""

    def __init__(self) -> None:
        # (id, title, series_index): Calibre keeps a position for every book, 1.0 by default.
        self.books: list[tuple[int, str, float]] = []
        self.authors: list[tuple[int, str]] = []
        self.series: list[tuple[int, str]] = []
        self.identifiers: list[tuple[int, str, str]] = []

    def add(
        self,
        book_id: int,
        title: str,
        *authors: str,
        series: str | None = None,
        identifiers: dict[str, str] | None = None,
    ) -> None:
        self.books.append((book_id, title, 1.0))
        self.authors.extend((book_id, name) for name in authors)
        if series:
            self.series.append((book_id, series))
        self.identifiers.extend((book_id, k, v) for k, v in (identifiers or {}).items())

    def execute(self, sql: str) -> _CalibreRows._Cursor:
        if sql == calibre._BOOKS_SQL:
            return self._Cursor(self.books)
        if sql == calibre._AUTHORS_SQL:
            return self._Cursor(self.authors)
        if sql == calibre._SERIES_SQL:
            return self._Cursor(self.series)
        if sql == calibre._IDENTIFIERS_SQL:
            return self._Cursor(self.identifiers)
        raise AssertionError(sql)

    class _Cursor:
        def __init__(self, rows: list[Any]) -> None:
            self.rows = rows

        def fetchall(self) -> list[Any]:
            return list(self.rows)

        def __iter__(self) -> Any:
            return iter(self.rows)


def _ebooks(rows: _CalibreRows) -> list[LibraryEntry]:
    return calibre._read_entries(rows)  # type: ignore[arg-type]


def _ebook(
    book_id: int,
    title: str,
    author: str,
    *,
    series: str | None = None,
    identifiers: dict[str, str] | None = None,
) -> LibraryEntry:
    rows = _CalibreRows()
    rows.add(book_id, title, author, series=series, identifiers=identifiers)
    return _ebooks(rows)[0]


def _pair_ids(pairs: list[cross_format.Pair]) -> set[tuple[str, str]]:
    return {(pair.ebook.item_id, pair.audiobook.item_id) for pair in pairs}


# --- pair computation ------------------------------------------------------------------


def test_same_title_and_author_is_a_pair() -> None:
    ebook = _ebook(1, "Dungeon Crawler Carl", "Matt Dinniman")
    audiobook = _audiobook("li_1", "Dungeon Crawler Carl", "Matt Dinniman")

    pairs = cross_format.compute_pairs([ebook], [audiobook])

    assert _pair_ids(pairs) == {("1", "li_1")}
    assert pairs[0].ebook.title == "Dungeon Crawler Carl"
    assert pairs[0].audiobook.author == "Matt Dinniman"


def test_a_shared_isbn_pairs_without_a_title_match() -> None:
    ebook = _ebook(1, "DCC", "Dinniman", identifiers={"isbn": "978-0-593-82024-7"})
    audiobook = _audiobook("li_1", "Dungeon Crawler Carl", "Matt Dinniman", isbn="0593820247")

    assert _pair_ids(cross_format.compute_pairs([ebook], [audiobook])) == {("1", "li_1")}


def test_a_shared_asin_pairs_without_a_title_match() -> None:
    ebook = _ebook(1, "Book One", "Someone", identifiers={"mobi-asin": "b08bkgyqxw"})
    audiobook = _audiobook("li_1", "Something Else", "Nobody", asin="B08BKGYQXW")

    assert _pair_ids(cross_format.compute_pairs([ebook], [audiobook])) == {("1", "li_1")}


@pytest.mark.parametrize(
    ("abs_title", "abs_series", "calibre_title", "calibre_series"),
    [
        # The shelf shows reading order in the title; Calibre keeps the plain title.
        ("Alex Cross 02: Kiss the Girls", "Alex Cross #2", "Kiss the Girls", "Alex Cross"),
        # Calibre puts the series in a numbered parenthetical instead of the series field.
        ("Kiss the Girls", None, "Kiss the Girls (Alex Cross Series #2)", None),
        # Both sides carry the series, differently.
        ("DCC 2: Carl's Doomsday Scenario", "DCC #2", "Carl's Doomsday Scenario", "DCC"),
    ],
)
def test_series_labels_on_either_side_do_not_hide_a_pair(
    abs_title: str, abs_series: str | None, calibre_title: str, calibre_series: str | None
) -> None:
    ebook = _ebook(7, calibre_title, "James Patterson", series=calibre_series)
    audiobook = _audiobook("li_7", abs_title, "James Patterson", series=abs_series)

    assert _pair_ids(cross_format.compute_pairs([ebook], [audiobook])) == {("7", "li_7")}


def test_another_book_in_the_same_series_is_not_a_pair() -> None:
    ebook = _ebook(1, "Dungeon Crawler Carl", "Matt Dinniman", series="Dungeon Crawler Carl")
    audiobook = _audiobook(
        "li_2", "DCC 2: Carl's Doomsday Scenario", "Matt Dinniman", series="DCC #2"
    )

    assert cross_format.compute_pairs([ebook], [audiobook]) == []


def test_a_sequel_whose_title_extends_the_other_is_not_a_pair() -> None:
    dune = _ebook(1, "Dune", "Frank Herbert")
    messiah = _audiobook("li_2", "Dune Messiah", "Frank Herbert")

    assert cross_format.compute_pairs([dune], [messiah]) == []
    # Nor the other way round.
    assert cross_format.compute_pairs([_ebook(2, "Dune Messiah", "Frank Herbert")], []) == []
    assert (
        cross_format.compute_pairs(
            [_ebook(2, "Dune Messiah", "Frank Herbert")], [_audiobook("li_1", "Dune", "Herbert")]
        )
        == []
    )


@pytest.mark.parametrize(
    "abs_title",
    [
        "Dungeon Crawler Carl (Full Cast Edition)",
        "Dungeon Crawler Carl [Dramatized Adaptation]",
        "Dungeon Crawler Carl (GraphicAudio)",
    ],
)
def test_a_different_edition_is_not_a_pair(abs_title: str) -> None:
    ebook = _ebook(1, "Dungeon Crawler Carl", "Matt Dinniman")
    audiobook = _audiobook("li_1", abs_title, "Matt Dinniman")

    assert cross_format.compute_pairs([ebook], [audiobook]) == []


@pytest.mark.parametrize(
    "abs_title",
    ["Dungeon Crawler Carl (Unabridged)", "Dungeon Crawler Carl: Illustrated Edition"],
)
def test_a_printing_word_is_still_a_pair(abs_title: str) -> None:
    ebook = _ebook(1, "Dungeon Crawler Carl", "Matt Dinniman")
    audiobook = _audiobook("li_1", abs_title, "Matt Dinniman")

    assert _pair_ids(cross_format.compute_pairs([ebook], [audiobook])) == {("1", "li_1")}


def test_an_omnibus_is_not_a_pair() -> None:
    ebook = _ebook(1, "Dungeon Crawler Carl", "Matt Dinniman")
    omnibus = _audiobook("li_9", "Dungeon Crawler Carl: Books 1-3 Collection", "Matt Dinniman")

    assert cross_format.compute_pairs([ebook], [omnibus]) == []


def test_the_same_title_by_another_author_is_not_a_pair() -> None:
    ebook = _ebook(1, "Cross", "James Patterson")
    audiobook = _audiobook("li_1", "Cross", "Ethan Cross")

    assert cross_format.compute_pairs([ebook], [audiobook]) == []


def test_two_editions_on_one_side_both_pair_with_the_one_copy_on_the_other() -> None:
    rows = _CalibreRows()
    rows.add(1, "Dune", "Frank Herbert")
    rows.add(2, "Dune (Deluxe Edition)", "Frank Herbert")
    audiobook = _audiobook("li_1", "Dune", "Frank Herbert")

    pairs = cross_format.compute_pairs(_ebooks(rows), [audiobook])

    assert _pair_ids(pairs) == {("1", "li_1"), ("2", "li_1")}


def test_a_book_in_only_one_library_has_no_pair() -> None:
    rows = _CalibreRows()
    rows.add(1, "Dungeon Crawler Carl", "Matt Dinniman")
    rows.add(2, "The Martian", "Andy Weir")
    audiobooks = [
        _audiobook("li_1", "Dungeon Crawler Carl", "Matt Dinniman"),
        _audiobook("li_3", "Project Hail Mary", "Andy Weir"),
    ]

    assert _pair_ids(cross_format.compute_pairs(_ebooks(rows), audiobooks)) == {("1", "li_1")}


def test_pairs_report_indexes_both_sides() -> None:
    rows = _CalibreRows()
    rows.add(1, "Dune", "Frank Herbert")
    rows.add(2, "Dune (Deluxe Edition)", "Frank Herbert")
    rows.add(3, "The Martian", "Andy Weir")
    audiobooks = [_audiobook("li_1", "Dune", "Frank Herbert")]
    _register_libraries(_ebooks(rows), audiobooks)

    report = cross_format.pairs_report()

    assert (report["ebooks_indexed"], report["audiobooks_indexed"], report["pair_count"]) == (
        3,
        1,
        2,
    )
    assert report["by_calibre_id"] == {
        "1": [{"id": "li_1", "title": "Dune", "author": "Frank Herbert"}],
        "2": [{"id": "li_1", "title": "Dune", "author": "Frank Herbert"}],
    }
    assert report["by_audiobookshelf_id"] == {
        "li_1": [
            {"id": "1", "title": "Dune", "author": "Frank Herbert"},
            {"id": "2", "title": "Dune (Deluxe Edition)", "author": "Frank Herbert"},
        ]
    }
    assert report["pairs"][0] == {
        "ebook": {"id": "1", "title": "Dune", "author": "Frank Herbert"},
        "audiobook": {"id": "li_1", "title": "Dune", "author": "Frank Herbert"},
    }


# --- the Audiobookshelf tag sync ----------------------------------------------------------


class _Provider:
    def __init__(self, name: str, entries: list[LibraryEntry], *, enabled: bool = True) -> None:
        self.name = name
        self.display_name = name.title()
        self.content_types = frozenset({"ebook" if name == "calibre" else "audiobook"})
        self.entries = entries
        self.enabled = enabled

    def is_enabled(self) -> bool:
        return self.enabled

    def describe(self) -> str:
        return f"{self.display_name} (test)"

    def fingerprint(self) -> None:
        return None

    def fetch_entries(self) -> list[LibraryEntry]:
        return list(self.entries)


def _register_libraries(
    ebooks: list[LibraryEntry] | Exception,
    audiobooks: list[LibraryEntry],
    *,
    calibre_enabled: bool = True,
    abs_enabled: bool = True,
) -> None:
    class _Broken(_Provider):
        def fetch_entries(self) -> list[LibraryEntry]:
            raise ebooks  # type: ignore[misc]

    calibre_provider = (
        _Broken("calibre", [], enabled=calibre_enabled)
        if isinstance(ebooks, Exception)
        else _Provider("calibre", ebooks, enabled=calibre_enabled)
    )
    providers = [
        _Provider("audiobookshelf", audiobooks, enabled=abs_enabled),
        calibre_provider,
    ]
    cross_format.all_providers = lambda overrides=None: list(providers)  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _restore_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cross_format, "all_providers", cross_format.all_providers)


class _FakeAbs:
    """Audiobookshelf as the tag client sees it: live tags per item, and a call log."""

    def __init__(self, tags: dict[str, list[str]], *, forbidden: bool = False) -> None:
        self.live = {item_id: list(value) for item_id, value in tags.items()}
        self.forbidden = forbidden
        self.reads: list[str] = []
        self.writes: list[tuple[str, list[str]]] = []

    def tags(self, item_id: str) -> list[str]:
        self.reads.append(item_id)
        return list(self.live[item_id])

    def set_tags(self, item_id: str, tags: list[str]) -> None:
        if self.forbidden:
            raise AudiobookshelfForbiddenError("Audiobookshelf refused the update (403)")
        self.writes.append((item_id, list(tags)))
        self.live[item_id] = list(tags)


def _install_abs(monkeypatch: pytest.MonkeyPatch, fake: _FakeAbs) -> None:
    monkeypatch.setattr(cross_format, "AudiobookshelfTagClient", lambda overrides=None: fake)


def _enable(fake_app_config: FakeConfig, **values: Any) -> None:
    fake_app_config.values.update({"CROSS_FORMAT_ABS_TAGGING_ENABLED": True, **values})


_TAG = cross_format.DEFAULT_TAG


def _shelves() -> tuple[list[LibraryEntry], list[LibraryEntry]]:
    rows = _CalibreRows()
    rows.add(1, "Dungeon Crawler Carl", "Matt Dinniman")
    rows.add(2, "The Martian", "Andy Weir")
    audiobooks = [
        _audiobook("li_dcc", "Dungeon Crawler Carl", "Matt Dinniman", tags=["sci-fi"]),
        _audiobook("li_martian", "The Martian", "Andy Weir", tags=[_TAG, "favourite"]),
        _audiobook("li_phm", "Project Hail Mary", "Andy Weir", tags=["favourite", _TAG]),
        _audiobook("li_other", "Good Omens", "Terry Pratchett"),
    ]
    return _ebooks(rows), audiobooks


def test_plan_only_touches_the_marker_tag() -> None:
    ebooks, audiobooks = _shelves()
    pairs = cross_format.compute_pairs(ebooks, audiobooks)

    changes = cross_format.plan_tag_changes(audiobooks, pairs, _TAG)

    assert [(c.item_id, c.action) for c in changes] == [("li_dcc", "add"), ("li_phm", "remove")]


def test_apply_tag_keeps_other_tags_and_their_order() -> None:
    assert cross_format.apply_tag(["b", "a"], _TAG, "add") == ["b", "a", _TAG]
    assert cross_format.apply_tag(["b", _TAG, "a"], _TAG, "add") == ["b", _TAG, "a"]
    assert cross_format.apply_tag(["b", _TAG, "a"], _TAG, "remove") == ["b", "a"]
    assert cross_format.apply_tag(["b", "a"], _TAG, "remove") == ["b", "a"]


def test_dry_run_reports_without_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks)
    fake = _FakeAbs({})
    _install_abs(monkeypatch, fake)

    summary = cross_format.sync_audiobookshelf_tags(dry_run=True)

    assert summary["dry_run"] is True
    assert summary["pairs"] == 2
    assert summary["added"] == [{"id": "li_dcc", "title": "Dungeon Crawler Carl"}]
    assert summary["removed"] == [{"id": "li_phm", "title": "Project Hail Mary"}]
    assert (summary["errors"], summary["forbidden"]) == (0, False)
    assert fake.reads == [] and fake.writes == []


def test_dry_run_works_with_tagging_off(fake_app_config: FakeConfig) -> None:
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks)
    assert fake_app_config.get("CROSS_FORMAT_ABS_TAGGING_ENABLED") is None

    assert cross_format.sync_audiobookshelf_tags(dry_run=True)["pairs"] == 2


def test_a_real_run_refuses_while_tagging_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks)
    fake = _FakeAbs({})
    _install_abs(monkeypatch, fake)

    with pytest.raises(cross_format.CrossFormatDisabledError):
        cross_format.sync_audiobookshelf_tags(dry_run=False)

    assert fake.writes == []


def test_a_real_run_writes_the_full_tag_list_from_a_fresh_read(
    monkeypatch: pytest.MonkeyPatch, fake_app_config: FakeConfig
) -> None:
    _enable(fake_app_config)
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks)
    # Someone tagged the DCC audiobook "new" in Audiobookshelf after the index was built.
    fake = _FakeAbs({"li_dcc": ["sci-fi", "new"], "li_phm": ["favourite", _TAG]})
    _install_abs(monkeypatch, fake)
    log = capture_log(monkeypatch, cross_format.logger, "info")

    summary = cross_format.sync_audiobookshelf_tags(dry_run=False)

    assert fake.writes == [("li_dcc", ["sci-fi", "new", _TAG]), ("li_phm", ["favourite"])]
    assert summary["added"] == [{"id": "li_dcc", "title": "Dungeon Crawler Carl"}]
    assert summary["removed"] == [{"id": "li_phm", "title": "Project Hail Mary"}]
    assert summary["already_current"] == 0
    assert any("added to 'Dungeon Crawler Carl' (li_dcc)" in line for line in log)
    assert any("removed from 'Project Hail Mary' (li_phm)" in line for line in log)
    assert log[-1].startswith("cross-format: 2 pair(s); 'ebook-in-library' added to 1, removed")


def test_a_second_run_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, fake_app_config: FakeConfig
) -> None:
    _enable(fake_app_config)
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks)
    fake = _FakeAbs({"li_dcc": ["sci-fi"], "li_phm": ["favourite", _TAG]})
    _install_abs(monkeypatch, fake)
    cross_format.sync_audiobookshelf_tags(dry_run=False)
    first = list(fake.writes)

    # The index is re-read from the (now updated) shelves, as the next cycle would.
    _register_libraries(
        ebooks,
        [
            _audiobook("li_dcc", "Dungeon Crawler Carl", "Matt Dinniman", tags=["sci-fi", _TAG]),
            _audiobook("li_martian", "The Martian", "Andy Weir", tags=[_TAG, "favourite"]),
            _audiobook("li_phm", "Project Hail Mary", "Andy Weir", tags=["favourite"]),
            _audiobook("li_other", "Good Omens", "Terry Pratchett"),
        ],
    )
    library_index._cache.clear()
    summary = cross_format.sync_audiobookshelf_tags(dry_run=False)

    assert fake.writes == first
    assert (summary["added"], summary["removed"], summary["already_current"]) == ([], [], 0)


def test_a_stale_index_does_not_rewrite_an_item_already_current(
    monkeypatch: pytest.MonkeyPatch, fake_app_config: FakeConfig
) -> None:
    _enable(fake_app_config)
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks)
    # Both items were fixed by hand between the index and this run.
    fake = _FakeAbs({"li_dcc": ["sci-fi", _TAG], "li_phm": ["favourite"]})
    _install_abs(monkeypatch, fake)

    summary = cross_format.sync_audiobookshelf_tags(dry_run=False)

    assert fake.reads == ["li_dcc", "li_phm"]
    assert fake.writes == []
    assert summary["already_current"] == 2
    assert (summary["added"], summary["removed"]) == ([], [])


def test_a_403_stops_the_run_and_is_reported(
    monkeypatch: pytest.MonkeyPatch, fake_app_config: FakeConfig
) -> None:
    _enable(fake_app_config)
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks)
    fake = _FakeAbs({"li_dcc": ["sci-fi"], "li_phm": ["favourite", _TAG]}, forbidden=True)
    _install_abs(monkeypatch, fake)
    warnings = capture_log(monkeypatch, cross_format.logger, "warning")

    summary = cross_format.sync_audiobookshelf_tags(dry_run=False)

    assert summary["forbidden"] is True
    assert summary["errors"] == 1
    assert "403" in summary["error"]
    assert (summary["added"], summary["removed"]) == ([], [])
    assert fake.reads == ["li_dcc"]  # the second item is never attempted
    assert warnings == ["cross-format: Audiobookshelf refused the update (403)"]
    assert "Stopped:" in cross_format.describe_summary(summary)


def test_one_failing_item_does_not_stop_the_rest(
    monkeypatch: pytest.MonkeyPatch, fake_app_config: FakeConfig
) -> None:
    _enable(fake_app_config)
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks)
    fake = _FakeAbs({"li_phm": ["favourite", _TAG]})  # li_dcc is missing: the read raises
    _install_abs(monkeypatch, fake)
    warnings = capture_log(monkeypatch, cross_format.logger, "warning")

    summary = cross_format.sync_audiobookshelf_tags(dry_run=False)

    assert fake.writes == [("li_phm", ["favourite"])]
    assert summary["errors"] == 1
    assert summary["forbidden"] is False
    assert warnings[0].startswith("cross-format: could not update 'Dungeon Crawler Carl' (li_dcc)")


def test_should_stop_ends_the_run_between_items(
    monkeypatch: pytest.MonkeyPatch, fake_app_config: FakeConfig
) -> None:
    _enable(fake_app_config)
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks)
    fake = _FakeAbs({"li_dcc": ["sci-fi"], "li_phm": ["favourite", _TAG]})
    _install_abs(monkeypatch, fake)
    calls = iter([False, True])

    summary = cross_format.sync_audiobookshelf_tags(dry_run=False, should_stop=lambda: next(calls))

    assert fake.writes == [("li_dcc", ["sci-fi", _TAG])]
    assert summary["errors"] == 1


def test_the_tag_name_is_configurable(
    monkeypatch: pytest.MonkeyPatch, fake_app_config: FakeConfig
) -> None:
    _enable(fake_app_config, CROSS_FORMAT_ABS_TAG="  has-ebook ")
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks)
    fake = _FakeAbs(
        {"li_dcc": [], "li_martian": [_TAG, "favourite"], "li_phm": ["favourite", _TAG]}
    )
    _install_abs(monkeypatch, fake)

    summary = cross_format.sync_audiobookshelf_tags(dry_run=False)

    assert summary["tag"] == "has-ebook"
    # The default tag someone else put on these items is not ours to remove.
    assert fake.writes == [
        ("li_dcc", ["has-ebook"]),
        ("li_martian", [_TAG, "favourite", "has-ebook"]),
    ]


def test_a_blank_tag_setting_falls_back_to_the_default(fake_app_config: FakeConfig) -> None:
    fake_app_config.values["CROSS_FORMAT_ABS_TAG"] = "   "

    assert cross_format.tag_name() == _TAG
    assert cross_format.tag_name({"CROSS_FORMAT_ABS_TAG": "form-value"}) == "form-value"


def test_both_library_checks_must_be_enabled() -> None:
    ebooks, audiobooks = _shelves()
    _register_libraries(ebooks, audiobooks, calibre_enabled=False)

    with pytest.raises(cross_format.CrossFormatNotConfiguredError):
        cross_format.pairs_report()

    _register_libraries(ebooks, audiobooks, abs_enabled=False)
    with pytest.raises(cross_format.CrossFormatNotConfiguredError):
        cross_format.sync_audiobookshelf_tags(dry_run=True)


def test_an_unreadable_library_raises_rather_than_stripping_every_tag(
    monkeypatch: pytest.MonkeyPatch, fake_app_config: FakeConfig
) -> None:
    _enable(fake_app_config)
    _ebooks_unused, audiobooks = _shelves()
    _register_libraries(OSError("metadata.db: no such file"), audiobooks)
    fake = _FakeAbs({"li_martian": [_TAG, "favourite"], "li_phm": ["favourite", _TAG]})
    _install_abs(monkeypatch, fake)
    monkeypatch.setattr(library_index.logger, "warning", lambda *a, **k: None)

    with pytest.raises(library_index.LibraryUnavailableError):
        cross_format.sync_audiobookshelf_tags(dry_run=False)

    assert fake.writes == []
