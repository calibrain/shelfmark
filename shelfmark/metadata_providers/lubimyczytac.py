"""Lubimyczytać.pl metadata provider.

The site does not expose a public metadata API, so this provider reads its
search and book pages. Selectors intentionally include schema.org metadata
where available and HTML fallbacks for edition-specific details.
"""

import json
import re
import unicodedata
from typing import Any, ClassVar
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Tag

from shelfmark.core.cache import cacheable
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    SettingsField,
    register_settings,
)
from shelfmark.download.network import get_ssl_verify
from shelfmark.metadata_providers import (
    BookMetadata,
    DisplayField,
    MetadataProvider,
    MetadataSearchOptions,
    SearchField,
    SearchType,
    SortOrder,
    TextSearchField,
    register_provider,
)
from shelfmark.metadata_providers.openlibrary import RateLimiter

logger = setup_logger(__name__)

LUBIMYCZYTAC_BASE_URL = "https://lubimyczytac.pl"
LUBIMYCZYTAC_SEARCH_URL = f"{LUBIMYCZYTAC_BASE_URL}/szukaj/ksiazki?"
LUBIMYCZYTAC_BOOK_URL = f"{LUBIMYCZYTAC_BASE_URL}/ksiazka/"
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept-Language": "pl,en;q=0.7",
}

_ISBN_RE = re.compile(r"(?<!\d)(?:\d[ -]?){9}[\dXx](?!\d)|(?<!\d)(?:\d[ -]?){12}\d(?!\d)")
_LANGUAGE_MAP = {
    "polski": "pl",
    "polish": "pl",
    "angielski": "en",
    "english": "en",
    "niemiecki": "de",
    "deutsch": "de",
    "francuski": "fr",
    "français": "fr",
    "hiszpański": "es",
    "włoski": "it",
    "rosyjski": "ru",
    "czeski": "cs",
}
_rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").replace("\u200b", "").replace("\ufeff", "").split())


def _normalize_search_text(value: str | None) -> str:
    """Normalize text for comparing book titles and author names."""
    text = unicodedata.normalize("NFKD", _clean_text(value).casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", text))



def _split_title_author(query: str) -> tuple[str, str]:
    """Split a general query into title and author when recognizable."""
    cleaned = _clean_text(query)
    parts = re.split(r"\s*[–—-]\s*", cleaned, maxsplit=1)
    if len(parts) == 2 and len(parts[1].split()) >= 2:
        return parts[0].strip().rstrip("–—- "), parts[1].strip()

    words = cleaned.split()
    if len(words) >= 4 and all(word[:1].isupper() for word in words[-2:]):
        return " ".join(words[:-2]), " ".join(words[-2:])

    if len(words) >= 3 and all(word[:1].isupper() for word in words[:2]):
        return " ".join(words[2:]), " ".join(words[:2])

    return cleaned, ""

def _alternative_search_phrase(query: str) -> str | None:
    cleaned = _clean_text(query)
    parts = re.split(r"\s*[–—-]\s*", cleaned, maxsplit=1)
    if len(parts) == 2:
        title, author = parts
        author_words = author.split()
        if len(author_words) >= 2:
            return " ".join([title, author_words[-1]]).strip()
        return None

    words = cleaned.split()
    if len(words) < 3 or not all(word[:1].isupper() for word in words[:2]):
        return None
    return " ".join([words[1], *words[2:]])


def _tokens_match(query: str, value: str) -> bool:
    query_tokens = _normalize_search_text(query).split()
    value_tokens = _normalize_search_text(value).split()
    return bool(query_tokens) and all(token in value_tokens for token in query_tokens)


def _is_exact_book_match(book: BookMetadata, title: str, author: str) -> bool:
    title_matches = _normalize_search_text(title) == _normalize_search_text(
        book.search_title or book.title
    )
    author_matches = not author or any(
        _normalize_search_text(author) == _normalize_search_text(name)
        for name in (book.authors or [])
    )
    return title_matches and author_matches


def _absolute_url(value: str | None) -> str | None:
    if not value:
        return None
    url = urljoin(LUBIMYCZYTAC_BASE_URL + "/", value.strip())
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host == "lubimyczytac.pl" or host.endswith(".lubimyczytac.pl")
    ):
        return None
    return url


