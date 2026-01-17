from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources.search_plan import build_release_search_plan


class TestReleaseSearchPlan:
    def test_uses_default_languages_when_none(self, monkeypatch):
        # config.BOOK_LANGUAGE is a Config attribute; patch the instance.
        import shelfmark.release_sources.search_plan as sp

        monkeypatch.setattr(sp.config, "BOOK_LANGUAGE", ["en", "hu"], raising=False)

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
            isbn_13="9780765311788",
        )

        plan = build_release_search_plan(book, languages=None)

        assert plan.languages == ["en", "hu"]
        assert plan.isbn_candidates == ["9780765311788"]
        assert [v.query for v in plan.title_variants] == [
            "The Final Empire Brandon Sanderson",
            "A végső birodalom Brandon Sanderson",
        ]

        assert [(v.title, v.languages) for v in plan.grouped_title_variants] == [
            ("The Final Empire", ["en"]),
            ("A végső birodalom", ["hu"]),
        ]

    def test_all_language_disables_grouping(self, monkeypatch):
        import shelfmark.release_sources.search_plan as sp

        monkeypatch.setattr(sp.config, "BOOK_LANGUAGE", ["en"], raising=False)

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="The Lightning Thief",
            authors=["Rick Riordan"],
            titles_by_language={"hu": "A villámtolvaj"},
        )

        plan = build_release_search_plan(book, languages=["all"])

        assert plan.languages is None
        assert [v.query for v in plan.title_variants] == [
            "The Lightning Thief Rick Riordan",
        ]
        assert [(v.title, v.languages) for v in plan.grouped_title_variants] == [
            ("The Lightning Thief", None),
        ]
