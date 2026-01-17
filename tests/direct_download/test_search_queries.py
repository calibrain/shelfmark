from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources.direct_download import DirectDownloadSource
from shelfmark.release_sources.search_plan import build_release_search_plan


class TestDirectDownloadSearchQueries:
    def test_uses_search_title_for_english_queries(self, monkeypatch):
        captured: list[str] = []

        def fake_search_books(query: str, filters):
            captured.append(query)
            return []

        import shelfmark.release_sources.direct_download as dd

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
