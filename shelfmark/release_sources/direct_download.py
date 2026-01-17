"""Direct download source - Anna's Archive/Libgen with fallback cascade."""

import itertools
import json
import re
import time
from pathlib import Path
from threading import Event
from typing import Callable, Dict, List, Optional
from urllib.parse import quote

import requests

from bs4 import BeautifulSoup, NavigableString, Tag

from shelfmark.download import http as downloader
from shelfmark.download import network
from shelfmark.config.env import DEBUG_SKIP_SOURCES, TMP_DIR
from shelfmark.core.config import config
from shelfmark.core.utils import CONTENT_TYPES
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import BookInfo, SearchFilters, DownloadTask
from shelfmark.metadata_providers import BookMetadata, group_languages_by_localized_title
from shelfmark.release_sources import (
    Release,
    ReleaseProtocol,
    ReleaseSource,
    DownloadHandler,
    register_source,
    register_handler,
    ReleaseColumnConfig,
    ColumnSchema,
    ColumnRenderType,
    ColumnAlign,
    ColumnColorHint,
)

logger = setup_logger(__name__)

_aa_slow_rotation = itertools.count()
_url_source_types: Dict[str, str] = {}

if DEBUG_SKIP_SOURCES:
    logger.warning("DEBUG_SKIP_SOURCES active: skipping sources %s", DEBUG_SKIP_SOURCES)

_DOWNLOAD_SOURCES = [
    ("welib", "Welib", ["welib.org"]),
    ("aa-fast", "Anna's Archive (Fast)", ["/dyn/api/fast_download"]),
    ("aa-slow-wait", "Anna's Archive (Waitlist)", []),  # Matched via _url_source_types
    ("aa-slow-nowait", "Anna's Archive", []),  # Matched via _url_source_types
    ("aa-slow", "Anna's Archive", ["/slow_download/", "annas-"]),  # Fallback for untagged AA URLs
    ("libgen", "Libgen", ["libgen"]),
    ("zlib", "Z-Library", ["z-lib", "zlibrary"]),
]

_SOURCE_FAILURE_THRESHOLD = 4
_MIN_VALID_FILE_SIZE = 10 * 1024

# Sources that require Cloudflare bypass
_CF_BYPASS_REQUIRED = frozenset({"aa-slow-nowait", "aa-slow-wait", "zlib", "welib"})

# Sources whose URLs come from AA page (multiple mirrors)
_AA_PAGE_SOURCES = frozenset({"aa-slow-nowait", "aa-slow-wait"})

def _get_md5_url_template(source_id: str) -> Optional[str]:
    """Get URL template for MD5-based sources from centralized config."""
    from shelfmark.core import mirrors

    if source_id == "zlib":
        return mirrors.get_zlib_url_template()
    elif source_id == "welib":
        return mirrors.get_welib_url_template()
    return None


def _get_libgen_domains() -> List[str]:
    """Get LibGen domains from centralized config."""
    from shelfmark.core import mirrors
    return mirrors.get_libgen_mirrors()

_LIBGEN_GET_PATTERNS = [
    re.compile(r'<a\s+href=["\']([^"\']*get\.php\?md5=[^"\']+&key=[^"\']+)["\'][^>]*>\s*<h2[^>]*>GET</h2>\s*</a>', re.IGNORECASE),
    re.compile(r'<a[^>]+href=["\']([^"\']*get\.php\?md5=[^"\']+&(?:amp;)?key=[^"\']+)["\']', re.IGNORECASE),
    re.compile(r'<a\s+href=["\']([^"\']*get\.php[^"\']*)["\'][^>]*>[\s\S]*?<h2[^>]*>GET</h2>', re.IGNORECASE),
    re.compile(r'href=["\']([^"\']*get\.php\?[^"\']*md5=[^"\']*&[^"\']*key=[^"\']+)["\']', re.IGNORECASE),
]

def _get_source_priority() -> List[Dict]:
    """Get the full source priority list.

    Fast sources (AA Fast, LibGen) are hardcoded first.
    Slow sources come from user config.
    """
    # Fast sources - always first, hardcoded
    fast_sources = []

    # AA Fast only if donator key is set
    if config.get("AA_DONATOR_KEY"):
        fast_sources.append({"id": "aa-fast", "enabled": True})

    # LibGen always available
    fast_sources.append({"id": "libgen", "enabled": True})

    # User's configured slow sources (config won't contain fast sources)
    slow_sources = config.get("SOURCE_PRIORITY") or []

    # Filter out any legacy fast source entries from old configs
    slow_sources = [s for s in slow_sources if s["id"] not in ("aa-fast", "libgen")]

    return fast_sources + slow_sources


def _is_source_enabled(source_id: str) -> bool:
    """Check if a source is enabled in the priority config.

    Returns False for unknown sources.
    """
    for item in _get_source_priority():
        if item["id"] == source_id:
            return item.get("enabled", True)
    return False


_SIZE_UNIT_PATTERN = re.compile(r'(kb|mb|gb|tb)', re.IGNORECASE)


def _normalize_size(size_str: str) -> str:
    """Normalize size string by uppercasing units (e.g., '5.2 mb' -> '5.2 MB')."""
    return _SIZE_UNIT_PATTERN.sub(lambda m: m.group(1).upper(), size_str.strip())


class SearchUnavailable(Exception):
    """Raised when Anna's Archive cannot be reached via any mirror/DNS."""


