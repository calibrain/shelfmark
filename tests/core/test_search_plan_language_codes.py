"""Language filters must reach the sources as ISO codes.

Issue #1276: a debug bundle showed `BOOK_LANGUAGE=english` arriving at Anna's Archive
verbatim as `&lang=english`. AA matches that parameter against ISO codes, so it is not a
loose spelling of `lang=en` - it is a facet value AA does not have, and it empties every
search. In that bundle a successful solve of an ISBN search for Philosopher's Stone
returned zero hits, while a warm-up query carrying no language filter hit the same host
in the same minute and returned 50.

Only the per-user override was normalised (config.users_settings.validate). The global
default - which is what the env var feeds - was passed through with nothing but a
.strip(), so anyone carrying `BOOK_LANGUAGE=english` from the old docs had every search
silently filtered to nothing, with no error anywhere.
"""

import logging

import pytest

import shelfmark.core.search_plan as sp
from shelfmark.metadata_providers import BookMetadata


def _config_get(global_languages):
    def _get(key: str, default: object = None, user_id: int | None = None) -> object:
        return global_languages if key == "BOOK_LANGUAGE" else default

    return _get


def _book() -> BookMetadata:
    return BookMetadata(
        provider="hardcover",
        provider_id="123",
        title="Harry Potter and the Philosopher's Stone",
        search_title="Harry Potter and the Philosopher's Stone",
        search_author="J.K. Rowling",
        authors=["J.K. Rowling"],
    )


@pytest.fixture
def plan_logs():
    """Collect search_plan log records.

    setup_logger builds its loggers outside the standard hierarchy, so caplog's root
    handler never sees them, and Logger.setLevel cannot clear their is-enabled cache.
    """
    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    handler = _Capture()
    sp.logger.addHandler(handler)
    previous = sp.logger.level
    sp.logger.setLevel(logging.DEBUG)
    sp.logger._cache.clear()
    try:
        yield messages
    finally:
        sp.logger.removeHandler(handler)
        sp.logger.setLevel(previous)


# --------------------------------------------------------------------------- #
# The global default (what BOOK_LANGUAGE feeds)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (["english"], ["en"]),  # the spelling from the old docs - the reported bug
        ("english", ["en"]),  # env vars arrive as a bare string
        (["English"], ["en"]),
        (["eng"], ["en"]),  # ISO 639-2
        (["en"], ["en"]),  # already a code, unchanged
        (["english", "german"], ["en", "de"]),
        (["english", "en", "English"], ["en"]),  # collapses to one code
    ],
)
def test_default_languages_reach_the_plan_as_iso_codes(monkeypatch, configured, expected):
    monkeypatch.setattr(sp.config, "get", _config_get(configured))

    plan = sp.build_release_search_plan(_book(), languages=None)

    assert plan.languages == expected


def test_unrecognised_default_searches_unfiltered_rather_than_empty(monkeypatch, plan_logs):
    """Dropping the filter is the safe failure: a warned-about unfiltered search beats
    telling the user a book AA is full of does not exist."""
    monkeypatch.setattr(sp.config, "get", _config_get(["klingon"]))

    plan = sp.build_release_search_plan(_book(), languages=None)

    assert plan.languages is None
    assert any("klingon" in m and "BOOK_LANGUAGE" in m for m in plan_logs), plan_logs


def test_partly_unrecognised_default_keeps_what_resolved(monkeypatch, plan_logs):
    monkeypatch.setattr(sp.config, "get", _config_get(["english", "klingon"]))

    plan = sp.build_release_search_plan(_book(), languages=None)

    assert plan.languages == ["en"]
    assert any("klingon" in m for m in plan_logs)


def test_a_clean_default_logs_no_warning(monkeypatch, plan_logs):
    monkeypatch.setattr(sp.config, "get", _config_get(["en", "de"]))

    sp.build_release_search_plan(_book(), languages=None)

    assert not [m for m in plan_logs if "Ignoring unrecognised" in m]


# --------------------------------------------------------------------------- #
# Explicit request languages go through the same door
# --------------------------------------------------------------------------- #
def test_explicit_languages_are_normalised_too(monkeypatch):
    """The request branch had the same bare .strip(), so an API client could reproduce
    the bug even with BOOK_LANGUAGE set correctly."""
    monkeypatch.setattr(sp.config, "get", _config_get(["en"]))

    plan = sp.build_release_search_plan(_book(), languages=["english", "German"])

    assert plan.languages == ["en", "de"]


def test_all_still_means_no_language_filter(monkeypatch):
    monkeypatch.setattr(sp.config, "get", _config_get(["en"]))

    assert sp.build_release_search_plan(_book(), languages=["all"]).languages is None


def test_blank_entries_are_skipped(monkeypatch):
    monkeypatch.setattr(sp.config, "get", _config_get(["en"]))

    plan = sp.build_release_search_plan(_book(), languages=["english", "", "  ", None])

    assert plan.languages == ["en"]


def test_no_configured_default_leaves_the_filter_off(monkeypatch):
    monkeypatch.setattr(sp.config, "get", _config_get(None))

    assert sp.build_release_search_plan(_book(), languages=None).languages is None