def _extract_cover(soup: BeautifulSoup, data: dict[str, Any]) -> str | None:
    candidates = []
    for value in (soup.select_one("a#js-lightboxCover"), soup.select_one(".book-cover__link"), data.get("image"), soup.select_one("meta[property=\"og:image\"]")):
        if isinstance(value, str):
            raw = value
        elif value:
            raw = value.get("href") or value.get("content") or ""
        else:
            raw = ""
        url = _absolute_url(str(raw))
        if url and url not in candidates:
            candidates.append(url)
    return candidates[0] if candidates else None


def _book_id(value: str | None) -> str:
    match = re.search(r"/ksiazka/(\d+)", value or "")
    return match.group(1) if match else ""


def _valid_isbn(value: str | None) -> str | None:
    normalized = re.sub(r"[\s-]", "", value or "").upper()

    if re.fullmatch(r"\d{13}", normalized):
        total = sum(
            int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(normalized[:12])
        )
        checksum = (10 - total % 10) % 10
        return normalized if checksum == int(normalized[-1]) else None

    if re.fullmatch(r"\d{9}[\dX]", normalized):
        total = sum(
            int(digit) * weight
            for digit, weight in zip(normalized[:9], range(10, 1, -1), strict=True)
        )
        check_digit = 10 if normalized[-1] == "X" else int(normalized[-1])
        return normalized if (total + check_digit) % 11 == 0 else None

    return None


def _json_ld_books(soup: BeautifulSoup) -> list[dict[str, Any]]:
    books = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, ValueError):
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "Book":
                books.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                books.extend(
                    node for node in graph if isinstance(node, dict) and node.get("@type") == "Book"
                )
    return books


def _names(value: object) -> list[str]:
    if not value:
        return []
    output = []
    for item in value if isinstance(value, list) else [value]:
        name = item.get("name", "") if isinstance(item, dict) else str(item)
        name = _clean_text(name)
        if name and name not in output:
            output.append(name)
    return output


