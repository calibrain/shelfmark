"""Tests for MyAnonamouse enrichment of Prowlarr results (narrator, series, bitrate)."""

import json
import time

import pytest
import requests

import shelfmark.release_sources.prowlarr.mam as mam
import shelfmark.release_sources.prowlarr.source as prowlarr_source
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import (
    ColumnSchema,
    ReleaseColumnConfig,
    apply_column_visibility,
    serialize_column_config,
)
from shelfmark.release_sources.prowlarr.mam import (
    MamAuthError,
    MamClient,
    MamError,
    MamSearchOptions,
    lookup_torrent_details,
    mam_search_options,
    mam_torrent_id,
    parse_torrent_details,
    prowlarr_search_text,
)
from shelfmark.release_sources.prowlarr.settings import _test_mam_connection
from shelfmark.release_sources.prowlarr.source import (
    ProwlarrSource,
    _enrich_mam_releases,
    _prowlarr_result_to_release,
)


@pytest.fixture(autouse=True)
def _reset_mam_state(monkeypatch):
    mam._cache.clear()
    monkeypatch.setattr(mam, "_failures", mam._Failures())
    yield
    mam._cache.clear()


def _mam_item(torrent_id: int, **fields) -> dict:
    item = {
        "id": torrent_id,
        "title": "Empire of Silence",
        "author_info": json.dumps({"1": "Christopher Ruocchio"}),
    }
    item.update(fields)
    return item


def _items(first_id: int, last_id: int) -> list[dict]:
    return [_mam_item(torrent_id) for torrent_id in range(first_id, last_id + 1)]


class TestParsing:
    def test_torrent_id_from_prowlarr_info_url(self):
        assert mam_torrent_id("https://www.myanonamouse.net/t/123456") == 123456
        assert mam_torrent_id("https://cdn.myanonamouse.net/t/42/") == 42
        assert mam_torrent_id("https://example.org/t/123") is None
        assert mam_torrent_id(None) is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example?myanonamouse.net/t/123",
            "https://evil.example#myanonamouse.net/t/123",
            "https://evil.example/myanonamouse.net/t/123",
            "https://notmyanonamouse.net/t/123",
            "https://www.myanonamouse.net.evil.example/t/123",
            "https://evil.example\\@www.myanonamouse.net/t/123",
            "https://user@www.myanonamouse.net/t/123",
            "ftp://www.myanonamouse.net/t/123",
            "https://www.myanonamouse.net/t/123abc",
            "https://www.myanonamouse.net/tor/t/123",
            "https://[::1/t/123",
        ],
    )
    def test_urls_that_only_mention_mam_are_not_mam(self, url):
        assert mam_torrent_id(url) is None

    def test_parses_narrator_series_and_bitrate(self):
        details = parse_torrent_details(
            _mam_item(
                1,
                narrator_info=json.dumps({"7": "Samuel Roukin", "8": "Samuel Roukin"}),
                series_info=json.dumps({"3": ["The Sun Eater", "1"]}),
                tags="Unabridged | 64 kbps | Tantor",
            )
        )

        assert details.narrator == "Samuel Roukin"
        assert details.series == "The Sun Eater #1"
        assert details.bitrate == "64 Kbps"
        assert details.bitrate_kbps == 64

    def test_multiple_series_and_missing_number(self):
        details = parse_torrent_details(
            _mam_item(1, series_info=json.dumps({"3": ["Saga", ""], "4": ["Arc", "2.5"]}))
        )
        assert details.series == "Saga, Arc #2.5"

    def test_missing_or_malformed_fields_are_none(self):
        details = parse_torrent_details(
            _mam_item(1, narrator_info="not json", series_info="", tags="Unabridged")
        )
        assert details == mam.MamTorrentDetails()


