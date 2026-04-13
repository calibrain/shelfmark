"""Unit tests for the Newznab release source."""

from unittest.mock import MagicMock

from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import ReleaseProtocol
from shelfmark.release_sources.newznab.source import (
    NewznabSource,
    _newznab_result_to_release,
)

# ── fixtures / helpers ─────────────────────────────────────────────────────────


def _make_book(**kwargs) -> BookMetadata:
    defaults = {
        "provider": "hardcover",
        "provider_id": "1",
        "title": "Dune",
        "authors": ["Frank Herbert"],
    }
    defaults.update(kwargs)
    return BookMetadata(**defaults)


def _make_result(**kwargs) -> dict:
    """Minimal Newznab-like result dict."""
    base = {
        "title": "Dune (2024) [EPUB]",
        "guid": "https://indexer.example.com/nzb/42",
        "downloadUrl": "https://indexer.example.com/nzb/42?apikey=secret",
        "protocol": "usenet",
        "size": 2097152,
        "indexer": "MyIndexer",
        "categories": [7000],
        "indexerFlags": [],
        "publishDate": "2024-01-01",
    }
    base.update(kwargs)
    return base


def _make_plan(book: BookMetadata, *, manual_query: str | None = None):
    from shelfmark.core.search_plan import build_release_search_plan

    return build_release_search_plan(book, languages=["en"], manual_query=manual_query)


# ── _newznab_result_to_release ─────────────────────────────────────────────────


class TestResultToRelease:
    def test_basic_usenet_result(self):
        r = _newznab_result_to_release(_make_result())
        assert r.source == "newznab"
        assert r.title == "Dune (2024) [EPUB]"
        assert r.protocol == ReleaseProtocol.NZB
        assert r.size == "2.0 MB"
        assert r.size_bytes == 2097152
        assert r.indexer == "MyIndexer"
        assert r.source_id == "https://indexer.example.com/nzb/42"
        assert r.download_url == "https://indexer.example.com/nzb/42?apikey=secret"

    def test_torrent_result_has_torrent_protocol(self):
        r = _newznab_result_to_release(
            _make_result(
                protocol="torrent",
                magnetUrl="magnet:?xt=urn:btih:abc",
                categories=[3030],
            )
        )
        assert r.protocol == ReleaseProtocol.TORRENT

    def test_audiobook_category_detected(self):
        r = _newznab_result_to_release(_make_result(categories=[3030]), "ebook")
        assert r.content_type == "audiobook"

    def test_book_category_detected(self):
        r = _newznab_result_to_release(_make_result(categories=[7000]))
        assert r.content_type == "book"

    def test_no_categories_uses_content_type_fallback(self):
        r = _newznab_result_to_release(_make_result(categories=[]), "audiobook")
        assert r.content_type == "audiobook"

    def test_freeleech_flag_detected_via_download_volume(self):
        r = _newznab_result_to_release(_make_result(downloadVolumeFactor=0.0))
        assert r.extra["freeleech"] is True
        assert "FreeLeech" in r.extra["indexer_flags"]

    def test_freeleech_flag_detected_via_indexer_flags(self):
        r = _newznab_result_to_release(_make_result(indexerFlags=["freeleech"]))
        assert r.extra["freeleech"] is True

    def test_vip_detected_from_title(self):
        r = _newznab_result_to_release(_make_result(title="Dune [VIP] [EPUB]"))
        assert r.extra["vip"] is True
        assert "VIP" in r.extra["indexer_flags"]

    def test_duplicate_flags_deduplicated(self):
        r = _newznab_result_to_release(_make_result(indexerFlags=["FreeLeech", "freeleech", "FL"]))
        lower_flags = [f.lower() for f in r.extra["indexer_flags"]]
        assert lower_flags.count("freeleech") == 1

    def test_seeders_only_set_for_torrents(self):
        usenet = _newznab_result_to_release(_make_result(protocol="usenet", seeders=10))
        assert usenet.seeders is None

        torrent = _newznab_result_to_release(
            _make_result(protocol="torrent", seeders=10, leechers=2)
        )
        assert torrent.seeders == 10
        assert torrent.peers == "10 / 2"

    def test_fallback_source_id_when_no_guid(self):
        r = _newznab_result_to_release(_make_result(guid=None))
        assert r.source_id.startswith("newznab:")

    def test_none_size_returns_none(self):
        r = _newznab_result_to_release(_make_result(size=None))
        assert r.size is None
        assert r.size_bytes is None

    def test_extra_fields_preserved(self):
        r = _newznab_result_to_release(
            _make_result(
                author="Frank Herbert",
                bookTitle="Dune",
                infoHash="abc123",
            )
        )
        assert r.extra["author"] == "Frank Herbert"
        assert r.extra["book_title"] == "Dune"
        assert r.extra["info_hash"] == "abc123"


