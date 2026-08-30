"""A release search uses one author, not every contributor the book lists.

`bookTransformers.ts` joins a book's authors with ", " for display, and the release modal
sends that display string back as the `author` request parameter; several providers set
`search_author` from equally joined text. `_pick_search_author` returned it verbatim while
the authors[] fallback beside it deliberately narrowed to the first name, so a book with
translators was searched for as

    Blindness Jose Saramago, Giovanni Pontiero, <persian translator>

which matches nothing on Anna's Archive. The bypass succeeds, the search returns empty,
and the user is told the book has no releases. Reported on issue #1252.
"""

from shelfmark.core.search_plan import build_release_search_plan, first_author
from shelfmark.metadata_providers import BookMetadata

# The exact string from the report, as the frontend would join it.
JOINED = "José Saramago, Giovanni Pontiero, زهره افتخاری"


def _book(**kwargs) -> BookMetadata:
    base = {
        "provider": "manual",
        "provider_id": "x",
        "title": "Blindness",
        "search_title": "Blindness",
    }
    return BookMetadata(**{**base, **kwargs})


def _queries(book: BookMetadata) -> list[str]:
    plan = build_release_search_plan(book, languages=["en"])
    return [f"{v.title} {plan.author}".strip() for v in plan.grouped_title_variants]


def test_search_author_is_narrowed_to_the_first_author():
    book = _book(search_author=JOINED, authors=[a.strip() for a in JOINED.split(",")])

    assert _queries(book) == ["Blindness José Saramago"]


def test_the_authors_fallback_still_narrows():
    """Unchanged behaviour, kept under test so the two paths cannot drift apart again."""
    book = _book(authors=[JOINED])

    assert _queries(book) == ["Blindness José Saramago"]


def test_a_single_author_is_left_alone():
    book = _book(search_author="José Saramago")

    assert _queries(book) == ["Blindness José Saramago"]


def test_a_book_with_no_author_still_searches_by_title():
    book = _book(authors=[])

    assert _queries(book) == ["Blindness"]


def test_last_first_collapses_to_the_surname():
    """Still a usable search term, and what the fallback has always done."""
    assert first_author("Saramago, José") == "Saramago"


def test_first_author_trims_and_tolerates_odd_input():
    assert first_author("  José Saramago  ") == "José Saramago"
    assert first_author("") == ""
    assert first_author(",") == ""
    assert first_author("José Saramago,") == "José Saramago"


def test_manual_query_is_untouched():
    """A manual query is the user's own words; narrowing it would rewrite their search."""
    book = _book(search_author=JOINED)
    plan = build_release_search_plan(book, manual_query=JOINED)

    assert plan.manual_query == JOINED
    assert plan.author == ""


def test_irc_query_uses_one_author_too():
    """The IRC source builds its own query and had the same verbatim preference."""
    from shelfmark.release_sources.irc.source import IRCReleaseSource

    book = _book(search_author=JOINED, authors=[a.strip() for a in JOINED.split(",")])

    assert IRCReleaseSource()._build_query(book) == "Blindness José Saramago"
