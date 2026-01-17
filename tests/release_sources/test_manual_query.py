from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources.search_plan import build_release_search_plan


class TestReleaseSearchPlanManualQuery:
    def test_manual_query_overrides_plan(self, monkeypatch):
        import shelfmark.release_sources.search_plan as sp

        monkeypatch.setattr(sp.config, "BOOK_LANGUAGE", ["en", "hu"], raising=False)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={"hu": "A végső birodalom"},
            isbn_13="9780765311788",
        )

        plan = build_release_search_plan(book, languages=None, manual_query="some custom query")

        assert plan.manual_query == "some custom query"
        assert plan.isbn_candidates == []
        assert plan.languages == ["en", "hu"]
        assert [v.query for v in plan.title_variants] == ["some custom query"]
        assert [(v.title, v.languages) for v in plan.grouped_title_variants] == [("some custom query", None)]