# ── NewznabSource.is_available ─────────────────────────────────────────────────


class TestIsAvailable:
    def _config(self, **overrides):
        values = {
            "NEWZNAB_ENABLED": True,
            "NEWZNAB_URL": "http://nzbhydra:5076",
        }
        values.update(overrides)
        return lambda k, default=None: values.get(k, default)

    def test_available_when_enabled_and_url_set(self, monkeypatch):
        import shelfmark.release_sources.newznab.source as mod

        monkeypatch.setattr(mod.config, "get", self._config())
        assert NewznabSource().is_available() is True

    def test_unavailable_when_disabled(self, monkeypatch):
        import shelfmark.release_sources.newznab.source as mod

        monkeypatch.setattr(mod.config, "get", self._config(NEWZNAB_ENABLED=False))
        assert NewznabSource().is_available() is False

    def test_unavailable_when_no_url(self, monkeypatch):
        import shelfmark.release_sources.newznab.source as mod

        monkeypatch.setattr(mod.config, "get", self._config(NEWZNAB_URL=""))
        assert NewznabSource().is_available() is False


# ── NewznabSource.search ───────────────────────────────────────────────────────


class TestSearch:
    def _fake_config(self, **overrides):
        values = {
            "NEWZNAB_AUTO_EXPAND": False,
        }
        values.update(overrides)
        return lambda k, default=None: values.get(k, default)

    def _patched_source(self, monkeypatch, client, config_overrides=None):
        import shelfmark.release_sources.newznab.source as mod

        monkeypatch.setattr(mod.config, "get", self._fake_config(**(config_overrides or {})))
        src = NewznabSource()
        monkeypatch.setattr(src, "_get_client", lambda: client)
        return src

    def test_returns_empty_when_no_client(self, monkeypatch):
        import shelfmark.release_sources.newznab.source as mod

        monkeypatch.setattr(mod.config, "get", self._fake_config())
        src = NewznabSource()
        monkeypatch.setattr(src, "_get_client", lambda: None)
        book = _make_book()
        results = src.search(book, _make_plan(book))
        assert results == []

    def test_returns_empty_with_no_queries(self, monkeypatch):
        client = MagicMock()
        client.search.return_value = []
        src = self._patched_source(monkeypatch, client)
        book = BookMetadata(provider="test", provider_id="1", title="", authors=[])
        from shelfmark.core.search_plan import ReleaseSearchPlan

        empty_plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="",
            title_variants=[],
            grouped_title_variants=[],
            manual_query=None,
            indexers=[],
        )
        results = src.search(book, empty_plan)
        assert results == []
        client.search.assert_not_called()

    def test_searches_with_ebook_category(self, monkeypatch):
        client = MagicMock()
        client.search.return_value = []
        src = self._patched_source(monkeypatch, client)
        book = _make_book()
        src.search(book, _make_plan(book), content_type="ebook")
        _, kwargs = client.search.call_args
        assert kwargs["categories"] == [7000]

    def test_searches_with_audiobook_category(self, monkeypatch):
        client = MagicMock()
        client.search.return_value = []
        src = self._patched_source(monkeypatch, client)
        book = _make_book()
        src.search(book, _make_plan(book), content_type="audiobook")
        _, kwargs = client.search.call_args
        assert kwargs["categories"] == [3030]

    def test_expand_search_removes_categories(self, monkeypatch):
        client = MagicMock()
        client.search.return_value = []
        src = self._patched_source(monkeypatch, client)
        book = _make_book()
        src.search(book, _make_plan(book), expand_search=True, content_type="ebook")
        _, kwargs = client.search.call_args
        assert kwargs["categories"] is None

    def test_deduplicates_results_by_guid(self, monkeypatch):
        dup = _make_result(guid="same-guid")
        client = MagicMock()
        client.search.side_effect = [[dup], [dup]]  # two queries, same result each
        book = _make_book()
        src = self._patched_source(monkeypatch, client)
        plan_two = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=[],
            author="Frank Herbert",
            title_variants=[
                ReleaseSearchVariant("Dune", "Frank Herbert"),
                ReleaseSearchVariant("Düne", "Frank Herbert"),
            ],
            grouped_title_variants=[],
            manual_query=None,
            indexers=[],
        )
        results = src.search(book, plan_two)
        assert len(results) == 1

    def test_auto_expand_retries_without_categories(self, monkeypatch):
        calls: list = []

        def fake_search(query, categories=None):
            calls.append(categories)
            return [] if categories else [_make_result()]

        client = MagicMock()
        client.search.side_effect = fake_search
        src = self._patched_source(monkeypatch, client, {"NEWZNAB_AUTO_EXPAND": True})
        book = _make_book()
        results = src.search(book, _make_plan(book), content_type="ebook")

        assert [7000] in calls  # first call with category
        assert None in calls  # auto-expanded call without
        assert len(results) == 1

    def test_no_auto_expand_when_disabled(self, monkeypatch):
        client = MagicMock()
        client.search.return_value = []
        src = self._patched_source(monkeypatch, client, {"NEWZNAB_AUTO_EXPAND": False})
        book = _make_book()
        src.search(book, _make_plan(book))
        # Only one call per query, no retry
        assert client.search.call_count == 1

    def test_converts_results_to_releases(self, monkeypatch):
        client = MagicMock()
        client.search.return_value = [_make_result()]
        src = self._patched_source(monkeypatch, client)
        book = _make_book()
        results = src.search(book, _make_plan(book))
        assert len(results) == 1
        r = results[0]
        assert r.source == "newznab"
        assert r.protocol == ReleaseProtocol.NZB

    def test_manual_query_overrides_title_variants(self, monkeypatch):
        client = MagicMock()
        client.search.return_value = []
        src = self._patched_source(monkeypatch, client)
        book = _make_book()
        plan = _make_plan(book, manual_query="custom search term")
        src.search(book, plan)
        call_kwargs = client.search.call_args
        assert call_kwargs[1]["query"] == "custom search term"

    def test_isbn_used_when_no_title_variants(self, monkeypatch):
        client = MagicMock()
        client.search.return_value = []
        src = self._patched_source(monkeypatch, client)
        book = _make_book()
        from shelfmark.core.search_plan import ReleaseSearchPlan

        isbn_plan = ReleaseSearchPlan(
            languages=["en"],
            isbn_candidates=["9780441013593"],
            author="Frank Herbert",
            title_variants=[],
            grouped_title_variants=[],
            manual_query=None,
            indexers=[],
        )
        src.search(book, isbn_plan)
        call_kwargs = client.search.call_args
        assert call_kwargs[1]["query"] == "9780441013593"

    def test_exception_in_client_returns_empty(self, monkeypatch):
        client = MagicMock()
        client.search.side_effect = RuntimeError("boom")
        src = self._patched_source(monkeypatch, client)
        book = _make_book()
        results = src.search(book, _make_plan(book))
        assert results == []