class TestProwlarrSearchMirror:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("Empire of Silence", "Empire of Silence"),
            ("Ender's Game", "Ender s Game"),
            ("Ender’s Game", "Ender s Game"),
            ("Dune: Part One", "Dune Part One"),
            ("Spider‑Man — Homecoming", "Spider Man Homecoming"),
            ("1,000 Years & More", "1000 Years More"),
            ("C++ Primer (5th ed.)", "C Primer 5th ed"),
            ("Les Misérables", "Les Misérables"),
            ("!!!", ""),
        ],
    )
    def test_query_is_cleaned_like_prowlarr(self, query, expected):
        assert prowlarr_search_text(query) == expected

    def test_defaults_match_prowlarr_defaults(self):
        options = mam_search_options({"implementation": "MyAnonamouse"}, [3030])

        assert options == MamSearchOptions(main_categories=("13", "15", "16"))

    def test_indexer_settings_are_mirrored(self):
        indexer = {
            "fields": [
                {"name": "searchType", "value": 1},
                {"name": "searchInDescription", "value": False},
                {"name": "searchInSeries", "value": True},
                {"name": "searchLanguages", "value": [1, "37", "junk"]},
            ]
        }

        options = mam_search_options(indexer, [7000])

        assert options == MamSearchOptions(
            search_type="active",
            search_in=("title", "author", "narrator", "series"),
            languages=("1", "37"),
            main_categories=("14",),
        )

    def test_expanded_search_covers_every_category(self):
        assert mam_search_options(None, None).main_categories == ()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            msg = "not JSON"
            raise ValueError(msg)
        return self._payload


@pytest.fixture
def mam_http(monkeypatch):
    """Record every HTTP request the MAM client makes and answer with `response`."""

    class _Http:
        def __init__(self):
            self.response = _FakeResponse(payload={"data": []})
            self.calls: list[tuple[str, dict]] = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return self.response

    http = _Http()
    monkeypatch.setattr(mam.requests, "get", http.get)
    monkeypatch.setattr(mam, "get_proxies", lambda _url: {})
    monkeypatch.setattr(mam, "get_ssl_verify", lambda _url: True)
    return http


class TestMamClient:
    def test_search_repeats_prowlarrs_request(self, mam_http):
        options = MamSearchOptions(
            search_in=("title", "author", "narrator", "series"),
            languages=("1",),
            main_categories=("13", "15", "16"),
        )

        MamClient(" session ").search("Empire of Silence", options, start=100)

        [(url, kwargs)] = mam_http.calls
        assert url == "https://www.myanonamouse.net/tor/js/loadSearchJSONbasic.php"
        assert kwargs["headers"]["Cookie"] == "mam_id=session"
        assert kwargs["allow_redirects"] is False
        params = kwargs["params"]
        assert params["tor[text]"] == "Empire of Silence"
        assert params["tor[searchType]"] == "all"
        assert params["tor[srchIn][series]"] == "true"
        assert "tor[srchIn][description]" not in params
        assert params["tor[main_cat][]"] == ["13", "15", "16"]
        assert params["tor[browse_lang][]"] == ["1"]
        assert params["tor[startNumber]"] == "100"
        assert params["perpage"] == "100"

    def test_nothing_returned_is_an_empty_page(self, mam_http):
        mam_http.response = _FakeResponse(payload={"error": "Nothing returned, out of 12"})

        assert MamClient("session").search("q", MamSearchOptions()) == []

    def test_unexpected_reply_is_an_error(self, mam_http):
        mam_http.response = _FakeResponse(payload={"error": "Invalid search parameters"})

        with pytest.raises(MamError, match="Invalid search parameters"):
            MamClient("session").search("q", MamSearchOptions())

    def test_redirect_is_an_error_not_followed(self, mam_http):
        mam_http.response = _FakeResponse(status_code=302)

        with pytest.raises(requests.exceptions.HTTPError, match="302"):
            MamClient("session").search("q", MamSearchOptions())
        assert len(mam_http.calls) == 1

    def test_forbidden_carries_mams_reply(self, mam_http):
        mam_http.response = _FakeResponse(status_code=403, text="Bad cookie: ASN mismatch")

        with pytest.raises(MamAuthError, match="ASN mismatch"):
            MamClient("session").get_username()


class _FakeMamClient:
    """Serves pages of `items_by_query` and records each (query, start) requested."""

    def __init__(self, items_by_query=None, error=None):
        self.items_by_query = items_by_query or {}
        self.error = error
        self.requests: list[tuple[str, int]] = []
        self.options: MamSearchOptions | None = None

    def __call__(self, mam_id):
        self.mam_id = mam_id
        return self

    def search(self, text, options, *, start=0):
        self.requests.append((text, start))
        self.options = options
        if self.error:
            raise self.error
        return self.items_by_query.get(text, [])[start : start + mam._RESULTS_PER_PAGE]


