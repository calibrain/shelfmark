"""Tests for the series-gap report: grouping, Hardcover lookup, statuses and requests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from shelfmark.core import library_index, series_gap
from shelfmark.core.cache import get_metadata_cache
from shelfmark.core.library_providers import LibraryEntry
from shelfmark.core.text_match import tokens
from shelfmark.core.user_db import UserDB
from shelfmark.metadata_providers import BookMetadata

pytestmark = pytest.mark.usefixtures("fake_app_config")

DCC = "Dungeon Crawler Carl"
AUTHOR = "Matt Dinniman"


def _entry(
    title: str,
    author: str = AUTHOR,
    series: str | None = DCC,
    index: float | None = None,
    external_ids: frozenset[tuple[str, str]] = frozenset(),
) -> LibraryEntry:
    title_tok = set(tokens(title))
    context = set(tokens(author)) | set(tokens(series))
    return LibraryEntry(
        frozenset(title_tok | context),
        frozenset(),
        frozenset(),
        external_ids,
        frozenset(title_tok),
        frozenset(context),
        series_name=series,
        series_index=index,
        author=author,
    )


def _volume(
    provider_id: int,
    title: str,
    position: object,
    *,
    author: str = AUTHOR,
    series: str = DCC,
    year: int | None = 2020,
) -> BookMetadata:
    return BookMetadata(
        provider="hardcover",
        provider_id=str(provider_id),
        title=title,
        authors=[author],
        search_title=title,
        search_author=author,
        series_id="77",
        series_name=series,
        series_position=position,  # type: ignore[arg-type]  - raw API value, as the provider sets it
        publish_year=year,
    )


# Hardcover's listing of the series: a novella at 2.5 between the numbered volumes.
VOLUMES = [
    _volume(1, "Dungeon Crawler Carl", 1),
    _volume(2, "Carl's Doomsday Scenario", 2),
    _volume(25, "Backstage at the Pinnacle", "2.5"),
    _volume(3, "The Dungeon Anarchist's Cookbook", 3),
]


class _Hardcover:
    """Stub of the two provider methods the report uses, counting what it is asked."""

    def __init__(
        self,
        hits: dict[str, list[dict[str, Any]]] | None = None,
        volumes: dict[int, list[BookMetadata]] | None = None,
    ) -> None:
        self.hits = hits if hits is not None else {DCC: [{"id": 77, "name": DCC}]}
        self.volumes = volumes if volumes is not None else {77: VOLUMES}
        self.searches: list[str] = []
        self.fetches: list[int] = []

    def _search_field_hits(self, *, query: str, query_type: str, **_kwargs: Any) -> list[dict]:
        assert query_type == "Series"
        self.searches.append(query)
        return [{"document": document} for document in self.hits.get(query, [])]

    def _fetch_series_books_by_id(
        self, series_id: int, page: int, limit: int, **_kwargs: Any
    ) -> SimpleNamespace:
        assert (page, limit) == (1, series_gap._MAX_VOLUMES)
        self.fetches.append(series_id)
        return SimpleNamespace(books=list(self.volumes.get(series_id, [])), has_more=False)


class _Library:
    def __init__(self, name: str, content_type: str, entries: list[LibraryEntry]) -> None:
        self.name = name
        self.display_name = name.title()
        self.content_types = frozenset({content_type})
        self.entries = entries

    def is_enabled(self) -> bool:
        return True

    def describe(self) -> str:
        return self.display_name

    def fingerprint(self) -> None:
        return None

    def fetch_entries(self) -> list[LibraryEntry]:
        return list(self.entries)


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_index, "_cache", {})
    get_metadata_cache().clear()


@pytest.fixture
def libraries(monkeypatch: pytest.MonkeyPatch) -> list[_Library]:
    registered: list[_Library] = []
    monkeypatch.setattr(library_index, "all_providers", lambda overrides=None: list(registered))
    return registered


@pytest.fixture
def user_db(tmp_path) -> UserDB:
    db = UserDB(str(tmp_path / "users.db"))
    db.initialize()
    db.create_user(username="admin", role="admin")
    return db


def _request(user_db: UserDB, title: str, provider_id: str, content_type: str, **extra: Any) -> int:
    row = user_db.create_request(
        user_id=1,
        content_type=content_type,
        request_level="book",
        policy_mode="request_book",
        book_data={
            "title": title,
            "author": AUTHOR,
            "provider": extra.pop("provider", "hardcover"),
            "provider_id": provider_id,
            "content_type": content_type,
        },
    )
    return int(row["id"])


def _statuses(report: dict[str, Any], content_type: str) -> dict[float, str | None]:
    (row,) = report["series"]
    return {volume["position"]: volume["statuses"][content_type] for volume in row["volumes"]}


class TestLibrarySeries:
    def test_groups_the_formats_by_name_and_keeps_each_ones_positions(self) -> None:
        entries = {
            "ebook": [_entry("Dungeon Crawler Carl", index=1), _entry("Carl's", index=2)],
            "audiobook": [_entry("Dungeon Crawler Carl", series="The DCC Series", index=1)],
        }
        # "The DCC Series" is not the same words, so it is its own series; the ebook
        # series' packaging word is dropped.
        entries["audiobook"].append(_entry("Carl's", series="Dungeon Crawler Carl Series"))

        series = series_gap.library_series(entries)

        # Sorted by the name the library shows, not the grouping key.
        assert [(s.key, s.name, s.author) for s in series] == [
            ("dungeon crawler carl", DCC, AUTHOR),
            ("dcc", "The DCC Series", AUTHOR),
        ]
        assert series[0].positions == {"ebook": {1.0, 2.0}, "audiobook": set()}
        assert series[1].positions == {"audiobook": {1.0}}

    def test_an_entry_without_a_series_is_not_a_series(self) -> None:
        assert series_gap.library_series({"ebook": [_entry("Lone Book", series=None)]}) == []

    @pytest.mark.parametrize(
        ("name", "key"),
        [
            ("The Expanse", "expanse"),
            ("Dune Series", "dune"),
            ("A Song of Ice and Fire", "song of ice and fire"),
        ],
    )
    def test_series_key_drops_packaging_words(self, name: str, key: str) -> None:
        assert series_gap.series_key(name) == key


class TestBuildReport:
    def test_marks_each_volume_owned_requested_or_missing_per_format(
        self, libraries, user_db, monkeypatch
    ) -> None:
        libraries.append(
            _Library(
                "calibre",
                "ebook",
                [
                    _entry("Dungeon Crawler Carl", index=1),
                    _entry("Carl's Doomsday Scenario", index=2),
                ],
            )
        )
        libraries.append(_Library("abs", "audiobook", [_entry("Dungeon Crawler Carl", index=1)]))
        # Any status counts as requested: this one was rejected.
        request_id = _request(user_db, "The Dungeon Anarchist's Cookbook", "3", "ebook")
        user_db.update_request(request_id, status="rejected")
        hardcover = _Hardcover()

        report = series_gap.build_report(user_db, hardcover)

        assert report["formats"] == ["ebook", "audiobook"]
        assert report["pending"] == 0
        (row,) = report["series"]
        assert (row["status"], row["name"], row["author"]) == ("ok", DCC, AUTHOR)
        assert row["hardcover"] == {"id": 77, "name": DCC, "author": AUTHOR}
        assert _statuses(report, "ebook") == {
            1.0: "owned",
            2.0: "owned",
            2.5: "missing",
            3.0: "requested",
        }
        assert _statuses(report, "audiobook") == {
            1.0: "owned",
            2.0: "missing",
            2.5: "missing",
            3.0: "missing",
        }
        assert row["formats"] == {
            "ebook": {"collected": True, "owned": 2, "requested": 1, "missing": 1},
            "audiobook": {"collected": True, "owned": 1, "requested": 0, "missing": 3},
        }
        assert [v["title"] for v in row["volumes"]][2] == "Backstage at the Pinnacle"
        assert row["volumes"][0]["provider_id"] == "1"

    def test_a_volume_held_at_its_position_under_another_title_is_owned(
        self, libraries, user_db
    ) -> None:
        # The library calls volume 3 "Book Three"; no title match, but the series is
        # filed at position 3, which is the same book.
        libraries.append(_Library("calibre", "ebook", [_entry("Book Three", index=3)]))

        report = series_gap.build_report(user_db, _Hardcover())

        assert _statuses(report, "ebook")[3.0] == "owned"
        assert _statuses(report, "ebook")[1.0] == "missing"

    def test_an_omnibus_holding_the_volume_is_a_collection(self, libraries, user_db) -> None:
        libraries.append(
            _Library(
                "calibre",
                "ebook",
                # Filed in the series without a position, so the matcher decides.
                [_entry("Dungeon Crawler Carl Omnibus Books 1-3")],
            )
        )

        report = series_gap.build_report(user_db, _Hardcover())

        assert _statuses(report, "ebook")[1.0] == "collection"
        # Counted with owned: the reader has it, so it is not a gap.
        assert report["series"][0]["formats"]["ebook"]["owned"] == 1

    def test_a_request_from_another_provider_for_the_same_book_counts(
        self, libraries, user_db
    ) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Dungeon Crawler Carl", index=1)]))
        _request(user_db, "Carl's Doomsday Scenario", "OL123W", "ebook", provider="openlibrary")

        report = series_gap.build_report(user_db, _Hardcover())

        assert _statuses(report, "ebook")[2.0] == "requested"

    def test_a_format_the_library_does_not_collect_has_no_statuses(
        self, libraries, user_db
    ) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Dungeon Crawler Carl", index=1)]))
        libraries.append(_Library("abs", "audiobook", [_entry("Other Book", series="Other")]))

        report = series_gap.build_report(
            user_db, _Hardcover(hits={DCC: [{"id": 77, "name": DCC}], "Other": []})
        )

        dcc = next(row for row in report["series"] if row["name"] == DCC)
        assert dcc["formats"]["audiobook"] == {
            "collected": False,
            "owned": 0,
            "requested": 0,
            "missing": 0,
        }
        assert {v["statuses"]["audiobook"] for v in dcc["volumes"]} == {None}
        other = next(row for row in report["series"] if row["name"] == "Other")
        assert (other["status"], other["message"]) == ("unresolved", "Not found on Hardcover")
        assert other["volumes"] == []

    def test_hardcover_is_asked_once_a_day_not_once_a_build(self, libraries, user_db) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Dungeon Crawler Carl", index=1)]))
        hardcover = _Hardcover()

        series_gap.build_report(user_db, hardcover)
        series_gap.build_report(user_db, hardcover)

        assert (hardcover.searches, hardcover.fetches) == ([DCC], [77])

        series_gap.clear_cache()
        series_gap.build_report(user_db, hardcover)

        assert (hardcover.searches, hardcover.fetches) == ([DCC, DCC], [77, 77])

    def test_series_past_the_time_budget_come_back_pending(self, libraries, user_db) -> None:
        libraries.append(
            _Library(
                "calibre",
                "ebook",
                [_entry("Dungeon Crawler Carl", index=1), _entry("Other Book", series="Other")],
            )
        )
        hardcover = _Hardcover(
            hits={DCC: [{"id": 77, "name": DCC}], "Other": [{"id": 9, "name": "Other"}]}
        )
        hardcover.volumes[9] = [_volume(90, "Other Book", 1, series="Other")]
        ticks = iter([0.0, 0.0, 5.0])  # start, first series (in budget), second (over)

        report = series_gap.build_report(
            user_db, hardcover, budget_seconds=1.0, clock=lambda: next(ticks)
        )

        assert report["pending"] == 1
        assert [(row["name"], row["status"]) for row in report["series"]] == [
            (DCC, "ok"),
            ("Other", "pending"),
        ]
        assert hardcover.searches == [DCC]

        # The next build picks up where this one stopped; the first series is cached.
        report = series_gap.build_report(user_db, hardcover)

        assert report["pending"] == 0
        assert hardcover.searches == [DCC, "Other"]

    def test_a_series_credited_to_another_author_is_not_taken(self, libraries, user_db) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Legacy", series="Legacy")]))
        hits = {
            "Legacy": [
                {"id": 1, "name": "Legacy", "author_name": "Someone Else"},
                {"id": 2, "name": "Legacy", "author": {"name": "Matt Dinniman"}},
            ]
        }
        hardcover = _Hardcover(hits=hits, volumes={2: [_volume(5, "Legacy", 1, series="Legacy")]})

        report = series_gap.build_report(user_db, hardcover)

        assert report["series"][0]["hardcover"]["id"] == 2
        assert hardcover.fetches == [2]

    def test_a_listing_by_another_author_is_rejected_and_remembered(
        self, libraries, user_db
    ) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Legacy", series="Legacy")]))
        hardcover = _Hardcover(
            hits={"Legacy": [{"id": 1, "name": "Legacy"}]},
            volumes={1: [_volume(5, "Legacy", 1, author="Someone Else", series="Legacy")]},
        )

        report = series_gap.build_report(user_db, hardcover)
        (row,) = report["series"]
        assert row["status"] == "unresolved"
        assert row["message"] == "Hardcover's 'Legacy' is by Someone Else, not Matt Dinniman"

        series_gap.build_report(user_db, hardcover)
        assert hardcover.searches == ["Legacy"]  # the refusal is cached too

    def test_a_series_name_holding_the_librarys_words_is_taken_when_nothing_exact(
        self, libraries, user_db
    ) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Dungeon Crawler Carl", index=1)]))
        hardcover = _Hardcover(hits={DCC: [{"id": 77, "name": "Dungeon Crawler Carl (DCC)"}]})

        report = series_gap.build_report(user_db, hardcover)

        assert report["series"][0]["hardcover"]["name"] == "Dungeon Crawler Carl (DCC)"

    def test_an_empty_hardcover_answer_is_reported_and_not_cached(self, libraries, user_db) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Dungeon Crawler Carl", index=1)]))
        hardcover = _Hardcover(volumes={})

        report = series_gap.build_report(user_db, hardcover)
        (row,) = report["series"]
        assert row["status"] == "unresolved"
        assert "no released volumes" in row["message"]

        series_gap.build_report(user_db, hardcover)
        assert hardcover.fetches == [77, 77]

    def test_a_lookup_that_raises_fails_only_that_series(self, libraries, user_db) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Dungeon Crawler Carl", index=1)]))
        hardcover = _Hardcover()

        def _boom(**_kwargs):
            raise RuntimeError("Hardcover API request timed out")

        hardcover._search_field_hits = _boom  # type: ignore[method-assign]

        report = series_gap.build_report(user_db, hardcover)

        (row,) = report["series"]
        assert row["status"] == "unresolved"
        assert row["message"] == "Hardcover lookup failed: Hardcover API request timed out"

    def test_no_enabled_library_means_an_empty_report(self, libraries, user_db) -> None:
        report = series_gap.build_report(user_db, _Hardcover())

        assert (report["formats"], report["series"], report["pending"]) == ([], [], 0)

    def test_an_unreadable_library_stops_the_build(self, libraries, user_db) -> None:
        broken = _Library("calibre", "ebook", [])
        broken.fetch_entries = lambda: (_ for _ in ()).throw(OSError("no such file"))  # type: ignore[method-assign]
        libraries.append(broken)

        with pytest.raises(library_index.LibraryUnavailableError):
            series_gap.build_report(user_db, _Hardcover())


class TestRequestVolumes:
    def _items(self, *provider_ids: str, content_type: str = "ebook") -> list[dict[str, Any]]:
        return [
            {"series_id": 77, "provider_id": pid, "content_type": content_type}
            for pid in provider_ids
        ]

    def test_creates_a_hardcover_request_per_chosen_volume(self, libraries, user_db) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Dungeon Crawler Carl", index=1)]))

        result = series_gap.request_volumes(user_db, _Hardcover(), self._items("25", "3"))

        assert (result["created"], result["skipped"], result["errors"]) == (2, 0, 0)
        assert [(item["result"], item["title"]) for item in result["items"]] == [
            ("created", "Backstage at the Pinnacle"),
            ("created", "The Dungeon Anarchist's Cookbook"),
        ]
        rows = user_db.list_requests(status="pending")
        assert len(rows) == 2
        row = next(r for r in rows if r["book_data"]["provider_id"] == "25")
        assert row["user_id"] == 1  # the admin, as synced requests are
        assert row["content_type"] == "ebook"
        assert row["request_level"] == "book"
        assert row["book_data"]["provider"] == "hardcover"
        assert row["book_data"]["title"] == "Backstage at the Pinnacle"
        assert row["book_data"]["author"] == AUTHOR
        assert row["book_data"]["year"] == 2020
        assert row["note"] == "Series gap: Dungeon Crawler Carl #2.5"
        assert result["items"][0]["request_id"] == row["id"]

    def test_owned_and_requested_volumes_are_skipped(self, libraries, user_db) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Dungeon Crawler Carl", index=1)]))
        _request(user_db, "Carl's Doomsday Scenario", "2", "ebook")

        result = series_gap.request_volumes(user_db, _Hardcover(), self._items("1", "2", "3"))

        assert (result["created"], result["skipped"], result["errors"]) == (1, 2, 0)
        assert [item["result"] for item in result["items"]] == ["owned", "requested", "created"]

    def test_the_same_volume_chosen_twice_is_requested_once(self, libraries, user_db) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Dungeon Crawler Carl", index=1)]))

        result = series_gap.request_volumes(user_db, _Hardcover(), self._items("3", "3"))

        assert [item["result"] for item in result["items"]] == ["created", "requested"]
        assert len(user_db.list_requests()) == 1

    def test_each_format_is_its_own_request(self, libraries, user_db) -> None:
        libraries.append(_Library("calibre", "ebook", [_entry("Dungeon Crawler Carl", index=1)]))
        libraries.append(_Library("abs", "audiobook", [_entry("Dungeon Crawler Carl", index=1)]))
        items = self._items("3") + self._items("3", content_type="audiobook")

        result = series_gap.request_volumes(user_db, _Hardcover(), items)

        assert result["created"] == 2
        assert sorted(r["content_type"] for r in user_db.list_requests()) == ["audiobook", "ebook"]

    def test_a_format_without_a_library_can_still_be_requested(self, libraries, user_db) -> None:
        result = series_gap.request_volumes(
            user_db, _Hardcover(), self._items("3", content_type="audiobook")
        )

        assert result["created"] == 1

    def test_a_volume_hardcover_does_not_list_is_an_error(self, libraries, user_db) -> None:
        result = series_gap.request_volumes(user_db, _Hardcover(), self._items("999"))

        assert (result["created"], result["errors"]) == (0, 1)
        assert result["items"][0]["result"] == "unknown_volume"

    @pytest.mark.parametrize(
        "items",
        [
            None,
            [],
            ["x"],
            [{"series_id": 0, "provider_id": "3", "content_type": "ebook"}],
            [{"series_id": 77, "provider_id": "", "content_type": "ebook"}],
            [{"series_id": 77, "provider_id": "3", "content_type": "paperback"}],
        ],
    )
    def test_a_malformed_payload_is_refused_before_anything_is_requested(
        self, libraries, user_db, items
    ) -> None:
        hardcover = _Hardcover()

        with pytest.raises(series_gap.SeriesGapError) as info:
            series_gap.request_volumes(user_db, hardcover, items)

        assert info.value.status_code == 400
        assert hardcover.fetches == []
        assert user_db.list_requests() == []

    def test_too_many_items_are_refused(self, libraries, user_db) -> None:
        items = self._items(*(str(n) for n in range(series_gap.MAX_REQUEST_ITEMS + 1)))

        with pytest.raises(series_gap.SeriesGapError, match="At most"):
            series_gap.request_volumes(user_db, _Hardcover(), items)

    def test_without_an_admin_there_is_no_one_to_own_the_requests(
        self, libraries, tmp_path
    ) -> None:
        db = UserDB(str(tmp_path / "noadmin.db"))
        db.initialize()

        with pytest.raises(series_gap.SeriesGapError) as info:
            series_gap.request_volumes(db, _Hardcover(), self._items("3"))

        assert info.value.status_code == 503
