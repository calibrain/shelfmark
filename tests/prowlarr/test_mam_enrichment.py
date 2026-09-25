"""Tests for MyAnonamouse enrichment of Prowlarr results (narrator, series, bitrate)."""

import json

import pytest
import requests

import shelfmark.release_sources.prowlarr.mam as mam
import shelfmark.release_sources.prowlarr.source as prowlarr_source
from shelfmark.release_sources import (
    ColumnSchema,
    ReleaseColumnConfig,
    apply_column_visibility,
    serialize_column_config,
)
from shelfmark.release_sources.prowlarr.mam import (
    MamAuthError,
    lookup_torrent_details,
    mam_base_url,
    mam_torrent_id,
    parse_torrent_details,
)
from shelfmark.release_sources.prowlarr.source import (
    ProwlarrSource,
    _enrich_mam_releases,
    _prowlarr_result_to_release,
)


@pytest.fixture(autouse=True)
def _clear_mam_cache():
    mam._cache.clear()
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


class TestParsing:
    def test_torrent_id_from_prowlarr_info_url(self):
        assert mam_torrent_id("https://www.myanonamouse.net/t/123456") == 123456
        assert mam_torrent_id("https://cdn.myanonamouse.net/t/42") == 42
        assert mam_torrent_id("https://example.org/t/123") is None
        assert mam_torrent_id(None) is None

    def test_base_url_follows_the_result(self):
        assert mam_base_url("https://cdn.myanonamouse.net/t/42") == "https://cdn.myanonamouse.net"
        assert mam_base_url("https://example.org/t/42") == mam.DEFAULT_MAM_BASE_URL

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


class _FakeMamClient:
    def __init__(self, items_by_query=None, error=None):
        self.items_by_query = items_by_query or {}
        self.error = error
        self.queries: list[str] = []

    def __call__(self, mam_id, base_url=mam.DEFAULT_MAM_BASE_URL):
        self.mam_id = mam_id
        self.base_url = base_url
        return self

    def search(self, text):
        self.queries.append(text)
        if self.error:
            raise self.error
        return self.items_by_query.get(text, [])


class TestLookup:
    def test_stops_searching_once_every_id_is_found(self, monkeypatch):
        fake = _FakeMamClient(
            {"Empire of Silence": [_mam_item(1, narrator_info=json.dumps({"1": "Roukin"}))]}
        )
        monkeypatch.setattr(mam, "MamClient", fake)

        found = lookup_torrent_details("session", {1}, ["Empire of Silence", "Other title"])

        assert found[1].narrator == "Roukin"
        assert fake.queries == ["Empire of Silence"]

    def test_uses_cache_on_repeat(self, monkeypatch):
        fake = _FakeMamClient({"q": [_mam_item(1, narrator_info=json.dumps({"1": "Roukin"}))]})
        monkeypatch.setattr(mam, "MamClient", fake)

        lookup_torrent_details("session", {1}, ["q"])
        lookup_torrent_details("session", {1}, ["q"])

        assert fake.queries == ["q"]

    @pytest.mark.parametrize(
        "error",
        [MamAuthError("rejected"), requests.exceptions.ConnectionError("down")],
    )
    def test_failures_return_empty_instead_of_raising(self, monkeypatch, error):
        fake = _FakeMamClient(error=error)
        monkeypatch.setattr(mam, "MamClient", fake)

        assert lookup_torrent_details("session", {1}, ["q", "r"]) == {}
        assert fake.queries == ["q"]

    def test_expired_deadline_skips_the_request(self, monkeypatch):
        fake = _FakeMamClient()
        monkeypatch.setattr(mam, "MamClient", fake)

        assert lookup_torrent_details("session", {1}, ["q"], deadline=0.0) == {}
        assert fake.queries == []


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

    def test_only_mam_releases_are_enriched(self, monkeypatch):
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
        mam_release = self._release("https://www.myanonamouse.net/t/99", "mam-guid")
        other_release = self._release("https://tracker.example/t/99", "other-guid")

        _enrich_mam_releases(
            [mam_release, other_release], "session", ["Empire of Silence"], deadline=None
        )

        assert mam_release.extra["narrator"] == "Samuel Roukin"
        assert mam_release.extra["series"] == "The Sun Eater #1"
        assert mam_release.extra["bitrate"] == "64 Kbps"
        assert mam_release.extra["bitrate_value"] == 64
        assert "narrator" not in other_release.extra
        assert fake.base_url == "https://www.myanonamouse.net"

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
