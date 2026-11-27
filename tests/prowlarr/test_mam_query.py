"""The MyAnonamouse title query and its no-results fallback."""

import pytest

import shelfmark.release_sources.prowlarr.source as prowlarr_source
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.prowlarr.api import ProwlarrSearchError
from shelfmark.release_sources.prowlarr.mam_query import core_title, mam_fallback_queries
from shelfmark.release_sources.prowlarr.source import ProwlarrSource

MAM = 6
OTHER = 7


class TestCoreTitle:
    @pytest.mark.parametrize(
        ("title", "series", "expected"),
        [
            (
                "Magic: The Gathering - Ravnica - War of the Spark: 1",
                "Magic: The Gathering",
                "War of the Spark",
            ),
            ("Magic: The Gathering - Ravnica - War of the Spark: 1", None, "War of the Spark"),
            ("Magic: The Gathering: Time Streams", "Magic: The Gathering", "Time Streams"),
            ("War of the Spark: Ravnica", None, "War of the Spark: Ravnica"),
            ("The Way of Kings, Book 1", None, "The Way of Kings"),
            ("Leviathan Wakes (Book 1)", None, "Leviathan Wakes"),
            ("Dune [2]", None, "Dune"),
            ("Mistborn #1", None, "Mistborn"),
            ("The Fellowship of the Ring Vol. 1", None, "The Fellowship of the Ring"),
            ("Fahrenheit 451", None, "Fahrenheit 451"),
            ("Catch-22", None, "Catch-22"),
            ("1984", None, "1984"),
            ("Spider-Man - Forever Young", None, "Forever Young"),
        ],
    )
    def test_core_title(self, title, series, expected):
        assert core_title(title, series) == expected

    def test_fallbacks_ignore_punctuation_when_skipping_sent_queries(self):
        assert mam_fallback_queries(
            "Time Streams: A Novel",
            ["9780786966417", "0786966416"],
            already_sent=["Time-Streams."],
        ) == ["9780786966417"]


class _Client:
    """One MAM indexer and one other indexer; answers by (indexer, query)."""

    indexer_timeout = 90

    def __init__(self, hits=None, fail_queries=()):
        self.hits = set(hits or ())
        self.fail_queries = set(fail_queries)
        self.calls: list[tuple[int, str]] = []
        self.fail_other = False

    def get_enabled_indexers_detailed(self, *, raise_on_error=False):
        del raise_on_error
        caps = {"categories": [{"id": 7000, "subCategories": []}]}
        return [
            {"id": MAM, "enable": True, "implementation": "MyAnonamouse", "capabilities": caps},
            {"id": OTHER, "enable": True, "implementation": "Torznab", "capabilities": caps},
        ]

    def get_enriched_indexer_ids(self, restrict_to=None, indexers=None):
        del indexers
        return [i for i in [MAM] if restrict_to is None or i in restrict_to]

    def get_disabled_indexers(self, **_):
        return {}

    def torznab_search(self, *, indexer_id, query, categories=None, search_type="book", **_):
        del categories, search_type
        self.calls.append((indexer_id, query))
        if query in self.fail_queries or (indexer_id == OTHER and self.fail_other):
            raise ProwlarrSearchError("boom")
        if (indexer_id, query) not in self.hits:
            return []
        return [
            {
                "guid": f"{indexer_id}-{query}",
                "title": query,
                "indexerId": indexer_id,
                "indexer": "MyAnonamouse" if indexer_id == MAM else "Other",
                "protocol": "torrent",
                "size": 1048576,
                "seeders": 1,
                "categories": [{"id": 7020}],
                "infoUrl": f"https://www.myanonamouse.net/t/{len(self.calls)}",
            }
        ]


@pytest.fixture
def search(monkeypatch):
    values = {"PROWLARR_INDEXERS": "", "PROWLARR_AUTO_EXPAND": False}

    def run(client, book, *, mam_id="", **plan_kwargs):
        values["PROWLARR_MAM_ID"] = mam_id
        monkeypatch.setattr(
            prowlarr_source.config, "get", lambda key, default=None: values.get(key, default)
        )
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: client)
        plan = build_release_search_plan(book, languages=["en"], **plan_kwargs)
        return source.search(book, plan)

    return run


