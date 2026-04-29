"""Parsing and search-normalization helpers for Hardcover payloads."""

import re
from contextlib import suppress
from datetime import datetime
from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.metadata_providers import BookMetadata, DisplayField

from .constants import HARDCOVER_MIN_AUTHOR_PARTS

logger = setup_logger(__name__)


def _combine_headline_description(headline: str | None, description: str | None) -> str | None:
    """Combine headline (tagline) and description into a single description."""
    if headline and description:
        return f"{headline}\n\n{description}"
    return headline or description


def _extract_cover_url(data: dict, *keys: str) -> str | None:
    """Extract cover URL from data dict, trying multiple keys.

    Handles both string URLs and dict with 'url' key.
    """
    for key in keys:
        value = data.get(key)
        if value:
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                return value.get("url")
    return None


def _extract_publish_year(data: dict) -> int | None:
    """Extract publish year from release_year or release_date fields."""
    if data.get("release_year"):
        try:
            return int(data["release_year"])
        except ValueError, TypeError:
            pass
    if data.get("release_date"):
        try:
            return int(str(data["release_date"])[:4])
        except ValueError, TypeError:
            pass
    return None


def _parse_release_date(value: Any) -> datetime | None:
    """Parse Hardcover release dates stored as YYYY-MM-DD strings."""
    if not value:
        return None

    normalized_value = str(value).strip()
    if not normalized_value:
        return None

    try:
        return datetime.fromisoformat(normalized_value[:10])
    except ValueError:
        return None


def _normalize_series_position(value: Any) -> float | None:
    """Normalize a series position to a float for sorting and grouping."""
    if value is None:
        return None

    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _normalize_hardcover_api_key(value: object) -> str:
    """Normalize Hardcover API keys, stripping copied auth-header prefixes."""
    normalized_value = normalize_optional_text(value) or ""
    return normalized_value.removeprefix("Bearer ").strip()


def _normalize_search_text(value: str) -> str:
    """Normalize free-text search input for matching and caching."""
    return " ".join(value.split()).strip()


def _unwrap_hit_document(hit: Any) -> dict[str, Any] | None:
    """Extract the document dict from a Typesense hit, or return None."""
    if not isinstance(hit, dict):
        return None
    item = hit.get("document", hit)
    return item if isinstance(item, dict) else None


def _search_tokens(value: str) -> list[str]:
    """Tokenize search text for lightweight prefix matching."""
    return re.findall(r"[a-z0-9']+", value.casefold())


def _query_matches_author_name(query: str, author_name: str) -> bool:
    """Return True when the query looks like an author-name search."""
    normalized_query = _normalize_search_text(query)
    normalized_author_name = _normalize_search_text(author_name)
    if not normalized_query or not normalized_author_name:
        return False

    query_folded = normalized_query.casefold()
    author_folded = normalized_author_name.casefold()
    if query_folded in author_folded:
        return True

    query_tokens = _search_tokens(normalized_query)
    author_tokens = _search_tokens(normalized_author_name)
    if not query_tokens or not author_tokens:
        return False

    return all(
        any(author_token.startswith(query_token) for author_token in author_tokens)
        for query_token in query_tokens
    )


def _split_part_base_title(title: str) -> str | None:
    """Extract the base title from segmented part releases like ', Part 2'."""
    normalized_title = _normalize_search_text(title)
    if not normalized_title:
        return None

    match = re.match(r"^(?P<base>.+?),\s*Part\s+\d+$", normalized_title, re.IGNORECASE)
    if not match:
        return None

    base_title = str(match.group("base") or "").strip()
    return base_title or None


def _series_allows_split_parts(series_name: str) -> bool:
    """Return True for series that intentionally organize split-part releases."""
    normalized_name = _normalize_search_text(series_name).casefold()
    if not normalized_name:
        return False

    markers = (
        "dramatized adaptation",
        "graphicaudio",
        "graphic audio",
        "(3 parts)",
        "(2 parts)",
        "(4 parts)",
    )
    return any(marker in normalized_name for marker in markers)