class TestLookup:
    def test_stops_searching_once_every_id_is_found(self, monkeypatch):
        fake = _FakeMamClient(
            {"Empire of Silence": [_mam_item(1, narrator_info=json.dumps({"1": "Roukin"}))]}
        )
        monkeypatch.setattr(mam, "MamClient", fake)

        found = lookup_torrent_details("session", {1}, ["Empire of Silence", "Other title"])

        assert found[1].narrator == "Roukin"
        assert fake.requests == [("Empire of Silence", 0)]

    def test_queries_are_sent_as_prowlarr_sent_them(self, monkeypatch):
        fake = _FakeMamClient()
        monkeypatch.setattr(mam, "MamClient", fake)

        lookup_torrent_details("session", {1}, ["Ender's Game", "Ender’s Game", "!!!"])

        assert fake.requests == [("Ender s Game", 0)]

    def test_uses_cache_on_repeat(self, monkeypatch):
        fake = _FakeMamClient({"q": [_mam_item(1, narrator_info=json.dumps({"1": "Roukin"}))]})
        monkeypatch.setattr(mam, "MamClient", fake)

        lookup_torrent_details("session", {1}, ["q"])
        lookup_torrent_details("session", {1}, ["q"])

        assert fake.requests == [("q", 0)]

    def test_expired_entries_are_pruned_when_storing(self):
        mam._cache[5] = (mam.MamTorrentDetails(), time.time() - 2 * mam._CACHE_TTL_SECONDS)

        mam._store({1: mam.MamTorrentDetails()})

        assert set(mam._cache) == {1}

    def test_pages_until_every_id_is_found(self, monkeypatch):
        fake = _FakeMamClient({"q": _items(1, 150)})
        monkeypatch.setattr(mam, "MamClient", fake)

        found = lookup_torrent_details("session", {5, 140}, ["q"])

        assert set(found) == {5, 140}
        assert fake.requests == [("q", 0), ("q", 100)]

    def test_first_page_of_every_query_comes_before_further_pages(self, monkeypatch):
        fake = _FakeMamClient({"a": [*_items(1, 100), _mam_item(500)], "b": [_mam_item(900)]})
        monkeypatch.setattr(mam, "MamClient", fake)

        found = lookup_torrent_details("session", {500, 900}, ["a", "b"])

        assert set(found) == {500, 900}
        assert fake.requests == [("a", 0), ("b", 0), ("a", 100)]

    def test_a_short_page_is_the_last(self, monkeypatch):
        fake = _FakeMamClient({"q": _items(1, 50)})
        monkeypatch.setattr(mam, "MamClient", fake)

        assert lookup_torrent_details("session", {999}, ["q"]) == {}
        assert fake.requests == [("q", 0)]

    def test_requests_are_capped(self, monkeypatch):
        fake = _FakeMamClient({"q": _items(1, 1000)})
        monkeypatch.setattr(mam, "MamClient", fake)

        lookup_torrent_details("session", {999}, ["q"])

        assert len(fake.requests) == mam._MAX_REQUESTS

    @pytest.mark.parametrize(
        "error",
        [
            MamAuthError("rejected"),
            MamError("odd reply"),
            requests.exceptions.ConnectionError("down"),
            ValueError("not JSON"),
        ],
    )
    def test_failures_return_empty_instead_of_raising(self, monkeypatch, error):
        fake = _FakeMamClient(error=error)
        monkeypatch.setattr(mam, "MamClient", fake)

        assert lookup_torrent_details("session", {1}, ["q", "r"]) == {}
        assert fake.requests == [("q", 0)]

    def test_expired_deadline_skips_the_request(self, monkeypatch):
        fake = _FakeMamClient()
        monkeypatch.setattr(mam, "MamClient", fake)

        assert lookup_torrent_details("session", {1}, ["q"], deadline=0.0) == {}
        assert fake.requests == []