def search_books(query: str, filters: SearchFilters) -> List[BookInfo]:
    """Search for books matching the query.

    Args:
        query: Search term (ISBN, title, author, etc.)
        filters: Search filters (language, format, content type, etc.)

    Returns:
        List[BookInfo]: List of matching books

    Raises:
        SearchUnavailable: If Anna's Archive cannot be reached
        Exception: If parsing fails
    """
    query_html = quote(query)

    if filters.isbn:
        isbns = " || ".join(
            [f"('isbn13:{isbn}' || 'isbn10:{isbn}')" for isbn in filters.isbn]
        )
        query_html = quote(f"({isbns}) {query}")

    filters_query = ""

    for value in filters.lang or config.BOOK_LANGUAGE:
        if value != "all":
            filters_query += f"&lang={quote(value)}"

    if filters.sort and filters.sort != "relevance":
        filters_query += f"&sort={quote(filters.sort)}"

    if filters.content:
        for value in filters.content:
            filters_query += f"&content={quote(value)}"

    formats_to_use = filters.format if filters.format else config.SUPPORTED_FORMATS

    index = 1
    for filter_type, filter_values in vars(filters).items():
        if filter_type in ("author", "title") and filter_values:
            for value in filter_values:
                filters_query += f"&termtype_{index}={filter_type}&termval_{index}={quote(value)}"
                index += 1

    selector = network.AAMirrorSelector()

    url = (
        f"{network.get_aa_base_url()}"
        f"/search?index=&page=1&display=table"
        f"&acc=aa_download&acc=external_download"
        f"&ext={'&ext='.join(formats_to_use)}"
        f"&q={query_html}"
        f"{filters_query}"
    )

    html = downloader.html_get_page(url, selector=selector)
    if not html:
        # Network/mirror exhaustion path bubbles up so API can notify clients
        raise SearchUnavailable("Unable to reach download source. Network restricted or mirrors are blocked.")

    if "No files found." in html:
        logger.info(f"No books found for query: {query}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    tbody: Tag | NavigableString | None = soup.find("table")

    if not tbody:
        logger.warning(f"No results table found for query: {query}")
        raise Exception("No books found. Please try another query.")

    books = []
    if isinstance(tbody, Tag):
        for line_tr in tbody.find_all("tr"):
            try:
                book = _parse_search_result_row(line_tr)
                if book:
                    books.append(book)
            except Exception as e:
                logger.error_trace(f"Failed to parse search result row: {e}")

    books.sort(
        key=lambda x: (
            config.SUPPORTED_FORMATS.index(x.format)
            if x.format in config.SUPPORTED_FORMATS
            else len(config.SUPPORTED_FORMATS)
        )
    )

    return books


def get_book_info(book_id: str, fetch_download_count: bool = True) -> BookInfo:
    """Get detailed information for a specific book.

    Args:
        book_id: Book identifier (MD5 hash)
        fetch_download_count: Whether to fetch download count from summary API.
            Only needed for display in DetailsModal, not for downloads.

    Returns:
        BookInfo: Detailed book information including download URLs
    """
    url = f"{network.get_aa_base_url()}/md5/{book_id}"
    selector = network.AAMirrorSelector()
    html = downloader.html_get_page(url, selector=selector)

    if not html:
        raise Exception(f"Failed to fetch book info for ID: {book_id}")

    soup = BeautifulSoup(html, "html.parser")

    return _parse_book_info_page(soup, book_id, fetch_download_count)


def _parse_search_result_row(row: Tag) -> Optional[BookInfo]:
    """Parse a single search result row into a BookInfo object."""
    try:
        if row.text.strip().lower().startswith("your ad here"):
            return None
        cells = row.find_all("td")
        preview_img = cells[0].find("img")
        preview = preview_img["src"] if preview_img else None

        return BookInfo(
            id=row.find_all("a")[0]["href"].split("/")[-1],
            preview=preview,
            title=cells[1].find("span").next,
            author=cells[2].find("span").next,
            publisher=cells[3].find("span").next,
            year=cells[4].find("span").next,
            language=cells[7].find("span").next,
            content=cells[8].find("span").next.lower(),
            format=cells[9].find("span").next.lower(),
            size=cells[10].find("span").next,
        )
    except Exception as e:
        logger.error_trace(f"Error parsing search result row: {e}")
        return None


def _parse_book_info_page(soup: BeautifulSoup, book_id: str, fetch_download_count: bool = True) -> BookInfo:
    """Parse the book info page HTML into a BookInfo object."""
    data = soup.select_one("body > main > div:nth-of-type(1)")

    if not data:
        raise Exception(f"Failed to parse book info for ID: {book_id}")

    preview: str = ""

    node = data.select_one("div:nth-of-type(1) > img")
    if node:
        preview_value = node.get("src", "")
        if isinstance(preview_value, list):
            preview = preview_value[0]
        else:
            preview = preview_value

    data = soup.find_all("div", {"class": "main-inner"})[0].find_next("div")
    divs = list(data.children)

    slow_urls_no_waitlist: set[str] = set()
    slow_urls_with_waitlist: set[str] = set()

    for anchor in soup.find_all("a"):
        try:
            text = anchor.text.strip().lower()
            href = anchor.get("href", "")
            if not href:
                continue

            next_text = ""
            if anchor.next and anchor.next.next:
                next_text = getattr(anchor.next.next, 'text', str(anchor.next.next)).strip().lower()

            if text.startswith("slow partner server") and "waitlist" in next_text:
                if "no waitlist" in next_text:
                    slow_urls_no_waitlist.add(href)
                else:
                    slow_urls_with_waitlist.add(href)
        except Exception:
            pass

    logger.debug(
        "Source inventory for %s -> aa_no_wait=%d, aa_wait=%d",
        book_id,
        len(slow_urls_no_waitlist),
        len(slow_urls_with_waitlist),
    )

    # Convert to absolute URLs and tag by source type
    base_url = network.get_aa_base_url()
    urls = []

    for rel_url in slow_urls_no_waitlist:
        abs_url = downloader.get_absolute_url(base_url, rel_url)
        if abs_url:
            urls.append(abs_url)
            _url_source_types[abs_url] = "aa-slow-nowait"

    for rel_url in slow_urls_with_waitlist:
        abs_url = downloader.get_absolute_url(base_url, rel_url)
        if abs_url:
            urls.append(abs_url)
            _url_source_types[abs_url] = "aa-slow-wait"

    original_divs = divs
    divs = [div for div in divs if div.text.strip() != ""]

    all_details = _find_in_divs(divs, " · ")
    format = ""
    size = ""
    content = ""

    for _details in all_details:
        _details = _details.split(" · ")
        for f in _details:
            if format == "" and f.strip().lower() in config.SUPPORTED_FORMATS:
                format = f.strip().lower()
            if size == "" and any(u in f.strip().lower() for u in ("mb", "kb", "gb")):
                size = _normalize_size(f)
            if content == "":
                for ct in CONTENT_TYPES:
                    if ct in f.strip().lower():
                        content = ct
                        break
        if format == "" or size == "":
            for f in _details:
                stripped = f.strip().lower()
                if format == "" and stripped and " " not in stripped:
                    format = stripped
                if size == "" and "." in stripped:
                    size = _normalize_size(f)

    book_title = (_find_in_divs(divs, "🔍") or [""])[0].strip("🔍").strip()

    # Extract basic information
    description = _extract_book_description(soup)

    book_info = BookInfo(
        id=book_id,
        preview=preview,
        title=book_title,
        content=content,
        publisher=(_find_in_divs(divs, "icon-[mdi--company]", is_class=True) or [""])[0],
        author=(_find_in_divs(divs, "icon-[mdi--user-edit]", is_class=True) or [""])[0],
        format=format,
        size=size,
        description=description,
        download_urls=urls,
    )

    # Extract additional metadata
    info = _extract_book_metadata(original_divs[-6])

    if fetch_download_count:
        try:
            summary_url = f"{network.get_aa_base_url()}/dyn/md5/summary/{book_id}"
            summary_response = downloader.html_get_page(summary_url, selector=network.AAMirrorSelector())
            if summary_response:
                summary_data = json.loads(summary_response)
                if "downloads_total" in summary_data:
                    info["Downloads"] = [str(summary_data["downloads_total"])]
        except Exception as e:
            logger.debug(f"Failed to fetch download count for {book_id}: {e}")

    book_info.info = info

    # Set language and year from metadata if available
    if info.get("Language"):
        book_info.language = info["Language"][0]
    if info.get("Year"):
        book_info.year = info["Year"][0]

    # Set source URL for linking back to Anna's Archive
    book_info.source_url = f"{network.get_aa_base_url()}/md5/{book_id}"

    return book_info


def _find_in_divs(divs: List, text: str, is_class: bool = False) -> List[str]:
    """Find divs containing text or having a specific class."""
    results = []
    for div in divs:
        if is_class:
            if div.find(class_=text):
                results.append(div.text.strip())
        elif text in div.text.strip():
            results.append(div.text.strip())
    return results


def _get_next_value_div(label_div: Tag) -> Optional[Tag]:
    """Find the next sibling div that holds the value for a metadata label."""
    sibling = label_div.next_sibling
    while sibling:
        if isinstance(sibling, Tag) and sibling.name == "div":
            return sibling
        sibling = sibling.next_sibling
    return None


def _extract_book_description(soup: BeautifulSoup) -> Optional[str]:
    """Extract the primary or alternative description from the book page."""
    container = soup.select_one(".js-md5-top-box-description")
    if not container:
        return None

    description: Optional[str] = None
    alternative: Optional[str] = None

    label_divs = container.select("div.text-xs.text-gray-500.uppercase")
    for label_div in label_divs:
        label_text = label_div.get_text(strip=True).lower()
        value_div = _get_next_value_div(label_div)
        if not value_div:
            continue

        value_text = value_div.get_text(separator=" ", strip=True)
        if not value_text:
            continue

        if label_text == "description":
            return value_text
        if label_text == "alternative description" and not alternative:
            alternative = value_text

    if alternative:
        return alternative

    # Fallback to the first text block inside the description container
    fallback_div = container.find("div", class_="mb-1")
    if fallback_div:
        fallback_text = fallback_div.get_text(separator=" ", strip=True)
        if fallback_text:
            return fallback_text

    return None


def _extract_book_metadata(metadata_divs) -> Dict[str, List[str]]:
    """Extract metadata from book info divs."""
    info: Dict[str, set[str]] = {}

    sub_datas = metadata_divs.find_all("div")[0]
    for sub_data in sub_datas.children:
        if sub_data.text.strip() == "":
            continue
        children = list(sub_data.children)
        key = children[0].text.strip()
        value = children[1].text.strip()
        if key not in info:
            info[key] = set()
        info[key].add(value)

    relevant_prefixes = ("isbn-", "alternative", "asin", "goodreads", "language", "year")
    return {
        k.strip(): list(v)
        for k, v in info.items()
        if k.lower().startswith(relevant_prefixes) and "filename" not in k.lower()
    }


def _get_source_info(link: str) -> tuple[str, str]:
    """Get source label and friendly name for a download link.

    Args:
        link: Download URL

    Returns:
        Tuple of (log_label, friendly_name)
    """
    # Check detailed source type mapping first (for AA slow distinction)
    if link in _url_source_types:
        detailed_label = _url_source_types[link]
        for log_label, friendly_name, _ in _DOWNLOAD_SOURCES:
            if log_label == detailed_label:
                return log_label, friendly_name

    for log_label, friendly_name, patterns in _DOWNLOAD_SOURCES:
        if patterns and any(pattern in link for pattern in patterns):
            return log_label, friendly_name
    return "unknown", "Mirror"


def _friendly_source_name(link: str) -> str:
    """Get user-friendly name for a download source."""
    return _get_source_info(link)[1]


def _group_urls_by_source(urls: List[str], urls_by_source: Dict[str, List[str]]) -> None:
    """Group URLs into urls_by_source dict by their source type."""
    for url in urls:
        source_type = _url_source_types.get(url)
        if source_type:
            urls_by_source.setdefault(source_type, []).append(url)


def _fetch_aa_page_urls(book_info: BookInfo, urls_by_source: Dict[str, List[str]]) -> None:
    """Fetch and parse AA page, populating urls_by_source dict.

    Groups existing book_info.download_urls by source type. If book_info
    has no URLs, fetches the AA page fresh.
    """
    if book_info.download_urls:
        _group_urls_by_source(book_info.download_urls, urls_by_source)
        return

    try:
        fresh_book_info = get_book_info(book_info.id, fetch_download_count=False)
        _group_urls_by_source(fresh_book_info.download_urls, urls_by_source)
    except Exception as e:
        logger.warning(f"Failed to fetch AA page: {e}")


def _get_urls_for_source(
    source_id: str,
    book_info: BookInfo,
    selector: network.AAMirrorSelector,
    cancel_flag: Optional[Event],
    status_callback: Optional[Callable[[str, Optional[str]], None]],
    urls_by_source: Dict[str, List[str]],
) -> List[str]:
    """Get URLs for a specific source, fetching lazily if needed."""
    # AA Fast - generate URL dynamically
    if source_id == "aa-fast":
        if not config.AA_DONATOR_KEY:
            return []
        url = f"{network.get_aa_base_url()}/dyn/api/fast_download.json?md5={book_info.id}&key={config.AA_DONATOR_KEY}"
        _url_source_types[url] = "aa-fast"
        return [url]

    # MD5-based sources - generate URL from template
    template = _get_md5_url_template(source_id)
    if template:
        url = template.format(md5=book_info.id)
        _url_source_types[url] = source_id
        return [url]

    if source_id == "libgen":
        urls = []
        for base_url in _get_libgen_domains():
            url = f"{base_url}/ads.php?md5={book_info.id}"
            _url_source_types[url] = "libgen"
            urls.append(url)
        return urls

    # Welib - fetch page and parse for slow_download links
    if source_id == "welib":
        if status_callback:
            status_callback("resolving", "Fetching welib sources")
        return _get_download_urls_from_welib(book_info.id, selector=selector, cancel_flag=cancel_flag, status_callback=status_callback)

    # AA page sources - fetch AA page if not already done
    if source_id in _AA_PAGE_SOURCES:
        if not urls_by_source:
            if status_callback:
                status_callback("resolving", "Fetching download sources")
            _fetch_aa_page_urls(book_info, urls_by_source)

        return urls_by_source.get(source_id, [])

    return []


def _try_download_url(
    url: str,
    source_id: str,
    book_info: BookInfo,
    book_path: Path,
    progress_callback: Optional[Callable[[float], None]],
    cancel_flag: Optional[Event],
    status_callback: Optional[Callable[[str, Optional[str]], None]],
    selector: network.AAMirrorSelector,
    source_context: str
) -> Optional[str]:
    """Attempt to download from a single URL.

    Returns: download URL on success, None on failure.
    """
    try:
        logger.info(f"Trying download source [{source_id}]: {url}")

        if status_callback:
            status_callback("resolving", f"Trying {source_context}")

        download_url = _get_download_url(url, book_info.title, cancel_flag, status_callback, selector, source_context)
        if not download_url:
            raise Exception("No download URL resolved")

        logger.info(f"Resolved download URL [{source_id}]: {download_url}")

        data = downloader.download_url(
            download_url, book_info.size or "",
            progress_callback, cancel_flag, selector,
            status_callback, referer=url
        )

        if not data:
            raise Exception("No data received from download")

        file_size = data.tell()
        if file_size < _MIN_VALID_FILE_SIZE:
            logger.warning(f"Downloaded file too small ({file_size} bytes), likely an error page")
            raise Exception(f"File too small ({file_size} bytes)")

        logger.debug(f"Download finished ({file_size} bytes). Writing to {book_path}")
        data.seek(0)
        with open(book_path, "wb") as f:
            f.write(data.getbuffer())

        return download_url

    except Exception as e:
        logger.warning(f"Failed to download from {url} (source={source_id}): {e}")
        return None


def _get_download_urls_from_welib(
    book_id: str,
    selector: Optional[network.AAMirrorSelector] = None,
    cancel_flag: Optional[Event] = None,
    status_callback: Optional[Callable[[str, Optional[str]], None]] = None
) -> List[str]:
    """Get download URLs from welib.org (bypasser required)."""
    from shelfmark.core import mirrors

    if not _is_source_enabled("welib"):
        return []
    url = mirrors.get_welib_url_template().format(md5=book_id)
    logger.info(f"Fetching welib download URLs for {book_id}")
    try:
        html = downloader.html_get_page(url, use_bypasser=True, selector=selector or network.AAMirrorSelector(), cancel_flag=cancel_flag, status_callback=status_callback)
    except Exception as exc:
        logger.error_trace(f"Welib fetch failed for {book_id}: {exc}")
        return []
    if not html:
        logger.warning(f"Welib page empty for {book_id}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = [
        downloader.get_absolute_url(url, a["href"])
        for a in soup.find_all("a", href=True)
        if "/slow_download/" in a["href"]
    ]
    return list(dict.fromkeys(links))  # Dedupe while preserving order


def _extract_libgen_download_url(link: str, cancel_flag: Optional[Event] = None) -> str:
    """Extract download URL from Libgen ads.php page using direct HTTP."""
    if cancel_flag and cancel_flag.is_set():
        return ""

    base_url = "/".join(link.split("/")[:3])
    logger.debug(f"Libgen fast: trying {link}")

    try:
        response = requests.get(
            link,
            headers=downloader.DOWNLOAD_HEADERS,
            timeout=(5, 10),
            allow_redirects=True,
            proxies=network.get_proxies(link),
        )

        if response.status_code != 200:
            logger.debug(f"Libgen fast: {link} returned {response.status_code}")
            return ""

        html = response.text
        final_url = response.url

        if "libgen" not in final_url.lower() and "ads.php" not in final_url.lower():
            logger.debug(f"Libgen fast: redirected away to {final_url}")
            return ""

        if "get.php" not in html:
            logger.debug(f"Libgen fast: page doesn't contain get.php")
            return ""

        download_url = None
        for pattern in _LIBGEN_GET_PATTERNS:
            match = pattern.search(html)
            if match:
                download_url = match.group(1).replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
                break

        if not download_url:
            logger.debug(f"Libgen fast: couldn't extract GET link")
            return ""
        if not download_url.startswith("http"):
            download_url = f"{base_url}/{download_url.lstrip('/')}"

        logger.debug(f"Libgen fast: extracted {download_url}")
        return download_url

    except requests.exceptions.RequestException as e:
        logger.debug(f"Libgen fast: request failed: {e}")
        return ""
    except Exception as e:
        logger.warning(f"Libgen fast: unexpected error: {e}")
        return ""


def _download_book(
    book_info: BookInfo,
    book_path: Path,
    progress_callback: Optional[Callable[[float], None]] = None,
    cancel_flag: Optional[Event] = None,
    status_callback: Optional[Callable[[str, Optional[str]], None]] = None
) -> Optional[str]:
    """Download a book using sources in configured priority order.

    Returns: Download URL if successful, None otherwise.
    """
    selector = network.AAMirrorSelector()
    source_failures: dict[str, int] = {}
    urls_by_source: dict[str, list[str]] = {}
    url_attempt_counter = 0

    # Get enabled sources in priority order
    priority = [s for s in _get_source_priority() if s.get("enabled", True)]

    for source_config in priority:
        source_id = source_config["id"]

        if cancel_flag and cancel_flag.is_set():
            return None

        # Debug: skip sources for testing fallback chains
        if source_id in DEBUG_SKIP_SOURCES:
            logger.info("DEBUG_SKIP_SOURCES: skipping %s", source_id)
            continue

        # Skip if source requires CF bypass and it's not enabled
        if source_id in _CF_BYPASS_REQUIRED and not config.USE_CF_BYPASS:
            logger.debug(f"Skipping {source_id} - requires CF bypass")
            continue

        # Skip if source has failed too many times
        if source_failures.get(source_id, 0) >= _SOURCE_FAILURE_THRESHOLD:
            logger.debug(f"Skipping {source_id} - too many failures")
            continue

        # Get URLs for this source (lazy-loads as needed)
        urls_to_try = _get_urls_for_source(
            source_id, book_info, selector, cancel_flag, status_callback,
            urls_by_source,
        )

        if not urls_to_try:
            continue

        # Apply round-robin rotation if multiple URLs
        if len(urls_to_try) > 1:
            rotation_value = next(_aa_slow_rotation)
            rotation = rotation_value % len(urls_to_try)
            urls_to_try = urls_to_try[rotation:] + urls_to_try[:rotation]
            if rotation:
                logger.debug(f"Rotated {source_id} URLs by {rotation}")

        # Try each URL for this source
        for url in urls_to_try:
            if cancel_flag and cancel_flag.is_set():
                return None

            if source_id == "libgen":
                source_context = "Libgen (Fast)"
            else:
                url_attempt_counter += 1
                friendly_name = _friendly_source_name(url)
                source_context = f"{friendly_name} (Server #{url_attempt_counter})"

            result = _try_download_url(
                url, source_id, book_info, book_path,
                progress_callback, cancel_flag, status_callback, selector,
                source_context
            )

            if result:
                return result

            source_failures[source_id] = source_failures.get(source_id, 0) + 1

            # Check if we've hit the failure threshold
            if source_failures[source_id] >= _SOURCE_FAILURE_THRESHOLD:
                logger.info(f"Source {source_id} hit failure threshold, moving to next source")
                break

    if status_callback:
        status_callback("error", "All sources failed")
    return None


def _get_download_url(
    link: str,
    title: str,
    cancel_flag: Optional[Event] = None,
    status_callback: Optional[Callable[[str, Optional[str]], None]] = None,
    selector: Optional[network.AAMirrorSelector] = None,
    source_context: Optional[str] = None
) -> str:
    """Extract actual download URL from various source pages.

    Args:
        link: URL to extract download link from
        title: Book title for logging
        cancel_flag: Optional cancellation flag
        status_callback: Optional callback for status updates
        selector: Optional AA mirror selector
        source_context: Optional context string like "Welib (1/12)" for status messages
    """
    sel = selector or network.AAMirrorSelector()

    # AA fast download API (JSON response)
    if link.startswith(f"{network.get_aa_base_url()}/dyn/api/fast_download.json"):
        page = downloader.html_get_page(link, selector=sel, cancel_flag=cancel_flag, status_callback=status_callback)
        return downloader.get_absolute_url(link, json.loads(page).get("download_url", ""))

    if "/ads.php?md5=" in link and any(domain in link for domain in _get_libgen_domains()):
        return _extract_libgen_download_url(link, cancel_flag)

    html = downloader.html_get_page(link, selector=sel, cancel_flag=cancel_flag, status_callback=status_callback)
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    url = ""

    # Z-Library
    if link.startswith("https://z-lib."):
        dl = soup.find("a", href=True, class_="addDownloadedBook")
        if not dl:
            # Retry after delay if page not fully loaded
            time.sleep(2)
            html = downloader.html_get_page(link, selector=sel, cancel_flag=cancel_flag, status_callback=status_callback)
            if html:
                soup = BeautifulSoup(html, "html.parser")
                dl = soup.find("a", href=True, class_="addDownloadedBook")
        url = dl["href"] if dl else ""

    # AA slow download / partner servers
    elif "/slow_download/" in link:
        url = _extract_slow_download_url(soup, link, title, cancel_flag, status_callback, sel, source_context)

    else:
        get_btn = soup.find("a", string="GET") or soup.find("a", string="Download")
        if get_btn:
            url = get_btn.get("href", "")
        else:
            logger.warning(f"Unknown source type, couldn't find download link: {link}")
            url = ""

    return downloader.get_absolute_url(link, url)


def _extract_slow_download_url(
    soup: BeautifulSoup,
    link: str,
    title: str,
    cancel_flag: Optional[Event],
    status_callback,
    selector,
    source_context: Optional[str] = None
) -> str:
    """Extract download URL from AA slow download pages."""
    html_str = str(soup)

    clipboard_match = re.search(r"navigator\.clipboard\.writeText\(['\"]([^'\"]+)['\"]\)", html_str)
    if clipboard_match:
        url = clipboard_match.group(1)
        if url.startswith("http") and "/slow_download/" not in url:
            return url

    dl_link = soup.find("a", href=True, string="📚 Download now")
    if not dl_link:
        dl_link = soup.find("a", href=True, string=lambda s: s and "Download now" in s)
    if dl_link:
        return dl_link["href"]

    for a_tag in soup.find_all("a", href=True):
        if a_tag.has_attr("download"):
            href = a_tag["href"]
            if href.startswith("http") and "/slow_download/" not in href:
                return href

    for span in soup.find_all("span", class_=lambda c: c and "whitespace-normal" in c):
        text = span.get_text(strip=True)
        if text.startswith(("http://", "https://")) and "/slow_download/" not in text:
            return text

    for span in soup.find_all("span", class_=lambda c: c and "bg-gray-200" in c):
        text = span.get_text(strip=True)
        if text.startswith(("http://", "https://")):
            return text

    location_match = re.search(r"window\.location\.href\s*=\s*['\"]([^'\"]+)['\"]", html_str)
    if location_match:
        url = location_match.group(1)
        if url.startswith("http") and "/slow_download/" not in url:
            return url

    copy_text = soup.find(string=lambda s: s and "copy this url" in s.lower())
    if copy_text and copy_text.parent:
        parent = copy_text.parent
        next_link = parent.find_next("a", href=True)
        if next_link and next_link.get("href"):
            return next_link["href"]
        code_elem = parent.find_next("code")
        if code_elem:
            return code_elem.get_text(strip=True)
        for sibling in parent.find_next_siblings():
            text = sibling.get_text(strip=True) if hasattr(sibling, 'get_text') else str(sibling).strip()
            if text.startswith("http"):
                return text

    countdown_seconds = _extract_countdown_seconds(soup, html_str)
    if countdown_seconds > 0:
        MAX_COUNTDOWN_SECONDS = 600
        sleep_time = min(countdown_seconds, MAX_COUNTDOWN_SECONDS)
        if countdown_seconds > MAX_COUNTDOWN_SECONDS:
            logger.warning(f"Countdown {countdown_seconds}s exceeds max, capping at {MAX_COUNTDOWN_SECONDS}s")
        logger.info(f"AA waitlist: {sleep_time}s for {title}")

        # Live countdown with status updates
        for remaining in range(sleep_time, 0, -1):
            wait_msg = f"{source_context} - Waiting {remaining}s" if source_context else f"Waiting {remaining}s"
            if status_callback:
                status_callback("resolving", wait_msg)

            # Wait 1 second (or until cancelled)
            if cancel_flag and cancel_flag.wait(timeout=1):
                logger.info(f"Cancelled wait for {title}")
                return ""

        # After countdown, update status and re-fetch
        if status_callback and source_context:
            status_callback("resolving", f"{source_context} - Fetching")

        return _get_download_url(link, title, cancel_flag, status_callback, selector, source_context)

    link_texts = [a.get_text(strip=True)[:50] for a in soup.find_all("a", href=True)[:10]]
    logger.warning(f"No download URL found. First 10 links: {link_texts}")
    return ""


def _extract_countdown_seconds(soup: BeautifulSoup, html_str: str) -> int:
    """Extract countdown timer seconds from AA slow download page."""
    countdown_elem = soup.find("span", class_="js-partner-countdown")
    if countdown_elem:
        try:
            seconds = int(countdown_elem.get_text(strip=True))
            if 0 < seconds < 300:
                return seconds
        except (ValueError, TypeError):
            pass

    for elem in soup.find_all(["span", "div"], class_=lambda c: c and ("timer" in c.lower() or "countdown" in c.lower())):
        try:
            seconds = int(elem.get_text(strip=True))
            if 0 < seconds < 300:
                return seconds
        except (ValueError, TypeError):
            pass

    countdown_attr = re.search(r'data-countdown=["\'](\d+)["\']', html_str)
    if countdown_attr:
        seconds = int(countdown_attr.group(1))
        if 0 < seconds < 300:
            return seconds

    js_countdown = re.search(r'countdown:\s*(\d+)', html_str)
    if js_countdown:
        seconds = int(js_countdown.group(1))
        if 0 < seconds < 300:
            return seconds

    js_var = re.search(r'(?:var|let|const)\s+countdown\s*=\s*(\d+)', html_str)
    if js_var:
        seconds = int(js_var.group(1))
        if 0 < seconds < 300:
            return seconds

    countdown_secs = re.search(r'countdownSeconds\s*=\s*(\d+)', html_str)
    if countdown_secs:
        seconds = int(countdown_secs.group(1))
        if 0 < seconds < 300:
            return seconds

    json_countdown = re.search(r'["\']countdown[_-]?seconds["\']\s*:\s*(\d+)', html_str)
    if json_countdown:
        seconds = int(json_countdown.group(1))
        if 0 < seconds < 300:
            return seconds

    wait_text = re.search(r'wait\s+(\d+)\s+seconds', html_str, re.IGNORECASE)
    if wait_text:
        seconds = int(wait_text.group(1))
        if 0 < seconds < 300:
            return seconds

    return 0


def _book_info_to_release(book_info: BookInfo) -> Release:
    """Convert a BookInfo object to a Release object.

    This bridges the existing BookInfo model (which combines metadata + release info)
    to the new Release model (release info only).
    """
    return Release(
        source="direct_download",
        source_id=book_info.id,
        title=book_info.title,
        format=book_info.format,
        size=book_info.size,
        download_url=book_info.download_urls[0] if book_info.download_urls else None,
        info_url=f"{network.get_aa_base_url()}/md5/{book_info.id}",
        protocol=ReleaseProtocol.HTTP,
        indexer="Direct Download",
        content_type=book_info.content,  # Preserve content type from source
        extra={
            "author": book_info.author,
            "publisher": book_info.publisher,
            "year": book_info.year,
            "language": book_info.language,
            "preview": book_info.preview,
            "description": book_info.description,
            "download_urls": book_info.download_urls,
            "info": book_info.info,
        }
    )


@register_source("direct_download")
class DirectDownloadSource(ReleaseSource):
    """
    Direct download source - searches web sources for books.

    This wraps the search_books() functionality to provide releases
    via the plugin interface.
    """
    name = "direct_download"
    display_name = "Direct Download"
    supported_content_types = ["ebook"]  # Direct downloads only support ebooks

    def __init__(self):
        # Tracks which search method was used in the last search() call
        # "isbn" = ISBN search returned results, "title_author" = title+author was used
        self._last_search_type: str = "title_author"

    @property
    def last_search_type(self) -> str:
        """Returns the search type used in the last search() call."""
        return self._last_search_type

    def get_column_config(self) -> ReleaseColumnConfig:
        """Column configuration for Direct Download source.

        Shows language, format, and size badges for each release.
        Language is hidden on mobile; format and size are shown.
        """
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="extra.language",
                    label="Language",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    hide_mobile=False,  # Language shown on mobile
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,  # Format shown on mobile
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    hide_mobile=False,  # Size shown on mobile
                ),
            ],
            grid_template="minmax(0,2fr) 60px 80px 80px",
            supported_filters=["format", "language"],  # AA has reliable language metadata
        )

    def search(
        self,
        book: BookMetadata,
        plan: "ReleaseSearchPlan",  # noqa: F821
        expand_search: bool = False,
        content_type: str = "ebook"
    ) -> List[Release]:
        """
        Search for releases using the book's metadata.

        Priority: ISBN search first (most precise), then title+author fallback.
        For non-English languages, uses localized titles from book.titles_by_language.

        Args:
            book: Book metadata from provider
            expand_search: If True, skip ISBN and use title+author directly
            languages: Language codes to filter by (overrides book.language/config)
            content_type: Ignored - Direct download uses format filtering instead
        """
        lang_filter = plan.languages

        # Reset search type tracking
        self._last_search_type = "title_author"

        # ISBN search first (unless expand_search requested)
        if plan.manual_query:
            expand_search = True

        if not expand_search:
            isbn = plan.isbn_candidates[0] if plan.isbn_candidates else None
            if isbn:
                logger.debug(f"Searching by ISBN: {isbn}")
                filters = SearchFilters(isbn=[isbn])
                if lang_filter:
                    filters.lang = lang_filter
                try:
                    results = search_books(isbn, filters)
                    if results:
                        logger.info(f"Found {len(results)} releases via ISBN")
                        self._last_search_type = "isbn"
                        return [_book_info_to_release(bi) for bi in results]
                    logger.debug("No ISBN results, falling back to title+author")
                except SearchUnavailable:
                    raise
                except Exception as e:
                    logger.warning(f"ISBN search failed: {e}")

        # Title + author fallback
        author = plan.author
        searches = [(v.title, v.languages) for v in plan.grouped_title_variants]

        # Execute searches with deduplication
        seen_ids: set = set()
        all_results: List[BookInfo] = []

        for title, langs in searches:
            query = f"{title} {author}".strip()
            if not query:
                continue

            logger.debug(f"Searching: query='{query}', langs={langs}")
            filters = SearchFilters(lang=langs) if langs else SearchFilters()
            try:
                for bi in search_books(query, filters):
                    if bi.id not in seen_ids:
                        seen_ids.add(bi.id)
                        all_results.append(bi)
            except SearchUnavailable:
                raise
            except Exception as e:
                logger.error(f"Search error: {e}")

        logger.info(f"Found {len(all_results)} releases via title+author")
        return [_book_info_to_release(bi) for bi in all_results]

    def is_available(self) -> bool:
        """Direct download is always available."""
        return True


