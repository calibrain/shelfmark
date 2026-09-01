"""Helpers for building release search plans from metadata and user input."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.metadata_providers import (
    BookMetadata,
    build_localized_search_titles,
    group_languages_by_localized_title,
)

if TYPE_CHECKING:
    from shelfmark.core.models import SearchFilters

logger = setup_logger(__name__)

MANUAL_QUERY_MAX_LEN = 256


@dataclass(frozen=True)
class ReleaseSearchVariant:
    """A single search variant (title + author) associated with languages."""

    title: str
    author: str
    languages: list[str] | None = None

    @property
    def query(self) -> str:
        """Return the combined title-and-author query for this variant."""
        return " ".join(part for part in [self.title, self.author] if part).strip()


@dataclass(frozen=True)
class ReleaseSearchPlan:
    """Pre-computed search inputs shared across release sources."""

    languages: list[str] | None
    isbn_candidates: list[str]
    author: str
    title_variants: list[ReleaseSearchVariant]
    grouped_title_variants: list[ReleaseSearchVariant]
    manual_query: str | None = None
    indexers: list[str] | None = None  # Indexer names for Prowlarr (overrides settings)
    source_filters: SearchFilters | None = None

    @property
    def primary_query(self) -> str:
        """Return the first expanded title query, if one exists."""
        return self.title_variants[0].query if self.title_variants else ""


def _to_language_codes(values: Iterable[object], *, source: str) -> list[str] | None:
    """Resolve any spelling of a language to the ISO code the sources expect.

    Anna's Archive matches `lang=` against ISO codes: `lang=english` is not a loose
    spelling of `lang=en`, it is a facet value AA does not have, and it filters every
    search down to nothing. Only the *per-user* override was normalised
    (config.users_settings.validate), so a global BOOK_LANGUAGE=english - the spelling
    the old docs used - reached the query verbatim and silently emptied every search
    with no error anywhere. See issue #1276.

    An entry that resolves to nothing is dropped with a warning rather than passed
    through: searching unfiltered and saying so beats reporting "no results" for a book
    the source is full of.
    """
    from shelfmark.core.languages import normalize_language

    codes: list[str] = []
    unresolved: list[str] = []
    for value in values:
        text = str(value).strip() if value is not None else ""
        if not text:
            continue
        if text.lower() == "all":
            # An explicit "search every language", not a language.
            return None
        code = normalize_language(text)
        if code is None:
            unresolved.append(text)
            continue
        if code not in codes:
            codes.append(code)

    if unresolved:
        logger.warning(
            "Ignoring unrecognised language(s) in %s: %s. Use an ISO code such as 'en', "
            "a three-letter code, or an English name like 'English'.",
            source,
            ", ".join(unresolved),
        )

    return codes or None


def _normalize_languages(languages: list[str] | None, user_id: int | None) -> list[str] | None:
    if not languages:
        default = config.get("BOOK_LANGUAGE", None, user_id=user_id)
        if isinstance(default, str):
            default_values: list[object] = [default]
        elif isinstance(default, Iterable) and not isinstance(default, (bytes, bytearray, dict)):
            default_values = list(default)
        else:
            return None
        return _to_language_codes(default_values, source="BOOK_LANGUAGE")

    return _to_language_codes(languages, source="the search request")


<<<<<<< HEAD
def first_author(value: str) -> str:
    """The first name in a possibly comma-joined author string.

    Both ends of the app hand us every contributor in one string. The frontend joins
    `authors` with ", " for display (`bookTransformers.ts`) and that display string comes
    straight back as the `author` request parameter, while several providers set
    `search_author` from the same joined text. Searching a release source for
    "Blindness Jose Saramago, Giovanni Pontiero, ..." - the author plus two translators -
    matches nothing, and the user is told the book has no releases at all.

    A "Last, First" author collapses to the surname, which is still a usable search term
    and is what the authors[] fallback has always done with the same input. See #1252.
    """
    first, _, _ = value.partition(",")
    return first.strip()


def pick_search_author(book: BookMetadata) -> str:
    """The one author a release query should carry, from whichever field holds one.

    Every release source that builds its own query wants exactly this, so it lives here
    rather than being re-derived per source - the two branches below drifted apart once
    already (#1252) and the IRC source carried a third copy of the same preference.

    #1290 fixed the same report by merging the two branches and trimming whichever one
    won; this keeps that outcome ("Blindness Jose Saramago" from either field, measured
    there at 0 releases before and 49 after) and adds the empty-narrowing fallback, so a
    credit list that merely starts with a blank entry does not fall out to title-only.
    """
    # Narrowing can come back empty - the joined string starts with a comma because the
    # first contributor was blank, and `authors.join(', ')` does not drop the empty entry.
    # Falling through to authors[] then still finds a usable name; returning "" would
    # search by title alone and lose the author we were holding all along.
    if book.search_author:
        narrowed = first_author(book.search_author)
        if narrowed:
            return narrowed

    # A bare string here would otherwise be iterated one character at a time; the IRC
    # source guarded against exactly that before it shared this helper.
    authors = book.authors if isinstance(book.authors, list) else [book.authors or ""]
    for author in authors:
        narrowed = first_author(author or "")
        if narrowed:
            return narrowed

    return ""
=======
def _pick_search_author(book: BookMetadata) -> str:
    author = book.search_author or (book.authors[0] if book.authors else "")
    if not author:
        return ""

    # `search_author` can arrive as the display string for the whole credit list
    # ("Author, Translator, Narrator"), which Anna's Archive answers with nothing at
    # all. Trim it to the first name, which is what the authors list already gets.
    if "," in author:
        author = author.split(",")[0].strip()

    return author
>>>>>>> main


def _pick_search_title(book: BookMetadata) -> str:
    return book.search_title or book.title


def build_release_search_plan(
    book: BookMetadata,
    languages: list[str] | None = None,
    manual_query: str | None = None,
    indexers: list[str] | None = None,
    source_filters: SearchFilters | None = None,
    user_id: int | None = None,
) -> ReleaseSearchPlan:
    """Build normalized search variants shared across release sources.

    ``user_id`` picks up that user's default languages when the caller does not
    filter explicitly, so a search started without a language filter uses the
    reader's own default rather than the instance-wide one.
    """
    resolved_languages = _normalize_languages(languages, user_id)

    resolved_manual_query = None
    if manual_query:
        resolved_manual_query = manual_query.strip()[:MANUAL_QUERY_MAX_LEN] or None

    author = pick_search_author(book)
    base_title = _pick_search_title(book)

    if resolved_manual_query:
        # Manual override: use the raw query as-is (no language/title expansion).
        variant = ReleaseSearchVariant(title=resolved_manual_query, author="", languages=None)
        return ReleaseSearchPlan(
            languages=resolved_languages,
            isbn_candidates=[],
            author="",
            title_variants=[variant],
            grouped_title_variants=[variant],
            manual_query=resolved_manual_query,
            indexers=indexers,
            source_filters=source_filters,
        )

    isbn_candidates: list[str] = []
    if book.isbn_13:
        isbn_candidates.append(book.isbn_13)
    if book.isbn_10 and book.isbn_10 not in isbn_candidates:
        isbn_candidates.append(book.isbn_10)

    titles_by_language = book.titles_by_language or None
    if book.search_title and titles_by_language:
        titles_by_language = {
            k: v
            for k, v in titles_by_language.items()
            if str(k).strip().lower() not in {"en", "eng", "english"}
        }

    grouped = group_languages_by_localized_title(
        base_title=base_title,
        languages=resolved_languages,
        titles_by_language=titles_by_language,
    )

    grouped_variants: list[ReleaseSearchVariant] = [
        ReleaseSearchVariant(title=title, author=author, languages=langs)
        for title, langs in grouped
        if title
    ]

    expanded_titles = build_localized_search_titles(
        base_title=base_title,
        languages=resolved_languages,
        titles_by_language=titles_by_language,
        excluded_languages={"en", "eng", "english"},
    )

    title_variants: list[ReleaseSearchVariant] = [
        ReleaseSearchVariant(title=title, author=author, languages=None)
        for title in expanded_titles
        if title
    ]

    # If no titles could be built, fall back to ISBN queries.
    if not title_variants and isbn_candidates:
        title_variants = [
            ReleaseSearchVariant(title=isbn, author="", languages=None) for isbn in isbn_candidates
        ]

    return ReleaseSearchPlan(
        languages=resolved_languages,
        isbn_candidates=isbn_candidates,
        author=author,
        title_variants=title_variants,
        grouped_title_variants=grouped_variants,
        manual_query=None,
        indexers=indexers,
        source_filters=source_filters,
    )
