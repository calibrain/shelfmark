"""Moly.hu metadata provider. Hungarian book catalog, no API key required.

Scraping approach (search URL, book-page structure, language mapping) adapted
from the Calibre Moly_hu plugin by Hoffer Csaba, Kloon, otapi, Dezso, Hokutya,
seeder and contributors (GPL v3, mobileread.com).
"""

import re
import threading
import time
import unicodedata
from collections import deque
from typing import Any, ClassVar
from urllib.parse import quote

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

logger = setup_logger(__name__)

MOLY_BASE_URL = "https://moly.hu"
MOLY_BOOK_URL = f"{MOLY_BASE_URL}/konyvek/"
MOLY_SEARCH_URL = f"{MOLY_BASE_URL}/kereses?query="

# Be polite: moly.hu is a small community site
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60

REQUEST_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"),
    "Accept-Language": "hu,en;q=0.7",
}

ISBN_13_LENGTH = 13

# Moly tags its foreign-language editions; everything else is Hungarian.
# Mapping from the Calibre Moly_hu plugin.
_LANGUAGE_TAG_MAP = {
    "angol nyelvű": "en",
    "n\xe9met nyelvű": "de",
    "francia nyelvű": "fr",
    "olasz nyelvű": "it",
    "spanyol nyelvű": "es",
    "orosz nyelvű": "ru",
    "t\xf6r\xf6k nyelvű": "tr",
    "g\xf6r\xf6g nyelvű": "el",
    "k\xednai nyelvű": "zh",
    "jap\xe1n nyelvű": "ja",
}


class RateLimiter:
    """Simple sliding window rate limiter."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        """Initialize rate limiter with max requests per time window."""
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.timestamps: deque[float] = deque()
        self.lock = threading.Lock()

    def wait_if_needed(self) -> None:
        """Block until a request is allowed (thread-safe)."""
        wait_time = 0.0

        with self.lock:
            now = time.time()
            cutoff = now - self.window_seconds
            while self.timestamps and self.timestamps[0] < cutoff:
                self.timestamps.popleft()
            if len(self.timestamps) >= self.max_requests:
                wait_time = self.timestamps[0] + self.window_seconds - now

        if wait_time > 0:
            logger.debug("Rate limited, waiting %0.2fs", wait_time)
            time.sleep(wait_time)

        with self.lock:
            now = time.time()
            cutoff = now - self.window_seconds
            while self.timestamps and self.timestamps[0] < cutoff:
                self.timestamps.popleft()
            self.timestamps.append(time.time())


_rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_SECONDS)


def _clean_text(value: str | None) -> str | None:
    """Strip zero-width characters and collapse whitespace."""
    if value is None:
        return None
    value = value.replace("​", "").replace("﻿", "")
    return " ".join(value.split())


def _normalize_for_match(value: str | None) -> str:
    """Accent-insensitive, punctuation-insensitive comparison form."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = "".join(char if char.isalnum() else " " for char in value)
    return " ".join(value.lower().split())


def _absolute_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return MOLY_BASE_URL + url


def _valid_isbn(candidate: str) -> str | None:
    """Return a normalized ISBN-10/13 (digits, with optional X check digit), else None."""
    digits = candidate.replace("-", "").strip()
    if len(digits) == ISBN_13_LENGTH and digits.isdigit():
        return digits
    if len(digits) == 10 and re.fullmatch(r"\d{9}[\dXx]", digits):
        return digits.upper()
    return None