@register_provider("lubimyczytac")
class LubimyczytacProvider(MetadataProvider):
    """Polish book catalog provider using Lubimyczytać.pl pages."""

    name = "lubimyczytac"
    display_name = "Lubimyczytać.pl"
    requires_auth = False
    supported_sorts: ClassVar[tuple[SortOrder, ...]] = (SortOrder.RELEVANCE,)
    search_fields: ClassVar[tuple[SearchField, ...]] = (
        TextSearchField(key="title", label="Title", description="Search by book title"),
        TextSearchField(key="author", label="Author", description="Search by author name"),
    )

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(REQUEST_HEADERS)

    def is_available(self) -> bool:
        return True

    def _fetch(self, url: str, timeout: int = 15) -> str | None:
        _rate_limiter.wait_if_needed()
        try:
            response = self.session.get(
                url, timeout=timeout, verify=get_ssl_verify(LUBIMYCZYTAC_BASE_URL)
            )
            response.raise_for_status()
        except requests.Timeout:
            logger.warning("Lubimyczytać.pl request timed out: %s", url)
            return None
        except requests.RequestException:
            logger.exception("Lubimyczytać.pl request failed: %s", url)
            return None
        return response.text

    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
        if options.search_type == SearchType.ISBN:
            book = self.search_by_isbn(options.query)
            return [book] if book else []
        title = _clean_text(options.fields.get("title"))
        author = _clean_text(options.fields.get("author"))
        query = _clean_text(options.query)
        if options.search_type == SearchType.AUTHOR and not author:
            author, query = query, ""
        if not title and not author and options.search_type == SearchType.GENERAL:
            title, author = _split_title_author(query)
        phrase = title or query or author
        if not phrase:
            return []
        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        cache_key = (
            f"{phrase}:{options.search_type.value}:{options.page}:{options.limit}:{fields_key}"
        )
        books = self._search_cached(cache_key, phrase, author, options.page, options.limit) or []
        if (
            not title
            or not author
            or any(_is_exact_book_match(book, title, author) for book in books)
        ):
            return books[: options.limit]
        alternative = _alternative_search_phrase(query)
        if not alternative or alternative == phrase:
            return books[: options.limit]
        alternative_key = f"{cache_key}:alternative:{alternative}"
        extra = (
            self._search_cached(alternative_key, alternative, author, options.page, options.limit)
            or []
        )
        merged: list[BookMetadata] = []
        seen: set[str] = set()
        for book in [*books, *extra]:
            if book.provider_id not in seen:
                seen.add(book.provider_id)
                merged.append(book)
            if len(merged) >= options.limit:
                break
        return merged

    @cacheable(
        ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="lubimyczytac:search"
    )
    def _search_cached(
        self, cache_key: str, phrase: str, author: str, page: int, limit: int
    ) -> list[BookMetadata] | None:
        params = {"phrase": phrase}
        if page > 1:
            params["page"] = str(page)
        html = self._fetch(LUBIMYCZYTAC_SEARCH_URL + urlencode(params))
        if html is None:
            return None
        soup = BeautifulSoup(html, "html.parser")
        books: list[BookMetadata] = []
        seen: set[str] = set()
        for card in soup.select(".book-card--l"):
            if card.find_parent(class_=re.compile(r"promoted-offers")):
                continue

            title_link = card.select_one(".book-card__title")
            if title_link is None:
                continue

            href = str(title_link.get("href") or "")
            raw_id = str(card.get("data-book-id") or "").strip()
            book_id = raw_id if raw_id.isdigit() else _book_id(href)
            title = _clean_text(title_link.get_text(" "))

            if not book_id or not title or book_id in seen or not href:
                continue

            card_text = _clean_text(card.get_text(" "))
            authors = self._parse_authors(card, {})

            if author and authors:

                def normalize_name(value: str) -> tuple[str, ...]:
                    return tuple(sorted(_clean_text(value).casefold().split()))

                requested_author = normalize_name(author)
                if not any(normalize_name(name) == requested_author for name in authors):
                    continue

            image = card.select_one(".book-card__cover-image")
            cover = _absolute_url(
                str((image or {}).get("data-src") or (image or {}).get("src") or "")
            )

            rating = card.select_one(".rating__avarage")
            rating_count = card.select_one(".rating__text")
            rating_value = _clean_text(rating.get_text(" ")) if rating else ""
            count_value = _clean_text(rating_count.get_text(" ")) if rating_count else ""
            rating_text = f"{rating_value} ({count_value})" if rating_value and count_value else rating_value
            display_fields = [DisplayField(label="Ocena LC", value=rating_text, icon="star")] if rating_text else []
            for selector, label in ((".book-card__readers", "Czytelnicy LC"), (".book-card__reviews", "Opinie LC")):
                element = card.select_one(selector)
                if element:
                    value = _clean_text(element.get_text(" "))
                    if value:
                        display_fields.append(DisplayField(label=label, value=value))
            series_link = card.select_one(".book-card__detail--cycle a")
            series_id = None
            series_name = None
            series_position = None
            if series_link:
                series_text = _clean_text(series_link.get_text(" "))
                series_href = str(series_link.get("href") or "")
                match = re.search(r"/cykl/(\d+)", series_href)
                series_id = match.group(1) if match else None
                position_text = series_text.lower().partition("(tom ")[2].partition(")")[0].strip() if "(tom " in series_text.lower() else ""
                series_position = int(position_text) if position_text.isdigit() else None
                series_name = series_text.partition("(tom ")[0].strip() or None
            book_url = _absolute_url(href)
            if not book_url:
                continue
            book_path = urlsplit(book_url).path.rstrip("/")
            if _book_id(book_path) != book_id:
                continue

            seen.add(book_id)
            books.append(
                BookMetadata(
                    provider=self.name,
                    provider_id=f"{book_id}--{book_path.rsplit(chr(47), 1)[-1]}",
                    provider_display_name=self.display_name,
                    title=title,
                    authors=authors,
                    cover_url=cover,
                    publish_year=None,
                    display_fields=display_fields,
                    source_url=book_url,
                    search_title=title,
                    search_author=authors[0] if authors else None,
                    series_id=series_id,
                    series_name=series_name,
                    series_position=series_position,
                )
            )
            if len(books) >= limit:
                break
        return books

    def _parse_authors(self, scope: Tag | BeautifulSoup | None, data: dict[str, Any]) -> list[str]:
        names = _names(data.get("author"))
        if names or scope is None:
            return names
        for selector in (
            ".book-card__author",
            '[class*="author"] a',
            '[itemprop="author"]',
            'a[href*="/autor/"]',
        ):
            found = [
                _clean_text(node.get("name") or node.get_text(" "))
                for node in scope.select(selector)
            ]
            found = [name for name in found if name]
            if found:
                return list(dict.fromkeys(found))
        return []

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="lubimyczytac:book")
    def get_book(self, book_id: str) -> BookMetadata | None:
        requested_id = book_id.strip()
        if re.fullmatch(r"\d+--[^/]+", requested_id):
            book_id, slug = requested_id.split("--", 1)
            provider_id = requested_id
            book_url = f"{LUBIMYCZYTAC_BOOK_URL}{book_id}/{slug}"
        elif requested_id.isdigit():
            return None
        else:
            book_url = _absolute_url(requested_id)
            if not book_url:
                return None
            book_path = urlsplit(book_url).path.rstrip("/")
            if not re.fullmatch(r"/ksiazka/\d+/[^/]+", book_path):
                return None
            book_id = _book_id(book_path)
            provider_id = f"{book_id}--{book_path.rsplit(chr(47), 1)[-1]}"

        html = self._fetch(book_url)
        if html is None:
            return None
        soup = BeautifulSoup(html, "html.parser")
        data = next(
            (
                item
                for item in _json_ld_books(soup)
                if _book_id(str(item.get("url", ""))) in ("", book_id)
            ),
            {},
        )
        rating = data.get("aggregateRating") or {}
        if isinstance(rating, dict):
            rating_value = rating.get("ratingValue")
            rating_count = rating.get("ratingCount") or rating.get("reviewCount")
        else:
            rating_value = rating_count = None
        title = _clean_text(str(data.get("name") or ""))
        title_node = soup.select_one('h1[itemprop="name"], h1.book__title, h1')
        title = title or (_clean_text(title_node.get_text(" ")) if title_node else "")
        authors = self._parse_authors(soup, data)
        if not title:
            return None
        isbn_10, isbn_13 = self._parse_isbns(soup, data)
        cover = _extract_cover(soup, data)
        description = _clean_text(str(data.get("description") or "")) or None
        if not description:
            node = soup.select_one(
                '#book-description, [itemprop="description"], .book__description, .description'
            )
            description = _clean_text(node.get_text(" ")) if node else None
        if not description:
            node = soup.select_one('meta[property="og:description"]')
            description = _clean_text(str(node.get("content") or "")) if node else None

        publisher = _names(data.get("publisher"))
        if not publisher:
            node = soup.select_one(
                '[itemprop="publisher"] [itemprop="name"], [itemprop="publisher"], '
                'a[href*="/wydawnictwo/"]'
            )
            publisher = [_clean_text(node.get_text(" "))] if node else []
        if not publisher:
            for node in soup.select("span.book__txt"):
                if "Wydawnictwo:" not in node.get_text(" ", strip=True):
                    continue
                value_node = node.select_one("a")
                value = _clean_text(
                    value_node.get_text(" ")
                    if value_node
                    else node.get_text(" ").replace("Wydawnictwo:", "", 1)
                )
                if value:
                    publisher = [value]
                    break
        if not publisher:
            node = soup.select_one("[data-ga-book-publishers]")
            value = _clean_text(str(node.get("data-ga-book-publishers") or "")) if node else ""
            if value:
                publisher = [value]
        year = self._parse_year(data, soup)
        language = self._parse_language(data, soup)
        genre_nodes = soup.select(
            ".book__category.d-sm-block.d-none, [itemprop='genre'], "
            ".book__genres a, a[href*='/gatunek/']"
        )
        html_genres = [
            _clean_text(part)
            for node in genre_nodes
            for part in node.get_text(" ").split(",")
            if _clean_text(part)
        ]
        detail_genres = self._detail_value(soup, "Kategoria")
        if detail_genres:
            html_genres = [
                _clean_text(part) for part in detail_genres.split(",") if _clean_text(part)
            ]
        genres = html_genres or data.get("genre") or []
        if isinstance(genres, str):
            genres = [genres]
        series_name, series_position, series_id = self._parse_series(soup, data)
        translator = self._parse_translator(soup)
        display_fields = [
            DisplayField(label="Translator", value=name, icon="user") for name in translator
        ]
        if rating_value is not None:
            display_fields.append(DisplayField(label="LC rating", value=f"{rating_value}/10", icon="star"))
        if rating_count is not None:
            display_fields.append(DisplayField(label="LC ratings", value=str(rating_count), icon="users"))
        page_count = self._detail_value(soup, "Liczba stron")
        if page_count:
            digits = re.sub(r"[^0-9]", "", page_count)
            if digits:
                display_fields.append(DisplayField(label="Pages", value=digits, icon="book"))
        for label, detail_label, icon in (("Original title", "Tytuł oryginału", "book"), ("Format", "Format", "book"), ("Publication date", "Data wydania", "calendar"), ("First Polish edition", "Data 1. wyd. pol.", "calendar")):
            value = self._detail_value(soup, detail_label)
            if value:
                display_fields.append(DisplayField(label=label, value=value, icon=icon))
        tags = list(
            dict.fromkeys(
                _clean_text(node.get_text(" "))
                for node in soup.select('a[href*="/ksiazki/t/"]')
                if _clean_text(node.get_text(" "))
            )
        )
        if tags:
            display_fields.append(DisplayField(label="Tags", value=", ".join(tags), icon="tag"))
        source_url = _absolute_url(str(data.get("url") or LUBIMYCZYTAC_BOOK_URL + book_id))
        return BookMetadata(
            provider=self.name,
            provider_id=provider_id,
            provider_display_name=self.display_name,
            title=title,
            authors=authors,
            isbn_10=isbn_10,
            isbn_13=isbn_13,
            cover_url=cover,
            description=description,
            publisher=", ".join(publisher) or None,
            publish_year=year,
            language=language,
            genres=list(
                dict.fromkeys(
                    _clean_text(str(genre)) for genre in genres if _clean_text(str(genre))
                )
            ),
            source_url=source_url,
            search_title=title,
            search_author=authors[0] if authors else None,
            series_id=series_id,
            series_name=series_name,
            series_position=series_position,
            display_fields=display_fields,
        )

    @staticmethod
    def _detail_value(soup: BeautifulSoup, label: str) -> str | None:
        expected = _clean_text(label).rstrip(":").casefold()
        for dt in soup.find_all("dt"):
            current = _clean_text(dt.get_text(" ")).rstrip(":").casefold()
            if current == expected:
                dd = dt.find_next_sibling("dd")
                if dd:
                    value = _clean_text(dd.get_text(" "))
                    return value or None
        return None

    def _parse_isbns(
        self, soup: BeautifulSoup, data: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        values: list[str] = []
        if data.get("isbn"):
            values.extend(data["isbn"] if isinstance(data["isbn"], list) else [str(data["isbn"])])
        values.extend(
            str(node.get("content") or node.get_text(" "))
            for node in soup.select('[itemprop="isbn"], .isbn')
        )
        detail_isbn = self._detail_value(soup, "ISBN")
        if detail_isbn:
            values.append(detail_isbn)
        if not values:
            values.extend(_ISBN_RE.findall(soup.get_text(" ")))
        isbn10 = isbn13 = None
        for raw in values:
            isbn = _valid_isbn(raw)
            if isbn and len(isbn) == 13:
                isbn13 = isbn13 or isbn
            elif isbn:
                isbn10 = isbn10 or isbn
        return isbn10, isbn13

    def _parse_year(self, data: dict[str, Any], soup: BeautifulSoup) -> int | None:
        value = data.get("datePublished") or data.get("copyrightYear")
        match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", str(value or ""))
        if not match:
            node = soup.select_one('[itemprop="datePublished"], .book__release-date')
            text = node.get("content", node.get_text(" ")) if node else ""
            match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", text)
        return int(match.group(1)) if match else None

    def _parse_language(self, data: dict[str, Any], soup: BeautifulSoup) -> str | None:
        value = str(data.get("inLanguage") or "").strip().lower()
        if not value:
            node = soup.select_one('[itemprop="inLanguage"]')
            value = (
                _clean_text(str(node.get("content") or node.get_text(" "))).lower() if node else ""
            )
        if not value:
            for node in soup.find_all("dt"):
                if "język:" in node.get_text(" ", strip=True).casefold():
                    sibling = node.find_next_sibling("dd")
                    if sibling:
                        value = _clean_text(sibling.get_text(" ")).lower()
                    break
        if len(value) == 2:
            return value
        return _LANGUAGE_MAP.get(value)

    def _parse_series(
        self, soup: BeautifulSoup, data: dict[str, Any]
    ) -> tuple[str | None, float | None, str | None]:
        series = data.get("isPartOfSeries") or data.get("isPartOf") or data.get("series")
        if isinstance(series, list):
            series = series[0] if series else None

        name = None
        position = None
        series_id = None

        if isinstance(series, dict):
            name = _clean_text(str(series.get("name") or "")) or None
            raw_position = str(series.get("position") or "").replace(",", ".")
            try:
                position = float(raw_position) if raw_position else None
            except ValueError:
                position = None
            series_url = str(series.get("url") or series.get("@id") or "")
            if series_url:
                series_id = (
                    re.search(r"/cykl/(\d+)", series_url).group(1)
                    if re.search(r"/cykl/(\d+)", series_url)
                    else None
                )
        elif series:
            name = _clean_text(str(series)) or None

        detail_series = self._detail_value(soup, "Cykl")
        if detail_series:
            match = re.search(
                r"^(.*?)\s*\((?:tom|część|nr)\s*(\d+(?:[.,]\d+)?)\)",
                detail_series,
                re.IGNORECASE,
            )
            if match:
                name = _clean_text(match.group(1)) or name
                if (
                    position is None
                    and match.group(2).replace(",", ".").replace(".", "", 1).isdigit()
                ):
                    position = float(match.group(2).replace(",", "."))
            elif not name:
                name = detail_series

        node = soup.select_one('a[href*="/cykl/"]')
        if node:
            name = name or _clean_text(node.get_text(" ")) or None
            href = str(node.get("href") or "")
            if href:
                series_id = (
                    re.search(r"/cykl/(\d+)", href).group(1)
                    if re.search(r"/cykl/(\d+)", href)
                    else None
                )
            parent_text = _clean_text(node.parent.get_text(" ") if node.parent else "")
            match = re.search(
                r"(?:tom|część|nr)\s*(\d+(?:[.,]\d+)?)",
                parent_text,
                re.IGNORECASE,
            )
            if (
                position is None
                and match
                and match.group(1).replace(",", ".").replace(".", "", 1).isdigit()
            ):
                position = float(match.group(1).replace(",", "."))

        return name, position, series_id

    def _parse_translator(self, soup: BeautifulSoup) -> list[str]:
        value = self._detail_value(soup, "Tłumacz")
        if not value:
            return []
        names = re.split(r",|;|\si\s", value)
        return [_clean_text(name) for name in names if _clean_text(name)]

    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        """ISBN search is intentionally unsupported."""
        return None


def _test_lubimyczytac_connection() -> dict[str, Any]:
    try:
        provider = LubimyczytacProvider()
        response = provider.session.get(
            LUBIMYCZYTAC_SEARCH_URL + urlencode({"phrase": "test"}),
            timeout=10,
            verify=get_ssl_verify(LUBIMYCZYTAC_BASE_URL),
        )
        response.raise_for_status()
    except requests.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Connection failed: {exc}"}
    if "lubimyczytac" in response.text.lower():
        return {"success": True, "message": "Successfully connected to Lubimyczytać.pl"}
    return {"success": False, "message": "Unexpected response from Lubimyczytać.pl"}


@register_settings(
    "lubimyczytac", "Lubimyczytać.pl", icon="library", order=56, group="metadata_providers"
)
def lubimyczytac_settings() -> list[SettingsField]:
    return [
        HeadingField(
            key="lubimyczytac_heading",
            title="Lubimyczytać.pl",
            description="Polish book catalog. No API key required.",
            link_url=LUBIMYCZYTAC_BASE_URL,
            link_text="lubimyczytac.pl",
        ),
        CheckboxField(
            key="LUBIMYCZYTAC_ENABLED",
            label="Enable Lubimyczytać.pl",
            description="Enable Lubimyczytać.pl as a metadata provider for book searches",
            default=False,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description="Verify Lubimyczytać.pl is accessible",
            style="primary",
            callback=_test_lubimyczytac_connection,
        ),
    ]