def mam_calls(client):
    return [q for i, q in client.calls if i == MAM]


# Hardcover's metadata for this edition, search_title included.
WAR_OF_THE_SPARK = BookMetadata(
    provider="hardcover",
    provider_id="3036857",
    title="Magic: The Gathering - Ravnica - War of the Spark: 1",
    search_title="Magic",
    authors=["Greg Weisman"],
    isbn_13="9781789092714",
    series_name="Magic: The Gathering",
)

TIME_STREAMS = BookMetadata(
    provider="hardcover",
    provider_id="1128566",
    title="Magic: The Gathering: Time Streams",
    authors=["J. Robert King"],
    isbn_13="9780786966417",
    series_name="Magic: The Gathering",
)


class TestMamQuery:
    def test_mam_drops_series_words_other_indexers_do_not(self, search):
        client = _Client(hits={(MAM, "Time Streams")})
        assert len(search(client, TIME_STREAMS)) == 1
        assert client.calls == [
            (MAM, "Time Streams"),
            (OTHER, "Magic: The Gathering: Time Streams"),
        ]

    def test_fallback_title_comes_from_the_full_title(self, search):
        client = _Client(hits={(MAM, "War of the Spark")})
        assert len(search(client, WAR_OF_THE_SPARK)) == 1
        assert mam_calls(client) == ["Magic", "War of the Spark"]
        assert [q for i, q in client.calls if i == OTHER] == ["Magic"]

    def test_isbn_last(self, search):
        client = _Client(hits={(MAM, "9781789092714")})
        assert len(search(client, WAR_OF_THE_SPARK)) == 1
        assert mam_calls(client) == ["Magic", "War of the Spark", "9781789092714"]

    def test_nothing_anywhere_costs_mam_three_requests(self, search):
        client = _Client()
        assert search(client, WAR_OF_THE_SPARK) == []
        assert mam_calls(client) == ["Magic", "War of the Spark", "9781789092714"]

    def test_other_indexer_hit_still_retries_mam(self, search):
        client = _Client(hits={(OTHER, "Magic"), (MAM, "War of the Spark")})
        assert len(search(client, WAR_OF_THE_SPARK)) == 2
        assert mam_calls(client) == ["Magic", "War of the Spark"]

    def test_other_indexer_failure_does_not_block_mam_retry(self, search):
        client = _Client(hits={(MAM, "War of the Spark")})
        client.fail_other = True
        assert len(search(client, WAR_OF_THE_SPARK)) == 1
        assert mam_calls(client) == ["Magic", "War of the Spark"]

    def test_no_fallback_after_a_failed_request(self, search):
        client = _Client(fail_queries={"Magic"})
        with pytest.raises(SourceUnavailableError):
            search(client, WAR_OF_THE_SPARK)
        assert mam_calls(client) == ["Magic"]

    def test_failed_fallback_stops_the_chain(self, search):
        client = _Client(fail_queries={"War of the Spark"})
        with pytest.raises(SourceUnavailableError):
            search(client, WAR_OF_THE_SPARK)
        assert mam_calls(client) == ["Magic", "War of the Spark"]

    def test_manual_query_is_sent_verbatim_without_fallback(self, search):
        client = _Client()
        search(client, TIME_STREAMS, manual_query="Magic: The Gathering: Time Streams")
        assert client.calls == [
            (MAM, "Magic: The Gathering: Time Streams"),
            (OTHER, "Magic: The Gathering: Time Streams"),
        ]

    def test_enrichment_reruns_only_the_query_that_found_the_torrent(self, search, monkeypatch):
        rerun: list[list[str]] = []
        monkeypatch.setattr(
            prowlarr_source,
            "_enrich_mam_releases",
            lambda _releases, _mam_id, queries, _options, _deadline: rerun.append(queries),
        )
        client = _Client(hits={(MAM, "War of the Spark")})
        search(client, WAR_OF_THE_SPARK, mam_id="session")
        assert rerun == [["War of the Spark"]]