@register_provider("moly")
class MolyProvider(MetadataProvider):
    """Moly.hu metadata provider (HTML scraping, Hungarian catalog)."""

    name = "moly"
    display_name = "Moly.hu"
    requires_auth = False
    supported_sorts: ClassVar[tuple[SortOrder, ...]] = (SortOrder.RELEVANCE,)
    search_fields: ClassVar[tuple[SearchField, ...]] = (
        TextSearchField(
            key="author",
            label="Author",
            description="Search by author name",
        ),
        TextSearchField(
            key="title",
            label="Title",
            description="Search by book title",
        ),
    )

    def __init__(self) -> None:
        """Initialize provider."""
        self.session = requests.Session()
        self.session.headers.update(REQUEST_HEADERS)

    def is_available(self) -> bool:
        """Moly.hu needs no authentication."""
        return True

    def _fetch(self, url: str, timeout: int = 15) -> str | None:
        _rate_limiter.wait_if_needed()
        try:
            response = self.session.get(url, timeout=timeout, verify=get_ssl_verify(MOLY_BASE_URL))
            response.raise_for_status()
        except requests.Timeout:
            logger.warning("Moly.hu request timed out: %s", url)
            return None
        except requests.RequestException:
            logger.exception("Moly.hu request failed: %s", url)
            return None
        return response.text

    def search(self, options: MetadataSearchOptions) -> list[BookMetadata]:
        """Search moly.hu's site search."""
        if options.search_type == SearchType.ISBN:
            result = self.search_by_isbn(options.query)
            return [result] if result else []

        # Moly's search is a single ranked page; no server-side pagination.
        if options.page > 1:
            return []

        author_value = (options.fields.get("author") or "").strip()
        title_value = (options.fields.get("title") or "").strip()
        terms = " ".join(t for t in (author_value, title_value) if t)
        query = terms or options.query.strip()
        if not query:
            return []

        fields_key = ":".join(f"{k}={v}" for k, v in sorted(options.fields.items()))
        cache_key = f"{query}:{options.search_type.value}:{options.limit}:{fields_key}"
        return self._search_cached(cache_key, query, options.limit) or []

    @cacheable(ttl_key="METADATA_CACHE_SEARCH_TTL", ttl_default=300, key_prefix="moly:search")
    def _search_cached(self, cache_key: str, query: str, limit: int) -> list[BookMetadata] | None:
        # Return None (not []) on fetch failure so the failure is not cached.
        html = self._fetch(MOLY_SEARCH_URL + quote(query.encode("utf-8")))
        if html is None:
            return None

        soup = BeautifulSoup(html, "html.parser")
        books: list[BookMetadata] = []
        seen: set[str] = set()

        for anchor in soup.select("#content div.search_area a.book_selector"):
            href = anchor.get("href") or ""
            match = re.search(r"/konyvek/([^/?#]+)", str(href))
            if not match:
                continue
            slug = match.group(1)
            if slug in seen:
                continue

            # No separator: moly wraps matched search terms in <strong> even
            # mid-word ("Lis<strong>a</strong> Jewell"), so inserting one
            # would split words at highlight boundaries.
            text = _clean_text(anchor.get_text()) or ""
            author, _, title = text.partition(":")
            if not title:
                # Result rows are "Author: Title"; skip anything else.
                continue
            author = author.strip()
            title = title.strip()

            seen.add(slug)
            books.append(
                BookMetadata(
                    provider=self.name,
                    provider_id=slug,
                    provider_display_name=self.display_name,
                    title=title,
                    authors=[author] if author else [],
                    cover_url=self._cover_for_result(soup, text),
                    source_url=MOLY_BOOK_URL + slug,
                    language="hu",
                    search_title=title,
                    search_author=author or None,
                    display_fields=self._result_display_fields(anchor),
                )
            )
            if len(books) >= limit:
                break

        logger.info("Moly.hu search '%s' returned %s results", query, len(books))
        return books

    def _cover_for_result(self, soup: BeautifulSoup, result_text: str) -> str | None:
        """Find the search-result thumbnail whose alt matches 'Author: Title'."""
        target = _normalize_for_match(result_text)
        if not target:
            return None
        for img in soup.select("#content img.tooltip[alt]"):
            if _normalize_for_match(str(img.get("alt") or "")) == target:
                return _absolute_url(str(img.get("src") or "")) or None
        return None

    def _result_display_fields(self, anchor: Tag) -> list[DisplayField]:
        fields: list[DisplayField] = []
        parent = anchor.parent
        if parent is None:
            return fields
        like = parent.select_one("span.like_count")
        if like:
            fields.append(
                DisplayField(label="Rating", value=like.get_text(strip=True), icon="star")
            )
        series = parent.select_one('a[href*="/sorozatok/"]')
        if series:
            fields.append(
                DisplayField(
                    label="Series",
                    value=series.get_text(strip=True).strip("()"),
                    icon="editions",
                )
            )
        return fields

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="moly:book")
    def get_book(self, book_id: str) -> BookMetadata | None:
        """Get book details by moly.hu slug (e.g. 'mocsidzuki-mai-a-telihold-kavezo')."""
        html = self._fetch(MOLY_BOOK_URL + quote(book_id))
        if html is None:
            return None

        soup = BeautifulSoup(html, "html.parser")

        title = self._parse_title(soup)
        authors = [_clean_text(a.get_text()) or "" for a in soup.select("#content div.authors a")]
        authors = [a for a in authors if a]
        if not title or not authors:
            logger.warning("Moly.hu book page missing title/authors: %s", book_id)
            return None

        isbn_13, isbn_10 = self._parse_isbns(soup)
        series = self._parse_series(soup)
        tags = [_clean_text(t.get_text()) or "" for t in soup.select("#book_tags a.tag")]
        tags = [t for t in tags if t]

        display_fields: list[DisplayField] = []
        rating = soup.select_one("#content .rating .like_count")
        if rating:
            display_fields.append(
                DisplayField(label="Rating", value=rating.get_text(strip=True), icon="star")
            )
        if series:
            display_fields.append(DisplayField(label="Series", value=series, icon="editions"))

        return BookMetadata(
            provider=self.name,
            provider_id=book_id,
            provider_display_name=self.display_name,
            title=title,
            authors=authors,
            isbn_13=isbn_13,
            isbn_10=isbn_10,
            cover_url=self._parse_cover(soup),
            description=self._parse_description(soup),
            publisher=self._parse_publisher(soup),
            publish_year=self._parse_publish_year(soup),
            language=self._parse_language(tags),
            genres=tags,
            source_url=MOLY_BOOK_URL + book_id,
            search_title=title,
            search_author=authors[0],
            display_fields=display_fields,
        )

    @cacheable(ttl_key="METADATA_CACHE_BOOK_TTL", ttl_default=600, key_prefix="moly:isbn")
    def search_by_isbn(self, isbn: str) -> BookMetadata | None:
        """Moly's site search resolves ISBN queries directly."""
        isbn = isbn.replace("-", "").strip()
        if not isbn:
            return None
        html = self._fetch(MOLY_SEARCH_URL + quote(isbn))
        if html is None:
            return None
        soup = BeautifulSoup(html, "html.parser")
        anchor = soup.select_one("#content div.search_area a.book_selector[href]")
        if not anchor:
            return None
        match = re.search(r"/konyvek/([^/?#]+)", str(anchor.get("href")))
        if not match:
            return None
        return self.get_book(match.group(1))

    def _parse_title(self, soup: BeautifulSoup) -> str | None:
        node = soup.select_one("#content .head_title h1 span.item")
        if node:
            # The series link is nested inside this span; only direct text
            # belongs to the book title.
            direct = "".join(node.find_all(string=True, recursive=False))
            title = _clean_text(direct)
            if title:
                return title
        node = soup.select_one("#content .book > span")
        if node:
            return _clean_text(node.get_text())
        return None

    def _parse_series(self, soup: BeautifulSoup) -> str | None:
        node = soup.select_one('#content h1 a[href*="/sorozatok/"]')
        if not node:
            return None
        return (_clean_text(node.get_text()) or "").strip("()") or None

    def _parse_isbns(self, soup: BeautifulSoup) -> tuple[str | None, str | None]:
        isbn_13 = isbn_10 = None
        editions = soup.select("#content .items .edition") or soup.select("#content .items > div")
        for edition in editions:
            text = edition.get_text(" ")
            for candidate in re.findall(r"(?<!\d)[\d-]{10,17}(?!\d)", text):
                isbn = _valid_isbn(candidate)
                if not isbn:
                    continue
                if len(isbn) == ISBN_13_LENGTH and not isbn_13:
                    isbn_13 = isbn
                elif len(isbn) != ISBN_13_LENGTH and not isbn_10:
                    isbn_10 = isbn
            if isbn_13:
                break
        return isbn_13, isbn_10

    def _parse_cover(self, soup: BeautifulSoup) -> str | None:
        node = soup.select_one("#content .coverbox a.zoom[href]")
        if node:
            return _absolute_url(str(node.get("href")))
        img = soup.select_one("#content .coverbox img[src]")
        if img:
            return _absolute_url(str(img.get("src")))
        return None

    def _parse_description(self, soup: BeautifulSoup) -> str | None:
        node = soup.select_one("#content #full_description")
        if node is None:
            node = soup.select_one("#content div.text")
        if node is None:
            return None
        spoiler_warning = "Vigyázat! Cselekményleírást tartalmaz."
        parts = []
        for text in node.stripped_strings:
            cleaned = _clean_text(text) or ""
            if cleaned.startswith(spoiler_warning):
                cleaned = cleaned[len(spoiler_warning) :].strip()
            if cleaned:
                parts.append(cleaned)
        return "\n".join(parts) or None

    def _parse_publisher(self, soup: BeautifulSoup) -> str | None:
        node = soup.select_one('#content .items .edition a[href*="/kiadok/"]')
        if node:
            return _clean_text(node.get_text())
        return None

    def _parse_publish_year(self, soup: BeautifulSoup) -> int | None:
        editions = soup.select("#content .items .edition") or soup.select("#content .items > div")
        for edition in editions:
            match = re.search(r"\b(\d{4})\b", edition.get_text(" "))
            if match:
                return int(match.group(1))
        return None

    def _parse_language(self, tags: list[str]) -> str:
        for tag in tags:
            code = _LANGUAGE_TAG_MAP.get(tag.lower().strip())
            if code:
                return code
        return "hu"


