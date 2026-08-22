from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata


def _book_language_config_get(
    global_languages: list[str],
    user_languages: list[str] | None = None,
):
    """Stand in for config.get(), answering BOOK_LANGUAGE per user."""

    def _get(key: str, default: object = None, user_id: int | None = None) -> object:
        if key != "BOOK_LANGUAGE":
            return default
        if user_id is not None and user_languages is not None:
            return user_languages
        return global_languages

    return _get


class TestReleaseSearchPlan:
    def test_uses_default_languages_when_none(self, monkeypatch):
        import shelfmark.core.search_plan as sp

        monkeypatch.setattr(sp.config, "get", _book_language_config_get(["en", "hu"]))

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

        # Title-only variants are used by some sources (e.g. Prowlarr).
        assert [v.title for v in plan.title_variants] == [
            "The Final Empire",
            "A végső birodalom",
        ]

        assert [(v.title, v.languages) for v in plan.grouped_title_variants] == [
            ("The Final Empire", ["en"]),
            ("A végső birodalom", ["hu"]),
        ]

    def test_all_language_disables_grouping(self, monkeypatch):
        import shelfmark.core.search_plan as sp

        monkeypatch.setattr(sp.config, "get", _book_language_config_get(["en"]))

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
        assert [v.title for v in plan.title_variants] == [
            "The Lightning Thief",
        ]
        assert [(v.title, v.languages) for v in plan.grouped_title_variants] == [
            ("The Lightning Thief", None),
        ]

    def test_user_default_languages_beat_the_global_default(self, monkeypatch):
        import shelfmark.core.search_plan as sp

        monkeypatch.setattr(
            sp.config,
            "get",
            _book_language_config_get(["en"], user_languages=["de", "en"]),
        )

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="The Final Empire",
            authors=["Brandon Sanderson"],
        )

        assert build_release_search_plan(book).languages == ["en"]
        assert build_release_search_plan(book, user_id=7).languages == ["de", "en"]

    def test_explicit_languages_beat_the_user_default(self, monkeypatch):
        import shelfmark.core.search_plan as sp

        monkeypatch.setattr(
            sp.config,
            "get",
            _book_language_config_get(["en"], user_languages=["de", "en"]),
        )

        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="The Final Empire",
            authors=["Brandon Sanderson"],
        )

        plan = build_release_search_plan(book, languages=["fr"], user_id=7)

        assert plan.languages == ["fr"]
