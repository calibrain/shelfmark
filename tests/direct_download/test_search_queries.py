from shelfmark.core.models import SearchFilters
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import BrowseRecord
from shelfmark.release_sources.direct_download import DirectDownloadSource


def _browse_record(record_id: str, title: str) -> BrowseRecord:
    return BrowseRecord(id=record_id, title=title, source="direct_download")


def _enable_direct_download(monkeypatch):
    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_ENABLED":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True)
    return dd


class TestDirectDownloadSearchQueries:
    def test_uses_search_title_for_english_queries(self, monkeypatch):
        captured: list[str] = []

        def fake_search_books(query: str, filters):
            captured.append(query)
            return []

        dd = _enable_direct_download(monkeypatch)

        monkeypatch.setattr(dd, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        plan = build_release_search_plan(book, languages=["en", "hu"])
        source.search(book, plan, expand_search=True)

        assert "The Final Empire Brandon Sanderson" in captured
        assert "A végső birodalom Brandon Sanderson" in captured
        assert "Mistborn: The Final Empire Brandon Sanderson" not in captured

    def test_deduplicates_results_across_localized_queries(self, monkeypatch):
        captured: list[tuple[str, list[str] | None]] = []
        records_by_query = {
            "The Final Empire Brandon Sanderson": [
                _browse_record("shared", "Shared release"),
                _browse_record("en-only", "English only"),
            ],
            "A végső birodalom Brandon Sanderson": [
                _browse_record("shared", "Shared release"),
                _browse_record("hu-only", "Hungarian only"),
            ],
        }

        def fake_search_books(query: str, filters):
            captured.append((query, filters.lang))
            return records_by_query[query]

        dd = _enable_direct_download(monkeypatch)

        monkeypatch.setattr(dd, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        plan = build_release_search_plan(book, languages=["en", "hu"])
        results = source.search(book, plan, expand_search=True)

        assert captured == [
            ("The Final Empire Brandon Sanderson", ["en"]),
            ("A végső birodalom Brandon Sanderson", ["hu"]),
        ]
        assert [release.source_id for release in results] == ["shared", "en-only", "hu-only"]

    def test_retries_without_language_filters_when_localized_queries_miss(self, monkeypatch):
        captured: list[tuple[str, list[str] | None]] = []
        fallback_results = {
            "The Final Empire Brandon Sanderson": [
                _browse_record("fallback-en", "Fallback English")
            ],
            "A végső birodalom Brandon Sanderson": [
                _browse_record("fallback-hu", "Fallback Hungarian")
            ],
        }

        def fake_search_books(query: str, filters):
            captured.append((query, filters.lang))
            if filters.lang:
                return []
            return fallback_results[query]

        dd = _enable_direct_download(monkeypatch)

        monkeypatch.setattr(dd, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        plan = build_release_search_plan(book, languages=["en", "hu"])
        results = source.search(book, plan, expand_search=True)

        assert captured == [
            ("The Final Empire Brandon Sanderson", ["en"]),
            ("A végső birodalom Brandon Sanderson", ["hu"]),
            ("The Final Empire Brandon Sanderson", None),
            ("A végső birodalom Brandon Sanderson", None),
        ]
        assert [release.source_id for release in results] == ["fallback-en", "fallback-hu"]

    def test_manual_query_fallback_preserves_other_filters(self, monkeypatch):
        captured: list[tuple[str, list[str] | None, list[str] | None]] = []

        def fake_search_books(query: str, filters):
            captured.append((query, filters.lang, filters.format))
            if filters.lang:
                return []
            return [_browse_record("manual-1", "Manual result")]

        dd = _enable_direct_download(monkeypatch)

        monkeypatch.setattr(dd, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            authors=["Brandon Sanderson"],
        )

        plan = build_release_search_plan(
            book,
            languages=["en"],
            manual_query="mistborn custom query",
            source_filters=SearchFilters(format=["epub"], sort="newest"),
        )
        results = source.search(book, plan)

        assert [release.source_id for release in results] == ["manual-1"]
        assert captured == [
            ("mistborn custom query", ["en"], ["epub"]),
            ("mistborn custom query", None, ["epub"]),
        ]