@register_handler("direct_download")
class DirectDownloadHandler(DownloadHandler):
    """
    Handler for direct HTTP downloads from Anna's Archive, Libgen, etc.

    Receives a DownloadTask with task_id (AA MD5 hash) and cascades through
    sources in priority order. The AA page is only fetched if AA slow sources
    are enabled in the user's source priority configuration.
    """

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None]
    ) -> Optional[str]:
        """
        Execute a direct HTTP download.

        Uses task.task_id (AA MD5 hash) to cascade through sources in priority
        order. The AA page is only fetched if AA slow sources are enabled.

        Args:
            task: Download task with task_id (AA MD5 hash)
            cancel_flag: Event to check for cancellation
            progress_callback: Called with progress percentage (0-100)
            status_callback: Called with (status, message) for status updates

        Returns:
            Path to downloaded file if successful, None otherwise
        """
        try:
            # Check for cancellation before starting
            if cancel_flag.is_set():
                logger.info(f"Download cancelled before starting: {task.task_id}")
                status_callback("cancelled", "Cancelled")
                return None

            # Create BookInfo from task data - NO AA page fetch here
            # AA page is fetched lazily by _fetch_aa_page_urls only when
            # we actually reach an AA slow source in the priority order
            book_info = BookInfo(
                id=task.task_id,
                title=task.title,
                author=task.author,
                format=task.format,
                size=task.size,
                preview=task.preview,
            )

            return self._execute_download(
                book_info,
                cancel_flag,
                progress_callback,
                status_callback
            )

        except Exception as e:
            if cancel_flag.is_set():
                logger.info(f"Download cancelled during error handling: {task.task_id}")
                status_callback("cancelled", "Cancelled")
            else:
                logger.error(f"Error downloading book: {e}")
                status_callback("error", str(e))
            return None

    def _execute_download(
        self,
        book_info: BookInfo,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, Optional[str]], None]
    ) -> Optional[str]:
        """
        Internal method to execute the download with fetched BookInfo.

        This contains the core download logic: cascade through sources,
        handle bypass, move to final location.
        """
        try:
            logger.debug("Starting download: %s", book_info.title)

            # Prepare paths - use descriptive staging filename, orchestrator will rename
            # based on FILE_ORGANIZATION setting
            file_org = config.get("FILE_ORGANIZATION", "rename")
            if file_org == "none":
                book_name = f"{book_info.id}.{book_info.format or 'bin'}"
            else:
                book_name = book_info.get_filename()
            book_path = TMP_DIR / book_name

            # Check cancellation before download
            if cancel_flag.is_set():
                logger.info(f"Download cancelled before download call: {book_info.id}")
                status_callback("cancelled", "Cancelled")
                return None

            # Execute download via _download_book (handles cascade and bypass)
            status_callback("resolving", "Finding download source")
            success_url = _download_book(
                book_info,
                book_path,
                progress_callback,
                cancel_flag,
                status_callback
            )

            # Check for cancellation after download
            if cancel_flag.is_set():
                logger.info(f"Download cancelled during download: {book_info.id}")
                if book_path.exists():
                    book_path.unlink()
                status_callback("cancelled", "Cancelled")
                return None

            if not success_url:
                status_callback("error", "All download sources failed")
                return None

            # Return temp path - orchestrator handles post-processing (archive extraction, ingest)
            return str(book_path)

        except Exception as e:
            if cancel_flag.is_set():
                logger.info(f"Download cancelled during error handling: {book_info.id}")
                status_callback("cancelled", "Cancelled")
            else:
                logger.error(f"Error downloading book: {e}")
            return None

    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download.

        Cancellation is handled via the cancel_flag passed to download().
        This method exists for the interface but actual cancellation
        happens through the Event flag mechanism.
        """
        # Cancellation is handled by the orchestrator via cancel_flag
        return False