def _test_moly_connection() -> dict[str, Any]:
    """Test connectivity to moly.hu."""
    try:
        provider = MolyProvider()
        response = provider.session.get(
            MOLY_SEARCH_URL + quote("teszt"),
            timeout=10,
            verify=get_ssl_verify(MOLY_BASE_URL),
        )
        response.raise_for_status()
    except requests.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except requests.RequestException as e:
        return {"success": False, "message": f"Connection failed: {e}"}
    if "moly" in response.text.lower():
        return {"success": True, "message": "Successfully connected to moly.hu"}
    return {"success": False, "message": "Unexpected response from moly.hu"}


@register_settings("moly", "Moly.hu", icon="library", order=54, group="metadata_providers")
def moly_settings() -> list[SettingsField]:
    """Moly.hu metadata provider settings."""
    return [
        HeadingField(
            key="moly_heading",
            title="Moly.hu",
            description=(
                "Hungarian community book catalog with excellent coverage of "
                "Hungarian editions and translations. No API key required."
            ),
            link_url="https://moly.hu",
            link_text="moly.hu",
        ),
        CheckboxField(
            key="MOLY_ENABLED",
            label="Enable Moly.hu",
            description="Enable Moly.hu as a metadata provider for book searches",
            default=False,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description="Verify moly.hu is accessible",
            style="primary",
            callback=_test_moly_connection,
        ),
    ]