class TestFailureBackoff:
    @staticmethod
    def _failing(monkeypatch, error=None):
        fake = _FakeMamClient(error=error or requests.exceptions.ConnectionError("down"))
        monkeypatch.setattr(mam, "MamClient", fake)
        return fake

    @staticmethod
    def _retry_now():
        mam._failures.retry_at = 0.0

    def test_a_failure_holds_the_next_lookup_back(self, monkeypatch):
        fake = self._failing(monkeypatch)

        lookup_torrent_details("session", {1}, ["q"])
        lookup_torrent_details("session", {1}, ["q"])

        assert fake.requests == [("q", 0)]
        assert mam._failures.count == 1
        assert mam._failures.retry_at - time.monotonic() == pytest.approx(60, abs=5)

    def test_backoff_doubles_up_to_half_an_hour(self, monkeypatch):
        self._failing(monkeypatch)
        delays = []

        for _ in range(mam._MAX_CONSECUTIVE_FAILURES - 1):
            self._retry_now()
            before = time.monotonic()
            lookup_torrent_details("session", {1}, ["q"])
            delays.append(round(mam._failures.retry_at - before))

        assert delays == [60, 120, 240, 480, 960, 1800, 1800, 1800, 1800]

    def test_ten_failures_in_a_row_stop_enrichment(self, monkeypatch):
        fake = self._failing(monkeypatch, MamAuthError("rejected"))

        for _ in range(mam._MAX_CONSECUTIVE_FAILURES + 3):
            self._retry_now()
            lookup_torrent_details("session", {1}, ["q"])

        assert len(fake.requests) == mam._MAX_CONSECUTIVE_FAILURES
        assert mam._blocked_reason("session") == "stopped after 10 consecutive failures"

    def test_a_success_resets_the_count(self, monkeypatch):
        fake = self._failing(monkeypatch)
        for _ in range(3):
            self._retry_now()
            lookup_torrent_details("session", {1}, ["q"])

        fake.error = None
        self._retry_now()
        lookup_torrent_details("session", {1}, ["q"])

        assert mam._failures.count == 0
        assert mam._blocked_reason("session") is None

    def test_a_new_session_id_starts_over(self, monkeypatch):
        fake = self._failing(monkeypatch)
        for _ in range(mam._MAX_CONSECUTIVE_FAILURES):
            self._retry_now()
            lookup_torrent_details("old", {1}, ["q"])

        fake.error = None
        lookup_torrent_details("new", {1}, ["q"])

        assert fake.requests[-1] == ("q", 0)
        assert len(fake.requests) == mam._MAX_CONSECUTIVE_FAILURES + 1

    def test_a_passing_session_test_turns_enrichment_back_on(self, monkeypatch):
        self._failing(monkeypatch)
        for _ in range(mam._MAX_CONSECUTIVE_FAILURES):
            self._retry_now()
            lookup_torrent_details("session", {1}, ["q"])
        assert mam._blocked_reason("session") is not None

        monkeypatch.setattr(mam, "MamClient", _UsernameClient)
        result = _test_mam_connection({"PROWLARR_MAM_ID": "session"})

        assert result["success"] is True
        assert mam._blocked_reason("session") is None


class _UsernameClient:
    def __init__(self, mam_id):
        self.mam_id = mam_id

    def get_username(self):
        return "reader"


class TestReleaseEnrichment:
    def _release(self, info_url: str, guid: str):
        return _prowlarr_result_to_release(
            {
                "guid": guid,
                "infoUrl": info_url,
                "title": "Empire of Silence",
                "indexer": "MyAnonamouse",
                "indexerId": 1,
                "protocol": "torrent",
                "categories": [3030],
            },
            "audiobook",
        )

    def test_mam_details_fill_the_release(self, monkeypatch):
        fake = _FakeMamClient(
            {
                "Empire of Silence": [
                    _mam_item(
                        99,
                        narrator_info=json.dumps({"1": "Samuel Roukin"}),
                        series_info=json.dumps({"2": ["The Sun Eater", "1"]}),
                        tags="64 kbps",
                    )
                ]
            }
        )
        monkeypatch.setattr(mam, "MamClient", fake)
        options = MamSearchOptions(main_categories=("13", "15", "16"))
        mam_release = self._release("https://www.myanonamouse.net/t/99", "mam-guid")

        _enrich_mam_releases(
            [mam_release], "session", ["Empire of Silence"], options, deadline=None
        )

        assert mam_release.extra["narrator"] == "Samuel Roukin"
        assert mam_release.extra["series"] == "The Sun Eater #1"
        assert mam_release.extra["bitrate"] == "64 Kbps"
        assert mam_release.extra["bitrate_value"] == 64
        assert fake.options == options

    def test_a_url_that_only_mentions_mam_is_never_looked_up(self, mam_http):
        spoofed = self._release("https://evil.example?myanonamouse.net/t/99", "guid")

        _enrich_mam_releases(
            [spoofed], "session", ["Empire of Silence"], MamSearchOptions(), deadline=None
        )

        assert mam_http.calls == []
        assert "narrator" not in spoofed.extra

    def test_torznab_bitrate_attribute_is_used_for_other_indexers(self):
        release = _prowlarr_result_to_release(
            {
                "guid": "g",
                "title": "Book",
                "protocol": "torrent",
                "torznabAttrs": {"bitrate": "320"},
            },
            "audiobook",
        )
        assert release.extra["bitrate"] == "320 Kbps"
        assert release.extra["bitrate_value"] == 320