def _extract_typesense_hits(result: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Extract hit documents + total count from Hardcover search output."""
    root = result.get("search", result) if isinstance(result, dict) else {}
    results_obj = root.get("results", {}) if isinstance(root, dict) else {}
    if isinstance(results_obj, dict):
        hits = results_obj.get("hits", [])
        found_count = results_obj.get("found", 0)
    else:
        hits = results_obj if isinstance(results_obj, list) else []
        found_count = 0
    return hits, found_count


def _build_source_url(slug: str) -> str | None:
    """Build Hardcover source URL from book slug."""
    return f"https://hardcover.app/books/{slug}" if slug else None


def _is_probably_series_position(subtitle: str) -> bool:
    normalized = subtitle.strip().lower()

    # Common patterns: "Book One", "Book 1", "Part 2", "Volume III", etc.
    if re.match(
        r"^(book|part|volume|vol\.?|episode)\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
        normalized,
    ):
        return True

    # e.g. "A Novel", "An Epic Fantasy", etc. These add noise to indexer queries.
    if normalized in {"a novel", "a novella", "a story", "a memoir"}:
        return True

    # Descriptive subtitles like "A [Name] Novel", "An [Name] Mystery", etc.
    genre_words = (
        "novel",
        "novella",
        "story",
        "memoir",
        "tale",
        "thriller",
        "mystery",
        "romance",
        "adventure",
        "epic",
        "saga",
        "chronicle",
        "fantasy",
        "novel-in-stories",
    )
    genre_pattern = "|".join(re.escape(w) for w in genre_words)
    return bool(re.match(rf"^an?\s+.+\s+({genre_pattern})$", normalized))


def _strip_parenthetical_suffix(title: str) -> str:
    # Drop trailing qualifiers like "(Unabridged)", "(Illustrated Edition)", etc.
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()


def _simplify_author_for_search(author: str) -> str | None:
    """Return a looser author string for indexer searches.

    Primary goal: reduce mismatch between metadata providers and indexers.
    Indexers store author names inconsistently ("R.A.", "R. A.", "Salvatore, R.A.")
    so initials add noise and hurt recall.

    Heuristics:
    - Strip all initials (single or compound), keeping only full names
      e.g. "R. A. Salvatore" -> "Salvatore", "George R.R. Martin" -> "George Martin"
    - Preserve suffixes like "Jr."/"Sr."/"III" as they sometimes matter
    """
    if not author:
        return None

    normalized = " ".join(author.split()).strip()
    if not normalized:
        return None

    # Handle "Last, First ..." -> "First ... Last"
    if "," in normalized:
        parts = [p.strip() for p in normalized.split(",") if p.strip()]
        if len(parts) >= HARDCOVER_MIN_AUTHOR_PARTS:
            normalized = " ".join([*parts[1:], parts[0]]).strip()

    tokens = normalized.split(" ")
    if len(tokens) < HARDCOVER_MIN_AUTHOR_PARTS:
        return None

    keep_suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

    simplified: list[str] = []
    for idx, token in enumerate(tokens):
        t = token.strip()
        if not t:
            continue

        t_lower = t.lower()
        is_suffix = (idx == len(tokens) - 1) and (t_lower in keep_suffixes)
        if is_suffix:
            simplified.append(t)
            continue

        # Drop all initials: "R.", "R", "R.R.", "J.K.", etc.
        if re.match(r"^[A-Za-z]$|^([A-Za-z]\.)+[A-Za-z]?$", t):
            continue

        simplified.append(t)

    if not simplified:
        return None

    candidate = " ".join(simplified).strip()
    if candidate.lower() == normalized.lower():
        return None

    return candidate


def _compute_search_title(
    title: str,
    subtitle: str | None,
    *,
    series_name: str | None = None,
) -> str | None:
    """Compute a provider-specific, *looser* title for indexer searching.

    Goal: produce a string that maximizes recall in downstream sources (Prowlarr,
    IRC bots, etc.). Being too detailed is counterproductive.

    Hardcover often stores titles in a "Series: Book Title" format and places the
    standalone book title in `subtitle`. When this appears to be the case, prefer
    the subtitle (unless it looks like a series position or other noise).

    Additional heuristics:
    - If Hardcover prefixes the series in the title, remove it.
    - Drop trailing parenthetical qualifiers.
    """
    if not title:
        return None

    original_title = " ".join(title.split()).strip()

    normalized_title = _strip_parenthetical_suffix(original_title)

    normalized_subtitle = " ".join(subtitle.split()).strip() if subtitle else ""
    normalized_subtitle = (
        _strip_parenthetical_suffix(normalized_subtitle) if normalized_subtitle else ""
    )

    if normalized_subtitle and normalized_subtitle.lower() == normalized_title.lower():
        normalized_subtitle = ""

    # If subtitle is noise, strip it from the title and use just the prefix.
    if normalized_subtitle and _is_probably_series_position(normalized_subtitle):
        match = re.match(r"^(.+?)\s*:\s*(.+)$", normalized_title)
        if match:
            suffix = _strip_parenthetical_suffix(match.group(2).strip())
            if (
                normalized_subtitle.lower() == suffix.lower()
                or normalized_subtitle.lower() in suffix.lower()
            ):
                return None

    # Prefer subtitle when it looks like the real title.
    if normalized_subtitle and not _is_probably_series_position(normalized_subtitle):
        match = re.match(r"^(.+?)\s*:\s*(.+)$", normalized_title)
        if match:
            prefix = match.group(1).strip()
            suffix = _strip_parenthetical_suffix(match.group(2).strip())

            prefix_words = len(prefix.split()) if prefix else 0
            subtitle_words = len(normalized_subtitle.split())

            series_normalized = " ".join(series_name.split()).strip() if series_name else ""
            if series_normalized and prefix.lower() == series_normalized.lower():
                return normalized_subtitle

            # If the subtitle is much longer than the prefix, treat it as a descriptive subtitle.
            if prefix and subtitle_words >= (prefix_words + 4):
                return prefix

            # Otherwise assume "Series: Book Title" and prefer the subtitle.
            if (
                normalized_subtitle.lower() == suffix.lower()
                or normalized_subtitle.lower() in suffix.lower()
            ):
                return normalized_subtitle

        # Fallback: if title contains the subtitle, this is likely "Series: Subtitle".
        if normalized_subtitle.lower() in normalized_title.lower():
            return normalized_subtitle

    # If we know the series name (from full book fetch), strip it.
    if series_name:
        series_normalized = " ".join(series_name.split()).strip()
        if series_normalized:
            # Common Hardcover format: "Series: Book Title".
            prefix = f"{series_normalized}:"
            if normalized_title.lower().startswith(prefix.lower()):
                candidate = normalized_title[len(prefix) :].strip()
                candidate = _strip_parenthetical_suffix(candidate)
                if candidate and candidate.lower() != normalized_title.lower():
                    return candidate

    # Last resort: return a cleaned version of the title if we removed noise.
    if normalized_title and normalized_title.lower() != original_title.lower():
        return normalized_title

    return None


class HardcoverParsingMixin:
    def _parse_search_result(self, item: dict) -> BookMetadata | None:
        """Parse a search result item into BookMetadata."""
        try:
            book_id = item.get("id") or item.get("document", {}).get("id")
            title = item.get("title") or item.get("document", {}).get("title")

            if not book_id or not title:
                return None

            # Extract authors - use contribution_types to filter author_names if available
            authors = []

            author_names = item.get("author_names", [])
            if isinstance(author_names, str):
                author_names = [author_names]

            contribution_types = item.get("contribution_types", [])

            # If we have parallel arrays, filter to only "Author" contributions
            if contribution_types and len(contribution_types) == len(author_names):
                for name, contrib_type in zip(author_names, contribution_types, strict=True):
                    if contrib_type == "Author":
                        authors.append(name)
            elif author_names:
                # No contribution_types or length mismatch - use all names as fallback
                authors = author_names

            # Normalize whitespace in author names (some API data has multiple spaces)
            authors = [" ".join(name.split()) for name in authors]

            search_author = _simplify_author_for_search(authors[0]) if authors else None

            cover_url = _extract_cover_url(item, "image")
            publish_year = _extract_publish_year(item)
            source_url = _build_source_url(item.get("slug", ""))

            # Build display fields from Hardcover-specific data
            display_fields = []

            # Rating (e.g., "4.5 (3,764)")
            rating = item.get("rating")
            ratings_count = item.get("ratings_count")
            if rating is not None:
                rating_str = f"{rating:.1f}"
                if ratings_count:
                    rating_str += f" ({ratings_count:,})"
                display_fields.append(DisplayField(label="Rating", value=rating_str, icon="star"))

            # Readers (users who have this book)
            users_count = item.get("users_count")
            if users_count:
                display_fields.append(
                    DisplayField(label="Readers", value=f"{users_count:,}", icon="users")
                )

            # Combine headline and description if both present
            headline = item.get("headline")
            description = item.get("description")
            full_description = _combine_headline_description(headline, description)

            # Extract subtitle if available in search results
            subtitle = item.get("subtitle")

            return BookMetadata(
                provider="hardcover",
                provider_id=str(book_id),
                title=title,
                subtitle=subtitle,
                search_title=_compute_search_title(title, subtitle),
                search_author=search_author,
                provider_display_name="Hardcover",
                authors=authors,
                cover_url=cover_url,
                description=full_description,
                publish_year=publish_year,
                source_url=source_url,
                display_fields=display_fields,
            )

        except (AttributeError, KeyError, TypeError, ValueError) as e:
            logger.debug("Failed to parse Hardcover search result: %s", e)
            return None

    def _parse_book(self, book: dict) -> BookMetadata:
        """Parse a book object into BookMetadata."""
        title = str(book.get("title") or "")
        subtitle = book.get("subtitle")

        # Extract authors - try contributions first (filtered), fall back to cached_contributors
        authors = []
        contributions = book.get("contributions") or []
        cached_contributors = book.get("cached_contributors") or []

        # Try contributions first (filtered to "Author" role only - cleaner data)
        for contrib in contributions:
            author = contrib.get("author", {})
            if author and author.get("name"):
                authors.append(author["name"])

        # Fallback to cached_contributors if no authors found
        if not authors:
            for contrib in cached_contributors:
                if isinstance(contrib, dict):
                    # Handle nested structure: {"author": {"name": "..."}, "contribution": ...}
                    if contrib.get("author", {}).get("name"):
                        authors.append(contrib["author"]["name"])
                    # Handle flat structure: {"name": "..."}
                    elif contrib.get("name"):
                        authors.append(contrib["name"])
                elif isinstance(contrib, str):
                    authors.append(contrib)

        # Normalize whitespace in author names (some API data has multiple spaces)
        authors = [" ".join(name.split()) for name in authors]

        search_author = _simplify_author_for_search(authors[0]) if authors else None

        cover_url = _extract_cover_url(book, "cached_image", "image")
        publish_year = _extract_publish_year(book)

        # Extract genres from cached_tags
        genres = []
        for tag in book.get("cached_tags", []):
            if isinstance(tag, dict) and tag.get("tag"):
                genres.append(tag["tag"])
            elif isinstance(tag, str):
                genres.append(tag)

        # Get ISBN from direct fields, default_physical_edition, or editions
        isbn_10 = book.get("isbn_10")
        isbn_13 = book.get("isbn_13")

        if not isbn_10 and not isbn_13:
            # Try default_physical_edition first
            edition = book.get("default_physical_edition")
            if edition:
                isbn_10 = edition.get("isbn_10")
                isbn_13 = edition.get("isbn_13")

            # Fallback to editions array
            if not isbn_10 and not isbn_13 and book.get("editions"):
                for ed in book["editions"]:
                    if not isbn_10 and ed.get("isbn_10"):
                        isbn_10 = ed["isbn_10"]
                    if not isbn_13 and ed.get("isbn_13"):
                        isbn_13 = ed["isbn_13"]
                    if isbn_10 and isbn_13:
                        break

        source_url = _build_source_url(book.get("slug", ""))

        # Combine headline and description if both present
        headline = book.get("headline")
        description = book.get("description")
        full_description = _combine_headline_description(headline, description)

        # Extract series info from featured_book_series
        series_id = None
        series_name = None
        series_position = None
        series_count = None
        featured_series = book.get("featured_book_series")
        if featured_series:
            series_position = featured_series.get("position")
            series_data = featured_series.get("series")
            if series_data:
                if series_data.get("id") is not None:
                    series_id = str(series_data.get("id"))
                series_name = series_data.get("name")
                series_count = series_data.get("primary_books_count")

        # Extract titles by language from editions
        # This allows searching with localized titles when language filter is active
        titles_by_language: dict[str, str] = {}
        editions = book.get("editions", [])
        for edition in editions:
            edition_title = edition.get("title")
            lang_data = edition.get("language")
            if edition_title and lang_data:
                # Store by various language identifiers for flexible matching
                # Language name (e.g., "German", "English")
                lang_name = lang_data.get("language")
                # 2-letter code (e.g., "de", "en")
                code2 = lang_data.get("code2")
                # 3-letter code (e.g., "deu", "eng")
                code3 = lang_data.get("code3")

                # Store with all available keys (first title wins for each language)
                if lang_name and lang_name not in titles_by_language:
                    titles_by_language[lang_name] = edition_title
                if code2 and code2 not in titles_by_language:
                    titles_by_language[code2] = edition_title
                if code3 and code3 not in titles_by_language:
                    titles_by_language[code3] = edition_title

        # Build display fields from Hardcover-specific metrics
        display_fields: list[DisplayField] = []

        rating = book.get("rating")
        ratings_count = book.get("ratings_count")
        if rating is not None:
            try:
                rating_str = f"{float(rating):.1f}"
            except TypeError, ValueError:
                rating_str = str(rating)

            if ratings_count:
                with suppress(TypeError, ValueError):
                    rating_str += f" ({int(ratings_count):,})"

            display_fields.append(DisplayField(label="Rating", value=rating_str, icon="star"))

        users_count = book.get("users_count")
        if users_count:
            try:
                readers_value = f"{int(users_count):,}"
            except TypeError, ValueError:
                readers_value = str(users_count)
            display_fields.append(DisplayField(label="Readers", value=readers_value, icon="users"))

        return BookMetadata(
            provider="hardcover",
            provider_id=str(book["id"]),
            title=title,
            subtitle=subtitle,
            search_title=_compute_search_title(title, subtitle, series_name=series_name),
            search_author=search_author,
            provider_display_name="Hardcover",
            authors=authors,
            isbn_10=isbn_10,
            isbn_13=isbn_13,
            cover_url=cover_url,
            description=full_description,
            publish_year=publish_year,
            genres=genres,
            source_url=source_url,
            series_id=series_id,
            series_name=series_name,
            series_position=series_position,
            series_count=series_count,
            titles_by_language=titles_by_language,
            display_fields=display_fields,
        )
