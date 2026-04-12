"""Tests for automatic release selection policy helpers."""

from shelfmark.core import request_auto_selection as module
from shelfmark.core.request_auto_selection import (
    RequestAutoFallbackStrategy,
    RequestAutoSelectionPolicy,
    RequestAutoSelectionSettings,
    select_release_for_request,
    validate_request_auto_selection_settings,
)
from shelfmark.release_sources import Release, ReleaseProtocol


def _book_data() -> dict[str, str]:
    return {
        "title": "Example Book",
        "author": "Example Author",
        "provider": "openlibrary",
        "provider_id": "ol-example",
        "content_type": "ebook",
    }


def _configure_single_stage(monkeypatch, *, settings: RequestAutoSelectionSettings, releases: list[Release]) -> None:
    monkeypatch.setattr(
        module,
        "resolve_request_auto_selection_settings",
        lambda **kwargs: settings,
    )
    monkeypatch.setattr(
        module,
        "_build_search_stages",
        lambda **kwargs: [module._SearchStage(key="preferred_source", sources=("prowlarr",))],
    )
    monkeypatch.setattr(
        module,
        "_search_stage",
        lambda **kwargs: (releases, []),
    )


def test_validate_request_auto_selection_settings_rejects_unknown_content_type():
    normalized, errors = validate_request_auto_selection_settings(
        {"REQUEST_AUTO_CONTENT_TYPES": ["ebook", "comic"]}
    )

    assert normalized == {}
    assert errors == ["REQUEST_AUTO_CONTENT_TYPES must be a list of content types"]


def test_select_release_for_request_prefers_format_before_seeders_for_best_match(monkeypatch):
    _configure_single_stage(
        monkeypatch,
        settings=RequestAutoSelectionSettings(
            enabled=True,
            preferred_formats=("epub", "pdf"),
            selection_policy=RequestAutoSelectionPolicy.BEST_MATCH,
        ),
        releases=[
            Release(
                source="prowlarr",
                source_id="pdf-release",
                title="Example Book",
                format="pdf",
                seeders=200,
                content_type="ebook",
                extra={"author": "Example Author"},
            ),
            Release(
                source="prowlarr",
                source_id="epub-release",
                title="Example Book",
                format="epub",
                seeders=50,
                content_type="ebook",
                extra={"author": "Example Author"},
            ),
        ],
    )

    result = select_release_for_request(
        book_data=_book_data(),
        source_hint="prowlarr",
        content_type="ebook",
        user_id=7,
    )

    assert result.selected is not None
    assert result.selected.release.source_id == "epub-release"
    assert result.selected.stage == "preferred_source"
    assert result.selected.release_data["extra"]["auto_selection"]["policy"] == "best_match"


def test_select_release_for_request_prefers_exact_single_book_over_collection(monkeypatch):
    _configure_single_stage(
        monkeypatch,
        settings=RequestAutoSelectionSettings(
            enabled=True,
            preferred_formats=("epub",),
            selection_policy=RequestAutoSelectionPolicy.BEST_MATCH,
        ),
        releases=[
            Release(
                source="prowlarr",
                source_id="collection-release",
                title="Example Book Collection Books 1-8",
                format="epub",
                seeders=500,
                size_bytes=150 * 1024 * 1024,
                content_type="ebook",
                extra={"author": "Example Author"},
            ),
            Release(
                source="prowlarr",
                source_id="single-release",
                title="Example Book",
                format="epub",
                seeders=35,
                size_bytes=2 * 1024 * 1024,
                content_type="ebook",
                extra={"author": "Example Author"},
            ),
        ],
    )

    result = select_release_for_request(
        book_data=_book_data(),
        source_hint="prowlarr",
        content_type="ebook",
        user_id=7,
    )

    assert result.selected is not None
    assert result.selected.release.source_id == "single-release"
    assert result.selected.stage == "preferred_source"
    assert result.selected.release_data["extra"]["auto_selection"]["policy"] == "best_match"


def test_select_release_for_request_exact_title_and_author_beat_bundle_with_more_seeders(monkeypatch):
    _configure_single_stage(
        monkeypatch,
        settings=RequestAutoSelectionSettings(
            enabled=True,
            selection_policy=RequestAutoSelectionPolicy.BEST_MATCH,
        ),
        releases=[
            Release(
                source="prowlarr",
                source_id="bundle-release",
                title="Example Book Omnibus",
                format="epub",
                seeders=900,
                size_bytes=120 * 1024 * 1024,
                content_type="ebook",
                extra={"author": "Another Author"},
            ),
            Release(
                source="prowlarr",
                source_id="exact-release",
                title="Example Book",
                format="epub",
                seeders=12,
                size_bytes=3 * 1024 * 1024,
                content_type="ebook",
                extra={"author": "Example Author"},
            ),
        ],
    )

    result = select_release_for_request(
        book_data=_book_data(),
        source_hint="prowlarr",
        content_type="ebook",
        user_id=7,
    )

    assert result.selected is not None
    assert result.selected.release.source_id == "exact-release"


