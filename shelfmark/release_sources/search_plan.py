from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

MANUAL_QUERY_MAX_LEN = 256

from shelfmark.core.config import config
from shelfmark.metadata_providers import (
    BookMetadata,
    group_languages_by_localized_title,
    build_localized_search_titles,
)


@dataclass(frozen=True)
class ReleaseSearchVariant:
    """A single search variant (title + author) associated with languages."""

    title: str
    author: str
    languages: Optional[List[str]] = None

    @property
    def query(self) -> str:
        return " ".join(part for part in [self.title, self.author] if part).strip()


@dataclass(frozen=True)
class ReleaseSearchPlan:
    """Pre-computed search inputs shared across release sources."""

    languages: Optional[List[str]]
    isbn_candidates: List[str]
    author: str
    title_variants: List[ReleaseSearchVariant]
    grouped_title_variants: List[ReleaseSearchVariant]
    manual_query: Optional[str] = None

    @property
    def primary_query(self) -> str:
        return self.title_variants[0].query if self.title_variants else ""


def _normalize_languages(languages: Optional[List[str]]) -> Optional[List[str]]:
    if not languages:
        default = config.BOOK_LANGUAGE
        if not default:
            return None
        return [str(lang).strip() for lang in default if str(lang).strip()]

    normalized: List[str] = []
    for lang in languages:
        if not lang:
            continue
        s = str(lang).strip()
        if not s:
            continue
        normalized.append(s)

    if any(lang.lower() == "all" for lang in normalized):
        return None

    return normalized or None


def _pick_search_author(book: BookMetadata) -> str:
    if book.search_author:
        return book.search_author

    if not book.authors:
        return ""

    first = book.authors[0]
    if "," in first:
        first = first.split(",")[0].strip()

    return first


def _pick_search_title(book: BookMetadata) -> str:
    return book.search_title or book.title


def build_release_search_plan(
    book: BookMetadata,
    languages: Optional[List[str]] = None,
    manual_query: Optional[str] = None,
) -> ReleaseSearchPlan:
    resolved_languages = _normalize_languages(languages)

    resolved_manual_query = None
    if manual_query:
        resolved_manual_query = manual_query.strip()[:MANUAL_QUERY_MAX_LEN] or None

    author = _pick_search_author(book)
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
        )

    isbn_candidates: List[str] = []
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

    grouped_variants: List[ReleaseSearchVariant] = [
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

    title_variants: List[ReleaseSearchVariant] = [
        ReleaseSearchVariant(title=title, author=author, languages=None)
        for title in expanded_titles
        if title
    ]

    # If no titles could be built, fall back to ISBN queries.
    if not title_variants and isbn_candidates:
        title_variants = [
            ReleaseSearchVariant(title=isbn, author="", languages=None)
            for isbn in isbn_candidates
        ]

    return ReleaseSearchPlan(
        languages=resolved_languages,
        isbn_candidates=isbn_candidates,
        author=author,
        title_variants=title_variants,
        grouped_title_variants=grouped_variants,
        manual_query=None,
    )