MAM_INDEXER_ID = 1
OTHER_INDEXER_ID = 2


class _ProwlarrWithMam:
    """A Prowlarr with MyAnonamouse and one other indexer enabled."""

    indexer_timeout = 90

    def __init__(self, mam_fields=None):
        self.mam_fields = mam_fields or []

    def get_enabled_indexers_detailed(self, *, raise_on_error=False):
        del raise_on_error
        capabilities = {"categories": [{"id": 7000, "subCategories": []}]}
        return [
            {
                "id": MAM_INDEXER_ID,
                "enable": True,
                "implementation": "MyAnonamouse",
                "fields": self.mam_fields,
                "capabilities": capabilities,
            },
            {
                "id": OTHER_INDEXER_ID,
                "enable": True,
                "implementation": "Torznab",
                "capabilities": capabilities,
            },
        ]

    def get_enriched_indexer_ids(self, restrict_to=None, indexers=None):
        del restrict_to, indexers
        return [MAM_INDEXER_ID]

    def torznab_search(
        self, *, indexer_id, query, categories=None, search_type="book", limit=100, offset=0
    ):
        del query, categories, search_type, limit, offset
        # The other indexer's result also claims a MyAnonamouse URL.
        torrent_id = 99 if indexer_id == MAM_INDEXER_ID else 77
        return [
            {
                "guid": f"guid-{indexer_id}",
                "title": "Empire of Silence",
                "indexerId": indexer_id,
                "indexer": f"Indexer {indexer_id}",
                "protocol": "torrent",
                "categories": [{"id": 7020}],
                "infoUrl": f"https://www.myanonamouse.net/t/{torrent_id}",
            }
        ]


class TestSearchEnrichment:
    def _search(self, monkeypatch, client, *, expand_search=False):
        from shelfmark.core.search_plan import build_release_search_plan

        values = {
            "PROWLARR_INDEXERS": "",
            "PROWLARR_AUTO_EXPAND": False,
            "PROWLARR_MAM_ID": "session",
        }
        monkeypatch.setattr(
            prowlarr_source.config, "get", lambda key, default=None: values.get(key, default)
        )
        lookups = []

        def fake_lookup(mam_id, torrent_ids, queries, *, options, deadline):
            del deadline
            lookups.append((mam_id, torrent_ids, queries, options))
            return {99: mam.MamTorrentDetails(series="The Sun Eater #1")}

        monkeypatch.setattr(prowlarr_source, "lookup_torrent_details", fake_lookup)
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: client)
        book = BookMetadata(
            provider="hardcover",
            provider_id="1",
            title="Empire of Silence",
            authors=["Christopher Ruocchio"],
        )
        plan = build_release_search_plan(book, languages=["en"])
        results = source.search(book, plan, expand_search=expand_search, content_type="ebook")
        return results, lookups

    def test_only_the_mam_indexer_is_enriched(self, monkeypatch):
        results, lookups = self._search(monkeypatch, _ProwlarrWithMam())

        [(mam_id, torrent_ids, queries, _options)] = lookups
        assert (mam_id, torrent_ids, queries) == ("session", {99}, ["Empire of Silence"])
        series = {release.indexer: release.extra.get("series") for release in results}
        assert series == {"Indexer 1": "The Sun Eater #1", "Indexer 2": None}

    def test_lookup_mirrors_the_mam_indexer_settings(self, monkeypatch):
        client = _ProwlarrWithMam(
            [{"name": "searchType", "value": 2}, {"name": "searchLanguages", "value": [1]}]
        )

        _results, lookups = self._search(monkeypatch, client)

        assert lookups[0][3] == MamSearchOptions(
            search_type="fl", languages=("1",), main_categories=("14",)
        )

    def test_expanded_search_looks_in_every_category(self, monkeypatch):
        _results, lookups = self._search(monkeypatch, _ProwlarrWithMam(), expand_search=True)

        assert lookups[0][3].main_categories == ()