def test_select_release_for_request_falls_back_to_any_source_stage(monkeypatch):
    monkeypatch.setattr(
        module,
        "resolve_request_auto_selection_settings",
        lambda **kwargs: RequestAutoSelectionSettings(
            enabled=True,
            preferred_source="prowlarr",
            fallback_strategy=RequestAutoFallbackStrategy.SAME_SOURCE_THEN_ANY_SOURCE,
        ),
    )
    monkeypatch.setattr(
        module,
        "_list_enabled_sources_for_content_type",
        lambda content_type: ["prowlarr", "direct_download"],
    )

    def fake_search_stage(*, stage, **kwargs):
        if stage.key == "preferred_source":
            return [], []
        return (
            [
                Release(
                    source="direct_download",
                    source_id="fallback-release",
                    title="Fallback Example",
                    format="epub",
                    content_type="ebook",
                )
            ],
            [],
        )

    monkeypatch.setattr(module, "_search_stage", fake_search_stage)

    result = select_release_for_request(
        book_data=_book_data(),
        source_hint="*",
        content_type="ebook",
        user_id=7,
    )

    assert result.selected is not None
    assert result.selected.release.source == "direct_download"
    assert result.selected.stage == "any_source"
    assert result.searched_sources == ("prowlarr", "direct_download")


def test_select_release_for_request_most_seeders_policy_prefers_highest_seeders(monkeypatch):
    _configure_single_stage(
        monkeypatch,
        settings=RequestAutoSelectionSettings(
            enabled=True,
            selection_policy=RequestAutoSelectionPolicy.MOST_SEEDERS,
        ),
        releases=[
            Release(
                source="prowlarr",
                source_id="low-seeders",
                title="Example Book",
                format="epub",
                seeders=12,
                content_type="ebook",
                extra={"author": "Example Author"},
            ),
            Release(
                source="prowlarr",
                source_id="high-seeders",
                title="Example Book",
                format="epub",
                seeders=250,
                content_type="ebook",
                extra={"author": "Example Author"},
            ),
        ],
    )

    result = select_release_for_request(
        book_data=_book_data(),
        source_hint="prowlarr",
        content_type="ebook",
        user_id=7,
    )

    assert result.selected is not None
    assert result.selected.release.source_id == "high-seeders"


def test_select_release_for_request_best_availability_policy_prefers_more_available_release(monkeypatch):
    _configure_single_stage(
        monkeypatch,
        settings=RequestAutoSelectionSettings(
            enabled=True,
            selection_policy=RequestAutoSelectionPolicy.BEST_AVAILABILITY,
        ),
        releases=[
            Release(
                source="prowlarr",
                source_id="low-availability",
                title="Example Book",
                format="epub",
                seeders=5,
                content_type="ebook",
                extra={"author": "Example Author"},
            ),
            Release(
                source="prowlarr",
                source_id="high-availability",
                title="Example Book",
                format="epub",
                seeders=4,
                download_url="https://example.test/book.epub",
                protocol=ReleaseProtocol.HTTP,
                content_type="ebook",
                extra={"author": "Example Author"},
            ),
        ],
    )

    result = select_release_for_request(
        book_data=_book_data(),
        source_hint="prowlarr",
        content_type="ebook",
        user_id=7,
    )

    assert result.selected is not None
    assert result.selected.release.source_id == "high-availability"


def test_select_release_for_request_newest_policy_prefers_newest_release(monkeypatch):
    _configure_single_stage(
        monkeypatch,
        settings=RequestAutoSelectionSettings(
            enabled=True,
            selection_policy=RequestAutoSelectionPolicy.NEWEST,
        ),
        releases=[
            Release(
                source="prowlarr",
                source_id="older-release",
                title="Example Book",
                format="epub",
                content_type="ebook",
                extra={"author": "Example Author", "publish_date": "2020-01-01T00:00:00Z"},
            ),
            Release(
                source="prowlarr",
                source_id="newer-release",
                title="Example Book",
                format="epub",
                content_type="ebook",
                extra={"author": "Example Author", "publish_date": "2025-01-01T00:00:00Z"},
            ),
        ],
    )

    result = select_release_for_request(
        book_data=_book_data(),
        source_hint="prowlarr",
        content_type="ebook",
        user_id=7,
    )

    assert result.selected is not None
    assert result.selected.release.source_id == "newer-release"