class TestColumnConfig:
    def _source(self, monkeypatch, mam_id: str) -> ProwlarrSource:
        monkeypatch.setattr(prowlarr_source, "_get_mam_session_id", lambda: mam_id)
        source = ProwlarrSource()
        monkeypatch.setattr(source, "_get_client", lambda: None)
        return source

    def test_no_mam_columns_without_session_id(self, monkeypatch):
        config = self._source(monkeypatch, "").get_column_config()
        keys = [c.key for c in config.columns]

        assert "extra.narrator" not in keys
        assert "extra.series" not in keys
        assert config.grid_template == "minmax(0,2fr) minmax(140px,1fr) 50px 50px 90px 80px"

    def test_audiobook_shows_all_enabled_mam_columns(self, monkeypatch):
        config = apply_column_visibility(
            self._source(monkeypatch, "session").get_column_config(),
            content_type="audiobook",
            is_setting_enabled=lambda _key: True,
        )

        assert [c.key for c in config.columns][:3] == [
            "extra.series",
            "extra.narrator",
            "indexer",
        ]
        assert "extra.bitrate" in [c.key for c in config.columns]
        assert config.grid_template == (
            "minmax(0,2fr) minmax(90px,1fr) minmax(90px,1fr) minmax(140px,1fr) "
            "50px 50px 90px 72px 80px"
        )

    def test_ebook_keeps_series_only(self, monkeypatch):
        config = apply_column_visibility(
            self._source(monkeypatch, "session").get_column_config(),
            content_type="ebook",
            is_setting_enabled=lambda _key: True,
        )
        keys = [c.key for c in config.columns]

        assert "extra.series" in keys
        assert "extra.narrator" not in keys
        assert "extra.bitrate" not in keys
        assert config.grid_template == (
            "minmax(0,2fr) minmax(90px,1fr) minmax(140px,1fr) 50px 50px 90px 80px"
        )

    def test_toggles_hide_columns(self, monkeypatch):
        config = apply_column_visibility(
            self._source(monkeypatch, "session").get_column_config(),
            content_type="audiobook",
            is_setting_enabled=lambda key: key == "SHOW_NARRATOR_COLUMN",
        )
        keys = [c.key for c in config.columns]

        assert "extra.narrator" in keys
        assert "extra.series" not in keys
        assert "extra.bitrate" not in keys
        assert len(config.grid_template.split(" ")) == len(keys) + 1


class TestApplyColumnVisibility:
    def test_unchanged_when_nothing_is_gated(self):
        config = ReleaseColumnConfig(columns=[ColumnSchema(key="size", label="Size")])
        assert (
            apply_column_visibility(config, content_type="ebook", is_setting_enabled=bool) is config
        )

    def test_rebuilds_template_when_tracks_do_not_line_up(self):
        config = ReleaseColumnConfig(
            columns=[
                ColumnSchema(key="a", label="A", width="60px"),
                ColumnSchema(key="b", label="B", width="70px", content_types=("audiobook",)),
            ],
            grid_template="minmax(0, 2fr) 60px",
        )
        result = apply_column_visibility(
            config, content_type="ebook", is_setting_enabled=lambda _k: True
        )
        assert result.grid_template == "minmax(0, 2fr) 60px"

    def test_gates_are_not_serialized(self):
        config = ReleaseColumnConfig(
            columns=[ColumnSchema(key="a", label="A", setting_key="X", content_types=("ebook",))]
        )
        column = serialize_column_config(config)["columns"][0]
        assert "setting_key" not in column
        assert "content_types" not in column
